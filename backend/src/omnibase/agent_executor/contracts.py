"""Typed, server-owned contracts for the first P5.4 single-Agent tool.

P5.4A intentionally exposes one read-only logical capability only.  The
contracts in this module contain no database locator, provider credential,
Browser token, process handle or host-specific execution detail.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Protocol

# ``knowledge_search`` is the immutable P5.3A tool binding.  The capability
# gateway-facing name is kept separate so the planner contract does not need
# to expose transport or adapter details.
KNOWLEDGE_SEARCH_TOOL_ID = "knowledge_search"
KNOWLEDGE_SEARCH_CAPABILITY = "workspace.knowledge.search"
_MAX_QUERY_LENGTH = 500
_MAX_TOP_K = 20
_MAX_TIMEOUT_MS = 5_000
_MAX_BYTES = 1_048_576


class ExecutorContractError(ValueError):
    """A caller supplied or server supplied executor contract is unsafe."""


def _required_string(value: object, *, name: str, max_length: int = 256) -> str:
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise ExecutorContractError(f"{name} must be a non-empty bounded string")
    return value


def _logical_uuid(value: object, *, name: str) -> str:
    result = _required_string(value, name=name, max_length=64)
    # Public contracts carry opaque logical identifiers.  Reject separators,
    # whitespace and control characters without exposing a physical locator.
    if any(ch.isspace() or ord(ch) < 32 for ch in result):
        raise ExecutorContractError(f"{name} must be a logical identifier")
    return result


def _digest(value: object, *, name: str) -> str:
    result = _required_string(value, name=name, max_length=128)
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ExecutorContractError(f"{name} must be a lowercase SHA-256 digest")
    return result


def _bounded_int(value: object, *, name: str, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise ExecutorContractError(f"{name} must be an integer in [{minimum}, {maximum}]")
    return value


def _canonical_digest(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutorInvocationContext:
    """Server-owned identity bound to one immutable plan node execution."""

    tenant_id: str
    workspace_id: str
    workspace_generation: int
    actor_user_id: str
    task_id: str
    task_generation: int
    run_id: str
    run_fencing_token: int
    agent_version_id: str
    agent_version_digest: str
    proposal_digest: str
    node_id: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "workspace_id",
            "actor_user_id",
            "task_id",
            "run_id",
            "agent_version_id",
            "node_id",
        ):
            _logical_uuid(getattr(self, name), name=name)
        for name in ("workspace_generation", "task_generation", "run_fencing_token"):
            _bounded_int(getattr(self, name), name=name, minimum=1, maximum=2**63 - 1)
        _digest(self.agent_version_digest, name="agent_version_digest")
        _digest(self.proposal_digest, name="proposal_digest")

    def to_dict(self) -> dict[str, object]:
        return {
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "workspace_generation": self.workspace_generation,
            "actor_user_id": self.actor_user_id,
            "task_id": self.task_id,
            "task_generation": self.task_generation,
            "run_id": self.run_id,
            "run_fencing_token": self.run_fencing_token,
            "agent_version_id": self.agent_version_id,
            "agent_version_digest": self.agent_version_digest,
            "proposal_digest": self.proposal_digest,
            "node_id": self.node_id,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeSearchRequest:
    """Bounded logical request for ``workspace.knowledge.search``."""

    resource_id: str
    query: str
    top_k: int = 10
    timeout_ms: int = 3_000
    max_bytes: int = 262_144

    def __post_init__(self) -> None:
        _logical_uuid(self.resource_id, name="resource_id")
        _required_string(self.query, name="query", max_length=_MAX_QUERY_LENGTH)
        _bounded_int(self.top_k, name="top_k", minimum=1, maximum=_MAX_TOP_K)
        _bounded_int(self.timeout_ms, name="timeout_ms", minimum=1, maximum=_MAX_TIMEOUT_MS)
        _bounded_int(self.max_bytes, name="max_bytes", minimum=1, maximum=_MAX_BYTES)

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "query": self.query,
            "top_k": self.top_k,
            "timeout_ms": self.timeout_ms,
            "max_bytes": self.max_bytes,
        }

    @property
    def request_digest(self) -> str:
        return _canonical_digest(self.to_dict())


@dataclass(frozen=True, slots=True)
class KnowledgeSearchHit:
    citation_id: str
    document_id: str
    score: float
    snippet: str | None = None
    page_number: int | None = None

    def __post_init__(self) -> None:
        _logical_uuid(self.citation_id, name="citation_id")
        _logical_uuid(self.document_id, name="document_id")
        if not isinstance(self.score, float) or not math.isfinite(self.score):
            raise ExecutorContractError("score must be a finite float")
        if self.snippet is not None:
            _required_string(self.snippet, name="snippet", max_length=10_000)
        if self.page_number is not None:
            _bounded_int(self.page_number, name="page_number", minimum=1, maximum=2**31 - 1)

    def to_dict(self) -> dict[str, object]:
        return {
            "citation_id": self.citation_id,
            "document_id": self.document_id,
            "score": self.score,
            "snippet": self.snippet,
            "page_number": self.page_number,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeSearchResult:
    resource_id: str
    results: tuple[KnowledgeSearchHit, ...]
    bytes_out: int
    truncated: bool

    def __post_init__(self) -> None:
        _logical_uuid(self.resource_id, name="resource_id")
        _bounded_int(self.bytes_out, name="bytes_out", minimum=0, maximum=_MAX_BYTES)
        if not isinstance(self.truncated, bool):
            raise ExecutorContractError("truncated must be a boolean")

    def to_dict(self) -> dict[str, object]:
        return {
            "resource_id": self.resource_id,
            "results": [item.to_dict() for item in self.results],
            "bytes_out": self.bytes_out,
            "truncated": self.truncated,
        }

    @property
    def result_digest(self) -> str:
        return _canonical_digest(self.to_dict())


class KnowledgeSearchPort(Protocol):
    """Capability-Gateway-backed logical read port."""

    def search(
        self,
        *,
        context: ExecutorInvocationContext,
        request: KnowledgeSearchRequest,
    ) -> KnowledgeSearchResult: ...


@dataclass(frozen=True, slots=True)
class ExecutorToolReceipt:
    tool_id: str
    capability: str
    request_digest: str
    result_digest: str
    effect_class: str
    status: str

    def __post_init__(self) -> None:
        if self.tool_id != KNOWLEDGE_SEARCH_TOOL_ID:
            raise ExecutorContractError("unknown executor tool")
        if self.capability != KNOWLEDGE_SEARCH_CAPABILITY:
            raise ExecutorContractError("unknown executor capability")
        for name in ("request_digest", "result_digest"):
            _digest(getattr(self, name), name=name)
        if self.effect_class != "read_only":
            raise ExecutorContractError("P5.4A tool effect must be read_only")
        if self.status != "succeeded":
            raise ExecutorContractError("P5.4A receipt status must be succeeded")

    def to_dict(self) -> dict[str, str]:
        return {
            "tool_id": self.tool_id,
            "capability": self.capability,
            "request_digest": self.request_digest,
            "result_digest": self.result_digest,
            "effect_class": self.effect_class,
            "status": self.status,
        }


@dataclass(frozen=True, slots=True)
class ExecutorNodeResult:
    proposal_digest: str
    node_id: str
    output: KnowledgeSearchResult
    receipt: ExecutorToolReceipt

    def __post_init__(self) -> None:
        _digest(self.proposal_digest, name="proposal_digest")
        _logical_uuid(self.node_id, name="node_id")

    def to_dict(self) -> dict[str, object]:
        return {
            "proposal_digest": self.proposal_digest,
            "node_id": self.node_id,
            "output": self.output.to_dict(),
            "receipt": self.receipt.to_dict(),
        }


__all__ = [
    "KNOWLEDGE_SEARCH_CAPABILITY",
    "KNOWLEDGE_SEARCH_TOOL_ID",
    "ExecutorContractError",
    "ExecutorInvocationContext",
    "ExecutorNodeResult",
    "ExecutorToolReceipt",
    "KnowledgeSearchHit",
    "KnowledgeSearchPort",
    "KnowledgeSearchRequest",
    "KnowledgeSearchResult",
]
