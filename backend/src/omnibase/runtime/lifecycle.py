"""Safe local lifecycle wrapper for approved non-hostile-code profiles.

The desktop lifecycle is a thin, allowlisted wrapper over repository Compose
configuration. It shares the container-engine resolution contract
(:func:`omnibase.runtime.capabilities.resolve_engine_resolution`) with the
capability probe: Docker first, then Podman, and a controlled
``podman compose`` path when Podman is the only engine. The lifecycle uses the
**canonical absolute path of the verified executable as ``argv[0]``** and
re-verifies its stable file identity before building any Compose command; it
never re-resolves ``PATH`` via ``shutil.which``, so a TOCTOU that swaps the
``which`` result after probe time cannot redirect execution. It only ever
invokes Compose with an explicit ``--env-file .env.example`` and an argument
array (never a shell command string built from user input). Hardened mode
stays blocked: the desktop wrapper cannot enable hostile-code isolation on its
own and reports it as ``not_proven``.

Output is bounded **during reading**, not after capture: each subprocess runs
with ``stdout``/``stderr`` pipes drained incrementally by bounded threads that
cap per-stream and total byte counts. When any cap is exceeded the process is
terminated and the output is marked truncated; the reader never buffers an
unbounded stream into memory or a temp file first. Timeout and byte caps are
two independent constraints and both have negative tests.

Windows, Linux and macOS behave differently (service availability, port probe
semantics, GPU probes). Evidence from one host is never generalized to another;
the platform matrix in :class:`CapabilityReport` records this explicitly.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from omnibase.runtime.capabilities import (
    ExecutableIdentity,
    ProductMode,
    check_port,
    probe_capabilities,
    resolve_engine_resolution,
    suggest_port,
    verify_executable_identity,
)
from omnibase.runtime.diagnostics import ServiceStatus, redact_mapping

# Approved product profiles. Hardened is intentionally absent: the desktop
# wrapper never claims Hardened start support.
APPROVED_PROFILES: Final[frozenset[str]] = frozenset({"lite", "local"})

# Allowlisted Compose services. The wrapper never accepts arbitrary service
# names from user input.
ALLOWED_SERVICES: Final[frozenset[str]] = frozenset(
    {"backend", "frontend", "celery-worker", "postgres", "minio", "redis"}
)

# Allowlisted Compose subcommands. The wrapper never runs ``exec`` or any
# command that could turn into arbitrary code execution.
ALLOWED_VERBS: Final[frozenset[str]] = frozenset(
    {"ps", "logs", "config", "up", "down", "stop", "start", "restart"}
)

# Bounded default timeouts for Compose lifecycle verbs.
DEFAULT_LIFECYCLE_TIMEOUT: Final[float] = 60.0
DEFAULT_LOG_TAIL: Final[int] = 200

# Real byte caps applied DURING reading (not after capture). Each stream is
# capped independently and the combined total is capped too. On exceeding any
# cap the process is terminated (best-effort) and the truncated flag is set so
# no unbounded output is ever buffered into memory first. A replaced or
# malicious executable that streams huge stdout/stderr before exit cannot
# exhaust memory; timeout and byte caps are independent constraints.
OUTPUT_BYTE_CAP_PER_STREAM: Final[int] = 65536  # 64 KiB per stream
OUTPUT_BYTE_CAP_TOTAL: Final[int] = 131072  # 128 KiB combined
# Process reaping grace period after terminate/kill.
_TERMINATE_GRACE: Final[float] = 2.0


@dataclass(frozen=True)
class LifecycleRequest:
    """A validated, allowlisted lifecycle request.

    Fields are validated at construction time so the subprocess layer only ever
    sees allowlisted values. No field is ever interpolated into a shell string.
    """

    profile: ProductMode
    services: tuple[str, ...]
    tail_lines: int = DEFAULT_LOG_TAIL
    timeout_seconds: float = DEFAULT_LIFECYCLE_TIMEOUT

    def __post_init__(self) -> None:
        if self.profile is ProductMode.HARDENED:
            raise ValueError("hardened_mode_blocked:not_proven")
        for service in self.services:
            if service not in ALLOWED_SERVICES:
                raise ValueError(f"service_not_allowed:{service}")
        if not 1 <= self.tail_lines <= 5000:
            raise ValueError("tail_lines_out_of_range")
        if not 1.0 <= self.timeout_seconds <= 600.0:
            raise ValueError("timeout_out_of_range")


@dataclass(frozen=True)
class LifecycleResult:
    exit_code: int
    stdout: Mapping[str, object]
    stderr: Mapping[str, object]
    redacted: bool = True
    truncated: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "stdout": dict(self.stdout),
            "stderr": dict(self.stderr),
            "redacted": self.redacted,
            "truncated": self.truncated,
        }


def _resolve_compose_env_file(repo_root: Path) -> Path:
    """Return the explicit ``.env.example`` path used for all Compose verbs."""
    env_file = repo_root / ".env.example"
    if not env_file.is_file():
        raise FileNotFoundError("compose_env_file_missing:.env.example")
    return env_file


def _resolve_verified_executable() -> tuple[str, ExecutableIdentity]:
    """Return the verified absolute path and identity of the resolved engine.

    Uses the SAME :func:`resolve_engine_resolution` contract the capability
    probe uses. The lifecycle takes the canonical absolute path recorded at
    probe time as ``argv[0]`` and re-verifies the stable file identity; it
    never re-resolves ``PATH`` via ``shutil.which``. A missing engine, a
    missing path/identity or any identity drift (deletion, replacement,
    symlink/reparse transition, stat change) fails closed before any Compose
    command is built.
    """
    resolution = resolve_engine_resolution()
    if resolution.container_engine == "none":
        raise FileNotFoundError("container_engine_not_found")
    path = resolution.selected_executable_path
    identity = resolution.selected_executable_identity
    if path is None or identity is None:
        raise FileNotFoundError("container_engine_not_found")
    if not verify_executable_identity(path, identity):
        raise FileNotFoundError("container_engine_identity_drift")
    return path, identity


def _compose_command(
    verb: str,
    *,
    repo_root: Path,
    services: Sequence[str] = (),
    extra_args: Sequence[str] = (),
) -> list[str]:
    """Build a Compose argument array with an explicit safe env file.

    The container engine comes from the SAME
    :func:`resolve_engine_resolution` contract the capability probe uses
    (Docker first, then Podman). The lifecycle uses the **canonical absolute
    path of the verified executable as ``argv[0]``** and re-verifies its
    stable file identity; it never re-resolves ``PATH`` via
    ``shutil.which``. When only Podman is observable the command executes a
    controlled ``podman compose --env-file .env.example -f docker-compose.yml``
    path, so a Podman-only host either really runs the lifecycle or never
    claims Local available. ``docker compose``/``podman compose`` are invoked
    with an explicit ``--env-file .env.example`` so the repository root
    ``.env`` is never implicitly read or expanded. The verb and service names
    are validated against closed allowlists. The result is always an argument
    array passed directly to ``subprocess``: it is never joined into a shell
    string.
    """
    if verb not in ALLOWED_VERBS:
        raise ValueError(f"verb_not_allowed:{verb}")
    for service in services:
        if service not in ALLOWED_SERVICES:
            raise ValueError(f"service_not_allowed:{service}")
    compose_executable, _identity = _resolve_verified_executable()
    env_file = _resolve_compose_env_file(repo_root)
    command: list[str] = [
        compose_executable,
        "compose",
        "--env-file",
        str(env_file),
        "-f",
        str(repo_root / "docker-compose.yml"),
        verb,
    ]
    command.extend(extra_args)
    command.extend(services)
    return command


def _terminate(proc: subprocess.Popen[str]) -> None:
    """Best-effort terminate then kill so bounded-reader threads can finish."""
    for _ in range(50):
        if proc.poll() is not None:
            return
        with contextlib.suppress(OSError):
            proc.terminate()
        time.sleep(0.02)
    if proc.poll() is None:
        with contextlib.suppress(OSError):
            proc.kill()


class _DrainState:
    """Mutable, lock-protected accumulation state for the bounded reader."""

    __slots__ = ("lock", "stderr", "stdout", "total", "truncated")

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.stderr: list[str] = []
        self.stdout: list[str] = []
        self.total: int = 0
        self.truncated: bool = False


def _drain_stream(stream: object, target: list[str], state: _DrainState) -> None:
    """Read one stream incrementally, capping per-stream and total bytes.

    ``stream`` is a text-mode pipe (``IO[str]``) in production and a
    ``FakeStream`` stand-in under test; ``read`` is resolved via ``getattr``
    so both satisfy the type. On any cap the process is flagged truncated and
    the drain stops; the caller terminates the process.
    """
    reader = getattr(stream, "read", None)
    if reader is None:
        return
    local = 0
    while True:
        try:
            chunk = reader(4096)
        except (OSError, ValueError):
            break
        if not chunk:
            break
        with state.lock:
            if (
                state.truncated
                or local >= OUTPUT_BYTE_CAP_PER_STREAM
                or state.total >= OUTPUT_BYTE_CAP_TOTAL
            ):
                state.truncated = True
                return
            allow = min(
                OUTPUT_BYTE_CAP_PER_STREAM - local,
                OUTPUT_BYTE_CAP_TOTAL - state.total,
                len(chunk),
            )
            if allow <= 0:
                state.truncated = True
                return
            target.append(chunk[:allow])
            local += allow
            state.total += allow
            if allow < len(chunk):
                state.truncated = True
                return


def _reap_process(proc: subprocess.Popen[str], state: _DrainState, timeout: float) -> bool:
    """Wait for ``proc`` until it exits, truncates or times out.

    Returns ``True`` when the deadline elapsed before the process exited.
    """
    deadline = time.monotonic() + timeout
    while True:
        if proc.poll() is not None:
            return False
        with state.lock:
            if state.truncated:
                return False
        if time.monotonic() >= deadline:
            return True
        time.sleep(0.02)


def _close_pipes(proc: subprocess.Popen[str]) -> None:
    """Close stdout/stderr so draining threads see EOF and finish."""
    for stream in (proc.stdout, proc.stderr):
        if stream is not None:
            with contextlib.suppress(OSError, ValueError):
                stream.close()


def _finalize_process(proc: subprocess.Popen[str]) -> None:
    """Final kill + wait after the bounded readers have stopped."""
    if proc.poll() is None:
        with contextlib.suppress(OSError):
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(timeout=_TERMINATE_GRACE)


def _assemble_result(
    proc: subprocess.Popen[str], state: _DrainState, timed_out: bool
) -> tuple[int, str, str, bool]:
    stdout = "".join(state.stdout)
    stderr = "".join(state.stderr)
    with state.lock:
        truncated = state.truncated or timed_out
    exit_code = proc.returncode if proc.returncode is not None else 124
    if timed_out:
        exit_code = 124
        if not stderr:
            stderr = "timeout"
    return exit_code, stdout, stderr, truncated


def _run_bounded(
    command: Sequence[str],
    *,
    timeout: float,
) -> tuple[int, str, str, bool]:
    """Run a command array with bounded timeout AND bounded stdout/stderr bytes.

    ``shell`` is never True; ``argv[0]`` is the verified absolute executable
    path supplied by the caller. stdout and stderr are read incrementally by
    bounded threads; each stream is capped independently and the combined total
    is capped. When any cap is exceeded the process is terminated (best-effort)
    and the ``truncated`` flag is set. Output is never buffered unbounded into
    memory or a temp file first: the caps apply during reading. Timeout and
    byte caps are independent constraints. Returns
    ``(exit_code, stdout, stderr, truncated)``.
    """
    try:
        # argv[0] is the verified absolute path resolved by
        # resolve_engine_resolution; shell is False and the
        # verb/profile/service are closed-set allowlisted.
        proc = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError:
        return 127, "", "executable_not_found", False
    except OSError as exc:
        return 126, "", f"spawn_failed:{type(exc).__name__}", False

    state = _DrainState()
    stdout_thread = threading.Thread(
        target=_drain_stream, args=(proc.stdout, state.stdout, state), daemon=True
    )
    stderr_thread = threading.Thread(
        target=_drain_stream, args=(proc.stderr, state.stderr, state), daemon=True
    )
    stdout_thread.start()
    stderr_thread.start()

    timed_out = _reap_process(proc, state, timeout)
    if proc.poll() is None:
        _terminate(proc)
    # Closing pipes signals EOF to the draining threads so they finish.
    _close_pipes(proc)
    stdout_thread.join(timeout=_TERMINATE_GRACE)
    stderr_thread.join(timeout=_TERMINATE_GRACE)
    # Still alive after grace: a final kill; the bounded readers have already
    # stopped, so no unbounded buffering can occur.
    _finalize_process(proc)
    return _assemble_result(proc, state, timed_out)


def doctor(
    *,
    repo_root: Path,
    ports: Sequence[int] = (8000, 3000),
) -> dict[str, object]:
    """Return capability/capabilities diagnostics (alias of ``capabilities``)."""
    return capabilities(repo_root=repo_root, ports=ports)


def capabilities(
    *,
    repo_root: Path,
    ports: Sequence[int] = (8000, 3000),
) -> dict[str, object]:
    """Probe observable host facts and report them with provenance."""
    report = probe_capabilities(ports, root=repo_root)
    return redact_mapping(report.to_dict())


def ports_status(
    *,
    ports: Sequence[int] = (8000, 3000),
) -> list[dict[str, object]]:
    """Report advisory port availability; startup must still handle bind failure."""
    return [
        {
            "port": check_port(port).port,
            "available": check_port(port).available,
            "reason": check_port(port).reason,
            "evidence": check_port(port).evidence.value,
            "advisory": "detection is advisory; startup must handle bind failure explicitly",
        }
        for port in ports
    ]


def suggest_port_command(preferred: int) -> dict[str, object]:
    suggestion = suggest_port(preferred)
    return {
        "requested": preferred,
        "suggested": suggestion,
        "advisory": "suggestion is advisory; startup must handle bind failure explicitly",
    }


def _service_status_from_ps(
    *,
    repo_root: Path,
    timeout: float,
) -> list[ServiceStatus]:
    command = _compose_command("ps", repo_root=repo_root)
    exit_code, stdout, _stderr, _truncated = _run_bounded(command, timeout=timeout)
    statuses: list[ServiceStatus] = []
    if exit_code == 0:
        for line in stdout.splitlines():
            # Compose `ps` output is presentation text; we only record whether
            # a service name token appears, never secrets.
            token = line.strip()
            if token and not token.startswith("NAME") and "-" in token:
                statuses.append(ServiceStatus(token.split()[0], "present"))
    else:
        statuses.append(ServiceStatus("compose_ps", "unavailable", f"exit={exit_code}"))
    return statuses


def start(
    request: LifecycleRequest,
    *,
    repo_root: Path,
) -> LifecycleResult:
    """Start allowlisted services for an approved profile using Compose ``up``.

     Uses an argument array (``-d`` detached, explicit service allowlist). Hard
    coded host binds are not claimed; the wrapper records the bind result after
     the fact. Hardened is rejected at :class:`LifecycleRequest` construction.
    """
    extra_args = ["-d"]
    command = _compose_command(
        "up", repo_root=repo_root, services=request.services, extra_args=extra_args
    )
    exit_code, stdout, stderr, truncated = _run_bounded(command, timeout=request.timeout_seconds)
    return LifecycleResult(
        exit_code=exit_code,
        stdout=redact_mapping({"lines": stdout}),
        stderr=redact_mapping({"lines": stderr}),
        truncated=truncated,
    )


def status(
    request: LifecycleRequest,
    *,
    repo_root: Path,
) -> LifecycleResult:
    """Report service status via Compose ``ps``; output is redacted."""
    command = _compose_command("ps", repo_root=repo_root, services=request.services)
    exit_code, stdout, stderr, truncated = _run_bounded(
        command, timeout=min(request.timeout_seconds, 30.0)
    )
    return LifecycleResult(
        exit_code=exit_code,
        stdout=redact_mapping({"lines": stdout}),
        stderr=redact_mapping({"lines": stderr}),
        truncated=truncated,
    )


def health(
    *,
    repo_root: Path,
    ports: Sequence[int] = (8000, 3000),
) -> dict[str, object]:
    """Report advisory health: capability readiness + port availability.

    This is advisory only. A green health report does not reserve ports or
    prove production readiness. The whole payload passes through the bounded
    diagnostic redactor so status/health text can never carry credentials.
    """
    report = probe_capabilities(ports, root=repo_root)
    port_states = ports_status(ports=ports)
    service_statuses = _service_status_from_ps(repo_root=repo_root, timeout=20.0)
    return redact_mapping(
        {
            "capabilities": report.to_dict(),
            "ports": port_states,
            "services": [
                {"name": s.name, "state": s.state, "detail": s.detail} for s in service_statuses
            ],
            "advisory": True,
        }
    )


def logs(
    request: LifecycleRequest,
    *,
    repo_root: Path,
) -> LifecycleResult:
    """Return bounded, redacted Compose ``logs --tail N`` output."""
    extra_args = ["--tail", str(request.tail_lines)]
    command = _compose_command(
        "logs",
        repo_root=repo_root,
        services=request.services,
        extra_args=extra_args,
    )
    exit_code, stdout, stderr, truncated = _run_bounded(command, timeout=request.timeout_seconds)
    return LifecycleResult(
        exit_code=exit_code,
        stdout=redact_mapping({"lines": stdout}),
        stderr=redact_mapping({"lines": stderr}),
        truncated=truncated,
    )


def stop(
    request: LifecycleRequest,
    *,
    repo_root: Path,
) -> LifecycleResult:
    """Stop allowlisted services via Compose ``stop``."""
    command = _compose_command("stop", repo_root=repo_root, services=request.services)
    exit_code, stdout, stderr, truncated = _run_bounded(command, timeout=request.timeout_seconds)
    return LifecycleResult(
        exit_code=exit_code,
        stdout=redact_mapping({"lines": stdout}),
        stderr=redact_mapping({"lines": stderr}),
        truncated=truncated,
    )


def validate_request(
    profile_name: str,
    services: Sequence[str],
    *,
    tail_lines: int = DEFAULT_LOG_TAIL,
    timeout_seconds: float = DEFAULT_LIFECYCLE_TIMEOUT,
) -> LifecycleRequest:
    """Validate and construct a lifecycle request from raw CLI inputs."""
    if profile_name not in APPROVED_PROFILES:
        if profile_name == "hardened":
            raise ValueError("hardened_mode_blocked:not_proven")
        raise ValueError(f"profile_not_allowed:{profile_name}")
    mode = ProductMode.LOCAL if profile_name == "local" else ProductMode.LITE
    return LifecycleRequest(
        profile=mode,
        services=tuple(services),
        tail_lines=tail_lines,
        timeout_seconds=timeout_seconds,
    )


__all__ = [
    "ALLOWED_SERVICES",
    "ALLOWED_VERBS",
    "APPROVED_PROFILES",
    "DEFAULT_LIFECYCLE_TIMEOUT",
    "DEFAULT_LOG_TAIL",
    "OUTPUT_BYTE_CAP_PER_STREAM",
    "OUTPUT_BYTE_CAP_TOTAL",
    "LifecycleRequest",
    "LifecycleResult",
    "capabilities",
    "doctor",
    "health",
    "logs",
    "ports_status",
    "start",
    "status",
    "stop",
    "suggest_port_command",
    "validate_request",
]
