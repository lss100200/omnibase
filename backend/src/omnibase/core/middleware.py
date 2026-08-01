"""Pure-ASGI middleware for request boundaries and observability."""

from __future__ import annotations

import re
import time
from uuid import uuid4

import structlog
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from omnibase.core.logging import get_logger

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


class RequestBodyTooLarge(Exception):
    """Raised when a streamed request body exceeds the configured ceiling."""


def _request_id_from_scope(scope: Scope) -> str:
    for key, value in scope.get("headers", []):
        if key.lower() == b"x-request-id":
            candidate = value.decode("latin-1").strip()
            if _REQUEST_ID_PATTERN.fullmatch(candidate):
                return candidate
            break
    return str(uuid4())


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _declared_length_error(scope: Scope, max_body_size: int) -> JSONResponse | None:
    """Validate Content-Length without trusting it as the only body boundary."""
    headers = {key.lower(): value for key, value in scope.get("headers", [])}
    raw_value = headers.get(b"content-length")
    if raw_value is None:
        return None
    try:
        declared_size = int(raw_value)
    except ValueError:
        declared_size = -1
    if declared_size < 0:
        return _error_response(
            400,
            "invalid_content_length",
            "Content-Length must be a non-negative integer",
        )
    if declared_size > max_body_size:
        return _error_response(
            413,
            "payload_too_large",
            "Request body exceeds the configured size limit",
        )
    return None


class RequestBodyLimitMiddleware:
    """Reject oversized bodies before they can be fully buffered by the app."""

    def __init__(self, app: ASGIApp, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in _BODY_METHODS:
            await self.app(scope, receive, send)
            return

        declared_length_error = _declared_length_error(scope, self.max_body_size)
        if declared_length_error is not None:
            await declared_length_error(scope, receive, send)
            return

        consumed = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_body_size:
                    raise RequestBodyTooLarge
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            if response_started:
                raise
            await _error_response(
                413,
                "payload_too_large",
                "Request body exceeds the configured size limit",
            )(scope, receive, send)


class RequestContextMiddleware:
    """Attach a safe request ID and emit one structured completion event."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.log = get_logger("omnibase.http")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = _request_id_from_scope(scope)
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)
        started_at = time.perf_counter()
        status_code = 500
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started, status_code
            if message["type"] == "http.response.start":
                response_started = True
                status_code = int(message["status"])
                headers = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() != b"x-request-id"
                ]
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception as exc:
            self.log.error(
                "http.request_failed",
                method=scope.get("method"),
                path=scope.get("path"),
                error_type=type(exc).__name__,
            )
            if response_started:
                raise
            status_code = 500
            response = _error_response(
                500,
                "internal_server_error",
                "An internal server error occurred",
            )
            response.headers["X-Request-Id"] = request_id
            await response(scope, receive, send)
        finally:
            route = scope.get("route")
            route_path = getattr(route, "path", scope.get("path"))
            self.log.info(
                "http.request_completed",
                method=scope.get("method"),
                path=scope.get("path"),
                route=route_path,
                status_code=status_code,
                duration_ms=round((time.perf_counter() - started_at) * 1000, 2),
            )
            structlog.contextvars.clear_contextvars()


__all__ = [
    "RequestBodyLimitMiddleware",
    "RequestBodyTooLarge",
    "RequestContextMiddleware",
]
