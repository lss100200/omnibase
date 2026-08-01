"""Unit tests for omnibase.rag.evaluator — recall@k and evidence computation."""

from __future__ import annotations

import pytest

from omnibase.rag.evaluator import _build_evidence, _recall_at_k

# ===========================================================================
# _recall_at_k unit tests
# ===========================================================================


class TestRecallAtKUnit:
    """Given a set of relevant chunk IDs and a ranked list,
    When computing _recall_at_k,
    Then the fraction of relevant chunks found in the top-k is returned."""

    def test_perfect_recall(self) -> None:
        """Given all relevant chunks appear in top-3,
        When k=3,
        Then recall is 1.0."""
        relevant = {"c1", "c2", "c3"}
        ranked = ["c1", "c2", "c3", "c4", "c5"]
        assert _recall_at_k(relevant, ranked, k=3) == 1.0

    def test_partial_recall(self) -> None:
        """Given 2 of 3 relevant chunks appear in top-3,
        When k=3,
        Then recall is 2/3."""
        relevant = {"c1", "c2", "c3"}
        ranked = ["c1", "c4", "c2", "c5"]  # c3 is at index 5+
        assert _recall_at_k(relevant, ranked, k=3) == pytest.approx(2 / 3)

    def test_zero_recall(self) -> None:
        """Given no relevant chunks appear in top-3,
        When k=3,
        Then recall is 0.0."""
        relevant = {"c1", "c2", "c3"}
        ranked = ["c4", "c5", "c6", "c7"]
        assert _recall_at_k(relevant, ranked, k=3) == 0.0

    def test_k_larger_than_ranked_list(self) -> None:
        """Given k exceeds the ranked list length,
        When k=10,
        Then only available items are examined."""
        relevant = {"c1", "c2", "c3"}
        ranked = ["c1", "c2", "c4"]  # only 3 items
        assert _recall_at_k(relevant, ranked, k=10) == pytest.approx(2 / 3)

    def test_empty_relevant_set(self) -> None:
        """Given an empty relevant set (degenerate),
        When k=5,
        Then recall is 1.0."""
        assert _recall_at_k(set(), ["c1"], k=5) == 1.0

    def test_empty_ranked_list(self) -> None:
        """Given an empty ranked list,
        When k=5,
        Then recall is 0.0."""
        assert _recall_at_k({"c1"}, [], k=5) == 0.0

    def test_k1_cutoff(self) -> None:
        """Given k=1,
        When the top result is relevant,
        Then recall@1 is 1/total_relevant."""
        relevant = {"c1", "c2", "c3"}
        ranked = ["c1", "c4", "c5"]
        assert _recall_at_k(relevant, ranked, k=1) == pytest.approx(1 / 3)


# ===========================================================================
# _build_evidence unit tests
# ===========================================================================


class TestBuildEvidence:
    """Given relevant chunks and a ranked list,
    When building RankingEvidence,
    Then positions are recorded (0-based) or None for missing chunks."""

    def test_all_found_with_positions(self) -> None:
        """Given all relevant chunks appear in ranking,
        When building evidence,
        Then found_in_ranking equals total and positions are set."""
        evidence = _build_evidence(
            "q1", {"c1", "c2"}, ["c1", "c2", "c3"]
        )
        assert evidence.query_id == "q1"
        assert evidence.total_relevant == 2
        assert evidence.found_in_ranking == 2
        assert evidence.rank_positions["c1"] == 0
        assert evidence.rank_positions["c2"] == 1
        assert None not in evidence.rank_positions.values()

    def test_some_missing(self) -> None:
        """Given one relevant chunk is absent from the ranking,
        When building evidence,
        Then its position is None."""
        evidence = _build_evidence(
            "q2", {"c1", "c_missing"}, ["c1", "c2"]
        )
        assert evidence.found_in_ranking == 1
        assert evidence.total_relevant == 2
        assert evidence.rank_positions["c1"] == 0
        assert evidence.rank_positions["c_missing"] is None

    def test_all_missing(self) -> None:
        """Given no relevant chunks appear in the ranking,
        When building evidence,
        Then found_in_ranking is 0 and all positions are None."""
        evidence = _build_evidence(
            "q3", {"c1", "c2"}, ["c3", "c4"]
        )
        assert evidence.found_in_ranking == 0
        assert all(v is None for v in evidence.rank_positions.values())
