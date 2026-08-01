"""Read-only HTTP and tenant-boundary contracts for P34.1."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from omnibase.control_plane import router as router_module
from omnibase.control_plane.schemas import (
    ApprovalRead,
    AuditEventRead,
    OperationRead,
    ResourceRead,
)
from omnibase.control_plane.service import (
    ApprovalNotFound,
    OperationNotFound,
    ResourceNotFound,
)
from omnibase.tenants.dependencies import (
    TenantContext,
    get_current_tenant,
    get_tenant_db,
)

_RESOURCE_ID = "10000000-0000-0000-0000-000000000001"
_OPERATION_ID = "20000000-0000-0000-0000-000000000001"
_APPROVAL_ID = "30000000-0000-0000-0000-000000000001"


def _context(*, is_admin: bool) -> TenantContext:
    tenant = SimpleNamespace(
        id="40000000-0000-0000-0000-000000000001",
        schema_name="tenant_deadbeef",
    )
    user = SimpleNamespace(
        id="50000000-0000-0000-0000-000000000001",
        is_active=True,
        is_tenant_admin=is_admin,
    )
    return TenantContext(tenant=tenant, user=user)


def _client(*, is_admin: bool = False) -> TestClient:
    app = FastAPI()
    api = APIRouter(prefix="/api/v1")
    api.include_router(router_module.router)
    app.include_router(api)
    app.dependency_overrides[get_current_tenant] = lambda: _context(is_admin=is_admin)
    database_session = MagicMock()
    app.dependency_overrides[get_tenant_db] = lambda: database_session
    return TestClient(app, raise_server_exceptions=False)


def _public_resource() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=_RESOURCE_ID,
        kind="document",
        owner_type="user",
        owner_id=None,
        parent_id=None,
        display_name="Document",
        state="active",
        version=1,
        policy_class="canonical_readonly",
        resource_metadata={"internal": "must not be serialized"},
        created_at=now,
        updated_at=now,
    )


def _public_operation() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=_OPERATION_ID,
        workspace_id=None,
        run_id=None,
        actor_type="user",
        actor_id="50000000-0000-0000-0000-000000000001",
        resource_id=None,
        resource_version=None,
        approval_id=None,
        kind="resource.read",
        state="queued",
        risk_level="R0",
        progress=0,
        attempt_count=0,
        version=1,
        deadline_at=now + timedelta(minutes=5),
        started_at=None,
        completed_at=None,
        result_ref={"internal": True},
        error_code=None,
        error_detail="internal detail",
        operation_metadata={"internal": True},
        created_at=now,
        updated_at=now,
    )


def _public_approval() -> SimpleNamespace:
    now = datetime.now(UTC)
    return SimpleNamespace(
        id=_APPROVAL_ID,
        requester_type="user",
        requester_id="50000000-0000-0000-0000-000000000001",
        workspace_id=None,
        run_id=None,
        resource_id=None,
        operation_id=_OPERATION_ID,
        grant_id=None,
        action="resource.read",
        risk_level="R2",
        required_approver_role="tenant_admin",
        state="pending",
        resource_version=None,
        version=1,
        decided_by_actor_type=None,
        decided_by_actor_id=None,
        decision_reason="internal detail",
        expires_at=now + timedelta(minutes=5),
        decided_at=None,
        consumed_at=None,
        approval_metadata={"internal": True},
        created_at=now,
        updated_at=now,
    )


def test_control_plane_surface_contains_get_routes_only() -> None:
    app = FastAPI()
    api = APIRouter(prefix="/api/v1")
    api.include_router(router_module.router)
    app.include_router(api)

    exposed = {
        method
        for route in app.routes
        if route.path.startswith("/api/v1/control-plane")
        for method in route.methods
    }
    assert exposed == {"GET"}
    assert not ({"POST", "PUT", "PATCH", "DELETE"} & exposed)


def test_openapi_control_plane_schemas_never_expose_internal_or_sensitive_fields() -> None:
    app = FastAPI()
    api = APIRouter(prefix="/api/v1")
    api.include_router(router_module.router)
    app.include_router(api)

    serialized = json.dumps(app.openapi(), sort_keys=True)
    for forbidden in (
        "physical_locator",
        "schema_name",
        "minio_key",
        "metadata",
        "result_ref",
        "error_detail",
        "decision_reason",
        "details",
    ):
        assert forbidden not in serialized


@pytest.mark.parametrize(
    ("schema", "forbidden"),
    [
        (ResourceRead, {"metadata", "physical_locator"}),
        (OperationRead, {"metadata", "result_ref", "error_detail"}),
        (ApprovalRead, {"metadata", "decision_reason"}),
        (AuditEventRead, {"details"}),
    ],
)
def test_public_read_dtos_are_minimal(
    schema: object,
    forbidden: set[str],
) -> None:
    properties = schema.model_json_schema()["properties"]  # type: ignore[attr-defined]
    assert forbidden.isdisjoint(properties)


@pytest.mark.parametrize(
    ("path", "attribute", "exception"),
    [
        (
            f"/api/v1/control-plane/resources/{_RESOURCE_ID}",
            "get_resource",
            ResourceNotFound,
        ),
        (
            f"/api/v1/control-plane/operations/{_OPERATION_ID}",
            "get_operation",
            OperationNotFound,
        ),
        (
            f"/api/v1/control-plane/approvals/{_APPROVAL_ID}",
            "get_approval",
            ApprovalNotFound,
        ),
    ],
)
def test_cross_tenant_ids_return_one_uniform_404_envelope(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    attribute: str,
    exception: type[Exception],
) -> None:
    def hidden_record(*args: object, **kwargs: object) -> None:
        raise exception("internal tenant-specific detail")

    monkeypatch.setattr(router_module, attribute, hidden_record)
    response = _client(is_admin=True).get(path)

    assert response.status_code == 404
    assert response.json() == {
        "detail": {"error": {"code": "not_found", "message": "Record not found"}}
    }
    assert "tenant" not in response.text.lower()


def test_resource_list_uses_authenticated_tenant_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received: dict[str, object] = {}

    def capture_list(*args: object, **kwargs: object) -> tuple[list[object], int]:
        received.update(kwargs)
        return [], 0

    monkeypatch.setattr(router_module, "list_resources", capture_list)
    response = _client(is_admin=True).get("/api/v1/control-plane/resources")

    assert response.status_code == 200
    assert response.json() == {"items": [], "total": 0}
    assert received["tenant_id"] == "40000000-0000-0000-0000-000000000001"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/control-plane/resources?limit=101",
        "/api/v1/control-plane/operations?limit=101",
        "/api/v1/control-plane/approvals?limit=101",
        "/api/v1/control-plane/audit/events?limit=101",
    ],
)
def test_public_list_page_size_is_capped_at_100(path: str) -> None:
    response = _client(is_admin=True).get(path)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("path", "attribute"),
    [
        ("/api/v1/control-plane/resources", "list_resources"),
        (f"/api/v1/control-plane/resources/{_RESOURCE_ID}", "get_resource"),
        ("/api/v1/control-plane/operations", "list_operations"),
        (f"/api/v1/control-plane/operations/{_OPERATION_ID}", "get_operation"),
        ("/api/v1/control-plane/approvals", "list_approvals"),
        (f"/api/v1/control-plane/approvals/{_APPROVAL_ID}", "get_approval"),
        ("/api/v1/control-plane/audit/events", "list_audit_events"),
    ],
)
def test_all_control_plane_gets_require_tenant_admin(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    attribute: str,
) -> None:
    target = MagicMock()
    monkeypatch.setattr(router_module, attribute, target)

    denied = _client(is_admin=False).get(path)
    assert denied.status_code == 403
    target.assert_not_called()


@pytest.mark.parametrize(
    ("path", "attribute"),
    [
        ("/api/v1/control-plane/resources", "list_resources"),
        ("/api/v1/control-plane/operations", "list_operations"),
        ("/api/v1/control-plane/approvals", "list_approvals"),
        ("/api/v1/control-plane/audit/events", "list_audit_events"),
    ],
)
def test_all_control_plane_list_gets_allow_tenant_admin(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    attribute: str,
) -> None:
    target = MagicMock(return_value=([], 0))
    monkeypatch.setattr(router_module, attribute, target)

    allowed = _client(is_admin=True).get(path)
    assert allowed.status_code == 200
    assert allowed.json() == {"items": [], "total": 0}
    assert target.call_args.kwargs["tenant_id"] == ("40000000-0000-0000-0000-000000000001")


@pytest.mark.parametrize(
    ("path", "attribute", "record_factory"),
    [
        (
            f"/api/v1/control-plane/resources/{_RESOURCE_ID}",
            "get_resource",
            _public_resource,
        ),
        (
            f"/api/v1/control-plane/operations/{_OPERATION_ID}",
            "get_operation",
            _public_operation,
        ),
        (
            f"/api/v1/control-plane/approvals/{_APPROVAL_ID}",
            "get_approval",
            _public_approval,
        ),
    ],
)
def test_all_control_plane_detail_gets_allow_tenant_admin(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    attribute: str,
    record_factory: object,
) -> None:
    target = MagicMock(return_value=record_factory())  # type: ignore[operator]
    monkeypatch.setattr(router_module, attribute, target)

    allowed = _client(is_admin=True).get(path)

    assert allowed.status_code == 200
    assert target.call_args.kwargs["tenant_id"] == ("40000000-0000-0000-0000-000000000001")
