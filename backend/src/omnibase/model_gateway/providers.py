"""Provider adapters for the internal, tool-free Model Gateway."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

from omnibase.model_gateway.adaptation import plan_model_adaptation
from omnibase.model_gateway.contracts import (
    ModelRequest,
    ModelResponse,
    ModelStreamChunk,
    ModelUsage,
)


class ModelProviderError(RuntimeError):
    """Sanitized provider failure safe for internal propagation."""


class ModelIdentityMismatch(ModelProviderError):
    """The provider silently returned a different model identity."""


def _usage_from_openai(value: Any) -> ModelUsage:
    if value is None:
        return ModelUsage(input_tokens=0, output_tokens=0, total_tokens=0)
    details = getattr(value, "completion_tokens_details", None)
    reasoning = int(getattr(details, "reasoning_tokens", 0) or 0)
    prompt_details = getattr(value, "prompt_tokens_details", None)
    cached = int(
        getattr(value, "prompt_cache_hit_tokens", 0)
        or getattr(prompt_details, "cached_tokens", 0)
        or 0
    )
    cache_miss = int(getattr(value, "prompt_cache_miss_tokens", 0) or 0)
    return ModelUsage(
        input_tokens=int(getattr(value, "prompt_tokens", 0) or 0),
        output_tokens=int(getattr(value, "completion_tokens", 0) or 0),
        total_tokens=int(getattr(value, "total_tokens", 0) or 0),
        reasoning_tokens=reasoning,
        cached_input_tokens=cached,
        cache_miss_input_tokens=cache_miss,
    )


class OpenAICompatibleProvider:
    """OpenAI-compatible adapter for DeepSeek, DashScope, Zhipu, OpenAI or Ollama."""

    def __init__(
        self,
        *,
        provider_id: str,
        api_key: str,
        base_url: str,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        if not provider_id or not base_url:
            raise ValueError("model_provider_configuration_invalid")
        if not api_key:
            raise ValueError("model_provider_secret_missing")
        self._provider_id = provider_id
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._client_factory = client_factory

    @property
    def provider_id(self) -> str:
        return self._provider_id

    def _client(self, timeout_seconds: float) -> Any:
        factory = self._client_factory
        if factory is None:
            from openai import OpenAI

            factory = OpenAI
        return factory(
            api_key=self._api_key,
            base_url=self._base_url,
            timeout=timeout_seconds,
        )

    def _payload(self, request: ModelRequest) -> dict[str, object]:
        if request.provider_id != self._provider_id:
            raise ModelProviderError("model_provider_identity_mismatch")
        adaptation = plan_model_adaptation(request.model_id, request.reasoning_gear)
        payload: dict[str, object] = {
            "model": request.model_id,
            "messages": [
                {"role": "system", "content": adaptation.stable_prefix},
                *[
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
            ],
        }
        if adaptation.family == "openai":
            payload["max_completion_tokens"] = request.max_output_tokens
        else:
            payload["max_tokens"] = request.max_output_tokens
        if adaptation.family == "generic":
            payload["temperature"] = request.temperature
        payload.update(adaptation.extra_payload)
        return payload

    @staticmethod
    def _verify_model(requested: str, actual: object) -> str:
        actual_text = str(actual or "")
        if not actual_text or actual_text != requested:
            raise ModelIdentityMismatch("model_actual_identity_mismatch")
        return actual_text

    def complete(self, request: ModelRequest) -> ModelResponse:
        started = time.monotonic()
        try:
            response = self._client(request.timeout_seconds).chat.completions.create(
                **self._payload(request)
            )
            actual = self._verify_model(request.model_id, getattr(response, "model", None))
            choice = response.choices[0]
            content = choice.message.content or ""
            return ModelResponse(
                provider_id=self._provider_id,
                requested_model_id=request.model_id,
                actual_model_id=actual,
                content=content,
                finish_reason=str(choice.finish_reason or "unknown"),
                usage=_usage_from_openai(getattr(response, "usage", None)),
                latency_ms=(time.monotonic() - started) * 1000,
            )
        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError("model_provider_request_failed") from exc

    def stream(self, request: ModelRequest) -> Iterator[ModelStreamChunk]:
        try:
            response = self._client(request.timeout_seconds).chat.completions.create(
                **self._payload(request), stream=True, stream_options={"include_usage": True}
            )
            observed_model: str | None = None
            for item in response:
                actual = self._verify_model(request.model_id, getattr(item, "model", None))
                if observed_model is not None and actual != observed_model:
                    raise ModelIdentityMismatch("model_stream_identity_drift")
                observed_model = actual
                choice = item.choices[0] if item.choices else None
                delta = "" if choice is None else (choice.delta.content or "")
                finish = None if choice is None else choice.finish_reason
                usage_value = getattr(item, "usage", None)
                yield ModelStreamChunk(
                    provider_id=self._provider_id,
                    requested_model_id=request.model_id,
                    actual_model_id=actual,
                    content=delta,
                    finish_reason=None if finish is None else str(finish),
                    usage=None if usage_value is None else _usage_from_openai(usage_value),
                )
            if observed_model is None:
                raise ModelProviderError("model_provider_empty_stream")
        except ModelProviderError:
            raise
        except Exception as exc:
            raise ModelProviderError("model_provider_stream_failed") from exc


__all__ = [
    "ModelIdentityMismatch",
    "ModelProviderError",
    "OpenAICompatibleProvider",
]
