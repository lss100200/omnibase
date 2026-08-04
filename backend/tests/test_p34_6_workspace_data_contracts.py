"""Strict P34.6 Workspace-data DTO and ORM boundary tests."""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index

from omnibase.control_plane.models import OperationRecord, ResourceLineage
from omnibase.controlled_data.models import DataTableBinding
from omnibase.db.models import GLOBAL_SCHEMA
from omnibase.workspace_data.contracts import (
    ArtifactStageRequest,
    PublicationRequest,
    SnapshotInventoryRequest,
)
from omnibase.workspace_data.models import (
    WorkspaceArtifact,
    WorkspaceDataEffect,
    WorkspaceDerivedIndex,
    WorkspacePublication,
    WorkspaceSnapshotItem,
)
from omnibase.workspace_data.tenant_models import WorkspaceDerivedChunkV2
from omnibase.workspaces.models import WorkspaceSnapshot


def test_workspace_data_contracts_reject_physical_and_ambiguous_fields() -> None:
    with pytest.raises(ValidationError):
        ArtifactStageRequest(
            workspace_id=uuid4(),
            expected_workspace_generation=1,
            display_name="artifact",
            content_digest="a" * 64,
            size_bytes=1,
            media_type="text/plain",
            idempotency_key="idem",
            request_id="req-1",
            physical_locator={"bucket": "forbidden"},
        )
    with pytest.raises(ValidationError):
        PublicationRequest(
            source_workspace_id=uuid4(),
            source_resource_id=uuid4(),
            source_version=1,
            source_manifest_digest="b" * 64,
            target_scope="tenant_shared",
            target_workspace_id=uuid4(),
            display_name="published",
            idempotency_key="idem",
            request_id="req-2",
        )


def test_snapshot_inventory_is_bounded_and_resource_unique() -> None:
    resource_id = uuid4()
    payload = {
        "workspace_id": uuid4(),
        "expected_workspace_generation": 1,
        "items": [
            {
                "source_resource_id": resource_id,
                "source_version": 1,
                "item_kind": "artifact",
                "content_digest": "c" * 64,
                "payload_artifact_id": uuid4(),
                "size_bytes": 4,
            },
            {
                "source_resource_id": resource_id,
                "source_version": 1,
                "item_kind": "artifact",
                "content_digest": "d" * 64,
                "payload_artifact_id": uuid4(),
                "size_bytes": 4,
            },
        ],
        "idempotency_key": "idem",
        "request_id": "req-3",
    }
    with pytest.raises(ValidationError, match="resource IDs must be unique"):
        SnapshotInventoryRequest.model_validate(payload)


def test_workspace_data_models_are_separate_logical_ledgers() -> None:
    for model in (
        WorkspaceArtifact,
        WorkspaceDerivedIndex,
        WorkspacePublication,
        WorkspaceSnapshotItem,
        WorkspaceDataEffect,
    ):
        assert model.__table__.schema == GLOBAL_SCHEMA
        forbidden = {
            "physical_locator",
            "object_key",
            "bucket",
            "database_url",
            "credential",
            "token",
        }
        assert forbidden.isdisjoint(model.__table__.columns.keys())
    assert WorkspaceDerivedChunkV2.__table__.schema is None
    assert WorkspaceDerivedChunkV2.__tablename__ == "workspace_derived_chunks_v2"
    assert WorkspaceDerivedChunkV2.__tablename__ not in {"embeddings", "embeddings_v2"}
    indexes = {index.name: index for index in WorkspaceDerivedChunkV2.__table__.indexes}
    assert isinstance(indexes["workspace_derived_chunks_v2_embedding_hnsw_idx"], Index)
    assert (
        indexes["workspace_derived_chunks_v2_embedding_hnsw_idx"].dialect_options["postgresql"][
            "using"
        ]
        == "hnsw"
    )


def test_existing_tenant_composites_and_snapshot_states_are_hardened() -> None:
    operation_uniques = {constraint.name for constraint in OperationRecord.__table__.constraints}
    assert "operations_id_tenant_uq" in operation_uniques

    lineage_fks = {
        constraint.name: tuple(element.parent.name for element in constraint.elements)
        for constraint in ResourceLineage.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert lineage_fks["resource_lineage_operation_tenant_fk"] == (
        "created_by_operation_id",
        "tenant_id",
    )
    binding_fks = {
        constraint.name: tuple(element.parent.name for element in constraint.elements)
        for constraint in DataTableBinding.__table__.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert binding_fks["data_table_bindings_resource_tenant_fk"] == (
        "resource_id",
        "tenant_id",
    )
    assert binding_fks["data_table_bindings_workspace_tenant_fk"] == (
        "workspace_id",
        "tenant_id",
    )
    snapshot_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in WorkspaceSnapshot.__table__.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert "building" in snapshot_checks["workspace_snapshots_state_check"]
    assert "failed" in snapshot_checks["workspace_snapshots_state_check"]
