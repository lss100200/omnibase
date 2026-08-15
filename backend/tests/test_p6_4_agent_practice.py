from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest

from omnibase.agent_alpha.contracts import AlphaStreamEvent
from omnibase.agent_alpha.service import AgentAlphaUnavailable
from omnibase.agent_practice.alpha_coordinator import DurablePersonalPracticeCoordinator
from omnibase.agent_practice.artifacts import render_clock_html, render_slide_deck_html
from omnibase.agent_practice.changesets import (
    TextChangeProposal,
    apply_text_change,
    rollback_text_change,
)
from omnibase.agent_practice.contracts import (
    CitationClaim,
    EvidenceChunk,
    ParticipantRole,
    PracticeRequest,
)
from omnibase.agent_practice.posture import personal_practice_posture
from omnibase.agent_practice.scoring import ExpectedFact, score_citations
from omnibase.agent_practice.service import PersonalAgentPracticeService
from omnibase.model_gateway import (
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
    ModelUsage,
)
from omnibase.model_gateway.providers import ModelProviderError


class _Provider:
    provider_id = "fixture"

    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        if len(self.calls) == 3:
            content = json.dumps(
                {
                    "answer": "ORCHID-417",
                    "claims": [
                        {
                            "fact_id": "recovery_code",
                            "statement": "The recovery code is ORCHID-417.",
                            "citation_chunk_ids": ["chunk-a"],
                        }
                    ],
                    "abstained": False,
                }
            )
        else:
            content = json.dumps(
                {
                    "role": "fixture",
                    "observations": ["The marker occurs in chunk-a."],
                    "recommendations": [],
                    "blockers": [],
                    "references": ["chunk-a"],
                }
            )
        return ModelResponse(
            provider_id=self.provider_id,
            requested_model_id=request.model_id,
            actual_model_id=request.model_id,
            content=content,
            finish_reason="stop",
            usage=ModelUsage(input_tokens=10, output_tokens=10, total_tokens=20),
            latency_ms=1,
        )

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        del request
        return iter(())


class _FailingProvider(_Provider):
    def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        raise ModelProviderError("sanitized fixture failure")


class _Resolver:
    def __init__(self, provider: _Provider) -> None:
        self._gateway = ModelGateway(
            provider=provider,
            model_id="deepseek-v4-flash",
            max_concurrency=1,
        )
        self.roles: list[ParticipantRole] = []

    def resolve(self, role: ParticipantRole) -> ModelGateway:
        self.roles.append(role)
        return self._gateway


class _AlphaInvoker:
    def __init__(
        self,
        *,
        fail_at: int | None = None,
        omit_usage_at: int | None = None,
        omit_citations_at: int | None = None,
        mismatched_model_at: int | None = None,
        specialist_answer_characters: int = 0,
        duplicate_meta_at: int | None = None,
        reuse_identity_at: int | None = None,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_at = fail_at
        self.omit_usage_at = omit_usage_at
        self.omit_citations_at = omit_citations_at
        self.mismatched_model_at = mismatched_model_at
        self.specialist_answer_characters = specialist_answer_characters
        self.duplicate_meta_at = duplicate_meta_at
        self.reuse_identity_at = reuse_identity_at

    def invoke(self, **kwargs: object) -> Iterator[AlphaStreamEvent]:
        self.calls.append(kwargs)
        ordinal = len(self.calls)
        identity_ordinal = 1 if self.reuse_identity_at == ordinal else ordinal
        role = str(kwargs["employee_role_id"])
        meta = AlphaStreamEvent(
            kind="meta",
            payload={
                "invocation_id": f"00000000-0000-0000-0000-{identity_ordinal:012d}",
                "task_id": f"10000000-0000-0000-0000-{identity_ordinal:012d}",
                "requested_model_id": "deepseek-v4-flash",
            },
        )
        yield meta
        if self.duplicate_meta_at == ordinal:
            yield meta
        if self.omit_citations_at != ordinal:
            yield AlphaStreamEvent(
                kind="citations",
                payload={
                    "citations": [
                        {
                            "index": 1,
                            "chunk_id": f"chunk-{ordinal}",
                            "document_id": f"document-{ordinal}",
                            "snippet": "must not enter the practice receipt",
                            "page_number": 1,
                            "score": 0.99,
                        }
                    ]
                },
            )
        if self.fail_at == ordinal:
            yield AlphaStreamEvent(kind="error", payload={"code": "fixture_unknown"})
            return
        answer = (
            "S" * self.specialist_answer_characters
            if role != "parent" and self.specialist_answer_characters
            else json.dumps({"role": role, "ok": True})
        )
        if self.omit_usage_at != ordinal:
            yield AlphaStreamEvent(
                kind="usage",
                payload={
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "total_tokens": 15,
                    "reasoning_tokens": 0,
                    "cached_input_tokens": 0,
                    "cache_miss_input_tokens": 10,
                },
            )
        yield AlphaStreamEvent(
            kind="done",
            payload={
                "answer": answer,
                "actual_model_id": (
                    "deepseek-v4-pro"
                    if self.mismatched_model_at == ordinal
                    else "deepseek-v4-flash"
                ),
            },
        )


def _rag_request(participant_count: int) -> PracticeRequest:
    return PracticeRequest(
        scenario="rag",
        participant_count=participant_count,
        task="Return the recovery code with an exact citation.",
        evidence=(
            EvidenceChunk(
                chunk_id="chunk-a",
                document_id="document-a",
                content="Project Amber recovery code is ORCHID-417.",
            ),
        ),
    )


def test_three_agent_practice_uses_three_independent_gateway_calls() -> None:
    provider = _Provider()
    resolver = _Resolver(provider)

    result = PersonalAgentPracticeService(gateways=resolver).execute(_rag_request(3))

    assert result.status == "completed"
    assert resolver.roles == ["data", "qa", "parent"]
    assert len(provider.calls) == 3
    assert len(result.contributions) == 3
    assert all(
        item.requested_model_id == item.actual_model_id == "deepseek-v4-flash"
        for item in result.contributions
    )


def test_direct_practice_stops_after_first_provider_failure() -> None:
    provider = _FailingProvider()
    resolver = _Resolver(provider)

    result = PersonalAgentPracticeService(gateways=resolver).execute(_rag_request(3))

    assert result.status == "failed"
    assert result.blockers == ("practice_provider_failed:data",)
    assert resolver.roles == ["data"]
    assert len(provider.calls) == 1


def test_durable_coordinator_runs_three_existing_alpha_invocations_serially() -> None:
    invoker = _AlphaInvoker()
    events = list(
        DurablePersonalPracticeCoordinator(invoker).run(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="owner",
            agent_version_id="version",
            scenario="rag",
            specialist_roles=("data", "qa"),
            task="Answer from uploaded Workspace evidence.",
            top_k=5,
            idempotency_key="owner-request",
        )
    )

    assert [call["employee_role_id"] for call in invoker.calls] == ["data", "qa", "parent"]
    assert len({str(call["idempotency_key"]) for call in invoker.calls}) == 3
    completed = [event for event in events if event.kind == "practice_completed"]
    assert completed[0].payload["provider_call_count"] == 3
    assert completed[0].payload["participant_count"] == 3


def test_durable_coordinator_stops_after_unknown_member() -> None:
    invoker = _AlphaInvoker(fail_at=2)
    coordinator = DurablePersonalPracticeCoordinator(invoker)

    with pytest.raises(RuntimeError, match="practice_node_terminal_failure:qa"):
        list(
            coordinator.run(
                tenant_id="tenant",
                tenant_schema="tenant_schema",
                workspace_id="workspace",
                actor_user_id="owner",
                agent_version_id="version",
                scenario="rag",
                specialist_roles=("data", "qa"),
                task="Answer from evidence.",
                top_k=5,
                idempotency_key="owner-request",
            )
        )

    assert [call["employee_role_id"] for call in invoker.calls] == ["data", "qa"]


class _SynchronousFailingAlphaInvoker(_AlphaInvoker):
    def __init__(self, code: str) -> None:
        super().__init__()
        self.code = code

    def invoke(self, **kwargs: object) -> Iterator[AlphaStreamEvent]:
        self.calls.append(kwargs)
        raise AgentAlphaUnavailable(self.code)


@pytest.mark.parametrize(
    ("code", "projected"),
    [
        ("personal_runtime_scope_mismatch", "personal_runtime_scope_mismatch"),
        ("agent_alpha_context_unavailable", "agent_alpha_context_unavailable"),
        ("unsafe-detail:C:/secret", "agent_alpha_error"),
    ],
)
def test_durable_coordinator_projects_only_stable_synchronous_node_failures(
    code: str,
    projected: str,
) -> None:
    invoker = _SynchronousFailingAlphaInvoker(code)

    with pytest.raises(
        RuntimeError,
        match=rf"^practice_node_terminal_failure:parent:{projected}$",
    ):
        list(
            DurablePersonalPracticeCoordinator(invoker).run(
                tenant_id="tenant",
                tenant_schema="tenant_schema",
                workspace_id="workspace",
                actor_user_id="owner",
                agent_version_id="version",
                scenario="rag",
                specialist_roles=(),
                task="Answer from evidence.",
                top_k=5,
                idempotency_key="owner-request",
            )
        )

    assert [call["employee_role_id"] for call in invoker.calls] == ["parent"]


def test_durable_coordinator_rejects_duplicate_meta_before_next_member() -> None:
    invoker = _AlphaInvoker(duplicate_meta_at=1)

    with pytest.raises(RuntimeError, match="practice_node_meta_duplicate_or_late:data"):
        list(
            DurablePersonalPracticeCoordinator(invoker).run(
                tenant_id="tenant",
                tenant_schema="tenant_schema",
                workspace_id="workspace",
                actor_user_id="owner",
                agent_version_id="version",
                scenario="rag",
                specialist_roles=("data", "qa"),
                task="Answer from evidence.",
                top_k=5,
                idempotency_key="owner-request",
            )
        )

    assert [call["employee_role_id"] for call in invoker.calls] == ["data"]


def test_durable_coordinator_rejects_cross_node_identity_reuse() -> None:
    invoker = _AlphaInvoker(reuse_identity_at=2)

    with pytest.raises(RuntimeError, match="practice_node_identity_reused:qa"):
        list(
            DurablePersonalPracticeCoordinator(invoker).run(
                tenant_id="tenant",
                tenant_schema="tenant_schema",
                workspace_id="workspace",
                actor_user_id="owner",
                agent_version_id="version",
                scenario="rag",
                specialist_roles=("data", "qa"),
                task="Answer from evidence.",
                top_k=5,
                idempotency_key="owner-request",
            )
        )

    assert [call["employee_role_id"] for call in invoker.calls] == ["data", "qa"]


def test_parent_projection_caps_each_untrusted_specialist_result() -> None:
    invoker = _AlphaInvoker(specialist_answer_characters=10_000)

    list(
        DurablePersonalPracticeCoordinator(invoker).run(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="owner",
            agent_version_id="version",
            scenario="workspace",
            specialist_roles=("product", "frontend", "backend", "security", "qa"),
            task="x" * 16_000,
            top_k=5,
            idempotency_key="owner-request",
        )
    )

    parent_message = str(invoker.calls[-1]["message"])
    assert len(parent_message) <= 32_000
    assert "S" * 2_001 not in parent_message


@pytest.mark.parametrize(
    ("invoker", "message"),
    [
        (_AlphaInvoker(omit_citations_at=2), "practice_node_citations_missing:qa"),
        (_AlphaInvoker(omit_usage_at=2), "practice_node_usage_missing:qa"),
        (
            _AlphaInvoker(mismatched_model_at=2),
            "practice_node_model_identity_mismatch:qa",
        ),
    ],
)
def test_durable_coordinator_stops_on_unverifiable_node_receipt(
    invoker: _AlphaInvoker,
    message: str,
) -> None:
    with pytest.raises(RuntimeError, match=message):
        list(
            DurablePersonalPracticeCoordinator(invoker).run(
                tenant_id="tenant",
                tenant_schema="tenant_schema",
                workspace_id="workspace",
                actor_user_id="owner",
                agent_version_id="version",
                scenario="rag",
                specialist_roles=("data", "qa"),
                task="Answer from evidence.",
                top_k=5,
                idempotency_key="owner-request",
            )
        )

    assert [call["employee_role_id"] for call in invoker.calls] == ["data", "qa"]


@pytest.mark.parametrize("count", [2, 7])
def test_practice_rejects_counts_outside_single_or_three_to_six(count: int) -> None:
    with pytest.raises(ValueError, match="practice_participant_count_invalid"):
        _rag_request(count)


def test_exact_citation_score_passes() -> None:
    evidence = (
        EvidenceChunk("chunk-a", "document-a", "Code ORCHID-417"),
        EvidenceChunk("chunk-decoy", "document-b", "Code COBALT-992"),
    )
    result = score_citations(
        claims=(
            CitationClaim(
                "recovery_code",
                "The recovery code is ORCHID-417.",
                ("chunk-a",),
            ),
        ),
        expected_facts=(ExpectedFact("recovery_code", frozenset({"chunk-a"}), "ORCHID-417"),),
        evidence=evidence,
    )

    assert result.passed is True
    assert result.fact_precision == result.fact_recall == 1.0
    assert result.citation_precision == result.citation_recall == 1.0
    assert result.unsupported_claim_count == result.wrong_chunk_count == 0


def test_wrong_document_citation_fails() -> None:
    result = score_citations(
        claims=(CitationClaim("fact", "claim", ("decoy",)),),
        expected_facts=(ExpectedFact("fact", frozenset({"right"})),),
        evidence=(
            EvidenceChunk("right", "document-a", "right"),
            EvidenceChunk("decoy", "document-b", "wrong"),
        ),
    )

    assert result.passed is False
    assert result.wrong_chunk_count == 1


def test_right_fact_id_and_chunk_with_wrong_statement_still_fails() -> None:
    result = score_citations(
        claims=(CitationClaim("recovery_code", "The code is COBALT-992.", ("right",)),),
        expected_facts=(ExpectedFact("recovery_code", frozenset({"right"}), "ORCHID-417"),),
        evidence=(EvidenceChunk("right", "document-a", "Code ORCHID-417"),),
    )

    assert result.passed is False
    assert result.statement_mismatch_count == 1


def test_duplicate_fact_claims_fail_closed() -> None:
    claim = CitationClaim("recovery_code", "ORCHID-417", ("right",))
    with pytest.raises(ValueError, match="practice_claim_fact_duplicate"):
        score_citations(
            claims=(claim, claim),
            expected_facts=(ExpectedFact("recovery_code", frozenset({"right"})),),
            evidence=(EvidenceChunk("right", "document-a", "Code ORCHID-417"),),
        )


def test_rendered_clock_is_offline_and_escaped() -> None:
    artifact = render_clock_html(title='<script src="https://bad"></script>')
    text = artifact.content.decode()

    assert artifact.filename == "clock.html"
    assert artifact.sha256 == hashlib.sha256(artifact.content).hexdigest()
    assert "setInterval(tick, 1000)" in text
    assert '<script src="https://bad">' not in text
    assert 'src="http' not in text
    assert 'href="http' not in text


def test_slide_deck_is_real_offline_html_not_claimed_as_pptx() -> None:
    artifact = render_slide_deck_html(
        title="P6.4",
        slides=(("Goal", ("RAG", "Artifact", "Workspace")),),
    )

    assert artifact.filename == "slides.html"
    assert artifact.media_type.startswith("text/html")
    assert b"<h2>Goal</h2>" in artifact.content


def test_changeset_applies_and_rolls_back_exactly(tmp_path: Path) -> None:
    target = tmp_path / "clock.html"
    target.write_text("before\n", encoding="utf-8")
    before = hashlib.sha256(target.read_bytes()).hexdigest()

    applied = apply_text_change(
        root=tmp_path,
        proposal=TextChangeProposal("clock.html", before, "after\n"),
    )

    assert target.read_text(encoding="utf-8") == "after\n"
    assert rollback_text_change(root=tmp_path, applied=applied) == before
    assert target.read_text(encoding="utf-8") == "before\n"
    assert list(tmp_path.glob(".*.p64tmp")) == []


def test_changeset_rejects_an_internal_file_symlink(tmp_path: Path) -> None:
    target = tmp_path / "real.txt"
    target.write_text("before\n", encoding="utf-8")
    link = tmp_path / "linked.txt"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("file symlinks are unavailable on this host")

    with pytest.raises(ValueError, match="practice_changeset_link_rejected"):
        apply_text_change(
            root=tmp_path,
            proposal=TextChangeProposal(
                "linked.txt",
                hashlib.sha256(target.read_bytes()).hexdigest(),
                "after\n",
            ),
        )
    assert target.read_text(encoding="utf-8") == "before\n"


@pytest.mark.parametrize("path", ["../escape.txt", ".git/config", ".env", "a\\b.txt"])
def test_changeset_rejects_unsafe_paths(tmp_path: Path, path: str) -> None:
    (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")
    with pytest.raises(ValueError, match="practice_changeset_path_invalid"):
        apply_text_change(
            root=tmp_path,
            proposal=TextChangeProposal(path, "0" * 64, "unsafe"),
        )


def test_changeset_rejects_before_drift(tmp_path: Path) -> None:
    (tmp_path / "safe.txt").write_text("safe", encoding="utf-8")
    with pytest.raises(ValueError, match="practice_changeset_before_drift"):
        apply_text_change(
            root=tmp_path,
            proposal=TextChangeProposal("safe.txt", "0" * 64, "unsafe"),
        )


def test_production_personal_practice_posture_is_narrow() -> None:
    posture = personal_practice_posture(
        {
            "ENV": "production",
            "P6_4_PERSONAL_PRACTICE_ENABLED": "true",
            "AGENT_RUNTIME_ENABLED": "true",
            "AGENT_PLANNER_ENABLED": "false",
            "MULTI_AGENT_ENABLED": "false",
            "MCP_RUNTIME_ENABLED": "false",
            "PERSONAL_RUNTIME_PROFILE": "personal_single_owner",
        },
        participant_count=6,
    )

    assert posture.activation_allowed is True
    assert posture.enterprise_multi_agent_disabled is True


def test_enterprise_multi_agent_gate_vetoes_personal_practice() -> None:
    posture = personal_practice_posture(
        {
            "ENV": "production",
            "P6_4_PERSONAL_PRACTICE_ENABLED": "true",
            "AGENT_RUNTIME_ENABLED": "true",
            "AGENT_PLANNER_ENABLED": "false",
            "MULTI_AGENT_ENABLED": "true",
            "MCP_RUNTIME_ENABLED": "false",
            "PERSONAL_RUNTIME_PROFILE": "personal_single_owner",
        },
        participant_count=3,
    )

    assert posture.activation_allowed is False
    assert "personal practice requires enterprise Multi-Agent disabled" in posture.blockers
