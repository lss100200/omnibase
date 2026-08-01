"""Integration tests for omnibase.rag.evaluator — evaluate() function."""

from __future__ import annotations

import pytest

from omnibase.rag.evaluator import EvaluationFixture, evaluate
from omnibase.rag.index_metadata import (
    ACTIVE_METADATA,
    DimensionMismatchError,
    IndexMetadata,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_fixture_dict() -> dict:
    """Return the minimal valid fixture dict for test construction."""
    return {
        "metadata": {
            "model_name": "BAAI/bge-small-zh-v1.5",
            "dimension": 512,
            "version": 1,
        },
        "queries": [
            {
                "query_id": "q1",
                "query_text": "test query",
                "relevant_chunk_ids": ["c1", "c2", "c3"],
                "ranked_chunk_ids": ["c1", "c4", "c2", "c5", "c6"],
            },
        ],
    }


# ===========================================================================
# evaluate integration tests
# ===========================================================================


class TestEvaluateHappyPath:
    """Given a valid fixture with matching metadata,
    When evaluate is called,
    Then a report with correct recall@k and aggregate metrics is returned."""

    def test_single_query_perfect_recall(self) -> None:
        """Given one query with all relevant chunks ranked top,
        When evaluating,
        Then recall@10 is 1.0."""
        data = _sample_fixture_dict()
        # Make perfect: all relevant in top-3
        data["queries"][0]["ranked_chunk_ids"] = ["c1", "c2", "c3", "c4", "c5"]
        fixture = EvaluationFixture.model_validate(data)
        report = evaluate(fixture)
        q = report.queries[0]
        assert q.recall.k3 == 1.0
        assert q.recall.k5 == 1.0
        assert q.recall.k10 == 1.0
        assert report.aggregate.query_count == 1

    def test_single_query_partial_recall(self) -> None:
        """Given one query with some relevant chunks outside top-3,
        When evaluating,
        Then recall@3 < 1.0 but recall@10 may reach 1.0."""
        data = {
            "metadata": {
                "model_name": "BAAI/bge-small-zh-v1.5",
                "dimension": 512,
                "version": 1,
            },
            "queries": [
                {
                    "query_id": "qp",
                    "query_text": "partial",
                    "relevant_chunk_ids": ["c1", "c2", "c3"],
                    "ranked_chunk_ids": ["c1", "c4", "c2", "c5", "c3"],
                },
            ],
        }
        fixture = EvaluationFixture.model_validate(data)
        report = evaluate(fixture)
        q = report.queries[0]
        # At k=3: c1, c4, c2 => only c1 and c2 found
        assert q.recall.k3 == pytest.approx(2 / 3)
        # At k=5: c1, c4, c2, c5, c3 => all three found
        assert q.recall.k5 == 1.0
        assert q.recall.k10 == 1.0

    def test_multiple_queries_aggregate(self) -> None:
        """Given three queries with known recall values,
        When evaluating,
        Then aggregate means are correct."""
        data = {
            "metadata": {
                "model_name": "BAAI/bge-small-zh-v1.5",
                "dimension": 512,
                "version": 1,
            },
            "queries": [
                {
                    "query_id": "q_perfect",
                    "query_text": "p",
                    "relevant_chunk_ids": ["c1"],
                    "ranked_chunk_ids": ["c1", "c2"],
                },
                {
                    "query_id": "q_zero",
                    "query_text": "z",
                    "relevant_chunk_ids": ["c1"],
                    "ranked_chunk_ids": ["c2", "c3"],
                },
                {
                    "query_id": "q_mid",
                    "query_text": "m",
                    "relevant_chunk_ids": ["c1", "c2"],
                    "ranked_chunk_ids": ["c1", "c3"],
                },
            ],
        }
        fixture = EvaluationFixture.model_validate(data)
        report = evaluate(fixture)

        assert report.aggregate.query_count == 3
        # q_perfect: recall@1 = 1.0, q_zero: 0.0, q_mid: 0.5
        assert report.aggregate.mean_recall_k1 == pytest.approx((1.0 + 0.0 + 0.5) / 3)
        assert report.aggregate.mean_recall_k10 == pytest.approx((1.0 + 0.0 + 0.5) / 3)

    def test_report_metadata_matches_active(self) -> None:
        """Given a valid evaluation,
        When inspecting report.metadata,
        Then it equals ACTIVE_METADATA."""
        fixture = EvaluationFixture.model_validate(_sample_fixture_dict())
        report = evaluate(fixture)
        assert report.metadata == ACTIVE_METADATA

    def test_ranking_evidence_in_report(self) -> None:
        """Given a query with known ranking,
        When evaluating,
        Then the report includes ranking evidence with position data."""
        fixture = EvaluationFixture.model_validate(_sample_fixture_dict())
        report = evaluate(fixture)
        q = report.queries[0]
        assert q.evidence.query_id == "q1"
        assert q.evidence.total_relevant == 3
        assert q.evidence.found_in_ranking >= 0
        assert len(q.evidence.rank_positions) == 3


class TestEvaluateExplicitMetadata:
    """Given an explicit IndexMetadata override,
    When evaluating,
    Then that metadata is used for the report (not ACTIVE_METADATA)."""

    def test_explicit_metadata_in_report(self) -> None:
        """Given a custom metadata instance with same dimension,
        When evaluating with explicit metadata,
        Then the report carries the custom metadata."""
        custom = IndexMetadata(
            model_name="test/model",
            dimension=512,
            version=99,
        )
        fixture = EvaluationFixture.model_validate(_sample_fixture_dict())
        report = evaluate(fixture, metadata=custom)
        assert report.metadata.model_name == "test/model"
        assert report.metadata.version == 99
        assert report.metadata.dimension == 512


# ===========================================================================
# Dimension mismatch tests
# ===========================================================================


class TestEvaluateDimensionMismatch:
    """Given a fixture whose dimension conflicts with active metadata,
    When evaluating,
    Then DimensionMismatchError is raised before any computation."""

    def test_mismatch_raises_clear_error(self) -> None:
        """Given fixture dimension=1024 vs active 512,
        When evaluate is called,
        Then DimensionMismatchError is raised."""
        data = {
            "metadata": {
                "model_name": "BAAI/bge-small-zh-v1.5",
                "dimension": 1024,  # mismatch!
                "version": 1,
            },
            "queries": [
                {
                    "query_id": "q1",
                    "query_text": "test",
                    "relevant_chunk_ids": ["c1"],
                    "ranked_chunk_ids": ["c1"],
                },
            ],
        }
        fixture = EvaluationFixture.model_validate(data)
        with pytest.raises(DimensionMismatchError) as exc_info:
            evaluate(fixture)
        assert exc_info.value.expected == 512
        assert exc_info.value.actual == 1024

    def test_mismatch_with_explicit_metadata_as_well(self) -> None:
        """Given explicit metadata dim 512 but fixture dim 768,
        When evaluating,
        Then mismatch is still raised."""
        data = {
            "metadata": {
                "model_name": "BAAI/bge-small-zh-v1.5",
                "dimension": 768,
                "version": 1,
            },
            "queries": [
                {
                    "query_id": "q1",
                    "query_text": "test",
                    "relevant_chunk_ids": ["c1"],
                    "ranked_chunk_ids": ["c1"],
                },
            ],
        }
        fixture = EvaluationFixture.model_validate(data)
        custom = IndexMetadata(model_name="x", dimension=512, version=1)
        with pytest.raises(DimensionMismatchError):
            evaluate(fixture, metadata=custom)
