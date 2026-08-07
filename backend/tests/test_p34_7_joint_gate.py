"""Tests for the run-scoped hardened P34.7 joint gate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from omnibase.production.composition import ConfigurationError
from omnibase.production.joint_gate import validate_joint_evidence


def _manifest(run: Path, name: str, content: bytes) -> dict[str, object]:
    path = run / name
    path.write_bytes(content)
    entry = {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
    raw = hashlib.sha256(json.dumps([entry], separators=(",", ":"), sort_keys=True).encode()).hexdigest()
    return {"raw_sha256": raw, "files": [entry]}


def _payload(run: Path) -> dict[str, object]:
    commands = {
        name: {"exit_code": 0, "status": "passed", "stdout": "", "stderr": ""}
        for name in ("core_runner", "runner_broker", "runner_gateway", "broker_gateway", "overlay_data_plane", "recovery_sla")
    }
    components = {
        name: {"status": "passed", "direct": True, "evidence_id": f"{name}-evidence"}
        for name in ("core", "runner", "broker", "gateway", "overlay", "recovery_sla")
    }
    return {
        "schema": "omnibase.p34-7.hardened-joint-evidence.v1",
        "run_id": "run-001",
        "environment": "production",
        "disposable": False,
        "source_manifest": _manifest(run, "source.txt", b"source"),
        "artifact_manifest": _manifest(run, "artifact.txt", b"artifact"),
        "commands": commands,
        "components": components,
        "migration_head": "0012",
        "feature_gates": {
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        "runtime_posture": {"production_runtime_activated": False, "hostile_code_executed": False},
        "attack_matrix": {"status": "passed", "results": {"revocation": "rejected"}},
        "cleanup": {"containers": 0, "networks": 0, "processes": 0, "volumes": 0},
        "evidence": {"status": "passed"},
    }


def test_joint_gate_requires_all_direct_prerequisites(tmp_path: Path) -> None:
    report = validate_joint_evidence(tmp_path, _payload(tmp_path))
    assert report.passed is True
    assert report.status == "passed"


def test_joint_gate_keeps_missing_command_blocked(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    payload["commands"]["overlay_data_plane"]["status"] = "not_proven"
    report = validate_joint_evidence(tmp_path, payload)
    assert report.status == "blocked/not_proven"
    assert "command:overlay_data_plane" in report.blockers


@pytest.mark.parametrize("head", ["0011", "0013"])
def test_joint_gate_rejects_wrong_migration_head(tmp_path: Path, head: str) -> None:
    payload = _payload(tmp_path)
    payload["migration_head"] = head
    with pytest.raises(ConfigurationError, match="migration head"):
        validate_joint_evidence(tmp_path, payload)


def test_joint_gate_rejects_enabled_feature_gate(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    payload["feature_gates"]["agent_runtime_enabled"] = True
    with pytest.raises(ConfigurationError, match="feature gates"):
        validate_joint_evidence(tmp_path, payload)


def test_joint_gate_rejects_cleanup_residue(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    payload["cleanup"]["processes"] = 1
    with pytest.raises(ConfigurationError, match="cleanup residue"):
        validate_joint_evidence(tmp_path, payload)


def test_joint_gate_rejects_env_manifest_path(tmp_path: Path) -> None:
    payload = _payload(tmp_path)
    payload["source_manifest"]["files"][0]["path"] = ".env"
    with pytest.raises(ConfigurationError, match=".env"):
        validate_joint_evidence(tmp_path, payload)
