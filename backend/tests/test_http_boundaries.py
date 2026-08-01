"""Focused tests for HTTP request boundaries and correlation IDs."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from omnibase.core.middleware import RequestBodyLimitMiddleware, RequestContextMiddleware


def _boundary_app(max_body_size: int = 8) -> tuple[TestClient, list[int]]:
    side_effects: list[int] = []
    app = FastAPI()

    @app.post("/body")
    async def body_endpoint(request: Request) -> dict[str, int]:
        body = await request.body()
        side_effects.append(len(body))
        return {"size": len(body)}

    app.add_middleware(RequestBodyLimitMiddleware, max_body_size=max_body_size)
    app.add_middleware(RequestContextMiddleware)
    return TestClient(app, raise_server_exceptions=False), side_effects


def test_request_id_is_generated_and_returned() -> None:
    client, _ = _boundary_app()
    response = client.post("/body", content=b"ok")
    assert response.status_code == 200
    assert response.headers["x-request-id"]


def test_valid_request_id_is_preserved() -> None:
    client, _ = _boundary_app()
    response = client.post(
        "/body",
        content=b"ok",
        headers={"X-Request-Id": "client.request-123"},
    )
    assert response.headers["x-request-id"] == "client.request-123"


@pytest.mark.parametrize("request_id", ["contains space", "x" * 65, "bad/value"])
def test_unsafe_request_id_is_replaced(request_id: str) -> None:
    client, _ = _boundary_app()
    response = client.post(
        "/body",
        content=b"ok",
        headers={"X-Request-Id": request_id},
    )
    assert response.status_code == 200
    assert response.headers["x-request-id"] != request_id


def test_declared_oversized_body_is_rejected_without_endpoint_side_effect() -> None:
    client, side_effects = _boundary_app(max_body_size=3)
    response = client.post("/body", content=b"four")
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "payload_too_large"
    assert response.headers["x-request-id"]
    assert side_effects == []


async def _invoke_streamed_body(
    *,
    messages: list[Message],
    max_body_size: int,
) -> tuple[list[Message], list[int]]:
    side_effects: list[int] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        consumed = 0
        while True:
            message = await receive()
            consumed += len(message.get("body", b""))
            if not message.get("more_body", False):
                break
        side_effects.append(consumed)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    pending: AsyncIterator[Message]

    async def message_stream() -> AsyncIterator[Message]:
        for message in messages:
            yield message

    pending = message_stream()

    async def receive() -> Message:
        return await anext(pending)

    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/body",
        "raw_path": b"/body",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }
    middleware = RequestBodyLimitMiddleware(app, max_body_size=max_body_size)
    await middleware(scope, receive, send)
    return sent, side_effects


@pytest.mark.asyncio
async def test_streamed_oversized_body_is_rejected_without_endpoint_side_effect() -> None:
    sent, side_effects = await _invoke_streamed_body(
        messages=[
            {"type": "http.request", "body": b"123", "more_body": True},
            {"type": "http.request", "body": b"456", "more_body": False},
        ],
        max_body_size=5,
    )
    start = next(message for message in sent if message["type"] == "http.response.start")
    assert start["status"] == 413
    assert side_effects == []


def test_invalid_content_length_is_rejected() -> None:
    client, side_effects = _boundary_app()
    response = client.post(
        "/body",
        content=b"ok",
        headers={"Content-Length": "not-a-number"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_content_length"
    assert side_effects == []


def test_request_id_is_present_on_unhandled_error() -> None:
    app = FastAPI()

    @app.get("/explode")
    async def explode() -> dict[str, Any]:
        raise RuntimeError("boom")

    app.add_middleware(RequestContextMiddleware)
    response = TestClient(app, raise_server_exceptions=False).get("/explode")
    assert response.status_code == 500
    assert response.headers["x-request-id"]
    assert response.json()["error"]["code"] == "internal_server_error"
