"""Isolated PostgreSQL acceptance tests for the P34.1 control-plane foundation.

This module is intentionally unavailable in the normal test suite.  The
integration conftest additionally verifies the dedicated database name,
sentinel table, and restricted role before yielding ``db_engine``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.1 integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _upgrade_head() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _insert_rollback_only_tenant(connection: object) -> tuple[str, str]:
    tenant_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]
    schema_name = f"tenant_{suffix}"
    connection.execute(  # type: ignore[attr-defined]
        text(
            "INSERT INTO omnibase_meta.tenants "
            "(id, name, slug, schema_name, is_default, is_active) "
            "VALUES (:id, :name, :slug, :schema, FALSE, TRUE)"
        ),
        {
            "id": tenant_id,
            "name": "P34.1 rollback-only tenant",
            "slug": f"p34-{suffix}",
            "schema": schema_name,
        },
    )
    return tenant_id, schema_name


def test_0004_creates_only_global_control_plane_tables_and_trigger(db_engine) -> None:
    _upgrade_head()

    with db_engine.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM omnibase_meta.alembic_version")
        ).scalar_one()
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'omnibase_meta'"
                )
            ).scalars()
        )
        trigger = connection.execute(
            text(
                "SELECT 1 FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'omnibase_meta' "
                "AND c.relname = 'audit_events' "
                "AND t.tgname = 'audit_events_append_only' "
                "AND NOT t.tgisinternal"
            )
        ).scalar_one_or_none()

    assert revision == "0012"
    assert {
        "resource_registry",
        "resource_lineage",
        "audit_events",
        "operations",
        "approval_requests",
        "idempotency_records",
    } <= tables
    assert trigger == 1


def test_audit_events_database_trigger_rejects_update_and_delete(db_engine) -> None:
    _upgrade_head()
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        tenant_id, _ = _insert_rollback_only_tenant(connection)
        event_id = connection.execute(
            text(
                "INSERT INTO omnibase_meta.audit_events "
                "(tenant_id, request_id, actor_type, action, decision, risk_level) "
                "VALUES (:tenant, 'req-append-only', 'system', "
                "'control_plane.test', 'allowed', 'R0') RETURNING id"
            ),
            {"tenant": tenant_id},
        ).scalar_one()

        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(
                text("UPDATE omnibase_meta.audit_events SET action = 'tampered' " "WHERE id = :id"),
                {"id": event_id},
            )

        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(
                text("DELETE FROM omnibase_meta.audit_events WHERE id = :id"),
                {"id": event_id},
            )
    finally:
        transaction.rollback()
        connection.close()


def test_database_rejects_unsafe_kind_and_duplicate_idempotency_scope(db_engine) -> None:
    _upgrade_head()
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        tenant_id, _ = _insert_rollback_only_tenant(connection)

        valid_resource = connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(tenant_id, kind, owner_type, display_name, policy_class) "
                "VALUES (:tenant, 'agent_memory', 'system', 'Memory', "
                "'workspace_private') RETURNING id"
            ),
            {"tenant": tenant_id},
        ).scalar_one()
        assert valid_resource is not None

        with (
            pytest.raises(
                IntegrityError,
                match="resource_registry_kind_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.resource_registry "
                    "(tenant_id, kind, owner_type, display_name, policy_class) "
                    "VALUES (:tenant, '../../host', 'system', 'Unsafe', "
                    "'workspace_private')"
                ),
                {"tenant": tenant_id},
            )

        scope = {
            "tenant": tenant_id,
            "actor": "user:test",
            "operation": "resource.register",
            "key": "same-key",
            "hash": "a" * 64,
            "expires": datetime.now(UTC) + timedelta(minutes=5),
        }
        statement = text(
            "INSERT INTO omnibase_meta.idempotency_records "
            "(tenant_id, actor_scope, operation_name, key, request_hash, expires_at) "
            "VALUES (:tenant, :actor, :operation, :key, :hash, :expires)"
        )
        connection.execute(statement, scope)
        with (
            pytest.raises(
                IntegrityError,
                match="idempotency_records_scope_key_uq",
            ),
            connection.begin_nested(),
        ):
            connection.execute(statement, scope)
    finally:
        transaction.rollback()
        connection.close()


def test_database_enforces_high_risk_authorization_and_human_approval_contracts(
    db_engine,
) -> None:
    _upgrade_head()
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        tenant_id, _ = _insert_rollback_only_tenant(connection)
        requester_id = str(uuid.uuid4())
        grant_id = str(uuid.uuid4())
        deadline = datetime.now(UTC) + timedelta(minutes=5)

        high_risk_operation = text(
            "INSERT INTO omnibase_meta.operations "
            "(tenant_id, actor_type, actor_id, kind, state, risk_level, "
            "request_hash, deadline_at) "
            "VALUES (:tenant, 'user', :actor, 'data.schema.apply', :state, 'R2', "
            ":hash, :deadline) RETURNING id"
        )
        with (
            pytest.raises(
                IntegrityError,
                match="operations_high_risk_approval_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                high_risk_operation,
                {
                    "tenant": tenant_id,
                    "actor": requester_id,
                    "state": "queued",
                    "hash": "b" * 64,
                    "deadline": deadline,
                },
            )

        operation_id = connection.execute(
            high_risk_operation,
            {
                "tenant": tenant_id,
                "actor": requester_id,
                "state": "pending_approval",
                "hash": "b" * 64,
                "deadline": deadline,
            },
        ).scalar_one()
        assert operation_id is not None

        compensation_state = connection.execute(
            text(
                "INSERT INTO omnibase_meta.operations "
                "(tenant_id, actor_type, kind, state, risk_level, request_hash) "
                "VALUES (:tenant, 'system', 'operation.compensate', "
                "'compensating', 'R0', :hash) RETURNING state"
            ),
            {"tenant": tenant_id, "hash": "c" * 64},
        ).scalar_one()
        assert compensation_state == "compensating"

        approval = text(
            "INSERT INTO omnibase_meta.approval_requests "
            "(tenant_id, requester_type, requester_id, operation_id, grant_id, action, "
            "risk_level, required_approver_role, state, request_hash, expires_at, "
            "decided_by_actor_type, decided_by_actor_id) "
            "VALUES (:tenant, :requester_type, :requester_id, :operation, :grant, "
            "'data.schema.apply', 'R2', :role, :state, :hash, :expires, "
            ":decider, :decider_id)"
        )
        with (
            pytest.raises(
                IntegrityError,
                match="approval_requests_requester_identity_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                approval,
                {
                    "tenant": tenant_id,
                    "requester_type": "user",
                    "requester_id": None,
                    "operation": operation_id,
                    "grant": grant_id,
                    "role": "tenant_admin",
                    "state": "pending",
                    "hash": "b" * 64,
                    "expires": deadline,
                    "decider": None,
                    "decider_id": None,
                },
            )

        with (
            pytest.raises(
                IntegrityError,
                match="approval_requests_decider_type_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                approval,
                {
                    "tenant": tenant_id,
                    "requester_type": "user",
                    "requester_id": requester_id,
                    "operation": operation_id,
                    "grant": grant_id,
                    "role": "tenant_admin",
                    "state": "pending",
                    "hash": "b" * 64,
                    "expires": deadline,
                    "decider": "agent",
                    "decider_id": str(uuid.uuid4()),
                },
            )

        with (
            pytest.raises(
                IntegrityError,
                match="approval_requests_required_role_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                approval,
                {
                    "tenant": tenant_id,
                    "requester_type": "user",
                    "requester_id": requester_id,
                    "operation": operation_id,
                    "grant": grant_id,
                    "role": "workspace_admin",
                    "state": "pending",
                    "hash": "b" * 64,
                    "expires": deadline,
                    "decider": None,
                    "decider_id": None,
                },
            )

        with (
            pytest.raises(
                IntegrityError,
                match="approval_requests_decider_identity_pair_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                approval,
                {
                    "tenant": tenant_id,
                    "requester_type": "system",
                    "requester_id": None,
                    "operation": operation_id,
                    "grant": grant_id,
                    "role": "tenant_admin",
                    "state": "pending",
                    "hash": "b" * 64,
                    "expires": deadline,
                    "decider": "system",
                    "decider_id": None,
                },
            )

        with (
            pytest.raises(
                IntegrityError,
                match="approval_requests_decided_state_identity_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                approval,
                {
                    "tenant": tenant_id,
                    "requester_type": "system",
                    "requester_id": None,
                    "operation": operation_id,
                    "grant": grant_id,
                    "role": "tenant_admin",
                    "state": "approved",
                    "hash": "b" * 64,
                    "expires": deadline,
                    "decider": None,
                    "decider_id": None,
                },
            )
    finally:
        transaction.rollback()
        connection.close()
