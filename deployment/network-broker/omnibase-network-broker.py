#!/usr/bin/env python3
"""Fail-closed P34.5B Linux network Broker daemon.

The daemon accepts the bounded ``omnibase-broker-connect-v1`` protocol on one
private AF_UNIX socket.  A request is not authorization: before any network
side effect the daemon requires a short-lived root/control-plane permit that
binds the exact plan, logical service destination, workload network namespace
and byte limits.  It then consumes the operation durably, forks a bounded
worker, enters the already-open trusted nsfs handle and establishes one TCP
connection from that namespace.

The daemon deliberately does not resolve DNS, accept arbitrary payloads, join
an Overlay, or access PostgreSQL/Redis/MinIO.  Its initial systemd namespace is
network-empty; only the forked worker enters an explicitly permitted workload
network namespace.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import ipaddress
import json
import os
import select
import signal
import socket
import stat
import struct
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType
from typing import Final
from uuid import UUID

_PROTOCOL: Final = "omnibase-broker-connect-v1"
_PERMIT_PROTOCOL: Final = "omnibase-broker-permit-v1"
_MAX_CONFIG_BYTES: Final = 65_536
_MAX_PERMIT_BYTES: Final = 65_536
_MAX_RESULT_BYTES: Final = 65_536
_SHA256_LENGTH: Final = 64

_REQUEST_KEYS: Final = {"challenge", "destination", "namespace", "plan", "protocol"}
_DESTINATION_KEYS: Final = {
    "address",
    "port",
    "protocol",
    "resolution_digest",
    "route_kind",
    "service_id",
}
_NAMESPACE_KEYS: Final = {
    "evidence_digest",
    "namespace_id",
    "network_namespace_identity",
    "namespace_process_id",
    "namespace_process_start_time_ticks",
    "runtime_instance_id",
}
_PLAN_KEYS: Final = {
    "authorization_digest",
    "destination_resolution_digest",
    "namespace_evidence_digest",
    "operation_id",
    "plan_digest",
    "request_binding_digest",
}
_PERMIT_KEYS: Final = {
    "address",
    "authorization_digest",
    "destination_resolution_digest",
    "expires_at",
    "max_bytes_in",
    "max_bytes_out",
    "max_connections",
    "namespace_evidence_digest",
    "namespace_id",
    "network_namespace_identity",
    "namespace_process_id",
    "namespace_process_start_time_ticks",
    "network_protocol",
    "not_before",
    "operation_id",
    "plan_digest",
    "port",
    "protocol",
    "request_binding_digest",
    "route_kind",
    "runtime_instance_id",
    "service_id",
}
_CONFIG_KEYS: Final = {
    "connect_timeout_seconds",
    "consumed_directory",
    "daemon_authentication_key_path",
    "daemon_uid",
    "host_namespace_owner_uid",
    "host_network_namespace_path",
    "max_request_bytes",
    "permit_directory",
    "permit_owner_uid",
    "read_timeout_seconds",
    "socket_path",
    "trusted_client_gid",
    "trusted_client_uid",
}


class BrokerRejected(Exception):
    """A generic fail-closed rejection safe to expose as one error class."""


def _strict_mapping(value: object, *, keys: set[str]) -> Mapping[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise BrokerRejected("invalid object shape")
    return value


def _strict_int(value: object, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise BrokerRejected("invalid integer")
    if value < minimum or value > maximum:
        raise BrokerRejected("integer outside bounds")
    return value


def _strict_float(value: object, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BrokerRejected("invalid number")
    result = float(value)
    if result < minimum or result > maximum:
        raise BrokerRejected("number outside bounds")
    return result


def _strict_uuid(value: object) -> UUID:
    try:
        parsed = UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise BrokerRejected("invalid UUID") from exc
    if str(parsed) != str(value).lower():
        raise BrokerRejected("UUID is not canonical")
    return parsed


def _strict_digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != _SHA256_LENGTH:
        raise BrokerRejected("invalid digest")
    if any(character not in "0123456789abcdef" for character in value):
        raise BrokerRejected("invalid digest")
    return value


def _strict_namespace_identity(value: object) -> str:
    if not isinstance(value, str):
        raise BrokerRejected("invalid namespace identity")
    parts = value.split(":")
    if len(parts) != 2 or any(not part.isdigit() or int(part) < 1 for part in parts):
        raise BrokerRejected("invalid namespace identity")
    return value


def _strict_datetime(value: object) -> datetime:
    if not isinstance(value, str):
        raise BrokerRejected("invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise BrokerRejected("invalid timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BrokerRejected("timestamp must be timezone-aware")
    return parsed.astimezone(UTC)


def _read_bounded_json(path: Path, *, maximum: int, owner_uid: int) -> Mapping[str, object]:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BrokerRejected("trusted file unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or before.st_nlink != 1
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or before.st_size < 2
            or before.st_size > maximum
        ):
            raise BrokerRejected("trusted file metadata rejected")
        payload = os.read(descriptor, maximum + 1)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise BrokerRejected("trusted file changed during read")
    if len(payload) != before.st_size or len(payload) > maximum:
        raise BrokerRejected("trusted file size rejected")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerRejected("trusted JSON rejected") from exc
    if not isinstance(value, dict):
        raise BrokerRejected("trusted JSON rejected")
    return value


def _read_authentication_key(path: Path, *, owner_uid: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BrokerRejected("daemon authentication key unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != owner_uid
            or info.st_nlink != 1
            or info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or info.st_size < 64
            or info.st_size > 65
        ):
            raise BrokerRejected("daemon authentication key metadata rejected")
        raw = os.read(descriptor, 66)
    finally:
        os.close(descriptor)
    try:
        encoded = raw.decode("ascii").strip()
        key = bytes.fromhex(encoded)
    except (UnicodeDecodeError, ValueError) as exc:
        raise BrokerRejected("daemon authentication key rejected") from exc
    if len(key) != 32:
        raise BrokerRejected("daemon authentication key rejected")
    return key


def _write_all(descriptor: int, payload: bytes) -> None:
    view = memoryview(payload)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count < 1:
            raise BrokerRejected("durable write made no progress")
        written += count


def _fsync_directory(path: Path) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BrokerRejected("durable directory unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise BrokerRejected("durable directory rejected")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _verify_directory(
    path: Path,
    *,
    owner_uid: int,
    allow_group_read_execute: bool = False,
) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise BrokerRejected("trusted directory unavailable") from exc
    forbidden = stat.S_IWGRP | stat.S_IWOTH | stat.S_IROTH | stat.S_IXOTH
    if not allow_group_read_execute:
        forbidden |= stat.S_IRGRP | stat.S_IXGRP
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not stat.S_ISDIR(info.st_mode)
        or info.st_uid != owner_uid
        or info.st_mode & forbidden
    ):
        raise BrokerRejected("trusted directory metadata rejected")


@dataclass(frozen=True, slots=True)
class BrokerConfig:
    socket_path: Path
    permit_directory: Path
    consumed_directory: Path
    host_network_namespace_path: Path
    daemon_authentication_key_path: Path
    daemon_uid: int
    trusted_client_uid: int
    trusted_client_gid: int
    permit_owner_uid: int
    host_namespace_owner_uid: int
    max_request_bytes: int
    connect_timeout_seconds: float
    read_timeout_seconds: float

    @classmethod
    def load(cls, path: Path) -> BrokerConfig:
        if not path.is_absolute():
            raise BrokerRejected("config path must be absolute")
        _verify_directory(path.parent, owner_uid=0, allow_group_read_execute=True)
        value = _strict_mapping(
            _read_bounded_json(path, maximum=_MAX_CONFIG_BYTES, owner_uid=0),
            keys=_CONFIG_KEYS,
        )
        config = cls(
            socket_path=Path(str(value["socket_path"])),
            permit_directory=Path(str(value["permit_directory"])),
            consumed_directory=Path(str(value["consumed_directory"])),
            host_network_namespace_path=Path(str(value["host_network_namespace_path"])),
            daemon_authentication_key_path=Path(str(value["daemon_authentication_key_path"])),
            daemon_uid=_strict_int(value["daemon_uid"], minimum=1, maximum=2**31 - 1),
            trusted_client_uid=_strict_int(
                value["trusted_client_uid"], minimum=0, maximum=2**31 - 1
            ),
            trusted_client_gid=_strict_int(
                value["trusted_client_gid"], minimum=0, maximum=2**31 - 1
            ),
            permit_owner_uid=_strict_int(value["permit_owner_uid"], minimum=0, maximum=2**31 - 1),
            host_namespace_owner_uid=_strict_int(
                value["host_namespace_owner_uid"], minimum=0, maximum=2**31 - 1
            ),
            max_request_bytes=_strict_int(
                value["max_request_bytes"], minimum=1_024, maximum=1_048_576
            ),
            connect_timeout_seconds=_strict_float(
                value["connect_timeout_seconds"], minimum=0.05, maximum=5.0
            ),
            read_timeout_seconds=_strict_float(
                value["read_timeout_seconds"], minimum=0.0, maximum=2.0
            ),
        )
        for configured_path in (
            config.socket_path,
            config.permit_directory,
            config.consumed_directory,
            config.host_network_namespace_path,
            config.daemon_authentication_key_path,
        ):
            if not configured_path.is_absolute():
                raise BrokerRejected("all configured paths must be absolute")
        return config

    def verify_runtime(self) -> None:
        if os.name != "posix" or not hasattr(os, "setns") or not hasattr(os, "fork"):
            raise BrokerRejected("Linux setns/fork runtime is required")
        if os.geteuid() != self.daemon_uid or self.daemon_uid == 0:
            raise BrokerRejected("daemon must run as the configured dedicated non-root UID")
        _verify_directory(self.socket_path.parent, owner_uid=self.daemon_uid)
        _verify_directory(
            self.permit_directory,
            owner_uid=self.permit_owner_uid,
            allow_group_read_execute=True,
        )
        _verify_directory(self.consumed_directory, owner_uid=self.daemon_uid)
        _trusted_namespace_identity(
            self.host_network_namespace_path,
            owner_uid=self.host_namespace_owner_uid,
        )

    def load_authentication_key(self) -> bytes:
        return _read_authentication_key(
            self.daemon_authentication_key_path,
            owner_uid=0,
        )


@dataclass(frozen=True, slots=True)
class BrokerRequest:
    challenge: str
    operation_id: UUID
    request_binding_digest: str
    authorization_digest: str
    namespace_evidence_digest: str
    destination_resolution_digest: str
    plan_digest: str
    namespace_id: UUID
    runtime_instance_id: UUID
    network_namespace_identity: str
    namespace_process_id: int
    namespace_process_start_time_ticks: int
    service_id: UUID
    address: ipaddress.IPv4Address | ipaddress.IPv6Address
    port: int
    network_protocol: str
    route_kind: str

    @classmethod
    def parse(cls, value: object) -> BrokerRequest:
        request = _strict_mapping(value, keys=_REQUEST_KEYS)
        if request["protocol"] != _PROTOCOL:
            raise BrokerRejected("protocol rejected")
        destination = _strict_mapping(request["destination"], keys=_DESTINATION_KEYS)
        namespace = _strict_mapping(request["namespace"], keys=_NAMESPACE_KEYS)
        plan = _strict_mapping(request["plan"], keys=_PLAN_KEYS)
        try:
            address = ipaddress.ip_address(str(destination["address"]))
        except ValueError as exc:
            raise BrokerRejected("destination rejected") from exc
        route_kind = str(destination["route_kind"])
        network_protocol = str(destination["protocol"])
        if route_kind != "workspace_service" or network_protocol != "tcp":
            raise BrokerRejected("route or network protocol rejected")
        if not address.is_global:
            raise BrokerRejected("non-global destination class rejected")
        if any(
            (
                address.is_loopback,
                address.is_link_local,
                address.is_multicast,
                address.is_private,
                address.is_reserved,
                address.is_unspecified,
            )
        ):
            raise BrokerRejected("destination class rejected")
        parsed = cls(
            challenge=_strict_digest(request["challenge"]),
            operation_id=_strict_uuid(plan["operation_id"]),
            request_binding_digest=_strict_digest(plan["request_binding_digest"]),
            authorization_digest=_strict_digest(plan["authorization_digest"]),
            namespace_evidence_digest=_strict_digest(plan["namespace_evidence_digest"]),
            destination_resolution_digest=_strict_digest(plan["destination_resolution_digest"]),
            plan_digest=_strict_digest(plan["plan_digest"]),
            namespace_id=_strict_uuid(namespace["namespace_id"]),
            runtime_instance_id=_strict_uuid(namespace["runtime_instance_id"]),
            network_namespace_identity=_strict_namespace_identity(
                namespace["network_namespace_identity"]
            ),
            namespace_process_id=_strict_int(
                namespace["namespace_process_id"], minimum=1, maximum=2**31 - 1
            ),
            namespace_process_start_time_ticks=_strict_int(
                namespace["namespace_process_start_time_ticks"],
                minimum=1,
                maximum=2**63 - 1,
            ),
            service_id=_strict_uuid(destination["service_id"]),
            address=address,
            port=_strict_int(destination["port"], minimum=1, maximum=65_535),
            network_protocol=network_protocol,
            route_kind=route_kind,
        )
        if namespace["evidence_digest"] != parsed.namespace_evidence_digest:
            raise BrokerRejected("namespace digest binding rejected")
        if destination["resolution_digest"] != parsed.destination_resolution_digest:
            raise BrokerRejected("destination digest binding rejected")
        return parsed


@dataclass(frozen=True, slots=True)
class BrokerPermit:
    request: BrokerRequest
    max_connections: int
    max_bytes_in: int
    max_bytes_out: int
    not_before: datetime
    expires_at: datetime

    @classmethod
    def load(cls, *, config: BrokerConfig, request: BrokerRequest) -> BrokerPermit:
        path = config.permit_directory / f"{request.operation_id}.permit.json"
        value = _strict_mapping(
            _read_bounded_json(path, maximum=_MAX_PERMIT_BYTES, owner_uid=config.permit_owner_uid),
            keys=_PERMIT_KEYS,
        )
        if value["protocol"] != _PERMIT_PROTOCOL:
            raise BrokerRejected("permit protocol rejected")
        exact_bindings: tuple[tuple[str, object], ...] = (
            ("operation_id", str(request.operation_id)),
            ("request_binding_digest", request.request_binding_digest),
            ("authorization_digest", request.authorization_digest),
            ("namespace_evidence_digest", request.namespace_evidence_digest),
            ("destination_resolution_digest", request.destination_resolution_digest),
            ("plan_digest", request.plan_digest),
            ("namespace_id", str(request.namespace_id)),
            ("runtime_instance_id", str(request.runtime_instance_id)),
            ("network_namespace_identity", request.network_namespace_identity),
            ("namespace_process_id", request.namespace_process_id),
            (
                "namespace_process_start_time_ticks",
                request.namespace_process_start_time_ticks,
            ),
            ("service_id", str(request.service_id)),
            ("address", str(request.address)),
            ("port", request.port),
            ("network_protocol", request.network_protocol),
            ("route_kind", request.route_kind),
        )
        if any(value[key] != expected for key, expected in exact_bindings):
            raise BrokerRejected("permit binding rejected")
        now = datetime.now(UTC)
        not_before = _strict_datetime(value["not_before"])
        expires_at = _strict_datetime(value["expires_at"])
        if not_before > now or expires_at <= now:
            raise BrokerRejected("permit is not currently valid")
        if expires_at - not_before > timedelta(minutes=5):
            raise BrokerRejected("permit lifetime rejected")
        return cls(
            request=request,
            max_connections=_strict_int(value["max_connections"], minimum=1, maximum=1),
            max_bytes_in=_strict_int(value["max_bytes_in"], minimum=0, maximum=16 * 1024 * 1024),
            max_bytes_out=_strict_int(value["max_bytes_out"], minimum=0, maximum=16 * 1024 * 1024),
            not_before=not_before,
            expires_at=expires_at,
        )


def _namespace_identity(descriptor: int) -> str:
    info = os.fstat(descriptor)
    if info.st_dev < 1 or info.st_ino < 1:
        raise BrokerRejected("namespace identity unavailable")
    return f"{info.st_dev}:{info.st_ino}"


def _process_start_time_ticks(process_id: int) -> int:
    try:
        raw = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii")
        closing = raw.rindex(")")
        value = int(raw[closing + 2 :].split()[19])
    except (OSError, UnicodeDecodeError, ValueError, IndexError) as exc:
        raise BrokerRejected("namespace process unavailable") from exc
    if value < 1:
        raise BrokerRejected("namespace process rejected")
    return value


def _live_process_namespace_identity(process_id: int) -> tuple[str, int]:
    start_before = _process_start_time_ticks(process_id)
    try:
        descriptor = os.open(
            f"/proc/{process_id}/ns/net",
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise BrokerRejected("namespace process unavailable") from exc
    try:
        identity = _namespace_identity(descriptor)
    finally:
        os.close(descriptor)
    start_after = _process_start_time_ticks(process_id)
    if start_before != start_after:
        raise BrokerRejected("namespace process changed")
    return identity, start_after


def _trusted_namespace_identity(path: Path, *, owner_uid: int) -> str:
    if path not in {Path("/proc/1/ns/net"), Path("/run/omnibase-host-ns/net")}:
        raise BrokerRejected("host network namespace path rejected")
    try:
        info = path.lstat()
    except OSError as exc:
        raise BrokerRejected("host network namespace reference unavailable") from exc
    if path == Path("/proc/1/ns/net") and stat.S_ISLNK(info.st_mode):
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        except OSError as exc:
            raise BrokerRejected("host network namespace reference unavailable") from exc
        try:
            return _namespace_identity(descriptor)
        finally:
            os.close(descriptor)
    return _read_host_snapshot_identity(path, owner_uid=owner_uid)


def _read_host_snapshot_identity(path: Path, *, owner_uid: int) -> str:
    parent = path.parent
    try:
        parent_info = parent.lstat()
    except OSError as exc:
        raise BrokerRejected("host network namespace reference unavailable") from exc
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent.is_symlink()
        or parent_info.st_uid != 0
        or parent_info.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    ):
        raise BrokerRejected("host network namespace reference rejected")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BrokerRejected("host network namespace reference unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != owner_uid
            or before.st_nlink != 1
            or before.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
            or before.st_size < 3
            or before.st_size > 128
        ):
            raise BrokerRejected("host network namespace reference rejected")
        raw = os.read(descriptor, 129)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise BrokerRejected("host network namespace reference changed")
    if len(raw) != before.st_size or len(raw) > 128:
        raise BrokerRejected("host network namespace reference rejected")
    try:
        identity = raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise BrokerRejected("host network namespace reference rejected") from exc
    return _strict_namespace_identity(identity)


def _open_verified_namespace(config: BrokerConfig, request: BrokerRequest) -> int:
    start_before = _process_start_time_ticks(request.namespace_process_id)
    path = Path(f"/proc/{request.namespace_process_id}/ns/net")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise BrokerRejected("network namespace handle unavailable") from exc
    try:
        identity = _namespace_identity(descriptor)
        start_after = _process_start_time_ticks(request.namespace_process_id)
        initial_identity, _ = _live_process_namespace_identity(os.getpid())
        host_identity = _trusted_namespace_identity(
            config.host_network_namespace_path,
            owner_uid=config.host_namespace_owner_uid,
        )
        if request.network_namespace_identity != identity:
            raise BrokerRejected("network namespace request identity rejected")
        if start_before != start_after or start_after != request.namespace_process_start_time_ticks:
            raise BrokerRejected("network namespace process binding rejected")
        if initial_identity == identity:
            raise BrokerRejected("host/broker namespace escape rejected")
        if host_identity == identity:
            raise BrokerRejected("host/broker namespace escape rejected")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _consume_operation(config: BrokerConfig, request: BrokerRequest) -> None:
    path = config.consumed_directory / f"{request.operation_id}.consumed.json"
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    payload = json.dumps(
        {
            "destination_resolution_digest": request.destination_resolution_digest,
            "namespace_evidence_digest": request.namespace_evidence_digest,
            "operation_id": str(request.operation_id),
            "plan_digest": request.plan_digest,
            "state": "consumed-outcome-unknown-until-core-receipt-commit",
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError as exc:
        raise BrokerRejected("operation replay rejected") from exc
    except OSError as exc:
        raise BrokerRejected("operation consumption unavailable") from exc
    try:
        _write_all(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(config.consumed_directory)


def _worker_connect(
    *,
    config: BrokerConfig,
    permit: BrokerPermit,
    namespace_descriptor: int,
) -> Mapping[str, object]:
    request = permit.request
    namespace_flag = getattr(os, "CLONE_NEWNET", 0x40000000)
    os.setns(namespace_descriptor, namespace_flag)
    current_identity, _ = _live_process_namespace_identity(os.getpid())
    if current_identity != request.network_namespace_identity:
        raise BrokerRejected("setns verification rejected")
    family = socket.AF_INET6 if request.address.version == 6 else socket.AF_INET
    connection = socket.socket(family, socket.SOCK_STREAM)
    connection.settimeout(config.connect_timeout_seconds)
    try:
        connection.connect((str(request.address), request.port))
        bytes_in = 0
        if permit.max_bytes_in > 0 and config.read_timeout_seconds > 0:
            connection.settimeout(config.read_timeout_seconds)
            try:
                payload = connection.recv(permit.max_bytes_in + 1)
            except TimeoutError:
                payload = b""
            if len(payload) > permit.max_bytes_in:
                raise BrokerRejected("inbound byte budget exceeded")
            bytes_in = len(payload)
    finally:
        connection.close()
    return {
        "accepted_at": datetime.now(UTC).isoformat(),
        "bytes_in": bytes_in,
        "bytes_out": 0,
        "connections": 1,
        "destination_resolution_digest": request.destination_resolution_digest,
        "namespace_evidence_digest": request.namespace_evidence_digest,
        "operation_id": str(request.operation_id),
        "plan_digest": request.plan_digest,
        "request_binding_digest": request.request_binding_digest,
    }


def _execute_in_worker(
    *,
    config: BrokerConfig,
    permit: BrokerPermit,
    namespace_descriptor: int,
    client_descriptor: int,
) -> Mapping[str, object]:
    read_descriptor, write_descriptor = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        try:
            os.close(read_descriptor)
            os.close(client_descriptor)
            try:
                result: Mapping[str, object] = {
                    "ok": True,
                    "receipt": _worker_connect(
                        config=config,
                        permit=permit,
                        namespace_descriptor=namespace_descriptor,
                    ),
                }
            except Exception:
                result = {"ok": False}
            payload = json.dumps(result, sort_keys=True, separators=(",", ":")).encode("utf-8")
            _write_all(write_descriptor, payload[:_MAX_RESULT_BYTES])
        finally:
            os._exit(0)
    os.close(write_descriptor)
    timeout = config.connect_timeout_seconds + config.read_timeout_seconds + 1.0
    ready, _, _ = select.select([read_descriptor], [], [], timeout)
    if not ready:
        os.kill(child_pid, signal.SIGKILL)
        os.waitpid(child_pid, 0)
        os.close(read_descriptor)
        raise BrokerRejected("network worker deadline exceeded")
    payload = os.read(read_descriptor, _MAX_RESULT_BYTES + 1)
    os.close(read_descriptor)
    _, status = os.waitpid(child_pid, 0)
    if status != 0 or not payload or len(payload) > _MAX_RESULT_BYTES:
        raise BrokerRejected("network worker rejected")
    try:
        result = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerRejected("network worker rejected") from exc
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise BrokerRejected("network worker rejected")
    receipt = result.get("receipt")
    if not isinstance(receipt, dict):
        raise BrokerRejected("network worker rejected")
    return receipt


class NetworkBrokerDaemon:
    def __init__(self, config: BrokerConfig) -> None:
        self._config = config
        self._authentication_key = config.load_authentication_key()
        self._stopping = False
        self._server: socket.socket | None = None

    def stop(self, signum: int, frame: FrameType | None) -> None:
        del signum, frame
        self._stopping = True
        if self._server is not None:
            self._server.close()

    def serve(self) -> None:
        self._config.verify_runtime()
        try:
            self._config.socket_path.unlink(missing_ok=True)
        except OSError as exc:
            raise BrokerRejected("stale socket cannot be removed") from exc
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._server = server
        server.bind(str(self._config.socket_path))
        self._config.socket_path.chmod(0o600)
        server.listen(16)
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        try:
            while not self._stopping:
                try:
                    client, _ = server.accept()
                except OSError:
                    if self._stopping:
                        break
                    raise
                with client:
                    try:
                        self._handle(client)
                    except Exception as exc:
                        print(
                            f"broker request rejected: {type(exc).__name__}: {exc}",
                            file=sys.stderr,
                            flush=True,
                        )
                        response = b'{"error":"broker_request_rejected"}'
                        with contextlib.suppress(OSError):
                            client.sendall(response)
        finally:
            server.close()
            self._server = None
            self._config.socket_path.unlink(missing_ok=True)

    def _handle(self, client: socket.socket) -> None:
        peer = self._verify_peer(client)
        payload = self._read_request(client)
        if self._verify_peer(client) != peer:
            raise BrokerRejected("client peer process changed")
        request = BrokerRequest.parse(json.loads(payload))
        permit = BrokerPermit.load(config=self._config, request=request)
        namespace_descriptor = _open_verified_namespace(self._config, request)
        try:
            _consume_operation(self._config, request)
            receipt = _execute_in_worker(
                config=self._config,
                permit=permit,
                namespace_descriptor=namespace_descriptor,
                client_descriptor=client.fileno(),
            )
        finally:
            os.close(namespace_descriptor)
        receipt["challenge_response"] = hmac.new(
            self._authentication_key,
            f"{request.challenge}:{request.operation_id}:{request.plan_digest}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        client.sendall(json.dumps(receipt, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def _verify_peer(self, client: socket.socket) -> tuple[int, int, int, int]:
        peer_option = getattr(socket, "SO_PEERCRED", None)
        if peer_option is None:
            raise BrokerRejected("SO_PEERCRED unavailable")
        credentials = client.getsockopt(socket.SOL_SOCKET, peer_option, 12)
        process_id, uid, gid = struct.unpack("3i", credentials)
        if uid != self._config.trusted_client_uid or gid != self._config.trusted_client_gid:
            raise BrokerRejected(
                "client peer rejected "
                f"uid={uid} gid={gid} expected_uid={self._config.trusted_client_uid} "
                f"expected_gid={self._config.trusted_client_gid}"
            )
        return process_id, uid, gid, _process_start_time_ticks(process_id)

    def _read_request(self, client: socket.socket) -> bytes:
        client.settimeout(self._config.connect_timeout_seconds)
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = client.recv(min(16_384, self._config.max_request_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > self._config.max_request_bytes:
                raise BrokerRejected("request too large")
        payload = b"".join(chunks)
        if not payload or not payload.endswith(b"\n"):
            raise BrokerRejected("request framing rejected")
        return payload[:-1]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("serve", nargs="?")
    parser.add_argument("--config", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    if args.serve not in (None, "serve"):
        raise SystemExit("only the serve command is supported")
    try:
        config = BrokerConfig.load(args.config)
        NetworkBrokerDaemon(config).serve()
    except BrokerRejected as exc:
        print(f"network broker refused to start: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
