"""Focused P5.4A tests for the single read-only logical capability."""

from __future__ import annotations

from dataclasses import replace

import pytest

from omnibase.agent_executor.contracts import (
    ExecutorInvocationContext,
    KnowledgeSearchHit,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)
from omnibase.agent_executor.service import (
    TypedExecutorPolicyDenied,
    TypedExecutorUnavailable,
    TypedSingleAgentExecutor,
    UnavailableTypedSingleAgentExecutor,
    build_engineering_typed_executor,
)
from omnibase.production.phase5_planner_contract import (
    AgentVersionSnapshot,
    FrozenTaskSnapshot,
    NodeKind,
    PlannerCeilings,
    PlannerPolicy,
    PlanNodeBudget,
    PlanNodeProposal,
    PlanOutputContract,
    PlanProposal,
    PlanProposalValidator,
    PlanRetryPolicy,
    PlanValidationReport,
    ToolVersionSnapshot,
    ValidatedPlan,
    WorkspaceScopeSnapshot,
)

TENANT = "00000000-0000-0000-0000-00000000000a"
WORKSPACE = "66666666-6666-6666-6666-666666666666"
ACTOR = "00000000-0000-0000-0000-0000000000aa"
TASK = "00000000-0000-0000-0000-0000000000e1"
RUN = "00000000-0000-0000-0000-0000000000f1"
DEFINITION = "00000000-0000-0000-0000-000000000001"
VERSION = "11111111-1111-1111-1111-111111111111"
NODE = "bb000000-bb00-bb00-bb00-bb0000000001"
RESOURCE = "77777777-7777-7777-7777-777777777777"

VERSION_DIGEST = "ae" * 32
REQUEST_HASH = "cc" * 32
GOAL_DIGEST = "aa" * 32
POLICY_DIGEST = "bb" * 32
SCOPE_DIGEST = "f1" * 32
RESOURCE_SCOPE_DIGEST = "c1" * 32
BUDGET_POLICY_DIGEST = "c2" * 32
INSTRUCTIONS_DIGEST = "dd" * 32
OUTPUT_DIGEST = "ee" * 32
TOOL_DIGEST = "f2" * 32
PROPOSAL_ID = "aa000000-aa00-aa00-aa00-aa0000000001"

CEILINGS = {
    "input_tokens": 10_000_000,
    "output_tokens": 5_000_000,
    "reasoning_tokens": 5_000_000,
    "total_tokens": 20_000_000,
    "cost_micros": 10_000_000,
    "model_calls": 5_000,
    "tool_calls": 2_000,
    "wall_clock_ms": 3_600_000,
    "artifact_bytes": 1_073_741_824,
    "sandbox_jobs": 500,
    "max_attempts": 32,
    "max_parallel_steps": 8,
}
NODE_BUDGET = {
    "input_tokens": 100_000,
    "output_tokens": 50_000,
    "reasoning_tokens": 50_000,
    "total_tokens": 200_000,
    "cost_micros": 10_000,
    "model_calls": 1,
    "tool_calls": 1,
    "wall_clock_ms": 10_000,
    "artifact_bytes": 1_048_576,
    "sandbox_jobs": 1,
    "max_attempts": 1,
    "max_parallel_steps": 1,
}
TASK_BUDGET = {
    "input_tokens": 1_000_000,
    "output_tokens": 500_000,
    "reasoning_tokens": 500_000,
    "total_tokens": 2_000_000,
    "cost_micros": 100_000,
    "model_calls": 10,
    "tool_calls": 5,
    "wall_clock_ms": 60_000,
    "artifact_bytes": 1_048_576,
    "sandbox_jobs": 32,
    "max_attempts": 32,
    "max_parallel_steps": 32,
}


def _node(*, tools: tuple[str, ...] = ("knowledge_search",), risk: str = "low") -> PlanNodeProposal:
    node = PlanNodeProposal(
        node_id=NODE,
        node_kind="knowledge_read",
        agent_definition_id=DEFINITION,
        agent_version_id=VERSION,
        agent_version_digest=VERSION_DIGEST,
        depends_on=(),
        input_bindings=(),
        output_contract=PlanOutputContract(
            output_schema={"type": "object", "properties": {"summary": {"type": "string"}}},
            output_digest=OUTPUT_DIGEST,
        ),
        allowed_tool_ids=tools,
        resource_scopes=("workspace-docs",),
        risk_level=risk,
        budget=PlanNodeBudget.from_mapping(NODE_BUDGET, ceilings=CEILINGS),
        timeout_ms=10_000,
        retry_policy=PlanRetryPolicy(policy="no_retry", max_retries=0, backoff_base_ms=0),
        approval_requirement=None,
        effect_class="read_only",
        execution_requirement=None,
        node_digest="00" * 32,
    )
    return replace(node, node_digest=node.compute_node_digest())


def _plan(
    *,
    tools: tuple[str, ...] = ("knowledge_search",),
    risk: str = "low",
    force_report_valid: bool = False,
) -> ValidatedPlan:
    node = _node(tools=tools, risk=risk)
    proposal = PlanProposal(
        schema_version=1,
        proposal_id=PROPOSAL_ID,
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        workspace_generation=1,
        task_id=TASK,
        task_generation=1,
        actor_user_id=ACTOR,
        root_agent_definition_id=DEFINITION,
        root_agent_version_id=VERSION,
        root_agent_version_digest=VERSION_DIGEST,
        request_hash=REQUEST_HASH,
        goal_digest=GOAL_DIGEST,
        planner_policy_digest=POLICY_DIGEST,
        resource_scope_digest=RESOURCE_SCOPE_DIGEST,
        budget_policy_digest=BUDGET_POLICY_DIGEST,
        deadline="2026-08-12T00:00:00Z",
        proposal_version=1,
        created_at="2026-08-05T00:00:00Z",
        nodes=(node,),
        plan_budget=TASK_BUDGET,
        plan_risk_summary={"low": 1, "medium": 0, "high": 0, "critical": 0},
        proposal_digest="00" * 32,
        parent_proposal_id=None,
        parent_proposal_version=None,
    )
    proposal = replace(proposal, proposal_digest=proposal.compute_proposal_digest())
    validator = PlanProposalValidator(
        agent_versions=(
            AgentVersionSnapshot(
                agent_definition_id=DEFINITION,
                agent_version_id=VERSION,
                agent_version_digest=VERSION_DIGEST,
                tenant_id=TENANT,
                version_state="sealed",
                risk_level="low",
                allowed_tool_ids=("knowledge_search",),
                resource_scopes=("workspace-docs",),
                instructions_digest=INSTRUCTIONS_DIGEST,
            ),
        ),
        tool_versions=(
            ToolVersionSnapshot(
                tool_id="knowledge_search",
                tool_version="1.0.0",
                tool_digest=TOOL_DIGEST,
                effect_class="read_only",
                input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
            ),
        ),
        workspace_scope=WorkspaceScopeSnapshot(
            workspace_id=WORKSPACE,
            workspace_generation=1,
            tenant_id=TENANT,
            resource_scopes=("workspace-docs",),
            tool_binding_ids=("knowledge_search",),
            scope_digest=SCOPE_DIGEST,
        ),
        frozen_task=FrozenTaskSnapshot(
            task_id=TASK,
            task_generation=1,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            workspace_generation=1,
            actor_user_id=ACTOR,
            agent_definition_id=DEFINITION,
            agent_version_id=VERSION,
            agent_version_digest=VERSION_DIGEST,
            resource_scope_digest=RESOURCE_SCOPE_DIGEST,
            budget_policy_digest=BUDGET_POLICY_DIGEST,
            deadline="2026-08-12T00:00:00Z",
            task_budget=TASK_BUDGET,
        ),
        planner_policy=PlannerPolicy(
            schema_version=1,
            policy_digest=POLICY_DIGEST,
            allowed_node_kinds=tuple(item.value for item in NodeKind),
            allowed_tool_ids=("knowledge_search",),
            max_replan=2,
            approval_policy={
                "low": "optional",
                "medium": "optional",
                "high": "required",
                "critical": "required",
            },
            ceilings=PlannerCeilings(
                values={
                    "max_nodes": 32,
                    "max_depth": 8,
                    "max_fan_out": 8,
                    "max_concurrency": 4,
                    "max_replan": 2,
                    "max_attempts_per_node": 2,
                }
            ),
        ),
        budget_ceilings=CEILINGS,
    )
    report = validator.validate(proposal)
    if force_report_valid:
        report = PlanValidationReport(
            valid=True,
            proposal_digest=proposal.proposal_digest,
            topological_order=(NODE,),
            findings=(),
        )
    else:
        assert report.valid, report.to_dict()
    return ValidatedPlan(
        proposal=proposal, validation_report=report, validated_at="2026-08-05T00:01:00Z"
    )


def _context(plan: ValidatedPlan) -> ExecutorInvocationContext:
    p = plan.proposal
    return ExecutorInvocationContext(
        tenant_id=p.tenant_id,
        workspace_id=p.workspace_id,
        workspace_generation=p.workspace_generation,
        actor_user_id=p.actor_user_id,
        task_id=p.task_id,
        task_generation=p.task_generation,
        run_id=RUN,
        run_fencing_token=1,
        agent_version_id=p.root_agent_version_id,
        agent_version_digest=p.root_agent_version_digest,
        proposal_digest=p.proposal_digest,
        node_id=NODE,
    )


class _Port:
    def __init__(self) -> None:
        self.calls: list[tuple[ExecutorInvocationContext, KnowledgeSearchRequest]] = []

    def search(
        self, *, context: ExecutorInvocationContext, request: KnowledgeSearchRequest
    ) -> KnowledgeSearchResult:
        self.calls.append((context, request))
        return KnowledgeSearchResult(
            resource_id=request.resource_id,
            results=(
                KnowledgeSearchHit(
                    citation_id="88888888-8888-8888-8888-888888888888",
                    document_id="99999999-9999-9999-9999-999999999999",
                    score=0.9,
                    snippet="bounded result",
                    page_number=1,
                ),
            ),
            bytes_out=128,
            truncated=False,
        )


def test_single_tool_executor_calls_only_injected_port() -> None:
    plan = _plan()
    port = _Port()
    result = TypedSingleAgentExecutor(knowledge_search=port).execute(
        context=_context(plan),
        plan=plan,
        request=KnowledgeSearchRequest(resource_id=RESOURCE, query="hello"),
    )
    assert len(port.calls) == 1
    assert result.receipt.capability == "workspace.knowledge.search"
    assert result.receipt.effect_class == "read_only"
    assert result.output.results[0].snippet == "bounded result"


def test_default_engineering_seam_is_unavailable() -> None:
    executor = build_engineering_typed_executor(
        enabled=True,
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
    )
    assert isinstance(executor, UnavailableTypedSingleAgentExecutor)
    with pytest.raises(TypedExecutorUnavailable, match="typed_executor_unavailable"):
        executor.execute()


@pytest.mark.parametrize(
    ("tools", "risk", "message"),
    [
        ((), "low", "tool_authority_expansion"),
        (("knowledge_search", "shell"), "low", "tool_authority_expansion"),
        (("workspace.knowledge.search",), "low", "tool_authority_expansion"),
        (("knowledge_search",), "high", "tool_risk_not_low"),
    ],
)
def test_executor_rejects_authority_expansion(
    tools: tuple[str, ...], risk: str, message: str
) -> None:
    plan = _plan(tools=tools, risk=risk, force_report_valid=True)
    with pytest.raises(TypedExecutorPolicyDenied, match=message):
        TypedSingleAgentExecutor(knowledge_search=_Port()).execute(
            context=_context(plan),
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=RESOURCE, query="hello"),
        )


def test_executor_rejects_plan_digest_drift() -> None:
    plan = _plan()
    drifted = replace(plan.proposal, request_hash="ab" * 32)
    drifted_plan = replace(plan, proposal=drifted)
    with pytest.raises(TypedExecutorPolicyDenied, match="plan_digest_drift"):
        TypedSingleAgentExecutor(knowledge_search=_Port()).execute(
            context=_context(plan),
            plan=drifted_plan,
            request=KnowledgeSearchRequest(RESOURCE, "hello"),
        )


def test_executor_rejects_context_tenant_mismatch() -> None:
    plan = _plan()
    context = replace(_context(plan), tenant_id="00000000-0000-0000-0000-00000000000b")
    with pytest.raises(TypedExecutorPolicyDenied, match="context_tenant_mismatch"):
        TypedSingleAgentExecutor(knowledge_search=_Port()).execute(
            context=context, plan=plan, request=KnowledgeSearchRequest(RESOURCE, "hello")
        )


@pytest.mark.parametrize(
    "search_request",
    [
        KnowledgeSearchRequest(RESOURCE, "x", top_k=20, timeout_ms=5000, max_bytes=262_144),
        KnowledgeSearchRequest(RESOURCE, "x", top_k=1, timeout_ms=1, max_bytes=1),
    ],
)
def test_request_bounds_are_accepted(search_request: KnowledgeSearchRequest) -> None:
    assert len(search_request.request_digest) == 64
    assert search_request.to_dict()["resource_id"] == RESOURCE


def test_result_contract_does_not_expose_physical_locator() -> None:
    result = _Port().search(
        context=_context(_plan()), request=KnowledgeSearchRequest(RESOURCE, "hello")
    )
    text = str(result.to_dict())
    assert "schema" not in text.lower()
    assert "table" not in text.lower()
    assert "postgres" not in text.lower()
