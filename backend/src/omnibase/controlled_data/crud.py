"""Pure, parameterized mutation planning for P34.3 controlled data.

This module never opens a database connection.  It validates a server-owned
logical/physical binding, normalizes typed values, produces SQLAlchemy Core
statements, and requires a later executor to re-check authorization and the
resource version inside its transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any
from uuid import UUID

from sqlalchemy import (
    String,
    and_,
    bindparam,
    cast,
    column,
    delete,
    insert,
    literal_column,
    select,
    table,
    update,
)
from sqlalchemy import or_ as sql_or
from sqlalchemy.sql import Delete, Insert, Select, Update
from sqlalchemy.sql.elements import BindParameter, ColumnClause
from sqlalchemy.sql.selectable import TableClause

from omnibase.controlled_data.crud_contracts import (
    MAX_MUTATION_PAYLOAD_BYTES,
    DeleteMutationRequest,
    FilteredMutationRequest,
    InsertMutationRequest,
    MutationBoolean,
    MutationCompare,
    MutationRequest,
    MutationScalar,
    UpdateMutationRequest,
)
from omnibase.controlled_data.identifiers import column_identifier, table_identifier
from omnibase.controlled_data.types import LogicalDataType, validate_type_spec
from omnibase.tenants.schema_manager import SchemaError, validate_schema_name

_ROW_TOKEN = re.compile(r"^\([0-9]{1,10},[0-9]{1,5}\)$")


class MutationContractError(ValueError):
    """Raised when a mutation attempts to escape the closed contract."""


class MutationVersionConflict(MutationContractError):
    """Raised when the requested resource version is stale or forged."""


class MutationBudgetExceeded(MutationContractError):
    """Raised before planning an oversized payload or row set."""


@dataclass(frozen=True, slots=True)
class MutationColumnBinding:
    """Trusted internal logical/physical column binding."""

    logical_id: UUID
    physical_name: str
    data_type: LogicalDataType
    type_args: Mapping[str, object]
    nullable: bool

    def __post_init__(self) -> None:
        if self.physical_name != column_identifier(self.logical_id):
            raise MutationContractError("column physical name is not deterministic")
        try:
            normalized = validate_type_spec(self.data_type, dict(self.type_args))
        except ValueError as exc:
            raise MutationContractError("column type binding is invalid") from exc
        object.__setattr__(self, "type_args", MappingProxyType(dict(normalized.args)))


@dataclass(frozen=True, slots=True)
class TrustedMutationLocator:
    """Server-owned table binding; never construct this object from a request DTO."""

    tenant_schema: str
    table_binding_id: UUID
    resource_id: UUID
    resource_version: int
    physical_table_name: str
    columns: Mapping[UUID, MutationColumnBinding]

    def __post_init__(self) -> None:
        try:
            validate_schema_name(self.tenant_schema)
        except SchemaError as exc:
            raise MutationContractError("tenant schema binding is invalid") from exc
        if self.physical_table_name != table_identifier(self.resource_id):
            raise MutationContractError("table physical name is not deterministic")
        if (
            isinstance(self.resource_version, bool)
            or not isinstance(self.resource_version, int)
            or self.resource_version < 1
        ):
            raise MutationContractError("resource version must be a positive integer")
        if not 1 <= len(self.columns) <= 128:
            raise MutationContractError("trusted locator requires 1 to 128 columns")
        for logical_id, item in self.columns.items():
            if logical_id != item.logical_id:
                raise MutationContractError("locator column map key mismatch")
        object.__setattr__(self, "columns", MappingProxyType(dict(self.columns)))


@dataclass(frozen=True, slots=True)
class PreparedInsert:
    """Bound insert statement plus execution controls for a later adapter."""

    statement: Insert
    request_hash: str
    idempotency_key: str
    resource_id: UUID
    resource_version: int
    timeout_ms: int
    row_count: int
    payload_bytes: int


@dataclass(frozen=True, slots=True)
class PreparedFilteredMutation:
    """Preflight plan that cannot mutate until bounded row tokens are supplied."""

    kind: str
    preflight: Select[Any]
    request_hash: str
    idempotency_key: str
    resource_id: UUID
    resource_version: int
    timeout_ms: int
    max_rows: int
    payload_bytes: int
    _source: TableClause
    _values: dict[ColumnClause[Any], object] | None

    def build_apply_statement(self, row_tokens: list[str]) -> Update | Delete:
        """Bind server-selected, locked CTID tokens to a bounded mutation."""
        if not 1 <= len(row_tokens) <= self.max_rows:
            raise MutationBudgetExceeded("target rows must be non-empty and within max_rows")
        if len(set(row_tokens)) != len(row_tokens):
            raise MutationContractError("target row tokens must be unique")
        if any(_ROW_TOKEN.fullmatch(item) is None for item in row_tokens):
            raise MutationContractError("target row token is malformed")
        target = cast(literal_column("ctid"), String).in_(
            bindparam("_target_row_tokens", value=row_tokens, expanding=True)
        )
        if self.kind == "update":
            if self._values is None:
                raise AssertionError("prepared update lost its value bindings")
            return update(self._source).where(target).values(self._values)
        if self.kind == "delete":
            return delete(self._source).where(target)
        raise AssertionError("unknown prepared mutation kind")


def normalize_idempotency_key(value: str) -> str:
    """Normalize an already validated idempotency key for durable scoping."""
    normalized = value.strip()
    if not 8 <= len(normalized) <= 128 or re.fullmatch(r"[A-Za-z0-9._:-]+", normalized) is None:
        raise MutationContractError("invalid idempotency key")
    return normalized


def canonical_request_hash(request: MutationRequest) -> str:
    """Hash the normalized request body, excluding the retry key itself."""
    payload = request.model_dump(mode="json", exclude={"idempotency_key"})
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prepare_insert(
    locator: TrustedMutationLocator,
    request: InsertMutationRequest,
) -> PreparedInsert:
    """Validate rows and build a fully parameterized bounded insert."""
    payload_bytes = _request_payload_bytes(request)
    _validate_scope_and_version(locator, request)
    source, physical = _source(locator)
    normalized_rows: list[dict[ColumnClause[Any], object]] = []
    for row in request.rows:
        resolved: dict[ColumnClause[Any], object] = {}
        for logical_id, value in row.items():
            binding = _resolve_column(locator, logical_id)
            resolved[physical[binding.physical_name]] = _normalize_value(binding, value)
        missing_required = {
            item.logical_id
            for item in locator.columns.values()
            if not item.nullable and item.logical_id not in row
        }
        if missing_required:
            raise MutationContractError("insert row omits required logical columns")
        normalized_rows.append(resolved)
    statement = insert(source).values(normalized_rows)
    return PreparedInsert(
        statement=statement,
        request_hash=canonical_request_hash(request),
        idempotency_key=normalize_idempotency_key(request.idempotency_key),
        resource_id=request.resource_id,
        resource_version=request.resource_version,
        timeout_ms=request.timeout_ms,
        row_count=len(normalized_rows),
        payload_bytes=payload_bytes,
    )


def prepare_update(
    locator: TrustedMutationLocator,
    request: UpdateMutationRequest,
) -> PreparedFilteredMutation:
    """Prepare a locked preflight and bounded follow-up update."""
    payload_bytes = _request_payload_bytes(request)
    _validate_scope_and_version(locator, request)
    source, physical = _source(locator)
    predicate, params = _compile_predicate(locator, physical, request.predicate)
    values = {
        physical[_resolve_column(locator, logical_id).physical_name]: _normalize_value(
            _resolve_column(locator, logical_id), value
        )
        for logical_id, value in request.values.items()
    }
    preflight = _preflight(source, predicate, params, request)
    return PreparedFilteredMutation(
        kind="update",
        preflight=preflight,
        request_hash=canonical_request_hash(request),
        idempotency_key=normalize_idempotency_key(request.idempotency_key),
        resource_id=request.resource_id,
        resource_version=request.resource_version,
        timeout_ms=request.timeout_ms,
        max_rows=request.max_rows,
        payload_bytes=payload_bytes,
        _source=source,
        _values=values,
    )


def prepare_delete(
    locator: TrustedMutationLocator,
    request: DeleteMutationRequest,
) -> PreparedFilteredMutation:
    """Prepare a locked preflight and bounded follow-up delete."""
    payload_bytes = _request_payload_bytes(request)
    _validate_scope_and_version(locator, request)
    source, physical = _source(locator)
    predicate, params = _compile_predicate(locator, physical, request.predicate)
    return PreparedFilteredMutation(
        kind="delete",
        preflight=_preflight(source, predicate, params, request),
        request_hash=canonical_request_hash(request),
        idempotency_key=normalize_idempotency_key(request.idempotency_key),
        resource_id=request.resource_id,
        resource_version=request.resource_version,
        timeout_ms=request.timeout_ms,
        max_rows=request.max_rows,
        payload_bytes=payload_bytes,
        _source=source,
        _values=None,
    )


def _request_payload_bytes(request: MutationRequest) -> int:
    encoded = json.dumps(
        request.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    size = len(encoded)
    if size > MAX_MUTATION_PAYLOAD_BYTES:
        raise MutationBudgetExceeded("mutation payload exceeds the hard byte limit")
    return size


def _validate_scope_and_version(
    locator: TrustedMutationLocator,
    request: MutationRequest,
) -> None:
    if request.resource_id != locator.resource_id:
        raise MutationContractError("mutation resource does not match trusted locator")
    if request.resource_version != locator.resource_version:
        raise MutationVersionConflict("resource version conflict")


def _source(
    locator: TrustedMutationLocator,
) -> tuple[TableClause, dict[str, ColumnClause[Any]]]:
    source = table(
        locator.physical_table_name,
        *(column(item.physical_name) for item in locator.columns.values()),
        schema=locator.tenant_schema,
    )
    return source, {item.name: item for item in source.c}


def _resolve_column(
    locator: TrustedMutationLocator,
    logical_id: UUID,
) -> MutationColumnBinding:
    binding = locator.columns.get(logical_id)
    if binding is None:
        raise MutationContractError("logical column is not bound to this resource")
    return binding


def _compile_predicate(
    locator: TrustedMutationLocator,
    physical: dict[str, ColumnClause[Any]],
    node: MutationCompare | MutationBoolean,
) -> tuple[Any, dict[str, object]]:
    params: dict[str, object] = {}
    counter = 0

    def walk(current: MutationCompare | MutationBoolean) -> Any:
        nonlocal counter
        if isinstance(current, MutationBoolean):
            children = [walk(item) for item in current.clauses]
            return and_(*children) if current.kind == "and" else sql_or(*children)
        binding = _resolve_column(locator, current.column_id)
        target = physical[binding.physical_name]
        if current.op == "is_null":
            return target.is_(None) if current.value else target.is_not(None)
        allowed = {"eq", "ne", "in"}
        if binding.data_type not in {"boolean", "uuid"}:
            allowed.update({"lt", "lte", "gt", "gte"})
        if current.op not in allowed:
            raise MutationContractError("operator is not allowed for the logical type")
        raw_values = current.value if isinstance(current.value, list) else [current.value]
        normalized = [_normalize_predicate_value(binding, value) for value in raw_values]
        counter += 1
        parameter_name = f"predicate_{counter}"
        value: object = normalized if current.op == "in" else normalized[0]
        params[parameter_name] = value
        parameter: BindParameter[Any] = bindparam(
            parameter_name,
            expanding=current.op == "in",
        )
        return {
            "eq": target == parameter,
            "ne": target != parameter,
            "lt": target < parameter,
            "lte": target <= parameter,
            "gt": target > parameter,
            "gte": target >= parameter,
            "in": target.in_(parameter),
        }[current.op]

    return walk(node), params


def _preflight(
    source: TableClause,
    predicate: Any,
    params: dict[str, object],
    request: FilteredMutationRequest,
) -> Select[Any]:
    row_token = cast(literal_column("ctid"), String).label("_row_token")
    return (
        select(row_token)
        .select_from(source)
        .where(predicate)
        .limit(request.max_rows + 1)
        .with_for_update()
        .params(**params)
    )


def _normalize_predicate_value(
    binding: MutationColumnBinding,
    value: MutationScalar,
) -> object:
    if value is None:
        raise MutationContractError("null comparisons require is_null")
    return _normalize_non_null(binding, value)


def _normalize_value(binding: MutationColumnBinding, value: MutationScalar) -> object:
    if value is None:
        if not binding.nullable:
            raise MutationContractError("null is not allowed for this logical column")
        return None
    return _normalize_non_null(binding, value)


def _normalize_non_null(binding: MutationColumnBinding, value: MutationScalar) -> object:
    normalizer = _TYPE_NORMALIZERS.get(binding.data_type)
    if normalizer is None:
        raise AssertionError("trusted type binding escaped the closed allowlist")
    return normalizer(binding, value)


def _required_int_type_arg(binding: MutationColumnBinding, name: str) -> int:
    value = binding.type_args.get(name)
    if isinstance(value, bool) or not isinstance(value, int):
        raise MutationContractError("trusted type binding has an invalid integer argument")
    return value


def _normalize_string(binding: MutationColumnBinding, value: MutationScalar) -> object:
    if not isinstance(value, str):
        raise MutationContractError("string column requires a string")
    if len(value) > _required_int_type_arg(binding, "max_length"):
        raise MutationContractError("string value exceeds max_length")
    return value


def _normalize_int64(_binding: MutationColumnBinding, value: MutationScalar) -> object:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MutationContractError("int64 column requires an integer")
    if not -(2**63) <= value <= 2**63 - 1:
        raise MutationContractError("int64 value is out of range")
    return value


def _normalize_decimal(binding: MutationColumnBinding, value: MutationScalar) -> object:
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int)):
        raise MutationContractError("decimal column requires a canonical string or integer")
    try:
        normalized = Decimal(value)
    except InvalidOperation as exc:
        raise MutationContractError("decimal value is invalid") from exc
    if not normalized.is_finite():
        raise MutationContractError("decimal value must be finite")
    precision = _required_int_type_arg(binding, "precision")
    scale = _required_int_type_arg(binding, "scale")
    _, digits, exponent = normalized.as_tuple()
    if not isinstance(exponent, int):
        raise MutationContractError("decimal value has an invalid exponent")
    fractional_digits = max(-exponent, 0)
    integer_digits = max(len(digits) + exponent, 0)
    if fractional_digits > scale or integer_digits > precision - scale:
        raise MutationContractError("decimal value exceeds precision or scale")
    return normalized


def _normalize_boolean(_binding: MutationColumnBinding, value: MutationScalar) -> object:
    if not isinstance(value, bool):
        raise MutationContractError("boolean column requires a boolean")
    return value


def _normalize_uuid(_binding: MutationColumnBinding, value: MutationScalar) -> object:
    if not isinstance(value, str):
        raise MutationContractError("uuid column requires a UUID string")
    try:
        return str(UUID(value))
    except ValueError as exc:
        raise MutationContractError("uuid value is invalid") from exc


def _normalize_date(_binding: MutationColumnBinding, value: MutationScalar) -> object:
    if not isinstance(value, str):
        raise MutationContractError("date column requires an ISO date string")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise MutationContractError("date value is invalid") from exc


def _normalize_timestamp(_binding: MutationColumnBinding, value: MutationScalar) -> object:
    if not isinstance(value, str):
        raise MutationContractError("timestamp_tz requires an ISO timestamp string")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise MutationContractError("timestamp_tz value is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MutationContractError("timestamp_tz requires an explicit timezone")
    return parsed


_TYPE_NORMALIZERS = {
    "string": _normalize_string,
    "int64": _normalize_int64,
    "decimal": _normalize_decimal,
    "boolean": _normalize_boolean,
    "uuid": _normalize_uuid,
    "date": _normalize_date,
    "timestamp_tz": _normalize_timestamp,
}


__all__ = [
    "MutationBudgetExceeded",
    "MutationColumnBinding",
    "MutationContractError",
    "MutationVersionConflict",
    "PreparedFilteredMutation",
    "PreparedInsert",
    "TrustedMutationLocator",
    "canonical_request_hash",
    "normalize_idempotency_key",
    "prepare_delete",
    "prepare_insert",
    "prepare_update",
]
