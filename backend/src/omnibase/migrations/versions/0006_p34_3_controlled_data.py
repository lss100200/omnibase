"""Add the P34.3 controlled-data persistence foundation.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-31 19:00:00

The global pass creates logical bindings and orchestration metadata in
``omnibase_meta``.  Each tenant pass creates only the private operation
payload table.  This migration never creates a user-defined data table and
never changes business data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "omnibase_meta"
_UUID = postgresql.UUID(as_uuid=False)
_JSONB = postgresql.JSONB(astext_type=sa.Text())


def _id_column() -> sa.Column:
    return sa.Column(
        "id",
        _UUID,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _tenant_id_column() -> sa.Column:
    return sa.Column(
        "tenant_id",
        _UUID,
        sa.ForeignKey(f"{_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


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


def upgrade() -> None:
    """Create only the tables belonging to the active migration scope."""
    scope = op.get_context().config.attributes.get("migration_schema_scope")
    if scope == "tenant":
        _upgrade_tenant()
        return
    if scope == "global":
        _upgrade_global()
        return
    raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")


def _upgrade_global() -> None:
    op.create_table(
        "data_table_bindings",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "resource_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.resource_registry.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("policy_class", sa.String(32), nullable=False),
        sa.Column("physical_table_name", sa.String(63), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("resource_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_by_actor_id", _UUID, nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "policy_class IN ('workspace_private', 'tenant_managed', " "'controlled_shared')",
            name="data_table_bindings_policy_check",
        ),
        sa.CheckConstraint(
            "policy_class <> 'workspace_private' OR workspace_id IS NOT NULL",
            name="data_table_bindings_workspace_private_check",
        ),
        sa.CheckConstraint(
            "physical_table_name ~ '^odt_[0-9a-f]{32}$'",
            name="data_table_bindings_physical_name_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'archived')",
            name="data_table_bindings_state_check",
        ),
        sa.CheckConstraint(
            "resource_version >= 1 AND version >= 1",
            name="data_table_bindings_version_check",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="data_table_bindings_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id",
            "resource_id",
            name="data_table_bindings_tenant_resource_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "physical_table_name",
            name="data_table_bindings_tenant_physical_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "data_table_bindings_tenant_workspace_idx",
        "data_table_bindings",
        ["tenant_id", "workspace_id", "state"],
        schema=_SCHEMA,
    )
    op.create_index(
        "data_table_bindings_tenant_policy_idx",
        "data_table_bindings",
        ["tenant_id", "policy_class", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "data_column_bindings",
        _id_column(),
        _tenant_id_column(),
        sa.Column("table_binding_id", _UUID, nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("physical_column_name", sa.String(63), nullable=False),
        sa.Column("data_type", sa.String(24), nullable=False),
        sa.Column(
            "type_args",
            _JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("nullable", sa.Boolean(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "physical_column_name ~ '^odc_[0-9a-f]{32}$'",
            name="data_column_bindings_physical_name_check",
        ),
        sa.CheckConstraint(
            "data_type IN ('string', 'int64', 'decimal', 'boolean', 'uuid', "
            "'date', 'timestamp_tz')",
            name="data_column_bindings_type_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(type_args) = 'object'",
            name="data_column_bindings_type_args_object_check",
        ),
        sa.CheckConstraint(
            "(data_type = 'string' AND type_args ? 'max_length' "
            "AND type_args - 'max_length' = '{}'::jsonb "
            "AND jsonb_typeof(type_args -> 'max_length') = 'number' "
            "AND (type_args ->> 'max_length')::numeric = "
            "trunc((type_args ->> 'max_length')::numeric) "
            "AND (type_args ->> 'max_length')::numeric BETWEEN 1 AND 10000) OR "
            "(data_type = 'decimal' AND type_args ?& ARRAY['precision', 'scale'] "
            "AND type_args - ARRAY['precision', 'scale'] = '{}'::jsonb "
            "AND jsonb_typeof(type_args -> 'precision') = 'number' "
            "AND jsonb_typeof(type_args -> 'scale') = 'number' "
            "AND (type_args ->> 'precision')::numeric = "
            "trunc((type_args ->> 'precision')::numeric) "
            "AND (type_args ->> 'scale')::numeric = "
            "trunc((type_args ->> 'scale')::numeric) "
            "AND (type_args ->> 'precision')::numeric BETWEEN 1 AND 38 "
            "AND (type_args ->> 'scale')::numeric BETWEEN 0 "
            "AND (type_args ->> 'precision')::numeric) OR "
            "(data_type IN ('int64', 'boolean', 'uuid', 'date', 'timestamp_tz') "
            "AND type_args = '{}'::jsonb)",
            name="data_column_bindings_type_args_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'archived')",
            name="data_column_bindings_state_check",
        ),
        sa.CheckConstraint(
            "ordinal >= 1 AND version >= 1",
            name="data_column_bindings_position_version_check",
        ),
        sa.ForeignKeyConstraint(
            ["table_binding_id", "tenant_id"],
            [
                f"{_SCHEMA}.data_table_bindings.id",
                f"{_SCHEMA}.data_table_bindings.tenant_id",
            ],
            name="data_column_bindings_table_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="data_column_bindings_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id",
            "table_binding_id",
            "physical_column_name",
            name="data_column_bindings_table_physical_uq",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "table_binding_id",
            "ordinal",
            name="data_column_bindings_table_ordinal_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "data_column_bindings_table_state_idx",
        "data_column_bindings",
        ["tenant_id", "table_binding_id", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "data_index_bindings",
        _id_column(),
        _tenant_id_column(),
        sa.Column("table_binding_id", _UUID, nullable=False),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("physical_index_name", sa.String(63), nullable=False),
        sa.Column("column_ids", postgresql.ARRAY(_UUID), nullable=False),
        sa.Column("method", sa.String(16), nullable=False, server_default="btree"),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "physical_index_name ~ '^odi_[0-9a-f]{32}$'",
            name="data_index_bindings_physical_name_check",
        ),
        sa.CheckConstraint("method = 'btree'", name="data_index_bindings_method_check"),
        sa.CheckConstraint(
            "cardinality(column_ids) BETWEEN 1 AND 8",
            name="data_index_bindings_columns_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'active', 'archived')",
            name="data_index_bindings_state_check",
        ),
        sa.CheckConstraint("version >= 1", name="data_index_bindings_version_check"),
        sa.ForeignKeyConstraint(
            ["table_binding_id", "tenant_id"],
            [
                f"{_SCHEMA}.data_table_bindings.id",
                f"{_SCHEMA}.data_table_bindings.tenant_id",
            ],
            name="data_index_bindings_table_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="data_index_bindings_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id",
            "table_binding_id",
            "physical_index_name",
            name="data_index_bindings_table_physical_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "data_index_bindings_table_state_idx",
        "data_index_bindings",
        ["tenant_id", "table_binding_id", "state"],
        schema=_SCHEMA,
    )

    op.create_table(
        "authorization_contexts",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("actor_user_id", _UUID, nullable=False),
        sa.Column("grant_id", _UUID, nullable=True),
        sa.Column(
            "role_snapshot",
            postgresql.ARRAY(sa.String(32)),
            nullable=False,
            server_default=sa.text("ARRAY[]::varchar[]"),
        ),
        sa.Column("actions", postgresql.ARRAY(sa.String(100)), nullable=False),
        sa.Column("resource_ids", postgresql.ARRAY(_UUID), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(64), nullable=False),
        sa.Column(
            "live_recheck_required",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        _created_at_column(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('capability', 'user_rbac')",
            name="authorization_contexts_source_check",
        ),
        sa.CheckConstraint(
            "(source = 'capability' AND grant_id IS NOT NULL) OR "
            "(source = 'user_rbac' AND grant_id IS NULL)",
            name="authorization_contexts_source_binding_check",
        ),
        sa.CheckConstraint(
            "actor_user_id IS NOT NULL",
            name="authorization_contexts_actor_check",
        ),
        sa.CheckConstraint(
            "cardinality(actions) > 0 AND cardinality(resource_ids) > 0",
            name="authorization_contexts_scope_check",
        ),
        sa.CheckConstraint(
            "source_version >= 1 AND live_recheck_required IS TRUE",
            name="authorization_contexts_live_recheck_check",
        ),
        sa.CheckConstraint(
            "snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="authorization_contexts_snapshot_hash_check",
        ),
        sa.CheckConstraint(
            "expires_at > created_at",
            name="authorization_contexts_expiry_check",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id", "tenant_id"],
            [
                f"{_SCHEMA}.capability_grants.id",
                f"{_SCHEMA}.capability_grants.tenant_id",
            ],
            name="authorization_contexts_grant_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="authorization_contexts_id_tenant_uq"),
        schema=_SCHEMA,
    )
    op.create_index(
        "authorization_contexts_tenant_workspace_idx",
        "authorization_contexts",
        ["tenant_id", "workspace_id", "expires_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "authorization_contexts_grant_idx",
        "authorization_contexts",
        ["grant_id"],
        schema=_SCHEMA,
        postgresql_where=sa.text("grant_id IS NOT NULL"),
    )

    op.create_table(
        "schema_change_plans",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("table_binding_id", _UUID, nullable=True),
        sa.Column("authorization_context_id", _UUID, nullable=False),
        sa.Column(
            "operation_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.operations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "approval_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.approval_requests.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("normalized_spec", _JSONB, nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("risk_level", sa.String(2), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "kind IN ('create_table', 'add_nullable_column', "
            "'rename_table_display', 'rename_column_display', "
            "'create_btree_index')",
            name="schema_change_plans_kind_check",
        ),
        sa.CheckConstraint(
            "kind = 'create_table' OR table_binding_id IS NOT NULL",
            name="schema_change_plans_target_check",
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'validated', 'pending_approval', 'approved', "
            "'applying', 'applied', 'rejected', 'expired', 'failed', "
            "'compensating', 'compensated', 'manual_intervention_required')",
            name="schema_change_plans_state_check",
        ),
        sa.CheckConstraint(
            "risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')",
            name="schema_change_plans_risk_check",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="schema_change_plans_request_hash_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(normalized_spec) = 'object' "
            "AND NOT (normalized_spec ? 'sql') "
            "AND NOT (normalized_spec ? 'raw_sql')",
            name="schema_change_plans_no_sql_check",
        ),
        sa.CheckConstraint(
            "base_version IS NULL OR base_version >= 1",
            name="schema_change_plans_base_version_check",
        ),
        sa.CheckConstraint("version >= 1", name="schema_change_plans_version_check"),
        sa.CheckConstraint(
            "(requires_approval IS FALSE) OR approval_id IS NOT NULL "
            "OR state IN ('draft', 'validated', 'pending_approval', 'rejected', "
            "'expired', 'failed')",
            name="schema_change_plans_approval_check",
        ),
        sa.ForeignKeyConstraint(
            ["authorization_context_id", "tenant_id"],
            [
                f"{_SCHEMA}.authorization_contexts.id",
                f"{_SCHEMA}.authorization_contexts.tenant_id",
            ],
            name="schema_change_plans_authorization_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["table_binding_id", "tenant_id"],
            [
                f"{_SCHEMA}.data_table_bindings.id",
                f"{_SCHEMA}.data_table_bindings.tenant_id",
            ],
            name="schema_change_plans_table_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="schema_change_plans_id_tenant_uq"),
        sa.UniqueConstraint(
            "tenant_id",
            "operation_id",
            name="schema_change_plans_operation_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "schema_change_plans_tenant_state_created_idx",
        "schema_change_plans",
        ["tenant_id", "state", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "schema_change_plans_table_created_idx",
        "schema_change_plans",
        ["tenant_id", "table_binding_id", "created_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "operation_dispatch_outbox",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "operation_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_id", _UUID, nullable=True),
        sa.Column("payload_id", _UUID, nullable=False),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column("dedupe_key", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "event_type IN ('crud_mutation', 'schema_change', 'compensation')",
            name="operation_dispatch_outbox_event_type_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'leased', 'dispatched', 'failed', 'dead_letter')",
            name="operation_dispatch_outbox_state_check",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 20",
            name="operation_dispatch_outbox_attempts_check",
        ),
        sa.CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'",
            name="operation_dispatch_outbox_dedupe_check",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "dedupe_key",
            name="operation_dispatch_outbox_tenant_dedupe_uq",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "tenant_id"],
            [
                f"{_SCHEMA}.schema_change_plans.id",
                f"{_SCHEMA}.schema_change_plans.tenant_id",
            ],
            name="operation_dispatch_outbox_plan_tenant_fk",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("id", "tenant_id", name="operation_dispatch_outbox_id_tenant_uq"),
        schema=_SCHEMA,
    )
    op.create_index(
        "operation_dispatch_outbox_ready_idx",
        "operation_dispatch_outbox",
        ["state", "available_at"],
        schema=_SCHEMA,
        postgresql_where=sa.text("state IN ('pending', 'failed')"),
    )
    op.create_index(
        "operation_dispatch_outbox_tenant_operation_idx",
        "operation_dispatch_outbox",
        ["tenant_id", "operation_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "operation_compensations",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "operation_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.operations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("plan_id", _UUID, nullable=True),
        sa.Column("payload_id", _UUID, nullable=False),
        sa.Column("target_logical_id", _UUID, nullable=False),
        sa.Column("plan_digest", sa.String(64), nullable=False),
        sa.Column("resource_version", sa.Integer(), nullable=False),
        sa.Column(
            "before_snapshot",
            _JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "kind IN ('drop_created_table', 'drop_added_column', "
            "'drop_created_index', 'restore_display_name')",
            name="operation_compensations_kind_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'failed', "
            "'manual_intervention_required')",
            name="operation_compensations_state_check",
        ),
        sa.CheckConstraint(
            "sequence >= 1 AND attempt_count >= 0",
            name="operation_compensations_sequence_attempts_check",
        ),
        sa.CheckConstraint(
            "plan_digest ~ '^[0-9a-f]{64}$' AND resource_version >= 1",
            name="operation_compensations_binding_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(before_snapshot) = 'object' "
            "AND NOT (before_snapshot ? 'sql') "
            "AND NOT (before_snapshot ? 'raw_sql') "
            "AND ((kind = 'restore_display_name' "
            "AND before_snapshot ? 'display_name' "
            "AND before_snapshot - 'display_name' = '{}'::jsonb) "
            "OR (kind <> 'restore_display_name' AND before_snapshot = '{}'::jsonb))",
            name="operation_compensations_snapshot_check",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "operation_id",
            "sequence",
            name="operation_compensations_operation_sequence_uq",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "tenant_id"],
            [
                f"{_SCHEMA}.schema_change_plans.id",
                f"{_SCHEMA}.schema_change_plans.tenant_id",
            ],
            name="operation_compensations_plan_tenant_fk",
            ondelete="CASCADE",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "operation_compensations_operation_state_idx",
        "operation_compensations",
        ["tenant_id", "operation_id", "state"],
        schema=_SCHEMA,
    )


def _upgrade_tenant() -> None:
    op.create_table(
        "controlled_data_operation_payloads",
        _id_column(),
        sa.Column("operation_id", _UUID, nullable=False),
        sa.Column("plan_id", _UUID, nullable=True),
        sa.Column("payload_kind", sa.String(24), nullable=False),
        sa.Column("normalized_payload", _JSONB, nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="pending"),
        _created_at_column(),
        _updated_at_column(),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "payload_kind IN ('crud_mutation', 'schema_change', 'compensation')",
            name="controlled_data_operation_payloads_kind_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'claimed', 'applied', 'compensated', 'discarded')",
            name="controlled_data_operation_payloads_state_check",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="controlled_data_operation_payloads_request_hash_check",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(normalized_payload) = 'object' "
            "AND NOT (normalized_payload ? 'sql') "
            "AND NOT (normalized_payload ? 'raw_sql')",
            name="controlled_data_operation_payloads_no_sql_check",
        ),
    )
    op.create_index(
        "controlled_data_operation_payloads_operation_idx",
        "controlled_data_operation_payloads",
        ["operation_id", "created_at"],
    )
    op.create_index(
        "controlled_data_operation_payloads_state_expiry_idx",
        "controlled_data_operation_payloads",
        ["state", "expires_at"],
    )


def downgrade() -> None:
    """Refuse to discard any dynamic resource or pending tenant payload."""
    scope = op.get_context().config.attributes.get("migration_schema_scope")
    if scope == "tenant":
        _downgrade_tenant()
        return
    if scope == "global":
        _downgrade_global()
        return
    raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")


def _downgrade_tenant() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM controlled_data_operation_payloads LIMIT 1
            ) OR EXISTS (
                SELECT 1
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                  AND c.relkind IN ('r', 'p', 'i')
                  AND c.relname ~ '^(odt|odi)_[0-9a-f]{32}$'
            ) THEN
                RAISE EXCEPTION
                    'P34.3 downgrade refused: tenant dynamic resources or payloads exist'
                    USING ERRCODE = '55000';
            END IF;
        END;
        $$
        """
    )
    op.drop_table("controlled_data_operation_payloads")


def _downgrade_global() -> None:
    op.execute(
        """
        DO $$
        DECLARE
            tenant_schema text;
            has_payload boolean;
        BEGIN
            FOR tenant_schema IN
                SELECT schema_name FROM omnibase_meta.tenants
            LOOP
                IF to_regclass(format('%I.controlled_data_operation_payloads',
                                      tenant_schema)) IS NOT NULL THEN
                    EXECUTE format(
                        'SELECT EXISTS (SELECT 1 FROM %I.controlled_data_operation_payloads LIMIT 1)',
                        tenant_schema
                    ) INTO has_payload;
                    IF has_payload THEN
                        RAISE EXCEPTION
                            'P34.3 downgrade refused: tenant operation payloads exist'
                            USING ERRCODE = '55000';
                    END IF;
                END IF;
            END LOOP;

            IF EXISTS (
                SELECT 1
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN omnibase_meta.tenants t ON t.schema_name = n.nspname
                WHERE c.relkind IN ('r', 'p', 'i')
                  AND c.relname ~ '^(odt|odi)_[0-9a-f]{32}$'
            ) THEN
                RAISE EXCEPTION
                    'P34.3 downgrade refused: tenant dynamic relations exist'
                    USING ERRCODE = '55000';
            END IF;
        END;
        $$
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM omnibase_meta.data_table_bindings LIMIT 1)
               OR EXISTS (SELECT 1 FROM omnibase_meta.schema_change_plans LIMIT 1)
               OR EXISTS (SELECT 1 FROM omnibase_meta.operation_dispatch_outbox LIMIT 1)
               OR EXISTS (SELECT 1 FROM omnibase_meta.operation_compensations LIMIT 1)
            THEN
                RAISE EXCEPTION
                    'P34.3 downgrade refused: controlled dynamic resources exist'
                    USING ERRCODE = '55000';
            END IF;
        END;
        $$
        """
    )
    op.drop_table("operation_compensations", schema=_SCHEMA)
    op.drop_table("operation_dispatch_outbox", schema=_SCHEMA)
    op.drop_table("schema_change_plans", schema=_SCHEMA)
    op.drop_table("authorization_contexts", schema=_SCHEMA)
    op.drop_table("data_index_bindings", schema=_SCHEMA)
    op.drop_table("data_column_bindings", schema=_SCHEMA)
    op.drop_table("data_table_bindings", schema=_SCHEMA)
