from __future__ import annotations

from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest

from omnibase.agent_executor import (
    CapabilityGatewayKnowledgeSearchPort,
    ExecutorInvocationContext,
    GatewayAdapterDenied,
    GatewayAdapterError,
    GatewayAdapterUnavailable,
    KnowledgeSearchRequest,
)
from omnibase.capability_gateway.contracts import (
    RagSearchRequest,
    RagSearchResponse,
    SearchHitRead,
    TrustedWorkloadContext,
    WorkloadCredential,
)
from omnibase.capability_gateway.service import GatewayFailure

TENANT = str(uuid4())
WORKSPACE = str(uuid4())
RESOURCE = str(uuid4())
RUN = str(uuid4())
TASK = str(uuid4())
NODE = str(uuid4())
AGENT_VERSION = str(uuid4())
DIGEST = "a" * 64


class _ClosedSession:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Authority:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls = 0
        self.error = error

    def validate(self, *, context, credential) -> None:
        del context, credential
        self.calls += 1
        if self.error is not None:
            raise self.error


def _context() -> ExecutorInvocationContext:
    return ExecutorInvocationContext(
        tenant_id=TENANT,
        workspace_id=WORKSPACE,
        workspace_generation=3,
        actor_user_id=str(uuid4()),
        task_id=TASK,
        task_generation=2,
        run_id=RUN,
        run_fencing_token=7,
        agent_version_id=AGENT_VERSION,
        agent_version_digest=DIGEST,
        proposal_digest=DIGEST,
        node_id=NODE,
    )


def _credential(*, tenant_id: str = TENANT, workspace_id: str = WORKSPACE) -> WorkloadCredential:
    return WorkloadCredential(
        authorization="Capability server-owned-token",
        identity="runtime-workload",
        trusted_context=TrustedWorkloadContext(
            opaque_identity="runtime-workload",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            runtime_instance_id="runtime-1",
            certificate_thumbprint="thumbprint",
        ),
    )


def _response() -> RagSearchResponse:
    return RagSearchResponse(
        resource_id=UUID(RESOURCE),
        results=[
            SearchHitRead(
                citation_id=uuid4(),
                document_id=uuid4(),
                score=0.91,
                snippet="bounded result",
                page_number=2,
            )
        ],
        total_found=1,
        bytes_out=128,
        truncated=False,
    )


def _port(*, credential=None, authority=None, gateway=None, session=None):
    session = session or _ClosedSession()
    if gateway is None:
        gateway = Mock()
        gateway.rag_search.return_value = _response()
    authority = authority or _Authority()
    credential = credential or _credential()
    return (
        CapabilityGatewayKnowledgeSearchPort(
            gateway=gateway,
            session_factory=lambda: session,
            credential_provider=lambda *, context: credential,
            authority_validator=authority,
            request_id_factory=lambda context, request: "request-1",
        ),
        gateway,
        authority,
        session,
    )


def test_gateway_adapter_calls_existing_service_and_closes_session() -> None:
    port, gateway, authority, session = _port()
    result = port.search(
        context=_context(),
        request=KnowledgeSearchRequest(resource_id=RESOURCE, query="hello"),
    )

    assert result.resource_id == RESOURCE
    assert len(result.results) == 1
    assert authority.calls == 1
    assert session.closed is True
    gateway.rag_search.assert_called_once()
    payload = gateway.rag_search.call_args.args[2]
    assert isinstance(payload, RagSearchRequest)
    assert str(payload.resource_id) == RESOURCE
    assert gateway.rag_search.call_args.args[3] == "request-1"


def test_gateway_adapter_rejects_cross_workspace_credential_without_gateway_call() -> None:
    port, gateway, authority, _ = _port(credential=_credential(workspace_id=str(uuid4())))

    with pytest.raises(GatewayAdapterDenied, match="workload_workspace_mismatch"):
        port.search(context=_context(), request=KnowledgeSearchRequest(RESOURCE, "hello"))

    gateway.rag_search.assert_not_called()
    assert authority.calls == 0


def test_gateway_adapter_rejects_stale_runtime_lease() -> None:
    port, gateway, authority, _ = _port(authority=_Authority(RuntimeError("stale")))

    with pytest.raises(GatewayAdapterDenied, match="runtime_authority_denied"):
        port.search(context=_context(), request=KnowledgeSearchRequest(RESOURCE, "hello"))

    gateway.rag_search.assert_not_called()
    assert authority.calls == 1


@pytest.mark.parametrize(
    "failure",
    [
        GatewayFailure(403, "capability_scope_denied", "hidden"),
        GatewayFailure(429, "capability_budget_exceeded", "hidden"),
    ],
)
def test_gateway_denials_are_fail_closed_and_not_retried(failure: GatewayFailure) -> None:
    session = _ClosedSession()
    gateway = Mock()
    gateway.rag_search.side_effect = failure
    port, _, _, _ = _port(gateway=gateway, session=session)

    with pytest.raises(GatewayAdapterError) as raised:
        port.search(context=_context(), request=KnowledgeSearchRequest(RESOURCE, "hello"))

    assert raised.value.code == f"gateway_{failure.code}"
    assert gateway.rag_search.call_count == 1
    assert session.closed is True


def test_gateway_adapter_rejects_unavailable_credential() -> None:
    gateway = Mock()
    session = _ClosedSession()
    port = CapabilityGatewayKnowledgeSearchPort(
        gateway=gateway,
        session_factory=lambda: session,
        credential_provider=lambda *, context: (_ for _ in ()).throw(RuntimeError("missing")),
        authority_validator=_Authority(),
    )

    with pytest.raises(GatewayAdapterUnavailable, match="workload_credential_unavailable"):
        port.search(context=_context(), request=KnowledgeSearchRequest(RESOURCE, "hello"))

    gateway.rag_search.assert_not_called()
    assert session.closed is False


def test_gateway_adapter_rejects_physical_locator_without_leaking_it() -> None:
    port, gateway, _, _ = _port()
    locator = "postgresql://db/internal/schema/table"

    with pytest.raises(GatewayAdapterDenied) as raised:
        port.search(
            context=_context(),
            request=KnowledgeSearchRequest(resource_id=locator, query="hello"),
        )

    assert "postgresql" not in str(raised.value)
    gateway.rag_search.assert_not_called()


def test_gateway_response_scope_mismatch_is_not_a_success() -> None:
    gateway = Mock()
    gateway.rag_search.return_value = RagSearchResponse(
        resource_id=uuid4(), results=[], total_found=0, bytes_out=0, truncated=False
    )
    port, _, _, _ = _port(gateway=gateway)

    with pytest.raises(GatewayAdapterError, match="gateway_resource_scope_mismatch"):
        port.search(context=_context(), request=KnowledgeSearchRequest(RESOURCE, "hello"))

    assert gateway.rag_search.call_count == 1
