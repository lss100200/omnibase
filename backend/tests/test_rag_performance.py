from omnibase.rag.performance import (
    ComputeProvider,
    RerankerState,
    mark_warmup_result,
    profile_for_resources,
)


def test_unknown_resources_use_conservative_lite_profile() -> None:
    profile = profile_for_resources(memory_bytes=None, disk_free_bytes=None)
    assert profile.name == "lite-cpu"
    assert profile.batch_size == 4


def test_gpu_profiles_are_explicit_and_bounded() -> None:
    profile = profile_for_resources(
        memory_bytes=16 * 1024**3, disk_free_bytes=20 * 1024**3, gpu="nvidia"
    )
    assert profile.provider is ComputeProvider.CUDA
    assert profile.max_query_timeout_seconds <= 15


def test_reranker_unavailable_is_explicit_fallback() -> None:
    readiness = mark_warmup_result(
        embedding_ready=True,
        reranker_ready=False,
        provider=ComputeProvider.CPU,
        error="model_missing",
    )
    assert readiness.usable
    assert readiness.reranker_state is RerankerState.FALLBACK_RRF
    assert not readiness.reranking_enabled
    assert readiness.reason == "model_missing"
