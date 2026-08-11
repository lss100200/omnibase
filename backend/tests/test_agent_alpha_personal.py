"""Personal production canary composition and exact-scope facade tests."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from omnibase.agent_alpha.contracts import AlphaAgentProfile
from omnibase.agent_alpha.personal import (
    PersonalAlphaConfigurationError,
    PersonalAlphaPosture,
    PersonalCanaryAgentAlpha,
    build_personal_agent_alpha,
    personal_alpha_posture,
    resolve_personal_runtime_profile,
)
from omnibase.agent_alpha.service import (
    AgentAlphaService,
    AgentAlphaUnavailable,
    UnavailableAgentAlpha,
)
from omnibase.core.config import Settings
from omnibase.model_gateway import (
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
    ModelUsage,
)
from omnibase.production.personal_runtime_activation import (
    PersonalRuntimeCanaryConfig,
    activate_personal_runtime_canary,
    read_personal_runtime_status,
)

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)
REPO_ROOT = Path(__file__).resolve().parents[2]
OWNER_READINESS_SHA256 = "d71516d6a4c9ebd2e335c5e06e7507ce300ddc138e581b6dd34f9992933185de"


def _settings(env: str = "production") -> Settings:
    return Settings(
        env=env,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        minio_endpoint="localhost:9000",
        minio_access_key="k",
        minio_secret_key="s",  # noqa: S106 - synthetic non-secret test value
        redis_url="redis://localhost:6379/0",
        jwt_secret="x" * 40,
    )


class _Provider:
    provider_id = "fake-provider"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError(request)

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        del request
        yield ModelStreamChunk(
            provider_id="fake-provider",
            requested_model_id="model-alpha",
            actual_model_id="model-alpha",
            content="ok",
            finish_reason="stop",
            usage=ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2),
        )


def _gateway() -> ModelGateway:
    return ModelGateway(provider=_Provider(), model_id="model-alpha")


def _mapping() -> dict[str, object]:
    return {
        "agent_planner_enabled": False,
        "agent_version_id": "00000000-0000-0000-0000-000000000104",
        "canary_id": "00000000-0000-0000-0000-000000000100",
        "enterprise_approved_digest_present": False,
        "environment": "production",
        "external_side_effects": False,
        "invocation_mode": "no_tool",
        "max_canary_seconds": 3600,
        "max_concurrent_invocations": 1,
        "max_top_k": 5,
        "migration_0013_created": False,
        "migration_head": "0012",
        "multi_agent_enabled": False,
        "network": {"default_deny": True, "destinations": []},
        "owner_readiness": {
            "path": "deployment/production/personal-single-owner.example.json",
            "sha256": OWNER_READINESS_SHA256,
        },
        "owner_user_id": "00000000-0000-0000-0000-000000000103",
        "profile": "personal_single_owner",
        "schema_version": 1,
        "tenant_id": "00000000-0000-0000-0000-000000000101",
        "workspace_id": "00000000-0000-0000-0000-000000000102",
    }


def _config() -> PersonalRuntimeCanaryConfig:
    return PersonalRuntimeCanaryConfig.from_mapping(_mapping())


def _write_config(path: Path) -> None:
    path.write_text(
        json.dumps(_mapping(), separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


class _FakeFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):
        self.calls += 1
        raise AssertionError("the unit seam must be monkeypatched before DB access")


def _session_factory(factory: _FakeFactory | None = None) -> sessionmaker[Any]:
    return cast(sessionmaker[Any], factory or _FakeFactory())


def _profile() -> AlphaAgentProfile:
    return AlphaAgentProfile(
        agent_definition_id="00000000-0000-0000-0000-000000000201",
        agent_version_id=_config().agent_version_id,
        agent_version_digest="b" * 64,
        display_name="Canary",
        instructions="answer",
        instructions_digest="c" * 64,
        max_context_tokens=4096,
        allowed_tool_ids=(),
        workspace_agent_binding_id="00000000-0000-0000-0000-000000000202",
        resource_scope_digest="d" * 64,
        budget_policy_digest="e" * 64,
    )


class _Delegate:
    def list_profiles(self, **_: object) -> tuple[AlphaAgentProfile, ...]:
        other = replace(
            _profile(),
            agent_version_id="00000000-0000-0000-0000-000000000999",
        )
        return (_profile(), other)

    def invoke(self, **kwargs: object):
        return iter((kwargs,))

    def cancel(self, **_: object) -> bool:
        return True


def test_personal_profile_closed_set() -> None:
    assert resolve_personal_runtime_profile(None) is False
    assert resolve_personal_runtime_profile("") is False
    assert resolve_personal_runtime_profile("personal_single_owner") is True
    for token in ("true", "production", "enterprise_governed", "PERSONAL_SINGLE_OWNER"):
        with pytest.raises(PersonalAlphaConfigurationError, match="profile_invalid"):
            resolve_personal_runtime_profile(token)


def test_default_builder_returns_unavailable_before_loading_dependencies() -> None:
    result = build_personal_agent_alpha(
        tenant_id=_config().tenant_id,
        workspace_id=_config().workspace_id,
        actor_user_id=_config().owner_user_id,
        profile="",
    )
    assert isinstance(result, UnavailableAgentAlpha)


def test_posture_assembles_only_with_active_exact_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    config_path = (tmp_path / "canary.json").resolve()
    state_dir = (tmp_path / "state").resolve()
    _write_config(config_path)
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    factory = _FakeFactory()
    fake_session = SimpleNamespace(rollback=lambda: None, close=lambda: None)
    monkeypatch.setattr(
        "omnibase.agent_alpha.personal._migration_head",
        lambda _: "0012",
    )
    monkeypatch.setattr(
        "omnibase.agent_alpha.personal._open_tenant_session",
        lambda *_args, **_kwargs: fake_session,
    )
    monkeypatch.setattr(
        "omnibase.agent_alpha.personal._verify_live_single_owner",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "omnibase.agent_alpha.personal.read_personal_runtime_status",
        lambda path: read_personal_runtime_status(path, now=NOW),
    )

    posture = personal_alpha_posture(
        tenant_id=config.tenant_id,
        workspace_id=config.workspace_id,
        actor_user_id=config.owner_user_id,
        profile="personal_single_owner",
        config_path=str(config_path),
        state_dir=str(state_dir),
        readiness_root=str(REPO_ROOT),
        gate_values={
            "AGENT_RUNTIME_ENABLED": "true",
            "AGENT_PLANNER_ENABLED": "false",
            "MULTI_AGENT_ENABLED": "false",
        },
        settings=_settings(),
        session_factory=_session_factory(factory),
        gateway=_gateway(),
    )

    assert posture.assembled is True
    assert posture.canary_active is True
    assert posture.scope_matches is True
    assert posture.live_owner_verified is True
    assert posture.runtime_gate_enabled is True
    assert posture.planner_gate_enabled is False
    assert posture.multi_agent_gate_enabled is False


def test_posture_rejects_gate_and_scope_drift(tmp_path: Path) -> None:
    config_path = (tmp_path / "canary.json").resolve()
    state_dir = (tmp_path / "state").resolve()
    _write_config(config_path)
    posture = personal_alpha_posture(
        tenant_id="00000000-0000-0000-0000-000000000999",
        workspace_id=_config().workspace_id,
        actor_user_id=_config().owner_user_id,
        profile="personal_single_owner",
        config_path=str(config_path),
        state_dir=str(state_dir),
        readiness_root=str(REPO_ROOT),
        gate_values={
            "AGENT_RUNTIME_ENABLED": "false",
            "AGENT_PLANNER_ENABLED": "false",
            "MULTI_AGENT_ENABLED": "false",
        },
        settings=_settings(),
        session_factory=_session_factory(),
        gateway=_gateway(),
    )
    assert posture.assembled is False
    assert any("runtime_true" in blocker for blocker in posture.blockers)


def test_posture_rejects_owner_readiness_digest_drift(tmp_path: Path) -> None:
    mapping = _mapping()
    owner_readiness = cast(dict[str, object], mapping["owner_readiness"])
    owner_readiness["sha256"] = "0" * 64
    config_path = (tmp_path / "canary.json").resolve()
    config_path.write_text(
        json.dumps(mapping, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    posture = personal_alpha_posture(
        tenant_id=str(mapping["tenant_id"]),
        workspace_id=str(mapping["workspace_id"]),
        actor_user_id=str(mapping["owner_user_id"]),
        profile="personal_single_owner",
        config_path=str(config_path),
        state_dir=str((tmp_path / "state").resolve()),
        readiness_root=str(REPO_ROOT),
        gate_values={
            "AGENT_RUNTIME_ENABLED": "true",
            "AGENT_PLANNER_ENABLED": "false",
            "MULTI_AGENT_ENABLED": "false",
        },
        settings=_settings(),
        session_factory=_session_factory(),
        gateway=_gateway(),
    )

    assert posture.assembled is False
    assert any("readiness config SHA-256 drifted" in item for item in posture.blockers)


def test_posture_turns_database_failure_into_stable_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config()
    config_path = (tmp_path / "canary.json").resolve()
    state_dir = (tmp_path / "state").resolve()
    _write_config(config_path)
    activate_personal_runtime_canary(
        config,
        state_dir=state_dir,
        confirmed_plan_sha256=config.activation_plan().canonical_digest(),
        now=NOW,
    )
    monkeypatch.setattr("omnibase.agent_alpha.personal._migration_head", lambda _: "0012")
    monkeypatch.setattr(
        "omnibase.agent_alpha.personal._open_tenant_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OperationalError("SELECT secret_locator", {}, RuntimeError("private-host"))
        ),
    )
    monkeypatch.setattr(
        "omnibase.agent_alpha.personal.read_personal_runtime_status",
        lambda path: read_personal_runtime_status(path, now=NOW),
    )

    posture = personal_alpha_posture(
        tenant_id=config.tenant_id,
        workspace_id=config.workspace_id,
        actor_user_id=config.owner_user_id,
        profile="personal_single_owner",
        config_path=str(config_path),
        state_dir=str(state_dir),
        readiness_root=str(REPO_ROOT),
        gate_values={
            "AGENT_RUNTIME_ENABLED": "true",
            "AGENT_PLANNER_ENABLED": "false",
            "MULTI_AGENT_ENABLED": "false",
        },
        settings=_settings(),
        session_factory=_session_factory(),
        gateway=_gateway(),
    )

    assert posture.assembled is False
    assert posture.blockers == ("personal_runtime_database_unavailable",)


def test_builder_assembles_scoped_facade_after_verified_posture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_path = (tmp_path / "canary.json").resolve()
    state_dir = (tmp_path / "state").resolve()
    _write_config(config_path)
    config = _config()
    active_posture = PersonalAlphaPosture(
        profile_selected=True,
        feature_gates_valid=True,
        runtime_gate_enabled=True,
        planner_gate_enabled=False,
        multi_agent_gate_enabled=False,
        canary_state="active",
        canary_active=True,
        canary_id=config.canary_id,
        canary_expires_at="2026-08-10T13:00:00Z",
        scope_matches=True,
        live_owner_verified=True,
        environment_allowed=True,
        gateway_configured=True,
        migration_ready=True,
        assembled=True,
        blockers=(),
    )
    monkeypatch.setattr(
        "omnibase.agent_alpha.personal.personal_alpha_posture",
        lambda **_: active_posture,
    )
    factory = _FakeFactory()
    result = build_personal_agent_alpha(
        tenant_id=config.tenant_id,
        workspace_id=config.workspace_id,
        actor_user_id=config.owner_user_id,
        profile="personal_single_owner",
        config_path=str(config_path),
        state_dir=str(state_dir),
        readiness_root=str(REPO_ROOT),
        gate_values={
            "AGENT_RUNTIME_ENABLED": "true",
            "AGENT_PLANNER_ENABLED": "false",
            "MULTI_AGENT_ENABLED": "false",
        },
        settings=_settings(),
        session_factory=_session_factory(factory),
        gateway=_gateway(),
    )
    assert isinstance(result, PersonalCanaryAgentAlpha)


def test_scoped_facade_filters_agent_and_rejects_scope_or_top_k() -> None:
    config = _config()
    facade = PersonalCanaryAgentAlpha(cast(AgentAlphaService, _Delegate()), config)
    profiles = facade.list_profiles(
        tenant_id=config.tenant_id,
        workspace_id=config.workspace_id,
        actor_user_id=config.owner_user_id,
    )
    assert [item.agent_version_id for item in profiles] == [config.agent_version_id]

    with pytest.raises(AgentAlphaUnavailable, match="scope_mismatch"):
        facade.list_profiles(
            tenant_id=config.tenant_id,
            workspace_id="00000000-0000-0000-0000-000000000999",
            actor_user_id=config.owner_user_id,
        )
    with pytest.raises(AgentAlphaUnavailable, match="agent_version_mismatch"):
        facade.invoke(
            tenant_id=config.tenant_id,
            tenant_schema="tenant_schema",
            workspace_id=config.workspace_id,
            actor_user_id=config.owner_user_id,
            agent_version_id="00000000-0000-0000-0000-000000000999",
            message="hello",
            top_k=1,
            idempotency_key="key",
            retry_of=None,
        )
    with pytest.raises(AgentAlphaUnavailable, match="top_k_exceeded"):
        facade.invoke(
            tenant_id=config.tenant_id,
            tenant_schema="tenant_schema",
            workspace_id=config.workspace_id,
            actor_user_id=config.owner_user_id,
            agent_version_id=config.agent_version_id,
            message="hello",
            top_k=config.max_top_k + 1,
            idempotency_key="key",
            retry_of=None,
        )
