"""Independent ASGI application for the non-browser Capability Gateway."""

from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from omnibase.capability_gateway.adapters import (
    CanonicalRagReadAdapter,
    PostgresDataReadAdapter,
    UnavailableDataReadAdapter,
    UnavailableRagReadAdapter,
)
from omnibase.capability_gateway.audit import ControlPlaneGatewayAuditSink
from omnibase.capability_gateway.query import CursorCodec
from omnibase.capability_gateway.resolver import RegistryResourceResolver
from omnibase.capability_gateway.router import router
from omnibase.capability_gateway.security import (
    CoreCapabilityVerifier,
    RejectingCapabilityVerifier,
    RejectingWorkloadAttestor,
    WorkloadAttestor,
)
from omnibase.capability_gateway.service import GatewayComponents, GatewayFailure, GatewayService
from omnibase.capability_gateway.workload import SqlAlchemyRunLeaseWorkloadAttestor
from omnibase.core.config import get_settings
from omnibase.core.middleware import RequestBodyLimitMiddleware, RequestContextMiddleware

_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class GatewayRequestIdMiddleware:
    """Generate once, inject into scope, and let shared middleware reuse it."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = None
        headers = []
        for key, value in scope.get("headers", []):
            if key.lower() == b"x-request-id":
                candidate = value.decode("latin-1").strip()
                if _REQUEST_ID.fullmatch(candidate):
                    request_id = candidate
                continue
            headers.append((key, value))
        request_id = request_id or str(uuid4())
        headers.append((b"x-request-id", request_id.encode("ascii")))
        scope["headers"] = headers
        scope.setdefault("state", {})["request_id"] = request_id
        await self.app(scope, receive, send)


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def create_gateway_app(
    components: GatewayComponents | None = None,
    *,
    workload_attestor: WorkloadAttestor | None = None,
    cursor_secret: bytes | None = None,
) -> FastAPI:
    """Create the isolated app.

    The default attestor and verifier deliberately reject every request. This
    factory is not production-usable until the deployment injects a trusted
    mTLS/runner attestor and the core capability verifier.
    """
    settings = get_settings()
    app = FastAPI(
        title="OmniBase Capability Gateway",
        version="1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.is_development else None,
    )
    app.add_middleware(
        RequestBodyLimitMiddleware,
        max_body_size=min(settings.max_request_body_size_bytes, 2 * 1024 * 1024),
    )
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(GatewayRequestIdMiddleware)
    if components is None:
        resolver = RegistryResourceResolver()
        data_adapter = (
            PostgresDataReadAdapter(resolver, CursorCodec(cursor_secret))
            if cursor_secret is not None
            else UnavailableDataReadAdapter()
        )
        components = GatewayComponents(
            verifier=RejectingCapabilityVerifier(),
            resolver=resolver,
            data_adapter=data_adapter,
            rag_adapter=(
                CanonicalRagReadAdapter(resolver)
                if cursor_secret is not None
                else UnavailableRagReadAdapter()
            ),
            audit_sink=ControlPlaneGatewayAuditSink(),
        )
    app.state.gateway_service = GatewayService(components)
    app.state.capability_verifier = components.verifier
    app.state.workload_attestor = workload_attestor or RejectingWorkloadAttestor()
    app.include_router(router)

    @app.exception_handler(GatewayFailure)
    async def gateway_failure_handler(request: Request, exc: GatewayFailure) -> JSONResponse:
        del request
        return _error(exc.status_code, exc.code, exc.message)

    @app.exception_handler(HTTPException)
    async def http_failure_handler(request: Request, exc: HTTPException) -> JSONResponse:
        del request
        detail: Any = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            return JSONResponse(status_code=exc.status_code, content=detail, headers=exc.headers)
        return _error(exc.status_code, "request_rejected", "Request rejected")

    @app.exception_handler(RequestValidationError)
    async def validation_failure_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request, exc
        # Validation details can echo attacker-controlled field names/values.
        return _error(422, "validation_error", "Request validation failed")

    return app


def create_production_gateway_app(
    *,
    workload_attestor: SqlAlchemyRunLeaseWorkloadAttestor,
    cursor_secret: bytes,
) -> FastAPI:
    """Compose the real read-only Gateway behind a trusted mTLS attestor.

    The caller must provide the workload attestor explicitly.  The function
    never creates a permissive fallback and never mounts the Gateway into the
    Browser application.
    """
    if not isinstance(workload_attestor, SqlAlchemyRunLeaseWorkloadAttestor):
        raise ValueError("production Gateway requires the live Run Lease workload attestor")
    if not isinstance(cursor_secret, bytes) or len(cursor_secret) < 32:
        raise ValueError("cursor_secret must contain at least 32 bytes")
    resolver = RegistryResourceResolver()
    components = GatewayComponents(
        verifier=CoreCapabilityVerifier(),
        resolver=resolver,
        data_adapter=PostgresDataReadAdapter(resolver, CursorCodec(cursor_secret)),
        rag_adapter=CanonicalRagReadAdapter(resolver),
        audit_sink=ControlPlaneGatewayAuditSink(),
    )
    return create_gateway_app(
        components,
        workload_attestor=workload_attestor,
        cursor_secret=cursor_secret,
    )


__all__ = ["create_gateway_app", "create_production_gateway_app"]
