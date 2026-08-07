"""P5.4C Lite product gate tests."""

from __future__ import annotations

import pytest

from omnibase.agent_alpha.lite import (
    LiteAgentConfigurationError,
    lite_agent_posture,
    resolve_lite_agent_flag,
)


def test_lite_gate_defaults_off() -> None:
    assert resolve_lite_agent_flag(None) is False
    posture = lite_agent_posture(raw=None)
    assert posture["lite_gate_enabled"] is False
    assert posture["production_runtime_enabled"] is False


@pytest.mark.parametrize("value", ["TRUE", "True", "1", "yes", "enabled", "false "])
def test_lite_gate_rejects_non_exact_tokens(value: str) -> None:
    with pytest.raises(LiteAgentConfigurationError, match="flag_invalid"):
        resolve_lite_agent_flag(value)


def test_lite_gate_true_does_not_enable_production_features() -> None:
    posture = lite_agent_posture(raw="true")
    assert posture == {
        "lite_gate_enabled": True,
        "production_runtime_enabled": False,
        "planner_enabled": False,
        "multi_agent_enabled": False,
        "tools_enabled": False,
    }
