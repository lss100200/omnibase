"""Alembic migrations environment.

Multi-schema migration flow:
- Global tables (omnibase_meta.tenants): migrate against `omnibase_meta` schema
- Tenant tables: loop over every retained tenant in omnibase_meta.tenants and
  migrate each tenant schema independently (inactive rows retain their data)

Two operating modes:
- ONLINE: `alembic upgrade head` - connects to DB and applies migrations
- OFFLINE: `alembic upgrade head --sql` - generates SQL script

The version tracking table lives in each schema separately (per-tenant
version_table_schema), so different tenants can be at different migration
versions if needed.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from alembic import context
from sqlalchemy import engine_from_config, pool, text

# Ensure src/ is on sys.path so `omnibase` package is importable when running
# `alembic` from the backend/ directory.
BACKEND_ROOT = Path(__file__).resolve().parents[3]  # backend/
SRC_ROOT = BACKEND_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from omnibase.capabilities import models as capability_models  # noqa: E402, F401
from omnibase.control_plane import models as control_plane_models  # noqa: E402, F401
from omnibase.core.config import get_settings  # noqa: E402
from omnibase.core.logging import configure_logging, get_logger  # noqa: E402
from omnibase.db import tenant as tenant_models  # noqa: E402, F401
from omnibase.db.models import GLOBAL_METADATA, TENANT_METADATA  # noqa: E402
from omnibase.sandbox import models as sandbox_models  # noqa: E402, F401
from omnibase.tenants.service import _initialize_tenant_schema  # noqa: E402
from omnibase.workspaces import models as workspace_models  # noqa: E402, F401

# Configure logging for Alembic
configure_logging()
log = get_logger("omnibase.migrations")

# Load Alembic .ini config
config = context.config

# Interpret the config file for Python logging if present
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Inject database URL from app settings (single source of truth)
settings = get_settings()
config.set_main_option("sqlalchemy.url", str(settings.database_url))

# -----------------------------------------------------------
# Target metadata
# -----------------------------------------------------------
TARGET_METADATA_GLOBAL = GLOBAL_METADATA
TARGET_METADATA_TENANT = TENANT_METADATA
GLOBAL_SCHEMA_NAME = "omnibase_meta"


def _get_all_tenant_schemas(connectable: Any) -> list[str]:
    """Return all retained tenant schema names currently registered.

    Inactive tenants are soft-deleted but their schemas and data are preserved,
    so they must continue to receive migrations.  Returns [] if the registry
    does not exist yet (first migration).
    """
    try:
        with connectable.connect() as conn:
            result = conn.execute(
                text("SELECT schema_name FROM omnibase_meta.tenants " "ORDER BY schema_name")
            )
            return [row[0] for row in result]
    except Exception as exc:
        log.info(
            "migrations.tenant_schemas_skipped",
            reason="tenants table not yet available",
            error=str(exc)[:200],
        )
        return []


def _configure_context(
    *,
    connection: Any | None = None,
    url: str | None = None,
    target_metadata: Any,
    version_table_schema: str,
    literal_binds: bool = False,
) -> None:
    """Common context.configure() call with consistent options."""
    kwargs: dict[str, Any] = {
        "target_metadata": target_metadata,
        "version_table_schema": version_table_schema,
        "compare_type": True,
        "compare_server_default": True,
        "include_schemas": True,
    }
    if connection is not None:
        kwargs["connection"] = connection
    if url is not None:
        kwargs["url"] = url
    if literal_binds:
        kwargs["literal_binds"] = True
        kwargs["dialect_opts"] = {"paramstyle": "named"}
    context.configure(**kwargs)


def run_migrations_offline() -> None:
    """Generate SQL without DB connection (GLOBAL schema only)."""
    url = config.get_main_option("sqlalchemy.url")
    config.attributes["migration_schema_scope"] = "global"
    log.info("migrations.offline_start", schema=GLOBAL_SCHEMA_NAME)
    _configure_context(
        url=url,
        target_metadata=TARGET_METADATA_GLOBAL,
        version_table_schema=GLOBAL_SCHEMA_NAME,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()
    log.info("migrations.offline_complete", schema=GLOBAL_SCHEMA_NAME)
    log.warning(
        "migrations.offline_tenant_skipped",
        reason="tenant schemas require online mode",
    )


def run_migrations_online() -> None:
    """Apply migrations: global first, then loop over each tenant schema."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # ---- Phase 1: global schema ----
    config.attributes["migration_schema_scope"] = "global"
    log.info("migrations.online.global_start", schema=GLOBAL_SCHEMA_NAME)
    with connectable.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{GLOBAL_SCHEMA_NAME}"'))

    with connectable.connect() as connection:
        _configure_context(
            connection=connection,
            target_metadata=TARGET_METADATA_GLOBAL,
            version_table_schema=GLOBAL_SCHEMA_NAME,
        )
        with context.begin_transaction():
            context.run_migrations()
    log.info("migrations.online.global_complete", schema=GLOBAL_SCHEMA_NAME)

    # ---- Phase 2: per-tenant schemas ----
    tenant_schemas = _get_all_tenant_schemas(connectable)
    if not tenant_schemas:
        log.info("migrations.online.tenant_none", reason="no retained tenants yet")
        return

    log.info("migrations.online.tenant_start", count=len(tenant_schemas))
    config.attributes["migration_schema_scope"] = "tenant"
    for schema_name in tenant_schemas:
        # Existing runtime-bootstrapped schemas may have no tenant-local
        # alembic_version table. Use one physical connection for bootstrap and
        # Alembic setup; each phase retains its own explicit transaction.
        with connectable.connect() as connection:
            with connection.begin():
                _initialize_tenant_schema(connection, schema_name)

            connection.execute(
                text(f'SET search_path TO "{schema_name}", {GLOBAL_SCHEMA_NAME}, public')
            )
            connection.commit()

            _configure_context(
                connection=connection,
                target_metadata=TARGET_METADATA_TENANT,
                version_table_schema=schema_name,
            )
            with context.begin_transaction():
                context.run_migrations()
        log.info("migrations.online.tenant_one_complete", schema=schema_name)

    log.info(
        "migrations.online.tenant_all_complete",
        count=len(tenant_schemas),
    )


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
