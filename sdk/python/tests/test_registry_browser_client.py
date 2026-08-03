"""P5.1C Browser Agent Registry SDK client tests (fake transport, no network)."""

from __future__ import annotations

import pytest

from omnibase_sdk.browser_registry import (
    AgentDefinitionRead,
    AgentInstallationRead,
    AgentRegistryBrowserClient,
    AgentVersionRead,
    BrowserHttpTransport,
    RegistryBrowserError,
    StaticAccessTokenProvider,
    TransportResponse,
)

WORKSPACE_ID = "11111111-1111-4111-8111-111111111111"
DEFINITION_ID = "22222222-2222-4222-8222-222222222222"
VERSION_ID = "33333333-3333-4333-8333-333333333333"
BINDING_ID = "44444444-4444-4444-8444-444444444444"
DIGEST = "4b5a26ba3980e80216db50d8d069a6c052ca472954c33247baa1b81ec69f91ca"

DEFINITION_BODY = {
    "agent_definition_id": DEFINITION_ID,
    "stable_logical_key": "agent-gate",
    "display_name": "Gate Agent",
    "description": None,
    "risk_level": "low",
    "definition_state": "active",
    "metadata_version": 1,
    "created_at": "2026-08-03T00:00:00Z",
}

VERSION_BODY = {
    "agent_version_id": VERSION_ID,
    "agent_definition_id": DEFINITION_ID,
    "version": "1.0.0",
    "version_state": "sealed",
    "manifest_digest": DIGEST,
    "instructions_digest": DIGEST,
    "risk_level": "low",
    "max_context_tokens": 200000,
    "allowed_tool_ids": ["rag_search"],
    "max_concurrency": 2,
    "created_at": "2026-08-03T00:00:00Z",
}

BINDING_BODY = {
    "binding_id": BINDING_ID,
    "workspace_id": WORKSPACE_ID,
    "workspace_generation": 1,
    "agent_definition_id": DEFINITION_ID,
    "agent_version_id": VERSION_ID,
    "agent_version_digest": DIGEST,
    "binding_state": "installed",
    "resource_scopes": ["workspace_private_read"],
    "default_budget_policy": {
        "max_tokens": 50000,
        "max_cost_units": 500,
        "max_wall_clock_seconds": 300,
        "max_tool_calls": 50,
    },
    "created_at": "2026-08-03T00:00:00Z",
    "disabled_at": None,
    "superseded_by": None,
}


class FakeTransport:
    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, object] | None,
        *,
        idempotency_key: str | None = None,
    ) -> TransportResponse:
        self.calls.append(
            {"method": method, "path": path, "body": body, "idempotency_key": idempotency_key}
        )
        return self.response


def _client(response: TransportResponse) -> tuple[AgentRegistryBrowserClient, FakeTransport]:
    transport = FakeTransport(response)
    return AgentRegistryBrowserClient(transport), transport


def test_catalog_reads_use_logical_paths_and_parse_projection() -> None:
    client, transport = _client(TransportResponse(200, {"x-request-id": "req-1"}, DEFINITION_BODY))
    result = client.get_agent_definition(DEFINITION_ID)
    assert isinstance(result, AgentDefinitionRead)
    assert result.agent_definition_id == DEFINITION_ID
    assert transport.calls[0]["path"] == f"/api/v1/agent-definitions/{DEFINITION_ID}"
    assert transport.calls[0]["method"] == "GET"

    client, transport = _client(
        TransportResponse(200, {"x-request-id": "req-2"}, {"items": [VERSION_BODY], "total": 1})
    )
    versions, total = client.list_agent_versions(DEFINITION_ID)
    assert total == 1
    assert isinstance(versions[0], AgentVersionRead)
    assert versions[0].manifest_digest == DIGEST

    client, transport = _client(TransportResponse(200, {"x-request-id": "req-3"}, BINDING_BODY))
    binding = client.get_installation(WORKSPACE_ID, BINDING_ID)
    assert isinstance(binding, AgentInstallationRead)
    assert binding.binding_state == "installed"
    assert binding.default_budget_policy.max_tokens == 50000


def test_install_sends_deterministic_body_with_idempotency_key() -> None:
    client, transport = _client(TransportResponse(201, {"x-request-id": "req-4"}, BINDING_BODY))
    client.install(
        workspace_id=WORKSPACE_ID,
        idempotency_key="p51c-sdk-key-0001",
        agent_definition_id=DEFINITION_ID,
        agent_version_id=VERSION_ID,
        agent_version_digest=DIGEST,
        workspace_generation=1,
        resource_scopes=["workspace_private_read"],
        default_budget_policy={
            "max_tokens": 50000,
            "max_cost_units": 500,
            "max_wall_clock_seconds": 300,
            "max_tool_calls": 50,
        },
    )
    call = transport.calls[0]
    assert call["method"] == "POST"
    assert call["idempotency_key"] == "p51c-sdk-key-0001"
    body = call["body"]
    assert body["workspace_generation"] == 1
    assert "approval_id" not in body


def test_upgrade_and_disable_paths() -> None:
    client, transport = _client(TransportResponse(200, {"x-request-id": "req-5"}, BINDING_BODY))
    client.upgrade(
        workspace_id=WORKSPACE_ID,
        binding_id=BINDING_ID,
        idempotency_key="p51c-upgrade-key-0001",
        target_agent_version_id=VERSION_ID,
        target_agent_version_digest=DIGEST,
        expected_binding_id=BINDING_ID,
    )
    assert transport.calls[0]["path"] == (
        f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations/{BINDING_ID}/upgrade"
    )
    assert transport.calls[0]["body"]["expected_binding_id"] == BINDING_ID

    client, transport = _client(TransportResponse(200, {"x-request-id": "req-6"}, BINDING_BODY))
    client.disable(
        workspace_id=WORKSPACE_ID,
        binding_id=BINDING_ID,
        idempotency_key="p51c-disable-key-0001",
    )
    assert transport.calls[0]["path"].endswith("/disable")
    assert transport.calls[0]["body"] is None


def test_registry_error_preserves_envelope_code() -> None:
    client, transport = _client(
        TransportResponse(
            503,
            {"x-request-id": "req-unavailable"},
            {"error": {"code": "agent_registry_unavailable", "message": "Not assembled"}},
        )
    )
    with pytest.raises(RegistryBrowserError) as captured:
        client.list_agent_definitions()
    assert captured.value.code == "agent_registry_unavailable"
    assert captured.value.request_id == "req-unavailable"


def test_error_envelope_rejects_extra_fields() -> None:
    client, transport = _client(
        TransportResponse(409, {"x-request-id": "req-7"}, {"error": {"code": "conflict"}})
    )
    with pytest.raises(RegistryBrowserError) as captured:
        client.install(
            workspace_id=WORKSPACE_ID,
            idempotency_key="p51c-extra-key-0001",
            agent_definition_id=DEFINITION_ID,
            agent_version_id=VERSION_ID,
            agent_version_digest=DIGEST,
            workspace_generation=1,
            resource_scopes=["workspace_private_read"],
            default_budget_policy={
                "max_tokens": 1,
                "max_cost_units": 1,
                "max_wall_clock_seconds": 1,
                "max_tool_calls": 1,
            },
        )
    assert captured.value.code == "invalid_browser_response"


def test_wildcard_scope_is_rejected_client_side() -> None:
    client, _ = _client(TransportResponse(201, {"x-request-id": "req-8"}, BINDING_BODY))
    with pytest.raises(ValueError, match="wildcard"):
        client.install(
            workspace_id=WORKSPACE_ID,
            idempotency_key="p51c-scope-key-0001",
            agent_definition_id=DEFINITION_ID,
            agent_version_id=VERSION_ID,
            agent_version_digest=DIGEST,
            workspace_generation=1,
            resource_scopes=["*"],
            default_budget_policy={
                "max_tokens": 1,
                "max_cost_units": 1,
                "max_wall_clock_seconds": 1,
                "max_tool_calls": 1,
            },
        )


def test_browser_transport_rejects_non_api_v1_paths() -> None:
    transport = BrowserHttpTransport(
        "https://omnibase.example.invalid",
        StaticAccessTokenProvider("token-value"),
    )
    with pytest.raises(ValueError, match="/api/v1"):
        transport.request("GET", "/gateway/v1/data/schema/read", None)
    with pytest.raises(ValueError, match="/api/v1"):
        transport.request("DELETE", "/api/v1/agent-definitions", None)
    for escaped in (
        "/api/v1/../../gateway/v1/probe",
        "/api/v1/%2e%2e/gateway/v1/probe",
        "/api/v1/agent-definitions?next=/gateway/v1",
        "/api/v1\\..\\gateway\\v1\\probe",
        "/api/v1//agent-definitions",
    ):
        with pytest.raises(ValueError, match="path|segments|/api/v1"):
            transport.request("GET", escaped, None)


def test_response_models_reject_unknown_fields_and_invalid_closed_states() -> None:
    with pytest.raises(ValueError, match="fields"):
        AgentDefinitionRead.from_dict(
            {**DEFINITION_BODY, "physical_schema_locator": "tenant_secret"}
        )
    with pytest.raises(ValueError, match="closed set"):
        AgentVersionRead.from_dict({**VERSION_BODY, "version_state": "running"})
    with pytest.raises(ValueError, match="closed set"):
        AgentInstallationRead.from_dict({**BINDING_BODY, "binding_state": "executing"})


def test_browser_transport_requires_https_origin() -> None:
    with pytest.raises(ValueError, match="HTTPS"):
        BrowserHttpTransport(
            "http://omnibase.example.invalid",
            StaticAccessTokenProvider("token-value"),
        )
