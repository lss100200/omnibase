"""Health check endpoints.

GET /health        - Lightweight liveness probe (always 200 if process is up)
GET /health/ready  - Deep readiness probe (checks DB, MinIO, Redis connections)

Design rationale:
- Liveness and readiness are separated: orchestrators (k8s, docker compose)
  use liveness to know "should this be restarted" and readiness to know
  "should this receive traffic".
- For Phase 0 we keep them on the same path; Phase 2 may split when we have
  a real orchestrator.
"""

from __future__ import annotations

import time
from typing import Any, Literal

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from omnibase import __version__
from omnibase.api.health_schemas import ComponentStatus, HealthResponse
from omnibase.core.config import Settings, get_settings
from omnibase.core.logging import get_logger

OverallStatus = Literal["ok", "fail", "degraded"]

router = APIRouter(prefix="/health", tags=["health"])
log = get_logger(__name__)


# -----------------------------------------------------------
# Lightweight probes (no external dependencies)
# -----------------------------------------------------------
@router.get(
    "",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Liveness probe",
    description="Returns 200 as long as the process is running. Does not check dependencies.",
)
async def liveness(
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    """Liveness check - process is alive."""
    return HealthResponse(
        status="ok",
        version=__version__,
        env=settings.env.value,
        components={},
    )


# -----------------------------------------------------------
# Deep readiness probe
# -----------------------------------------------------------
@router.get(
    "/ready",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Readiness probe",
    description="Checks DB, MinIO, Redis connections. Returns 200 only if all are reachable.",
)
async def readiness(
    settings: Settings = Depends(get_settings),
) -> HealthResponse | JSONResponse:
    """Readiness check - all dependencies reachable."""
    components: dict[str, ComponentStatus] = {}

    # Probe each dependency in parallel-friendly fashion.
    # Phase 0 keeps them sequential for simplicity; Phase 1 can use asyncio.gather.
    components["database"] = await _probe_database(settings)
    components["minio"] = await _probe_minio(settings)
    components["redis"] = await _probe_redis(settings)

    # Aggregate
    failing = [k for k, v in components.items() if v.status == "fail"]
    degraded = [k for k, v in components.items() if v.status == "degraded"]

    if failing:
        overall: OverallStatus = "fail"
    elif degraded:
        overall = "degraded"
    else:
        overall = "ok"

    http_status = status.HTTP_200_OK if overall == "ok" else status.HTTP_503_SERVICE_UNAVAILABLE

    response = HealthResponse(
        status=overall,
        version=__version__,
        env=settings.env.value,
        components=components,
    )

    # Use JSONResponse to control status code (response_model still validates body)
    return JSONResponse(
        status_code=http_status,
        content=response.model_dump(mode="json"),
    )


# -----------------------------------------------------------
# Probe implementations
# -----------------------------------------------------------
async def _probe_database(settings: Settings) -> ComponentStatus:
    """Probe PostgreSQL via SQLAlchemy."""
    start = time.monotonic()
    try:
        # Lazy import to avoid hard dependency at module load time
        from sqlalchemy import text

        from omnibase.core.db import get_engine

        engine = get_engine(settings)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        latency_ms = (time.monotonic() - start) * 1000.0
        log.debug("health.db.ok", latency_ms=latency_ms)
        return ComponentStatus(status="ok", latency_ms=round(latency_ms, 2))
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000.0
        log.warning("health.db.fail", error=str(exc), latency_ms=latency_ms)
        return ComponentStatus(status="fail", latency_ms=round(latency_ms, 2))


async def _probe_minio(settings: Settings) -> ComponentStatus:
    """Probe MinIO bucket list."""
    start = time.monotonic()
    try:
        from omnibase.storage.minio_client import get_minio_client

        client = get_minio_client(settings)
        # bucket_exists is a cheap RPC; perfect for health check
        exists = client.bucket_exists(settings.minio_bucket)
        latency_ms = (time.monotonic() - start) * 1000.0
        if not exists:
            log.warning("health.minio.bucket_missing", bucket=settings.minio_bucket)
            return ComponentStatus(
                status="degraded",
                detail="Configured bucket is unavailable",
                latency_ms=round(latency_ms, 2),
            )
        log.debug("health.minio.ok", latency_ms=latency_ms)
        return ComponentStatus(status="ok", latency_ms=round(latency_ms, 2))
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000.0
        log.warning("health.minio.fail", error=str(exc), latency_ms=latency_ms)
        return ComponentStatus(status="fail", latency_ms=round(latency_ms, 2))


async def _probe_redis(settings: Settings) -> ComponentStatus:
    """Probe Redis PING."""
    start = time.monotonic()
    try:
        # Lazy import; redis.asyncio avoids blocking the event loop
        from redis.asyncio import Redis

        redis_client: Any = Redis.from_url(
            str(settings.redis_url),
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2.0,
            socket_timeout=2.0,
        )
        try:
            pong = await redis_client.ping()
            latency_ms = (time.monotonic() - start) * 1000.0
            if not pong:
                return ComponentStatus(
                    status="fail",
                    detail="Dependency health check failed",
                    latency_ms=round(latency_ms, 2),
                )
            log.debug("health.redis.ok", latency_ms=latency_ms)
            return ComponentStatus(status="ok", latency_ms=round(latency_ms, 2))
        finally:
            await redis_client.aclose()
    except Exception as exc:
        latency_ms = (time.monotonic() - start) * 1000.0
        log.warning("health.redis.fail", error=str(exc), latency_ms=latency_ms)
        return ComponentStatus(status="fail", latency_ms=round(latency_ms, 2))
