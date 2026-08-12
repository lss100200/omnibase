"""Transaction-bound Alembic catch-up for a newly registered tenant schema."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import Connection

from omnibase.db.models import TENANT_METADATA
from omnibase.tenants.schema_manager import validate_schema_name

_BACKEND_ROOT = Path(__file__).resolve().parents[3]
_ALEMBIC_INI = _BACKEND_ROOT / "alembic.ini"


def upgrade_new_tenant_schema(connection: Connection, schema_name: str) -> None:
    """Upgrade exactly one newly created tenant schema to the current head.

    The caller owns the connection transaction.  Any migration failure therefore
    rolls back the tenant registry row, schema creation, bootstrap DDL, and
    tenant-local Alembic changes together.
    """

    validate_schema_name(schema_name)
    config = Config(str(_ALEMBIC_INI))
    config.set_main_option(
        "script_location", str(_BACKEND_ROOT / "src" / "omnibase" / "migrations")
    )
    config.attributes["migration_schema_scope"] = "tenant"
    script = ScriptDirectory.from_config(config)

    def upgrade(revision: str, _context: Any) -> list[Any]:
        return script._upgrade_revs("head", revision)

    connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", omnibase_meta, public'))
    with EnvironmentContext(
        config,
        script,
        fn=upgrade,
        destination_rev="head",
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


__all__ = ["upgrade_new_tenant_schema"]
