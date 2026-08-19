"""Validate and author the closed OmniBase desktop payload for WiX.

The tool never builds or executes the payload. It rejects unsafe filesystem
objects and secret/data-bearing file names before opening file contents, then
optionally emits deterministic WiX components with per-user HKCU key paths.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NamedTuple

WIX_NAMESPACE = "http://wixtoolset.org/schemas/v4/wxs"
COMPONENT_NAMESPACE = uuid.UUID("718b5334-c235-45f4-8621-9349c9c234ff")
REQUIRED_ENTRYPOINT = "OmniBase.exe"
MAX_FILES = 4096
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_RELATIVE_PATH = 240
_REPARSE_FLAG = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_FORBIDDEN_SUFFIXES = frozenset(
    {
        ".db",
        ".env",
        ".key",
        ".p12",
        ".pdb",
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
_SAFE_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")


class PayloadValidationError(ValueError):
    """A stable, path-redacted payload validation failure."""


class FileIdentity(NamedTuple):
    device: int
    inode: int
    links: int
    size: int
    modified_ns: int


@dataclass(frozen=True, slots=True)
class PayloadFile:
    relative_path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PayloadSummary:
    files: tuple[PayloadFile, ...]
    total_bytes: int
    tree_sha256: str

    @property
    def file_count(self) -> int:
        return len(self.files)


def _identity(metadata: os.stat_result) -> FileIdentity:
    return FileIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        links=metadata.st_nlink,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
    )


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_FLAG)


def _same_physical_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left)) == os.path.normcase(str(right))


def _safe_payload_root(payload_root: Path) -> Path:
    if not payload_root.is_absolute():
        raise PayloadValidationError("installer_payload_root_must_be_absolute")
    absolute = payload_root.absolute()
    if absolute == Path(absolute.anchor):
        raise PayloadValidationError("installer_payload_root_must_not_be_volume_root")
    if os.name == "nt" and str(absolute).startswith("\\\\"):
        raise PayloadValidationError("installer_payload_root_must_be_local")
    try:
        metadata = os.stat(absolute, follow_symlinks=False)
        resolved = absolute.resolve(strict=True)
    except OSError:
        raise PayloadValidationError("installer_payload_root_unavailable") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or not _same_physical_path(absolute, resolved)
    ):
        raise PayloadValidationError("installer_payload_root_identity_invalid")
    return absolute


def _validate_relative_path(raw: str) -> str:
    if (
        not raw
        or len(raw) > MAX_RELATIVE_PATH
        or raw.startswith("/")
        or "\\" in raw
        or "\x00" in raw
    ):
        raise PayloadValidationError("installer_payload_path_invalid")
    parsed = PurePosixPath(raw)
    if (
        parsed.is_absolute()
        or str(parsed) != raw
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise PayloadValidationError("installer_payload_path_invalid")
    for part in parsed.parts:
        if (
            len(part) > 100
            or part.endswith((" ", "."))
            or any(ord(character) < 32 for character in part)
            or any(character in '<>:"|?*' for character in part)
        ):
            raise PayloadValidationError("installer_payload_path_invalid")
        stem = part.split(".", 1)[0].casefold()
        if stem in _WINDOWS_RESERVED:
            raise PayloadValidationError("installer_payload_path_invalid")
        folded = part.casefold()
        suffix = PurePosixPath(part).suffix.casefold()
        if (
            folded == ".env"
            or folded.startswith(".env.")
            or folded.endswith(".env")
            or ".env." in folded
            or folded in {"id_ed25519", "id_rsa"}
            or suffix in _FORBIDDEN_SUFFIXES
        ):
            raise PayloadValidationError("installer_payload_sensitive_path_forbidden")
    return raw


def _read_and_digest(path: Path, expected: FileIdentity) -> str:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    digest = hashlib.sha256()
    read_bytes = 0
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb", closefd=True) as source:
            opened = os.fstat(source.fileno())
            if (
                _identity(opened) != expected
                or not stat.S_ISREG(opened.st_mode)
                or _is_reparse(opened)
            ):
                raise PayloadValidationError(
                    "installer_payload_source_changed_during_validation"
                )
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                read_bytes += len(chunk)
                if read_bytes > expected.size or read_bytes > MAX_FILE_BYTES:
                    raise PayloadValidationError("installer_payload_file_size_invalid")
                digest.update(chunk)
            if _identity(os.fstat(source.fileno())) != expected:
                raise PayloadValidationError(
                    "installer_payload_source_changed_during_validation"
                )
    except PayloadValidationError:
        raise
    except OSError:
        raise PayloadValidationError("installer_payload_source_read_failed") from None
    if read_bytes != expected.size:
        raise PayloadValidationError(
            "installer_payload_source_changed_during_validation"
        )
    return digest.hexdigest()


def validate_payload(payload_root: Path) -> PayloadSummary:
    """Return a deterministic payload inventory without exposing physical paths."""

    root = _safe_payload_root(payload_root)
    inventory: list[PayloadFile] = []
    folded_paths: set[str] = set()
    total_bytes = 0

    def walk(directory: Path, parts: tuple[str, ...]) -> None:
        nonlocal total_bytes
        try:
            entries = sorted(
                os.scandir(directory),
                key=lambda entry: (entry.name.casefold(), entry.name),
            )
        except OSError:
            raise PayloadValidationError("installer_payload_scan_failed") from None
        for entry in entries:
            relative = _validate_relative_path("/".join((*parts, entry.name)))
            folded = relative.casefold()
            if folded in folded_paths:
                raise PayloadValidationError("installer_payload_duplicate_path")
            try:
                metadata = os.stat(entry.path, follow_symlinks=False)
            except OSError:
                raise PayloadValidationError(
                    "installer_payload_source_identity_unavailable"
                ) from None
            if (
                stat.S_ISDIR(metadata.st_mode)
                and not entry.is_symlink()
                and not _is_reparse(metadata)
            ):
                walk(Path(entry.path), (*parts, entry.name))
                continue
            if (
                not stat.S_ISREG(metadata.st_mode)
                or entry.is_symlink()
                or _is_reparse(metadata)
                or metadata.st_nlink != 1
            ):
                raise PayloadValidationError(
                    "installer_payload_link_reparse_or_hardlink_forbidden"
                )
            if metadata.st_size < 0 or metadata.st_size > MAX_FILE_BYTES:
                raise PayloadValidationError("installer_payload_file_size_invalid")
            if len(inventory) >= MAX_FILES:
                raise PayloadValidationError("installer_payload_file_count_over_budget")
            total_bytes += metadata.st_size
            if total_bytes > MAX_TOTAL_BYTES:
                raise PayloadValidationError("installer_payload_total_size_over_budget")
            digest = _read_and_digest(Path(entry.path), _identity(metadata))
            inventory.append(PayloadFile(relative, metadata.st_size, digest))
            folded_paths.add(folded)

    walk(root, ())
    ordered = tuple(
        sorted(
            inventory,
            key=lambda item: (item.relative_path.casefold(), item.relative_path),
        )
    )
    if not ordered:
        raise PayloadValidationError("installer_payload_empty")
    if REQUIRED_ENTRYPOINT not in {item.relative_path for item in ordered}:
        raise PayloadValidationError("installer_payload_entrypoint_missing")
    canonical = b"".join(
        (
            item.relative_path.encode("utf-8")
            + b"\0"
            + str(item.size).encode("ascii")
            + b"\0"
            + item.sha256.encode("ascii")
            + b"\n"
        )
        for item in ordered
    )
    return PayloadSummary(
        files=ordered,
        total_bytes=total_bytes,
        tree_sha256=hashlib.sha256(canonical).hexdigest(),
    )


def _safe_staging_destination(destination: Path, source_root: Path) -> Path:
    if not destination.is_absolute():
        raise PayloadValidationError("installer_payload_copy_root_must_be_absolute")
    absolute = destination.absolute()
    if absolute == Path(absolute.anchor):
        raise PayloadValidationError("installer_payload_copy_root_invalid")
    if os.name == "nt" and str(absolute).startswith("\\\\"):
        raise PayloadValidationError("installer_payload_copy_root_invalid")
    if absolute.exists() or absolute.is_symlink():
        raise PayloadValidationError("installer_payload_copy_root_exists")
    try:
        absolute.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise PayloadValidationError("installer_payload_copy_root_inside_source")
    parent = absolute.parent
    try:
        metadata = os.stat(parent, follow_symlinks=False)
        resolved = parent.resolve(strict=True)
    except OSError:
        raise PayloadValidationError("installer_payload_copy_parent_invalid") from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or not _same_physical_path(parent, resolved)
    ):
        raise PayloadValidationError("installer_payload_copy_parent_invalid")
    return absolute


def _copy_payload_file(source: Path, destination: Path, expected: PayloadFile) -> None:
    try:
        metadata = os.stat(source, follow_symlinks=False)
    except OSError:
        raise PayloadValidationError("installer_payload_copy_source_changed") from None
    identity = _identity(metadata)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or metadata.st_nlink != 1
        or metadata.st_size != expected.size
    ):
        raise PayloadValidationError("installer_payload_copy_source_changed")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    digest = hashlib.sha256()
    copied = 0
    try:
        descriptor = os.open(source, flags)
        with (
            os.fdopen(descriptor, "rb", closefd=True) as input_file,
            destination.open("xb") as output,
        ):
            if _identity(os.fstat(input_file.fileno())) != identity:
                raise PayloadValidationError("installer_payload_copy_source_changed")
            while chunk := input_file.read(1024 * 1024):
                copied += len(chunk)
                if copied > expected.size:
                    raise PayloadValidationError(
                        "installer_payload_copy_source_changed"
                    )
                digest.update(chunk)
                output.write(chunk)
            if _identity(os.fstat(input_file.fileno())) != identity:
                raise PayloadValidationError("installer_payload_copy_source_changed")
    except PayloadValidationError:
        raise
    except OSError:
        raise PayloadValidationError("installer_payload_copy_failed") from None
    if copied != expected.size or digest.hexdigest() != expected.sha256:
        raise PayloadValidationError("installer_payload_copy_source_changed")


def copy_validated_payload(payload_root: Path, destination: Path) -> PayloadSummary:
    """Copy exactly the validated files into a new exclusive WiX bind tree."""

    source_root = _safe_payload_root(payload_root)
    summary = validate_payload(source_root)
    copy_root = _safe_staging_destination(destination, source_root)
    try:
        copy_root.mkdir()
        for payload_file in summary.files:
            relative = PurePosixPath(payload_file.relative_path)
            target = copy_root.joinpath(*relative.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            _copy_payload_file(
                source_root.joinpath(*relative.parts),
                target,
                payload_file,
            )
    except PayloadValidationError:
        raise
    except OSError:
        raise PayloadValidationError("installer_payload_copy_failed") from None
    copied_summary = validate_payload(copy_root)
    if copied_summary != summary:
        raise PayloadValidationError("installer_payload_copy_digest_mismatch")
    return copied_summary


def _stable_identifier(prefix: str, relative_path: str) -> str:
    identifier = (
        f"{prefix}_{hashlib.sha256(relative_path.casefold().encode()).hexdigest()[:24]}"
    )
    if _SAFE_ID.fullmatch(identifier) is None:
        raise PayloadValidationError("installer_payload_identifier_invalid")
    return identifier


def render_payload_wxs(summary: PayloadSummary) -> bytes:
    """Render deterministic per-user components for a validated payload."""

    ET.register_namespace("", WIX_NAMESPACE)
    wix = ET.Element(f"{{{WIX_NAMESPACE}}}Wix")
    fragment = ET.SubElement(wix, f"{{{WIX_NAMESPACE}}}Fragment")
    group = ET.SubElement(
        fragment,
        f"{{{WIX_NAMESPACE}}}ComponentGroup",
        {"Id": "OmniBasePayload"},
    )
    payload_directories = {""}
    for payload_file in summary.files:
        parent = PurePosixPath(payload_file.relative_path).parent
        if parent == PurePosixPath("."):
            continue
        parts = parent.parts
        for index in range(1, len(parts) + 1):
            payload_directories.add("/".join(parts[:index]))
    for directory in sorted(
        payload_directories, key=lambda value: (value.casefold(), value)
    ):
        identity = directory if directory else "install-root"
        attributes = {
            "Id": _stable_identifier("dircmp", identity),
            "Directory": "INSTALLFOLDER",
            "Guid": (
                "{"
                + str(
                    uuid.uuid5(
                        COMPONENT_NAMESPACE,
                        f"directory:{identity.casefold()}",
                    )
                ).upper()
                + "}"
            ),
        }
        if directory:
            attributes["Subdirectory"] = directory.replace("/", "\\")
        component = ET.SubElement(
            group,
            f"{{{WIX_NAMESPACE}}}Component",
            attributes,
        )
        ET.SubElement(
            component,
            f"{{{WIX_NAMESPACE}}}RemoveFolder",
            {
                "Id": _stable_identifier("rmdir", identity),
                "On": "uninstall",
            },
        )
        ET.SubElement(
            component,
            f"{{{WIX_NAMESPACE}}}RegistryValue",
            {
                "Root": "HKCU",
                "Key": "Software\\OmniBase\\Installer\\Directories",
                "Name": hashlib.sha256(identity.casefold().encode("utf-8")).hexdigest(),
                "Type": "integer",
                "Value": "1",
                "KeyPath": "yes",
            },
        )

    for payload_file in summary.files:
        relative = PurePosixPath(payload_file.relative_path)
        directory = (
            "" if relative.parent == PurePosixPath(".") else str(relative.parent)
        )
        component_id = _stable_identifier("cmp", payload_file.relative_path)
        file_id = _stable_identifier("fil", payload_file.relative_path)
        marker_name = hashlib.sha256(
            payload_file.relative_path.casefold().encode("utf-8")
        ).hexdigest()
        attributes = {
            "Id": component_id,
            "Directory": "INSTALLFOLDER",
            "Guid": f"{{{str(uuid.uuid5(COMPONENT_NAMESPACE, payload_file.relative_path.casefold())).upper()}}}",
        }
        if directory:
            attributes["Subdirectory"] = directory.replace("/", "\\")
        component = ET.SubElement(
            group,
            f"{{{WIX_NAMESPACE}}}Component",
            attributes,
        )
        ET.SubElement(
            component,
            f"{{{WIX_NAMESPACE}}}File",
            {
                "Id": file_id,
                "Source": (
                    "!(bindpath.PayloadRoot)\\"
                    + payload_file.relative_path.replace("/", "\\")
                ),
                "KeyPath": "no",
            },
        )
        ET.SubElement(
            component,
            f"{{{WIX_NAMESPACE}}}RegistryValue",
            {
                "Root": "HKCU",
                "Key": "Software\\OmniBase\\Installer\\Components",
                "Name": marker_name,
                "Type": "integer",
                "Value": "1",
                "KeyPath": "yes",
            },
        )
    ET.indent(wix, space="  ")
    return ET.tostring(wix, encoding="utf-8", xml_declaration=True) + b"\n"


def write_payload_wxs(output_path: Path, raw: bytes, payload_root: Path) -> None:
    output = output_path.absolute()
    root = payload_root.absolute()
    try:
        output.relative_to(root)
    except ValueError:
        pass
    else:
        raise PayloadValidationError("installer_payload_output_inside_payload")
    try:
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = output.with_name(
            f".{output.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        )
        with temporary.open("xb") as handle:
            handle.write(raw)
        os.replace(temporary, output)
    except OSError:
        raise PayloadValidationError(
            "installer_payload_authoring_write_failed"
        ) from None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload-root", type=Path, required=True)
    parser.add_argument("--copy-to", type=Path)
    parser.add_argument("--output-wxs", type=Path)
    args = parser.parse_args()
    try:
        effective_root = args.payload_root
        if args.copy_to is None:
            summary = validate_payload(effective_root)
        else:
            summary = copy_validated_payload(effective_root, args.copy_to)
            effective_root = args.copy_to
        if args.output_wxs is not None:
            write_payload_wxs(
                args.output_wxs,
                render_payload_wxs(summary),
                effective_root,
            )
    except PayloadValidationError as exc:
        print(json.dumps({"error": str(exc)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "file_count": summary.file_count,
                "total_bytes": summary.total_bytes,
                "tree_sha256": summary.tree_sha256,
                "payload_copied": args.copy_to is not None,
                "wix_authoring_written": args.output_wxs is not None,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
