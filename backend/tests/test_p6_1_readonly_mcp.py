"""P6.1 read-only MCP threat-boundary tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from omnibase.mcp_runtime import readonly as readonly_mcp
from omnibase.mcp_runtime.readonly import McpToolError, ReadOnlyMcpServer


@pytest.fixture
def server(tmp_path: Path) -> ReadOnlyMcpServer:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git unavailable")
    root = tmp_path / "authorized"
    root.mkdir()
    (root / "visible.txt").write_text("hello", encoding="utf-8")
    (root / ".env").write_text("SECRET=forbidden", encoding="utf-8")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run([git, "init", "--quiet"], cwd=repo, check=True)
    (repo / "tracked.txt").write_text("worktree", encoding="utf-8")
    return ReadOnlyMcpServer.create(
        authorized_root=root,
        repo_root=repo,
        git_executable=Path(git),
    )


def test_tool_list_is_an_exact_six_tool_read_only_closed_set(server: ReadOnlyMcpServer) -> None:
    assert [tool["name"] for tool in server.tools()] == [
        "omnibase_files_list",
        "omnibase_files_read",
        "omnibase_git_inspect",
        "omnibase_files_hash",
        "omnibase_text_search",
        "omnibase_git_diff_summary",
    ]
    git_tool = server.tools()[2]
    assert git_tool["inputSchema"]["properties"]["operation"]["enum"] == [
        "status",
        "log",
    ]


def test_file_hash_returns_digest_without_content(server: ReadOnlyMcpServer) -> None:
    result = server.call("omnibase_files_hash", {"path": "visible.txt"})
    assert result == {
        "path": "visible.txt",
        "algorithm": "sha256",
        "sha256": "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824",
        "bytes": 5,
    }
    assert "content" not in result


def test_literal_text_search_is_bounded_and_redacted(server: ReadOnlyMcpServer) -> None:
    nested = server.authorized_root / "src"
    nested.mkdir()
    (nested / "first.txt").write_text(
        "needle public\napi_key=do-not-return needle\n",
        encoding="utf-8",
    )
    (nested / "binary.bin").write_bytes(b"needle\x00binary")
    (nested / ".git").mkdir()
    (nested / ".git" / "hidden.txt").write_text("needle", encoding="utf-8")

    result = server.call("omnibase_text_search", {"path": ".", "query": "needle"})

    assert result["inspected_files"] == 3
    assert [match["path"] for match in result["matches"]] == [
        "src/first.txt",
        "src/first.txt",
    ]
    assert "do-not-return" not in str(result["matches"])
    assert ".git" not in str(result)


@pytest.mark.parametrize("query", ["x", "x" * 129, "bad\nquery"])
def test_text_search_rejects_invalid_query(server: ReadOnlyMcpServer, query: str) -> None:
    with pytest.raises(McpToolError, match="mcp_search_query_invalid"):
        server.call("omnibase_text_search", {"path": ".", "query": query})


def test_text_search_fails_closed_before_an_unbounded_tree_walk(
    server: ReadOnlyMcpServer,
) -> None:
    for index in range(3):
        (server.authorized_root / f"item-{index}.txt").write_text("needle", encoding="utf-8")
    with (
        patch("omnibase.mcp_runtime.readonly._MAX_SEARCH_ENTRIES", 2),
        pytest.raises(McpToolError, match="mcp_search_tree_too_large"),
    ):
        server.call("omnibase_text_search", {"path": ".", "query": "needle"})


def test_files_list_and_read_never_expose_env(server: ReadOnlyMcpServer) -> None:
    listing = server.call("omnibase_files_list", {"path": "."})
    assert [item["path"] for item in listing["entries"]] == ["visible.txt"]
    assert server.call("omnibase_files_read", {"path": "visible.txt"})["content"] == "hello"
    with pytest.raises(McpToolError, match="mcp_sensitive_file_forbidden"):
        server.call("omnibase_files_read", {"path": ".env"})


@pytest.mark.parametrize(
    "name",
    [
        ".git-credentials",
        ".git",
        ".hg",
        ".svn",
        ".netrc",
        ".npmrc",
        ".pypirc",
        "auth.json",
        "private.pem",
    ],
)
def test_extended_sensitive_file_closed_set_is_rejected(
    server: ReadOnlyMcpServer, name: str
) -> None:
    (server.authorized_root / name).write_text("secret", encoding="utf-8")
    listing = server.call("omnibase_files_list", {"path": "."})
    assert name not in [item["path"] for item in listing["entries"]]
    with pytest.raises(McpToolError, match="mcp_sensitive_file_forbidden"):
        server.call("omnibase_files_read", {"path": name})


def test_file_content_is_passed_through_secret_redaction(server: ReadOnlyMcpServer) -> None:
    path = server.authorized_root / "notes.txt"
    path.write_text("api_key=do-not-return\nmode=preview", encoding="utf-8")
    result = server.call("omnibase_files_read", {"path": "notes.txt"})
    assert "do-not-return" not in str(result["content"])
    assert "preview" in str(result["content"])


@pytest.mark.parametrize(
    "value",
    [("../visible.txt"), ("/visible.txt"), ("C:/visible.txt"), ("..\\visible.txt")],
)
def test_path_escape_forms_are_rejected(server: ReadOnlyMcpServer, value: str) -> None:
    with pytest.raises(McpToolError, match="mcp_path_invalid"):
        server.call("omnibase_files_read", {"path": value})


def test_symlink_or_junction_escape_is_rejected(server: ReadOnlyMcpServer, tmp_path: Path) -> None:
    link = server.authorized_root / "escape"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("link creation unavailable")
    with pytest.raises(McpToolError, match="mcp_path_link_forbidden"):
        server.call("omnibase_files_list", {"path": "escape"})


def test_git_operations_are_a_fixed_non_mutating_closed_set(server: ReadOnlyMcpServer) -> None:
    status = server.call("omnibase_git_inspect", {"operation": "status"})
    assert "tracked.txt" in str(status["output"])
    with pytest.raises(McpToolError, match="mcp_git_operation_invalid"):
        server.call("omnibase_git_inspect", {"operation": "reset"})
    for operation in ("diff", "cached_diff", "show"):
        with pytest.raises(McpToolError, match="mcp_git_operation_invalid"):
            server.call("omnibase_git_inspect", {"operation": operation})


def test_git_diff_summary_reports_only_metadata_for_worktree_and_staged(
    server: ReadOnlyMcpServer,
) -> None:
    subprocess.run(
        [str(server.git_executable), "-c", "core.autocrlf=false", "add", "tracked.txt"],
        cwd=server.repo_root,
        check=True,
    )
    subprocess.run(
        [
            str(server.git_executable),
            "-c",
            "user.name=OmniBase Test",
            "-c",
            "user.email=test@invalid.local",
            "commit",
            "--quiet",
            "-m",
            "baseline",
        ],
        cwd=server.repo_root,
        check=True,
    )
    (server.repo_root / "tracked.txt").write_text("worktree\nsecond\n", encoding="utf-8")

    worktree = server.call("omnibase_git_diff_summary", {"scope": "worktree"})
    assert worktree["files"] == [{"path": "tracked.txt", "status": "M", "added": 2, "deleted": 1}]
    assert "worktree\nsecond" not in str(worktree)

    subprocess.run(
        [str(server.git_executable), "-c", "core.autocrlf=false", "add", "tracked.txt"],
        cwd=server.repo_root,
        check=True,
    )
    staged = server.call("omnibase_git_diff_summary", {"scope": "staged"})
    assert staged["files"] == worktree["files"]
    assert server.call("omnibase_git_diff_summary", {"scope": "worktree"})["files"] == []


def test_git_diff_summary_rejects_arbitrary_scope_and_arguments(
    server: ReadOnlyMcpServer,
) -> None:
    for arguments in (
        {"scope": "HEAD~1"},
        {"scope": "worktree", "path": "."},
        {"scope": ["worktree"]},
    ):
        with pytest.raises(McpToolError, match="mcp_git_diff_scope_invalid"):
            server.call("omnibase_git_diff_summary", arguments)


def test_git_status_omits_sensitive_path_names(server: ReadOnlyMcpServer) -> None:
    for name in (".env", ".env.local", ".git-credentials", "private.pem"):
        (server.repo_root / name).write_text("secret", encoding="utf-8")
    output = str(server.call("omnibase_git_inspect", {"operation": "status"})["output"])
    assert "tracked.txt" in output
    assert ".env" not in output
    assert ".git-credentials" not in output
    assert "private.pem" not in output


def test_git_ignores_repository_fsmonitor_and_disables_optional_locks(
    server: ReadOnlyMcpServer,
) -> None:
    hook = server.repo_root / "fsmonitor-hook"
    marker = server.repo_root / "executed"
    hook.write_text(
        f"#!/bin/sh\necho executed > '{marker.as_posix()}'\nexit 1\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    subprocess.run(
        [str(server.git_executable), "config", "core.fsmonitor", str(hook)],
        cwd=server.repo_root,
        check=True,
    )
    server.call("omnibase_git_inspect", {"operation": "status"})
    assert not marker.exists()


def test_git_subprocess_environment_and_arguments_are_hardened(
    server: ReadOnlyMcpServer,
) -> None:
    real_popen = subprocess.Popen
    observed: dict[str, object] = {}

    def capture(*args: object, **kwargs: object):
        observed["command"] = args[0]
        observed["environment"] = kwargs["env"]
        return real_popen(*args, **kwargs)

    with patch("omnibase.mcp_runtime.readonly.subprocess.Popen", side_effect=capture):
        server.call("omnibase_git_inspect", {"operation": "status"})
    command = observed["command"]
    environment = observed["environment"]
    assert isinstance(command, list)
    assert "core.fsmonitor=false" in command
    assert "credential.helper=" in command
    assert isinstance(environment, dict)
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"


def test_each_call_revalidates_root_repo_and_git_identity(
    server: ReadOnlyMcpServer,
) -> None:
    drifted = server.git_executable_identity.__class__(
        device=server.git_executable_identity.device,
        inode=server.git_executable_identity.inode,
        mode=server.git_executable_identity.mode,
        size=server.git_executable_identity.size + 1,
        modified_ns=server.git_executable_identity.modified_ns,
        changed_ns=server.git_executable_identity.changed_ns,
    )
    object.__setattr__(server, "git_executable_identity", drifted)
    with pytest.raises(McpToolError, match="mcp_identity_drifted"):
        server.call("omnibase_files_list", {"path": "."})


def test_read_rejects_opened_handle_identity_drift(server: ReadOnlyMcpServer) -> None:
    other = server.authorized_root / "other.txt"
    other.write_text("changed", encoding="utf-8")

    with (
        patch(
            "omnibase.mcp_runtime.readonly._open_regular_file",
            side_effect=lambda path: open(other, "rb"),
        ),
        pytest.raises(McpToolError, match="mcp_file_identity_drifted"),
    ):
        server.call("omnibase_files_read", {"path": "visible.txt"})


def test_git_output_limit_is_enforced_while_process_is_running(
    server: ReadOnlyMcpServer,
) -> None:
    class FakePipe:
        def __init__(self, chunks: list[bytes]) -> None:
            self.chunks = iter(chunks)

        def read(self, size: int) -> bytes:
            return next(self.chunks, b"")

        def close(self) -> None:
            pass

    class FakeProcess:
        stdout = FakePipe([b"x" * 300_000, b"x" * 300_000])
        stderr = FakePipe([])
        killed = False

        def poll(self) -> None:
            return None

        def kill(self) -> None:
            self.killed = True

        def wait(self, timeout: float | None = None) -> int:
            return 0

    process = FakeProcess()
    with (
        patch("omnibase.mcp_runtime.readonly.subprocess.Popen", return_value=process),
        pytest.raises(McpToolError, match="mcp_git_output_too_large"),
    ):
        server.call("omnibase_git_inspect", {"operation": "status"})
    assert process.killed


def test_unknown_tool_and_extra_arguments_fail_closed(server: ReadOnlyMcpServer) -> None:
    with pytest.raises(McpToolError, match="mcp_tool_unknown"):
        server.call("shell", {})
    with pytest.raises(McpToolError, match="mcp_arguments_invalid"):
        server.call("omnibase_files_read", {"path": "visible.txt", "extra": True})


def test_process_lifetime_call_file_and_git_budgets_fail_closed(
    server: ReadOnlyMcpServer,
) -> None:
    server._budget.calls = readonly_mcp._MAX_PROCESS_CALLS
    with pytest.raises(McpToolError, match="mcp_process_call_budget_exhausted"):
        server.call("omnibase_files_list", {"path": "."})

    server._budget.calls = 0
    server._budget.file_bytes = readonly_mcp._MAX_PROCESS_FILE_BYTES
    with pytest.raises(McpToolError, match="mcp_process_file_budget_exhausted"):
        server.call("omnibase_files_read", {"path": "visible.txt"})

    server._budget.git_bytes = readonly_mcp._MAX_PROCESS_GIT_BYTES
    with pytest.raises(McpToolError, match="mcp_process_git_budget_exhausted"):
        server.call("omnibase_git_inspect", {"operation": "status"})
