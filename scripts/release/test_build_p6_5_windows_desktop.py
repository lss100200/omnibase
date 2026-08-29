from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import tarfile
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


def test_component_bundle_report_is_projected_from_the_runtime_manifest(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    manifest = tmp_path / "runtime-manifest.json"
    files = [
        {"path": "OmniBase.RuntimeHost.exe", "size": 11, "sha256": "a" * 64},
        {
            "path": "components/knowledge-ebook/manifest.json",
            "size": 23,
            "sha256": "b" * 64,
        },
        {
            "path": "components/knowledge-ebook/catalog.json",
            "size": 37,
            "sha256": "c" * 64,
        },
    ]
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "entrypoint": {"path": "OmniBase.RuntimeHost.exe", "args": []},
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    canonical = (
        b"knowledge-ebook/catalog.json\0" + b"37\0" + b"c" * 64 + b"\n"
        b"knowledge-ebook/manifest.json\0" + b"23\0" + b"b" * 64 + b"\n"
    )

    expected = {
        "bundle_sha256": "d" * 64,
        "file_count": 2,
        "output_bytes": 60,
        "package_count": 10,
        "tree_sha256": hashlib.sha256(canonical).hexdigest(),
    }
    assert builder._component_runtime_report(manifest, expected_bundle=expected) == {
        "file_count": 2,
        "total_bytes": 60,
        "tree_sha256": hashlib.sha256(canonical).hexdigest(),
    }
    with pytest.raises(
        builder.DesktopReleaseError, match="component_bundle_unexpected"
    ):
        builder._component_runtime_report(manifest, expected_bundle=None)
    with pytest.raises(builder.DesktopReleaseError, match="component_bundle_changed"):
        builder._component_runtime_report(
            manifest, expected_bundle={**expected, "tree_sha256": "0" * 64}
        )


def test_component_bundle_report_rejects_a_supplied_empty_bundle(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    manifest = tmp_path / "runtime-manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "entrypoint": {"path": "OmniBase.RuntimeHost.exe", "args": []},
                "files": [
                    {
                        "path": "OmniBase.RuntimeHost.exe",
                        "size": 11,
                        "sha256": "a" * 64,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert builder._component_runtime_report(manifest, expected_bundle=None) is None
    with pytest.raises(builder.DesktopReleaseError, match="component_bundle_empty"):
        builder._component_runtime_report(
            manifest,
            expected_bundle={
                "file_count": 10,
                "output_bytes": 1,
                "tree_sha256": "a" * 64,
            },
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


def _release_tool_inputs(builder, tmp_path: Path) -> dict[str, Path]:
    tools = tmp_path / "tools"
    tools.mkdir()
    dotnet = tools / "dotnet.exe"
    python = tools / "python.exe"
    node = tools / builder.NODE_EXECUTABLE_NAME
    corepack = tools / "corepack.cmd"
    electron = tools / builder.ELECTRON_ZIP_NAME
    for path in (dotnet, python, node, corepack, electron):
        path.write_bytes(b"fixture")
    return {
        "dotnet_executable": dotnet,
        "backend_python": python,
        "node_executable": node,
        "electron_zip": electron,
    }


def test_clean_release_requires_the_exact_component_bundle_before_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    monkeypatch.setattr(
        builder,
        "_repository_state",
        lambda _repository: builder.RepositoryState("a" * 40, b""),
    )
    artifacts = tmp_path / "artifacts"

    with pytest.raises(
        builder.DesktopReleaseError, match="clean_component_bundle_required"
    ):
        builder.build_windows_desktop(
            repo_root=repo,
            artifact_root=artifacts,
            **_release_tool_inputs(builder, tmp_path),
        )

    assert not artifacts.exists()


def test_head_snapshot_ignores_a_post_check_live_worktree_replacement(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", str(source)], check=True)
    tracked = source / "tracked.txt"
    tracked.write_text("committed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "tracked.txt"], check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=OmniBase Test",
            "-c",
            "user.email=test@omnibase.local",
            "-C",
            str(source),
            "commit",
            "-qm",
            "fixture",
        ],
        check=True,
    )
    head = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tracked.write_text("temporary replacement\n", encoding="utf-8")
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()

    tree_sha256 = builder._materialize_head_snapshot(
        source,
        head,
        artifacts / "source.tar",
        artifacts / "snapshot",
    )

    assert (artifacts / "snapshot/tracked.txt").read_text(encoding="utf-8") == (
        "committed\n"
    )
    assert (
        tree_sha256
        == hashlib.sha256(
            b"tracked.txt\0"
            + b"10\0"
            + hashlib.sha256(b"committed\n").hexdigest().encode("ascii")
            + b"\n"
        ).hexdigest()
    )


@pytest.mark.parametrize("attack", ["escape", "symlink"])
def test_source_snapshot_rejects_non_regular_or_escaping_members(
    tmp_path: Path, attack: str
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    archive = tmp_path / f"{attack}.tar"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as bundle:
        if attack == "escape":
            info = tarfile.TarInfo("../escape.txt")
            raw = b"escape"
            info.size = len(raw)
            bundle.addfile(info, io.BytesIO(raw))
        else:
            info = tarfile.TarInfo("linked.txt")
            info.type = tarfile.SYMTYPE
            info.linkname = "outside.txt"
            bundle.addfile(info)
    archive.write_bytes(buffer.getvalue())

    with pytest.raises(builder.DesktopReleaseError, match="source_archive_"):
        builder._extract_source_archive(archive, tmp_path / "snapshot")


@pytest.mark.parametrize("content", ["placeholder", "wrong_digest", "decoy_digest"])
def test_staged_manifest_pin_rejects_placeholder_or_wrong_digest(
    tmp_path: Path, content: str
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    expected = "a" * 64
    path = tmp_path / "trusted-manifest.js"
    value = builder._TRUST_TOKEN if content == "placeholder" else "b" * 64
    decoy = f"// {expected}\n" if content == "decoy_digest" else ""
    path.write_text(
        decoy
        + "exports.PINNED_RUNTIME_MANIFEST_SHA256 = void 0;\n"
        + f'exports.PINNED_RUNTIME_MANIFEST_SHA256 = "{value}";\n',
        encoding="utf-8",
    )

    with pytest.raises(builder.DesktopReleaseError, match="staged_pin_invalid"):
        builder._verify_manifest_pin_file(
            path,
            expected_sha256=expected,
            code="desktop_release_staged_pin_invalid",
        )


def test_staged_manifest_pin_accepts_the_exact_compiled_assignment(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    expected = "a" * 64
    path = tmp_path / "trusted-manifest.js"
    raw = (
        "exports.PINNED_RUNTIME_MANIFEST_SHA256 = void 0;\n"
        f'exports.PINNED_RUNTIME_MANIFEST_SHA256 = "{expected}";\n'
    ).encode()
    path.write_bytes(raw)

    assert builder._verify_manifest_pin_file(
        path,
        expected_sha256=expected,
        code="desktop_release_staged_pin_invalid",
    ) == {
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "manifest_sha256": expected,
        "placeholder_absent": True,
    }


def test_packaged_asar_manifest_pin_rejects_a_wrong_extracted_member(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    builder = _builder(repo)
    staged = tmp_path / "staged"
    library = (
        staged
        / "node_modules/.pnpm/@electron+asar@4.3.0/node_modules/@electron/asar/lib/asar.js"
    )
    library.parent.mkdir(parents=True)
    library.write_text("export function extractFile() {}\n", encoding="utf-8")
    app_asar = tmp_path / "app.asar"
    app_asar.write_bytes(b"asar fixture")
    node = tmp_path / "node.exe"
    node.write_bytes(b"node fixture")

    def fake_runner(name, command, cwd, environment, timeout, capture=False):
        assert name == "asar-manifest-extract"
        Path(command[-1]).write_text(
            'exports.PINNED_RUNTIME_MANIFEST_SHA256 = "' + "b" * 64 + '";\n',
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    with pytest.raises(
        builder.DesktopReleaseError, match="packaged_manifest_pin_invalid"
    ):
        builder._verify_packaged_asar_manifest_pin(
            runner=fake_runner,
            node=node,
            staged_desktop=staged,
            app_asar=app_asar,
            output=tmp_path / "extracted.js",
            cwd=tmp_path,
            environment={},
            expected_sha256="a" * 64,
        )
