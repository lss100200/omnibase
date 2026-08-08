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
    MAX_HORIZONTAL_WS,
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
    payload2 = {"proxy_header_line": "basic dXNlcjpwYXNzd29yZA=="}
    redacted = redact_mapping(payload)
    redacted2 = redact_mapping(payload2)
    assert SECRET not in json.dumps(redacted)
    # Sensitive header name -> value replaced structurally, scheme stays visible.
    assert redacted["header_line"] == "authorization: [REDACTED]"
    # Non-sensitive name with a basic- marker -> fail-closed whole-line marker.
    assert redacted2["proxy_header_line"] == "[REDACTED]"


def test_urls_with_embedded_secret_are_structurally_redacted() -> None:
    payload = {"endpoint": f"https://user:{SECRET}@example.com/path"}
    redacted = redact_mapping(payload)
    serialized = json.dumps(redacted)
    assert SECRET not in serialized
    # The URI userinfo password is parsed and replaced without echoing it; the
    # scheme and host stay visible so operators can still diagnose the target.
    assert redacted["endpoint"] == "https://user:[REDACTED]@example.com/path"


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


# --- Opaque secrets without any token/secret/password keyword --------------
#
# The round-1 redactor only recognized generic words (bearer/basic/token/
# secret/password) inside string values, so URL/DSN/log samples whose values
# carried no such keyword leaked verbatim. Every secret below is opaque on
# purpose; redaction must come from structural parsing (URI userinfo, query
# keys, NAME=value assignments, headers, DSN forms), never from keyword
# guessing or secret prefixes.

OPAQUE_SECRETS = (
    "abc123xyz",  # X-Api-Key header value
    "abc123",  # URI userinfo password and query value
    "sk-proj-abc123xyz",  # provider-key shape under a sensitive name
    "zq7x2m9k4v",  # DSN userinfo password
    "8f3a9b1c",  # access_token query value
    "wX9fQ2",  # CLI --auth value
    "Lk3mN9",  # JSON-ish api_key value
    "Qq7xR5t2",  # fragment api_key value
    "frag99",  # fragment key value
    "opaque77",  # api_key query value
)

REVIEW_PAYLOAD = {
    "argv": ["--header", "X-Api-Key: abc123xyz"],
    "endpoint": "https://user:abc123@example.com/path?key=abc123",
    "exception": "connection failed postgres://user:abc123@host/db",
    "log_line": "OPENAI_API_KEY=sk-proj-abc123xyz",
}


def _assert_opaque_secrets_absent(payload: object) -> None:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    for secret in OPAQUE_SECRETS:
        assert secret not in serialized


def test_review_payload_opaque_secrets_are_redacted() -> None:
    # The exact review payload from round 2: no sample contains a
    # token/secret/password keyword, so only structural parsing can save it.
    redacted = redact_mapping(REVIEW_PAYLOAD)
    _assert_opaque_secrets_absent(redacted)
    _assert_opaque_secrets_absent(
        json.loads(diagnostics_json(probe_capabilities(ports=()), config_shape=REVIEW_PAYLOAD))
    )
    # The sensitive structure stays visible for diagnosis; only values are gone.
    assert redacted["argv"] == ["--header", "X-Api-Key: [REDACTED]"]
    assert "[REDACTED]" in redacted["endpoint"]
    assert "[REDACTED]" in redacted["exception"]
    assert redacted["log_line"] == "OPENAI_API_KEY=[REDACTED]"


def test_uri_query_sensitive_keys_and_fragments_are_redacted() -> None:
    payload = {
        "endpoint": (
            "https://api.example.com/v1?access_token=8f3a9b1c&api_key=opaque77"
            "&sig=9b1c2d3e&credential=zz99"
        ),
        "fragment_url": "https://host.example/page#api_key=frag99",
    }
    redacted = redact_mapping(payload)
    _assert_opaque_secrets_absent(redacted)
    assert "access_token=[REDACTED]" in redacted["endpoint"]
    assert "api_key=[REDACTED]" in redacted["endpoint"]
    assert "sig=[REDACTED]" in redacted["endpoint"]
    assert "credential=[REDACTED]" in redacted["endpoint"]
    assert redacted["fragment_url"] == "https://host.example/page#api_key=[REDACTED]"


def test_dsn_userinfo_opaque_password_is_redacted() -> None:
    payload = {
        "exception": "connection failed postgres://app:zq7x2m9k4v@db.internal:5432/omnibase",
        "redis_endpoint": "redis://:zq7x2m9k4v@cache.internal:6379/0",
    }
    redacted = redact_mapping(payload)
    _assert_opaque_secrets_absent(redacted)
    assert redacted["exception"] == (
        "connection failed postgres://app:[REDACTED]@db.internal:5432/omnibase"
    )
    assert redacted["redis_endpoint"] == "redis://:[REDACTED]@cache.internal:6379/0"


def test_cli_equals_and_header_forms_are_redacted() -> None:
    payload = {
        "argv": ["run", "--auth=wX9fQ2", "--api-key=wX9fQ2", "server"],
        "header_line": "X-Api-Key: abc123xyz",
        "header_upper": "AUTHORIZATION: Bearer abc123xyz",
    }
    redacted = redact_mapping(payload)
    _assert_opaque_secrets_absent(redacted)
    assert redacted["argv"] == ["run", "--auth=[REDACTED]", "--api-key=[REDACTED]", "server"]
    assert redacted["header_line"] == "X-Api-Key: [REDACTED]"
    assert redacted["header_upper"] == "AUTHORIZATION: [REDACTED]"


def test_jsonish_log_line_assignments_are_redacted() -> None:
    payload = {
        "log_line": '{"provider": "openai", "api_key": "Lk3mN9", "status": 200}',
        "json_record": '{"dsn": "postgres://app:zq7x2m9k4v@db/internal"}',
    }
    redacted = redact_mapping(payload)
    _assert_opaque_secrets_absent(redacted)
    assert '"api_key": [REDACTED]' in redacted["log_line"]
    assert '"provider": "openai"' in redacted["log_line"]
    # The dsn name policy redacts the whole value; the userinfo inside it was
    # already removed by the URI pass before the name match replaced the rest.
    assert redacted["json_record"] == '{"dsn": [REDACTED]}'


def test_provider_key_shapes_redacted_by_name_not_prefix() -> None:
    # Arbitrary providers can use opaque values; coverage comes from the
    # sensitive name policy, never from guessing sk-/ghp-/hf_ prefixes.
    payload = {
        "env": {
            "STRIPE_API_KEY": "sk_live_opaque77",
            "GITHUB_TOKEN": "ghp_8f3a9b1c",
            "X_CORP_CREDENTIAL": "Qq7xR5t2",
        },
        "log_line": "OPENAI_API_KEY=sk-proj-abc123xyz status=200",
    }
    redacted = redact_mapping(payload)
    _assert_opaque_secrets_absent(redacted)
    assert redacted["env"]["STRIPE_API_KEY"] == "[REDACTED]"
    assert redacted["env"]["GITHUB_TOKEN"] == "[REDACTED]"
    assert redacted["env"]["X_CORP_CREDENTIAL"] == "[REDACTED]"
    assert redacted["log_line"] == "OPENAI_API_KEY=[REDACTED] status=200"


def test_user_only_userinfo_is_not_mangled() -> None:
    # A URI userinfo without a password carries no credential and stays intact.
    payload = {"endpoint": "https://user@example.com/path"}
    redacted = redact_mapping(payload)
    assert redacted["endpoint"] == "https://user@example.com/path"


def test_encoded_colon_userinfo_password_is_redacted() -> None:
    payload = {"endpoint": "postgres://app%3Azq7x2m9k4v@db.internal/db"}
    redacted = redact_mapping(payload)
    _assert_opaque_secrets_absent(redacted)
    assert redacted["endpoint"] == "postgres://app%3A[REDACTED]@db.internal/db"


def test_ordinary_text_is_not_rewritten_unpredictably() -> None:
    payload = {
        "note": "the quick brown fox jumps over the lazy dog",
        "time_note": "we shipped the release at 12:30 and it passed",
    }
    redacted = redact_mapping(payload)
    assert redacted["note"] == "the quick brown fox jumps over the lazy dog"
    assert redacted["time_note"] == "we shipped the release at 12:30 and it passed"


def test_lifecycle_style_output_lines_are_redacted() -> None:
    # stdout/stderr bundles produced by the lifecycle wrapper carry exactly
    # this shape: {"lines": "<compose output>"}.
    stdout = (
        "backend  Running 0.0.0.0:8000->8000/tcp\n"
        "postgres  Running  postgres://app:zq7x2m9k4v@db.internal:5432/omnibase\n"
        "warning: OPENAI_API_KEY=sk-proj-abc123xyz will be ignored\n"
    )
    redacted = redact_mapping({"lines": stdout})
    _assert_opaque_secrets_absent(redacted)
    rendered = redacted["lines"]
    assert "postgres://app:[REDACTED]@db.internal:5432/omnibase" in rendered
    assert "OPENAI_API_KEY=[REDACTED]" in rendered


# --- Round 3: cross-element CLI pairs, whitespace forms, whole-item
# --- fail-closed limits and the closed-set sensitive-name policy -------------

CLI_PAIR_SECRET = "9f7vK2pQ"  # opaque: no token/secret/password keyword


def test_cross_element_cli_argument_pairs_are_redacted() -> None:
    payload = {
        "argv": ["--api-key", CLI_PAIR_SECRET, "--profile", "lite", "server"],
        "nested": [["--token", CLI_PAIR_SECRET], ["--password", CLI_PAIR_SECRET]],
        "tuple_args": ("--token", CLI_PAIR_SECRET),
    }
    redacted = redact_mapping(payload)
    _assert_opaque_secrets_absent(redacted)
    # Sensitive flag + following element -> the value is redacted whole; the
    # non-sensitive "--profile" pair is preserved.
    assert redacted["argv"] == ["--api-key", "[REDACTED]", "--profile", "lite", "server"]
    assert redacted["nested"] == [
        ["--token", "[REDACTED]"],
        ["--password", "[REDACTED]"],
    ]
    assert redacted["tuple_args"] == ("--token", "[REDACTED]")


def test_cross_element_cli_non_sensitive_args_are_preserved() -> None:
    payload = {
        "argv": ["--profile", "lite", "--verbose", "--service", "backend", "run"],
    }
    redacted = redact_mapping(payload)
    assert redacted["argv"] == ["--profile", "lite", "--verbose", "--service", "backend", "run"]


def test_cross_element_cli_sensitive_flag_without_value_fails_closed() -> None:
    payload = {"argv": ["--password"], "tail": ["--api-key", "--profile", "lite"]}
    redacted = redact_mapping(payload)
    # A sensitive flag with no following value is redacted itself; a following
    # element that is itself a flag is never swallowed as a value.
    assert redacted["argv"] == ["[REDACTED]"]
    assert redacted["tail"] == ["[REDACTED]", "--profile", "lite"]


def test_cross_element_cli_secret_in_serialized_diagnostics_absent() -> None:
    payload = {"argv": ["--token", CLI_PAIR_SECRET], "endpoint": f"key={CLI_PAIR_SECRET}"}
    serialized = diagnostics_json(probe_capabilities(ports=()), config_shape=payload)
    assert CLI_PAIR_SECRET not in serialized
    assert "[REDACTED]" in serialized


def test_bounded_whitespace_assignment_forms_are_redacted() -> None:
    payload = {
        "env_line": "API_KEY = abc123xyz",
        "cli_line": "--token  =  abc123xyz",
        "header_line": "X-Api-Key : abc123xyz",
        "json_line": '"access_token"  :  "abc123xyz"',
        "fragment": "#api_key = frag99",
    }
    redacted = redact_mapping(payload)
    _assert_opaque_secrets_absent(redacted)
    assert redacted["env_line"] == "API_KEY=[REDACTED]"
    assert redacted["cli_line"] == "--token=[REDACTED]"
    assert redacted["header_line"] == "X-Api-Key: [REDACTED]"
    assert redacted["json_line"] == '"access_token": [REDACTED]'
    assert redacted["fragment"] == "#api_key=[REDACTED]"


def test_bounded_whitespace_forms_do_not_rewrite_ordinary_text() -> None:
    payload = {
        "note": "the mode = standard and profile = lite",
        "time_note": "released at 12 : 30 and passed",
    }
    redacted = redact_mapping(payload)
    assert redacted["note"] == "the mode = standard and profile = lite"
    assert redacted["time_note"] == "released at 12 : 30 and passed"


def test_oversized_sensitive_header_fails_closed_whole_item() -> None:
    secret = "x" * (512 + 300)
    payload = {"header_line": f"X-Api-Key: {secret}"}
    redacted = redact_mapping(payload)
    assert secret not in json.dumps(redacted)
    # The whole item is replaced; no 512-char prefix with a leaked tail.
    assert redacted["header_line"] == "[REDACTED]"


def test_oversized_sensitive_assignment_fails_closed_whole_item() -> None:
    secret = "y" * (512 + 300)
    payload = {"log_line": f"API_KEY={secret}"}
    redacted = redact_mapping(payload)
    assert secret not in json.dumps(redacted)
    assert redacted["log_line"] == "[REDACTED]"


def test_oversized_sensitive_cli_item_fails_closed_whole_item() -> None:
    secret = "z" * (512 + 300)
    payload = {"argv": ["--token", secret]}
    redacted = redact_mapping(payload)
    assert secret not in json.dumps(redacted)
    # Cross-element pair: the following element is redacted whole regardless of
    # size (the flag is kept for diagnosis).
    assert redacted["argv"] == ["--token", "[REDACTED]"]
    payload2 = {"argv": [f"--token={secret}"]}
    redacted2 = redact_mapping(payload2)
    assert secret not in json.dumps(redacted2)
    assert redacted2["argv"] == ["[REDACTED]"]


def test_oversized_non_sensitive_item_is_not_whole_item_redacted() -> None:
    value = "n" * 700
    payload = {"note": f"NOTE={value}"}
    redacted = redact_mapping(payload)
    # The bounded policy only fail-closes sensitive names; an oversized
    # non-sensitive assignment stays structurally visible.
    assert redacted["note"] == f"NOTE={value}"


def test_sensitive_key_policy_is_closed_set_not_arbitrary_substring() -> None:
    payload = {
        "monkey": "banana",
        "keyboard_layout": "qwerty",
        "design": "modern",
        "session_count": 42,
        "api_key": "abc123",
        "access_token": "xyz789",
        "signature": "sig123",
        "session_token": "tok456",
    }
    redacted = redact_mapping(payload)
    # Preserved: keys that merely CONTAIN sensitive fragments must survive.
    assert redacted["monkey"] == "banana"
    assert redacted["keyboard_layout"] == "qwerty"
    assert redacted["design"] == "modern"
    assert redacted["session_count"] == 42
    # Redacted: exact closed-set tokens and bounded-suffix variants.
    assert redacted["api_key"] == "[REDACTED]"
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["signature"] == "[REDACTED]"
    assert redacted["session_token"] == "[REDACTED]"


def test_closed_set_policy_applies_to_parsed_names_too() -> None:
    payload = {
        "log_line": "monkey=banana keyboard_layout=qwerty design=modern session_count=42 "
        "api_key=abc123 access_token=xyz789",
        "header_line": "monkey: banana",
    }
    redacted = redact_mapping(payload)
    assert redacted["log_line"] == (
        "monkey=banana keyboard_layout=qwerty design=modern session_count=42 "
        "api_key=[REDACTED] access_token=[REDACTED]"
    )
    assert redacted["header_line"] == "monkey: banana"


def test_whitespace_cli_pair_across_elements_with_bounded_spacing() -> None:
    payload = {"argv": ["--api-key = " + CLI_PAIR_SECRET]}
    redacted = redact_mapping(payload)
    assert CLI_PAIR_SECRET not in json.dumps(redacted)
    assert redacted["argv"] == ["--api-key=[REDACTED]"]


# --- Round 4: cross-element dash values, deterministic token-state parser,
# --- bounded horizontal whitespace, quoted values, header tails,
# --- camelCase/PascalCase tokenization and the narrowed _key suffix rule -----

DASH_VALUE_SECRETS = ("--q7x9opaque", "-opaque", "rest8v")


def _assert_dash_secrets_absent(payload: object) -> None:
    serialized = json.dumps(payload, sort_keys=True, default=str)
    for secret in DASH_VALUE_SECRETS:
        assert secret not in serialized
    # The exact "--" element consumed as a value slot must vanish (flag names
    # like "--api-key" legitimately remain for diagnosis).
    assert '"--"' not in serialized


def test_cross_element_sensitive_flag_redacts_dash_prefixed_value_slot() -> None:
    # Once a sequence element is a sensitive CLI flag, its next value slot is
    # redacted ENTIRELY even when the value starts with "-" or "--".
    payload = {
        "argv": ["--api-key", "--q7x9opaque", "--token", "-opaque", "--password", "--"],
        "nested": [["--api-key", "--q7x9opaque"], ["--token", "-opaque"]],
        "tuple_args": ("--password", "--"),
    }
    redacted = redact_mapping(payload)
    _assert_dash_secrets_absent(redacted)
    _assert_dash_secrets_absent(
        json.loads(diagnostics_json(probe_capabilities(ports=()), config_shape=payload))
    )
    assert redacted["argv"] == [
        "--api-key",
        "[REDACTED]",
        "--token",
        "[REDACTED]",
        "--password",
        "[REDACTED]",
    ]
    assert redacted["nested"] == [
        ["--api-key", "[REDACTED]"],
        ["--token", "[REDACTED]"],
    ]
    assert redacted["tuple_args"] == ("--password", "[REDACTED]")


def test_sensitive_flag_never_swallows_allowlisted_flag_structure() -> None:
    # A sensitive flag followed by an element that deterministically belongs
    # to ANOTHER allowlisted flag has no value here: it fails closed on its
    # own and never swallows that flag or its value.
    payload = {
        "argv": ["--api-key", "--profile", "lite"],
        "service_args": ["--token", "--service", "backend"],
        "tail_args": ["--password", "--tail", "200"],
        "double_sensitive": ["--api-key", "--token", "x"],
        "trailing_flag": ["--api-key", "--profile"],
    }
    redacted = redact_mapping(payload)
    assert redacted["argv"] == ["[REDACTED]", "--profile", "lite"]
    assert redacted["service_args"] == ["[REDACTED]", "--service", "backend"]
    assert redacted["tail_args"] == ["[REDACTED]", "--tail", "200"]
    # Two sensitive flags in a row: neither swallows the other; both fail
    # closed (the second still redacts its own following value).
    assert redacted["double_sensitive"] == ["[REDACTED]", "--token", "[REDACTED]"]
    assert redacted["trailing_flag"] == ["[REDACTED]", "--profile"]


def test_wide_bounded_whitespace_forms_are_still_redacted() -> None:
    # "More than 8 spaces means pass-through" must not hold: any bounded
    # horizontal whitespace run around the separator is recognized.
    payload = {
        "env_line": "API_KEY" + " " * 20 + "=" + " " * 10 + "abc123xyz",
        "cli_line": "--token" + " " * 40 + "=  abc123xyz",
        "header_line": "X-Api-Key" + " " * 60 + ": abc123xyz",
        "json_line": '"access_token"' + " " * 30 + ":  " + '"abc123xyz"',
    }
    redacted = redact_mapping(payload)
    _assert_opaque_secrets_absent(redacted)
    assert redacted["env_line"] == "API_KEY=[REDACTED]"
    assert redacted["cli_line"] == "--token=[REDACTED]"
    assert redacted["header_line"] == "X-Api-Key: [REDACTED]"
    assert redacted["json_line"] == '"access_token": [REDACTED]'


def test_over_limit_whitespace_fails_closed_whole_item() -> None:
    # Parser state beyond the bounded horizontal-whitespace limit fails closed
    # as a WHOLE item: neither the name nor any value tail survives.
    secrets = ("opaque_ws_over", "opaque_colon_over", "opaque_cli_over")
    payload = {
        "env_line": "API_KEY" + " " * (MAX_HORIZONTAL_WS + 10) + "= opaque_ws_over",
        "header_line": "X-Api-Key" + " " * (MAX_HORIZONTAL_WS + 10) + ": opaque_colon_over",
        "cli_line": "--token" + " " * (MAX_HORIZONTAL_WS + 10) + "= opaque_cli_over",
        "within_limit": "API_KEY" + " " * MAX_HORIZONTAL_WS + "= abc123xyz",
    }
    redacted = redact_mapping(payload)
    serialized = json.dumps(redacted)
    for secret in secrets:
        assert secret not in serialized
    assert redacted["env_line"] == "[REDACTED]"
    assert redacted["header_line"] == "[REDACTED]"
    assert redacted["cli_line"] == "[REDACTED]"
    # Exactly at the bound the item is recognized and its value redacted.
    assert redacted["within_limit"] == "API_KEY=[REDACTED]"


def test_quoted_assignment_values_are_consumed_completely() -> None:
    payload = {
        "log_line": 'OPENAI_API_KEY = "q7x9opaque rest8v"',
        "cli_line": '--token = "q7x9opaque rest8v"',
        "header_line": 'X-Api-Key : "q7x9opaque rest8v"',
    }
    redacted = redact_mapping(payload)
    _assert_dash_secrets_absent(redacted)
    assert redacted["log_line"] == "OPENAI_API_KEY=[REDACTED]"
    assert redacted["cli_line"] == "--token=[REDACTED]"
    assert redacted["header_line"] == "X-Api-Key: [REDACTED]"


def test_unterminated_quote_fails_closed_whole_item() -> None:
    payload = {"log_line": 'OPENAI_API_KEY = "q7x9opaque rest8v'}
    redacted = redact_mapping(payload)
    _assert_dash_secrets_absent(redacted)
    assert redacted["log_line"] == "[REDACTED]"


def test_confirmed_sensitive_header_redacts_entire_value_including_tail() -> None:
    payload = {
        "auth_line": "Authorization: q7x9opaque;rest8v",
        "key_line": "X-Api-Key: q7x9opaque;rest8v more-tail",
        "json_line": '"api_key": "q7x9opaque;rest8v", "status": 200',
    }
    redacted = redact_mapping(payload)
    _assert_dash_secrets_absent(redacted)
    assert redacted["auth_line"] == "Authorization: [REDACTED]"
    assert redacted["key_line"] == "X-Api-Key: [REDACTED]"
    assert redacted["json_line"] == '"api_key": [REDACTED]'


def test_camel_and_pascal_case_tokens_are_redacted() -> None:
    payload = {
        "mapping": {
            "stripeApiKey": "sk_live_9f7vK2pQ",
            "providerPassword": "pwd_9f7vK2pQ",
            "myToken": "tok_9f7vK2pQ",
            "azureAccessToken": "az_9f7vK2pQ",
            "openAiApiKey": "sk_9f7vK2pQ",
            "StripeApiKey": "sk_9f7vK2pQ",
        },
        "log_line": "stripeApiKey=9f7vK2pQ azureAccessToken : 9f7vK2pQ",
        "cli_line": "--openAiApiKey=9f7vK2pQ",
    }
    redacted = redact_mapping(payload)
    serialized = json.dumps(redacted)
    assert "9f7vK2pQ" not in serialized
    for key in (
        "stripeApiKey",
        "providerPassword",
        "myToken",
        "azureAccessToken",
        "openAiApiKey",
        "StripeApiKey",
    ):
        assert redacted["mapping"][key] == "[REDACTED]"
    assert redacted["log_line"] == "stripeApiKey=[REDACTED] azureAccessToken: [REDACTED]"
    assert redacted["cli_line"] == "--openAiApiKey=[REDACTED]"


def test_narrowed_key_suffix_rule() -> None:
    # The _key suffix is narrow: generic database/keyboard keys are preserved
    # while the specific sensitive *_key family is redacted.
    payload = {
        "preserved": {
            "sort_key": "created_at",
            "cache_key": "user:42",
            "foreign_key": "tenant_id",
            "keyboard_layout": "qwerty",
            "monkey": "banana",
            "session_count": 42,
        },
        "redacted": {
            "api_key": "abc123",
            "secret_key": "def456",
            "access_key": "ghi789",
            "signing_key": "jkl012",
            "private_key": "mno345",
            "encryption_key": "pqr678",
        },
        "log_line": (
            "sort_key=created_at cache_key=user:42 foreign_key=tenant_id "
            "api_key=abc123 encryption_key=pqr678"
        ),
        "secret_line": "secret_key=def456",
        "camel": {"sortKey": "created_at", "cacheKey": "user:42", "stripeApiKey": "sk_live_9"},
    }
    redacted = redact_mapping(payload)
    for key in ("sort_key", "cache_key", "foreign_key", "keyboard_layout", "monkey"):
        assert redacted["preserved"][key] == payload["preserved"][key]
    for key in (
        "api_key",
        "secret_key",
        "access_key",
        "signing_key",
        "private_key",
        "encryption_key",
    ):
        assert redacted["redacted"][key] == "[REDACTED]"
    assert redacted["log_line"] == (
        "sort_key=created_at cache_key=user:42 foreign_key=tenant_id "
        "api_key=[REDACTED] encryption_key=[REDACTED]"
    )
    # The preserved name itself stays, the sensitive value is gone.
    assert "def456" not in json.dumps(redacted)
    # "secret_key" as a name still contains the "secret" keyword after its
    # value is redacted, so the keyword fail-closed fallback turns the whole
    # line into one [REDACTED] item — no tail can survive either way.
    assert redacted["secret_line"] == "[REDACTED]"
    assert redacted["camel"]["sortKey"] == "created_at"
    assert redacted["camel"]["cacheKey"] == "user:42"
    assert redacted["camel"]["stripeApiKey"] == "[REDACTED]"
