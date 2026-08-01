"""Database models.

All models live in the `omnibase_meta` schema (the global / cross-tenant
namespace). Business per-tenant models (users, documents, chunks) will be
defined in their respective modules and resolved against the per-tenant
search_path at runtime.

Convention:
- Table name: snake_case, plural
- Primary key: UUID (as Server-side default uuid_generate_v4())
- Timestamps: created_at + updated_at, UTC
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, MetaData, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# -----------------------------------------------------------
# Global metadata: lives in omnibase_meta schema
# -----------------------------------------------------------
# Using a fixed MetaData with explicit schema keeps all global tables
# (currently just `tenants`) in the same place regardless of search_path.
GLOBAL_SCHEMA = "omnibase_meta"
GLOBAL_METADATA = MetaData(schema=GLOBAL_SCHEMA)

# Tenant schemas get their own MetaData so business models bind to it;
# the schema is set dynamically by the schema_manager (search_path trick).
TENANT_METADATA = MetaData(schema=None)  # schema resolved at runtime


class Base(DeclarativeBase):
    """Declarative base for GLOBAL tables (in omnibase_meta)."""

    metadata = GLOBAL_METADATA

    type_annotation_map = {datetime: DateTime(timezone=True)}


class Tenant(Base):
    """A single tenant (workspace). Each tenant owns a private PostgreSQL schema.

    Lifecycle:
    1. Row inserted into omnibase_meta.tenants
    2. CREATE SCHEMA tenant_<short_id>
    3. Run all tenant-scoped migrations against that schema
    """

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Human-readable tenant name"
    )
    # PostgreSQL schema name backing this tenant (e.g. 'tenant_abc123').
    # Pattern: tenant_<first 8 chars of id>. Always lowercase, ASCII-safe.
    schema_name: Mapped[str] = mapped_column(
        String(63),
        unique=True,
        nullable=False,
        index=True,
        comment="PostgreSQL schema name for this tenant's data",
    )
    # Slug used in URLs (e.g. omnibase.app/t/acme/dashboard).
    slug: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
        comment="URL-safe tenant identifier",
    )
    is_default: Mapped[bool] = mapped_column(
        default=False,
        nullable=False,
        index=True,
        comment="True for the auto-created tenant when a user registers",
    )
    is_active: Mapped[bool] = mapped_column(
        default=True,
        nullable=False,
        comment="Soft delete: inactive tenants reject all requests",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("now()"),
        onupdate=text("now()"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<Tenant id={self.id[:8]} schema={self.schema_name} slug={self.slug}>"


__all__ = ["GLOBAL_METADATA", "GLOBAL_SCHEMA", "TENANT_METADATA", "Base", "Tenant"]
