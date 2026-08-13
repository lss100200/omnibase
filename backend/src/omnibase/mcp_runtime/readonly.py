"""Bounded local-files and Git inspection tools for a stdio MCP server."""

from __future__ import annotations

import os
import queue
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import IO, Any

from omnibase.runtime.diagnostics import redact_mapping

_MAX_READ_BYTES = 256_000
_MAX_OUTPUT_BYTES = 512_000
_MAX_LIST_ENTRIES = 500
_READ_CHUNK_BYTES = 64 * 1024
_GIT_TIMEOUT_SECONDS = 10.0
_SENSITIVE_NAMES = {
    ".aws",
    ".azure",
    ".docker",
    ".env",
    ".env.local",
    ".git-credentials",
    ".gnupg",
    ".kube",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".ssh",
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


def _read_bounded_file(path: Path) -> bytes:
    before = _capture_path_identity(path, directory=False)
    with _open_regular_file(path) as stream:
        opened = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(opened.st_mode)
            or _is_reparse(opened)
            or not _same_opened_file(_identity(opened), before)
        ):
            raise McpToolError("mcp_file_identity_drifted")
        if opened.st_size > _MAX_READ_BYTES:
            raise McpToolError("mcp_file_too_large")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = stream.read(min(_READ_CHUNK_BYTES, _MAX_READ_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_READ_BYTES:
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


@dataclass(frozen=True, slots=True)
class ReadOnlyMcpServer:
    authorized_root: Path
    repo_root: Path
    git_executable: Path
    authorized_root_identity: _StableIdentity
    repo_root_identity: _StableIdentity
    git_executable_identity: _StableIdentity

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
        ]

    def call(self, name: str, arguments: object) -> dict[str, object]:
        self._verify_boundaries()
        if not isinstance(arguments, dict) or any(not isinstance(key, str) for key in arguments):
            raise McpToolError("mcp_arguments_invalid")
        if name == "omnibase_files_list":
            result = self._list(arguments)
        elif name == "omnibase_files_read":
            result = self._read(arguments)
        elif name == "omnibase_git_inspect":
            result = self._git(arguments)
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
        entries: list[dict[str, object]] = []
        for child in sorted(directory.iterdir(), key=lambda item: item.name.casefold()):
            child_relative = PurePosixPath(child.relative_to(self.authorized_root).as_posix())
            if _sensitive(child_relative):
                continue
            metadata = os.stat(child, follow_symlinks=False)
            if stat.S_ISLNK(metadata.st_mode) or _is_reparse(metadata):
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
        raw = _read_bounded_file(path)
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
        stdout = _run_bounded(command, cwd=self.repo_root, environment=environment)
        output = stdout.decode("utf-8", errors="replace")
        if operation == "status":
            output = _filter_git_status(output)
        return {"operation": operation, "output": _redact_text(output), "truncated": False}


def _run_bounded(command: list[str], *, cwd: Path, environment: dict[str, str]) -> bytes:
    process = _start_git_process(command, cwd=cwd, environment=environment)
    assert process.stdout is not None
    assert process.stderr is not None
    chunks, readers = _start_pipe_readers(process.stdout, process.stderr)
    try:
        return _collect_process_output(process, chunks, len(readers))
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
