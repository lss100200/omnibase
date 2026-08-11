"""Integration coverage for the Phase 1.6 tenant bootstrap convergence."""

# ruff: noqa: S608 -- schema names are generated UUID identifiers in this test.

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text

from omnibase.tenants.schema_manager import create_schema, drop_schema
from omnibase.tenants.service import _initialize_tenant_schema

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _upgrade_head() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _downgrade_0011() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", "downgrade", "0011"],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )


def test_unversioned_legacy_schema_converges_without_touching_v1(db_engine) -> None:
    schema = f"tenant_{uuid.uuid4().hex[:8]}"
    create_schema(db_engine, schema)

    with db_engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        conn.execute(
            text(
                f"""
                CREATE TABLE "{schema}".documents (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    filename VARCHAR(255) NOT NULL,
                    mime_type VARCHAR(100) NOT NULL,
                    size_bytes BIGINT NOT NULL,
                    status VARCHAR(20) NOT NULL DEFAULT 'pending',
                    minio_key VARCHAR(500) NOT NULL,
                    page_count INTEGER,
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CONSTRAINT documents_status_check
                        CHECK (status IN ('pending', 'parsed', 'failed', 'indexed'))
                )
                """
            )
        )
        conn.execute(
            text(
                f"""
                CREATE TABLE "{schema}".embeddings (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    document_id UUID NOT NULL REFERENCES "{schema}".documents(id) ON DELETE CASCADE,
                    chunk_index INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    embedding vector(512),
                    tsv tsvector,
                    char_start INTEGER,
                    char_end INTEGER,
                    chunk_type VARCHAR(20) NOT NULL DEFAULT 'paragraph',
                    metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
        )
        document_id = conn.execute(
            text(
                f"""
                INSERT INTO "{schema}".documents
                    (filename, mime_type, size_bytes, status, minio_key)
                VALUES ('legacy.pdf', 'application/pdf', 1, 'parsed', 'legacy')
                RETURNING id
                """
            )
        ).scalar_one()
        chunk_id = conn.execute(
            text(
                f"""
                INSERT INTO "{schema}".embeddings
                    (document_id, chunk_index, content)
                VALUES (:document_id, 0, 'stable v1 chunk')
                RETURNING id
                """
            ),
            {"document_id": document_id},
        ).scalar_one()

    try:
        with db_engine.begin() as conn:
            _initialize_tenant_schema(conn, schema)
        with db_engine.begin() as conn:
            _initialize_tenant_schema(conn, schema)

        with db_engine.connect() as conn:
            assert (
                conn.execute(
                    text(f'SELECT status FROM "{schema}".documents WHERE id = :id'),
                    {"id": document_id},
                ).scalar_one()
                == "queued"
            )
            assert (
                conn.execute(
                    text(f'SELECT id FROM "{schema}".embeddings WHERE id = :id'),
                    {"id": chunk_id},
                ).scalar_one()
                == chunk_id
            )

            vector_dimension = conn.execute(
                text(
                    """
                    SELECT format_type(a.atttypid, a.atttypmod)
                    FROM pg_attribute a
                    JOIN pg_class c ON c.oid = a.attrelid
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = :schema AND c.relname = 'embeddings_v2'
                      AND a.attname = 'embedding'
                    """
                ),
                {"schema": schema},
            ).scalar_one()
            assert vector_dimension == "vector(1024)"

            tables = set(
                conn.execute(
                    text(
                        """
                        SELECT table_name FROM information_schema.tables
                        WHERE table_schema = :schema
                        """
                    ),
                    {"schema": schema},
                ).scalars()
            )
            assert {"embeddings", "embeddings_v2", "rag_document_index_state"}.issubset(tables)
    finally:
        drop_schema(
            db_engine,
            schema,
            cascade=True,
            expected_schema_name=schema,
        )


def test_registered_tenant_alembic_upgrade_head_is_idempotent(
    db_engine,
    run_owned_resources,
) -> None:
    tenant_id = uuid.uuid4()
    schema = f"tenant_{tenant_id.hex[:8]}"

    with db_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id, name, slug, schema_name, is_default, is_active) "
                "VALUES (:id, :name, :slug, :schema, FALSE, FALSE)"
            ),
            {
                "id": tenant_id,
                "name": "Alembic retained tenant",
                "slug": f"alembic-{tenant_id.hex[:12]}",
                "schema": schema,
            },
        )
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
        conn.execute(
            text(
                f'CREATE TABLE "{schema}".documents ('
                "id UUID PRIMARY KEY DEFAULT gen_random_uuid(), "
                "filename VARCHAR(255) NOT NULL, "
                "mime_type VARCHAR(100) NOT NULL, "
                "size_bytes BIGINT NOT NULL, "
                "status VARCHAR(20) NOT NULL DEFAULT 'pending', "
                "minio_key VARCHAR(500) NOT NULL, "
                "metadata JSONB NOT NULL DEFAULT '{}'::jsonb, "
                "created_at TIMESTAMPTZ NOT NULL DEFAULT now(), "
                "updated_at TIMESTAMPTZ NOT NULL DEFAULT now())"
            )
        )
    run_owned_resources.add(str(tenant_id), schema)

    _upgrade_head()
    _upgrade_head()

    with db_engine.connect() as conn:
        tenant_revision = conn.execute(
            text(f'SELECT version_num FROM "{schema}".alembic_version')
        ).scalar_one()
        assert tenant_revision == "0013"

        tables = set(
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema"
                ),
                {"schema": schema},
            ).scalars()
        )
        assert {
            "embeddings_v2",
            "rag_document_index_state",
            "user_profiles",
            "model_provider_credentials",
        }.issubset(tables)


def test_0012_populated_tenant_blocks_global_downgrade_before_any_head_moves(
    db_engine,
    run_owned_resources,
) -> None:
    tenant_ids = (uuid.uuid4(), uuid.uuid4())
    schemas = tuple(f"tenant_{tenant_id.hex[:8]}" for tenant_id in tenant_ids)
    with db_engine.begin() as conn:
        for tenant_id, schema in zip(tenant_ids, schemas, strict=True):
            conn.execute(
                text(
                    "INSERT INTO omnibase_meta.tenants "
                    "(id, name, slug, schema_name, is_default, is_active) "
                    "VALUES (:id, :name, :slug, :schema, FALSE, FALSE)"
                ),
                {
                    "id": tenant_id,
                    "name": "0012 downgrade guard tenant",
                    "slug": f"downgrade-{tenant_id.hex[:12]}",
                    "schema": schema,
                },
            )
            conn.execute(text(f'CREATE SCHEMA "{schema}"'))
            _initialize_tenant_schema(conn, schema)
            run_owned_resources.add(str(tenant_id), schema)

        user_id = uuid.uuid4()
        conn.execute(
            text(
                f'INSERT INTO "{schemas[1]}".users '
                "(id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, :email, 'not-a-real-hash', TRUE, TRUE)"
            ),
            {"id": user_id, "email": f"downgrade-{user_id.hex[:12]}@example.invalid"},
        )
        conn.execute(
            text(
                f'INSERT INTO "{schemas[1]}".user_profiles '
                "(user_id, display_name) VALUES (:user_id, 'Downgrade guard')"
            ),
            {"user_id": user_id},
        )

    _upgrade_head()
    downgrade = _downgrade_0011()
    assert downgrade.returncode != 0
    assert "0012 downgrade refused:" in (downgrade.stdout + downgrade.stderr)

    with db_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT version_num FROM omnibase_meta.alembic_version")).scalar_one()
            == "0013"
        )
        for schema in schemas:
            assert (
                conn.execute(
                    text(f'SELECT version_num FROM "{schema}".alembic_version')
                ).scalar_one()
                == "0013"
            )
