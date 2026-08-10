"""P34.4 Workspace membership, lifecycle, template, and run-lease services."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from omnibase.capabilities.models import CapabilityGrant
from omnibase.control_plane.models import IdempotencyRecord
from omnibase.control_plane.service import (
    IdempotencyConflict,
    append_audit_event,
    append_resource_lineage,
    complete_idempotency,
    get_resource,
    register_resource,
    reserve_idempotency,
)
from omnibase.db.tenant import User
from omnibase.workspaces.contracts import VerifiedRunLeaseFacts, WorkspaceReconciler
from omnibase.workspaces.models import (
    NetworkLease,
    NodeAttestation,
    PeerGrant,
    ResourceScopeBinding,
    RunLease,
    ServiceAdvertisement,
    Workspace,
    WorkspaceAuthority,
    WorkspaceMembership,
    WorkspaceNode,
    WorkspaceRun,
    WorkspaceScopeGrant,
    WorkspaceSnapshot,
    WorkspaceTemplate,
)

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_ACTION = re.compile(r"^[a-z][a-z0-9_.:-]{1,99}$")
_TEMPLATE_KEY = re.compile(r"^[a-z][a-z0-9_.-]{1,99}$")
_FORBIDDEN_TEMPLATE_KEYS = (
    "authorization",
    "credential",
    "database_url",
    "docker_socket",
    "env",
    "host_path",
    "ip_address",
    "lease_id",
    "minio",
    "object_key",
    "node_id",
    "password",
    "physical_locator",
    "private_key",
    "provider_handle",
    "runtime_instance_id",
    "redis_url",
    "secret",
    "token",
    "authority_id",
    "authority_epoch",
    "fencing_token",
    "vpn_key",
    "workload_identity",
)
_FORBIDDEN_TEMPLATE_VALUE_MARKERS = (
    ".env",
    "authorization:",
    "bearer ",
    "docker.sock",
    "postgresql://",
    "redis://",
    "sk-",
)
_WINDOWS_ABSOLUTE_PATH = re.compile(r"(?:^|[\s'\"=:(])(?:[A-Za-z]:[\\/]|\\\\[^\\\s]+[\\/][^\\\s]+)")
_UNIX_HOST_PATH = re.compile(
    r"(?:^|[\s'\"=:(])/(?:dev|etc|home|mnt|opt|proc|root|run|srv|sys|tmp|usr|var)(?:/|\b)"
)
_ACTIVE_RUN_STATES = frozenset({"leased", "starting", "running", "pausing", "stopping"})
_TERMINAL_RUN_STATES = frozenset({"stopped", "succeeded", "failed", "cancelled"})
_SCOPE_GRANT_ACTIONS = frozenset({"resource.list", "resource.read"})
_RUN_STATE_TRANSITIONS = {
    "queued": frozenset({"leased", "cancelled"}),
    "leased": frozenset({"starting", "failed", "cancelled"}),
    "starting": frozenset({"running", "failed", "cancelled"}),
    "running": frozenset({"paused", "stopped", "succeeded", "failed", "cancelled"}),
    "pausing": frozenset({"paused", "failed", "cancelled"}),
    "paused": frozenset({"running", "stopped", "cancelled"}),
    "stopping": frozenset({"stopped", "succeeded", "failed", "cancelled"}),
}
_ROLE_RANK = {"viewer": 1, "member": 2, "operator": 3, "maintainer": 4, "owner": 5}
_ACTION_ROLE = {
    "workspace.read": "viewer",
    "workspace.run": "member",
    "workspace.lifecycle": "operator",
    "workspace.members.manage": "maintainer",
    "workspace.grants.manage": "maintainer",
    "workspace.snapshot": "operator",
    "workspace.restore": "maintainer",
    "workspace.nodes.manage": "maintainer",
    "workspace.data.read": "viewer",
    "workspace.data.write": "member",
    "workspace.data.publish": "owner",
}


class WorkspaceError(Exception):
    """Base P34.4 domain failure."""


class WorkspaceNotFound(WorkspaceError):
    """Missing or unauthorized resources deliberately share one outcome."""


class WorkspaceConflict(WorkspaceError):
    """A version, generation, idempotency, or state conflict."""


class WorkspacePolicyDenied(WorkspaceNotFound):
    """Fail-closed authorization result with IDOR-safe semantics."""


class TemplateRejected(WorkspaceConflict):
    """Unsafe or non-reproducible template manifest."""


class LeaseRejected(WorkspaceConflict):
    """Expired, stale, revoked, or incorrectly fenced lease."""


def _db_now(session: Session) -> datetime:
    value = session.execute(select(func.now())).scalar_one()
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def get_active_attested_node(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    node_id: str,
    lock: bool = False,
) -> WorkspaceNode:
    """Resolve one live Node and re-check attestation expiry against DB time."""
    statement = select(WorkspaceNode).where(
        WorkspaceNode.id == node_id,
        WorkspaceNode.tenant_id == tenant_id,
        WorkspaceNode.workspace_id == workspace_id,
    )
    if lock:
        statement = statement.with_for_update()
    node = session.execute(statement).scalar_one_or_none()
    if (
        node is None
        or node.state != "active"
        or node.attestation_state != "verified"
        or node.revoked_at is not None
    ):
        raise WorkspaceNotFound("workspace node not found")
    now = _db_now(session)
    attestation = session.execute(
        select(NodeAttestation)
        .where(
            NodeAttestation.tenant_id == tenant_id,
            NodeAttestation.node_id == node_id,
            NodeAttestation.state == "verified",
            NodeAttestation.verified_at <= now,
            NodeAttestation.expires_at > now,
        )
        .order_by(NodeAttestation.expires_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if attestation is None:
        raise WorkspaceNotFound("workspace node not found")
    return node


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _request_hash(kind: str, payload: Mapping[str, object]) -> str:
    return canonical_digest({"kind": kind, "payload": dict(payload)})


def _validate_digest(value: str, field: str) -> None:
    if not _DIGEST.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def _validate_safe_metadata(value: object, *, path: str = "metadata") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            normalized = str(key).lower()
            if any(marker in normalized for marker in _FORBIDDEN_TEMPLATE_KEYS):
                raise TemplateRejected(f"unsafe metadata key at {path}")
            _validate_safe_metadata(nested, path=f"{path}.{normalized}")
        return
    if isinstance(value, list):
        for index, nested in enumerate(value):
            _validate_safe_metadata(nested, path=f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.lower()
        if any(marker in lowered for marker in _FORBIDDEN_TEMPLATE_VALUE_MARKERS):
            raise TemplateRejected(f"unsafe metadata value at {path}")
        if _WINDOWS_ABSOLUTE_PATH.search(value) or _UNIX_HOST_PATH.search(value):
            raise TemplateRejected(f"host or absolute path forbidden at {path}")


def validate_template_spec(spec: Mapping[str, object]) -> str:
    """Return the canonical digest after rejecting credentials and host/runtime state."""
    if not spec:
        raise TemplateRejected("template spec must not be empty")
    _validate_safe_metadata(spec, path="template")
    return canonical_digest(spec)


def _get_template(session: Session, *, tenant_id: str, template_id: str) -> WorkspaceTemplate:
    template = session.execute(
        select(WorkspaceTemplate).where(
            WorkspaceTemplate.id == template_id,
            WorkspaceTemplate.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if template is None:
        raise WorkspaceNotFound("workspace template not found")
    return template


def register_template(
    session: Session,
    *,
    tenant_id: str,
    actor_user_id: str,
    template_key: str,
    version: int,
    display_name: str,
    template_spec: Mapping[str, object],
    supersedes_template_id: str | None = None,
    request_id: str,
) -> WorkspaceTemplate:
    if not _TEMPLATE_KEY.fullmatch(template_key):
        raise TemplateRejected("template key has an invalid format")
    if version < 1:
        raise TemplateRejected("template version must be positive")
    actor = session.execute(
        select(User)
        .where(
            User.id == actor_user_id,
            User.is_active.is_(True),
            User.is_tenant_admin.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if actor is None:
        raise WorkspacePolicyDenied("tenant governance access is unavailable")
    digest = validate_template_spec(template_spec)
    input_hash = _request_hash(
        "workspace_template.register",
        {
            "template_key": template_key,
            "version": version,
            "display_name": display_name,
            "template_spec": dict(template_spec),
            "supersedes_template_id": supersedes_template_id,
        },
    )
    if supersedes_template_id is not None:
        previous = _get_template(
            session,
            tenant_id=tenant_id,
            template_id=supersedes_template_id,
        )
        if previous.template_key != template_key or previous.version >= version:
            raise TemplateRejected("template supersession must advance the same key")
    inserted_id = session.execute(
        pg_insert(WorkspaceTemplate)
        .values(
            tenant_id=tenant_id,
            template_key=template_key,
            version=version,
            display_name=display_name,
            digest=digest,
            template_spec=dict(template_spec),
            state="active",
            supersedes_template_id=supersedes_template_id,
            created_by_user_id=actor_user_id,
        )
        .on_conflict_do_nothing(
            index_elements=[
                WorkspaceTemplate.tenant_id,
                WorkspaceTemplate.template_key,
                WorkspaceTemplate.version,
            ]
        )
        .returning(WorkspaceTemplate.id)
    ).scalar_one_or_none()
    record = session.execute(
        select(WorkspaceTemplate)
        .where(
            WorkspaceTemplate.tenant_id == tenant_id,
            WorkspaceTemplate.template_key == template_key,
            WorkspaceTemplate.version == version,
        )
        .with_for_update()
    ).scalar_one()
    if (
        record.digest != digest
        or record.template_spec != dict(template_spec)
        or record.display_name != display_name
        or record.supersedes_template_id != supersedes_template_id
    ):
        raise WorkspaceConflict("template version already exists with different content")
    created = inserted_id is not None
    append_audit_event(
        session,
        tenant_id=tenant_id,
        request_id=request_id,
        actor_type="user",
        actor_id=actor_user_id,
        action="workspace_template.register",
        decision="allowed",
        risk_level="R1",
        input_hash=input_hash,
        status_code=201 if created else 200,
        details={"resource_kind": "workspace_template"},
    )
    return record


def list_templates(
    session: Session,
    *,
    tenant_id: str,
    limit: int,
    offset: int,
) -> tuple[list[WorkspaceTemplate], int]:
    filters = (
        WorkspaceTemplate.tenant_id == tenant_id,
        WorkspaceTemplate.state.in_(("active", "deprecated")),
    )
    total = session.execute(
        select(func.count()).select_from(WorkspaceTemplate).where(*filters)
    ).scalar_one()
    items = list(
        session.scalars(
            select(WorkspaceTemplate)
            .where(*filters)
            .order_by(WorkspaceTemplate.template_key, WorkspaceTemplate.version.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, int(total)


def _get_membership(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    lock: bool = False,
) -> WorkspaceMembership | None:
    statement = select(WorkspaceMembership).where(
        WorkspaceMembership.tenant_id == tenant_id,
        WorkspaceMembership.workspace_id == workspace_id,
        WorkspaceMembership.user_id == user_id,
        WorkspaceMembership.state.in_(("active", "suspended")),
    )
    if lock:
        statement = statement.with_for_update()
    return session.execute(statement).scalar_one_or_none()


def authorize_workspace_action(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    action: str,
    lock: bool = False,
) -> WorkspaceMembership:
    required = _ACTION_ROLE.get(action)
    if required is None:
        raise WorkspacePolicyDenied("unknown workspace action")
    membership = _get_membership(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        lock=lock,
    )
    if (
        membership is None
        or membership.state != "active"
        or _ROLE_RANK[membership.role] < _ROLE_RANK[required]
    ):
        raise WorkspacePolicyDenied("workspace not found")
    return membership


def get_workspace(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    user_id: str,
    action: str = "workspace.read",
    lock: bool = False,
) -> Workspace:
    if lock:
        workspace = session.execute(
            select(Workspace)
            .where(
                Workspace.id == workspace_id,
                Workspace.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if workspace is None:
            raise WorkspaceNotFound("workspace not found")
        authorize_workspace_action(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            lock=True,
        )
        return workspace
    authorize_workspace_action(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=user_id,
        action=action,
    )
    statement = select(Workspace).where(
        Workspace.id == workspace_id,
        Workspace.tenant_id == tenant_id,
    )
    workspace = session.execute(statement).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFound("workspace not found")
    return workspace


def _lock_workspace_aggregate(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
) -> Workspace:
    workspace = session.execute(
        select(Workspace)
        .where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFound("workspace not found")
    return workspace


def list_workspaces(
    session: Session,
    *,
    tenant_id: str,
    user_id: str,
    limit: int,
    offset: int,
) -> tuple[list[Workspace], int]:
    membership_filter = (
        WorkspaceMembership.tenant_id == tenant_id,
        WorkspaceMembership.user_id == user_id,
        WorkspaceMembership.state == "active",
    )
    total = session.execute(
        select(func.count())
        .select_from(WorkspaceMembership)
        .join(Workspace, Workspace.id == WorkspaceMembership.workspace_id)
        .where(*membership_filter)
    ).scalar_one()
    items = list(
        session.scalars(
            select(Workspace)
            .join(WorkspaceMembership, WorkspaceMembership.workspace_id == Workspace.id)
            .where(*membership_filter, Workspace.tenant_id == tenant_id)
            .order_by(Workspace.created_at.desc(), Workspace.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return items, int(total)


def _replay_id(
    session: Session,
    *,
    record: IdempotencyRecord,
    response_key: str,
) -> str | None:
    if record.state == "completed" and record.response_ref:
        value = record.response_ref.get(response_key)
        return str(value) if value is not None else None
    if record.state != "pending":
        raise WorkspaceConflict("idempotent operation previously failed")
    return None


def create_workspace(
    session: Session,
    *,
    tenant_id: str,
    actor_user_id: str,
    display_name: str,
    template_id: str,
    quota: Mapping[str, int],
    parent_workspace_id: str | None,
    idempotency_key: str,
    request_id: str,
) -> Workspace:
    template = _get_template(session, tenant_id=tenant_id, template_id=template_id)
    if template.state != "active":
        raise WorkspaceConflict("workspace template is not active")
    if any(value < 0 for value in quota.values()):
        raise WorkspaceConflict("workspace quota values must be non-negative")
    if parent_workspace_id is not None:
        get_workspace(
            session,
            tenant_id=tenant_id,
            workspace_id=parent_workspace_id,
            user_id=actor_user_id,
            action="workspace.lifecycle",
        )
    request_hash = _request_hash(
        "workspace.create",
        {
            "display_name": display_name,
            "template_id": template_id,
            "quota": dict(quota),
            "parent_workspace_id": parent_workspace_id,
        },
    )
    try:
        idem, inserted = reserve_idempotency(
            session,
            tenant_id=tenant_id,
            actor_scope=f"user:{actor_user_id}",
            operation_name="workspace.create",
            key=idempotency_key,
            request_hash=request_hash,
            expires_at=_db_now(session) + timedelta(hours=24),
        )
    except IdempotencyConflict as exc:
        raise WorkspaceConflict(str(exc)) from exc
    if not inserted:
        workspace_id = _replay_id(session, record=idem, response_key="workspace_id")
        if workspace_id is None:
            raise WorkspaceConflict("workspace creation is already in progress")
        return get_workspace(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=actor_user_id,
        )
    resource = register_resource(
        session,
        tenant_id=tenant_id,
        kind="workspace",
        owner_type="user",
        owner_id=actor_user_id,
        parent_id=parent_workspace_id,
        display_name=display_name,
        state="stopped",
        policy_class="workspace_private",
        created_by_actor_id=actor_user_id,
    )
    workspace = Workspace(
        id=resource.id,
        tenant_id=tenant_id,
        template_id=template.id,
        owner_user_id=actor_user_id,
        parent_workspace_id=parent_workspace_id,
        display_name=display_name,
        desired_state="stopped",
        observed_state="stopped",
        generation=1,
        version=1,
        quota=dict(quota),
    )
    # The composite scope and membership foreign keys are immediate.  Flush
    # the Workspace aggregate explicitly before its dependent rows instead of
    # relying on SQLAlchemy to infer ordering across the control-plane model
    # graph.  All rows still live in the same caller-owned transaction.
    session.add(workspace)
    session.flush()
    session.add_all(
        [
            ResourceScopeBinding(
                resource_id=resource.id,
                tenant_id=tenant_id,
                scope_class="workspace_private",
                workspace_id=resource.id,
            ),
            WorkspaceMembership(
                tenant_id=tenant_id,
                workspace_id=resource.id,
                user_id=actor_user_id,
                role="owner",
                state="active",
                created_by_user_id=actor_user_id,
            ),
        ]
    )
    session.flush()
    complete_idempotency(
        session,
        tenant_id=tenant_id,
        record_id=idem.id,
        expected_version=idem.version,
        response_ref={"workspace_id": resource.id},
    )
    append_audit_event(
        session,
        tenant_id=tenant_id,
        request_id=request_id,
        actor_type="user",
        actor_id=actor_user_id,
        workspace_id=resource.id,
        resource_id=resource.id,
        action="workspace.create",
        decision="allowed",
        risk_level="R1",
        input_hash=request_hash,
        after_version=1,
        status_code=201,
        details={"resource_kind": "workspace"},
    )
    return workspace


def list_memberships(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
) -> list[WorkspaceMembership]:
    authorize_workspace_action(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.read",
    )
    return list(
        session.scalars(
            select(WorkspaceMembership)
            .where(
                WorkspaceMembership.tenant_id == tenant_id,
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.state.in_(("active", "suspended")),
            )
            .order_by(WorkspaceMembership.created_at, WorkspaceMembership.id)
        )
    )


def _active_owner_count(session: Session, *, tenant_id: str, workspace_id: str) -> int:
    return int(
        session.execute(
            select(func.count())
            .select_from(WorkspaceMembership)
            .where(
                WorkspaceMembership.tenant_id == tenant_id,
                WorkspaceMembership.workspace_id == workspace_id,
                WorkspaceMembership.role == "owner",
                WorkspaceMembership.state == "active",
            )
        ).scalar_one()
    )


def upsert_membership(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    target_user_id: str,
    role: str,
    expected_version: int | None,
    request_id: str,
) -> WorkspaceMembership:
    _lock_workspace_aggregate(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    actor = authorize_workspace_action(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.members.manage",
        lock=True,
    )
    if role not in _ROLE_RANK:
        raise WorkspaceConflict("unknown workspace role")
    if role == "owner" and actor.role != "owner":
        raise WorkspacePolicyDenied("only an owner can add another owner")
    target_user = session.execute(
        select(User).where(User.id == target_user_id, User.is_active.is_(True)).with_for_update()
    ).scalar_one_or_none()
    if target_user is None:
        raise WorkspaceNotFound("workspace member not found")
    membership = _get_membership(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=target_user_id,
        lock=True,
    )
    previous_version = membership.version if membership is not None else None
    previous_role = membership.role if membership is not None else "none"
    if membership is None:
        membership = WorkspaceMembership(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=target_user_id,
            role=role,
            state="active",
            created_by_user_id=actor_user_id,
        )
        session.add(membership)
    else:
        if expected_version is not None and membership.version != expected_version:
            raise WorkspaceConflict("workspace membership version changed")
        if membership.role == "owner" and role != "owner" and actor.role != "owner":
            raise WorkspacePolicyDenied("only an owner can change another owner")
        if (
            membership.role == "owner"
            and role != "owner"
            and _active_owner_count(session, tenant_id=tenant_id, workspace_id=workspace_id) <= 1
        ):
            raise WorkspaceConflict("the last workspace owner cannot be demoted")
        membership.role = role
        membership.state = "active"
        membership.version += 1
    session.flush()
    append_audit_event(
        session,
        tenant_id=tenant_id,
        request_id=request_id,
        actor_type="user",
        actor_id=actor_user_id,
        workspace_id=workspace_id,
        action="workspace.membership.upsert",
        decision="allowed",
        risk_level="R1",
        input_hash=_request_hash(
            "workspace.membership.upsert",
            {"target_user_id": target_user_id, "role": role},
        ),
        before_version=previous_version,
        after_version=membership.version,
        status_code=200,
        details={"from_state": previous_role, "to_state": role},
    )
    return membership


def set_membership_state(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    target_user_id: str,
    state: str,
    request_id: str,
) -> WorkspaceMembership:
    _lock_workspace_aggregate(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    actor = authorize_workspace_action(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.members.manage",
        lock=True,
    )
    if state not in {"suspended", "revoked"}:
        raise WorkspaceConflict("invalid membership state")
    membership = _get_membership(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=target_user_id,
        lock=True,
    )
    if membership is None:
        raise WorkspaceNotFound("workspace member not found")
    if membership.role == "owner":
        if actor.role != "owner":
            raise WorkspacePolicyDenied("only an owner can change another owner")
        if _active_owner_count(session, tenant_id=tenant_id, workspace_id=workspace_id) <= 1:
            raise WorkspaceConflict("the last workspace owner cannot be disabled")
    if membership.state == state:
        return membership
    previous_state = membership.state
    previous_version = membership.version
    membership.state = state
    membership.version += 1
    session.flush()
    append_audit_event(
        session,
        tenant_id=tenant_id,
        request_id=request_id,
        actor_type="user",
        actor_id=actor_user_id,
        workspace_id=workspace_id,
        action="workspace.membership.state",
        decision="allowed",
        risk_level="R1",
        input_hash=_request_hash(
            "workspace.membership.state",
            {"target_user_id": target_user_id, "state": state},
        ),
        before_version=previous_version,
        after_version=membership.version,
        status_code=200,
        details={"from_state": previous_state, "to_state": state},
    )
    return membership


def create_scope_grant(
    session: Session,
    *,
    tenant_id: str,
    target_workspace_id: str,
    actor_user_id: str,
    actor_is_tenant_admin: bool,
    source_scope: str,
    source_owner_id: str | None,
    resource_id: str,
    actions: list[str],
    expires_at: datetime | None,
    request_id: str,
) -> WorkspaceScopeGrant:
    authorize_workspace_action(
        session,
        tenant_id=tenant_id,
        workspace_id=target_workspace_id,
        user_id=actor_user_id,
        action="workspace.grants.manage",
    )
    if not actions or len(actions) > 32 or any(not _ACTION.fullmatch(item) for item in actions):
        raise WorkspaceConflict("scope grant actions are invalid")
    if not set(actions).issubset(_SCOPE_GRANT_ACTIONS):
        raise WorkspaceConflict("scope grant actions exceed the P34.4 allowlist")
    binding = session.execute(
        select(ResourceScopeBinding).where(
            ResourceScopeBinding.resource_id == resource_id,
            ResourceScopeBinding.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if binding is None or binding.scope_class != source_scope:
        raise WorkspaceNotFound("scoped resource not found")
    actual_owner = binding.user_id or binding.workspace_id
    if actual_owner != source_owner_id:
        raise WorkspaceNotFound("scoped resource not found")
    if source_scope == "user_private" and source_owner_id != actor_user_id:
        raise WorkspacePolicyDenied("private user scope requires its owner")
    if source_scope in {"workspace_private", "workspace_shared"}:
        if source_owner_id is None:
            raise WorkspaceConflict("workspace scope requires an owner")
        authorize_workspace_action(
            session,
            tenant_id=tenant_id,
            workspace_id=source_owner_id,
            user_id=actor_user_id,
            action="workspace.grants.manage",
        )
    if source_scope == "tenant_shared" and not actor_is_tenant_admin:
        raise WorkspacePolicyDenied("tenant shared scope requires tenant governance")
    now = _db_now(session)
    if expires_at is not None:
        expires_at = expires_at.replace(tzinfo=UTC) if expires_at.tzinfo is None else expires_at
        if expires_at <= now:
            raise WorkspaceConflict("scope grant expiry must be in the future")
    grant = WorkspaceScopeGrant(
        tenant_id=tenant_id,
        target_workspace_id=target_workspace_id,
        source_scope=source_scope,
        source_owner_id=source_owner_id,
        resource_id=resource_id,
        actions=sorted(set(actions)),
        state="active",
        expires_at=expires_at,
        created_by_user_id=actor_user_id,
    )
    session.add(grant)
    session.flush()
    append_audit_event(
        session,
        tenant_id=tenant_id,
        request_id=request_id,
        actor_type="user",
        actor_id=actor_user_id,
        workspace_id=target_workspace_id,
        grant_id=grant.id,
        resource_id=resource_id,
        action="workspace.scope_grant.create",
        decision="allowed",
        risk_level="R1",
        input_hash=_request_hash(
            "workspace.scope_grant.create",
            {
                "target_workspace_id": target_workspace_id,
                "source_scope": source_scope,
                "source_owner_id": source_owner_id,
                "resource_id": resource_id,
                "actions": sorted(set(actions)),
                "expires_at": expires_at.isoformat() if expires_at is not None else None,
            },
        ),
        after_version=grant.version,
        status_code=200,
        details={"resource_kind": "scope_projection"},
    )
    return grant


def request_workspace_state(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    desired_state: str,
    expected_version: int,
    request_id: str,
) -> Workspace:
    if desired_state not in {"stopped", "running", "paused", "archived"}:
        raise WorkspaceConflict("unsupported workspace desired state")
    workspace = get_workspace(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.lifecycle",
        lock=True,
    )
    if workspace.desired_state == desired_state:
        return workspace
    if workspace.version != expected_version:
        raise WorkspaceConflict("workspace version changed")
    if workspace.observed_state == "archived":
        raise WorkspaceConflict("archived workspaces cannot be restarted")
    previous_version = workspace.version
    workspace.desired_state = desired_state
    workspace.version += 1
    if desired_state in {"paused", "stopped", "archived"}:
        run_target = "paused" if desired_state == "paused" else "cancelled"
        session.execute(
            update(WorkspaceRun)
            .where(
                WorkspaceRun.tenant_id == tenant_id,
                WorkspaceRun.workspace_id == workspace_id,
                ~WorkspaceRun.observed_state.in_(_TERMINAL_RUN_STATES),
            )
            .values(desired_state=run_target, version=WorkspaceRun.version + 1)
        )
        now = _db_now(session)
        session.execute(
            update(RunLease)
            .where(
                RunLease.tenant_id == tenant_id,
                RunLease.state == "active",
                RunLease.run_id.in_(
                    select(WorkspaceRun.id).where(
                        WorkspaceRun.tenant_id == tenant_id,
                        WorkspaceRun.workspace_id == workspace_id,
                    )
                ),
            )
            .values(state="revoked", revoked_at=now)
        )
        session.execute(
            update(CapabilityGrant)
            .where(
                CapabilityGrant.tenant_id == tenant_id,
                CapabilityGrant.workspace_id == workspace_id,
                CapabilityGrant.state == "active",
            )
            .values(
                state="revoked",
                revoked_at=now,
                version=CapabilityGrant.version + 1,
            )
        )
        if desired_state == "archived":
            workspace.generation += 1
            session.execute(
                update(WorkspaceNode)
                .where(
                    WorkspaceNode.tenant_id == tenant_id,
                    WorkspaceNode.workspace_id == workspace_id,
                    WorkspaceNode.state.in_(("pending", "active", "suspended")),
                )
                .values(
                    state="revoked", revoked_at=now, fencing_token=WorkspaceNode.fencing_token + 1
                )
            )
            session.execute(
                update(PeerGrant)
                .where(
                    PeerGrant.tenant_id == tenant_id,
                    PeerGrant.workspace_id == workspace_id,
                    PeerGrant.state == "active",
                )
                .values(state="revoked", revoked_at=now, fencing_token=PeerGrant.fencing_token + 1)
            )
            session.execute(
                update(ServiceAdvertisement)
                .where(
                    ServiceAdvertisement.tenant_id == tenant_id,
                    ServiceAdvertisement.workspace_id == workspace_id,
                    ServiceAdvertisement.state == "active",
                )
                .values(state="revoked", revoked_at=now)
            )
            session.execute(
                update(NetworkLease)
                .where(
                    NetworkLease.tenant_id == tenant_id,
                    NetworkLease.workspace_id == workspace_id,
                    NetworkLease.state == "active",
                )
                .values(state="revoked", revoked_at=now)
            )
            session.execute(
                update(WorkspaceAuthority)
                .where(
                    WorkspaceAuthority.tenant_id == tenant_id,
                    WorkspaceAuthority.workspace_id == workspace_id,
                    WorkspaceAuthority.state == "active",
                )
                .values(state="revoked")
            )
    session.flush()
    append_audit_event(
        session,
        tenant_id=tenant_id,
        request_id=request_id,
        actor_type="user",
        actor_id=actor_user_id,
        workspace_id=workspace_id,
        resource_id=workspace_id,
        action=f"workspace.{desired_state}",
        decision="allowed",
        risk_level="R1" if desired_state != "archived" else "R2",
        before_version=previous_version,
        after_version=workspace.version,
        status_code=200,
        details={"from_state": workspace.observed_state, "to_state": desired_state},
    )
    return workspace


def reconcile_workspace(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    expected_generation: int,
    expected_version: int,
    reconciler: WorkspaceReconciler,
) -> Workspace:
    workspace = session.execute(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFound("workspace not found")
    if workspace.generation != expected_generation or workspace.version != expected_version:
        raise WorkspaceConflict("stale workspace reconciler")
    result = reconciler.reconcile(
        workspace_id=workspace_id,
        generation=workspace.generation,
        desired_state=workspace.desired_state,
        observed_state=workspace.observed_state,
    )
    workspace.observed_state = result.observed_state
    workspace.version += 1
    workspace.last_error_code = None
    if result.observed_state == "archived":
        workspace.archived_at = _db_now(session)
    resource = get_resource(session, tenant_id=tenant_id, resource_id=workspace_id)
    resource.state = result.observed_state
    resource.version += 1
    session.flush()
    return workspace


def create_run(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    kind: str,
    expected_workspace_generation: int,
    request_digest: str,
    idempotency_key: str,
) -> WorkspaceRun:
    _validate_digest(request_digest, "request_digest")
    if kind not in {"batch", "interactive"}:
        raise WorkspaceConflict("unsupported run kind")
    workspace = get_workspace(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.run",
        lock=True,
    )
    if workspace.generation != expected_workspace_generation:
        raise WorkspaceConflict("workspace generation changed")
    if workspace.desired_state == "archived" or workspace.observed_state == "archived":
        raise WorkspaceConflict("archived workspaces cannot create runs")
    active_count = session.execute(
        select(func.count())
        .select_from(WorkspaceRun)
        .where(
            WorkspaceRun.tenant_id == tenant_id,
            WorkspaceRun.workspace_id == workspace_id,
            WorkspaceRun.observed_state.in_(_ACTIVE_RUN_STATES),
        )
    ).scalar_one()
    max_active_value = workspace.quota.get("max_active_runs", 1)
    if not isinstance(max_active_value, int) or isinstance(max_active_value, bool):
        raise WorkspaceConflict("workspace active run quota is invalid")
    max_active = max_active_value
    if int(active_count) >= max_active:
        raise WorkspaceConflict("workspace active run quota reached")
    idem_hash = _request_hash(
        "workspace.run.create",
        {
            "workspace_id": workspace_id,
            "generation": expected_workspace_generation,
            "kind": kind,
            "request_digest": request_digest,
        },
    )
    try:
        idem, inserted = reserve_idempotency(
            session,
            tenant_id=tenant_id,
            actor_scope=f"workspace:{workspace_id}:user:{actor_user_id}",
            operation_name="workspace.run.create",
            key=idempotency_key,
            request_hash=idem_hash,
            expires_at=_db_now(session) + timedelta(hours=24),
        )
    except IdempotencyConflict as exc:
        raise WorkspaceConflict(str(exc)) from exc
    if not inserted:
        run_id = _replay_id(session, record=idem, response_key="run_id")
        if run_id is None:
            raise WorkspaceConflict("run creation is already in progress")
        run = session.execute(
            select(WorkspaceRun).where(
                WorkspaceRun.id == run_id,
                WorkspaceRun.tenant_id == tenant_id,
                WorkspaceRun.workspace_id == workspace_id,
            )
        ).scalar_one_or_none()
        if run is None:
            raise WorkspaceConflict("idempotent run result is unavailable")
        return run
    resource = register_resource(
        session,
        tenant_id=tenant_id,
        kind="interactive_session" if kind == "interactive" else "run",
        owner_type="workspace",
        owner_id=workspace_id,
        parent_id=workspace_id,
        display_name=f"{kind} run",
        state="provisioning",
        policy_class="workspace_private",
        created_by_actor_id=actor_user_id,
    )
    run = WorkspaceRun(
        id=resource.id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        kind=kind,
        generation=workspace.generation,
        desired_state="running",
        observed_state="queued",
        request_digest=request_digest,
        created_by_user_id=actor_user_id,
    )
    # The run-bound scope FK is immediate.  Flush the WorkspaceRun before its
    # dependent scope row; both remain in the same caller-owned transaction.
    session.add(run)
    session.flush()
    session.add(
        ResourceScopeBinding(
            resource_id=resource.id,
            tenant_id=tenant_id,
            scope_class="run_ephemeral",
            workspace_id=workspace_id,
            run_id=resource.id,
        )
    )
    session.flush()
    complete_idempotency(
        session,
        tenant_id=tenant_id,
        record_id=idem.id,
        expected_version=idem.version,
        response_ref={"run_id": resource.id},
    )
    return run


def list_runs(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
) -> list[WorkspaceRun]:
    authorize_workspace_action(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.read",
    )
    return list(
        session.scalars(
            select(WorkspaceRun)
            .where(
                WorkspaceRun.tenant_id == tenant_id,
                WorkspaceRun.workspace_id == workspace_id,
            )
            .order_by(WorkspaceRun.created_at.desc(), WorkspaceRun.id.desc())
        )
    )


def claim_run_lease(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    node_id: str,
    lease_seconds: int = 30,
) -> RunLease:
    if lease_seconds < 5 or lease_seconds > 300:
        raise LeaseRejected("run lease duration is outside the safe range")
    located_run = session.execute(
        select(WorkspaceRun).where(
            WorkspaceRun.id == run_id,
            WorkspaceRun.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if located_run is None:
        raise WorkspaceNotFound("run not found")
    workspace = session.execute(
        select(Workspace)
        .where(
            Workspace.id == located_run.workspace_id,
            Workspace.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceNotFound("workspace not found")
    try:
        node = get_active_attested_node(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace.id,
            node_id=node_id,
            lock=True,
        )
    except WorkspaceNotFound as exc:
        raise LeaseRejected("node is not active and attested") from exc
    run = session.execute(
        select(WorkspaceRun)
        .where(
            WorkspaceRun.id == run_id,
            WorkspaceRun.tenant_id == tenant_id,
            WorkspaceRun.workspace_id == workspace.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise WorkspaceNotFound("run not found")
    if (
        run.generation != workspace.generation
        or run.desired_state != "running"
        or run.observed_state != "queued"
    ):
        raise LeaseRejected("run generation or desired state is stale")
    now = _db_now(session)
    existing = session.execute(
        select(RunLease)
        .where(
            RunLease.tenant_id == tenant_id,
            RunLease.run_id == run_id,
            RunLease.state == "active",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        raise LeaseRejected("run has already been leased; create a new run")
    token = run.next_fencing_token
    run.next_fencing_token += 1
    run.observed_state = "leased"
    run.version += 1
    lease = RunLease(
        tenant_id=tenant_id,
        run_id=run_id,
        workspace_id=run.workspace_id,
        node_id=node_id,
        node_fencing_token=node.fencing_token,
        generation=run.generation,
        fencing_token=token,
        state="active",
        heartbeat_at=now,
        expires_at=now + timedelta(seconds=lease_seconds),
    )
    session.add(lease)
    session.flush()
    return lease


def _validated_run_lease(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    lease_id: str,
    node_id: str,
    generation: int,
    fencing_token: int,
    lock: bool = True,
) -> tuple[WorkspaceRun, RunLease, datetime]:
    located_run = session.execute(
        select(WorkspaceRun).where(
            WorkspaceRun.id == run_id,
            WorkspaceRun.tenant_id == tenant_id,
        )
    ).scalar_one_or_none()
    if located_run is None:
        raise LeaseRejected("run lease not found")
    workspace_statement = select(Workspace).where(
        Workspace.id == located_run.workspace_id,
        Workspace.tenant_id == tenant_id,
    )
    if lock:
        workspace_statement = workspace_statement.with_for_update()
    workspace = session.execute(workspace_statement).scalar_one_or_none()
    if workspace is None:
        raise LeaseRejected("run lease not found")
    try:
        node = get_active_attested_node(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace.id,
            node_id=node_id,
            lock=lock,
        )
    except WorkspaceNotFound as exc:
        raise LeaseRejected("run lease holder is unavailable") from exc
    run_statement = select(WorkspaceRun).where(
        WorkspaceRun.id == run_id,
        WorkspaceRun.tenant_id == tenant_id,
        WorkspaceRun.workspace_id == workspace.id,
    )
    lease_statement = select(RunLease).where(
        RunLease.id == lease_id,
        RunLease.tenant_id == tenant_id,
        RunLease.run_id == run_id,
    )
    if lock:
        run_statement = run_statement.with_for_update()
        lease_statement = lease_statement.with_for_update()
    run = session.execute(run_statement).scalar_one_or_none()
    lease = session.execute(lease_statement).scalar_one_or_none()
    now = _db_now(session)
    if run is None or lease is None:
        raise LeaseRejected("run lease not found")
    if (
        lease.state != "active"
        or lease.node_id != node_id
        or lease.node_fencing_token != node.fencing_token
        or lease.generation != generation
        or run.generation != generation
        or workspace.generation != generation
        or lease.fencing_token != fencing_token
        or run.next_fencing_token - 1 != fencing_token
        or lease.expires_at <= now
    ):
        raise LeaseRejected("run lease is expired, stale, revoked, or incorrectly fenced")
    return run, lease, now


def heartbeat_run_lease(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    lease_id: str,
    node_id: str,
    generation: int,
    fencing_token: int,
    lease_seconds: int = 30,
) -> RunLease:
    if lease_seconds < 5 or lease_seconds > 300:
        raise LeaseRejected("run lease duration is outside the safe range")
    run, lease, now = _validated_run_lease(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        lease_id=lease_id,
        node_id=node_id,
        generation=generation,
        fencing_token=fencing_token,
    )
    del run
    lease.heartbeat_at = now
    lease.expires_at = now + timedelta(seconds=lease_seconds)
    session.flush()
    return lease


def bind_run_runtime_identity(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    lease_id: str,
    node_id: str,
    generation: int,
    fencing_token: int,
    runtime_instance_id: str,
    workload_identity_digest: str,
) -> WorkspaceRun:
    """Bind one fresh runtime identity while holding the live fenced Run lease."""
    try:
        canonical_runtime_instance_id = str(UUID(runtime_instance_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise LeaseRejected("runtime instance identity is invalid") from exc
    _validate_digest(workload_identity_digest, "workload_identity_digest")
    run, _, _ = _validated_run_lease(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        lease_id=lease_id,
        node_id=node_id,
        generation=generation,
        fencing_token=fencing_token,
    )
    existing = (run.runtime_instance_id, run.workload_identity_digest)
    supplied = (canonical_runtime_instance_id, workload_identity_digest)
    if existing == supplied:
        return run
    if existing != (None, None):
        raise LeaseRejected("run runtime identity is already bound")
    if run.observed_state != "leased":
        raise LeaseRejected("run runtime identity requires leased state")
    run.runtime_instance_id = canonical_runtime_instance_id
    run.workload_identity_digest = workload_identity_digest
    run.version += 1
    session.flush()
    return run


def verify_run_lease_for_sandbox(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    runtime_instance_id: str,
    lease_id: str,
    node_id: str,
    generation: int,
    fencing_token: int,
    workload_identity_digest: str,
) -> VerifiedRunLeaseFacts:
    """Return current DB-backed Run/Node/fencing facts for a Sandbox verifier."""
    try:
        canonical_runtime_instance_id = str(UUID(runtime_instance_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise LeaseRejected("runtime instance identity is invalid") from exc
    _validate_digest(workload_identity_digest, "workload_identity_digest")
    run, lease, now = _validated_run_lease(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        lease_id=lease_id,
        node_id=node_id,
        generation=generation,
        fencing_token=fencing_token,
    )
    if (
        run.runtime_instance_id != canonical_runtime_instance_id
        or run.workload_identity_digest != workload_identity_digest
    ):
        raise LeaseRejected("run runtime identity is stale or unbound")
    verification_digest = canonical_digest(
        {
            "lease_id": lease.id,
            "node_fencing_token": lease.node_fencing_token,
            "node_id": lease.node_id,
            "run_fencing_token": lease.fencing_token,
            "run_id": run.id,
            "runtime_instance_id": run.runtime_instance_id,
            "tenant_id": run.tenant_id,
            "verified_at": now.isoformat(),
            "workload_identity_digest": run.workload_identity_digest,
            "workspace_generation": run.generation,
            "workspace_id": run.workspace_id,
        }
    )
    return VerifiedRunLeaseFacts(
        tenant_id=run.tenant_id,
        workspace_id=run.workspace_id,
        run_id=run.id,
        runtime_instance_id=canonical_runtime_instance_id,
        node_id=lease.node_id,
        lease_id=lease.id,
        workspace_generation=run.generation,
        run_fencing_token=lease.fencing_token,
        node_fencing_token=lease.node_fencing_token,
        workload_identity_digest=workload_identity_digest,
        verified_at=now,
        expires_at=lease.expires_at,
        verification_digest=verification_digest,
    )


def submit_run_state(
    session: Session,
    *,
    tenant_id: str,
    run_id: str,
    lease_id: str,
    node_id: str,
    generation: int,
    fencing_token: int,
    observed_state: str,
    result_digest: str | None = None,
    error_code: str | None = None,
) -> WorkspaceRun:
    allowed = {"starting", "running", "paused", "stopped", "succeeded", "failed", "cancelled"}
    if observed_state not in allowed:
        raise WorkspaceConflict("unsupported run observed state")
    if result_digest is not None:
        _validate_digest(result_digest, "result_digest")
    run, lease, _ = _validated_run_lease(
        session,
        tenant_id=tenant_id,
        run_id=run_id,
        lease_id=lease_id,
        node_id=node_id,
        generation=generation,
        fencing_token=fencing_token,
    )
    transitions = _RUN_STATE_TRANSITIONS.get(run.observed_state, frozenset())
    if observed_state != run.observed_state and observed_state not in transitions:
        raise WorkspaceConflict("invalid run observed-state transition")
    if run.desired_state != "running" and observed_state in {"starting", "running"}:
        raise LeaseRejected("run is no longer scheduled to run")
    run.observed_state = observed_state
    run.last_result_digest = result_digest
    run.last_error_code = error_code
    run.version += 1
    if observed_state in _TERMINAL_RUN_STATES:
        lease.state = "completed" if observed_state in {"stopped", "succeeded"} else "revoked"
        run.desired_state = "cancelled" if observed_state == "cancelled" else "stopped"
        run.runtime_instance_id = None
        run.workload_identity_digest = None
    session.flush()
    return run


def close_historical_run_holder(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    workspace_run_id: str,
    run_lease_id: str,
    node_id: str,
    generation: int,
    run_fencing_token: int,
    node_fencing_token: int,
    observed_state: str,
    result_digest: str | None = None,
    error_code: str | None = None,
) -> WorkspaceRun:
    """Fail-closed terminal close of a HISTORICAL run holder.

    Server-owned recovery for the real long-disconnect path where BOTH the
    Task Lease and the Workspace Run Lease have lapsed before the terminal
    transition arrives.  ``submit_run_state`` deliberately refuses expired /
    revoked / stale leases through ``_validated_run_lease`` — that check is
    never relaxed.  This path is the ONLY alternative, and it is restricted
    to terminal FAILURE states:

    * ``observed_state`` must be ``failed`` or ``cancelled`` — a historical
      holder can never be closed as ``succeeded``/``stopped`` (no committed
      success interpretation of an expired authorization);
    * the holder identity must match EXACTLY: workspace run, run lease,
      node binding, workspace generation, run fencing and node fencing are
      all validated against the server-owned rows under lock; any mismatch
      (stale/replaced lease, generation drift, wrong node, wrong workspace)
      fails closed and touches nothing;
    * the RunLease is never renewed, never revived and never returned to
      ``active`` — an active-but-lapsed lease is revoked, an already
      revoked/expired lease stays as it is, and the heartbeat window is
      never extended;
    * the WorkspaceRun is terminalized (failed/cancelled), its
      runtime/workload bindings are cleared, and the partial unique index
      ``workspace_runs_one_active_uq`` therefore frees the interactive slot;
    * all writes happen in the caller's transaction: any later failure
      rolls the whole terminal transition back.
    """
    if observed_state not in {"failed", "cancelled"}:
        raise WorkspaceConflict(
            "historical run holder close only accepts failed or cancelled states"
        )
    if result_digest is not None:
        _validate_digest(result_digest, "result_digest")
    workspace = session.execute(
        select(Workspace)
        .where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise LeaseRejected("run lease not found")
    run = session.execute(
        select(WorkspaceRun)
        .where(
            WorkspaceRun.id == workspace_run_id,
            WorkspaceRun.tenant_id == tenant_id,
            WorkspaceRun.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if run is None:
        raise LeaseRejected("run lease not found")
    if run.generation != generation or workspace.generation != generation:
        raise LeaseRejected("run lease is expired, stale, revoked, or incorrectly fenced")
    lease = session.execute(
        select(RunLease)
        .where(
            RunLease.id == run_lease_id,
            RunLease.tenant_id == tenant_id,
            RunLease.run_id == workspace_run_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if lease is None:
        raise LeaseRejected("run lease not found")
    # Exact historical holder: node binding, generation, run fencing and
    # node fencing must all match the server-owned rows.  A replaced lease,
    # a new node or an advanced fencing token never authorizes this close.
    if (
        lease.node_id != node_id
        or lease.generation != generation
        or lease.node_fencing_token != node_fencing_token
        or lease.fencing_token != run_fencing_token
        or run.next_fencing_token - 1 != run_fencing_token
    ):
        raise LeaseRejected("run lease is expired, stale, revoked, or incorrectly fenced")
    node = session.execute(
        select(WorkspaceNode).where(
            WorkspaceNode.id == node_id,
            WorkspaceNode.tenant_id == tenant_id,
            WorkspaceNode.workspace_id == workspace_id,
        )
    ).scalar_one_or_none()
    if node is None:
        raise LeaseRejected("run lease holder is unavailable")
    # The lease is closed, never renewed or revived: an active-but-lapsed
    # lease is revoked; an already-terminal lease is left untouched.
    if lease.state == "active":
        lease.state = "revoked"
    run.observed_state = observed_state
    run.desired_state = "cancelled" if observed_state == "cancelled" else "stopped"
    run.runtime_instance_id = None
    run.workload_identity_digest = None
    run.last_result_digest = result_digest
    run.last_error_code = error_code
    run.version += 1
    session.flush()
    return run


def create_snapshot(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    expected_workspace_generation: int,
    manifest_digest: str,
    metadata: Mapping[str, object],
) -> WorkspaceSnapshot:
    _validate_digest(manifest_digest, "manifest_digest")
    _validate_safe_metadata(metadata, path="snapshot")
    workspace = get_workspace(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action="workspace.snapshot",
        lock=True,
    )
    if workspace.generation != expected_workspace_generation:
        raise WorkspaceConflict("workspace generation changed")
    if workspace.observed_state not in {"stopped", "paused"}:
        raise WorkspaceConflict("snapshot metadata requires a stopped or paused workspace")
    resource = register_resource(
        session,
        tenant_id=tenant_id,
        kind="snapshot",
        owner_type="workspace",
        owner_id=workspace_id,
        parent_id=workspace_id,
        display_name=f"snapshot generation {workspace.generation}",
        state="active",
        policy_class="workspace_private",
        created_by_actor_id=actor_user_id,
    )
    snapshot = WorkspaceSnapshot(
        id=resource.id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_generation=workspace.generation,
        manifest_digest=manifest_digest,
        snapshot_metadata=dict(metadata),
        state="ready",
        created_by_user_id=actor_user_id,
    )
    session.add_all(
        [
            snapshot,
            ResourceScopeBinding(
                resource_id=resource.id,
                tenant_id=tenant_id,
                scope_class="workspace_private",
                workspace_id=workspace_id,
            ),
        ]
    )
    session.flush()
    append_resource_lineage(
        session,
        tenant_id=tenant_id,
        source_resource_id=workspace_id,
        derived_resource_id=resource.id,
        relation="snapshot_of",
        source_version=get_resource(session, tenant_id=tenant_id, resource_id=workspace_id).version,
        transform_digest=manifest_digest,
    )
    return snapshot


def restore_snapshot_new_workspace(
    session: Session,
    *,
    tenant_id: str,
    source_workspace_id: str,
    snapshot_id: str,
    actor_user_id: str,
    display_name: str,
) -> Workspace:
    source = get_workspace(
        session,
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        user_id=actor_user_id,
        action="workspace.restore",
        lock=True,
    )
    snapshot = session.execute(
        select(WorkspaceSnapshot).where(
            WorkspaceSnapshot.id == snapshot_id,
            WorkspaceSnapshot.tenant_id == tenant_id,
            WorkspaceSnapshot.workspace_id == source_workspace_id,
            WorkspaceSnapshot.state == "ready",
        )
    ).scalar_one_or_none()
    if snapshot is None:
        raise WorkspaceNotFound("workspace snapshot not found")
    resource = register_resource(
        session,
        tenant_id=tenant_id,
        kind="workspace",
        owner_type="user",
        owner_id=actor_user_id,
        parent_id=source.parent_workspace_id,
        display_name=display_name,
        state="stopped",
        policy_class="workspace_private",
        created_by_actor_id=actor_user_id,
    )
    restored = Workspace(
        id=resource.id,
        tenant_id=tenant_id,
        template_id=source.template_id,
        owner_user_id=actor_user_id,
        parent_workspace_id=source.parent_workspace_id,
        restored_from_snapshot_id=snapshot.id,
        display_name=display_name,
        desired_state="stopped",
        observed_state="stopped",
        generation=source.generation + 1,
        version=1,
        quota=dict(source.quota),
    )
    session.add_all(
        [
            restored,
            ResourceScopeBinding(
                resource_id=resource.id,
                tenant_id=tenant_id,
                scope_class="workspace_private",
                workspace_id=resource.id,
            ),
            WorkspaceMembership(
                tenant_id=tenant_id,
                workspace_id=resource.id,
                user_id=actor_user_id,
                role="owner",
                state="active",
                created_by_user_id=actor_user_id,
            ),
        ]
    )
    session.flush()
    append_resource_lineage(
        session,
        tenant_id=tenant_id,
        source_resource_id=snapshot.id,
        derived_resource_id=resource.id,
        relation="restored_from",
        source_version=get_resource(session, tenant_id=tenant_id, resource_id=snapshot.id).version,
        transform_digest=snapshot.manifest_digest,
    )
    return restored


__all__ = [
    "LeaseRejected",
    "TemplateRejected",
    "WorkspaceConflict",
    "WorkspaceError",
    "WorkspaceNotFound",
    "WorkspacePolicyDenied",
    "authorize_workspace_action",
    "bind_run_runtime_identity",
    "canonical_digest",
    "claim_run_lease",
    "create_run",
    "create_scope_grant",
    "create_snapshot",
    "create_workspace",
    "get_workspace",
    "heartbeat_run_lease",
    "list_memberships",
    "list_runs",
    "list_templates",
    "list_workspaces",
    "reconcile_workspace",
    "register_template",
    "request_workspace_state",
    "restore_snapshot_new_workspace",
    "set_membership_state",
    "submit_run_state",
    "upsert_membership",
    "validate_template_spec",
    "verify_run_lease_for_sandbox",
]
