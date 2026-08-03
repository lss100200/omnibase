#!/usr/bin/env python3
"""Validate the P5.1A Agent Registry offline contract preflight.

``--validate-only`` parses the strict contract without Git source hashing,
feature-gate resolution or external evidence verification and never returns
``ready``.  ``--verify`` hashes the clean checkout, resolves the three Phase 5
feature gates from the server environment, checks the sealed contract digests
and the P5.0/P34.7 formal states, and proves that no ORM, migration, router,
Celery task or runtime package was added.

Neither mode reads the root ``.env``, a credential, a database, a migration or
the network, and neither starts an Agent, Planner, Executor, queue, worker or
scheduler.  Exit codes are ``0`` for a valid static contract, ``2`` for
``blocked/not_proven`` and ``1`` for an invalid contract or a safety veto.
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

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

_contract = importlib.import_module("omnibase.production.phase5_registry_contract")
AdmissionState = _contract.AdmissionState
RegistryContractError = _contract.RegistryContractError
RegistryContractGate = _contract.RegistryContractGate
load_registry_contract_config = _contract.load_registry_contract_config

DEFAULT_CONFIG = REPO_ROOT / "deployment" / "production" / "phase5-registry-contract.example.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
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


def _safe_config_path(path: Path) -> Path:
    unresolved = path if path.is_absolute() else Path.cwd() / path
    metadata = os.lstat(unresolved)
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise RegistryContractError("configuration must be a regular non-link file")
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(REPO_ROOT.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise RegistryContractError("configuration path escaped the repository") from exc
    if relative.lower() == ".env":
        raise RegistryContractError("root .env is forbidden")
    return resolved


def _write_report(path: Path, report: dict[str, object]) -> None:
    resolved = path.resolve()
    try:
        resolved.relative_to(REPO_ROOT.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise RegistryContractError("report output must be outside the repository")
    if resolved.exists():
        metadata = os.lstat(resolved)
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
            raise RegistryContractError("report output must be a regular non-link file")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    arguments = _parse_args()
    try:
        config = load_registry_contract_config(_safe_config_path(arguments.config))
        gate = RegistryContractGate(REPO_ROOT)
        report = gate.validate_only(config) if arguments.validate_only else gate.verify(config)
        payload = report.to_dict()
        if arguments.output is not None:
            _write_report(arguments.output, payload)
    except (
        RegistryContractError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
    ) as exc:
        payload = {
            "schema_version": 1,
            "gate": "P5.1A Agent Registry contract preflight",
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
            "blockers": [],
            "vetoes": [str(exc)],
            "migration_head": None,
            "registry_runtime_implemented": False,
            "database_schema_applied": False,
            "public_api_exposed": False,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "external_network_accessed": False,
            "agent_registry_runtime_created": False,
            "agent_api_exposed": False,
            "agent_runtime_activated": False,
            "planner_activated": False,
            "executor_activated": False,
            "worker_or_scheduler_started": False,
        }
        exit_code = 1
        if arguments.output is not None:
            # The veto is already reported on stdout if the report path fails.
            with suppress(RegistryContractError, OSError):
                _write_report(arguments.output, payload)
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
