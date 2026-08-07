"""Safe service diagnostics for the local desktop launcher.

The diagnostic redactor is the privacy boundary between operator support bundles
and secrets. It is deliberately recursive over the bounded JSON-like subset
(mappings, lists, tuples and scalars) so a value such as
``{"headers": [{"Authorization": "Bearer SECRET"}]}`` cannot reach a support
bundle by nesting under a sequence.

Scalar strings are additionally passed through a bounded, deterministic line
tokenizer that removes credentials from common structures **without relying on
keyword-bearing samples**:

* ``scheme://user:password@host`` URI/DSN userinfo for any scheme;
* sensitive query keys and fragments (``key``, ``api_key``, ``token``,
  ``access_token``, ``signature``, ``sig``, ``credential``, ``password`` and
  provider variants) such as ``?key=abc`` / ``#token=abc``;
* ``NAME=value`` assignments, CLI ``--name=value`` forms, ``Name: value``
  headers and quoted JSON-ish log lines, all with the same normalized
  sensitive-name policy;
* provider-key shapes are covered through the value of a sensitive name, never
  through guessing secret prefixes.

All parsing is bounded and linear (no unbounded quantifiers, no nested
quantifiers, no catastrophic backtracking): strings are capped before parsing,
lines are capped in count, names and values are capped in length and every
replacement is deterministic.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from omnibase.runtime.capabilities import CapabilityReport, ProductMode

# Sensitive key fragments, matched case-insensitively against any mapping key
# and against parsed assignment/query/header names. Covers authorization,
# cookie/set-cookie, api key/token/secret/password/private-key/credential
# variants plus common provider credential names. A fragment match redacts the
# whole value without inspecting its contents.
_SENSITIVE_KEY_FRAGMENTS: Final[tuple[str, ...]] = (
    "secret",
    "password",
    "passwd",
    "pwd",
    "passphrase",
    "token",
    "apikey",
    "api_key",
    "api-key",
    "apisecret",
    "api_secret",
    "api-secret",
    "key",
    "accesskey",
    "access_key",
    "access-key",
    "secretkey",
    "secret_key",
    "secret-key",
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
    "jwt",
    "signingkey",
    "signing_key",
    "signing-key",
    "signature",
    "sig",
    "session",
    "sessionkey",
    "session_key",
    "session-key",
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
# Bounded line tokenizer limits: at most this many lines per string are parsed
# and each parsed name/value is length-capped by the regexes below.
MAX_REDACTION_LINES: Final[int] = 512

_REDACTED: Final[str] = "[REDACTED]"

# URI/DSN userinfo: ``scheme://userinfo@...``. ``userinfo`` may carry a
# ``user:password`` pair; the part after the first ``:`` (or ``%3A``) is the
# password and is replaced without echoing it.
_URI_USERINFO_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<userinfo>[^/\s@#?]+)@"
)

# ``NAME=value`` assignments (env/log/query/fragment style). The name is a
# bounded identifier; the value is a bounded run that stops at whitespace,
# ``&`` and ``#`` so consecutive query keys are redacted one by one. The
# lookbehind stops mid-identifier matches such as ``key`` inside ``--api-key``.
_EQUALS_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_.\-])([A-Za-z_][A-Za-z0-9_.\-]{0,127})=([^\s&#]*)"
)

# CLI ``--name=value`` form.
_CLI_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_.\-])--([A-Za-z][A-Za-z0-9_.\-]{0,127})=([^\s&#]*)"
)

# ``Name: value`` headers and quoted JSON-ish ``"name": "value"`` lines. The
# optional surrounding quotes must match; the value may contain whitespace and
# is length-capped at 512 characters, stopping at quotes/semicolons/braces.
_COLON_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r'(["\']?)([A-Za-z_][A-Za-z0-9_.\-]{0,127})\1\s*:\s*(["\']?[^"\'\r\n;{}]{0,512}["\']?)'
)


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


def _redact_uri_userinfo(text: str) -> str:
    """Redact ``scheme://user:password@host`` userinfo passwords.

    A userinfo without a password (``scheme://user@host``) carries no embedded
    credential and is left untouched. The password (everything after the first
    ``:`` or ``%3A``) is replaced with a fixed marker; the secret itself is
    never echoed into the replacement.
    """

    def _replace(match: re.Match[str]) -> str:
        scheme = match.group("scheme")
        userinfo = match.group("userinfo")
        lower = userinfo.lower()
        literal_colon = userinfo.find(":")
        encoded_colon = lower.find("%3a")
        if literal_colon == -1 and encoded_colon == -1:
            return match.group(0)
        if literal_colon != -1 and (encoded_colon == -1 or literal_colon < encoded_colon):
            return f"{scheme}{userinfo[:literal_colon]}:{_REDACTED}@"
        return f"{scheme}{userinfo[:encoded_colon]}%3A{_REDACTED}@"

    return _URI_USERINFO_RE.sub(_replace, text)


def _redact_assignments(text: str) -> str:
    """Redact ``NAME=value`` and ``--name=value`` forms by sensitive name."""

    def _replace_equals(match: re.Match[str]) -> str:
        name, _value = match.group(1), match.group(2)
        if _is_sensitive_key(name):
            return f"{name}={_REDACTED}"
        return match.group(0)

    def _replace_cli(match: re.Match[str]) -> str:
        name, _value = match.group(1), match.group(2)
        if _is_sensitive_key(name):
            return f"--{name}={_REDACTED}"
        return match.group(0)

    redacted = _EQUALS_ASSIGNMENT_RE.sub(_replace_equals, text)
    return _CLI_ASSIGNMENT_RE.sub(_replace_cli, redacted)


def _redact_colon_assignments(text: str) -> str:
    """Redact ``Name: value`` headers and quoted ``"name": "value"`` lines."""

    def _replace(match: re.Match[str]) -> str:
        quote, name, _value = match.group(1), match.group(2), match.group(3)
        if _is_sensitive_key(name):
            if quote:
                return f"{quote}{name}{quote}: {_REDACTED}"
            return f"{name}: {_REDACTED}"
        return match.group(0)

    return _COLON_ASSIGNMENT_RE.sub(_replace, text)


def _redact_line(line: str) -> str:
    """Redact one bounded line: URI userinfo, assignments, headers, fallback.

    Structural passes run first so opaque values that carry no keyword are
    still removed. The keyword-marker check then stays as a deterministic
    fail-closed fallback for anything the parsers could not recognize.
    """
    redacted = _redact_uri_userinfo(line)
    redacted = _redact_colon_assignments(redacted)
    redacted = _redact_assignments(redacted)
    if any(marker in redacted.lower() for marker in _SECRET_VALUE_MARKERS):
        return _REDACTED
    return redacted


def _redact_string(value: str) -> str:
    """Redact credentials inside a scalar string with bounded line parsing.

    The input is capped at :data:`MAX_STRING_LENGTH` before parsing and at
    :data:`MAX_REDACTION_LINES` lines, then each line is passed through the
    deterministic structural redactor (URI/DSN userinfo, sensitive query keys,
    ``NAME=value``, CLI ``--name=value``, ``Name: value`` headers and JSON-ish
    assignments) before the keyword-marker fallback. Oversized inputs are
    replaced with a ``[TRUNCATED:N]`` marker using the original length.
    """
    original_length = len(value)
    if original_length > MAX_STRING_LENGTH:
        value = value[:MAX_STRING_LENGTH]
    lines = value.split("\n")
    if len(lines) > MAX_REDACTION_LINES:
        redacted = "\n".join(_redact_line(line) for line in lines[:MAX_REDACTION_LINES])
        redacted = f"{redacted}\n[TRUNCATED_LINES:{len(lines)}]"
    else:
        redacted = "\n".join(_redact_line(line) for line in lines)
    if original_length > MAX_STRING_LENGTH:
        return f"[TRUNCATED:{original_length}]"
    return redacted


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
    are passed through the bounded credential parser (URI/DSN userinfo,
    sensitive query keys, ``NAME=value``, CLI ``--name=value``, ``Name: value``
    headers and JSON-ish assignments) before length-bounding. Cycles, excessive
    depth/width and oversized strings are replaced with deterministic markers
    instead of recursing or leaking.
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
                "detail": None if service.detail is None else _redact_string(service.detail),
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
