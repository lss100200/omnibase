from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _runner():
    path = Path(__file__).with_name("run_p7_4_hardening_matrix.py")
    spec = importlib.util.spec_from_file_location("p74_hardening_runner_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _repo() -> Path:
    return Path(__file__).resolve().parents[2]


def test_micro_matrix_uses_real_schema_and_keeps_release_claims_false(
    tmp_path: Path,
) -> None:
    runner = _runner()
    output = tmp_path / "receipt.json"
    report = runner.run_hardening_matrix(
        repo_root=_repo(), output_path=output, profile_name="test"
    )

    assert report["schema"] == "omnibase.p7-4.hardening-matrix.v1"
    assert report["desktop_schema_version"] == 12
    assert report["counts"]["workspaces"] == 2
    assert report["counts"]["installations"] == 4
    assert report["counts"]["catalog_versions"] >= 8
    assert report["integrity"] == "ok"
    assert report["foreign_key_violations"] == 0
    assert report["bounded_soak"]["cycles"] == 2
    assert len(report["bounded_soak"]["rss_samples_bytes"]) == 2
    assert report["bounded_soak"]["automatic_replays"] == 0
    assert report["bounded_soak"]["unresolved_effects"] == 0
    assert report["bounded_soak"]["passed"] is True
    assert report["bounded_soak"]["nightly_8h_completed"] is False
    assert report["bounded_soak"]["release_candidate_24h_completed"] is False
    assert report["engineering_gate_passed"] is True
    assert report["authenticode_verified"] is False
    assert report["marketplace_verified"] is False
    assert report["human_visual_reviewed"] is False
    assert report["production_ready"] is False
    assert report["release_authorized"] is False
    assert json.loads(output.read_text(encoding="utf-8")) == report


def test_p95_is_nearest_rank_and_unknown_profile_fails_closed(tmp_path: Path) -> None:
    runner = _runner()
    assert runner._p95([1.0, 2.0, 3.0, 4.0, 100.0]) == 100.0
    try:
        runner.run_hardening_matrix(
            repo_root=_repo(),
            output_path=tmp_path / "receipt.json",
            profile_name="unknown",
        )
    except ValueError as exc:
        assert str(exc) == "p74_hardening_profile_invalid"
    else:
        raise AssertionError("unknown profile did not fail closed")
