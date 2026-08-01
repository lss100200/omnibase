"""Structured-query security contracts for P34.2."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from omnibase.capability_gateway import adapters as adapters_module
from omnibase.capability_gateway.adapters import (
    AdapterError,
    CanonicalRagReadAdapter,
    PostgresDataReadAdapter,
    ResultBudgetExceeded,
)
from omnibase.capability_gateway.contracts import (
    ReadQuery,
    TrustedWorkloadContext,
    WorkloadCredential,
)
from omnibase.capability_gateway.query import (
    CursorCodec,
    CursorScope,
    QueryContractError,
    compile_select,
    parse_postgres_binding,
)
from omnibase.capability_gateway.security import (
    CapabilityVerificationError,
    CoreCapabilityVerifier,
)

COL_ID = "10000000-0000-0000-0000-000000000001"


def _binding():
    return parse_postgres_binding(
        {
            "adapter": "postgres",
            "schema": "tenant_deadbeef",
            "table": "data_1234",
            "columns": {
                COL_ID: {
                    "name": "col_1234",
                    "display_name": "Name",
                    "type": "text",
                    "nullable": False,
                }
            },
        }
    )


def test_filter_value_is_bound_and_never_becomes_sql() -> None:
    attack = "x'; DROP TABLE users; --"
    query = ReadQuery.model_validate(
        {
            "columns": [COL_ID],
            "filter": {"kind": "compare", "column_id": COL_ID, "op": "eq", "value": attack},
        }
    )
    statement = compile_select(_binding(), query, offset=0)
    compiled = statement.compile(dialect=postgresql.dialect())
    assert attack not in str(compiled)
    assert attack in compiled.params.values()


def test_forged_logical_column_id_is_rejected_before_execution() -> None:
    query = ReadQuery(columns=[UUID("20000000-0000-0000-0000-000000000001")])
    with pytest.raises(QueryContractError):
        compile_select(_binding(), query, offset=0)


def test_filter_value_must_match_locator_data_type() -> None:
    locator = {
        "adapter": "postgres",
        "schema": "tenant_deadbeef",
        "table": "data_1234",
        "columns": {
            COL_ID: {
                "name": "col_1234",
                "display_name": "Count",
                "type": "integer",
                "nullable": False,
            }
        },
    }
    query = ReadQuery.model_validate(
        {
            "columns": [COL_ID],
            "filter": {
                "kind": "compare",
                "column_id": COL_ID,
                "op": "eq",
                "value": True,
            },
        }
    )
    with pytest.raises(QueryContractError):
        compile_select(parse_postgres_binding(locator), query, offset=0)


@pytest.mark.parametrize("field", ["schema", "table"])
def test_physical_identifier_injection_in_internal_locator_fails_closed(field: str) -> None:
    locator = {
        "adapter": "postgres",
        "schema": "tenant_deadbeef",
        "table": "data_1234",
        "columns": {
            COL_ID: {
                "name": "col_1234",
                "display_name": "Name",
                "type": "text",
                "nullable": False,
            }
        },
    }
    locator[field] = 'safe"; DROP TABLE x; --'
    with pytest.raises(QueryContractError):
        parse_postgres_binding(locator)


def test_cursor_is_authenticated_and_bounded() -> None:
    codec = CursorCodec(b"a" * 32)
    scope = CursorScope(
        tenant_id="10000000-0000-0000-0000-000000000001",
        resource_id="20000000-0000-0000-0000-000000000001",
        resource_version=1,
        query_hash="a" * 64,
    )
    cursor = codec.encode(42, scope)
    assert codec.decode(cursor, scope) == 42
    with pytest.raises(QueryContractError):
        codec.decode(cursor[:-1] + ("A" if cursor[-1] != "A" else "B"), scope)
    with pytest.raises(QueryContractError):
        codec.decode(
            cursor,
            CursorScope(
                tenant_id=scope.tenant_id,
                resource_id=scope.resource_id,
                resource_version=2,
                query_hash=scope.query_hash,
            ),
        )


def test_query_limits_and_filter_depth_are_bounded() -> None:
    with pytest.raises(ValueError):
        ReadQuery(columns=[UUID(COL_ID)], limit=101)
    node: dict[str, object] = {
        "kind": "compare",
        "column_id": COL_ID,
        "op": "eq",
        "value": "x",
    }
    for _ in range(5):
        node = {"kind": "and", "clauses": [node]}
    with pytest.raises(ValueError):
        ReadQuery.model_validate({"columns": [COL_ID], "filter": node})


def test_rag_admission_saturation_fails_before_executor_queue(monkeypatch) -> None:
    admission = MagicMock()
    admission.acquire.return_value = False
    executor = MagicMock()
    monkeypatch.setattr(adapters_module, "_RAG_ADMISSION", admission)
    monkeypatch.setattr(adapters_module, "_RAG_EXECUTOR", executor)
    adapter = CanonicalRagReadAdapter(MagicMock())
    monkeypatch.setattr(adapter, "_locator", lambda *args: ("tenant_deadbeef", None))
    with pytest.raises(AdapterError):
        adapter.search(
            MagicMock(),
            capability=MagicMock(),
            resource=MagicMock(),
            query="hello",
            top_k=5,
            timeout_ms=100,
            max_bytes=1024,
        )
    executor.submit.assert_not_called()


@pytest.mark.parametrize(
    "constraints",
    [
        {},
        {"timeout_ms": True},
        {"timeout_ms": 1000, "unknown": 1},
        {"timeout_ms": 0},
    ],
)
def test_core_constraint_mapping_fails_closed(monkeypatch, constraints) -> None:
    core = SimpleNamespace(
        tenant_id="10000000-0000-0000-0000-000000000001",
        workspace_id="20000000-0000-0000-0000-000000000001",
        runtime_instance_id="30000000-0000-0000-0000-000000000001",
        actor_user_id="40000000-0000-0000-0000-000000000001",
        grant_id="50000000-0000-0000-0000-000000000001",
        claims=SimpleNamespace(jti="safe-jti-00000001"),
        action="data.rows.read",
        resource_id="60000000-0000-0000-0000-000000000001",
        constraints=constraints,
    )
    monkeypatch.setattr("omnibase.capabilities.service.verify_capability", lambda *a, **k: core)
    credential = WorkloadCredential(
        authorization="token",
        identity="runtime-one",
        trusted_context=TrustedWorkloadContext(
            opaque_identity="runtime-one",
            tenant_id=core.tenant_id,
            workspace_id=core.workspace_id,
            runtime_instance_id=core.runtime_instance_id,
            certificate_thumbprint="trusted-thumbprint",
        ),
    )
    with pytest.raises(CapabilityVerificationError):
        CoreCapabilityVerifier().verify(
            MagicMock(), credential, action=core.action, resource_id=core.resource_id
        )


def test_core_timeout_constraint_maps_without_gateway_default(monkeypatch) -> None:
    core = SimpleNamespace(
        tenant_id="10000000-0000-0000-0000-000000000001",
        workspace_id="20000000-0000-0000-0000-000000000001",
        runtime_instance_id="30000000-0000-0000-0000-000000000001",
        actor_user_id="40000000-0000-0000-0000-000000000001",
        grant_id="50000000-0000-0000-0000-000000000001",
        claims=SimpleNamespace(jti="safe-jti-00000001"),
        action="data.rows.read",
        resource_id="60000000-0000-0000-0000-000000000001",
        constraints={"timeout_ms": 777},
    )
    monkeypatch.setattr("omnibase.capabilities.service.verify_capability", lambda *a, **k: core)
    credential = WorkloadCredential(
        authorization="token",
        identity="runtime-one",
        trusted_context=TrustedWorkloadContext(
            opaque_identity="runtime-one",
            tenant_id=core.tenant_id,
            workspace_id=core.workspace_id,
            runtime_instance_id=core.runtime_instance_id,
            certificate_thumbprint="trusted-thumbprint",
        ),
    )
    verified = CoreCapabilityVerifier().verify(
        MagicMock(), credential, action=core.action, resource_id=core.resource_id
    )
    assert verified.constraints.max_timeout_ms == 777


def test_rows_preflight_rejects_oversized_single_row_before_content_fetch() -> None:
    locator_store = MagicMock()
    locator_store.get_locator.return_value = {
        "adapter": "postgres",
        "schema": "tenant_deadbeef",
        "table": "data_1234",
        "columns": {
            COL_ID: {
                "name": "col_1234",
                "display_name": "Name",
                "type": "text",
                "nullable": False,
            }
        },
    }
    session = MagicMock()
    preflight = MagicMock()
    preflight.scalars.return_value.fetchmany.return_value = [2_000_000]
    session.execute.side_effect = [MagicMock(), preflight]
    adapter = PostgresDataReadAdapter(locator_store, CursorCodec(b"a" * 32))
    capability = MagicMock(tenant_id="10000000-0000-0000-0000-000000000001")
    resource = MagicMock(
        id="20000000-0000-0000-0000-000000000001",
        version=1,
    )
    with pytest.raises(ResultBudgetExceeded):
        adapter.read_rows(
            session,
            capability=capability,
            resource=resource,
            query=ReadQuery(columns=[UUID(COL_ID)], max_bytes=1024),
        )
    assert session.execute.call_count == 2


def test_citation_preflight_rejects_oversized_body_before_content_fetch() -> None:
    citation_id = "30000000-0000-0000-0000-000000000001"
    locator_store = MagicMock()
    locator_store.get_locator.return_value = {
        "adapter": "canonical_rag_v1",
        "schema": "tenant_deadbeef",
    }
    session = MagicMock()
    preflight = MagicMock()
    preflight.fetchmany.return_value = [(citation_id, 2_000_000)]
    session.execute.side_effect = [MagicMock(), preflight]
    adapter = CanonicalRagReadAdapter(locator_store)
    with pytest.raises(ResultBudgetExceeded):
        adapter.read_citations(
            session,
            capability=MagicMock(),
            resource=MagicMock(),
            citation_ids=[citation_id],
            timeout_ms=100,
            max_bytes=1024,
        )
    assert session.execute.call_count == 2
