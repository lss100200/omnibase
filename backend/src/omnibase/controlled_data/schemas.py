"""Public, logical-only DTOs for the P34.3 user controlled-write API."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from omnibase.controlled_data.crud_contracts import (
    DeleteMutationRequest,
    InsertMutationRequest,
    UpdateMutationRequest,
)


class ControlledWriteRequest(BaseModel):
    """Closed HTTP envelope; the mutation itself only contains logical UUIDs."""

    model_config = ConfigDict(extra="forbid")

    mutation: InsertMutationRequest | UpdateMutationRequest | DeleteMutationRequest = Field(
        discriminator="kind"
    )


class ControlledWriteResponse(BaseModel):
    """Minimal result that never serializes registry or physical locator state."""

    model_config = ConfigDict(extra="forbid")

    operation_id: UUID
    resource_id: UUID
    resource_version: int
    action: Literal["data.rows.insert", "data.rows.update", "data.rows.delete"]
    affected_rows: int
    replayed: bool
    request_id: str


class ControlledWriteErrorDetail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    request_id: str | None = None
    retryable: bool | None = None


class ControlledWriteErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ControlledWriteErrorDetail


__all__ = [
    "ControlledWriteErrorDetail",
    "ControlledWriteErrorResponse",
    "ControlledWriteRequest",
    "ControlledWriteResponse",
]
