"""Redaction attack matrix for the desktop diagnostics redactor.

Each test asserts that a forbidden secret marker is absent from both the
structured redacted output and the serialized JSON. The redactor must handle
nested mappings/sequences, mixed-case keys, bearer/basic credentials, URLs,
DSNs, multiline exception text, cycles, excessive depth/width and oversized
strings without leaking secrets.
"""

from __future__ import annotations

import json

import pytest

from omnibase.runtime.capabilities import probe_capabilities
from omnibase.runtime.diagnostics import (
    MAX_COLLECTION_SIZE,
    MAX_REDACTION_DEPTH,
    MAX_STRING_LENGTH,
    diagnostics_json,
    diagnostics_payload,
    redact_mapping,
)

SECRET = "BEARER-SECRET-VALUE-123456"
FORBIDDEN_MARKERS = (
    "BEARER-SECRET-VALUE-123456",
    "password123",
    "sk-live-secret-key",
    "postgres://user:hardcoded@host",
)


def _assert_no_secret(payload: object) -> None:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    for marker in FORBIDDEN_MARKERS:
        assert marker not in serialized
        assert marker.lower() not in serialized.lower()


def test_nested_mapping_inside_list_is_redacted() -> None:
    payload = {"headers": [{"Authorization": f"Bearer {SECRET}"}]}
    redacted = redact_mapping(payload)
    _assert_no_secret(redacted)
    assert redacted["headers"] == [{"Authorization": "[REDACTED]"}]


def test_nested_sequence_inside_list_is_redacted() -> None:
    payload = {"nested": [{"api_key": SECRET}, {"items": [{"token": SECRET}]}]}
    redacted = redact_mapping(payload)
    _assert_no_secret(redacted)
    assert redacted["nested"][0]["api_key"] == "[REDACTED]"
    assert redacted["nested"][1]["items"][0]["token"] == "[REDACTED]"


def test_mixed_case_sensitive_keys_are_redacted() -> None:
    payload = {
        "API-Key": SECRET,
        "x-Set-Cookie": "session=abc",
        "Authorization": f"Bearer {SECRET}",
        "ClientSecret": SECRET,
    }
    redacted = redact_mapping(payload)
    _assert_no_secret(redacted)
    assert redacted["API-Key"] == "[REDACTED]"
    assert redacted["x-Set-Cookie"] == "[REDACTED]"
    assert redacted["Authorization"] == "[REDACTED]"
    assert redacted["ClientSecret"] == "[REDACTED]"


def test_bearer_and_basic_credentials_in_string_values_are_redacted() -> None:
    # The key is not sensitive, but the value carries a bearer/basic marker.
    payload = {"header_line": "authorization: Bearer " + SECRET}
    payload2 = {"proxy_auth": "basic dXNlcjpwYXNzd29yZA=="}
    redacted = redact_mapping(payload)
    redacted2 = redact_mapping(payload2)
    assert redacted["header_line"] == "[REDACTED]"
    assert redacted2["proxy_auth"] == "[REDACTED]"


def test_urls_with_embedded_secret_are_bounded_not_parsed() -> None:
    payload = {"endpoint": f"https://user:{SECRET}@example.com/path"}
    redacted = redact_mapping(payload)
    serialized = json.dumps(redacted)
    assert SECRET not in serialized
    # The value is not key-sensitive; it gets the inline-secret marker.
    assert redacted["endpoint"] == "[REDACTED]"


def test_dsn_connection_strings_are_redacted() -> None:
    payload = {
        "DATABASE_URL": "postgres://user:hardcoded@host:5432/db",
        "redis_connection_string": "redis://:hardcoded@host:6379",
        "dsn_value": "postgres://user:password123@host",
    }
    redacted = redact_mapping(payload)
    _assert_no_secret(redacted)
    assert redacted["DATABASE_URL"] == "[REDACTED]"
    assert redacted["redis_connection_string"] == "[REDACTED]"


def test_multiline_exception_text_does_not_leak_secret() -> None:
    class Boom(Exception):
        pass

    exc = Boom(f"failed with token={SECRET}\nsecond line password123")
    payload = {"error": repr(exc)}
    redacted = redact_mapping(payload)
    _assert_no_secret(redacted)


def test_command_arguments_and_env_values_are_redacted() -> None:
    payload = {
        "command": ["run", f"--api-key={SECRET}"],
        "env": {"LLM_API_KEY": "sk-live-secret-key"},
        "args": [{"--password": "password123"}],
    }
    redacted = redact_mapping(payload)
    _assert_no_secret(redacted)
    assert redacted["env"]["LLM_API_KEY"] == "[REDACTED]"
    # Nested dict under a list with a sensitive key is redacted.
    assert redacted["args"][0]["--password"] == "[REDACTED]"


def test_cycle_is_handled_deterministically_without_leaking() -> None:
    nested: dict[str, object] = {"api_key": SECRET}
    container: dict[str, object] = {"child": nested}
    nested["parent"] = container  # create a cycle
    redacted = redact_mapping(container)
    _assert_no_secret(redacted)
    assert "[CYCLE]" in json.dumps(redacted)


def test_self_referencing_list_cycle_is_handled() -> None:
    inner: list[object] = []
    container = {"items": inner}
    inner.append({"secret": SECRET})
    inner.append(container)  # cycle through container
    redacted = redact_mapping(container)
    serialized = json.dumps(redacted)
    assert SECRET not in serialized
    assert "[CYCLE]" in serialized


def test_excessive_depth_is_bounded() -> None:
    # Build a nested structure deeper than MAX_REDACTION_DEPTH.
    deep: dict[str, object] = {"secret": SECRET}
    current = deep
    for _ in range(MAX_REDACTION_DEPTH + 5):
        wrapper: dict[str, object] = {"next": current}
        current = wrapper
    redacted = redact_mapping(current)
    serialized = json.dumps(redacted)
    assert SECRET not in serialized
    assert "[MAX_DEPTH]" in serialized


def test_oversized_mapping_is_bounded() -> None:
    huge = {
        f"key_{i}": SECRET if i == 50 else f"value_{i}" for i in range(MAX_COLLECTION_SIZE + 10)
    }
    redacted = redact_mapping(huge)
    serialized = json.dumps(redacted)
    assert SECRET not in serialized
    assert f"[OVERSIZED_MAPPING:{MAX_COLLECTION_SIZE + 10}]" in serialized


def test_oversized_sequence_is_bounded() -> None:
    huge_list = [{"token": SECRET} for _ in range(MAX_COLLECTION_SIZE + 5)]
    payload = {"items": huge_list}
    redacted = redact_mapping(payload)
    serialized = json.dumps(redacted)
    assert SECRET not in serialized
    assert f"[OVERSIZED_SEQUENCE:{MAX_COLLECTION_SIZE + 5}]" in serialized


def test_oversized_string_is_truncated() -> None:
    big_value = "A" * (MAX_STRING_LENGTH + 100)
    payload = {"note": big_value}
    redacted = redact_mapping(payload)
    assert redacted["note"] == f"[TRUNCATED:{MAX_STRING_LENGTH + 100}]"


def test_tuple_shape_preserved_after_redaction() -> None:
    payload = {"tuple_of_dicts": ({"password": SECRET}, {"safe": "ok"})}
    redacted = redact_mapping(payload)
    value = redacted["tuple_of_dicts"]
    assert isinstance(value, tuple)
    assert value[0]["password"] == "[REDACTED]"
    assert value[1]["safe"] == "ok"


def test_diagnostics_json_serialization_is_secret_free() -> None:
    report = probe_capabilities(ports=())
    config_shape = {
        "headers": [{"Authorization": f"Bearer {SECRET}"}],
        "env": {"LLM_API_KEY": "sk-live-secret-key"},
        "nested": [{"api_key": SECRET}],
    }
    serialized = diagnostics_json(report, config_shape=config_shape)
    for marker in FORBIDDEN_MARKERS:
        assert marker not in serialized
        assert marker.lower() not in serialized.lower()
    assert "[REDACTED]" in serialized


def test_diagnostics_payload_carries_privacy_flags() -> None:
    report = probe_capabilities(ports=())
    payload = diagnostics_payload(report, config_shape={"token": SECRET})
    assert payload["privacy"]["secrets_included"] is False
    serialized = json.dumps(payload, sort_keys=True)
    assert SECRET not in serialized


@pytest.mark.parametrize(
    "key",
    [
        "Authorization",
        "authorization",
        "AUTHORIZATION",
        "X-Api-Key",
        "x-api-key",
        "setCookie",
        "set-cookie",
        "refreshToken",
        "connectionString",
        "privateKey",
    ],
)
def test_sensitive_key_variants_are_redacted(key: str) -> None:
    payload = {key: SECRET}
    redacted = redact_mapping(payload)
    assert redacted[key] == "[REDACTED]"
