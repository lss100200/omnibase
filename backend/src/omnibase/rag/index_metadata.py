"""Immutable contracts for every supported embedding index version.

The registry in this module is deliberately closed: adding a new index version
requires a code change that defines its model and vector dimension together.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class IndexVersion(StrEnum):
    """Supported, persisted embedding index generation identifiers."""

    V1 = "v1"
    V2 = "v2"

    @classmethod
    def parse(cls, value: IndexVersion | int | str) -> IndexVersion:
        """Resolve an enum, legacy integer, or ``v1``/``v2`` string."""
        if isinstance(value, cls):
            return value
        if isinstance(value, int):
            value = f"v{value}"
        elif isinstance(value, str):
            normalized = value.strip().lower()
            value = normalized if normalized.startswith("v") else f"v{normalized}"
        try:
            return cls(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Unsupported index version: {value!r}") from exc

    @property
    def generation(self) -> int:
        """Return the legacy numeric generation stored in IndexMetadata.version."""
        return int(self.value[1:])

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class IndexMetadata:
    """Immutable identity contract for one vector index generation."""

    model_name: str
    dimension: int
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.version, int) or isinstance(self.version, bool):
            raise TypeError("version must be an integer")
        if not self.model_name.strip():
            raise ValueError("model_name must not be empty")
        if self.dimension <= 0:
            raise ValueError("dimension must be greater than zero")


@dataclass(frozen=True, slots=True)
class IndexLane:
    """Immutable routing lane binding a public version to index metadata."""

    version: IndexVersion
    metadata: IndexMetadata

    def __post_init__(self) -> None:
        if self.metadata.version != self.version.generation:
            raise ValueError(
                "Index lane version must match the metadata generation: "
                f"{self.version} != {self.metadata.version}"
            )


_V1_METADATA: Final[IndexMetadata] = IndexMetadata(
    model_name="BAAI/bge-small-zh-v1.5",
    dimension=512,
    version=IndexVersion.V1.generation,
)
_V2_METADATA: Final[IndexMetadata] = IndexMetadata(
    model_name="BAAI/bge-m3",
    dimension=1024,
    version=IndexVersion.V2.generation,
)

INDEX_REGISTRY: Final[Mapping[IndexVersion, IndexLane]] = MappingProxyType(
    {
        IndexVersion.V1: IndexLane(IndexVersion.V1, _V1_METADATA),
        IndexVersion.V2: IndexLane(IndexVersion.V2, _V2_METADATA),
    }
)
"""Closed, immutable registry of supported index contracts."""

# Preserve the Phase 1 public singleton and its exact v1 identity.
ACTIVE_METADATA: Final[IndexMetadata] = _V1_METADATA
"""Legacy active index identity. It remains v1 until read-path migration."""


def get_index_lane(version: IndexVersion | int | str) -> IndexLane:
    """Return the immutable routing lane for *version*."""
    return INDEX_REGISTRY[IndexVersion.parse(version)]


def get_index_metadata(version: IndexVersion | int | str) -> IndexMetadata:
    """Return the immutable metadata contract for *version*.

    Raises:
        ValueError: If the requested version is outside the closed registry.
    """
    return get_index_lane(version).metadata


def get_active_metadata() -> IndexMetadata:
    """Return the legacy active v1 metadata singleton."""
    return ACTIVE_METADATA


class DimensionMismatchError(ValueError):
    """Raised when a supplied vector dimension violates an index contract."""

    def __init__(self, expected: int, actual: int) -> None:
        self.expected = expected
        self.actual = actual
        super().__init__(f"Dimension mismatch: expected {expected}, got {actual}")


def validate_dimension(
    actual: int,
    version: IndexVersion | int | str = IndexVersion.V1,
) -> None:
    """Validate *actual* against a specific index version's dimension.

    The default remains v1 so existing callers retain their 512-dimensional
    contract exactly.
    """
    expected = get_index_metadata(version).dimension
    if actual != expected:
        raise DimensionMismatchError(expected=expected, actual=actual)


__all__ = [
    "ACTIVE_METADATA",
    "INDEX_REGISTRY",
    "DimensionMismatchError",
    "IndexLane",
    "IndexMetadata",
    "IndexVersion",
    "get_active_metadata",
    "get_index_lane",
    "get_index_metadata",
    "validate_dimension",
]
