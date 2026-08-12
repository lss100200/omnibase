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
assert SPEC is not None
assert SPEC.loader is not None
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode()


def _write_migration(
    repo: Path, revision: str, down_revision: str | None, *, memory_schema: bool = False
) -> Path:
    versions = repo / "backend" / "src" / "omnibase" / "migrations" / "versions"
    versions.mkdir(parents=True, exist_ok=True)
    suffix = "memory_context_capsules" if memory_schema else "fixture"
    path = versions / f"{revision}_{suffix}.py"
    body = [
        f'revision: str = "{revision}"',
        f"down_revision: str | None = {down_revision!r}",
    ]
    if memory_schema:
        body.extend(
            [
                "_MEMORY_VECTOR_LANE_VERSIONS = (1, 2)",
                "_MEMORY_TABLES = (",
                '    "memory_candidates",',
                '    "memories",',
                '    "memory_versions",',
                '    "memory_review_evidence",',
                '    "context_capsules",',
                '    "context_capsule_items",',
                '    "memory_effects",',
                '    "memory_tombstones",',
                '    "memory_embeddings_v1",',
                '    "memory_embeddings_v2",',
                ")",
                "",
                "def upgrade() -> None:",
                '    _create_embedding_lane("memory_embeddings_v1", 1024)',
                '    _create_embedding_lane("memory_embeddings_v2", 1536)',
            ]
        )
    path.write_text("\n".join(body) + "\n", encoding="utf-8")
    return path


def _repo_fixture(repo: Path, *, head: str = "0015") -> None:
    previous: str | None = None
    for number in range(1, int(head) + 1):
        revision = f"{number:04d}"
        _write_migration(
            repo,
            revision,
            previous,
            memory_schema=revision == "0013",
        )
        previous = revision


def _prepare_backup(tmp_path: Path, *, head: str = "0015") -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo_fixture(repo, head=head)
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
    return repo, target


def _stage(tmp_path: Path, *, head: str = "0015") -> tuple[Path, Path]:
    repo, target = _prepare_backup(tmp_path, head=head)
    postgres_inventory = _postgres_backup_inventory(
        tmp_path / "postgres-inventory.json",
        dump=target / backup.POSTGRES_DUMP,
        head=head,
        source_database="omnibase",
    )
    backup.seal_assets(
        Namespace(
            repo_root=str(repo),
            backup_target=str(target),
            postgres_inventory=str(postgres_inventory),
        )
    )
    return repo, target


def _stage_v1(tmp_path: Path) -> tuple[Path, Path]:
    repo, target = _stage(tmp_path, head="0013")
    plan_path = target / backup.PLAN_NAME
    plan = json.loads(plan_path.read_bytes())
    for field in (
        "memory_table_names",
        "memory_vector_inventory",
        "memory_vector_lane_versions",
        "migration_0013_schema_sha256",
        "migration_0014_schema_sha256",
        "migration_0015_schema_sha256",
        "migration_revision_list_sha256",
        "source_migration_head",
    ):
        plan.pop(field)
    plan["layout"].pop("postgres_inventory")
    plan["schema"] = backup.SCHEMA_PLAN_V1
    plan["schema_version"] = 1
    plan_raw = _canonical(plan)
    plan_path.write_bytes(plan_raw)

    manifest_path = target / backup.MANIFEST_NAME
    manifest = json.loads(manifest_path.read_bytes())
    for field in (
        "memory_table_names",
        "memory_vector_inventory",
        "memory_vector_lane_versions",
        "migration_0013_schema_sha256",
        "migration_0014_schema_sha256",
        "migration_0015_schema_sha256",
        "migration_revision_list_sha256",
        "source_migration_head",
    ):
        manifest.pop(field)
    manifest["backup_plan_sha256"] = backup._sha256_bytes(plan_raw)
    manifest["postgres"].pop("inventory")
    manifest["schema"] = backup.SCHEMA_MANIFEST_V1
    manifest["schema_version"] = 1
    manifest_path.write_bytes(_canonical(manifest))
    (target / backup.POSTGRES_INVENTORY).unlink()
    return repo, target


def _postgres_backup_inventory(
    path: Path,
    *,
    dump: Path,
    head: str,
    source_database: str,
    capture_mode: str = "source_backup",
    memory_present: bool = True,
) -> Path:
    payload = {
        "capture_mode": capture_mode,
        "global_alembic_head": head,
        "memory_table_names": (
            list(backup._REQUIRED_MEMORY_TABLES) if memory_present else []
        ),
        "memory_trigger_names": (
            list(backup._REQUIRED_MEMORY_TRIGGERS) if memory_present else []
        ),
        "memory_vector_inventory": (
            list(backup._REQUIRED_MEMORY_VECTOR_INVENTORY) if memory_present else []
        ),
        "postgres_dump_sha256": backup._sha256_file(dump),
        "schema": backup.SCHEMA_POSTGRES_BACKUP_INVENTORY,
        "schema_version": 1,
        "source_database": source_database,
        "skill_table_names": (
            list(backup._REQUIRED_SKILL_TABLES) if head >= "0014" else []
        ),
        "skill_trigger_names": (
            list(backup._REQUIRED_SKILL_TRIGGERS) if head >= "0014" else []
        ),
        "tenant_alembic_heads": [{"head": head, "tenant_id": "tenant-1"}],
        "tenant_memory_inventories": (
            [
                {
                    "memory_table_names": list(backup._REQUIRED_MEMORY_TABLES),
                    "memory_trigger_names": list(backup._REQUIRED_MEMORY_TRIGGERS),
                    "memory_vector_inventory": list(
                        backup._REQUIRED_MEMORY_VECTOR_INVENTORY
                    ),
                    "schema_name": "tenant_1",
                    "tenant_id": "tenant-1",
                }
            ]
            if memory_present
            else []
        ),
        "tenant_registry": [
            {
                "is_active": True,
                "schema_name": "tenant_1",
                "tenant_id": "tenant-1",
            }
        ],
    }
    path.write_bytes(_canonical(payload))
    return path


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
    assert verified["postgres_inventory_verified"] is True
    assert verified["source_migration_head"] == "0015"
    assert verified["memory_vector_lane_versions"] == ["v1", "v2"]
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
    assert restore["migration_mode"] == "same_revision"
    assert restore["source_migration_head"] == "0015"
    assert restore["target_migration_head"] == "0015"
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
    assert manifest["source_migration_head"] == "0015"
    assert manifest["memory_vector_lane_versions"] == ["v1", "v2"]
    assert len(manifest["migration_revision_list_sha256"]) == 64
    assert len(manifest["migration_0013_schema_sha256"]) == 64
    assert len(manifest["migration_0014_schema_sha256"]) == 64
    assert len(manifest["migration_0015_schema_sha256"]) == 64


def test_0013_backup_requires_canonical_0014_skill_upgrade_entry(
    tmp_path: Path,
) -> None:
    repo, target = _stage(tmp_path, head="0013")
    _write_migration(repo, "0014", "0013")
    inventory = _inventory(tmp_path / "databases.json")
    arguments = {
        "repo_root": str(repo),
        "backup_target": str(target),
        "target_database": "omnibase_restore_skills_upgrade",
        "database_inventory": str(inventory),
        "minio_restore_root": str(tmp_path / "restored-skills-minio"),
    }
    with pytest.raises(backup.BackupError, match="canonical compatibility entry"):
        backup.plan_restore(Namespace(**arguments))
    restore = backup.plan_restore(
        Namespace(
            **arguments,
            compatibility_entry="p5-skills-0013-to-0014",
        )
    )
    assert restore["migration_mode"] == "canonical_compatibility_upgrade"
    assert restore["source_migration_head"] == "0013"
    assert restore["target_migration_head"] == "0014"
    assert restore["compatibility_entry"]["entry_id"] == "p5-skills-0013-to-0014"
    assert restore["compatibility_entry"]["target_skill_table_names"] == list(
        backup._REQUIRED_SKILL_TABLES
    )
    assert restore["compatibility_entry"]["target_skill_trigger_names"] == list(
        backup._REQUIRED_SKILL_TRIGGERS
    )
    assert len(restore["migration_0014_schema_sha256"]) == 64
    assert restore["migration_0015_schema_sha256"] is None


def test_0014_backup_requires_canonical_0015_memory_bootstrap_upgrade_entry(
    tmp_path: Path,
) -> None:
    repo, target = _stage(tmp_path, head="0014")
    _write_migration(repo, "0015", "0014")
    inventory = _inventory(tmp_path / "databases.json")
    arguments = {
        "repo_root": str(repo),
        "backup_target": str(target),
        "target_database": "omnibase_restore_memory_bootstrap_upgrade",
        "database_inventory": str(inventory),
        "minio_restore_root": str(tmp_path / "restored-memory-bootstrap-minio"),
    }
    with pytest.raises(backup.BackupError, match="canonical compatibility entry"):
        backup.plan_restore(Namespace(**arguments))
    restore = backup.plan_restore(
        Namespace(
            **arguments,
            compatibility_entry="p5-memory-bootstrap-0014-to-0015",
        )
    )
    assert restore["migration_mode"] == "canonical_compatibility_upgrade"
    assert restore["source_migration_head"] == "0014"
    assert restore["target_migration_head"] == "0015"
    assert restore["compatibility_entry"]["entry_id"] == (
        "p5-memory-bootstrap-0014-to-0015"
    )
    assert (
        "verify_context_capsule_zero_token_constraint"
        in restore["compatibility_entry"]["required_commands"]
    )
    assert len(restore["migration_0015_schema_sha256"]) == 64


def test_seal_rejects_a_dump_inventory_observed_at_0012_as_0013(
    tmp_path: Path,
) -> None:
    repo, target = _prepare_backup(tmp_path)
    inventory = _postgres_backup_inventory(
        tmp_path / "postgres-inventory-0012.json",
        dump=target / backup.POSTGRES_DUMP,
        head="0012",
        source_database="omnibase",
        memory_present=False,
    )
    with pytest.raises(backup.BackupError, match="global migration head drifted"):
        backup.seal_assets(
            Namespace(
                repo_root=str(repo),
                backup_target=str(target),
                postgres_inventory=str(inventory),
            )
        )
    assert not (target / backup.MANIFEST_NAME).exists()


def test_postgres_inventory_must_be_canonical_and_dump_bound(tmp_path: Path) -> None:
    repo, target = _prepare_backup(tmp_path)
    inventory = _postgres_backup_inventory(
        tmp_path / "postgres-inventory.json",
        dump=target / backup.POSTGRES_DUMP,
        head="0015",
        source_database="omnibase",
    )
    value = json.loads(inventory.read_bytes())
    inventory.write_text(json.dumps(value, indent=2), encoding="utf-8")
    with pytest.raises(backup.BackupError, match="canonical"):
        backup.seal_assets(
            Namespace(
                repo_root=str(repo),
                backup_target=str(target),
                postgres_inventory=str(inventory),
            )
        )

    inventory.write_bytes(_canonical(value | {"postgres_dump_sha256": "0" * 64}))
    with pytest.raises(backup.BackupError, match="selected dump"):
        backup.seal_assets(
            Namespace(
                repo_root=str(repo),
                backup_target=str(target),
                postgres_inventory=str(inventory),
            )
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (
            lambda value: value["tenant_registry"][0].update(
                schema_name="tenant_drift"
            ),
            "registry binding drifted",
        ),
        (
            lambda value: value["tenant_memory_inventories"][0][
                "memory_trigger_names"
            ].pop(),
            "trigger set drifted",
        ),
        (
            lambda value: value["tenant_memory_inventories"][0][
                "memory_table_names"
            ].pop(),
            "Memory table set drifted",
        ),
        (
            lambda value: value["skill_trigger_names"].pop(),
            "Skill trigger set drifted",
        ),
        (
            lambda value: value["skill_table_names"].pop(),
            "Skill table set drifted",
        ),
    ],
)
def test_postgres_inventory_binds_tenant_registry_tables_and_triggers(
    tmp_path: Path,
    mutation: object,
    message: str,
) -> None:
    repo, target = _prepare_backup(tmp_path)
    inventory = _postgres_backup_inventory(
        tmp_path / "postgres-inventory.json",
        dump=target / backup.POSTGRES_DUMP,
        head="0015",
        source_database="omnibase",
    )
    value = json.loads(inventory.read_bytes())
    assert callable(mutation)
    mutation(value)
    inventory.write_bytes(_canonical(value))

    with pytest.raises(backup.BackupError, match=message):
        backup.seal_assets(
            Namespace(
                repo_root=str(repo),
                backup_target=str(target),
                postgres_inventory=str(inventory),
            )
        )


def test_capture_postgres_inventory_requires_explicit_database_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, target = _prepare_backup(tmp_path)
    output = tmp_path / "captured-inventory.json"
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(backup.BackupError, match="DATABASE_URL"):
        backup.capture_postgres_inventory(
            Namespace(
                repo_root=str(repo),
                postgres_dump=str(target / backup.POSTGRES_DUMP),
                output=str(output),
                source_database="omnibase",
                capture_mode="source_backup",
            )
        )

    assert not output.exists()


def test_embedded_postgres_inventory_drift_is_rejected(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    path = target / backup.POSTGRES_INVENTORY
    inventory = json.loads(path.read_bytes())
    inventory["tenant_alembic_heads"][0]["head"] = "0012"
    path.write_bytes(_canonical(inventory))
    with pytest.raises(backup.BackupError, match="tenant migration head drifted"):
        _verify(repo, target)


def test_legacy_v1_backup_requires_restore_new_evidence_for_0012_to_0013(
    tmp_path: Path,
) -> None:
    repo, target = _stage_v1(tmp_path)
    verified = _verify(repo, target)
    assert verified["backup_verified"] is True
    assert verified["legacy_v1"] is True
    assert verified["postgres_inventory_verified"] is False
    assert verified["source_migration_head"] == "0012"

    databases = _inventory(tmp_path / "databases.json")
    arguments = {
        "repo_root": str(repo),
        "backup_target": str(target),
        "target_database": "omnibase_restore_legacy_target",
        "database_inventory": str(databases),
        "minio_restore_root": str(tmp_path / "restored-minio"),
        "compatibility_entry": "p5-memory-0012-to-0013",
    }
    with pytest.raises(backup.BackupError, match="restore-new --postgres-inventory"):
        backup.plan_restore(Namespace(**arguments))

    restored_inventory = _postgres_backup_inventory(
        tmp_path / "legacy-restored-inventory.json",
        dump=target / backup.POSTGRES_DUMP,
        head="0012",
        source_database="omnibase_restore_legacy_inspection",
        capture_mode="restore_new_evidence",
        memory_present=False,
    )
    restore = backup.plan_restore(
        Namespace(**arguments, postgres_inventory=str(restored_inventory))
    )
    assert restore["migration_mode"] == "canonical_compatibility_upgrade"
    assert restore["source_migration_head"] == "0012"
    assert restore["target_migration_head"] == "0013"
    assert restore["legacy_restore_new_inventory_sha256"] == backup._sha256_file(
        restored_inventory
    )
    assert len(restore["compatibility_matrix_sha256"]) == 64
    assert restore["compatibility_entry"]["entry_id"] == ("p5-memory-0012-to-0013")
    assert (
        "restore_dump_into_new_database"
        in restore["compatibility_entry"]["required_commands"]
    )
    _, expected_matrix_sha256 = backup._compatibility_matrix(repo)
    assert restore["compatibility_matrix_sha256"] == expected_matrix_sha256


def test_legacy_restore_new_inventory_must_match_0012_dump(tmp_path: Path) -> None:
    repo, target = _stage_v1(tmp_path)
    databases = _inventory(tmp_path / "databases.json")
    restored_inventory = _postgres_backup_inventory(
        tmp_path / "legacy-restored-inventory.json",
        dump=target / backup.POSTGRES_DUMP,
        head="0013",
        source_database="omnibase_restore_legacy_inspection",
        capture_mode="restore_new_evidence",
        memory_present=False,
    )
    arguments = Namespace(
        repo_root=str(repo),
        backup_target=str(target),
        target_database="omnibase_restore_legacy_target",
        database_inventory=str(databases),
        minio_restore_root=str(tmp_path / "restored-minio"),
        compatibility_entry="p5-memory-0012-to-0013",
        postgres_inventory=str(restored_inventory),
    )
    with pytest.raises(backup.BackupError, match="global migration head drifted"):
        backup.plan_restore(arguments)

    value = json.loads(restored_inventory.read_bytes())
    value["global_alembic_head"] = "0012"
    value["tenant_alembic_heads"][0]["head"] = "0012"
    value["postgres_dump_sha256"] = "0" * 64
    restored_inventory.write_bytes(_canonical(value))
    with pytest.raises(backup.BackupError, match="selected dump"):
        backup.plan_restore(arguments)


def test_plan_restore_reads_verified_manifest_only_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, target = _stage(tmp_path)
    databases = _inventory(tmp_path / "databases.json")
    original = backup._load_canonical
    manifest_reads = 0

    def counted(path: Path) -> tuple[dict[str, object], bytes]:
        nonlocal manifest_reads
        if path.name == backup.MANIFEST_NAME:
            manifest_reads += 1
        return original(path)

    monkeypatch.setattr(backup, "_load_canonical", counted)
    backup.plan_restore(
        Namespace(
            repo_root=str(repo),
            backup_target=str(target),
            target_database="omnibase_restore_once",
            database_inventory=str(databases),
            minio_restore_root=str(tmp_path / "restored-minio"),
        )
    )
    assert manifest_reads == 1


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


@pytest.mark.parametrize("revision", ["0007", "0013", "0014", "0015"])
def test_repository_migration_byte_drift_is_rejected(
    tmp_path: Path, revision: str
) -> None:
    repo, target = _stage(tmp_path)
    migration = next(
        (repo / "backend/src/omnibase/migrations/versions").glob(f"{revision}_*.py")
    )
    migration.write_bytes(migration.read_bytes() + b"# byte drift\n")
    with pytest.raises(backup.BackupError, match="migration binding drifted"):
        _verify(repo, target)


def test_memory_vector_lane_binding_is_closed(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    path = target / backup.MANIFEST_NAME
    manifest = json.loads(path.read_bytes())
    manifest["memory_vector_lane_versions"] = ["v1"]
    path.write_bytes(_canonical(manifest))
    with pytest.raises(backup.BackupError, match="exactly v1/v2"):
        _verify(repo, target)


def test_seal_rejects_repository_advancing_after_plan(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _repo_fixture(repo)
    target = tmp_path / "cold" / "backup-advance"
    backup.plan_backup(
        Namespace(
            repo_root=str(repo),
            backup_target=str(target),
            source_database="omnibase",
        )
    )
    _write_migration(repo, "0016", "0015")
    with pytest.raises(backup.BackupError, match="drifted after backup planning"):
        backup.seal_assets(
            Namespace(
                repo_root=str(repo),
                backup_target=str(target),
                postgres_inventory=str(tmp_path / "unused-inventory.json"),
            )
        )


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
    base = {
        "repo_root": str(repo),
        "backup_target": str(target),
        "database_inventory": str(inventory),
        "minio_restore_root": str(tmp_path / "restore-minio"),
    }
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


def test_arbitrary_forward_restore_is_not_in_the_canonical_matrix(
    tmp_path: Path,
) -> None:
    repo, target = _stage(tmp_path)
    _write_migration(repo, "0016", "0015")
    inventory = _inventory(tmp_path / "databases.json")
    arguments = {
        "repo_root": str(repo),
        "backup_target": str(target),
        "target_database": "omnibase_restore_forward",
        "database_inventory": str(inventory),
        "minio_restore_root": str(tmp_path / "restore-minio"),
    }
    with pytest.raises(backup.BackupError, match="canonical compatibility entry"):
        backup.plan_restore(
            Namespace(
                **arguments,
                compatibility_entry="p5-memory-0012-to-0013",
            )
        )


def test_compatibility_entry_is_forbidden_at_same_revision(
    tmp_path: Path,
) -> None:
    repo, target = _stage(tmp_path)
    inventory = _inventory(tmp_path / "databases.json")
    with pytest.raises(backup.BackupError, match="forbidden at the same revision"):
        backup.plan_restore(
            Namespace(
                repo_root=str(repo),
                backup_target=str(target),
                target_database="omnibase_restore_same",
                database_inventory=str(inventory),
                minio_restore_root=str(tmp_path / "restore-minio"),
                compatibility_entry="p5-memory-0012-to-0013",
            )
        )


def test_newer_backup_is_rejected_by_an_older_repository(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path, head="0014")
    next((repo / "backend/src/omnibase/migrations/versions").glob("0014_*.py")).unlink()
    inventory = _inventory(tmp_path / "databases.json")
    with pytest.raises(
        backup.BackupError, match="source migration head is unavailable"
    ):
        backup.plan_restore(
            Namespace(
                repo_root=str(repo),
                backup_target=str(target),
                target_database="omnibase_restore_older_repo",
                database_inventory=str(inventory),
                minio_restore_root=str(tmp_path / "restore-minio"),
                compatibility_entry="p5-memory-0012-to-0013",
            )
        )


def test_branched_target_migration_graph_is_rejected(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    _write_migration(repo, "0016", "0015")
    _write_migration(repo, "0017", "0015")
    inventory = _inventory(tmp_path / "databases.json")
    with pytest.raises(backup.BackupError, match="exactly one head"):
        backup.plan_restore(
            Namespace(
                repo_root=str(repo),
                backup_target=str(target),
                target_database="omnibase_restore_branch",
                database_inventory=str(inventory),
                minio_restore_root=str(tmp_path / "restore-minio"),
                compatibility_entry="p5-memory-0012-to-0013",
            )
        )


def test_redis_archive_and_unexpected_files_are_rejected(tmp_path: Path) -> None:
    repo, target = _stage(tmp_path)
    extra = target / "redis" / "dump.rdb"
    extra.parent.mkdir()
    extra.write_bytes(b"not-authoritative")
    with pytest.raises(backup.BackupError, match="closure drifted"):
        _verify(repo, target)
