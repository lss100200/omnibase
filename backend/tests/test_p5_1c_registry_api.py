"""P5.1C Browser Agent Registry control API unit tests (no database)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnibase.agent_registry.control import (
    AgentRegistryControlService,
    RegistryControlPlaneUnavailable,
    UnavailableAgentRegistryControlPlane,
)
from omnibase.agent_registry.router import (
    get_registry_control_plane,
    installation_router,
    router,
)
from omnibase.tenants.dependencies import get_current_tenant

TENANT_ID = "00000000-0000-0000-0000-00000000000a"
USER_ID = "00000000-0000-0000-0000-0000000000aa"
WORKSPACE_ID = "66666666-6666-6666-6666-666666666666"
DEFINITION_ID = "00000000-0000-0000-0000-000000000001"
VERSION_ID = "11111111-1111-1111-1111-111111111111"
BINDING_ID = "55555555-5555-5555-5555-555555555555"
DIGEST = "4b5a26ba3980e80216db50d8d069a6c052ca472954c33247baa1b81ec69f91ca"
HEADERS = {"Idempotency-Key": "p51c-test-key-0001"}


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(tenant_id=TENANT_ID, user_id=USER_ID)


def _install_error_handler(app: FastAPI) -> None:
    from typing import Any

    from fastapi import HTTPException
    from fastapi.responses import JSONResponse

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Any, exc: HTTPException) -> JSONResponse:
        detail: Any = exc.detail
        if isinstance(detail, dict) and "error" in detail:
            content = detail
        else:
            content = {"error": {"code": "error", "message": str(detail)}}
        return JSONResponse(status_code=exc.status_code, content=content)


def _client(control: object | None = None) -> TestClient:
    from fastapi import APIRouter

    test_app = FastAPI()
    _install_error_handler(test_app)
    prefix = APIRouter(prefix="/api/v1")
    prefix.include_router(router)
    prefix.include_router(installation_router)
    test_app.include_router(prefix)
    test_app.dependency_overrides[get_current_tenant] = _ctx
    if control is not None:
        test_app.dependency_overrides[get_registry_control_plane] = lambda: control
    return TestClient(test_app, raise_server_exceptions=False)


def _install_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "agent_definition_id": DEFINITION_ID,
        "agent_version_id": VERSION_ID,
        "agent_version_digest": DIGEST,
        "workspace_generation": 1,
        "resource_scopes": ["workspace_private_read"],
        "default_budget_policy": {
            "max_tokens": 50000,
            "max_cost_units": 500,
            "max_wall_clock_seconds": 300,
            "max_tool_calls": 50,
        },
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Default fail-closed composition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/v1/agent-definitions"),
        ("GET", f"/api/v1/agent-definitions/{DEFINITION_ID}"),
        ("GET", f"/api/v1/agent-definitions/{DEFINITION_ID}/versions"),
        ("GET", f"/api/v1/agent-definitions/{DEFINITION_ID}/versions/{VERSION_ID}"),
        ("GET", f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations"),
        ("GET", f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations/{BINDING_ID}"),
        ("POST", f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations"),
        ("POST", f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations/{BINDING_ID}/disable"),
        ("POST", f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations/{BINDING_ID}/upgrade"),
        ("POST", f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations/{BINDING_ID}/rollback"),
    ],
)
def test_default_composition_rejects_with_503(method: str, path: str) -> None:
    client = _client()  # no control-plane override: fail closed
    body: dict[str, object] | None = _install_payload()
    if path.endswith("/upgrade"):
        body = {
            "target_agent_version_id": VERSION_ID,
            "target_agent_version_digest": DIGEST,
        }
    elif path.endswith("/rollback"):
        body = {
            "rollback_agent_version_id": VERSION_ID,
            "rollback_agent_version_digest": DIGEST,
        }
    elif path.endswith("/disable"):
        body = None
    response = client.request(method, path, headers=HEADERS, json=body)
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "agent_registry_unavailable"


def test_rejecting_authorizer_raises_stable_error_without_database() -> None:
    plane = UnavailableAgentRegistryControlPlane()
    for operation in (
        "list_definitions",
        "get_definition",
        "list_versions",
        "get_version",
        "list_installations",
        "get_installation",
        "install",
        "upgrade",
        "disable",
        "rollback",
    ):
        with pytest.raises(RegistryControlPlaneUnavailable) as exc_info:
            getattr(plane, operation)(tenant_id=TENANT_ID)
        assert exc_info.value.code == "agent_registry_unavailable"
        assert exc_info.value.status == 503


# ---------------------------------------------------------------------------
# Request DTO strictness
# ---------------------------------------------------------------------------


def test_install_rejects_wildcard_resource_scope() -> None:
    control = MagicMock(spec=AgentRegistryControlService)
    client = _client(control)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations",
        headers=HEADERS,
        json=_install_payload(resource_scopes=["*"]),
    )
    assert response.status_code == 422
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations",
        headers=HEADERS,
        json=_install_payload(agent_version_digest="short"),
    )
    assert response.status_code == 422


def test_install_rejects_unknown_fields() -> None:
    control = MagicMock(spec=AgentRegistryControlService)
    client = _client(control)
    payload = _install_payload()
    payload["tenant_id"] = TENANT_ID
    payload["installed_by"] = USER_ID
    payload["schema_name"] = "tenant_secret"
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations",
        headers=HEADERS,
        json=payload,
    )
    assert response.status_code == 422


def test_install_rejects_non_uuid_and_bad_digest() -> None:
    control = MagicMock(spec=AgentRegistryControlService)
    client = _client(control)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations",
        headers=HEADERS,
        json=_install_payload(agent_definition_id="not-a-uuid"),
    )
    assert response.status_code == 422


def test_path_identifier_rejects_invalid_uuid_with_stable_422() -> None:
    control = MagicMock(spec=AgentRegistryControlService)
    client = _client(control)
    response = client.get("/api/v1/agent-definitions/not-a-uuid")
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_logical_identifier",
            "message": "Logical identifier must be a valid UUID",
        }
    }
    control.get_definition.assert_not_called()


def test_mutation_requires_idempotency_key() -> None:
    control = MagicMock(spec=AgentRegistryControlService)
    client = _client(control)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations",
        json=_install_payload(),
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Mutation dispatch and server-derived fields
# ---------------------------------------------------------------------------


def test_install_dispatches_with_server_derived_identity() -> None:
    control = MagicMock(spec=AgentRegistryControlService)
    control.install.return_value = _fake_installation_read()
    client = _client(control)
    response = client.post(
        f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations",
        headers=HEADERS,
        json=_install_payload(),
    )
    assert response.status_code == 201
    call = control.install.call_args
    assert call.kwargs["tenant_id"] == TENANT_ID
    assert call.kwargs["actor_user_id"] == USER_ID
    assert call.kwargs["workspace_id"] == WORKSPACE_ID
    assert call.kwargs["idempotency_key"] == HEADERS["Idempotency-Key"]
    assert call.kwargs["payload"].agent_definition_id == DEFINITION_ID


def test_upgrade_disable_rollback_dispatch() -> None:
    control = MagicMock(spec=AgentRegistryControlService)
    control.upgrade.return_value = _fake_installation_read()
    control.disable.return_value = _fake_installation_read()
    control.rollback.return_value = _fake_installation_read()
    client = _client(control)
    base = f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations/{BINDING_ID}"

    response = client.post(
        f"{base}/upgrade",
        headers=HEADERS,
        json={
            "target_agent_version_id": VERSION_ID,
            "target_agent_version_digest": DIGEST,
        },
    )
    assert response.status_code == 200
    assert control.upgrade.call_args.kwargs["actor_user_id"] == USER_ID

    response = client.post(f"{base}/disable", headers=HEADERS)
    assert response.status_code == 200
    assert control.disable.call_args.kwargs["binding_id"] == BINDING_ID

    response = client.post(
        f"{base}/rollback",
        headers=HEADERS,
        json={
            "rollback_agent_version_id": VERSION_ID,
            "rollback_agent_version_digest": DIGEST,
        },
    )
    assert response.status_code == 200
    assert control.rollback.call_args.kwargs["idempotency_key"] == HEADERS["Idempotency-Key"]


def test_catalog_and_installation_reads_require_tenant_scope() -> None:
    control = MagicMock(spec=AgentRegistryControlService)
    control.list_definitions.return_value = MagicMock(items=[], total=0)
    control.list_installations.return_value = MagicMock(items=[], total=0)
    client = _client(control)
    response = client.get("/api/v1/agent-definitions")
    assert response.status_code == 200
    assert control.list_definitions.call_args.kwargs["tenant_id"] == TENANT_ID

    response = client.get(f"/api/v1/workspaces/{WORKSPACE_ID}/agent-installations")
    assert response.status_code == 200
    assert control.list_installations.call_args.kwargs["workspace_id"] == WORKSPACE_ID
    assert control.list_installations.call_args.kwargs["user_id"] == USER_ID


# ---------------------------------------------------------------------------
# OpenAPI contract
# ---------------------------------------------------------------------------


def _browser_app() -> FastAPI:
    from fastapi import APIRouter

    app = FastAPI()
    prefix = APIRouter(prefix="/api/v1")
    prefix.include_router(router)
    prefix.include_router(installation_router)
    app.include_router(prefix)
    return app


def _fake_installation_read() -> object:
    from omnibase.agent_registry.schemas import AgentInstallationRead

    return AgentInstallationRead(
        binding_id=BINDING_ID,
        workspace_id=WORKSPACE_ID,
        workspace_generation=1,
        agent_definition_id=DEFINITION_ID,
        agent_version_id=VERSION_ID,
        agent_version_digest=DIGEST,
        binding_state="installed",
        resource_scopes=["workspace_private_read"],
        default_budget_policy={
            "max_tokens": 50000,
            "max_cost_units": 500,
            "max_wall_clock_seconds": 300,
            "max_tool_calls": 50,
        },
    )


def test_openapi_exposes_only_logical_endpoints() -> None:
    paths = set(_browser_app().openapi()["paths"])
    expected = {
        "/api/v1/agent-definitions",
        "/api/v1/agent-definitions/{agent_definition_id}",
        "/api/v1/agent-definitions/{agent_definition_id}/versions",
        "/api/v1/agent-definitions/{agent_definition_id}/versions/{agent_version_id}",
        "/api/v1/workspaces/{workspace_id}/agent-installations",
        "/api/v1/workspaces/{workspace_id}/agent-installations/{binding_id}",
        "/api/v1/workspaces/{workspace_id}/agent-installations/{binding_id}/disable",
        "/api/v1/workspaces/{workspace_id}/agent-installations/{binding_id}/upgrade",
        "/api/v1/workspaces/{workspace_id}/agent-installations/{binding_id}/rollback",
    }
    assert paths == expected


def test_openapi_has_no_physical_locators_or_secrets() -> None:
    spec = _browser_app().openapi()
    serialized = str(spec)
    for forbidden in (
        "omnibase_meta",
        "postgresql",
        "schema_name",
        "tenant_schema",
        "password",
        "api_key",
        "pg_",
        "dsn",
        "credential",
        "workload_token",
    ):
        assert forbidden not in serialized.lower()


def test_openapi_request_bodies_have_no_internal_fields() -> None:
    spec = _browser_app().openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    for name in ("AgentInstallCreate", "AgentUpgradeRequest", "AgentRollbackRequest"):
        properties = set(schemas[name]["properties"])
        for forbidden in (
            "tenant_id",
            "installed_by",
            "schema_name",
            "created_at",
            "binding_state",
            "superseded_by",
        ):
            assert forbidden not in properties, f"{name} leaks {forbidden}"
