"""Attack tests for the offline personal backup/restore planner."""

from __future__ import annotations

import importlib.util
import json
import os
from argparse import Namespace
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("manage_p5_personal_backup.py")
SPEC = importlib.util.spec_from_file_location("manage_p5_personal_backup", SCRIPT)
assert SPEC and SPEC.loader
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()


def _stage(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = tmp_path / "cold" / "backup-1"
    backup.plan_backup(
        Namespace(
            repo_root=str(repo), backup_target=str(target), source_database="omnibase"
        )
    )
    files = {
        backup.RELEASE_RECEIPT: b'{"release":"abc"}\n',
        backup.POSTGRES_DUMP: b"PGDMP\x01payload",
        backup.RUNTIME_CONFIG: b'{"profile":"personal_single_owner"}\n',
        f"{backup.MINIO_ROOT}/bucket/object.bin": b"object-data",
        f"{backup.RUNTIME_STATE_ROOT}/000001.json": b'{"state":"rolled_back"}\n',
        f"{backup.READINESS_ROOT}/owner.json": b'{"ready":true}\n',
    }
    for relative, content in files.items():
        path = target.joinpath(*Path(relative).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    backup.seal_assets(Namespace(repo_root=str(repo), backup_target=str(target)))
    return repo, target


def _verify(repo: Path, target: Path) -> dict[str, object]:
    return backup.verify_backup(
        Namespace(repo_root=str(repo), backup_target=str(target))
    )


def _inventory(path: Path, databases: list[str] | None = None) -> Path:
    payload = {
        "databases": databases or ["omnibase", "postgres"],
        "schema": backup.SCHEMA_DATABASE_INVENTORY,
        "schema_version": 1,
    }
    payload["databases"] = sorted(payload["databases"])
    path.write_bytes(_canonical(payload))
    return path


def test_complete_backup_and_restore_plan_are_offline_and_bound(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    verified = _verify(repo, target)
    assert verified["backup_verified"] is True
    assert verified["execution_authorized"] is False
    inventory = _inventory(tmp_path / "databases.json")
    restore = backup.plan_restore(
        Namespace(
            repo_root=str(repo),
            backup_target=str(target),
            target_database="omnibase_restore_20260811",
            database_inventory=str(inventory),
            minio_restore_root=str(tmp_path / "restored-minio"),
        )
    )
    assert restore["execution_authorized"] is False
    assert restore["target_database"] == "omnibase_restore_20260811"
    assert restore["redis"] == {
        "archived": False,
        "authoritative": False,
        "rebuild_required": True,
    }
    manifest = json.loads((target / backup.MANIFEST_NAME).read_bytes())
    assert manifest["release_receipt"]["path"] == backup.RELEASE_RECEIPT
    assert manifest["postgres"]["artifact"]["path"] == backup.POSTGRES_DUMP
    assert (
        manifest["minio"]["files"][0]["path"]
        == f"{backup.MINIO_ROOT}/bucket/object.bin"
    )
    assert manifest["personal_runtime"]["config"]["path"] == backup.RUNTIME_CONFIG


def test_backup_target_must_be_absolute_outside_repo_and_new(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(backup.BackupError, match="absolute"):
        backup.plan_backup(
            Namespace(
                repo_root=str(repo),
                backup_target="relative",
                source_database="omnibase",
            )
        )
    with pytest.raises(backup.BackupError, match="outside"):
        backup.plan_backup(
            Namespace(
                repo_root=str(repo),
                backup_target=str(repo / "backup"),
                source_database="omnibase",
            )
        )
    existing = tmp_path / "existing"
    existing.mkdir()
    with pytest.raises(backup.BackupError, match="new directory"):
        backup.plan_backup(
            Namespace(
                repo_root=str(repo),
                backup_target=str(existing),
                source_database="omnibase",
            )
        )


def test_symlinked_asset_is_rejected(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    asset = target / backup.MINIO_ROOT / "bucket" / "object.bin"
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"object-data")
    asset.unlink()
    try:
        os.symlink(outside, asset)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    with pytest.raises(backup.BackupError, match="symlink"):
        _verify(repo, target)


def test_manifest_path_escape_is_rejected_even_if_resealed(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    path = target / backup.MANIFEST_NAME
    manifest = json.loads(path.read_bytes())
    manifest["release_receipt"]["path"] = "../receipt.json"
    path.write_bytes(_canonical(manifest))
    with pytest.raises(backup.BackupError, match="unsafe manifest path"):
        _verify(repo, target)


def test_digest_drift_is_rejected(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    (target / backup.POSTGRES_DUMP).write_bytes(b"PGDMP tampered")
    with pytest.raises(backup.BackupError, match="drifted"):
        _verify(repo, target)


def test_duplicate_inventory_path_is_rejected(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    path = target / backup.MANIFEST_NAME
    manifest = json.loads(path.read_bytes())
    manifest["minio"]["files"].append(dict(manifest["minio"]["files"][0]))
    path.write_bytes(_canonical(manifest))
    with pytest.raises(backup.BackupError, match="sorted and unique"):
        _verify(repo, target)


def test_unknown_manifest_field_is_rejected(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    path = target / backup.MANIFEST_NAME
    manifest = json.loads(path.read_bytes())
    manifest["surprise"] = True
    path.write_bytes(_canonical(manifest))
    with pytest.raises(backup.BackupError, match="unknown=.*surprise"):
        _verify(repo, target)


def test_noncanonical_or_duplicate_json_field_is_rejected(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    path = target / backup.MANIFEST_NAME
    value = json.loads(path.read_bytes())
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    with pytest.raises(backup.BackupError, match="canonical"):
        _verify(repo, target)
    path.write_text('{"schema":"a","schema":"b"}\n', encoding="utf-8")
    with pytest.raises(backup.BackupError, match="duplicate JSON field"):
        _verify(repo, target)


def test_existing_restore_database_and_in_place_name_are_rejected(
    tmp_path: Path,
) -> None:
    repo, target = _stage(tmp_path)
    inventory = _inventory(
        tmp_path / "databases.json", ["omnibase", "omnibase_restore_taken", "postgres"]
    )
    base = dict(
        repo_root=str(repo),
        backup_target=str(target),
        database_inventory=str(inventory),
        minio_restore_root=str(tmp_path / "restore-minio"),
    )
    with pytest.raises(backup.BackupError, match="already exists"):
        backup.plan_restore(Namespace(**base, target_database="omnibase_restore_taken"))
    with pytest.raises(backup.BackupError, match="omnibase_restore"):
        backup.plan_restore(Namespace(**base, target_database="omnibase"))


def test_existing_minio_restore_root_is_rejected(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    inventory = _inventory(tmp_path / "databases.json")
    restore_root = tmp_path / "restore-minio"
    restore_root.mkdir()
    with pytest.raises(backup.BackupError, match="new path"):
        backup.plan_restore(
            Namespace(
                repo_root=str(repo),
                backup_target=str(target),
                target_database="omnibase_restore_new",
                database_inventory=str(inventory),
                minio_restore_root=str(restore_root),
            )
        )


def test_redis_archive_and_unexpected_files_are_rejected(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    extra = target / "redis" / "dump.rdb"
    extra.parent.mkdir()
    extra.write_bytes(b"not-authoritative")
    with pytest.raises(backup.BackupError, match="closure drifted"):
        _verify(repo, target)
