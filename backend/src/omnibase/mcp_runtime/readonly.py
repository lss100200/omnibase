"""Bounded local-files and Git inspection tools for a stdio MCP server."""

from __future__ import annotations

import os
import queue
import stat
import subprocess
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path, PurePosixPath
from typing import IO, Any

from omnibase.runtime.diagnostics import redact_mapping

_MAX_READ_BYTES = 256_000
_MAX_HASH_BYTES = 4 * 1024 * 1024
_MAX_OUTPUT_BYTES = 512_000
_MAX_LIST_ENTRIES = 500
_MAX_LIST_VISITED_ENTRIES = 2_048
_MAX_PROCESS_CALLS = 256
_MAX_PROCESS_FILE_BYTES = 16 * 1024 * 1024
_MAX_PROCESS_GIT_BYTES = 4 * 1024 * 1024
_MAX_SEARCH_DEPTH = 8
_MAX_SEARCH_FILES = 64
_MAX_SEARCH_ENTRIES = 2_048
_MAX_SEARCH_BYTES = 2 * 1024 * 1024
_MAX_SEARCH_MATCHES = 100
_MAX_SEARCH_SNIPPET_CHARS = 180
_READ_CHUNK_BYTES = 64 * 1024
_GIT_TIMEOUT_SECONDS = 10.0
_SENSITIVE_NAMES = {
    ".aws",
    ".azure",
    ".docker",
    ".env",
    ".env.local",
    ".git",
    ".git-credentials",
    ".gnupg",
    ".hg",
    ".kube",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh",
    ".svn",
    "auth.json",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "id_rsa",
    "known_hosts",
    "secrets.json",
}
_SENSITIVE_SUFFIXES = (
    ".db",
    ".der",
    ".jks",
    ".key",
    ".keystore",
    ".kdbx",
    ".p12",
    ".pem",
    ".pfx",
    ".sqlite",
)


class McpToolError(ValueError):
    """Stable sanitized read-only MCP error."""


def _is_reparse(metadata: os.stat_result) -> bool:
    return bool(getattr(metadata, "st_file_attributes", 0) & 0x400)


@dataclass(frozen=True, slots=True)
class _StableIdentity:
    device: int
    inode: int
    mode: int
    size: int
    modified_ns: int
    changed_ns: int


def _identity(metadata: os.stat_result) -> _StableIdentity:
    return _StableIdentity(
        device=metadata.st_dev,
        inode=metadata.st_ino,
        mode=metadata.st_mode,
        size=metadata.st_size,
        modified_ns=metadata.st_mtime_ns,
        changed_ns=metadata.st_ctime_ns,
    )


def _capture_path_identity(path: Path, *, directory: bool) -> _StableIdentity:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise McpToolError("mcp_identity_unavailable") from exc
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
        raise McpToolError("mcp_identity_invalid")
    return _identity(metadata)


def _verify_path_identity(path: Path, expected: _StableIdentity, *, directory: bool) -> None:
    current = _capture_path_identity(path, directory=directory)
    stable = (
        current.device == expected.device
        and current.inode == expected.inode
        and stat.S_IFMT(current.mode) == stat.S_IFMT(expected.mode)
    )
    if not directory:
        stable = stable and current == expected
    if not stable:
        raise McpToolError("mcp_identity_drifted")


def _same_opened_file(left: _StableIdentity, right: _StableIdentity) -> bool:
    return (
        left.device == right.device
        and left.inode == right.inode
        and stat.S_IFMT(left.mode) == stat.S_IFMT(right.mode)
        and left.size == right.size
        and left.modified_ns == right.modified_ns
    )


def _reject_component_links(root: Path, relative: PurePosixPath) -> Path:
    current = root
    for part in relative.parts:
        if part in {"", ".", ".."} or ":" in part or "\\" in part:
            raise McpToolError("mcp_path_invalid")
        current = current / part
        try:
            metadata = os.stat(current, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise McpToolError("mcp_path_not_found") from exc
        if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
            raise McpToolError("mcp_path_link_forbidden")
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise McpToolError("mcp_path_escape_forbidden")
    return resolved


def _safe_relative(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or value.startswith(("/", "\\")):
        raise McpToolError("mcp_path_invalid")
    if value == ".":
        return PurePosixPath()
    normalized = PurePosixPath(value.replace("\\", "/"))
    if any(part in {"", ".", ".."} or ":" in part for part in normalized.parts):
        raise McpToolError("mcp_path_invalid")
    return normalized


def _sensitive(relative: PurePosixPath) -> bool:
    lowered = [part.casefold() for part in relative.parts]
    return any(
        part in _SENSITIVE_NAMES or part.startswith(".env.") or part.endswith(_SENSITIVE_SUFFIXES)
        for part in lowered
    )


def _filter_git_status(raw: str) -> str:
    visible: list[str] = []
    for line in raw.splitlines():
        if len(line) < 4:
            continue
        path_text = line[3:]
        candidates = [part.strip('"') for part in path_text.split(" -> ")]
        try:
            if any(_sensitive(_safe_relative(candidate)) for candidate in candidates):
                continue
        except McpToolError:
            continue
        visible.append(line)
    return "\n".join(visible) + ("\n" if visible else "")


def _open_regular_file(path: Path) -> IO[bytes]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise McpToolError("mcp_file_open_failed") from exc
    try:
        stream = os.fdopen(descriptor, "rb", closefd=True)
    except Exception:
        os.close(descriptor)
        raise
    return stream


def _read_bounded_file(
    path: Path,
    *,
    max_bytes: int = _MAX_READ_BYTES,
    budget: _ProcessBudget | None = None,
) -> bytes:
    before = _capture_path_identity(path, directory=False)
    if before.size > max_bytes:
        raise McpToolError("mcp_file_too_large")
    reserved_size = 0
    if budget is not None:
        budget.require_file_capacity()
        budget.reserve_file_bytes(before.size)
        reserved_size = before.size
    with _open_regular_file(path) as stream:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or not _same_opened_file(_identity(opened), before)
        ):
            raise McpToolError("mcp_file_identity_drifted")
        if opened.st_size > max_bytes:
            raise McpToolError("mcp_file_too_large")
        chunks: list[bytes] = []
        total = 0
        charged_growth = 0
        while True:
            chunk = stream.read(min(_READ_CHUNK_BYTES, max_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            growth = max(0, total - reserved_size)
            if budget is not None and growth > charged_growth:
                budget.consume_file_bytes(growth - charged_growth)
                charged_growth = growth
            if total > max_bytes:
                raise McpToolError("mcp_file_too_large")
        after = os.fstat(stream.fileno())
        if not _same_opened_file(_identity(after), _identity(opened)):
            raise McpToolError("mcp_file_identity_drifted")
    current = _capture_path_identity(path, directory=False)
    if not _same_opened_file(current, before):
        raise McpToolError("mcp_file_identity_drifted")
    return b"".join(chunks)


def _redact_text(content: str) -> str:
    redacted = redact_mapping({"content": content}).get("content")
    if not isinstance(redacted, str):
        raise McpToolError("mcp_secret_scan_failed")
    return redacted


def _walk_regular_files(directory: Path, authorized_root: Path) -> Iterator[Path]:
    pending: list[tuple[Path, int]] = [(directory, 0)]
    visited_entries = 0
    while pending:
        current, depth = pending.pop()
        current_relative = PurePosixPath(current.relative_to(authorized_root).as_posix())
        current = _reject_component_links(authorized_root, current_relative)
        current_identity = _capture_path_identity(current, directory=True)
        children: list[Path] = []
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    visited_entries += 1
                    if visited_entries > _MAX_SEARCH_ENTRIES:
                        raise McpToolError("mcp_search_tree_too_large")
                    children.append(Path(entry.path))
        except OSError as exc:
            raise McpToolError("mcp_search_tree_unavailable") from exc
        _verify_path_identity(current, current_identity, directory=True)
        children.sort(key=lambda item: item.name.casefold())
        next_directories: list[Path] = []
        for child in children:
            relative = PurePosixPath(child.relative_to(authorized_root).as_posix())
            if _sensitive(relative):
                continue
            child = _reject_component_links(authorized_root, relative)
            try:
                metadata = os.stat(child, follow_symlinks=False)
            except OSError as exc:
                raise McpToolError("mcp_search_tree_unavailable") from exc
            if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
                continue
            if stat.S_ISREG(metadata.st_mode):
                yield child
            elif stat.S_ISDIR(metadata.st_mode) and depth < _MAX_SEARCH_DEPTH:
                next_directories.append(child)
        pending.extend((child, depth + 1) for child in reversed(next_directories))


def _git_environment(git_executable: Path) -> dict[str, str]:
    return {
        "PATH": str(git_executable.parent),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PAGER": "cat",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C.UTF-8",
    }


def _git_command(git_executable: Path, arguments: list[str]) -> list[str]:
    return [
        str(git_executable),
        "-c",
        "core.pager=cat",
        "-c",
        "core.fsmonitor=false",
        "-c",
        "core.hooksPath=",
        "-c",
        "diff.external=",
        "-c",
        "credential.helper=",
        *arguments,
    ]


def _parse_name_status(raw: str) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            continue
        status, path_text = fields
        try:
            relative = _safe_relative(path_text)
        except McpToolError:
            continue
        if _sensitive(relative):
            continue
        statuses[relative.as_posix()] = status
    return statuses


def _parse_numstat(raw: str, statuses: dict[str, str]) -> list[dict[str, object]]:
    files: list[dict[str, object]] = []
    for line in raw.splitlines():
        fields = line.split("\t")
        if len(fields) != 3:
            continue
        added_text, deleted_text, path_text = fields
        try:
            relative = _safe_relative(path_text)
        except McpToolError:
            continue
        path = relative.as_posix()
        if _sensitive(relative) or path not in statuses:
            continue
        added = int(added_text) if added_text.isdigit() else None
        deleted = int(deleted_text) if deleted_text.isdigit() else None
        files.append(
            {
                "path": path,
                "status": statuses[path],
                "added": added,
                "deleted": deleted,
            }
        )
    return files


def _search_query(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 2 <= len(value) <= 128
        or any(ord(character) < 32 for character in value)
    ):
        raise McpToolError("mcp_search_query_invalid")
    return value


def _search_file(
    path: Path,
    relative: PurePosixPath,
    query: str,
    *,
    authorized_root: Path,
    budget: _ProcessBudget,
    max_matches: int,
) -> tuple[int, list[dict[str, object]]]:
    path = _reject_component_links(authorized_root, relative)
    raw = _read_bounded_file(path, budget=budget)
    if b"\x00" in raw:
        return len(raw), []
    try:
        content = raw.decode("utf-8")
    except UnicodeDecodeError:
        return len(raw), []
    matches: list[dict[str, object]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        column = line.find(query)
        if column < 0:
            continue
        snippet_start = max(0, column - (_MAX_SEARCH_SNIPPET_CHARS // 3))
        snippet = line[snippet_start : snippet_start + _MAX_SEARCH_SNIPPET_CHARS]
        matches.append(
            {
                "path": relative.as_posix(),
                "line": line_number,
                "column": column + 1,
                "snippet": _redact_text(snippet),
            }
        )
        if len(matches) >= max_matches:
            break
    return len(raw), matches


@dataclass(slots=True)
class _ProcessBudget:
    calls: int = 0
    file_bytes: int = 0
    git_bytes: int = 0

    def reserve_call(self) -> None:
        if self.calls >= _MAX_PROCESS_CALLS:
            raise McpToolError("mcp_process_call_budget_exhausted")
        self.calls += 1

    def reserve_file_bytes(self, amount: int) -> None:
        if amount < 0 or self.file_bytes + amount > _MAX_PROCESS_FILE_BYTES:
            raise McpToolError("mcp_process_file_budget_exhausted")
        self.file_bytes += amount

    def require_file_capacity(self) -> None:
        if self.file_bytes >= _MAX_PROCESS_FILE_BYTES:
            raise McpToolError("mcp_process_file_budget_exhausted")

    def consume_file_bytes(self, amount: int) -> None:
        if amount < 0:
            raise McpToolError("mcp_process_file_budget_exhausted")
        if self.file_bytes + amount > _MAX_PROCESS_FILE_BYTES:
            self.file_bytes = _MAX_PROCESS_FILE_BYTES
            raise McpToolError("mcp_process_file_budget_exhausted")
        self.file_bytes += amount

    def reserve_git_bytes(self, amount: int) -> None:
        if amount < 0:
            raise McpToolError("mcp_process_git_budget_exhausted")
        if self.git_bytes + amount > _MAX_PROCESS_GIT_BYTES:
            # Git output has already crossed the pipe boundary. Saturate the
            # lifetime counter before failing so consumed stderr/stdout cannot
            # be replayed against an apparently unspent budget.
            self.git_bytes = _MAX_PROCESS_GIT_BYTES
            raise McpToolError("mcp_process_git_budget_exhausted")
        self.git_bytes += amount

    def require_git_capacity(self) -> None:
        if self.git_bytes >= _MAX_PROCESS_GIT_BYTES:
            raise McpToolError("mcp_process_git_budget_exhausted")


@dataclass(frozen=True, slots=True)
class ReadOnlyMcpServer:
    authorized_root: Path
    repo_root: Path
    git_executable: Path
    authorized_root_identity: _StableIdentity
    repo_root_identity: _StableIdentity
    git_executable_identity: _StableIdentity
    _budget: _ProcessBudget = field(default_factory=_ProcessBudget, compare=False, repr=False)

    @classmethod
    def create(
        cls,
        *,
        authorized_root: Path,
        repo_root: Path,
        git_executable: Path,
    ) -> ReadOnlyMcpServer:
        roots: list[Path] = []
        identities: list[_StableIdentity] = []
        for root in (authorized_root, repo_root):
            canonical = root.resolve()
            identity = _capture_path_identity(root, directory=True)
            if canonical != root.absolute():
                raise McpToolError("mcp_root_invalid")
            roots.append(canonical)
            identities.append(identity)
        git = git_executable.resolve()
        git_identity = _capture_path_identity(git, directory=False)
        return cls(
            authorized_root=roots[0],
            repo_root=roots[1],
            git_executable=git,
            authorized_root_identity=identities[0],
            repo_root_identity=identities[1],
            git_executable_identity=git_identity,
        )

    def _verify_boundaries(self) -> None:
        _verify_path_identity(self.authorized_root, self.authorized_root_identity, directory=True)
        _verify_path_identity(self.repo_root, self.repo_root_identity, directory=True)
        _verify_path_identity(self.git_executable, self.git_executable_identity, directory=False)

    @staticmethod
    def tools() -> list[dict[str, object]]:
        return [
            {
                "name": "omnibase_files_list",
                "description": "List bounded entries below the explicitly authorized root.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "omnibase_files_read",
                "description": "Read one bounded UTF-8 text file below the authorized root.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "omnibase_git_inspect",
                "description": "Run one closed metadata-only Git inspection operation.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"operation": {"enum": ["status", "log"]}},
                    "required": ["operation"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "omnibase_files_hash",
                "description": "Compute SHA-256 for one bounded regular file without returning content.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "omnibase_text_search",
                "description": "Search a bounded UTF-8 tree for one literal text query.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "query": {"type": "string", "minLength": 2, "maxLength": 128},
                    },
                    "required": ["path", "query"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "omnibase_git_diff_summary",
                "description": "Return bounded worktree or staged Git name/status and line-count metadata.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"scope": {"enum": ["worktree", "staged"]}},
                    "required": ["scope"],
                    "additionalProperties": False,
                },
            },
        ]

    def call(self, name: str, arguments: object) -> dict[str, object]:
        self._verify_boundaries()
        self._budget.reserve_call()
        if not isinstance(arguments, dict) or any(not isinstance(key, str) for key in arguments):
            raise McpToolError("mcp_arguments_invalid")
        if name == "omnibase_files_list":
            result = self._list(arguments)
        elif name == "omnibase_files_read":
            result = self._read(arguments)
        elif name == "omnibase_git_inspect":
            result = self._git(arguments)
        elif name == "omnibase_files_hash":
            result = self._hash(arguments)
        elif name == "omnibase_text_search":
            result = self._search(arguments)
        elif name == "omnibase_git_diff_summary":
            result = self._git_diff_summary(arguments)
        else:
            raise McpToolError("mcp_tool_unknown")
        self._verify_boundaries()
        return result

    def _list(self, arguments: dict[str, Any]) -> dict[str, object]:
        if set(arguments) != {"path"}:
            raise McpToolError("mcp_arguments_invalid")
        relative = _safe_relative(arguments["path"])
        directory = _reject_component_links(self.authorized_root, relative)
        before = _capture_path_identity(directory, directory=True)
        children: list[Path] = []
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if len(children) >= _MAX_LIST_VISITED_ENTRIES:
                        raise McpToolError("mcp_list_directory_too_large")
                    children.append(Path(entry.path))
        except OSError as exc:
            raise McpToolError("mcp_list_directory_unavailable") from exc
        _verify_path_identity(directory, before, directory=True)
        entries: list[dict[str, object]] = []
        for child in sorted(children, key=lambda item: item.name.casefold()):
            child_relative = PurePosixPath(child.relative_to(self.authorized_root).as_posix())
            if _sensitive(child_relative):
                continue
            metadata = os.stat(child, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
                continue
            if not (stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)):
                continue
            entries.append(
                {
                    "path": child_relative.as_posix(),
                    "kind": "directory" if stat.S_ISDIR(metadata.st_mode) else "file",
                    "size": metadata.st_size if stat.S_ISREG(metadata.st_mode) else None,
                }
            )
            if len(entries) >= _MAX_LIST_ENTRIES:
                break
        _verify_path_identity(directory, before, directory=True)
        return {"entries": entries, "truncated": len(entries) >= _MAX_LIST_ENTRIES}

    def _read(self, arguments: dict[str, Any]) -> dict[str, object]:
        if set(arguments) != {"path"}:
            raise McpToolError("mcp_arguments_invalid")
        relative = _safe_relative(arguments["path"])
        if _sensitive(relative):
            raise McpToolError("mcp_sensitive_file_forbidden")
        path = _reject_component_links(self.authorized_root, relative)
        raw = _read_bounded_file(path, budget=self._budget)
        if b"\x00" in raw:
            raise McpToolError("mcp_binary_file_forbidden")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise McpToolError("mcp_text_encoding_invalid") from exc
        return {
            "path": relative.as_posix(),
            "content": _redact_text(content),
            "bytes": len(raw),
        }

    def _hash(self, arguments: dict[str, Any]) -> dict[str, object]:
        if set(arguments) != {"path"}:
            raise McpToolError("mcp_arguments_invalid")
        relative = _safe_relative(arguments["path"])
        if _sensitive(relative):
            raise McpToolError("mcp_sensitive_file_forbidden")
        path = _reject_component_links(self.authorized_root, relative)
        raw = _read_bounded_file(path, max_bytes=_MAX_HASH_BYTES, budget=self._budget)
        return {
            "path": relative.as_posix(),
            "algorithm": "sha256",
            "sha256": sha256(raw).hexdigest(),
            "bytes": len(raw),
        }

    def _search(self, arguments: dict[str, Any]) -> dict[str, object]:
        if set(arguments) != {"path", "query"}:
            raise McpToolError("mcp_arguments_invalid")
        query = _search_query(arguments["query"])
        relative = _safe_relative(arguments["path"])
        if _sensitive(relative):
            raise McpToolError("mcp_sensitive_file_forbidden")
        directory = _reject_component_links(self.authorized_root, relative)
        before = _capture_path_identity(directory, directory=True)
        matches: list[dict[str, object]] = []
        inspected_files = 0
        inspected_bytes = 0
        truncated = False

        for path in _walk_regular_files(directory, self.authorized_root):
            if inspected_files >= _MAX_SEARCH_FILES or len(matches) >= _MAX_SEARCH_MATCHES:
                truncated = True
                break
            item_relative = PurePosixPath(path.relative_to(self.authorized_root).as_posix())
            if _sensitive(item_relative):
                continue
            metadata = os.stat(path, follow_symlinks=False)
            if metadata.st_size > _MAX_READ_BYTES:
                continue
            if inspected_bytes + metadata.st_size > _MAX_SEARCH_BYTES:
                truncated = True
                break
            read_bytes, file_matches = _search_file(
                path,
                item_relative,
                query,
                authorized_root=self.authorized_root,
                budget=self._budget,
                max_matches=_MAX_SEARCH_MATCHES - len(matches),
            )
            inspected_files += 1
            inspected_bytes += read_bytes
            matches.extend(file_matches)
            if len(matches) >= _MAX_SEARCH_MATCHES:
                truncated = True
        _verify_path_identity(directory, before, directory=True)
        return {
            "query": query,
            "matches": matches,
            "inspected_files": inspected_files,
            "inspected_bytes": inspected_bytes,
            "truncated": truncated,
        }

    def _git(self, arguments: dict[str, Any]) -> dict[str, object]:
        operation = arguments.get("operation")
        if operation == "status" and set(arguments) == {"operation"}:
            args = ["status", "--porcelain=v1", "--untracked-files=all"]
        elif operation == "log" and set(arguments) == {"operation"}:
            args = ["log", "--no-decorate", "--max-count=20", "--format=%H%x09%s"]
        else:
            raise McpToolError("mcp_git_operation_invalid")
        environment = {
            "PATH": str(self.git_executable.parent),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat",
            "GIT_TERMINAL_PROMPT": "0",
            "LC_ALL": "C.UTF-8",
        }
        command = [
            str(self.git_executable),
            "-c",
            "core.pager=cat",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "core.hooksPath=",
            "-c",
            "diff.external=",
            "-c",
            "credential.helper=",
            *args,
        ]
        stdout = _run_bounded(
            command,
            cwd=self.repo_root,
            environment=environment,
            budget=self._budget,
        )
        output = stdout.decode("utf-8", errors="replace")
        if operation == "status":
            output = _filter_git_status(output)
        return {"operation": operation, "output": _redact_text(output), "truncated": False}

    def _git_diff_summary(self, arguments: dict[str, Any]) -> dict[str, object]:
        scope_value = arguments.get("scope")
        if (
            set(arguments) != {"scope"}
            or not isinstance(scope_value, str)
            or scope_value
            not in {
                "worktree",
                "staged",
            }
        ):
            raise McpToolError("mcp_git_diff_scope_invalid")
        scope = scope_value
        cached = ["--cached"] if scope == "staged" else []
        common = ["diff", "--no-ext-diff", "--no-textconv", "--no-renames", *cached]
        environment = _git_environment(self.git_executable)
        name_status_raw = _run_bounded(
            _git_command(self.git_executable, [*common, "--name-status"]),
            cwd=self.repo_root,
            environment=environment,
            budget=self._budget,
        )
        numstat_raw = _run_bounded(
            _git_command(self.git_executable, [*common, "--numstat"]),
            cwd=self.repo_root,
            environment=environment,
            budget=self._budget,
        )
        status_by_path = _parse_name_status(name_status_raw.decode("utf-8", errors="replace"))
        files = _parse_numstat(numstat_raw.decode("utf-8", errors="replace"), status_by_path)
        return {
            "scope": scope,
            "files": files,
            "file_count": len(files),
            "truncated": False,
        }


def _run_bounded(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    budget: _ProcessBudget,
) -> bytes:
    budget.require_git_capacity()
    process = _start_git_process(command, cwd=cwd, environment=environment)
    assert process.stdout is not None
    assert process.stderr is not None
    chunks, readers = _start_pipe_readers(process.stdout, process.stderr)
    try:
        return _collect_process_output(process, chunks, len(readers), budget)
    except McpToolError:
        _terminate_process(process)
        _drain_reader_queue(chunks, len(readers))
        raise
    finally:
        for reader in readers:
            reader.join(timeout=1)
        process.stdout.close()
        process.stderr.close()


def _start_git_process(
    command: list[str], *, cwd: Path, environment: dict[str, str]
) -> subprocess.Popen[bytes]:
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise McpToolError("mcp_git_inspection_failed") from exc
    if process.stdout is None or process.stderr is None:
        _terminate_process(process)
        raise McpToolError("mcp_git_inspection_failed")
    return process


def _start_pipe_readers(
    stdout_pipe: IO[bytes], stderr_pipe: IO[bytes]
) -> tuple[queue.Queue[tuple[bool, bytes | None]], list[threading.Thread]]:
    chunks: queue.Queue[tuple[bool, bytes | None]] = queue.Queue(maxsize=4)

    def read_pipe(pipe: IO[bytes], is_stdout: bool) -> None:
        try:
            while data := pipe.read(_READ_CHUNK_BYTES):
                chunks.put((is_stdout, data))
        finally:
            chunks.put((is_stdout, None))

    readers = [
        threading.Thread(target=read_pipe, args=(stdout_pipe, True), daemon=True),
        threading.Thread(target=read_pipe, args=(stderr_pipe, False), daemon=True),
    ]
    for reader in readers:
        reader.start()
    return chunks, readers


def _collect_process_output(
    process: subprocess.Popen[bytes],
    chunks: queue.Queue[tuple[bool, bytes | None]],
    reader_count: int,
    budget: _ProcessBudget,
) -> bytes:
    stdout = bytearray()
    total = 0
    closed_pipes = 0
    deadline = time.monotonic() + _GIT_TIMEOUT_SECONDS
    while closed_pipes < reader_count:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise McpToolError("mcp_git_inspection_timed_out")
        try:
            is_stdout, chunk = chunks.get(timeout=remaining)
        except queue.Empty as exc:
            raise McpToolError("mcp_git_inspection_timed_out") from exc
        if chunk is None:
            closed_pipes += 1
            continue
        budget.reserve_git_bytes(len(chunk))
        total += len(chunk)
        if total > _MAX_OUTPUT_BYTES:
            raise McpToolError("mcp_git_output_too_large")
        if is_stdout:
            stdout.extend(chunk)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise McpToolError("mcp_git_inspection_timed_out")
    try:
        return_code = process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        raise McpToolError("mcp_git_inspection_timed_out") from exc
    if return_code != 0:
        raise McpToolError("mcp_git_inspection_failed")
    return bytes(stdout)


def _terminate_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
    process.wait()


def _drain_reader_queue(chunks: queue.Queue[tuple[bool, bytes | None]], reader_count: int) -> None:
    closed_pipes = 0
    while closed_pipes < reader_count:
        try:
            _, chunk = chunks.get(timeout=1)
        except queue.Empty:
            break
        if chunk is None:
            closed_pipes += 1
