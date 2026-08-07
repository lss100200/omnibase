"""P5.4C Lite product gate tests.

The Lite gate parser must be independent of the ambient host environment:
``resolve_lite_agent_flag(None)`` is documented to mean "the variable is
absent", so the parser never calls ``os.environ.get`` itself.  The tests below
deliberately clear every Lite/Phase-5 variable with ``monkeypatch.delenv`` and
then prove the closed-set parser against an explicit ``raw`` argument plus an
explicit ``env`` mapping, so a host with
``AGENT_LITE_ENGINEERING_ENABLED=true`` cannot make the default-off assertion
fail.
"""

from __future__ import annotations

import pytest

from omnibase.agent_alpha.lite import (
    ALPHA_BUILDER_NAME,
    FORMAL_BUILDER_NAME,
    LITE_AGENT_ENGINEERING_FLAG,
    SUPPORTED_INVOCATION_MODES,
    LiteAgentConfigurationError,
    lite_agent_posture,
    resolve_lite_agent_flag,
)

_LITE_VARS = (
    LITE_AGENT_ENGINEERING_FLAG,
    "P5_4B_ENGINEERING_ENABLED",
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove every Lite/Phase-5 variable so the parser is host-independent."""
    for name in _LITE_VARS:
        monkeypatch.delenv(name, raising=False)


def test_lite_flag_defaults_off_when_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    # ``raw=None`` is documented as "variable absent" -> default-off, and must
    # not depend on the ambient host even when a stray variable is present.
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


def test_lite_posture_defaults_off_and_never_authorizes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    posture = lite_agent_posture(raw=None)
    assert posture["lite_gate_enabled"] is False
    assert posture["production_runtime_enabled"] is False
    assert posture["planner_enabled"] is False
    assert posture["multi_agent_enabled"] is False
    assert posture["tools_enabled"] is False
    assert posture["knowledge_search_read_only_enabled"] is False
    assert posture["formal_builder"] == FORMAL_BUILDER_NAME
    assert posture["alpha_builder"] == ALPHA_BUILDER_NAME
    assert tuple(posture["supported_invocation_modes"]) == SUPPORTED_INVOCATION_MODES
    assert posture["formal_builder_flag_enabled"] is False
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


def test_lite_posture_knowledge_search_requires_formal_builder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    # Lite gate on, but the formal P5.4B builder flag is off: the read-only
    # knowledge-search capability must stay closed.
    posture = lite_agent_posture(raw="true", env={})
    assert posture["knowledge_search_read_only_enabled"] is False
    assert posture["formal_builder_flag_enabled"] is False

    # Lite gate on AND the formal builder flag on AND all Phase 5 gates false:
    # the knowledge-search-capable path is admissible.  This still does not
    # authorize production Runtime, Planner, multi-Agent or arbitrary tools.
    posture = lite_agent_posture(
        raw="true",
        env={"P5_4B_ENGINEERING_ENABLED": "true"},
    )
    assert posture["knowledge_search_read_only_enabled"] is True
    assert posture["formal_builder_flag_enabled"] is True
    assert posture["production_runtime_enabled"] is False
    assert posture["tools_enabled"] is False


def test_lite_posture_rejects_invalid_phase5_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    with pytest.raises(LiteAgentConfigurationError, match="phase5_feature_gate_invalid"):
        lite_agent_posture(raw="true", env={"AGENT_RUNTIME_ENABLED": "yes"})


def test_lite_posture_rejects_invalid_formal_builder_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_env(monkeypatch)
    with pytest.raises(LiteAgentConfigurationError, match="phase5_feature_gate_invalid"):
        lite_agent_posture(raw="true", env={"P5_4B_ENGINEERING_ENABLED": "on"})


def test_lite_posture_independent_of_ambient_host(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    # Stray ambient variables must not leak into the explicit-env posture.
    monkeypatch.setenv(LITE_AGENT_ENGINEERING_FLAG, "true")
    monkeypatch.setenv("P5_4B_ENGINEERING_ENABLED", "true")
    posture = lite_agent_posture(raw="false", env={})
    assert posture["lite_gate_enabled"] is False
    assert posture["formal_builder_flag_enabled"] is False
    assert posture["knowledge_search_read_only_enabled"] is False
