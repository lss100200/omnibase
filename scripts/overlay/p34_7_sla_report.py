"""Aggregate production P34.7 capacity, SLA, and fault-injection observations."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from p34_7_overlay_common import (
    ProductionGateError,
    canonical_bytes,
    percentile,
    reject_secret_fields,
    require,
    require_exact_keys,
    safe_json_file,
    sha256_bytes,
    sha256_file,
)

POLICY_SCHEMA = "omnibase.p34-7.overlay-sla-policy.v1"
SAMPLE_SCHEMA = "omnibase.p34-7.overlay-sla-sample.v1"
REPORT_SCHEMA = "omnibase.p34-7.overlay-sla-report.v1"
OUTCOMES = {"cleaned", "committed", "rejected", "restored", "unknown"}
TRANSPORT_PATHS = {"derp", "direct", "not_applicable"}


def _read_samples(path: Path) -> list[dict[str, Any]]:
    require(path.name.lower() != ".env", "SLA sample input cannot be an env file")
    require(not path.is_symlink(), "symlinked SLA sample input is forbidden")
    samples: list[dict[str, Any]] = []
    try:
        lines = path.resolve(strict=True).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as exc:
        raise ProductionGateError("SLA sample input is unavailable") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            decoded = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ProductionGateError(f"SLA sample line {line_number} is invalid JSON") from exc
        require(isinstance(decoded, dict), f"SLA sample line {line_number} is not an object")
        reject_secret_fields(decoded, context=f"sample[{line_number}]")
        _validate_sample(decoded, line_number=line_number)
        samples.append(decoded)
    return samples


def _validate_sample(sample: dict[str, Any], *, line_number: int) -> None:
    require_exact_keys(
        sample,
        allowed={
            "attempt_id",
            "concurrency",
            "direct_infrastructure_route_observed",
            "duration_ms",
            "environment",
            "expected_effect_verified",
            "outcome",
            "scenario",
            "schema",
            "secret_exposure_observed",
            "source_node_id",
            "success",
            "target_node_id",
            "transport_path",
        },
        context=f"sample line {line_number}",
    )
    require(sample.get("schema") == SAMPLE_SCHEMA, "SLA sample schema is unsupported")
    require(
        sample.get("environment") == "production",
        "SLA sample is not production evidence",
    )
    require(
        isinstance(sample.get("attempt_id"), str) and sample["attempt_id"],
        "attempt_id missing",
    )
    require(
        isinstance(sample.get("scenario"), str) and sample["scenario"],
        "scenario missing",
    )
    duration = sample.get("duration_ms")
    require(
        isinstance(duration, (int, float)) and 0 <= duration <= 86_400_000,
        "duration invalid",
    )
    require(isinstance(sample.get("success"), bool), "success must be boolean")
    require(isinstance(sample.get("expected_effect_verified"), bool), "effect proof missing")
    require(sample.get("outcome") in OUTCOMES, "sample outcome is unsupported")
    require(sample.get("transport_path") in TRANSPORT_PATHS, "transport path is unsupported")
    require(
        isinstance(sample.get("direct_infrastructure_route_observed"), bool),
        "direct infrastructure flag missing",
    )
    require(
        isinstance(sample.get("secret_exposure_observed"), bool),
        "secret exposure flag missing",
    )
    concurrency = sample.get("concurrency")
    require(
        isinstance(concurrency, int) and 1 <= concurrency <= 100_000,
        "concurrency invalid",
    )


def _validate_policy(policy: dict[str, Any]) -> dict[str, dict[str, Any]]:
    require_exact_keys(
        policy,
        allowed={"schema", "scenarios"},
        context="SLA policy",
    )
    require(policy.get("schema") == POLICY_SCHEMA, "SLA policy schema is unsupported")
    scenarios = policy.get("scenarios")
    require(isinstance(scenarios, dict) and scenarios, "SLA policy must define scenarios")
    validated: dict[str, dict[str, Any]] = {}
    for name, value in scenarios.items():
        require(isinstance(name, str) and name, "SLA scenario name is invalid")
        require(isinstance(value, dict), f"SLA policy {name} is invalid")
        require_exact_keys(
            value,
            allowed={
                "allowed_outcomes",
                "max_p95_ms",
                "min_observed_concurrency",
                "min_samples",
                "min_success_ratio",
                "required_transport_paths",
            },
            context=f"SLA policy {name}",
        )
        require(
            isinstance(value.get("min_samples"), int) and value["min_samples"] >= 1,
            f"SLA policy {name} min_samples invalid",
        )
        ratio = value.get("min_success_ratio")
        require(isinstance(ratio, (int, float)) and 0 <= ratio <= 1, f"{name} ratio invalid")
        p95 = value.get("max_p95_ms")
        require(isinstance(p95, (int, float)) and p95 > 0, f"{name} p95 invalid")
        concurrency = value.get("min_observed_concurrency")
        require(
            isinstance(concurrency, int) and concurrency >= 1,
            f"{name} concurrency invalid",
        )
        outcomes = value.get("allowed_outcomes")
        require(
            isinstance(outcomes, list) and outcomes and set(outcomes) <= OUTCOMES,
            f"{name} outcomes invalid",
        )
        paths = value.get("required_transport_paths")
        require(
            isinstance(paths, list) and set(paths) <= TRANSPORT_PATHS,
            f"{name} transport requirements invalid",
        )
        validated[name] = value
    return validated


def _evaluate_scenario(
    *, name: str, threshold: dict[str, Any], samples: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    selected = [sample for sample in samples if sample["scenario"] == name]
    durations = [float(sample["duration_ms"]) for sample in selected]
    successes = sum(bool(sample["success"]) for sample in selected)
    success_ratio = successes / len(selected) if selected else 0.0
    observed_paths = sorted({str(sample["transport_path"]) for sample in selected})
    observed_outcomes = sorted({str(sample["outcome"]) for sample in selected})
    max_concurrency = max((int(sample["concurrency"]) for sample in selected), default=0)
    p95 = percentile(durations, 95) if durations else None
    failures = []
    if len(selected) < int(threshold["min_samples"]):
        failures.append("insufficient_samples")
    if success_ratio < float(threshold["min_success_ratio"]):
        failures.append("success_ratio_below_threshold")
    if p95 is None or p95 > float(threshold["max_p95_ms"]):
        failures.append("p95_above_threshold")
    if max_concurrency < int(threshold["min_observed_concurrency"]):
        failures.append("concurrency_below_threshold")
    if not set(threshold["required_transport_paths"]) <= set(observed_paths):
        failures.append("required_transport_path_missing")
    if not set(observed_outcomes) <= set(threshold["allowed_outcomes"]):
        failures.append("unexpected_outcome")
    result = {
        "failures": failures,
        "max_observed_concurrency": max_concurrency,
        "observed_outcomes": observed_outcomes,
        "observed_transport_paths": observed_paths,
        "p95_ms": p95,
        "sample_count": len(selected),
        "success_ratio": success_ratio,
    }
    return result, [f"{name}:{failure}" for failure in failures]


def _sample_vetoes(samples: list[dict[str, Any]]) -> list[str]:
    attempts = Counter(str(sample["attempt_id"]) for sample in samples)
    vetoes = []
    if any(count > 1 for count in attempts.values()):
        vetoes.append("duplicate_attempt_identity")
    if any(sample["secret_exposure_observed"] for sample in samples):
        vetoes.append("secret_exposure_observed")
    if any(sample["direct_infrastructure_route_observed"] for sample in samples):
        vetoes.append("direct_infrastructure_route_observed")
    if any(sample["success"] and not sample["expected_effect_verified"] for sample in samples):
        vetoes.append("unverified_success_claim")
    return vetoes


def build_report(*, policy: dict[str, Any], samples: list[dict[str, Any]]) -> dict[str, Any]:
    scenarios = _validate_policy(policy)
    for index, sample in enumerate(samples, start=1):
        _validate_sample(sample, line_number=index)
    vetoes = _sample_vetoes(samples)
    blockers: list[str] = []
    results: dict[str, Any] = {}
    for name, threshold in scenarios.items():
        result, scenario_blockers = _evaluate_scenario(
            name=name, threshold=threshold, samples=samples
        )
        results[name] = result
        blockers.extend(scenario_blockers)

    status = "veto" if vetoes else ("blocked/not_proven" if blockers else "passed")
    report = {
        "schema": REPORT_SCHEMA,
        "status": status,
        "production_sla_passed": status == "passed",
        "blockers": sorted(blockers),
        "vetoes": sorted(vetoes),
        "scenario_results": results,
        "sample_count": len(samples),
        "sample_set_sha256": sha256_bytes(canonical_bytes(samples)),
        "policy_sha256": sha256_bytes(canonical_bytes(policy)),
    }
    return report


def write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy", required=True)
    parser.add_argument("--samples", required=True)
    parser.add_argument("--report-out", required=True)
    args = parser.parse_args()
    try:
        policy_path = Path(args.policy)
        samples_path = Path(args.samples)
        policy = safe_json_file(policy_path)
        samples = _read_samples(samples_path)
        report = build_report(policy=policy, samples=samples)
        write_report(Path(args.report_out).resolve(), report)
    except (ProductionGateError, OSError, TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(
        f"P34.7 Overlay SLA report: {report['status']} "
        f"({report['sample_count']} samples, {sha256_file(Path(args.report_out).resolve())})"
    )
    return 0 if report["production_sla_passed"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
