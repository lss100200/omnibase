#!/usr/bin/env python3
"""Run the six-journey P6.4 live matrix against an already activated target.

The controller never reads the repository root ``.env``.  The Browser access
token and DeepSeek key are accepted only from the process environment under
fixed names.  They are used in request headers/bodies, redacted from failures,
and never written to the matrix fragment.  This runner does not start Docker,
WSL, a VM or a production Runtime; target activation/closure is a separate
operator-owned step and the final receipt remains unaccepted until closure and
disposable-target cleanup are independently proved.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
if str(BACKEND_SRC) not in sys.path:
    sys.path.insert(0, str(BACKEND_SRC))

from omnibase.agent_practice.artifacts import (  # noqa: E402
    RenderedArtifact,
    render_clock_html,
    render_slide_deck_html,
)
from omnibase.agent_practice.changesets import (  # noqa: E402
    TextChangeProposal,
    apply_text_change,
    rollback_text_change,
)
from omnibase.agent_practice.contracts import CitationClaim, EvidenceChunk  # noqa: E402
from omnibase.agent_practice.scoring import ExpectedFact, score_citations  # noqa: E402

ACCESS_TOKEN_ENV = "OMNIBASE_P64_ACCESS_TOKEN"
DEEPSEEK_KEY_ENV = "OMNIBASE_P64_DEEPSEEK_API_KEY"
MATRIX_SCHEMA = "omnibase.p6-4.personal-agent-practice-matrix.v1"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_COUNTS = frozenset({1, 3, 4, 5, 6})
_ALLOWED_MODELS = frozenset({"deepseek-v4-flash", "deepseek-v4-pro"})
_ROLES = frozenset(
    {
        "parent",
        "product",
        "ux",
        "frontend",
        "backend",
        "data",
        "security",
        "qa",
        "operations",
        "docs",
    }
)
_ROSTERS: dict[str, tuple[str, ...]] = {
    "rag_single": ("parent",),
    "rag_three": ("data", "qa", "parent"),
    "artifact_single": ("parent",),
    "artifact_four": ("product", "ux", "frontend", "parent"),
    "workspace_single": ("parent",),
    "workspace_six": ("product", "frontend", "backend", "security", "qa", "parent"),
}


class LiveMatrixError(RuntimeError):
    """Stable fail-closed live-matrix error without Provider payloads."""


@dataclass(frozen=True, slots=True)
class TargetCoordinates:
    base_url: str
    workspace_id: str
    decoy_workspace_id: str
    agent_version_id: str


@dataclass(slots=True)
class _ActiveNode:
    ordinal: int
    role: str
    started_at: float
    identity: dict[str, str] | None = None
    citations: list[dict[str, object]] | None = None
    usage_seen: bool = False


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _record(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise LiveMatrixError(f"{label} must be an object")
    return value


def _string(value: object, *, label: str, maximum: int = 200) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise LiveMatrixError(f"{label} is invalid")
    return value


def _integer(value: object, *, label: str, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise LiveMatrixError(f"{label} is invalid")
    return value


def _redacted_failure(code: str, *, status: int | None = None) -> LiveMatrixError:
    suffix = f":http_{status}" if status is not None else ""
    return LiveMatrixError(f"{code}{suffix}")


def _target_url(base_url: str, path: str) -> str:
    return base_url.rstrip("/") + (path if path.startswith("/") else f"/{path}")


def _validate_loopback_url(value: str) -> str:
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise LiveMatrixError("target must be an explicit loopback HTTP origin")
    if (
        parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise LiveMatrixError(
            "target origin must not contain credentials, query, fragment or path"
        )
    if parsed.port is None:
        raise LiveMatrixError("target origin must include an explicit port")
    return value.rstrip("/")


class BrowserClient:
    def __init__(self, *, base_url: str, access_token: str) -> None:
        self.base_url = _validate_loopback_url(base_url)
        if not access_token:
            raise LiveMatrixError(f"{ACCESS_TOKEN_ENV} is empty")
        self._access_token = access_token

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: object | None = None,
        headers: dict[str, str] | None = None,
        expected: tuple[int, ...] = (200,),
        timeout: float = 60,
    ) -> tuple[int, bytes, str]:
        request_headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._access_token}",
        }
        if headers:
            request_headers.update(headers)
        data: bytes | None = None
        if payload is not None:
            data = _canonical(payload)
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            _target_url(self.base_url, path),
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read(1024 * 1024 + 1)
                status = int(response.status)
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            if status not in expected:
                raise _redacted_failure(
                    "browser_request_rejected", status=status
                ) from None
            body = exc.read(1024 * 1024 + 1)
            content_type = exc.headers.get("Content-Type", "")
        except (OSError, urllib.error.URLError, TimeoutError):
            raise _redacted_failure("browser_request_unavailable") from None
        if status not in expected:
            raise _redacted_failure("browser_request_unexpected", status=status)
        if len(body) > 1024 * 1024:
            raise LiveMatrixError("browser_response_budget_exceeded")
        return status, body, content_type

    def json(
        self,
        method: str,
        path: str,
        *,
        payload: object | None = None,
        expected: tuple[int, ...] = (200,),
        timeout: float = 60,
    ) -> dict[str, object]:
        _, body, content_type = self._request(
            method,
            path,
            payload=payload,
            expected=expected,
            timeout=timeout,
        )
        if "application/json" not in content_type.lower():
            raise LiveMatrixError("browser_response_content_type_invalid")
        try:
            return _record(json.loads(body), label="browser response")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveMatrixError("browser_response_json_invalid") from exc

    def delete(self, path: str, *, expected: tuple[int, ...] = (204,)) -> None:
        self._request("DELETE", path, expected=expected)

    def expect_status(
        self, method: str, path: str, *, expected: tuple[int, ...]
    ) -> None:
        self._request(method, path, expected=expected)

    def upload(
        self, *, workspace_id: str, filename: str, content: bytes
    ) -> dict[str, object]:
        boundary = f"omnibase-p64-{uuid.uuid4().hex}"
        parts = [
            f'--{boundary}\r\nContent-Disposition: form-data; name="workspace_id"\r\n\r\n{workspace_id}\r\n'.encode(),
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n'
            ).encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ]
        _, body, content_type = self._multipart_request(
            boundary=boundary, body=b"".join(parts)
        )
        if "application/json" not in content_type.lower():
            raise LiveMatrixError("document_upload_content_type_invalid")
        try:
            return _record(json.loads(body), label="document upload")
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LiveMatrixError("document_upload_json_invalid") from exc

    def _multipart_request(
        self, *, boundary: str, body: bytes
    ) -> tuple[int, bytes, str]:
        request = urllib.request.Request(
            _target_url(self.base_url, "/api/v1/documents"),
            data=body,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                observed = response.read(1024 * 1024 + 1)
                status = int(response.status)
                content_type = response.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            raise _redacted_failure(
                "document_upload_rejected", status=exc.code
            ) from None
        except (OSError, urllib.error.URLError, TimeoutError):
            raise _redacted_failure("document_upload_unavailable") from None
        if status != 202 or len(observed) > 1024 * 1024:
            raise _redacted_failure("document_upload_unexpected", status=status)
        return status, observed, content_type

    def wait_document_indexed(self, document_id: str, *, timeout: float = 900) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            document = self.json("GET", f"/api/v1/documents/{document_id}")
            status = document.get("status")
            if status == "indexed":
                return
            if status == "failed":
                raise LiveMatrixError("document_ingestion_failed")
            if status not in {"pending", "queued", "processing"}:
                raise LiveMatrixError("document_ingestion_state_invalid")
            time.sleep(2)
        raise LiveMatrixError("document_ingestion_timeout")

    def practice(
        self,
        *,
        workspace_id: str,
        payload: dict[str, object],
        expected_roles: tuple[str, ...],
    ) -> tuple[list[dict[str, object]], dict[str, object], str]:
        request = urllib.request.Request(
            _target_url(
                self.base_url, f"/api/v1/workspaces/{workspace_id}/agent-alpha/practice"
            ),
            data=_canonical(payload),
            headers={
                "Accept": "text/event-stream",
                "Authorization": f"Bearer {self._access_token}",
                "Content-Type": "application/json",
                "Idempotency-Key": f"p64-{uuid.uuid4().hex}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=600) as response:
                if int(response.status) != 200:
                    raise _redacted_failure(
                        "practice_request_unexpected", status=int(response.status)
                    )
                if (
                    "text/event-stream"
                    not in response.headers.get("Content-Type", "").lower()
                ):
                    raise LiveMatrixError("practice_stream_content_type_invalid")
                return collect_practice_stream(
                    response,
                    expected_roles=expected_roles,
                    expected_scenario=_string(
                        payload.get("scenario"), label="practice scenario", maximum=16
                    ),
                )
        except urllib.error.HTTPError as exc:
            raise _redacted_failure(
                "practice_request_rejected", status=exc.code
            ) from None
        except (OSError, urllib.error.URLError, TimeoutError):
            raise _redacted_failure("practice_request_unavailable") from None


def _sse_events(response: Any) -> Any:
    event = ""
    data: list[str] = []
    observed = 0
    for raw_line in response:
        if not isinstance(raw_line, bytes):
            raise LiveMatrixError("practice_stream_bytes_required")
        observed += len(raw_line)
        if observed > 1024 * 1024:
            raise LiveMatrixError("practice_stream_budget_exceeded")
        try:
            line = raw_line.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise LiveMatrixError("practice_stream_utf8_invalid") from exc
        if not line:
            if event and data:
                try:
                    payload = _record(
                        json.loads("\n".join(data)), label="practice event"
                    )
                except json.JSONDecodeError as exc:
                    raise LiveMatrixError("practice_stream_json_invalid") from exc
                yield event, payload
            event = ""
            data = []
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    if event or data:
        raise LiveMatrixError("practice_stream_incomplete_frame")


def _parse_usage(value: object) -> dict[str, int]:
    usage = _record(value, label="node usage")
    fields = (
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "reasoning_tokens",
        "cached_input_tokens",
        "cache_miss_input_tokens",
    )
    if set(usage) != set(fields):
        raise LiveMatrixError("practice_node_usage_shape_invalid")
    parsed = {field: _integer(usage[field], label=f"usage.{field}") for field in fields}
    if parsed["total_tokens"] < parsed["input_tokens"] + parsed["output_tokens"]:
        raise LiveMatrixError("practice_node_usage_inconsistent")
    return parsed


def _parse_citations(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list) or len(value) > 8:
        raise LiveMatrixError("practice_citations_invalid")
    parsed: list[dict[str, object]] = []
    for offset, raw in enumerate(value, start=1):
        citation = _record(raw, label="practice citation")
        if set(citation) != {"index", "chunk_id", "document_id", "page_number"}:
            raise LiveMatrixError("practice_citation_shape_invalid")
        if _integer(citation["index"], label="citation.index", minimum=1) != offset:
            raise LiveMatrixError("practice_citation_order_invalid")
        parsed.append(
            {
                "index": offset,
                "chunk_id": _string(
                    citation["chunk_id"], label="citation.chunk_id", maximum=128
                ),
                "document_id": _string(
                    citation["document_id"], label="citation.document_id", maximum=128
                ),
                "page_number": _integer(
                    citation["page_number"], label="citation.page_number", minimum=1
                ),
            }
        )
    return parsed


def _same_json(left: object, right: object) -> bool:
    return json.dumps(left, sort_keys=True, separators=(",", ":")) == json.dumps(
        right, sort_keys=True, separators=(",", ":")
    )


def _complete_node(
    active: _ActiveNode, payload: dict[str, object]
) -> dict[str, object]:
    if active.identity is None or active.citations is None or not active.usage_seen:
        raise LiveMatrixError("practice_node_receipt_incomplete")
    if payload.get("ordinal") != active.ordinal or payload.get("role") != active.role:
        raise LiveMatrixError("practice_node_receipt_order_invalid")
    identity = {
        "invocation_id": _string(
            payload.get("invocation_id"), label="invocation_id", maximum=128
        ),
        "task_id": _string(payload.get("task_id"), label="task_id", maximum=128),
        "requested_model_id": _string(
            payload.get("requested_model_id"), label="requested_model_id"
        ),
    }
    if identity != active.identity:
        raise LiveMatrixError("practice_node_identity_drift")
    actual_model_id = _string(payload.get("actual_model_id"), label="actual_model_id")
    if actual_model_id != identity["requested_model_id"]:
        raise LiveMatrixError("practice_node_model_identity_mismatch")
    answer_sha256 = _string(
        payload.get("answer_sha256"), label="answer_sha256", maximum=64
    )
    if _SHA256.fullmatch(answer_sha256) is None:
        raise LiveMatrixError("practice_node_answer_digest_invalid")
    citations = _parse_citations(payload.get("citations"))
    if not _same_json(citations, active.citations):
        raise LiveMatrixError("practice_node_citation_drift")
    return {
        "ordinal": active.ordinal,
        "role": active.role,
        **identity,
        "actual_model_id": actual_model_id,
        "usage": _parse_usage(payload.get("usage")),
        "latency_ms": max(0, round((time.monotonic() - active.started_at) * 1000)),
        "answer_sha256": answer_sha256,
        "citations": citations,
    }


@dataclass(slots=True)
class _PracticeStreamState:
    expected_roles: tuple[str, ...]
    expected_scenario: str
    started: bool = False
    active: _ActiveNode | None = None
    nodes: list[dict[str, object]] | None = None
    final_payload: dict[str, object] | None = None
    final_answer: str = ""
    terminal: bool = False

    def __post_init__(self) -> None:
        self.nodes = []

    def _node_list(self) -> list[dict[str, object]]:
        if self.nodes is None:  # pragma: no cover - dataclass initialization guard
            raise LiveMatrixError("practice_stream_state_invalid")
        return self.nodes

    def _started(self, payload: dict[str, object]) -> None:
        nodes = self._node_list()
        if self.started or self.active is not None or nodes:
            raise LiveMatrixError("practice_started_duplicate")
        if (
            payload.get("participant_count") != len(self.expected_roles)
            or payload.get("scenario") != self.expected_scenario
            or payload.get("roles") != list(self.expected_roles)
            or payload.get("serial") is not True
            or payload.get("enterprise_multi_agent") is not False
        ):
            raise LiveMatrixError("practice_started_scope_drift")
        self.started = True

    def _node_started(self, payload: dict[str, object]) -> None:
        nodes = self._node_list()
        ordinal = len(nodes) + 1
        if ordinal > len(self.expected_roles):
            raise LiveMatrixError("practice_node_started_order_invalid")
        role = self.expected_roles[ordinal - 1]
        if (
            not self.started
            or self.active is not None
            or payload.get("ordinal") != ordinal
            or payload.get("role") != role
        ):
            raise LiveMatrixError("practice_node_started_order_invalid")
        self.active = _ActiveNode(
            ordinal=ordinal, role=role, started_at=time.monotonic()
        )

    def _node_event(self, payload: dict[str, object]) -> None:
        active = self.active
        if (
            active is None
            or payload.get("ordinal") != active.ordinal
            or payload.get("role") != active.role
        ):
            raise LiveMatrixError("practice_node_event_identity_invalid")
        kind = payload.get("event")
        if kind == "meta":
            if active.identity is not None:
                raise LiveMatrixError("practice_node_meta_duplicate")
            active.identity = {
                "invocation_id": _string(
                    payload.get("invocation_id"), label="invocation_id", maximum=128
                ),
                "task_id": _string(
                    payload.get("task_id"), label="task_id", maximum=128
                ),
                "requested_model_id": _string(
                    payload.get("requested_model_id"), label="requested_model_id"
                ),
            }
            return
        if kind == "citations":
            if active.citations is not None:
                raise LiveMatrixError("practice_node_citations_duplicate")
            active.citations = _parse_citations(payload.get("citations"))
            return
        if kind == "usage":
            if active.usage_seen:
                raise LiveMatrixError("practice_node_usage_duplicate")
            active.usage_seen = True
            return
        raise LiveMatrixError("practice_node_event_unknown")

    def _node_completed(self, payload: dict[str, object]) -> None:
        if self.active is None:
            raise LiveMatrixError("practice_node_completed_without_start")
        self._node_list().append(_complete_node(self.active, payload))
        self.active = None

    def _completed(self, payload: dict[str, object]) -> None:
        nodes = self._node_list()
        if self.active is not None or len(nodes) != len(self.expected_roles):
            raise LiveMatrixError("practice_completed_before_roster")
        if (
            payload.get("participant_count") != len(self.expected_roles)
            or payload.get("scenario") != self.expected_scenario
            or payload.get("provider_call_count") != len(self.expected_roles)
            or payload.get("parent_invocation_id") != nodes[-1]["invocation_id"]
            or payload.get("parent_task_id") != nodes[-1]["task_id"]
        ):
            raise LiveMatrixError("practice_completed_scope_drift")
        final_answer = _string(
            payload.get("final_answer"), label="final_answer", maximum=256 * 1024
        )
        final_digest = _string(
            payload.get("final_answer_sha256"), label="final_answer_sha256", maximum=64
        )
        if (
            _SHA256.fullmatch(final_digest) is None
            or _sha256(final_answer.encode()) != final_digest
            or final_digest != nodes[-1]["answer_sha256"]
        ):
            raise LiveMatrixError("practice_final_answer_digest_invalid")
        try:
            self.final_payload = _record(
                json.loads(final_answer), label="practice final payload"
            )
        except json.JSONDecodeError as exc:
            raise LiveMatrixError("practice_final_payload_json_invalid") from exc
        self.final_answer = final_answer
        self.terminal = True

    def consume(self, event: str, payload: dict[str, object]) -> None:
        if self.terminal:
            raise LiveMatrixError("practice_stream_after_terminal")
        handlers: dict[str, Callable[[dict[str, object]], None]] = {
            "practice_started": self._started,
            "node_started": self._node_started,
            "node_event": self._node_event,
            "node_completed": self._node_completed,
            "practice_completed": self._completed,
        }
        if event == "error":
            raise LiveMatrixError("practice_stream_terminal_error")
        handler = handlers.get(event)
        if handler is None:
            raise LiveMatrixError("practice_stream_event_unknown")
        handler(payload)

    def finish(self) -> tuple[list[dict[str, object]], dict[str, object], str]:
        nodes = self._node_list()
        if not self.terminal or self.final_payload is None:
            raise LiveMatrixError("practice_stream_incomplete")
        if len({node["invocation_id"] for node in nodes}) != len(nodes):
            raise LiveMatrixError("practice_invocation_identity_reused")
        if len({node["task_id"] for node in nodes}) != len(nodes):
            raise LiveMatrixError("practice_task_identity_reused")
        return nodes, self.final_payload, self.final_answer


def collect_practice_stream(
    response: Any, *, expected_roles: tuple[str, ...], expected_scenario: str
) -> tuple[list[dict[str, object]], dict[str, object], str]:
    if len(expected_roles) not in _COUNTS or expected_roles[-1] != "parent":
        raise LiveMatrixError("expected practice roster is invalid")
    state = _PracticeStreamState(
        expected_roles=expected_roles,
        expected_scenario=expected_scenario,
    )
    for event, payload in _sse_events(response):
        state.consume(event, payload)
    return state.finish()


def _deepseek_models_preflight(*, api_key: str, model_id: str) -> None:
    request = urllib.request.Request(
        "https://api.deepseek.com/v1/models",
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            if int(response.status) != 200:
                raise _redacted_failure(
                    "deepseek_models_preflight_rejected", status=response.status
                )
            body = response.read(1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        raise _redacted_failure(
            "deepseek_models_preflight_rejected", status=exc.code
        ) from None
    except (OSError, urllib.error.URLError, TimeoutError):
        raise _redacted_failure("deepseek_models_preflight_unavailable") from None
    if len(body) > 1024 * 1024:
        raise LiveMatrixError("deepseek_models_preflight_budget_exceeded")
    try:
        payload = _record(json.loads(body), label="DeepSeek models response")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LiveMatrixError("deepseek_models_preflight_json_invalid") from exc
    raw_models = payload.get("data")
    if not isinstance(raw_models, list):
        raise LiveMatrixError("deepseek_models_preflight_shape_invalid")
    available = {
        item.get("id")
        for item in raw_models
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    if model_id not in available:
        raise LiveMatrixError("deepseek_requested_model_unavailable")


def _exact_keys(value: dict[str, object], expected: set[str], *, label: str) -> None:
    if set(value) != expected:
        raise LiveMatrixError(f"{label} shape invalid")


def _parse_rag_claims(
    payload: dict[str, object], *, parent_citations: list[dict[str, object]]
) -> tuple[str, tuple[CitationClaim, ...]]:
    _exact_keys(payload, {"answer", "claims", "abstained"}, label="RAG payload")
    answer = _string(payload["answer"], label="RAG answer", maximum=64_000)
    if payload["abstained"] is not False or not isinstance(payload["claims"], list):
        raise LiveMatrixError("RAG payload abstained or claims invalid")
    index_map = {
        _integer(item["index"], label="RAG citation index", minimum=1): str(
            item["chunk_id"]
        )
        for item in parent_citations
    }
    claims: list[CitationClaim] = []
    for raw in payload["claims"]:
        claim = _record(raw, label="RAG claim")
        _exact_keys(
            claim,
            {"fact_id", "statement", "citation_indices"},
            label="RAG claim",
        )
        indices = claim["citation_indices"]
        if (
            not isinstance(indices, list)
            or not indices
            or any(
                isinstance(item, bool) or not isinstance(item, int) for item in indices
            )
            or len(set(indices)) != len(indices)
            or any(item not in index_map for item in indices)
        ):
            raise LiveMatrixError("RAG claim citation indices invalid")
        claims.append(
            CitationClaim(
                fact_id=_string(claim["fact_id"], label="RAG fact_id", maximum=128),
                statement=_string(
                    claim["statement"], label="RAG statement", maximum=2_000
                ),
                citation_chunk_ids=tuple(index_map[item] for item in indices),
            )
        )
    return answer, tuple(claims)


def _rag_result(
    *,
    payload: dict[str, object],
    nodes: list[dict[str, object]],
    fact_document_ids: dict[str, str],
    decoy_document_ids: frozenset[str],
) -> dict[str, object]:
    if set(fact_document_ids) != {"project_codename", "release_channel"}:
        raise LiveMatrixError("RAG fact document map is invalid")
    parent_citations = nodes[-1]["citations"]
    if not isinstance(parent_citations, list) or not parent_citations:
        raise LiveMatrixError("RAG parent returned no citations")
    document_ids = {str(item["document_id"]) for item in parent_citations}
    expected_document_ids = set(fact_document_ids.values())
    if document_ids & decoy_document_ids or not expected_document_ids.issubset(
        document_ids
    ):
        raise LiveMatrixError("RAG Workspace citation isolation failed")
    supporting_chunks = {
        fact_id: frozenset(
            str(item["chunk_id"])
            for item in parent_citations
            if item["document_id"] == document_id
        )
        for fact_id, document_id in fact_document_ids.items()
    }
    if any(not chunks for chunks in supporting_chunks.values()):
        raise LiveMatrixError("RAG fact-specific chunks are absent")
    answer, claims = _parse_rag_claims(payload, parent_citations=parent_citations)
    required_markers = {
        "project_codename": "ORCHID-417",
        "release_channel": "LANTERN-82",
    }
    if any(
        marker.casefold() not in answer.casefold()
        for marker in required_markers.values()
    ) or any(
        marker.casefold() in answer.casefold() for marker in ("COBALT-992", "EMBER-31")
    ):
        raise LiveMatrixError("RAG answer fact content is invalid")
    claim_by_fact = {claim.fact_id: claim for claim in claims}
    if set(claim_by_fact) != set(required_markers):
        raise LiveMatrixError("RAG claim fact set is invalid")
    citation_indices_by_chunk = {
        str(item["chunk_id"]): _integer(
            item["index"], label="RAG citation index", minimum=1
        )
        for item in parent_citations
    }
    for fact_id, claim in claim_by_fact.items():
        if not any(
            f"[{citation_indices_by_chunk[chunk_id]}]" in answer
            for chunk_id in claim.citation_chunk_ids
        ):
            raise LiveMatrixError(f"RAG answer citation label missing:{fact_id}")
    evidence = tuple(
        EvidenceChunk(
            chunk_id=str(item["chunk_id"]),
            document_id=str(item["document_id"]),
            content="redacted acceptance chunk",
            page_number=_integer(
                item["page_number"], label="RAG citation page", minimum=1
            ),
        )
        for item in parent_citations
    )
    score = score_citations(
        claims=claims,
        expected_facts=(
            ExpectedFact(
                "project_codename",
                supporting_chunks["project_codename"],
                "ORCHID-417",
            ),
            ExpectedFact(
                "release_channel",
                supporting_chunks["release_channel"],
                "LANTERN-82",
            ),
        ),
        evidence=evidence,
    )
    if not score.passed:
        raise LiveMatrixError("RAG deterministic citation score failed")
    return {
        "browser_upload_completed": True,
        "workspace_binding_verified": True,
        "index_ready": True,
        "decoy_workspace_excluded": True,
        "expected_fact_count": score.expected_fact_count,
        "supported_claim_count": score.supported_claim_count,
        "unsupported_claim_count": score.unsupported_claim_count,
        "missing_fact_count": score.missing_fact_count,
        "wrong_chunk_count": score.wrong_chunk_count,
        "unknown_chunk_count": score.unknown_chunk_count,
        "statement_mismatch_count": score.statement_mismatch_count,
        "fact_precision": score.fact_precision,
        "fact_recall": score.fact_recall,
        "citation_precision": score.citation_precision,
        "citation_recall": score.citation_recall,
    }


def _render_artifact(payload: dict[str, object]) -> RenderedArtifact:
    _exact_keys(
        payload,
        {"artifact_type", "title", "specification", "acceptance_checks"},
        label="artifact payload",
    )
    artifact_type = payload["artifact_type"]
    title = _string(payload["title"], label="artifact title", maximum=100)
    specification = _record(payload["specification"], label="artifact specification")
    checks = payload["acceptance_checks"]
    if not isinstance(checks, list) or len(checks) > 12:
        raise LiveMatrixError("artifact acceptance checks invalid")
    if artifact_type == "clock_html":
        _exact_keys(specification, {"accent"}, label="clock specification")
        return render_clock_html(
            title=title,
            accent=_string(specification["accent"], label="clock accent", maximum=7),
        )
    if artifact_type != "slides_html":
        raise LiveMatrixError("artifact type unsupported")
    _exact_keys(specification, {"slides"}, label="slides specification")
    raw_slides = specification["slides"]
    if not isinstance(raw_slides, list):
        raise LiveMatrixError("slides specification invalid")
    slides: list[tuple[str, tuple[str, ...]]] = []
    for raw in raw_slides:
        slide = _record(raw, label="slide")
        _exact_keys(slide, {"heading", "bullets"}, label="slide")
        bullets = slide["bullets"]
        if not isinstance(bullets, list) or len(bullets) > 8:
            raise LiveMatrixError("slide bullets invalid")
        slides.append(
            (
                _string(slide["heading"], label="slide heading", maximum=120),
                tuple(
                    _string(item, label="slide bullet", maximum=240) for item in bullets
                ),
            )
        )
    return render_slide_deck_html(title=title, slides=tuple(slides))


def _edge_path() -> Path:
    candidates = [
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
    ]
    resolved = shutil.which("msedge") or shutil.which("microsoft-edge")
    if resolved:
        candidates.append(Path(resolved))
    for path in candidates:
        if path.is_file():
            return path
    raise LiveMatrixError("trusted_headless_edge_unavailable")


def _dump_dom(*, artifact_path: Path, profile_root: Path) -> str:
    edge = _edge_path()
    result = subprocess.run(
        [
            str(edge),
            "--headless=new",
            "--disable-gpu",
            "--disable-extensions",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            f"--user-data-dir={profile_root}",
            "--dump-dom",
            artifact_path.as_uri(),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=45,
    )
    if result.returncode != 0 or "<html" not in result.stdout.lower():
        raise LiveMatrixError("trusted_headless_edge_dom_probe_failed")
    if len(result.stdout) > 1024 * 1024:
        raise LiveMatrixError("trusted_headless_edge_dom_budget_exceeded")
    return result.stdout


def _artifact_result(
    *, artifact: RenderedArtifact, artifact_path: Path, profile_root: Path
) -> dict[str, object]:
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with artifact_path.open("xb") as target:
        target.write(artifact.content)
    observed = artifact_path.read_bytes()
    if _sha256(observed) != artifact.sha256:
        raise LiveMatrixError("artifact_digest_verification_failed")
    first_dom = _dump_dom(artifact_path=artifact_path, profile_root=profile_root)
    clock_changed: bool | None = None
    if artifact.filename == "clock.html":
        first = re.search(
            r'<time[^>]*id=["\']clock["\'][^>]*>([^<]+)</time>', first_dom
        )
        time.sleep(1.2)
        second_dom = _dump_dom(artifact_path=artifact_path, profile_root=profile_root)
        second = re.search(
            r'<time[^>]*id=["\']clock["\'][^>]*>([^<]+)</time>', second_dom
        )
        clock_changed = bool(first and second and first.group(1) != second.group(1))
        if not clock_changed:
            raise LiveMatrixError("clock_dom_time_did_not_change")
    return {
        "artifact_type": "clock_html"
        if artifact.filename == "clock.html"
        else "slides_html",
        "filename": artifact.filename,
        "media_type": artifact.media_type,
        "byte_length": len(observed),
        "sha256": artifact.sha256,
        "digest_verified": True,
        "offline_dependency_free": b"http://" not in observed
        and b"https://" not in observed,
        "dom_loaded": True,
        "clock_time_changed": clock_changed,
    }


def _tree_digest(root: Path) -> str:
    try:
        root_metadata = os.lstat(root)
    except OSError as exc:
        raise LiveMatrixError("disposable_workspace_unavailable") from exc
    if stat.S_ISLNK(root_metadata.st_mode) or bool(
        getattr(root_metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    ):
        raise LiveMatrixError("disposable_workspace_contains_link")
    resolved_root = root.resolve(strict=True)
    files: list[Path] = []
    stack = [resolved_root]
    while stack:
        current = stack.pop()
        try:
            children = list(os.scandir(current))
        except OSError as exc:
            raise LiveMatrixError("disposable_workspace_inventory_failed") from exc
        for child in children:
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise LiveMatrixError("disposable_workspace_inventory_failed") from exc
            if child.is_symlink() or bool(
                getattr(metadata, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise LiveMatrixError("disposable_workspace_contains_link")
            path = Path(child.path)
            if child.is_dir(follow_symlinks=False):
                stack.append(path)
            elif child.is_file(follow_symlinks=False):
                files.append(path)
            else:
                raise LiveMatrixError("disposable_workspace_contains_special_file")
    digest = hashlib.sha256()
    for path in sorted(
        files, key=lambda item: item.relative_to(resolved_root).as_posix()
    ):
        relative = path.relative_to(resolved_root).as_posix()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative.encode())
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _workspace_result(
    *, payload: dict[str, object], root: Path, path: str, expected_after: str
) -> dict[str, object]:
    _exact_keys(payload, {"summary", "changes", "tests"}, label="Workspace payload")
    changes = payload["changes"]
    if not isinstance(changes, list) or len(changes) != 1:
        raise LiveMatrixError("Workspace payload must contain one change")
    change = _record(changes[0], label="Workspace change")
    _exact_keys(
        change,
        {"path", "expected_before_sha256", "after_text"},
        label="Workspace change",
    )
    if change["path"] != path or change["after_text"] != expected_after:
        raise LiveMatrixError("Workspace proposal drifted from the Owner allowlist")
    expected_before = _string(
        change["expected_before_sha256"], label="Workspace before digest", maximum=64
    )
    if _SHA256.fullmatch(expected_before) is None:
        raise LiveMatrixError("Workspace before digest invalid")
    tree_before = _tree_digest(root)
    applied = apply_text_change(
        root=root,
        proposal=TextChangeProposal(
            path=path,
            expected_before_sha256=expected_before,
            after_text=expected_after,
        ),
    )
    failure: BaseException | None = None
    tree_applied = ""
    project_check_passed = False
    try:
        tree_applied = _tree_digest(root)
        target = root / Path(*path.split("/"))
        project_check_passed = target.read_text(encoding="utf-8") == expected_after
        if not project_check_passed or tree_applied == tree_before:
            raise LiveMatrixError("Workspace deterministic project check failed")
    except (LiveMatrixError, OSError, UnicodeError, ValueError) as exc:
        failure = exc
    try:
        restored = rollback_text_change(root=root, applied=applied)
    except (OSError, RuntimeError, ValueError) as exc:
        raise LiveMatrixError("Workspace rollback failed") from exc
    tree_rollback = _tree_digest(root)
    if restored != applied.before_sha256 or tree_rollback != tree_before:
        raise LiveMatrixError("Workspace rollback did not restore the original tree")
    if failure is not None:
        raise failure
    return {
        "logical_path": path,
        "before_sha256": applied.before_sha256,
        "after_sha256": applied.after_sha256,
        "tree_before_sha256": tree_before,
        "tree_applied_sha256": tree_applied,
        "tree_rollback_sha256": tree_rollback,
        "disposable_root_verified": True,
        "cas_applied": True,
        "post_write_verified": True,
        "project_check_passed": True,
        "rollback_verified": True,
        "original_tree_restored": True,
    }


class LiveMatrixRunner:
    def __init__(
        self,
        *,
        repo_root: Path,
        work_root: Path,
        coordinates: TargetCoordinates,
        model_id: str,
        access_token: str,
        deepseek_key: str,
    ) -> None:
        self.repo_root = repo_root
        self.work_root = work_root
        self.coordinates = coordinates
        self.model_id = model_id
        self.deepseek_key = deepseek_key
        self.client = BrowserClient(
            base_url=coordinates.base_url,
            access_token=access_token,
        )
        self.credential_id: str | None = None
        self.document_ids: list[str] = []

    def _source_head(self) -> str:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        value = result.stdout.strip()
        if result.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", value) is None:
            raise LiveMatrixError("source_head_unavailable")
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"],
            cwd=self.repo_root,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if status.returncode != 0:
            raise LiveMatrixError("source_cleanliness_unavailable")
        if status.stdout:
            raise LiveMatrixError("source_worktree_not_clean")
        return value

    def _posture(self, *, must_be_active: bool) -> dict[str, object]:
        posture = self.client.json(
            "GET",
            f"/api/v1/workspaces/{self.coordinates.workspace_id}/agent-alpha/status",
        )
        active = posture.get("personal_practice_active") is True
        if active != must_be_active:
            raise LiveMatrixError("personal_practice_posture_unexpected")
        if (
            posture.get("runtime_profile") != "personal_single_owner"
            or posture.get("tools_enabled") is not False
            or posture.get("multi_agent_enabled") is not False
        ):
            raise LiveMatrixError("personal_practice_posture_scope_drift")
        blockers = posture.get("personal_practice_blockers")
        if must_be_active and blockers != []:
            raise LiveMatrixError("personal_practice_posture_has_blockers")
        return {
            "environment": "production",
            "runtime_profile": "personal_single_owner",
            "personal_practice_enabled": active,
            "agent_runtime_enabled": posture.get("personal_runtime_active") is True,
            "agent_planner_enabled": False,
            "enterprise_multi_agent_enabled": False,
            "mcp_runtime_enabled": False,
            "max_concurrent_invocations": 1,
        }

    def _install_provider(self) -> None:
        _deepseek_models_preflight(api_key=self.deepseek_key, model_id=self.model_id)
        credential = self.client.json(
            "POST",
            "/api/v1/model-provider-credentials",
            payload={
                "display_name": "P6.4 disposable DeepSeek acceptance",
                "provider_id": "deepseek",
                "base_url": "https://api.deepseek.com",
                "model_id": self.model_id,
                "api_key": self.deepseek_key,
                "is_default": True,
            },
            expected=(201,),
        )
        self.credential_id = _string(
            credential.get("id"), label="credential id", maximum=64
        )
        tested = self.client.json(
            "POST",
            f"/api/v1/model-provider-credentials/{self.credential_id}/test",
        )
        if (
            tested.get("status") != "passed"
            or tested.get("requested_model_id") != self.model_id
            or tested.get("actual_model_id") != self.model_id
        ):
            raise LiveMatrixError("DeepSeek credential test failed")
        listed = self.client.json("GET", "/api/v1/model-provider-credentials")
        items = listed.get("items")
        if not isinstance(items, list):
            raise LiveMatrixError("provider credential list invalid")
        current = next(
            (
                item
                for item in items
                if isinstance(item, dict) and item.get("id") == self.credential_id
            ),
            None,
        )
        if not isinstance(current, dict):
            raise LiveMatrixError("provider credential disappeared")
        version = _integer(
            current.get("version"), label="credential version", minimum=1
        )
        activated = self.client.json(
            "POST",
            f"/api/v1/model-provider-credentials/{self.credential_id}/activate",
            payload={"expected_version": version, "make_default": True},
        )
        if (
            activated.get("is_active") is not True
            or activated.get("is_default") is not True
        ):
            raise LiveMatrixError("DeepSeek credential activation failed")
        runtime = self.client.json("GET", "/api/v1/model-provider-runtime")
        if (
            runtime.get("credential_source") != "personal"
            or runtime.get("credential_id") != self.credential_id
            or runtime.get("model_id") != self.model_id
        ):
            raise LiveMatrixError(
                "personal Provider runtime did not select the credential"
            )

    def _upload_corpus(self) -> tuple[dict[str, str], frozenset[str]]:
        specifications = (
            (
                self.coordinates.workspace_id,
                "project_codename",
                "p64-project-codename.txt",
                b"OmniBase P6.4 project_codename is ORCHID-417.\n",
                False,
            ),
            (
                self.coordinates.workspace_id,
                "release_channel",
                "p64-release-channel.txt",
                b"OmniBase P6.4 release_channel is LANTERN-82.\n",
                False,
            ),
            (
                self.coordinates.decoy_workspace_id,
                "project_codename",
                "p64-decoy-project-codename.txt",
                b"Conflicting project_codename is COBALT-992.\n",
                True,
            ),
            (
                self.coordinates.decoy_workspace_id,
                "release_channel",
                "p64-decoy-release-channel.txt",
                b"Conflicting release_channel is EMBER-31.\n",
                True,
            ),
        )
        fact_document_ids: dict[str, str] = {}
        decoy_document_ids: set[str] = set()
        for workspace_id, fact_id, filename, content, decoy in specifications:
            uploaded = self.client.upload(
                workspace_id=workspace_id,
                filename=filename,
                content=content,
            )
            document = _record(uploaded.get("document"), label="acceptance document")
            document_id = _string(
                document.get("id"), label="acceptance document id", maximum=64
            )
            self.document_ids.append(document_id)
            self.client.wait_document_indexed(document_id)
            if decoy:
                decoy_document_ids.add(document_id)
            else:
                fact_document_ids[fact_id] = document_id
        if (
            set(fact_document_ids) != {"project_codename", "release_channel"}
            or len(decoy_document_ids) != 2
        ):
            raise LiveMatrixError("acceptance corpus identity is incomplete")
        return fact_document_ids, frozenset(decoy_document_ids)

    def _run_journey(
        self,
        *,
        name: str,
        scenario: str,
        task: str,
        result_builder: Any,
    ) -> dict[str, object]:
        roles = _ROSTERS[name]
        nodes, payload, _ = self.client.practice(
            workspace_id=self.coordinates.workspace_id,
            payload={
                "agent_version_id": self.coordinates.agent_version_id,
                "scenario": scenario,
                "participant_count": len(roles),
                "specialist_roles": list(roles[:-1]),
                "task": task,
                "top_k": 5,
            },
            expected_roles=roles,
        )
        if any(
            node["requested_model_id"] != self.model_id
            or node["actual_model_id"] != self.model_id
            for node in nodes
        ):
            raise LiveMatrixError("live matrix used an unexpected model")
        result = result_builder(payload, nodes)
        return {
            "scenario": scenario,
            "participant_count": len(roles),
            "roles": list(roles),
            "provider_call_count": len(nodes),
            "nodes": nodes,
            "result": result,
            "passed": True,
        }

    def _workspace_fixture(self, name: str) -> tuple[Path, str, str]:
        root = self.work_root / "workspaces" / name
        target = root / "src" / "acceptance.txt"
        target.parent.mkdir(parents=True, exist_ok=False)
        before = f"journey={name}\nstatus=before\n"
        target.write_text(before, encoding="utf-8", newline="\n")
        return root, "src/acceptance.txt", _sha256(before.encode())

    def execute(self) -> dict[str, object]:
        source_head = self._source_head()
        self.work_root.mkdir(parents=False, exist_ok=False)
        during = self._posture(must_be_active=True)
        self._install_provider()
        fact_document_ids, decoy_document_ids = self._upload_corpus()
        journeys: dict[str, object] = {}
        rag_task = (
            "Read only the current Workspace evidence. Return exact JSON with fact_id values "
            "project_codename and release_channel, the exact evidence-bound statements, and "
            "citation_indices using the [n] labels. Do not use unsupported facts or the decoy."
        )
        for name in ("rag_single", "rag_three"):
            journeys[name] = self._run_journey(
                name=name,
                scenario="rag",
                task=rag_task,
                result_builder=lambda payload, nodes: _rag_result(
                    payload=payload,
                    nodes=nodes,
                    fact_document_ids=fact_document_ids,
                    decoy_document_ids=decoy_document_ids,
                ),
            )
        artifact_specs = {
            "artifact_single": (
                "Return artifact_type exactly clock_html, title exactly P6.4 Clock, "
                'specification exactly {"accent":"#2255aa"}, and bounded acceptance_checks.',
                "clock.html",
            ),
            "artifact_four": (
                "Return artifact_type exactly slides_html, title exactly OmniBase P6.4, and a "
                "specification with 3 slides. Each slide has only heading and bullets; use no "
                "HTML, URL, external dependency, image, macro or PPTX claim.",
                "slides.html",
            ),
        }
        for name, (task, filename) in artifact_specs.items():
            artifact_path = self.work_root / "artifacts" / name / filename
            profile_root = self.work_root / "browser-profiles" / name
            profile_root.mkdir(parents=True, exist_ok=False)
            journeys[name] = self._run_journey(
                name=name,
                scenario="artifact",
                task=task,
                result_builder=lambda payload,
                _nodes,
                artifact_path=artifact_path,
                profile_root=profile_root: _artifact_result(
                    artifact=_render_artifact(payload),
                    artifact_path=artifact_path,
                    profile_root=profile_root,
                ),
            )
        workspace_specs = {
            "workspace_single": "status=after-single\nP6_4_ACCEPTED=true\n",
            "workspace_six": "status=after-six\nP6_4_ACCEPTED=true\n",
        }
        for name, expected_after in workspace_specs.items():
            root, logical_path, before_sha256 = self._workspace_fixture(name)
            task = (
                "Return exactly one Workspace change proposal. Path must be "
                f"{logical_path}; expected_before_sha256 must be {before_sha256}; after_text "
                f"must be exactly {json.dumps(expected_after)}. Do not add another file, path, "
                "tool, command, URL or dependency."
            )
            journeys[name] = self._run_journey(
                name=name,
                scenario="workspace",
                task=task,
                result_builder=lambda payload,
                _nodes,
                root=root,
                logical_path=logical_path,
                expected_after=expected_after: _workspace_result(
                    payload=payload,
                    root=root,
                    path=logical_path,
                    expected_after=expected_after,
                ),
            )
        return {
            "schema": MATRIX_SCHEMA,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source_head": source_head,
            "provider": {
                "provider_id": "deepseek",
                "model_id": self.model_id,
                "models_preflight_passed": True,
            },
            "during_posture": during,
            "journeys": journeys,
            "cleanup": {
                "disposable_documents_removed": False,
                "provider_credential_revoked": False,
                "disposable_target_cleanup_pending": True,
            },
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "live_matrix_completed": True,
            "production_accepted": False,
        }

    def cleanup_browser_state(
        self, matrix: dict[str, object] | None
    ) -> tuple[str, ...]:
        errors: list[str] = []
        for document_id in reversed(self.document_ids):
            try:
                self.client.delete(f"/api/v1/documents/{document_id}", expected=(200,))
                self.client.expect_status(
                    "GET",
                    f"/api/v1/documents/{document_id}",
                    expected=(404,),
                )
            except LiveMatrixError:
                errors.append("disposable_document_cleanup_failed")
        if self.credential_id:
            try:
                self.client.delete(
                    f"/api/v1/model-provider-credentials/{self.credential_id}",
                    expected=(204,),
                )
                listed = self.client.json("GET", "/api/v1/model-provider-credentials")
                items = listed.get("items")
                if not isinstance(items, list):
                    raise LiveMatrixError("provider_credential_list_invalid")
                current = next(
                    (
                        item
                        for item in items
                        if isinstance(item, dict)
                        and item.get("id") == self.credential_id
                    ),
                    None,
                )
                if not isinstance(current, dict) or (
                    current.get("is_active") is not False
                    or current.get("revoked_at") is None
                ):
                    raise LiveMatrixError("provider_credential_revocation_unverified")
            except LiveMatrixError:
                errors.append("provider_credential_cleanup_failed")
        if matrix is not None:
            cleanup = _record(matrix.get("cleanup"), label="matrix cleanup")
            cleanup["disposable_documents_removed"] = not any(
                error == "disposable_document_cleanup_failed" for error in errors
            )
            cleanup["provider_credential_revoked"] = not any(
                error == "provider_credential_cleanup_failed" for error in errors
            )
        return tuple(errors)


def _absolute_path(value: str, *, label: str, must_exist: bool) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise LiveMatrixError(f"{label}_must_be_absolute")
    try:
        return path.resolve(strict=must_exist)
    except OSError as exc:
        raise LiveMatrixError(f"{label}_unavailable") from exc


def _is_within(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def _validate_repo_root(value: str) -> Path:
    repo_root = _absolute_path(value, label="repo_root", must_exist=True)
    if repo_root != REPO_ROOT.resolve(strict=True):
        raise LiveMatrixError("repo_root_must_match_runner_source")
    if not (repo_root / ".git").exists():
        raise LiveMatrixError("repo_root_git_identity_missing")
    return repo_root


def _validate_work_root(value: str, *, repo_root: Path) -> Path:
    work_root = _absolute_path(value, label="work_root", must_exist=False)
    if work_root.exists():
        raise LiveMatrixError("work_root_must_not_exist")
    if not work_root.name.startswith("omnibase-p64-"):
        raise LiveMatrixError("work_root_name_invalid")
    if _is_within(work_root, repo_root) or _is_within(repo_root, work_root):
        raise LiveMatrixError("work_root_must_be_outside_repo")
    if not work_root.parent.is_dir():
        raise LiveMatrixError("work_root_parent_missing")
    return work_root


def _validate_output(value: str, *, work_root: Path) -> Path:
    output = _absolute_path(value, label="output", must_exist=False)
    if output.exists() or output.suffix.lower() != ".json":
        raise LiveMatrixError("output_path_invalid")
    if output == work_root or not _is_within(output, work_root):
        raise LiveMatrixError("output_must_be_inside_work_root")
    return output


def _validate_coordinates(
    args: argparse.Namespace,
) -> tuple[Path, Path, Path, TargetCoordinates]:
    repo_root = _validate_repo_root(args.repo_root)
    work_root = _validate_work_root(args.work_root, repo_root=repo_root)
    output = _validate_output(args.output, work_root=work_root)
    for label in ("workspace_id", "decoy_workspace_id", "agent_version_id"):
        if _UUID.fullmatch(getattr(args, label)) is None:
            raise LiveMatrixError(f"{label}_invalid")
    if args.workspace_id == args.decoy_workspace_id:
        raise LiveMatrixError("decoy_workspace_must_be_distinct")
    if args.model_id not in _ALLOWED_MODELS:
        raise LiveMatrixError("model_id_not_allowed")
    coordinates = TargetCoordinates(
        base_url=_validate_loopback_url(args.base_url),
        workspace_id=args.workspace_id,
        decoy_workspace_id=args.decoy_workspace_id,
        agent_version_id=args.agent_version_id,
    )
    return repo_root, work_root, output, coordinates


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--workspace-id", required=True)
    parser.add_argument("--decoy-workspace-id", required=True)
    parser.add_argument("--agent-version-id", required=True)
    parser.add_argument("--model-id", choices=sorted(_ALLOWED_MODELS), required=True)
    parser.add_argument("--output", required=True)
    return parser.parse_args(argv)


def _failure_payload(
    code: str, *, cleanup_errors: tuple[str, ...]
) -> dict[str, object]:
    return {
        "schema": MATRIX_SCHEMA,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "state": "failed/veto",
        "error_code": code,
        "cleanup_errors": list(cleanup_errors),
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "live_matrix_completed": False,
        "production_accepted": False,
    }


def _safe_error_code(exc: BaseException) -> str:
    if not isinstance(exc, LiveMatrixError):
        return "local_execution_failed"
    value = re.sub(r"[^a-z0-9]+", "_", str(exc).lower()).strip("_")
    return value[:160] or "live_matrix_failed"


def _write_output(output: Path, payload: dict[str, object]) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = _canonical(payload)
    with output.open("xb") as target:
        target.write(raw)
    return _sha256(raw)


def main(argv: list[str] | None = None) -> int:
    try:
        args = _args(argv)
        repo_root, work_root, output, coordinates = _validate_coordinates(args)
        access_token = os.environ.get(ACCESS_TOKEN_ENV, "")
        deepseek_key = os.environ.get(DEEPSEEK_KEY_ENV, "")
        if not access_token:
            raise LiveMatrixError(f"{ACCESS_TOKEN_ENV} is empty")
        if not deepseek_key:
            raise LiveMatrixError(f"{DEEPSEEK_KEY_ENV} is empty")
    except (LiveMatrixError, OSError, ValueError) as exc:
        print(
            _canonical(
                {"state": "failed/veto", "error_code": _safe_error_code(exc)}
            ).decode(),
            end="",
        )
        return 2

    runner = LiveMatrixRunner(
        repo_root=repo_root,
        work_root=work_root,
        coordinates=coordinates,
        model_id=args.model_id,
        access_token=access_token,
        deepseek_key=deepseek_key,
    )
    matrix: dict[str, object] | None = None
    failure: BaseException | None = None
    try:
        matrix = runner.execute()
    except (LiveMatrixError, OSError, subprocess.SubprocessError, ValueError) as exc:
        failure = exc
    cleanup_errors = runner.cleanup_browser_state(matrix)
    if matrix is None:
        payload = _failure_payload(
            _safe_error_code(failure or LiveMatrixError("live_matrix_failed")),
            cleanup_errors=cleanup_errors,
        )
        exit_code = 1
    elif cleanup_errors:
        payload = {
            **matrix,
            "state": "failed/veto",
            "error_code": "browser_state_cleanup_failed",
            "cleanup_errors": list(cleanup_errors),
            "production_accepted": False,
        }
        exit_code = 1
    else:
        payload = matrix
        exit_code = 0
    payload["production_accepted"] = False
    try:
        digest = _write_output(output, payload)
    except OSError:
        print(
            _canonical(
                {
                    "state": "failed/veto",
                    "error_code": "matrix_output_write_failed",
                }
            ).decode(),
            end="",
        )
        return 1
    print(
        _canonical(
            {
                "state": "matrix-complete" if exit_code == 0 else "failed/veto",
                "output_name": output.name,
                "output_sha256": digest,
                "production_accepted": False,
            }
        ).decode(),
        end="",
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
