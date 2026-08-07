"""Tests for the trust-anchored hardened P34.7 joint gate.

The joint gate must never return ``passed`` for a self-forged bundle, even
when every hash matches.  The tests here use the adversarial forger
``scripts/production/forge_p34_7_evidence_bundle.py`` to fabricate complete
bundles from scratch (files, manifests, cross-bindings and hashes) and then
assert that every authenticity gap keeps the report ``blocked/not_proven``.

A production PASS additionally requires an independently approved trust policy
(pinned in ``joint_gate._APPROVED_TRUST_POLICY_SHA256``, currently empty), so
no fixture in this suite may ever receive ``passed``; the strongest fixture
outcome is ``blocked/not_proven`` with only the approval blocker remaining.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from omnibase.production.composition import ConfigurationError
from omnibase.production.joint_gate import (
    validate_joint_evidence,
    validate_joint_evidence_contract,
    verify_joint_evidence,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
_FORGER_PATH = REPO_ROOT / "scripts" / "production" / "forge_p34_7_evidence_bundle.py"

_spec = importlib.util.spec_from_file_location("forge_p34_7_evidence_bundle", _FORGER_PATH)
assert _spec is not None
assert _spec.loader is not None
forge = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(forge)

_canonical = forge._canonical
_digest = forge._digest
REPOSITORY = "https://github.com/lss100200/omnibase.git"
COMMIT = "a" * 64
TREE = "b" * 64


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_canonical(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _policy_dict(
    run: Path,
    payload: dict[str, object],
    keys: dict[str, dict[str, str]],
    *,
    approved_commits: tuple[str, ...] = (),
    approved_trees: tuple[str, ...] = (),
    argv_overrides: dict[str, object] | None = None,
    executable_overrides: dict[str, object] | None = None,
    allowed_env_names: tuple[str, ...] = ("PATH", "OMNIBASE_RUN_ID"),
) -> dict[str, object]:
    executables: dict[str, object] = {}
    commands: dict[str, object] = {}
    for name, refs in payload["commands"].items():  # type: ignore[union-attr]
        receipt = json.loads((run / str(refs["receipt"]["path"])).read_text())
        exe = receipt["executable"]
        executables[exe["path"]] = {"sha256": exe["sha256"], "commands": [name]}
        commands[name] = receipt["argv"]
    if executable_overrides:
        executables.update(executable_overrides)
    if argv_overrides:
        commands.update(argv_overrides)
    gateway = json.loads((run / "components/gateway.json").read_text())
    certificate = gateway["gateway"]["certificate"]
    return {
        "schema": "omnibase.p34-7.trust-policy.v1",
        "schema_version": "2",
        "producers": {role: {"ed25519_public_key": keys[role]["public"]} for role in keys},
        "source_seal": {
            "repository": REPOSITORY,
            "approved_commits": list(approved_commits),
            "approved_trees": list(approved_trees),
        },
        "executables": executables,
        "commands": commands,
        "allowed_env_names": list(allowed_env_names),
        "gateway": {
            "issuer": certificate["issuer"],
            "san_suffix": ".omnibase",
            "validity_seconds": 100 * 365 * 86400,
        },
        "migration_head": "0012",
    }


def _policy_file(
    tmp_path: Path,
    run: Path,
    payload: dict[str, object],
    keys: dict[str, dict[str, str]],
    **kwargs: object,
) -> Path:
    policy = _policy_dict(run, payload, keys, **kwargs)  # type: ignore[arg-type]
    path = tmp_path / "trust-policy.json"
    _write_canonical(path, policy)
    return path


def _forge(tmp_path: Path, **kwargs: object) -> tuple[Path, dict[str, object]]:
    run = tmp_path / "run"
    payload = forge.forge_bundle(run, source_commit=COMMIT, source_tree=TREE, **kwargs)
    return run, payload


def _signed(tmp_path: Path, **kwargs: object) -> tuple[Path, dict[str, object], dict[str, object]]:
    keys = forge.generate_keyfile()
    run = tmp_path / "run"
    payload = forge.forge_bundle(run, source_commit=COMMIT, source_tree=TREE, keys=keys, **kwargs)
    return run, payload, keys


def _sign_raw(keys: dict[str, dict[str, str]], role: str, raw: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(keys[role]["private"]))
    return private.sign(raw)


def _resign_seal(run: Path, payload: dict[str, object], keys: dict[str, dict[str, str]]) -> None:
    """Recompute the evidence-seal binding from the current bundle refs and
    re-sign it with the sealer key (mirrors the verifier's binding)."""
    binding = {
        "schema": "omnibase.p34-7.evidence-seal.v1",
        "producer": "sealer",
        "run_id": payload["run_id"],
        "source_commit": payload["provenance"]["source_commit"],
        "source_tree": payload["provenance"]["source_tree"],
        "source_manifest_sha256": payload["source_manifest"]["raw_sha256"],
        "artifact_manifest_sha256": payload["artifact_manifest"]["raw_sha256"],
        "commands": {name: refs["receipt"]["sha256"] for name, refs in payload["commands"].items()},
        "components": {
            name: refs["evidence"]["sha256"] for name, refs in payload["components"].items()
        },
        "posture_measurement": payload["measurements"]["posture"]["evidence"]["sha256"],
        "attack_matrix": payload["attack_matrix"]["evidence"]["sha256"],
        "cleanup": payload["cleanup"]["evidence"]["sha256"],
        "migration_head": "0012",
        "feature_gates": dict(payload["feature_gates"]),
    }
    raw = _canonical(binding)
    seal = payload["evidence_seal"]
    seal["binding_sha256"] = _digest(raw)
    if seal.get("signature") is not None:
        sig_path = run / str(seal["signature"]["path"])
        sig_path.write_bytes(_sign_raw(keys, "sealer", raw))
        seal["signature"]["size"] = sig_path.stat().st_size
        seal["signature"]["sha256"] = _digest(sig_path.read_bytes())


def _rewrite_signed_file(
    run: Path,
    payload: dict[str, object],
    refs: dict[str, object],
    content_key: str,
    obj: dict[str, object],
    keys: dict[str, dict[str, str]],
    role: str,
) -> None:
    """Overwrite a signed canonical evidence/receipt file, re-sign it and
    re-seal the whole chain."""
    path = run / str(refs[content_key]["path"])
    raw = _canonical(obj)
    path.write_bytes(raw)
    refs[content_key]["size"] = len(raw)
    refs[content_key]["sha256"] = _digest(raw)
    sig_ref = refs.get("signature")
    if sig_ref is not None:
        sig_path = run / str(sig_ref["path"])
        sig_path.write_bytes(_sign_raw(keys, role, raw))
        sig_ref["size"] = sig_path.stat().st_size
        sig_ref["sha256"] = _digest(sig_path.read_bytes())
    _resign_seal(run, payload, keys)


def _rewrite_shared(
    run: Path,
    payload: dict[str, object],
    keys: dict[str, dict[str, str]],
    kind: str,
    obj: dict[str, object],
) -> None:
    """Rewrite a shared evidence file (posture/attack/cleanup), re-attest every
    component binding to it and re-seal the chain."""
    entry = {
        "posture": payload["measurements"]["posture"],
        "attack": payload["attack_matrix"],
        "cleanup": payload["cleanup"],
    }[kind]
    path = run / str(entry["evidence"]["path"])
    raw = _canonical(obj)
    path.write_bytes(raw)
    entry["evidence"]["size"] = len(raw)
    entry["evidence"]["sha256"] = _digest(raw)
    role = {"posture": "core", "attack": "runner", "cleanup": "sealer"}[kind]
    sig_ref = entry.get("signature")
    if sig_ref is not None:
        sig_path = run / str(sig_ref["path"])
        sig_path.write_bytes(_sign_raw(keys, role, raw))
        sig_ref["size"] = sig_path.stat().st_size
        sig_ref["sha256"] = _digest(sig_path.read_bytes())
    digest = entry["evidence"]["sha256"]
    field = {
        "posture": ("measurements", "posture_sha256"),
        "attack": ("results", "attack_matrix_sha256"),
        "cleanup": ("results", "cleanup_sha256"),
    }[kind]
    for name, refs in payload["components"].items():
        component = json.loads((run / str(refs["evidence"]["path"])).read_text())
        component[field[0]][field[1]] = digest
        component_raw = _canonical(component)
        refs["evidence"]["size"] = len(component_raw)
        refs["evidence"]["sha256"] = _digest(component_raw)
        (run / str(refs["evidence"]["path"])).write_bytes(component_raw)
        component_sig = refs.get("signature")
        if component_sig is not None:
            sig_path = run / str(component_sig["path"])
            sig_path.write_bytes(_sign_raw(keys, name, component_raw))
            component_sig["size"] = sig_path.stat().st_size
            component_sig["sha256"] = _digest(sig_path.read_bytes())
    _resign_seal(run, payload, keys)


def _blocked_report(run: Path, payload: dict[str, object], policy: Path | None):
    return verify_joint_evidence(run, payload, trust_policy_path=policy)


# ---------------------------------------------------------------------------
# Core negative proofs: a complete self-forged bundle never passes
# ---------------------------------------------------------------------------


def test_unsigned_complete_bundle_never_passes_without_policy(tmp_path: Path) -> None:
    """A complete unsigned bundle with every hash matching is blocked."""
    run, payload = _forge(tmp_path)
    report = _blocked_report(run, payload, None)
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert "trust_policy_unavailable" in report.blockers
    assert report.safety["signature_authenticity"] == "not_proven"
    for key, value in report.safety.items():
        if value == "not_proven":
            assert key in report.blockers, f"safety {key} must be a blocker"


def test_unsigned_complete_bundle_never_passes_with_policy(tmp_path: Path) -> None:
    """Even with a policy supplied, an unsigned bundle cannot pass: every
    evidence file lacks a verified detached signature."""
    run, payload = _forge(tmp_path)
    keys = forge.generate_keyfile()
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert "signature:command:core_runner" in report.blockers
    assert "signature:component:core" in report.blockers
    assert "signature:posture" in report.blockers
    assert "signature:attack" in report.blockers
    assert "signature:cleanup" in report.blockers
    assert "signature:seal" in report.blockers
    assert report.safety["signature_authenticity"] == "not_proven"


def test_forged_signature_bytes_never_pass(tmp_path: Path) -> None:
    """Random signature bytes must fail verification, not be trusted."""
    run, payload, keys = _signed(tmp_path, forged_signatures=True)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert report.safety["signature_authenticity"] == "not_proven"
    assert any(b.startswith("signature:") for b in report.blockers)


def test_signed_bundle_still_never_passes_without_approved_policy(tmp_path: Path) -> None:
    """Positive control: valid signatures verify, but the self-authored policy
    is not an approved trust anchor, so the fixture can never receive
    production passed."""
    run, payload, keys = _signed(tmp_path)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert report.safety["signature_authenticity"] == "verified"
    assert "trust_policy_not_approved" in report.blockers
    assert not any(b.startswith("signature:") for b in report.blockers)
    # signature_authenticity is verified, so it is NOT a blocker
    assert "signature_authenticity" not in report.blockers


def test_bundle_supplied_trust_root_is_never_a_trust_anchor(tmp_path: Path) -> None:
    """A public key shipped inside the bundle (top level or inside component
    evidence) is rejected as unknown schema and can never substitute for the
    external policy."""
    run, payload = _forge(tmp_path)
    keys = forge.generate_keyfile()
    attacker_key = keys["core"]["public"]
    payload["trust_roots"] = [attacker_key]
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        verify_joint_evidence(run, payload)
    run2, payload2, keys2 = _signed(tmp_path / "run2")
    component_path = run2 / "components/core.json"
    component = json.loads(component_path.read_text())
    component["public_key"] = attacker_key
    _write_canonical(component_path, component)
    policy = _policy_file(
        tmp_path, run2, payload2, keys2, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="drifted|unexpected fields"):
        verify_joint_evidence(run2, payload2, trust_policy_path=policy)
    # Without an external policy the bundle key is simply ignored: blocked.
    report = verify_joint_evidence(run2, payload2, trust_policy_path=None)
    assert report.passed is False
    assert report.status == "blocked/not_proven"


def test_swapped_producer_keys_are_blocked(tmp_path: Path) -> None:
    """A bundle signed by key A cannot pass when the policy pins key B."""
    run, payload, keys = _signed(tmp_path)
    other = forge.generate_keyfile()
    policy = _policy_file(
        tmp_path, run, payload, other, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert report.safety["signature_authenticity"] == "not_proven"
    assert "signature:component:core" in report.blockers


def test_cross_run_replay_is_blocked(tmp_path: Path) -> None:
    """Signed evidence from run A cannot be replayed as run B: every evidence
    file binds its own run_id, so re-enveloping under a new run_id is a
    cross-binding veto and can never pass."""
    run_a, payload_a, keys = _signed(tmp_path / "a")
    payload_a["run_id"] = "replayed-run-9999"
    policy = _policy_file(
        tmp_path, run_a, payload_a, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="different run_id"):
        verify_joint_evidence(run_a, payload_a, trust_policy_path=policy)
    # Replaying the identical signed files inside a second envelope must also
    # never pass: the files still bind the original run id.
    run_b, payload_b = _forge(tmp_path / "b", run_id="replayed-run-9999")
    for relative in ("receipts", "components", "measurements", "attack", "cleanup", "signatures"):
        target = run_b / relative
        if target.exists():
            import shutil

            shutil.rmtree(target)
        source = run_a / relative
        if source.exists():
            shutil.copytree(source, target)
    payload_b["commands"] = payload_a["commands"]
    payload_b["components"] = payload_a["components"]
    payload_b["measurements"] = payload_a["measurements"]
    payload_b["attack_matrix"] = payload_a["attack_matrix"]
    payload_b["cleanup"] = payload_a["cleanup"]
    policy_b = _policy_file(
        tmp_path, run_b, payload_b, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="different run_id|binding drifted"):
        verify_joint_evidence(run_b, payload_b, trust_policy_path=policy_b)


def test_cross_component_replay_is_blocked(tmp_path: Path) -> None:
    """The runner's signed evidence cannot serve as the broker's evidence."""
    run, payload, keys = _signed(tmp_path)
    runner_ref = payload["components"]["runner"]  # type: ignore[index]
    payload["components"]["broker"] = {
        "evidence": runner_ref["evidence"],  # type: ignore[index]
        "signature": runner_ref["signature"],  # type: ignore[index]
    }
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_modified_raw_bytes_are_rejected(tmp_path: Path) -> None:
    """Tampering with a signed evidence file breaks the hash binding."""
    run, payload, keys = _signed(tmp_path)
    receipt_path = run / "receipts/core_runner.json"
    receipt = json.loads(receipt_path.read_text())
    receipt["argv"] = ["/bin/evil", "--pwn"]
    _write_canonical(receipt_path, receipt)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="drifted"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_modified_raw_bytes_resigned_with_attacker_key_still_blocked(
    tmp_path: Path,
) -> None:
    """Even re-signing a tampered file with an attacker key cannot pass."""
    run, payload, keys = _signed(tmp_path)
    attacker = forge.generate_keyfile()
    refs = payload["components"]["core"]  # type: ignore[index]
    component_path = run / str(refs["evidence"]["path"])
    component = json.loads(component_path.read_text())
    component["host"] = {"os": "tampered", "kernel": "6.8.0", "arch": "x86_64"}
    raw = _canonical(component)
    component_path.write_bytes(raw)
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.from_private_bytes(
        bytes.fromhex(attacker["core"]["private"])
    )
    sig_path = run / str(refs["signature"]["path"])
    sig_path.write_bytes(private.sign(raw))
    refs["evidence"]["size"] = len(raw)
    refs["evidence"]["sha256"] = _digest(raw)
    refs["signature"]["size"] = sig_path.stat().st_size
    refs["signature"]["sha256"] = _digest(sig_path.read_bytes())
    _resign_seal(run, payload, attacker)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert report.safety["signature_authenticity"] == "not_proven"


def test_stale_certificate_is_blocked(tmp_path: Path) -> None:
    """A gateway certificate that has expired is an unproven posture."""
    stale = {
        "public_fingerprint": _digest(b"cert"),
        "issuer": _digest(b"issuer"),
        "san": "workload.gateway.omnibase",
        "valid_from": "2020-01-01T00:00:00Z",
        "valid_until": "2020-06-01T00:00:00Z",
        "revoked": False,
    }
    run, payload, keys = _signed(tmp_path, gateway_certificate=stale)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert "certificate_posture" in report.blockers
    assert report.safety["certificate_posture"] == "not_proven"


def test_revoked_certificate_is_blocked(tmp_path: Path) -> None:
    revoked = {
        "public_fingerprint": _digest(b"cert"),
        "issuer": _digest(b"issuer"),
        "san": "workload.gateway.omnibase",
        "valid_from": "2020-01-01T00:00:00Z",
        "valid_until": "2099-01-01T00:00:00Z",
        "revoked": True,
    }
    run, payload, keys = _signed(tmp_path, gateway_certificate=revoked)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert "certificate_posture" in report.blockers


def test_replayed_gateway_credentials_are_blocked(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    refs = payload["components"]["gateway"]  # type: ignore[index]
    component = json.loads((run / str(refs["evidence"]["path"])).read_text())
    component["gateway"]["replay"]["replayed"] = True
    _rewrite_signed_file(run, payload, refs, "evidence", component, keys, "gateway")
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert "replay_posture" in report.blockers


def test_safety_evidence_absence_is_blocked(tmp_path: Path) -> None:
    """Removing the detached signature from the posture measurement makes
    every posture-derived safety item not_proven and blocks."""
    run, payload, keys = _signed(tmp_path)
    posture_entry = payload["measurements"]["posture"]  # type: ignore[index]
    posture_entry["signature"] = None
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert "signature:posture" in report.blockers
    assert "runtime_posture" in report.blockers
    assert "root_env_not_accessed" in report.blockers
    assert "business_database_not_accessed" in report.blockers
    assert "business_database_not_migrated" in report.blockers
    assert "production_runtime_inactive" in report.blockers
    assert "hostile_code_not_executed" in report.blockers


def test_unmeasured_posture_never_passes_regression(tmp_path: Path) -> None:
    """REGRESSION (Round 2): a bundle whose signed posture measurement says
    measured=false must NOT pass; runtime_posture must be a blocker."""
    run, payload, keys = _signed(tmp_path)
    posture_refs = payload["measurements"]["posture"]  # type: ignore[index]
    posture = json.loads((run / str(posture_refs["evidence"]["path"])).read_text())
    posture["measured"] = False
    _rewrite_shared(run, payload, keys, "posture", posture)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert report.safety["runtime_posture"] == "not_proven"
    assert "runtime_posture" in report.blockers
    assert "production_runtime_inactive" in report.blockers


def test_attack_results_must_come_from_signed_evidence(tmp_path: Path) -> None:
    """Inline attack status/results are not part of the schema: they must be
    parsed from the signed attack-matrix evidence and cross-checked against
    the inventory."""
    run, payload, keys = _signed(tmp_path)
    attack_entry = payload["attack_matrix"]  # type: ignore[index]
    attack_entry["status"] = "passed"
    attack_entry["results"] = {"node_compromise": "rejected"}
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_attack_inventory_cross_check_is_blocked(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    attack_refs = payload["attack_matrix"]  # type: ignore[index]
    attack = json.loads((run / str(attack_refs["evidence"]["path"])).read_text())
    attack["inventory"][0]["outcome"] = "succeeded"
    _rewrite_shared(run, payload, keys, "attack", attack)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert "attack:node_compromise" in report.blockers
    assert report.safety["attack_results"] == "not_proven"


def test_cleanup_counts_must_come_from_signed_evidence(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    cleanup_entry = payload["cleanup"]  # type: ignore[index]
    cleanup_entry["containers"] = 0
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_cleanup_residue_is_blocked(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    cleanup_refs = payload["cleanup"]  # type: ignore[index]
    cleanup = json.loads((run / str(cleanup_refs["evidence"]["path"])).read_text())
    cleanup["inventory"] = [
        {
            "class": "processes",
            "item_id": "pid-1234",
            "removed_at": "2026-08-07T00:05:00Z",
        }
    ]
    cleanup["counts"] = {key: 0 for key in cleanup["counts"]}
    _rewrite_shared(run, payload, keys, "cleanup", cleanup)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert "cleanup:processes" in report.blockers
    assert report.safety["cleanup_complete"] == "not_proven"


def test_evidence_seal_binding_drift_is_veto(tmp_path: Path) -> None:
    """The seal signature covers the whole verified chain; a stale binding is
    a veto, never a pass."""
    run, payload, keys = _signed(tmp_path)
    payload["evidence_seal"]["binding_sha256"] = "f" * 64  # type: ignore[index]
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="binding_sha256"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_forged_seal_signature_is_blocked(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    seal = payload["evidence_seal"]  # type: ignore[index]
    sig_path = run / str(seal["signature"]["path"])
    sig_path.write_bytes(b"\x00" * 64)
    seal["signature"]["size"] = 64  # type: ignore[index]
    seal["signature"]["sha256"] = _digest(b"\x00" * 64)  # type: ignore[index]
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert "signature:seal" in report.blockers


# ---------------------------------------------------------------------------
# P1: source identity, command semantics, timestamps, schema version
# ---------------------------------------------------------------------------


def test_source_commit_not_in_approved_seal_is_blocked(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    policy = _policy_file(tmp_path, run, payload, keys)
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert report.safety["source_provenance"] == "not_proven"
    assert "source_provenance" in report.blockers


def test_approved_source_seal_is_not_enough_to_pass(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert report.safety["source_provenance"] == "verified"


def test_schema_version_1_is_rejected(tmp_path: Path) -> None:
    """The implementation and documentation only describe v2; accepting an
    undefined compatibility version 1 is forbidden."""
    run, payload = _forge(tmp_path)
    payload["schema_version"] = "1"
    with pytest.raises(ConfigurationError, match="schema_version"):
        verify_joint_evidence(run, payload)


def test_argv_template_mismatch_is_blocked(tmp_path: Path) -> None:
    """Arbitrary argv with a matching executable hash is insufficient: the
    exact command template from the policy is required."""
    run, payload, keys = _signed(tmp_path)
    overrides = {"core_runner": ["/run/omnibase/bin/core_runner", "--unapproved-flag"]}
    policy = _policy_file(
        tmp_path,
        run,
        payload,
        keys,
        approved_commits=(COMMIT,),
        approved_trees=(TREE,),
        argv_overrides=overrides,
    )
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert report.safety["command_semantics"] == "not_proven"
    assert "command_semantics" in report.blockers


def test_executable_not_in_approved_manifest_is_blocked(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    overrides = {"bin/core_runner": {"sha256": "e" * 64, "commands": ["core_runner"]}}
    policy = _policy_file(
        tmp_path,
        run,
        payload,
        keys,
        approved_commits=(COMMIT,),
        approved_trees=(TREE,),
        executable_overrides=overrides,
    )
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert report.safety["artifact_provenance"] == "not_proven"
    assert "artifact_provenance" in report.blockers


def test_unknown_env_names_are_blocked(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    policy = _policy_file(
        tmp_path,
        run,
        payload,
        keys,
        approved_commits=(COMMIT,),
        approved_trees=(TREE,),
        allowed_env_names=("PATH",),
    )
    report = _blocked_report(run, payload, policy)
    assert report.passed is False
    assert "command_semantics" in report.blockers


def test_timestamps_compared_as_utc_instants(tmp_path: Path) -> None:
    """An offset timestamp that sorts AFTER but is really BEFORE the previous
    command end must be rejected: comparisons use UTC instants, never raw
    strings."""
    run, payload, keys = _signed(tmp_path)
    refs = payload["commands"]["runner_broker"]  # type: ignore[index]
    receipt = json.loads((run / str(refs["receipt"]["path"])).read_text())
    # Lexicographically '01:00:00+02:00' > '00:00:30Z' but as UTC instants
    # 2026-08-06T23:00:00Z is before the previous command ended.
    receipt["started_at"] = "2026-08-07T01:00:00+02:00"
    receipt["ended_at"] = "2026-08-07T01:00:30+02:00"
    _rewrite_signed_file(run, payload, refs, "receipt", receipt, keys, "runner")
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="chronology|UTC"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_non_utc_timestamp_is_rejected(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    refs = payload["commands"]["core_runner"]  # type: ignore[index]
    receipt = json.loads((run / str(refs["receipt"]["path"])).read_text())
    receipt["started_at"] = "2026-08-07 00:00:00"
    _rewrite_signed_file(run, payload, refs, "receipt", receipt, keys, "core")
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="UTC"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_trust_policy_inside_evidence_dir_is_rejected(tmp_path: Path) -> None:
    """The trust policy must be independently configured OUTSIDE the evidence
    directory; a policy shipped inside the run dir is never an anchor."""
    run, payload, keys = _signed(tmp_path)
    inside = run / "bundle-supplied-policy.json"
    _write_canonical(
        inside, _policy_dict(run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,))
    )
    with pytest.raises(ConfigurationError, match="outside the evidence run directory"):
        verify_joint_evidence(run, payload, trust_policy_path=inside)


def test_example_trust_policy_is_parseable(tmp_path: Path) -> None:
    policy_path = REPO_ROOT / "deployment" / "production" / "p34-7-trust-policy.example.json"
    from omnibase.production.joint_gate import load_trust_policy

    policy, raw_sha256 = load_trust_policy(policy_path)
    assert policy.schema_version == "2"
    assert len(raw_sha256) == 64


# ---------------------------------------------------------------------------
# Structural negative matrix (fail-closed, never passed)
# ---------------------------------------------------------------------------


def test_validate_only_never_passes(tmp_path: Path) -> None:
    run, payload = _forge(tmp_path)
    report = validate_joint_evidence_contract(payload)
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert report.mode == "validate-only"
    assert "contract_mode_no_direct_evidence" in report.blockers


def test_backwards_compatible_alias_matches_verify(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    expected = verify_joint_evidence(run, payload, trust_policy_path=policy)
    actual = validate_joint_evidence(run, payload, trust_policy_path=policy)
    assert actual.status == expected.status
    assert actual.passed is expected.passed


def test_missing_receipt_file_is_veto(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    (run / "receipts/core_runner.json").unlink()
    with pytest.raises(ConfigurationError, match="unavailable|drifted"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_reordered_command_steps_is_rejected(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    payload["commands"]["core_runner"]["order"] = 1  # type: ignore[index]
    payload["commands"]["runner_broker"]["order"] = 0  # type: ignore[index]
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="order"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_missing_command_step_is_rejected(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    del payload["commands"]["overlay_data_plane"]  # type: ignore[index]
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="commands must contain"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_command_exit_code_mismatch_is_rejected(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    refs = payload["commands"]["broker_gateway"]  # type: ignore[index]
    receipt = json.loads((run / str(refs["receipt"]["path"])).read_text())
    receipt["exit_code"] = 1
    _rewrite_signed_file(run, payload, refs, "receipt", receipt, keys, "broker")
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="exit_code"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_command_chronology_inconsistency_is_rejected(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    refs = payload["commands"]["runner_gateway"]  # type: ignore[index]
    receipt = json.loads((run / str(refs["receipt"]["path"])).read_text())
    receipt["started_at"] = "2026-08-07T00:00:00Z"
    receipt["ended_at"] = "2026-08-07T00:00:10Z"
    _rewrite_signed_file(run, payload, refs, "receipt", receipt, keys, "runner")
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="chronology"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_secret_env_name_is_rejected(tmp_path: Path) -> None:
    run, payload, keys = _signed(tmp_path)
    refs = payload["commands"]["core_runner"]  # type: ignore[index]
    receipt = json.loads((run / str(refs["receipt"]["path"])).read_text())
    receipt["env_names"] = ["PATH", "JWT_SECRET"]
    _rewrite_signed_file(run, payload, refs, "receipt", receipt, keys, "core")
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="secret env names"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_path_traversal_in_manifest_is_rejected(tmp_path: Path) -> None:
    run, payload = _forge(tmp_path)
    payload["source_manifest"]["files"][0]["path"] = "../escape.txt"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="traversal|normalized"):
        verify_joint_evidence(run, payload)


def test_absolute_path_escape_is_rejected(tmp_path: Path) -> None:
    run, payload = _forge(tmp_path)
    payload["artifact_manifest"]["files"][0]["path"] = "/etc/passwd"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="normalized relative|absolute"):
        verify_joint_evidence(run, payload)


def test_symlink_artifact_is_rejected(tmp_path: Path) -> None:
    if os.name == "nt":
        pytest.skip("symlink behavior on Windows is covered by reparse-point guards")
    run, payload = _forge(tmp_path)
    link = run / "source-link.txt"
    link.symlink_to(run / "source.txt")
    payload["source_manifest"]["files"][0]["path"] = "source-link.txt"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="link or reparse point"):
        verify_joint_evidence(run, payload)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction test")
def test_windows_junction_in_middle_component_is_rejected(tmp_path: Path) -> None:
    """Every path component is checked, not only the final file: a junction
    directory in the middle of an evidence path must be rejected."""
    run, payload = _forge(tmp_path)
    target = tmp_path / "junction-target"
    target.mkdir(parents=True, exist_ok=True)
    (target / "payload.txt").write_bytes(b"outside-run-bytes")
    junction = run / "outside-link"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(target)],
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("mklink /J unavailable")
    # The manifest now points through the junction: the intermediate
    # component "outside-link" is a reparse point and must be rejected.
    payload["source_manifest"]["files"][0]["path"] = "outside-link/payload.txt"
    payload["source_manifest"]["files"][0]["size"] = 17
    payload["source_manifest"]["files"][0]["sha256"] = _digest(b"outside-run-bytes")
    with pytest.raises(ConfigurationError, match="link or reparse point"):
        verify_joint_evidence(run, payload)


def test_unknown_top_level_field_is_rejected(tmp_path: Path) -> None:
    run, payload = _forge(tmp_path)
    payload["operator_self_asserted_pass"] = True
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        verify_joint_evidence(run, payload)


def test_wrong_migration_head_is_rejected(tmp_path: Path) -> None:
    run, payload = _forge(tmp_path)
    payload["migration_head"] = "0013"
    with pytest.raises(ConfigurationError, match="migration head"):
        verify_joint_evidence(run, payload)


@pytest.mark.parametrize(
    "gate", ["agent_runtime_enabled", "agent_planner_enabled", "multi_agent_enabled"]
)
def test_enabled_feature_gate_is_rejected(tmp_path: Path, gate: str) -> None:
    run, payload = _forge(tmp_path)
    payload["feature_gates"][gate] = True  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="feature gates"):
        verify_joint_evidence(run, payload)


def test_dirty_checkout_is_rejected(tmp_path: Path) -> None:
    run, payload = _forge(tmp_path)
    payload["provenance"]["dirty"] = True  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="clean checkout"):
        verify_joint_evidence(run, payload)


def test_non_production_environment_is_rejected(tmp_path: Path) -> None:
    run, payload = _forge(tmp_path)
    payload["environment"] = "staging"
    with pytest.raises(ConfigurationError, match="environment=production"):
        verify_joint_evidence(run, payload)


def test_disposable_evidence_cannot_prove_production(tmp_path: Path) -> None:
    run, payload = _forge(tmp_path)
    payload["disposable"] = True
    with pytest.raises(ConfigurationError, match="disposable"):
        verify_joint_evidence(run, payload)


def test_non_canonical_evidence_bytes_are_rejected(tmp_path: Path) -> None:
    """A signed JSON file whose raw bytes are not canonical JSON must be
    rejected: the signature must cover canonical raw bytes."""
    run, payload, keys = _signed(tmp_path)
    receipt_path = run / "receipts/core_runner.json"
    receipt = json.loads(receipt_path.read_text())
    raw = json.dumps(receipt, indent=2).encode()
    receipt_path.write_bytes(raw)
    refs = payload["commands"]["core_runner"]  # type: ignore[index]
    refs["receipt"]["size"] = len(raw)
    refs["receipt"]["sha256"] = _digest(raw)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="canonical"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_report_to_dict_never_hardcodes_safety(tmp_path: Path) -> None:
    run, payload = _forge(tmp_path)
    report = verify_joint_evidence(run, payload)
    serialized = report.to_dict()
    assert "safety" in serialized
    for forbidden in (
        "root_env_accessed",
        "business_database_accessed",
        "business_database_migrated",
        "runtime_activated",
    ):
        assert forbidden not in serialized


# ---------------------------------------------------------------------------
# CLI-level adversarial proof (forger -> validator)
# ---------------------------------------------------------------------------


def test_cli_forger_unsigned_bundle_is_blocked_by_validator(tmp_path: Path) -> None:
    run = tmp_path / "cli-run"
    forged = subprocess.run(
        [sys.executable, str(_FORGER_PATH), "--output", str(run)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert forged.returncode == 0, forged.stderr
    validated = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "production" / "validate_p34_7_joint_gate.py"),
            "--verify-evidence",
            str(run),
            "--evidence",
            str(run / "evidence.json"),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert validated.returncode == 2, validated.stdout + validated.stderr
    report = json.loads(validated.stdout)
    assert report["status"] == "blocked/not_proven"
    assert report["passed"] is False
    assert "trust_policy_unavailable" in report["blockers"]


def test_cli_validate_only_never_passes(tmp_path: Path) -> None:
    contract = (
        REPO_ROOT / "deployment" / "production" / "p34-7-joint-evidence-contract.example.json"
    )
    validated = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "production" / "validate_p34_7_joint_gate.py"),
            "--validate-only",
            "--evidence",
            str(contract),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert validated.returncode == 2, validated.stdout + validated.stderr
    report = json.loads(validated.stdout)
    assert report["status"] == "blocked/not_proven"
    assert report["passed"] is False
    assert "contract_mode_no_direct_evidence" in report["blockers"]
