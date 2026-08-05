"""RAG answer generation through the internal Model Gateway."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from omnibase.model_gateway import ModelMessage
from omnibase.model_gateway.service import (
    ModelGateway,
    UnavailableModelGateway,
    configured_model_gateway,
)

_RAG_SYSTEM_PROMPT = """你是 OmniBase 的知识检索助手.

规则:
1. 只根据提供的检索上下文回答, 不要编造信息.
2. 每个事实陈述后标注引用编号, 例如 [1]、[2].
3. 上下文不足时明确说明“根据已有资料, 我无法回答这个问题”.
4. 回答应简洁、专业, 并使用用户提问所采用的语言.
"""


def _messages(query: str, context_chunks: list[dict[str, Any]]) -> tuple[ModelMessage, ...]:
    context_parts: list[str] = []
    for index, chunk in enumerate(context_chunks, start=1):
        content = str(chunk.get("content", ""))[:800]
        context_parts.append(f"[{index}] {content}")
    context_text = "\n\n".join(context_parts)
    user_message = f"""## 检索上下文

{context_text}

## 用户问题

{query}"""
    return (
        ModelMessage(role="system", content=_RAG_SYSTEM_PROMPT),
        ModelMessage(role="user", content=user_message),
    )


def generate_answer(
    query: str,
    context_chunks: list[dict[str, Any]],
    stream: bool = False,
    *,
    gateway: ModelGateway | UnavailableModelGateway | None = None,
) -> str | Iterator[str]:
    """Generate a cited answer while preserving the legacy RAG call shape."""

    active_gateway = gateway or configured_model_gateway()
    messages = _messages(query, context_chunks)
    if stream:

        def _stream() -> Iterator[str]:
            for chunk in active_gateway.stream(messages, max_output_tokens=2000, temperature=0.3):
                if chunk.content:
                    yield chunk.content

        return _stream()
    return active_gateway.complete(messages, max_output_tokens=2000, temperature=0.3).content


def is_configured() -> bool:
    return isinstance(configured_model_gateway(), ModelGateway)


__all__ = ["generate_answer", "is_configured"]
