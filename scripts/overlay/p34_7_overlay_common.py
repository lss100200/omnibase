"""Shared fail-closed helpers for the P34.7 production Overlay gates."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
FORBIDDEN_SECRET_KEYS = {
    "api_key",
    "authorization",
    "cookie",
    "password",
    "private_key",
    "provider_key",
    "secret",
    "secret_value",
    "token",
}
ALLOWED_CREDENTIAL_REFERENCE_SCHEMES = {
    "kms",
    "omnibase-secret",
    "secret",
    "vault",
}


class ProductionGateError(RuntimeError):
    """The production Gate input is invalid or unsafe."""


def canonical_bytes(value: object) -> bytes:
    """Return stable JSON bytes used to bind configuration and evidence."""

    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProductionGateError(message)


def require_exact_keys(value: dict[str, Any], *, allowed: set[str], context: str) -> None:
    extra = sorted(set(value) - allowed)
    require(not extra, f"{context} contains unsupported fields: {extra}")


def require_sha256(value: object, *, context: str) -> str:
    require(isinstance(value, str) and SHA256_PATTERN.fullmatch(value) is not None, context)
    return str(value)


def require_git_commit(value: object, *, context: str) -> str:
    require(
        isinstance(value, str) and GIT_COMMIT_PATTERN.fullmatch(value) is not None,
        context,
    )
    return str(value)


def reject_secret_fields(value: object, *, context: str = "document") -> None:
    """Reject credential-shaped fields without printing their values."""

    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower()
            require(
                normalized not in FORBIDDEN_SECRET_KEYS,
                f"{context} contains secret field",
            )
            reject_secret_fields(child, context=f"{context}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            reject_secret_fields(child, context=f"{context}[{index}]")


def _contains_env_component(path: Path) -> bool:
    return any(part.lower() == ".env" for part in path.parts)


def _contains_link_component(path: Path) -> bool:
    current = Path(path.anchor) if path.anchor else Path.cwd()
    parts = path.parts[1:] if path.anchor else path.parts
    for part in parts:
        current /= part
        if not current.exists():
            continue
        if current.is_symlink():
            return True
        junction_check = getattr(current, "is_junction", None)
        if junction_check is not None and junction_check():
            return True
    return False


def safe_json_file(path: Path) -> dict[str, Any]:
    """Read one explicit JSON file while refusing env files and filesystem links."""

    absolute = path.absolute()
    require(not _contains_env_component(absolute), "root or nested .env input is forbidden")
    require(not _contains_link_component(absolute), "linked Gate input is forbidden")
    resolved = absolute.resolve(strict=True)
    require(not _contains_env_component(resolved), "resolved env input is forbidden")
    require(resolved.is_file(), "Gate input must be a regular file")
    try:
        decoded = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProductionGateError("Gate input is not valid UTF-8 JSON") from exc
    require(isinstance(decoded, dict), "Gate input must be a JSON object")
    reject_secret_fields(decoded)
    return decoded


def safe_regular_file(path: Path, *, context: str) -> Path:
    """Resolve a non-env, non-link regular input without reading its contents."""

    absolute = path.absolute()
    require(not _contains_env_component(absolute), f"{context} cannot be an env file")
    require(not _contains_link_component(absolute), f"linked {context} is forbidden")
    resolved = absolute.resolve(strict=True)
    require(
        not _contains_env_component(resolved),
        f"resolved {context} cannot be an env file",
    )
    require(resolved.is_file(), f"{context} must be a regular file")
    return resolved


def validate_https_endpoint(value: object, *, context: str) -> str:
    require(isinstance(value, str), f"{context} must be a URL")
    parsed = urlsplit(str(value))
    require(parsed.scheme == "https", f"{context} must use HTTPS")
    require(bool(parsed.hostname), f"{context} is missing a hostname")
    require(
        not parsed.username and not parsed.password,
        f"{context} must not contain userinfo",
    )
    require(
        not parsed.query and not parsed.fragment,
        f"{context} must not contain query/fragment",
    )
    hostname = str(parsed.hostname).lower()
    require(
        hostname not in {"localhost", "127.0.0.1", "::1"},
        f"{context} cannot use a loopback endpoint",
    )
    return str(value)


def validate_credential_reference(value: object, *, context: str) -> str:
    require(isinstance(value, str), f"{context} must be a logical reference")
    parsed = urlsplit(str(value))
    require(
        parsed.scheme in ALLOWED_CREDENTIAL_REFERENCE_SCHEMES,
        f"{context} must use an approved server-owned secret reference scheme",
    )
    require(bool(parsed.netloc or parsed.path), f"{context} is empty")
    require(
        not parsed.query and not parsed.fragment,
        f"{context} must not contain query/fragment",
    )
    return str(value)


def percentile(values: list[float], percentile_value: float) -> float:
    """Return a deterministic nearest-rank percentile."""

    require(bool(values), "percentile requires at least one value")
    require(0 < percentile_value <= 100, "percentile must be in (0, 100]")
    ordered = sorted(values)
    rank = max(1, int((percentile_value / 100) * len(ordered) + 0.999999))
    return ordered[min(rank - 1, len(ordered) - 1)]
