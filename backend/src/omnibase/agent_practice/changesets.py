"""Controller-owned CAS apply and rollback for disposable P6.4 workspaces."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

_FORBIDDEN_PARTS = frozenset({".git", ".env", "node_modules", "__pycache__"})


@dataclass(frozen=True, slots=True)
class TextChangeProposal:
    path: str
    expected_before_sha256: str
    after_text: str


@dataclass(frozen=True, slots=True)
class AppliedTextChange:
    path: str
    before_sha256: str
    after_sha256: str
    before_text: str


def _digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _is_link_or_reparse(path: Path) -> bool:
    """Reject symlinks and Windows junction/reparse points without following them."""

    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise ValueError("practice_changeset_path_unavailable") from exc
    return stat.S_ISLNK(metadata.st_mode) or bool(
        getattr(metadata, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _reject_link_components(root: Path, pure: PurePosixPath) -> Path:
    lexical_root = root.absolute()
    if _is_link_or_reparse(lexical_root) or not lexical_root.is_dir():
        raise ValueError("practice_changeset_root_invalid")
    cursor = lexical_root
    for part in pure.parts:
        cursor /= part
        if _is_link_or_reparse(cursor):
            raise ValueError("practice_changeset_link_rejected")
    return cursor


def _resolve_file(root: Path, relative: str) -> Path:
    pure = PurePosixPath(relative)
    if (
        not relative
        or pure.is_absolute()
        or ".." in pure.parts
        or any(part in _FORBIDDEN_PARTS for part in pure.parts)
        or "\\" in relative
    ):
        raise ValueError("practice_changeset_path_invalid")
    lexical = _reject_link_components(root, pure)
    resolved_root = root.resolve(strict=True)
    resolved = lexical.resolve(strict=True)
    if resolved == resolved_root or resolved_root not in resolved.parents:
        raise ValueError("practice_changeset_path_outside_root")
    if not resolved.is_file():
        raise ValueError("practice_changeset_file_invalid")
    return resolved


def _atomic_replace(*, target: Path, content: bytes, expected_current_sha256: str) -> None:
    """Replace one already-validated file without exposing a partial write."""

    if _is_link_or_reparse(target):
        raise ValueError("practice_changeset_link_rejected")
    current = target.read_bytes()
    if _digest(current) != expected_current_sha256:
        raise ValueError("practice_changeset_write_race")
    descriptor, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".p64tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(target.stat(follow_symlinks=False).st_mode))
        if _is_link_or_reparse(target) or _digest(target.read_bytes()) != expected_current_sha256:
            raise ValueError("practice_changeset_write_race")
        os.replace(temporary, target)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def apply_text_change(
    *, root: Path, proposal: TextChangeProposal, max_bytes: int = 256 * 1024
) -> AppliedTextChange:
    """Apply one complete UTF-8 text replacement after exact before-CAS."""

    target = _resolve_file(root, proposal.path)
    before = target.read_bytes()
    if _digest(before) != proposal.expected_before_sha256:
        raise ValueError("practice_changeset_before_drift")
    try:
        before_text = before.decode("utf-8")
        after = proposal.after_text.encode("utf-8")
    except UnicodeError as exc:
        raise ValueError("practice_changeset_utf8_required") from exc
    if len(after) > max_bytes:
        raise ValueError("practice_changeset_budget_exceeded")
    _atomic_replace(
        target=target,
        content=after,
        expected_current_sha256=proposal.expected_before_sha256,
    )
    observed = target.read_bytes()
    after_digest = _digest(after)
    if observed != after or _digest(observed) != after_digest:
        raise RuntimeError("practice_changeset_post_write_mismatch")
    return AppliedTextChange(
        path=proposal.path,
        before_sha256=proposal.expected_before_sha256,
        after_sha256=after_digest,
        before_text=before_text,
    )


def rollback_text_change(*, root: Path, applied: AppliedTextChange) -> str:
    """Restore exact prior text only while the applied digest is still live."""

    target = _resolve_file(root, applied.path)
    current = target.read_bytes()
    if _digest(current) != applied.after_sha256:
        raise ValueError("practice_changeset_rollback_conflict")
    restored = applied.before_text.encode("utf-8")
    _atomic_replace(
        target=target,
        content=restored,
        expected_current_sha256=applied.after_sha256,
    )
    if _digest(target.read_bytes()) != applied.before_sha256:
        raise RuntimeError("practice_changeset_rollback_mismatch")
    return applied.before_sha256


__all__ = [
    "AppliedTextChange",
    "TextChangeProposal",
    "apply_text_change",
    "rollback_text_change",
]
