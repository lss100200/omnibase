from __future__ import annotations

import socket
import subprocess

import pytest

from omnibase.runtime.capabilities import (
    EvidenceState,
    ProductMode,
    check_port,
    probe_capabilities,
    probe_network_state,
    suggest_port,
)
from omnibase.runtime.diagnostics import (
    ServiceStatus,
    diagnostics_json,
    diagnostics_payload,
    redact_mapping,
    select_mode,
)


def test_port_conflict_and_suggestion() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen()
    port = server.getsockname()[1]
    try:
        assert check_port(port).available is False
        assert check_port(port).reason == "in_use"
        assert check_port(port).evidence is EvidenceState.DETECTED
        assert suggest_port(port, attempts=2) in {port + 1, None}
    finally:
        server.close()


def test_invalid_port_is_not_applicable() -> None:
    status = check_port(70000)
    assert not status.available
    assert status.evidence is EvidenceState.NOT_APPLICABLE


def test_capability_probe_keeps_hardened_unproven(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("omnibase.runtime.capabilities._container_engine", lambda: "none")
    monkeypatch.setattr(
        "omnibase.runtime.capabilities._probe_gpu",
        lambda: ("unknown", EvidenceState.UNKNOWN, ("no gpu",)),
    )
    report = probe_capabilities(ports=(), root=tmp_path)
    assert report.supports(ProductMode.LITE)
    assert not report.supports(ProductMode.LOCAL)
    assert not report.supports(ProductMode.HARDENED)
    assert "hardened isolation not proven" in report.evidence
    fact_names = {fact[0] for fact in report.facts}
    assert "hardened_isolation" in fact_names


@pytest.mark.parametrize(
    ("case_name", "paths", "expected"),
    [
        ("docker-only", {"docker": "/usr/bin/docker", "podman": None}, "docker"),
        ("podman-only", {"docker": None, "podman": "/usr/bin/podman"}, "podman"),
        ("both-present", {"docker": "/usr/bin/docker", "podman": "/usr/bin/podman"}, "docker"),
        ("neither-present", {"docker": None, "podman": None}, "none"),
    ],
)
def test_shared_container_engine_resolution_four_cases(
    monkeypatch, case_name: str, paths: dict[str, str | None], expected: str
) -> None:
    # The probe and the lifecycle MUST share one resolution contract. Docker
    # wins over Podman when both exist; absence yields "none" so Local is
    # never claimed without an executable Compose path.
    from omnibase.runtime import capabilities as caps

    monkeypatch.setattr(caps.shutil, "which", lambda name, paths=paths: paths.get(name))
    assert caps.resolve_container_engine() == expected


def test_local_mode_claim_matches_shared_engine_resolution(monkeypatch, tmp_path) -> None:
    # Podman-only: Local IS claimed because the lifecycle has a real controlled
    # Podman Compose path (verified in test_runtime_lifecycle).
    from omnibase.runtime import capabilities as caps

    monkeypatch.setattr(
        caps.shutil, "which", lambda name: "/usr/bin/podman" if name == "podman" else None
    )
    monkeypatch.setattr(
        caps, "_probe_nvidia_gpu", lambda: ("unknown", EvidenceState.UNKNOWN, ())
    )
    report = caps.probe_capabilities(ports=(), root=tmp_path)
    assert report.container_engine == "podman"
    assert report.supports(ProductMode.LOCAL)
    assert caps.ExecutionBackend.LOCAL_CONTAINER in report.backends

    # Neither present: Local is NOT claimed; only the no-tool backend remains.
    monkeypatch.setattr(caps.shutil, "which", lambda _name: None)
    report2 = caps.probe_capabilities(ports=(), root=tmp_path)
    assert report2.container_engine == "none"
    assert not report2.supports(ProductMode.LOCAL)
    assert report2.backends == (caps.ExecutionBackend.NO_TOOL,)


def test_mode_selection_rejects_unproven_local() -> None:
    report = probe_capabilities(ports=(), network="unknown", virtualization="unknown")
    with pytest.raises(ValueError, match="mode_not_available:hardened"):
        select_mode(report, ProductMode.HARDENED)


def test_network_never_inferred_from_hostname() -> None:
    # A hostname is not network evidence. Default is unknown.
    state, evidence, note = probe_network_state(None)
    assert state == "unknown"
    assert "hostname is not network evidence" in note
    # Caller-supplied closed-set values are honored with configured provenance.
    assert probe_network_state("available")[1] is EvidenceState.CONFIGURED
    # Garbage caller values collapse to unknown, never a positive claim.
    assert probe_network_state("maybe")[0] == "unknown"


def test_gpu_probe_is_bounded_and_does_not_crash(monkeypatch, tmp_path) -> None:
    # No nvidia-smi -> unknown, never a false negative for the whole runtime.
    monkeypatch.setattr(
        "omnibase.runtime.capabilities._probe_nvidia_gpu",
        lambda: ("unknown", EvidenceState.UNKNOWN, ("nvidia-smi not found",)),
    )
    report = probe_capabilities(ports=(), root=tmp_path)
    assert report.gpu == "unknown"

    # Simulate nvidia-smi success -> available with evidence.
    monkeypatch.setattr(
        "omnibase.runtime.capabilities._probe_nvidia_gpu",
        lambda: (
            "nvidia:rtx 5060, 580.0, 8000 mib",
            EvidenceState.AVAILABLE,
            ("nvidia-smi query succeeded",),
        ),
    )
    report2 = probe_capabilities(ports=(), root=tmp_path)
    assert report2.gpu.startswith("nvidia:")
    gpu_fact = next(f for f in report2.facts if f[0] == "gpu")
    assert gpu_fact[1] is EvidenceState.AVAILABLE


def test_nvidia_probe_times_out(monkeypatch) -> None:
    from omnibase.runtime import capabilities as caps

    def _boom(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=3)

    monkeypatch.setattr(caps.shutil, "which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(caps.subprocess, "run", _boom)
    label, state, _notes = caps._probe_nvidia_gpu()
    assert label == "unknown"
    assert state is EvidenceState.UNAVAILABLE


def test_platform_matrix_keeps_unrun_hosts_not_proven(tmp_path) -> None:
    report = probe_capabilities(ports=(), root=tmp_path)
    assert report.platform_matrix
    not_proven = [m for m in report.platform_matrix if m[2] is EvidenceState.NOT_PROVEN]
    detected = [m for m in report.platform_matrix if m[2] is EvidenceState.DETECTED]
    assert not_proven  # other OS/arch/accelerator/container variants stay not_proven
    assert detected  # the current host is detected


def test_diagnostics_redact_secrets_and_status() -> None:
    shape = redact_mapping({"JWT_SECRET": "secret", "nested": {"api_key": "key", "mode": "lite"}})
    assert shape == {
        "JWT_SECRET": "[REDACTED]",
        "nested": {"api_key": "[REDACTED]", "mode": "lite"},
    }
    report = probe_capabilities(ports=())
    payload = diagnostics_payload(
        report,
        [ServiceStatus("backend", "unhealthy", "timeout", 124)],
        config_shape=shape,
    )
    assert payload["services"][0]["exit_code"] == 124
    assert payload["privacy"]["secrets_included"] is False


def test_diagnostics_json_typed_signature_no_untyped_args() -> None:
    report = probe_capabilities(ports=())
    serialized = diagnostics_json(report, config_shape={"token": "abc"})
    assert "[REDACTED]" in serialized
    assert "abc" not in serialized
