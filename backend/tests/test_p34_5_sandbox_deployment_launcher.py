from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from uuid import uuid4

import pytest


def _load_launcher() -> ModuleType:
    repository_path = (
        Path(__file__).resolve().parents[2] / "deployment/sandbox/omnibase-isolation-launcher.py"
    )
    container_path = Path("/deployment/sandbox/omnibase-isolation-launcher.py")
    path = container_path if container_path.is_file() else repository_path
    if not path.is_file():
        raise RuntimeError("deployment_launcher_source_unavailable")
    spec = importlib.util.spec_from_file_location("omnibase_deployment_isolation_launcher", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load_launcher()


def _limits() -> dict[str, int]:
    return {
        "cpu_millis": 100,
        "inodes": 64,
        "memory_bytes": 16 * 1024 * 1024,
        "output_bytes": 4096,
        "pids": 8,
        "wall_time_seconds": 5,
        "writable_bytes": 1024 * 1024,
    }


def _payload() -> dict[str, object]:
    return {
        "binding_digest": "a" * 64,
        "command": {
            "argv": ["true"],
            "max_output_bytes": 4096,
            "timeout_seconds": 5,
        },
        "operation_id": str(uuid4()),
        "runner_id": str(uuid4()),
        "runtime_instance_id": str(uuid4()),
        "runtime_spec": {"limits": _limits()},
    }


def _runner_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "runner"
    (root / "runtimes").mkdir(parents=True)
    (root / "evidence").mkdir()
    monkeypatch.setattr(launcher, "RUNNER_ROOT", root)
    monkeypatch.setattr(launcher, "LAUNCHER", tmp_path / "launcher")
    monkeypatch.setattr(launcher, "_validate_payload", lambda payload: None)
    return root


def test_prepare_cgroup_cleans_partial_limit_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(launcher, "CGROUP_ROOT", tmp_path)
    writes = 0

    def fail_second_write(cgroup: Path, name: str, value: str) -> None:
        nonlocal writes
        del cgroup, name, value
        writes += 1
        if writes == 2:
            raise OSError("fault-injected cgroup limit write")

    monkeypatch.setattr(launcher, "_write_cgroup_value", fail_second_write)
    monkeypatch.setattr(launcher, "_kill_and_prove_empty", lambda cgroup: True)
    runtime_id = str(uuid4())

    with pytest.raises(OSError, match="fault-injected"):
        launcher._prepare_cgroup(runtime_id, _limits())

    assert not (tmp_path / runtime_id).exists()


def test_bounded_communicate_always_closes_selector(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed = False

    class _Stream:
        def fileno(self) -> int:
            return 7

    class _Selector:
        def register(self, stream, event) -> None:
            del stream, event

        def get_map(self) -> dict[str, object]:
            return {"stdout": object()}

        def select(self, *, timeout: float):
            del timeout
            raise OSError("fault-injected selector failure")

        def close(self) -> None:
            nonlocal closed
            closed = True

    class _Process:
        stdout = _Stream()
        stderr = None

        def poll(self) -> None:
            return None

    monkeypatch.setattr(launcher.selectors, "DefaultSelector", _Selector)
    monkeypatch.setattr(launcher.os, "set_blocking", lambda fd, value: None)

    with pytest.raises(OSError, match="selector failure"):
        launcher._bounded_communicate(
            _Process(), timeout=1, output_limit=64, cgroup=tmp_path / "cgroup"
        )

    assert closed is True


def test_execute_spawn_failure_proves_cgroup_empty_before_removing_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runner_root(tmp_path, monkeypatch)
    payload = _payload()
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    cleanup_calls: list[str] = []
    monkeypatch.setattr(launcher, "_prepare_cgroup", lambda runtime_id, limits: cgroup)
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("fault-injected spawn failure")),
    )
    monkeypatch.setattr(
        launcher,
        "_kill_and_prove_empty",
        lambda path: cleanup_calls.append(str(path)) or True,
    )

    with pytest.raises(OSError, match="spawn failure"):
        launcher._execute(payload)

    runtime_dir = root / "runtimes" / str(payload["runtime_instance_id"])
    assert cleanup_calls == [str(cgroup)]
    assert not cgroup.exists()
    assert not runtime_dir.exists()


def test_execute_communicate_failure_cleans_cgroup_and_process_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runner_root(tmp_path, monkeypatch)
    payload = _payload()
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()

    class _Process:
        pid = 42
        stdout = None
        stderr = None

        def poll(self) -> None:
            return None

    process = _Process()
    terminated: list[object] = []
    monkeypatch.setattr(launcher, "_prepare_cgroup", lambda runtime_id, limits: cgroup)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(
        launcher,
        "_bounded_communicate",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            OSError("fault-injected communicate failure")
        ),
    )
    monkeypatch.setattr(launcher, "_kill_and_prove_empty", lambda path: True)
    monkeypatch.setattr(
        launcher,
        "_terminate_process_group",
        lambda value: terminated.append(value),
    )

    with pytest.raises(OSError, match="communicate failure"):
        launcher._execute(payload)

    runtime_dir = root / "runtimes" / str(payload["runtime_instance_id"])
    assert terminated == [process]
    assert not cgroup.exists()
    assert not runtime_dir.exists()


def test_cleanup_unproven_preserves_runtime_evidence_and_skips_process_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cgroup = tmp_path / "cgroup"
    runtime_dir = tmp_path / "runtime"
    cgroup.mkdir()
    runtime_dir.mkdir()
    terminated = False
    monkeypatch.setattr(launcher, "_kill_and_prove_empty", lambda path: False)

    def terminate(process) -> None:
        nonlocal terminated
        del process
        terminated = True

    monkeypatch.setattr(launcher, "_terminate_process_group", terminate)

    with pytest.raises(RuntimeError, match="runner_cgroup_termination_unproven"):
        launcher._cleanup_execution(
            cgroup=cgroup,
            process=None,
            runtime_dir=runtime_dir,
        )

    assert terminated is False
    assert cgroup.exists()
    assert runtime_dir.exists()


def test_execute_evidence_write_failure_occurs_after_safe_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runner_root(tmp_path, monkeypatch)
    payload = _payload()
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    for name in ("cpu.stat", "memory.events", "pids.events"):
        (cgroup / name).write_text("ok 0\n")

    class _Process:
        returncode = 0
        stdout = None
        stderr = None

    cleaned = False
    monkeypatch.setattr(launcher, "_prepare_cgroup", lambda runtime_id, limits: cgroup)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *args, **kwargs: _Process())
    monkeypatch.setattr(
        launcher,
        "_bounded_communicate",
        lambda *args, **kwargs: (b"", b"", False, False),
    )

    def cleanup(*, cgroup: Path, process, runtime_dir: Path | None) -> None:
        nonlocal cleaned
        del cgroup, process, runtime_dir
        cleaned = True

    monkeypatch.setattr(launcher, "_cleanup_execution", cleanup)
    original_write_text = Path.write_text

    def fail_evidence_write(path: Path, data: str, *args, **kwargs) -> int:
        if path.parent == root / "evidence":
            assert cleaned is True
            raise OSError("fault-injected evidence write failure")
        return original_write_text(path, data, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_evidence_write)

    with pytest.raises(OSError, match="evidence write failure"):
        launcher._execute(payload)

    assert cleaned is True


def test_execute_cleanup_unproven_does_not_delete_runtime_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _runner_root(tmp_path, monkeypatch)
    payload = _payload()
    cgroup = tmp_path / "cgroup"
    cgroup.mkdir()
    monkeypatch.setattr(launcher, "_prepare_cgroup", lambda runtime_id, limits: cgroup)
    monkeypatch.setattr(
        launcher.subprocess,
        "Popen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("fault-injected spawn failure")),
    )
    monkeypatch.setattr(launcher, "_kill_and_prove_empty", lambda path: False)

    with pytest.raises(RuntimeError, match="runner_cgroup_termination_unproven"):
        launcher._execute(payload)

    runtime_dir = root / "runtimes" / str(payload["runtime_instance_id"])
    assert cgroup.exists()
    assert runtime_dir.exists()


def test_deployment_launcher_has_no_shell_or_environment_secret_access() -> None:
    source = Path(launcher.__file__).read_text(encoding="utf-8")
    assert "shell=True" not in source
    assert "os.environ" not in source
    assert "getenv(" not in source
    assert "docker" not in source.lower()
    assert "podman" not in source.lower()
