"""Per-tenant ORM models.

These models bind to TENANT_METADATA (schema=None) so that the schema is
resolved dynamically at runtime via search_path switching (see
omnibase.tenants.schema_manager.set_search_path).

Tables:
- users: per-tenant user accounts (email/password/tenant_admin)
- documents: file metadata and async ingestion lifecycle
- embeddings: immutable Phase 1 v1 index (512 dimensions)
- embeddings_v2: Phase 1.6 rebuildable 1024-dimensional index
- rag_document_index_state: durable per-document/per-version build state
"""

from __future__ import annotations

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from omnibase.db.models import TENANT_METADATA


# -----------------------------------------------------------
# Tenant-scoped declarative base (independent from global Base)
# -----------------------------------------------------------
class TenantBase(DeclarativeBase):
    """Declarative base for per-tenant tables.

    Uses TENANT_METADATA (schema=None) so unqualified CREATE TABLE picks up
    the active search_path set by set_search_path().
    """

    metadata = TENANT_METADATA


# Shared server defaults (avoids dict-splat which confuses mypy with strict
# checking of mapped_column kwargs)
_PK_SERVER_DEFAULT = text("gen_random_uuid()")
_CREATED_SERVER_DEFAULT = func.now()


class User(TenantBase):
    """A user account, scoped to a single tenant schema.

    Email uniqueness is per-tenant (not global): the same email can exist
    in different tenant schemas. This is intentional - supports multi-org.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=_PK_SERVER_DEFAULT
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_tenant_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=_CREATED_SERVER_DEFAULT
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=_CREATED_SERVER_DEFAULT,
        onupdate=_CREATED_SERVER_DEFAULT,
    )

    def __repr__(self) -> str:
        return f"<User email={self.email!r}>"


class UserProfile(TenantBase):
    """User-owned workbench presentation and assistant preferences."""

    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint(
            "theme IN ('system', 'light', 'dark')",
            name="user_profiles_theme_check",
        ),
        CheckConstraint(
            "assistant_tone IN ('concise', 'balanced', 'detailed')",
            name="user_profiles_assistant_tone_check",
        ),
        CheckConstraint("version >= 1", name="user_profiles_version_check"),
        CheckConstraint(
            "char_length(assistant_instructions) <= 4000",
            name="user_profiles_instructions_length_check",
        ),
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="zh-CN")
    theme: Mapped[str] = mapped_column(String(16), nullable=False, default="system")
    assistant_name: Mapped[str] = mapped_column(String(80), nullable=False, default="Omni")
    assistant_tone: Mapped[str] = mapped_column(String(16), nullable=False, default="balanced")
    assistant_instructions: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CREATED_SERVER_DEFAULT
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=_CREATED_SERVER_DEFAULT,
        onupdate=_CREATED_SERVER_DEFAULT,
    )


class ModelProviderCredential(TenantBase):
    """Encrypted, user-owned OpenAI-compatible provider configuration."""

    __tablename__ = "model_provider_credentials"
    __table_args__ = (
        CheckConstraint("version >= 1", name="model_provider_credentials_version_check"),
        CheckConstraint("key_version >= 1", name="model_provider_credentials_key_version_check"),
        CheckConstraint(
            "last_test_status IS NULL OR last_test_status IN "
            "('passed', 'auth_failed', 'timeout', 'identity_mismatch', 'unreachable', 'failed')",
            name="model_provider_credentials_test_status_check",
        ),
        CheckConstraint(
            "last_test_latency_ms IS NULL OR last_test_latency_ms >= 0",
            name="model_provider_credentials_latency_check",
        ),
        CheckConstraint(
            "(is_active AND revoked_at IS NULL) OR (NOT is_active)",
            name="model_provider_credentials_active_revoked_check",
        ),
        Index("model_provider_credentials_user_idx", "user_id", "created_at"),
        Index(
            "model_provider_credentials_one_default_uq",
            "user_id",
            unique=True,
            postgresql_where=text("is_active AND is_default AND revoked_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=_PK_SERVER_DEFAULT
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model_id: Mapped[str] = mapped_column(String(200), nullable=False)
    encrypted_api_key: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_nonce: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    key_fingerprint: Mapped[str | None] = mapped_column(String(24), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    last_test_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_test_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=_CREATED_SERVER_DEFAULT
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=_CREATED_SERVER_DEFAULT,
        onupdate=_CREATED_SERVER_DEFAULT,
    )


class Document(TenantBase):
    """A user-uploaded file's metadata.

    Phase 0 stores only metadata + parsing status; full content extraction
    and chunking arrive in Phase 1. The actual file bytes live in MinIO
    (keyed by minio_key).
    """

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'queued', 'processing', 'indexed', 'failed')",
            name="documents_status_check",
        ),
        Index("documents_created_at_idx", "created_at"),
        Index("documents_status_idx", "status"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=_PK_SERVER_DEFAULT
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    minio_key: Mapped[str] = mapped_column(String(500), nullable=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(1000), nullable=True, default=None)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=_CREATED_SERVER_DEFAULT
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=_CREATED_SERVER_DEFAULT,
        onupdate=_CREATED_SERVER_DEFAULT,
    )

    @property
    def meta(self) -> dict:
        """Alias for metadata_ (avoids conflict with SQLAlchemy's Base.metadata)."""
        return self.metadata_ or {}

    def __repr__(self) -> str:
        return f"<Document filename={self.filename!r} status={self.status!r}>"


class Embedding(TenantBase):
    """A text chunk + its vector embedding for AI RAG retrieval.

    AI RAG architecture (Phase 1):
    - embedding (512-dim): bge-small-zh-v1.5, used for L1 coarse recall via HNSW
    - tsv: PostgreSQL tsvector for BM25 keyword search (hybrid retrieval)
    - char_start/char_end: character offsets for citation backlinks
    - chunk_type: paragraph/code/heading — enables type-aware retrieval

    The reranker (bge-reranker-v2-m3) runs in application code, not stored here.
    """

    __tablename__ = "embeddings"
    __table_args__ = (
        Index("embeddings_document_id_idx", "document_id"),
        Index("embeddings_chunk_index_idx", "document_id", "chunk_index"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=_PK_SERVER_DEFAULT
    )
    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 512 dims matches bge-small-zh-v1.5 (L0/L1 coarse recall, CPU-friendly)
    embedding = mapped_column(Vector(512), nullable=True)
    # BM25 keyword search vector (auto-generated from content)
    tsv = mapped_column(
        TSVECTOR,
        nullable=True,
    )
    # Citation backlink data
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_type: Mapped[str] = mapped_column(String(20), nullable=False, default="paragraph")
    # Per-chunk metadata (page number, section heading, language, etc.)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=_CREATED_SERVER_DEFAULT
    )


class EmbeddingV2(TenantBase):
    """Phase 1.6 text chunks backed by the independent 1024d index.

    ``id`` remains a first-class UUID so rebuild code can carry the v1 chunk ID
    forward unchanged.  The v1 ``embeddings`` table is intentionally unrelated
    and remains available throughout migration and rollback.
    """

    __tablename__ = "embeddings_v2"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="embeddings_v2_chunk_index_check"),
        Index("embeddings_v2_document_id_idx", "document_id"),
        Index(
            "embeddings_v2_document_chunk_uq",
            "document_id",
            "chunk_index",
            unique=True,
        ),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=_PK_SERVER_DEFAULT
    )
    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(1024), nullable=True)
    tsv = mapped_column(TSVECTOR, nullable=True)
    char_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    char_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunk_type: Mapped[str] = mapped_column(String(20), nullable=False, default="paragraph")
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=_CREATED_SERVER_DEFAULT
    )


class RagDocumentIndexState(TenantBase):
    """Durable progress/readiness state for a document index generation."""

    __tablename__ = "rag_document_index_state"
    __table_args__ = (
        CheckConstraint("index_version > 0", name="rag_document_index_state_version_check"),
        CheckConstraint("chunk_count >= 0", name="rag_document_index_state_count_check"),
        CheckConstraint("attempt_count >= 0", name="rag_document_index_state_attempt_check"),
        CheckConstraint(
            "readiness IN ('pending', 'building', 'ready', 'failed')",
            name="rag_document_index_state_readiness_check",
        ),
        Index("rag_document_index_state_readiness_idx", "readiness"),
    )

    document_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    index_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    readiness: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default=text("'pending'")
    )
    chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    last_attempt_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_detail: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    generation: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, server_default=_PK_SERVER_DEFAULT
    )
    created_at: Mapped[datetime] = mapped_column(
        nullable=False, server_default=_CREATED_SERVER_DEFAULT
    )
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False,
        server_default=_CREATED_SERVER_DEFAULT,
        onupdate=_CREATED_SERVER_DEFAULT,
    )


__all__ = [
    "Document",
    "Embedding",
    "EmbeddingV2",
    "ModelProviderCredential",
    "RagDocumentIndexState",
    "TenantBase",
    "User",
    "UserProfile",
]
