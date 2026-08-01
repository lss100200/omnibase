"""RAG API router: search + playground + ask endpoints.

All endpoints are tenant-scoped (JWT required).
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from omnibase.core.logging import get_logger
from omnibase.core.rate_limit import enforce_rag_rate_limit
from omnibase.rag.embedding import is_available as embedding_ready
from omnibase.rag.llm import generate_answer
from omnibase.rag.llm import is_configured as llm_configured
from omnibase.rag.reranker import is_available as reranker_ready
from omnibase.rag.reranker import rerank
from omnibase.rag.retriever import hybrid_search
from omnibase.rag.schemas import (
    AskRequest,
    ChunkResult,
    PlaygroundRequest,
    PlaygroundResponse,
    RetrievalDebug,
    SearchRequest,
    SearchResponse,
)
from omnibase.tenants.dependencies import TenantContext, get_current_tenant

router = APIRouter(prefix="/rag", tags=["rag"])
log = get_logger(__name__)

_LLM_NOT_CONFIGURED_MESSAGE = "Answer generation is not configured."
_LLM_PROVIDER_ERROR_MESSAGE = "Answer generation is temporarily unavailable."


@router.post(
    "/search",
    dependencies=[Depends(enforce_rag_rate_limit)],
    response_model=SearchResponse,
    summary="Search the knowledge base (hybrid vector + BM25 + rerank)",
)
def search_endpoint(
    payload: SearchRequest,
    ctx: TenantContext = Depends(get_current_tenant),
) -> SearchResponse:
    """Run a full RAG search: hybrid retrieval → rerank → top-K results."""
    t0 = time.monotonic()

    # 1. Hybrid retrieval (vector + BM25 + RRF)
    candidates = hybrid_search(
        schema_name=ctx.schema_name,
        query=payload.query,
        top_k=100,
        document_id_filter=payload.document_id,
    )

    # 2. Rerank (L2 precision)
    reranked = rerank(payload.query, candidates, top_k=payload.top_k)

    # 3. Format results
    results = [
        ChunkResult(
            chunk_id=c.chunk.chunk_id,
            document_id=c.chunk.document_id,
            content=c.chunk.content,
            score=c.chunk.score,
            rrf_score=c.rrf_score,
            chunk_index=c.chunk.chunk_index,
            page_number=c.chunk.metadata.get("page", 1),
            char_start=c.chunk.char_start,
            char_end=c.chunk.char_end,
            chunk_type=c.chunk.chunk_type,
        )
        for c in reranked
    ]

    latency = (time.monotonic() - t0) * 1000
    log.info(
        "rag.search",
        schema=ctx.schema_name,
        query_preview=payload.query[:50],
        results=len(results),
        latency_ms=round(latency, 1),
    )

    return SearchResponse(
        query=payload.query,
        results=results,
        total_found=len(results),
        latency_ms=round(latency, 1),
    )


@router.post(
    "/playground",
    dependencies=[Depends(enforce_rag_rate_limit)],
    response_model=PlaygroundResponse,
    summary="Search with full retrieval debug info (for testing/tuning)",
)
def playground_endpoint(
    payload: PlaygroundRequest,
    ctx: TenantContext = Depends(get_current_tenant),
) -> PlaygroundResponse:
    """Like /search but returns debug info about each retrieval stage."""
    t0 = time.monotonic()

    # 1. Hybrid retrieval
    candidates = hybrid_search(
        schema_name=ctx.schema_name,
        query=payload.query,
        top_k=payload.vector_top_k,
    )

    vector_count = sum(1 for c in candidates if c.vector_rank is not None)
    bm25_count = sum(1 for c in candidates if c.bm25_rank is not None)

    # 2. Optional rerank
    if payload.enable_rerank:
        final = rerank(payload.query, candidates, top_k=payload.top_k)
        reranked_count = len(final)
    else:
        final = candidates[: payload.top_k]
        reranked_count = len(final)

    # 3. Format
    results = [
        ChunkResult(
            chunk_id=c.chunk.chunk_id,
            document_id=c.chunk.document_id,
            content=c.chunk.content,
            score=c.chunk.score,
            rrf_score=c.rrf_score,
            chunk_index=c.chunk.chunk_index,
            page_number=c.chunk.metadata.get("page", 1),
            char_start=c.chunk.char_start,
            char_end=c.chunk.char_end,
            chunk_type=c.chunk.chunk_type,
        )
        for c in final
    ]

    latency = (time.monotonic() - t0) * 1000
    debug = RetrievalDebug(
        query_embedded=embedding_ready(),
        vector_results_count=vector_count,
        bm25_results_count=bm25_count,
        fused_count=len(candidates),
        reranked_count=reranked_count,
        reranker_available=reranker_ready(),
    )

    return PlaygroundResponse(
        query=payload.query,
        results=results,
        debug=debug,
        latency_ms=round(latency, 1),
    )


# -----------------------------------------------------------
# POST /api/v1/rag/ask — LLM Q&A with citations (SSE streaming)
# -----------------------------------------------------------
@router.post(
    "/ask",
    dependencies=[Depends(enforce_rag_rate_limit)],
    summary="Ask a question and get a streamed answer with citations",
    description=(
        "Runs full RAG pipeline: retrieve → rerank → LLM answer with [1][2] citations. "
        "Returns an SSE stream. Success emits citations → chunk* → done; failures emit "
        "optional citations → terminal error, and never emit done after error."
    ),
)
def ask_endpoint(
    payload: AskRequest,
    ctx: TenantContext = Depends(get_current_tenant),
) -> StreamingResponse:
    """RAG Q&A with SSE streaming + citations."""
    t0 = time.monotonic()

    # 1. Retrieve + rerank
    candidates = hybrid_search(
        schema_name=ctx.schema_name,
        query=payload.query,
        top_k=100,
    )
    reranked = rerank(payload.query, candidates, top_k=payload.top_k)

    # 2. Build context chunks for LLM
    context_chunks = [
        {
            "content": c.chunk.content,
            "chunk_id": c.chunk.chunk_id,
            "document_id": c.chunk.document_id,
            "score": c.chunk.score,
            "page_number": c.chunk.metadata.get("page", 1),
        }
        for c in reranked
    ]

    # 3. Build citations metadata
    citations = [
        {
            "index": i + 1,
            "chunk_id": c["chunk_id"],
            "document_id": c["document_id"],
            "snippet": c["content"][:200],
            "page_number": c["page_number"],
            "score": c["score"],
        }
        for i, c in enumerate(context_chunks)
    ]

    retrieval_latency = (time.monotonic() - t0) * 1000

    # 4. Check LLM availability
    if not llm_configured():

        def _no_llm_stream() -> Iterator[str]:
            yield _sse("citations", {"citations": citations})
            yield _sse("error", {"message": _LLM_NOT_CONFIGURED_MESSAGE})

        return StreamingResponse(_no_llm_stream(), media_type="text/event-stream")

    # 5. Stream LLM answer
    def _stream() -> Iterator[str]:
        # Citations always precede answer chunks on a configured-provider stream.
        yield _sse("citations", {"citations": citations})

        answer_parts: list[str] = []
        try:
            for chunk_text in generate_answer(payload.query, context_chunks, stream=True):
                answer_parts.append(chunk_text)
                yield _sse("chunk", {"content": chunk_text})
        except Exception as exc:
            log.error("rag.ask.llm_failed", error=str(exc), exc_info=True)
            yield _sse("error", {"message": _LLM_PROVIDER_ERROR_MESSAGE})
            return

        total_latency = (time.monotonic() - t0) * 1000
        yield _sse(
            "done",
            {
                "answer": "".join(answer_parts),
                "citations": citations,
                "retrieval_latency_ms": round(retrieval_latency, 1),
                "total_latency_ms": round(total_latency, 1),
            },
        )

    log.info(
        "rag.ask",
        schema=ctx.schema_name,
        query_preview=payload.query[:50],
        chunks=len(context_chunks),
        retrieval_ms=round(retrieval_latency, 1),
    )

    return StreamingResponse(_stream(), media_type="text/event-stream")


def _sse(event: str, data: dict) -> str:
    """Format a Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


__all__ = ["router"]
