"""Model-name-first request adaptation for the P6.1 personal gateway."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

ModelFamily = Literal["deepseek", "openai", "generic"]
ReasoningGear = Literal["economy", "standard", "deep", "audit"]


def detect_gateway_family(model_id: str) -> ModelFamily:
    value = re.sub(r"[_.:/\\\s]+", "-", unicodedata.normalize("NFKC", model_id).casefold())
    incompatible_claim = re.search(
        r"(?:^|-)(?:compatible|compat|proxy|bridge|emulator)(?:-|$)", value
    )
    if incompatible_claim is not None:
        return "generic"
    deepseek = re.fullmatch(r"deepseek(?:-[a-z0-9]+)+", value) is not None
    openai = re.fullmatch(r"(?:gpt(?:-[a-z0-9]+)+|o[134](?:-[a-z0-9]+)*)", value) is not None
    if deepseek == openai:
        return "generic"
    return "deepseek" if deepseek else "openai"


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
        "[OmniBase P6.1 model profile]\n"
        "Keep completed work distinct from proposals. Preserve security boundaries, "
        "report verification evidence, and stop when required authority is missing.\n"
        f"Reasoning gear: {gear}. {_GEAR_GUIDANCE[gear]}"
    )
    if family == "deepseek":
        effort = "low" if gear == "economy" else "medium" if gear == "standard" else "high"
        return ModelAdaptation(
            family=family,
            stable_prefix=(
                common
                + "\nReuse this stable instruction prefix across turns; put changing task data last "
                "to improve DeepSeek disk context-cache hits."
            ),
            extra_payload={
                "reasoning_effort": effort,
                "extra_body": {"thinking": {"type": "enabled"}},
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
    return ModelAdaptation(family="generic", stable_prefix=common, extra_payload={})


__all__ = [
    "ModelAdaptation",
    "ModelFamily",
    "ReasoningGear",
    "detect_gateway_family",
    "plan_model_adaptation",
]
