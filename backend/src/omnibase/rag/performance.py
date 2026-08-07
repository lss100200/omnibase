"""Measured, bounded RAG readiness and degradation profiles."""

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


@dataclass(frozen=True)
class RagPerformanceProfile:
    name: str
    provider: ComputeProvider
    batch_size: int
    keep_alive_seconds: int
    warmup_timeout_seconds: float
    max_query_timeout_seconds: float
    model_cache_required: bool = True


@dataclass(frozen=True)
class RagReadiness:
    embedding_ready: bool
    reranker_state: RerankerState
    provider: ComputeProvider
    cold_start_ms: float | None = None
    warm_start_ms: float | None = None
    reason: str | None = None

    @property
    def usable(self) -> bool:
        return self.embedding_ready

    @property
    def reranking_enabled(self) -> bool:
        return self.reranker_state is RerankerState.READY


def profile_for_resources(
    *, memory_bytes: int | None, disk_free_bytes: int | None, gpu: str = "none"
) -> RagPerformanceProfile:
    """Choose a bounded profile; unknown resources select the conservative profile."""
    low_memory = memory_bytes is not None and memory_bytes < 8 * 1024**3
    low_disk = disk_free_bytes is not None and disk_free_bytes < 5 * 1024**3
    if low_memory or low_disk or memory_bytes is None or disk_free_bytes is None:
        return RagPerformanceProfile("lite-cpu", ComputeProvider.CPU, 4, 60, 20.0, 10.0)
    if gpu.lower() in {"cuda", "nvidia"}:
        return RagPerformanceProfile("cuda-balanced", ComputeProvider.CUDA, 32, 300, 30.0, 15.0)
    if gpu.lower() in {"mps", "metal", "apple"}:
        return RagPerformanceProfile("mps-balanced", ComputeProvider.MPS, 16, 180, 30.0, 15.0)
    return RagPerformanceProfile("cpu-balanced", ComputeProvider.CPU, 16, 180, 30.0, 15.0)


def mark_warmup_result(
    *,
    embedding_ready: bool,
    reranker_ready: bool,
    provider: ComputeProvider,
    error: str | None = None,
) -> RagReadiness:
    """Convert warmup evidence into explicit readiness without faking reranker success."""
    return RagReadiness(
        embedding_ready=embedding_ready,
        reranker_state=RerankerState.READY if reranker_ready else RerankerState.FALLBACK_RRF,
        provider=provider,
        reason=error if not embedding_ready or not reranker_ready else None,
    )


__all__ = [
    "ComputeProvider",
    "RagPerformanceProfile",
    "RagReadiness",
    "RerankerState",
    "mark_warmup_result",
    "profile_for_resources",
]
