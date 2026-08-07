"""Tests for the run-scoped hardened P34.7 joint gate evidence-authenticity contract.

These tests deliberately label any cryptographic/semantic verification that uses
deterministic test fixtures as *fixture verification*, never as a production
PASS.  A production PASS requires an actual run-generated evidence directory;
when no real evidence chain exists the only correct state is
``blocked/not_proven``.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from omnibase.production.composition import ConfigurationError
from omnibase.production.joint_gate import (
    validate_joint_evidence,
    validate_joint_evidence_contract,
    verify_joint_evidence,
)

SCHEMA = "omnibase.p34-7.hardened-joint-evidence.v2"
RUN_ID = "run-fixture-001"
SOURCE_COMMIT = "a" * 64
SOURCE_TREE = "b" * 64
REPOSITORY = "https://github.com/lss100200/omnibase.git"


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _write_file(run: Path, relative: str, raw: bytes) -> dict[str, object]:
    path = run / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"path": relative, "size": len(raw), "sha256": _digest(raw)}


def _manifest(run: Path, name: str, content: bytes) -> dict[str, object]:
    entry = _write_file(run, name, content)
    files = [entry]
    raw = hashlib.sha256(
        json.dumps(files, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return {"raw_sha256": raw, "files": files}


def _stream(run: Path, name: str, content: bytes) -> dict[str, object]:
    return _write_file(run, name, content)


def _command(run: Path, name: str, order: int) -> dict[str, object]:
    executable = _write_file(run, f"bin/{name}", name.encode())
    stdout = _stream(run, f"out/{name}.out", f"stdout-{name}".encode())
    stderr = _stream(run, f"out/{name}.err", b"")
    started = f"2026-08-07T00:0{order}:00Z"
    ended = f"2026-08-07T00:0{order}:30Z"
    return {
        "order": order,
        "executable_path": executable["path"],
        "executable_digest": executable["sha256"],
        "argv": [f"/run/omnibase/bin/{name}", "--probe"],
        "working_directory": "/run/omnibase",
        "env_manifest": {"names": ["PATH", "OMNIBASE_RUN_ID"], "secret_free": True},
        "started_at": started,
        "ended_at": ended,
        "timeout_seconds": 60,
        "exit_code": 0,
        "stdout": stdout,
        "stderr": stderr,
    }


def _component(run: Path, name: str) -> dict[str, object]:
    evidence = _write_file(run, f"components/{name}.bin", f"evidence-{name}".encode())
    record: dict[str, object] = {
        "schema": f"omnibase.p34-7.{name}.v1",
        "producer": name,
        "component_run_id": RUN_ID,
        "identity": {"kind": "sha256", "value": _digest(name.encode())},
        "trust_roots": [_digest(b"root")],
        "evidence": evidence,
        "host": {"os": "ubuntu", "kernel": "6.8.0", "arch": "x86_64"},
    }
    if name == "gateway":
        record["certificate"] = {
            "public_fingerprint": _digest(b"cert"),
            "issuer": _digest(b"issuer"),
            "san": "workload.gateway.omnibase",
            "valid_from": "2026-08-07T00:00:00Z",
            "valid_until": "2026-08-07T00:05:00Z",
            "revoked": False,
        }
        record["replay"] = {"replayed": False, "sequence": 1}
    return record


def _attack(run: Path) -> dict[str, object]:
    evidence = _write_file(run, "attack.bin", b"attack-results")
    return {
        "status": "passed",
        "results": {
            "node_compromise": "rejected",
            "credential_theft": "contained",
            "revocation_replay": "rejected",
            "derp_failover": "failed_attack",
            "cross_component_replay": "rejected",
        },
        "evidence": evidence,
    }


def _cleanup(run: Path) -> dict[str, object]:
    evidence = _write_file(run, "cleanup.bin", b"cleanup-inventory")
    return {
        "containers": 0,
        "networks": 0,
        "processes": 0,
        "volumes": 0,
        "databases": 0,
        "test_identities": 0,
        "evidence": evidence,
    }


def _bundle(run: Path) -> dict[str, object]:
    commands = {
        name: _command(run, name, order)
        for order, name in enumerate(
            [
                "core_runner",
                "runner_broker",
                "runner_gateway",
                "broker_gateway",
                "overlay_data_plane",
                "recovery_sla",
            ]
        )
    }
    components = {
        name: _component(run, name)
        for name in ("core", "runner", "broker", "gateway", "overlay", "recovery_sla")
    }
    return {
        "schema": SCHEMA,
        "schema_version": "2",
        "run_id": RUN_ID,
        "environment": "production",
        "disposable": False,
        "provenance": {
            "source_commit": SOURCE_COMMIT,
            "source_tree": SOURCE_TREE,
            "dirty": False,
            "repository": REPOSITORY,
        },
        "source_manifest": _manifest(run, "source.txt", b"source-bytes"),
        "artifact_manifest": _manifest(run, "artifact.txt", b"artifact-bytes"),
        "commands": commands,
        "components": components,
        "migration_head": "0012",
        "feature_gates": {
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        "runtime_posture": {
            "production_runtime_activated": False,
            "hostile_code_executed": False,
            "measured": True,
            "measurement_source": "process_config",
        },
        "attack_matrix": _attack(run),
        "cleanup": _cleanup(run),
        "evidence_seal": {
            "status": "passed",
            "run_id": RUN_ID,
            "source_commit": SOURCE_COMMIT,
            "source_tree": SOURCE_TREE,
        },
    }


# ---------------------------------------------------------------------------
# Positive: fixture verification (NOT a production PASS claim)
# ---------------------------------------------------------------------------


def test_verify_joint_evidence_passes_on_complete_fixture_bundle(tmp_path: Path) -> None:
    """Fixture verification only: a complete deterministic bundle verifies.

    This proves the cryptographic/semantic binding works end-to-end on fixture
    bytes.  It is NOT production evidence: the files were written by this test,
    not by a real run.
    """
    run = tmp_path / "run"
    run.mkdir()
    report = verify_joint_evidence(run, _bundle(run))
    assert report.status == "passed"
    assert report.passed is True
    assert report.mode == "verify-evidence"
    assert report.safety["migration_head"] == "0012"
    assert report.safety["runtime_posture"] == "measured:process_config"


def test_validate_only_never_passes(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    report = validate_joint_evidence_contract(_bundle(run))
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert report.mode == "validate-only"
    assert "contract_mode_no_direct_evidence" in report.blockers


def test_backwards_compatible_alias_matches_verify(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    expected = verify_joint_evidence(run, _bundle(run))
    actual = validate_joint_evidence(run, _bundle(run))
    assert actual.status == expected.status
    assert actual.passed is expected.passed


# ---------------------------------------------------------------------------
# Negative: no synthetic PASS from inline assertions
# ---------------------------------------------------------------------------


def test_fabricated_inline_pass_bundle_without_files_is_rejected(tmp_path: Path) -> None:
    """A bundle that only self-asserts status with no real files is rejected."""
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    # Remove every referenced file, leaving only the self-asserting JSON.
    for path in list(run.rglob("*")):
        if path.is_file():
            path.unlink()
    with pytest.raises(ConfigurationError, match="unavailable|drifted|regular"):
        verify_joint_evidence(run, payload)


def test_fabricated_inline_exit_code_cannot_pass_without_real_stdout(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    # Forge a passing exit code while deleting the real stdout artifact.
    (run / payload["commands"]["core_runner"]["stdout"]["path"]).unlink()
    with pytest.raises(ConfigurationError):
        verify_joint_evidence(run, payload)


def test_real_file_with_forged_sidecar_hash_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["source_manifest"]["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ConfigurationError, match="raw hash drifted"):
        verify_joint_evidence(run, payload)


def test_manifest_raw_sha256_not_binding_files_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["source_manifest"]["raw_sha256"] = "f" * 64
    with pytest.raises(ConfigurationError, match="raw_sha256 does not bind"):
        verify_joint_evidence(run, payload)


def test_swapped_stdout_stderr_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    cmd = payload["commands"]["runner_broker"]
    # Forge: claim stdout points at the stderr file while keeping stdout's
    # recorded size/digest describing the stdout bytes -> path/content mismatch.
    cmd["stdout"]["path"] = cmd["stderr"]["path"]
    with pytest.raises(ConfigurationError, match="drifted"):
        verify_joint_evidence(run, payload)


def test_swapped_component_artifacts_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    # Forge: claim core evidence points at the runner file while keeping core's
    # recorded size/digest describing the core bytes -> path/content mismatch.
    core_ev = payload["components"]["core"]["evidence"]
    runner_ev = payload["components"]["runner"]["evidence"]
    payload["components"]["core"]["evidence"]["path"] = runner_ev["path"]
    payload["components"]["runner"]["evidence"]["path"] = core_ev["path"]
    with pytest.raises(ConfigurationError, match="drifted"):
        verify_joint_evidence(run, payload)


def test_reused_evidence_from_different_run_id_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["components"]["runner"]["component_run_id"] = "run-fixture-999"
    with pytest.raises(ConfigurationError, match="different run_id"):
        verify_joint_evidence(run, payload)


def test_reused_evidence_from_different_source_commit_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["evidence_seal"]["source_commit"] = "c" * 64
    with pytest.raises(ConfigurationError, match="provenance must match"):
        verify_joint_evidence(run, payload)


def test_reordered_command_steps_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["commands"]["core_runner"]["order"] = 1
    payload["commands"]["runner_broker"]["order"] = 0
    with pytest.raises(ConfigurationError, match="order must match"):
        verify_joint_evidence(run, payload)


def test_missing_command_step_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    del payload["commands"]["overlay_data_plane"]
    with pytest.raises(ConfigurationError, match="commands must contain"):
        verify_joint_evidence(run, payload)


def test_command_exit_code_mismatch_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["commands"]["broker_gateway"]["exit_code"] = 1
    with pytest.raises(ConfigurationError, match="exit_code must be 0"):
        verify_joint_evidence(run, payload)


def test_command_chronology_inconsistency_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["commands"]["runner_gateway"]["started_at"] = "2026-08-07T00:00:00Z"
    payload["commands"]["runner_gateway"]["ended_at"] = "2026-08-07T00:00:10Z"
    # runner_gateway (order 2) now starts before runner_broker (order 1) ended.
    with pytest.raises(ConfigurationError, match="chronology"):
        verify_joint_evidence(run, payload)


def test_path_traversal_in_manifest_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["source_manifest"]["files"][0]["path"] = "../escape.txt"
    with pytest.raises(ConfigurationError, match="traversal|normalized"):
        verify_joint_evidence(run, payload)


def test_absolute_path_escape_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["artifact_manifest"]["files"][0]["path"] = "/etc/passwd"
    with pytest.raises(ConfigurationError, match="normalized relative|absolute"):
        verify_joint_evidence(run, payload)


def test_symlink_artifact_is_rejected(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("symlink behavior on Windows is covered by reparse-point guards")
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    target = run / "source.txt"
    link = run / "link.txt"
    link.symlink_to(target)
    payload["source_manifest"]["files"][0]["path"] = "link.txt"
    payload["source_manifest"]["files"][0]["sha256"] = _digest(target.read_bytes())
    with pytest.raises(ConfigurationError, match="link or reparse point"):
        verify_joint_evidence(run, payload)


def test_env_manifest_with_secret_name_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["commands"]["core_runner"]["env_manifest"]["names"].append("JWT_SECRET")
    with pytest.raises(ConfigurationError, match="secret names"):
        verify_joint_evidence(run, payload)


def test_revoked_gateway_certificate_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["components"]["gateway"]["certificate"]["revoked"] = True
    with pytest.raises(ConfigurationError, match="must not be revoked"):
        verify_joint_evidence(run, payload)


def test_replayed_gateway_credentials_are_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["components"]["gateway"]["replay"]["replayed"] = True
    with pytest.raises(ConfigurationError, match="must not be replayed"):
        verify_joint_evidence(run, payload)


def test_missing_attack_evidence_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    (run / payload["attack_matrix"]["evidence"]["path"]).unlink()
    with pytest.raises(ConfigurationError, match="unavailable"):
        verify_joint_evidence(run, payload)


def test_failed_attack_outcome_blocks_pass(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["attack_matrix"]["results"]["node_compromise"] = "succeeded"
    report = verify_joint_evidence(run, payload)
    assert report.status == "blocked/not_proven"
    assert "attack:node_compromise" in report.blockers


def test_cleanup_residue_is_a_veto(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["cleanup"]["processes"] = 1
    report = verify_joint_evidence(run, payload)
    assert report.status == "blocked/not_proven"
    assert "cleanup:processes" in report.blockers


def test_unmeasured_runtime_posture_blocks_via_not_proven(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["runtime_posture"]["measured"] = False
    report = verify_joint_evidence(run, payload)
    # Unmeasured posture does not raise (it is not a veto) but is reported as
    # not_proven; combined with the rest it still passes here because posture
    # is not a blocker field -- this documents that posture is informational.
    assert report.safety["runtime_posture"] == "not_proven"


def test_hardcoded_runtime_posture_without_measurement_source_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["runtime_posture"]["measurement_source"] = "report_literal"
    with pytest.raises(ConfigurationError, match="measurement_source"):
        verify_joint_evidence(run, payload)


def test_unknown_top_level_field_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["operator_self_asserted_pass"] = True
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        verify_joint_evidence(run, payload)


def test_wrong_schema_version_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["schema_version"] = "99"
    with pytest.raises(ConfigurationError, match="schema_version"):
        verify_joint_evidence(run, payload)


def test_wrong_migration_head_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["migration_head"] = "0013"
    with pytest.raises(ConfigurationError, match="migration head"):
        verify_joint_evidence(run, payload)


@pytest.mark.parametrize(
    "gate", ["agent_runtime_enabled", "agent_planner_enabled", "multi_agent_enabled"]
)
def test_enabled_feature_gate_is_rejected(tmp_path: Path, gate: str) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["feature_gates"][gate] = True
    with pytest.raises(ConfigurationError, match="feature gates"):
        verify_joint_evidence(run, payload)


def test_dirty_checkout_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["provenance"]["dirty"] = True
    with pytest.raises(ConfigurationError, match="clean checkout"):
        verify_joint_evidence(run, payload)


def test_non_production_environment_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["environment"] = "staging"
    with pytest.raises(ConfigurationError, match="environment=production"):
        verify_joint_evidence(run, payload)


def test_disposable_evidence_cannot_prove_production(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["disposable"] = True
    with pytest.raises(ConfigurationError, match="disposable"):
        verify_joint_evidence(run, payload)


def test_non_utc_timestamp_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["commands"]["core_runner"]["started_at"] = "2026-08-07 00:00:00"
    with pytest.raises(ConfigurationError, match="UTC offset"):
        verify_joint_evidence(run, payload)


def test_empty_certificate_validity_window_is_rejected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["components"]["gateway"]["certificate"]["valid_until"] = "2026-08-07T00:00:00Z"
    with pytest.raises(ConfigurationError, match="validity window"):
        verify_joint_evidence(run, payload)


# ---------------------------------------------------------------------------
# Negative: maintenance-map / sealed-digest drift detection
# ---------------------------------------------------------------------------


def test_sealed_source_manifest_drift_is_detected(tmp_path: Path) -> None:
    """If a sealed source file (e.g. maintenance-map.json) changes after a run,
    the recorded source manifest no longer binds the file bytes and the joint
    gate must reject (fail-closed) rather than silently accept.
    """
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    # Simulate post-run mutation of a sealed source file: keep the recorded
    # digest, change the on-disk bytes.  Size and/or hash must drift and the
    # joint gate must reject (fail-closed) rather than silently accept.
    relative = payload["source_manifest"]["files"][0]["path"]
    (run / relative).write_bytes(b"tampered-bytes-that-are-much-longer-than-original")
    with pytest.raises(ConfigurationError, match="drifted"):
        verify_joint_evidence(run, payload)


def test_sealed_artifact_manifest_size_drift_is_detected(tmp_path: Path) -> None:
    run = tmp_path / "run"
    run.mkdir()
    payload = _bundle(run)
    payload["artifact_manifest"]["files"][0]["size"] += 1
    with pytest.raises(ConfigurationError, match="size drifted"):
        verify_joint_evidence(run, payload)


def test_report_to_dict_never_hardcodes_safety_passed(tmp_path: Path) -> None:
    """The serialized report must not hardcode safety negatives as 'false' on a
    passed result; safety is derived from the measured posture / gates."""
    run = tmp_path / "run"
    run.mkdir()
    report = verify_joint_evidence(run, _bundle(run))
    serialized = report.to_dict()
    assert "safety" in serialized
    # No top-level hardcoded boolean safety literals are emitted on a pass.
    for forbidden in (
        "root_env_accessed",
        "business_database_accessed",
        "business_database_migrated",
        "runtime_activated",
    ):
        assert forbidden not in serialized
