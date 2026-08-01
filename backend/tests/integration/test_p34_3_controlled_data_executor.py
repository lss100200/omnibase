"""Formal isolated-PostgreSQL gates for the controlled CRUD executor.

This module is inert unless the shared destructive-test sentinel approves an
explicit ``omnibase_test_*`` database and restricted non-owner role. It never
discovers credentials or connects outside the guarded ``db_engine`` fixture.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Protocol

import pytest
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from omnibase.control_plane.models import IdempotencyRecord, OperationRecord
from omnibase.controlled_data.crud import (
    MutationColumnBinding,
    TrustedMutationLocator,
    canonical_request_hash,
)
from omnibase.controlled_data.crud_contracts import UpdateMutationRequest
from omnibase.controlled_data.execution_service import (
    ControlledCrudAuditContext,
    ControlledCrudServiceError,
    builtin_atomic_controlled_crud_executor,
    execute_controlled_crud_audited,
)
from omnibase.controlled_data.executor import (
    ControlledCrudCommand,
    ControlledCrudResult,
    ControlledCrudSuccessAuditError,
    TrustedUserRbacDecision,
    execute_controlled_crud,
)
from omnibase.controlled_data.identifiers import column_identifier, table_identifier

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.3 executor integration requires OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class _OwnedResources(Protocol):
    def add(self, tenant_id: str, schema_name: str) -> None: ...


@dataclass(frozen=True, slots=True)
class _Scenario:
    tenant_id: uuid.UUID
    workspace_id: uuid.UUID
    actor_id: uuid.UUID
    resource_id: uuid.UUID
    operation_id: uuid.UUID
    tenant_schema: str
    physical_table: str
    physical_column: str
    initial_values: tuple[str, ...]
    locator: TrustedMutationLocator
    request: UpdateMutationRequest
    command: ControlledCrudCommand


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
    initial_values: tuple[str, ...] = ("before",),
    max_rows: int = 1,
) -> _Scenario:
    _upgrade_head()
    tenant_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    resource_id = uuid.uuid4()
    binding_id = uuid.uuid4()
    column_id = uuid.uuid4()
    authorization_id = uuid.uuid4()
    operation_id = uuid.uuid4()
    suffix = tenant_id.hex[:8]
    tenant_schema = f"tenant_{suffix}"
    physical_table = table_identifier(resource_id)
    physical_column = column_identifier(column_id)
    request = UpdateMutationRequest.model_validate(
        {
            "resource_id": resource_id,
            "resource_version": 1,
            "idempotency_key": f"executor-{uuid.uuid4().hex}",
            "timeout_ms": 2_000,
            "max_rows": max_rows,
            "predicate": {
                "kind": "compare",
                "column_id": column_id,
                "op": "eq",
                "value": "before",
            },
            "values": {column_id: "after"},
        }
    )
    request_hash = canonical_request_hash(request)

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
                "name": "P34.3 executor isolated tenant",
                "slug": f"p343-executor-{suffix}",
                "schema": tenant_schema,
            },
        )
    run_owned_resources.add(str(tenant_id), tenant_schema)
    _upgrade_head()

    authorization_now = datetime.now(UTC)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                f'INSERT INTO "{tenant_schema}".users '  # noqa: S608
                "(id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, :email, :password_hash, TRUE, TRUE)"
            ),
            {
                "id": str(actor_id),
                "email": f"executor-{suffix}@example.invalid",
                "password_hash": "integration-test-not-a-real-password-hash",
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
                f'("{physical_column}") VALUES (:value)'
            ),
            [{"value": value} for value in initial_values],
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(id, tenant_id, kind, owner_type, owner_id, display_name, state, "
                "version, policy_class) VALUES (:id, :tenant, 'controlled_table', "
                "'workspace', :workspace, 'Executor table', 'active', 1, "
                "'workspace_private')"
            ),
            {
                "id": str(resource_id),
                "tenant": str(tenant_id),
                "workspace": str(workspace_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.data_table_bindings "
                "(id, tenant_id, resource_id, workspace_id, display_name, policy_class, "
                "physical_table_name, state, resource_version, version, "
                "created_by_actor_id) VALUES (:id, :tenant, :resource, :workspace, "
                "'Executor table', 'workspace_private', :physical, 'active', 1, 1, "
                ":actor)"
            ),
            {
                "id": str(binding_id),
                "tenant": str(tenant_id),
                "resource": str(resource_id),
                "workspace": str(workspace_id),
                "physical": physical_table,
                "actor": str(actor_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.data_column_bindings "
                "(id, tenant_id, table_binding_id, display_name, physical_column_name, "
                "data_type, type_args, nullable, ordinal, state, version) VALUES "
                "(:id, :tenant, :binding, 'Finding', :physical, 'string', "
                "'{\"max_length\": 500}'::jsonb, FALSE, 1, 'active', 1)"
            ),
            {
                "id": str(column_id),
                "tenant": str(tenant_id),
                "binding": str(binding_id),
                "physical": physical_column,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.authorization_contexts "
                "(id, tenant_id, workspace_id, source, actor_user_id, role_snapshot, "
                "actions, resource_ids, source_version, snapshot_hash, "
                "live_recheck_required, created_at, expires_at) VALUES "
                "(:id, :tenant, :workspace, 'user_rbac', :actor, "
                "ARRAY['tenant_admin']::varchar[], "
                "ARRAY['data.rows.update']::varchar[], ARRAY[:resource]::uuid[], "
                "1, :hash, TRUE, :created, :expires)"
            ),
            {
                "id": str(authorization_id),
                "tenant": str(tenant_id),
                "workspace": str(workspace_id),
                "actor": str(actor_id),
                "resource": str(resource_id),
                "hash": "a" * 64,
                "created": authorization_now,
                "expires": authorization_now + timedelta(minutes=10),
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.operations "
                "(id, tenant_id, workspace_id, actor_type, actor_id, resource_id, "
                "resource_version, request_hash, kind, state, risk_level, progress, "
                "attempt_count, version) VALUES (:id, :tenant, :workspace, 'user', "
                ":actor, :resource, 1, :hash, 'data.rows.update', 'queued', 'R1', "
                "0, 0, 1)"
            ),
            {
                "id": str(operation_id),
                "tenant": str(tenant_id),
                "workspace": str(workspace_id),
                "actor": str(actor_id),
                "resource": str(resource_id),
                "hash": request_hash,
            },
        )

    locator = TrustedMutationLocator(
        tenant_schema=tenant_schema,
        table_binding_id=binding_id,
        resource_id=resource_id,
        resource_version=1,
        physical_table_name=physical_table,
        columns={
            column_id: MutationColumnBinding(
                logical_id=column_id,
                physical_name=physical_column,
                data_type="string",
                type_args={"max_length": 500},
                nullable=False,
            )
        },
    )
    decision_now = datetime.now(UTC)
    decision = TrustedUserRbacDecision(
        decision_id=uuid.uuid4(),
        allowed=True,
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        resource_id=resource_id,
        resource_version=1,
        action="data.rows.update",
        authorization_context_id=authorization_id,
        source_version=1,
        snapshot_hash="a" * 64,
        roles=frozenset({"tenant_admin"}),
        user_is_active=True,
        tenant_is_active=True,
        evaluated_at=decision_now,
        expires_at=decision_now + timedelta(seconds=30),
    )
    command = ControlledCrudCommand(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_user_id=actor_id,
        authorization_context_id=authorization_id,
        operation_id=operation_id,
        locator=locator,
        request=request,
        decision=decision,
        lock_timeout_ms=2_000,
    )
    return _Scenario(
        tenant_id=tenant_id,
        workspace_id=workspace_id,
        actor_id=actor_id,
        resource_id=resource_id,
        operation_id=operation_id,
        tenant_schema=tenant_schema,
        physical_table=physical_table,
        physical_column=physical_column,
        initial_values=initial_values,
        locator=locator,
        request=request,
        command=command,
    )


def _session_factory(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=db_engine)


def _tenant_values(db_engine: Engine, scenario: _Scenario) -> list[str]:
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


def _operation_state(db_engine: Engine, scenario: _Scenario) -> dict[str, object]:
    with db_engine.connect() as connection:
        row = (
            connection.execute(
                text(
                    "SELECT state, progress, attempt_count, result_ref, "
                    "started_at, completed_at FROM omnibase_meta.operations "
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
    return dict(row)


def _idempotency_count(db_engine: Engine, scenario: _Scenario) -> int:
    with db_engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT count(*) FROM omnibase_meta.idempotency_records "
                "WHERE tenant_id = :tenant AND operation_id = :operation"
            ),
            {
                "tenant": str(scenario.tenant_id),
                "operation": str(scenario.operation_id),
            },
        ).scalar_one()


def _audit_rows(db_engine: Engine, scenario: _Scenario) -> list[dict[str, object]]:
    with db_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT request_id, decision, status_code, row_count, details "
                    "FROM omnibase_meta.audit_events "
                    "WHERE operation_id = :operation ORDER BY created_at, id"
                ),
                {"operation": str(scenario.operation_id)},
            )
            .mappings()
            .all()
        )
    return [dict(row) for row in rows]


def _assert_mutation_rolled_back(db_engine: Engine, scenario: _Scenario) -> None:
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


def test_executor_updates_and_exactly_replays_with_atomic_success_audits(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    factory = _session_factory(db_engine)
    suffix = scenario.tenant_id.hex[:8]

    first = execute_controlled_crud_audited(
        factory,
        scenario.command,
        audit=ControlledCrudAuditContext(
            request_id=f"executor-first-{suffix}",
            risk_level="R1",
        ),
        executor=builtin_atomic_controlled_crud_executor,
    )
    replay = execute_controlled_crud_audited(
        factory,
        scenario.command,
        audit=ControlledCrudAuditContext(
            request_id=f"executor-replay-{suffix}",
            risk_level="R1",
        ),
        executor=builtin_atomic_controlled_crud_executor,
    )

    assert first.affected_rows == 1
    assert first.replayed is False
    assert replay.affected_rows == 1
    assert replay.replayed is True
    assert _tenant_values(db_engine, scenario) == ["after"]
    operation = _operation_state(db_engine, scenario)
    assert operation["state"] == "succeeded"
    assert operation["attempt_count"] == 1
    assert _idempotency_count(db_engine, scenario) == 1
    audits = _audit_rows(db_engine, scenario)
    assert [row["request_id"] for row in audits] == [
        f"executor-first-{suffix}",
        f"executor-replay-{suffix}",
    ]
    assert [row["decision"] for row in audits] == ["allowed", "allowed"]
    assert [row["row_count"] for row in audits] == [1, 1]
    assert [row["details"]["reason_code"] for row in audits] == [
        "CONTROLLED_CRUD_SUCCEEDED",
        "CONTROLLED_CRUD_REPLAYED",
    ]
    persisted_audit = str(audits).lower()
    for forbidden in (
        "ctid",
        "physical",
        scenario.tenant_schema.lower(),
        scenario.physical_table.lower(),
    ):
        assert forbidden not in persisted_audit


def test_wrong_but_valid_tenant_schema_fails_closed_before_tenant_data(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    wrong_schema = f"tenant_{uuid.uuid4().hex[:8]}"
    wrong_locator = replace(scenario.locator, tenant_schema=wrong_schema)
    wrong_command = replace(scenario.command, locator=wrong_locator)
    request_id = f"wrong-schema-{scenario.tenant_id.hex[:8]}"

    with pytest.raises(ControlledCrudServiceError) as caught:
        execute_controlled_crud_audited(
            _session_factory(db_engine),
            wrong_command,
            audit=ControlledCrudAuditContext(request_id=request_id, risk_level="R1"),
            executor=builtin_atomic_controlled_crud_executor,
        )

    assert caught.value.code == "CONTROLLED_CRUD_AUTHORIZATION_DENIED"
    assert caught.value.status_code == 403
    _assert_mutation_rolled_back(db_engine, scenario)
    audits = _audit_rows(db_engine, scenario)
    assert len(audits) == 1
    assert audits[0]["request_id"] == request_id
    assert audits[0]["decision"] == "denied"
    assert audits[0]["details"] == {
        "error_code": "CONTROLLED_CRUD_AUTHORIZATION_DENIED",
        "reason_code": "CONTROLLED_CRUD_AUTHORIZATION_DENIED",
        "retryable": False,
    }
    assert wrong_schema not in str(audits).lower()


def test_max_rows_overflow_rolls_back_mutation_and_commits_failure_audit(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(
        db_engine,
        run_owned_resources,
        initial_values=("before", "before"),
        max_rows=1,
    )
    request_id = f"overflow-{scenario.tenant_id.hex[:8]}"

    with pytest.raises(ControlledCrudServiceError) as caught:
        execute_controlled_crud_audited(
            _session_factory(db_engine),
            scenario.command,
            audit=ControlledCrudAuditContext(request_id=request_id, risk_level="R1"),
            executor=builtin_atomic_controlled_crud_executor,
        )

    assert caught.value.code == "CONTROLLED_CRUD_BUDGET_EXCEEDED"
    assert caught.value.status_code == 422
    _assert_mutation_rolled_back(db_engine, scenario)
    audits = _audit_rows(db_engine, scenario)
    assert len(audits) == 1
    assert audits[0]["request_id"] == request_id
    assert audits[0]["decision"] == "denied"
    assert audits[0]["status_code"] == 422
    assert audits[0]["row_count"] is None
    assert audits[0]["details"] == {
        "error_code": "CONTROLLED_CRUD_BUDGET_EXCEEDED",
        "reason_code": "CONTROLLED_CRUD_BUDGET_EXCEEDED",
        "retryable": False,
    }


def test_success_audit_insert_failure_rolls_back_every_atomic_write(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    failed_audit_request_id = f"audit-insert-fail-{scenario.tenant_id.hex[:8]}"

    def failing_success_audit_insert(
        session: Session,
        result: ControlledCrudResult,
        operation: OperationRecord,
        idempotency: IdempotencyRecord,
    ) -> None:
        assert result.affected_rows == 1
        assert operation.state == "succeeded"
        assert idempotency.state == "completed"
        session.execute(
            text(
                "INSERT INTO omnibase_meta.audit_events "
                "(tenant_id, request_id, actor_type, actor_id, workspace_id, "
                "resource_id, operation_id, action, decision, risk_level, input_hash, "
                "before_version, after_version, status_code, row_count, duration_ms, "
                "details) VALUES (:tenant, :request_id, 'user', :actor, :workspace, "
                ":resource, :operation, 'data.rows.update', 'allowed', 'RX', :hash, "
                "1, 1, 200, 1, 0, '{}'::jsonb)"
            ),
            {
                "tenant": str(scenario.tenant_id),
                "request_id": failed_audit_request_id,
                "actor": str(scenario.actor_id),
                "workspace": str(scenario.workspace_id),
                "resource": str(scenario.resource_id),
                "operation": str(scenario.operation_id),
                "hash": canonical_request_hash(scenario.request),
            },
        )

    with (
        Session(db_engine) as session,
        pytest.raises(ControlledCrudSuccessAuditError),
    ):
        execute_controlled_crud(
            session,
            scenario.command,
            success_audit_hook=failing_success_audit_insert,
        )

    _assert_mutation_rolled_back(db_engine, scenario)
    assert _audit_rows(db_engine, scenario) == []


def test_concurrent_same_key_and_operation_execute_once_then_replay(
    db_engine: Engine,
    run_owned_resources: _OwnedResources,
) -> None:
    scenario = _create_scenario(db_engine, run_owned_resources)
    factory = _session_factory(db_engine)
    start = Barrier(2)
    request_ids = [
        f"concurrent-a-{scenario.tenant_id.hex[:8]}",
        f"concurrent-b-{scenario.tenant_id.hex[:8]}",
    ]

    def run_one(request_id: str) -> ControlledCrudResult:
        start.wait(timeout=10)
        return execute_controlled_crud_audited(
            factory,
            scenario.command,
            audit=ControlledCrudAuditContext(request_id=request_id, risk_level="R1"),
            executor=builtin_atomic_controlled_crud_executor,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_one, request_id) for request_id in request_ids]
        results = [future.result(timeout=30) for future in futures]

    assert sorted(result.replayed for result in results) == [False, True]
    assert [result.affected_rows for result in results] == [1, 1]
    assert _tenant_values(db_engine, scenario) == ["after"]
    operation = _operation_state(db_engine, scenario)
    assert operation["state"] == "succeeded"
    assert operation["attempt_count"] == 1
    assert _idempotency_count(db_engine, scenario) == 1
    audits = _audit_rows(db_engine, scenario)
    assert {row["request_id"] for row in audits} == set(request_ids)
    assert [row["decision"] for row in audits] == ["allowed", "allowed"]
    assert sorted(row["details"]["reason_code"] for row in audits) == [
        "CONTROLLED_CRUD_REPLAYED",
        "CONTROLLED_CRUD_SUCCEEDED",
    ]
