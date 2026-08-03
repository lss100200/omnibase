"""Non-destructive contract tests for the P5.1B disposable Gate wrapper."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.production import run_p5_1b_registry_disposable_gate as gate


def test_static_contract_accepts_the_checked_in_tree() -> None:
    gate._validate_static_contract()


def test_source_manifest_is_stable_and_never_contains_root_env(monkeypatch) -> None:
    def fake_run(
        arguments: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CompletedProcess[str]:
        assert check is False
        if arguments[:2] == ["git", "status"]:
            output = ""
        else:
            raise AssertionError(arguments)
        return CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(gate, "_run", fake_run)
    first = gate._source_manifest()
    second = gate._source_manifest()

    assert first == second
    assert first["repository_clean"] is True
    assert first["dirty_paths"] == ()
    files = first["files"]
    assert isinstance(files, dict)
    assert ".env" not in files
    assert "backend/src/omnibase/agent_registry/service.py" in files
    assert "backend/src/omnibase/migrations/versions/0010_p5_1b_agent_registry.py" in files
    assert "backend/tests/integration/test_p5_1b_agent_registry_foundation.py" in files
    assert "scripts/production/run_p5_1b_registry_disposable_gate.py" in files
    assert all(len(str(digest)) == 64 for digest in files.values())


def test_dirty_checkout_is_recorded_and_rejected_by_run_flow(monkeypatch) -> None:
    def fake_run(
        arguments: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CompletedProcess[str]:
        assert check is False
        if arguments[:2] == ["git", "status"]:
            output = " M backend/src/omnibase/agent_registry/service.py\n"
        else:
            raise AssertionError(arguments)
        return CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(gate, "_run", fake_run)
    manifest = gate._source_manifest()
    assert manifest["repository_clean"] is False
    assert any("service.py" in item for item in manifest["dirty_paths"])


def test_manifest_sha256_is_canonical_and_deterministic() -> None:
    first = gate._manifest_sha256({"b": 2, "a": [1, 2], "c": {"x": "y"}})
    second = gate._manifest_sha256({"c": {"x": "y"}, "a": [1, 2], "b": 2})
    assert first == second
    assert len(first) == 64


def test_verify_evidence_rejects_drifted_source(monkeypatch, tmp_path: Path) -> None:
    def fake_run(
        arguments: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CompletedProcess[str]:
        assert check is False
        if arguments[:2] == ["git", "status"]:
            output = ""
        else:
            raise AssertionError(arguments)
        return CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(gate, "_run", fake_run)
    manifest = gate._source_manifest()
    evidence = {
        "schema_version": 1,
        "gate": "P5.1B Agent Registry persistence disposable Gate",
        "passed": True,
        "root_env_accessed": False,
        "business_database_migrated": False,
        "manifest_sha256": gate._manifest_sha256(manifest),
    }
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    gate._verify_recorded_evidence(json.loads(path.read_text(encoding="utf-8")))

    drifted = dict(evidence)
    drifted["manifest_sha256"] = "0" * 64
    drifted_path = tmp_path / "drifted.json"
    drifted_path.write_text(json.dumps(drifted), encoding="utf-8")
    with pytest.raises(RuntimeError, match="source manifest drifted"):
        gate._verify_recorded_evidence(json.loads(drifted_path.read_text(encoding="utf-8")))


def test_verify_evidence_rejects_unsafe_claims(monkeypatch, tmp_path: Path) -> None:
    def fake_run(
        arguments: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CompletedProcess[str]:
        assert check is False
        if arguments[:2] == ["git", "status"]:
            output = ""
        else:
            raise AssertionError(arguments)
        return CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(gate, "_run", fake_run)
    manifest = gate._source_manifest()
    evidence = {
        "schema_version": 1,
        "gate": "P5.1B Agent Registry persistence disposable Gate",
        "passed": True,
        "root_env_accessed": True,
        "business_database_migrated": False,
        "manifest_sha256": gate._manifest_sha256(manifest),
    }
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(RuntimeError, match="root .env"):
        gate._verify_recorded_evidence(json.loads(path.read_text(encoding="utf-8")))
