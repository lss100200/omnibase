from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.responses import StreamingResponse

from omnibase.agent_alpha.contracts import AlphaStreamEvent
from omnibase.agent_alpha.router import run_alpha_practice
from omnibase.agent_alpha.schemas import AlphaPracticeRunRequest
from omnibase.tenants.context import get_current_schema

TENANT_ID = "00000000-0000-0000-0000-000000000101"
WORKSPACE_ID = "00000000-0000-0000-0000-000000000102"
OWNER_ID = "00000000-0000-0000-0000-000000000103"
AGENT_VERSION_ID = "00000000-0000-0000-0000-000000000104"


class _PracticeAlpha:
    def __init__(
        self,
        *,
        fail_at: int | None = None,
        mismatch_at: int | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.schemas: list[str | None] = []
        self.fail_at = fail_at
        self.mismatch_at = mismatch_at

    def invoke(self, **kwargs: object) -> Iterator[AlphaStreamEvent]:
        self.calls.append(kwargs)
        self.schemas.append(get_current_schema())
        ordinal = len(self.calls)
        role = str(kwargs["employee_role_id"])
        yield AlphaStreamEvent(
            kind="meta",
            payload={
                "invocation_id": f"00000000-0000-0000-0001-{ordinal:012d}",
                "task_id": f"00000000-0000-0000-0002-{ordinal:012d}",
                "requested_model_id": "deepseek-v4-flash",
            },
        )
        yield AlphaStreamEvent(
            kind="citations",
            payload={
                "citations": [
                    {
                        "index": 1,
                        "chunk_id": f"chunk-{ordinal}",
                        "document_id": f"document-{ordinal}",
                        "snippet": "source text must stay private",
                        "page_number": 1,
                        "score": 0.9,
                    }
                ]
            },
        )
        if self.fail_at == ordinal:
            yield AlphaStreamEvent(kind="error", payload={"code": "fixture_unknown"})
            return
        yield AlphaStreamEvent(
            kind="usage",
            payload={
                "input_tokens": 20,
                "output_tokens": 10,
                "total_tokens": 30,
                "reasoning_tokens": 2,
                "cached_input_tokens": 4,
                "cache_miss_input_tokens": 16,
            },
        )
        yield AlphaStreamEvent(
            kind="done",
            payload={
                "answer": json.dumps({"role": role, "accepted": True}),
                "actual_model_id": (
                    "deepseek-v4-pro" if self.mismatch_at == ordinal else "deepseek-v4-flash"
                ),
            },
        )


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        tenant_id=TENANT_ID,
        schema_name="tenant_personal",
        user_id=OWNER_ID,
    )


def _enable_posture(monkeypatch: pytest.MonkeyPatch) -> None:
    values = {
        "ENV": "production",
        "P6_4_PERSONAL_PRACTICE_ENABLED": "true",
        "AGENT_RUNTIME_ENABLED": "true",
        "AGENT_PLANNER_ENABLED": "false",
        "MULTI_AGENT_ENABLED": "false",
        "MCP_RUNTIME_ENABLED": "false",
        "PERSONAL_RUNTIME_PROFILE": "personal_single_owner",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr("omnibase.agent_alpha.router.PersonalCanaryAgentAlpha", _PracticeAlpha)


async def _collect_body(response: StreamingResponse) -> str:
    chunks: list[str] = []
    body: AsyncIterator[bytes | str] = response.body_iterator
    async for chunk in body:
        chunks.append(chunk.decode() if isinstance(chunk, bytes) else chunk)
    return "".join(chunks)


def _run(
    *,
    payload: AlphaPracticeRunRequest,
    alpha: _PracticeAlpha,
) -> tuple[str, _PracticeAlpha]:
    response = run_alpha_practice(
        workspace_id=WORKSPACE_ID,
        payload=payload,
        idempotency_key="owner-p64-request",
        alpha=alpha,
        ctx=_ctx(),
    )
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_collect_body(response)), alpha
    finally:
        loop.close()


def test_practice_stream_reestablishes_and_restores_tenant_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_posture(monkeypatch)
    alpha = _PracticeAlpha()
    payload = AlphaPracticeRunRequest(
        agent_version_id=AGENT_VERSION_ID,
        scenario="rag",
        participant_count=1,
        task="Answer from evidence.",
    )

    assert get_current_schema() is None
    body, alpha = _run(payload=payload, alpha=alpha)

    assert "event: practice_completed" in body
    assert alpha.schemas == ["tenant_personal"]
    assert get_current_schema() is None


@pytest.mark.parametrize(
    ("scenario", "roles", "count"),
    [
        ("rag", [], 1),
        ("rag", ["data", "qa"], 3),
        ("workspace", ["product", "frontend", "backend", "security", "qa"], 6),
    ],
)
def test_practice_route_runs_one_three_and_six_independent_alpha_calls(
    monkeypatch: pytest.MonkeyPatch,
    scenario: str,
    roles: list[str],
    count: int,
) -> None:
    _enable_posture(monkeypatch)
    payload = AlphaPracticeRunRequest.model_validate(
        {
            "agent_version_id": AGENT_VERSION_ID,
            "scenario": scenario,
            "participant_count": count,
            "specialist_roles": roles,
            "task": "Complete the bounded personal practice scenario.",
            "top_k": 5,
        }
    )

    body, alpha = _run(payload=payload, alpha=_PracticeAlpha())

    assert len(alpha.calls) == count
    assert [call["employee_role_id"] for call in alpha.calls] == [*roles, "parent"]
    assert body.count("event: node_completed") == count
    assert "event: practice_completed" in body
    assert f'"provider_call_count": {count}' in body
    assert '"enterprise_multi_agent": false' in body
    assert '"input_tokens": 20' in body
    assert '"chunk_id": "chunk-1"' in body
    assert "source text must stay private" not in body
    assert "untrusted_result" not in body


@pytest.mark.parametrize(
    ("roles", "count", "expected_code"),
    [
        (["data"], 3, "practice_roster_count_mismatch"),
        (["data", "data"], 3, "practice_duplicate_role"),
    ],
)
def test_practice_route_rejects_invalid_roster_before_any_call(
    monkeypatch: pytest.MonkeyPatch,
    roles: list[str],
    count: int,
    expected_code: str,
) -> None:
    _enable_posture(monkeypatch)
    alpha = _PracticeAlpha()
    payload = AlphaPracticeRunRequest.model_validate(
        {
            "agent_version_id": AGENT_VERSION_ID,
            "scenario": "rag",
            "participant_count": count,
            "specialist_roles": roles,
            "task": "Answer from evidence.",
        }
    )

    with pytest.raises(HTTPException) as raised:
        _run(payload=payload, alpha=alpha)

    assert raised.value.status_code == 422
    assert raised.value.detail["error"]["code"] == expected_code
    assert alpha.calls == []


def test_practice_route_rejects_when_production_posture_is_not_exact(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_posture(monkeypatch)
    monkeypatch.setenv("MULTI_AGENT_ENABLED", "true")
    alpha = _PracticeAlpha()
    payload = AlphaPracticeRunRequest(
        agent_version_id=AGENT_VERSION_ID,
        scenario="rag",
        participant_count=1,
        task="Answer from evidence.",
    )

    with pytest.raises(HTTPException) as raised:
        _run(payload=payload, alpha=alpha)

    assert raised.value.status_code == 503
    assert raised.value.detail["error"]["code"] == "personal_practice_unavailable"
    assert alpha.calls == []


def test_practice_route_requires_the_exact_personal_canary_facade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_posture(monkeypatch)
    monkeypatch.setattr(
        "omnibase.agent_alpha.router.PersonalCanaryAgentAlpha",
        type("_DifferentCanaryFacade", (), {}),
    )
    alpha = _PracticeAlpha()
    payload = AlphaPracticeRunRequest(
        agent_version_id=AGENT_VERSION_ID,
        scenario="rag",
        participant_count=1,
        task="Answer from evidence.",
    )

    with pytest.raises(HTTPException) as raised:
        _run(payload=payload, alpha=alpha)

    assert raised.value.status_code == 503
    assert raised.value.detail["error"]["code"] == "personal_practice_unavailable"
    assert alpha.calls == []


def test_practice_route_rejects_unsafe_idempotency_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _enable_posture(monkeypatch)
    alpha = _PracticeAlpha()
    payload = AlphaPracticeRunRequest(
        agent_version_id=AGENT_VERSION_ID,
        scenario="rag",
        participant_count=1,
        task="Answer from evidence.",
    )

    with pytest.raises(HTTPException) as raised:
        run_alpha_practice(
            workspace_id=WORKSPACE_ID,
            payload=payload,
            idempotency_key="unsafe key with spaces",
            alpha=alpha,
            ctx=_ctx(),
        )

    assert raised.value.status_code == 422
    assert raised.value.detail["error"]["code"] == "invalid_idempotency_key"
    assert alpha.calls == []


@pytest.mark.parametrize(
    ("alpha", "expected_code"),
    [
        (_PracticeAlpha(fail_at=2), "practice_node_terminal_failure:qa:fixture_unknown"),
        (_PracticeAlpha(mismatch_at=2), "practice_node_model_identity_mismatch:qa"),
    ],
)
def test_practice_route_stops_before_parent_after_unverifiable_child(
    monkeypatch: pytest.MonkeyPatch,
    alpha: _PracticeAlpha,
    expected_code: str,
) -> None:
    _enable_posture(monkeypatch)
    payload = AlphaPracticeRunRequest(
        agent_version_id=AGENT_VERSION_ID,
        scenario="rag",
        participant_count=3,
        specialist_roles=["data", "qa"],
        task="Answer from evidence.",
    )

    body, alpha = _run(payload=payload, alpha=alpha)

    assert [call["employee_role_id"] for call in alpha.calls] == ["data", "qa"]
    assert "event: practice_completed" not in body
    assert "event: error" in body
    assert expected_code in body
