from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import BytesIO

import pytest

from omnibase_sdk import (
    DerivedChunkWrite,
    GatewayError,
    OmniBaseClient,
    RowsQuery,
    WorkloadCredential,
)
from omnibase_sdk.transport import (
    HttpTransport,
    StaticCredentialProvider,
    TransportResponse,
    _NoRedirect,
    _read_json_bounded,
)

RESOURCE_ID = "11111111-1111-4111-8111-111111111111"
COLUMN_ID = "22222222-2222-4222-8222-222222222222"
OPERATION_ID = "33333333-3333-4333-8333-333333333333"


class FakeTransport:
    def __init__(self, response: TransportResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, str, dict[str, object]]] = []

    def request(self, method: str, path: str, body: dict[str, object]) -> TransportResponse:
        self.calls.append((method, path, body))
        return self.response


def test_schema_read_uses_frozen_gateway_path_and_logical_id_only() -> None:
    transport = FakeTransport(
        TransportResponse(
            200,
            {"x-request-id": "req-1"},
            {
                "resource_id": RESOURCE_ID,
                "resource_version": 3,
                "columns": [
                    {"id": COLUMN_ID, "display_name": "Title", "type": "text", "nullable": False}
                ],
            },
        )
    )
    result = OmniBaseClient(transport).read_schema(RESOURCE_ID)
    assert result.resource_version == 3
    assert transport.calls == [
        ("POST", "/gateway/v1/data/schema/read", {"resource_id": RESOURCE_ID})
    ]


def test_rows_query_keeps_cursor_opaque_and_rejects_sql_escape_hatches() -> None:
    query = RowsQuery(columns=(COLUMN_ID,), cursor="opaque.cursor/value==", limit=10)
    assert query.to_payload()["cursor"] == "opaque.cursor/value=="
    with pytest.raises(ValueError, match="raw_sql"):
        RowsQuery(columns=(COLUMN_ID,), filter={"raw_sql": "select 1"}).to_payload()


def test_gateway_error_preserves_safe_request_id_without_details() -> None:
    transport = FakeTransport(
        TransportResponse(
            403,
            {"x-request-id": "req-denied"},
            {"error": {"code": "capability_scope_denied", "message": "Capability denied"}},
        )
    )
    with pytest.raises(GatewayError) as captured:
        OmniBaseClient(transport).read_schema(RESOURCE_ID)
    assert captured.value.code == "capability_scope_denied"
    assert captured.value.request_id == "req-denied"


def test_error_envelope_rejects_extra_debug_or_secret_fields() -> None:
    transport = FakeTransport(
        TransportResponse(
            401,
            {"x-request-id": "req-2"},
            {
                "error": {
                    "code": "invalid_capability",
                    "message": "Invalid capability",
                    "token": "must-not-surface",
                }
            },
        )
    )
    with pytest.raises(GatewayError) as captured:
        OmniBaseClient(transport).read_schema(RESOURCE_ID)
    assert captured.value.code == "invalid_gateway_response"
    assert "must-not-surface" not in str(captured.value)


def test_success_dto_rejects_physical_locator_regression() -> None:
    transport = FakeTransport(
        TransportResponse(
            200,
            {},
            {
                "resource_id": RESOURCE_ID,
                "resource_version": 1,
                "columns": [],
                "physical_locator": "tenant_schema.secret_table",
            },
        )
    )
    with pytest.raises(ValueError, match="physical_locator"):
        OmniBaseClient(transport).read_schema(RESOURCE_ID)


def test_success_dto_rejects_coerced_boolean_and_integer_values() -> None:
    transport = FakeTransport(
        TransportResponse(
            200,
            {},
            {
                "resource_id": RESOURCE_ID,
                "resource_version": True,
                "columns": [
                    {"id": COLUMN_ID, "display_name": "Title", "type": "text", "nullable": "false"}
                ],
            },
        )
    )
    with pytest.raises(ValueError):
        OmniBaseClient(transport).read_schema(RESOURCE_ID)


def test_workload_credential_is_capability_scheme_and_secret_safe_repr() -> None:
    credential = WorkloadCredential(
        token="secret-capability",
        workload_identity="runtime-1",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert credential.authorization_value() == "Capability secret-capability"
    assert "secret-capability" not in repr(credential)


def test_http_transport_rejects_redirect_targets_paths_and_invalid_timeout() -> None:
    credential = WorkloadCredential(
        token="test-capability",
        workload_identity="runtime-1",
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )
    provider = StaticCredentialProvider(credential)
    with pytest.raises(ValueError, match="origin"):
        HttpTransport("https://gateway.internal/base", provider)
    with pytest.raises(ValueError, match="timeout_seconds"):
        HttpTransport("https://gateway.internal", provider, timeout_seconds=float("inf"))
    assert _NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil") is None


def test_response_body_limit_applies_before_json_decode() -> None:
    with pytest.raises(ValueError, match="byte limit"):
        _read_json_bounded(BytesIO(b'{"value":"too-large"}'), 8)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_python_sdk_rejects_non_finite_json_values(value: float) -> None:
    with pytest.raises(ValueError, match="NaN or Infinity"):
        RowsQuery(
            columns=(COLUMN_ID,),
            filter={"kind": "compare", "column_id": COLUMN_ID, "op": "eq", "value": value},
        ).to_payload()


@pytest.mark.parametrize("value", [True, "5", 1.5])
def test_python_sdk_rejects_type_confused_integer_options(value: object) -> None:
    with pytest.raises(ValueError):
        RowsQuery(columns=(COLUMN_ID,), limit=value).to_payload()  # type: ignore[arg-type]


def test_artifact_and_derived_helpers_bind_content_and_use_logical_routes() -> None:
    class RoutedTransport:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        def request(self, method: str, path: str, body: dict[str, object]) -> TransportResponse:
            self.calls.append((method, path, body))
            if path.endswith("artifacts/write"):
                return TransportResponse(
                    200,
                    {},
                    {
                        "operation_id": OPERATION_ID,
                        "resource_id": RESOURCE_ID,
                        "resource_version": 1,
                        "media_type": "text/plain",
                        "size_bytes": 5,
                        "content_sha256": body["content_sha256"],
                        "replayed": False,
                        "request_id": "req-write",
                    },
                )
            if path.endswith("derived/create"):
                return TransportResponse(
                    200,
                    {},
                    {
                        "operation_id": OPERATION_ID,
                        "resource_id": RESOURCE_ID,
                        "resource_version": 1,
                        "chunk_count": 1,
                        "replayed": False,
                        "request_id": "req-derived",
                    },
                )
            raise AssertionError(path)

    transport = RoutedTransport()
    client = OmniBaseClient(transport)
    written = client.write_artifact(
        idempotency_key="artifact-write-1",
        display_name="note",
        media_type="text/plain",
        content=b"hello",
    )
    assert written.size_bytes == 5
    assert transport.calls[0][2]["content_base64"] == "aGVsbG8="
    assert len(str(transport.calls[0][2]["content_sha256"])) == 64

    derived = client.create_derived(
        idempotency_key="derived-create-1",
        display_name="summary",
        source_resource_ids=(RESOURCE_ID,),
        chunks=(DerivedChunkWrite("summary", RESOURCE_ID),),
    )
    assert derived.chunk_count == 1
    assert [call[1] for call in transport.calls] == [
        "/gateway/v1/artifacts/write",
        "/gateway/v1/rag/derived/create",
    ]
    assert "workspace_id" not in transport.calls[1][2]
