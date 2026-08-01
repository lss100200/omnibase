"""SQLAlchemy engine and session management.

Every pooled connection is reset to the non-tenant baseline when checked out.
Tenant scope is then applied with ``SET LOCAL`` when a Session transaction
begins, so tenant state cannot survive a commit or return to the pool.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, sessionmaker

from omnibase.core.config import Settings, get_settings
from omnibase.core.logging import get_logger

log = get_logger(__name__)

# Module-level cache: keyed by database URL
_engines: dict[str, Engine] = {}
_session_factories: dict[str, sessionmaker[Session]] = {}


_BASELINE_SEARCH_PATH = "omnibase_meta, public"
TENANT_SCHEMA_SESSION_KEY = "omnibase_tenant_schema"
TENANT_CONTEXT_REQUIRED_SESSION_KEY = "omnibase_tenant_context_required"


def _install_search_path_hook(engine: Engine) -> None:
    """Reset pooled connections and apply validated transaction-local tenant scope."""

    @event.listens_for(engine.pool, "checkout")
    def _reset_search_path_on_checkout(
        dbapi_conn: Any,
        connection_record: Any,
        proxy: Any,
    ) -> None:
        del connection_record, proxy
        try:
            with dbapi_conn.cursor() as cursor:
                cursor.execute(f"SET search_path TO {_BASELINE_SEARCH_PATH}")
            dbapi_conn.commit()
        except Exception:
            try:
                dbapi_conn.rollback()
            except Exception:
                log.exception("db.search_path_baseline_rollback_failed")
            log.exception("db.search_path_baseline_reset_failed")
            raise

    @event.listens_for(Session, "after_begin")
    def _set_tenant_search_path_after_begin(
        session: Session,
        transaction: Any,
        connection: Connection,
    ) -> None:
        if transaction.parent is not None or connection.engine is not engine:
            return

        from omnibase.tenants.context import get_current_schema
        from omnibase.tenants.schema_manager import SchemaError, validate_schema_name

        context_schema = get_current_schema()
        session_schema = session.info.get(TENANT_SCHEMA_SESSION_KEY)
        context_required = bool(session.info.get(TENANT_CONTEXT_REQUIRED_SESSION_KEY))
        if context_required and context_schema is None:
            raise RuntimeError("Tenant database session requires an active tenant context")
        if session_schema is not None and context_schema is not None and session_schema != context_schema:
            raise RuntimeError("Tenant session schema does not match the active tenant context")
        schema = session_schema or context_schema
        if schema is None:
            return
        try:
            validate_schema_name(schema)
            connection.execute(
                text(f'SET LOCAL search_path TO "{schema}", {_BASELINE_SEARCH_PATH}')
            )
        except SchemaError:
            log.error("db.invalid_tenant_schema_in_contextvar", schema=schema)
            raise
        except Exception:
            log.error("db.search_path_set_failed", schema=schema)
            raise


def get_engine(settings: Settings | None = None) -> Engine:
    """Return the (cached) SQLAlchemy engine.

    The engine is created lazily on first call and reused. Connection pooling
    is configured from settings; in development we disable pool pre-ping for
    speed, in production we enable it for resilience.
    """
    settings = settings or get_settings()
    url = str(settings.database_url)

    if url in _engines:
        return _engines[url]

    # Translate settings into SQLAlchemy create_engine kwargs.
    # NOTE: psycopg3 (used here) accepts both `postgresql://` and `postgresql+psycopg://`.
    connect_args: dict[str, Any] = {
        "connect_timeout": 10,
    }

    engine = create_engine(
        url=url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout_seconds,
        pool_recycle=settings.db_pool_recycle_seconds,
        pool_pre_ping=settings.is_production,
        future=True,
        connect_args=connect_args,
        # Echo SQL in development for debugging; very noisy otherwise.
        echo=False,
    )

    # Install automatic tenant search_path switching
    _install_search_path_hook(engine)

    _engines[url] = engine
    log.info(
        "db.engine_created",
        url=_safe_url(url),
        pool_size=settings.db_pool_size,
    )
    return engine


def get_session_factory(settings: Settings | None = None) -> sessionmaker[Session]:
    """Return cached sessionmaker bound to the engine."""
    settings = settings or get_settings()
    url = str(settings.database_url)
    if url in _session_factories:
        return _session_factories[url]

    engine = get_engine(settings)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    _session_factories[url] = factory
    return factory


def get_db() -> Iterator[Session]:
    """FastAPI dependency: yield a session, ensure it's closed.

    Usage:
        @app.get("/items")
        def list_items(db: Session = Depends(get_db)):
            ...
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        # Commit is the route's responsibility (or use a Unit-of-Work wrapper).
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def dispose_engines() -> None:
    """Dispose all engines (used in tests and graceful shutdown)."""
    for url, engine in _engines.items():
        try:
            engine.dispose()
            log.info("db.engine_disposed", url=_safe_url(url))
        except Exception as exc:
            log.warning("db.engine_dispose_failed", url=_safe_url(url), error=str(exc))
    _engines.clear()
    _session_factories.clear()


def _safe_url(url: str) -> str:
    """Mask password in URL for logging."""
    if "@" not in url:
        return url
    # Split scheme://user:pass@host/db
    scheme, rest = url.split("://", 1) if "://" in url else ("", url)
    if "@" not in rest:
        return url
    creds, host_part = rest.rsplit("@", 1)
    if ":" in creds:
        user, _ = creds.split(":", 1)
        masked_creds = f"{user}:***"
    else:
        masked_creds = "***"
    return f"{scheme}://{masked_creds}@{host_part}" if scheme else f"{masked_creds}@{host_part}"


__all__ = [
    "dispose_engines",
    "get_db",
    "get_engine",
    "get_session_factory",
]
