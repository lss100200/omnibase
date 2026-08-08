#!/usr/bin/env python3
"""Validate the P5.3A Planner Proposal contract preflight.

``--validate-only`` parses the strict offline contract without Git source
hashing, feature-gate resolution or external evidence verification and never
returns ``ready``.  ``--verify`` hashes the clean checkout, resolves the three
Phase 5 feature gates from the server environment, checks the sealed contract
digests and the P34.7/P5.0/P5.1/P5.2A formal states, and proves that no P5.3
Planner Runtime, Executor, dispatcher, scheduler, worker or runtime package
was added.

Neither mode reads the root ``.env``, a credential, a database, a migration or
the network, and neither starts an Agent, Planner, Executor, queue, worker,
scheduler, polling loop or background coroutine.  Exit codes are ``0`` for a
valid static contract, ``2`` for ``blocked/not_proven`` and ``1`` for an
invalid contract or a safety veto.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import stat
import sys
from contextlib import suppress
from pathlib import Path

DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = DEFAULT_REPO_ROOT / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

_contract = importlib.import_module("omnibase.production.phase5_planner_contract")
AdmissionState = _contract.AdmissionState
PlannerContractError = _contract.PlannerContractError
PlannerContractGate = _contract.PlannerContractGate
load_planner_contract_config = _contract.load_planner_contract_config

_FEATURE_GATE_ENV_NAMES = (
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
)

DEFAULT_CONFIG = (
    DEFAULT_REPO_ROOT / "deployment" / "production" / "phase5-planner-contract.example.json"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=DEFAULT_REPO_ROOT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON report path OUTSIDE the repository; stdout is always emitted",
    )
    return parser.parse_args()


def _safe_config_path(path: Path, repo_root: Path) -> Path:
    unresolved = path if path.is_absolute() else Path.cwd() / path
    if ".." in unresolved.parts:
        raise PlannerContractError("configuration path must not contain parent traversal")
    root = repo_root.resolve(strict=True)
    try:
        relative = unresolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise PlannerContractError("configuration path escaped the repository") from exc
    if relative.lower() == ".env":
        raise PlannerContractError("root .env is forbidden")
    candidate = root
    for part in Path(relative).parts:
        candidate = candidate / part
        metadata = os.lstat(candidate)
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise PlannerContractError("configuration path contains a link or reparse point")
    metadata = os.lstat(candidate)
    if not stat.S_ISREG(metadata.st_mode):
        raise PlannerContractError("configuration must be a regular non-link file")
    return candidate.resolve(strict=True)


def _reject_link_components(path: Path) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    if ".." in absolute.parts:
        raise PlannerContractError("report output path must not contain parent traversal")
    candidate = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        candidate = candidate / part
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            continue
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise PlannerContractError("report output path contains a link or reparse point")


def _server_gate_values() -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in _FEATURE_GATE_ENV_NAMES}


def _write_report(path: Path, report: dict[str, object], repo_root: Path) -> None:
    unresolved = path if path.is_absolute() else Path.cwd() / path
    _reject_link_components(unresolved)
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(repo_root.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise PlannerContractError("report output must be outside the repository")
    if unresolved.exists():
        metadata = os.lstat(unresolved)
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
            raise PlannerContractError("report output must be a regular non-link file")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_components(resolved.parent)
    resolved.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    arguments = _parse_args()
    repo_root = arguments.repo_root.resolve(strict=True)
    try:
        config = load_planner_contract_config(_safe_config_path(arguments.config, repo_root))
        gate = PlannerContractGate(repo_root)
        report = (
            gate.validate_only(config)
            if arguments.validate_only
            else gate.verify(config, gate_values=_server_gate_values())
        )
        payload = report.to_dict()
        if arguments.output is not None:
            _write_report(arguments.output, payload, repo_root)
    except (
        PlannerContractError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
    ) as exc:
        payload = {
            "schema_version": 1,
            "gate": "P5.3A Planner Proposal contract preflight",
            "state": AdmissionState.INVALID.value,
            "activation_allowed": False,
            "contract_valid": False,
            "feature_gates": {
                "agent_runtime_enabled": False,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            },
            "p34_7_formal_state": AdmissionState.INVALID.value,
            "p5_0_formal_state": AdmissionState.INVALID.value,
            "p5_1_formal_state": AdmissionState.INVALID.value,
            "p5_2a_formal_state": AdmissionState.INVALID.value,
            "blockers": [],
            "vetoes": [str(exc)],
            "migration_head": None,
            "planner_runtime_created": False,
            "planner_execution_activated": False,
            "dag_execution_allowed": False,
            "planner_validation_results": [],
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "external_network_accessed": False,
            "model_or_tool_invoked": False,
            "agent_runtime_activated": False,
            "executor_activated": False,
            "worker_or_scheduler_started": False,
        }
        exit_code = 1
        if arguments.output is not None:
            with suppress(PlannerContractError, OSError):
                _write_report(arguments.output, payload, repo_root)
    else:
        if arguments.validate_only or report.state is AdmissionState.READY:
            exit_code = 0
        elif report.state is AdmissionState.BLOCKED:
            exit_code = 2
        else:
            exit_code = 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
