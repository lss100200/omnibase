#!/usr/bin/env python3
"""Offline release controller for the P5 personal production target.

The controller only inspects local files, Git metadata, Docker/Compose version
commands and filesystem capacity.  It never loads the repository ``.env``,
starts Compose, contacts a database/provider, or enables a feature gate.
"""

from __future__ import annotations

import argparse
import ast
import base64
import getpass
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_VERSION = 1
DEFAULT_MIN_FREE_BYTES = 10 * 1024**3
ARTIFACT_PATHS = (
    "backend/Dockerfile.production",
    "frontend/Dockerfile",
    "deployment/personal-production/compose.yml",
    "deployment/personal-production/operator.env.example",
    "deployment/production/personal-runtime-canary.compose.example.yml",
    "scripts/production/manage_p5_personal_backup.py",
    "scripts/production/manage_p5_personal_runtime.py",
    "scripts/production/manage_p5_personal_target.py",
)
GATE_NAMES = (
    "AGENT_RUNTIME_ENABLED",
    "AGENT_PLANNER_ENABLED",
    "MULTI_AGENT_ENABLED",
)
REQUIRED_ENV_KEYS = (
    "OMNIBASE_FRONTEND_PORT",
    "POSTGRES_USER",
    "POSTGRES_PASSWORD",
    "POSTGRES_DB",
    "DATABASE_URL",
    "MINIO_ROOT_USER",
    "MINIO_ROOT_PASSWORD",
    "MINIO_BUCKET",
    "REDIS_PASSWORD",
    "REDIS_URL",
    "JWT_SECRET",
    "PROVIDER_CREDENTIAL_ENCRYPTION_KEY",
    "PROVIDER_ENDPOINT_ALLOWLIST",
    "OMNIBASE_DEPLOYMENT_INSTANCE_ID",
    "CORS_ORIGINS",
)
SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "REDIS_PASSWORD",
    "JWT_SECRET",
    "PROVIDER_CREDENTIAL_ENCRYPTION_KEY",
)
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
_OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PUBLIC_REMOTE_REF = re.compile(
    r"^refs/remotes/[^\x00-\x20\x7f~^:?*\[\\/]+/[^\x00-\x20\x7f~^:?*\[\\]+$"
)
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:[\\/]")
_PLACEHOLDER_PARTS = (
    "changeme",
    "change_me",
    "example",
    "placeholder",
    "please_generate",
    "replace_me",
    "replace-with",
    "todo",
    "your_",
)


class TargetConfigurationError(RuntimeError):
    """A target precondition failed closed."""


@dataclass(frozen=True)
class TargetPaths:
    release_dir: Path
    state_dir: Path
    backup_dir: Path
    secret_env: Path


def _canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _emit(payload: dict[str, object]) -> None:
    sys.stdout.buffer.write(_canonical_bytes(payload))


def _run(argv: list[str], *, cwd: Path, timeout: float = 5.0) -> str:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TargetConfigurationError(f"command unavailable: {argv[0]}") from exc
    if completed.returncode != 0:
        raise TargetConfigurationError(f"command failed: {argv[0]}")
    output = completed.stdout.strip()
    if not output or "\n" in output or "\r" in output or len(output) > 512:
        raise TargetConfigurationError(f"unexpected command output: {argv[0]}")
    return output


def _resolve_absolute(value: str | Path, *, name: str, must_exist: bool = True) -> Path:
    raw = str(value)
    if os.name != "nt" and _WINDOWS_ABSOLUTE.match(raw):
        raise TargetConfigurationError(f"{name} uses a foreign Windows path")
    path = Path(raw)
    if not path.is_absolute():
        raise TargetConfigurationError(f"{name} must be a host-native absolute path")
    try:
        return path.resolve(strict=must_exist)
    except OSError as exc:
        raise TargetConfigurationError(f"{name} does not resolve") from exc


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _operator_directory(value: str | Path, *, name: str, repo_root: Path) -> Path:
    _reject_link_components(Path(str(value)), name=name)
    path = _resolve_absolute(value, name=name)
    if not path.is_dir() or path.is_symlink():
        raise TargetConfigurationError(f"{name} must be a real directory")
    if _is_within(path, repo_root) or path == repo_root:
        raise TargetConfigurationError(f"{name} must be outside the repository")
    return path


def _reject_link_components(path: Path, *, name: str) -> None:
    cursor = path
    while True:
        is_junction = getattr(cursor, "is_junction", lambda: False)
        if cursor.exists() and (cursor.is_symlink() or is_junction()):
            raise TargetConfigurationError(
                f"{name} must not traverse a symlink or junction"
            )
        if cursor.parent == cursor:
            return
        cursor = cursor.parent


def _parse_env(raw: bytes) -> dict[str, str]:
    if len(raw) > 128 * 1024 or b"\x00" in raw or raw.startswith(b"\xef\xbb\xbf"):
        raise TargetConfigurationError("secret env has an invalid encoding envelope")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise TargetConfigurationError("secret env must be strict UTF-8") from exc
    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        if line != line.strip() or "=" not in line:
            raise TargetConfigurationError(
                f"secret env line {line_number} is not canonical"
            )
        name, value = line.split("=", 1)
        if not _ENV_NAME.fullmatch(name) or name in values:
            raise TargetConfigurationError(
                f"secret env line {line_number} has an invalid key"
            )
        if not value or value != value.strip():
            raise TargetConfigurationError(f"secret env key {name} is empty or padded")
        values[name] = value
    return values


def _looks_placeholder(value: str) -> bool:
    lowered = value.casefold()
    compact = re.sub(r"[^a-z0-9_-]+", "", lowered)
    return (
        any(part in compact for part in _PLACEHOLDER_PARTS)
        or len(set(value)) < 5
        or value.casefold() in {"password", "secret", "test", "admin", "omnibase"}
    )


def _validate_secret_values(values: dict[str, str]) -> None:
    missing = sorted(set(REQUIRED_ENV_KEYS) - values.keys())
    unexpected = sorted(values.keys() - set(REQUIRED_ENV_KEYS))
    if missing or unexpected:
        raise TargetConfigurationError(
            "secret env key set is invalid: "
            f"missing={','.join(missing)} unexpected={','.join(unexpected)}"
        )
    for name in SECRET_KEYS:
        if _looks_placeholder(values[name]):
            raise TargetConfigurationError(f"secret env key {name} is a placeholder")
    for name in ("POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD", "REDIS_PASSWORD"):
        if len(values[name]) < 20:
            raise TargetConfigurationError(f"secret env key {name} is too short")
    if len(values["JWT_SECRET"]) < 48:
        raise TargetConfigurationError("secret env key JWT_SECRET is too short")
    encoded = values["PROVIDER_CREDENTIAL_ENCRYPTION_KEY"]
    try:
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (ValueError, TypeError) as exc:
        raise TargetConfigurationError(
            "secret env key PROVIDER_CREDENTIAL_ENCRYPTION_KEY is not base64url"
        ) from exc
    if len(decoded) != 32:
        raise TargetConfigurationError(
            "secret env key PROVIDER_CREDENTIAL_ENCRYPTION_KEY must encode 32 bytes"
        )
    if not re.fullmatch(
        r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-"
        r"[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}",
        values["OMNIBASE_DEPLOYMENT_INSTANCE_ID"],
    ):
        raise TargetConfigurationError(
            "secret env key OMNIBASE_DEPLOYMENT_INSTANCE_ID must be a UUID"
        )
    _validate_operator_coordinates(values)


def _validate_operator_coordinates(values: dict[str, str]) -> None:
    try:
        frontend_port = int(values["OMNIBASE_FRONTEND_PORT"])
    except ValueError as exc:
        raise TargetConfigurationError(
            "OMNIBASE_FRONTEND_PORT must be an integer"
        ) from exc
    if not 1024 <= frontend_port <= 65535:
        raise TargetConfigurationError(
            "OMNIBASE_FRONTEND_PORT is outside the allowed range"
        )
    for name in ("POSTGRES_USER", "POSTGRES_DB"):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,62}", values[name]):
            raise TargetConfigurationError(
                f"secret env key {name} is not a safe identifier"
            )
    for name in ("MINIO_ROOT_USER", "MINIO_BUCKET"):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,62}", values[name]):
            raise TargetConfigurationError(f"secret env key {name} is invalid")
    database = urlsplit(values["DATABASE_URL"])
    if (
        database.scheme != "postgresql+psycopg"
        or database.hostname != "postgres"
        or database.port != 5432
        or unquote(database.username or "") != values["POSTGRES_USER"]
        or unquote(database.password or "") != values["POSTGRES_PASSWORD"]
        or database.path != f"/{values['POSTGRES_DB']}"
        or database.query
        or database.fragment
    ):
        raise TargetConfigurationError(
            "DATABASE_URL is not bound to the production PostgreSQL service"
        )
    redis = urlsplit(values["REDIS_URL"])
    if (
        redis.scheme != "redis"
        or redis.hostname != "redis"
        or redis.port != 6379
        or redis.username not in {None, ""}
        or unquote(redis.password or "") != values["REDIS_PASSWORD"]
        or redis.path != "/0"
        or redis.query
        or redis.fragment
    ):
        raise TargetConfigurationError(
            "REDIS_URL is not bound to the production Redis service"
        )
    try:
        cors = json.loads(values["CORS_ORIGINS"])
        allowlist = json.loads(values["PROVIDER_ENDPOINT_ALLOWLIST"])
    except json.JSONDecodeError as exc:
        raise TargetConfigurationError("operator JSON list value is invalid") from exc
    if cors != [f"http://127.0.0.1:{frontend_port}"]:
        raise TargetConfigurationError(
            "CORS_ORIGINS must match the loopback frontend port"
        )
    if (
        not isinstance(allowlist, list)
        or not allowlist
        or len(allowlist) != len(set(allowlist))
        or any(
            not isinstance(host, str)
            or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host)
            for host in allowlist
        )
    ):
        raise TargetConfigurationError(
            "PROVIDER_ENDPOINT_ALLOWLIST must be a unique hostname list"
        )


def _check_windows_acl(path: Path) -> None:
    try:
        completed = subprocess.run(
            ["icacls", str(path)],
            cwd=path.parent,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TargetConfigurationError("secret env ACL could not be inspected") from exc
    if (
        completed.returncode != 0
        or not completed.stdout
        or len(completed.stdout) > 16_384
    ):
        raise TargetConfigurationError("secret env ACL could not be inspected")
    output = completed.stdout
    allowed_suffixes = (
        "\\system",
        "\\administrators",
        f"\\{getpass.getuser().casefold()}",
    )
    entries: list[str] = []
    rendered_path = str(path).casefold()
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if line.casefold().startswith(rendered_path):
            line = line[len(str(path)) :].strip()
        if ":" in line:
            entries.append(line)
    if not entries:
        raise TargetConfigurationError("secret env ACL could not be inspected")
    for entry in entries:
        identity = entry.split(":", 1)[0].strip().casefold()
        if not identity.endswith(allowed_suffixes):
            raise TargetConfigurationError(
                "secret env ACL grants an unapproved principal"
            )


def _check_secret_permissions(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise TargetConfigurationError("secret env must be a regular non-symlink file")
    mode = path.stat().st_mode
    if not stat.S_ISREG(mode):
        raise TargetConfigurationError("secret env must be a regular file")
    if os.name == "nt":
        _check_windows_acl(path)
    elif stat.S_IMODE(mode) & 0o077:
        raise TargetConfigurationError(
            "secret env permissions must be 0600 or stricter"
        )


def _secret_env(value: str | Path, *, repo_root: Path) -> tuple[Path, dict[str, str]]:
    unresolved = Path(str(value))
    _reject_link_components(unresolved, name="secret env")
    path = _resolve_absolute(value, name="secret env")
    if _is_within(path, repo_root) or path == repo_root:
        raise TargetConfigurationError("secret env must be outside the repository")
    _check_secret_permissions(path)
    raw = path.read_bytes()
    values = _parse_env(raw)
    _validate_secret_values(values)
    return path, values


def _repo_facts(repo_root: Path) -> dict[str, object]:
    if not (repo_root / ".git").exists():
        raise TargetConfigurationError("repo root is not a Git worktree")
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=5,
    )
    if status.returncode != 0 or status.stdout:
        raise TargetConfigurationError("repository must be clean")
    commit = _run(["git", "rev-parse", "HEAD"], cwd=repo_root)
    tree = _run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo_root)
    if not _OBJECT_ID.fullmatch(commit) or not _OBJECT_ID.fullmatch(tree):
        raise TargetConfigurationError("repository provenance object ids are invalid")
    remote_refs = subprocess.run(
        [
            "git",
            "for-each-ref",
            "--format=%(refname)",
            "--contains=HEAD",
            "refs/remotes/",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        timeout=5,
    )
    refs = sorted(
        line
        for line in remote_refs.stdout.splitlines()
        if line.startswith("refs/remotes/")
    )
    if remote_refs.returncode != 0 or not refs:
        raise TargetConfigurationError("HEAD is not contained by a public remote ref")
    return {"commit_sha256": commit, "tree_sha256": tree, "public_remote_refs": refs}


def _verify_repo_facts(recorded: object, current: object) -> None:
    expected_keys = {"commit_sha256", "tree_sha256", "public_remote_refs"}
    if (
        not isinstance(recorded, dict)
        or not isinstance(current, dict)
        or set(recorded) != expected_keys
        or set(current) != expected_keys
    ):
        raise TargetConfigurationError("release repository fact set is invalid")

    for name, facts in (("recorded", recorded), ("current", current)):
        commit = facts.get("commit_sha256")
        tree = facts.get("tree_sha256")
        refs = facts.get("public_remote_refs")
        if (
            not isinstance(commit, str)
            or not _OBJECT_ID.fullmatch(commit)
            or not isinstance(tree, str)
            or not _OBJECT_ID.fullmatch(tree)
        ):
            raise TargetConfigurationError(
                f"release {name} repository object ids are invalid"
            )
        if (
            not isinstance(refs, list)
            or not refs
            or any(
                not isinstance(ref, str) or not _PUBLIC_REMOTE_REF.fullmatch(ref)
                for ref in refs
            )
            or refs != sorted(set(refs))
        ):
            raise TargetConfigurationError(
                f"release {name} public remote refs are invalid"
            )

    if recorded["commit_sha256"] != current["commit_sha256"]:
        raise TargetConfigurationError("release target fact drifted: repo commit")
    if recorded["tree_sha256"] != current["tree_sha256"]:
        raise TargetConfigurationError("release target fact drifted: repo tree")


def _migration_facts(repo_root: Path) -> dict[str, object]:
    versions = repo_root / "backend" / "src" / "omnibase" / "migrations" / "versions"
    revisions: dict[str, str | None] = {}
    for path in sorted(versions.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        revision: str | None = None
        down_revision: str | None = None
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                value = node.value
                for target in targets:
                    if isinstance(target, ast.Name) and target.id in {
                        "revision",
                        "down_revision",
                    }:
                        literal = ast.literal_eval(value) if value is not None else None
                        if literal is not None and not isinstance(literal, str):
                            raise TargetConfigurationError(
                                "migration graph is not linear"
                            )
                        if target.id == "revision":
                            revision = literal
                        else:
                            down_revision = literal
        if revision is None or revision in revisions:
            raise TargetConfigurationError(
                "migration revision is missing or duplicated"
            )
        revisions[revision] = down_revision
    heads = sorted(
        set(revisions) - {value for value in revisions.values() if value is not None}
    )
    numeric_revisions = {int(revision) for revision in revisions if revision.isdigit()}
    if heads != ["0013"] or "0013" not in revisions or any(
        revision >= 14 for revision in numeric_revisions
    ):
        raise TargetConfigurationError(
            "migration head must be 0013 and migration 0014 or higher must be absent"
        )
    return {
        "head": "0013",
        "migration_0013_created": True,
        "migration_0014_or_higher_absent": True,
    }


def _parse_simple_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        values[name] = value.split("#", 1)[0].strip()
    return values


def _gate_facts(repo_root: Path) -> dict[str, bool]:
    example = _parse_simple_env(repo_root / ".env.example")
    base_compose = (repo_root / "docker-compose.yml").read_text(encoding="utf-8")
    production_compose = (
        repo_root / "deployment" / "personal-production" / "compose.yml"
    ).read_text(encoding="utf-8")
    result: dict[str, bool] = {}
    for name in GATE_NAMES:
        if example.get(name) != "false":
            raise TargetConfigurationError(f".env.example key {name} must be false")
        if f"${{{name}:-false}}" not in base_compose:
            raise TargetConfigurationError(
                f"base Compose key {name} must default false"
            )
        if f'{name}: "false"' not in production_compose:
            raise TargetConfigurationError(
                f"personal production Compose key {name} must be fixed false"
            )
        result[name] = False
    return result


def _artifact_facts(repo_root: Path) -> list[dict[str, object]]:
    facts: list[dict[str, object]] = []
    for relative in ARTIFACT_PATHS:
        path = (repo_root / relative).resolve(strict=True)
        if not _is_within(path, repo_root) or not path.is_file() or path.is_symlink():
            raise TargetConfigurationError(f"release artifact is unsafe: {relative}")
        raw = path.read_bytes()
        facts.append({"path": relative, "sha256": _sha256(raw), "size_bytes": len(raw)})
    return facts


def _tool_facts(repo_root: Path) -> dict[str, str]:
    docker = _run(["docker", "--version"], cwd=repo_root)
    compose = _run(["docker", "compose", "version"], cwd=repo_root)
    if not docker.startswith("Docker version ") or not compose.startswith(
        "Docker Compose version "
    ):
        raise TargetConfigurationError(
            "Docker/Compose version output is not recognized"
        )
    return {"docker": docker, "compose": compose}


def _target_paths(args: argparse.Namespace, repo_root: Path) -> TargetPaths:
    directories = {
        "release directory": _operator_directory(
            args.release_dir, name="release directory", repo_root=repo_root
        ),
        "state directory": _operator_directory(
            args.state_dir, name="state directory", repo_root=repo_root
        ),
        "backup directory": _operator_directory(
            args.backup_dir, name="backup directory", repo_root=repo_root
        ),
    }
    if len(set(directories.values())) != len(directories):
        raise TargetConfigurationError("operator directories must be distinct")
    secret_path, _ = _secret_env(args.secret_env, repo_root=repo_root)
    return TargetPaths(
        release_dir=directories["release directory"],
        state_dir=directories["state directory"],
        backup_dir=directories["backup directory"],
        secret_env=secret_path,
    )


def _doctor(args: argparse.Namespace) -> dict[str, object]:
    repo_root = _resolve_absolute(args.repo_root, name="repo root")
    paths = _target_paths(args, repo_root)
    min_free = args.min_free_bytes
    if not isinstance(min_free, int) or min_free <= 0:
        raise TargetConfigurationError("minimum free bytes must be positive")
    disk: dict[str, int] = {}
    for name, path in (
        ("release_dir", paths.release_dir),
        ("state_dir", paths.state_dir),
        ("backup_dir", paths.backup_dir),
    ):
        free = shutil.disk_usage(path).free
        if free < min_free:
            raise TargetConfigurationError(f"{name} has insufficient free space")
        disk[name] = free
    return {
        "artifacts": _artifact_facts(repo_root),
        "feature_gates": _gate_facts(repo_root),
        "migration": _migration_facts(repo_root),
        "operator_paths": {
            "backup_dir": str(paths.backup_dir),
            "release_dir": str(paths.release_dir),
            "secret_env": str(paths.secret_env),
            "state_dir": str(paths.state_dir),
        },
        "platform": {"machine": platform.machine(), "system": platform.system()},
        "repo": _repo_facts(repo_root),
        "secret_posture": {
            "permissions_valid": True,
            "required_keys": list(REQUIRED_ENV_KEYS),
            "values_redacted": True,
            "values_valid": True,
        },
        "storage": {"minimum_free_bytes": min_free, "observed_free_bytes": disk},
        "tools": _tool_facts(repo_root),
    }


def _write_manifest(
    args: argparse.Namespace, facts: dict[str, object]
) -> dict[str, object]:
    output = _resolve_absolute(args.output, name="manifest output", must_exist=False)
    release_dir = Path(str(facts["operator_paths"]["release_dir"]))  # type: ignore[index]
    if (
        output.parent.resolve(strict=True) != release_dir
        or output.exists()
        or output.is_symlink()
    ):
        raise TargetConfigurationError(
            "manifest output must be a new direct child of release directory"
        )
    payload = {
        "kind": "p5_personal_target_release",
        "schema_version": SCHEMA_VERSION,
        "target": facts,
    }
    envelope = {
        "payload": payload,
        "payload_sha256": _sha256(_canonical_bytes(payload)),
    }
    raw = _canonical_bytes(envelope)
    output.write_bytes(raw)
    return {
        "manifest_path": str(output),
        "manifest_sha256": _sha256(raw),
        "operation": "release-manifest",
        "payload_sha256": envelope["payload_sha256"],
        "production_runtime_started": False,
        "status": "release/recorded_not_started",
    }


def _load_manifest(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        envelope = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TargetConfigurationError("release manifest is not valid JSON") from exc
    if not isinstance(envelope, dict) or set(envelope) != {"payload", "payload_sha256"}:
        raise TargetConfigurationError("release manifest envelope is invalid")
    if raw != _canonical_bytes(envelope):
        raise TargetConfigurationError("release manifest bytes are not canonical")
    payload = envelope["payload"]
    digest = envelope["payload_sha256"]
    if not isinstance(payload, dict) or not isinstance(digest, str):
        raise TargetConfigurationError("release manifest fields are invalid")
    if not _SHA256.fullmatch(digest) or _sha256(_canonical_bytes(payload)) != digest:
        raise TargetConfigurationError("release manifest payload digest drifted")
    if (
        payload.get("schema_version") != SCHEMA_VERSION
        or payload.get("kind") != "p5_personal_target_release"
    ):
        raise TargetConfigurationError("release manifest schema is unsupported")
    return payload, raw


def _verify_release(args: argparse.Namespace) -> dict[str, object]:
    repo_root = _resolve_absolute(args.repo_root, name="repo root")
    manifest = _resolve_absolute(args.manifest, name="release manifest")
    if _is_within(manifest, repo_root) or manifest.is_symlink():
        raise TargetConfigurationError(
            "release manifest must be a non-symlink outside the repository"
        )
    payload, raw = _load_manifest(manifest)
    target = payload.get("target")
    if not isinstance(target, dict):
        raise TargetConfigurationError("release manifest target facts are invalid")
    expected_target_keys = {
        "artifacts",
        "feature_gates",
        "migration",
        "operator_paths",
        "platform",
        "repo",
        "secret_posture",
        "storage",
        "tools",
    }
    if set(target) != expected_target_keys:
        raise TargetConfigurationError("release manifest target fact set is invalid")
    paths = target.get("operator_paths")
    storage = target.get("storage")
    if not isinstance(paths, dict) or not isinstance(storage, dict):
        raise TargetConfigurationError("release manifest target path facts are invalid")
    if set(paths) != {"backup_dir", "release_dir", "secret_env", "state_dir"}:
        raise TargetConfigurationError("release manifest operator path set is invalid")
    if set(storage) != {"minimum_free_bytes", "observed_free_bytes"}:
        raise TargetConfigurationError("release manifest storage fact set is invalid")
    release_dir = _resolve_absolute(paths["release_dir"], name="release directory")
    if manifest.parent.resolve(strict=True) != release_dir:
        raise TargetConfigurationError(
            "release manifest is outside its recorded release directory"
        )
    namespace = argparse.Namespace(
        repo_root=str(repo_root),
        release_dir=paths.get("release_dir"),
        state_dir=paths.get("state_dir"),
        backup_dir=paths.get("backup_dir"),
        secret_env=paths.get("secret_env"),
        min_free_bytes=storage.get("minimum_free_bytes"),
    )
    current = _doctor(namespace)
    for key in (
        "artifacts",
        "feature_gates",
        "migration",
        "operator_paths",
        "platform",
        "secret_posture",
        "tools",
    ):
        if target.get(key) != current.get(key):
            raise TargetConfigurationError(f"release target fact drifted: {key}")
    _verify_repo_facts(target.get("repo"), current.get("repo"))
    return {
        "manifest_sha256": _sha256(raw),
        "operation": "verify-release",
        "payload_sha256": _sha256(_canonical_bytes(payload)),
        "production_runtime_started": False,
        "status": "release/verified_not_started",
        "verified": True,
    }


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--secret-env", required=True)
    parser.add_argument("--min-free-bytes", type=int, default=DEFAULT_MIN_FREE_BYTES)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    doctor = commands.add_parser("doctor", help="fail-closed offline target preflight")
    _add_common(doctor)
    release = commands.add_parser(
        "release-manifest", help="write a canonical release receipt"
    )
    _add_common(release)
    release.add_argument("--output", required=True)
    verify = commands.add_parser(
        "verify-release", help="verify receipt bytes and current target facts"
    )
    verify.add_argument("--repo-root", default=str(REPO_ROOT))
    verify.add_argument("--manifest", required=True)
    return parser.parse_args(argv)


def _execute(args: argparse.Namespace) -> dict[str, object]:
    if args.command == "doctor":
        facts = _doctor(args)
        return {
            "operation": "doctor",
            "production_runtime_started": False,
            "status": "target/ready_for_release_manifest",
            "target": facts,
        }
    if args.command == "release-manifest":
        return _write_manifest(args, _doctor(args))
    if args.command == "verify-release":
        return _verify_release(args)
    raise TargetConfigurationError("unsupported command")


def main(argv: list[str] | None = None) -> int:
    try:
        _emit(_execute(_parse_args(argv)))
        return 0
    except (OSError, TargetConfigurationError, ValueError, TypeError) as exc:
        _emit(
            {
                "error": str(exc),
                "operation": "invalid/veto",
                "production_runtime_started": False,
                "status": "invalid/veto",
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
