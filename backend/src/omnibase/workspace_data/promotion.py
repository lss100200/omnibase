"""Approval-bound copy-on-publish service for P34.6.

Promotion is deliberately a control-plane operation, not a workload Gateway
action.  It always creates a new ``controlled_shared`` logical resource and
never changes the source resource's policy, version, or physical locator.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnibase.control_plane.models import ApprovalRequest, OperationRecord, ResourceRecord
from omnibase.control_plane.service import register_resource
from omnibase.workspace_data.contracts import EffectOutcome, PublicationRequest
from omnibase.workspace_data.models import WorkspacePublication
from omnibase.workspace_data.service import (
    WorkspaceDataConflict,
    WorkspaceDataDenied,
    audit_data_event,
    create_effect,
    finish_operation,
    lock_operation,
    lock_workspace_scope,
    operation_request_hash,
    require_digest,
    require_workspace_resource,
    transition_effect,
)
from omnibase.workspaces.models import ResourceScopeBinding, Workspace
from omnibase.workspaces.service import WorkspacePolicyDenied, authorize_workspace_action

_PROMOTION_KIND = "workspace_data.promote"
_PROMOTION_ACTION = "workspace.data.publish"
_MAX_ADMIN_FACT_TTL = timedelta(seconds=30)


@dataclass(frozen=True, slots=True)
class TrustedTenantAdminFacts:
    """Live tenant-admin facts produced from already locked tenant user rows."""

    tenant_id: str
    user_id: str
    is_active: bool
    is_tenant_admin: bool
    verified_at: datetime
    expires_at: datetime

    def validate(self, *, tenant_id: str, user_id: str, now: datetime) -> None:
        if self.tenant_id != tenant_id or self.user_id != user_id:
            raise WorkspaceDataDenied("promotion approval is unavailable")
        if not self.is_active or not self.is_tenant_admin:
            raise WorkspaceDataDenied("promotion approval is unavailable")
        if self.verified_at.tzinfo is None or self.expires_at.tzinfo is None:
            raise ValueError("tenant-admin facts must use timezone-aware timestamps")
        if self.expires_at <= self.verified_at:
            raise ValueError("tenant-admin fact expiry must follow verification")
        if self.expires_at - self.verified_at > _MAX_ADMIN_FACT_TTL:
            raise ValueError("tenant-admin facts cannot outlive the live-lock window")
        if not self.verified_at <= now < self.expires_at:
            raise WorkspaceDataDenied("promotion approval is unavailable")


@dataclass(frozen=True, slots=True)
class PromotionResult:
    publication: WorkspacePublication
    target_resource: ResourceRecord
    outcome: EffectOutcome


def promotion_request_hash(request: PublicationRequest) -> str:
    """Return the immutable Operation/Approval binding for one publication."""

    return operation_request_hash(
        _PROMOTION_KIND,
        {
            "source_workspace_id": str(request.source_workspace_id),
            "source_resource_id": str(request.source_resource_id),
            "source_version": request.source_version,
            "source_manifest_digest": request.source_manifest_digest,
            "target_scope": request.target_scope.value,
            "target_workspace_id": (
                str(request.target_workspace_id)
                if request.target_workspace_id is not None
                else None
            ),
            "display_name": request.display_name,
            "idempotency_key": request.idempotency_key,
        },
    )


def execute_promotion(
    session: Session,
    *,
    tenant_id: str,
    requester_user_id: str,
    operation_id: str,
    approval_id: str,
    request: PublicationRequest,
    verified_source_digest: str,
    tenant_admin: TrustedTenantAdminFacts,
    outcome: EffectOutcome,
    receipt_digest: str | None = None,
    reason_code: str | None = None,
    target_policy_class: str = "controlled_shared",
) -> PromotionResult:
    """Execute one approval-bound copy-on-publish transition without committing.

    The caller owns the transaction and any provider copy boundary.  ``unknown``
    is a durable terminal result for this operation and is never replayed.
    """

    if target_policy_class != "controlled_shared":
        raise WorkspaceDataDenied("promotion target policy is unavailable")
    if outcome is EffectOutcome.COMMITTED:
        raise WorkspaceDataDenied(
            "provider-backed promotion is unavailable until the durable copy adapter Gate passes"
        )
    require_digest(verified_source_digest, "verified_source_digest")
    if verified_source_digest != request.source_manifest_digest:
        raise WorkspaceDataConflict("promotion source digest changed")

    now = datetime.now(UTC)
    workspace, _membership = lock_workspace_scope(
        session,
        tenant_id=tenant_id,
        workspace_id=str(request.source_workspace_id),
        actor_user_id=requester_user_id,
        action=_PROMOTION_ACTION,
    )
    source = require_workspace_resource(
        session,
        tenant_id=tenant_id,
        workspace_id=str(request.source_workspace_id),
        resource_id=str(request.source_resource_id),
        expected_version=request.source_version,
        allowed_policies=frozenset({"workspace_private", "workspace_derived"}),
    )
    operation = lock_operation(session, tenant_id=tenant_id, operation_id=operation_id)
    approval = _lock_approval(session, tenant_id=tenant_id, approval_id=approval_id)
    request_hash = promotion_request_hash(request)
    _validate_operation_and_approval(
        operation=operation,
        approval=approval,
        tenant_id=tenant_id,
        requester_user_id=requester_user_id,
        workspace_id=workspace.id,
        source=source,
        request_hash=request_hash,
        tenant_admin=tenant_admin,
        now=now,
    )

    existing = _lock_operation_publication(
        session,
        tenant_id=tenant_id,
        operation_id=operation_id,
    )
    if existing is not None:
        if existing.state == "unknown":
            raise WorkspaceDataConflict("unknown publication effect requires reconciliation")
        raise WorkspaceDataConflict("promotion operation was already materialized")
    duplicate = _lock_duplicate_publication(
        session,
        tenant_id=tenant_id,
        request=request,
    )
    if duplicate is not None:
        raise WorkspaceDataConflict("source version was already published to this target")

    target_workspace_id = (
        str(request.target_workspace_id) if request.target_workspace_id is not None else None
    )
    if target_workspace_id is not None:
        _lock_target_workspace(session, tenant_id=tenant_id, workspace_id=target_workspace_id)
        try:
            authorize_workspace_action(
                session,
                tenant_id=tenant_id,
                workspace_id=target_workspace_id,
                user_id=requester_user_id,
                action=_PROMOTION_ACTION,
                lock=True,
            )
        except WorkspacePolicyDenied as exc:
            raise WorkspaceDataDenied("promotion target is unavailable") from exc

    target = register_resource(
        session,
        tenant_id=tenant_id,
        kind=source.kind,
        owner_type="workspace" if target_workspace_id is not None else "system",
        owner_id=target_workspace_id,
        parent_id=target_workspace_id,
        display_name=request.display_name,
        state="provisioning",
        policy_class="controlled_shared",
        physical_locator=None,
        metadata={
            "source_resource_id": source.id,
            "source_version": source.version,
            "source_manifest_digest": verified_source_digest,
        },
        created_by_actor_id=requester_user_id,
    )
    binding = ResourceScopeBinding(
        resource_id=target.id,
        tenant_id=tenant_id,
        scope_class=request.target_scope.value,
        workspace_id=target_workspace_id,
    )
    publication = WorkspacePublication(
        tenant_id=tenant_id,
        source_workspace_id=workspace.id,
        target_workspace_id=target_workspace_id,
        source_resource_id=source.id,
        source_version=source.version,
        source_manifest_digest=verified_source_digest,
        target_scope=request.target_scope.value,
        target_resource_id=target.id,
        operation_id=operation.id,
        approval_id=approval.id,
        request_hash=request_hash,
        state="copying",
        created_by_actor_id=requester_user_id,
    )
    session.add_all([binding, publication])
    session.flush()

    effect = create_effect(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace.id,
        operation_id=operation.id,
        effect_kind="publication_copy",
        resource_id=target.id,
        binding={
            "approval_id": approval.id,
            "operation_id": operation.id,
            "request_hash": request_hash,
            "source_resource_id": source.id,
            "source_version": source.version,
            "source_manifest_digest": verified_source_digest,
            "target_resource_id": target.id,
            "target_scope": request.target_scope.value,
            "target_workspace_id": target_workspace_id,
        },
    )
    if effect.state != "pending":
        raise WorkspaceDataConflict("publication effect cannot be replayed")

    terminal_state = "unknown" if outcome is EffectOutcome.UNKNOWN else "failed"
    transition_effect(
        effect,
        target_state=terminal_state,
        receipt_digest=receipt_digest,
        reason_code=reason_code or f"publication.{terminal_state}",
    )
    target.state = "failed"
    publication.state = terminal_state
    finish_operation(
        session,
        operation=operation,
        succeeded=False,
        error_code=f"workspace_data_publication_{terminal_state}",
    )
    decision = "error"
    status_code = 503

    publication.version += 1
    audit_data_event(
        session,
        tenant_id=tenant_id,
        request_id=request.request_id,
        actor_user_id=requester_user_id,
        workspace_id=workspace.id,
        resource_id=target.id,
        operation_id=operation.id,
        action=_PROMOTION_KIND,
        decision=decision,
        risk_level=operation.risk_level,
        input_hash=request_hash,
        before_version=source.version,
        after_version=target.version,
        status_code=status_code,
        reason_code=reason_code,
    )
    return PromotionResult(publication=publication, target_resource=target, outcome=outcome)


def _lock_approval(session: Session, *, tenant_id: str, approval_id: str) -> ApprovalRequest:
    approval = session.execute(
        select(ApprovalRequest)
        .where(ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if approval is None:
        raise WorkspaceDataDenied("promotion approval is unavailable")
    return approval


def _lock_operation_publication(
    session: Session, *, tenant_id: str, operation_id: str
) -> WorkspacePublication | None:
    return session.execute(
        select(WorkspacePublication)
        .where(
            WorkspacePublication.tenant_id == tenant_id,
            WorkspacePublication.operation_id == operation_id,
        )
        .with_for_update()
    ).scalar_one_or_none()


def _lock_duplicate_publication(
    session: Session, *, tenant_id: str, request: PublicationRequest
) -> WorkspacePublication | None:
    target_workspace_id = (
        str(request.target_workspace_id) if request.target_workspace_id is not None else None
    )
    lock_payload = json.dumps(
        {
            "tenant_id": tenant_id,
            "source_resource_id": str(request.source_resource_id),
            "source_version": request.source_version,
            "source_manifest_digest": request.source_manifest_digest,
            "target_scope": request.target_scope.value,
            "target_workspace_id": target_workspace_id,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    advisory_key = int.from_bytes(hashlib.sha256(lock_payload).digest()[:8], "big", signed=True)
    session.execute(select(func.pg_advisory_xact_lock(advisory_key)))
    return session.execute(
        select(WorkspacePublication)
        .where(
            WorkspacePublication.tenant_id == tenant_id,
            WorkspacePublication.source_resource_id == str(request.source_resource_id),
            WorkspacePublication.source_version == request.source_version,
            WorkspacePublication.source_manifest_digest == request.source_manifest_digest,
            WorkspacePublication.target_scope == request.target_scope.value,
            WorkspacePublication.target_workspace_id == target_workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()


def _lock_target_workspace(session: Session, *, tenant_id: str, workspace_id: str) -> Workspace:
    workspace = session.execute(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None or workspace.observed_state in {"archived", "failed"}:
        raise WorkspaceDataDenied("promotion target is unavailable")
    return workspace


def _validate_operation_and_approval(
    *,
    operation: OperationRecord,
    approval: ApprovalRequest,
    tenant_id: str,
    requester_user_id: str,
    workspace_id: str,
    source: ResourceRecord,
    request_hash: str,
    tenant_admin: TrustedTenantAdminFacts,
    now: datetime,
) -> None:
    if operation.deadline_at is not None and (
        operation.deadline_at.tzinfo is None or operation.deadline_at <= now
    ):
        raise WorkspaceDataDenied("promotion operation is unavailable")
    if approval.expires_at.tzinfo is None or approval.expires_at <= now:
        raise WorkspaceDataDenied("promotion approval is unavailable")
    if not all(
        (
            operation.tenant_id == tenant_id,
            operation.workspace_id == workspace_id,
            operation.actor_type == "user",
            operation.actor_id == requester_user_id,
            operation.resource_id == source.id,
            operation.resource_version == source.version,
            operation.kind == _PROMOTION_KIND,
            operation.state == "queued",
            operation.risk_level in {"R2", "R3"},
            operation.request_hash == request_hash,
            operation.approval_id == approval.id,
        )
    ):
        raise WorkspaceDataConflict("promotion operation binding changed")
    if not all(
        (
            approval.tenant_id == tenant_id,
            approval.operation_id == operation.id,
            approval.workspace_id == workspace_id,
            approval.resource_id == source.id,
            approval.resource_version == source.version,
            approval.requester_type == "user",
            approval.requester_id == requester_user_id,
            approval.action == _PROMOTION_ACTION,
            approval.required_approver_role == "tenant_admin",
            approval.state == "consumed",
            approval.consumed_at is not None,
            approval.request_hash == request_hash,
            approval.decided_by_actor_type == "user",
            approval.decided_by_actor_id is not None,
            approval.decided_by_actor_id != requester_user_id,
        )
    ):
        raise WorkspaceDataDenied("promotion approval is unavailable")
    assert approval.decided_by_actor_id is not None
    tenant_admin.validate(tenant_id=tenant_id, user_id=approval.decided_by_actor_id, now=now)


__all__ = [
    "PromotionResult",
    "TrustedTenantAdminFacts",
    "execute_promotion",
    "promotion_request_hash",
]
