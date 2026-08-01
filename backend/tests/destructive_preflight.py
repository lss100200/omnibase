"""Read-only preflight for the disposable destructive-test database.

This module must run before Alembic or pytest. It verifies explicit opt-in,
the dedicated database-name policy, the sentinel, and a restricted non-owner
role without executing DDL or DML.
"""

from __future__ import annotations

import os

from cleanup import require_destructive_test_environment


def main() -> int:
    database_url = require_destructive_test_environment()
    if os.environ.get("DATABASE_URL") != database_url:
        raise RuntimeError("DATABASE_URL must exactly match TEST_DATABASE_URL")

    from sqlalchemy import create_engine

    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            from cleanup import verify_sqlalchemy_connection

            verify_sqlalchemy_connection(connection)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
