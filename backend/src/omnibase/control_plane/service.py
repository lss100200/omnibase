"""Tenant-scoped domain services for the Phase 3-4 control plane.

All functions accept a caller-owned ``Session``.  They flush when a generated
identifier or optimistic update must be observed, but never commit or roll
back the caller's transaction.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, cast

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from omnibase.control_plane.models import (
    ApprovalRequest,
    AuditEvent,
    IdempotencyRecord,
    OperationRecord,
    ResourceLineage,
    ResourceRecord,
)
from omnibase.db.tenant import User


class _RowCountResult(Protocol):
    """Narrow result shape guaranteed by SQLAlchemy UPDATE execution."""

    rowcount: int


def _dml_rowcount(result: object) -> int:
    """Read the CursorResult row count after a SQLAlchemy UPDATE statement."""

    return cast("_RowCountResult", result).rowcount


class ControlPlaneError(Exception):
    """Base class for control-plane domain errors."""


class ResourceNotFound(ControlPlaneError):
    """Resource is absent or belongs to another tenant."""


class OperationNotFound(ControlPlaneError):
    """Operation is absent or belongs to another tenant."""


class ApprovalNotFound(ControlPlaneError):
    """Approval is absent or belongs to another tenant."""


class DomainConflict(ControlPlaneError):
    """Requested mutation conflicts with durable control-plane state."""


class InvalidTransition(DomainConflict):
    """A state transition is not in the explicit allowlist."""


class OptimisticLockConflict(DomainConflict):
    """A record changed after the caller read its version."""


class ApprovalConflict(DomainConflict):
    """Approval is stale, expired, self-approved, or otherwise unusable."""


class IdempotencyConflict(DomainConflict):
    """An idempotency key was reused with a different request hash."""


@dataclass(frozen=True)
class ResourceKindPolicy:
    """Extension seam for kind-specific policy without closing the namespace."""

    description: str
    may_have_parent: bool = True


RESOURCE_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
OPERATION_KIND_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{1,63}$")
_REQUEST_HASH_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_AUDIT_ACTION_PATTERN = re.compile(r"^[a-z][a-z0-9_.:-]{0,99}$")
_TRANSFORM_DIGEST_PATTERN = re.compile(r"^[a-z0-9][a-z0-9:._-]{0,127}$")
RESOURCE_KIND_POLICIES: Mapping[str, ResourceKindPolicy] = {
    "workspace": ResourceKindPolicy("Long-lived logical workspace", may_have_parent=True),
    "run": ResourceKindPolicy("Disposable execution instance"),
    "interactive_session": ResourceKindPolicy("Disposable interactive execution instance"),
    "data_table": ResourceKindPolicy("Controlled logical table"),
    "data_view": ResourceKindPolicy("Controlled logical view"),
    "document": ResourceKindPolicy("Canonical document"),
    "corpus": ResourceKindPolicy("Logical knowledge corpus"),
    "artifact": ResourceKindPolicy("Workspace or operation artifact"),
    "derived_index": ResourceKindPolicy("Derived, lineage-bearing index"),
    "snapshot": ResourceKindPolicy("Workspace snapshot"),
    "operation": ResourceKindPolicy("Logical operation resource"),
}
"""Known kind policies; safe future kinds do not require a schema migration."""

_OWNER_TYPES = frozenset({"user", "workspace", "agent", "system"})
_RESOURCE_STATES = frozenset(
    {
        "active",
        "provisioning",
        "stopped",
        "starting",
        "running",
        "pausing",
        "paused",
        "snapshotting",
        "stopping",
        "archiving",
        "archived",
        "purge_pending",
        "purged",
        "failed",
    }
)
_POLICY_CLASSES = frozenset(
    {
        "system_internal",
        "canonical_readonly",
        "tenant_managed",
        "controlled_shared",
        "workspace_private",
        "workspace_derived",
    }
)
_RISK_LEVELS = frozenset({"R0", "R1", "R2", "R3", "R4"})
_HIGH_RISK_LEVELS = frozenset({"R2", "R3", "R4"})
_APPROVAL_REQUIRED_OPERATION_STATES = frozenset(
    {"queued", "running", "cancelling", "succeeded", "compensating", "compensated"}
)
_ACTOR_TYPES = frozenset({"user", "workspace", "agent", "system"})
_REQUESTER_TYPES = frozenset({"user", "workspace", "run", "agent", "system"})
_APPROVER_ACTOR_TYPES = frozenset({"user", "system"})
_APPROVER_ROLES = frozenset({"tenant_admin", "platform_admin"})
_APPROVER_ROLE_RANK = {"tenant_admin": 1, "platform_admin": 2}
_OPERATION_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "pending_approval": frozenset({"failed", "cancelled"}),
    "queued": frozenset({"running", "cancelling", "cancelled", "failed"}),
    "running": frozenset({"cancelling", "succeeded", "failed", "compensating"}),
    "cancelling": frozenset({"cancelled", "failed", "compensating"}),
    "succeeded": frozenset(),
    "failed": frozenset({"compensating"}),
    "cancelled": frozenset(),
    "compensating": frozenset({"compensated", "failed"}),
    "compensated": frozenset(),
}
_APPROVAL_DECISIONS = frozenset({"approved", "rejected"})
_LINEAGE_RELATIONS = frozenset(
    {
        "derived_from",
        "transformed_from",
        "snapshot_of",
        "restored_from",
        "published_from",
    }
)
_SENSITIVE_METADATA_KEYS = frozenset(
    {
        "physical_locator",
        "schema_name",
        "minio_key",
        "object_key",
        "host_path",
        "database_url",
        "connection_string",
        "authorization",
        "token",
        "password",
        "api_key",
        "provider_handle",
        "sql",
        "prompt",
        "file_bytes",
        "credential",
        "credentials",
    }
)
_SENSITIVE_METADATA_SUFFIXES = tuple(f"_{key}" for key in _SENSITIVE_METADATA_KEYS)
_AUDIT_DETAIL_KEYS = frozenset(
    {
        "reason_code",
        "policy_code",
        "resource_kind",
        "operation_kind",
        "from_state",
        "to_state",
        "error_code",
        "approval_state",
        "idempotency_state",
        "retryable",
        "limit",
        "offset",
    }
)
_AUDIT_CODE_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def _now() -> datetime:
    return datetime.now(UTC)


def _ensure_aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _redact_sensitive_metadata(value: Any) -> Any:
    """Keep physical locators and credentials out of public/audit metadata."""
    if isinstance(value, dict):
        return {
            key: _redact_sensitive_metadata(item)
            for key, item in value.items()
            if not _is_sensitive_metadata_key(key)
        }
    if isinstance(value, list):
        return [_redact_sensitive_metadata(item) for item in value]
    return value


def _is_sensitive_metadata_key(key: object) -> bool:
    raw_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key))
    normalized = "_".join(part for part in re.split(r"[^a-z0-9]+", raw_key.casefold()) if part)
    return normalized in _SENSITIVE_METADATA_KEYS or normalized.endswith(
        _SENSITIVE_METADATA_SUFFIXES
    )


def _validate_resource_kind(kind: str) -> None:
    if not RESOURCE_KIND_PATTERN.fullmatch(kind):
        raise ValueError("Resource kind must match ^[a-z][a-z0-9_]{1,63}$")


def _validate_operation_kind(kind: str) -> None:
    if not OPERATION_KIND_PATTERN.fullmatch(kind):
        raise ValueError("Operation kind must match ^[a-z][a-z0-9_.:-]{1,63}$")


def _validate_request_hash(request_hash: str) -> None:
    if not _REQUEST_HASH_PATTERN.fullmatch(request_hash):
        raise ValueError("request_hash must be 64 lowercase hexadecimal characters")


def _validate_pagination(limit: int, offset: int) -> None:
    if not 1 <= limit <= 200:
        raise ValueError("limit must be between 1 and 200")
    if offset < 0:
        raise ValueError("offset must be non-negative")


def _validate_choice(value: str, choices: frozenset[str], field: str) -> None:
    if value not in choices:
        raise ValueError(f"Unsupported {field}: {value!r}")


def _validate_audit_details(details: dict[str, object] | None) -> dict[str, object]:
    """Accept only short, explicitly classified audit attributes."""
    if details is None:
        return {}
    safe: dict[str, object] = {}
    for key, value in details.items():
        if key not in _AUDIT_DETAIL_KEYS:
            raise ValueError(f"Unsupported audit detail key: {key!r}")
        if key == "retryable":
            if type(value) is not bool:
                raise ValueError("audit detail retryable must be a boolean")
        elif key in {"limit", "offset"}:
            if type(value) is not int or value < 0:
                raise ValueError(f"audit detail {key} must be a non-negative integer")
        elif not isinstance(value, str) or not _AUDIT_CODE_PATTERN.fullmatch(value):
            raise ValueError(f"audit detail {key} must be a short code-like string")
        safe[key] = value
    return safe


def _get_resource_of_kind(
    session: Session,
    *,
    tenant_id: str,
    resource_id: str,
    kinds: frozenset[str],
    field: str,
) -> ResourceRecord:
    resource = get_resource(session, tenant_id=tenant_id, resource_id=resource_id)
    if resource.kind not in kinds:
        expected = ", ".join(sorted(kinds))
        raise DomainConflict(f"{field} must reference resource kind: {expected}")
    return resource


def _validate_workspace_and_run(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str | None,
    run_id: str | None,
) -> tuple[ResourceRecord | None, ResourceRecord | None]:
    workspace = None
    run = None
    if workspace_id is not None:
        workspace = _get_resource_of_kind(
            session,
            tenant_id=tenant_id,
            resource_id=workspace_id,
            kinds=frozenset({"workspace"}),
            field="workspace_id",
        )
    if run_id is not None:
        run = _get_resource_of_kind(
            session,
            tenant_id=tenant_id,
            resource_id=run_id,
            kinds=frozenset({"run", "interactive_session"}),
            field="run_id",
        )
        if workspace_id is not None and run.parent_id != workspace_id:
            raise DomainConflict("run_id is not bound to workspace_id")
    return workspace, run


def _validate_tenant_user(
    session: Session,
    *,
    user_id: str,
    field: str,
    require_tenant_admin: bool = False,
) -> None:
    filters = [User.id == user_id, User.is_active.is_(True)]
    if require_tenant_admin:
        filters.append(User.is_tenant_admin.is_(True))
    user_exists = session.execute(select(User.id).where(*filters)).scalar_one_or_none()
    if user_exists is None:
        requirement = "active tenant admin" if require_tenant_admin else "active tenant user"
        raise DomainConflict(f"{field} must reference an {requirement}")


def _validate_resource_actor(
    session: Session,
    *,
    tenant_id: str,
    actor_type: str,
    actor_id: str | None,
) -> None:
    if actor_type != "system" and not actor_id:
        raise ValueError(f"actor_id is required for {actor_type} actors")
    if actor_type == "user":
        assert actor_id is not None
        _validate_tenant_user(session, user_id=actor_id, field="actor_id")
    elif actor_type in {"workspace", "agent"}:
        assert actor_id is not None
        _get_resource_of_kind(
            session,
            tenant_id=tenant_id,
            resource_id=actor_id,
            kinds=frozenset({actor_type}),
            field="actor_id",
        )
    elif actor_type == "run":
        assert actor_id is not None
        _get_resource_of_kind(
            session,
            tenant_id=tenant_id,
            resource_id=actor_id,
            kinds=frozenset({"run", "interactive_session"}),
            field="actor_id",
        )


def _validate_resource_owner(
    session: Session,
    *,
    tenant_id: str,
    owner_type: str,
    owner_id: str | None,
) -> None:
    if owner_type == "system":
        if owner_id is not None:
            raise ValueError("system-owned resources must not set owner_id")
        return
    if not owner_id:
        raise ValueError(f"owner_id is required for {owner_type} ownership")
    if owner_type == "user":
        _validate_tenant_user(session, user_id=owner_id, field="owner_id")
        return
    _get_resource_of_kind(
        session,
        tenant_id=tenant_id,
        resource_id=owner_id,
        kinds=frozenset({owner_type}),
        field="owner_id",
    )


def _validate_audit_references(
    session: Session,
    *,
    tenant_id: str,
    actor_type: str,
    actor_id: str | None,
    workspace_id: str | None,
    run_id: str | None,
    resource_id: str | None,
    approval_id: str | None,
    operation_id: str | None,
) -> None:
    _validate_resource_actor(
        session,
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
    )
    _validate_workspace_and_run(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
    )
    if resource_id is not None:
        get_resource(session, tenant_id=tenant_id, resource_id=resource_id)
    if operation_id is not None:
        get_operation(session, tenant_id=tenant_id, operation_id=operation_id)
    if approval_id is not None:
        get_approval(session, tenant_id=tenant_id, approval_id=approval_id)


def _required_approver_role(risk_level: str) -> str:
    return "platform_admin" if risk_level == "R4" else "tenant_admin"


def _approval_matches_operation(
    approval: ApprovalRequest,
    operation: OperationRecord,
) -> bool:
    requester_type = operation.actor_type
    return all(
        (
            approval.requester_type == requester_type,
            approval.requester_id == operation.actor_id,
            approval.workspace_id == operation.workspace_id,
            approval.run_id == operation.run_id,
            approval.resource_id == operation.resource_id,
            approval.operation_id == operation.id,
            approval.action == operation.kind,
            approval.risk_level == operation.risk_level,
            approval.required_approver_role == _required_approver_role(operation.risk_level),
            approval.request_hash == operation.request_hash,
            approval.resource_version == operation.resource_version,
        )
    )


def register_resource(
    session: Session,
    *,
    tenant_id: str,
    kind: str,
    owner_type: str,
    display_name: str,
    policy_class: str,
    resource_id: str | None = None,
    owner_id: str | None = None,
    parent_id: str | None = None,
    state: str = "active",
    physical_locator: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
    created_by_actor_id: str | None = None,
) -> ResourceRecord:
    """Register a tenant-owned logical resource without committing.

    ``resource_id`` pins the registry row to a caller-owned logical identity
    (e.g. an Agent Registry entity id) instead of a server-generated one.
    """
    _validate_resource_kind(kind)
    _validate_choice(owner_type, _OWNER_TYPES, "owner_type")
    _validate_choice(state, _RESOURCE_STATES, "resource state")
    _validate_choice(policy_class, _POLICY_CLASSES, "policy_class")
    if not tenant_id:
        raise ValueError("tenant_id is required")
    if not display_name or len(display_name) > 200:
        raise ValueError("display_name must contain 1-200 characters")
    _validate_resource_owner(
        session,
        tenant_id=tenant_id,
        owner_type=owner_type,
        owner_id=owner_id,
    )
    if parent_id is not None:
        get_resource(session, tenant_id=tenant_id, resource_id=parent_id)

    resource = ResourceRecord(
        id=resource_id,
        tenant_id=tenant_id,
        kind=kind,
        owner_type=owner_type,
        owner_id=owner_id,
        parent_id=parent_id,
        display_name=display_name,
        state=state,
        policy_class=policy_class,
        physical_locator=physical_locator,
        resource_metadata=_redact_sensitive_metadata(metadata or {}),
        created_by_actor_id=created_by_actor_id,
    )
    session.add(resource)
    session.flush()
    return resource


def get_resource(
    session: Session,
    *,
    tenant_id: str,
    resource_id: str,
) -> ResourceRecord:
    """Return a resource in the tenant, using the same 404 semantics for IDOR."""
    resource = session.execute(
        select(ResourceRecord).where(
            ResourceRecord.id == resource_id,
            ResourceRecord.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if resource is None:
        raise ResourceNotFound("Resource not found")
    return resource


def list_resources(
    session: Session,
    *,
    tenant_id: str,
    limit: int,
    offset: int,
    kind: str | None = None,
    state: str | None = None,
) -> tuple[list[ResourceRecord], int]:
    """List tenant resources with bounded pagination and optional filters."""
    _validate_pagination(limit, offset)
    filters: list[Any] = [ResourceRecord.tenant_id == tenant_id]
    if kind is not None:
        _validate_resource_kind(kind)
        filters.append(ResourceRecord.kind == kind)
    if state is not None:
        _validate_choice(state, _RESOURCE_STATES, "resource state")
        filters.append(ResourceRecord.state == state)

    total = session.scalar(select(func.count()).select_from(ResourceRecord).where(*filters)) or 0
    items = list(
        session.scalars(
            select(ResourceRecord)
            .where(*filters)
            .order_by(ResourceRecord.created_at.desc(), ResourceRecord.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, int(total)


def append_resource_lineage(
    session: Session,
    *,
    tenant_id: str,
    source_resource_id: str,
    derived_resource_id: str,
    relation: str,
    source_version: int,
    transform_digest: str | None = None,
    created_by_operation_id: str | None = None,
) -> ResourceLineage:
    """Append a tenant-scoped lineage edge bound to the source's version."""

    session.execute(
        select(
            func.pg_advisory_xact_lock(
                func.hashtextextended(f"omnibase:resource-lineage:{tenant_id}", 0)
            )
        )
    )
    _validate_choice(relation, _LINEAGE_RELATIONS, "lineage relation")
    if source_resource_id == derived_resource_id:
        raise DomainConflict("A resource cannot derive from itself")
    source = get_resource(
        session,
        tenant_id=tenant_id,
        resource_id=source_resource_id,
    )
    get_resource(
        session,
        tenant_id=tenant_id,
        resource_id=derived_resource_id,
    )
    if source.version != source_version:
        raise OptimisticLockConflict("Source resource version changed")
    if transform_digest is not None and not _TRANSFORM_DIGEST_PATTERN.fullmatch(transform_digest):
        raise ValueError("transform_digest has an invalid format")
    if created_by_operation_id is not None:
        get_operation(
            session,
            tenant_id=tenant_id,
            operation_id=created_by_operation_id,
        )

    lineage = ResourceLineage(
        tenant_id=tenant_id,
        source_resource_id=source_resource_id,
        derived_resource_id=derived_resource_id,
        relation=relation,
        source_version=source_version,
        transform_digest=transform_digest,
        created_by_operation_id=created_by_operation_id,
    )
    session.add(lineage)
    session.flush()
    return lineage


def list_resource_lineage(
    session: Session,
    *,
    tenant_id: str,
    limit: int,
    offset: int,
    source_resource_id: str | None = None,
    derived_resource_id: str | None = None,
    relation: str | None = None,
) -> tuple[list[ResourceLineage], int]:
    """List append-only lineage edges within one tenant."""
    _validate_pagination(limit, offset)
    filters: list[Any] = [ResourceLineage.tenant_id == tenant_id]
    if source_resource_id is not None:
        filters.append(ResourceLineage.source_resource_id == source_resource_id)
    if derived_resource_id is not None:
        filters.append(ResourceLineage.derived_resource_id == derived_resource_id)
    if relation is not None:
        _validate_choice(relation, _LINEAGE_RELATIONS, "lineage relation")
        filters.append(ResourceLineage.relation == relation)
    total = session.scalar(select(func.count()).select_from(ResourceLineage).where(*filters)) or 0
    items = list(
        session.scalars(
            select(ResourceLineage)
            .where(*filters)
            .order_by(ResourceLineage.created_at.desc(), ResourceLineage.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, int(total)


def append_audit_event(
    session: Session,
    *,
    tenant_id: str,
    request_id: str,
    actor_type: str,
    action: str,
    decision: str,
    risk_level: str,
    actor_id: str | None = None,
    workspace_id: str | None = None,
    run_id: str | None = None,
    grant_id: str | None = None,
    resource_id: str | None = None,
    approval_id: str | None = None,
    operation_id: str | None = None,
    input_hash: str | None = None,
    before_version: int | None = None,
    after_version: int | None = None,
    status_code: int | None = None,
    row_count: int | None = None,
    bytes_in: int | None = None,
    bytes_out: int | None = None,
    duration_ms: int | None = None,
    details: dict[str, object] | None = None,
) -> AuditEvent:
    """Append one audit row.  No update/delete counterpart exists by design."""
    _validate_choice(actor_type, _REQUESTER_TYPES, "actor_type")
    _validate_choice(decision, frozenset({"allowed", "denied", "error"}), "decision")
    _validate_choice(risk_level, _RISK_LEVELS, "risk_level")
    if not _REQUEST_ID_PATTERN.fullmatch(request_id):
        raise ValueError("request_id must be 1-64 safe identifier characters")
    if not _AUDIT_ACTION_PATTERN.fullmatch(action):
        raise ValueError("audit action must be a bounded code-like identifier")
    if input_hash is not None:
        _validate_request_hash(input_hash)
    safe_details = _validate_audit_details(details)
    if decision == "allowed":
        _validate_audit_references(
            session,
            tenant_id=tenant_id,
            actor_type=actor_type,
            actor_id=actor_id,
            workspace_id=workspace_id,
            run_id=run_id,
            resource_id=resource_id,
            approval_id=approval_id,
            operation_id=operation_id,
        )
    event = AuditEvent(
        tenant_id=tenant_id,
        request_id=request_id,
        actor_type=actor_type,
        actor_id=actor_id,
        workspace_id=workspace_id,
        run_id=run_id,
        grant_id=grant_id,
        resource_id=resource_id,
        approval_id=approval_id,
        operation_id=operation_id,
        action=action,
        decision=decision,
        risk_level=risk_level,
        input_hash=input_hash,
        before_version=before_version,
        after_version=after_version,
        status_code=status_code,
        row_count=row_count,
        bytes_in=bytes_in,
        bytes_out=bytes_out,
        duration_ms=duration_ms,
        details=safe_details,
    )
    session.add(event)
    session.flush()
    return event


def list_audit_events(
    session: Session,
    *,
    tenant_id: str,
    limit: int,
    offset: int,
    action: str | None = None,
    resource_id: str | None = None,
) -> tuple[list[AuditEvent], int]:
    _validate_pagination(limit, offset)
    filters: list[Any] = [AuditEvent.tenant_id == tenant_id]
    if action is not None:
        filters.append(AuditEvent.action == action)
    if resource_id is not None:
        filters.append(AuditEvent.resource_id == resource_id)
    total = session.scalar(select(func.count()).select_from(AuditEvent).where(*filters)) or 0
    items = list(
        session.scalars(
            select(AuditEvent)
            .where(*filters)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, int(total)


def create_operation(
    session: Session,
    *,
    tenant_id: str,
    kind: str,
    risk_level: str,
    actor_type: str,
    request_hash: str | None = None,
    workspace_id: str | None = None,
    run_id: str | None = None,
    actor_id: str | None = None,
    resource_id: str | None = None,
    resource_version: int | None = None,
    approval_id: str | None = None,
    deadline_at: datetime | None = None,
    metadata: dict[str, object] | None = None,
) -> OperationRecord:
    _validate_operation_kind(kind)
    _validate_choice(risk_level, _RISK_LEVELS, "risk_level")
    _validate_choice(actor_type, _ACTOR_TYPES, "actor_type")
    if request_hash is None:
        raise ValueError("request_hash is required")
    _validate_request_hash(request_hash)
    _validate_resource_actor(
        session,
        tenant_id=tenant_id,
        actor_type=actor_type,
        actor_id=actor_id,
    )
    _validate_workspace_and_run(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
    )
    if resource_id is not None:
        resource = get_resource(session, tenant_id=tenant_id, resource_id=resource_id)
        if resource_version is None or resource.version != resource_version:
            raise DomainConflict("Operation must bind the current resource version")
    elif resource_version is not None:
        raise DomainConflict("resource_version requires resource_id")
    if deadline_at is not None and _ensure_aware(deadline_at) <= _now():
        raise ValueError("Operation deadline must be in the future")
    if approval_id is not None:
        get_approval(session, tenant_id=tenant_id, approval_id=approval_id)
        raise ApprovalConflict("Create the operation before its approval; authorize it separately")

    operation = OperationRecord(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
        actor_type=actor_type,
        actor_id=actor_id,
        resource_id=resource_id,
        resource_version=resource_version,
        approval_id=approval_id,
        request_hash=request_hash,
        kind=kind,
        state="pending_approval" if risk_level in _HIGH_RISK_LEVELS else "queued",
        risk_level=risk_level,
        deadline_at=deadline_at,
        operation_metadata=_redact_sensitive_metadata(metadata or {}),
    )
    session.add(operation)
    session.flush()
    return operation


def get_operation(
    session: Session,
    *,
    tenant_id: str,
    operation_id: str,
) -> OperationRecord:
    operation = session.execute(
        select(OperationRecord).where(
            OperationRecord.id == operation_id,
            OperationRecord.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if operation is None:
        raise OperationNotFound("Operation not found")
    return operation


def list_operations(
    session: Session,
    *,
    tenant_id: str,
    limit: int,
    offset: int,
    state: str | None = None,
    resource_id: str | None = None,
) -> tuple[list[OperationRecord], int]:
    _validate_pagination(limit, offset)
    filters: list[Any] = [OperationRecord.tenant_id == tenant_id]
    if state is not None:
        if state not in _OPERATION_TRANSITIONS:
            raise ValueError(f"Unsupported operation state: {state!r}")
        filters.append(OperationRecord.state == state)
    if resource_id is not None:
        filters.append(OperationRecord.resource_id == resource_id)
    total = session.scalar(select(func.count()).select_from(OperationRecord).where(*filters)) or 0
    items = list(
        session.scalars(
            select(OperationRecord)
            .where(*filters)
            .order_by(OperationRecord.created_at.desc(), OperationRecord.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, int(total)


def _validate_consumed_operation_approval(
    session: Session,
    *,
    tenant_id: str,
    operation: OperationRecord,
) -> ApprovalRequest:
    if operation.approval_id is None:
        raise ApprovalConflict("High-risk operation has no consumed approval")
    approval = get_approval(
        session,
        tenant_id=tenant_id,
        approval_id=operation.approval_id,
    )
    if approval.state != "consumed" or approval.consumed_at is None:
        raise ApprovalConflict("High-risk operation approval is not consumed")
    if not _approval_matches_operation(approval, operation):
        raise ApprovalConflict("High-risk operation approval binding changed")
    if operation.resource_id is not None:
        resource = get_resource(
            session,
            tenant_id=tenant_id,
            resource_id=operation.resource_id,
        )
        if resource.version != operation.resource_version:
            raise ApprovalConflict("Resource version changed after authorization")
    return approval


def _validate_operation_transition(
    session: Session,
    *,
    tenant_id: str,
    operation: OperationRecord,
    target_state: str,
    progress: int | None,
    result_ref: dict[str, object] | None,
    error_code: str | None,
    error_detail: str | None,
) -> None:
    if target_state not in _OPERATION_TRANSITIONS.get(operation.state, frozenset()):
        raise InvalidTransition(
            f"Cannot transition operation {operation.state!r} to {target_state!r}"
        )
    if progress is not None and not 0 <= progress <= 100:
        raise ValueError("progress must be between 0 and 100")
    if result_ref is not None and target_state not in {"succeeded", "compensated"}:
        raise ValueError("result_ref is only valid for succeeded or compensated operations")
    if (error_code is not None or error_detail is not None) and target_state not in {
        "failed",
        "cancelled",
    }:
        raise ValueError("error fields are only valid for failed or cancelled operations")
    if error_code is not None and not _AUDIT_CODE_PATTERN.fullmatch(error_code):
        raise ValueError("error_code must be a short code-like string")
    if (
        target_state == "running"
        and operation.deadline_at is not None
        and _ensure_aware(operation.deadline_at) <= _now()
    ):
        raise InvalidTransition("Operation deadline has expired")
    if (
        operation.risk_level in _HIGH_RISK_LEVELS
        and target_state in _APPROVAL_REQUIRED_OPERATION_STATES
    ):
        _validate_consumed_operation_approval(
            session,
            tenant_id=tenant_id,
            operation=operation,
        )


def _operation_transition_values(
    *,
    expected_version: int,
    target_state: str,
    progress: int | None,
    result_ref: dict[str, object] | None,
    error_code: str | None,
    error_detail: str | None,
) -> dict[str, Any]:
    now = _now()
    values: dict[str, Any] = {"state": target_state, "version": expected_version + 1}
    if progress is not None:
        values["progress"] = progress
    if target_state == "running":
        values.update(started_at=now, attempt_count=OperationRecord.attempt_count + 1)
    if target_state in {"succeeded", "failed", "cancelled", "compensated"}:
        values["completed_at"] = now
    if target_state in {"succeeded", "compensated"} and progress is None:
        values["progress"] = 100
    if result_ref is not None:
        values["result_ref"] = _redact_sensitive_metadata(result_ref)
    if error_code is not None:
        values["error_code"] = error_code
    if error_detail is not None:
        values["error_detail"] = error_detail[:1000]
    return values


def transition_operation(
    session: Session,
    *,
    tenant_id: str,
    operation_id: str,
    expected_version: int,
    target_state: str,
    progress: int | None = None,
    result_ref: dict[str, object] | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
) -> OperationRecord:
    """Apply an allowlisted transition using an optimistic version predicate."""
    operation = get_operation(session, tenant_id=tenant_id, operation_id=operation_id)
    if operation.version != expected_version:
        raise OptimisticLockConflict("Operation version changed")
    _validate_operation_transition(
        session,
        tenant_id=tenant_id,
        operation=operation,
        target_state=target_state,
        progress=progress,
        result_ref=result_ref,
        error_code=error_code,
        error_detail=error_detail,
    )
    values = _operation_transition_values(
        expected_version=expected_version,
        target_state=target_state,
        progress=progress,
        result_ref=result_ref,
        error_code=error_code,
        error_detail=error_detail,
    )

    result = session.execute(
        update(OperationRecord)
        .where(
            OperationRecord.id == operation_id,
            OperationRecord.tenant_id == tenant_id,
            OperationRecord.version == expected_version,
            OperationRecord.state == operation.state,
        )
        .values(**values)
    )
    if _dml_rowcount(result) != 1:
        raise OptimisticLockConflict("Operation changed during transition")
    session.refresh(operation)
    return operation


def _validate_approval_requester(
    session: Session,
    *,
    tenant_id: str,
    requester_type: str,
    requester_id: str | None,
) -> None:
    if requester_type != "system" and not requester_id:
        raise ValueError("requester_id is required for non-system requesters")
    _validate_resource_actor(
        session,
        tenant_id=tenant_id,
        actor_type=requester_type,
        actor_id=requester_id,
    )


def _validate_approval_operation_binding(
    operation: OperationRecord,
    *,
    requester_type: str,
    requester_id: str | None,
    action: str,
    risk_level: str,
    request_hash: str,
    workspace_id: str | None,
    run_id: str | None,
    resource_id: str | None,
    resource_version: int | None,
) -> None:
    if risk_level in _HIGH_RISK_LEVELS and operation.state != "pending_approval":
        raise ApprovalConflict("High-risk approval requires a pending_approval operation")
    if any(
        (
            operation.workspace_id != workspace_id,
            operation.run_id != run_id,
            operation.resource_id != resource_id,
            operation.resource_version != resource_version,
            operation.kind != action,
            operation.risk_level != risk_level,
            operation.request_hash != request_hash,
        )
    ):
        raise ApprovalConflict("Approval does not match operation bindings")
    if operation.actor_type != requester_type or operation.actor_id != requester_id:
        raise ApprovalConflict("Approval requester does not match operation actor")


def create_approval(
    session: Session,
    *,
    tenant_id: str,
    requester_type: str,
    requester_id: str | None,
    action: str,
    risk_level: str,
    request_hash: str,
    expires_at: datetime,
    grant_id: str | None = None,
    workspace_id: str | None = None,
    run_id: str | None = None,
    resource_id: str | None = None,
    resource_version: int | None = None,
    operation_id: str | None = None,
    required_approver_role: str | None = None,
    metadata: dict[str, object] | None = None,
) -> ApprovalRequest:
    _validate_choice(requester_type, _REQUESTER_TYPES, "requester_type")
    _validate_choice(risk_level, _RISK_LEVELS, "risk_level")
    _validate_operation_kind(action)
    _validate_request_hash(request_hash)
    if _ensure_aware(expires_at) <= _now():
        raise ValueError("Approval expiry must be in the future")
    _validate_approval_requester(
        session,
        tenant_id=tenant_id,
        requester_type=requester_type,
        requester_id=requester_id,
    )
    _validate_workspace_and_run(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
    )
    if resource_id is not None:
        resource = get_resource(session, tenant_id=tenant_id, resource_id=resource_id)
        if resource_version is None or resource.version != resource_version:
            raise ApprovalConflict("Approval must bind the current resource version")
    elif resource_version is not None:
        raise ApprovalConflict("resource_version requires resource_id")
    if risk_level in _HIGH_RISK_LEVELS and not operation_id:
        raise ApprovalConflict("High-risk approval must bind an operation_id")
    if operation_id is not None:
        operation = get_operation(
            session,
            tenant_id=tenant_id,
            operation_id=operation_id,
        )
        _validate_approval_operation_binding(
            operation,
            requester_type=requester_type,
            requester_id=requester_id,
            action=action,
            risk_level=risk_level,
            request_hash=request_hash,
            workspace_id=workspace_id,
            run_id=run_id,
            resource_id=resource_id,
            resource_version=resource_version,
        )

    if not grant_id:
        raise ValueError("grant_id must be bound when an approval is created")
    expected_role = _required_approver_role(risk_level)
    if required_approver_role is not None and required_approver_role != expected_role:
        raise ValueError("required_approver_role does not match risk_level")

    approval = ApprovalRequest(
        tenant_id=tenant_id,
        requester_type=requester_type,
        requester_id=requester_id,
        workspace_id=workspace_id,
        run_id=run_id,
        resource_id=resource_id,
        operation_id=operation_id,
        grant_id=grant_id,
        action=action,
        risk_level=risk_level,
        required_approver_role=expected_role,
        state="pending",
        request_hash=request_hash,
        resource_version=resource_version,
        expires_at=expires_at,
        approval_metadata=_redact_sensitive_metadata(metadata or {}),
    )
    session.add(approval)
    session.flush()
    return approval


def get_approval(
    session: Session,
    *,
    tenant_id: str,
    approval_id: str,
) -> ApprovalRequest:
    approval = session.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if approval is None:
        raise ApprovalNotFound("Approval not found")
    return approval


def list_approvals(
    session: Session,
    *,
    tenant_id: str,
    limit: int,
    offset: int,
    state: str | None = None,
    resource_id: str | None = None,
) -> tuple[list[ApprovalRequest], int]:
    _validate_pagination(limit, offset)
    filters: list[Any] = [ApprovalRequest.tenant_id == tenant_id]
    if state is not None:
        _validate_choice(
            state,
            frozenset(
                {"draft", "pending", "approved", "rejected", "expired", "cancelled", "consumed"}
            ),
            "approval state",
        )
        filters.append(ApprovalRequest.state == state)
    if resource_id is not None:
        filters.append(ApprovalRequest.resource_id == resource_id)
    total = session.scalar(select(func.count()).select_from(ApprovalRequest).where(*filters)) or 0
    items = list(
        session.scalars(
            select(ApprovalRequest)
            .where(*filters)
            .order_by(ApprovalRequest.created_at.desc(), ApprovalRequest.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, int(total)


def _validate_approver(
    session: Session,
    approval: ApprovalRequest,
    *,
    tenant_id: str,
    decided_by_actor_type: str | None,
    decided_by_actor_id: str | None,
    decided_by_actor_role: str | None,
) -> None:
    if decided_by_actor_type is None:
        raise ValueError("decided_by_actor_type is required")
    _validate_choice(
        decided_by_actor_type,
        _APPROVER_ACTOR_TYPES,
        "decided_by_actor_type",
    )
    if not decided_by_actor_id:
        raise ValueError("decided_by_actor_id is required for all approvers")
    if decided_by_actor_role is None:
        raise ValueError("decided_by_actor_role is required")
    _validate_choice(
        decided_by_actor_role,
        _APPROVER_ROLES,
        "decided_by_actor_role",
    )
    if decided_by_actor_type == "user":
        if decided_by_actor_role == "platform_admin":
            raise ApprovalConflict("User approvers cannot assert platform_admin in P34.1")
        _validate_tenant_user(
            session,
            user_id=decided_by_actor_id,
            field="decided_by_actor_id",
            require_tenant_admin=True,
        )
    required_rank = _APPROVER_ROLE_RANK[approval.required_approver_role]
    if _APPROVER_ROLE_RANK[decided_by_actor_role] < required_rank:
        raise ApprovalConflict("Approver role is insufficient for this risk level")


def _validate_approval_decision_bindings(
    session: Session,
    *,
    tenant_id: str,
    approval: ApprovalRequest,
    request_hash: str,
    resource_version: int | None,
) -> None:
    if approval.request_hash != request_hash or approval.resource_version != resource_version:
        raise ApprovalConflict("Approval request binding changed")
    if approval.resource_id is not None:
        resource = get_resource(
            session,
            tenant_id=tenant_id,
            resource_id=approval.resource_id,
        )
        if resource.version != resource_version:
            raise ApprovalConflict("Resource version changed before approval")
    if approval.operation_id is None:
        if approval.risk_level in _HIGH_RISK_LEVELS:
            raise ApprovalConflict("High-risk approval lost its operation binding")
        return
    operation = get_operation(
        session,
        tenant_id=tenant_id,
        operation_id=approval.operation_id,
    )
    if not _approval_matches_operation(approval, operation):
        raise ApprovalConflict("Approval operation binding changed")
    if approval.risk_level in _HIGH_RISK_LEVELS and operation.state != "pending_approval":
        raise ApprovalConflict("Bound operation is no longer pending approval")


def decide_approval(
    session: Session,
    *,
    tenant_id: str,
    approval_id: str,
    expected_version: int,
    decided_by_actor_id: str | None,
    decision: str,
    decision_reason: str | None,
    request_hash: str,
    resource_version: int | None,
    decided_by_actor_type: str | None = None,
    decided_by_actor_role: str | None = None,
) -> ApprovalRequest:
    """Approve/reject a pending request bound to exact request/resource versions."""
    _validate_choice(decision, _APPROVAL_DECISIONS, "approval decision")
    _validate_request_hash(request_hash)
    approval = get_approval(session, tenant_id=tenant_id, approval_id=approval_id)
    if approval.version != expected_version:
        raise OptimisticLockConflict("Approval version changed")
    if approval.state != "pending":
        raise ApprovalConflict("Only pending approvals can be decided")
    if approval.requester_id is not None and approval.requester_id == decided_by_actor_id:
        raise ApprovalConflict("Requester cannot decide their own approval")
    if _ensure_aware(approval.expires_at) <= _now():
        raise ApprovalConflict("Approval request expired")
    _validate_approval_decision_bindings(
        session,
        tenant_id=tenant_id,
        approval=approval,
        request_hash=request_hash,
        resource_version=resource_version,
    )
    _validate_approver(
        session,
        approval,
        tenant_id=tenant_id,
        decided_by_actor_type=decided_by_actor_type,
        decided_by_actor_id=decided_by_actor_id,
        decided_by_actor_role=decided_by_actor_role,
    )

    now = _now()
    result = session.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.version == expected_version,
            ApprovalRequest.state == "pending",
        )
        .values(
            state=decision,
            decided_by_actor_type=decided_by_actor_type,
            decided_by_actor_id=decided_by_actor_id,
            decision_reason=(decision_reason or "")[:1000] or None,
            decided_at=now,
            version=expected_version + 1,
        )
    )
    if _dml_rowcount(result) != 1:
        raise OptimisticLockConflict("Approval changed during decision")
    session.refresh(approval)
    return approval


def _consume_operation_approval(
    session: Session,
    *,
    tenant_id: str,
    approval_id: str,
    expected_version: int,
    consumer_actor_type: str,
    consumer_actor_id: str | None,
    action: str,
    workspace_id: str | None,
    run_id: str | None,
    operation_id: str,
    request_hash: str,
    resource_version: int | None,
    grant_id: str,
) -> ApprovalRequest:
    """Consume an approved request exactly once with the original bindings."""
    _validate_choice(consumer_actor_type, _REQUESTER_TYPES, "consumer_actor_type")
    _validate_request_hash(request_hash)
    _validate_resource_actor(
        session,
        tenant_id=tenant_id,
        actor_type=consumer_actor_type,
        actor_id=consumer_actor_id,
    )
    _validate_workspace_and_run(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        run_id=run_id,
    )
    operation = get_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
    )
    approval = get_approval(session, tenant_id=tenant_id, approval_id=approval_id)
    if approval.version != expected_version:
        raise OptimisticLockConflict("Approval version changed")
    if approval.state != "approved" or approval.consumed_at is not None:
        raise ApprovalConflict("Approval is not consumable")
    if _ensure_aware(approval.expires_at) <= _now():
        raise ApprovalConflict("Approval expired before consumption")
    if any(
        (
            approval.requester_type != consumer_actor_type,
            approval.requester_id != consumer_actor_id,
            approval.action != action,
            approval.workspace_id != workspace_id,
            approval.run_id != run_id,
            approval.operation_id != operation_id,
            approval.request_hash != request_hash,
            approval.resource_version != resource_version,
            approval.grant_id != grant_id,
        )
    ):
        raise ApprovalConflict("Approval binding changed before consumption")
    if not _approval_matches_operation(approval, operation):
        raise ApprovalConflict("Approval does not match the bound operation")
    if approval.resource_id is not None:
        resource = get_resource(
            session,
            tenant_id=tenant_id,
            resource_id=approval.resource_id,
        )
        if resource.version != resource_version:
            raise ApprovalConflict("Resource version changed before consumption")

    result = session.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.id == approval_id,
            ApprovalRequest.tenant_id == tenant_id,
            ApprovalRequest.version == expected_version,
            ApprovalRequest.state == "approved",
            ApprovalRequest.consumed_at.is_(None),
        )
        .values(
            state="consumed",
            consumed_at=_now(),
            version=expected_version + 1,
        )
    )
    if _dml_rowcount(result) != 1:
        raise ApprovalConflict("Approval was already consumed or changed")
    session.refresh(approval)
    return approval


def authorize_operation(
    session: Session,
    *,
    tenant_id: str,
    operation_id: str,
    expected_version: int,
    approval_id: str,
    approval_expected_version: int,
    consumer_actor_type: str,
    consumer_actor_id: str | None,
    action: str,
    workspace_id: str | None,
    run_id: str | None,
    request_hash: str,
    resource_version: int | None,
    grant_id: str,
) -> OperationRecord:
    """Atomically consume a bound approval and queue a high-risk operation.

    The caller owns the transaction. Any exception must cause that transaction
    to roll back so approval consumption and operation authorization cannot
    become partially durable.
    """
    operation = get_operation(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
    )
    if operation.version != expected_version:
        raise OptimisticLockConflict("Operation version changed")
    if operation.state != "pending_approval":
        raise InvalidTransition("Only pending_approval operations can be authorized")
    if operation.risk_level not in _HIGH_RISK_LEVELS:
        raise ApprovalConflict("Low-risk operation does not require authorization")
    if operation.approval_id is not None and operation.approval_id != approval_id:
        raise ApprovalConflict("Operation is bound to a different approval")
    if any(
        (
            operation.actor_type != consumer_actor_type,
            operation.actor_id != consumer_actor_id,
            operation.kind != action,
            operation.workspace_id != workspace_id,
            operation.run_id != run_id,
            operation.request_hash != request_hash,
            operation.resource_version != resource_version,
        )
    ):
        raise ApprovalConflict("Operation authorization binding changed")
    if operation.deadline_at is not None and _ensure_aware(operation.deadline_at) <= _now():
        raise ApprovalConflict("Operation deadline expired before authorization")

    approval = get_approval(
        session,
        tenant_id=tenant_id,
        approval_id=approval_id,
    )
    if approval.version != approval_expected_version:
        raise OptimisticLockConflict("Approval version changed")
    if not _approval_matches_operation(approval, operation):
        raise ApprovalConflict("Approval does not match operation bindings")

    _consume_operation_approval(
        session,
        tenant_id=tenant_id,
        approval_id=approval_id,
        expected_version=approval_expected_version,
        consumer_actor_type=consumer_actor_type,
        consumer_actor_id=consumer_actor_id,
        action=action,
        workspace_id=workspace_id,
        run_id=run_id,
        operation_id=operation_id,
        request_hash=request_hash,
        resource_version=resource_version,
        grant_id=grant_id,
    )
    result = session.execute(
        update(OperationRecord)
        .where(
            OperationRecord.id == operation_id,
            OperationRecord.tenant_id == tenant_id,
            OperationRecord.version == expected_version,
            OperationRecord.state == "pending_approval",
        )
        .values(
            state="queued",
            approval_id=approval_id,
            version=expected_version + 1,
        )
    )
    if _dml_rowcount(result) != 1:
        raise OptimisticLockConflict("Operation changed during authorization")
    session.refresh(operation)
    return operation


def reserve_idempotency(
    session: Session,
    *,
    tenant_id: str,
    actor_scope: str,
    operation_name: str,
    key: str,
    request_hash: str,
    expires_at: datetime,
    operation_id: str | None = None,
) -> tuple[IdempotencyRecord, bool]:
    """Reserve a scope/key or return its existing record on an exact replay."""
    if not actor_scope or not operation_name or not key:
        raise ValueError("actor_scope, operation_name, key, and request_hash are required")
    _validate_request_hash(request_hash)
    if _ensure_aware(expires_at) <= _now():
        raise ValueError("Idempotency expiry must be in the future")
    if operation_id is not None:
        get_operation(session, tenant_id=tenant_id, operation_id=operation_id)

    insert_stmt = (
        pg_insert(IdempotencyRecord)
        .values(
            tenant_id=tenant_id,
            actor_scope=actor_scope,
            operation_name=operation_name,
            key=key,
            request_hash=request_hash,
            state="pending",
            operation_id=operation_id,
            expires_at=expires_at,
        )
        .on_conflict_do_nothing(
            index_elements=[
                IdempotencyRecord.tenant_id,
                IdempotencyRecord.actor_scope,
                IdempotencyRecord.operation_name,
                IdempotencyRecord.key,
            ]
        )
        .returning(IdempotencyRecord.id)
    )
    inserted_id = session.execute(insert_stmt).scalar_one_or_none()
    filters = (
        IdempotencyRecord.tenant_id == tenant_id,
        IdempotencyRecord.actor_scope == actor_scope,
        IdempotencyRecord.operation_name == operation_name,
        IdempotencyRecord.key == key,
    )
    record = session.execute(select(IdempotencyRecord).where(*filters)).scalar_one()
    if record.request_hash != request_hash:
        raise IdempotencyConflict("Idempotency key was reused with different input")
    return record, inserted_id is not None


def _finish_idempotency(
    session: Session,
    *,
    tenant_id: str,
    record_id: str,
    target_state: str,
    expected_version: int | None,
    response_ref: dict[str, object] | None,
    operation_id: str | None,
) -> IdempotencyRecord:
    record = session.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.id == record_id,
            IdempotencyRecord.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if record is None:
        raise IdempotencyConflict("Idempotency record not found")
    version = record.version if expected_version is None else expected_version
    if record.version != version:
        raise OptimisticLockConflict("Idempotency record version changed")
    if record.state != "pending":
        if record.state == target_state:
            return record
        raise IdempotencyConflict("Idempotency record already finalized")
    bound_operation_id = operation_id or record.operation_id
    if bound_operation_id is not None:
        get_operation(
            session,
            tenant_id=tenant_id,
            operation_id=bound_operation_id,
        )
    result = session.execute(
        update(IdempotencyRecord)
        .where(
            IdempotencyRecord.id == record_id,
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.version == version,
            IdempotencyRecord.state == "pending",
        )
        .values(
            state=target_state,
            version=version + 1,
            response_ref=_redact_sensitive_metadata(response_ref),
            operation_id=bound_operation_id,
        )
    )
    if _dml_rowcount(result) != 1:
        raise OptimisticLockConflict("Idempotency record changed during completion")
    session.refresh(record)
    return record


def complete_idempotency(
    session: Session,
    *,
    tenant_id: str,
    record_id: str,
    response_ref: dict[str, object] | None = None,
    operation_id: str | None = None,
    expected_version: int | None = None,
) -> IdempotencyRecord:
    return _finish_idempotency(
        session,
        tenant_id=tenant_id,
        record_id=record_id,
        target_state="completed",
        expected_version=expected_version,
        response_ref=response_ref,
        operation_id=operation_id,
    )


def fail_idempotency(
    session: Session,
    *,
    tenant_id: str,
    record_id: str,
    response_ref: dict[str, object] | None = None,
    operation_id: str | None = None,
    expected_version: int | None = None,
) -> IdempotencyRecord:
    return _finish_idempotency(
        session,
        tenant_id=tenant_id,
        record_id=record_id,
        target_state="failed",
        expected_version=expected_version,
        response_ref=response_ref,
        operation_id=operation_id,
    )


__all__ = [
    "RESOURCE_KIND_PATTERN",
    "RESOURCE_KIND_POLICIES",
    "ApprovalConflict",
    "ApprovalNotFound",
    "ControlPlaneError",
    "DomainConflict",
    "IdempotencyConflict",
    "InvalidTransition",
    "OperationNotFound",
    "OptimisticLockConflict",
    "ResourceKindPolicy",
    "ResourceNotFound",
    "append_audit_event",
    "append_resource_lineage",
    "authorize_operation",
    "complete_idempotency",
    "create_approval",
    "create_operation",
    "decide_approval",
    "fail_idempotency",
    "get_approval",
    "get_operation",
    "get_resource",
    "list_approvals",
    "list_audit_events",
    "list_operations",
    "list_resource_lineage",
    "list_resources",
    "register_resource",
    "reserve_idempotency",
    "transition_operation",
]
