"""Internal, tenant-admin-only bootstrap for controlled create-table plans.

This module is deliberately not imported by any Router.  Its only caller input
is a server-owned request context plus a logical workspace and the strict
create-table definition.  Tenant schemas, physical identifiers, resource IDs,
authorization IDs, and operation IDs are resolved or generated here.

The current DDL authorization vocabulary does not yet contain a distinct
``data.schema.create`` action.  ``data.schema.apply`` is therefore the narrowest
action that can both authorize registration and pass the existing apply-time
live recheck.  Splitting create from apply requires a coordinated DDL contract
migration and must not be done only in this bootstrap layer.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Boolean, column, select, table
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Session

from omnibase.control_plane.models import IdempotencyRecord, ResourceRecord
from omnibase.control_plane.service import complete_idempotency, reserve_idempotency
from omnibase.controlled_data.ddl_contracts import CreateTablePlanDefinition
from omnibase.controlled_data.models import AuthorizationContext
from omnibase.controlled_data.operation_service import (
    CreateTableRegistration,
    register_create_table,
)
from omnibase.db.models import Tenant
from omnibase.tenants.schema_manager import validate_schema_name

CREATE_TABLE_AUTHORIZATION_ACTION = "data.schema.apply"
CREATE_TABLE_IDEMPOTENCY_OPERATION = "data.schema.create"
_AUTHORIZATION_TTL = timedelta(minutes=5)
_IDEMPOTENCY_TTL = timedelta(hours=24)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class CreateTableBootstrapError(RuntimeError):
    """Base class for sanitized internal bootstrap failures."""


class CreateTableBootstrapDenied(CreateTableBootstrapError):
    """The trusted request context is not currently authorized."""


class CreateTableBootstrapConflict(CreateTableBootstrapError):
    """The workspace or idempotency state cannot safely accept the request."""


@dataclass(frozen=True, slots=True)
class TrustedCreateTableRequestContext:
    """Server-owned identity and replay context, never a public request DTO."""

    tenant_id: UUID
    actor_user_id: UUID
    request_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not _REQUEST_ID.fullmatch(self.request_id):
            raise ValueError("request_id must contain 1 to 64 safe characters")
        if not _IDEMPOTENCY_KEY.fullmatch(self.idempotency_key):
            raise ValueError("idempotency_key must contain 8 to 128 safe characters")


@dataclass(frozen=True, slots=True)
class CreateTableBootstrapResult:
    """Internal result; logical IDs are retained for exact idempotency replay."""

    resource_id: UUID
    operation_id: UUID
    authorization_context_id: UUID
    idempotency: IdempotencyRecord
    replayed: bool
    registration: CreateTableRegistration | None = None
    authorization: AuthorizationContext | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _digest(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _request_hash(
    context: TrustedCreateTableRequestContext,
    workspace_id: UUID,
    definition: CreateTablePlanDefinition,
) -> str:
    return _digest(
        {
            "actor_user_id": str(context.actor_user_id),
            "definition": definition.model_dump(mode="json"),
            "tenant_id": str(context.tenant_id),
            "workspace_id": str(workspace_id),
        }
    )


def _authorization_snapshot_hash(
    *,
    tenant_id: UUID,
    workspace_id: UUID,
    actor_user_id: UUID,
    resource_id: UUID,
    source_version: int,
    created_at: datetime,
    expires_at: datetime,
    request_hash: str,
) -> str:
    return _digest(
        {
            "actions": [CREATE_TABLE_AUTHORIZATION_ACTION],
            "actor_user_id": str(actor_user_id),
            "created_at": created_at.isoformat(),
            "expires_at": expires_at.isoformat(),
            "request_hash": request_hash,
            "resource_ids": [str(resource_id)],
            "role_snapshot": ["tenant_admin"],
            "source": "user_rbac",
            "source_version": source_version,
            "tenant_id": str(tenant_id),
            "workspace_id": str(workspace_id),
        }
    )


def _locked_tenant(session: Session, tenant_id: UUID) -> Tenant:
    tenant = session.execute(
        select(Tenant).where(Tenant.id == str(tenant_id)).with_for_update()
    ).scalar_one_or_none()
    if tenant is None or tenant.is_active is not True:
        raise CreateTableBootstrapDenied("tenant is inactive or unavailable")
    validate_schema_name(tenant.schema_name)
    return tenant


def _lock_tenant_admin(
    session: Session,
    *,
    schema_name: str,
    actor_user_id: UUID,
) -> None:
    user_source = table(
        "users",
        column("id", PG_UUID(as_uuid=False)),
        column("is_active", Boolean()),
        column("is_tenant_admin", Boolean()),
        schema=schema_name,
    )
    user = (
        session.execute(
            select(
                user_source.c.id,
                user_source.c.is_active,
                user_source.c.is_tenant_admin,
            )
            .where(user_source.c.id == str(actor_user_id))
            .with_for_update()
        )
        .mappings()
        .one_or_none()
    )
    if user is None or user["is_active"] is not True or user["is_tenant_admin"] is not True:
        raise CreateTableBootstrapDenied("active tenant administrator is required")


def _locked_workspace(session: Session, *, tenant_id: UUID, workspace_id: UUID) -> ResourceRecord:
    workspace = session.execute(
        select(ResourceRecord)
        .where(
            ResourceRecord.id == str(workspace_id),
            ResourceRecord.tenant_id == str(tenant_id),
        )
        .with_for_update()
    ).scalar_one_or_none()
    if (
        workspace is None
        or workspace.id != str(workspace_id)
        or workspace.tenant_id != str(tenant_id)
        or workspace.kind != "workspace"
        or workspace.state != "active"
    ):
        raise CreateTableBootstrapConflict("workspace is not an active tenant resource")
    return workspace


def _replayed_result(record: IdempotencyRecord) -> CreateTableBootstrapResult:
    response = record.response_ref
    if record.operation_id is None or not isinstance(response, dict):
        raise CreateTableBootstrapConflict("completed replay metadata is incomplete")
    try:
        resource_id = UUID(str(response["resource_id"]))
        operation_id = UUID(str(response["operation_id"]))
        authorization_context_id = UUID(str(response["authorization_context_id"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise CreateTableBootstrapConflict("completed replay metadata is invalid") from exc
    if str(operation_id) != record.operation_id:
        raise CreateTableBootstrapConflict("completed replay operation is cross-wired")
    return CreateTableBootstrapResult(
        resource_id=resource_id,
        operation_id=operation_id,
        authorization_context_id=authorization_context_id,
        idempotency=record,
        replayed=True,
    )


def bootstrap_create_table(
    session: Session,
    *,
    context: TrustedCreateTableRequestContext,
    workspace_id: UUID,
    definition: CreateTablePlanDefinition,
) -> CreateTableBootstrapResult:
    """Authorize and register one create-table aggregate in an atomic savepoint.

    The caller owns the outer transaction and must commit it.  The nested
    transaction guarantees that an exception after AuthorizationContext flush
    cannot leave a partial bootstrap aggregate in the caller's transaction.
    """

    with session.begin_nested():
        tenant = _locked_tenant(session, context.tenant_id)
        _lock_tenant_admin(
            session,
            schema_name=tenant.schema_name,
            actor_user_id=context.actor_user_id,
        )
        _locked_workspace(
            session,
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
        )

        request_hash = _request_hash(context, workspace_id, definition)
        now = _now()
        idempotency, created = reserve_idempotency(
            session,
            tenant_id=str(context.tenant_id),
            actor_scope=f"user:{context.actor_user_id}",
            operation_name=CREATE_TABLE_IDEMPOTENCY_OPERATION,
            key=context.idempotency_key,
            request_hash=request_hash,
            expires_at=now + _IDEMPOTENCY_TTL,
        )
        if not created:
            if idempotency.state == "completed":
                return _replayed_result(idempotency)
            raise CreateTableBootstrapConflict("matching create-table request is not completed")
        if idempotency.state != "pending":
            raise CreateTableBootstrapConflict("new idempotency reservation is not pending")

        resource_id = uuid4()
        authorization_context_id = uuid4()
        authorization_expires_at = now + _AUTHORIZATION_TTL
        source_version = 1
        authorization = AuthorizationContext(
            id=str(authorization_context_id),
            tenant_id=str(context.tenant_id),
            workspace_id=str(workspace_id),
            source="user_rbac",
            actor_user_id=str(context.actor_user_id),
            grant_id=None,
            role_snapshot=["tenant_admin"],
            actions=[CREATE_TABLE_AUTHORIZATION_ACTION],
            resource_ids=[str(resource_id)],
            source_version=source_version,
            snapshot_hash=_authorization_snapshot_hash(
                tenant_id=context.tenant_id,
                workspace_id=workspace_id,
                actor_user_id=context.actor_user_id,
                resource_id=resource_id,
                source_version=source_version,
                created_at=now,
                expires_at=authorization_expires_at,
                request_hash=request_hash,
            ),
            live_recheck_required=True,
            created_at=now,
            expires_at=authorization_expires_at,
        )
        session.add(authorization)
        session.flush()

        registration = register_create_table(
            session,
            tenant_id=context.tenant_id,
            workspace_id=workspace_id,
            actor_user_id=context.actor_user_id,
            authorization_context_id=authorization_context_id,
            policy_class="workspace_private",
            definition=definition,
            expires_at=authorization_expires_at,
            resource_id=resource_id,
        )
        operation_id = UUID(registration.operation.id)
        response_ref: dict[str, object] = {
            "authorization_context_id": str(authorization_context_id),
            "operation_id": str(operation_id),
            "resource_id": str(resource_id),
        }
        idempotency = complete_idempotency(
            session,
            tenant_id=str(context.tenant_id),
            record_id=idempotency.id,
            response_ref=response_ref,
            operation_id=str(operation_id),
            expected_version=idempotency.version,
        )
        return CreateTableBootstrapResult(
            resource_id=resource_id,
            operation_id=operation_id,
            authorization_context_id=authorization_context_id,
            idempotency=idempotency,
            replayed=False,
            registration=registration,
            authorization=authorization,
        )


__all__ = [
    "CREATE_TABLE_AUTHORIZATION_ACTION",
    "CREATE_TABLE_IDEMPOTENCY_OPERATION",
    "CreateTableBootstrapConflict",
    "CreateTableBootstrapDenied",
    "CreateTableBootstrapError",
    "CreateTableBootstrapResult",
    "TrustedCreateTableRequestContext",
    "bootstrap_create_table",
]
