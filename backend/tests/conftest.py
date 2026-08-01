"""Pytest configuration and fixtures (Phase 0 minimal).

This conftest sets up:
- A temporary working directory
- A test settings instance with in-memory / throwaway resources
- Logging configured for tests

Phase 1 will add:
- An isolated test database schema (created per-test-session)
- MinIO mock / test container
- Redis mock
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Ensure src/ is on path for test imports
BACKEND_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = BACKEND_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


@pytest.fixture(scope="session", autouse=True)
def _configure_test_env() -> Iterator[None]:
    """Set minimal env vars so Settings() can be constructed during tests.

    Tests that need different values can monkeypatch these directly.
    """
    os.environ.setdefault("ENV", "development")
    os.environ.setdefault("LOG_LEVEL", "WARNING")
    os.environ.setdefault(
        "DATABASE_URL",
        "postgresql+psycopg://omnibase:secret@localhost:5432/omnibase_test",
    )
    os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
    os.environ.setdefault("MINIO_ACCESS_KEY", "test_access")
    os.environ.setdefault("MINIO_SECRET_KEY", "test_secret")
    os.environ.setdefault("MINIO_BUCKET", "omnibase-test")
    os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")
    os.environ.setdefault("RATE_LIMIT_ENABLED", "false")
    # 32+ char placeholder for JWT_SECRET (validation requires min_length=32)
    os.environ.setdefault(
        "JWT_SECRET",
        "test_secret_at_least_32_characters_long_for_validation",
    )

    # Clear cached settings so new env values are picked up
    from omnibase.core.config import get_settings

    get_settings.cache_clear()

    yield

    get_settings.cache_clear()


@pytest.fixture
def settings():  # type: ignore[no-untyped-def]
    """Return freshly-cached settings (test instance)."""
    from omnibase.core.config import get_settings

    return get_settings()
