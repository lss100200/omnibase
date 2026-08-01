"""MinIO client wrapper.

Thin facade around the official minio Python SDK.
The client is created lazily and cached; bucket is auto-created on first use.
"""

from __future__ import annotations

from typing import Any

from minio import Minio

from omnibase.core.config import Settings, get_settings
from omnibase.core.logging import get_logger

log = get_logger(__name__)

_clients: dict[str, Minio] = {}


def get_minio_client(settings: Settings | None = None) -> Minio:
    """Return a cached MinIO client.

    The endpoint, credentials, and TLS flag all come from settings.
    """
    settings = settings or get_settings()
    key = f"{settings.minio_endpoint}|{settings.minio_access_key}"

    if key in _clients:
        return _clients[key]

    client = Minio(
        endpoint=settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=settings.minio_secure,
        # HTTP client defaults are fine for Phase 0; tune later if needed.
    )

    _clients[key] = client
    log.info(
        "minio.client_created",
        endpoint=settings.minio_endpoint,
        secure=settings.minio_secure,
    )
    return client


def ensure_bucket_exists(bucket: str | None = None) -> bool:
    """Ensure the configured bucket exists; create if missing.

    Returns True if bucket exists (or was created), False on failure.
    """
    settings = get_settings()
    bucket = bucket or settings.minio_bucket
    client = get_minio_client(settings)

    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            log.info("minio.bucket_created", bucket=bucket)
        else:
            log.debug("minio.bucket_exists", bucket=bucket)
        return True
    except Exception as exc:
        log.error("minio.bucket_ensure_failed", bucket=bucket, error=str(exc))
        return False


def dispose_clients() -> None:
    """Clear cached clients (used in tests)."""
    _clients.clear()


# Type export for type-checkers
__all__ = [
    "Minio",
    "dispose_clients",
    "ensure_bucket_exists",
    "get_minio_client",
]

# Re-export for downstream imports
_: Any = Minio  # keep type import
