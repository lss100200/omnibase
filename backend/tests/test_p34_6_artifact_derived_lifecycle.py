"""P34.6 Artifact and derived-RAG lifecycle invariants."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from omnibase.workspace_data import artifacts, derived_rag
from omnibase.workspace_data.models import (
    WorkspaceArtifact,
    WorkspaceDataEffect,
    WorkspaceDerivedIndex,
)
from omnibase.workspace_data.service import (
    WorkspaceDataConflict,
    WorkspaceDataNotFound,
    canonical_digest,
)
from omnibase.workspaces.models import ResourceScopeBinding


def _operation(*, workspace_id: str, kind: str, resource_id: str | None = None) -> object:
    return SimpleNamespace(
        id=str(uuid4()),
        workspace_id=workspace_id,
        kind=kind,
        state="queued",
        resource_id=resource_id,
    )


def _resource(
    *,
    resource_id: str,
    workspace_id: str,
    kind: str,
    policy_class: str,
    state: str = "provisioning",
) -> object:
    return SimpleNamespace(
        id=resource_id,
        version=1,
        kind=kind,
        policy_class=policy_class,
        owner_type="workspace",
        owner_id=workspace_id,
        state=state,
    )


def _effect(
    *,
    tenant_id: str,
    workspace_id: str,
    operation_id: str,
    resource_id: str,
    effect_kind: str,
    binding_digest: str,
) -> WorkspaceDataEffect:
    return WorkspaceDataEffect(
        id=str(uuid4()),
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        resource_id=resource_id,
        operation_id=operation_id,
        sequence=1,
        effect_kind=effect_kind,
        binding_digest=binding_digest,
        state="pending",
        version=1,
    )


def test_stage_artifact_creates_new_private_identity_binding_and_pending_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())
    operation_id = str(uuid4())
    resource_id = str(uuid4())
    session = MagicMock()
    added: list[object] = []
    session.add.side_effect = added.append
    resource = _resource(
        resource_id=resource_id,
        workspace_id=workspace_id,
        kind="artifact",
        policy_class="workspace_private",
    )
    created_effect: dict[str, object] = {}

    monkeypatch.setattr(
        artifacts,
        "lock_operation",
        lambda *args, **kwargs: _operation(
            workspace_id=workspace_id,
            kind="artifact.write",
            resource_id=workspace_id,
        ),
    )
    monkeypatch.setattr(artifacts, "_lock_artifact_by_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(artifacts, "register_resource", lambda *args, **kwargs: resource)
    monkeypatch.setattr(
        artifacts,
        "create_effect",
        lambda *args, **kwargs: created_effect.update(kwargs),
    )
    lineage = MagicMock()
    monkeypatch.setattr(artifacts, "append_resource_lineage", lineage)

    result = artifacts.stage_artifact(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        actor_id=str(uuid4()),
        display_name="build.tar.zst",
        content_digest="a" * 64,
        size_bytes=4096,
        media_type="application/zstd",
    )

    assert result.id == resource_id
    assert result.state == "staging"
    assert result.content_digest == "a" * 64
    binding = next(item for item in added if isinstance(item, ResourceScopeBinding))
    assert binding.resource_id == resource_id
    assert binding.workspace_id == workspace_id
    assert binding.scope_class == "workspace_private"
    assert created_effect["effect_kind"] == "artifact_put"
    assert created_effect["resource_id"] == resource_id
    assert created_effect["binding"] == artifacts._artifact_effect_binding(result)
    lineage.assert_not_called()
    session.commit.assert_not_called()


def test_new_artifact_version_gets_new_identity_and_append_only_lineage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())
    operation_id = str(uuid4())
    source_id = str(uuid4())
    new_id = str(uuid4())
    source = _resource(
        resource_id=source_id,
        workspace_id=workspace_id,
        kind="artifact",
        policy_class="workspace_private",
        state="active",
    )
    session = MagicMock()
    monkeypatch.setattr(
        artifacts,
        "lock_operation",
        lambda *args, **kwargs: _operation(workspace_id=workspace_id, kind="artifact.write"),
    )
    monkeypatch.setattr(artifacts, "_lock_artifact_by_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(artifacts, "require_workspace_resource", lambda *args, **kwargs: source)
    monkeypatch.setattr(artifacts, "_source_digest", lambda *args, **kwargs: "b" * 64)
    monkeypatch.setattr(
        artifacts,
        "register_resource",
        lambda *args, **kwargs: _resource(
            resource_id=new_id,
            workspace_id=workspace_id,
            kind="artifact",
            policy_class="workspace_private",
        ),
    )
    lineage = MagicMock()
    monkeypatch.setattr(artifacts, "append_resource_lineage", lineage)
    monkeypatch.setattr(artifacts, "create_effect", MagicMock())

    result = artifacts.stage_artifact(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        actor_id=str(uuid4()),
        display_name="artifact-v2",
        content_digest="c" * 64,
        size_bytes=9,
        media_type="application/octet-stream",
        source_generation=2,
        source_resource_id=source_id,
        source_version=1,
        source_content_digest="b" * 64,
        lineage_relation="transformed_from",
    )

    assert result.id == new_id
    assert result.id != source_id
    lineage.assert_called_once_with(
        session,
        tenant_id=tenant_id,
        source_resource_id=source_id,
        derived_resource_id=new_id,
        relation="transformed_from",
        source_version=1,
        transform_digest=f"sha256:{'c' * 64}",
        created_by_operation_id=operation_id,
    )


def test_artifact_rejects_cross_workspace_and_source_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    workspace_id = str(uuid4())
    monkeypatch.setattr(
        artifacts,
        "lock_operation",
        lambda *args, **kwargs: _operation(
            workspace_id=str(uuid4()),
            kind="artifact.write",
        ),
    )
    with pytest.raises(WorkspaceDataConflict, match="operation binding"):
        artifacts.stage_artifact(
            session,
            tenant_id=str(uuid4()),
            workspace_id=workspace_id,
            operation_id=str(uuid4()),
            actor_id=str(uuid4()),
            display_name="x",
            content_digest="a" * 64,
            size_bytes=1,
            media_type="text/plain",
        )
    session.add.assert_not_called()

    source = _resource(
        resource_id=str(uuid4()),
        workspace_id=workspace_id,
        kind="artifact",
        policy_class="workspace_private",
        state="active",
    )
    monkeypatch.setattr(
        artifacts,
        "lock_operation",
        lambda *args, **kwargs: _operation(workspace_id=workspace_id, kind="artifact.write"),
    )
    monkeypatch.setattr(artifacts, "_lock_artifact_by_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(artifacts, "require_workspace_resource", lambda *args, **kwargs: source)
    monkeypatch.setattr(artifacts, "_source_digest", lambda *args, **kwargs: "d" * 64)
    with pytest.raises(WorkspaceDataConflict, match="source digest changed"):
        artifacts.stage_artifact(
            session,
            tenant_id=str(uuid4()),
            workspace_id=workspace_id,
            operation_id=str(uuid4()),
            actor_id=str(uuid4()),
            display_name="x-v2",
            content_digest="e" * 64,
            size_bytes=2,
            media_type="text/plain",
            source_generation=2,
            source_resource_id=source.id,
            source_version=1,
            source_content_digest="c" * 64,
        )


def test_unknown_artifact_is_terminal_and_cannot_be_replayed_or_revived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())
    operation_id = str(uuid4())
    artifact_id = str(uuid4())
    artifact = WorkspaceArtifact(
        id=artifact_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_run_id=None,
        source_generation=1,
        operation_id=operation_id,
        content_digest="a" * 64,
        size_bytes=1,
        media_type="text/plain",
        state="staging",
        version=1,
        created_by_actor_id=str(uuid4()),
    )
    resource = _resource(
        resource_id=artifact_id,
        workspace_id=workspace_id,
        kind="artifact",
        policy_class="workspace_private",
    )
    effect = _effect(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        resource_id=artifact_id,
        effect_kind="artifact_put",
        binding_digest=canonical_digest(artifacts._artifact_effect_binding(artifact)),
    )
    monkeypatch.setattr(artifacts, "_lock_artifact", lambda *args, **kwargs: artifact)
    monkeypatch.setattr(artifacts, "lock_resource", lambda *args, **kwargs: resource)
    monkeypatch.setattr(artifacts, "_lock_effect", lambda *args, **kwargs: effect)
    session = MagicMock()

    artifacts.finalize_artifact(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        artifact_id=artifact_id,
        operation_id=operation_id,
        target_state="unknown",
        reason_code="provider.outcome_unknown",
    )
    assert artifact.state == "unknown"
    assert effect.state == "unknown"
    with pytest.raises(WorkspaceDataConflict, match="terminal"):
        artifacts.finalize_artifact(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            artifact_id=artifact_id,
            operation_id=operation_id,
            target_state="available",
            receipt_digest="b" * 64,
        )


def test_unknown_stage_and_build_are_not_automatic_replay_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = str(uuid4())
    artifact = WorkspaceArtifact(
        id=str(uuid4()),
        tenant_id=str(uuid4()),
        workspace_id=workspace_id,
        source_run_id=None,
        source_generation=1,
        operation_id=str(uuid4()),
        content_digest="a" * 64,
        size_bytes=1,
        media_type="text/plain",
        state="unknown",
        version=2,
        created_by_actor_id=str(uuid4()),
    )
    monkeypatch.setattr(
        artifacts,
        "lock_operation",
        lambda *args, **kwargs: _operation(workspace_id=workspace_id, kind="artifact.write"),
    )
    monkeypatch.setattr(artifacts, "_lock_artifact_by_operation", lambda *args, **kwargs: artifact)
    with pytest.raises(WorkspaceDataConflict, match="requires reconciliation"):
        artifacts.stage_artifact(
            MagicMock(),
            tenant_id=artifact.tenant_id,
            workspace_id=workspace_id,
            operation_id=artifact.operation_id,
            actor_id=str(uuid4()),
            display_name="artifact",
            content_digest=artifact.content_digest,
            size_bytes=artifact.size_bytes,
            media_type=artifact.media_type,
        )

    index = WorkspaceDerivedIndex(
        id=str(uuid4()),
        tenant_id=str(uuid4()),
        workspace_id=workspace_id,
        source_resource_id=str(uuid4()),
        source_version=1,
        operation_id=str(uuid4()),
        generation=str(uuid4()),
        index_profile_digest="b" * 64,
        manifest_digest=None,
        chunk_count=0,
        state="unknown",
        version=2,
        created_by_actor_id=str(uuid4()),
    )
    monkeypatch.setattr(
        derived_rag,
        "lock_operation",
        lambda *args, **kwargs: _operation(
            workspace_id=workspace_id,
            kind="rag.derived.create",
            resource_id=index.source_resource_id,
        ),
    )
    monkeypatch.setattr(derived_rag, "_lock_index_by_operation", lambda *args, **kwargs: index)
    with pytest.raises(WorkspaceDataConflict, match="requires reconciliation"):
        derived_rag.start_derived_index(
            MagicMock(),
            tenant_id=index.tenant_id,
            workspace_id=workspace_id,
            operation_id=index.operation_id,
            actor_id=str(uuid4()),
            display_name="index",
            source_resource_id=index.source_resource_id,
            source_version=index.source_version,
            source_content_digest="c" * 64,
            index_profile_digest=index.index_profile_digest,
            generation=index.generation,
        )


def test_start_derived_creates_new_identity_lineage_and_never_mutates_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())
    operation_id = str(uuid4())
    source_id = str(uuid4())
    derived_id = str(uuid4())
    source = _resource(
        resource_id=source_id,
        workspace_id=workspace_id,
        kind="artifact",
        policy_class="workspace_private",
        state="active",
    )
    session = MagicMock()
    added: list[object] = []
    session.add.side_effect = added.append
    monkeypatch.setattr(
        derived_rag,
        "lock_operation",
        lambda *args, **kwargs: _operation(
            workspace_id=workspace_id,
            kind="rag.derived.create",
            resource_id=source_id,
        ),
    )
    monkeypatch.setattr(derived_rag, "_lock_index_by_operation", lambda *args, **kwargs: None)
    monkeypatch.setattr(derived_rag, "require_workspace_resource", lambda *args, **kwargs: source)
    monkeypatch.setattr(derived_rag, "_readable_source_digest", lambda *args, **kwargs: "a" * 64)
    monkeypatch.setattr(
        derived_rag,
        "register_resource",
        lambda *args, **kwargs: _resource(
            resource_id=derived_id,
            workspace_id=workspace_id,
            kind="derived_index",
            policy_class="workspace_derived",
        ),
    )
    lineage = MagicMock()
    monkeypatch.setattr(derived_rag, "append_resource_lineage", lineage)
    monkeypatch.setattr(derived_rag, "create_effect", MagicMock())
    generation = str(uuid4())

    result = derived_rag.start_derived_index(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        actor_id=str(uuid4()),
        display_name="private-search-v1",
        source_resource_id=source_id,
        source_version=1,
        source_content_digest="a" * 64,
        index_profile_digest="b" * 64,
        generation=generation,
    )

    assert result.id == derived_id
    assert result.id != source_id
    assert result.state == "building"
    assert source.state == "active"
    assert source.policy_class == "workspace_private"
    assert not hasattr(source, "manifest_digest")
    lineage.assert_called_once_with(
        session,
        tenant_id=tenant_id,
        source_resource_id=source_id,
        derived_resource_id=derived_id,
        relation="derived_from",
        source_version=1,
        transform_digest=f"sha256:{'b' * 64}",
        created_by_operation_id=operation_id,
    )
    assert any(isinstance(item, ResourceScopeBinding) for item in added)


def test_derived_rejects_canonical_lane_version_and_digest_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = str(uuid4())
    source_id = str(uuid4())
    session = MagicMock()
    monkeypatch.setattr(
        derived_rag,
        "lock_operation",
        lambda *args, **kwargs: _operation(
            workspace_id=workspace_id,
            kind="rag.derived.create",
            resource_id=source_id,
        ),
    )
    monkeypatch.setattr(derived_rag, "_lock_index_by_operation", lambda *args, **kwargs: None)
    canonical = _resource(
        resource_id=source_id,
        workspace_id=workspace_id,
        kind="document",
        policy_class="canonical_readonly",
        state="active",
    )
    monkeypatch.setattr(
        derived_rag, "require_workspace_resource", lambda *args, **kwargs: canonical
    )
    common = {
        "tenant_id": str(uuid4()),
        "workspace_id": workspace_id,
        "operation_id": str(uuid4()),
        "actor_id": str(uuid4()),
        "display_name": "index",
        "source_resource_id": source_id,
        "source_version": 1,
        "source_content_digest": "a" * 64,
        "index_profile_digest": "b" * 64,
        "generation": str(uuid4()),
    }
    with pytest.raises(WorkspaceDataNotFound, match="source is not readable"):
        derived_rag.start_derived_index(session, **common)
    session.add.assert_not_called()

    private = _resource(
        resource_id=source_id,
        workspace_id=workspace_id,
        kind="artifact",
        policy_class="workspace_private",
        state="active",
    )
    monkeypatch.setattr(derived_rag, "require_workspace_resource", lambda *args, **kwargs: private)
    monkeypatch.setattr(derived_rag, "_readable_source_digest", lambda *args, **kwargs: "c" * 64)
    with pytest.raises(WorkspaceDataConflict, match="source digest changed"):
        derived_rag.start_derived_index(session, **{**common, "generation": str(uuid4())})

    monkeypatch.setattr(
        derived_rag,
        "require_workspace_resource",
        MagicMock(side_effect=WorkspaceDataConflict("workspace resource version changed")),
    )
    with pytest.raises(WorkspaceDataConflict, match="version changed"):
        derived_rag.start_derived_index(session, **{**common, "generation": str(uuid4())})


def test_only_ready_derived_is_readable_and_unknown_cannot_be_revived(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant_id = str(uuid4())
    workspace_id = str(uuid4())
    operation_id = str(uuid4())
    index_id = str(uuid4())
    index = WorkspaceDerivedIndex(
        id=index_id,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        source_resource_id=str(uuid4()),
        source_version=1,
        operation_id=operation_id,
        generation=str(uuid4()),
        index_profile_digest="a" * 64,
        manifest_digest=None,
        chunk_count=0,
        state="building",
        version=1,
        created_by_actor_id=str(uuid4()),
    )
    resource = _resource(
        resource_id=index_id,
        workspace_id=workspace_id,
        kind="derived_index",
        policy_class="workspace_derived",
    )
    effect = _effect(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        operation_id=operation_id,
        resource_id=index_id,
        effect_kind="derived_index_build",
        binding_digest=canonical_digest(derived_rag._derived_effect_binding(index)),
    )
    monkeypatch.setattr(derived_rag, "_lock_index", lambda *args, **kwargs: index)
    monkeypatch.setattr(derived_rag, "lock_resource", lambda *args, **kwargs: resource)
    monkeypatch.setattr(derived_rag, "_lock_effect", lambda *args, **kwargs: effect)
    session = MagicMock()

    derived_rag.finish_derived_index(
        session,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        derived_index_id=index_id,
        operation_id=operation_id,
        target_state="unknown",
        reason_code="provider.outcome_unknown",
    )
    assert index.state == "unknown"
    assert effect.state == "unknown"
    with pytest.raises(WorkspaceDataConflict, match="terminal"):
        derived_rag.finish_derived_index(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            derived_index_id=index_id,
            operation_id=operation_id,
            target_state="ready",
            manifest_digest="b" * 64,
            chunk_count=3,
            receipt_digest="c" * 64,
        )

    unavailable = MagicMock()
    unavailable.scalar_one_or_none.return_value = None
    session.execute.return_value = unavailable
    with pytest.raises(WorkspaceDataNotFound):
        derived_rag.get_ready_derived_index(
            session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            derived_index_id=index_id,
        )


def test_ready_derived_can_be_read_then_revoked_but_never_resurrected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = str(uuid4())
    index = WorkspaceDerivedIndex(
        id=str(uuid4()),
        tenant_id=str(uuid4()),
        workspace_id=workspace_id,
        source_resource_id=str(uuid4()),
        source_version=1,
        operation_id=str(uuid4()),
        generation=str(uuid4()),
        index_profile_digest="a" * 64,
        manifest_digest="b" * 64,
        chunk_count=4,
        state="ready",
        version=2,
        created_by_actor_id=str(uuid4()),
    )
    resource = _resource(
        resource_id=index.id,
        workspace_id=workspace_id,
        kind="derived_index",
        policy_class="workspace_derived",
        state="active",
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = index
    session = MagicMock()
    session.execute.return_value = result
    monkeypatch.setattr(derived_rag, "lock_resource", lambda *args, **kwargs: resource)
    assert (
        derived_rag.get_ready_derived_index(
            session,
            tenant_id=index.tenant_id,
            workspace_id=workspace_id,
            derived_index_id=index.id,
        )
        is index
    )

    monkeypatch.setattr(derived_rag, "_lock_index", lambda *args, **kwargs: index)
    revoked = derived_rag.revoke_derived_index(
        session,
        tenant_id=index.tenant_id,
        workspace_id=workspace_id,
        derived_index_id=index.id,
    )
    assert revoked.state == "revoked"
    assert resource.state == "archived"
    index.state = "failed"
    with pytest.raises(WorkspaceDataConflict, match="only a ready"):
        derived_rag.revoke_derived_index(
            session,
            tenant_id=index.tenant_id,
            workspace_id=workspace_id,
            derived_index_id=index.id,
        )
