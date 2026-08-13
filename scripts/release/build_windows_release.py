"""Build and verify the deterministic OmniBase Windows ZIP release root."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import zipfile
from pathlib import Path, PurePosixPath
from typing import NamedTuple

_FILES = (
    "deployment/release/windows/README.zh-CN.md",
    "deployment/release/windows/compose.yml",
    "deployment/release/windows/operator.env.template",
    "scripts/release/validate_windows_release_config.py",
    "LICENSE",
)
_FORBIDDEN = (".env", "node_modules", ".next", ".venv", ".vhdx", ".db", ".sqlite")
_EPOCH = (1980, 1, 1, 0, 0, 0)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SECRET_MARKERS = (
    b"-----BEGIN PRIVATE KEY-----",
    b"-----BEGIN OPENSSH PRIVATE KEY-----",
    b"-----BEGIN RSA PRIVATE KEY-----",
)
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SENSITIVE_TEMPLATE_KEYS = frozenset(
    {
        "POSTGRES_PASSWORD",
        "DATABASE_URL",
        "REDIS_PASSWORD",
        "REDIS_URL",
        "MINIO_ROOT_PASSWORD",
        "JWT_SECRET",
        "PROVIDER_CREDENTIAL_ENCRYPTION_KEY",
        "MEMORY_CONTENT_ENCRYPTION_KEY",
    }
)


class ReleaseBuildError(ValueError):
    pass


class RepositoryState(NamedTuple):
    head: str
    clean: bool


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _git_environment() -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_REPLACE_OBJECTS": "1",
    }


def _repository_state(root: Path) -> RepositoryState:
    environment = _git_environment()

    def run(*arguments: str) -> str:
        completed = subprocess.run(
            [
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
                str(root),
                *arguments,
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=10,
            env=environment,
        )
        return completed.stdout.strip()

    return RepositoryState(
        head=run("rev-parse", "--verify", "HEAD"),
        clean=not bool(run("status", "--porcelain=v1", "--untracked-files=all")),
    )


def _read_committed_file(root: Path, source_commit: str, relative: str) -> bytes:
    try:
        completed = subprocess.run(
            [
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
                str(root),
                "show",
                f"{source_commit}:{relative}",
            ],
            check=True,
            capture_output=True,
            timeout=10,
            env=_git_environment(),
        )
    except subprocess.SubprocessError as exc:
        raise ReleaseBuildError(f"release_committed_source_unavailable:{relative}") from exc
    return completed.stdout


def _validate_template(raw: bytes) -> None:
    if any(marker in raw for marker in _SECRET_MARKERS):
        raise ReleaseBuildError("release_secret_material_detected")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseBuildError("release_template_not_utf8") from exc
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name in _SENSITIVE_TEMPLATE_KEYS and "REPLACE_WITH_" not in value:
            raise ReleaseBuildError(f"release_template_secret_not_placeholder:{name}")


def _read_release_files(
    root: Path,
    source_commit: str,
    repository_state: RepositoryState | None,
) -> list[tuple[str, bytes]]:
    files: list[tuple[str, bytes]] = []
    for relative in _FILES:
        path = root / PurePosixPath(relative)
        metadata = os.stat(path, follow_symlinks=False)
        if (
            stat.S_ISLNK(metadata.st_mode)
            or not stat.S_ISREG(metadata.st_mode)
            or bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)
        ):
            raise ReleaseBuildError("release_input_not_regular")
        normalized = relative.casefold()
        if any(marker in PurePosixPath(normalized).parts for marker in _FORBIDDEN):
            raise ReleaseBuildError("release_forbidden_path")
        raw = (
            path.read_bytes()
            if repository_state is not None
            else _read_committed_file(root, source_commit, relative)
        )
        if any(marker in raw for marker in _SECRET_MARKERS):
            raise ReleaseBuildError("release_secret_material_detected")
        if relative.endswith("operator.env.template"):
            _validate_template(raw)
        files.append((relative, raw))
    return files


def build_release(
    repo_root: Path,
    output: Path,
    *,
    source_commit: str,
    repository_state: RepositoryState | None = None,
) -> dict[str, object]:
    root = repo_root.resolve()
    if _COMMIT.fullmatch(source_commit) is None:
        raise ReleaseBuildError("release_source_commit_invalid")
    state = repository_state if repository_state is not None else _repository_state(root)
    if not state.clean:
        raise ReleaseBuildError("release_repository_must_be_clean")
    if state.head != source_commit:
        raise ReleaseBuildError("release_source_commit_not_current_head")
    if output.resolve(strict=False).is_relative_to(root):
        raise ReleaseBuildError("release_output_must_be_outside_repository")
    if output.exists():
        raise ReleaseBuildError("release_output_exists")
    files = _read_release_files(root, source_commit, repository_state)
    if repository_state is None and _repository_state(root) != state:
        raise ReleaseBuildError("release_repository_changed_during_build")
    manifest = {
        "schema_version": 1,
        "product": "OmniBase",
        "release": "v1.0.0-preview",
        "platform": "windows-x64",
        "source_commit": source_commit,
        "production_ready": False,
        "requires_digest_pinned_images": True,
        "publisher_signature_verified": False,
        "authenticode_verified": False,
        "vhdx_mutation_allowed": False,
        "files": [{"path": name, "sha256": _digest(raw), "size": len(raw)} for name, raw in files],
    }
    manifest_raw = (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    files.append(("release.json", manifest_raw))
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_STORED) as archive:
        for name, raw in sorted(files):
            info = zipfile.ZipInfo(name, date_time=_EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            info.create_system = 3
            archive.writestr(info, raw)
    return {
        "zip": str(output),
        "sha256": _digest(output.read_bytes()),
        "manifest": manifest,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    try:
        result = build_release(args.repo_root, args.output, source_commit=args.source_commit)
    except (OSError, subprocess.SubprocessError, ReleaseBuildError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
