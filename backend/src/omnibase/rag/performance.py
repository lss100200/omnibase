"""Measured, bounded RAG readiness and degradation profiles.

Profiles are conservative and explicit: CPU/CUDA/MPS are separate, embedding
readiness is separate from reranker readiness, and an unavailable or timing-out
reranker degrades to ``fallback_rrf`` without faking reranker success. Batch
sizes, warmup, keep-alive and query timeouts are all bounded. Tests must not
auto-download models or use external network.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ComputeProvider(StrEnum):
    CPU = "cpu"
    CUDA = "cuda"
    MPS = "mps"


class RerankerState(StrEnum):
    READY = "ready"
    FALLBACK_RRF = "fallback_rrf"
    WARMING = "warming"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class EmbeddingState(StrEnum):
    READY = "ready"
    WARMING = "warming"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class RagPerformanceProfile:
    name: str
    provider: ComputeProvider
    batch_size: int
    keep_alive_seconds: int
    warmup_timeout_seconds: float
    max_query_timeout_seconds: float
    max_memory_bytes: int | None
    model_cache_required: bool = True

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self.keep_alive_seconds < 0:
            raise ValueError("keep_alive_seconds must be non-negative")
        if self.warmup_timeout_seconds <= 0 or self.warmup_timeout_seconds > 600:
            raise ValueError("warmup_timeout_seconds out of range")
        if self.max_query_timeout_seconds <= 0 or self.max_query_timeout_seconds > 120:
            raise ValueError("max_query_timeout_seconds out of range")


@dataclass(frozen=True)
class RagReadiness:
    embedding_state: EmbeddingState
    reranker_state: RerankerState
    provider: ComputeProvider
    cold_start_ms: float | None = None
    warm_start_ms: float | None = None
    reason: str | None = None

    @property
    def embedding_ready(self) -> bool:
        return self.embedding_state is EmbeddingState.READY

    @property
    def usable(self) -> bool:
        return self.embedding_ready

    @property
    def reranking_enabled(self) -> bool:
        return self.reranker_state is RerankerState.READY

    @property
    def fallback_rrf_active(self) -> bool:
        return self.reranker_state in {
            RerankerState.FALLBACK_RRF,
            RerankerState.TIMED_OUT,
            RerankerState.FAILED,
            RerankerState.WARMING,
        }


def profile_for_resources(
    *,
    memory_bytes: int | None,
    disk_free_bytes: int | None,
    gpu: str = "none",
) -> RagPerformanceProfile:
    """Choose a bounded profile; unknown resources select the conservative profile.

    Unknown memory, disk or GPU never become a positive CUDA/MPS claim. Low or
    unknown resources select ``lite-cpu``; proven NVIDIA selects CUDA; Apple
    Silicon selects MPS; everything else falls back to a balanced CPU profile.
    """
    low_memory = memory_bytes is not None and memory_bytes < 8 * 1024**3
    low_disk = disk_free_bytes is not None and disk_free_bytes < 5 * 1024**3
    if low_memory or low_disk or memory_bytes is None or disk_free_bytes is None:
        return RagPerformanceProfile(
            "lite-cpu",
            ComputeProvider.CPU,
            batch_size=4,
            keep_alive_seconds=60,
            warmup_timeout_seconds=20.0,
            max_query_timeout_seconds=10.0,
            max_memory_bytes=memory_bytes,
        )
    gpu_lower = gpu.lower()
    if gpu_lower.startswith("nvidia") or gpu_lower == "cuda":
        return RagPerformanceProfile(
            "cuda-balanced",
            ComputeProvider.CUDA,
            batch_size=32,
            keep_alive_seconds=300,
            warmup_timeout_seconds=30.0,
            max_query_timeout_seconds=15.0,
            max_memory_bytes=memory_bytes,
        )
    if gpu_lower.startswith("mps") or gpu_lower in {"metal", "apple"}:
        return RagPerformanceProfile(
            "mps-balanced",
            ComputeProvider.MPS,
            batch_size=16,
            keep_alive_seconds=180,
            warmup_timeout_seconds=30.0,
            max_query_timeout_seconds=15.0,
            max_memory_bytes=memory_bytes,
        )
    return RagPerformanceProfile(
        "cpu-balanced",
        ComputeProvider.CPU,
        batch_size=16,
        keep_alive_seconds=180,
        warmup_timeout_seconds=30.0,
        max_query_timeout_seconds=15.0,
        max_memory_bytes=memory_bytes,
    )


def mark_warmup_result(
    *,
    embedding_ready: bool,
    reranker_ready: bool,
    provider: ComputeProvider,
    error: str | None = None,
) -> RagReadiness:
    """Convert warmup evidence into explicit readiness without faking reranker success.

    A missing or failed reranker reports ``fallback_rrf`` and keeps retrieval
    usable rather than claiming reranking succeeded. Embedding and reranker
    readiness are reported separately.
    """
    if error is not None and not embedding_ready:
        embedding_state = EmbeddingState.FAILED
    elif error is not None and not reranker_ready:
        embedding_state = EmbeddingState.READY
    else:
        embedding_state = EmbeddingState.READY if embedding_ready else EmbeddingState.FAILED
    reranker_state = RerankerState.READY if reranker_ready else RerankerState.FALLBACK_RRF
    return RagReadiness(
        embedding_state=embedding_state,
        reranker_state=reranker_state,
        provider=provider,
        reason=error if not embedding_ready or not reranker_ready else None,
    )


def mark_query_result(
    *,
    reranker_ready: bool,
    reranker_timed_out: bool,
    provider: ComputeProvider,
    embedding_state: EmbeddingState = EmbeddingState.READY,
    reason: str | None = None,
) -> RagReadiness:
    """Convert query-time evidence into explicit readiness.

    A timing-out or unavailable reranker falls back to ``fallback_rrf`` (or
    ``timed_out``) deterministically. Retrieval stays usable when embeddings
    are ready; reranking readiness is never faked.
    """
    if reranker_timed_out:
        reranker_state = RerankerState.TIMED_OUT
    elif reranker_ready:
        reranker_state = RerankerState.READY
    else:
        reranker_state = RerankerState.FALLBACK_RRF
    return RagReadiness(
        embedding_state=embedding_state,
        reranker_state=reranker_state,
        provider=provider,
        reason=reason,
    )


__all__ = [
    "ComputeProvider",
    "EmbeddingState",
    "RagPerformanceProfile",
    "RagReadiness",
    "RerankerState",
    "mark_query_result",
    "mark_warmup_result",
    "profile_for_resources",
]
