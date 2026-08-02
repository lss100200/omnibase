"""Protocol and fail-closed tests for the independent Linux Broker daemon."""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DAEMON_PATH = REPO_ROOT / "deployment/network-broker/omnibase-network-broker.py"
SERVICE_PATH = REPO_ROOT / "deployment/network-broker/omnibase-network-broker.service"


def _load_daemon() -> ModuleType:
    specification = importlib.util.spec_from_file_location(
        "omnibase_network_broker_deployment",
        DAEMON_PATH,
    )
    assert specification is not None
    assert specification.loader is not None
    module = importlib.util.module_from_spec(specification)
    sys.modules[specification.name] = module
    specification.loader.exec_module(module)
    return module


daemon = _load_daemon()

OPERATION_ID = UUID("60000000-0000-4000-8000-000000000001")
RUNTIME_ID = UUID("60000000-0000-4000-8000-000000000002")
NAMESPACE_ID = UUID("60000000-0000-4000-8000-000000000003")
SERVICE_ID = UUID("60000000-0000-4000-8000-000000000004")
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64


def _request(
    *, route_kind: str = "workspace_service", address: str = "11.0.0.2"
) -> dict[str, object]:
    return {
        "challenge": "f" * 64,
        "destination": {
            "address": address,
            "port": 8443,
            "protocol": "tcp",
            "resolution_digest": DIGEST_D,
            "route_kind": route_kind,
            "service_id": str(SERVICE_ID),
        },
        "namespace": {
            "evidence_digest": DIGEST_C,
            "namespace_id": str(NAMESPACE_ID),
            "network_namespace_identity": "2:4026533001",
            "namespace_process_id": os.getpid(),
            "namespace_process_start_time_ticks": 123456,
            "runtime_instance_id": str(RUNTIME_ID),
        },
        "plan": {
            "authorization_digest": DIGEST_B,
            "destination_resolution_digest": DIGEST_D,
            "namespace_evidence_digest": DIGEST_C,
            "operation_id": str(OPERATION_ID),
            "plan_digest": DIGEST_E,
            "request_binding_digest": DIGEST_A,
        },
        "protocol": "omnibase-broker-connect-v1",
    }


def _config(tmp_path: Path) -> object:
    authentication_key_path = tmp_path / "daemon-auth.key"
    authentication_key_path.write_text("11" * 32 + "\n", encoding="ascii")
    authentication_key_path.chmod(0o600)
    return daemon.BrokerConfig(
        socket_path=tmp_path / "socket/broker.sock",
        permit_directory=tmp_path / "permits",
        consumed_directory=tmp_path / "consumed",
        host_network_namespace_path=tmp_path / "host-net",
        daemon_authentication_key_path=authentication_key_path,
        daemon_uid=max(os.geteuid(), 1),
        trusted_client_uid=os.geteuid(),
        trusted_client_gid=os.getegid(),
        permit_owner_uid=os.geteuid(),
        host_namespace_owner_uid=os.geteuid(),
        max_request_bytes=65_536,
        connect_timeout_seconds=1.0,
        read_timeout_seconds=0.1,
    )


def _permit(request: object) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "address": str(request.address),
        "authorization_digest": request.authorization_digest,
        "destination_resolution_digest": request.destination_resolution_digest,
        "expires_at": (now + timedelta(minutes=1)).isoformat(),
        "max_bytes_in": 4,
        "max_bytes_out": 0,
        "max_connections": 1,
        "namespace_evidence_digest": request.namespace_evidence_digest,
        "namespace_id": str(request.namespace_id),
        "network_namespace_identity": request.network_namespace_identity,
        "namespace_process_id": request.namespace_process_id,
        "namespace_process_start_time_ticks": request.namespace_process_start_time_ticks,
        "network_protocol": request.network_protocol,
        "not_before": (now - timedelta(seconds=1)).isoformat(),
        "operation_id": str(request.operation_id),
        "plan_digest": request.plan_digest,
        "port": request.port,
        "protocol": "omnibase-broker-permit-v1",
        "request_binding_digest": request.request_binding_digest,
        "route_kind": request.route_kind,
        "runtime_instance_id": str(request.runtime_instance_id),
        "service_id": str(request.service_id),
    }


def test_request_accepts_only_bound_tcp_workspace_service() -> None:
    parsed = daemon.BrokerRequest.parse(_request())
    assert parsed.runtime_instance_id == RUNTIME_ID
    assert str(parsed.address) == "11.0.0.2"

    for route_kind in ("public_internet", "member_overlay"):
        with pytest.raises(daemon.BrokerRejected):
            daemon.BrokerRequest.parse(_request(route_kind=route_kind))
    for address in ("127.0.0.1", "169.254.169.254", "10.0.0.1", "::1", "fc00::1"):
        with pytest.raises(daemon.BrokerRejected):
            daemon.BrokerRequest.parse(_request(address=address))


def test_request_rejects_digest_and_shape_drift() -> None:
    mismatched = _request()
    destination = mismatched["destination"]
    assert isinstance(destination, dict)
    destination["resolution_digest"] = "f" * 64
    with pytest.raises(daemon.BrokerRejected):
        daemon.BrokerRequest.parse(mismatched)

    extra = _request()
    extra["credential"] = "must-never-cross"
    with pytest.raises(daemon.BrokerRejected):
        daemon.BrokerRequest.parse(extra)


def test_root_owned_short_lived_permit_binds_every_request_field(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.permit_directory.mkdir(mode=0o700)
    request = daemon.BrokerRequest.parse(_request())
    permit_path = config.permit_directory / f"{OPERATION_ID}.permit.json"
    permit_path.write_text(json.dumps(_permit(request)), encoding="utf-8")
    permit_path.chmod(0o600)

    loaded = daemon.BrokerPermit.load(config=config, request=request)
    assert loaded.max_connections == 1
    assert loaded.max_bytes_in == 4

    value = _permit(request)
    value["runtime_instance_id"] = str(UUID("60000000-0000-4000-8000-000000000099"))
    permit_path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(daemon.BrokerRejected, match="binding"):
        daemon.BrokerPermit.load(config=config, request=request)


def test_permit_rejects_long_lifetime_and_broad_file(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.permit_directory.mkdir(mode=0o700)
    request = daemon.BrokerRequest.parse(_request())
    value = _permit(request)
    now = datetime.now(UTC)
    value["not_before"] = (now - timedelta(seconds=1)).isoformat()
    value["expires_at"] = (now + timedelta(minutes=6)).isoformat()
    permit_path = config.permit_directory / f"{OPERATION_ID}.permit.json"
    permit_path.write_text(json.dumps(value), encoding="utf-8")
    permit_path.chmod(0o600)
    with pytest.raises(daemon.BrokerRejected, match="lifetime"):
        daemon.BrokerPermit.load(config=config, request=request)

    permit_path.chmod(0o666)
    with pytest.raises(daemon.BrokerRejected, match="metadata"):
        daemon.BrokerPermit.load(config=config, request=request)


def test_consumption_is_durable_and_never_replayed(tmp_path: Path) -> None:
    config = _config(tmp_path)
    config.consumed_directory.mkdir(mode=0o700)
    request = daemon.BrokerRequest.parse(_request())
    daemon._consume_operation(config, request)
    consumed = config.consumed_directory / f"{OPERATION_ID}.consumed.json"
    assert consumed.stat().st_mode & 0o777 == 0o600
    assert json.loads(consumed.read_text(encoding="utf-8"))["state"].startswith("consumed")
    with pytest.raises(daemon.BrokerRejected, match="replay"):
        daemon._consume_operation(config, request)


def test_consumption_handles_short_writes_and_fsyncs_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    config.consumed_directory.mkdir(mode=0o700)
    request = daemon.BrokerRequest.parse(_request())
    real_write = daemon.os.write
    real_fsync = daemon.os.fsync
    fsync_modes: list[int] = []

    def short_write(descriptor: int, payload: object) -> int:
        value = bytes(payload)
        return real_write(descriptor, value[: max(1, len(value) // 2)])

    def tracked_fsync(descriptor: int) -> None:
        fsync_modes.append(os.fstat(descriptor).st_mode)
        real_fsync(descriptor)

    monkeypatch.setattr(daemon.os, "write", short_write)
    monkeypatch.setattr(daemon.os, "fsync", tracked_fsync)
    daemon._consume_operation(config, request)

    consumed = config.consumed_directory / f"{OPERATION_ID}.consumed.json"
    assert json.loads(consumed.read_text(encoding="utf-8"))["operation_id"] == str(OPERATION_ID)
    assert any(stat.S_ISREG(mode) for mode in fsync_modes)
    assert any(stat.S_ISDIR(mode) for mode in fsync_modes)


def test_host_snapshot_uses_nofollow_and_same_fd_continuity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    snapshot_directory = tmp_path / "host-ns"
    snapshot_directory.mkdir(mode=0o755)
    snapshot = snapshot_directory / "net"
    snapshot.write_text("2:4026531992\n", encoding="ascii")
    snapshot.chmod(0o444)

    real_lstat = Path.lstat

    def root_owned_snapshot_parent(path: Path) -> os.stat_result:
        info = real_lstat(path)
        if path != snapshot_directory:
            return info
        values = list(info)
        values[4] = 0
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", root_owned_snapshot_parent)
    assert daemon._read_host_snapshot_identity(snapshot, owner_uid=os.geteuid()) == "2:4026531992"

    target = tmp_path / "attacker-net"
    target.write_text("3:4026532992\n", encoding="ascii")
    snapshot.unlink()
    snapshot.symlink_to(target)
    with pytest.raises(daemon.BrokerRejected, match="unavailable"):
        daemon._read_host_snapshot_identity(snapshot, owner_uid=os.geteuid())

    snapshot.unlink()
    snapshot.write_text("2:4026531992\n", encoding="ascii")
    snapshot.chmod(0o444)
    real_read = daemon.os.read

    def mutate_during_read(descriptor: int, maximum: int) -> bytes:
        value = real_read(descriptor, maximum)
        snapshot.chmod(0o600)
        snapshot.write_text("2:402653199200\n", encoding="ascii")
        snapshot.chmod(0o444)
        return value

    monkeypatch.setattr(daemon.os, "read", mutate_during_read)
    with pytest.raises(daemon.BrokerRejected, match="changed"):
        daemon._read_host_snapshot_identity(snapshot, owner_uid=os.geteuid())


def test_production_config_requires_private_root_owned_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_directory = tmp_path / "config"
    config_directory.mkdir(mode=0o750)
    key_path = config_directory / "daemon-auth.key"
    key_path.write_text("11" * 32 + "\n", encoding="ascii")
    key_path.chmod(0o440)
    value = {
        "connect_timeout_seconds": 2.0,
        "consumed_directory": str(tmp_path / "state/consumed"),
        "daemon_authentication_key_path": str(key_path),
        "daemon_uid": 999,
        "host_namespace_owner_uid": 0,
        "host_network_namespace_path": "/run/omnibase-host-ns/net",
        "max_request_bytes": 65_536,
        "permit_directory": str(tmp_path / "permits"),
        "permit_owner_uid": 0,
        "read_timeout_seconds": 0.25,
        "socket_path": str(tmp_path / "socket/broker.sock"),
        "trusted_client_gid": 0,
        "trusted_client_uid": 0,
    }
    config_path = config_directory / "config.json"
    config_path.write_text(json.dumps(value), encoding="utf-8")
    config_path.chmod(0o440)

    verify_directory = daemon._verify_directory
    read_bounded_json = daemon._read_bounded_json

    def verify_fixture_directory(
        path: Path,
        *,
        owner_uid: int,
        allow_group_read_execute: bool = False,
    ) -> None:
        assert owner_uid == 0
        verify_directory(
            path,
            owner_uid=os.geteuid(),
            allow_group_read_execute=allow_group_read_execute,
        )

    def read_fixture_config(
        path: Path,
        *,
        maximum: int,
        owner_uid: int,
    ) -> object:
        assert owner_uid == 0
        return read_bounded_json(path, maximum=maximum, owner_uid=os.geteuid())

    monkeypatch.setattr(daemon, "_verify_directory", verify_fixture_directory)
    monkeypatch.setattr(daemon, "_read_bounded_json", read_fixture_config)
    assert daemon.BrokerConfig.load(config_path).daemon_uid == 999

    config_directory.chmod(0o770)
    with pytest.raises(daemon.BrokerRejected, match="directory metadata"):
        daemon.BrokerConfig.load(config_path)


def test_systemd_profile_blocks_mount_syscalls_while_retaining_setns_capability() -> None:
    service = SERVICE_PATH.read_text(encoding="utf-8")
    assert "CapabilityBoundingSet=CAP_SYS_ADMIN CAP_SYS_PTRACE" in service
    filter_line = next(
        line for line in service.splitlines() if line.startswith("SystemCallFilter=~")
    )
    for syscall in (
        "mount",
        "umount2",
        "pivot_root",
        "fsopen",
        "fsmount",
        "fsconfig",
        "open_tree",
        "move_mount",
        "mount_setattr",
    ):
        assert syscall in filter_line.split()


@pytest.mark.skipif(os.name != "posix", reason="SO_PEERCRED is Linux/POSIX-only")
def test_server_rejects_socket_peer_uid_impersonation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        daemon.BrokerConfig,
        "load_authentication_key",
        lambda self: bytes.fromhex("11" * 32),
    )
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        accepted = daemon.NetworkBrokerDaemon(config)
        accepted._verify_peer(left)

        rejected_config = daemon.BrokerConfig(
            socket_path=config.socket_path,
            permit_directory=config.permit_directory,
            consumed_directory=config.consumed_directory,
            host_network_namespace_path=config.host_network_namespace_path,
            daemon_authentication_key_path=config.daemon_authentication_key_path,
            daemon_uid=config.daemon_uid,
            trusted_client_uid=os.geteuid() + 1,
            trusted_client_gid=config.trusted_client_gid,
            permit_owner_uid=config.permit_owner_uid,
            host_namespace_owner_uid=config.host_namespace_owner_uid,
            max_request_bytes=config.max_request_bytes,
            connect_timeout_seconds=config.connect_timeout_seconds,
            read_timeout_seconds=config.read_timeout_seconds,
        )
        with pytest.raises(daemon.BrokerRejected, match="peer"):
            daemon.NetworkBrokerDaemon(rejected_config)._verify_peer(right)
    finally:
        left.close()
        right.close()
