from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from omnibase.desktop_local import (
    DESKTOP_APPLICATION_ID,
    DESKTOP_SCHEMA_VERSION,
    DesktopLocalConfig,
    DesktopMigrationError,
    append_audit_event,
    claim_next_runtime_job,
    create_owner,
    create_workspace,
    enqueue_runtime_job,
    finish_runtime_job,
    initialized_database,
    local_health,
    migrate_database,
    open_database,
    start_runtime_job,
)


def _config(tmp_path: Path, *, version: str = "1.0.0") -> DesktopLocalConfig:
    return DesktopLocalConfig(data_root=tmp_path / "OmniBaseData", application_version=version)


def _seed_one_job(connection: sqlite3.Connection) -> None:
    create_owner(connection, "owner-local", "Local Owner")
    create_workspace(connection, "workspace-1", "owner-local", "Personal Workspace")
    enqueue_runtime_job(
        connection,
        job_id="job-1",
        owner_id="owner-local",
        workspace_id="workspace-1",
        job_kind="agent_invoke",
    )


def test_fresh_database_has_hardened_pragmas_strict_schema_and_health(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with initialized_database(config) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5_000
        assert connection.execute("PRAGMA user_version").fetchone()[0] == DESKTOP_SCHEMA_VERSION
        assert connection.execute("PRAGMA application_id").fetchone()[0] == DESKTOP_APPLICATION_ID

        table_sql = {
            row["name"]: row["sql"]
            for row in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type = 'table' AND sql IS NOT NULL"
            )
        }
        for table in (
            "desktop_schema_metadata",
            "desktop_migration_history",
            "owner",
            "workspace",
            "audit_event",
            "runtime_job",
            "provider",
            "workspace_agent",
            "conversation",
            "invocation",
            "message",
            "workspace_agent_role_config",
            "team_run",
            "team_plan_revision",
            "team_assignment",
            "team_node",
            "team_collaboration_request",
            "team_employee_report",
        ):
            assert table_sql[table].rstrip().endswith("STRICT")

        health = local_health(connection)
        assert health.status == "healthy"
        assert health.schema_version == DESKTOP_SCHEMA_VERSION
        assert health.application_version == "1.0.0"
        assert health.application_id == DESKTOP_APPLICATION_ID
        assert health.journal_mode == "wal"
        assert health.foreign_keys is True
        assert health.integrity == "ok"


def test_restart_is_idempotent_and_preserves_application_migration_record(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with initialized_database(config) as first:
        _seed_one_job(first)

    with initialized_database(config) as restarted:
        migration = restarted.execute(
            "SELECT version, migration_id, application_version FROM desktop_migration_history"
        ).fetchall()
        assert [tuple(row) for row in migration] == [
            (1, "desktop_0001", "1.0.0"),
            (2, "desktop_0002_provider_conversation", "1.0.0"),
            (3, "desktop_0003_personal_agent_team", "1.0.0"),
            (4, "desktop_0004_personal_team_runtime", "1.0.0"),
            (5, "desktop_0005_team_node_identity_epochs", "1.0.0"),
        ]
        assert restarted.execute("SELECT COUNT(*) FROM runtime_job").fetchone()[0] == 1
        assert local_health(restarted).status == "healthy"

    upgraded_config = _config(tmp_path, version="1.0.1")
    with initialized_database(upgraded_config) as upgraded:
        assert local_health(upgraded).application_version == "1.0.1"
        # History records the application that applied the migration; metadata
        # separately records the application currently opening the database.
        assert (
            upgraded.execute(
                "SELECT application_version FROM desktop_migration_history WHERE version = 1"
            ).fetchone()[0]
            == "1.0.0"
        )


def test_migration_rolls_back_all_new_ddl_when_any_statement_fails(tmp_path: Path) -> None:
    config = _config(tmp_path)
    connection = open_database(config)
    try:
        connection.execute("CREATE TABLE owner (id TEXT PRIMARY KEY) STRICT")
        with pytest.raises(DesktopMigrationError, match="^desktop_migration_failed$"):
            migrate_database(connection, config)
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        assert "owner" in names
        assert "desktop_schema_metadata" not in names
        assert "desktop_migration_history" not in names
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("PRAGMA application_id").fetchone()[0] == 0
    finally:
        connection.close()


def test_concurrent_first_launch_applies_migration_once(tmp_path: Path) -> None:
    config = _config(tmp_path)

    def initialize(_: int) -> int:
        connection = open_database(config)
        try:
            return migrate_database(connection, config)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        versions = list(executor.map(initialize, (1, 2)))
    assert versions == [DESKTOP_SCHEMA_VERSION, DESKTOP_SCHEMA_VERSION]

    with initialized_database(config) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM desktop_migration_history").fetchone()[0]
            == DESKTOP_SCHEMA_VERSION
        )


def test_foreign_sqlite_application_id_is_never_adopted(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.database_path.parent.mkdir(parents=True)
    foreign = sqlite3.connect(config.database_path)
    try:
        foreign.execute("PRAGMA application_id = 1234")
        foreign.execute("PRAGMA user_version = 1")
    finally:
        foreign.close()

    connection = open_database(config)
    try:
        with pytest.raises(
            DesktopMigrationError, match="^desktop_database_application_id_mismatch$"
        ) as caught:
            migrate_database(connection, config)
        assert str(config.database_path) not in str(caught.value)
    finally:
        connection.close()


def test_concurrent_claim_has_exactly_one_winner(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with initialized_database(config) as connection:
        _seed_one_job(connection)

    def claim(worker: str):  # type: ignore[no-untyped-def]
        connection = open_database(config)
        try:
            return claim_next_runtime_job(connection, claim_owner=worker)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(claim, ("worker-a", "worker-b")))

    winners = [result for result in results if result is not None]
    assert len(winners) == 1
    assert winners[0].id == "job-1"
    assert winners[0].claim_owner in {"worker-a", "worker-b"}

    with initialized_database(config) as connection:
        row = connection.execute(
            "SELECT state, claim_owner, claim_token FROM runtime_job WHERE id = 'job-1'"
        ).fetchone()
        assert row["state"] == "claimed"
        assert row["claim_owner"] == winners[0].claim_owner
        assert row["claim_token"] == winners[0].claim_token


def test_legal_runtime_job_lifecycle_uses_claim_token_fencing(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with initialized_database(config) as connection:
        _seed_one_job(connection)
        claimed = claim_next_runtime_job(connection, claim_owner="worker-a")
        assert claimed is not None
        assert not start_runtime_job(connection, claimed.id, "wrong-token")
        assert start_runtime_job(connection, claimed.id, claimed.claim_token)
        assert not finish_runtime_job(connection, claimed.id, "wrong-token", "succeeded")
        assert finish_runtime_job(connection, claimed.id, claimed.claim_token, "succeeded")
        assert (
            connection.execute(
                "SELECT state FROM runtime_job WHERE id = ?", (claimed.id,)
            ).fetchone()[0]
            == "succeeded"
        )
        with pytest.raises(sqlite3.IntegrityError, match="claim_binding_immutable"):
            connection.execute(
                "UPDATE runtime_job SET claim_token = 'replacement' WHERE id = ?", (claimed.id,)
            )


def test_database_rejects_illegal_job_and_workspace_state_transitions(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with initialized_database(config) as connection:
        _seed_one_job(connection)
        with pytest.raises(sqlite3.IntegrityError, match="state_transition_forbidden"):
            connection.execute(
                "UPDATE runtime_job SET state = 'succeeded', claim_owner = 'worker', "
                "claim_token = 'token', claimed_at = '2026-08-18T00:00:00Z' "
                "WHERE id = 'job-1'"
            )
        connection.execute(
            "UPDATE workspace SET state = 'archived', row_version = row_version + 1 "
            "WHERE id = 'workspace-1'"
        )
        with pytest.raises(sqlite3.IntegrityError, match="state_transition_forbidden"):
            connection.execute("UPDATE workspace SET state = 'active' WHERE id = 'workspace-1'")


def test_audit_events_are_database_enforced_append_only(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with initialized_database(config) as connection:
        _seed_one_job(connection)
        append_audit_event(
            connection,
            event_id="event-1",
            owner_id="owner-local",
            workspace_id="workspace-1",
            event_type="runtime_job_queued",
            payload={"job_id": "job-1"},
        )
        with pytest.raises(sqlite3.IntegrityError, match="desktop_audit_append_only"):
            connection.execute(
                "UPDATE audit_event SET payload_json = '{}' WHERE event_id = 'event-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="desktop_audit_append_only"):
            connection.execute("DELETE FROM audit_event WHERE event_id = 'event-1'")
        assert connection.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0] == 1


def test_workspace_insert_creates_one_parent_agent(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with initialized_database(config) as connection:
        create_owner(connection, "owner-local", "Local Owner")
        create_workspace(connection, "workspace-1", "owner-local", "Personal Workspace")
        agents = connection.execute("SELECT role, display_name FROM workspace_agent").fetchall()
        assert [(row["role"], row["display_name"]) for row in agents] == [("parent", "父 Agent")]


def test_schema_upgrades_from_desktop_0001_and_backfills_parent_agent(tmp_path: Path) -> None:
    from omnibase.desktop_local.database import utc_now_text
    from omnibase.desktop_local.schema import DESKTOP_0001

    config = _config(tmp_path)
    connection = open_database(config)
    try:
        applied_at = utc_now_text()
        connection.execute("BEGIN EXCLUSIVE")
        for statement in DESKTOP_0001.statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO desktop_migration_history "
            "(version, migration_id, checksum_sha256, application_version, applied_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (1, DESKTOP_0001.migration_id, DESKTOP_0001.checksum, "1.0.0", applied_at),
        )
        connection.execute("PRAGMA user_version = 1")
        connection.execute(f"PRAGMA application_id = {DESKTOP_APPLICATION_ID}")
        connection.execute(
            "INSERT INTO desktop_schema_metadata "
            "(singleton_key, schema_version, application_version, updated_at) "
            "VALUES (1, 1, '1.0.0', ?)",
            (applied_at,),
        )
        create_owner(connection, "owner-local", "Local Owner")
        create_workspace(connection, "workspace-1", "owner-local", "Personal Workspace")
        connection.execute("COMMIT")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert migrate_database(connection, config) == DESKTOP_SCHEMA_VERSION
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM workspace_agent WHERE role = 'parent'"
            ).fetchone()[0]
            == 1
        )
        assert local_health(connection).schema_version == DESKTOP_SCHEMA_VERSION
    finally:
        connection.close()
