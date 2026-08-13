"""Closed internal contracts for the P5 Fast Track Model Gateway."""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

from omnibase.model_gateway.adaptation import ReasoningGear

ModelRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class ModelMessage:
    """Tool-free chat message accepted by the engineering gateway."""

    role: ModelRole
    content: str

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("model_message_content_empty")


@dataclass(frozen=True, slots=True)
class ModelUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    cache_miss_input_tokens: int = 0

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.reasoning_tokens,
            self.cached_input_tokens,
            self.cache_miss_input_tokens,
        )
        if any(value < 0 for value in values):
            raise ValueError("model_usage_negative")
        if self.total_tokens < self.input_tokens + self.output_tokens:
            raise ValueError("model_usage_total_inconsistent")
        if self.cached_input_tokens + self.cache_miss_input_tokens > self.input_tokens:
            raise ValueError("model_usage_cache_tokens_inconsistent")


@dataclass(frozen=True, slots=True)
class ModelRequest:
    provider_id: str
    model_id: str
    messages: tuple[ModelMessage, ...]
    max_output_tokens: int
    temperature: float = 0.2
    timeout_seconds: float = 60.0
    reasoning_gear: ReasoningGear = "standard"

    def __post_init__(self) -> None:
        if not self.provider_id or not self.model_id:
            raise ValueError("model_identity_required")
        if not self.messages:
            raise ValueError("model_messages_required")
        if self.max_output_tokens < 1:
            raise ValueError("model_max_output_invalid")
        if not 0 <= self.temperature <= 2:
            raise ValueError("model_temperature_invalid")
        if self.timeout_seconds <= 0:
            raise ValueError("model_timeout_invalid")
        if self.reasoning_gear not in {"economy", "standard", "deep", "audit"}:
            raise ValueError("model_reasoning_gear_invalid")


@dataclass(frozen=True, slots=True)
class ModelResponse:
    provider_id: str
    requested_model_id: str
    actual_model_id: str
    content: str
    finish_reason: str
    usage: ModelUsage
    latency_ms: float


@dataclass(frozen=True, slots=True)
class ModelStreamChunk:
    provider_id: str
    requested_model_id: str
    actual_model_id: str
    content: str
    finish_reason: str | None = None
    usage: ModelUsage | None = None


class ModelProvider(Protocol):
    """Provider adapter boundary; API keys remain inside the adapter."""

    @property
    def provider_id(self) -> str: ...

    def complete(self, request: ModelRequest) -> ModelResponse: ...

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]: ...


def messages_from_pairs(
    messages: Sequence[tuple[ModelRole, str]],
) -> tuple[ModelMessage, ...]:
    return tuple(ModelMessage(role=role, content=content) for role, content in messages)


__all__ = [
    "ModelMessage",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "ModelRole",
    "ModelStreamChunk",
    "ModelUsage",
    "messages_from_pairs",
]
