"""Post-lock authorization race gates for the P34.3 controlled CRUD executor.

The tests deliberately hold one record in the executor's frozen lock order,
change that record in the blocking transaction, and release it only after
PostgreSQL proves that the executor is waiting on the blocker PID.  This makes
the gates exercise database-visible state after lock acquisition instead of a
lock-free timing approximation.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Connection, Engine

from omnibase.controlled_data.execution_service import (
    ControlledCrudAuditContext,
    ControlledCrudServiceError,
    builtin_atomic_controlled_crud_executor,
    execute_controlled_crud_audited,
)
from omnibase.controlled_data.executor import ControlledCrudCommand
from tests.integration.test_p34_3_controlled_data_executor import (
    _audit_rows,
    _create_scenario,
    _idempotency_count,
    _operation_state,
    _OwnedResources,
    _Scenario,
    _session_factory,
    _tenant_values,
)

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.3 lock-race integration requires OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration

_Change = Callable[[Connection], ControlledCrudCommand]
_BeforeRelease = Callable[[Engine, ControlledCrudCommand], None]


def _wait_until_blocked_by(db_engine: Engine, blocker_pid: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with db_engine.connect() as observer:
            blocked = observer.execute(
                text(
                    "SELECT EXISTS ("
                    "SELECT 1 FROM pg_stat_activity "
                    "WHERE :blocker_pid = ANY(pg_blocking_pids(pid))"
                    ")"
                ),
                {"blocker_pid": blocker_pid},
            ).scalar_one()
        if blocked is True:
            return
        time.sleep(0.02)
    raise AssertionError("executor never entered a PostgreSQL lock wait")


def _wait_until_database_time(
    db_engine: Engine,
    command: ControlledCrudCommand,
) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        with db_engine.connect() as observer:
            expired = observer.execute(
                text("SELECT clock_timestamp() >= :expires_at"),
                {"expires_at": command.decision.expires_at},
            ).scalar_one()
        if expired is True:
            return
        time.sleep(0.02)
    raise AssertionError("database clock did not cross authorization expiry")


def _run_blocked_change(
    db_engine: Engine,
    scenario: _Scenario,
    *,
    request_id: str,
    change: _Change,
    before_release: _BeforeRelease | None = None,
) -> ControlledCrudServiceError:
    factory = _session_factory(db_engine)
    with ThreadPoolExecutor(max_workers=1) as pool, db_engine.connect() as blocker:
        transaction = blocker.begin()
        try:
            blocker_pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
            command = change(blocker)
            future = pool.submit(
                execute_controlled_crud_audited,
                factory,
                command,
                audit=ControlledCrudAuditContext(request_id=request_id, risk_level="R1"),
                executor=builtin_atomic_controlled_crud_executor,
            )
            _wait_until_blocked_by(db_engine, blocker_pid)
            if before_release is not None:
                before_release(db_engine, command)
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise

        with pytest.raises(ControlledCrudServiceError) as caught:
            future.result(timeout=10)
    return caught.value


def _assert_queued_mutation_rejected(db_engine: Engine, scenario: _Scenario) -> None:
    assert _tenant_values(db_engine, scenario) == sorted(scenario.initial_values)
    assert _operation_state(db_engine, scenario) == {
        "state": "queued",
        "progress": 0,
        "attempt_count": 0,
        "result_ref": None,
        "started_at": None,
        "completed_at": None,
    }
    assert _idempotency_count(db_engine, scenario) == 0


def _assert_failure_audit(
    db_engine: Engine,
    scenario: _Scenario,
    *,
    request_id: str,
    code: str,
    decision: str,
    status_code: int,
) -> None:
    audits = _audit_rows(db_engine, scenario)
    assert len(audits) == 1
    assert audits[0] == {
        "request_id": request_id,
        "decision": decision,
        "status_code": status_code,
        "row_count": None,
        "details": {
            "error_code": code,
            "reason_code": code,
            "retryable": False,
        },
    }
    persisted = str(audits).lower()
    for forbidden in (
        scenario.tenant_schema.lower(),
        scenario.physical_table.lower(),
        scenario.physical_column.lower(),
    ):
        assert forbidden not in persisted


def test_tenant_deactivated_while_executor_waits_fails_closed(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    request_id = f"tenant-deactivated-{scenario.tenant_id.hex[:8]}"

    def deactivate_tenant(connection: Connection) -> ControlledCrudCommand:
        changed = connection.execute(
            text(
                "UPDATE omnibase_meta.tenants SET is_active = FALSE WHERE id = :tenant RETURNING id"
            ),
            {"tenant": str(scenario.tenant_id)},
        ).scalar_one()
        assert str(changed) == str(scenario.tenant_id)
        return scenario.command

    failure = _run_blocked_change(
        db_engine,
        scenario,
        request_id=request_id,
        change=deactivate_tenant,
    )

    assert failure.code == "CONTROLLED_CRUD_AUTHORIZATION_DENIED"
    assert failure.status_code == 403
    _assert_queued_mutation_rejected(db_engine, scenario)
    _assert_failure_audit(
        db_engine,
        scenario,
        request_id=request_id,
        code=failure.code,
        decision="denied",
        status_code=403,
    )


@pytest.mark.parametrize("live_change", ["inactive", "downgraded"])
def test_tenant_user_changed_while_executor_waits_fails_closed(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
    live_change: str,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    request_id = f"user-{live_change}-{scenario.tenant_id.hex[:8]}"

    def change_user(connection: Connection) -> ControlledCrudCommand:
        assignment = "is_active = FALSE" if live_change == "inactive" else "is_tenant_admin = FALSE"
        changed = connection.execute(
            text(
                f'UPDATE "{scenario.tenant_schema}".users SET {assignment} '  # noqa: S608
                "WHERE id = :actor RETURNING id"
            ),
            {"actor": str(scenario.actor_id)},
        ).scalar_one()
        assert str(changed) == str(scenario.actor_id)
        return scenario.command

    failure = _run_blocked_change(
        db_engine,
        scenario,
        request_id=request_id,
        change=change_user,
    )

    assert failure.code == "CONTROLLED_CRUD_AUTHORIZATION_DENIED"
    assert failure.status_code == 403
    _assert_queued_mutation_rejected(db_engine, scenario)
    _assert_failure_audit(
        db_engine,
        scenario,
        request_id=request_id,
        code=failure.code,
        decision="denied",
        status_code=403,
    )


def test_authorization_expires_by_database_clock_while_executor_waits(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    request_id = f"authorization-expired-{scenario.tenant_id.hex[:8]}"

    def shorten_and_lock_authorization(connection: Connection) -> ControlledCrudCommand:
        expires_at = connection.execute(
            text(
                "UPDATE omnibase_meta.authorization_contexts "
                "SET expires_at = clock_timestamp() + interval '1250 milliseconds' "
                "WHERE id = :authorization AND tenant_id = :tenant "
                "RETURNING expires_at"
            ),
            {
                "authorization": str(scenario.command.authorization_context_id),
                "tenant": str(scenario.tenant_id),
            },
        ).scalar_one()
        decision = replace(
            scenario.command.decision,
            evaluated_at=expires_at - timedelta(milliseconds=1250),
            expires_at=expires_at,
        )
        return replace(scenario.command, decision=decision)

    failure = _run_blocked_change(
        db_engine,
        scenario,
        request_id=request_id,
        change=shorten_and_lock_authorization,
        before_release=_wait_until_database_time,
    )

    assert failure.code == "CONTROLLED_CRUD_AUTHORIZATION_DENIED"
    assert failure.status_code == 403
    _assert_queued_mutation_rejected(db_engine, scenario)
    _assert_failure_audit(
        db_engine,
        scenario,
        request_id=request_id,
        code=failure.code,
        decision="denied",
        status_code=403,
    )


def test_operation_cancelled_and_version_changed_while_executor_waits(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    request_id = f"operation-cancelled-{scenario.tenant_id.hex[:8]}"

    def cancel_operation(connection: Connection) -> ControlledCrudCommand:
        changed = (
            connection.execute(
                text(
                    "UPDATE omnibase_meta.operations "
                    "SET state = 'cancelled', version = version + 1, "
                    "completed_at = clock_timestamp() "
                    "WHERE id = :operation AND tenant_id = :tenant "
                    "RETURNING state, version"
                ),
                {
                    "operation": str(scenario.operation_id),
                    "tenant": str(scenario.tenant_id),
                },
            )
            .mappings()
            .one()
        )
        assert dict(changed) == {"state": "cancelled", "version": 2}
        return scenario.command

    failure = _run_blocked_change(
        db_engine,
        scenario,
        request_id=request_id,
        change=cancel_operation,
    )

    assert failure.code == "CONTROLLED_CRUD_STATE_CONFLICT"
    assert failure.status_code == 409
    assert _tenant_values(db_engine, scenario) == sorted(scenario.initial_values)
    assert _idempotency_count(db_engine, scenario) == 0
    with db_engine.connect() as connection:
        operation = (
            connection.execute(
                text(
                    "SELECT state, version, progress, attempt_count, result_ref "
                    "FROM omnibase_meta.operations "
                    "WHERE id = :operation AND tenant_id = :tenant"
                ),
                {
                    "operation": str(scenario.operation_id),
                    "tenant": str(scenario.tenant_id),
                },
            )
            .mappings()
            .one()
        )
    assert dict(operation) == {
        "state": "cancelled",
        "version": 2,
        "progress": 0,
        "attempt_count": 0,
        "result_ref": None,
    }
    _assert_failure_audit(
        db_engine,
        scenario,
        request_id=request_id,
        code=failure.code,
        decision="error",
        status_code=409,
    )
