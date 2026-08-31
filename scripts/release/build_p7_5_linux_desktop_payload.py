"""Build a closed, unsigned Linux desktop runtime staging tree.

The builder consumes already-built Linux inputs. It never downloads tools,
reads repository secrets, signs artifacts, or overwrites an existing output.
The resulting directory is an engineering payload for the Linux AppDir
packager; it is not a distribution package or a P3.4 Runner proof.
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
RUNTIME_ENTRYPOINT = "node/node"
RUNTIME_HOST_SCRIPT = "omnibase-runtime-host.mjs"
TRUST_TOKEN = "__OMNIBASE_RUNTIME_MANIFEST_SHA256__"
P7_SANDBOX_HELPER_SOURCE = "desktop-build/prebuilt-dist/runtime/p34-sandbox-helper.js"
P7_SANDBOX_HELPER_TARGET = "component-host/p34-sandbox-helper.js"
MAX_RUNTIME_FILES = 4096
MAX_STAGING_FILES = 8192
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_RUNTIME_BYTES = 8 * 1024 * 1024 * 1024
MAX_STAGING_BYTES = 12 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 1024 * 1024
SHA256 = re.compile(r"^[0-9a-f]{64}$")
VERSION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$")
FORBIDDEN_SUFFIXES = frozenset(
    {
        ".db",
        ".env",
        ".key",
        ".pem",
        ".p12",
        ".pfx",
        ".sqlite",
        ".sqlite3",
        ".vhd",
        ".vhdx",
        ".vmdk",
    }
)


class LinuxPayloadError(ValueError):
    """A stable, path-redacted Linux payload failure."""


class Identity(NamedTuple):
    device: int
    inode: int
    links: int
    size: int
    modified_ns: int
    mode: int


class SourceArtifact(NamedTuple):
    source: Path
    target: str
    identity: Identity


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


def _identity(metadata: os.stat_result) -> Identity:
    return Identity(
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_nlink,
        metadata.st_size,
        metadata.st_mtime_ns,
        stat.S_IMODE(metadata.st_mode),
    )


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _ordinary_directory(path: Path, *, code: str) -> Path:
    absolute = path.absolute()
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise LinuxPayloadError(code) from exc
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or not _same_path(absolute, resolved)
    ):
        raise LinuxPayloadError(code)
    return absolute


def _validate_relative(raw: str) -> str:
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw) > 240
        or raw.startswith("/")
        or "\\" in raw
        or "\x00" in raw
    ):
        raise LinuxPayloadError("linux_payload_path_invalid")
    parsed = PurePosixPath(raw)
    if (
        parsed.is_absolute()
        or str(parsed) != raw
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise LinuxPayloadError("linux_payload_path_invalid")
    for part in parsed.parts:
        if len(part) > 100 or part.endswith("/"):
            raise LinuxPayloadError("linux_payload_path_invalid")
        folded = part.casefold()
        if (
            folded == ".env"
            or folded.startswith(".env.")
            or folded in {"id_rsa", "id_ed25519"}
            or PurePosixPath(part).suffix.casefold() in FORBIDDEN_SUFFIXES
        ):
            raise LinuxPayloadError("linux_payload_sensitive_path_forbidden")
    return raw


def _join(prefix: str, relative: str) -> str:
    return _validate_relative(
        f"{_validate_relative(prefix)}/{_validate_relative(relative)}"
    )


def _assert_executable(identity: Identity, code: str) -> None:
    if identity.mode & 0o111 == 0:
        raise LinuxPayloadError(code)


def _single_file(
    source: Path, *, target: str, executable: bool = False, allow_empty: bool = True
) -> SourceArtifact:
    _validate_relative(target)
    absolute = source.absolute()
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError as exc:
        raise LinuxPayloadError("linux_payload_source_file_unavailable") from exc
    identity = _identity(metadata)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_nlink != 1
        or not _same_path(absolute, resolved)
    ):
        raise LinuxPayloadError("linux_payload_source_link_forbidden")
    if (
        metadata.st_size < 0
        or metadata.st_size > MAX_FILE_BYTES
        or (metadata.st_size == 0 and not allow_empty)
    ):
        raise LinuxPayloadError("linux_payload_source_file_size_invalid")
    if executable:
        _assert_executable(identity, "linux_payload_source_executable_bit_missing")
    return SourceArtifact(absolute, target, identity)


def _scan_tree(root: Path, *, prefix: str) -> list[SourceArtifact]:
    safe_root = _ordinary_directory(root, code="linux_payload_source_root_invalid")
    artifacts: list[SourceArtifact] = []

    def walk(directory: Path, parts: tuple[str, ...]) -> None:
        try:
            entries = sorted(
                os.scandir(directory),
                key=lambda item: (item.name.casefold(), item.name),
            )
        except OSError as exc:
            raise LinuxPayloadError("linux_payload_source_scan_failed") from exc
        for entry in entries:
            relative = "/".join((*parts, entry.name))
            _validate_relative(relative)
            target = (
                _validate_relative(relative) if not prefix else _join(prefix, relative)
            )
            try:
                metadata = os.stat(entry.path, follow_symlinks=False)
                resolved = Path(entry.path).resolve(strict=True)
            except OSError as exc:
                raise LinuxPayloadError(
                    "linux_payload_source_identity_unavailable"
                ) from exc
            if (
                stat.S_ISDIR(metadata.st_mode)
                and not stat.S_ISLNK(metadata.st_mode)
                and _same_path(Path(entry.path), resolved)
            ):
                walk(Path(entry.path), (*parts, entry.name))
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_nlink != 1
                or not _same_path(Path(entry.path), resolved)
            ):
                raise LinuxPayloadError("linux_payload_source_link_forbidden")
            if metadata.st_size > MAX_FILE_BYTES:
                raise LinuxPayloadError("linux_payload_source_file_size_invalid")
            artifacts.append(
                SourceArtifact(Path(entry.path), target, _identity(metadata))
            )

    walk(safe_root, ())
    return artifacts


def _check_targets(
    artifacts: list[SourceArtifact], *, maximum: int, total_max: int
) -> None:
    if not artifacts or len(artifacts) > maximum:
        raise LinuxPayloadError("linux_payload_file_count_invalid")
    targets = set()
    total = 0
    for artifact in artifacts:
        target = _validate_relative(artifact.target)
        if target in targets:
            raise LinuxPayloadError("linux_payload_duplicate_target")
        targets.add(target)
        total += artifact.identity.size
        if total > total_max:
            raise LinuxPayloadError("linux_payload_total_size_invalid")


def _read_bound(artifact: SourceArtifact, *, maximum: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(artifact.source, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as handle:
            if _identity(os.fstat(handle.fileno())) != artifact.identity:
                raise LinuxPayloadError("linux_payload_source_changed_during_build")
            raw = handle.read(maximum + 1)
            if (
                len(raw) != artifact.identity.size
                or len(raw) > maximum
                or _identity(os.fstat(handle.fileno())) != artifact.identity
            ):
                raise LinuxPayloadError("linux_payload_source_changed_during_build")
            return raw
    except OSError as exc:
        raise LinuxPayloadError("linux_payload_source_read_failed") from exc


def _copy_bound(artifact: SourceArtifact, root: Path) -> CopiedArtifact:
    target = root.joinpath(*PurePosixPath(artifact.target).parts)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    digest = hashlib.sha256()
    copied = 0
    try:
        descriptor = os.open(artifact.source, flags)
        with (
            os.fdopen(descriptor, "rb", closefd=True) as source,
            target.open("xb") as output,
        ):
            if _identity(os.fstat(source.fileno())) != artifact.identity:
                raise LinuxPayloadError("linux_payload_source_changed_during_build")
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > artifact.identity.size:
                    raise LinuxPayloadError("linux_payload_source_changed_during_build")
                digest.update(chunk)
                output.write(chunk)
            if (
                _identity(os.fstat(source.fileno())) != artifact.identity
                or copied != artifact.identity.size
            ):
                raise LinuxPayloadError("linux_payload_source_changed_during_build")
        os.chmod(target, artifact.identity.mode)
    except FileExistsError as exc:
        raise LinuxPayloadError("linux_payload_destination_collision") from exc
    except OSError as exc:
        raise LinuxPayloadError("linux_payload_copy_failed") from exc
    return CopiedArtifact(artifact.target, copied, digest.hexdigest())


def _write_exclusive(path: Path, raw: bytes, *, mode: int = 0o644) -> CopiedArtifact:
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise LinuxPayloadError("linux_payload_generated_file_size_invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as handle:
            handle.write(raw)
        os.chmod(path, mode)
    except FileExistsError as exc:
        raise LinuxPayloadError("linux_payload_destination_collision") from exc
    return CopiedArtifact("", len(raw), hashlib.sha256(raw).hexdigest())


def _select(artifacts: list[SourceArtifact], target: str, label: str) -> SourceArtifact:
    matches = [item for item in artifacts if item.target == target]
    if len(matches) != 1:
        raise LinuxPayloadError(f"linux_payload_{label}_missing")
    return matches[0]


def _copy_project(project_dir: Path, staging: Path, manifest_sha256: str) -> None:
    project = _ordinary_directory(
        project_dir, code="linux_payload_desktop_project_invalid"
    )
    selected: list[SourceArtifact] = []
    for name in (
        "package.json",
        "pnpm-lock.yaml",
        "tsconfig.json",
        "tsconfig.build.json",
    ):
        selected.append(
            _single_file(
                project / name,
                target=f"desktop-build/project/{name}",
                allow_empty=False,
            )
        )
    selected.extend(_scan_tree(project / "src", prefix="desktop-build/project/src"))
    _check_targets(selected, maximum=MAX_STAGING_FILES, total_max=MAX_STAGING_BYTES)
    trusted = _select(
        selected,
        "desktop-build/project/src/runtime/trusted-manifest.ts",
        "trusted_manifest_source",
    )
    for artifact in selected:
        target = staging.joinpath(*PurePosixPath(artifact.target).parts)
        if artifact is not trusted:
            _copy_bound(artifact, staging)
            continue
        text = _read_bound(artifact, maximum=MAX_MANIFEST_BYTES).decode("utf-8")
        if text.count(TRUST_TOKEN) != 1 or SHA256.fullmatch(manifest_sha256) is None:
            raise LinuxPayloadError(
                "linux_payload_trusted_manifest_placeholder_invalid"
            )
        _write_exclusive(
            target, text.replace(TRUST_TOKEN, manifest_sha256).encode("utf-8")
        )


def _runtime_host_config(
    *,
    backend: CopiedArtifact,
    node: CopiedArtifact,
    frontend: CopiedArtifact,
    application_version: str,
    backend_port: int,
    frontend_port: int,
    startup_timeout_ms: int,
    shutdown_timeout_ms: int,
) -> dict[str, Any]:
    if VERSION.fullmatch(application_version) is None:
        raise LinuxPayloadError("linux_payload_application_version_invalid")
    if (
        not 1024 <= backend_port <= 65535
        or not 1024 <= frontend_port <= 65535
        or backend_port == frontend_port
    ):
        raise LinuxPayloadError("linux_payload_runtime_port_invalid")
    if not 1_000 <= startup_timeout_ms <= 120_000 or startup_timeout_ms % 1_000 != 0:
        raise LinuxPayloadError("linux_payload_startup_timeout_invalid")
    if not 1_000 <= shutdown_timeout_ms <= 30_000 or shutdown_timeout_ms % 1_000 != 0:
        raise LinuxPayloadError("linux_payload_shutdown_timeout_invalid")
    return {
        "schema_version": RUNTIME_HOST_SCHEMA_VERSION,
        "backend": {"path": backend.target, "sha256": backend.sha256},
        "frontend": {"path": frontend.target, "sha256": frontend.sha256},
        "node": {"path": node.target, "sha256": node.sha256},
        "application_version": application_version,
        "backend_port": backend_port,
        "frontend_port": frontend_port,
        "startup_timeout_seconds": startup_timeout_ms // 1_000,
        "shutdown_timeout_seconds": shutdown_timeout_ms // 1_000,
    }


def _runtime_manifest(files: list[CopiedArtifact]) -> dict[str, Any]:
    ordered = sorted(files, key=lambda item: item.target)
    if not ordered or len(ordered) > MAX_RUNTIME_FILES:
        raise LinuxPayloadError("linux_payload_runtime_file_count_invalid")
    if sum(item.size for item in ordered) > MAX_RUNTIME_BYTES or len(
        {item.target for item in ordered}
    ) != len(ordered):
        raise LinuxPayloadError("linux_payload_runtime_inventory_invalid")
    return {
        "schemaVersion": RUNTIME_SCHEMA_VERSION,
        "entrypoint": {"path": RUNTIME_ENTRYPOINT, "args": [RUNTIME_HOST_SCRIPT]},
        "files": [
            {"path": item.target, "size": item.size, "sha256": item.sha256}
            for item in ordered
        ],
    }


def validate_component_bundle(path: Path) -> dict[str, object]:
    exporter_path = Path(__file__).with_name("export_p7_3_component_bundles.py")
    spec = importlib.util.spec_from_file_location(
        "omnibase_p73_bundle_validator", exporter_path
    )
    if spec is None or spec.loader is None:
        raise LinuxPayloadError("linux_payload_component_bundle_invalid")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        report = module.validate_component_bundle(path)
    except Exception as exc:
        raise LinuxPayloadError("linux_payload_component_bundle_invalid") from exc
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
        raise LinuxPayloadError("linux_payload_component_bundle_invalid")
    return report


def _verify_runtime(root: Path, manifest: dict[str, Any], raw: bytes) -> None:
    actual = _scan_tree(root, prefix="")
    actual_targets = {item.target for item in actual}
    declared = {item["path"] for item in manifest["files"]}
    if actual_targets != declared | {"runtime-manifest.json"}:
        raise LinuxPayloadError("linux_payload_runtime_closed_set_drifted")
    if (root / "runtime-manifest.json").read_bytes() != raw:
        raise LinuxPayloadError("linux_payload_runtime_manifest_changed")
    for item in manifest["files"]:
        path = root.joinpath(*PurePosixPath(item["path"]).parts)
        if (
            not path.is_file()
            or path.stat().st_size != item["size"]
            or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]
        ):
            raise LinuxPayloadError("linux_payload_runtime_integrity_invalid")


def build_linux_payload(
    *,
    frontend_standalone_dir: Path,
    frontend_static_dir: Path,
    frontend_public_dir: Path,
    desktop_dist_dir: Path,
    runtime_host_script: Path,
    backend_executable: Path,
    node_executable: Path,
    desktop_project_dir: Path,
    output_dir: Path,
    component_bundle_dir: Path | None = None,
    application_version: str = "1.0.0",
    backend_port: int = 8765,
    frontend_port: int = 3000,
    startup_timeout_ms: int = 60_000,
    shutdown_timeout_ms: int = 10_000,
) -> BuildResult:
    output = output_dir.absolute()
    if output.exists():
        raise LinuxPayloadError("linux_payload_output_exists")
    parent = _ordinary_directory(
        output.parent, code="linux_payload_output_parent_invalid"
    )
    sources = [
        frontend_standalone_dir,
        frontend_static_dir,
        frontend_public_dir,
        desktop_dist_dir,
        runtime_host_script,
        backend_executable,
        node_executable,
        desktop_project_dir,
    ]
    if component_bundle_dir is not None:
        sources.append(component_bundle_dir)
    for source in sources:
        source_abs = source.absolute()
        try:
            output.relative_to(source_abs)
        except ValueError:
            continue
        raise LinuxPayloadError("linux_payload_output_inside_source")

    host = _single_file(
        runtime_host_script, target=RUNTIME_HOST_SCRIPT, allow_empty=False
    )
    backend = _single_file(
        backend_executable,
        target="backend/omnibase-desktop-backend",
        executable=True,
        allow_empty=False,
    )
    node = _single_file(
        node_executable, target="node/node", executable=True, allow_empty=False
    )
    frontend = _scan_tree(frontend_standalone_dir, prefix="frontend")
    frontend_static = _scan_tree(frontend_static_dir, prefix="frontend/.next/static")
    frontend_public = _scan_tree(frontend_public_dir, prefix="frontend/public")
    desktop_dist = _scan_tree(desktop_dist_dir, prefix="desktop-build/prebuilt-dist")
    helper = _select(desktop_dist, P7_SANDBOX_HELPER_SOURCE, "p7_sandbox_helper")
    helper = SourceArtifact(helper.source, P7_SANDBOX_HELPER_TARGET, helper.identity)
    component_report = (
        None
        if component_bundle_dir is None
        else validate_component_bundle(component_bundle_dir)
    )
    components = (
        []
        if component_bundle_dir is None
        else _scan_tree(component_bundle_dir, prefix="components")
    )
    runtime_sources = [
        host,
        backend,
        node,
        *frontend,
        *frontend_static,
        *frontend_public,
        *components,
        helper,
    ]
    _check_targets(
        runtime_sources, maximum=MAX_RUNTIME_FILES - 2, total_max=MAX_RUNTIME_BYTES
    )
    _check_targets(
        [*runtime_sources, *desktop_dist],
        maximum=MAX_STAGING_FILES,
        total_max=MAX_STAGING_BYTES,
    )
    _select(frontend, "frontend/server.js", "frontend_entrypoint")
    _select(desktop_dist, "desktop-build/prebuilt-dist/main.js", "desktop_entrypoint")

    staging = parent / f".{output.name}.staging-{os.getpid()}-{uuid.uuid4().hex}"
    staging.mkdir()
    try:
        copied: list[CopiedArtifact] = [
            _copy_bound(item, staging / "runtime") for item in runtime_sources
        ]
        for item in desktop_dist:
            _copy_bound(item, staging)
        by_target = {item.target: item for item in copied}
        config = _runtime_host_config(
            backend=by_target[backend.target],
            node=by_target[node.target],
            frontend=by_target["frontend/server.js"],
            application_version=application_version,
            backend_port=backend_port,
            frontend_port=frontend_port,
            startup_timeout_ms=startup_timeout_ms,
            shutdown_timeout_ms=shutdown_timeout_ms,
        )
        config_copy = _write_exclusive(
            staging / "runtime/runtime-host.json", _canonical_json(config)
        )
        copied.append(
            CopiedArtifact("runtime-host.json", config_copy.size, config_copy.sha256)
        )
        manifest = _runtime_manifest(copied)
        manifest_raw = _canonical_json(manifest)
        manifest_copy = _write_exclusive(
            staging / "runtime/runtime-manifest.json", manifest_raw
        )
        _copy_project(desktop_project_dir, staging, manifest_copy.sha256)
        _verify_runtime(staging / "runtime", manifest, manifest_raw)
        if component_report is not None:
            copied_report = validate_component_bundle(staging / "runtime/components")
            if copied_report != component_report:
                raise LinuxPayloadError("linux_payload_component_bundle_changed")
        if output.exists():
            raise LinuxPayloadError("linux_payload_output_raced")
        staging.rename(output)
    except Exception:
        # Retain failed staging trees for explicit inspection.
        raise
    return BuildResult(
        output,
        manifest_copy.sha256,
        len(manifest["files"]),
        sum(item["size"] for item in manifest["files"]),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frontend-standalone-dir", type=Path, required=True)
    parser.add_argument("--frontend-static-dir", type=Path, required=True)
    parser.add_argument("--frontend-public-dir", type=Path, required=True)
    parser.add_argument("--desktop-dist-dir", type=Path, required=True)
    parser.add_argument("--runtime-host-script", type=Path, required=True)
    parser.add_argument("--backend-executable", type=Path, required=True)
    parser.add_argument("--node-executable", type=Path, required=True)
    parser.add_argument("--desktop-project-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--component-bundle-dir", type=Path)
    parser.add_argument("--application-version", default="1.0.0")
    parser.add_argument("--backend-port", type=int, default=8765)
    parser.add_argument("--frontend-port", type=int, default=3000)
    parser.add_argument("--startup-timeout-ms", type=int, default=60_000)
    parser.add_argument("--shutdown-timeout-ms", type=int, default=10_000)
    args = parser.parse_args()
    try:
        result = build_linux_payload(**vars(args))
    except (LinuxPayloadError, OSError, UnicodeError) as exc:
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
