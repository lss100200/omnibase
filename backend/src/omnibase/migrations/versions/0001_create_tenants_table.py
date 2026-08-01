"""create omnibase_meta.tenants table

Revision ID: 0001
Revises:
Create Date: 2026-07-29 19:30:00

This is the initial global migration. It creates the `tenants` table in the
`omnibase_meta` schema. Tenant-scoped tables (users, documents, embeddings)
are created via the runtime schema bootstrap (omnibase.tenants.service.
_initialize_tenant_schema), since each tenant lives in its own PostgreSQL
schema and Alembic iterates them dynamically.

NOTE: This migration does NOT create tenant schemas - they are created on
demand when a tenant is registered (see omnibase.tenants.service.create_tenant).
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create omnibase_meta schema + tenants table + indices."""
    # The same revision graph is executed once globally and once per tenant.
    # Tenant schemas must record this revision without replaying global DDL.
    if op.get_context().config.attributes.get("migration_schema_scope") == "tenant":
        return

    # Defensive: ensure schema exists (env.py also does this, but be idempotent)
    op.execute('CREATE SCHEMA IF NOT EXISTS "omnibase_meta"')

    op.create_table(
        "tenants",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=False),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column(
            "schema_name",
            sa.String(length=63),
            nullable=False,
            unique=True,
        ),
        sa.Column("slug", sa.String(length=50), nullable=False, unique=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        schema="omnibase_meta",
        comment="Tenant registry (global; one row per workspace)",
    )

    # Indices for common query patterns
    op.create_index(
        "ix_omnibase_meta_tenants_schema_name",
        "tenants",
        ["schema_name"],
        unique=True,
        schema="omnibase_meta",
    )
    op.create_index(
        "ix_omnibase_meta_tenants_slug",
        "tenants",
        ["slug"],
        unique=True,
        schema="omnibase_meta",
    )
    op.create_index(
        "ix_omnibase_meta_tenants_is_default",
        "tenants",
        ["is_default"],
        schema="omnibase_meta",
    )


def downgrade() -> None:
    """Drop tenants table (preserves schema for safety)."""
    if op.get_context().config.attributes.get("migration_schema_scope") == "tenant":
        return

    op.drop_index(
        "ix_omnibase_meta_tenants_is_default",
        schema="omnibase_meta",
        table_name="tenants",
    )
    op.drop_index(
        "ix_omnibase_meta_tenants_slug",
        schema="omnibase_meta",
        table_name="tenants",
    )
    op.drop_index(
        "ix_omnibase_meta_tenants_schema_name",
        schema="omnibase_meta",
        table_name="tenants",
    )
    op.drop_table("tenants", schema="omnibase_meta")
    # NOTE: we deliberately do NOT drop the omnibase_meta schema in downgrade.
    # Tenant schemas may still contain data; dropping global schema is destructive.
