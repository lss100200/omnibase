#!/usr/bin/python3
"""Root-owned P34.5 Linux isolation launcher and non-root Runner daemon.

The trusted RuntimeDriver invokes ``execute`` or ``terminate``.  Those modes
are small Unix-socket clients.  Only the systemd-confined ``serve`` mode may
create workloads.  ``isolate`` and ``enter`` are private re-exec stages used to
enter the delegated cgroup before any namespace child is forked.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import UUID

LAUNCHER = Path("/usr/libexec/omnibase/omnibase-isolation-launcher")
SOCKET_PATH = Path("/run/omnibase-runner/control.sock")
RUNNER_ROOT = Path("/var/lib/omnibase-runner")
CGROUP_ROOT = Path("/sys/fs/cgroup/system.slice/omnibase-runner.service")
SECCOMP_PATH = Path("/etc/omnibase-runner/seccomp.json")
APPARMOR_PATH = Path("/etc/apparmor.d/omnibase-runner")
MAX_REQUEST = 1024 * 1024
MAX_RECEIPT = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

libc = ctypes.CDLL(None, use_errno=True)

MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_REMOUNT = 32
MS_BIND = 4096
MNT_DETACH = 2
PR_SET_NO_NEW_PRIVS = 38
PR_GET_NO_NEW_PRIVS = 39
PR_SET_SECCOMP = 22
PR_GET_SECCOMP = 21
PR_CAPBSET_DROP = 24
SECCOMP_MODE_FILTER = 2
SECCOMP_RET_KILL_PROCESS = 0x80000000
SECCOMP_RET_ERRNO = 0x00050000
SECCOMP_RET_ALLOW = 0x7FFF0000
AUDIT_ARCH_X86_64 = 0xC000003E

BPF_LD_W_ABS = 0x20
BPF_JMP_JEQ_K = 0x15
BPF_RET_K = 0x06

DENIED_SYSCALLS = {
    101,  # ptrace
    155,  # pivot_root
    165,  # mount
    166,  # umount2
    167,  # swapon
    168,  # swapoff
    169,  # reboot
    175,  # init_module
    176,  # delete_module
    246,  # kexec_load
    248,  # add_key
    249,  # request_key
    250,  # keyctl
    272,  # unshare
    298,  # perf_event_open
    304,  # open_by_handle_at
    308,  # setns
    313,  # finit_module
    320,  # kexec_file_load
    321,  # bpf
    323,  # userfaultfd
    425,  # io_uring_setup
    428,  # open_tree
    429,  # move_mount
}


class SockFilter(ctypes.Structure):
    _fields_ = [
        ("code", ctypes.c_ushort),
        ("jt", ctypes.c_ubyte),
        ("jf", ctypes.c_ubyte),
        ("k", ctypes.c_uint32),
    ]


class SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(SockFilter))]


class CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class CapData(ctypes.Structure):
    _fields_ = [
        ("effective", ctypes.c_uint32),
        ("permitted", ctypes.c_uint32),
        ("inheritable", ctypes.c_uint32),
    ]


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_digest(value: object) -> str:
    return _sha256_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _read_json_stream(stream, *, limit: int = MAX_REQUEST) -> dict[str, Any]:
    value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("request_too_large")
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("request_shape_invalid")
    return parsed


def _canonical_uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("uuid_invalid")
    parsed = UUID(value)
    if str(parsed) != value:
        raise ValueError("uuid_not_canonical")
    return value


def _status_fields() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, separator, value = line.partition(":")
        if separator:
            result[key] = value.strip()
    return result


def _namespace_evidence() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("user", "pid", "mnt", "net"):
        info = os.stat(f"/proc/self/ns/{name}")
        result[name] = f"{info.st_dev}:{info.st_ino}"
    return result


def _mount(source: str | None, target: Path, fs_type: str | None, flags: int, data: str) -> None:
    source_value = None if source is None else source.encode()
    type_value = None if fs_type is None else fs_type.encode()
    data_value = None if not data else data.encode()
    if libc.mount(source_value, os.fsencode(target), type_value, flags, data_value) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(target))


def _umount(target: Path) -> None:
    if libc.umount2(os.fsencode(target), MNT_DETACH) != 0:
        error = ctypes.get_errno()
        if error not in {errno.EINVAL, errno.ENOENT}:
            raise OSError(error, os.strerror(error), str(target))


def _prctl(option: int, argument: int) -> None:
    if libc.prctl(option, argument, 0, 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _install_seccomp() -> None:
    instructions: list[SockFilter] = [
        SockFilter(BPF_LD_W_ABS, 0, 0, 4),
        SockFilter(BPF_JMP_JEQ_K, 1, 0, AUDIT_ARCH_X86_64),
        SockFilter(BPF_RET_K, 0, 0, SECCOMP_RET_KILL_PROCESS),
        SockFilter(BPF_LD_W_ABS, 0, 0, 0),
    ]
    for syscall_number in sorted(DENIED_SYSCALLS):
        instructions.append(SockFilter(BPF_JMP_JEQ_K, 0, 1, syscall_number))
        instructions.append(SockFilter(BPF_RET_K, 0, 0, SECCOMP_RET_ERRNO | errno.EPERM))
    instructions.append(SockFilter(BPF_RET_K, 0, 0, SECCOMP_RET_ALLOW))
    array_type = SockFilter * len(instructions)
    instruction_array = array_type(*instructions)
    program = SockFprog(len(instructions), instruction_array)
    _prctl(PR_SET_NO_NEW_PRIVS, 1)
    if libc.prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(program), 0, 0) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _drop_capabilities() -> None:
    for capability in range(64):
        libc.prctl(PR_CAPBSET_DROP, capability, 0, 0, 0)
    header = CapHeader(0x20080522, 0)
    data_type = CapData * 2
    data = data_type(CapData(0, 0, 0), CapData(0, 0, 0))
    if libc.capset(ctypes.byref(header), ctypes.byref(data)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def _capability_effective_hex() -> str:
    header = CapHeader(0x20080522, 0)
    data_type = CapData * 2
    data = data_type(CapData(), CapData())
    if libc.capget(ctypes.byref(header), ctypes.byref(data)) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    value = (int(data[1].effective) << 32) | int(data[0].effective)
    return f"{value:016x}"


def _validate_payload(  # noqa: C901 - one closed protocol validator is auditable.
    payload: dict[str, Any], *, terminate: bool = False
) -> None:
    common = {
        "binding_digest",
        "cgroup_name",
        "isolation_attestation",
        "operation_id",
        "runner_id",
        "runtime_handle",
        "runtime_instance_id",
        "schema_version",
    }
    expected = common | ({"action", "reason_code"} if terminate else {"command", "runtime_spec"})
    if set(payload) != expected or payload.get("schema_version") != 1:
        raise ValueError("payload_shape_invalid")
    for field in ("operation_id", "runner_id", "runtime_handle", "runtime_instance_id"):
        _canonical_uuid(payload[field])
    if _canonical_uuid(payload["cgroup_name"]) != payload["runtime_instance_id"]:
        raise ValueError("cgroup_runtime_binding_invalid")
    for field in ("binding_digest", "isolation_attestation"):
        if not isinstance(payload[field], str) or not SHA256_RE.fullmatch(payload[field]):
            raise ValueError("digest_invalid")
    if terminate:
        if payload.get("action") not in {"stop", "destroy"}:
            raise ValueError("termination_action_invalid")
        if not isinstance(payload.get("reason_code"), str):
            raise ValueError("termination_reason_invalid")
        return
    command = payload.get("command")
    runtime_spec = payload.get("runtime_spec")
    if not isinstance(command, dict) or set(command) != {
        "argv",
        "cwd",
        "max_output_bytes",
        "timeout_seconds",
    }:
        raise ValueError("command_shape_invalid")
    argv = command.get("argv")
    if (
        not isinstance(argv, list)
        or not 1 <= len(argv) <= 64
        or any(not isinstance(item, str) or not item or "\x00" in item for item in argv)
    ):
        raise ValueError("argv_invalid")
    if command.get("cwd") != "workspace":
        raise ValueError("cwd_invalid")
    if not isinstance(runtime_spec, dict) or set(runtime_spec) != {
        "isolation",
        "limits",
        "network",
        "policy_digest",
        "template_digest",
    }:
        raise ValueError("runtime_spec_shape_invalid")
    isolation = runtime_spec.get("isolation")
    if not isinstance(isolation, dict) or isolation != {
        "allow_devices": False,
        "allow_host_mounts": False,
        "allow_runtime_socket": False,
        "drop_all_capabilities": True,
        "no_new_privileges": True,
        "read_only_root": True,
        "run_as_gid": isolation.get("run_as_gid") if isinstance(isolation, dict) else None,
        "run_as_uid": isolation.get("run_as_uid") if isinstance(isolation, dict) else None,
    }:
        raise ValueError("isolation_policy_invalid")
    if (
        type(isolation.get("run_as_uid")) is not int
        or not 10_000 <= isolation["run_as_uid"] <= 2**31 - 1
    ):
        raise ValueError("run_uid_invalid")
    if (
        type(isolation.get("run_as_gid")) is not int
        or not 10_000 <= isolation["run_as_gid"] <= 2**31 - 1
    ):
        raise ValueError("run_gid_invalid")
    network = runtime_spec.get("network")
    if network != {
        "allowed_service_ids": [],
        "direct_overlay": False,
        "mode": "deny_all",
    }:
        raise ValueError("network_policy_invalid")
    limits = runtime_spec.get("limits")
    required_limits = {
        "cpu_millis",
        "inodes",
        "memory_bytes",
        "output_bytes",
        "pids",
        "wall_time_seconds",
        "writable_bytes",
    }
    if not isinstance(limits, dict) or set(limits) != required_limits:
        raise ValueError("limits_shape_invalid")
    if any(not isinstance(value, int) or value <= 0 for value in limits.values()):
        raise ValueError("limits_invalid")
    if limits["memory_bytes"] > 512 * 1024 * 1024 or limits["pids"] > 128:
        raise ValueError("limits_exceed_runner_policy")
    if limits["writable_bytes"] > 128 * 1024 * 1024 or limits["inodes"] > 16384:
        raise ValueError("writable_limits_exceed_runner_policy")
    if limits["wall_time_seconds"] > 120 or limits["output_bytes"] > 1024 * 1024:
        raise ValueError("execution_limits_exceed_runner_policy")
    for field in ("policy_digest", "template_digest"):
        if not isinstance(runtime_spec[field], str) or not SHA256_RE.fullmatch(runtime_spec[field]):
            raise ValueError("runtime_digest_invalid")


def _write_cgroup_value(cgroup: Path, name: str, value: str) -> None:
    (cgroup / name).write_text(value)


def _prepare_cgroup(runtime_id: str, limits: dict[str, int]) -> Path:
    cgroup = CGROUP_ROOT / runtime_id
    if cgroup.exists():
        events = (cgroup / "cgroup.events").read_text()
        if "populated 1" in events:
            raise RuntimeError("runtime_cgroup_already_populated")
        cgroup.rmdir()
    cgroup.mkdir(mode=0o700)
    try:
        _write_cgroup_value(cgroup, "memory.max", str(limits["memory_bytes"]))
        _write_cgroup_value(cgroup, "memory.swap.max", "0")
        _write_cgroup_value(cgroup, "pids.max", str(limits["pids"]))
        quota = max(1000, limits["cpu_millis"] * 100)
        _write_cgroup_value(cgroup, "cpu.max", f"{quota} 100000")
    except BaseException as exc:
        if not _kill_and_prove_empty(cgroup):
            raise RuntimeError("runner_cgroup_termination_unproven") from exc
        try:
            cgroup.rmdir()
        except OSError as cleanup_error:
            raise RuntimeError("runner_cgroup_remove_failed") from cleanup_error
        raise
    return cgroup


def _kill_and_prove_empty(cgroup: Path, deadline_seconds: float = 10.0) -> bool:
    if not cgroup.exists():
        return True
    with suppress(OSError):
        (cgroup / "cgroup.kill").write_text("1")
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        try:
            events = (cgroup / "cgroup.events").read_text()
        except OSError:
            return False
        if "populated 0" in events:
            return True
        time.sleep(0.05)
    return False


def _receipt(
    payload: dict[str, Any],
    *,
    reason: str,
    exit_code: int,
    truncated: bool,
    stdout: bytes,
    stderr: bytes,
    namespace_digest: str,
    cgroup_empty: bool,
) -> dict[str, Any]:
    evidence = {
        "binding_digest": payload["binding_digest"],
        "cgroup_empty": cgroup_empty,
        "exit_code": exit_code,
        "namespace_evidence_digest": namespace_digest,
        "operation_id": payload["operation_id"],
        "reason_code": reason,
        "runner_id": payload["runner_id"],
        "runtime_instance_id": payload["runtime_instance_id"],
        "stderr_digest": _sha256_bytes(stderr),
        "stdout_digest": _sha256_bytes(stdout),
        "truncated": truncated,
    }
    return {
        "binding_digest": payload["binding_digest"],
        "cgroup_empty": cgroup_empty,
        "evidence_digest": _canonical_digest(evidence),
        "exit_code": exit_code,
        "namespace_evidence_digest": namespace_digest,
        "namespaces_isolated": bool(namespace_digest and cgroup_empty),
        "operation_id": payload["operation_id"],
        "reason_code": reason,
        "runner_id": payload["runner_id"],
        "runtime_instance_id": payload["runtime_instance_id"],
        "stderr_digest": _sha256_bytes(stderr),
        "stdout_digest": _sha256_bytes(stdout),
        "truncated": truncated,
    }


def _bounded_communicate(  # noqa: C901 - bounded pipe state is kept together.
    process: subprocess.Popen[bytes], *, timeout: int, output_limit: int, cgroup: Path
) -> tuple[bytes, bytes, bool, bool]:
    selector = selectors.DefaultSelector()
    try:
        streams = {process.stdout: "stdout", process.stderr: "stderr"}
        for stream in streams:
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
        output = {"stdout": bytearray(), "stderr": bytearray()}
        deadline = time.monotonic() + timeout
        timed_out = False
        overflow = False
        while selector.get_map() or process.poll() is None:
            if time.monotonic() >= deadline:
                timed_out = True
                _kill_and_prove_empty(cgroup)
                break
            for key, _ in selector.select(timeout=0.05):
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    continue
                target = output[streams[stream]]
                remaining = output_limit - len(output["stdout"]) - len(output["stderr"])
                if remaining <= 0 or len(chunk) > remaining:
                    target.extend(chunk[: max(0, remaining)])
                    overflow = True
                    _kill_and_prove_empty(cgroup)
                    break
                target.extend(chunk)
            if overflow:
                break
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            _kill_and_prove_empty(cgroup)
            process.kill()
            process.wait(timeout=5)
        for stream, name in streams.items():
            if stream is None:
                continue
            try:
                while chunk := os.read(stream.fileno(), 65536):
                    remaining = output_limit - len(output["stdout"]) - len(output["stderr"])
                    output[name].extend(chunk[: max(0, remaining)])
                    if len(chunk) > remaining:
                        overflow = True
            except (BlockingIOError, OSError):
                pass
        return bytes(output["stdout"]), bytes(output["stderr"]), timed_out, overflow
    finally:
        selector.close()


def _cleanup_execution(
    *,
    cgroup: Path,
    process: subprocess.Popen[bytes] | None,
    runtime_dir: Path | None,
) -> None:
    if not _kill_and_prove_empty(cgroup):
        raise RuntimeError("runner_cgroup_termination_unproven")
    _terminate_process_group(process)
    if cgroup.exists():
        try:
            cgroup.rmdir()
        except OSError as exc:
            raise RuntimeError("runner_cgroup_remove_failed") from exc
    if runtime_dir is not None:
        shutil.rmtree(runtime_dir)


def _terminate_process_group(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
        return
    except (OSError, subprocess.TimeoutExpired):
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("runner_launcher_process_group_termination_failed") from exc


def _parse_id_map(value: str, *, field: str) -> list[dict[str, int]]:
    mappings: list[dict[str, int]] = []
    for line in value.splitlines():
        parts = line.split()
        if len(parts) != 3:
            raise RuntimeError(f"{field}_shape_invalid")
        try:
            inside_id, outside_id, length = (int(part) for part in parts)
        except ValueError as exc:
            raise RuntimeError(f"{field}_shape_invalid") from exc
        if inside_id < 0 or outside_id < 0 or length <= 0:
            raise RuntimeError(f"{field}_range_invalid")
        mappings.append(
            {
                "inside_id": inside_id,
                "length": length,
                "outside_id": outside_id,
            }
        )
    if not mappings:
        raise RuntimeError(f"{field}_empty")
    return mappings


def _enforce_workload_identity(
    isolation: dict[str, Any],
    *,
    uid_map_text: str,
    gid_map_text: str,
    setgroups_mode: str,
) -> dict[str, Any]:
    requested_uid = isolation["run_as_uid"]
    requested_gid = isolation["run_as_gid"]
    uid_map = _parse_id_map(uid_map_text, field="uid_map")
    gid_map = _parse_id_map(gid_map_text, field="gid_map")
    expected_uid_map = [
        {"inside_id": requested_uid, "length": 1, "outside_id": uid_map[0]["outside_id"]}
    ]
    expected_gid_map = [
        {"inside_id": requested_gid, "length": 1, "outside_id": gid_map[0]["outside_id"]}
    ]
    if uid_map != expected_uid_map or uid_map[0]["outside_id"] == 0:
        raise RuntimeError("workload_uid_map_invalid")
    if gid_map != expected_gid_map or gid_map[0]["outside_id"] == 0:
        raise RuntimeError("workload_gid_map_invalid")
    if setgroups_mode != "deny":
        raise RuntimeError("workload_setgroups_not_denied")
    if os.getgroups():
        raise RuntimeError("workload_supplementary_groups_not_empty")

    # The requested IDs are already the current IDs after unshare installs the
    # one-entry maps.  Explicitly normalize real/effective/saved identities so
    # no inherited saved ID can regain the Runner service identity.
    os.setresgid(requested_gid, requested_gid, requested_gid)
    os.setresuid(requested_uid, requested_uid, requested_uid)
    identity = {
        "egid": os.getegid(),
        "euid": os.geteuid(),
        "gid": os.getgid(),
        "gid_map": gid_map,
        "gid_map_digest": _sha256_bytes(gid_map_text.encode()),
        "setgroups_mode": setgroups_mode,
        "supplementary_groups": os.getgroups(),
        "uid": os.getuid(),
        "uid_map": uid_map,
        "uid_map_digest": _sha256_bytes(uid_map_text.encode()),
    }
    if (
        identity["uid"] != requested_uid
        or identity["euid"] != requested_uid
        or identity["gid"] != requested_gid
        or identity["egid"] != requested_gid
        or identity["supplementary_groups"]
    ):
        raise RuntimeError("workload_identity_transition_failed")
    return identity


def _execute(payload: dict[str, Any]) -> dict[str, Any]:
    _validate_payload(payload)
    runtime_id = payload["runtime_instance_id"]
    operation_id = payload["operation_id"]
    limits = payload["runtime_spec"]["limits"]
    command = payload["command"]
    cgroup: Path | None = None
    runtime_dir = RUNNER_ROOT / "runtimes" / runtime_id
    runtime_dir_created = False
    process: subprocess.Popen[bytes] | None = None
    try:
        cgroup = _prepare_cgroup(runtime_id, limits)
        runtime_dir.mkdir(mode=0o700, exist_ok=False)
        runtime_dir_created = True
        payload_path = runtime_dir / f"{operation_id}.request.json"
        meta_path = runtime_dir / f"{operation_id}.namespace.json"
        root_path = runtime_dir / "root"
        root_path.mkdir(mode=0o700)
        payload_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        payload_path.chmod(0o600)
        argv = [
            str(LAUNCHER),
            "enter",
            str(cgroup),
            str(payload_path),
            str(meta_path),
            str(root_path),
        ]
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=RUNNER_ROOT,
            env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
            close_fds=True,
            start_new_session=True,
        )
        stdout, stderr, timed_out, overflow = _bounded_communicate(
            process,
            timeout=min(command["timeout_seconds"], limits["wall_time_seconds"]),
            output_limit=min(command["max_output_bytes"], limits["output_bytes"]),
            cgroup=cgroup,
        )
        metadata: dict[str, Any] = {}
        if meta_path.exists():
            try:
                metadata = json.loads(meta_path.read_text())
            except (OSError, json.JSONDecodeError):
                metadata = {}
        namespace_digest = _canonical_digest(metadata) if metadata else "0" * 64
        if timed_out:
            reason = "runner_execution_timed_out"
            exit_code = 124
        elif overflow:
            reason = "runner_output_limit_exceeded"
            exit_code = 125
        elif process.returncode == 0:
            reason = "runner_execution_succeeded"
            exit_code = 0
        else:
            reason = "runner_execution_failed"
            exit_code = process.returncode if isinstance(process.returncode, int) else 126
        if not metadata:
            reason = "runner_isolation_evidence_failed"
            exit_code = 126
        evidence_record = {
            "cgroup_cpu_stat": (cgroup / "cpu.stat").read_text().splitlines(),
            "cgroup_memory_events": (cgroup / "memory.events").read_text().splitlines(),
            "cgroup_pids_events": (cgroup / "pids.events").read_text().splitlines(),
            "cgroup_empty": True,
            "command_digest": _canonical_digest(command),
            "exit_code": exit_code,
            "metadata": metadata,
            "operation_id": operation_id,
            "reason_code": reason,
            "runtime_instance_id": runtime_id,
            "stderr_bytes": len(stderr),
            "stderr_digest": _sha256_bytes(stderr),
            "stdout_bytes": len(stdout),
            "stdout_digest": _sha256_bytes(stdout),
            "truncated": overflow,
        }
        receipt = _receipt(
            payload,
            reason=reason,
            exit_code=exit_code,
            truncated=overflow,
            stdout=stdout,
            stderr=stderr,
            namespace_digest=namespace_digest,
            cgroup_empty=True,
        )
    except BaseException as exc:
        if cgroup is None:
            raise
        try:
            _cleanup_execution(
                cgroup=cgroup,
                process=process,
                runtime_dir=runtime_dir if runtime_dir_created else None,
            )
        except RuntimeError as cleanup_error:
            raise cleanup_error from exc
        raise
    assert cgroup is not None
    _cleanup_execution(
        cgroup=cgroup,
        process=process,
        runtime_dir=runtime_dir if runtime_dir_created else None,
    )
    evidence_path = RUNNER_ROOT / "evidence" / f"{operation_id}.json"
    evidence_path.write_text(json.dumps(evidence_record, indent=2, sort_keys=True) + "\n")
    evidence_path.chmod(0o600)
    return receipt


def _terminate(payload: dict[str, Any]) -> dict[str, Any]:
    _validate_payload(payload, terminate=True)
    cgroup = CGROUP_ROOT / payload["runtime_instance_id"]
    cgroup_empty = _kill_and_prove_empty(cgroup)
    if cgroup_empty and cgroup.exists():
        with suppress(OSError):
            cgroup.rmdir()
    return _receipt(
        payload,
        reason="runner_termination_succeeded" if cgroup_empty else "runner_termination_unproven",
        exit_code=0 if cgroup_empty else 126,
        truncated=False,
        stdout=b"",
        stderr=b"",
        namespace_digest=_canonical_digest({"terminated_runtime": payload["runtime_instance_id"]}),
        cgroup_empty=cgroup_empty,
    )


def _probe(  # noqa: C901 - a single evidence collector avoids partial attestation.
    config: dict[str, Any],
) -> dict[str, Any]:
    required = {
        "cgroup_root",
        "expected_launcher_digest",
        "host_namespace_root",
        "launcher_path",
        "lsm_profile_digest",
        "lsm_profile_name",
        "lsm_profile_path",
        "runner_id",
        "runner_root",
        "seccomp_profile_digest",
        "seccomp_profile_path",
    }
    if set(config) != required:
        raise ValueError("probe_config_shape_invalid")
    _canonical_uuid(config["runner_id"])
    current_namespaces = _namespace_evidence()
    host_root = Path(config["host_namespace_root"])
    host_namespaces = {}
    for name in ("user", "pid", "mnt", "net"):
        reference = host_root / name
        if reference.is_file() and not reference.is_symlink():
            host_namespaces[name] = reference.read_text().strip()
        else:
            info = os.stat(reference)
            host_namespaces[name] = f"{info.st_dev}:{info.st_ino}"
    status = _status_fields()
    apparmor = Path("/proc/self/attr/current").read_text().strip()
    launcher = Path(config["launcher_path"])
    runner_root = Path(config["runner_root"])
    cgroup_root = Path(config["cgroup_root"])
    missing: list[str] = []
    if os.geteuid() == 0:
        missing.append("non_root_service")
    for name in current_namespaces:
        if current_namespaces[name] == host_namespaces[name]:
            missing.append(f"isolated_{name}_namespace")
    if status.get("NoNewPrivs") != "1":
        missing.append("no_new_privileges")
    if status.get("Seccomp") != "2":
        missing.append("seccomp_filter")
    if apparmor != f"{config['lsm_profile_name']} (enforce)":
        missing.append("apparmor_enforce")
    launcher_stat = launcher.stat()
    if launcher_stat.st_uid != 0 or launcher_stat.st_mode & 0o022:
        missing.append("root_owned_launcher")
    if _sha256_file(launcher) != config["expected_launcher_digest"]:
        missing.append("launcher_digest")
    if _sha256_file(Path(config["seccomp_profile_path"])) != config["seccomp_profile_digest"]:
        missing.append("seccomp_digest")
    if _sha256_file(Path(config["lsm_profile_path"])) != config["lsm_profile_digest"]:
        missing.append("lsm_digest")
    root_stat = runner_root.stat()
    if root_stat.st_uid != os.geteuid() or stat.S_IMODE(root_stat.st_mode) != 0o700:
        missing.append("private_runner_root")
    if not os.access(cgroup_root, os.W_OK | os.X_OK):
        missing.append("delegated_cgroup")
    controllers = set((cgroup_root / "cgroup.controllers").read_text().split())
    if not {"cpu", "memory", "pids"}.issubset(controllers):
        missing.append("cgroup_controllers")
    evidence = {
        "apparmor": apparmor,
        "cgroup_controllers": sorted(controllers),
        "cgroup_root": str(cgroup_root),
        "host_namespaces": host_namespaces,
        "launcher_digest": _sha256_file(launcher),
        "no_new_privileges": status.get("NoNewPrivs"),
        "runner_id": config["runner_id"],
        "runner_namespaces": current_namespaces,
        "seccomp_mode": status.get("Seccomp"),
        "service_uid": os.geteuid(),
    }
    return {
        "evidence": evidence,
        "evidence_digest": _canonical_digest(evidence),
        "missing_controls": sorted(missing),
        "ready": not missing,
    }


def _handle_request(request: dict[str, Any]) -> dict[str, Any]:
    if set(request) != {"mode", "payload"} or not isinstance(request["payload"], dict):
        raise ValueError("runner_request_shape_invalid")
    mode = request["mode"]
    if mode == "execute":
        return _execute(request["payload"])
    if mode == "terminate":
        return _terminate(request["payload"])
    if mode == "probe":
        return _probe(request["payload"])
    raise ValueError("runner_mode_invalid")


def _serve_connection(connection: socket.socket) -> None:
    try:
        chunks = bytearray()
        while len(chunks) <= MAX_REQUEST:
            chunk = connection.recv(65536)
            if not chunk:
                break
            chunks.extend(chunk)
        if len(chunks) > MAX_REQUEST:
            raise ValueError("request_too_large")
        request = json.loads(chunks)
        if not isinstance(request, dict):
            raise ValueError("request_shape_invalid")
        response = {"ok": True, "result": _handle_request(request)}
    except Exception as exc:  # Fail closed at the daemon boundary.
        response = {"error": type(exc).__name__, "ok": False}
    encoded = json.dumps(response, sort_keys=True, separators=(",", ":")).encode()
    if len(encoded) > MAX_RECEIPT:
        encoded = b'{"error":"response_too_large","ok":false}'
    try:
        connection.sendall(encoded)
    finally:
        connection.close()


def _serve() -> int:
    if os.geteuid() == 0:
        raise RuntimeError("runner_daemon_must_be_non_root")
    (CGROUP_ROOT / "cgroup.subtree_control").write_text("+cpu +memory +pids")
    for child in CGROUP_ROOT.iterdir():
        if not child.is_dir() or child.name == "supervisor":
            continue
        try:
            UUID(child.name)
            if "populated 0" in (child / "cgroup.events").read_text():
                child.rmdir()
        except (OSError, ValueError):
            continue
    SOCKET_PATH.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if SOCKET_PATH.exists() or SOCKET_PATH.is_symlink():
        SOCKET_PATH.unlink()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(SOCKET_PATH))
    SOCKET_PATH.chmod(0o600)
    server.listen(16)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        connection, _ = server.accept()
        threading.Thread(target=_serve_connection, args=(connection,), daemon=True).start()


def _prepare_host_namespace_references() -> int:
    if os.geteuid() != 0:
        raise RuntimeError("host_namespace_prepare_requires_root")
    reference_root = Path("/run/omnibase-host-ns")
    reference_root.mkdir(mode=0o755, parents=True, exist_ok=True)
    reference_root.chmod(0o755)
    for name in ("user", "pid", "mnt", "net"):
        target = reference_root / name
        if target.exists() or target.is_symlink():
            target.unlink()
        info = os.stat(f"/proc/1/ns/{name}")
        target.write_text(f"{info.st_dev}:{info.st_ino}\n")
        target.chmod(0o444)
    reference_root.chmod(0o555)
    return 0


def _client(mode: str) -> int:
    try:
        payload = _read_json_stream(sys.stdin.buffer)
        request = json.dumps(
            {"mode": mode, "payload": payload}, sort_keys=True, separators=(",", ":")
        ).encode()
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client.settimeout(180)
        client.connect(str(SOCKET_PATH))
        client.sendall(request)
        client.shutdown(socket.SHUT_WR)
        chunks = bytearray()
        while len(chunks) <= MAX_RECEIPT:
            chunk = client.recv(65536)
            if not chunk:
                break
            chunks.extend(chunk)
        response = json.loads(chunks)
        if not isinstance(response, dict) or response.get("ok") is not True:
            print(
                json.dumps(
                    {"ready": False, "error": response.get("error", "runner_rejected")},
                    sort_keys=True,
                )
            )
            return 2
        print(json.dumps(response["result"], sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:
        print(json.dumps({"ready": False, "error": type(exc).__name__}, sort_keys=True))
        return 2


def _enter(arguments: list[str]) -> int:
    if len(arguments) != 4:
        raise ValueError("enter_arguments_invalid")
    cgroup = Path(arguments[0]).resolve(strict=True)
    if cgroup.parent != CGROUP_ROOT or not UUID(cgroup.name):
        raise ValueError("enter_cgroup_invalid")
    payload = json.loads(Path(arguments[1]).read_text())
    _validate_payload(payload)
    isolation = payload["runtime_spec"]["isolation"]
    # Clear the Runner service's supplementary groups while CAP_SETGID is
    # still available.  unshare then locks setgroups before installing the
    # single GID mapping, so the isolated workload cannot add them back.
    os.setgroups([])
    if os.getgroups():
        raise RuntimeError("runner_supplementary_groups_clear_failed")
    (cgroup / "cgroup.procs").write_text(str(os.getpid()))
    os.execve(  # noqa: S606 - fixed trusted executable; shell use is forbidden.
        "/usr/bin/unshare",
        [
            "unshare",
            "--user",
            f"--map-user={isolation['run_as_uid']}",
            f"--map-group={isolation['run_as_gid']}",
            "--pid",
            "--mount",
            "--net",
            "--ipc",
            "--uts",
            "--fork",
            str(LAUNCHER),
            "isolate",
            arguments[1],
            arguments[2],
            arguments[3],
        ],
        {"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8", "PATH": "/usr/bin:/bin"},
    )


def _isolate(arguments: list[str]) -> int:
    if len(arguments) != 3:
        raise ValueError("isolate_arguments_invalid")
    payload_path, meta_path, root_path = map(Path, arguments)
    payload = json.loads(payload_path.read_text())
    _validate_payload(payload)
    limits = payload["runtime_spec"]["limits"]
    metadata_fd = os.open(meta_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    stage = "prepare_root_directory"
    root_path.mkdir(mode=0o700, exist_ok=True)
    try:
        stage = "mount_ephemeral_root"
        _mount(
            "tmpfs",
            root_path,
            "tmpfs",
            MS_NOSUID | MS_NODEV,
            "mode=0755,size=16777216,nr_inodes=1024",
        )
        stage = "populate_minimal_root"
        for relative in ("bin", "proc", "workspace", "tmp", "dev", "etc"):
            (root_path / relative).mkdir(mode=0o755)
        shutil.copyfile("/usr/bin/busybox", root_path / "bin/busybox")
        (root_path / "bin/busybox").chmod(0o555)
        for applet in (
            "awk",
            "cat",
            "dd",
            "env",
            "find",
            "head",
            "id",
            "ln",
            "mkdir",
            "mknod",
            "nc",
            "nslookup",
            "ps",
            "readlink",
            "rm",
            "sh",
            "sleep",
            "stat",
            "touch",
            "true",
            "uname",
            "wc",
            "yes",
        ):
            os.symlink("busybox", root_path / f"bin/{applet}")
        writable_bytes = limits["writable_bytes"]
        workspace_bytes = max(1024 * 1024, writable_bytes * 3 // 4)
        tmp_bytes = max(1024 * 1024, writable_bytes - workspace_bytes)
        workspace_inodes = max(32, limits["inodes"] * 3 // 4)
        tmp_inodes = max(32, limits["inodes"] - workspace_inodes)
        stage = "mount_workspace_tmpfs"
        _mount(
            "tmpfs",
            root_path / "workspace",
            "tmpfs",
            MS_NOSUID | MS_NODEV | MS_NOEXEC,
            f"mode=0700,size={workspace_bytes},nr_inodes={workspace_inodes}",
        )
        stage = "mount_tmp_tmpfs"
        _mount(
            "tmpfs",
            root_path / "tmp",
            "tmpfs",
            MS_NOSUID | MS_NODEV | MS_NOEXEC,
            f"mode=0700,size={tmp_bytes},nr_inodes={tmp_inodes}",
        )
        stage = "remount_root_read_only"
        _mount(None, root_path, None, MS_REMOUNT | MS_RDONLY | MS_NOSUID | MS_NODEV, "")
        stage = "capture_namespace_evidence"
        uid_map = Path("/proc/self/uid_map").read_text().strip()
        gid_map = Path("/proc/self/gid_map").read_text().strip()
        setgroups_mode = Path("/proc/self/setgroups").read_text().strip()
        namespaces = _namespace_evidence()
        apparmor = Path("/proc/self/attr/current").read_text().strip()
        stage = "enter_ephemeral_root"
        os.chroot(root_path)
        os.chdir("/workspace")
        stage = "enforce_workload_identity"
        identity = _enforce_workload_identity(
            payload["runtime_spec"]["isolation"],
            uid_map_text=uid_map,
            gid_map_text=gid_map,
            setgroups_mode=setgroups_mode,
        )
        stage = "install_seccomp"
        _install_seccomp()
        stage = "drop_capabilities"
        _drop_capabilities()
        if (
            os.getuid() != identity["uid"]
            or os.geteuid() != identity["euid"]
            or os.getgid() != identity["gid"]
            or os.getegid() != identity["egid"]
            or os.getgroups() != identity["supplementary_groups"]
        ):
            raise RuntimeError("workload_identity_drift_before_exec")
        metadata = {
            "apparmor": apparmor,
            "cap_eff": _capability_effective_hex(),
            "host_uid_mapped_nonroot": identity["uid_map"][0]["outside_id"] != 0,
            "namespaces": namespaces,
            "no_new_privileges": str(libc.prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0)),
            "root_read_only": bool(os.statvfs("/").f_flag & os.ST_RDONLY),
            "seccomp_mode": str(libc.prctl(PR_GET_SECCOMP, 0, 0, 0, 0)),
            "workload_identity": identity,
        }
        os.write(metadata_fd, json.dumps(metadata, sort_keys=True).encode())
        os.close(metadata_fd)
        metadata_fd = -1
        argv = payload["command"]["argv"]
        executable = argv[0] if argv[0].startswith("/") else f"/bin/{argv[0]}"
        stage = "execute_workload"
        os.execve(  # noqa: S606 - structured argv inside the isolated root.
            executable,
            argv,
            {"HOME": "/workspace", "LANG": "C.UTF-8", "PATH": "/bin"},
        )
    except BaseException as exc:
        if metadata_fd >= 0:
            failure = {
                "error_type": type(exc).__name__,
                "errno": getattr(exc, "errno", None),
                "failure_stage": stage,
            }
            os.write(metadata_fd, json.dumps(failure, sort_keys=True).encode())
            os.close(metadata_fd)
            metadata_fd = -1
        raise
    finally:
        for target in (
            root_path / "proc",
            root_path / "tmp",
            root_path / "workspace",
            root_path,
        ):
            with suppress(OSError):
                _umount(target)
    return 126


def main() -> int:
    if len(sys.argv) < 2:
        return 2
    mode = sys.argv[1]
    if mode == "serve":
        return _serve()
    if mode == "host-ns-prepare":
        return _prepare_host_namespace_references()
    if mode in {"execute", "terminate", "probe"}:
        return _client(mode)
    if mode == "enter":
        return _enter(sys.argv[2:])
    if mode == "isolate":
        return _isolate(sys.argv[2:])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
