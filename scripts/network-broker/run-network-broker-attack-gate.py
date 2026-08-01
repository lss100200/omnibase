#!/usr/bin/env python3
"""Run the P34.5B Broker Gate on the independent hardened Linux VM.

This gate requires root because it creates disposable network namespaces and
root-pinned nsfs handles.  It never uses Docker, a business database, an
Overlay credential or external network access.  The test service exists only
on a globally-classified loopback address inside the disposable workload
namespace, so a successful connection proves that the Broker worker entered
the requested namespace before opening the socket.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import pwd
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

_SOCKET: Final = Path("/run/omnibase-network-broker-daemon/broker.sock")
_PERMIT_ROOT: Final = Path("/run/omnibase-network-broker-permits")
_CONSUMED_ROOT: Final = Path("/var/lib/omnibase-network-broker/consumed")
_HOST_NET: Final = Path("/run/omnibase-host-ns/net")
_AUTHENTICATION_KEY: Final = Path("/etc/omnibase-network-broker/daemon-auth.key")
_SERVICE: Final = "omnibase-network-broker.service"
_BROKER_USER: Final = "omnibase-network-broker"
_ADDRESS: Final = "11.254.254.2"
_PORT: Final = 18443
_BANNER: Final = b"gate"

_CLIENT_CODE: Final = r"""
import os, socket, struct, sys
def start_ticks(pid):
    raw=open(f'/proc/{pid}/stat', encoding='ascii').read()
    return int(raw[raw.rindex(')')+2:].split()[19])
expected_uid=int(sys.argv[2]); expected_gid=int(sys.argv[3])
before=os.stat(sys.argv[1], follow_symlinks=False)
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.settimeout(3)
s.connect(sys.argv[1])
peer=struct.unpack('3i',s.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
evidence=(peer[0],peer[1],peer[2],start_ticks(peer[0]))
if peer[1:] != (expected_uid,expected_gid): raise SystemExit(21)
current=os.stat(sys.argv[1], follow_symlinks=False)
if (before.st_dev,before.st_ino)!=(current.st_dev,current.st_ino): raise SystemExit(22)
s.sendall(sys.stdin.buffer.read())
s.shutdown(socket.SHUT_WR)
chunks = []
while True:
    chunk = s.recv(16384)
    if not chunk:
        break
    chunks.append(chunk)
peer2=struct.unpack('3i',s.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
if (peer2[0],peer2[1],peer2[2],start_ticks(peer2[0])) != evidence: raise SystemExit(23)
after=os.stat(sys.argv[1], follow_symlinks=False)
if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino): raise SystemExit(24)
sys.stdout.buffer.write(b''.join(chunks))
"""


def _run(command: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, check=check, text=True, capture_output=True)


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _process_start_time_ticks(process_id: int) -> int:
    raw = Path(f"/proc/{process_id}/stat").read_text(encoding="ascii")
    closing = raw.rindex(")")
    value = int(raw[closing + 2 :].split()[19])
    if value < 1:
        raise RuntimeError("process start time is invalid")
    return value


def _request(
    *,
    operation_id: UUID,
    runtime_id: UUID,
    namespace_id: UUID,
    namespace_identity: str,
    namespace_process_id: int,
    namespace_process_start_time_ticks: int,
    route_kind: str = "workspace_service",
    address: str = _ADDRESS,
) -> dict[str, object]:
    return {
        "challenge": _digest(f"challenge:{operation_id}"),
        "destination": {
            "address": address,
            "port": _PORT,
            "protocol": "tcp",
            "resolution_digest": _digest(f"destination:{operation_id}"),
            "route_kind": route_kind,
            "service_id": str(UUID("50000000-0000-4000-8000-000000000001")),
        },
        "namespace": {
            "evidence_digest": _digest(f"namespace:{operation_id}"),
            "namespace_id": str(namespace_id),
            "network_namespace_identity": namespace_identity,
            "namespace_process_id": namespace_process_id,
            "namespace_process_start_time_ticks": namespace_process_start_time_ticks,
            "runtime_instance_id": str(runtime_id),
        },
        "plan": {
            "authorization_digest": _digest(f"authorization:{operation_id}"),
            "destination_resolution_digest": _digest(f"destination:{operation_id}"),
            "namespace_evidence_digest": _digest(f"namespace:{operation_id}"),
            "operation_id": str(operation_id),
            "plan_digest": _digest(f"plan:{operation_id}"),
            "request_binding_digest": _digest(f"request:{operation_id}"),
        },
        "protocol": "omnibase-broker-connect-v1",
    }


def _write_permit(
    request: dict[str, object],
    *,
    broker_gid: int,
    max_connections: int = 1,
    max_bytes_in: int = len(_BANNER),
) -> Path:
    destination = request["destination"]
    namespace = request["namespace"]
    plan = request["plan"]
    assert isinstance(destination, dict)
    assert isinstance(namespace, dict)
    assert isinstance(plan, dict)
    operation_id = str(plan["operation_id"])
    now = datetime.now(UTC)
    permit = {
        "address": destination["address"],
        "authorization_digest": plan["authorization_digest"],
        "destination_resolution_digest": plan["destination_resolution_digest"],
        "expires_at": (now + timedelta(minutes=2)).isoformat(),
        "max_bytes_in": max_bytes_in,
        "max_bytes_out": 0,
        "max_connections": max_connections,
        "namespace_evidence_digest": plan["namespace_evidence_digest"],
        "namespace_id": namespace["namespace_id"],
        "network_namespace_identity": namespace["network_namespace_identity"],
        "namespace_process_id": namespace["namespace_process_id"],
        "namespace_process_start_time_ticks": namespace["namespace_process_start_time_ticks"],
        "network_protocol": destination["protocol"],
        "not_before": (now - timedelta(seconds=5)).isoformat(),
        "operation_id": operation_id,
        "plan_digest": plan["plan_digest"],
        "port": destination["port"],
        "protocol": "omnibase-broker-permit-v1",
        "request_binding_digest": plan["request_binding_digest"],
        "route_kind": destination["route_kind"],
        "runtime_instance_id": namespace["runtime_instance_id"],
        "service_id": destination["service_id"],
    }
    path = _PERMIT_ROOT / f"{operation_id}.permit.json"
    descriptor = os.open(
        path,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o440,
    )
    try:
        os.write(
            descriptor,
            json.dumps(permit, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.chown(path, 0, broker_gid)
    os.chmod(path, 0o440)
    return path


def _send_as(
    request: dict[str, object],
    *,
    uid: int,
    gid: int,
) -> subprocess.CompletedProcess[bytes]:
    payload = json.dumps(request, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if uid == os.getuid() and gid == os.getgid():
        command = [
            sys.executable,
            "-c",
            _CLIENT_CODE,
            str(_SOCKET),
            str(pwd.getpwnam(_BROKER_USER).pw_uid),
            str(pwd.getpwnam(_BROKER_USER).pw_gid),
        ]
    else:
        command = [
            "setpriv",
            "--reuid",
            str(uid),
            "--regid",
            str(gid),
            "--clear-groups",
            sys.executable,
            "-c",
            _CLIENT_CODE,
            str(_SOCKET),
            str(pwd.getpwnam(_BROKER_USER).pw_uid),
            str(pwd.getpwnam(_BROKER_USER).pw_gid),
        ]
    return subprocess.run(
        command,
        input=payload,
        capture_output=True,
        check=False,
    )


def _wait_for_socket() -> None:
    for _ in range(50):
        if _SOCKET.is_socket():
            return
        time.sleep(0.1)
    raise RuntimeError("Broker socket did not become ready")


def _start_banner_server(namespace_pid: int) -> subprocess.Popen[bytes]:
    code = (
        "import socket;"
        "s=socket.socket();"
        "s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);"
        f"s.bind(('{_ADDRESS}',{_PORT}));"
        "s.listen(1);"
        "c,_=s.accept();"
        f"c.sendall({_BANNER!r});"
        "c.close();s.close()"
    )
    process = subprocess.Popen(
        [
            "nsenter",
            "--target",
            str(namespace_pid),
            "--net",
            sys.executable,
            "-c",
            code,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    time.sleep(0.2)
    if process.poll() is not None:
        stderr = process.stderr.read().decode("utf-8", errors="replace") if process.stderr else ""
        raise RuntimeError(f"banner server failed: {stderr}")
    return process


def _namespace_identity(process_id: int) -> str:
    info = Path(f"/proc/{process_id}/ns/net").stat()
    if info.st_dev < 1 or info.st_ino < 1:
        raise RuntimeError("network namespace identity is invalid")
    return f"{info.st_dev}:{info.st_ino}"


def _assert_generic_rejection(response: subprocess.CompletedProcess[bytes]) -> None:
    if response.returncode != 0:
        raise AssertionError(response.stderr.decode("utf-8", errors="replace"))
    if json.loads(response.stdout) != {"error": "broker_request_rejected"}:
        raise AssertionError("daemon did not return its generic rejection")


def _assert_namespace_connect_rejected(
    *,
    namespace_pid: int,
    address: str,
    port: int,
) -> None:
    code = (
        "import socket,sys;"
        "family=socket.AF_INET6 if ':' in sys.argv[1] else socket.AF_INET;"
        "s=socket.socket(family,socket.SOCK_STREAM);s.settimeout(.5);"
        "result=s.connect_ex((sys.argv[1],int(sys.argv[2])));s.close();"
        "print(result);raise SystemExit(0 if result != 0 else 7)"
    )
    result = _run(
        [
            "nsenter",
            "--target",
            str(namespace_pid),
            "--net",
            sys.executable,
            "-c",
            code,
            address,
            str(port),
        ],
        check=False,
    )
    if result.returncode != 0 or not result.stdout.strip().isdigit():
        raise AssertionError(f"disposable namespace unexpectedly reached {address}:{port}")


def _verify_challenge(
    *,
    receipt: dict[str, object],
    request: dict[str, object],
    authentication_key: bytes,
) -> bool:
    plan = request["plan"]
    assert isinstance(plan, dict)
    expected = hmac.new(
        authentication_key,
        (f"{request['challenge']}:{plan['operation_id']}:{plan['plan_digest']}").encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(str(receipt.get("challenge_response")), expected)


# The attack matrix intentionally keeps setup, ordered security assertions, and
# cleanup in one transaction so a partial run cannot be mistaken for a pass.
def run_gate() -> dict[str, object]:  # noqa: C901
    if os.name != "posix" or os.geteuid() != 0:
        raise RuntimeError("this Gate must run as root on Linux")
    for executable in ("ip", "nsenter", "setpriv", "systemctl", "unshare"):
        if shutil.which(executable) is None:
            raise RuntimeError(f"required executable missing: {executable}")
    broker = pwd.getpwnam(_BROKER_USER)
    _wait_for_socket()
    if not _SOCKET.exists() or not _HOST_NET.exists() or not _AUTHENTICATION_KEY.exists():
        raise RuntimeError("Broker deployment is not installed and ready")
    authentication_key = bytes.fromhex(_AUTHENTICATION_KEY.read_text(encoding="ascii").strip())
    if len(authentication_key) != 32:
        raise RuntimeError("Broker authentication key is invalid")

    runtime_id = uuid4()
    namespace_id = uuid4()
    success_operation = uuid4()
    public_operation = uuid4()
    member_operation = uuid4()
    cross_operation = uuid4()
    host_operation = uuid4()
    connection_budget_operation = uuid4()
    bytes_budget_operation = uuid4()
    stale_pid_operation = uuid4()
    wrong_start_operation = uuid4()
    wrong_identity_operation = uuid4()
    created_paths: list[Path] = []
    namespace_process: subprocess.Popen[bytes] | None = None
    banner_process: subprocess.Popen[bytes] | None = None
    results: dict[str, object] = {}
    final_report: dict[str, object] | None = None

    try:
        namespace_process = subprocess.Popen(
            ["unshare", "--net", "sleep", "300"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        time.sleep(0.2)
        if namespace_process.poll() is not None:
            stderr = (
                namespace_process.stderr.read().decode("utf-8", errors="replace")
                if namespace_process.stderr
                else ""
            )
            raise RuntimeError(f"unshare failed: {stderr}")
        namespace_pid = namespace_process.pid
        namespace_start_time = _process_start_time_ticks(namespace_pid)
        _run(
            [
                "nsenter",
                "--target",
                str(namespace_pid),
                "--net",
                "ip",
                "link",
                "set",
                "lo",
                "up",
            ]
        )
        _run(
            [
                "nsenter",
                "--target",
                str(namespace_pid),
                "--net",
                "ip",
                "address",
                "add",
                f"{_ADDRESS}/32",
                "dev",
                "lo",
            ]
        )

        _assert_namespace_connect_rejected(
            namespace_pid=namespace_pid,
            address="1.1.1.1",
            port=443,
        )
        results["NETNS-DIRECT-PUBLIC-DENY"] = "PASS"
        _assert_namespace_connect_rejected(
            namespace_pid=namespace_pid,
            address="172.29.142.68",
            port=22,
        )
        results["NETNS-DIRECT-HOST-DENY"] = "PASS"

        runtime_identity = _namespace_identity(namespace_pid)
        host_runtime_id = uuid4()
        host_identity = _namespace_identity(1)

        success_request = _request(
            operation_id=success_operation,
            runtime_id=runtime_id,
            namespace_id=namespace_id,
            namespace_identity=runtime_identity,
            namespace_process_id=namespace_pid,
            namespace_process_start_time_ticks=namespace_start_time,
        )
        public_request = _request(
            operation_id=public_operation,
            runtime_id=runtime_id,
            namespace_id=namespace_id,
            namespace_identity=runtime_identity,
            namespace_process_id=namespace_pid,
            namespace_process_start_time_ticks=namespace_start_time,
            route_kind="public_internet",
        )
        member_request = _request(
            operation_id=member_operation,
            runtime_id=runtime_id,
            namespace_id=namespace_id,
            namespace_identity=runtime_identity,
            namespace_process_id=namespace_pid,
            namespace_process_start_time_ticks=namespace_start_time,
            route_kind="member_overlay",
        )
        cross_request = _request(
            operation_id=cross_operation,
            runtime_id=uuid4(),
            namespace_id=namespace_id,
            namespace_identity=runtime_identity,
            namespace_process_id=namespace_pid,
            namespace_process_start_time_ticks=namespace_start_time,
        )
        cross_permit_request = _request(
            operation_id=cross_operation,
            runtime_id=runtime_id,
            namespace_id=namespace_id,
            namespace_identity=runtime_identity,
            namespace_process_id=namespace_pid,
            namespace_process_start_time_ticks=namespace_start_time,
        )
        host_request = _request(
            operation_id=host_operation,
            runtime_id=host_runtime_id,
            namespace_id=uuid4(),
            namespace_identity=host_identity,
            namespace_process_id=1,
            namespace_process_start_time_ticks=_process_start_time_ticks(1),
        )
        connection_budget_request = _request(
            operation_id=connection_budget_operation,
            runtime_id=runtime_id,
            namespace_id=namespace_id,
            namespace_identity=runtime_identity,
            namespace_process_id=namespace_pid,
            namespace_process_start_time_ticks=namespace_start_time,
        )
        bytes_budget_request = _request(
            operation_id=bytes_budget_operation,
            runtime_id=runtime_id,
            namespace_id=namespace_id,
            namespace_identity=runtime_identity,
            namespace_process_id=namespace_pid,
            namespace_process_start_time_ticks=namespace_start_time,
        )
        stale_pid_request = _request(
            operation_id=stale_pid_operation,
            runtime_id=runtime_id,
            namespace_id=namespace_id,
            namespace_identity=runtime_identity,
            namespace_process_id=2**31 - 1,
            namespace_process_start_time_ticks=1,
        )
        wrong_start_request = _request(
            operation_id=wrong_start_operation,
            runtime_id=runtime_id,
            namespace_id=namespace_id,
            namespace_identity=runtime_identity,
            namespace_process_id=namespace_pid,
            namespace_process_start_time_ticks=namespace_start_time + 1,
        )
        identity_parts = runtime_identity.split(":")
        wrong_identity = f"{identity_parts[0]}:{int(identity_parts[1]) + 1}"
        wrong_identity_request = _request(
            operation_id=wrong_identity_operation,
            runtime_id=runtime_id,
            namespace_id=namespace_id,
            namespace_identity=wrong_identity,
            namespace_process_id=namespace_pid,
            namespace_process_start_time_ticks=namespace_start_time,
        )
        for request in (
            success_request,
            cross_permit_request,
            host_request,
            stale_pid_request,
            wrong_start_request,
            wrong_identity_request,
        ):
            created_paths.append(_write_permit(request, broker_gid=broker.pw_gid))
        created_paths.append(
            _write_permit(
                connection_budget_request,
                broker_gid=broker.pw_gid,
                max_connections=0,
            )
        )
        created_paths.append(
            _write_permit(
                bytes_budget_request,
                broker_gid=broker.pw_gid,
                max_bytes_in=len(_BANNER) - 1,
            )
        )

        _run(["systemctl", "restart", _SERVICE])
        _wait_for_socket()

        _assert_generic_rejection(_send_as(public_request, uid=0, gid=0))
        results["NET-PUBLIC"] = "PASS"
        _assert_generic_rejection(_send_as(member_request, uid=0, gid=0))
        results["NET-MEMBER"] = "PASS"
        blocked_destinations = {
            "DEST-LOOPBACK": "127.0.0.1",
            "DEST-METADATA": "169.254.169.254",
            "DEST-RFC1918": "10.23.45.67",
            "DEST-ULA": "fd00::1",
            "DEST-MULTICAST": "224.0.0.1",
            "DEST-RESERVED": "240.0.0.1",
        }
        for result_name, address in blocked_destinations.items():
            blocked_request = _request(
                operation_id=uuid4(),
                runtime_id=runtime_id,
                namespace_id=namespace_id,
                namespace_identity=runtime_identity,
                namespace_process_id=namespace_pid,
                namespace_process_start_time_ticks=namespace_start_time,
                address=address,
            )
            _assert_generic_rejection(_send_as(blocked_request, uid=0, gid=0))
            results[result_name] = "PASS"
        _assert_generic_rejection(_send_as(cross_request, uid=0, gid=0))
        results["CROSS-01"] = "PASS"
        _assert_generic_rejection(_send_as(host_request, uid=0, gid=0))
        results["NETNS-HOST"] = "PASS"
        _assert_generic_rejection(_send_as(stale_pid_request, uid=0, gid=0))
        results["NETNS-STALE-PID"] = "PASS"
        _assert_generic_rejection(_send_as(wrong_start_request, uid=0, gid=0))
        results["NETNS-WRONG-STARTTIME"] = "PASS"
        _assert_generic_rejection(_send_as(wrong_identity_request, uid=0, gid=0))
        results["NETNS-WRONG-IDENTITY"] = "PASS"
        _assert_generic_rejection(_send_as(connection_budget_request, uid=0, gid=0))
        results["BUDGET-CONNECTION-EXCEEDED"] = "PASS"

        untrusted = _send_as(public_request, uid=65534, gid=65534)
        if untrusted.returncode == 0:
            raise AssertionError("untrusted UID connected to the private Broker socket")
        results["SOCKET-IMPERSONATION"] = "PASS"

        banner_process = _start_banner_server(namespace_pid)
        _assert_generic_rejection(_send_as(bytes_budget_request, uid=0, gid=0))
        banner_process.wait(timeout=5)
        banner_process = None
        created_paths.append(_CONSUMED_ROOT / f"{bytes_budget_operation}.consumed.json")
        results["BUDGET-BYTES-EXCEEDED"] = "PASS"

        banner_process = _start_banner_server(namespace_pid)
        success = _send_as(success_request, uid=0, gid=0)
        if success.returncode != 0:
            raise AssertionError(success.stderr.decode("utf-8", errors="replace"))
        receipt = json.loads(success.stdout)
        if (
            receipt.get("operation_id") != str(success_operation)
            or receipt.get("connections") != 1
            or receipt.get("bytes_in") != len(_BANNER)
            or receipt.get("bytes_out") != 0
            or not _verify_challenge(
                receipt=receipt,
                request=success_request,
                authentication_key=authentication_key,
            )
        ):
            raise AssertionError("measured Broker receipt rejected")
        results["NETNS-CONNECT"] = "PASS"
        results["MEASURED-BUDGET"] = "PASS"
        results["DAEMON-CHALLENGE"] = "PASS"
        results["SOCKET-PEER-CONTINUITY"] = "PASS"
        forged_receipt = dict(receipt)
        forged_receipt["challenge_response"] = "0" * 64
        if _verify_challenge(
            receipt=forged_receipt,
            request=success_request,
            authentication_key=authentication_key,
        ) or _verify_challenge(
            receipt=receipt,
            request=success_request,
            authentication_key=b"\xff" * 32,
        ):
            raise AssertionError("forged Broker challenge was accepted")
        results["DAEMON-CHALLENGE-FORGERY"] = "PASS"

        replay = _send_as(success_request, uid=0, gid=0)
        _assert_generic_rejection(replay)
        results["REPLAY-01"] = "PASS"

        consumed = _CONSUMED_ROOT / f"{success_operation}.consumed.json"
        if not consumed.is_file():
            raise AssertionError("durable operation consumption evidence missing")
        created_paths.append(consumed)
        results["DURABLE-CONSUME"] = "PASS"

        status = _run(["systemctl", "is-active", _SERVICE]).stdout.strip()
        if status != "active":
            raise AssertionError("Broker service unhealthy after Gate")
        results["SERVICE-HEALTH"] = "PASS"
        final_report = {
            "attack_gate": results,
            "broker_uid": broker.pw_uid,
            "completed_at": datetime.now(UTC).isoformat(),
            "external_network_connection_succeeded": False,
            "hostile_code_production_claimed": False,
            "passed": all(value == "PASS" for value in results.values()),
            "tests": len(results),
        }
        return final_report
    finally:
        if banner_process is not None and banner_process.poll() is None:
            banner_process.kill()
            banner_process.wait(timeout=5)
        for path in reversed(created_paths):
            with suppress(OSError):
                path.unlink(missing_ok=True)
        if namespace_process is not None and namespace_process.poll() is None:
            namespace_process.kill()
            namespace_process.wait(timeout=5)
        _run(["systemctl", "restart", _SERVICE], check=False)
        if final_report is not None:
            residual_paths = [str(path) for path in created_paths if path.exists()]
            service_active = (
                _run(["systemctl", "is-active", _SERVICE], check=False).stdout.strip() == "active"
            )
            namespace_stopped = namespace_process is None or namespace_process.poll() is not None
            cleanup_passed = not residual_paths and service_active and namespace_stopped
            final_report["cleanup"] = {
                "namespace_stopped": namespace_stopped,
                "residual_gate_paths": residual_paths,
                "service_active": service_active,
            }
            if not cleanup_passed:
                final_report["passed"] = False


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        report = run_gate()
    except Exception as exc:
        report = {
            "error": type(exc).__name__,
            "passed": False,
            "reason": str(exc),
        }
    payload = json.dumps(report, indent=2, sort_keys=True)
    print(payload)
    if args.output is not None:
        if not args.output.is_absolute():
            raise SystemExit("--output must be absolute")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    return 0 if report.get("passed") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
