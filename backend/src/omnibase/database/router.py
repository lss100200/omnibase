"""Database router for an allowlisted, metadata-only table browser.

Raw SQL execution is intentionally not exposed over HTTP. The tenant search path
is a name-resolution convenience, not an authorization boundary.
"""

from __future__ import annotations

from fastapi import Depends
from fastapi.routing import APIRouter
from sqlalchemy import text
from sqlalchemy.orm import Session

from omnibase.core.logging import get_logger
from omnibase.database.schemas import (
    TableColumn,
    TableInfo,
    TablesListResponse,
)
from omnibase.tenants.dependencies import TenantContext, get_current_tenant, get_tenant_db

router = APIRouter(prefix="/database", tags=["database"])
log = get_logger(__name__)

# The browser is intentionally metadata-only and allowlisted so future tables or
# columns cannot become visible by accident.
_VISIBLE_TABLE_COLUMNS = {
    "documents": {
        "id",
        "filename",
        "mime_type",
        "size_bytes",
        "status",
        "page_count",
        "created_at",
        "updated_at",
    }
}


# -----------------------------------------------------------
# GET /api/v1/database/tables
# -----------------------------------------------------------
@router.get(
    "/tables",
    response_model=TablesListResponse,
    summary="List tables in the current tenant schema",
    description="Returns all base tables + their columns in the schema resolved from the caller's JWT.",
)
def list_tables(
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> TablesListResponse:
    """List tables + columns in the current tenant schema."""
    schema_name = ctx.schema_name

    # 1. Fetch list of base tables in this schema
    list_stmt = text(
        """
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = :schema
          AND table_type = 'BASE TABLE'
        ORDER BY table_name
        """
    )
    result = db.execute(list_stmt, {"schema": schema_name})
    table_names: list[str] = [row[0] for row in result if row[0] in _VISIBLE_TABLE_COLUMNS]

    # 2. For each table, fetch columns + estimated row count
    tables: list[TableInfo] = []
    for table_name in table_names:
        cols_stmt = text(
            """
            SELECT column_name, data_type, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = :schema AND table_name = :table
            ORDER BY ordinal_position
            """
        )
        cols_result = db.execute(cols_stmt, {"schema": schema_name, "table": table_name})
        columns = [
            TableColumn(
                name=row[0],
                type=row[1],
                nullable=row[2] == "YES",
            )
            for row in cols_result
            if row[0] in _VISIBLE_TABLE_COLUMNS[table_name]
        ]

        # Estimated row count (pg_class.reltuples, updated by ANALYZE)
        count_stmt = text(
            """
            SELECT reltuples::bigint
            FROM pg_class
            WHERE relname = :table AND relnamespace = (
                SELECT oid FROM pg_namespace WHERE nspname = :schema
            )
            """
        )
        count_result = db.execute(count_stmt, {"table": table_name, "schema": schema_name})
        row_count = int(count_result.scalar() or 0)

        tables.append(
            TableInfo(
                name=table_name,
                columns=columns,
                row_count_estimate=row_count,
            )
        )

    log.debug("database.list_tables", schema=schema_name, count=len(tables))
    return TablesListResponse(tables=tables)


__all__ = ["router"]
