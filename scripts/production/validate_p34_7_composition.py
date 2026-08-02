#!/usr/bin/env python3
"""Validate the P34.7A/B production provenance and composition contract.

``--validate-only`` performs no Git source hashing and does not verify external
evidence.  ``--verify`` hashes the clean checkout and verifies every evidence
reference marked ``passed``.  Neither mode starts services, reads secrets,
opens a database, runs a migration or executes hostile code.
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

_composition = importlib.import_module("omnibase.production.composition")
AdmissionState = _composition.AdmissionState
ConfigurationError = _composition.ConfigurationError
ProductionCompositionGate = _composition.ProductionCompositionGate
load_production_composition_config = _composition.load_production_composition_config

DEFAULT_CONFIG = REPO_ROOT / "deployment" / "production" / "composition.example.json"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--verify", action="store_true")
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON report path; stdout is always emitted",
    )
    return parser.parse_args()


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
        config = load_production_composition_config(_safe_config_path(arguments.config))
        gate = ProductionCompositionGate(REPO_ROOT)
        report = gate.validate_only(config) if arguments.validate_only else gate.verify(config)
        payload = report.to_dict()
    except (
        ConfigurationError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
    ) as exc:
        payload = {
            "schema_version": 1,
            "gate": "P34.7A/B production provenance and composition admission",
            "state": AdmissionState.INVALID.value,
            "activation_allowed": False,
            "blockers": [],
            "vetoes": [str(exc)],
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "hostile_code_executed": False,
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
