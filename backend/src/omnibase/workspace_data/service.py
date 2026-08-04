"""Shared transaction and state-machine helpers for P34.6 Workspace data."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from omnibase.control_plane.models import (
    IdempotencyRecord,
    OperationRecord,
    ResourceRecord,
)
from omnibase.control_plane.service import (
    DomainConflict,
    append_audit_event,
    complete_idempotency,
    fail_idempotency,
)
from omnibase.workspace_data.models import WorkspaceDataEffect
from omnibase.workspaces.models import ResourceScopeBinding, Workspace, WorkspaceMembership
from omnibase.workspaces.service import get_workspace

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_REASON = re.compile(r"^[a-z][a-z0-9_.:-]{1,99}$")


class WorkspaceDataError(RuntimeError):
    """Stable base error for P34.6 data lifecycle failures."""


class WorkspaceDataNotFound(WorkspaceDataError):
    """IDOR-safe missing/out-of-scope result."""


class WorkspaceDataConflict(WorkspaceDataError):
    """Version, state, idempotency or immutable-binding conflict."""


class WorkspaceDataDenied(WorkspaceDataNotFound):
    """Authorization denial intentionally shares not-found semantics."""


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def operation_request_hash(kind: str, payload: Mapping[str, object]) -> str:
    return canonical_digest({"kind": kind, "payload": dict(payload)})


def require_digest(value: str, field: str) -> None:
    if _DIGEST.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


def lock_workspace_scope(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    action: str,
) -> tuple[Workspace, WorkspaceMembership]:
    workspace = get_workspace(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        user_id=actor_user_id,
        action=action,
        lock=True,
    )
    membership = session.execute(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.tenant_id == tenant_id,
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == actor_user_id,
            WorkspaceMembership.state == "active",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if membership is None:
        raise WorkspaceDataDenied("workspace data not found")
    return workspace, membership


def lock_resource(
    session: Session,
    *,
    tenant_id: str,
    resource_id: str,
) -> ResourceRecord:
    resource = session.execute(
        select(ResourceRecord)
        .where(ResourceRecord.id == resource_id, ResourceRecord.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if resource is None:
        raise WorkspaceDataNotFound("workspace data not found")
    return resource


def require_workspace_resource(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    resource_id: str,
    expected_version: int | None = None,
    allowed_policies: frozenset[str] = frozenset({"workspace_private", "workspace_derived"}),
) -> ResourceRecord:
    resource = lock_resource(session, tenant_id=tenant_id, resource_id=resource_id)
    binding = session.execute(
        select(ResourceScopeBinding)
        .where(
            ResourceScopeBinding.resource_id == resource_id,
            ResourceScopeBinding.tenant_id == tenant_id,
            ResourceScopeBinding.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if (
        binding is None
        or resource.owner_type != "workspace"
        or resource.owner_id != workspace_id
        or resource.policy_class not in allowed_policies
        or binding.scope_class not in {"workspace_private", "workspace_shared"}
    ):
        raise WorkspaceDataNotFound("workspace data not found")
    if expected_version is not None and resource.version != expected_version:
        raise WorkspaceDataConflict("workspace resource version changed")
    return resource


def lock_operation(
    session: Session,
    *,
    tenant_id: str,
    operation_id: str,
) -> OperationRecord:
    operation = session.execute(
        select(OperationRecord)
        .where(OperationRecord.id == operation_id, OperationRecord.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if operation is None:
        raise WorkspaceDataConflict("workspace data operation not found")
    return operation


def create_effect(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    operation_id: str,
    effect_kind: str,
    binding: Mapping[str, object],
    resource_id: str | None = None,
    sequence: int = 1,
) -> WorkspaceDataEffect:
    digest = canonical_digest(binding)
    existing = session.execute(
        select(WorkspaceDataEffect)
        .where(
            WorkspaceDataEffect.tenant_id == tenant_id,
            WorkspaceDataEffect.operation_id == operation_id,
            WorkspaceDataEffect.sequence == sequence,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if existing is not None:
        if existing.binding_digest != digest or existing.effect_kind != effect_kind:
            raise WorkspaceDataConflict("workspace data effect binding drift")
        if existing.state == "unknown":
            raise WorkspaceDataConflict("unknown workspace data effect requires reconciliation")
        return existing
    effect = WorkspaceDataEffect(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=resource_id,
        operation_id=operation_id,
        sequence=sequence,
        effect_kind=effect_kind,
        binding_digest=digest,
        state="pending",
    )
    session.add(effect)
    session.flush()
    return effect


def transition_effect(
    effect: WorkspaceDataEffect,
    *,
    target_state: str,
    receipt_digest: str | None = None,
    reason_code: str | None = None,
) -> None:
    if effect.state == target_state:
        if receipt_digest is not None and effect.receipt_digest != receipt_digest:
            raise WorkspaceDataConflict("workspace data effect receipt drift")
        return
    if effect.state != "pending":
        raise WorkspaceDataConflict("terminal workspace data effect cannot transition")
    if target_state not in {"committed", "failed", "unknown"}:
        raise ValueError("unsupported workspace data effect state")
    if target_state == "committed":
        if receipt_digest is None:
            raise ValueError("committed effect requires receipt_digest")
        require_digest(receipt_digest, "receipt_digest")
    elif receipt_digest is not None:
        require_digest(receipt_digest, "receipt_digest")
    if reason_code is not None and _REASON.fullmatch(reason_code) is None:
        raise ValueError("reason_code has invalid format")
    effect.state = target_state
    effect.receipt_digest = receipt_digest
    effect.reason_code = reason_code
    effect.version += 1


def finish_operation(
    session: Session,
    *,
    operation: OperationRecord,
    succeeded: bool,
    result_ref: Mapping[str, object] | None = None,
    error_code: str | None = None,
) -> None:
    if operation.state in {"succeeded", "failed"}:
        if succeeded != (operation.state == "succeeded"):
            raise WorkspaceDataConflict("terminal operation outcome drift")
        return
    if operation.state not in {"queued", "running"}:
        raise WorkspaceDataConflict("workspace data operation is not executable")
    operation.state = "succeeded" if succeeded else "failed"
    operation.progress = 100 if succeeded else operation.progress
    operation.completed_at = datetime.now(UTC)
    operation.result_ref = dict(result_ref) if result_ref is not None else None
    operation.error_code = None if succeeded else error_code
    operation.error_detail = None
    operation.version += 1

    idem = session.execute(
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.tenant_id == operation.tenant_id,
            IdempotencyRecord.operation_id == operation.id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if idem is None:
        raise WorkspaceDataConflict("workspace data idempotency binding missing")
    if succeeded:
        complete_idempotency(
            session,
            tenant_id=operation.tenant_id,
            record_id=idem.id,
            expected_version=idem.version,
            operation_id=operation.id,
            response_ref=dict(result_ref or {}),
        )
    else:
        fail_idempotency(
            session,
            tenant_id=operation.tenant_id,
            record_id=idem.id,
            expected_version=idem.version,
            operation_id=operation.id,
            response_ref={"error_code": error_code or "workspace_data_failed"},
        )


def audit_data_event(
    session: Session,
    *,
    tenant_id: str,
    request_id: str,
    actor_user_id: str,
    workspace_id: str,
    resource_id: str | None,
    operation_id: str | None,
    action: str,
    decision: str,
    risk_level: str,
    input_hash: str,
    before_version: int | None = None,
    after_version: int | None = None,
    status_code: int | None = None,
    reason_code: str | None = None,
) -> None:
    details: dict[str, object] = {"operation_kind": action}
    if reason_code is not None:
        details["reason_code"] = reason_code
    try:
        append_audit_event(
            session,
            tenant_id=tenant_id,
            request_id=request_id,
            actor_type="user",
            actor_id=actor_user_id,
            workspace_id=workspace_id,
            resource_id=resource_id,
            operation_id=operation_id,
            action=action,
            decision=decision,
            risk_level=risk_level,
            input_hash=input_hash,
            before_version=before_version,
            after_version=after_version,
            status_code=status_code,
            details=details,
        )
    except DomainConflict as exc:
        raise WorkspaceDataConflict(str(exc)) from exc


__all__ = [
    "WorkspaceDataConflict",
    "WorkspaceDataDenied",
    "WorkspaceDataError",
    "WorkspaceDataNotFound",
    "audit_data_event",
    "canonical_digest",
    "create_effect",
    "finish_operation",
    "lock_operation",
    "lock_resource",
    "lock_workspace_scope",
    "operation_request_hash",
    "require_digest",
    "require_workspace_resource",
    "transition_effect",
]
