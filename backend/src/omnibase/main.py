"""FastAPI application entry point.

Wire-up:
- Lifespan: configure logging, run startup checks, teardown gracefully
- Middleware: CORS
- Routers: /health
- Exception handlers: unified error format

Phase 0 keeps things minimal. Auth / documents / database routers will be
mounted in their respective phases (B4 / B5 / C5).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from omnibase import __version__
from omnibase.api.health import router as health_router
from omnibase.auth.router import router as auth_router
from omnibase.control_plane.router import router as control_plane_router
from omnibase.controlled_data.router import router as controlled_data_router
from omnibase.core.config import get_settings
from omnibase.core.logging import configure_logging, get_logger
from omnibase.core.middleware import RequestBodyLimitMiddleware, RequestContextMiddleware
from omnibase.database.router import router as database_router
from omnibase.documents.router import router as documents_router
from omnibase.rag.router import router as rag_router
from omnibase.tenants.router import router as tenants_router
from omnibase.workspaces.router import router as workspaces_router
from omnibase.workspaces.router import template_router as workspace_templates_router


# -----------------------------------------------------------
# Lifespan (modern FastAPI startup/shutdown)
# -----------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Application startup and shutdown lifecycle."""
    settings = get_settings()
    configure_logging()
    log = get_logger("omnibase.lifespan")

    log.info(
        "app.starting",
        app=settings.app_name,
        version=__version__,
        env=settings.env.value,
    )

    # Fail-fast on missing critical config (Pydantic already validates types,
    # this is a belt-and-suspenders check for production secrets).
    if settings.is_production and settings.jwt_secret.startswith("please_"):
        log.error("app.startup_aborted", reason="default_jwt_secret_in_production")
        raise RuntimeError(
            "JWT_SECRET must be set to a strong random value in production. "
            "See .env.example for generation instructions."
        )

    # Pre-warm DB engine (creates pool; fails fast if DB unreachable)
    try:
        from sqlalchemy import text

        from omnibase.core.db import get_engine

        engine = get_engine(settings)
        # Ensure the global omnibase_meta schema exists before any tenant query.
        # Idempotent - safe to run on every startup.
        with engine.begin() as conn:
            conn.execute(text('CREATE SCHEMA IF NOT EXISTS "omnibase_meta"'))
            # Enable pgvector extension in public schema (must run before any
            # tenant schema tries to use the vector type)
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public"))
        log.info("app.db_engine_ready")
    except Exception as exc:
        # In development we tolerate DB being down at startup (e.g. running
        # tests without docker compose). In production we fail hard.
        if settings.is_production:
            raise
        log.warning("app.db_engine_failed_at_startup", error=str(exc))

    # Pre-warm MinIO client and ensure bucket exists
    try:
        from omnibase.storage.minio_client import ensure_bucket_exists

        if ensure_bucket_exists():
            log.info("app.minio_ready", bucket=settings.minio_bucket)
    except Exception as exc:
        if settings.is_production:
            raise
        log.warning("app.minio_failed_at_startup", error=str(exc))

    log.info("app.started", host="0.0.0.0", port=8000)

    yield

    # -----------------------------------------------------------
    # Shutdown
    # -----------------------------------------------------------
    log.info("app.stopping")
    try:
        from omnibase.core.db import dispose_engines

        dispose_engines()
    except Exception as exc:
        log.warning("app.shutdown_db_dispose_failed", error=str(exc))
    log.info("app.stopped")


# -----------------------------------------------------------
# Application factory
# -----------------------------------------------------------
def _status_to_code(status_code: int) -> str:
    """Map HTTP status codes to short machine-readable error codes.

    Used by the unified exception handler when an HTTPException is raised
    with a plain string detail (no envelope).
    """
    mapping = {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        405: "method_not_allowed",
        409: "conflict",
        413: "payload_too_large",
        415: "unsupported_media_type",
        422: "validation_error",
        429: "rate_limited",
        500: "internal_server_error",
        502: "bad_gateway",
        503: "service_unavailable",
        504: "gateway_timeout",
    }
    return mapping.get(status_code, f"status_{status_code}")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()
    configure_logging()
    log = get_logger("omnibase.app")

    docs_enabled = settings.is_development
    docs_url = "/docs" if docs_enabled else None
    redoc_url = "/redoc" if docs_enabled else None
    openapi_url = "/openapi.json" if docs_enabled else None

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        description=(
            "Self-hosted, AI-native personal knowledge workbench. "
            "Database-first foundation with built-in RAG, multi-agent "
            "orchestration, and Skill/MCP extension ecosystem."
        ),
        docs_url=docs_url,
        redoc_url=redoc_url,
        openapi_url=openapi_url,
        lifespan=lifespan,
        swagger_ui_parameters={"docExpansion": "none", "persistAuthorization": False},
    )

    # -----------------------------------------------------------
    # CORS
    # -----------------------------------------------------------
    # Starlette wraps middleware in reverse registration order. Register the
    # body guard first, then CORS, and request context last so every response
    # (including CORS/body-limit rejections) receives a request ID and access log.
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_size=settings.max_request_body_size_bytes,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_strings,
        allow_credentials=True,
        allow_methods=list(settings.cors_allow_methods),
        allow_headers=list(settings.cors_allow_headers),
        expose_headers=["X-Total-Count", "X-Request-Id"],
    )
    app.add_middleware(RequestContextMiddleware)

    # -----------------------------------------------------------
    # Routers
    # -----------------------------------------------------------
    api_router = APIRouter(prefix="/api/v1")
    api_router.include_router(health_router)
    api_router.include_router(auth_router)
    api_router.include_router(tenants_router)
    api_router.include_router(documents_router)
    api_router.include_router(database_router)
    api_router.include_router(rag_router)
    api_router.include_router(control_plane_router)
    api_router.include_router(workspace_templates_router)
    api_router.include_router(workspaces_router)
    api_router.include_router(controlled_data_router)

    # Convenience: /health at root too (without /api prefix) for simple probes
    app.include_router(health_router)
    app.include_router(api_router)

    # -----------------------------------------------------------
    # Exception handlers (unified error format)
    # -----------------------------------------------------------
    # Order matters: more specific handlers must be registered before the
    # generic Exception handler so they take precedence.

    from fastapi import HTTPException as _FastAPIHTTPException
    from fastapi.exceptions import RequestValidationError as _RequestValidationError

    @app.exception_handler(_FastAPIHTTPException)
    async def http_exception_handler(request: Request, exc: _FastAPIHTTPException) -> JSONResponse:
        """Normalize HTTPException into our {error: {code, message, details?}} envelope.

        Routes that raise HTTPException(detail={"error": {...}}) pass through
        unchanged; routes that use the legacy (str detail) form get wrapped.
        """
        # exc.detail is typed as str | None by FastAPI stubs, but at runtime
        # it can be a dict (we raise HTTPException with dict detail). Cast to Any.
        detail: Any = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            # Already in our envelope - pass through
            content = detail
        elif isinstance(detail, dict):
            # Structured but non-envelope detail - wrap minimally
            content = {"error": {"code": "error", "message": str(detail), "details": detail}}
        else:
            # Plain string or None - synthesize envelope
            code = _status_to_code(exc.status_code)
            content = {"error": {"code": code, "message": str(detail) if detail else "Error"}}

        log.info(
            "app.http_exception",
            path=request.url.path,
            method=request.method,
            status_code=exc.status_code,
            error=content["error"].get("code"),
        )
        return JSONResponse(
            status_code=exc.status_code,
            content=content,
            headers=exc.headers,
        )

    @app.exception_handler(_RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: _RequestValidationError
    ) -> JSONResponse:
        """Normalize Pydantic validation errors into our envelope."""
        content = {
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "details": exc.errors(),
            }
        }
        log.info(
            "app.validation_error",
            path=request.url.path,
            method=request.method,
            error_count=len(exc.errors()),
        )
        return JSONResponse(status_code=422, content=content)

    # NOTE: We intentionally do NOT register a handler for bare `Exception`.
    # Starlette's ServerErrorMiddleware already catches unhandled exceptions
    # and returns 500. Registering `Exception` here would shadow
    # `RequestValidationError` and other FastAPI internal exception types
    # that need to be handled by their specific handlers.
    # Use middleware-level error logging instead (see configure_logging).

    # -----------------------------------------------------------
    # Root route (informational only) - inside create_app so tests see it
    # -----------------------------------------------------------
    @app.get("/", tags=["root"], include_in_schema=False)
    async def root() -> dict[str, Any]:
        """Service banner / quick links."""
        response: dict[str, Any] = {
            "name": settings.app_name,
            "version": __version__,
            "health": "/health",
            "readiness": "/health/ready",
        }
        if docs_enabled:
            response["docs"] = "/docs"
        return response

    log.info("app.configured", routes=len(app.routes))
    return app


# -----------------------------------------------------------
# Module-level app instance (for uvicorn: omnibase.main:app)
# -----------------------------------------------------------
app = create_app()
