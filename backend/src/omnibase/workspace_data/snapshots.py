"""Server-generated Workspace data snapshot and restore-new-identity services."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from omnibase.control_plane.models import OperationRecord, ResourceRecord
from omnibase.control_plane.service import append_resource_lineage, register_resource
from omnibase.workspace_data.contracts import EffectOutcome, SnapshotItemKind
from omnibase.workspace_data.models import (
    WorkspaceArtifact,
    WorkspaceDataEffect,
    WorkspaceSnapshotItem,
)
from omnibase.workspace_data.service import (
    WorkspaceDataConflict,
    WorkspaceDataDenied,
    audit_data_event,
    canonical_digest,
    create_effect,
    finish_operation,
    lock_operation,
    lock_resource,
    lock_workspace_scope,
    operation_request_hash,
    require_digest,
    transition_effect,
)
from omnibase.workspaces.models import (
    ResourceScopeBinding,
    Workspace,
    WorkspaceMembership,
    WorkspaceSnapshot,
)

_CAPTURE_KIND = "workspace_data.snapshot.capture"
_RESTORE_KIND = "workspace_data.snapshot.restore"
_MANIFEST_SCHEMA_VERSION = 1
_SUPPORTED_ITEM_KINDS = {
    "data_table": SnapshotItemKind.PRIVATE_TABLE,
    "artifact": SnapshotItemKind.ARTIFACT,
    "derived_index": SnapshotItemKind.DERIVED_INDEX,
}


@dataclass(frozen=True, slots=True)
class VerifiedSnapshotPayload:
    """Server-adapter proof for one content-addressed snapshot payload."""

    source_resource_id: str
    source_version: int
    content_digest: str
    payload_artifact_id: str
    size_bytes: int


class SnapshotPayloadVerifier(Protocol):
    """Trusted adapter that captures and re-verifies snapshot payloads."""

    def capture(
        self,
        *,
        resource: ResourceRecord,
        workspace_id: str,
        workspace_generation: int,
    ) -> VerifiedSnapshotPayload: ...

    def verify(self, *, item: WorkspaceSnapshotItem) -> VerifiedSnapshotPayload: ...


@dataclass(frozen=True, slots=True)
class SnapshotCaptureResult:
    snapshot: WorkspaceSnapshot
    items: tuple[WorkspaceSnapshotItem, ...]
    outcome: EffectOutcome


@dataclass(frozen=True, slots=True)
class SnapshotRestoreResult:
    workspace: Workspace
    resource_ids: tuple[str, ...]
    outcome: EffectOutcome


def snapshot_capture_request_hash(
    *,
    workspace_id: str,
    expected_workspace_generation: int,
    display_name: str,
    idempotency_key: str,
) -> str:
    return operation_request_hash(
        _CAPTURE_KIND,
        {
            "workspace_id": workspace_id,
            "expected_workspace_generation": expected_workspace_generation,
            "display_name": display_name,
            "idempotency_key": idempotency_key,
        },
    )


def snapshot_restore_request_hash(
    *,
    source_workspace_id: str,
    snapshot_id: str,
    display_name: str,
    idempotency_key: str,
) -> str:
    return operation_request_hash(
        _RESTORE_KIND,
        {
            "source_workspace_id": source_workspace_id,
            "snapshot_id": snapshot_id,
            "display_name": display_name,
            "idempotency_key": idempotency_key,
        },
    )


def capture_workspace_snapshot(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    operation_id: str,
    expected_workspace_generation: int,
    display_name: str,
    idempotency_key: str,
    request_id: str,
    verifier: SnapshotPayloadVerifier,
    outcome: EffectOutcome,
    receipt_digest: str | None = None,
    reason_code: str | None = None,
) -> SnapshotCaptureResult:
    """Capture a server-enumerated inventory and seal it only after verification."""

    workspace, _membership = lock_workspace_scope(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        action="workspace.snapshot",
    )
    if workspace.generation != expected_workspace_generation:
        raise WorkspaceDataConflict("workspace generation changed before snapshot")
    if workspace.observed_state not in {"stopped", "paused"}:
        raise WorkspaceDataConflict("snapshot requires a stopped or paused workspace")
    request_hash = snapshot_capture_request_hash(
        workspace_id=workspace_id,
        expected_workspace_generation=expected_workspace_generation,
        display_name=display_name,
        idempotency_key=idempotency_key,
    )
    operation = lock_operation(session, tenant_id=tenant_id, operation_id=operation_id)
    _validate_operation(
        operation,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_user_id,
        kind=_CAPTURE_KIND,
        request_hash=request_hash,
        resource_id=workspace_id,
        resource_version=None,
    )
    _reject_existing_effect(session, tenant_id=tenant_id, operation_id=operation_id)

    resources = _list_inventory_resources(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if not resources:
        raise WorkspaceDataConflict("snapshot inventory is empty")
    verified: list[tuple[ResourceRecord, VerifiedSnapshotPayload]] = []
    for resource in resources:
        payload = verifier.capture(
            resource=resource,
            workspace_id=workspace_id,
            workspace_generation=expected_workspace_generation,
        )
        _validate_payload_binding(resource=resource, payload=payload)
        _verify_payload_artifact(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            payload=payload,
        )
        verified.append((resource, payload))

    current_workspace = _lock_workspace_identity(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
    )
    if (
        current_workspace.generation != expected_workspace_generation
        or current_workspace.observed_state not in {"stopped", "paused"}
    ):
        raise WorkspaceDataConflict("workspace generation changed during snapshot")

    item_values = [
        {"ordinal": index, **_manifest_item(resource, payload)}
        for index, (resource, payload) in enumerate(verified, start=1)
    ]
    manifest_digest = _manifest_digest(
        workspace_id=workspace_id,
        source_generation=expected_workspace_generation,
        item_values=item_values,
    )
    snapshot_resource = register_resource(
        session,
        tenant_id=tenant_id,
        kind="snapshot",
        owner_type="workspace",
        owner_id=workspace_id,
        parent_id=workspace_id,
        display_name=display_name,
        state="provisioning",
        policy_class="workspace_private",
        physical_locator=None,
        metadata={
            "manifest_schema_version": _MANIFEST_SCHEMA_VERSION,
            "source_generation": expected_workspace_generation,
        },
        created_by_actor_id=actor_user_id,
    )
    snapshot = WorkspaceSnapshot(
        id=snapshot_resource.id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_generation=expected_workspace_generation,
        manifest_digest=manifest_digest,
        snapshot_metadata={
            "manifest_schema_version": _MANIFEST_SCHEMA_VERSION,
            "item_count": len(item_values),
            "total_bytes": sum(payload.size_bytes for _resource, payload in verified),
            "operation_id": operation.id,
        },
        state="building",
        created_by_user_id=actor_user_id,
    )
    items = tuple(
        WorkspaceSnapshotItem(
            snapshot_id=snapshot_resource.id,
            ordinal=index,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            source_resource_id=resource.id,
            source_version=resource.version,
            source_kind=resource.kind,
            source_policy_class=resource.policy_class,
            display_name=resource.display_name,
            item_kind=_item_kind(resource).value,
            content_digest=payload.content_digest,
            payload_artifact_id=payload.payload_artifact_id,
            size_bytes=payload.size_bytes,
        )
        for index, (resource, payload) in enumerate(verified, start=1)
    )
    session.add_all(
        [
            snapshot,
            ResourceScopeBinding(
                resource_id=snapshot_resource.id,
                tenant_id=tenant_id,
                scope_class="workspace_private",
                workspace_id=workspace_id,
            ),
            *items,
        ]
    )
    session.flush()
    effect = create_effect(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation.id,
        effect_kind="snapshot_capture",
        resource_id=snapshot.id,
        binding={
            "snapshot_id": snapshot.id,
            "workspace_id": workspace_id,
            "source_generation": expected_workspace_generation,
            "manifest_digest": manifest_digest,
            "request_hash": request_hash,
        },
    )
    if effect.state != "pending":
        raise WorkspaceDataConflict("snapshot capture effect cannot be replayed")

    if outcome is EffectOutcome.COMMITTED:
        transition_effect(
            effect,
            target_state="committed",
            receipt_digest=receipt_digest or manifest_digest,
        )
        snapshot.state = "ready"
        snapshot_resource.state = "active"
        append_resource_lineage(
            session,
            tenant_id=tenant_id,
            source_resource_id=workspace_id,
            derived_resource_id=snapshot.id,
            relation="snapshot_of",
            source_version=lock_resource(
                session, tenant_id=tenant_id, resource_id=workspace_id
            ).version,
            transform_digest=manifest_digest,
            created_by_operation_id=operation.id,
        )
        finish_operation(
            session,
            operation=operation,
            succeeded=True,
            result_ref={"snapshot_id": snapshot.id, "manifest_digest": manifest_digest},
        )
        decision = "allowed"
        status_code = 201
    else:
        terminal_state = "unknown" if outcome is EffectOutcome.UNKNOWN else "failed"
        transition_effect(
            effect,
            target_state=terminal_state,
            receipt_digest=receipt_digest,
            reason_code=reason_code or f"snapshot.capture.{terminal_state}",
        )
        snapshot.state = "failed"
        snapshot_resource.state = "failed"
        finish_operation(
            session,
            operation=operation,
            succeeded=False,
            error_code=f"workspace_snapshot_capture_{terminal_state}",
        )
        decision = "error"
        status_code = 503

    audit_data_event(
        session,
        tenant_id=tenant_id,
        request_id=request_id,
        actor_user_id=actor_user_id,
        workspace_id=workspace_id,
        resource_id=snapshot.id,
        operation_id=operation.id,
        action=_CAPTURE_KIND,
        decision=decision,
        risk_level=operation.risk_level,
        input_hash=request_hash,
        after_version=snapshot_resource.version,
        status_code=status_code,
        reason_code=reason_code,
    )
    return SnapshotCaptureResult(snapshot=snapshot, items=items, outcome=outcome)


def restore_workspace_snapshot(
    session: Session,
    *,
    tenant_id: str,
    source_workspace_id: str,
    snapshot_id: str,
    actor_user_id: str,
    operation_id: str,
    display_name: str,
    idempotency_key: str,
    request_id: str,
    verifier: SnapshotPayloadVerifier,
    outcome: EffectOutcome,
    receipt_digest: str | None = None,
    reason_code: str | None = None,
) -> SnapshotRestoreResult:
    """Restore verified items into a new Workspace and new logical resources."""

    if outcome is EffectOutcome.COMMITTED:
        raise WorkspaceDataDenied(
            "provider-backed snapshot restore is unavailable until subtype adapters pass Gate"
        )

    source_workspace, _membership = lock_workspace_scope(
        session,
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        actor_user_id=actor_user_id,
        action="workspace.restore",
    )
    snapshot = _lock_snapshot(
        session,
        tenant_id=tenant_id,
        source_workspace_id=source_workspace_id,
        snapshot_id=snapshot_id,
    )
    snapshot_resource = lock_resource(session, tenant_id=tenant_id, resource_id=snapshot_id)
    request_hash = snapshot_restore_request_hash(
        source_workspace_id=source_workspace_id,
        snapshot_id=snapshot_id,
        display_name=display_name,
        idempotency_key=idempotency_key,
    )
    operation = lock_operation(session, tenant_id=tenant_id, operation_id=operation_id)
    _validate_operation(
        operation,
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        actor_user_id=actor_user_id,
        kind=_RESTORE_KIND,
        request_hash=request_hash,
        resource_id=snapshot_id,
        resource_version=snapshot_resource.version,
    )
    _reject_existing_effect(session, tenant_id=tenant_id, operation_id=operation_id)

    items = tuple(_load_snapshot_items(session, tenant_id=tenant_id, snapshot_id=snapshot_id))
    verify_snapshot_manifest(snapshot=snapshot, items=items)
    for item in items:
        payload = verifier.verify(item=item)
        _validate_item_payload(item=item, payload=payload)
        _verify_payload_artifact(
            session,
            tenant_id=tenant_id,
            workspace_id=source_workspace_id,
            payload=payload,
        )

    effect = create_effect(
        session,
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        operation_id=operation.id,
        effect_kind="snapshot_restore",
        resource_id=snapshot.id,
        binding={
            "source_workspace_id": source_workspace_id,
            "snapshot_id": snapshot.id,
            "manifest_digest": snapshot.manifest_digest,
            "request_hash": request_hash,
        },
    )
    if effect.state != "pending":
        raise WorkspaceDataConflict("snapshot restore effect cannot be replayed")

    workspace_resource = register_resource(
        session,
        tenant_id=tenant_id,
        kind="workspace",
        owner_type="user",
        owner_id=actor_user_id,
        parent_id=source_workspace.parent_workspace_id,
        display_name=display_name,
        state="provisioning",
        policy_class="workspace_private",
        physical_locator=None,
        metadata={"restored_from_snapshot_id": snapshot.id},
        created_by_actor_id=actor_user_id,
    )
    restored = Workspace(
        id=workspace_resource.id,
        tenant_id=tenant_id,
        template_id=source_workspace.template_id,
        owner_user_id=actor_user_id,
        parent_workspace_id=source_workspace.parent_workspace_id,
        restored_from_snapshot_id=snapshot.id,
        display_name=display_name,
        desired_state="stopped",
        observed_state="provisioning",
        generation=max(source_workspace.generation, snapshot.source_generation) + 1,
        version=1,
        quota=dict(source_workspace.quota),
    )
    session.add_all(
        [
            restored,
            ResourceScopeBinding(
                resource_id=restored.id,
                tenant_id=tenant_id,
                scope_class="workspace_private",
                workspace_id=restored.id,
            ),
            WorkspaceMembership(
                tenant_id=tenant_id,
                workspace_id=restored.id,
                user_id=actor_user_id,
                role="owner",
                state="active",
                created_by_user_id=actor_user_id,
            ),
        ]
    )
    restored_resources: list[ResourceRecord] = []
    for item in items:
        resource = register_resource(
            session,
            tenant_id=tenant_id,
            kind=item.source_kind,
            owner_type="workspace",
            owner_id=restored.id,
            parent_id=restored.id,
            display_name=item.display_name,
            state="provisioning",
            policy_class=item.source_policy_class,
            physical_locator=None,
            metadata={
                "restored_from_resource_id": item.source_resource_id,
                "restored_from_snapshot_id": snapshot.id,
                "content_digest": item.content_digest,
                "size_bytes": item.size_bytes,
            },
            created_by_actor_id=actor_user_id,
        )
        restored_resources.append(resource)
        session.add(
            ResourceScopeBinding(
                resource_id=resource.id,
                tenant_id=tenant_id,
                scope_class="workspace_private",
                workspace_id=restored.id,
            )
        )
    session.flush()

    terminal_state = "unknown" if outcome is EffectOutcome.UNKNOWN else "failed"
    transition_effect(
        effect,
        target_state=terminal_state,
        receipt_digest=receipt_digest,
        reason_code=reason_code or f"snapshot.restore.{terminal_state}",
    )
    restored.observed_state = "failed"
    workspace_resource.state = "failed"
    for resource in restored_resources:
        resource.state = "failed"
    finish_operation(
        session,
        operation=operation,
        succeeded=False,
        error_code=f"workspace_snapshot_restore_{terminal_state}",
    )
    decision = "error"
    status_code = 503

    audit_data_event(
        session,
        tenant_id=tenant_id,
        request_id=request_id,
        actor_user_id=actor_user_id,
        workspace_id=source_workspace_id,
        resource_id=snapshot.id,
        operation_id=operation.id,
        action=_RESTORE_KIND,
        decision=decision,
        risk_level=operation.risk_level,
        input_hash=request_hash,
        after_version=restored.version,
        status_code=status_code,
        reason_code=reason_code,
    )
    return SnapshotRestoreResult(
        workspace=restored,
        resource_ids=tuple(resource.id for resource in restored_resources),
        outcome=outcome,
    )


def verify_snapshot_manifest(
    *, snapshot: WorkspaceSnapshot, items: Sequence[WorkspaceSnapshotItem]
) -> None:
    """Reject any post-seal item addition, removal, reorder, or metadata drift."""

    if snapshot.state != "ready":
        raise WorkspaceDataConflict("snapshot is not sealed and ready")
    metadata = snapshot.snapshot_metadata
    if metadata.get("manifest_schema_version") != _MANIFEST_SCHEMA_VERSION:
        raise WorkspaceDataConflict("snapshot manifest schema is unsupported")
    ordered = sorted(items, key=lambda item: item.ordinal)
    if any(
        item.tenant_id != snapshot.tenant_id
        or item.snapshot_id != snapshot.id
        or item.workspace_id != snapshot.workspace_id
        for item in ordered
    ):
        raise WorkspaceDataConflict("snapshot item scope binding changed")
    if [item.ordinal for item in ordered] != list(range(1, len(ordered) + 1)):
        raise WorkspaceDataConflict("snapshot manifest ordinals are not contiguous")
    if metadata.get("item_count") != len(ordered):
        raise WorkspaceDataConflict("snapshot manifest item count changed")
    if metadata.get("total_bytes") != sum(item.size_bytes for item in ordered):
        raise WorkspaceDataConflict("snapshot manifest size changed")
    item_values = [
        {
            "ordinal": item.ordinal,
            "source_resource_id": item.source_resource_id,
            "source_version": item.source_version,
            "source_kind": item.source_kind,
            "source_policy_class": item.source_policy_class,
            "display_name": item.display_name,
            "item_kind": item.item_kind,
            "content_digest": item.content_digest,
            "payload_artifact_id": item.payload_artifact_id,
            "size_bytes": item.size_bytes,
        }
        for item in ordered
    ]
    actual = _manifest_digest(
        workspace_id=snapshot.workspace_id,
        source_generation=snapshot.source_generation,
        item_values=item_values,
    )
    if actual != snapshot.manifest_digest:
        raise WorkspaceDataConflict("snapshot manifest digest changed")


def _validate_operation(
    operation: OperationRecord,
    *,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    kind: str,
    request_hash: str,
    resource_id: str,
    resource_version: int | None,
) -> None:
    if not all(
        (
            operation.tenant_id == tenant_id,
            operation.workspace_id == workspace_id,
            operation.actor_type == "user",
            operation.actor_id == actor_user_id,
            operation.kind == kind,
            operation.state == "queued",
            operation.request_hash == request_hash,
            operation.resource_id == resource_id,
            operation.resource_version == resource_version,
        )
    ):
        raise WorkspaceDataConflict("snapshot operation binding changed")


def _list_inventory_resources(
    session: Session, *, tenant_id: str, workspace_id: str
) -> list[ResourceRecord]:
    return list(
        session.scalars(
            select(ResourceRecord)
            .join(
                ResourceScopeBinding,
                (ResourceScopeBinding.resource_id == ResourceRecord.id)
                & (ResourceScopeBinding.tenant_id == ResourceRecord.tenant_id),
            )
            .where(
                ResourceRecord.tenant_id == tenant_id,
                ResourceRecord.owner_type == "workspace",
                ResourceRecord.owner_id == workspace_id,
                ResourceRecord.policy_class.in_({"workspace_private", "workspace_derived"}),
                ResourceRecord.kind.in_(set(_SUPPORTED_ITEM_KINDS)),
                ResourceRecord.state == "active",
                ResourceScopeBinding.workspace_id == workspace_id,
                ResourceScopeBinding.scope_class == "workspace_private",
            )
            .order_by(ResourceRecord.id)
            .with_for_update()
        )
    )


def _lock_workspace_identity(session: Session, *, tenant_id: str, workspace_id: str) -> Workspace:
    workspace = session.execute(
        select(Workspace)
        .where(Workspace.id == workspace_id, Workspace.tenant_id == tenant_id)
        .with_for_update()
    ).scalar_one_or_none()
    if workspace is None:
        raise WorkspaceDataDenied("workspace snapshot is unavailable")
    return workspace


def _lock_snapshot(
    session: Session,
    *,
    tenant_id: str,
    source_workspace_id: str,
    snapshot_id: str,
) -> WorkspaceSnapshot:
    snapshot = session.execute(
        select(WorkspaceSnapshot)
        .where(
            WorkspaceSnapshot.id == snapshot_id,
            WorkspaceSnapshot.tenant_id == tenant_id,
            WorkspaceSnapshot.workspace_id == source_workspace_id,
            WorkspaceSnapshot.state == "ready",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if snapshot is None:
        raise WorkspaceDataDenied("workspace snapshot is unavailable")
    return snapshot


def _load_snapshot_items(
    session: Session, *, tenant_id: str, snapshot_id: str
) -> list[WorkspaceSnapshotItem]:
    return list(
        session.scalars(
            select(WorkspaceSnapshotItem)
            .where(
                WorkspaceSnapshotItem.tenant_id == tenant_id,
                WorkspaceSnapshotItem.snapshot_id == snapshot_id,
            )
            .order_by(WorkspaceSnapshotItem.ordinal)
            .with_for_update()
        )
    )


def _reject_existing_effect(session: Session, *, tenant_id: str, operation_id: str) -> None:
    effect = session.execute(
        select(WorkspaceDataEffect)
        .where(
            WorkspaceDataEffect.tenant_id == tenant_id,
            WorkspaceDataEffect.operation_id == operation_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if effect is None:
        return
    if effect.state == "unknown":
        raise WorkspaceDataConflict("unknown snapshot effect requires reconciliation")
    raise WorkspaceDataConflict("snapshot operation effect cannot be replayed")


def _verify_payload_artifact(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    payload: VerifiedSnapshotPayload,
) -> None:
    artifact = session.execute(
        select(WorkspaceArtifact)
        .where(
            WorkspaceArtifact.id == payload.payload_artifact_id,
            WorkspaceArtifact.tenant_id == tenant_id,
            WorkspaceArtifact.workspace_id == workspace_id,
            WorkspaceArtifact.state == "available",
        )
        .with_for_update()
    ).scalar_one_or_none()
    if (
        artifact is None
        or artifact.content_digest != payload.content_digest
        or artifact.size_bytes != payload.size_bytes
    ):
        raise WorkspaceDataConflict("snapshot payload is missing or changed")


def _validate_payload_binding(
    *, resource: ResourceRecord, payload: VerifiedSnapshotPayload
) -> None:
    require_digest(payload.content_digest, "content_digest")
    if payload.size_bytes < 0:
        raise ValueError("snapshot payload size cannot be negative")
    if payload.source_resource_id != resource.id or payload.source_version != resource.version:
        raise WorkspaceDataConflict("snapshot resource version changed during capture")
    _item_kind(resource)


def _validate_item_payload(
    *, item: WorkspaceSnapshotItem, payload: VerifiedSnapshotPayload
) -> None:
    require_digest(payload.content_digest, "content_digest")
    if not all(
        (
            payload.source_resource_id == item.source_resource_id,
            payload.source_version == item.source_version,
            payload.content_digest == item.content_digest,
            payload.payload_artifact_id == item.payload_artifact_id,
            payload.size_bytes == item.size_bytes,
        )
    ):
        raise WorkspaceDataConflict("snapshot payload verification changed")


def _item_kind(resource: ResourceRecord) -> SnapshotItemKind:
    expected_policy = {
        SnapshotItemKind.PRIVATE_TABLE: "workspace_private",
        SnapshotItemKind.ARTIFACT: "workspace_private",
        SnapshotItemKind.DERIVED_INDEX: "workspace_derived",
    }
    kind = _SUPPORTED_ITEM_KINDS.get(resource.kind)
    if kind is None or resource.policy_class != expected_policy[kind]:
        raise WorkspaceDataConflict("resource is outside the snapshot inventory closed set")
    return kind


def _manifest_item(resource: ResourceRecord, payload: VerifiedSnapshotPayload) -> dict[str, object]:
    return {
        "source_resource_id": resource.id,
        "source_version": resource.version,
        "source_kind": resource.kind,
        "source_policy_class": resource.policy_class,
        "display_name": resource.display_name,
        "item_kind": _item_kind(resource).value,
        "content_digest": payload.content_digest,
        "payload_artifact_id": payload.payload_artifact_id,
        "size_bytes": payload.size_bytes,
    }


def _manifest_digest(
    *,
    workspace_id: str,
    source_generation: int,
    item_values: Sequence[dict[str, object]],
) -> str:
    return canonical_digest(
        {
            "schema_version": _MANIFEST_SCHEMA_VERSION,
            "workspace_id": workspace_id,
            "source_generation": source_generation,
            "items": list(item_values),
        }
    )


__all__ = [
    "SnapshotCaptureResult",
    "SnapshotPayloadVerifier",
    "SnapshotRestoreResult",
    "VerifiedSnapshotPayload",
    "capture_workspace_snapshot",
    "restore_workspace_snapshot",
    "snapshot_capture_request_hash",
    "snapshot_restore_request_hash",
    "verify_snapshot_manifest",
]
