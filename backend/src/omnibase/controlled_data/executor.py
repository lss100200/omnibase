"""Minimal transaction-owned executor for P34.3 controlled CRUD.

The executor accepts only a server-owned locator and a trusted live user-RBAC
decision.  It owns one SQLAlchemy transaction, locks all authorization and
operation records before touching tenant data, applies transaction-local
timeouts, and never returns physical identifiers or PostgreSQL row tokens.

The global deadlock-avoidance order is frozen as: Tenant -> tenant User ->
Resource -> TableBinding -> ColumnBindings(sorted) -> AuthorizationContext ->
Operation -> Idempotency.  No caller may reorder or pre-lock these records.

No router imports this module.  It does not perform DDL and it never discovers
credentials, schemas, or locators from request data.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol, cast
from uuid import UUID

from sqlalchemy import Boolean, column, func, select, table
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    SQLAlchemyError,
)
from sqlalchemy.exc import (
    TimeoutError as SQLAlchemyTimeoutError,
)
from sqlalchemy.orm import Session

from omnibase.control_plane.models import (
    IdempotencyRecord,
    OperationRecord,
    ResourceRecord,
)
from omnibase.controlled_data.crud import (
    MutationBudgetExceeded,
    MutationContractError,
    PreparedFilteredMutation,
    PreparedInsert,
    TrustedMutationLocator,
    prepare_delete,
    prepare_insert,
    prepare_update,
)
from omnibase.controlled_data.crud_contracts import (
    DeleteMutationRequest,
    InsertMutationRequest,
    UpdateMutationRequest,
)
from omnibase.controlled_data.models import (
    AuthorizationContext,
    DataColumnBinding,
    DataTableBinding,
)
from omnibase.db.models import Tenant

MutationAction = Literal["data.rows.insert", "data.rows.update", "data.rows.delete"]
MutationRequestType = InsertMutationRequest | UpdateMutationRequest | DeleteMutationRequest

_ACTION_BY_KIND: dict[str, MutationAction] = {
    "insert": "data.rows.insert",
    "update": "data.rows.update",
    "delete": "data.rows.delete",
}
_ALLOWED_POLICIES = frozenset({"workspace_private", "tenant_managed", "controlled_shared"})
_ALLOWED_LIVE_ROLES = frozenset(
    {"workspace_member", "workspace_admin", "tenant_admin", "platform_admin"}
)
_IDEMPOTENCY_TTL = timedelta(hours=24)
_MAX_LOCK_TIMEOUT_MS = 2_000

CONTROLLED_CRUD_LOCK_ORDER = (
    "omnibase_meta.tenants",
    "tenant.users",
    "omnibase_meta.resource_registry",
    "omnibase_meta.data_table_bindings",
    "omnibase_meta.data_column_bindings",
    "omnibase_meta.authorization_contexts",
    "omnibase_meta.operations",
    "omnibase_meta.idempotency_records",
)


class _RowcountResult(Protocol):
    rowcount: int


class ControlledCrudExecutionError(RuntimeError):
    """Base fail-closed executor error."""


class ControlledCrudAuthorizationDenied(ControlledCrudExecutionError):
    """The locked records and trusted live decision do not authorize the request."""


class ControlledCrudConflict(ControlledCrudExecutionError):
    """A resource, operation, or row set changed during execution."""


class ControlledCrudIdempotencyConflict(ControlledCrudExecutionError):
    """An idempotency key was reused for different or incomplete work."""


class ControlledCrudSuccessAuditError(ControlledCrudExecutionError):
    """The required pre-commit success audit hook rejected or failed."""


DatabaseFailureCode = Literal[
    "CONTROLLED_CRUD_CONNECTION_TIMEOUT",
    "CONTROLLED_CRUD_LOCK_TIMEOUT",
    "CONTROLLED_CRUD_STATEMENT_TIMEOUT",
    "CONTROLLED_CRUD_SERIALIZATION_CONFLICT",
    "CONTROLLED_CRUD_DEADLOCK",
    "CONTROLLED_CRUD_CONSTRAINT_CONFLICT",
    "CONTROLLED_CRUD_DATABASE_ERROR",
]


class ControlledCrudDatabaseFailure(ControlledCrudExecutionError):
    """Sanitized database failure retaining only a stable classification code."""

    def __init__(self, code: DatabaseFailureCode) -> None:
        super().__init__("controlled CRUD database operation failed")
        self.code = code


@dataclass(frozen=True, slots=True)
class TrustedUserRbacDecision:
    """Short-lived internal result of a live user/RBAC authorization check."""

    decision_id: UUID
    allowed: bool
    tenant_id: UUID
    workspace_id: UUID | None
    actor_user_id: UUID
    resource_id: UUID
    resource_version: int
    action: MutationAction
    authorization_context_id: UUID
    source_version: int
    snapshot_hash: str
    roles: frozenset[str]
    user_is_active: bool
    tenant_is_active: bool
    evaluated_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if (
            isinstance(self.resource_version, bool)
            or not isinstance(self.resource_version, int)
            or self.resource_version < 1
        ):
            raise ValueError("decision resource_version must be positive")
        if (
            isinstance(self.source_version, bool)
            or not isinstance(self.source_version, int)
            or self.source_version < 1
        ):
            raise ValueError("decision source_version must be positive")
        if (
            not isinstance(self.roles, frozenset)
            or not self.roles
            or not self.roles <= _ALLOWED_LIVE_ROLES
        ):
            raise ValueError("decision roles must be a non-empty closed subset")
        if not _aware(self.evaluated_at) or not _aware(self.expires_at):
            raise ValueError("decision timestamps must be timezone-aware")
        if self.expires_at <= self.evaluated_at:
            raise ValueError("decision expiry must follow evaluation")
        if self.expires_at - self.evaluated_at > timedelta(seconds=30):
            raise ValueError("decision TTL cannot exceed 30 seconds")
        if len(self.snapshot_hash) != 64 or any(
            character not in "0123456789abcdef" for character in self.snapshot_hash
        ):
            raise ValueError("decision snapshot_hash must be lowercase SHA-256 hex")


@dataclass(frozen=True, slots=True)
class ControlledCrudCommand:
    """Internal execution command; IDs are bound again against locked records."""

    tenant_id: UUID
    workspace_id: UUID | None
    actor_user_id: UUID
    authorization_context_id: UUID
    operation_id: UUID
    locator: TrustedMutationLocator
    request: MutationRequestType
    decision: TrustedUserRbacDecision
    expected_operation_version: int = 1
    lock_timeout_ms: int = 1_000

    def __post_init__(self) -> None:
        if (
            isinstance(self.expected_operation_version, bool)
            or not isinstance(self.expected_operation_version, int)
            or self.expected_operation_version < 1
        ):
            raise ValueError("expected_operation_version must be positive")
        if (
            isinstance(self.lock_timeout_ms, bool)
            or not isinstance(self.lock_timeout_ms, int)
            or not 1 <= self.lock_timeout_ms <= _MAX_LOCK_TIMEOUT_MS
        ):
            raise ValueError("lock_timeout_ms must be between 1 and 2000")
        if self.lock_timeout_ms > self.request.timeout_ms:
            raise ValueError("lock_timeout_ms cannot exceed statement timeout")


@dataclass(frozen=True, slots=True)
class ControlledCrudResult:
    """Logical-only mutation result safe for Operation and idempotency metadata."""

    operation_id: UUID
    resource_id: UUID
    resource_version: int
    action: MutationAction
    affected_rows: int
    request_hash: str
    replayed: bool

    def as_safe_metadata(self) -> dict[str, object]:
        return {
            "operation_id": str(self.operation_id),
            "resource_id": str(self.resource_id),
            "resource_version": self.resource_version,
            "action": self.action,
            "affected_rows": self.affected_rows,
            "request_hash": self.request_hash,
            "status": "succeeded",
        }


SuccessAuditHook = Callable[
    [Session, ControlledCrudResult, OperationRecord, IdempotencyRecord],
    None,
]


@dataclass(frozen=True, slots=True)
class _LockedRecords:
    tenant: Tenant
    user_id: str
    user_is_active: bool
    user_is_tenant_admin: bool
    binding: DataTableBinding
    resource: ResourceRecord
    authorization: AuthorizationContext
    operation: OperationRecord
    columns: tuple[DataColumnBinding, ...]


def execute_controlled_crud(
    session: Session,
    command: ControlledCrudCommand,
    *,
    now: datetime | None = None,
    success_audit_hook: SuccessAuditHook | None = None,
) -> ControlledCrudResult:
    """Execute one controlled mutation in one owned transaction and connection."""
    if session.in_transaction():
        raise ControlledCrudExecutionError("executor requires transaction ownership")
    if now is not None and not _aware(now):
        raise ValueError("now must be timezone-aware")
    action = _ACTION_BY_KIND[command.request.kind]
    prepared = _prepare(command.locator, command.request)

    try:
        return _execute_owned_transaction(
            session,
            command=command,
            prepared=prepared,
            action=action,
            success_audit_hook=success_audit_hook,
        )
    except (
        ControlledCrudExecutionError,
        MutationContractError,
        MutationBudgetExceeded,
    ):
        raise
    except SQLAlchemyError as exc:
        raise _classify_database_failure(exc) from None


def execute_controlled_crud_in_transaction(
    session: Session,
    command: ControlledCrudCommand,
    *,
    now: datetime | None = None,
    success_audit_hook: SuccessAuditHook | None = None,
) -> ControlledCrudResult:
    """Execute inside a caller-owned transaction without committing it.

    This internal lifecycle entry exists so server-owned bootstrap records can
    be atomic with mutation, idempotency, Operation completion, and success
    Audit.  The public low-level entry above retains its transaction-ownership
    contract for existing callers.
    """
    if not session.in_transaction():
        raise ControlledCrudExecutionError("in-transaction executor requires an active transaction")
    if now is not None and not _aware(now):
        raise ValueError("now must be timezone-aware")
    action = _ACTION_BY_KIND[command.request.kind]
    prepared = _prepare(command.locator, command.request)
    try:
        return _execute_in_transaction(
            session,
            command=command,
            prepared=prepared,
            action=action,
            success_audit_hook=success_audit_hook,
        )
    except (
        ControlledCrudExecutionError,
        MutationContractError,
        MutationBudgetExceeded,
    ):
        raise
    except SQLAlchemyError as exc:
        raise _classify_database_failure(exc) from None


def _execute_owned_transaction(
    session: Session,
    *,
    command: ControlledCrudCommand,
    prepared: PreparedInsert | PreparedFilteredMutation,
    action: MutationAction,
    success_audit_hook: SuccessAuditHook | None,
) -> ControlledCrudResult:
    """Run the database portion behind the sanitized public exception boundary."""

    with session.begin():
        return _execute_in_transaction(
            session,
            command=command,
            prepared=prepared,
            action=action,
            success_audit_hook=success_audit_hook,
        )


def _execute_in_transaction(
    session: Session,
    *,
    command: ControlledCrudCommand,
    prepared: PreparedInsert | PreparedFilteredMutation,
    action: MutationAction,
    success_audit_hook: SuccessAuditHook | None,
) -> ControlledCrudResult:
    """Database core shared by owned and caller-owned transaction entry points."""
    connection = session.connection()
    _set_local_timeouts(
        connection,
        statement_timeout_ms=command.request.timeout_ms,
        lock_timeout_ms=command.lock_timeout_ms,
    )
    locked = _lock_records(session, command)
    _validate_locator_columns(
        command.locator,
        locked.columns,
        tenant_id=str(command.tenant_id),
    )
    reservation_time = _database_now(session)
    idempotency, created = _reserve_and_lock_idempotency(
        session,
        command=command,
        action=action,
        request_hash=prepared.request_hash,
        now=reservation_time,
    )
    authorization_time = _database_now(session)
    _validate_locked_records(
        locked,
        command=command,
        action=action,
        request_hash=prepared.request_hash,
        now=authorization_time,
    )
    if idempotency.expires_at <= authorization_time:
        raise ControlledCrudIdempotencyConflict("idempotency reservation expired")
    if not created:
        result = _replay_result(
            idempotency,
            operation=locked.operation,
            command=command,
            action=action,
            request_hash=prepared.request_hash,
        )
        if success_audit_hook is not None:
            _run_success_audit_hook(
                success_audit_hook,
                session=session,
                result=result,
                operation=locked.operation,
                idempotency=idempotency,
            )
            session.flush()
        _assert_same_connection(session, connection)
        return result
    if locked.operation.state != "queued":
        raise ControlledCrudConflict("new idempotency reservation requires queued operation")

    _mark_operation_running(locked.operation, authorization_time)
    session.flush()
    affected_rows = _apply_prepared(
        session,
        command=command,
        locked=locked,
        prepared=prepared,
    )
    result = ControlledCrudResult(
        operation_id=command.operation_id,
        resource_id=command.request.resource_id,
        resource_version=command.request.resource_version,
        action=action,
        affected_rows=affected_rows,
        request_hash=prepared.request_hash,
        replayed=False,
    )
    completion_time = _database_now(session)
    _mark_completed(
        locked.operation,
        idempotency,
        result=result,
        now=completion_time,
    )
    _run_success_audit_hook(
        success_audit_hook,
        session=session,
        result=result,
        operation=locked.operation,
        idempotency=idempotency,
    )
    session.flush()
    _assert_same_connection(session, connection)
    return result


def _prepare(
    locator: TrustedMutationLocator,
    request: MutationRequestType,
) -> PreparedInsert | PreparedFilteredMutation:
    if isinstance(request, InsertMutationRequest):
        return prepare_insert(locator, request)
    if isinstance(request, UpdateMutationRequest):
        return prepare_update(locator, request)
    if isinstance(request, DeleteMutationRequest):
        return prepare_delete(locator, request)
    raise MutationContractError("mutation kind is outside the closed executor contract")


def _classify_database_failure(exc: SQLAlchemyError) -> ControlledCrudDatabaseFailure:
    if isinstance(exc, SQLAlchemyTimeoutError):
        return ControlledCrudDatabaseFailure("CONTROLLED_CRUD_CONNECTION_TIMEOUT")
    if isinstance(exc, DBAPIError):
        sqlstate = _database_sqlstate(exc)
        code_by_sqlstate: dict[str, DatabaseFailureCode] = {
            "55P03": "CONTROLLED_CRUD_LOCK_TIMEOUT",
            "57014": "CONTROLLED_CRUD_STATEMENT_TIMEOUT",
            "40001": "CONTROLLED_CRUD_SERIALIZATION_CONFLICT",
            "40P01": "CONTROLLED_CRUD_DEADLOCK",
        }
        if sqlstate in code_by_sqlstate:
            return ControlledCrudDatabaseFailure(code_by_sqlstate[sqlstate])
        if isinstance(exc, IntegrityError):
            return ControlledCrudDatabaseFailure("CONTROLLED_CRUD_CONSTRAINT_CONFLICT")
    return ControlledCrudDatabaseFailure("CONTROLLED_CRUD_DATABASE_ERROR")


def _database_sqlstate(exc: DBAPIError) -> str | None:
    value = getattr(exc.orig, "sqlstate", None) or getattr(exc.orig, "pgcode", None)
    return value if isinstance(value, str) else None


def _run_success_audit_hook(
    hook: SuccessAuditHook | None,
    *,
    session: Session,
    result: ControlledCrudResult,
    operation: OperationRecord,
    idempotency: IdempotencyRecord,
) -> None:
    if hook is None:
        return
    try:
        hook(session, result, operation, idempotency)
    except Exception:
        raise ControlledCrudSuccessAuditError("controlled CRUD success audit hook failed") from None


def _set_local_timeouts(
    connection: object,
    *,
    statement_timeout_ms: int,
    lock_timeout_ms: int,
) -> None:
    # Values are validated bounded integers, never request strings.  SET LOCAL
    # keeps both limits scoped to the transaction and the one captured connection.
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        f"SET LOCAL statement_timeout = '{statement_timeout_ms}ms'"
    )
    connection.exec_driver_sql(  # type: ignore[attr-defined]
        f"SET LOCAL lock_timeout = '{lock_timeout_ms}ms'"
    )


def _lock_records(session: Session, command: ControlledCrudCommand) -> _LockedRecords:
    tenant_id = str(command.tenant_id)
    resource_id = str(command.request.resource_id)
    tenant = session.execute(
        select(Tenant).where(Tenant.id == tenant_id).with_for_update()
    ).scalar_one_or_none()
    if (
        tenant is None
        or tenant.is_active is not True
        or tenant.schema_name != command.locator.tenant_schema
    ):
        raise ControlledCrudAuthorizationDenied("tenant route is inactive or mismatched")

    user_source = table(
        "users",
        column("id", PG_UUID(as_uuid=False)),
        column("is_active", Boolean()),
        column("is_tenant_admin", Boolean()),
        schema=tenant.schema_name,
    )
    user_row = (
        session.execute(
            select(
                user_source.c.id,
                user_source.c.is_active,
                user_source.c.is_tenant_admin,
            )
            .where(user_source.c.id == str(command.actor_user_id))
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if (
        user_row is None
        or str(user_row["id"]) != str(command.actor_user_id)
        or user_row["is_active"] is not True
    ):
        raise ControlledCrudAuthorizationDenied("current tenant user is missing or inactive")

    resource = session.execute(
        select(ResourceRecord)
        .where(
            ResourceRecord.id == resource_id,
            ResourceRecord.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    binding = session.execute(
        select(DataTableBinding)
        .where(
            DataTableBinding.id == str(command.locator.table_binding_id),
            DataTableBinding.tenant_id == tenant_id,
            DataTableBinding.resource_id == resource_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    columns = tuple(
        session.execute(
            select(DataColumnBinding)
            .where(
                DataColumnBinding.tenant_id == tenant_id,
                DataColumnBinding.table_binding_id == str(command.locator.table_binding_id),
            )
            .order_by(DataColumnBinding.ordinal, DataColumnBinding.id)
            .with_for_update()
        )
        .scalars()
        .all()
    )
    authorization = session.execute(
        select(AuthorizationContext)
        .where(
            AuthorizationContext.id == str(command.authorization_context_id),
            AuthorizationContext.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    operation = session.execute(
        select(OperationRecord)
        .where(
            OperationRecord.id == str(command.operation_id),
            OperationRecord.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if binding is None or resource is None or authorization is None or operation is None:
        raise ControlledCrudConflict("required controlled CRUD record is missing")
    if not columns:
        raise ControlledCrudConflict("controlled table has no locked column bindings")
    return _LockedRecords(
        tenant=tenant,
        user_id=str(user_row["id"]),
        user_is_active=user_row["is_active"] is True,
        user_is_tenant_admin=user_row["is_tenant_admin"] is True,
        binding=binding,
        resource=resource,
        authorization=authorization,
        operation=operation,
        columns=columns,
    )


def _database_now(session: Session) -> datetime:
    value = session.execute(select(func.clock_timestamp())).scalar_one()
    if not isinstance(value, datetime) or not _aware(value):
        raise ControlledCrudExecutionError("database returned an invalid transaction time")
    return value


def _validate_locked_records(
    locked: _LockedRecords,
    *,
    command: ControlledCrudCommand,
    action: MutationAction,
    request_hash: str,
    now: datetime,
) -> None:
    tenant_id = str(command.tenant_id)
    workspace_id = None if command.workspace_id is None else str(command.workspace_id)
    actor_id = str(command.actor_user_id)
    resource_id = str(command.request.resource_id)
    expected_version = command.request.resource_version
    binding = locked.binding
    resource = locked.resource
    authorization = locked.authorization
    operation = locked.operation
    decision = command.decision

    current_role = "tenant_admin" if locked.user_is_tenant_admin else "workspace_member"
    expected_operation_version = command.expected_operation_version + (
        2 if operation.state == "succeeded" else 0
    )
    if not all(
        (
            locked.tenant.id == tenant_id,
            locked.tenant.is_active is True,
            locked.tenant.schema_name == command.locator.tenant_schema,
            locked.user_id == actor_id,
            locked.user_is_active is True,
            decision.roles == frozenset({current_role}),
        )
    ):
        raise ControlledCrudAuthorizationDenied("current tenant user role is inactive or changed")

    if not all(
        (
            binding.tenant_id == tenant_id,
            binding.resource_id == resource_id,
            binding.id == str(command.locator.table_binding_id),
            binding.workspace_id in {None, workspace_id},
            binding.physical_table_name == command.locator.physical_table_name,
            binding.resource_version == expected_version,
            binding.state == "active",
            binding.policy_class in _ALLOWED_POLICIES,
            resource.tenant_id == tenant_id,
            resource.id == resource_id,
            resource.version == expected_version,
            resource.state == "active",
            resource.policy_class == binding.policy_class,
        )
    ):
        raise ControlledCrudConflict("locked resource or binding changed")
    if binding.policy_class == "workspace_private" and not (
        workspace_id is not None
        and resource.owner_type == "workspace"
        and resource.owner_id == workspace_id
    ):
        raise ControlledCrudAuthorizationDenied("workspace-private resource ownership mismatch")

    if not all(
        (
            decision.allowed is True,
            decision.user_is_active is True,
            decision.tenant_is_active is True,
            decision.tenant_id == command.tenant_id,
            decision.workspace_id == command.workspace_id,
            decision.actor_user_id == command.actor_user_id,
            decision.resource_id == command.request.resource_id,
            decision.resource_version == expected_version,
            decision.action == action,
            decision.authorization_context_id == command.authorization_context_id,
            decision.evaluated_at <= now < decision.expires_at,
        )
    ):
        raise ControlledCrudAuthorizationDenied("trusted live RBAC decision does not bind request")
    if not all(
        (
            authorization.tenant_id == tenant_id,
            authorization.workspace_id == workspace_id,
            authorization.source == "user_rbac",
            authorization.actor_user_id == actor_id,
            authorization.grant_id is None,
            authorization.live_recheck_required is True,
            authorization.source_version == decision.source_version,
            authorization.snapshot_hash == decision.snapshot_hash,
            decision.roles <= frozenset(authorization.role_snapshot),
            action in authorization.actions,
            resource_id in authorization.resource_ids,
            _aware(authorization.expires_at),
            now < authorization.expires_at,
            decision.expires_at <= authorization.expires_at,
        )
    ):
        raise ControlledCrudAuthorizationDenied("authorization context is stale or out of scope")
    if not all(
        (
            operation.tenant_id == tenant_id,
            operation.workspace_id == workspace_id,
            operation.actor_type == "user",
            operation.actor_id == actor_id,
            operation.resource_id == resource_id,
            operation.resource_version == expected_version,
            operation.request_hash == request_hash,
            operation.kind == action,
            operation.state in {"queued", "succeeded"},
            operation.version == expected_operation_version,
            operation.risk_level in {"R0", "R1"},
            operation.deadline_at is None
            or (_aware(operation.deadline_at) and now < operation.deadline_at),
        )
    ):
        raise ControlledCrudConflict("operation does not bind the authorized mutation")


def _validate_locator_columns(
    locator: TrustedMutationLocator,
    rows: tuple[DataColumnBinding, ...],
    *,
    tenant_id: str,
) -> None:
    if len(rows) != len(locator.columns):
        raise ControlledCrudConflict("trusted locator column set changed")
    for row in rows:
        try:
            logical_id = UUID(row.id)
        except ValueError as exc:
            raise ControlledCrudConflict("stored logical column ID is invalid") from exc
        item = locator.columns.get(logical_id)
        if item is None or not all(
            (
                row.tenant_id == tenant_id,
                row.table_binding_id == str(locator.table_binding_id),
                row.physical_column_name == item.physical_name,
                row.data_type == item.data_type,
                row.type_args == item.type_args,
                row.nullable is item.nullable,
                row.state == "active",
            )
        ):
            raise ControlledCrudConflict("trusted locator column binding changed")


def _reserve_and_lock_idempotency(
    session: Session,
    *,
    command: ControlledCrudCommand,
    action: MutationAction,
    request_hash: str,
    now: datetime,
) -> tuple[IdempotencyRecord, bool]:
    tenant_id = str(command.tenant_id)
    actor_scope = _actor_scope(command)
    statement = (
        pg_insert(IdempotencyRecord)
        .values(
            tenant_id=tenant_id,
            actor_scope=actor_scope,
            operation_name=action,
            key=command.request.idempotency_key,
            request_hash=request_hash,
            state="pending",
            operation_id=str(command.operation_id),
            expires_at=now + _IDEMPOTENCY_TTL,
        )
        .on_conflict_do_nothing(
            index_elements=[
                IdempotencyRecord.tenant_id,
                IdempotencyRecord.actor_scope,
                IdempotencyRecord.operation_name,
                IdempotencyRecord.key,
            ]
        )
        .returning(IdempotencyRecord.id)
    )
    inserted_id = session.execute(statement).scalar_one_or_none()
    record = session.execute(
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.actor_scope == actor_scope,
            IdempotencyRecord.operation_name == action,
            IdempotencyRecord.key == command.request.idempotency_key,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if record is None:
        raise ControlledCrudIdempotencyConflict("idempotency reservation disappeared")
    if record.request_hash != request_hash:
        raise ControlledCrudIdempotencyConflict("idempotency key was reused with different input")
    if record.operation_id != str(command.operation_id):
        raise ControlledCrudIdempotencyConflict("idempotency replay is bound to another operation")
    if not _aware(record.expires_at) or record.expires_at <= now:
        raise ControlledCrudIdempotencyConflict("idempotency reservation expired")
    return record, inserted_id is not None


def _actor_scope(command: ControlledCrudCommand) -> str:
    workspace = "tenant" if command.workspace_id is None else str(command.workspace_id)
    return f"user:{command.actor_user_id}:workspace:{workspace}"


def _replay_result(
    record: IdempotencyRecord,
    *,
    operation: OperationRecord,
    command: ControlledCrudCommand,
    action: MutationAction,
    request_hash: str,
) -> ControlledCrudResult:
    if record.state == "pending":
        raise ControlledCrudIdempotencyConflict("matching mutation is still pending")
    if record.state == "failed":
        raise ControlledCrudIdempotencyConflict("matching mutation previously failed")
    metadata = record.response_ref
    affected_rows = metadata.get("affected_rows") if isinstance(metadata, dict) else None
    expected_keys = {
        "operation_id",
        "resource_id",
        "resource_version",
        "action",
        "affected_rows",
        "request_hash",
        "status",
    }
    if (
        record.state != "completed"
        or not isinstance(metadata, dict)
        or set(metadata) != expected_keys
        or metadata.get("operation_id") != str(command.operation_id)
        or metadata.get("resource_id") != str(command.request.resource_id)
        or metadata.get("resource_version") != command.request.resource_version
        or metadata.get("action") != action
        or metadata.get("request_hash") != request_hash
        or metadata.get("status") != "succeeded"
        or operation.state != "succeeded"
        or operation.result_ref != metadata
        or isinstance(affected_rows, bool)
        or not isinstance(affected_rows, int)
        or not 0 <= affected_rows <= 100
    ):
        raise ControlledCrudIdempotencyConflict("stored replay metadata is invalid")
    return ControlledCrudResult(
        operation_id=command.operation_id,
        resource_id=command.request.resource_id,
        resource_version=command.request.resource_version,
        action=action,
        affected_rows=affected_rows,
        request_hash=request_hash,
        replayed=True,
    )


def _mark_operation_running(operation: OperationRecord, now: datetime) -> None:
    operation.state = "running"
    operation.started_at = operation.started_at or now
    operation.attempt_count += 1
    operation.progress = 1
    operation.version += 1


def _apply_prepared(
    session: Session,
    *,
    command: ControlledCrudCommand,
    locked: _LockedRecords,
    prepared: PreparedInsert | PreparedFilteredMutation,
) -> int:
    if isinstance(prepared, PreparedInsert):
        _recheck_versions(session, command=command, locked=locked)
        result = cast("_RowcountResult", session.execute(prepared.statement))
        if result.rowcount != prepared.row_count:
            raise ControlledCrudConflict("insert affected an unexpected number of rows")
        return prepared.row_count

    row_tokens = list(session.execute(prepared.preflight).scalars().all())
    if len(row_tokens) > prepared.max_rows:
        raise MutationBudgetExceeded("mutation matched more rows than max_rows")
    _recheck_versions(session, command=command, locked=locked)
    if not row_tokens:
        return 0
    result = cast(
        "_RowcountResult",
        session.execute(prepared.build_apply_statement(row_tokens)),
    )
    if result.rowcount != len(row_tokens):
        raise ControlledCrudConflict("mutation target changed after locked preflight")
    return len(row_tokens)


def _recheck_versions(
    session: Session,
    *,
    command: ControlledCrudCommand,
    locked: _LockedRecords,
) -> None:
    binding_version = session.execute(
        select(DataTableBinding.resource_version).where(
            DataTableBinding.id == locked.binding.id,
            DataTableBinding.tenant_id == str(command.tenant_id),
        )
    ).scalar_one()
    resource_version = session.execute(
        select(ResourceRecord.version).where(
            ResourceRecord.id == locked.resource.id,
            ResourceRecord.tenant_id == str(command.tenant_id),
        )
    ).scalar_one()
    expected = command.request.resource_version
    if binding_version != expected or resource_version != expected:
        raise ControlledCrudConflict("resource version changed before apply")


def _mark_completed(
    operation: OperationRecord,
    idempotency: IdempotencyRecord,
    *,
    result: ControlledCrudResult,
    now: datetime,
) -> None:
    metadata = result.as_safe_metadata()
    operation.state = "succeeded"
    operation.progress = 100
    operation.completed_at = now
    operation.result_ref = metadata
    operation.error_code = None
    operation.error_detail = None
    operation.version += 1
    idempotency.state = "completed"
    idempotency.response_ref = metadata
    idempotency.version += 1


def _assert_same_connection(session: Session, connection: object) -> None:
    if session.connection() is not connection:
        raise ControlledCrudExecutionError("session connection changed during mutation")


def _aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


__all__ = [
    "ControlledCrudAuthorizationDenied",
    "ControlledCrudCommand",
    "ControlledCrudConflict",
    "ControlledCrudDatabaseFailure",
    "ControlledCrudExecutionError",
    "ControlledCrudIdempotencyConflict",
    "ControlledCrudResult",
    "ControlledCrudSuccessAuditError",
    "TrustedUserRbacDecision",
    "execute_controlled_crud",
    "execute_controlled_crud_in_transaction",
]
