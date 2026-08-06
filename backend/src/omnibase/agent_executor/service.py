"""Engineering-only typed Executor for one read-only Workspace capability."""

from __future__ import annotations

from collections.abc import Mapping

from omnibase.agent_executor.contracts import (
    KNOWLEDGE_SEARCH_CAPABILITY,
    KNOWLEDGE_SEARCH_TOOL_ID,
    ExecutorContractError,
    ExecutorInvocationContext,
    ExecutorNodeResult,
    ExecutorToolReceipt,
    KnowledgeSearchPort,
    KnowledgeSearchRequest,
)
from omnibase.production.phase5_planner_contract import PlanNodeProposal, ValidatedPlan


class TypedExecutorError(RuntimeError):
    """The validated plan or execution boundary cannot be used."""


class TypedExecutorUnavailable(TypedExecutorError):
    """The engineering dependency was not explicitly installed."""


class TypedExecutorPolicyDenied(TypedExecutorError):
    """The plan attempted to expand the single-tool authority."""


class UnavailableTypedSingleAgentExecutor:
    """Production-safe default; no tool or model is reachable."""

    def execute(self, **_: object) -> ExecutorNodeResult:
        raise TypedExecutorUnavailable("typed_executor_unavailable")


class TypedSingleAgentExecutor:
    """Execute exactly one validated ``knowledge_search`` node.

    This class has no default adapter and is never mounted by the Browser app.
    Callers must inject a capability-gateway-backed ``KnowledgeSearchPort`` in
    an explicitly enabled engineering composition.  The plan is rechecked at
    the execution boundary because Planner validation is not authorization.
    """

    def __init__(self, *, knowledge_search: KnowledgeSearchPort) -> None:
        self._knowledge_search = knowledge_search

    def execute(
        self,
        *,
        context: ExecutorInvocationContext,
        plan: ValidatedPlan,
        request: KnowledgeSearchRequest,
    ) -> ExecutorNodeResult:
        node = self._validate_plan_and_context(context=context, plan=plan, request=request)
        try:
            result = self._knowledge_search.search(context=context, request=request)
        except ExecutorContractError:
            raise
        except Exception as exc:
            raise TypedExecutorError("knowledge_search_failed") from exc

        receipt = ExecutorToolReceipt(
            tool_id=KNOWLEDGE_SEARCH_TOOL_ID,
            capability=KNOWLEDGE_SEARCH_CAPABILITY,
            request_digest=request.request_digest,
            result_digest=result.result_digest,
            effect_class="read_only",
            status="succeeded",
        )
        return ExecutorNodeResult(
            proposal_digest=plan.proposal.proposal_digest,
            node_id=node.node_id,
            output=result,
            receipt=receipt,
        )

    def _validate_plan_and_context(
        self,
        *,
        context: ExecutorInvocationContext,
        plan: ValidatedPlan,
        request: KnowledgeSearchRequest,
    ) -> PlanNodeProposal:
        proposal = plan.proposal
        report = plan.validation_report
        if not report.valid:
            raise TypedExecutorPolicyDenied("plan_not_validated")
        if report.proposal_digest != proposal.proposal_digest:
            raise TypedExecutorPolicyDenied("plan_validation_digest_missing")
        if proposal.compute_proposal_digest() != proposal.proposal_digest:
            raise TypedExecutorPolicyDenied("plan_digest_drift")
        self._validate_context(context=context, plan=plan)

        nodes = {node.node_id: node for node in proposal.nodes}
        node = nodes.get(context.node_id)
        if node is None:
            raise TypedExecutorPolicyDenied("executor_node_not_found")
        if len(proposal.nodes) != 1:
            raise TypedExecutorPolicyDenied("single_agent_executor_requires_one_node")
        self._validate_node(node=node, request=request)
        return node

    @staticmethod
    def _validate_context(*, context: ExecutorInvocationContext, plan: ValidatedPlan) -> None:
        proposal = plan.proposal
        if context.proposal_digest != proposal.proposal_digest:
            raise TypedExecutorPolicyDenied("context_plan_digest_mismatch")
        if context.tenant_id != proposal.tenant_id:
            raise TypedExecutorPolicyDenied("context_tenant_mismatch")
        if context.workspace_id != proposal.workspace_id:
            raise TypedExecutorPolicyDenied("context_workspace_mismatch")
        if context.workspace_generation != proposal.workspace_generation:
            raise TypedExecutorPolicyDenied("context_workspace_generation_mismatch")
        if context.actor_user_id != proposal.actor_user_id:
            raise TypedExecutorPolicyDenied("context_actor_mismatch")
        if context.task_id != proposal.task_id:
            raise TypedExecutorPolicyDenied("context_task_mismatch")
        if context.task_generation != proposal.task_generation:
            raise TypedExecutorPolicyDenied("context_task_generation_mismatch")
        if context.agent_version_id != proposal.root_agent_version_id:
            raise TypedExecutorPolicyDenied("context_agent_version_mismatch")
        if context.agent_version_digest != proposal.root_agent_version_digest:
            raise TypedExecutorPolicyDenied("context_agent_version_digest_mismatch")

    @staticmethod
    def _validate_node(*, node: PlanNodeProposal, request: KnowledgeSearchRequest) -> None:
        if node.allowed_tool_ids != (KNOWLEDGE_SEARCH_TOOL_ID,):
            raise TypedExecutorPolicyDenied("tool_authority_expansion")
        if node.effect_class != "read_only":
            raise TypedExecutorPolicyDenied("tool_effect_not_read_only")
        if node.risk_level != "low":
            raise TypedExecutorPolicyDenied("tool_risk_not_low")
        if node.node_kind not in {"model_reasoning", "knowledge_read"}:
            raise TypedExecutorPolicyDenied("node_kind_not_supported")
        node_budget = node.budget.as_mapping()
        if node_budget["tool_calls"] < 1:
            raise TypedExecutorPolicyDenied("tool_budget_exhausted")
        if request.max_bytes > node_budget["artifact_bytes"]:
            raise TypedExecutorPolicyDenied("request_bytes_exceed_node_budget")


def build_engineering_typed_executor(
    *,
    enabled: bool,
    feature_gates: Mapping[str, bool],
    knowledge_search: KnowledgeSearchPort | None = None,
) -> TypedSingleAgentExecutor | UnavailableTypedSingleAgentExecutor:
    """Build only the explicit engineering seam; defaults remain rejecting."""

    required_gates = (
        "agent_runtime_enabled",
        "agent_planner_enabled",
        "multi_agent_enabled",
    )
    if not enabled or any(feature_gates.get(name) is not False for name in required_gates):
        return UnavailableTypedSingleAgentExecutor()
    if knowledge_search is None:
        return UnavailableTypedSingleAgentExecutor()
    return TypedSingleAgentExecutor(knowledge_search=knowledge_search)


__all__ = [
    "TypedExecutorError",
    "TypedExecutorPolicyDenied",
    "TypedExecutorUnavailable",
    "TypedSingleAgentExecutor",
    "UnavailableTypedSingleAgentExecutor",
    "build_engineering_typed_executor",
]
