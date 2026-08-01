"""Read-only HTTP surface for Phase 3-4 control-plane records.

Mutation services are internal-only in P34.1.  This router intentionally
defines no POST, PATCH, PUT, or DELETE endpoint.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from omnibase.control_plane.schemas import (
    ApprovalList,
    ApprovalRead,
    AuditEventList,
    AuditEventRead,
    OperationList,
    OperationRead,
    ResourceList,
    ResourceRead,
)
from omnibase.control_plane.service import (
    ApprovalNotFound,
    OperationNotFound,
    ResourceNotFound,
    get_approval,
    get_operation,
    get_resource,
    list_approvals,
    list_audit_events,
    list_operations,
    list_resources,
)
from omnibase.tenants.dependencies import (
    TenantContext,
    get_tenant_db,
    require_tenant_admin,
)

router = APIRouter(prefix="/control-plane", tags=["control-plane"])

_MAX_PAGE_SIZE = 100
_MAX_OFFSET = 100_000


def _not_found() -> HTTPException:
    """Use one response for absent and cross-tenant identifiers."""
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": {"code": "not_found", "message": "Record not found"}},
    )


@router.get(
    "/resources",
    response_model=ResourceList,
    summary="List logical resources for the current tenant",
)
def list_resources_endpoint(
    limit: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
    kind: str | None = Query(default=None, pattern=r"^[a-z][a-z0-9_]{1,63}$"),
    state: str | None = Query(default=None, max_length=32),
    ctx: TenantContext = Depends(require_tenant_admin),
    db: Session = Depends(get_tenant_db),
) -> ResourceList:
    items, total = list_resources(
        db,
        tenant_id=ctx.tenant_id,
        limit=limit,
        offset=offset,
        kind=kind,
        state=state,
    )
    return ResourceList(
        items=[ResourceRead.model_validate(item) for item in items],
        total=total,
    )


@router.get(
    "/resources/{resource_id}",
    response_model=ResourceRead,
    summary="Get a logical resource for the current tenant",
)
def get_resource_endpoint(
    resource_id: UUID,
    ctx: TenantContext = Depends(require_tenant_admin),
    db: Session = Depends(get_tenant_db),
) -> ResourceRead:
    try:
        resource = get_resource(
            db,
            tenant_id=ctx.tenant_id,
            resource_id=str(resource_id),
        )
    except ResourceNotFound as exc:
        raise _not_found() from exc
    return ResourceRead.model_validate(resource)


@router.get(
    "/operations",
    response_model=OperationList,
    summary="List durable operations for the current tenant",
)
def list_operations_endpoint(
    limit: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
    state: str | None = Query(default=None, max_length=16),
    resource_id: UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_tenant_admin),
    db: Session = Depends(get_tenant_db),
) -> OperationList:
    items, total = list_operations(
        db,
        tenant_id=ctx.tenant_id,
        limit=limit,
        offset=offset,
        state=state,
        resource_id=str(resource_id) if resource_id is not None else None,
    )
    return OperationList(
        items=[OperationRead.model_validate(item) for item in items],
        total=total,
    )


@router.get(
    "/operations/{operation_id}",
    response_model=OperationRead,
    summary="Get a durable operation for the current tenant",
)
def get_operation_endpoint(
    operation_id: UUID,
    ctx: TenantContext = Depends(require_tenant_admin),
    db: Session = Depends(get_tenant_db),
) -> OperationRead:
    try:
        operation = get_operation(
            db,
            tenant_id=ctx.tenant_id,
            operation_id=str(operation_id),
        )
    except OperationNotFound as exc:
        raise _not_found() from exc
    return OperationRead.model_validate(operation)


@router.get(
    "/approvals",
    response_model=ApprovalList,
    summary="List approval requests for the current tenant",
)
def list_approvals_endpoint(
    limit: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
    state: str | None = Query(default=None, max_length=16),
    resource_id: UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_tenant_admin),
    db: Session = Depends(get_tenant_db),
) -> ApprovalList:
    items, total = list_approvals(
        db,
        tenant_id=ctx.tenant_id,
        limit=limit,
        offset=offset,
        state=state,
        resource_id=str(resource_id) if resource_id is not None else None,
    )
    return ApprovalList(
        items=[ApprovalRead.model_validate(item) for item in items],
        total=total,
    )


@router.get(
    "/approvals/{approval_id}",
    response_model=ApprovalRead,
    summary="Get an approval request for the current tenant",
)
def get_approval_endpoint(
    approval_id: UUID,
    ctx: TenantContext = Depends(require_tenant_admin),
    db: Session = Depends(get_tenant_db),
) -> ApprovalRead:
    try:
        approval = get_approval(
            db,
            tenant_id=ctx.tenant_id,
            approval_id=str(approval_id),
        )
    except ApprovalNotFound as exc:
        raise _not_found() from exc
    return ApprovalRead.model_validate(approval)


@router.get(
    "/audit/events",
    response_model=AuditEventList,
    summary="List append-only audit events (tenant admin only)",
)
def list_audit_events_endpoint(
    limit: int = Query(default=50, ge=1, le=_MAX_PAGE_SIZE),
    offset: int = Query(default=0, ge=0, le=_MAX_OFFSET),
    action: str | None = Query(default=None, max_length=100),
    resource_id: UUID | None = Query(default=None),
    ctx: TenantContext = Depends(require_tenant_admin),
    db: Session = Depends(get_tenant_db),
) -> AuditEventList:
    items, total = list_audit_events(
        db,
        tenant_id=ctx.tenant_id,
        limit=limit,
        offset=offset,
        action=action,
        resource_id=str(resource_id) if resource_id is not None else None,
    )
    return AuditEventList(
        items=[AuditEventRead.model_validate(item) for item in items],
        total=total,
    )


__all__ = ["router"]
