"""P5.4C Lite product gate tests.

The pure parser ``resolve_lite_agent_flag(raw)`` must be independent of the
ambient host environment and take an explicit ``raw`` argument.  The runtime
resolver ``runtime_lite_agent_enabled()`` is the *only* place the gate reads
``os.environ``; the Browser dependency and the live posture go through it so
that setting ``AGENT_LITE_ENGINEERING_ENABLED=true`` genuinely enables the
route.  The API-level tests prove that the flag reaches the assembled Alpha
dependency instead of always returning the Lite-gate-disabled path.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from omnibase.agent_alpha.lite import (
    ALPHA_BUILDER_NAME,
    FORMAL_BUILDER_INTEGRATION,
    FORMAL_BUILDER_NAME,
    LITE_AGENT_ENGINEERING_FLAG,
    LiteAgentConfigurationError,
    lite_agent_posture,
    resolve_lite_agent_flag,
    runtime_lite_agent_enabled,
)
from omnibase.agent_alpha.router import router
from omnibase.tenants.dependencies import get_current_tenant

_LITE_VARS = (
    LITE_AGENT_ENGINEERING_FLAG,
    "P5_4B_ENGINEERING_ENABLED",
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every Lite/Phase-5 variable so tests are host-independent."""
    for name in _LITE_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Pure closed-set parser (explicit input, never reads the environment)
# ---------------------------------------------------------------------------


def test_lite_flag_defaults_off_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    # ``raw=None`` means "the variable is absent" -> default-off, and the pure
    # parser must not depend on the ambient host even when a stray variable is
    # present.
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "true")
    assert resolve_lite_agent_flag(None) is False


def test_lite_flag_explicit_false_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert resolve_lite_agent_flag("false") is False
    assert resolve_lite_agent_flag("") is False


def test_lite_flag_explicit_true_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert resolve_lite_agent_flag("true") is True


@pytest.mark.parametrize(
    "value",
    ["TRUE", "True", "1", "yes", "enabled", "false ", " true", "on", "0", "off"],
)
def test_lite_flag_rejects_non_exact_tokens(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    _clear_env(monkeypatch)
    with pytest.raises(LiteAgentConfigurationError, match="flag_invalid"):
        resolve_lite_agent_flag(value)


def test_lite_flag_is_independent_of_ambient_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """A host with the Lite gate enabled cannot change the parser result."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "true")
    # Explicit ``raw`` always wins over the ambient variable.
    assert resolve_lite_agent_flag("false") is False
    assert resolve_lite_agent_flag("true") is True


# ---------------------------------------------------------------------------
# Runtime resolver: the only os.environ read, proven with monkeypatch
# ---------------------------------------------------------------------------


def test_runtime_resolver_absent_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    assert runtime_lite_agent_enabled() is False


def test_runtime_resolver_false_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "false")
    assert runtime_lite_agent_enabled() is False


def test_runtime_resolver_true_is_on(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "true")
    assert runtime_lite_agent_enabled() is True


def test_runtime_resolver_invalid_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "1")
    with pytest.raises(LiteAgentConfigurationError, match="flag_invalid"):
        runtime_lite_agent_enabled()


def test_runtime_resolver_reads_the_patched_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The runtime resolver must observe the explicitly patched environment."""
    _clear_env(monkeypatch)
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "true")
    assert runtime_lite_agent_enabled() is True
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "false")
    assert runtime_lite_agent_enabled() is False
    monkeypatch.delenv(LITE_AGENT_ENGINEERING_FLAG)
    assert runtime_lite_agent_enabled() is False


# ---------------------------------------------------------------------------
# Posture: honest single-mode disclosure, live path uses the runtime resolver
# ---------------------------------------------------------------------------


def test_lite_posture_defaults_off_and_never_authorizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    posture = lite_agent_posture(env={})
    assert posture["lite_gate_enabled"] is False
    assert posture["production_runtime_enabled"] is False
    assert posture["planner_enabled"] is False
    assert posture["multi_agent_enabled"] is False
    assert posture["tools_enabled"] is False
    assert posture["formal_builder"] == FORMAL_BUILDER_NAME
    assert posture["alpha_builder"] == ALPHA_BUILDER_NAME
    assert tuple(posture["supported_invocation_modes"]) == ("no_tool",)
    assert posture["formal_builder_integration"] == FORMAL_BUILDER_INTEGRATION
    assert posture["phase5_gates_all_false"] is True


def test_lite_posture_true_does_not_enable_production_features(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    posture = lite_agent_posture(raw="true")
    assert posture["lite_gate_enabled"] is True
    assert posture["production_runtime_enabled"] is False
    assert posture["planner_enabled"] is False
    assert posture["multi_agent_enabled"] is False
    assert posture["tools_enabled"] is False


def test_lite_posture_never_claims_formal_builder_integration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    # The formal P5.4B builder is disclosed by name but is never integrated
    # into the Lite product loop; no mode is claimed merely because a builder
    # name is displayed.
    for raw in (None, "true", "false"):
        posture = lite_agent_posture(raw=raw, env={"P5_4B_ENGINEERING_ENABLED": "true"})
        assert tuple(posture["supported_invocation_modes"]) == ("no_tool",)
        assert posture["formal_builder_integration"] == "not_integrated"
        assert "knowledge_search_read_only" not in posture["supported_invocation_modes"]


def test_lite_posture_rejects_invalid_phase5_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    with pytest.raises(LiteAgentConfigurationError, match="phase5_feature_gate_invalid"):
        lite_agent_posture(raw="true", env={"AGENT_RUNTIME_ENABLED": "yes"})


def test_lite_posture_live_path_uses_runtime_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-argument live posture must read the patched process environment."""
    _clear_env(monkeypatch)
    assert lite_agent_posture()["lite_gate_enabled"] is False
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "true")
    assert lite_agent_posture()["lite_gate_enabled"] is True
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "false")
    assert lite_agent_posture()["lite_gate_enabled"] is False
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "1")
    with pytest.raises(LiteAgentConfigurationError, match="flag_invalid"):
        lite_agent_posture()


def test_lite_posture_independent_of_ambient_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    # Stray ambient variables must not leak into the explicit-env posture.
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "true")
    monkeypatch.setenv("P5_4B_ENGINEERING_ENABLED", "true")
    posture = lite_agent_posture(raw="false", env={})
    assert posture["lite_gate_enabled"] is False
    assert posture["phase5_gates_all_false"] is True


# ---------------------------------------------------------------------------
# API level: AGENT_LITE_ENGINEERING_ENABLED=true must reach the assembled
# Alpha dependency, instead of always returning the Lite-gate-disabled path.
# ---------------------------------------------------------------------------


class _StubAlphaService:
    """Minimal stand-in proving the router reached the assembled seam."""

    def __init__(self) -> None:
        self.invoke_calls = 0
        self.profile_calls = 0

    def list_profiles(self, **_: object) -> tuple[object, ...]:
        self.profile_calls += 1
        return ()

    def invoke(self, **_: object):
        self.invoke_calls += 1
        yield SimpleNamespace(
            kind="done",
            payload={"invocation_id": "i", "answer": "assembled"},
        )

    def cancel(self, **_: object) -> bool:
        return False


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_current_tenant] = lambda: SimpleNamespace(
        tenant_id="tenant",
        schema_name="tenant_schema",
        user_id="user",
    )
    return app


def test_api_flag_absent_returns_lite_gate_disabled_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    app = _make_app()
    called: list[bool] = []

    def _never(**_: object):
        called.append(True)
        raise AssertionError("builder must not run when the Lite gate is closed")

    monkeypatch.setattr("omnibase.agent_alpha.router.build_engineering_agent_alpha", _never)
    client = TestClient(app)
    response = client.post(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000020/agent-alpha/invoke",
        headers={"Idempotency-Key": "test-key"},
        json={
            "agent_version_id": "00000000-0000-0000-0000-000000000002",
            "message": "hello",
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "agent_alpha_unavailable"
    assert called == []


def test_api_flag_false_returns_lite_gate_disabled_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "false")
    app = _make_app()
    monkeypatch.setattr(
        "omnibase.agent_alpha.router.build_engineering_agent_alpha",
        lambda **_: (_ for _ in ()).throw(AssertionError("must not run")),
    )
    client = TestClient(app)
    response = client.post(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000020/agent-alpha/invoke",
        headers={"Idempotency-Key": "test-key"},
        json={
            "agent_version_id": "00000000-0000-0000-0000-000000000002",
            "message": "hello",
        },
    )
    assert response.status_code == 503
    assert response.json()["detail"]["error"]["code"] == "agent_alpha_unavailable"


def test_api_flag_true_reaches_the_assembled_alpha_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "true")
    app = _make_app()
    stub = _StubAlphaService()
    monkeypatch.setattr("omnibase.agent_alpha.router.build_engineering_agent_alpha", lambda: stub)
    client = TestClient(app)
    response = client.post(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000020/agent-alpha/invoke",
        headers={"Idempotency-Key": "test-key"},
        json={
            "agent_version_id": "00000000-0000-0000-0000-000000000002",
            "message": "hello",
        },
    )
    assert response.status_code == 200
    assert "event: done" in response.text
    assert stub.invoke_calls == 1

    # The assembled dependency is reached by the profile route as well.
    profiles = client.get(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000020/agent-alpha/profiles"
    )
    assert profiles.status_code == 200
    assert stub.profile_calls == 1


def test_api_flag_invalid_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "1")
    app = _make_app()
    called: list[bool] = []
    monkeypatch.setattr(
        "omnibase.agent_alpha.router.build_engineering_agent_alpha",
        lambda **_: called.append(True) or SimpleNamespace(),
    )
    client = TestClient(app)
    with pytest.raises(LiteAgentConfigurationError, match="flag_invalid"):
        client.post(
            "/api/v1/workspaces/00000000-0000-0000-0000-000000000020/agent-alpha/invoke",
            headers={"Idempotency-Key": "test-key"},
            json={
                "agent_version_id": "00000000-0000-0000-0000-000000000002",
                "message": "hello",
            },
        )
    assert called == []


def test_api_status_live_posture_reflects_runtime_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setattr(
        "omnibase.agent_alpha.router.engineering_alpha_status",
        lambda **_: {
            "assembled": False,
            "engineering_flag_enabled": False,
            "environment_allowed": False,
            "phase5_gates_all_false": True,
        },
    )
    app = _make_app()
    client = TestClient(app)
    absent = client.get(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000020/agent-alpha/status"
    )
    assert absent.status_code == 200
    assert absent.json()["lite_gate_enabled"] is False
    assert absent.json()["supported_invocation_modes"] == ["no_tool"]
    assert absent.json()["formal_builder_integration"] == "not_integrated"

    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "true")
    enabled = client.get(
        "/api/v1/workspaces/00000000-0000-0000-0000-000000000020/agent-alpha/status"
    )
    assert enabled.status_code == 200
    assert enabled.json()["lite_gate_enabled"] is True
    assert enabled.json()["supported_invocation_modes"] == ["no_tool"]
    assert enabled.json()["formal_builder_integration"] == "not_integrated"
    assert "knowledge_search_read_only" not in enabled.json()["supported_invocation_modes"]
