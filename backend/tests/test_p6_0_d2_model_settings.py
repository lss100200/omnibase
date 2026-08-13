"""Focused source/DTO contracts for P6.0-D2 model selection."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from pydantic import ValidationError

from omnibase.agent_alpha.schemas import AlphaInvokeRequest
from omnibase.user_settings.model_settings import (
    EMPLOYEE_ROLE_IDS,
    AgentModelScopeSnapshot,
    _tested_configuration_digest,
    detect_model_family,
)
from omnibase.user_settings.schemas import AgentModelSettingRead, AgentModelSettingWrite

MIGRATION = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "omnibase"
    / "migrations"
    / "versions"
    / "0016_p6_0_workspace_agent_model_overrides.py"
)
SOURCE = MIGRATION.read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def _assigned_string(name: str) -> str:
    for node in TREE.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
        ):
            assert isinstance(node.value, ast.Constant)
            assert isinstance(node.value.value, str)
            return node.value.value
    raise AssertionError(f"missing assignment: {name}")


def test_migration_0016_is_tenant_scoped_and_preserves_secret_ownership() -> None:
    assert _assigned_string("revision") == "0016"
    assert _assigned_string("down_revision") == "0015"
    assert 'if _migration_schema_scope() == "global":\n        return' in SOURCE
    assert '"workspace_agent_model_overrides"' in SOURCE
    assert '"model_provider_credentials.id"' in SOURCE
    for forbidden in ("api_key", "encrypted_api_key", "key_nonce"):
        assert forbidden not in SOURCE
    assert "_assert_global_downgrade_safe()" in SOURCE
    assert "SELECT schema_name FROM omnibase_meta.tenants" in SOURCE
    assert "tenant registry contains an invalid schema name" in SOURCE
    assert "downgrade refused before global revision change" in SOURCE
    assert "migration head must be exactly 0015" in SOURCE
    assert "table or credential ownership constraint remains" in SOURCE
    assert "model_provider_credentials_id_user_uq" in SOURCE


def test_ten_role_closed_set_and_alpha_request_default() -> None:
    assert EMPLOYEE_ROLE_IDS == (
        "parent",
        "product",
        "ux",
        "frontend",
        "backend",
        "data",
        "security",
        "qa",
        "operations",
        "docs",
    )
    request = AlphaInvokeRequest(
        agent_version_id="00000000-0000-0000-0000-000000000001",
        message="hello",
    )
    assert request.employee_role_id == "parent"
    with pytest.raises(ValidationError):
        AlphaInvokeRequest(
            agent_version_id="00000000-0000-0000-0000-000000000001",
            message="hello",
            employee_role_id="everyone",
        )


def test_write_contract_supports_inheritance_or_saved_credential_and_model() -> None:
    inherit = AgentModelSettingWrite(inherit_default=True, expected_version=0)
    assert inherit.provider_credential_id is None
    selected = AgentModelSettingWrite(
        inherit_default=False,
        provider_credential_id="00000000-0000-0000-0000-000000000002",
        requested_model_id="deepseek-chat",
        family_override="deepseek",
        expected_version=3,
    )
    assert selected.requested_model_id == "deepseek-chat"
    assert "api_key" not in AgentModelSettingWrite.model_fields
    assert "api_key" not in AgentModelSettingRead.model_fields


@pytest.mark.parametrize(
    ("model_id", "family"),
    [
        ("gpt-5.1", "openai"),
        ("claude-sonnet-4", "anthropic"),
        ("deepseek-chat", "deepseek"),
        ("DeepSeek\uff0dV4\uff0dFlash", "deepseek"),
        ("kimi-k3", "kimi"),
        ("glm-5.2", "glm"),
        ("claude-gpt-bridge", "generic"),
        ("custom-model", "generic"),
    ],
)
def test_model_name_family_detection_is_display_only(model_id: str, family: str) -> None:
    assert detect_model_family(model_id) == family


def test_custom_model_probe_digest_binds_override_identity_and_version() -> None:
    class Credential:
        id = "00000000-0000-0000-0000-000000000002"
        version = 4
        provider_id = "proxy"
        base_url = "https://example.invalid/v1"
        key_version = 3
        key_fingerprint = "sha256:example"
        is_active = True
        revoked_at = None

    scope = AgentModelScopeSnapshot(
        workspace_generation=1,
        binding_id="00000000-0000-0000-0000-000000000005",
        agent_version_digest="a" * 64,
    )
    first = _tested_configuration_digest(
        Credential(),  # type: ignore[arg-type]
        model_id="deepseek-v4-flash",
        override_id="00000000-0000-0000-0000-000000000003",
        override_version=1,
        scope=scope,
        endpoint_policy_digest="b" * 64,
    )
    recreated = _tested_configuration_digest(
        Credential(),  # type: ignore[arg-type]
        model_id="deepseek-v4-flash",
        override_id="00000000-0000-0000-0000-000000000004",
        override_version=1,
        scope=scope,
        endpoint_policy_digest="b" * 64,
    )
    updated = _tested_configuration_digest(
        Credential(),  # type: ignore[arg-type]
        model_id="deepseek-v4-flash",
        override_id="00000000-0000-0000-0000-000000000003",
        override_version=2,
        scope=scope,
        endpoint_policy_digest="b" * 64,
    )
    assert len({first, recreated, updated}) == 3


@pytest.mark.parametrize(
    "model_id",
    [
        "sk-secretvalue",
        "Bearer token-value",
        "eyJabc.def.ghi",
        "-----BEGIN PRIVATE KEY-----",
        "postgresql://user:pass@example.com/db",
        "https://user:pass@example.com/v1",
        "OPENAI_API_KEY=secret",
        r"C:\\project\\.env",
        "/etc/omnibase/secrets",
    ],
)
def test_model_id_rejects_secret_or_physical_locator(model_id: str) -> None:
    with pytest.raises(ValidationError) as exc_info:
        AgentModelSettingWrite(
            inherit_default=False,
            requested_model_id=model_id,
            expected_version=0,
        )
    assert model_id not in str(exc_info.value)


@pytest.mark.parametrize(
    "model_id",
    ["openai/gpt-5.1", "accounts/fireworks/models/deepseek-v3", "deepseek-v4-flash"],
)
def test_model_id_allows_public_registry_names(model_id: str) -> None:
    value = AgentModelSettingWrite(
        inherit_default=False,
        requested_model_id=model_id,
        expected_version=0,
    )
    assert value.requested_model_id == model_id
