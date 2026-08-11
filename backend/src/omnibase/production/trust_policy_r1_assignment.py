"""P34.7 Trust Policy R1-A authority and target-environment assignment contract.

This module is an offline, engineering-only preparation gate.  It records who
would hold each independent authority, which custody posture would be used,
which production resources must exist, and how the eleven P34.7 blockers map
to those resources.  It deliberately does not authenticate a real person,
approve a trust policy, authorize a key ceremony, collect production evidence,
write an approved digest, start a service, or activate Agent Runtime.

The proposal is not an authority registry, review-receipt verifier, custody
attestation gate, or production-evidence gate.  Consequently it rejects
input-declared ``VERIFIED`` and ``PROVEN`` facts instead of trusting a digest
that the proposal supplied about itself.

The example contract intentionally contains only ``UNASSIGNED`` and
``NOT_ASSESSED`` facts.  Such a document can be structurally valid while the
derived result remains ``r1_assignment/valid_incomplete`` and P34.7 remains
``blocked/not_proven``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from omnibase.production.composition import ConfigurationError
from omnibase.production.joint_gate import (
    _bool,
    _keys,
    _list,
    _object,
    _opt_string,
    _sha256,
    _string,
)
from omnibase.production.phase5_admission import discover_migration_head
from omnibase.production.trust_policy_candidate import (
    MIGRATION_HEAD,
    REQUIRED_ROLES,
    _load_regular_json,
    _read_canonical,
    scan_forbidden_secrets,
)

ASSIGNMENT_SCHEMA = "omnibase.p34-7.trust-policy-r1-assignment.v1"
SCHEMA_VERSION = "1"

ASSIGNMENT_STATES = frozenset({"UNASSIGNED", "ASSIGNED_NOT_VERIFIED", "VERIFIED", "REJECTED"})
AUTHENTICATION_KINDS = frozenset(
    {"NOT_ASSESSED", "ED25519_PUBLIC_KEY", "OIDC_SUBJECT", "ENTERPRISE_DIRECTORY"}
)
CUSTODY_STATES = frozenset({"NOT_ASSESSED", "SELECTED_NOT_VERIFIED", "VERIFIED", "REJECTED"})
CUSTODY_KINDS = frozenset(
    {
        "offline_hardware",
        "managed_kms_hsm",
        "runner_local_protected",
        "external_signing_service",
    }
)
ASSESSMENT_STATES = frozenset(
    {
        "NOT_ASSESSED",
        "MISSING",
        "PLANNED",
        "AVAILABLE_NOT_PROVEN",
        "EVIDENCE_COLLECTED_NOT_REVIEWED",
        "PROVEN",
        "REJECTED",
    }
)

REQUIRED_RESOURCE_KINDS = (
    "core_deployment",
    "linux_runner",
    "network_broker",
    "capability_gateway",
    "overlay_member_a",
    "overlay_member_b",
    "independent_derp",
    "provider_object_store",
    "non_disposable_tenant_rag",
    "pki_certificate_boundary",
    "seven_signing_roles",
    "time_source",
    "observability",
    "capacity_sla_harness",
    "recovery_cleanup_authority",
)

BLOCKER_REQUIREMENTS: dict[str, tuple[tuple[str, ...], str, str]] = {
    "current_source_linux_runner_12_of_12": (
        ("linux_runner", "time_source", "observability", "recovery_cleanup_authority"),
        "runner",
        "runner_attack_matrix",
    ),
    "core_runner_mtls_production_roundtrip": (
        ("core_deployment", "linux_runner", "pki_certificate_boundary"),
        "core",
        "core_runner",
    ),
    "runner_broker_production_identity_roundtrip": (
        ("linux_runner", "network_broker"),
        "runner",
        "runner_broker",
    ),
    "runner_gateway_mtls_non_disposable_roundtrip": (
        (
            "linux_runner",
            "capability_gateway",
            "pki_certificate_boundary",
            "provider_object_store",
            "non_disposable_tenant_rag",
        ),
        "runner",
        "runner_gateway",
    ),
    "broker_gateway_mtls_non_disposable_roundtrip": (
        ("network_broker", "capability_gateway", "pki_certificate_boundary"),
        "broker",
        "broker_gateway",
    ),
    "provider_backed_workspace_recovery_non_disposable": (
        (
            "provider_object_store",
            "non_disposable_tenant_rag",
            "recovery_cleanup_authority",
        ),
        "recovery_sla",
        "recovery_sla",
    ),
    "data_owner_authorized_tenant_rag_smoke": (
        (
            "non_disposable_tenant_rag",
            "provider_object_store",
            "capability_gateway",
        ),
        "recovery_sla",
        "recovery_sla",
    ),
    "two_real_member_overlay_derp": (
        (
            "overlay_member_a",
            "overlay_member_b",
            "independent_derp",
            "pki_certificate_boundary",
        ),
        "overlay",
        "overlay_data_plane",
    ),
    "overlay_compromise_rejoin_matrix": (
        (
            "overlay_member_a",
            "overlay_member_b",
            "independent_derp",
            "pki_certificate_boundary",
            "recovery_cleanup_authority",
        ),
        "overlay",
        "overlay_data_plane",
    ),
    "dual_independent_member_signatures": (
        ("overlay_member_a", "overlay_member_b", "seven_signing_roles", "time_source"),
        "overlay",
        "overlay_data_plane",
    ),
    "production_capacity_fault_injection_sla": (
        (
            "core_deployment",
            "linux_runner",
            "network_broker",
            "capability_gateway",
            "overlay_member_a",
            "overlay_member_b",
            "capacity_sla_harness",
            "observability",
            "time_source",
            "recovery_cleanup_authority",
        ),
        "recovery_sla",
        "recovery_sla",
    ),
}

_LOGICAL_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_FORBIDDEN_PRODUCTION_SUBSTITUTES = (
    "docker",
    "wsl",
    "mock",
    "fixture",
    "test-double",
    "test_double",
    "disposable",
)


def _enum(value: object, name: str, allowed: frozenset[str]) -> str:
    text = _string(value, name)
    if text not in allowed:
        raise ConfigurationError(f"{name} has an unknown value")
    return text


def _exact_keys(value: dict[str, object], allowed: set[str], name: str) -> None:
    _keys(value, allowed, name)
    missing = sorted(allowed - set(value))
    if missing:
        raise ConfigurationError(f"{name} is missing fields: {', '.join(missing)}")


def _logical_id(value: object, name: str) -> str:
    text = _string(value, name)
    if not _LOGICAL_ID.fullmatch(text):
        raise ConfigurationError(
            f"{name} must be a logical identifier ([a-z0-9._-], <= 64 characters)"
        )
    return text


def _optional_logical_id(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _logical_id(value, name)


@dataclass(frozen=True, slots=True)
class PrincipalAssignment:
    identity_id: str
    assignment_state: str
    canonical_subject_id: str | None
    authentication_kind: str
    authentication_reference_sha256: str | None

    @classmethod
    def from_mapping(cls, value: object, name: str) -> PrincipalAssignment:
        item = _object(value, name)
        _exact_keys(
            item,
            {
                "identity_id",
                "assignment_state",
                "canonical_subject_id",
                "authentication_kind",
                "authentication_reference_sha256",
            },
            name,
        )
        state = _enum(item.get("assignment_state"), f"{name}.assignment_state", ASSIGNMENT_STATES)
        identity = _string(item.get("identity_id"), f"{name}.identity_id")
        subject = _optional_logical_id(
            item.get("canonical_subject_id"), f"{name}.canonical_subject_id"
        )
        authentication_kind = _enum(
            item.get("authentication_kind"),
            f"{name}.authentication_kind",
            AUTHENTICATION_KINDS,
        )
        reference_value = item.get("authentication_reference_sha256")
        reference = (
            None
            if reference_value is None
            else _sha256(reference_value, f"{name}.authentication_reference_sha256")
        )
        if state == "UNASSIGNED":
            if identity != "UNASSIGNED" or subject is not None:
                raise ConfigurationError(f"{name}: UNASSIGNED must not claim a real identity")
            if authentication_kind != "NOT_ASSESSED" or reference is not None:
                raise ConfigurationError(f"{name}: UNASSIGNED must not claim authentication")
        else:
            identity = _logical_id(identity, f"{name}.identity_id")
            if subject is None:
                raise ConfigurationError(
                    f"{name}: assigned identities require canonical_subject_id"
                )
            if state == "VERIFIED":
                raise ConfigurationError(
                    f"{name}: VERIFIED requires an independently pinned authority registry "
                    "and review-receipt verifier"
                )
            if authentication_kind == "NOT_ASSESSED" and reference is not None:
                raise ConfigurationError(
                    f"{name}: authentication reference requires an assessed authentication kind"
                )
            if authentication_kind != "NOT_ASSESSED" and reference is None:
                raise ConfigurationError(
                    f"{name}: assessed authentication kind requires a content-addressed reference"
                )
        return cls(identity, state, subject, authentication_kind, reference)

    @property
    def assigned(self) -> bool:
        return self.assignment_state != "UNASSIGNED"

    @property
    def proposed(self) -> bool:
        return self.assignment_state == "ASSIGNED_NOT_VERIFIED"


@dataclass(frozen=True, slots=True)
class AuthorityAssignments:
    policy_author: PrincipalAssignment
    policy_reviewers: tuple[PrincipalAssignment, ...]
    producer_owners: dict[str, PrincipalAssignment]
    producer_backup_owners: dict[str, PrincipalAssignment]
    ceremony_operator: PrincipalAssignment
    ceremony_observers: tuple[PrincipalAssignment, ...]
    custody_attestation_issuers: dict[str, PrincipalAssignment]
    digest_change_approver: PrincipalAssignment
    incident_revocation_authority: PrincipalAssignment

    @classmethod
    def from_mapping(cls, value: object) -> AuthorityAssignments:
        item = _object(value, "authority_assignments")
        _exact_keys(
            item,
            {
                "policy_author",
                "policy_reviewers",
                "producer_owners",
                "producer_backup_owners",
                "ceremony_operator",
                "ceremony_observers",
                "custody_attestation_issuers",
                "digest_change_approver",
                "incident_revocation_authority",
            },
            "authority_assignments",
        )
        reviewers = _principal_list(item.get("policy_reviewers"), "policy_reviewers", exactly=2)
        observers = _principal_list(item.get("ceremony_observers"), "ceremony_observers", exactly=2)
        result = cls(
            policy_author=PrincipalAssignment.from_mapping(
                item.get("policy_author"), "policy_author"
            ),
            policy_reviewers=reviewers,
            producer_owners=_principal_role_map(item.get("producer_owners"), "producer_owners"),
            producer_backup_owners=_principal_role_map(
                item.get("producer_backup_owners"), "producer_backup_owners"
            ),
            ceremony_operator=PrincipalAssignment.from_mapping(
                item.get("ceremony_operator"), "ceremony_operator"
            ),
            ceremony_observers=observers,
            custody_attestation_issuers=_principal_role_map(
                item.get("custody_attestation_issuers"), "custody_attestation_issuers"
            ),
            digest_change_approver=PrincipalAssignment.from_mapping(
                item.get("digest_change_approver"), "digest_change_approver"
            ),
            incident_revocation_authority=PrincipalAssignment.from_mapping(
                item.get("incident_revocation_authority"), "incident_revocation_authority"
            ),
        )
        _verify_authority_separation(result)
        return result

    def all_principals(self) -> tuple[PrincipalAssignment, ...]:
        return (
            self.policy_author,
            *self.policy_reviewers,
            *self.producer_owners.values(),
            *self.producer_backup_owners.values(),
            self.ceremony_operator,
            *self.ceremony_observers,
            *self.custody_attestation_issuers.values(),
            self.digest_change_approver,
            self.incident_revocation_authority,
        )


def _principal_list(value: object, name: str, *, exactly: int) -> tuple[PrincipalAssignment, ...]:
    raw = _list(value, name)
    if len(raw) != exactly:
        raise ConfigurationError(f"{name} must contain exactly {exactly} assignments")
    return tuple(
        PrincipalAssignment.from_mapping(entry, f"{name}[{index}]")
        for index, entry in enumerate(raw)
    )


def _principal_role_map(value: object, name: str) -> dict[str, PrincipalAssignment]:
    item = _object(value, name)
    _exact_keys(item, set(REQUIRED_ROLES), name)
    return {
        role: PrincipalAssignment.from_mapping(item[role], f"{name}.{role}")
        for role in REQUIRED_ROLES
    }


def _subject(principal: PrincipalAssignment) -> str | None:
    return principal.canonical_subject_id if principal.assigned else None


def _ensure_disjoint(
    label: str, left: tuple[PrincipalAssignment, ...], right: tuple[PrincipalAssignment, ...]
) -> None:
    left_subjects = {_subject(item) for item in left} - {None}
    right_subjects = {_subject(item) for item in right} - {None}
    if left_subjects & right_subjects:
        raise ConfigurationError(f"authority separation failed: {label}")


def _ensure_unique(label: str, principals: tuple[PrincipalAssignment, ...]) -> None:
    subjects = [_subject(item) for item in principals if item.assigned]
    references = [
        item.authentication_reference_sha256
        for item in principals
        if item.authentication_reference_sha256 is not None
    ]
    if len(subjects) != len(set(subjects)) or len(references) != len(set(references)):
        raise ConfigurationError(f"authority separation failed: {label}")


def _verify_authority_separation(value: AuthorityAssignments) -> None:
    reviewers = value.policy_reviewers
    owners = tuple(value.producer_owners.values())
    backups = tuple(value.producer_backup_owners.values())
    observers = value.ceremony_observers
    issuers = tuple(value.custody_attestation_issuers.values())
    _ensure_unique("reviewers must be independent", reviewers)
    _ensure_unique("producer owners must be independent", owners)
    _ensure_unique("producer backup owners must be independent", backups)
    _ensure_unique("ceremony observers must be independent", observers)
    _ensure_disjoint("author and reviewers", (value.policy_author,), reviewers)
    _ensure_disjoint("author and producer owners/backups", (value.policy_author,), owners + backups)
    _ensure_disjoint("reviewers and producer owners/backups", reviewers, owners + backups)
    _ensure_disjoint("producer owners and backup owners", owners, backups)
    _ensure_disjoint("ceremony operator and observers", (value.ceremony_operator,), observers)
    _ensure_disjoint(
        "ceremony operator and author/reviewers/producers",
        (value.ceremony_operator,),
        (value.policy_author, *reviewers, *owners, *backups),
    )
    _ensure_disjoint(
        "digest approver and author/reviewers/producers/operator",
        (value.digest_change_approver,),
        (value.policy_author, *reviewers, *owners, *backups, value.ceremony_operator),
    )
    _ensure_disjoint(
        "incident authority and producer signing owners",
        (value.incident_revocation_authority,),
        owners + backups,
    )
    for role in REQUIRED_ROLES:
        _ensure_disjoint(
            f"custody issuer and {role} owner/backup/operator",
            (value.custody_attestation_issuers[role],),
            (
                value.producer_owners[role],
                value.producer_backup_owners[role],
                value.ceremony_operator,
            ),
        )
    _ensure_unique("custody issuers must not alias by authentication reference", issuers)


@dataclass(frozen=True, slots=True)
class CustodyAssignment:
    role: str
    selection_state: str
    custody_kind: str | None
    attestation_reference_sha256: str | None

    @classmethod
    def from_mapping(cls, value: object, name: str) -> CustodyAssignment:
        item = _object(value, name)
        _exact_keys(
            item, {"role", "selection_state", "custody_kind", "attestation_reference_sha256"}, name
        )
        role = _string(item.get("role"), f"{name}.role")
        if role not in REQUIRED_ROLES:
            raise ConfigurationError(f"{name}.role is unknown")
        state = _enum(item.get("selection_state"), f"{name}.selection_state", CUSTODY_STATES)
        kind = _opt_string(item.get("custody_kind"), f"{name}.custody_kind")
        if kind is not None and kind not in CUSTODY_KINDS:
            raise ConfigurationError(f"{name}.custody_kind is unknown")
        raw_reference = item.get("attestation_reference_sha256")
        reference = (
            None
            if raw_reference is None
            else _sha256(raw_reference, f"{name}.attestation_reference_sha256")
        )
        if state == "NOT_ASSESSED" and (kind is not None or reference is not None):
            raise ConfigurationError(f"{name}: NOT_ASSESSED cannot claim custody evidence")
        if state in {"SELECTED_NOT_VERIFIED", "VERIFIED"} and kind is None:
            raise ConfigurationError(f"{name}: selected custody requires custody_kind")
        if state == "VERIFIED":
            raise ConfigurationError(
                f"{name}: VERIFIED custody requires an independently reviewed attestation contract"
            )
        if state == "SELECTED_NOT_VERIFIED" and reference is not None:
            raise ConfigurationError(
                f"{name}: unverified custody selection cannot claim attestation evidence"
            )
        return cls(role, state, kind, reference)


def _custody_assignments(value: object) -> dict[str, CustodyAssignment]:
    raw = _list(value, "custody_assignments")
    parsed = [
        CustodyAssignment.from_mapping(item, f"custody_assignments[{index}]")
        for index, item in enumerate(raw)
    ]
    roles = [item.role for item in parsed]
    if len(roles) != len(set(roles)) or set(roles) != set(REQUIRED_ROLES):
        raise ConfigurationError(
            "custody_assignments must contain each frozen producer role exactly once"
        )
    return {item.role: item for item in parsed}


@dataclass(frozen=True, slots=True)
class EnvironmentResource:
    resource_kind: str
    assessment_state: str
    resource_id: str | None
    owner_identity_id: str | None
    access_authority_identity_id: str | None
    security_domain_id: str | None
    evidence_reference_sha256: str | None
    data_owner_authority_identity_id: str | None
    production_equivalent: bool

    @classmethod
    def from_mapping(cls, value: object, name: str) -> EnvironmentResource:
        item = _object(value, name)
        _exact_keys(
            item,
            {
                "resource_kind",
                "assessment_state",
                "resource_id",
                "owner_identity_id",
                "access_authority_identity_id",
                "security_domain_id",
                "evidence_reference_sha256",
                "data_owner_authority_identity_id",
                "production_equivalent",
            },
            name,
        )
        kind = _string(item.get("resource_kind"), f"{name}.resource_kind")
        if kind not in REQUIRED_RESOURCE_KINDS:
            raise ConfigurationError(f"{name}.resource_kind is unknown")
        state = _enum(item.get("assessment_state"), f"{name}.assessment_state", ASSESSMENT_STATES)
        resource_id = _optional_logical_id(item.get("resource_id"), f"{name}.resource_id")
        owner = _optional_logical_id(item.get("owner_identity_id"), f"{name}.owner_identity_id")
        access = _optional_logical_id(
            item.get("access_authority_identity_id"), f"{name}.access_authority_identity_id"
        )
        domain = _optional_logical_id(item.get("security_domain_id"), f"{name}.security_domain_id")
        data_owner = _optional_logical_id(
            item.get("data_owner_authority_identity_id"), f"{name}.data_owner_authority_identity_id"
        )
        raw_evidence = item.get("evidence_reference_sha256")
        evidence = (
            None
            if raw_evidence is None
            else _sha256(raw_evidence, f"{name}.evidence_reference_sha256")
        )
        production_equivalent = _bool(
            item.get("production_equivalent"), f"{name}.production_equivalent"
        )
        optional_values = (resource_id, owner, access, domain, evidence, data_owner)
        if state == "NOT_ASSESSED" and any(value is not None for value in optional_values):
            raise ConfigurationError(f"{name}: NOT_ASSESSED cannot carry assessed resource facts")
        if state == "NOT_ASSESSED" and production_equivalent:
            raise ConfigurationError(f"{name}: NOT_ASSESSED cannot be production equivalent")
        if state in {
            "PLANNED",
            "AVAILABLE_NOT_PROVEN",
            "EVIDENCE_COLLECTED_NOT_REVIEWED",
            "PROVEN",
        } and (resource_id is None or owner is None or access is None or domain is None):
            raise ConfigurationError(
                f"{name}: assessed resources require logical assignment fields"
            )
        if state in {"EVIDENCE_COLLECTED_NOT_REVIEWED", "PROVEN"} and evidence is None:
            raise ConfigurationError(
                f"{name}: evidence state requires a content-addressed reference"
            )
        if state == "PROVEN":
            raise ConfigurationError(
                f"{name}: PROVEN requires the later independently signed evidence gate"
            )
        if production_equivalent:
            raise ConfigurationError(f"{name}: R1-A assignment cannot claim production equivalence")
        if kind == "non_disposable_tenant_rag" and state != "NOT_ASSESSED" and data_owner is None:
            raise ConfigurationError(f"{name}: tenant/RAG assessment requires data-owner authority")
        joined = " ".join(
            value or "" for value in (resource_id, owner, access, domain, data_owner)
        ).lower()
        if any(token in joined for token in _FORBIDDEN_PRODUCTION_SUBSTITUTES):
            raise ConfigurationError(
                f"{name}: engineering substitute cannot be assigned as target infrastructure"
            )
        return cls(
            kind,
            state,
            resource_id,
            owner,
            access,
            domain,
            evidence,
            data_owner,
            production_equivalent,
        )


def _environment_inventory(value: object) -> dict[str, EnvironmentResource]:
    raw = _list(value, "environment_inventory")
    parsed = [
        EnvironmentResource.from_mapping(item, f"environment_inventory[{index}]")
        for index, item in enumerate(raw)
    ]
    kinds = [item.resource_kind for item in parsed]
    if len(kinds) != len(set(kinds)) or set(kinds) != set(REQUIRED_RESOURCE_KINDS):
        raise ConfigurationError(
            "environment_inventory must contain each required resource kind exactly once"
        )
    resource_ids = [item.resource_id for item in parsed if item.resource_id is not None]
    if len(resource_ids) != len(set(resource_ids)):
        raise ConfigurationError("environment resource ids must be unique")
    inventory = {item.resource_kind: item for item in parsed}
    a = inventory["overlay_member_a"]
    b = inventory["overlay_member_b"]
    derp = inventory["independent_derp"]
    if a.security_domain_id is not None and a.security_domain_id == b.security_domain_id:
        raise ConfigurationError("overlay members must use independent security domains")
    if derp.security_domain_id is not None and derp.security_domain_id in {
        a.security_domain_id,
        b.security_domain_id,
    }:
        raise ConfigurationError(
            "independent DERP must not share an overlay member security domain"
        )
    return inventory


@dataclass(frozen=True, slots=True)
class BlockerAssignment:
    blocker_id: str
    assessment_state: str
    environment_resources: tuple[str, ...]
    producer_role: str
    command: str
    evidence_reference_sha256: str | None

    @classmethod
    def from_mapping(cls, value: object, name: str) -> BlockerAssignment:
        item = _object(value, name)
        _exact_keys(
            item,
            {
                "blocker_id",
                "assessment_state",
                "environment_resources",
                "producer_role",
                "command",
                "evidence_reference_sha256",
            },
            name,
        )
        blocker_id = _string(item.get("blocker_id"), f"{name}.blocker_id")
        if blocker_id not in BLOCKER_REQUIREMENTS:
            raise ConfigurationError(f"{name}.blocker_id is unknown")
        state = _enum(item.get("assessment_state"), f"{name}.assessment_state", ASSESSMENT_STATES)
        resources_raw = _list(item.get("environment_resources"), f"{name}.environment_resources")
        resources = tuple(
            _string(entry, f"{name}.environment_resources[{index}]")
            for index, entry in enumerate(resources_raw)
        )
        if len(resources) != len(set(resources)):
            raise ConfigurationError(f"{name}.environment_resources must not contain duplicates")
        producer_role = _string(item.get("producer_role"), f"{name}.producer_role")
        command = _string(item.get("command"), f"{name}.command")
        expected_resources, expected_role, expected_command = BLOCKER_REQUIREMENTS[blocker_id]
        if (
            resources != expected_resources
            or producer_role != expected_role
            or command != expected_command
        ):
            raise ConfigurationError(f"{name}: blocker mapping drifted from the frozen contract")
        raw_evidence = item.get("evidence_reference_sha256")
        evidence = (
            None
            if raw_evidence is None
            else _sha256(raw_evidence, f"{name}.evidence_reference_sha256")
        )
        if state in {"EVIDENCE_COLLECTED_NOT_REVIEWED", "PROVEN"} and evidence is None:
            raise ConfigurationError(
                f"{name}: evidence state requires a content-addressed reference"
            )
        if state == "PROVEN":
            raise ConfigurationError(
                f"{name}: PROVEN blocker requires the later independently signed evidence gate"
            )
        if state not in {"EVIDENCE_COLLECTED_NOT_REVIEWED", "PROVEN"} and evidence is not None:
            raise ConfigurationError(f"{name}: evidence reference is not valid for this state")
        return cls(blocker_id, state, resources, producer_role, command, evidence)


def _blocker_assignments(value: object) -> dict[str, BlockerAssignment]:
    raw = _list(value, "blocker_assignments")
    parsed = [
        BlockerAssignment.from_mapping(item, f"blocker_assignments[{index}]")
        for index, item in enumerate(raw)
    ]
    ids = [item.blocker_id for item in parsed]
    if len(ids) != len(set(ids)) or set(ids) != set(BLOCKER_REQUIREMENTS):
        raise ConfigurationError(
            "blocker_assignments must contain blockers 1 through 11 exactly once"
        )
    return {item.blocker_id: item for item in parsed}


@dataclass(frozen=True, slots=True)
class R1AssignmentReport:
    contract_valid: bool
    assignment_complete: bool
    authority_separation_contract_valid: bool
    authority_separation_verified: bool
    authority_authentication_verified: bool
    independent_review_receipts_verified: bool
    custody_assignment_complete: bool
    custody_attestations_verified: bool
    environment_inventory_complete: bool
    environment_evidence_verified: bool
    production_blockers_closed: bool
    trust_policy_approved: bool
    approved_digest_written: bool
    key_ceremony_authorized: bool
    production_evidence_authorized: bool
    activation_allowed: bool
    migration_head: str
    migration_0013_created: bool
    feature_gates: dict[str, bool]
    root_env_accessed: bool
    business_database_accessed: bool
    business_database_migrated: bool
    runtime_activated: bool
    status: str
    blockers: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": ASSIGNMENT_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "contract_valid": self.contract_valid,
            "assignment_complete": self.assignment_complete,
            "authority_separation_contract_valid": self.authority_separation_contract_valid,
            "authority_separation_verified": self.authority_separation_verified,
            "authority_authentication_verified": self.authority_authentication_verified,
            "independent_review_receipts_verified": self.independent_review_receipts_verified,
            "custody_assignment_complete": self.custody_assignment_complete,
            "custody_attestations_verified": self.custody_attestations_verified,
            "environment_inventory_complete": self.environment_inventory_complete,
            "environment_evidence_verified": self.environment_evidence_verified,
            "production_blockers_closed": self.production_blockers_closed,
            "trust_policy_approved": self.trust_policy_approved,
            "approved_digest_written": self.approved_digest_written,
            "key_ceremony_authorized": self.key_ceremony_authorized,
            "production_evidence_authorized": self.production_evidence_authorized,
            "activation_allowed": self.activation_allowed,
            "migration_head": self.migration_head,
            "migration_0013_created": self.migration_0013_created,
            "feature_gates": dict(self.feature_gates),
            "root_env_accessed": self.root_env_accessed,
            "business_database_accessed": self.business_database_accessed,
            "business_database_migrated": self.business_database_migrated,
            "runtime_activated": self.runtime_activated,
            "p34_7_production_total_gate": "blocked/not_proven",
            "blockers": list(self.blockers),
        }


def _parse_top_level(payload: object) -> dict[str, object]:
    scan_forbidden_secrets(payload, "R1 assignment")
    item = _object(payload, "R1 assignment")
    _exact_keys(
        item,
        {
            "schema",
            "schema_version",
            "assignment_only",
            "trust_policy_approved",
            "approved_digest_written",
            "key_ceremony_authorized",
            "production_evidence_authorized",
            "activation_allowed",
            "authority_assignments",
            "custody_assignments",
            "environment_inventory",
            "blocker_assignments",
            "migration_head",
            "migration_0013_created",
            "feature_gates",
        },
        "R1 assignment",
    )
    if _string(item.get("schema"), "schema") != ASSIGNMENT_SCHEMA:
        raise ConfigurationError("R1 assignment schema is unsupported")
    if _string(item.get("schema_version"), "schema_version") != SCHEMA_VERSION:
        raise ConfigurationError("R1 assignment schema_version is unsupported")
    if not _bool(item.get("assignment_only"), "assignment_only"):
        raise ConfigurationError("R1 assignment must remain assignment_only=true")
    _verify_frozen_false_fields(item)
    if _string(item.get("migration_head"), "migration_head") != MIGRATION_HEAD:
        raise ConfigurationError(f"migration_head must remain {MIGRATION_HEAD}")
    _verify_feature_gates(item.get("feature_gates"))
    return item


def _verify_frozen_false_fields(item: dict[str, object]) -> None:
    for field in (
        "trust_policy_approved",
        "approved_digest_written",
        "key_ceremony_authorized",
        "production_evidence_authorized",
        "activation_allowed",
    ):
        if _bool(item.get(field), field):
            raise ConfigurationError(f"R1-A cannot set {field}=true")
    if not _bool(item.get("migration_0013_created"), "migration_0013_created"):
        raise ConfigurationError("R1-A must report current migration 0013 as created")


def _verify_feature_gates(value: object) -> None:
    feature_gates = _object(value, "feature_gates")
    _exact_keys(
        feature_gates,
        {"agent_runtime_enabled", "agent_planner_enabled", "multi_agent_enabled"},
        "feature_gates",
    )
    if any(_bool(feature_gates.get(name), f"feature_gates.{name}") for name in feature_gates):
        raise ConfigurationError("all Phase 5 Feature Gates must remain false")


def _verify_repository_posture(repo_root: Path) -> str:
    migration_head = discover_migration_head(repo_root, "backend/src/omnibase/migrations/versions")
    if migration_head != MIGRATION_HEAD:
        raise ConfigurationError(f"repository migration head must remain {MIGRATION_HEAD}")
    versions = repo_root / "backend" / "src" / "omnibase" / "migrations" / "versions"
    if not any(path.name.startswith("0013_") for path in versions.glob("*.py")):
        raise ConfigurationError("migration 0013 must exist at the current repository head")
    if any(
        path.name[:4].isdigit() and int(path.name[:4]) >= 14
        for path in versions.glob("[0-9][0-9][0-9][0-9]_*.py")
    ):
        raise ConfigurationError("migration 0014 or higher must not exist")
    return migration_head


def _derive_blockers(
    assignment_complete: bool,
    custody_complete: bool,
    inventory_complete: bool,
) -> tuple[str, ...]:
    blockers: list[str] = []
    if not assignment_complete:
        blockers.append("authority_assignments_incomplete")
    blockers.extend(("authority_registry_unpinned", "independent_review_receipts_absent"))
    if not custody_complete:
        blockers.append("custody_assignments_incomplete")
    blockers.append("custody_attestations_not_independently_verified")
    if not inventory_complete:
        blockers.append("environment_inventory_not_assessed")
    blockers.append("environment_evidence_not_independently_verified")
    blockers.append("production_blockers_not_closed")
    return tuple(blockers)


def validate_trust_policy_r1_assignment(payload: object, repo_root: Path) -> R1AssignmentReport:
    """Validate one in-memory R1-A assignment proposal without side effects."""
    item = _parse_top_level(payload)
    authorities = AuthorityAssignments.from_mapping(item.get("authority_assignments"))
    custody = _custody_assignments(item.get("custody_assignments"))
    inventory = _environment_inventory(item.get("environment_inventory"))
    _blocker_assignments(item.get("blocker_assignments"))
    migration_head = _verify_repository_posture(repo_root)
    assignment_complete = all(principal.proposed for principal in authorities.all_principals())
    custody_complete = all(
        entry.selection_state == "SELECTED_NOT_VERIFIED" for entry in custody.values()
    )
    inventory_complete = all(
        entry.assessment_state not in {"NOT_ASSESSED", "MISSING", "REJECTED"}
        for entry in inventory.values()
    )
    blockers_closed = False
    blockers = _derive_blockers(assignment_complete, custody_complete, inventory_complete)
    complete = assignment_complete and custody_complete and inventory_complete
    status = (
        "r1_assignment/complete_not_authenticated" if complete else "r1_assignment/valid_incomplete"
    )
    return R1AssignmentReport(
        contract_valid=True,
        assignment_complete=assignment_complete,
        authority_separation_contract_valid=True,
        authority_separation_verified=False,
        authority_authentication_verified=False,
        independent_review_receipts_verified=False,
        custody_assignment_complete=custody_complete,
        custody_attestations_verified=False,
        environment_inventory_complete=inventory_complete,
        environment_evidence_verified=False,
        production_blockers_closed=blockers_closed,
        trust_policy_approved=False,
        approved_digest_written=False,
        key_ceremony_authorized=False,
        production_evidence_authorized=False,
        activation_allowed=False,
        migration_head=migration_head,
        migration_0013_created=True,
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        root_env_accessed=False,
        business_database_accessed=False,
        business_database_migrated=False,
        runtime_activated=False,
        status=status,
        blockers=blockers,
    )


def validate_trust_policy_r1_assignment_file(path: Path, repo_root: Path) -> R1AssignmentReport:
    """Validate canonical assignment bytes from a regular file inside the repository."""
    root = repo_root.resolve(strict=True)
    candidate = _load_regular_json(path, "R1 assignment")
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ConfigurationError("R1 assignment must resolve inside the repository") from exc
    return validate_trust_policy_r1_assignment(_read_canonical(candidate, "R1 assignment"), root)


__all__ = [
    "ASSESSMENT_STATES",
    "ASSIGNMENT_SCHEMA",
    "BLOCKER_REQUIREMENTS",
    "CUSTODY_KINDS",
    "REQUIRED_RESOURCE_KINDS",
    "R1AssignmentReport",
    "validate_trust_policy_r1_assignment",
    "validate_trust_policy_r1_assignment_file",
]
