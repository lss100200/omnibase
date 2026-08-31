"""Assemble the deterministic, fail-closed P6.5 desktop payload staging tree.

The builder consumes already-built, offline inputs.  It does not run package
managers, download runtimes, sign binaries, install software, or read
configuration from ``.env``.  The output has two deliberately separate parts:

* ``runtime/`` is the exact bundle verified and launched by Electron; and
* ``desktop-build/`` is a copied Electron compilation input whose copied
  ``trusted-manifest.ts`` contains the SHA-256 of ``runtime-manifest.json``.

The repository source placeholder is never modified.  An output path must not
exist, all sources and parents must be ordinary non-reparse filesystem objects,
and a failed build never replaces an existing path.  Failed staging trees are
retained for explicit inspection instead of being recursively deleted by this
tool.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

RUNTIME_SCHEMA_VERSION = 1
RUNTIME_HOST_SCHEMA_VERSION = 1
RUNTIME_ENTRYPOINT = "OmniBase.RuntimeHost.exe"
P7_SANDBOX_HELPER_SOURCE = "desktop-build/prebuilt-dist/runtime/p34-sandbox-helper.js"
P7_SANDBOX_HELPER_TARGET = "component-host/p34-sandbox-helper.js"
TRUST_TOKEN = "__OMNIBASE_RUNTIME_MANIFEST_SHA256__"
MAX_RUNTIME_FILES = 4096
MAX_STAGING_FILES = 8192
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_RUNTIME_BYTES = 8 * 1024 * 1024 * 1024
MAX_STAGING_BYTES = 12 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_APPLICATION_VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$")
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
_WINDOWS_RESERVED = frozenset(
    {
        "aux",
        "con",
        "nul",
        "prn",
        *(f"com{index}" for index in range(1, 10)),
        *(f"lpt{index}" for index in range(1, 10)),
    }
)


class DesktopPayloadError(ValueError):
    """A deterministic, path-redacted desktop payload build failure."""


class FileIdentity(NamedTuple):
    device: int
    inode: int
    links: int
    size: int
    modified_ns: int


class SourceArtifact(NamedTuple):
    source: Path
    target: str
    identity: FileIdentity


class CopiedArtifact(NamedTuple):
    target: str
    size: int
    sha256: str


class BuildResult(NamedTuple):
    output_dir: Path
    runtime_manifest_sha256: str
    runtime_file_count: int
    runtime_total_bytes: int


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)


def _is_regular_non_reparse(metadata: os.stat_result) -> bool:
    return (
        stat.S_ISREG(metadata.st_mode)
        and not stat.S_ISLNK(metadata.st_mode)
        and not _is_reparse(metadata)
    )


def _identity(metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        links=metadata.st_nlink,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
    )


def _same_physical_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _assert_safe_directory(path: Path, *, label: str) -> Path:
    absolute = path.absolute()
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise DesktopPayloadError(f"desktop_payload_{label}_unavailable") from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or not _same_physical_path(absolute, resolved)
    ):
        raise DesktopPayloadError(f"desktop_payload_{label}_identity_invalid")
    return absolute


def _validate_relative_path(raw: str) -> str:
    if not raw or len(raw) > 240 or raw.startswith("/") or "\\" in raw or "\x00" in raw:
        raise DesktopPayloadError("desktop_payload_path_invalid")
    parsed = PurePosixPath(raw)
    if (
        parsed.is_absolute()
        or str(parsed) != raw
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise DesktopPayloadError("desktop_payload_path_invalid")
    for part in parsed.parts:
        if (
            len(part) > 100
            or part.endswith((" ", "."))
            or any(ord(character) < 32 for character in part)
            or any(character in '<>:"|?*' for character in part)
        ):
            raise DesktopPayloadError("desktop_payload_path_invalid")
        stem = part.split(".", 1)[0].casefold()
        if stem in _WINDOWS_RESERVED:
            raise DesktopPayloadError("desktop_payload_path_invalid")
        folded = part.casefold()
        if (
            folded == ".env"
            or folded.startswith(".env.")
            or folded.endswith(".env")
            or ".env." in folded
            or folded in {"id_rsa", "id_ed25519"}
            or PurePosixPath(part).suffix.casefold() in _FORBIDDEN_SUFFIXES
        ):
            raise DesktopPayloadError("desktop_payload_sensitive_path_forbidden")
    return raw


def _join_target(prefix: str, relative: str) -> str:
    prefix_value = _validate_relative_path(prefix)
    relative_value = _validate_relative_path(relative)
    return _validate_relative_path(f"{prefix_value}/{relative_value}")


def _scan_tree(root: Path, *, target_prefix: str) -> list[SourceArtifact]:
    safe_root = _assert_safe_directory(root, label="source_root")
    artifacts: list[SourceArtifact] = []

    def walk(directory: Path, parts: tuple[str, ...]) -> None:
        try:
            entries = sorted(
                os.scandir(directory),
                key=lambda entry: (entry.name.casefold(), entry.name),
            )
        except OSError as exc:
            raise DesktopPayloadError("desktop_payload_source_scan_failed") from exc
        for entry in entries:
            relative = "/".join((*parts, entry.name))
            _validate_relative_path(relative)
            try:
                # On Windows ``DirEntry.stat()`` may report zero dev/inode even
                # though ``fstat()`` returns the stable file identity.  Use the
                # same ``os.stat`` family as the later handle-binding check.
                metadata = os.stat(entry.path, follow_symlinks=False)
            except OSError as exc:
                raise DesktopPayloadError(
                    "desktop_payload_source_identity_unavailable"
                ) from exc
            if (
                stat.S_ISDIR(metadata.st_mode)
                and not entry.is_symlink()
                and not _is_reparse(metadata)
            ):
                walk(Path(entry.path), (*parts, entry.name))
                continue
            if not _is_regular_non_reparse(metadata) or metadata.st_nlink != 1:
                raise DesktopPayloadError(
                    "desktop_payload_source_link_or_reparse_forbidden"
                )
            if metadata.st_size < 0 or metadata.st_size > MAX_FILE_BYTES:
                raise DesktopPayloadError("desktop_payload_source_file_size_invalid")
            artifacts.append(
                SourceArtifact(
                    source=Path(entry.path),
                    target=_join_target(target_prefix, relative),
                    identity=_identity(metadata),
                )
            )

    walk(safe_root, ())
    return artifacts


def _single_file(
    source: Path, *, target: str, allow_empty: bool = False
) -> SourceArtifact:
    _validate_relative_path(target)
    # Validate the source basename before opening it so an explicitly supplied
    # ``.env``/key/database file cannot be disguised by a safe destination.
    _validate_relative_path(source.name)
    absolute = source.absolute()
    _assert_safe_directory(absolute.parent, label="source_parent")
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise DesktopPayloadError("desktop_payload_source_file_unavailable") from exc
    if (
        not _is_regular_non_reparse(metadata)
        or metadata.st_nlink != 1
        or not _same_physical_path(absolute, resolved)
    ):
        raise DesktopPayloadError("desktop_payload_source_link_or_reparse_forbidden")
    if (
        metadata.st_size < 0
        or (metadata.st_size == 0 and not allow_empty)
        or metadata.st_size > MAX_FILE_BYTES
    ):
        raise DesktopPayloadError("desktop_payload_source_file_size_invalid")
    return SourceArtifact(absolute, target, _identity(metadata))


def _read_bound(artifact: SourceArtifact, *, max_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(artifact.source, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            opened = os.fstat(source.fileno())
            if (
                _identity(opened) != artifact.identity
                or not stat.S_ISREG(opened.st_mode)
                or opened.st_size > max_bytes
            ):
                raise DesktopPayloadError("desktop_payload_source_changed_during_build")
            raw = source.read(max_bytes + 1)
            if len(raw) != artifact.identity.size or len(raw) > max_bytes:
                raise DesktopPayloadError("desktop_payload_source_changed_during_build")
            if _identity(os.fstat(source.fileno())) != artifact.identity:
                raise DesktopPayloadError("desktop_payload_source_changed_during_build")
            return raw
    except OSError as exc:
        raise DesktopPayloadError("desktop_payload_source_read_failed") from exc


def _select_tree_file(
    artifacts: list[SourceArtifact], *, target: str, label: str
) -> SourceArtifact:
    folded = target.casefold()
    matches = [
        artifact for artifact in artifacts if artifact.target.casefold() == folded
    ]
    if len(matches) != 1 or matches[0].target != target:
        raise DesktopPayloadError(f"desktop_payload_{label}_missing")
    if matches[0].identity.size <= 0:
        raise DesktopPayloadError(f"desktop_payload_{label}_empty")
    return matches[0]


def _check_closed_targets(artifacts: list[SourceArtifact], *, ceiling: int) -> None:
    if not artifacts or len(artifacts) > ceiling:
        raise DesktopPayloadError("desktop_payload_file_count_invalid")
    folded: set[str] = set()
    total = 0
    for artifact in artifacts:
        target = _validate_relative_path(artifact.target)
        target_folded = target.casefold()
        if target_folded in folded:
            raise DesktopPayloadError("desktop_payload_duplicate_target")
        folded.add(target_folded)
        total += artifact.identity.size
        if total > MAX_STAGING_BYTES:
            raise DesktopPayloadError("desktop_payload_total_size_invalid")


def _copy_bound(artifact: SourceArtifact, staging: Path) -> CopiedArtifact:
    target = staging.joinpath(*PurePosixPath(artifact.target).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    copied = 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(artifact.source, flags)
        with (
            os.fdopen(descriptor, "rb", closefd=True) as source,
            target.open("xb") as output,
        ):
            opened_identity = _identity(os.fstat(source.fileno()))
            if opened_identity != artifact.identity or not stat.S_ISREG(
                os.fstat(source.fileno()).st_mode
            ):
                raise DesktopPayloadError("desktop_payload_source_changed_during_build")
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > artifact.identity.size:
                    raise DesktopPayloadError(
                        "desktop_payload_source_changed_during_build"
                    )
                digest.update(chunk)
                output.write(chunk)
            if _identity(os.fstat(source.fileno())) != artifact.identity:
                raise DesktopPayloadError("desktop_payload_source_changed_during_build")
    except FileExistsError as exc:
        raise DesktopPayloadError("desktop_payload_destination_collision") from exc
    except OSError as exc:
        raise DesktopPayloadError("desktop_payload_copy_failed") from exc
    if copied != artifact.identity.size:
        raise DesktopPayloadError("desktop_payload_source_changed_during_build")
    return CopiedArtifact(artifact.target, copied, digest.hexdigest())


def _write_exclusive(path: Path, raw: bytes) -> CopiedArtifact:
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise DesktopPayloadError("desktop_payload_generated_file_size_invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
    except FileExistsError as exc:
        raise DesktopPayloadError("desktop_payload_destination_collision") from exc
    return CopiedArtifact("", len(raw), hashlib.sha256(raw).hexdigest())


def _digest_bound_file(path: Path, *, target: str) -> CopiedArtifact:
    # Empty package marker modules are valid runtime files (for example,
    # client-only/index.js in a Next standalone tree). Required entrypoints
    # still use the non-empty default at their original trust boundary.
    artifact = _single_file(path, target=target, allow_empty=True)
    digest = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(artifact.source, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            if _identity(os.fstat(source.fileno())) != artifact.identity:
                raise DesktopPayloadError("desktop_payload_staging_changed")
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise DesktopPayloadError(
                        "desktop_payload_staging_file_size_invalid"
                    )
                digest.update(chunk)
            if _identity(os.fstat(source.fileno())) != artifact.identity:
                raise DesktopPayloadError("desktop_payload_staging_changed")
    except OSError as exc:
        raise DesktopPayloadError("desktop_payload_staging_read_failed") from exc
    return CopiedArtifact(target, size, digest.hexdigest())


def _verify_final_runtime(
    runtime_root: Path,
    *,
    expected_manifest: dict[str, Any],
    expected_manifest_raw: bytes,
) -> None:
    manifest_path = runtime_root / "runtime-manifest.json"
    manifest_artifact = _single_file(manifest_path, target="runtime-manifest.json")
    manifest_raw = _read_bound(manifest_artifact, max_bytes=MAX_MANIFEST_BYTES)
    if manifest_raw != expected_manifest_raw:
        raise DesktopPayloadError("desktop_payload_runtime_manifest_changed")
    try:
        parsed = json.loads(manifest_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DesktopPayloadError("desktop_payload_runtime_manifest_invalid") from exc
    if parsed != expected_manifest or manifest_raw != _canonical_json(parsed):
        raise DesktopPayloadError("desktop_payload_runtime_manifest_invalid")

    final_runtime = _scan_tree(runtime_root, target_prefix="runtime")
    final_targets = {
        artifact.target.removeprefix("runtime/") for artifact in final_runtime
    }
    declared = {item["path"]: item for item in expected_manifest["files"]}
    if final_targets != set(declared) | {"runtime-manifest.json"}:
        raise DesktopPayloadError("desktop_payload_runtime_closed_set_drifted")
    for relative, item in declared.items():
        actual = _digest_bound_file(
            runtime_root.joinpath(*PurePosixPath(relative).parts), target=relative
        )
        if actual.size != item["size"] or actual.sha256 != item["sha256"]:
            raise DesktopPayloadError("desktop_payload_runtime_integrity_invalid")


def _copy_desktop_project(
    desktop_project_dir: Path,
    staging: Path,
    *,
    manifest_sha256: str,
) -> list[CopiedArtifact]:
    project = _assert_safe_directory(desktop_project_dir, label="desktop_project")
    selected: list[SourceArtifact] = []
    for name in (
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
        "tsconfig.build.json",
    ):
        selected.append(
            _single_file(project / name, target=f"desktop-build/project/{name}")
        )
    selected.extend(
        _scan_tree(project / "src", target_prefix="desktop-build/project/src")
    )
    _check_closed_targets(selected, ceiling=MAX_STAGING_FILES)
    trusted_target = "desktop-build/project/src/runtime/trusted-manifest.ts"
    _select_tree_file(selected, target=trusted_target, label="trusted_manifest_source")
    copied: list[CopiedArtifact] = []
    for artifact in selected:
        if artifact.target != trusted_target:
            copied.append(_copy_bound(artifact, staging))
            continue
        raw = _read_bound(artifact, max_bytes=MAX_MANIFEST_BYTES)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DesktopPayloadError(
                "desktop_payload_trusted_manifest_encoding_invalid"
            ) from exc
        if text.count(TRUST_TOKEN) != 1 or _SHA256.fullmatch(manifest_sha256) is None:
            raise DesktopPayloadError(
                "desktop_payload_trusted_manifest_placeholder_invalid"
            )
        rendered = text.replace(TRUST_TOKEN, manifest_sha256).encode("utf-8")
        target = staging.joinpath(*PurePosixPath(trusted_target).parts)
        generated = _write_exclusive(target, rendered)
        copied.append(CopiedArtifact(trusted_target, generated.size, generated.sha256))
    return copied


def _runtime_host_config(
    *,
    backend: CopiedArtifact,
    node: CopiedArtifact,
    frontend: CopiedArtifact,
    backend_port: int,
    frontend_port: int,
    application_version: str,
    startup_timeout_ms: int,
    shutdown_timeout_ms: int,
    max_captured_output_bytes: int,
) -> dict[str, Any]:
    if (
        not 1024 <= backend_port <= 65535
        or not 1024 <= frontend_port <= 65535
        or backend_port == frontend_port
    ):
        raise DesktopPayloadError("desktop_payload_runtime_port_invalid")
    if not 1_000 <= startup_timeout_ms <= 120_000 or startup_timeout_ms % 1_000 != 0:
        raise DesktopPayloadError("desktop_payload_startup_timeout_invalid")
    if not 1_000 <= shutdown_timeout_ms <= 30_000 or shutdown_timeout_ms % 1_000 != 0:
        raise DesktopPayloadError("desktop_payload_shutdown_timeout_invalid")
    if not 4096 <= max_captured_output_bytes <= 1024 * 1024:
        raise DesktopPayloadError("desktop_payload_output_budget_invalid")
    if (
        not isinstance(application_version, str)
        or _APPLICATION_VERSION.fullmatch(application_version) is None
    ):
        raise DesktopPayloadError("desktop_payload_application_version_invalid")
    return {
        "schema_version": RUNTIME_HOST_SCHEMA_VERSION,
        "backend": {"path": backend.target, "sha256": backend.sha256},
        "node": {"path": node.target, "sha256": node.sha256},
        "frontend": {"path": frontend.target, "sha256": frontend.sha256},
        "application_version": application_version,
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "startup_timeout_seconds": startup_timeout_ms // 1_000,
        "shutdown_timeout_seconds": shutdown_timeout_ms // 1_000,
        "per_stream_output_limit_bytes": max_captured_output_bytes,
        "total_output_limit_bytes": max_captured_output_bytes * 2,
    }


def _runtime_manifest(files: list[CopiedArtifact]) -> dict[str, Any]:
    ordered = sorted(files, key=lambda item: (item.target.casefold(), item.target))
    if not ordered or len(ordered) > MAX_RUNTIME_FILES:
        raise DesktopPayloadError("desktop_payload_runtime_file_count_invalid")
    total = sum(item.size for item in ordered)
    if total > MAX_RUNTIME_BYTES:
        raise DesktopPayloadError("desktop_payload_runtime_total_size_invalid")
    if len({item.target.casefold() for item in ordered}) != len(ordered):
        raise DesktopPayloadError("desktop_payload_duplicate_runtime_target")
    return {
        "schemaVersion": RUNTIME_SCHEMA_VERSION,
        "entrypoint": {"path": RUNTIME_ENTRYPOINT, "args": []},
        "files": [
            {"path": item.target, "size": item.size, "sha256": item.sha256}
            for item in ordered
        ],
    }


def _assert_output_is_separate(output: Path, sources: list[Path]) -> None:
    output_absolute = output.absolute()
    for source in sources:
        source_absolute = source.absolute()
        try:
            source_metadata = os.stat(source_absolute, follow_symlinks=False)
        except OSError as exc:
            raise DesktopPayloadError("desktop_payload_source_unavailable") from exc
        if stat.S_ISREG(source_metadata.st_mode):
            if _same_physical_path(output_absolute, source_absolute):
                raise DesktopPayloadError("desktop_payload_output_inside_source")
            continue
        try:
            output_absolute.relative_to(source_absolute)
        except ValueError:
            pass
        else:
            raise DesktopPayloadError("desktop_payload_output_inside_source")


def _validate_component_bundle(path: Path) -> dict[str, object]:
    exporter_path = Path(__file__).with_name("export_p7_3_component_bundles.py")
    spec = importlib.util.spec_from_file_location(
        "omnibase_p73_component_bundle_validator", exporter_path
    )
    if spec is None or spec.loader is None:
        raise DesktopPayloadError("desktop_payload_component_bundle_invalid")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        report = module.validate_component_bundle(path)
    except Exception as exc:
        raise DesktopPayloadError("desktop_payload_component_bundle_invalid") from exc
    if (
        not isinstance(report, dict)
        or set(report)
        != {
            "bundle_sha256",
            "file_count",
            "output_bytes",
            "package_count",
            "tree_sha256",
        }
        or report["package_count"] != 10
    ):
        raise DesktopPayloadError("desktop_payload_component_bundle_invalid")
    return report


def build_desktop_payload(
    *,
    frontend_standalone_dir: Path,
    frontend_static_dir: Path,
    frontend_public_dir: Path,
    desktop_dist_dir: Path,
    runtime_host_publish_dir: Path,
    backend_publish_dir: Path,
    node_executable: Path,
    desktop_project_dir: Path,
    output_dir: Path,
    component_bundle_dir: Path | None = None,
    backend_executable: str = "OmniBase.Desktop.Backend.exe",
    frontend_entry: str = "server.js",
    desktop_entry: str = "main.js",
    application_version: str = "1.0.0",
    backend_port: int = 8765,
    frontend_port: int = 3000,
    startup_timeout_ms: int = 60_000,
    shutdown_timeout_ms: int = 10_000,
    max_captured_output_bytes: int = 128 * 1024,
) -> BuildResult:
    """Build one offline staging tree without overwriting any existing output."""

    output = output_dir.absolute()
    if output.exists():
        raise DesktopPayloadError("desktop_payload_output_exists")
    parent = _assert_safe_directory(output.parent, label="output_parent")
    sources = [
        frontend_standalone_dir,
        frontend_static_dir,
        frontend_public_dir,
        desktop_dist_dir,
        runtime_host_publish_dir,
        backend_publish_dir,
        node_executable,
        desktop_project_dir,
    ]
    if component_bundle_dir is not None:
        sources.append(component_bundle_dir)
    _assert_output_is_separate(output, sources)
    component_report = (
        None
        if component_bundle_dir is None
        else _validate_component_bundle(component_bundle_dir)
    )

    host = _scan_tree(runtime_host_publish_dir, target_prefix="runtime")
    # RuntimeHost is placed at the runtime root, not under a configurable path.
    host = [
        SourceArtifact(
            artifact.source,
            artifact.target.removeprefix("runtime/"),
            artifact.identity,
        )
        for artifact in host
    ]
    backend = _scan_tree(backend_publish_dir, target_prefix="backend")
    frontend = _scan_tree(frontend_standalone_dir, target_prefix="frontend")
    frontend_static = _scan_tree(
        frontend_static_dir, target_prefix="frontend/.next/static"
    )
    frontend_public = _scan_tree(frontend_public_dir, target_prefix="frontend/public")
    node = _single_file(node_executable, target="node/node.exe")
    desktop_dist = _scan_tree(
        desktop_dist_dir, target_prefix="desktop-build/prebuilt-dist"
    )
    sandbox_helper_source = _select_tree_file(
        desktop_dist,
        target=P7_SANDBOX_HELPER_SOURCE,
        label="p7_sandbox_helper",
    )
    sandbox_helper = SourceArtifact(
        sandbox_helper_source.source,
        P7_SANDBOX_HELPER_TARGET,
        sandbox_helper_source.identity,
    )
    components = (
        []
        if component_bundle_dir is None
        else _scan_tree(component_bundle_dir, target_prefix="components")
    )
    runtime_sources = [
        *host,
        *backend,
        *frontend,
        *frontend_static,
        *frontend_public,
        *components,
        sandbox_helper,
        node,
    ]
    all_sources = [*runtime_sources, *desktop_dist]
    _check_closed_targets(runtime_sources, ceiling=MAX_RUNTIME_FILES - 1)
    _check_closed_targets(all_sources, ceiling=MAX_STAGING_FILES)

    host_entry = _select_tree_file(
        host, target=RUNTIME_ENTRYPOINT, label="runtime_host_entrypoint"
    )
    backend_target = _join_target("backend", backend_executable)
    frontend_target = _join_target("frontend", frontend_entry)
    desktop_target = _join_target("desktop-build/prebuilt-dist", desktop_entry)
    backend_entry_artifact = _select_tree_file(
        backend, target=backend_target, label="backend_entrypoint"
    )
    frontend_entry_artifact = _select_tree_file(
        frontend, target=frontend_target, label="frontend_entrypoint"
    )
    _select_tree_file(desktop_dist, target=desktop_target, label="desktop_entrypoint")

    staging = parent / f".{output.name}.staging-{os.getpid()}-{uuid.uuid4().hex}"
    try:
        staging.mkdir()
    except OSError as exc:
        raise DesktopPayloadError("desktop_payload_staging_create_failed") from exc

    runtime_copied: list[CopiedArtifact] = []
    copied_by_target: dict[str, CopiedArtifact] = {}
    for artifact in runtime_sources:
        copied = _copy_bound(artifact, staging / "runtime")
        runtime_copied.append(copied)
        copied_by_target[copied.target] = copied
    for artifact in desktop_dist:
        _copy_bound(artifact, staging)

    backend_copied = copied_by_target[backend_entry_artifact.target]
    frontend_copied = copied_by_target[frontend_entry_artifact.target]
    node_copied = copied_by_target[node.target]
    host_copied = copied_by_target[host_entry.target]
    if host_copied.target != RUNTIME_ENTRYPOINT:
        raise DesktopPayloadError("desktop_payload_runtime_host_entrypoint_invalid")

    host_config = _runtime_host_config(
        backend=backend_copied,
        node=node_copied,
        frontend=frontend_copied,
        backend_port=backend_port,
        frontend_port=frontend_port,
        application_version=application_version,
        startup_timeout_ms=startup_timeout_ms,
        shutdown_timeout_ms=shutdown_timeout_ms,
        max_captured_output_bytes=max_captured_output_bytes,
    )
    host_config_raw = _canonical_json(host_config)
    generated_host = _write_exclusive(
        staging / "runtime/runtime-host.json", host_config_raw
    )
    runtime_copied.append(
        CopiedArtifact("runtime-host.json", generated_host.size, generated_host.sha256)
    )
    manifest = _runtime_manifest(runtime_copied)
    manifest_raw = _canonical_json(manifest)
    generated_manifest = _write_exclusive(
        staging / "runtime/runtime-manifest.json", manifest_raw
    )
    manifest_sha256 = generated_manifest.sha256
    _copy_desktop_project(desktop_project_dir, staging, manifest_sha256=manifest_sha256)

    # Re-inventory and re-digest after every generated desktop build input has
    # been written.  This catches concurrent/tampered staging changes before
    # the directory is made visible at the requested output path.
    _verify_final_runtime(
        staging / "runtime",
        expected_manifest=manifest,
        expected_manifest_raw=manifest_raw,
    )
    if component_report is not None:
        copied_component_report = _validate_component_bundle(
            staging / "runtime/components"
        )
        if copied_component_report != component_report:
            raise DesktopPayloadError("desktop_payload_component_bundle_changed")

    if output.exists():
        raise DesktopPayloadError("desktop_payload_output_raced")
    try:
        staging.rename(output)
    except OSError as exc:
        raise DesktopPayloadError("desktop_payload_publish_failed") from exc
    return BuildResult(
        output_dir=output,
        runtime_manifest_sha256=manifest_sha256,
        runtime_file_count=len(manifest["files"]),
        runtime_total_bytes=sum(item["size"] for item in manifest["files"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend-standalone-dir", type=Path, required=True)
    parser.add_argument("--frontend-static-dir", type=Path, required=True)
    parser.add_argument("--frontend-public-dir", type=Path, required=True)
    parser.add_argument("--desktop-dist-dir", type=Path, required=True)
    parser.add_argument("--runtime-host-publish-dir", type=Path, required=True)
    parser.add_argument("--backend-publish-dir", type=Path, required=True)
    parser.add_argument("--node-executable", type=Path, required=True)
    parser.add_argument("--desktop-project-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--component-bundle-dir", type=Path)
    parser.add_argument("--backend-executable", default="OmniBase.Desktop.Backend.exe")
    parser.add_argument("--frontend-entry", default="server.js")
    parser.add_argument("--desktop-entry", default="main.js")
    parser.add_argument("--application-version", default="1.0.0")
    parser.add_argument("--backend-port", type=int, default=8765)
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--startup-timeout-ms", type=int, default=60_000)
    parser.add_argument("--shutdown-timeout-ms", type=int, default=10_000)
    parser.add_argument("--max-captured-output-bytes", type=int, default=128 * 1024)
    args = parser.parse_args()
    try:
        result = build_desktop_payload(**vars(args))
    except (DesktopPayloadError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "output_dir": str(result.output_dir),
                "runtime_manifest_sha256": result.runtime_manifest_sha256,
                "runtime_file_count": result.runtime_file_count,
                "runtime_total_bytes": result.runtime_total_bytes,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
