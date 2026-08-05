"""Unit tests for the P5.2C Agent Alpha disposable Gate wrapper."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).with_name("run_p5_2c_agent_alpha_disposable_gate.py")
_SPEC = importlib.util.spec_from_file_location("p5_2c_gate", _SCRIPT)
assert _SPEC is not None
assert _SPEC.loader is not None
gate = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gate)


def test_static_manifest_is_closed_and_contains_no_root_env() -> None:
    manifest = gate._manifest()
    files = manifest["files"]
    assert isinstance(files, dict)
    assert manifest["file_count"] == len(files)
    assert "backend/src/omnibase/agent_alpha/engineering.py" in files
    assert "backend/src/omnibase/agent_alpha/adapters.py" in files
    assert "backend/tests/integration/test_p5_2c_agent_alpha_foundation.py" in files
    assert ".env" not in files
    assert all(not str(path).startswith(".tmp/") for path in files)


def test_manifest_digest_is_deterministic() -> None:
    manifest = gate._manifest()
    assert gate._manifest_digest(manifest) == gate._manifest_digest(
        json.loads(json.dumps(manifest))
    )
    assert len(gate._manifest_digest(manifest)) == 64


def test_recorded_evidence_rejects_production_activation(tmp_path: Path) -> None:
    report = {
        "schema_version": 1,
        "gate": gate.GATE_NAME,
        "passed": True,
        "engineering_alpha_ready": True,
        "production_runtime_activated": True,
        "feature_gates_enabled": False,
        "migration_head": "0011",
        "database_sentinel_verified": True,
        "model_identity_verified": True,
        "tool_free_verified": True,
        "tenant_scope_verified": True,
        "cancellation_scope_verified": True,
        "unknown_no_replay_verified": True,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "external_network_accessed": False,
        "cleanup": {"containers": 0, "networks": 0, "volumes": 0},
        "source_manifest_sha256": gate._manifest_digest(gate._manifest()),
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    original_root = gate.REPO_ROOT
    try:
        gate.REPO_ROOT = tmp_path
        with pytest.raises(RuntimeError, match="production_runtime_activated"):
            gate._verify_evidence(path)
    finally:
        gate.REPO_ROOT = original_root


def test_cleanup_requires_all_three_resource_classes_zero(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        gate,
        "_compose",
        lambda *_args, **_kwargs: type("Result", (), {"returncode": 0, "stdout": ""})(),
    )
    monkeypatch.setattr(
        gate,
        "_resource_counts",
        lambda _project: {"containers": 0, "networks": 1, "volumes": 0},
    )
    with pytest.raises(RuntimeError, match="cleanup failed"):
        gate._cleanup("omnibase-p52c-test", {})
