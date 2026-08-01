"""Workload auth, IDOR, limits, audit, and public-surface tests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from omnibase.capability_gateway.app import create_gateway_app
from omnibase.capability_gateway.contracts import (
    CapabilityConstraints,
    ColumnRead,
    DataRowsResult,
    ResourceDescriptor,
    TrustedWorkloadContext,
    VerifiedCapability,
)
from omnibase.capability_gateway.router import get_gateway_db
from omnibase.capability_gateway.security import CapabilityVerificationError
from omnibase.capability_gateway.service import GatewayComponents

TENANT = "10000000-0000-0000-0000-000000000001"
WORKSPACE = "20000000-0000-0000-0000-000000000001"
RESOURCE = "30000000-0000-0000-0000-000000000001"
COLUMN = "40000000-0000-0000-0000-000000000001"


class FakeVerifier:
    def __init__(self, capability: VerifiedCapability) -> None:
        self.capability = capability
        self.consumed: list[tuple[int, int, int]] = []

    def verify(self, session, credential, *, action: str, resource_id: str):
        del session, resource_id
        if credential.authorization == "bad":
            raise CapabilityVerificationError
        assert credential.trusted_context.certificate_thumbprint == "trusted-thumbprint"
        assert action in self.capability.actions
        return self.capability

    def consume_budget(
        self, session, capability, *, calls: int, bytes_in: int, bytes_out_reserved: int
    ):
        del session
        assert capability is self.capability
        self.consumed.append((calls, bytes_in, bytes_out_reserved))


class FakeResolver:
    def __init__(self, descriptor: ResourceDescriptor) -> None:
        self.descriptor = descriptor

    def resolve(self, session, *, capability, resource_id):
        del session, capability, resource_id
        return self.descriptor


class FakeDataAdapter:
    def read_schema(self, session, *, capability, resource):
        del session, capability, resource
        return [ColumnRead(id=UUID(COLUMN), display_name="Name", type="text", nullable=False)]

    def read_rows(self, session, *, capability, resource, query):
        del session, capability, resource, query
        return DataRowsResult(
            rows=[{COLUMN: "safe"}], next_cursor=None, bytes_out=20, truncated=False
        )


class OversizedSchemaAdapter(FakeDataAdapter):
    def read_schema(self, session, *, capability, resource):
        del session, capability, resource
        return [
            ColumnRead(
                id=UUID(int=index + 1),
                display_name="x" * 200,
                type="very_long_server_controlled_type_name",
                nullable=False,
            )
            for index in range(500)
        ]


class FakeRagAdapter:
    def search(self, *args, **kwargs):
        raise AssertionError("not used")

    def read_citations(self, *args, **kwargs):
        raise AssertionError("not used")


@dataclass
class FakeAudit:
    records: list[object] = field(default_factory=list)

    def append(self, session, *, capability, record):
        del session, capability
        self.records.append(record)


class StaticAttestor:
    def attest(self, scope, opaque_identity):
        del scope
        return TrustedWorkloadContext(
            opaque_identity=opaque_identity,
            tenant_id=TENANT,
            workspace_id=WORKSPACE,
            runtime_instance_id="50000000-0000-0000-0000-000000000001",
            certificate_thumbprint="trusted-thumbprint",
        )


def _capability(**changes) -> VerifiedCapability:
    values = {
        "tenant_id": TENANT,
        "workspace_id": WORKSPACE,
        "runtime_instance_id": "50000000-0000-0000-0000-000000000001",
        "actor_user_id": None,
        "grant_id": "60000000-0000-0000-0000-000000000001",
        "token_jti": "70000000-0000-0000-0000-000000000001",
        "actions": frozenset({"data.schema.read", "data.rows.read"}),
        "resource_ids": frozenset({RESOURCE}),
        "constraints": CapabilityConstraints(max_rows=50, max_bytes=100_000, max_timeout_ms=1000),
    }
    values.update(changes)
    return VerifiedCapability(**values)


def _client(
    *,
    descriptor: ResourceDescriptor | None = None,
    data_adapter=None,
    trusted_attestor: bool = True,
):
    descriptor = descriptor or ResourceDescriptor(
        id=RESOURCE,
        tenant_id=TENANT,
        kind="data_table",
        owner_type="workspace",
        owner_id=WORKSPACE,
        parent_id=WORKSPACE,
        state="active",
        version=1,
        policy_class="workspace_private",
    )
    verifier = FakeVerifier(_capability())
    audit = FakeAudit()
    app = create_gateway_app(
        GatewayComponents(
            verifier=verifier,
            resolver=FakeResolver(descriptor),
            data_adapter=data_adapter or FakeDataAdapter(),
            rag_adapter=FakeRagAdapter(),
            audit_sink=audit,
        ),
        workload_attestor=StaticAttestor() if trusted_attestor else None,
    )
    session = MagicMock()
    app.dependency_overrides[get_gateway_db] = lambda: session
    return TestClient(app, raise_server_exceptions=False), verifier, audit, session


HEADERS = {
    "Authorization": "Capability good",
    "X-Omnibase-Workload-Identity": "spiffe://omnibase/runtime/one",
}


def test_gateway_rejects_user_bearer_jwt_and_has_no_cors() -> None:
    client, _, _, _ = _client()
    response = client.post(
        "/gateway/v1/data/schema/read",
        json={"resource_id": RESOURCE},
        headers={"Authorization": "Bearer user.jwt", "Origin": "http://127.0.0.1:3001"},
    )
    assert response.status_code == 401
    assert "access-control-allow-origin" not in response.headers


def test_gateway_schema_read_consumes_budget_and_audits() -> None:
    client, verifier, audit, _ = _client()
    response = client.post(
        "/gateway/v1/data/schema/read", json={"resource_id": RESOURCE}, headers=HEADERS
    )
    assert response.status_code == 200
    assert response.json()["columns"][0]["id"] == COLUMN
    assert verifier.consumed
    assert verifier.consumed[0][0] == 1
    assert audit.records[0].decision == "allowed"


def test_default_missing_trusted_attestor_fails_closed() -> None:
    client, verifier, _, _ = _client(trusted_attestor=False)
    response = client.post(
        "/gateway/v1/data/schema/read", json={"resource_id": RESOURCE}, headers=HEADERS
    )
    assert response.status_code == 401
    assert verifier.consumed == []


def test_client_supplied_certificate_thumbprint_is_ignored() -> None:
    client, _, _, _ = _client()
    headers = {**HEADERS, "X-Omnibase-Workload-Cert-Sha256": "attacker-controlled"}
    response = client.post(
        "/gateway/v1/data/schema/read", json={"resource_id": RESOURCE}, headers=headers
    )
    assert response.status_code == 200


def test_oversized_schema_response_is_rejected_with_413() -> None:
    client, verifier, audit, _ = _client(data_adapter=OversizedSchemaAdapter())
    response = client.post(
        "/gateway/v1/data/schema/read", json={"resource_id": RESOURCE}, headers=HEADERS
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "result_too_large"
    assert verifier.consumed
    assert audit.records[0].decision == "denied"


def test_audit_and_response_share_the_middleware_request_id() -> None:
    client, _, audit, _ = _client()
    response = client.post(
        "/gateway/v1/data/schema/read", json={"resource_id": RESOURCE}, headers=HEADERS
    )
    assert response.status_code == 200
    assert audit.records[0].request_id == response.headers["x-request-id"]


@pytest.mark.parametrize(
    "descriptor",
    [
        ResourceDescriptor(
            RESOURCE,
            "dead0000-0000-0000-0000-000000000000",
            "data_table",
            "workspace",
            WORKSPACE,
            None,
            "active",
            1,
            "workspace_private",
        ),
        ResourceDescriptor(
            RESOURCE,
            TENANT,
            "data_table",
            "workspace",
            "dead0000-0000-0000-0000-000000000000",
            None,
            "active",
            1,
            "workspace_private",
        ),
    ],
)
def test_tenant_and_workspace_idor_return_same_safe_404(descriptor) -> None:
    client, verifier, audit, session = _client(descriptor=descriptor)
    response = client.post(
        "/gateway/v1/data/schema/read", json={"resource_id": RESOURCE}, headers=HEADERS
    )
    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "resource_not_found", "message": "Resource not found"}
    }
    assert audit.records[0].decision == "denied"
    assert verifier.consumed
    assert session.commit.call_count == 2
    assert session.rollback.call_count >= 1


def test_scope_constraints_reject_rows_timeout_and_bytes() -> None:
    client, _, audit, _ = _client()
    response = client.post(
        "/gateway/v1/data/rows/read",
        headers=HEADERS,
        json={
            "resource_id": RESOURCE,
            "query": {"columns": [COLUMN], "limit": 51, "timeout_ms": 1001, "max_bytes": 100001},
        },
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "capability_constraint_denied"
    assert audit.records[0].reason_code == "capability_constraint_denied"


def test_scope_cannot_be_self_reported_in_body() -> None:
    client, _, _, _ = _client()
    response = client.post(
        "/gateway/v1/data/schema/read",
        headers=HEADERS,
        json={"resource_id": RESOURCE, "tenant_id": TENANT, "schema_name": "tenant_deadbeef"},
    )
    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "validation_error", "message": "Request validation failed"}
    }


@pytest.mark.parametrize("invalid_limit", ["5", True, 1.5])
def test_gateway_rejects_type_confused_integer_fields(invalid_limit) -> None:
    client, _, _, _ = _client()
    response = client.post(
        "/gateway/v1/data/rows/read",
        headers=HEADERS,
        json={"resource_id": RESOURCE, "query": {"columns": [COLUMN], "limit": invalid_limit}},
    )
    assert response.status_code == 422


def test_openapi_exposes_only_closed_gateway_contract() -> None:
    client, _, _, _ = _client()
    schema = client.app.openapi()
    paths = set(schema["paths"])
    assert paths == {
        "/gateway/v1/data/schema/read",
        "/gateway/v1/data/rows/read",
        "/gateway/v1/rag/search",
        "/gateway/v1/rag/citations/read",
    }
    serialized = json.dumps(schema, sort_keys=True).casefold()
    assert "x-omnibase-workload-cert-sha256" not in serialized
    for forbidden in (
        "physical_locator",
        "schema_name",
        "table_name",
        "minio_key",
        "provider_handle",
        "sql_fragment",
    ):
        assert forbidden not in serialized
    for path in schema["paths"].values():
        operation = path["post"]
        assert "X-Request-Id" in operation["responses"]["200"]["headers"]
        for status_code in ("401", "403", "404", "413", "422", "429", "503"):
            response_schema = operation["responses"][status_code]["content"]["application/json"][
                "schema"
            ]
            assert response_schema == {"$ref": "#/components/schemas/ErrorEnvelope"}
            assert "X-Request-Id" in operation["responses"][status_code]["headers"]
