"""Immutable ground-truth and generated-ranking models for retrieval evaluation.

Ground truth is intentionally stored separately from rankings produced by a
retrieval run.  This prevents a fixture from silently becoming both the test
oracle and the system output under test.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from omnibase.rag.index_metadata import IndexMetadata


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FixtureDocumentEntry(_FrozenModel):
    document_id: str
    title: str = ""


class FixtureChunkEntry(_FrozenModel):
    chunk_id: str
    document_id: str
    content: str = ""


class FixtureQueryEntry(_FrozenModel):
    """An immutable query and its human-authored relevance judgement.

    ``ranked_chunk_ids`` is accepted only as a deprecated v1 fixture input.
    New deterministic/live tooling stores rankings in a separate ``RankingRun``.
    """

    query_id: str
    query_text: str
    relevant_chunk_ids: tuple[str, ...] = Field(min_length=1)
    ranked_chunk_ids: tuple[str, ...] | None = None


class EvaluationFixture(_FrozenModel):
    """Checked-in ground truth; generated rankings never belong here."""

    metadata: IndexMetadata
    documents: tuple[FixtureDocumentEntry, ...] = ()
    chunks: tuple[FixtureChunkEntry, ...] = ()
    queries: tuple[FixtureQueryEntry, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_references(self) -> EvaluationFixture:
        query_ids = [query.query_id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("query_id values must be unique")

        chunk_ids = [chunk.chunk_id for chunk in self.chunks]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise ValueError("chunk_id values must be unique")

        if chunk_ids:
            known = set(chunk_ids)
            missing = {
                chunk_id
                for query in self.queries
                for chunk_id in query.relevant_chunk_ids
                if chunk_id not in known
            }
            if missing:
                raise ValueError(f"relevant chunks absent from fixture: {sorted(missing)}")
        return self


class RetrievalStage(StrEnum):
    VECTOR = "vector"
    HYBRID = "hybrid"
    RERANKED = "reranked"


class StageExecutionEvidence(_FrozenModel):
    """Auditable proof that a ranking stage ran rather than being inferred.

    ``implementation`` identifies the concrete adapter/backend.  Source stages
    and counts make accidental pass-through or mislabeled rankings detectable.
    """

    stage: RetrievalStage
    executed: bool
    implementation: str = Field(min_length=1)
    source_stages: tuple[str, ...] = ()
    input_count: int = Field(ge=0)
    output_count: int = Field(ge=0)


class StageRanking(_FrozenModel):
    stage: RetrievalStage
    ranked_chunk_ids: tuple[str, ...]
    execution: StageExecutionEvidence

    @model_validator(mode="after")
    def validate_evidence_matches_ranking(self) -> StageRanking:
        if self.execution.stage != self.stage:
            raise ValueError("execution evidence stage does not match ranking stage")
        if self.execution.output_count != len(self.ranked_chunk_ids):
            raise ValueError("execution output_count does not match ranking length")
        if len(self.ranked_chunk_ids) != len(set(self.ranked_chunk_ids)):
            raise ValueError("ranked_chunk_ids must not contain duplicates")
        return self


class QueryRankings(_FrozenModel):
    query_id: str
    stages: tuple[StageRanking, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_stages(self) -> QueryRankings:
        stages = [ranking.stage for ranking in self.stages]
        if len(stages) != len(set(stages)):
            raise ValueError("each retrieval stage may appear only once per query")
        return self

    def ranking_for(self, stage: RetrievalStage) -> StageRanking:
        for ranking in self.stages:
            if ranking.stage == stage:
                return ranking
        raise KeyError(f"missing {stage.value} ranking for query {self.query_id}")


class RankingRun(_FrozenModel):
    """Generated rankings from one deterministic or live index version."""

    run_id: str = Field(min_length=1)
    index_metadata: IndexMetadata
    queries: tuple[QueryRankings, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_queries(self) -> RankingRun:
        query_ids = [query.query_id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("query rankings must have unique query_id values")
        return self


def load_fixture(path: str | Path) -> EvaluationFixture:
    """Load and validate immutable ground truth from JSON."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return EvaluationFixture.model_validate(data)


def load_ranking_run(path: str | Path) -> RankingRun:
    """Load generated deterministic rankings from a separate JSON artifact."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return RankingRun.model_validate(data)


__all__ = [
    "EvaluationFixture",
    "FixtureChunkEntry",
    "FixtureDocumentEntry",
    "FixtureQueryEntry",
    "QueryRankings",
    "RankingRun",
    "RetrievalStage",
    "StageExecutionEvidence",
    "StageRanking",
    "load_fixture",
    "load_ranking_run",
]
