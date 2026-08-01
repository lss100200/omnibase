"""Focused Phase 1.6 deterministic/live evaluation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from omnibase.rag.evaluation_fixture import (
    RankingRun,
    RetrievalStage,
    load_fixture,
)
from omnibase.rag.evaluation_runner import compare_versions, evaluate_run, validate_run

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _ranking(stage: str, ids: list[str], input_count: int) -> dict:
    sources = {"vector": [], "hybrid": ["vector", "bm25"], "reranked": ["hybrid"]}[stage]
    return {
        "stage": stage,
        "ranked_chunk_ids": ids,
        "execution": {
            "stage": stage,
            "executed": True,
            "implementation": f"fake-{stage}",
            "source_stages": sources,
            "input_count": input_count,
            "output_count": len(ids),
        },
    }


def _run(run_id: str, final: dict[str, list[str]]) -> RankingRun:
    all_ids = ["c1", "c2", "c3", "c4", "c5", "c6"]
    queries = []
    for query_id, reranked in final.items():
        queries.append(
            {
                "query_id": query_id,
                "stages": [
                    _ranking("vector", all_ids, 0),
                    _ranking("hybrid", all_ids, len(all_ids) * 2),
                    _ranking("reranked", reranked, len(all_ids)),
                ],
            }
        )
    return RankingRun.model_validate(
        {
            "run_id": run_id,
            "index_metadata": {
                "model_name": f"model-{run_id}",
                "dimension": 512 if run_id == "v1" else 1024,
                "version": 1 if run_id == "v1" else 2,
            },
            "queries": queries,
        }
    )


def test_ground_truth_is_immutable_and_has_no_generated_rankings() -> None:
    fixture = load_fixture(FIXTURES / "retrieval_eval_ground_truth.json")
    assert fixture.queries[0].ranked_chunk_ids is None
    with pytest.raises(ValidationError):
        fixture.queries[0].query_text = "changed"  # type: ignore[misc]


def test_all_three_stage_rankings_are_evaluable() -> None:
    fixture = load_fixture(FIXTURES / "retrieval_eval_ground_truth.json")
    run = _run(
        "v2",
        {"q1": ["c1", "c2"], "q2": ["c3", "c4"], "q3": ["c5", "c6"], "q4": ["c5"]},
    )
    for stage in RetrievalStage:
        report = evaluate_run(fixture, run, stage)
        assert report.stage == stage
        assert report.aggregate.query_count == 4


def test_v2_comparison_passes_all_quality_gates() -> None:
    fixture = load_fixture(FIXTURES / "retrieval_eval_ground_truth.json")
    v1 = _run(
        "v1",
        {"q1": ["c1"], "q2": ["c3"], "q3": ["c5"], "q4": ["c5"]},
    )
    v2 = _run(
        "v2",
        {"q1": ["c1", "c2"], "q2": ["c3", "c4"], "q3": ["c5", "c6"], "q4": ["c5"]},
    )
    comparison = compare_versions(fixture, v1, v2)
    assert comparison.passed
    assert comparison.v2.aggregate.mean_recall_k5 == 1.0
    assert all(gate.passed for gate in comparison.gates)


def test_top5_hit_to_complete_miss_fails_even_if_aggregate_is_high() -> None:
    fixture = load_fixture(FIXTURES / "retrieval_eval_ground_truth.json")
    v1 = _run(
        "v1",
        {"q1": ["c1"], "q2": ["x"], "q3": ["c5"], "q4": ["c5"]},
    )
    v2 = _run(
        "v2",
        {"q1": ["c1", "c2"], "q2": ["c3", "c4"], "q3": ["c5", "c6"], "q4": ["x"]},
    )
    comparison = compare_versions(fixture, v1, v2)
    regression_gate = next(
        gate for gate in comparison.gates if gate.name == "no_v1_top5_hit_becomes_v2_complete_miss"
    )
    assert comparison.v2.aggregate.mean_recall_k5 == 0.75
    assert not regression_gate.passed
    assert not comparison.passed


def test_strict_stage_execution_evidence_rejects_unexecuted_stage() -> None:
    fixture = load_fixture(FIXTURES / "retrieval_eval_ground_truth.json")
    data = _run(
        "v2",
        {"q1": ["c1"], "q2": ["c3"], "q3": ["c5"], "q4": ["c5"]},
    ).model_dump()
    data["queries"][0]["stages"][1]["execution"]["executed"] = False
    run = RankingRun.model_validate(data)
    with pytest.raises(ValueError, match="strict stage execution evidence"):
        validate_run(fixture, run)
