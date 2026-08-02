"""Add P34.5 Sandbox capability profile and durable dispatch ledger.

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-02 01:00:00

This revision is global-scope only. It does not create a Runner, contact a
container runtime, or modify tenant schemas and canonical RAG data.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SCHEMA = "omnibase_meta"
_UUID = postgresql.UUID(as_uuid=False)
_SANDBOX_ACTIONS = (
    "'sandbox.prepare', 'sandbox.create', 'sandbox.start', 'sandbox.exec', "
    "'sandbox.cancel', 'sandbox.logs', 'sandbox.stats', 'sandbox.snapshot', "
    "'sandbox.restore', 'sandbox.stop', 'sandbox.destroy'"
)
_ALL_OPERATION_ACTIONS = (
    f"{_SANDBOX_ACTIONS}, 'sandbox.control.emergency_stop', " "'sandbox.control.emergency_destroy'"
)
_OPERATION_STATES = (
    "'accepted', 'authorized', 'dispatching', 'succeeded', 'failed', "
    "'ambiguous', 'reconciliation_required', 'reconciled_succeeded', "
    "'reconciled_failed'"
)


def _migration_schema_scope() -> str:
    scope = op.get_context().config.attributes.get("migration_schema_scope")
    if scope not in {"global", "tenant"}:
        raise RuntimeError(f"unsupported migration_schema_scope: {scope!r}")
    return scope


def upgrade() -> None:
    """Install global capability and durable Sandbox dispatch records."""
    if _migration_schema_scope() == "tenant":
        return

    op.add_column(
        "capability_grants",
        sa.Column("workload_identity_digest", sa.String(64), nullable=True),
        schema=_SCHEMA,
    )
    op.drop_constraint(
        "capability_grants_read_actions_check",
        "capability_grants",
        schema=_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "capability_grants_action_profile_check",
        "capability_grants",
        "cardinality(actions) > 0 AND ((actions <@ ARRAY["
        "'data.schema.read', 'data.rows.read', 'rag.search', "
        "'rag.citation.read']::varchar[] AND workload_identity_digest IS NULL) "
        "OR (actions <@ ARRAY["
        f"{_SANDBOX_ACTIONS}]::varchar[] AND "
        "workload_identity_digest IS NOT NULL AND "
        "workload_identity_digest ~ '^[0-9a-f]{64}$' AND "
        "cardinality(resource_ids) = 1 AND delegation_depth = 0 AND "
        "delegation_depth_limit = 0))",
        schema=_SCHEMA,
    )

    op.create_table(
        "capability_usage_reservations",
        sa.Column("operation_id", _UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("grant_id", _UUID, nullable=False),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("runtime_instance_id", _UUID, nullable=False),
        sa.Column("action", sa.String(32), nullable=False),
        sa.Column("calls", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("cost_units", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"action IN ({_SANDBOX_ACTIONS})",
            name="capability_usage_reservations_action_check",
        ),
        sa.CheckConstraint(
            "calls = 1 AND cost_units = 1",
            name="capability_usage_reservations_budget_check",
        ),
        sa.ForeignKeyConstraint(
            ["grant_id", "tenant_id"],
            [f"{_SCHEMA}.capability_grants.id", f"{_SCHEMA}.capability_grants.tenant_id"],
            name="capability_usage_reservations_grant_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "capability_usage_reservations_tenant_grant_created_idx",
        "capability_usage_reservations",
        ["tenant_id", "grant_id", "created_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "sandbox_operations",
        sa.Column("operation_id", _UUID, primary_key=True),
        sa.Column(
            "tenant_id",
            _UUID,
            sa.ForeignKey(f"{_SCHEMA}.tenants.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("workspace_id", _UUID, nullable=False),
        sa.Column("run_id", _UUID, nullable=False),
        sa.Column("runtime_instance_id", _UUID, nullable=False),
        sa.Column("capability_grant_id", _UUID, nullable=True),
        sa.Column("action", sa.String(40), nullable=False),
        sa.Column("request_digest", sa.String(64), nullable=False),
        sa.Column("spec_digest", sa.String(64), nullable=True),
        sa.Column("workspace_generation", sa.BigInteger(), nullable=False),
        sa.Column("run_fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("node_fencing_token", sa.BigInteger(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="accepted"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
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
        sa.CheckConstraint(
            f"action IN ({_ALL_OPERATION_ACTIONS})",
            name="sandbox_operations_action_check",
        ),
        sa.CheckConstraint(
            f"state IN ({_OPERATION_STATES})",
            name="sandbox_operations_state_check",
        ),
        sa.CheckConstraint(
            "request_digest ~ '^[0-9a-f]{64}$'",
            name="sandbox_operations_request_digest_check",
        ),
        sa.CheckConstraint(
            "spec_digest IS NULL OR spec_digest ~ '^[0-9a-f]{64}$'",
            name="sandbox_operations_spec_digest_check",
        ),
        sa.CheckConstraint(
            "version >= 1 AND workspace_generation >= 1 AND "
            "run_fencing_token >= 1 AND node_fencing_token >= 1",
            name="sandbox_operations_version_fencing_check",
        ),
        sa.CheckConstraint(
            "((action IN ('sandbox.control.emergency_stop', "
            "'sandbox.control.emergency_destroy')) "
            "AND capability_grant_id IS NULL) OR "
            "((action NOT IN ('sandbox.control.emergency_stop', "
            "'sandbox.control.emergency_destroy')) "
            "AND capability_grant_id IS NOT NULL)",
            name="sandbox_operations_capability_binding_check",
        ),
        sa.UniqueConstraint(
            "operation_id",
            "tenant_id",
            name="sandbox_operations_id_tenant_uq",
        ),
        sa.ForeignKeyConstraint(
            ["capability_grant_id", "tenant_id"],
            [f"{_SCHEMA}.capability_grants.id", f"{_SCHEMA}.capability_grants.tenant_id"],
            name="sandbox_operations_grant_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            [f"{_SCHEMA}.workspaces.tenant_id", f"{_SCHEMA}.workspaces.id"],
            name="sandbox_operations_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id", "run_id"],
            [
                f"{_SCHEMA}.workspace_runs.tenant_id",
                f"{_SCHEMA}.workspace_runs.workspace_id",
                f"{_SCHEMA}.workspace_runs.id",
            ],
            name="sandbox_operations_run_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "sandbox_operations_tenant_state_created_idx",
        "sandbox_operations",
        ["tenant_id", "state", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "sandbox_operations_tenant_run_created_idx",
        "sandbox_operations",
        ["tenant_id", "run_id", "created_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "sandbox_operation_transitions",
        sa.Column("operation_id", _UUID, primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("tenant_id", _UUID, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(100), nullable=False),
        sa.Column("evidence_digest", sa.String(64), nullable=True),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"state IN ({_OPERATION_STATES})",
            name="sandbox_transitions_state_check",
        ),
        sa.CheckConstraint("sequence >= 1", name="sandbox_transitions_sequence_check"),
        sa.CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_]{2,99}$'",
            name="sandbox_transitions_reason_check",
        ),
        sa.CheckConstraint(
            "evidence_digest IS NULL OR evidence_digest ~ '^[0-9a-f]{64}$'",
            name="sandbox_transitions_evidence_check",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [
                f"{_SCHEMA}.sandbox_operations.operation_id",
                f"{_SCHEMA}.sandbox_operations.tenant_id",
            ],
            name="sandbox_transitions_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "sandbox_transitions_tenant_recorded_idx",
        "sandbox_operation_transitions",
        ["tenant_id", "recorded_at"],
        schema=_SCHEMA,
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION omnibase_meta.prevent_p34_5_append_only_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'P34.5 reservation/transition evidence is append-only'
                USING ERRCODE = '55000';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("capability_usage_reservations", "sandbox_operation_transitions"):
        op.execute(
            f"""
            CREATE TRIGGER {table}_append_only
            BEFORE UPDATE OR DELETE ON omnibase_meta.{table}
            FOR EACH ROW EXECUTE FUNCTION omnibase_meta.prevent_p34_5_append_only_mutation()
            """
        )


def downgrade() -> None:
    """Remove P34.5 dispatch schema only when it contains no durable evidence."""
    if _migration_schema_scope() == "tenant":
        return

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM omnibase_meta.sandbox_operations)
               OR EXISTS (SELECT 1 FROM omnibase_meta.sandbox_operation_transitions)
               OR EXISTS (SELECT 1 FROM omnibase_meta.capability_usage_reservations)
                OR EXISTS (
                    SELECT 1 FROM omnibase_meta.capability_grants
                    WHERE actions && ARRAY[
                        'sandbox.prepare', 'sandbox.create', 'sandbox.start',
                        'sandbox.exec', 'sandbox.cancel', 'sandbox.logs',
                        'sandbox.stats', 'sandbox.snapshot', 'sandbox.restore',
                        'sandbox.stop', 'sandbox.destroy'
                    ]::varchar[]
               ) THEN
                RAISE EXCEPTION 'refusing populated P34.5 downgrade'
                    USING ERRCODE = '55000';
            END IF;
        END;
        $$
        """
    )
    for table in ("sandbox_operation_transitions", "capability_usage_reservations"):
        op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON omnibase_meta.{table}")
    op.execute("DROP FUNCTION IF EXISTS omnibase_meta.prevent_p34_5_append_only_mutation()")
    op.drop_table("sandbox_operation_transitions", schema=_SCHEMA)
    op.drop_table("sandbox_operations", schema=_SCHEMA)
    op.drop_table("capability_usage_reservations", schema=_SCHEMA)
    op.drop_constraint(
        "capability_grants_action_profile_check",
        "capability_grants",
        schema=_SCHEMA,
        type_="check",
    )
    op.create_check_constraint(
        "capability_grants_read_actions_check",
        "capability_grants",
        "cardinality(actions) > 0 AND actions <@ ARRAY["
        "'data.schema.read', 'data.rows.read', 'rag.search', "
        "'rag.citation.read']::varchar[]",
        schema=_SCHEMA,
    )
    op.drop_column("capability_grants", "workload_identity_digest", schema=_SCHEMA)
