"""Real PostgreSQL gates for the P34.3 controlled-write HTTP router.

The module is inert unless the shared destructive-test sentinel approves an
explicit ``omnibase_test_*`` database and restricted non-owner role.  It uses
the real router bootstrap and built-in audited executor; no production or
developer database is discovered here.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace
from typing import Protocol

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from omnibase.controlled_data.execution_service import (
    ControlledCrudAuditContext,
    ControlledCrudServiceError,
    SuccessAuditHook,
    builtin_atomic_controlled_crud_executor,
    builtin_atomic_controlled_crud_lifecycle_executor,
    execute_controlled_crud_audited,
)
from omnibase.controlled_data.executor import ControlledCrudCommand, ControlledCrudResult
from omnibase.controlled_data.identifiers import column_identifier, table_identifier
from omnibase.controlled_data.router import router
from omnibase.tenants.dependencies import get_current_principal
from tests.integration.test_p34_3_controlled_data_executor import (
    _audit_rows as _executor_audit_rows,
)
from tests.integration.test_p34_3_controlled_data_executor import (
    _create_scenario as _create_executor_scenario,
)
from tests.integration.test_p34_3_controlled_data_executor import (
    _idempotency_count as _executor_idempotency_count,
)
from tests.integration.test_p34_3_controlled_data_executor import (
    _session_factory as _executor_session_factory,
)
from tests.integration.test_p34_3_controlled_data_executor import (
    _tenant_values as _executor_tenant_values,
)

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.3 router integration requires OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class _OwnedResources(Protocol):
    def add(self, tenant_id: str, schema_name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class _RouterScenario:
    tenant_id: uuid.UUID
    owner_id: uuid.UUID
    resource_id: uuid.UUID
    column_id: uuid.UUID
    tenant_schema: str
    physical_table: str
    physical_column: str


class _SlowAtomicLifecycleExecutor:
    """Keep one lifecycle open long enough for the concurrent request to wait."""

    supports_atomic_lifecycle = True

    def __init__(self, delay_seconds: float = 0.3) -> None:
        self._delay_seconds = delay_seconds

    def __call__(
        self,
        session: Session,
        command: ControlledCrudCommand,
        *,
        success_audit_hook: SuccessAuditHook,
    ) -> ControlledCrudResult:
        time.sleep(self._delay_seconds)
        return builtin_atomic_controlled_crud_lifecycle_executor(
            session,
            command,
            success_audit_hook=success_audit_hook,
        )


def _upgrade_head() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _create_scenario(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
    *,
    owner_is_admin: bool = False,
) -> _RouterScenario:
    """Adapt the executor scenario to a Router-eligible user-owned resource."""
    _upgrade_head()
    tenant_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    column_id = uuid.uuid4()
    suffix = tenant_id.hex[:8]
    tenant_schema = f"tenant_{suffix}"
    physical_table = table_identifier(resource_id)
    physical_column = column_identifier(column_id)

    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{tenant_schema}"'))
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id, name, slug, schema_name, is_default, is_active) "
                "VALUES (:id, :name, :slug, :schema, FALSE, TRUE)"
            ),
            {
                "id": str(tenant_id),
                "name": "P34.3 router isolated tenant",
                "slug": f"p343-router-{suffix}",
                "schema": tenant_schema,
            },
        )
    run_owned_resources.add(str(tenant_id), tenant_schema)
    _upgrade_head()

    with db_engine.begin() as connection:
        connection.execute(
            text(
                f'INSERT INTO "{tenant_schema}".users '  # noqa: S608
                "(id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, :email, :password_hash, :is_admin, TRUE)"
            ),
            {
                "id": str(owner_id),
                "email": f"router-{suffix}@example.invalid",
                "password_hash": "integration-test-not-a-real-password-hash",
                "is_admin": owner_is_admin,
            },
        )
        connection.execute(
            text(
                f'CREATE TABLE "{tenant_schema}"."{physical_table}" '
                f'("{physical_column}" VARCHAR(500) NOT NULL)'
            )
        )
        connection.execute(
            text(
                f'INSERT INTO "{tenant_schema}"."{physical_table}" '  # noqa: S608
                f"(\"{physical_column}\") VALUES ('before')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(id, tenant_id, kind, owner_type, owner_id, display_name, state, "
                "version, policy_class, physical_locator) VALUES "
                "(:id, :tenant, 'controlled_table', 'user', :owner, 'Router table', "
                "'active', 1, 'tenant_managed', CAST(:locator AS jsonb))"
            ),
            {
                "id": str(resource_id),
                "tenant": str(tenant_id),
                "owner": str(owner_id),
                "locator": ('{"schema":"' + tenant_schema + '","table":"' + physical_table + '"}'),
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.data_table_bindings "
                "(id, tenant_id, resource_id, workspace_id, display_name, policy_class, "
                "physical_table_name, state, resource_version, version, "
                "created_by_actor_id) VALUES (:id, :tenant, :resource, NULL, "
                "'Router table', 'tenant_managed', :physical, 'active', 1, 1, :actor)"
            ),
            {
                "id": str(binding_id),
                "tenant": str(tenant_id),
                "resource": str(resource_id),
                "physical": physical_table,
                "actor": str(owner_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.data_column_bindings "
                "(id, tenant_id, table_binding_id, display_name, physical_column_name, "
                "data_type, type_args, nullable, ordinal, state, version) VALUES "
                "(:id, :tenant, :binding, 'Value', :physical, 'string', "
                "'{\"max_length\": 500}'::jsonb, FALSE, 1, 'active', 1)"
            ),
            {
                "id": str(column_id),
                "tenant": str(tenant_id),
                "binding": str(binding_id),
                "physical": physical_column,
            },
        )

    return _RouterScenario(
        tenant_id=tenant_id,
        owner_id=owner_id,
        resource_id=resource_id,
        column_id=column_id,
        tenant_schema=tenant_schema,
        physical_table=physical_table,
        physical_column=physical_column,
    )


def _add_user(
    db_engine: Engine,
    scenario: _RouterScenario,
    *,
    is_admin: bool = False,
) -> uuid.UUID:
    user_id = uuid.uuid4()
    with db_engine.begin() as connection:
        connection.execute(
            text(
                f'INSERT INTO "{scenario.tenant_schema}".users '  # noqa: S608
                "(id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, :email, :password_hash, :is_admin, TRUE)"
            ),
            {
                "id": str(user_id),
                "email": f"router-{user_id.hex[:8]}@example.invalid",
                "password_hash": "integration-test-not-a-real-password-hash",
                "is_admin": is_admin,
            },
        )
    return user_id


def _principal(
    scenario: _RouterScenario,
    *,
    user_id: uuid.UUID | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        tenant=SimpleNamespace(
            id=str(scenario.tenant_id),
            schema_name=scenario.tenant_schema,
            is_active=True,
        ),
        user=SimpleNamespace(
            id=str(user_id or scenario.owner_id),
            is_active=True,
            is_tenant_admin=False,
        ),
    )


def _app(
    db_engine: Engine,
    *,
    principal: SimpleNamespace,
    executor: object = builtin_atomic_controlled_crud_lifecycle_executor,
) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    @app.exception_handler(HTTPException)
    async def http_error(_request: object, exc: HTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    app.dependency_overrides[get_current_principal] = lambda: principal
    app.state.controlled_crud_session_factory = sessionmaker(bind=db_engine)
    app.state.controlled_crud_executor = executor
    return app


def _payload(
    scenario: _RouterScenario,
    *,
    key: str,
    value: str = "after",
    timeout_ms: int = 2_000,
) -> dict[str, object]:
    return {
        "mutation": {
            "kind": "update",
            "resource_id": str(scenario.resource_id),
            "resource_version": 1,
            "idempotency_key": key,
            "timeout_ms": timeout_ms,
            "predicate": {
                "kind": "compare",
                "column_id": str(scenario.column_id),
                "op": "eq",
                "value": "before",
            },
            "max_rows": 1,
            "values": {str(scenario.column_id): value},
        }
    }


def _post(
    app: FastAPI,
    payload: dict[str, object],
    *,
    request_id: str,
) -> tuple[int, dict[str, object], str]:
    response = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/controlled-data/rows/mutate",
        json=payload,
        headers={"X-Request-Id": request_id},
    )
    return response.status_code, response.json(), response.text


def _tenant_values(db_engine: Engine, scenario: _RouterScenario) -> list[str]:
    with db_engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    f'SELECT "{scenario.physical_column}" FROM '  # noqa: S608
                    f'"{scenario.tenant_schema}"."{scenario.physical_table}" '
                    f'ORDER BY "{scenario.physical_column}"'
                )
            ).scalars()
        )


def _operation_rows(db_engine: Engine, scenario: _RouterScenario) -> list[dict[str, object]]:
    with db_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT id, state, attempt_count, version FROM omnibase_meta.operations "
                    "WHERE tenant_id = :tenant AND actor_id = :actor "
                    "AND resource_id = :resource AND kind = 'data.rows.update' "
                    "ORDER BY created_at, id"
                ),
                {
                    "tenant": str(scenario.tenant_id),
                    "actor": str(scenario.owner_id),
                    "resource": str(scenario.resource_id),
                },
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _audit_rows(
    db_engine: Engine,
    *,
    request_ids: tuple[str, ...],
) -> list[dict[str, object]]:
    with db_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT tenant_id, request_id, operation_id, decision, status_code, "
                    "details FROM omnibase_meta.audit_events "
                    "WHERE request_id IN :request_ids ORDER BY created_at, id"
                ).bindparams(bindparam("request_ids", expanding=True)),
                {"request_ids": request_ids},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _assert_no_locator_leak(exposed: str, *scenarios: _RouterScenario) -> None:
    lowered = exposed.lower()
    for scenario in scenarios:
        for forbidden in (
            scenario.tenant_schema,
            scenario.physical_table,
            scenario.physical_column,
            "physical_locator",
            "raw_sql",
            "sqlstate",
            "ctid",
        ):
            assert forbidden.lower() not in lowered


def test_serial_same_key_replays_one_operation_and_one_mutation(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    app = _app(db_engine, principal=_principal(scenario))
    payload = _payload(scenario, key=f"serial-{uuid.uuid4().hex}")

    first = _post(app, payload, request_id=f"router-first-{scenario.tenant_id.hex[:8]}")
    assert first[0] == 200
    assert first[1]["replayed"] is False
    with db_engine.connect() as connection:
        lifecycle_counts = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM omnibase_meta.authorization_contexts "
                " WHERE tenant_id = :tenant AND actor_user_id = :actor), "
                "(SELECT count(*) FROM omnibase_meta.operations "
                " WHERE tenant_id = :tenant AND actor_id = :actor), "
                "(SELECT count(*) FROM omnibase_meta.idempotency_records "
                " WHERE tenant_id = :tenant), "
                "(SELECT count(*) FROM omnibase_meta.audit_events "
                " WHERE request_id = :request_id)"
            ),
            {
                "tenant": str(scenario.tenant_id),
                "actor": str(scenario.owner_id),
                "request_id": first[1]["request_id"],
            },
        ).one()
    assert tuple(lifecycle_counts) == (1, 1, 1, 1)

    second = _post(app, payload, request_id=f"router-replay-{scenario.tenant_id.hex[:8]}")

    assert first[0] == second[0] == 200
    assert second[1]["replayed"] is True
    assert first[1]["operation_id"] == second[1]["operation_id"]
    assert first[1]["affected_rows"] == second[1]["affected_rows"] == 1
    assert _tenant_values(db_engine, scenario) == ["after"]
    operations = _operation_rows(db_engine, scenario)
    assert len(operations) == 1
    assert operations[0]["state"] == "succeeded"
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.idempotency_records "
                    "WHERE tenant_id = :tenant AND operation_id = :operation"
                ),
                {"tenant": str(scenario.tenant_id), "operation": operations[0]["id"]},
            ).scalar_one()
            == 1
        )
    audits = _audit_rows(
        db_engine,
        request_ids=(first[1]["request_id"], second[1]["request_id"]),  # type: ignore[arg-type]
    )
    assert [row["details"]["reason_code"] for row in audits] == [  # type: ignore[index]
        "CONTROLLED_CRUD_SUCCEEDED",
        "CONTROLLED_CRUD_REPLAYED",
    ]
    _assert_no_locator_leak(f"{first[2]}\n{second[2]}\n{audits!r}", scenario)


def test_same_key_payload_drift_conflicts_without_second_mutation(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    app = _app(db_engine, principal=_principal(scenario))
    key = f"drift-{uuid.uuid4().hex}"
    first_request_id = f"router-drift-first-{scenario.tenant_id.hex[:8]}"
    conflict_request_id = f"router-drift-conflict-{scenario.tenant_id.hex[:8]}"

    first = _post(
        app,
        _payload(scenario, key=key),
        request_id=first_request_id,
    )
    conflict = _post(
        app,
        _payload(scenario, key=key, value="attacker-drift"),
        request_id=conflict_request_id,
    )

    assert first[0] == 200
    assert conflict[0] == 409
    assert conflict[1]["error"]["code"] == "CONTROLLED_CRUD_IDEMPOTENCY_CONFLICT"  # type: ignore[index]
    assert _tenant_values(db_engine, scenario) == ["after"]
    assert len(_operation_rows(db_engine, scenario)) == 1
    audits = _audit_rows(
        db_engine,
        request_ids=(first_request_id, conflict_request_id),
    )
    assert [row["decision"] for row in audits] == ["allowed", "error"]
    assert audits[1]["operation_id"] is None
    assert audits[1]["details"] == {
        "reason_code": "CONTROLLED_CRUD_IDEMPOTENCY_CONFLICT",
        "retryable": False,
    }
    _assert_no_locator_leak(f"{conflict[2]}\n{audits!r}", scenario)


def test_concurrent_same_key_executes_once_and_replays_one_stable_operation(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    app = _app(
        db_engine,
        principal=_principal(scenario),
        executor=_SlowAtomicLifecycleExecutor(),
    )
    payload = _payload(scenario, key=f"concurrent-{uuid.uuid4().hex}")
    request_ids = (
        f"router-race-a-{scenario.tenant_id.hex[:8]}",
        f"router-race-b-{scenario.tenant_id.hex[:8]}",
    )

    request_barrier = Barrier(2)

    def submit_request(request_id: str) -> tuple[int, dict[str, object], str]:
        request_barrier.wait(timeout=15)
        return _post(app, payload, request_id=request_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(submit_request, request_id) for request_id in request_ids]
        results = [future.result(timeout=30) for future in futures]

    assert [result[0] for result in results] == [200, 200]
    assert sorted(result[1]["replayed"] for result in results) == [False, True]
    assert len({result[1]["operation_id"] for result in results}) == 1
    assert _tenant_values(db_engine, scenario) == ["after"]
    operations = _operation_rows(db_engine, scenario)
    assert len(operations) == 1
    assert operations[0]["state"] == "succeeded"
    assert operations[0]["attempt_count"] == 1
    audits = _audit_rows(db_engine, request_ids=request_ids)
    assert sorted(row["details"]["reason_code"] for row in audits) == [  # type: ignore[index]
        "CONTROLLED_CRUD_REPLAYED",
        "CONTROLLED_CRUD_SUCCEEDED",
    ]
    _assert_no_locator_leak("\n".join(result[2] for result in results) + repr(audits), scenario)


def test_statement_timeout_reuses_queued_operation_then_succeeds(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    app = _app(db_engine, principal=_principal(scenario))
    key = f"timeout-{uuid.uuid4().hex}"
    payload = _payload(scenario, key=key, timeout_ms=100)
    failure_request_id = f"router-timeout-{scenario.tenant_id.hex[:8]}"
    retry_request_id = f"router-timeout-retry-{scenario.tenant_id.hex[:8]}"
    function_name = f"slow_router_{scenario.tenant_id.hex[:8]}"
    trigger_name = f"slow_router_{scenario.resource_id.hex[:8]}"

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

    failed = _post(app, payload, request_id=failure_request_id)
    assert failed[0] == 504
    assert failed[1]["error"]["code"] == "CONTROLLED_CRUD_STATEMENT_TIMEOUT"  # type: ignore[index]
    assert _tenant_values(db_engine, scenario) == ["before"]
    assert _operation_rows(db_engine, scenario) == []
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.authorization_contexts "
                    "WHERE tenant_id = :tenant AND actor_user_id = :actor"
                ),
                {"tenant": str(scenario.tenant_id), "actor": str(scenario.owner_id)},
            ).scalar_one()
            == 0
        )
        assert (
            connection.execute(
                text(
                    "SELECT count(*) FROM omnibase_meta.idempotency_records "
                    "WHERE tenant_id = :tenant"
                ),
                {"tenant": str(scenario.tenant_id)},
            ).scalar_one()
            == 0
        )
    failure_audits = _audit_rows(db_engine, request_ids=(failure_request_id,))
    assert len(failure_audits) == 1
    assert failure_audits[0]["operation_id"] is not None
    stable_operation_id = failure_audits[0]["operation_id"]

    with db_engine.begin() as connection:
        connection.execute(
            text(
                f'DROP TRIGGER "{trigger_name}" ON '
                f'"{scenario.tenant_schema}"."{scenario.physical_table}"'
            )
        )
        connection.execute(text(f'DROP FUNCTION "{scenario.tenant_schema}"."{function_name}"()'))

    retried = _post(app, payload, request_id=retry_request_id)
    assert retried[0] == 200
    assert retried[1]["replayed"] is False
    assert retried[1]["operation_id"] == str(stable_operation_id)
    assert _tenant_values(db_engine, scenario) == ["after"]
    completed = _operation_rows(db_engine, scenario)
    assert len(completed) == 1
    assert completed[0]["id"] == stable_operation_id
    assert completed[0]["state"] == "succeeded"
    assert completed[0]["attempt_count"] == 1
    audits = _audit_rows(
        db_engine,
        request_ids=(failure_request_id, retry_request_id),
    )
    assert [row["details"]["reason_code"] for row in audits] == [  # type: ignore[index]
        "CONTROLLED_CRUD_STATEMENT_TIMEOUT",
        "CONTROLLED_CRUD_SUCCEEDED",
    ]
    _assert_no_locator_leak(f"{failed[2]}\n{retried[2]}\n{audits!r}", scenario)


def test_cross_tenant_and_non_owner_denials_are_audited_without_locator_leaks(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    target = _create_scenario(db_engine, run_owned_resources)
    other_tenant = _create_scenario(db_engine, run_owned_resources)
    non_owner_id = _add_user(db_engine, target)
    cross_request_id = f"router-cross-{target.tenant_id.hex[:8]}"
    owner_request_id = f"router-owner-denied-{target.tenant_id.hex[:8]}"
    payload = _payload(target, key=f"denied-{uuid.uuid4().hex}")

    cross = _post(
        _app(db_engine, principal=_principal(other_tenant)),
        payload,
        request_id=cross_request_id,
    )
    non_owner = _post(
        _app(db_engine, principal=_principal(target, user_id=non_owner_id)),
        payload,
        request_id=owner_request_id,
    )

    assert cross[0] == 404
    assert cross[1]["error"]["code"] == "resource_not_found"  # type: ignore[index]
    assert non_owner[0] == 403
    assert non_owner[1]["error"]["code"] == "controlled_write_forbidden"  # type: ignore[index]
    assert _tenant_values(db_engine, target) == ["before"]
    assert _operation_rows(db_engine, target) == []
    audits = _audit_rows(
        db_engine,
        request_ids=(cross_request_id, owner_request_id),
    )
    assert [(row["request_id"], row["decision"], row["operation_id"]) for row in audits] == [
        (cross_request_id, "denied", None),
        (owner_request_id, "denied", None),
    ]
    assert {row["details"]["reason_code"] for row in audits} == {  # type: ignore[index]
        "resource_not_found",
        "controlled_write_forbidden",
    }
    _assert_no_locator_leak(
        f"{cross[2]}\n{non_owner[2]}\n{audits!r}",
        target,
        other_tenant,
    )


def test_operation_deadline_expires_by_database_clock_during_real_lock_wait(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    """Close the deadline race not covered by cancel/version lock-race gates."""
    scenario = _create_executor_scenario(db_engine, run_owned_resources)
    request_id = f"operation-deadline-{scenario.tenant_id.hex[:8]}"
    persisted_deadline: list[object] = []

    with ThreadPoolExecutor(max_workers=1) as pool, db_engine.connect() as blocker:
        transaction = blocker.begin()
        try:
            blocker_pid = blocker.execute(text("SELECT pg_backend_pid()")).scalar_one()
            deadline_at = blocker.execute(
                text(
                    "UPDATE omnibase_meta.operations "
                    "SET deadline_at = clock_timestamp() + interval '1250 milliseconds' "
                    "WHERE id = :operation AND tenant_id = :tenant RETURNING deadline_at"
                ),
                {
                    "operation": str(scenario.operation_id),
                    "tenant": str(scenario.tenant_id),
                },
            ).scalar_one()
            persisted_deadline.append(deadline_at)
            future = pool.submit(
                execute_controlled_crud_audited,
                _executor_session_factory(db_engine),
                scenario.command,
                audit=ControlledCrudAuditContext(request_id=request_id, risk_level="R1"),
                executor=builtin_atomic_controlled_crud_executor,
            )

            wait_deadline = time.monotonic() + 10
            while time.monotonic() < wait_deadline:
                with db_engine.connect() as observer:
                    blocked = observer.execute(
                        text(
                            "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                            "WHERE :blocker_pid = ANY(pg_blocking_pids(pid)))"
                        ),
                        {"blocker_pid": blocker_pid},
                    ).scalar_one()
                if blocked is True:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("executor never entered a PostgreSQL lock wait")

            clock_deadline = time.monotonic() + 5
            while time.monotonic() < clock_deadline:
                with db_engine.connect() as observer:
                    expired = observer.execute(
                        text("SELECT clock_timestamp() >= :deadline_at"),
                        {"deadline_at": persisted_deadline[0]},
                    ).scalar_one()
                if expired is True:
                    break
                time.sleep(0.02)
            else:
                raise AssertionError("database clock did not cross Operation deadline")
            transaction.commit()
        except Exception:
            transaction.rollback()
            raise

        with pytest.raises(ControlledCrudServiceError) as caught:
            future.result(timeout=10)

    assert caught.value.code == "CONTROLLED_CRUD_STATE_CONFLICT"
    assert caught.value.status_code == 409
    assert _executor_tenant_values(db_engine, scenario) == sorted(scenario.initial_values)
    assert _executor_idempotency_count(db_engine, scenario) == 0
    audits = _executor_audit_rows(db_engine, scenario)
    assert len(audits) == 1
    assert audits[0]["request_id"] == request_id
    assert audits[0]["decision"] == "error"
    assert audits[0]["status_code"] == 409
    assert audits[0]["details"]["reason_code"] == "CONTROLLED_CRUD_STATE_CONFLICT"  # type: ignore[index]
