"""Audit orchestration contract tests for controlled CRUD execution."""

from __future__ import annotations

from contextlib import AbstractContextManager
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from omnibase.controlled_data.crud import canonical_request_hash
from omnibase.controlled_data.crud_contracts import UpdateMutationRequest
from omnibase.controlled_data.execution_service import (
    ControlledCrudAtomicAuditContractError,
    ControlledCrudAuditContext,
    ControlledCrudAuditPersistenceError,
    ControlledCrudServiceError,
    builtin_atomic_controlled_crud_executor,
    execute_controlled_crud_audited,
    execute_controlled_crud_lifecycle_audited,
)
from omnibase.controlled_data.executor import (
    ControlledCrudCommand,
    ControlledCrudConflict,
    ControlledCrudDatabaseFailure,
    ControlledCrudResult,
)

TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
WORKSPACE_ID = UUID("20000000-0000-0000-0000-000000000001")
ACTOR_ID = UUID("30000000-0000-0000-0000-000000000001")
RESOURCE_ID = UUID("40000000-0000-0000-0000-000000000001")
OPERATION_ID = UUID("80000000-0000-0000-0000-000000000001")


class _Transaction(AbstractContextManager[None]):
    def __init__(self, events: list[str], name: str) -> None:
        self.events = events
        self.name = name

    def __enter__(self) -> None:
        self.events.append(f"{self.name}.begin")

    def __exit__(self, exc_type, exc, traceback) -> bool:
        self.events.append(f"{self.name}.{'rollback' if exc_type else 'commit'}")
        return False


class _Session:
    def __init__(self, events: list[str], name: str) -> None:
        self.events = events
        self.name = name

    def begin(self) -> _Transaction:
        return _Transaction(self.events, self.name)

    def close(self) -> None:
        self.events.append(f"{self.name}.close")


class _SessionFactory:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.created: list[_Session] = []

    def __call__(self) -> Session:
        session = _Session(self.events, f"session{len(self.created) + 1}")
        self.created.append(session)
        return cast("Session", session)


class _AtomicExecutor:
    supports_atomic_success_audit = True

    def __init__(self, callback) -> None:
        self.callback = callback

    def __call__(self, session, command, *, success_audit_hook):
        return self.callback(
            session,
            command,
            success_audit_hook=success_audit_hook,
        )


class _AtomicLifecycleExecutor:
    supports_atomic_lifecycle = True

    def __init__(self, callback) -> None:
        self.callback = callback

    def __call__(self, session, command, *, success_audit_hook):
        return self.callback(
            session,
            command,
            success_audit_hook=success_audit_hook,
        )


def _request() -> UpdateMutationRequest:
    column_id = UUID("60000000-0000-0000-0000-000000000001")
    return UpdateMutationRequest.model_validate(
        {
            "resource_id": RESOURCE_ID,
            "resource_version": 4,
            "idempotency_key": "idem.audit-0001",
            "timeout_ms": 2000,
            "max_rows": 2,
            "predicate": {
                "kind": "compare",
                "column_id": column_id,
                "op": "eq",
                "value": "before",
            },
            "values": {column_id: "after"},
        }
    )


def _command() -> ControlledCrudCommand:
    request = _request()
    return cast(
        "ControlledCrudCommand",
        SimpleNamespace(
            tenant_id=TENANT_ID,
            workspace_id=WORKSPACE_ID,
            actor_user_id=ACTOR_ID,
            operation_id=OPERATION_ID,
            request=request,
        ),
    )


def _completed_records(result: ControlledCrudResult):
    metadata = result.as_safe_metadata()
    operation = SimpleNamespace(
        id=str(OPERATION_ID),
        tenant_id=str(TENANT_ID),
        actor_type="user",
        actor_id=str(ACTOR_ID),
        resource_id=str(RESOURCE_ID),
        resource_version=4,
        request_hash=result.request_hash,
        kind="data.rows.update",
        risk_level="R1",
        state="succeeded",
        result_ref=metadata,
    )
    idempotency = SimpleNamespace(
        operation_id=str(OPERATION_ID),
        request_hash=result.request_hash,
        state="completed",
        response_ref=metadata,
    )
    return operation, idempotency


def test_success_audit_is_invoked_inside_executor_transaction(monkeypatch) -> None:
    events: list[str] = []
    factory = _SessionFactory(events)
    command = _command()
    request_hash = canonical_request_hash(command.request)
    captured: dict[str, object] = {}

    def append(session, **kwargs):
        events.append("audit.append")
        captured.update(kwargs)

    monkeypatch.setattr(
        "omnibase.controlled_data.execution_service._append_success_audit_event", append
    )

    def executor(session, received, *, success_audit_hook):
        assert received is command
        with session.begin():
            events.append("mutation.completed")
            result = ControlledCrudResult(
                operation_id=OPERATION_ID,
                resource_id=RESOURCE_ID,
                resource_version=4,
                action="data.rows.update",
                affected_rows=2,
                request_hash=request_hash,
                replayed=False,
            )
            operation, idempotency = _completed_records(result)
            success_audit_hook(session, result, operation, idempotency)
            events.append("executor.after_audit")
        return result

    result = execute_controlled_crud_audited(
        factory,
        command,
        audit=ControlledCrudAuditContext(request_id="req.audit-1", risk_level="R1"),
        executor=_AtomicExecutor(executor),
    )

    assert result.affected_rows == 2
    assert events == [
        "session1.begin",
        "mutation.completed",
        "audit.append",
        "executor.after_audit",
        "session1.commit",
        "session1.close",
    ]
    assert captured == {
        "tenant_id": str(TENANT_ID),
        "request_id": "req.audit-1",
        "actor_type": "user",
        "actor_id": str(ACTOR_ID),
        "workspace_id": str(WORKSPACE_ID),
        "resource_id": str(RESOURCE_ID),
        "operation_id": str(OPERATION_ID),
        "action": "data.rows.update",
        "decision": "allowed",
        "risk_level": "R1",
        "input_hash": request_hash,
        "before_version": 4,
        "after_version": 4,
        "status_code": 200,
        "row_count": 2,
        "duration_ms": captured["duration_ms"],
        "details": {"reason_code": "CONTROLLED_CRUD_SUCCEEDED", "retryable": False},
    }
    assert isinstance(captured["duration_ms"], int)


def test_failure_audit_starts_only_after_mutation_rollback(monkeypatch) -> None:
    events: list[str] = []
    factory = _SessionFactory(events)
    command = _command()
    captured: dict[str, object] = {}

    def append(session, **kwargs):
        events.append("audit.append")
        captured.update(kwargs)

    monkeypatch.setattr("omnibase.controlled_data.execution_service.append_audit_event", append)

    def executor(session, received, *, success_audit_hook):
        del received, success_audit_hook
        with session.begin():
            events.append("mutation.started")
            raise ControlledCrudConflict(
                'unsafe tenant_schema="tenant_secret" ctid=(0,1) value=secret'
            )

    with pytest.raises(ControlledCrudServiceError) as caught:
        execute_controlled_crud_audited(
            factory,
            command,
            audit=ControlledCrudAuditContext(request_id="req.audit-2", risk_level="R1"),
            executor=_AtomicExecutor(executor),
        )

    assert events == [
        "session1.begin",
        "mutation.started",
        "session1.rollback",
        "session1.close",
        "session2.begin",
        "audit.append",
        "session2.commit",
        "session2.close",
    ]
    assert caught.value.code == "CONTROLLED_CRUD_STATE_CONFLICT"
    assert str(caught.value) == "CONTROLLED_CRUD_STATE_CONFLICT"
    assert caught.value.__cause__ is None
    assert captured["decision"] == "error"
    assert captured["details"] == {
        "error_code": "CONTROLLED_CRUD_STATE_CONFLICT",
        "reason_code": "CONTROLLED_CRUD_STATE_CONFLICT",
        "retryable": False,
    }
    assert "secret" not in str(caught.value).lower()
    assert "schema" not in str(captured).lower()
    assert "ctid" not in str(captured).lower()


@pytest.mark.parametrize(
    "sqlstate,expected_code,status_code",
    [
        ("55P03", "CONTROLLED_CRUD_LOCK_TIMEOUT", 503),
        ("57014", "CONTROLLED_CRUD_STATEMENT_TIMEOUT", 504),
        ("40001", "CONTROLLED_CRUD_SERIALIZATION_CONFLICT", 409),
        ("40P01", "CONTROLLED_CRUD_DEADLOCK", 503),
    ],
)
def test_database_errors_are_mapped_without_exception_text(
    monkeypatch, sqlstate: str, expected_code: str, status_code: int
) -> None:
    events: list[str] = []
    factory = _SessionFactory(events)
    captured: dict[str, object] = {}

    def append(session, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("omnibase.controlled_data.execution_service.append_audit_event", append)

    class DriverError(Exception):
        pass

    original = DriverError("physical_table=secret ctid=(0,1)")
    original.sqlstate = sqlstate  # type: ignore[attr-defined]
    database_error = OperationalError(
        "UPDATE tenant_secret.physical_table SET secret=:value",
        {"value": "private"},
        original,
    )

    def executor(session, command, *, success_audit_hook):
        del command, success_audit_hook
        with session.begin():
            raise database_error

    with pytest.raises(ControlledCrudServiceError) as caught:
        execute_controlled_crud_audited(
            factory,
            _command(),
            audit=ControlledCrudAuditContext(request_id="req.audit-db", risk_level="R1"),
            executor=_AtomicExecutor(executor),
        )

    assert caught.value.code == expected_code
    assert caught.value.status_code == status_code
    assert caught.value.retryable is True
    assert str(caught.value) == expected_code
    assert "secret" not in str(caught.value).lower()
    assert captured["details"] == {
        "error_code": expected_code,
        "reason_code": expected_code,
        "retryable": True,
    }


def test_sanitized_executor_database_code_is_preserved(monkeypatch) -> None:
    events: list[str] = []
    factory = _SessionFactory(events)
    captured: dict[str, object] = {}

    def append(session, **kwargs):
        del session
        captured.update(kwargs)

    monkeypatch.setattr("omnibase.controlled_data.execution_service.append_audit_event", append)

    def executor(session, command, *, success_audit_hook):
        del command, success_audit_hook
        with session.begin():
            raise ControlledCrudDatabaseFailure("CONTROLLED_CRUD_LOCK_TIMEOUT")

    with pytest.raises(ControlledCrudServiceError) as caught:
        execute_controlled_crud_audited(
            factory,
            _command(),
            audit=ControlledCrudAuditContext(request_id="req.audit-safe-db", risk_level="R1"),
            executor=_AtomicExecutor(executor),
        )

    assert caught.value.code == "CONTROLLED_CRUD_LOCK_TIMEOUT"
    assert caught.value.status_code == 503
    assert caught.value.retryable is True
    assert captured["details"] == {
        "error_code": "CONTROLLED_CRUD_LOCK_TIMEOUT",
        "reason_code": "CONTROLLED_CRUD_LOCK_TIMEOUT",
        "retryable": True,
    }


def test_missing_success_hook_is_audited_as_contract_failure(monkeypatch) -> None:
    events: list[str] = []
    factory = _SessionFactory(events)
    captured: dict[str, object] = {}

    def append(session, **kwargs):
        captured.update(kwargs)

    monkeypatch.setattr("omnibase.controlled_data.execution_service.append_audit_event", append)

    def executor(session, command, *, success_audit_hook):
        del command, success_audit_hook
        with session.begin():
            events.append("mutation.returned_without_hook")
        return ControlledCrudResult(
            operation_id=OPERATION_ID,
            resource_id=RESOURCE_ID,
            resource_version=4,
            action="data.rows.update",
            affected_rows=1,
            request_hash=canonical_request_hash(_request()),
            replayed=False,
        )

    with pytest.raises(ControlledCrudServiceError) as caught:
        execute_controlled_crud_audited(
            factory,
            _command(),
            audit=ControlledCrudAuditContext(request_id="req.audit-hook", risk_level="R1"),
            executor=_AtomicExecutor(executor),
        )

    assert caught.value.code == "CONTROLLED_CRUD_ATOMIC_AUDIT_HOOK_MISSING"
    assert captured["details"] == {
        "error_code": "CONTROLLED_CRUD_ATOMIC_AUDIT_HOOK_MISSING",
        "reason_code": "CONTROLLED_CRUD_ATOMIC_AUDIT_HOOK_MISSING",
        "retryable": False,
    }


def test_failure_audit_persistence_error_is_sanitized(monkeypatch) -> None:
    events: list[str] = []
    factory = _SessionFactory(events)

    def append(session, **kwargs):
        del session, kwargs
        raise RuntimeError("database_url=secret schema=tenant_secret")

    monkeypatch.setattr("omnibase.controlled_data.execution_service.append_audit_event", append)

    def executor(session, command, *, success_audit_hook):
        del command, success_audit_hook
        with session.begin():
            raise ControlledCrudConflict("ctid=(0,1)")

    with pytest.raises(ControlledCrudAuditPersistenceError) as caught:
        execute_controlled_crud_audited(
            factory,
            _command(),
            audit=ControlledCrudAuditContext(request_id="req.audit-fail", risk_level="R1"),
            executor=_AtomicExecutor(executor),
        )

    assert str(caught.value) == "CONTROLLED_CRUD_AUDIT_PERSISTENCE_FAILED"
    assert caught.value.__cause__ is None
    assert "secret" not in str(caught.value).lower()


def test_invalid_request_id_fails_before_session_or_executor() -> None:
    events: list[str] = []
    factory = _SessionFactory(events)
    called = False

    def executor(session, command, *, success_audit_hook):
        nonlocal called
        del session, command, success_audit_hook
        called = True
        raise AssertionError("must not execute")

    with pytest.raises(ValueError, match="request_id"):
        ControlledCrudAuditContext(request_id="bad request id", risk_level="R1")

    assert called is False
    assert factory.created == []


def test_legacy_executor_without_atomic_capability_is_rejected_before_session() -> None:
    events: list[str] = []
    factory = _SessionFactory(events)

    def legacy_executor(session, command):
        del session, command
        raise AssertionError("legacy executor must not run")

    with pytest.raises(ControlledCrudAtomicAuditContractError, match="HOOK_MISSING"):
        execute_controlled_crud_audited(
            factory,
            _command(),
            audit=ControlledCrudAuditContext(request_id="req.audit-legacy", risk_level="R1"),
            executor=cast("object", legacy_executor),  # type: ignore[arg-type]
        )

    assert factory.created == []


def test_builtin_atomic_adapter_forwards_the_required_precommit_hook(monkeypatch) -> None:
    session = MagicMock(spec=Session)
    command = _command()
    hook = MagicMock()
    expected = MagicMock(spec=ControlledCrudResult)

    def execute(received_session, received_command, *, success_audit_hook):
        assert received_session is session
        assert received_command is command
        assert success_audit_hook is hook
        return expected

    monkeypatch.setattr(
        "omnibase.controlled_data.execution_service.execute_controlled_crud", execute
    )

    result = builtin_atomic_controlled_crud_executor(
        session,
        command,
        success_audit_hook=hook,
    )

    assert result is expected


def test_success_hook_rejects_cross_wired_operation_before_audit(monkeypatch) -> None:
    events: list[str] = []
    factory = _SessionFactory(events)
    append_called = False

    def append(session, **kwargs):
        nonlocal append_called
        del session, kwargs
        append_called = True

    monkeypatch.setattr("omnibase.controlled_data.execution_service.append_audit_event", append)

    def executor(session, command, *, success_audit_hook):
        with session.begin():
            result = ControlledCrudResult(
                operation_id=OPERATION_ID,
                resource_id=RESOURCE_ID,
                resource_version=4,
                action="data.rows.update",
                affected_rows=1,
                request_hash=canonical_request_hash(command.request),
                replayed=False,
            )
            operation, idempotency = _completed_records(result)
            operation.id = "90000000-0000-0000-0000-000000000001"
            success_audit_hook(session, result, operation, idempotency)
        return result

    with pytest.raises(ControlledCrudServiceError) as caught:
        execute_controlled_crud_audited(
            factory,
            _command(),
            audit=ControlledCrudAuditContext(request_id="req.audit-cross", risk_level="R1"),
            executor=_AtomicExecutor(executor),
        )

    assert caught.value.code == "CONTROLLED_CRUD_ATOMIC_AUDIT_HOOK_MISSING"
    assert append_called is True  # Only the independent code-only failure audit.
    assert events[1] == "session1.rollback"


def test_lifecycle_bootstrap_mutation_and_success_audit_share_one_transaction(
    monkeypatch,
) -> None:
    events: list[str] = []
    factory = _SessionFactory(events)
    command = _command()
    request_hash = canonical_request_hash(command.request)

    def append(session, **kwargs):
        del session, kwargs
        events.append("success_audit.append")

    monkeypatch.setattr(
        "omnibase.controlled_data.execution_service._append_success_audit_event", append
    )

    def bootstrap(session):
        assert session is factory.created[0]
        events.extend(["bootstrap.authorization", "bootstrap.operation"])
        return command

    def executor(session, received, *, success_audit_hook):
        assert session is factory.created[0]
        assert received is command
        events.extend(["mutation.apply", "idempotency.complete", "operation.complete"])
        result = ControlledCrudResult(
            operation_id=OPERATION_ID,
            resource_id=RESOURCE_ID,
            resource_version=4,
            action="data.rows.update",
            affected_rows=1,
            request_hash=request_hash,
            replayed=False,
        )
        operation, idempotency = _completed_records(result)
        success_audit_hook(session, result, operation, idempotency)
        return result

    result = execute_controlled_crud_lifecycle_audited(
        factory,
        bootstrap,
        audit=ControlledCrudAuditContext(request_id="req.lifecycle-ok", risk_level="R1"),
        executor=_AtomicLifecycleExecutor(executor),
    )

    assert result.affected_rows == 1
    assert events == [
        "session1.begin",
        "bootstrap.authorization",
        "bootstrap.operation",
        "mutation.apply",
        "idempotency.complete",
        "operation.complete",
        "success_audit.append",
        "session1.commit",
        "session1.close",
    ]


def test_lifecycle_executor_failure_rolls_back_bootstrap_before_failure_audit(
    monkeypatch,
) -> None:
    events: list[str] = []
    factory = _SessionFactory(events)
    command = _command()

    def append(session, **kwargs):
        del session, kwargs
        events.append("failure_audit.append")

    monkeypatch.setattr("omnibase.controlled_data.execution_service.append_audit_event", append)

    def bootstrap(session):
        del session
        events.extend(["bootstrap.authorization", "bootstrap.operation"])
        return command

    def executor(session, received, *, success_audit_hook):
        del session, received, success_audit_hook
        events.append("mutation.failed")
        raise ControlledCrudConflict("sensitive physical detail")

    with pytest.raises(ControlledCrudServiceError) as caught:
        execute_controlled_crud_lifecycle_audited(
            factory,
            bootstrap,
            audit=ControlledCrudAuditContext(request_id="req.lifecycle-fail", risk_level="R1"),
            executor=_AtomicLifecycleExecutor(executor),
        )

    assert caught.value.code == "CONTROLLED_CRUD_STATE_CONFLICT"
    assert events == [
        "session1.begin",
        "bootstrap.authorization",
        "bootstrap.operation",
        "mutation.failed",
        "session1.rollback",
        "session1.close",
        "session2.begin",
        "failure_audit.append",
        "session2.commit",
        "session2.close",
    ]


def test_lifecycle_preflight_failure_rolls_back_without_executor_failure_audit() -> None:
    events: list[str] = []
    factory = _SessionFactory(events)

    class PreflightDenied(RuntimeError):
        pass

    def bootstrap(session):
        del session
        events.append("preflight.denied")
        raise PreflightDenied

    def executor(session, command, *, success_audit_hook):
        del session, command, success_audit_hook
        raise AssertionError("executor must not run")

    with pytest.raises(PreflightDenied):
        execute_controlled_crud_lifecycle_audited(
            factory,
            bootstrap,
            audit=ControlledCrudAuditContext(request_id="req.lifecycle-preflight", risk_level="R1"),
            executor=_AtomicLifecycleExecutor(executor),
        )

    assert events == [
        "session1.begin",
        "preflight.denied",
        "session1.rollback",
        "session1.close",
    ]


def test_lifecycle_failure_leaves_no_bootstrap_records_committed(monkeypatch) -> None:
    events: list[str] = []

    class StatefulTransaction(_Transaction):
        def __init__(self, session) -> None:
            super().__init__(events, session.name)
            self.session = session

        def __exit__(self, exc_type, exc, traceback) -> bool:
            if exc_type is None:
                self.session.committed.extend(self.session.pending)
            self.session.pending.clear()
            return super().__exit__(exc_type, exc, traceback)

    class StatefulSession(_Session):
        def __init__(self, name: str) -> None:
            super().__init__(events, name)
            self.pending: list[str] = []
            self.committed: list[str] = []

        def begin(self):
            return StatefulTransaction(self)

    mutation_session = StatefulSession("mutation")
    audit_session = StatefulSession("audit")
    sessions = iter([mutation_session, audit_session])

    def factory():
        return cast("Session", next(sessions))

    monkeypatch.setattr(
        "omnibase.controlled_data.execution_service.append_audit_event",
        lambda *_args, **_kwargs: audit_session.pending.append("failure_audit"),
    )

    def bootstrap(session):
        session.pending.extend(["AuthorizationContext", "OperationRecord"])
        return _command()

    def executor(session, command, *, success_audit_hook):
        del command, success_audit_hook
        session.pending.extend(["tenant_mutation", "IdempotencyRecord"])
        raise ControlledCrudConflict("rollback everything")

    with pytest.raises(ControlledCrudServiceError):
        execute_controlled_crud_lifecycle_audited(
            factory,
            bootstrap,
            audit=ControlledCrudAuditContext(request_id="req.lifecycle-no-orphan", risk_level="R1"),
            executor=_AtomicLifecycleExecutor(executor),
        )

    assert mutation_session.pending == []
    assert mutation_session.committed == []
    assert audit_session.committed == ["failure_audit"]
