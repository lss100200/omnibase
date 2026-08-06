"""Focused P5.4B engineering composition tests."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from omnibase.agent_executor.contracts import KnowledgeSearchRequest
from omnibase.agent_executor.engineering import (
    EngineeringCompositionUnavailable,
    EngineeringSingleAgentExecutor,
    UnavailableEngineeringSingleAgentExecutor,
    build_engineering_single_agent_executor,
)
from omnibase.agent_executor.gateway_adapter import RuntimeAuthorityValidator
from omnibase.capability_gateway.contracts import (
    RagSearchResponse,
    SearchHitRead,
    TrustedWorkloadContext,
    WorkloadCredential,
)
from tests.test_p5_4a_typed_executor import RESOURCE, _context, _plan


class _CredentialSeam:
    def __init__(self, credential: WorkloadCredential) -> None:
        self.credential = credential
        self.calls = 0

    def issue(self, *, context):
        del context
        self.calls += 1
        return self.credential


class _Authority(RuntimeAuthorityValidator):
    def __init__(self) -> None:
        self.calls = 0

    def validate(self, *, context, credential) -> None:
        del context, credential
        self.calls += 1


def _credential() -> WorkloadCredential:
    return WorkloadCredential(
        authorization="Capability server-owned",
        identity="runtime-workload",
        trusted_context=TrustedWorkloadContext(
            opaque_identity="opaque",
            tenant_id=_context(_plan()).tenant_id,
            workspace_id=_context(_plan()).workspace_id,
            runtime_instance_id="runtime-1",
            certificate_thumbprint="thumb",
        ),
    )


def test_composition_is_unavailable_by_default() -> None:
    executor = build_engineering_single_agent_executor(
        enabled=False,
        feature_gates={
            "AGENT_RUNTIME_ENABLED": False,
            "AGENT_PLANNER_ENABLED": False,
            "MULTI_AGENT_ENABLED": False,
        },
    )
    assert isinstance(executor, UnavailableEngineeringSingleAgentExecutor)
    with pytest.raises(
        EngineeringCompositionUnavailable, match="engineering_composition_unavailable"
    ):
        executor.execute()


def test_composition_uses_server_credential_and_one_gateway_search() -> None:
    plan = _plan()
    context = _context(plan)
    credential_seam = _CredentialSeam(_credential())
    authority = _Authority()
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
    session = Mock()
    executor = build_engineering_single_agent_executor(
        enabled=True,
        feature_gates={
            "AGENT_RUNTIME_ENABLED": False,
            "AGENT_PLANNER_ENABLED": False,
            "MULTI_AGENT_ENABLED": False,
        },
        gateway=gateway,
        session_factory=lambda: session,
        workload_credential_seam=credential_seam,
        authority_validator=authority,
    )
    assert isinstance(executor, EngineeringSingleAgentExecutor)
    result = executor.execute(
        context=context,
        plan=plan,
        request=KnowledgeSearchRequest(resource_id=RESOURCE, query="hello"),
    )
    assert len(result.output.results) == 1
    assert credential_seam.calls == 1
    assert authority.calls == 1
    gateway.rag_search.assert_called_once()
    session.close.assert_called_once()


def test_composition_rejects_migration_and_gate_drift_without_dependencies() -> None:
    for migration_head, gates in [
        (
            "0011",
            {
                "AGENT_RUNTIME_ENABLED": False,
                "AGENT_PLANNER_ENABLED": False,
                "MULTI_AGENT_ENABLED": False,
            },
        ),
        (
            "0012",
            {
                "AGENT_RUNTIME_ENABLED": True,
                "AGENT_PLANNER_ENABLED": False,
                "MULTI_AGENT_ENABLED": False,
            },
        ),
    ]:
        executor = build_engineering_single_agent_executor(
            enabled=True,
            migration_head=migration_head,
            feature_gates=gates,
        )
        assert isinstance(executor, UnavailableEngineeringSingleAgentExecutor)
