"""Focused unit tests for destructive integration-test database guards."""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest

CLEANUP_PATH = Path(__file__).with_name("cleanup.py")


def _load_cleanup_module():
    spec = importlib.util.spec_from_file_location("omnibase_test_cleanup", CLEANUP_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_cleanup_import_has_no_database_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    import psycopg

    def unexpected_connect(*args, **kwargs):
        pytest.fail("importing cleanup.py must never connect to a database")

    monkeypatch.setattr(psycopg, "connect", unexpected_connect)
    _load_cleanup_module()


def test_environment_requires_both_explicit_opt_ins() -> None:
    cleanup = _load_cleanup_module()

    with pytest.raises(cleanup.DestructiveTestSafetyError, match="OMNIBASE_INTEGRATION_TESTS"):
        cleanup.require_destructive_test_environment({})
    with pytest.raises(cleanup.DestructiveTestSafetyError, match="TEST_DATABASE_URL"):
        cleanup.require_destructive_test_environment({"OMNIBASE_INTEGRATION_TESTS": "1"})

    assert (
        cleanup.require_destructive_test_environment(
            {
                "OMNIBASE_INTEGRATION_TESTS": "1",
                "TEST_DATABASE_URL": "postgresql+psycopg://runner:secret@localhost/omnibase_test_ci",
            }
        )
        == "postgresql+psycopg://runner:secret@localhost/omnibase_test_ci"
    )


@pytest.mark.parametrize(
    ("identity", "message"),
    [
        (("omnibase", "runner", "owner", False, False, False, False), "current_database"),
        (
            ("omnibase_test_ci", "owner", "owner", False, False, False, False),
            "must not own",
        ),
        (
            ("omnibase_test_ci", "runner", "owner", True, False, False, False),
            "NOSUPERUSER",
        ),
    ],
)
def test_database_identity_rejects_unsafe_targets(identity, message: str) -> None:
    cleanup = _load_cleanup_module()

    def fetch_one(query, params):
        return identity

    with pytest.raises(cleanup.DestructiveTestSafetyError, match=message):
        cleanup.verify_database_identity(fetch_one)


def test_database_identity_requires_sentinel() -> None:
    cleanup = _load_cleanup_module()
    identity = ("omnibase_test_ci", "runner", "owner", False, False, False, False)

    def fetch_one(query, params):
        if "pg_database" in query:
            return identity
        return None

    with pytest.raises(cleanup.DestructiveTestSafetyError, match="sentinel"):
        cleanup.verify_database_identity(fetch_one)


def test_database_identity_accepts_restricted_non_owner_with_sentinel() -> None:
    cleanup = _load_cleanup_module()
    identity = ("omnibase_test_ci", "runner", "owner", False, False, False, False)

    def fetch_one(query, params):
        if "pg_database" in query:
            return identity
        return (cleanup.SENTINEL_MARKER,)

    cleanup.verify_database_identity(fetch_one)


def test_resource_validation_accepts_only_exact_uuid_schema_pair() -> None:
    cleanup = _load_cleanup_module()
    tenant_id = str(uuid.uuid4())

    assert cleanup.validate_tenant_resource(
        cleanup.TenantResource(tenant_id=tenant_id, schema_name="tenant_deadbeef")
    ) == cleanup.TenantResource(tenant_id=tenant_id, schema_name="tenant_deadbeef")

    with pytest.raises(cleanup.DestructiveTestSafetyError, match="UUID"):
        cleanup.validate_tenant_resource(
            cleanup.TenantResource(tenant_id="not-a-uuid", schema_name="tenant_deadbeef")
        )
    with pytest.raises(cleanup.DestructiveTestSafetyError, match="schema_name"):
        cleanup.validate_tenant_resource(
            cleanup.TenantResource(tenant_id=tenant_id, schema_name="tenant_%")
        )


def test_exact_cleanup_can_retain_tenant_with_append_only_audit() -> None:
    cleanup = _load_cleanup_module()
    tenant_id = str(uuid.uuid4())
    schema_name = f"tenant_{tenant_id.replace('-', '')[:8]}"

    class Result:
        def __init__(self, value=None):
            self.value = value

        def one_or_none(self):
            return self.value

        def scalar_one_or_none(self):
            return self.value

        def scalar_one(self):
            return self.value

    class Connection:
        def __init__(self):
            self.statements: list[str] = []

        def execute(self, statement, parameters=None):
            sql = str(statement)
            self.statements.append(sql)
            if "SELECT schema_name" in sql:
                return Result((schema_name,))
            if "to_regclass" in sql:
                return Result("omnibase_meta.audit_events")
            if "SELECT EXISTS" in sql:
                return Result(True)
            pytest.fail(f"audited cleanup must not mutate the tenant: {sql}")

    connection = Connection()
    removed = cleanup.delete_exact_tenant(
        connection,
        cleanup.TenantResource(tenant_id=tenant_id, schema_name=schema_name),
        retain_if_audited=True,
    )

    assert removed is False
    assert not any("DROP SCHEMA" in statement for statement in connection.statements)
    assert not any("DELETE FROM" in statement for statement in connection.statements)
