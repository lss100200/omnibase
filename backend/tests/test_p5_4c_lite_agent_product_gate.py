"""Fail-closed unit tests for the P5.4C Lite Agent product disposable Gate."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

_RUNNER_RELATIVE = Path("scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py")
RUNNER = next(
    (
        candidate
        for candidate in (
            Path(__file__).resolve().parents[2] / _RUNNER_RELATIVE,
            Path("/workspace") / _RUNNER_RELATIVE,
        )
        if candidate.is_file()
    ),
    None,
)
if RUNNER is None:
    pytest.skip("P5.4C Gate tests require a full-repository mount", allow_module_level=True)


def _load_runner() -> ModuleType:
    assert RUNNER is not None
    spec = importlib.util.spec_from_file_location("p5_4c_gate", RUNNER)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_runner()


def _synthetic_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    evidence_root = tmp_path / "gate-p5-4c"
    monkeypatch.setattr(gate, "EVIDENCE_ROOT", evidence_root)
    run_id = "20260807T010203000000Z-abcdef123456"
    run_dir = evidence_root / run_id
    run_dir.mkdir(parents=True)
    manifest = gate._manifest()
    manifest_sha = gate._write_json(run_dir / "source-manifest.json", manifest)
    gate._write_bytes(run_dir / "source-manifest.sha256", f"{manifest_sha}\n".encode())
    command = gate._container_command("python", "-m", "pytest", gate.LITE_UNIT_TEST, "-q")
    result = subprocess.CompletedProcess(command, 0, "20 passed in 1.23s\n")
    commands = [gate._record_command(run_dir, "lite-unit-suite", command, result)]
    measurements = {
        "lite_unit_summary": {"passed": 20, "skipped": 0},
        "migration_head": gate.EXPECTED_MIGRATION_HEAD,
        "lite_gate_default_off": True,
        "knowledge_search_read_only_gated": True,
        "formal_builder_named": True,
    }
    report = gate._write_report(
        run_dir,
        run_id=run_id,
        started_at="2026-08-07T01:02:03+00:00",
        passed=True,
        manifest=manifest,
        manifest_raw_sha=manifest_sha,
        commands=commands,
        measurements=measurements,
        cleanup={"files_removed": 0},
        error=None,
    )
    return run_dir / "evidence.json", report


def _rewrite_evidence(path: Path, report: dict) -> None:
    digest = gate._write_json(path, report)
    gate._write_bytes(path.with_name("evidence.sha256"), f"{digest}\n".encode())


def test_source_closure_excludes_secrets_and_env() -> None:
    paths = set(gate.SOURCE_FILES)
    assert ".env" not in paths
    assert "backend/src/omnibase/agent_alpha/lite.py" in paths
    assert "backend/src/omnibase/agent_executor/engineering.py" in paths
    assert "scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py" in paths


def test_container_command_uses_env_file_and_closed_gates() -> None:
    command = gate._container_command("python", "-m", "pytest")
    assert command[:4] == ["docker", "compose", "--env-file", str(gate.ENV_FILE)]
    assert "run" in command
    assert "--rm" in command
    assert "--no-deps" in command
    assert "AGENT_LITE_ENGINEERING_ENABLED=false" in command
    assert "P5_4B_ENGINEERING_ENABLED=false" in command
    assert "AGENT_RUNTIME_ENABLED=false" in command
    assert "AGENT_PLANNER_ENABLED=false" in command
    assert "MULTI_AGENT_ENABLED=false" in command
    # No root .env leak through the workload command.
    assert all(item != ".env" for item in command)


def test_manifest_seals_only_declared_regular_files() -> None:
    manifest = gate._manifest()
    assert manifest["schema_version"] == 1
    assert manifest["file_count"] == len(gate.SOURCE_FILES)
    for relative, metadata in manifest["files"].items():
        assert relative in gate.SOURCE_FILES
        assert metadata["size"] > 0
        assert len(metadata["sha256"]) == 64


def test_test_summary_parser_requires_passed_count() -> None:
    assert gate._parse_test_summary("14 passed in 6.77s\n") == {"passed": 14, "skipped": 0}
    assert gate._parse_test_summary("14 passed, 2 skipped in 6.77s\n") == {
        "passed": 14,
        "skipped": 2,
    }
    with pytest.raises(RuntimeError):
        gate._parse_test_summary("no summary here")


def test_synthetic_sealed_run_verifies(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence, _ = _synthetic_run(tmp_path, monkeypatch)
    gate._verify(evidence)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("passed", False),
        ("migration_head", "0011"),
        ("production_runtime_activated", True),
        ("lite_gate_default_off", False),
        ("knowledge_search_read_only_gated", False),
        ("formal_builder_named", False),
        ("root_env_accessed", True),
        ("business_database_accessed", True),
        ("business_database_migrated", True),
        (
            "feature_gates",
            {
                "agent_runtime_enabled": True,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            },
        ),
    ],
)
def test_report_claim_tamper_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    report[field] = value
    _rewrite_evidence(evidence, report)
    with pytest.raises(RuntimeError):
        gate._verify(evidence)


def test_command_failure_and_sidecar_tamper_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, report = _synthetic_run(tmp_path, monkeypatch)
    report["commands"][0]["returncode"] = 1
    _rewrite_evidence(evidence, report)
    with pytest.raises(RuntimeError, match="did not prove success"):
        gate._verify(evidence)

    evidence, _ = _synthetic_run(tmp_path / "second", monkeypatch)
    stdout = evidence.parent / "commands" / "lite-unit-suite.stdout"
    stdout.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="stdout digest mismatch|artifact digest mismatch"):
        gate._verify(evidence)


def test_source_and_artifact_seals_reject_tamper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    evidence, _ = _synthetic_run(tmp_path, monkeypatch)
    source_hash = evidence.parent / "source-manifest.sha256"
    source_hash.write_text("0" * 64 + "\n", encoding="ascii")
    with pytest.raises(RuntimeError, match="source manifest raw-byte digest mismatch"):
        gate._verify(evidence)

    evidence, _ = _synthetic_run(tmp_path / "second", monkeypatch)
    artifact = evidence.parent / "evidence.md"
    artifact.write_text("tampered\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="artifact digest mismatch"):
        gate._verify(evidence)


def test_static_validation_closes_migration_and_gates(tmp_path: Path) -> None:
    # The typed-executor example contract must keep migration baseline 0012,
    # activation false and all three Phase 5 Feature Gates false.
    gate._validate_config()
