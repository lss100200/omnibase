"""Fail-closed validation for public, persistable model identifiers."""

from __future__ import annotations

import re
import unicodedata
from urllib.parse import urlsplit

_KEY_PATTERNS = (
    re.compile(r"(?i)\bsk-[a-z0-9_-]{8,}\b"),
    re.compile(r"(?i)\b(?:ghp|github_pat|glpat|xox[aboprs]|akia)[-_a-z0-9]{8,}\b"),
)
_AUTH_PATTERN = re.compile(r"(?i)\b(?:bearer|basic)\s+\S+")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_PRIVATE_KEY_PATTERN = re.compile(r"(?i)-----BEGIN(?: [A-Z0-9]+)? PRIVATE KEY-----")
_DATABASE_URL_PATTERN = re.compile(r"(?i)\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://")
_SENSITIVE_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(?:^|[\s;])(?:[A-Z0-9_.-]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|PRIVATE[_-]?KEY)|DATABASE_URL)\s*="
)
_ENV_LOCATOR_PATTERN = re.compile(r"(?i)(?:^|[/\\])\.env(?:\.[A-Z0-9_.-]+)?(?:$|[/\\])")
_WINDOWS_PATH_PATTERN = re.compile(r"^(?:[A-Za-z]:[/\\]|\\\\)")


class ModelIdentifierError(ValueError):
    """A model-name field contains secret or physical-locator material."""


def validate_public_model_id(value: str) -> str:
    """Return a normalized public model name or reject secret/locator material."""
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        raise ModelIdentifierError("agent_model_id_invalid")
    parsed = urlsplit(normalized)
    url_userinfo = bool(
        parsed.scheme and (parsed.username is not None or parsed.password is not None)
    )
    forbidden = (
        any(pattern.search(normalized) for pattern in _KEY_PATTERNS)
        or _AUTH_PATTERN.search(normalized) is not None
        or _JWT_PATTERN.search(normalized) is not None
        or _PRIVATE_KEY_PATTERN.search(normalized) is not None
        or _DATABASE_URL_PATTERN.search(normalized) is not None
        or url_userinfo
        or _SENSITIVE_ASSIGNMENT_PATTERN.search(normalized) is not None
        or _ENV_LOCATOR_PATTERN.search(normalized) is not None
        or _WINDOWS_PATH_PATTERN.search(normalized) is not None
        or normalized.startswith("/")
    )
    if forbidden:
        raise ModelIdentifierError("agent_model_id_sensitive_or_locator_forbidden")
    return normalized


__all__ = ["ModelIdentifierError", "validate_public_model_id"]
