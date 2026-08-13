"""Minimal stdio Model Context Protocol server for P6.1 read-only tools."""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from omnibase.mcp_runtime.readonly import McpToolError, ReadOnlyMcpServer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="OmniBase first-party read-only MCP")
    parser.add_argument("--authorized-root", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    return parser


def _response(
    identifier: object, *, result: object = None, error: object = None
) -> dict[str, object]:
    payload: dict[str, object] = {"jsonrpc": "2.0", "id": identifier}
    payload["error" if error is not None else "result"] = (
        error if error is not None else result
    )
    return payload


def _dispatch(server: ReadOnlyMcpServer, request: object) -> dict[str, object] | None:
    if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
        return _response(None, error={"code": -32600, "message": "invalid_request"})
    method = request.get("method")
    identifier = request.get("id")
    if identifier is None:
        return None
    if method == "initialize":
        return _response(
            identifier,
            result={
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "omnibase-readonly", "version": "0.1.0"},
            },
        )
    if method == "tools/list":
        return _response(identifier, result={"tools": server.tools()})
    if method == "tools/call":
        params = request.get("params")
        if not isinstance(params, dict):
            return _response(
                identifier, error={"code": -32602, "message": "invalid_params"}
            )
        try:
            result = server.call(
                str(params.get("name") or ""), params.get("arguments", {})
            )
        except McpToolError as exc:
            return _response(
                identifier,
                result={
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            )
        return _response(
            identifier,
            result={
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(result, ensure_ascii=False, sort_keys=True),
                    }
                ],
                "structuredContent": result,
                "isError": False,
            },
        )
    return _response(identifier, error={"code": -32601, "message": "method_not_found"})


def main() -> int:
    args = _parser().parse_args()
    git = shutil.which("git")
    if git is None:
        print("git_unavailable", file=sys.stderr)
        return 2
    try:
        server = ReadOnlyMcpServer.create(
            authorized_root=args.authorized_root,
            repo_root=args.repo_root,
            git_executable=Path(git),
        )
    except (OSError, McpToolError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            response = _response(None, error={"code": -32700, "message": "parse_error"})
        else:
            response = _dispatch(server, request)
        if response is not None:
            print(
                json.dumps(response, ensure_ascii=False, separators=(",", ":")),
                flush=True,
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
