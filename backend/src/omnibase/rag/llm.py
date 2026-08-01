"""LLM service for RAG answer generation.

Uses OpenAI-compatible API (DeepSeek / Zhipu GLM / any OpenAI-compatible provider).
Generates answers with citation markers [1], [2], etc. that link back to source chunks.

Configuration (via environment variables):
- LLM_API_KEY: API key for the LLM provider
- LLM_API_BASE_URL: Base URL (default: DeepSeek)
- LLM_MODEL: Model name (default: deepseek-chat)
"""

from __future__ import annotations

from typing import Any

from omnibase.core.config import get_settings
from omnibase.core.logging import get_logger

log = get_logger(__name__)

# RAG system prompt — instructs the LLM to cite sources and avoid hallucination
_RAG_SYSTEM_PROMPT = """你是一个知识检索助手。根据提供的检索上下文回答用户问题。

## 规则
1. 只基于【检索上下文】中的内容回答，不要编造信息
2. 每个事实陈述后必须标注引用编号，如 [1]、[2]
3. 如果检索上下文中没有相关信息，明确说"根据已有资料，我无法回答这个问题"
4. 回答简洁专业，使用中文

## 检索上下文格式
每个上下文片段前面有 [编号] 标记，对应源文档。"""


def _get_llm_client() -> Any:
    """Lazily create an OpenAI-compatible client."""
    from openai import OpenAI

    settings = get_settings()
    api_key = getattr(settings, "llm_api_key", "") or ""
    base_url = getattr(settings, "llm_api_base_url", "https://api.deepseek.com/v1")

    if not api_key:
        raise ValueError(
            "LLM_API_KEY is not set. Configure it in .env to enable RAG Q&A."
        )

    return OpenAI(api_key=api_key, base_url=base_url)


def _get_model() -> str:
    """Return the configured LLM model name."""
    settings = get_settings()
    return getattr(settings, "llm_model", "deepseek-chat")


def generate_answer(
    query: str,
    context_chunks: list[dict[str, Any]],
    stream: bool = False,
) -> Any:
    """Generate a RAG answer with citations.

    Args:
        query: User's question.
        context_chunks: List of {content, chunk_id, document_id, score} dicts
                       from the retrieval pipeline.
        stream: If True, returns an iterator of text chunks (for SSE).
                If False, returns the complete answer string.

    Returns:
        If stream=False: str (the complete answer)
        If stream=True: generator yielding str chunks
    """
    # Build context text with citation markers
    context_parts = []
    for i, chunk in enumerate(context_chunks):
        marker = f"[{i + 1}]"
        content = chunk.get("content", "")[:800]  # cap per-chunk to save tokens
        context_parts.append(f"{marker} {content}")

    context_text = "\n\n".join(context_parts)

    user_message = f"""## 检索上下文

{context_text}

## 用户问题

{query}"""

    client = _get_llm_client()
    model = _get_model()

    if stream:
        return _stream_answer(client, model, user_message)
    else:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": _RAG_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        return response.choices[0].message.content or ""


def _stream_answer(client: Any, model: str, user_message: str) -> Any:
    """Stream answer chunks via OpenAI-compatible SSE."""
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": _RAG_SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        temperature=0.3,
        max_tokens=2000,
        stream=True,
    )
    for chunk in response:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


def is_configured() -> bool:
    """Check if LLM API key is configured."""
    settings = get_settings()
    api_key = getattr(settings, "llm_api_key", "") or ""
    return bool(api_key)


__all__ = ["generate_answer", "is_configured"]
