"""Deterministic OpenAI-compatible provider for the P5.9P disposable journey.

The server is intentionally reachable only on the disposable Compose data
network.  It records counters and boolean prompt observations, never prompt
text, credentials or authorization material.
"""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

_MODEL_ID = "p5-9p-fake-model"
_LOCK = threading.Lock()
_STATS: dict[str, Any] = {
    "call_count": 0,
    "saw_memory_marker": False,
    "saw_skill_marker": False,
}


def _canonical(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _prompt_text(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return ""
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def _event(content: str, *, finish_reason: str | None = None) -> bytes:
    return (
        "data: "
        + json.dumps(
            {
                "id": "p5-9p-disposable",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": _MODEL_ID,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": content},
                        "finish_reason": finish_reason,
                    }
                ],
            },
            separators=(",", ":"),
        )
        + "\n\n"
    ).encode()


def _usage_event() -> bytes:
    return (
        "data: "
        + json.dumps(
            {
                "id": "p5-9p-disposable",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": _MODEL_ID,
                "choices": [],
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 6,
                    "total_tokens": 46,
                },
            },
            separators=(",", ":"),
        )
        + "\n\n"
    ).encode()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "OmniBaseP59PFake/1"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: HTTPStatus, payload: object) -> None:
        raw = _canonical(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if self.path == "/stats":
            with _LOCK:
                payload = dict(_STATS)
            self._json(HTTPStatus.OK, payload)
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})

    def _write_chunk(self, raw: bytes) -> None:
        self.wfile.write(f"{len(raw):X}\r\n".encode())
        self.wfile.write(raw)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    def do_POST(self) -> None:  # noqa: N802
        if self.path.rstrip("/") != "/v1/chat/completions":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
        except (ValueError, json.JSONDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid_request"})
            return
        prompt = _prompt_text(payload)
        with _LOCK:
            _STATS["call_count"] += 1
            _STATS["saw_memory_marker"] = bool(
                _STATS["saw_memory_marker"] or "P5_MEMORY_MARKER" in prompt
            )
            _STATS["saw_skill_marker"] = bool(
                _STATS["saw_skill_marker"] or "P5_SKILL_MARKER" in prompt
            )

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()

        if "P5_CRASH_HOLD" in prompt:
            parts = ("crash-first ",) + tuple(
                f"crash-hold-{index} " for index in range(80)
            )
            delay = 2.0
        elif "P5_CANCEL" in prompt:
            parts = ("cancel-first ", "cancel-second ", "cancel-third ")
            delay = 0.75
        elif "P5_RETRY_HOLD" in prompt:
            parts = ("retry-first ", "retry-second ", "retry-complete")
            delay = 1.0
        else:
            parts = ("P5.9 ", "personal ", "accepted")
            delay = 0.35
        try:
            for part in parts:
                self._write_chunk(_event(part))
                time.sleep(delay)
            self._write_chunk(_event("", finish_reason="stop"))
            self._write_chunk(_usage_event())
            self._write_chunk(b"data: [DONE]\n\n")
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), _Handler)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
