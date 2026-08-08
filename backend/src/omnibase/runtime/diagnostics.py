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
* ``NAME=value`` assignments with **any bounded horizontal whitespace**
  (``NAME = value``, including wide runs), CLI ``--name=value`` /
  ``--name = value`` forms, ``Name: value`` headers and quoted JSON-ish log
  lines, all with the same normalized sensitive-name policy;
* quoted assignment values are consumed **completely** through the closing
  quote (``OPENAI_API_KEY = "q7x9opaque rest8v"`` keeps neither the tail nor
  the quotes); the quoted scanner is **escape-aware** — a quote terminates the
  value only when the preceding run of backslashes is even, so ``\\``
  (escaped backslash) and escaped quotes inside the value never leave a secret
  tail; an unterminated, over-long or state-uncertain quoted value fails closed
  as a whole item;
* once a sensitive Header is confirmed its **entire** value is consumed to
  the physical line end — ``{``, ``}``, ``;``, quotes, commas and whitespace
  are NOT early-stop boundaries, so ``Authorization: q7x9{rest8v}``,
  ``Authorization: q7x9}rest8v}`` and ``X-Api-Key: q7x9;rest8v,more`` keep no
  tail (a JSON right-brace is sacrificed rather than risking a leak);
* cross-element CLI argument pairs in sequences: a sensitive flag such as
  ``--api-key`` redacts the *following* array element as one whole item
  (``["--api-key", "SECRET"]``) **even when that element starts with ``-`` or
  ``--``** (``["--api-key", "--q7x9opaque"]``, ``["--token", "-opaque"]``,
  ``["--password", "--"]``), while non-sensitive arguments are preserved; a
  following element that deterministically belongs to another allowlisted flag
  — including its inline ``--name=value`` form (``--profile=lite``,
  ``--service=backend``) or a sensitive inline flag (``--token=value``) that
  belongs to its own structure — is never swallowed; the flag then has no value
  and fails closed on its own; unknown or ambiguous state fails closed;
* provider-key shapes are covered through the value of a sensitive name, never
  through guessing secret prefixes.

The sensitive-name policy is a normalized token/full-field closed set plus a
bounded ``_``-delimited suffix policy — deliberately **no arbitrary substring
matching**. Cased keys are tokenized at **acronym-aware** case boundaries:
both lower/digit -> upper (``stripeA`` -> ``stripe_A``) and the end of an
all-caps acronym run before a Capitalized word (``APIKey`` -> ``API_Key``),
so ``stripeAPIKey`` -> ``stripe_api_key``, ``OPENAIApiKey`` ->
``openai_api_key``, ``openAIApiKey`` -> ``open_ai_api_key``,
``azureADAccessToken`` -> ``azure_ad_access_token``, ``myTOKEN`` ->
``my_token``, ``providerPASSWORD`` -> ``provider_password`` and ``xAPIKey`` ->
``x_api_key`` are all redacted while non-secret controls such as ``sortKey``,
``cacheID``, ``apiVersion``, ``foreignKey``, ``keyboardLayout`` and ``monkey``
are preserved. The ``_key`` suffix is **narrow**: ``sort_key``, ``cache_key``,
``foreign_key``, ``keyboard_layout`` and ``monkey`` are preserved while
``api_key``, ``secret_key``, ``access_key``, ``signing_key``, ``private_key``,
``encryption_key`` and provider variants (``STRIPE_API_KEY``) are redacted.

All parsing is bounded and linear (no unbounded quantifiers, no nested
quantifiers, no catastrophic backtracking): strings are capped before parsing,
lines are capped in count, names and values are capped in length and every
replacement is deterministic. Sensitive item values that exceed the
single-item parse limit fail closed: the **whole item** is replaced with
``[REDACTED]``, never a truncated prefix that would leak the tail. Parser
state beyond the bounded horizontal-whitespace limit or an unterminated quote
also fails closed as a whole item.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from omnibase.runtime.capabilities import CapabilityReport, ProductMode

# Sensitive-name policy. Keys are normalized into a separator form (runs of
# non-alphanumerics become ``_``), a flat form (non-alphanumerics removed) and
# a camelCase/PascalCase form (case boundaries become ``_``), then matched
# against a closed set of full-field tokens and a bounded set of ``_``-delimited
# suffixes. There is deliberately NO arbitrary substring matching: ``monkey``,
# ``keyboard_layout``, ``sort_key``, ``cache_key``, ``foreign_key``, ``design``
# and ``session_count`` are preserved while ``api_key``, ``secret_key``,
# ``access_key``, ``signing_key``, ``private_key``, ``encryption_key``,
# ``access_token``, ``signature``, ``session_token`` and provider variants are
# redacted. The same policy drives mapping keys, parsed
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
        "encryption_key",
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
        "encryptionkey",
    }
)

# Bounded ``_``-delimited suffix policy for provider variants. The generic
# ``_key`` suffix is deliberately NOT listed: ``sort_key``, ``cache_key`` and
# ``foreign_key`` are preserved. Only the specific sensitive ``*_key`` family
# (api/secret/access/signing/private/encryption key) plus the token/secret/
# password/credential family and connection-string/database-url variants are
# redacted. ``monkey`` never matches (no ``_`` boundary) and
# ``session_count``/``keyboard_layout`` do not end with a sensitive suffix.
_SENSITIVE_KEY_SUFFIXES: Final[tuple[str, ...]] = (
    "_api_key",
    "_secret_key",
    "_access_key",
    "_signing_key",
    "_private_key",
    "_encryption_key",
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
# Bounded horizontal whitespace limit around ``=`` / ``:`` separators. Any run
# up to this bound is recognized; a wider run puts the parser state beyond a
# reasonable limit and the whole sensitive item fails closed as ``[REDACTED]``
# instead of passing through unredacted.
MAX_HORIZONTAL_WS: Final[int] = 256

_REDACTED: Final[str] = "[REDACTED]"

# Identifier character classes used by the deterministic line scanners. Runs of
# these characters form candidate assignment/header names; a name must start
# with a letter or underscore and must not be embedded in a longer identifier.
_IDENTIFIER_CHARS: Final[frozenset[str]] = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-"
)
_NAME_START_CHARS: Final[frozenset[str]] = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
)

# URI/DSN userinfo: ``scheme://userinfo@...``. ``userinfo`` may carry a
# ``user:password`` pair; the part after the first ``:`` (or ``%3A``) is the
# password and is replaced without echoing it.
_URI_USERINFO_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<scheme>[A-Za-z][A-Za-z0-9+.\-]*://)(?P<userinfo>[^/\s@#?]+)@"
)

# A standalone CLI flag element (no ``=``): used for cross-element argument
# pairs such as ``["--api-key", "SECRET"]``.
_CLI_FLAG_ONLY_RE: Final[re.Pattern[str]] = re.compile(r"--[A-Za-z][A-Za-z0-9_.\-]{0,127}")

# An inline ``--name=value`` CLI flag element. The name is captured up to the
# first ``=``; this lets the cross-element state machine tell apart:
#   * allowlisted structural inline flags (``--profile=lite``, ``--service=backend``)
#   * sensitive inline flags (``--token=value``, ``--api-key=value``) that belong
#     to their OWN structure and must never be swallowed as a prior flag's value
# A plain dash-prefixed value that is neither (``--q7x9opaque``) is still
# treated as a value slot and redacted whole.
_CLI_FLAG_EQUALS_RE: Final[re.Pattern[str]] = re.compile(r"--([A-Za-z][A-Za-z0-9_.\-]{0,127})=.*")

# Deterministic closed set of the desktop CLI's own allowlisted flags. A
# sensitive flag whose following element is one of these deterministically
# belongs to ANOTHER allowlisted flag structure, so the sensitive flag has no
# value here and fails closed itself; the other flag is never swallowed.
KNOWN_ALLOWLISTED_CLI_FLAGS: Final[frozenset[str]] = frozenset(
    {
        "--profile",
        "--service",
        "--port",
        "--tail",
        "--help",
        "-h",
    }
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


# Acronym-aware case-boundary splitter. Two zero-width boundaries are inserted:
# (1) lower/digit -> upper (``stripeA`` -> ``stripe_A``) and (2) the end of an
# all-caps acronym run before a Capitalized word (``APIKey`` -> ``API_Key``,
# ``OPENAIApiKey`` -> ``OPENAI_Api_Key``). Without (2) a continuous acronym such
# as ``API`` inside ``stripeAPIKey`` / ``OPENAIApiKey`` never splits from the
# following Capitalized word, so the token never becomes ``api_key`` and the
# secret leaks. The combined splitter makes ``stripeAPIKey`` -> ``stripe_api_key``,
# ``OPENAIApiKey`` -> ``openai_api_key``, ``openAIApiKey`` -> ``open_ai_api_key``,
# ``azureADAccessToken`` -> ``azure_ad_access_token``, ``myTOKEN`` ->
# ``my_token``, ``providerPASSWORD`` -> ``provider_password`` and ``xAPIKey`` ->
# ``x_api_key`` while non-secret controls such as ``sortKey`` -> ``sort_key``,
# ``cacheID`` -> ``cache_id``, ``apiVersion`` -> ``api_version``,
# ``foreignKey`` -> ``foreign_key``, ``keyboardLayout`` -> ``keyboard_layout``
# and ``monkey`` stay outside the closed set/suffix policy.
_CASE_BOUNDARY_SPLIT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])"
)


def _is_sensitive_key(key: object) -> bool:
    """Return True when ``key`` matches the normalized sensitive-name policy.

    The key is normalized into a separator form (``API-Key`` -> ``api_key``),
    a flat form (``APIKey`` -> ``apikey``) and an acronym-aware cased form
    (``stripeAPIKey`` -> ``stripe_api_key``, ``OPENAIApiKey`` ->
    ``openai_api_key``, ``azureADAccessToken`` -> ``azure_ad_access_token``)
    and matched against a closed set of full-field tokens or a bounded
    ``_``-delimited suffix set. There is deliberately no arbitrary substring
    matching and the ``_key`` suffix is narrowed so ``sort_key``/``cache_key``/
    ``foreign_key`` are preserved.
    """
    if not isinstance(key, str):
        return False
    lower = key.lower()
    sep = re.sub(r"[^a-z0-9]+", "_", lower).strip("_")
    flat = re.sub(r"[^a-z0-9]", "", lower)
    camel = re.sub(r"[^a-z0-9]+", "_", _CASE_BOUNDARY_SPLIT_RE.sub("_", key).lower()).strip("_")
    if sep in _SENSITIVE_KEY_TOKENS:
        return True
    if flat in _SENSITIVE_KEY_TOKENS:
        return True
    if camel in _SENSITIVE_KEY_TOKENS:
        return True
    return any(sep.endswith(suffix) or camel.endswith(suffix) for suffix in _SENSITIVE_KEY_SUFFIXES)


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


def _scan_name_left(line: str, sep_index: int) -> tuple[str, int, int, bool] | None:
    """Match the name element immediately left of ``sep_index``.

    Returns ``(name, name_start, ws_before, is_cli)`` where ``name`` is the
    candidate (the ``--name`` part for the CLI form), ``name_start`` its index
    in the line, ``ws_before`` the horizontal whitespace run between the name
    and the separator (may exceed :data:`MAX_HORIZONTAL_WS` so callers can
    fail closed the whole item) and ``is_cli`` whether the name was written as
    ``--name``. Returns ``None`` when no valid name precedes the separator.
    The lookbehind rule mirrors the previous regex contract: the character
    before the name must not be an identifier character, so ``key`` inside
    ``--api-key`` is never matched by the plain ``NAME=`` form.
    """
    index = sep_index - 1
    ws_before = 0
    while index >= 0 and line[index] in " \t":
        ws_before += 1
        index -= 1
    end = index + 1
    start = end
    while start > 0 and line[start - 1] in _IDENTIFIER_CHARS:
        start -= 1
    run = line[start:end]
    if not run:
        return None
    if start > 0 and line[start - 1] in _IDENTIFIER_CHARS:
        return None
    if run.startswith("--"):
        tail = run[2:]
        if tail and tail[0] in _NAME_START_CHARS:
            return tail, start, ws_before, True
        return None
    if run[0] in _NAME_START_CHARS:
        return run, start, ws_before, False
    return None


def _match_colon_quoted_name(line: str, sep_index: int) -> tuple[str, int, int, str] | None:
    """Match a quoted ``"name"`` / ``'name'`` element left of a colon.

    Returns ``(name, name_start, ws_before, quote)`` or ``None``. The quotes
    must balance around the name and the name must not be embedded in a longer
    identifier.
    """
    index = sep_index - 1
    ws_before = 0
    while index >= 0 and line[index] in " \t":
        ws_before += 1
        index -= 1
    if index < 0 or line[index] not in "\"'":
        return None
    quote = line[index]
    index -= 1
    end = index + 1
    start = end
    while start > 0 and line[start - 1] in _IDENTIFIER_CHARS:
        start -= 1
    run = line[start:end]
    if not run or run[0] not in _NAME_START_CHARS:
        return None
    if start == 0 or line[start - 1] != quote:
        return None
    if start > 1 and line[start - 2] in _IDENTIFIER_CHARS:
        return None
    return run, start - 1, ws_before, quote


def _redact_colon_items(line: str) -> str:
    """Redact ``Name: value`` headers with the whole remaining line as value.

    Once a sensitive Header name is confirmed the ENTIRE value is consumed to
    the physical line end — semicolons, whitespace, quotes, commas, ``{`` and
    ``}`` no longer end the value, so ``Authorization: q7x9{rest8v}``,
    ``Authorization: q7x9}rest8v}`` and ``X-Api-Key: q7x9;rest8v,more`` never
    keep any tail. A JSON right-brace is sacrificed rather than risking a
    secret tail: over-redaction is fail-closed and safe; preserving JSON
    structure is not. A sensitive item whose bounded horizontal whitespace or
    value-length limit is exceeded fails closed as one whole ``[REDACTED]``
    item (the whole match is consumed, never a truncated prefix with a leak).
    """
    out: list[str] = []
    index = 0
    while index < len(line):
        sep = line.find(":", index)
        if sep == -1:
            out.append(line[index:])
            break
        quoted = _match_colon_quoted_name(line, sep)
        if quoted is not None:
            name, name_start, ws_before, quote = quoted
        else:
            plain = _scan_name_left(line, sep)
            if plain is None:
                out.append(line[index : sep + 1])
                index = sep + 1
                continue
            name, name_start, ws_before, _is_cli = plain
            quote = ""
        out.append(line[index:name_start])
        if not _is_sensitive_key(name):
            out.append(line[name_start : sep + 1])
            index = sep + 1
            continue
        # The whole physical line tail is the value; {/}/;/quote/comma are
        # NOT early-stop boundaries. An over-limit item fails closed whole.
        value_end = len(line)
        value = line[sep + 1 : value_end].rstrip("\r")
        if ws_before > MAX_HORIZONTAL_WS or len(value) > MAX_ITEM_VALUE_LENGTH:
            out.append(_REDACTED)
            index = value_end
            continue
        out.append(f"{quote}{name}{quote}: {_REDACTED}")
        index = value_end
    return "".join(out)


def _match_equals_value(line: str, sep_index: int) -> tuple[str, int, int, int] | None:
    """Match the value element right of an ``=`` separator.

    Returns ``(value, value_start, value_end, ws_after)`` for a terminated
    value — quoted values are consumed completely through the closing quote
    (``OPENAI_API_KEY = "q7x9opaque rest8v"`` keeps neither the tail nor the
    quotes), unquoted values stop at whitespace, ``&`` and ``#`` so
    consecutive query keys are redacted one by one — or ``None`` for an
    unterminated quote, which callers must fail closed as a whole item.

    The quoted scanner is escape-aware: a quote terminates the value only when
    the preceding run of backslashes is even. ``\\`` is an escaped backslash
    (not a quote terminator) and ``\\"`` is an escaped quote inside the value,
    so ``OPENAI_API_KEY="q7x9\\"rest8v"`` and ``OPENAI_API_KEY="q7x9\\"rest``
    no longer leave a secret tail after a wrongly-guessed quote boundary.
    Single and double quotes are handled identically. An unterminated,
    over-length or state-uncertain quoted value returns ``None`` so the caller
    fail-closes the whole item as ``[REDACTED]``.
    """
    index = sep_index + 1
    ws_after = 0
    while index < len(line) and line[index] in " \t":
        ws_after += 1
        index += 1
    value_start = index
    if index < len(line) and line[index] in "\"'":
        quote = line[index]
        index += 1
        while index < len(line) and line[index] not in "\r\n":
            if line[index] == "\\":
                # Escaped char: skip the backslash and the following character
                # so ``\\`` (escaped backslash) and ``\"`` (escaped quote) are
                # consumed as part of the value. A trailing lone backslash at
                # end-of-line falls through to the unterminated return below.
                index += 2
                continue
            if line[index] == quote:
                return line[value_start : index + 1], value_start, index + 1, ws_after
            index += 1
        return None
    while index < len(line) and line[index] not in " \t\r\n&#":
        index += 1
    return line[value_start:index], value_start, index, ws_after


def _redact_equals_items(line: str) -> str:
    """Redact ``NAME=value`` / ``--name=value`` forms by sensitive name.

    Any bounded horizontal whitespace run around the separator is recognized
    (``NAME   =   value``, ``--name = value``); a wider run is over-limit
    parser state and the whole sensitive item fails closed as ``[REDACTED]``.
    Quoted values are consumed completely; an unterminated quote fails closed
    as a whole item. A sensitive assignment whose value exceeds the
    single-item parse limit is also fail-closed as a whole item, never a
    truncated prefix that could leak the tail.
    """

    def _append_redacted(out: list[str], name: str, is_cli: bool) -> None:
        if is_cli:
            out.append(f"--{name}={_REDACTED}")
        else:
            out.append(f"{name}={_REDACTED}")

    out: list[str] = []
    index = 0
    while index < len(line):
        sep = line.find("=", index)
        if sep == -1:
            out.append(line[index:])
            break
        matched = _scan_name_left(line, sep)
        if matched is None:
            out.append(line[index : sep + 1])
            index = sep + 1
            continue
        name, name_start, ws_before, is_cli = matched
        out.append(line[index:name_start])
        if not _is_sensitive_key(name):
            out.append(line[name_start : sep + 1])
            index = sep + 1
            continue
        value_info = _match_equals_value(line, sep)
        if value_info is None:
            # Unterminated quoted value: fail closed as a whole item.
            out.append(_REDACTED)
            break
        value, _value_start, value_end, ws_after = value_info
        if (
            ws_before > MAX_HORIZONTAL_WS
            or ws_after > MAX_HORIZONTAL_WS
            or len(value) > MAX_ITEM_VALUE_LENGTH
        ):
            out.append(_REDACTED)
            index = value_end
            continue
        _append_redacted(out, name, is_cli)
        index = value_end
    return "".join(out)


def _redact_line(line: str) -> str:
    """Redact one bounded line: URI userinfo, headers, assignments, fallback.

    Structural passes run first so opaque values that carry no keyword are
    still removed. The keyword-marker check then stays as a deterministic
    fail-closed fallback for anything the parsers could not recognize.
    """
    redacted = _redact_uri_userinfo(line)
    redacted = _redact_colon_items(redacted)
    redacted = _redact_equals_items(redacted)
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


def _belongs_to_another_allowlisted_flag(item: object) -> bool:
    """Return True when ``item`` deterministically belongs to another flag.

    The item is one of:
    * a desktop CLI allowlisted flag (``--profile``, ``--service``, ``--port``,
      ``--tail``, ``--help``, ``-h``), including its inline ``--name=value``
      form (``--profile=lite``, ``--service=backend``);
    * itself a sensitive flag element, either standalone (``--token``) or in
      the inline ``--name=value`` form (``--token=value``, ``--api-key=value``)
      — such an element belongs to its OWN structure and is redacted on its own,
      so a preceding sensitive flag must never swallow it.

    A plain dash-prefixed value that is neither allowlisted nor a sensitive
    flag (``--q7x9opaque``, ``-opaque``, ``--``) is NOT another flag's
    structure: it is the value slot of the preceding sensitive flag and is
    redacted whole. Unknown or ambiguous state fails closed.
    """
    if not isinstance(item, str):
        return False
    if item in KNOWN_ALLOWLISTED_CLI_FLAGS:
        return True
    # Inline --name=value form: split out the name and classify it.
    inline = _CLI_FLAG_EQUALS_RE.fullmatch(item)
    if inline is not None:
        name = inline.group(1)
        if f"--{name}" in KNOWN_ALLOWLISTED_CLI_FLAGS:
            return True
        return _is_sensitive_key(name)
    # Standalone --name form: only a sensitive flag belongs to its own structure.
    return _CLI_FLAG_ONLY_RE.fullmatch(item) is not None and _is_sensitive_key(item[2:])


def _redact_sequence(
    items: Sequence[object],
    *,
    depth: int,
    seen_ids: set[int],
) -> list[object]:
    """Redact a bounded sequence element-by-element.

    Cross-element CLI argument pairs are handled by a deterministic
    token-state parser: a standalone sensitive flag (``--api-key``) redacts
    the FOLLOWING element as one whole item (covering opaque values with no
    keyword) **even when that element starts with ``-`` or ``--``**. When the
    following element deterministically belongs to another allowlisted flag
    (``--profile``, ``--service``, ``--port``, ``--tail``, ``--help``,
    ``-h`` or another sensitive flag) the current flag has no value and fails
    closed on its own without swallowing that structure. A sensitive flag with
    no following value fails closed redacted itself; non-sensitive flags and
    their values are preserved.
    """
    redacted_items: list[object] = []
    index = 0
    while index < len(items):
        item = items[index]
        if (
            isinstance(item, str)
            and _CLI_FLAG_ONLY_RE.fullmatch(item) is not None
            and _is_sensitive_key(item[2:])
        ):
            if index + 1 < len(items):
                following = items[index + 1]
                if _belongs_to_another_allowlisted_flag(following):
                    # No value slot here: the following element deterministically
                    # belongs to another allowlisted flag. Fail closed the flag
                    # itself and never swallow that structure.
                    redacted_items.append(_REDACTED)
                    index += 1
                    continue
                redacted_items.append(item)
                redacted_items.append(_REDACTED)
                index += 2
                continue
            redacted_items.append(_REDACTED)
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
    "KNOWN_ALLOWLISTED_CLI_FLAGS",
    "MAX_COLLECTION_SIZE",
    "MAX_HORIZONTAL_WS",
    "MAX_ITEM_VALUE_LENGTH",
    "MAX_REDACTION_DEPTH",
    "MAX_STRING_LENGTH",
    "ServiceStatus",
    "diagnostics_json",
    "diagnostics_payload",
    "redact_mapping",
    "select_mode",
]
