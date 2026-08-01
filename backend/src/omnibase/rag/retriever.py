"""Version-aware hybrid retrieval with explicit stage failure semantics."""

from __future__ import annotations

from dataclasses import dataclass, field

from omnibase.core.config import get_settings
from omnibase.core.logging import get_logger
from omnibase.rag.embedding import embed_query_for_version
from omnibase.rag.index_metadata import IndexLane, get_index_lane
from omnibase.rag.store import (
    SearchMode,
    SearchResult,
    SearchStageError,
    search_bm25_lane,
    search_vector_lane,
)

log = get_logger(__name__)
RRF_K = 60


@dataclass
class HybridResult:
    chunk: SearchResult
    rrf_score: float
    vector_rank: int | None
    bm25_rank: int | None


@dataclass(frozen=True)
class RetrievalTrace:
    """Safe retrieval diagnostics; contains no query, content, or exception text."""

    lane: str
    mode: str
    query_embedded: bool
    vector_results_count: int
    bm25_results_count: int
    fused_count: int
    vector_failed: bool = False
    bm25_failed: bool = False
    failed_stages: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class HybridSearchResponse:
    results: list[HybridResult]
    trace: RetrievalTrace


def hybrid_search_detailed(
    schema_name: str,
    query: str,
    top_k: int = 100,
    document_id_filter: str | None = None,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
    *,
    lane: IndexLane | None = None,
    mode: SearchMode = SearchMode.ONLINE,
) -> HybridSearchResponse:
    """Search vector and BM25 in exactly one lane.

    ONLINE returns healthy-stage hits when another stage fails. STRICT raises a
    typed ``SearchStageError``. A successful zero-hit stage is never marked failed.
    """
    resolved = lane or get_index_lane(get_settings().embedding_index_version)
    vector_hits: list[SearchResult] = []
    vector_failed = False
    query_embedded = False
    failed: list[str] = []

    try:
        query_vector = embed_query_for_version(query, resolved.version)
        query_embedded = True
        vector_stage = search_vector_lane(
            schema_name,
            query_vector,
            lane=resolved,
            top_k=top_k,
            document_id_filter=document_id_filter,
            mode=mode,
        )
        vector_hits = vector_stage.hits
        vector_failed = vector_stage.failed
        if vector_failed:
            failed.append("vector")
    except Exception as exc:
        if mode is SearchMode.STRICT:
            if isinstance(exc, SearchStageError):
                raise
            raise SearchStageError("embedding", resolved, exc) from exc
        vector_failed = True
        failed.append("embedding")
        log.warning(
            "retriever.embedding_unavailable",
            lane=str(resolved.version),
            error_type=type(exc).__name__,
        )

    bm25_stage = search_bm25_lane(
        schema_name,
        query,
        lane=resolved,
        top_k=top_k,
        document_id_filter=document_id_filter,
        mode=mode,
    )
    if bm25_stage.failed:
        failed.append("bm25")

    fused = _rrf_fuse(
        vector_hits,
        bm25_stage.hits,
        k=RRF_K,
        vector_weight=vector_weight,
        bm25_weight=bm25_weight,
    )[:top_k]
    trace = RetrievalTrace(
        lane=str(resolved.version),
        mode=mode.value,
        query_embedded=query_embedded,
        vector_results_count=len(vector_hits),
        bm25_results_count=len(bm25_stage.hits),
        fused_count=len(fused),
        vector_failed=vector_failed,
        bm25_failed=bm25_stage.failed,
        failed_stages=tuple(dict.fromkeys(failed)),
    )
    return HybridSearchResponse(results=fused, trace=trace)


def hybrid_search(
    schema_name: str,
    query: str,
    top_k: int = 100,
    document_id_filter: str | None = None,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[HybridResult]:
    """Backwards-compatible online hybrid search wrapper."""
    return hybrid_search_detailed(
        schema_name=schema_name,
        query=query,
        top_k=top_k,
        document_id_filter=document_id_filter,
        vector_weight=vector_weight,
        bm25_weight=bm25_weight,
        mode=SearchMode.ONLINE,
    ).results


def _rrf_fuse(
    vector_results: list[SearchResult],
    bm25_results: list[SearchResult],
    k: int = RRF_K,
    vector_weight: float = 1.0,
    bm25_weight: float = 1.0,
) -> list[HybridResult]:
    vec_rank = {result.chunk_id: index + 1 for index, result in enumerate(vector_results)}
    bm_rank = {result.chunk_id: index + 1 for index, result in enumerate(bm25_results)}
    chunk_map = {result.chunk_id: result for result in vector_results}
    chunk_map.update({result.chunk_id: result for result in bm25_results if result.chunk_id not in chunk_map})
    scored: list[HybridResult] = []
    for chunk_id in set(vec_rank) | set(bm_rank):
        vr = vec_rank.get(chunk_id)
        br = bm_rank.get(chunk_id)
        score = (vector_weight / (k + vr) if vr else 0.0) + (
            bm25_weight / (k + br) if br else 0.0
        )
        scored.append(HybridResult(chunk_map[chunk_id], score, vr, br))
    scored.sort(key=lambda item: item.rrf_score, reverse=True)
    return scored


__all__ = [
    "RRF_K",
    "HybridResult",
    "HybridSearchResponse",
    "RetrievalTrace",
    "hybrid_search",
    "hybrid_search_detailed",
]
