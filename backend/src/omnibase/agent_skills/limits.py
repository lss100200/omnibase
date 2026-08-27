"""Server-owned aggregate limits for personal instruction Skills."""

from __future__ import annotations

from collections.abc import Iterable

MAX_LIVE_SKILL_INSTALLATIONS = 8
MAX_SKILL_INSTRUCTION_BYTES = 32_768


class SkillBundleLimitError(ValueError):
    """A prospective or resolved Skill bundle exceeded a fixed personal limit."""


def validate_skill_bundle_limits(instructions: Iterable[str]) -> tuple[int, int]:
    """Return ``(count, UTF-8 bytes)`` or fail closed on aggregate growth."""

    items = tuple(instructions)
    count = len(items)
    if count > MAX_LIVE_SKILL_INSTALLATIONS:
        raise SkillBundleLimitError("skill_bundle_live_limit_exceeded")
    total_bytes = sum(len(instruction.encode("utf-8")) for instruction in items)
    if total_bytes > MAX_SKILL_INSTRUCTION_BYTES:
        raise SkillBundleLimitError("skill_bundle_instruction_budget_exceeded")
    return count, total_bytes


__all__ = [
    "MAX_LIVE_SKILL_INSTALLATIONS",
    "MAX_SKILL_INSTRUCTION_BYTES",
    "SkillBundleLimitError",
    "validate_skill_bundle_limits",
]
