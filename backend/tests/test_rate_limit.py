"""Unit tests for Redis-backed request rate limiting."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from redis.exceptions import RedisError

from omnibase.core.config import Settings
from omnibase.core.rate_limit import (
    RateLimitDecision,
    RateLimitPolicy,
    RedisRateLimiter,
    _enforce,
)


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://test:test@localhost/test",
        "minio_endpoint": "localhost:9000",
        "minio_access_key": "test",
        "minio_secret_key": "test-secret",
        "redis_url": "redis://localhost:6379/15",
        "jwt_secret": "test_secret_at_least_32_characters_long",
        "rate_limit_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


def test_redis_limiter_uses_hashed_identity_and_returns_decision() -> None:
    client = MagicMock()
    client.eval.return_value = [2, 43]
    decision = RedisRateLimiter(client).check(
        RateLimitPolicy(name="rag", limit=5, window_seconds=60),
        "tenant-id:user-id",
    )
    assert decision == RateLimitDecision(allowed=True, remaining=3, retry_after=43)
    key = client.eval.call_args.args[2]
    assert key.startswith("omnibase:rate-limit:rag:")
    assert "tenant-id" not in key
    assert "user-id" not in key
    assert client.eval.call_args.args[3] == "60"


@pytest.mark.parametrize(
    "script_result",
    [None, [], [1], [1, 2, 3], ["not-an-integer", 60]],
)
def test_redis_limiter_rejects_invalid_script_results(script_result: object) -> None:
    client = MagicMock()
    client.eval.return_value = script_result

    with pytest.raises(RedisError, match="Rate limit script returned"):
        RedisRateLimiter(client).check(
            RateLimitPolicy(name="rag", limit=5, window_seconds=60),
            "tenant-id:user-id",
        )


def test_rate_limit_rejection_preserves_retry_after() -> None:
    limiter = MagicMock()
    limiter.check.return_value = RateLimitDecision(
        allowed=False,
        remaining=0,
        retry_after=17,
    )
    with (
        patch("omnibase.core.rate_limit._get_limiter", return_value=limiter),
        pytest.raises(HTTPException) as raised,
    ):
        _enforce(
            policy=RateLimitPolicy(name="auth", limit=5, window_seconds=60),
            identity="127.0.0.1",
            settings=_settings(),
        )
    assert raised.value.status_code == 429
    assert raised.value.headers == {"Retry-After": "17"}


def test_redis_failure_can_be_explicitly_allowed_for_local_development() -> None:
    limiter = MagicMock()
    limiter.check.side_effect = RedisError("offline")
    with patch("omnibase.core.rate_limit._get_limiter", return_value=limiter):
        _enforce(
            policy=RateLimitPolicy(name="auth", limit=5, window_seconds=60),
            identity="127.0.0.1",
            settings=_settings(rate_limit_fail_closed=False),
        )


def test_redis_failure_fails_closed_by_default() -> None:
    limiter = MagicMock()
    limiter.check.side_effect = RedisError("offline")
    with (
        patch("omnibase.core.rate_limit._get_limiter", return_value=limiter),
        pytest.raises(HTTPException) as raised,
    ):
        _enforce(
            policy=RateLimitPolicy(name="auth", limit=5, window_seconds=60),
            identity="127.0.0.1",
            settings=_settings(),
        )
    assert raised.value.status_code == 503
    assert raised.value.headers == {"Retry-After": "1"}


def test_redis_failure_can_fail_closed() -> None:
    limiter = MagicMock()
    limiter.check.side_effect = RedisError("offline")
    with (
        patch("omnibase.core.rate_limit._get_limiter", return_value=limiter),
        pytest.raises(HTTPException) as raised,
    ):
        _enforce(
            policy=RateLimitPolicy(name="auth", limit=5, window_seconds=60),
            identity="127.0.0.1",
            settings=_settings(rate_limit_fail_closed=True),
        )
    assert raised.value.status_code == 503
    assert raised.value.headers == {"Retry-After": "1"}


def test_disabled_rate_limit_does_not_contact_redis() -> None:
    with patch("omnibase.core.rate_limit._get_limiter") as get_limiter:
        _enforce(
            policy=RateLimitPolicy(name="auth", limit=5, window_seconds=60),
            identity="127.0.0.1",
            settings=_settings(rate_limit_enabled=False),
        )
    get_limiter.assert_not_called()
