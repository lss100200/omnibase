"""Provider-neutral local execution capability contracts.

This module only observes host capabilities and describes safe local modes. It
never treats Docker/WSL/Podman as hostile-code isolation evidence and never
enables a production runtime by itself. Reported facts carry explicit
provenance and an evidence state so consumers cannot mistake "executable
present" for "capability available".
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Iterable


class ExecutionBackend(StrEnum):
    NO_TOOL = "no_tool"
    LOCAL_CONTAINER = "local_container"
    HARDENED_SANDBOX = "hardened_sandbox"
    REMOTE_RUNNER = "remote_runner"


class ProductMode(StrEnum):
    LITE = "lite"
    LOCAL = "local"
    HARDENED = "hardened"


class EvidenceState(StrEnum):
    """How a reported capability fact was established.

    * ``configured``: caller supplied the value explicitly.
    * ``detected``: a bounded local probe produced direct evidence.
    * ``available``: probe evidence plus the resource responded successfully.
    * ``unavailable``: probe evidence showed the resource is not usable.
    * ``unknown``: no bounded probe ran; absence of evidence, not evidence of
      absence.
    * ``not_applicable``: the fact does not apply on this host.
    * ``not_proven``: a platform claim that has not been independently verified
      on this host. Used for cross-platform matrices where only one host ran.
    """

    CONFIGURED = "configured"
    DETECTED = "detected"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    NOT_PROVEN = "not_proven"


@dataclass(frozen=True)
class PortStatus:
    port: int
    available: bool
    reason: str | None = None
    evidence: EvidenceState = EvidenceState.DETECTED


@dataclass(frozen=True)
class CapabilityReport:
    os_name: str
    architecture: str
    memory_bytes: int | None
    disk_free_bytes: int | None
    gpu: str
    virtualization: str
    container_engine: str
    network: str
    ports: tuple[PortStatus, ...]
    backends: tuple[ExecutionBackend, ...]
    modes: tuple[ProductMode, ...]
    evidence: tuple[str, ...] = field(default_factory=tuple)
    facts: tuple[tuple[str, EvidenceState, str], ...] = field(default_factory=tuple)
    platform_matrix: tuple[tuple[str, str, EvidenceState], ...] = field(default_factory=tuple)

    def supports(self, mode: ProductMode) -> bool:
        return mode in self.modes

    def to_dict(self) -> dict[str, object]:
        """Return safe, JSON-compatible capability data with provenance."""
        return {
            "os": self.os_name,
            "architecture": self.architecture,
            "memory_bytes": self.memory_bytes,
            "disk_free_bytes": self.disk_free_bytes,
            "gpu": self.gpu,
            "virtualization": self.virtualization,
            "container_engine": self.container_engine,
            "network": self.network,
            "ports": [
                {
                    "port": item.port,
                    "available": item.available,
                    "reason": item.reason,
                    "evidence": item.evidence.value,
                }
                for item in self.ports
            ],
            "backends": [item.value for item in self.backends],
            "modes": [item.value for item in self.modes],
            "evidence": list(self.evidence),
            "facts": [
                {"name": name, "state": state.value, "provenance": provenance}
                for name, state, provenance in self.facts
            ],
            "platform_matrix": [
                {"host": host, "claim": claim, "state": state.value}
                for host, claim, state in self.platform_matrix
            ],
        }


def _memory_bytes() -> tuple[int | None, EvidenceState]:
    try:
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            size = os.sysconf("SC_PAGE_SIZE")
            return int(pages * size), EvidenceState.DETECTED
    except (OSError, ValueError):
        pass
    return None, EvidenceState.UNKNOWN


def _disk_free_bytes(path: str | Path) -> tuple[int | None, EvidenceState]:
    try:
        return int(shutil.disk_usage(path).free), EvidenceState.DETECTED
    except (OSError, ValueError):
        return None, EvidenceState.UNKNOWN


def check_port(port: int, host: str = "127.0.0.1") -> PortStatus:
    """Check a TCP port without binding or changing host state.

    Port detection is advisory only: a successful ``connect_ex`` refusal only
    proves nothing was bound at probe time. Startup must still handle a bind
    failure explicitly rather than treating this as a reservation.
    """
    if not 1 <= port <= 65535:
        return PortStatus(port, False, "invalid_port", EvidenceState.NOT_APPLICABLE)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.25)
    try:
        result = sock.connect_ex((host, port))
        if result != 0:
            return PortStatus(port, True, None, EvidenceState.DETECTED)
        return PortStatus(port, False, "in_use", EvidenceState.DETECTED)
    except OSError as exc:
        return PortStatus(port, False, type(exc).__name__, EvidenceState.UNAVAILABLE)
    finally:
        sock.close()


def suggest_port(preferred: int, *, attempts: int = 20) -> int | None:
    """Return the first available port in a bounded range, or ``None``.

    The suggestion is advisory; callers must handle bind failure explicitly.
    """
    if not 1 <= preferred <= 65535 or attempts < 1:
        return None
    for port in range(preferred, min(65535, preferred + attempts - 1) + 1):
        if check_port(port).available:
            return port
    return None


# Shared container-engine resolution contract. The capability probe and the
# lifecycle wrapper use the SAME candidates and preference order: Docker
# first, then Podman, then ``"none"``. When only Podman is observable the
# probe claims Local only because the lifecycle actually executes a controlled
# ``podman compose --env-file .env.example`` path; when neither is present
# Local is never claimed. Executable presence is evidence of local Compose
# orchestration availability only, never of hostile-code isolation.
CONTAINER_ENGINE_CANDIDATES: Final[tuple[str, ...]] = ("docker", "podman")


def resolve_container_engine() -> str:
    """Resolve the container engine shared by probe and lifecycle.

    Returns ``"docker"``, ``"podman"`` or ``"none"``. Both consumers must
    derive every claim and every Compose invocation from this single
    resolution so a Podman-only host either gets a real Podman Compose path or
    never claims Local available.
    """
    for executable in CONTAINER_ENGINE_CANDIDATES:
        if shutil.which(executable):
            return executable
    return "none"


def _container_engine() -> str:
    """Backward-compatible alias used by the probe (same shared contract)."""
    return resolve_container_engine()


def _probe_nvidia_gpu() -> tuple[str, EvidenceState, tuple[str, ...]]:
    """Bound an NVIDIA/CUDA probe to a local ``nvidia-smi`` subprocess.

    Returns ``(label, evidence, notes)``. Absence of ``nvidia-smi`` is
    ``unknown`` (a CPU-only or non-NVIDIA host), never a false negative for the
    whole runtime. The probe never captures secrets: it reads only driver/CUDA
    version and memory totals, and times out to ``unknown``.
    """
    executable = shutil.which("nvidia-smi")
    if executable is None:
        return "unknown", EvidenceState.UNKNOWN, ("nvidia-smi executable not found",)
    try:
        completed = subprocess.run(
            [executable, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            check=False,
            timeout=3.0,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", EvidenceState.UNAVAILABLE, ("nvidia-smi probe failed",)
    if completed.returncode != 0:
        return "unknown", EvidenceState.UNAVAILABLE, ("nvidia-smi non-zero exit",)
    first_line = (completed.stdout.splitlines() or [""])[0].strip()
    if not first_line:
        return "unknown", EvidenceState.UNAVAILABLE, ("nvidia-smi empty output",)
    return (
        f"nvidia:{first_line.lower()}",
        EvidenceState.AVAILABLE,
        ("nvidia-smi name/driver/memory query succeeded",),
    )


def _probe_apple_gpu() -> tuple[str, EvidenceState, tuple[str, ...]]:
    """Bound an Apple Silicon/MPS probe using platform/architecture only.

    On Apple Silicon we can establish the platform precondition without opening
    a Metal device. We never claim MPS availability from architecture alone; we
    mark it ``detected`` (platform precondition present) so consumers know the
    host is eligible but the device was not opened. Other platforms are
    ``not_applicable``.
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system != "darwin":
        return (
            "not_applicable",
            EvidenceState.NOT_APPLICABLE,
            ("apple MPS probe only applies to macOS",),
        )
    if machine in {"arm64", "aarch64"}:
        return (
            "mps:apple-silicon",
            EvidenceState.DETECTED,
            ("macOS on Apple Silicon detected; MPS device not opened",),
        )
    return (
        "mps:not_applicable",
        EvidenceState.NOT_APPLICABLE,
        ("macOS on non-Apple-Silicon architecture",),
    )


def _probe_gpu() -> tuple[str, EvidenceState, tuple[str, ...]]:
    """Run provider-neutral bounded GPU probes.

    NVIDIA first (bounded subprocess, no secrets captured), then Apple Silicon
    (platform/architecture). Both time out / fall back to ``unknown``. CPU-only
    hosts remain a valid ``unknown`` rather than a false negative.
    """
    label, state, nvidia_notes = _probe_nvidia_gpu()
    if state is not EvidenceState.UNKNOWN:
        return label, state, nvidia_notes
    label, state, apple_notes = _probe_apple_gpu()
    if state is EvidenceState.NOT_APPLICABLE:
        # No NVIDIA probe and no applicable Apple probe: keep the overall GPU
        # claim unknown instead of reporting not_applicable as a capability.
        return "unknown", EvidenceState.UNKNOWN, nvidia_notes + apple_notes
    return label, state, apple_notes


def probe_network_state(network: str | None) -> tuple[str, EvidenceState, str]:
    """Return the network availability state without inferring from hostname.

    A hostname is not network evidence. Default to ``unknown`` unless a bounded,
    explicitly requested local probe supplies direct evidence. We never perform
    an external internet request by default.
    """
    if network is not None:
        network_text = network.strip().lower()
        if network_text in {"available", "unavailable", "unknown"}:
            evidence = EvidenceState.CONFIGURED
            return network_text, evidence, "network state supplied by caller"
        return (
            "unknown",
            EvidenceState.UNKNOWN,
            (f"caller-supplied network value '{network}' not in closed set"),
        )
    return (
        "unknown",
        EvidenceState.UNKNOWN,
        ("no bounded network probe configured; hostname is not network evidence"),
    )


def _platform_matrix(os_name: str, architecture: str) -> tuple[tuple[str, str, EvidenceState], ...]:
    """Build a conservative cross-platform evidence matrix.

    Only the current tested host is marked ``detected``; Windows/macOS/Linux,
    x86_64/ARM64, NVIDIA/MPS and container variants not run on this host stay
    ``not_proven`` so evidence from one host is never generalized to another.
    """
    matrix: list[tuple[str, str, EvidenceState]] = []
    for candidate_os in ("windows", "linux", "macos"):
        state = EvidenceState.DETECTED if candidate_os == os_name else EvidenceState.NOT_PROVEN
        matrix.append((candidate_os, "host_operating_system", state))
    for candidate_arch in ("x86_64", "arm64"):
        state = (
            EvidenceState.DETECTED if candidate_arch == architecture else EvidenceState.NOT_PROVEN
        )
        matrix.append((candidate_arch, "cpu_architecture", state))
    for accelerator in ("nvidia_cuda", "apple_mps", "cpu_only"):
        matrix.append(("this_host", accelerator, EvidenceState.NOT_PROVEN))
    for container in ("docker", "podman", "wsl2", "hyperv"):
        matrix.append(("this_host", container, EvidenceState.NOT_PROVEN))
    return tuple(matrix)


def probe_capabilities(
    ports: Iterable[int] = (8000, 3000),
    *,
    root: str | Path = ".",
    network: str | None = None,
    virtualization: str | None = None,
) -> CapabilityReport:
    """Probe observable host facts and derive only provable capabilities."""
    engine = _container_engine()
    port_status = tuple(check_port(port) for port in ports)
    os_name = platform.system().lower() or "unknown"
    architecture = platform.machine().lower() or "unknown"
    network_state, network_evidence, network_note = probe_network_state(network)
    virtualization_state = virtualization or "unknown"
    gpu_label, gpu_state, gpu_notes = _probe_gpu()
    memory, memory_state = _memory_bytes()
    disk_free, disk_state = _disk_free_bytes(root)

    facts: list[tuple[str, EvidenceState, str]] = [
        ("memory", memory_state, "os.sysconf physical pages probe"),
        ("disk_free", disk_state, "shutil.disk_usage probe"),
        ("gpu", gpu_state, "; ".join(gpu_notes)),
        ("network", network_evidence, network_note),
        (
            "container_engine",
            EvidenceState.DETECTED if engine != "none" else EvidenceState.UNKNOWN,
            "shared resolve_container_engine shutil.which probe (docker then podman)",
        ),
    ]
    evidence: list[str] = [
        "host probe only",
        "hardened isolation not proven",
        f"gpu evidence: {gpu_state.value}",
        f"network evidence: {network_evidence.value}",
    ]

    backends = [ExecutionBackend.NO_TOOL]
    modes = [ProductMode.LITE]
    if engine != "none":
        backends.append(ExecutionBackend.LOCAL_CONTAINER)
        modes.append(ProductMode.LOCAL)
        evidence.append(f"{engine} executable found")
        facts.append(
            ("local_container_engine", EvidenceState.DETECTED, f"{engine} executable present")
        )
    else:
        evidence.append("no Docker or Podman executable found")

    # Hardened stays blocked unless an independently sealed P34.5/P34.7 target
    # evidence chain is injected and verified. The desktop probe never enables
    # this mode; Docker/WSL presence is not hostile-code isolation proof.
    evidence.append("hardened mode requires independently sealed runner evidence")
    facts.append(
        ("hardened_isolation", EvidenceState.NOT_PROVEN, "no sealed runner evidence on this host")
    )

    return CapabilityReport(
        os_name=os_name,
        architecture=architecture,
        memory_bytes=memory,
        disk_free_bytes=disk_free,
        gpu=gpu_label,
        virtualization=virtualization_state,
        container_engine=engine,
        network=network_state,
        ports=port_status,
        backends=tuple(backends),
        modes=tuple(modes),
        evidence=tuple(evidence),
        facts=tuple(facts),
        platform_matrix=_platform_matrix(os_name, architecture),
    )


__all__ = [
    "CONTAINER_ENGINE_CANDIDATES",
    "CapabilityReport",
    "EvidenceState",
    "ExecutionBackend",
    "PortStatus",
    "ProductMode",
    "check_port",
    "probe_capabilities",
    "probe_network_state",
    "resolve_container_engine",
    "suggest_port",
]
