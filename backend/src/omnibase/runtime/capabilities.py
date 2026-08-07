"""Provider-neutral local execution capability contracts.

This module only observes host capabilities and describes safe local modes. It
never treats Docker/WSL as hostile-code isolation evidence and never enables a
production runtime by itself.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING

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


@dataclass(frozen=True)
class PortStatus:
    port: int
    available: bool
    reason: str | None = None


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

    def supports(self, mode: ProductMode) -> bool:
        return mode in self.modes

    def to_dict(self) -> dict[str, object]:
        """Return safe, JSON-compatible capability data."""
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
                {"port": item.port, "available": item.available, "reason": item.reason}
                for item in self.ports
            ],
            "backends": [item.value for item in self.backends],
            "modes": [item.value for item in self.modes],
            "evidence": list(self.evidence),
        }


def _memory_bytes() -> int | None:
    try:
        if hasattr(os, "sysconf"):
            pages = os.sysconf("SC_PHYS_PAGES")
            size = os.sysconf("SC_PAGE_SIZE")
            return int(pages * size)
    except (OSError, ValueError):
        pass
    return None


def _disk_free_bytes(path: str | Path) -> int | None:
    try:
        return int(shutil.disk_usage(path).free)
    except (OSError, ValueError):
        return None


def check_port(port: int, host: str = "127.0.0.1") -> PortStatus:
    """Check a TCP port without binding or changing host state."""
    if not 1 <= port <= 65535:
        return PortStatus(port, False, "invalid_port")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.25)
    try:
        result = sock.connect_ex((host, port))
        return PortStatus(port, result != 0, None if result != 0 else "in_use")
    except OSError as exc:
        return PortStatus(port, False, type(exc).__name__)
    finally:
        sock.close()


def suggest_port(preferred: int, *, attempts: int = 20) -> int | None:
    """Return the first available port in a bounded range, or ``None``."""
    if not 1 <= preferred <= 65535 or attempts < 1:
        return None
    for port in range(preferred, min(65535, preferred + attempts - 1) + 1):
        if check_port(port).available:
            return port
    return None


def _container_engine() -> str:
    for executable in ("docker", "podman"):
        if shutil.which(executable):
            return executable
    return "none"


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
    network_state = network or ("available" if socket.gethostname() else "unknown")
    virtualization_state = virtualization or "unknown"
    gpu = "unknown"
    evidence = ["host probe only", "hardened isolation not proven"]

    backends = [ExecutionBackend.NO_TOOL]
    modes = [ProductMode.LITE]
    if engine != "none":
        backends.append(ExecutionBackend.LOCAL_CONTAINER)
        modes.append(ProductMode.LOCAL)
        evidence.append(f"{engine} executable found")
    else:
        evidence.append("no Docker or Podman executable found")

    return CapabilityReport(
        os_name=os_name,
        architecture=architecture,
        memory_bytes=_memory_bytes(),
        disk_free_bytes=_disk_free_bytes(root),
        gpu=gpu,
        virtualization=virtualization_state,
        container_engine=engine,
        network=network_state,
        ports=port_status,
        backends=tuple(backends),
        modes=tuple(modes),
        evidence=tuple(evidence),
    )


__all__ = [
    "CapabilityReport",
    "ExecutionBackend",
    "PortStatus",
    "ProductMode",
    "check_port",
    "probe_capabilities",
    "suggest_port",
]
