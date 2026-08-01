"""Browser API exposure and strict DTO contracts for P34.4."""

from __future__ import annotations

import json
from collections.abc import Mapping
from uuid import uuid4

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.routing import APIRoute
from pydantic import ValidationError

from omnibase.tenants.dependencies import require_tenant_admin
from omnibase.workspaces import router as router_module
from omnibase.workspaces.schemas import (
    LifecycleRequest,
    MembershipRead,
    MembershipWrite,
    RestoreRequest,
    RunCreate,
    RunRead,
    ScopeGrantCreate,
    ScopeGrantRead,
    SnapshotCreate,
    SnapshotRead,
    TemplateCreate,
    TemplateRead,
    WorkspaceCreate,
    WorkspaceRead,
)


def _browser_app() -> FastAPI:
    app = FastAPI()
    api = APIRouter(prefix="/api/v1")
    api.include_router(router_module.template_router)
    api.include_router(router_module.router)
    app.include_router(api)
    return app


def _property_names(value: object) -> set[str]:
    names: set[str] = set()
    if isinstance(value, Mapping):
        properties = value.get("properties")
        if isinstance(properties, Mapping):
            names.update(str(key) for key in properties)
        for nested in value.values():
            names.update(_property_names(nested))
    elif isinstance(value, list):
        for nested in value:
            names.update(_property_names(nested))
    return names


def test_public_workspace_dtos_exclude_tenant_secret_provider_and_fencing_state() -> None:
    schemas = (
        WorkspaceCreate,
        WorkspaceRead,
        MembershipWrite,
        MembershipRead,
        ScopeGrantCreate,
        ScopeGrantRead,
        LifecycleRequest,
        RunCreate,
        RunRead,
        SnapshotCreate,
        SnapshotRead,
        RestoreRequest,
        TemplateCreate,
        TemplateRead,
    )
    forbidden = {
        "tenant_id",
        "schema_name",
        "secret",
        "credential",
        "authorization",
        "provider_handle",
        "physical_locator",
        "fencing_token",
        "node_id",
        "lease_id",
        "runtime_instance_id",
        "workload_identity_digest",
    }

    for schema in schemas:
        assert forbidden.isdisjoint(_property_names(schema.model_json_schema())), schema.__name__


@pytest.mark.parametrize(
    "injected",
    [
        {"tenant_id": str(uuid4())},
        {"provider_handle": "opaque"},
        {"fencing_token": 99},
        {"secret": "placeholder"},
    ],
)
def test_strict_browser_request_dtos_reject_internal_fields(
    injected: dict[str, object],
) -> None:
    payload: dict[str, object] = {
        "display_name": "Workspace",
        "template_id": str(uuid4()),
        **injected,
    }

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        WorkspaceCreate.model_validate(payload)


def test_browser_openapi_mounts_workspace_governance_but_no_node_or_lease_control_plane() -> None:
    schema = _browser_app().openapi()
    paths = set(schema["paths"])

    assert "/api/v1/workspaces" in paths
    assert "/api/v1/workspace-templates" in paths
    assert "post" in schema["paths"]["/api/v1/workspace-templates"]
    assert "/api/v1/workspaces/{workspace_id}/runs" in paths
    assert "/api/v1/workspaces/{workspace_id}/snapshots" in paths
    for path in paths:
        lowered = path.lower()
        assert "/nodes" not in lowered
        assert "/peers" not in lowered
        assert "/leases" not in lowered
        assert "/authorities" not in lowered
        assert "/service-advertisements" not in lowered


def test_template_registration_requires_live_tenant_admin_dependency() -> None:
    route = next(
        route
        for route in _browser_app().routes
        if isinstance(route, APIRoute)
        and route.path == "/api/v1/workspace-templates"
        and "POST" in route.methods
    )

    assert require_tenant_admin in {dependency.call for dependency in route.dependant.dependencies}


def test_browser_openapi_schema_never_exposes_internal_node_lease_or_fencing_fields() -> None:
    openapi = _browser_app().openapi()
    forbidden = {
        "tenant_id",
        "schema_name",
        "provider_handle",
        "physical_locator",
        "fencing_token",
        "node_id",
        "lease_id",
        "runtime_instance_id",
        "workload_identity_digest",
    }

    assert forbidden.isdisjoint(_property_names(openapi))
    serialized = json.dumps(openapi, sort_keys=True).lower()
    assert "peer_overlay_provider" not in serialized
    assert "docker.sock" not in serialized
