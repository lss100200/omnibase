"""Guarded PostgreSQL acceptance tests for P34.5A3 durable dispatch state."""

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
from sqlalchemy.orm import Session, sessionmaker

from omnibase.capabilities.service import verify_and_reserve_sandbox_capability
from omnibase.sandbox.contracts import SandboxConflict
from omnibase.sandbox.operations import SandboxOperationIntent, SandboxOperationState
from omnibase.sandbox.persistence import SqlAlchemySandboxOperationStore

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.5 integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_WORKLOAD = "a" * 64
_REQUEST = "b" * 64
_SPEC = "c" * 64
_AUTH = "d" * 64


def _run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.fixture(scope="module")
def p345_state(db_engine) -> dict[str, str]:
    upgrade = _run_alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "tenant",
            "template",
            "workspace",
            "run",
            "runtime",
            "user",
            "issuer",
            "grant",
        )
    }
    suffix = uuid.uuid4().hex[:8]
    now = datetime.now(UTC)
    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "tenant_{suffix}"'))
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id, name, slug, schema_name, is_default, is_active) "
                "VALUES (:id, 'P34.5 durable', :slug, :schema, FALSE, TRUE)"
            ),
            {
                "id": ids["tenant"],
                "slug": f"p345-{suffix}",
                "schema": f"tenant_{suffix}",
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_templates "
                "(id, tenant_id, template_key, version, display_name, digest, "
                "template_spec, created_by_user_id) VALUES "
                "(:id, :tenant, :key, 1, 'P34.5', :digest, "
                '\'{"profile":"metadata-only"}\'::jsonb, :actor)'
            ),
            {
                "id": ids["template"],
                "tenant": ids["tenant"],
                "key": f"p345-{suffix}",
                "digest": "e" * 64,
                "actor": ids["user"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(id, tenant_id, kind, owner_type, display_name, state, policy_class) "
                "VALUES (:id, :tenant, 'workspace', 'system', 'P34.5 Workspace', "
                "'active', 'workspace_private')"
            ),
            {"id": ids["workspace"], "tenant": ids["tenant"]},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspaces "
                "(id, tenant_id, template_id, owner_user_id, display_name, "
                "desired_state, observed_state, quota) VALUES "
                "(:id, :tenant, :template, :owner, 'P34.5 Workspace', "
                "'running', 'running', '{}'::jsonb)"
            ),
            {
                "id": ids["workspace"],
                "tenant": ids["tenant"],
                "template": ids["template"],
                "owner": ids["user"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(id, tenant_id, kind, owner_type, owner_id, parent_id, display_name, "
                "state, policy_class) VALUES (:id, :tenant, 'run', 'workspace', "
                ":workspace, :workspace, 'P34.5 Run', 'active', 'workspace_private')"
            ),
            {
                "id": ids["run"],
                "tenant": ids["tenant"],
                "workspace": ids["workspace"],
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_runs "
                "(id, tenant_id, workspace_id, kind, generation, desired_state, "
                "observed_state, request_digest, runtime_instance_id, "
                "workload_identity_digest, created_by_user_id) VALUES "
                "(:id, :tenant, :workspace, 'batch', 1, 'running', 'running', "
                ":digest, :runtime, :workload, :actor)"
            ),
            {
                "id": ids["run"],
                "tenant": ids["tenant"],
                "workspace": ids["workspace"],
                "digest": _REQUEST,
                "runtime": ids["runtime"],
                "workload": _WORKLOAD,
                "actor": ids["user"],
            },
        )
        ids["grant"] = str(
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.capability_grants "
                    "(tenant_id, workspace_id, runtime_instance_id, "
                    "workload_identity_digest, actor_user_id, actions, resource_ids, "
                    "constraints, not_before, expires_at, max_calls, max_bytes, "
                    "max_cost_units, delegation_depth, delegation_depth_limit, "
                    "created_by_actor_type, created_by_actor_id) VALUES "
                    "(:tenant, :workspace, :runtime, :workload, :user, "
                    "ARRAY['sandbox.start']::varchar[], ARRAY[:workspace]::uuid[], "
                    "CAST(:constraints AS jsonb), :not_before, :expires, "
                    "10, 1, 10, 0, 0, 'system', :issuer) RETURNING id"
                ),
                {
                    "tenant": ids["tenant"],
                    "workspace": ids["workspace"],
                    "runtime": ids["runtime"],
                    "workload": _WORKLOAD,
                    "user": ids["user"],
                    "constraints": '{"timeout_ms":1000}',
                    "not_before": now - timedelta(seconds=1),
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
            {"grant": ids["grant"], "tenant": ids["tenant"]},
        )
    tenant_upgrade = _run_alembic("upgrade", "head")
    assert tenant_upgrade.returncode == 0, tenant_upgrade.stdout + tenant_upgrade.stderr
    return ids


def _capability_kwargs(ids: dict[str, str], operation_id: str) -> dict[str, str]:
    return {
        "operation_id": operation_id,
        "grant_id": ids["grant"],
        "expected_tenant_id": ids["tenant"],
        "expected_workspace_id": ids["workspace"],
        "expected_runtime_instance_id": ids["runtime"],
        "expected_workload_identity_digest": _WORKLOAD,
        "action": "sandbox.start",
    }


def test_0008_schema_is_global_closed_and_append_only(db_engine, p345_state) -> None:
    del p345_state
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
                "AND table_name IN ('sandbox_operations', "
                "'sandbox_operation_transitions', 'capability_usage_reservations')"
            )
        ).scalar_one()
    assert revision == "0013"
    assert {
        "sandbox_operations",
        "sandbox_operation_transitions",
        "capability_usage_reservations",
    } <= tables
    assert tenant_tables == 0


@pytest.mark.parametrize(
    ("actions", "workload", "resources", "depth", "depth_limit"),
    [
        (["sandbox.start", "data.rows.read"], _WORKLOAD, 1, 0, 0),
        (["sandbox.start"], None, 1, 0, 0),
        (["sandbox.start"], _WORKLOAD, 2, 0, 0),
        (["sandbox.start"], _WORKLOAD, 1, 1, 1),
    ],
)
def test_database_rejects_mixed_unbound_or_delegated_sandbox_grants(
    db_engine,
    p345_state,
    actions: list[str],
    workload: str | None,
    resources: int,
    depth: int,
    depth_limit: int,
) -> None:
    resource_values = [p345_state["workspace"]]
    if resources == 2:
        resource_values.append(str(uuid.uuid4()))
    statement = text(
        "INSERT INTO omnibase_meta.capability_grants "
        "(tenant_id, workspace_id, runtime_instance_id, workload_identity_digest, "
        "actor_user_id, actions, resource_ids, constraints, not_before, expires_at, "
        "max_calls, max_bytes, max_cost_units, delegation_depth, delegation_depth_limit, "
        "parent_grant_id, created_by_actor_type, created_by_actor_id) VALUES "
        "(:tenant, :workspace, :runtime, :workload, :user, "
        "CAST(:actions AS varchar[]), "
        "CAST(:resources AS uuid[]), CAST(:constraints AS jsonb), :start, :end, "
        "1, 1, 1, :depth, :depth_limit, :parent, 'system', :issuer)"
    )
    now = datetime.now(UTC)
    with (
        db_engine.connect() as connection,
        connection.begin(),
        pytest.raises(IntegrityError, match="capability_grants_action_profile_check"),
        connection.begin_nested(),
    ):
        connection.execute(
            statement,
            {
                "tenant": p345_state["tenant"],
                "workspace": p345_state["workspace"],
                "runtime": str(uuid.uuid4()),
                "workload": workload,
                "user": str(uuid.uuid4()),
                "actions": actions,
                "resources": resource_values,
                "constraints": '{"timeout_ms":1000}',
                "start": now,
                "end": now + timedelta(minutes=1),
                "depth": depth,
                "depth_limit": depth_limit,
                "parent": p345_state["grant"] if depth else None,
                "issuer": str(uuid.uuid4()),
            },
        )


def test_operation_replay_reserves_once_and_store_is_durable(db_engine, p345_state) -> None:
    operation_id = str(uuid.uuid4())
    with Session(db_engine) as session, session.begin():
        first = verify_and_reserve_sandbox_capability(
            session,
            **_capability_kwargs(p345_state, operation_id),
        )
    with Session(db_engine) as session, session.begin():
        replay = verify_and_reserve_sandbox_capability(
            session,
            **_capability_kwargs(p345_state, operation_id),
        )
    assert first.verification_digest == replay.verification_digest
    with db_engine.connect() as connection:
        calls, reservations = connection.execute(
            text(
                "SELECT u.calls, count(r.operation_id) "
                "FROM omnibase_meta.capability_usage u "
                "JOIN omnibase_meta.capability_usage_reservations r "
                "ON r.grant_id = u.grant_id AND r.tenant_id = u.tenant_id "
                "WHERE u.grant_id = :grant GROUP BY u.calls"
            ),
            {"grant": p345_state["grant"]},
        ).one()
    assert calls == 1
    assert reservations == 1

    factory = sessionmaker(bind=db_engine, expire_on_commit=False)
    store = SqlAlchemySandboxOperationStore(session_factory=factory)
    intent = SandboxOperationIntent(
        operation_id=uuid.UUID(operation_id),
        tenant_id=uuid.UUID(p345_state["tenant"]),
        workspace_id=uuid.UUID(p345_state["workspace"]),
        run_id=uuid.UUID(p345_state["run"]),
        runtime_instance_id=uuid.UUID(p345_state["runtime"]),
        capability_grant_id=uuid.UUID(p345_state["grant"]),
        workspace_generation=1,
        run_fencing_token=1,
        node_fencing_token=1,
        action="sandbox.start",
        request_digest=_REQUEST,
        spec_digest=_SPEC,
    )
    assert store.begin(intent).state is SandboxOperationState.ACCEPTED
    assert store.begin(intent).state is SandboxOperationState.ACCEPTED
    assert store.authorize(intent.operation_id, evidence_digest=_AUTH).state is (
        SandboxOperationState.AUTHORIZED
    )
    assert store.authorize(intent.operation_id, evidence_digest=_AUTH).state is (
        SandboxOperationState.AUTHORIZED
    )

    barrier = threading.Barrier(2)

    def claim() -> str:
        barrier.wait(timeout=5)
        try:
            return store.claim_dispatch(intent.operation_id).state.value
        except SandboxConflict as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: claim(), range(2)))
    assert outcomes.count("dispatching") == 1
    assert outcomes.count("sandbox_operation_transition_rejected") == 1

    store.mark_ambiguous(intent.operation_id)
    store.require_reconciliation(intent.operation_id)
    terminal = store.reconcile(
        intent.operation_id,
        succeeded=False,
        evidence_digest="f" * 64,
    )
    assert terminal.state is SandboxOperationState.RECONCILED_FAILED
    assert [item.sequence for item in terminal.transitions] == [1, 2, 3, 4, 5, 6]
    with db_engine.connect() as connection:
        audit_count = connection.execute(
            text(
                "SELECT count(*) FROM omnibase_meta.audit_events " "WHERE request_id = :operation"
            ),
            {"operation": operation_id},
        ).scalar_one()
    assert audit_count == 6


def test_reservation_and_transition_evidence_reject_mutation(db_engine, p345_state) -> None:
    with db_engine.connect() as connection, connection.begin():
        for statement, params in (
            (
                "UPDATE omnibase_meta.capability_usage_reservations "
                "SET cost_units = 1 WHERE grant_id = :grant",
                {"grant": p345_state["grant"]},
            ),
            (
                "DELETE FROM omnibase_meta.sandbox_operation_transitions "
                "WHERE tenant_id = :tenant",
                {"tenant": p345_state["tenant"]},
            ),
        ):
            with pytest.raises(DBAPIError, match="append-only"), connection.begin_nested():
                connection.execute(text(statement), params)


def test_zz_0008_populated_downgrade_is_fail_closed(db_engine, p345_state) -> None:
    del p345_state
    downgrade = _run_alembic("downgrade", "0007")
    assert downgrade.returncode != 0
    assert "refusing populated P34.5 downgrade" in downgrade.stdout + downgrade.stderr
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
            == "0013"
        )
