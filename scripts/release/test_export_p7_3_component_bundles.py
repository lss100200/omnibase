from __future__ import annotations

import importlib.util
import json
import shutil
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest


def _exporter(repo: Path):
    path = repo / "scripts/release/export_p7_3_component_bundles.py"
    spec = importlib.util.spec_from_file_location(
        "export_p7_3_component_bundles_test", path
    )
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
        INSERT INTO documents
            (title, source_path, doc_type, content, plain_summary, imported_at, file_hash)
        VALUES
            ('Architecture', 'E:\\private\\handover.md', 'markdown', 'Bounded content',
             'Summary', '2026-08-30',
             'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa');
        INSERT INTO sections
            (doc_id, level, heading, content, plain_explanation, theme_tag, position)
        VALUES (1, 2, 'Boundary', 'No ambient authority', 'Owner review', 'security', 1);
        INSERT INTO invariants
            (inv_id, title, content, plain_explanation, severity, related_modules, phase)
        VALUES ('INV-TEST', 'Test', 'Bounded', 'Bounded', 'high', '["desktop"]', 'P7');
        INSERT INTO modules
            (module_key, name, description, source_paths, dependencies, invariants,
             verification, plain_summary)
        VALUES
            ('desktop', 'Desktop', 'Host', '["E:/private/repo"]', '[]',
             '["INV-TEST"]', '["pytest"]', 'Summary');
        INSERT INTO glossary (term, plain_explanation, technical_def, category)
        VALUES ('fencing', 'Reject stale work', 'monotonic token', 'security');
        """
    )
    connection.commit()
    connection.close()
    return root


def _repo() -> Path:
    return Path(__file__).resolve().parents[2]


def test_bundle_is_deterministic_closed_and_digest_bound(tmp_path: Path) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    first = tmp_path / "bundle-a"
    second = tmp_path / "bundle-b"

    first_result = exporter.export_component_bundles(
        repo_root=_repo(), ebook_root=source, output_dir=first
    )
    second_result = exporter.export_component_bundles(
        repo_root=_repo(), ebook_root=source, output_dir=second
    )

    assert first_result == second_result
    assert first_result["package_count"] == 10
    assert first_result == exporter.validate_component_bundle(first)
    assert first_result["bundle_sha256"] == exporter._sha256(
        (first / "index.json").read_bytes()
    )
    assert first_result["output_bytes"] == sum(
        path.stat().st_size for path in first.rglob("*") if path.is_file()
    )
    assert (first / "index.json").read_bytes() == (second / "index.json").read_bytes()

    index = json.loads((first / "index.json").read_bytes())
    identities = [(item["component_id"], item["version"]) for item in index["packages"]]
    assert identities == sorted(exporter._EXPECTED_SOURCE_COMPONENTS)
    assert {item["family"] for item in index["packages"]} == {
        "declarative_ui",
        "instruction_skill",
        "mcp_connector",
        "sandbox_workload",
        "trusted_local_adapter",
    }

    claimed = {"index.json"}
    versions: dict[str, dict[str, tuple[str, str, str, tuple[str, ...]]]] = {}
    ebook_sources: set[str] = set()
    for item in index["packages"]:
        manifest_path = first / item["manifest_path"]
        package_path = first / item["package_path"]
        inventory_path = first / item["inventory_path"]
        manifest_raw = manifest_path.read_bytes()
        package_raw = package_path.read_bytes()
        inventory_raw = inventory_path.read_bytes()
        assert exporter._sha256(manifest_raw) == item["manifest_sha256"]
        assert exporter._sha256(package_raw) == item["package_sha256"]
        assert exporter._sha256(inventory_raw) == item["inventory_sha256"]
        assert item["policy_manifest_sha256"] == item["manifest_sha256"]

        manifest = json.loads(manifest_raw)
        package = json.loads(package_raw)
        inventory = json.loads(inventory_raw)
        assert (
            manifest["component_id"] == package["component_id"] == item["component_id"]
        )
        assert manifest["version"] == package["version"] == item["version"]
        assert package["manifest_sha256"] == item["manifest_sha256"]
        assert package["inventory_sha256"] == item["inventory_sha256"]
        assert inventory["component_id"] == item["component_id"]
        assert inventory["version"] == item["version"]

        claimed.update(
            {item["manifest_path"], item["package_path"], item["inventory_path"]}
        )
        payload_hashes: list[str] = []
        for file in inventory["files"]:
            relative = manifest_path.parent / file["path"]
            raw = relative.read_bytes()
            assert len(raw) == file["size"]
            assert exporter._sha256(raw) == file["sha256"]
            claimed.add(relative.relative_to(first).as_posix())
            payload_hashes.append(file["sha256"])
            if (
                item["component_id"] == "knowledge.ebook"
                and file["path"] == "payload/catalog.json"
            ):
                catalog = json.loads(raw)
                ebook_sources.add(catalog["source_snapshot_sha256"])
                assert file["sha256"] not in {
                    item["manifest_sha256"],
                    item["package_sha256"],
                }
        versions.setdefault(item["component_id"], {})[item["version"]] = (
            item["manifest_sha256"],
            item["package_sha256"],
            item["inventory_sha256"],
            tuple(payload_hashes),
        )

    assert {
        path.relative_to(first).as_posix()
        for path in first.rglob("*")
        if path.is_file()
    } == claimed
    assert ebook_sources == {next(iter(ebook_sources))}
    for component_versions in versions.values():
        assert set(component_versions) == {"1.0.0", "1.1.0"}
        assert component_versions["1.0.0"] != component_versions["1.1.0"]


def test_existing_output_is_rejected_without_mutation(tmp_path: Path) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    output = tmp_path / "bundle"
    output.mkdir()
    marker = output / "owner-data.txt"
    marker.write_text("preserve", encoding="utf-8")

    with pytest.raises(exporter.ComponentBundleExportError, match="output_exists"):
        exporter.export_component_bundles(
            repo_root=_repo(), ebook_root=source, output_dir=output
        )

    assert marker.read_text(encoding="utf-8") == "preserve"


def test_sandbox_packages_bind_distinct_zero_import_wasm_bytes(tmp_path: Path) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    bundle = tmp_path / "bundle"
    exporter.export_component_bundles(
        repo_root=_repo(), ebook_root=source, output_dir=bundle
    )
    index = json.loads((bundle / "index.json").read_bytes())
    module_digests: set[str] = set()
    for item in index["packages"]:
        if item["component_id"] != "builtin.sandbox-workload":
            continue
        package_root = (bundle / item["manifest_path"]).parent
        descriptor = json.loads((package_root / "payload/workload.json").read_bytes())
        module = (package_root / "payload/workload.wasm").read_bytes()
        assert module.startswith(b"\x00asm\x01\x00\x00\x00")
        assert descriptor["module_path"] == "payload/workload.wasm"
        assert descriptor["module_sha256"] == exporter._sha256(module)
        assert descriptor["module_format"] == "webassembly_v1"
        assert descriptor["entrypoint"] == "transform"
        assert descriptor["network"] == "no_imports"
        assert descriptor["memory_max_bytes"] == 64 * 1024
        module_digests.add(descriptor["module_sha256"])
    assert len(module_digests) == 2


@pytest.mark.parametrize("drift", ["missing", "duplicate", "family"])
def test_catalog_closed_set_drift_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    original = list(exporter._load_catalog(_repo()).SEEDED_COMPONENT_VERSIONS)
    if drift == "missing":
        changed = original[:-1]
    elif drift == "duplicate":
        changed = [*original[:-1], original[0]]
    else:
        item = original[0]
        replacement = SimpleNamespace(
            component_id=item.component_id,
            version=item.version,
            family="mcp_connector",
            adapter_id=item.adapter_id,
            entrypoint_kind=item.entrypoint_kind,
            manifest_json=item.manifest_json,
            manifest_sha256=item.manifest_sha256,
        )
        changed = [replacement, *original[1:]]
    monkeypatch.setattr(
        exporter,
        "_load_catalog",
        lambda _repo_root: SimpleNamespace(SEEDED_COMPONENT_VERSIONS=changed),
    )

    with pytest.raises(exporter.ComponentBundleExportError, match="component_catalog_"):
        exporter.export_component_bundles(
            repo_root=_repo(), ebook_root=source, output_dir=tmp_path / "bundle"
        )


def test_noncanonical_or_drifted_manifest_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    original = list(exporter._load_catalog(_repo()).SEEDED_COMPONENT_VERSIONS)
    item = original[0]
    replacement = SimpleNamespace(
        component_id=item.component_id,
        version=item.version,
        family=item.family,
        adapter_id=item.adapter_id,
        entrypoint_kind=item.entrypoint_kind,
        manifest_json=item.manifest_json + " ",
        manifest_sha256=exporter._sha256((item.manifest_json + " ").encode("utf-8")),
    )
    monkeypatch.setattr(
        exporter,
        "_load_catalog",
        lambda _repo_root: SimpleNamespace(
            SEEDED_COMPONENT_VERSIONS=[replacement, *original[1:]]
        ),
    )

    with pytest.raises(
        exporter.ComponentBundleExportError, match="manifest_identity_invalid"
    ):
        exporter.export_component_bundles(
            repo_root=_repo(), ebook_root=source, output_dir=tmp_path / "bundle"
        )


def test_payload_path_escape_is_rejected_and_staging_is_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    original = exporter._family_payload

    def escaped(component, *, ebook_catalog):
        if component.family == "instruction_skill":
            return {"payload/../../outside.json": b"{}\n"}
        return original(component, ebook_catalog=ebook_catalog)

    monkeypatch.setattr(exporter, "_family_payload", escaped)
    output = tmp_path / "bundle"
    with pytest.raises(
        exporter.ComponentBundleExportError, match="package_path_invalid"
    ):
        exporter.export_component_bundles(
            repo_root=_repo(), ebook_root=source, output_dir=output
        )

    assert not output.exists()
    assert not (tmp_path / "outside.json").exists()
    assert not list(tmp_path.glob(".bundle.staging-*"))


def test_undeclared_payload_member_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    original = exporter._family_payload

    def extra_member(component, *, ebook_catalog):
        payloads = original(component, ebook_catalog=ebook_catalog)
        if component.family == "instruction_skill":
            return {**payloads, "payload/unused.json": b"{}\n"}
        return payloads

    monkeypatch.setattr(exporter, "_family_payload", extra_member)
    with pytest.raises(exporter.ComponentBundleExportError, match="member_set_invalid"):
        exporter.export_component_bundles(
            repo_root=_repo(), ebook_root=source, output_dir=tmp_path / "bundle"
        )


def test_bundle_size_budget_is_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    monkeypatch.setattr(exporter, "MAX_PACKAGE_BYTES", 1)
    output = tmp_path / "bundle"

    with pytest.raises(exporter.ComponentBundleExportError, match="bundle_too_large"):
        exporter.export_component_bundles(
            repo_root=_repo(), ebook_root=source, output_dir=output
        )

    assert not output.exists()


def test_knowledge_ebook_catalog_keeps_headroom_inside_adapter_output_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    output = tmp_path / "oversized-ebook-bundle"
    monkeypatch.setattr(exporter, "MAX_KNOWLEDGE_EBOOK_CATALOG_BYTES", 1)

    with pytest.raises(
        exporter.ComponentBundleExportError,
        match="ebook_catalog_output_budget_exceeded",
    ):
        exporter.export_component_bundles(
            repo_root=_repo(), ebook_root=source, output_dir=output
        )

    assert not output.exists()
    assert not list(tmp_path.glob(".bundle.staging-*"))


def test_ebook_wal_is_rejected_by_complete_bundle_export(tmp_path: Path) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    (source / "data/ebook.db-wal").write_bytes(b"uncheckpointed")

    with pytest.raises(ValueError, match="wal_not_checkpointed"):
        exporter.export_component_bundles(
            repo_root=_repo(), ebook_root=source, output_dir=tmp_path / "bundle"
        )


def test_ebook_change_between_version_exports_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    ebook_exporter = exporter._load_ebook_exporter(_repo())
    original = ebook_exporter.export_knowledge_ebook

    def changing_export(**kwargs):
        result = original(**kwargs)
        if kwargs["component_version"] == "1.0.0":
            connection = sqlite3.connect(source / "data/ebook.db")
            connection.execute("UPDATE documents SET content = 'changed' WHERE id = 1")
            connection.commit()
            connection.close()
        return result

    monkeypatch.setattr(ebook_exporter, "export_knowledge_ebook", changing_export)
    monkeypatch.setattr(
        exporter, "_load_ebook_exporter", lambda _repo_root: ebook_exporter
    )

    with pytest.raises(
        exporter.ComponentBundleExportError, match="ebook_catalog_source_changed"
    ):
        exporter.export_component_bundles(
            repo_root=_repo(), ebook_root=source, output_dir=tmp_path / "bundle"
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_package",
        "extra_member",
        "payload_drift",
        "index_digest_drift",
        "coherently_resealed_manifest_drift",
    ],
)
def test_bundle_validator_rejects_closed_set_or_digest_drift(
    tmp_path: Path, mutation: str
) -> None:
    exporter = _exporter(_repo())
    source = _source(tmp_path)
    bundle = tmp_path / "bundle"
    exporter.export_component_bundles(
        repo_root=_repo(), ebook_root=source, output_dir=bundle
    )

    index_path = bundle / "index.json"
    index = json.loads(index_path.read_bytes())
    first = index["packages"][0]
    if mutation == "missing_package":
        shutil.rmtree((bundle / first["package_path"]).parent)
    elif mutation == "extra_member":
        (bundle / "unexpected.json").write_bytes(b"{}\n")
    elif mutation == "payload_drift":
        inventory = json.loads((bundle / first["inventory_path"]).read_bytes())
        payload = (bundle / first["manifest_path"]).parent / inventory["files"][0][
            "path"
        ]
        payload.write_bytes(payload.read_bytes() + b" ")
    elif mutation == "index_digest_drift":
        first["package_sha256"] = "0" * 64
        index_path.write_bytes(exporter._canonical_json(index))
    else:
        manifest_path = bundle / first["manifest_path"]
        manifest = json.loads(manifest_path.read_bytes())
        manifest["quiesce_timeout_ms"] += 1
        manifest_raw = exporter._canonical_json(manifest).removesuffix(b"\n")
        manifest_path.write_bytes(manifest_raw)
        manifest_sha256 = exporter._sha256(manifest_raw)
        package_path = bundle / first["package_path"]
        package = json.loads(package_path.read_bytes())
        package["manifest_sha256"] = manifest_sha256
        package_raw = exporter._canonical_json(package)
        package_path.write_bytes(package_raw)
        first["manifest_sha256"] = manifest_sha256
        first["policy_manifest_sha256"] = manifest_sha256
        first["package_sha256"] = exporter._sha256(package_raw)
        index_path.write_bytes(exporter._canonical_json(index))

    with pytest.raises(exporter.ComponentBundleExportError):
        exporter.validate_component_bundle(bundle)
