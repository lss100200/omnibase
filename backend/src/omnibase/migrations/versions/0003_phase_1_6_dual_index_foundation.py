"""Add independent Phase 1.6 vector index and durable build state.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-30 16:00:00

The v1 ``embeddings`` table is deliberately untouched.  Every statement is
idempotent so previously bootstrapped, unversioned tenant schemas can converge
onto the Alembic head safely.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the v2 index and per-document index state in tenant schemas."""
    if op.get_context().config.attributes.get("migration_schema_scope") != "tenant":
        return

    statements = [
        "CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public",
        """
        CREATE TABLE IF NOT EXISTS embeddings_v2 (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            embedding vector(1024),
            tsv tsvector,
            char_start INTEGER,
            char_end INTEGER,
            chunk_type VARCHAR(20) NOT NULL DEFAULT 'paragraph',
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT embeddings_v2_chunk_index_check CHECK (chunk_index >= 0),
            CONSTRAINT embeddings_v2_document_chunk_uq UNIQUE (document_id, chunk_index)
        )
        """,
        "CREATE INDEX IF NOT EXISTS embeddings_v2_document_id_idx ON embeddings_v2 (document_id)",
        "CREATE INDEX IF NOT EXISTS embeddings_v2_tsv_idx ON embeddings_v2 USING GIN (tsv)",
        "DROP TRIGGER IF EXISTS embeddings_v2_tsv_trigger ON embeddings_v2",
        """
        CREATE TRIGGER embeddings_v2_tsv_trigger
        BEFORE INSERT OR UPDATE ON embeddings_v2
        FOR EACH ROW EXECUTE FUNCTION
            tsvector_update_trigger(tsv, 'pg_catalog.simple', content)
        """,
        """
        CREATE INDEX IF NOT EXISTS embeddings_v2_hnsw_idx
        ON embeddings_v2 USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """,
        """
        CREATE TABLE IF NOT EXISTS rag_document_index_state (
            document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
            index_version INTEGER NOT NULL,
            readiness VARCHAR(20) NOT NULL DEFAULT 'pending',
            chunk_count INTEGER NOT NULL DEFAULT 0,
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TIMESTAMPTZ,
            ready_at TIMESTAMPTZ,
            error_detail VARCHAR(2000),
            generation UUID NOT NULL DEFAULT gen_random_uuid(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (document_id, index_version),
            CONSTRAINT rag_document_index_state_version_check CHECK (index_version > 0),
            CONSTRAINT rag_document_index_state_count_check CHECK (chunk_count >= 0),
            CONSTRAINT rag_document_index_state_attempt_check CHECK (attempt_count >= 0),
            CONSTRAINT rag_document_index_state_readiness_check
                CHECK (readiness IN ('pending', 'building', 'ready', 'failed'))
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS rag_document_index_state_readiness_idx
        ON rag_document_index_state (readiness)
        """,
    ]
    for statement in statements:
        op.execute(statement)


def downgrade() -> None:
    """Remove v2-only structures while preserving the immutable v1 index."""
    if op.get_context().config.attributes.get("migration_schema_scope") != "tenant":
        return

    op.execute("DROP TABLE IF EXISTS rag_document_index_state")
    op.execute("DROP TABLE IF EXISTS embeddings_v2")
