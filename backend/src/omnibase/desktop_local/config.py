"""Explicit configuration and data-root validation for the personal desktop runtime.

This module deliberately does not use ``BaseSettings``, ``dotenv`` or ambient
environment variables.  A desktop launcher must pass the selected data root
and application version explicitly.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from omnibase.desktop_local.errors import UnsafeDataRoot

_REPARSE_POINT_ATTRIBUTE = 0x400
_DATABASE_FILENAME = "omnibase.sqlite3"


def _is_reparse_or_symlink(path: Path) -> bool:
    try:
        metadata = path.lstat()
    except OSError:
        raise UnsafeDataRoot("desktop_data_root_metadata_unavailable") from None
    if stat.S_ISLNK(metadata.st_mode):
        return True
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & _REPARSE_POINT_ATTRIBUTE)


def _existing_components(path: Path) -> tuple[Path, ...]:
    components: list[Path] = []
    current = path
    while True:
        if current.exists() or current.is_symlink():
            components.append(current)
        if current.parent == current:
            break
        current = current.parent
    components.reverse()
    return tuple(components)


def validate_data_root(data_root: Path) -> Path:
    """Validate and return an absolute lexical data root without exposing it in errors.

    Existing path components may not be symbolic links or Windows reparse points.
    The filesystem root itself and an existing non-directory are rejected.  The
    final directory may be absent so a first launch can create it deliberately.
    """

    root = Path(data_root)
    if not root.is_absolute():
        raise UnsafeDataRoot("desktop_data_root_must_be_absolute")
    if ".." in root.parts:
        raise UnsafeDataRoot("desktop_data_root_parent_traversal_not_allowed")
    if os.name == "nt":
        if str(root).startswith("\\\\"):
            raise UnsafeDataRoot("desktop_data_root_must_be_local")
        if any(":" in component for component in root.parts[1:]):
            raise UnsafeDataRoot("desktop_data_root_alternate_stream_not_allowed")
    if root == Path(root.anchor):
        raise UnsafeDataRoot("desktop_data_root_must_not_be_filesystem_root")

    for component in _existing_components(root):
        if _is_reparse_or_symlink(component):
            raise UnsafeDataRoot("desktop_data_root_reparse_not_allowed")
    if root.exists() and not root.is_dir():
        raise UnsafeDataRoot("desktop_data_root_not_directory")
    return root


def prepare_data_root(data_root: Path) -> Path:
    """Create the validated data/state directories and revalidate their identity."""

    root = validate_data_root(data_root)
    try:
        root.mkdir(parents=True, exist_ok=True)
        state_root = root / "state"
        state_root.mkdir(exist_ok=True)
    except OSError:
        raise UnsafeDataRoot("desktop_data_root_create_failed") from None

    validate_data_root(root)
    if _is_reparse_or_symlink(state_root) or not state_root.is_dir():
        raise UnsafeDataRoot("desktop_state_root_not_safe")
    return root


@dataclass(frozen=True, slots=True)
class DesktopLocalConfig:
    """Explicit, environment-independent configuration for the local database."""

    data_root: Path
    application_version: str
    busy_timeout_ms: int = 5_000

    def __post_init__(self) -> None:
        root = validate_data_root(self.data_root)
        if not self.application_version or len(self.application_version) > 64:
            raise ValueError("application_version must contain 1 to 64 characters")
        if not 100 <= self.busy_timeout_ms <= 60_000:
            raise ValueError("busy_timeout_ms must be between 100 and 60000")
        object.__setattr__(self, "data_root", root)

    @property
    def database_path(self) -> Path:
        """Return the fixed database location below the validated data root."""

        return self.data_root / "state" / _DATABASE_FILENAME


def default_user_data_root(local_app_data: Path) -> Path:
    """Derive the product data root from a launcher-supplied Windows location."""

    return validate_data_root(Path(local_app_data) / "OmniBase")
