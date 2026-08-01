#!/usr/bin/env python3
"""Verify and restore an OmniBase backup into a newly created database only."""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import load_json, require_executable, require_identifier, run_checked, sha256_file


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Restore a verified backup to a new database; existing databases are never overwritten."
    )
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--maintenance-database", default="postgres")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--username", required=True)
    parser.add_argument("--confirm", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.confirm != "CREATE_NEW_DATABASE_ONLY":
        raise ValueError("confirmation must be exactly CREATE_NEW_DATABASE_ONLY")
    target = require_identifier(args.target_database, label="target database")
    maintenance = require_identifier(args.maintenance_database, label="maintenance database")
    username = require_identifier(args.username, label="username")
    if not target.startswith("omnibase_restore_"):
        raise ValueError("target database must use the omnibase_restore_ prefix")
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    backup = args.backup.resolve(strict=True)
    manifest_path = args.manifest.resolve(strict=True)
    manifest = load_json(manifest_path)
    if manifest.get("format_version") != 1:
        raise ValueError("unsupported backup manifest version")
    if manifest.get("backup_file") != backup.name:
        raise ValueError("manifest backup_file does not match the selected backup")
    source = require_identifier(str(manifest.get("source_database", "")), label="source database")
    if target == source:
        raise ValueError("target database must differ from the source database")
    expected_checksum = str(manifest.get("sha256", ""))
    actual_checksum = sha256_file(backup)
    if expected_checksum != actual_checksum:
        raise ValueError("backup checksum does not match the manifest")

    psql = require_executable("psql")
    createdb = require_executable("createdb")
    pg_restore = require_executable("pg_restore")
    connection = ["--host", args.host, "--port", str(args.port), "--username", username]
    existing = run_checked(
        [
            psql,
            *connection,
            "--dbname",
            maintenance,
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--set",
            f"target={target}",
            "--command",
            "SELECT 1 FROM pg_database WHERE datname = :'target';",
        ],
        capture=True,
    )
    if existing:
        raise RuntimeError(f"refusing to overwrite existing database: {target}")

    run_checked([createdb, *connection, "--maintenance-db", maintenance, target])
    try:
        run_checked(
            [
                pg_restore,
                *connection,
                "--dbname",
                target,
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-privileges",
                str(backup),
            ]
        )
    except Exception as exc:
        raise RuntimeError(
            f"restore failed; the newly created database {target!r} was retained for inspection"
        ) from exc
    print(f"restored_database={target}")
    print("next=run verify_restore.py before any application cutover")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
