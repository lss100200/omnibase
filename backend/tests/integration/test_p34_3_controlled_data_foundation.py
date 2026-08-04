"""Isolated PostgreSQL acceptance tests for the P34.3 foundation.

The suite is inert unless the shared integration sentinel accepts an explicit
isolated database and restricted non-owner role.  It never targets the normal
business database.
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
from sqlalchemy.exc import IntegrityError

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.3 integration tests require OMNIBASE_INTEGRATION_TESTS=1",
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


def _upgrade_head() -> None:
    result = _run_alembic("upgrade", "head")
    assert result.returncode == 0, result.stdout + result.stderr


def _create_retained_test_tenant(db_engine, run_owned_resources) -> tuple[str, str]:
    tenant_id = str(uuid.uuid4())
    suffix = uuid.uuid4().hex[:8]
    schema_name = f"tenant_{suffix}"
    with db_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.tenants "
                "(id, name, slug, schema_name, is_default, is_active) "
                "VALUES (:id, :name, :slug, :schema, FALSE, TRUE)"
            ),
            {
                "id": tenant_id,
                "name": "P34.3 isolated tenant",
                "slug": f"p343-{suffix}",
                "schema": schema_name,
            },
        )
    run_owned_resources.add(tenant_id, schema_name)
    return tenant_id, schema_name


def test_0006_creates_global_foundation_and_only_tenant_payload_table(
    db_engine,
    run_owned_resources,
) -> None:
    _upgrade_head()
    _, schema_name = _create_retained_test_tenant(db_engine, run_owned_resources)
    _upgrade_head()

    with db_engine.connect() as connection:
        global_revision = connection.execute(
            text("SELECT version_num FROM omnibase_meta.alembic_version")
        ).scalar_one()
        tenant_revision = connection.execute(
            text(
                f'SELECT version_num FROM "{schema_name}".alembic_version'  # noqa: S608
            )
        ).scalar_one()
        global_tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'omnibase_meta'"
                )
            ).scalars()
        )
        tenant_tables = set(
            connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema"
                ),
                {"schema": schema_name},
            ).scalars()
        )

    # Later global-only revisions still advance both scoped revision ledgers
    # to the repository head after their tenant no-op.
    assert global_revision == "0010"
    assert tenant_revision == "0010"
    assert {
        "data_table_bindings",
        "data_column_bindings",
        "data_index_bindings",
        "schema_change_plans",
        "operation_dispatch_outbox",
        "operation_compensations",
        "authorization_contexts",
    } <= global_tables
    assert "controlled_data_operation_payloads" in tenant_tables
    assert not any(name.startswith(("odt_", "odc_", "odi_")) for name in tenant_tables)


def test_0006_empty_downgrade_and_reupgrade_are_safe(db_engine) -> None:
    _upgrade_head()

    downgrade = _run_alembic("downgrade", "0005")
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
            == "0005"
        )
        assert (
            connection.execute(
                text("SELECT to_regclass('omnibase_meta.data_table_bindings')")
            ).scalar_one_or_none()
            is None
        )

    _upgrade_head()
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
            == "0010"
        )


def test_database_rejects_forbidden_policy_physical_name_type_and_auth_source(
    db_engine,
    run_owned_resources,
) -> None:
    _upgrade_head()
    tenant_id, tenant_schema = _create_retained_test_tenant(
        db_engine,
        run_owned_resources,
    )
    other_tenant_id, _ = _create_retained_test_tenant(
        db_engine,
        run_owned_resources,
    )
    _upgrade_head()
    connection = db_engine.connect()
    transaction = connection.begin()
    try:
        workspace_id = str(uuid.uuid4())
        workspace_template_id = str(uuid.uuid4())
        actor_id = str(uuid.uuid4())
        connection.execute(
            text(
                f'INSERT INTO "{tenant_schema}".users '  # noqa: S608
                "(id, email, password_hash, is_tenant_admin, is_active) "
                "VALUES (:id, :email, :password_hash, TRUE, TRUE)"
            ),
            {
                "id": actor_id,
                "email": f"p343-foundation-{uuid.uuid4().hex[:8]}@example.invalid",
                "password_hash": "integration-test-not-a-real-password-hash",
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(id, tenant_id, kind, owner_type, owner_id, display_name, state, "
                "version, policy_class) VALUES (:id, :tenant, 'workspace', "
                "'workspace', :id, 'P34.3 foundation workspace', 'active', 1, "
                "'workspace_private')"
            ),
            {"id": workspace_id, "tenant": tenant_id},
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_templates "
                "(id, tenant_id, template_key, version, display_name, digest, "
                "template_spec, state, created_by_user_id) VALUES "
                "(:id, :tenant, :key, 1, 'P34.3 foundation template', :digest, "
                "'{}'::jsonb, 'active', :actor)"
            ),
            {
                "id": workspace_template_id,
                "tenant": tenant_id,
                "key": f"p343-foundation-{uuid.uuid4().hex[:8]}",
                "digest": "d" * 64,
                "actor": actor_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspaces "
                "(id, tenant_id, template_id, owner_user_id, display_name, "
                "desired_state, observed_state, generation, version, quota) VALUES "
                "(:id, :tenant, :template, :actor, 'P34.3 foundation workspace', "
                "'running', 'running', 1, 1, '{}'::jsonb)"
            ),
            {
                "id": workspace_id,
                "tenant": tenant_id,
                "template": workspace_template_id,
                "actor": actor_id,
            },
        )
        resource_id = connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(tenant_id, kind, owner_type, owner_id, display_name, policy_class) "
                "VALUES (:tenant, 'controlled_table', 'workspace', :workspace, "
                "'Controlled', "
                "'workspace_private') RETURNING id"
            ),
            {"tenant": tenant_id, "workspace": workspace_id},
        ).scalar_one()

        insert_binding = text(
            "INSERT INTO omnibase_meta.data_table_bindings "
            "(tenant_id, resource_id, workspace_id, display_name, policy_class, "
            "physical_table_name, created_by_actor_id) "
            "VALUES (:tenant, :resource, :workspace, 'Controlled', :policy, "
            ":physical, :actor) RETURNING id"
        )
        values = {
            "tenant": tenant_id,
            "resource": resource_id,
            "workspace": workspace_id,
            "policy": "workspace_private",
            "physical": f"odt_{uuid.uuid4().hex}",
            "actor": actor_id,
        }
        table_binding_id = connection.execute(insert_binding, values).scalar_one()

        with (
            pytest.raises(
                IntegrityError,
                match="data_table_bindings_physical_name_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "UPDATE omnibase_meta.data_table_bindings "
                    "SET physical_table_name = 'users' WHERE id = :binding"
                ),
                {"binding": table_binding_id},
            )

        with (
            pytest.raises(
                IntegrityError,
                match="data_table_bindings_policy_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                insert_binding,
                {
                    **values,
                    "resource": str(uuid.uuid4()),
                    "policy": "canonical_readonly",
                    "physical": f"odt_{uuid.uuid4().hex}",
                },
            )

        with (
            pytest.raises(
                IntegrityError,
                match=r"data_column_bindings_(?:type|type_args)_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.data_column_bindings "
                    "(tenant_id, table_binding_id, display_name, physical_column_name, "
                    "data_type, type_args, nullable, ordinal) "
                    "VALUES (:tenant, :table, 'Unsafe', :physical, 'jsonb', "
                    "'{}'::jsonb, TRUE, 1)"
                ),
                {
                    "tenant": tenant_id,
                    "table": table_binding_id,
                    "physical": f"odc_{uuid.uuid4().hex}",
                },
            )

        with (
            pytest.raises(
                IntegrityError,
                match="data_column_bindings_table_tenant_fk",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "INSERT INTO omnibase_meta.data_column_bindings "
                    "(tenant_id, table_binding_id, display_name, physical_column_name, "
                    "data_type, type_args, nullable, ordinal) "
                    "VALUES (:tenant, :table, 'Cross tenant', :physical, 'uuid', "
                    "'{}'::jsonb, TRUE, 1)"
                ),
                {
                    "tenant": other_tenant_id,
                    "table": table_binding_id,
                    "physical": f"odc_{uuid.uuid4().hex}",
                },
            )

        auth_insert = text(
            "INSERT INTO omnibase_meta.authorization_contexts "
            "(tenant_id, source, actor_user_id, grant_id, actions, resource_ids, "
            "source_version, snapshot_hash, expires_at) "
            "VALUES (:tenant, :source, :actor, :grant, :actions, :resources, 1, "
            ":hash, :expires)"
        )
        with (
            pytest.raises(
                IntegrityError,
                match="authorization_contexts_source_binding_check",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                auth_insert,
                {
                    "tenant": tenant_id,
                    "source": "user_rbac",
                    "actor": str(uuid.uuid4()),
                    "grant": str(uuid.uuid4()),
                    "actions": ["data.schema.apply"],
                    "resources": [str(resource_id)],
                    "hash": "a" * 64,
                    "expires": datetime.now(UTC) + timedelta(minutes=5),
                },
            )
    finally:
        transaction.rollback()
        connection.close()


def test_0006_downgrade_refuses_live_controlled_resources(
    db_engine,
    run_owned_resources,
) -> None:
    _upgrade_head()
    tenant_id, _ = _create_retained_test_tenant(db_engine, run_owned_resources)
    with db_engine.begin() as connection:
        resource_id = connection.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry "
                "(tenant_id, kind, owner_type, display_name, policy_class) "
                "VALUES (:tenant, 'controlled_table', 'system', 'Retained', "
                "'tenant_managed') RETURNING id"
            ),
            {"tenant": tenant_id},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.data_table_bindings "
                "(tenant_id, resource_id, display_name, policy_class, "
                "physical_table_name, created_by_actor_id) "
                "VALUES (:tenant, :resource, 'Retained', 'tenant_managed', "
                ":physical, :actor)"
            ),
            {
                "tenant": tenant_id,
                "resource": resource_id,
                "physical": f"odt_{uuid.uuid4().hex}",
                "actor": str(uuid.uuid4()),
            },
        )

    downgrade = _run_alembic("downgrade", "0005")
    assert downgrade.returncode != 0
    assert "downgrade refused" in (downgrade.stdout + downgrade.stderr)
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
            == "0010"
        )
