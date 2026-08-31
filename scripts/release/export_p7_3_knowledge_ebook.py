"""Export the auxiliary OmniBase ebook as a bounded immutable component bundle.

The auxiliary project is not executed or embedded. Its SQLite store is opened
read-only and projected into canonical JSON with physical source paths removed.
The resulting directory is an optional offline input to the desktop payload
builder and contains no database, launcher, server or wildcard message bridge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

SCHEMA_VERSION = 1
COMPONENT_ID = "knowledge.ebook"
COMPONENT_VERSION = "1.0.0"
COMPONENT_VERSIONS = frozenset({"1.0.0", "1.1.0"})
MAX_DATABASE_BYTES = 64 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_TEXT_BYTES = 512 * 1024
MAX_DOCUMENTS = 1_024
MAX_SECTIONS = 20_000
MAX_ROWS_PER_COLLECTION = 10_000
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_WINDOWS_PATH = re.compile(r"(?i)(?<![A-Za-z0-9_])[A-Z]:\\[^\r\n\t\"'<>|?*]+")
_UNC_PATH = re.compile(r"\\\\[^\\\s]+\\[^\r\n\t\"'<>|?*]+")
_SECRET = re.compile(r"(?i)\b(?:sk|key|token)-[A-Za-z0-9_-]{16,}\b")
_LOGICAL_TAG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")

_TABLE_COLUMNS = {
    "documents": (
        "id",
        "title",
        "source_path",
        "doc_type",
        "content",
        "plain_summary",
        "imported_at",
        "file_hash",
    ),
    "sections": (
        "id",
        "doc_id",
        "level",
        "heading",
        "content",
        "plain_explanation",
        "theme_tag",
        "position",
    ),
    "invariants": (
        "id",
        "inv_id",
        "title",
        "content",
        "plain_explanation",
        "severity",
        "related_modules",
        "related_source",
        "phase",
    ),
    "modules": (
        "id",
        "module_key",
        "name",
        "description",
        "source_paths",
        "dependencies",
        "invariants",
        "verification",
        "plain_summary",
    ),
    "glossary": (
        "id",
        "term",
        "plain_explanation",
        "technical_def",
        "category",
    ),
}


class EbookExportError(ValueError):
    """A deterministic path-redacted export failure."""


class FileIdentity(NamedTuple):
    device: int
    inode: int
    links: int
    size: int
    modified_ns: int


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)


def _identity(path: Path) -> FileIdentity:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise EbookExportError("ebook_export_source_unavailable") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or metadata.st_nlink != 1
        or metadata.st_size <= 0
        or metadata.st_size > MAX_DATABASE_BYTES
    ):
        raise EbookExportError("ebook_export_source_identity_invalid")
    return FileIdentity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _safe_database(data_root: Path) -> tuple[Path, FileIdentity]:
    data = _safe_root(data_root)
    database = (data / "ebook.db").absolute()
    try:
        resolved = database.resolve(strict=True)
    except OSError as exc:
        raise EbookExportError("ebook_export_source_unavailable") from exc
    if database.parent != data or os.path.normcase(str(database)) != os.path.normcase(
        str(resolved)
    ):
        raise EbookExportError("ebook_export_source_identity_invalid")
    return database, _identity(database)


def _assert_no_pending_journal(database: Path) -> None:
    for suffix in ("-wal", "-journal"):
        journal = database.with_name(f"{database.name}{suffix}")
        try:
            metadata = os.stat(journal, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise EbookExportError("ebook_export_journal_unavailable") from exc
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(metadata.st_mode)
            or _is_reparse(metadata)
            or metadata.st_nlink != 1
            or metadata.st_size != 0
        ):
            code = (
                "ebook_export_wal_not_checkpointed"
                if suffix == "-wal"
                else "ebook_export_journal_not_checkpointed"
            )
            raise EbookExportError(code)


def _read_bound_database(database: Path, expected: FileIdentity) -> bytes:
    try:
        with database.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            opened_identity = FileIdentity(*_identity_fields(opened))
            if opened_identity != expected or not stat.S_ISREG(opened.st_mode):
                raise EbookExportError("ebook_export_source_changed")
            raw = handle.read(MAX_DATABASE_BYTES + 1)
            closed_identity = FileIdentity(*_identity_fields(os.fstat(handle.fileno())))
            if len(raw) != expected.size or closed_identity != opened_identity:
                raise EbookExportError("ebook_export_source_changed")
    except EbookExportError:
        raise
    except OSError as exc:
        raise EbookExportError("ebook_export_source_unavailable") from exc
    return raw


def _identity_fields(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
    )


def _safe_root(path: Path) -> Path:
    absolute = path.absolute()
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise EbookExportError("ebook_export_root_unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or os.path.normcase(str(absolute)) != os.path.normcase(str(resolved))
    ):
        raise EbookExportError("ebook_export_root_identity_invalid")
    return absolute


def _sanitize_text(value: object, *, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()) or "\x00" in value:
        raise EbookExportError("ebook_export_text_invalid")
    sanitized = _SECRET.sub("<redacted-secret>", _UNC_PATH.sub("<redacted-local-path>", value))
    sanitized = _WINDOWS_PATH.sub("<redacted-local-path>", sanitized)
    if len(sanitized.encode("utf-8")) > MAX_TEXT_BYTES:
        raise EbookExportError("ebook_export_text_too_large")
    return sanitized


def _bounded_rows(rows: Iterable[sqlite3.Row], *, limit: int) -> list[sqlite3.Row]:
    values = list(rows)
    if len(values) > limit:
        raise EbookExportError("ebook_export_row_budget_exceeded")
    return values


def _verify_schema(connection: sqlite3.Connection) -> None:
    for table, expected in _TABLE_COLUMNS.items():
        rows = connection.execute(f"PRAGMA table_info({table})").fetchall()
        actual = tuple(str(row[1]) for row in rows)
        if actual != expected:
            raise EbookExportError("ebook_export_schema_incompatible")


def _string_list(value: object, *, allow_comma_delimited_tags: bool = False) -> list[str]:
    if value is None or value == "":
        return []
    if not isinstance(value, str):
        raise EbookExportError("ebook_export_list_invalid")
    try:
        parsed = json.loads(value)
    except ValueError as exc:
        if not allow_comma_delimited_tags:
            raise EbookExportError("ebook_export_list_invalid") from exc
        parsed = [item.strip() for item in value.split(",")]
        if not parsed or any(_LOGICAL_TAG.fullmatch(item) is None for item in parsed):
            raise EbookExportError("ebook_export_list_invalid") from exc
    if (
        not isinstance(parsed, list)
        or len(parsed) > 256
        or any(not isinstance(item, str) for item in parsed)
    ):
        raise EbookExportError("ebook_export_list_invalid")
    return [str(_sanitize_text(item, required=True)) for item in parsed]


def _catalog(
    connection: sqlite3.Connection, source_sha256: str, component_version: str
) -> dict[str, object]:
    documents = _bounded_rows(
        connection.execute(
            "SELECT id, title, doc_type, content, plain_summary, file_hash "
            "FROM documents ORDER BY id"
        ),
        limit=MAX_DOCUMENTS,
    )
    sections = _bounded_rows(
        connection.execute(
            "SELECT id, doc_id, level, heading, content, plain_explanation, "
            "theme_tag, position FROM sections ORDER BY doc_id, position, id"
        ),
        limit=MAX_SECTIONS,
    )
    sections_by_document: dict[int, list[dict[str, object]]] = {}
    document_ids = {int(row["id"]) for row in documents}
    for row in sections:
        document_id = int(row["doc_id"])
        if document_id not in document_ids:
            raise EbookExportError("ebook_export_section_orphaned")
        sections_by_document.setdefault(document_id, []).append(
            {
                "content": _sanitize_text(row["content"]),
                "explanation": _sanitize_text(row["plain_explanation"]),
                "heading": _sanitize_text(row["heading"]),
                "id": f"section:{int(row['id'])}",
                "level": int(row["level"] or 0),
                "position": int(row["position"] or 0),
                "theme": _sanitize_text(row["theme_tag"]),
            }
        )

    invariant_rows = _bounded_rows(
        connection.execute(
            "SELECT inv_id, title, content, plain_explanation, severity, "
            "related_modules, phase FROM invariants ORDER BY inv_id"
        ),
        limit=MAX_ROWS_PER_COLLECTION,
    )
    module_rows = _bounded_rows(
        connection.execute(
            "SELECT module_key, name, description, dependencies, invariants, "
            "verification, plain_summary FROM modules ORDER BY module_key"
        ),
        limit=MAX_ROWS_PER_COLLECTION,
    )
    glossary_rows = _bounded_rows(
        connection.execute(
            "SELECT term, plain_explanation, technical_def, category " "FROM glossary ORDER BY term"
        ),
        limit=MAX_ROWS_PER_COLLECTION,
    )
    return {
        "component_id": COMPONENT_ID,
        "component_version": component_version,
        "documents": [
            {
                "content": _sanitize_text(row["content"]),
                "file_hash": _sanitize_text(row["file_hash"]),
                "id": f"document:{int(row['id'])}",
                "sections": sections_by_document.get(int(row["id"]), []),
                "summary": _sanitize_text(row["plain_summary"]),
                "title": _sanitize_text(row["title"], required=True),
                "type": _sanitize_text(row["doc_type"]),
            }
            for row in documents
        ],
        "glossary": [
            {
                "category": _sanitize_text(row["category"]),
                "definition": _sanitize_text(row["technical_def"]),
                "explanation": _sanitize_text(row["plain_explanation"]),
                "term": _sanitize_text(row["term"], required=True),
            }
            for row in glossary_rows
        ],
        "invariants": [
            {
                "content": _sanitize_text(row["content"]),
                "explanation": _sanitize_text(row["plain_explanation"]),
                "id": _sanitize_text(row["inv_id"], required=True),
                "modules": _string_list(row["related_modules"], allow_comma_delimited_tags=True),
                "phase": _sanitize_text(row["phase"]),
                "severity": _sanitize_text(row["severity"]),
                "title": _sanitize_text(row["title"], required=True),
            }
            for row in invariant_rows
        ],
        "modules": [
            {
                "dependencies": _string_list(row["dependencies"]),
                "description": _sanitize_text(row["description"]),
                "id": _sanitize_text(row["module_key"], required=True),
                "invariants": _string_list(row["invariants"]),
                "name": _sanitize_text(row["name"], required=True),
                "summary": _sanitize_text(row["plain_summary"]),
                "verification": _string_list(row["verification"]),
            }
            for row in module_rows
        ],
        "schema_version": SCHEMA_VERSION,
        "source_snapshot_sha256": source_sha256,
    }


def export_knowledge_ebook(
    *,
    source_root: Path,
    output_dir: Path,
    component_version: str = COMPONENT_VERSION,
) -> dict[str, object]:
    if component_version not in COMPONENT_VERSIONS:
        raise EbookExportError("ebook_export_version_invalid")
    root = _safe_root(source_root)
    database, before = _safe_database(root / "data")
    _assert_no_pending_journal(database)
    source_raw = _read_bound_database(database, before)
    if _identity(database) != before:
        raise EbookExportError("ebook_export_source_changed")
    _assert_no_pending_journal(database)
    source_sha256 = _sha256(source_raw)

    with tempfile.TemporaryDirectory(prefix="omnibase-ebook-export-") as temporary:
        snapshot = Path(temporary) / "ebook.db"
        try:
            snapshot.write_bytes(source_raw)
        except OSError as exc:
            raise EbookExportError("ebook_export_snapshot_failed") from exc
        uri = f"file:{snapshot.as_posix()}?mode=ro&immutable=1"
        try:
            connection = sqlite3.connect(uri, uri=True)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            _verify_schema(connection)
            catalog = _catalog(connection, source_sha256, component_version)
        except sqlite3.Error as exc:
            raise EbookExportError("ebook_export_database_invalid") from exc
        finally:
            if "connection" in locals():
                connection.close()
    if _identity(database) != before:
        raise EbookExportError("ebook_export_source_changed")
    _assert_no_pending_journal(database)

    catalog_raw = _canonical_json(catalog)
    if len(catalog_raw) > MAX_OUTPUT_BYTES:
        raise EbookExportError("ebook_export_output_too_large")
    catalog_sha256 = _sha256(catalog_raw)
    inventory_raw = _canonical_json(
        [{"path": "catalog.json", "sha256": catalog_sha256, "size": len(catalog_raw)}]
    )
    manifest = {
        "component_id": COMPONENT_ID,
        "entrypoint": {
            "adapter_id": "trusted-local-app.v1",
            "kind": "native_catalog_v1",
        },
        "family": "trusted_local_adapter",
        "inventory_sha256": _sha256(inventory_raw),
        "package_sha256": catalog_sha256,
        "publisher": "source_owned",
        "schema_version": SCHEMA_VERSION,
        "version": component_version,
    }
    manifest_raw = _canonical_json(manifest)

    output = output_dir.absolute()
    if output.exists():
        raise EbookExportError("ebook_export_output_exists")
    parent = _safe_root(output.parent)
    staging = parent / f".{output.name}.staging-{os.getpid()}-{uuid.uuid4().hex}"
    component = staging / "knowledge-ebook"
    try:
        component.mkdir(parents=True)
        (component / "catalog.json").write_bytes(catalog_raw)
        (component / "manifest.json").write_bytes(manifest_raw)
        staging.rename(output)
    except OSError as exc:
        raise EbookExportError("ebook_export_publish_failed") from exc
    return {
        "catalog_sha256": catalog_sha256,
        "component_id": COMPONENT_ID,
        "component_version": component_version,
        "manifest_sha256": _sha256(manifest_raw),
        "output_bytes": len(catalog_raw) + len(manifest_raw),
        "source_snapshot_sha256": source_sha256,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--component-version", default=COMPONENT_VERSION)
    args = parser.parse_args()
    try:
        result = export_knowledge_ebook(**vars(args))
    except EbookExportError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
