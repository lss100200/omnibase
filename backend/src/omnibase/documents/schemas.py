"""Documents request/response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DocumentStatus = Literal["pending", "queued", "processing", "indexed", "failed"]

# Valid transition targets from each state (for runtime validation).
# The worker sets processing->indexed or processing->failed.
# The upload endpoint sets pending->queued.
_LIFECYCLE: dict[str, tuple[str, ...]] = {
    "pending": ("queued",),
    "queued": ("processing",),
    "processing": ("indexed", "failed"),
    "failed": ("queued",),  # retry: back to processing via queued
    "indexed": ("queued",),  # re-index
}


class DocumentRead(BaseModel):
    """Public document representation."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    mime_type: str
    size_bytes: int
    status: DocumentStatus
    page_count: int | None = None
    error_detail: str | None = Field(
        default=None,
        description="Safe failure detail (truncated to 1000 chars). Null when no error.",
    )
    meta: dict[str, Any] = Field(
        default_factory=dict,
        description="Document metadata (RAG chunks, parse info, etc.)",
    )
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    """Paginated document list."""

    items: list[DocumentRead]
    total: int


class DocumentUploadResponse(BaseModel):
    """Response immediately after upload (status='queued' if enqueued successfully)."""

    document: DocumentRead
    message: str = "File uploaded and queued for ingestion."


class DocumentDownloadURL(BaseModel):
    """Presigned download URL (short-lived)."""

    url: str
    expires_in_seconds: int
    filename: str


class DocumentDeleteResponse(BaseModel):
    """Confirmation after delete."""

    id: str
    deleted: bool = True


class DocumentErrorResponse(BaseModel):
    """Standard error envelope."""

    error: dict[str, str]


__all__ = [
    "_LIFECYCLE",
    "DocumentDeleteResponse",
    "DocumentDownloadURL",
    "DocumentErrorResponse",
    "DocumentList",
    "DocumentRead",
    "DocumentStatus",
    "DocumentUploadResponse",
]
