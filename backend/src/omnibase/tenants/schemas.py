"""Tenant Pydantic schemas (request / response models)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# -----------------------------------------------------------
# Request schemas
# -----------------------------------------------------------
class TenantCreate(BaseModel):
    """Payload for POST /api/tenants."""

    name: str = Field(..., min_length=1, max_length=100, description="Display name")
    slug: str | None = Field(
        default=None,
        min_length=3,
        max_length=50,
        description="URL-safe slug; auto-generated if omitted",
    )


# -----------------------------------------------------------
# Response schemas
# -----------------------------------------------------------
class TenantRead(BaseModel):
    """Public tenant representation (returned by GET / POST)."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    slug: str
    is_default: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class TenantList(BaseModel):
    """Paginated tenant list."""

    items: list[TenantRead]
    total: int


# -----------------------------------------------------------
# Error schemas
# -----------------------------------------------------------
class TenantErrorResponse(BaseModel):
    """Standard error envelope for tenant endpoints."""

    error: dict[str, Any] = Field(..., description="{code, message, details?}")


__all__ = ["TenantCreate", "TenantErrorResponse", "TenantList", "TenantRead"]
