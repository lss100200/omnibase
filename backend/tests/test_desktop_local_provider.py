from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import threading
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
from omnibase.desktop_local.family import resolve_desktop_family

_CONTROL = "e" * 64
_TOKEN = "a" * 64
_PROOF = "c" * 64
_ISOLATION_SECRET = "isolation-provider-secret-not-for-git"
_BLOB = "ZW5jcnlwdGVkLXNhZmVzdG9yYWdlLWJsb2I"
_FINGERPRINT = hashlib.sha256(_ISOLATION_SECRET.encode("utf-8")).hexdigest()


def _config(tmp_path: Path) -> DesktopLocalAppConfig:
    return DesktopLocalAppConfig(
        storage=DesktopLocalConfig(
            data_root=tmp_path / "private-desktop-data",
            application_version="1.0.0",
        ),
        instance_token=_TOKEN,
        native_proof_key=_PROOF,
        native_control_token=_CONTROL,
        port=47_431,
    )


def _native() -> dict[str, str]:
    return {DESKTOP_NATIVE_CONTROL_HEADER: _CONTROL}


def _bootstrap(client: TestClient) -> None:
    assert (
        client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_native(),
            json={"display_name": "Local Owner"},
        ).status_code
        == 200
    )


def _provider_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "display_name": "Loopback model",
        "base_url": "http://127.0.0.1:9/v1",
        "model_name": "deepseek-chat",
        "gear": "standard",
        "thinking_depth": "medium",
        "timeout_seconds": 15,
        "allow_loopback_http": True,
        "is_default": True,
        "is_enabled": True,
        "credential_reference": "electron-safe-storage:v1",
        "encrypted_secret_blob": _BLOB,
        "secret_fingerprint": _FINGERPRINT,
    }
    payload.update(overrides)
    return payload


class _FakeOpenAIHandler(BaseHTTPRequestHandler):
    status = 200
    body = b""
    stream = False
    last_authorization = ""
    last_path = ""

    def log_message(self, *_args: object) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        type(self).last_authorization = self.headers.get("Authorization", "")
        type(self).last_path = self.path
        self.rfile.read(length)
        self.send_response(self.status)
        if self.stream and self.status < 400:
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(self.body)
            return
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)


def _serve(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def _stop(server: ThreadingHTTPServer) -> None:
    server.shutdown()
    server.server_close()


def test_desktop_local_sources_do_not_import_frozen_excludes() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "omnibase" / "desktop_local"
    forbidden = {
        "httpx",
        "openai",
        "cryptography",
        "sqlalchemy",
        "psycopg",
        "alembic",
        "celery",
    }
    imported: set[str] = set()
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
    assert imported.isdisjoint(forbidden)


@pytest.mark.parametrize(
    ("model_id", "base_url", "family"),
    [
        ("deepseek-v4-flash", "https://relay.example/openai/v1", "deepseek"),
        ("gpt-5.6-luna", "https://api.deepseek.com/v1", "openai"),
        ("claude-sonnet-4", "https://open.bigmodel.cn/api/paas/v4", "anthropic"),
        ("glm-4.7-flashx", "https://api.openai.com/v1", "glm"),
        ("kimi-k2", "https://example.invalid/v1", "kimi"),
        ("my-oss-llama", "https://api.openai.com/v1", "openai"),
        ("local-custom-7b", "http://127.0.0.1:11434/v1", "generic-openai-compatible"),
    ],
)
def test_model_name_wins_and_unknown_names_are_generic(
    model_id: str,
    base_url: str,
    family: str,
) -> None:
    assert resolve_desktop_family(model_id, base_url) == family


def test_provider_upsert_list_and_sqlite_never_store_plaintext(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        _bootstrap(client)
        created = client.post(
            "/desktop/v1/providers",
            headers=_native(),
            json=_provider_payload(),
        )
        listed = client.get("/desktop/v1/providers", headers=_native())
        vault = client.get(
            f"/desktop/v1/providers/{created.json()['provider']['id']}/vault",
            headers=_native(),
        )

    assert created.status_code == 200
    provider = created.json()["provider"]
    assert provider["family"] == "deepseek"
    assert provider["has_secret"] is True
    assert "encrypted_secret_blob" not in provider
    assert "secret_fingerprint" not in json.dumps(listed.json())
    assert _ISOLATION_SECRET not in json.dumps(created.json())
    assert vault.json()["encrypted_secret_blob"] == _BLOB
    assert vault.json()["credential_reference"] == "electron-safe-storage:v1"

    raw = (
        sqlite3.connect(config.storage.database_path)
        .execute("SELECT encrypted_secret_blob, secret_fingerprint FROM provider")
        .fetchone()
    )
    assert raw[0] == _BLOB
    assert raw[1] == _FINGERPRINT
    dump = Path(config.storage.database_path).read_bytes()
    assert _ISOLATION_SECRET.encode("utf-8") not in dump
    assert b"Bearer " not in dump


def test_remote_http_and_userinfo_and_private_urls_are_rejected(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        _bootstrap(client)
        http_remote = client.post(
            "/desktop/v1/providers",
            headers=_native(),
            json=_provider_payload(
                base_url="http://example.com/v1",
                allow_loopback_http=False,
            ),
        )
        userinfo = client.post(
            "/desktop/v1/providers",
            headers=_native(),
            json=_provider_payload(
                base_url="https://user:pass@api.example.com/v1",
                allow_loopback_http=False,
            ),
        )
        query = client.post(
            "/desktop/v1/providers",
            headers=_native(),
            json=_provider_payload(
                base_url="https://api.example.com/v1?api_key=secret",
                allow_loopback_http=False,
            ),
        )
        lan = client.post(
            "/desktop/v1/providers",
            headers=_native(),
            json=_provider_payload(
                base_url="http://192.168.1.10/v1",
                allow_loopback_http=True,
            ),
        )
    assert http_remote.status_code == 422
    assert userinfo.status_code == 422
    assert query.status_code == 422
    assert lan.status_code == 422
    for response in (http_remote, userinfo, query, lan):
        rendered = response.text
        assert "pass" not in rendered
        assert "secret" not in rendered
        assert "192.168.1.10" not in rendered


def test_provider_test_uses_loopback_fake_server_and_redacts_errors(tmp_path: Path) -> None:
    _FakeOpenAIHandler.status = 200
    _FakeOpenAIHandler.stream = False
    _FakeOpenAIHandler.body = json.dumps(
        {"id": "chatcmpl-1", "model": "deepseek-chat", "choices": [{"message": {"content": "ok"}}]}
    ).encode("utf-8")
    server = _serve(_FakeOpenAIHandler)
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
            _bootstrap(client)
            created = client.post(
                "/desktop/v1/providers",
                headers=_native(),
                json=_provider_payload(base_url=base_url),
            )
            provider_id = created.json()["provider"]["id"]
            ok = client.post(
                f"/desktop/v1/providers/{provider_id}/test",
                headers=_native(),
                json={"secret": _ISOLATION_SECRET},
            )
            _FakeOpenAIHandler.status = 401
            _FakeOpenAIHandler.body = (
                b'{"error":{"message":"invalid api key isolation-provider-secret-not-for-git"}}'
            )
            denied = client.post(
                f"/desktop/v1/providers/{provider_id}/test",
                headers=_native(),
                json={"secret": _ISOLATION_SECRET},
            )
    finally:
        _stop(server)

    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert ok.json()["actual_model"] == "deepseek-chat"
    assert denied.json()["ok"] is False
    assert denied.json()["error_code"] == "desktop_provider_unauthorized"
    assert _ISOLATION_SECRET not in denied.text
    assert _FakeOpenAIHandler.last_authorization == f"Bearer {_ISOLATION_SECRET}"
    assert _FakeOpenAIHandler.last_path.endswith("/chat/completions")
    assert ok.json()["identity_proven"] is True


def test_dns_rebind_second_lookup_cannot_connect_to_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    from omnibase.desktop_local.endpoint import resolve_provider_endpoint
    from omnibase.desktop_local.provider_http import DesktopProviderCallError, _open_connection

    lookups: list[str] = []
    connected: list[str] = []

    def fake_getaddrinfo(host: str, port: int, *args: object, **kwargs: object) -> list[tuple]:
        lookups.append(str(host))
        if lookups.count(str(host)) == 1:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.1.1", port))]

    def fake_create_connection(
        address: tuple[object, ...], timeout: object = None, **kwargs: object
    ):
        host = str(address[0])
        connected.append(host)
        if host.startswith(("192.168.", "10.", "172.16.")):
            raise AssertionError("connected to private address after DNS rebind")
        raise OSError("pinned public connect failed closed")

    monkeypatch.setattr("omnibase.desktop_local.endpoint.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(
        "omnibase.desktop_local.provider_http.socket.create_connection",
        fake_create_connection,
    )
    endpoint = resolve_provider_endpoint("https://rebind.example/v1", allow_loopback_http=False)
    assert endpoint.hostname == "rebind.example"
    assert endpoint.connect_host == "93.184.216.34"
    assert endpoint.connect_addrs == ("93.184.216.34",)
    with pytest.raises(DesktopProviderCallError, match="desktop_provider_unreachable"):
        _open_connection(endpoint, 1.0)
    assert connected == ["93.184.216.34"]
    assert "192.168.1.1" not in connected
    assert lookups.count("rebind.example") == 1


def test_provider_test_empty_json_object_is_not_success(tmp_path: Path) -> None:
    _FakeOpenAIHandler.status = 200
    _FakeOpenAIHandler.stream = False
    _FakeOpenAIHandler.body = b"{}"
    server = _serve(_FakeOpenAIHandler)
    try:
        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
            _bootstrap(client)
            created = client.post(
                "/desktop/v1/providers",
                headers=_native(),
                json=_provider_payload(base_url=base_url),
            )
            provider_id = created.json()["provider"]["id"]
            result = client.post(
                f"/desktop/v1/providers/{provider_id}/test",
                headers=_native(),
                json={"secret": _ISOLATION_SECRET},
            )
            _FakeOpenAIHandler.body = json.dumps(
                {"choices": [{"message": {"role": "assistant", "content": "pong"}}]}
            ).encode("utf-8")
            unproven = client.post(
                f"/desktop/v1/providers/{provider_id}/test",
                headers=_native(),
                json={"secret": _ISOLATION_SECRET},
            )
    finally:
        _stop(server)

    assert result.status_code == 200
    assert result.json()["ok"] is False
    assert result.json()["error_code"] == "desktop_provider_response_invalid"
    assert _ISOLATION_SECRET not in result.text
    assert unproven.json()["ok"] is True
    assert unproven.json()["identity_proven"] is False
    assert unproven.json()["actual_model"] is None
    assert unproven.json()["requested_model"] == "deepseek-chat"


def test_disabled_provider_vault_is_bound_to_the_same_snapshot(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        _bootstrap(client)
        created = client.post(
            "/desktop/v1/providers",
            headers=_native(),
            json=_provider_payload(),
        )
        provider_id = created.json()["provider"]["id"]
        enabled_vault = client.get(
            f"/desktop/v1/providers/{provider_id}/vault",
            headers=_native(),
        )
        listed = client.get("/desktop/v1/providers", headers=_native())
        assert listed.json()["items"][0]["is_enabled"] is True
        disabled = client.post(
            "/desktop/v1/providers",
            headers=_native(),
            json=_provider_payload(id=provider_id, is_enabled=False),
        )
        assert disabled.status_code == 200
        assert disabled.json()["provider"]["is_enabled"] is False
        vault = client.get(
            f"/desktop/v1/providers/{provider_id}/vault",
            headers=_native(),
        )
    assert enabled_vault.status_code == 200
    assert enabled_vault.json()["encrypted_secret_blob"] == _BLOB
    assert vault.status_code == 409
    assert vault.json()["error"]["code"] == "desktop_provider_disabled"
    assert "encrypted_secret_blob" not in vault.text
    assert _BLOB not in vault.text


def test_sse_model_drift_mid_stream_fails_closed_not_success() -> None:
    _FakeOpenAIHandler.status = 200
    _FakeOpenAIHandler.stream = True
    _FakeOpenAIHandler.body = (
        b'data: {"model":"deepseek-chat","choices":[{"delta":{"content":"hel"}}]}\n\n'
        b'data: {"model":"other-model","choices":[{"delta":{"content":"lo"}}]}\n\n'
        b"data: [DONE]\n\n"
    )
    server = _serve(_FakeOpenAIHandler)
    try:
        from omnibase.desktop_local.endpoint import resolve_provider_endpoint
        from omnibase.desktop_local.family import plan_desktop_adaptation
        from omnibase.desktop_local.provider_http import (
            ChatMessage,
            DesktopProviderCallError,
            stream_provider_chat,
        )

        base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
        endpoint = resolve_provider_endpoint(base_url, allow_loopback_http=True)
        adaptation = plan_desktop_adaptation("deepseek-chat", base_url, "standard", "medium")
        with pytest.raises(DesktopProviderCallError, match="desktop_provider_model_identity_drift"):
            list(
                stream_provider_chat(
                    endpoint,
                    secret=_ISOLATION_SECRET,
                    model_name="deepseek-chat",
                    messages=(ChatMessage(role="user", content="hi"),),
                    adaptation=adaptation,
                    timeout_seconds=5.0,
                    cancelled=lambda: False,
                )
            )
    finally:
        _stop(server)
        _FakeOpenAIHandler.stream = False
