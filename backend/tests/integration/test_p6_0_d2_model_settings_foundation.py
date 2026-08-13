"""Guarded PostgreSQL attacks for P6.0-D2 role model settings.

This module may run only against the repository's explicit ``omnibase_test_*``
sentinel database.  It exercises the real Alembic 0016 tenant DDL, downgrade
preflight and service concurrency boundaries without contacting a real model
provider or a non-test database.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from contextlib import contextmanager
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any

import pytest
from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnibase.db.models import GLOBAL_METADATA, TENANT_METADATA
from omnibase.user_settings.model_settings import AgentModelSettingsService
from omnibase.user_settings.schemas import AgentModelSettingWrite
from omnibase.user_settings.service import UserSettingsError, UserSettingsNotFound
from tests.integration.test_p5_1b_agent_registry_foundation import (
    ACTOR_ID,
    _binding_dto,
    _binding_mapping,
    _install,
    _run_alembic,
    _seed_definition_version,
    _session,
    _tenant_schema,
    _tenant_with_schema,
    _upgrade_head,
)

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P6.0-D2 integration tests require OMNIBASE_INTEGRATION_TESTS=1",
        allow_module_level=True,
    )

pytestmark = pytest.mark.integration

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_TABLE = "workspace_agent_model_overrides"


@pytest.fixture(scope="module", autouse=True)
def p60d2_schema(db_engine) -> None:  # type: ignore[no-untyped-def]
    del db_engine
    _upgrade_head()


def _tenant_head(connection: Any, schema_name: str) -> str:
    return str(
        connection.execute(
            text(f'SELECT version_num FROM "{schema_name}".alembic_version')  # noqa: S608
        ).scalar_one()
    )


def _migrate_one_tenant(
    connection: Any,
    *,
    schema_name: str,
    destination: str,
    upgrade: bool,
) -> None:
    """Run one tenant migration inside the caller-owned sentinel transaction."""
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_BACKEND_ROOT / "src" / "omnibase" / "migrations")
    )
    config.attributes["migration_schema_scope"] = "tenant"
    script = ScriptDirectory.from_config(config)

    def migration_steps(revision: str, _context: Any) -> list[Any]:
        if upgrade:
            return script._upgrade_revs(destination, revision)
        return script._downgrade_revs(destination, revision)

    connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", omnibase_meta, public'))
    with EnvironmentContext(
        config,
        script,
        fn=migration_steps,
        destination_rev=destination,
    ) as environment:
        environment.configure(
            connection=connection,
            target_metadata=TENANT_METADATA,
            version_table_schema=schema_name,
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
        )
        with environment.begin_transaction():
            environment.run_migrations()


def _migrate_global_only(
    connection: Any,
    *,
    destination: str,
    upgrade: bool,
) -> None:
    """Run only the global phase to attack tenant-first ordering guards."""
    config = Config(str(_BACKEND_ROOT / "alembic.ini"))
    config.set_main_option(
        "script_location", str(_BACKEND_ROOT / "src" / "omnibase" / "migrations")
    )
    config.attributes["migration_schema_scope"] = "global"
    script = ScriptDirectory.from_config(config)

    def migration_steps(revision: str, _context: Any) -> list[Any]:
        if upgrade:
            return script._upgrade_revs(destination, revision)
        return script._downgrade_revs(destination, revision)

    connection.execute(text("SET LOCAL search_path TO omnibase_meta, public"))
    with EnvironmentContext(
        config,
        script,
        fn=migration_steps,
        destination_rev=destination,
    ) as environment:
        environment.configure(
            connection=connection,
            target_metadata=GLOBAL_METADATA,
            version_table_schema="omnibase_meta",
            compare_type=True,
            compare_server_default=True,
            include_schemas=True,
        )
        with environment.begin_transaction():
            environment.run_migrations()


def _insert_user(connection: Any, schema_name: str, *, label: str) -> str:
    user_id = str(uuid.uuid4())
    connection.execute(
        text(
            f'INSERT INTO "{schema_name}".users '  # noqa: S608
            "(id, email, password_hash, is_tenant_admin, is_active) "
            "VALUES (:id, :email, 'not-a-real-hash', TRUE, TRUE)"
        ),
        {"id": user_id, "email": f"{label}-{uuid.uuid4().hex[:8]}@example.invalid"},
    )
    return user_id


def _insert_credential(
    connection: Any,
    schema_name: str,
    *,
    user_id: str,
    label: str,
    default: bool = False,
) -> str:
    credential_id = str(uuid.uuid4())
    connection.execute(
        text(
            f'INSERT INTO "{schema_name}".model_provider_credentials '  # noqa: S608
            "(id, user_id, display_name, provider_id, base_url, model_id, "
            "encrypted_api_key, key_nonce, key_version, key_fingerprint, "
            "is_default, is_active, version, last_test_status) "
            "VALUES (:id, :user, :label, 'test-provider', 'https://example.invalid/v1', "
            "'deepseek-v4-flash', decode('aa', 'hex'), decode('000000000000000000000000', "
            "'hex'), 1, 'test-fingerprint', :default, TRUE, 1, 'passed')"
        ),
        {
            "id": credential_id,
            "user": user_id,
            "label": label,
            "default": default,
        },
    )
    return credential_id


def _delete_test_tenant(connection: Any, *, tenant_id: str, schema_name: str) -> None:
    """Remove one exact unaudited sentinel tenant after local migration tests."""
    connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
    deleted = connection.execute(
        text("DELETE FROM omnibase_meta.tenants " "WHERE id = :tenant AND schema_name = :schema"),
        {"tenant": tenant_id, "schema": schema_name},
    )
    assert deleted.rowcount == 1


def _insert_override(
    connection: Any,
    schema_name: str,
    *,
    user_id: str,
    credential_id: str | None,
    workspace_id: str | None = None,
    agent_version_id: str | None = None,
    role: str = "backend",
) -> str:
    override_id = str(uuid.uuid4())
    connection.execute(
        text(
            f'INSERT INTO "{schema_name}".{_TABLE} '  # noqa: S608
            "(id, user_id, workspace_id, agent_version_id, employee_role_id, "
            "credential_id, model_id, version) "
            "VALUES (:id, :user, :workspace, :version_id, :role, :credential, "
            "'deepseek-v4-flash', 1)"
        ),
        {
            "id": override_id,
            "user": user_id,
            "workspace": workspace_id or str(uuid.uuid4()),
            "version_id": agent_version_id or str(uuid.uuid4()),
            "role": role,
            "credential": credential_id,
        },
    )
    return override_id


def _live_scope(db_engine: Any, run_owned_resources: Any, label: str) -> dict[str, str]:
    tenant_id, workspace_id, version = _seed_definition_version(
        db_engine,
        run_owned_resources,
        label,
    )
    schema_name: str
    with db_engine.begin() as connection:
        schema_name = _tenant_schema(connection, tenant_id)
        connection.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_memberships "
                "(tenant_id, workspace_id, user_id, role, state, created_by_user_id) "
                "VALUES (:tenant, :workspace, :user, 'owner', 'active', :user)"
            ),
            {"tenant": tenant_id, "workspace": workspace_id, "user": ACTOR_ID},
        )
        credential_id = _insert_credential(
            connection,
            schema_name,
            user_id=ACTOR_ID,
            label=f"{label} default",
            default=True,
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
        _install(session, tenant_id=tenant_id, binding=binding, key=uuid.uuid4().hex)
        session.commit()
    return {
        "tenant_id": tenant_id,
        "schema_name": schema_name,
        "workspace_id": workspace_id,
        "agent_version_id": version.agent_version_id,
        "credential_id": credential_id,
    }


@contextmanager
def _tenant_session(db_engine: Any, scope: dict[str, str]) -> Iterator[Session]:
    session = _session(db_engine, scope["tenant_id"])
    try:
        yield session
    finally:
        session.close()


def _put(
    session: Session,
    scope: dict[str, str],
    *,
    expected_version: int,
    role: str = "backend",
) -> None:
    AgentModelSettingsService().put_setting(
        session,
        tenant_id=scope["tenant_id"],
        user_id=ACTOR_ID,
        workspace_id=scope["workspace_id"],
        agent_version_id=scope["agent_version_id"],
        employee_role_id=role,  # type: ignore[arg-type]
        payload=AgentModelSettingWrite(
            inherit_default=False,
            provider_credential_id=scope["credential_id"],
            requested_model_id="deepseek-v4-flash",
            expected_version=expected_version,
        ),
        request_id=str(uuid.uuid4()),
    )


def test_0016_fresh_tenant_upgrade_has_reviewed_shape_and_empty_round_trip(
    db_engine,
    run_owned_resources,
) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "p60d2-shape")
    with db_engine.begin() as connection:
        schema_name = _tenant_schema(connection, tenant_id)
        assert (
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
            == "0016"
        )
        assert _tenant_head(connection, schema_name) == "0016"
        inspector = inspect(connection)
        columns = {column["name"] for column in inspector.get_columns(_TABLE, schema=schema_name)}
        indexes = {
            index["name"]: index for index in inspector.get_indexes(_TABLE, schema=schema_name)
        }
        foreign_keys = {
            foreign_key["name"]: foreign_key
            for foreign_key in inspector.get_foreign_keys(_TABLE, schema=schema_name)
        }
        credential_uniques = {
            constraint["name"]: constraint
            for constraint in inspector.get_unique_constraints(
                "model_provider_credentials",
                schema=schema_name,
            )
        }
        constraints = {
            row[0]
            for row in connection.execute(
                text(
                    "SELECT conname FROM pg_constraint c "
                    "JOIN pg_class t ON t.oid = c.conrelid "
                    "JOIN pg_namespace n ON n.oid = t.relnamespace "
                    "WHERE n.nspname = :schema AND t.relname = :table"
                ),
                {"schema": schema_name, "table": _TABLE},
            )
        }
    assert {
        "id",
        "user_id",
        "workspace_id",
        "agent_version_id",
        "employee_role_id",
        "credential_id",
        "model_id",
        "family_override",
        "last_test_status",
        "last_tested_at",
        "tested_configuration_digest",
        "version",
        "created_at",
        "updated_at",
    } == columns
    assert {"api_key", "encrypted_api_key", "key_nonce"}.isdisjoint(columns)
    assert indexes["workspace_agent_model_overrides_scope_uq"]["unique"] is True
    assert "workspace_agent_model_overrides_credential_idx" in indexes
    assert foreign_keys["workspace_agent_model_overrides_credential_user_fk"][
        "constrained_columns"
    ] == ["credential_id", "user_id"]
    assert foreign_keys["workspace_agent_model_overrides_credential_user_fk"][
        "referred_columns"
    ] == ["id", "user_id"]
    assert "model_provider_credentials_id_user_uq" in credential_uniques
    assert {
        "workspace_agent_model_overrides_role_check",
        "workspace_agent_model_overrides_selection_check",
        "workspace_agent_model_overrides_family_check",
        "workspace_agent_model_overrides_test_status_check",
        "workspace_agent_model_overrides_test_digest_check",
        "workspace_agent_model_overrides_credential_user_fk",
    }.issubset(constraints)

    with db_engine.begin() as connection:
        _migrate_one_tenant(
            connection,
            schema_name=schema_name,
            destination="0015",
            upgrade=False,
        )
        assert _tenant_head(connection, schema_name) == "0015"
        assert (
            connection.execute(
                text("SELECT to_regclass(:name)"),
                {"name": f"{schema_name}.{_TABLE}"},
            ).scalar_one_or_none()
            is None
        )
        _migrate_one_tenant(
            connection,
            schema_name=schema_name,
            destination="0016",
            upgrade=True,
        )
        assert _tenant_head(connection, schema_name) == "0016"
        _delete_test_tenant(
            connection,
            tenant_id=tenant_id,
            schema_name=schema_name,
        )
    run_owned_resources.tenants.pop(str(tenant_id), None)


def test_0016_database_constraints_reject_cross_user_credential_and_bad_values(
    db_engine,
    run_owned_resources,
) -> None:
    tenant_id = _tenant_with_schema(db_engine, run_owned_resources, "p60d2-constraints")
    with db_engine.begin() as connection:
        schema_name = _tenant_schema(connection, tenant_id)
        user_a = _insert_user(connection, schema_name, label="credential-owner")
        user_b = _insert_user(connection, schema_name, label="override-owner")
        credential_id = _insert_credential(
            connection,
            schema_name,
            user_id=user_a,
            label="foreign credential",
        )

    with pytest.raises(IntegrityError) as cross_user, db_engine.begin() as connection:
        _insert_override(
            connection,
            schema_name,
            user_id=user_b,
            credential_id=credential_id,
        )
    assert "workspace_agent_model_overrides_credential_user_fk" in str(cross_user.value)

    with pytest.raises(IntegrityError) as missing_selection, db_engine.begin() as connection:
        connection.execute(
            text(
                f'INSERT INTO "{schema_name}".{_TABLE} '  # noqa: S608
                "(user_id, workspace_id, agent_version_id, employee_role_id, version) "
                "VALUES (:user, :workspace, :agent, 'backend', 1)"
            ),
            {
                "user": user_a,
                "workspace": str(uuid.uuid4()),
                "agent": str(uuid.uuid4()),
            },
        )
    assert "workspace_agent_model_overrides_selection_check" in str(missing_selection.value)

    workspace_id = str(uuid.uuid4())
    agent_version_id = str(uuid.uuid4())
    with db_engine.begin() as connection:
        _insert_override(
            connection,
            schema_name,
            user_id=user_a,
            credential_id=credential_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
        )
    with pytest.raises(IntegrityError) as duplicate_scope, db_engine.begin() as connection:
        _insert_override(
            connection,
            schema_name,
            user_id=user_a,
            credential_id=credential_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
        )
    assert "workspace_agent_model_overrides_scope_uq" in str(duplicate_scope.value)

    for field, value, constraint in (
        ("employee_role_id", "everyone", "workspace_agent_model_overrides_role_check"),
        ("family_override", "magic", "workspace_agent_model_overrides_family_check"),
        ("last_test_status", "trusted", "workspace_agent_model_overrides_test_status_check"),
        ("tested_configuration_digest", "ABC", "workspace_agent_model_overrides_test_digest_check"),
    ):
        with db_engine.begin() as connection:
            override_id = _insert_override(
                connection,
                schema_name,
                user_id=user_a,
                credential_id=credential_id,
            )
        with pytest.raises(IntegrityError) as invalid, db_engine.begin() as connection:
            connection.execute(
                text(
                    f'UPDATE "{schema_name}".{_TABLE} SET "{field}" = :value '  # noqa: S608
                    "WHERE id = :id"
                ),
                {"id": override_id, "value": value},
            )
        assert constraint in str(invalid.value)


def test_expected_version_zero_serializes_concurrent_first_create(
    db_engine,
    run_owned_resources,
) -> None:
    scope = _live_scope(db_engine, run_owned_resources, "p60d2-concurrent-create")
    first_created = Event()
    release_first = Event()

    def first() -> str:
        with _tenant_session(db_engine, scope) as session:
            _put(session, scope, expected_version=0)
            first_created.set()
            assert release_first.wait(timeout=10)
            session.commit()
            return "created"

    def competing() -> str:
        with _tenant_session(db_engine, scope) as session:
            try:
                _put(session, scope, expected_version=0)
                session.commit()
                return "unexpected-success"
            except UserSettingsError as exc:
                session.rollback()
                return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        assert first_created.wait(timeout=10)
        competing_future = pool.submit(competing)
        with pytest.raises(FutureTimeout):
            competing_future.result(timeout=0.2)
        release_first.set()
        assert first_future.result(timeout=10) == "created"
        assert competing_future.result(timeout=10) == "agent_model_setting_version_conflict"

    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text(
                    f'SELECT count(*) FROM "{scope["schema_name"]}".{_TABLE} '  # noqa: S608
                    "WHERE user_id = :user AND workspace_id = :workspace "
                    "AND agent_version_id = :agent AND employee_role_id = 'backend'"
                ),
                {
                    "user": ACTOR_ID,
                    "workspace": scope["workspace_id"],
                    "agent": scope["agent_version_id"],
                },
            ).scalar_one()
            == 1
        )


def test_stale_update_and_delete_are_rejected(
    db_engine,
    run_owned_resources,
) -> None:
    scope = _live_scope(db_engine, run_owned_resources, "p60d2-stale")
    with _tenant_session(db_engine, scope) as session:
        _put(session, scope, expected_version=0)
        session.commit()
    with _tenant_session(db_engine, scope) as session:
        _put(session, scope, expected_version=1)
        session.commit()

    with _tenant_session(db_engine, scope) as session:
        with pytest.raises(UserSettingsError, match="agent_model_setting_version_conflict"):
            _put(session, scope, expected_version=1)
        session.rollback()
    with _tenant_session(db_engine, scope) as session:
        with pytest.raises(UserSettingsError, match="agent_model_setting_version_conflict"):
            AgentModelSettingsService().delete_setting(
                session,
                tenant_id=scope["tenant_id"],
                user_id=ACTOR_ID,
                workspace_id=scope["workspace_id"],
                agent_version_id=scope["agent_version_id"],
                employee_role_id="backend",
                expected_version=1,
                request_id=str(uuid.uuid4()),
            )
        session.rollback()


class _ProbeResponse:
    status_code = 200
    is_success = True

    def __init__(self, model_id: str) -> None:
        self._model_id = model_id

    def json(self) -> dict[str, str]:
        return {"model": self._model_id}


class _BlockingClient:
    def __init__(self, started: Event, release: Event, model_id: str, **_kwargs: Any) -> None:
        self._started = started
        self._release = release
        self._model_id = model_id

    def __enter__(self) -> _BlockingClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def post(self, *_args: object, **_kwargs: object) -> _ProbeResponse:
        self._started.set()
        assert self._release.wait(timeout=10)
        return _ProbeResponse(self._model_id)


def _start_blocked_probe(
    monkeypatch: pytest.MonkeyPatch,
    db_engine: Any,
    scope: dict[str, str],
) -> tuple[Event, Event, Future[str], ThreadPoolExecutor]:
    from omnibase.user_settings import model_settings as model_settings_module

    started = Event()
    release = Event()
    monkeypatch.setattr(
        model_settings_module,
        "validate_provider_base_url",
        lambda *_args, **_kwargs: "https://example.invalid/v1",
    )
    monkeypatch.setattr(
        model_settings_module.CredentialCipher,
        "from_settings",
        classmethod(lambda cls, settings: SimpleNamespace(decrypt=lambda *a, **kw: "test-key")),
    )
    monkeypatch.setattr(
        model_settings_module.httpx,
        "Client",
        lambda **kwargs: _BlockingClient(started, release, "deepseek-v4-flash", **kwargs),
    )

    def probe() -> str:
        from omnibase.core.config import get_settings

        with _tenant_session(db_engine, scope) as session:
            try:
                AgentModelSettingsService().test_setting(
                    session,
                    settings=get_settings(),
                    tenant_id=scope["tenant_id"],
                    user_id=ACTOR_ID,
                    workspace_id=scope["workspace_id"],
                    agent_version_id=scope["agent_version_id"],
                    employee_role_id="backend",
                    request_id=str(uuid.uuid4()),
                )
                session.commit()
                return "unexpected-success"
            except (UserSettingsError, UserSettingsNotFound) as exc:
                session.rollback()
                return str(exc)

    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(probe)
    return started, release, future, pool


def _finish_probe(
    release: Event,
    future: Future[str],
    pool: ThreadPoolExecutor,
) -> str:
    release.set()
    try:
        return str(future.result(timeout=10))
    finally:
        pool.shutdown(wait=True)


def test_delete_and_same_value_recreate_during_probe_invalidates_result(
    monkeypatch,
    db_engine,
    run_owned_resources,
) -> None:
    scope = _live_scope(db_engine, run_owned_resources, "p60d2-probe-recreate")
    with _tenant_session(db_engine, scope) as session:
        _put(session, scope, expected_version=0)
        session.commit()

    started, release, future, pool = _start_blocked_probe(monkeypatch, db_engine, scope)
    assert started.wait(timeout=10)
    with _tenant_session(db_engine, scope) as session:
        AgentModelSettingsService().delete_setting(
            session,
            tenant_id=scope["tenant_id"],
            user_id=ACTOR_ID,
            workspace_id=scope["workspace_id"],
            agent_version_id=scope["agent_version_id"],
            employee_role_id="backend",
            expected_version=1,
            request_id=str(uuid.uuid4()),
        )
        session.commit()
    with _tenant_session(db_engine, scope) as session:
        _put(session, scope, expected_version=0)
        session.commit()

    assert _finish_probe(release, future, pool) == "agent_model_setting_changed_during_test"
    with db_engine.connect() as connection:
        status, digest = connection.execute(
            text(
                f'SELECT last_test_status, tested_configuration_digest FROM '  # noqa: S608
                f'"{scope["schema_name"]}".{_TABLE} WHERE user_id = :user '
                "AND workspace_id = :workspace AND agent_version_id = :agent "
                "AND employee_role_id = 'backend'"
            ),
            {
                "user": ACTOR_ID,
                "workspace": scope["workspace_id"],
                "agent": scope["agent_version_id"],
            },
        ).one()
    assert status is None
    assert digest is None


@pytest.mark.parametrize(
    ("drift", "expected"),
    [
        ("membership", "workspace_membership_insufficient"),
        ("binding", "agent_binding_not_live"),
        ("generation", "agent_binding_not_live"),
    ],
)
def test_authority_drift_during_probe_fails_closed(
    monkeypatch,
    db_engine,
    run_owned_resources,
    drift: str,
    expected: str,
) -> None:
    scope = _live_scope(db_engine, run_owned_resources, f"p60d2-probe-{drift}")
    with _tenant_session(db_engine, scope) as session:
        _put(session, scope, expected_version=0)
        session.commit()
    started, release, future, pool = _start_blocked_probe(monkeypatch, db_engine, scope)
    assert started.wait(timeout=10)

    with db_engine.begin() as connection:
        if drift == "membership":
            connection.execute(
                text(
                    "UPDATE omnibase_meta.workspace_memberships SET state = 'suspended' "
                    "WHERE tenant_id = :tenant AND workspace_id = :workspace "
                    "AND user_id = :user AND state = 'active'"
                ),
                {
                    "tenant": scope["tenant_id"],
                    "workspace": scope["workspace_id"],
                    "user": ACTOR_ID,
                },
            )
        elif drift == "binding":
            connection.execute(
                text(
                    "UPDATE omnibase_meta.workspace_agent_bindings "
                    "SET binding_state = 'disabled', disabled_at = now() "
                    "WHERE tenant_id = :tenant AND workspace_id = :workspace "
                    "AND agent_version_id = :agent AND binding_state = 'installed'"
                ),
                {
                    "tenant": scope["tenant_id"],
                    "workspace": scope["workspace_id"],
                    "agent": scope["agent_version_id"],
                },
            )
        else:
            connection.execute(
                text(
                    "UPDATE omnibase_meta.workspaces SET generation = generation + 1 "
                    "WHERE tenant_id = :tenant AND id = :workspace"
                ),
                {"tenant": scope["tenant_id"], "workspace": scope["workspace_id"]},
            )
    assert _finish_probe(release, future, pool) == expected


def test_two_empty_tenants_refuse_global_first_then_complete_tenant_first_downgrade(
    db_engine,
    run_owned_resources,
) -> None:
    first_tenant = _tenant_with_schema(db_engine, run_owned_resources, "p60d2-down-first")
    second_tenant = _tenant_with_schema(db_engine, run_owned_resources, "p60d2-down-second")
    with db_engine.connect() as connection:
        first_schema = _tenant_schema(connection, first_tenant)
        second_schema = _tenant_schema(connection, second_tenant)

    with (
        pytest.raises(RuntimeError, match="every tenant migration head must be exactly 0015"),
        db_engine.begin() as connection,
    ):
        _migrate_global_only(
            connection,
            destination="0015",
            upgrade=False,
        )

    downgrade = _run_alembic("downgrade", "0015")
    assert downgrade.returncode == 0, downgrade.stdout + downgrade.stderr
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
            == "0015"
        )
        for schema_name in (first_schema, second_schema):
            assert _tenant_head(connection, schema_name) == "0015"
            assert (
                connection.execute(
                    text("SELECT to_regclass(:name)"),
                    {"name": f"{schema_name}.{_TABLE}"},
                ).scalar_one_or_none()
                is None
            )
            assert (
                connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_constraint constraint_row "
                        "JOIN pg_class table_row ON table_row.oid = constraint_row.conrelid "
                        "JOIN pg_namespace schema_row ON schema_row.oid = table_row.relnamespace "
                        "WHERE schema_row.nspname = :schema "
                        "AND table_row.relname = 'model_provider_credentials' "
                        "AND constraint_row.conname = 'model_provider_credentials_id_user_uq')"
                    ),
                    {"schema": schema_name},
                ).scalar_one()
                is False
            )

    upgrade = _run_alembic("upgrade", "head")
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    with db_engine.begin() as connection:
        for tenant_id, schema_name in (
            (first_tenant, first_schema),
            (second_tenant, second_schema),
        ):
            assert _tenant_head(connection, schema_name) == "0016"
            _delete_test_tenant(
                connection,
                tenant_id=tenant_id,
                schema_name=schema_name,
            )
    run_owned_resources.tenants.pop(str(first_tenant), None)
    run_owned_resources.tenants.pop(str(second_tenant), None)


def test_populated_tenant_rolls_back_prior_empty_tenant_before_global_head_moves(
    db_engine,
    run_owned_resources,
) -> None:
    empty_tenant = _tenant_with_schema(db_engine, run_owned_resources, "p60d2-down-empty")
    populated_tenant = _tenant_with_schema(
        db_engine,
        run_owned_resources,
        "p60d2-down-populated",
    )
    with db_engine.begin() as connection:
        empty_schema = _tenant_schema(connection, empty_tenant)
        populated_schema = _tenant_schema(connection, populated_tenant)
        user_id = _insert_user(connection, populated_schema, label="downgrade-owner")
        credential_id = _insert_credential(
            connection,
            populated_schema,
            user_id=user_id,
            label="downgrade credential",
        )
        _insert_override(
            connection,
            populated_schema,
            user_id=user_id,
            credential_id=credential_id,
        )

    with (
        pytest.raises(RuntimeError, match="0016 downgrade refused"),
        db_engine.begin() as connection,
    ):
        _migrate_one_tenant(
            connection,
            schema_name=populated_schema,
            destination="0015",
            upgrade=False,
        )

    downgrade = _run_alembic("downgrade", "0015")
    assert downgrade.returncode != 0
    assert "0016 downgrade refused" in (downgrade.stdout + downgrade.stderr)
    with db_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version")
            ).scalar_one()
            == "0016"
        )
        assert _tenant_head(connection, empty_schema) == "0016"
        assert _tenant_head(connection, populated_schema) == "0016"
        assert (
            connection.execute(
                text(
                    f'SELECT count(*) FROM "{populated_schema}".{_TABLE}'  # noqa: S608
                )
            ).scalar_one()
            == 1
        )

    with db_engine.begin() as connection:
        _delete_test_tenant(
            connection,
            tenant_id=empty_tenant,
            schema_name=empty_schema,
        )
        _delete_test_tenant(
            connection,
            tenant_id=populated_tenant,
            schema_name=populated_schema,
        )
    run_owned_resources.tenants.pop(str(empty_tenant), None)
    run_owned_resources.tenants.pop(str(populated_tenant), None)
