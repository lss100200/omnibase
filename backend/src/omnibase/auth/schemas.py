"""Auth request / response Pydantic schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


# -----------------------------------------------------------
# Request schemas
# -----------------------------------------------------------
class RegisterRequest(BaseModel):
    """POST /api/auth/register.

    Phase 0: registration auto-creates a default tenant for the user.
    Multi-tenant membership (joining an existing tenant) arrives in Phase 2.
    """

    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    # Optional: pre-existing tenant slug to join (Phase 2; ignored in Phase 0)
    tenant_name: str | None = Field(
        default=None,
        max_length=100,
        description="Display name for the auto-created tenant (default: derived from email)",
    )


class LoginRequest(BaseModel):
    """POST /api/auth/login."""

    email: EmailStr
    password: str = Field(..., min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    """POST /api/auth/refresh."""

    refresh_token: str = Field(..., min_length=10)


# -----------------------------------------------------------
# Response schemas
# -----------------------------------------------------------
class TokenResponse(BaseModel):
    """Successful auth response containing the token pair + user info."""

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(..., description="Access token TTL in seconds")
    user: UserPublic
    tenant: TenantPublic

    model_config = ConfigDict(from_attributes=True)


class UserPublic(BaseModel):
    """Public user representation (no password_hash)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    is_tenant_admin: bool
    created_at: datetime


class TenantPublic(BaseModel):
    """Public tenant representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str


class RefreshResponse(BaseModel):
    """Response for /auth/refresh (new access token only)."""

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class AuthErrorResponse(BaseModel):
    """Standard error envelope for auth endpoints."""

    error: dict[str, str] = Field(..., description="{code, message}")


# Forward-ref resolution (TokenResponse references UserPublic / TenantPublic)
TokenResponse.model_rebuild()


__all__ = [
    "AuthErrorResponse",
    "LoginRequest",
    "RefreshRequest",
    "RefreshResponse",
    "RegisterRequest",
    "TenantPublic",
    "TokenResponse",
    "UserPublic",
]
