from __future__ import annotations

import hashlib
import hmac
import sqlite3
from pathlib import Path

import pytest
import uvicorn
from fastapi.testclient import TestClient

from omnibase.desktop_local.app import (
    DESKTOP_CHALLENGE_HEADER,
    DESKTOP_INSTANCE_HEADER,
    DESKTOP_INSTANCE_TOKEN_ENV,
    DESKTOP_NATIVE_CONTROL_HEADER,
    DESKTOP_NATIVE_CONTROL_TOKEN_ENV,
    DESKTOP_NATIVE_PROOF_KEY_ENV,
    DESKTOP_PROOF_HEADER,
    DesktopLocalAppConfig,
    create_desktop_local_app,
    main,
)
from omnibase.desktop_local.config import DesktopLocalConfig

_TOKEN = "a" * 64
_SECOND_TOKEN = "b" * 64
_PROOF_KEY = "c" * 64
_CHALLENGE = "d" * 64
_CONTROL_TOKEN = "e" * 64


def _config(tmp_path: Path, *, token: str = _TOKEN) -> DesktopLocalAppConfig:
    return DesktopLocalAppConfig(
        storage=DesktopLocalConfig(
            data_root=tmp_path / "private-desktop-data",
            application_version="1.0.0",
        ),
        instance_token=token,
        native_proof_key=_PROOF_KEY,
        native_control_token=_CONTROL_TOKEN,
        port=47_431,
    )


def _headers(token: str = _TOKEN) -> dict[str, str]:
    return {DESKTOP_INSTANCE_HEADER: token}


def _native_headers(token: str = _CONTROL_TOKEN) -> dict[str, str]:
    return {DESKTOP_NATIVE_CONTROL_HEADER: token}


def test_lifespan_initializes_sqlite_and_health_is_redacted(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        health = client.get("/health", headers=_headers())
        ready = client.get("/health/ready", headers=_headers())

    assert health.status_code == 200
    assert DESKTOP_INSTANCE_HEADER not in health.headers
    assert health.json() == {
        "status": "ok",
        "service": "omnibase_desktop_local",
        "version": "1.0.0",
        "bind": "ipv4_loopback",
    }
    assert ready.status_code == 200
    assert DESKTOP_INSTANCE_HEADER not in ready.headers
    assert ready.json() == {
        "status": "ready",
        "storage": "sqlite",
        "schema_version": 3,
        "application_version": "1.0.0",
        "integrity": "ok",
    }
    rendered = health.text + ready.text
    assert str(config.storage.data_root) not in rendered
    assert config.storage.database_path.name not in rendered
    assert _TOKEN not in rendered
    assert config.storage.database_path.is_file()


def test_health_proof_uses_backend_only_key_and_exact_challenge(tmp_path: Path) -> None:
    expected = hmac.new(
        bytes.fromhex(_PROOF_KEY),
        _CHALLENGE.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        response = client.get(
            "/health",
            headers={**_headers(), DESKTOP_CHALLENGE_HEADER: _CHALLENGE},
        )
        ordinary = client.get("/health", headers=_headers())

    assert response.status_code == 200
    assert response.headers[DESKTOP_PROOF_HEADER] == expected
    assert response.headers["cache-control"] == "no-store"
    assert _TOKEN not in response.text
    assert _PROOF_KEY not in response.text
    assert DESKTOP_PROOF_HEADER not in ordinary.headers


@pytest.mark.parametrize("challenge", ["invalid", "A" * 64])
def test_health_rejects_noncanonical_native_challenge(tmp_path: Path, challenge: str) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        response = client.get(
            "/health",
            headers={**_headers(), DESKTOP_CHALLENGE_HEADER: challenge},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "desktop_challenge_invalid"
    assert DESKTOP_PROOF_HEADER not in response.headers


def test_native_challenge_is_confined_to_health_route(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        response = client.get(
            "/health/ready",
            headers={**_headers(), DESKTOP_CHALLENGE_HEADER: _CHALLENGE},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "desktop_challenge_invalid"
    assert DESKTOP_PROOF_HEADER not in response.headers


@pytest.mark.parametrize("token", [None, "", "a" * 63, "g" * 64, "A" * 64, _SECOND_TOKEN])
def test_every_route_requires_the_exact_single_instance_header(
    tmp_path: Path, token: str | None
) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        headers = {} if token is None else _headers(token)
        response = client.get("/health", headers=headers)

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "desktop_instance_unauthorized",
            "message": "Desktop instance authorization failed",
        }
    }
    assert DESKTOP_INSTANCE_HEADER not in response.headers
    assert _TOKEN not in response.text


def test_duplicate_instance_headers_fail_closed(tmp_path: Path) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        response = client.get(
            "/health",
            headers=[(DESKTOP_INSTANCE_HEADER, _TOKEN), (DESKTOP_INSTANCE_HEADER, _TOKEN)],
        )
    assert response.status_code == 401
    assert DESKTOP_INSTANCE_HEADER not in response.headers


def test_duplicate_or_noncanonical_native_control_headers_fail_closed(
    tmp_path: Path,
) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        duplicate = client.get(
            "/desktop/v1/owner",
            headers=[
                (DESKTOP_NATIVE_CONTROL_HEADER, _CONTROL_TOKEN),
                (DESKTOP_NATIVE_CONTROL_HEADER, _CONTROL_TOKEN),
            ],
        )
        uppercase = client.get(
            "/desktop/v1/owner",
            headers=_native_headers(_CONTROL_TOKEN.upper()),
        )
    for response in (duplicate, uppercase):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "desktop_native_control_unauthorized"
        assert _CONTROL_TOKEN not in response.text


def test_owner_bootstrap_is_idempotent_singleton_and_audited(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        before = client.get("/desktop/v1/owner", headers=_native_headers())
        first = client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_native_headers(),
            json={"display_name": "  Personal Owner  "},
        )
        replay = client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_native_headers(),
            json={"display_name": "A conflicting replay name"},
        )
        after = client.get("/desktop/v1/owner", headers=_native_headers())

    assert before.json() == {"initialized": False, "owner": None}
    assert first.status_code == 200
    assert first.json()["created"] is True
    assert first.json()["owner"]["display_name"] == "Personal Owner"
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["owner"] == first.json()["owner"]
    assert after.json() == {"initialized": True, "owner": first.json()["owner"]}

    connection = sqlite3.connect(config.storage.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM owner").fetchone()[0] == 1
        event = connection.execute("SELECT event_type, payload_json FROM audit_event").fetchone()
        assert event == (
            "owner_bootstrapped",
            '{"authority":"local_owner","source":"desktop_local"}',
        )
    finally:
        connection.close()


def test_owner_and_audit_survive_application_restart(tmp_path: Path) -> None:
    first_config = _config(tmp_path)
    with TestClient(create_desktop_local_app(first_config)) as first_client:
        created = first_client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_native_headers(),
            json={"display_name": "Persistent Owner"},
        ).json()

    restarted_config = _config(tmp_path, token=_SECOND_TOKEN)
    with TestClient(create_desktop_local_app(restarted_config)) as restarted_client:
        status = restarted_client.get("/desktop/v1/owner", headers=_native_headers())
        replay = restarted_client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_native_headers(),
            json={"display_name": "Ignored replay"},
        )

    assert status.json() == {"initialized": True, "owner": created["owner"]}
    assert replay.json()["created"] is False
    connection = sqlite3.connect(first_config.storage.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0] == 1
    finally:
        connection.close()


def test_native_control_is_separate_and_public_bootstrap_is_closed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        public_bootstrap = client.post(
            "/api/v1/owner/bootstrap",
            headers=_headers(),
            json={"display_name": "must-not-be-created"},
        )
        public_owner = client.get("/api/v1/owner", headers=_headers())
        native_root_without_control = client.get("/desktop/v1")
        native_unknown = client.get("/desktop/v1/unknown", headers=_native_headers())
        missing_control = client.post(
            "/desktop/v1/owner/bootstrap",
            json={"display_name": "must-not-be-created"},
        )
        instance_is_not_control = client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_headers(),
            json={"display_name": "must-not-be-created"},
        )
        mixed_identity = client.post(
            "/desktop/v1/owner/bootstrap",
            headers={**_headers(), **_native_headers()},
            json={"display_name": "must-not-be-created"},
        )
        control_on_public_route = client.get(
            "/health",
            headers={**_headers(), **_native_headers()},
        )

    assert public_bootstrap.status_code == 404
    assert public_bootstrap.json()["error"]["code"] == "desktop_not_found"
    assert public_owner.status_code == 404
    assert public_owner.json()["error"]["code"] == "desktop_not_found"
    assert native_root_without_control.status_code == 401
    assert (
        native_root_without_control.json()["error"]["code"] == "desktop_native_control_unauthorized"
    )
    assert native_unknown.status_code == 404
    assert native_unknown.json()["error"]["code"] == "desktop_not_found"
    for response in (missing_control, instance_is_not_control, mixed_identity):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "desktop_native_control_unauthorized"
        assert _CONTROL_TOKEN not in response.text
    assert control_on_public_route.status_code == 400
    assert control_on_public_route.json()["error"]["code"] == "desktop_native_control_invalid"

    connection = sqlite3.connect(config.storage.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM owner").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM audit_event").fetchone()[0] == 0
    finally:
        connection.close()


def test_workspace_create_list_archive_is_cas_guarded_audited_and_persistent(
    tmp_path: Path,
) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        before_owner = client.post(
            "/desktop/v1/workspaces",
            headers=_native_headers(),
            json={"name": "Rejected"},
        )
        owner = client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_native_headers(),
            json={"display_name": "Personal Owner"},
        ).json()["owner"]
        empty = client.get("/desktop/v1/workspaces", headers=_native_headers())
        first = client.post(
            "/desktop/v1/workspaces",
            headers=_native_headers(),
            json={"name": "  Primary Space  "},
        )
        second = client.post(
            "/desktop/v1/workspaces",
            headers=_native_headers(),
            json={"name": "Second Space"},
        )
        archived = client.post(
            f"/desktop/v1/workspaces/{first.json()['workspace']['id']}/archive",
            headers=_native_headers(),
            json={"expected_row_version": 1},
        )
        stale = client.post(
            f"/desktop/v1/workspaces/{first.json()['workspace']['id']}/archive",
            headers=_native_headers(),
            json={"expected_row_version": 1},
        )
        missing = client.post(
            f"/desktop/v1/workspaces/workspace_{'f' * 32}/archive",
            headers=_native_headers(),
            json={"expected_row_version": 1},
        )
        listed = client.get("/desktop/v1/workspaces", headers=_native_headers())

    assert before_owner.status_code == 409
    assert before_owner.json()["error"]["code"] == "desktop_owner_not_initialized"
    assert empty.json() == {"items": []}
    assert first.status_code == 200
    assert first.json()["workspace"]["owner_id"] == owner["id"]
    assert first.json()["workspace"]["name"] == "Primary Space"
    assert first.json()["workspace"]["state"] == "active"
    assert first.json()["workspace"]["row_version"] == 1
    assert second.status_code == 200
    assert archived.status_code == 200
    assert archived.json()["workspace"]["state"] == "archived"
    assert archived.json()["workspace"]["row_version"] == 2
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "desktop_workspace_version_conflict"
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "desktop_workspace_not_found"
    assert [item["name"] for item in listed.json()["items"]] == [
        "Second Space",
        "Primary Space",
    ]
    assert [item["state"] for item in listed.json()["items"]] == ["active", "archived"]

    restarted = _config(tmp_path, token=_SECOND_TOKEN)
    with TestClient(create_desktop_local_app(restarted)) as client:
        after_restart = client.get("/desktop/v1/workspaces", headers=_native_headers())
    assert after_restart.json() == listed.json()

    connection = sqlite3.connect(config.storage.database_path)
    try:
        events = connection.execute(
            "SELECT event_type, payload_json FROM audit_event ORDER BY sequence"
        ).fetchall()
    finally:
        connection.close()
    assert [event[0] for event in events] == [
        "owner_bootstrapped",
        "workspace_created",
        "workspace_created",
        "workspace_archived",
    ]
    assert events[1][1] == '{"row_version":1,"state":"active"}'
    assert "Primary Space" not in "".join(event[1] for event in events)


def test_workspace_archive_rolls_back_when_audit_append_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from omnibase.desktop_local import app as desktop_app

    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_native_headers(),
            json={"display_name": "Owner"},
        )
        created = client.post(
            "/desktop/v1/workspaces",
            headers=_native_headers(),
            json={"name": "Rollback Probe"},
        ).json()["workspace"]

        def fail_audit(*_args, **_kwargs):  # type: ignore[no-untyped-def]
            raise sqlite3.OperationalError("bounded audit failure")

        monkeypatch.setattr(desktop_app, "append_audit_event", fail_audit)
        failed = client.post(
            f"/desktop/v1/workspaces/{created['id']}/archive",
            headers=_native_headers(),
            json={"expected_row_version": 1},
        )
        listed = client.get("/desktop/v1/workspaces", headers=_native_headers())

    assert failed.status_code == 503
    assert failed.json()["error"]["code"] == "desktop_workspace_archive_failed"
    assert listed.json()["items"] == [created]

    connection = sqlite3.connect(config.storage.database_path)
    try:
        assert connection.execute(
            "SELECT state, row_version FROM workspace WHERE id = ?", (created["id"],)
        ).fetchone() == ("active", 1)
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_event WHERE event_type = 'workspace_archived'"
            ).fetchone()[0]
            == 0
        )
    finally:
        connection.close()


def test_workspace_capacity_is_bounded_without_partial_audit(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with TestClient(create_desktop_local_app(config)) as client:
        client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_native_headers(),
            json={"display_name": "Owner"},
        )
        for index in range(256):
            created = client.post(
                "/desktop/v1/workspaces",
                headers=_native_headers(),
                json={"name": f"Workspace {index + 1}"},
            )
            assert created.status_code == 200
        rejected = client.post(
            "/desktop/v1/workspaces",
            headers=_native_headers(),
            json={"name": "Workspace 257"},
        )

    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "desktop_workspace_capacity_reached"
    connection = sqlite3.connect(config.storage.database_path)
    try:
        assert connection.execute("SELECT COUNT(*) FROM workspace").fetchone()[0] == 256
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM audit_event WHERE event_type = 'workspace_created'"
            ).fetchone()[0]
            == 256
        )
    finally:
        connection.close()


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/auth/login",
        "/api/v1/documents",
        "/api/v1/rag/ask",
        "/api/v1/workspaces",
        "/api/v1/agent-alpha/invoke",
        "/api/v1/model-provider-credentials",
        "/api/v1/sandbox/run",
    ],
)
def test_postgresql_and_runtime_routes_remain_closed(tmp_path: Path, path: str) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        response = client.post(path, headers=_headers(), json={})
    assert response.status_code == 404
    assert response.json() == {
        "error": {
            "code": "desktop_not_found",
            "message": "Desktop request rejected",
        }
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"display_name": "   "},
        {"display_name": "Owner", "unexpected": "opaque-private-value"},
        {"display_name": "x" * 257},
    ],
)
def test_owner_validation_error_is_stable_and_does_not_echo_input(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    with TestClient(create_desktop_local_app(_config(tmp_path))) as client:
        response = client.post(
            "/desktop/v1/owner/bootstrap",
            headers=_native_headers(),
            json=payload,
        )
    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "desktop_validation_error",
            "message": "Desktop request validation failed",
        }
    }
    assert "opaque-private-value" not in response.text
    assert "x" * 50 not in response.text
    assert DESKTOP_INSTANCE_HEADER not in response.headers


@pytest.mark.parametrize(
    ("token", "host", "port", "code"),
    [
        ("a" * 63, "127.0.0.1", 47_431, "desktop_instance_token_must_be_64_hex"),
        ("g" * 64, "127.0.0.1", 47_431, "desktop_instance_token_must_be_64_hex"),
        ("A" * 64, "127.0.0.1", 47_431, "desktop_instance_token_must_be_64_hex"),
        (_TOKEN, "0.0.0.0", 47_431, "desktop_bind_host_must_be_ipv4_loopback"),
        (_TOKEN, "::1", 47_431, "desktop_bind_host_must_be_ipv4_loopback"),
        (_TOKEN, "127.0.0.1", 0, "desktop_port_out_of_range"),
        (_TOKEN, "127.0.0.1", 65_536, "desktop_port_out_of_range"),
    ],
)
def test_illegal_server_configuration_fails_before_startup(
    tmp_path: Path, token: str, host: str, port: int, code: str
) -> None:
    storage = DesktopLocalConfig(
        data_root=tmp_path / "must-not-be-created",
        application_version="1.0.0",
    )
    with pytest.raises(ValueError, match=f"^{code}$"):
        DesktopLocalAppConfig(
            storage=storage,
            instance_token=token,
            native_proof_key=_PROOF_KEY,
            native_control_token=_CONTROL_TOKEN,
            bind_host=host,
            port=port,
        )
    assert not storage.data_root.exists()


def test_server_rejects_noncanonical_application_version_before_startup(tmp_path: Path) -> None:
    storage = DesktopLocalConfig(
        data_root=tmp_path / "must-not-be-created",
        application_version="1.0.0\nprivate-marker",
    )
    with pytest.raises(ValueError, match="^desktop_application_version_invalid$"):
        DesktopLocalAppConfig(
            storage=storage,
            instance_token=_TOKEN,
            native_proof_key=_PROOF_KEY,
            native_control_token=_CONTROL_TOKEN,
        )
    assert not storage.data_root.exists()


def test_server_rejects_invalid_native_proof_key_before_startup(tmp_path: Path) -> None:
    storage = DesktopLocalConfig(
        data_root=tmp_path / "must-not-be-created",
        application_version="1.0.0",
    )
    with pytest.raises(ValueError, match="^desktop_native_proof_key_must_be_64_hex$"):
        DesktopLocalAppConfig(
            storage=storage,
            instance_token=_TOKEN,
            native_proof_key="invalid",
            native_control_token=_CONTROL_TOKEN,
        )
    assert not storage.data_root.exists()


def test_server_rejects_invalid_native_control_token_before_startup(tmp_path: Path) -> None:
    storage = DesktopLocalConfig(
        data_root=tmp_path / "must-not-be-created",
        application_version="1.0.0",
    )
    with pytest.raises(ValueError, match="^desktop_native_control_token_must_be_64_hex$"):
        DesktopLocalAppConfig(
            storage=storage,
            instance_token=_TOKEN,
            native_proof_key=_PROOF_KEY,
            native_control_token="invalid",  # noqa: S106 - malformed non-secret fixture
        )
    assert not storage.data_root.exists()


def test_unexpected_route_error_is_path_redacted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from omnibase.desktop_local import app as desktop_app

    config = _config(tmp_path)
    private_marker = str(config.storage.data_root / "must-never-leak")

    def fail_health(_connection: sqlite3.Connection):  # type: ignore[no-untyped-def]
        raise RuntimeError(private_marker)

    monkeypatch.setattr(desktop_app, "local_health", fail_health)
    with TestClient(create_desktop_local_app(config), raise_server_exceptions=False) as client:
        response = client.get("/health/ready", headers=_headers())

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "desktop_internal_error",
            "message": "Desktop local request failed",
        }
    }
    assert private_marker not in response.text
    assert DESKTOP_INSTANCE_HEADER not in response.headers


def test_cli_ignores_ambient_service_configuration_and_binds_loopback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://ambient-private-value")
    monkeypatch.setenv("OMNIBASE_DATA_ROOT", str(tmp_path / "ambient-data"))
    monkeypatch.setenv("UVICORN_HOST", "0.0.0.0")
    monkeypatch.setenv(DESKTOP_INSTANCE_TOKEN_ENV, _TOKEN)
    monkeypatch.setenv(DESKTOP_NATIVE_PROOF_KEY_ENV, _PROOF_KEY)
    monkeypatch.setenv(DESKTOP_NATIVE_CONTROL_TOKEN_ENV, _CONTROL_TOKEN)
    captured: dict[str, object] = {}

    def fake_run(application, **kwargs):  # type: ignore[no-untyped-def]
        captured["application"] = application
        captured.update(kwargs)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    explicit_root = tmp_path / "explicit-data"
    assert (
        main(
            [
                "--data-root",
                str(explicit_root),
                "--application-version",
                "1.0.0",
                "--port",
                "48123",
            ]
        )
        == 0
    )

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 48_123
    assert captured["workers"] == 1
    assert captured["reload"] is False
    assert captured["env_file"] is None
    assert captured["forwarded_allow_ips"] == ""
    assert captured["http"] == "h11"
    assert captured["lifespan"] == "on"
    assert captured["loop"] == "asyncio"
    assert captured["proxy_headers"] is False
    assert captured["server_header"] is False
    assert captured["ws"] == "none"
    assert not (tmp_path / "ambient-data").exists()
    assert not explicit_root.exists(), "CLI assembly must not initialize storage before lifespan"


def test_cli_rejects_token_argv_without_echoing_the_secret(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    argv_secret = "c" * 64
    monkeypatch.setenv(DESKTOP_INSTANCE_TOKEN_ENV, _TOKEN)
    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--data-root",
                str(tmp_path / "explicit-data"),
                "--instance-token",
                argv_secret,
            ]
        )
    assert caught.value.code == 2
    stderr = capsys.readouterr().err
    assert "desktop_runtime_secret_cli_forbidden" in stderr
    assert argv_secret not in stderr


def test_cli_requires_the_allowlisted_instance_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.delenv(DESKTOP_INSTANCE_TOKEN_ENV, raising=False)
    monkeypatch.setenv(DESKTOP_NATIVE_PROOF_KEY_ENV, _PROOF_KEY)
    monkeypatch.setenv(DESKTOP_NATIVE_CONTROL_TOKEN_ENV, _CONTROL_TOKEN)
    with pytest.raises(SystemExit) as caught:
        main(["--data-root", str(tmp_path / "explicit-data")])
    assert caught.value.code == 2
    assert "desktop_instance_token_environment_missing" in capsys.readouterr().err


def test_cli_requires_the_backend_only_native_proof_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(DESKTOP_INSTANCE_TOKEN_ENV, _TOKEN)
    monkeypatch.delenv(DESKTOP_NATIVE_PROOF_KEY_ENV, raising=False)
    monkeypatch.setenv(DESKTOP_NATIVE_CONTROL_TOKEN_ENV, _CONTROL_TOKEN)
    with pytest.raises(SystemExit) as caught:
        main(["--data-root", str(tmp_path / "explicit-data")])
    assert caught.value.code == 2
    assert "desktop_native_proof_key_environment_missing" in capsys.readouterr().err


def test_cli_requires_the_backend_only_native_control_environment(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv(DESKTOP_INSTANCE_TOKEN_ENV, _TOKEN)
    monkeypatch.setenv(DESKTOP_NATIVE_PROOF_KEY_ENV, _PROOF_KEY)
    monkeypatch.delenv(DESKTOP_NATIVE_CONTROL_TOKEN_ENV, raising=False)
    with pytest.raises(SystemExit) as caught:
        main(["--data-root", str(tmp_path / "explicit-data")])
    assert caught.value.code == 2
    assert "desktop_native_control_token_environment_missing" in capsys.readouterr().err
