"""Dependency-free public models for the P34.2 workload gateway."""

from __future__ import annotations

import base64
import binascii
import hashlib
import math
from dataclasses import dataclass
from typing import Any, TypeAlias
from uuid import UUID

JsonPrimitive: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonPrimitive | list["JsonValue"] | dict[str, "JsonValue"]

_FORBIDDEN_REQUEST_KEYS = frozenset(
    {
        "database_url",
        "minio_key",
        "object_key",
        "path",
        "physical_locator",
        "provider_handle",
        "raw_sql",
        "schema",
        "sql",
        "statement",
        "table",
        "tenant_id",
        "token",
        "workspace_id",
    }
)


class GatewayError(RuntimeError):
    """A safe gateway error with the response request ID attached."""

    def __init__(self, status_code: int, code: str, message: str, request_id: str | None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(f"{code}: {message} (request_id={request_id or 'unavailable'})")


def require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def require_integer(value: Any, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def require_number(value: Any, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
    ):
        raise ValueError(f"{label} must be a number")
    return float(value)


def require_boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be a boolean")
    return value


def require_exact_keys(
    value: dict[str, Any], required: set[str], optional: set[str], label: str
) -> None:
    missing = required - value.keys()
    extra = value.keys() - required - optional
    if missing or extra:
        raise ValueError(
            f"Invalid {label} fields; missing={sorted(missing)}, extra={sorted(extra)}"
        )


def require_logical_id(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be an opaque UUID")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise ValueError(f"{label} must be an opaque UUID") from exc


def reject_forbidden_request_keys(value: Any) -> None:
    """Reject locator/credential escape hatches without inspecting user data values."""

    if isinstance(value, dict):
        for key, nested in value.items():
            if key.casefold() in _FORBIDDEN_REQUEST_KEYS:
                raise ValueError(f"Gateway requests do not accept {key!r}")
            reject_forbidden_request_keys(nested)
    elif isinstance(value, list):
        for nested in value:
            reject_forbidden_request_keys(nested)


def require_json_value(value: Any, label: str, *, depth: int = 0) -> JsonValue:
    if depth > 8:
        raise ValueError(f"{label} exceeds the maximum nesting depth")
    if value is None or isinstance(value, str | bool | int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{label} must not contain NaN or Infinity")
        return value
    if isinstance(value, list):
        return [require_json_value(item, label, depth=depth + 1) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {
            key: require_json_value(item, label, depth=depth + 1) for key, item in value.items()
        }
    raise ValueError(f"{label} must contain JSON values only")


@dataclass(frozen=True, slots=True)
class OrderBy:
    column_id: str
    direction: str = "asc"

    def to_payload(self) -> dict[str, JsonValue]:
        require_logical_id(self.column_id, "column_id")
        if self.direction not in {"asc", "desc"}:
            raise ValueError("direction must be 'asc' or 'desc'")
        return {"column_id": self.column_id, "direction": self.direction}


@dataclass(frozen=True, slots=True)
class RowsQuery:
    columns: tuple[str, ...]
    filter: dict[str, JsonValue] | None = None
    order_by: tuple[OrderBy, ...] = ()
    cursor: str | None = None
    limit: int = 50
    timeout_ms: int | None = None
    max_bytes: int | None = None

    def to_payload(self) -> dict[str, JsonValue]:
        if not self.columns:
            raise ValueError("columns must contain at least one logical column ID")
        columns = [require_logical_id(column, "column_id") for column in self.columns]
        if len(columns) > 50:
            raise ValueError("columns cannot contain more than 50 IDs")
        if len(set(columns)) != len(columns):
            raise ValueError("columns must not contain duplicates")
        limit = require_integer(self.limit, "limit", minimum=1)
        if limit > 100:
            raise ValueError("limit must be between 1 and 100")
        column_values: list[JsonValue] = list(columns)
        payload: dict[str, JsonValue] = {"columns": column_values, "limit": limit}
        if self.filter is not None:
            reject_forbidden_request_keys(self.filter)
            payload["filter"] = require_json_value(self.filter, "filter")
        if self.order_by:
            if len(self.order_by) > 5:
                raise ValueError("order_by cannot contain more than 5 fields")
            order_values: list[JsonValue] = []
            order_values.extend(item.to_payload() for item in self.order_by)
            payload["order_by"] = order_values
        if self.cursor is not None:
            if not isinstance(self.cursor, str):
                raise ValueError("cursor must be an opaque string")
            if not 16 <= len(self.cursor) <= 512:
                raise ValueError("cursor must be an opaque string between 16 and 512 characters")
            payload["cursor"] = self.cursor
        if self.timeout_ms is not None:
            timeout_ms = require_integer(self.timeout_ms, "timeout_ms", minimum=1)
            if timeout_ms > 5000:
                raise ValueError("timeout_ms must be between 1 and 5000")
            payload["timeout_ms"] = timeout_ms
        if self.max_bytes is not None:
            max_bytes = require_integer(self.max_bytes, "max_bytes", minimum=1)
            if max_bytes > 1_048_576:
                raise ValueError("max_bytes must be between 1 and 1048576")
            payload["max_bytes"] = max_bytes
        return payload


@dataclass(frozen=True, slots=True)
class SchemaColumn:
    id: str
    display_name: str
    type: str
    nullable: bool

    @classmethod
    def from_dict(cls, raw: Any) -> SchemaColumn:
        value = require_mapping(raw, "schema column")
        require_exact_keys(value, {"id", "display_name", "type", "nullable"}, set(), "column")
        return cls(
            id=require_logical_id(value["id"], "column.id"),
            display_name=require_string(value["display_name"], "display_name"),
            type=require_string(value["type"], "type"),
            nullable=require_boolean(value["nullable"], "nullable"),
        )


@dataclass(frozen=True, slots=True)
class SchemaReadResponse:
    resource_id: str
    resource_version: int
    columns: tuple[SchemaColumn, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> SchemaReadResponse:
        value = require_mapping(raw, "schema response")
        require_exact_keys(value, {"resource_id", "resource_version", "columns"}, set(), "schema")
        if not isinstance(value["columns"], list):
            raise ValueError("columns must be an array")
        return cls(
            resource_id=require_logical_id(value["resource_id"], "resource_id"),
            resource_version=require_integer(
                value["resource_version"], "resource_version", minimum=1
            ),
            columns=tuple(SchemaColumn.from_dict(item) for item in value["columns"]),
        )


@dataclass(frozen=True, slots=True)
class RowsReadResponse:
    resource_id: str
    resource_version: int
    rows: tuple[dict[str, JsonValue], ...]
    next_cursor: str | None
    row_count: int
    bytes_out: int
    truncated: bool

    @classmethod
    def from_dict(cls, raw: Any) -> RowsReadResponse:
        value = require_mapping(raw, "rows response")
        require_exact_keys(
            value,
            {"resource_id", "resource_version", "rows", "row_count", "bytes_out", "truncated"},
            {"next_cursor"},
            "rows",
        )
        if not isinstance(value["rows"], list):
            raise ValueError("rows must be an array")
        rows = tuple(
            require_json_value(require_mapping(row, "row"), "row") for row in value["rows"]
        )
        cursor = value.get("next_cursor")
        if cursor is not None and not isinstance(cursor, str):
            raise ValueError("next_cursor must be an opaque string")
        return cls(
            resource_id=require_logical_id(value["resource_id"], "resource_id"),
            resource_version=require_integer(
                value["resource_version"], "resource_version", minimum=1
            ),
            rows=rows,  # type: ignore[arg-type]
            next_cursor=cursor,
            row_count=require_integer(value["row_count"], "row_count"),
            bytes_out=require_integer(value["bytes_out"], "bytes_out"),
            truncated=require_boolean(value["truncated"], "truncated"),
        )


@dataclass(frozen=True, slots=True)
class RagSearchResult:
    citation_id: str
    document_id: str
    score: float
    snippet: str | None = None
    page_number: int | None = None

    @classmethod
    def from_dict(cls, raw: Any) -> RagSearchResult:
        value = require_mapping(raw, "search result")
        require_exact_keys(
            value,
            {"citation_id", "document_id", "score"},
            {"snippet", "page_number"},
            "search result",
        )
        return cls(
            citation_id=require_logical_id(value["citation_id"], "citation_id"),
            document_id=require_logical_id(value["document_id"], "document_id"),
            score=require_number(value["score"], "score"),
            snippet=(
                require_string(value["snippet"], "snippet")
                if value.get("snippet") is not None
                else None
            ),
            page_number=(
                require_integer(value["page_number"], "page_number", minimum=1)
                if value.get("page_number") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class RagSearchResponse:
    resource_id: str
    results: tuple[RagSearchResult, ...]
    total_found: int
    bytes_out: int
    truncated: bool

    @classmethod
    def from_dict(cls, raw: Any) -> RagSearchResponse:
        value = require_mapping(raw, "search response")
        require_exact_keys(
            value,
            {"resource_id", "results", "total_found", "bytes_out", "truncated"},
            set(),
            "search",
        )
        if not isinstance(value["results"], list):
            raise ValueError("results must be an array")
        return cls(
            resource_id=require_logical_id(value["resource_id"], "resource_id"),
            results=tuple(RagSearchResult.from_dict(item) for item in value["results"]),
            total_found=require_integer(value["total_found"], "total_found"),
            bytes_out=require_integer(value["bytes_out"], "bytes_out"),
            truncated=require_boolean(value["truncated"], "truncated"),
        )


@dataclass(frozen=True, slots=True)
class Citation:
    citation_id: str
    document_id: str
    content: str
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None

    @classmethod
    def from_dict(cls, raw: Any) -> Citation:
        value = require_mapping(raw, "citation")
        require_exact_keys(
            value,
            {"citation_id", "document_id", "content"},
            {"page_number", "char_start", "char_end"},
            "citation",
        )
        return cls(
            citation_id=require_logical_id(value["citation_id"], "citation_id"),
            document_id=require_logical_id(value["document_id"], "document_id"),
            content=require_string(value["content"], "content"),
            page_number=(
                require_integer(value["page_number"], "page_number", minimum=1)
                if value.get("page_number") is not None
                else None
            ),
            char_start=(
                require_integer(value["char_start"], "char_start")
                if value.get("char_start") is not None
                else None
            ),
            char_end=(
                require_integer(value["char_end"], "char_end")
                if value.get("char_end") is not None
                else None
            ),
        )


@dataclass(frozen=True, slots=True)
class CitationReadResponse:
    resource_id: str
    citations: tuple[Citation, ...]
    bytes_out: int
    truncated: bool

    @classmethod
    def from_dict(cls, raw: Any) -> CitationReadResponse:
        value = require_mapping(raw, "citation response")
        require_exact_keys(
            value,
            {"resource_id", "citations", "bytes_out", "truncated"},
            set(),
            "citations",
        )
        if not isinstance(value["citations"], list):
            raise ValueError("citations must be an array")
        return cls(
            resource_id=require_logical_id(value["resource_id"], "resource_id"),
            citations=tuple(Citation.from_dict(item) for item in value["citations"]),
            bytes_out=require_integer(value["bytes_out"], "bytes_out"),
            truncated=require_boolean(value["truncated"], "truncated"),
        )


def require_sha256(value: Any, label: str) -> str:
    digest = require_string(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def require_media_type(value: Any) -> str:
    media_type = require_string(value, "media_type")
    if (
        "/" not in media_type
        or len(media_type) > 127
        or any(character.isspace() for character in media_type)
    ):
        raise ValueError("media_type is invalid")
    return media_type


@dataclass(frozen=True, slots=True)
class ArtifactReadResponse:
    resource_id: str
    resource_version: int
    media_type: str
    size_bytes: int
    content_sha256: str
    content: bytes
    bytes_out: int

    @classmethod
    def from_dict(cls, raw: Any) -> ArtifactReadResponse:
        value = require_mapping(raw, "artifact read response")
        require_exact_keys(
            value,
            {
                "resource_id",
                "resource_version",
                "media_type",
                "size_bytes",
                "content_sha256",
                "content_base64",
                "bytes_out",
            },
            set(),
            "artifact read",
        )
        encoded = require_string(value["content_base64"], "content_base64")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("content_base64 must be canonical base64") from exc
        if base64.b64encode(content).decode("ascii") != encoded:
            raise ValueError("content_base64 must be canonical base64")
        size_bytes = require_integer(value["size_bytes"], "size_bytes")
        digest = require_sha256(value["content_sha256"], "content_sha256")
        if len(content) != size_bytes:
            raise ValueError("artifact size_bytes does not match content")
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError("artifact content_sha256 does not match content")
        return cls(
            resource_id=require_logical_id(value["resource_id"], "resource_id"),
            resource_version=require_integer(
                value["resource_version"], "resource_version", minimum=1
            ),
            media_type=require_media_type(value["media_type"]),
            size_bytes=size_bytes,
            content_sha256=digest,
            content=content,
            bytes_out=require_integer(value["bytes_out"], "bytes_out"),
        )


@dataclass(frozen=True, slots=True)
class ArtifactWriteResponse:
    operation_id: str
    resource_id: str
    resource_version: int
    media_type: str
    size_bytes: int
    content_sha256: str
    replayed: bool
    request_id: str

    @classmethod
    def from_dict(cls, raw: Any) -> ArtifactWriteResponse:
        value = require_mapping(raw, "artifact write response")
        require_exact_keys(
            value,
            {
                "operation_id",
                "resource_id",
                "resource_version",
                "media_type",
                "size_bytes",
                "content_sha256",
                "replayed",
                "request_id",
            },
            set(),
            "artifact write",
        )
        return cls(
            operation_id=require_logical_id(value["operation_id"], "operation_id"),
            resource_id=require_logical_id(value["resource_id"], "resource_id"),
            resource_version=require_integer(
                value["resource_version"], "resource_version", minimum=1
            ),
            media_type=require_media_type(value["media_type"]),
            size_bytes=require_integer(value["size_bytes"], "size_bytes"),
            content_sha256=require_sha256(value["content_sha256"], "content_sha256"),
            replayed=require_boolean(value["replayed"], "replayed"),
            request_id=require_string(value["request_id"], "request_id"),
        )


@dataclass(frozen=True, slots=True)
class DerivedChunkWrite:
    content: str
    source_resource_id: str
    chunk_type: str = "paragraph"
    page_number: int | None = None
    char_start: int | None = None
    char_end: int | None = None

    def to_payload(self, declared_sources: frozenset[str]) -> dict[str, JsonValue]:
        if not isinstance(self.content, str) or not 1 <= len(self.content) <= 8000:
            raise ValueError("chunk content must contain between 1 and 8000 characters")
        source_resource_id = require_logical_id(self.source_resource_id, "source_resource_id")
        if source_resource_id not in declared_sources:
            raise ValueError("every chunk source must be declared")
        if self.chunk_type not in {"paragraph", "code", "table", "summary"}:
            raise ValueError("chunk_type is invalid")
        payload: dict[str, JsonValue] = {
            "content": self.content,
            "source_resource_id": source_resource_id,
            "chunk_type": self.chunk_type,
        }
        if self.page_number is not None:
            page_number = require_integer(self.page_number, "page_number", minimum=1)
            if page_number > 1_000_000:
                raise ValueError("page_number is too large")
            payload["page_number"] = page_number
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("char_start and char_end must be supplied together")
        if self.char_start is not None and self.char_end is not None:
            start = require_integer(self.char_start, "char_start")
            end = require_integer(self.char_end, "char_end")
            if end < start:
                raise ValueError("char_end cannot precede char_start")
            payload["char_start"] = start
            payload["char_end"] = end
        return payload


@dataclass(frozen=True, slots=True)
class DerivedCreateResponse:
    operation_id: str
    resource_id: str
    resource_version: int
    chunk_count: int
    replayed: bool
    request_id: str

    @classmethod
    def from_dict(cls, raw: Any) -> DerivedCreateResponse:
        value = require_mapping(raw, "derived create response")
        require_exact_keys(
            value,
            {
                "operation_id",
                "resource_id",
                "resource_version",
                "chunk_count",
                "replayed",
                "request_id",
            },
            set(),
            "derived create",
        )
        return cls(
            operation_id=require_logical_id(value["operation_id"], "operation_id"),
            resource_id=require_logical_id(value["resource_id"], "resource_id"),
            resource_version=require_integer(
                value["resource_version"], "resource_version", minimum=1
            ),
            chunk_count=require_integer(value["chunk_count"], "chunk_count", minimum=1),
            replayed=require_boolean(value["replayed"], "replayed"),
            request_id=require_string(value["request_id"], "request_id"),
        )


@dataclass(frozen=True, slots=True)
class DerivedDeleteResponse:
    operation_id: str
    resource_id: str
    resource_version: int
    deleted: bool
    replayed: bool
    request_id: str

    @classmethod
    def from_dict(cls, raw: Any) -> DerivedDeleteResponse:
        value = require_mapping(raw, "derived delete response")
        require_exact_keys(
            value,
            {
                "operation_id",
                "resource_id",
                "resource_version",
                "deleted",
                "replayed",
                "request_id",
            },
            set(),
            "derived delete",
        )
        return cls(
            operation_id=require_logical_id(value["operation_id"], "operation_id"),
            resource_id=require_logical_id(value["resource_id"], "resource_id"),
            resource_version=require_integer(
                value["resource_version"], "resource_version", minimum=1
            ),
            deleted=require_boolean(value["deleted"], "deleted"),
            replayed=require_boolean(value["replayed"], "replayed"),
            request_id=require_string(value["request_id"], "request_id"),
        )
