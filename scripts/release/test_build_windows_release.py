"""Offline deterministic Windows release tests."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest


def _module(repo: Path, name: str, relative: str):
    path = repo / relative
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _builder(repo: Path):
    return _module(repo, "p61_release", "scripts/release/build_windows_release.py")


def _preflight(repo: Path):
    return _module(
        repo,
        "p61_release_preflight",
        "scripts/release/validate_windows_release_config.py",
    )


def _clean_state(builder, commit: str = "a" * 40):
    return builder.RepositoryState(head=commit, clean=True)


def test_release_zip_is_byte_reproducible_and_closed(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    result_a = builder.build_release(
        repo,
        first,
        source_commit="a" * 40,
        repository_state=_clean_state(builder),
    )
    result_b = builder.build_release(
        repo,
        second,
        source_commit="a" * 40,
        repository_state=_clean_state(builder),
    )
    assert result_a["sha256"] == result_b["sha256"]
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        names = archive.namelist()
        assert names == sorted(names)
        assert all(
            entry.compress_type == zipfile.ZIP_STORED for entry in archive.infolist()
        )
        assert "release.json" in names
        manifest = json.loads(archive.read("release.json"))
        assert manifest["publisher_signature_verified"] is False
        assert manifest["authenticode_verified"] is False
        lowered = "\n".join(names).casefold()
        assert "node_modules" not in lowered
        assert ".next" not in lowered
        assert ".vhdx" not in lowered
        assert "deployment/release/windows/operator.env.template" in names
        assert "scripts/release/validate_windows_release_config.py" in names
        assert "deployment/release/windows/.env" not in names


def test_release_builder_requires_matching_clean_repository_state(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    with pytest.raises(builder.ReleaseBuildError, match="must_be_clean"):
        builder.build_release(
            repo,
            tmp_path / "dirty.zip",
            source_commit="a" * 40,
            repository_state=builder.RepositoryState(head="a" * 40, clean=False),
        )


def test_repository_state_ignores_ambient_git_repository_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        monkeypatch.setenv(name, str(repo.parent / "attacker-controlled"))

    state = builder._repository_state(repo)
    for name in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    ):
        monkeypatch.delenv(name, raising=False)
    expected = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    assert state.head == expected


def test_release_builder_rechecks_repository_after_reading_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    states = iter(
        [
            builder.RepositoryState(head="a" * 40, clean=True),
            builder.RepositoryState(head="a" * 40, clean=False),
        ]
    )
    monkeypatch.setattr(builder, "_repository_state", lambda _root: next(states))
    monkeypatch.setattr(
        builder,
        "_read_committed_file",
        lambda root, commit, relative: (root / relative).read_bytes(),
    )
    with pytest.raises(builder.ReleaseBuildError, match="changed_during_build"):
        builder.build_release(repo, tmp_path / "drift.zip", source_commit="a" * 40)


def test_committed_payload_reader_ignores_dirty_worktree_bytes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    payload = repo / "payload.txt"
    payload.write_text("committed", encoding="utf-8")
    subprocess.run(["git", "add", "payload.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "fixture"], cwd=repo, check=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout.strip()
    payload.write_text("dirty attacker bytes", encoding="utf-8")

    builder = _builder(Path(__file__).resolve().parents[2])
    assert builder._read_committed_file(repo, commit, "payload.txt") == b"committed"


def test_committed_payload_reader_ignores_git_replace_refs(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"], cwd=repo, check=True
    )
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    payload = repo / "payload.txt"
    payload.write_text("original", encoding="utf-8")
    subprocess.run(["git", "add", "payload.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "--quiet", "-m", "original"], cwd=repo, check=True)
    original = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    payload.write_text("replacement", encoding="utf-8")
    subprocess.run(["git", "add", "payload.txt"], cwd=repo, check=True)
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "replacement"], cwd=repo, check=True
    )
    replacement = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    subprocess.run(["git", "replace", original, replacement], cwd=repo, check=True)

    builder = _builder(Path(__file__).resolve().parents[2])
    assert builder._read_committed_file(repo, original, "payload.txt") == b"original"


def test_repository_state_disables_local_fsmonitor(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("shell fsmonitor fixture is POSIX-only")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repo, check=True)
    marker = repo / "executed"
    hook = repo / "fsmonitor-hook"
    hook.write_text(
        f"#!/bin/sh\necho executed > '{marker}'\nexit 1\n", encoding="utf-8"
    )
    hook.chmod(0o755)
    subprocess.run(["git", "config", "core.fsmonitor", str(hook)], cwd=repo, check=True)

    builder = _builder(Path(__file__).resolve().parents[2])
    builder._repository_state(repo)
    assert not marker.exists()
    with pytest.raises(builder.ReleaseBuildError, match="not_current_head"):
        builder.build_release(
            repo,
            tmp_path / "drifted.zip",
            source_commit="a" * 40,
            repository_state=builder.RepositoryState(head="b" * 40, clean=True),
        )


def test_release_template_secret_scan_rejects_non_placeholders() -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    builder._validate_template(b"JWT_SECRET=REPLACE_WITH_RANDOM_VALUE\n")
    with pytest.raises(builder.ReleaseBuildError, match="secret_not_placeholder"):
        builder._validate_template(b"JWT_SECRET=actual-secret-value\n")
    with pytest.raises(builder.ReleaseBuildError, match="secret_material"):
        builder._validate_template(b"-----BEGIN PRIVATE KEY-----\n")


def test_release_compose_has_no_build_and_reuses_personal_lifecycle() -> None:
    repo = Path(__file__).resolve().parents[2]
    compose = (repo / "deployment/release/windows/compose.yml").read_text(
        encoding="utf-8"
    )
    assert "build:" not in compose
    for service in ("redis-init:", "minio-init:", "migrate:"):
        assert service in compose
    assert compose.count("healthcheck:") == 5
    assert 'MCP_RUNTIME_ENABLED: "false"' in compose
    assert 'AGENT_RUNTIME_ENABLED: "false"' in compose
    assert 'AGENT_PLANNER_ENABLED: "false"' in compose
    assert 'MULTI_AGENT_ENABLED: "false"' in compose
    assert 'RATE_LIMIT_FAIL_CLOSED: "true"' in compose


def test_windows_installer_retries_only_the_final_atomic_move() -> None:
    repo = Path(__file__).resolve().parents[2]
    source = (repo / "packaging/windows/OmniBase.Setup/Program.cs").read_text(
        encoding="utf-8"
    )

    assert source.count("MoveDirectoryWithRetry(staging, target);") == 1
    assert source.count("Directory.Move(source, destination);") == 1
    assert source.count("catch (IOException exception)") == 1

    helper_start = source.index("static void MoveDirectoryWithRetry")
    helper_end = source.index("static bool ValidArchivePath", helper_start)
    helper = source[helper_start:helper_end]
    assert "const int maxAttempts = 8;" in helper
    assert "const int retryDelayMilliseconds = 100;" in helper
    assert "for (var attempt = 1; attempt <= maxAttempts; attempt++)" in helper
    assert "if (attempt == maxAttempts)" in helper
    assert "atomic install retry budget exhausted" in helper
    assert "Thread.Sleep(retryDelayMilliseconds * attempt);" in helper
    destination_guard = "if (File.Exists(destination) || Directory.Exists(destination))"
    assert destination_guard in helper
    assert helper.index(destination_guard) < helper.index(
        "Directory.Move(source, destination);"
    )
    assert "release target appeared before atomic install" in helper
    assert "atomic install retry budget exhausted" in helper

    before_move = source[: source.index("MoveDirectoryWithRetry(staging, target);")]
    assert "catch (IOException) when" not in before_move


def test_offline_preflight_accepts_only_allowlisted_digest_images(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    preflight = _preflight(repo)
    env = tmp_path / "operator.env"
    env.write_text(
        "\n".join(
            f"{name}={repository}@sha256:{index:064x}"
            for index, (name, repository) in enumerate(
                preflight.IMAGE_REPOSITORIES.items(), 1
            )
        )
        + "\n",
        encoding="utf-8",
    )
    result = preflight.validate_release_config(
        repo / "deployment/release/windows/compose.yml", env
    )
    assert result["valid"] is True
    assert result["network_used"] is False
    assert len(result["images"]) == 6

    env.write_text(
        env.read_text(encoding="utf-8").replace(
            "redis@sha256:", "attacker.example/redis@sha256:"
        ),
        encoding="utf-8",
    )
    with pytest.raises(preflight.ReleaseConfigError, match="not_allowlisted"):
        preflight.validate_release_config(
            repo / "deployment/release/windows/compose.yml", env
        )


def test_offline_preflight_rejects_tags_placeholders_and_duplicate_env(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    preflight = _preflight(repo)
    compose = repo / "deployment/release/windows/compose.yml"
    for value in ("redis:7.4-alpine", "redis@sha256:REPLACE_WITH_64_HEX_DIGEST"):
        env = tmp_path / f"bad-{len(value)}.env"
        env.write_text(f"OMNIBASE_REDIS_IMAGE={value}\n", encoding="utf-8")
        with pytest.raises(preflight.ReleaseConfigError, match="not_allowlisted"):
            preflight.validate_release_config(compose, env)
    duplicate = tmp_path / "duplicate.env"
    duplicate.write_text("A=1\nA=2\n", encoding="utf-8")
    with pytest.raises(preflight.ReleaseConfigError, match="duplicate"):
        preflight.validate_release_config(compose, duplicate)
