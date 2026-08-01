"""Focused tests for embedding index migration settings."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from omnibase.core.config import Settings
from omnibase.rag.index_metadata import IndexVersion

_REQUIRED = {
    "database_url": "postgresql+psycopg://test:test@localhost/test",
    "minio_endpoint": "localhost:9000",
    "minio_access_key": "test",
    "minio_secret_key": "test-secret",
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret": "x" * 32,
}


def _settings(**overrides: object) -> Settings:
    return Settings(_env_file=None, **_REQUIRED, **overrides)  # type: ignore[call-arg]


def test_embedding_write_lane_defaults_preserve_v1_and_disable_shadow() -> None:
    settings = _settings()

    assert settings.embedding_index_version is IndexVersion.V1
    assert settings.embedding_shadow_index_version is None


def test_embedding_write_lanes_accept_v1_primary_and_v2_shadow() -> None:
    settings = _settings(
        embedding_index_version="v1",
        embedding_shadow_index_version="v2",
    )

    assert settings.embedding_index_version is IndexVersion.V1
    assert settings.embedding_shadow_index_version is IndexVersion.V2


def test_embedding_write_lanes_reject_duplicate_shadow() -> None:
    with pytest.raises(ValidationError, match="must differ"):
        _settings(
            embedding_index_version="v1",
            embedding_shadow_index_version="v1",
        )


def test_embedding_primary_rejects_v2_before_cutover_gate() -> None:
    with pytest.raises(ValidationError, match="must remain v1"):
        _settings(embedding_index_version="v2")


def test_embedding_index_version_rejects_unknown_contract() -> None:
    with pytest.raises(ValidationError):
        _settings(embedding_index_version="v3")
