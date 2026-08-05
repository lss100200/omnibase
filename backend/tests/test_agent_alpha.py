"""Engineering-only tests for the tool-free single-Agent Alpha."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnibase.agent_alpha.contracts import (
    AlphaAgentProfile,
    AlphaContextChunk,
    AlphaGatewaySelection,
    AlphaInvocationIdentity,
)
from omnibase.agent_alpha.router import get_agent_alpha, router
from omnibase.agent_alpha.service import AgentAlphaError, AgentAlphaService
from omnibase.model_gateway import (
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
    ModelUsage,
)
from omnibase.model_gateway.providers import ModelProviderError
from omnibase.tenants.dependencies import get_current_tenant


class _Provider:
    provider_id = "fake-provider"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError(request)

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        assert all(message.role in {"system", "user", "assistant"} for message in request.messages)
        yield ModelStreamChunk(
            provider_id=self.provider_id,
            requested_model_id=request.model_id,
            actual_model_id=request.model_id,
            content="grounded ",
        )
        yield ModelStreamChunk(
            provider_id=self.provider_id,
            requested_model_id=request.model_id,
            actual_model_id=request.model_id,
            content="answer [1]",
            finish_reason="stop",
            usage=ModelUsage(input_tokens=10, output_tokens=3, total_tokens=13),
        )


class _Profiles:
    allowed_tool_ids: tuple[str, ...] = ()

    def resolve(self, **_: object) -> AlphaAgentProfile:
        return AlphaAgentProfile(
            agent_definition_id="00000000-0000-0000-0000-000000000001",
            agent_version_id="00000000-0000-0000-0000-000000000002",
            agent_version_digest="a" * 64,
            display_name="Research Alpha",
            instructions="Answer using the workspace context and cite it.",
            instructions_digest="b" * 64,
            max_context_tokens=1024,
            allowed_tool_ids=self.allowed_tool_ids,
            workspace_agent_binding_id="00000000-0000-0000-0000-000000000003",
            resource_scope_digest="c" * 64,
            budget_policy_digest="d" * 64,
        )


class _Knowledge:
    def retrieve(self, **_: object) -> tuple[AlphaContextChunk, ...]:
        return (
            AlphaContextChunk(
                chunk_id="chunk-1",
                document_id="document-1",
                content="verified workspace fact",
                score=0.9,
            ),
        )


class _Ledger:
    def __init__(self) -> None:
        self.completed: tuple[str, ModelUsage] | None = None
        self.failed: tuple[str, str] | None = None
        self.begin_request_hash: str | None = None

    def begin(self, **kwargs: object) -> AlphaInvocationIdentity:
        self.begin_request_hash = str(kwargs["request_hash"])
        return AlphaInvocationIdentity(
            invocation_id="00000000-0000-0000-0000-000000000010",
            task_id="00000000-0000-0000-0000-000000000011",
            attempt_id="00000000-0000-0000-0000-000000000012",
            effect_id="00000000-0000-0000-0000-000000000013",
            tenant_id="tenant",
            workspace_id="workspace",
            actor_user_id="user",
        )

    def complete(
        self,
        *,
        identity: AlphaInvocationIdentity,
        result_digest: str,
        usage: ModelUsage,
    ) -> None:
        del identity
        self.completed = (result_digest, usage)

    def fail(
        self,
        *,
        identity: AlphaInvocationIdentity,
        outcome: str,
        error_code: str,
    ) -> None:
        del identity
        self.failed = (outcome, error_code)


def _service(
    *, profiles: _Profiles | None = None, ledger: _Ledger | None = None
) -> AgentAlphaService:
    return AgentAlphaService(
        profiles=profiles or _Profiles(),
        knowledge=_Knowledge(),
        ledger=ledger or _Ledger(),
        gateway=ModelGateway(provider=_Provider(), model_id="model-alpha"),
    )


def test_single_agent_stream_is_tool_free_and_persisted() -> None:
    ledger = _Ledger()
    events = list(
        _service(ledger=ledger).invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="What is verified?",
            top_k=5,
            idempotency_key="key",
            retry_of=None,
        )
    )

    assert [event.kind for event in events] == [
        "meta",
        "citations",
        "chunk",
        "chunk",
        "usage",
        "done",
    ]
    assert events[0].payload["tools_enabled"] is False
    assert events[-1].payload["answer"] == "grounded answer [1]"
    assert events[-1].payload["actual_model_id"] == "model-alpha"
    assert ledger.completed is not None
    assert ledger.completed[1].total_tokens == 13
    assert ledger.failed is None


class _GatewayResolver:
    def __init__(self, configuration_digest: str) -> None:
        self.configuration_digest = configuration_digest

    def resolve(self, **_: object) -> AlphaGatewaySelection:
        return AlphaGatewaySelection(
            gateway=ModelGateway(provider=_Provider(), model_id="model-alpha"),
            credential_source="personal",
            configuration_digest=self.configuration_digest,
            credential_id="credential-1",
        )


def test_invocation_intent_binds_non_secret_provider_configuration() -> None:
    first_ledger = _Ledger()
    first = AgentAlphaService(
        profiles=_Profiles(),
        knowledge=_Knowledge(),
        ledger=first_ledger,
        gateway=ModelGateway(provider=_Provider(), model_id="model-alpha"),
        gateway_resolver=_GatewayResolver("a" * 64),
    )
    list(
        first.invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="hello",
            top_k=1,
            idempotency_key="key",
            retry_of=None,
        )
    )

    second_ledger = _Ledger()
    second = AgentAlphaService(
        profiles=_Profiles(),
        knowledge=_Knowledge(),
        ledger=second_ledger,
        gateway=ModelGateway(provider=_Provider(), model_id="model-alpha"),
        gateway_resolver=_GatewayResolver("b" * 64),
    )
    list(
        second.invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="hello",
            top_k=1,
            idempotency_key="key",
            retry_of=None,
        )
    )

    assert first_ledger.begin_request_hash is not None
    assert second_ledger.begin_request_hash is not None
    assert first_ledger.begin_request_hash != second_ledger.begin_request_hash


def test_profile_with_tools_is_rejected_before_model_or_ledger() -> None:
    profiles = _Profiles()
    profiles.allowed_tool_ids = ("shell",)
    with pytest.raises(AgentAlphaError, match="agent_alpha_tools_forbidden") as exc_info:
        _service(profiles=profiles).invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="hello",
            top_k=5,
            idempotency_key="key",
            retry_of=None,
        )
    assert exc_info.value.code == "agent_alpha_tools_forbidden"


def test_cancel_is_bound_to_tenant_workspace_and_actor() -> None:
    service = _service()
    stream = service.invoke(
        tenant_id="tenant",
        tenant_schema="tenant_schema",
        workspace_id="workspace",
        actor_user_id="user",
        agent_version_id="version",
        message="hello",
        top_k=5,
        idempotency_key="key",
        retry_of=None,
    )
    first = next(stream)
    invocation_id = str(first.payload["invocation_id"])

    assert not service.cancel(
        tenant_id="other-tenant",
        workspace_id="workspace",
        actor_user_id="user",
        invocation_id=invocation_id,
    )
    assert not service.cancel(
        tenant_id="tenant",
        workspace_id="other-workspace",
        actor_user_id="user",
        invocation_id=invocation_id,
    )
    assert not service.cancel(
        tenant_id="tenant",
        workspace_id="workspace",
        actor_user_id="other-user",
        invocation_id=invocation_id,
    )
    assert service.cancel(
        tenant_id="tenant",
        workspace_id="workspace",
        actor_user_id="user",
        invocation_id=invocation_id,
    )
    assert next(stream).kind == "citations"
    assert next(stream).kind == "cancelled"


def test_browser_default_rejects_before_runtime_activation() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: SimpleNamespace(
        tenant_id="tenant",
        schema_name="tenant_schema",
        user_id="user",
    )
    client = TestClient(app)

    response = client.post(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000020/agent-alpha/invoke",
        json={
            "agent_version_id": "00000000-0000-0000-0000-000000000002",
            "message": "hello",
        },
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "agent_alpha_unavailable"
    assert get_agent_alpha not in app.dependency_overrides


def test_browser_override_streams_sse_without_enabling_production_default() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: SimpleNamespace(
        tenant_id="tenant",
        schema_name="tenant_schema",
        user_id="user",
    )
    app.dependency_overrides[get_agent_alpha] = lambda: _service()
    client = TestClient(app)

    response = client.post(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000020/agent-alpha/invoke",
        headers={"Idempotency-Key": "test-key"},
        json={
            "agent_version_id": "00000000-0000-0000-0000-000000000002",
            "message": "hello",
        },
    )

    assert response.status_code == 200
    assert "event: meta" in response.text
    assert "event: done" in response.text
    assert "grounded answer [1]" in response.text


class _RaisingProfiles:
    def __init__(self, error: Exception) -> None:
        self._error = error

    def resolve(self, **_: object) -> AlphaAgentProfile:
        raise self._error


class _FailingProvider:
    provider_id = "failing-provider"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError(request)

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        del request
        raise ModelProviderError("agent_alpha_provider_failed")


class _NoIdentityProvider:
    provider_id = "no-identity-provider"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError(request)

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        del request
        yield ModelStreamChunk(
            provider_id=self.provider_id,
            requested_model_id="model-alpha",
            actual_model_id=None,
            content="no identity",
        )


def test_message_over_server_limit_is_rejected() -> None:
    from omnibase.agent_alpha.service import AlphaLimits

    service = AgentAlphaService(
        profiles=_Profiles(),
        knowledge=_Knowledge(),
        ledger=_Ledger(),
        gateway=ModelGateway(provider=_Provider(), model_id="model-alpha"),
        limits=AlphaLimits(max_message_characters=4),
    )
    with pytest.raises(AgentAlphaError, match="agent_alpha_message_too_large"):
        service.invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="a" * 5,
            top_k=5,
            idempotency_key="key",
            retry_of=None,
        )


def test_top_k_over_server_limit_is_rejected() -> None:
    from omnibase.agent_alpha.service import AlphaLimits

    service = AgentAlphaService(
        profiles=_Profiles(),
        knowledge=_Knowledge(),
        ledger=_Ledger(),
        gateway=ModelGateway(provider=_Provider(), model_id="model-alpha"),
        limits=AlphaLimits(max_rag_chunks=2),
    )
    with pytest.raises(AgentAlphaError, match="agent_alpha_top_k_exceeded"):
        service.invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="hello",
            top_k=5,
            idempotency_key="key",
            retry_of=None,
        )


class _SlowProvider:
    provider_id = "slow-provider"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError(request)

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        del request
        import time

        time.sleep(0.05)
        yield ModelStreamChunk(
            provider_id=self.provider_id,
            requested_model_id="model-alpha",
            actual_model_id="model-alpha",
            content="slow",
        )


def test_invocation_deadline_is_enforced() -> None:
    from omnibase.agent_alpha.service import AlphaLimits

    ledger = _Ledger()
    service = AgentAlphaService(
        profiles=_Profiles(),
        knowledge=_Knowledge(),
        ledger=ledger,
        gateway=ModelGateway(provider=_SlowProvider(), model_id="model-alpha"),
        limits=AlphaLimits(invocation_deadline_seconds=0.01),
    )
    events = list(
        service.invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="hello",
            top_k=1,
            idempotency_key="key",
            retry_of=None,
        )
    )
    assert events[-1].kind == "error"
    assert events[-1].payload["code"] == "agent_alpha_invocation_deadline_exceeded"
    assert ledger.failed == ("unknown", "agent_alpha_invocation_deadline_exceeded")


def test_missing_actual_model_identity_fails_closed() -> None:
    ledger = _Ledger()
    service = AgentAlphaService(
        profiles=_Profiles(),
        knowledge=_Knowledge(),
        ledger=ledger,
        gateway=ModelGateway(provider=_NoIdentityProvider(), model_id="model-alpha"),
    )
    events = list(
        service.invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="hello",
            top_k=1,
            idempotency_key="key",
            retry_of=None,
        )
    )
    assert events[-1].kind == "error"
    assert events[-1].payload["code"] == "agent_alpha_model_identity_missing"
    assert ledger.failed == ("unknown", "agent_alpha_model_identity_missing")


def test_provider_error_records_unknown_and_never_cancelled() -> None:
    ledger = _Ledger()
    service = AgentAlphaService(
        profiles=_Profiles(),
        knowledge=_Knowledge(),
        ledger=ledger,
        gateway=ModelGateway(provider=_FailingProvider(), model_id="model-alpha"),
    )
    events = list(
        service.invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="hello",
            top_k=1,
            idempotency_key="key",
            retry_of=None,
        )
    )
    assert events[-1].kind == "error"
    assert events[-1].payload["code"] == "agent_alpha_provider_unavailable"
    assert ledger.failed == ("unknown", "agent_alpha_provider_outcome_unknown")


def test_sse_disconnect_records_unknown_not_cancelled() -> None:
    ledger = _Ledger()
    service = AgentAlphaService(
        profiles=_Profiles(),
        knowledge=_Knowledge(),
        ledger=ledger,
        gateway=ModelGateway(provider=_Provider(), model_id="model-alpha"),
    )
    stream = service.invoke(
        tenant_id="tenant",
        tenant_schema="tenant_schema",
        workspace_id="workspace",
        actor_user_id="user",
        agent_version_id="version",
        message="hello",
        top_k=1,
        idempotency_key="key",
        retry_of=None,
    )
    next(stream)  # meta
    next(stream)  # citations
    stream.close()
    assert ledger.failed == ("unknown", "agent_alpha_sse_disconnected")


def test_adapter_unavailable_maps_to_agent_alpha_unavailable() -> None:
    from omnibase.agent_alpha.adapters import AlphaAdapterUnavailable

    service = AgentAlphaService(
        profiles=_RaisingProfiles(AlphaAdapterUnavailable("agent_alpha_binding_not_live")),
        knowledge=_Knowledge(),
        ledger=_Ledger(),
        gateway=ModelGateway(provider=_Provider(), model_id="model-alpha"),
    )
    from omnibase.agent_alpha.service import AgentAlphaUnavailable

    with pytest.raises(AgentAlphaUnavailable, match="agent_alpha_binding_not_live"):
        service.invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="hello",
            top_k=1,
            idempotency_key="key",
            retry_of=None,
        )


def test_cancel_after_terminal_invocation_returns_false() -> None:
    service = _service()
    events = list(
        service.invoke(
            tenant_id="tenant",
            tenant_schema="tenant_schema",
            workspace_id="workspace",
            actor_user_id="user",
            agent_version_id="version",
            message="hello",
            top_k=1,
            idempotency_key="key",
            retry_of=None,
        )
    )
    invocation_id = str(events[0].payload["invocation_id"])
    assert not service.cancel(
        tenant_id="tenant",
        workspace_id="workspace",
        actor_user_id="user",
        invocation_id=invocation_id,
    )
