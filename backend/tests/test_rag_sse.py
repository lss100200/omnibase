"""SSE contract tests for the tenant-scoped POST /rag/ask endpoint."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnibase.rag.retriever import HybridResult
from omnibase.rag.router import _sse
from omnibase.rag.store import SearchResult
from omnibase.tenants.dependencies import TenantContext, get_current_tenant

_SAFE_NO_KEY_MESSAGE = "Answer generation is not configured."
_SAFE_PROVIDER_MESSAGE = "Answer generation is temporarily unavailable."


def _parse_sse(body: str) -> list[tuple[str, dict[str, Any]]]:
    """Parse the endpoint's single-line JSON SSE frames."""
    events: list[tuple[str, dict[str, Any]]] = []
    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        event = next(line.removeprefix("event: ") for line in lines if line.startswith("event: "))
        data = next(line.removeprefix("data: ") for line in lines if line.startswith("data: "))
        events.append((event, json.loads(data)))
    return events


def _result(
    *,
    chunk_id: str = "chunk-1",
    document_id: str = "doc-1",
    content: str = "Source content for the answer",
    score: float = 0.91,
    page: int = 3,
) -> HybridResult:
    chunk = SearchResult(
        chunk_id=chunk_id,
        document_id=document_id,
        content=content,
        score=score,
        chunk_index=0,
        char_start=0,
        char_end=len(content),
        chunk_type="paragraph",
        metadata={"page": page},
    )
    return HybridResult(chunk=chunk, rrf_score=0.02, vector_rank=1, bm25_rank=1)


def _build_client(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
    *,
    configured: bool,
    answer: Callable[..., Any],
    schema_name: str = "tenant_a1b2c3d4",
    results: list[HybridResult] | None = None,
    received: dict[str, Any] | None = None,
) -> TestClient:
    """Build a real router client while replacing external retrieval/provider work."""
    from omnibase.rag.router import router

    app = FastAPI()
    app.include_router(router)

    tenant = MagicMock()
    tenant.id = "tenant-id"
    tenant.schema_name = schema_name

    async def fake_tenant() -> TenantContext:
        return TenantContext(tenant=tenant)

    app.dependency_overrides[get_current_tenant] = fake_tenant

    def fake_search(**kwargs: Any) -> list[HybridResult]:
        if received is not None:
            received.update(kwargs)
        return results if results is not None else [_result()]

    monkeypatch.setattr("omnibase.rag.router.hybrid_search", fake_search)
    monkeypatch.setattr(
        "omnibase.rag.router.rerank", lambda query, candidates, top_k: candidates[:top_k]
    )
    monkeypatch.setattr("omnibase.rag.router.llm_configured", lambda: configured)
    monkeypatch.setattr("omnibase.rag.router.generate_answer", answer)
    client = TestClient(app)
    request.addfinalizer(client.close)
    return client


class TestSseFormat:
    """The _sse() helper produces spec-compliant, UTF-8 JSON frames."""

    def test_frame_shape_and_json(self) -> None:
        frame = _sse("chunk", {"content": "你好世界"})
        assert frame == 'event: chunk\ndata: {"content": "你好世界"}\n\n'
        assert _parse_sse(frame) == [("chunk", {"content": "你好世界"})]

    @pytest.mark.parametrize("event_name", ["citations", "chunk", "done", "error"])
    def test_event_name_is_preserved(self, event_name: str) -> None:
        assert _sse(event_name, {}).startswith(f"event: {event_name}\n")


class TestAskEndpointAuthentication:
    """The router retains the shared JWT/tenant authentication dependency."""

    def test_missing_authorization_is_rejected(self) -> None:
        from omnibase.rag.router import router

        app = FastAPI()
        app.include_router(router)
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.post("/rag/ask", json={"query": "question"})
        assert response.status_code == 401
        assert response.json()["detail"] == "Authorization header missing"


class TestAskEndpointStreams:
    """Exercise the actual endpoint and its streaming generators."""

    def test_success_is_citations_then_chunks_then_done(
        self,
        monkeypatch: pytest.MonkeyPatch,
        request: pytest.FixtureRequest,
    ) -> None:
        calls: dict[str, Any] = {}

        def answer(query: str, context: list[dict[str, Any]], *, stream: bool) -> Any:
            calls.update(query=query, context=context, stream=stream)
            return iter(["First ", "answer"])

        source = "A" * 240
        client = _build_client(
            monkeypatch,
            request,
            configured=True,
            answer=answer,
            results=[_result(content=source)],
        )
        response = client.post("/rag/ask", json={"query": "question", "top_k": 5})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(response.text)
        assert [name for name, _ in events] == ["citations", "chunk", "chunk", "done"]
        assert [data["content"] for name, data in events if name == "chunk"] == [
            "First ",
            "answer",
        ]
        assert events[-1][1]["answer"] == "First answer"
        assert events[-1][1]["citations"] == events[0][1]["citations"]
        assert calls == {
            "query": "question",
            "context": [
                {
                    "content": source,
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "score": 0.91,
                    "page_number": 3,
                }
            ],
            "stream": True,
        }

    def test_citation_shape_is_stable(
        self,
        monkeypatch: pytest.MonkeyPatch,
        request: pytest.FixtureRequest,
    ) -> None:
        client = _build_client(
            monkeypatch,
            request,
            configured=True,
            answer=lambda *args, **kwargs: iter(["ok"]),
            results=[_result(content="Z" * 240)],
        )
        events = _parse_sse(client.post("/rag/ask", json={"query": "q"}).text)
        citation = events[0][1]["citations"][0]

        assert citation == {
            "index": 1,
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "snippet": "Z" * 200,
            "page_number": 3,
            "score": 0.91,
        }

    def test_no_key_is_citations_then_terminal_safe_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        request: pytest.FixtureRequest,
    ) -> None:
        provider_called = False

        def answer(*args: Any, **kwargs: Any) -> Any:
            nonlocal provider_called
            provider_called = True
            return iter(())

        client = _build_client(monkeypatch, request, configured=False, answer=answer)
        events = _parse_sse(client.post("/rag/ask", json={"query": "q"}).text)

        assert [name for name, _ in events] == ["citations", "error"]
        assert events[-1][1] == {"message": _SAFE_NO_KEY_MESSAGE}
        assert provider_called is False
        assert all(name != "done" for name, _ in events)

    def test_provider_failure_is_terminal_and_does_not_leak_exception(
        self,
        monkeypatch: pytest.MonkeyPatch,
        request: pytest.FixtureRequest,
    ) -> None:
        secret = "sk-secret C:\\internal\\provider.py SQL SELECT password"

        def answer(*args: Any, **kwargs: Any) -> Any:
            def failing_stream() -> Any:
                yield "partial"
                raise RuntimeError(secret)

            return failing_stream()

        client = _build_client(monkeypatch, request, configured=True, answer=answer)
        events = _parse_sse(client.post("/rag/ask", json={"query": "q"}).text)

        assert [name for name, _ in events] == ["citations", "chunk", "error"]
        assert events[-1][1] == {"message": _SAFE_PROVIDER_MESSAGE}
        assert secret not in response_text(events)
        assert all(name != "done" for name, _ in events)

    def test_tenant_schema_is_forwarded_to_retrieval(
        self,
        monkeypatch: pytest.MonkeyPatch,
        request: pytest.FixtureRequest,
    ) -> None:
        received: dict[str, Any] = {}
        client = _build_client(
            monkeypatch,
            request,
            configured=True,
            answer=lambda *args, **kwargs: iter(["ok"]),
            schema_name="tenant_deadbeef",
            received=received,
        )
        response = client.post("/rag/ask", json={"query": "tenant question"})

        assert response.status_code == 200
        assert received == {
            "schema_name": "tenant_deadbeef",
            "query": "tenant question",
            "top_k": 100,
        }


def response_text(events: list[tuple[str, dict[str, Any]]]) -> str:
    """Serialize parsed event data for negative secret-leak assertions."""
    return json.dumps(events, ensure_ascii=False)
