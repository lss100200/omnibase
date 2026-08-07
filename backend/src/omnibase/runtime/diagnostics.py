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
* ``NAME=value`` assignments (with bounded horizontal whitespace,
  ``NAME = value``), CLI ``--name=value`` / ``--name = value`` forms, ``Name:
  value`` headers and quoted JSON-ish log lines, all with the same normalized
  sensitive-name policy;
* cross-element CLI argument pairs in sequences: a sensitive flag such as
  ``--api-key`` redacts the *following* array element as one whole item
  (``["--api-key", "SECRET"]``), while non-sensitive arguments are preserved;
* provider-key shapes are covered through the value of a sensitive name, never
  through guessing secret prefixes.

The sensitive-name policy is a normalized token/full-field closed set plus a
bounded ``_``-delimited suffix policy -- deliberately **no arbitrary substring
matching**: ``monkey``, ``keyboard_layout``, ``design`` and ``session_count``
are preserved while ``api_key``, ``access_token``, ``signature``,
``session_token`` and provider variants are redacted.

All parsing is bounded and linear (no unbounded quantifiers, no nested
quantifiers, no catastrophic backtracking): strings are capped before parsing,
lines are capped in count, names and values are capped in length and every
replacement is deterministic. Sensitive item values that exceed the
single-item parse limit fail closed: the **whole item** is replaced with
``[REDACTED]``, never a truncated prefix that would leak the tail.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from omnibase.runtime.capabilities import CapabilityReport, ProductMode

# Sensitive-name policy. Keys are normalized into a separator form (runs of
# non-alphanumerics become ``_``) and a flat form (non-alphanumerics removed),
# then matched against a closed set of full-field tokens and a bounded set of
# ``_``-delimited suffixes. There is deliberately NO arbitrary substring
# matching: ``monkey``, ``keyboard_layout``, ``design`` and ``session_count``
# are preserved while ``api_key``, ``access_token``, ``signature`` and
# ``session_token`` are redacted. The same policy drives mapping keys, parsed
# assignment/query/header names and cross-element CLI flags. A match redacts
# the whole value without inspecting its contents.
_SENSITIVE_KEY_TOKENS: Final[frozenset[str]] = frozenset(
    {
        # Full-field names in normalized separator form.
        "authorization",
        "authorisation",
        "auth",
        "cookie",
        "set_cookie",
        "key",
        "secret",
        "password",
        "passwd",
        "pwd",
        "passphrase",
        "token",
        "api_key",
        "api_secret",
        "access_key",
        "access_token",
        "secret_key",
        "refresh_token",
        "session",
        "session_key",
        "session_token",
        "signing_key",
        "signature",
        "sig",
        "credential",
        "credentials",
        "private_key",
        "client_secret",
        "connection_string",
        "dsn",
        "database_url",
        "jwt",
        "jwt_secret",
        "service_account_json",
        "llm_api_key",
        "openai_api_key",
        "anthropic_api_key",
        "azure_api_key",
        "google_api_key",
        "huggingface_token",
        "hf_token",
        "postgres_password",
        "minio_root_password",
        "redis_password",
        # Flat (no-separator) variants used by mixed-case/env-style keys.
        "apikey",
        "apisecret",
        "accesskey",
        "accesstoken",
        "secretkey",
        "refreshtoken",
        "signingkey",
        "privatekey",
        "clientsecret",
        "connectionstring",
        "databaseurl",
        "setcookie",
        "sessionkey",
        "sessiontoken",
        "serviceaccountjson",
        "jwtsecret",
    }
)

# Bounded ``_``-delimited suffix policy for provider variants: any normalized
# separator-form key ending with one of these is sensitive. ``monkey`` never
# matches (no ``_`` boundary) and ``session_count``/``keyboard_layout`` do not
# end with a sensitive suffix.
_SENSITIVE_KEY_SUFFIXES: Final[tuple[str, ...]] = (
    "_key",
    "_token",
    "_secret",
    "_password",
    "_passwd",
    "_passphrase",
    "_credential",
    "_signature",
    "_auth",
    "_authorization",
    "_cookie",
    "_dsn",
    "_jwt",
    "_pwd",
    "_connection_string",
    "_database_url",
    "_connectionstring",
    "_databaseurl",
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
# Single-item value parse limit for headers/assignments/CLI values. A sensitive
# item whose value exceeds this limit is fail-closed as a WHOLE item (the full
# match is consumed and replaced with the marker), never truncated to a prefix
# that would leak the tail.
MAX_ITEM_VALUE_LENGTH: Final[int] = 512

_REDACTED: Final[str] = "[REDACTED]"

# URI/DSN userinfo: ``scheme://userinfo@...``. ``userinfo`` may carry a
# ``user:password`` pair; the part after the first ``:`` (or ``%3A``) is the
# password and is replaced without echoing it.
_URI_USERINFO_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<userinfo>[^/\s@#?]+)@"
)

# ``NAME=value`` assignments (env/log/query/fragment style) with bounded
# horizontal whitespace around the separator (``NAME = value``). The name is a
# bounded identifier; the value is a bounded run that stops at whitespace,
# ``&`` and ``#`` so consecutive query keys are redacted one by one. The
# lookbehind stops mid-identifier matches such as ``key`` inside ``--api-key``.
_EQUALS_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_.\-])([A-Za-z_][A-Za-z0-9_.\-]{0,127})"
    r"[ \t]{0,8}=[ \t]{0,8}([^\s&#]{0,2048})"
)

# CLI ``--name=value`` / ``--name = value`` form.
_CLI_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9_.\-])--([A-Za-z][A-Za-z0-9_.\-]{0,127})"
    r"[ \t]{0,8}=[ \t]{0,8}([^\s&#]{0,2048})"
)

# A standalone CLI flag element (no ``=``): used for cross-element argument
# pairs such as ``["--api-key", "SECRET"]``.
_CLI_FLAG_ONLY_RE: Final[re.Pattern[str]] = re.compile(r"--[A-Za-z][A-Za-z0-9_.\-]{0,127}")

# ``Name: value`` headers and quoted JSON-ish ``"name": "value"`` lines, with
# bounded horizontal whitespace around the colon (``Name : value``). The
# optional surrounding quotes must match; the value may contain whitespace and
# is capped at the whole-string limit so an oversized sensitive item is
# consumed entirely and fail-closed as one item, never leaving a leaked tail.
_COLON_ASSIGNMENT_RE: Final[re.Pattern[str]] = re.compile(
    r'(["\']?)([A-Za-z_][A-Za-z0-9_.\-]{0,127})\1'
    r'[ \t]{0,8}:[ \t]{0,8}(["\']?[^"\'\r\n;{}]{0,2048}["\']?)'
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
    """Return True when ``key`` matches the normalized sensitive-name policy.

    The key is normalized into a separator form (``API-Key`` -> ``api_key``)
    and a flat form (``APIKey`` -> ``apikey``) and matched against a closed
    set of full-field tokens or a bounded ``_``-delimited suffix set. There is
    deliberately no arbitrary substring matching.
    """
    if not isinstance(key, str):
        return False
    lower = key.lower()
    sep = re.sub(r"[^a-z0-9]+", "_", lower).strip("_")
    if sep in _SENSITIVE_KEY_TOKENS:
        return True
    if re.sub(r"[^a-z0-9]", "", lower) in _SENSITIVE_KEY_TOKENS:
        return True
    return any(sep.endswith(suffix) for suffix in _SENSITIVE_KEY_SUFFIXES)


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
    """Redact ``NAME=value`` / ``--name=value`` forms by sensitive name.

    A sensitive assignment whose value exceeds the single-item parse limit is
    fail-closed as a whole item (the entire match is consumed and replaced
    with the marker), never a truncated prefix that could leak the tail.
    """

    def _replace_equals(match: re.Match[str]) -> str:
        name, value = match.group(1), match.group(2)
        if _is_sensitive_key(name):
            if len(value) > MAX_ITEM_VALUE_LENGTH:
                return _REDACTED
            return f"{name}={_REDACTED}"
        return match.group(0)

    def _replace_cli(match: re.Match[str]) -> str:
        name, value = match.group(1), match.group(2)
        if _is_sensitive_key(name):
            if len(value) > MAX_ITEM_VALUE_LENGTH:
                return _REDACTED
            return f"--{name}={_REDACTED}"
        return match.group(0)

    redacted = _EQUALS_ASSIGNMENT_RE.sub(_replace_equals, text)
    return _CLI_ASSIGNMENT_RE.sub(_replace_cli, redacted)


def _redact_colon_assignments(text: str) -> str:
    """Redact ``Name: value`` headers and quoted ``"name": "value"`` lines.

    A sensitive header/JSON item whose value exceeds the single-item parse
    limit is fail-closed as a whole item (the full match up to the line
    delimiter is consumed), so the tail can never leak after a truncated
    prefix replacement.
    """

    def _replace(match: re.Match[str]) -> str:
        quote, name, value = match.group(1), match.group(2), match.group(3)
        if _is_sensitive_key(name):
            if len(value) > MAX_ITEM_VALUE_LENGTH:
                return _REDACTED
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


def _is_standalone_cli_flag(item: object) -> bool:
    """Return True when ``item`` is a standalone ``--name`` flag element."""
    return isinstance(item, str) and _CLI_FLAG_ONLY_RE.fullmatch(item) is not None


def _redact_sequence(
    items: Sequence[object],
    *,
    depth: int,
    seen_ids: set[int],
) -> list[object]:
    """Redact a bounded sequence element-by-element.

    Cross-element CLI argument pairs are handled first: a standalone sensitive
    flag (``--api-key``) redacts the FOLLOWING element as one whole item
    (covering opaque values with no keyword), while non-sensitive flags and
    their values are preserved. A sensitive flag with no following value is
    fail-closed redacted itself, and a following element that is itself a flag
    is never swallowed as a value.
    """
    redacted_items: list[object] = []
    index = 0
    while index < len(items):
        item = items[index]
        if (
            isinstance(item, str)
            and _CLI_FLAG_ONLY_RE.fullmatch(item)
            and _is_sensitive_key(item[2:])
        ):
            if index + 1 < len(items) and not _is_standalone_cli_flag(items[index + 1]):
                redacted_items.append(item)
                redacted_items.append("[REDACTED]")
                index += 2
                continue
            redacted_items.append("[REDACTED]")
            index += 1
            continue
        redacted_items.append(_redact_value(item, depth=depth + 1, seen_ids=seen_ids))
        index += 1
    return redacted_items


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
            redacted_items = _redact_sequence(value, depth=depth, seen_ids=seen_ids)
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
    "MAX_ITEM_VALUE_LENGTH",
    "MAX_REDACTION_DEPTH",
    "MAX_STRING_LENGTH",
    "ServiceStatus",
    "diagnostics_json",
    "diagnostics_payload",
    "redact_mapping",
    "select_mode",
]
