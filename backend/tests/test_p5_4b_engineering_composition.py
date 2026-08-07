"""Focused P5.4B engineering composition tests."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omnibase.agent_executor.contracts import KnowledgeSearchRequest
from omnibase.agent_executor.engineering import (
    EngineeringCompositionError,
    EngineeringCompositionUnavailable,
    EngineeringSingleAgentExecutor,
    LiveRuntimeAuthorityValidator,
    UnavailableEngineeringSingleAgentExecutor,
    build_engineering_single_agent_executor,
)
from omnibase.agent_executor.service import TypedExecutorPolicyDenied
from omnibase.capability_gateway.contracts import (
    RagSearchResponse,
    SearchHitRead,
    TrustedWorkloadContext,
    WorkloadCredential,
)
from omnibase.production.phase5_planner_contract import PlanValidationReport, ValidatedPlan
from tests.test_p5_4a_typed_executor import DEFINITION, RESOURCE, _context, _plan

DIGEST = "ab" * 32
WORKSPACE_RUN = "00000000-0000-0000-0000-0000000000f2"
RUNTIME = "00000000-0000-0000-0000-0000000000f3"
LEASE = "00000000-0000-0000-0000-0000000000f4"
RUNTIME_NODE = "00000000-0000-0000-0000-0000000000f6"


class _CredentialSeam:
    def __init__(self, credential: WorkloadCredential) -> None:
        self.credential = credential
        self.calls = 0

    def issue(self, *, context):
        del context
        self.calls += 1
        return self.credential


def _credential() -> WorkloadCredential:
    context = _context(_plan())
    return WorkloadCredential(
        authorization="Capability server-owned",
        identity="runtime-workload",
        trusted_context=TrustedWorkloadContext(
            opaque_identity="runtime",
            tenant_id=context.tenant_id,
            workspace_id=context.workspace_id,
            runtime_instance_id=RUNTIME,
            certificate_thumbprint=DIGEST,
            workload_identity_digest=DIGEST,
        ),
    )


def _result(value, *, one_or_none=None, one=None):
    result = Mock()
    result.scalar_one_or_none.return_value = value if one_or_none is None else one_or_none
    result.scalar_one.return_value = value if one is None else one
    return result


def _authority_session(context, *, task_state: str = "scheduled"):
    now = datetime.now(UTC)
    workspace = SimpleNamespace(
        id=context.workspace_id,
        tenant_id=context.tenant_id,
        generation=context.workspace_generation,
        desired_state="running",
        observed_state="running",
    )
    task = SimpleNamespace(
        id=context.task_id,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        workspace_generation=context.workspace_generation,
        actor_user_id=context.actor_user_id,
        agent_definition_id=DEFINITION,
        task_generation=context.task_generation,
        agent_version_id=context.agent_version_id,
        agent_version_digest=context.agent_version_digest,
        request_hash=context.proposal_digest,
        plan_digest=context.proposal_digest,
        state=task_state,
        workspace_agent_binding_id="00000000-0000-0000-0000-0000000000f5",
        deadline=now + timedelta(minutes=5),
        resource_scope_digest="c1" * 32,
        budget_policy_digest="c2" * 32,
        plan_version=1,
    )
    run = SimpleNamespace(
        id=context.run_id,
        task_id=context.task_id,
        workspace_id=context.workspace_id,
        workspace_generation=context.workspace_generation,
        runtime_instance_id=RUNTIME,
        workload_identity_digest=DIGEST,
        run_fencing_token=context.run_fencing_token,
        node_id=RUNTIME_NODE,
        node_fencing_token=7,
        run_lease_id=LEASE,
        workspace_run_id=WORKSPACE_RUN,
        state="leased",
    )
    version = SimpleNamespace(
        id=context.agent_version_id,
        tenant_id=context.tenant_id,
        version_state="sealed",
        manifest_digest=context.agent_version_digest,
        definition_id=DEFINITION,
    )
    binding = SimpleNamespace(
        id="00000000-0000-0000-0000-0000000000f5",
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        workspace_generation=context.workspace_generation,
        agent_definition_id=DEFINITION,
        agent_version_id=context.agent_version_id,
        agent_version_digest=context.agent_version_digest,
        binding_state="installed",
    )
    workspace_run = SimpleNamespace(
        id=WORKSPACE_RUN,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        generation=context.workspace_generation,
        desired_state="running",
        observed_state="running",
        next_fencing_token=context.run_fencing_token + 1,
        runtime_instance_id=RUNTIME,
        workload_identity_digest=DIGEST,
    )
    lease = SimpleNamespace(
        id=LEASE,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        run_id=WORKSPACE_RUN,
        state="active",
        generation=context.workspace_generation,
        fencing_token=context.run_fencing_token,
        node_id=RUNTIME_NODE,
        node_fencing_token=7,
        expires_at=now + timedelta(minutes=5),
    )
    node = SimpleNamespace(
        state="active",
        attestation_state="verified",
        revoked_at=None,
        fencing_token=7,
    )
    attestation = SimpleNamespace(state="verified")
    session = Mock()
    session.execute.side_effect = [
        _result(workspace),
        _result(task),
        _result(run),
        _result(version),
        _result(binding),
        _result(datetime.now(UTC), one=datetime.now(UTC)),
        _result(workspace_run),
        _result(lease),
        _result(node),
        _result(now, one=now),
        _result(attestation),
    ]
    session.facts = SimpleNamespace(
        workspace=workspace,
        task=task,
        run=run,
        version=version,
        binding=binding,
        workspace_run=workspace_run,
        lease=lease,
        node=node,
        attestation=attestation,
        now=now,
    )
    return session


class _SessionFactory:
    def __init__(self, *sessions) -> None:
        self._sessions = list(sessions)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        if not self._sessions:
            raise AssertionError("unexpected session request")
        return self._sessions.pop(0)


def test_composition_is_unavailable_by_default() -> None:
    executor = build_engineering_single_agent_executor(
        enabled=False,
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
    )
    assert isinstance(executor, UnavailableEngineeringSingleAgentExecutor)
    with pytest.raises(
        EngineeringCompositionUnavailable, match="engineering_composition_unavailable"
    ):
        executor.execute()


@pytest.mark.parametrize("task_state", ["scheduled", "running"])
def test_composition_uses_live_authority_server_credential_and_one_gateway_search(
    task_state: str,
) -> None:
    plan = _plan()
    context = _context(plan)
    credential_seam = _CredentialSeam(_credential())
    gateway = Mock()
    gateway.rag_search.return_value = RagSearchResponse(
        resource_id=RESOURCE,
        results=[
            SearchHitRead(
                citation_id="88888888-8888-8888-8888-888888888888",
                document_id="99999999-9999-9999-9999-999999999999",
                score=0.9,
                snippet="bounded",
                page_number=1,
            )
        ],
        total_found=1,
        bytes_out=64,
        truncated=False,
    )
    authority_session = _authority_session(context, task_state=task_state)
    gateway_session = Mock()
    session_factory = _SessionFactory(authority_session, gateway_session)
    executor = build_engineering_single_agent_executor(
        enabled=True,
        migration_head="0012",
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        gateway=gateway,
        session_factory=session_factory,
        workload_credential_seam=credential_seam,
    )
    assert isinstance(executor, EngineeringSingleAgentExecutor)
    result = executor.execute(
        context=context,
        plan=plan,
        request=KnowledgeSearchRequest(resource_id=RESOURCE, query="hello"),
    )
    assert result.receipt.status == "succeeded"
    assert len(result.output.results) == 1
    assert credential_seam.calls == 1
    assert authority_session.execute.call_count == 11
    assert authority_session.commit.call_count == 1
    assert authority_session.close.call_count == 1
    assert gateway_session.close.call_count == 1
    assert session_factory.calls == 2
    gateway.rag_search.assert_called_once()
    assert context.run_id != WORKSPACE_RUN
    assert context.node_id != RUNTIME_NODE


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda f: setattr(f.workspace, "generation", 2), "workspace_authority_stale"),
        (lambda f: setattr(f.workspace, "desired_state", "paused"), "workspace_authority_stale"),
        (lambda f: setattr(f.task, "state", "paused"), "task_authority_stale"),
        (lambda f: setattr(f.task, "state", "created"), "task_authority_stale"),
        (lambda f: setattr(f.task, "deadline", f.now), "task_authority_stale"),
        (lambda f: setattr(f.task, "task_generation", 2), "task_authority_stale"),
        (lambda f: setattr(f.task, "actor_user_id", LEASE), "task_authority_stale"),
        (
            lambda f: setattr(f.task, "agent_version_digest", "ff" * 32),
            "agent_version_authority_stale",
        ),
        (lambda f: setattr(f.task, "plan_digest", "ff" * 32), "task_authority_stale"),
        (lambda f: setattr(f.task, "plan_version", 2), "task_authority_stale"),
        (lambda f: setattr(f.task, "resource_scope_digest", "ff" * 32), "task_authority_stale"),
        (lambda f: setattr(f.task, "budget_policy_digest", "ff" * 32), "task_authority_stale"),
        (lambda f: setattr(f.run, "state", "paused"), "runtime_fencing_stale"),
        (lambda f: setattr(f.run, "run_fencing_token", 2), "runtime_fencing_stale"),
        (lambda f: setattr(f.run, "workload_identity_digest", "ff" * 32), "runtime_fencing_stale"),
        (lambda f: setattr(f.run, "node_fencing_token", 8), "run_lease_stale"),
        (
            lambda f: setattr(f.workspace_run, "observed_state", "paused"),
            "workspace_run_authority_stale",
        ),
        (
            lambda f: setattr(f.workspace_run, "runtime_instance_id", LEASE),
            "workspace_run_authority_stale",
        ),
        (
            lambda f: setattr(f.workspace_run, "workload_identity_digest", "ff" * 32),
            "workspace_run_authority_stale",
        ),
        (
            lambda f: setattr(f.workspace_run, "next_fencing_token", 3),
            "workspace_run_authority_stale",
        ),
        (lambda f: setattr(f.version, "definition_id", LEASE), "agent_version_authority_stale"),
        (
            lambda f: setattr(f.binding, "agent_definition_id", LEASE),
            "agent_version_authority_stale",
        ),
        (
            lambda f: setattr(f.binding, "binding_state", "disabled"),
            "agent_version_authority_stale",
        ),
        (lambda f: setattr(f.lease, "state", "revoked"), "run_lease_stale"),
        (lambda f: setattr(f.lease, "expires_at", f.now), "run_lease_stale"),
        (lambda f: setattr(f.lease, "expires_at", f.now - timedelta(seconds=1)), "run_lease_stale"),
        (lambda f: setattr(f.lease, "node_id", LEASE), "run_lease_stale"),
        (lambda f: setattr(f.node, "state", "revoked"), "node_attestation_stale"),
        (lambda f: setattr(f.node, "fencing_token", 8), "node_fencing_stale"),
    ],
)
def test_live_authority_rejects_stale_facts(mutate, code: str) -> None:
    context = _context(_plan())
    session = _authority_session(context)
    mutate(session.facts)
    validator = LiveRuntimeAuthorityValidator(session_factory=lambda: session)
    with pytest.raises(EngineeringCompositionError, match=code):
        validator.validate(context=context, credential=_credential())
    session.rollback.assert_called_once()
    session.close.assert_called_once()


def test_workload_identity_digest_is_required_and_strict() -> None:
    context = _context(_plan())
    kwargs = {
        "opaque_identity": "runtime",
        "tenant_id": context.tenant_id,
        "workspace_id": context.workspace_id,
        "runtime_instance_id": RUNTIME,
        "certificate_thumbprint": DIGEST,
    }
    with pytest.raises(TypeError):
        TrustedWorkloadContext(**kwargs)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        TrustedWorkloadContext(**kwargs, workload_identity_digest="A" * 64)


def _force_plan(*, multi_node: bool = False, effect_class: str | None = None) -> ValidatedPlan:
    plan = _plan()
    nodes = list(plan.proposal.nodes)
    if effect_class is not None:
        changed = replace(nodes[0], effect_class=effect_class, node_digest="00" * 32)
        nodes[0] = replace(changed, node_digest=changed.compute_node_digest())
    if multi_node:
        second = replace(
            nodes[0],
            node_id="bb000000-bb00-bb00-bb00-bb0000000002",
            node_digest="00" * 32,
        )
        nodes.append(replace(second, node_digest=second.compute_node_digest()))
    proposal = replace(plan.proposal, nodes=tuple(nodes), proposal_digest="00" * 32)
    proposal = replace(proposal, proposal_digest=proposal.compute_proposal_digest())
    report = PlanValidationReport(
        valid=True,
        proposal_digest=proposal.proposal_digest,
        topological_order=tuple(node.node_id for node in nodes),
        findings=(),
    )
    return ValidatedPlan(
        proposal=proposal,
        validation_report=report,
        validated_at=plan.validated_at,
    )


@pytest.mark.parametrize(
    ("plan", "code"),
    [
        (
            _plan(tools=("knowledge_search", "shell"), force_report_valid=True),
            "tool_authority_expansion",
        ),
        (_plan(risk="high", force_report_valid=True), "tool_risk_not_low"),
        (_force_plan(effect_class="reversible"), "tool_effect_not_read_only"),
        (_force_plan(multi_node=True), "single_agent_executor_requires_one_node"),
    ],
)
def test_formal_p5_4b_builder_rejects_executor_authority_expansion(
    plan: ValidatedPlan, code: str
) -> None:
    gateway = Mock()
    seam = Mock()
    session_factory = Mock(side_effect=AssertionError("authority session must not open"))
    executor = build_engineering_single_agent_executor(
        enabled=True,
        migration_head="0012",
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        gateway=gateway,
        session_factory=session_factory,
        workload_credential_seam=seam,
    )
    with pytest.raises(TypedExecutorPolicyDenied, match=code):
        executor.execute(
            context=_context(plan),
            plan=plan,
            request=KnowledgeSearchRequest(resource_id=RESOURCE, query="denied"),
        )
    seam.issue.assert_not_called()
    gateway.rag_search.assert_not_called()
    session_factory.assert_not_called()


@pytest.mark.parametrize(
    ("migration_head", "gates"),
    [
        (None, None),
        (
            "0011",
            {
                "agent_runtime_enabled": False,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            },
        ),
        (
            "0013",
            {
                "agent_runtime_enabled": False,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            },
        ),
        ("0012", {"agent_runtime_enabled": False, "agent_planner_enabled": False}),
        (
            "0012",
            {
                "AGENT_RUNTIME_ENABLED": False,
                "AGENT_PLANNER_ENABLED": False,
                "MULTI_AGENT_ENABLED": False,
            },
        ),
        (
            "0012",
            {
                "agent_runtime_enabled": False,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
                "extra": False,
            },
        ),
        (
            "0012",
            {
                "agent_runtime_enabled": "false",
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            },
        ),
        (
            "0012",
            {
                "agent_runtime_enabled": True,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            },
        ),
    ],
)
def test_composition_rejects_migration_and_gate_drift_without_dependencies(
    migration_head, gates
) -> None:
    executor = build_engineering_single_agent_executor(
        enabled=True,
        migration_head=migration_head,
        feature_gates=gates,
    )
    assert isinstance(executor, UnavailableEngineeringSingleAgentExecutor)


def test_composition_rejects_missing_dependencies() -> None:
    executor = build_engineering_single_agent_executor(
        enabled=True,
        migration_head="0012",
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
    )
    assert isinstance(executor, UnavailableEngineeringSingleAgentExecutor)
