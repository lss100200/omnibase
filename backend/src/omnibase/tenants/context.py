"""Tenant context propagation via contextvars.

This module provides a request-scoped (and task-scoped) mechanism to track
which tenant schema is currently active, WITHOUT passing the schema string
through every function call.

How it works:
1. FastAPI dependency `get_current_tenant` sets the contextvar on request entry
2. SQLAlchemy event hook (in db.py) reads the contextvar whenever a session
   begins a transaction, and automatically SETs search_path
3. Celery tasks can set the contextvar manually (via `tenant_contextvar.set`)
   so background work is also properly isolated

Usage in FastAPI (automatic via dependency):
    @app.get("/items")
    def list_items(db: Session = Depends(get_tenant_db)):
        ...  # search_path already set from contextvar

Usage in Celery / scripts (manual):
    from omnibase.tenants.context import tenant_contextvar
    tenant_contextvar.set("tenant_abc123")
    # All subsequent DB operations use that schema
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

# The contextvar holds the active tenant's PostgreSQL schema name.
# Default is None (no tenant context - uses default search_path).
tenant_contextvar: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "omnibase_tenant_schema",
    default=None,
)


def get_current_schema() -> str | None:
    """Return the currently active tenant schema name, or None if not set."""
    return tenant_contextvar.get()


def set_current_schema(schema_name: str | None) -> contextvars.Token[str | None]:
    """Set the active tenant schema. Returns a token for restoring the previous value.

    Typical usage in FastAPI dependencies:
        token = set_current_schema(tenant.schema_name)
        try:
            yield ...
        finally:
            tenant_contextvar.reset(token)
    """
    return tenant_contextvar.set(schema_name)


def reset_schema(token: contextvars.Token[str | None]) -> None:
    """Reset the contextvar to its previous value (use with set_current_schema)."""
    tenant_contextvar.reset(token)


@contextmanager
def tenant_scope(schema_name: str) -> Iterator[None]:
    """Context manager that sets the tenant schema for the duration of the block.

    Usage:
        with tenant_scope("tenant_abc123"):
            # DB operations here are scoped to tenant_abc123
            ...
    """
    token = set_current_schema(schema_name)
    try:
        yield
    finally:
        reset_schema(token)


__all__ = [
    "get_current_schema",
    "reset_schema",
    "set_current_schema",
    "tenant_contextvar",
    "tenant_scope",
]
