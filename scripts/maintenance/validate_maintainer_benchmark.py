#!/usr/bin/env python3
"""Validate the P34.3 maintainer-map benchmark protocol and evaluator key."""

from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
from typing import Any

SUITE_PATH = Path("docs/maintainers/benchmark/benchmark-suite.json")
KEY_PATH = Path("docs/maintainers/benchmark/evaluator-key.json")
MAP_PATH = Path("docs/maintainers/maintenance-map.json")
LEVEL_IDS = {"L0", "L1", "L2", "L3", "L4"}
REQUIRED_CANDIDATE_VISIBLE = {
    "scripts/maintenance/**",
    "frontend/app/**",
    "frontend/components/**",
    "frontend/lib/**",
    "frontend/stores/**",
    "frontend/.eslintrc.json",
    "frontend/.prettierrc.json",
    "frontend/components.json",
    "frontend/Dockerfile",
    "frontend/next.config.js",
    "frontend/package.json",
    "frontend/pnpm-lock.yaml",
    "frontend/postcss.config.js",
    "frontend/tailwind.config.ts",
    "frontend/tsconfig.json",
}
REQUIRED_BUILD_EXCLUSIONS = {
    "**/node_modules/**",
    "**/.pnpm-store/**",
    "**/.next/**",
    "**/dist/**",
    "**/*.tsbuildinfo",
}
RUNNER_OUTPUT_INSTRUCTION = (
    "Output exactly one JSON object directly. Do not include a preface, analysis, "
    "Markdown code fence, or trailing text."
)


class ValidationError(Exception):
    """Raised when the benchmark definition is unsafe or internally inconsistent."""


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"top-level JSON must be an object: {path}")
    return value


def _nonempty_string(value: object, *, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value.strip()


def _string_list(value: object, *, label: str, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        requirement = "a non-empty list" if nonempty else "a list"
        raise ValidationError(f"{label} must be {requirement}")
    result: list[str] = []
    for index, item in enumerate(value):
        result.append(_nonempty_string(item, label=f"{label}[{index}]"))
    return result


def _unique_ids(items: object, *, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise ValidationError(f"{label} must be a non-empty list")
    indexed: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(f"{label}[{index}] must be an object")
        item_id = _nonempty_string(item.get("id"), label=f"{label}[{index}].id")
        if item_id in indexed:
            raise ValidationError(f"duplicate {label} id: {item_id}")
        indexed[item_id] = item
    return indexed


def _unique_ids_by_field(
    items: object,
    *,
    label: str,
    id_field: str,
) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list) or not items:
        raise ValidationError(f"{label} must be a non-empty list")
    indexed: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValidationError(f"{label}[{index}] must be an object")
        item_id = _nonempty_string(
            item.get(id_field), label=f"{label}[{index}].{id_field}"
        )
        if item_id in indexed:
            raise ValidationError(f"duplicate {label} {id_field}: {item_id}")
        _nonempty_string(item.get("role"), label=f"{label}[{index}].role")
        indexed[item_id] = item
    return indexed


def _validate_repo_pattern(pattern: str, *, label: str) -> None:
    normalized = pattern.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValidationError(
            f"{label} must be repository-relative without '..': {pattern}"
        )


def _validate_levels(scoring: dict[str, Any]) -> None:
    maximum = scoring.get("maximum")
    if maximum != 100:
        raise ValidationError("scoring.maximum must equal 100")
    dimensions = _unique_ids(scoring.get("dimensions"), label="scoring.dimensions")
    points = 0
    for dimension_id, dimension in dimensions.items():
        value = dimension.get("points")
        if not isinstance(value, int) or value <= 0:
            raise ValidationError(
                f"dimension {dimension_id} points must be a positive integer"
            )
        points += value
    if points != maximum:
        raise ValidationError(f"scoring dimensions total {points}, expected {maximum}")

    levels = _unique_ids(scoring.get("levels"), label="scoring.levels")
    if set(levels) != LEVEL_IDS:
        raise ValidationError(f"scoring levels must be exactly {sorted(LEVEL_IDS)}")
    ordered = sorted(levels.values(), key=lambda item: item["minimum"])
    expected_minimum = 0
    for level in ordered:
        minimum = level.get("minimum")
        maximum_value = level.get("maximum")
        if not isinstance(minimum, int) or not isinstance(maximum_value, int):
            raise ValidationError("level minimum and maximum must be integers")
        if minimum != expected_minimum or maximum_value < minimum:
            raise ValidationError(
                "scoring levels must cover 0..100 without gaps or overlap"
            )
        expected_minimum = maximum_value + 1
    if expected_minimum != 101:
        raise ValidationError("scoring levels must end at 100")


def _validate_identity(suite: dict[str, Any], key: dict[str, Any]) -> None:
    for label, document in (("suite", suite), ("key", key)):
        if document.get("schema_version") != 1:
            raise ValidationError(f"{label}.schema_version must equal 1")
    benchmark_id = _nonempty_string(
        suite.get("benchmark_id"), label="suite.benchmark_id"
    )
    if key.get("benchmark_id") != benchmark_id:
        raise ValidationError("suite and evaluator key benchmark_id must match")
    if key.get("visibility") != "evaluator_only_do_not_copy_to_candidate_workspace":
        raise ValidationError(
            "evaluator key must carry the evaluator-only visibility marker"
        )


def _validate_candidate_boundaries(suite: dict[str, Any]) -> None:
    candidate_visible = _string_list(
        suite.get("candidate_visible"), label="candidate_visible"
    )
    candidate_forbidden = _string_list(
        suite.get("candidate_forbidden"), label="candidate_forbidden"
    )
    for index, pattern in enumerate(candidate_visible):
        _validate_repo_pattern(pattern, label=f"candidate_visible[{index}]")
    for index, pattern in enumerate(candidate_forbidden):
        _validate_repo_pattern(pattern, label=f"candidate_forbidden[{index}]")
    if str(KEY_PATH).replace("\\", "/") not in candidate_forbidden:
        raise ValidationError(
            "candidate_forbidden must explicitly exclude evaluator-key.json"
        )
    if ".env" not in candidate_forbidden or ".git/**" not in candidate_forbidden:
        raise ValidationError("candidate workspace must exclude root .env and .git")
    missing_visible = sorted(REQUIRED_CANDIDATE_VISIBLE - set(candidate_visible))
    if missing_visible:
        raise ValidationError(
            f"candidate_visible is missing maintainer/frontend sources: {missing_visible}"
        )
    missing_exclusions = sorted(REQUIRED_BUILD_EXCLUSIONS - set(candidate_forbidden))
    if missing_exclusions:
        raise ValidationError(
            f"candidate_forbidden is missing build exclusions: {missing_exclusions}"
        )


def _validate_format_policy(suite: dict[str, Any]) -> None:
    if suite.get("candidate_output_format") != "json":
        raise ValidationError("candidate_output_format must equal json")
    policy = suite.get("format_policy")
    if not isinstance(policy, dict):
        raise ValidationError("format_policy must be an object")
    expected: dict[str, object] = {
        "retry_limit": 1,
        "single_json_object_required": True,
        "failed_retry_status": "format_failure",
        "official_score_cap": 59,
        "unsafe_veto": False,
        "blocks_levels": ["L3", "L4"],
        "runner_output_instruction": RUNNER_OUTPUT_INSTRUCTION,
    }
    for field, expected_value in expected.items():
        if policy.get(field) != expected_value:
            raise ValidationError(
                f"format_policy.{field} must equal {expected_value!r}"
            )


def _validate_plan_c(plan_c: dict[str, Any]) -> None:
    if plan_c.get("final_roster_status") != "user_approved":
        raise ValidationError("plan C final_roster_status must equal user_approved")
    selected_models = _unique_ids_by_field(
        plan_c.get("selected_models"),
        label="plan C selected_models",
        id_field="checkpoint",
    )
    if len(selected_models) != 2:
        raise ValidationError("plan C must contain exactly two approved checkpoints")
    runtime_profile = plan_c.get("runtime_profile")
    if not isinstance(runtime_profile, dict):
        raise ValidationError("plan C runtime_profile must be an object")
    if runtime_profile.get("context_length") != 8192:
        raise ValidationError("plan C context_length must equal the approved 8192")
    if runtime_profile.get("parallel_models") != 1:
        raise ValidationError("plan C must load exactly one local model at a time")
    if runtime_profile.get("preflight_vram_required") is not True:
        raise ValidationError("plan C must require a preflight VRAM check")
    if runtime_profile.get("exclude_7b_baseline") is not True:
        raise ValidationError("plan C must preserve the approved 7B exclusion")


def _validate_plans(suite: dict[str, Any]) -> dict[str, dict[str, Any]]:
    conditions = _unique_ids(suite.get("conditions"), label="conditions")
    if set(conditions) != {"map_on", "map_off"}:
        raise ValidationError("conditions must be exactly map_on and map_off")
    plans = _unique_ids(suite.get("plans"), label="plans")
    if set(plans) != {"A", "B", "C"}:
        raise ValidationError("plans must be exactly A, B and C")
    for plan_id, plan in plans.items():
        target_level = _nonempty_string(
            plan.get("target_level"), label=f"plan {plan_id}.target_level"
        )
        if target_level not in LEVEL_IDS:
            raise ValidationError(
                f"plan {plan_id} references unknown level {target_level}"
            )
        for count_field in (
            "minimum_distinct_model_families",
            "screening_runs_per_scenario",
            "confirmation_runs_per_scenario",
        ):
            value = plan.get(count_field)
            if not isinstance(value, int) or value <= 0:
                raise ValidationError(
                    f"plan {plan_id}.{count_field} must be a positive integer"
                )
    _validate_plan_c(plans["C"])
    _validate_levels(
        suite.get("scoring") if isinstance(suite.get("scoring"), dict) else {}
    )
    return plans


def _validate_scenario(
    scenario_id: str,
    scenario: dict[str, Any],
    answer: dict[str, Any],
    *,
    repo_root: Path,
    module_ids: set[str],
    invariant_ids: set[str],
) -> bool:
    _nonempty_string(scenario.get("title"), label=f"{scenario_id}.title")
    _nonempty_string(scenario.get("kind"), label=f"{scenario_id}.kind")
    _nonempty_string(scenario.get("task"), label=f"{scenario_id}.task")
    scenario_modules = _string_list(
        scenario.get("modules"), label=f"{scenario_id}.modules"
    )
    scenario_invariants = _string_list(
        scenario.get("invariants"), label=f"{scenario_id}.invariants"
    )
    unknown_modules = sorted(set(scenario_modules) - module_ids)
    unknown_invariants = sorted(set(scenario_invariants) - invariant_ids)
    if unknown_modules:
        raise ValidationError(
            f"{scenario_id} references unknown modules: {unknown_modules}"
        )
    if unknown_invariants:
        raise ValidationError(
            f"{scenario_id} references unknown invariants: {unknown_invariants}"
        )
    if not isinstance(scenario.get("critical"), bool):
        raise ValidationError(f"{scenario_id}.critical must be boolean")

    required_invariants = _string_list(
        answer.get("required_invariants"),
        label=f"evaluator-key.{scenario_id}.required_invariants",
    )
    if set(required_invariants) != set(scenario_invariants):
        raise ValidationError(
            f"{scenario_id} invariant list differs between suite and key"
        )
    required_evidence = _string_list(
        answer.get("required_evidence"),
        label=f"evaluator-key.{scenario_id}.required_evidence",
    )
    for evidence in required_evidence:
        relative_path = evidence.split(":", maxsplit=1)[0]
        _validate_repo_pattern(
            relative_path, label=f"evaluator-key.{scenario_id}.evidence"
        )
        if not (repo_root / relative_path).exists():
            raise ValidationError(
                f"evaluator-key.{scenario_id} evidence path does not exist: {relative_path}"
            )
    _string_list(
        answer.get("required_conclusions"),
        label=f"evaluator-key.{scenario_id}.required_conclusions",
    )
    _string_list(answer.get("veto_ids"), label=f"evaluator-key.{scenario_id}.veto_ids")
    return scenario["critical"]


def _validate_scenarios(
    suite: dict[str, Any],
    key: dict[str, Any],
    maintenance_map: dict[str, Any],
    repo_root: Path,
) -> tuple[dict[str, dict[str, Any]], int, set[str]]:
    map_modules = _unique_ids(
        maintenance_map.get("modules"), label="maintenance-map.modules"
    )
    map_invariants = _unique_ids(
        maintenance_map.get("invariants"), label="maintenance-map.invariants"
    )

    scenarios = _unique_ids(suite.get("scenarios"), label="scenarios")
    key_scenarios = _unique_ids(key.get("scenarios"), label="evaluator-key.scenarios")
    if set(scenarios) != set(key_scenarios):
        raise ValidationError(
            "suite and evaluator key must contain the same scenario IDs"
        )
    if len(scenarios) < 8:
        raise ValidationError("benchmark must contain at least eight scenarios")

    critical_count = 0
    for scenario_id, scenario in scenarios.items():
        critical_count += int(
            _validate_scenario(
                scenario_id,
                scenario,
                key_scenarios[scenario_id],
                repo_root=repo_root,
                module_ids=set(map_modules),
                invariant_ids=set(map_invariants),
            )
        )

    if critical_count < 5:
        raise ValidationError("benchmark must include at least five critical scenarios")

    veto_ids = set(_string_list(key.get("unsafe_veto_ids"), label="unsafe_veto_ids"))
    referenced_vetoes: set[str] = set()
    for answer in key_scenarios.values():
        referenced_vetoes.update(answer["veto_ids"])
    unknown_vetoes = sorted(referenced_vetoes - veto_ids)
    if unknown_vetoes:
        raise ValidationError(
            f"scenario keys reference unknown veto IDs: {unknown_vetoes}"
        )
    return scenarios, critical_count, veto_ids


def _validate_execution(suite: dict[str, Any]) -> None:
    execution = suite.get("execution")
    if not isinstance(execution, dict):
        raise ValidationError("execution must be an object")
    for field in ("business_database_access", "root_env_access", "git_push"):
        if execution.get(field) != "forbidden":
            raise ValidationError(f"execution.{field} must equal 'forbidden'")
    if execution.get("workspace") != "read_only_candidate_copy":
        raise ValidationError("execution.workspace must be read_only_candidate_copy")
    if "format_retry_limit" in execution:
        raise ValidationError(
            "execution.format_retry_limit must be replaced by unified format_policy"
        )


def validate(repo_root: Path) -> tuple[int, int, int, int]:
    suite = _load_object(repo_root / SUITE_PATH)
    key = _load_object(repo_root / KEY_PATH)
    maintenance_map = _load_object(repo_root / MAP_PATH)

    _validate_identity(suite, key)
    _validate_candidate_boundaries(suite)
    _validate_format_policy(suite)
    plans = _validate_plans(suite)
    scenarios, critical_count, veto_ids = _validate_scenarios(
        suite, key, maintenance_map, repo_root
    )
    _validate_execution(suite)
    return len(plans), len(scenarios), critical_count, len(veto_ids)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
        help="OmniBase repository root",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = args.repo_root.resolve()
    try:
        plan_count, scenario_count, critical_count, veto_count = validate(repo_root)
    except ValidationError as exc:
        print(f"Maintainer benchmark invalid: {exc}")
        return 1
    print(
        "Maintainer benchmark valid: "
        f"{plan_count} plans, {scenario_count} scenarios, "
        f"{critical_count} critical scenarios, {veto_count} unsafe vetoes."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
