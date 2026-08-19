"""Build one unsigned P6.5 Windows desktop installer outside the repository.

This orchestrator composes the focused source-owned builders. It never signs,
installs, uninstalls, starts Docker/WSL/Hyper-V, reads dotenv files, or removes
an existing path. Every failure retains its exclusive artifact root for review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Callable, NamedTuple, Sequence

APPLICATION_VERSION = "1.0.0"
EXPECTED_DOTNET_SDK = "8.0.424"
EXPECTED_NODE_VERSION = "v24.14.0"
EXPECTED_PNPM_VERSION = "9.12.3"
EXPECTED_PYTHON_VERSION = "3.12.10"
NODE_EXECUTABLE_NAME = "node.exe"
NODE_EXECUTABLE_SIZE = 91_380_224
NODE_EXECUTABLE_SHA256 = (
    "63c259c81e5d472b5f11c8d506070130cb04a1ecf84b80377a34ed6ec9048088"
)
ELECTRON_ZIP_NAME = "electron-v43.4.0-win32-x64.zip"
ELECTRON_ZIP_SIZE = 144_408_141
ELECTRON_ZIP_SHA256 = "ef0709cfa719739acce73de6f9b684304baf38c6454376638a70d34a7cecffe0"
ENGINEERING_CONFIRMATION = "I_UNDERSTAND_THIS_IS_AN_UNSIGNED_ENGINEERING_BUILD"
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_INSTALLER_SOURCE_FILES = (
    "bundle/Bundle.wxs",
    "bundle/OmniBase.Bundle.wixproj",
    "package/Product.wxs",
    "package/OmniBase.Package.wixproj",
    "tools/validate_payload.py",
)
_MAX_INSTALLER_SOURCE_FILE_BYTES = 1024 * 1024


class DesktopReleaseError(ValueError):
    """A stable P6.5 build failure without physical paths or command output."""


class RepositoryState(NamedTuple):
    head: str
    status: bytes

    @property
    def clean(self) -> bool:
        return self.status == b""


RunCommand = Callable[
    [str, Sequence[str], Path, dict[str, str], int, bool],
    subprocess.CompletedProcess[str],
]


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _file_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _ordinary_directory(path: Path, *, exists: bool, code: str) -> Path:
    absolute = path.absolute()
    if not absolute.is_absolute() or absolute == Path(absolute.anchor):
        raise DesktopReleaseError(code)
    if not exists:
        if absolute.exists() or absolute.is_symlink():
            raise DesktopReleaseError(code)
        return absolute
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError:
        raise DesktopReleaseError(code) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or not _same_path(absolute, resolved)
    ):
        raise DesktopReleaseError(code)
    return absolute


def _ordinary_file(path: Path, *, code: str) -> Path:
    absolute = path.absolute()
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError:
        raise DesktopReleaseError(code) from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or not _same_path(absolute, resolved)
    ):
        raise DesktopReleaseError(code)
    return absolute


def _built_output_file(path: Path, *, code: str) -> Path:
    """Accept an exact build output before promotion, including SDK hardlinks."""
    absolute = path.absolute()
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError:
        raise DesktopReleaseError(code) from None
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or metadata.st_nlink < 1
        or metadata.st_size <= 0
        or not _same_path(absolute, resolved)
    ):
        raise DesktopReleaseError(code)
    return absolute


def _outside_repository(path: Path, repository: Path) -> None:
    try:
        path.relative_to(repository)
    except ValueError:
        return
    raise DesktopReleaseError("desktop_release_artifact_root_inside_repository")


def _safe_environment(artifact_root: Path) -> dict[str, str]:
    allowed = (
        "APPDATA",
        "CommonProgramFiles",
        "CommonProgramFiles(x86)",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "PATH",
        "ProgramFiles",
        "ProgramFiles(x86)",
        "PROGRAMDATA",
        "SystemRoot",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    )
    environment = {
        name: value
        for name in allowed
        if (value := os.environ.get(name)) is not None
        and value
        and "\x00" not in value
        and "\r" not in value
        and "\n" not in value
    }
    if not {"PATH", "SystemRoot", "TEMP", "USERPROFILE"}.issubset(environment):
        raise DesktopReleaseError("desktop_release_build_environment_invalid")
    environment.update(
        {
            "CI": "1",
            "COREPACK_ENABLE_DOWNLOAD_PROMPT": "0",
            "DOTNET_CLI_HOME": str(artifact_root / ".dotnet-home"),
            "DOTNET_CLI_TELEMETRY_OPTOUT": "1",
            "DOTNET_NOLOGO": "1",
            "NEXT_TELEMETRY_DISABLED": "1",
            "NUGET_PACKAGES": str(artifact_root / ".nuget-packages"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONUTF8": "1",
        }
    )
    return environment


def _git_environment() -> dict[str, str]:
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "SystemRoot": os.environ.get("SystemRoot", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }
    return environment


def _repository_state(repository: Path) -> RepositoryState:
    prefix = [
        "git",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=",
        "-c",
        "core.pager=cat",
        "-c",
        "diff.external=",
        "-c",
        "credential.helper=",
        "-C",
        str(repository),
    ]
    try:
        head = (
            subprocess.run(
                [*prefix, "rev-parse", "--verify", "HEAD"],
                check=True,
                capture_output=True,
                timeout=15,
                env=_git_environment(),
            )
            .stdout.decode("ascii")
            .strip()
        )
        status = subprocess.run(
            [*prefix, "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            check=True,
            capture_output=True,
            timeout=60,
            env=_git_environment(),
        ).stdout
    except (OSError, subprocess.SubprocessError, UnicodeDecodeError):
        raise DesktopReleaseError("desktop_release_git_state_unavailable") from None
    if _COMMIT.fullmatch(head) is None:
        raise DesktopReleaseError("desktop_release_git_head_invalid")
    return RepositoryState(head, status)


def _validate_source_mode(
    state: RepositoryState, source_mode: str, engineering_confirmation: str | None
) -> None:
    if source_mode == "clean-release":
        if not state.clean:
            raise DesktopReleaseError("desktop_release_repository_must_be_clean")
        if engineering_confirmation is not None:
            raise DesktopReleaseError(
                "desktop_release_engineering_confirmation_forbidden"
            )
        return
    if source_mode != "engineering-dirty":
        raise DesktopReleaseError("desktop_release_source_mode_invalid")
    if engineering_confirmation != ENGINEERING_CONFIRMATION:
        raise DesktopReleaseError("desktop_release_engineering_confirmation_required")


def _run_command(
    name: str,
    command: Sequence[str],
    cwd: Path,
    environment: dict[str, str],
    timeout: int,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=cwd,
            env=environment,
            check=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=capture,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        raise DesktopReleaseError(f"desktop_release_step_failed:{name}") from None


def _version_output(
    runner: RunCommand,
    *,
    name: str,
    command: Sequence[str],
    cwd: Path,
    environment: dict[str, str],
    expected: str,
) -> None:
    completed = runner(name, command, cwd, environment, 60, True)
    if completed.stdout.strip() != expected or completed.stderr.strip():
        raise DesktopReleaseError(f"desktop_release_{name}_version_mismatch")


def _copy_installer_source(source: Path, destination: Path) -> None:
    source_root = _ordinary_directory(
        source, exists=True, code="desktop_release_installer_source_invalid"
    )
    if destination.exists() or destination.is_symlink():
        raise DesktopReleaseError("desktop_release_installer_staging_exists")
    destination.mkdir()
    for relative in _INSTALLER_SOURCE_FILES:
        source_file = _ordinary_file(
            source_root / relative,
            code="desktop_release_installer_source_file_invalid",
        )
        before = os.stat(source_file, follow_symlinks=False)
        if before.st_size > _MAX_INSTALLER_SOURCE_FILE_BYTES:
            raise DesktopReleaseError("desktop_release_installer_source_budget")
        with source_file.open("rb") as input_file:
            if _file_identity(os.fstat(input_file.fileno())) != _file_identity(before):
                raise DesktopReleaseError("desktop_release_installer_source_changed")
            raw = input_file.read(_MAX_INSTALLER_SOURCE_FILE_BYTES + 1)
            if _file_identity(os.fstat(input_file.fileno())) != _file_identity(before):
                raise DesktopReleaseError("desktop_release_installer_source_changed")
        if len(raw) != before.st_size:
            raise DesktopReleaseError("desktop_release_installer_source_changed")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as output:
            output.write(raw)


def _copy_verified_input(
    source: Path,
    destination: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> str:
    source_file = _ordinary_file(source, code="desktop_release_verified_input_invalid")
    _ordinary_directory(
        source_file.parent,
        exists=True,
        code="desktop_release_verified_input_parent_invalid",
    )
    before = os.stat(source_file, follow_symlinks=False)
    if before.st_size != expected_size:
        raise DesktopReleaseError("desktop_release_verified_input_size_mismatch")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    digest = hashlib.sha256()
    copied = 0
    try:
        descriptor = os.open(source_file, flags)
        with (
            os.fdopen(descriptor, "rb", closefd=True) as input_file,
            destination.open("xb") as output,
        ):
            if _file_identity(os.fstat(input_file.fileno())) != _file_identity(before):
                raise DesktopReleaseError("desktop_release_verified_input_changed")
            while chunk := input_file.read(1024 * 1024):
                copied += len(chunk)
                if copied > expected_size:
                    raise DesktopReleaseError("desktop_release_verified_input_changed")
                digest.update(chunk)
                output.write(chunk)
            if _file_identity(os.fstat(input_file.fileno())) != _file_identity(before):
                raise DesktopReleaseError("desktop_release_verified_input_changed")
    except OSError:
        raise DesktopReleaseError(
            "desktop_release_verified_input_copy_failed"
        ) from None
    actual_sha256 = digest.hexdigest()
    if copied != expected_size or actual_sha256 != expected_sha256:
        raise DesktopReleaseError("desktop_release_verified_input_digest_mismatch")
    return actual_sha256


def _verify_digest_bound_file(
    path: Path, *, expected_size: int, expected_sha256: str
) -> str:
    source_file = _ordinary_file(path, code="desktop_release_verified_file_invalid")
    before = os.stat(source_file, follow_symlinks=False)
    if before.st_size != expected_size:
        raise DesktopReleaseError("desktop_release_verified_file_size_mismatch")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    digest = hashlib.sha256()
    read_bytes = 0
    try:
        descriptor = os.open(source_file, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as input_file:
            if _file_identity(os.fstat(input_file.fileno())) != _file_identity(before):
                raise DesktopReleaseError("desktop_release_verified_file_changed")
            while chunk := input_file.read(1024 * 1024):
                read_bytes += len(chunk)
                if read_bytes > expected_size:
                    raise DesktopReleaseError("desktop_release_verified_file_changed")
                digest.update(chunk)
            if _file_identity(os.fstat(input_file.fileno())) != _file_identity(before):
                raise DesktopReleaseError("desktop_release_verified_file_changed")
    except OSError:
        raise DesktopReleaseError("desktop_release_verified_file_read_failed") from None
    actual_sha256 = digest.hexdigest()
    if read_bytes != expected_size or actual_sha256 != expected_sha256:
        raise DesktopReleaseError("desktop_release_verified_file_digest_mismatch")
    return actual_sha256


def _promote_built_artifact(source: Path, destination: Path) -> Path:
    source_file = _built_output_file(
        source, code="desktop_release_installer_output_invalid"
    )
    before = os.stat(source_file, follow_symlinks=False)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    copied = 0
    try:
        descriptor = os.open(source_file, flags)
        with (
            os.fdopen(descriptor, "rb", closefd=True) as input_file,
            destination.open("xb") as output,
        ):
            if _file_identity(os.fstat(input_file.fileno())) != _file_identity(before):
                raise DesktopReleaseError("desktop_release_installer_output_changed")
            while chunk := input_file.read(1024 * 1024):
                copied += len(chunk)
                if copied > before.st_size:
                    raise DesktopReleaseError(
                        "desktop_release_installer_output_changed"
                    )
                output.write(chunk)
            if _file_identity(os.fstat(input_file.fileno())) != _file_identity(before):
                raise DesktopReleaseError("desktop_release_installer_output_changed")
    except OSError:
        raise DesktopReleaseError(
            "desktop_release_installer_promotion_failed"
        ) from None
    if copied != before.st_size:
        raise DesktopReleaseError("desktop_release_installer_output_changed")
    return _ordinary_file(
        destination, code="desktop_release_installer_promotion_invalid"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _final_installer_artifact(root: Path, project: str, name: str, suffix: str) -> Path:
    if (
        project not in {"bundle", "package"}
        or Path(name).name != name
        or Path(name).suffix.casefold() != suffix
    ):
        raise DesktopReleaseError("desktop_release_installer_output_invalid")
    return _built_output_file(
        root / project / "bin" / "Release" / name,
        code="desktop_release_installer_output_invalid",
    )


def _parse_payload_report(
    completed: subprocess.CompletedProcess[str], *, copied: bool
) -> dict[str, object]:
    if completed.stderr.strip():
        raise DesktopReleaseError("desktop_release_payload_report_invalid")
    try:
        value = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError):
        raise DesktopReleaseError("desktop_release_payload_report_invalid") from None
    expected_keys = {
        "file_count",
        "payload_copied",
        "total_bytes",
        "tree_sha256",
        "wix_authoring_written",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise DesktopReleaseError("desktop_release_payload_report_invalid")
    if (
        type(value["file_count"]) is not int
        or not 1 <= value["file_count"] <= 4096
        or type(value["total_bytes"]) is not int
        or not 0 < value["total_bytes"] <= 2 * 1024 * 1024 * 1024
        or type(value["tree_sha256"]) is not str
        or _SHA256.fullmatch(value["tree_sha256"]) is None
        or type(value["payload_copied"]) is not bool
        or value["payload_copied"] is not copied
        or type(value["wix_authoring_written"]) is not bool
        or value["wix_authoring_written"]
    ):
        raise DesktopReleaseError("desktop_release_payload_report_invalid")
    return value


def _write_report(path: Path, value: dict[str, object]) -> None:
    raw = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    with path.open("xb") as handle:
        handle.write(raw)


def build_windows_desktop(
    *,
    repo_root: Path,
    artifact_root: Path,
    dotnet_executable: Path,
    backend_python: Path,
    node_executable: Path,
    electron_zip: Path,
    version: str = APPLICATION_VERSION,
    source_mode: str = "clean-release",
    engineering_confirmation: str | None = None,
    runner: RunCommand = _run_command,
) -> dict[str, object]:
    if os.name != "nt":
        raise DesktopReleaseError("desktop_release_requires_windows")
    if _VERSION.fullmatch(version) is None or version != APPLICATION_VERSION:
        raise DesktopReleaseError("desktop_release_version_invalid")
    repository = _ordinary_directory(
        repo_root, exists=True, code="desktop_release_repository_invalid"
    )
    artifacts = _ordinary_directory(
        artifact_root, exists=False, code="desktop_release_artifact_root_invalid"
    )
    _outside_repository(artifacts, repository)
    _ordinary_directory(
        artifacts.parent,
        exists=True,
        code="desktop_release_artifact_parent_invalid",
    )
    dotnet = _ordinary_file(
        dotnet_executable, code="desktop_release_dotnet_executable_invalid"
    )
    python = _ordinary_file(
        backend_python, code="desktop_release_backend_python_invalid"
    )
    node = _ordinary_file(
        node_executable, code="desktop_release_node_executable_invalid"
    )
    if node.name.casefold() != NODE_EXECUTABLE_NAME:
        raise DesktopReleaseError("desktop_release_node_executable_name_invalid")
    electron_archive = _ordinary_file(
        electron_zip, code="desktop_release_electron_zip_invalid"
    )
    if electron_archive.name != ELECTRON_ZIP_NAME:
        raise DesktopReleaseError("desktop_release_electron_zip_name_invalid")
    corepack = _ordinary_file(
        node.parent / "corepack.cmd",
        code="desktop_release_corepack_executable_invalid",
    )
    initial_state = _repository_state(repository)
    _validate_source_mode(initial_state, source_mode, engineering_confirmation)

    artifacts.mkdir()
    node_input = artifacts / "node-input"
    node_input.mkdir()
    runtime_node = node_input / NODE_EXECUTABLE_NAME
    _copy_verified_input(
        node,
        runtime_node,
        expected_size=NODE_EXECUTABLE_SIZE,
        expected_sha256=NODE_EXECUTABLE_SHA256,
    )
    electron_input = artifacts / "electron-input"
    electron_input.mkdir()
    _copy_verified_input(
        electron_archive,
        electron_input / ELECTRON_ZIP_NAME,
        expected_size=ELECTRON_ZIP_SIZE,
        expected_sha256=ELECTRON_ZIP_SHA256,
    )
    environment = _safe_environment(artifacts)
    frontend = repository / "frontend"
    desktop = repository / "desktop"
    runtime_host = repository / "packaging/windows/OmniBase.RuntimeHost"
    runtime_host_tests = repository / "packaging/windows/OmniBase.RuntimeHost.Tests"
    for source in (frontend, desktop, runtime_host, runtime_host_tests):
        _ordinary_directory(
            source, exists=True, code="desktop_release_source_directory_invalid"
        )

    _version_output(
        runner,
        name="dotnet",
        command=[str(dotnet), "--version"],
        cwd=repository,
        environment=environment,
        expected=EXPECTED_DOTNET_SDK,
    )
    _version_output(
        runner,
        name="node",
        command=[str(runtime_node), "--version"],
        cwd=repository,
        environment=environment,
        expected=EXPECTED_NODE_VERSION,
    )
    _version_output(
        runner,
        name="python",
        command=[str(python), "--version"],
        cwd=repository,
        environment=environment,
        expected=f"Python {EXPECTED_PYTHON_VERSION}",
    )
    _version_output(
        runner,
        name="pnpm",
        command=[str(corepack), "pnpm", "--version"],
        cwd=frontend,
        environment=environment,
        expected=EXPECTED_PNPM_VERSION,
    )

    runner(
        "frontend-dependencies",
        [
            str(corepack),
            "pnpm",
            "install",
            "--frozen-lockfile",
            "--config.node-linker=hoisted",
            "--config.package-import-method=copy",
        ],
        frontend,
        environment,
        900,
        False,
    )
    runner(
        "frontend-build",
        [str(corepack), "pnpm", "build"],
        frontend,
        environment,
        600,
        False,
    )
    runner(
        "desktop-dependencies",
        [str(corepack), "pnpm", "install", "--frozen-lockfile"],
        desktop,
        environment,
        900,
        False,
    )
    runner(
        "desktop-build",
        [str(corepack), "pnpm", "build"],
        desktop,
        environment,
        300,
        False,
    )

    backend_dist = artifacts / "backend-dist"
    backend_work = artifacts / "backend-work"
    runner(
        "backend-freeze",
        [
            str(python),
            str(
                repository
                / "packaging/windows/OmniBase.DesktopBackend/build_backend.py"
            ),
            "--repo-root",
            str(repository),
            "--dist-dir",
            str(backend_dist),
            "--work-dir",
            str(backend_work),
        ],
        repository,
        environment,
        900,
        False,
    )

    runner(
        "runtime-host-tests",
        [
            str(dotnet),
            "run",
            "--project",
            str(runtime_host_tests / "OmniBase.RuntimeHost.Tests.csproj"),
            "-c",
            "Release",
        ],
        repository,
        environment,
        600,
        False,
    )
    runtime_publish = artifacts / "runtime-host-publish"
    runner(
        "runtime-host-publish",
        [
            str(dotnet),
            "publish",
            str(runtime_host / "OmniBase.RuntimeHost.csproj"),
            "-c",
            "Release",
            "-r",
            "win-x64",
            "--self-contained",
            "true",
            "--disable-build-servers",
            "-o",
            str(runtime_publish),
        ],
        repository,
        environment,
        900,
        False,
    )

    _verify_digest_bound_file(
        runtime_node,
        expected_size=NODE_EXECUTABLE_SIZE,
        expected_sha256=NODE_EXECUTABLE_SHA256,
    )
    payload = artifacts / "payload"
    runner(
        "payload-build",
        [
            str(python),
            str(repository / "scripts/release/build_p6_5_desktop_payload.py"),
            "--frontend-standalone-dir",
            str(frontend / ".next/standalone"),
            "--frontend-static-dir",
            str(frontend / ".next/static"),
            "--frontend-public-dir",
            str(frontend / "public"),
            "--desktop-dist-dir",
            str(desktop / "dist"),
            "--runtime-host-publish-dir",
            str(runtime_publish),
            "--backend-publish-dir",
            str(backend_dist / "OmniBase.Desktop.Backend"),
            "--node-executable",
            str(runtime_node),
            "--desktop-project-dir",
            str(desktop),
            "--output-dir",
            str(payload),
            "--application-version",
            version,
        ],
        repository,
        environment,
        900,
        False,
    )
    _verify_digest_bound_file(
        payload / "runtime/node/node.exe",
        expected_size=NODE_EXECUTABLE_SIZE,
        expected_sha256=NODE_EXECUTABLE_SHA256,
    )

    staged_desktop = payload / "desktop-build/project"
    runner(
        "staged-desktop-install",
        [
            str(corepack),
            "pnpm",
            "install",
            "--frozen-lockfile",
            "--offline",
        ],
        staged_desktop,
        environment,
        600,
        False,
    )
    runner(
        "staged-desktop-build",
        [str(corepack), "pnpm", "build"],
        staged_desktop,
        environment,
        300,
        False,
    )

    _verify_digest_bound_file(
        runtime_node,
        expected_size=NODE_EXECUTABLE_SIZE,
        expected_sha256=NODE_EXECUTABLE_SHA256,
    )
    electron_output = artifacts / "electron"
    electron_output.mkdir()
    runner(
        "electron-package",
        [
            str(runtime_node),
            str(desktop / "scripts/package-windows.mjs"),
            "--app-dir",
            str(staged_desktop),
            "--electron-zip-dir",
            str(electron_input),
            "--runtime-dir",
            str(payload / "runtime"),
            "--output-dir",
            str(electron_output),
            "--version",
            version,
        ],
        repository,
        environment,
        1800,
        False,
    )
    packaged_app = _ordinary_directory(
        electron_output / "OmniBase-win32-x64",
        exists=True,
        code="desktop_release_electron_output_invalid",
    )
    _verify_digest_bound_file(
        packaged_app / "resources/runtime/node/node.exe",
        expected_size=NODE_EXECUTABLE_SIZE,
        expected_sha256=NODE_EXECUTABLE_SHA256,
    )

    installer_source = artifacts / "installer-source"
    _copy_installer_source(
        repository / "packaging/windows/OmniBase.Installer", installer_source
    )
    installer_payload = artifacts / "installer-payload"
    staged_payload_report = _parse_payload_report(
        runner(
            "installer-payload-staging",
            [
                str(python),
                str(installer_source / "tools/validate_payload.py"),
                "--payload-root",
                str(packaged_app),
                "--copy-to",
                str(installer_payload),
            ],
            repository,
            environment,
            900,
            True,
        ),
        copied=True,
    )
    runner(
        "wix-build",
        [
            str(dotnet),
            "build",
            str(installer_source / "bundle/OmniBase.Bundle.wixproj"),
            "-c",
            "Release",
            "--disable-build-servers",
            f"-p:PayloadRoot={installer_payload}",
            f"-p:ProductVersion={version}",
            f"-p:PythonExecutable={python}",
        ],
        repository,
        environment,
        1800,
        False,
    )
    final_payload_report = _parse_payload_report(
        runner(
            "installer-payload-revalidation",
            [
                str(python),
                str(installer_source / "tools/validate_payload.py"),
                "--payload-root",
                str(installer_payload),
            ],
            repository,
            environment,
            900,
            True,
        ),
        copied=False,
    )
    for field in ("file_count", "total_bytes", "tree_sha256"):
        if final_payload_report[field] != staged_payload_report[field]:
            raise DesktopReleaseError("desktop_release_installer_payload_changed")
    _verify_digest_bound_file(
        installer_payload / "resources/runtime/node/node.exe",
        expected_size=NODE_EXECUTABLE_SIZE,
        expected_sha256=NODE_EXECUTABLE_SHA256,
    )

    setup_name = f"OmniBase-{version}-windows-x64-setup.exe"
    msi_name = f"OmniBase-{version}-windows-x64.msi"
    setup_source = _final_installer_artifact(
        installer_source, "bundle", setup_name, ".exe"
    )
    msi_source = _final_installer_artifact(
        installer_source, "package", msi_name, ".msi"
    )
    release = artifacts / "release"
    release.mkdir()
    setup = _promote_built_artifact(setup_source, release / setup_name)
    msi = _promote_built_artifact(msi_source, release / msi_name)

    final_state = _repository_state(repository)
    if final_state != initial_state:
        raise DesktopReleaseError("desktop_release_repository_changed_during_build")
    report: dict[str, object] = {
        "schema_version": 1,
        "product": "OmniBase",
        "version": version,
        "platform": "windows-x64",
        "source_commit": initial_state.head,
        "source_clean": initial_state.clean,
        "source_mode": source_mode,
        "node_runtime_input": {
            "name": NODE_EXECUTABLE_NAME,
            "size": NODE_EXECUTABLE_SIZE,
            "sha256": NODE_EXECUTABLE_SHA256,
        },
        "electron_runtime_input": {
            "name": ELECTRON_ZIP_NAME,
            "size": ELECTRON_ZIP_SIZE,
            "sha256": ELECTRON_ZIP_SHA256,
        },
        "installer_payload": {
            "file_count": staged_payload_report["file_count"],
            "total_bytes": staged_payload_report["total_bytes"],
            "tree_sha256": staged_payload_report["tree_sha256"],
        },
        "production_ready": False,
        "authenticode_verified": False,
        "clean_windows_lifecycle_verified": False,
        "required_product_journeys_verified": False,
        "artifacts": [
            {
                "kind": "burn_exe",
                "path": setup.relative_to(artifacts).as_posix(),
                "size": setup.stat().st_size,
                "sha256": _sha256(setup),
            },
            {
                "kind": "msi",
                "path": msi.relative_to(artifacts).as_posix(),
                "size": msi.stat().st_size,
                "sha256": _sha256(msi),
            },
        ],
    }
    _write_report(artifacts / "desktop-build-report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--dotnet-executable", type=Path, required=True)
    parser.add_argument("--backend-python", type=Path, required=True)
    parser.add_argument("--node-executable", type=Path, required=True)
    parser.add_argument("--electron-zip", type=Path, required=True)
    parser.add_argument("--version", default=APPLICATION_VERSION)
    parser.add_argument(
        "--source-mode",
        choices=("clean-release", "engineering-dirty"),
        default="clean-release",
    )
    parser.add_argument("--engineering-confirmation")
    args = parser.parse_args()
    try:
        result = build_windows_desktop(**vars(args))
    except DesktopReleaseError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
