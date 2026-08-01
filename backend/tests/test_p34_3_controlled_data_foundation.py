"""Focused contracts for the P34.3 controlled-data foundation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint

from omnibase.controlled_data.contracts import (
    AuthorizationSnapshot,
    ColumnDefinition,
    ControlledTypeSpec,
    CreateTableDefinition,
    DataTableBindingRead,
    narrow_authorization_snapshot,
)
from omnibase.controlled_data.identifiers import (
    column_identifier,
    index_identifier,
    is_controlled_identifier,
    table_identifier,
)
from omnibase.controlled_data.models import (
    AuthorizationContext,
    DataColumnBinding,
    DataIndexBinding,
    DataTableBinding,
    OperationCompensation,
    OperationDispatchOutbox,
    SchemaChangePlan,
)
from omnibase.controlled_data.tenant_models import ControlledDataOperationPayload
from omnibase.controlled_data.types import (
    ALLOWED_LOGICAL_TYPES,
    FORBIDDEN_LOGICAL_TYPES,
    validate_type_spec,
)
from omnibase.db.models import GLOBAL_SCHEMA


def _checks(model: object) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in model.__table__.constraints  # type: ignore[attr-defined]
        if isinstance(constraint, CheckConstraint)
    }


def test_physical_identifiers_are_deterministic_uuid_only_and_fixed_width() -> None:
    logical_id = UUID("12345678-1234-5678-9abc-def012345678")
    assert table_identifier(logical_id) == "odt_12345678123456789abcdef012345678"
    assert column_identifier(str(logical_id)) == "odc_12345678123456789abcdef012345678"
    assert index_identifier(logical_id) == "odi_12345678123456789abcdef012345678"

    for identifier in (
        table_identifier(logical_id),
        column_identifier(logical_id),
        index_identifier(logical_id),
    ):
        assert len(identifier) == 36
        assert is_controlled_identifier(identifier)

    with pytest.raises(ValueError, match="UUID"):
        table_identifier('users"; DROP TABLE users; --')
    assert not is_controlled_identifier("users")
    assert not is_controlled_identifier("odt_ABCDEF")


def test_logical_type_allowlist_and_exact_type_args_fail_closed() -> None:
    expected_types = {
        "string",
        "int64",
        "decimal",
        "boolean",
        "uuid",
        "date",
        "timestamp_tz",
    }
    assert expected_types == ALLOWED_LOGICAL_TYPES
    assert {"json", "jsonb", "array", "bytea", "vector", "enum", "domain"} <= (
        FORBIDDEN_LOGICAL_TYPES
    )
    assert validate_type_spec("string", {"max_length": 255}).args == {"max_length": 255}
    assert validate_type_spec("decimal", {"precision": 18, "scale": 4}).args == {
        "precision": 18,
        "scale": 4,
    }
    assert validate_type_spec("timestamp_tz", {}).args == {}

    invalid = (
        ("jsonb", {}),
        ("string", {}),
        ("string", {"max_length": 10, "collation": "unsafe"}),
        ("string", {"max_length": True}),
        ("decimal", {"precision": 4, "scale": 5}),
        ("uuid", {"default": "gen_random_uuid()"}),
    )
    for name, args in invalid:
        with pytest.raises(ValueError):
            validate_type_spec(name, args)


def test_strict_dtos_reject_physical_names_sql_defaults_and_unknown_fields() -> None:
    column = {
        "id": uuid4(),
        "display_name": "Finding",
        "data_type": {"type": "string", "args": {"max_length": 500}},
        "nullable": True,
    }
    definition = CreateTableDefinition.model_validate(
        {
            "resource_id": uuid4(),
            "workspace_id": uuid4(),
            "display_name": "Findings",
            "policy_class": "workspace_private",
            "columns": [column],
        }
    )
    assert isinstance(definition.columns[0], ColumnDefinition)
    assert isinstance(definition.columns[0].data_type, ControlledTypeSpec)

    for forbidden_field in (
        "physical_table_name",
        "schema_name",
        "sql",
        "raw_sql",
        "default_sql",
        "generated",
        "check",
        "foreign_key",
    ):
        payload = {
            "resource_id": uuid4(),
            "workspace_id": uuid4(),
            "display_name": "Unsafe",
            "policy_class": "workspace_private",
            "columns": [column],
            forbidden_field: "attacker controlled",
        }
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            CreateTableDefinition.model_validate(payload)


def test_binding_policy_rejects_canonical_internal_and_derived_resources() -> None:
    base = {
        "resource_id": uuid4(),
        "workspace_id": uuid4(),
        "display_name": "Private table",
        "columns": [
            {
                "id": uuid4(),
                "display_name": "id",
                "data_type": {"type": "uuid", "args": {}},
                "nullable": False,
            }
        ],
    }
    for forbidden_policy in (
        "canonical_readonly",
        "system_internal",
        "workspace_derived",
        "data_view",
    ):
        with pytest.raises(ValidationError):
            CreateTableDefinition.model_validate({**base, "policy_class": forbidden_policy})

    with pytest.raises(ValidationError, match="workspace_id"):
        CreateTableDefinition.model_validate(
            {**base, "workspace_id": None, "policy_class": "workspace_private"}
        )


def test_authorization_sources_are_distinct_and_snapshot_can_only_narrow() -> None:
    actor_id = uuid4()
    resources = frozenset({uuid4(), uuid4()})
    snapshot = AuthorizationSnapshot(
        source="capability",
        actor_user_id=actor_id,
        grant_id=uuid4(),
        actions=frozenset({"data.rows.insert", "data.rows.update"}),
        resource_ids=resources,
        source_version=3,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    narrowed = narrow_authorization_snapshot(
        snapshot,
        actions=frozenset({"data.rows.insert"}),
        resource_ids=frozenset({next(iter(resources))}),
    )
    assert narrowed.actions < snapshot.actions
    assert narrowed.resource_ids < snapshot.resource_ids
    assert narrowed.live_recheck_required is True

    with pytest.raises(ValueError, match="subset"):
        narrow_authorization_snapshot(
            snapshot,
            actions=frozenset({"data.schema.apply"}),
            resource_ids=resources,
        )
    with pytest.raises(ValidationError, match="grant_id"):
        AuthorizationSnapshot(
            source="user_rbac",
            actor_user_id=actor_id,
            grant_id=uuid4(),
            roles=frozenset({"tenant_admin"}),
            actions=frozenset({"data.schema.apply"}),
            resource_ids=resources,
            source_version=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=5),
        )


def test_global_models_cover_foundation_and_enforce_closed_database_contracts() -> None:
    expected = {
        DataTableBinding: "data_table_bindings",
        DataColumnBinding: "data_column_bindings",
        DataIndexBinding: "data_index_bindings",
        SchemaChangePlan: "schema_change_plans",
        OperationDispatchOutbox: "operation_dispatch_outbox",
        OperationCompensation: "operation_compensations",
        AuthorizationContext: "authorization_contexts",
    }
    for model, table_name in expected.items():
        assert model.__table__.name == table_name
        assert model.__table__.schema == GLOBAL_SCHEMA
        assert model.__table__.columns.tenant_id.nullable is False

    policy = _checks(DataTableBinding)["data_table_bindings_policy_check"]
    assert "workspace_private" in policy
    assert "tenant_managed" in policy
    assert "controlled_shared" in policy
    assert "canonical_readonly" not in policy
    assert "system_internal" not in policy
    assert "workspace_derived" not in policy

    type_check = _checks(DataColumnBinding)["data_column_bindings_type_check"]
    for allowed in ALLOWED_LOGICAL_TYPES:
        assert allowed in type_check
    for forbidden in FORBIDDEN_LOGICAL_TYPES:
        assert f"'{forbidden}'" not in type_check

    plan_kind = _checks(SchemaChangePlan)["schema_change_plans_kind_check"]
    assert "create_table" in plan_kind
    assert "add_nullable_column" in plan_kind
    assert "create_btree_index" in plan_kind
    assert "drop_table" not in plan_kind
    assert "drop_column" not in plan_kind

    compensation = OperationCompensation.__table__
    assert compensation.columns.target_logical_id.nullable is False
    assert compensation.columns.plan_digest.nullable is False
    assert compensation.columns.resource_version.nullable is False
    snapshot_check = _checks(OperationCompensation)["operation_compensations_snapshot_check"]
    assert "display_name" in snapshot_check
    assert "raw_sql" in snapshot_check
    assert "raw_sql" not in plan_kind


def test_tenant_payload_is_tenant_scoped_and_public_contracts_hide_physical_fields() -> None:
    assert ControlledDataOperationPayload.__table__.schema is None
    assert ControlledDataOperationPayload.__table__.name == ("controlled_data_operation_payloads")

    public_schema = DataTableBindingRead.model_json_schema()
    serialized_schema = str(public_schema).lower()
    for forbidden in (
        "physical_table_name",
        "physical_column_name",
        "physical_index_name",
        "schema_name",
        "locator",
        "sql",
    ):
        assert forbidden not in serialized_schema

    internal_columns = set(DataTableBinding.__table__.columns.keys())
    assert "physical_table_name" in internal_columns
    assert "physical_table_name" not in DataTableBindingRead.model_fields


def test_0006_is_dual_scope_non_bootstrapping_and_downgrade_fails_closed() -> None:
    migration = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "omnibase"
        / "migrations"
        / "versions"
        / "0006_p34_3_controlled_data.py"
    ).read_text(encoding="utf-8")
    assert 'revision: str = "0006"' in migration
    assert 'down_revision: str | None = "0005"' in migration
    assert 'scope == "tenant"' in migration
    assert 'scope == "global"' in migration
    assert "unsupported migration_schema_scope" in migration
    assert '"controlled_data_operation_payloads"' in migration
    assert '"data_table_bindings"' in migration
    assert "CREATE TABLE odt_" not in migration
    assert "CREATE DYNAMIC TABLE" not in migration
    assert "downgrade refused" in migration
    assert "^(odt|odi)_[0-9a-f]{32}$" in migration
