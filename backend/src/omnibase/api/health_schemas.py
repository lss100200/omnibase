"""Health check schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ComponentStatus(BaseModel):
    """Status of a single infrastructure component."""

    status: Literal["ok", "fail", "degraded"] = Field(
        ..., description="Whether the component is reachable."
    )
    detail: str | None = Field(default=None, description="Optional detail (e.g. error message).")
    latency_ms: float | None = Field(
        default=None,
        description="Round-trip latency to the component in milliseconds.",
    )


class HealthResponse(BaseModel):
    """Aggregated health response returned by GET /health."""

    status: Literal["ok", "fail", "degraded"] = Field(..., description="Aggregated service status.")
    version: str = Field(..., description="Application version.")
    env: str = Field(..., description="Deployment environment.")
    components: dict[str, ComponentStatus] = Field(
        default_factory=dict,
        description="Status of each infrastructure dependency.",
    )
