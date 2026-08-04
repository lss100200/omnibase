"""P34.2 server-side capability ledger and verifier.

All functions use a caller-owned transaction.  They flush when generated IDs
are needed, but never commit or roll back.  No function accepts a physical
locator, SQL string, credential, or remotely supplied verification key.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from base64 import urlsafe_b64encode
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any

from sqlalchemy import exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from omnibase.capabilities.models import (
    CapabilityGrant,
    CapabilityRevocation,
    CapabilitySigningKey,
    CapabilityUsage,
    CapabilityUsageReservation,
    WorkspaceDataUsageReservation,
)
from omnibase.capabilities.token import (
    ALGORITHM,
    JTI_PATTERN,
    MAX_TOKEN_TTL,
    CapabilityTokenClaims,
    CapabilityTokenError,
    decode_capability_token,
    encode_capability_token,
    get_trusted_kid,
    private_key_fingerprint,
    public_key_fingerprint,
)
from omnibase.control_plane.models import OperationRecord, ResourceRecord

READ_ACTIONS = frozenset(
    {
        "data.schema.read",
        "data.rows.read",
        "rag.search",
        "rag.citation.read",
    }
)
"""P34.2 action vocabulary.  ``citation`` is intentionally singular."""

SANDBOX_ACTIONS = frozenset(
    {
        "sandbox.prepare",
        "sandbox.create",
        "sandbox.start",
        "sandbox.exec",
        "sandbox.cancel",
        "sandbox.logs",
        "sandbox.stats",
        "sandbox.snapshot",
        "sandbox.restore",
        "sandbox.stop",
        "sandbox.destroy",
    }
)
"""P34.5 workload lifecycle vocabulary; emergency control is deliberately absent."""

WORKSPACE_DATA_ACTIONS = frozenset(
    {
        "data.rows.insert",
        "data.rows.update",
        "data.rows.delete",
        "artifact.read",
        "artifact.write",
        "rag.derived.create",
        "rag.derived.delete",
    }
)
"""P34.6 workload data vocabulary; promotion and canonical mutation are absent."""

MAX_DELEGATION_DEPTH = 8

_CONSTRAINT_KEYS = frozenset({"max_rows", "max_result_bytes", "rag_top_k", "timeout_ms"})
_KID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_REASON_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{1,63}$")


class CapabilityError(Exception):
    """Base class for capability-domain failures."""


class InvalidCapability(CapabilityError):
    """Authentication failed; callers must not disclose the specific reason."""


class CapabilityScopeDenied(CapabilityError):
    """A valid capability does not authorize the requested logical scope."""


class CapabilityBudgetExceeded(CapabilityError):
    """The online calls/bytes/cost budget cannot cover a request."""


class CapabilityConflict(CapabilityError):
    """A grant, key, or delegation conflicts with current durable state."""


class WorkspaceDataReplayForbidden(CapabilityConflict):
    """A pending or unknown workspace-data effect must be reconciled, not replayed."""


class WorkspaceDataReservationConflict(CapabilityConflict):
    """An operation ID was replayed with a different immutable binding."""


@dataclass(frozen=True)
class VerifiedCapability:
    """Safe verifier output for a thin gateway adapter."""

    claims: CapabilityTokenClaims
    grant_id: str
    tenant_id: str
    workspace_id: str
    runtime_instance_id: str
    actor_user_id: str
    action: str
    resource_id: str
    constraints: dict[str, object]


@dataclass(frozen=True, slots=True)
class VerifiedSandboxCapabilityFacts:
    """Token-free server-owned proof for one idempotently budgeted operation."""

    grant_id: str
    tenant_id: str
    workspace_id: str
    runtime_instance_id: str
    workload_identity_digest: str
    operation_id: str
    action: str
    grant_version: int
    verified_at: datetime
    expires_at: datetime
    verification_digest: str


@dataclass(frozen=True, slots=True)
class VerifiedWorkspaceDataCapabilityFacts:
    """Server-owned proof for one operation-idempotent P34.6 data request."""

    grant_id: str
    tenant_id: str
    workspace_id: str
    runtime_instance_id: str
    workload_identity_digest: str
    operation_id: str
    action: str
    resource_id: str
    resource_version: int | None
    request_hash: str
    grant_version: int
    reservation_state: str
    replayed: bool
    verified_at: datetime
    expires_at: datetime
    verification_digest: str


@dataclass(frozen=True)
class TrustedIssuerContext:
    """Already-authenticated internal issuer context.

    The internal issuer boundary constructs this only after resolving the
    tenant-scoped user with its tenant-aware session.  Capability persistence
    deliberately does not query tenant-schema ``User`` through a global
    session or infer tenant membership from a token claim.
    """

    tenant_id: str
    system_actor_id: str
    originating_user_id: str


@dataclass(frozen=True)
class TrustedPlatformContext:
    """Already-authenticated platform security context for global key changes."""

    system_actor_id: str


def register_signing_key(
    session: Session,
    *,
    platform_context: TrustedPlatformContext,
    kid: str,
    public_key_pem: str,
    not_before: datetime,
    expires_at: datetime,
) -> CapabilitySigningKey:
    """Register one local public verification key; no URL form is accepted."""

    _validate_platform_context(platform_context)
    not_before = _aware(not_before)
    expires_at = _aware(expires_at)
    if not _KID_PATTERN.fullmatch(kid):
        raise ValueError("kid has an invalid format")
    if expires_at <= not_before:
        raise ValueError("signing key expires_at must be after not_before")
    fingerprint = public_key_fingerprint(public_key_pem)
    key = CapabilitySigningKey(
        kid=kid,
        algorithm=ALGORITHM,
        public_key_pem=public_key_pem,
        public_key_sha256=fingerprint,
        state="active",
        not_before=not_before,
        expires_at=expires_at,
    )
    session.add(key)
    return key


def create_grant(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_instance_id: str,
    issuer_context: TrustedIssuerContext,
    actions: set[str] | frozenset[str],
    resource_ids: set[str] | frozenset[str],
    not_before: datetime,
    expires_at: datetime,
    max_calls: int,
    max_bytes: int,
    max_cost_units: int,
    delegation_depth_limit: int,
    constraints: dict[str, object] | None = None,
    approval_id: str | None = None,
) -> CapabilityGrant:
    """Create a root grant from a trusted server principal only."""

    _validate_issuer_context(issuer_context, tenant_id=tenant_id)
    if approval_id is not None:
        raise CapabilityScopeDenied("P34.2 read grants cannot bind an approval")
    return _create_grant(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_instance_id=runtime_instance_id,
        actor_user_id=issuer_context.originating_user_id,
        actions=actions,
        resource_ids=resource_ids,
        not_before=not_before,
        expires_at=expires_at,
        max_calls=max_calls,
        max_bytes=max_bytes,
        max_cost_units=max_cost_units,
        delegation_depth=0,
        delegation_depth_limit=delegation_depth_limit,
        created_by_actor_type="system",
        created_by_actor_id=issuer_context.system_actor_id,
        constraints=constraints,
        approval_id=approval_id,
        parent_grant_id=None,
        allowed_actions=READ_ACTIONS,
        workload_identity_digest=None,
    )


def create_sandbox_grant(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_instance_id: str,
    workload_identity_digest: str,
    issuer_context: TrustedIssuerContext,
    actions: set[str] | frozenset[str],
    not_before: datetime,
    expires_at: datetime,
    max_calls: int,
    max_bytes: int,
    max_cost_units: int,
    constraints: dict[str, object] | None = None,
) -> CapabilityGrant:
    """Create one non-delegable, runtime-bound Sandbox lifecycle grant."""

    _validate_issuer_context(issuer_context, tenant_id=tenant_id)
    _validate_digest(workload_identity_digest, "workload_identity_digest")
    not_before = _aware(not_before)
    expires_at = _aware(expires_at)
    if expires_at - not_before > MAX_TOKEN_TTL:
        raise CapabilityScopeDenied("sandbox grant lifetime cannot exceed five minutes")
    return _create_grant(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_instance_id=runtime_instance_id,
        actor_user_id=issuer_context.originating_user_id,
        actions=actions,
        resource_ids=frozenset({workspace_id}),
        not_before=not_before,
        expires_at=expires_at,
        max_calls=max_calls,
        max_bytes=max_bytes,
        max_cost_units=max_cost_units,
        delegation_depth=0,
        delegation_depth_limit=0,
        created_by_actor_type="system",
        created_by_actor_id=issuer_context.system_actor_id,
        constraints=constraints,
        approval_id=None,
        parent_grant_id=None,
        allowed_actions=SANDBOX_ACTIONS,
        workload_identity_digest=workload_identity_digest,
    )


def create_workspace_data_grant(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_instance_id: str,
    workload_identity_digest: str,
    issuer_context: TrustedIssuerContext,
    actions: set[str] | frozenset[str],
    resource_ids: set[str] | frozenset[str],
    not_before: datetime,
    expires_at: datetime,
    max_calls: int,
    max_bytes: int,
    max_cost_units: int,
    constraints: dict[str, object] | None = None,
) -> CapabilityGrant:
    """Create one short-lived, non-delegable workspace-data workload grant."""

    _validate_issuer_context(issuer_context, tenant_id=tenant_id)
    _validate_digest(workload_identity_digest, "workload_identity_digest")
    not_before = _aware(not_before)
    expires_at = _aware(expires_at)
    if expires_at - not_before > MAX_TOKEN_TTL:
        raise CapabilityScopeDenied("workspace-data grant lifetime cannot exceed five minutes")
    return _create_grant(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_instance_id=runtime_instance_id,
        actor_user_id=issuer_context.originating_user_id,
        actions=actions,
        resource_ids=resource_ids,
        not_before=not_before,
        expires_at=expires_at,
        max_calls=max_calls,
        max_bytes=max_bytes,
        max_cost_units=max_cost_units,
        delegation_depth=0,
        delegation_depth_limit=0,
        created_by_actor_type="system",
        created_by_actor_id=issuer_context.system_actor_id,
        constraints=constraints,
        approval_id=None,
        parent_grant_id=None,
        allowed_actions=WORKSPACE_DATA_ACTIONS,
        workload_identity_digest=workload_identity_digest,
    )


def delegate_grant(
    session: Session,
    *,
    tenant_id: str,
    parent_grant_id: str,
    runtime_instance_id: str,
    actions: set[str] | frozenset[str],
    resource_ids: set[str] | frozenset[str],
    expires_at: datetime,
    max_calls: int,
    max_bytes: int,
    max_cost_units: int,
    issuer_context: TrustedIssuerContext,
    constraints: dict[str, object] | None = None,
) -> CapabilityGrant:
    """Delegate to another runtime with a provably strict scope reduction."""

    _validate_issuer_context(issuer_context, tenant_id=tenant_id)
    parent = get_grant(session, tenant_id=tenant_id, grant_id=parent_grant_id, lock=True)
    now = _now()
    if (
        parent.state != "active"
        or _aware(parent.not_before) > now
        or _aware(parent.expires_at) <= now
    ):
        raise CapabilityConflict("parent grant is not active")
    if parent.actor_user_id != issuer_context.originating_user_id:
        raise CapabilityScopeDenied("issuer context is not bound to the parent grant user")
    _assert_active_ancestry(
        session,
        tenant_id=tenant_id,
        leaf=parent,
        lock=True,
        failure_type=CapabilityConflict,
    )
    child_depth = parent.delegation_depth + 1
    if child_depth > parent.delegation_depth_limit:
        raise CapabilityScopeDenied("delegation depth exceeded")

    child_actions = _validate_actions(actions)
    child_resources = _validate_resource_ids(resource_ids)
    parent_actions = frozenset(parent.actions)
    parent_resources = frozenset(parent.resource_ids)
    if not child_actions <= parent_actions or not child_resources <= parent_resources:
        raise CapabilityScopeDenied("delegation cannot widen action or resource scope")
    child_constraints = _validate_constraints(constraints)
    if not _constraints_narrow(parent.constraints, child_constraints):
        raise CapabilityScopeDenied("delegation constraints cannot be widened")
    expires_at = _aware(expires_at)
    if expires_at > _aware(parent.expires_at):
        raise CapabilityScopeDenied("delegation cannot outlive its parent")
    _validate_budgets(
        max_calls=max_calls,
        max_bytes=max_bytes,
        max_cost_units=max_cost_units,
    )
    if (
        max_calls > parent.max_calls
        or max_bytes > parent.max_bytes
        or max_cost_units > parent.max_cost_units
    ):
        raise CapabilityScopeDenied("delegation budget cannot exceed its parent")

    strict = (
        child_actions < parent_actions
        or child_resources < parent_resources
        or expires_at < _aware(parent.expires_at)
        or max_calls < parent.max_calls
        or max_bytes < parent.max_bytes
        or max_cost_units < parent.max_cost_units
        or child_constraints != parent.constraints
    )
    if not strict:
        raise CapabilityScopeDenied("delegation must strictly reduce at least one bound")
    # Reserve the complete child envelope on the parent in one conditional
    # UPDATE.  Concurrent sibling delegations therefore cannot multiply the
    # parent's authority; rollback also releases the reservation.
    _reserve_budget(
        session,
        tenant_id=tenant_id,
        grant_id=parent.id,
        grant_version=parent.version,
        calls=max_calls,
        bytes_in=max_bytes,
        bytes_out=0,
        cost_units=max_cost_units,
    )
    return _create_grant(
        session,
        tenant_id=tenant_id,
        workspace_id=parent.workspace_id,
        runtime_instance_id=runtime_instance_id,
        actor_user_id=parent.actor_user_id,
        actions=child_actions,
        resource_ids=child_resources,
        not_before=max(now, _aware(parent.not_before)),
        expires_at=expires_at,
        max_calls=max_calls,
        max_bytes=max_bytes,
        max_cost_units=max_cost_units,
        delegation_depth=child_depth,
        delegation_depth_limit=parent.delegation_depth_limit,
        created_by_actor_type="system",
        created_by_actor_id=issuer_context.system_actor_id,
        constraints=child_constraints,
        approval_id=parent.approval_id,
        parent_grant_id=parent.id,
        allowed_actions=READ_ACTIONS,
        workload_identity_digest=None,
    )


def issue_token(
    session: Session,
    *,
    tenant_id: str,
    grant_id: str,
    kid: str,
    private_key_pem: str | bytes,
    workload_thumbprint: str,
    issuer_context: TrustedIssuerContext,
    ttl: timedelta = MAX_TOKEN_TTL,
) -> str:
    """Issue a short-lived token after online grant and local-key checks."""

    _validate_issuer_context(issuer_context, tenant_id=tenant_id)
    if not _KID_PATTERN.fullmatch(kid):
        raise ValueError("kid has an invalid format")
    now = _now()
    grant = get_grant(session, tenant_id=tenant_id, grant_id=grant_id)
    profile = _action_profile(frozenset(grant.actions))
    if profile == "sandbox":
        raise CapabilityScopeDenied("sandbox grants cannot be issued as Gateway bearer tokens")
    if profile == "workspace_data":
        digest = getattr(grant, "workload_identity_digest", None)
        if (
            not isinstance(digest, str)
            or _x5t_s256_from_hex(digest) != workload_thumbprint
            or grant.delegation_depth != 0
            or grant.delegation_depth_limit != 0
            or grant.parent_grant_id is not None
            or _aware(grant.expires_at) - _aware(grant.not_before) > MAX_TOKEN_TTL
        ):
            raise CapabilityScopeDenied("workspace-data grant binding is invalid")
    if grant.actor_user_id != issuer_context.originating_user_id:
        raise CapabilityScopeDenied("issuer context is not bound to the grant user")
    if grant.state != "active" or _aware(grant.not_before) > now or _aware(grant.expires_at) <= now:
        raise CapabilityConflict("grant is not active")
    _assert_active_ancestry(
        session,
        tenant_id=tenant_id,
        leaf=grant,
        lock=False,
        failure_type=CapabilityConflict,
    )
    key = session.execute(
        select(CapabilitySigningKey).where(CapabilitySigningKey.kid == kid)
    ).scalar_one_or_none()
    if (
        key is None
        or key.algorithm != ALGORITHM
        or key.state != "active"
        or _aware(key.not_before) > now
        or _aware(key.expires_at) <= now
    ):
        raise CapabilityConflict("signing key is not active")
    if private_key_fingerprint(private_key_pem) != key.public_key_sha256:
        raise CapabilityConflict("private signing key does not match registered public key")
    if ttl <= timedelta(0) or ttl > MAX_TOKEN_TTL:
        raise ValueError("token ttl must be positive and at most five minutes")
    token_expiry = min(now + ttl, _aware(grant.expires_at), _aware(key.expires_at))
    return encode_capability_token(
        private_key_pem=private_key_pem,
        kid=key.kid,
        jti=uuid.uuid4().hex,
        subject=grant.runtime_instance_id,
        tenant_id=grant.tenant_id,
        workspace_id=grant.workspace_id,
        actor_user_id=grant.actor_user_id,
        grant_id=grant.id,
        grant_version=grant.version,
        delegation_depth=grant.delegation_depth,
        workload_thumbprint=workload_thumbprint,
        issued_at=now,
        expires_at=token_expiry,
        approval_id=grant.approval_id,
    )


def _verified_gateway_profile_actions(
    grant: CapabilityGrant,
    *,
    expected_workload_thumbprint: str,
) -> frozenset[str]:
    """Return the closed Gateway action profile after binding validation."""
    try:
        profile = _action_profile(frozenset(grant.actions))
    except CapabilityScopeDenied as exc:
        raise InvalidCapability("invalid capability") from exc
    if profile == "sandbox":
        raise InvalidCapability("invalid capability")
    if profile == "workspace_data":
        digest = getattr(grant, "workload_identity_digest", None)
        if (
            not isinstance(digest, str)
            or _x5t_s256_from_hex(digest) != expected_workload_thumbprint
            or grant.delegation_depth != 0
            or grant.delegation_depth_limit != 0
            or grant.parent_grant_id is not None
        ):
            raise InvalidCapability("invalid capability")
        return WORKSPACE_DATA_ACTIONS
    if getattr(grant, "workload_identity_digest", None) is not None:
        raise InvalidCapability("invalid capability")
    return READ_ACTIONS


def verify_capability(
    session: Session,
    *,
    token: str,
    expected_tenant_id: str,
    expected_workspace_id: str,
    expected_runtime_instance_id: str,
    expected_workload_thumbprint: str,
    action: str,
    resource_id: str,
) -> VerifiedCapability:
    """Validate token, workload binding, online ledger, revocation, and scope."""

    try:
        kid = get_trusted_kid(token)
        key = session.execute(
            select(CapabilitySigningKey).where(CapabilitySigningKey.kid == kid)
        ).scalar_one_or_none()
        now = _now()
        if (
            key is None
            or key.algorithm != ALGORITHM
            or key.state == "revoked"
            or _aware(key.not_before) > now
            or _aware(key.expires_at) <= now
        ):
            raise InvalidCapability("invalid capability")
        claims = decode_capability_token(token=token, public_key_pem=key.public_key_pem)
    except (CapabilityTokenError, ValueError) as exc:
        raise InvalidCapability("invalid capability") from exc

    if (
        claims.tenant_id != expected_tenant_id
        or claims.workspace_id != expected_workspace_id
        or claims.subject != expected_runtime_instance_id
        or claims.workload_thumbprint != expected_workload_thumbprint
    ):
        raise InvalidCapability("invalid capability")
    grant = session.execute(
        select(CapabilityGrant).where(
            CapabilityGrant.tenant_id == expected_tenant_id,
            CapabilityGrant.id == claims.grant_id,
        )
    ).scalar_one_or_none()
    if grant is None:
        raise InvalidCapability("invalid capability")
    if (
        grant.state != "active"
        or _aware(grant.not_before) > now
        or _aware(grant.expires_at) <= now
        or grant.version != claims.grant_version
        or grant.workspace_id != claims.workspace_id
        or grant.runtime_instance_id != claims.subject
        or grant.actor_user_id != claims.actor_user_id
        or grant.delegation_depth != claims.delegation_depth
        or grant.approval_id != claims.approval_id
    ):
        raise InvalidCapability("invalid capability")
    _assert_active_ancestry(
        session,
        tenant_id=expected_tenant_id,
        leaf=grant,
        lock=False,
        failure_type=InvalidCapability,
    )
    revoked = session.execute(
        select(CapabilityRevocation.id).where(
            CapabilityRevocation.tenant_id == expected_tenant_id,
            CapabilityRevocation.grant_id == grant.id,
            or_(
                CapabilityRevocation.token_jti.is_(None),
                CapabilityRevocation.token_jti == claims.jti,
            ),
        )
    ).scalar_one_or_none()
    if revoked is not None:
        raise InvalidCapability("invalid capability")
    allowed_actions = _verified_gateway_profile_actions(
        grant,
        expected_workload_thumbprint=expected_workload_thumbprint,
    )
    if (
        action not in allowed_actions
        or action not in grant.actions
        or resource_id not in grant.resource_ids
    ):
        raise CapabilityScopeDenied("capability scope denied")
    resource = session.execute(
        select(ResourceRecord.id).where(
            ResourceRecord.tenant_id == expected_tenant_id,
            ResourceRecord.id == resource_id,
            ResourceRecord.state == "active",
            ResourceRecord.policy_class != "system_internal",
        )
    ).scalar_one_or_none()
    if resource is None:
        raise CapabilityScopeDenied("capability scope denied")
    return VerifiedCapability(
        claims=claims,
        grant_id=grant.id,
        tenant_id=grant.tenant_id,
        workspace_id=grant.workspace_id,
        runtime_instance_id=grant.runtime_instance_id,
        actor_user_id=grant.actor_user_id,
        action=action,
        resource_id=resource_id,
        constraints=dict(grant.constraints),
    )


def consume_budget(
    session: Session,
    *,
    verified: VerifiedCapability,
    calls: int = 1,
    bytes_in: int = 0,
    bytes_out: int = 0,
    cost_units: int = 1,
) -> CapabilityUsage:
    """Atomically reserve calls, total bytes, and integer cost units."""

    for name, value in {
        "calls": calls,
        "bytes_in": bytes_in,
        "bytes_out": bytes_out,
        "cost_units": cost_units,
    }.items():
        if type(value) is not int or value < 0:
            raise ValueError(f"{name} must be a non-negative integer")
    if calls == 0:
        raise ValueError("calls must be positive")

    _assert_active_ancestry(
        session,
        tenant_id=verified.tenant_id,
        grant_id=verified.grant_id,
        expected_version=verified.claims.grant_version,
        expected_depth=verified.claims.delegation_depth,
        lock=True,
        failure_type=CapabilityBudgetExceeded,
    )
    return _reserve_budget(
        session,
        tenant_id=verified.tenant_id,
        grant_id=verified.grant_id,
        grant_version=verified.claims.grant_version,
        calls=calls,
        bytes_in=bytes_in,
        bytes_out=bytes_out,
        cost_units=cost_units,
    )


def _reserve_budget(
    session: Session,
    *,
    tenant_id: str,
    grant_id: str,
    grant_version: int,
    calls: int,
    bytes_in: int,
    bytes_out: int,
    cost_units: int,
) -> CapabilityUsage:
    """Use one row-locked conditional update for use and delegation reserve."""

    now = _now()
    grant_active = exists(
        select(CapabilityGrant.id).where(
            CapabilityGrant.id == CapabilityUsage.grant_id,
            CapabilityGrant.tenant_id == tenant_id,
            CapabilityGrant.state == "active",
            CapabilityGrant.version == grant_version,
            CapabilityGrant.not_before <= now,
            CapabilityGrant.expires_at > now,
        )
    )
    max_calls = (
        select(CapabilityGrant.max_calls)
        .where(CapabilityGrant.id == CapabilityUsage.grant_id)
        .scalar_subquery()
    )
    max_bytes = (
        select(CapabilityGrant.max_bytes)
        .where(CapabilityGrant.id == CapabilityUsage.grant_id)
        .scalar_subquery()
    )
    max_cost = (
        select(CapabilityGrant.max_cost_units)
        .where(CapabilityGrant.id == CapabilityUsage.grant_id)
        .scalar_subquery()
    )
    statement = (
        update(CapabilityUsage)
        .where(
            CapabilityUsage.tenant_id == tenant_id,
            CapabilityUsage.grant_id == grant_id,
            grant_active,
            CapabilityUsage.calls + calls <= max_calls,
            CapabilityUsage.bytes_in + CapabilityUsage.bytes_out + bytes_in + bytes_out
            <= max_bytes,
            CapabilityUsage.cost_units + cost_units <= max_cost,
        )
        .values(
            calls=CapabilityUsage.calls + calls,
            bytes_in=CapabilityUsage.bytes_in + bytes_in,
            bytes_out=CapabilityUsage.bytes_out + bytes_out,
            cost_units=CapabilityUsage.cost_units + cost_units,
            updated_at=now,
        )
        .returning(CapabilityUsage)
    )
    usage = session.execute(statement).scalar_one_or_none()
    if usage is None:
        raise CapabilityBudgetExceeded("capability budget exceeded")
    return usage


def verify_and_reserve_sandbox_capability(
    session: Session,
    *,
    operation_id: str,
    grant_id: str,
    expected_tenant_id: str,
    expected_workspace_id: str,
    expected_runtime_instance_id: str,
    expected_workload_identity_digest: str,
    action: str,
) -> VerifiedSandboxCapabilityFacts:
    """Verify one Sandbox grant and reserve its budget exactly once per operation."""

    for identifier in (
        operation_id,
        grant_id,
        expected_tenant_id,
        expected_workspace_id,
        expected_runtime_instance_id,
    ):
        _validate_uuid(identifier)
    _validate_digest(expected_workload_identity_digest, "workload_identity_digest")
    if action not in SANDBOX_ACTIONS:
        raise CapabilityScopeDenied("sandbox capability action is outside the closed vocabulary")

    grant = get_grant(
        session,
        tenant_id=expected_tenant_id,
        grant_id=grant_id,
        lock=True,
    )
    now = _now()
    if (
        grant.state != "active"
        or _aware(grant.not_before) > now
        or _aware(grant.expires_at) <= now
        or grant.workspace_id != expected_workspace_id
        or grant.runtime_instance_id != expected_runtime_instance_id
        or grant.workload_identity_digest != expected_workload_identity_digest
        or grant.delegation_depth != 0
        or grant.delegation_depth_limit != 0
        or grant.parent_grant_id is not None
        or action not in grant.actions
        or not frozenset(grant.actions) <= SANDBOX_ACTIONS
        or frozenset(grant.resource_ids) != frozenset({expected_workspace_id})
    ):
        raise CapabilityScopeDenied("sandbox capability binding is not active")
    _assert_active_ancestry(
        session,
        tenant_id=expected_tenant_id,
        leaf=grant,
        lock=True,
        failure_type=CapabilityScopeDenied,
    )
    workspace = session.execute(
        select(ResourceRecord.id).where(
            ResourceRecord.tenant_id == expected_tenant_id,
            ResourceRecord.id == expected_workspace_id,
            ResourceRecord.kind == "workspace",
            ResourceRecord.state.in_(("active", "running", "paused", "stopped")),
            ResourceRecord.policy_class != "system_internal",
        )
    ).scalar_one_or_none()
    if workspace is None:
        raise CapabilityScopeDenied("sandbox workspace resource is unavailable")

    inserted = session.execute(
        pg_insert(CapabilityUsageReservation)
        .values(
            operation_id=operation_id,
            tenant_id=expected_tenant_id,
            grant_id=grant.id,
            workspace_id=expected_workspace_id,
            runtime_instance_id=expected_runtime_instance_id,
            action=action,
            calls=1,
            cost_units=1,
        )
        .on_conflict_do_nothing(index_elements=[CapabilityUsageReservation.operation_id])
        .returning(CapabilityUsageReservation.operation_id)
    ).scalar_one_or_none()
    if inserted is None:
        reservation = session.execute(
            select(CapabilityUsageReservation).where(
                CapabilityUsageReservation.operation_id == operation_id
            )
        ).scalar_one_or_none()
        if reservation is None or (
            reservation.tenant_id,
            reservation.grant_id,
            reservation.workspace_id,
            reservation.runtime_instance_id,
            reservation.action,
        ) != (
            expected_tenant_id,
            grant.id,
            expected_workspace_id,
            expected_runtime_instance_id,
            action,
        ):
            raise CapabilityScopeDenied("sandbox capability reservation binding drift")
    else:
        _reserve_budget(
            session,
            tenant_id=expected_tenant_id,
            grant_id=grant.id,
            grant_version=grant.version,
            calls=1,
            bytes_in=0,
            bytes_out=0,
            cost_units=1,
        )

    verification_digest = _canonical_digest(
        {
            "action": action,
            "expires_at": _aware(grant.expires_at).isoformat(),
            "grant_id": grant.id,
            "grant_version": grant.version,
            "operation_id": operation_id,
            "runtime_instance_id": expected_runtime_instance_id,
            "tenant_id": expected_tenant_id,
            "workload_identity_digest": expected_workload_identity_digest,
            "workspace_id": expected_workspace_id,
        }
    )
    return VerifiedSandboxCapabilityFacts(
        grant_id=grant.id,
        tenant_id=expected_tenant_id,
        workspace_id=expected_workspace_id,
        runtime_instance_id=expected_runtime_instance_id,
        workload_identity_digest=expected_workload_identity_digest,
        operation_id=operation_id,
        action=action,
        grant_version=grant.version,
        verified_at=now,
        expires_at=_aware(grant.expires_at),
        verification_digest=verification_digest,
    )


def _validate_workspace_data_reservation_request(
    *,
    operation_id: str,
    request_hash: str,
    grant_id: str,
    expected_tenant_id: str,
    expected_workspace_id: str,
    expected_runtime_instance_id: str,
    expected_workload_identity_digest: str,
    action: str,
    resource_id: str,
    resource_version: int,
    bytes_in: int,
    bytes_out_reserved: int,
    cost_units: int = 1,
) -> None:
    for value in (
        operation_id,
        grant_id,
        expected_tenant_id,
        expected_workspace_id,
        expected_runtime_instance_id,
        resource_id,
    ):
        _validate_uuid(value)
    _validate_digest(expected_workload_identity_digest, "workload_identity_digest")
    _validate_digest(request_hash, "request_hash")
    if action not in WORKSPACE_DATA_ACTIONS:
        raise CapabilityScopeDenied("workspace-data action is outside the closed vocabulary")
    for name, numeric_value in {
        "resource_version": resource_version,
        "bytes_in": bytes_in,
        "bytes_out_reserved": bytes_out_reserved,
        "cost_units": cost_units,
    }.items():
        minimum = 1 if name in {"resource_version", "cost_units"} else 0
        if type(numeric_value) is not int or numeric_value < minimum:
            raise ValueError(f"{name} must be an integer greater than or equal to {minimum}")


def verify_and_reserve_workspace_data_capability(
    session: Session,
    *,
    operation_id: str,
    request_hash: str,
    grant_id: str,
    expected_tenant_id: str,
    expected_workspace_id: str,
    expected_runtime_instance_id: str,
    expected_workload_identity_digest: str,
    action: str,
    resource_id: str,
    resource_version: int,
    bytes_in: int,
    bytes_out_reserved: int,
    cost_units: int = 1,
) -> VerifiedWorkspaceDataCapabilityFacts:
    """Verify and charge one P34.6 operation exactly once before any effect."""

    _validate_workspace_data_reservation_request(
        operation_id=operation_id,
        request_hash=request_hash,
        grant_id=grant_id,
        expected_tenant_id=expected_tenant_id,
        expected_workspace_id=expected_workspace_id,
        expected_runtime_instance_id=expected_runtime_instance_id,
        expected_workload_identity_digest=expected_workload_identity_digest,
        action=action,
        resource_id=resource_id,
        resource_version=resource_version,
        bytes_in=bytes_in,
        bytes_out_reserved=bytes_out_reserved,
        cost_units=cost_units,
    )

    grant = get_grant(
        session,
        tenant_id=expected_tenant_id,
        grant_id=grant_id,
        lock=True,
    )
    now = _now()
    if (
        grant.state != "active"
        or _aware(grant.not_before) > now
        or _aware(grant.expires_at) <= now
        or _aware(grant.expires_at) - _aware(grant.not_before) > MAX_TOKEN_TTL
        or grant.workspace_id != expected_workspace_id
        or grant.runtime_instance_id != expected_runtime_instance_id
        or grant.workload_identity_digest != expected_workload_identity_digest
        or grant.delegation_depth != 0
        or grant.delegation_depth_limit != 0
        or grant.parent_grant_id is not None
        or action not in grant.actions
        or _action_profile(frozenset(grant.actions)) != "workspace_data"
        or resource_id not in grant.resource_ids
    ):
        raise CapabilityScopeDenied("workspace-data capability binding is not active")
    _assert_active_ancestry(
        session,
        tenant_id=expected_tenant_id,
        leaf=grant,
        lock=True,
        failure_type=CapabilityScopeDenied,
    )
    revoked = session.execute(
        select(CapabilityRevocation.id).where(
            CapabilityRevocation.tenant_id == expected_tenant_id,
            CapabilityRevocation.grant_id == grant.id,
            CapabilityRevocation.token_jti.is_(None),
        )
    ).scalar_one_or_none()
    if revoked is not None:
        raise CapabilityScopeDenied("workspace-data capability is revoked")

    resource = session.execute(
        select(ResourceRecord).where(
            ResourceRecord.tenant_id == expected_tenant_id,
            ResourceRecord.id == resource_id,
        )
    ).scalar_one_or_none()
    if resource is None or resource.version != resource_version:
        raise CapabilityScopeDenied("workspace-data resource is unavailable")
    _authorize_workspace_data_resource(
        resource,
        workspace_id=expected_workspace_id,
        action=action,
    )

    operation_values = {
        "id": operation_id,
        "tenant_id": expected_tenant_id,
        "workspace_id": expected_workspace_id,
        "run_id": None,
        "actor_type": "user",
        "actor_id": grant.actor_user_id,
        "resource_id": resource_id,
        "resource_version": resource_version,
        "approval_id": None,
        "request_hash": request_hash,
        "kind": f"workspace_data.{action}",
        "state": "running",
        "risk_level": "R1",
        "progress": 0,
        "attempt_count": 1,
        "version": 1,
        "started_at": now,
        "operation_metadata": {
            "capability_profile": "workspace_data",
            "grant_id": grant.id,
            "runtime_instance_id": expected_runtime_instance_id,
        },
    }
    operation_inserted = session.execute(
        pg_insert(OperationRecord)
        .values(**operation_values)
        .on_conflict_do_nothing(index_elements=[OperationRecord.id])
        .returning(OperationRecord.id)
    ).scalar_one_or_none()
    if operation_inserted is None:
        operation = session.execute(
            select(OperationRecord)
            .where(
                OperationRecord.id == operation_id,
                OperationRecord.tenant_id == expected_tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        immutable_operation_values = {
            key: value
            for key, value in operation_values.items()
            if key
            not in {
                "state",
                "progress",
                "attempt_count",
                "version",
                "started_at",
                "operation_metadata",
            }
        }
        if operation is None or any(
            getattr(operation, key) != value for key, value in immutable_operation_values.items()
        ):
            raise WorkspaceDataReservationConflict("workspace-data operation binding drift")

    values = {
        "operation_id": operation_id,
        "tenant_id": expected_tenant_id,
        "grant_id": grant.id,
        "grant_version": grant.version,
        "workspace_id": expected_workspace_id,
        "runtime_instance_id": expected_runtime_instance_id,
        "workload_identity_digest": expected_workload_identity_digest,
        "action": action,
        "resource_id": resource_id,
        "resource_version": resource_version,
        "request_hash": request_hash,
        "calls": 1,
        "bytes_in": bytes_in,
        "bytes_out_reserved": bytes_out_reserved,
        "cost_units": cost_units,
        "state": "pending",
    }
    inserted = session.execute(
        pg_insert(WorkspaceDataUsageReservation)
        .values(**values)
        .on_conflict_do_nothing(index_elements=[WorkspaceDataUsageReservation.operation_id])
        .returning(WorkspaceDataUsageReservation.operation_id)
    ).scalar_one_or_none()
    replayed = inserted is None
    reservation_state = "pending"
    if replayed:
        reservation = session.execute(
            select(WorkspaceDataUsageReservation).where(
                WorkspaceDataUsageReservation.operation_id == operation_id
            )
        ).scalar_one_or_none()
        if reservation is None or any(
            getattr(reservation, key) != value for key, value in values.items() if key != "state"
        ):
            raise WorkspaceDataReservationConflict("workspace-data reservation binding drift")
        reservation_state = reservation.state
        if reservation_state in {"pending", "unknown"}:
            raise WorkspaceDataReplayForbidden(
                "workspace-data effect requires reconciliation before replay"
            )
        if reservation_state != "committed":
            raise WorkspaceDataReservationConflict("workspace-data reservation state is invalid")
    else:
        _reserve_budget(
            session,
            tenant_id=expected_tenant_id,
            grant_id=grant.id,
            grant_version=grant.version,
            calls=1,
            bytes_in=bytes_in,
            bytes_out=bytes_out_reserved,
            cost_units=cost_units,
        )

    verification_digest = _canonical_digest(
        {**values, "expires_at": _aware(grant.expires_at).isoformat()}
    )
    return VerifiedWorkspaceDataCapabilityFacts(
        grant_id=grant.id,
        tenant_id=expected_tenant_id,
        workspace_id=expected_workspace_id,
        runtime_instance_id=expected_runtime_instance_id,
        workload_identity_digest=expected_workload_identity_digest,
        operation_id=operation_id,
        action=action,
        resource_id=resource_id,
        resource_version=resource_version,
        request_hash=request_hash,
        grant_version=grant.version,
        reservation_state=reservation_state,
        replayed=replayed,
        verified_at=now,
        expires_at=_aware(grant.expires_at),
        verification_digest=verification_digest,
    )


def finalize_workspace_data_reservation(
    session: Session,
    *,
    operation_id: str,
    expected_state: str = "pending",
    final_state: str,
    result_digest: str | None = None,
) -> WorkspaceDataUsageReservation:
    """Move one reservation forward to committed or unknown; never back to pending."""

    _validate_uuid(operation_id)
    if expected_state != "pending" or final_state not in {"committed", "unknown"}:
        raise ValueError("workspace-data reservation transitions only from pending")
    if final_state == "committed" and result_digest is None:
        raise ValueError("committed workspace-data reservation requires result_digest")
    if final_state == "unknown" and result_digest is not None:
        raise ValueError("unknown workspace-data reservation cannot retain result_digest")
    if result_digest is not None:
        _validate_digest(result_digest, "result_digest")
    now = _now()
    row = session.execute(
        update(WorkspaceDataUsageReservation)
        .where(
            WorkspaceDataUsageReservation.operation_id == operation_id,
            WorkspaceDataUsageReservation.state == expected_state,
        )
        .values(state=final_state, result_digest=result_digest, updated_at=now)
        .returning(WorkspaceDataUsageReservation)
    ).scalar_one_or_none()
    if row is None:
        raise WorkspaceDataReplayForbidden("workspace-data reservation already finalized")
    operation_values: dict[str, object] = {
        "state": "succeeded" if final_state == "committed" else "failed",
        "progress": 100 if final_state == "committed" else 0,
        "completed_at": now,
        "updated_at": now,
        "version": OperationRecord.version + 1,
        "result_ref": ({"result_digest": result_digest} if final_state == "committed" else None),
        "error_code": (None if final_state == "committed" else "workspace_data_effect_unknown"),
        "error_detail": None,
    }
    operation_updated = session.execute(
        update(OperationRecord)
        .where(
            OperationRecord.id == operation_id,
            OperationRecord.tenant_id == row.tenant_id,
            OperationRecord.state == "running",
        )
        .values(**operation_values)
        .returning(OperationRecord.id)
    ).scalar_one_or_none()
    if operation_updated is None:
        raise WorkspaceDataReservationConflict("workspace-data operation lifecycle binding changed")
    return row


def _authorize_workspace_data_resource(
    resource: ResourceRecord,
    *,
    workspace_id: str,
    action: str,
) -> None:
    if resource.state not in {"active", "running", "paused", "stopped"}:
        raise CapabilityScopeDenied("workspace-data resource is unavailable")
    if action in {"artifact.write", "rag.derived.create"} and resource.kind == "workspace":
        if resource.id != workspace_id or resource.policy_class == "system_internal":
            raise CapabilityScopeDenied("workspace-data create scope is unavailable")
        return
    expected: dict[str, tuple[frozenset[str], str]] = {
        "data.rows.insert": (frozenset({"data_table"}), "workspace_private"),
        "data.rows.update": (frozenset({"data_table"}), "workspace_private"),
        "data.rows.delete": (frozenset({"data_table"}), "workspace_private"),
        "artifact.read": (frozenset({"artifact"}), "workspace_private"),
        "artifact.write": (frozenset({"artifact"}), "workspace_private"),
        "rag.derived.delete": (frozenset({"derived_index"}), "workspace_derived"),
    }
    kinds, policy_class = expected.get(action, (frozenset(), ""))
    if (
        resource.kind not in kinds
        or resource.policy_class != policy_class
        or resource.owner_type != "workspace"
        or resource.owner_id != workspace_id
    ):
        raise CapabilityScopeDenied("workspace-data resource scope denied")


def revoke_grant(
    session: Session,
    *,
    tenant_id: str,
    grant_id: str,
    reason_code: str,
    issuer_context: TrustedIssuerContext,
) -> CapabilityRevocation:
    """Revoke a grant online and increment its version in the same transaction."""

    _validate_issuer_context(issuer_context, tenant_id=tenant_id)
    _validate_reason_code(reason_code)
    grant = get_grant(session, tenant_id=tenant_id, grant_id=grant_id, lock=True)
    if grant.state != "active":
        raise CapabilityConflict("grant is not active")
    now = _now()
    grant.state = "revoked"
    grant.version += 1
    grant.revoked_at = now
    record = CapabilityRevocation(
        tenant_id=tenant_id,
        grant_id=grant.id,
        token_jti=None,
        reason_code=reason_code,
        actor_type="system",
        actor_id=issuer_context.system_actor_id,
    )
    session.add(record)
    return record


def revoke_token(
    session: Session,
    *,
    tenant_id: str,
    grant_id: str,
    token_jti: str,
    reason_code: str,
    issuer_context: TrustedIssuerContext,
) -> CapabilityRevocation:
    """Revoke one token JTI without invalidating sibling tokens."""

    _validate_issuer_context(issuer_context, tenant_id=tenant_id)
    _validate_reason_code(reason_code)
    grant = get_grant(session, tenant_id=tenant_id, grant_id=grant_id)
    if grant.state != "active":
        raise CapabilityConflict("grant is not active")
    if not JTI_PATTERN.fullmatch(token_jti):
        raise ValueError("token_jti has an invalid format")
    record = CapabilityRevocation(
        tenant_id=tenant_id,
        grant_id=grant.id,
        token_jti=token_jti,
        reason_code=reason_code,
        actor_type="system",
        actor_id=issuer_context.system_actor_id,
    )
    session.add(record)
    return record


def get_grant(
    session: Session,
    *,
    tenant_id: str,
    grant_id: str,
    lock: bool = False,
) -> CapabilityGrant:
    _validate_uuid(tenant_id)
    _validate_uuid(grant_id)
    statement = select(CapabilityGrant).where(
        CapabilityGrant.tenant_id == tenant_id,
        CapabilityGrant.id == grant_id,
    )
    if lock:
        statement = statement.with_for_update()
    grant = session.execute(statement).scalar_one_or_none()
    if grant is None:
        raise InvalidCapability("invalid capability")
    return grant


def _assert_active_ancestry(  # noqa: C901 - explicit fail-closed ancestry invariants
    session: Session,
    *,
    tenant_id: str,
    grant_id: str | None = None,
    leaf: CapabilityGrant | None = None,
    expected_version: int | None = None,
    expected_depth: int | None = None,
    lock: bool,
    failure_type: type[CapabilityError],
) -> CapabilityGrant:
    """Validate one bounded tenant-local delegation chain.

    Budget consumption requests row locks from the leaf towards the root.  A
    concurrent ancestor revocation therefore linearizes either before this
    check (and fails closed) or after the caller commits its reservation.  The
    helper also rejects forged cycles and inconsistent depth/scope metadata
    even if rows were inserted outside the domain service.
    """

    if leaf is None:
        if grant_id is None:
            raise ValueError("grant_id or leaf is required")
        try:
            leaf = get_grant(session, tenant_id=tenant_id, grant_id=grant_id, lock=lock)
        except InvalidCapability as exc:
            raise failure_type("capability ancestry is not active") from exc
    elif leaf.tenant_id != tenant_id:
        raise failure_type("capability ancestry is not active")

    now = _now()
    chain = [leaf]
    seen = {leaf.id}
    current = leaf
    parent_id_value = getattr(current, "parent_grant_id", None)
    while parent_id_value is not None:
        if not isinstance(parent_id_value, str) or not parent_id_value:
            raise failure_type("capability ancestry is invalid")
        if len(chain) > MAX_DELEGATION_DEPTH:
            raise failure_type("capability ancestry exceeds the maximum depth")
        parent_id = parent_id_value
        if parent_id in seen:
            raise failure_type("capability ancestry is invalid")
        seen.add(parent_id)
        try:
            current = get_grant(
                session,
                tenant_id=tenant_id,
                grant_id=parent_id,
                lock=lock,
            )
        except InvalidCapability as exc:
            raise failure_type("capability ancestry is not active") from exc
        chain.append(current)
        parent_id_value = current.parent_grant_id

    if len(chain) - 1 > MAX_DELEGATION_DEPTH:
        raise failure_type("capability ancestry exceeds the maximum depth")
    if expected_version is not None and leaf.version != expected_version:
        raise failure_type("capability ancestry is not active")
    if expected_depth is not None and leaf.delegation_depth != expected_depth:
        raise failure_type("capability ancestry is invalid")
    if leaf.delegation_depth != len(chain) - 1:
        raise failure_type("capability ancestry is invalid")

    for grant in chain:
        try:
            _validate_constraints(grant.constraints)
        except (TypeError, ValueError) as exc:
            raise failure_type("capability ancestry constraints are invalid") from exc
        if (
            grant.state != "active"
            or _aware(grant.not_before) > now
            or _aware(grant.expires_at) <= now
            or grant.approval_id is not None
            or grant.delegation_depth < 0
            or getattr(grant, "delegation_depth_limit", grant.delegation_depth)
            > MAX_DELEGATION_DEPTH
            or grant.delegation_depth
            > getattr(grant, "delegation_depth_limit", grant.delegation_depth)
        ):
            raise failure_type("capability ancestry is not active")

    root = chain[-1]
    if root.delegation_depth != 0 or getattr(root, "parent_grant_id", None) is not None:
        raise failure_type("capability ancestry is invalid")

    for child, parent in pairwise(chain):
        if (
            child.delegation_depth != parent.delegation_depth + 1
            or child.delegation_depth_limit != parent.delegation_depth_limit
            or child.tenant_id != parent.tenant_id
            or child.workspace_id != parent.workspace_id
            or child.actor_user_id != parent.actor_user_id
            or not frozenset(child.actions) <= frozenset(parent.actions)
            or not frozenset(child.resource_ids) <= frozenset(parent.resource_ids)
            or not _constraints_narrow(parent.constraints, child.constraints)
            or _aware(child.not_before) < _aware(parent.not_before)
            or _aware(child.expires_at) > _aware(parent.expires_at)
            or child.max_calls > parent.max_calls
            or child.max_bytes > parent.max_bytes
            or child.max_cost_units > parent.max_cost_units
        ):
            raise failure_type("capability ancestry is invalid")
    return leaf


def _create_grant(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    runtime_instance_id: str,
    actor_user_id: str,
    actions: set[str] | frozenset[str],
    resource_ids: set[str] | frozenset[str],
    not_before: datetime,
    expires_at: datetime,
    max_calls: int,
    max_bytes: int,
    max_cost_units: int,
    delegation_depth: int,
    delegation_depth_limit: int,
    created_by_actor_type: str,
    created_by_actor_id: str,
    constraints: dict[str, object] | None,
    approval_id: str | None,
    parent_grant_id: str | None,
    allowed_actions: frozenset[str],
    workload_identity_digest: str | None,
) -> CapabilityGrant:
    safe_actions = _validate_actions(actions, allowed_actions=allowed_actions)
    safe_resources = _validate_resource_ids(resource_ids)
    safe_constraints = _validate_constraints(constraints)
    if approval_id is not None:
        raise CapabilityScopeDenied("P34.2 read grants cannot bind an approval")
    for identifier in (
        tenant_id,
        workspace_id,
        runtime_instance_id,
        actor_user_id,
        created_by_actor_id,
    ):
        _validate_uuid(identifier)
    for optional_identifier in (approval_id, parent_grant_id):
        if optional_identifier is not None:
            _validate_uuid(optional_identifier)
    not_before = _aware(not_before)
    expires_at = _aware(expires_at)
    if expires_at <= not_before:
        raise ValueError("grant expires_at must be after not_before")
    _validate_budgets(
        max_calls=max_calls,
        max_bytes=max_bytes,
        max_cost_units=max_cost_units,
    )
    if (
        delegation_depth_limit < delegation_depth
        or delegation_depth_limit < 0
        or delegation_depth_limit > MAX_DELEGATION_DEPTH
    ):
        raise ValueError(
            "delegation depth limit must contain the current depth and be at most eight"
        )
    workspace = session.execute(
        select(ResourceRecord.id).where(
            ResourceRecord.tenant_id == tenant_id,
            ResourceRecord.id == workspace_id,
            ResourceRecord.kind == "workspace",
            ResourceRecord.state.in_(("active", "running", "paused", "stopped")),
        )
    ).scalar_one_or_none()
    if workspace is None:
        raise CapabilityConflict("workspace is not an active tenant resource")
    resources = set(
        session.execute(
            select(ResourceRecord.id).where(
                ResourceRecord.tenant_id == tenant_id,
                ResourceRecord.id.in_(safe_resources),
                ResourceRecord.state == "active",
                ResourceRecord.policy_class != "system_internal",
            )
        ).scalars()
    )
    if resources != set(safe_resources):
        raise CapabilityScopeDenied("one or more resources are unavailable")

    grant = CapabilityGrant(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        runtime_instance_id=runtime_instance_id,
        workload_identity_digest=workload_identity_digest,
        actor_user_id=actor_user_id,
        parent_grant_id=parent_grant_id,
        actions=sorted(safe_actions),
        resource_ids=sorted(safe_resources),
        constraints=safe_constraints,
        state="active",
        not_before=not_before,
        expires_at=expires_at,
        max_calls=max_calls,
        max_bytes=max_bytes,
        max_cost_units=max_cost_units,
        delegation_depth=delegation_depth,
        delegation_depth_limit=delegation_depth_limit,
        approval_id=approval_id,
        created_by_actor_type=created_by_actor_type,
        created_by_actor_id=created_by_actor_id,
    )
    session.add(grant)
    session.flush()
    session.add(CapabilityUsage(grant_id=grant.id, tenant_id=tenant_id))
    return grant


def _validate_actions(
    actions: set[str] | frozenset[str],
    *,
    allowed_actions: frozenset[str] = READ_ACTIONS,
) -> frozenset[str]:
    values = frozenset(actions)
    if not values or "*" in values or not values <= allowed_actions:
        raise CapabilityScopeDenied("capability actions are outside the selected closed vocabulary")
    return values


def _action_profile(actions: frozenset[str]) -> str:
    """Return the one closed profile containing actions; mixed profiles fail closed."""

    if actions and actions <= READ_ACTIONS:
        return "read"
    if actions and actions <= WORKSPACE_DATA_ACTIONS:
        return "workspace_data"
    if actions and actions <= SANDBOX_ACTIONS:
        return "sandbox"
    raise CapabilityScopeDenied("capability actions mix or exceed closed profiles")


def _x5t_s256_from_hex(value: str) -> str:
    _validate_digest(value, "workload_identity_digest")
    return urlsafe_b64encode(bytes.fromhex(value)).rstrip(b"=").decode("ascii")


def _validate_resource_ids(resource_ids: set[str] | frozenset[str]) -> frozenset[str]:
    values = frozenset(resource_ids)
    if not values or "*" in values:
        raise CapabilityScopeDenied("capability resource scope must be explicit")
    for value in values:
        _validate_uuid(value)
    return values


def _validate_constraints(constraints: dict[str, object] | None) -> dict[str, object]:
    values = {} if constraints is None else dict(constraints)
    if not set(values) <= _CONSTRAINT_KEYS:
        raise ValueError("unsupported capability constraint")
    if "timeout_ms" not in values:
        raise ValueError("constraint timeout_ms is required")
    for key, value in values.items():
        if type(value) is not int or value <= 0:
            raise ValueError(f"constraint {key} must be a positive integer")
        if key == "rag_top_k" and value > 100:
            raise ValueError("constraint rag_top_k cannot exceed 100")
        if key == "timeout_ms" and value > 5_000:
            raise ValueError("constraint timeout_ms cannot exceed 5000")
    return values


def _validate_budgets(*, max_calls: int, max_bytes: int, max_cost_units: int) -> None:
    for name, value in {
        "max_calls": max_calls,
        "max_bytes": max_bytes,
        "max_cost_units": max_cost_units,
    }.items():
        if type(value) is not int or value <= 0:
            raise ValueError(f"{name} must be a positive integer")


def _validate_issuer_context(
    context: TrustedIssuerContext,
    *,
    tenant_id: str,
) -> None:
    if not isinstance(context, TrustedIssuerContext) or context.tenant_id != tenant_id:
        raise CapabilityScopeDenied("trusted issuer context does not match tenant")
    for value in (context.tenant_id, context.system_actor_id, context.originating_user_id):
        _validate_uuid(value)


def _validate_platform_context(context: TrustedPlatformContext) -> None:
    if not isinstance(context, TrustedPlatformContext):
        raise CapabilityScopeDenied("trusted platform context is required")
    _validate_uuid(context.system_actor_id)


def _constraints_narrow(parent: dict[str, Any], child: dict[str, Any]) -> bool:
    for key, parent_value in parent.items():
        if key not in child or child[key] > parent_value:
            return False
    return True


def _validate_reason_code(reason_code: str) -> None:
    if not _REASON_PATTERN.fullmatch(reason_code):
        raise ValueError("reason_code has an invalid format")


def _validate_uuid(value: str) -> None:
    try:
        uuid.UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("capability identifiers must be UUIDs") from exc


def _validate_digest(value: str, name: str) -> None:
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} must be a lowercase sha256 digest")


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


__all__ = [
    "MAX_DELEGATION_DEPTH",
    "READ_ACTIONS",
    "SANDBOX_ACTIONS",
    "WORKSPACE_DATA_ACTIONS",
    "CapabilityBudgetExceeded",
    "CapabilityConflict",
    "CapabilityError",
    "CapabilityScopeDenied",
    "InvalidCapability",
    "TrustedIssuerContext",
    "TrustedPlatformContext",
    "VerifiedCapability",
    "VerifiedSandboxCapabilityFacts",
    "VerifiedWorkspaceDataCapabilityFacts",
    "WorkspaceDataReplayForbidden",
    "WorkspaceDataReservationConflict",
    "consume_budget",
    "create_grant",
    "create_sandbox_grant",
    "create_workspace_data_grant",
    "delegate_grant",
    "finalize_workspace_data_reservation",
    "get_grant",
    "issue_token",
    "register_signing_key",
    "revoke_grant",
    "revoke_token",
    "verify_and_reserve_sandbox_capability",
    "verify_and_reserve_workspace_data_capability",
    "verify_capability",
]
