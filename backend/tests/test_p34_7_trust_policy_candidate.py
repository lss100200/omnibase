"""Tests for the P34.7 Trust Policy Candidate R0 governance contract.

The candidate validator must never approve anything: the highest positive
status is ``candidate/valid_not_approved``, ``production_approved`` and
``activation_allowed`` stay ``False``, and validating a candidate NEVER
writes into ``joint_gate._APPROVED_TRUST_POLICY_SHA256``.  The suite covers
the full negative matrix (missing/eighth roles, duplicate/zero keys, secret
shapes, wildcard and out-of-role scopes, Git object-format drift, digest
drift, lifecycle/rotation/revocation violations, approval-packet violations,
path/link attacks, migration and feature-gate posture) plus the positive
proofs (seven unique roles, real SHA-1 main commit/tree in the source seal,
digest consistency, identity separation, candidate lifecycle, and the P34.7
production Gate remaining blocked/not_proven).
"""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from omnibase.production import joint_gate as jg
from omnibase.production.composition import ConfigurationError
from omnibase.production.trust_policy_candidate import (
    CANDIDATE_SCHEMA,
    _detect_replacement_cycles,
    scan_forbidden_secrets,
    validate_trust_policy_candidate,
    validate_trust_policy_candidate_files,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

_ROLES = ("core", "runner", "broker", "gateway", "overlay", "recovery_sla", "sealer")
_SCOPES = {
    "core": ["core_runtime_posture", "core_runner_request_identity"],
    "runner": ["linux_runner_isolation", "runner_command_receipt", "runner_attack_matrix"],
    "broker": ["broker_namespace", "broker_identity", "broker_budget_replay"],
    "gateway": ["gateway_mtls", "gateway_certificate", "gateway_capability_boundary"],
    "overlay": ["overlay_membership", "overlay_derp", "overlay_node_compromise"],
    "recovery_sla": ["provider_recovery", "capacity_fault_injection", "sla_measurement"],
    "sealer": ["evidence_seal", "cleanup_inventory"],
}
_COMMANDS = (
    "core_runner",
    "runner_broker",
    "runner_gateway",
    "broker_gateway",
    "overlay_data_plane",
    "recovery_sla",
)


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _public_key(role: str) -> str:
    index = _ROLES.index(role) + 1
    return f"00000000000000000000000000000000000000000000000000000000000000{index:02d}"


def _key_dict(role: str, **overrides: object) -> dict[str, object]:
    public_key = _public_key(role)
    entry: dict[str, object] = {
        "key_id": f"key-{role}-001",
        "role": role,
        "algorithm": "ed25519",
        "public_key": public_key,
        "fingerprint_sha256": _digest(bytes.fromhex(public_key)),
        "owner_id": f"owner-{role}",
        "backup_owner_id": f"backup-owner-{role}",
        "created_at": "2026-08-08T00:00:00Z",
        "candidate_from": "2026-08-08T00:00:00Z",
        "planned_expiry": "2027-08-08T00:00:00Z",
        "lifecycle_state": "candidate",
        "custody_kind": "operator_offline",
        "allowed_signing_scopes": _SCOPES[role],
        "replaces_key_id": None,
        "revocation_record_id": None,
    }
    entry.update(overrides)
    return entry


def _producer_dict(role: str) -> dict[str, object]:
    return {
        "role": role,
        "owner_id": f"owner-{role}",
        "backup_owner_id": f"backup-owner-{role}",
        "keys": [_key_dict(role)],
        "allowed_signing_scopes": _SCOPES[role],
    }


def _candidate_dict(**overrides: object) -> dict[str, object]:
    candidate: dict[str, object] = {
        "schema": CANDIDATE_SCHEMA,
        "schema_version": "1",
        "policy_id": "p34-7-trust-policy-r0-candidate-test",
        "lifecycle_state": "candidate",
        "candidate_only": True,
        "production_approved": False,
        "created_at": "2026-08-08T00:00:00Z",
        "author_id": "policy-author-1",
        "producers": {role: _producer_dict(role) for role in _ROLES},
        "source_seal": {
            "repository": "https://github.com/lss100200/omnibase.git",
            "git_object_format": "sha1",
            "approved_commits": ["a" * 40],
            "approved_trees": ["b" * 40],
            "candidate_only": True,
            "production_approved": False,
        },
        "artifact_approvals": {
            f"bin/{command}": {"path": f"bin/{command}", "sha256": "1" * 64, "commands": [command]}
            for command in _COMMANDS
        },
        "commands": {
            command: {"command": command, "argv": [f"/run/omnibase/bin/{command}", "--probe"]}
            for command in _COMMANDS
        },
        "allowed_env_names": ["PATH", "OMNIBASE_RUN_ID"],
        "gateway": {
            "issuer": "0" * 64,
            "san_suffix": ".omnibase",
            "validity_seconds": 3153600000,
        },
        "evidence_freshness": {"max_evidence_age_seconds": 604800},
        "rotation_plan": {"entries": []},
        "revocation_records": [],
        "supersession": None,
    }
    candidate.update(overrides)
    return candidate


def _packet_dict(candidate: dict[str, object], **overrides: object) -> dict[str, object]:
    producers = candidate["producers"]  # type: ignore[index]
    fingerprints = [
        str(producers[role]["keys"][0]["fingerprint_sha256"])  # type: ignore[index]
        for role in _ROLES
    ]
    packet: dict[str, object] = {
        "schema": "omnibase.p34-7.trust-policy-approval-packet.v1",
        "schema_version": "1",
        "candidate_policy_path": "deployment/production/p34-7-trust-policy-candidate.example.json",
        "candidate_policy_raw_sha256": "0" * 64,
        "candidate_schema": CANDIDATE_SCHEMA,
        "candidate_schema_version": "1",
        "repository": "https://github.com/lss100200/omnibase.git",
        "git_object_format": "sha1",
        "candidate_commits": list(candidate["source_seal"]["approved_commits"]),  # type: ignore[index]
        "candidate_trees": list(candidate["source_seal"]["approved_trees"]),  # type: ignore[index]
        "producer_key_fingerprints": fingerprints,
        "artifact_manifest_sha256": _digest(
            _canonical(candidate["artifact_approvals"])  # type: ignore[index]
        ),
        "command_templates_sha256": _digest(
            _canonical(candidate["commands"])  # type: ignore[index]
        ),
        "env_allowlist_sha256": _digest(
            _canonical(candidate["allowed_env_names"])  # type: ignore[index]
        ),
        "gateway_policy_sha256": _digest(
            _canonical(candidate["gateway"])  # type: ignore[index]
        ),
        "max_evidence_age_seconds": candidate["evidence_freshness"][  # type: ignore[index]
            "max_evidence_age_seconds"
        ],
        "author_id": "policy-author-1",
        "reviewer_ids": ["reviewer-alice", "reviewer-bob"],
        "review_started_at": "2026-08-08T01:00:00Z",
        "review_completed_at": "2026-08-08T02:00:00Z",
        "decision": "candidate",
        "decision_reason": "R0 rehearsal: contract and separation validated; not approved.",
        "supersedes_policy_sha256": None,
        "rollback_policy_sha256": None,
    }
    packet.update(overrides)
    return packet


def _validate(candidate: dict[str, object], packet: dict[str, object]):
    return validate_trust_policy_candidate(candidate, packet, REPO_ROOT)


def _write_canonical(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical(value))


def _valid_pair() -> tuple[dict[str, object], dict[str, object]]:
    candidate = _candidate_dict()
    packet = _packet_dict(candidate)
    packet["candidate_policy_raw_sha256"] = _digest(_canonical(candidate))
    return candidate, packet


# ---------------------------------------------------------------------------
# Positive proofs
# ---------------------------------------------------------------------------


def test_seven_roles_unique_keys_positive() -> None:
    candidate, packet = _valid_pair()
    report = _validate(candidate, packet)
    assert report.status == "candidate/valid_not_approved"
    assert report.role_set_verified is True
    assert report.key_uniqueness_verified is True
    assert report.contract_valid is True


def test_example_files_validate() -> None:
    """The checked-in candidate and approval-packet examples are valid,
    candidate-only and bind the current main merge commit (sha1 OIDs)."""
    report = validate_trust_policy_candidate_files(
        REPO_ROOT / "deployment" / "production" / "p34-7-trust-policy-candidate.example.json",
        REPO_ROOT / "deployment" / "production" / "p34-7-trust-policy-approval-packet.example.json",
        REPO_ROOT,
    )
    assert report.status == "candidate/valid_not_approved"
    assert report.production_approved is False
    assert report.approved_digest_written is False
    assert report.activation_allowed is False
    assert report.migration_head == "0012"
    assert report.migration_0013_created is False
    assert report.feature_gates == {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }


def test_real_main_sha1_commit_tree_enter_candidate_seal(tmp_path: Path) -> None:
    """Real 40-hex SHA-1 main commit/tree OIDs enter the candidate source seal
    and the approval packet.  On hosts where git can reach the current
    repository the actual main OIDs are used; inside a container whose mounted
    worktree is git-unreachable a fresh real SHA-1 repository proves the same
    assertion."""
    try:
        commit = subprocess.run(
            ["git", "--no-pager", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "--no-pager", "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            check=True,
            cwd=REPO_ROOT,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        repo = tmp_path / "git-fixture"
        repo.mkdir(parents=True)
        for args in (
            ["init", "-q"],
            ["config", "user.email", "dev@omnibase.local"],
            ["config", "user.name", "OmniBase"],
            ["commit", "--allow-empty", "-q", "-m", "seed"],
        ):
            subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)
        commit = subprocess.run(
            ["git", "--no-pager", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo,
        ).stdout.strip()
        tree = subprocess.run(
            ["git", "--no-pager", "rev-parse", "HEAD^{tree}"],
            capture_output=True,
            text=True,
            check=True,
            cwd=repo,
        ).stdout.strip()
    assert len(commit) == 40
    assert len(tree) == 40
    candidate = _candidate_dict()
    candidate["source_seal"]["approved_commits"] = [commit]  # type: ignore[index]
    candidate["source_seal"]["approved_trees"] = [tree]  # type: ignore[index]
    packet = _packet_dict(candidate)
    packet["candidate_policy_raw_sha256"] = _digest(_canonical(candidate))
    report = _validate(candidate, packet)
    assert report.status == "candidate/valid_not_approved"
    assert report.source_seal_verified is True


def test_digest_consistency_positive() -> None:
    candidate, packet = _valid_pair()
    report = _validate(candidate, packet)
    assert report.candidate_digest_verified is True


def test_identity_separation_positive() -> None:
    candidate, packet = _valid_pair()
    report = _validate(candidate, packet)
    assert report.author_reviewer_separation_verified is True
    assert report.producer_approver_separation_verified is True


def test_candidate_lifecycle_and_never_approved_positive() -> None:
    candidate, packet = _valid_pair()
    report = _validate(candidate, packet)
    assert report.lifecycle_valid is True
    assert report.production_approved is False
    assert report.approved_digest_written is False
    assert report.activation_allowed is False
    assert report.root_env_accessed is False
    assert report.business_database_accessed is False
    assert report.business_database_migrated is False
    assert report.runtime_activated is False


def test_production_gate_stays_blocked_with_candidate_round(tmp_path: Path) -> None:
    """The candidate round never opens the P34.7 production gate: verifying a
    forged bundle against a structurally consistent but UNAPPROVED trust
    policy stays blocked/not_proven (no approved digest exists)."""
    import importlib.util

    forger_path = REPO_ROOT / "scripts" / "production" / "forge_p34_7_evidence_bundle.py"
    spec = importlib.util.spec_from_file_location("forge_p34_7_evidence_bundle", forger_path)
    assert spec is not None
    assert spec.loader is not None
    forge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(forge)
    run = tmp_path / "run"
    payload = forge.forge_bundle(run)
    executables: dict[str, object] = {}
    commands: dict[str, object] = {}
    for name, refs in payload["commands"].items():  # type: ignore[union-attr]
        receipt = json.loads((run / str(refs["receipt"]["path"])).read_text())
        executables[receipt["executable"]["path"]] = {  # type: ignore[index]
            "sha256": receipt["executable"]["sha256"],
            "commands": [name],
        }
        commands[name] = receipt["argv"]
    gateway = json.loads((run / "components/gateway.json").read_text())
    certificate = gateway["gateway"]["certificate"]
    policy = {
        "schema": "omnibase.p34-7.trust-policy.v1",
        "schema_version": "2",
        "producers": {role: {"ed25519_public_key": _public_key(role)} for role in _ROLES},
        "source_seal": {
            "repository": "https://github.com/lss100200/omnibase.git",
            "git_object_format": "sha1",
            "approved_commits": [payload["provenance"]["source_commit"]],  # type: ignore[index]
            "approved_trees": [payload["provenance"]["source_tree"]],  # type: ignore[index]
        },
        "executables": executables,
        "commands": commands,
        "allowed_env_names": ["PATH", "OMNIBASE_RUN_ID"],
        "gateway": {
            "issuer": certificate["issuer"],
            "san_suffix": ".omnibase",
            "validity_seconds": 3153600000,
        },
        "max_evidence_age_seconds": 604800,
        "migration_head": "0012",
    }
    policy_path = tmp_path / "gate-policy.json"
    _write_canonical(policy_path, policy)
    assert frozenset() == jg._APPROVED_TRUST_POLICY_SHA256
    report = jg.verify_joint_evidence(run, payload, trust_policy_path=policy_path)
    assert report.status == "blocked/not_proven"
    assert report.passed is False
    assert "trust_policy_not_approved" in report.blockers


# ---------------------------------------------------------------------------
# Negative matrix
# ---------------------------------------------------------------------------


def test_missing_producer_is_rejected() -> None:
    candidate, packet = _valid_pair()
    del candidate["producers"]["runner"]  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="exactly the seven roles"):
        _validate(candidate, packet)


def test_eighth_unknown_producer_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["auditor"] = {  # type: ignore[index]
        "role": "auditor",
        "owner_id": "owner-auditor",
        "backup_owner_id": None,
        "keys": [
            {
                **_key_dict("core"),
                "key_id": "key-auditor-001",
                "role": "auditor",
                "owner_id": "owner-auditor",
            }
        ],
        "allowed_signing_scopes": ["evidence_seal"],
    }
    with pytest.raises(ConfigurationError, match="exactly the seven roles"):
        _validate(candidate, packet)


def test_duplicate_producer_key_is_rejected() -> None:
    candidate, packet = _valid_pair()
    dup = copy.deepcopy(candidate["producers"]["core"]["keys"][0])  # type: ignore[index]
    dup["role"] = "runner"
    dup["key_id"] = "key-runner-dup"
    dup["allowed_signing_scopes"] = _SCOPES["runner"]
    candidate["producers"]["runner"]["keys"] = [dup]  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="duplicate public key"):
        _validate(candidate, packet)


def test_sealer_shares_key_with_producer_is_rejected() -> None:
    candidate, packet = _valid_pair()
    dup = copy.deepcopy(candidate["producers"]["core"]["keys"][0])  # type: ignore[index]
    dup["role"] = "sealer"
    dup["key_id"] = "key-sealer-dup"
    dup["allowed_signing_scopes"] = _SCOPES["sealer"]
    candidate["producers"]["sealer"]["keys"] = [dup]  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="duplicate public key|sealer must not share"):
        _validate(candidate, packet)


def test_all_zero_public_key_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["keys"][0]["public_key"] = "0" * 64  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="all-zero"):
        _validate(candidate, packet)


@pytest.mark.parametrize(
    "bad_key",
    ["A" * 64, "a" * 32, "a" * 64 + "0", "z" * 64, "a" * 63],
)
def test_malformed_public_key_is_rejected(bad_key: str) -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["keys"][0]["public_key"] = bad_key  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="64-hex lowercase"):
        _validate(candidate, packet)


def test_private_key_field_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["keys"][0]["private_key"] = "f" * 64  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="forbidden secret-shaped"):
        _validate(candidate, packet)


def test_camelcase_private_key_field_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["keys"][0]["privateKey"] = "f" * 64  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="forbidden secret-shaped"):
        _validate(candidate, packet)


def test_nested_signing_seed_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["keys"][0]["backup"] = {  # type: ignore[index]
        "signingSeed": "f" * 64
    }
    with pytest.raises(ConfigurationError, match="forbidden secret-shaped"):
        _validate(candidate, packet)


@pytest.mark.parametrize("field", ["api_key", "bearer_token", "password"])
def test_secret_credential_fields_are_rejected(field: str) -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["keys"][0][field] = "leaked"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="forbidden secret-shaped"):
        _validate(candidate, packet)


@pytest.mark.parametrize("scope", ["*", "**", "*.*", "any", "all"])
def test_wildcard_signing_scope_is_rejected(scope: str) -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["allowed_signing_scopes"] = [scope]  # type: ignore[index]
    candidate["producers"]["core"]["keys"][0]["allowed_signing_scopes"] = [scope]  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="wildcard|unknown signing scope"):
        _validate(candidate, packet)


def test_producer_out_of_role_scope_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["allowed_signing_scopes"] = [  # type: ignore[index]
        "core_runtime_posture",
        "linux_runner_isolation",
    ]
    candidate["producers"]["core"]["keys"][0]["allowed_signing_scopes"] = [  # type: ignore[index]
        "core_runtime_posture",
        "linux_runner_isolation",
    ]
    with pytest.raises(ConfigurationError, match="may only declare exactly"):
        _validate(candidate, packet)


def test_unknown_git_object_format_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["source_seal"]["git_object_format"] = "md5"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="'sha1' or 'sha256'"):
        _validate(candidate, packet)


def test_sha1_declared_64_hex_oid_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["source_seal"]["approved_commits"] = ["a" * 64]  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="40-hex"):
        _validate(candidate, packet)


def test_sha256_declared_40_hex_oid_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["source_seal"]["git_object_format"] = "sha256"  # type: ignore[index]
    candidate["source_seal"]["approved_commits"] = ["a" * 40]  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="64-hex"):
        _validate(candidate, packet)


def test_uppercase_git_oid_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["source_seal"]["approved_commits"] = ["A" * 40]  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="40-hex"):
        _validate(candidate, packet)


def test_candidate_raw_digest_drift_is_rejected(tmp_path: Path) -> None:
    candidate, packet = _valid_pair()
    candidate_path = tmp_path / "candidate.json"
    packet_path = tmp_path / "packet.json"
    _write_canonical(candidate_path, candidate)
    packet["candidate_policy_raw_sha256"] = _digest(_canonical(candidate))
    _write_canonical(packet_path, packet)
    # Rewrite the candidate with a drifted field; the packet still pins the
    # OLD raw digest.
    candidate["policy_id"] = "drifted-policy-id"
    _write_canonical(candidate_path, candidate)
    with pytest.raises(ConfigurationError, match="does not match the candidate raw bytes"):
        validate_trust_policy_candidate_files(candidate_path, packet_path, REPO_ROOT)


def test_source_commit_tree_drift_is_rejected() -> None:
    candidate, packet = _valid_pair()
    packet["candidate_commits"] = ["c" * 40]
    with pytest.raises(ConfigurationError, match="must match the source seal"):
        _validate(candidate, packet)


def test_unknown_key_lifecycle_state_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["keys"][0]["lifecycle_state"] = "expired"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="lifecycle_state is unknown"):
        _validate(candidate, packet)


def test_active_key_in_candidate_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["keys"][0]["lifecycle_state"] = "active"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="R0 pre-approval set"):
        _validate(candidate, packet)


def test_candidate_to_active_transition_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["rotation_plan"]["entries"] = [  # type: ignore[index]
        {
            "key_id": "key-core-001",
            "role": "core",
            "from_state": "candidate",
            "to_state": "active",
            "planned_at": "2026-08-09T00:00:00Z",
            "replaces_key_id": None,
        }
    ]
    with pytest.raises(ConfigurationError, match="illegal lifecycle transition"):
        _validate(candidate, packet)


def test_revoked_to_active_transition_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["rotation_plan"]["entries"] = [  # type: ignore[index]
        {
            "key_id": "key-core-001",
            "role": "core",
            "from_state": "revoked",
            "to_state": "active",
            "planned_at": "2026-08-09T00:00:00Z",
            "replaces_key_id": None,
        }
    ]
    with pytest.raises(ConfigurationError, match="illegal lifecycle transition"):
        _validate(candidate, packet)


def test_replacement_self_cycle_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["rotation_plan"]["entries"] = [  # type: ignore[index]
        {
            "key_id": "key-core-001",
            "role": "core",
            "from_state": "active",
            "to_state": "rotating",
            "planned_at": "2026-08-09T00:00:00Z",
            "replaces_key_id": "key-core-001",
        }
    ]
    with pytest.raises(ConfigurationError, match="cannot replace itself"):
        _validate(candidate, packet)


def test_multi_key_rotation_cycle_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="rotation cycle"):
        _detect_replacement_cycles({"key-a": "key-b", "key-b": "key-a"})
    with pytest.raises(ConfigurationError, match="rotation cycle"):
        _detect_replacement_cycles({"key-a": "key-b", "key-b": "key-c", "key-c": "key-a"})


def test_cross_role_replacement_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["rotation_plan"]["entries"] = [  # type: ignore[index]
        {
            "key_id": "key-core-001",
            "role": "core",
            "from_state": "active",
            "to_state": "rotating",
            "planned_at": "2026-08-09T00:00:00Z",
            "replaces_key_id": "key-runner-001",
        }
    ]
    with pytest.raises(ConfigurationError, match="same role"):
        _validate(candidate, packet)


def test_author_self_review_is_rejected() -> None:
    candidate, packet = _valid_pair()
    packet["reviewer_ids"] = ["policy-author-1", "reviewer-bob"]
    with pytest.raises(ConfigurationError, match="self-approval|their own reviewer"):
        _validate(candidate, packet)


def test_duplicate_reviewer_is_rejected() -> None:
    candidate, packet = _valid_pair()
    packet["reviewer_ids"] = ["reviewer-alice", "reviewer-alice"]
    with pytest.raises(ConfigurationError, match="must not repeat"):
        _validate(candidate, packet)


def test_producer_owner_as_approver_is_rejected() -> None:
    candidate, packet = _valid_pair()
    packet["reviewer_ids"] = ["owner-core", "reviewer-bob"]
    with pytest.raises(ConfigurationError, match="producer owner cannot be an approver"):
        _validate(candidate, packet)


def test_empty_decision_reason_is_rejected() -> None:
    candidate, packet = _valid_pair()
    packet["decision_reason"] = ""
    with pytest.raises(ConfigurationError, match="non-empty string"):
        _validate(candidate, packet)


def test_unknown_decision_is_rejected() -> None:
    candidate, packet = _valid_pair()
    packet["decision"] = "finalized"
    with pytest.raises(ConfigurationError, match="R0 decision set"):
        _validate(candidate, packet)


@pytest.mark.parametrize(
    "decision", ["approved", "approved_for_production", "production_ready", "passed", "published"]
)
def test_production_approving_decisions_are_rejected(decision: str) -> None:
    candidate, packet = _valid_pair()
    packet["decision"] = decision
    with pytest.raises(ConfigurationError, match="R0 decision set|forbidden in R0"):
        _validate(candidate, packet)


def test_approval_packet_embedding_trust_root_is_rejected() -> None:
    candidate, packet = _valid_pair()
    packet["producers"] = {"core": {"ed25519_public_key": _public_key("core")}}
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        _validate(candidate, packet)


def test_candidate_embedding_approval_packet_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["approval_packet"] = {"decision": "candidate"}
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        _validate(candidate, packet)


def test_symlink_candidate_is_rejected(tmp_path: Path) -> None:
    candidate, packet = _valid_pair()
    real = tmp_path / "real-candidate.json"
    _write_canonical(real, candidate)
    link = tmp_path / "link-candidate.json"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation unavailable")
    packet_path = tmp_path / "packet.json"
    _write_canonical(packet_path, packet)
    with pytest.raises(ConfigurationError, match="link or reparse point"):
        validate_trust_policy_candidate_files(link, packet_path, REPO_ROOT)


def test_candidate_path_traversal_is_rejected(tmp_path: Path) -> None:
    candidate, packet = _valid_pair()
    packet_path = tmp_path / "packet.json"
    _write_canonical(packet_path, packet)
    with pytest.raises(ConfigurationError, match="parent traversal"):
        validate_trust_policy_candidate_files(
            tmp_path / ".." / "escape.json", packet_path, REPO_ROOT
        )


def test_approval_packet_path_traversal_is_rejected(tmp_path: Path) -> None:
    candidate, packet = _valid_pair()
    candidate_path = tmp_path / "candidate.json"
    _write_canonical(candidate_path, candidate)
    with pytest.raises(ConfigurationError, match="parent traversal"):
        validate_trust_policy_candidate_files(
            candidate_path, tmp_path / ".." / "escape.json", REPO_ROOT
        )


def test_root_env_locator_value_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["env_locator"] = "./.env"
    with pytest.raises(ConfigurationError, match="root .env locator|forbidden secret-shaped"):
        _validate(candidate, packet)


def test_root_env_named_field_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["producers"]["core"]["keys"][0]["root_env"] = "unused"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="forbidden secret-shaped"):
        _validate(candidate, packet)


def test_migration_head_0013_is_rejected(tmp_path: Path) -> None:
    """A repository whose migration head is 0013 must veto the candidate."""
    candidate, packet = _valid_pair()
    fake_repo = tmp_path / "fake-repo"
    versions = fake_repo / "backend" / "src" / "omnibase" / "migrations" / "versions"
    versions.mkdir(parents=True)
    (versions / "0012_user_profiles_provider_credentials.py").write_text(
        "revision = '0012'\ndown_revision = None\n", encoding="utf-8"
    )
    (versions / "0013_bad_migration.py").write_text(
        "revision = '0013'\ndown_revision = '0012'\n", encoding="utf-8"
    )
    with pytest.raises(ConfigurationError, match="migration head must remain 0012|0013"):
        validate_trust_policy_candidate(candidate, packet, fake_repo)


def test_phase5_feature_gate_true_is_rejected() -> None:
    candidate, packet = _valid_pair()
    candidate["feature_gates"] = {"agent_runtime_enabled": True}
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        _validate(candidate, packet)


def test_approved_digest_injection_never_lands_in_production_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An in-process injection into _APPROVED_TRUST_POLICY_SHA256 is scoped to
    the test: the candidate validator never reads or writes it, and teardown
    restores the empty production set."""
    candidate, packet = _valid_pair()
    injected = "e" * 64
    monkeypatch.setattr(jg, "_APPROVED_TRUST_POLICY_SHA256", frozenset({injected}))
    report = _validate(candidate, packet)
    assert report.approved_digest_written is False
    assert report.status == "candidate/valid_not_approved"
    assert frozenset({injected}) == jg._APPROVED_TRUST_POLICY_SHA256
    # Teardown restores the committed empty set; the report never claimed an
    # approval or a digest write.


def test_unknown_fields_fail_closed() -> None:
    candidate, packet = _valid_pair()
    candidate["mystery_field"] = True
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        _validate(candidate, packet)
    candidate, packet = _valid_pair()
    packet["mystery_field"] = True
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        _validate(candidate, packet)


def test_forbidden_secret_scan_is_recursive_and_case_insensitive() -> None:
    payload = {"outer": {"inner": {"Mnemonic": "x"}, "privateKeyHex": "y"}, "api-key": "z"}
    with pytest.raises(ConfigurationError, match="forbidden secret-shaped"):
        scan_forbidden_secrets(payload)
    with pytest.raises(ConfigurationError):
        scan_forbidden_secrets({"a": [{"seed": "x"}]})


def test_production_pin_stays_empty_after_full_validation() -> None:
    candidate, packet = _valid_pair()
    _validate(candidate, packet)
    assert frozenset() == jg._APPROVED_TRUST_POLICY_SHA256
