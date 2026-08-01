"""Audit-safe orchestration contract for controlled CRUD execution.

The current low-level executor owns its transaction.  A success audit can only
be atomic with the tenant mutation, OperationRecord, and IdempotencyRecord when
the executor invokes ``success_audit_hook`` before that transaction commits.
This module therefore requires an executor implementing that explicit hook; it
does not silently wrap the legacy two-argument executor and audit after commit.

Failure audits are deliberately different: the mutation transaction must have
already rolled back before a fresh session and transaction append a code-only
audit event.  Raw exception text, SQL, parameters, physical identifiers, schema
names, and PostgreSQL row tokens never enter the audit contract or raised error.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    SQLAlchemyError,
)
from sqlalchemy.exc import (
    TimeoutError as SQLAlchemyTimeoutError,
)
from sqlalchemy.orm import Session

from omnibase.control_plane.models import AuditEvent, IdempotencyRecord, OperationRecord
from omnibase.control_plane.service import append_audit_event
from omnibase.controlled_data.crud import (
    MutationBudgetExceeded,
    MutationContractError,
    canonical_request_hash,
)
from omnibase.controlled_data.executor import (
    ControlledCrudAuthorizationDenied,
    ControlledCrudCommand,
    ControlledCrudConflict,
    ControlledCrudDatabaseFailure,
    ControlledCrudExecutionError,
    ControlledCrudIdempotencyConflict,
    ControlledCrudResult,
    ControlledCrudSuccessAuditError,
    execute_controlled_crud,
    execute_controlled_crud_in_transaction,
)

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_ACTION_BY_KIND = {
    "insert": "data.rows.insert",
    "update": "data.rows.update",
    "delete": "data.rows.delete",
}

AuditDecision = Literal["allowed", "denied", "error"]
SuccessAuditHook = Callable[
    [Session, ControlledCrudResult, OperationRecord, IdempotencyRecord], None
]


class SessionFactory(Protocol):
    def __call__(self) -> Session: ...


class AtomicControlledCrudExecutor(Protocol):
    """Minimum hook the low-level executor must provide before service wiring."""

    supports_atomic_success_audit: Literal[True]

    def __call__(
        self,
        session: Session,
        command: ControlledCrudCommand,
        *,
        success_audit_hook: SuccessAuditHook,
    ) -> ControlledCrudResult: ...


CommandBootstrap = Callable[[Session], ControlledCrudCommand]


class AtomicControlledCrudLifecycleExecutor(Protocol):
    """Executor that participates in a caller-owned bootstrap transaction."""

    supports_atomic_lifecycle: Literal[True]

    def __call__(
        self,
        session: Session,
        command: ControlledCrudCommand,
        *,
        success_audit_hook: SuccessAuditHook,
    ) -> ControlledCrudResult: ...


class BuiltinAtomicControlledCrudExecutor:
    """Trusted adapter proving that the built-in executor runs the hook pre-commit."""

    supports_atomic_success_audit: Literal[True] = True

    def __call__(
        self,
        session: Session,
        command: ControlledCrudCommand,
        *,
        success_audit_hook: SuccessAuditHook,
    ) -> ControlledCrudResult:
        return execute_controlled_crud(
            session,
            command,
            success_audit_hook=success_audit_hook,
        )


builtin_atomic_controlled_crud_executor = BuiltinAtomicControlledCrudExecutor()


class BuiltinAtomicControlledCrudLifecycleExecutor:
    """Trusted adapter for the caller-owned in-transaction executor entry."""

    supports_atomic_lifecycle: Literal[True] = True

    def __call__(
        self,
        session: Session,
        command: ControlledCrudCommand,
        *,
        success_audit_hook: SuccessAuditHook,
    ) -> ControlledCrudResult:
        return execute_controlled_crud_in_transaction(
            session,
            command,
            success_audit_hook=success_audit_hook,
        )


builtin_atomic_controlled_crud_lifecycle_executor = BuiltinAtomicControlledCrudLifecycleExecutor()


@dataclass(frozen=True, slots=True)
class ControlledCrudAuditContext:
    """Server-owned request metadata; no free-form details are accepted."""

    request_id: str
    risk_level: Literal["R0", "R1"]

    def __post_init__(self) -> None:
        if not _REQUEST_ID.fullmatch(self.request_id):
            raise ValueError("request_id must contain 1 to 64 safe identifier characters")


@dataclass(frozen=True, slots=True)
class ControlledCrudFailure:
    code: str
    decision: Literal["denied", "error"]
    status_code: int
    retryable: bool


class ControlledCrudServiceError(RuntimeError):
    """Sanitized execution failure safe to cross an internal service boundary."""

    def __init__(self, failure: ControlledCrudFailure, *, request_id: str) -> None:
        super().__init__(failure.code)
        self.code = failure.code
        self.decision = failure.decision
        self.status_code = failure.status_code
        self.retryable = failure.retryable
        self.request_id = request_id


class ControlledCrudAuditPersistenceError(RuntimeError):
    """The independent failure audit could not be made durable."""

    def __init__(self, *, request_id: str) -> None:
        super().__init__("CONTROLLED_CRUD_AUDIT_PERSISTENCE_FAILED")
        self.code = "CONTROLLED_CRUD_AUDIT_PERSISTENCE_FAILED"
        self.request_id = request_id


class ControlledCrudAtomicAuditContractError(RuntimeError):
    """The injected executor returned without appending its success audit."""

    def __init__(self, *, request_id: str) -> None:
        super().__init__("CONTROLLED_CRUD_ATOMIC_AUDIT_HOOK_MISSING")
        self.code = "CONTROLLED_CRUD_ATOMIC_AUDIT_HOOK_MISSING"
        self.request_id = request_id


def execute_controlled_crud_audited(
    session_factory: SessionFactory,
    command: ControlledCrudCommand,
    *,
    audit: ControlledCrudAuditContext,
    executor: AtomicControlledCrudExecutor,
) -> ControlledCrudResult:
    """Execute through an atomic-success hook and independently audit failures.

    The injected executor must own the mutation transaction and call
    ``success_audit_hook`` after it has finalized Operation and Idempotency state
    but before committing.  The existing low-level executor does not yet expose
    this hook and must not be passed through an un-audited adapter.
    """

    action = _requested_action(command)
    request_hash = canonical_request_hash(command.request)
    started_ns = time.monotonic_ns()
    if getattr(executor, "supports_atomic_success_audit", False) is not True:
        raise ControlledCrudAtomicAuditContractError(request_id=audit.request_id)
    mutation_session = session_factory()
    success_audit_written = False

    def success_audit_hook(
        hook_session: Session,
        result: ControlledCrudResult,
        operation: OperationRecord,
        idempotency: IdempotencyRecord,
    ) -> None:
        nonlocal success_audit_written
        if success_audit_written:
            raise ControlledCrudAtomicAuditContractError(request_id=audit.request_id)
        if hook_session is not mutation_session:
            raise ControlledCrudAtomicAuditContractError(request_id=audit.request_id)
        _validate_success_bindings(
            command=command,
            audit=audit,
            result=result,
            operation=operation,
            idempotency=idempotency,
            action=action,
            request_hash=request_hash,
        )
        _append_success_audit_event(
            hook_session,
            tenant_id=str(command.tenant_id),
            request_id=audit.request_id,
            actor_type="user",
            actor_id=str(command.actor_user_id),
            workspace_id=(None if command.workspace_id is None else str(command.workspace_id)),
            resource_id=str(command.request.resource_id),
            operation_id=str(command.operation_id),
            action=action,
            decision="allowed",
            risk_level=audit.risk_level,
            input_hash=request_hash,
            before_version=command.request.resource_version,
            after_version=command.request.resource_version,
            status_code=200,
            row_count=result.affected_rows,
            duration_ms=_duration_ms(started_ns),
            details={
                "reason_code": (
                    "CONTROLLED_CRUD_REPLAYED" if result.replayed else "CONTROLLED_CRUD_SUCCEEDED"
                ),
                "retryable": False,
            },
        )
        success_audit_written = True

    try:
        result = executor(
            mutation_session,
            command,
            success_audit_hook=success_audit_hook,
        )
        if not success_audit_written:
            raise ControlledCrudAtomicAuditContractError(request_id=audit.request_id)
        return result
    except Exception as exc:
        failure = _classify_failure(exc)
    finally:
        mutation_session.close()

    _append_failure_audit(
        session_factory,
        command=command,
        audit=audit,
        action=action,
        request_hash=request_hash,
        failure=failure,
        duration_ms=_duration_ms(started_ns),
    )
    raise ControlledCrudServiceError(failure, request_id=audit.request_id) from None


def execute_controlled_crud_lifecycle_audited(
    session_factory: SessionFactory,
    bootstrap: CommandBootstrap,
    *,
    audit: ControlledCrudAuditContext,
    executor: AtomicControlledCrudLifecycleExecutor,
) -> ControlledCrudResult:
    """Atomically bootstrap and execute one User-RBAC controlled mutation.

    AuthorizationContext, queued Operation, tenant mutation, IdempotencyRecord,
    completed Operation state, and success Audit all share one transaction.
    Any bootstrap, lock, timeout, executor, hook, flush, or commit failure rolls
    the complete lifecycle back before a separate code-only failure Audit.
    """
    if getattr(executor, "supports_atomic_lifecycle", False) is not True:
        raise ControlledCrudAtomicAuditContractError(request_id=audit.request_id)

    started_ns = time.monotonic_ns()
    mutation_session = session_factory()
    command: ControlledCrudCommand | None = None
    caught: Exception | None = None
    failure: ControlledCrudFailure | None = None
    result: ControlledCrudResult | None = None
    success_audit_written = False

    try:
        with mutation_session.begin():
            command = bootstrap(mutation_session)
            action = _requested_action(command)
            request_hash = canonical_request_hash(command.request)

            def success_audit_hook(
                hook_session: Session,
                hook_result: ControlledCrudResult,
                operation: OperationRecord,
                idempotency: IdempotencyRecord,
            ) -> None:
                nonlocal success_audit_written
                if success_audit_written or hook_session is not mutation_session:
                    raise ControlledCrudAtomicAuditContractError(request_id=audit.request_id)
                _validate_success_bindings(
                    command=command,
                    audit=audit,
                    result=hook_result,
                    operation=operation,
                    idempotency=idempotency,
                    action=action,
                    request_hash=request_hash,
                )
                _append_success_audit_event(
                    hook_session,
                    tenant_id=str(command.tenant_id),
                    request_id=audit.request_id,
                    actor_type="user",
                    actor_id=str(command.actor_user_id),
                    workspace_id=(
                        None if command.workspace_id is None else str(command.workspace_id)
                    ),
                    resource_id=str(command.request.resource_id),
                    operation_id=str(command.operation_id),
                    action=action,
                    decision="allowed",
                    risk_level=audit.risk_level,
                    input_hash=request_hash,
                    before_version=command.request.resource_version,
                    after_version=command.request.resource_version,
                    status_code=200,
                    row_count=hook_result.affected_rows,
                    duration_ms=_duration_ms(started_ns),
                    details={
                        "reason_code": (
                            "CONTROLLED_CRUD_REPLAYED"
                            if hook_result.replayed
                            else "CONTROLLED_CRUD_SUCCEEDED"
                        ),
                        "retryable": False,
                    },
                )
                success_audit_written = True

            result = executor(
                mutation_session,
                command,
                success_audit_hook=success_audit_hook,
            )
            if not success_audit_written:
                raise ControlledCrudAtomicAuditContractError(request_id=audit.request_id)
    except Exception as exc:
        caught = exc
        if command is not None:
            failure = _classify_failure(exc)
    finally:
        mutation_session.close()

    if caught is None and result is not None:
        return result
    if caught is None:
        raise ControlledCrudAtomicAuditContractError(request_id=audit.request_id)
    if command is None or failure is None:
        raise caught

    _append_failure_audit(
        session_factory,
        command=command,
        audit=audit,
        action=_requested_action(command),
        request_hash=canonical_request_hash(command.request),
        failure=failure,
        duration_ms=_duration_ms(started_ns),
    )
    raise ControlledCrudServiceError(failure, request_id=audit.request_id) from None


def _validate_success_bindings(
    *,
    command: ControlledCrudCommand,
    audit: ControlledCrudAuditContext,
    result: ControlledCrudResult,
    operation: OperationRecord,
    idempotency: IdempotencyRecord,
    action: str,
    request_hash: str,
) -> None:
    metadata = result.as_safe_metadata()
    if not all(
        (
            result.operation_id == command.operation_id,
            result.resource_id == command.request.resource_id,
            result.resource_version == command.request.resource_version,
            result.action == action,
            result.request_hash == request_hash,
            operation.id == str(command.operation_id),
            operation.tenant_id == str(command.tenant_id),
            operation.actor_type == "user",
            operation.actor_id == str(command.actor_user_id),
            operation.resource_id == str(command.request.resource_id),
            operation.resource_version == command.request.resource_version,
            operation.request_hash == request_hash,
            operation.kind == action,
            operation.risk_level == audit.risk_level,
            operation.state == "succeeded",
            operation.result_ref == metadata,
            idempotency.operation_id == str(command.operation_id),
            idempotency.request_hash == request_hash,
            idempotency.state == "completed",
            idempotency.response_ref == metadata,
        )
    ):
        raise ControlledCrudAtomicAuditContractError(request_id=audit.request_id)


def _append_failure_audit(
    session_factory: SessionFactory,
    *,
    command: ControlledCrudCommand,
    audit: ControlledCrudAuditContext,
    action: str,
    request_hash: str,
    failure: ControlledCrudFailure,
    duration_ms: int,
) -> None:
    audit_session = session_factory()
    try:
        with audit_session.begin():
            append_audit_event(
                audit_session,
                tenant_id=str(command.tenant_id),
                request_id=audit.request_id,
                actor_type="user",
                actor_id=str(command.actor_user_id),
                workspace_id=(None if command.workspace_id is None else str(command.workspace_id)),
                resource_id=str(command.request.resource_id),
                operation_id=str(command.operation_id),
                action=action,
                decision=failure.decision,
                risk_level=audit.risk_level,
                input_hash=request_hash,
                before_version=command.request.resource_version,
                status_code=failure.status_code,
                duration_ms=duration_ms,
                details={
                    "error_code": failure.code,
                    "reason_code": failure.code,
                    "retryable": failure.retryable,
                },
            )
    except Exception:
        raise ControlledCrudAuditPersistenceError(request_id=audit.request_id) from None
    finally:
        audit_session.close()


def _append_success_audit_event(session: Session, **values: object) -> None:
    """Append an event after the executor has locked and verified every reference."""
    event = AuditEvent(**values)
    session.add(event)
    session.flush()


def _requested_action(command: ControlledCrudCommand) -> str:
    try:
        return _ACTION_BY_KIND[command.request.kind]
    except KeyError as exc:
        raise ValueError("controlled CRUD action is outside the closed service contract") from exc


def _classify_failure(  # noqa: C901 - explicit closed error taxonomy
    exc: Exception,
) -> ControlledCrudFailure:
    if isinstance(exc, ControlledCrudAuthorizationDenied):
        return ControlledCrudFailure("CONTROLLED_CRUD_AUTHORIZATION_DENIED", "denied", 403, False)
    if isinstance(exc, ControlledCrudIdempotencyConflict):
        return ControlledCrudFailure("CONTROLLED_CRUD_IDEMPOTENCY_CONFLICT", "error", 409, False)
    if isinstance(exc, MutationBudgetExceeded):
        return ControlledCrudFailure("CONTROLLED_CRUD_BUDGET_EXCEEDED", "denied", 422, False)
    if isinstance(exc, MutationContractError):
        return ControlledCrudFailure("CONTROLLED_CRUD_CONTRACT_REJECTED", "denied", 422, False)
    if isinstance(exc, ControlledCrudConflict):
        return ControlledCrudFailure("CONTROLLED_CRUD_STATE_CONFLICT", "error", 409, False)
    if isinstance(exc, ControlledCrudAtomicAuditContractError):
        return ControlledCrudFailure(
            "CONTROLLED_CRUD_ATOMIC_AUDIT_HOOK_MISSING", "error", 500, False
        )
    if isinstance(exc, ControlledCrudSuccessAuditError):
        return ControlledCrudFailure(
            "CONTROLLED_CRUD_ATOMIC_AUDIT_HOOK_MISSING", "error", 500, False
        )
    if isinstance(exc, ControlledCrudDatabaseFailure):
        status_retryable = {
            "CONTROLLED_CRUD_CONNECTION_TIMEOUT": (503, True),
            "CONTROLLED_CRUD_LOCK_TIMEOUT": (503, True),
            "CONTROLLED_CRUD_STATEMENT_TIMEOUT": (504, True),
            "CONTROLLED_CRUD_SERIALIZATION_CONFLICT": (409, True),
            "CONTROLLED_CRUD_DEADLOCK": (503, True),
            "CONTROLLED_CRUD_CONSTRAINT_CONFLICT": (409, False),
            "CONTROLLED_CRUD_DATABASE_ERROR": (500, False),
        }
        status_code, retryable = status_retryable[exc.code]
        return ControlledCrudFailure(exc.code, "error", status_code, retryable)
    if isinstance(exc, SQLAlchemyTimeoutError):
        return ControlledCrudFailure("CONTROLLED_CRUD_CONNECTION_TIMEOUT", "error", 503, True)
    if isinstance(exc, DBAPIError):
        sqlstate = _sqlstate(exc)
        if sqlstate == "55P03":
            return ControlledCrudFailure("CONTROLLED_CRUD_LOCK_TIMEOUT", "error", 503, True)
        if sqlstate == "57014":
            return ControlledCrudFailure("CONTROLLED_CRUD_STATEMENT_TIMEOUT", "error", 504, True)
        if sqlstate == "40001":
            return ControlledCrudFailure(
                "CONTROLLED_CRUD_SERIALIZATION_CONFLICT", "error", 409, True
            )
        if sqlstate == "40P01":
            return ControlledCrudFailure("CONTROLLED_CRUD_DEADLOCK", "error", 503, True)
        if isinstance(exc, IntegrityError):
            return ControlledCrudFailure("CONTROLLED_CRUD_CONSTRAINT_CONFLICT", "error", 409, False)
        return ControlledCrudFailure("CONTROLLED_CRUD_DATABASE_ERROR", "error", 500, False)
    if isinstance(exc, ControlledCrudExecutionError):
        return ControlledCrudFailure("CONTROLLED_CRUD_EXECUTION_REJECTED", "error", 500, False)
    if isinstance(exc, SQLAlchemyError):
        return ControlledCrudFailure("CONTROLLED_CRUD_DATABASE_ERROR", "error", 500, False)
    return ControlledCrudFailure("CONTROLLED_CRUD_INTERNAL_ERROR", "error", 500, False)


def _sqlstate(exc: DBAPIError) -> str | None:
    original = exc.orig
    value = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return value if isinstance(value, str) else None


def _duration_ms(started_ns: int) -> int:
    return max(0, (time.monotonic_ns() - started_ns) // 1_000_000)


__all__ = [
    "AtomicControlledCrudExecutor",
    "AtomicControlledCrudLifecycleExecutor",
    "BuiltinAtomicControlledCrudExecutor",
    "BuiltinAtomicControlledCrudLifecycleExecutor",
    "CommandBootstrap",
    "ControlledCrudAtomicAuditContractError",
    "ControlledCrudAuditContext",
    "ControlledCrudAuditPersistenceError",
    "ControlledCrudFailure",
    "ControlledCrudServiceError",
    "SessionFactory",
    "SuccessAuditHook",
    "builtin_atomic_controlled_crud_executor",
    "builtin_atomic_controlled_crud_lifecycle_executor",
    "execute_controlled_crud_audited",
    "execute_controlled_crud_lifecycle_audited",
]
