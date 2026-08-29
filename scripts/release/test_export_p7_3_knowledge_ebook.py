from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
from pathlib import Path

import pytest


def _exporter(repo: Path):
    path = repo / "scripts/release/export_p7_3_knowledge_ebook.py"
    spec = importlib.util.spec_from_file_location("export_p7_3_knowledge_ebook", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bundle_exporter(repo: Path):
    path = repo / "scripts/release/export_p7_3_component_bundles.py"
    spec = importlib.util.spec_from_file_location("export_p7_3_component_bundles", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "ebook"
    data = root / "data"
    data.mkdir(parents=True)
    connection = sqlite3.connect(data / "ebook.db")
    connection.executescript(
        """
        CREATE TABLE documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
            source_path TEXT, doc_type TEXT DEFAULT 'markdown', content TEXT,
            plain_summary TEXT, imported_at TEXT, file_hash TEXT
        );
        CREATE TABLE sections (
            id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id INTEGER, level INTEGER,
            heading TEXT, content TEXT, plain_explanation TEXT, theme_tag TEXT,
            position INTEGER, FOREIGN KEY (doc_id) REFERENCES documents(id)
        );
        CREATE TABLE invariants (
            id INTEGER PRIMARY KEY AUTOINCREMENT, inv_id TEXT UNIQUE, title TEXT,
            content TEXT, plain_explanation TEXT, severity TEXT DEFAULT 'high',
            related_modules TEXT, related_source TEXT, phase TEXT
        );
        CREATE TABLE modules (
            id INTEGER PRIMARY KEY AUTOINCREMENT, module_key TEXT UNIQUE,
            name TEXT, description TEXT, source_paths TEXT, dependencies TEXT,
            invariants TEXT, verification TEXT, plain_summary TEXT
        );
        CREATE TABLE glossary (
            id INTEGER PRIMARY KEY AUTOINCREMENT, term TEXT UNIQUE,
            plain_explanation TEXT, technical_def TEXT, category TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO documents "
        "(title, source_path, doc_type, content, plain_summary, imported_at, file_hash) "
        "VALUES (?, ?, 'markdown', ?, ?, '2026-08-30', 'a' || printf('%063d', 0))",
        (
            "Architecture",
            r"E:\private\handover.md",
            "Use E:\\private\\repo safely",
            "Summary",
        ),
    )
    connection.execute(
        "INSERT INTO sections "
        "(doc_id, level, heading, content, plain_explanation, theme_tag, position) "
        "VALUES (1, 2, 'Boundary', 'No ambient authority', 'Owner review', 'security', 1)"
    )
    connection.execute(
        "INSERT INTO invariants "
        "(inv_id, title, content, plain_explanation, severity, related_modules, phase) "
        "VALUES ('INV-TEST', 'Test', 'Bounded', 'Bounded', 'high', '[\"desktop\"]', 'P7')"
    )
    connection.execute(
        "INSERT INTO modules "
        "(module_key, name, description, source_paths, dependencies, invariants, "
        "verification, plain_summary) VALUES "
        "('desktop', 'Desktop', 'Host', '[\"E:/private/repo\"]', '[]', "
        "'[\"INV-TEST\"]', '[\"pytest\"]', 'Summary')"
    )
    connection.execute(
        "INSERT INTO glossary (term, plain_explanation, technical_def, category) "
        "VALUES ('fencing', 'Reject stale work', 'monotonic token', 'security')"
    )
    connection.commit()
    connection.close()
    return root


def test_export_is_canonical_bounded_and_omits_physical_source_fields(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    exporter = _exporter(repo)
    source = _source(tmp_path)

    first = tmp_path / "components-a"
    second = tmp_path / "components-b"
    first_result = exporter.export_knowledge_ebook(source_root=source, output_dir=first)
    second_result = exporter.export_knowledge_ebook(
        source_root=source, output_dir=second
    )

    assert first_result == second_result
    assert (first / "knowledge-ebook/catalog.json").read_bytes() == (
        second / "knowledge-ebook/catalog.json"
    ).read_bytes()
    catalog_raw = (first / "knowledge-ebook/catalog.json").read_bytes()
    assert catalog_raw.endswith(b"\n")
    catalog = json.loads(catalog_raw)
    assert catalog["component_id"] == "knowledge.ebook"
    assert catalog["documents"][0]["content"] == "Use <redacted-local-path>"
    assert "source_path" not in json.dumps(catalog)
    assert "source_paths" not in json.dumps(catalog)
    manifest = json.loads((first / "knowledge-ebook/manifest.json").read_bytes())
    assert manifest["package_sha256"] == first_result["catalog_sha256"]
    assert manifest["entrypoint"] == {
        "adapter_id": "trusted-local-app.v1",
        "kind": "native_catalog_v1",
    }


def test_complete_bundle_export_pins_two_versions_of_all_five_families(
    tmp_path: Path,
) -> None:
    repo = Path(__file__).resolve().parents[2]
    exporter = _bundle_exporter(repo)
    source = _source(tmp_path)
    first = tmp_path / "bundle-a"
    second = tmp_path / "bundle-b"

    first_result = exporter.export_component_bundles(
        repo_root=repo, ebook_root=source, output_dir=first
    )
    second_result = exporter.export_component_bundles(
        repo_root=repo, ebook_root=source, output_dir=second
    )

    assert first_result == second_result
    assert first_result["package_count"] == 10
    assert (first / "index.json").read_bytes() == (second / "index.json").read_bytes()
    index = json.loads((first / "index.json").read_bytes())
    assert index["schema_version"] == 1
    assert len(index["packages"]) == 10
    assert {item["family"] for item in index["packages"]} == {
        "declarative_ui",
        "instruction_skill",
        "mcp_connector",
        "sandbox_workload",
        "trusted_local_adapter",
    }
    for item in index["packages"]:
        manifest = first / item["manifest_path"]
        package = first / item["package_path"]
        inventory = first / item["inventory_path"]
        assert exporter._sha256(manifest.read_bytes()) == item["manifest_sha256"]
        assert exporter._sha256(package.read_bytes()) == item["package_sha256"]
        assert exporter._sha256(inventory.read_bytes()) == item["inventory_sha256"]
        inventory_value = json.loads(inventory.read_bytes())
        for file in inventory_value["files"]:
            payload = manifest.parent / file["path"]
            assert payload.stat().st_size == file["size"]
            assert exporter._sha256(payload.read_bytes()) == file["sha256"]
    ebook_versions = {
        item["version"]
        for item in index["packages"]
        if item["component_id"] == "knowledge.ebook"
    }
    assert ebook_versions == {"1.0.0", "1.1.0"}


def test_export_rejects_schema_drift_and_uncheckpointed_wal(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    exporter = _exporter(repo)
    source = _source(tmp_path)
    database = source / "data/ebook.db"

    connection = sqlite3.connect(database)
    connection.execute("ALTER TABLE glossary ADD COLUMN unexpected TEXT")
    connection.commit()
    connection.close()
    with pytest.raises(exporter.EbookExportError, match="schema_incompatible"):
        exporter.export_knowledge_ebook(source_root=source, output_dir=tmp_path / "bad")

    source = _source(tmp_path / "wal-case")
    (source / "data/ebook.db-wal").write_bytes(b"pending")
    with pytest.raises(exporter.EbookExportError, match="wal_not_checkpointed"):
        exporter.export_knowledge_ebook(
            source_root=source, output_dir=tmp_path / "wal-out"
        )

    source = _source(tmp_path / "journal-case")
    (source / "data/ebook.db-journal").write_bytes(b"pending")
    with pytest.raises(exporter.EbookExportError, match="journal_not_checkpointed"):
        exporter.export_knowledge_ebook(
            source_root=source, output_dir=tmp_path / "journal-out"
        )


def _symlink_or_skip(link: Path, target: Path, *, directory: bool) -> None:
    try:
        os.symlink(target, link, target_is_directory=directory)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")


def test_export_rejects_data_directory_and_database_link_escape(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[2]
    exporter = _exporter(repo)

    source = _source(tmp_path / "data-link")
    external_data = tmp_path / "external-data"
    (source / "data").rename(external_data)
    _symlink_or_skip(source / "data", external_data, directory=True)
    with pytest.raises(exporter.EbookExportError, match="root_identity_invalid"):
        exporter.export_knowledge_ebook(
            source_root=source, output_dir=tmp_path / "data-link-out"
        )

    source = _source(tmp_path / "database-link")
    database = source / "data/ebook.db"
    external_database = tmp_path / "external-ebook.db"
    database.rename(external_database)
    _symlink_or_skip(database, external_database, directory=False)
    with pytest.raises(exporter.EbookExportError, match="source_identity_invalid"):
        exporter.export_knowledge_ebook(
            source_root=source, output_dir=tmp_path / "database-link-out"
        )


def test_export_rechecks_wal_after_the_initial_identity_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = Path(__file__).resolve().parents[2]
    exporter = _exporter(repo)
    source = _source(tmp_path)
    database = source / "data/ebook.db"
    original = exporter._assert_no_pending_journal
    calls = 0

    def inject_wal_after_first_check(path: Path) -> None:
        nonlocal calls
        calls += 1
        original(path)
        if calls == 1:
            database.with_name("ebook.db-wal").write_bytes(b"pending")

    monkeypatch.setattr(
        exporter, "_assert_no_pending_journal", inject_wal_after_first_check
    )
    with pytest.raises(exporter.EbookExportError, match="wal_not_checkpointed"):
        exporter.export_knowledge_ebook(
            source_root=source, output_dir=tmp_path / "race-out"
        )
