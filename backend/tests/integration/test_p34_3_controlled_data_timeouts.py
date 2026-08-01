"""Real PostgreSQL timeout gates for P34.3 controlled CRUD.

This module is inert unless the shared destructive-test sentinel approves an
explicit ``omnibase_test_*`` database and restricted non-owner role.  The two
tests exercise PostgreSQL's real ``lock_timeout`` and ``statement_timeout``;
they do not simulate DBAPI exceptions and never discover another database.
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine

from omnibase.controlled_data.crud import canonical_request_hash
from omnibase.controlled_data.execution_service import (
    ControlledCrudAuditContext,
    ControlledCrudServiceError,
    builtin_atomic_controlled_crud_executor,
    execute_controlled_crud_audited,
)
from tests.integration.test_p34_3_controlled_data_executor import (
    _assert_mutation_rolled_back,
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
        "P34.3 timeout integration requires OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration


def _command_with_timeouts(
    db_engine: Engine,
    scenario: _Scenario,
    *,
    statement_timeout_ms: int,
    lock_timeout_ms: int,
):
    request = scenario.request.model_copy(update={"timeout_ms": statement_timeout_ms})
    request_hash = canonical_request_hash(request)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE omnibase_meta.operations SET request_hash = :request_hash "
                "WHERE id = :operation AND tenant_id = :tenant"
            ),
            {
                "request_hash": request_hash,
                "operation": str(scenario.operation_id),
                "tenant": str(scenario.tenant_id),
            },
        )
    return replace(
        scenario.command,
        request=request,
        lock_timeout_ms=lock_timeout_ms,
    )


def _assert_code_only_timeout_audit(
    db_engine: Engine,
    scenario: _Scenario,
    *,
    request_id: str,
    code: str,
    status_code: int,
    captured_logs: str,
) -> None:
    audits = _audit_rows(db_engine, scenario)
    assert len(audits) == 1
    assert audits[0] == {
        "request_id": request_id,
        "decision": "error",
        "status_code": status_code,
        "row_count": None,
        "details": {
            "error_code": code,
            "reason_code": code,
            "retryable": True,
        },
    }
    exposed = f"{audits!r}\n{captured_logs}".lower()
    for forbidden in (
        "ctid",
        scenario.tenant_schema.lower(),
        scenario.physical_table.lower(),
        scenario.physical_column.lower(),
        "canceling statement due to statement timeout",
        "canceling statement due to lock timeout",
        "could not obtain lock",
        "set local",
        "sqlstate",
    ):
        assert forbidden not in exposed


def _assert_retry_completed_once(db_engine: Engine, scenario: _Scenario) -> None:
    assert _tenant_values(db_engine, scenario) == ["after"]
    operation = _operation_state(db_engine, scenario)
    assert operation["state"] == "succeeded"
    assert operation["attempt_count"] == 1
    assert _idempotency_count(db_engine, scenario) == 1
    with db_engine.connect() as connection:
        state = connection.execute(
            text(
                "SELECT state FROM omnibase_meta.idempotency_records "
                "WHERE tenant_id = :tenant AND operation_id = :operation"
            ),
            {
                "tenant": str(scenario.tenant_id),
                "operation": str(scenario.operation_id),
            },
        ).scalar_one()
    assert state == "completed"


def _assert_retry_audits(
    db_engine: Engine,
    scenario: _Scenario,
    *,
    failure_request_id: str,
    success_request_id: str,
    failure_code: str,
) -> None:
    audits = _audit_rows(db_engine, scenario)
    assert [row["request_id"] for row in audits] == [
        failure_request_id,
        success_request_id,
    ]
    assert [row["decision"] for row in audits] == ["error", "allowed"]
    assert [row["details"]["reason_code"] for row in audits] == [
        failure_code,
        "CONTROLLED_CRUD_SUCCEEDED",
    ]


def test_real_lock_timeout_rolls_back_audits_and_allows_safe_retry(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    command = _command_with_timeouts(
        db_engine,
        scenario,
        statement_timeout_ms=750,
        lock_timeout_ms=100,
    )
    request_id = f"lock-timeout-{scenario.tenant_id.hex[:8]}"
    retry_request_id = f"lock-retry-{scenario.tenant_id.hex[:8]}"
    caplog.set_level(logging.DEBUG, logger="omnibase.controlled_data")

    # The executor reaches this row after Tenant/User/Resource and before
    # Authorization/Operation/Idempotency.  A failure audit has no FK to this
    # binding, so it also proves the independent audit transaction can commit
    # while the unrelated blocker remains held.
    with db_engine.connect() as blocker:
        blocker_transaction = blocker.begin()
        blocker.execute(
            text(
                "SELECT id FROM omnibase_meta.data_table_bindings "
                "WHERE tenant_id = :tenant AND resource_id = :resource FOR UPDATE"
            ),
            {
                "tenant": str(scenario.tenant_id),
                "resource": str(scenario.resource_id),
            },
        ).one()
        try:
            with pytest.raises(ControlledCrudServiceError) as caught:
                execute_controlled_crud_audited(
                    _session_factory(db_engine),
                    command,
                    audit=ControlledCrudAuditContext(
                        request_id=request_id,
                        risk_level="R1",
                    ),
                    executor=builtin_atomic_controlled_crud_executor,
                )
        finally:
            blocker_transaction.rollback()

    assert caught.value.code == "CONTROLLED_CRUD_LOCK_TIMEOUT"
    assert str(caught.value) == "CONTROLLED_CRUD_LOCK_TIMEOUT"
    assert caught.value.status_code == 503
    assert caught.value.retryable is True
    assert caught.value.__cause__ is None
    _assert_mutation_rolled_back(db_engine, scenario)
    _assert_code_only_timeout_audit(
        db_engine,
        scenario,
        request_id=request_id,
        code="CONTROLLED_CRUD_LOCK_TIMEOUT",
        status_code=503,
        captured_logs=caplog.text,
    )

    result = execute_controlled_crud_audited(
        _session_factory(db_engine),
        command,
        audit=ControlledCrudAuditContext(
            request_id=retry_request_id,
            risk_level="R1",
        ),
        executor=builtin_atomic_controlled_crud_executor,
    )
    assert result.replayed is False
    assert result.affected_rows == 1
    _assert_retry_completed_once(db_engine, scenario)
    _assert_retry_audits(
        db_engine,
        scenario,
        failure_request_id=request_id,
        success_request_id=retry_request_id,
        failure_code="CONTROLLED_CRUD_LOCK_TIMEOUT",
    )


def test_real_statement_timeout_rolls_back_audits_and_allows_safe_retry(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
    caplog: pytest.LogCaptureFixture,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    command = _command_with_timeouts(
        db_engine,
        scenario,
        statement_timeout_ms=100,
        lock_timeout_ms=50,
    )
    request_id = f"statement-timeout-{scenario.tenant_id.hex[:8]}"
    retry_request_id = f"statement-retry-{scenario.tenant_id.hex[:8]}"
    function_name = f"slow_{scenario.tenant_id.hex[:8]}"
    trigger_name = f"slow_{scenario.resource_id.hex[:8]}"
    caplog.set_level(logging.DEBUG, logger="omnibase.controlled_data")

    # A tenant-local test trigger makes the mutation statement itself slow.
    # lock_timeout cannot fire because pg_sleep does not wait on a lock.
    with db_engine.begin() as connection:
        connection.execute(
            text(
                f'CREATE FUNCTION "{scenario.tenant_schema}"."{function_name}"() '
                "RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN "
                "PERFORM pg_sleep(0.3); RETURN NEW; END $$"
            )
        )
        connection.execute(
            text(
                f'CREATE TRIGGER "{trigger_name}" BEFORE UPDATE ON '
                f'"{scenario.tenant_schema}"."{scenario.physical_table}" '
                f'FOR EACH ROW EXECUTE FUNCTION "{scenario.tenant_schema}".'
                f'"{function_name}"()'
            )
        )

    with pytest.raises(ControlledCrudServiceError) as caught:
        execute_controlled_crud_audited(
            _session_factory(db_engine),
            command,
            audit=ControlledCrudAuditContext(request_id=request_id, risk_level="R1"),
            executor=builtin_atomic_controlled_crud_executor,
        )

    assert caught.value.code == "CONTROLLED_CRUD_STATEMENT_TIMEOUT"
    assert str(caught.value) == "CONTROLLED_CRUD_STATEMENT_TIMEOUT"
    assert caught.value.status_code == 504
    assert caught.value.retryable is True
    assert caught.value.__cause__ is None
    _assert_mutation_rolled_back(db_engine, scenario)
    _assert_code_only_timeout_audit(
        db_engine,
        scenario,
        request_id=request_id,
        code="CONTROLLED_CRUD_STATEMENT_TIMEOUT",
        status_code=504,
        captured_logs=caplog.text,
    )

    with db_engine.begin() as connection:
        connection.execute(
            text(
                f'DROP TRIGGER "{trigger_name}" ON '
                f'"{scenario.tenant_schema}"."{scenario.physical_table}"'
            )
        )
        connection.execute(text(f'DROP FUNCTION "{scenario.tenant_schema}"."{function_name}"()'))

    result = execute_controlled_crud_audited(
        _session_factory(db_engine),
        command,
        audit=ControlledCrudAuditContext(
            request_id=retry_request_id,
            risk_level="R1",
        ),
        executor=builtin_atomic_controlled_crud_executor,
    )
    assert result.replayed is False
    assert result.affected_rows == 1
    _assert_retry_completed_once(db_engine, scenario)
    _assert_retry_audits(
        db_engine,
        scenario,
        failure_request_id=request_id,
        success_request_id=retry_request_id,
        failure_code="CONTROLLED_CRUD_STATEMENT_TIMEOUT",
    )
