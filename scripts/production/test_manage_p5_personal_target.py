"""Tests for the offline personal production target release controller."""

from __future__ import annotations

import base64
import json
import os
import subprocess
from pathlib import Path

import manage_p5_personal_target as manager
import pytest


@pytest.fixture(autouse=True)
def _stable_windows_acl_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        monkeypatch.setattr(manager, "_check_windows_acl", lambda _path: None)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _public_repo(tmp_path: Path) -> Path:
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    subprocess.run(
        ["git", "clone", str(remote), str(repo)], check=True, capture_output=True
    )
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Test")
    (repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "public")
    _git(repo, "push", "-u", "origin", "HEAD:main")
    _git(repo, "fetch", "origin", "main")
    return repo


def _secret_bytes(**overrides: str) -> bytes:
    values = {
        "OMNIBASE_FRONTEND_PORT": "3121",
        "POSTGRES_USER": "omnibase",
        "POSTGRES_PASSWORD": "Pg-7v3Nf8wQ2cR5mK9xD4sL",
        "POSTGRES_DB": "omnibase",
        "MINIO_ROOT_PASSWORD": "Mi-9xT4kP7mV2qW8cN5rJ6",
        "MINIO_ROOT_USER": "omnibase",
        "MINIO_BUCKET": "omnibase-files",
        "REDIS_PASSWORD": "Rd-6pL8wQ4nV7xK2mT9cF5",
        "JWT_SECRET": "JwT-7v3Nf8wQ2cR5mK9xD4sL6pT8yH1aB0zC7uE5iG9nQ4rW2pK8x",
        "PROVIDER_CREDENTIAL_ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32)
        .decode("ascii")
        .rstrip("="),
        "OMNIBASE_DEPLOYMENT_INSTANCE_ID": "123e4567-e89b-42d3-a456-426614174000",
        "PROVIDER_ENDPOINT_ALLOWLIST": '["api.deepseek.com"]',
        "CORS_ORIGINS": '["http://127.0.0.1:3121"]',
    }
    values["DATABASE_URL"] = (
        "postgresql+psycopg://omnibase:"
        f"{values['POSTGRES_PASSWORD']}@postgres:5432/omnibase"
    )
    values["REDIS_URL"] = f"redis://:{values['REDIS_PASSWORD']}@redis:6379/0"
    values.update(overrides)
    if "POSTGRES_PASSWORD" in overrides and "DATABASE_URL" not in overrides:
        values["DATABASE_URL"] = (
            "postgresql+psycopg://omnibase:"
            f"{values['POSTGRES_PASSWORD']}@postgres:5432/omnibase"
        )
    if "REDIS_PASSWORD" in overrides and "REDIS_URL" not in overrides:
        values["REDIS_URL"] = f"redis://:{values['REDIS_PASSWORD']}@redis:6379/0"
    return "".join(f"{key}={value}\n" for key, value in values.items()).encode()


def _paths(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    paths = tuple(tmp_path / name for name in ("release", "state", "backup"))
    for path in paths:
        path.mkdir()
    secret = tmp_path / "target.env"
    secret.write_bytes(_secret_bytes())
    if os.name != "nt":
        secret.chmod(0o600)
    return (*paths, secret)


def test_secret_env_rejects_missing_and_placeholder_without_leaking_value(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "target.env"
    secret.write_bytes(
        _secret_bytes(
            POSTGRES_PASSWORD="changeme_super_secret_value"  # noqa: S106 - synthetic test value
        )
    )
    if os.name != "nt":
        secret.chmod(0o600)

    with pytest.raises(manager.TargetConfigurationError) as caught:
        manager._secret_env(secret, repo_root=repo.resolve())
    assert "POSTGRES_PASSWORD" in str(caught.value)
    assert "changeme_super_secret_value" not in str(caught.value)

    secret.write_bytes(b"ENV=production\n")
    with pytest.raises(manager.TargetConfigurationError, match="key set is invalid"):
        manager._secret_env(secret, repo_root=repo.resolve())


def test_operator_path_escape_and_secret_symlink_fail_closed(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    inside = repo / "operator"
    inside.mkdir(parents=True)
    with pytest.raises(
        manager.TargetConfigurationError, match="outside the repository"
    ):
        manager._operator_directory(
            inside, name="state directory", repo_root=repo.resolve()
        )

    outside = tmp_path / "outside.env"
    outside.write_bytes(_secret_bytes())
    if os.name != "nt":
        outside.chmod(0o600)
    link = tmp_path / "linked.env"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation is not available")
    with pytest.raises(manager.TargetConfigurationError, match="symlink or junction"):
        manager._secret_env(link, repo_root=repo.resolve())


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"CORS_ORIGINS": '["http://127.0.0.1:3000"]'}, "CORS_ORIGINS"),
        (
            {
                "DATABASE_URL": "postgresql+psycopg://omnibase:wrong@postgres:5432/omnibase"
            },
            "DATABASE_URL",
        ),
        ({"REDIS_URL": "redis://:wrong@redis:6379/0"}, "REDIS_URL"),
        (
            {"PROVIDER_ENDPOINT_ALLOWLIST": '["api.deepseek.com","api.deepseek.com"]'},
            "ALLOWLIST",
        ),
    ],
)
def test_operator_coordinates_are_exactly_bound(
    tmp_path: Path, overrides: dict[str, str], message: str
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "target.env"
    secret.write_bytes(_secret_bytes(**overrides))
    if os.name != "nt":
        secret.chmod(0o600)
    with pytest.raises(manager.TargetConfigurationError, match=message):
        manager._secret_env(secret, repo_root=repo.resolve())


def test_release_artifact_closed_set_binds_personal_production_sources() -> None:
    assert "backend/Dockerfile.production" in manager.ARTIFACT_PATHS
    assert "deployment/personal-production/compose.yml" in manager.ARTIFACT_PATHS
    assert (
        "deployment/personal-production/operator.env.example" in manager.ARTIFACT_PATHS
    )
    assert "scripts/production/manage_p5_personal_backup.py" in manager.ARTIFACT_PATHS
    assert "scripts/production/manage_p5_personal_target.py" in manager.ARTIFACT_PATHS
    assert "backend/Dockerfile" not in manager.ARTIFACT_PATHS
    assert "docker-compose.yml" not in manager.ARTIFACT_PATHS


def test_dirty_and_non_public_repository_are_rejected(tmp_path: Path) -> None:
    repo = _public_repo(tmp_path)
    facts = manager._repo_facts(repo)
    assert facts["public_remote_refs"]

    (repo / "untracked.txt").write_text("dirty\n", encoding="utf-8")
    with pytest.raises(manager.TargetConfigurationError, match="clean"):
        manager._repo_facts(repo)
    (repo / "untracked.txt").unlink()

    (repo / "tracked.txt").write_text("second\n", encoding="utf-8")
    _git(repo, "add", "tracked.txt")
    _git(repo, "commit", "-m", "local only")
    with pytest.raises(manager.TargetConfigurationError, match="public remote ref"):
        manager._repo_facts(repo)


def test_release_manifest_round_trip_and_digest_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, state, backup, secret = _paths(tmp_path)
    facts = {
        "artifacts": [
            {"path": "docker-compose.yml", "sha256": "a" * 64, "size_bytes": 1}
        ],
        "feature_gates": {name: False for name in manager.GATE_NAMES},
        "migration": {
            "head": "0014",
            "migration_0013_created": True,
            "migration_0014_created": True,
            "migration_0015_or_higher_absent": True,
        },
        "operator_paths": {
            "backup_dir": str(backup.resolve()),
            "release_dir": str(release.resolve()),
            "secret_env": str(secret.resolve()),
            "state_dir": str(state.resolve()),
        },
        "platform": {"machine": "test", "system": "test"},
        "repo": {
            "commit_sha256": "b" * 64,
            "tree_sha256": "c" * 64,
            "public_remote_refs": ["refs/remotes/origin/main"],
        },
        "secret_posture": {
            "permissions_valid": True,
            "required_keys": list(manager.REQUIRED_ENV_KEYS),
            "values_redacted": True,
            "values_valid": True,
        },
        "storage": {"minimum_free_bytes": 1, "observed_free_bytes": {}},
        "tools": {"docker": "Docker version 1", "compose": "Docker Compose version 1"},
    }
    output = release / "release.json"
    args = manager.argparse.Namespace(output=str(output))
    written = manager._write_manifest(args, facts)
    assert written["production_runtime_started"] is False
    payload, raw = manager._load_manifest(output)
    assert payload["target"]["repo"]["commit_sha256"] == "b" * 64
    assert written["manifest_sha256"] == manager._sha256(raw)

    envelope = json.loads(raw)
    envelope["payload"]["target"]["repo"]["tree_sha256"] = "d" * 64
    output.write_bytes(manager._canonical_bytes(envelope))
    with pytest.raises(manager.TargetConfigurationError, match="digest drifted"):
        manager._load_manifest(output)


def test_verify_release_detects_artifact_fact_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, state, backup, secret = _paths(tmp_path)
    facts = {
        "artifacts": [
            {"path": "backend/Dockerfile", "sha256": "a" * 64, "size_bytes": 2}
        ],
        "feature_gates": {name: False for name in manager.GATE_NAMES},
        "migration": {
            "head": "0014",
            "migration_0013_created": True,
            "migration_0014_created": True,
            "migration_0015_or_higher_absent": True,
        },
        "operator_paths": {
            "backup_dir": str(backup.resolve()),
            "release_dir": str(release.resolve()),
            "secret_env": str(secret.resolve()),
            "state_dir": str(state.resolve()),
        },
        "platform": {"machine": "test", "system": "test"},
        "repo": {
            "commit_sha256": "b" * 64,
            "tree_sha256": "c" * 64,
            "public_remote_refs": ["refs/remotes/origin/main"],
        },
        "secret_posture": {
            "permissions_valid": True,
            "required_keys": list(manager.REQUIRED_ENV_KEYS),
            "values_redacted": True,
            "values_valid": True,
        },
        "storage": {"minimum_free_bytes": 1, "observed_free_bytes": {}},
        "tools": {"docker": "Docker version 1", "compose": "Docker Compose version 1"},
    }
    manifest = release / "release.json"
    manager._write_manifest(manager.argparse.Namespace(output=str(manifest)), facts)
    drifted = {
        **facts,
        "artifacts": [
            {"path": "backend/Dockerfile", "sha256": "f" * 64, "size_bytes": 2}
        ],
    }
    monkeypatch.setattr(manager, "_doctor", lambda _args: drifted)

    args = manager.argparse.Namespace(
        repo_root=str(manager.REPO_ROOT), manifest=str(manifest)
    )
    with pytest.raises(manager.TargetConfigurationError, match="artifacts"):
        manager._verify_release(args)


def test_verify_release_accepts_public_remote_ref_movement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release, state, backup, secret = _paths(tmp_path)
    recorded = {
        "artifacts": [],
        "feature_gates": {name: False for name in manager.GATE_NAMES},
        "migration": {
            "head": "0014",
            "migration_0013_created": True,
            "migration_0014_created": True,
            "migration_0015_or_higher_absent": True,
        },
        "operator_paths": {
            "backup_dir": str(backup.resolve()),
            "release_dir": str(release.resolve()),
            "secret_env": str(secret.resolve()),
            "state_dir": str(state.resolve()),
        },
        "platform": {"machine": "test", "system": "test"},
        "repo": {
            "commit_sha256": "b" * 40,
            "tree_sha256": "c" * 40,
            "public_remote_refs": ["refs/remotes/origin/codex/feature"],
        },
        "secret_posture": {
            "permissions_valid": True,
            "required_keys": list(manager.REQUIRED_ENV_KEYS),
            "values_redacted": True,
            "values_valid": True,
        },
        "storage": {"minimum_free_bytes": 1, "observed_free_bytes": {}},
        "tools": {"docker": "Docker version 1", "compose": "Docker Compose version 1"},
    }
    manifest = release / "release.json"
    manager._write_manifest(manager.argparse.Namespace(output=str(manifest)), recorded)
    current = {
        **recorded,
        "repo": {
            **recorded["repo"],
            "public_remote_refs": ["refs/remotes/origin/main"],
        },
    }
    monkeypatch.setattr(manager, "_doctor", lambda _args: current)

    result = manager._verify_release(
        manager.argparse.Namespace(
            repo_root=str(manager.REPO_ROOT), manifest=str(manifest)
        )
    )

    assert result["verified"] is True
    assert result["status"] == "release/verified_not_started"


@pytest.mark.parametrize(
    ("recorded", "current", "message"),
    [
        (
            {
                "commit_sha256": "a" * 40,
                "tree_sha256": "b" * 40,
                "public_remote_refs": ["refs/remotes/origin/feature"],
            },
            {
                "commit_sha256": "c" * 40,
                "tree_sha256": "b" * 40,
                "public_remote_refs": ["refs/remotes/origin/main"],
            },
            "repo commit",
        ),
        (
            {
                "commit_sha256": "a" * 40,
                "tree_sha256": "b" * 40,
                "public_remote_refs": ["refs/remotes/origin/feature"],
            },
            {
                "commit_sha256": "a" * 40,
                "tree_sha256": "c" * 40,
                "public_remote_refs": ["refs/remotes/origin/main"],
            },
            "repo tree",
        ),
        (
            {
                "commit_sha256": "a" * 40,
                "tree_sha256": "b" * 40,
                "public_remote_refs": ["refs/remotes/origin/feature"],
            },
            {
                "commit_sha256": "a" * 40,
                "tree_sha256": "b" * 40,
                "public_remote_refs": [],
            },
            "current public remote refs",
        ),
        (
            {
                "commit_sha256": "a" * 40,
                "tree_sha256": "b" * 40,
                "public_remote_refs": ["refs/remotes/origin/feature"],
            },
            {
                "commit_sha256": "a" * 40,
                "tree_sha256": "b" * 40,
                "public_remote_refs": ["origin/main"],
            },
            "current public remote refs",
        ),
        (
            {
                "commit_sha256": "not-an-object-id",
                "tree_sha256": "b" * 40,
                "public_remote_refs": ["refs/remotes/origin/feature"],
            },
            {
                "commit_sha256": "a" * 40,
                "tree_sha256": "b" * 40,
                "public_remote_refs": ["refs/remotes/origin/main"],
            },
            "recorded repository object ids",
        ),
    ],
)
def test_verify_repo_facts_rejects_provenance_drift(
    recorded: object,
    current: object,
    message: str,
) -> None:
    with pytest.raises(manager.TargetConfigurationError, match=message):
        manager._verify_repo_facts(recorded, current)
