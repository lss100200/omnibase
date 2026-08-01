"""Add the global P34.2 capability ledger and server-side key registry.

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-31 16:00:00

This revision is global-scope only.  It does not modify tenant schemas,
canonical RAG data, or the Phase 1.6 V1/V2 indexes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "omnibase_meta"
_UUID = postgresql.UUID(as_uuid=False)
_JSONB = postgresql.JSONB(astext_type=sa.Text())


def _migration_schema_scope() -> str:
    scope = op.get_context().config.attributes.get("migration_schema_scope")
    if scope not in {"global", "tenant"}:
        raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")
    return scope


def upgrade() -> None:
    """Create P34.2 capability tables in ``omnibase_meta`` only."""
    if _migration_schema_scope() == "tenant":
        return

    op.create_table(
        "capability_signing_keys",
        sa.Column("kid", sa.String(64), primary_key=True),
        sa.Column("algorithm", sa.String(16), nullable=False, server_default="RS256"),
        sa.Column("public_key_pem", sa.Text(), nullable=False),
        sa.Column("public_key_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("algorithm = 'RS256'", name="capability_signing_keys_algorithm_check"),
        sa.CheckConstraint(
            "state IN ('active', 'retired', 'revoked')", name="capability_signing_keys_state_check"
        ),
        sa.CheckConstraint(
            "kid ~ '^[A-Za-z0-9._-]{8,64}$'", name="capability_signing_keys_kid_check"
        ),
        sa.CheckConstraint(
            "public_key_sha256 ~ '^[0-9a-f]{64}$'", name="capability_signing_keys_fingerprint_check"
        ),
        sa.CheckConstraint(
            "public_key_pem LIKE '-----BEGIN PUBLIC KEY-----%'",
            name="capability_signing_keys_public_pem_check",
        ),
        sa.CheckConstraint("expires_at > not_before", name="capability_signing_keys_window_check"),
        schema=_SCHEMA,
    )
    op.create_index(
        "capability_signing_keys_state_window_idx",
        "capability_signing_keys",
        ["state", "not_before", "expires_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "capability_grants",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("runtime_instance_id", _UUID, nullable=False),
        sa.Column("actor_user_id", _UUID, nullable=False),
        sa.Column("parent_grant_id", _UUID, nullable=True),
        sa.Column("actions", postgresql.ARRAY(sa.String(32)), nullable=False),
        sa.Column("resource_ids", postgresql.ARRAY(_UUID), nullable=False),
        sa.Column(
            "constraints",
            _JSONB,
            nullable=False,
            server_default=sa.text("'{\"timeout_ms\": 2000}'::jsonb"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("max_calls", sa.BigInteger(), nullable=False),
        sa.Column("max_bytes", sa.BigInteger(), nullable=False),
        sa.Column("max_cost_units", sa.BigInteger(), nullable=False),
        sa.Column("delegation_depth", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delegation_depth_limit", sa.Integer(), nullable=False),
        sa.Column("approval_id", _UUID, nullable=True),
        sa.Column("created_by_actor_type", sa.String(16), nullable=False, server_default="system"),
        sa.Column("created_by_actor_id", _UUID, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('active', 'revoked', 'expired')", name="capability_grants_state_check"
        ),
        sa.CheckConstraint(
            "cardinality(actions) > 0 AND actions <@ ARRAY['data.schema.read', "
            "'data.rows.read', 'rag.search', 'rag.citation.read']::varchar[]",
            name="capability_grants_read_actions_check",
        ),
        sa.CheckConstraint(
            "cardinality(resource_ids) > 0", name="capability_grants_resources_check"
        ),
        sa.CheckConstraint("version >= 1", name="capability_grants_version_check"),
        sa.CheckConstraint(
            "delegation_depth >= 0 AND delegation_depth_limit >= delegation_depth AND delegation_depth_limit <= 8",
            name="capability_grants_delegation_depth_check",
        ),
        sa.CheckConstraint("approval_id IS NULL", name="capability_grants_p34_2_no_approval_check"),
        sa.CheckConstraint(
            "max_calls > 0 AND max_bytes > 0 AND max_cost_units > 0",
            name="capability_grants_budget_check",
        ),
        sa.CheckConstraint("expires_at > not_before", name="capability_grants_window_check"),
        sa.CheckConstraint(
            "created_by_actor_type = 'system' AND created_by_actor_id IS NOT NULL",
            name="capability_grants_trusted_issuer_check",
        ),
        sa.CheckConstraint(
            "(delegation_depth = 0 AND parent_grant_id IS NULL) OR (delegation_depth > 0 AND parent_grant_id IS NOT NULL)",
            name="capability_grants_parent_depth_check",
        ),
        sa.CheckConstraint(
            "(state = 'revoked' AND revoked_at IS NOT NULL) OR (state <> 'revoked' AND revoked_at IS NULL)",
            name="capability_grants_revoked_at_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(constraints) = 'object'",
            name="capability_grants_constraints_object_check",
        ),
        sa.CheckConstraint(
            "constraints ? 'timeout_ms' AND "
            "jsonb_typeof(constraints -> 'timeout_ms') = 'number' AND "
            "(constraints ->> 'timeout_ms')::numeric = "
            "trunc((constraints ->> 'timeout_ms')::numeric) AND "
            "(constraints ->> 'timeout_ms')::numeric BETWEEN 1 AND 5000",
            name="capability_grants_timeout_constraint_check",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="capability_grants_id_tenant_uq"),
        sa.ForeignKeyConstraint(
            ["parent_grant_id", "tenant_id"],
            [f"{_SCHEMA}.capability_grants.id", f"{_SCHEMA}.capability_grants.tenant_id"],
            name="capability_grants_parent_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "capability_grants_tenant_workspace_idx",
        "capability_grants",
        ["tenant_id", "workspace_id", "state"],
        schema=_SCHEMA,
    )
    op.create_index(
        "capability_grants_runtime_idx",
        "capability_grants",
        ["runtime_instance_id", "state", "expires_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "capability_grants_parent_idx", "capability_grants", ["parent_grant_id"], schema=_SCHEMA
    )

    op.create_table(
        "capability_usage",
        sa.Column("grant_id", _UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("bytes_out", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("cost_units", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "calls >= 0 AND bytes_in >= 0 AND bytes_out >= 0 AND cost_units >= 0",
            name="capability_usage_nonnegative_check",
        ),
        sa.UniqueConstraint("tenant_id", "grant_id", name="capability_usage_tenant_grant_uq"),
        sa.ForeignKeyConstraint(
            ["grant_id", "tenant_id"],
            [f"{_SCHEMA}.capability_grants.id", f"{_SCHEMA}.capability_grants.tenant_id"],
            name="capability_usage_grant_tenant_fk",
            ondelete="CASCADE",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "capability_usage_tenant_updated_idx",
        "capability_usage",
        ["tenant_id", "updated_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "capability_revocations",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("grant_id", _UUID, nullable=False),
        sa.Column("token_jti", sa.String(128), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("actor_type", sa.String(16), nullable=False),
        sa.Column("actor_id", _UUID, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "token_jti IS NULL OR token_jti ~ '^[A-Za-z0-9._-]{16,128}$'",
            name="capability_revocations_jti_check",
        ),
        sa.CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_.:-]{1,63}$'", name="capability_revocations_reason_check"
        ),
        sa.CheckConstraint(
            "actor_type IN ('user', 'system') AND actor_id IS NOT NULL",
            name="capability_revocations_actor_check",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id", "tenant_id"],
            [f"{_SCHEMA}.capability_grants.id", f"{_SCHEMA}.capability_grants.tenant_id"],
            name="capability_revocations_grant_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "capability_revocations_grant_wide_uq",
        "capability_revocations",
        ["grant_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("token_jti IS NULL"),
    )
    op.create_index(
        "capability_revocations_grant_jti_uq",
        "capability_revocations",
        ["grant_id", "token_jti"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("token_jti IS NOT NULL"),
    )
    op.create_index(
        "capability_revocations_tenant_created_idx",
        "capability_revocations",
        ["tenant_id", "created_at"],
        schema=_SCHEMA,
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION omnibase_meta.prevent_capability_revocation_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'omnibase_meta.capability_revocations is append-only'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER capability_revocations_append_only
        BEFORE UPDATE OR DELETE ON omnibase_meta.capability_revocations
        FOR EACH ROW EXECUTE FUNCTION omnibase_meta.prevent_capability_revocation_mutation()
        """
    )


def downgrade() -> None:
    """Drop only the P34.2 global capability ledger."""
    if _migration_schema_scope() == "tenant":
        return
    op.execute(
        "DROP TRIGGER IF EXISTS capability_revocations_append_only ON omnibase_meta.capability_revocations"
    )
    op.execute("DROP FUNCTION IF EXISTS omnibase_meta.prevent_capability_revocation_mutation()")
    op.drop_table("capability_revocations", schema=_SCHEMA)
    op.drop_table("capability_usage", schema=_SCHEMA)
    op.drop_table("capability_grants", schema=_SCHEMA)
    op.drop_table("capability_signing_keys", schema=_SCHEMA)
