"""Focused engineering tests for the P5.2B durable Task ledger foundation."""

from __future__ import annotations

import inspect
from importlib import import_module
from typing import TypeVar

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, UniqueConstraint
from sqlalchemy.sql.schema import Constraint, Table

from omnibase.task_ledger import models
from omnibase.task_ledger.service import (
    TaskLedgerPersistenceService,
    canonical_digest,
)

_MODEL_TYPES = (
    models.AgentTaskModel,
    models.AgentRunModel,
    models.AgentStepModel,
    models.AgentStepDependencyModel,
    models.AgentAttemptModel,
    models.AgentTaskLeaseModel,
    models.AgentTaskFencingCursorModel,
    models.AgentTaskBudgetLedgerModel,
    models.AgentTaskEffectModel,
    models.AgentCheckpointModel,
    models.AgentReconciliationCaseModel,
)


_ConstraintT = TypeVar("_ConstraintT", bound=Constraint)


def _named(table: Table, kind: type[_ConstraintT]) -> dict[str | None, _ConstraintT]:
    return {item.name: item for item in table.constraints if isinstance(item, kind)}


def test_exact_eleven_global_control_plane_tables_are_declared() -> None:
    assert {model.__tablename__ for model in _MODEL_TYPES} == {
        "agent_tasks",
        "agent_runs",
        "agent_steps",
        "agent_step_dependencies",
        "agent_attempts",
        "agent_task_leases",
        "agent_task_fencing_cursors",
        "agent_task_budget_ledgers",
        "agent_task_effects",
        "agent_checkpoints",
        "agent_reconciliation_cases",
    }
    assert all(model.__table__.schema == "omnibase_meta" for model in _MODEL_TYPES)


def test_attempt_current_lease_fk_is_deferred_and_tenant_bound() -> None:
    constraints = _named(models.AgentAttemptModel.__table__, ForeignKeyConstraint)
    current = constraints["agent_attempts_current_lease_fk"]
    assert current.deferrable is True
    assert current.initially == "DEFERRED"
    assert [column.name for column in current.columns] == [
        "task_lease_id",
        "task_id",
        "agent_run_id",
        "tenant_id",
    ]


def test_task_lease_history_has_task_wide_fencing_and_one_active_lease_per_attempt() -> None:
    constraints = _named(models.AgentTaskLeaseModel.__table__, UniqueConstraint)
    fencing = constraints["agent_task_leases_fencing_uq"]
    assert [column.name for column in fencing.columns] == [
        "task_id",
        "task_fencing_token",
        "tenant_id",
    ]
    indexes = {index.name: index for index in models.AgentTaskLeaseModel.__table__.indexes}
    active = indexes["agent_task_leases_active_attempt_uq"]
    assert isinstance(active, Index)
    assert active.unique is True
    assert "state = 'active'" in str(active.dialect_options["postgresql"]["where"])


def test_effect_exact_replay_is_unique_and_unknown_is_closed() -> None:
    constraints = _named(models.AgentTaskEffectModel.__table__, UniqueConstraint)
    request = constraints["agent_task_effects_request_uq"]
    assert [column.name for column in request.columns] == [
        "task_id",
        "request_hash",
        "tenant_id",
    ]
    state_checks = " ".join(
        str(item.sqltext)
        for item in models.AgentTaskEffectModel.__table__.constraints
        if isinstance(item, CheckConstraint)
    )
    assert "unknown" in state_checks
    assert "committed" in state_checks
    assert "result_digest" in state_checks


def test_budget_ledger_keeps_all_twelve_dimensions_closed() -> None:
    checks = " ".join(
        str(item.sqltext)
        for item in models.AgentTaskBudgetLedgerModel.__table__.constraints
        if isinstance(item, CheckConstraint)
    )
    for dimension in (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
        "cost_micros",
        "model_calls",
        "tool_calls",
        "wall_clock_ms",
        "artifact_bytes",
        "sandbox_jobs",
        "max_attempts",
        "max_parallel_steps",
    ):
        assert dimension in checks
    assert "remaining = limit_value - reserved" in checks


def test_all_cross_aggregate_foreign_keys_carry_tenant_identity() -> None:
    for model in _MODEL_TYPES:
        for constraint in model.__table__.constraints:
            if not isinstance(constraint, ForeignKeyConstraint):
                continue
            local_names = {column.name for column in constraint.columns}
            remote_tables = {element.column.table.name for element in constraint.elements}
            if remote_tables == {"tenants"}:
                continue
            assert "tenant_id" in local_names, (
                model.__tablename__,
                constraint.name,
            )


def test_service_owns_no_transaction_and_imports_no_provider_or_tool_runtime() -> None:
    source = inspect.getsource(TaskLedgerPersistenceService)
    assert ".commit(" not in source
    assert ".rollback(" not in source
    module_source = inspect.getsource(inspect.getmodule(TaskLedgerPersistenceService))
    for forbidden in (
        "omnibase.model_gateway",
        "omnibase.sandbox",
        "subprocess",
        "httpx",
        "requests",
        "mcp",
    ):
        assert forbidden not in module_source.lower()


def test_canonical_digest_is_deterministic_raw_utf8_json() -> None:
    assert canonical_digest({"b": 2, "a": "值"}) == canonical_digest({"a": "值", "b": 2})
    assert len(canonical_digest({"a": 1})) == 64


def test_migration_declares_unique_head_and_populated_downgrade_guard() -> None:
    migration = import_module("omnibase.migrations.versions.0011_p5_2b_task_ledger")

    assert migration.revision == "0011"
    assert migration.down_revision == "0010"
    source = inspect.getsource(migration)
    assert "P5.2B populated downgrade is forbidden" in source
    assert "ERRCODE = '55000'" in source
    assert "migration_schema_scope" in source


def test_task_lease_heartbeat_window_and_terminal_convergence_contract() -> None:
    """The lease heartbeat must stay inside [created_at, expires_at].

    ``TaskLedgerPersistenceService.finish_attempt`` clamps a late
    terminalization heartbeat to ``expires_at``; if the window constraint
    were ever relaxed or the clamp removed, a disconnected stream that
    finalizes after its lease lapsed would roll back the terminal
    transition and leave the task/run stuck in "running" forever
    (P5.4D acceptance finding F-3b).
    """
    checks = " ".join(
        str(item.sqltext)
        for item in models.AgentTaskLeaseModel.__table__.constraints
        if isinstance(item, CheckConstraint)
    )
    assert "heartbeat_at >= created_at" in checks
    assert "heartbeat_at <= expires_at" in checks
    assert "expires_at > created_at" in checks
    source = inspect.getsource(TaskLedgerPersistenceService.finish_attempt)
    assert "min(now, lease.expires_at)" in source
    assert "lease.heartbeat_at" in source
