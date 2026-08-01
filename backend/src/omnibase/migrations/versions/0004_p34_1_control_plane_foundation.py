"""Add the global Phase 3-4 control-plane persistence foundation.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-31 14:00:00

This revision runs only for the global ``omnibase_meta`` migration scope.
Tenant schemas, canonical RAG data, and the Phase 1.6 V1/V2 indexes are
deliberately untouched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
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


def _tenant_id_column() -> sa.Column:
    return sa.Column(
        "tenant_id",
        _UUID,
        sa.ForeignKey(f"{_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )


def _id_column() -> sa.Column:
    return sa.Column(
        "id",
        _UUID,
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
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
    """Create P34.1 control-plane tables in ``omnibase_meta`` only."""
    if _migration_schema_scope() == "tenant":
        return

    op.create_table(
        "resource_registry",
        _id_column(),
        _tenant_id_column(),
        sa.Column(
            "kind",
            sa.String(length=64),
            nullable=False,
            comment="Application-registered logical kind; DB enforces only safe naming",
        ),
        sa.Column("owner_type", sa.String(length=20), nullable=False),
        sa.Column("owner_id", _UUID, nullable=True),
        sa.Column(
            "parent_id",
            _UUID,
            sa.ForeignKey(
                f"{_SCHEMA}.resource_registry.id",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("policy_class", sa.String(length=32), nullable=False),
        sa.Column(
            "physical_locator",
            _JSONB,
            nullable=True,
            comment="Adapter-internal locator; never expose through public DTOs or logs",
        ),
        sa.Column(
            "metadata",
            _JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by_actor_id", _UUID, nullable=True),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "kind ~ '^[a-z][a-z0-9_]{1,63}$'",
            name="resource_registry_kind_check",
        ),
        sa.CheckConstraint(
            "owner_type IN ('user', 'workspace', 'agent', 'system')",
            name="resource_registry_owner_type_check",
        ),
        sa.CheckConstraint(
            "(owner_type = 'system' AND owner_id IS NULL) "
            "OR (owner_type IN ('user', 'workspace', 'agent') "
            "AND owner_id IS NOT NULL)",
            name="resource_registry_owner_identity_check",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'provisioning', 'stopped', 'starting', 'running', "
            "'pausing', 'paused', 'snapshotting', 'stopping', 'archiving', "
            "'archived', 'purge_pending', 'purged', 'failed')",
            name="resource_registry_state_check",
        ),
        sa.CheckConstraint(
            "policy_class IN ('system_internal', 'canonical_readonly', "
            "'tenant_managed', 'controlled_shared', 'workspace_private', "
            "'workspace_derived')",
            name="resource_registry_policy_class_check",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="resource_registry_version_check",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "resource_registry_tenant_kind_state_idx",
        "resource_registry",
        ["tenant_id", "kind", "state"],
        schema=_SCHEMA,
    )
    op.create_index(
        "resource_registry_tenant_owner_idx",
        "resource_registry",
        ["tenant_id", "owner_type", "owner_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "resource_registry_tenant_parent_idx",
        "resource_registry",
        ["tenant_id", "parent_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "resource_registry_tenant_policy_idx",
        "resource_registry",
        ["tenant_id", "policy_class"],
        schema=_SCHEMA,
    )

    op.create_table(
        "resource_lineage",
        _id_column(),
        _tenant_id_column(),
        sa.Column("source_resource_id", _UUID, nullable=False),
        sa.Column("derived_resource_id", _UUID, nullable=False),
        sa.Column("relation", sa.String(length=32), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("transform_digest", sa.String(length=128), nullable=True),
        sa.Column("created_by_operation_id", _UUID, nullable=True),
        _created_at_column(),
        sa.CheckConstraint(
            "relation IN ('derived_from', 'transformed_from', 'snapshot_of', "
            "'restored_from', 'published_from')",
            name="resource_lineage_relation_check",
        ),
        sa.CheckConstraint(
            "source_version >= 1",
            name="resource_lineage_source_version_check",
        ),
        sa.CheckConstraint(
            "source_resource_id <> derived_resource_id",
            name="resource_lineage_distinct_resources_check",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "source_resource_id",
            "derived_resource_id",
            "relation",
            name="resource_lineage_edge_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "resource_lineage_tenant_source_idx",
        "resource_lineage",
        ["tenant_id", "source_resource_id"],
        schema=_SCHEMA,
    )
    op.create_index(
        "resource_lineage_tenant_derived_idx",
        "resource_lineage",
        ["tenant_id", "derived_resource_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "audit_events",
        _id_column(),
        _tenant_id_column(),
        sa.Column("request_id", sa.String(length=64), nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", _UUID, nullable=True),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("run_id", _UUID, nullable=True),
        sa.Column("grant_id", _UUID, nullable=True),
        sa.Column("resource_id", _UUID, nullable=True),
        sa.Column("approval_id", _UUID, nullable=True),
        sa.Column("operation_id", _UUID, nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("risk_level", sa.String(length=2), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("before_version", sa.Integer(), nullable=True),
        sa.Column("after_version", sa.Integer(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("row_count", sa.BigInteger(), nullable=True),
        sa.Column("bytes_in", sa.BigInteger(), nullable=True),
        sa.Column("bytes_out", sa.BigInteger(), nullable=True),
        sa.Column("duration_ms", sa.BigInteger(), nullable=True),
        sa.Column(
            "details",
            _JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _created_at_column(),
        sa.CheckConstraint(
            "actor_type IN ('user', 'workspace', 'run', 'agent', 'system')",
            name="audit_events_actor_type_check",
        ),
        sa.CheckConstraint(
            "decision IN ('allowed', 'denied', 'error')",
            name="audit_events_decision_check",
        ),
        sa.CheckConstraint(
            "risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')",
            name="audit_events_risk_level_check",
        ),
        sa.CheckConstraint(
            "status_code IS NULL OR (status_code >= 100 AND status_code <= 599)",
            name="audit_events_status_code_check",
        ),
        sa.CheckConstraint(
            "before_version IS NULL OR before_version >= 1",
            name="audit_events_before_version_check",
        ),
        sa.CheckConstraint(
            "after_version IS NULL OR after_version >= 1",
            name="audit_events_after_version_check",
        ),
        sa.CheckConstraint(
            "row_count IS NULL OR row_count >= 0",
            name="audit_events_row_count_check",
        ),
        sa.CheckConstraint(
            "bytes_in IS NULL OR bytes_in >= 0",
            name="audit_events_bytes_in_check",
        ),
        sa.CheckConstraint(
            "bytes_out IS NULL OR bytes_out >= 0",
            name="audit_events_bytes_out_check",
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="audit_events_duration_check",
        ),
        schema=_SCHEMA,
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION omnibase_meta.prevent_audit_event_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'omnibase_meta.audit_events is append-only'
                USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_events_append_only
        BEFORE UPDATE OR DELETE ON omnibase_meta.audit_events
        FOR EACH ROW
        EXECUTE FUNCTION omnibase_meta.prevent_audit_event_mutation()
        """
    )
    op.create_index(
        "audit_events_tenant_created_idx",
        "audit_events",
        ["tenant_id", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "audit_events_tenant_actor_idx",
        "audit_events",
        ["tenant_id", "actor_type", "actor_id", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "audit_events_tenant_resource_idx",
        "audit_events",
        ["tenant_id", "resource_id", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "audit_events_request_id_idx",
        "audit_events",
        ["request_id"],
        schema=_SCHEMA,
    )

    op.create_table(
        "operations",
        _id_column(),
        _tenant_id_column(),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("run_id", _UUID, nullable=True),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", _UUID, nullable=True),
        sa.Column("resource_id", _UUID, nullable=True),
        sa.Column("resource_version", sa.Integer(), nullable=True),
        sa.Column("approval_id", _UUID, nullable=True),
        sa.Column(
            "request_hash",
            sa.String(length=64),
            nullable=False,
            comment="Immutable lowercase SHA-256 digest of the authorized request",
        ),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("risk_level", sa.String(length=2), nullable=False),
        sa.Column(
            "progress",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_ref", _JSONB, nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", sa.String(length=1000), nullable=True),
        sa.Column(
            "metadata",
            _JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "actor_type IN ('user', 'workspace', 'agent', 'system')",
            name="operations_actor_type_check",
        ),
        sa.CheckConstraint(
            "state IN ('pending_approval', 'queued', 'running', 'cancelling', "
            "'succeeded', 'failed', 'cancelled', 'compensating', 'compensated')",
            name="operations_state_check",
        ),
        sa.CheckConstraint(
            "risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')",
            name="operations_risk_level_check",
        ),
        sa.CheckConstraint(
            "progress >= 0 AND progress <= 100",
            name="operations_progress_check",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0",
            name="operations_attempt_count_check",
        ),
        sa.CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="operations_request_hash_check",
        ),
        sa.CheckConstraint(
            "resource_version IS NULL OR resource_version >= 1",
            name="operations_resource_version_check",
        ),
        sa.CheckConstraint(
            "risk_level NOT IN ('R2', 'R3', 'R4') "
            "OR state IN ('pending_approval', 'failed', 'cancelled') "
            "OR approval_id IS NOT NULL",
            name="operations_high_risk_approval_check",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="operations_version_check",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "operations_tenant_state_created_idx",
        "operations",
        ["tenant_id", "state", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "operations_tenant_workspace_created_idx",
        "operations",
        ["tenant_id", "workspace_id", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "operations_tenant_resource_created_idx",
        "operations",
        ["tenant_id", "resource_id", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "operations_tenant_request_hash_idx",
        "operations",
        ["tenant_id", "request_hash"],
        schema=_SCHEMA,
    )

    op.create_table(
        "approval_requests",
        _id_column(),
        _tenant_id_column(),
        sa.Column("requester_type", sa.String(length=20), nullable=False),
        sa.Column("requester_id", _UUID, nullable=True),
        sa.Column("workspace_id", _UUID, nullable=True),
        sa.Column("run_id", _UUID, nullable=True),
        sa.Column("resource_id", _UUID, nullable=True),
        sa.Column("operation_id", _UUID, nullable=True),
        sa.Column(
            "grant_id",
            _UUID,
            nullable=True,
            comment="Capability grant bound when the approval is created; immutable thereafter",
        ),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("risk_level", sa.String(length=2), nullable=False),
        sa.Column("required_approver_role", sa.String(length=20), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'draft'"),
        ),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("resource_version", sa.Integer(), nullable=True),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("decided_by_actor_type", sa.String(length=16), nullable=True),
        sa.Column("decided_by_actor_id", _UUID, nullable=True),
        sa.Column("decision_reason", sa.String(length=1000), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "metadata",
            _JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "requester_type IN ('user', 'workspace', 'run', 'agent', 'system')",
            name="approval_requests_requester_type_check",
        ),
        sa.CheckConstraint(
            "requester_type = 'system' OR requester_id IS NOT NULL",
            name="approval_requests_requester_identity_check",
        ),
        sa.CheckConstraint(
            "state IN ('draft', 'pending', 'approved', 'rejected', 'expired', "
            "'cancelled', 'consumed')",
            name="approval_requests_state_check",
        ),
        sa.CheckConstraint(
            "risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')",
            name="approval_requests_risk_level_check",
        ),
        sa.CheckConstraint(
            "resource_version IS NULL OR resource_version >= 1",
            name="approval_requests_resource_version_check",
        ),
        sa.CheckConstraint(
            "decided_by_actor_type IS NULL " "OR decided_by_actor_type IN ('user', 'system')",
            name="approval_requests_decider_type_check",
        ),
        sa.CheckConstraint(
            "(decided_by_actor_type IS NULL AND decided_by_actor_id IS NULL) "
            "OR (decided_by_actor_type IS NOT NULL "
            "AND decided_by_actor_id IS NOT NULL)",
            name="approval_requests_decider_identity_pair_check",
        ),
        sa.CheckConstraint(
            "state NOT IN ('approved', 'rejected', 'consumed') "
            "OR (decided_by_actor_type IS NOT NULL "
            "AND decided_by_actor_id IS NOT NULL)",
            name="approval_requests_decided_state_identity_check",
        ),
        sa.CheckConstraint(
            "required_approver_role IN ('tenant_admin', 'platform_admin')",
            name="approval_requests_required_role_check",
        ),
        sa.CheckConstraint(
            "(risk_level = 'R4' AND required_approver_role = 'platform_admin') "
            "OR (risk_level IN ('R0', 'R1', 'R2', 'R3') "
            "AND required_approver_role = 'tenant_admin')",
            name="approval_requests_risk_role_check",
        ),
        sa.CheckConstraint(
            "state NOT IN ('pending', 'approved', 'rejected', 'consumed') "
            "OR grant_id IS NOT NULL",
            name="approval_requests_committed_grant_check",
        ),
        sa.CheckConstraint(
            "risk_level NOT IN ('R2', 'R3', 'R4') "
            "OR state NOT IN ('pending', 'approved', 'rejected', 'consumed') "
            "OR operation_id IS NOT NULL",
            name="approval_requests_high_risk_operation_check",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="approval_requests_version_check",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "approval_requests_tenant_state_created_idx",
        "approval_requests",
        ["tenant_id", "state", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "approval_requests_tenant_requester_idx",
        "approval_requests",
        ["tenant_id", "requester_type", "requester_id", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "approval_requests_tenant_resource_idx",
        "approval_requests",
        ["tenant_id", "resource_id", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "approval_requests_tenant_role_state_created_idx",
        "approval_requests",
        ["tenant_id", "required_approver_role", "state", "created_at"],
        schema=_SCHEMA,
    )

    op.create_table(
        "idempotency_records",
        _id_column(),
        _tenant_id_column(),
        sa.Column("actor_scope", sa.String(length=128), nullable=False),
        sa.Column("operation_name", sa.String(length=100), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        sa.Column(
            "version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column("response_ref", _JSONB, nullable=True),
        sa.Column("operation_id", _UUID, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        _created_at_column(),
        _updated_at_column(),
        sa.CheckConstraint(
            "state IN ('pending', 'completed', 'failed')",
            name="idempotency_records_state_check",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="idempotency_records_version_check",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "actor_scope",
            "operation_name",
            "key",
            name="idempotency_records_scope_key_uq",
        ),
        schema=_SCHEMA,
    )
    op.create_index(
        "idempotency_records_tenant_state_created_idx",
        "idempotency_records",
        ["tenant_id", "state", "created_at"],
        schema=_SCHEMA,
    )
    op.create_index(
        "idempotency_records_tenant_expires_idx",
        "idempotency_records",
        ["tenant_id", "expires_at"],
        schema=_SCHEMA,
    )


def downgrade() -> None:
    """Drop only P34.1 global tables, preserving all prior schemas and indexes."""
    if _migration_schema_scope() == "tenant":
        return

    op.execute("DROP TRIGGER IF EXISTS audit_events_append_only " "ON omnibase_meta.audit_events")
    op.drop_table("audit_events", schema=_SCHEMA)
    op.execute("DROP FUNCTION IF EXISTS omnibase_meta.prevent_audit_event_mutation()")
    op.drop_table("idempotency_records", schema=_SCHEMA)
    op.drop_table("approval_requests", schema=_SCHEMA)
    op.drop_table("operations", schema=_SCHEMA)
    op.drop_table("resource_lineage", schema=_SCHEMA)
    op.drop_table("resource_registry", schema=_SCHEMA)
