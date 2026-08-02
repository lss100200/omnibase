"""Deployment-seam tests for namespace attestation and local Broker transport."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

import omnibase.sandbox.network_runtime as network_runtime_module
from omnibase.sandbox.broker import BrokerConnectionPlan, VerifiedNetworkNamespace
from omnibase.sandbox.contracts import SandboxRejected, SandboxUnavailable
from omnibase.sandbox.network import (
    LogicalNetworkService,
    NetworkDestination,
    NetworkProtocol,
    NetworkRouteKind,
    SandboxNetworkAuthorizationRequest,
    SandboxNetworkBudget,
    VerifiedSandboxNetworkAuthorization,
    stable_digest,
)
from omnibase.sandbox.network_runtime import (
    FilesystemNetworkNamespaceAttestor,
    UnixSocketBrokerTransport,
)

NOW = datetime(2026, 8, 2, 17, 0, tzinfo=UTC)
TENANT_ID = UUID("40000000-0000-4000-8000-000000000001")
WORKSPACE_ID = UUID("40000000-0000-4000-8000-000000000002")
RUN_ID = UUID("40000000-0000-4000-8000-000000000003")
RUNTIME_ID = UUID("40000000-0000-4000-8000-000000000004")
NODE_ID = UUID("40000000-0000-4000-8000-000000000005")
LEASE_ID = UUID("40000000-0000-4000-8000-000000000006")
SERVICE_ID = UUID("40000000-0000-4000-8000-000000000007")
OPERATION_ID = UUID("40000000-0000-4000-8000-000000000008")
NAMESPACE_ID = UUID("40000000-0000-4000-8000-000000000009")
RUNNER_ID = UUID("40000000-0000-4000-8000-00000000000a")
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def _process_start_time_ticks() -> int:
    text = Path(f"/proc/{os.getpid()}/stat").read_text(encoding="ascii")
    return int(text[text.rindex(")") + 2 :].split()[19])


def _live_namespace_identity() -> str:
    info = Path(f"/proc/{os.getpid()}/ns/net").stat()
    return f"{info.st_dev}:{info.st_ino}"


def _namespace() -> VerifiedNetworkNamespace:
    return VerifiedNetworkNamespace(
        namespace_id=NAMESPACE_ID,
        network_namespace_identity=_live_namespace_identity(),
        namespace_process_id=os.getpid(),
        namespace_process_start_time_ticks=_process_start_time_ticks(),
        runner_id=RUNNER_ID,
        node_id=NODE_ID,
        runtime_instance_id=RUNTIME_ID,
        workload_identity_thumbprint=DIGEST_A,
        workspace_generation=3,
        run_fencing_token=5,
        node_fencing_token=7,
        network_fencing_token=11,
        policy_digest=DIGEST_B,
        direct_overlay=False,
        verified_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=60),
        evidence_digest=DIGEST_C,
    )


def _authorization() -> VerifiedSandboxNetworkAuthorization:
    request = SandboxNetworkAuthorizationRequest(
        operation_id=OPERATION_ID,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        run_id=RUN_ID,
        runtime_instance_id=RUNTIME_ID,
        node_id=NODE_ID,
        network_lease_id=LEASE_ID,
        logical_service_id=SERVICE_ID,
        workload_identity_thumbprint=DIGEST_A,
        workspace_generation=3,
        run_fencing_token=5,
        node_fencing_token=7,
        network_fencing_token=11,
        service_version=13,
        protocol=NetworkProtocol.TCP,
        port=8443,
        requested_connections=1,
        requested_bytes_in=1_024,
        requested_bytes_out=2_048,
        deadline=NOW + timedelta(seconds=30),
    )
    service = LogicalNetworkService(
        service_id=SERVICE_ID,
        tenant_id=TENANT_ID,
        workspace_id=WORKSPACE_ID,
        publisher_node_id=UUID("40000000-0000-4000-8000-00000000000b"),
        logical_name="workspace.gateway",
        protocol=NetworkProtocol.TCP,
        logical_port=8443,
        workspace_generation=3,
        publisher_node_fencing_token=17,
        network_fencing_token=11,
        service_version=13,
        expires_at=NOW + timedelta(seconds=60),
    )
    return VerifiedSandboxNetworkAuthorization(
        request=request,
        service=service,
        expected_runner_id=RUNNER_ID,
        expected_namespace_id=NAMESPACE_ID,
        expected_network_namespace_identity=_live_namespace_identity(),
        expected_namespace_process_id=os.getpid(),
        expected_namespace_process_start_time_ticks=_process_start_time_ticks(),
        budget=SandboxNetworkBudget(4, 8_192, 8_192, 60),
        allowed_service_ids=(SERVICE_ID,),
        policy_digest=DIGEST_B,
        verified_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=60),
        verification_digest=DIGEST_C,
    )


def _private_directory(tmp_path: Path, name: str) -> Path:
    directory = tmp_path / name
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    return directory


def _evidence_value(*, namespace_identity: str | None = None) -> dict[str, object]:
    selected_identity = namespace_identity or _live_namespace_identity()
    value: dict[str, object] = {
        "direct_overlay": False,
        "expires_at": (NOW + timedelta(seconds=60)).isoformat(),
        "namespace_id": str(NAMESPACE_ID),
        "network_fencing_token": 11,
        "network_namespace_identity": selected_identity,
        "namespace_process_id": os.getpid(),
        "namespace_process_start_time_ticks": _process_start_time_ticks(),
        "node_fencing_token": 7,
        "node_id": str(NODE_ID),
        "policy_digest": DIGEST_B,
        "run_fencing_token": 5,
        "runner_id": str(RUNNER_ID),
        "runtime_instance_id": str(RUNTIME_ID),
        "verified_at": (NOW - timedelta(seconds=1)).isoformat(),
        "workload_identity_thumbprint": DIGEST_A,
        "workspace_generation": 3,
    }
    value["evidence_digest"] = stable_digest(value)
    return value


def _write_evidence(directory: Path, value: dict[str, object]) -> Path:
    path = directory / f"{RUNTIME_ID}.network-namespace.json"
    path.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    return path


def _simulate_root_owned_host_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    host_snapshot: Path,
) -> None:
    """Keep production root-owner checks while adapting tmp_path fixtures."""

    real_lstat = Path.lstat
    real_fstat = os.fstat

    def fixture_lstat(path: Path) -> os.stat_result:
        info = real_lstat(path)
        if path != host_snapshot.parent:
            return info
        values = list(info)
        values[4] = 0
        return os.stat_result(values)

    def fixture_fstat(descriptor: int) -> os.stat_result:
        info = real_fstat(descriptor)
        try:
            descriptor_path = Path(f"/proc/self/fd/{descriptor}").resolve()
        except OSError:
            return info
        if descriptor_path != host_snapshot:
            return info
        values = list(info)
        values[4] = 0
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", fixture_lstat)
    monkeypatch.setattr(os, "fstat", fixture_fstat)


@pytest.mark.skipif(os.name != "posix", reason="production attestor is POSIX-only")
def test_filesystem_attestor_binds_private_current_non_host_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_directory = _private_directory(tmp_path, "namespace-evidence")
    host_snapshot = tmp_path / "host-net"
    host_snapshot.write_text("1:4026531992\n", encoding="ascii")
    host_snapshot.chmod(0o600)
    monkeypatch.setattr(network_runtime_module, "_HOST_SNAPSHOT_PATH", host_snapshot)
    _simulate_root_owned_host_snapshot(monkeypatch, host_snapshot)
    _write_evidence(evidence_directory, _evidence_value())

    attestor = FilesystemNetworkNamespaceAttestor(
        evidence_directory=evidence_directory,
        host_network_namespace_path=host_snapshot,
        trusted_owner_uid=os.geteuid(),
        clock=lambda: NOW,
    )
    proof = attestor.attest(authorization=_authorization())

    assert proof.runtime_instance_id == RUNTIME_ID
    assert proof.network_namespace_identity == _live_namespace_identity()
    assert proof.evidence_digest == _evidence_value()["evidence_digest"]


@pytest.mark.skipif(os.name != "posix", reason="production attestor is POSIX-only")
def test_filesystem_attestor_rejects_host_namespace_symlink_and_broad_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence_directory = _private_directory(tmp_path, "namespace-evidence")
    host_snapshot = tmp_path / "host-net"
    host_snapshot.write_text(f"{_live_namespace_identity()}\n", encoding="ascii")
    host_snapshot.chmod(0o600)
    monkeypatch.setattr(network_runtime_module, "_HOST_SNAPSHOT_PATH", host_snapshot)
    _simulate_root_owned_host_snapshot(monkeypatch, host_snapshot)
    evidence_path = _write_evidence(evidence_directory, _evidence_value())
    attestor = FilesystemNetworkNamespaceAttestor(
        evidence_directory=evidence_directory,
        host_network_namespace_path=host_snapshot,
        trusted_owner_uid=os.geteuid(),
        clock=lambda: NOW,
    )
    with pytest.raises(SandboxRejected, match="not_isolated"):
        attestor.attest(authorization=_authorization())

    evidence_path.unlink()
    target = tmp_path / "attacker-evidence.json"
    target.write_text(json.dumps(_evidence_value()), encoding="utf-8")
    target.chmod(0o600)
    evidence_path.symlink_to(target)
    with pytest.raises(SandboxUnavailable, match="evidence_unavailable"):
        attestor.attest(authorization=_authorization())

    evidence_path.unlink()
    evidence_path = _write_evidence(evidence_directory, _evidence_value())
    evidence_path.chmod(0o666)
    with pytest.raises(SandboxUnavailable, match="evidence_untrusted"):
        attestor.attest(authorization=_authorization())


def _plan() -> BrokerConnectionPlan:
    return BrokerConnectionPlan(
        operation_id=OPERATION_ID,
        request_binding_digest=DIGEST_A,
        authorization_digest=DIGEST_B,
        namespace_evidence_digest=DIGEST_C,
        destination_resolution_digest=DIGEST_D,
        plan_digest="e" * 64,
    )


def _destination(
    *,
    route_kind: NetworkRouteKind = NetworkRouteKind.WORKSPACE_SERVICE,
) -> NetworkDestination:
    return NetworkDestination.from_text(
        service_id=SERVICE_ID,
        protocol=NetworkProtocol.TCP,
        port=8443,
        address="8.8.8.8",
        route_kind=route_kind,
        resolution_digest=DIGEST_D,
        resolved_at=NOW - timedelta(seconds=1),
        expires_at=NOW + timedelta(seconds=60),
    )


@pytest.mark.skipif(
    os.name != "posix" or os.geteuid() != 0,
    reason="dedicated-UID AF_UNIX peer test requires a POSIX root test container",
)
def test_unix_transport_uses_private_peer_credentialed_bounded_protocol(
    request: pytest.FixtureRequest,
) -> None:
    peer_uid = 65_534
    peer_gid = 65_534
    socket_directory = Path(tempfile.mkdtemp(prefix="omnibase-broker-test-", dir="/tmp"))
    request.addfinalizer(lambda: shutil.rmtree(socket_directory, ignore_errors=True))
    os.chown(socket_directory, peer_uid, peer_gid)
    socket_path = socket_directory / "broker.sock"
    request_path = socket_directory / "request.json"
    code = """
import hashlib,hmac,json,os,socket,sys,time
path=sys.argv[1]
request_path=sys.argv[2]
server=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
server.bind(path)
os.chmod(path,0o600)
server.listen(1)
print('READY',flush=True)
connection,_=server.accept()
chunks=[]
while True:
    chunk=connection.recv(16384)
    if not chunk:
        break
    chunks.append(chunk)
raw_request=b''.join(chunks)
with open(request_path,'wb') as handle:
    handle.write(raw_request)
request=json.loads(raw_request)
response={
    'accepted_at':'2026-08-02T17:00:00+00:00',
    'bytes_in':512,
    'bytes_out':1024,
    'connections':1,
    'destination_resolution_digest':'dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd',
    'namespace_evidence_digest':'cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc',
    'operation_id':'40000000-0000-4000-8000-000000000008',
    'plan_digest':'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee',
    'request_binding_digest':'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
}
response['challenge_response']=hmac.new(
    b'k' * 32,
    f"{request['challenge']}:{response['operation_id']}:{response['plan_digest']}".encode('ascii'),
    hashlib.sha256,
).hexdigest()
connection.sendall(json.dumps(response).encode('utf-8'))
connection.close()
server.close()
time.sleep(1.0)
"""

    def demote() -> None:
        os.setgid(peer_gid)
        os.setuid(peer_uid)

    process = subprocess.Popen(
        [sys.executable, "-c", code, str(socket_path), str(request_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        preexec_fn=demote,
    )
    request.addfinalizer(lambda: process.kill() if process.poll() is None else None)
    assert process.stdout is not None
    ready = process.stdout.readline().strip()
    if ready != "READY":
        _, stderr = process.communicate(timeout=5)
        pytest.fail(f"dedicated broker test process failed before ready: {stderr}")
    receipt = UnixSocketBrokerTransport(
        socket_path=socket_path,
        trusted_peer_uid=peer_uid,
        trusted_peer_gid=peer_gid,
        daemon_authentication_key=b"k" * 32,
    ).connect(
        plan=_plan(),
        namespace=_namespace(),
        destination=_destination(),
    )
    _, stderr = process.communicate(timeout=5)
    assert process.returncode == 0, stderr

    assert receipt.operation_id == OPERATION_ID
    received = json.loads(request_path.read_text(encoding="utf-8"))
    assert received["protocol"] == "omnibase-broker-connect-v1"
    serialized = json.dumps(received, sort_keys=True)
    assert "public_internet" not in serialized
    assert "member_overlay" not in serialized
    assert "credential" not in serialized


@pytest.mark.skipif(os.name != "posix", reason="AF_UNIX peer credentials are POSIX-only")
def test_unix_transport_rejects_public_or_member_overlay_before_socket_use(
    tmp_path: Path,
) -> None:
    missing_socket = _private_directory(tmp_path, "broker-socket") / "missing.sock"
    transport = UnixSocketBrokerTransport(
        socket_path=missing_socket,
        trusted_peer_uid=65_534,
        trusted_peer_gid=65_534,
        daemon_authentication_key=b"k" * 32,
    )
    namespace = _namespace()
    for route_kind in (NetworkRouteKind.PUBLIC_INTERNET, NetworkRouteKind.MEMBER_OVERLAY):
        with pytest.raises(SandboxRejected):
            transport.connect(
                plan=_plan(),
                namespace=namespace,
                destination=_destination(route_kind=route_kind),
            )


@pytest.mark.parametrize("invalid_value", [True, "1", 1.5])
@pytest.mark.parametrize("field", ["connections", "bytes_in", "bytes_out"])
def test_receipt_parser_rejects_non_exact_integer_types(
    field: str,
    invalid_value: object,
) -> None:
    value: dict[str, object] = {
        "accepted_at": NOW.isoformat(),
        "bytes_in": 512,
        "bytes_out": 1_024,
        "connections": 1,
        "destination_resolution_digest": DIGEST_D,
        "namespace_evidence_digest": DIGEST_C,
        "operation_id": str(OPERATION_ID),
        "plan_digest": "e" * 64,
        "request_binding_digest": DIGEST_A,
    }
    value[field] = invalid_value
    with pytest.raises(SandboxRejected):
        UnixSocketBrokerTransport._parse_receipt(json.dumps(value).encode("utf-8"))
