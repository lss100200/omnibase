"""Tenant-schema storage for Workspace-derived RAG chunks.

This lane is deliberately separate from canonical ``embeddings`` and
``embeddings_v2``.  A caller must always bind workspace, logical derived index
and immutable generation; no client-provided physical identifier is accepted.
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnibase.db.tenant import TenantBase


class WorkspaceDerivedChunkV2(TenantBase):
    """One BGE-M3/1024d derived chunk in a Workspace-private generation."""

    __tablename__ = "workspace_derived_chunks_v2"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="workspace_derived_chunks_v2_index_check"),
        CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_derived_chunks_v2_digest_check",
        ),
        CheckConstraint(
            "char_start IS NULL OR char_start >= 0",
            name="workspace_derived_chunks_v2_char_start_check",
        ),
        CheckConstraint(
            "char_end IS NULL OR (char_start IS NOT NULL AND char_end >= char_start)",
            name="workspace_derived_chunks_v2_char_end_check",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object'",
            name="workspace_derived_chunks_v2_metadata_check",
        ),
        UniqueConstraint(
            "derived_index_id",
            "generation",
            "chunk_index",
            name="workspace_derived_chunks_v2_generation_chunk_uq",
        ),
        Index(
            "workspace_derived_chunks_v2_scope_idx",
            "workspace_id",
            "derived_index_id",
            "generation",
        ),
        Index("workspace_derived_chunks_v2_tsv_idx", "tsv", postgresql_using="gin"),
        Index(
            "workspace_derived_chunks_v2_embedding_hnsw_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    workspace_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    derived_index_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    generation: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    source_resource_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding = mapped_column(Vector(1024), nullable=True)
    tsv = mapped_column(TSVECTOR, nullable=True)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_type: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'paragraph'")
    )
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


__all__ = ["WorkspaceDerivedChunkV2"]
