"""Closed logical type system for P34.3 controlled tables.

This module does not accept SQL type fragments.  It converts a small,
versioned logical allowlist into server-owned SQLAlchemy types.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal, cast

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql.type_api import TypeEngine

LogicalDataType = Literal[
    "string",
    "int64",
    "decimal",
    "boolean",
    "uuid",
    "date",
    "timestamp_tz",
]

ALLOWED_LOGICAL_TYPES = frozenset(
    {"string", "int64", "decimal", "boolean", "uuid", "date", "timestamp_tz"}
)
FORBIDDEN_LOGICAL_TYPES = frozenset(
    {
        "json",
        "jsonb",
        "array",
        "bytea",
        "vector",
        "enum",
        "domain",
    }
)


@dataclass(frozen=True, slots=True)
class ValidatedType:
    """Normalized logical type whose arguments have passed the closed contract."""

    name: LogicalDataType
    args: dict[str, int]


def _strict_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{field} must be an integer")
    return value


def validate_type_spec(name: str, args: dict[str, object] | None = None) -> ValidatedType:
    """Validate and normalize one logical data type without accepting SQL text."""
    raw_args = {} if args is None else dict(args)
    if name not in ALLOWED_LOGICAL_TYPES:
        raise ValueError(f"unsupported controlled data type: {name}")

    if name == "string":
        if set(raw_args) != {"max_length"}:
            raise ValueError("string type_args must contain only max_length")
        max_length = _strict_int(raw_args["max_length"], "max_length")
        if not 1 <= max_length <= 10_000:
            raise ValueError("max_length must be between 1 and 10000")
        return ValidatedType(name="string", args={"max_length": max_length})

    if name == "decimal":
        if set(raw_args) != {"precision", "scale"}:
            raise ValueError("decimal type_args must contain only precision and scale")
        precision = _strict_int(raw_args["precision"], "precision")
        scale = _strict_int(raw_args["scale"], "scale")
        if not 1 <= precision <= 38:
            raise ValueError("precision must be between 1 and 38")
        if not 0 <= scale <= precision:
            raise ValueError("scale must be between 0 and precision")
        return ValidatedType(
            name="decimal",
            args={"precision": precision, "scale": scale},
        )

    if raw_args:
        raise ValueError(f"{name} type_args must be empty")
    return ValidatedType(name=cast("LogicalDataType", name), args={})


ControlledSQLAlchemyType = (
    TypeEngine[str]
    | TypeEngine[int]
    | TypeEngine[Decimal]
    | TypeEngine[bool]
    | TypeEngine[date]
    | TypeEngine[datetime]
)


def sqlalchemy_type(spec: ValidatedType) -> ControlledSQLAlchemyType:
    """Map a validated logical type to a server-owned SQLAlchemy type."""
    if spec.name == "string":
        return String(spec.args["max_length"])
    if spec.name == "int64":
        return BigInteger()
    if spec.name == "decimal":
        return Numeric(spec.args["precision"], spec.args["scale"])
    if spec.name == "boolean":
        return Boolean()
    if spec.name == "uuid":
        return UUID(as_uuid=False)
    if spec.name == "date":
        return Date()
    if spec.name == "timestamp_tz":
        return DateTime(timezone=True)
    raise AssertionError("validated type escaped the closed allowlist")


ControlledScalar = str | int | Decimal | bool

__all__ = [
    "ALLOWED_LOGICAL_TYPES",
    "FORBIDDEN_LOGICAL_TYPES",
    "ControlledSQLAlchemyType",
    "ControlledScalar",
    "LogicalDataType",
    "ValidatedType",
    "sqlalchemy_type",
    "validate_type_spec",
]
