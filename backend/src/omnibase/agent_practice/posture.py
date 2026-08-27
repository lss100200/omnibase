"""Strict production posture for the P6.4 personal practice window."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PracticePosture:
    enabled: bool
    production: bool
    runtime_enabled: bool
    planner_disabled: bool
    enterprise_multi_agent_disabled: bool
    mcp_disabled: bool
    personal_profile_selected: bool
    participant_count_allowed: bool
    activation_allowed: bool
    blockers: tuple[str, ...]


def _strict_bool(env: Mapping[str, object], name: str) -> bool | None:
    value = env.get(name)
    if value is True or value == "true":
        return True
    if value is False or value == "false" or value is None:
        return False
    return None


def personal_practice_posture(
    env: Mapping[str, object], *, participant_count: int
) -> PracticePosture:
    blockers: list[str] = []
    enabled = _strict_bool(env, "P6_4_PERSONAL_PRACTICE_ENABLED")
    runtime = _strict_bool(env, "AGENT_RUNTIME_ENABLED")
    planner = _strict_bool(env, "AGENT_PLANNER_ENABLED")
    multi = _strict_bool(env, "MULTI_AGENT_ENABLED")
    mcp = _strict_bool(env, "MCP_RUNTIME_ENABLED")
    for name, value in (
        ("P6_4_PERSONAL_PRACTICE_ENABLED", enabled),
        ("AGENT_RUNTIME_ENABLED", runtime),
        ("AGENT_PLANNER_ENABLED", planner),
        ("MULTI_AGENT_ENABLED", multi),
        ("MCP_RUNTIME_ENABLED", mcp),
    ):
        if value is None:
            blockers.append(f"{name} must be exact true or false")
    production = env.get("ENV") == "production"
    personal_profile = env.get("PERSONAL_RUNTIME_PROFILE") == "personal_single_owner"
    count_allowed = participant_count in {1, 3, 4, 5, 6}
    if enabled is not True:
        blockers.append("personal practice gate is disabled")
    if not production:
        blockers.append("personal practice requires production environment")
    if runtime is not True:
        blockers.append("personal practice requires Agent Runtime")
    if planner is not False:
        blockers.append("personal practice requires Planner disabled")
    if multi is not False:
        blockers.append("personal practice requires enterprise Multi-Agent disabled")
    if mcp is not False:
        blockers.append("personal practice requires MCP disabled")
    if not personal_profile:
        blockers.append("personal practice requires personal_single_owner profile")
    if not count_allowed:
        blockers.append("personal practice participant count is not allowed")
    return PracticePosture(
        enabled=enabled is True,
        production=production,
        runtime_enabled=runtime is True,
        planner_disabled=planner is False,
        enterprise_multi_agent_disabled=multi is False,
        mcp_disabled=mcp is False,
        personal_profile_selected=personal_profile,
        participant_count_allowed=count_allowed,
        activation_allowed=not blockers,
        blockers=tuple(blockers),
    )


__all__ = ["PracticePosture", "personal_practice_posture"]
