"""Guarded PostgreSQL foundation Gate for P34.6 Workspace data."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from itertools import pairwise
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.6 integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _run_alembic(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )


def test_0009_global_and_tenant_foundation_guards(db_engine) -> None:
    upgrade = _run_alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    suffix = uuid.uuid4().hex[:10]
    tenant_id = str(uuid.uuid4())
    tenant_schema = f"tenant_{suffix}"
    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{tenant_schema}"'))
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id, name, slug, schema_name, is_default, is_active) "
                "VALUES (:id, 'P34.6 foundation', :slug, :schema, FALSE, TRUE)"
            ),
            {"id": tenant_id, "slug": f"p346-{suffix}", "schema": tenant_schema},
        )
    tenant_upgrade = _run_alembic("upgrade", "head")
    assert tenant_upgrade.returncode == 0, tenant_upgrade.stdout + tenant_upgrade.stderr

    expected_global = {
        "workspace_artifacts",
        "workspace_derived_indexes",
        "workspace_publications",
        "workspace_snapshot_items",
        "workspace_data_effects",
        "workspace_data_usage_reservations",
    }
    with db_engine.begin() as connection:
        global_revision = connection.execute(
            text("SELECT version_num FROM omnibase_meta.alembic_version")
        ).scalar_one()
        tenant_revision = connection.execute(
            text(f'SELECT version_num FROM "{tenant_schema}".alembic_version')  # noqa: S608
        ).scalar_one()
        assert global_revision == "0016"
        assert tenant_revision == "0016"
        tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'omnibase_meta'"
                )
            ).scalars()
        )
        assert expected_global <= tables
        assert (
            connection.execute(
                text("SELECT to_regclass(:name)"),
                {"name": f"{tenant_schema}.workspace_derived_chunks_v2"},
            ).scalar_one()
            is not None
        )

        trigger_names = set(
            connection.execute(
                text(
                    "SELECT tgname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'omnibase_meta' AND NOT t.tgisinternal"
                )
            ).scalars()
        )
        assert {
            "resource_lineage_append_only",
            "resource_lineage_cycle_guard",
            "resource_registry_immutability_guard",
            "workspace_snapshot_items_append_only",
            "workspace_snapshots_transition_guard",
            "workspace_data_effects_transition_guard",
            "workspace_data_usage_reservations_transition_guard",
        } <= trigger_names

        constraints = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint co "
                    "JOIN pg_namespace n ON n.oid = co.connamespace "
                    "WHERE n.nspname = 'omnibase_meta'"
                )
            ).scalars()
        )
        assert {
            "operations_id_tenant_uq",
            "resource_lineage_operation_tenant_fk",
            "data_table_bindings_resource_tenant_fk",
            "data_table_bindings_workspace_tenant_fk",
            "workspace_data_effects_operation_sequence_uq",
            "workspace_data_effects_binding_uq",
            "workspace_data_usage_reservations_state_result_check",
            "workspace_publications_approval_tenant_fk",
        } <= constraints

        resource_ids = [str(uuid.uuid4()) for _ in range(4)]
        for index, resource_id in enumerate(resource_ids):
            policy = "canonical_readonly" if index == 3 else "tenant_managed"
            locator = '{"lane":"canonical"}' if index == 3 else "null"
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.resource_registry "
                    "(id, tenant_id, kind, owner_type, display_name, state, "
                    "policy_class, physical_locator) VALUES "
                    "(:id, :tenant, 'artifact', 'system', :name, 'active', "
                    ":policy, CAST(:locator AS jsonb))"
                ),
                {
                    "id": resource_id,
                    "tenant": tenant_id,
                    "name": f"resource-{index}",
                    "policy": policy,
                    "locator": locator,
                },
            )
        for source, derived in pairwise(resource_ids[:3]):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.resource_lineage "
                    "(tenant_id, source_resource_id, derived_resource_id, relation, source_version) "
                    "VALUES (:tenant, :source, :derived, 'derived_from', 1)"
                ),
                {"tenant": tenant_id, "source": source, "derived": derived},
            )
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.resource_lineage "
                    "(tenant_id, source_resource_id, derived_resource_id, relation, source_version) "
                    "VALUES (:tenant, :source, :derived, 'derived_from', 1)"
                ),
                {"tenant": tenant_id, "source": resource_ids[2], "derived": resource_ids[0]},
            )
        savepoint.rollback()
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE omnibase_meta.resource_lineage SET source_version = 2 "
                    "WHERE tenant_id = :tenant"
                ),
                {"tenant": tenant_id},
            )
        savepoint.rollback()
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "UPDATE omnibase_meta.resource_registry "
                    'SET physical_locator = \'{"lane":"changed"}\'::jsonb '
                    "WHERE id = :id AND tenant_id = :tenant"
                ),
                {"id": resource_ids[3], "tenant": tenant_id},
            )
        savepoint.rollback()

    workspace_id = str(uuid.uuid4())
    template_id = str(uuid.uuid4())
    derived_resource_id = str(uuid.uuid4())
    operation_id = str(uuid.uuid4())
    actor_id = str(uuid.uuid4())
    grant_id = str(uuid.uuid4())
    runtime_instance_id = str(uuid.uuid4())
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(id, tenant_id, kind, owner_type, owner_id, display_name, state, "
                "version, policy_class) VALUES "
                "(:id, :tenant, 'workspace', 'workspace', :id, 'P34.6 workspace', "
                "'active', 1, 'workspace_private')"
            ),
            {"id": workspace_id, "tenant": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_templates "
                "(id, tenant_id, template_key, version, display_name, digest, "
                "template_spec, state, created_by_user_id) VALUES "
                "(:id, :tenant, :key, 1, 'P34.6 template', :digest, '{}'::jsonb, "
                "'active', :actor)"
            ),
            {
                "id": template_id,
                "tenant": tenant_id,
                "key": f"p346-{suffix}",
                "digest": "e" * 64,
                "actor": actor_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspaces "
                "(id, tenant_id, template_id, owner_user_id, display_name, "
                "desired_state, observed_state, generation, version, quota) VALUES "
                "(:id, :tenant, :template, :actor, 'P34.6 workspace', 'stopped', "
                "'stopped', 1, 1, '{}'::jsonb)"
            ),
            {
                "id": workspace_id,
                "tenant": tenant_id,
                "template": template_id,
                "actor": actor_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(id, tenant_id, kind, owner_type, owner_id, display_name, state, "
                "version, policy_class) VALUES "
                "(:id, :tenant, 'derived_index', 'workspace', :workspace, "
                "'P34.6 derived index', 'active', 1, 'workspace_derived')"
            ),
            {
                "id": derived_resource_id,
                "tenant": tenant_id,
                "workspace": workspace_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.capability_grants "
                "(id, tenant_id, workspace_id, runtime_instance_id, "
                "workload_identity_digest, actor_user_id, actions, resource_ids, "
                "constraints, version, state, not_before, expires_at, max_calls, "
                "max_bytes, max_cost_units, delegation_depth, delegation_depth_limit, "
                "created_by_actor_type, created_by_actor_id) VALUES "
                "(:id, :tenant, :workspace, :runtime, :workload, :actor, "
                "ARRAY['rag.derived.create']::varchar[], ARRAY[:resource]::uuid[], "
                "CAST(:constraints AS jsonb), 1, 'active', now() - interval '1 second', "
                "now() + interval '5 minutes', 10, 1048576, 10, 0, 0, 'system', :actor)"
            ),
            {
                "id": grant_id,
                "tenant": tenant_id,
                "workspace": workspace_id,
                "runtime": runtime_instance_id,
                "workload": "3" * 64,
                "actor": actor_id,
                "resource": derived_resource_id,
                "constraints": '{"timeout_ms":2000}',
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.operations "
                "(id, tenant_id, workspace_id, actor_type, actor_id, resource_id, "
                "resource_version, request_hash, kind, state, risk_level, progress, "
                "attempt_count, version) VALUES "
                "(:id, :tenant, :workspace, 'system', :actor, :resource, 1, :hash, "
                "'rag.derived.create', 'queued', 'R1', 0, 0, 1)"
            ),
            {
                "id": operation_id,
                "tenant": tenant_id,
                "workspace": workspace_id,
                "actor": actor_id,
                "resource": derived_resource_id,
                "hash": "f" * 64,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_data_effects "
                "(tenant_id, workspace_id, resource_id, operation_id, sequence, "
                "effect_kind, binding_digest, state, version) VALUES "
                "(:tenant, :workspace, :resource, :operation, 1, 'derived_build', "
                ":digest, 'pending', 1)"
            ),
            {
                "tenant": tenant_id,
                "workspace": workspace_id,
                "resource": derived_resource_id,
                "operation": operation_id,
                "digest": "1" * 64,
            },
        )
        assert (
            connection.execute(
                text(
                    "SELECT effect_kind FROM omnibase_meta.workspace_data_effects "
                    "WHERE operation_id = :operation"
                ),
                {"operation": operation_id},
            ).scalar_one()
            == "derived_build"
        )
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.workspace_data_effects "
                    "(tenant_id, workspace_id, resource_id, operation_id, sequence, "
                    "effect_kind, binding_digest, state, version) VALUES "
                    "(:tenant, :workspace, :resource, :operation, 2, "
                    "'derived_index_build', :digest, 'pending', 1)"
                ),
                {
                    "tenant": tenant_id,
                    "workspace": workspace_id,
                    "resource": derived_resource_id,
                    "operation": operation_id,
                    "digest": "2" * 64,
                },
            )
        savepoint.rollback()
        savepoint = connection.begin_nested()
        with pytest.raises(DBAPIError):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.workspace_data_usage_reservations "
                    "(operation_id, tenant_id, grant_id, grant_version, workspace_id, "
                    "runtime_instance_id, workload_identity_digest, action, resource_id, "
                    "resource_version, request_hash, calls, bytes_in, bytes_out_reserved, "
                    "cost_units, state, result_digest) VALUES "
                    "(:operation, :tenant, :grant, 1, :workspace, :runtime, :workload, "
                    "'rag.derived.create', :resource, 1, :hash, 1, 1, 1, 1, "
                    "'pending', :result)"
                ),
                {
                    "operation": operation_id,
                    "tenant": tenant_id,
                    "grant": grant_id,
                    "workspace": workspace_id,
                    "runtime": runtime_instance_id,
                    "workload": "3" * 64,
                    "resource": derived_resource_id,
                    "hash": "4" * 64,
                    "result": "5" * 64,
                },
            )
        savepoint.rollback()
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_data_usage_reservations "
                "(operation_id, tenant_id, grant_id, grant_version, workspace_id, "
                "runtime_instance_id, workload_identity_digest, action, resource_id, "
                "resource_version, request_hash, calls, bytes_in, bytes_out_reserved, "
                "cost_units, state, result_digest) VALUES "
                "(:operation, :tenant, :grant, 1, :workspace, :runtime, :workload, "
                "'rag.derived.create', :resource, 1, :hash, 1, 1, 1, 1, "
                "'pending', NULL)"
            ),
            {
                "operation": operation_id,
                "tenant": tenant_id,
                "grant": grant_id,
                "workspace": workspace_id,
                "runtime": runtime_instance_id,
                "workload": "3" * 64,
                "resource": derived_resource_id,
                "hash": "4" * 64,
            },
        )
        connection.execute(
            text(
                "UPDATE omnibase_meta.workspace_data_usage_reservations "
                "SET state = 'committed', result_digest = :result "
                "WHERE operation_id = :operation"
            ),
            {"operation": operation_id, "result": "5" * 64},
        )
        assert (
            connection.execute(
                text(
                    "SELECT state FROM omnibase_meta.workspace_data_usage_reservations "
                    "WHERE operation_id = :operation"
                ),
                {"operation": operation_id},
            ).scalar_one()
            == "committed"
        )

    downgrade = _run_alembic("downgrade", "0008")
    assert downgrade.returncode != 0
    assert "refusing populated P34.6 global downgrade" in downgrade.stdout + downgrade.stderr
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
            == "0016"
        )

    migration_source = (
        _BACKEND_ROOT
        / "src"
        / "omnibase"
        / "migrations"
        / "versions"
        / "0009_p34_6_workspace_data.py"
    ).read_text(encoding="utf-8")
    assert "refusing populated P34.6 global downgrade" in migration_source
    assert "refusing populated P34.6 tenant downgrade" in migration_source
    assert "unsupported migration_schema_scope" in migration_source


def test_0009_lineage_trigger_serializes_concurrent_reverse_edges(db_engine) -> None:
    upgrade = _run_alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr

    suffix = uuid.uuid4().hex[:10]
    tenant_id = str(uuid.uuid4())
    tenant_schema = f"tenant_{suffix}"
    source_id = str(uuid.uuid4())
    derived_id = str(uuid.uuid4())
    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{tenant_schema}"'))
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id, name, slug, schema_name, is_default, is_active) "
                "VALUES (:id, 'P34.6 lineage concurrency', :slug, :schema, FALSE, TRUE)"
            ),
            {"id": tenant_id, "slug": f"p346-lineage-{suffix}", "schema": tenant_schema},
        )
    tenant_upgrade = _run_alembic("upgrade", "head")
    assert tenant_upgrade.returncode == 0, tenant_upgrade.stdout + tenant_upgrade.stderr
    with db_engine.begin() as connection:
        for resource_id, display_name in (
            (source_id, "lineage source"),
            (derived_id, "lineage derived"),
        ):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.resource_registry "
                    "(id, tenant_id, kind, owner_type, display_name, state, "
                    "policy_class) VALUES "
                    "(:id, :tenant, 'artifact', 'system', :name, 'active', "
                    "'tenant_managed')"
                ),
                {"id": resource_id, "tenant": tenant_id, "name": display_name},
            )

    insert_lineage = text(
        "INSERT INTO omnibase_meta.resource_lineage "
        "(tenant_id, source_resource_id, derived_resource_id, relation, source_version) "
        "VALUES (:tenant, :source, :derived, 'derived_from', 1)"
    )

    def insert_reverse() -> None:
        with db_engine.begin() as connection:
            connection.execute(
                insert_lineage,
                {"tenant": tenant_id, "source": derived_id, "derived": source_id},
            )

    first_connection = db_engine.connect()
    first_transaction = first_connection.begin()
    try:
        first_connection.execute(
            insert_lineage,
            {"tenant": tenant_id, "source": source_id, "derived": derived_id},
        )
        with ThreadPoolExecutor(max_workers=1) as pool:
            reverse = pool.submit(insert_reverse)
            assert not reverse.done()
            first_transaction.commit()
            with pytest.raises(DBAPIError):
                reverse.result(timeout=10)
    finally:
        if first_transaction.is_active:
            first_transaction.rollback()
        first_connection.close()
