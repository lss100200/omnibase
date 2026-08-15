"""Closed contracts for the P6.4 personal Agent practice lane.

The lane is deliberately smaller than the Phase 5 Planner/Multi-Agent system.
One Owner request creates one request-scoped roster.  Every roster member is a
real, separately metered Model Gateway call; members cannot wake themselves,
spawn descendants, use tools, call MCP, or write files.  The parent call is
the final synthesizer and is included in the requested participant count.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from omnibase.model_gateway import ModelGateway

PracticeScenario = Literal["rag", "artifact", "workspace"]
PracticeStatus = Literal["completed", "partial", "failed"]
ParticipantRole = Literal[
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
]


@dataclass(frozen=True, slots=True)
class EvidenceChunk:
    chunk_id: str
    document_id: str
    content: str
    page_number: int = 1

    def __post_init__(self) -> None:
        if not self.chunk_id or not self.document_id or not self.content.strip():
            raise ValueError("practice_evidence_invalid")
        if self.page_number < 1:
            raise ValueError("practice_evidence_page_invalid")


@dataclass(frozen=True, slots=True)
class CitationClaim:
    fact_id: str
    statement: str
    citation_chunk_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.fact_id or not self.statement.strip():
            raise ValueError("practice_claim_invalid")
        if len(set(self.citation_chunk_ids)) != len(self.citation_chunk_ids):
            raise ValueError("practice_claim_duplicate_citation")


@dataclass(frozen=True, slots=True)
class PracticeLimits:
    max_task_characters: int = 16_000
    max_context_characters: int = 24_000
    max_output_tokens_per_call: int = 1_200
    max_total_output_tokens: int = 7_200
    max_total_calls: int = 6
    timeout_seconds_per_call: float = 75.0

    def __post_init__(self) -> None:
        if (
            min(
                self.max_task_characters,
                self.max_context_characters,
                self.max_output_tokens_per_call,
                self.max_total_output_tokens,
                self.max_total_calls,
            )
            < 1
        ):
            raise ValueError("practice_limits_invalid")
        if self.timeout_seconds_per_call <= 0:
            raise ValueError("practice_limits_invalid")


@dataclass(frozen=True, slots=True)
class PracticeRequest:
    scenario: PracticeScenario
    participant_count: int
    task: str
    evidence: tuple[EvidenceChunk, ...] = ()
    allowed_paths: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.participant_count not in {1, 3, 4, 5, 6}:
            raise ValueError("practice_participant_count_invalid")
        if not self.task.strip():
            raise ValueError("practice_task_empty")
        if self.scenario == "rag" and not self.evidence:
            raise ValueError("practice_rag_evidence_required")
        if self.scenario == "workspace" and not self.allowed_paths:
            raise ValueError("practice_workspace_paths_required")
        if len(set(self.allowed_paths)) != len(self.allowed_paths):
            raise ValueError("practice_workspace_paths_duplicate")


@dataclass(frozen=True, slots=True)
class AgentContribution:
    role: ParticipantRole
    requested_model_id: str
    actual_model_id: str
    content: str
    finish_reason: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cached_input_tokens: int


@dataclass(frozen=True, slots=True)
class PracticeResult:
    scenario: PracticeScenario
    participant_count: int
    status: PracticeStatus
    contributions: tuple[AgentContribution, ...]
    final_payload: dict[str, object] | None
    blockers: tuple[str, ...]


class RoleGatewayResolver(Protocol):
    """Resolve one already-authorized gateway for one request-scoped role."""

    def resolve(self, role: ParticipantRole) -> ModelGateway: ...


__all__ = [
    "AgentContribution",
    "CitationClaim",
    "EvidenceChunk",
    "ParticipantRole",
    "PracticeLimits",
    "PracticeRequest",
    "PracticeResult",
    "PracticeScenario",
    "PracticeStatus",
    "RoleGatewayResolver",
]
