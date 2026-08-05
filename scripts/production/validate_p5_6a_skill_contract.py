#!/usr/bin/env python3
"""Validate the compile-only P5.6A first-party native Skill contract.

The command parses and verifies immutable SkillDefinition/SkillVersion
manifests only. It never reads the root .env, connects to a database or
provider, installs a Skill, executes a verification command, starts an Agent,
or enables Runtime, Planner, multi-Agent, MCP, Marketplace or script execution.

Exit codes: 0 for valid ``--validate-only``; 2 for the expected
``blocked/not_proven`` verify result; 1 for an invalid contract or safety veto.
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
sys.path.insert(0, str(DEFAULT_REPO_ROOT / "backend" / "src"))

_contract = importlib.import_module("omnibase.production.phase5_skill_contract")
AdmissionState = _contract.AdmissionState
ConfigurationError = _contract.ConfigurationError
SkillContractGate = _contract.SkillContractGate
load_skill_contract_config = _contract.load_skill_contract_config

DEFAULT_CONFIG = (
    DEFAULT_REPO_ROOT / "deployment" / "production" / "phase5-skill-contract.example.json"
)
_FEATURE_GATE_ENV_NAMES = (
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
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
        help="optional JSON report path outside the repository; stdout is always emitted",
    )
    return parser.parse_args()


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


def _safe_config_path(path: Path, repo_root: Path) -> Path:
    unresolved = path if path.is_absolute() else Path.cwd() / path
    if ".." in unresolved.parts:
        raise ConfigurationError("configuration path must not contain parent traversal")
    root = repo_root.resolve(strict=True)
    try:
        relative = unresolved.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError("configuration path escaped the repository") from exc
    if relative.as_posix().lower() == ".env":
        raise ConfigurationError("root .env is forbidden")
    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        metadata = os.lstat(candidate)
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise ConfigurationError("configuration path contains a link or reparse point")
    metadata = os.lstat(candidate)
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError("configuration must be a regular non-link file")
    return candidate.resolve(strict=True)


def _reject_link_components(path: Path) -> None:
    absolute = path if path.is_absolute() else Path.cwd() / path
    if ".." in absolute.parts:
        raise ConfigurationError("report output path must not contain parent traversal")
    candidate = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        candidate = candidate / part
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise ConfigurationError("report output path contains a link or reparse point")


def _write_report(path: Path, payload: dict[str, object], repo_root: Path) -> None:
    unresolved = path if path.is_absolute() else Path.cwd() / path
    _reject_link_components(unresolved)
    resolved = unresolved.resolve()
    try:
        resolved.relative_to(repo_root.resolve(strict=True))
    except ValueError:
        pass
    else:
        raise ConfigurationError("report output must be outside the repository")
    if unresolved.exists():
        metadata = os.lstat(unresolved)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or _is_reparse(metadata)
            or not stat.S_ISREG(metadata.st_mode)
        ):
            raise ConfigurationError("report output must be a regular non-link file")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    _reject_link_components(resolved.parent)
    resolved.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _gate_values() -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in _FEATURE_GATE_ENV_NAMES}


def _invalid_payload(error: Exception) -> dict[str, object]:
    return {
        "schema_version": 1,
        "gate": "P5.6A native Skill contract admission",
        "state": AdmissionState.INVALID.value,
        "contract_valid": False,
        "activation_allowed": False,
        "feature_gates": {
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        "blockers": [],
        "vetoes": [str(error)],
        "skill_runtime_created": False,
        "skill_installation_executed": False,
        "mcp_enabled": False,
        "third_party_marketplace_enabled": False,
        "migration_created": False,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "external_network_accessed": False,
    }


def main() -> int:
    arguments = _parse_args()
    repo_root = arguments.repo_root.resolve(strict=True)
    try:
        config = load_skill_contract_config(_safe_config_path(arguments.config, repo_root))
        gate = SkillContractGate(repo_root)
        report = (
            gate.validate_only(config)
            if arguments.validate_only
            else gate.verify(config, gate_values=_gate_values())
        )
        payload = report.to_dict()
        if arguments.output is not None:
            _write_report(arguments.output, payload, repo_root)
    except (ConfigurationError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
        payload = _invalid_payload(exc)
        exit_code = 1
        if arguments.output is not None:
            with suppress(ConfigurationError, OSError):
                _write_report(arguments.output, payload, repo_root)
    else:
        if arguments.validate_only:
            exit_code = 0
        elif report.state is AdmissionState.BLOCKED:
            exit_code = 2
        else:
            exit_code = 1
    print(json.dumps(payload, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
