"""Production-oriented local seams for P34.5B Broker deployment.

The namespace attestor consumes a private, daemon-owned evidence file and the
transport speaks a bounded JSON protocol over one explicit AF_UNIX socket.
Neither component is installed by default, opens an Internet socket, joins an
Overlay, resolves credentials, or accepts caller-supplied paths.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import UUID

from omnibase.sandbox.broker import (
    BrokerConnectionPlan,
    BrokerConnectionReceipt,
    VerifiedNetworkNamespace,
)
from omnibase.sandbox.contracts import SandboxRejected, SandboxUnavailable, utc_now
from omnibase.sandbox.network import (
    NetworkDestination,
    NetworkRouteKind,
    VerifiedSandboxNetworkAuthorization,
    stable_digest,
    validate_destination_address,
)

_MAX_EVIDENCE_BYTES: Final = 65_536
_PROC_ROOT: Final = Path("/proc")
_HOST_PROC_NET_PATH: Final = Path("/proc/1/ns/net")
_HOST_SNAPSHOT_PATH: Final = Path("/run/omnibase-host-ns/net")
_NAMESPACE_KEYS: Final = {
    "direct_overlay",
    "evidence_digest",
    "expires_at",
    "namespace_id",
    "network_fencing_token",
    "network_namespace_identity",
    "namespace_process_id",
    "namespace_process_start_time_ticks",
    "node_fencing_token",
    "node_id",
    "policy_digest",
    "run_fencing_token",
    "runner_id",
    "runtime_instance_id",
    "verified_at",
    "workload_identity_thumbprint",
    "workspace_generation",
}
_RECEIPT_KEYS: Final = {
    "accepted_at",
    "bytes_in",
    "bytes_out",
    "connections",
    "destination_resolution_digest",
    "namespace_evidence_digest",
    "operation_id",
    "plan_digest",
    "request_binding_digest",
}
_AUTHENTICATED_RECEIPT_KEYS: Final = _RECEIPT_KEYS | {"challenge_response"}


def _strict_int(value: object, *, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SandboxRejected(f"{name}_rejected")
    return value


def _strict_bool(value: object, *, name: str) -> bool:
    if not isinstance(value, bool):
        raise SandboxRejected(f"{name}_rejected")
    return value


def _strict_string(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise SandboxRejected(f"{name}_rejected")
    return value


def _read_json_fd(descriptor: int, *, maximum: int) -> Mapping[str, object]:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(remaining, 16_384))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    if not payload or len(payload) > maximum:
        raise SandboxRejected("sandbox_network_namespace_evidence_rejected")
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SandboxRejected("sandbox_network_namespace_evidence_rejected") from exc
    if not isinstance(value, dict):
        raise SandboxRejected("sandbox_network_namespace_evidence_rejected")
    return value


def _fd_identity(descriptor: int) -> str:
    info = os.fstat(descriptor)
    if info.st_dev < 1 or info.st_ino < 1:
        raise SandboxUnavailable("sandbox_network_namespace_identity_unavailable")
    return f"{info.st_dev}:{info.st_ino}"


def _read_bounded_fd(descriptor: int, *, maximum: int) -> bytes:
    chunks: list[bytes] = []
    remaining = maximum + 1
    while remaining > 0:
        chunk = os.read(descriptor, min(remaining, 4_096))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    value = b"".join(chunks)
    if not value or len(value) > maximum:
        raise SandboxUnavailable("sandbox_network_namespace_reference_unavailable")
    return value


def _host_namespace_identity(path: Path) -> str:
    if path == _HOST_PROC_NET_PATH:
        try:
            path_info = path.lstat()
            if not stat.S_ISLNK(path_info.st_mode):
                raise SandboxUnavailable("sandbox_network_host_namespace_untrusted")
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        except OSError as exc:
            raise SandboxUnavailable("sandbox_network_host_namespace_unavailable") from exc
        try:
            return _fd_identity(descriptor)
        finally:
            os.close(descriptor)
    if path != _HOST_SNAPSHOT_PATH:
        raise SandboxUnavailable("sandbox_network_host_namespace_path_rejected")
    parent = path.parent
    try:
        parent_info = parent.lstat()
    except OSError as exc:
        raise SandboxUnavailable("sandbox_network_host_namespace_unavailable") from exc
    if (
        not stat.S_ISDIR(parent_info.st_mode)
        or parent.is_symlink()
        or parent_info.st_uid != 0
        or parent_info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
    ):
        raise SandboxUnavailable("sandbox_network_host_namespace_untrusted")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SandboxUnavailable("sandbox_network_host_namespace_unavailable") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != 0
            or before.st_nlink != 1
            or before.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise SandboxUnavailable("sandbox_network_host_namespace_untrusted")
        raw = _read_bounded_fd(descriptor, maximum=128)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_size,
        after.st_mtime_ns,
    ):
        raise SandboxUnavailable("sandbox_network_host_namespace_changed")
    try:
        identity = raw.decode("ascii").strip()
    except UnicodeDecodeError as exc:
        raise SandboxUnavailable("sandbox_network_host_namespace_untrusted") from exc
    parts = identity.split(":")
    if len(parts) != 2 or any(not part.isdigit() or int(part) < 1 for part in parts):
        raise SandboxUnavailable("sandbox_network_host_namespace_untrusted")
    return identity


def _process_start_time_ticks(process_id: int) -> int:
    path = _PROC_ROOT / str(process_id) / "stat"
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SandboxUnavailable("sandbox_network_namespace_process_unavailable") from exc
    try:
        raw = _read_bounded_fd(descriptor, maximum=8_192)
    finally:
        os.close(descriptor)
    try:
        text = raw.decode("ascii")
        closing = text.rindex(")")
        fields = text[closing + 2 :].split()
        value = int(fields[19])
    except (UnicodeDecodeError, ValueError, IndexError) as exc:
        raise SandboxUnavailable("sandbox_network_namespace_process_untrusted") from exc
    if value < 1:
        raise SandboxUnavailable("sandbox_network_namespace_process_untrusted")
    return value


def _live_process_namespace_identity(process_id: int) -> tuple[str, int]:
    start_before = _process_start_time_ticks(process_id)
    path = _PROC_ROOT / str(process_id) / "ns" / "net"
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
    except OSError as exc:
        raise SandboxUnavailable("sandbox_network_namespace_process_unavailable") from exc
    try:
        identity = _fd_identity(descriptor)
    finally:
        os.close(descriptor)
    start_after = _process_start_time_ticks(process_id)
    if start_before != start_after:
        raise SandboxRejected("sandbox_network_namespace_process_changed")
    return identity, start_after


@dataclass(frozen=True, slots=True)
class _PeerProcessEvidence:
    process_id: int
    user_id: int
    group_id: int
    start_time_ticks: int


def _peer_process_evidence(
    *,
    process_id: int,
    user_id: int,
    group_id: int,
) -> _PeerProcessEvidence:
    start_before = _process_start_time_ticks(process_id)
    start_after = _process_start_time_ticks(process_id)
    if start_before != start_after:
        raise SandboxRejected("workspace_network_broker_peer_process_changed")
    return _PeerProcessEvidence(
        process_id=process_id,
        user_id=user_id,
        group_id=group_id,
        start_time_ticks=start_after,
    )


class FilesystemNetworkNamespaceAttestor:
    """Read current runtime network-namespace proof from a private state root."""

    def __init__(
        self,
        *,
        evidence_directory: Path,
        host_network_namespace_path: Path,
        trusted_owner_uid: int,
        clock=utc_now,
    ) -> None:
        if os.name != "posix":
            raise SandboxUnavailable("sandbox_network_namespace_attestor_requires_posix")
        if (
            not evidence_directory.is_absolute()
            or host_network_namespace_path not in {_HOST_PROC_NET_PATH, _HOST_SNAPSHOT_PATH}
            or isinstance(trusted_owner_uid, bool)
            or trusted_owner_uid < 0
        ):
            raise ValueError("network namespace attestor configuration is invalid")
        self._evidence_directory = evidence_directory
        self._host_network_namespace_path = host_network_namespace_path
        self._trusted_owner_uid = trusted_owner_uid
        self._clock = clock
        self._verify_private_directory()
        _host_namespace_identity(self._host_network_namespace_path)

    def attest(
        self,
        *,
        authorization: VerifiedSandboxNetworkAuthorization,
    ) -> VerifiedNetworkNamespace:
        self._verify_private_directory()
        evidence_path = (
            self._evidence_directory
            / f"{authorization.request.runtime_instance_id}.network-namespace.json"
        )
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(evidence_path, flags)
        except OSError as exc:
            raise SandboxUnavailable("sandbox_network_namespace_evidence_unavailable") from exc
        try:
            before = os.fstat(descriptor)
            self._verify_evidence_file(before)
            value = _read_json_fd(descriptor, maximum=_MAX_EVIDENCE_BYTES)
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
            raise SandboxRejected("sandbox_network_namespace_evidence_changed")
        if set(value) != _NAMESPACE_KEYS:
            raise SandboxRejected("sandbox_network_namespace_evidence_rejected")
        supplied_digest = value.get("evidence_digest")
        digest_value = {key: item for key, item in value.items() if key != "evidence_digest"}
        if supplied_digest != stable_digest(digest_value):
            raise SandboxRejected("sandbox_network_namespace_evidence_digest_rejected")
        try:
            proof = VerifiedNetworkNamespace(
                namespace_id=UUID(str(value["namespace_id"])),
                network_namespace_identity=_strict_string(
                    value["network_namespace_identity"],
                    name="sandbox_network_namespace_identity",
                ),
                namespace_process_id=_strict_int(
                    value["namespace_process_id"],
                    name="sandbox_network_namespace_process_id",
                ),
                namespace_process_start_time_ticks=_strict_int(
                    value["namespace_process_start_time_ticks"],
                    name="sandbox_network_namespace_process_start_time",
                ),
                runner_id=UUID(str(value["runner_id"])),
                node_id=UUID(str(value["node_id"])),
                runtime_instance_id=UUID(str(value["runtime_instance_id"])),
                workload_identity_thumbprint=str(value["workload_identity_thumbprint"]),
                workspace_generation=_strict_int(
                    value["workspace_generation"],
                    name="sandbox_network_namespace_workspace_generation",
                ),
                run_fencing_token=_strict_int(
                    value["run_fencing_token"],
                    name="sandbox_network_namespace_run_fencing_token",
                ),
                node_fencing_token=_strict_int(
                    value["node_fencing_token"],
                    name="sandbox_network_namespace_node_fencing_token",
                ),
                network_fencing_token=_strict_int(
                    value["network_fencing_token"],
                    name="sandbox_network_namespace_network_fencing_token",
                ),
                policy_digest=str(value["policy_digest"]),
                direct_overlay=_strict_bool(
                    value["direct_overlay"],
                    name="sandbox_network_namespace_direct_overlay",
                ),
                verified_at=datetime.fromisoformat(str(value["verified_at"])),
                expires_at=datetime.fromisoformat(str(value["expires_at"])),
                evidence_digest=str(supplied_digest),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SandboxRejected("sandbox_network_namespace_evidence_rejected") from exc
        now = self._clock()
        if (
            proof.verified_at > now
            or proof.expires_at <= now
            or proof.expires_at - proof.verified_at > timedelta(minutes=5)
        ):
            raise SandboxRejected("sandbox_network_namespace_expired")
        live_identity, live_start_time = _live_process_namespace_identity(
            proof.namespace_process_id
        )
        if (
            proof.network_namespace_identity != live_identity
            or proof.namespace_process_start_time_ticks != live_start_time
        ):
            raise SandboxRejected("sandbox_network_namespace_process_binding_rejected")
        request = authorization.request
        proof_binding = (
            proof.runner_id,
            proof.namespace_id,
            proof.network_namespace_identity,
            proof.namespace_process_id,
            proof.namespace_process_start_time_ticks,
            proof.node_id,
            proof.runtime_instance_id,
            proof.workload_identity_thumbprint,
            proof.workspace_generation,
            proof.run_fencing_token,
            proof.node_fencing_token,
            proof.network_fencing_token,
            proof.policy_digest,
        )
        expected_binding = (
            authorization.expected_runner_id,
            authorization.expected_namespace_id,
            authorization.expected_network_namespace_identity,
            authorization.expected_namespace_process_id,
            authorization.expected_namespace_process_start_time_ticks,
            request.node_id,
            request.runtime_instance_id,
            request.workload_identity_thumbprint,
            request.workspace_generation,
            request.run_fencing_token,
            request.node_fencing_token,
            request.network_fencing_token,
            authorization.policy_digest,
        )
        if proof_binding != expected_binding or proof.direct_overlay:
            raise SandboxRejected("sandbox_network_namespace_binding_rejected")
        host_identity = _host_namespace_identity(self._host_network_namespace_path)
        if proof.network_namespace_identity == host_identity:
            raise SandboxRejected("sandbox_network_namespace_not_isolated")
        return proof

    def _verify_private_directory(self) -> None:
        try:
            info = self._evidence_directory.lstat()
        except OSError as exc:
            raise SandboxUnavailable("sandbox_network_namespace_directory_unavailable") from exc
        if (
            not stat.S_ISDIR(info.st_mode)
            or self._evidence_directory.is_symlink()
            or info.st_uid != self._trusted_owner_uid
            or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise SandboxUnavailable("sandbox_network_namespace_directory_untrusted")

    def _verify_evidence_file(self, info: os.stat_result) -> None:
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != self._trusted_owner_uid
            or info.st_nlink != 1
            or info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or info.st_size < 2
            or info.st_size > _MAX_EVIDENCE_BYTES
        ):
            raise SandboxUnavailable("sandbox_network_namespace_evidence_untrusted")


class UnixSocketBrokerTransport:
    """Bounded AF_UNIX client for a separately confined local Broker daemon."""

    def __init__(
        self,
        *,
        socket_path: Path,
        trusted_peer_uid: int,
        trusted_peer_gid: int,
        daemon_authentication_key: bytes,
        timeout_seconds: float = 3.0,
        max_response_bytes: int = 65_536,
    ) -> None:
        if os.name != "posix":
            raise SandboxUnavailable("workspace_network_broker_transport_requires_posix")
        if (
            not socket_path.is_absolute()
            or isinstance(trusted_peer_uid, bool)
            or trusted_peer_uid < 1
            or trusted_peer_uid == os.geteuid()
            or isinstance(trusted_peer_gid, bool)
            or trusted_peer_gid < 1
            or not isinstance(daemon_authentication_key, bytes)
            or len(daemon_authentication_key) < 32
            or not 0.05 <= timeout_seconds <= 5.0
            or not 1 <= max_response_bytes <= 1_048_576
        ):
            raise ValueError("Broker transport configuration is invalid")
        self._socket_path = socket_path
        self._trusted_peer_uid = trusted_peer_uid
        self._trusted_peer_gid = trusted_peer_gid
        self._daemon_authentication_key = bytes(daemon_authentication_key)
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def connect(
        self,
        *,
        plan: BrokerConnectionPlan,
        namespace: VerifiedNetworkNamespace,
        destination: NetworkDestination,
    ) -> BrokerConnectionReceipt:
        validate_destination_address(destination)
        if destination.route_kind is not NetworkRouteKind.WORKSPACE_SERVICE:
            raise SandboxRejected("sandbox_network_route_rejected")
        socket_identity = self._verify_socket_path()
        challenge = secrets.token_hex(32)
        payload = (
            json.dumps(
                {
                    "challenge": challenge,
                    "destination": {
                        "address": str(destination.address),
                        "port": destination.port,
                        "protocol": destination.protocol.value,
                        "resolution_digest": destination.resolution_digest,
                        "route_kind": destination.route_kind.value,
                        "service_id": str(destination.service_id),
                    },
                    "namespace": {
                        "evidence_digest": namespace.evidence_digest,
                        "namespace_id": str(namespace.namespace_id),
                        "network_namespace_identity": namespace.network_namespace_identity,
                        "namespace_process_id": namespace.namespace_process_id,
                        "namespace_process_start_time_ticks": (
                            namespace.namespace_process_start_time_ticks
                        ),
                        "runtime_instance_id": str(namespace.runtime_instance_id),
                    },
                    "plan": {
                        "authorization_digest": plan.authorization_digest,
                        "destination_resolution_digest": plan.destination_resolution_digest,
                        "namespace_evidence_digest": plan.namespace_evidence_digest,
                        "operation_id": str(plan.operation_id),
                        "plan_digest": plan.plan_digest,
                        "request_binding_digest": plan.request_binding_digest,
                    },
                    "protocol": "omnibase-broker-connect-v1",
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            + b"\n"
        )
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(self._timeout_seconds)
        try:
            client.connect(str(self._socket_path))
            self._verify_socket_continuity(socket_identity)
            peer_evidence = self._verify_peer(client)
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            response = self._read_response(client)
            if self._verify_peer(client) != peer_evidence:
                raise SandboxRejected("workspace_network_broker_peer_process_changed")
            self._verify_socket_continuity(socket_identity)
        except SandboxRejected:
            raise
        except (OSError, TimeoutError) as exc:
            raise SandboxUnavailable("workspace_network_broker_transport_unavailable") from exc
        finally:
            client.close()
        return self._parse_authenticated_receipt(
            response,
            challenge=challenge,
            plan=plan,
        )

    def _verify_socket_path(self) -> tuple[int, int]:
        parent = self._socket_path.parent
        try:
            parent_info = parent.lstat()
            socket_info = self._socket_path.lstat()
        except OSError as exc:
            raise SandboxUnavailable("workspace_network_broker_socket_unavailable") from exc
        if (
            not stat.S_ISDIR(parent_info.st_mode)
            or parent.is_symlink()
            or parent_info.st_uid != self._trusted_peer_uid
            or parent_info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or not stat.S_ISSOCK(socket_info.st_mode)
            or self._socket_path.is_symlink()
            or socket_info.st_uid != self._trusted_peer_uid
            or socket_info.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
        ):
            raise SandboxUnavailable("workspace_network_broker_socket_untrusted")
        return socket_info.st_dev, socket_info.st_ino

    def _verify_socket_continuity(self, expected: tuple[int, int]) -> None:
        current = self._verify_socket_path()
        if current != expected:
            raise SandboxRejected("workspace_network_broker_socket_changed")

    def _verify_peer(self, client: socket.socket) -> _PeerProcessEvidence:
        peer_option = getattr(socket, "SO_PEERCRED", None)
        if peer_option is None:
            raise SandboxUnavailable("workspace_network_broker_peer_credential_unavailable")
        try:
            credentials = client.getsockopt(socket.SOL_SOCKET, peer_option, 12)
            process_id, uid, gid = struct.unpack("3i", credentials)
        except (OSError, struct.error) as exc:
            raise SandboxUnavailable(
                "workspace_network_broker_peer_credential_unavailable"
            ) from exc
        if uid != self._trusted_peer_uid or gid != self._trusted_peer_gid:
            raise SandboxRejected("workspace_network_broker_peer_rejected")
        evidence = _peer_process_evidence(
            process_id=process_id,
            user_id=uid,
            group_id=gid,
        )
        return evidence

    def _parse_authenticated_receipt(
        self,
        response: bytes,
        *,
        challenge: str,
        plan: BrokerConnectionPlan,
    ) -> BrokerConnectionReceipt:
        try:
            value = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SandboxRejected("workspace_network_broker_response_rejected") from exc
        if not isinstance(value, dict) or set(value) != _AUTHENTICATED_RECEIPT_KEYS:
            raise SandboxRejected("workspace_network_broker_response_rejected")
        supplied = _strict_string(
            value.pop("challenge_response"),
            name="workspace_network_broker_challenge_response",
        )
        expected = hmac.new(
            self._daemon_authentication_key,
            f"{challenge}:{plan.operation_id}:{plan.plan_digest}".encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise SandboxRejected("workspace_network_broker_challenge_rejected")
        return self._parse_receipt(
            json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )

    def _read_response(self, client: socket.socket) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = client.recv(min(16_384, self._max_response_bytes + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > self._max_response_bytes:
                raise SandboxRejected("workspace_network_broker_response_too_large")
        response = b"".join(chunks)
        if not response:
            raise SandboxRejected("workspace_network_broker_response_rejected")
        return response

    @staticmethod
    def _parse_receipt(response: bytes) -> BrokerConnectionReceipt:
        try:
            value = json.loads(response)
            if not isinstance(value, dict) or set(value) != _RECEIPT_KEYS:
                raise ValueError
            return BrokerConnectionReceipt(
                operation_id=UUID(
                    _strict_string(
                        value["operation_id"],
                        name="workspace_network_broker_receipt_operation_id",
                    )
                ),
                request_binding_digest=_strict_string(
                    value["request_binding_digest"],
                    name="workspace_network_broker_receipt_request_digest",
                ),
                plan_digest=_strict_string(
                    value["plan_digest"],
                    name="workspace_network_broker_receipt_plan_digest",
                ),
                namespace_evidence_digest=_strict_string(
                    value["namespace_evidence_digest"],
                    name="workspace_network_broker_receipt_namespace_digest",
                ),
                destination_resolution_digest=_strict_string(
                    value["destination_resolution_digest"],
                    name="workspace_network_broker_receipt_destination_digest",
                ),
                connections=_strict_int(
                    value["connections"],
                    name="workspace_network_broker_receipt_connections",
                ),
                bytes_in=_strict_int(
                    value["bytes_in"],
                    name="workspace_network_broker_receipt_bytes_in",
                ),
                bytes_out=_strict_int(
                    value["bytes_out"],
                    name="workspace_network_broker_receipt_bytes_out",
                ),
                accepted_at=datetime.fromisoformat(
                    _strict_string(
                        value["accepted_at"],
                        name="workspace_network_broker_receipt_accepted_at",
                    )
                ),
            )
        except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SandboxRejected("workspace_network_broker_response_rejected") from exc


__all__ = ["FilesystemNetworkNamespaceAttestor", "UnixSocketBrokerTransport"]
