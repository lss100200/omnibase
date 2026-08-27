from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest


def _builder(repo: Path):
    path = repo / "scripts/release/build_p6_5_windows_desktop.py"
    spec = importlib.util.spec_from_file_location("build_p6_5_windows_desktop", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_source_mode_requires_clean_release_or_explicit_engineering_acknowledgement() -> (
    None
):
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    clean = builder.RepositoryState("a" * 40, b"")
    dirty = builder.RepositoryState("a" * 40, b" M desktop/package.json\0")

    builder._validate_source_mode(clean, "clean-release", None)
    with pytest.raises(builder.DesktopReleaseError, match="repository_must_be_clean"):
        builder._validate_source_mode(dirty, "clean-release", None)
    with pytest.raises(
        builder.DesktopReleaseError, match="engineering_confirmation_required"
    ):
        builder._validate_source_mode(dirty, "engineering-dirty", None)
    builder._validate_source_mode(
        dirty,
        "engineering-dirty",
        builder.ENGINEERING_CONFIRMATION,
    )


def test_build_environment_is_closed_and_does_not_forward_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    required = {
        "CommonProgramFiles": "C:\\Program Files\\Common Files",
        "HOMEDRIVE": "C:",
        "HOMEPATH": "\\Users\\Builder",
        "PATH": "C:\\Tools",
        "ProgramFiles": "C:\\Program Files",
        "SystemRoot": "C:\\Windows",
        "TEMP": str(tmp_path),
        "USERPROFILE": "C:\\Users\\Builder",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-forward")
    monkeypatch.setenv("DATABASE_URL", "must-not-forward")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "must-not-forward")

    environment = builder._safe_environment(tmp_path / "artifacts")

    assert environment["PATH"] == required["PATH"]
    assert environment["ProgramFiles"] == required["ProgramFiles"]
    assert environment["HOMEDRIVE"] == required["HOMEDRIVE"]
    assert environment["SystemRoot"] == required["SystemRoot"]
    assert environment["NEXT_TELEMETRY_DISABLED"] == "1"
    assert environment["PYTHONHASHSEED"] == "0"
    assert "OPENAI_API_KEY" not in environment
    assert "DATABASE_URL" not in environment
    assert "GIT_CONFIG_GLOBAL" not in environment
    assert "must-not-forward" not in repr(environment)


def test_installer_staging_is_an_exact_source_owned_closed_set(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    destination = tmp_path / "installer-source"

    builder._copy_installer_source(
        repo / "packaging/windows/OmniBase.Installer",
        destination,
    )

    copied = {
        path.relative_to(destination).as_posix()
        for path in destination.rglob("*")
        if path.is_file()
    }
    assert copied == set(builder._INSTALLER_SOURCE_FILES)
    assert not any(
        part in {"tests", "bin", "obj"} for path in copied for part in Path(path).parts
    )


def test_external_runtime_input_is_copied_and_digest_bound(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    source = tmp_path / "electron.zip"
    destination = tmp_path / "staged.zip"
    raw = b"offline electron archive"
    source.write_bytes(raw)

    digest = builder._copy_verified_input(
        source,
        destination,
        expected_size=len(raw),
        expected_sha256=hashlib.sha256(raw).hexdigest(),
    )

    assert destination.read_bytes() == raw
    assert digest == hashlib.sha256(raw).hexdigest()
    assert (
        builder._verify_digest_bound_file(
            destination,
            expected_size=len(raw),
            expected_sha256=hashlib.sha256(raw).hexdigest(),
        )
        == digest
    )
    destination.write_bytes(b"offline electron archivf")
    with pytest.raises(builder.DesktopReleaseError, match="digest_mismatch"):
        builder._verify_digest_bound_file(
            destination,
            expected_size=len(raw),
            expected_sha256=hashlib.sha256(raw).hexdigest(),
        )
    with pytest.raises(builder.DesktopReleaseError, match="digest_mismatch"):
        builder._copy_verified_input(
            source,
            tmp_path / "rejected.zip",
            expected_size=len(raw),
            expected_sha256="0" * 64,
        )


def test_final_installer_artifacts_ignore_wix_intermediate_copies(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    root = tmp_path / "installer-source"
    setup_name = "OmniBase-1.0.0-windows-x64-setup.exe"
    msi_name = "OmniBase-1.0.0-windows-x64.msi"
    setup = root / "bundle/bin/Release" / setup_name
    msi = root / "package/bin/Release" / msi_name
    setup.parent.mkdir(parents=True)
    msi.parent.mkdir(parents=True)
    setup.write_bytes(b"final setup")
    msi.write_bytes(b"final msi")
    intermediate_setup = root / "bundle/obj/Release" / setup_name
    intermediate_msi = root / "package/obj/Release" / msi_name
    intermediate_setup.parent.mkdir(parents=True)
    intermediate_msi.parent.mkdir(parents=True)
    os.link(setup, intermediate_setup)
    os.link(msi, intermediate_msi)

    assert (
        builder._final_installer_artifact(root, "bundle", setup_name, ".exe") == setup
    )
    assert builder._final_installer_artifact(root, "package", msi_name, ".msi") == msi
    release = tmp_path / "release"
    release.mkdir()
    promoted_setup = builder._promote_built_artifact(setup, release / setup_name)
    promoted_msi = builder._promote_built_artifact(msi, release / msi_name)
    assert promoted_setup.read_bytes() == b"final setup"
    assert promoted_msi.read_bytes() == b"final msi"
    assert promoted_setup.stat().st_nlink == 1
    assert promoted_msi.stat().st_nlink == 1


def test_payload_copy_report_is_strict_and_digest_bound() -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    value = {
        "file_count": 2,
        "payload_copied": True,
        "total_bytes": 15,
        "tree_sha256": "a" * 64,
        "wix_authoring_written": False,
    }
    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout=json.dumps(value),
        stderr="",
    )

    assert builder._parse_payload_report(completed, copied=True) == value
    value["unexpected"] = True
    with pytest.raises(builder.DesktopReleaseError, match="payload_report_invalid"):
        builder._parse_payload_report(
            subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=json.dumps(value),
                stderr="",
            ),
            copied=True,
        )


def test_release_version_is_fixed_to_1_0_0_before_any_output(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)

    with pytest.raises(builder.DesktopReleaseError, match="version_invalid"):
        builder.build_windows_desktop(
            repo_root=repo,
            artifact_root=tmp_path / "artifacts",
            dotnet_executable=tmp_path / "dotnet.exe",
            backend_python=tmp_path / "python.exe",
            node_executable=tmp_path / "node.exe",
            electron_zip=tmp_path / "electron.zip",
            version="1.0.1",
        )

    assert not (tmp_path / "artifacts").exists()
