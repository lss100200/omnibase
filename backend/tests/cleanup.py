"""Fail-closed helpers for destructive integration-test database operations.

Importing this module never connects to a database and never mutates anything.
The command-line entry point only removes explicitly named resources after all
safety checks pass.
"""

from __future__ import annotations

import argparse
import os
import re
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

TEST_DATABASE_NAME_RE = re.compile(r"^omnibase_test_[a-z0-9_]+$")
TENANT_SCHEMA_RE = re.compile(r"^tenant_[0-9a-f]{8}$")
SENTINEL_MARKER = "OMNIBASE_DESTRUCTIVE_TEST_DATABASE_V1"
_SENTINEL_QUERY = "SELECT marker FROM public.omnibase_test_sentinel " "WHERE marker = %s"
_DATABASE_IDENTITY_QUERY = """
SELECT
    current_database(),
    current_user,
    pg_get_userbyid(d.datdba),
    r.rolsuper,
    r.rolcreatedb,
    r.rolcreaterole,
    r.rolreplication
FROM pg_database AS d
JOIN pg_roles AS r ON r.rolname = current_user
WHERE d.datname = current_database()
"""


class DestructiveTestSafetyError(RuntimeError):
    """Raised when a destructive integration-test safety check fails."""


@dataclass(frozen=True)
class TenantResource:
    """An exact tenant registry row and schema owned by one test run."""

    tenant_id: str
    schema_name: str


def require_destructive_test_environment(environ: dict[str, str] | None = None) -> str:
    """Require explicit opt-in and an explicit test-only database URL."""
    env = os.environ if environ is None else environ
    if env.get("OMNIBASE_INTEGRATION_TESTS") != "1":
        raise DestructiveTestSafetyError("OMNIBASE_INTEGRATION_TESTS must be exactly '1'")

    test_database_url = env.get("TEST_DATABASE_URL", "").strip()
    if not test_database_url:
        raise DestructiveTestSafetyError("TEST_DATABASE_URL must be set explicitly")
    return test_database_url


def verify_database_identity(
    fetch_one: Callable[[str, Sequence[Any]], Sequence[Any] | None],
) -> None:
    """Verify database name, sentinel, and a restricted non-owner current role."""
    identity = fetch_one(_DATABASE_IDENTITY_QUERY, ())
    if identity is None or len(identity) != 7:
        raise DestructiveTestSafetyError("could not verify the current database and role")

    database_name, current_user, database_owner, *role_flags = identity
    if not isinstance(database_name, str) or not TEST_DATABASE_NAME_RE.fullmatch(database_name):
        raise DestructiveTestSafetyError("current_database() must match 'omnibase_test_[a-z0-9_]+'")
    if current_user == database_owner:
        raise DestructiveTestSafetyError("the integration-test role must not own the database")
    if any(role_flags):
        raise DestructiveTestSafetyError(
            "the integration-test role must be NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
        )

    sentinel = fetch_one(_SENTINEL_QUERY, (SENTINEL_MARKER,))
    if sentinel is None or tuple(sentinel) != (SENTINEL_MARKER,):
        raise DestructiveTestSafetyError("the destructive-test database sentinel is missing")


def validate_tenant_resource(resource: TenantResource) -> TenantResource:
    """Validate exact resource identifiers before constructing schema DDL."""
    try:
        normalized_id = str(uuid.UUID(resource.tenant_id))
    except (ValueError, AttributeError) as exc:
        raise DestructiveTestSafetyError("tenant_id must be a UUID") from exc
    if not TENANT_SCHEMA_RE.fullmatch(resource.schema_name):
        raise DestructiveTestSafetyError("schema_name must match 'tenant_[0-9a-f]{8}'")
    return TenantResource(tenant_id=normalized_id, schema_name=resource.schema_name)


def verify_sqlalchemy_connection(connection: Any) -> None:
    """Apply the database identity checks to a SQLAlchemy connection."""
    from sqlalchemy import text

    def fetch_one(query: str, params: Sequence[Any]) -> Sequence[Any] | None:
        if query == _SENTINEL_QUERY:
            row = connection.execute(
                text("SELECT marker FROM public.omnibase_test_sentinel " "WHERE marker = :marker"),
                {"marker": params[0]},
            ).one_or_none()
        else:
            row = connection.execute(text(query)).one_or_none()
        return None if row is None else tuple(row)

    verify_database_identity(fetch_one)


def delete_exact_tenant(
    connection: Any,
    resource: TenantResource,
    *,
    retain_if_audited: bool = False,
) -> bool:
    """Drop one exact tenant, or retain it when append-only audit requires it.

    ``audit_events`` deliberately cannot be updated or deleted.  A tenant that
    owns audit rows therefore cannot be removed through its ``ON DELETE
    CASCADE`` foreign key without violating the production append-only
    contract.  Disposable integration suites may retain that exact tenant
    until the entire sentinel database is destroyed; the standalone cleanup
    command remains fail-closed by leaving ``retain_if_audited`` disabled.

    Returns ``True`` when the tenant was removed and ``False`` when an audited
    tenant was intentionally retained for whole-database teardown.
    """
    from sqlalchemy import text

    resource = validate_tenant_resource(resource)
    row = connection.execute(
        text("SELECT schema_name FROM omnibase_meta.tenants WHERE id = :tenant_id"),
        {"tenant_id": resource.tenant_id},
    ).one_or_none()
    if row is None or row[0] != resource.schema_name:
        raise DestructiveTestSafetyError(
            "the exact tenant registry row does not match the requested schema"
        )

    audit_table = connection.execute(
        text("SELECT to_regclass('omnibase_meta.audit_events')")
    ).scalar_one_or_none()
    if audit_table is not None:
        has_audit = connection.execute(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM omnibase_meta.audit_events "
                "WHERE tenant_id = :tenant_id)"
            ),
            {"tenant_id": resource.tenant_id},
        ).scalar_one()
        if has_audit:
            if retain_if_audited:
                return False
            raise DestructiveTestSafetyError(
                "append-only audit rows require whole disposable database teardown"
            )

    connection.execute(text(f'DROP SCHEMA IF EXISTS "{resource.schema_name}" CASCADE'))
    result = connection.execute(
        text(
            "DELETE FROM omnibase_meta.tenants "
            "WHERE id = :tenant_id AND schema_name = :schema_name"
        ),
        {"tenant_id": resource.tenant_id, "schema_name": resource.schema_name},
    )
    if result.rowcount != 1:
        raise DestructiveTestSafetyError("exactly one tenant registry row was not deleted")
    return True


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant-id", required=True, help="Exact tenant registry UUID")
    parser.add_argument("--schema", required=True, help="Exact tenant schema name")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Remove one explicitly identified tenant resource after all guards pass."""
    args = _parse_args(argv)
    resource = validate_tenant_resource(TenantResource(args.tenant_id, args.schema))
    database_url = require_destructive_test_environment()

    import psycopg

    with psycopg.connect(database_url) as conn:

        def fetch_one(query: str, params: Sequence[Any]) -> Sequence[Any] | None:
            with conn.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchone()

        verify_database_identity(fetch_one)

        with conn.cursor() as cursor:
            cursor.execute(
                "SELECT schema_name FROM omnibase_meta.tenants WHERE id = %s",
                (resource.tenant_id,),
            )
            row = cursor.fetchone()
            if row is None or row[0] != resource.schema_name:
                raise DestructiveTestSafetyError(
                    "the exact tenant registry row does not match the requested schema"
                )

            # The identifier is interpolated only after a strict allowlist check.
            cursor.execute(f'DROP SCHEMA IF EXISTS "{resource.schema_name}" CASCADE')
            cursor.execute(
                "DELETE FROM omnibase_meta.tenants WHERE id = %s AND schema_name = %s",
                (resource.tenant_id, resource.schema_name),
            )
            if cursor.rowcount != 1:
                raise DestructiveTestSafetyError("exactly one tenant registry row was not deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
