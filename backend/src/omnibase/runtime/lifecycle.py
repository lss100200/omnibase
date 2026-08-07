"""Safe local lifecycle wrapper for approved non-hostile-code profiles.

The desktop lifecycle is a thin, allowlisted wrapper over repository Compose
configuration. It only ever invokes ``docker compose`` with an explicit
``--env-file .env.example`` and an argument array (never a shell command string
built from user input). Hardened mode stays blocked: the desktop wrapper cannot
enable hostile-code isolation on its own and reports it as ``not_proven``.

Windows, Linux and macOS behave differently (service availability, port probe
semantics, GPU probes). Evidence from one host is never generalized to another;
the platform matrix in :class:`CapabilityReport` records this explicitly.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from omnibase.runtime.capabilities import (
    ProductMode,
    check_port,
    probe_capabilities,
    suggest_port,
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

    def to_dict(self) -> dict[str, object]:
        return {
            "exit_code": self.exit_code,
            "stdout": dict(self.stdout),
            "stderr": dict(self.stderr),
            "redacted": self.redacted,
        }


def _resolve_compose_env_file(repo_root: Path) -> Path:
    """Return the explicit ``.env.example`` path used for all Compose verbs."""
    env_file = repo_root / ".env.example"
    if not env_file.is_file():
        raise FileNotFoundError("compose_env_file_missing:.env.example")
    return env_file


def _compose_command(
    verb: str,
    *,
    repo_root: Path,
    services: Sequence[str] = (),
    extra_args: Sequence[str] = (),
) -> list[str]:
    """Build a Compose argument array with an explicit safe env file.

    ``docker compose`` is invoked with ``--env-file .env.example`` so the
    repository root ``.env`` is never implicitly read or expanded. The verb and
    service names are validated against closed allowlists. The result is always
    an argument array passed directly to ``subprocess``: it is never joined
    into a shell string.
    """
    if verb not in ALLOWED_VERBS:
        raise ValueError(f"verb_not_allowed:{verb}")
    for service in services:
        if service not in ALLOWED_SERVICES:
            raise ValueError(f"service_not_allowed:{service}")
    compose_executable = shutil.which("docker")
    if compose_executable is None:
        raise FileNotFoundError("docker_executable_not_found")
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


def _run_bounded(
    command: Sequence[str],
    *,
    timeout: float,
) -> tuple[int, str, str]:
    """Run a command argument array with a bounded timeout, no shell.

    Output is captured and length-bounded before redaction. The command is
    passed as a list; ``shell`` is never True.
    """
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            timeout=timeout,
            capture_output=True,
            text=True,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = (exc.stderr or "timeout") if isinstance(exc.stderr, str) else "timeout"
        return 124, _bounded(stdout), _bounded(stderr)
    except FileNotFoundError:
        return 127, "", "executable_not_found"
    return (
        completed.returncode,
        _bounded(completed.stdout),
        _bounded(completed.stderr),
    )


def _bounded(text: str, *, limit: int = 8192) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}...[truncated:{len(text)}]"


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
    return report.to_dict()


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
    exit_code, stdout, stderr = _run_bounded(command, timeout=timeout)
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
    exit_code, stdout, stderr = _run_bounded(command, timeout=request.timeout_seconds)
    return LifecycleResult(
        exit_code=exit_code,
        stdout=redact_mapping({"lines": stdout}),
        stderr=redact_mapping({"lines": stderr}),
    )


def status(
    request: LifecycleRequest,
    *,
    repo_root: Path,
) -> LifecycleResult:
    """Report service status via Compose ``ps``; output is redacted."""
    command = _compose_command("ps", repo_root=repo_root, services=request.services)
    exit_code, stdout, stderr = _run_bounded(command, timeout=min(request.timeout_seconds, 30.0))
    return LifecycleResult(
        exit_code=exit_code,
        stdout=redact_mapping({"lines": stdout}),
        stderr=redact_mapping({"lines": stderr}),
    )


def health(
    *,
    repo_root: Path,
    ports: Sequence[int] = (8000, 3000),
) -> dict[str, object]:
    """Report advisory health: capability readiness + port availability.

    This is advisory only. A green health report does not reserve ports or
    prove production readiness.
    """
    report = probe_capabilities(ports, root=repo_root)
    port_states = ports_status(ports=ports)
    service_statuses = _service_status_from_ps(repo_root=repo_root, timeout=20.0)
    return {
        "capabilities": report.to_dict(),
        "ports": port_states,
        "services": [
            {"name": s.name, "state": s.state, "detail": s.detail} for s in service_statuses
        ],
        "advisory": True,
    }


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
    exit_code, stdout, stderr = _run_bounded(command, timeout=request.timeout_seconds)
    return LifecycleResult(
        exit_code=exit_code,
        stdout=redact_mapping({"lines": stdout}),
        stderr=redact_mapping({"lines": stderr}),
    )


def stop(
    request: LifecycleRequest,
    *,
    repo_root: Path,
) -> LifecycleResult:
    """Stop allowlisted services via Compose ``stop``."""
    command = _compose_command("stop", repo_root=repo_root, services=request.services)
    exit_code, stdout, stderr = _run_bounded(command, timeout=request.timeout_seconds)
    return LifecycleResult(
        exit_code=exit_code,
        stdout=redact_mapping({"lines": stdout}),
        stderr=redact_mapping({"lines": stderr}),
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
