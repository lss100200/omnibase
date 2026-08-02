"""Global persistence models for the P34.3 controlled-data foundation.

These records map logical resources to server-generated physical identifiers
and coordinate authorization, schema plans, dispatch, and compensation.  They
must never be serialized directly as public API responses.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from omnibase.db.models import GLOBAL_SCHEMA, Base

_UUID = UUID(as_uuid=False)
_EMPTY_OBJECT = text("'{}'::jsonb")
_EMPTY_ARRAY = text("'[]'::jsonb")


class DataTableBinding(Base):
    """Logical table resource bound to one deterministic physical table name."""

    __tablename__ = "data_table_bindings"
    __table_args__ = (
        CheckConstraint(
            "policy_class IN ('workspace_private', 'tenant_managed', " "'controlled_shared')",
            name="data_table_bindings_policy_check",
        ),
        CheckConstraint(
            "policy_class <> 'workspace_private' OR workspace_id IS NOT NULL",
            name="data_table_bindings_workspace_private_check",
        ),
        CheckConstraint(
            "physical_table_name ~ '^odt_[0-9a-f]{32}$'",
            name="data_table_bindings_physical_name_check",
        ),
        CheckConstraint(
            "state IN ('pending', 'active', 'archived')",
            name="data_table_bindings_state_check",
        ),
        CheckConstraint(
            "resource_version >= 1 AND version >= 1",
            name="data_table_bindings_version_check",
        ),
        UniqueConstraint("id", "tenant_id", name="data_table_bindings_id_tenant_uq"),
        UniqueConstraint(
            "tenant_id",
            "resource_id",
            name="data_table_bindings_tenant_resource_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "physical_table_name",
            name="data_table_bindings_tenant_physical_uq",
        ),
        ForeignKeyConstraint(
            ["resource_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="data_table_bindings_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspaces.id",
                f"{GLOBAL_SCHEMA}.workspaces.tenant_id",
            ],
            name="data_table_bindings_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index(
            "data_table_bindings_tenant_workspace_idx",
            "tenant_id",
            "workspace_id",
            "state",
        ),
        Index(
            "data_table_bindings_tenant_policy_idx",
            "tenant_id",
            "policy_class",
            "state",
        ),
        {"comment": "Internal logical-to-physical table map; never serialize directly"},
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    resource_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    policy_class: Mapped[str] = mapped_column(String(32), nullable=False)
    physical_table_name: Mapped[str] = mapped_column(String(63), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    resource_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_by_actor_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class DataColumnBinding(Base):
    """Logical column UUID and type bound to a deterministic physical name."""

    __tablename__ = "data_column_bindings"
    __table_args__ = (
        CheckConstraint(
            "physical_column_name ~ '^odc_[0-9a-f]{32}$'",
            name="data_column_bindings_physical_name_check",
        ),
        CheckConstraint(
            "data_type IN ('string', 'int64', 'decimal', 'boolean', 'uuid', "
            "'date', 'timestamp_tz')",
            name="data_column_bindings_type_check",
        ),
        CheckConstraint(
            "jsonb_typeof(type_args) = 'object'",
            name="data_column_bindings_type_args_object_check",
        ),
        CheckConstraint(
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
        CheckConstraint(
            "state IN ('pending', 'active', 'archived')",
            name="data_column_bindings_state_check",
        ),
        CheckConstraint(
            "ordinal >= 1 AND version >= 1",
            name="data_column_bindings_position_version_check",
        ),
        ForeignKeyConstraint(
            ["table_binding_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.data_table_bindings.id",
                f"{GLOBAL_SCHEMA}.data_table_bindings.tenant_id",
            ],
            name="data_column_bindings_table_tenant_fk",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "tenant_id", name="data_column_bindings_id_tenant_uq"),
        UniqueConstraint(
            "tenant_id",
            "table_binding_id",
            "physical_column_name",
            name="data_column_bindings_table_physical_uq",
        ),
        UniqueConstraint(
            "tenant_id",
            "table_binding_id",
            "ordinal",
            name="data_column_bindings_table_ordinal_uq",
        ),
        Index(
            "data_column_bindings_table_state_idx",
            "tenant_id",
            "table_binding_id",
            "state",
        ),
        {"comment": "Internal logical-to-physical column map; no SQL fragments"},
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    table_binding_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    physical_column_name: Mapped[str] = mapped_column(String(63), nullable=False)
    data_type: Mapped[str] = mapped_column(String(24), nullable=False)
    type_args: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_OBJECT
    )
    nullable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class DataIndexBinding(Base):
    """Logical btree index declaration with a server-generated identifier."""

    __tablename__ = "data_index_bindings"
    __table_args__ = (
        CheckConstraint(
            "physical_index_name ~ '^odi_[0-9a-f]{32}$'",
            name="data_index_bindings_physical_name_check",
        ),
        CheckConstraint("method = 'btree'", name="data_index_bindings_method_check"),
        CheckConstraint(
            "cardinality(column_ids) BETWEEN 1 AND 8",
            name="data_index_bindings_columns_check",
        ),
        CheckConstraint(
            "state IN ('pending', 'active', 'archived')",
            name="data_index_bindings_state_check",
        ),
        CheckConstraint("version >= 1", name="data_index_bindings_version_check"),
        ForeignKeyConstraint(
            ["table_binding_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.data_table_bindings.id",
                f"{GLOBAL_SCHEMA}.data_table_bindings.tenant_id",
            ],
            name="data_index_bindings_table_tenant_fk",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "tenant_id", name="data_index_bindings_id_tenant_uq"),
        UniqueConstraint(
            "tenant_id",
            "table_binding_id",
            "physical_index_name",
            name="data_index_bindings_table_physical_uq",
        ),
        Index(
            "data_index_bindings_table_state_idx",
            "tenant_id",
            "table_binding_id",
            "state",
        ),
        {"comment": "P34.3 allows plain btree indexes only"},
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    table_binding_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    physical_index_name: Mapped[str] = mapped_column(String(63), nullable=False)
    column_ids: Mapped[list[str]] = mapped_column(ARRAY(_UUID), nullable=False)
    method: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'btree'"))
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class AuthorizationContext(Base):
    """Immutable authorization snapshot that must be live-rechecked at apply."""

    __tablename__ = "authorization_contexts"
    __table_args__ = (
        CheckConstraint(
            "source IN ('capability', 'user_rbac')",
            name="authorization_contexts_source_check",
        ),
        CheckConstraint(
            "(source = 'capability' AND grant_id IS NOT NULL) OR "
            "(source = 'user_rbac' AND grant_id IS NULL)",
            name="authorization_contexts_source_binding_check",
        ),
        CheckConstraint(
            "actor_user_id IS NOT NULL",
            name="authorization_contexts_actor_check",
        ),
        CheckConstraint(
            "cardinality(actions) > 0 AND cardinality(resource_ids) > 0",
            name="authorization_contexts_scope_check",
        ),
        CheckConstraint(
            "source_version >= 1 AND live_recheck_required IS TRUE",
            name="authorization_contexts_live_recheck_check",
        ),
        CheckConstraint(
            "snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="authorization_contexts_snapshot_hash_check",
        ),
        CheckConstraint(
            "expires_at > created_at",
            name="authorization_contexts_expiry_check",
        ),
        ForeignKeyConstraint(
            ["grant_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.capability_grants.id",
                f"{GLOBAL_SCHEMA}.capability_grants.tenant_id",
            ],
            name="authorization_contexts_grant_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "tenant_id", name="authorization_contexts_id_tenant_uq"),
        Index(
            "authorization_contexts_tenant_workspace_idx",
            "tenant_id",
            "workspace_id",
            "expires_at",
        ),
        Index(
            "authorization_contexts_grant_idx",
            "grant_id",
            postgresql_where=text("grant_id IS NOT NULL"),
        ),
        {"comment": "Snapshot can only narrow authority; apply must re-check live state"},
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    grant_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    role_snapshot: Mapped[list[str]] = mapped_column(
        ARRAY(String(32)), nullable=False, server_default=text("ARRAY[]::varchar[]")
    )
    actions: Mapped[list[str]] = mapped_column(ARRAY(String(100)), nullable=False)
    resource_ids: Mapped[list[str]] = mapped_column(ARRAY(_UUID), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    live_recheck_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SchemaChangePlan(Base):
    """Immutable normalized schema-change intent, never arbitrary SQL."""

    __tablename__ = "schema_change_plans"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('create_table', 'add_nullable_column', "
            "'rename_table_display', 'rename_column_display', "
            "'create_btree_index')",
            name="schema_change_plans_kind_check",
        ),
        CheckConstraint(
            "kind = 'create_table' OR table_binding_id IS NOT NULL",
            name="schema_change_plans_target_check",
        ),
        CheckConstraint(
            "state IN ('draft', 'validated', 'pending_approval', 'approved', "
            "'applying', 'applied', 'rejected', 'expired', 'failed', "
            "'compensating', 'compensated', 'manual_intervention_required')",
            name="schema_change_plans_state_check",
        ),
        CheckConstraint(
            "risk_level IN ('R0', 'R1', 'R2', 'R3', 'R4')",
            name="schema_change_plans_risk_check",
        ),
        CheckConstraint(
            "request_hash ~ '^[0-9a-f]{64}$'",
            name="schema_change_plans_request_hash_check",
        ),
        CheckConstraint(
            "jsonb_typeof(normalized_spec) = 'object' "
            "AND NOT (normalized_spec ? 'sql') "
            "AND NOT (normalized_spec ? 'raw_sql')",
            name="schema_change_plans_no_sql_check",
        ),
        CheckConstraint(
            "base_version IS NULL OR base_version >= 1",
            name="schema_change_plans_base_version_check",
        ),
        CheckConstraint("version >= 1", name="schema_change_plans_version_check"),
        CheckConstraint(
            "(requires_approval IS FALSE) OR approval_id IS NOT NULL "
            "OR state IN ('draft', 'validated', 'pending_approval', 'rejected', "
            "'expired', 'failed')",
            name="schema_change_plans_approval_check",
        ),
        ForeignKeyConstraint(
            ["authorization_context_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.authorization_contexts.id",
                f"{GLOBAL_SCHEMA}.authorization_contexts.tenant_id",
            ],
            name="schema_change_plans_authorization_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["table_binding_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.data_table_bindings.id",
                f"{GLOBAL_SCHEMA}.data_table_bindings.tenant_id",
            ],
            name="schema_change_plans_table_tenant_fk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("id", "tenant_id", name="schema_change_plans_id_tenant_uq"),
        UniqueConstraint(
            "tenant_id",
            "operation_id",
            name="schema_change_plans_operation_uq",
        ),
        Index(
            "schema_change_plans_tenant_state_created_idx",
            "tenant_id",
            "state",
            "created_at",
        ),
        Index(
            "schema_change_plans_table_created_idx",
            "tenant_id",
            "table_binding_id",
            "created_at",
        ),
        {"comment": "Normalized logical DDL plan; raw SQL is forbidden"},
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    workspace_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    table_binding_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    authorization_context_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    operation_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.operations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approval_id: Mapped[str | None] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.approval_requests.id", ondelete="RESTRICT"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    normalized_spec: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    base_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_level: Mapped[str] = mapped_column(String(2), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'draft'"))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class OperationDispatchOutbox(Base):
    """Global dispatch metadata pointing to a tenant-scoped payload row."""

    __tablename__ = "operation_dispatch_outbox"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('crud_mutation', 'schema_change', 'compensation')",
            name="operation_dispatch_outbox_event_type_check",
        ),
        CheckConstraint(
            "state IN ('pending', 'leased', 'dispatched', 'failed', 'dead_letter')",
            name="operation_dispatch_outbox_state_check",
        ),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 20",
            name="operation_dispatch_outbox_attempts_check",
        ),
        CheckConstraint(
            "dedupe_key ~ '^[0-9a-f]{64}$'",
            name="operation_dispatch_outbox_dedupe_check",
        ),
        UniqueConstraint(
            "tenant_id",
            "dedupe_key",
            name="operation_dispatch_outbox_tenant_dedupe_uq",
        ),
        ForeignKeyConstraint(
            ["plan_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.schema_change_plans.id",
                f"{GLOBAL_SCHEMA}.schema_change_plans.tenant_id",
            ],
            name="operation_dispatch_outbox_plan_tenant_fk",
            ondelete="CASCADE",
        ),
        UniqueConstraint("id", "tenant_id", name="operation_dispatch_outbox_id_tenant_uq"),
        Index(
            "operation_dispatch_outbox_ready_idx",
            "state",
            "available_at",
            postgresql_where=text("state IN ('pending', 'failed')"),
        ),
        Index(
            "operation_dispatch_outbox_tenant_operation_idx",
            "tenant_id",
            "operation_id",
        ),
        {"comment": "Contains no mutation body; payload lives in the tenant schema"},
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.operations.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    payload_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    event_type: Mapped[str] = mapped_column(String(24), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("5"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )


class OperationCompensation(Base):
    """Ordered compensation steps associated with a controlled operation."""

    __tablename__ = "operation_compensations"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('drop_created_table', 'drop_added_column', "
            "'drop_created_index', 'restore_display_name')",
            name="operation_compensations_kind_check",
        ),
        CheckConstraint(
            "state IN ('pending', 'running', 'succeeded', 'failed', "
            "'manual_intervention_required')",
            name="operation_compensations_state_check",
        ),
        CheckConstraint(
            "sequence >= 1 AND attempt_count >= 0",
            name="operation_compensations_sequence_attempts_check",
        ),
        CheckConstraint(
            "plan_digest ~ '^[0-9a-f]{64}$' AND resource_version >= 1",
            name="operation_compensations_binding_check",
        ),
        CheckConstraint(
            "jsonb_typeof(before_snapshot) = 'object' "
            "AND NOT (before_snapshot ? 'sql') "
            "AND NOT (before_snapshot ? 'raw_sql') "
            "AND ((kind = 'restore_display_name' "
            "AND before_snapshot ? 'display_name' "
            "AND before_snapshot - 'display_name' = '{}'::jsonb) "
            "OR (kind <> 'restore_display_name' AND before_snapshot = '{}'::jsonb))",
            name="operation_compensations_snapshot_check",
        ),
        UniqueConstraint(
            "tenant_id",
            "operation_id",
            "sequence",
            name="operation_compensations_operation_sequence_uq",
        ),
        ForeignKeyConstraint(
            ["plan_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.schema_change_plans.id",
                f"{GLOBAL_SCHEMA}.schema_change_plans.tenant_id",
            ],
            name="operation_compensations_plan_tenant_fk",
            ondelete="CASCADE",
        ),
        Index(
            "operation_compensations_operation_state_idx",
            "tenant_id",
            "operation_id",
            "state",
        ),
        {"comment": "Explicit bounded compensation metadata; never arbitrary SQL"},
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    operation_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.operations.id", ondelete="CASCADE"),
        nullable=False,
    )
    plan_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    payload_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    target_logical_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_version: Mapped[int] = mapped_column(Integer, nullable=False)
    before_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=_EMPTY_OBJECT
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
        onupdate=text("now()"),
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "AuthorizationContext",
    "DataColumnBinding",
    "DataIndexBinding",
    "DataTableBinding",
    "OperationCompensation",
    "OperationDispatchOutbox",
    "SchemaChangePlan",
]
