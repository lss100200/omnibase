#!/usr/bin/env python3
"""Run read-only structural checks against a restored OmniBase database."""

from __future__ import annotations

import argparse
import json

from _common import require_executable, require_identifier, run_checked


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a restored OmniBase database read-only.")
    parser.add_argument("--database", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=5432, type=int)
    parser.add_argument("--username", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    database = require_identifier(args.database, label="database")
    username = require_identifier(args.username, label="username")
    if not database.startswith("omnibase_restore_"):
        raise ValueError("verification target must use the omnibase_restore_ prefix")
    if not 1 <= args.port <= 65535:
        raise ValueError("port must be between 1 and 65535")

    psql = require_executable("psql")
    sql = """
WITH checks(name, actual, expected) AS (
    VALUES
      ('database_name', current_database(), :'expected_database'),
      ('meta_schema', CASE WHEN to_regnamespace('omnibase_meta') IS NULL THEN 'missing' ELSE 'present' END, 'present'),
      ('meta_revision', COALESCE((SELECT version_num FROM omnibase_meta.alembic_version LIMIT 1), 'missing'), :'expected_revision'),
      ('tenant_registry', CASE WHEN to_regclass('omnibase_meta.tenants') IS NULL THEN 'missing' ELSE 'present' END, 'present'),
      ('missing_tenant_schemas', (
          SELECT count(*)::text FROM omnibase_meta.tenants t
          WHERE to_regnamespace(t.schema_name) IS NULL
      ), '0'),
      ('audit_append_only_trigger', CASE WHEN EXISTS (
          SELECT 1 FROM pg_trigger tg
          JOIN pg_class c ON c.oid = tg.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = 'omnibase_meta' AND c.relname = 'audit_events'
            AND tg.tgname = 'audit_events_append_only' AND NOT tg.tgisinternal
      ) THEN 'present' ELSE 'missing' END, 'present'),
      ('capability_revocation_trigger', CASE WHEN EXISTS (
          SELECT 1 FROM pg_trigger tg
          JOIN pg_class c ON c.oid = tg.tgrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
          WHERE n.nspname = 'omnibase_meta' AND c.relname = 'capability_revocations'
            AND tg.tgname = 'capability_revocations_append_only' AND NOT tg.tgisinternal
      ) THEN 'present' ELSE 'missing' END, 'present')
)
SELECT name || E'\t' || actual || E'\t' || expected || E'\t' ||
       CASE WHEN actual = expected THEN 'pass' ELSE 'fail' END
FROM checks ORDER BY name;
"""
    output = run_checked(
        [
            psql,
            "--host",
            args.host,
            "--port",
            str(args.port),
            "--username",
            username,
            "--dbname",
            database,
            "--no-psqlrc",
            "--tuples-only",
            "--no-align",
            "--set",
            "ON_ERROR_STOP=1",
            "--set",
            f"expected_database={database}",
            "--set",
            f"expected_revision={args.expected_revision}",
            "--command",
            sql,
        ],
        capture=True,
    )
    checks = []
    for line in output.splitlines():
        name, actual, expected, status = line.split("\t", 3)
        checks.append({"name": name, "actual": actual, "expected": expected, "status": status})
    report = {"database": database, "read_only_checks": checks}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not checks or any(item["status"] != "pass" for item in checks):
        raise RuntimeError("restored database verification failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
