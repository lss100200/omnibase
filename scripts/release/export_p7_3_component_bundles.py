"""Export the complete source-owned P7.3 component bundle set.

The result is a closed, deterministic directory consumed by the desktop
payload builder. Package descriptors contain only logical host-adapter
identities; no executable path, command, URL, token, or renderer code is
accepted from a component package.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_PACKAGE_FILES = 128
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_PATH = re.compile(r"^payload/[a-z][a-z0-9._-]{1,63}\.json$")
_EXPECTED_PAYLOAD_PATHS = {
    "declarative_ui": {"payload/view.json"},
    "instruction_skill": {"payload/instruction.json"},
    "mcp_connector": {"payload/mcp.json"},
    "sandbox_workload": {"payload/workload.json"},
    "trusted_local_adapter": {"payload/adapter.json", "payload/catalog.json"},
}
_EXPECTED_SOURCE_COMPONENTS = {
    ("builtin.instruction-skill", "1.0.0"): (
        "instruction_skill",
        "instruction-skill.v1",
    ),
    ("builtin.instruction-skill", "1.1.0"): (
        "instruction_skill",
        "instruction-skill.v1",
    ),
    ("builtin.readonly-mcp", "1.0.0"): ("mcp_connector", "readonly-mcp.v1"),
    ("builtin.readonly-mcp", "1.1.0"): ("mcp_connector", "readonly-mcp.v1"),
    ("builtin.sandbox-workload", "1.0.0"): (
        "sandbox_workload",
        "p34-sandbox.v1",
    ),
    ("builtin.sandbox-workload", "1.1.0"): (
        "sandbox_workload",
        "p34-sandbox.v1",
    ),
    ("builtin.workspace-canvas", "1.0.0"): ("declarative_ui", "builtin-ui.v1"),
    ("builtin.workspace-canvas", "1.1.0"): ("declarative_ui", "builtin-ui.v1"),
    ("knowledge.ebook", "1.0.0"): (
        "trusted_local_adapter",
        "trusted-local-app.v1",
    ),
    ("knowledge.ebook", "1.1.0"): (
        "trusted_local_adapter",
        "trusted-local-app.v1",
    ),
}
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_INDEX_KEYS = {
    "adapter_id",
    "component_id",
    "family",
    "inventory_path",
    "inventory_sha256",
    "manifest_path",
    "manifest_sha256",
    "package_path",
    "package_sha256",
    "policy_manifest_sha256",
    "version",
}


class ComponentBundleExportError(RuntimeError):
    pass


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_value(raw: bytes, *, code: str, trailing_newline: bool = True) -> object:
    try:
        value = json.loads(raw)
    except (UnicodeError, ValueError) as exc:
        raise ComponentBundleExportError(code) from exc
    canonical = _canonical_json(value)
    if (canonical if trailing_newline else canonical.removesuffix(b"\n")) != raw:
        raise ComponentBundleExportError(code)
    return value


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)


def _safe_bundle_root(bundle_root: Path) -> Path:
    root = bundle_root.absolute()
    try:
        metadata = os.stat(root, follow_symlinks=False)
        resolved = root.resolve(strict=True)
    except OSError as exc:
        raise ComponentBundleExportError("component_bundle_unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or os.path.normcase(str(root)) != os.path.normcase(str(resolved))
    ):
        raise ComponentBundleExportError("component_bundle_root_identity_invalid")
    return root


def _read_bundle_member(root: Path, relative: str) -> bytes:
    if (
        not relative
        or "\\" in relative
        or relative.startswith("/")
        or any(part in {"", ".", ".."} for part in relative.split("/"))
    ):
        raise ComponentBundleExportError("component_bundle_member_path_invalid")
    path = root.joinpath(*relative.split("/"))
    try:
        metadata = os.stat(path, follow_symlinks=False)
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise ComponentBundleExportError("component_bundle_member_missing") from exc
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or metadata.st_nlink != 1
        or metadata.st_size < 0
        or metadata.st_size > MAX_PACKAGE_BYTES
        or os.path.normcase(str(path)) != os.path.normcase(str(resolved))
    ):
        raise ComponentBundleExportError("component_bundle_member_identity_invalid")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ComponentBundleExportError("component_bundle_member_unavailable") from exc
    if len(raw) != metadata.st_size:
        raise ComponentBundleExportError("component_bundle_member_changed")
    return raw


def validate_component_bundle(bundle_root: Path) -> dict[str, object]:
    """Validate and report the exact source-owned ten-package release bundle."""

    root = _safe_bundle_root(bundle_root)
    catalog = _load_catalog(Path(__file__).resolve().parents[2])
    expected_components = {
        (item.component_id, item.version): item
        for item in _validated_components(catalog)
    }
    index_raw = _read_bundle_member(root, "index.json")
    index = _canonical_value(index_raw, code="component_bundle_index_invalid")
    if (
        not isinstance(index, dict)
        or set(index) != {"packages", "schema_version"}
        or index["schema_version"] != SCHEMA_VERSION
        or not isinstance(index["packages"], list)
        or len(index["packages"]) != len(_EXPECTED_SOURCE_COMPONENTS)
    ):
        raise ComponentBundleExportError("component_bundle_index_invalid")

    claimed_files = {"index.json"}
    claimed_directories: set[str] = set()
    file_records = [("index.json", index_raw)]
    identities: list[tuple[str, str]] = []
    ebook_sources: set[str] = set()
    for item in index["packages"]:
        if not isinstance(item, dict) or set(item) != _INDEX_KEYS:
            raise ComponentBundleExportError("component_bundle_index_invalid")
        component_id = item["component_id"]
        version = item["version"]
        identity = (component_id, version)
        if (
            not isinstance(component_id, str)
            or not isinstance(version, str)
            or identity not in _EXPECTED_SOURCE_COMPONENTS
        ):
            raise ComponentBundleExportError("component_bundle_identity_invalid")
        family, adapter_id = _EXPECTED_SOURCE_COMPONENTS[identity]
        component = expected_components[identity]
        slug = component_id.replace(".", "-")
        package_root = f"{slug}/{version}"
        expected_paths = {
            "manifest_path": f"{package_root}/manifest.json",
            "package_path": f"{package_root}/package.json",
            "inventory_path": f"{package_root}/inventory.json",
        }
        if (
            item["family"] != family
            or item["adapter_id"] != adapter_id
            or item["policy_manifest_sha256"] != item["manifest_sha256"]
            or any(item[key] != expected for key, expected in expected_paths.items())
            or any(
                not isinstance(item[key], str) or _SHA256.fullmatch(item[key]) is None
                for key in (
                    "manifest_sha256",
                    "package_sha256",
                    "inventory_sha256",
                    "policy_manifest_sha256",
                )
            )
        ):
            raise ComponentBundleExportError("component_bundle_identity_invalid")

        manifest_raw = _read_bundle_member(root, expected_paths["manifest_path"])
        package_raw = _read_bundle_member(root, expected_paths["package_path"])
        inventory_raw = _read_bundle_member(root, expected_paths["inventory_path"])
        if (
            _sha256(manifest_raw) != item["manifest_sha256"]
            or _sha256(package_raw) != item["package_sha256"]
            or _sha256(inventory_raw) != item["inventory_sha256"]
            or manifest_raw != component.manifest_json.encode("utf-8")
        ):
            raise ComponentBundleExportError("component_bundle_digest_invalid")
        manifest = _canonical_value(
            manifest_raw,
            code="component_bundle_manifest_invalid",
            trailing_newline=False,
        )
        package = _canonical_value(package_raw, code="component_bundle_package_invalid")
        inventory = _canonical_value(
            inventory_raw, code="component_bundle_inventory_invalid"
        )
        if (
            not isinstance(manifest, dict)
            or manifest.get("component_id") != component_id
            or manifest.get("version") != version
            or manifest.get("family") != family
            or manifest.get("publisher")
            != {"classification": "source_owned", "id": "omnibase"}
            or not isinstance(manifest.get("entrypoint"), dict)
            or manifest["entrypoint"].get("adapter_id") != adapter_id
            or not isinstance(package, dict)
            or set(package)
            != {
                "adapter_id",
                "component_id",
                "family",
                "inventory_sha256",
                "manifest_sha256",
                "package_schema_version",
                "publisher",
                "version",
            }
            or package
            != {
                "adapter_id": adapter_id,
                "component_id": component_id,
                "family": family,
                "inventory_sha256": item["inventory_sha256"],
                "manifest_sha256": item["manifest_sha256"],
                "package_schema_version": SCHEMA_VERSION,
                "publisher": {"classification": "source_owned", "id": "omnibase"},
                "version": version,
            }
            or not isinstance(inventory, dict)
            or set(inventory) != {"component_id", "files", "schema_version", "version"}
            or inventory["component_id"] != component_id
            or inventory["version"] != version
            or inventory["schema_version"] != SCHEMA_VERSION
            or not isinstance(inventory["files"], list)
        ):
            raise ComponentBundleExportError("component_bundle_metadata_invalid")

        payload_paths: list[str] = []
        payload_values: dict[str, bytes] = {}
        for payload in inventory["files"]:
            if (
                not isinstance(payload, dict)
                or set(payload) != {"path", "sha256", "size"}
                or not isinstance(payload["path"], str)
                or _PAYLOAD_PATH.fullmatch(payload["path"]) is None
                or not isinstance(payload["sha256"], str)
                or _SHA256.fullmatch(payload["sha256"]) is None
                or type(payload["size"]) is not int
                or payload["size"] < 0
            ):
                raise ComponentBundleExportError("component_bundle_inventory_invalid")
            relative = f"{package_root}/{payload['path']}"
            raw = _read_bundle_member(root, relative)
            if len(raw) != payload["size"] or _sha256(raw) != payload["sha256"]:
                raise ComponentBundleExportError("component_bundle_payload_invalid")
            payload_paths.append(payload["path"])
            payload_values[payload["path"]] = raw
            claimed_files.add(relative)
            file_records.append((relative, raw))
        if (
            payload_paths != sorted(payload_paths)
            or len(payload_paths) != len(_EXPECTED_PAYLOAD_PATHS[family])
            or set(payload_paths) != _EXPECTED_PAYLOAD_PATHS[family]
        ):
            raise ComponentBundleExportError("component_package_member_set_invalid")
        expected_payloads = _family_payload(
            component,
            ebook_catalog=payload_values.get("payload/catalog.json"),
        )
        if payload_values != expected_payloads:
            raise ComponentBundleExportError("component_bundle_payload_invalid")
        if component_id == "knowledge.ebook":
            ebook_catalog = _canonical_value(
                payload_values["payload/catalog.json"],
                code="ebook_catalog_invalid",
            )
            if (
                not isinstance(ebook_catalog, dict)
                or ebook_catalog.get("component_id") != component_id
                or ebook_catalog.get("component_version") != version
                or not isinstance(ebook_catalog.get("source_snapshot_sha256"), str)
                or _SHA256.fullmatch(ebook_catalog["source_snapshot_sha256"]) is None
            ):
                raise ComponentBundleExportError("ebook_catalog_identity_invalid")
            ebook_sources.add(ebook_catalog["source_snapshot_sha256"])

        identities.append(identity)
        claimed_files.update(expected_paths.values())
        file_records.extend(
            (
                (expected_paths["manifest_path"], manifest_raw),
                (expected_paths["package_path"], package_raw),
                (expected_paths["inventory_path"], inventory_raw),
            )
        )
        claimed_directories.update({slug, package_root, f"{package_root}/payload"})

    if identities != sorted(_EXPECTED_SOURCE_COMPONENTS):
        raise ComponentBundleExportError("component_bundle_identity_set_invalid")
    if len(ebook_sources) != 1:
        raise ComponentBundleExportError("ebook_catalog_source_changed")

    actual_files: set[str] = set()
    actual_directories: set[str] = set()
    folded: set[str] = set()
    pending = [(root, "")]
    while pending:
        directory, prefix = pending.pop()
        try:
            with os.scandir(directory) as iterator:
                entries = list(iterator)
        except OSError as exc:
            raise ComponentBundleExportError("component_bundle_tree_invalid") from exc
        for entry in entries:
            relative = f"{prefix}/{entry.name}" if prefix else entry.name
            key = relative.casefold()
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise ComponentBundleExportError(
                    "component_bundle_tree_invalid"
                ) from exc
            if (
                entry.name in {"", ".", ".."}
                or "\\" in entry.name
                or key in folded
                or stat.S_ISLNK(metadata.st_mode)
                or _is_reparse(metadata)
            ):
                raise ComponentBundleExportError("component_bundle_tree_invalid")
            folded.add(key)
            if stat.S_ISREG(metadata.st_mode):
                actual_files.add(relative)
            elif stat.S_ISDIR(metadata.st_mode):
                actual_directories.add(relative)
                pending.append((Path(entry.path), relative))
            else:
                raise ComponentBundleExportError("component_bundle_tree_invalid")
    if actual_files != claimed_files or actual_directories != claimed_directories:
        raise ComponentBundleExportError("component_bundle_tree_closed_set_invalid")

    total_bytes = sum(len(raw) for _, raw in file_records)
    if total_bytes > MAX_PACKAGE_BYTES:
        raise ComponentBundleExportError("component_bundle_too_large")
    tree_raw = b"".join(
        relative.encode("utf-8")
        + b"\0"
        + str(len(raw)).encode("ascii")
        + b"\0"
        + _sha256(raw).encode("ascii")
        + b"\n"
        for relative, raw in sorted(file_records)
    )
    return {
        "bundle_sha256": _sha256(index_raw),
        "file_count": len(file_records),
        "output_bytes": total_bytes,
        "package_count": len(identities),
        "tree_sha256": _sha256(tree_raw),
    }


def _load_catalog(repo_root: Path) -> Any:
    backend_src = (repo_root / "backend" / "src").resolve(strict=True)
    sys.path.insert(0, str(backend_src))
    try:
        from omnibase.desktop_local.components import catalog
    except ImportError as exc:
        raise ComponentBundleExportError("component_catalog_import_failed") from exc
    finally:
        sys.path.pop(0)
    return catalog


def _load_ebook_exporter(repo_root: Path) -> Any:
    release_dir = (repo_root / "scripts" / "release").resolve(strict=True)
    sys.path.insert(0, str(release_dir))
    try:
        import export_p7_3_knowledge_ebook as exporter
    except ImportError as exc:
        raise ComponentBundleExportError("ebook_exporter_import_failed") from exc
    finally:
        sys.path.pop(0)
    return exporter


def _validated_components(catalog: Any) -> tuple[Any, ...]:
    try:
        components = tuple(catalog.SEEDED_COMPONENT_VERSIONS)
        identities = {(item.component_id, item.version) for item in components}
    except (AttributeError, TypeError) as exc:
        raise ComponentBundleExportError(
            "component_catalog_closed_set_invalid"
        ) from exc
    if len(components) != len(_EXPECTED_SOURCE_COMPONENTS) or identities != set(
        _EXPECTED_SOURCE_COMPONENTS
    ):
        raise ComponentBundleExportError("component_catalog_closed_set_invalid")
    for component in components:
        identity = (component.component_id, component.version)
        expected_family, expected_adapter = _EXPECTED_SOURCE_COMPONENTS[identity]
        if (
            component.family != expected_family
            or component.adapter_id != expected_adapter
            or not isinstance(component.manifest_json, str)
            or not isinstance(component.manifest_sha256, str)
            or _SHA256.fullmatch(component.manifest_sha256) is None
        ):
            raise ComponentBundleExportError("component_catalog_identity_invalid")
        manifest_raw = component.manifest_json.encode("utf-8")
        try:
            manifest = json.loads(manifest_raw)
        except (UnicodeError, ValueError) as exc:
            raise ComponentBundleExportError(
                "component_catalog_manifest_invalid"
            ) from exc
        canonical = json.dumps(
            manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if (
            manifest_raw != canonical
            or _sha256(manifest_raw) != component.manifest_sha256
            or not isinstance(manifest, dict)
            or manifest.get("component_id") != component.component_id
            or manifest.get("version") != component.version
            or manifest.get("family") != component.family
            or manifest.get("publisher")
            != {"classification": "source_owned", "id": "omnibase"}
            or manifest.get("entrypoint")
            != {
                "adapter_id": component.adapter_id,
                "kind": component.entrypoint_kind,
            }
        ):
            raise ComponentBundleExportError(
                "component_catalog_manifest_identity_invalid"
            )
    return tuple(sorted(components, key=lambda item: (item.component_id, item.version)))


def _validated_payloads(
    payloads: object, *, family: str
) -> tuple[tuple[str, bytes], ...]:
    if not isinstance(payloads, dict) or not 1 <= len(payloads) <= MAX_PACKAGE_FILES:
        raise ComponentBundleExportError("component_package_file_count_invalid")
    values: list[tuple[str, bytes]] = []
    for relative, raw in payloads.items():
        if (
            not isinstance(relative, str)
            or _PAYLOAD_PATH.fullmatch(relative) is None
            or not isinstance(raw, bytes)
        ):
            raise ComponentBundleExportError("component_package_path_invalid")
        values.append((relative, raw))
    if (
        family not in _EXPECTED_PAYLOAD_PATHS
        or set(payloads) != _EXPECTED_PAYLOAD_PATHS[family]
    ):
        raise ComponentBundleExportError("component_package_member_set_invalid")
    return tuple(sorted(values))


def _family_payload(component: Any, *, ebook_catalog: bytes | None) -> dict[str, bytes]:
    version = component.version
    if component.family == "declarative_ui":
        view = {
            "component_id": component.component_id,
            "schema_version": 1,
            "version": version,
            "view": {
                "kind": "workspace_summary",
                "sections": [
                    {
                        "id": "identity",
                        "label": "Component identity",
                        "source": "installation",
                    },
                    {"id": "health", "label": "Runtime health", "source": "health"},
                    {"id": "grants", "label": "Granted operations", "source": "grants"},
                ],
                "title": "Workspace component"
                if version == "1.0.0"
                else "Workspace component status",
            },
        }
        return {"payload/view.json": _canonical_json(view)}
    if component.family == "instruction_skill":
        instruction = {
            "component_id": component.component_id,
            "instruction": (
                "Inspect only the supplied Workspace context. Return concise observations and "
                "an Owner-reviewable next action. Do not claim capabilities or perform writes."
                if version == "1.0.0"
                else "Inspect only the supplied Workspace context. Separate evidence, uncertainty, and "
                "an Owner-reviewable next action. Do not claim capabilities or perform writes."
            ),
            "schema_version": 1,
            "version": version,
        }
        return {"payload/instruction.json": _canonical_json(instruction)}
    if component.family == "mcp_connector":
        mcp = {
            "component_id": component.component_id,
            "schema_version": 1,
            "server": {
                "server_id": "workspace-files-readonly",
                "transport": "host_native",
            },
            "tools": [
                {
                    "input": {"directory": "logical_relative_directory"},
                    "operation": "workspace.files.list",
                    "output": "bounded_logical_file_inventory",
                    "tool_id": "omnibase_files_list",
                },
                {
                    "input": {"path": "logical_relative_file"},
                    "operation": "workspace.files.read",
                    "output": "bounded_utf8_file",
                    "tool_id": "omnibase_files_read",
                },
                {
                    "input": {"path": "logical_relative_file"},
                    "operation": "workspace.files.hash",
                    "output": "bounded_file_identity",
                    "tool_id": "omnibase_files_hash",
                },
                {
                    "input": {"path": "logical_relative_file", "query": "bounded_text"},
                    "operation": "workspace.text.search",
                    "output": "bounded_match_inventory",
                    "tool_id": "omnibase_text_search",
                },
            ],
            "version": version,
        }
        return {"payload/mcp.json": _canonical_json(mcp)}
    if component.family == "sandbox_workload":
        workload = {
            "component_id": component.component_id,
            "input_contract": "logical_artifact_ids",
            "output_contract": "artifact_inventory",
            "provider": "p34-sandbox.v1",
            "schema_version": 1,
            "version": version,
            "workload_id": "bounded-transform",
        }
        return {"payload/workload.json": _canonical_json(workload)}
    if component.family == "trusted_local_adapter":
        if ebook_catalog is None:
            raise ComponentBundleExportError("ebook_catalog_missing")
        adapter = {
            "adapter_id": "trusted-local-app.v1",
            "catalog_path": "payload/catalog.json",
            "component_id": component.component_id,
            "operation": "local_adapter.open",
            "schema_version": 1,
            "version": version,
        }
        return {
            "payload/adapter.json": _canonical_json(adapter),
            "payload/catalog.json": ebook_catalog,
        }
    raise ComponentBundleExportError("component_family_unsupported")


def _ebook_catalogs(
    repo_root: Path, ebook_root: Path, versions: set[str]
) -> dict[str, bytes]:
    if versions != {"1.0.0", "1.1.0"}:
        raise ComponentBundleExportError("ebook_catalog_version_set_invalid")
    exporter = _load_ebook_exporter(repo_root)
    catalogs: dict[str, bytes] = {}
    source_snapshot_sha256: str | None = None
    with tempfile.TemporaryDirectory(prefix="omnibase-p73-ebook-") as temporary:
        root = Path(temporary)
        for version in sorted(versions):
            output = root / version
            result = exporter.export_knowledge_ebook(
                source_root=ebook_root,
                output_dir=output,
                component_version=version,
            )
            catalog_raw = (output / "knowledge-ebook" / "catalog.json").read_bytes()
            try:
                catalog = json.loads(catalog_raw)
            except ValueError as exc:
                raise ComponentBundleExportError("ebook_catalog_invalid") from exc
            snapshot = result.get("source_snapshot_sha256")
            if (
                not isinstance(snapshot, str)
                or _SHA256.fullmatch(snapshot) is None
                or result.get("catalog_sha256") != _sha256(catalog_raw)
                or not isinstance(catalog, dict)
                or catalog.get("component_id") != "knowledge.ebook"
                or catalog.get("component_version") != version
                or catalog.get("source_snapshot_sha256") != snapshot
            ):
                raise ComponentBundleExportError("ebook_catalog_identity_invalid")
            if (
                source_snapshot_sha256 is not None
                and snapshot != source_snapshot_sha256
            ):
                raise ComponentBundleExportError("ebook_catalog_source_changed")
            source_snapshot_sha256 = snapshot
            catalogs[version] = catalog_raw
    return catalogs


def export_component_bundles(
    *, repo_root: Path, ebook_root: Path, output_dir: Path
) -> dict[str, object]:
    repo = repo_root.resolve(strict=True)
    ebook = ebook_root.resolve(strict=True)
    output = output_dir.resolve()
    if output.exists():
        raise ComponentBundleExportError("component_bundle_output_exists")
    parent = output.parent.resolve(strict=True)
    catalog = _load_catalog(repo)
    components = _validated_components(catalog)
    ebook_versions = {
        item.version for item in components if item.component_id == "knowledge.ebook"
    }
    ebook_catalogs = _ebook_catalogs(repo, ebook, ebook_versions)
    staging = parent / f".{output.name}.staging-{os.getpid()}-{uuid.uuid4().hex}"
    packages: list[dict[str, object]] = []
    total_bytes = 0
    try:
        staging.mkdir()
        for component in components:
            slug = component.component_id.replace(".", "-")
            package_root = staging / slug / component.version
            package_root.mkdir(parents=True)
            manifest_raw = component.manifest_json.encode("utf-8")
            manifest_path = f"{slug}/{component.version}/manifest.json"
            (package_root / "manifest.json").write_bytes(manifest_raw)
            payloads = _family_payload(
                component,
                ebook_catalog=ebook_catalogs.get(component.version),
            )
            validated_payloads = _validated_payloads(payloads, family=component.family)
            inventory_files: list[dict[str, object]] = []
            for relative, raw in validated_payloads:
                inventory_files.append(
                    {"path": relative, "sha256": _sha256(raw), "size": len(raw)}
                )
            inventory = {
                "component_id": component.component_id,
                "files": inventory_files,
                "schema_version": SCHEMA_VERSION,
                "version": component.version,
            }
            inventory_raw = _canonical_json(inventory)
            inventory_sha256 = _sha256(inventory_raw)
            inventory_path = f"{slug}/{component.version}/inventory.json"
            (package_root / "inventory.json").write_bytes(inventory_raw)
            package = {
                "adapter_id": component.adapter_id,
                "component_id": component.component_id,
                "family": component.family,
                "inventory_sha256": inventory_sha256,
                "manifest_sha256": component.manifest_sha256,
                "package_schema_version": SCHEMA_VERSION,
                "publisher": {"classification": "source_owned", "id": "omnibase"},
                "version": component.version,
            }
            package_raw = _canonical_json(package)
            package_sha256 = _sha256(package_raw)
            package_path = f"{slug}/{component.version}/package.json"
            package_bytes = (
                len(manifest_raw)
                + len(inventory_raw)
                + len(package_raw)
                + sum(len(raw) for _, raw in validated_payloads)
            )
            if total_bytes + package_bytes > MAX_PACKAGE_BYTES:
                raise ComponentBundleExportError("component_bundle_too_large")
            for relative, raw in validated_payloads:
                target = package_root.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(raw)
            (package_root / "package.json").write_bytes(package_raw)
            total_bytes += package_bytes
            packages.append(
                {
                    "adapter_id": component.adapter_id,
                    "component_id": component.component_id,
                    "family": component.family,
                    "inventory_path": inventory_path,
                    "inventory_sha256": inventory_sha256,
                    "manifest_path": manifest_path,
                    "manifest_sha256": component.manifest_sha256,
                    "package_path": package_path,
                    "package_sha256": package_sha256,
                    "policy_manifest_sha256": component.manifest_sha256,
                    "version": component.version,
                }
            )
        index_raw = _canonical_json(
            {"packages": packages, "schema_version": SCHEMA_VERSION}
        )
        if total_bytes + len(index_raw) > MAX_PACKAGE_BYTES:
            raise ComponentBundleExportError("component_bundle_too_large")
        (staging / "index.json").write_bytes(index_raw)
        report = validate_component_bundle(staging)
        staging.rename(output)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--ebook-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = export_component_bundles(**vars(args))
    except (ComponentBundleExportError, OSError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
