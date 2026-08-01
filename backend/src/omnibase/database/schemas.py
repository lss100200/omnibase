"""Schemas for the allowlisted database metadata browser."""

from __future__ import annotations

from pydantic import BaseModel, Field


class TableColumn(BaseModel):
    """A single column in a table."""

    name: str
    type: str = Field(..., description="PostgreSQL data type")
    nullable: bool


class TableInfo(BaseModel):
    """Metadata about a single table."""

    name: str
    columns: list[TableColumn]
    row_count_estimate: int = Field(
        ..., description="Estimated row count (from pg_class.reltuples)"
    )


class TablesListResponse(BaseModel):
    """Response for GET /api/database/tables."""

    tables: list[TableInfo]
