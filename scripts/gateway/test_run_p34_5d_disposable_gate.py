"""Non-destructive contract tests for the P34.5D disposable Gate wrapper."""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.gateway import run_p34_5d_disposable_gate as gate


def test_static_contract_rejects_ambient_images_venvs_and_client_mounts() -> None:
    gate._validate_static_contract()


def test_source_manifest_is_stable_and_binds_clean_checkout_inputs(monkeypatch) -> None:
    def fake_git(arguments: list[str], *, check: bool = True) -> CompletedProcess[str]:
        assert check is True
        if arguments[:2] == ["git", "status"]:
            output = " M deployment/gateway/compose.disposable.yml\n"
        elif arguments == ["git", "rev-parse", "HEAD"]:
            output = ("1" * 40) + "\n"
        elif arguments == ["git", "rev-parse", "HEAD^{tree}"]:
            output = ("2" * 40) + "\n"
        else:
            raise AssertionError(arguments)
        return CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(gate, "_run", fake_git)
    first = gate._source_manifest()
    second = gate._source_manifest()

    assert first == second
    assert len(str(first["git_commit"])) == 40
    assert len(str(first["git_tree"])) == 40
    assert len(str(first["dirty_scope_sha256"])) == 64
    files = {entry["path"]: entry for entry in first["files"]}
    assert ".gitattributes" in files
    assert "backend/pyproject.toml" in files
    assert "backend/uv.lock" in files
    assert "deployment/gateway/Dockerfile.gate" in files
    assert "deployment/gateway/Dockerfile.client" in files
    assert "deployment/gateway/compose.disposable.yml" in files
    assert "scripts/gateway/run_p34_5d_disposable_gate.py" in files
    assert "scripts/gateway/p34_5d_broker_client.py" in files
    assert "backend/src/omnibase/capability_gateway/server.py" in files
    assert "backend/src/omnibase/migrations/versions/0008_p34_5_sandbox_dispatch.py" in files
    assert "backend/tests/integration/test_p34_5_gateway_mtls_split_disposable.py" in files
    assert first["symlink_count"] == 0
    assert all(path != ".env" for path in first["dirty_paths"])
    assert not any("__pycache__" in path or ".pytest_cache" in path for path in files)
    assert all(len(str(entry["sha256"])) == 64 for entry in files.values())


def test_shell_build_inputs_require_lf_line_endings(tmp_path: Path) -> None:
    valid = tmp_path / "valid.sh"
    valid.write_bytes(b"#!/bin/sh\nset -eu\n")
    gate._validate_lf_shell_script(valid)

    crlf = tmp_path / "crlf.sh"
    crlf.write_bytes(b"#!/bin/sh\r\nset -eu\r\n")
    with pytest.raises(RuntimeError, match="not LF-only"):
        gate._validate_lf_shell_script(crlf)


def test_generated_env_and_compose_command_are_explicitly_disposable(tmp_path: Path) -> None:
    env_file = tmp_path / "gate.env"
    image_id = "sha256:" + ("1" * 64)
    gate._write_env_file(
        env_file,
        database_name="omnibase_test_p345d_contract",
        role_name="omnibase_test_p345d_contract_role",
        images={
            "GATE_GATEWAY_IMAGE": image_id,
            "GATE_POSTGRES_IMAGE": image_id,
            "GATE_CLIENT_IMAGE": image_id,
        },
    )

    content = env_file.read_text(encoding="utf-8")
    assert "TEST_DATABASE_NAME=omnibase_test_p345d_contract" in content
    assert "GATE_GATEWAY_IMAGE=sha256:" in content
    assert ".env" not in content
    compose = gate._compose_command(env_file, "omnibase-p345d-contract")
    assert compose[:3] == ["docker", "compose", "--env-file"]
    assert compose[3] == str(env_file)
    assert str(gate.COMPOSE_FILE) in compose


def test_historical_clean_evidence_verifies_only_stable_source_bytes() -> None:
    manifest = gate._source_manifest()
    manifest["git_commit"] = "1" * 40
    manifest["git_tree"] = "2" * 40
    manifest["dirty"] = False
    manifest["dirty_paths"] = []
    report = {
        "passed": True,
        "root_env_accessed": False,
        "business_database_migrated": False,
        "cleanup": {
            "containers": 0,
            "networks": 0,
            "temporary_env_removed": True,
            "volumes": 0,
        },
        "secret_scan_findings": [],
        "source_manifest": manifest,
        "source_manifest_sha256": hashlib.sha256(gate._canonical_json_bytes(manifest)).hexdigest(),
        "clean_checkout_build": {
            "ambient_backend_image_used": False,
            "ambient_virtualenv_used": False,
            "broker_client_host_mount_present": False,
        },
    }
    gate._verify_recorded_evidence(report)

    manifest["files"][0]["size"] += 1
    report["source_manifest_sha256"] = hashlib.sha256(
        gate._canonical_json_bytes(manifest)
    ).hexdigest()
    with pytest.raises(RuntimeError, match="source bytes changed"):
        gate._verify_recorded_evidence(report)
