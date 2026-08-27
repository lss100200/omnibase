"""Public, path-redacted errors for the personal desktop database."""

from __future__ import annotations


class DesktopLocalError(RuntimeError):
    """Base error whose public representation contains only a stable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class UnsafeDataRoot(DesktopLocalError):
    """The configured data root does not satisfy the local safety contract."""


class DesktopDatabaseUnavailable(DesktopLocalError):
    """The desktop database could not be opened or configured safely."""


class DesktopMigrationError(DesktopLocalError):
    """The local schema cannot be proven current and internally consistent."""
