"""Assemble the P7.5 Linux payload and unsigned Electron AppDir.

This orchestrator deliberately consumes already-built Linux inputs. It never
downloads a runtime, mutates the source tree, signs an artifact, or claims a
Linux distribution package. The staged desktop project is built offline so
the compiled ``dist/main.js`` is paired with the manifest-pinned runtime before
the AppDir packager runs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

from build_p7_5_linux_desktop_payload import (
    LinuxPayloadError,
    build_linux_payload,
    validate_component_bundle,
)

ELECTRON_ZIP_NAME = "electron-v43.4.0-linux-x64.zip"
# SHA-256 published in Electron's v43.4.0 SHASUMS256.txt.
ELECTRON_ZIP_SHA256 = "7c5f7918bcae74a05a814543940eb28469c055edaa3cfcf41d0ff1787b314c52"
DESKTOP_PACKAGER = "desktop/scripts/package-linux.mjs"
MAX_REPORT_BYTES = 128 * 1024


class LinuxDesktopBuildError(ValueError):
    """A stable, path-redacted Linux desktop build failure."""


def _ordinary_directory(path: Path, *, code: str) -> Path:
    absolute = path.absolute()
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise LinuxDesktopBuildError(code) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or os.path.normcase(str(absolute)) != os.path.normcase(str(resolved))
    ):
        raise LinuxDesktopBuildError(code)
    return absolute


def _ordinary_file(path: Path, *, code: str, executable: bool = False) -> Path:
    absolute = path.absolute()
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise LinuxDesktopBuildError(code) from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or os.path.normcase(str(absolute)) != os.path.normcase(str(resolved))
        or (executable and metadata.st_mode & 0o111 == 0)
    ):
        raise LinuxDesktopBuildError(code)
    return absolute


def _outside_repository(path: Path, repository: Path) -> None:
    try:
        path.relative_to(repository)
    except ValueError:
        return
    raise LinuxDesktopBuildError("linux_desktop_output_inside_repository")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(command: list[str], *, cwd: Path, timeout: int, label: str) -> None:
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(Path.home())),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "CI": "1",
        "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
    }
    try:
        subprocess.run(command, cwd=cwd, env=environment, check=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise LinuxDesktopBuildError(f"linux_desktop_{label}_failed") from exc


def build_linux_desktop(
    *,
    repo_root: Path,
    frontend_standalone_dir: Path,
    frontend_static_dir: Path,
    frontend_public_dir: Path,
    desktop_dist_dir: Path,
    runtime_host_script: Path,
    backend_executable: Path,
    node_executable: Path,
    desktop_project_dir: Path,
    electron_zip_dir: Path,
    electron_zip_sha256: str,
    payload_dir: Path,
    output_dir: Path,
    component_bundle_dir: Path,
    component_bundle_sha256: str,
    component_bundle_tree_sha256: str,
    application_version: str = "1.0.0",
    backend_port: int = 8765,
    frontend_port: int = 3000,
    startup_timeout_ms: int = 60_000,
    shutdown_timeout_ms: int = 10_000,
    pnpm_executable: str = "pnpm",
) -> dict[str, Any]:
    if sys.platform != "linux":
        raise LinuxDesktopBuildError("linux_desktop_build_requires_linux")
    repository = _ordinary_directory(repo_root, code="linux_desktop_repository_invalid")
    _outside_repository(payload_dir.absolute(), repository)
    _outside_repository(output_dir.absolute(), repository)
    payload_parent = _ordinary_directory(
        payload_dir.absolute().parent, code="linux_desktop_payload_parent_invalid"
    )
    output = _ordinary_directory(output_dir, code="linux_desktop_output_invalid")
    if payload_dir.exists():
        raise LinuxDesktopBuildError("linux_desktop_payload_exists")
    if output_dir.absolute().resolve() == repository:
        raise LinuxDesktopBuildError("linux_desktop_output_invalid")
    if payload_dir.absolute().resolve() == output:
        raise LinuxDesktopBuildError("linux_desktop_output_paths_must_differ")
    if not electron_zip_sha256.isascii() or len(electron_zip_sha256) != 64:
        raise LinuxDesktopBuildError("linux_desktop_electron_sha_invalid")
    try:
        int(electron_zip_sha256, 16)
    except ValueError as exc:
        raise LinuxDesktopBuildError("linux_desktop_electron_sha_invalid") from exc

    frontend_standalone = _ordinary_directory(
        frontend_standalone_dir, code="linux_desktop_frontend_standalone_invalid"
    )
    frontend_static = _ordinary_directory(
        frontend_static_dir, code="linux_desktop_frontend_static_invalid"
    )
    frontend_public = _ordinary_directory(
        frontend_public_dir, code="linux_desktop_frontend_public_invalid"
    )
    desktop_dist = _ordinary_directory(
        desktop_dist_dir, code="linux_desktop_dist_invalid"
    )
    desktop_project = _ordinary_directory(
        desktop_project_dir, code="linux_desktop_project_invalid"
    )
    runtime_host = _ordinary_file(
        runtime_host_script, code="linux_desktop_runtime_host_invalid"
    )
    backend = _ordinary_file(
        backend_executable, code="linux_desktop_backend_invalid", executable=True
    )
    node = _ordinary_file(
        node_executable, code="linux_desktop_node_invalid", executable=True
    )
    electron_dir = _ordinary_directory(
        electron_zip_dir, code="linux_desktop_electron_dir_invalid"
    )
    electron_zip = _ordinary_file(
        electron_dir / ELECTRON_ZIP_NAME,
        code="linux_desktop_electron_zip_invalid",
    )
    if _sha256(electron_zip) != electron_zip_sha256:
        raise LinuxDesktopBuildError("linux_desktop_electron_zip_digest_mismatch")
    if electron_zip_sha256 != ELECTRON_ZIP_SHA256:
        raise LinuxDesktopBuildError("linux_desktop_electron_zip_not_pinned")

    component_bundle = _ordinary_directory(
        component_bundle_dir, code="linux_desktop_component_bundle_invalid"
    )
    _outside_repository(component_bundle, repository)
    try:
        component_report = validate_component_bundle(component_bundle)
    except LinuxPayloadError as exc:
        raise LinuxDesktopBuildError("linux_desktop_component_bundle_invalid") from exc
    for value in (component_bundle_sha256, component_bundle_tree_sha256):
        if not value.isascii() or len(value) != 64:
            raise LinuxDesktopBuildError("linux_desktop_component_bundle_sha_invalid")
        try:
            int(value, 16)
        except ValueError as exc:
            raise LinuxDesktopBuildError(
                "linux_desktop_component_bundle_sha_invalid"
            ) from exc
    if (
        component_report["bundle_sha256"] != component_bundle_sha256
        or component_report["tree_sha256"] != component_bundle_tree_sha256
    ):
        raise LinuxDesktopBuildError("linux_desktop_component_bundle_digest_mismatch")

    try:
        payload_result = build_linux_payload(
            frontend_standalone_dir=frontend_standalone,
            frontend_static_dir=frontend_static,
            frontend_public_dir=frontend_public,
            desktop_dist_dir=desktop_dist,
            runtime_host_script=runtime_host,
            backend_executable=backend,
            node_executable=node,
            desktop_project_dir=desktop_project,
            output_dir=payload_dir,
            component_bundle_dir=component_bundle,
            application_version=application_version,
            backend_port=backend_port,
            frontend_port=frontend_port,
            startup_timeout_ms=startup_timeout_ms,
            shutdown_timeout_ms=shutdown_timeout_ms,
        )
    except LinuxPayloadError as exc:
        raise LinuxDesktopBuildError(str(exc)) from exc

    staged_desktop = payload_result.output_dir / "desktop-build" / "project"
    _run(
        [pnpm_executable, "install", "--frozen-lockfile", "--offline"],
        cwd=staged_desktop,
        timeout=600,
        label="staged_install",
    )
    _run(
        [pnpm_executable, "build"],
        cwd=staged_desktop,
        timeout=600,
        label="staged_build",
    )
    staged_entrypoint = _ordinary_file(
        staged_desktop / "dist" / "main.js",
        code="linux_desktop_staged_entrypoint_invalid",
    )

    _run(
        [
            str(node),
            str(repository / DESKTOP_PACKAGER),
            "--app-dir",
            str(staged_desktop),
            "--electron-zip-dir",
            str(electron_dir),
            "--electron-zip-sha256",
            electron_zip_sha256,
            "--runtime-dir",
            str(payload_result.output_dir / "runtime"),
            "--output-dir",
            str(output),
            "--version",
            application_version,
        ],
        cwd=repository,
        timeout=1800,
        label="electron_package",
    )
    packaged = _ordinary_directory(
        output / "OmniBase-linux-x64", code="linux_desktop_packaged_output_invalid"
    )
    _ordinary_file(
        packaged / "OmniBase",
        code="linux_desktop_packaged_entrypoint_invalid",
        executable=True,
    )
    app_asar = _ordinary_file(
        packaged / "resources" / "app.asar",
        code="linux_desktop_packaged_asar_invalid",
    )
    try:
        copied_component_report = validate_component_bundle(
            payload_result.output_dir / "runtime/components"
        )
    except LinuxPayloadError as exc:
        raise LinuxDesktopBuildError("linux_desktop_component_bundle_invalid") from exc
    if copied_component_report != component_report:
        raise LinuxDesktopBuildError("linux_desktop_component_bundle_changed")
    report = {
        "application_version": application_version,
        "electron_zip_sha256": electron_zip_sha256,
        "payload_dir": str(payload_result.output_dir),
        "output_dir": str(packaged),
        "runtime_manifest_sha256": payload_result.runtime_manifest_sha256,
        "runtime_file_count": payload_result.runtime_file_count,
        "runtime_total_bytes": payload_result.runtime_total_bytes,
        "staged_entrypoint_sha256": _sha256(staged_entrypoint),
        "app_asar_sha256": _sha256(app_asar),
        "component_bundle": copied_component_report,
        "distribution_package": False,
        "lifecycle_accepted": False,
    }
    raw = json.dumps(report, ensure_ascii=True, sort_keys=True, indent=2).encode(
        "utf-8"
    )
    if len(raw) > MAX_REPORT_BYTES:
        raise LinuxDesktopBuildError("linux_desktop_report_too_large")
    report_path = payload_parent / f"{payload_dir.name}-report.json"
    try:
        with report_path.open("xb") as handle:
            handle.write(raw + b"\n")
    except FileExistsError as exc:
        raise LinuxDesktopBuildError("linux_desktop_report_exists") from exc
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--frontend-standalone-dir", type=Path, required=True)
    parser.add_argument("--frontend-static-dir", type=Path, required=True)
    parser.add_argument("--frontend-public-dir", type=Path, required=True)
    parser.add_argument("--desktop-dist-dir", type=Path, required=True)
    parser.add_argument("--runtime-host-script", type=Path, required=True)
    parser.add_argument("--backend-executable", type=Path, required=True)
    parser.add_argument("--node-executable", type=Path, required=True)
    parser.add_argument("--desktop-project-dir", type=Path, required=True)
    parser.add_argument("--electron-zip-dir", type=Path, required=True)
    parser.add_argument("--electron-zip-sha256", required=True)
    parser.add_argument("--payload-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--component-bundle-dir", type=Path, required=True)
    parser.add_argument("--component-bundle-sha256", required=True)
    parser.add_argument("--component-bundle-tree-sha256", required=True)
    parser.add_argument("--application-version", default="1.0.0")
    parser.add_argument("--backend-port", type=int, default=8765)
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--startup-timeout-ms", type=int, default=60_000)
    parser.add_argument("--shutdown-timeout-ms", type=int, default=10_000)
    parser.add_argument("--pnpm-executable", default="pnpm")
    args = parser.parse_args()
    try:
        result = build_linux_desktop(**vars(args))
    except (LinuxDesktopBuildError, LinuxPayloadError, OSError, UnicodeError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
