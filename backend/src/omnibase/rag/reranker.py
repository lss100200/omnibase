"""BGE-reranker-v2-m3: precision reranking for AI RAG.

L2 layer: takes top-100 from hybrid retrieval, narrows to top-5 with
cross-encoder precision. The reranker scores (query, document) pairs
using a transformer that reads both together — much more accurate than
cosine similarity of independent embeddings.

Runs on CPU via sentence-transformers. ~84ms/100 pairs on GPU,
~260ms/100 pairs on CPU (int8 quantized).

For Agent memory scenarios, 100→5 reranking at <300ms is well within
the tool-call latency budget.
"""

from __future__ import annotations

import hashlib
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

from omnibase.core.config import get_settings
from omnibase.core.logging import get_logger
from omnibase.rag.retriever import HybridResult

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

log = get_logger(__name__)

# Singleton model
_reranker: CrossEncoder | None = None
_reranker_initialized = False
_reranker_lock = threading.Lock()
_reranker_model = "BAAI/bge-reranker-v2-m3"


def _get_reranker() -> CrossEncoder | None:
    """Lazily load the reranker model (singleton, thread-safe)."""
    global _reranker, _reranker_initialized
    model = _reranker
    initialized = _reranker_initialized
    if model is not None:
        return model
    if initialized:
        return None

    with _reranker_lock:
        model = _reranker
        initialized = _reranker_initialized
        if model is not None:
            return model
        if initialized:
            return None

        settings = get_settings()
        configured_path = Path(settings.reranker_model_path)
        if configured_path.is_dir():
            model_source = str(configured_path)
            local_files_only = True
        elif settings.model_download_enabled:
            model_source = _reranker_model
            local_files_only = False
        else:
            _reranker_initialized = True
            log.warning(
                "rag.reranker.model_missing",
                model=_reranker_model,
                expected_path=str(configured_path),
                fallback="rrf",
            )
            return None

        try:
            from sentence_transformers import CrossEncoder

            log.info(
                "rag.reranker.model_loading",
                model=_reranker_model,
                source=model_source,
                local_files_only=local_files_only,
            )
            _reranker = CrossEncoder(
                model_source,
                device="cpu",
                max_length=512,
                cache_dir=settings.model_cache_dir,
                local_files_only=local_files_only,
            )
            log.info(
                "rag.reranker.model_ready",
                model=_reranker_model,
                device="cpu",
            )
        except ImportError:
            log.error(
                "rag.reranker.import_error",
                msg="sentence-transformers not installed",
            )
        except Exception as exc:
            log.error("rag.reranker.model_load_failed", error=str(exc), exc_info=True)
        finally:
            _reranker_initialized = True

        return _reranker


def rerank(
    query: str,
    candidates: list[HybridResult],
    top_k: int = 5,
) -> list[HybridResult]:
    """Rerank hybrid retrieval results using BGE cross-encoder.

    Args:
        query: User query.
        candidates: Hybrid results from retriever (top-100).
        top_k: Number of results to return after reranking.

    Returns:
        Top-k candidates sorted by reranker score (highest first).
        If reranker unavailable, returns top_k by RRF score (graceful degradation).
    """
    if not candidates:
        return []

    model = _get_reranker()
    if model is None:
        log.warning("rag.reranker.unavailable", msg="Using RRF scores as fallback")
        return candidates[:top_k]

    # Limit to avoid CPU timeout (>200 pairs is slow on CPU)
    max_pairs = min(len(candidates), 100)
    to_rerank = candidates[:max_pairs]

    try:
        started = time.monotonic()
        # Build (query, document) pairs
        pairs = [(query, c.chunk.content[:1000]) for c in to_rerank]

        # Score all pairs
        scores = model.predict(pairs, show_progress_bar=False)

        # Attach reranker scores
        scored = [(c, float(s)) for c, s in zip(to_rerank, scores, strict=False)]

        # Sort by reranker score
        scored.sort(key=lambda x: x[1], reverse=True)

        result = [c for c, _ in scored[:top_k]]

        log.info(
            "rag.reranker.complete",
            query_hash=hashlib.sha256(query.encode("utf-8")).hexdigest(),
            query_length=len(query),
            candidates=len(to_rerank),
            returned=len(result),
            best_score=scored[0][1] if scored else 0,
            duration_ms=round((time.monotonic() - started) * 1000, 1),
        )
        return result

    except Exception as exc:
        log.error("rag.reranker.failed", error_type=type(exc).__name__, exc_info=True)
        return candidates[:top_k]


def is_available() -> bool:
    """Check if the reranker model is loaded."""
    return _get_reranker() is not None


__all__ = ["is_available", "rerank"]
