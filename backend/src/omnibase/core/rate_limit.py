"""Redis-backed fixed-window rate limiting dependencies."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Protocol, cast

from fastapi import Depends, HTTPException, Request, status
from redis import Redis
from redis.exceptions import RedisError

from omnibase.core.config import Settings, get_settings
from omnibase.core.logging import get_logger
from omnibase.tenants.dependencies import TenantContext, get_current_tenant

log = get_logger(__name__)

_FIXED_WINDOW_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


class _SynchronousRedisEvalClient(Protocol):
    """The synchronous subset of Redis used by the atomic limiter script."""

    def eval(
        self,
        script: str,
        numkeys: int,
        *keys_and_args: str,
    ) -> object: ...


def _redis_integer(value: object) -> int:
    if not isinstance(value, (int, str, bytes)) or isinstance(value, bool):
        raise RedisError("Rate limit script returned a non-integer value")
    try:
        return int(value)
    except ValueError as exc:
        raise RedisError("Rate limit script returned a non-integer value") from exc


def _parse_script_result(result: object) -> tuple[int, int]:
    if not isinstance(result, (list, tuple)) or len(result) != 2:
        raise RedisError("Rate limit script returned an invalid response")
    return _redis_integer(result[0]), _redis_integer(result[1])


@dataclass(frozen=True)
class RateLimitPolicy:
    """A named fixed-window request policy."""

    name: str
    limit: int
    window_seconds: int


@dataclass(frozen=True)
class RateLimitDecision:
    """Result returned by the Redis limiter."""

    allowed: bool
    remaining: int
    retry_after: int


class RedisRateLimiter:
    """Small synchronous limiter safe for FastAPI dependency thread pools."""

    def __init__(self, client: Redis) -> None:
        self.client = client

    def check(self, policy: RateLimitPolicy, identity: str) -> RateLimitDecision:
        identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
        key = f"omnibase:rate-limit:{policy.name}:{identity_hash}"
        synchronous_client = cast("_SynchronousRedisEvalClient", self.client)
        result = synchronous_client.eval(
            _FIXED_WINDOW_SCRIPT,
            1,
            key,
            str(policy.window_seconds),
        )
        current, raw_ttl = _parse_script_result(result)
        ttl = max(raw_ttl, 1)
        return RateLimitDecision(
            allowed=current <= policy.limit,
            remaining=max(policy.limit - current, 0),
            retry_after=ttl,
        )


@lru_cache(maxsize=4)
def _get_limiter(redis_url: str, timeout_seconds: float) -> RedisRateLimiter:
    client = Redis.from_url(
        redis_url,
        decode_responses=False,
        socket_connect_timeout=timeout_seconds,
        socket_timeout=timeout_seconds,
    )
    return RedisRateLimiter(client)


def _enforce(
    *,
    policy: RateLimitPolicy,
    identity: str,
    settings: Settings,
) -> None:
    if not settings.rate_limit_enabled:
        return
    try:
        decision = _get_limiter(
            settings.redis_url,
            settings.redis_timeout_seconds,
        ).check(policy, identity)
    except RedisError as exc:
        log.warning(
            "rate_limit.redis_unavailable",
            policy=policy.name,
            error_type=type(exc).__name__,
        )
        if settings.rate_limit_fail_closed:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": {
                        "code": "rate_limit_unavailable",
                        "message": "Request protection is temporarily unavailable",
                    }
                },
                headers={"Retry-After": "1"},
            ) from exc
        return

    if decision.allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "error": {
                "code": "rate_limited",
                "message": "Too many requests; retry later",
            }
        },
        headers={"Retry-After": str(decision.retry_after)},
    )


def enforce_auth_rate_limit(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Protect login/register/refresh without trusting proxy-provided IP headers."""
    client = request.client
    identity = client.host if client is not None else "unknown-client"
    _enforce(
        policy=RateLimitPolicy(
            name="auth",
            limit=settings.auth_rate_limit_per_window,
            window_seconds=settings.rate_limit_window_seconds,
        ),
        identity=identity,
        settings=settings,
    )


def enforce_rag_rate_limit(
    ctx: Annotated[TenantContext, Depends(get_current_tenant)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Limit expensive retrieval/LLM work per active tenant user."""
    _enforce(
        policy=RateLimitPolicy(
            name="rag",
            limit=settings.rag_rate_limit_per_window,
            window_seconds=settings.rate_limit_window_seconds,
        ),
        identity=f"{ctx.tenant_id}:{ctx.user_id}",
        settings=settings,
    )


def enforce_upload_rate_limit(
    ctx: Annotated[TenantContext, Depends(get_current_tenant)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Limit upload dispatch pressure per active tenant user."""
    _enforce(
        policy=RateLimitPolicy(
            name="upload",
            limit=settings.upload_rate_limit_per_window,
            window_seconds=settings.rate_limit_window_seconds,
        ),
        identity=f"{ctx.tenant_id}:{ctx.user_id}",
        settings=settings,
    )


def enforce_provider_test_rate_limit(
    credential_id: str,
    ctx: Annotated[TenantContext, Depends(get_current_tenant)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> None:
    """Bound real, potentially billable provider probes per user credential."""
    _enforce(
        policy=RateLimitPolicy(
            name="provider-test",
            limit=settings.provider_test_rate_limit_per_window,
            window_seconds=settings.rate_limit_window_seconds,
        ),
        identity=f"{ctx.tenant_id}:{ctx.user_id}:{credential_id}",
        settings=settings,
    )


def reset_rate_limiter_cache() -> None:
    """Clear cached Redis clients (tests and settings reloads)."""
    _get_limiter.cache_clear()


__all__ = [
    "RateLimitDecision",
    "RateLimitPolicy",
    "RedisRateLimiter",
    "enforce_auth_rate_limit",
    "enforce_provider_test_rate_limit",
    "enforce_rag_rate_limit",
    "enforce_upload_rate_limit",
    "reset_rate_limiter_cache",
]
