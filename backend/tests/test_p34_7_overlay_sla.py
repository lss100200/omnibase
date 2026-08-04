"""Capacity, SLA and fault-injection aggregation tests for P34.7."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts" / "overlay"
sys.path.insert(0, str(SCRIPT_DIR))


def _load_reporter():
    path = SCRIPT_DIR / "p34_7_sla_report.py"
    spec = importlib.util.spec_from_file_location("p34_7_sla_report", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("P34.7 SLA reporter could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SLA = _load_reporter()


def _policy() -> dict[str, object]:
    return {
        "schema": SLA.POLICY_SCHEMA,
        "scenarios": {
            "forced_derp_relay": {
                "min_samples": 2,
                "min_success_ratio": 1.0,
                "max_p95_ms": 1000,
                "min_observed_concurrency": 2,
                "required_transport_paths": ["derp"],
                "allowed_outcomes": ["committed"],
            },
            "node_revoke_propagation": {
                "min_samples": 2,
                "min_success_ratio": 1.0,
                "max_p95_ms": 5000,
                "min_observed_concurrency": 1,
                "required_transport_paths": ["not_applicable"],
                "allowed_outcomes": ["rejected"],
            },
        },
    }


def _sample(
    *, attempt: str, scenario: str, duration: int, path: str, outcome: str
) -> dict[str, object]:
    return {
        "schema": SLA.SAMPLE_SCHEMA,
        "attempt_id": attempt,
        "scenario": scenario,
        "environment": "production",
        "duration_ms": duration,
        "success": True,
        "expected_effect_verified": True,
        "outcome": outcome,
        "transport_path": path,
        "source_node_id": "node-a",
        "target_node_id": "node-b",
        "concurrency": 2,
        "direct_infrastructure_route_observed": False,
        "secret_exposure_observed": False,
    }


def _passing_samples() -> list[dict[str, object]]:
    return [
        _sample(
            attempt="derp-1",
            scenario="forced_derp_relay",
            duration=400,
            path="derp",
            outcome="committed",
        ),
        _sample(
            attempt="derp-2",
            scenario="forced_derp_relay",
            duration=600,
            path="derp",
            outcome="committed",
        ),
        _sample(
            attempt="revoke-1",
            scenario="node_revoke_propagation",
            duration=1200,
            path="not_applicable",
            outcome="rejected",
        ),
        _sample(
            attempt="revoke-2",
            scenario="node_revoke_propagation",
            duration=1500,
            path="not_applicable",
            outcome="rejected",
        ),
    ]


def test_thresholds_and_transport_paths_can_pass() -> None:
    report = SLA.build_report(policy=_policy(), samples=_passing_samples())
    assert report["status"] == "passed"
    assert report["production_sla_passed"] is True
    assert report["scenario_results"]["forced_derp_relay"]["p95_ms"] == 600
    assert report["vetoes"] == []


def test_missing_samples_and_slow_recovery_remain_not_proven() -> None:
    samples = _passing_samples()[:2]
    samples[1]["duration_ms"] = 5001
    report = SLA.build_report(policy=_policy(), samples=samples)
    assert report["status"] == "blocked/not_proven"
    assert "node_revoke_propagation:insufficient_samples" in report["blockers"]
    assert "forced_derp_relay:p95_above_threshold" in report["blockers"]


def test_unverified_success_secret_and_direct_route_are_vetoes() -> None:
    samples = _passing_samples()
    samples[0]["expected_effect_verified"] = False
    samples[1]["secret_exposure_observed"] = True
    samples[2]["direct_infrastructure_route_observed"] = True
    report = SLA.build_report(policy=_policy(), samples=samples)
    assert report["status"] == "veto"
    assert set(report["vetoes"]) == {
        "direct_infrastructure_route_observed",
        "secret_exposure_observed",
        "unverified_success_claim",
    }


def test_duplicate_attempt_identity_is_a_veto() -> None:
    samples = _passing_samples()
    samples[1]["attempt_id"] = samples[0]["attempt_id"]
    report = SLA.build_report(policy=_policy(), samples=samples)
    assert report["status"] == "veto"
    assert "duplicate_attempt_identity" in report["vetoes"]
