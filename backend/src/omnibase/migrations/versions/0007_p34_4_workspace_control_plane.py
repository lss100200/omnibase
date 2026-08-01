"""Add the global P34.4 Workspace control-plane persistence layer.

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-01 15:30:00

This revision is global-only.  It stores logical Workspace, lifecycle, lease,
overlay-control, and synthetic collaboration metadata in ``omnibase_meta``.
Tenant schemas and canonical tenant data remain untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
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


def _id_column(*, generated: bool = True) -> sa.Column:
    return sa.Column(
        "id",
        _UUID,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()") if generated else None,
    )


def _tenant_id_column(*, foreign_key: bool = False) -> sa.Column:
    if foreign_key:
        return sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.tenants.id", ondelete="CASCADE"),
            nullable=False,
        )
    return sa.Column("tenant_id", _UUID, nullable=False)


def _created_at_column() -> sa.Column:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _updated_at_column() -> sa.Column:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def _tighten_existing_resource_tenancy() -> None:
    """Replace legacy single-column references with tenant-bound references."""
    op.create_unique_constraint(
        "resource_registry_id_tenant_uq",
        "resource_registry",
        ["id", "tenant_id"],
        schema=_SCHEMA,
    )
    op.execute(
        """
        DO $$
        DECLARE
            legacy_constraint record;
        BEGIN
            FOR legacy_constraint IN
                SELECT c.conname
                FROM pg_constraint c
                JOIN pg_class source_table ON source_table.oid = c.conrelid
                JOIN pg_namespace source_schema
                  ON source_schema.oid = source_table.relnamespace
                JOIN pg_class target_table ON target_table.oid = c.confrelid
                JOIN pg_namespace target_schema
                  ON target_schema.oid = target_table.relnamespace
                WHERE c.contype = 'f'
                  AND source_schema.nspname = 'omnibase_meta'
                  AND source_table.relname = 'resource_registry'
                  AND target_schema.nspname = 'omnibase_meta'
                  AND target_table.relname = 'resource_registry'
                  AND cardinality(c.conkey) = 1
                  AND pg_get_constraintdef(c.oid) LIKE 'FOREIGN KEY (parent_id)%'
            LOOP
                EXECUTE format(
                    'ALTER TABLE omnibase_meta.resource_registry DROP CONSTRAINT %I',
                    legacy_constraint.conname
                );
            END LOOP;
        END;
        $$
        """
    )
    op.create_foreign_key(
        "resource_registry_parent_tenant_fk",
        "resource_registry",
        "resource_registry",
        ["parent_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "resource_lineage_source_tenant_fk",
        "resource_lineage",
        "resource_registry",
        ["source_resource_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "resource_lineage_derived_tenant_fk",
        "resource_lineage",
        "resource_registry",
        ["derived_resource_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    """Create P34.4 Workspace control-plane tables in the global schema."""
    if _migration_schema_scope() == "tenant":
        return

    _tighten_existing_resource_tenancy()

    op.create_table(
        "workspace_templates",
        _id_column(),
        _tenant_id_column(foreign_key=True),
        sa.Column("template_key", sa.String(length=100), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("digest", sa.String(length=64), nullable=False),
        sa.Column(
            "template_spec",
            _JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("supersedes_template_id", _UUID, nullable=True),
        sa.Column("created_by_user_id", _UUID, nullable=False),
        _created_at_column(),
        sa.CheckConstraint("version >= 1", name="workspace_templates_version_check"),
        sa.CheckConstraint(
            "digest ~ '^[0-9a-f]{64}$'",
            name="workspace_templates_digest_check",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'deprecated', 'revoked')",
            name="workspace_templates_state_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(template_spec) = 'object'",
            name="workspace_templates_spec_object_check",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "template_key",
            "version",
            name="workspace_templates_key_version_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="workspace_templates_id_tenant_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_templates_tenant_state_idx",
        "workspace_templates",
        ["tenant_id", "state", "template_key"],
        schema=_SCHEMA,
    )

    op.create_table(
        "workspaces",
        _id_column(generated=False),
        _tenant_id_column(foreign_key=True),
        sa.Column("template_id", _UUID, nullable=False),
        sa.Column("owner_user_id", _UUID, nullable=False),
        sa.Column("parent_workspace_id", _UUID, nullable=True),
        sa.Column("restored_from_snapshot_id", _UUID, nullable=True),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "desired_state",
            sa.String(length=16),
            nullable=False,
            server_default="stopped",
        ),
        sa.Column(
            "observed_state",
            sa.String(length=16),
            nullable=False,
            server_default="stopped",
        ),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "quota",
            _JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "desired_state IN ('stopped', 'running', 'paused', 'archived')",
            name="workspaces_desired_state_check",
        ),
        sa.CheckConstraint(
            "observed_state IN ('provisioning', 'stopped', 'starting', 'running', "
            "'pausing', 'paused', 'stopping', 'archiving', 'archived', 'failed')",
            name="workspaces_observed_state_check",
        ),
        sa.CheckConstraint("generation >= 1", name="workspaces_generation_check"),
        sa.CheckConstraint("version >= 1", name="workspaces_version_check"),
        sa.CheckConstraint(
            "jsonb_typeof(quota) = 'object'",
            name="workspaces_quota_object_check",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="workspaces_id_tenant_uq"),
        sa.ForeignKeyConstraint(
            ["id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspaces_resource_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["template_id", "tenant_id"],
            [f"{_SCHEMA}.workspace_templates.id", f"{_SCHEMA}.workspace_templates.tenant_id"],
            name="workspaces_template_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspaces_parent_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspaces_tenant_observed_idx",
        "workspaces",
        ["tenant_id", "observed_state", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "workspaces_tenant_owner_idx",
        "workspaces",
        ["tenant_id", "owner_user_id", "created_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "workspace_memberships",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_user_id", _UUID, nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "role IN ('viewer', 'member', 'operator', 'maintainer', 'owner')",
            name="workspace_memberships_role_check",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'suspended', 'revoked')",
            name="workspace_memberships_state_check",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="workspace_memberships_version_check",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_memberships_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_memberships_active_user_uq",
        "workspace_memberships",
        ["tenant_id", "workspace_id", "user_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("state IN ('active', 'suspended')"),
    )
    op.create_index(
        "workspace_memberships_tenant_user_idx",
        "workspace_memberships",
        ["tenant_id", "user_id", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "resource_scope_bindings",
        sa.Column("resource_id", _UUID, primary_key=True),
        _tenant_id_column(),
        sa.Column("scope_class", sa.String(length=32), nullable=False),
        sa.Column("user_id", _UUID, nullable=True),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("run_id", _UUID, nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _created_at_column(),
        sa.CheckConstraint(
            "scope_class IN ('platform_internal', 'tenant_shared', 'user_private', "
            "'workspace_private', 'workspace_shared', 'run_ephemeral')",
            name="resource_scope_bindings_scope_check",
        ),
        sa.CheckConstraint(
            "(scope_class = 'platform_internal' AND user_id IS NULL "
            "AND workspace_id IS NULL AND run_id IS NULL) OR "
            "(scope_class = 'tenant_shared' AND user_id IS NULL "
            "AND workspace_id IS NULL AND run_id IS NULL) OR "
            "(scope_class = 'user_private' AND user_id IS NOT NULL "
            "AND workspace_id IS NULL AND run_id IS NULL) OR "
            "(scope_class IN ('workspace_private', 'workspace_shared') "
            "AND user_id IS NULL AND workspace_id IS NOT NULL AND run_id IS NULL) OR "
            "(scope_class = 'run_ephemeral' AND user_id IS NULL "
            "AND workspace_id IS NOT NULL AND run_id IS NOT NULL)",
            name="resource_scope_bindings_identity_check",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="resource_scope_bindings_version_check",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="resource_scope_bindings_resource_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="resource_scope_bindings_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "resource_scope_bindings_workspace_idx",
        "resource_scope_bindings",
        ["tenant_id", "workspace_id", "scope_class"],
        schema=_SCHEMA,
    )
    op.create_index(
        "resource_scope_bindings_user_idx",
        "resource_scope_bindings",
        ["tenant_id", "user_id", "scope_class"],
        schema=_SCHEMA,
    )

    op.create_table(
        "workspace_scope_grants",
        _id_column(),
        _tenant_id_column(),
        sa.Column("target_workspace_id", _UUID, nullable=False),
        sa.Column("source_scope", sa.String(length=32), nullable=False),
        sa.Column("source_owner_id", _UUID, nullable=True),
        sa.Column("resource_id", _UUID, nullable=False),
        sa.Column("actions", postgresql.ARRAY(sa.String(length=100)), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", _UUID, nullable=False),
        _created_at_column(),
        sa.CheckConstraint(
            "source_scope IN ('user_private', 'workspace_private', "
            "'workspace_shared', 'tenant_shared')",
            name="workspace_scope_grants_source_scope_check",
        ),
        sa.CheckConstraint(
            "source_scope <> 'tenant_shared' OR source_owner_id IS NULL",
            name="workspace_scope_grants_tenant_owner_check",
        ),
        sa.CheckConstraint(
            "source_scope = 'tenant_shared' OR source_owner_id IS NOT NULL",
            name="workspace_scope_grants_source_owner_check",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'revoked', 'expired')",
            name="workspace_scope_grants_state_check",
        ),
        sa.CheckConstraint(
            "cardinality(actions) BETWEEN 1 AND 32",
            name="workspace_scope_grants_actions_check",
        ),
        sa.CheckConstraint(
            "actions <@ ARRAY['resource.list', 'resource.read']::varchar[]",
            name="workspace_scope_grants_actions_allowlist_check",
        ),
        sa.CheckConstraint("version >= 1", name="workspace_scope_grants_version_check"),
        sa.ForeignKeyConstraint(
            ["target_workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_scope_grants_target_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_scope_grants_resource_tenant_fk",
            ondelete="CASCADE",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_scope_grants_target_idx",
        "workspace_scope_grants",
        ["tenant_id", "target_workspace_id", "state", "expires_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "workspace_runs",
        _id_column(generated=False),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("desired_state", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("observed_state", sa.String(length=16), nullable=False, server_default="queued"),
        sa.Column("next_fencing_token", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("runtime_instance_id", _UUID, nullable=True),
        sa.Column("workload_identity_digest", sa.String(length=64), nullable=True),
        sa.Column("last_result_digest", sa.String(length=64), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("created_by_user_id", _UUID, nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint("kind IN ('batch', 'interactive')", name="workspace_runs_kind_check"),
        sa.CheckConstraint(
            "desired_state IN ('queued', 'running', 'paused', 'stopped', 'cancelled')",
            name="workspace_runs_desired_state_check",
        ),
        sa.CheckConstraint(
            "observed_state IN ('queued', 'leased', 'starting', 'running', "
            "'pausing', 'paused', 'stopping', 'stopped', 'succeeded', 'failed', "
            "'cancelled')",
            name="workspace_runs_observed_state_check",
        ),
        sa.CheckConstraint("generation >= 1", name="workspace_runs_generation_check"),
        sa.CheckConstraint("next_fencing_token >= 1", name="workspace_runs_fencing_check"),
        sa.CheckConstraint("version >= 1", name="workspace_runs_version_check"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_runs_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="workspace_runs_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="workspace_runs_id_workspace_tenant_uq",
        ),
        sa.ForeignKeyConstraint(
            ["id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_runs_resource_tenant_fk",
            ondelete="CASCADE",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_runs_workspace_state_idx",
        "workspace_runs",
        ["tenant_id", "workspace_id", "observed_state", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_runs_one_active_uq",
        "workspace_runs",
        ["tenant_id", "workspace_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text(
            "observed_state IN ('leased', 'starting', 'running', 'pausing', 'stopping')"
        ),
    )
    op.create_foreign_key(
        "resource_scope_bindings_run_workspace_tenant_fk",
        "resource_scope_bindings",
        "workspace_runs",
        ["run_id", "tenant_id", "workspace_id"],
        ["id", "tenant_id", "workspace_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="CASCADE",
    )

    op.create_table(
        "run_leases",
        _id_column(),
        _tenant_id_column(),
        sa.Column("run_id", _UUID, nullable=False),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("node_id", _UUID, nullable=False),
        sa.Column("node_fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at_column(),
        sa.CheckConstraint(
            "state IN ('active', 'expired', 'revoked', 'completed')",
            name="run_leases_state_check",
        ),
        sa.CheckConstraint("generation >= 1", name="run_leases_generation_check"),
        sa.CheckConstraint("fencing_token >= 1", name="run_leases_fencing_check"),
        sa.CheckConstraint(
            "node_fencing_token >= 1",
            name="run_leases_node_fencing_check",
        ),
        sa.CheckConstraint(
            "heartbeat_at <= expires_at",
            name="run_leases_heartbeat_expiry_check",
        ),
        sa.ForeignKeyConstraint(
            ["run_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.workspace_runs.id",
                f"{_SCHEMA}.workspace_runs.tenant_id",
                f"{_SCHEMA}.workspace_runs.workspace_id",
            ],
            name="run_leases_run_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "run_id",
            "fencing_token",
            name="run_leases_fencing_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "run_leases_run_state_idx",
        "run_leases",
        ["tenant_id", "run_id", "state", "expires_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "run_leases_one_active_uq",
        "run_leases",
        ["tenant_id", "run_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("state = 'active'"),
    )

    op.create_table(
        "workspace_snapshots",
        _id_column(generated=False),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("source_generation", sa.Integer(), nullable=False),
        sa.Column("manifest_digest", sa.String(length=64), nullable=False),
        sa.Column("metadata", _JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="ready"),
        sa.Column("created_by_user_id", _UUID, nullable=False),
        _created_at_column(),
        sa.CheckConstraint(
            "source_generation >= 1",
            name="workspace_snapshots_generation_check",
        ),
        sa.CheckConstraint(
            "manifest_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_snapshots_digest_check",
        ),
        sa.CheckConstraint(
            "state IN ('ready', 'revoked')",
            name="workspace_snapshots_state_check",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_snapshots_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="workspace_snapshots_id_tenant_uq"),
        sa.ForeignKeyConstraint(
            ["id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_snapshots_resource_tenant_fk",
            ondelete="CASCADE",
        ),
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "workspaces_restored_snapshot_tenant_fk",
        "workspaces",
        "workspace_snapshots",
        ["restored_from_snapshot_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "workspace_nodes",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("owner_user_id", _UUID, nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("identity_digest", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "attestation_state",
            sa.String(length=16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at_column(),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'suspended', 'revoked')",
            name="workspace_nodes_state_check",
        ),
        sa.CheckConstraint(
            "attestation_state IN ('pending', 'verified', 'expired', 'rejected')",
            name="workspace_nodes_attestation_state_check",
        ),
        sa.CheckConstraint(
            "identity_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_nodes_identity_digest_check",
        ),
        sa.CheckConstraint("fencing_token >= 1", name="workspace_nodes_fencing_check"),
        sa.CheckConstraint("version >= 1", name="workspace_nodes_version_check"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_nodes_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="workspace_nodes_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="workspace_nodes_id_workspace_tenant_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "identity_digest",
            name="workspace_nodes_identity_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_nodes_workspace_state_idx",
        "workspace_nodes",
        ["tenant_id", "workspace_id", "state", "attestation_state"],
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "run_leases_node_workspace_tenant_fk",
        "run_leases",
        "workspace_nodes",
        ["node_id", "tenant_id", "workspace_id"],
        ["id", "tenant_id", "workspace_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )

    op.create_table(
        "node_attestations",
        _id_column(),
        _tenant_id_column(),
        sa.Column("node_id", _UUID, nullable=False),
        sa.Column("nonce_digest", sa.String(length=64), nullable=False),
        sa.Column("evidence_digest", sa.String(length=64), nullable=False),
        sa.Column("verifier", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "state IN ('verified', 'expired', 'rejected', 'revoked')",
            name="node_attestations_state_check",
        ),
        sa.CheckConstraint(
            "nonce_digest ~ '^[0-9a-f]{64}$' AND evidence_digest ~ '^[0-9a-f]{64}$'",
            name="node_attestations_digest_check",
        ),
        sa.ForeignKeyConstraint(
            ["node_id", "tenant_id"],
            [f"{_SCHEMA}.workspace_nodes.id", f"{_SCHEMA}.workspace_nodes.tenant_id"],
            name="node_attestations_node_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "nonce_digest",
            name="node_attestations_nonce_uq",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "peer_grants",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("source_node_id", _UUID, nullable=False),
        sa.Column("target_node_id", _UUID, nullable=False),
        sa.Column("actions", postgresql.ARRAY(sa.String(length=100)), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_user_id", _UUID, nullable=False),
        _created_at_column(),
        sa.CheckConstraint(
            "source_node_id <> target_node_id",
            name="peer_grants_distinct_nodes_check",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'revoked', 'expired')",
            name="peer_grants_state_check",
        ),
        sa.CheckConstraint(
            "cardinality(actions) BETWEEN 1 AND 32",
            name="peer_grants_actions_check",
        ),
        sa.CheckConstraint("fencing_token >= 1", name="peer_grants_fencing_check"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="peer_grants_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_node_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.workspace_nodes.id",
                f"{_SCHEMA}.workspace_nodes.tenant_id",
                f"{_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="peer_grants_source_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["target_node_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.workspace_nodes.id",
                f"{_SCHEMA}.workspace_nodes.tenant_id",
                f"{_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="peer_grants_target_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="peer_grants_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="peer_grants_id_workspace_tenant_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "peer_grants_workspace_state_idx",
        "peer_grants",
        ["tenant_id", "workspace_id", "state", "expires_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "service_advertisements",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("node_id", _UUID, nullable=False),
        sa.Column("service_key", sa.String(length=100), nullable=False),
        sa.Column("protocol", sa.String(length=16), nullable=False),
        sa.Column("logical_port", sa.Integer(), nullable=False),
        sa.Column(
            "actions",
            postgresql.ARRAY(sa.String(length=100)),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at_column(),
        sa.CheckConstraint(
            "protocol IN ('https', 'git', 'artifact', 'event')",
            name="service_advertisements_protocol_check",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'revoked', 'expired')",
            name="service_advertisements_state_check",
        ),
        sa.CheckConstraint(
            "logical_port BETWEEN 1 AND 65535",
            name="service_port_check",
        ),
        sa.CheckConstraint("generation >= 1", name="service_generation_check"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="service_advertisements_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["node_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.workspace_nodes.id",
                f"{_SCHEMA}.workspace_nodes.tenant_id",
                f"{_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="service_advertisements_node_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "id",
            name="service_advertisements_id_tenant_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="service_advertisements_id_workspace_tenant_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "node_id",
            "service_key",
            "generation",
            name="service_advertisements_generation_uq",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "network_lease_cursors",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("peer_grant_id", _UUID, nullable=False),
        sa.Column("service_id", _UUID, nullable=False),
        sa.Column("requester_node_id", _UUID, nullable=False),
        sa.Column("next_fencing_token", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("current_fencing_token", sa.BigInteger(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "next_fencing_token >= 1",
            name="network_lease_cursors_next_fencing_check",
        ),
        sa.CheckConstraint(
            "current_fencing_token IS NULL OR current_fencing_token >= 1",
            name="network_lease_cursors_current_fencing_check",
        ),
        sa.CheckConstraint("version >= 1", name="network_lease_cursors_version_check"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="network_lease_cursors_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["peer_grant_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.peer_grants.id",
                f"{_SCHEMA}.peer_grants.tenant_id",
                f"{_SCHEMA}.peer_grants.workspace_id",
            ],
            name="network_lease_cursors_peer_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.service_advertisements.id",
                f"{_SCHEMA}.service_advertisements.tenant_id",
                f"{_SCHEMA}.service_advertisements.workspace_id",
            ],
            name="network_lease_cursors_service_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requester_node_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.workspace_nodes.id",
                f"{_SCHEMA}.workspace_nodes.tenant_id",
                f"{_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="network_lease_cursors_requester_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "peer_grant_id",
            "service_id",
            "requester_node_id",
            name="network_lease_cursors_tuple_uq",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "network_leases",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("peer_grant_id", _UUID, nullable=False),
        sa.Column("service_id", _UUID, nullable=False),
        sa.Column("requester_node_id", _UUID, nullable=False),
        sa.Column("fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="active"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _created_at_column(),
        sa.CheckConstraint(
            "state IN ('active', 'revoked', 'expired', 'consumed')",
            name="network_leases_state_check",
        ),
        sa.CheckConstraint("fencing_token >= 1", name="network_leases_fencing_check"),
        sa.ForeignKeyConstraint(
            ["peer_grant_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.peer_grants.id",
                f"{_SCHEMA}.peer_grants.tenant_id",
                f"{_SCHEMA}.peer_grants.workspace_id",
            ],
            name="network_leases_peer_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["service_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.service_advertisements.id",
                f"{_SCHEMA}.service_advertisements.tenant_id",
                f"{_SCHEMA}.service_advertisements.workspace_id",
            ],
            name="network_leases_service_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="network_leases_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requester_node_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.workspace_nodes.id",
                f"{_SCHEMA}.workspace_nodes.tenant_id",
                f"{_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="network_leases_requester_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "peer_grant_id",
            "service_id",
            "fencing_token",
            name="network_leases_fencing_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "network_leases_expiry_idx",
        "network_leases",
        ["tenant_id", "state", "expires_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "network_leases_one_active_uq",
        "network_leases",
        ["tenant_id", "peer_grant_id", "service_id", "requester_node_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("state = 'active'"),
    )

    op.create_table(
        "workspace_authorities",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("authority_node_id", _UUID, nullable=False),
        sa.Column("epoch", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=False),
        _created_at_column(),
        sa.CheckConstraint("epoch >= 1", name="workspace_authorities_epoch_check"),
        sa.CheckConstraint(
            "state IN ('active', 'offline', 'revoked')",
            name="workspace_authorities_state_check",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_authorities_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.workspace_nodes.id",
                f"{_SCHEMA}.workspace_nodes.tenant_id",
                f"{_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="workspace_authorities_node_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "epoch",
            name="workspace_authorities_epoch_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_authorities_current_idx",
        "workspace_authorities",
        ["tenant_id", "workspace_id", "state", "epoch"],
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_authorities_one_active_uq",
        "workspace_authorities",
        ["tenant_id", "workspace_id"],
        unique=True,
        schema=_SCHEMA,
        postgresql_where=sa.text("state = 'active'"),
    )

    op.create_table(
        "collaboration_artifacts",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("authority_epoch", sa.BigInteger(), nullable=False),
        sa.Column("content_digest", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(length=100), nullable=False),
        sa.Column("metadata", _JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("state", sa.String(length=16), nullable=False, server_default="available"),
        sa.Column("created_by_node_id", _UUID, nullable=False),
        _created_at_column(),
        sa.CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'",
            name="collaboration_artifacts_digest_check",
        ),
        sa.CheckConstraint("size_bytes >= 0", name="collaboration_artifacts_size_check"),
        sa.CheckConstraint(
            "state IN ('available', 'revoked')",
            name="collaboration_artifacts_state_check",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="collaboration_artifacts_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_node_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.workspace_nodes.id",
                f"{_SCHEMA}.workspace_nodes.tenant_id",
                f"{_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="collaboration_artifacts_node_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "content_digest",
            name="collaboration_artifacts_digest_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="collaboration_artifacts_id_workspace_tenant_uq",
        ),
        schema=_SCHEMA,
    )

    op.create_table(
        "collaboration_events",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("authority_node_id", _UUID, nullable=False),
        sa.Column("authority_epoch", sa.BigInteger(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("event_digest", sa.String(length=64), nullable=False),
        sa.Column("artifact_id", _UUID, nullable=True),
        sa.Column("parent_event_id", _UUID, nullable=True),
        sa.Column("metadata", _JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        _created_at_column(),
        sa.CheckConstraint("sequence >= 1", name="collaboration_events_sequence_check"),
        sa.CheckConstraint(
            "authority_epoch >= 1",
            name="collaboration_events_epoch_check",
        ),
        sa.CheckConstraint(
            "event_type IN ('git_ref', 'artifact_published', 'draft_promoted')",
            name="collaboration_events_type_check",
        ),
        sa.CheckConstraint(
            "event_digest ~ '^[0-9a-f]{64}$'",
            name="collaboration_events_digest_check",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="collaboration_events_workspace_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["authority_node_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.workspace_nodes.id",
                f"{_SCHEMA}.workspace_nodes.tenant_id",
                f"{_SCHEMA}.workspace_nodes.workspace_id",
            ],
            name="collaboration_events_node_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["artifact_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.collaboration_artifacts.id",
                f"{_SCHEMA}.collaboration_artifacts.tenant_id",
                f"{_SCHEMA}.collaboration_artifacts.workspace_id",
            ],
            name="collaboration_events_artifact_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_event_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.collaboration_events.id",
                f"{_SCHEMA}.collaboration_events.tenant_id",
                f"{_SCHEMA}.collaboration_events.workspace_id",
            ],
            name="collaboration_events_parent_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "sequence",
            name="collaboration_events_sequence_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "event_digest",
            name="collaboration_events_digest_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "workspace_id",
            "id",
            name="collaboration_events_id_workspace_tenant_uq",
        ),
        schema=_SCHEMA,
    )


def downgrade() -> None:
    """Drop P34.4 only when every P34.4 table is empty."""
    if _migration_schema_scope() == "tenant":
        return

    op.execute(
        """
        DO $$
        DECLARE
            table_name text;
            has_rows boolean;
        BEGIN
            FOREACH table_name IN ARRAY ARRAY[
                'workspace_templates',
                'workspaces',
                'workspace_memberships',
                'resource_scope_bindings',
                'workspace_scope_grants',
                'workspace_runs',
                'run_leases',
                'workspace_snapshots',
                'workspace_nodes',
                'node_attestations',
                'peer_grants',
                'service_advertisements',
                'network_lease_cursors',
                'network_leases',
                'workspace_authorities',
                'collaboration_artifacts',
                'collaboration_events'
            ]
            LOOP
                EXECUTE format(
                    'SELECT EXISTS (SELECT 1 FROM omnibase_meta.%I LIMIT 1)',
                    table_name
                ) INTO has_rows;
                IF has_rows THEN
                    RAISE EXCEPTION
                        'P34.4 downgrade refused: omnibase_meta.% contains data',
                        table_name
                        USING ERRCODE = '55000';
                END IF;
            END LOOP;
        END;
        $$
        """
    )

    op.drop_table("collaboration_events", schema=_SCHEMA)
    op.drop_table("collaboration_artifacts", schema=_SCHEMA)
    op.drop_table("workspace_authorities", schema=_SCHEMA)
    op.drop_table("network_leases", schema=_SCHEMA)
    op.drop_table("network_lease_cursors", schema=_SCHEMA)
    op.drop_table("service_advertisements", schema=_SCHEMA)
    op.drop_table("peer_grants", schema=_SCHEMA)
    op.drop_table("run_leases", schema=_SCHEMA)
    op.drop_table("node_attestations", schema=_SCHEMA)
    op.drop_table("workspace_nodes", schema=_SCHEMA)
    op.drop_constraint(
        "workspaces_restored_snapshot_tenant_fk",
        "workspaces",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.drop_table("resource_scope_bindings", schema=_SCHEMA)
    op.drop_table("workspace_snapshots", schema=_SCHEMA)
    op.drop_table("workspace_runs", schema=_SCHEMA)
    op.drop_table("workspace_scope_grants", schema=_SCHEMA)
    op.drop_table("workspace_memberships", schema=_SCHEMA)
    op.drop_table("workspaces", schema=_SCHEMA)
    op.drop_table("workspace_templates", schema=_SCHEMA)

    op.drop_constraint(
        "resource_lineage_derived_tenant_fk",
        "resource_lineage",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint(
        "resource_lineage_source_tenant_fk",
        "resource_lineage",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint(
        "resource_registry_parent_tenant_fk",
        "resource_registry",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint(
        "resource_registry_id_tenant_uq",
        "resource_registry",
        schema=_SCHEMA,
        type_="unique",
    )
    op.create_foreign_key(
        "resource_registry_parent_id_fkey",
        "resource_registry",
        "resource_registry",
        ["parent_id"],
        ["id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="SET NULL",
    )
