"""Tenant service - business logic for tenant lifecycle.

Responsibilities:
- Create a new tenant: insert row + CREATE SCHEMA + apply tenant migrations
- Look up tenant by slug / id
- Deactivate tenant (soft delete; schema preserved)
- (Future) Hard delete: drop schema + delete row

This is the orchestration layer between the router (HTTP) and the
schema_manager (low-level DDL).
"""

from __future__ import annotations

import re
import secrets

from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnibase.core.db import get_engine, get_session_factory
from omnibase.core.logging import get_logger
from omnibase.db.models import Tenant
from omnibase.tenants.migrations import upgrade_new_tenant_schema
from omnibase.tenants.schema_manager import (
    create_schema,
    list_tenant_schemas,
    make_schema_name,
)

log = get_logger(__name__)


# -----------------------------------------------------------
# Slug validation
# -----------------------------------------------------------
# URL-safe slugs: lowercase letters, digits, hyphens; 3-50 chars; must start with a letter
_SLUG_PATTERN = re.compile(r"^[a-z][a-z0-9-]{2,49}$")


class TenantError(Exception):
    """Base class for tenant-related business errors."""


class TenantAlreadyExists(TenantError):
    """Raised when a slug or schema name collides with an existing tenant."""

    def __init__(self, field: str, value: str) -> None:
        super().__init__(f"Tenant with {field}={value!r} already exists")
        self.field = field
        self.value = value


class TenantNotFound(TenantError):
    """Raised when a tenant lookup fails."""


class InvalidTenantSlug(TenantError):
    """Raised when a slug fails validation."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"Invalid slug {slug!r}. Must match {_SLUG_PATTERN.pattern}")
        self.slug = slug


def validate_slug(slug: str) -> None:
    """Raise InvalidTenantSlug if the slug is malformed."""
    if not _SLUG_PATTERN.match(slug):
        raise InvalidTenantSlug(slug)


def _generate_unique_slug(name: str) -> str:
    """Generate a URL-safe slug from a display name + random suffix.

    Used when the user does not specify a slug explicitly.
    Example: "Acme Corp" -> "acme-corp-a1b2"
    """
    # Normalize: lowercase, replace non-alnum with hyphen, collapse repeats
    base = re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"))
    if not base or not base[0].isalpha():
        base = "tenant"
    # Truncate to leave room for suffix; 30 chars base + "-" + 4 random = 35
    base = base[:30]
    suffix = secrets.token_hex(2)  # 4 hex chars
    return f"{base}-{suffix}"


# -----------------------------------------------------------
# CRUD operations
# -----------------------------------------------------------
def create_tenant(
    *,
    name: str,
    slug: str | None = None,
    is_default: bool = False,
    session: Session | None = None,
) -> Tenant:
    """Create a new tenant and its backing PostgreSQL schema.

    Args:
        name: Human-readable tenant name.
        slug: URL-safe slug. If None, one is generated from `name`.
        is_default: True if this is the auto-created tenant for a new user.
        session: Optional existing session (for transactional callers).

    Returns:
        The newly created Tenant.

    Raises:
        InvalidTenantSlug, TenantAlreadyExists, SchemaError

    When ``session`` is supplied, the caller owns commit and rollback. The
    tenant row and schema DDL still share that session's transaction.
    """
    slug = slug or _generate_unique_slug(name)
    validate_slug(slug)

    owns_session = session is None
    if session is None:
        factory = get_session_factory()
        session = factory()

    try:
        # 1. Insert tenant row with a temporary schema_name placeholder.
        #    We need the tenant.id to derive schema_name, so use a two-phase insert.
        #    Phase 0 simplification: derive schema_name from a fresh UUID generated client-side.
        import uuid

        tenant_id = str(uuid.uuid4())
        schema_name = make_schema_name(tenant_id)

        tenant = Tenant(
            id=tenant_id,
            name=name[:100],
            schema_name=schema_name,
            slug=slug,
            is_default=is_default,
            is_active=True,
        )
        session.add(tenant)

        # Flush the metadata row first so uniqueness failures happen before DDL.
        try:
            session.flush()
        except IntegrityError as exc:
            if owns_session:
                session.rollback()
            msg = str(exc.orig) if exc.orig else str(exc)
            if "slug" in msg:
                raise TenantAlreadyExists("slug", slug) from exc
            if "schema_name" in msg:
                log.warning("tenant.schema_collision", slug=slug, schema=schema_name)
                raise TenantAlreadyExists("schema_name", schema_name) from exc
            raise TenantError(f"Integrity error: {msg}") from exc

        # PostgreSQL transactional DDL keeps the metadata row, schema, and all
        # bootstrap tables on the same connection and transaction. CREATE
        # SCHEMA intentionally omits IF NOT EXISTS so an orphan is never reused.
        connection = session.connection()
        create_schema(connection, schema_name, if_not_exists=False)
        _initialize_tenant_schema(connection, schema_name)
        upgrade_new_tenant_schema(connection, schema_name)

        if owns_session:
            session.commit()
        log.info(
            "tenant.created",
            tenant_id=tenant.id,
            slug=tenant.slug,
            schema=tenant.schema_name,
            is_default=is_default,
        )
        return tenant

    finally:
        if owns_session:
            session.close()


def get_tenant_by_slug(slug: str, session: Session | None = None) -> Tenant:
    """Look up an active tenant by slug."""
    owns_session = session is None
    if session is None:
        factory = get_session_factory()
        session = factory()
    try:
        stmt = select(Tenant).where(Tenant.slug == slug, Tenant.is_active.is_(True))
        tenant = session.execute(stmt).scalar_one_or_none()
        if tenant is None:
            raise TenantNotFound(f"No active tenant with slug={slug!r}")
        return tenant
    finally:
        if owns_session:
            session.close()


def get_tenant_by_id(tenant_id: str, session: Session | None = None) -> Tenant:
    """Look up an active tenant by id."""
    owns_session = session is None
    if session is None:
        factory = get_session_factory()
        session = factory()
    try:
        stmt = select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
        tenant = session.execute(stmt).scalar_one_or_none()
        if tenant is None:
            raise TenantNotFound(f"No active tenant with id={tenant_id!r}")
        return tenant
    finally:
        if owns_session:
            session.close()


def deactivate_tenant(tenant_id: str, session: Session | None = None) -> None:
    """Soft-delete a tenant: marks is_active=False, preserves schema."""
    owns_session = session is None
    if session is None:
        factory = get_session_factory()
        session = factory()
    try:
        tenant = get_tenant_by_id(tenant_id, session=session)
        tenant.is_active = False
        session.commit()
        log.info("tenant.deactivated", tenant_id=tenant_id, schema=tenant.schema_name)
    finally:
        if owns_session:
            session.close()


def _initialize_tenant_schema(connection: Connection, schema_name: str) -> None:
    """Apply Phase 0 business tables to a freshly created tenant schema.

    In Phase 0 we hand-write the DDL here; B3 will replace this with a proper
    Alembic multi-schema migration that loops over all tenant schemas.
    """
    from sqlalchemy import text

    from omnibase.tenants.schema_manager import validate_schema_name

    validate_schema_name(schema_name)
    statements = [
        # Users table (per-tenant)
        f"""
        CREATE TABLE IF NOT EXISTS "{schema_name}".users (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email           VARCHAR(255) NOT NULL UNIQUE,
            password_hash   VARCHAR(255) NOT NULL,
            is_tenant_admin BOOLEAN NOT NULL DEFAULT FALSE,
            is_active       BOOLEAN NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        # Documents table (per-tenant) - Phase 0 metadata only
        f"""
        CREATE TABLE IF NOT EXISTS "{schema_name}".documents (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            filename        VARCHAR(255) NOT NULL,
            mime_type       VARCHAR(100) NOT NULL,
            size_bytes      BIGINT NOT NULL,
            status          VARCHAR(20) NOT NULL DEFAULT 'pending',
            minio_key       VARCHAR(500) NOT NULL,
            page_count      INTEGER,
            error_detail    VARCHAR(1000),
            metadata        JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        f'ALTER TABLE "{schema_name}".documents ADD COLUMN IF NOT EXISTS error_detail VARCHAR(1000)',
        # Documents status lifecycle constraint.  Drop/recreate makes this a
        # convergent repair for legacy schemas that still allow ``parsed``.
        f'ALTER TABLE "{schema_name}".documents DROP CONSTRAINT IF EXISTS documents_status_check',
        f"UPDATE \"{schema_name}\".documents SET status = 'queued' WHERE status = 'parsed'",  # noqa: S608 - validated identifier
        f"""
        ALTER TABLE "{schema_name}".documents
            ADD CONSTRAINT documents_status_check
            CHECK (status IN ('pending', 'queued', 'processing', 'indexed', 'failed'))
        """,
        # Index for documents list (sorted by created_at desc)
        f"CREATE INDEX IF NOT EXISTS documents_created_at_idx "
        f'ON "{schema_name}".documents (created_at DESC)',
        # Index for documents status filter
        f"CREATE INDEX IF NOT EXISTS documents_status_idx "
        f'ON "{schema_name}".documents (status)',
        # pgvector extension (idempotent - shared across schemas)
        "CREATE EXTENSION IF NOT EXISTS vector",
        # Embeddings table (Phase 1 AI RAG: 512-dim + BM25 + citation data)
        f"""
        CREATE TABLE IF NOT EXISTS "{schema_name}".embeddings (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id     UUID NOT NULL REFERENCES "{schema_name}".documents(id) ON DELETE CASCADE,
            chunk_index     INTEGER NOT NULL,
            content         TEXT NOT NULL,
            embedding       vector(512),
            tsv             tsvector,
            char_start      INTEGER,
            char_end        INTEGER,
            chunk_type      VARCHAR(20) NOT NULL DEFAULT 'paragraph',
            metadata        JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """,
        # BM25 full-text search index (GIN on tsvector)
        f'CREATE INDEX IF NOT EXISTS embeddings_tsv_idx ON "{schema_name}".embeddings USING GIN (tsv)',
        # Auto-populate tsvector from content on insert/update
        # Note: PostgreSQL doesn't support CREATE TRIGGER IF NOT EXISTS,
        # so we DROP IF EXISTS first, then CREATE.
        f'DROP TRIGGER IF EXISTS embeddings_tsv_trigger ON "{schema_name}".embeddings',
        f"""
        CREATE TRIGGER embeddings_tsv_trigger
        BEFORE INSERT OR UPDATE ON "{schema_name}".embeddings
        FOR EACH ROW EXECUTE FUNCTION tsvector_update_trigger(tsv, 'pg_catalog.simple', content)
        """,
        # HNSW vector index for fast ANN retrieval (L1 coarse recall)
        f"""
        CREATE INDEX IF NOT EXISTS embeddings_hnsw_idx
        ON "{schema_name}".embeddings USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """,
        # Independent Phase 1.6 index.  IDs are explicit UUID primary keys so a
        # rebuild can preserve the stable v1 chunk identity.
        f"""
        CREATE TABLE IF NOT EXISTS "{schema_name}".embeddings_v2 (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id     UUID NOT NULL REFERENCES "{schema_name}".documents(id) ON DELETE CASCADE,
            chunk_index     INTEGER NOT NULL,
            content         TEXT NOT NULL,
            embedding       vector(1024),
            tsv             tsvector,
            char_start      INTEGER,
            char_end        INTEGER,
            chunk_type      VARCHAR(20) NOT NULL DEFAULT 'paragraph',
            metadata        JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT embeddings_v2_chunk_index_check CHECK (chunk_index >= 0),
            CONSTRAINT embeddings_v2_document_chunk_uq UNIQUE (document_id, chunk_index)
        )
        """,
        f'CREATE INDEX IF NOT EXISTS embeddings_v2_document_id_idx ON "{schema_name}".embeddings_v2 (document_id)',
        f'CREATE INDEX IF NOT EXISTS embeddings_v2_tsv_idx ON "{schema_name}".embeddings_v2 USING GIN (tsv)',
        f'DROP TRIGGER IF EXISTS embeddings_v2_tsv_trigger ON "{schema_name}".embeddings_v2',
        f"""
        CREATE TRIGGER embeddings_v2_tsv_trigger
        BEFORE INSERT OR UPDATE ON "{schema_name}".embeddings_v2
        FOR EACH ROW EXECUTE FUNCTION tsvector_update_trigger(tsv, 'pg_catalog.simple', content)
        """,
        f"""
        CREATE INDEX IF NOT EXISTS embeddings_v2_hnsw_idx
        ON "{schema_name}".embeddings_v2 USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """,
        # Durable document+version state for rebuild readiness and retries.
        f"""
        CREATE TABLE IF NOT EXISTS "{schema_name}".rag_document_index_state (
            document_id     UUID NOT NULL REFERENCES "{schema_name}".documents(id) ON DELETE CASCADE,
            index_version   INTEGER NOT NULL,
            readiness       VARCHAR(20) NOT NULL DEFAULT 'pending',
            chunk_count     INTEGER NOT NULL DEFAULT 0,
            attempt_count   INTEGER NOT NULL DEFAULT 0,
            last_attempt_at TIMESTAMPTZ,
            ready_at        TIMESTAMPTZ,
            error_detail    VARCHAR(2000),
            generation      UUID NOT NULL DEFAULT gen_random_uuid(),
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (document_id, index_version),
            CONSTRAINT rag_document_index_state_version_check CHECK (index_version > 0),
            CONSTRAINT rag_document_index_state_count_check CHECK (chunk_count >= 0),
            CONSTRAINT rag_document_index_state_attempt_check CHECK (attempt_count >= 0),
            CONSTRAINT rag_document_index_state_readiness_check
                CHECK (readiness IN ('pending', 'building', 'ready', 'failed'))
        )
        """,
        f"""
        CREATE INDEX IF NOT EXISTS rag_document_index_state_readiness_idx
        ON "{schema_name}".rag_document_index_state (readiness)
        """,
        # User-owned workbench preferences (migration 0012 convergence for
        # newly registered tenants before the next operator migration sweep).
        f"""
        CREATE TABLE IF NOT EXISTS "{schema_name}".user_profiles (
            user_id UUID PRIMARY KEY REFERENCES "{schema_name}".users(id) ON DELETE CASCADE,
            display_name VARCHAR(120) NOT NULL,
            locale VARCHAR(16) NOT NULL DEFAULT 'zh-CN',
            theme VARCHAR(16) NOT NULL DEFAULT 'system',
            assistant_name VARCHAR(80) NOT NULL DEFAULT 'Omni',
            assistant_tone VARCHAR(16) NOT NULL DEFAULT 'balanced',
            assistant_instructions TEXT NOT NULL DEFAULT '',
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT user_profiles_theme_check CHECK (theme IN ('system', 'light', 'dark')),
            CONSTRAINT user_profiles_assistant_tone_check CHECK (assistant_tone IN ('concise', 'balanced', 'detailed')),
            CONSTRAINT user_profiles_version_check CHECK (version >= 1),
            CONSTRAINT user_profiles_instructions_length_check CHECK (char_length(assistant_instructions) <= 4000)
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS "{schema_name}".model_provider_credentials (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id UUID NOT NULL REFERENCES "{schema_name}".users(id) ON DELETE CASCADE,
            display_name VARCHAR(120) NOT NULL,
            provider_id VARCHAR(64) NOT NULL,
            base_url VARCHAR(500) NOT NULL,
            model_id VARCHAR(200) NOT NULL,
            encrypted_api_key BYTEA,
            key_nonce BYTEA,
            key_version INTEGER NOT NULL DEFAULT 1,
            key_fingerprint VARCHAR(24),
            is_default BOOLEAN NOT NULL DEFAULT FALSE,
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            version INTEGER NOT NULL DEFAULT 1,
            last_test_status VARCHAR(32),
            last_test_latency_ms INTEGER,
            last_tested_at TIMESTAMPTZ,
            revoked_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT model_provider_credentials_version_check CHECK (version >= 1),
            CONSTRAINT model_provider_credentials_key_version_check CHECK (key_version >= 1),
            CONSTRAINT model_provider_credentials_test_status_check CHECK (last_test_status IS NULL OR last_test_status IN ('passed', 'auth_failed', 'timeout', 'identity_mismatch', 'unreachable', 'failed')),
            CONSTRAINT model_provider_credentials_latency_check CHECK (last_test_latency_ms IS NULL OR last_test_latency_ms >= 0),
            CONSTRAINT model_provider_credentials_active_revoked_check CHECK ((is_active AND revoked_at IS NULL) OR (NOT is_active))
        )
        """,
        f'CREATE INDEX IF NOT EXISTS model_provider_credentials_user_idx ON "{schema_name}".model_provider_credentials (user_id, created_at)',
        f'CREATE UNIQUE INDEX IF NOT EXISTS model_provider_credentials_one_default_uq ON "{schema_name}".model_provider_credentials (user_id) WHERE is_active AND is_default AND revoked_at IS NULL',
    ]

    # IMPORTANT: pgvector extension must exist before the embeddings table can
    # use the vector type. Keep all bootstrap DDL on the caller's transaction.
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
    connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", omnibase_meta, public'))
    for stmt in statements:
        connection.execute(text(stmt))

    log.info(
        "tenant.schema.initialized",
        schema=schema_name,
        tables=[
            "users",
            "documents",
            "embeddings",
            "embeddings_v2",
            "rag_document_index_state",
            "user_profiles",
            "model_provider_credentials",
        ],
    )


def get_all_active_tenant_schemas() -> list[str]:
    """Return retained tenant schemas for backward-compatible migration tooling.

    The historical function name is kept for CLI compatibility; inactive
    tenants are intentionally included because soft deletion preserves data.
    """
    return list_tenant_schemas(get_engine())


__all__ = [
    "InvalidTenantSlug",
    "TenantAlreadyExists",
    "TenantError",
    "TenantNotFound",
    "create_tenant",
    "deactivate_tenant",
    "get_all_active_tenant_schemas",
    "get_tenant_by_id",
    "get_tenant_by_slug",
    "validate_slug",
]
