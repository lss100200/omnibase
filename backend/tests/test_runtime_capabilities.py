from __future__ import annotations

import socket

import pytest

from omnibase.runtime.capabilities import (
    ProductMode,
    check_port,
    probe_capabilities,
    suggest_port,
)
from omnibase.runtime.diagnostics import (
    ServiceStatus,
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
        assert suggest_port(port, attempts=2) in {port + 1, None}
    finally:
        server.close()


def test_capability_probe_keeps_hardened_unproven(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("omnibase.runtime.capabilities._container_engine", lambda: "none")
    report = probe_capabilities(ports=(), root=tmp_path)
    assert report.supports(ProductMode.LITE)
    assert not report.supports(ProductMode.LOCAL)
    assert not report.supports(ProductMode.HARDENED)
    assert "hardened isolation not proven" in report.evidence


def test_mode_selection_rejects_unproven_local() -> None:
    report = probe_capabilities(ports=(), network="unknown", virtualization="unknown")
    with pytest.raises(ValueError, match="mode_not_available:hardened"):
        select_mode(report, ProductMode.HARDENED)


def test_diagnostics_redact_secrets_and_status() -> None:
    shape = redact_mapping({"JWT_SECRET": "secret", "nested": {"api_key": "key", "mode": "lite"}})
    assert shape == {
        "JWT_SECRET": "[REDACTED]",
        "nested": {"api_key": "[REDACTED]", "mode": "lite"},
    }
    payload = diagnostics_payload(
        probe_capabilities(ports=()),
        [ServiceStatus("backend", "unhealthy", "timeout", 124)],
        config_shape=shape,
    )
    assert payload["services"][0]["exit_code"] == 124
    assert payload["privacy"]["secrets_included"] is False
