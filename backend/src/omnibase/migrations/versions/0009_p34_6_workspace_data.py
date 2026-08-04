"""Add P34.6 Workspace-private data, lineage and derived-RAG persistence.

Revision ID: 0009
Revises: 0008
Create Date: 2026-08-02 14:30:00

The global scope installs logical metadata, immutable lineage and durable
effect records.  The tenant scope installs a derived-only RAG lane that is
physically separate from canonical documents/embeddings tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "omnibase_meta"
_UUID = postgresql.UUID(as_uuid=False)
_READ_ACTIONS = "'data.schema.read', 'data.rows.read', 'rag.search', 'rag.citation.read'"
_WORKSPACE_DATA_ACTIONS = (
    "'data.rows.insert', 'data.rows.update', 'data.rows.delete', "
    "'artifact.read', 'artifact.write', 'rag.derived.create', "
    "'rag.derived.delete'"
)
_SANDBOX_ACTIONS = (
    "'sandbox.prepare', 'sandbox.create', 'sandbox.start', 'sandbox.exec', "
    "'sandbox.cancel', 'sandbox.logs', 'sandbox.stats', 'sandbox.snapshot', "
    "'sandbox.restore', 'sandbox.stop', 'sandbox.destroy'"
)


def _migration_schema_scope() -> str:
    scope = op.get_context().config.attributes.get("migration_schema_scope")
    if scope not in {"global", "tenant"}:
        raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")
    return scope


def _created_at() -> sa.Column:
    return sa.Column(
        "created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def _updated_at() -> sa.Column:
    return sa.Column(
        "updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")
    )


def upgrade() -> None:
    if _migration_schema_scope() == "tenant":
        _upgrade_tenant()
    else:
        _upgrade_global()


def _upgrade_global() -> None:
    op.drop_constraint(
        "capability_grants_action_profile_check",
        "capability_grants",
        schema=_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "capability_grants_action_profile_check",
        "capability_grants",
        "cardinality(actions) > 0 AND ((actions <@ ARRAY["
        f"{_READ_ACTIONS}]::varchar[] AND workload_identity_digest IS NULL) OR "
        "(actions <@ ARRAY["
        f"{_WORKSPACE_DATA_ACTIONS}]::varchar[] AND workload_identity_digest IS NOT NULL "
        "AND workload_identity_digest ~ '^[0-9a-f]{64}$' AND delegation_depth = 0 "
        "AND delegation_depth_limit = 0) OR (actions <@ ARRAY["
        f"{_SANDBOX_ACTIONS}]::varchar[] AND workload_identity_digest IS NOT NULL "
        "AND workload_identity_digest ~ '^[0-9a-f]{64}$' AND cardinality(resource_ids) = 1 "
        "AND delegation_depth = 0 AND delegation_depth_limit = 0))",
        schema=_SCHEMA,
    )

    op.create_unique_constraint(
        "operations_id_tenant_uq", "operations", ["id", "tenant_id"], schema=_SCHEMA
    )
    op.create_unique_constraint(
        "approval_requests_id_tenant_uq",
        "approval_requests",
        ["id", "tenant_id"],
        schema=_SCHEMA,
    )
    op.create_foreign_key(
        "resource_lineage_operation_tenant_fk",
        "resource_lineage",
        "operations",
        ["created_by_operation_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "data_table_bindings_resource_id_fkey",
        "data_table_bindings",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.create_foreign_key(
        "data_table_bindings_resource_tenant_fk",
        "data_table_bindings",
        "resource_registry",
        ["resource_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "data_table_bindings_workspace_tenant_fk",
        "data_table_bindings",
        "workspaces",
        ["workspace_id", "tenant_id"],
        ["id", "tenant_id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "workspace_snapshots_state_check",
        "workspace_snapshots",
        schema=_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "workspace_snapshots_state_check",
        "workspace_snapshots",
        "state IN ('building', 'ready', 'failed', 'revoked')",
        schema=_SCHEMA,
    )
    op.alter_column("workspace_snapshots", "state", schema=_SCHEMA, server_default="building")

    op.create_table(
        "workspace_artifacts",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("source_run_id", _UUID, nullable=True),
        sa.Column("source_generation", sa.Integer(), nullable=False),
        sa.Column("operation_id", _UUID, nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("media_type", sa.String(100), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="staging"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("retention_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_actor_id", _UUID, nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("source_generation >= 1", name="workspace_artifacts_generation_check"),
        sa.CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'", name="workspace_artifacts_digest_check"
        ),
        sa.CheckConstraint("size_bytes >= 0", name="workspace_artifacts_size_check"),
        sa.CheckConstraint(
            "state IN ('staging', 'available', 'tombstoned', 'purge_pending', "
            "'purged', 'failed', 'unknown')",
            name="workspace_artifacts_state_check",
        ),
        sa.CheckConstraint("version >= 1", name="workspace_artifacts_version_check"),
        sa.UniqueConstraint("tenant_id", "id", name="workspace_artifacts_id_tenant_uq"),
        sa.UniqueConstraint("tenant_id", "operation_id", name="workspace_artifacts_operation_uq"),
        sa.ForeignKeyConstraint(
            ["id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_artifacts_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_artifacts_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_run_id", "tenant_id", "workspace_id"],
            [
                f"{_SCHEMA}.workspace_runs.id",
                f"{_SCHEMA}.workspace_runs.tenant_id",
                f"{_SCHEMA}.workspace_runs.workspace_id",
            ],
            name="workspace_artifacts_run_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [f"{_SCHEMA}.operations.id", f"{_SCHEMA}.operations.tenant_id"],
            name="workspace_artifacts_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_artifacts_workspace_state_idx",
        "workspace_artifacts",
        ["tenant_id", "workspace_id", "state", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_artifacts_workspace_digest_idx",
        "workspace_artifacts",
        ["tenant_id", "workspace_id", "content_digest"],
        schema=_SCHEMA,
    )

    op.create_table(
        "workspace_derived_indexes",
        sa.Column("id", _UUID, primary_key=True),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("source_resource_id", _UUID, nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("operation_id", _UUID, nullable=False),
        sa.Column("generation", _UUID, nullable=False, server_default=sa.text("gen_random_uuid()")),
        sa.Column("index_profile_digest", sa.String(64), nullable=False),
        sa.Column("manifest_digest", sa.String(64), nullable=True),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_actor_id", _UUID, nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("source_version >= 1", name="workspace_derived_source_version_check"),
        sa.CheckConstraint(
            "index_profile_digest ~ '^[0-9a-f]{64}$'", name="workspace_derived_profile_digest_check"
        ),
        sa.CheckConstraint(
            "manifest_digest IS NULL OR manifest_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_derived_manifest_digest_check",
        ),
        sa.CheckConstraint("chunk_count >= 0", name="workspace_derived_chunk_count_check"),
        sa.CheckConstraint(
            "state IN ('pending', 'building', 'ready', 'failed', 'revoked', 'unknown')",
            name="workspace_derived_state_check",
        ),
        sa.CheckConstraint(
            "state <> 'ready' OR manifest_digest IS NOT NULL",
            name="workspace_derived_ready_manifest_check",
        ),
        sa.CheckConstraint("version >= 1", name="workspace_derived_version_check"),
        sa.UniqueConstraint("tenant_id", "id", name="workspace_derived_id_tenant_uq"),
        sa.UniqueConstraint("tenant_id", "operation_id", name="workspace_derived_operation_uq"),
        sa.UniqueConstraint("tenant_id", "generation", name="workspace_derived_generation_uq"),
        sa.ForeignKeyConstraint(
            ["id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_derived_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_derived_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_resource_id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_derived_source_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [f"{_SCHEMA}.operations.id", f"{_SCHEMA}.operations.tenant_id"],
            name="workspace_derived_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_derived_workspace_state_idx",
        "workspace_derived_indexes",
        ["tenant_id", "workspace_id", "state", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_derived_source_idx",
        "workspace_derived_indexes",
        ["tenant_id", "source_resource_id", "source_version"],
        schema=_SCHEMA,
    )

    op.create_table(
        "workspace_publications",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("source_workspace_id", _UUID, nullable=False),
        sa.Column("target_workspace_id", _UUID, nullable=True),
        sa.Column("source_resource_id", _UUID, nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("source_manifest_digest", sa.String(64), nullable=False),
        sa.Column("target_scope", sa.String(24), nullable=False),
        sa.Column("target_resource_id", _UUID, nullable=True),
        sa.Column("operation_id", _UUID, nullable=False),
        sa.Column(
            "approval_id",
            _UUID,
            nullable=True,
        ),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(24), nullable=False, server_default="pending_approval"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_actor_id", _UUID, nullable=False),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "source_version >= 1", name="workspace_publications_source_version_check"
        ),
        sa.CheckConstraint(
            "source_manifest_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_publications_source_digest_check",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'", name="workspace_publications_request_hash_check"
        ),
        sa.CheckConstraint(
            "target_scope IN ('workspace_shared', 'tenant_shared')",
            name="workspace_publications_target_scope_check",
        ),
        sa.CheckConstraint(
            "(target_scope = 'workspace_shared' AND target_workspace_id IS NOT NULL) OR (target_scope = 'tenant_shared' AND target_workspace_id IS NULL)",
            name="workspace_publications_target_identity_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending_approval', 'approved', 'copying', 'published', 'rejected', 'expired', 'failed', 'unknown')",
            name="workspace_publications_state_check",
        ),
        sa.CheckConstraint(
            "state = 'pending_approval' OR approval_id IS NOT NULL",
            name="workspace_publications_approval_check",
        ),
        sa.CheckConstraint(
            "state <> 'published' OR target_resource_id IS NOT NULL",
            name="workspace_publications_published_target_check",
        ),
        sa.CheckConstraint("version >= 1", name="workspace_publications_version_check"),
        sa.UniqueConstraint("tenant_id", "id", name="workspace_publications_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id", "operation_id", name="workspace_publications_operation_uq"
        ),
        sa.ForeignKeyConstraint(
            ["source_workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_publications_source_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_publications_target_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_resource_id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_publications_source_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_resource_id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_publications_target_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id", "tenant_id"],
            [f"{_SCHEMA}.approval_requests.id", f"{_SCHEMA}.approval_requests.tenant_id"],
            name="workspace_publications_approval_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [f"{_SCHEMA}.operations.id", f"{_SCHEMA}.operations.tenant_id"],
            name="workspace_publications_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_publications_workspace_state_idx",
        "workspace_publications",
        ["tenant_id", "source_workspace_id", "state", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_publications_source_target_uq",
        "workspace_publications",
        [
            "tenant_id",
            "source_resource_id",
            "source_version",
            "source_manifest_digest",
            "target_scope",
            "target_workspace_id",
        ],
        unique=True,
        schema=_SCHEMA,
        postgresql_nulls_not_distinct=True,
    )

    op.create_table(
        "workspace_snapshot_items",
        sa.Column("snapshot_id", _UUID, primary_key=True),
        sa.Column("ordinal", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("source_resource_id", _UUID, nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("source_kind", sa.String(64), nullable=False),
        sa.Column("source_policy_class", sa.String(32), nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("item_kind", sa.String(24), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("payload_artifact_id", _UUID, nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        _created_at(),
        sa.CheckConstraint("ordinal >= 1", name="workspace_snapshot_items_ordinal_check"),
        sa.CheckConstraint("source_version >= 1", name="workspace_snapshot_items_version_check"),
        sa.CheckConstraint(
            "item_kind IN ('private_table', 'artifact', 'derived_index')",
            name="workspace_snapshot_items_kind_check",
        ),
        sa.CheckConstraint(
            "source_policy_class IN ('workspace_private', 'workspace_derived')",
            name="workspace_snapshot_items_policy_check",
        ),
        sa.CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'", name="workspace_snapshot_items_digest_check"
        ),
        sa.CheckConstraint("size_bytes >= 0", name="workspace_snapshot_items_size_check"),
        sa.UniqueConstraint(
            "tenant_id",
            "snapshot_id",
            "source_resource_id",
            name="workspace_snapshot_items_source_uq",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id", "tenant_id"],
            [f"{_SCHEMA}.workspace_snapshots.id", f"{_SCHEMA}.workspace_snapshots.tenant_id"],
            name="workspace_snapshot_items_snapshot_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_snapshot_items_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_resource_id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_snapshot_items_source_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payload_artifact_id", "tenant_id"],
            [f"{_SCHEMA}.workspace_artifacts.id", f"{_SCHEMA}.workspace_artifacts.tenant_id"],
            name="workspace_snapshot_items_payload_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_snapshot_items_workspace_idx",
        "workspace_snapshot_items",
        ["tenant_id", "workspace_id", "snapshot_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "workspace_data_effects",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("resource_id", _UUID, nullable=True),
        sa.Column("operation_id", _UUID, nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("effect_kind", sa.String(32), nullable=False),
        sa.Column("binding_digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("receipt_digest", sa.String(64), nullable=True),
        sa.Column("reason_code", sa.String(100), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint("sequence >= 1", name="workspace_data_effects_sequence_check"),
        sa.CheckConstraint(
            "effect_kind IN ('artifact_put', 'artifact_delete', 'derived_build', 'publication_copy', 'snapshot_capture', 'snapshot_restore')",
            name="workspace_data_effects_kind_check",
        ),
        sa.CheckConstraint(
            "binding_digest ~ '^[0-9a-f]{64}$'", name="workspace_data_effects_binding_digest_check"
        ),
        sa.CheckConstraint(
            "receipt_digest IS NULL OR receipt_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_data_effects_receipt_digest_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'committed', 'failed', 'unknown')",
            name="workspace_data_effects_state_check",
        ),
        sa.CheckConstraint(
            "state <> 'committed' OR receipt_digest IS NOT NULL",
            name="workspace_data_effects_committed_receipt_check",
        ),
        sa.CheckConstraint("version >= 1", name="workspace_data_effects_version_check"),
        sa.UniqueConstraint("tenant_id", "id", name="workspace_data_effects_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id",
            "operation_id",
            "sequence",
            name="workspace_data_effects_operation_sequence_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "operation_id",
            "effect_kind",
            "binding_digest",
            name="workspace_data_effects_binding_uq",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_data_effects_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_data_effects_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [f"{_SCHEMA}.operations.id", f"{_SCHEMA}.operations.tenant_id"],
            name="workspace_data_effects_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_data_effects_workspace_state_idx",
        "workspace_data_effects",
        ["tenant_id", "workspace_id", "state", "created_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "workspace_data_usage_reservations",
        sa.Column("operation_id", _UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("grant_id", _UUID, nullable=False),
        sa.Column("grant_version", sa.Integer(), nullable=False),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("runtime_instance_id", _UUID, nullable=False),
        sa.Column("workload_identity_digest", sa.String(64), nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("resource_id", _UUID, nullable=False),
        sa.Column("resource_version", sa.Integer(), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("bytes_in", sa.BigInteger(), nullable=False),
        sa.Column("bytes_out_reserved", sa.BigInteger(), nullable=False),
        sa.Column("cost_units", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("result_digest", sa.String(64), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            f"action IN ({_WORKSPACE_DATA_ACTIONS})",
            name="workspace_data_usage_reservations_action_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'committed', 'unknown')",
            name="workspace_data_usage_reservations_state_check",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="workspace_data_usage_reservations_request_hash_check",
        ),
        sa.CheckConstraint(
            "workload_identity_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_data_usage_reservations_workload_digest_check",
        ),
        sa.CheckConstraint(
            "resource_version >= 1",
            name="workspace_data_usage_reservations_resource_version_check",
        ),
        sa.CheckConstraint(
            "calls = 1 AND bytes_in >= 0 AND bytes_out_reserved >= 0 AND cost_units > 0",
            name="workspace_data_usage_reservations_budget_check",
        ),
        sa.CheckConstraint(
            "result_digest IS NULL OR result_digest ~ '^[0-9a-f]{64}$'",
            name="workspace_data_usage_reservations_result_digest_check",
        ),
        sa.CheckConstraint(
            "(state = 'committed' AND result_digest IS NOT NULL) OR "
            "(state IN ('pending', 'unknown') AND result_digest IS NULL)",
            name="workspace_data_usage_reservations_state_result_check",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id", "tenant_id"],
            [f"{_SCHEMA}.capability_grants.id", f"{_SCHEMA}.capability_grants.tenant_id"],
            name="workspace_data_usage_reservations_grant_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [f"{_SCHEMA}.operations.id", f"{_SCHEMA}.operations.tenant_id"],
            name="workspace_data_usage_reservations_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{_SCHEMA}.workspaces.id", f"{_SCHEMA}.workspaces.tenant_id"],
            name="workspace_data_usage_reservations_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            [f"{_SCHEMA}.resource_registry.id", f"{_SCHEMA}.resource_registry.tenant_id"],
            name="workspace_data_usage_reservations_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "workspace_data_usage_reservations_tenant_grant_created_idx",
        "workspace_data_usage_reservations",
        ["tenant_id", "grant_id", "created_at"],
        schema=_SCHEMA,
    )

    _install_global_triggers()


def _install_global_triggers() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION omnibase_meta.prevent_p34_6_append_only_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'P34.6 append-only record cannot be changed' USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER resource_lineage_append_only
        BEFORE UPDATE OR DELETE ON omnibase_meta.resource_lineage
        FOR EACH ROW EXECUTE FUNCTION omnibase_meta.prevent_p34_6_append_only_mutation();

        CREATE TRIGGER workspace_snapshot_items_append_only
        BEFORE UPDATE OR DELETE ON omnibase_meta.workspace_snapshot_items
        FOR EACH ROW EXECUTE FUNCTION omnibase_meta.prevent_p34_6_append_only_mutation();

        CREATE OR REPLACE FUNCTION omnibase_meta.guard_workspace_snapshot_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'workspace snapshot cannot be deleted' USING ERRCODE = '55000';
            END IF;
            IF NEW.id IS DISTINCT FROM OLD.id OR
               NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
               NEW.workspace_id IS DISTINCT FROM OLD.workspace_id OR
               NEW.source_generation IS DISTINCT FROM OLD.source_generation OR
               NEW.manifest_digest IS DISTINCT FROM OLD.manifest_digest OR
               NEW.metadata IS DISTINCT FROM OLD.metadata OR
               NEW.created_by_user_id IS DISTINCT FROM OLD.created_by_user_id OR
               NEW.created_at IS DISTINCT FROM OLD.created_at THEN
                RAISE EXCEPTION 'workspace snapshot binding is immutable' USING ERRCODE = '55000';
            END IF;
            IF NEW.state = OLD.state THEN
                RETURN NEW;
            END IF;
            IF (OLD.state = 'building' AND NEW.state IN ('ready', 'failed')) OR
               (OLD.state = 'ready' AND NEW.state = 'revoked') THEN
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'invalid workspace snapshot transition' USING ERRCODE = '55000';
        END;
        $$;

        CREATE TRIGGER workspace_snapshots_transition_guard
        BEFORE UPDATE OR DELETE ON omnibase_meta.workspace_snapshots
        FOR EACH ROW EXECUTE FUNCTION omnibase_meta.guard_workspace_snapshot_transition();

        CREATE OR REPLACE FUNCTION omnibase_meta.reject_resource_lineage_cycle()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            PERFORM pg_advisory_xact_lock(
                hashtextextended('omnibase:resource-lineage:' || NEW.tenant_id::text, 0)
            );
            IF EXISTS (
                WITH RECURSIVE reachable(resource_id) AS (
                    SELECT NEW.derived_resource_id
                    UNION
                    SELECT rl.derived_resource_id
                    FROM omnibase_meta.resource_lineage rl
                    JOIN reachable r ON rl.source_resource_id = r.resource_id
                    WHERE rl.tenant_id = NEW.tenant_id
                )
                SELECT 1 FROM reachable WHERE resource_id = NEW.source_resource_id
            ) THEN
                RAISE EXCEPTION 'resource lineage cycle rejected' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER resource_lineage_cycle_guard
        BEFORE INSERT ON omnibase_meta.resource_lineage
        FOR EACH ROW EXECUTE FUNCTION omnibase_meta.reject_resource_lineage_cycle();

        CREATE OR REPLACE FUNCTION omnibase_meta.guard_resource_registry_immutability()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.policy_class IS DISTINCT FROM OLD.policy_class THEN
                RAISE EXCEPTION 'resource policy_class is immutable' USING ERRCODE = '55000';
            END IF;
            IF OLD.policy_class = 'canonical_readonly' AND (
                NEW.physical_locator IS DISTINCT FROM OLD.physical_locator OR
                NEW.owner_type IS DISTINCT FROM OLD.owner_type OR
                NEW.owner_id IS DISTINCT FROM OLD.owner_id OR
                NEW.parent_id IS DISTINCT FROM OLD.parent_id
            ) THEN
                RAISE EXCEPTION 'canonical resource binding is immutable' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER resource_registry_immutability_guard
        BEFORE UPDATE ON omnibase_meta.resource_registry
        FOR EACH ROW EXECUTE FUNCTION omnibase_meta.guard_resource_registry_immutability();

        CREATE OR REPLACE FUNCTION omnibase_meta.guard_p34_6_effect_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'P34.6 effect cannot be deleted' USING ERRCODE = '55000';
            END IF;
            IF NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
               NEW.workspace_id IS DISTINCT FROM OLD.workspace_id OR
               NEW.resource_id IS DISTINCT FROM OLD.resource_id OR
               NEW.operation_id IS DISTINCT FROM OLD.operation_id OR
               NEW.sequence IS DISTINCT FROM OLD.sequence OR
               NEW.effect_kind IS DISTINCT FROM OLD.effect_kind OR
               NEW.binding_digest IS DISTINCT FROM OLD.binding_digest THEN
                RAISE EXCEPTION 'P34.6 effect binding is immutable' USING ERRCODE = '55000';
            END IF;
            IF OLD.state <> 'pending' OR NEW.state NOT IN ('committed', 'failed', 'unknown') THEN
                RAISE EXCEPTION 'invalid P34.6 effect transition' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER workspace_data_effects_transition_guard
        BEFORE UPDATE OR DELETE ON omnibase_meta.workspace_data_effects
        FOR EACH ROW EXECUTE FUNCTION omnibase_meta.guard_p34_6_effect_transition();

        CREATE OR REPLACE FUNCTION omnibase_meta.guard_workspace_data_reservation_transition()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'workspace data reservation cannot be deleted' USING ERRCODE = '55000';
            END IF;
            IF NEW.operation_id IS DISTINCT FROM OLD.operation_id OR
               NEW.tenant_id IS DISTINCT FROM OLD.tenant_id OR
               NEW.grant_id IS DISTINCT FROM OLD.grant_id OR
               NEW.grant_version IS DISTINCT FROM OLD.grant_version OR
               NEW.workspace_id IS DISTINCT FROM OLD.workspace_id OR
               NEW.runtime_instance_id IS DISTINCT FROM OLD.runtime_instance_id OR
               NEW.workload_identity_digest IS DISTINCT FROM OLD.workload_identity_digest OR
               NEW.action IS DISTINCT FROM OLD.action OR
               NEW.resource_id IS DISTINCT FROM OLD.resource_id OR
               NEW.resource_version IS DISTINCT FROM OLD.resource_version OR
               NEW.request_hash IS DISTINCT FROM OLD.request_hash OR
               NEW.calls IS DISTINCT FROM OLD.calls OR
               NEW.bytes_in IS DISTINCT FROM OLD.bytes_in OR
               NEW.bytes_out_reserved IS DISTINCT FROM OLD.bytes_out_reserved OR
               NEW.cost_units IS DISTINCT FROM OLD.cost_units THEN
                RAISE EXCEPTION 'workspace data reservation binding is immutable' USING ERRCODE = '55000';
            END IF;
            IF OLD.state <> 'pending' OR NEW.state NOT IN ('committed', 'unknown') THEN
                RAISE EXCEPTION 'invalid workspace data reservation transition' USING ERRCODE = '55000';
            END IF;
            RETURN NEW;
        END;
        $$;

        CREATE TRIGGER workspace_data_usage_reservations_transition_guard
        BEFORE UPDATE OR DELETE ON omnibase_meta.workspace_data_usage_reservations
        FOR EACH ROW EXECUTE FUNCTION omnibase_meta.guard_workspace_data_reservation_transition();
        """
    )


def _upgrade_tenant() -> None:
    op.create_table(
        "workspace_derived_chunks_v2",
        sa.Column("id", _UUID, primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("derived_index_id", _UUID, nullable=False),
        sa.Column("generation", _UUID, nullable=False),
        sa.Column("source_resource_id", _UUID, nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("tsv", postgresql.TSVECTOR(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("chunk_type", sa.String(20), nullable=False, server_default="paragraph"),
        sa.Column(
            "metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        _created_at(),
        sa.CheckConstraint("chunk_index >= 0", name="workspace_derived_chunks_v2_index_check"),
        sa.CheckConstraint(
            "content_digest ~ '^[0-9a-f]{64}$'", name="workspace_derived_chunks_v2_digest_check"
        ),
        sa.CheckConstraint(
            "char_start IS NULL OR char_start >= 0",
            name="workspace_derived_chunks_v2_char_start_check",
        ),
        sa.CheckConstraint(
            "char_end IS NULL OR (char_start IS NOT NULL AND char_end >= char_start)",
            name="workspace_derived_chunks_v2_char_end_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata) = 'object'", name="workspace_derived_chunks_v2_metadata_check"
        ),
        sa.UniqueConstraint(
            "derived_index_id",
            "generation",
            "chunk_index",
            name="workspace_derived_chunks_v2_generation_chunk_uq",
        ),
    )
    op.create_index(
        "workspace_derived_chunks_v2_scope_idx",
        "workspace_derived_chunks_v2",
        ["workspace_id", "derived_index_id", "generation"],
    )
    op.create_index(
        "workspace_derived_chunks_v2_tsv_idx",
        "workspace_derived_chunks_v2",
        ["tsv"],
        postgresql_using="gin",
    )
    op.create_index(
        "workspace_derived_chunks_v2_embedding_hnsw_idx",
        "workspace_derived_chunks_v2",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"m": 16, "ef_construction": 64},
    )


def downgrade() -> None:
    if _migration_schema_scope() == "tenant":
        _downgrade_tenant()
    else:
        _downgrade_global()


def _downgrade_tenant() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM workspace_derived_chunks_v2) THEN
                RAISE EXCEPTION 'refusing populated P34.6 tenant downgrade' USING ERRCODE = '55000';
            END IF;
        END $$;
        """
    )
    op.drop_index(
        "workspace_derived_chunks_v2_embedding_hnsw_idx", table_name="workspace_derived_chunks_v2"
    )
    op.drop_index("workspace_derived_chunks_v2_tsv_idx", table_name="workspace_derived_chunks_v2")
    op.drop_index("workspace_derived_chunks_v2_scope_idx", table_name="workspace_derived_chunks_v2")
    op.drop_table("workspace_derived_chunks_v2")


def _downgrade_global() -> None:
    op.execute(
        """
        DO $$ BEGIN
            IF EXISTS (SELECT 1 FROM omnibase_meta.workspace_artifacts)
               OR EXISTS (SELECT 1 FROM omnibase_meta.workspace_derived_indexes)
               OR EXISTS (SELECT 1 FROM omnibase_meta.workspace_publications)
               OR EXISTS (SELECT 1 FROM omnibase_meta.workspace_snapshot_items)
               OR EXISTS (SELECT 1 FROM omnibase_meta.workspace_data_effects)
               OR EXISTS (SELECT 1 FROM omnibase_meta.workspace_data_usage_reservations)
               OR EXISTS (
                    SELECT 1 FROM omnibase_meta.capability_grants
                    WHERE actions && ARRAY[
                        'data.rows.insert', 'data.rows.update', 'data.rows.delete',
                        'artifact.read', 'artifact.write', 'rag.derived.create',
                        'rag.derived.delete'
                    ]::varchar[]
               )
               OR EXISTS (
                    SELECT 1 FROM omnibase_meta.workspace_snapshots
                    WHERE state IN ('building', 'failed')
               ) THEN
                RAISE EXCEPTION 'refusing populated P34.6 global downgrade' USING ERRCODE = '55000';
            END IF;
        END $$;
        """
    )
    op.execute(
        "DROP TRIGGER IF EXISTS workspace_data_usage_reservations_transition_guard ON omnibase_meta.workspace_data_usage_reservations"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS workspace_data_effects_transition_guard ON omnibase_meta.workspace_data_effects"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS workspace_snapshot_items_append_only ON omnibase_meta.workspace_snapshot_items"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS workspace_snapshots_transition_guard ON omnibase_meta.workspace_snapshots"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS resource_lineage_cycle_guard ON omnibase_meta.resource_lineage"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS resource_lineage_append_only ON omnibase_meta.resource_lineage"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS resource_registry_immutability_guard ON omnibase_meta.resource_registry"
    )
    for function in (
        "guard_workspace_data_reservation_transition",
        "guard_p34_6_effect_transition",
        "guard_workspace_snapshot_transition",
        "guard_resource_registry_immutability",
        "reject_resource_lineage_cycle",
        "prevent_p34_6_append_only_mutation",
    ):
        op.execute(f"DROP FUNCTION IF EXISTS omnibase_meta.{function}()")

    for table in (
        "workspace_data_usage_reservations",
        "workspace_data_effects",
        "workspace_snapshot_items",
        "workspace_publications",
        "workspace_derived_indexes",
        "workspace_artifacts",
    ):
        op.drop_table(table, schema=_SCHEMA)

    op.alter_column("workspace_snapshots", "state", schema=_SCHEMA, server_default="ready")
    op.drop_constraint(
        "workspace_snapshots_state_check", "workspace_snapshots", schema=_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "workspace_snapshots_state_check",
        "workspace_snapshots",
        "state IN ('ready', 'revoked')",
        schema=_SCHEMA,
    )
    op.drop_constraint(
        "data_table_bindings_workspace_tenant_fk",
        "data_table_bindings",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint(
        "data_table_bindings_resource_tenant_fk",
        "data_table_bindings",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.create_foreign_key(
        "data_table_bindings_resource_id_fkey",
        "data_table_bindings",
        "resource_registry",
        ["resource_id"],
        ["id"],
        source_schema=_SCHEMA,
        referent_schema=_SCHEMA,
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "resource_lineage_operation_tenant_fk",
        "resource_lineage",
        schema=_SCHEMA,
        type_="foreignkey",
    )
    op.drop_constraint("operations_id_tenant_uq", "operations", schema=_SCHEMA, type_="unique")
    op.drop_constraint(
        "approval_requests_id_tenant_uq",
        "approval_requests",
        schema=_SCHEMA,
        type_="unique",
    )
    op.drop_constraint(
        "capability_grants_action_profile_check", "capability_grants", schema=_SCHEMA, type_="check"
    )
    op.create_check_constraint(
        "capability_grants_action_profile_check",
        "capability_grants",
        "cardinality(actions) > 0 AND ((actions <@ ARRAY["
        f"{_READ_ACTIONS}]::varchar[] AND workload_identity_digest IS NULL) OR "
        "(actions <@ ARRAY["
        f"{_SANDBOX_ACTIONS}]::varchar[] AND workload_identity_digest IS NOT NULL "
        "AND workload_identity_digest ~ '^[0-9a-f]{64}$' AND cardinality(resource_ids) = 1 "
        "AND delegation_depth = 0 AND delegation_depth_limit = 0))",
        schema=_SCHEMA,
    )
