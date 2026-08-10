from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from omnibase.production.composition import ConfigurationError
from omnibase.production.trust_policy_r1_assignment import (
    BLOCKER_REQUIREMENTS,
    REQUIRED_RESOURCE_KINDS,
    validate_trust_policy_r1_assignment,
    validate_trust_policy_r1_assignment_file,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = REPO_ROOT / "deployment" / "production" / "p34-7-trust-policy-r1-assignment.example.json"


def _payload() -> dict[str, object]:
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _verified(identity: str, subject: str, marker: str) -> dict[str, object]:
    return {
        "identity_id": identity,
        "assignment_state": "VERIFIED",
        "canonical_subject_id": subject,
        "authentication_kind": "ED25519_PUBLIC_KEY",
        "authentication_reference_sha256": marker * 64,
    }


def _authorities(payload: dict[str, object]) -> dict[str, object]:
    value = payload["authority_assignments"]
    assert isinstance(value, dict)
    return value


def _inventory(payload: dict[str, object]) -> list[dict[str, object]]:
    value = payload["environment_inventory"]
    assert isinstance(value, list)
    return value  # type: ignore[return-value]


def _blockers(payload: dict[str, object]) -> list[dict[str, object]]:
    value = payload["blocker_assignments"]
    assert isinstance(value, list)
    return value  # type: ignore[return-value]


def _validate(payload: dict[str, object]):
    return validate_trust_policy_r1_assignment(payload, REPO_ROOT)


def test_example_is_valid_but_explicitly_incomplete() -> None:
    report = validate_trust_policy_r1_assignment_file(EXAMPLE, REPO_ROOT)
    assert report.contract_valid is True
    assert report.status == "r1_assignment/valid_incomplete"
    assert report.assignment_complete is False
    assert report.custody_assignment_complete is False
    assert report.environment_inventory_complete is False
    assert report.production_blockers_closed is False
    assert report.trust_policy_approved is False
    assert report.approved_digest_written is False
    assert report.activation_allowed is False
    assert report.migration_head == "0012"
    assert report.migration_0013_created is False
    assert report.feature_gates == {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }
    assert report.to_dict()["p34_7_production_total_gate"] == "blocked/not_proven"


def test_contract_has_exact_resource_and_blocker_closed_sets() -> None:
    payload = _payload()
    assert {item["resource_kind"] for item in _inventory(payload)} == set(REQUIRED_RESOURCE_KINDS)
    assert {item["blocker_id"] for item in _blockers(payload)} == set(BLOCKER_REQUIREMENTS)


@pytest.mark.parametrize(
    "field",
    [
        "trust_policy_approved",
        "approved_digest_written",
        "key_ceremony_authorized",
        "production_evidence_authorized",
        "activation_allowed",
        "migration_0013_created",
    ],
)
def test_r1a_cannot_claim_authorized_or_approved_state(field: str) -> None:
    payload = _payload()
    payload[field] = True
    with pytest.raises(ConfigurationError, match=field):
        _validate(payload)


@pytest.mark.parametrize(
    "gate", ["agent_runtime_enabled", "agent_planner_enabled", "multi_agent_enabled"]
)
def test_feature_gates_must_remain_false(gate: str) -> None:
    payload = _payload()
    gates = payload["feature_gates"]
    assert isinstance(gates, dict)
    gates[gate] = True
    with pytest.raises(ConfigurationError, match="Feature Gates"):
        _validate(payload)


def test_unknown_and_missing_top_level_fields_fail_closed() -> None:
    unknown = _payload()
    unknown["ready"] = True
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        _validate(unknown)
    missing = _payload()
    del missing["authority_assignments"]
    with pytest.raises(ConfigurationError):
        _validate(missing)


def test_exactly_two_reviewers_and_observers_are_required() -> None:
    payload = _payload()
    authorities = _authorities(payload)
    reviewers = authorities["policy_reviewers"]
    assert isinstance(reviewers, list)
    reviewers.pop()
    with pytest.raises(ConfigurationError, match="exactly 2"):
        _validate(payload)

    payload = _payload()
    authorities = _authorities(payload)
    observers = authorities["ceremony_observers"]
    assert isinstance(observers, list)
    observers.append(copy.deepcopy(observers[0]))
    with pytest.raises(ConfigurationError, match="exactly 2"):
        _validate(payload)


def test_unassigned_marker_cannot_claim_identity_or_authentication() -> None:
    payload = _payload()
    author = _authorities(payload)["policy_author"]
    assert isinstance(author, dict)
    author["identity_id"] = "fake-author"
    with pytest.raises(ConfigurationError, match="must not claim a real identity"):
        _validate(payload)

    payload = _payload()
    author = _authorities(payload)["policy_author"]
    assert isinstance(author, dict)
    author["authentication_reference_sha256"] = "a" * 64
    with pytest.raises(ConfigurationError, match="must not claim authentication"):
        _validate(payload)


def test_verified_identity_requires_authentication_evidence() -> None:
    payload = _payload()
    author = _authorities(payload)["policy_author"]
    assert isinstance(author, dict)
    author.update(_verified("author-a", "subject-a", "a"))
    author["authentication_reference_sha256"] = None
    with pytest.raises(ConfigurationError, match="VERIFIED requires authentication evidence"):
        _validate(payload)


def test_reviewers_cannot_alias_subject_or_authentication_key() -> None:
    payload = _payload()
    reviewers = _authorities(payload)["policy_reviewers"]
    assert isinstance(reviewers, list)
    reviewers[0] = _verified("reviewer-a", "reviewer-subject", "a")
    reviewers[1] = _verified("reviewer-b", "reviewer-subject", "b")
    with pytest.raises(ConfigurationError, match="reviewers must be independent"):
        _validate(payload)

    payload = _payload()
    reviewers = _authorities(payload)["policy_reviewers"]
    assert isinstance(reviewers, list)
    reviewers[0] = _verified("reviewer-a", "reviewer-subject-a", "a")
    reviewers[1] = _verified("reviewer-b", "reviewer-subject-b", "a")
    with pytest.raises(ConfigurationError, match="reviewers must be independent"):
        _validate(payload)


@pytest.mark.parametrize(
    ("left_path", "right_path", "message"),
    [
        (("policy_author",), ("policy_reviewers", 0), "author and reviewers"),
        (("policy_reviewers", 0), ("producer_owners", "core"), "reviewers and producer"),
        (
            ("producer_owners", "core"),
            ("producer_backup_owners", "runner"),
            "producer owners and backup",
        ),
        (("ceremony_operator",), ("ceremony_observers", 0), "ceremony operator and observers"),
        (("digest_change_approver",), ("policy_author",), "digest approver"),
        (("incident_revocation_authority",), ("producer_owners", "broker"), "incident authority"),
        (
            ("custody_attestation_issuers", "gateway"),
            ("producer_owners", "gateway"),
            "custody issuer",
        ),
    ],
)
def test_authority_collision_matrix(
    left_path: tuple[object, ...], right_path: tuple[object, ...], message: str
) -> None:
    payload = _payload()
    authorities = _authorities(payload)

    def set_path(path: tuple[object, ...], value: object) -> None:
        current: object = authorities
        for segment in path[:-1]:
            assert isinstance(current, (dict, list))
            current = current[segment]  # type: ignore[index]
        assert isinstance(current, (dict, list))
        current[path[-1]] = value  # type: ignore[index]

    identity = _verified("same-principal", "same-subject", "c")
    set_path(left_path, copy.deepcopy(identity))
    set_path(right_path, copy.deepcopy(identity))
    with pytest.raises(ConfigurationError, match=message):
        _validate(payload)


def test_role_maps_are_exact_closed_sets() -> None:
    payload = _payload()
    owners = _authorities(payload)["producer_owners"]
    assert isinstance(owners, dict)
    owners["eighth-role"] = copy.deepcopy(owners["core"])
    with pytest.raises(ConfigurationError, match="unexpected fields"):
        _validate(payload)

    payload = _payload()
    owners = _authorities(payload)["producer_owners"]
    assert isinstance(owners, dict)
    del owners["core"]
    with pytest.raises(ConfigurationError, match="missing fields"):
        _validate(payload)


@pytest.mark.parametrize(
    ("section", "field"),
    [
        ("principal", "authentication_reference_sha256"),
        ("custody", "attestation_reference_sha256"),
        ("resource", "evidence_reference_sha256"),
        ("blocker", "evidence_reference_sha256"),
        ("feature_gate", "multi_agent_enabled"),
    ],
)
def test_nullable_and_false_fields_must_still_be_present(section: str, field: str) -> None:
    payload = _payload()
    if section == "principal":
        target = _authorities(payload)["policy_author"]
    elif section == "custody":
        custody = payload["custody_assignments"]
        assert isinstance(custody, list)
        target = custody[0]
    elif section == "resource":
        target = _inventory(payload)[0]
    elif section == "blocker":
        target = _blockers(payload)[0]
    else:
        target = payload["feature_gates"]
    assert isinstance(target, dict)
    del target[field]
    with pytest.raises(ConfigurationError, match="missing fields"):
        _validate(payload)


def test_custody_assignments_are_closed_and_cannot_fake_attestation() -> None:
    payload = _payload()
    custody = payload["custody_assignments"]
    assert isinstance(custody, list)
    custody.pop()
    with pytest.raises(ConfigurationError, match="each frozen producer role"):
        _validate(payload)

    payload = _payload()
    custody = payload["custody_assignments"]
    assert isinstance(custody, list)
    custody[0]["selection_state"] = "VERIFIED"  # type: ignore[index]
    custody[0]["custody_kind"] = "managed_kms_hsm"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="requires attestation evidence"):
        _validate(payload)

    payload = _payload()
    custody = payload["custody_assignments"]
    assert isinstance(custody, list)
    custody[0]["custody_kind"] = "hsm_planned"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="unknown"):
        _validate(payload)


def test_environment_inventory_is_an_exact_fifteen_slot_closed_set() -> None:
    payload = _payload()
    _inventory(payload).pop()
    with pytest.raises(ConfigurationError, match="each required resource kind"):
        _validate(payload)

    payload = _payload()
    _inventory(payload)[1]["resource_kind"] = "core_deployment"
    with pytest.raises(ConfigurationError, match="each required resource kind"):
        _validate(payload)

    payload = _payload()
    _inventory(payload)[0]["assessment_state"] = "READY"
    with pytest.raises(ConfigurationError, match="unknown value"):
        _validate(payload)


def test_not_assessed_resource_cannot_carry_production_facts() -> None:
    payload = _payload()
    _inventory(payload)[0]["resource_id"] = "core-prod"
    with pytest.raises(ConfigurationError, match="NOT_ASSESSED cannot carry"):
        _validate(payload)


def _make_proven(item: dict[str, object], marker: str) -> None:
    item.update(
        {
            "assessment_state": "PROVEN",
            "resource_id": f"resource-{marker}",
            "owner_identity_id": f"owner-{marker}",
            "access_authority_identity_id": f"access-{marker}",
            "security_domain_id": f"domain-{marker}",
            "evidence_reference_sha256": marker * 64,
            "production_equivalent": True,
        }
    )


def test_proven_resource_requires_complete_real_production_facts() -> None:
    payload = _payload()
    resource = _inventory(payload)[0]
    resource["assessment_state"] = "PROVEN"
    with pytest.raises(ConfigurationError, match="require logical assignment"):
        _validate(payload)

    payload = _payload()
    resource = _inventory(payload)[0]
    _make_proven(resource, "a")
    resource["resource_id"] = "docker-fixture"
    with pytest.raises(ConfigurationError, match="engineering substitute"):
        _validate(payload)


def test_non_disposable_tenant_rag_requires_data_owner_authority() -> None:
    payload = _payload()
    resource = next(
        item for item in _inventory(payload) if item["resource_kind"] == "non_disposable_tenant_rag"
    )
    _make_proven(resource, "d")
    with pytest.raises(ConfigurationError, match="data-owner authority"):
        _validate(payload)


def test_overlay_members_and_derp_require_independent_security_domains() -> None:
    payload = _payload()
    inventory = {item["resource_kind"]: item for item in _inventory(payload)}
    for marker, kind in zip(
        "abc", ("overlay_member_a", "overlay_member_b", "independent_derp"), strict=True
    ):
        _make_proven(inventory[kind], marker)
    inventory["overlay_member_b"]["security_domain_id"] = inventory["overlay_member_a"][
        "security_domain_id"
    ]
    with pytest.raises(ConfigurationError, match="overlay members"):
        _validate(payload)

    payload = _payload()
    inventory = {item["resource_kind"]: item for item in _inventory(payload)}
    for marker, kind in zip(
        "abc", ("overlay_member_a", "overlay_member_b", "independent_derp"), strict=True
    ):
        _make_proven(inventory[kind], marker)
    inventory["independent_derp"]["security_domain_id"] = inventory["overlay_member_a"][
        "security_domain_id"
    ]
    with pytest.raises(ConfigurationError, match="DERP"):
        _validate(payload)


def test_blocker_mapping_is_exact_and_proven_is_derived_from_resources() -> None:
    payload = _payload()
    _blockers(payload).pop()
    with pytest.raises(ConfigurationError, match="blockers 1 through 11"):
        _validate(payload)

    payload = _payload()
    _blockers(payload)[0]["producer_role"] = "core"
    with pytest.raises(ConfigurationError, match="mapping drifted"):
        _validate(payload)

    payload = _payload()
    blocker = _blockers(payload)[0]
    blocker["assessment_state"] = "PROVEN"
    blocker["evidence_reference_sha256"] = "a" * 64
    with pytest.raises(ConfigurationError, match="requires every mapped"):
        _validate(payload)


def test_unreviewed_evidence_never_closes_a_blocker() -> None:
    payload = _payload()
    blocker = _blockers(payload)[0]
    blocker["assessment_state"] = "EVIDENCE_COLLECTED_NOT_REVIEWED"
    blocker["evidence_reference_sha256"] = "a" * 64
    report = _validate(payload)
    assert report.production_blockers_closed is False
    assert report.activation_allowed is False


@pytest.mark.parametrize(
    "field",
    ["private_key", "apiKey", "bearer_token", "password", "rootEnv"],
)
def test_secret_shaped_fields_are_rejected(field: str) -> None:
    payload = _payload()
    payload[field] = "must-not-leak"
    with pytest.raises(ConfigurationError, match="forbidden secret-shaped field") as exc:
        _validate(payload)
    assert "must-not-leak" not in str(exc.value)


def test_root_env_locator_is_rejected() -> None:
    payload = _payload()
    payload["note"] = "E:\\Agent IDE\\.env"
    with pytest.raises(ConfigurationError, match=r"root \.env locator"):
        _validate(payload)


def test_file_entry_rejects_noncanonical_and_outside_repo(tmp_path: Path) -> None:
    pretty = tmp_path / "assignment.json"
    pretty.write_text(json.dumps(_payload(), indent=2), encoding="utf-8")
    with pytest.raises(ConfigurationError, match="inside the repository"):
        validate_trust_policy_r1_assignment_file(pretty, REPO_ROOT)

    inside = REPO_ROOT / ".tmp-r1-assignment-noncanonical.json"
    try:
        inside.write_text(json.dumps(_payload(), indent=2), encoding="utf-8")
        with pytest.raises(ConfigurationError, match="canonical JSON bytes"):
            validate_trust_policy_r1_assignment_file(inside, REPO_ROOT)
    finally:
        inside.unlink(missing_ok=True)


def test_migration_0013_is_rejected(tmp_path: Path) -> None:
    versions = tmp_path / "backend" / "src" / "omnibase" / "migrations" / "versions"
    versions.mkdir(parents=True)
    (versions / "0012_base.py").write_text(
        'revision: str = "0012"\ndown_revision: str | None = None\n', encoding="utf-8"
    )
    (versions / "0013_forbidden.py").write_text(
        'revision: str = "0013"\ndown_revision: str | None = "0012"\n', encoding="utf-8"
    )
    with pytest.raises(ConfigurationError, match="migration head must remain 0012"):
        validate_trust_policy_r1_assignment(_payload(), tmp_path)


def test_report_never_reinterprets_contract_validation_as_production_pass() -> None:
    report = _validate(_payload()).to_dict()
    assert report["contract_valid"] is True
    assert report["trust_policy_approved"] is False
    assert report["approved_digest_written"] is False
    assert report["key_ceremony_authorized"] is False
    assert report["production_evidence_authorized"] is False
    assert report["activation_allowed"] is False
    assert report["p34_7_production_total_gate"] == "blocked/not_proven"
