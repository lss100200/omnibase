"""Shared ordering helpers for guarded multi-schema migration tests."""

from __future__ import annotations

import subprocess
from collections.abc import Callable

AlembicRunner = Callable[..., subprocess.CompletedProcess[str]]


def downgrade_0016_to_0015(run_alembic: AlembicRunner) -> None:
    """Apply the one reviewed tenant-first downgrade before older revisions."""
    result = run_alembic("downgrade", "0015")
    assert result.returncode == 0, result.stdout + result.stderr
