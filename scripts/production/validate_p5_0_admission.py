#!/usr/bin/env python3
"""Validate the P5.0 Phase 5 admission contract.

``--validate-only`` parses the contract without Git source hashing or external
evidence verification.  ``--verify`` hashes the clean checkout, resolves the
three Phase 5 feature gates from the server environment (with optional
``--gate`` overrides) and verifies every manifest reference.

Neither mode starts an Agent, Planner, Executor, queue, worker, scheduler or
service, opens a database, runs a migration or executes hostile code, and
neither reads the root ``.env``.  Exit codes are ``0`` for a valid static
contract or a formally ready admission, ``2`` for ``blocked/not_proven`` and
``1`` for an invalid contract or a safety veto.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_SRC = REPO_ROOT / "backend" / "src"
sys.path.insert(0, str(BACKEND_SRC))

_admission = importlib.import_module("omnibase.production.phase5_admission")
AdmissionState = _admission.AdmissionState
ConfigurationError = _admission.ConfigurationError
FEATURE_GATE_ENV_NAMES = _admission.FEATURE_GATE_ENV_NAMES
Phase5AdmissionGate = _admission.Phase5AdmissionGate
load_phase5_admission_config = _admission.load_phase5_admission_config

DEFAULT_CONFIG = REPO_ROOT / "deployment" / "production" / "phase5-admission.example.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--gate",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help=(
            "override one Phase 5 feature gate (repeatable), e.g. "
            "--gate AGENT_RUNTIME_ENABLED=false"
        ),
    )
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON report path; stdout is always emitted",
    )
    return parser.parse_args()


def _parse_gate_overrides(raw: list[str]) -> dict[str, str]:
    allowed = set(FEATURE_GATE_ENV_NAMES.values())
    overrides: dict[str, str] = {}
    for item in raw:
        if "=" not in item:
            raise ConfigurationError("--gate must use NAME=VALUE form")
        name, value = item.split("=", 1)
        if name not in allowed:
            raise ConfigurationError(f"unknown feature gate override: {name}")
        if name in overrides:
            raise ConfigurationError(f"duplicate feature gate override: {name}")
        overrides[name] = value
    return overrides


def _server_gate_values() -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in FEATURE_GATE_ENV_NAMES.values()}


def _safe_config_path(path: Path) -> Path:
    resolved = path.resolve(strict=True)
    try:
        relative = resolved.relative_to(REPO_ROOT.resolve(strict=True)).as_posix()
    except ValueError as exc:
        raise ConfigurationError("configuration path escaped the repository") from exc
    if relative.lower() == ".env":
        raise ConfigurationError("root .env is forbidden")
    return resolved


def _write_report(path: Path, report: dict[str, object]) -> None:
    resolved = path.resolve()
    if resolved == (REPO_ROOT / ".env").resolve():
        raise ConfigurationError("report output must never overwrite the root .env")
    if resolved.exists():
        metadata = os.lstat(resolved)
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
            raise ConfigurationError("report output must be a regular non-link file")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    arguments = _parse_args()
    try:
        gate_overrides = _parse_gate_overrides(arguments.gate)
        config = load_phase5_admission_config(_safe_config_path(arguments.config))
        gate = Phase5AdmissionGate(REPO_ROOT)
        if arguments.validate_only:
            report = gate.validate_only(config)
        else:
            gate_values = {**_server_gate_values(), **gate_overrides}
            report = gate.verify(config, gate_values=gate_values)
        payload = report.to_dict()
    except (
        ConfigurationError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
    ) as exc:
        payload = {
            "schema_version": 1,
            "gate": "P5.0 Phase 5 admission gate",
            "state": AdmissionState.INVALID.value,
            "activation_allowed": False,
            "feature_gates": {
                "agent_runtime_enabled": False,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            },
            "p34_7_formal_state": AdmissionState.INVALID.value,
            "blockers": [],
            "vetoes": [str(exc)],
            "migration_head": None,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "hostile_code_executed": False,
            "phase5_runtime_activated": False,
        }
        exit_code = 1
    else:
        if arguments.validate_only or report.state is AdmissionState.READY:
            exit_code = 0
        elif report.state is AdmissionState.BLOCKED:
            exit_code = 2
        else:
            exit_code = 1
    if arguments.output is not None:
        _write_report(arguments.output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
