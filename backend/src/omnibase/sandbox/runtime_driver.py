"""Attested Linux local RuntimeDriver for an independent P34.5A4 Runner.

The driver never invokes a shell or container engine.  It sends one bounded,
canonical JSON request to an operator-installed isolation launcher whose digest
and host controls were attested by ``runtime_probe``.  The launcher is expected
to create the cgroup/namespaces and to return a strictly bound receipt.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import NoReturn, Protocol
from uuid import UUID

from omnibase.sandbox.contracts import SandboxRejected, SandboxUnavailable, utc_now
from omnibase.sandbox.dispatch_digest import runner_execution_binding_digest
from omnibase.sandbox.host import VerifiedRunnerHost
from omnibase.sandbox.runner import RunnerExecutionPlan, RunnerTerminationPlan
from omnibase.sandbox.runtime_probe import LinuxRuntimeAttestation

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REASON_RE = re.compile(r"^runner_[a-z0-9_]{2,92}$")
_MAX_LAUNCHER_RECEIPT_BYTES = 1024 * 1024
_MAX_LAUNCHER_ERROR_BYTES = 64 * 1024


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _runtime_spec_value(plan: RunnerExecutionPlan) -> dict[str, object]:
    spec = plan.runtime_spec
    return {
        "isolation": {
            "allow_devices": spec.isolation.allow_devices,
            "allow_host_mounts": spec.isolation.allow_host_mounts,
            "allow_runtime_socket": spec.isolation.allow_runtime_socket,
            "drop_all_capabilities": spec.isolation.drop_all_capabilities,
            "no_new_privileges": spec.isolation.no_new_privileges,
            "read_only_root": spec.isolation.read_only_root,
            "run_as_gid": spec.isolation.run_as_gid,
            "run_as_uid": spec.isolation.run_as_uid,
        },
        "limits": {
            "cpu_millis": spec.limits.cpu_millis,
            "inodes": spec.limits.inodes,
            "memory_bytes": spec.limits.memory_bytes,
            "output_bytes": spec.limits.output_bytes,
            "pids": spec.limits.pids,
            "wall_time_seconds": spec.limits.wall_time_seconds,
            "writable_bytes": spec.limits.writable_bytes,
        },
        "network": {
            "allowed_service_ids": [str(item) for item in spec.network.allowed_service_ids],
            "direct_overlay": spec.network.direct_overlay,
            "mode": spec.network.mode.value,
        },
        "policy_digest": spec.policy_digest,
        "template_digest": spec.template_digest,
    }


def execution_binding_digest(
    plan: RunnerExecutionPlan,
    host: VerifiedRunnerHost,
) -> str:
    """Compatibility entrypoint backed by the canonical Core digest."""
    return runner_execution_binding_digest(plan, host)


def termination_binding_digest(
    plan: RunnerTerminationPlan,
    host: VerifiedRunnerHost,
) -> str:
    request = plan.authorization.request
    return _digest(
        {
            "action": request.action.value,
            "authorization": plan.authorization.verification_digest,
            "controller_id": str(request.controller_id),
            "controller_identity": request.controller_identity_thumbprint,
            "deadline_at": request.deadline_at.isoformat(),
            "host_evidence": host.evidence_digest,
            "isolation_profile": plan.isolation_profile.digest(),
            "node_fencing_token": request.node_fencing_token,
            "node_id": str(request.node_id),
            "operation_id": str(request.operation_id),
            "reason_code": request.reason_code,
            "run_fencing_token": request.run_fencing_token,
            "run_id": str(request.run_id),
            "runner_id": str(host.runner_id),
            "runtime_handle": str(request.runtime_handle.value),
            "runtime_instance_id": str(request.runtime_instance_id),
            "tenant_id": str(request.tenant_id),
            "workspace_generation": request.workspace_generation,
            "workspace_id": str(request.workspace_id),
        }
    )


@dataclass(frozen=True, slots=True)
class RuntimeDriverReceipt:
    operation_id: UUID
    runtime_instance_id: UUID
    runner_id: UUID
    binding_digest: str
    evidence_digest: str
    reason_code: str
    exit_code: int | None
    stdout_digest: str
    stderr_digest: str
    namespace_evidence_digest: str
    namespaces_isolated: bool
    truncated: bool
    cgroup_empty: bool

    def __post_init__(self) -> None:
        if any(
            not isinstance(value, UUID)
            for value in (self.operation_id, self.runtime_instance_id, self.runner_id)
        ):
            raise TypeError("runtime receipt identifiers must be UUID values")
        for name, value in (
            ("binding_digest", self.binding_digest),
            ("evidence_digest", self.evidence_digest),
            ("stdout_digest", self.stdout_digest),
            ("stderr_digest", self.stderr_digest),
            ("namespace_evidence_digest", self.namespace_evidence_digest),
        ):
            if _SHA256_RE.fullmatch(value) is None:
                raise ValueError(f"{name} must be sha256")
        if _REASON_RE.fullmatch(self.reason_code) is None:
            raise ValueError("runtime receipt reason_code is invalid")
        if self.exit_code is not None and (
            isinstance(self.exit_code, bool)
            or not isinstance(self.exit_code, int)
            or self.exit_code < -255
            or self.exit_code > 255
        ):
            raise ValueError("runtime receipt exit_code is invalid")
        if not isinstance(self.truncated, bool):
            raise TypeError("runtime receipt truncated must be bool")
        if self.namespaces_isolated is not True:
            raise ValueError("runtime receipt must prove isolated execution namespaces")
        if self.cgroup_empty is not True:
            raise ValueError("runtime receipt must prove an empty execution cgroup")
        if self.reason_code == "runner_execution_succeeded" and self.exit_code != 0:
            raise ValueError("successful execution receipt requires exit code zero")


class IsolationLauncher(Protocol):
    @property
    def launcher_digest(self) -> str: ...

    @property
    def runner_root_digest(self) -> str: ...

    def invoke(
        self,
        *,
        mode: str,
        payload: bytes,
        timeout_seconds: int,
        bounded_kill_seconds: int,
    ) -> bytes: ...


class _BoundedCapture:
    def __init__(self, *, stdout_limit: int, stderr_limit: int) -> None:
        self._limits = {"stdout": stdout_limit, "stderr": stderr_limit}
        self._buffers = {"stdout": bytearray(), "stderr": bytearray()}
        self._lock = threading.Lock()
        self.overflow = threading.Event()

    def read(self, name: str, stream) -> None:
        try:
            while chunk := stream.read(64 * 1024):
                with self._lock:
                    target = self._buffers[name]
                    if len(target) + len(chunk) > self._limits[name]:
                        remaining = max(0, self._limits[name] - len(target))
                        target.extend(chunk[:remaining])
                        self.overflow.set()
                        return
                    target.extend(chunk)
        finally:
            stream.close()

    def value(self, name: str) -> bytes:
        with self._lock:
            return bytes(self._buffers[name])


class SubprocessIsolationLauncher:
    """Invoke one pinned isolation launcher with a clean environment."""

    def __init__(
        self,
        *,
        launcher_path: Path,
        expected_launcher_digest: str,
        runner_root: Path,
        cgroup_root: Path,
        launcher_owner_uid: int = 0,
    ) -> None:
        if any(not path.is_absolute() for path in (launcher_path, runner_root, cgroup_root)):
            raise ValueError("launcher, runner root and cgroup root paths must be absolute")
        if runner_root == Path("/") or cgroup_root == Path("/"):
            raise ValueError("runner and cgroup roots cannot be filesystem root")
        if _SHA256_RE.fullmatch(expected_launcher_digest) is None:
            raise ValueError("expected launcher digest must be sha256")
        if (
            isinstance(launcher_owner_uid, bool)
            or not isinstance(launcher_owner_uid, int)
            or launcher_owner_uid < 0
        ):
            raise ValueError("launcher owner UID is invalid")
        self._launcher_path = launcher_path
        self._expected_launcher_digest = expected_launcher_digest
        self._runner_root = runner_root
        self._cgroup_root = cgroup_root
        self._launcher_owner_uid = launcher_owner_uid
        self._launcher_digest = self._hash_launcher()
        self._runner_root_digest = self._hash_runner_root()
        self._cgroup_root_digest = self._hash_cgroup_root()

    @property
    def launcher_digest(self) -> str:
        return self._launcher_digest

    @property
    def runner_root_digest(self) -> str:
        return self._runner_root_digest

    def invoke(
        self,
        *,
        mode: str,
        payload: bytes,
        timeout_seconds: int,
        bounded_kill_seconds: int,
    ) -> bytes:
        if os.name != "posix":
            raise SandboxUnavailable("sandbox_linux_launcher_unavailable")
        if mode not in {"execute", "terminate"}:
            raise ValueError("unsupported isolation launcher mode")
        if len(payload) > 1024 * 1024:
            raise SandboxRejected("sandbox_launcher_request_too_large")
        if self._hash_launcher() != self._launcher_digest:
            raise SandboxRejected("sandbox_launcher_digest_changed")
        if self._hash_runner_root() != self._runner_root_digest:
            raise SandboxRejected("sandbox_runner_root_changed")
        if self._hash_cgroup_root() != self._cgroup_root_digest:
            raise SandboxRejected("sandbox_cgroup_root_changed")
        cgroup_name = self._cgroup_name(payload)
        cgroup_path = self._cgroup_root / str(cgroup_name)
        process: subprocess.Popen[bytes] | None = None
        try:
            process = self._spawn(mode)
            if process.stdin is None or process.stdout is None or process.stderr is None:
                raise SandboxUnavailable("sandbox_launcher_pipe_unavailable")
            capture, threads = self._start_capture(process)
            self._send_and_wait(
                process=process,
                payload=payload,
                capture=capture,
                cgroup_path=cgroup_path,
                timeout_seconds=timeout_seconds,
                bounded_kill_seconds=bounded_kill_seconds,
            )
            for thread in threads:
                thread.join(timeout=bounded_kill_seconds)
            return self._finish_capture(
                process=process,
                capture=capture,
                threads=threads,
            )
        except BaseException as exc:
            try:
                self._cleanup_failed_invoke(
                    process=process,
                    cgroup_path=cgroup_path,
                    bounded_kill_seconds=bounded_kill_seconds,
                )
            except SandboxUnavailable as cleanup_error:
                raise cleanup_error from exc
            raise

    def _spawn(self, mode: str) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            [str(self._launcher_path), mode],
            cwd=self._runner_root,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            close_fds=True,
            start_new_session=True,
        )

    @staticmethod
    def _start_capture(
        process: subprocess.Popen[bytes],
    ) -> tuple[_BoundedCapture, tuple[threading.Thread, threading.Thread]]:
        if process.stdout is None or process.stderr is None:
            raise SandboxUnavailable("sandbox_launcher_pipe_unavailable")
        capture = _BoundedCapture(
            stdout_limit=_MAX_LAUNCHER_RECEIPT_BYTES,
            stderr_limit=_MAX_LAUNCHER_ERROR_BYTES,
        )
        stdout_thread = threading.Thread(
            target=capture.read,
            args=("stdout", process.stdout),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=capture.read,
            args=("stderr", process.stderr),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        return capture, (stdout_thread, stderr_thread)

    def _send_and_wait(
        self,
        *,
        process: subprocess.Popen[bytes],
        payload: bytes,
        capture: _BoundedCapture,
        cgroup_path: Path,
        timeout_seconds: int,
        bounded_kill_seconds: int,
    ) -> None:
        if process.stdin is None:
            raise SandboxUnavailable("sandbox_launcher_pipe_unavailable")
        deadline = time.monotonic() + timeout_seconds
        try:
            file_descriptor = process.stdin.fileno()
            os.set_blocking(file_descriptor, False)
            payload_view = memoryview(payload)
            written = 0
            while written < len(payload_view):
                if capture.overflow.is_set():
                    raise SandboxRejected("sandbox_launcher_output_limit_exceeded")
                if time.monotonic() >= deadline:
                    raise SandboxRejected("sandbox_launcher_deadline_exceeded")
                if process.poll() is not None:
                    raise SandboxRejected("sandbox_launcher_pipe_closed")
                try:
                    count = os.write(file_descriptor, payload_view[written:])
                except BlockingIOError:
                    time.sleep(0.01)
                    continue
                if count <= 0:
                    raise SandboxRejected("sandbox_launcher_pipe_closed")
                written += count
            process.stdin.close()
            while process.poll() is None:
                if capture.overflow.is_set():
                    raise SandboxRejected("sandbox_launcher_output_limit_exceeded")
                if time.monotonic() >= deadline:
                    raise SandboxRejected("sandbox_launcher_deadline_exceeded")
                time.sleep(0.01)
        except (BrokenPipeError, OSError) as exc:
            raise SandboxRejected("sandbox_launcher_deadline_exceeded") from exc

    def _finish_capture(
        self,
        *,
        process: subprocess.Popen[bytes],
        capture: _BoundedCapture,
        threads: tuple[threading.Thread, threading.Thread],
    ) -> bytes:
        if any(thread.is_alive() for thread in threads):
            raise SandboxRejected("sandbox_launcher_pipe_drain_failed")
        if capture.overflow.is_set():
            raise SandboxRejected("sandbox_launcher_output_limit_exceeded")
        if process.returncode != 0:
            raise SandboxRejected("sandbox_launcher_failed")
        return capture.value("stdout")

    @staticmethod
    def _cleanup_failed_invoke(
        *,
        process: subprocess.Popen[bytes] | None,
        cgroup_path: Path,
        bounded_kill_seconds: int,
    ) -> None:
        if process is None:
            if not SubprocessIsolationLauncher._kill_cgroup(
                cgroup_path,
                bounded_kill_seconds=bounded_kill_seconds,
            ):
                raise SandboxUnavailable("sandbox_cgroup_termination_unproven")
            return
        SubprocessIsolationLauncher._terminate_tree(
            process,
            cgroup_path=cgroup_path,
            bounded_kill_seconds=bounded_kill_seconds,
        )

    def _hash_launcher(self) -> str:
        info = self._launcher_path.lstat()
        if (
            not self._launcher_path.is_file()
            or self._launcher_path.is_symlink()
            or info.st_uid != self._launcher_owner_uid
            or info.st_mode & (0o020 | 0o002)
        ):
            raise ValueError("isolation launcher path is not trusted")
        digest = hashlib.sha256()
        with self._launcher_path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        value = digest.hexdigest()
        if value != self._expected_launcher_digest:
            raise ValueError("isolation launcher digest mismatch")
        return value

    def _hash_runner_root(self) -> str:
        info = self._runner_root.lstat()
        effective_uid = os.geteuid()
        if (
            not self._runner_root.is_dir()
            or self._runner_root.is_symlink()
            or info.st_uid != effective_uid
            or info.st_mode & (0o077)
        ):
            raise ValueError("runner root is not a private directory")
        return _digest(
            {
                "device": info.st_dev,
                "inode": info.st_ino,
                "mode": info.st_mode & 0o7777,
                "owner": info.st_uid,
            }
        )

    def _hash_cgroup_root(self) -> str:
        try:
            resolved = self._cgroup_root.resolve(strict=True)
        except OSError as exc:
            raise ValueError("delegated cgroup root is unavailable") from exc
        if (
            self._cgroup_root.is_symlink()
            or not resolved.is_relative_to(Path("/sys/fs/cgroup"))
            or not (resolved / "cgroup.kill").is_file()
            or not os.access(resolved, os.W_OK | os.X_OK)
        ):
            raise ValueError("delegated cgroup root is not trusted")
        self._cgroup_root = resolved
        info = resolved.lstat()
        return _digest(
            {
                "device": info.st_dev,
                "inode": info.st_ino,
                "mode": info.st_mode & 0o7777,
                "owner": info.st_uid,
            }
        )

    @staticmethod
    def _cgroup_name(payload: bytes) -> UUID:
        try:
            value = json.loads(payload)
            operation_id = UUID(value["operation_id"])
            cgroup_name = UUID(value["cgroup_name"])
        except (AttributeError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SandboxRejected("sandbox_launcher_request_invalid") from exc
        if (
            not isinstance(value, dict)
            or value["operation_id"] != str(operation_id)
            or value["cgroup_name"] != str(cgroup_name)
        ):
            raise SandboxRejected("sandbox_launcher_request_invalid")
        return cgroup_name

    @staticmethod
    def _terminate_tree(
        process: subprocess.Popen[bytes],
        *,
        cgroup_path: Path,
        bounded_kill_seconds: int,
    ) -> None:
        cgroup_empty = SubprocessIsolationLauncher._kill_cgroup(
            cgroup_path,
            bounded_kill_seconds=bounded_kill_seconds,
        )
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=bounded_kill_seconds)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=bounded_kill_seconds)
                except (OSError, subprocess.TimeoutExpired) as exc:
                    raise SandboxUnavailable("sandbox_process_tree_termination_failed") from exc
        if not cgroup_empty:
            raise SandboxUnavailable("sandbox_cgroup_termination_unproven")

    @staticmethod
    def _kill_cgroup(cgroup_path: Path, *, bounded_kill_seconds: int) -> bool:
        kill_path = cgroup_path / "cgroup.kill"
        events_path = cgroup_path / "cgroup.events"
        try:
            cgroup_info = cgroup_path.lstat()
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            if cgroup_path.is_symlink() or kill_path.is_symlink() or events_path.is_symlink():
                return False
            if not stat.S_ISDIR(cgroup_info.st_mode):
                return False
            with kill_path.open("wb", buffering=0) as stream:
                stream.write(b"1\n")
        except OSError:
            return False
        deadline = time.monotonic() + bounded_kill_seconds
        while time.monotonic() < deadline:
            try:
                with events_path.open("rb") as stream:
                    events = stream.read(4097)
            except OSError:
                return False
            if len(events) > 4096:
                return False
            if b"populated 0" in events.splitlines():
                return True
            time.sleep(0.01)
        return False


class LinuxRuntimeDriver(Protocol):
    def execute(
        self,
        *,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
    ) -> RuntimeDriverReceipt: ...

    def terminate(
        self,
        *,
        plan: RunnerTerminationPlan,
        host: VerifiedRunnerHost,
    ) -> RuntimeDriverReceipt: ...


def _unavailable() -> NoReturn:
    raise SandboxUnavailable("sandbox_linux_runtime_driver_unavailable")


class UnavailableLinuxRuntimeDriver:
    def execute(
        self,
        *,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
    ) -> RuntimeDriverReceipt:
        del plan, host
        _unavailable()

    def terminate(
        self,
        *,
        plan: RunnerTerminationPlan,
        host: VerifiedRunnerHost,
    ) -> RuntimeDriverReceipt:
        del plan, host
        _unavailable()


class AttestedLinuxLocalRuntimeDriver:
    """Runnable local driver that remains locked behind fresh host evidence."""

    def __init__(
        self,
        *,
        runner_id: UUID,
        attestation: LinuxRuntimeAttestation,
        launcher: IsolationLauncher,
        clock=utc_now,
    ) -> None:
        self._runner_id = runner_id
        self._attestation = attestation
        self._launcher = launcher
        self._clock = clock
        if (
            launcher.launcher_digest != attestation.launcher_digest
            or launcher.runner_root_digest != attestation.runner_root_digest
        ):
            raise ValueError("runtime launcher does not match host attestation")

    def execute(
        self,
        *,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
    ) -> RuntimeDriverReceipt:
        now = self._clock()
        self._verify_host(plan=plan, host=host, now=now)
        binding_digest = execution_binding_digest(plan, host)
        payload = {
            "binding_digest": binding_digest,
            "cgroup_name": str(plan.request.runtime_instance_id),
            "command": {
                "argv": list(plan.command.argv),
                "cwd": plan.command.cwd.value,
                "max_output_bytes": min(
                    plan.command.max_output_bytes,
                    plan.runtime_spec.limits.output_bytes,
                ),
                "timeout_seconds": min(
                    plan.command.timeout_seconds,
                    plan.runtime_spec.limits.wall_time_seconds,
                ),
            },
            "isolation_attestation": self._attestation.evidence_digest,
            "operation_id": str(plan.request.operation_id),
            "runner_id": str(host.runner_id),
            "runtime_handle": str(plan.runtime_handle.value),
            "runtime_instance_id": str(plan.request.runtime_instance_id),
            "runtime_spec": _runtime_spec_value(plan),
            "schema_version": 1,
        }
        response = self._launcher.invoke(
            mode="execute",
            payload=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            timeout_seconds=min(
                plan.command.timeout_seconds,
                plan.runtime_spec.limits.wall_time_seconds,
            ),
            bounded_kill_seconds=plan.isolation_profile.bounded_kill_seconds,
        )
        return self._parse_receipt(
            response,
            operation_id=plan.request.operation_id,
            runtime_instance_id=plan.request.runtime_instance_id,
            binding_digest=binding_digest,
        )

    def terminate(
        self,
        *,
        plan: RunnerTerminationPlan,
        host: VerifiedRunnerHost,
    ) -> RuntimeDriverReceipt:
        now = self._clock()
        request = plan.authorization.request
        self._attestation.verify(
            runner_id=host.runner_id,
            isolation_profile=plan.isolation_profile,
            now=now,
        )
        if (
            host.runner_id != self._runner_id
            or host.node_id != request.node_id
            or host.node_fencing_token != request.node_fencing_token
            or host.isolation_profile_digest != plan.isolation_profile.digest()
            or host.verified_at > now
            or host.expires_at <= now
            or request.deadline_at <= now
        ):
            raise SandboxRejected("sandbox_runner_host_attestation_rejected")
        binding_digest = termination_binding_digest(plan, host)
        remaining = max(1, int((request.deadline_at - now).total_seconds()))
        payload = {
            "action": request.action.value,
            "binding_digest": binding_digest,
            "cgroup_name": str(request.runtime_instance_id),
            "isolation_attestation": self._attestation.evidence_digest,
            "operation_id": str(request.operation_id),
            "reason_code": request.reason_code,
            "runner_id": str(host.runner_id),
            "runtime_handle": str(request.runtime_handle.value),
            "runtime_instance_id": str(request.runtime_instance_id),
            "schema_version": 1,
        }
        response = self._launcher.invoke(
            mode="terminate",
            payload=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            timeout_seconds=min(remaining, plan.isolation_profile.bounded_kill_seconds),
            bounded_kill_seconds=plan.isolation_profile.bounded_kill_seconds,
        )
        return self._parse_receipt(
            response,
            operation_id=request.operation_id,
            runtime_instance_id=request.runtime_instance_id,
            binding_digest=binding_digest,
        )

    def _verify_host(
        self,
        *,
        plan: RunnerExecutionPlan,
        host: VerifiedRunnerHost,
        now: datetime,
    ) -> None:
        self._attestation.verify(
            runner_id=host.runner_id,
            isolation_profile=plan.isolation_profile,
            now=now,
        )
        host.verify_binding(
            request=plan.request,
            isolation_profile=plan.isolation_profile,
            now=now,
        )
        if host.runner_id != self._runner_id:
            raise SandboxRejected("sandbox_runner_identity_rejected")

    def _parse_receipt(
        self,
        response: bytes,
        *,
        operation_id: UUID,
        runtime_instance_id: UUID,
        binding_digest: str,
    ) -> RuntimeDriverReceipt:
        try:
            value = json.loads(response)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SandboxRejected("sandbox_launcher_receipt_invalid") from exc
        expected_keys = {
            "binding_digest",
            "cgroup_empty",
            "evidence_digest",
            "exit_code",
            "operation_id",
            "reason_code",
            "runner_id",
            "runtime_instance_id",
            "namespace_evidence_digest",
            "namespaces_isolated",
            "stderr_digest",
            "stdout_digest",
            "truncated",
        }
        if not isinstance(value, dict) or set(value) != expected_keys:
            raise SandboxRejected("sandbox_launcher_receipt_invalid")
        try:
            receipt = RuntimeDriverReceipt(
                operation_id=UUID(value["operation_id"]),
                runtime_instance_id=UUID(value["runtime_instance_id"]),
                runner_id=UUID(value["runner_id"]),
                binding_digest=value["binding_digest"],
                evidence_digest=value["evidence_digest"],
                reason_code=value["reason_code"],
                exit_code=value["exit_code"],
                stdout_digest=value["stdout_digest"],
                stderr_digest=value["stderr_digest"],
                namespace_evidence_digest=value["namespace_evidence_digest"],
                namespaces_isolated=value["namespaces_isolated"],
                truncated=value["truncated"],
                cgroup_empty=value["cgroup_empty"],
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise SandboxRejected("sandbox_launcher_receipt_invalid") from exc
        if (
            receipt.operation_id != operation_id
            or receipt.runtime_instance_id != runtime_instance_id
            or receipt.runner_id != self._runner_id
            or receipt.binding_digest != binding_digest
        ):
            raise SandboxRejected("sandbox_launcher_receipt_binding_rejected")
        return receipt


__all__ = [
    "AttestedLinuxLocalRuntimeDriver",
    "IsolationLauncher",
    "LinuxRuntimeDriver",
    "RuntimeDriverReceipt",
    "SubprocessIsolationLauncher",
    "UnavailableLinuxRuntimeDriver",
    "execution_binding_digest",
    "termination_binding_digest",
]
