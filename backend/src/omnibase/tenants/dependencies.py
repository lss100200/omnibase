"""Tenant-aware dependencies for FastAPI.

Provides:
- TenantContext: dataclass holding the resolved tenant for the current request
- get_current_tenant: dependency that resolves the tenant from the JWT
- get_tenant_db: dependency that yields a Session scoped to the tenant's schema

Resolution flow:
    Authorization: Bearer <jwt>
        -> decode JWT (B4 will add real verification)
        -> extract tenant_id
        -> look up Tenant row
        -> set search_path on the session

Phase 0 simplification:
- Auth is stubbed; we trust the JWT payload without signature verification.
- B4 shipped real JWT verification via omnibase.auth.security.decode_access_token
"""

from __future__ import annotations

import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import structlog
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from omnibase.auth.security import TokenPayload
from omnibase.core.config import Settings, get_settings
from omnibase.core.db import (
    TENANT_CONTEXT_REQUIRED_SESSION_KEY,
    TENANT_SCHEMA_SESSION_KEY,
    get_session_factory,
)
from omnibase.core.logging import get_logger
from omnibase.db.models import Tenant
from omnibase.db.tenant import User
from omnibase.tenants.context import reset_schema, set_current_schema
from omnibase.tenants.schema_manager import SchemaError, validate_schema_name
from omnibase.tenants.service import get_tenant_by_id

log = get_logger(__name__)


@dataclass(frozen=True)
class TenantContext:
    """Resolved tenant context for the current request."""

    tenant: Tenant
    user: User | None = None

    @property
    def tenant_id(self) -> str:
        return str(self.tenant.id)

    @property
    def schema_name(self) -> str:
        return self.tenant.schema_name

    @property
    def user_id(self) -> str:
        return str(self.user.id) if self.user is not None else "unknown-user"


@dataclass(frozen=True)
class CurrentPrincipal:
    """Active database-backed user and tenant resolved for a request."""

    tenant: Tenant
    user: User
    token: TokenPayload

    @property
    def tenant_id(self) -> str:
        return str(self.tenant.id)

    @property
    def schema_name(self) -> str:
        return self.tenant.schema_name

    @property
    def user_id(self) -> str:
        return str(self.user.id)


# -----------------------------------------------------------
# JWT verification (real - replaced the Phase 0 bootstrap stub)
# -----------------------------------------------------------
def _extract_access_payload(authorization: str | None) -> TokenPayload:
    """Decode a signed access token from the Authorization header."""
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
        )
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization must be 'Bearer <token>'",
        )
    token = authorization.removeprefix("Bearer ").strip()

    # Real verification: signature + expiration + typ='access'
    from omnibase.auth.security import (
        TokenExpired,
        TokenInvalid,
        decode_access_token,
    )

    try:
        payload = decode_access_token(token)
    except TokenExpired as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token expired: {exc}",
        ) from exc
    except TokenInvalid as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc

    if not payload.tenant_id or not payload.sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="JWT missing required identity claims",
        )
    return payload


# -----------------------------------------------------------
# Dependencies
# -----------------------------------------------------------
def require_platform_admin(
    x_platform_admin_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Require explicitly enabled platform administration and its shared token."""
    expected = settings.platform_admin_token
    if not settings.tenant_management_api_enabled or not expected:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if not x_platform_admin_token or not secrets.compare_digest(x_platform_admin_token, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform administrator access required",
        )


async def get_current_principal(
    authorization: str | None = Header(default=None),
) -> AsyncIterator[CurrentPrincipal]:
    """Resolve an active user from the registry tenant, never from schema claims.

    Valid access tokens are necessary but not sufficient: every request also
    verifies that the tenant and user remain active and reads the current role
    from the tenant database. This makes account disablement and RBAC changes
    effective without waiting for access-token expiration.
    """
    payload = _extract_access_payload(authorization)
    try:
        tenant = get_tenant_by_id(payload.tenant_id)
    except Exception as exc:
        log.warning(
            "tenant.resolve_failed",
            tenant_id=payload.tenant_id,
            error_type=type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant not found or inactive",
        ) from exc

    try:
        validate_schema_name(tenant.schema_name)
    except SchemaError as exc:
        log.error("tenant.invalid_schema", schema=tenant.schema_name, error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Tenant has an invalid schema configuration",
        ) from exc

    context_token = set_current_schema(tenant.schema_name)
    try:
        user = await run_in_threadpool(
            _load_active_user,
            tenant.schema_name,
            payload.sub,
        )

        if user is None:
            log.warning(
                "principal.user_inactive_or_missing",
                tenant_id=str(tenant.id),
                user_id=payload.sub,
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found or inactive",
            )

        structlog.contextvars.bind_contextvars(
            tenant_id=str(tenant.id),
            user_id=str(user.id),
        )
        yield CurrentPrincipal(tenant=tenant, user=user, token=payload)
    finally:
        reset_schema(context_token)


def _load_active_user(schema_name: str, user_id: str) -> User | None:
    """Read the current user in a worker thread so auth never blocks the event loop."""
    session = get_session_factory()()
    session.info[TENANT_SCHEMA_SESSION_KEY] = schema_name
    session.info[TENANT_CONTEXT_REQUIRED_SESSION_KEY] = True
    try:
        stmt = select(User).where(
            User.id == user_id,
            User.is_active.is_(True),
        )
        return session.execute(stmt).scalar_one_or_none()
    finally:
        session.close()


def get_current_tenant(
    principal: CurrentPrincipal = Depends(get_current_principal),
) -> TenantContext:
    """Compatibility dependency exposing the verified tenant and active user."""
    return TenantContext(tenant=principal.tenant, user=principal.user)


def require_tenant_admin(
    ctx: TenantContext = Depends(get_current_tenant),
) -> TenantContext:
    """Require the current database-backed role to be tenant administrator."""
    if ctx.user is None or not ctx.user.is_active or not ctx.user.is_tenant_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant administrator access required",
        )
    return ctx


def get_tenant_db(
    ctx: TenantContext = Depends(get_current_tenant),
) -> Iterator[Session]:
    """Yield a Session explicitly bound to the resolved tenant schema."""
    validate_schema_name(ctx.schema_name)
    session = get_session_factory()()
    session.info[TENANT_SCHEMA_SESSION_KEY] = ctx.schema_name
    session.info[TENANT_CONTEXT_REQUIRED_SESSION_KEY] = True
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


__all__ = [
    "CurrentPrincipal",
    "TenantContext",
    "get_current_principal",
    "get_current_tenant",
    "get_tenant_db",
    "require_platform_admin",
    "require_tenant_admin",
]
