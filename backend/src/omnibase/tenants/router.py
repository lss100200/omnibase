"""Tenant router - HTTP endpoints for tenant management.

Phase 0 scope:
- POST /api/tenants                : create a new tenant (admin-only later)
- GET  /api/tenants                : list tenants (admin-only later)
- GET  /api/tenants/{slug}         : fetch a tenant by slug
- DELETE /api/tenants/{id}         : deactivate a tenant (soft delete)

Auth: Phase 0 keeps endpoints open for bootstrap (we'll add auth in B4).
When B4 lands, these routes will require an admin JWT.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from omnibase.core.logging import get_logger
from omnibase.tenants.dependencies import require_platform_admin
from omnibase.tenants.schemas import TenantCreate, TenantList, TenantRead
from omnibase.tenants.service import (
    InvalidTenantSlug,
    TenantAlreadyExists,
    TenantError,
    TenantNotFound,
    create_tenant,
    deactivate_tenant,
    get_tenant_by_id,
    get_tenant_by_slug,
)

router = APIRouter(
    prefix="/tenants",
    tags=["tenants"],
    dependencies=[Depends(require_platform_admin)],
)
log = get_logger(__name__)


@router.post(
    "",
    response_model=TenantRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new tenant",
    description=(
        "Creates a tenant record and its backing PostgreSQL schema. "
        "Business tables (users, documents) are auto-applied to the new schema."
    ),
)
def create_tenant_endpoint(payload: TenantCreate) -> TenantRead:
    """Create tenant."""
    try:
        tenant = create_tenant(name=payload.name, slug=payload.slug)
    except InvalidTenantSlug as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid slug: {exc}",
        ) from exc
    except TenantAlreadyExists as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except TenantError as exc:
        log.error("tenant.create_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create tenant",
        ) from exc
    return TenantRead.model_validate(tenant)


@router.get(
    "",
    response_model=TenantList,
    summary="List tenants",
    description="Returns all active tenants. Pagination via limit/offset.",
)
def list_tenants_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TenantList:
    """List tenants (Phase 0: no auth, admin filter added in B4)."""
    from sqlalchemy import func, select

    from omnibase.core.db import get_session_factory
    from omnibase.db.models import Tenant

    session = get_session_factory()()
    try:
        stmt = (
            select(Tenant)
            .where(Tenant.is_active.is_(True))
            .order_by(Tenant.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = [TenantRead.model_validate(t) for t in session.execute(stmt).scalars()]
        total_stmt = select(func.count()).select_from(Tenant).where(Tenant.is_active.is_(True))
        total = int(session.execute(total_stmt).scalar() or 0)
    finally:
        session.close()
    return TenantList(items=items, total=total)


@router.get(
    "/by-slug/{slug}",
    response_model=TenantRead,
    summary="Get a tenant by slug",
)
def get_tenant_by_slug_endpoint(slug: str) -> TenantRead:
    """Fetch a tenant by slug."""
    try:
        tenant = get_tenant_by_slug(slug)
    except TenantNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return TenantRead.model_validate(tenant)


@router.get(
    "/{tenant_id}",
    response_model=TenantRead,
    summary="Get a tenant by id",
)
def get_tenant_by_id_endpoint(tenant_id: str) -> TenantRead:
    """Fetch a tenant by id."""
    try:
        tenant = get_tenant_by_id(tenant_id)
    except TenantNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return TenantRead.model_validate(tenant)


@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_200_OK,
    summary="Deactivate a tenant (soft delete)",
    description=(
        "Marks the tenant as inactive. The PostgreSQL schema is preserved for "
        "data recovery; hard delete requires DBA intervention."
    ),
)
def deactivate_tenant_endpoint(tenant_id: str) -> dict:
    """Soft-delete tenant."""
    try:
        deactivate_tenant(tenant_id)
    except TenantNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return {"id": tenant_id, "deactivated": True}


__all__ = ["router"]
