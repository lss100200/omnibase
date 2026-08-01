"""Fail-closed User-RBAC HTTP entry point for P34.3 controlled row writes.

The route accepts only logical resource/column UUIDs.  Tenant identity, the
physical locator, AuthorizationContext, OperationRecord, and the short-lived
RBAC decision are all resolved or created by the server.

The built-in executor is deliberately *not* installed by importing this
module.  An application owner must explicitly set
``app.state.controlled_crud_executor`` to an atomic-success-audit executor.
This keeps the new write surface closed until deployment wiring is approved.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import Boolean, column, func, select, table
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from omnibase.capabilities.models import CapabilityGrant
from omnibase.control_plane.models import (
    AuditEvent,
    IdempotencyRecord,
    OperationRecord,
    ResourceRecord,
)
from omnibase.controlled_data.crud import (
    MutationColumnBinding,
    TrustedMutationLocator,
    canonical_request_hash,
)
from omnibase.controlled_data.crud_contracts import (
    DeleteMutationRequest,
    InsertMutationRequest,
    UpdateMutationRequest,
)
from omnibase.controlled_data.execution_service import (
    AtomicControlledCrudLifecycleExecutor,
    ControlledCrudAtomicAuditContractError,
    ControlledCrudAuditContext,
    ControlledCrudAuditPersistenceError,
    ControlledCrudServiceError,
    SessionFactory,
    execute_controlled_crud_lifecycle_audited,
)
from omnibase.controlled_data.executor import (
    ControlledCrudCommand,
    MutationAction,
    TrustedUserRbacDecision,
)
from omnibase.controlled_data.models import (
    AuthorizationContext,
    DataColumnBinding,
    DataTableBinding,
)
from omnibase.controlled_data.schemas import (
    ControlledWriteErrorResponse,
    ControlledWriteRequest,
    ControlledWriteResponse,
)
from omnibase.controlled_data.types import LogicalDataType
from omnibase.core.db import get_session_factory
from omnibase.db.models import Tenant
from omnibase.tenants.dependencies import CurrentPrincipal, get_current_principal

router = APIRouter(prefix="/controlled-data", tags=["controlled-data"])

MutationRequestType = InsertMutationRequest | UpdateMutationRequest | DeleteMutationRequest

_ACTION_BY_KIND: dict[str, MutationAction] = {
    "insert": "data.rows.insert",
    "update": "data.rows.update",
    "delete": "data.rows.delete",
}
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_AUTHORIZATION_TTL = timedelta(minutes=5)
_DECISION_TTL = timedelta(seconds=30)
# AuthorizationContext has a composite FK to CapabilityGrant.  Keep an
# explicit model reference so standalone Router/lifecycle imports register the
# target table in shared SQLAlchemy metadata before AuthorizationContext flush.
_CAPABILITY_GRANT_TABLE = CapabilityGrant.__table__


@dataclass(frozen=True, slots=True)
class ControlledCrudComponents:
    session_factory: SessionFactory
    executor: AtomicControlledCrudLifecycleExecutor


class ControlledWriteRouteError(RuntimeError):
    def __init__(self, code: str, message: str, status_code: int) -> None:
        super().__init__(code)
        self.code = code
        self.message = message
        self.status_code = status_code


def get_controlled_crud_components(request: Request) -> ControlledCrudComponents:
    """Resolve explicitly installed components; absence is a closed feature gate."""
    executor = getattr(request.app.state, "controlled_crud_executor", None)
    if executor is None or getattr(executor, "supports_atomic_lifecycle", False) is not True:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "controlled_write_unavailable",
            "Controlled writes are not enabled",
        )
    factory = getattr(request.app.state, "controlled_crud_session_factory", None)
    return ControlledCrudComponents(
        session_factory=factory or get_session_factory(),
        executor=cast("AtomicControlledCrudLifecycleExecutor", executor),
    )


@router.post(
    "/rows/mutate",
    response_model=ControlledWriteResponse,
    responses={
        401: {"model": ControlledWriteErrorResponse},
        403: {"model": ControlledWriteErrorResponse},
        404: {"model": ControlledWriteErrorResponse},
        409: {"model": ControlledWriteErrorResponse},
        422: {"model": ControlledWriteErrorResponse},
        500: {"model": ControlledWriteErrorResponse},
        503: {"model": ControlledWriteErrorResponse},
        504: {"model": ControlledWriteErrorResponse},
    },
)
def mutate_rows(
    payload: ControlledWriteRequest,
    request: Request,
    principal: CurrentPrincipal = Depends(get_current_principal),
    components: ControlledCrudComponents = Depends(get_controlled_crud_components),
) -> ControlledWriteResponse:
    request_id = _request_id(request)
    try:
        result = execute_controlled_crud_lifecycle_audited(
            components.session_factory,
            lambda session: _build_command_in_transaction(
                session,
                principal=principal,
                mutation=payload.mutation,
            ),
            audit=ControlledCrudAuditContext(request_id=request_id, risk_level="R1"),
            executor=components.executor,
        )
    except ControlledWriteRouteError as exc:
        try:
            _append_preflight_failure_audit(
                components.session_factory,
                principal=principal,
                mutation=payload.mutation,
                request_id=request_id,
                failure=exc,
            )
        except Exception:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "CONTROLLED_CRUD_AUDIT_PERSISTENCE_FAILED",
                "Controlled write service is unavailable",
                request_id=request_id,
                retryable=False,
            ) from None
        raise _http_error(exc.status_code, exc.code, exc.message, request_id=request_id) from None
    except SQLAlchemyError:
        failure = ControlledWriteRouteError(
            "controlled_write_unavailable",
            "Controlled write service is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
        try:
            _append_preflight_failure_audit(
                components.session_factory,
                principal=principal,
                mutation=payload.mutation,
                request_id=request_id,
                failure=failure,
            )
        except Exception:
            raise _http_error(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "CONTROLLED_CRUD_AUDIT_PERSISTENCE_FAILED",
                "Controlled write service is unavailable",
                request_id=request_id,
                retryable=False,
            ) from None
        raise _http_error(
            failure.status_code,
            failure.code,
            failure.message,
            request_id=request_id,
            retryable=True,
        ) from None
    except ControlledCrudServiceError as exc:
        raise _http_error(
            exc.status_code,
            exc.code,
            "Controlled write was rejected",
            request_id=exc.request_id,
            retryable=exc.retryable,
        ) from None
    except (ControlledCrudAuditPersistenceError, ControlledCrudAtomicAuditContractError) as exc:
        raise _http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            exc.code,
            "Controlled write service is unavailable",
            request_id=exc.request_id,
            retryable=False,
        ) from None

    return ControlledWriteResponse(
        operation_id=result.operation_id,
        resource_id=result.resource_id,
        resource_version=result.resource_version,
        action=result.action,
        affected_rows=result.affected_rows,
        replayed=result.replayed,
        request_id=request_id,
    )


def _build_command(
    session_source: SessionFactory | Session,
    *,
    principal: CurrentPrincipal,
    mutation: MutationRequestType,
) -> ControlledCrudCommand:
    """Build with either an owned Session factory or an existing transaction."""
    if isinstance(session_source, Session):
        owns_session = False
        session = session_source
    else:
        owns_session = True
        session = session_source()
    if not owns_session and not session.in_transaction():
        raise ControlledWriteRouteError(
            "controlled_write_unavailable",
            "Controlled write service is unavailable",
            status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    try:
        transaction = session.begin() if owns_session else nullcontext()
        with transaction:
            connection = session.connection()
            lock_timeout_ms = min(1_000, mutation.timeout_ms)
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = '{mutation.timeout_ms}ms'")
            connection.exec_driver_sql(f"SET LOCAL lock_timeout = '{lock_timeout_ms}ms'")
            now = session.execute(select(func.clock_timestamp())).scalar_one()
            if not isinstance(now, datetime) or now.tzinfo is None:
                raise ControlledWriteRouteError(
                    "controlled_write_unavailable",
                    "Controlled write service is unavailable",
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            tenant_id = str(principal.tenant.id)
            actor_id = str(principal.user.id)
            tenant = session.execute(
                select(Tenant).where(Tenant.id == tenant_id).with_for_update()
            ).scalar_one_or_none()
            if tenant is None or tenant.is_active is not True:
                raise _forbidden()
            if tenant.schema_name != principal.tenant.schema_name:
                raise _forbidden()

            user = _lock_user(session, schema_name=tenant.schema_name, actor_id=actor_id)
            if user is None or user["is_active"] is not True:
                raise _forbidden()

            resource = session.execute(
                select(ResourceRecord)
                .where(
                    ResourceRecord.id == str(mutation.resource_id),
                    ResourceRecord.tenant_id == tenant_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            binding = session.execute(
                select(DataTableBinding)
                .where(
                    DataTableBinding.resource_id == str(mutation.resource_id),
                    DataTableBinding.tenant_id == tenant_id,
                )
                .with_for_update()
            ).scalar_one_or_none()
            if resource is None or binding is None:
                raise _not_found()
            _authorize_resource(
                resource=resource,
                binding=binding,
                actor_id=actor_id,
                is_tenant_admin=user["is_tenant_admin"] is True,
                requested_version=mutation.resource_version,
            )

            columns = tuple(
                session.execute(
                    select(DataColumnBinding)
                    .where(
                        DataColumnBinding.tenant_id == tenant_id,
                        DataColumnBinding.table_binding_id == binding.id,
                    )
                    .order_by(DataColumnBinding.ordinal, DataColumnBinding.id)
                    .with_for_update()
                )
                .scalars()
                .all()
            )
            if not columns or any(item.state != "active" for item in columns):
                raise _not_found()

            action = _ACTION_BY_KIND[mutation.kind]
            request_hash = canonical_request_hash(mutation)
            roles = frozenset(
                {"tenant_admin" if user["is_tenant_admin"] is True else "workspace_member"}
            )
            source_version = resource.version
            authorization_id = uuid4()
            operation_id, expected_operation_version = _operation_identity(
                session,
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                mutation=mutation,
                request_hash=request_hash,
                now=now,
            )
            snapshot_hash = _snapshot_hash(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=action,
                resource_id=str(resource.id),
                resource_version=resource.version,
                roles=roles,
                source_version=source_version,
            )
            authorization_expires_at = now + _AUTHORIZATION_TTL
            decision_expires_at = now + _DECISION_TTL
            session.add(
                AuthorizationContext(
                    id=str(authorization_id),
                    tenant_id=tenant_id,
                    workspace_id=None,
                    source="user_rbac",
                    actor_user_id=actor_id,
                    grant_id=None,
                    role_snapshot=sorted(roles),
                    actions=[action],
                    resource_ids=[str(resource.id)],
                    source_version=source_version,
                    snapshot_hash=snapshot_hash,
                    live_recheck_required=True,
                    created_at=now,
                    expires_at=authorization_expires_at,
                )
            )
            session.flush()

            locator = TrustedMutationLocator(
                tenant_schema=tenant.schema_name,
                table_binding_id=UUID(binding.id),
                resource_id=UUID(resource.id),
                resource_version=resource.version,
                physical_table_name=binding.physical_table_name,
                columns=MappingProxyType(
                    {
                        UUID(item.id): MutationColumnBinding(
                            logical_id=UUID(item.id),
                            physical_name=item.physical_column_name,
                            data_type=cast("LogicalDataType", item.data_type),
                            type_args=item.type_args,
                            nullable=item.nullable,
                        )
                        for item in columns
                    }
                ),
            )
            decision = TrustedUserRbacDecision(
                decision_id=uuid4(),
                allowed=True,
                tenant_id=UUID(tenant_id),
                workspace_id=None,
                actor_user_id=UUID(actor_id),
                resource_id=UUID(resource.id),
                resource_version=resource.version,
                action=action,
                authorization_context_id=authorization_id,
                source_version=source_version,
                snapshot_hash=snapshot_hash,
                roles=roles,
                user_is_active=True,
                tenant_is_active=True,
                evaluated_at=now.astimezone(UTC),
                expires_at=decision_expires_at.astimezone(UTC),
            )
            return ControlledCrudCommand(
                tenant_id=UUID(tenant_id),
                workspace_id=None,
                actor_user_id=UUID(actor_id),
                authorization_context_id=authorization_id,
                operation_id=operation_id,
                locator=locator,
                request=mutation,
                decision=decision,
                expected_operation_version=expected_operation_version,
                lock_timeout_ms=min(1_000, mutation.timeout_ms),
            )
    finally:
        if owns_session:
            session.close()


def _build_command_in_transaction(
    session: Session,
    *,
    principal: CurrentPrincipal,
    mutation: MutationRequestType,
) -> ControlledCrudCommand:
    """Bootstrap command records in the lifecycle service's active transaction."""
    return _build_command(
        session,
        principal=principal,
        mutation=mutation,
    )


def _lock_user(session: Session, *, schema_name: str, actor_id: str):
    user_source = table(
        "users",
        column("id", PG_UUID(as_uuid=False)),
        column("is_active", Boolean()),
        column("is_tenant_admin", Boolean()),
        schema=schema_name,
    )
    return (
        session.execute(
            select(
                user_source.c.id,
                user_source.c.is_active,
                user_source.c.is_tenant_admin,
            )
            .where(user_source.c.id == actor_id)
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )


def _authorize_resource(
    *,
    resource: ResourceRecord,
    binding: DataTableBinding,
    actor_id: str,
    is_tenant_admin: bool,
    requested_version: int,
) -> None:
    if resource.state != "active" or binding.state != "active":
        raise _not_found()
    if resource.version != requested_version or binding.resource_version != requested_version:
        raise ControlledWriteRouteError(
            "controlled_write_version_conflict",
            "Resource version does not match",
            status.HTTP_409_CONFLICT,
        )
    if resource.policy_class != binding.policy_class:
        raise _not_found()
    if binding.workspace_id is not None or binding.policy_class == "workspace_private":
        raise _forbidden()
    if binding.policy_class not in {"tenant_managed", "controlled_shared"}:
        raise _forbidden()
    if not is_tenant_admin and not (
        resource.owner_type == "user" and resource.owner_id == actor_id
    ):
        raise _forbidden()


def _operation_identity(
    session: Session,
    *,
    tenant_id: str,
    actor_id: str,
    action: str,
    mutation: MutationRequestType,
    request_hash: str,
    now: datetime,
) -> tuple[UUID, int]:
    actor_scope = f"user:{actor_id}:workspace:tenant"
    record = session.execute(
        select(IdempotencyRecord)
        .where(
            IdempotencyRecord.tenant_id == tenant_id,
            IdempotencyRecord.actor_scope == actor_scope,
            IdempotencyRecord.operation_name == action,
            IdempotencyRecord.key == mutation.idempotency_key,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if record is None:
        operation_id = uuid5(
            NAMESPACE_URL,
            "omnibase:controlled-write:"
            f"{tenant_id}:{actor_id}:{action}:{mutation.idempotency_key}",
        )
        session.execute(
            pg_insert(OperationRecord)
            .values(
                id=str(operation_id),
                tenant_id=tenant_id,
                workspace_id=None,
                actor_type="user",
                actor_id=actor_id,
                resource_id=str(mutation.resource_id),
                resource_version=mutation.resource_version,
                request_hash=request_hash,
                kind=action,
                state="queued",
                risk_level="R1",
                progress=0,
                attempt_count=0,
                version=1,
                deadline_at=now + _AUTHORIZATION_TTL,
                operation_metadata={"request_origin": "user_rbac_http"},
            )
            .on_conflict_do_nothing(index_elements=[OperationRecord.id])
        )
        operation = session.execute(
            select(OperationRecord)
            .where(
                OperationRecord.id == str(operation_id),
                OperationRecord.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if operation is None or not all(
            (
                operation.actor_type == "user",
                operation.actor_id == actor_id,
                operation.resource_id == str(mutation.resource_id),
                operation.resource_version == mutation.resource_version,
                operation.request_hash == request_hash,
                operation.kind == action,
                operation.state == "queued",
                operation.version == 1,
            )
        ):
            raise ControlledWriteRouteError(
                "CONTROLLED_CRUD_IDEMPOTENCY_CONFLICT",
                "Idempotency key conflicts with an existing request",
                status.HTTP_409_CONFLICT,
            )
        return operation_id, 1
    if (
        record.request_hash != request_hash
        or record.operation_id is None
        or record.expires_at.tzinfo is None
        or record.expires_at <= now
    ):
        raise ControlledWriteRouteError(
            "CONTROLLED_CRUD_IDEMPOTENCY_CONFLICT",
            "Idempotency key conflicts with an existing request",
            status.HTTP_409_CONFLICT,
        )
    operation = session.execute(
        select(OperationRecord)
        .where(
            OperationRecord.id == record.operation_id,
            OperationRecord.tenant_id == tenant_id,
        )
        .with_for_update()
    ).scalar_one_or_none()
    if operation is None or operation.request_hash != request_hash or operation.kind != action:
        raise ControlledWriteRouteError(
            "CONTROLLED_CRUD_IDEMPOTENCY_CONFLICT",
            "Idempotency key conflicts with an existing request",
            status.HTTP_409_CONFLICT,
        )
    expected_version = (
        operation.version - 2 if operation.state == "succeeded" else operation.version
    )
    if expected_version < 1:
        raise ControlledWriteRouteError(
            "CONTROLLED_CRUD_IDEMPOTENCY_CONFLICT",
            "Idempotency key conflicts with an existing request",
            status.HTTP_409_CONFLICT,
        )
    return UUID(operation.id), expected_version


def _append_preflight_failure_audit(
    session_factory: SessionFactory,
    *,
    principal: CurrentPrincipal,
    mutation: MutationRequestType,
    request_id: str,
    failure: ControlledWriteRouteError,
) -> None:
    """Persist code-only preflight denials without registry or locator details."""
    session = session_factory()
    try:
        with session.begin():
            session.add(
                AuditEvent(
                    tenant_id=str(principal.tenant.id),
                    request_id=request_id,
                    actor_type="user",
                    actor_id=str(principal.user.id),
                    workspace_id=None,
                    resource_id=str(mutation.resource_id),
                    operation_id=None,
                    action=_ACTION_BY_KIND[mutation.kind],
                    decision=(
                        "denied"
                        if failure.status_code
                        in {
                            status.HTTP_403_FORBIDDEN,
                            status.HTTP_404_NOT_FOUND,
                            422,
                        }
                        else "error"
                    ),
                    risk_level="R1",
                    input_hash=canonical_request_hash(mutation),
                    before_version=mutation.resource_version,
                    after_version=None,
                    status_code=failure.status_code,
                    row_count=None,
                    details={"reason_code": failure.code, "retryable": False},
                )
            )
            session.flush()
    finally:
        session.close()


def _snapshot_hash(
    *,
    tenant_id: str,
    actor_id: str,
    action: str,
    resource_id: str,
    resource_version: int,
    roles: frozenset[str],
    source_version: int,
) -> str:
    encoded = json.dumps(
        {
            "action": action,
            "actor_user_id": actor_id,
            "resource_id": resource_id,
            "resource_version": resource_version,
            "roles": sorted(roles),
            "source": "user_rbac",
            "source_version": source_version,
            "tenant_id": tenant_id,
            "workspace_id": None,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request_id(request: Request) -> str:
    candidate = structlog.contextvars.get_contextvars().get("request_id")
    if isinstance(candidate, str) and _REQUEST_ID.fullmatch(candidate):
        return candidate
    header = request.headers.get("X-Request-Id", "").strip()
    return header if _REQUEST_ID.fullmatch(header) else str(uuid4())


def _not_found() -> ControlledWriteRouteError:
    return ControlledWriteRouteError(
        "resource_not_found",
        "Resource not found",
        status.HTTP_404_NOT_FOUND,
    )


def _forbidden() -> ControlledWriteRouteError:
    return ControlledWriteRouteError(
        "controlled_write_forbidden",
        "Controlled write is not permitted",
        status.HTTP_403_FORBIDDEN,
    )


def _http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    request_id: str | None = None,
    retryable: bool | None = None,
) -> HTTPException:
    error: dict[str, object] = {"code": code, "message": message}
    if request_id is not None:
        error["request_id"] = request_id
    if retryable is not None:
        error["retryable"] = retryable
    return HTTPException(status_code=status_code, detail={"error": error})


__all__ = [
    "ControlledCrudComponents",
    "ControlledWriteRouteError",
    "get_controlled_crud_components",
    "mutate_rows",
    "router",
]
