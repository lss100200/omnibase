"""Model-name-first request adaptation for the P6.3 personal gateway."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

ModelFamily = Literal["deepseek", "glm", "kimi", "openai", "anthropic", "generic"]
ReasoningGear = Literal["economy", "standard", "deep", "audit"]

_INCOMPATIBLE_MODEL_CLAIM = re.compile(r"(?:^|-)(?:compatible|compat|proxy|bridge|emulator)(?:-|$)")
_FAMILY_CLAIMS: tuple[tuple[ModelFamily, frozenset[str]], ...] = (
    ("deepseek", frozenset({"deepseek"})),
    ("glm", frozenset({"zhipu", "bigmodel", "chatglm", "glm"})),
    ("kimi", frozenset({"moonshot", "kimi"})),
    ("openai", frozenset({"openai", "gpt", "o1", "o3", "o4"})),
    ("anthropic", frozenset({"anthropic", "claude"})),
)
_EXACT_MODEL_PATTERNS: tuple[tuple[ModelFamily, re.Pattern[str]], ...] = (
    ("deepseek", re.compile(r"^deepseek-(?:[a-z0-9]+(?:-[a-z0-9]+)*)$")),
    (
        "glm",
        re.compile(
            r"^(?:(?:zhipu|bigmodel|zai|z-ai|thudm|relay|openrouter)-)?"
            r"(?:glm|chatglm)-[0-9][a-z0-9]*(?:-[a-z0-9]+)*$"
        ),
    ),
    ("kimi", re.compile(r"^(?:kimi|moonshot)-(?:[a-z0-9]+(?:-[a-z0-9]+)*)$")),
    (
        "openai",
        re.compile(r"^(?:gpt-(?:[a-z0-9]+(?:-[a-z0-9]+)*)|o[134](?:-[a-z0-9]+)*)$"),
    ),
    (
        "anthropic",
        re.compile(
            r"^(?:(?:relay|openrouter)-)?claude-"
            r"(?:(?:fable|mythos|opus|sonnet|haiku|[0-9][a-z0-9]*)"
            r"(?:-[a-z0-9]+)*)$|^anthropic-(?:claude-)?"
            r"(?:fable|mythos|opus|sonnet|haiku)(?:-[a-z0-9]+)*$"
        ),
    ),
)


def detect_gateway_family(model_id: str) -> ModelFamily:
    value = re.sub(r"[_.:/\\\s]+", "-", unicodedata.normalize("NFKC", model_id).casefold())
    tokens = frozenset(part for part in value.split("-") if part)
    claims = {
        family for family, family_tokens in _FAMILY_CLAIMS if not tokens.isdisjoint(family_tokens)
    }
    if len(claims) != 1 or _INCOMPATIBLE_MODEL_CLAIM.search(value) is not None:
        return "generic"
    claimed_family = next(iter(claims))
    for family, pattern in _EXACT_MODEL_PATTERNS:
        if family == claimed_family and pattern.search(value) is not None:
            return family
    return "generic"


@dataclass(frozen=True, slots=True)
class ModelAdaptation:
    family: ModelFamily
    stable_prefix: str
    extra_payload: dict[str, object]


_GEAR_GUIDANCE: dict[ReasoningGear, str] = {
    "economy": "Choose the shortest safe path and answer directly.",
    "standard": "Check the main alternatives, then give a practical implementation answer.",
    "deep": "Analyze dependencies and failure modes before the conclusion; expose concise rationale, not private chain-of-thought.",
    "audit": "Audit claims against supplied evidence and mark blockers or unverified facts explicitly.",
}


def plan_model_adaptation(model_id: str, gear: ReasoningGear) -> ModelAdaptation:
    """Return a stable prefix and closed payload selected only by model name."""

    family = detect_gateway_family(model_id)
    common = (
        "[OmniBase P6.3 model profile]\n"
        "Keep completed work distinct from proposals. Preserve security boundaries, "
        "report verification evidence, and stop when required authority is missing.\n"
        f"Reasoning gear: {gear}. {_GEAR_GUIDANCE[gear]}"
    )
    if family == "deepseek":
        effort = "low" if gear == "economy" else "medium" if gear == "standard" else "high"
        thinking = "disabled" if gear == "economy" else "enabled"
        return ModelAdaptation(
            family=family,
            stable_prefix=(
                common
                + "\nReuse this stable instruction prefix across turns; put changing task data last "
                "to improve DeepSeek disk context-cache hits."
            ),
            extra_payload={
                "reasoning_effort": effort,
                "extra_body": {"thinking": {"type": thinking}},
            },
        )
    if family == "openai":
        effort = "low" if gear == "economy" else "medium" if gear == "standard" else "high"
        return ModelAdaptation(
            family=family,
            stable_prefix=(
                common + "\nLead with the outcome, retain useful constraints and success criteria, "
                "and keep this prefix stable for prompt-cache locality."
            ),
            # The provider currently uses Chat Completions. ``verbosity`` is a
            # Responses API control, so it must not cross this compatibility
            # boundary until a dedicated endpoint is implemented and tested.
            extra_payload={"reasoning_effort": effort},
        )
    if family == "glm":
        return ModelAdaptation(
            family=family,
            stable_prefix=(
                common + "\nUse explicit structure, preserve identifiers exactly, and keep stable "
                "instructions before changing task data for context locality. Treat reasoning, "
                "cache hits, tools and GLM-specific controls as unverified on this Chat "
                "Completions transport."
            ),
            extra_payload={},
        )
    if family == "kimi":
        return ModelAdaptation(
            family=family,
            stable_prefix=(
                common
                + "\nPrioritize the supplied file and conversation context, cite its labels when "
                "making claims, and state when the context budget omitted material. Treat "
                "Moonshot/Kimi thinking, cache, schema and tool controls as unverified on this "
                "Chat Completions transport."
            ),
            extra_payload={},
        )
    if family == "anthropic":
        return ModelAdaptation(
            family=family,
            stable_prefix=(
                common
                + "\nSeparate observations, risks and actions; preserve supplied constraints "
                "verbatim and prefer narrow edits. This Chat Completions transport does not "
                "claim native Anthropic Messages thinking, prompt caching or output effort."
            ),
            extra_payload={},
        )
    return ModelAdaptation(family="generic", stable_prefix=common, extra_payload={})


__all__ = [
    "ModelAdaptation",
    "ModelFamily",
    "ReasoningGear",
    "detect_gateway_family",
    "plan_model_adaptation",
]
