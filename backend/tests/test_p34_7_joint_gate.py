"""Tests for the trust-anchored hardened P34.7 joint gate.

The joint gate must never return ``passed`` for a self-forged bundle, even
when every hash matches.  The tests here use the adversarial forger
``scripts/production/forge_p34_7_evidence_bundle.py`` to fabricate complete
bundles from scratch (files, manifests, cross-bindings and hashes) and then
assert that every authenticity gap keeps the report ``blocked/not_proven``.

A production PASS additionally requires an independently approved trust policy
(pinned in ``joint_gate._APPROVED_TRUST_POLICY_SHA256``, currently empty).  The
suite therefore contains exactly one TRUE positive control
(:func:`test_positive_control_signed_chain_passes_after_policy_approval`) that
temporarily monkeypatches the approved-digest set in-process (never committed)
so the fully signed, manifest-bound, seal-consistent chain can be proven
pass-capable; every post-approval attack test around it proves that any single
drift flips the result to ``passed=false`` or a ``ConfigurationError`` veto.
No other fixture may ever receive ``passed``.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from omnibase.production import joint_gate as jg
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
COMMIT = "a" * 40
TREE = "b" * 40
OBJECT_FORMAT = "sha1"
MAX_EVIDENCE_AGE_SECONDS = 7 * 86400


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _write_canonical(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _iso_utc(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _run_window(payload: dict[str, object]) -> tuple[datetime, datetime]:
    started = datetime.fromisoformat(str(payload["run_started_at"]).replace("Z", "+00:00"))
    completed = datetime.fromisoformat(str(payload["run_completed_at"]).replace("Z", "+00:00"))
    return started, completed


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
    git_object_format: str = OBJECT_FORMAT,
    max_evidence_age_seconds: int = MAX_EVIDENCE_AGE_SECONDS,
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
            "git_object_format": git_object_format,
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
        "max_evidence_age_seconds": max_evidence_age_seconds,
        "migration_head": "0016",
    }


def _policy_file(
    tmp_path: Path,
    run: Path,
    payload: dict[str, object],
    keys: dict[str, dict[str, str]],
    *,
    name: str = "trust-policy.json",
    **kwargs: object,
) -> Path:
    policy = _policy_dict(run, payload, keys, **kwargs)  # type: ignore[arg-type]
    path = tmp_path / name
    _write_canonical(path, policy)
    return path


def _forge(tmp_path: Path, **kwargs: object) -> tuple[Path, dict[str, object]]:
    run = tmp_path / "run"
    fmt = str(kwargs.get("git_object_format", OBJECT_FORMAT))
    if "source_commit" not in kwargs:
        kwargs["source_commit"] = "a" * 64 if fmt == "sha256" else COMMIT
    if "source_tree" not in kwargs:
        kwargs["source_tree"] = "b" * 64 if fmt == "sha256" else TREE
    payload = forge.forge_bundle(run, **kwargs)
    return run, payload


def _signed(tmp_path: Path, **kwargs: object) -> tuple[Path, dict[str, object], dict[str, object]]:
    keys = forge.generate_keyfile()
    run = tmp_path / "run"
    fmt = str(kwargs.get("git_object_format", OBJECT_FORMAT))
    if "source_commit" not in kwargs:
        kwargs["source_commit"] = "a" * 64 if fmt == "sha256" else COMMIT
    if "source_tree" not in kwargs:
        kwargs["source_tree"] = "b" * 64 if fmt == "sha256" else TREE
    payload = forge.forge_bundle(run, keys=keys, **kwargs)
    return run, payload, keys


def _sign_raw(keys: dict[str, dict[str, str]], role: str, raw: bytes) -> bytes:
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.from_private_bytes(bytes.fromhex(keys[role]["private"]))
    return private.sign(raw)


def _self_policy_dict(
    run: Path, payload: dict[str, object], keys: dict[str, dict[str, str]]
) -> dict[str, object]:
    """A trust-policy object that is fully consistent with the current bundle
    (producer keys, receipt executables/argv and the gateway certificate),
    mirroring what a self-consistent producer would have used at seal time."""
    executables: dict[str, object] = {}
    commands: dict[str, object] = {}
    for name, refs in payload["commands"].items():  # type: ignore[union-attr]
        receipt = json.loads((run / str(refs["receipt"]["path"])).read_text())
        exe = receipt["executable"]
        executables[exe["path"]] = {"sha256": exe["sha256"], "commands": [name]}
        commands[name] = receipt["argv"]
    gateway = json.loads((run / "components/gateway.json").read_text())
    certificate = gateway["gateway"]["certificate"]
    return {
        "schema": "omnibase.p34-7.trust-policy.v1",
        "schema_version": "2",
        "producers": {role: {"ed25519_public_key": keys[role]["public"]} for role in forge.ROLES},
        "source_seal": {
            "repository": REPOSITORY,
            "git_object_format": str(payload["provenance"]["git_object_format"]),  # type: ignore[index]
            "approved_commits": [payload["provenance"]["source_commit"]],  # type: ignore[index]
            "approved_trees": [payload["provenance"]["source_tree"]],  # type: ignore[index]
        },
        "executables": executables,
        "commands": commands,
        "allowed_env_names": ["PATH", "OMNIBASE_RUN_ID"],
        "gateway": {
            "issuer": certificate["issuer"],
            "san_suffix": ".omnibase",
            "validity_seconds": 100 * 365 * 86400,
        },
        "max_evidence_age_seconds": MAX_EVIDENCE_AGE_SECONDS,
        "migration_head": "0016",
    }


def _resign_seal(run: Path, payload: dict[str, object], keys: dict[str, dict[str, str]]) -> None:
    """Recompute the evidence-seal binding exactly as the verifier derives it
    for the CURRENT bundle (including any mutation), using a policy that is
    consistent with the bundle, and re-sign it with the sealer key."""
    try:
        binding = jg.compute_seal_binding(run, payload, _self_policy_dict(run, payload, keys))
    except ConfigurationError:
        # The mutated bundle is structurally invalid; verification will veto it
        # before the seal, so there is nothing to re-seal.
        return
    raw = _canonical(binding)
    seal = payload["evidence_seal"]  # type: ignore[index]
    seal["binding_sha256"] = _digest(raw)
    if seal.get("signature") is not None:
        sig_path = run / str(seal["signature"]["path"])
        sig_path.write_bytes(_sign_raw(keys, "sealer", raw))
        seal["signature"]["size"] = sig_path.stat().st_size  # type: ignore[index]
        seal["signature"]["sha256"] = _digest(sig_path.read_bytes())  # type: ignore[index]


def _resign_seal_with_policy(
    run: Path,
    payload: dict[str, object],
    keys: dict[str, dict[str, str]],
    policy_path: Path,
) -> None:
    """Recompute the seal binding with the ACTUAL external policy the verifier
    will use (producer keys, argv templates, executable pins and env allowlist
    all influence the derived safety posture) and re-sign with the sealer key.
    Keeps the bundle seal-consistent for tests whose policy deviates from the
    bundle's self-consistent defaults."""
    policy = json.loads(policy_path.read_text())
    binding = jg.compute_seal_binding(run, payload, policy)
    raw = _canonical(binding)
    seal = payload["evidence_seal"]  # type: ignore[index]
    seal["binding_sha256"] = _digest(raw)
    if seal.get("signature") is not None:
        sig_path = run / str(seal["signature"]["path"])
        sig_path.write_bytes(_sign_raw(keys, "sealer", raw))
        seal["signature"]["size"] = sig_path.stat().st_size  # type: ignore[index]
        seal["signature"]["sha256"] = _digest(sig_path.read_bytes())  # type: ignore[index]


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


def _rewrite_artifact_manifest(
    run: Path,
    payload: dict[str, object],
    keys: dict[str, dict[str, str]],
    files: list[dict[str, object]],
) -> None:
    """Rewrite the artifact manifest (files plus recomputed ``raw_sha256``),
    re-attest every component evidence's ``artifact_manifest_sha256`` binding
    (re-signing each component) and re-seal the chain.  Simulates an attacker
    who updates the manifest consistently but leaves the signed receipts
    untouched."""
    payload["artifact_manifest"] = {  # type: ignore[index]
        "raw_sha256": _digest(_canonical(files)),
        "files": files,
    }
    digest = payload["artifact_manifest"]["raw_sha256"]  # type: ignore[index]
    for name, refs in payload["components"].items():  # type: ignore[union-attr]
        component = json.loads((run / str(refs["evidence"]["path"])).read_text())
        component["artifact_manifest_sha256"] = digest
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
    # Re-seal against the swapped policy so the ONLY defect under test is the
    # producer-key mismatch (the seal must bind the derived safety posture).
    _resign_seal_with_policy(run, payload, other, policy)
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
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    # Re-seal against the real policy so the only defect under test is the
    # attacker-signed component (the seal must bind the derived safety).
    _resign_seal_with_policy(run, payload, keys, policy)
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
    _resign_seal(run, payload, keys)
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
    run_started, _run_completed = _run_window(payload)
    cleanup_refs = payload["cleanup"]  # type: ignore[index]
    cleanup = json.loads((run / str(cleanup_refs["evidence"]["path"])).read_text())
    cleanup["inventory"] = [
        {
            "class": "processes",
            "item_id": "pid-1234",
            "removed_at": _iso_utc(run_started + timedelta(minutes=50)),
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
    # The seal is the attack vector here, so it must NOT be re-sealed.
    report = verify_joint_evidence(run, payload, trust_policy_path=policy)
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
    _resign_seal_with_policy(run, payload, keys, policy)
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
    _resign_seal_with_policy(run, payload, keys, policy)
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
    _resign_seal_with_policy(run, payload, keys, policy)
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
    run_started, _run_completed = _run_window(payload)
    # The previous command (core_runner) ends 30s after run_started_at.  A
    # lexicographically-later string ('...+02:00') that is really an earlier
    # UTC instant (run_started+20s) must be rejected, while staying inside the
    # run window so the chronology rule -- not the window rule -- fires.
    rewritten_start = run_started + timedelta(seconds=20)
    offset = timezone(timedelta(hours=2))
    receipt["started_at"] = rewritten_start.astimezone(offset).isoformat()
    receipt["ended_at"] = (rewritten_start + timedelta(seconds=30)).astimezone(offset).isoformat()
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
    run_started, _run_completed = _run_window(payload)
    # runner_gateway is the third command: its start must be after the second
    # command (runner_broker) ended (run_started+10m30s).  A start at
    # run_started+60s -- inside the run window but before the previous end --
    # must be rejected for chronology.
    receipt["started_at"] = _iso_utc(run_started + timedelta(seconds=60))
    receipt["ended_at"] = _iso_utc(run_started + timedelta(seconds=70))
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
    payload["migration_head"] = "0017"
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


# ---------------------------------------------------------------------------
# Round 3: TRUE positive control (in-process approval only) and the
# post-approval attack matrix.  The approved-digest set is monkeypatched
# in-process and never committed; the production pin stays empty.
# ---------------------------------------------------------------------------


def _approve_in_process(monkeypatch: pytest.MonkeyPatch, policy: Path) -> None:
    """Temporarily approve exactly one test policy digest IN PROCESS ONLY.
    ``monkeypatch`` restores the production empty set at teardown; nothing is
    committed into ``_APPROVED_TRUST_POLICY_SHA256``."""
    monkeypatch.setattr(
        jg, "_APPROVED_TRUST_POLICY_SHA256", frozenset({_digest(policy.read_bytes())})
    )


def _approved_policy(
    tmp_path: Path,
    run: Path,
    payload: dict[str, object],
    keys: dict[str, dict[str, str]],
) -> Path:
    return _policy_file(
        tmp_path,
        run,
        payload,
        keys,
        approved_commits=(COMMIT,),
        approved_trees=(TREE,),
    )


def _bind_seal_over(
    run: Path,
    payload: dict[str, object],
    keys: dict[str, dict[str, str]],
    policy_path: Path,
    overrides: dict[str, object],
) -> None:
    """Simulate a sealer that signed the binding over DIFFERENT
    envelope/provenance values than the outer bundle now claims: record a
    binding (and a valid sealer signature) over ``overrides`` without touching
    the outer fields.  The verifier recomputes the canonical binding from the
    outer values, so any rewrite must fail."""
    policy = json.loads(policy_path.read_text())
    binding = jg.compute_seal_binding(run, payload, policy)
    if "provenance" in overrides and isinstance(overrides["provenance"], dict):
        binding["provenance"] = {**binding["provenance"], **overrides["provenance"]}
        overrides = {k: v for k, v in overrides.items() if k != "provenance"}
    binding.update(overrides)
    raw = _canonical(binding)
    seal = payload["evidence_seal"]  # type: ignore[index]
    seal["binding_sha256"] = _digest(raw)
    sig_path = run / str(seal["signature"]["path"])
    sig_path.write_bytes(_sign_raw(keys, "sealer", raw))
    seal["signature"]["size"] = sig_path.stat().st_size  # type: ignore[index]
    seal["signature"]["sha256"] = _digest(sig_path.read_bytes())  # type: ignore[index]


def test_positive_control_signed_chain_passes_after_policy_approval(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """TRUE positive control: with the trust policy approved in-process and a
    fully signed, artifact-manifest-bound, seal-consistent chain, the verifier
    CAN reach ``passed``.  This proves the pass path is real and that every
    attack test around it flips a genuinely pass-capable bundle to
    ``passed=false``.  The approved digest is never committed: the production
    set stays empty after teardown."""
    assert frozenset() == jg._APPROVED_TRUST_POLICY_SHA256
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    report = verify_joint_evidence(run, payload, trust_policy_path=policy)
    assert report.status == "passed"
    assert report.passed is True
    assert report.blockers == ()
    assert report.safety["trust_policy"] == "verified"
    assert report.safety["source_provenance"] == "verified"
    assert report.safety["signature_authenticity"] == "verified"
    assert report.safety["artifact_provenance"] == "verified"
    assert report.safety["command_semantics"] == "verified"
    assert report.safety["runtime_posture"] == "measured:process_config"
    assert report.safety["production_runtime_inactive"] == "verified"
    assert report.safety["hostile_code_not_executed"] == "verified"
    assert report.safety["root_env_not_accessed"] == "verified"
    assert report.safety["business_database_not_accessed"] == "verified"
    assert report.safety["business_database_not_migrated"] == "verified"
    assert report.safety["attack_results"] == "verified"
    assert report.safety["cleanup_complete"] == "verified"
    assert report.safety["certificate_posture"] == "verified"
    assert report.safety["replay_posture"] == "verified"
    assert report.safety["evidence_seal"] == "verified"
    assert all(value != "not_proven" for value in report.safety.values())
    assert frozenset({_digest(policy.read_bytes())}) == jg._APPROVED_TRUST_POLICY_SHA256
    # Teardown restores the production pin; assert it right after the test
    # scope so the committed set is demonstrably untouched.


def test_positive_control_teardown_restores_empty_production_pin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The in-process monkeypatch is scoped to its test: after approval of a
    test digest, the module-level production pin is the empty set again."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    assert frozenset({_digest(policy.read_bytes())}) == jg._APPROVED_TRUST_POLICY_SHA256
    # monkeypatch teardown happens here at the end of the test body scope.


def test_post_approval_swapped_executable_bytes_are_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attack: replace the actual bin/core_runner bytes WITHOUT changing the
    receipt (the manifest is updated consistently, as a byte-swapping attacker
    would).  The verifier reads the real file bytes, so the three-way digest
    equality (actual == receipt == policy) breaks and the bundle is blocked."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    assert verify_joint_evidence(run, payload, trust_policy_path=policy).passed is True
    evil = b"evil-replacement-bytes"
    (run / "bin/core_runner").write_bytes(evil)
    files = payload["artifact_manifest"]["files"]  # type: ignore[index]
    for entry in files:
        if entry["path"] == "bin/core_runner":
            entry["size"] = len(evil)
            entry["sha256"] = _digest(evil)
    _rewrite_artifact_manifest(run, payload, keys, files)
    report = verify_joint_evidence(run, payload, trust_policy_path=policy)
    assert report.passed is False
    assert report.status == "blocked/not_proven"
    assert "artifact_provenance" in report.blockers
    assert report.safety["artifact_provenance"] == "not_proven"


def test_post_approval_executable_absent_from_artifact_manifest_is_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attack: an executable exists in the receipt/policy declarations but is
    absent from the approved artifact manifest (the manifest is otherwise
    rewritten consistently).  Every executable must be manifest-bound;
    declaration-only executables are blocked."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    assert verify_joint_evidence(run, payload, trust_policy_path=policy).passed is True
    files = [
        entry
        for entry in payload["artifact_manifest"]["files"]  # type: ignore[index]
        if entry["path"] != "bin/core_runner"
    ]
    _rewrite_artifact_manifest(run, payload, keys, files)
    report = verify_joint_evidence(run, payload, trust_policy_path=policy)
    assert report.passed is False
    assert report.status == "blocked/not_proven"
    assert "artifact_provenance" in report.blockers


def test_post_approval_environment_rewrite_without_resigning_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attack: staging evidence is relabelled production without a key
    rewrite.  (a) the envelope rejects a non-production declaration; (b) the
    canonical seal binding covers ``environment``, so even a seal that was
    signed over ``staging`` can never match a ``production`` outer bundle."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    assert verify_joint_evidence(run, payload, trust_policy_path=policy).passed is True
    payload["environment"] = "staging"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="environment=production"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)
    payload["environment"] = "production"  # type: ignore[index]
    _bind_seal_over(run, payload, keys, policy, {"environment": "staging"})
    with pytest.raises(ConfigurationError, match="binding_sha256"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_post_approval_disposable_rewrite_without_resigning_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attack: disposable evidence is relabelled non-disposable without a key
    rewrite.  (a) the envelope rejects a disposable declaration; (b) the seal
    binding covers ``disposable``, so a seal signed over ``true`` can never
    match an outer bundle claiming ``false``."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    assert verify_joint_evidence(run, payload, trust_policy_path=policy).passed is True
    payload["disposable"] = True  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="disposable"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)
    payload["disposable"] = False  # type: ignore[index]
    _bind_seal_over(run, payload, keys, policy, {"disposable": True})
    with pytest.raises(ConfigurationError, match="binding_sha256"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_post_approval_dirty_rewrite_without_resigning_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attack: evidence from a dirty checkout is relabelled clean without a
    key rewrite.  (a) the envelope rejects a dirty declaration; (b) the seal
    binding covers the full provenance (repository/source_commit/source_tree/
    dirty), so a seal signed over ``dirty=true`` can never match a clean outer
    bundle."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    assert verify_joint_evidence(run, payload, trust_policy_path=policy).passed is True
    payload["provenance"]["dirty"] = True  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="clean checkout"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)
    payload["provenance"]["dirty"] = False  # type: ignore[index]
    _bind_seal_over(run, payload, keys, policy, {"provenance": {"dirty": True}})
    with pytest.raises(ConfigurationError, match="binding_sha256"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_duplicate_producer_keys_all_roles_share_one_key_fail_closed(
    tmp_path: Path,
) -> None:
    """Attack: all seven roles (six components + sealer) share ONE Ed25519
    key.  Duplicate keys must fail closed at policy parse time."""
    run, payload, keys = _signed(tmp_path)
    shared = keys["core"]["public"]
    dup_keys = {role: {"public": shared, "private": keys[role]["private"]} for role in forge.ROLES}
    policy = _policy_file(
        tmp_path, run, payload, dup_keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="unique"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_duplicate_sealer_key_shared_with_producer_fails_closed(tmp_path: Path) -> None:
    """Attack: the sealer shares a key with a producer.  The seven producer
    keys must all be unique; at least the sealer must differ from every
    producer, so sharing fails closed at policy parse time."""
    run, payload, keys = _signed(tmp_path)
    dup_keys = dict(keys)
    dup_keys["sealer"] = {
        "public": keys["core"]["public"],
        "private": keys["core"]["private"],
    }
    policy = _policy_file(
        tmp_path, run, payload, dup_keys, approved_commits=(COMMIT,), approved_trees=(TREE,)
    )
    with pytest.raises(ConfigurationError, match="unique"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_post_approval_future_gateway_certificate_is_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attack: a gateway certificate whose valid_from is in the future must be
    rejected (valid_from <= now < valid_until)."""
    future = {
        "public_fingerprint": _digest(b"cert"),
        "issuer": _digest(b"issuer"),
        "san": "workload.gateway.omnibase",
        "valid_from": "2999-01-01T00:00:00Z",
        "valid_until": "2999-06-01T00:00:00Z",
        "revoked": False,
    }
    run, payload, keys = _signed(tmp_path, gateway_certificate=future)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    report = verify_joint_evidence(run, payload, trust_policy_path=policy)
    assert report.passed is False
    assert report.status == "blocked/not_proven"
    assert "certificate_posture" in report.blockers
    assert report.safety["certificate_posture"] == "not_proven"


def test_post_approval_executable_manifest_receipt_digest_drift_is_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Attack: any drift among the executable/manifest/receipt three-way
    digests must fail.  (a) the receipt declares a different executable digest
    (receipt and owning component re-signed, chain re-sealed): blocker.
    (b) the artifact manifest records a different digest for the executable:
    hard veto."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    assert verify_joint_evidence(run, payload, trust_policy_path=policy).passed is True
    refs = payload["commands"]["core_runner"]  # type: ignore[index]
    receipt = json.loads((run / str(refs["receipt"]["path"])).read_text())
    receipt["executable"]["sha256"] = "e" * 64
    _rewrite_signed_file(run, payload, refs, "receipt", receipt, keys, "core")
    # Re-attest the owning component's receipt binding to the new digest.
    component = json.loads((run / "components/core.json").read_text())
    component["receipts"]["core_runner"] = refs["receipt"]["sha256"]  # type: ignore[index]
    _rewrite_signed_file(
        run, payload, payload["components"]["core"], "evidence", component, keys, "core"
    )
    report = verify_joint_evidence(run, payload, trust_policy_path=policy)
    assert report.passed is False
    assert report.status == "blocked/not_proven"
    assert "artifact_provenance" in report.blockers
    # (b) manifest-side drift is a hard veto.
    run_b, payload_b, keys_b = _signed(tmp_path / "run-b")
    policy_b = _approved_policy(tmp_path, run_b, payload_b, keys_b)
    _approve_in_process(monkeypatch, policy_b)
    assert verify_joint_evidence(run_b, payload_b, trust_policy_path=policy_b).passed is True
    for entry in payload_b["artifact_manifest"]["files"]:  # type: ignore[index]
        if entry["path"] == "bin/core_runner":
            entry["sha256"] = "e" * 64
    with pytest.raises(ConfigurationError, match="drifted|bind"):
        verify_joint_evidence(run_b, payload_b, trust_policy_path=policy_b)


# ---------------------------------------------------------------------------
# P34.7 Integration Review-Fix Round 2 (P1-A): Git object format
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path = REPO_ROOT) -> str:
    result = subprocess.run(
        ["git", "--no-pager", *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed in {cwd}: {result.stderr.strip()}")
    return result.stdout.strip()


def _fresh_sha1_repo(tmp_path: Path) -> Path:
    """Create a real, fresh Git repository (SHA-1 object format) with one
    commit, so real 40-hex OIDs are always available even when the mounted
    worktree's ``.git`` file (which embeds a host path) is unreachable from
    inside the container."""
    repo = tmp_path / "git-repo"
    repo.mkdir(parents=True)
    for args in (
        ["init", "-q"],
        ["config", "user.email", "dev@omnibase.local"],
        ["config", "user.name", "OmniBase"],
        ["commit", "--allow-empty", "-q", "-m", "seed"],
    ):
        subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
    return repo


def _current_or_fresh_oids(tmp_path: Path) -> tuple[str, str, Path]:
    """Return ``(commit, tree, repo_root)`` from the current repository when
    git can reach it (host/CI), otherwise from a fresh real SHA-1 repository."""
    try:
        return _git(["rev-parse", "HEAD"]), _git(["rev-parse", "HEAD^{tree}"]), REPO_ROOT
    except RuntimeError:
        repo = _fresh_sha1_repo(tmp_path)
        return (
            _git(["rev-parse", "HEAD"], cwd=repo),
            _git(["rev-parse", "HEAD^{tree}"], cwd=repo),
            repo,
        )


def test_current_repo_object_format_is_sha1(tmp_path: Path) -> None:
    """The current Git repository is SHA-1: `git rev-parse
    --show-object-format` reports ``sha1``, so the real 40-hex commit/tree
    OIDs must be able to enter the joint-gate contract.  When the mounted
    worktree is unreachable from inside the container, the same assertion is
    proven against a fresh real SHA-1 repository."""
    try:
        fmt = _git(["rev-parse", "--show-object-format"])
    except RuntimeError:
        fmt = _git(["rev-parse", "--show-object-format"], cwd=_fresh_sha1_repo(tmp_path))
    assert fmt == "sha1"


def test_real_repo_sha1_oids_enter_the_chain_without_production_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """REAL 40-hex SHA-1 commit/tree OIDs (from the current repository, or a
    fresh real Git repository when the worktree is container-unreachable)
    parse into the envelope, the trust-policy source seal, the signed
    component evidence and the evidence seal.  Without an approved policy the
    report stays blocked/not_proven (no veto); with the in-process
    monkeypatch approval the same real-OID chain reaches ``passed``, proving
    the OIDs flow through the whole signature chain while the empty
    production pin keeps the gate closed."""
    commit, tree, _repo = _current_or_fresh_oids(tmp_path)
    assert len(commit) == 40
    assert len(tree) == 40
    run, payload, keys = _signed(tmp_path, source_commit=commit, source_tree=tree)
    policy = _policy_file(
        tmp_path, run, payload, keys, approved_commits=(commit,), approved_trees=(tree,)
    )
    report = verify_joint_evidence(run, payload, trust_policy_path=policy)
    assert report.status == "blocked/not_proven"
    assert "trust_policy_not_approved" in report.blockers
    _approve_in_process(monkeypatch, policy)
    passed = verify_joint_evidence(run, payload, trust_policy_path=policy)
    assert passed.passed is True
    assert passed.safety["source_provenance"] == "verified"


def test_sha1_declared_64_hex_oid_is_rejected(tmp_path: Path) -> None:
    """sha1 object format accepts exactly 40-hex OIDs; a 64-hex SHA-256-style
    identifier fails closed."""
    run, payload = _forge(tmp_path)
    payload["provenance"]["source_commit"] = "a" * 64  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="40-hex"):
        verify_joint_evidence(run, payload)


def test_sha256_declared_40_hex_oid_is_rejected(tmp_path: Path) -> None:
    """sha256 object format accepts exactly 64-hex OIDs; a 40-hex SHA-1-style
    identifier fails closed."""
    run, payload = _forge(tmp_path, git_object_format="sha256")
    payload["provenance"]["source_commit"] = "a" * 40  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="64-hex"):
        verify_joint_evidence(run, payload)


def test_unknown_git_object_format_is_rejected(tmp_path: Path) -> None:
    run, payload = _forge(tmp_path)
    payload["provenance"]["git_object_format"] = "md5"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="sha1' or 'sha256'"):
        verify_joint_evidence(run, payload)


def test_uppercase_git_oid_is_rejected(tmp_path: Path) -> None:
    """OIDs must be lowercase hex; uppercase characters fail closed."""
    run, payload = _forge(tmp_path)
    payload["provenance"]["source_commit"] = "A" * 40  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="40-hex"):
        verify_joint_evidence(run, payload)


def test_policy_evidence_object_format_drift_is_rejected(tmp_path: Path) -> None:
    """The trust-policy source seal declares sha256 while the evidence
    provenance declares sha1: policy/evidence object-format drift fails
    closed."""
    run, payload, keys = _signed(tmp_path)
    policy = _policy_file(
        tmp_path,
        run,
        payload,
        keys,
        approved_commits=("c" * 64,),
        approved_trees=("d" * 64,),
        git_object_format="sha256",
    )
    with pytest.raises(ConfigurationError, match="object format"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_component_object_format_drift_is_rejected(tmp_path: Path) -> None:
    """Component evidence must bind the SAME object format as the
    provenance; a component declaring sha256 in a sha1 run fails closed."""
    run, payload, keys = _signed(tmp_path)
    refs = payload["components"]["core"]  # type: ignore[index]
    component = json.loads((run / str(refs["evidence"]["path"])).read_text())
    component["git_object_format"] = "sha256"
    _rewrite_signed_file(run, payload, refs, "evidence", component, keys, "core")
    policy = _policy_file(tmp_path, run, payload, keys)
    with pytest.raises(ConfigurationError, match="object format"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_seal_binds_git_object_format(tmp_path: Path) -> None:
    """The evidence seal's canonical binding covers git_object_format: a seal
    signed over a different format can never match the outer bundle."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _bind_seal_over(run, payload, keys, policy, {"provenance": {"git_object_format": "sha256"}})
    with pytest.raises(ConfigurationError, match="binding_sha256"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


# ---------------------------------------------------------------------------
# P34.7 Integration Review-Fix Round 2 (P1-B): evidence freshness window
# ---------------------------------------------------------------------------


def test_expired_bundle_is_blocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A signed bundle whose evidence_valid_until has passed must never be
    treated as current: `evidence_issued_at <= now < evidence_valid_until`
    fails and evidence_freshness becomes a blocker."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    assert verify_joint_evidence(run, payload, trust_policy_path=policy).passed is True
    valid_until = datetime.fromisoformat(
        str(payload["evidence_valid_until"]).replace("Z", "+00:00")
    )
    late = valid_until + timedelta(seconds=1)
    report = verify_joint_evidence(run, payload, trust_policy_path=policy, now=late)
    assert report.passed is False
    assert report.status == "blocked/not_proven"
    assert "evidence_freshness" in report.blockers
    assert report.safety["evidence_freshness"] == "not_proven"


def test_future_issued_at_is_blocked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A bundle whose evidence_issued_at is in the future relative to ``now``
    must never be treated as current."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    issued_at = datetime.fromisoformat(str(payload["evidence_issued_at"]).replace("Z", "+00:00"))
    early = issued_at - timedelta(seconds=1)
    report = verify_joint_evidence(run, payload, trust_policy_path=policy, now=early)
    assert report.passed is False
    assert "evidence_freshness" in report.blockers


def test_evidence_age_over_policy_max_is_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Evidence older than the policy maximum is stale even inside the
    validity window: `max_evidence_age_seconds` bounds the age."""
    run, payload, keys = _signed(tmp_path)
    issued_at = datetime.fromisoformat(str(payload["evidence_issued_at"]).replace("Z", "+00:00"))
    payload["evidence_valid_until"] = _iso_utc(issued_at + timedelta(seconds=60))  # type: ignore[index]
    _resign_seal(run, payload, keys)
    policy = _policy_file(
        tmp_path,
        run,
        payload,
        keys,
        approved_commits=(COMMIT,),
        approved_trees=(TREE,),
        max_evidence_age_seconds=60,
    )
    _approve_in_process(monkeypatch, policy)
    report = verify_joint_evidence(run, payload, trust_policy_path=policy)
    assert report.passed is False
    assert "evidence_freshness" in report.blockers


def test_receipt_outside_run_window_is_rejected(tmp_path: Path) -> None:
    """Every command receipt must lie inside [run_started_at,
    run_completed_at]; a receipt that starts before the run fails closed."""
    run, payload, keys = _signed(tmp_path)
    run_started, _run_completed = _run_window(payload)
    refs = payload["commands"]["core_runner"]  # type: ignore[index]
    receipt = json.loads((run / str(refs["receipt"]["path"])).read_text())
    receipt["started_at"] = _iso_utc(run_started - timedelta(seconds=60))
    receipt["ended_at"] = _iso_utc(run_started - timedelta(seconds=30))
    _rewrite_signed_file(run, payload, refs, "receipt", receipt, keys, "core")
    policy = _policy_file(tmp_path, run, payload, keys)
    with pytest.raises(ConfigurationError, match="run window"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_validity_window_longer_than_policy_max_is_veto(tmp_path: Path) -> None:
    """A validity window longer than the policy maximum is a structural
    veto, never a pass."""
    run, payload, keys = _signed(tmp_path)
    policy = _policy_file(
        tmp_path,
        run,
        payload,
        keys,
        approved_commits=(COMMIT,),
        approved_trees=(TREE,),
        max_evidence_age_seconds=60,
    )
    with pytest.raises(ConfigurationError, match="validity window exceeds"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_outer_time_field_rewrite_without_resigning_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The evidence seal's canonical binding covers the full validity window:
    rewriting evidence_issued_at without re-signing must fail (a) on the
    recomputed binding and (b) via a seal signed over a different window."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    assert verify_joint_evidence(run, payload, trust_policy_path=policy).passed is True
    valid_until = datetime.fromisoformat(
        str(payload["evidence_valid_until"]).replace("Z", "+00:00")
    )
    payload["evidence_issued_at"] = _iso_utc(valid_until - timedelta(hours=1))  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="binding_sha256"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)
    run_completed = datetime.fromisoformat(str(payload["run_completed_at"]).replace("Z", "+00:00"))
    payload["evidence_issued_at"] = _iso_utc(run_completed + timedelta(seconds=60))  # type: ignore[index]
    _bind_seal_over(run, payload, keys, policy, {"evidence_issued_at": "2099-01-01T00:00:00Z"})
    with pytest.raises(ConfigurationError, match="binding_sha256"):
        verify_joint_evidence(run, payload, trust_policy_path=policy)


def test_policy_max_age_drift_is_rejected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A trust policy whose max_evidence_age_seconds differs from the approved
    anchor is a different policy: its bytes are not in the approved set, and
    its tighter maximum makes the bundle's validity window structurally
    invalid, so the drift is vetoed -- never a pass."""
    run, payload, keys = _signed(tmp_path)
    policy_approved = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy_approved)
    assert verify_joint_evidence(run, payload, trust_policy_path=policy_approved).passed is True
    drifted = _policy_file(
        tmp_path,
        run,
        payload,
        keys,
        name="trust-policy-drifted.json",
        approved_commits=(COMMIT,),
        approved_trees=(TREE,),
        max_evidence_age_seconds=60,
    )
    assert _digest(drifted.read_bytes()) != _digest(policy_approved.read_bytes())
    with pytest.raises(ConfigurationError, match="validity window exceeds"):
        verify_joint_evidence(run, payload, trust_policy_path=drifted)


def test_idempotent_offline_reverification_of_unexpired_bundle(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The same unexpired bundle verified twice at the same instant is
    idempotent; once its validity window has passed it must never be
    re-PASSed."""
    run, payload, keys = _signed(tmp_path)
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    clock = datetime.now(UTC)
    first = verify_joint_evidence(run, payload, trust_policy_path=policy, now=clock)
    second = verify_joint_evidence(run, payload, trust_policy_path=policy, now=clock)
    assert first.passed is True
    assert first.to_dict() == second.to_dict()
    valid_until = datetime.fromisoformat(
        str(payload["evidence_valid_until"]).replace("Z", "+00:00")
    )
    expired = verify_joint_evidence(
        run,
        payload,
        trust_policy_path=policy,
        now=valid_until + timedelta(seconds=1),
    )
    assert expired.passed is False
    assert "evidence_freshness" in expired.blockers


# ---------------------------------------------------------------------------
# P34.7 Integration Review-Fix Round 2 (P2): certificate exact-expiry boundary
# ---------------------------------------------------------------------------


def test_certificate_expires_exactly_at_now_is_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Boundary: the documented window is valid_from <= now < valid_until, so
    valid_until == now must fail closed (the certificate is already expired);
    one second earlier the same certificate still proves current posture."""
    exact = {
        "public_fingerprint": _digest(b"cert"),
        "issuer": _digest(b"issuer"),
        "san": "workload.gateway.omnibase",
        "valid_from": "2020-01-01T00:00:00Z",
        "valid_until": "2030-01-01T01:01:00Z",
        "revoked": False,
    }
    run, payload, keys = _signed(
        tmp_path,
        gateway_certificate=exact,
        run_started_at="2030-01-01T00:00:00Z",
        run_completed_at="2030-01-01T01:00:00Z",
        evidence_issued_at="2030-01-01T01:01:00Z",
        evidence_valid_until="2030-01-02T00:00:00Z",
    )
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    boundary = datetime(2030, 1, 1, 1, 1, tzinfo=UTC)
    report = verify_joint_evidence(run, payload, trust_policy_path=policy, now=boundary)
    assert report.passed is False
    assert "certificate_posture" in report.blockers
    assert report.safety["certificate_posture"] == "not_proven"
    before = boundary - timedelta(seconds=1)
    ok = verify_joint_evidence(run, payload, trust_policy_path=policy, now=before)
    assert ok.safety["certificate_posture"] == "verified"


def test_certificate_valid_from_exactly_now_is_allowed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Boundary: valid_from == now is an allowed state (valid_from <= now);
    the certificate proves current posture at the exact first valid instant."""
    exact = {
        "public_fingerprint": _digest(b"cert"),
        "issuer": _digest(b"issuer"),
        "san": "workload.gateway.omnibase",
        "valid_from": "2030-01-01T01:01:00Z",
        "valid_until": "2099-01-01T00:00:00Z",
        "revoked": False,
    }
    run, payload, keys = _signed(
        tmp_path,
        gateway_certificate=exact,
        run_started_at="2030-01-01T00:00:00Z",
        run_completed_at="2030-01-01T01:00:00Z",
        evidence_issued_at="2030-01-01T01:01:00Z",
        evidence_valid_until="2030-01-02T00:00:00Z",
    )
    policy = _approved_policy(tmp_path, run, payload, keys)
    _approve_in_process(monkeypatch, policy)
    boundary = datetime(2030, 1, 1, 1, 1, tzinfo=UTC)
    report = verify_joint_evidence(run, payload, trust_policy_path=policy, now=boundary)
    assert report.passed is True
    assert report.safety["certificate_posture"] == "verified"
