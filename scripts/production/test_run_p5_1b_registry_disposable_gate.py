"""Non-destructive contract tests for the P5.1B disposable Gate wrapper."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.production import run_p5_1b_registry_disposable_gate as gate


def _safe_evidence(manifest_sha256: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "gate": "P5.1B Agent Registry persistence disposable Gate",
        "passed": True,
        "root_env_accessed": False,
        "business_database_accessed": False,
        "business_database_migrated": False,
        "database_sentinel_verified": True,
        "physical_locator_exposed": False,
        "cleanup": {"containers": 0, "networks": 0, "volumes": 0},
        "manifest_sha256": manifest_sha256,
    }


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


def test_compose_always_uses_explicit_env_example(monkeypatch) -> None:
    captured: list[str] = []

    def fake_run(
        arguments: list[str],
        *,
        check: bool = True,
        env: dict[str, str] | None = None,
    ) -> CompletedProcess[str]:
        captured.extend(arguments)
        assert check is False
        return CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(gate, "_run", fake_run)
    gate._compose("omnibase-p51b-test", "ps", env={})
    assert captured[:4] == ["docker", "compose", "--env-file", str(gate.ENV_FILE)]
    assert str(gate.REPO_ROOT / ".env") not in captured


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
    evidence = _safe_evidence(gate._manifest_sha256(manifest))
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
    evidence = _safe_evidence(gate._manifest_sha256(manifest))
    evidence["root_env_accessed"] = True
    path = tmp_path / "unsafe.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    with pytest.raises(RuntimeError, match="root .env"):
        gate._verify_recorded_evidence(json.loads(path.read_text(encoding="utf-8")))


@pytest.mark.parametrize(
    ("field", "unsafe_value", "message"),
    [
        ("business_database_accessed", True, "business database access"),
        ("business_database_migrated", True, "business database migration"),
        ("database_sentinel_verified", False, "sentinel"),
        ("physical_locator_exposed", True, "physical locator"),
        ("cleanup", {"containers": 1, "networks": 0, "volumes": 0}, "cleanup"),
    ],
)
def test_verify_evidence_rejects_incomplete_safety_proof(
    monkeypatch, field: str, unsafe_value: object, message: str
) -> None:
    monkeypatch.setattr(
        gate,
        "_source_manifest",
        lambda: {
            "schema_version": 1,
            "repository_clean": True,
            "dirty_paths": (),
            "file_count": 0,
            "files": {},
        },
    )
    manifest = gate._source_manifest()
    evidence = _safe_evidence(gate._manifest_sha256(manifest))
    evidence[field] = unsafe_value
    with pytest.raises(RuntimeError, match=message):
        gate._verify_recorded_evidence(evidence)


def test_cleanup_project_requires_zero_labeled_resources(monkeypatch) -> None:
    monkeypatch.setattr(
        gate,
        "_compose",
        lambda *args, **kwargs: CompletedProcess(list(args), 0, "", ""),
    )
    monkeypatch.setattr(
        gate,
        "_project_resource_counts",
        lambda project: {"containers": 0, "networks": 1, "volumes": 0},
    )
    with pytest.raises(RuntimeError, match="left resources"):
        gate._cleanup_project("omnibase-p51b-test", env={})
