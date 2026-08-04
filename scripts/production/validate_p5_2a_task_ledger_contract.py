#!/usr/bin/env python3
"""Validate the P5.2A Agent Task / Run / Lease / Fencing ledger contract preflight.

``--validate-only`` parses the strict offline contract without Git source
hashing, feature-gate resolution or external evidence verification and never
returns ``ready``.  ``--verify`` hashes the clean checkout, resolves the three
Phase 5 feature gates from the server environment, checks the sealed contract
digests and the P34.7/P5.0/P5.1 formal states, and proves that no P5.2 ORM,
migration, router, Planner, Executor, dispatcher, scheduler, worker or runtime
package was added.

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

_contract = importlib.import_module("omnibase.production.phase5_task_ledger_contract")
AdmissionState = _contract.AdmissionState
TaskLedgerContractError = _contract.TaskLedgerContractError
TaskLedgerContractGate = _contract.TaskLedgerContractGate
load_task_ledger_contract_config = _contract.load_task_ledger_contract_config

_FEATURE_GATE_ENV_NAMES = (
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
)

DEFAULT_CONFIG = (
    DEFAULT_REPO_ROOT / "deployment" / "production" / "phase5-task-ledger-contract.example.json"
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
        raise TaskLedgerContractError("configuration path must not contain parent traversal")
    root = repo_root.resolve(strict=True)
    try:
        relative = unresolved.relative_to(root).as_posix()
    except ValueError as exc:
        raise TaskLedgerContractError("configuration path escaped the repository") from exc
    if relative.lower() == ".env":
        raise TaskLedgerContractError("root .env is forbidden")
    candidate = root
    for part in Path(relative).parts:
        candidate = candidate / part
        metadata = os.lstat(candidate)
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise TaskLedgerContractError("configuration path contains a link or reparse point")
    metadata = os.lstat(candidate)
    if not stat.S_ISREG(metadata.st_mode):
        raise TaskLedgerContractError("configuration must be a regular non-link file")
    return candidate.resolve(strict=True)


def _reject_link_components(path: Path) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    if ".." in absolute.parts:
        raise TaskLedgerContractError("report output path must not contain parent traversal")
    candidate = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        candidate = candidate / part
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            continue
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise TaskLedgerContractError("report output path contains a link or reparse point")


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
        raise TaskLedgerContractError("report output must be outside the repository")
    if unresolved.exists():
        metadata = os.lstat(unresolved)
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
            raise TaskLedgerContractError("report output must be a regular non-link file")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_components(resolved.parent)
    resolved.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    arguments = _parse_args()
    repo_root = arguments.repo_root.resolve(strict=True)
    try:
        config = load_task_ledger_contract_config(_safe_config_path(arguments.config, repo_root))
        gate = TaskLedgerContractGate(repo_root)
        report = (
            gate.validate_only(config)
            if arguments.validate_only
            else gate.verify(config, gate_values=_server_gate_values())
        )
        payload = report.to_dict()
        if arguments.output is not None:
            _write_report(arguments.output, payload, repo_root)
    except (
        TaskLedgerContractError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
    ) as exc:
        payload = {
            "schema_version": 1,
            "gate": "P5.2A Agent Task ledger contract preflight",
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
            "blockers": [],
            "vetoes": [str(exc)],
            "migration_head": None,
            "task_ledger_orm_created": False,
            "task_ledger_migration_created": False,
            "agent_invocation_api_exposed": False,
            "agent_runtime_created": False,
            "planner_created": False,
            "executor_created": False,
            "scheduler_or_worker_started": False,
            "model_or_tool_invoked": False,
            "task_execution_activated": False,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "external_network_accessed": False,
        }
        exit_code = 1
        if arguments.output is not None:
            # The veto is already reported on stdout if the report path fails.
            with suppress(TaskLedgerContractError, OSError):
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
