"""Security and contract tests for pure P34.3 CRUD mutation planning."""

from __future__ import annotations

from math import inf, nan
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import postgresql

from omnibase.controlled_data.crud import (
    MutationBudgetExceeded,
    MutationColumnBinding,
    MutationContractError,
    MutationVersionConflict,
    TrustedMutationLocator,
    canonical_request_hash,
    prepare_delete,
    prepare_insert,
    prepare_update,
)
from omnibase.controlled_data.crud_contracts import (
    MAX_MUTATION_ROWS,
    DeleteMutationRequest,
    InsertMutationRequest,
    UpdateMutationRequest,
)
from omnibase.controlled_data.identifiers import column_identifier, table_identifier

RESOURCE_ID = UUID("10000000-0000-0000-0000-000000000001")
TABLE_ID = UUID("20000000-0000-0000-0000-000000000001")
STRING_ID = UUID("30000000-0000-0000-0000-000000000001")
INT_ID = UUID("30000000-0000-0000-0000-000000000002")
DECIMAL_ID = UUID("30000000-0000-0000-0000-000000000003")
BOOL_ID = UUID("30000000-0000-0000-0000-000000000004")
UUID_ID = UUID("30000000-0000-0000-0000-000000000005")
DATE_ID = UUID("30000000-0000-0000-0000-000000000006")
TIMESTAMP_ID = UUID("30000000-0000-0000-0000-000000000007")


def _column(
    logical_id: UUID,
    data_type: str,
    type_args: dict[str, object],
    *,
    nullable: bool = True,
) -> MutationColumnBinding:
    return MutationColumnBinding(
        logical_id=logical_id,
        physical_name=column_identifier(logical_id),
        data_type=data_type,  # type: ignore[arg-type]
        type_args=type_args,
        nullable=nullable,
    )


def _locator(*, version: int = 4) -> TrustedMutationLocator:
    columns = {
        STRING_ID: _column(STRING_ID, "string", {"max_length": 10_000}, nullable=False),
        INT_ID: _column(INT_ID, "int64", {}),
        DECIMAL_ID: _column(DECIMAL_ID, "decimal", {"precision": 10, "scale": 2}),
        BOOL_ID: _column(BOOL_ID, "boolean", {}),
        UUID_ID: _column(UUID_ID, "uuid", {}),
        DATE_ID: _column(DATE_ID, "date", {}),
        TIMESTAMP_ID: _column(TIMESTAMP_ID, "timestamp_tz", {}),
    }
    return TrustedMutationLocator(
        tenant_schema="tenant_deadbeef",
        table_binding_id=TABLE_ID,
        resource_id=RESOURCE_ID,
        resource_version=version,
        physical_table_name=table_identifier(RESOURCE_ID),
        columns=columns,
    )


def _base() -> dict[str, object]:
    return {
        "resource_id": RESOURCE_ID,
        "resource_version": 4,
        "idempotency_key": "idem.request-0001",
    }


def _predicate(column_id: UUID = STRING_ID, value: object = "safe") -> dict[str, object]:
    return {
        "kind": "compare",
        "column_id": column_id,
        "op": "eq",
        "value": value,
    }


def test_dtos_reject_physical_scope_raw_sql_and_unconditional_mutation() -> None:
    for forbidden_field in (
        "schema",
        "schema_name",
        "table",
        "physical_table_name",
        "sql",
        "raw_sql",
        "where_sql",
    ):
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            InsertMutationRequest.model_validate(
                {
                    **_base(),
                    "rows": [{STRING_ID: "safe"}],
                    forbidden_field: "attacker controlled",
                }
            )

    with pytest.raises(ValidationError):
        UpdateMutationRequest.model_validate({**_base(), "values": {STRING_ID: "changed"}})
    with pytest.raises(ValidationError):
        DeleteMutationRequest.model_validate(_base())
    with pytest.raises(ValidationError):
        UpdateMutationRequest.model_validate({**_base(), "predicate": _predicate(), "values": {}})


def test_locator_identifiers_must_be_server_deterministic() -> None:
    with pytest.raises(MutationContractError, match="table physical"):
        TrustedMutationLocator(
            tenant_schema="tenant_deadbeef",
            table_binding_id=TABLE_ID,
            resource_id=RESOURCE_ID,
            resource_version=1,
            physical_table_name='safe"; DROP TABLE users; --',
            columns={STRING_ID: _column(STRING_ID, "string", {"max_length": 100})},
        )
    with pytest.raises(MutationContractError, match="column physical"):
        MutationColumnBinding(
            logical_id=STRING_ID,
            physical_name='name"; DROP TABLE users; --',
            data_type="string",
            type_args={"max_length": 100},
            nullable=True,
        )
    with pytest.raises(MutationContractError, match="tenant schema"):
        TrustedMutationLocator(
            tenant_schema='tenant_safe"; DROP SCHEMA public; --',
            table_binding_id=TABLE_ID,
            resource_id=RESOURCE_ID,
            resource_version=1,
            physical_table_name=table_identifier(RESOURCE_ID),
            columns={STRING_ID: _column(STRING_ID, "string", {"max_length": 100})},
        )


def test_insert_values_are_bound_and_identifier_injection_remains_plain_data() -> None:
    attack = "x'); DROP TABLE users; --"
    request = InsertMutationRequest.model_validate({**_base(), "rows": [{STRING_ID: attack}]})
    prepared = prepare_insert(_locator(), request)
    compiled = prepared.statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert attack not in sql
    assert attack in compiled.params.values()
    assert table_identifier(RESOURCE_ID) in sql
    assert column_identifier(STRING_ID) in sql
    assert prepared.row_count == 1
    assert prepared.resource_version == 4


def test_update_predicate_is_bound_and_apply_requires_bounded_server_row_tokens() -> None:
    attack = "x' OR TRUE; --"
    request = UpdateMutationRequest.model_validate(
        {
            **_base(),
            "predicate": _predicate(value=attack),
            "values": {STRING_ID: "changed"},
            "max_rows": 2,
        }
    )
    prepared = prepare_update(_locator(), request)
    preflight = prepared.preflight.compile(dialect=postgresql.dialect())
    assert attack not in str(preflight)
    assert attack in preflight.params.values()
    assert preflight.params["param_1"] == 3  # max_rows + 1 detects overflow
    assert "FOR UPDATE" in str(preflight)

    with pytest.raises(MutationBudgetExceeded):
        prepared.build_apply_statement([])
    with pytest.raises(MutationBudgetExceeded):
        prepared.build_apply_statement(["(0,1)", "(0,2)", "(0,3)"])
    with pytest.raises(MutationContractError, match="malformed"):
        prepared.build_apply_statement(["(0,1); DROP TABLE users"])

    statement = prepared.build_apply_statement(["(0,1)", "(0,2)"])
    compiled = statement.compile(dialect=postgresql.dialect())
    assert "DROP TABLE" not in str(compiled)
    assert "UPDATE" in str(compiled)
    assert compiled.params["_target_row_tokens"] == ["(0,1)", "(0,2)"]


def test_delete_is_filtered_bounded_and_never_compiles_full_table_delete() -> None:
    request = DeleteMutationRequest.model_validate(
        {**_base(), "predicate": _predicate(INT_ID, 7), "max_rows": 1}
    )
    prepared = prepare_delete(_locator(), request)
    preflight_sql = str(prepared.preflight.compile(dialect=postgresql.dialect()))
    assert "WHERE" in preflight_sql
    statement = prepared.build_apply_statement(["(12,3)"])
    apply_sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "DELETE FROM" in apply_sql
    assert "WHERE" in apply_sql
    assert "ctid" in apply_sql


@pytest.mark.parametrize(
    ("column_id", "value"),
    [
        (INT_ID, True),
        (INT_ID, 2**63),
        (DECIMAL_ID, True),
        (DECIMAL_ID, 1.25),
        (DECIMAL_ID, "NaN"),
        (DECIMAL_ID, "Infinity"),
        (DECIMAL_ID, "123456789.12"),
        (BOOL_ID, 1),
        (UUID_ID, "not-a-uuid"),
        (DATE_ID, "2026-02-30"),
        (TIMESTAMP_ID, "2026-07-31T12:00:00"),
    ],
)
def test_logical_values_reject_type_confusion_and_non_finite_numbers(
    column_id: UUID,
    value: object,
) -> None:
    request = UpdateMutationRequest.model_validate(
        {
            **_base(),
            "predicate": _predicate(),
            "values": {column_id: value},
        }
    )
    with pytest.raises(MutationContractError):
        prepare_update(_locator(), request)


@pytest.mark.parametrize("value", [nan, inf, -inf])
def test_json_non_finite_numbers_are_rejected_by_dto(value: float) -> None:
    with pytest.raises(ValidationError):
        UpdateMutationRequest.model_validate(
            {
                **_base(),
                "predicate": _predicate(),
                "values": {DECIMAL_ID: value},
            }
        )


def test_valid_decimal_date_timestamp_uuid_values_are_normalized_as_bound_params() -> None:
    request = InsertMutationRequest.model_validate(
        {
            **_base(),
            "rows": [
                {
                    STRING_ID: "valid",
                    DECIMAL_ID: "123.40",
                    DATE_ID: "2026-07-31",
                    TIMESTAMP_ID: "2026-07-31T12:00:00+08:00",
                    UUID_ID: "40000000-0000-0000-0000-000000000001",
                    BOOL_ID: False,
                }
            ],
        }
    )
    compiled = prepare_insert(_locator(), request).statement.compile(dialect=postgresql.dialect())
    values = list(compiled.params.values())
    assert "123.40" not in str(compiled)
    assert any(str(value) == "123.40" for value in values)
    assert any(str(value) == "2026-07-31" for value in values)
    assert any(str(value).startswith("2026-07-31 12:00:00+08:00") for value in values)


def test_cross_resource_columns_missing_required_columns_and_stale_versions_fail_closed() -> None:
    foreign_column = uuid4()
    with pytest.raises(MutationContractError, match="not bound"):
        prepare_update(
            _locator(),
            UpdateMutationRequest.model_validate(
                {
                    **_base(),
                    "predicate": _predicate(),
                    "values": {foreign_column: "forged"},
                }
            ),
        )
    with pytest.raises(MutationContractError, match="not bound"):
        prepare_delete(
            _locator(),
            DeleteMutationRequest.model_validate(
                {**_base(), "predicate": _predicate(foreign_column, "forged")}
            ),
        )
    with pytest.raises(MutationContractError, match="required"):
        prepare_insert(
            _locator(),
            InsertMutationRequest.model_validate({**_base(), "rows": [{INT_ID: 1}]}),
        )
    with pytest.raises(MutationVersionConflict):
        prepare_delete(
            _locator(version=5),
            DeleteMutationRequest.model_validate({**_base(), "predicate": _predicate()}),
        )
    with pytest.raises(MutationContractError, match="resource"):
        prepare_delete(
            _locator(),
            DeleteMutationRequest.model_validate(
                {
                    **_base(),
                    "resource_id": uuid4(),
                    "predicate": _predicate(),
                }
            ),
        )
    without_version = _base()
    without_version.pop("resource_version")
    with pytest.raises(ValidationError):
        DeleteMutationRequest.model_validate({**without_version, "predicate": _predicate()})


def test_rows_timeout_payload_and_predicate_complexity_have_hard_limits() -> None:
    with pytest.raises(ValidationError):
        InsertMutationRequest.model_validate(
            {
                **_base(),
                "rows": [{STRING_ID: "x"}] * (MAX_MUTATION_ROWS + 1),
            }
        )
    with pytest.raises(ValidationError):
        DeleteMutationRequest.model_validate(
            {**_base(), "predicate": _predicate(), "timeout_ms": 5_001}
        )
    node: dict[str, object] = _predicate()
    for _ in range(5):
        node = {"kind": "and", "clauses": [node]}
    with pytest.raises(ValidationError):
        DeleteMutationRequest.model_validate({**_base(), "predicate": node})

    oversized = InsertMutationRequest.model_validate(
        {
            **_base(),
            "rows": [{STRING_ID: "x" * 10_000} for _ in range(30)],
        }
    )
    with pytest.raises(MutationBudgetExceeded, match="byte limit"):
        prepare_insert(_locator(), oversized)


def test_request_hash_is_canonical_and_independent_of_retry_key() -> None:
    first = UpdateMutationRequest.model_validate(
        {
            **_base(),
            "predicate": _predicate(),
            "values": {STRING_ID: "changed", INT_ID: 7},
        }
    )
    retry = first.model_copy(update={"idempotency_key": "idem.request-0002"})
    changed = first.model_copy(update={"values": {STRING_ID: "different"}})
    assert canonical_request_hash(first) == canonical_request_hash(retry)
    assert canonical_request_hash(first) != canonical_request_hash(changed)
    assert len(canonical_request_hash(first)) == 64


def test_trusted_locator_and_nested_type_args_are_deep_frozen_copies() -> None:
    original_type_args: dict[str, object] = {"max_length": 500}
    column = _column(
        STRING_ID,
        "string",
        original_type_args,
        nullable=False,
    )
    original_columns = {STRING_ID: column}
    locator = TrustedMutationLocator(
        tenant_schema="tenant_deadbeef",
        table_binding_id=TABLE_ID,
        resource_id=RESOURCE_ID,
        resource_version=4,
        physical_table_name=table_identifier(RESOURCE_ID),
        columns=original_columns,
    )

    original_type_args["max_length"] = 999
    original_columns.clear()

    assert locator.columns[STRING_ID] is column
    assert locator.columns[STRING_ID].type_args == {"max_length": 500}
    with pytest.raises(TypeError):
        locator.columns[uuid4()] = column  # type: ignore[index]
    with pytest.raises(TypeError):
        locator.columns[STRING_ID].type_args["max_length"] = 999  # type: ignore[index]
