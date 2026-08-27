"""Model-name-first family recognition for the desktop-local adapter.

This is a stdlib-only port of the Model Gateway name grammar. It does not
import PostgreSQL Settings, httpx, openai, or user_settings crypto.
Unrecognized names become ``generic-openai-compatible`` instead of a hard
reject. URL hostnames are an auxiliary hint only when the model name is
unrecognized.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit

DesktopModelFamily = Literal[
    "deepseek",
    "openai",
    "anthropic",
    "glm",
    "kimi",
    "generic-openai-compatible",
]
DesktopReasoningGear = Literal["economy", "standard", "deep", "audit"]
DesktopThinkingDepth = Literal["disabled", "low", "medium", "high"]

_INCOMPATIBLE_MODEL_CLAIM = re.compile(r"(?:^|-)(?:compatible|compat|proxy|bridge|emulator)(?:-|$)")
_FAMILY_CLAIMS: tuple[tuple[DesktopModelFamily, frozenset[str]], ...] = (
    ("deepseek", frozenset({"deepseek"})),
    ("glm", frozenset({"zhipu", "bigmodel", "chatglm", "glm"})),
    ("kimi", frozenset({"moonshot", "kimi"})),
    ("openai", frozenset({"openai", "gpt", "o1", "o3", "o4"})),
    ("anthropic", frozenset({"anthropic", "claude"})),
)
_EXACT_MODEL_PATTERNS: tuple[tuple[DesktopModelFamily, re.Pattern[str]], ...] = (
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
_URL_HINTS: tuple[tuple[DesktopModelFamily, tuple[str, ...]], ...] = (
    ("deepseek", ("deepseek.com", "deepseek.cn")),
    ("openai", ("openai.com", "api.openai.com")),
    ("anthropic", ("anthropic.com", "api.anthropic.com")),
    ("glm", ("bigmodel.cn", "open.bigmodel.cn", "zhipuai.cn")),
    ("kimi", ("moonshot.cn", "moonshot.ai")),
)
_GEAR_GUIDANCE: dict[DesktopReasoningGear, str] = {
    "economy": "Choose the shortest safe path and answer directly.",
    "standard": "Check the main alternatives, then give a practical implementation answer.",
    "deep": (
        "Analyze dependencies and failure modes before the conclusion; "
        "expose concise rationale, not private chain-of-thought."
    ),
    "audit": (
        "Audit claims against supplied evidence and mark blockers or unverified facts explicitly."
    ),
}
_GEAR_OUTPUT_TOKENS: dict[DesktopReasoningGear, int] = {
    "economy": 1_024,
    "standard": 2_048,
    "deep": 4_096,
    "audit": 4_096,
}
_GEAR_CONTEXT_CHARS: dict[DesktopReasoningGear, int] = {
    "economy": 6_000,
    "standard": 14_000,
    "deep": 22_000,
    "audit": 24_000,
}


def _normalize_model_id(model_id: str) -> str:
    return re.sub(
        r"[_.:/\\\s]+",
        "-",
        unicodedata.normalize("NFKC", model_id).casefold(),
    )


def detect_model_family(model_id: str) -> DesktopModelFamily:
    """Return a display/adaptation family without treating it as provider identity."""

    if not model_id.strip():
        return "generic-openai-compatible"
    value = _normalize_model_id(model_id)
    tokens = frozenset(part for part in value.split("-") if part)
    claims = {
        family for family, family_tokens in _FAMILY_CLAIMS if not tokens.isdisjoint(family_tokens)
    }
    if len(claims) != 1 or _INCOMPATIBLE_MODEL_CLAIM.search(value) is not None:
        return "generic-openai-compatible"
    claimed_family = next(iter(claims))
    for family, pattern in _EXACT_MODEL_PATTERNS:
        if family == claimed_family and pattern.search(value) is not None:
            return family
    return "generic-openai-compatible"


def detect_url_family_hint(base_url: str) -> DesktopModelFamily | None:
    try:
        hostname = (urlsplit(base_url.strip()).hostname or "").lower().rstrip(".")
    except ValueError:
        return None
    if not hostname:
        return None
    matches = {
        family
        for family, suffixes in _URL_HINTS
        if any(hostname == item or hostname.endswith(f".{item}") for item in suffixes)
    }
    if len(matches) != 1:
        return None
    return next(iter(matches))


def resolve_desktop_family(model_id: str, base_url: str) -> DesktopModelFamily:
    """Model name wins; URL is consulted only when the name is unrecognized."""

    named = detect_model_family(model_id)
    if named != "generic-openai-compatible":
        return named
    hinted = detect_url_family_hint(base_url)
    return hinted if hinted is not None else "generic-openai-compatible"


@dataclass(frozen=True, slots=True)
class DesktopModelAdaptation:
    family: DesktopModelFamily
    stable_prefix: str
    extra_payload: dict[str, object]
    max_output_tokens: int
    context_character_budget: int


def plan_desktop_adaptation(
    model_id: str,
    base_url: str,
    gear: DesktopReasoningGear,
    thinking_depth: DesktopThinkingDepth,
) -> DesktopModelAdaptation:
    family = resolve_desktop_family(model_id, base_url)
    common = (
        "[OmniBase P6.7 desktop profile]\n"
        "Keep completed work distinct from proposals. Preserve security boundaries, "
        "report verification evidence, and stop when required authority is missing. "
        "Tools, files, MCP, Skills and child Agents remain closed.\n"
        f"Reasoning gear: {gear}. {_GEAR_GUIDANCE[gear]}"
    )
    extra: dict[str, object] = {}
    if family == "deepseek":
        effort = "low" if gear == "economy" else "medium" if gear == "standard" else "high"
        thinking = "disabled" if thinking_depth == "disabled" else "enabled"
        extra = {
            "reasoning_effort": effort,
            "thinking": {"type": thinking},
        }
        prefix = (
            common
            + "\nReuse this stable instruction prefix across turns; put changing task data last."
        )
    elif family == "openai":
        effort = "low" if gear == "economy" else "medium" if gear == "standard" else "high"
        extra = {"reasoning_effort": effort}
        prefix = (
            common + "\nLead with the outcome, retain useful constraints and success criteria, "
            "and keep this prefix stable for prompt-cache locality."
        )
    elif family == "glm":
        prefix = (
            common
            + "\nUse explicit structure and preserve identifiers exactly. Treat GLM-specific "
            "controls as unverified on this Chat Completions transport."
        )
    elif family == "kimi":
        prefix = (
            common
            + "\nPrioritize the supplied conversation context and state when the budget omitted "
            "material. Treat Moonshot/Kimi thinking controls as unverified on this transport."
        )
    elif family == "anthropic":
        prefix = (
            common
            + "\nSeparate observations, risks and actions. This Chat Completions transport does "
            "not claim native Anthropic Messages thinking or prompt caching."
        )
    else:
        prefix = common
    return DesktopModelAdaptation(
        family=family,
        stable_prefix=prefix,
        extra_payload=extra,
        max_output_tokens=_GEAR_OUTPUT_TOKENS[gear],
        context_character_budget=_GEAR_CONTEXT_CHARS[gear],
    )
