"""Safe structured query compiler and opaque cursor codec."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnClause,
    and_,
    asc,
    bindparam,
    column,
    desc,
    func,
    or_,
    select,
    table,
    tuple_,
)
from sqlalchemy.sql import Select
from sqlalchemy.sql.elements import BindParameter

from omnibase.capability_gateway.contracts import BooleanFilter, CompareFilter, ReadQuery

_PHYSICAL_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,62}$")


class QueryContractError(Exception):
    """Raised for a forged logical identifier or malformed cursor/locator."""


@dataclass(frozen=True)
class ColumnBinding:
    logical_id: str
    physical_name: str
    display_name: str
    data_type: str
    nullable: bool


@dataclass(frozen=True)
class PostgresBinding:
    schema: str
    table: str
    columns: dict[str, ColumnBinding]


@dataclass(frozen=True)
class CursorScope:
    tenant_id: str
    resource_id: str
    resource_version: int
    query_hash: str


class CursorCodec:
    """HMAC-authenticated offset cursor with no physical identifiers."""

    def __init__(self, secret: bytes) -> None:
        if len(secret) < 32:
            raise ValueError("cursor secret must contain at least 32 bytes")
        self._secret = secret

    def encode(self, offset: int, scope: CursorScope) -> str:
        payload = json.dumps(
            {
                "v": 1,
                "o": offset,
                "t": scope.tenant_id,
                "r": scope.resource_id,
                "rv": scope.resource_version,
                "q": scope.query_hash,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode()
        signature = hmac.new(self._secret, payload, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(payload + signature).decode().rstrip("=")

    def decode(self, cursor: str | None, scope: CursorScope) -> int:
        if cursor is None:
            return 0
        try:
            raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            payload, signature = raw[:-32], raw[-32:]
            expected = hmac.new(self._secret, payload, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise QueryContractError
            decoded = json.loads(payload)
            if set(decoded) != {"v", "o", "t", "r", "rv", "q"} or decoded["v"] != 1:
                raise QueryContractError
            if (
                decoded["t"] != scope.tenant_id
                or decoded["r"] != scope.resource_id
                or decoded["rv"] != scope.resource_version
                or decoded["q"] != scope.query_hash
            ):
                raise QueryContractError
            offset = decoded["o"]
            if (
                isinstance(offset, bool)
                or not isinstance(offset, int)
                or not 0 <= offset <= 100_000
            ):
                raise QueryContractError
            return offset
        except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise QueryContractError from exc


def parse_postgres_binding(locator: dict[str, object]) -> PostgresBinding:
    """Validate the server-owned locator before it reaches SQLAlchemy."""
    if locator.get("adapter") != "postgres":
        raise QueryContractError
    schema = locator.get("schema")
    table_name = locator.get("table")
    raw_columns = locator.get("columns")
    if not isinstance(schema, str) or not _PHYSICAL_IDENTIFIER.fullmatch(schema):
        raise QueryContractError
    if not isinstance(table_name, str) or not _PHYSICAL_IDENTIFIER.fullmatch(table_name):
        raise QueryContractError
    if not isinstance(raw_columns, dict) or not raw_columns:
        raise QueryContractError
    columns: dict[str, ColumnBinding] = {}
    for logical_id, raw in raw_columns.items():
        if not isinstance(logical_id, str) or not isinstance(raw, dict):
            raise QueryContractError
        physical_name = raw.get("name")
        display_name = raw.get("display_name")
        data_type = raw.get("type")
        nullable = raw.get("nullable")
        if not isinstance(physical_name, str) or not _PHYSICAL_IDENTIFIER.fullmatch(physical_name):
            raise QueryContractError
        if not isinstance(display_name, str) or not 1 <= len(display_name) <= 200:
            raise QueryContractError
        if not isinstance(data_type, str) or not 1 <= len(data_type) <= 64:
            raise QueryContractError
        data_type = data_type.strip().casefold()
        if data_type not in {
            "text",
            "varchar",
            "character varying",
            "uuid",
            "boolean",
            "smallint",
            "integer",
            "bigint",
            "numeric",
            "decimal",
            "real",
            "double precision",
            "date",
            "timestamp",
            "timestamp with time zone",
            "timestamp without time zone",
        }:
            raise QueryContractError
        if not isinstance(nullable, bool):
            raise QueryContractError
        columns[logical_id] = ColumnBinding(
            logical_id=logical_id,
            physical_name=physical_name,
            display_name=display_name,
            data_type=data_type,
            nullable=nullable,
        )
    return PostgresBinding(schema=schema, table=table_name, columns=columns)


def query_hash(query: ReadQuery) -> str:
    payload = query.model_dump(mode="json", exclude={"cursor"})
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compile_select(
    binding: PostgresBinding,
    query: ReadQuery,
    *,
    offset: int,
    size_only: bool = False,
    limit_override: int | None = None,
) -> Select[Any]:
    """Compile logical-column AST into bound SQLAlchemy expressions."""
    source = table(
        binding.table,
        *(column(item.physical_name) for item in binding.columns.values()),
        schema=binding.schema,
    )
    physical_by_name = {item.name: item for item in source.c}

    def resolve(logical_id: object) -> ColumnClause[Any]:
        item = binding.columns.get(str(logical_id))
        if item is None:
            raise QueryContractError
        return physical_by_name[item.physical_name]

    selected = [resolve(item) for item in query.columns]
    statement = (
        select(func.pg_column_size(tuple_(*selected))) if size_only else select(*selected)
    ).select_from(source)
    params: dict[str, object] = {}
    counter = 0

    def predicate(node: CompareFilter | BooleanFilter) -> Any:
        nonlocal counter
        if isinstance(node, BooleanFilter):
            children = [predicate(item) for item in node.clauses]
            return and_(*children) if node.kind == "and" else or_(*children)
        target = resolve(node.column_id)
        column_binding = binding.columns[str(node.column_id)]
        if node.op == "is_null":
            return target.is_(None) if node.value else target.is_not(None)
        _validate_filter_value(column_binding, node.op, node.value)
        counter += 1
        name = f"p_{counter}"
        params[name] = node.value
        value: BindParameter[object] = bindparam(name, expanding=node.op == "in")
        return {
            "eq": target == value,
            "ne": target != value,
            "lt": target < value,
            "lte": target <= value,
            "gt": target > value,
            "gte": target >= value,
            "in": target.in_(value),
        }[node.op]

    if query.filter is not None:
        statement = statement.where(predicate(query.filter))
    for item in query.order_by:
        target = resolve(item.column_id)
        statement = statement.order_by(asc(target) if item.direction == "asc" else desc(target))
    if not query.order_by:
        # A deterministic, server-resolved order is required for cursor paging.
        statement = statement.order_by(asc(selected[0]))
    result_limit = query.limit + 1 if limit_override is None else limit_override
    return statement.params(**params).offset(offset).limit(result_limit)


def _validate_filter_value(binding: ColumnBinding, operator: str, value: object) -> None:
    values = value if isinstance(value, list) else [value]
    data_type = binding.data_type
    allowed_operators = {"eq", "ne", "in"}
    if data_type not in {"boolean", "uuid"}:
        allowed_operators |= {"lt", "lte", "gt", "gte"}
    if operator not in allowed_operators:
        raise QueryContractError
    for item in values:
        if not _matches_data_type(data_type, item):
            raise QueryContractError


def _matches_data_type(data_type: str, value: object) -> bool:
    if value is None:
        return False
    if data_type == "boolean":
        return isinstance(value, bool)
    if data_type in {"smallint", "integer", "bigint"}:
        return isinstance(value, int) and not isinstance(value, bool)
    if data_type in {"numeric", "decimal", "real", "double precision"}:
        return _is_finite_number(value)
    if data_type == "uuid":
        return _is_uuid(value)
    if data_type == "date":
        return _is_iso_date(value)
    if data_type.startswith("timestamp"):
        return _is_iso_datetime(value)
    return isinstance(value, str)


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    )


def _is_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        UUID(value)
        return True
    except ValueError:
        return False


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _is_iso_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
        return True
    except ValueError:
        return False


__all__ = [
    "ColumnBinding",
    "CursorCodec",
    "CursorScope",
    "PostgresBinding",
    "QueryContractError",
    "compile_select",
    "parse_postgres_binding",
    "query_hash",
]
