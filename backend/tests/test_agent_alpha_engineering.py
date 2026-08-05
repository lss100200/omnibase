"""Engineering activation and composition tests for the tool-free Agent Alpha.

The composition seam must fail closed on every non-engineering condition:
missing/exact-false flag, truthy drift, production or unknown environment,
any Phase 5 Feature Gate true, missing gateway, missing/incorrect migration
head.  Only an exactly-enabled development environment with a configured
gateway and head 0012 may assemble the DB-backed service.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from omnibase.agent_alpha.engineering import (
    EngineeringAlphaConfigurationError,
    build_engineering_agent_alpha,
    engineering_alpha_status,
    resolve_engineering_alpha_flag,
)
from omnibase.agent_alpha.service import AgentAlphaService, UnavailableAgentAlpha
from omnibase.core.config import Settings
from omnibase.model_gateway import (
    ModelGateway,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
    ModelUsage,
)


def _settings(env: str) -> Settings:
    return Settings(
        env=env,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        minio_endpoint="localhost:9000",
        minio_access_key="k",
        minio_secret_key="s",  # noqa: S106 - synthetic non-secret test value
        redis_url="redis://localhost:6379/0",
        jwt_secret="x" * 40,
    )


class _HeadRow:
    def scalar_one_or_none(self) -> str:
        return "0012"


class _MissingHeadRow:
    def scalar_one_or_none(self) -> str:
        return None


class _FakeSession:
    def __init__(self, row: _HeadRow) -> None:
        self._row = row

    def execute(self, *_: object, **__: object) -> _HeadRow:
        return self._row

    def close(self) -> None:
        return


class _FakeFactory:
    def __init__(self, *, head: str | None = "0012", raise_on_call: bool = False) -> None:
        self._head = head
        self._raise_on_call = raise_on_call

    def __call__(self) -> _FakeSession:
        if self._raise_on_call:
            raise RuntimeError("database unavailable")
        return _FakeSession(_HeadRow() if self._head == "0012" else _MissingHeadRow())


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


def _valid_kwargs() -> dict[str, object]:
    return {
        "flag": "true",
        "settings": _settings("development"),
        "session_factory": _FakeFactory(),
        "gateway": _gateway(),
    }


# ---------------------------------------------------------------------------
# A. Flag parsing
# ---------------------------------------------------------------------------


def test_engineering_flag_defaults_to_false_when_missing() -> None:
    assert resolve_engineering_alpha_flag(None) is False
    assert resolve_engineering_alpha_flag("") is False


def test_engineering_flag_exact_tokens() -> None:
    assert resolve_engineering_alpha_flag("true") is True
    assert resolve_engineering_alpha_flag("false") is False


@pytest.mark.parametrize("token", ["TRUE", "True", " yes", "1", "on", "ON", "enabled", "null"])
def test_engineering_flag_truthy_drift_is_rejected(token: str) -> None:
    with pytest.raises(EngineeringAlphaConfigurationError, match="flag_invalid"):
        resolve_engineering_alpha_flag(token)


# ---------------------------------------------------------------------------
# B. Composition seam
# ---------------------------------------------------------------------------


def test_disabled_flag_returns_unavailable_before_any_dependency() -> None:
    result = build_engineering_agent_alpha(
        flag="false",
        settings=_settings("development"),
        session_factory=_FakeFactory(),
        gateway=_gateway(),
    )
    assert isinstance(result, UnavailableAgentAlpha)


def test_missing_flag_returns_unavailable() -> None:
    result = build_engineering_agent_alpha(
        flag=None,
        settings=_settings("development"),
        session_factory=_FakeFactory(),
        gateway=_gateway(),
    )
    assert isinstance(result, UnavailableAgentAlpha)


def test_production_environment_always_rejects() -> None:
    result = build_engineering_agent_alpha(
        flag="true",
        settings=_settings("production"),
        session_factory=_FakeFactory(),
        gateway=_gateway(),
    )
    assert isinstance(result, UnavailableAgentAlpha)


def test_staging_environment_rejects() -> None:
    result = build_engineering_agent_alpha(
        flag="true",
        settings=_settings("staging"),
        session_factory=_FakeFactory(),
        gateway=_gateway(),
    )
    assert isinstance(result, UnavailableAgentAlpha)


def test_missing_gateway_returns_unavailable() -> None:
    from omnibase.model_gateway.service import UnavailableModelGateway

    result = build_engineering_agent_alpha(
        flag="true",
        settings=_settings("development"),
        session_factory=_FakeFactory(),
        gateway=UnavailableModelGateway(),
    )
    assert isinstance(result, UnavailableAgentAlpha)


def test_migration_head_not_0012_returns_unavailable() -> None:
    result = build_engineering_agent_alpha(
        flag="true",
        settings=_settings("development"),
        session_factory=_FakeFactory(head="0010"),
        gateway=_gateway(),
    )
    assert isinstance(result, UnavailableAgentAlpha)


def test_missing_database_returns_unavailable() -> None:
    result = build_engineering_agent_alpha(
        flag="true",
        settings=_settings("development"),
        session_factory=_FakeFactory(raise_on_call=True),
        gateway=_gateway(),
    )
    assert isinstance(result, UnavailableAgentAlpha)


def test_valid_engineering_configuration_assembles_service() -> None:
    result = build_engineering_agent_alpha(**_valid_kwargs())
    assert isinstance(result, AgentAlphaService)


def test_production_never_assembles_even_with_all_other_conditions() -> None:
    for env in ("production", "staging"):
        result = build_engineering_agent_alpha(
            flag="true",
            settings=_settings(env),
            session_factory=_FakeFactory(),
            gateway=_gateway(),
        )
        assert isinstance(result, UnavailableAgentAlpha)


def test_phase5_gate_true_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", "true")
    result = build_engineering_agent_alpha(
        flag="true",
        settings=_settings("development"),
        session_factory=_FakeFactory(),
        gateway=_gateway(),
    )
    assert isinstance(result, UnavailableAgentAlpha)


@pytest.mark.parametrize("token", ["TRUE", "yes", "on", "1", "false ", "garbage"])
def test_phase5_gate_invalid_token_fails_closed(
    monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    monkeypatch.setenv("AGENT_RUNTIME_ENABLED", token)
    with pytest.raises(EngineeringAlphaConfigurationError, match="feature_gate_invalid"):
        build_engineering_agent_alpha(**_valid_kwargs())


def test_status_never_overclaims_assembly_when_migration_is_not_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_ALPHA_ENGINEERING_ENABLED", "true")
    for name in ("AGENT_RUNTIME_ENABLED", "AGENT_PLANNER_ENABLED", "MULTI_AGENT_ENABLED"):
        monkeypatch.setenv(name, "false")
    posture = engineering_alpha_status(
        settings=_settings("development"),
        session_factory=_FakeFactory(head="0010"),
        gateway=_gateway(),
    )
    assert posture["assembled"] is False


def test_status_reports_assembled_only_with_the_authoritative_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_ALPHA_ENGINEERING_ENABLED", "true")
    for name in ("AGENT_RUNTIME_ENABLED", "AGENT_PLANNER_ENABLED", "MULTI_AGENT_ENABLED"):
        monkeypatch.setenv(name, "false")
    posture = engineering_alpha_status(
        settings=_settings("development"),
        session_factory=_FakeFactory(),
        gateway=_gateway(),
    )
    assert posture["assembled"] is True


def test_engineering_alpha_does_not_imply_other_gates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_ALPHA_ENGINEERING_ENABLED", "true")
    monkeypatch.delenv("AGENT_RUNTIME_ENABLED", raising=False)
    monkeypatch.delenv("AGENT_PLANNER_ENABLED", raising=False)
    monkeypatch.delenv("MULTI_AGENT_ENABLED", raising=False)
    posture = engineering_alpha_status(
        settings=_settings("development"),
        session_factory=_FakeFactory(),
        gateway=_gateway(),
    )
    assert posture["phase5_gates_all_false"] is True
    assert posture["engineering_flag_enabled"] is True


def test_engineering_alpha_flag_does_not_use_pydantic_coercion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A value that pydantic-style bool() coercion would accept must still fail.
    for token in ("1", "yes", "TRUE", " on"):
        monkeypatch.setenv("AGENT_ALPHA_ENGINEERING_ENABLED", token)
        with pytest.raises(EngineeringAlphaConfigurationError, match="flag_invalid"):
            resolve_engineering_alpha_flag(token)
