"""Safe service diagnostics for the local desktop launcher.

The diagnostic redactor is the privacy boundary between operator support bundles
and secrets. It is deliberately recursive over the bounded JSON-like subset
(mappings, lists, tuples and scalars) so a value such as
``{"headers": [{"Authorization": "Bearer SECRET"}]}`` cannot reach a support
bundle by nesting under a sequence.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from omnibase.runtime.capabilities import CapabilityReport, ProductMode

# Sensitive key fragments, matched case-insensitively against any mapping key.
# Covers authorization, cookie/set-cookie, api key/token/secret/password/
# private-key/credential variants plus common provider credential names. A key
# fragment match redacts the whole value without inspecting its contents.
_SENSITIVE_KEY_FRAGMENTS: Final[tuple[str, ...]] = (
    "secret",
    "password",
    "passwd",
    "pwd",
    "token",
    "apikey",
    "api_key",
    "api-key",
    "apisecret",
    "api_secret",
    "api-secret",
    "authorization",
    "authorisation",
    "auth",
    "cookie",
    "set-cookie",
    "setcookie",
    "credential",
    "credentials",
    "privatekey",
    "private_key",
    "private-key",
    "private-key-path",
    "clientsecret",
    "client_secret",
    "client-secret",
    "accesstoken",
    "access_token",
    "access-token",
    "refreshtoken",
    "refresh_token",
    "refresh-token",
    "connectionstring",
    "connection_string",
    "connection-string",
    "dsn",
    "databaseurl",
    "database_url",
    "database-url",
    "postgres_password",
    "minio_root_password",
    "redis_password",
    "jwt_secret",
    "signingkey",
    "signing_key",
    "signing-key",
    "llm_api_key",
    "openai_api_key",
    "anthropic_api_key",
    "azure_api_key",
    "google_api_key",
    "serviceaccountjson",
    "huggingface_token",
    "hf_token",
)

_SECRET_VALUE_MARKERS: Final[tuple[str, ...]] = (
    "bearer ",
    "basic ",
    "token ",
    "secret",
    "password",
)

# Bounded redaction limits. Anything deeper/wider/longer is replaced with a
# deterministic marker rather than recursing or rendering unbounded content.
MAX_REDACTION_DEPTH: Final[int] = 8
MAX_COLLECTION_SIZE: Final[int] = 256
MAX_STRING_LENGTH: Final[int] = 2048


@dataclass(frozen=True)
class ServiceStatus:
    name: str
    state: str
    detail: str | None = None
    exit_code: int | None = None


def select_mode(report: CapabilityReport, requested: ProductMode | None = None) -> ProductMode:
    """Select a mode without upgrading an unproven capability."""
    if requested is not None:
        if not report.supports(requested):
            raise ValueError(f"mode_not_available:{requested.value}")
        return requested
    return ProductMode.LOCAL if report.supports(ProductMode.LOCAL) else ProductMode.LITE


def _is_sensitive_key(key: object) -> bool:
    """Return True when ``key`` matches a sensitive fragment case-insensitively."""
    if not isinstance(key, str):
        return False
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    if not normalized:
        return False
    for fragment in _SENSITIVE_KEY_FRAGMENTS:
        needle = re.sub(r"[^a-z0-9]", "", fragment.lower())
        if needle and needle in normalized:
            return True
    return False


def _redact_string(value: str) -> str:
    """Truncate long strings; never attempt to parse credentials out of them.

    A key-level match already replaced the whole value with ``[REDACTED]``.
    This helper only runs on scalar string values whose *key* was not sensitive,
    so it bounds rendered length and removes obvious inline secret markers
    without making claims about parsing bearer/basic values safely.
    """
    if any(marker in value.lower() for marker in _SECRET_VALUE_MARKERS):
        return "[REDACTED]"
    if len(value) > MAX_STRING_LENGTH:
        return f"[TRUNCATED:{len(value)}]"
    return value


def _redact_value(
    value: object,
    *,
    depth: int,
    seen_ids: set[int],
) -> object:
    """Recursively redact a bounded JSON-like value.

    ``mappings`` are redacted key-by-key; ``list``/``tuple`` are redacted
    element-by-element so secrets nested under sequences are removed. Cycles are
    detected through ``id()`` tracking and replaced with a deterministic marker
    rather than recursing or leaking cycle contents. Depth, collection size and
    string length are all bounded.
    """
    if depth >= MAX_REDACTION_DEPTH:
        return "[MAX_DEPTH]"
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        object_id = id(value)
        if object_id in seen_ids:
            return "[CYCLE]"
        seen_ids.add(object_id)
        try:
            if len(value) > MAX_COLLECTION_SIZE:
                return f"[OVERSIZED_MAPPING:{len(value)}]"
            redacted: dict[str, object] = {}
            for key, nested in value.items():
                key_text = key if isinstance(key, str) else str(key)
                if _is_sensitive_key(key_text):
                    redacted[key_text] = "[REDACTED]"
                else:
                    redacted[key_text] = _redact_value(nested, depth=depth + 1, seen_ids=seen_ids)
        finally:
            seen_ids.discard(object_id)
        return redacted
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        object_id = id(value)
        if object_id in seen_ids:
            return "[CYCLE]"
        seen_ids.add(object_id)
        try:
            if len(value) > MAX_COLLECTION_SIZE:
                return f"[OVERSIZED_SEQUENCE:{len(value)}]"
            redacted_items: list[object] = [
                _redact_value(item, depth=depth + 1, seen_ids=seen_ids) for item in value
            ]
        finally:
            seen_ids.discard(object_id)
        # Preserve tuple-ness so callers that rely on type shape do not break.
        return tuple(redacted_items) if isinstance(value, tuple) else redacted_items
    # Render unknown objects via repr, then bound/truncate. This keeps exception
    # text, command arguments, URLs and connection strings visible-but-bounded
    # without claiming to parse secrets out of them.
    return _redact_string(repr(value))


def redact_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """Keep configuration shape while removing secret values recursively.

    Returns a plain ``dict`` whose nested mappings/lists/tuples have all
    sensitive-key values replaced with ``[REDACTED]`` and whose scalar strings
    are length-bounded. Cycles, excessive depth/width and oversized strings are
    replaced with deterministic markers instead of recursing or leaking.
    """
    result = _redact_value(values, depth=0, seen_ids=set())
    return dict(result) if isinstance(result, dict) else {"value": result}


def diagnostics_payload(
    report: CapabilityReport,
    services: Iterable[ServiceStatus] = (),
    *,
    config_shape: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a safe diagnostic payload suitable for support bundles."""
    return {
        "capabilities": report.to_dict(),
        "services": [
            {
                "name": service.name,
                "state": service.state,
                "detail": service.detail,
                "exit_code": service.exit_code,
            }
            for service in services
        ],
        "config_shape": redact_mapping(config_shape or {}),
        "privacy": {
            "secrets_included": False,
            "user_documents_included": False,
            "provider_responses_included": False,
        },
    }


def diagnostics_json(
    report: CapabilityReport,
    services: Iterable[ServiceStatus] = (),
    *,
    config_shape: Mapping[str, object] | None = None,
) -> str:
    """Serialize diagnostics deterministically for a support bundle.

    Accepts the same explicit typed arguments as :func:`diagnostics_payload` so
    arbitrary untyped arguments cannot be forwarded into the typed payload
    builder.
    """
    return json.dumps(
        diagnostics_payload(report, services, config_shape=config_shape),
        sort_keys=True,
    )


__all__ = [
    "MAX_COLLECTION_SIZE",
    "MAX_REDACTION_DEPTH",
    "MAX_STRING_LENGTH",
    "ServiceStatus",
    "diagnostics_json",
    "diagnostics_payload",
    "redact_mapping",
    "select_mode",
]
