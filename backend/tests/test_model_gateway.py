"""Focused engineering tests for the tool-free internal Model Gateway."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from omnibase.model_gateway import ModelGateway, ModelMessage
from omnibase.model_gateway.adaptation import detect_gateway_family
from omnibase.model_gateway.providers import (
    ModelIdentityMismatch,
    OpenAICompatibleProvider,
)
from omnibase.model_gateway.service import (
    ModelGatewayBudgetExceeded,
    ModelGatewayUnavailable,
    UnavailableModelGateway,
)
from omnibase.rag.llm import generate_answer


class _Completions:
    def __init__(self, response: object) -> None:
        self.response = response
        self.calls: list[dict[str, object]] = []

    def create(self, **payload: object) -> object:
        self.calls.append(payload)
        return self.response


class _Client:
    def __init__(self, response: object) -> None:
        self.chat = SimpleNamespace(completions=_Completions(response))


def _factory(
    response: object, *, expected_base_url: str = "https://provider.example/v1"
) -> tuple[Any, _Client]:
    client = _Client(response)

    def create_client(**kwargs: object) -> _Client:
        assert kwargs["api_key"] == "server-secret"
        assert kwargs["base_url"] == expected_base_url
        return client

    return create_client, client


def _response(*, model: str = "model-alpha", content: str = "answer") -> object:
    usage = SimpleNamespace(
        prompt_tokens=7,
        completion_tokens=3,
        total_tokens=10,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
    )
    choice = SimpleNamespace(
        message=SimpleNamespace(content=content),
        finish_reason="stop",
    )
    return SimpleNamespace(model=model, choices=[choice], usage=usage)


def _provider_for(
    model_id: str,
    *,
    provider_id: str = "relay",
    base_url: str = "https://provider.example/v1",
) -> tuple[ModelGateway, _Client]:
    client_factory, client = _factory(_response(model=model_id), expected_base_url=base_url)
    return (
        ModelGateway(
            provider=OpenAICompatibleProvider(
                provider_id=provider_id,
                api_key="server-secret",
                base_url=base_url,
                client_factory=client_factory,
            ),
            model_id=model_id,
        ),
        client,
    )


def test_complete_records_exact_identity_and_never_sends_tools() -> None:
    client_factory, client = _factory(_response())
    provider = OpenAICompatibleProvider(
        provider_id="test-provider",
        api_key="server-secret",
        base_url="https://provider.example/v1",
        client_factory=client_factory,
    )
    gateway = ModelGateway(provider=provider, model_id="model-alpha")

    result = gateway.complete((ModelMessage(role="user", content="hello"),))

    assert result.requested_model_id == result.actual_model_id == "model-alpha"
    assert result.provider_id == "test-provider"
    assert result.usage.total_tokens == 10
    payload = client.chat.completions.calls[0]
    assert payload["model"] == "model-alpha"
    assert "tools" not in payload
    assert "tool_choice" not in payload


def test_silent_model_fallback_is_rejected() -> None:
    client_factory, _ = _factory(_response(model="fallback-model"))
    gateway = ModelGateway(
        provider=OpenAICompatibleProvider(
            provider_id="test-provider",
            api_key="server-secret",
            base_url="https://provider.example/v1",
            client_factory=client_factory,
        ),
        model_id="model-alpha",
    )

    with pytest.raises(ModelIdentityMismatch, match="model_actual_identity_mismatch"):
        gateway.complete((ModelMessage(role="user", content="hello"),))


def test_deepseek_model_name_enables_stable_prefix_and_thinking_on_any_relay() -> None:
    gateway, client = _provider_for("deepseek-v4-pro")
    gateway.complete(
        (ModelMessage(role="user", content="changing task tail"),),
        reasoning_gear="deep",
    )

    payload = client.chat.completions.calls[0]
    assert payload["reasoning_effort"] == "high"
    assert payload["extra_body"] == {"thinking": {"type": "enabled"}}
    assert "temperature" not in payload
    assert payload["max_tokens"] == 4096
    messages = payload["messages"]
    assert isinstance(messages, list)
    assert "DeepSeek disk context-cache hits" in str(messages[0])
    assert messages[-1] == {"role": "user", "content": "changing task tail"}


def test_deepseek_economy_disables_thinking_for_bounded_specialist_work() -> None:
    gateway, client = _provider_for("deepseek-v4-flash")
    gateway.complete(
        (ModelMessage(role="user", content="return one compact JSON object"),),
        reasoning_gear="economy",
    )

    payload = client.chat.completions.calls[0]
    assert payload["reasoning_effort"] == "low"
    assert payload["extra_body"] == {"thinking": {"type": "disabled"}}


def test_gpt_model_name_uses_chat_compatible_outcome_profile_reasoning() -> None:
    gateway, client = _provider_for("gpt-5.6-luna")
    gateway.complete((ModelMessage(role="user", content="ship it"),), reasoning_gear="economy")

    payload = client.chat.completions.calls[0]
    assert payload["reasoning_effort"] == "low"
    assert "verbosity" not in payload
    assert payload["max_completion_tokens"] == 4096
    assert "max_tokens" not in payload
    assert "temperature" not in payload
    assert "Lead with the outcome" in str(payload["messages"])


@pytest.mark.parametrize(
    ("model_id", "family"),
    [
        ("glm-5.2", "glm"),
        ("zhipu/glm-5.2", "glm"),
        ("relay/glm-4.7-flashx", "glm"),
        ("kimi-k2", "kimi"),
        ("moonshot-v1-128k", "kimi"),
        ("claude-opus-5", "anthropic"),
        ("anthropic/claude-sonnet-5", "anthropic"),
        ("anthropic/sonnet-5", "anthropic"),
        ("relay/claude-haiku-4-5", "anthropic"),
    ],
)
def test_conservative_exact_model_names_match_on_relays(model_id: str, family: str) -> None:
    assert detect_gateway_family(model_id) == family


@pytest.mark.parametrize(
    "model_id",
    [
        "glm",
        "chatglm",
        "zhipu",
        "kimi",
        "moonshot",
        "claude",
        "anthropic",
        "sonnet-5",
        "proxy/claude-opus-5",
        "proxy/kimi-k2",
        "glm-5.2-claude-sonnet-5",
        "kimi-k2-gpt-5",
        "claude-gpt-bridge",
        "custom-model",
    ],
)
def test_bare_conflicting_proxy_or_unknown_names_stay_generic(model_id: str) -> None:
    assert detect_gateway_family(model_id) == "generic"


@pytest.mark.parametrize(
    ("model_id", "expected_text"),
    [
        ("relay/glm-5.2", "GLM-specific controls as unverified"),
        ("kimi-k2", "Treat Moonshot/Kimi thinking"),
        ("moonshot-v1-128k", "Treat Moonshot/Kimi thinking"),
        ("relay/claude-opus-5", "does not claim native Anthropic Messages"),
    ],
)
def test_conservative_chat_profiles_send_prompt_guidance_only(
    model_id: str, expected_text: str
) -> None:
    gateway, client = _provider_for(model_id)
    gateway.complete((ModelMessage(role="user", content="ship safely"),), reasoning_gear="audit")

    payload = client.chat.completions.calls[0]
    assert payload["max_tokens"] == 4096
    assert expected_text in str(payload["messages"])
    assert "reasoning_effort" not in payload
    assert "extra_body" not in payload
    assert "cache_control" not in payload
    assert "output_config" not in payload
    assert "tools" not in payload
    assert "tool_choice" not in payload


def test_unknown_model_is_not_promoted_by_branded_provider_or_base_url() -> None:
    gateway, client = _provider_for(
        "unknown-model",
        provider_id="moonshot",
        base_url="https://api.moonshot.cn/v1",
    )
    gateway.complete((ModelMessage(role="user", content="hello"),), reasoning_gear="audit")

    payload = client.chat.completions.calls[0]
    assert payload["temperature"] == 0.2
    assert "Kimi" not in str(payload["messages"])
    assert "reasoning_effort" not in payload
    assert "extra_body" not in payload
    assert "cache_control" not in payload


def test_unknown_or_conflicting_model_name_never_receives_native_controls() -> None:
    gateway, client = _provider_for("deepseek-gpt-bridge")
    gateway.complete((ModelMessage(role="user", content="hello"),), reasoning_gear="audit")

    payload = client.chat.completions.calls[0]
    assert payload["temperature"] == 0.2
    assert payload["max_tokens"] == 4096
    assert "reasoning_effort" not in payload
    assert "verbosity" not in payload
    assert "extra_body" not in payload


@pytest.mark.parametrize(
    "model_id",
    [
        "my-gpt-compatible",
        "openai-compatible-proxy",
        "o3-emulator",
        "not-o4-model",
        "gpt",
        "openai-gpt-5",
    ],
)
def test_native_model_matching_is_a_conservative_closed_grammar(model_id: str) -> None:
    gateway, client = _provider_for(model_id)
    gateway.complete((ModelMessage(role="user", content="hello"),), reasoning_gear="audit")

    payload = client.chat.completions.calls[0]
    assert payload["temperature"] == 0.2
    assert "reasoning_effort" not in payload
    assert "verbosity" not in payload
    assert "extra_body" not in payload


def test_cache_and_reasoning_usage_are_projected_without_provider_payload_leakage() -> None:
    usage = SimpleNamespace(
        prompt_tokens=12,
        completion_tokens=5,
        total_tokens=17,
        prompt_cache_hit_tokens=8,
        prompt_cache_miss_tokens=4,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
    )
    response = _response()
    response.usage = usage
    client_factory, _ = _factory(response)
    gateway = ModelGateway(
        provider=OpenAICompatibleProvider(
            provider_id="test-provider",
            api_key="server-secret",
            base_url="https://provider.example/v1",
            client_factory=client_factory,
        ),
        model_id="model-alpha",
    )

    result = gateway.complete((ModelMessage(role="user", content="hello"),))
    assert result.usage.cached_input_tokens == 8
    assert result.usage.cache_miss_input_tokens == 4
    assert result.usage.reasoning_tokens == 3


def test_gateway_fails_closed_when_unavailable() -> None:
    with pytest.raises(ModelGatewayUnavailable, match="model_gateway_unavailable"):
        UnavailableModelGateway().complete((ModelMessage(role="user", content="hello"),))


def test_output_and_input_budgets_are_server_owned() -> None:
    client_factory, _ = _factory(_response())
    gateway = ModelGateway(
        provider=OpenAICompatibleProvider(
            provider_id="test-provider",
            api_key="server-secret",
            base_url="https://provider.example/v1",
            client_factory=client_factory,
        ),
        model_id="model-alpha",
        max_output_tokens=8,
        max_input_characters=4,
    )

    with pytest.raises(ModelGatewayBudgetExceeded, match="model_output_budget_exceeded"):
        gateway.complete((ModelMessage(role="user", content="hey"),), max_output_tokens=9)
    with pytest.raises(ModelGatewayBudgetExceeded, match="model_input_budget_exceeded"):
        gateway.complete((ModelMessage(role="user", content="hello"),))


def test_rag_uses_gateway_without_changing_legacy_return_shape() -> None:
    client_factory, client = _factory(_response(content="grounded [1]"))
    gateway = ModelGateway(
        provider=OpenAICompatibleProvider(
            provider_id="test-provider",
            api_key="server-secret",
            base_url="https://provider.example/v1",
            client_factory=client_factory,
        ),
        model_id="model-alpha",
        max_output_tokens=2000,
    )

    answer = generate_answer(
        "question",
        [{"content": "source text", "chunk_id": "c1", "document_id": "d1"}],
        gateway=gateway,
    )

    assert answer == "grounded [1]"
    messages = client.chat.completions.calls[0]["messages"]
    assert isinstance(messages, list)
    assert "[1] source text" in str(messages)
