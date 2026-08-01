#!/usr/bin/env python3
"""Create a non-overwriting PostgreSQL custom-format backup plus checksum manifest."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from _common import require_executable, require_identifier, run_checked, sha256_file, write_json_new


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an OmniBase PostgreSQL backup without reading a .env file."
    )
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--username", required=True)
    parser.add_argument("--label", default="manual")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = require_identifier(args.database, label="database")
    username = require_identifier(args.username, label="username")
    label = require_identifier(args.label.replace("-", "_"), label="label")
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    stem = f"{database}_{label}_{timestamp}"
    backup_path = output_dir / f"{stem}.dump"
    manifest_path = output_dir / f"{stem}.manifest.json"
    checksum_path = output_dir / f"{stem}.sha256"
    for path in (backup_path, manifest_path, checksum_path):
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing backup artifact: {path}")

    pg_dump = require_executable("pg_dump")
    version = run_checked([pg_dump, "--version"], capture=True)
    run_checked(
        [
            pg_dump,
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--username",
            username,
            "--dbname",
            database,
            "--format=custom",
            "--compress=9",
            "--no-owner",
            "--no-privileges",
            "--file",
            str(backup_path),
        ]
    )
    try:
        backup_path.chmod(0o600)
    except OSError:
        pass

    checksum = sha256_file(backup_path)
    checksum_path.write_text(f"{checksum}  {backup_path.name}\n", encoding="ascii", newline="\n")
    try:
        checksum_path.chmod(0o600)
    except OSError:
        pass
    write_json_new(
        manifest_path,
        {
            "format_version": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "source_database": database,
            "backup_file": backup_path.name,
            "sha256": checksum,
            "pg_dump_version": version,
            "owner_and_acl_included": False,
        },
    )
    print(f"backup={backup_path}")
    print(f"manifest={manifest_path}")
    print(f"checksum={checksum_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
