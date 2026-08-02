"""P34.2/P34.6 Gateway breaking-change and secret-field regression checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from omnibase.capability_gateway.app import create_gateway_app

SNAPSHOT = Path(__file__).with_name("p34-2-openapi.snapshot.json")
ERROR_STATUSES = {"401", "403", "404", "413", "422", "429"}
FORBIDDEN_PUBLIC_FIELDS = {
    "database_url",
    "grant_id",
    "jti",
    "minio_key",
    "object_key",
    "path",
    "physical_locator",
    "provider_handle",
    "raw_sql",
    "schema_name",
    "sql",
    "table_name",
    "tenant_id",
    "token",
    "workspace_id",
}


def _ref_name(value: dict[str, Any]) -> str:
    return value["$ref"].rsplit("/", 1)[-1]


def _compact_contract(schema: dict[str, Any]) -> dict[str, Any]:
    operations: dict[str, Any] = {}
    for path, path_item in schema["paths"].items():
        assert set(path_item) == {"post"}, f"Gateway path {path} must remain POST-only"
        operation = path_item["post"]
        operations[path] = {
            "operation_id": operation["operationId"],
            "request_schema": _ref_name(
                operation["requestBody"]["content"]["application/json"]["schema"]
            ),
            "response_schema": _ref_name(
                operation["responses"]["200"]["content"]["application/json"]["schema"]
            ),
        }
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    models = {
        name: sorted(schema["components"]["schemas"][name].get("properties", {}))
        for name in expected["public_models"]
    }
    return {"contract_version": 1, "operations": operations, "public_models": models}


def test_gateway_openapi_matches_frozen_snapshot() -> None:
    schema = create_gateway_app().openapi()
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert _compact_contract(schema) == expected


def test_gateway_openapi_declares_one_safe_error_envelope_and_request_id() -> None:
    schema = create_gateway_app().openapi()
    for path_item in schema["paths"].values():
        operation = path_item["post"]
        responses = operation["responses"]
        assert responses.keys() >= ERROR_STATUSES
        for status in ERROR_STATUSES:
            model = _ref_name(
                responses[status]["content"]["application/json"]["schema"]
            )
            assert model == "ErrorEnvelope"
            headers = responses[status].get("headers", {})
            assert "X-Request-Id" in headers
        assert "X-Request-Id" in responses["200"].get("headers", {})


def test_gateway_openapi_never_exposes_locator_scope_or_credential_fields() -> None:
    schema = create_gateway_app().openapi()
    for model_name, model in schema["components"]["schemas"].items():
        properties = set(model.get("properties", {}))
        leaked = properties & FORBIDDEN_PUBLIC_FIELDS
        assert not leaked, f"{model_name} exposes forbidden fields: {sorted(leaked)}"
        if model_name not in {"ErrorBody", "ErrorEnvelope"}:
            assert model.get("additionalProperties") is False or model_name in {
                "HTTPValidationError",
                "ValidationError",
            }


def test_gateway_request_models_keep_query_and_result_budgets_bounded() -> None:
    schemas = create_gateway_app().openapi()["components"]["schemas"]
    query = schemas["ReadQuery"]["properties"]
    assert query["limit"]["maximum"] == 100
    assert query["cursor"]["anyOf"][0]["maxLength"] == 512
    assert query["max_bytes"]["maximum"] == 1_048_576
    assert query["timeout_ms"]["maximum"] == 5000
    search = schemas["RagSearchRequest"]["properties"]
    assert search["query"]["maxLength"] == 2000
    assert search["top_k"]["maximum"] == 20
