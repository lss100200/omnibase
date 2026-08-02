from __future__ import annotations

import ast
import inspect
import io
import json
import os
import signal
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

import omnibase.sandbox.runtime_driver as driver_module
import omnibase.sandbox.runtime_probe as probe_module
from omnibase.sandbox.contracts import (
    SandboxAction,
    SandboxCommandSpec,
    SandboxIsolationPolicy,
    SandboxNetworkPolicy,
    SandboxOperationRequest,
    SandboxRejected,
    SandboxRelativePath,
    SandboxResourceLimits,
    SandboxRuntimeHandle,
    SandboxRuntimeSpec,
    SandboxUnavailable,
    VerifiedSandboxAuthorization,
)
from omnibase.sandbox.control import (
    SandboxControlAction,
    SandboxControlRequest,
    VerifiedSandboxControlAuthorization,
)
from omnibase.sandbox.host import VerifiedRunnerHost
from omnibase.sandbox.operations import SandboxOperationIntent
from omnibase.sandbox.runner import (
    RunnerExecutionPlan,
    RunnerIsolationProfile,
    RunnerPlatform,
    RunnerTerminationPlan,
)
from omnibase.sandbox.runtime_driver import (
    AttestedLinuxLocalRuntimeDriver,
    UnavailableLinuxRuntimeDriver,
    execution_binding_digest,
)
from omnibase.sandbox.runtime_probe import LinuxRuntimeAttestation, SystemLinuxRuntimeProbe

_A = "a" * 64
_B = "b" * 64
_C = "c" * 64
_D = "d" * 64
_E = "e" * 64
_F = "f" * 64


def _profile() -> RunnerIsolationProfile:
    return RunnerIsolationProfile(
        platform=RunnerPlatform.LINUX,
        cgroup_v2=True,
        user_namespace=True,
        pid_namespace=True,
        mount_namespace=True,
        network_namespace=True,
        seccomp_profile_digest=_B,
        lsm_profile_digest=_C,
        bounded_kill_seconds=5,
    )


def _request() -> SandboxOperationRequest:
    return SandboxOperationRequest(
        operation_id=uuid4(),
        action=SandboxAction.EXEC,
        tenant_id=uuid4(),
        workspace_id=uuid4(),
        run_id=uuid4(),
        runtime_instance_id=uuid4(),
        capability_grant_id=uuid4(),
        node_id=uuid4(),
        lease_id=uuid4(),
        workspace_generation=3,
        run_fencing_token=5,
        node_fencing_token=7,
        workload_identity_thumbprint=_D,
    )


def _spec() -> SandboxRuntimeSpec:
    return SandboxRuntimeSpec(
        template_digest=_A,
        policy_digest=_B,
        limits=SandboxResourceLimits(
            cpu_millis=500,
            memory_bytes=128 * 1024 * 1024,
            pids=32,
            writable_bytes=32 * 1024 * 1024,
            inodes=4096,
            wall_time_seconds=30,
            output_bytes=64 * 1024,
        ),
        network=SandboxNetworkPolicy(),
        isolation=SandboxIsolationPolicy(run_as_uid=10_000, run_as_gid=10_000),
    )


def _plan(now: datetime) -> tuple[RunnerExecutionPlan, VerifiedRunnerHost]:
    request = _request()
    intent = SandboxOperationIntent(
        operation_id=request.operation_id,
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        run_id=request.run_id,
        runtime_instance_id=request.runtime_instance_id,
        capability_grant_id=request.capability_grant_id,
        workspace_generation=request.workspace_generation,
        run_fencing_token=request.run_fencing_token,
        node_fencing_token=request.node_fencing_token,
        action=request.action.value,
        request_digest=_A,
        spec_digest=_B,
    )
    profile = _profile()
    plan = RunnerExecutionPlan(
        intent=intent,
        request=request,
        authorization=VerifiedSandboxAuthorization(
            request=request,
            verified_at=now,
            expires_at=now + timedelta(seconds=30),
            verification_digest=_E,
        ),
        runtime_handle=SandboxRuntimeHandle(uuid4()),
        runtime_spec=_spec(),
        command=SandboxCommandSpec(
            argv=("python", "-I", "probe.py"),
            cwd=SandboxRelativePath("workspace"),
            timeout_seconds=10,
            max_output_bytes=4096,
        ),
        isolation_profile=profile,
    )
    host = VerifiedRunnerHost(
        runner_id=uuid4(),
        node_id=request.node_id,
        node_fencing_token=request.node_fencing_token,
        runner_identity_thumbprint=_F,
        isolation_profile_digest=profile.digest(),
        verified_at=now,
        expires_at=now + timedelta(seconds=20),
        evidence_digest=_C,
    )
    return plan, host


def _attestation(
    *,
    runner_id,
    profile: RunnerIsolationProfile,
    now: datetime,
    ready: bool = True,
) -> LinuxRuntimeAttestation:
    return LinuxRuntimeAttestation(
        runner_id=runner_id,
        isolation_profile_digest=profile.digest(),
        launcher_digest=_A,
        runner_root_digest=_B,
        verified_at=now,
        expires_at=now + timedelta(seconds=30),
        evidence_digest=_C,
        ready_for_untrusted_execution=ready,
        missing_controls=() if ready else ("linux",),
    )


def _termination_plan(
    *,
    execution_plan: RunnerExecutionPlan,
    now: datetime,
) -> RunnerTerminationPlan:
    request = execution_plan.request
    control = SandboxControlRequest(
        operation_id=uuid4(),
        action=SandboxControlAction.EMERGENCY_DESTROY,
        controller_id=uuid4(),
        controller_identity_thumbprint=_E,
        tenant_id=request.tenant_id,
        workspace_id=request.workspace_id,
        run_id=request.run_id,
        runtime_instance_id=request.runtime_instance_id,
        node_id=request.node_id,
        runtime_handle=execution_plan.runtime_handle,
        workspace_generation=request.workspace_generation,
        run_fencing_token=request.run_fencing_token,
        node_fencing_token=request.node_fencing_token,
        reason_code="runner_policy_violation",
        deadline_at=now + timedelta(seconds=20),
    )
    return RunnerTerminationPlan(
        intent=SandboxOperationIntent(
            operation_id=control.operation_id,
            tenant_id=control.tenant_id,
            workspace_id=control.workspace_id,
            run_id=control.run_id,
            runtime_instance_id=control.runtime_instance_id,
            capability_grant_id=None,
            workspace_generation=control.workspace_generation,
            run_fencing_token=control.run_fencing_token,
            node_fencing_token=control.node_fencing_token,
            action=control.action.value,
            request_digest=_A,
        ),
        authorization=VerifiedSandboxControlAuthorization(
            request=control,
            verified_at=now,
            expires_at=now + timedelta(seconds=20),
            verification_digest=_F,
        ),
        isolation_profile=execution_plan.isolation_profile,
    )


class _ReceiptLauncher:
    def __init__(self, *, runner_id, mismatch: str | None = None) -> None:
        self.runner_id = runner_id
        self.mismatch = mismatch
        self.calls: list[tuple[str, dict[str, object], int, int]] = []

    @property
    def launcher_digest(self) -> str:
        return _A

    @property
    def runner_root_digest(self) -> str:
        return _B

    def invoke(
        self,
        *,
        mode: str,
        payload: bytes,
        timeout_seconds: int,
        bounded_kill_seconds: int,
    ) -> bytes:
        value = json.loads(payload)
        self.calls.append((mode, value, timeout_seconds, bounded_kill_seconds))
        operation_id = value["operation_id"]
        runtime_instance_id = value["runtime_instance_id"]
        binding_digest = value["binding_digest"]
        if self.mismatch == "operation":
            operation_id = str(uuid4())
        if self.mismatch == "binding":
            binding_digest = _F
        namespaces_isolated = self.mismatch != "namespaces"
        return json.dumps(
            {
                "binding_digest": binding_digest,
                "cgroup_empty": True,
                "evidence_digest": _E,
                "exit_code": 0,
                "namespace_evidence_digest": _D,
                "namespaces_isolated": namespaces_isolated,
                "operation_id": operation_id,
                "reason_code": "runner_execution_succeeded",
                "runner_id": str(self.runner_id),
                "runtime_instance_id": runtime_instance_id,
                "stderr_digest": _A,
                "stdout_digest": _B,
                "truncated": False,
            },
            sort_keys=True,
        ).encode()


def test_current_non_linux_host_probe_is_fail_closed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(probe_module.platform, "system", lambda: "Windows")
    probe = SystemLinuxRuntimeProbe(
        runner_id=uuid4(),
        launcher_path=tmp_path / "launcher",
        expected_launcher_digest=_A,
        runner_root=tmp_path / "runner",
        cgroup_root=tmp_path / "cgroup",
        host_namespace_root=tmp_path / "host-ns",
        seccomp_profile_path=tmp_path / "seccomp",
        lsm_profile_path=tmp_path / "lsm",
        lsm_profile_name="omnibase-runner",
    )
    report = probe.probe(_profile())
    assert report.ready_for_untrusted_execution is False
    assert report.missing_controls == ("linux",)
    with pytest.raises(SandboxRejected, match="sandbox_linux_isolation_attestation_rejected"):
        report.verify(
            runner_id=report.runner_id, isolation_profile=_profile(), now=report.verified_at
        )


def test_linux_probe_requires_all_controls_and_pinned_artifacts(
    tmp_path: Path,
    monkeypatch,
) -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    launcher = tmp_path / "launcher"
    runner_root = tmp_path / "runner"
    cgroup_root = tmp_path / "cgroup"
    host_namespace_root = tmp_path / "host-ns"
    seccomp = tmp_path / "seccomp"
    lsm = tmp_path / "lsm"
    launcher.write_bytes(b"launcher")
    runner_root.mkdir()
    cgroup_root.mkdir()
    host_namespace_root.mkdir()
    seccomp.write_bytes(b"seccomp")
    lsm.write_bytes(b"lsm")
    runner_id = uuid4()
    monkeypatch.setattr(probe_module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(probe_module.os, "geteuid", lambda: 1000, raising=False)
    monkeypatch.setattr(probe_module, "_secure_file", lambda path, owner_uid: True)
    monkeypatch.setattr(probe_module, "_private_directory", lambda path, owner_uid: True)
    monkeypatch.setattr(probe_module, "_trusted_directory", lambda path, owner_uid: True)

    def file_digest(path: Path, *, maximum_bytes: int = 32 * 1024 * 1024) -> str:
        del maximum_bytes
        return {launcher: _A, seccomp: _B, lsm: _C}[path]

    monkeypatch.setattr(probe_module, "_sha256_file", file_digest)
    probe = SystemLinuxRuntimeProbe(
        runner_id=runner_id,
        launcher_path=launcher,
        expected_launcher_digest=_A,
        runner_root=runner_root,
        cgroup_root=cgroup_root,
        host_namespace_root=host_namespace_root,
        seccomp_profile_path=seccomp,
        lsm_profile_path=lsm,
        lsm_profile_name="omnibase-runner",
        require_root_owned_launcher=False,
        clock=lambda: now,
    )
    monkeypatch.setattr(probe, "_probe_namespaces", lambda missing: {})
    monkeypatch.setattr(probe, "_probe_cgroup", lambda missing: {})
    monkeypatch.setattr(
        probe,
        "_probe_seccomp_and_lsm",
        lambda missing, isolation_profile: None,
    )
    report = probe.probe(_profile())
    assert report.ready_for_untrusted_execution is True
    assert report.missing_controls == ()
    report.verify(runner_id=runner_id, isolation_profile=_profile(), now=now)


def test_linux_probe_rejects_runner_namespaces_shared_with_init(
    tmp_path: Path,
    monkeypatch,
) -> None:
    probe = SystemLinuxRuntimeProbe(
        runner_id=uuid4(),
        launcher_path=tmp_path / "launcher",
        expected_launcher_digest=_A,
        runner_root=tmp_path / "runner",
        cgroup_root=tmp_path / "cgroup",
        host_namespace_root=tmp_path / "host-ns",
        seccomp_profile_path=tmp_path / "seccomp",
        lsm_profile_path=tmp_path / "lsm",
        lsm_profile_name="omnibase-runner",
    )
    monkeypatch.setattr(
        probe_module,
        "_namespace_identity",
        lambda path: f"same:{path.name}",
    )
    monkeypatch.setattr(
        probe_module,
        "_host_namespace_identity",
        lambda path: f"same:{path.name}",
    )
    monkeypatch.setattr(probe_module, "_read_bounded", lambda path: "1024")
    missing: set[str] = set()
    evidence = probe._probe_namespaces(missing)
    assert set(evidence) == {
        "mount_namespace",
        "network_namespace",
        "pid_namespace",
        "user_namespace",
    }
    assert missing == {
        "isolated_mount_namespace",
        "isolated_network_namespace",
        "isolated_pid_namespace",
        "isolated_user_namespace",
    }


def test_linux_probe_accepts_only_root_owned_namespace_identity_snapshots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    reference = tmp_path / "user"
    reference.write_text("123:456\n")
    reference.chmod(0o444)
    simulated_owner_uid = 0
    real_lstat = Path.lstat

    def namespace_snapshot_lstat(path: Path) -> os.stat_result:
        info = real_lstat(path)
        if path != reference:
            return info
        values = list(info)
        values[4] = simulated_owner_uid
        return os.stat_result(values)

    monkeypatch.setattr(Path, "lstat", namespace_snapshot_lstat)
    monkeypatch.setattr(
        probe_module,
        "_read_bounded",
        lambda path, maximum_bytes=64 * 1024: path.read_text(),
    )

    assert probe_module._host_namespace_identity(reference) == "123:456"

    simulated_owner_uid = 1001
    with pytest.raises(ValueError, match="not trusted"):
        probe_module._host_namespace_identity(reference)
    simulated_owner_uid = 0

    reference.chmod(0o600)
    reference.write_text("not-a-namespace")
    reference.chmod(0o444)
    with pytest.raises(ValueError, match="identity is invalid"):
        probe_module._host_namespace_identity(reference)
    reference.chmod(0o600)
    reference.write_text("123:456")
    reference.chmod(0o666)
    with pytest.raises(ValueError, match="not trusted"):
        probe_module._host_namespace_identity(reference)


def test_attested_driver_invokes_only_bound_launcher_and_parses_receipt() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    launcher = _ReceiptLauncher(runner_id=host.runner_id)
    driver = AttestedLinuxLocalRuntimeDriver(
        runner_id=host.runner_id,
        attestation=_attestation(runner_id=host.runner_id, profile=_profile(), now=now),
        launcher=launcher,
        clock=lambda: now,
    )
    receipt = driver.execute(plan=plan, host=host)
    assert receipt.operation_id == plan.request.operation_id
    assert receipt.runtime_instance_id == plan.request.runtime_instance_id
    assert receipt.binding_digest == execution_binding_digest(plan, host)
    assert receipt.cgroup_empty is True
    assert launcher.calls[0][0] == "execute"
    payload = launcher.calls[0][1]
    assert payload["command"]["argv"] == ["python", "-I", "probe.py"]
    assert payload["cgroup_name"] == str(plan.request.runtime_instance_id)
    assert payload["runtime_spec"]["network"]["mode"] == "deny_all"
    assert "env" not in payload


def test_termination_targets_the_existing_runtime_cgroup() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    execution_plan, host = _plan(now)
    termination_plan = _termination_plan(execution_plan=execution_plan, now=now)
    launcher = _ReceiptLauncher(runner_id=host.runner_id)
    driver = AttestedLinuxLocalRuntimeDriver(
        runner_id=host.runner_id,
        attestation=_attestation(runner_id=host.runner_id, profile=_profile(), now=now),
        launcher=launcher,
        clock=lambda: now,
    )
    driver.execute(plan=execution_plan, host=host)
    driver.terminate(plan=termination_plan, host=host)
    execute_payload = launcher.calls[0][1]
    terminate_payload = launcher.calls[1][1]
    assert execute_payload["operation_id"] != terminate_payload["operation_id"]
    assert execute_payload["cgroup_name"] == str(execution_plan.request.runtime_instance_id)
    assert terminate_payload["cgroup_name"] == execute_payload["cgroup_name"]


@pytest.mark.parametrize("mismatch", ["operation", "binding"])
def test_attested_driver_rejects_mismatched_launcher_receipt(mismatch: str) -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    driver = AttestedLinuxLocalRuntimeDriver(
        runner_id=host.runner_id,
        attestation=_attestation(runner_id=host.runner_id, profile=_profile(), now=now),
        launcher=_ReceiptLauncher(runner_id=host.runner_id, mismatch=mismatch),
        clock=lambda: now,
    )
    with pytest.raises(SandboxRejected, match="sandbox_launcher_receipt_binding_rejected"):
        driver.execute(plan=plan, host=host)


def test_attested_driver_rejects_unproven_execution_namespaces() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    driver = AttestedLinuxLocalRuntimeDriver(
        runner_id=host.runner_id,
        attestation=_attestation(runner_id=host.runner_id, profile=_profile(), now=now),
        launcher=_ReceiptLauncher(runner_id=host.runner_id, mismatch="namespaces"),
        clock=lambda: now,
    )
    with pytest.raises(SandboxRejected, match="sandbox_launcher_receipt_invalid"):
        driver.execute(plan=plan, host=host)


def test_driver_rejects_unready_expired_or_wrong_runner_attestation() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    cases = (
        _attestation(runner_id=host.runner_id, profile=_profile(), now=now, ready=False),
        _attestation(
            runner_id=uuid4(),
            profile=_profile(),
            now=now,
        ),
    )
    for attestation in cases:
        driver = AttestedLinuxLocalRuntimeDriver(
            runner_id=host.runner_id,
            attestation=attestation,
            launcher=_ReceiptLauncher(runner_id=host.runner_id),
            clock=lambda: now,
        )
        with pytest.raises(SandboxRejected):
            driver.execute(plan=plan, host=host)


def test_unavailable_driver_is_the_safe_default() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    plan, host = _plan(now)
    with pytest.raises(SandboxUnavailable, match="sandbox_linux_runtime_driver_unavailable"):
        UnavailableLinuxRuntimeDriver().execute(plan=plan, host=host)


def test_launcher_capture_stops_at_the_configured_output_limit() -> None:
    capture = driver_module._BoundedCapture(stdout_limit=4, stderr_limit=4)
    capture.read("stdout", io.BytesIO(b"12345678"))
    assert capture.overflow.is_set()
    assert capture.value("stdout") == b"1234"


def test_launcher_failure_always_cleans_the_bound_operation_cgroup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    operation_id = uuid4()
    cgroup_name = uuid4()
    launcher = object.__new__(driver_module.SubprocessIsolationLauncher)
    launcher._launcher_digest = _A
    launcher._runner_root_digest = _B
    launcher._cgroup_root_digest = _C
    launcher._cgroup_root = tmp_path / "cgroups"
    cleaned: list[Path] = []

    class _Pipe:
        pass

    class _Process:
        stdin = _Pipe()
        stdout = _Pipe()
        stderr = _Pipe()

    class _JoinedThread:
        def join(self, *, timeout: int) -> None:
            del timeout

    monkeypatch.setattr(launcher, "_hash_launcher", lambda: _A)
    monkeypatch.setattr(launcher, "_hash_runner_root", lambda: _B)
    monkeypatch.setattr(launcher, "_hash_cgroup_root", lambda: _C)
    monkeypatch.setattr(launcher, "_spawn", lambda mode: _Process())
    monkeypatch.setattr(
        launcher,
        "_start_capture",
        lambda process: (object(), (_JoinedThread(), _JoinedThread())),
    )
    monkeypatch.setattr(launcher, "_send_and_wait", lambda **kwargs: None)

    def _fail_capture(**kwargs) -> bytes:
        raise SandboxRejected("sandbox_launcher_failed")

    monkeypatch.setattr(launcher, "_finish_capture", _fail_capture)

    def _record_cleanup(*, process, cgroup_path: Path, bounded_kill_seconds: int) -> None:
        del process, bounded_kill_seconds
        cleaned.append(cgroup_path)

    monkeypatch.setattr(launcher, "_cleanup_failed_invoke", _record_cleanup)
    payload = json.dumps(
        {
            "cgroup_name": str(cgroup_name),
            "operation_id": str(operation_id),
        }
    ).encode()

    with pytest.raises(SandboxRejected, match="sandbox_launcher_failed"):
        launcher.invoke(
            mode="execute",
            payload=payload,
            timeout_seconds=1,
            bounded_kill_seconds=1,
        )

    assert cleaned == [launcher._cgroup_root / str(cgroup_name)]


def test_process_tree_termination_escalates_from_term_to_kill(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[int] = []

    class _Process:
        pid = 42

        def __init__(self) -> None:
            self.waits = 0

        def poll(self):
            return None

        def wait(self, *, timeout: int):
            del timeout
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("launcher", 1)
            return 0

    monkeypatch.setattr(driver_module.os, "killpg", lambda pid, sig: calls.append(sig))
    monkeypatch.setattr(
        driver_module.SubprocessIsolationLauncher,
        "_kill_cgroup",
        lambda path, bounded_kill_seconds: True,
    )
    driver_module.SubprocessIsolationLauncher._terminate_tree(
        _Process(),
        cgroup_path=tmp_path / "operation-cgroup",
        bounded_kill_seconds=1,
    )
    assert calls == [signal.SIGTERM, signal.SIGKILL]


def test_process_tree_termination_fails_closed_without_cgroup_proof(
    tmp_path: Path,
    monkeypatch,
) -> None:
    class _ExitedProcess:
        pid = 42

        def poll(self):
            return 0

    monkeypatch.setattr(
        driver_module.SubprocessIsolationLauncher,
        "_kill_cgroup",
        lambda path, bounded_kill_seconds: False,
    )
    with pytest.raises(SandboxUnavailable, match="sandbox_cgroup_termination_unproven"):
        driver_module.SubprocessIsolationLauncher._terminate_tree(
            _ExitedProcess(),
            cgroup_path=tmp_path / "operation-cgroup",
            bounded_kill_seconds=1,
        )


@pytest.mark.parametrize("cgroup_name", ["../escape", "not-a-uuid", "00000000"])
def test_launcher_rejects_non_uuid_cgroup_names(cgroup_name: str) -> None:
    payload = json.dumps(
        {
            "cgroup_name": cgroup_name,
            "operation_id": str(uuid4()),
        }
    ).encode()
    with pytest.raises(SandboxRejected, match="sandbox_launcher_request_invalid"):
        driver_module.SubprocessIsolationLauncher._cgroup_name(payload)


def test_a4_runtime_source_has_no_docker_data_service_or_environment_access() -> None:
    forbidden_imports = {"docker", "httpx", "minio", "psycopg", "redis", "requests"}
    for module in (driver_module, probe_module):
        tree = ast.parse(inspect.getsource(module))
        imported_roots: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.partition(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.partition(".")[0])
        assert imported_roots.isdisjoint(forbidden_imports)
    source = inspect.getsource(driver_module)
    assert "os.environ" not in source
    assert "getenv(" not in source
    assert "shell=True" not in source
