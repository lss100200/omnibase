"""Focused P5.4B engineering composition tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from omnibase.agent_executor.contracts import KnowledgeSearchRequest
from omnibase.agent_executor.engineering import (
    EngineeringCompositionUnavailable,
    EngineeringSingleAgentExecutor,
    UnavailableEngineeringSingleAgentExecutor,
    build_engineering_single_agent_executor,
)
from omnibase.capability_gateway.contracts import (
    RagSearchResponse,
    SearchHitRead,
    TrustedWorkloadContext,
    WorkloadCredential,
)
from tests.test_p5_4a_typed_executor import RESOURCE, _context, _plan

DIGEST = "ab" * 32
WORKSPACE_RUN = "00000000-0000-0000-0000-0000000000f2"
RUNTIME = "00000000-0000-0000-0000-0000000000f3"
LEASE = "00000000-0000-0000-0000-0000000000f4"


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


def _authority_session(context):
    now = datetime.now(UTC)
    task = SimpleNamespace(
        id=context.task_id,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        workspace_generation=context.workspace_generation,
        actor_user_id=context.actor_user_id,
        task_generation=context.task_generation,
        agent_version_id=context.agent_version_id,
        agent_version_digest=context.agent_version_digest,
        request_hash=context.proposal_digest,
        plan_digest=context.proposal_digest,
        state="scheduled",
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
        node_id=context.node_id,
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
    )
    binding = SimpleNamespace(
        id="00000000-0000-0000-0000-0000000000f5",
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        workspace_generation=context.workspace_generation,
        agent_version_id=context.agent_version_id,
        agent_version_digest=context.agent_version_digest,
        binding_state="installed",
    )
    workspace_run = SimpleNamespace(
        id=WORKSPACE_RUN,
        tenant_id=context.tenant_id,
        workspace_id=context.workspace_id,
        generation=context.workspace_generation,
        observed_state="running",
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
        node_id=context.node_id,
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
    return session


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


def test_composition_uses_live_authority_server_credential_and_one_gateway_search() -> None:
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
    session = _authority_session(context)
    executor = build_engineering_single_agent_executor(
        enabled=True,
        migration_head="0012",
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        gateway=gateway,
        session_factory=lambda: session,
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
    assert session.execute.call_count == 10
    assert session.commit.call_count == 1
    assert session.close.call_count == 2
    gateway.rag_search.assert_called_once()
    assert context.run_id != WORKSPACE_RUN


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
