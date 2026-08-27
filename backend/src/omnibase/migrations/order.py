"""Fail-closed migration ordering decisions shared by Alembic and tests."""

from __future__ import annotations

from collections.abc import Sequence


def is_exact_0016_to_0015_downgrade_cli(arguments: Sequence[str]) -> bool:
    """Return whether arguments authorize the reviewed tenant-first path."""
    return tuple(arguments) == ("downgrade", "0015")
