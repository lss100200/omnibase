"""Tests for fixture parsing, report writing, and adversarial inputs."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from omnibase.rag.evaluator import (
    EvaluationFixture,
    EvaluationReport,
    evaluate,
    load_fixture,
    write_report,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"


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
# Fixture parsing tests
# ===========================================================================


class TestLoadFixture:
    """Given the checked-in fixture JSON file,
    When load_fixture is called,
    Then a valid EvaluationFixture is returned."""

    def test_loads_real_fixture(self) -> None:
        """Given the checked-in fixture at tests/fixtures/,
        When load_fixture reads it,
        Then the result has 3 queries and correct metadata."""
        path = FIXTURE_DIR / "retrieval_eval_fixture.json"
        fixture = load_fixture(path)
        assert len(fixture.queries) == 3
        assert fixture.metadata.model_name == "BAAI/bge-small-zh-v1.5"
        assert fixture.metadata.dimension == 512
        assert fixture.metadata.version == 1

    def test_loads_real_fixture_and_evaluates(self) -> None:
        """Given the real fixture,
        When evaluated,
        Then all three queries produce valid recall values."""
        path = FIXTURE_DIR / "retrieval_eval_fixture.json"
        fixture = load_fixture(path)
        report = evaluate(fixture)
        assert report.aggregate.query_count == 3
        for q in report.queries:
            assert 0.0 <= q.recall.k1 <= 1.0
            assert 0.0 <= q.recall.k10 <= 1.0
            assert q.recall.k1 <= q.recall.k3 <= q.recall.k5 <= q.recall.k10
            assert q.evidence.total_relevant > 0

    def test_rejects_malformed_json(self) -> None:
        """Given a file with invalid JSON,
        When load_fixture is called,
        Then a parse error is raised."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False, encoding="utf-8"
        ) as tmp_file:
            tmp_file.write("not json")
            tmp_path = Path(tmp_file.name)
        try:
            with pytest.raises((json.JSONDecodeError, ValueError)):
                load_fixture(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_rejects_missing_queries(self) -> None:
        """Given a fixture dict with no queries key,
        When parsed,
        Then a validation error is raised."""
        data: dict = {"metadata": _sample_fixture_dict()["metadata"]}
        with pytest.raises(ValueError):  # Pydantic validation error
            EvaluationFixture.model_validate(data)

    def test_rejects_empty_queries(self) -> None:
        """Given a fixture with an empty queries list,
        When parsed,
        Then a validation error is raised."""
        data = {
            "metadata": _sample_fixture_dict()["metadata"],
            "queries": [],
        }
        with pytest.raises(ValueError):
            EvaluationFixture.model_validate(data)


# ===========================================================================
# Report writing tests
# ===========================================================================


class TestWriteReport:
    """Given an EvaluationReport,
    When write_report is called,
    Then a deterministic JSON file is produced and re-parseable."""

    def test_writes_and_round_trips(self) -> None:
        """Given a report from a sample fixture evaluation,
        When written to JSON and re-loaded,
        Then the data in the file matches the original report."""
        fixture = EvaluationFixture.model_validate(_sample_fixture_dict())
        report = evaluate(fixture)

        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)

        try:
            written = write_report(report, tmp_path)
            assert written == tmp_path
            assert tmp_path.exists()

            raw = tmp_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            assert data["metadata"]["model_name"] == "BAAI/bge-small-zh-v1.5"
            assert data["metadata"]["dimension"] == 512
            assert data["aggregate"]["query_count"] == 1
            assert "queries" in data
            assert len(data["queries"]) == 1
            # Verify report can be re-parsed
            parsed = EvaluationReport.model_validate(data)
            assert parsed == report
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_overwrite_is_deterministic(self) -> None:
        """Given the same report is written twice to the same path,
        When comparing outputs,
        Then they are byte-identical."""
        fixture = EvaluationFixture.model_validate(_sample_fixture_dict())
        report = evaluate(fixture)

        with tempfile.NamedTemporaryFile(
            suffix=".json", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)

        try:
            write_report(report, tmp_path)
            first = tmp_path.read_bytes()
            write_report(report, tmp_path)
            second = tmp_path.read_bytes()
            assert first == second
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_creates_parent_directories(self) -> None:
        """Given an output path under a non-existent directory,
        When write_report is called,
        Then the parent directory is created."""
        fixture = EvaluationFixture.model_validate(_sample_fixture_dict())
        report = evaluate(fixture)
        tmp_dir = Path(tempfile.mkdtemp())
        nested = tmp_dir / "a" / "b" / "report.json"
        try:
            written = write_report(report, nested)
            assert written.exists()
        finally:
            import shutil
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ===========================================================================
# Adversarial probes
# ===========================================================================


class TestAdversarialInputs:
    """Given malformed or edge-case inputs,
    When the evaluator processes them,
    Then it rejects or handles them without crashing."""

    def test_duplicate_chunk_ids_in_ranking(self) -> None:
        """Given a ranked list with duplicate entries,
        When evaluating,
        Then the first occurrence determines the position."""
        data = {
            "metadata": {
                "model_name": "BAAI/bge-small-zh-v1.5",
                "dimension": 512,
                "version": 1,
            },
            "queries": [
                {
                    "query_id": "dup",
                    "query_text": "dups",
                    "relevant_chunk_ids": ["c1"],
                    "ranked_chunk_ids": ["c2", "c1", "c1", "c3"],
                },
            ],
        }
        fixture = EvaluationFixture.model_validate(data)
        report = evaluate(fixture)
        q = report.queries[0]
        assert q.recall.k5 == 1.0  # c1 found at position 1
        assert q.evidence.rank_positions["c1"] == 1  # first occurrence

    def test_relevant_subset_of_ranking(self) -> None:
        """Given relevant chunks are a subset of ranked chunks,
        When evaluating,
        Then recall@k should eventually reach 1.0 at some k."""
        data = {
            "metadata": {
                "model_name": "BAAI/bge-small-zh-v1.5",
                "dimension": 512,
                "version": 1,
            },
            "queries": [
                {
                    "query_id": "sub",
                    "query_text": "subset",
                    "relevant_chunk_ids": ["c1", "c2"],
                    "ranked_chunk_ids": [f"c{i}" for i in range(1, 21)],
                },
            ],
        }
        fixture = EvaluationFixture.model_validate(data)
        report = evaluate(fixture)
        q = report.queries[0]
        assert q.recall.k1 == 0.5  # only c1 in top-1
        assert q.recall.k3 == 1.0  # both c1 and c2 in top-3
        assert q.recall.k10 == 1.0
        assert q.evidence.found_in_ranking == 2
