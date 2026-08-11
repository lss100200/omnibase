#!/usr/bin/env python3
"""Plan, capture and verify the P5 personal cold-backup boundary.

``capture-postgres-inventory`` is the only online path. It requires an
explicitly injected ``DATABASE_URL`` and reads PostgreSQL through a
REPEATABLE-READ, read-only transaction while the operator keeps the same cold
writer barrier used to create the selected dump. It never reads the repository
root ``.env`` and never prints connection material.

``plan-backup``, ``seal-assets``, ``verify-backup`` and ``plan-restore`` remain
offline. They do not invoke PostgreSQL, MinIO, Redis, Docker, Compose or a
Runtime. Operators stage already-created artifacts in the fixed layout; the
seal binds canonical inventory bytes, paths, sizes and SHA-256 digests; restore
planning emits a non-executing restore-new plan.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any

SCHEMA_PLAN = "omnibase.p5-personal-backup-plan.v2"
SCHEMA_MANIFEST = "omnibase.p5-personal-backup-manifest.v2"
SCHEMA_DATABASE_INVENTORY = "omnibase.postgresql-database-inventory.v1"
SCHEMA_POSTGRES_BACKUP_INVENTORY = "omnibase.postgresql-backup-inventory.v1"
SCHEMA_RESTORE_PLAN = "omnibase.p5-personal-restore-plan.v2"
SCHEMA_PLAN_V1 = "omnibase.p5-personal-backup-plan.v1"
SCHEMA_MANIFEST_V1 = "omnibase.p5-personal-backup-manifest.v1"
PLAN_NAME = "backup-plan.json"
MANIFEST_NAME = "backup-manifest.json"
RELEASE_RECEIPT = "release/receipt.json"
POSTGRES_DUMP = "postgres/database.dump"
POSTGRES_INVENTORY = "postgres/inventory.json"
RUNTIME_CONFIG = "personal-runtime/config.json"
MINIO_ROOT = "minio/export"
RUNTIME_STATE_ROOT = "personal-runtime/state"
READINESS_ROOT = "personal-runtime/readiness"
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_REVISION = re.compile(r"^[0-9]{4}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MIGRATION_DIRECTORY = Path("backend/src/omnibase/migrations/versions")
_MEMORY_SCHEMA_REVISION = "0013"
_REQUIRED_MEMORY_VECTOR_LANES = ("v1", "v2")
_LEGACY_V1_SOURCE_HEAD = "0012"
_REQUIRED_MEMORY_TABLES = (
    "context_capsule_items",
    "context_capsules",
    "memories",
    "memory_candidates",
    "memory_effects",
    "memory_embeddings_v1",
    "memory_embeddings_v2",
    "memory_review_evidence",
    "memory_tombstones",
    "memory_versions",
)
_REQUIRED_MEMORY_VECTOR_INVENTORY = (
    {"dimensions": 1024, "table": "memory_embeddings_v1", "version": "v1"},
    {"dimensions": 1536, "table": "memory_embeddings_v2", "version": "v2"},
)
_REQUIRED_MEMORY_TRIGGERS = tuple(
    sorted(
        (
            "context_capsule_items_append_only",
            "context_capsule_items_binding_guard",
            "context_capsules_append_only",
            "memories_candidate_publication_binding",
            "memories_state_guard",
            "memory_candidates_publication_binding",
            "memory_candidates_state_guard",
            "memory_effects_state_guard",
            "memory_embeddings_v1_payload_guard",
            "memory_embeddings_v2_payload_guard",
            "memory_review_evidence_append_only",
            "memory_review_evidence_insert_binding",
            "memory_tombstones_state_guard",
            "memory_versions_payload_guard",
            *(f"{table}_tenant_schema_guard" for table in _REQUIRED_MEMORY_TABLES),
        )
    )
)


class BackupError(ValueError):
    """A fail-closed backup or restore-plan validation error."""


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _literal_assignment(tree: ast.Module, name: str) -> object:
    for statement in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(statement, ast.AnnAssign):
            target = statement.target
            value = statement.value
        elif isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target = statement.targets[0]
            value = statement.value
        if isinstance(target, ast.Name) and target.id == name and value is not None:
            try:
                return ast.literal_eval(value)
            except (TypeError, ValueError) as exc:
                raise BackupError(f"migration {name} must be a literal") from exc
    raise BackupError(f"migration is missing {name}")


def _migration_record(repo: Path, path: Path) -> dict[str, object]:
    _require_plain_file(path, root=repo)
    raw = path.read_bytes()
    try:
        tree = ast.parse(raw, filename=path.as_posix())
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise BackupError(f"invalid migration source: {path.name}") from exc
    revision = _literal_assignment(tree, "revision")
    down_revision = _literal_assignment(tree, "down_revision")
    if not isinstance(revision, str) or not _REVISION.fullmatch(revision):
        raise BackupError(f"invalid migration revision: {path.name}")
    if down_revision is not None and (
        not isinstance(down_revision, str) or not _REVISION.fullmatch(down_revision)
    ):
        raise BackupError(f"migration graph is not linear: {path.name}")
    if not path.name.startswith(f"{revision}_"):
        raise BackupError(f"migration filename/revision drifted: {path.name}")
    return {
        "down_revision": down_revision,
        "path": path.relative_to(repo).as_posix(),
        "revision": revision,
        "sha256": _sha256_bytes(raw),
        "size": len(raw),
    }


def _vector_lane_versions(raw: bytes) -> tuple[str, ...]:
    try:
        tree = ast.parse(raw.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise BackupError("invalid migration 0013 source") from exc
    configured = _literal_assignment(tree, "_MEMORY_VECTOR_LANE_VERSIONS")
    if not isinstance(configured, (tuple, list)):
        raise BackupError("migration 0013 memory vector lanes must be a literal list")
    lane_values: list[str] = []
    for value in configured:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise BackupError("migration 0013 memory vector lane value is invalid")
        lane_values.append(f"v{value}")
    if len(lane_values) != len(set(lane_values)):
        raise BackupError("migration 0013 memory vector lanes must be unique")
    ordered = tuple(sorted(lane_values, key=lambda item: int(item[1:])))
    if ordered != _REQUIRED_MEMORY_VECTOR_LANES:
        raise BackupError("migration 0013 memory vector lane set must be exactly v1/v2")
    return ordered


def _memory_table_names(raw: bytes) -> tuple[str, ...]:
    try:
        tree = ast.parse(raw.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise BackupError("invalid migration 0013 source") from exc
    configured = _literal_assignment(tree, "_MEMORY_TABLES")
    if not isinstance(configured, (tuple, list)) or any(
        not isinstance(value, str) for value in configured
    ):
        raise BackupError("migration 0013 memory tables must be a literal list")
    ordered = tuple(sorted(configured))
    if len(ordered) != len(set(ordered)) or ordered != _REQUIRED_MEMORY_TABLES:
        raise BackupError("migration 0013 memory table set drifted")
    return ordered


def _memory_vector_inventory(raw: bytes) -> tuple[dict[str, object], ...]:
    try:
        tree = ast.parse(raw.decode("utf-8"))
    except (SyntaxError, UnicodeDecodeError) as exc:
        raise BackupError("invalid migration 0013 source") from exc
    lanes: list[dict[str, object]] = []
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Name)
            or node.func.id != "_create_embedding_lane"
            or len(node.args) != 2
        ):
            continue
        try:
            table = ast.literal_eval(node.args[0])
            dimensions = ast.literal_eval(node.args[1])
        except (TypeError, ValueError) as exc:
            raise BackupError("migration 0013 vector lane must be literal") from exc
        if not isinstance(table, str) or not isinstance(dimensions, int):
            raise BackupError("migration 0013 vector lane is invalid")
        version = table.removeprefix("memory_embeddings_")
        lanes.append({"dimensions": dimensions, "table": table, "version": version})
    ordered = tuple(sorted(lanes, key=lambda item: str(item["version"])))
    if ordered != _REQUIRED_MEMORY_VECTOR_INVENTORY:
        raise BackupError("migration 0013 memory vector inventory drifted")
    return ordered


def _load_migration_records(repo: Path, versions: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    for path in sorted(versions.glob("*.py")):
        if path.name == "__init__.py":
            continue
        record = _migration_record(repo, path)
        revision = str(record["revision"])
        if revision in records:
            raise BackupError(f"duplicate migration revision: {revision}")
        records[revision] = record
    if not records:
        raise BackupError("migration revision list is empty")
    return records


def _repository_migration_head(records: dict[str, dict[str, object]]) -> str:
    referenced = {
        str(record["down_revision"])
        for record in records.values()
        if record["down_revision"] is not None
    }
    if not referenced.issubset(records):
        raise BackupError("migration graph contains an unknown parent")
    heads = sorted(set(records) - referenced)
    if len(heads) != 1:
        raise BackupError("migration graph must have exactly one head")
    return heads[0]


def _migration_chain(
    records: dict[str, dict[str, object]], selected_head: str
) -> list[dict[str, object]]:
    chain: list[dict[str, object]] = []
    cursor: str | None = selected_head
    seen: set[str] = set()
    while cursor is not None:
        if cursor in seen:
            raise BackupError("migration graph contains a cycle")
        seen.add(cursor)
        record = records[cursor]
        chain.append(record)
        parent = record["down_revision"]
        cursor = None if parent is None else str(parent)
    chain.reverse()
    return chain


def _repository_migration_facts(
    repo: Path, *, through_head: str | None = None
) -> dict[str, object]:
    versions = repo / _MIGRATION_DIRECTORY
    if not versions.is_dir():
        raise BackupError("migration directory is missing")
    records = _load_migration_records(repo, versions)
    repository_head = _repository_migration_head(records)
    selected_head = repository_head if through_head is None else through_head
    if selected_head not in records:
        raise BackupError(f"source migration head is unavailable: {selected_head}")
    chain = _migration_chain(records, selected_head)
    if through_head is None and len(chain) != len(records):
        raise BackupError("migration graph is branched or disconnected")
    if _MEMORY_SCHEMA_REVISION not in {str(item["revision"]) for item in chain}:
        return {
            "memory_table_names": [],
            "memory_vector_inventory": [],
            "memory_vector_lane_versions": [],
            "migration_0013_schema_sha256": None,
            "migration_revision_list_sha256": _sha256_bytes(_canonical(chain)),
            "repository_migration_head": repository_head,
            "source_migration_head": selected_head,
        }
    schema_record = records[_MEMORY_SCHEMA_REVISION]
    schema_path = repo.joinpath(*PurePosixPath(str(schema_record["path"])).parts)
    schema_raw = schema_path.read_bytes()
    return {
        "memory_table_names": list(_memory_table_names(schema_raw)),
        "memory_vector_inventory": list(_memory_vector_inventory(schema_raw)),
        "memory_vector_lane_versions": list(_vector_lane_versions(schema_raw)),
        "migration_0013_schema_sha256": _sha256_bytes(schema_raw),
        "migration_revision_list_sha256": _sha256_bytes(_canonical(chain)),
        "repository_migration_head": repository_head,
        "source_migration_head": selected_head,
    }


def _migration_binding(value: dict[str, object]) -> dict[str, object]:
    return {
        "memory_table_names": value["memory_table_names"],
        "memory_vector_inventory": value["memory_vector_inventory"],
        "memory_vector_lane_versions": value["memory_vector_lane_versions"],
        "migration_0013_schema_sha256": value["migration_0013_schema_sha256"],
        "migration_revision_list_sha256": value["migration_revision_list_sha256"],
        "source_migration_head": value["source_migration_head"],
    }


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
        raise BackupError("manifest path must be a non-empty canonical POSIX relative path")
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


def _logical_id(value: object, *, where: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 128
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        raise BackupError(f"{where} must be a non-empty printable logical identifier")
    return value


def _validate_migration_binding(value: dict[str, Any], *, where: str) -> None:
    head = value.get("source_migration_head")
    if not isinstance(head, str) or not _REVISION.fullmatch(head):
        raise BackupError(f"{where}.source_migration_head must be a four-digit revision")
    for field in (
        "migration_revision_list_sha256",
        "migration_0013_schema_sha256",
    ):
        digest = value.get(field)
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise BackupError(f"{where}.{field} must be lowercase SHA-256")
    lanes = value.get("memory_vector_lane_versions")
    if lanes != list(_REQUIRED_MEMORY_VECTOR_LANES):
        raise BackupError(f"{where}.memory_vector_lane_versions must be exactly v1/v2")
    if value.get("memory_table_names") != list(_REQUIRED_MEMORY_TABLES):
        raise BackupError(f"{where}.memory_table_names drifted")
    if value.get("memory_vector_inventory") != list(_REQUIRED_MEMORY_VECTOR_INVENTORY):
        raise BackupError(f"{where}.memory_vector_inventory drifted")


def _validate_plan_v1(plan: dict[str, Any]) -> None:
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
        plan["schema"] != SCHEMA_PLAN_V1
        or plan["schema_version"] != 1
        or plan["execution_authorized"] is not False
    ):
        raise BackupError("invalid backup plan posture")


def _validate_plan_v2(plan: dict[str, Any]) -> None:
    _exact(
        plan,
        {
            "execution_authorized",
            "layout",
            "memory_table_names",
            "memory_vector_inventory",
            "memory_vector_lane_versions",
            "migration_0013_schema_sha256",
            "migration_revision_list_sha256",
            "redis",
            "schema",
            "schema_version",
            "source_database",
            "source_migration_head",
        },
        where="backup plan",
    )
    if (
        plan["schema"] != SCHEMA_PLAN
        or plan["schema_version"] != 2
        or plan["execution_authorized"] is not False
    ):
        raise BackupError("invalid backup plan posture")
    _validate_migration_binding(plan, where="backup plan")


def _validate_plan(plan: dict[str, Any]) -> None:
    is_v1 = plan.get("schema") == SCHEMA_PLAN_V1 and plan.get("schema_version") == 1
    if is_v1:
        _validate_plan_v1(plan)
    else:
        _validate_plan_v2(plan)
    _identifier(plan["source_database"], where="source database")
    expected_layout = {
        "release_receipt": RELEASE_RECEIPT,
        "postgres_dump": POSTGRES_DUMP,
        "minio_root": MINIO_ROOT,
        "runtime_config": RUNTIME_CONFIG,
        "runtime_state_root": RUNTIME_STATE_ROOT,
        "runtime_readiness_root": READINESS_ROOT,
    }
    if not is_v1:
        expected_layout["postgres_inventory"] = POSTGRES_INVENTORY
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
    if not isinstance(value["size"], int) or isinstance(value["size"], bool) or value["size"] <= 0:
        raise BackupError(f"{where}.size must be a positive integer")
    if not isinstance(value["sha256"], str) or not _SHA256.fullmatch(value["sha256"]):
        raise BackupError(f"{where}.sha256 must be lowercase SHA-256")
    return value


def _validate_tenant_heads(value: Any, *, expected_head: str) -> None:
    if not isinstance(value, list) or not value:
        raise BackupError("PostgreSQL inventory tenant heads must be non-empty")
    tenant_ids: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise BackupError(f"tenant_alembic_heads[{index}] must be an object")
        _exact(item, {"head", "tenant_id"}, where=f"tenant_alembic_heads[{index}]")
        tenant_id = _logical_id(item["tenant_id"], where=f"tenant_alembic_heads[{index}].tenant_id")
        if item["head"] != expected_head:
            raise BackupError("PostgreSQL inventory tenant migration head drifted")
        tenant_ids.append(tenant_id)
    if tenant_ids != sorted(set(tenant_ids)):
        raise BackupError("PostgreSQL inventory tenant heads must be sorted and unique")


def _validate_tenant_registry(value: Any) -> dict[str, str]:
    if not isinstance(value, list) or not value:
        raise BackupError("PostgreSQL inventory tenant registry must be non-empty")
    registry: dict[str, str] = {}
    schema_names: list[str] = []
    for index, item in enumerate(value):
        where = f"tenant_registry[{index}]"
        if not isinstance(item, dict):
            raise BackupError(f"{where} must be an object")
        _exact(item, {"is_active", "schema_name", "tenant_id"}, where=where)
        tenant_id = _logical_id(item["tenant_id"], where=f"{where}.tenant_id")
        schema_name = _identifier(item["schema_name"], where=f"{where}.schema_name")
        if type(item["is_active"]) is not bool:
            raise BackupError(f"{where}.is_active must be a boolean")
        registry[tenant_id] = schema_name
        schema_names.append(schema_name)
    if list(registry) != sorted(registry) or len(registry) != len(value):
        raise BackupError("PostgreSQL inventory tenant registry must be sorted and unique")
    if len(schema_names) != len(set(schema_names)):
        raise BackupError("PostgreSQL inventory tenant schemas must be unique")
    return registry


def _validate_tenant_memory_inventories(value: Any, *, registry: dict[str, str]) -> None:
    if not isinstance(value, list) or not value:
        raise BackupError("PostgreSQL tenant Memory inventories must be non-empty")
    tenant_ids: list[str] = []
    for index, item in enumerate(value):
        where = f"tenant_memory_inventories[{index}]"
        if not isinstance(item, dict):
            raise BackupError(f"{where} must be an object")
        _exact(
            item,
            {
                "memory_table_names",
                "memory_trigger_names",
                "memory_vector_inventory",
                "schema_name",
                "tenant_id",
            },
            where=where,
        )
        tenant_id = _logical_id(item["tenant_id"], where=f"{where}.tenant_id")
        schema_name = _identifier(item["schema_name"], where=f"{where}.schema_name")
        if registry.get(tenant_id) != schema_name:
            raise BackupError("PostgreSQL tenant Memory inventory registry binding drifted")
        if item["memory_table_names"] != list(_REQUIRED_MEMORY_TABLES):
            raise BackupError("PostgreSQL tenant Memory table set drifted")
        if item["memory_trigger_names"] != list(_REQUIRED_MEMORY_TRIGGERS):
            raise BackupError("PostgreSQL tenant Memory trigger set drifted")
        if item["memory_vector_inventory"] != list(_REQUIRED_MEMORY_VECTOR_INVENTORY):
            raise BackupError("PostgreSQL tenant Memory vector lanes drifted")
        tenant_ids.append(tenant_id)
    if tenant_ids != sorted(registry) or len(tenant_ids) != len(set(tenant_ids)):
        raise BackupError("PostgreSQL tenant Memory inventories must cover the registry exactly")


def _validate_postgres_inventory(
    value: dict[str, Any],
    *,
    capture_mode: str,
    expected_database: str | None,
    expected_dump_sha256: str,
    expected_head: str,
    memory_present: bool,
) -> None:
    _exact(
        value,
        {
            "capture_mode",
            "global_alembic_head",
            "memory_table_names",
            "memory_trigger_names",
            "memory_vector_inventory",
            "postgres_dump_sha256",
            "schema",
            "schema_version",
            "source_database",
            "tenant_alembic_heads",
            "tenant_memory_inventories",
            "tenant_registry",
        },
        where="PostgreSQL backup inventory",
    )
    if (
        value["schema"] != SCHEMA_POSTGRES_BACKUP_INVENTORY
        or value["schema_version"] != 1
        or value["capture_mode"] != capture_mode
    ):
        raise BackupError("unsupported PostgreSQL backup inventory")
    database = _identifier(value["source_database"], where="inventory source database")
    if expected_database is not None and database != expected_database:
        raise BackupError("PostgreSQL inventory source database drifted")
    if capture_mode == "restore_new_evidence" and not database.startswith("omnibase_restore_"):
        raise BackupError("legacy inventory must come from a restore-new database")
    if value["postgres_dump_sha256"] != expected_dump_sha256:
        raise BackupError("PostgreSQL inventory is not bound to the selected dump")
    if value["global_alembic_head"] != expected_head:
        raise BackupError("PostgreSQL inventory global migration head drifted")
    _validate_tenant_heads(value["tenant_alembic_heads"], expected_head=expected_head)
    registry = _validate_tenant_registry(value["tenant_registry"])
    head_ids = [str(item["tenant_id"]) for item in value["tenant_alembic_heads"]]
    if head_ids != sorted(registry):
        raise BackupError("PostgreSQL tenant heads must cover the registry exactly")
    expected_tables: list[str] = list(_REQUIRED_MEMORY_TABLES) if memory_present else []
    expected_triggers: list[str] = list(_REQUIRED_MEMORY_TRIGGERS) if memory_present else []
    expected_vectors: list[dict[str, object]] = (
        list(_REQUIRED_MEMORY_VECTOR_INVENTORY) if memory_present else []
    )
    if value["memory_table_names"] != expected_tables:
        raise BackupError("PostgreSQL inventory memory table set drifted")
    if value["memory_trigger_names"] != expected_triggers:
        raise BackupError("PostgreSQL inventory memory trigger set drifted")
    if value["memory_vector_inventory"] != expected_vectors:
        raise BackupError("PostgreSQL inventory memory vector lanes drifted")
    if memory_present:
        _validate_tenant_memory_inventories(value["tenant_memory_inventories"], registry=registry)
    elif value["tenant_memory_inventories"] != []:
        raise BackupError("legacy PostgreSQL inventory must not claim Memory tenant evidence")


def _load_postgres_inventory(
    path: Path,
    *,
    capture_mode: str,
    expected_database: str | None,
    expected_dump_sha256: str,
    expected_head: str,
    memory_present: bool,
) -> tuple[dict[str, Any], bytes]:
    value, raw = _load_canonical(path)
    _validate_postgres_inventory(
        value,
        capture_mode=capture_mode,
        expected_database=expected_database,
        expected_dump_sha256=expected_dump_sha256,
        expected_head=expected_head,
        memory_present=memory_present,
    )
    return value, raw


def _one_revision(values: list[object], *, where: str) -> str:
    revisions = [str(value) for value in values]
    if len(revisions) != 1 or _REVISION.fullmatch(revisions[0]) is None:
        raise BackupError(f"{where} must contain exactly one four-digit revision")
    return revisions[0]


def _observed_vector_inventory(rows: list[tuple[object, object]]) -> list[dict[str, object]]:
    inventory: list[dict[str, object]] = []
    for raw_table, raw_type in rows:
        table = str(raw_table)
        match = re.fullmatch(r"vector\(([0-9]+)\)", str(raw_type))
        if match is None or not table.startswith("memory_embeddings_"):
            raise BackupError("PostgreSQL Memory vector type inventory is invalid")
        inventory.append(
            {
                "dimensions": int(match.group(1)),
                "table": table,
                "version": table.removeprefix("memory_embeddings_"),
            }
        )
    return sorted(inventory, key=lambda item: str(item["table"]))


def _capture_postgres_inventory_value(
    connection: Any,
    *,
    capture_mode: str,
    expected_database: str,
    expected_head: str,
    postgres_dump_sha256: str,
) -> dict[str, Any]:
    from sqlalchemy import text

    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    database = str(connection.execute(text("SELECT current_database()")).scalar_one())
    if database != expected_database:
        raise BackupError("connected PostgreSQL database does not match --source-database")
    global_head = _one_revision(
        list(
            connection.execute(
                text("SELECT version_num FROM omnibase_meta.alembic_version ORDER BY version_num")
            ).scalars()
        ),
        where="global alembic_version",
    )
    tenants = [
        (str(row[0]), _identifier(str(row[1]), where="tenant schema"), bool(row[2]))
        for row in connection.execute(
            text(
                "SELECT id::text, schema_name, is_active FROM omnibase_meta.tenants "
                "ORDER BY id::text"
            )
        ).all()
    ]
    if not tenants:
        raise BackupError("PostgreSQL inventory requires at least one registered tenant")

    tenant_heads: list[dict[str, object]] = []
    tenant_memory: list[dict[str, object]] = []
    tenant_registry: list[dict[str, object]] = []
    for tenant_id, schema_name, is_active in tenants:
        tenant_head = _one_revision(
            list(
                connection.execute(
                    text(
                        f'SELECT version_num FROM "{schema_name}".alembic_version '  # noqa: S608 -- schema is validated from the server-owned tenant registry
                        "ORDER BY version_num"
                    )
                ).scalars()
            ),
            where=f"tenant {tenant_id} alembic_version",
        )
        table_names = sorted(
            str(value)
            for value in connection.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = :schema AND table_name = ANY(:tables) "
                    "ORDER BY table_name"
                ),
                {"schema": schema_name, "tables": list(_REQUIRED_MEMORY_TABLES)},
            ).scalars()
        )
        trigger_names = sorted(
            str(value)
            for value in connection.execute(
                text(
                    "SELECT t.tgname FROM pg_trigger t "
                    "JOIN pg_class c ON c.oid = t.tgrelid "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = :schema AND NOT t.tgisinternal "
                    "AND c.relname = ANY(:tables) ORDER BY t.tgname"
                ),
                {"schema": schema_name, "tables": list(_REQUIRED_MEMORY_TABLES)},
            ).scalars()
        )
        vector_inventory = _observed_vector_inventory(
            [
                (row[0], row[1])
                for row in connection.execute(
                    text(
                        "SELECT c.relname, format_type(a.atttypid, a.atttypmod) "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "JOIN pg_attribute a ON a.attrelid = c.oid "
                        "WHERE n.nspname = :schema AND a.attname = 'embedding' "
                        "AND c.relname = ANY(:tables) ORDER BY c.relname"
                    ),
                    {
                        "schema": schema_name,
                        "tables": ["memory_embeddings_v1", "memory_embeddings_v2"],
                    },
                ).all()
            ]
        )
        tenant_registry.append(
            {
                "is_active": is_active,
                "schema_name": schema_name,
                "tenant_id": tenant_id,
            }
        )
        tenant_heads.append({"head": tenant_head, "tenant_id": tenant_id})
        tenant_memory.append(
            {
                "memory_table_names": table_names,
                "memory_trigger_names": trigger_names,
                "memory_vector_inventory": vector_inventory,
                "schema_name": schema_name,
                "tenant_id": tenant_id,
            }
        )

    inventory = {
        "capture_mode": capture_mode,
        "global_alembic_head": global_head,
        "memory_table_names": list(_REQUIRED_MEMORY_TABLES),
        "memory_trigger_names": list(_REQUIRED_MEMORY_TRIGGERS),
        "memory_vector_inventory": list(_REQUIRED_MEMORY_VECTOR_INVENTORY),
        "postgres_dump_sha256": postgres_dump_sha256,
        "schema": SCHEMA_POSTGRES_BACKUP_INVENTORY,
        "schema_version": 1,
        "source_database": database,
        "tenant_alembic_heads": tenant_heads,
        "tenant_memory_inventories": tenant_memory,
        "tenant_registry": tenant_registry,
    }
    _validate_postgres_inventory(
        inventory,
        capture_mode=capture_mode,
        expected_database=expected_database,
        expected_dump_sha256=postgres_dump_sha256,
        expected_head=expected_head,
        memory_present=expected_head >= _MEMORY_SCHEMA_REVISION,
    )
    return inventory


def capture_postgres_inventory(args: argparse.Namespace) -> dict[str, Any]:
    repo = _absolute(args.repo_root, where="repo root", must_exist=True)
    dump = _absolute(args.postgres_dump, where="PostgreSQL dump", must_exist=True)
    _require_plain_file(dump, root=dump.parent)
    output = _absolute(args.output, where="PostgreSQL inventory output")
    if output.exists():
        raise BackupError("PostgreSQL inventory output must be a new path")
    if not output.parent.is_dir():
        raise BackupError("PostgreSQL inventory output parent must exist")
    _reject_symlink_components(output.parent)
    if output == dump:
        raise BackupError("PostgreSQL inventory output must differ from the dump")
    source_database = _identifier(args.source_database, where="source database")
    if args.capture_mode == "restore_new_evidence" and not source_database.startswith(
        "omnibase_restore_"
    ):
        raise BackupError("restore-new capture requires an omnibase_restore_* database")
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise BackupError("DATABASE_URL must be injected explicitly for inventory capture")
    migration = _repository_migration_facts(repo)
    expected_head = str(migration["repository_migration_head"])
    dump_sha256 = _sha256_file(dump)
    engine = None
    try:
        from sqlalchemy import create_engine

        engine = create_engine(database_url)
        with engine.connect() as connection, connection.begin():
            inventory = _capture_postgres_inventory_value(
                connection,
                capture_mode=args.capture_mode,
                expected_database=source_database,
                expected_head=expected_head,
                postgres_dump_sha256=dump_sha256,
            )
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("PostgreSQL inventory capture failed") from exc
    finally:
        if engine is not None:
            engine.dispose()
    raw = _canonical(inventory)
    with output.open("xb") as stream:
        stream.write(raw)
    return {
        "capture_mode": args.capture_mode,
        "execution_authorized": False,
        "global_alembic_head": inventory["global_alembic_head"],
        "inventory_path": str(output),
        "inventory_sha256": _sha256_bytes(raw),
        "postgres_dump_sha256": dump_sha256,
        "source_database": source_database,
        "tenant_count": len(inventory["tenant_registry"]),
    }


def _validate_postgres_manifest(value: Any, *, legacy: bool) -> None:
    if not isinstance(value, dict):
        raise BackupError("postgres must be an object")
    fields = {"artifact", "format"}
    if not legacy:
        fields.add("inventory")
    _exact(value, fields, where="postgres")
    if value["format"] != "custom":
        raise BackupError("PostgreSQL dump format must be custom")
    _validate_entry(value["artifact"], where="postgres.artifact")
    if not legacy:
        inventory = _validate_entry(value["inventory"], where="postgres.inventory")
        if inventory["path"] != POSTGRES_INVENTORY:
            raise BackupError("PostgreSQL inventory path drifted")


def _validate_minio_manifest(value: Any) -> None:
    if not isinstance(value, dict):
        raise BackupError("minio must be an object")
    _exact(value, {"authoritative", "files", "root"}, where="minio")
    if value["authoritative"] is not True or value["root"] != MINIO_ROOT:
        raise BackupError("invalid MinIO inventory posture")
    _validate_inventory(value["files"], MINIO_ROOT, where="minio.files")


def _validate_personal_runtime_manifest(value: Any) -> None:
    if not isinstance(value, dict):
        raise BackupError("personal_runtime must be an object")
    _exact(value, {"config", "readiness", "state"}, where="personal_runtime")
    _validate_entry(value["config"], where="personal_runtime.config")
    for section, root_name in (
        ("state", RUNTIME_STATE_ROOT),
        ("readiness", READINESS_ROOT),
    ):
        section_value = value[section]
        if not isinstance(section_value, dict):
            raise BackupError(f"personal_runtime.{section} must be an object")
        _exact(section_value, {"files", "root"}, where=f"personal_runtime.{section}")
        if section_value["root"] != root_name:
            raise BackupError(f"personal_runtime.{section}.root drifted")
        _validate_inventory(
            section_value["files"],
            root_name,
            where=f"personal_runtime.{section}.files",
        )


def _validate_manifest_policies(manifest: dict[str, Any]) -> None:
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


def _validate_manifest_v1(manifest: dict[str, Any]) -> None:
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
        manifest["schema"] != SCHEMA_MANIFEST_V1
        or manifest["schema_version"] != 1
        or manifest["execution_authorized"] is not False
    ):
        raise BackupError("invalid backup manifest posture")


def _validate_manifest_v2(manifest: dict[str, Any]) -> None:
    _exact(
        manifest,
        {
            "backup_plan_sha256",
            "execution_authorized",
            "memory_table_names",
            "memory_vector_inventory",
            "memory_vector_lane_versions",
            "migration_0013_schema_sha256",
            "migration_revision_list_sha256",
            "minio",
            "personal_runtime",
            "postgres",
            "redis",
            "release_receipt",
            "restore_policy",
            "schema",
            "schema_version",
            "source_database",
            "source_migration_head",
        },
        where="backup manifest",
    )
    if (
        manifest["schema"] != SCHEMA_MANIFEST
        or manifest["schema_version"] != 2
        or manifest["execution_authorized"] is not False
    ):
        raise BackupError("invalid backup manifest posture")
    _validate_migration_binding(manifest, where="backup manifest")


def _validate_manifest_shape(manifest: dict[str, Any]) -> None:
    legacy = manifest.get("schema") == SCHEMA_MANIFEST_V1 and manifest.get("schema_version") == 1
    if legacy:
        _validate_manifest_v1(manifest)
    else:
        _validate_manifest_v2(manifest)
    if not _SHA256.fullmatch(str(manifest["backup_plan_sha256"])):
        raise BackupError("invalid backup plan digest")
    _identifier(manifest["source_database"], where="source database")
    _validate_entry(manifest["release_receipt"], where="release_receipt")
    _validate_postgres_manifest(manifest["postgres"], legacy=legacy)
    _validate_minio_manifest(manifest["minio"])
    _validate_personal_runtime_manifest(manifest["personal_runtime"])
    _validate_manifest_policies(manifest)


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
    if "inventory" in manifest["postgres"]:
        entries.append(manifest["postgres"]["inventory"])
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
        raise BackupError("backup target must be outside and non-overlapping with the repository")
    _reject_symlink_components(root)
    if new and root.exists():
        raise BackupError("backup target must be a new directory")
    if not new and (root.is_symlink() or not root.is_dir()):
        raise BackupError("backup target must be an existing regular directory")
    return repo, root


def plan_backup(args: argparse.Namespace) -> dict[str, Any]:
    repo, root = _load_root(args.repo_root, args.backup_target, new=True)
    migration = _repository_migration_facts(repo)
    plan = {
        "execution_authorized": False,
        "layout": {
            "minio_root": MINIO_ROOT,
            "postgres_dump": POSTGRES_DUMP,
            "postgres_inventory": POSTGRES_INVENTORY,
            "release_receipt": RELEASE_RECEIPT,
            "runtime_config": RUNTIME_CONFIG,
            "runtime_readiness_root": READINESS_ROOT,
            "runtime_state_root": RUNTIME_STATE_ROOT,
        },
        **_migration_binding(migration),
        "redis": {"archived": False, "authoritative": False, "rebuild_required": True},
        "schema": SCHEMA_PLAN,
        "schema_version": 2,
        "source_database": _identifier(args.source_database, where="source database"),
    }
    _validate_plan(plan)
    root.mkdir(parents=True)
    for directory in (
        "release",
        "postgres",
        MINIO_ROOT,
        "personal-runtime",
        RUNTIME_STATE_ROOT,
        READINESS_ROOT,
    ):
        root.joinpath(*PurePosixPath(directory).parts).mkdir(parents=True, exist_ok=True)
    (root / PLAN_NAME).write_bytes(_canonical(plan))
    return plan


def _load_plan(root: Path) -> tuple[dict[str, Any], bytes]:
    plan, raw = _load_canonical(root / PLAN_NAME)
    _validate_plan(plan)
    return plan, raw


def _postgres_inventory_for_seal(
    args: argparse.Namespace,
    *,
    root: Path,
    plan: dict[str, Any],
    dump_entry: dict[str, object],
) -> tuple[dict[str, object], bytes]:
    raw_path = getattr(args, "postgres_inventory", None)
    if not isinstance(raw_path, str) or not raw_path:
        raise BackupError("seal-assets requires --postgres-inventory")
    inventory_path = _absolute(raw_path, where="PostgreSQL backup inventory", must_exist=True)
    _, inventory_raw = _load_postgres_inventory(
        inventory_path,
        capture_mode="source_backup",
        expected_database=str(plan["source_database"]),
        expected_dump_sha256=str(dump_entry["sha256"]),
        expected_head=str(plan["source_migration_head"]),
        memory_present=True,
    )
    staged = root.joinpath(*PurePosixPath(POSTGRES_INVENTORY).parts)
    _reject_symlink_components(staged, stop=root)
    if staged.exists():
        raise BackupError("PostgreSQL inventory target must be absent before sealing")
    entry = {
        "path": POSTGRES_INVENTORY,
        "sha256": _sha256_bytes(inventory_raw),
        "size": len(inventory_raw),
    }
    return entry, inventory_raw


def seal_assets(args: argparse.Namespace) -> dict[str, Any]:
    repo, root = _load_root(args.repo_root, args.backup_target, new=False)
    if (root / MANIFEST_NAME).exists():
        raise BackupError("refusing to overwrite an existing backup manifest")
    plan, plan_raw = _load_plan(root)
    current_migration = _migration_binding(_repository_migration_facts(repo))
    if current_migration != _migration_binding(plan):
        raise BackupError("repository migration bytes drifted after backup planning")
    if (root / "redis").exists():
        raise BackupError("Redis artifacts must not be archived")
    dump_entry = _entry(root, POSTGRES_DUMP)
    postgres_inventory, postgres_inventory_raw = _postgres_inventory_for_seal(
        args, root=root, plan=plan, dump_entry=dump_entry
    )
    manifest = {
        "backup_plan_sha256": _sha256_bytes(plan_raw),
        "execution_authorized": False,
        **_migration_binding(plan),
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
        "postgres": {
            "artifact": dump_entry,
            "format": "custom",
            "inventory": postgres_inventory,
        },
        "redis": {"archived": False, "authoritative": False, "rebuild_required": True},
        "release_receipt": _entry(root, RELEASE_RECEIPT),
        "restore_policy": {
            "database_prefix": "omnibase_restore_",
            "minio_new_root_only": True,
            "new_database_only": True,
        },
        "schema": SCHEMA_MANIFEST,
        "schema_version": 2,
        "source_database": plan["source_database"],
    }
    _validate_manifest_shape(manifest)
    inventory_target = root.joinpath(*PurePosixPath(POSTGRES_INVENTORY).parts)
    inventory_target.write_bytes(postgres_inventory_raw)
    (root / MANIFEST_NAME).write_bytes(_canonical(manifest))
    _verify_assets(root, manifest)
    return manifest


def _is_legacy_manifest(manifest: dict[str, Any]) -> bool:
    return manifest.get("schema") == SCHEMA_MANIFEST_V1 and manifest.get("schema_version") == 1


def _verify_embedded_postgres_inventory(root: Path, manifest: dict[str, Any]) -> None:
    if _is_legacy_manifest(manifest):
        return
    inventory_path = _contained(root, POSTGRES_INVENTORY)
    _load_postgres_inventory(
        inventory_path,
        capture_mode="source_backup",
        expected_database=str(manifest["source_database"]),
        expected_dump_sha256=str(manifest["postgres"]["artifact"]["sha256"]),
        expected_head=str(manifest["source_migration_head"]),
        memory_present=True,
    )


def _verify_backup_loaded(repo: Path, root: Path) -> tuple[dict[str, Any], dict[str, Any], bytes]:
    plan, plan_raw = _load_plan(root)
    manifest, manifest_raw = _load_canonical(root / MANIFEST_NAME)
    _validate_manifest_shape(manifest)
    legacy = _is_legacy_manifest(manifest)
    if legacy != (plan.get("schema") == SCHEMA_PLAN_V1):
        raise BackupError("backup plan and manifest schema versions differ")
    if (
        manifest["backup_plan_sha256"] != _sha256_bytes(plan_raw)
        or manifest["source_database"] != plan["source_database"]
    ):
        raise BackupError("backup manifest is not bound to the selected plan")
    if not legacy:
        if _migration_binding(manifest) != _migration_binding(plan):
            raise BackupError("backup manifest is not bound to the selected plan")
        repository_binding = _migration_binding(
            _repository_migration_facts(repo, through_head=str(manifest["source_migration_head"]))
        )
        if repository_binding != _migration_binding(manifest):
            raise BackupError("backup migration binding drifted from repository bytes")
    _verify_embedded_postgres_inventory(root, manifest)
    _verify_assets(root, manifest)
    source_head = _LEGACY_V1_SOURCE_HEAD if legacy else str(manifest["source_migration_head"])
    verification = {
        "backup_manifest_sha256": _sha256_bytes(manifest_raw),
        "backup_verified": True,
        "execution_authorized": False,
        "legacy_v1": legacy,
        "postgres_inventory_verified": not legacy,
        "redis_archived": False,
        "schema": str(manifest["schema"]),
        "source_migration_head": source_head,
    }
    if not legacy:
        verification.update(_migration_binding(manifest))
    return verification, manifest, manifest_raw


def verify_backup(args: argparse.Namespace) -> dict[str, Any]:
    repo, root = _load_root(args.repo_root, args.backup_target, new=False)
    verification, _, _ = _verify_backup_loaded(repo, root)
    return verification


def _compatibility_matrix(repo: Path) -> tuple[dict[str, Any], str]:
    source = _repository_migration_facts(repo, through_head="0012")
    target = _repository_migration_facts(repo, through_head="0013")
    matrix = {
        "entries": [
            {
                "entry_id": "p5-memory-0012-to-0013",
                "migration_0013_schema_sha256": target["migration_0013_schema_sha256"],
                "required_commands": [
                    "restore_dump_into_new_database",
                    "capture_restore_new_postgresql_inventory",
                    "verify_global_and_tenant_heads_at_0012",
                    "upgrade_global_then_each_tenant_to_0013",
                    "verify_0013_memory_tables_and_vector_lanes",
                ],
                "required_evidence": [
                    "postgres_dump_sha256",
                    "restore_new_inventory_sha256",
                    "global_alembic_head",
                    "tenant_alembic_heads",
                    "memory_table_names",
                    "memory_vector_inventory",
                ],
                "source_head": "0012",
                "source_revision_list_sha256": source["migration_revision_list_sha256"],
                "target_head": "0013",
                "target_memory_table_names": target["memory_table_names"],
                "target_memory_vector_inventory": target["memory_vector_inventory"],
                "target_revision_list_sha256": target["migration_revision_list_sha256"],
            }
        ],
        "schema": "omnibase.p5-personal-restore-compatibility-matrix.v1",
        "schema_version": 1,
    }
    return matrix, _sha256_bytes(_canonical(matrix))


def _select_compatibility_entry(
    repo: Path, args: argparse.Namespace, *, source_head: str, target_head: str
) -> tuple[dict[str, Any], str]:
    matrix, matrix_sha256 = _compatibility_matrix(repo)
    requested = getattr(args, "compatibility_entry", None)
    matches = [
        entry
        for entry in matrix["entries"]
        if entry["source_head"] == source_head and entry["target_head"] == target_head
    ]
    if len(matches) != 1 or requested != matches[0]["entry_id"]:
        raise BackupError(
            f"restore migration {source_head}->{target_head} requires a canonical compatibility entry"
        )
    return matches[0], matrix_sha256


def _legacy_restore_inventory(
    args: argparse.Namespace, manifest: dict[str, Any]
) -> tuple[dict[str, Any], bytes]:
    raw_path = getattr(args, "postgres_inventory", None)
    if not isinstance(raw_path, str) or not raw_path:
        raise BackupError("legacy v1 restore requires restore-new --postgres-inventory evidence")
    path = _absolute(raw_path, where="legacy restore-new PostgreSQL inventory", must_exist=True)
    return _load_postgres_inventory(
        path,
        capture_mode="restore_new_evidence",
        expected_database=None,
        expected_dump_sha256=str(manifest["postgres"]["artifact"]["sha256"]),
        expected_head=_LEGACY_V1_SOURCE_HEAD,
        memory_present=False,
    )


def _restore_migration_selection(
    repo: Path,
    args: argparse.Namespace,
    verification: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    legacy = bool(verification["legacy_v1"])
    source_head = str(verification["source_migration_head"])
    target_head = str(_repository_migration_facts(repo)["repository_migration_head"])
    compatibility_entry: dict[str, Any] | None = None
    compatibility_matrix_sha256: str | None = None
    if source_head == target_head:
        if getattr(args, "compatibility_entry", None) is not None:
            raise BackupError("compatibility entry is forbidden at the same revision")
        migration_mode = "same_revision"
    else:
        compatibility_entry, compatibility_matrix_sha256 = _select_compatibility_entry(
            repo, args, source_head=source_head, target_head=target_head
        )
        migration_mode = "canonical_compatibility_upgrade"
    legacy_inventory_sha256: str | None = None
    if legacy:
        _, legacy_inventory_raw = _legacy_restore_inventory(args, manifest)
        legacy_inventory_sha256 = _sha256_bytes(legacy_inventory_raw)
    source_binding = (
        _repository_migration_facts(repo, through_head=source_head) if legacy else manifest
    )
    return {
        "compatibility_entry": compatibility_entry,
        "compatibility_matrix_sha256": compatibility_matrix_sha256,
        "legacy": legacy,
        "legacy_inventory_sha256": legacy_inventory_sha256,
        "migration_mode": migration_mode,
        "source_binding": source_binding,
        "source_head": source_head,
        "target_head": target_head,
    }


def plan_restore(args: argparse.Namespace) -> dict[str, Any]:
    repo, root = _load_root(args.repo_root, args.backup_target, new=False)
    verification, manifest, manifest_raw = _verify_backup_loaded(repo, root)
    migration = _restore_migration_selection(repo, args, verification, manifest)
    target = _identifier(args.target_database, where="target database")
    if not target.startswith("omnibase_restore_") or target == manifest["source_database"]:
        raise BackupError("restore target must be a new omnibase_restore_* database")
    inventory_path = _absolute(args.database_inventory, where="database inventory", must_exist=True)
    inventory, inventory_raw = _load_canonical(inventory_path)
    _exact(inventory, {"databases", "schema", "schema_version"}, where="database inventory")
    if inventory["schema"] != SCHEMA_DATABASE_INVENTORY or inventory["schema_version"] != 1:
        raise BackupError("unsupported database inventory")
    databases = inventory["databases"]
    if (
        not isinstance(databases, list)
        or not databases
        or any(not isinstance(item, str) for item in databases)
    ):
        raise BackupError("database inventory must contain a non-empty string list")
    normalized = [_identifier(item, where="database inventory item") for item in databases]
    if normalized != sorted(set(normalized)):
        raise BackupError("database inventory must be sorted and unique")
    if target in normalized:
        raise BackupError("restore target database already exists")
    minio_root = _absolute(args.minio_restore_root, where="MinIO restore root")
    if minio_root.exists():
        raise BackupError("MinIO restore root must be a new path")
    if _overlaps(minio_root, repo) or _overlaps(minio_root, root):
        raise BackupError("MinIO restore root must be outside the repository and backup")
    _reject_symlink_components(minio_root)
    source_binding = migration["source_binding"]
    compatibility_entry = migration["compatibility_entry"]
    restore_plan = {
        "backup_manifest_sha256": _sha256_bytes(manifest_raw),
        "backup_verified": verification["backup_verified"],
        "compatibility_entry": compatibility_entry,
        "compatibility_matrix_sha256": migration["compatibility_matrix_sha256"],
        "database_inventory_sha256": _sha256_bytes(inventory_raw),
        "execution_authorized": False,
        "legacy_restore_new_inventory_sha256": migration["legacy_inventory_sha256"],
        "legacy_v1": migration["legacy"],
        "memory_table_names": source_binding["memory_table_names"],
        "memory_vector_inventory": source_binding["memory_vector_inventory"],
        "memory_vector_lane_versions": source_binding["memory_vector_lane_versions"],
        "migration_0013_schema_sha256": (
            compatibility_entry["migration_0013_schema_sha256"]
            if compatibility_entry is not None
            else source_binding["migration_0013_schema_sha256"]
        ),
        "migration_mode": migration["migration_mode"],
        "migration_revision_list_sha256": source_binding["migration_revision_list_sha256"],
        "minio_restore_root": str(minio_root),
        "personal_runtime_assets_verified": True,
        "redis": {"archived": False, "authoritative": False, "rebuild_required": True},
        "schema": SCHEMA_RESTORE_PLAN,
        "schema_version": 2,
        "source_database": manifest["source_database"],
        "source_migration_head": migration["source_head"],
        "target_database": target,
        "target_migration_head": migration["target_head"],
    }
    return restore_plan


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    capture = commands.add_parser("capture-postgres-inventory")
    capture.add_argument("--repo-root", required=True)
    capture.add_argument("--postgres-dump", required=True)
    capture.add_argument("--output", required=True)
    capture.add_argument("--source-database", required=True)
    capture.add_argument(
        "--capture-mode",
        choices=("source_backup", "restore_new_evidence"),
        default="source_backup",
    )
    for name in ("plan-backup", "seal-assets", "verify-backup", "plan-restore"):
        command = commands.add_parser(name)
        command.add_argument("--repo-root", required=True)
        command.add_argument("--backup-target", required=True)
        if name == "plan-backup":
            command.add_argument("--source-database", required=True)
        if name == "seal-assets":
            command.add_argument("--postgres-inventory", required=True)
        if name == "plan-restore":
            command.add_argument("--target-database", required=True)
            command.add_argument("--database-inventory", required=True)
            command.add_argument("--minio-restore-root", required=True)
            command.add_argument("--compatibility-entry")
            command.add_argument("--postgres-inventory")
    return parser.parse_args(argv)


def _run(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "capture-postgres-inventory": capture_postgres_inventory,
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
