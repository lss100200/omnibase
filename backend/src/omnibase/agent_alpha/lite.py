"""Fail-closed gate and honest builder posture for the P5.4C Lite Agent.

The Lite gate is the *product* entry point for the engineering-only single-Agent
loop.  It is deliberately independent of the three production Phase 5 Feature
Gates and defaults to disabled.  Enabling it only authorizes the Lite product
surface in a development/engineering deployment; it never authorizes production
Agent Runtime, Planner, multi-Agent execution, arbitrary tools, or a new
migration.

The only supported invocation mode of the Lite product loop is ``no_tool``,
carried by the P5.2C seam ``build_engineering_agent_alpha``.  The formal
P5.4B builder ``build_engineering_single_agent_executor`` (which installs
``LiveRuntimeAuthorityValidator`` and ``CapabilityGatewayKnowledgeSearchPort``)
is formally connected to this product loop: its engineering composition is
proven through a formal integration fixture that exercises the real persisted
authority chain (AgentVersion, AgentTask, AgentRun, WorkspaceRun, RunLease,
WorkspaceNode, NodeAttestation, server-owned WorkloadCredential with bound
workload identity digest) and resolves AgentRun to WorkspaceRun via
``AgentRunModel.workspace_run_id``.  The proof is **engineering-only**:
``engineering_composition_ready`` is ``True`` while ``activation_allowed`` and
``production_runtime_activated`` remain ``False``.  This module never assembles
either builder: it only resolves the closed-set gate and reports a read-only,
non-authorizing posture so the Browser API and the UI can label state honestly.
Actual assembly happens in ``agent_alpha.engineering``/
``agent_executor.engineering`` and remains fail-closed whenever any dependency
is missing.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from omnibase.agent_executor.engineering import EXPECTED_MIGRATION_HEAD

LITE_AGENT_ENGINEERING_FLAG = "AGENT_LITE_ENGINEERING_ENABLED"

# The formal P5.4B builder is formally connected to the P5.4C Lite product
# loop: its engineering composition is proven through a formal integration
# fixture that exercises the real persisted authority chain.  The proof is
# engineering-only — it never authorizes production activation.
FORMAL_BUILDER_NAME = "build_engineering_single_agent_executor"
ALPHA_BUILDER_NAME = "build_engineering_agent_alpha"
SUPPORTED_INVOCATION_MODES = ("no_tool",)
FORMAL_BUILDER_INTEGRATION = "proven_engineering_only"

_PHASE5_GATE_ENV_NAMES = (
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
)


class LiteAgentConfigurationError(RuntimeError):
    """Raised when the Lite gate contains a value outside the closed set."""


def resolve_lite_agent_flag(raw: str | None = None) -> bool:
    """Resolve the exact ``true``/``false`` Lite gate from an explicit input.

    This is the pure closed-set parser: it never reads ``os.environ`` itself.
    ``None`` and ``""`` mean "the variable is absent" and resolve to ``False``;
    any token other than exactly ``true`` or ``false`` raises
    :class:`LiteAgentConfigurationError`.  Callers that want the process
    environment must use :func:`runtime_lite_agent_enabled`.
    """
    value = raw
    if value is None or value == "" or value == "false":
        return False
    if value == "true":
        return True
    raise LiteAgentConfigurationError(
        "lite_agent_engineering_flag_invalid: expected exactly true or false"
    )


def runtime_lite_agent_enabled() -> bool:
    """Runtime resolver: read ``AGENT_LITE_ENGINEERING_ENABLED`` from the live
    process environment and pass the result into the closed-set parser.

    This is the only place the Lite gate reads ``os.environ``; the Browser
    dependency and the live posture must go through it so that setting the flag
    actually enables the route, while the pure parser stays host-independent.
    """
    return resolve_lite_agent_flag(os.environ.get(LITE_AGENT_ENGINEERING_FLAG))


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

    With an explicit ``env`` mapping the posture is reproducible and
    independent of the ambient host: the Lite flag is read from that mapping
    and the Phase 5 gates are parsed from it too.  With ``env=None`` the live
    process environment is used: the Lite flag goes through
    :func:`runtime_lite_agent_enabled` (the only place the gate reads
    ``os.environ``) and the Phase 5 gates are parsed from ``os.environ``.
    An explicit ``raw`` always wins over the environment.  The posture never
    authorizes anything: it only describes what a UI should label.  Assembly
    decisions stay in the fail-closed builders.
    """
    if raw is not None:
        environment = env if env is not None else os.environ
        enabled = resolve_lite_agent_flag(raw)
    elif env is not None:
        environment = env
        enabled = resolve_lite_agent_flag(environment.get(LITE_AGENT_ENGINEERING_FLAG))
    else:
        environment = os.environ
        enabled = runtime_lite_agent_enabled()
    _, gates_false = _phase5_gates_false(environment)
    return {
        "lite_gate_enabled": enabled,
        "production_runtime_enabled": False,
        "planner_enabled": False,
        "multi_agent_enabled": False,
        "tools_enabled": False,
        "formal_builder": FORMAL_BUILDER_NAME,
        "alpha_builder": ALPHA_BUILDER_NAME,
        "supported_invocation_modes": SUPPORTED_INVOCATION_MODES,
        "formal_builder_integration": FORMAL_BUILDER_INTEGRATION,
        "engineering_composition_ready": True,
        "activation_allowed": False,
        "phase5_gates_all_false": gates_false,
        "expected_migration_head": EXPECTED_MIGRATION_HEAD,
    }


__all__ = [
    "ALPHA_BUILDER_NAME",
    "FORMAL_BUILDER_INTEGRATION",
    "FORMAL_BUILDER_NAME",
    "LITE_AGENT_ENGINEERING_FLAG",
    "SUPPORTED_INVOCATION_MODES",
    "LiteAgentConfigurationError",
    "lite_agent_posture",
    "resolve_lite_agent_flag",
    "runtime_lite_agent_enabled",
]
