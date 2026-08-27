"""SQLite connection, migration and health primitives for desktop-local mode."""

from __future__ import annotations

import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from omnibase.desktop_local.config import DesktopLocalConfig, prepare_data_root
from omnibase.desktop_local.errors import DesktopDatabaseUnavailable, DesktopMigrationError
from omnibase.desktop_local.schema import (
    DESKTOP_APPLICATION_ID,
    DESKTOP_MIGRATIONS,
    DESKTOP_SCHEMA_VERSION,
)

_MINIMUM_SQLITE_VERSION = (3, 37, 0)
_REPARSE_POINT_ATTRIBUTE = 0x400


def utc_now_text() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _database_file_identity(config: DesktopLocalConfig) -> tuple[int, int] | None:
    path = config.database_path
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise DesktopDatabaseUnavailable("desktop_database_target_metadata_unavailable") from None
    attributes = getattr(metadata, "st_file_attributes", 0)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or bool(attributes & _REPARSE_POINT_ATTRIBUTE)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_nlink != 1
    ):
        raise DesktopDatabaseUnavailable("desktop_database_target_not_safe")
    return (metadata.st_dev, metadata.st_ino)


def _enable_wal(connection: sqlite3.Connection, busy_timeout_ms: int) -> str:
    deadline = time.monotonic() + (busy_timeout_ms / 1_000)
    retryable_codes = {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}
    while True:
        try:
            mode = str(connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", None) not in retryable_codes:
                raise
            mode = "busy"
        if mode == "wal":
            return mode
        if time.monotonic() >= deadline:
            raise DesktopDatabaseUnavailable("desktop_database_wal_unavailable")
        time.sleep(0.01)


def open_database(config: DesktopLocalConfig) -> sqlite3.Connection:
    """Open and harden one desktop SQLite connection.

    The public error deliberately omits the physical database path and the
    original operating-system message.
    """

    connection: sqlite3.Connection | None = None
    try:
        if sqlite3.sqlite_version_info < _MINIMUM_SQLITE_VERSION:
            raise DesktopDatabaseUnavailable("desktop_sqlite_version_unsupported")
        prepare_data_root(config.data_root)
        initial_identity = _database_file_identity(config)
        connection = sqlite3.connect(
            config.database_path,
            timeout=config.busy_timeout_ms / 1_000,
            isolation_level=None,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        opened_identity = _database_file_identity(config)
        if opened_identity is None or (
            initial_identity is not None and opened_identity != initial_identity
        ):
            raise DesktopDatabaseUnavailable("desktop_database_target_identity_drift")
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {config.busy_timeout_ms}")
        mode = _enable_wal(connection, config.busy_timeout_ms)
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("SELECT json_valid('{}')").fetchone()
        if str(mode).lower() != "wal":
            connection.close()
            raise DesktopDatabaseUnavailable("desktop_database_wal_unavailable")
        if connection.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
            connection.close()
            raise DesktopDatabaseUnavailable("desktop_database_foreign_keys_unavailable")
        return connection
    except DesktopDatabaseUnavailable:
        if connection is not None:
            connection.close()
        raise
    except (OSError, sqlite3.Error):
        if connection is not None:
            connection.close()
        raise DesktopDatabaseUnavailable("desktop_database_open_failed") from None


def _current_schema_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def _verify_application_id(connection: sqlite3.Connection, current_version: int) -> None:
    application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
    if current_version == 0 and application_id == 0:
        return
    if application_id != DESKTOP_APPLICATION_ID:
        raise DesktopMigrationError("desktop_database_application_id_mismatch")


def _verify_applied_migrations(connection: sqlite3.Connection, current_version: int) -> None:
    if current_version == 0:
        return
    try:
        rows = connection.execute(
            "SELECT version, migration_id, checksum_sha256 FROM desktop_migration_history "
            "ORDER BY version"
        ).fetchall()
    except sqlite3.Error:
        raise DesktopMigrationError("desktop_migration_history_unavailable") from None
    expected = DESKTOP_MIGRATIONS[:current_version]
    if len(rows) != len(expected):
        raise DesktopMigrationError("desktop_migration_history_mismatch")
    for row, migration in zip(rows, expected, strict=True):
        if (
            row["version"] != migration.version
            or row["migration_id"] != migration.migration_id
            or row["checksum_sha256"] != migration.checksum
        ):
            raise DesktopMigrationError("desktop_migration_history_mismatch")


def _verify_schema_metadata(connection: sqlite3.Connection, current_version: int) -> None:
    try:
        row = connection.execute(
            "SELECT schema_version FROM desktop_schema_metadata WHERE singleton_key = 1"
        ).fetchone()
    except sqlite3.Error:
        raise DesktopMigrationError("desktop_schema_metadata_unavailable") from None
    if row is None or row["schema_version"] != current_version:
        raise DesktopMigrationError("desktop_schema_metadata_mismatch")


def migrate_database(connection: sqlite3.Connection, config: DesktopLocalConfig) -> int:
    """Apply every pending desktop migration in one atomic transaction."""

    current_version = _current_schema_version(connection)
    if current_version > DESKTOP_SCHEMA_VERSION:
        raise DesktopMigrationError("desktop_schema_newer_than_application")
    _verify_application_id(connection, current_version)
    _verify_applied_migrations(connection, current_version)
    if current_version == DESKTOP_SCHEMA_VERSION:
        _verify_schema_metadata(connection, current_version)
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE desktop_schema_metadata SET application_version = ?, updated_at = ? "
                "WHERE singleton_key = 1 AND schema_version = ?",
                (config.application_version, utc_now_text(), current_version),
            )
            connection.execute("COMMIT")
        except sqlite3.Error:
            if connection.in_transaction:
                connection.execute("ROLLBACK")
            raise DesktopMigrationError("desktop_application_version_update_failed") from None
        return current_version

    applied_at = utc_now_text()
    try:
        connection.execute("BEGIN EXCLUSIVE")
        # Recheck after acquiring the migration lock so two launchers cannot
        # independently apply the same migration.
        locked_version = _current_schema_version(connection)
        if locked_version != current_version:
            connection.execute("ROLLBACK")
            return migrate_database(connection, config)
        for migration in DESKTOP_MIGRATIONS[current_version:]:
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO desktop_migration_history "
                "(version, migration_id, checksum_sha256, application_version, applied_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    migration.version,
                    migration.migration_id,
                    migration.checksum,
                    config.application_version,
                    applied_at,
                ),
            )
            connection.execute(f"PRAGMA user_version = {migration.version}")
        connection.execute(f"PRAGMA application_id = {DESKTOP_APPLICATION_ID}")
        connection.execute(
            "INSERT INTO desktop_schema_metadata "
            "(singleton_key, schema_version, application_version, updated_at) "
            "VALUES (1, ?, ?, ?) "
            "ON CONFLICT(singleton_key) DO UPDATE SET "
            "schema_version = excluded.schema_version, "
            "application_version = excluded.application_version, "
            "updated_at = excluded.updated_at",
            (DESKTOP_SCHEMA_VERSION, config.application_version, applied_at),
        )
        connection.execute("COMMIT")
    except (sqlite3.Error, DesktopMigrationError):
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise DesktopMigrationError("desktop_migration_failed") from None
    return DESKTOP_SCHEMA_VERSION


@contextmanager
def initialized_database(config: DesktopLocalConfig) -> Iterator[sqlite3.Connection]:
    """Open, migrate and close a desktop database."""

    connection = open_database(config)
    try:
        migrate_database(connection, config)
        yield connection
    finally:
        connection.close()


@dataclass(frozen=True, slots=True)
class DesktopLocalHealth:
    status: str
    schema_version: int
    application_version: str
    application_id: int
    journal_mode: str
    foreign_keys: bool
    integrity: str


def local_health(connection: sqlite3.Connection) -> DesktopLocalHealth:
    """Return bounded local health facts without any physical path."""

    try:
        schema_version = _current_schema_version(connection)
        application_id = int(connection.execute("PRAGMA application_id").fetchone()[0])
        metadata = connection.execute(
            "SELECT schema_version, application_version FROM desktop_schema_metadata "
            "WHERE singleton_key = 1"
        ).fetchone()
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        foreign_keys = bool(connection.execute("PRAGMA foreign_keys").fetchone()[0])
        integrity = str(connection.execute("PRAGMA quick_check(1)").fetchone()[0]).lower()
    except sqlite3.Error:
        raise DesktopDatabaseUnavailable("desktop_database_health_failed") from None
    healthy = (
        metadata is not None
        and metadata["schema_version"] == schema_version == DESKTOP_SCHEMA_VERSION
        and application_id == DESKTOP_APPLICATION_ID
        and journal_mode == "wal"
        and foreign_keys
        and integrity == "ok"
    )
    return DesktopLocalHealth(
        status="healthy" if healthy else "unhealthy",
        schema_version=schema_version,
        application_version=str(metadata["application_version"]) if metadata else "unknown",
        application_id=application_id,
        journal_mode=journal_mode,
        foreign_keys=foreign_keys,
        integrity=integrity,
    )
