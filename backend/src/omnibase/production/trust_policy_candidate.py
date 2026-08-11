"""P34.7 Trust Policy Candidate R0 -- engineering-only trust governance.

This module establishes the *candidate* trust-policy governance contract for
the P34.7 joint gate.  It is deliberately NOT a production approval path:

* a candidate carries `candidate_only=true`, `production_approved=false` and a
  lifecycle state from ``{draft, candidate, rejected, superseded, revoked}``;
* the highest positive status any candidate validator may return is
  ``candidate/valid_not_approved`` -- it never returns ``approved``,
  ``passed`` or ``activation_allowed=true``;
* validating a candidate NEVER writes into
  ``joint_gate._APPROVED_TRUST_POLICY_SHA256`` (which stays an empty
  frozenset) and never changes the P34.7 production decision
  (``blocked/not_proven``);
* the module never generates, stores, prints or transports private keys,
  seeds, mnemonics, passphrases, API keys, bearer tokens, database
  passwords, provider credentials or root ``.env`` locators -- every DTO is
  recursively scanned for forbidden secret-shaped fields and fails closed.

The candidate contract mirrors the joint gate's frozen security semantics
(strict closed-set parsing, Git object-format binding with original OIDs,
seven unique Ed25519 producer keys, frozen per-role signing-scope matrix,
external approval packet, key lifecycle/rotation/revocation state machine).
All strict parsers are REUSED from :mod:`omnibase.production.joint_gate` so
the candidate and the evidence gate can never drift into two implementations.

This module is offline: it never starts a service, opens a network
connection, reads the root ``.env``, accesses a database, executes code or
activates the production Runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from omnibase.production.composition import ConfigurationError
from omnibase.production.joint_gate import (
    _FORBIDDEN_ENV_NAMES,
    _GIT_OBJECT_FORMATS,
    _REQUIRED_COMMANDS,
    _bool,
    _git_oid,
    _int,
    _keys,
    _list,
    _object,
    _opt_string,
    _relative_path,
    _sha256,
    _string,
    _utc_instant,
)
from omnibase.production.phase5_admission import discover_migration_head

CANDIDATE_SCHEMA = "omnibase.p34-7.trust-policy-candidate.v1"
APPROVAL_PACKET_SCHEMA = "omnibase.p34-7.trust-policy-approval-packet.v1"
SCHEMA_VERSION = "1"
MIGRATION_HEAD = "0013"

REQUIRED_ROLES = ("core", "runner", "broker", "gateway", "overlay", "recovery_sla", "sealer")
_SEALER = "sealer"

# Frozen per-role signing-scope matrix.  A producer may only declare scopes
# from its own row; wildcards and arbitrary extension scopes are rejected.
ROLE_SIGNING_SCOPES: dict[str, frozenset[str]] = {
    "core": frozenset({"core_runtime_posture", "core_runner_request_identity"}),
    "runner": frozenset(
        {"linux_runner_isolation", "runner_command_receipt", "runner_attack_matrix"}
    ),
    "broker": frozenset({"broker_namespace", "broker_identity", "broker_budget_replay"}),
    "gateway": frozenset({"gateway_mtls", "gateway_certificate", "gateway_capability_boundary"}),
    "overlay": frozenset({"overlay_membership", "overlay_derp", "overlay_node_compromise"}),
    "recovery_sla": frozenset({"provider_recovery", "capacity_fault_injection", "sla_measurement"}),
    "sealer": frozenset({"evidence_seal", "cleanup_inventory"}),
}
_ALL_SCOPES: frozenset[str] = frozenset().union(
    *(ROLE_SIGNING_SCOPES[role] for role in REQUIRED_ROLES)
)
_WILDCARD_TOKENS = frozenset({"*", "**", "*.*", "any", "all"})

# Key lifecycle states (closed set).  The R0 candidate file may only carry
# keys in the pre-approval states; ``active``/``rotating`` are history /
# future-compatibility states and can never be constructed by this validator.
KEY_LIFECYCLE_STATES = frozenset(
    {"generated", "registered", "candidate", "active", "rotating", "revoked", "archived"}
)
R0_CANDIDATE_KEY_STATES = frozenset({"generated", "registered", "candidate"})

CUSTODY_KINDS = frozenset(
    {
        "operator_offline",
        "hsm_planned",
        "kms_planned",
        "remote_runner_local",
        "external_signing_service_planned",
    }
)

# Approval-packet decision closed set.  Production-approving decisions are
# FORBIDDEN in R0.
DECISION_STATES = frozenset({"draft", "candidate", "rejected", "superseded", "revoked"})
FORBIDDEN_DECISIONS = frozenset(
    {"approved", "approved_for_production", "production_ready", "passed", "published"}
)

# Legal key-lifecycle transitions (closed set).  ``rotating -> active`` is
# only legal for a REPLACEMENT key.
LEGAL_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("generated", "registered"),
        ("registered", "candidate"),
        ("candidate", "rejected"),
        ("candidate", "superseded"),
        ("candidate", "revoked"),
        ("active", "rotating"),
        ("active", "revoked"),
        ("rotating", "active"),
        ("rotating", "revoked"),
        ("revoked", "archived"),
    }
)

# Forbidden secret-shaped field names, matched after normalizing any case /
# separators (privateKey, signing_seed, bearer-token, ...).
_FORBIDDEN_SECRET_NAMES = frozenset(
    {
        "privatekey",
        "privatekeyhex",
        "privatekeypem",
        "seed",
        "signingseed",
        "mnemonic",
        "secret",
        "passphrase",
        "apikey",
        "bearertoken",
        "accesstoken",
        "refreshtoken",
        "token",
        "password",
        "databasepassword",
        "providercredential",
        "clientsecret",
        "jwt",
        "rootenv",
        "dotenv",
    }
)

_IDENTITY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _normalize_name(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())


# Sensitive environment-name tokens, matched after case/separator
# normalization ("OPENAI_API_KEY" -> "openai_apikey", "OpenAiApiKey" ->
# "openaiapikey", "postgres_password" -> "postgrespassword").  A normalized
# env name containing ANY of these tokens is rejected.
_SENSITIVE_ENV_TOKENS = frozenset(
    {
        "apikey",
        "token",
        "password",
        "secret",
        "credential",
        "privatekey",
        "accesskey",
        "databasepassword",
        "databaseurl",
        "dsn",
        "connectionstring",
        "jwt",
        "bearer",
        "clientsecret",
        "mnemonic",
        "passphrase",
        "seed",
        "auth",
        "session",
        "cookie",
    }
)


def _forbidden_env_name(name: str) -> bool:
    """Case-insensitive, separator/camelCase-aware sensitive env-name check."""
    normalized = _normalize_name(name)
    return any(token in normalized for token in _SENSITIVE_ENV_TOKENS)


def _looks_like_env_locator(value: str) -> bool:
    """Root ``.env`` locator detection covering ``/``, ``\\``, Windows drive
    paths, case variants and normalized paths (``.env``, ``./.env``,
    ``.ENV``, ``E:\\...\\.env``, ``C:/foo/.Env``).  Errors never echo the
    offending value."""
    normalized = value.replace("\\", "/").lower()
    return normalized in {".env", "./.env", "../.env", "/.env"} or normalized.endswith("/.env")


def _require_identity(value: object, name: str) -> str:
    """A logical identity: non-secret, stable, format-restricted.  Never a
    Browser JWT or an email password."""
    text = _string(value, name)
    if not _IDENTITY_PATTERN.fullmatch(text):
        raise ConfigurationError(
            f"{name} must be a format-restricted logical identity "
            "(lowercase alphanumeric start, [a-z0-9._-], <= 64 chars)"
        )
    return text


def scan_forbidden_secrets(value: object, name: str = "payload") -> None:
    """Recursively scan any DTO for forbidden secret-shaped fields.

    Covers arbitrary case and separators (``private_key``, ``privateKey``,
    ``private-key``, ``signingSeed``, ``bearer_token``, ``api_key``,
    ``password``, ...) at every nesting level, plus root ``.env`` path
    locators as values (``.env``, ``./.env``, ``.ENV``, backslash paths,
    Windows drive paths and normalized variants).  Any hit fails closed with
    a stable contract error that never leaks the offending value."""
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ConfigurationError(f"{name}: object keys must be strings")
            if _normalize_name(key) in _FORBIDDEN_SECRET_NAMES:
                raise ConfigurationError(f"{name}.{key}: forbidden secret-shaped field")
            scan_forbidden_secrets(child, f"{name}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            scan_forbidden_secrets(child, f"{name}[{index}]")
    elif isinstance(value, str) and _looks_like_env_locator(value):
        raise ConfigurationError(f"{name}: root .env locator is forbidden")


def _check_locator_free(value: object, name: str) -> None:
    """Fail-closed locator check for strings that can carry a path (env
    names, argv entries, identity-adjacent values)."""
    if isinstance(value, str) and _looks_like_env_locator(value):
        raise ConfigurationError(f"{name}: root .env locator is forbidden")


# ---------------------------------------------------------------------------
# DTO contracts (frozen, closed-set)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SigningScope:
    scope: str


@dataclass(frozen=True, slots=True)
class KeyCustodyMetadata:
    """Custody metadata is PLANNED posture only: ``custody_kind`` is never
    treated as proof of an actual HSM/KMS.  Any custody posture that is not
    really proven must be reported ``not_proven``."""

    custody_kind: str
    custody_posture: str = "not_proven"


@dataclass(frozen=True, slots=True)
class RevocationRecord:
    revocation_record_id: str
    key_id: str
    role: str
    revoked_at: datetime
    reason: str
    superseded_by_key_id: str | None = None


@dataclass(frozen=True, slots=True)
class PublicKeyRegistration:
    key_id: str
    role: str
    algorithm: str
    public_key: str
    fingerprint_sha256: str
    owner_id: str
    backup_owner_id: str | None
    created_at: datetime
    candidate_from: datetime
    planned_expiry: datetime | None
    lifecycle_state: str
    custody_kind: str
    allowed_signing_scopes: frozenset[str]
    replaces_key_id: str | None
    revocation_record_id: str | None


@dataclass(frozen=True, slots=True)
class ProducerRoleRegistration:
    role: str
    owner_id: str
    backup_owner_id: str | None
    keys: tuple[PublicKeyRegistration, ...]
    allowed_signing_scopes: frozenset[str]


@dataclass(frozen=True, slots=True)
class SourceSealCandidate:
    repository: str
    git_object_format: str
    approved_commits: frozenset[str]
    approved_trees: frozenset[str]
    candidate_only: bool
    production_approved: bool


@dataclass(frozen=True, slots=True)
class ArtifactApprovalCandidate:
    path: str
    sha256: str
    commands: frozenset[str]


@dataclass(frozen=True, slots=True)
class CommandTemplateCandidate:
    command: str
    argv: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class GatewayTrustCandidate:
    issuer: str
    san_suffix: str
    validity_seconds: int


@dataclass(frozen=True, slots=True)
class EvidenceFreshnessCandidate:
    max_evidence_age_seconds: int


@dataclass(frozen=True, slots=True)
class RotationEntry:
    key_id: str
    role: str
    from_state: str
    to_state: str
    planned_at: datetime
    replaces_key_id: str | None


@dataclass(frozen=True, slots=True)
class RotationPlan:
    entries: tuple[RotationEntry, ...]


@dataclass(frozen=True, slots=True)
class SupersessionLink:
    supersedes_policy_sha256: str | None
    superseded_at: datetime | None
    reason: str | None


@dataclass(frozen=True, slots=True)
class TrustPolicyCandidate:
    schema: str
    schema_version: str
    policy_id: str
    lifecycle_state: str
    candidate_only: bool
    production_approved: bool
    created_at: datetime
    author_id: str
    producers: tuple[ProducerRoleRegistration, ...]
    source_seal: SourceSealCandidate
    artifact_approvals: tuple[ArtifactApprovalCandidate, ...]
    commands: tuple[CommandTemplateCandidate, ...]
    allowed_env_names: frozenset[str]
    gateway: GatewayTrustCandidate
    evidence_freshness: EvidenceFreshnessCandidate
    rotation_plan: RotationPlan
    revocation_records: tuple[RevocationRecord, ...]
    supersession: SupersessionLink | None


@dataclass(frozen=True, slots=True)
class ApprovalReview:
    reviewer_id: str
    review_started_at: datetime
    review_completed_at: datetime
    decision: str
    decision_reason: str


@dataclass(frozen=True, slots=True)
class ApprovalPacket:
    schema: str
    schema_version: str
    candidate_policy_path: str
    candidate_policy_raw_sha256: str
    candidate_schema: str
    candidate_schema_version: str
    repository: str
    git_object_format: str
    candidate_commits: frozenset[str]
    candidate_trees: frozenset[str]
    producer_key_fingerprints: frozenset[str]
    artifact_manifest_sha256: str
    command_templates_sha256: str
    env_allowlist_sha256: str
    gateway_policy_sha256: str
    max_evidence_age_seconds: int
    author_id: str
    reviewer_ids: tuple[str, ...]
    review_started_at: datetime
    review_completed_at: datetime
    decision: str
    decision_reason: str
    supersedes_policy_sha256: str | None
    rollback_policy_sha256: str | None


@dataclass(frozen=True, slots=True)
class CandidateValidationReport:
    """Outcome of a P34.7 trust-policy candidate check.

    ``status`` is ``candidate/valid_not_approved`` (the highest positive R0
    status) or ``invalid/veto`` (raised as :class:`ConfigurationError`).
    ``production_approved``, ``approved_digest_written`` and
    ``activation_allowed`` are always ``False``."""

    contract_valid: bool
    candidate_digest_verified: bool
    role_set_verified: bool
    key_uniqueness_verified: bool
    source_seal_verified: bool
    approval_packet_verified: bool
    author_reviewer_separation_verified: bool
    producer_approver_separation_verified: bool
    forbidden_secret_fields_absent: bool
    lifecycle_valid: bool
    production_approved: bool
    approved_digest_written: bool
    activation_allowed: bool
    root_env_accessed: bool
    business_database_accessed: bool
    business_database_migrated: bool
    runtime_activated: bool
    migration_head: str
    migration_0013_created: bool
    feature_gates: dict[str, bool]
    status: str
    blockers: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": CANDIDATE_SCHEMA,
            "schema_version": SCHEMA_VERSION,
            "status": self.status,
            "contract_valid": self.contract_valid,
            "candidate_digest_verified": self.candidate_digest_verified,
            "role_set_verified": self.role_set_verified,
            "key_uniqueness_verified": self.key_uniqueness_verified,
            "source_seal_verified": self.source_seal_verified,
            "approval_packet_verified": self.approval_packet_verified,
            "author_reviewer_separation_verified": self.author_reviewer_separation_verified,
            "producer_approver_separation_verified": self.producer_approver_separation_verified,
            "forbidden_secret_fields_absent": self.forbidden_secret_fields_absent,
            "lifecycle_valid": self.lifecycle_valid,
            "production_approved": self.production_approved,
            "approved_digest_written": self.approved_digest_written,
            "activation_allowed": self.activation_allowed,
            "root_env_accessed": self.root_env_accessed,
            "business_database_accessed": self.business_database_accessed,
            "business_database_migrated": self.business_database_migrated,
            "runtime_activated": self.runtime_activated,
            "migration_head": self.migration_head,
            "migration_0013_created": self.migration_0013_created,
            "feature_gates": dict(self.feature_gates),
            "blockers": list(self.blockers),
        }


# ---------------------------------------------------------------------------
# Strict from_mapping parsers (reuse joint_gate low-level helpers)
# ---------------------------------------------------------------------------


def _parse_signing_scope(value: object, name: str) -> str:
    scope = _string(value, name)
    if scope in _WILDCARD_TOKENS or "*" in scope:
        raise ConfigurationError(f"{name}: wildcard signing scopes are forbidden")
    if scope not in _ALL_SCOPES:
        raise ConfigurationError(f"{name}: unknown signing scope")
    return scope


def _parse_scopes(value: object, name: str, role: str) -> frozenset[str]:
    raw = _list(value, name)
    if not raw:
        raise ConfigurationError(f"{name} must be non-empty")
    parsed = tuple(_parse_signing_scope(item, f"{name}[{i}]") for i, item in enumerate(raw))
    if len(set(parsed)) != len(parsed):
        raise ConfigurationError(f"{name}: duplicate signing scopes")
    allowed = ROLE_SIGNING_SCOPES[role]
    if set(parsed) != allowed:
        raise ConfigurationError(
            f"{name}: role '{role}' may only declare exactly {sorted(allowed)}"
        )
    return frozenset(parsed)


def _parse_revoked_scopes(value: object, name: str, role: str) -> frozenset[str]:
    """A revoked historical key holds NO signing authority: its scope list
    must be empty (the frozen per-role matrix still applies to every CURRENT
    key, so a revoked key can never appear in the producer signing
    allowlist)."""
    raw = _list(value, name)
    if raw:
        raise ConfigurationError(
            f"{name}: a revoked key must declare no signing scopes (empty list)"
        )
    return frozenset()


def _parse_public_key(value: object, name: str) -> str:
    key = _string(value, name)
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key):
        raise ConfigurationError(f"{name} must be a 64-hex lowercase Ed25519 public key")
    if key == "0" * 64:
        raise ConfigurationError(f"{name}: all-zero public keys are rejected")
    return key


def _require_key_time_invariants(
    name: str,
    lifecycle_state: str,
    created_at: datetime,
    candidate_from: datetime,
    planned_expiry: datetime | None,
    candidate_created_at: datetime,
) -> None:
    """Full key validity interval and policy-time binding:

    * ``created_at <= candidate_from`` and, when set,
      ``planned_expiry > created_at`` and ``planned_expiry > candidate_from``
      (strict);
    * every key: ``created_at <= candidate.created_at``;
    * ``candidate``/``revoked`` keys: ``candidate_from <=
      candidate.created_at`` (a ``generated``/``registered`` key MAY declare
      a FUTURE ``candidate_from`` -- a plan only, it does not claim to have
      entered the candidate yet)."""
    if candidate_from < created_at:
        raise ConfigurationError(f"{name}.candidate_from must not precede created_at")
    if planned_expiry is not None and planned_expiry <= created_at:
        raise ConfigurationError(
            f"{name}.planned_expiry must be strictly after created_at "
            "(equal or earlier instants are rejected)"
        )
    if planned_expiry is not None and planned_expiry <= candidate_from:
        raise ConfigurationError(
            f"{name}.planned_expiry must be strictly after candidate_from "
            "(equal or earlier instants are rejected)"
        )
    if created_at > candidate_created_at:
        raise ConfigurationError(
            f"{name}.created_at must not be after the candidate creation timestamp"
        )
    if lifecycle_state in ("candidate", "revoked") and candidate_from > candidate_created_at:
        raise ConfigurationError(
            f"{name}.candidate_from must not be after the candidate creation timestamp "
            "for a candidate/revoked key"
        )


def _parse_key_registration(
    value: object,
    name: str,
    role: str,
    *,
    candidate_lifecycle: str,
    candidate_created_at: datetime,
) -> PublicKeyRegistration:
    data = _object(value, name)
    _keys(
        data,
        {
            "key_id",
            "role",
            "algorithm",
            "public_key",
            "fingerprint_sha256",
            "owner_id",
            "backup_owner_id",
            "created_at",
            "candidate_from",
            "planned_expiry",
            "lifecycle_state",
            "custody_kind",
            "allowed_signing_scopes",
            "replaces_key_id",
            "revocation_record_id",
        },
        name,
    )
    key_id = _string(data.get("key_id"), f"{name}.key_id")
    declared_role = _string(data.get("role"), f"{name}.role")
    if declared_role != role:
        raise ConfigurationError(f"{name}.role must equal the owning role")
    if _string(data.get("algorithm"), f"{name}.algorithm") != "ed25519":
        raise ConfigurationError(f"{name}.algorithm must be ed25519")
    public_key = _parse_public_key(data.get("public_key"), f"{name}.public_key")
    fingerprint = _sha256(data.get("fingerprint_sha256"), f"{name}.fingerprint_sha256")
    if fingerprint != hashlib.sha256(bytes.fromhex(public_key)).hexdigest():
        raise ConfigurationError(f"{name}.fingerprint_sha256 does not match the public key")
    owner_id = _require_identity(data.get("owner_id"), f"{name}.owner_id")
    backup_owner_id = _opt_identity(data.get("backup_owner_id"), f"{name}.backup_owner_id")
    created_at = _utc_instant(data.get("created_at"), f"{name}.created_at")
    candidate_from = _utc_instant(data.get("candidate_from"), f"{name}.candidate_from")
    planned_expiry = (
        _utc_instant(data.get("planned_expiry"), f"{name}.planned_expiry")
        if data.get("planned_expiry") is not None
        else None
    )
    lifecycle_state = _string(data.get("lifecycle_state"), f"{name}.lifecycle_state")
    _require_key_time_invariants(
        name,
        lifecycle_state,
        created_at,
        candidate_from,
        planned_expiry,
        candidate_created_at,
    )
    if lifecycle_state not in KEY_LIFECYCLE_STATES:
        raise ConfigurationError(f"{name}.lifecycle_state is unknown")
    replaces_key_id = _opt_string(data.get("replaces_key_id"), f"{name}.replaces_key_id")
    revocation_record_id: str | None
    if lifecycle_state == "revoked":
        # A revoked key is HISTORY: it may only exist inside a candidate that
        # declares lifecycle_state == "revoked", it holds NO signing scopes
        # (it can never appear in the producer signing allowlist) and it must
        # be bound to exactly one revocation record via revocation_record_id.
        if candidate_lifecycle != "revoked":
            raise ConfigurationError(
                f"{name}: a revoked key is only allowed inside a revoked candidate"
            )
        scopes = _parse_revoked_scopes(
            data.get("allowed_signing_scopes"), f"{name}.allowed_signing_scopes", role
        )
        revocation_record_id = _string(
            data.get("revocation_record_id"), f"{name}.revocation_record_id"
        )
    else:
        if lifecycle_state not in R0_CANDIDATE_KEY_STATES:
            raise ConfigurationError(
                f"{name}.lifecycle_state must stay in the R0 pre-approval set "
                "(active/rotating cannot be constructed by a candidate validator)"
            )
        scopes = _parse_scopes(
            data.get("allowed_signing_scopes"), f"{name}.allowed_signing_scopes", role
        )
        revocation_record_id = _opt_string(
            data.get("revocation_record_id"), f"{name}.revocation_record_id"
        )
        if revocation_record_id is not None:
            raise ConfigurationError(
                f"{name}: a non-revoked key must not carry a revocation_record_id"
            )
    custody_kind = _string(data.get("custody_kind"), f"{name}.custody_kind")
    if custody_kind not in CUSTODY_KINDS:
        raise ConfigurationError(f"{name}.custody_kind is unknown")
    return PublicKeyRegistration(
        key_id=key_id,
        role=role,
        algorithm="ed25519",
        public_key=public_key,
        fingerprint_sha256=fingerprint,
        owner_id=owner_id,
        backup_owner_id=backup_owner_id,
        created_at=created_at,
        candidate_from=candidate_from,
        planned_expiry=planned_expiry,
        lifecycle_state=lifecycle_state,
        custody_kind=custody_kind,
        allowed_signing_scopes=scopes,
        replaces_key_id=replaces_key_id,
        revocation_record_id=revocation_record_id,
    )


def _opt_identity(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _require_identity(value, name)


def _parse_producer_registration(
    value: object,
    name: str,
    role: str,
    *,
    candidate_lifecycle: str,
    candidate_created_at: datetime,
) -> ProducerRoleRegistration:
    data = _object(value, name)
    _keys(
        data,
        {"role", "owner_id", "backup_owner_id", "keys", "allowed_signing_scopes"},
        name,
    )
    if _string(data.get("role"), f"{name}.role") != role:
        raise ConfigurationError(f"{name}.role must equal the owning role")
    owner_id = _require_identity(data.get("owner_id"), f"{name}.owner_id")
    backup_owner_id = _opt_identity(data.get("backup_owner_id"), f"{name}.backup_owner_id")
    keys_raw = _list(data.get("keys"), f"{name}.keys")
    if not keys_raw:
        raise ConfigurationError(f"{name}.keys must be non-empty")
    keys = tuple(
        _parse_key_registration(
            item,
            f"{name}.keys[{i}]",
            role,
            candidate_lifecycle=candidate_lifecycle,
            candidate_created_at=candidate_created_at,
        )
        for i, item in enumerate(keys_raw)
    )
    scopes = _parse_scopes(
        data.get("allowed_signing_scopes"), f"{name}.allowed_signing_scopes", role
    )
    seen_key_ids: set[str] = set()
    for key in keys:
        if key.key_id in seen_key_ids:
            raise ConfigurationError(f"{name}.keys: duplicate key_id")
        seen_key_ids.add(key.key_id)
        if key.lifecycle_state != "revoked" and key.allowed_signing_scopes != scopes:
            raise ConfigurationError(
                f"{name}.keys: key scopes must match the role registration scopes"
            )
    return ProducerRoleRegistration(
        role=role,
        owner_id=owner_id,
        backup_owner_id=backup_owner_id,
        keys=keys,
        allowed_signing_scopes=scopes,
    )


def _parse_source_seal(value: object, name: str) -> SourceSealCandidate:
    data = _object(value, name)
    _keys(
        data,
        {
            "repository",
            "git_object_format",
            "approved_commits",
            "approved_trees",
            "candidate_only",
            "production_approved",
        },
        name,
    )
    repository = _string(data.get("repository"), f"{name}.repository")
    if not repository.startswith(("https://github.com/", "git@github.com:")):
        raise ConfigurationError(f"{name}.repository must be a GitHub remote")
    object_format = _string(data.get("git_object_format"), f"{name}.git_object_format")
    if object_format not in _GIT_OBJECT_FORMATS:
        raise ConfigurationError(f"{name}.git_object_format must be 'sha1' or 'sha256'")
    commits = _list(data.get("approved_commits"), f"{name}.approved_commits")
    trees = _list(data.get("approved_trees"), f"{name}.approved_trees")
    for item in commits:
        _git_oid(item, f"{name}.approved_commits[]", object_format)
    for item in trees:
        _git_oid(item, f"{name}.approved_trees[]", object_format)
    if _bool(data.get("candidate_only"), f"{name}.candidate_only") is not True:
        raise ConfigurationError(f"{name}.candidate_only must be true")
    if _bool(data.get("production_approved"), f"{name}.production_approved") is not False:
        raise ConfigurationError(f"{name}.production_approved must be false")
    return SourceSealCandidate(
        repository=repository,
        git_object_format=object_format,
        approved_commits=frozenset(c for c in commits if isinstance(c, str)),
        approved_trees=frozenset(t for t in trees if isinstance(t, str)),
        candidate_only=True,
        production_approved=False,
    )


def _parse_artifact_approval(value: object, name: str, map_key: str) -> ArtifactApprovalCandidate:
    data = _object(value, name)
    _keys(data, {"path", "sha256", "commands"}, name)
    path = _relative_path(data.get("path"), f"{name}.path")
    if path != map_key:
        raise ConfigurationError(f"{name}.path must equal the map key")
    digest = _sha256(data.get("sha256"), f"{name}.sha256")
    commands = _list(data.get("commands"), f"{name}.commands")
    if not commands or not all(isinstance(c, str) and c in _REQUIRED_COMMANDS for c in commands):
        raise ConfigurationError(f"{name}.commands must reference required joint boundaries only")
    if len(set(commands)) != len(commands):
        raise ConfigurationError(f"{name}.commands must not repeat a command")
    return ArtifactApprovalCandidate(
        path=path,
        sha256=digest,
        commands=frozenset(c for c in commands if isinstance(c, str)),
    )


def _verify_artifact_coverage(artifact_approvals: tuple[ArtifactApprovalCandidate, ...]) -> None:
    """The artifact-approval set must be non-empty and cover the six required
    joint commands exactly once each (no missing, duplicate, unknown or
    key/path-drifted coverage).  R0 validates the candidate PIN CONTRACT
    only: real artifact file bytes are NOT verified by this module."""
    if not artifact_approvals:
        raise ConfigurationError("trust policy candidate.artifact_approvals must be non-empty")
    seen_paths: set[str] = set()
    coverage: set[str] = set()
    for approval in artifact_approvals:
        if approval.path in seen_paths:
            raise ConfigurationError(
                f"trust policy candidate.artifact_approvals: duplicate path {approval.path}"
            )
        seen_paths.add(approval.path)
        for command in approval.commands:
            if command in coverage:
                raise ConfigurationError(
                    f"trust policy candidate.artifact_approvals: "
                    f"required command {command} is covered more than once"
                )
            coverage.add(command)
    if coverage != set(_REQUIRED_COMMANDS):
        missing = sorted(set(_REQUIRED_COMMANDS) - coverage)
        raise ConfigurationError(
            "trust policy candidate.artifact_approvals must cover every required "
            f"joint command exactly once; missing: {', '.join(missing)}"
        )


def _parse_command_template(value: object, name: str, map_key: str) -> CommandTemplateCandidate:
    data = _object(value, name)
    _keys(data, {"command", "argv"}, name)
    command = _string(data.get("command"), f"{name}.command")
    if command not in _REQUIRED_COMMANDS:
        raise ConfigurationError(f"{name}.command must be a required joint boundary")
    if command != map_key:
        raise ConfigurationError(f"{name}.command must equal the map key")
    argv = _list(data.get("argv"), f"{name}.argv")
    if not argv or not all(isinstance(a, str) and a for a in argv):
        raise ConfigurationError(f"{name}.argv must be a non-empty argv template")
    for index, entry in enumerate(argv):
        _check_locator_free(entry, f"{name}.argv[{index}]")
    return CommandTemplateCandidate(
        command=command, argv=tuple(a for a in argv if isinstance(a, str))
    )


def _parse_gateway_trust(value: object, name: str) -> GatewayTrustCandidate:
    data = _object(value, name)
    _keys(data, {"issuer", "san_suffix", "validity_seconds"}, name)
    issuer = _sha256(data.get("issuer"), f"{name}.issuer")
    san_suffix = _string(data.get("san_suffix"), f"{name}.san_suffix")
    if not san_suffix.startswith("."):
        raise ConfigurationError(f"{name}.san_suffix must start with a dot")
    validity = _int(data.get("validity_seconds"), f"{name}.validity_seconds")
    if validity <= 0 or validity > 200 * 365 * 86400:
        raise ConfigurationError(f"{name}.validity_seconds must be a positive bounded window")
    return GatewayTrustCandidate(issuer=issuer, san_suffix=san_suffix, validity_seconds=validity)


def _parse_freshness(value: object, name: str) -> EvidenceFreshnessCandidate:
    data = _object(value, name)
    _keys(data, {"max_evidence_age_seconds"}, name)
    max_age = _int(data.get("max_evidence_age_seconds"), f"{name}.max_evidence_age_seconds")
    if max_age <= 0 or max_age > 365 * 86400:
        raise ConfigurationError(f"{name}.max_evidence_age_seconds must be a bounded window")
    return EvidenceFreshnessCandidate(max_evidence_age_seconds=max_age)


def _parse_rotation_entry(value: object, name: str) -> RotationEntry:
    data = _object(value, name)
    _keys(
        data,
        {"key_id", "role", "from_state", "to_state", "planned_at", "replaces_key_id"},
        name,
    )
    key_id = _string(data.get("key_id"), f"{name}.key_id")
    role = _string(data.get("role"), f"{name}.role")
    if role not in REQUIRED_ROLES:
        raise ConfigurationError(f"{name}.role is unknown")
    from_state = _string(data.get("from_state"), f"{name}.from_state")
    to_state = _string(data.get("to_state"), f"{name}.to_state")
    if from_state not in KEY_LIFECYCLE_STATES or to_state not in KEY_LIFECYCLE_STATES:
        raise ConfigurationError(f"{name}: unknown lifecycle state")
    if (from_state, to_state) not in LEGAL_TRANSITIONS:
        raise ConfigurationError(f"{name}: illegal lifecycle transition {from_state} -> {to_state}")
    if key_id == data.get("replaces_key_id"):
        raise ConfigurationError(f"{name}: a key cannot replace itself")
    planned_at = _utc_instant(data.get("planned_at"), f"{name}.planned_at")
    replaces = _opt_string(data.get("replaces_key_id"), f"{name}.replaces_key_id")
    return RotationEntry(
        key_id=key_id,
        role=role,
        from_state=from_state,
        to_state=to_state,
        planned_at=planned_at,
        replaces_key_id=replaces,
    )


def _parse_rotation_plan(value: object, name: str) -> RotationPlan:
    data = _object(value, name)
    _keys(data, {"entries"}, name)
    entries_raw = _list(data.get("entries"), f"{name}.entries")
    entries = tuple(
        _parse_rotation_entry(item, f"{name}.entries[{i}]") for i, item in enumerate(entries_raw)
    )
    return RotationPlan(entries=entries)


def _parse_revocation_record(value: object, name: str) -> RevocationRecord:
    data = _object(value, name)
    _keys(
        data,
        {"revocation_record_id", "key_id", "role", "revoked_at", "reason", "superseded_by_key_id"},
        name,
    )
    record_id = _string(data.get("revocation_record_id"), f"{name}.revocation_record_id")
    key_id = _string(data.get("key_id"), f"{name}.key_id")
    role = _string(data.get("role"), f"{name}.role")
    if role not in REQUIRED_ROLES:
        raise ConfigurationError(f"{name}.role is unknown")
    revoked_at = _utc_instant(data.get("revoked_at"), f"{name}.revoked_at")
    reason = _string(data.get("reason"), f"{name}.reason")
    superseded = _opt_string(data.get("superseded_by_key_id"), f"{name}.superseded_by_key_id")
    return RevocationRecord(
        revocation_record_id=record_id,
        key_id=key_id,
        role=role,
        revoked_at=revoked_at,
        reason=reason,
        superseded_by_key_id=superseded,
    )


def _parse_supersession(value: object, name: str) -> SupersessionLink | None:
    if value is None:
        return None
    data = _object(value, name)
    _keys(data, {"supersedes_policy_sha256", "superseded_at", "reason"}, name)
    supersedes = (
        _sha256(data.get("supersedes_policy_sha256"), f"{name}.supersedes_policy_sha256")
        if data.get("supersedes_policy_sha256") is not None
        else None
    )
    superseded_at = (
        _utc_instant(data.get("superseded_at"), f"{name}.superseded_at")
        if data.get("superseded_at") is not None
        else None
    )
    reason = _opt_string(data.get("reason"), f"{name}.reason")
    return SupersessionLink(
        supersedes_policy_sha256=supersedes,
        superseded_at=superseded_at,
        reason=reason,
    )


_CANDIDATE_KEYS: set[str] = {
    "schema",
    "schema_version",
    "policy_id",
    "lifecycle_state",
    "candidate_only",
    "production_approved",
    "created_at",
    "author_id",
    "producers",
    "source_seal",
    "artifact_approvals",
    "commands",
    "allowed_env_names",
    "gateway",
    "evidence_freshness",
    "rotation_plan",
    "revocation_records",
    "supersession",
}


def _parse_allowed_env_names(data: dict[str, Any], name: str) -> frozenset[str]:
    """Env allowlist with the closed checks: non-empty strings, no duplicate
    entries (a repeated name is vetoed even when the section digest binds the
    repeated list), no locator-shaped entries (any separator/case/Windows
    variant), and no sensitive env names after case/separator
    normalization."""
    env_names = _list(data.get("allowed_env_names"), f"{name}.allowed_env_names")
    if not env_names or not all(isinstance(n, str) and n for n in env_names):
        raise ConfigurationError(f"{name}.allowed_env_names must be non-empty strings")
    if len(set(env_names)) != len(env_names):
        raise ConfigurationError(f"{name}.allowed_env_names must not repeat a name")
    for index, env_name in enumerate(env_names):
        _check_locator_free(env_name, f"{name}.allowed_env_names[{index}]")
        if _forbidden_env_name(str(env_name)):
            raise ConfigurationError(
                f"{name}.allowed_env_names[{index}]: sensitive environment name is forbidden"
            )
    if any(n in _FORBIDDEN_ENV_NAMES for n in env_names):
        raise ConfigurationError(f"{name}.allowed_env_names must not include secret env names")
    return frozenset(n for n in env_names if isinstance(n, str))


def _parse_candidate(value: object) -> TrustPolicyCandidate:
    data = _object(value, "trust policy candidate")
    scan_forbidden_secrets(data, "trust policy candidate")
    _keys(data, _CANDIDATE_KEYS, "trust policy candidate")
    if _string(data.get("schema"), "trust policy candidate.schema") != CANDIDATE_SCHEMA:
        raise ConfigurationError("trust policy candidate must use the frozen candidate schema")
    if (
        _string(data.get("schema_version"), "trust policy candidate.schema_version")
        != SCHEMA_VERSION
    ):
        raise ConfigurationError("trust policy candidate must use schema version 1")
    policy_id = _string(data.get("policy_id"), "trust policy candidate.policy_id")
    lifecycle = _string(data.get("lifecycle_state"), "trust policy candidate.lifecycle_state")
    if lifecycle not in DECISION_STATES:
        raise ConfigurationError(
            "trust policy candidate.lifecycle_state must be in the R0 decision set"
        )
    if _bool(data.get("candidate_only"), "trust policy candidate.candidate_only") is not True:
        raise ConfigurationError("trust policy candidate.candidate_only must be true")
    if (
        _bool(data.get("production_approved"), "trust policy candidate.production_approved")
        is not False
    ):
        raise ConfigurationError("trust policy candidate.production_approved must be false")
    created_at = _utc_instant(data.get("created_at"), "trust policy candidate.created_at")
    author_id = _require_identity(data.get("author_id"), "trust policy candidate.author_id")
    producers_raw = _object(data.get("producers"), "trust policy candidate.producers")
    if set(producers_raw) != set(REQUIRED_ROLES):
        raise ConfigurationError(
            "trust policy candidate.producers must contain exactly the seven roles"
        )
    producers = tuple(
        _parse_producer_registration(
            producers_raw.get(role),
            f"trust policy candidate.producers.{role}",
            role,
            candidate_lifecycle=lifecycle,
            candidate_created_at=created_at,
        )
        for role in REQUIRED_ROLES
    )
    source_seal = _parse_source_seal(data.get("source_seal"), "trust policy candidate.source_seal")
    artifacts_raw = _object(
        data.get("artifact_approvals"), "trust policy candidate.artifact_approvals"
    )
    if not artifacts_raw:
        raise ConfigurationError("trust policy candidate.artifact_approvals must be non-empty")
    artifact_approvals = tuple(
        _parse_artifact_approval(
            artifacts_raw.get(path),
            f"trust policy candidate.artifact_approvals.{path}",
            _relative_path(path, "trust policy candidate.artifact_approvals key"),
        )
        for path in sorted(artifacts_raw)
    )
    _verify_artifact_coverage(artifact_approvals)
    commands_raw = _object(data.get("commands"), "trust policy candidate.commands")
    if set(commands_raw) != set(_REQUIRED_COMMANDS):
        raise ConfigurationError(
            "trust policy candidate.commands must contain exactly the six joint boundaries"
        )
    commands = tuple(
        _parse_command_template(
            commands_raw.get(command),
            f"trust policy candidate.commands.{command}",
            command,
        )
        for command in _REQUIRED_COMMANDS
    )
    allowed_env_names = _parse_allowed_env_names(data, "trust policy candidate")
    gateway = _parse_gateway_trust(data.get("gateway"), "trust policy candidate.gateway")
    freshness = _parse_freshness(
        data.get("evidence_freshness"), "trust policy candidate.evidence_freshness"
    )
    rotation_plan = _parse_rotation_plan(
        data.get("rotation_plan"), "trust policy candidate.rotation_plan"
    )
    revocations_raw = _list(
        data.get("revocation_records"), "trust policy candidate.revocation_records"
    )
    revocation_records = tuple(
        _parse_revocation_record(item, f"trust policy candidate.revocation_records[{i}]")
        for i, item in enumerate(revocations_raw)
    )
    supersession = _parse_supersession(
        data.get("supersession"), "trust policy candidate.supersession"
    )
    return TrustPolicyCandidate(
        schema=CANDIDATE_SCHEMA,
        schema_version=SCHEMA_VERSION,
        policy_id=policy_id,
        lifecycle_state=lifecycle,
        candidate_only=True,
        production_approved=False,
        created_at=created_at,
        author_id=author_id,
        producers=producers,
        source_seal=source_seal,
        artifact_approvals=artifact_approvals,
        commands=commands,
        allowed_env_names=allowed_env_names,
        gateway=gateway,
        evidence_freshness=freshness,
        rotation_plan=rotation_plan,
        revocation_records=revocation_records,
        supersession=supersession,
    )


def _parse_packet_identities(data: dict[str, Any]) -> tuple[str, tuple[str, ...]]:
    """Author/reviewer identities with separation checks: the author cannot be
    a reviewer (self-approval), reviewers must be distinct logical
    identities."""
    author_id = _require_identity(data.get("author_id"), "approval packet.author_id")
    reviewers_raw = _list(data.get("reviewer_ids"), "approval packet.reviewer_ids")
    if len(reviewers_raw) < 1:
        raise ConfigurationError("approval packet.reviewer_ids must be non-empty")
    reviewer_ids = tuple(
        _require_identity(item, f"approval packet.reviewer_ids[{i}]")
        for i, item in enumerate(reviewers_raw)
    )
    if len(set(reviewer_ids)) != len(reviewer_ids):
        raise ConfigurationError("approval packet.reviewer_ids must not repeat")
    if author_id in reviewer_ids:
        raise ConfigurationError(
            "approval packet: the author cannot be their own reviewer (self-approval)"
        )
    return author_id, reviewer_ids


def _parse_packet_review_window(data: dict[str, Any]) -> tuple[datetime, datetime]:
    review_started_at = _utc_instant(
        data.get("review_started_at"), "approval packet.review_started_at"
    )
    review_completed_at = _utc_instant(
        data.get("review_completed_at"), "approval packet.review_completed_at"
    )
    if review_completed_at < review_started_at:
        raise ConfigurationError(
            "approval packet.review_completed_at must not precede review_started_at"
        )
    return review_started_at, review_completed_at


def _parse_packet_decision(data: dict[str, Any]) -> tuple[str, str]:
    decision = _string(data.get("decision"), "approval packet.decision")
    if decision not in DECISION_STATES:
        raise ConfigurationError(
            f"approval packet.decision must be in the R0 decision set {sorted(DECISION_STATES)}"
        )
    if decision in FORBIDDEN_DECISIONS:
        raise ConfigurationError("approval packet.decision is forbidden in R0")
    decision_reason = _string(data.get("decision_reason"), "approval packet.decision_reason")
    return decision, decision_reason


def _parse_packet_fingerprints(data: dict[str, Any]) -> frozenset[str]:
    """The distinct producer-key fingerprints: between seven (one per role)
    and fourteen (a revoked role may carry one revoked key plus its
    same-role successor)."""
    fingerprints = _list(
        data.get("producer_key_fingerprints"),
        "approval packet.producer_key_fingerprints",
    )
    if not (len(REQUIRED_ROLES) <= len(fingerprints) <= 2 * len(REQUIRED_ROLES)):
        raise ConfigurationError(
            "approval packet.producer_key_fingerprints must contain between "
            "seven and fourteen distinct fingerprints"
        )
    for item in fingerprints:
        _sha256(item, "approval packet.producer_key_fingerprints[]")
    if len(set(fingerprints)) != len(fingerprints):
        raise ConfigurationError("approval packet.producer_key_fingerprints must be distinct")
    return frozenset(f for f in fingerprints if isinstance(f, str))


def _parse_approval_packet(value: object) -> ApprovalPacket:
    data = _object(value, "approval packet")
    scan_forbidden_secrets(data, "approval packet")
    _keys(
        data,
        {
            "schema",
            "schema_version",
            "candidate_policy_path",
            "candidate_policy_raw_sha256",
            "candidate_schema",
            "candidate_schema_version",
            "repository",
            "git_object_format",
            "candidate_commits",
            "candidate_trees",
            "producer_key_fingerprints",
            "artifact_manifest_sha256",
            "command_templates_sha256",
            "env_allowlist_sha256",
            "gateway_policy_sha256",
            "max_evidence_age_seconds",
            "author_id",
            "reviewer_ids",
            "review_started_at",
            "review_completed_at",
            "decision",
            "decision_reason",
            "supersedes_policy_sha256",
            "rollback_policy_sha256",
        },
        "approval packet",
    )
    if _string(data.get("schema"), "approval packet.schema") != APPROVAL_PACKET_SCHEMA:
        raise ConfigurationError("approval packet must use the frozen packet schema")
    if _string(data.get("schema_version"), "approval packet.schema_version") != SCHEMA_VERSION:
        raise ConfigurationError("approval packet must use schema version 1")
    candidate_path = _relative_path(
        data.get("candidate_policy_path"), "approval packet.candidate_policy_path"
    )
    candidate_raw_sha256 = _sha256(
        data.get("candidate_policy_raw_sha256"),
        "approval packet.candidate_policy_raw_sha256",
    )
    if (
        _string(data.get("candidate_schema"), "approval packet.candidate_schema")
        != CANDIDATE_SCHEMA
    ):
        raise ConfigurationError("approval packet must reference the candidate schema")
    if (
        _string(data.get("candidate_schema_version"), "approval packet.candidate_schema_version")
        != SCHEMA_VERSION
    ):
        raise ConfigurationError("approval packet must reference candidate schema version 1")
    repository = _string(data.get("repository"), "approval packet.repository")
    if not repository.startswith(("https://github.com/", "git@github.com:")):
        raise ConfigurationError("approval packet.repository must be a GitHub remote")
    object_format = _string(data.get("git_object_format"), "approval packet.git_object_format")
    if object_format not in _GIT_OBJECT_FORMATS:
        raise ConfigurationError("approval packet.git_object_format must be 'sha1' or 'sha256'")
    commits = _list(data.get("candidate_commits"), "approval packet.candidate_commits")
    trees = _list(data.get("candidate_trees"), "approval packet.candidate_trees")
    for item in commits:
        _git_oid(item, "approval packet.candidate_commits[]", object_format)
    for item in trees:
        _git_oid(item, "approval packet.candidate_trees[]", object_format)
    fingerprints = _parse_packet_fingerprints(data)
    _sha256(data.get("artifact_manifest_sha256"), "approval packet.artifact_manifest_sha256")
    _sha256(data.get("command_templates_sha256"), "approval packet.command_templates_sha256")
    _sha256(data.get("env_allowlist_sha256"), "approval packet.env_allowlist_sha256")
    _sha256(data.get("gateway_policy_sha256"), "approval packet.gateway_policy_sha256")
    max_age = _int(data.get("max_evidence_age_seconds"), "approval packet.max_evidence_age_seconds")
    if max_age <= 0 or max_age > 365 * 86400:
        raise ConfigurationError("approval packet.max_evidence_age_seconds must be bounded")
    author_id, reviewer_ids = _parse_packet_identities(data)
    review_started_at, review_completed_at = _parse_packet_review_window(data)
    decision, decision_reason = _parse_packet_decision(data)
    supersedes = (
        _sha256(data.get("supersedes_policy_sha256"), "approval packet.supersedes_policy_sha256")
        if data.get("supersedes_policy_sha256") is not None
        else None
    )
    rollback = (
        _sha256(data.get("rollback_policy_sha256"), "approval packet.rollback_policy_sha256")
        if data.get("rollback_policy_sha256") is not None
        else None
    )
    return ApprovalPacket(
        schema=APPROVAL_PACKET_SCHEMA,
        schema_version=SCHEMA_VERSION,
        candidate_policy_path=candidate_path,
        candidate_policy_raw_sha256=candidate_raw_sha256,
        candidate_schema=CANDIDATE_SCHEMA,
        candidate_schema_version=SCHEMA_VERSION,
        repository=repository,
        git_object_format=object_format,
        candidate_commits=frozenset(c for c in commits if isinstance(c, str)),
        candidate_trees=frozenset(t for t in trees if isinstance(t, str)),
        producer_key_fingerprints=frozenset(f for f in fingerprints if isinstance(f, str)),
        artifact_manifest_sha256=_sha256(
            data.get("artifact_manifest_sha256"), "approval packet.artifact_manifest_sha256"
        ),
        command_templates_sha256=_sha256(
            data.get("command_templates_sha256"), "approval packet.command_templates_sha256"
        ),
        env_allowlist_sha256=_sha256(
            data.get("env_allowlist_sha256"), "approval packet.env_allowlist_sha256"
        ),
        gateway_policy_sha256=_sha256(
            data.get("gateway_policy_sha256"), "approval packet.gateway_policy_sha256"
        ),
        max_evidence_age_seconds=max_age,
        author_id=author_id,
        reviewer_ids=reviewer_ids,
        review_started_at=review_started_at,
        review_completed_at=review_completed_at,
        decision=decision,
        decision_reason=decision_reason,
        supersedes_policy_sha256=supersedes,
        rollback_policy_sha256=rollback,
    )


# ---------------------------------------------------------------------------
# Cross-object integrity: keys, roles, rotation, revocation, approval
# ---------------------------------------------------------------------------


def _collect_keys(
    candidate: TrustPolicyCandidate,
) -> dict[str, PublicKeyRegistration]:
    all_keys: dict[str, PublicKeyRegistration] = {}
    for producer in candidate.producers:
        for key in producer.keys:
            if key.key_id in all_keys:
                raise ConfigurationError(f"duplicate key_id across producers: {key.key_id}")
            all_keys[key.key_id] = key
    return all_keys


def _verify_two_key_revoked_role(
    producer: ProducerRoleRegistration,
    candidate: TrustPolicyCandidate,
) -> None:
    """Two keys: exactly one revoked key plus one SUCCESSOR bound in all
    three places (record names the second key, the second key's
    ``replaces_key_id`` points back, the revoked key's rotation entry names
    the successor)."""
    revoked_keys = [key for key in producer.keys if key.lifecycle_state == "revoked"]
    if len(revoked_keys) != 1:
        raise ConfigurationError("a role carrying two keys must contain exactly one revoked key")
    revoked_key = revoked_keys[0]
    successor_key = next(key for key in producer.keys if key.lifecycle_state != "revoked")
    record = next(
        (record for record in candidate.revocation_records if record.key_id == revoked_key.key_id),
        None,
    )
    if record is None or record.superseded_by_key_id is None:
        raise ConfigurationError(
            "a two-key revoked role must declare the successor in the revocation record"
        )
    if successor_key.replaces_key_id != revoked_key.key_id:
        raise ConfigurationError(
            "the successor key of a two-key revoked role must point back at the revoked key"
        )
    entry = next(
        (entry for entry in candidate.rotation_plan.entries if entry.key_id == revoked_key.key_id),
        None,
    )
    if entry is None or entry.replaces_key_id != successor_key.key_id:
        raise ConfigurationError(
            "the revoked key of a two-key role must have a rotation entry naming the successor"
        )


def _verify_one_key_revoked_role(
    revoked_key: PublicKeyRegistration,
    candidate: TrustPolicyCandidate,
    keys: dict[str, PublicKeyRegistration],
) -> None:
    """One revoked key: a historical revoked key WITHOUT a successor -- its
    record must declare no successor, and no successor registration or
    replacement plan may point at it."""
    record = next(
        (record for record in candidate.revocation_records if record.key_id == revoked_key.key_id),
        None,
    )
    if record is not None and record.superseded_by_key_id is not None:
        raise ConfigurationError("a one-key revoked role must not declare a successor")
    for other in keys.values():
        if other.replaces_key_id == revoked_key.key_id:
            raise ConfigurationError(
                "no successor registration may point at a one-key revoked role"
            )
    if any(
        entry.key_id == revoked_key.key_id and entry.replaces_key_id is not None
        for entry in candidate.rotation_plan.entries
    ):
        raise ConfigurationError("no replacement plan may target a one-key revoked role")


def _verify_revoked_role_key_counts(
    candidate: TrustPolicyCandidate,
    keys: dict[str, PublicKeyRegistration],
) -> None:
    """Close the revoked-role key structure.

    ONE key (revoked): a historical revoked key WITHOUT a successor (see
    ``_verify_one_key_revoked_role``).  TWO keys: exactly one revoked key
    plus one SUCCESSOR bound in all three places (see
    ``_verify_two_key_revoked_role``).  A second key that is not a bound
    successor is rejected."""
    for producer in candidate.producers:
        if len(producer.keys) > 2:
            raise ConfigurationError(
                "a revoked role may carry at most one revoked key plus one successor key"
            )
        revoked_keys = [key for key in producer.keys if key.lifecycle_state == "revoked"]
        if len(producer.keys) == 2:
            _verify_two_key_revoked_role(producer, candidate)
        elif revoked_keys:
            _verify_one_key_revoked_role(revoked_keys[0], candidate, keys)


def _verify_key_uniqueness(candidate: TrustPolicyCandidate) -> frozenset[str]:
    """Seven roles, distinct non-zero Ed25519 public keys; the sealer must
    differ from every producer.  A revoked candidate may carry, in addition
    to the seven role keys, ONE same-role successor key for a revoked key
    (at most two keys per role, exactly one of them revoked)."""
    public_keys: dict[str, str] = {}
    fingerprints: set[str] = set()
    for producer in candidate.producers:
        if producer.role not in REQUIRED_ROLES:
            raise ConfigurationError("unknown producer role")
        for key in producer.keys:
            if key.role != producer.role:
                raise ConfigurationError("key role must match its producer")
            if key.public_key in public_keys and public_keys[key.public_key] != key.key_id:
                raise ConfigurationError("duplicate public key across registrations")
            public_keys[key.public_key] = key.key_id
            fingerprints.add(key.fingerprint_sha256)
    if candidate.lifecycle_state == "revoked":
        _verify_revoked_role_key_counts(candidate, _collect_keys(candidate))
    elif len(public_keys) != len(REQUIRED_ROLES):
        raise ConfigurationError("the candidate must register exactly seven distinct keys")
    sealer_key = next(
        key for producer in candidate.producers if producer.role == _SEALER for key in producer.keys
    )
    for producer in candidate.producers:
        if producer.role == _SEALER:
            continue
        for key in producer.keys:
            if key.public_key == sealer_key.public_key:
                raise ConfigurationError("the sealer must not share a public key with a producer")
    return frozenset(fingerprints)


def _verify_source_seal(candidate: TrustPolicyCandidate) -> None:
    if candidate.source_seal.candidate_only is not True:
        raise ConfigurationError("candidate source seal must be candidate-only")
    if candidate.source_seal.production_approved is not False:
        raise ConfigurationError("candidate source seal must not claim production approval")
    if not candidate.source_seal.approved_commits:
        raise ConfigurationError("candidate source seal must pin at least one commit")
    if not candidate.source_seal.approved_trees:
        raise ConfigurationError("candidate source seal must pin at least one tree")


def _verify_approver_separation(candidate: TrustPolicyCandidate, packet: ApprovalPacket) -> None:
    """The packet author must equal the candidate author; reviewers must be
    distinct logical identities, disjoint from the candidate author and from
    every producer owner AND backup owner (a producer -- or its backup -- can
    never approve its own policy)."""
    if packet.author_id != candidate.author_id:
        raise ConfigurationError("approval packet author must equal the candidate author")
    producer_owners = (
        {producer.owner_id for producer in candidate.producers}
        | {producer.backup_owner_id for producer in candidate.producers if producer.backup_owner_id}
        | {
            key.owner_id
            for producer in candidate.producers
            for key in producer.keys
            if key.owner_id
        }
        | {
            key.backup_owner_id
            for producer in candidate.producers
            for key in producer.keys
            if key.backup_owner_id
        }
    )
    for reviewer in packet.reviewer_ids:
        if reviewer == candidate.author_id:
            raise ConfigurationError("the candidate author cannot be a reviewer")
        if reviewer in producer_owners:
            raise ConfigurationError(
                "a producer or backup owner cannot be an approver of their own candidate policy"
            )


def _detect_replacement_cycles(replaced_by: dict[str, str]) -> None:
    """Fail closed when the replacement links form a rotation cycle."""
    for key_id in replaced_by:
        cursor = key_id
        seen: set[str] = set()
        while cursor in replaced_by:
            if cursor in seen:
                raise ConfigurationError("rotation cycle detected")
            seen.add(cursor)
            cursor = replaced_by[cursor]


def _require_planned_at_in_window(
    entry: RotationEntry,
    key: PublicKeyRegistration,
    candidate: TrustPolicyCandidate,
) -> None:
    """``planned_at`` must sit inside the key's validity window:
    ``max(candidate.created_at, key.created_at, key.candidate_from) <=
    planned_at < planned_expiry`` when ``planned_expiry`` is set (inclusive
    lower bound, EXCLUSIVE upper bound).

    For a REVOKED key the current-state transition (``from_state ==
    revoked``) must additionally happen no earlier than the revocation
    event: ``planned_at >= matching RevocationRecord.revoked_at``
    (inclusive)."""
    if entry.planned_at < candidate.created_at:
        raise ConfigurationError(
            "rotation planned_at must not precede the candidate creation timestamp"
        )
    if entry.planned_at < key.created_at:
        raise ConfigurationError(
            "rotation planned_at must not precede the key created_at timestamp"
        )
    if entry.planned_at < key.candidate_from:
        raise ConfigurationError(
            "rotation planned_at must not precede the key candidate_from timestamp"
        )
    if key.planned_expiry is not None and entry.planned_at >= key.planned_expiry:
        raise ConfigurationError(
            "rotation planned_at must fall strictly before the key planned_expiry "
            "window (planned_at < planned_expiry)"
        )
    if key.lifecycle_state == "revoked":
        record = next(
            (record for record in candidate.revocation_records if record.key_id == key.key_id),
            None,
        )
        if record is not None and entry.planned_at < record.revoked_at:
            raise ConfigurationError(
                "rotation planned_at must not precede the revocation record revoked_at "
                "for a revoked key"
            )


def _verify_plan_replacement_reference(
    entry: RotationEntry,
    keys: dict[str, PublicKeyRegistration],
    key_ids: frozenset[str],
) -> str:
    """A plan-level replacement must reference a real, same-role, distinct
    key (distinct key id AND public key); returns the replacement key id."""
    replacer = entry.replaces_key_id
    assert replacer is not None
    if replacer not in key_ids:
        raise ConfigurationError(f"rotation plan references an unknown replacement key: {replacer}")
    if replacer == entry.key_id:
        raise ConfigurationError("a key cannot replace itself")
    if keys[replacer].role != keys[entry.key_id].role:
        raise ConfigurationError("replacement must stay within the same role")
    if keys[replacer].public_key == keys[entry.key_id].public_key:
        raise ConfigurationError("a replacement key must not share the old key's public key")
    return replacer


def _verify_rotation_entries(
    candidate: TrustPolicyCandidate,
    keys: dict[str, PublicKeyRegistration],
    key_ids: frozenset[str],
) -> tuple[dict[str, str], set[str]]:
    """Closed state-machine checks for the rotation plan.

    FROZEN SEMANTICS: every entry describes a DIRECT transition of the key's
    CURRENT lifecycle state -- ``entry.from_state`` must equal the referenced
    key's ``lifecycle_state`` exactly (R0 never fabricates a future chain
    starting from a state the key is not in).  Each ``key_id`` appears at
    most once (identical, partial or conflicting duplicates all fail), and
    ``planned_at`` must sit inside the key's validity window (see
    ``_require_planned_at_in_window``).  Returns ``(replaced_by, rotating)``
    maps."""
    replaced_by: dict[str, str] = {}
    rotating: set[str] = set()
    seen_keys: set[str] = set()
    for entry in candidate.rotation_plan.entries:
        if entry.key_id not in key_ids:
            raise ConfigurationError(f"rotation plan references an unknown key: {entry.key_id}")
        if entry.key_id in seen_keys:
            raise ConfigurationError(
                f"rotation plan must contain at most one entry per key: {entry.key_id}"
            )
        seen_keys.add(entry.key_id)
        key = keys[entry.key_id]
        if entry.role != key.role:
            raise ConfigurationError("rotation plan entry role must match the key role")
        if entry.from_state != key.lifecycle_state:
            raise ConfigurationError(
                "rotation entry from_state must equal the key lifecycle state "
                f"({entry.from_state} != {key.lifecycle_state})"
            )
        _require_planned_at_in_window(entry, key, candidate)
        if entry.replaces_key_id is not None:
            replaced_by[entry.key_id] = _verify_plan_replacement_reference(entry, keys, key_ids)
        if entry.to_state == "active":
            raise ConfigurationError(
                "the R0 candidate validator cannot construct an active production key"
            )
        if entry.from_state == "active" or entry.to_state == "rotating":
            rotating.add(entry.key_id)
    return replaced_by, rotating


def _verify_key_level_replacements(
    keys: dict[str, PublicKeyRegistration],
    key_ids: frozenset[str],
    entries_by_key: dict[str, RotationEntry],
) -> None:
    """The SUCCESSOR side: ``Y.replaces_key_id == X`` must reference a real,
    same-role, distinct key and the rotation entry of ``X`` must name ``Y``
    back."""
    for key_id, key in keys.items():
        replaced = key.replaces_key_id
        if replaced is None:
            continue
        if replaced not in key_ids:
            raise ConfigurationError(f"key-level replacement references an unknown key: {replaced}")
        old = keys[replaced]
        if old.role != key.role:
            raise ConfigurationError("key-level replacement must stay within the same role")
        if old.public_key == key.public_key:
            raise ConfigurationError("a replacement key must not share the old key's public key")
        plan_entry = entries_by_key.get(replaced)
        if plan_entry is None or plan_entry.replaces_key_id != key_id:
            raise ConfigurationError("key-level replacement must match the rotation plan entry")


def _verify_plan_level_replacements(
    keys: dict[str, PublicKeyRegistration],
    key_ids: frozenset[str],
    entries_by_key: dict[str, RotationEntry],
) -> None:
    """The REPLACED side: ``entry(X).replaces_key_id == Y`` must reference a
    real, same-role, distinct key whose own registration points back at
    ``X``."""
    for replaced, plan_entry in entries_by_key.items():
        replacer = plan_entry.replaces_key_id
        if replacer is None:
            continue
        if replacer not in key_ids:
            raise ConfigurationError(
                f"rotation plan references an unknown replacement key: {replacer}"
            )
        replacer_key = keys[replacer]
        if replacer_key.role != keys[replaced].role:
            raise ConfigurationError("rotation plan replacement must stay within the same role")
        if replacer_key.public_key == keys[replaced].public_key:
            raise ConfigurationError("a replacement key must not share the old key's public key")
        if replacer_key.replaces_key_id != replaced:
            raise ConfigurationError(
                "rotation plan replacement must match the key-level registration"
            )


def _verify_record_successors(
    candidate: TrustPolicyCandidate,
    keys: dict[str, PublicKeyRegistration],
    key_ids: frozenset[str],
    entries_by_key: dict[str, RotationEntry],
) -> None:
    """The REVOKED side: ``record(X).superseded_by_key_id == Y`` must be a
    real, same-role, non-self, non-revoked/archived successor with a
    different public key, whose registration and rotation entry both name
    ``X`` back."""
    for record in candidate.revocation_records:
        successor = record.superseded_by_key_id
        if successor is None:
            continue
        if successor not in key_ids:
            raise ConfigurationError(f"superseded_by_key_id references an unknown key: {successor}")
        revoked_key = keys[record.key_id]
        successor_key = keys[successor]
        if successor_key.key_id == revoked_key.key_id:
            raise ConfigurationError("a revoked key cannot be its own successor")
        if successor_key.lifecycle_state in ("revoked", "archived"):
            raise ConfigurationError(
                "a successor must not be revoked or archived "
                f"(got {successor_key.lifecycle_state})"
            )
        if successor_key.role != revoked_key.role:
            raise ConfigurationError("successor must stay within the same role")
        if successor_key.public_key == revoked_key.public_key:
            raise ConfigurationError("a successor must not share the revoked key's public key")
        _require_successor_valid_at_event(successor_key, record)
        if successor_key.replaces_key_id != record.key_id:
            raise ConfigurationError(
                "record superseded_by_key_id must match the successor key's " "replaces_key_id"
            )
        plan_entry = entries_by_key.get(record.key_id)
        if plan_entry is None or plan_entry.replaces_key_id != successor:
            raise ConfigurationError(
                "record superseded_by_key_id must match the rotation plan entry"
            )


def _require_successor_valid_at_event(
    successor_key: PublicKeyRegistration,
    record: RevocationRecord,
) -> None:
    """The successor takes over AT the revocation event (``revoked_at``): it
    must already be in the ``candidate`` lifecycle state with
    ``created_at <= candidate_from <= revoked_at`` and, when it has a
    planned expiry, ``planned_expiry > revoked_at`` (strict)."""
    if successor_key.lifecycle_state != "candidate":
        raise ConfigurationError(
            "a successor must be in the candidate lifecycle state at the "
            f"revocation event (got {successor_key.lifecycle_state})"
        )
    if successor_key.created_at > record.revoked_at:
        raise ConfigurationError(
            "a successor must already exist (created_at) at the revocation event"
        )
    if successor_key.candidate_from > record.revoked_at:
        raise ConfigurationError(
            "a successor candidate_from must not be after the revocation event"
        )
    if (
        successor_key.planned_expiry is not None
        and successor_key.planned_expiry <= record.revoked_at
    ):
        raise ConfigurationError(
            "a successor planned_expiry must be strictly after the revocation event"
        )


def _verify_replacement_bindings(
    candidate: TrustPolicyCandidate,
    keys: dict[str, PublicKeyRegistration],
    key_ids: frozenset[str],
) -> None:
    """Close the replacement semantics across every declaration site.

    A replacement fact can be declared in THREE places and, whenever more
    than one is present, they must agree exactly:

    * ``RevocationRecord.superseded_by_key_id`` -- the revoked key is
      succeeded by ``Y``;
    * ``PublicKeyRegistration.replaces_key_id`` on the SUCCESSOR key --
      ``Y.replaces_key_id == X``;
    * ``RotationEntry.replaces_key_id`` on the REPLACED key --
      ``entry(X).replaces_key_id == Y``.

    Any dangling, cross-role, self-referencing, revoked, same-public-key or
    drifted declaration fails closed."""
    entries_by_key: dict[str, RotationEntry] = {}
    for plan_entry in candidate.rotation_plan.entries:
        entries_by_key[plan_entry.key_id] = plan_entry
    _verify_key_level_replacements(keys, key_ids, entries_by_key)
    _verify_plan_level_replacements(keys, key_ids, entries_by_key)
    _verify_record_successors(candidate, keys, key_ids, entries_by_key)


def _verify_revocation_records(
    candidate: TrustPolicyCandidate,
    keys: dict[str, PublicKeyRegistration],
    key_ids: frozenset[str],
) -> None:
    """Revocation records are immutable history with a closed 1:1 binding:

    * every record references a KNOWN, same-role key whose lifecycle state is
      ``revoked``, whose ``revocation_record_id`` equals the record id, and
      which holds no signing scopes;
    * every revoked key inside the candidate is referenced by EXACTLY ONE
      record (record ids and key ids are both unique, and the counts match).

    A record that points at a candidate/generated/registered key, or a
    revoked key without a matching record, fails closed."""
    revoked_keys = {key_id: key for key_id, key in keys.items() if key.lifecycle_state == "revoked"}
    record_ids = {record.revocation_record_id for record in candidate.revocation_records}
    if len(record_ids) != len(candidate.revocation_records):
        raise ConfigurationError("revocation records must have unique ids")
    referenced_key_ids: set[str] = set()
    for record in candidate.revocation_records:
        if record.key_id not in key_ids:
            raise ConfigurationError(
                f"revocation record references an unknown key: {record.key_id}"
            )
        key = keys[record.key_id]
        if record.role != key.role:
            raise ConfigurationError("revocation record role must match the key role")
        if key.lifecycle_state != "revoked":
            raise ConfigurationError(
                "a revocation record must reference a revoked key, not a "
                f"{key.lifecycle_state} key"
            )
        if record.key_id in referenced_key_ids:
            raise ConfigurationError("a revocation record cannot reference a key twice")
        referenced_key_ids.add(record.key_id)
        if key.revocation_record_id != record.revocation_record_id:
            raise ConfigurationError(
                "revocation record id must match the referenced key's " "revocation_record_id"
            )
        if key.allowed_signing_scopes:
            raise ConfigurationError("a revoked key cannot keep signing scopes")
    if len(candidate.revocation_records) != len(revoked_keys):
        raise ConfigurationError(
            "every revoked key must be referenced by exactly one revocation record"
        )


def _verify_lifecycle_binding(candidate: TrustPolicyCandidate, packet: ApprovalPacket) -> None:
    """Bind the approval-packet decision to the candidate lifecycle state.

    A packet decision that differs from the candidate lifecycle is a hard
    veto: the review must decide the SAME closed-set state the candidate
    declares, and the review window must open only after the candidate was
    created.  ``superseded`` additionally requires a complete supersession
    link (target digest, timestamp and reason) that the packet echoes, and
    ``revoked`` requires revocation records plus a rollback policy in the
    packet.  These are completeness requirements, not approvals: no state
    transition here can produce a production-approved outcome.

    Timeline rule: every lifecycle event timestamp (``superseded_at`` /
    ``revoked_at``) must fall INSIDE the review window
    (``review_started_at <= event <= review_completed_at``), which itself
    opens no earlier than the candidate's ``created_at``.  All comparisons
    happen on normalized UTC datetimes (the parser accepts only explicit UTC
    instants, ``Z`` or ``+00:00``), so mixed spellings of the SAME instant
    compare equal; the bounds are inclusive, so an event exactly equal to a
    bound instant is allowed."""
    if packet.decision != candidate.lifecycle_state:
        raise ConfigurationError(
            "approval packet decision must equal the candidate lifecycle state"
        )
    if packet.review_started_at < candidate.created_at:
        raise ConfigurationError(
            "approval packet review must not start before the candidate was created"
        )
    if candidate.lifecycle_state == "superseded":
        link = candidate.supersession
        if link is None:
            raise ConfigurationError(
                "a superseded candidate must carry a complete supersession link"
            )
        if (
            link.supersedes_policy_sha256 is None
            or link.superseded_at is None
            or link.reason is None
        ):
            raise ConfigurationError(
                "a superseded candidate must pin the superseding policy digest, "
                "timestamp and reason"
            )
        if packet.supersedes_policy_sha256 != link.supersedes_policy_sha256:
            raise ConfigurationError(
                "approval packet supersedes_policy_sha256 must match the candidate supersession"
            )
        _require_event_inside_review_window(link.superseded_at, "superseded_at", candidate, packet)
    if candidate.lifecycle_state == "revoked":
        if not candidate.revocation_records:
            raise ConfigurationError("a revoked candidate must carry revocation records")
        if packet.rollback_policy_sha256 is None:
            raise ConfigurationError(
                "a revoked candidate requires a rollback policy in the approval packet"
            )
        for record in candidate.revocation_records:
            _require_event_inside_review_window(record.revoked_at, "revoked_at", candidate, packet)


def _require_event_inside_review_window(
    event: datetime,
    event_name: str,
    candidate: TrustPolicyCandidate,
    packet: ApprovalPacket,
) -> None:
    """A lifecycle event must not precede the candidate's creation and must
    fall inside the review window (inclusive UTC-instant bounds)."""
    if event < candidate.created_at:
        raise ConfigurationError(f"{event_name} must not precede the candidate creation timestamp")
    if event < packet.review_started_at or event > packet.review_completed_at:
        raise ConfigurationError(
            f"{event_name} must fall inside the review window "
            "(review_started_at <= event <= review_completed_at)"
        )


def _verify_rotation_revocation(candidate: TrustPolicyCandidate) -> None:
    """Validate the rotation plan and revocation records as a closed state
    machine: legal transitions only, one entry per key, current-state
    from_state, planned_at inside the key validity window, no
    self-replacement, no cycles, no cross-role replacement, no
    same-public-key replacement, no signing scope for revoked keys, 1:1
    record/key binding, and exact three-way replacement consistency (record
    successor / key-level replaces / plan-level replaces)."""
    keys = _collect_keys(candidate)
    key_ids = frozenset(keys)
    replaced_by, _rotating = _verify_rotation_entries(candidate, keys, key_ids)
    # cycle detection over replaces links
    _detect_replacement_cycles(replaced_by)
    _verify_revocation_records(candidate, keys, key_ids)
    _verify_replacement_bindings(candidate, keys, key_ids)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _verify_packet_section_digests(
    candidate_payload: dict[str, Any], packet: ApprovalPacket
) -> None:
    """The approval packet's section digests must bind the candidate's actual
    canonical section bytes (artifact approvals, command templates, env
    allowlist and gateway policy), so a drifted candidate can never be paired
    with a stale packet."""
    sections = {
        "artifact_manifest_sha256": candidate_payload.get("artifact_approvals"),
        "command_templates_sha256": candidate_payload.get("commands"),
        "env_allowlist_sha256": candidate_payload.get("allowed_env_names"),
        "gateway_policy_sha256": candidate_payload.get("gateway"),
    }
    for field_name, section in sections.items():
        declared = getattr(packet, field_name)
        if hashlib.sha256(_canonical(section)).hexdigest() != declared:
            raise ConfigurationError(
                f"approval packet {field_name} does not bind the candidate section"
            )


def _validate_candidate_structure(
    candidate_payload: object,
    approval_packet_payload: object,
    repo_root: Path,
    *,
    digest_verified: bool,
) -> CandidateValidationReport:
    """Structural validation shared by both entry points.

    ``digest_verified`` is a caller-provided FACT: only the file-level entry
    proves it against the candidate raw bytes BEFORE calling this helper.
    Only when the raw digest was actually verified AND the lifecycle is
    ``candidate`` can the report carry the positive status
    ``candidate/valid_not_approved``; every other combination is an explicit
    non-candidate outcome with a blocker.  This function NEVER approves a
    policy, NEVER writes into ``joint_gate._APPROVED_TRUST_POLICY_SHA256``
    and never changes the P34.7 production decision.  Structural violations
    raise :class:`ConfigurationError` (``invalid/veto``).
    """
    candidate = _parse_candidate(candidate_payload)
    packet = _parse_approval_packet(approval_packet_payload)
    candidate_dict = _object(candidate_payload, "trust policy candidate")
    _verify_key_uniqueness(candidate)
    _verify_source_seal(candidate)
    _verify_approver_separation(candidate, packet)
    _verify_rotation_revocation(candidate)
    _verify_lifecycle_binding(candidate, packet)
    _verify_packet_section_digests(candidate_dict, packet)
    # The approval packet must reference the candidate digest AND agree with
    # the candidate's source seal, scope set and freshness bounds.
    if packet.git_object_format != candidate.source_seal.git_object_format:
        raise ConfigurationError("approval packet Git object format must match the candidate")
    if packet.repository != candidate.source_seal.repository:
        raise ConfigurationError("approval packet repository must match the candidate")
    if packet.candidate_commits != candidate.source_seal.approved_commits:
        raise ConfigurationError("approval packet candidate commits must match the source seal")
    if packet.candidate_trees != candidate.source_seal.approved_trees:
        raise ConfigurationError("approval packet candidate trees must match the source seal")
    if packet.max_evidence_age_seconds != candidate.evidence_freshness.max_evidence_age_seconds:
        raise ConfigurationError(
            "approval packet max_evidence_age_seconds must match the candidate"
        )
    expected_fingerprints = {
        key.fingerprint_sha256 for producer in candidate.producers for key in producer.keys
    }
    if packet.producer_key_fingerprints != expected_fingerprints:
        raise ConfigurationError(
            "approval packet producer key fingerprints must match the candidate keys"
        )
    migration_head = discover_migration_head(repo_root, "backend/src/omnibase/migrations/versions")
    if migration_head != MIGRATION_HEAD:
        raise ConfigurationError(f"migration head must remain {MIGRATION_HEAD}")
    migration_versions = (
        repo_root / "backend" / "src" / "omnibase" / "migrations" / "versions"
    )
    migration_0013_created = any(
        path.name.startswith("0013_") for path in migration_versions.glob("*.py")
    )
    if not migration_0013_created:
        raise ConfigurationError("migration 0013 must exist at the current repository head")
    if any(
        path.name[:4].isdigit() and int(path.name[:4]) >= 14
        for path in migration_versions.glob("[0-9][0-9][0-9][0-9]_*.py")
    ):
        raise ConfigurationError("migration 0014 or higher must not exist")
    if digest_verified and candidate.lifecycle_state == "candidate":
        status = "candidate/valid_not_approved"
        blockers: tuple[str, ...] = ()
    elif digest_verified:
        status = f"{candidate.lifecycle_state}/not_approved"
        blockers = ("lifecycle_not_candidate",)
    else:
        status = "candidate/structural_valid"
        blockers = ("candidate_digest_unverified",)
    return CandidateValidationReport(
        contract_valid=True,
        candidate_digest_verified=digest_verified,
        role_set_verified=True,
        key_uniqueness_verified=True,
        source_seal_verified=True,
        approval_packet_verified=True,
        author_reviewer_separation_verified=True,
        producer_approver_separation_verified=True,
        forbidden_secret_fields_absent=True,
        lifecycle_valid=(packet.decision == candidate.lifecycle_state == "candidate"),
        production_approved=False,
        approved_digest_written=False,
        activation_allowed=False,
        root_env_accessed=False,
        business_database_accessed=False,
        business_database_migrated=False,
        runtime_activated=False,
        migration_head=migration_head,
        migration_0013_created=True,
        feature_gates={
            "agent_runtime_enabled": False,
            "agent_planner_enabled": False,
            "multi_agent_enabled": False,
        },
        status=status,
        blockers=blockers,
    )


def validate_trust_policy_candidate(
    candidate_payload: object,
    approval_packet_payload: object,
    repo_root: Path,
) -> CandidateValidationReport:
    """Structural-only, object-level validation (no raw file bytes).

    This entry parses and cross-checks the candidate and approval-packet
    OBJECTS against the frozen R0 contract, but it has no raw bytes to
    verify, so it NEVER claims ``candidate_digest_verified``: the report
    carries ``candidate_digest_verified=False``, status
    ``candidate/structural_valid`` and blocker ``candidate_digest_unverified``.
    The file-level entry :func:`validate_trust_policy_candidate_files` is the
    ONLY path that performs raw-byte digest verification and can construct
    the positive ``candidate/valid_not_approved`` report.
    """
    return _validate_candidate_structure(
        candidate_payload, approval_packet_payload, repo_root, digest_verified=False
    )


def _is_within(root: Path, path: Path) -> bool:
    """Containment test for already-resolved, link-free paths."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def validate_trust_policy_candidate_files(
    candidate_path: Path,
    approval_packet_path: Path,
    repo_root: Path,
) -> CandidateValidationReport:
    """File-based entry point with the same fail-closed guarantees.

    Both files must be regular, non-link, non-reparse files with canonical
    JSON bytes and must resolve INSIDE the repository root.  The approval
    packet is a SEPARATE file -- a candidate can never carry its own approval
    root -- and its recorded candidate policy path must equal the candidate's
    actual repository-relative POSIX path.  Only after the candidate raw
    bytes are verified against ``candidate_policy_raw_sha256`` can this entry
    construct the positive ``candidate/valid_not_approved`` report."""
    root = repo_root.resolve(strict=True)
    candidate = _load_regular_json(candidate_path, "candidate policy")
    packet = _load_regular_json(approval_packet_path, "approval packet")
    if candidate == packet:
        raise ConfigurationError("the approval packet must be a separate file from the candidate")
    if not _is_within(root, candidate) or not _is_within(root, packet):
        raise ConfigurationError(
            "candidate policy and approval packet must resolve inside the repository"
        )
    candidate_raw = candidate.read_bytes()
    packet_payload = _read_canonical(packet, "approval packet")
    if hashlib.sha256(candidate_raw).hexdigest() != packet_payload.get(
        "candidate_policy_raw_sha256"
    ):
        raise ConfigurationError(
            "approval packet candidate_policy_raw_sha256 does not match the candidate raw bytes"
        )
    recorded_path = packet_payload.get("candidate_policy_path")
    actual_path = candidate.relative_to(root).as_posix()
    if not isinstance(recorded_path, str) or recorded_path != actual_path:
        raise ConfigurationError(
            "approval packet candidate_policy_path does not match the candidate file location"
        )
    candidate_payload = _read_canonical(candidate, "candidate policy")
    return _validate_candidate_structure(
        candidate_payload, packet_payload, root, digest_verified=True
    )


def _load_regular_json(path: Path, name: str) -> Path:
    unresolved = path if path.is_absolute() else Path.cwd() / path
    if ".." in unresolved.parts:
        raise ConfigurationError(f"{name} path must not contain parent traversal")
    # lstat every component of the UNRESOLVED path: resolution would follow a
    # symlink, so links/reparse points must be detected before resolving.
    check = Path(unresolved.anchor)
    for part in Path(unresolved).parts[1:]:
        check = check / part
        try:
            metadata = os.lstat(check)
        except OSError as exc:
            raise ConfigurationError(f"{name} contains an unavailable component") from exc
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise ConfigurationError(f"{name} contains a link or reparse point")
    try:
        candidate = unresolved.resolve(strict=True)
    except OSError as exc:
        raise ConfigurationError(f"{name} is unavailable") from exc
    metadata = os.lstat(candidate)
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError(f"{name} must be a regular non-link file")
    return candidate


def _read_canonical(path: Path, name: str) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"{name} must be valid JSON") from exc
    if not isinstance(parsed, dict) or any(not isinstance(k, str) for k in parsed):
        raise ConfigurationError(f"{name} must be a JSON object")
    if json.dumps(parsed, separators=(",", ":"), sort_keys=True).encode("utf-8") != raw:
        raise ConfigurationError(f"{name} must be canonical JSON bytes")
    return parsed


__all__ = [
    "APPROVAL_PACKET_SCHEMA",
    "CANDIDATE_SCHEMA",
    "DECISION_STATES",
    "FORBIDDEN_DECISIONS",
    "KEY_LIFECYCLE_STATES",
    "LEGAL_TRANSITIONS",
    "MIGRATION_HEAD",
    "ROLE_SIGNING_SCOPES",
    "CandidateValidationReport",
    "scan_forbidden_secrets",
    "validate_trust_policy_candidate",
    "validate_trust_policy_candidate_files",
]
