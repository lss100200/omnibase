"""Fail-closed gate and honest builder posture for the P5.4C Lite Agent.

The Lite gate is the *product* entry point for the engineering-only single-Agent
loop.  It is deliberately independent of the three production Phase 5 Feature
Gates and defaults to disabled.  Enabling it only authorizes the Lite product
surface in a development/engineering deployment; it never authorizes production
Agent Runtime, Planner, multi-Agent execution, arbitrary tools, or a new
migration.

When the gate is open the knowledge-search-capable path must run through the
formal reviewed P5.4B composition builder
``build_engineering_single_agent_executor`` (which installs
``LiveRuntimeAuthorityValidator`` and ``CapabilityGatewayKnowledgeSearchPort``);
the older P5.2C ``build_engineering_agent_alpha`` seam only carries the
tool-free RAG-retrieval flow.  This module never assembles either builder: it
only resolves the closed-set gate and reports a read-only, non-authorizing
posture so the Browser API and the UI can label state honestly.  Actual
assembly happens in ``agent_alpha.engineering``/``agent_executor.engineering``
and remains fail-closed whenever any dependency is missing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from omnibase.agent_executor.engineering import (
    ENGINEERING_FLAG as P5_4B_ENGINEERING_FLAG,
)
from omnibase.agent_executor.engineering import (
    EXPECTED_MIGRATION_HEAD,
)

LITE_AGENT_ENGINEERING_FLAG = "AGENT_LITE_ENGINEERING_ENABLED"

# The formal P5.4B builder is the only knowledge-search-capable composition.
# The P5.2C Agent Alpha seam carries the tool-free RAG-retrieval product loop;
# it must not be presented as the knowledge-search authority path.
FORMAL_BUILDER_NAME = "build_engineering_single_agent_executor"
ALPHA_BUILDER_NAME = "build_engineering_agent_alpha"
SUPPORTED_INVOCATION_MODES = ("no_tool", "knowledge_search_read_only")

_PHASE5_GATE_ENV_NAMES = (
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
)


class LiteAgentConfigurationError(RuntimeError):
    """Raised when the Lite gate contains a value outside the closed set."""


def resolve_lite_agent_flag(raw: str | None = None) -> bool:
    """Resolve the exact ``true``/``false`` Lite gate.

    ``raw`` is required for tests so the closed-set parser never depends on an
    ambient host variable.  Callers that want the process environment must
    pass ``os.environ.get(LITE_AGENT_ENGINEERING_FLAG)`` explicitly; passing
    ``None`` is treated as "the variable is absent" and resolves to ``False``,
    which keeps the parser independent of the host environment.
    """
    value = raw
    if value is None or value == "" or value == "false":
        return False
    if value == "true":
        return True
    raise LiteAgentConfigurationError(
        "lite_agent_engineering_flag_invalid: expected exactly true or false"
    )


def _exact_phase5_gate(raw: str | None) -> bool:
    """Closed-set parse of one Phase 5 Feature Gate (default false)."""
    if raw is None or raw == "" or raw == "false":
        return False
    if raw == "true":
        return True
    raise LiteAgentConfigurationError("phase5_feature_gate_invalid: expected exactly true or false")


def _phase5_gates_false(env: Mapping[str, str]) -> tuple[dict[str, bool], bool]:
    """Read each Phase 5 gate from an explicit environment mapping."""
    gates = {name: _exact_phase5_gate(env.get(name)) for name in _PHASE5_GATE_ENV_NAMES}
    return gates, all(value is False for value in gates.values())


def lite_agent_posture(
    *,
    raw: str | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """Return a read-only, non-authorizing product posture.

    ``env`` defaults to the live process environment for the status endpoint,
    but tests pass an explicit mapping so the posture is reproducible and
    independent of the ambient host.  The posture never authorizes anything:
    it only describes what a UI should label.  Assembly decisions stay in the
    fail-closed builders.
    """
    enabled = resolve_lite_agent_flag(raw)
    environment = env if env is not None else os.environ
    _, gates_false = _phase5_gates_false(environment)
    formal_flag = _exact_phase5_gate(environment.get(P5_4B_ENGINEERING_FLAG))
    return {
        "lite_gate_enabled": enabled,
        "production_runtime_enabled": False,
        "planner_enabled": False,
        "multi_agent_enabled": False,
        "tools_enabled": False,
        "knowledge_search_read_only_enabled": enabled and formal_flag and gates_false,
        "formal_builder": FORMAL_BUILDER_NAME,
        "alpha_builder": ALPHA_BUILDER_NAME,
        "supported_invocation_modes": SUPPORTED_INVOCATION_MODES,
        "formal_builder_flag_enabled": formal_flag,
        "phase5_gates_all_false": gates_false,
        "expected_migration_head": EXPECTED_MIGRATION_HEAD,
        "formal_builder_migration_head": EXPECTED_MIGRATION_HEAD,
    }


__all__ = [
    "ALPHA_BUILDER_NAME",
    "FORMAL_BUILDER_NAME",
    "LITE_AGENT_ENGINEERING_FLAG",
    "SUPPORTED_INVOCATION_MODES",
    "LiteAgentConfigurationError",
    "lite_agent_posture",
    "resolve_lite_agent_flag",
]
