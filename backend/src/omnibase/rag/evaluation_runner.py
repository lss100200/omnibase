"""Deterministic/live retrieval runner and v1/v2 acceptance comparison.

Integration points are adapter protocols.  A live implementation should import
planned index/config/embedding APIs in its composition root and provide a
``RetrievalAdapter``; this module never assumes their final signatures.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from omnibase.rag.evaluation_fixture import (
    EvaluationFixture,
    QueryRankings,
    RankingRun,
    RetrievalStage,
    StageExecutionEvidence,
    StageRanking,
)
from omnibase.rag.evaluator import EvaluationReport, evaluate_rankings
from omnibase.rag.index_metadata import IndexMetadata

_REQUIRED_STAGES = (
    RetrievalStage.VECTOR,
    RetrievalStage.HYBRID,
    RetrievalStage.RERANKED,
)


class RetrievalAdapter(Protocol):
    """Adapt planned live retrieval APIs without coupling metrics to them."""

    def retrieve(
        self, query_text: str, stage: RetrievalStage
    ) -> tuple[Sequence[str], StageExecutionEvidence]: ...


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class GateResult(_FrozenModel):
    name: str
    passed: bool
    detail: str


class QueryRegression(_FrozenModel):
    query_id: str
    v1_top5_hit: bool
    v2_top5_hit: bool


class VersionComparison(_FrozenModel):
    stage: RetrievalStage
    v1: EvaluationReport
    v2: EvaluationReport
    query_regressions: tuple[QueryRegression, ...]
    gates: tuple[GateResult, ...]
    passed: bool


def _validate_stage_evidence(ranking: StageRanking) -> list[str]:
    evidence = ranking.execution
    failures: list[str] = []
    if not evidence.executed:
        failures.append(f"{ranking.stage.value}: executed=false")
    if evidence.output_count != len(ranking.ranked_chunk_ids):
        failures.append(f"{ranking.stage.value}: output count mismatch")
    expected_sources = {
        RetrievalStage.VECTOR: set(),
        RetrievalStage.HYBRID: {RetrievalStage.VECTOR.value, "bm25"},
        RetrievalStage.RERANKED: {RetrievalStage.HYBRID.value},
    }[ranking.stage]
    actual_sources = set(evidence.source_stages)
    if expected_sources != actual_sources:
        failures.append(
            f"{ranking.stage.value}: expected sources {sorted(expected_sources)}, "
            f"got {sorted(actual_sources)}"
        )
    if ranking.stage != RetrievalStage.VECTOR and evidence.input_count <= 0:
        failures.append(f"{ranking.stage.value}: input_count must prove upstream input")
    if ranking.stage == RetrievalStage.RERANKED and (
        evidence.output_count > evidence.input_count
    ):
        failures.append("reranked: output_count exceeds input_count")
    return failures


def validate_run(fixture: EvaluationFixture, run: RankingRun) -> None:
    """Require exact query coverage and concrete evidence for every stage."""

    expected_queries = {query.query_id for query in fixture.queries}
    actual_queries = {query.query_id for query in run.queries}
    if actual_queries != expected_queries:
        raise ValueError(
            f"ranking query coverage mismatch: expected {sorted(expected_queries)}, "
            f"got {sorted(actual_queries)}"
        )
    failures: list[str] = []
    for query in run.queries:
        actual_stages = {ranking.stage for ranking in query.stages}
        if actual_stages != set(_REQUIRED_STAGES):
            failures.append(
                f"{query.query_id}: expected all stages, got "
                f"{sorted(stage.value for stage in actual_stages)}"
            )
            continue
        for ranking in query.stages:
            failures.extend(
                f"{query.query_id}: {failure}"
                for failure in _validate_stage_evidence(ranking)
            )
    if failures:
        raise ValueError("strict stage execution evidence failed: " + "; ".join(failures))


def run_live_evaluation(
    *, fixture: EvaluationFixture, adapter: RetrievalAdapter, run_id: str,
    index_metadata: IndexMetadata,
) -> RankingRun:
    """Generate all three rankings through an injected live adapter."""

    queries: list[QueryRankings] = []
    for query in fixture.queries:
        stages: list[StageRanking] = []
        for stage in _REQUIRED_STAGES:
            ranked_ids, evidence = adapter.retrieve(query.query_text, stage)
            stages.append(
                StageRanking(
                    stage=stage,
                    ranked_chunk_ids=tuple(ranked_ids),
                    execution=evidence,
                )
            )
        queries.append(QueryRankings(query_id=query.query_id, stages=tuple(stages)))
    run = RankingRun(run_id=run_id, index_metadata=index_metadata, queries=tuple(queries))
    validate_run(fixture, run)
    return run


def evaluate_run(
    fixture: EvaluationFixture,
    run: RankingRun,
    stage: RetrievalStage,
) -> EvaluationReport:
    validate_run(fixture, run)
    rankings = {query.query_id: query.ranking_for(stage) for query in run.queries}
    return evaluate_rankings(
        run_id=run.run_id,
        metadata=run.index_metadata,
        stage=stage,
        queries=(
            (
                query.query_id,
                query.query_text,
                query.relevant_chunk_ids,
                rankings[query.query_id].ranked_chunk_ids,
            )
            for query in fixture.queries
        ),
    )


def compare_versions(
    fixture: EvaluationFixture,
    v1_run: RankingRun,
    v2_run: RankingRun,
    *,
    stage: RetrievalStage = RetrievalStage.RERANKED,
) -> VersionComparison:
    """Apply Phase 1.6 quality and regression gates to v1/v2 rankings."""

    v1_report = evaluate_run(fixture, v1_run, stage)
    v2_report = evaluate_run(fixture, v2_run, stage)
    v1_queries = {query.query_id: query for query in v1_report.queries}
    v2_queries = {query.query_id: query for query in v2_report.queries}
    regressions = tuple(
        QueryRegression(
            query_id=query.query_id,
            v1_top5_hit=v1_queries[query.query_id].recall.k5 > 0,
            v2_top5_hit=v2_queries[query.query_id].recall.k5 > 0,
        )
        for query in fixture.queries
    )
    lost_hits = [
        result.query_id
        for result in regressions
        if result.v1_top5_hit and not result.v2_top5_hit
    ]
    gates = (
        GateResult(
            name="v2_recall_at_5_minimum",
            passed=v2_report.aggregate.mean_recall_k5 >= 0.75,
            detail=f"v2={v2_report.aggregate.mean_recall_k5:.6f}, minimum=0.750000",
        ),
        GateResult(
            name="v2_recall_at_5_not_below_v1",
            passed=(
                v2_report.aggregate.mean_recall_k5
                >= v1_report.aggregate.mean_recall_k5
            ),
            detail=(
                f"v1={v1_report.aggregate.mean_recall_k5:.6f}, "
                f"v2={v2_report.aggregate.mean_recall_k5:.6f}"
            ),
        ),
        GateResult(
            name="no_v1_top5_hit_becomes_v2_complete_miss",
            passed=not lost_hits,
            detail=f"regressed_query_ids={lost_hits}",
        ),
        GateResult(
            name="strict_stage_execution_evidence",
            passed=True,
            detail="all queries contain executed vector, hybrid, and reranked evidence",
        ),
    )
    return VersionComparison(
        stage=stage,
        v1=v1_report,
        v2=v2_report,
        query_regressions=regressions,
        gates=gates,
        passed=all(gate.passed for gate in gates),
    )


def write_json(model: BaseModel, path: str | Path) -> Path:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output


def ranking_run_from_mapping(data: Mapping[str, object]) -> RankingRun:
    """Small boundary helper useful to adaptable integration adapters."""

    return RankingRun.model_validate(data)


__all__ = [
    "GateResult",
    "QueryRegression",
    "RetrievalAdapter",
    "VersionComparison",
    "compare_versions",
    "evaluate_run",
    "run_live_evaluation",
    "validate_run",
    "write_json",
]
