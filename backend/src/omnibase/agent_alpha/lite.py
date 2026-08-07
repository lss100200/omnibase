"""Fail-closed gate for the P5.4C Lite Agent product entry point.

This gate is deliberately independent from the three production Phase 5 gates
and defaults to disabled. Enabling it only authorizes the Lite product surface
in a development/engineering deployment; it never authorizes production Agent
Runtime, Planner, multi-Agent execution, tools, or a new migration.
"""

from __future__ import annotations

import os

LITE_AGENT_ENGINEERING_FLAG = "AGENT_LITE_ENGINEERING_ENABLED"


class LiteAgentConfigurationError(RuntimeError):
    """Raised when the Lite gate contains a value outside the closed set."""


def resolve_lite_agent_flag(raw: str | None = None) -> bool:
    """Resolve the exact ``true``/``false`` Lite gate, defaulting to false."""
    value = os.environ.get(LITE_AGENT_ENGINEERING_FLAG) if raw is None else raw
    if value is None or value == "" or value == "false":
        return False
    if value == "true":
        return True
    raise LiteAgentConfigurationError(
        "lite_agent_engineering_flag_invalid: expected exactly true or false"
    )


def lite_agent_posture(*, raw: str | None = None) -> dict[str, bool]:
    """Return a read-only, non-authorizing product posture."""
    enabled = resolve_lite_agent_flag(raw)
    return {
        "lite_gate_enabled": enabled,
        "production_runtime_enabled": False,
        "planner_enabled": False,
        "multi_agent_enabled": False,
        "tools_enabled": False,
    }


__all__ = [
    "LITE_AGENT_ENGINEERING_FLAG",
    "LiteAgentConfigurationError",
    "lite_agent_posture",
    "resolve_lite_agent_flag",
]
