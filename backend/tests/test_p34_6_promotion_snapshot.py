"""P34.6 promotion approval and snapshot/restore safety tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from omnibase.control_plane.models import ApprovalRequest, OperationRecord, ResourceRecord
from omnibase.workspace_data import promotion, snapshots
from omnibase.workspace_data.contracts import (
    EffectOutcome,
    PublicationRequest,
    PublicationTargetScope,
)
from omnibase.workspace_data.models import WorkspaceDataEffect
from omnibase.workspace_data.promotion import TrustedTenantAdminFacts
from omnibase.workspace_data.service import (
    WorkspaceDataConflict,
    WorkspaceDataDenied,
    canonical_digest,
)
from omnibase.workspace_data.snapshots import VerifiedSnapshotPayload
from omnibase.workspaces.models import Workspace, WorkspaceSnapshot


def _id() -> str:
    return str(uuid4())


class _FakeSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, item: object) -> None:
        self.added.append(item)

    def add_all(self, items: list[object] | tuple[object, ...]) -> None:
        self.added.extend(items)

    def flush(self) -> None:
        for item in self.added:
            mutable = cast(Any, item)
            if hasattr(item, "id") and getattr(item, "id", None) is None:
                mutable.id = _id()
            if hasattr(item, "version") and getattr(item, "version", None) is None:
                mutable.version = 1

    def execute(self, _statement: object) -> Any:
        raise AssertionError("unexpected unpatched database query")

    def scalars(self, _statement: object) -> Any:
        raise AssertionError("unexpected unpatched database query")


def _workspace(*, workspace_id: str, tenant_id: str, generation: int = 4) -> Workspace:
    return Workspace(
        id=workspace_id,
        tenant_id=tenant_id,
        template_id=_id(),
        owner_user_id=_id(),
        display_name="source",
        desired_state="stopped",
        observed_state="stopped",
        generation=generation,
        version=1,
        quota={},
    )


def _resource(
    *,
    resource_id: str,
    tenant_id: str,
    workspace_id: str,
    kind: str = "artifact",
    policy_class: str = "workspace_private",
    version: int = 3,
) -> ResourceRecord:
    return ResourceRecord(
        id=resource_id,
        tenant_id=tenant_id,
        kind=kind,
        owner_type="workspace",
        owner_id=workspace_id,
        parent_id=workspace_id,
        display_name="source data",
        state="active",
        version=version,
        policy_class=policy_class,
        physical_locator={"server_owned": "opaque"},
        resource_metadata={},
        created_by_actor_id=_id(),
    )


def _operation(
    *,
    tenant_id: str,
    workspace_id: str,
    actor_id: str,
    resource_id: str,
    resource_version: int | None,
    request_hash: str,
    kind: str,
    approval_id: str | None = None,
) -> OperationRecord:
    return OperationRecord(
        id=_id(),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_type="user",
        actor_id=actor_id,
        resource_id=resource_id,
        resource_version=resource_version,
        approval_id=approval_id,
        request_hash=request_hash,
        kind=kind,
        state="queued",
        risk_level="R2" if approval_id is not None else "R1",
        progress=0,
        attempt_count=0,
        version=1,
        operation_metadata={},
    )


def _effect(*, tenant_id: str, workspace_id: str, operation_id: str) -> WorkspaceDataEffect:
    return WorkspaceDataEffect(
        id=_id(),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=None,
        operation_id=operation_id,
        sequence=1,
        effect_kind="publication_copy",
        binding_digest="a" * 64,
        state="pending",
        version=1,
    )


def _promotion_context(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    tenant_id = _id()
    source_workspace_id = _id()
    source_resource_id = _id()
    requester_id = _id()
    approver_id = _id()
    approval_id = _id()
    digest = "a" * 64
    workspace = _workspace(workspace_id=source_workspace_id, tenant_id=tenant_id)
    source = _resource(
        resource_id=source_resource_id,
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
    )
    request = PublicationRequest(
        source_workspace_id=source_workspace_id,
        source_resource_id=source_resource_id,
        source_version=source.version,
        source_manifest_digest=digest,
        target_scope=PublicationTargetScope.TENANT_SHARED,
        display_name="approved copy",
        idempotency_key="publish-once",
        request_id="req-publish",
    )
    request_hash = promotion.promotion_request_hash(request)
    operation = _operation(
        tenant_id=tenant_id,
        workspace_id=source_workspace_id,
        actor_id=requester_id,
        resource_id=source_resource_id,
        resource_version=source.version,
        request_hash=request_hash,
        kind="workspace_data.promote",
        approval_id=approval_id,
    )
    approval = ApprovalRequest(
        id=approval_id,
        tenant_id=tenant_id,
        requester_type="user",
        requester_id=requester_id,
        workspace_id=source_workspace_id,
        resource_id=source_resource_id,
        operation_id=operation.id,
        grant_id=_id(),
        action="workspace.data.publish",
        risk_level="R2",
        required_approver_role="tenant_admin",
        state="consumed",
        request_hash=request_hash,
        resource_version=source.version,
        version=3,
        decided_by_actor_type="user",
        decided_by_actor_id=approver_id,
        expires_at=datetime.now(UTC) + timedelta(minutes=2),
        decided_at=datetime.now(UTC) - timedelta(seconds=5),
        consumed_at=datetime.now(UTC) - timedelta(seconds=2),
        approval_metadata={},
    )
    admin = TrustedTenantAdminFacts(
        tenant_id=tenant_id,
        user_id=approver_id,
        is_active=True,
        is_tenant_admin=True,
        verified_at=datetime.now(UTC) - timedelta(seconds=1),
        expires_at=datetime.now(UTC) + timedelta(seconds=10),
    )
    session = _FakeSession()
    created_targets: list[ResourceRecord] = []
    effects: list[WorkspaceDataEffect] = []

    monkeypatch.setattr(
        promotion,
        "lock_workspace_scope",
        lambda *_args, **_kwargs: (workspace, SimpleNamespace(role="owner")),
    )
    monkeypatch.setattr(promotion, "require_workspace_resource", lambda *_a, **_k: source)
    monkeypatch.setattr(promotion, "lock_operation", lambda *_a, **_k: operation)
    monkeypatch.setattr(promotion, "_lock_approval", lambda *_a, **_k: approval)
    monkeypatch.setattr(promotion, "_lock_operation_publication", lambda *_a, **_k: None)
    monkeypatch.setattr(promotion, "_lock_duplicate_publication", lambda *_a, **_k: None)

    def register_target(_session: object, **kwargs: object) -> ResourceRecord:
        target = ResourceRecord(id=_id(), version=1, **kwargs)
        created_targets.append(target)
        return target

    def create_promotion_effect(_session: object, **kwargs: object) -> WorkspaceDataEffect:
        effect = _effect(
            tenant_id=str(kwargs["tenant_id"]),
            workspace_id=str(kwargs["workspace_id"]),
            operation_id=str(kwargs["operation_id"]),
        )
        effect.resource_id = str(kwargs["resource_id"])
        effects.append(effect)
        return effect

    monkeypatch.setattr(promotion, "register_resource", register_target)
    monkeypatch.setattr(promotion, "create_effect", create_promotion_effect)
    monkeypatch.setattr(promotion, "finish_operation", lambda *_a, **_k: None)
    monkeypatch.setattr(promotion, "audit_data_event", lambda *_a, **_k: None)
    return {
        "tenant_id": tenant_id,
        "requester_id": requester_id,
        "approver_id": approver_id,
        "approval_id": approval_id,
        "digest": digest,
        "workspace": workspace,
        "source": source,
        "request": request,
        "operation": operation,
        "approval": approval,
        "admin": admin,
        "session": session,
        "targets": created_targets,
        "effects": effects,
    }


def _execute_promotion(
    context: dict[str, object], **overrides: object
) -> promotion.PromotionResult:
    values: dict[str, object] = {
        "session": context["session"],
        "tenant_id": context["tenant_id"],
        "requester_user_id": context["requester_id"],
        "operation_id": context["operation"].id,
        "approval_id": context["approval_id"],
        "request": context["request"],
        "verified_source_digest": context["digest"],
        "tenant_admin": context["admin"],
        "outcome": EffectOutcome.UNKNOWN,
        "receipt_digest": None,
    }
    values.update(overrides)
    return promotion.execute_promotion(**values)  # type: ignore[arg-type]


def test_promotion_commit_remains_unavailable_without_durable_copy_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _promotion_context(monkeypatch)
    source = context["source"]
    before = (source.policy_class, source.version, source.physical_locator, source.owner_id)
    with pytest.raises(WorkspaceDataDenied, match="durable copy adapter"):
        _execute_promotion(
            context,
            outcome=EffectOutcome.COMMITTED,
            receipt_digest="b" * 64,
        )
    assert (source.policy_class, source.version, source.physical_locator, source.owner_id) == before
    assert context["targets"] == []


def test_promotion_rejects_self_approval_and_missing_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _promotion_context(monkeypatch)
    context["approval"].decided_by_actor_id = context["requester_id"]
    with pytest.raises(WorkspaceDataDenied, match="approval"):
        _execute_promotion(context)

    context = _promotion_context(monkeypatch)
    monkeypatch.setattr(
        promotion,
        "_lock_approval",
        lambda *_a, **_k: (_ for _ in ()).throw(
            WorkspaceDataDenied("promotion approval is unavailable")
        ),
    )
    with pytest.raises(WorkspaceDataDenied, match="approval"):
        _execute_promotion(context)


def test_promotion_rejects_canonical_target_and_duplicate_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _promotion_context(monkeypatch)
    with pytest.raises(WorkspaceDataDenied, match="target policy"):
        _execute_promotion(context, target_policy_class="canonical_readonly")

    context = _promotion_context(monkeypatch)
    monkeypatch.setattr(
        promotion,
        "_lock_duplicate_publication",
        lambda *_a, **_k: SimpleNamespace(state="published"),
    )
    with pytest.raises(WorkspaceDataConflict, match="already published"):
        _execute_promotion(context)
    assert context["targets"] == []


def test_workspace_shared_promotion_requires_live_target_workspace_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _promotion_context(monkeypatch)
    target_workspace_id = _id()
    request = PublicationRequest(
        source_workspace_id=context["workspace"].id,
        source_resource_id=context["source"].id,
        source_version=context["source"].version,
        source_manifest_digest=context["digest"],
        target_scope=PublicationTargetScope.WORKSPACE_SHARED,
        target_workspace_id=target_workspace_id,
        display_name="approved copy",
        idempotency_key="publish-to-workspace-once",
        request_id="req-publish-target",
    )
    request_hash = promotion.promotion_request_hash(request)
    context["request"] = request
    context["operation"].request_hash = request_hash
    context["approval"].request_hash = request_hash
    monkeypatch.setattr(
        promotion,
        "_lock_target_workspace",
        lambda *_a, **_k: _workspace(
            workspace_id=target_workspace_id,
            tenant_id=context["tenant_id"],
        ),
    )
    monkeypatch.setattr(
        promotion,
        "authorize_workspace_action",
        lambda *_a, **_k: (_ for _ in ()).throw(
            promotion.WorkspacePolicyDenied("workspace not found")
        ),
    )

    with pytest.raises(WorkspaceDataDenied, match="target is unavailable"):
        _execute_promotion(context)
    assert context["targets"] == []


def test_unknown_promotion_effect_is_not_replayed(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _promotion_context(monkeypatch)
    first = _execute_promotion(
        context,
        outcome=EffectOutcome.UNKNOWN,
        receipt_digest=None,
        reason_code="provider.outcome_unknown",
    )
    assert first.publication.state == "unknown"
    assert context["effects"][0].state == "unknown"
    monkeypatch.setattr(
        promotion,
        "_lock_operation_publication",
        lambda *_a, **_k: first.publication,
    )
    with pytest.raises(WorkspaceDataConflict, match="requires reconciliation"):
        _execute_promotion(context)


class _Verifier:
    def __init__(self, payload: VerifiedSnapshotPayload) -> None:
        self.payload = payload

    def capture(self, **_kwargs: object) -> VerifiedSnapshotPayload:
        return self.payload

    def verify(self, **_kwargs: object) -> VerifiedSnapshotPayload:
        return self.payload


def _capture_context(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    tenant_id = _id()
    workspace_id = _id()
    actor_id = _id()
    workspace = _workspace(workspace_id=workspace_id, tenant_id=tenant_id, generation=7)
    source = _resource(
        resource_id=_id(),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        kind="artifact",
        version=2,
    )
    payload = VerifiedSnapshotPayload(
        source_resource_id=source.id,
        source_version=source.version,
        content_digest="c" * 64,
        payload_artifact_id=_id(),
        size_bytes=42,
    )
    request_hash = snapshots.snapshot_capture_request_hash(
        workspace_id=workspace_id,
        expected_workspace_generation=workspace.generation,
        display_name="sealed snapshot",
        idempotency_key="snap-once",
    )
    operation = _operation(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        resource_id=workspace_id,
        resource_version=None,
        request_hash=request_hash,
        kind="workspace_data.snapshot.capture",
    )
    session = _FakeSession()
    snapshot_resource = _resource(
        resource_id=_id(),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        kind="snapshot",
        version=1,
    )
    snapshot_resource.state = "provisioning"
    lineages: list[dict[str, object]] = []
    effect = _effect(tenant_id=tenant_id, workspace_id=workspace_id, operation_id=operation.id)
    effect.effect_kind = "snapshot_capture"

    monkeypatch.setattr(
        snapshots,
        "lock_workspace_scope",
        lambda *_a, **_k: (workspace, SimpleNamespace(role="operator")),
    )
    monkeypatch.setattr(snapshots, "lock_operation", lambda *_a, **_k: operation)
    monkeypatch.setattr(snapshots, "_reject_existing_effect", lambda *_a, **_k: None)
    monkeypatch.setattr(snapshots, "_list_inventory_resources", lambda *_a, **_k: [source])
    monkeypatch.setattr(snapshots, "_verify_payload_artifact", lambda *_a, **_k: None)
    monkeypatch.setattr(snapshots, "_lock_workspace_identity", lambda *_a, **_k: workspace)
    monkeypatch.setattr(snapshots, "register_resource", lambda *_a, **_k: snapshot_resource)
    monkeypatch.setattr(snapshots, "create_effect", lambda *_a, **_k: effect)
    monkeypatch.setattr(
        snapshots,
        "lock_resource",
        lambda *_a, **_k: ResourceRecord(id=workspace_id, version=1),
    )
    monkeypatch.setattr(
        snapshots,
        "append_resource_lineage",
        lambda _session, **kwargs: lineages.append(dict(kwargs)),
    )
    monkeypatch.setattr(snapshots, "finish_operation", lambda *_a, **_k: None)
    monkeypatch.setattr(snapshots, "audit_data_event", lambda *_a, **_k: None)
    return {
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "workspace": workspace,
        "source": source,
        "payload": payload,
        "operation": operation,
        "session": session,
        "verifier": _Verifier(payload),
        "lineages": lineages,
    }


def _capture(context: dict[str, object]) -> snapshots.SnapshotCaptureResult:
    workspace = context["workspace"]
    return snapshots.capture_workspace_snapshot(
        context["session"],  # type: ignore[arg-type]
        tenant_id=context["tenant_id"],  # type: ignore[arg-type]
        workspace_id=context["workspace_id"],  # type: ignore[arg-type]
        actor_user_id=context["actor_id"],  # type: ignore[arg-type]
        operation_id=context["operation"].id,
        expected_workspace_generation=workspace.generation,
        display_name="sealed snapshot",
        idempotency_key="snap-once",
        request_id="req-snapshot",
        verifier=context["verifier"],  # type: ignore[arg-type]
        outcome=EffectOutcome.COMMITTED,
    )


def test_snapshot_is_server_verified_and_manifest_tamper_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _capture_context(monkeypatch)
    result = _capture(context)
    assert result.snapshot.state == "ready"
    assert len(result.items) == 1
    snapshots.verify_snapshot_manifest(snapshot=result.snapshot, items=result.items)
    result.items[0].content_digest = "d" * 64
    with pytest.raises(WorkspaceDataConflict, match="digest changed"):
        snapshots.verify_snapshot_manifest(snapshot=result.snapshot, items=result.items)

    result.items[0].content_digest = "c" * 64
    result.items[0].ordinal = 2
    with pytest.raises(WorkspaceDataConflict, match="ordinals"):
        snapshots.verify_snapshot_manifest(snapshot=result.snapshot, items=result.items)

    result.items[0].ordinal = 1
    result.items[0].workspace_id = _id()
    with pytest.raises(WorkspaceDataConflict, match="scope binding"):
        snapshots.verify_snapshot_manifest(snapshot=result.snapshot, items=result.items)


def test_snapshot_rejects_generation_and_payload_drift(monkeypatch: pytest.MonkeyPatch) -> None:
    context = _capture_context(monkeypatch)
    drifted = _workspace(
        workspace_id=context["workspace_id"],  # type: ignore[arg-type]
        tenant_id=context["tenant_id"],  # type: ignore[arg-type]
        generation=context["workspace"].generation + 1,
    )
    monkeypatch.setattr(snapshots, "_lock_workspace_identity", lambda *_a, **_k: drifted)
    with pytest.raises(WorkspaceDataConflict, match="generation changed"):
        _capture(context)

    context = _capture_context(monkeypatch)
    bad = VerifiedSnapshotPayload(
        source_resource_id=context["source"].id,
        source_version=context["source"].version + 1,
        content_digest="c" * 64,
        payload_artifact_id=_id(),
        size_bytes=42,
    )
    context["verifier"] = _Verifier(bad)
    with pytest.raises(WorkspaceDataConflict, match="version changed"):
        _capture(context)


def test_restore_commit_remains_unavailable_without_subtype_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _capture_context(monkeypatch)
    capture = _capture(context)
    source_workspace = context["workspace"]
    source_item = capture.items[0]
    snapshot_resource = ResourceRecord(id=capture.snapshot.id, version=1)
    request_hash = snapshots.snapshot_restore_request_hash(
        source_workspace_id=source_workspace.id,
        snapshot_id=capture.snapshot.id,
        display_name="restored workspace",
        idempotency_key="restore-once",
    )
    operation = _operation(
        tenant_id=context["tenant_id"],  # type: ignore[arg-type]
        workspace_id=source_workspace.id,
        actor_id=context["actor_id"],  # type: ignore[arg-type]
        resource_id=capture.snapshot.id,
        resource_version=1,
        request_hash=request_hash,
        kind="workspace_data.snapshot.restore",
    )
    restore_effect = _effect(
        tenant_id=context["tenant_id"],  # type: ignore[arg-type]
        workspace_id=source_workspace.id,
        operation_id=operation.id,
    )
    restore_effect.effect_kind = "snapshot_restore"
    created: list[ResourceRecord] = []
    lineages: list[dict[str, object]] = []

    monkeypatch.setattr(
        snapshots,
        "lock_workspace_scope",
        lambda *_a, **_k: (source_workspace, SimpleNamespace(role="maintainer")),
    )
    monkeypatch.setattr(snapshots, "_lock_snapshot", lambda *_a, **_k: capture.snapshot)
    monkeypatch.setattr(snapshots, "lock_resource", lambda *_a, **_k: snapshot_resource)
    monkeypatch.setattr(snapshots, "lock_operation", lambda *_a, **_k: operation)
    monkeypatch.setattr(snapshots, "_reject_existing_effect", lambda *_a, **_k: None)
    monkeypatch.setattr(snapshots, "_load_snapshot_items", lambda *_a, **_k: [source_item])
    monkeypatch.setattr(snapshots, "_verify_payload_artifact", lambda *_a, **_k: None)
    monkeypatch.setattr(snapshots, "create_effect", lambda *_a, **_k: restore_effect)
    monkeypatch.setattr(snapshots, "finish_operation", lambda *_a, **_k: None)
    monkeypatch.setattr(snapshots, "audit_data_event", lambda *_a, **_k: None)
    monkeypatch.setattr(
        snapshots,
        "append_resource_lineage",
        lambda _session, **kwargs: lineages.append(dict(kwargs)),
    )

    def register_restored(_session: object, **kwargs: object) -> ResourceRecord:
        resource = ResourceRecord(id=_id(), version=1, **kwargs)
        created.append(resource)
        return resource

    monkeypatch.setattr(snapshots, "register_resource", register_restored)
    verifier = _Verifier(
        VerifiedSnapshotPayload(
            source_resource_id=source_item.source_resource_id,
            source_version=source_item.source_version,
            content_digest=source_item.content_digest,
            payload_artifact_id=source_item.payload_artifact_id,
            size_bytes=source_item.size_bytes,
        )
    )
    with pytest.raises(WorkspaceDataDenied, match="subtype adapters"):
        snapshots.restore_workspace_snapshot(
            context["session"],  # type: ignore[arg-type]
            tenant_id=context["tenant_id"],  # type: ignore[arg-type]
            source_workspace_id=source_workspace.id,
            snapshot_id=capture.snapshot.id,
            actor_user_id=context["actor_id"],  # type: ignore[arg-type]
            operation_id=operation.id,
            display_name="restored workspace",
            idempotency_key="restore-once",
            request_id="req-restore",
            verifier=verifier,
            outcome=EffectOutcome.COMMITTED,
        )
    assert created == []
    assert lineages == []


def test_manifest_rejects_missing_item() -> None:
    snapshot = WorkspaceSnapshot(
        id=_id(),
        tenant_id=_id(),
        workspace_id=_id(),
        source_generation=1,
        manifest_digest=canonical_digest(
            {
                "schema_version": 1,
                "workspace_id": "unused",
                "source_generation": 1,
                "items": [],
            }
        ),
        snapshot_metadata={
            "manifest_schema_version": 1,
            "item_count": 1,
            "total_bytes": 1,
        },
        state="ready",
        created_by_user_id=_id(),
    )
    with pytest.raises(WorkspaceDataConflict, match="item count"):
        snapshots.verify_snapshot_manifest(snapshot=snapshot, items=[])
