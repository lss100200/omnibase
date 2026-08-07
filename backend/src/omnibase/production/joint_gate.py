"""Fail-closed run-scoped evidence contract for hardened P34.7 joint gates.

The validator is deliberately offline: it reads only an operator-supplied run
directory and never starts services, opens a network connection, loads ``.env`` or
accesses a database.  It validates direct evidence fields independently rather
than deriving them from an aggregate pass flag.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from omnibase.production.composition import ConfigurationError

_SCHEMA = "omnibase.p34-7.hardened-joint-evidence.v1"
_SHA256 = set("0123456789abcdef")
_REQUIRED_COMPONENTS = {"core", "runner", "broker", "gateway", "overlay", "recovery_sla"}
_REQUIRED_COMMANDS = {
    "core_runner",
    "runner_broker",
    "runner_gateway",
    "broker_gateway",
    "overlay_data_plane",
    "recovery_sla",
}


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ConfigurationError(f"{name} must be an object")
    return value


def _keys(value: dict[str, Any], allowed: set[str], name: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise ConfigurationError(f"{name} has unexpected fields: {', '.join(extra)}")


def _sha(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in _SHA256 for c in value):
        raise ConfigurationError(f"{name} must be a lowercase SHA-256")
    return value


def _relative(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{name} must be a path")
    path = PurePosixPath(value.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ConfigurationError(f"{name} must be normalized and relative")
    if path.as_posix().lower() in {".env", "./.env"} or path.name.lower() == ".env":
        raise ConfigurationError(f"{name} must not reference .env")
    return path.as_posix()


def _file(root: Path, relative: str, name: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        metadata = os.lstat(candidate)
    except OSError as exc:
        raise ConfigurationError(f"{name} is unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError(f"{name} must be a regular non-link file")
    return candidate


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True, slots=True)
class JointGateReport:
    status: str
    run_id: str
    source_manifest_sha256: str
    artifact_manifest_sha256: str
    blockers: tuple[str, ...]
    vetoes: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "status": self.status,
            "passed": self.passed,
            "run_id": self.run_id,
            "source_manifest_sha256": self.source_manifest_sha256,
            "artifact_manifest_sha256": self.artifact_manifest_sha256,
            "blockers": list(self.blockers),
            "vetoes": list(self.vetoes),
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "runtime_activated": False,
        }


def _verify_manifest(root: Path, value: object, *, name: str) -> str:
    manifest = _object(value, name)
    _keys(manifest, {"raw_sha256", "files"}, name)
    raw = _sha(manifest.get("raw_sha256"), f"{name}.raw_sha256")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise ConfigurationError(f"{name}.files must be non-empty")
    seen: set[str] = set()
    for index, item in enumerate(files):
        entry = _object(item, f"{name}.files[{index}]")
        _keys(entry, {"path", "size", "sha256"}, f"{name}.files[{index}]")
        relative = _relative(entry.get("path"), f"{name}.files[{index}].path")
        if relative in seen:
            raise ConfigurationError(f"{name} contains duplicate file paths")
        seen.add(relative)
        if not isinstance(entry.get("size"), int) or entry["size"] < 0:
            raise ConfigurationError(f"{name}.files[{index}].size is invalid")
        digest = _sha(entry.get("sha256"), f"{name}.files[{index}].sha256")
        path = _file(root, relative, f"{name}.files[{index}]")
        if path.stat().st_size != entry["size"] or _hash_file(path) != digest:
            raise ConfigurationError(f"{name} raw file hash or size drifted")
    canonical = json.dumps(files, separators=(",", ":"), sort_keys=True).encode()
    if hashlib.sha256(canonical).hexdigest() != raw:
        raise ConfigurationError(f"{name}.raw_sha256 does not bind files")
    return raw


def validate_joint_evidence(run_dir: Path, payload: object) -> JointGateReport:
    """Validate one immutable, run-scoped P34.7 evidence bundle."""
    root = run_dir.resolve(strict=True)
    data = _object(payload, "joint evidence")
    _keys(
        data,
        {
            "schema", "run_id", "environment", "disposable", "source_manifest",
            "artifact_manifest", "commands", "components", "migration_head",
            "feature_gates", "runtime_posture", "attack_matrix", "cleanup", "evidence",
        },
        "joint evidence",
    )
    if data.get("schema") != _SCHEMA or data.get("environment") != "production":
        raise ConfigurationError("joint evidence must be production schema v1")
    if data.get("disposable") is not False:
        raise ConfigurationError("disposable evidence cannot prove production")
    run_id = data.get("run_id")
    if not isinstance(run_id, str) or not run_id or "/" in run_id or "\\" in run_id:
        raise ConfigurationError("run_id must be a non-empty opaque name")
    source_hash = _verify_manifest(root, data.get("source_manifest"), name="source_manifest")
    artifact_hash = _verify_manifest(root, data.get("artifact_manifest"), name="artifact_manifest")

    commands = _object(data.get("commands"), "commands")
    if set(commands) != _REQUIRED_COMMANDS:
        raise ConfigurationError("commands must contain every direct joint boundary")
    blockers: list[str] = []
    for name, value in commands.items():
        command = _object(value, f"commands.{name}")
        _keys(command, {"exit_code", "status", "stdout", "stderr"}, f"commands.{name}")
        if not isinstance(command.get("exit_code"), int):
            raise ConfigurationError(f"commands.{name}.exit_code is invalid")
        if command.get("status") != "passed" or command["exit_code"] != 0:
            blockers.append(f"command:{name}")

    components = _object(data.get("components"), "components")
    if set(components) != _REQUIRED_COMPONENTS:
        raise ConfigurationError("components must contain all six joint gates")
    for name, value in components.items():
        component = _object(value, f"components.{name}")
        _keys(component, {"status", "direct", "evidence_id"}, f"components.{name}")
        if component.get("status") != "passed" or component.get("direct") is not True:
            blockers.append(f"component:{name}")

    if data.get("migration_head") != "0012":
        raise ConfigurationError("migration head must remain 0012")
    gates = _object(data.get("feature_gates"), "feature_gates")
    if set(gates) != {"agent_runtime_enabled", "agent_planner_enabled", "multi_agent_enabled"}:
        raise ConfigurationError("feature_gates must contain exactly the three Phase 5 gates")
    if any(value is not False for value in gates.values()):
        raise ConfigurationError("Phase 5 feature gates must remain false")
    posture = _object(data.get("runtime_posture"), "runtime_posture")
    _keys(posture, {"production_runtime_activated", "hostile_code_executed"}, "runtime_posture")
    if any(posture.get(key) is not False for key in posture):
        raise ConfigurationError("production Runtime and hostile code must remain inactive")

    attack = _object(data.get("attack_matrix"), "attack_matrix")
    _keys(attack, {"status", "results"}, "attack_matrix")
    if attack.get("status") != "passed" or not isinstance(attack.get("results"), dict):
        blockers.append("attack_matrix")
    cleanup = _object(data.get("cleanup"), "cleanup")
    if any(cleanup.get(key) != 0 for key in ("containers", "networks", "processes", "volumes")):
        raise ConfigurationError("cleanup residue is a veto")
    evidence = _object(data.get("evidence"), "evidence")
    if evidence.get("status") != "passed":
        blockers.append("evidence_seal")
    status = "blocked/not_proven" if blockers else "passed"
    return JointGateReport(status, run_id, source_hash, artifact_hash, tuple(sorted(blockers)), ())


__all__ = ["JointGateReport", "validate_joint_evidence"]


_SCHEMA_VERSION = _SCHEMA
