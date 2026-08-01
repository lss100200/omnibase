"""Focused Phase 1.6 tenant data-model and bootstrap contract tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from pgvector.sqlalchemy import Vector

from omnibase.db.tenant import Embedding, EmbeddingV2, RagDocumentIndexState
from omnibase.tenants.schema_manager import (
    list_active_tenant_schemas,
    list_tenant_schemas,
)
from omnibase.tenants.service import _initialize_tenant_schema


class TestPhase16TenantModels:
    def test_v1_embedding_shape_is_unchanged(self) -> None:
        assert Embedding.__tablename__ == "embeddings"
        assert Embedding.__table__.columns["embedding"].type.dim == 512

    def test_v2_embedding_is_independent_1024d_index(self) -> None:
        table = EmbeddingV2.__table__
        assert table.name == "embeddings_v2"
        embedding_type = table.columns["embedding"].type
        assert isinstance(embedding_type, Vector)
        assert embedding_type.dim == 1024
        assert table.columns["id"].primary_key

        indexes = {index.name: index for index in table.indexes}
        assert "embeddings_v2_document_id_idx" in indexes
        unique_index = indexes["embeddings_v2_document_chunk_uq"]
        assert unique_index.unique
        assert [column.name for column in unique_index.columns] == [
            "document_id",
            "chunk_index",
        ]

    def test_document_index_state_is_keyed_by_document_and_version(self) -> None:
        table = RagDocumentIndexState.__table__
        assert [column.name for column in table.primary_key.columns] == [
            "document_id",
            "index_version",
        ]
        assert {
            "readiness",
            "chunk_count",
            "attempt_count",
            "last_attempt_at",
            "ready_at",
            "error_detail",
            "generation",
            "created_at",
            "updated_at",
        }.issubset(table.columns.keys())


class TestConvergentTenantBootstrap:
    def test_bootstrap_contains_independent_v2_ddl_and_preserves_v1(self) -> None:
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value

        _initialize_tenant_schema(connection, "tenant_deadbeef")

        sql = "\n".join(str(call.args[0]) for call in connection.execute.call_args_list)
        assert "CREATE TABLE IF NOT EXISTS \"tenant_deadbeef\".embeddings (" in sql
        assert "ALTER TABLE \"tenant_deadbeef\".embeddings" not in sql
        assert "DROP TABLE" not in sql
        assert "CREATE TABLE IF NOT EXISTS \"tenant_deadbeef\".embeddings_v2" in sql
        assert "vector(1024)" in sql
        assert "embeddings_v2_tsv_trigger" in sql
        assert "embeddings_v2_hnsw_idx" in sql
        assert "rag_document_index_state" in sql

    def test_bootstrap_repairs_legacy_lifecycle_idempotently(self) -> None:
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value

        _initialize_tenant_schema(connection, "tenant_deadbeef")

        sql = "\n".join(str(call.args[0]) for call in connection.execute.call_args_list)
        assert "DROP CONSTRAINT IF EXISTS documents_status_check" in sql
        assert "SET status = 'queued' WHERE status = 'parsed'" in sql
        assert "'queued', 'processing'" in sql

    def test_active_schema_enumeration_excludes_inactive_tenants(self) -> None:
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value = [("tenant_active1",)]

        assert list_active_tenant_schemas(engine) == ["tenant_active1"]

        query = str(connection.execute.call_args.args[0])
        assert "WHERE is_active IS TRUE" in query

    def test_schema_enumeration_includes_inactive_retained_tenants(self) -> None:
        engine = MagicMock()
        connection = engine.connect.return_value.__enter__.return_value
        connection.execute.return_value = [("tenant_active1",), ("tenant_retained",)]

        assert list_tenant_schemas(engine) == ["tenant_active1", "tenant_retained"]

        query = str(connection.execute.call_args.args[0])
        assert "ORDER BY schema_name" in query
        assert "is_active" not in query
