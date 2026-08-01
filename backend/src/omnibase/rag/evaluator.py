"""Pure retrieval metrics over immutable relevance judgements and rankings.

This module performs no file, model, database, network, configuration, or
retriever access.  Boundary I/O and live execution belong to evaluation_runner.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from omnibase.rag.evaluation_fixture import (
    EvaluationFixture,
    FixtureChunkEntry,
    FixtureQueryEntry,
    RetrievalStage,
    load_fixture,
)
from omnibase.rag.index_metadata import IndexMetadata, get_active_metadata

RECALL_CUTOFFS: Final = (1, 3, 5, 10)


@dataclass(frozen=True, slots=True)
class RecallAtK:
    k1: float
    k3: float
    k5: float
    k10: float


@dataclass(frozen=True, slots=True)
class RankingEvidence:
    query_id: str
    total_relevant: int
    found_in_ranking: int
    rank_positions: Mapping[str, int | None]


@dataclass(frozen=True, slots=True)
class QueryResult:
    query_id: str
    recall: RecallAtK
    evidence: RankingEvidence


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class RecallAtKReport(_FrozenModel):
    k1: float
    k3: float
    k5: float
    k10: float


class RankingEvidenceReport(_FrozenModel):
    query_id: str
    total_relevant: int
    found_in_ranking: int
    rank_positions: dict[str, int | None]


class QueryReport(_FrozenModel):
    query_id: str
    query_text: str
    recall: RecallAtKReport
    evidence: RankingEvidenceReport


class AggregateMetrics(_FrozenModel):
    query_count: int
    mean_recall_k1: float
    mean_recall_k3: float
    mean_recall_k5: float
    mean_recall_k10: float


class EvaluationReport(_FrozenModel):
    run_id: str = "legacy-fixture"
    metadata: IndexMetadata
    stage: RetrievalStage = RetrievalStage.RERANKED
    queries: tuple[QueryReport, ...]
    aggregate: AggregateMetrics


def _recall_at_k(relevant: set[str], ranked: Sequence[str], k: int) -> float:
    """Return the fraction of relevant identifiers present in the first k."""

    if k < 1:
        raise ValueError("k must be at least 1")
    if not relevant:
        return 1.0
    return len(relevant & set(ranked[:k])) / len(relevant)


def _build_evidence(
    query_id: str,
    relevant: set[str],
    ranked: Sequence[str],
) -> RankingEvidence:
    """Build deterministic zero-based positions for each relevant identifier."""

    first_positions: dict[str, int] = {}
    for position, chunk_id in enumerate(ranked):
        first_positions.setdefault(chunk_id, position)
    positions = {
        chunk_id: first_positions.get(chunk_id) for chunk_id in sorted(relevant)
    }
    return RankingEvidence(
        query_id=query_id,
        total_relevant=len(relevant),
        found_in_ranking=sum(position is not None for position in positions.values()),
        rank_positions=positions,
    )


def evaluate_rankings(
    *,
    run_id: str,
    metadata: IndexMetadata,
    stage: RetrievalStage,
    queries: Iterable[tuple[str, str, Sequence[str], Sequence[str]]],
) -> EvaluationReport:
    """Evaluate one stage's rankings.

    Each query tuple is ``(query_id, query_text, relevant_ids, ranked_ids)``.
    The evaluator deliberately accepts plain values, not fixture/runner models,
    keeping this metrics layer independent of I/O and execution concerns.
    """

    reports: list[QueryReport] = []
    sums = dict.fromkeys(RECALL_CUTOFFS, 0.0)
    for query_id, query_text, relevant_ids, ranked_ids in queries:
        relevant = set(relevant_ids)
        recalls = {k: _recall_at_k(relevant, ranked_ids, k) for k in RECALL_CUTOFFS}
        evidence = _build_evidence(query_id, relevant, ranked_ids)
        for cutoff, value in recalls.items():
            sums[cutoff] += value
        reports.append(
            QueryReport(
                query_id=query_id,
                query_text=query_text,
                recall=RecallAtKReport(
                    k1=recalls[1], k3=recalls[3], k5=recalls[5], k10=recalls[10]
                ),
                evidence=RankingEvidenceReport(
                    query_id=evidence.query_id,
                    total_relevant=evidence.total_relevant,
                    found_in_ranking=evidence.found_in_ranking,
                    rank_positions=dict(evidence.rank_positions),
                ),
            )
        )

    count = len(reports)
    if count == 0:
        raise ValueError("at least one query is required")
    return EvaluationReport(
        run_id=run_id,
        metadata=metadata,
        stage=stage,
        queries=tuple(reports),
        aggregate=AggregateMetrics(
            query_count=count,
            mean_recall_k1=sums[1] / count,
            mean_recall_k3=sums[3] / count,
            mean_recall_k5=sums[5] / count,
            mean_recall_k10=sums[10] / count,
        ),
    )


def evaluate(
    fixture: EvaluationFixture,
    metadata: IndexMetadata | None = None,
) -> EvaluationReport:
    """Backward-compatible evaluation of deprecated inline fixture rankings.

    New code should use separate ``RankingRun`` artifacts via
    :func:`omnibase.rag.evaluation_runner.evaluate_run`.
    """

    active = metadata if metadata is not None else get_active_metadata()
    if fixture.metadata.dimension != active.dimension:
        from omnibase.rag.index_metadata import DimensionMismatchError

        raise DimensionMismatchError(
            expected=active.dimension, actual=fixture.metadata.dimension
        )
    missing = [query.query_id for query in fixture.queries if query.ranked_chunk_ids is None]
    if missing:
        raise ValueError(
            "legacy evaluate() requires ranked_chunk_ids; use evaluation_runner "
            f"for separate rankings (missing for {missing})"
        )
    return evaluate_rankings(
        run_id="legacy-fixture",
        metadata=active,
        stage=RetrievalStage.RERANKED,
        queries=(
            (
                query.query_id,
                query.query_text,
                query.relevant_chunk_ids,
                query.ranked_chunk_ids or (),
            )
            for query in fixture.queries
        ),
    )


def write_report(report: EvaluationReport, path: str | Path) -> Path:
    """Write a deterministic JSON report, preserving the legacy public API."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return output


__all__ = [
    "AggregateMetrics",
    "EvaluationFixture",
    "EvaluationReport",
    "FixtureChunkEntry",
    "FixtureQueryEntry",
    "QueryReport",
    "QueryResult",
    "RankingEvidence",
    "RankingEvidenceReport",
    "RecallAtK",
    "RecallAtKReport",
    "evaluate",
    "evaluate_rankings",
    "load_fixture",
    "write_report",
]
