"""Public-contract tests for the disabled-by-default P34.3 write router."""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from omnibase.capabilities.models import CapabilityGrant
from omnibase.controlled_data import router as router_module
from omnibase.controlled_data.crud import canonical_request_hash
from omnibase.controlled_data.crud_contracts import UpdateMutationRequest
from omnibase.controlled_data.executor import ControlledCrudResult
from omnibase.controlled_data.models import AuthorizationContext
from omnibase.controlled_data.router import (
    ControlledCrudComponents,
    ControlledWriteRouteError,
    _append_preflight_failure_audit,
    _operation_identity,
    get_controlled_crud_components,
)
from omnibase.controlled_data.schemas import ControlledWriteRequest
from omnibase.tenants.dependencies import get_current_principal

TENANT_ID = "10000000-0000-0000-0000-000000000001"
ACTOR_ID = "20000000-0000-0000-0000-000000000001"
RESOURCE_ID = "30000000-0000-0000-0000-000000000001"
COLUMN_ID = "40000000-0000-0000-0000-000000000001"
OPERATION_ID = UUID("50000000-0000-0000-0000-000000000001")


class AtomicExecutor:
    supports_atomic_lifecycle = True

    def __call__(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("service is mocked in API contract tests")


def _principal(*, is_admin: bool = True):
    return SimpleNamespace(
        tenant=SimpleNamespace(
            id=TENANT_ID,
            schema_name="tenant_10000000000000000000000000000001",
            is_active=True,
        ),
        user=SimpleNamespace(id=ACTOR_ID, is_active=True, is_tenant_admin=is_admin),
    )


def _payload(**mutation_changes: object) -> dict[str, object]:
    mutation: dict[str, object] = {
        "kind": "update",
        "resource_id": RESOURCE_ID,
        "resource_version": 1,
        "idempotency_key": "request-key-0001",
        "timeout_ms": 1000,
        "predicate": {
            "kind": "compare",
            "column_id": COLUMN_ID,
            "op": "eq",
            "value": "old",
        },
        "max_rows": 1,
        "values": {COLUMN_ID: "new"},
    }
    mutation.update(mutation_changes)
    return {"mutation": mutation}


def _app(*, configure_components: bool = True, override_principal: bool = True) -> FastAPI:
    app = FastAPI()
    app.include_router(router_module.router, prefix="/api/v1")

    @app.exception_handler(HTTPException)
    async def http_error(_request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    if override_principal:
        app.dependency_overrides[get_current_principal] = _principal
    if configure_components:
        app.dependency_overrides[get_controlled_crud_components] = lambda: (
            ControlledCrudComponents(session_factory=MagicMock(), executor=AtomicExecutor())
        )
    return app


def test_missing_bearer_token_is_401() -> None:
    response = TestClient(
        _app(configure_components=True, override_principal=False),
        raise_server_exceptions=False,
    ).post("/api/v1/controlled-data/rows/mutate", json=_payload())
    assert response.status_code == 401


def test_standalone_router_registers_authorization_fk_target_metadata() -> None:
    assert router_module._CAPABILITY_GRANT_TABLE is CapabilityGrant.__table__
    assert (
        f"{CapabilityGrant.__table__.schema}.{CapabilityGrant.__table__.name}"
        in AuthorizationContext.metadata.tables
    )


def test_production_app_registers_router_but_keeps_executor_uninstalled() -> None:
    from omnibase.main import create_app

    app = create_app()

    assert "/api/v1/controlled-data/rows/mutate" in app.openapi()["paths"]
    assert not hasattr(app.state, "controlled_crud_executor")


def test_default_missing_executor_is_fail_closed_before_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build = MagicMock()
    monkeypatch.setattr(router_module, "_build_command_in_transaction", build)
    response = TestClient(_app(configure_components=False), raise_server_exceptions=False).post(
        "/api/v1/controlled-data/rows/mutate", json=_payload()
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "controlled_write_unavailable"
    build.assert_not_called()


@pytest.mark.parametrize(
    "forbidden",
    [
        "tenant_id",
        "schema",
        "schema_name",
        "physical_table_name",
        "physical_column_name",
        "physical_locator",
        "locator",
        "authorization_context_id",
        "operation_id",
        "sql",
        "raw_sql",
    ],
)
def test_request_dto_rejects_all_server_owned_and_sql_fields(forbidden: str) -> None:
    payload = _payload()
    payload["mutation"][forbidden] = "attacker-controlled"  # type: ignore[index]
    with pytest.raises(ValueError):
        ControlledWriteRequest.model_validate(payload)


def test_request_openapi_contains_only_logical_mutation_fields() -> None:
    schema = json.dumps(ControlledWriteRequest.model_json_schema(), sort_keys=True)
    for forbidden in (
        '"tenant_id"',
        '"schema_name"',
        '"physical_table_name"',
        '"physical_column_name"',
        '"physical_locator"',
        '"authorization_context_id"',
        '"operation_id"',
        '"raw_sql"',
    ):
        assert forbidden not in schema


def test_route_openapi_declares_internal_and_timeout_error_envelopes() -> None:
    operation = _app().openapi()["paths"]["/api/v1/controlled-data/rows/mutate"]["post"]
    assert {"500", "503", "504"} <= set(operation["responses"])
    for code in ("500", "503", "504"):
        serialized = json.dumps(operation["responses"][code], sort_keys=True)
        assert "ControlledWriteErrorResponse" in serialized


def test_max_rows_over_contract_is_rejected_by_dto() -> None:
    response = TestClient(_app(), raise_server_exceptions=False).post(
        "/api/v1/controlled-data/rows/mutate",
        json=_payload(max_rows=101),
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("error", "expected_status", "expected_code"),
    [
        (
            ControlledWriteRouteError(
                "controlled_write_forbidden", "Controlled write is not permitted", 403
            ),
            403,
            "controlled_write_forbidden",
        ),
        (
            ControlledWriteRouteError("resource_not_found", "Resource not found", 404),
            404,
            "resource_not_found",
        ),
        (
            ControlledWriteRouteError(
                "controlled_write_version_conflict", "Resource version does not match", 409
            ),
            409,
            "controlled_write_version_conflict",
        ),
        (
            ControlledWriteRouteError(
                "CONTROLLED_CRUD_IDEMPOTENCY_CONFLICT",
                "Idempotency key conflicts with an existing request",
                409,
            ),
            409,
            "CONTROLLED_CRUD_IDEMPOTENCY_CONFLICT",
        ),
    ],
)
def test_preflight_denials_are_safe_and_audited(
    monkeypatch: pytest.MonkeyPatch,
    error: ControlledWriteRouteError,
    expected_status: int,
    expected_code: str,
) -> None:
    monkeypatch.setattr(
        router_module,
        "execute_controlled_crud_lifecycle_audited",
        MagicMock(side_effect=error),
    )
    audit = MagicMock()
    monkeypatch.setattr(router_module, "_append_preflight_failure_audit", audit)
    response = TestClient(_app(), raise_server_exceptions=False).post(
        "/api/v1/controlled-data/rows/mutate",
        json=_payload(),
        headers={"X-Request-Id": "api-test-request"},
    )
    assert response.status_code == expected_status
    assert response.json() == {
        "error": {
            "code": expected_code,
            "message": error.message,
            "request_id": "api-test-request",
        }
    }
    audit.assert_called_once()
    assert "physical" not in response.text.lower()
    assert "tenant_" not in response.text.lower()


def test_success_and_replay_propagate_request_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def execute(_factory, bootstrap, *, audit, executor):
        del executor
        captured["bootstrap"] = bootstrap
        captured["audit"] = audit
        return ControlledCrudResult(
            operation_id=OPERATION_ID,
            resource_id=UUID(RESOURCE_ID),
            resource_version=1,
            action="data.rows.update",
            affected_rows=1,
            request_hash="a" * 64,
            replayed=True,
        )

    monkeypatch.setattr(router_module, "execute_controlled_crud_lifecycle_audited", execute)
    response = TestClient(_app(), raise_server_exceptions=False).post(
        "/api/v1/controlled-data/rows/mutate",
        json=_payload(),
        headers={"X-Request-Id": "api-request-0001"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "operation_id": str(OPERATION_ID),
        "resource_id": RESOURCE_ID,
        "resource_version": 1,
        "action": "data.rows.update",
        "affected_rows": 1,
        "replayed": True,
        "request_id": "api-request-0001",
    }
    assert callable(captured["bootstrap"])
    assert captured["audit"].request_id == "api-request-0001"  # type: ignore[union-attr]


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class _SequenceSession:
    def __init__(self, values: list[object]) -> None:
        self.values = iter(values)
        self.statements: list[object] = []

    def execute(self, statement: object) -> _ScalarResult:
        self.statements.append(statement)
        return _ScalarResult(next(self.values))


def _mutation(*, key: str = "request-key-0001", value: str = "new"):
    return UpdateMutationRequest.model_validate(
        _payload(idempotency_key=key)["mutation"] | {"values": {COLUMN_ID: value}}
    )


def test_same_key_without_idempotency_uses_one_deterministic_operation() -> None:
    mutation = _mutation()
    request_hash = canonical_request_hash(mutation)
    operation = SimpleNamespace(
        id="bd9d540d-43fa-5f5d-903d-8454aff3ec7d",
        actor_type="user",
        actor_id=ACTOR_ID,
        resource_id=RESOURCE_ID,
        resource_version=1,
        request_hash=request_hash,
        kind="data.rows.update",
        state="queued",
        version=1,
    )
    first = _SequenceSession([None, None, operation])
    second = _SequenceSession([None, None, operation])
    first_id, first_version = _operation_identity(
        first,
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        action="data.rows.update",
        mutation=mutation,
        request_hash=request_hash,
        now=datetime.now(UTC),
    )
    second_id, second_version = _operation_identity(
        second,
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        action="data.rows.update",
        mutation=mutation,
        request_hash=request_hash,
        now=datetime.now(UTC),
    )
    assert first_id == second_id
    assert first_version == second_version == 1
    assert "ON CONFLICT" in str(first.statements[1]).upper()


def test_completed_replay_derives_original_expected_operation_version() -> None:
    mutation = _mutation()
    request_hash = canonical_request_hash(mutation)
    record = SimpleNamespace(
        request_hash=request_hash,
        operation_id=str(OPERATION_ID),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    operation = SimpleNamespace(
        id=str(OPERATION_ID),
        request_hash=request_hash,
        kind="data.rows.update",
        state="succeeded",
        version=5,
    )
    operation_id, expected_version = _operation_identity(
        _SequenceSession([record, operation]),
        tenant_id=TENANT_ID,
        actor_id=ACTOR_ID,
        action="data.rows.update",
        mutation=mutation,
        request_hash=request_hash,
        now=datetime.now(UTC),
    )
    assert operation_id == OPERATION_ID
    assert expected_version == 3


def test_idempotency_payload_drift_is_rejected_before_operation_lookup() -> None:
    mutation = _mutation()
    record = SimpleNamespace(
        request_hash="f" * 64,
        operation_id=str(OPERATION_ID),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    session = _SequenceSession([record])
    with pytest.raises(ControlledWriteRouteError, match="CONTROLLED_CRUD_IDEMPOTENCY_CONFLICT"):
        _operation_identity(
            session,
            tenant_id=TENANT_ID,
            actor_id=ACTOR_ID,
            action="data.rows.update",
            mutation=mutation,
            request_hash=canonical_request_hash(mutation),
            now=datetime.now(UTC),
        )
    assert len(session.statements) == 1


def test_preflight_audit_contains_code_only_details() -> None:
    added: list[object] = []

    class Session:
        @contextmanager
        def begin(self):
            yield

        def add(self, value: object) -> None:
            added.append(value)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    _append_preflight_failure_audit(
        Session,
        principal=_principal(),
        mutation=_mutation(),
        request_id="audit-request-1",
        failure=ControlledWriteRouteError(
            "controlled_write_forbidden", "Controlled write is not permitted", 403
        ),
    )
    event = added[0]
    assert event.details == {"reason_code": "controlled_write_forbidden", "retryable": False}
    serialized = json.dumps(event.details)
    assert "schema" not in serialized
    assert "physical" not in serialized
    assert "sql" not in serialized


def test_preflight_conflict_audit_is_classified_as_error() -> None:
    added: list[object] = []

    class Session:
        @contextmanager
        def begin(self):
            yield

        def add(self, value: object) -> None:
            added.append(value)

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    _append_preflight_failure_audit(
        Session,
        principal=_principal(),
        mutation=_mutation(),
        request_id="audit-conflict-1",
        failure=ControlledWriteRouteError(
            "controlled_write_version_conflict", "Resource version does not match", 409
        ),
    )
    assert added[0].decision == "error"
