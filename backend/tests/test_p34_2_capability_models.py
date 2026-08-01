"""Persistence contracts for the P34.2 capability ledger."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint

from omnibase.capabilities.models import (
    CapabilityGrant,
    CapabilityRevocation,
    CapabilitySigningKey,
    CapabilityUsage,
)
from omnibase.db.models import GLOBAL_SCHEMA


def _checks(model: object) -> dict[str, str]:
    return {
        constraint.name: str(constraint.sqltext)
        for constraint in model.__table__.constraints  # type: ignore[attr-defined]
        if isinstance(constraint, CheckConstraint)
    }


def test_capability_models_are_global_and_tenant_scoped_where_required() -> None:
    expected = {
        CapabilitySigningKey: "capability_signing_keys",
        CapabilityGrant: "capability_grants",
        CapabilityUsage: "capability_usage",
        CapabilityRevocation: "capability_revocations",
    }
    for model, table_name in expected.items():
        assert model.__table__.name == table_name
        assert model.__table__.schema == GLOBAL_SCHEMA
    for model in (CapabilityGrant, CapabilityUsage, CapabilityRevocation):
        assert model.__table__.columns.tenant_id.nullable is False


def test_signing_key_registry_cannot_store_private_key_or_remote_url_fields() -> None:
    columns = set(CapabilitySigningKey.__table__.columns.keys())
    assert "public_key_pem" in columns
    assert "public_key_sha256" in columns
    assert {"private_key", "private_key_pem", "jku", "jwks_url", "x5u"}.isdisjoint(columns)
    assert (
        "algorithm = 'RS256'"
        in _checks(CapabilitySigningKey)["capability_signing_keys_algorithm_check"]
    )


def test_grant_database_contract_is_read_only_explicit_and_server_issued() -> None:
    checks = _checks(CapabilityGrant)
    action_check = checks["capability_grants_read_actions_check"]
    assert "data.schema.read" in action_check
    assert "data.rows.read" in action_check
    assert "rag.search" in action_check
    assert "rag.citation.read" in action_check
    assert "rows.insert" not in action_check
    assert "schema.apply" not in action_check
    assert "'*'" not in action_check
    assert "created_by_actor_type = 'system'" in checks["capability_grants_trusted_issuer_check"]
    assert "delegation_depth_limit <= 8" in checks["capability_grants_delegation_depth_check"]
    assert "approval_id IS NULL" in checks["capability_grants_p34_2_no_approval_check"]
    timeout_check = checks["capability_grants_timeout_constraint_check"]
    assert "constraints ? 'timeout_ms'" in timeout_check
    assert "BETWEEN 1 AND 5000" in timeout_check


def test_usage_and_revocation_bind_grant_to_same_tenant() -> None:
    for model in (CapabilityUsage, CapabilityRevocation):
        composite_fks = [
            constraint
            for constraint in model.__table__.constraints
            if isinstance(constraint, ForeignKeyConstraint)
            and constraint.name is not None
            and constraint.name.endswith("grant_tenant_fk")
        ]
        assert len(composite_fks) == 1
        columns = tuple(element.parent.name for element in composite_fks[0].elements)
        assert columns == ("grant_id", "tenant_id")


def test_revocation_has_separate_grant_wide_and_token_unique_indexes() -> None:
    indexes = {index.name: index for index in CapabilityRevocation.__table__.indexes}
    assert indexes["capability_revocations_grant_wide_uq"].unique is True
    assert indexes["capability_revocations_grant_jti_uq"].unique is True


def test_capability_tables_have_no_locator_sql_or_credential_columns() -> None:
    forbidden = {
        "physical_locator",
        "schema_name",
        "object_key",
        "host_path",
        "sql",
        "password",
        "credential",
        "token",
    }
    for model in (
        CapabilitySigningKey,
        CapabilityGrant,
        CapabilityUsage,
        CapabilityRevocation,
    ):
        assert forbidden.isdisjoint(model.__table__.columns.keys())
