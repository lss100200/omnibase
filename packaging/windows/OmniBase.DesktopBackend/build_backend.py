"""Build the minimal desktop-local backend as a closed Windows onedir bundle."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import os
import platform
import stat
import subprocess
import sys
from pathlib import Path

EXPECTED_PACKAGES = {
    "fastapi": "0.116.2",
    "pyinstaller": "6.22.2",
    "uvicorn": "0.33.0",
}
EXPECTED_OUTPUT_NAME = "OmniBase.Desktop.Backend"
EXPECTED_EXECUTABLE = "OmniBase.Desktop.Backend.exe"
MAX_FILES = 512
MAX_TOTAL_BYTES = 512 * 1024 * 1024
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_FORBIDDEN_SUFFIXES = frozenset(
    {
        ".db",
        ".env",
        ".key",
        ".p12",
        ".pdb",
        ".pem",
        ".pfx",
        ".sqlite",
        ".sqlite3",
        ".vdi",
        ".vhd",
        ".vhdx",
        ".vmdk",
    }
)


class BackendBuildError(ValueError):
    """A stable, path-redacted backend build failure."""


def _ordinary_directory(path: Path, *, must_exist: bool, code: str) -> Path:
    absolute = path.absolute()
    if not absolute.is_absolute():
        raise BackendBuildError(code)
    if not must_exist:
        if absolute.exists() or absolute.is_symlink():
            raise BackendBuildError(code)
        return absolute
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError:
        raise BackendBuildError(code) from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)
        or os.path.normcase(str(absolute)) != os.path.normcase(str(resolved))
    ):
        raise BackendBuildError(code)
    return absolute


def _require_outside_repository(path: Path, repository: Path) -> None:
    try:
        path.relative_to(repository)
    except ValueError:
        return
    raise BackendBuildError("desktop_backend_output_inside_repository")


def _verify_build_runtime() -> None:
    if (
        os.name != "nt"
        or sys.version_info[:2] != (3, 12)
        or sys.maxsize <= 2**32
        or platform.machine().casefold() not in {"amd64", "x86_64"}
    ):
        raise BackendBuildError("desktop_backend_build_runtime_unsupported")
    for package, expected in EXPECTED_PACKAGES.items():
        try:
            actual = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            raise BackendBuildError(
                "desktop_backend_build_dependency_missing"
            ) from None
        if actual != expected:
            raise BackendBuildError("desktop_backend_build_dependency_version_mismatch")


def _build_environment(work_dir: Path) -> dict[str, str]:
    windows = os.environ.get("SystemRoot") or os.environ.get("WINDIR")
    temporary = os.environ.get("TEMP") or os.environ.get("TMP")
    user_profile = os.environ.get("USERPROFILE")
    if not windows or not temporary or not user_profile:
        raise BackendBuildError("desktop_backend_build_environment_invalid")
    system32 = str(Path(windows) / "System32")
    return {
        "PATH": os.pathsep.join((str(Path(sys.executable).parent), system32)),
        "PYINSTALLER_CONFIG_DIR": str(work_dir.parent / ".pyinstaller-cache"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONUTF8": "1",
        "SystemRoot": windows,
        "TEMP": temporary,
        "TMP": temporary,
        "USERPROFILE": user_profile,
        "WINDIR": windows,
    }


def _validate_output(publish_dir: Path) -> tuple[int, int]:
    try:
        root_metadata = os.stat(publish_dir, follow_symlinks=False)
    except OSError:
        raise BackendBuildError("desktop_backend_publish_missing") from None
    if (
        not stat.S_ISDIR(root_metadata.st_mode)
        or stat.S_ISLNK(root_metadata.st_mode)
        or bool(getattr(root_metadata, "st_file_attributes", 0) & _REPARSE_FLAG)
    ):
        raise BackendBuildError("desktop_backend_publish_identity_invalid")

    count = 0
    total = 0
    executable_seen = False
    for directory, directory_names, file_names in os.walk(publish_dir):
        directory_names.sort(key=lambda value: (value.casefold(), value))
        file_names.sort(key=lambda value: (value.casefold(), value))
        for name in (*directory_names, *file_names):
            target = Path(directory) / name
            metadata = os.stat(target, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) or bool(
                getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG
            ):
                raise BackendBuildError("desktop_backend_publish_link_forbidden")
            if (
                name.casefold() == ".env"
                or Path(name).suffix.casefold() in _FORBIDDEN_SUFFIXES
            ):
                raise BackendBuildError(
                    "desktop_backend_publish_sensitive_path_forbidden"
                )
        for name in file_names:
            target = Path(directory) / name
            metadata = os.stat(target, follow_symlinks=False)
            relative = target.relative_to(publish_dir)
            empty_wheel_marker = (
                metadata.st_size == 0
                and len(relative.parts) == 3
                and relative.parts[0] == "_internal"
                and relative.parts[1].casefold().endswith(".dist-info")
                and relative.parts[2] == "REQUESTED"
            )
            if not stat.S_ISREG(metadata.st_mode) or (
                metadata.st_size <= 0 and not empty_wheel_marker
            ):
                raise BackendBuildError("desktop_backend_publish_file_invalid")
            count += 1
            total += metadata.st_size
            if count > MAX_FILES or total > MAX_TOTAL_BYTES:
                raise BackendBuildError("desktop_backend_publish_budget_exceeded")
            if target == publish_dir / EXPECTED_EXECUTABLE:
                executable_seen = True
    if not executable_seen:
        raise BackendBuildError("desktop_backend_publish_entrypoint_missing")
    return count, total


def build_backend(repo_root: Path, dist_dir: Path, work_dir: Path) -> dict[str, object]:
    _verify_build_runtime()
    repository = _ordinary_directory(
        repo_root, must_exist=True, code="desktop_backend_repository_invalid"
    )
    distribution = _ordinary_directory(
        dist_dir, must_exist=False, code="desktop_backend_dist_path_invalid"
    )
    work = _ordinary_directory(
        work_dir, must_exist=False, code="desktop_backend_work_path_invalid"
    )
    _require_outside_repository(distribution, repository)
    _require_outside_repository(work, repository)
    if os.path.normcase(str(distribution)) == os.path.normcase(str(work)):
        raise BackendBuildError("desktop_backend_build_paths_must_differ")
    _ordinary_directory(
        distribution.parent,
        must_exist=True,
        code="desktop_backend_dist_parent_invalid",
    )
    _ordinary_directory(
        work.parent, must_exist=True, code="desktop_backend_work_parent_invalid"
    )

    spec = (
        repository
        / "packaging"
        / "windows"
        / "OmniBase.DesktopBackend"
        / "OmniBase.Desktop.Backend.spec"
    )
    if not spec.is_file() or spec.is_symlink():
        raise BackendBuildError("desktop_backend_spec_invalid")
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--distpath",
        str(distribution),
        "--workpath",
        str(work),
        "--log-level",
        "WARN",
        str(spec),
    ]
    try:
        subprocess.run(
            command,
            cwd=repository,
            env=_build_environment(work),
            check=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError):
        raise BackendBuildError("desktop_backend_packager_failed") from None

    publish = distribution / EXPECTED_OUTPUT_NAME
    file_count, total_bytes = _validate_output(publish)
    return {
        "publish_dir": str(publish),
        "entrypoint": str(publish / EXPECTED_EXECUTABLE),
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build_backend(args.repo_root, args.dist_dir, args.work_dir)
    except BackendBuildError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
