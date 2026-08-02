"""Non-destructive contract tests for the P34.5D disposable Gate wrapper."""

from __future__ import annotations

import sys
from pathlib import Path
from subprocess import CompletedProcess

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
