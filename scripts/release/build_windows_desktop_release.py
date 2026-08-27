"""Build and verify the closed-set OmniBase Windows desktop manifest v2.

This first P6.5 release primitive is deliberately offline.  It inventories an
already-built payload directory; it does not sign, install, download, execute,
or archive payload files.  Signing evidence must come from a later trusted
verification stage and defaults to false.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

SCHEMA_VERSION = 2
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
HARD_MAX_FILES = 4096
HARD_MAX_FILE_BYTES = 512 * 1024 * 1024
HARD_MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_MIGRATION = re.compile(r"^[0-9]{4}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_. -]+$")
_EXPECTED_TEMPLATE_KEYS = frozenset(
    {
        "schema_version",
        "product",
        "version",
        "channel",
        "platform",
        "publisher",
        "runtime_profile",
        "migration_compatibility",
        "feature_gates",
        "optional_components",
        "production_ready",
        "limits",
        "expected_files",
    }
)
_EXPECTED_MIGRATION_KEYS = frozenset({"minimum", "current", "maximum"})
_EXPECTED_GATE_KEYS = frozenset(
    {
        "agent_runtime_enabled",
        "agent_planner_enabled",
        "multi_agent_enabled",
        "mcp_runtime_enabled",
    }
)
_EXPECTED_LIMIT_KEYS = frozenset({"max_files", "max_file_bytes", "max_total_bytes"})
_EXPECTED_COMPONENT_KEYS = frozenset({"id", "bundled", "required"})
_OPTIONAL_COMPONENTS = frozenset({"bge-m3", "postgresql-pgvector", "hardened-sandbox"})
_FORBIDDEN_SUFFIXES = frozenset(
    {
        ".db",
        ".env",
        ".key",
        ".p12",
        ".pem",
        ".pfx",
        ".sqlite",
        ".sqlite3",
        ".vdi",
        ".vhd",
        ".vhdx",
        ".vmdk",
    }
)


class DesktopReleaseError(ValueError):
    """A deterministic, fail-closed desktop release validation failure."""


class SignatureStatus(NamedTuple):
    publisher_signature_verified: bool = False
    authenticode_verified: bool = False


class PayloadLimits(NamedTuple):
    max_files: int
    max_file_bytes: int
    max_total_bytes: int


def _require_exact_keys(
    value: dict[str, Any], expected: frozenset[str], label: str
) -> None:
    if frozenset(value) != expected:
        raise DesktopReleaseError(f"desktop_{label}_key_set_invalid")


def _load_json_object(path: Path, *, label: str) -> dict[str, Any]:
    metadata = os.stat(path, follow_symlinks=False)
    if not _is_regular_non_reparse(metadata):
        raise DesktopReleaseError(f"desktop_{label}_not_regular")
    if metadata.st_size <= 0 or metadata.st_size > MAX_MANIFEST_BYTES:
        raise DesktopReleaseError(f"desktop_{label}_size_invalid")
    try:
        value = json.loads(path.read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DesktopReleaseError(f"desktop_{label}_json_invalid") from exc
    if not isinstance(value, dict):
        raise DesktopReleaseError(f"desktop_{label}_root_invalid")
    return value


def _is_regular_non_reparse(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)
    )


def _validate_relative_path(raw: object) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > 240:
        raise DesktopReleaseError("desktop_payload_path_invalid")
    if "\\" in raw or "\x00" in raw or raw.startswith("/"):
        raise DesktopReleaseError("desktop_payload_path_invalid")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or str(path) != raw
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DesktopReleaseError("desktop_payload_path_invalid")
    for part in path.parts:
        if not _SAFE_COMPONENT.fullmatch(part) or ":" in part:
            raise DesktopReleaseError("desktop_payload_path_invalid")
        folded = part.casefold()
        if (
            folded == ".env"
            or folded.startswith(".env.")
            or folded.endswith(".env")
            or ".env." in folded
        ):
            raise DesktopReleaseError("desktop_payload_sensitive_path_forbidden")
    if path.suffix.casefold() in _FORBIDDEN_SUFFIXES:
        raise DesktopReleaseError("desktop_payload_sensitive_path_forbidden")
    return raw


def _positive_int(value: object, *, label: str, ceiling: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > ceiling
    ):
        raise DesktopReleaseError(f"desktop_{label}_invalid")
    return value


def _validate_template(
    template: dict[str, Any],
) -> tuple[dict[str, Any], PayloadLimits, tuple[str, ...]]:
    _require_exact_keys(template, _EXPECTED_TEMPLATE_KEYS, "template")
    if template["schema_version"] != SCHEMA_VERSION:
        raise DesktopReleaseError("desktop_schema_version_invalid")
    if template["product"] != "OmniBase":
        raise DesktopReleaseError("desktop_product_invalid")
    if (
        not isinstance(template["version"], str)
        or _VERSION.fullmatch(template["version"]) is None
    ):
        raise DesktopReleaseError("desktop_version_invalid")
    if template["channel"] not in {"stable", "preview", "nightly"}:
        raise DesktopReleaseError("desktop_channel_invalid")
    if template["platform"] not in {"windows-x64", "windows-arm64"}:
        raise DesktopReleaseError("desktop_platform_invalid")
    publisher = template["publisher"]
    if not isinstance(publisher, str) or not publisher or len(publisher) > 128:
        raise DesktopReleaseError("desktop_publisher_invalid")
    if template["runtime_profile"] != "personal-desktop-core":
        raise DesktopReleaseError("desktop_runtime_profile_invalid")

    migration = template["migration_compatibility"]
    if not isinstance(migration, dict):
        raise DesktopReleaseError("desktop_migration_compatibility_invalid")
    _require_exact_keys(migration, _EXPECTED_MIGRATION_KEYS, "migration")
    if any(
        not isinstance(value, str) or _MIGRATION.fullmatch(value) is None
        for value in migration.values()
    ):
        raise DesktopReleaseError("desktop_migration_compatibility_invalid")
    if not migration["minimum"] <= migration["current"] <= migration["maximum"]:
        raise DesktopReleaseError("desktop_migration_compatibility_invalid")

    gates = template["feature_gates"]
    if not isinstance(gates, dict):
        raise DesktopReleaseError("desktop_feature_gates_invalid")
    _require_exact_keys(gates, _EXPECTED_GATE_KEYS, "feature_gates")
    if any(type(value) is not bool for value in gates.values()):
        raise DesktopReleaseError("desktop_feature_gates_invalid")

    components = template["optional_components"]
    if not isinstance(components, list):
        raise DesktopReleaseError("desktop_optional_components_invalid")
    component_ids: set[str] = set()
    for component in components:
        if not isinstance(component, dict):
            raise DesktopReleaseError("desktop_optional_components_invalid")
        _require_exact_keys(component, _EXPECTED_COMPONENT_KEYS, "optional_component")
        component_id = component["id"]
        if component_id not in _OPTIONAL_COMPONENTS or component_id in component_ids:
            raise DesktopReleaseError("desktop_optional_components_invalid")
        if type(component["bundled"]) is not bool or component["required"] is not False:
            raise DesktopReleaseError("desktop_optional_components_invalid")
        component_ids.add(component_id)

    if type(template["production_ready"]) is not bool:
        raise DesktopReleaseError("desktop_production_ready_invalid")
    limits_value = template["limits"]
    if not isinstance(limits_value, dict):
        raise DesktopReleaseError("desktop_limits_invalid")
    _require_exact_keys(limits_value, _EXPECTED_LIMIT_KEYS, "limits")
    limits = PayloadLimits(
        max_files=_positive_int(
            limits_value["max_files"], label="max_files", ceiling=HARD_MAX_FILES
        ),
        max_file_bytes=_positive_int(
            limits_value["max_file_bytes"],
            label="max_file_bytes",
            ceiling=HARD_MAX_FILE_BYTES,
        ),
        max_total_bytes=_positive_int(
            limits_value["max_total_bytes"],
            label="max_total_bytes",
            ceiling=HARD_MAX_TOTAL_BYTES,
        ),
    )
    if limits.max_file_bytes > limits.max_total_bytes:
        raise DesktopReleaseError("desktop_limits_invalid")

    expected_value = template["expected_files"]
    if not isinstance(expected_value, list) or not expected_value:
        raise DesktopReleaseError("desktop_expected_files_invalid")
    expected = tuple(_validate_relative_path(value) for value in expected_value)
    if len(expected) > limits.max_files or len(
        {value.casefold() for value in expected}
    ) != len(expected):
        raise DesktopReleaseError("desktop_expected_files_duplicate_or_over_budget")
    return (
        template,
        limits,
        tuple(sorted(expected, key=lambda value: (value.casefold(), value))),
    )


def load_template(path: Path) -> tuple[dict[str, Any], PayloadLimits, tuple[str, ...]]:
    return _validate_template(_load_json_object(path, label="template"))


def _assert_root_is_safe(root: Path) -> Path:
    absolute = root.absolute()
    metadata = os.stat(absolute, follow_symlinks=False)
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)
    ):
        raise DesktopReleaseError("desktop_payload_root_link_or_reparse_forbidden")
    return absolute


def _inventory_payload(root: Path, limits: PayloadLimits) -> dict[str, tuple[int, str]]:
    inventory: dict[str, tuple[int, str]] = {}
    folded_paths: set[str] = set()
    total_size = 0

    def walk(directory: Path, relative_parts: tuple[str, ...]) -> None:
        nonlocal total_size
        with os.scandir(directory) as entries:
            ordered = sorted(
                entries, key=lambda entry: (entry.name.casefold(), entry.name)
            )
        for entry in ordered:
            relative = "/".join((*relative_parts, entry.name))
            safe_relative = _validate_relative_path(relative)
            metadata = entry.stat(follow_symlinks=False)
            is_reparse = bool(
                getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG
            )
            if entry.is_symlink() or is_reparse:
                raise DesktopReleaseError("desktop_payload_link_or_reparse_forbidden")
            if stat.S_ISDIR(metadata.st_mode):
                walk(Path(entry.path), (*relative_parts, entry.name))
                continue
            if not _is_regular_non_reparse(metadata):
                raise DesktopReleaseError("desktop_payload_not_regular")
            folded = safe_relative.casefold()
            if folded in folded_paths:
                raise DesktopReleaseError("desktop_payload_duplicate_path")
            if len(inventory) >= limits.max_files:
                raise DesktopReleaseError("desktop_payload_file_count_over_budget")
            if metadata.st_size <= 0 or metadata.st_size > limits.max_file_bytes:
                raise DesktopReleaseError("desktop_payload_file_size_invalid")
            total_size += metadata.st_size
            if total_size > limits.max_total_bytes:
                raise DesktopReleaseError("desktop_payload_total_size_over_budget")
            raw = Path(entry.path).read_bytes()
            after = entry.stat(follow_symlinks=False)
            if (
                after.st_size != metadata.st_size
                or after.st_mtime_ns != metadata.st_mtime_ns
            ):
                raise DesktopReleaseError("desktop_payload_changed_during_build")
            inventory[safe_relative] = (len(raw), hashlib.sha256(raw).hexdigest())
            folded_paths.add(folded)

    walk(root, ())
    return inventory


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def build_desktop_release(
    input_dir: Path,
    output_manifest: Path,
    *,
    source_commit: str,
    template_path: Path,
    signature_status: SignatureStatus | None = None,
) -> dict[str, Any]:
    if _COMMIT.fullmatch(source_commit) is None:
        raise DesktopReleaseError("desktop_source_commit_invalid")
    template, limits, expected = load_template(template_path)
    payload_root = _assert_root_is_safe(input_dir)
    output = output_manifest.absolute()
    try:
        output.relative_to(payload_root)
    except ValueError:
        pass
    else:
        raise DesktopReleaseError("desktop_manifest_must_be_outside_payload")
    if output.exists():
        raise DesktopReleaseError("desktop_manifest_output_exists")

    inventory = _inventory_payload(payload_root, limits)
    if (
        tuple(sorted(inventory, key=lambda value: (value.casefold(), value)))
        != expected
    ):
        raise DesktopReleaseError("desktop_payload_closed_set_drifted")
    signatures = signature_status or SignatureStatus()
    if (
        type(signatures.publisher_signature_verified) is not bool
        or type(signatures.authenticode_verified) is not bool
    ):
        raise DesktopReleaseError("desktop_signature_status_invalid")
    production_ready = template["production_ready"]
    if production_ready and not (
        signatures.publisher_signature_verified and signatures.authenticode_verified
    ):
        raise DesktopReleaseError("desktop_unsigned_release_cannot_be_production_ready")

    files = [
        {"path": path, "size": inventory[path][0], "sha256": inventory[path][1]}
        for path in expected
    ]
    manifest = {
        key: template[key]
        for key in (
            "schema_version",
            "product",
            "version",
            "channel",
            "platform",
            "publisher",
            "runtime_profile",
            "migration_compatibility",
            "feature_gates",
            "optional_components",
        )
    }
    manifest.update(
        {
            "source_commit": source_commit,
            "production_ready": production_ready,
            "publisher_signature_verified": signatures.publisher_signature_verified,
            "authenticode_verified": signatures.authenticode_verified,
            "total_size": sum(item["size"] for item in files),
            "files": files,
        }
    )
    raw = _canonical_json(manifest)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("xb") as handle:
        handle.write(raw)
    return manifest


def verify_desktop_release(input_dir: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _load_json_object(manifest_path, label="manifest")
    expected_manifest_keys = _EXPECTED_TEMPLATE_KEYS.difference(
        {"limits", "expected_files"}
    ).union(
        {
            "source_commit",
            "publisher_signature_verified",
            "authenticode_verified",
            "total_size",
            "files",
        }
    )
    _require_exact_keys(manifest, frozenset(expected_manifest_keys), "manifest")
    if _COMMIT.fullmatch(str(manifest.get("source_commit", ""))) is None:
        raise DesktopReleaseError("desktop_source_commit_invalid")
    if (
        type(manifest["production_ready"]) is not bool
        or type(manifest["publisher_signature_verified"]) is not bool
        or type(manifest["authenticode_verified"]) is not bool
    ):
        raise DesktopReleaseError("desktop_signature_status_invalid")
    if manifest["production_ready"] and not (
        manifest["publisher_signature_verified"] and manifest["authenticode_verified"]
    ):
        raise DesktopReleaseError("desktop_unsigned_release_cannot_be_production_ready")
    metadata_template = {
        key: manifest[key]
        for key in _EXPECTED_TEMPLATE_KEYS.difference({"limits", "expected_files"})
    }
    files = manifest["files"]
    if not isinstance(files, list) or not files or len(files) > HARD_MAX_FILES:
        raise DesktopReleaseError("desktop_manifest_files_invalid")
    expected: dict[str, tuple[int, str]] = {}
    for item in files:
        if not isinstance(item, dict) or frozenset(item) != {"path", "size", "sha256"}:
            raise DesktopReleaseError("desktop_manifest_files_invalid")
        path = _validate_relative_path(item["path"])
        size = _positive_int(
            item["size"], label="manifest_file_size", ceiling=HARD_MAX_FILE_BYTES
        )
        digest = item["sha256"]
        if (
            not isinstance(digest, str)
            or _SHA256.fullmatch(digest) is None
            or path.casefold() in {name.casefold() for name in expected}
        ):
            raise DesktopReleaseError("desktop_manifest_files_invalid")
        expected[path] = (size, digest)
    if list(expected) != sorted(expected, key=lambda value: (value.casefold(), value)):
        raise DesktopReleaseError("desktop_manifest_files_not_canonical")
    metadata_template.update(
        {
            "limits": {
                "max_files": HARD_MAX_FILES,
                "max_file_bytes": HARD_MAX_FILE_BYTES,
                "max_total_bytes": HARD_MAX_TOTAL_BYTES,
            },
            "expected_files": list(expected),
        }
    )
    _validate_template(metadata_template)
    limits = PayloadLimits(HARD_MAX_FILES, HARD_MAX_FILE_BYTES, HARD_MAX_TOTAL_BYTES)
    actual = _inventory_payload(_assert_root_is_safe(input_dir), limits)
    if actual != expected:
        raise DesktopReleaseError("desktop_payload_integrity_invalid")
    if manifest["total_size"] != sum(size for size, _digest in actual.values()):
        raise DesktopReleaseError("desktop_manifest_total_size_invalid")
    if manifest_path.read_bytes() != _canonical_json(manifest):
        raise DesktopReleaseError("desktop_manifest_not_canonical")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--input-dir", type=Path, required=True)
    build.add_argument("--output-manifest", type=Path, required=True)
    build.add_argument("--template", type=Path, required=True)
    build.add_argument("--source-commit", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--input-dir", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "build":
            result = build_desktop_release(
                args.input_dir,
                args.output_manifest,
                source_commit=args.source_commit,
                template_path=args.template,
            )
        else:
            result = verify_desktop_release(args.input_dir, args.manifest)
    except (DesktopReleaseError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
