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
    from omnibase.runtime import capabilities as caps

    monkeypatch.setattr(caps, "_probe_engine_resolution", lambda: caps.EngineResolution("none", ()))
    monkeypatch.setattr(caps, "_probe_gpu", lambda: ("unknown", EvidenceState.UNKNOWN, ("no gpu",)))
    report = probe_capabilities(ports=(), root=tmp_path)
    assert report.supports(ProductMode.LITE)
    assert not report.supports(ProductMode.LOCAL)
    assert not report.supports(ProductMode.HARDENED)
    assert "hardened isolation not proven" in report.evidence
    fact_names = {fact[0] for fact in report.facts}
    assert "hardened_isolation" in fact_names


def _compose_probe(
    executable: str,
    *,
    detected: bool = True,
    verified: bool = False,
    exit_code: int | None = None,
    timed_out: bool = False,
) -> object:
    from omnibase.runtime import capabilities as caps

    if verified:
        detail = f"{executable} compose version exit 0"
    elif timed_out:
        detail = f"{executable} compose version probe timed out"
    elif exit_code is not None:
        detail = f"{executable} compose version exit {exit_code} (not verified)"
    else:
        detail = f"{executable} compose provider not verified (not_proven)"
    return caps.ComposeProbe(
        executable=executable,
        executable_detected=detected,
        compose_provider_verified=verified,
        exit_code=exit_code,
        timed_out=timed_out,
        detail=detail,
    )


def _patch_probes(monkeypatch: pytest.MonkeyPatch, docker: object, podman: object) -> None:
    from omnibase.runtime import capabilities as caps

    monkeypatch.setattr(caps, "_probe_compose", lambda name: docker if name == "docker" else podman)


@pytest.mark.parametrize(
    ("case_name", "docker", "podman", "expected"),
    [
        (
            "docker-only",
            _compose_probe("docker", verified=True, exit_code=0),
            _compose_probe("podman", detected=False),
            "docker",
        ),
        (
            "podman-only",
            _compose_probe("docker", detected=False),
            _compose_probe("podman", verified=True, exit_code=0),
            "podman",
        ),
        (
            "both-present-compose-verified",
            _compose_probe("docker", verified=True, exit_code=0),
            _compose_probe("podman", verified=True, exit_code=0),
            "docker",
        ),
        (
            "both-present-compose-fails",
            _compose_probe("docker", verified=False, exit_code=1),
            _compose_probe("podman", verified=False, exit_code=1),
            "none",
        ),
        (
            "timeout",
            _compose_probe("docker", verified=False, timed_out=True),
            _compose_probe("podman", detected=False),
            "none",
        ),
        (
            "not-found",
            _compose_probe("docker", detected=False),
            _compose_probe("podman", detected=False),
            "none",
        ),
        (
            "neither-present",
            _compose_probe("docker", detected=False),
            _compose_probe("podman", detected=False),
            "none",
        ),
    ],
)
def test_shared_engine_resolution_bounded_probe_matrix(
    monkeypatch: pytest.MonkeyPatch,
    case_name: str,
    docker: object,
    podman: object,
    expected: str,
) -> None:
    # Compose Local capability is NEVER inferred from shutil.which alone: only
    # an exit-0 bounded `docker compose version` / `podman compose version`
    # probe declares the compose provider verified. Timeout, not-found,
    # compose failure and neither-present all resolve to "none".
    from omnibase.runtime import capabilities as caps

    _patch_probes(monkeypatch, docker, podman)
    assert caps.resolve_container_engine() == expected


def test_local_mode_claim_requires_verified_compose_provider(monkeypatch, tmp_path) -> None:
    from omnibase.runtime import capabilities as caps

    # Docker verified -> Local claimed on the probe side.
    _patch_probes(
        monkeypatch,
        _compose_probe("docker", verified=True, exit_code=0),
        _compose_probe("podman", detected=False),
    )
    monkeypatch.setattr(caps, "_probe_gpu", lambda: ("unknown", EvidenceState.UNKNOWN, ()))
    report = caps.probe_capabilities(ports=(), root=tmp_path)
    assert report.container_engine == "docker"
    assert report.supports(caps.ProductMode.LOCAL)
    assert caps.ExecutionBackend.LOCAL_CONTAINER in report.backends

    # Podman verified -> Local claimed through the controlled Podman path.
    _patch_probes(
        monkeypatch,
        _compose_probe("docker", detected=False),
        _compose_probe("podman", verified=True, exit_code=0),
    )
    report2 = caps.probe_capabilities(ports=(), root=tmp_path)
    assert report2.container_engine == "podman"
    assert report2.supports(caps.ProductMode.LOCAL)

    # Neither present -> Local is NOT claimed; only the no-tool backend remains.
    _patch_probes(
        monkeypatch,
        _compose_probe("docker", detected=False),
        _compose_probe("podman", detected=False),
    )
    report3 = caps.probe_capabilities(ports=(), root=tmp_path)
    assert report3.container_engine == "none"
    assert not report3.supports(caps.ProductMode.LOCAL)
    assert report3.backends == (caps.ExecutionBackend.NO_TOOL,)


def test_executable_detected_without_verified_provider_is_not_proven(monkeypatch, tmp_path) -> None:
    # Podman executable is present but its compose provider is missing: the
    # report distinguishes executable_detected (detected) from
    # compose_provider_verified / local_mode_available (not_proven) and never
    # claims Local.
    from omnibase.runtime import capabilities as caps

    _patch_probes(
        monkeypatch,
        _compose_probe("docker", detected=False),
        _compose_probe("podman", verified=False, exit_code=1),
    )
    monkeypatch.setattr(caps, "_probe_gpu", lambda: ("unknown", EvidenceState.UNKNOWN, ()))
    report = caps.probe_capabilities(ports=(), root=tmp_path)
    facts = {fact[0]: fact[1] for fact in report.facts}
    assert report.container_engine == "none"
    assert not report.supports(caps.ProductMode.LOCAL)
    assert facts["podman_executable"] is EvidenceState.DETECTED
    assert facts["podman_compose_provider"] is EvidenceState.NOT_PROVEN
    assert facts["compose_provider_verified"] is EvidenceState.NOT_PROVEN
    assert facts["local_mode_available"] is EvidenceState.NOT_PROVEN
    assert any(
        "not verified" in fact[2] for fact in report.facts if fact[0] == "podman_compose_provider"
    )
    assert any("not_proven" in item for item in report.evidence)


def test_compose_probe_timeout_keeps_local_not_proven(monkeypatch, tmp_path) -> None:
    from omnibase.runtime import capabilities as caps

    _patch_probes(
        monkeypatch,
        _compose_probe("docker", verified=False, timed_out=True),
        _compose_probe("podman", detected=False),
    )
    monkeypatch.setattr(caps, "_probe_gpu", lambda: ("unknown", EvidenceState.UNKNOWN, ()))
    report = caps.probe_capabilities(ports=(), root=tmp_path)
    facts = {fact[0]: fact[1] for fact in report.facts}
    assert report.container_engine == "none"
    assert not report.supports(caps.ProductMode.LOCAL)
    assert facts["docker_executable"] is EvidenceState.DETECTED
    assert facts["docker_compose_provider"] is EvidenceState.NOT_PROVEN
    assert facts["local_mode_available"] is EvidenceState.NOT_PROVEN


def test_probe_uses_bounded_shell_false_capture_output_run(monkeypatch) -> None:
    # The bounded probe must pass an argument array with shell=False and
    # capture_output=True; only exit 0 declares the provider verified.
    from omnibase.runtime import capabilities as caps

    calls: list[dict[str, object]] = []

    def _fake_run(command: object, **kwargs: object) -> object:
        calls.append({"command": command, **kwargs})
        return type("Done", (), {"returncode": 0})()

    monkeypatch.setattr(caps.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(caps.subprocess, "run", _fake_run)
    result = caps._probe_compose("docker")
    assert result.executable_detected is True
    assert result.compose_provider_verified is True
    assert result.exit_code == 0
    assert calls[0]["command"] == ["/usr/bin/docker", "compose", "version"]
    assert calls[0]["shell"] is False
    assert calls[0]["capture_output"] is True
    assert calls[0]["check"] is False
    assert calls[0]["timeout"] == caps.COMPOSE_PROBE_TIMEOUT


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
