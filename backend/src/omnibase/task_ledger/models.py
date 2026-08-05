"""P5.2B durable Agent Task ledger models.

The ledger is an internal, global control-plane foundation.  It persists the
logical P5.2A contracts without exposing a Browser Agent runtime, model/tool
execution, physical locators, or credentials.  Database checks, composite
tenant-bound foreign keys, partial unique indexes, and migration-installed
triggers remain authoritative; ORM discipline is not the security boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
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
_SHA256 = "^[0-9a-f]{64}$"

_TASK_STATES = (
    "'created', 'planning', 'awaiting_approval', 'scheduled', 'running', "
    "'paused', 'blocked_unknown', 'succeeded', 'failed', 'cancelled'"
)
_RUN_STATES = "'created', 'leased', 'running', 'paused', 'succeeded', 'failed', 'cancelled'"
_STEP_STATES = "'pending', 'ready', 'running', 'succeeded', 'failed', 'cancelled'"
_ATTEMPT_STATES = (
    "'pending', 'ready', 'leased', 'dispatching', 'running', 'committed', "
    "'failed', 'unknown', 'cancelled'"
)
_LEASE_STATES = "'active', 'expired', 'revoked', 'completed'"
_EFFECT_STATES = "'reserved', 'dispatching', 'committed', 'failed', 'unknown'"
_RECONCILIATION_STATES = "'open', 'resolved'"
_BUDGET_DIMENSIONS = (
    "'input_tokens', 'output_tokens', 'reasoning_tokens', 'total_tokens', "
    "'cost_micros', 'model_calls', 'tool_calls', 'wall_clock_ms', "
    "'artifact_bytes', 'sandbox_jobs', 'max_attempts', 'max_parallel_steps'"
)


class AgentTaskModel(Base):
    """Frozen invocation identity and task lifecycle root."""

    __tablename__ = "agent_tasks"
    __table_args__ = (
        CheckConstraint(f"state IN ({_TASK_STATES})", name="agent_tasks_state_check"),
        CheckConstraint("workspace_generation >= 1", name="agent_tasks_workspace_generation_check"),
        CheckConstraint("task_generation >= 1", name="agent_tasks_generation_check"),
        CheckConstraint("plan_version >= 1", name="agent_tasks_plan_version_check"),
        CheckConstraint(
            f"agent_version_digest ~ '{_SHA256}'", name="agent_tasks_version_digest_check"
        ),
        CheckConstraint(f"plan_digest ~ '{_SHA256}'", name="agent_tasks_plan_digest_check"),
        CheckConstraint(
            f"resource_scope_digest ~ '{_SHA256}'",
            name="agent_tasks_resource_scope_digest_check",
        ),
        CheckConstraint(
            f"budget_policy_digest ~ '{_SHA256}'",
            name="agent_tasks_budget_policy_digest_check",
        ),
        CheckConstraint(f"request_hash ~ '{_SHA256}'", name="agent_tasks_request_hash_check"),
        CheckConstraint("deadline > created_at", name="agent_tasks_deadline_check"),
        UniqueConstraint("id", "tenant_id", name="agent_tasks_id_tenant_uq"),
        ForeignKeyConstraint(
            ["workspace_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.workspaces.id", f"{GLOBAL_SCHEMA}.workspaces.tenant_id"],
            name="agent_tasks_workspace_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["agent_definition_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_definitions.id",
                f"{GLOBAL_SCHEMA}.agent_definitions.tenant_id",
            ],
            name="agent_tasks_definition_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["agent_version_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.agent_versions.id", f"{GLOBAL_SCHEMA}.agent_versions.tenant_id"],
            name="agent_tasks_version_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_agent_binding_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.workspace_agent_bindings.id",
                f"{GLOBAL_SCHEMA}.workspace_agent_bindings.tenant_id",
            ],
            name="agent_tasks_binding_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["approval_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.approval_requests.id",
                f"{GLOBAL_SCHEMA}.approval_requests.tenant_id",
            ],
            name="agent_tasks_approval_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["creation_operation_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.operations.id", f"{GLOBAL_SCHEMA}.operations.tenant_id"],
            name="agent_tasks_operation_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.resource_registry.id",
                f"{GLOBAL_SCHEMA}.resource_registry.tenant_id",
            ],
            name="agent_tasks_resource_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index(
            "agent_tasks_workspace_state_idx", "tenant_id", "workspace_id", "state", "created_at"
        ),
        Index("agent_tasks_binding_state_idx", "tenant_id", "workspace_agent_binding_id", "state"),
    )

    id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(
        _UUID,
        ForeignKey(f"{GLOBAL_SCHEMA}.tenants.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_user_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_definition_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_version_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_version_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    workspace_agent_binding_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    task_generation: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    plan_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False, server_default=text("'created'"))
    resource_scope_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    budget_policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    approval_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    creation_operation_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class AgentRunModel(Base):
    """Agent execution binding that reuses the P34.4 Workspace Run identity."""

    __tablename__ = "agent_runs"
    __table_args__ = (
        CheckConstraint(f"state IN ({_RUN_STATES})", name="agent_runs_state_check"),
        CheckConstraint("workspace_generation >= 1", name="agent_runs_workspace_generation_check"),
        CheckConstraint(
            "(state = 'created' AND run_lease_id IS NULL AND run_fencing_token IS NULL "
            "AND node_id IS NULL AND node_fencing_token IS NULL "
            "AND runtime_instance_id IS NULL AND workload_identity_digest IS NULL) OR "
            "(state IN ('leased', 'running', 'paused') AND run_lease_id IS NOT NULL "
            "AND run_fencing_token IS NOT NULL AND node_id IS NOT NULL "
            "AND node_fencing_token IS NOT NULL AND runtime_instance_id IS NOT NULL "
            "AND workload_identity_digest IS NOT NULL) OR "
            "(state IN ('succeeded', 'failed', 'cancelled') AND run_lease_id IS NULL "
            "AND run_fencing_token IS NULL AND node_id IS NULL AND node_fencing_token IS NULL "
            "AND runtime_instance_id IS NULL AND workload_identity_digest IS NULL)",
            name="agent_runs_binding_state_check",
        ),
        CheckConstraint(
            f"workload_identity_digest IS NULL OR workload_identity_digest ~ '{_SHA256}'",
            name="agent_runs_workload_digest_check",
        ),
        UniqueConstraint("id", "tenant_id", name="agent_runs_id_tenant_uq"),
        UniqueConstraint("id", "task_id", "tenant_id", name="agent_runs_id_task_tenant_uq"),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.agent_tasks.id", f"{GLOBAL_SCHEMA}.agent_tasks.tenant_id"],
            name="agent_runs_task_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_run_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.workspace_runs.id", f"{GLOBAL_SCHEMA}.workspace_runs.tenant_id"],
            name="agent_runs_workspace_run_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["node_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.workspace_nodes.id", f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id"],
            name="agent_runs_node_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_lease_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.run_leases.id", f"{GLOBAL_SCHEMA}.run_leases.tenant_id"],
            name="agent_runs_run_lease_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index("agent_runs_task_state_idx", "tenant_id", "task_id", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    task_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    workspace_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    workspace_run_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    runtime_instance_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    workload_identity_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    node_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    node_fencing_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    run_lease_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    run_fencing_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'created'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class AgentStepModel(Base):
    """A plan-bound step within one Agent Run."""

    __tablename__ = "agent_steps"
    __table_args__ = (
        CheckConstraint(f"state IN ({_STEP_STATES})", name="agent_steps_state_check"),
        CheckConstraint("step_number >= 1", name="agent_steps_number_check"),
        CheckConstraint("plan_version >= 1", name="agent_steps_plan_version_check"),
        CheckConstraint(f"plan_digest ~ '{_SHA256}'", name="agent_steps_plan_digest_check"),
        UniqueConstraint("id", "tenant_id", name="agent_steps_id_tenant_uq"),
        UniqueConstraint(
            "id", "task_id", "agent_run_id", "tenant_id", name="agent_steps_binding_uq"
        ),
        UniqueConstraint(
            "task_id", "agent_run_id", "step_number", "tenant_id", name="agent_steps_number_uq"
        ),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.agent_tasks.id", f"{GLOBAL_SCHEMA}.agent_tasks.tenant_id"],
            name="agent_steps_task_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["agent_run_id", "task_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_runs.id",
                f"{GLOBAL_SCHEMA}.agent_runs.task_id",
                f"{GLOBAL_SCHEMA}.agent_runs.tenant_id",
            ],
            name="agent_steps_run_task_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index("agent_steps_task_state_idx", "tenant_id", "task_id", "state", "step_number"),
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    task_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_run_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    step_number: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class AgentStepDependencyModel(Base):
    """Normalized dependency edge; cycle detection remains a locked service check."""

    __tablename__ = "agent_step_dependencies"
    __table_args__ = (
        CheckConstraint("step_id <> depends_on_step_id", name="agent_step_dependencies_self_check"),
        ForeignKeyConstraint(
            ["step_id", "task_id", "agent_run_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_steps.id",
                f"{GLOBAL_SCHEMA}.agent_steps.task_id",
                f"{GLOBAL_SCHEMA}.agent_steps.agent_run_id",
                f"{GLOBAL_SCHEMA}.agent_steps.tenant_id",
            ],
            name="agent_step_dependencies_step_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["depends_on_step_id", "task_id", "agent_run_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_steps.id",
                f"{GLOBAL_SCHEMA}.agent_steps.task_id",
                f"{GLOBAL_SCHEMA}.agent_steps.agent_run_id",
                f"{GLOBAL_SCHEMA}.agent_steps.tenant_id",
            ],
            name="agent_step_dependencies_parent_fk",
            ondelete="RESTRICT",
        ),
    )

    step_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    depends_on_step_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    task_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_run_id: Mapped[str] = mapped_column(_UUID, nullable=False)


class AgentAttemptModel(Base):
    """A numbered execution attempt; terminal attempts clear their current lease."""

    __tablename__ = "agent_attempts"
    __table_args__ = (
        CheckConstraint(f"state IN ({_ATTEMPT_STATES})", name="agent_attempts_state_check"),
        CheckConstraint("attempt_number >= 1", name="agent_attempts_number_check"),
        CheckConstraint(
            "(state IN ('pending', 'ready') AND task_lease_id IS NULL "
            "AND task_fencing_token IS NULL) OR "
            "(state IN ('leased', 'dispatching', 'running') AND task_lease_id IS NOT NULL "
            "AND task_fencing_token IS NOT NULL) OR "
            "(state IN ('committed', 'failed', 'unknown', 'cancelled') "
            "AND task_lease_id IS NULL AND task_fencing_token IS NULL)",
            name="agent_attempts_lease_state_check",
        ),
        CheckConstraint("deadline > created_at", name="agent_attempts_deadline_check"),
        UniqueConstraint("id", "tenant_id", name="agent_attempts_id_tenant_uq"),
        UniqueConstraint(
            "id", "task_id", "agent_run_id", "tenant_id", name="agent_attempts_binding_uq"
        ),
        UniqueConstraint(
            "task_id",
            "step_id",
            "attempt_number",
            "tenant_id",
            name="agent_attempts_number_uq",
        ),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.agent_tasks.id", f"{GLOBAL_SCHEMA}.agent_tasks.tenant_id"],
            name="agent_attempts_task_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["step_id", "task_id", "agent_run_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_steps.id",
                f"{GLOBAL_SCHEMA}.agent_steps.task_id",
                f"{GLOBAL_SCHEMA}.agent_steps.agent_run_id",
                f"{GLOBAL_SCHEMA}.agent_steps.tenant_id",
            ],
            name="agent_attempts_step_binding_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["task_lease_id", "task_id", "agent_run_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_task_leases.id",
                f"{GLOBAL_SCHEMA}.agent_task_leases.task_id",
                f"{GLOBAL_SCHEMA}.agent_task_leases.agent_run_id",
                f"{GLOBAL_SCHEMA}.agent_task_leases.tenant_id",
            ],
            name="agent_attempts_current_lease_fk",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        Index("agent_attempts_step_state_idx", "tenant_id", "step_id", "state", "attempt_number"),
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    task_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    step_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_run_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'pending'"))
    task_lease_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    task_fencing_token: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    expected_previous_state: Mapped[str] = mapped_column(String(16), nullable=False)
    deadline: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class AgentTaskLeaseModel(Base):
    """History-preserving Task claim with immutable holder identity."""

    __tablename__ = "agent_task_leases"
    __table_args__ = (
        CheckConstraint(f"state IN ({_LEASE_STATES})", name="agent_task_leases_state_check"),
        CheckConstraint("task_fencing_token >= 1", name="agent_task_leases_fencing_check"),
        CheckConstraint("run_fencing_token >= 1", name="agent_task_leases_run_fencing_check"),
        CheckConstraint("node_fencing_token >= 1", name="agent_task_leases_node_fencing_check"),
        CheckConstraint(
            "workspace_generation >= 1", name="agent_task_leases_workspace_generation_check"
        ),
        CheckConstraint("expires_at > created_at", name="agent_task_leases_expiry_check"),
        CheckConstraint(
            "expires_at - created_at <= interval '300 seconds'",
            name="agent_task_leases_ttl_ceiling_check",
        ),
        CheckConstraint(
            "heartbeat_at IS NULL OR (heartbeat_at >= created_at AND heartbeat_at <= expires_at)",
            name="agent_task_leases_heartbeat_window_check",
        ),
        CheckConstraint(
            "state <> 'completed' OR heartbeat_at IS NOT NULL",
            name="agent_task_leases_completed_heartbeat_check",
        ),
        UniqueConstraint("id", "tenant_id", name="agent_task_leases_id_tenant_uq"),
        UniqueConstraint(
            "id", "task_id", "agent_run_id", "tenant_id", name="agent_task_leases_binding_uq"
        ),
        UniqueConstraint(
            "task_id", "task_fencing_token", "tenant_id", name="agent_task_leases_fencing_uq"
        ),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.agent_tasks.id", f"{GLOBAL_SCHEMA}.agent_tasks.tenant_id"],
            name="agent_task_leases_task_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "task_id", "agent_run_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_attempts.id",
                f"{GLOBAL_SCHEMA}.agent_attempts.task_id",
                f"{GLOBAL_SCHEMA}.agent_attempts.agent_run_id",
                f"{GLOBAL_SCHEMA}.agent_attempts.tenant_id",
            ],
            name="agent_task_leases_attempt_binding_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["run_lease_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.run_leases.id", f"{GLOBAL_SCHEMA}.run_leases.tenant_id"],
            name="agent_task_leases_run_lease_tenant_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["node_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.workspace_nodes.id", f"{GLOBAL_SCHEMA}.workspace_nodes.tenant_id"],
            name="agent_task_leases_node_tenant_fk",
            ondelete="RESTRICT",
        ),
        Index(
            "agent_task_leases_active_attempt_uq",
            "attempt_id",
            "tenant_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        Index("agent_task_leases_task_state_idx", "tenant_id", "task_id", "state", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    task_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    attempt_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_run_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    run_lease_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    run_fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    node_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    node_fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    workspace_generation: Mapped[int] = mapped_column(Integer, nullable=False)
    task_fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'active'"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class AgentTaskFencingCursorModel(Base):
    """Per-Task committed token and UTC chronology allocator."""

    __tablename__ = "agent_task_fencing_cursors"
    __table_args__ = (
        CheckConstraint("next_fencing_token >= 1", name="agent_task_fencing_cursor_next_check"),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.agent_tasks.id", f"{GLOBAL_SCHEMA}.agent_tasks.tenant_id"],
            name="agent_task_fencing_cursor_task_fk",
            ondelete="RESTRICT",
        ),
    )

    task_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    next_fencing_token: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    last_claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentTaskBudgetLedgerModel(Base):
    """One locked budget row for every P5.2A dimension."""

    __tablename__ = "agent_task_budget_ledgers"
    __table_args__ = (
        CheckConstraint(
            f"dimension IN ({_BUDGET_DIMENSIONS})", name="agent_task_budget_dimension_check"
        ),
        CheckConstraint("limit_value >= 1", name="agent_task_budget_limit_check"),
        CheckConstraint(
            "reserved >= 0 AND committed >= 0 AND released >= 0 AND remaining >= 0",
            name="agent_task_budget_nonnegative_check",
        ),
        CheckConstraint(
            "committed <= reserved AND reserved <= limit_value AND released <= committed",
            name="agent_task_budget_order_check",
        ),
        CheckConstraint(
            "remaining = limit_value - reserved", name="agent_task_budget_remaining_check"
        ),
        CheckConstraint(
            f"policy_digest ~ '{_SHA256}'", name="agent_task_budget_policy_digest_check"
        ),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.agent_tasks.id", f"{GLOBAL_SCHEMA}.agent_tasks.tenant_id"],
            name="agent_task_budget_task_fk",
            ondelete="RESTRICT",
        ),
    )

    task_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    tenant_id: Mapped[str] = mapped_column(_UUID, primary_key=True)
    dimension: Mapped[str] = mapped_column(String(32), primary_key=True)
    limit_value: Mapped[int] = mapped_column(BigInteger, nullable=False)
    reserved: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    committed: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    released: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
    remaining: Mapped[int] = mapped_column(BigInteger, nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class AgentTaskEffectModel(Base):
    """Provider-boundary effect; unknown is terminal and reconciled separately."""

    __tablename__ = "agent_task_effects"
    __table_args__ = (
        CheckConstraint(f"state IN ({_EFFECT_STATES})", name="agent_task_effects_state_check"),
        CheckConstraint(
            f"request_hash ~ '{_SHA256}'", name="agent_task_effects_request_hash_check"
        ),
        CheckConstraint(
            f"result_digest IS NULL OR result_digest ~ '{_SHA256}'",
            name="agent_task_effects_result_digest_check",
        ),
        CheckConstraint(
            "(state = 'committed' AND result_digest IS NOT NULL) OR "
            "(state <> 'committed' AND result_digest IS NULL)",
            name="agent_task_effects_result_state_check",
        ),
        UniqueConstraint("id", "tenant_id", name="agent_task_effects_id_tenant_uq"),
        UniqueConstraint(
            "task_id",
            "request_hash",
            "tenant_id",
            name="agent_task_effects_request_uq",
        ),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.agent_tasks.id", f"{GLOBAL_SCHEMA}.agent_tasks.tenant_id"],
            name="agent_task_effects_task_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "task_id", "agent_run_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_attempts.id",
                f"{GLOBAL_SCHEMA}.agent_attempts.task_id",
                f"{GLOBAL_SCHEMA}.agent_attempts.agent_run_id",
                f"{GLOBAL_SCHEMA}.agent_attempts.tenant_id",
            ],
            name="agent_task_effects_attempt_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["operation_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.operations.id", f"{GLOBAL_SCHEMA}.operations.tenant_id"],
            name="agent_task_effects_operation_fk",
            ondelete="RESTRICT",
        ),
        Index("agent_task_effects_attempt_state_idx", "tenant_id", "attempt_id", "state"),
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    task_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    attempt_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_run_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    operation_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    state: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'reserved'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class AgentCheckpointModel(Base):
    """Logical committed checkpoint without runtime or provider handles."""

    __tablename__ = "agent_checkpoints"
    __table_args__ = (
        CheckConstraint("committed_plan_version >= 1", name="agent_checkpoints_plan_version_check"),
        CheckConstraint(
            f"committed_plan_digest ~ '{_SHA256}'", name="agent_checkpoints_plan_digest_check"
        ),
        CheckConstraint(
            f"budget_policy_digest ~ '{_SHA256}'", name="agent_checkpoints_budget_digest_check"
        ),
        CheckConstraint(
            "jsonb_typeof(committed_attempt_results) = 'array' "
            "AND jsonb_array_length(committed_attempt_results) >= 1",
            name="agent_checkpoints_results_check",
        ),
        CheckConstraint(
            "jsonb_typeof(budget_snapshot) = 'object'", name="agent_checkpoints_budget_check"
        ),
        UniqueConstraint("id", "tenant_id", name="agent_checkpoints_id_tenant_uq"),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.agent_tasks.id", f"{GLOBAL_SCHEMA}.agent_tasks.tenant_id"],
            name="agent_checkpoints_task_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "task_id", "agent_run_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_attempts.id",
                f"{GLOBAL_SCHEMA}.agent_attempts.task_id",
                f"{GLOBAL_SCHEMA}.agent_attempts.agent_run_id",
                f"{GLOBAL_SCHEMA}.agent_attempts.tenant_id",
            ],
            name="agent_checkpoints_attempt_fk",
            ondelete="RESTRICT",
        ),
        Index("agent_checkpoints_task_created_idx", "tenant_id", "task_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    task_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    attempt_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_run_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    committed_plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    committed_plan_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    committed_attempt_results: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    budget_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    budget_policy_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )


class AgentReconciliationCaseModel(Base):
    """Manual reconciliation record for unknown provider outcomes."""

    __tablename__ = "agent_reconciliation_cases"
    __table_args__ = (
        CheckConstraint(
            f"state IN ({_RECONCILIATION_STATES})", name="agent_reconciliation_state_check"
        ),
        CheckConstraint(
            "reason_code ~ '^[a-z][a-z0-9_]{2,63}$'", name="agent_reconciliation_reason_check"
        ),
        CheckConstraint(
            "(state = 'open' AND resolved_at IS NULL) OR "
            "(state = 'resolved' AND resolved_at IS NOT NULL)",
            name="agent_reconciliation_resolved_check",
        ),
        UniqueConstraint("id", "tenant_id", name="agent_reconciliation_id_tenant_uq"),
        ForeignKeyConstraint(
            ["task_id", "tenant_id"],
            [f"{GLOBAL_SCHEMA}.agent_tasks.id", f"{GLOBAL_SCHEMA}.agent_tasks.tenant_id"],
            name="agent_reconciliation_task_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["attempt_id", "task_id", "agent_run_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_attempts.id",
                f"{GLOBAL_SCHEMA}.agent_attempts.task_id",
                f"{GLOBAL_SCHEMA}.agent_attempts.agent_run_id",
                f"{GLOBAL_SCHEMA}.agent_attempts.tenant_id",
            ],
            name="agent_reconciliation_attempt_fk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["effect_id", "tenant_id"],
            [
                f"{GLOBAL_SCHEMA}.agent_task_effects.id",
                f"{GLOBAL_SCHEMA}.agent_task_effects.tenant_id",
            ],
            name="agent_reconciliation_effect_fk",
            ondelete="RESTRICT",
        ),
        Index("agent_reconciliation_task_state_idx", "tenant_id", "task_id", "state"),
    )

    id: Mapped[str] = mapped_column(
        _UUID, primary_key=True, server_default=text("gen_random_uuid()")
    )
    tenant_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    task_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    attempt_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    agent_run_id: Mapped[str] = mapped_column(_UUID, nullable=False)
    effect_id: Mapped[str | None] = mapped_column(_UUID, nullable=True)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'open'"))
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("clock_timestamp()")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


__all__ = [
    "AgentAttemptModel",
    "AgentCheckpointModel",
    "AgentReconciliationCaseModel",
    "AgentRunModel",
    "AgentStepDependencyModel",
    "AgentStepModel",
    "AgentTaskBudgetLedgerModel",
    "AgentTaskEffectModel",
    "AgentTaskFencingCursorModel",
    "AgentTaskLeaseModel",
    "AgentTaskModel",
]
