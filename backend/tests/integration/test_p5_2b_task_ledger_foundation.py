"""Guarded PostgreSQL acceptance tests for the P5.2B Task ledger foundation."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.integration.test_p5_1b_agent_registry_foundation import (
    ACTOR_ID,
    _binding_dto,
    _binding_mapping,
    _install,
    _run_alembic,
    _seed_definition_version,
    _session,
    _upgrade_head,
)

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P5.2B integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration

_TABLES = {
    "agent_tasks",
    "agent_runs",
    "agent_steps",
    "agent_step_dependencies",
    "agent_attempts",
    "agent_task_leases",
    "agent_task_fencing_cursors",
    "agent_task_budget_ledgers",
    "agent_task_effects",
    "agent_checkpoints",
    "agent_reconciliation_cases",
}


@pytest.fixture(scope="module", autouse=True)
def p52b_schema(db_engine) -> None:  # type: ignore[no-untyped-def]
    del db_engine
    _upgrade_head()


def _head(connection) -> str:  # type: ignore[no-untyped-def]
    return str(
        connection.execute(
            text("SELECT version_num FROM omnibase_meta.alembic_version")
        ).scalar_one()
    )


def _installed_binding(db_engine, run_owned_resources, label: str):  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine,
        run_owned_resources,
        label,
    )
    binding = _binding_dto(
        _binding_mapping(
            tenant_id,
            workspace_id,
            version.agent_definition_id,
            version,
        )
    )
    with _session(db_engine, tenant_id) as session:
        model = _install(
            session,
            tenant_id=tenant_id,
            binding=binding,
            key=uuid.uuid4().hex,
        )
        session.commit()
    return tenant_id, workspace_id, version, model


def _insert_minimal_task(
    connection,
    *,
    tenant_id: str,
    workspace_id: str,
    version,
    binding,
) -> str:  # type: ignore[no-untyped-def]
    task_id = str(uuid.uuid4())
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.resource_registry "
            "(id, tenant_id, kind, owner_type, owner_id, parent_id, display_name, "
            "state, policy_class, created_by_actor_id) "
            "VALUES (:id, :tenant, 'agent_task', 'workspace', :workspace, :workspace, "
            "'P5.2B test task', 'active', 'workspace_private', :actor)"
        ),
        {
            "id": task_id,
            "tenant": tenant_id,
            "workspace": workspace_id,
            "actor": ACTOR_ID,
        },
    )
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.agent_tasks "
            "(id, tenant_id, workspace_id, workspace_generation, actor_user_id, "
            "agent_definition_id, agent_version_id, agent_version_digest, "
            "workspace_agent_binding_id, task_generation, plan_id, plan_version, "
            "plan_digest, deadline, state, resource_scope_digest, budget_policy_digest, "
            "request_hash) VALUES "
            "(:id, :tenant, :workspace, 1, :actor, :definition, :version, :version_digest, "
            ":binding, 1, :plan, 1, :digest, :deadline, 'created', :digest, :digest, :digest)"
        ),
        {
            "id": task_id,
            "tenant": tenant_id,
            "workspace": workspace_id,
            "actor": ACTOR_ID,
            "definition": version.agent_definition_id,
            "version": version.agent_version_id,
            "version_digest": version.canonical_digest(),
            "binding": binding.id,
            "plan": str(uuid.uuid4()),
            "digest": "a" * 64,
            "deadline": datetime.now(UTC) + timedelta(hours=1),
        },
    )
    connection.execute(
        text(
            "INSERT INTO omnibase_meta.agent_task_fencing_cursors "
            "(task_id, tenant_id, next_fencing_token) VALUES (:task, :tenant, 1)"
        ),
        {"task": task_id, "tenant": tenant_id},
    )
    return task_id


def test_migration_head_and_exact_table_set(db_engine) -> None:  # type: ignore[no-untyped-def]
    with db_engine.connect() as connection:
        assert _head(connection) == "0014"
        tables = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT tablename FROM pg_tables "
                    "WHERE schemaname = 'omnibase_meta' AND tablename LIKE 'agent_%'"
                )
            )
        }
    assert tables >= _TABLES


def test_deferred_attempt_lease_fk_and_database_triggers_exist(db_engine) -> None:  # type: ignore[no-untyped-def]
    with db_engine.connect() as connection:
        constraint = connection.execute(
            text(
                "SELECT condeferrable, condeferred FROM pg_constraint "
                "WHERE conname = 'agent_attempts_current_lease_fk'"
            )
        ).one()
        trigger_names = {
            str(row[0])
            for row in connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'omnibase_meta' AND NOT t.tgisinternal "
                    "AND c.relname LIKE 'agent_%'"
                )
            )
        }
    assert constraint == (True, True)
    assert {
        "agent_task_state_guard",
        "agent_run_state_guard",
        "agent_step_guard",
        "agent_attempt_guard",
        "agent_task_lease_guard",
        "agent_attempt_lease_consistency_from_attempt",
        "agent_attempt_lease_consistency_from_lease",
        "agent_task_effect_guard",
        "agent_reconciliation_guard",
    } <= trigger_names


def test_tenant_schema_advances_to_0014_without_ledger_tables(
    db_engine, run_owned_resources
) -> None:  # type: ignore[no-untyped-def]
    tenant_id, _, _, _ = _installed_binding(
        db_engine,
        run_owned_resources,
        "tenant-noop",
    )
    with db_engine.connect() as connection:
        schema = str(
            connection.execute(
                text("SELECT schema_name FROM omnibase_meta.tenants WHERE id = :tenant"),
                {"tenant": tenant_id},
            ).scalar_one()
        )
        tenant_head = str(
            connection.execute(
                text(
                    f'SELECT version_num FROM "{schema}".alembic_version'  # noqa: S608
                )
            ).scalar_one()
        )
        ledger_table_count = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM pg_tables "
                    "WHERE schemaname = :schema AND tablename = ANY(:tables)"
                ),
                {"schema": schema, "tables": sorted(_TABLES)},
            ).scalar_one()
        )
    assert tenant_head == "0014"
    assert ledger_table_count == 0


def test_empty_downgrade_and_reupgrade_are_safe(db_engine) -> None:  # type: ignore[no-untyped-def]
    downgrade = _run_alembic("downgrade", "0010")
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    with db_engine.connect() as connection:
        assert _head(connection) == "0010"
    _upgrade_head()
    with db_engine.connect() as connection:
        assert _head(connection) == "0014"


def test_cross_tenant_workspace_reference_is_rejected(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    tenant_a, _, version_a, binding_a = _installed_binding(
        db_engine,
        run_owned_resources,
        "tenant-a",
    )
    _, workspace_b, _, _ = _installed_binding(
        db_engine,
        run_owned_resources,
        "tenant-b",
    )
    with pytest.raises(IntegrityError), db_engine.begin() as connection:
        _insert_minimal_task(
            connection,
            tenant_id=tenant_a,
            workspace_id=workspace_b,
            version=version_a,
            binding=binding_a,
        )


def test_populated_0011_downgrade_fails_closed(db_engine, run_owned_resources) -> None:  # type: ignore[no-untyped-def]
    tenant_id, workspace_id, version, binding = _installed_binding(
        db_engine,
        run_owned_resources,
        "populated-downgrade",
    )
    with db_engine.begin() as connection:
        task_id = _insert_minimal_task(
            connection,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            version=version,
            binding=binding,
        )
    downgrade = _run_alembic("downgrade", "0010")
    assert downgrade.returncode != 0
    assert "P5.2B populated downgrade is forbidden" in (downgrade.stdout + downgrade.stderr)
    with db_engine.connect() as connection:
        assert _head(connection) == "0014"
        assert (
            str(
                connection.execute(
                    text("SELECT id FROM omnibase_meta.agent_tasks WHERE id = :id"),
                    {"id": task_id},
                ).scalar_one()
            )
            == task_id
        )
