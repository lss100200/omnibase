"""Fail-closed contract tests for the P34.7 production Overlay evidence Gate."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPO_ROOT / "scripts" / "overlay"
sys.path.insert(0, str(SCRIPT_DIR))


def _load_gate():
    path = SCRIPT_DIR / "p34_7_production_gate.py"
    spec = importlib.util.spec_from_file_location("p34_7_production_gate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("P34.7 production Overlay Gate could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GATE = _load_gate()
COMMIT = "1" * 40


def _config(*, placeholder: bool = False) -> dict[str, object]:
    return {
        "schema": GATE.CONFIG_SCHEMA,
        "environment": "production",
        "control_plane": {
            "provider": "headscale",
            "headscale_endpoint": "https://headscale.production.invalid",
            "derp_hosts": ["derp-host-c"],
        },
        "member_nodes": [
            {
                "node_id": "node-a",
                "host_id": "host-a",
                "machine_identity_digest": "1" * 64,
                "daemon_identity_digest": "a" * 64,
                "attestation_public_key_file": "C:/secure/node-a-public.pem",
                "attestation_public_key_sha256": "c" * 64,
                "node_daemon_endpoint": "https://node-a.production.invalid",
                "credential_reference": "omnibase-secret://overlay/node-a",
                "roles": ["member", "node_daemon", "runner", "broker"],
                "target_linux": True,
                "sandbox_overlay_peer": False,
                "placeholder": placeholder,
            },
            {
                "node_id": "node-b",
                "host_id": "host-b",
                "machine_identity_digest": "2" * 64,
                "daemon_identity_digest": "b" * 64,
                "attestation_public_key_file": "C:/secure/node-b-public.pem",
                "attestation_public_key_sha256": "d" * 64,
                "node_daemon_endpoint": "https://node-b.production.invalid",
                "credential_reference": "omnibase-secret://overlay/node-b",
                "roles": ["member", "node_daemon"],
                "target_linux": True,
                "sandbox_overlay_peer": False,
                "placeholder": placeholder,
            },
        ],
        "required_fault_scenarios": sorted(GATE.REQUIRED_FAULT_SCENARIOS),
    }


def _gate_result(total: int) -> dict[str, object]:
    return {
        "status": "passed",
        "passed": total,
        "total": total,
        "source_git_commit": COMMIT,
        "source_scope_sha256": "f" * 64,
        "evidence_sha256": "e" * 64,
    }


def _evidence(config: dict[str, object]) -> dict[str, object]:
    members = config["member_nodes"]
    assert isinstance(members, list)
    node_evidence = []
    for member in members:
        assert isinstance(member, dict)
        node_evidence.append(
            {
                "node_id": member["node_id"],
                "host_id": member["host_id"],
                "machine_identity_digest": member["machine_identity_digest"],
                "daemon_identity_digest": member["daemon_identity_digest"],
                "target_linux": True,
                "real_member": True,
                "node_daemon_independent": True,
                "node_daemon_test_double": False,
            }
        )
    return {
        "schema": GATE.EVIDENCE_SCHEMA,
        "run_id": "production-run-1",
        "environment": "production",
        "disposable": False,
        "source_git_commit": COMMIT,
        "source_git_dirty": False,
        "source_scope_sha256": "f" * 64,
        "topology_sha256": GATE.sha256_bytes(GATE.canonical_bytes(config)),
        "signatures": [],
        "member_nodes": node_evidence,
        "gates": {
            "runner_a4_current_source": _gate_result(12),
            "broker_rounds": [_gate_result(26), _gate_result(26)],
            "member_data_plane": {
                "status": "passed",
                "real_two_member_path": True,
                "sandbox_is_overlay_member": False,
                "direct_infrastructure_routes": [],
                "logical_service_only": True,
            },
            "derp": {
                "status": "passed",
                "forced_relay": True,
                "direct_path_disabled": True,
            },
            "node_compromise": {
                "status": "passed",
                "revoked_node_rejected": True,
                "stolen_credential_rejected": True,
                "stale_lease_rejected": True,
                "stale_fencing_rejected": True,
                "ambiguous_operation_replayed": False,
                "rejoin_uses_new_identity": True,
            },
            "fault_scenarios": sorted(GATE.REQUIRED_FAULT_SCENARIOS),
            "cleanup": {"containers": 0, "networks": 0, "processes": 0, "volumes": 0},
            "secret_scan": {"findings": []},
        },
    }


def _sla_report(*, passed: bool = True) -> dict[str, object]:
    return {
        "schema": GATE.SLA_REPORT_SCHEMA,
        "production_sla_passed": passed,
        "vetoes": [],
    }


def test_example_topology_is_valid_but_cannot_prove_production() -> None:
    example = json.loads(
        (REPO_ROOT / "deployment/overlay/production/topology.example.json").read_text(
            encoding="utf-8"
        )
    )
    GATE.validate_config(example)
    report = GATE.validation_only_report(
        config=example,
        current_commit=COMMIT,
        source_git_dirty=True,
        source_scope={"file_count": 42, "source_scope_sha256": "f" * 64},
    )
    assert report["configuration_valid"] is True
    assert report["status"] == "blocked/not_proven"
    assert report["placeholder_member_node_count"] == 2
    assert report["source_git_dirty"] is True
    assert not report["production_overlay_gate_passed"]


def test_independent_member_and_derp_hosts_are_required() -> None:
    config = _config()
    members = config["member_nodes"]
    assert isinstance(members, list)
    assert isinstance(members[1], dict)
    members[1]["host_id"] = "host-a"
    with pytest.raises(GATE.ProductionGateError, match="independent hosts"):
        GATE.validate_config(config)

    config = _config()
    control_plane = config["control_plane"]
    assert isinstance(control_plane, dict)
    control_plane["derp_hosts"] = ["host-a"]
    with pytest.raises(GATE.ProductionGateError, match="independently hosted"):
        GATE.validate_config(config)


def test_exact_current_source_standards_and_safe_evidence_can_pass() -> None:
    config = GATE.validate_config(_config())
    report = GATE.evaluate_evidence(
        config=config,
        evidence=_evidence(config),
        sla_report=_sla_report(),
        current_commit=COMMIT,
        current_source_scope_sha256="f" * 64,
    )
    assert report["status"] == "passed"
    assert report["current_source_a4_result"] == "12/12"
    assert report["network_broker_results"] == ["26/26", "26/26"]
    assert report["real_member_node_count"] == 2
    assert report["vetoes"] == []


def test_placeholder_and_weakened_historical_gate_cannot_pass() -> None:
    placeholder_config = GATE.validate_config(_config(placeholder=True))
    with pytest.raises(GATE.ProductionGateError, match="placeholder"):
        GATE.evaluate_evidence(
            config=placeholder_config,
            evidence=_evidence(placeholder_config),
            sla_report=_sla_report(),
            current_commit=COMMIT,
            current_source_scope_sha256="f" * 64,
        )

    config = GATE.validate_config(_config())
    evidence = _evidence(config)
    gates = evidence["gates"]
    assert isinstance(gates, dict)
    gates["runner_a4_current_source"] = _gate_result(11)
    with pytest.raises(GATE.ProductionGateError, match="passed count drifted"):
        GATE.evaluate_evidence(
            config=config,
            evidence=evidence,
            sla_report=_sla_report(),
            current_commit=COMMIT,
            current_source_scope_sha256="f" * 64,
        )


def test_compromised_identity_and_direct_infrastructure_are_vetoes() -> None:
    config = GATE.validate_config(_config())
    evidence = _evidence(config)
    unsafe = copy.deepcopy(evidence)
    gates = unsafe["gates"]
    assert isinstance(gates, dict)
    member_data_plane = gates["member_data_plane"]
    node_compromise = gates["node_compromise"]
    assert isinstance(member_data_plane, dict)
    assert isinstance(node_compromise, dict)
    member_data_plane["direct_infrastructure_routes"] = ["postgresql"]
    node_compromise["stolen_credential_rejected"] = False
    report = GATE.evaluate_evidence(
        config=config,
        evidence=unsafe,
        sla_report=_sla_report(),
        current_commit=COMMIT,
        current_source_scope_sha256="f" * 64,
    )
    assert report["status"] == "veto"
    assert "direct_infrastructure_route_present" in report["vetoes"]
    assert "stolen_credential_still_accepted" in report["vetoes"]
    assert not report["production_overlay_gate_passed"]


def test_sla_failure_remains_blocked_not_proven() -> None:
    config = GATE.validate_config(_config())
    report = GATE.evaluate_evidence(
        config=config,
        evidence=_evidence(config),
        sla_report=_sla_report(passed=False),
        current_commit=COMMIT,
        current_source_scope_sha256="f" * 64,
    )
    assert report["status"] == "blocked/not_proven"
    assert report["blockers"] == ["production_capacity_sla_not_proven"]


def test_both_member_signatures_bind_the_exact_payload(tmp_path, monkeypatch) -> None:
    config = GATE.validate_config(_config())
    evidence = _evidence(config)
    public_a = tmp_path / "node-a-public.pem"
    public_b = tmp_path / "node-b-public.pem"
    signature_a = tmp_path / "node-a.sig"
    signature_b = tmp_path / "node-b.sig"
    public_a.write_bytes(b"public-a")
    public_b.write_bytes(b"public-b")
    signature_a.write_bytes(b"signature-a")
    signature_b.write_bytes(b"signature-b")
    members = config["member_nodes"]
    members[0]["attestation_public_key_file"] = str(public_a)
    members[0]["attestation_public_key_sha256"] = GATE.sha256_file(public_a)
    members[1]["attestation_public_key_file"] = str(public_b)
    members[1]["attestation_public_key_sha256"] = GATE.sha256_file(public_b)
    evidence["topology_sha256"] = GATE.sha256_bytes(GATE.canonical_bytes(config))
    unsigned = dict(evidence)
    unsigned.pop("signatures")
    payload_sha256 = GATE.sha256_bytes(GATE.canonical_bytes(unsigned))
    evidence["signatures"] = [
        {
            "node_id": "node-a",
            "algorithm": "ed25519",
            "signature_file": str(signature_a),
            "signature_file_sha256": GATE.sha256_file(signature_a),
            "signed_payload_sha256": payload_sha256,
        },
        {
            "node_id": "node-b",
            "algorithm": "ed25519",
            "signature_file": str(signature_b),
            "signature_file_sha256": GATE.sha256_file(signature_b),
            "signed_payload_sha256": payload_sha256,
        },
    ]
    calls = []

    def fake_run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(GATE.subprocess, "run", fake_run)
    GATE.verify_member_signatures(config=config, evidence=evidence)
    assert len(calls) == 2
    assert all(call[0][0:3] == ["openssl", "pkeyutl", "-verify"] for call in calls)

    evidence["signatures"][0]["signed_payload_sha256"] = "0" * 64
    with pytest.raises(GATE.ProductionGateError, match="payload binding drifted"):
        GATE.verify_member_signatures(config=config, evidence=evidence)
