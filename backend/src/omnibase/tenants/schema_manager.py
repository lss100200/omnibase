"""Schema-per-tenant management.

Responsibilities:
- Create PostgreSQL schema for a new tenant
- Apply business-table migrations to a specific schema
- Switch the active schema on a Session via search_path

Why schema-per-tenant (vs Row-Level Security):
- Physical isolation: a bug in one tenant's query cannot leak another tenant's rows
- Easy backup/restore per tenant: pg_dump -n tenant_xxx
- Clear cost attribution for future multi-tenant cloud offering
- Trade-off: Alembic migrations need to loop over all tenant schemas (handled here)
"""

from __future__ import annotations

import re
from collections.abc import Iterator

from sqlalchemy import event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from omnibase.core.config import Settings
from omnibase.core.db import TENANT_SCHEMA_SESSION_KEY, get_engine
from omnibase.core.logging import get_logger

log = get_logger(__name__)


# PostgreSQL identifier rules: <= 63 chars, [a-z_][a-z0-9_$]*
# We prefix with "tenant_" + first 8 chars of UUID + crc32 checksum for uniqueness
_SCHEMA_NAME_PATTERN = re.compile(r"^tenant_[a-z0-9]{8,12}$")
_MAX_SCHEMA_NAME_LEN = 63


class SchemaError(Exception):
    """Raised when a schema operation fails or input is invalid."""


# -----------------------------------------------------------
# Schema name generation
# -----------------------------------------------------------
def make_schema_name(tenant_id: str) -> str:
    """Derive a valid PostgreSQL schema name from a tenant UUID.

    Format: tenant_<first 8 chars of UUID>  (e.g. tenant_a1b2c3d4)
    - Always lowercase (Postgres folds unquoted identifiers anyway)
    - 8 chars gives 4 billion combinations; collisions are astronomically unlikely
    - Collisions are additionally prevented by the unique constraint on tenants.schema_name
    """
    if not tenant_id:
        raise SchemaError("tenant_id is required to derive schema name")

    # Strip hyphens from UUID, take first 8 hex chars
    short = tenant_id.replace("-", "").lower()[:8]
    if len(short) < 8 or not short.isalnum():
        raise SchemaError(f"Invalid tenant_id for schema name: {tenant_id!r}")

    name = f"tenant_{short}"
    if len(name) > _MAX_SCHEMA_NAME_LEN:
        raise SchemaError(f"Schema name too long: {name!r}")
    return name


def validate_schema_name(name: str) -> None:
    """Raise SchemaError if name is not a valid tenant schema identifier."""
    if not _SCHEMA_NAME_PATTERN.match(name):
        raise SchemaError(
            f"Invalid schema name {name!r}. Must match {_SCHEMA_NAME_PATTERN.pattern}"
        )


# -----------------------------------------------------------
# DDL operations (CREATE / DROP / EXISTS)
# -----------------------------------------------------------
def schema_exists(engine: Engine, schema_name: str) -> bool:
    """Return True if the PostgreSQL schema exists."""
    validate_schema_name(schema_name)
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM information_schema.schemata WHERE schema_name = :name"),
            {"name": schema_name},
        )
        return result.scalar() is not None


def create_schema(
    engine_or_connection: Engine | Connection,
    schema_name: str,
    *,
    if_not_exists: bool = False,
) -> None:
    """Create a validated tenant schema without silently adopting an orphan."""
    validate_schema_name(schema_name)
    clause = "CREATE SCHEMA" + (" IF NOT EXISTS" if if_not_exists else "")
    statement = text(f'{clause} "{schema_name}"')
    if isinstance(engine_or_connection, Connection):
        engine_or_connection.execute(statement)
    else:
        with engine_or_connection.begin() as connection:
            connection.execute(statement)
    log.info("schema.created", schema=schema_name)


def drop_schema(
    engine: Engine,
    schema_name: str,
    *,
    cascade: bool = False,
    expected_schema_name: str | None = None,
) -> None:
    """Drop a tenant schema only after validation and optional exact-name guard."""
    validate_schema_name(schema_name)
    if expected_schema_name is not None and schema_name != expected_schema_name:
        raise SchemaError("Refusing to drop a schema that does not match the expected tenant")
    suffix = " CASCADE" if cascade else " RESTRICT"
    with engine.begin() as conn:
        conn.execute(text(f'DROP SCHEMA "{schema_name}"{suffix}'))
    log.warning("schema.dropped", schema=schema_name, cascade=cascade)


# -----------------------------------------------------------
# search_path switching (per-request isolation)
# -----------------------------------------------------------
def set_search_path(session: Session, schema_name: str) -> None:
    """Bind a Session to a validated tenant schema for each transaction."""
    validate_schema_name(schema_name)
    existing = session.info.get(TENANT_SCHEMA_SESSION_KEY)
    if existing is not None and existing != schema_name:
        raise SchemaError("Session is already bound to a different tenant schema")
    session.info[TENANT_SCHEMA_SESSION_KEY] = schema_name
    if session.in_transaction():
        session.execute(text(f'SET LOCAL search_path TO "{schema_name}", omnibase_meta, public'))
    log.debug("search_path.bound", schema=schema_name)


def get_current_search_path(session: Session) -> str:
    """Read the current search_path (for debugging / assertions)."""
    result = session.execute(text("SHOW search_path"))
    return str(result.scalar())


# -----------------------------------------------------------
# Tenant-scoped sessions
# -----------------------------------------------------------
class TenantSession:
    """Context manager that yields a Session scoped to a specific tenant schema.

    Usage:
        with TenantSession(engine, "tenant_abc123") as session:
            session.query(Document).all()  # resolves to tenant_abc123.documents

    The search_path is reset to the connection's default on exit.
    """

    def __init__(
        self,
        engine_or_settings: Engine | Settings,
        schema_name: str,
    ) -> None:
        if isinstance(engine_or_settings, Settings):
            self._engine = get_engine(engine_or_settings)
        else:
            self._engine = engine_or_settings
        validate_schema_name(schema_name)
        self._schema_name = schema_name
        self._session: Session | None = None

    def __enter__(self) -> Session:
        factory: sessionmaker[Session] = sessionmaker(
            bind=self._engine,
            autoflush=False,
            expire_on_commit=False,
        )
        self._session = factory()

        @event.listens_for(self._session, "after_begin")
        def _set_local_search_path(
            session: Session,
            transaction: object,
            connection: Connection,
        ) -> None:
            del session
            if getattr(transaction, "parent", None) is None:
                connection.execute(
                    text(
                        f'SET LOCAL search_path TO "{self._schema_name}", '
                        "omnibase_meta, public"
                    )
                )

        set_search_path(self._session, self._schema_name)
        return self._session

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._session is None:
            return
        try:
            if exc_type is not None:
                self._session.rollback()
            # search_path is LOCAL to the transaction; closing returns the
            # connection to the pool with its default search_path.
            self._session.close()
        finally:
            self._session = None


def tenant_session(schema_name: str) -> Iterator[Session]:
    """FastAPI dependency: yield a Session scoped to the given tenant schema.

    Usage:
        @app.get("/items")
        def list_items(db: Session = Depends(tenant_session("tenant_xxx"))):
            ...

    For request-scoped tenants (typical), wrap this in a Depends() factory
    that pulls the schema name from the JWT (see tenants/dependencies.py).
    """
    with TenantSession(get_engine(), schema_name) as session:  # type: ignore[arg-type]
        yield session


# -----------------------------------------------------------
# Tenant enumeration (for migrations)
# -----------------------------------------------------------
def list_active_tenant_schemas(engine: Engine) -> list[str]:
    """Return only active tenant schemas for authentication and request paths."""
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT schema_name FROM omnibase_meta.tenants "
                "WHERE is_active IS TRUE ORDER BY schema_name"
            )
        )
        return [row[0] for row in result]


def list_tenant_schemas(engine: Engine) -> list[str]:
    """Return all retained tenant schema names currently registered.

    Soft-deleted tenants retain their schemas and data, so migration tooling
    must not exclude them merely because requests are disabled.
    """
    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT schema_name FROM omnibase_meta.tenants "
                "ORDER BY schema_name"
            )
        )
        return [row[0] for row in result]


__all__ = [
    "SchemaError",
    "TenantSession",
    "create_schema",
    "drop_schema",
    "get_current_search_path",
    "list_active_tenant_schemas",
    "list_tenant_schemas",
    "make_schema_name",
    "schema_exists",
    "set_search_path",
    "tenant_session",
    "validate_schema_name",
]
