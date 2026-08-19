from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from omnibase.desktop_local.app import (
    DESKTOP_NATIVE_CONTROL_HEADER,
    DesktopLocalAppConfig,
    create_desktop_local_app,
)
from omnibase.desktop_local.config import DesktopLocalConfig
from omnibase.desktop_local.endpoint import resolve_provider_endpoint
from omnibase.desktop_local.family import plan_desktop_adaptation
from omnibase.desktop_local.provider_http import (
    ChatMessage,
    DesktopProviderCallError,
    stream_provider_chat,
)

_CONTROL = "e" * 64
_TOKEN = "a" * 64
_PROOF = "c" * 64
_SECRET = "isolation-stream-secret"
_BLOB = "c3RyZWFtLWVuY3J5cHRlZC1ibG9i"
_FINGERPRINT = "a" * 64


def _config(tmp_path: Path) -> DesktopLocalAppConfig:
    return DesktopLocalAppConfig(
        storage=DesktopLocalConfig(
            data_root=tmp_path / "conversation-data",
            application_version="1.0.0",
        ),
        instance_token=_TOKEN,
        native_proof_key=_PROOF,
        native_control_token=_CONTROL,
        port=47_431,
    )


def _native() -> dict[str, str]:
    return {DESKTOP_NATIVE_CONTROL_HEADER: _CONTROL}


class _StreamHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    chunks = (
        ('data: {"model":"loopback-chat","choices":[{"delta":{"content":"你好"}}]}\n\n').encode(),
        ('data: {"model":"loopback-chat","choices":[{"delta":{"content":"世界"}}]}\n\n').encode(),
        b"data: [DONE]\n\n",
    )
    hang = False
    last_body = b""

    def log_message(self, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        type(self).last_body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        if self.hang:
            deadline = time.monotonic() + 20
            while type(self).hang and time.monotonic() < deadline:
                time.sleep(0.05)
            return
        for chunk in self.chunks:
            self.wfile.write(chunk)
            self.wfile.flush()


def _serve() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _StreamHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _stop(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


def _start_workspace(client: TestClient, base_url: str) -> tuple[str, str]:
    owner = client.post(
        "/desktop/v1/owner/bootstrap",
        headers=_native(),
        json={"display_name": "对话用户"},
    )
    assert owner.status_code == 200
    workspace = client.post(
        "/desktop/v1/workspaces",
        headers=_native(),
        json={"name": "对话空间"},
    )
    workspace_id = workspace.json()["workspace"]["id"]
    provider = client.post(
        "/desktop/v1/providers",
        headers=_native(),
        json={
            "display_name": "本地模型",
            "base_url": base_url,
            "model_name": "loopback-chat",
            "gear": "standard",
            "thinking_depth": "low",
            "timeout_seconds": 15,
            "allow_loopback_http": True,
            "is_default": True,
            "is_enabled": True,
            "credential_reference": "electron-safe-storage:v1",
            "encrypted_secret_blob": _BLOB,
            "secret_fingerprint": _FINGERPRINT,
        },
    )
    assert provider.status_code == 200
    conversation = client.post(
        f"/desktop/v1/workspaces/{workspace_id}/conversations",
        headers=_native(),
        json={"title": "新会话"},
    )
    return workspace_id, conversation.json()["conversation"]["id"]


def test_parent_agent_exists_and_conversation_round_trip_streams(tmp_path: Path) -> None:
    _StreamHandler.hang = False
    server = _serve()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
            workspace_id, conversation_id = _start_workspace(client, base_url)
            agent = client.get(
                f"/desktop/v1/workspaces/{workspace_id}/agent",
                headers=_native(),
            )
            assert agent.json()["agent"]["role"] == "parent"
            with client.stream(
                "POST",
                f"/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages",
                headers=_native(),
                json={"secret": _SECRET, "content": "请介绍你自己"},
            ) as stream:
                body = b"".join(stream.iter_bytes()).decode("utf-8")
            restored = client.get(
                f"/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}",
                headers=_native(),
            )
    finally:
        _stop(server)

    assert "event: delta" in body
    assert "你好" in body
    assert "世界" in body
    assert "event: done" in body
    messages = restored.json()["messages"]
    assert messages[0]["role"] == "user"
    assert messages[0]["content"] == "请介绍你自己"
    assert messages[1]["content"] == "你好世界"
    assert messages[1]["status"] == "completed"
    assert messages[1]["invocation"]["status"] == "succeeded"
    assert messages[1]["invocation"]["requested_model"] == "loopback-chat"
    assert messages[1]["invocation"]["actual_model"] == "loopback-chat"
    payload = json.loads(_StreamHandler.last_body.decode("utf-8"))
    assert payload["messages"][0]["role"] == "system"
    assert payload["messages"][-1] == {"role": "user", "content": "请介绍你自己"}


def test_cancel_stops_running_invocation_and_restart_does_not_replay(tmp_path: Path) -> None:
    _StreamHandler.hang = True
    server = _serve()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        endpoint = resolve_provider_endpoint(base_url, allow_loopback_http=True)
        cancelled = threading.Event()
        threading.Timer(0.3, cancelled.set).start()
        with pytest.raises(DesktopProviderCallError) as caught:
            list(
                stream_provider_chat(
                    endpoint,
                    secret=_SECRET,
                    model_name="loopback-chat",
                    messages=(ChatMessage(role="user", content="请开始长回答"),),
                    adaptation=plan_desktop_adaptation(
                        "loopback-chat",
                        base_url,
                        "standard",
                        "low",
                    ),
                    timeout_seconds=5,
                    cancelled=cancelled.is_set,
                )
            )
        assert caught.value.code == "desktop_invocation_cancelled"
        assert "生成已停止" in caught.value.public_message
    finally:
        _StreamHandler.hang = False
        _stop(server)

    config = _config(tmp_path)

    def insert_running() -> str:
        connection = sqlite3.connect(config.storage.database_path)
        connection.row_factory = sqlite3.Row
        try:
            owner_id = str(connection.execute("SELECT id FROM owner").fetchone()[0])
            provider_id = str(connection.execute("SELECT id FROM provider").fetchone()[0])
            invocation_id = f"invocation_{uuid.uuid4().hex}"
            message_id = f"message_{uuid.uuid4().hex}"
            now = "2026-08-19T15:00:00Z"
            connection.execute(
                "INSERT INTO invocation ("
                "id, owner_id, workspace_id, conversation_id, provider_id, requested_model, "
                "actual_model, family, gear, thinking_depth, status, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, 'loopback-chat', NULL, 'generic-openai-compatible', "
                "'standard', 'low', 'running', ?, ?)",
                (
                    invocation_id,
                    owner_id,
                    workspace_id,
                    conversation_id,
                    provider_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO message ("
                "id, owner_id, workspace_id, conversation_id, role, content, status, "
                "invocation_id, created_at"
                ") VALUES (?, ?, ?, ?, 'assistant', '', 'streaming', ?, ?)",
                (message_id, owner_id, workspace_id, conversation_id, invocation_id, now),
            )
            connection.commit()
            return invocation_id
        finally:
            connection.close()

    _StreamHandler.hang = False
    server = _serve()
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        with TestClient(create_desktop_local_app(config)) as client:
            workspace_id, conversation_id = _start_workspace(client, base_url)
            with client.stream(
                "POST",
                f"/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}/messages",
                headers=_native(),
                json={"secret": _SECRET, "content": "请介绍你自己"},
            ) as stream:
                body = b"".join(stream.iter_bytes()).decode("utf-8")
            assert "event: done" in body
            snapshot = client.get(
                f"/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}",
                headers=_native(),
            )
            assert snapshot.json()["messages"][1]["invocation"]["status"] == "succeeded"
            cancel_id = insert_running()
            cancelled = client.post(
                f"/desktop/v1/invocations/{cancel_id}/cancel",
                headers=_native(),
            )
            assert cancelled.json()["accepted"] is True
            snapshot = client.get(
                f"/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}",
                headers=_native(),
            )
            targeted = [
                item
                for item in snapshot.json()["messages"]
                if item.get("invocation") and item["invocation"]["id"] == cancel_id
            ]
            assert targeted[0]["status"] == "cancelled"
            assert targeted[0]["invocation"]["status"] == "cancelled"
            assert targeted[0]["invocation"]["error_redacted"] == "生成已停止"
    finally:
        _stop(server)

    recover_id = insert_running()
    with TestClient(create_desktop_local_app(config)) as restarted:
        restored = restarted.get(
            f"/desktop/v1/workspaces/{workspace_id}/conversations/{conversation_id}",
            headers=_native(),
        )
    recovered = [
        item
        for item in restored.json()["messages"]
        if item.get("invocation") and item["invocation"]["id"] == recover_id
    ]
    assert recovered[0]["invocation"]["status"] == "unknown"
    assert recovered[0]["invocation"]["error_code"] == "desktop_invocation_interrupted"
    assert "AbortError" not in json.dumps(restored.json())
