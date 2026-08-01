"""Deterministic physical identifiers for P34.3 controlled data.

Only logical UUIDs may influence physical PostgreSQL identifiers.  Human
display names are deliberately absent from this module so callers cannot
accidentally turn user-controlled text into an identifier.
"""

from __future__ import annotations

import re
from uuid import UUID

_PHYSICAL_IDENTIFIER = re.compile(r"^(?:odt|odc|odi)_[0-9a-f]{32}$")


def _uuid_hex(value: str | UUID) -> str:
    """Return canonical lowercase UUID hex or reject the logical identifier."""
    try:
        parsed = value if isinstance(value, UUID) else UUID(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("logical identifier must be a UUID") from exc
    return parsed.hex


def table_identifier(logical_id: str | UUID) -> str:
    """Build the physical table identifier for one logical table UUID."""
    return f"odt_{_uuid_hex(logical_id)}"


def column_identifier(logical_id: str | UUID) -> str:
    """Build the physical column identifier for one logical column UUID."""
    return f"odc_{_uuid_hex(logical_id)}"


def index_identifier(logical_id: str | UUID) -> str:
    """Build the physical index identifier for one logical index UUID."""
    return f"odi_{_uuid_hex(logical_id)}"


def is_controlled_identifier(value: str) -> bool:
    """Return whether *value* is an exact OmniBase controlled identifier."""
    return _PHYSICAL_IDENTIFIER.fullmatch(value) is not None


__all__ = [
    "column_identifier",
    "index_identifier",
    "is_controlled_identifier",
    "table_identifier",
]
