"""Isolated PostgreSQL acceptance tests for the P34.2 capability ledger."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateSchema

from omnibase.capabilities.service import (
    CapabilityBudgetExceeded,
    TrustedIssuerContext,
    VerifiedCapability,
    consume_budget,
    revoke_grant,
)
from omnibase.capabilities.token import CapabilityTokenClaims

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.2 integration tests require OMNIBASE_INTEGRATION_TESTS=1",
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


@pytest.fixture(scope="module", autouse=True)
def capability_schema(db_engine) -> None:
    """Apply the global ledger once before append-only test records exist."""

    _upgrade_head()


def _tenant(connection, label: str) -> str:
    tenant_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]
    schema_name = f"tenant_{suffix}"
    connection.execute(CreateSchema(schema_name))
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.tenants "
            "(id, name, slug, schema_name, is_default, is_active) "
            "VALUES (:id, :name, :slug, :schema, FALSE, TRUE)"
        ),
        {
            "id": tenant_id,
            "name": f"P34.2 {label}",
            "slug": f"cap-{label}-{suffix}",
            "schema": schema_name,
        },
    )
    return tenant_id


def _grant(
    connection,
    tenant_id: str,
    *,
    max_calls: int = 2,
    max_bytes: int = 100,
    max_cost_units: int = 10,
    delegation_depth_limit: int = 0,
) -> tuple[str, dict[str, str]]:
    ids = {
        name: str(uuid.uuid4()) for name in ("workspace", "runtime", "user", "resource", "issuer")
    }
    now = datetime.now(UTC)
    grant_id = str(
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_grants "
                "(tenant_id, workspace_id, runtime_instance_id, actor_user_id, actions, "
                "resource_ids, not_before, expires_at, max_calls, max_bytes, "
                "max_cost_units, delegation_depth_limit, created_by_actor_type, "
                "created_by_actor_id) VALUES (:tenant, :workspace, :runtime, :user, "
                "ARRAY['rag.search']::varchar[], ARRAY[:resource]::uuid[], :not_before, "
                ":expires, :max_calls, :max_bytes, :max_cost, :delegation_depth_limit, "
                "'system', :issuer) "
                "RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "workspace": ids["workspace"],
                "runtime": ids["runtime"],
                "user": ids["user"],
                "resource": ids["resource"],
                "not_before": now - timedelta(minutes=1),
                "expires": now + timedelta(minutes=5),
                "max_calls": max_calls,
                "max_bytes": max_bytes,
                "max_cost": max_cost_units,
                "delegation_depth_limit": delegation_depth_limit,
                "issuer": ids["issuer"],
            },
        ).scalar_one()
    )
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.capability_usage (grant_id, tenant_id) "
            "VALUES (:grant, :tenant)"
        ),
        {"grant": grant_id, "tenant": tenant_id},
    )
    return grant_id, ids


def _child_grant(connection, tenant_id: str, parent_grant_id: str, ids: dict[str, str]) -> str:
    now = datetime.now(UTC)
    child_id = str(
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_grants "
                "(tenant_id, workspace_id, runtime_instance_id, actor_user_id, parent_grant_id, "
                "actions, resource_ids, constraints, not_before, expires_at, max_calls, max_bytes, "
                "max_cost_units, delegation_depth, delegation_depth_limit, created_by_actor_type, "
                "created_by_actor_id) VALUES (:tenant, :workspace, :runtime, :user, :parent, "
                "ARRAY['rag.search']::varchar[], ARRAY[:resource]::uuid[], "
                '\'{"rag_top_k": 5, "timeout_ms": 1500}\'::jsonb, '
                ":not_before, :expires, 1, 60, 1, 1, 1, "
                "'system', :issuer) RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "workspace": ids["workspace"],
                "runtime": ids["runtime"],
                "user": ids["user"],
                "parent": parent_grant_id,
                "resource": ids["resource"],
                "not_before": now,
                "expires": now + timedelta(minutes=4),
                "issuer": ids["issuer"],
            },
        ).scalar_one()
    )
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.capability_usage (grant_id, tenant_id) "
            "VALUES (:grant, :tenant)"
        ),
        {"grant": child_id, "tenant": tenant_id},
    )
    return child_id


def test_0005_creates_only_global_capability_tables_and_revocation_trigger(db_engine) -> None:
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
        tenant_tables = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema LIKE 'tenant\\_%' ESCAPE '\\' "
                "AND table_name LIKE 'capability_%'"
            )
        ).scalar_one()
        trigger = connection.execute(
            text(
                "SELECT 1 FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'omnibase_meta' "
                "AND c.relname = 'capability_revocations' "
                "AND t.tgname = 'capability_revocations_append_only' "
                "AND NOT t.tgisinternal"
            )
        ).scalar_one_or_none()
    assert revision == "0014"
    assert {
        "capability_signing_keys",
        "capability_grants",
        "capability_usage",
        "capability_revocations",
    } <= tables
    assert tenant_tables == 0
    assert trigger == 1


def test_database_rejects_write_actions_workspace_issuers_and_cross_tenant_usage(
    db_engine,
) -> None:
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        tenant_a = _tenant(connection, "a")
        tenant_b = _tenant(connection, "b")
        grant_id, ids = _grant(connection, tenant_a)
        params = {
            "tenant": tenant_a,
            "workspace": ids["workspace"],
            "runtime": ids["runtime"],
            "user": ids["user"],
            "resource": ids["resource"],
            "not_before": datetime.now(UTC),
            "expires": datetime.now(UTC) + timedelta(minutes=5),
            "issuer": ids["issuer"],
        }
        statement = text(
            "INSERT INTO omnibase_meta.capability_grants "
            "(tenant_id, workspace_id, runtime_instance_id, actor_user_id, actions, "
            "resource_ids, not_before, expires_at, max_calls, max_bytes, "
            "max_cost_units, delegation_depth_limit, created_by_actor_type, "
            "created_by_actor_id) VALUES (:tenant, :workspace, :runtime, :user, "
            ":actions, ARRAY[:resource]::uuid[], :not_before, :expires, "
            "1, 1, 1, 0, :issuer_type, :issuer)"
        )
        with (
            pytest.raises(IntegrityError, match="action_profile"),
            connection.begin_nested(),
        ):
            connection.execute(
                statement,
                {**params, "actions": ["data.rows.insert"], "issuer_type": "system"},
            )
        with pytest.raises(IntegrityError, match="trusted_issuer"), connection.begin_nested():
            connection.execute(
                statement,
                {**params, "actions": ["rag.search"], "issuer_type": "workspace"},
            )
        with pytest.raises(IntegrityError, match="grant_tenant_fk"), connection.begin_nested():
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.capability_revocations "
                    "(tenant_id, grant_id, reason_code, actor_type, actor_id) "
                    "VALUES (:tenant, :grant, 'security.test', 'system', :actor)"
                ),
                {
                    "tenant": tenant_b,
                    "grant": grant_id,
                    "actor": ids["issuer"],
                },
            )
        with pytest.raises(IntegrityError, match="no_approval"), connection.begin_nested():
            connection.execute(
                text(
                    "UPDATE omnibase_meta.capability_grants SET approval_id = :approval "
                    "WHERE id = :grant"
                ),
                {"approval": str(uuid.uuid4()), "grant": grant_id},
            )
        with pytest.raises(IntegrityError, match="delegation_depth"), connection.begin_nested():
            connection.execute(
                text(
                    "UPDATE omnibase_meta.capability_grants SET delegation_depth_limit = 9 "
                    "WHERE id = :grant"
                ),
                {"grant": grant_id},
            )
    finally:
        transaction.rollback()
        connection.close()


def test_revocation_is_append_only_and_budget_update_fails_closed(db_engine) -> None:
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        tenant_id = _tenant(connection, "ledger")
        grant_id, ids = _grant(connection, tenant_id, max_calls=2)
        revocation_id = connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_revocations "
                "(tenant_id, grant_id, token_jti, reason_code, actor_type, actor_id) "
                "VALUES (:tenant, :grant, :jti, 'security.test', 'system', :actor) "
                "RETURNING id"
            ),
            {
                "tenant": tenant_id,
                "grant": grant_id,
                "jti": uuid.uuid4().hex,
                "actor": ids["issuer"],
            },
        ).scalar_one()
        with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
            connection.execute(
                text("DELETE FROM omnibase_meta.capability_revocations WHERE id = :id"),
                {"id": revocation_id},
            )

        claims = CapabilityTokenClaims(
            jti=uuid.uuid4().hex,
            subject=ids["runtime"],
            tenant_id=tenant_id,
            workspace_id=ids["workspace"],
            actor_user_id=ids["user"],
            grant_id=grant_id,
            grant_version=1,
            delegation_depth=0,
            workload_thumbprint="A" * 43,
            issued_at=0,
            not_before=0,
            expires_at=1,
            approval_id=None,
        )
        verified = VerifiedCapability(
            claims=claims,
            grant_id=grant_id,
            tenant_id=tenant_id,
            workspace_id=ids["workspace"],
            runtime_instance_id=ids["runtime"],
            actor_user_id=ids["user"],
            action="rag.search",
            resource_id=ids["resource"],
            constraints={},
        )
        session = Session(bind=connection)
        consume_budget(session, verified=verified, calls=2, cost_units=2)
        with pytest.raises(CapabilityBudgetExceeded):
            consume_budget(session, verified=verified, calls=1, cost_units=1)
    finally:
        transaction.rollback()
        connection.close()


def test_concurrent_budget_reservations_cannot_overspend(db_engine) -> None:
    """Two independent transactions race; PostgreSQL must recheck the predicate."""

    tenant_id: str | None = None
    try:
        with db_engine.begin() as connection:
            tenant_id = _tenant(connection, "concurrent")
            grant_id, ids = _grant(
                connection,
                tenant_id,
                max_calls=1,
                max_bytes=60,
                max_cost_units=1,
            )

        claims = CapabilityTokenClaims(
            jti=uuid.uuid4().hex,
            subject=ids["runtime"],
            tenant_id=tenant_id,
            workspace_id=ids["workspace"],
            actor_user_id=ids["user"],
            grant_id=grant_id,
            grant_version=1,
            delegation_depth=0,
            workload_thumbprint="A" * 43,
            issued_at=0,
            not_before=0,
            expires_at=1,
            approval_id=None,
        )
        verified = VerifiedCapability(
            claims=claims,
            grant_id=grant_id,
            tenant_id=tenant_id,
            workspace_id=ids["workspace"],
            runtime_instance_id=ids["runtime"],
            actor_user_id=ids["user"],
            action="rag.search",
            resource_id=ids["resource"],
            constraints={},
        )
        barrier = threading.Barrier(2)

        def reserve_once() -> bool:
            with Session(db_engine) as session:
                barrier.wait(timeout=10)
                try:
                    consume_budget(
                        session,
                        verified=verified,
                        calls=1,
                        bytes_in=30,
                        bytes_out=30,
                        cost_units=1,
                    )
                    session.commit()
                    return True
                except CapabilityBudgetExceeded:
                    session.rollback()
                    return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: reserve_once(), range(2)))

        assert sorted(results) == [False, True]
        with db_engine.connect() as connection:
            calls, bytes_in, bytes_out, cost_units = connection.execute(
                text(
                    "SELECT calls, bytes_in, bytes_out, cost_units "
                    "FROM omnibase_meta.capability_usage WHERE grant_id = :grant"
                ),
                {"grant": grant_id},
            ).one()
        assert calls == 1
        assert bytes_in + bytes_out == 60
        assert cost_units == 1
    finally:
        if tenant_id is not None:
            with db_engine.begin() as connection:
                connection.execute(
                    text("DELETE FROM omnibase_meta.tenants WHERE id = :tenant"),
                    {"tenant": tenant_id},
                )


def test_ancestor_revoke_serializes_before_child_budget_reservation(db_engine) -> None:
    """A child cannot reserve budget after an ancestor revocation owns the root lock."""

    tenant_id: str | None = None
    try:
        with db_engine.begin() as connection:
            tenant_id = _tenant(connection, "ancestor-race")
            parent_id, ids = _grant(
                connection,
                tenant_id,
                max_calls=2,
                max_bytes=100,
                max_cost_units=2,
                delegation_depth_limit=1,
            )
            child_id = _child_grant(connection, tenant_id, parent_id, ids)

        claims = CapabilityTokenClaims(
            jti=uuid.uuid4().hex,
            subject=ids["runtime"],
            tenant_id=tenant_id,
            workspace_id=ids["workspace"],
            actor_user_id=ids["user"],
            grant_id=child_id,
            grant_version=1,
            delegation_depth=1,
            workload_thumbprint="A" * 43,
            issued_at=0,
            not_before=0,
            expires_at=1,
            approval_id=None,
        )
        verified = VerifiedCapability(
            claims=claims,
            grant_id=child_id,
            tenant_id=tenant_id,
            workspace_id=ids["workspace"],
            runtime_instance_id=ids["runtime"],
            actor_user_id=ids["user"],
            action="rag.search",
            resource_id=ids["resource"],
            constraints={"rag_top_k": 5, "timeout_ms": 1500},
        )
        ancestor_locked = threading.Event()
        consumer_started = threading.Event()
        allow_revoke_commit = threading.Event()

        def revoke_parent() -> bool:
            with Session(db_engine) as session:
                revoke_grant(
                    session,
                    tenant_id=tenant_id,
                    grant_id=parent_id,
                    reason_code="security.ancestor_test",
                    issuer_context=TrustedIssuerContext(
                        tenant_id=tenant_id,
                        system_actor_id=ids["issuer"],
                        originating_user_id=ids["user"],
                    ),
                )
                session.flush()
                ancestor_locked.set()
                assert allow_revoke_commit.wait(timeout=10)
                session.commit()
                return True

        def consume_child() -> bool:
            assert ancestor_locked.wait(timeout=10)
            consumer_started.set()
            with Session(db_engine) as session:
                try:
                    consume_budget(
                        session,
                        verified=verified,
                        calls=1,
                        bytes_in=10,
                        bytes_out=10,
                        cost_units=1,
                    )
                    session.commit()
                    return True
                except CapabilityBudgetExceeded:
                    session.rollback()
                    return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            revoke_future = executor.submit(revoke_parent)
            assert ancestor_locked.wait(timeout=10)
            consume_future = executor.submit(consume_child)
            assert consumer_started.wait(timeout=10)
            allow_revoke_commit.set()
            assert revoke_future.result(timeout=10) is True
            assert consume_future.result(timeout=10) is False

        with db_engine.connect() as connection:
            parent_state = connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.capability_grants "
                    "WHERE tenant_id = :tenant AND id = :grant"
                ),
                {"tenant": tenant_id, "grant": parent_id},
            ).scalar_one()
            child_calls = connection.execute(
                text(
                    "SELECT calls FROM omnibase_meta.capability_usage "
                    "WHERE tenant_id = :tenant AND grant_id = :grant"
                ),
                {"tenant": tenant_id, "grant": child_id},
            ).scalar_one()
        assert parent_state == "revoked"
        assert child_calls == 0
    finally:
        if tenant_id is not None:
            with db_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE omnibase_meta.tenants SET is_active = FALSE " "WHERE id = :tenant"
                    ),
                    {"tenant": tenant_id},
                )


def test_root_revoke_serializes_before_root_budget_reservation(db_engine) -> None:
    """A root grant cannot reserve budget after revocation owns its grant lock."""

    tenant_id: str | None = None
    try:
        with db_engine.begin() as connection:
            tenant_id = _tenant(connection, "root-race")
            grant_id, ids = _grant(
                connection,
                tenant_id,
                max_calls=2,
                max_bytes=100,
                max_cost_units=2,
            )

        claims = CapabilityTokenClaims(
            jti=uuid.uuid4().hex,
            subject=ids["runtime"],
            tenant_id=tenant_id,
            workspace_id=ids["workspace"],
            actor_user_id=ids["user"],
            grant_id=grant_id,
            grant_version=1,
            delegation_depth=0,
            workload_thumbprint="A" * 43,
            issued_at=0,
            not_before=0,
            expires_at=1,
            approval_id=None,
        )
        verified = VerifiedCapability(
            claims=claims,
            grant_id=grant_id,
            tenant_id=tenant_id,
            workspace_id=ids["workspace"],
            runtime_instance_id=ids["runtime"],
            actor_user_id=ids["user"],
            action="rag.search",
            resource_id=ids["resource"],
            constraints={"timeout_ms": 2000},
        )
        grant_locked = threading.Event()
        consumer_started = threading.Event()
        allow_revoke_commit = threading.Event()

        def revoke_root() -> bool:
            with Session(db_engine) as session:
                revoke_grant(
                    session,
                    tenant_id=tenant_id,
                    grant_id=grant_id,
                    reason_code="security.root_test",
                    issuer_context=TrustedIssuerContext(
                        tenant_id=tenant_id,
                        system_actor_id=ids["issuer"],
                        originating_user_id=ids["user"],
                    ),
                )
                session.flush()
                grant_locked.set()
                assert allow_revoke_commit.wait(timeout=10)
                session.commit()
                return True

        def consume_root() -> bool:
            assert grant_locked.wait(timeout=10)
            consumer_started.set()
            with Session(db_engine) as session:
                try:
                    consume_budget(
                        session,
                        verified=verified,
                        calls=1,
                        bytes_in=10,
                        bytes_out=10,
                        cost_units=1,
                    )
                    session.commit()
                    return True
                except CapabilityBudgetExceeded:
                    session.rollback()
                    return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            revoke_future = executor.submit(revoke_root)
            assert grant_locked.wait(timeout=10)
            consume_future = executor.submit(consume_root)
            assert consumer_started.wait(timeout=10)
            allow_revoke_commit.set()
            assert revoke_future.result(timeout=10) is True
            assert consume_future.result(timeout=10) is False

        with db_engine.connect() as connection:
            root_state = connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.capability_grants "
                    "WHERE tenant_id = :tenant AND id = :grant"
                ),
                {"tenant": tenant_id, "grant": grant_id},
            ).scalar_one()
            root_calls = connection.execute(
                text(
                    "SELECT calls FROM omnibase_meta.capability_usage "
                    "WHERE tenant_id = :tenant AND grant_id = :grant"
                ),
                {"tenant": tenant_id, "grant": grant_id},
            ).scalar_one()
        assert root_state == "revoked"
        assert root_calls == 0
    finally:
        if tenant_id is not None:
            with db_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE omnibase_meta.tenants SET is_active = FALSE " "WHERE id = :tenant"
                    ),
                    {"tenant": tenant_id},
                )
