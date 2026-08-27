"""Budgeted, provider-neutral internal Model Gateway."""

from __future__ import annotations

from collections.abc import Iterator
from threading import BoundedSemaphore

from omnibase.core.config import get_settings
from omnibase.model_gateway.adaptation import ReasoningGear, plan_model_adaptation
from omnibase.model_gateway.contracts import (
    ModelMessage,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
)
from omnibase.model_gateway.providers import OpenAICompatibleProvider


class ModelGatewayError(RuntimeError):
    """Stable gateway failure without provider payloads or secrets."""


class ModelGatewayUnavailable(ModelGatewayError):
    """No explicitly configured provider is available."""


class ModelGatewayBudgetExceeded(ModelGatewayError):
    """A server-owned request ceiling was exceeded."""


class UnavailableModelGateway:
    """Fail-closed production default used when no provider is configured."""

    provider_id = "unavailable"
    model_id = "unavailable"

    def complete(self, messages: tuple[ModelMessage, ...], **_: object) -> ModelResponse:
        del messages
        raise ModelGatewayUnavailable("model_gateway_unavailable")

    def stream(self, messages: tuple[ModelMessage, ...], **_: object) -> Iterator[ModelStreamChunk]:
        del messages
        raise ModelGatewayUnavailable("model_gateway_unavailable")


class ModelGateway:
    """Closed tool-free gateway with identity, output and concurrency ceilings."""

    def __init__(
        self,
        *,
        provider: ModelProvider,
        model_id: str,
        max_output_tokens: int = 4096,
        max_input_characters: int = 200_000,
        max_concurrency: int = 4,
        timeout_seconds: float = 60.0,
    ) -> None:
        if max_output_tokens < 1 or max_input_characters < 1 or max_concurrency < 1:
            raise ValueError("model_gateway_limits_invalid")
        self._provider = provider
        self.provider_id = provider.provider_id
        self.model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._max_input_characters = max_input_characters
        self._timeout_seconds = timeout_seconds
        self._semaphore = BoundedSemaphore(max_concurrency)

    def _request(
        self,
        messages: tuple[ModelMessage, ...],
        *,
        max_output_tokens: int | None,
        temperature: float,
        reasoning_gear: ReasoningGear,
    ) -> ModelRequest:
        requested_output = max_output_tokens or self._max_output_tokens
        if requested_output > self._max_output_tokens:
            raise ModelGatewayBudgetExceeded("model_output_budget_exceeded")
        adaptation = plan_model_adaptation(self.model_id, reasoning_gear)
        if (
            len(adaptation.stable_prefix) + sum(len(message.content) for message in messages)
            > self._max_input_characters
        ):
            raise ModelGatewayBudgetExceeded("model_input_budget_exceeded")
        return ModelRequest(
            provider_id=self.provider_id,
            model_id=self.model_id,
            messages=messages,
            max_output_tokens=requested_output,
            temperature=temperature,
            timeout_seconds=self._timeout_seconds,
            reasoning_gear=reasoning_gear,
        )

    def complete(
        self,
        messages: tuple[ModelMessage, ...],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
        reasoning_gear: ReasoningGear = "standard",
    ) -> ModelResponse:
        request = self._request(
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            reasoning_gear=reasoning_gear,
        )
        with self._semaphore:
            return self._provider.complete(request)

    def stream(
        self,
        messages: tuple[ModelMessage, ...],
        *,
        max_output_tokens: int | None = None,
        temperature: float = 0.2,
        reasoning_gear: ReasoningGear = "standard",
    ) -> Iterator[ModelStreamChunk]:
        request = self._request(
            messages,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            reasoning_gear=reasoning_gear,
        )

        def _guarded() -> Iterator[ModelStreamChunk]:
            with self._semaphore:
                yield from self._provider.stream(request)

        return _guarded()


def configured_model_gateway() -> ModelGateway | UnavailableModelGateway:
    """Build the server-owned gateway without exposing its provider secret."""

    settings = get_settings()
    api_key = getattr(settings, "llm_api_key", "") or ""
    if not api_key:
        return UnavailableModelGateway()
    base_url = getattr(settings, "llm_api_base_url", "https://api.deepseek.com/v1")
    model_id = getattr(settings, "llm_model", "deepseek-chat")
    provider_id = getattr(settings, "llm_provider", "openai_compatible")
    provider = OpenAICompatibleProvider(
        provider_id=provider_id,
        api_key=api_key,
        base_url=base_url,
    )
    return ModelGateway(provider=provider, model_id=model_id)


__all__ = [
    "ModelGateway",
    "ModelGatewayBudgetExceeded",
    "ModelGatewayError",
    "ModelGatewayUnavailable",
    "UnavailableModelGateway",
    "configured_model_gateway",
]
