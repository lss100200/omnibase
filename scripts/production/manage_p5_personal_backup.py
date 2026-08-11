#!/usr/bin/env python3
"""Plan and verify the offline P5 personal cold-backup boundary.

This controller never invokes PostgreSQL, MinIO, Redis, Docker, Compose, or a
Runtime. Operators stage already-created artifacts in the fixed layout emitted
by ``plan-backup``; ``seal-assets`` records only paths, sizes, and SHA-256
digests; ``verify-backup`` replays that closed inventory; and ``plan-restore``
emits a non-executing restore-new plan.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_PLAN = "omnibase.p5-personal-backup-plan.v1"
SCHEMA_MANIFEST = "omnibase.p5-personal-backup-manifest.v1"
SCHEMA_DATABASE_INVENTORY = "omnibase.postgresql-database-inventory.v1"
SCHEMA_RESTORE_PLAN = "omnibase.p5-personal-restore-plan.v1"
PLAN_NAME = "backup-plan.json"
MANIFEST_NAME = "backup-manifest.json"
RELEASE_RECEIPT = "release/receipt.json"
POSTGRES_DUMP = "postgres/database.dump"
RUNTIME_CONFIG = "personal-runtime/config.json"
MINIO_ROOT = "minio/export"
RUNTIME_STATE_ROOT = "personal-runtime/state"
READINESS_ROOT = "personal-runtime/readiness"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class BackupError(ValueError):
    """A fail-closed backup or restore-plan validation error."""


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BackupError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _load_canonical(path: Path) -> tuple[dict[str, Any], bytes]:
    _require_plain_file(path, root=path.parent)
    raw = path.read_bytes()
    try:
        value = json.loads(raw, object_pairs_hook=_object_pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BackupError(f"invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise BackupError(f"JSON root must be an object: {path.name}")
    if raw != _canonical(value):
        raise BackupError(f"JSON must use canonical encoding: {path.name}")
    return value, raw


def _exact(value: dict[str, Any], fields: set[str], *, where: str) -> None:
    actual = set(value)
    if actual != fields:
        raise BackupError(
            f"{where} fields must be exactly {sorted(fields)}; "
            f"unknown={sorted(actual - fields)} missing={sorted(fields - actual)}"
        )


def _absolute(value: str | Path, *, where: str, must_exist: bool = False) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise BackupError(f"{where} must be absolute")
    _reject_symlink_components(path)
    try:
        return path.resolve(strict=must_exist)
    except OSError as exc:
        raise BackupError(f"cannot resolve {where}") from exc


def _overlaps(left: Path, right: Path) -> bool:
    try:
        left.relative_to(right)
        return True
    except ValueError:
        pass
    try:
        right.relative_to(left)
        return True
    except ValueError:
        return False


def _reject_symlink_components(path: Path, *, stop: Path | None = None) -> None:
    cursor = path
    while True:
        is_junction = getattr(cursor, "is_junction", lambda: False)
        if cursor.exists() and (cursor.is_symlink() or is_junction()):
            raise BackupError(f"symlink is forbidden: {cursor}")
        if stop is not None and cursor == stop:
            return
        if cursor.parent == cursor:
            return
        cursor = cursor.parent


def _relative(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise BackupError(
            "manifest path must be a non-empty canonical POSIX relative path"
        )
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BackupError(f"unsafe manifest path: {value}")
    if path.as_posix() != value or ":" in path.parts[0]:
        raise BackupError(f"non-canonical manifest path: {value}")
    return path


def _contained(root: Path, relative: str, *, must_exist: bool = True) -> Path:
    posix = _relative(relative)
    candidate = root.joinpath(*posix.parts)
    _reject_symlink_components(candidate, stop=root)
    try:
        resolved = candidate.resolve(strict=must_exist)
        resolved.relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise BackupError(f"asset escapes backup root: {relative}") from exc
    return resolved


def _require_plain_file(path: Path, *, root: Path) -> None:
    _reject_symlink_components(path, stop=root)
    is_junction = getattr(path, "is_junction", lambda: False)
    if path.is_symlink() or is_junction() or not path.is_file():
        raise BackupError(f"required regular file is missing: {path}")
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise BackupError(f"file escapes its root: {path}") from exc


def _entry(root: Path, relative: str) -> dict[str, object]:
    path = _contained(root, relative)
    _require_plain_file(path, root=root)
    size = path.stat().st_size
    if size <= 0:
        raise BackupError(f"backup asset must not be empty: {relative}")
    return {"path": relative, "sha256": _sha256_file(path), "size": size}


def _inventory(root: Path, relative_root: str) -> list[dict[str, object]]:
    directory = _contained(root, relative_root)
    is_junction = getattr(directory, "is_junction", lambda: False)
    if directory.is_symlink() or is_junction() or not directory.is_dir():
        raise BackupError(f"required asset directory is missing: {relative_root}")
    entries: list[dict[str, object]] = []
    for current, directories, files in os.walk(directory, followlinks=False):
        current_path = Path(current)
        for name in directories:
            child = current_path / name
            is_junction = getattr(child, "is_junction", lambda: False)
            if child.is_symlink() or is_junction():
                raise BackupError(f"symlink is forbidden: {child}")
        for name in files:
            child = current_path / name
            is_junction = getattr(child, "is_junction", lambda: False)
            if child.is_symlink() or is_junction():
                raise BackupError(f"symlink is forbidden: {child}")
            relative = child.relative_to(root).as_posix()
            entries.append(_entry(root, relative))
    entries.sort(key=lambda item: str(item["path"]))
    if not entries:
        raise BackupError(f"asset inventory must not be empty: {relative_root}")
    paths = [str(entry["path"]) for entry in entries]
    if len(paths) != len(set(paths)):
        raise BackupError(f"duplicate inventory path under {relative_root}")
    return entries


def _identifier(value: str, *, where: str) -> str:
    if not _IDENTIFIER.fullmatch(value):
        raise BackupError(f"{where} must be a PostgreSQL identifier")
    return value


def _validate_plan(plan: dict[str, Any]) -> None:
    _exact(
        plan,
        {
            "execution_authorized",
            "layout",
            "redis",
            "schema",
            "schema_version",
            "source_database",
        },
        where="backup plan",
    )
    if (
        plan["schema"] != SCHEMA_PLAN
        or plan["schema_version"] != 1
        or plan["execution_authorized"] is not False
    ):
        raise BackupError("invalid backup plan posture")
    _identifier(plan["source_database"], where="source database")
    expected_layout = {
        "release_receipt": RELEASE_RECEIPT,
        "postgres_dump": POSTGRES_DUMP,
        "minio_root": MINIO_ROOT,
        "runtime_config": RUNTIME_CONFIG,
        "runtime_state_root": RUNTIME_STATE_ROOT,
        "runtime_readiness_root": READINESS_ROOT,
    }
    if plan["layout"] != expected_layout:
        raise BackupError("backup plan layout drifted")
    if plan["redis"] != {
        "archived": False,
        "authoritative": False,
        "rebuild_required": True,
    }:
        raise BackupError("Redis must remain non-authoritative and unarchived")


def _validate_entry(value: Any, *, where: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BackupError(f"{where} must be an object")
    _exact(value, {"path", "sha256", "size"}, where=where)
    _relative(value["path"])
    if (
        not isinstance(value["size"], int)
        or isinstance(value["size"], bool)
        or value["size"] <= 0
    ):
        raise BackupError(f"{where}.size must be a positive integer")
    if not isinstance(value["sha256"], str) or not _SHA256.fullmatch(value["sha256"]):
        raise BackupError(f"{where}.sha256 must be lowercase SHA-256")
    return value


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    _exact(
        manifest,
        {
            "backup_plan_sha256",
            "execution_authorized",
            "minio",
            "personal_runtime",
            "postgres",
            "redis",
            "release_receipt",
            "restore_policy",
            "schema",
            "schema_version",
            "source_database",
        },
        where="backup manifest",
    )
    if (
        manifest["schema"] != SCHEMA_MANIFEST
        or manifest["schema_version"] != 1
        or manifest["execution_authorized"] is not False
    ):
        raise BackupError("invalid backup manifest posture")
    if not _SHA256.fullmatch(str(manifest["backup_plan_sha256"])):
        raise BackupError("invalid backup plan digest")
    _identifier(manifest["source_database"], where="source database")
    _validate_entry(manifest["release_receipt"], where="release_receipt")
    if not isinstance(manifest["postgres"], dict):
        raise BackupError("postgres must be an object")
    _exact(manifest["postgres"], {"artifact", "format"}, where="postgres")
    if manifest["postgres"]["format"] != "custom":
        raise BackupError("PostgreSQL dump format must be custom")
    _validate_entry(manifest["postgres"]["artifact"], where="postgres.artifact")
    for section, root_name in (("minio", MINIO_ROOT),):
        value = manifest[section]
        if not isinstance(value, dict):
            raise BackupError(f"{section} must be an object")
        _exact(value, {"authoritative", "files", "root"}, where=section)
        if value["authoritative"] is not True or value["root"] != root_name:
            raise BackupError("invalid MinIO inventory posture")
        _validate_inventory(value["files"], root_name, where="minio.files")
    runtime = manifest["personal_runtime"]
    if not isinstance(runtime, dict):
        raise BackupError("personal_runtime must be an object")
    _exact(runtime, {"config", "readiness", "state"}, where="personal_runtime")
    _validate_entry(runtime["config"], where="personal_runtime.config")
    for section, root_name in (
        ("state", RUNTIME_STATE_ROOT),
        ("readiness", READINESS_ROOT),
    ):
        value = runtime[section]
        if not isinstance(value, dict):
            raise BackupError(f"personal_runtime.{section} must be an object")
        _exact(value, {"files", "root"}, where=f"personal_runtime.{section}")
        if value["root"] != root_name:
            raise BackupError(f"personal_runtime.{section}.root drifted")
        _validate_inventory(
            value["files"], root_name, where=f"personal_runtime.{section}.files"
        )
    if manifest["redis"] != {
        "archived": False,
        "authoritative": False,
        "rebuild_required": True,
    }:
        raise BackupError("Redis must remain non-authoritative and unarchived")
    if manifest["restore_policy"] != {
        "database_prefix": "omnibase_restore_",
        "minio_new_root_only": True,
        "new_database_only": True,
    }:
        raise BackupError("restore policy drifted")


def _validate_inventory(value: Any, root_name: str, *, where: str) -> None:
    if not isinstance(value, list) or not value:
        raise BackupError(f"{where} must be a non-empty list")
    paths: list[str] = []
    for index, item in enumerate(value):
        entry = _validate_entry(item, where=f"{where}[{index}]")
        path = str(entry["path"])
        if path == root_name or not path.startswith(root_name + "/"):
            raise BackupError(f"{where} path is outside its bound root")
        paths.append(path)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise BackupError(f"{where} paths must be sorted and unique")


def _expected_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    entries = [
        manifest["release_receipt"],
        manifest["postgres"]["artifact"],
        manifest["personal_runtime"]["config"],
    ]
    entries.extend(manifest["minio"]["files"])
    entries.extend(manifest["personal_runtime"]["state"]["files"])
    entries.extend(manifest["personal_runtime"]["readiness"]["files"])
    return entries


def _verify_assets(root: Path, manifest: dict[str, Any]) -> None:
    entries = _expected_entries(manifest)
    paths = [str(entry["path"]) for entry in entries]
    if len(paths) != len(set(paths)):
        raise BackupError("duplicate path across backup inventories")
    for item in entries:
        actual = _entry(root, str(item["path"]))
        if actual != item:
            raise BackupError(f"backup asset size or digest drifted: {item['path']}")
    actual_files: set[str] = set()
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            child = current_path / name
            is_junction = getattr(child, "is_junction", lambda: False)
            if child.is_symlink() or is_junction():
                raise BackupError(f"symlink is forbidden: {current_path / name}")
        for name in files:
            path = current_path / name
            is_junction = getattr(path, "is_junction", lambda: False)
            if path.is_symlink() or is_junction():
                raise BackupError(f"symlink is forbidden: {path}")
            actual_files.add(path.relative_to(root).as_posix())
    expected = set(paths) | {PLAN_NAME, MANIFEST_NAME}
    if actual_files != expected:
        raise BackupError(
            f"backup file closure drifted: unexpected={sorted(actual_files - expected)} missing={sorted(expected - actual_files)}"
        )


def _load_root(repo_root: str, backup_target: str, *, new: bool) -> tuple[Path, Path]:
    repo = _absolute(repo_root, where="repo root", must_exist=True)
    root = _absolute(backup_target, where="backup target", must_exist=not new)
    if _overlaps(root, repo):
        raise BackupError(
            "backup target must be outside and non-overlapping with the repository"
        )
    _reject_symlink_components(root)
    if new and root.exists():
        raise BackupError("backup target must be a new directory")
    if not new and (root.is_symlink() or not root.is_dir()):
        raise BackupError("backup target must be an existing regular directory")
    return repo, root


def plan_backup(args: argparse.Namespace) -> dict[str, Any]:
    _, root = _load_root(args.repo_root, args.backup_target, new=True)
    plan = {
        "execution_authorized": False,
        "layout": {
            "minio_root": MINIO_ROOT,
            "postgres_dump": POSTGRES_DUMP,
            "release_receipt": RELEASE_RECEIPT,
            "runtime_config": RUNTIME_CONFIG,
            "runtime_readiness_root": READINESS_ROOT,
            "runtime_state_root": RUNTIME_STATE_ROOT,
        },
        "redis": {"archived": False, "authoritative": False, "rebuild_required": True},
        "schema": SCHEMA_PLAN,
        "schema_version": 1,
        "source_database": _identifier(args.source_database, where="source database"),
    }
    root.mkdir(parents=True)
    for directory in (
        "release",
        "postgres",
        MINIO_ROOT,
        "personal-runtime",
        RUNTIME_STATE_ROOT,
        READINESS_ROOT,
    ):
        root.joinpath(*PurePosixPath(directory).parts).mkdir(
            parents=True, exist_ok=True
        )
    (root / PLAN_NAME).write_bytes(_canonical(plan))
    return plan


def _load_plan(root: Path) -> tuple[dict[str, Any], bytes]:
    plan, raw = _load_canonical(root / PLAN_NAME)
    _validate_plan(plan)
    return plan, raw


def seal_assets(args: argparse.Namespace) -> dict[str, Any]:
    _, root = _load_root(args.repo_root, args.backup_target, new=False)
    if (root / MANIFEST_NAME).exists():
        raise BackupError("refusing to overwrite an existing backup manifest")
    plan, plan_raw = _load_plan(root)
    if (root / "redis").exists():
        raise BackupError("Redis artifacts must not be archived")
    manifest = {
        "backup_plan_sha256": _sha256_bytes(plan_raw),
        "execution_authorized": False,
        "minio": {
            "authoritative": True,
            "files": _inventory(root, MINIO_ROOT),
            "root": MINIO_ROOT,
        },
        "personal_runtime": {
            "config": _entry(root, RUNTIME_CONFIG),
            "readiness": {
                "files": _inventory(root, READINESS_ROOT),
                "root": READINESS_ROOT,
            },
            "state": {
                "files": _inventory(root, RUNTIME_STATE_ROOT),
                "root": RUNTIME_STATE_ROOT,
            },
        },
        "postgres": {"artifact": _entry(root, POSTGRES_DUMP), "format": "custom"},
        "redis": {"archived": False, "authoritative": False, "rebuild_required": True},
        "release_receipt": _entry(root, RELEASE_RECEIPT),
        "restore_policy": {
            "database_prefix": "omnibase_restore_",
            "minio_new_root_only": True,
            "new_database_only": True,
        },
        "schema": SCHEMA_MANIFEST,
        "schema_version": 1,
        "source_database": plan["source_database"],
    }
    _validate_manifest_shape(manifest)
    (root / MANIFEST_NAME).write_bytes(_canonical(manifest))
    _verify_assets(root, manifest)
    return manifest


def verify_backup(args: argparse.Namespace) -> dict[str, Any]:
    _, root = _load_root(args.repo_root, args.backup_target, new=False)
    plan, plan_raw = _load_plan(root)
    manifest, manifest_raw = _load_canonical(root / MANIFEST_NAME)
    _validate_manifest_shape(manifest)
    if (
        manifest["backup_plan_sha256"] != _sha256_bytes(plan_raw)
        or manifest["source_database"] != plan["source_database"]
    ):
        raise BackupError("backup manifest is not bound to the selected plan")
    _verify_assets(root, manifest)
    return {
        "backup_manifest_sha256": _sha256_bytes(manifest_raw),
        "backup_verified": True,
        "execution_authorized": False,
        "redis_archived": False,
        "schema": SCHEMA_MANIFEST,
    }


def plan_restore(args: argparse.Namespace) -> dict[str, Any]:
    repo, root = _load_root(args.repo_root, args.backup_target, new=False)
    verification = verify_backup(args)
    manifest, manifest_raw = _load_canonical(root / MANIFEST_NAME)
    target = _identifier(args.target_database, where="target database")
    if (
        not target.startswith("omnibase_restore_")
        or target == manifest["source_database"]
    ):
        raise BackupError("restore target must be a new omnibase_restore_* database")
    inventory_path = _absolute(
        args.database_inventory, where="database inventory", must_exist=True
    )
    inventory, inventory_raw = _load_canonical(inventory_path)
    _exact(
        inventory, {"databases", "schema", "schema_version"}, where="database inventory"
    )
    if (
        inventory["schema"] != SCHEMA_DATABASE_INVENTORY
        or inventory["schema_version"] != 1
    ):
        raise BackupError("unsupported database inventory")
    databases = inventory["databases"]
    if (
        not isinstance(databases, list)
        or not databases
        or any(not isinstance(item, str) for item in databases)
    ):
        raise BackupError("database inventory must contain a non-empty string list")
    normalized = [
        _identifier(item, where="database inventory item") for item in databases
    ]
    if normalized != sorted(set(normalized)):
        raise BackupError("database inventory must be sorted and unique")
    if target in normalized:
        raise BackupError("restore target database already exists")
    minio_root = _absolute(args.minio_restore_root, where="MinIO restore root")
    if minio_root.exists():
        raise BackupError("MinIO restore root must be a new path")
    if _overlaps(minio_root, repo) or _overlaps(minio_root, root):
        raise BackupError(
            "MinIO restore root must be outside the repository and backup"
        )
    _reject_symlink_components(minio_root)
    return {
        "backup_manifest_sha256": _sha256_bytes(manifest_raw),
        "backup_verified": verification["backup_verified"],
        "database_inventory_sha256": _sha256_bytes(inventory_raw),
        "execution_authorized": False,
        "minio_restore_root": str(minio_root),
        "personal_runtime_assets_verified": True,
        "redis": {"archived": False, "authoritative": False, "rebuild_required": True},
        "schema": SCHEMA_RESTORE_PLAN,
        "schema_version": 1,
        "source_database": manifest["source_database"],
        "target_database": target,
    }


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan-backup", "seal-assets", "verify-backup", "plan-restore"):
        command = commands.add_parser(name)
        command.add_argument("--repo-root", required=True)
        command.add_argument("--backup-target", required=True)
        if name == "plan-backup":
            command.add_argument("--source-database", required=True)
        if name == "plan-restore":
            command.add_argument("--target-database", required=True)
            command.add_argument("--database-inventory", required=True)
            command.add_argument("--minio-restore-root", required=True)
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "plan-backup": plan_backup,
        "seal-assets": seal_assets,
        "verify-backup": verify_backup,
        "plan-restore": plan_restore,
    }[args.command](args)


def main(argv: list[str] | None = None) -> int:
    try:
        payload = _run(_parse_args(argv))
        print(_canonical(payload).decode(), end="")
        return 0
    except (BackupError, OSError, TypeError) as exc:
        print(
            _canonical(
                {
                    "error": str(exc),
                    "execution_authorized": False,
                    "state": "invalid/veto",
                }
            ).decode(),
            end="",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
