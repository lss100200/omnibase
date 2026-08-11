"""Fail-closed, trust-anchored P34.7 hardened joint evidence contract.

This module implements the *evidence authenticity* boundary for the P34.7
production joint gate.  It never derives a production ``passed`` result from
operator-authored inline assertions, from hash-consistent sidecars, or from a
public key shipped inside the evidence bundle.  Every proof must be a real,
regular, non-link file whose raw bytes are canonical JSON, are cross-bound by
sidecar manifests, and are covered by a detached Ed25519 signature that
verifies against a producer public key taken from an **externally configured
trust policy** (never from the bundle itself).

The trust policy is the only trust anchor.  It is a JSON file installed
outside the evidence run directory by the gate operator, and its raw bytes
must hash to a digest pinned in this module
(:data:`_APPROVED_TRUST_POLICY_SHA256`).  No trust policy has been
independently approved yet, so the set is empty and **every** bundle,
including a fully self-signed one, currently remains ``blocked/not_proven``.

Two operating modes are exposed and must never be blurred:

* :func:`validate_joint_evidence_contract` parses the static schema only and
  always returns ``blocked/not_proven``.
* :func:`verify_joint_evidence` may return ``passed`` only when a policy is
  approved, every mandatory evidence file exists under ``run_dir``, every
  detached signature verifies against the policy, every canonical component
  schema parses and cross-binds run id / producer / source and artifact
  identity / command receipts / peer identities / measurements / results, and
  every safety item is proven.  Unsigned or unverifiable evidence, an
  unapproved policy, an unmeasured posture or any other ``not_proven`` safety
  item becomes a blocker; a pass requires zero blockers.

The verifier is offline: it never starts a service, opens a network
connection, reads the root ``.env``, accesses a database, executes code or
activates the production Runtime.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from omnibase.production.composition import ConfigurationError

_SCHEMA = "omnibase.p34-7.hardened-joint-evidence.v2"
_SCHEMA_VERSIONS = frozenset({"2"})
_HEX = set("0123456789abcdef")
_GIT_OBJECT_FORMATS = frozenset({"sha1", "sha256"})
_REQUIRED_COMMANDS = (
    "core_runner",
    "runner_broker",
    "runner_gateway",
    "broker_gateway",
    "overlay_data_plane",
    "recovery_sla",
)
_COMMAND_PRODUCER = {
    "core_runner": "core",
    "runner_broker": "runner",
    "runner_gateway": "runner",
    "broker_gateway": "broker",
    "overlay_data_plane": "overlay",
    "recovery_sla": "recovery_sla",
}
_REQUIRED_COMPONENTS = (
    "core",
    "runner",
    "broker",
    "gateway",
    "overlay",
    "recovery_sla",
)
# Fixed P34.7 topology: Core<->Runner, Runner<->Broker, Runner<->Gateway,
# Broker<->Gateway; the Overlay data plane is published through the Runner and
# recovery/SLA evidence is bound to Core.
_REQUIRED_PEERS: dict[str, tuple[str, ...]] = {
    "core": ("runner",),
    "runner": ("core", "broker", "gateway"),
    "broker": ("runner", "gateway"),
    "gateway": ("runner", "broker"),
    "overlay": ("runner",),
    "recovery_sla": ("core",),
}
_REQUIRED_FEATURE_GATES = (
    "agent_runtime_enabled",
    "agent_planner_enabled",
    "multi_agent_enabled",
)
_REQUIRED_ATTACKS = (
    "node_compromise",
    "credential_theft",
    "revocation_replay",
    "derp_failover",
    "cross_component_replay",
)
_ALLOWED_ATTACK_OUTCOMES = frozenset({"rejected", "contained", "failed_attack"})
_REQUIRED_CLEANUP_KEYS = (
    "containers",
    "networks",
    "processes",
    "volumes",
    "databases",
    "test_identities",
)
_FORBIDDEN_ENV_NAMES = frozenset(
    {".env", "JWT_SECRET", "LLM_API_KEY", "POSTGRES_PASSWORD", "MINIO_ROOT_PASSWORD"}
)
_ROOT_ENV_NAMES = frozenset({".env", "./.env"})

COMPONENT_SCHEMA = "omnibase.p34-7.component-evidence.v1"
RECEIPT_SCHEMA = "omnibase.p34-7.command-receipt.v1"
POSTURE_SCHEMA = "omnibase.p34-7.posture-measurement.v1"
ATTACK_SCHEMA = "omnibase.p34-7.attack-matrix.v1"
CLEANUP_SCHEMA = "omnibase.p34-7.cleanup-inventory.v1"
SEAL_SCHEMA = "omnibase.p34-7.evidence-seal.v1"
TRUST_POLICY_SCHEMA = "omnibase.p34-7.trust-policy.v1"

# ---------------------------------------------------------------------------
# Trust anchor
# ---------------------------------------------------------------------------

# No trust policy has been independently approved yet.  Adding a digest here is
# an audited, reviewed change that establishes the production trust anchor (the
# same way a CA root is pinned); until then every bundle remains
# blocked/not_proven because the bundle producer can always self-author its own
# evidence and keys.
_APPROVED_TRUST_POLICY_SHA256: frozenset[str] = frozenset()


# ---------------------------------------------------------------------------
# Low-level strict parsers (kept small to honour the repository C901 <= 12 rule)
# ---------------------------------------------------------------------------


def _object(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ConfigurationError(f"{name} must be an object")
    return value


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{name} must be an array")
    return value


def _string(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{name} must be a non-empty string")
    return value


def _opt_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _string(value, name)


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} must be a boolean")
    return value


def _int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigurationError(f"{name} must be an integer")
    return value


def _keys(value: dict[str, Any], allowed: set[str], name: str) -> None:
    extra = sorted(set(value) - allowed)
    if extra:
        raise ConfigurationError(f"{name} has unexpected fields: {', '.join(extra)}")


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in _HEX for c in value):
        raise ConfigurationError(f"{name} must be a lowercase SHA-256")
    return value


def _git_oid(value: object, name: str, object_format: str) -> str:
    """Strictly parse a Git object identifier in the declared object format.

    ``sha1`` accepts exactly 40 lowercase hex characters, ``sha256`` exactly
    64.  The OID is preserved as the original Git identifier -- it is never
    re-hashed or transformed -- and any unknown format, wrong length, non-hex
    or uppercase character fails closed.  The declared format must itself be a
    member of the closed set :data:`_GIT_OBJECT_FORMATS`."""
    if object_format not in _GIT_OBJECT_FORMATS:
        raise ConfigurationError(f"{name} has an unknown git object format")
    expected = 40 if object_format == "sha1" else 64
    text = _string(value, name)
    if len(text) != expected or any(c not in _HEX for c in text):
        raise ConfigurationError(
            f"{name} must be a {expected}-hex lowercase {object_format.upper()} OID"
        )
    return text


def _utc_now() -> datetime:
    """The single, testable UTC clock seam.  Every ``now`` comparison inside a
    verification reads the same instant passed down from the entry point; no
    verification may consult the wall clock more than once."""
    return datetime.now(timezone.utc)  # noqa: UP017


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _relative_path(value: object, name: str) -> str:
    text = _string(value, name=name).replace("\\", "/")
    path = PurePosixPath(text)
    if path.is_absolute() or ":" in path.parts[0]:
        raise ConfigurationError(f"{name} must be a normalized relative path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise ConfigurationError(f"{name} must not contain traversal segments")
    if text.lower() in _ROOT_ENV_NAMES or path.name.lower() == ".env":
        raise ConfigurationError(f"{name} must not reference the root .env")
    if any(part.endswith(".env") for part in path.parts):
        raise ConfigurationError(f"{name} must not reference an env file")
    return path.as_posix()


def _real_file(root: Path, relative: str, name: str) -> Path:
    """Resolve ``relative`` under ``root`` refusing links, reparse points and
    escape; every path component is individually lstat-checked, not only the
    final file, so Windows junctions/reparse points anywhere in the chain are
    rejected."""
    candidate = root
    for part in PurePosixPath(relative).parts:
        candidate = candidate / part
        try:
            metadata = os.lstat(candidate)
        except OSError as exc:
            raise ConfigurationError(f"{name} is unavailable") from exc
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise ConfigurationError(f"{name} contains a link or reparse point")
    try:
        resolved = candidate.resolve(strict=True)
        rel = resolved.relative_to(root.resolve(strict=True)).as_posix()
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"{name} escaped the run directory") from exc
    if rel.lower() in _ROOT_ENV_NAMES:
        raise ConfigurationError(f"{name} resolved to the root .env")
    if rel != relative:
        raise ConfigurationError(f"{name} must be canonical within the run directory")
    metadata = os.lstat(resolved)
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError(f"{name} must be a regular file")
    return resolved


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _utc_instant(value: object, name: str) -> datetime:
    """Parse an ISO-8601 timestamp as a real UTC instant (Z or +00:00 only);
    comparisons happen on parsed instants, never on raw strings."""
    text = _string(value, name)
    if not (text.endswith("Z") or text.endswith("+00:00")):
        raise ConfigurationError(f"{name} must be an explicit UTC instant (Z or +00:00)")
    body = text[:-1] if text.endswith("Z") else text[:-6]
    try:
        parsed = datetime.fromisoformat(body)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an ISO-8601 UTC instant") from exc
    if (
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed.utcoffset() != timezone.utc  # noqa: UP017
    ):
        raise ConfigurationError(f"{name} must be UTC (non-zero offsets are ambiguous)")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)  # noqa: UP017
    return parsed


def _read_canonical_json(path: Path, name: str) -> tuple[dict[str, Any], bytes]:
    """Read a JSON file and require its raw bytes to BE canonical JSON so a
    detached signature over the raw bytes covers the exact parsed content."""
    raw = path.read_bytes()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"{name} must be valid JSON") from exc
    if not isinstance(parsed, dict) or any(not isinstance(k, str) for k in parsed):
        raise ConfigurationError(f"{name} must be a JSON object")
    if _canonical(parsed) != raw:
        raise ConfigurationError(f"{name} must be canonical JSON bytes")
    return parsed, raw


@dataclass(frozen=True, slots=True)
class _RunWindow:
    """The frozen evidence validity window of one run.

    ``started_at <= completed_at <= issued_at < valid_until`` is enforced
    structurally; every command receipt and every posture/attack/cleanup
    timestamp must lie inside ``[started_at, completed_at]``; ``now`` must lie
    inside ``[issued_at, valid_until)`` and its age must not exceed the policy
    maximum.  The fields enter the evidence-seal canonical binding so any outer
    rewrite without re-signing fails."""

    started_at: datetime
    completed_at: datetime
    issued_at: datetime
    valid_until: datetime


def _file_ref(root: Path, value: object, name: str) -> Path:
    ref = _object(value, name)
    _keys(ref, {"path", "size", "sha256"}, name)
    relative = _relative_path(ref.get("path"), f"{name}.path")
    size = _int(ref.get("size"), f"{name}.size")
    if size < 0:
        raise ConfigurationError(f"{name}.size is invalid")
    digest = _sha256(ref.get("sha256"), f"{name}.sha256")
    path = _real_file(root, relative, name)
    if path.stat().st_size != size:
        raise ConfigurationError(f"{name} size drifted")
    if _hash_file(path) != digest:
        raise ConfigurationError(f"{name} raw hash drifted")
    return path


def _verify_ed25519(public_key_hex: str, raw: bytes, signature: bytes) -> bool:
    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex))
        key.verify(signature, raw)
        return True
    except (InvalidSignature, ValueError):
        return False


def _signed_file(
    root: Path,
    ref: dict[str, Any],
    sig_ref: object,
    public_key_hex: str,
    name: str,
) -> tuple[dict[str, Any], str]:
    """Read one canonical JSON evidence file and verify its detached Ed25519
    signature over the raw bytes.  ``sig_ref`` may be ``None`` (unsigned).
    Returns ``(parsed, status)`` with ``status`` in
    ``verified``/``absent``/``invalid``; structural problems still veto."""
    path = _file_ref(root, ref, name)
    parsed, raw = _read_canonical_json(path, name)
    if sig_ref is None:
        return parsed, "absent"
    sig_path = _file_ref(root, sig_ref, f"{name}.signature")
    ok = _verify_ed25519(public_key_hex, raw, sig_path.read_bytes())
    return parsed, "verified" if ok else "invalid"


# ---------------------------------------------------------------------------
# Trust policy
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustPolicy:
    """Externally installed trust anchor: allowlisted producer keys, pinned
    source seal (including the Git object format), approved artifact manifest,
    exact command templates, the bounded evidence freshness window and the
    gateway certificate posture pins."""

    producers: dict[str, str]
    repository: str
    git_object_format: str
    approved_commits: frozenset[str]
    approved_trees: frozenset[str]
    executables: dict[str, tuple[str, frozenset[str]]]
    command_argv: dict[str, tuple[str, ...]]
    allowed_env_names: frozenset[str]
    gateway_issuer: str
    gateway_san_suffix: str
    gateway_validity_seconds: int
    max_evidence_age_seconds: int
    migration_head: str
    schema_version: str

    def producer_key(self, role: str) -> str:
        return self.producers[role]


def _parse_trust_policy(value: object) -> TrustPolicy:
    data = _object(value, "trust policy")
    _keys(
        data,
        {
            "schema",
            "schema_version",
            "producers",
            "source_seal",
            "executables",
            "commands",
            "allowed_env_names",
            "gateway",
            "max_evidence_age_seconds",
            "migration_head",
        },
        "trust policy",
    )
    if _string(data.get("schema"), "trust policy.schema") != TRUST_POLICY_SCHEMA:
        raise ConfigurationError("trust policy must use the frozen trust-policy schema")
    if _string(data.get("schema_version"), "trust policy.schema_version") not in _SCHEMA_VERSIONS:
        raise ConfigurationError("trust policy must target the hardened joint schema v2")
    producers = _parse_policy_producers(data)
    repository, object_format, commits, trees = _parse_policy_source_seal(data)
    executables = _parse_policy_executables(data)
    command_argv = _parse_policy_commands(data)
    env_names = _parse_policy_env_names(data)
    issuer, san_suffix, validity_seconds = _parse_policy_gateway(data)
    max_age = _int(data.get("max_evidence_age_seconds"), "trust policy.max_evidence_age_seconds")
    if max_age <= 0 or max_age > 365 * 86400:
        raise ConfigurationError(
            "trust policy.max_evidence_age_seconds must be a bounded positive window"
        )
    migration_head = _string(data.get("migration_head"), "trust policy.migration_head")
    if migration_head != "0013":
        raise ConfigurationError("trust policy.migration_head must remain 0013")
    return TrustPolicy(
        producers=producers,
        repository=repository,
        git_object_format=object_format,
        approved_commits=commits,
        approved_trees=trees,
        executables=executables,
        command_argv=command_argv,
        allowed_env_names=frozenset(env_names),
        gateway_issuer=issuer,
        gateway_san_suffix=san_suffix,
        gateway_validity_seconds=validity_seconds,
        max_evidence_age_seconds=max_age,
        migration_head=migration_head,
        schema_version="2",
    )


def _parse_policy_producers(data: dict[str, Any]) -> dict[str, str]:
    producers_raw = _object(data.get("producers"), "trust policy.producers")
    required_roles = frozenset((*_REQUIRED_COMPONENTS, "sealer"))
    if set(producers_raw) != required_roles:
        raise ConfigurationError(
            "trust policy.producers must contain exactly the six components plus sealer"
        )
    producers: dict[str, str] = {}
    for role in sorted(required_roles):
        entry = _object(producers_raw.get(role), f"trust policy.producers.{role}")
        _keys(entry, {"ed25519_public_key"}, f"trust policy.producers.{role}")
        key = _string(
            entry.get("ed25519_public_key"),
            f"trust policy.producers.{role}.ed25519_public_key",
        )
        if len(key) != 64 or any(c not in _HEX for c in key):
            raise ConfigurationError(
                f"trust policy.producers.{role}.ed25519_public_key must be a 64-hex key"
            )
        producers[role] = key
    if len(set(producers.values())) != len(producers):
        raise ConfigurationError(
            "trust policy.producers public keys must all be unique; "
            "duplicate producer keys fail closed (the sealer must differ from every producer)"
        )
    return producers


def _parse_policy_source_seal(
    data: dict[str, Any],
) -> tuple[str, str, frozenset[str], frozenset[str]]:
    source = _object(data.get("source_seal"), "trust policy.source_seal")
    _keys(
        source,
        {"repository", "git_object_format", "approved_commits", "approved_trees"},
        "trust policy.source_seal",
    )
    repository = _string(source.get("repository"), "trust policy.source_seal.repository")
    if not repository.startswith(("https://github.com/", "git@github.com:")):
        raise ConfigurationError("trust policy.source_seal.repository must be a GitHub remote")
    object_format = _string(
        source.get("git_object_format"), "trust policy.source_seal.git_object_format"
    )
    if object_format not in _GIT_OBJECT_FORMATS:
        raise ConfigurationError(
            "trust policy.source_seal.git_object_format must be 'sha1' or 'sha256'"
        )
    commits = _list(source.get("approved_commits"), "trust policy.source_seal.approved_commits")
    trees = _list(source.get("approved_trees"), "trust policy.source_seal.approved_trees")
    for item in commits:
        _git_oid(item, "trust policy.source_seal.approved_commits[]", object_format)
    for item in trees:
        _git_oid(item, "trust policy.source_seal.approved_trees[]", object_format)
    return (
        repository,
        object_format,
        frozenset(c for c in commits if isinstance(c, str)),
        frozenset(t for t in trees if isinstance(t, str)),
    )


def _parse_policy_executables(data: dict[str, Any]) -> dict[str, tuple[str, frozenset[str]]]:
    executables_raw = _object(data.get("executables"), "trust policy.executables")
    executables: dict[str, tuple[str, frozenset[str]]] = {}
    for exe_path in sorted(executables_raw):
        relative = _relative_path(exe_path, "trust policy.executables key")
        entry = _object(executables_raw.get(exe_path), f"trust policy.executables.{exe_path}")
        _keys(entry, {"sha256", "commands"}, f"trust policy.executables.{exe_path}")
        digest = _sha256(entry.get("sha256"), f"trust policy.executables.{exe_path}.sha256")
        command_names = _list(
            entry.get("commands"), f"trust policy.executables.{exe_path}.commands"
        )
        if not command_names or not all(
            isinstance(c, str) and c in _REQUIRED_COMMANDS for c in command_names
        ):
            raise ConfigurationError(
                f"trust policy.executables.{exe_path}.commands must reference required boundaries"
            )
        executables[relative] = (digest, frozenset(c for c in command_names if isinstance(c, str)))
    return executables


def _parse_policy_commands(data: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    commands_raw = _object(data.get("commands"), "trust policy.commands")
    if set(commands_raw) != set(_REQUIRED_COMMANDS):
        raise ConfigurationError(
            "trust policy.commands must contain exactly the six required boundaries"
        )
    command_argv: dict[str, tuple[str, ...]] = {}
    for command in _REQUIRED_COMMANDS:
        argv = _list(commands_raw.get(command), f"trust policy.commands.{command}")
        if not argv or not all(isinstance(a, str) and a for a in argv):
            raise ConfigurationError(f"trust policy.commands.{command} must be an argv template")
        command_argv[command] = tuple(a for a in argv if isinstance(a, str))
    return command_argv


def _parse_policy_env_names(data: dict[str, Any]) -> list[str]:
    env_names = _list(data.get("allowed_env_names"), "trust policy.allowed_env_names")
    if not all(isinstance(n, str) and n for n in env_names):
        raise ConfigurationError("trust policy.allowed_env_names must be non-empty strings")
    return [n for n in env_names if isinstance(n, str)]


def _parse_policy_gateway(data: dict[str, Any]) -> tuple[str, str, int]:
    gateway = _object(data.get("gateway"), "trust policy.gateway")
    _keys(
        gateway,
        {"issuer", "san_suffix", "validity_seconds"},
        "trust policy.gateway",
    )
    issuer = _sha256(gateway.get("issuer"), "trust policy.gateway.issuer")
    san_suffix = _string(gateway.get("san_suffix"), "trust policy.gateway.san_suffix")
    if not san_suffix.startswith("."):
        raise ConfigurationError("trust policy.gateway.san_suffix must start with a dot")
    validity_seconds = _int(
        gateway.get("validity_seconds"), "trust policy.gateway.validity_seconds"
    )
    if validity_seconds <= 0 or validity_seconds > 200 * 365 * 86400:
        raise ConfigurationError(
            "trust policy.gateway.validity_seconds must be a positive bounded window"
        )
    return issuer, san_suffix, validity_seconds


def load_trust_policy(path: Path) -> tuple[TrustPolicy, str]:
    """Load and strictly parse the external trust policy file.

    The policy must be a regular, non-link, non-reparse file; its raw-byte
    SHA-256 is returned so the caller can check it against the pinned approved
    digests.  A policy shipped inside an evidence bundle is never a trust
    anchor: :func:`verify_joint_evidence` refuses any policy located under the
    evidence run directory.
    """
    unresolved = path if path.is_absolute() else Path.cwd() / path
    if ".." in unresolved.parts:
        raise ConfigurationError("trust policy path must not contain parent traversal")
    try:
        candidate = unresolved.resolve(strict=True)
    except OSError as exc:
        raise ConfigurationError("trust policy is unavailable") from exc
    check = Path(candidate.anchor)
    for part in candidate.parts[1:]:
        check = check / part
        try:
            metadata = os.lstat(check)
        except OSError as exc:
            raise ConfigurationError("trust policy contains an unavailable component") from exc
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise ConfigurationError("trust policy contains a link or reparse point")
    metadata = os.lstat(candidate)
    if not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError("trust policy must be a regular non-link file")
    raw = candidate.read_bytes()
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError("trust policy must be valid JSON") from exc
    policy = _parse_trust_policy(parsed)
    return policy, hashlib.sha256(raw).hexdigest()


# ---------------------------------------------------------------------------
# Report contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JointGateReport:
    """Outcome of a P34.7 joint-evidence check.

    ``status`` is one of ``passed`` (only from :func:`verify_joint_evidence`
    with an approved trust policy, fully verified signatures and every safety
    item proven), ``blocked/not_proven`` (the only correct state while the
    trust chain or any safety proof is missing) or ``invalid/veto`` (raised as
    :class:`ConfigurationError`).
    """

    status: str
    run_id: str
    schema: str
    source_manifest_sha256: str
    artifact_manifest_sha256: str
    blockers: tuple[str, ...] = ()
    vetoes: tuple[str, ...] = ()
    safety: dict[str, str] = field(default_factory=dict)
    mode: str = "verify-evidence"

    @property
    def passed(self) -> bool:
        return self.status == "passed"

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "schema_version": 2,
            "mode": self.mode,
            "status": self.status,
            "passed": self.passed,
            "run_id": self.run_id,
            "source_manifest_sha256": self.source_manifest_sha256,
            "artifact_manifest_sha256": self.artifact_manifest_sha256,
            "blockers": list(self.blockers),
            "vetoes": list(self.vetoes),
            "safety": dict(self.safety),
        }


@dataclass(frozen=True, slots=True)
class _ChainOutcome:
    """Collected authenticity results of one evidence chain check.

    Signature and posture failures are never structural vetoes: they make
    safety items ``not_proven`` and add blockers, keeping the bundle in
    ``blocked/not_proven`` instead of ``passed``."""

    safety: dict[str, str] = field(default_factory=dict)
    blockers: tuple[str, ...] = ()
    receipt_digests: dict[str, str] = field(default_factory=dict)
    component_digests: dict[str, str] = field(default_factory=dict)
    posture_digest: str = ""
    attack_digest: str = ""
    cleanup_digest: str = ""


# ---------------------------------------------------------------------------
# Manifest verification (source/artifact raw-byte SHA-256 binding)
# ---------------------------------------------------------------------------


def _verify_manifest(
    root: Path, value: object, *, name: str
) -> tuple[str, dict[str, tuple[int, str]]]:
    """Verify a source/artifact manifest and return ``(raw_sha256, files)``
    where ``files`` maps each relative path to its ``(size, sha256)`` as
    recorded in the manifest.  Every entry is checked against the real file
    bytes, so the map is a trusted binding of path/size/digest."""
    manifest = _object(value, name)
    _keys(manifest, {"raw_sha256", "files"}, name)
    raw = _sha256(manifest.get("raw_sha256"), f"{name}.raw_sha256")
    files = _list(manifest.get("files"), f"{name}.files")
    if not files:
        raise ConfigurationError(f"{name}.files must be non-empty")
    seen: set[str] = set()
    file_map: dict[str, tuple[int, str]] = {}
    for index, item in enumerate(files):
        entry = _object(item, f"{name}.files[{index}]")
        _keys(entry, {"path", "size", "sha256"}, f"{name}.files[{index}]")
        relative = _relative_path(entry.get("path"), f"{name}.files[{index}].path")
        if relative in seen:
            raise ConfigurationError(f"{name} contains duplicate file paths")
        seen.add(relative)
        _file_ref(root, entry, f"{name}.files[{index}]")
        file_map[relative] = (
            _int(entry.get("size"), f"{name}.files[{index}].size"),
            _sha256(entry.get("sha256"), f"{name}.files[{index}].sha256"),
        )
    canonical = _canonical(files)
    if hashlib.sha256(canonical).hexdigest() != raw:
        raise ConfigurationError(f"{name}.raw_sha256 does not bind its files")
    return raw, file_map


# ---------------------------------------------------------------------------
# Run-envelope verification (run id, schema version, source/tree provenance)
# ---------------------------------------------------------------------------


def _verify_run_envelope(
    data: dict[str, Any],
) -> tuple[str, str, str, str, _RunWindow]:
    if data.get("schema") != _SCHEMA:
        raise ConfigurationError("joint evidence must use the hardened joint schema v2")
    schema_version = _string(data.get("schema_version"), "schema_version")
    if schema_version not in _SCHEMA_VERSIONS:
        raise ConfigurationError("unsupported schema_version")
    if data.get("environment") != "production":
        raise ConfigurationError("joint evidence must declare environment=production")
    if _bool(data.get("disposable"), "disposable") is not False:
        raise ConfigurationError("disposable evidence cannot prove production")
    run_id = _string(data.get("run_id"), "run_id")
    if "/" in run_id or "\\" in run_id or run_id in {".", ".."}:
        raise ConfigurationError("run_id must be an opaque non-path name")
    provenance = _object(data.get("provenance"), "provenance")
    _keys(
        provenance,
        {"git_object_format", "source_commit", "source_tree", "dirty", "repository"},
        "provenance",
    )
    object_format = _string(provenance.get("git_object_format"), "provenance.git_object_format")
    if object_format not in _GIT_OBJECT_FORMATS:
        raise ConfigurationError("provenance.git_object_format must be 'sha1' or 'sha256'")
    commit = _git_oid(provenance.get("source_commit"), "provenance.source_commit", object_format)
    tree = _git_oid(provenance.get("source_tree"), "provenance.source_tree", object_format)
    if _bool(provenance.get("dirty"), "provenance.dirty") is not False:
        raise ConfigurationError("production evidence requires a clean checkout")
    repository = _string(provenance.get("repository"), "provenance.repository")
    if not repository.startswith(("https://github.com/", "git@github.com:")):
        raise ConfigurationError("provenance.repository must be an explicit GitHub remote")
    run_started_at = _utc_instant(data.get("run_started_at"), "run_started_at")
    run_completed_at = _utc_instant(data.get("run_completed_at"), "run_completed_at")
    evidence_issued_at = _utc_instant(data.get("evidence_issued_at"), "evidence_issued_at")
    evidence_valid_until = _utc_instant(data.get("evidence_valid_until"), "evidence_valid_until")
    if not (run_started_at <= run_completed_at <= evidence_issued_at < evidence_valid_until):
        raise ConfigurationError(
            "evidence window must satisfy run_started_at <= run_completed_at "
            "<= evidence_issued_at < evidence_valid_until"
        )
    window = _RunWindow(
        started_at=run_started_at,
        completed_at=run_completed_at,
        issued_at=evidence_issued_at,
        valid_until=evidence_valid_until,
    )
    return run_id, object_format, commit, tree, window


# ---------------------------------------------------------------------------
# Command-receipt verification (signed receipt, policy-bound semantics)
# ---------------------------------------------------------------------------


def _verify_receipt(
    root: Path,
    name: str,
    refs: dict[str, Any],
    policy: TrustPolicy,
    run_id: str,
    producer: str,
    previous_end: datetime | None,
    window: _RunWindow,
    artifact_files: dict[str, tuple[int, str]],
    issues: list[str],
) -> tuple[datetime, str, tuple[str, str]]:
    order = _int(refs.get("order"), f"commands.{name}.order")
    if order != _REQUIRED_COMMANDS.index(name):
        raise ConfigurationError("command order must match the required sequence exactly")
    receipt_ref = _object(refs.get("receipt"), f"commands.{name}.receipt")
    parsed, sig_status = _signed_file(
        root,
        receipt_ref,
        refs.get("signature"),
        policy.producer_key(producer),
        f"commands.{name}.receipt",
    )
    if sig_status != "verified":
        issues.append(f"signature:command:{name}")
    _keys(
        parsed,
        {
            "schema",
            "command",
            "order",
            "run_id",
            "producer",
            "executable",
            "argv",
            "working_directory",
            "env_names",
            "started_at",
            "ended_at",
            "timeout_seconds",
            "exit_code",
            "stdout",
            "stderr",
        },
        f"commands.{name}.receipt",
    )
    _verify_receipt_envelope(parsed, name, order, run_id, producer)
    exe_path, exe_digest = _verify_receipt_executable(
        root, parsed, name, policy, artifact_files, issues
    )
    _verify_receipt_semantics(parsed, name, policy, issues)
    ended = _verify_receipt_timing(root, parsed, name, previous_end, window)
    return ended, receipt_ref["sha256"], (exe_path, exe_digest)


def _verify_receipt_envelope(
    parsed: dict[str, Any], name: str, order: int, run_id: str, producer: str
) -> None:
    if _string(parsed.get("schema"), f"commands.{name}.receipt.schema") != RECEIPT_SCHEMA:
        raise ConfigurationError(f"commands.{name}.receipt must use the frozen receipt schema")
    if _string(parsed.get("command"), f"commands.{name}.receipt.command") != name:
        raise ConfigurationError(f"commands.{name}.receipt is bound to a different boundary")
    if _int(parsed.get("order"), f"commands.{name}.receipt.order") != order:
        raise ConfigurationError(f"commands.{name}.receipt order drifted")
    if _string(parsed.get("run_id"), f"commands.{name}.receipt.run_id") != run_id:
        raise ConfigurationError(f"commands.{name}.receipt is bound to a different run_id")
    if _string(parsed.get("producer"), f"commands.{name}.receipt.producer") != producer:
        raise ConfigurationError(f"commands.{name}.receipt producer must equal the boundary owner")


def _verify_receipt_executable(
    root: Path,
    parsed: dict[str, Any],
    name: str,
    policy: TrustPolicy,
    artifact_files: dict[str, tuple[int, str]],
    issues: list[str],
) -> tuple[str, str]:
    """Bind the executable that a receipt claims to have run.

    The ACTUAL file bytes are read and hashed: the verifier requires
    actual-digest == receipt-declared digest == approved-policy digest, and
    additionally requires the executable to appear in the approved artifact
    manifest whose path/size/sha256 entries are themselves checked against the
    real bytes.  An executable that exists only in a receipt/policy declaration
    (or only on disk with a drifted digest) is never acceptable.
    """
    executable = _object(parsed.get("executable"), f"commands.{name}.receipt.executable")
    _keys(executable, {"path", "sha256"}, f"commands.{name}.receipt.executable")
    exe_path = _relative_path(executable.get("path"), f"commands.{name}.receipt.executable.path")
    exe_digest = _sha256(executable.get("sha256"), f"commands.{name}.receipt.executable.sha256")
    exe_file = _real_file(root, exe_path, f"commands.{name}.receipt.executable.path")
    actual_digest = _hash_file(exe_file)
    actual_size = exe_file.stat().st_size
    approved = policy.executables.get(exe_path)
    if approved is None or approved[0] != exe_digest or name not in approved[1]:
        issues.append("artifact_provenance")
    if actual_digest != exe_digest:
        # The file on disk does not match the digest the signed receipt claims.
        issues.append("artifact_provenance")
    manifest_entry = artifact_files.get(exe_path)
    if manifest_entry is None or manifest_entry != (actual_size, actual_digest):
        # The executable is not bound by the approved artifact manifest.
        issues.append("artifact_provenance")
    return exe_path, exe_digest


def _verify_receipt_semantics(
    parsed: dict[str, Any], name: str, policy: TrustPolicy, issues: list[str]
) -> None:
    argv = _list(parsed.get("argv"), f"commands.{name}.receipt.argv")
    if tuple(argv) != policy.command_argv[name]:
        issues.append("command_semantics")
    cwd = _string(parsed.get("working_directory"), f"commands.{name}.receipt.working_directory")
    if cwd not in {"/workspace", "/run/omnibase"} and not cwd.startswith("/run/omnibase/"):
        raise ConfigurationError(f"commands.{name}.receipt must use the approved run root")
    env_names = _list(parsed.get("env_names"), f"commands.{name}.receipt.env_names")
    if not all(isinstance(n, str) and n for n in env_names):
        raise ConfigurationError(f"commands.{name}.receipt.env_names must be strings")
    if any(n in _FORBIDDEN_ENV_NAMES for n in env_names):
        raise ConfigurationError(f"commands.{name}.receipt must not include secret env names")
    if any(n not in policy.allowed_env_names for n in env_names):
        issues.append("command_semantics")


def _verify_receipt_timing(
    root: Path,
    parsed: dict[str, Any],
    name: str,
    previous_end: datetime | None,
    window: _RunWindow,
) -> datetime:
    started = _utc_instant(parsed.get("started_at"), f"commands.{name}.receipt.started_at")
    ended = _utc_instant(parsed.get("ended_at"), f"commands.{name}.receipt.ended_at")
    if ended < started:
        raise ConfigurationError(f"commands.{name}.receipt ended before it started")
    if previous_end is not None and started < previous_end:
        raise ConfigurationError("command chronology is inconsistent with the required order")
    if started < window.started_at or ended > window.completed_at:
        raise ConfigurationError(
            f"commands.{name}.receipt must lie inside the run window "
            "[run_started_at, run_completed_at]"
        )
    timeout = _int(parsed.get("timeout_seconds"), f"commands.{name}.receipt.timeout_seconds")
    if timeout <= 0 or timeout > 3600:
        raise ConfigurationError(f"commands.{name}.receipt.timeout_seconds must be bounded")
    exit_code = _int(parsed.get("exit_code"), f"commands.{name}.receipt.exit_code")
    if exit_code != 0:
        raise ConfigurationError(f"commands.{name}.receipt.exit_code must be 0 for passed evidence")
    for stream_name in ("stdout", "stderr"):
        _file_ref(root, parsed.get(stream_name), f"commands.{name}.receipt.{stream_name}")
    return ended


def _verify_commands(
    root: Path,
    value: object,
    policy: TrustPolicy,
    run_id: str,
    window: _RunWindow,
    artifact_files: dict[str, tuple[int, str]],
    issues: list[str],
) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    commands = _object(value, "commands")
    if set(commands) != set(_REQUIRED_COMMANDS):
        raise ConfigurationError("commands must contain every required joint boundary exactly once")
    receipt_digests: dict[str, str] = {}
    receipt_executables: dict[str, tuple[str, str]] = {}
    previous_end: datetime | None = None
    for index, name in enumerate(_REQUIRED_COMMANDS):
        refs = commands.get(name)
        if not isinstance(refs, dict):
            raise ConfigurationError(f"commands.{name} must be an object")
        _keys(refs, {"order", "receipt", "signature"}, f"commands.{name}")
        if index == 0 and refs.get("signature") is not None:
            _file_ref(root, refs["signature"], f"commands.{name}.signature")
        previous_end, digest, executable = _verify_receipt(
            root,
            name,
            refs,
            policy,
            run_id,
            _COMMAND_PRODUCER[name],
            previous_end,
            window,
            artifact_files,
            issues,
        )
        receipt_digests[name] = digest
        receipt_executables[name] = executable
    return receipt_digests, receipt_executables


# ---------------------------------------------------------------------------
# Component-evidence verification (frozen canonical schema, cross-binds)
# ---------------------------------------------------------------------------


def _verify_gateway_posture(
    parsed: dict[str, Any], policy: TrustPolicy, now: datetime, issues: list[str]
) -> None:
    gateway = _object(parsed.get("gateway"), "components.gateway.evidence.gateway")
    _keys(gateway, {"certificate", "replay"}, "components.gateway.evidence.gateway")
    cert = _object(gateway.get("certificate"), "components.gateway.evidence.gateway.certificate")
    _keys(
        cert,
        {"public_fingerprint", "issuer", "san", "valid_from", "valid_until", "revoked"},
        "components.gateway.evidence.gateway.certificate",
    )
    _sha256(
        cert.get("public_fingerprint"),
        "components.gateway.evidence.gateway.certificate.public_fingerprint",
    )
    issuer = _sha256(cert.get("issuer"), "components.gateway.evidence.gateway.certificate.issuer")
    if issuer != policy.gateway_issuer:
        issues.append("certificate_posture")
    san = _string(cert.get("san"), "components.gateway.evidence.gateway.certificate.san")
    if not san.endswith(policy.gateway_san_suffix):
        issues.append("certificate_posture")
    valid_from = _utc_instant(
        cert.get("valid_from"), "components.gateway.evidence.gateway.certificate.valid_from"
    )
    valid_until = _utc_instant(
        cert.get("valid_until"), "components.gateway.evidence.gateway.certificate.valid_until"
    )
    if valid_until <= valid_from:
        issues.append("certificate_posture")
    if (valid_until - valid_from).total_seconds() > policy.gateway_validity_seconds:
        issues.append("certificate_posture")
    if valid_from > now:
        # A certificate that is not yet valid cannot prove current posture;
        # future certificates are rejected (valid_from <= now is required and
        # valid_from == now is an allowed boundary).
        issues.append("certificate_posture")
    if valid_until <= now:
        # The documented boundary is valid_from <= now < valid_until: a
        # certificate that expires exactly at ``now`` is already expired and
        # cannot prove current posture (valid_until == now fails closed).
        issues.append("certificate_posture")
    if (
        _bool(cert.get("revoked"), "components.gateway.evidence.gateway.certificate.revoked")
        is not False
    ):
        issues.append("certificate_posture")
    replay = _object(gateway.get("replay"), "components.gateway.evidence.gateway.replay")
    _keys(replay, {"replayed", "sequence"}, "components.gateway.evidence.gateway.replay")
    if (
        _bool(replay.get("replayed"), "components.gateway.evidence.gateway.replay.replayed")
        is not False
    ):
        issues.append("replay_posture")
    sequence = _int(replay.get("sequence"), "components.gateway.evidence.gateway.replay.sequence")
    if sequence <= 0:
        issues.append("replay_posture")


def _verify_component(
    root: Path,
    name: str,
    refs: dict[str, Any],
    policy: TrustPolicy,
    run_id: str,
    object_format: str,
    commit: str,
    tree: str,
    source_hash: str,
    artifact_hash: str,
    posture_digest: str,
    attack_digest: str,
    cleanup_digest: str,
    receipt_digests: dict[str, str],
    receipt_executables: dict[str, tuple[str, str]],
    artifact_files: dict[str, tuple[int, str]],
    issues: list[str],
    now: datetime,
) -> tuple[str, str]:
    evidence_ref = _object(refs.get("evidence"), f"components.{name}.evidence")
    parsed, sig_status = _signed_file(
        root,
        evidence_ref,
        refs.get("signature"),
        policy.producer_key(name),
        f"components.{name}.evidence",
    )
    if sig_status != "verified":
        issues.append(f"signature:component:{name}")
    _keys(
        parsed,
        {
            "schema",
            "producer",
            "run_id",
            "git_object_format",
            "source_commit",
            "source_tree",
            "source_manifest_sha256",
            "artifact_manifest_sha256",
            "component_identity",
            "peer_identities",
            "receipts",
            "executables",
            "measurements",
            "results",
            "host",
            "gateway",
        },
        f"components.{name}.evidence",
    )
    _verify_component_envelope(
        parsed, name, run_id, object_format, commit, tree, source_hash, artifact_hash
    )
    identity_digest = _verify_component_identity_peers(parsed, name)
    _verify_component_receipts(parsed, name, receipt_digests)
    _verify_component_executables(parsed, name, policy, receipt_executables, artifact_files, issues)
    _verify_component_bindings(parsed, name, posture_digest, attack_digest, cleanup_digest)
    _verify_component_host(parsed, name)
    if name == "gateway":
        _verify_gateway_posture(parsed, policy, now, issues)
    return str(evidence_ref["sha256"]), identity_digest


def _verify_component_envelope(
    parsed: dict[str, Any],
    name: str,
    run_id: str,
    object_format: str,
    commit: str,
    tree: str,
    source_hash: str,
    artifact_hash: str,
) -> None:
    if _string(parsed.get("schema"), f"components.{name}.evidence.schema") != COMPONENT_SCHEMA:
        raise ConfigurationError(f"components.{name}.evidence must use the frozen component schema")
    if _string(parsed.get("producer"), f"components.{name}.evidence.producer") != name:
        raise ConfigurationError(f"components.{name}.evidence producer must equal the component")
    if _string(parsed.get("run_id"), f"components.{name}.evidence.run_id") != run_id:
        raise ConfigurationError(f"components.{name}.evidence bound to a different run_id")
    component_format = _string(
        parsed.get("git_object_format"), f"components.{name}.evidence.git_object_format"
    )
    if component_format not in _GIT_OBJECT_FORMATS:
        raise ConfigurationError(
            f"components.{name}.evidence.git_object_format must be 'sha1' or 'sha256'"
        )
    if component_format != object_format:
        raise ConfigurationError(
            f"components.{name}.evidence git object format must match the provenance"
        )
    if (
        _git_oid(
            parsed.get("source_commit"),
            f"components.{name}.evidence.source_commit",
            component_format,
        )
        != commit
    ):
        raise ConfigurationError(f"components.{name}.evidence source_commit drifted")
    if (
        _git_oid(
            parsed.get("source_tree"),
            f"components.{name}.evidence.source_tree",
            component_format,
        )
        != tree
    ):
        raise ConfigurationError(f"components.{name}.evidence source_tree drifted")
    if (
        _sha256(
            parsed.get("source_manifest_sha256"),
            f"components.{name}.evidence.source_manifest_sha256",
        )
        != source_hash
    ):
        raise ConfigurationError(f"components.{name}.evidence source manifest binding drifted")
    if (
        _sha256(
            parsed.get("artifact_manifest_sha256"),
            f"components.{name}.evidence.artifact_manifest_sha256",
        )
        != artifact_hash
    ):
        raise ConfigurationError(f"components.{name}.evidence artifact manifest binding drifted")


def _verify_component_identity_peers(parsed: dict[str, Any], name: str) -> str:
    identity = _object(parsed.get("component_identity"), f"components.{name}.evidence.identity")
    _keys(identity, {"kind", "value"}, f"components.{name}.evidence.identity")
    if _string(identity.get("kind"), f"components.{name}.evidence.identity.kind") != "sha256":
        raise ConfigurationError(f"components.{name}.evidence identity must be a sha256 digest")
    identity_digest = _sha256(identity.get("value"), f"components.{name}.evidence.identity.value")
    peers = _object(parsed.get("peer_identities"), f"components.{name}.evidence.peer_identities")
    if set(peers) != set(_REQUIRED_PEERS[name]):
        raise ConfigurationError(f"components.{name}.evidence peer set must match the topology")
    for peer in sorted(peers):
        _sha256(peers.get(peer), f"components.{name}.evidence.peer_identities.{peer}")
    return identity_digest


def _verify_component_receipts(
    parsed: dict[str, Any], name: str, receipt_digests: dict[str, str]
) -> None:
    receipts = _object(parsed.get("receipts"), f"components.{name}.evidence.receipts")
    owned = [command for command, owner in _COMMAND_PRODUCER.items() if owner == name]
    if set(receipts) != set(owned):
        raise ConfigurationError(f"components.{name}.evidence must bind its owned command receipts")
    for command in sorted(owned):
        if _sha256(
            receipts.get(command), f"components.{name}.evidence.receipts.{command}"
        ) != receipt_digests.get(command):
            raise ConfigurationError(f"components.{name}.evidence receipt binding drifted")


def _verify_component_executables(
    parsed: dict[str, Any],
    name: str,
    policy: TrustPolicy,
    receipt_executables: dict[str, tuple[str, str]],
    artifact_files: dict[str, tuple[int, str]],
    issues: list[str],
) -> None:
    owned = [command for command, owner in _COMMAND_PRODUCER.items() if owner == name]
    executables = _list(parsed.get("executables"), f"components.{name}.evidence.executables")
    expected_executables = {receipt_executables[command] for command in owned}
    found_executables: set[tuple[str, str]] = set()
    for index, item in enumerate(executables):
        entry = _object(item, f"components.{name}.evidence.executables[{index}]")
        _keys(entry, {"path", "sha256"}, f"components.{name}.evidence.executables[{index}]")
        exe_path = _relative_path(
            entry.get("path"), f"components.{name}.evidence.executables[{index}].path"
        )
        exe_digest = _sha256(
            entry.get("sha256"), f"components.{name}.evidence.executables[{index}].sha256"
        )
        approved = policy.executables.get(exe_path)
        if approved is None or approved[0] != exe_digest:
            issues.append("artifact_provenance")
        manifest_entry = artifact_files.get(exe_path)
        if manifest_entry is None or manifest_entry[1] != exe_digest:
            # The component declares an executable that the approved artifact
            # manifest does not bind to this digest.
            issues.append("artifact_provenance")
        found_executables.add((exe_path, exe_digest))
    if found_executables != expected_executables:
        issues.append("artifact_provenance")


def _verify_component_bindings(
    parsed: dict[str, Any],
    name: str,
    posture_digest: str,
    attack_digest: str,
    cleanup_digest: str,
) -> None:
    measurements = _object(parsed.get("measurements"), f"components.{name}.evidence.measurements")
    _keys(measurements, {"posture_sha256"}, f"components.{name}.evidence.measurements")
    if (
        _sha256(
            measurements.get("posture_sha256"),
            f"components.{name}.evidence.measurements.posture_sha256",
        )
        != posture_digest
    ):
        raise ConfigurationError(
            f"components.{name}.evidence does not bind the posture measurement"
        )
    results = _object(parsed.get("results"), f"components.{name}.evidence.results")
    _keys(
        results, {"attack_matrix_sha256", "cleanup_sha256"}, f"components.{name}.evidence.results"
    )
    if (
        _sha256(
            results.get("attack_matrix_sha256"),
            f"components.{name}.evidence.results.attack_matrix_sha256",
        )
        != attack_digest
    ):
        raise ConfigurationError(f"components.{name}.evidence does not bind the attack matrix")
    if (
        _sha256(results.get("cleanup_sha256"), f"components.{name}.evidence.results.cleanup_sha256")
        != cleanup_digest
    ):
        raise ConfigurationError(f"components.{name}.evidence does not bind the cleanup inventory")


def _verify_component_host(parsed: dict[str, Any], name: str) -> None:
    host = _object(parsed.get("host"), f"components.{name}.evidence.host")
    _keys(host, {"os", "kernel", "arch"}, f"components.{name}.evidence.host")
    _string(host.get("os"), f"components.{name}.evidence.host.os")
    _string(host.get("kernel"), f"components.{name}.evidence.host.kernel")
    _string(host.get("arch"), f"components.{name}.evidence.host.arch")


def _verify_components(
    root: Path,
    value: object,
    policy: TrustPolicy,
    run_id: str,
    object_format: str,
    commit: str,
    tree: str,
    source_hash: str,
    artifact_hash: str,
    posture_digest: str,
    attack_digest: str,
    cleanup_digest: str,
    receipt_digests: dict[str, str],
    receipt_executables: dict[str, tuple[str, str]],
    artifact_files: dict[str, tuple[int, str]],
    issues: list[str],
    now: datetime,
) -> dict[str, str]:
    components = _object(value, "components")
    if set(components) != set(_REQUIRED_COMPONENTS):
        raise ConfigurationError("components must contain all six joint gates exactly once")
    identities: dict[str, str] = {}
    evidence_digests: dict[str, str] = {}
    for name in _REQUIRED_COMPONENTS:
        refs = components.get(name)
        if not isinstance(refs, dict):
            raise ConfigurationError(f"components.{name} must be an object")
        _keys(refs, {"evidence", "signature"}, f"components.{name}")
        evidence_digest, identity_digest = _verify_component(
            root,
            name,
            refs,
            policy,
            run_id,
            object_format,
            commit,
            tree,
            source_hash,
            artifact_hash,
            posture_digest,
            attack_digest,
            cleanup_digest,
            receipt_digests,
            receipt_executables,
            artifact_files,
            issues,
            now,
        )
        identities[name] = identity_digest
        evidence_digests[name] = evidence_digest
    for name in _REQUIRED_COMPONENTS:
        refs = components.get(name)
        if not isinstance(refs, dict):
            raise ConfigurationError(f"components.{name} must be an object")
        evidence_ref = _object(refs.get("evidence"), f"components.{name}.evidence")
        evidence_path = _file_ref(root, evidence_ref, f"components.{name}.evidence")
        parsed, _raw = _read_canonical_json(evidence_path, f"components.{name}.evidence")
        peers = _object(
            parsed.get("peer_identities"), f"components.{name}.evidence.peer_identities"
        )
        for peer in _REQUIRED_PEERS[name]:
            if peers.get(peer) != identities[peer]:
                raise ConfigurationError(
                    f"components.{name}.evidence peer identity does not match the peer"
                )
    return evidence_digests


# ---------------------------------------------------------------------------
# Repository invariants, measurements, attack matrix and cleanup
# ---------------------------------------------------------------------------


def _verify_repository_invariants(data: dict[str, Any]) -> tuple[dict[str, str], dict[str, bool]]:
    safety: dict[str, str] = {}
    gates: dict[str, bool] = {}
    migration_head = _string(data.get("migration_head"), "migration_head")
    if migration_head != "0013":
        raise ConfigurationError("migration head must remain 0013")
    safety["migration_head"] = migration_head
    feature_gates = _object(data.get("feature_gates"), "feature_gates")
    if set(feature_gates) != set(_REQUIRED_FEATURE_GATES):
        raise ConfigurationError("feature_gates must contain exactly the three Phase 5 gates")
    for gate_name in _REQUIRED_FEATURE_GATES:
        if _bool(feature_gates.get(gate_name), f"feature_gates.{gate_name}") is not False:
            raise ConfigurationError("Phase 5 feature gates must remain false")
        gates[gate_name] = False
        safety[gate_name] = "false"
    return safety, gates


def _require_inside_run_window(instant: datetime, name: str, window: _RunWindow) -> None:
    """Structural guard: an evidence timestamp must lie inside the frozen run
    window ``[run_started_at, run_completed_at]`` (inclusive)."""
    if not (window.started_at <= instant <= window.completed_at):
        raise ConfigurationError(f"{name} must lie inside the run window")


def _verify_posture(
    root: Path,
    value: object,
    policy: TrustPolicy,
    run_id: str,
    window: _RunWindow,
    issues: list[str],
    now: datetime,
) -> tuple[str, dict[str, str]]:
    measurements = _object(value, "measurements")
    _keys(measurements, {"posture"}, "measurements")
    posture = _object(measurements.get("posture"), "measurements.posture")
    _keys(posture, {"evidence", "signature"}, "measurements.posture")
    evidence_ref = _object(posture.get("evidence"), "measurements.posture.evidence")
    parsed, sig_status = _signed_file(
        root,
        evidence_ref,
        posture.get("signature"),
        policy.producer_key("core"),
        "measurements.posture.evidence",
    )
    if sig_status != "verified":
        issues.append("signature:posture")
    _keys(
        parsed,
        {
            "schema",
            "producer",
            "run_id",
            "measured",
            "measured_at",
            "measurement_source",
            "production_runtime_activated",
            "hostile_code_executed",
            "root_env_accessed",
            "business_database_accessed",
            "business_database_migrated",
            "host",
        },
        "measurements.posture.evidence",
    )
    if _string(parsed.get("schema"), "measurements.posture.evidence.schema") != POSTURE_SCHEMA:
        raise ConfigurationError("measurements.posture must use the frozen posture schema")
    if _string(parsed.get("producer"), "measurements.posture.evidence.producer") != "core":
        raise ConfigurationError("measurements.posture producer must be core")
    if _string(parsed.get("run_id"), "measurements.posture.evidence.run_id") != run_id:
        raise ConfigurationError("measurements.posture is bound to a different run_id")
    measured = _bool(parsed.get("measured"), "measurements.posture.evidence.measured")
    measured_at = _utc_instant(
        parsed.get("measured_at"), "measurements.posture.evidence.measured_at"
    )
    _require_inside_run_window(measured_at, "measurements.posture.evidence.measured_at", window)
    source = _string(
        parsed.get("measurement_source"), "measurements.posture.evidence.measurement_source"
    )
    if source not in {"process_config", "service_config", "host_probe"}:
        raise ConfigurationError(
            "measurements.posture.evidence.measurement_source must be an approved kind"
        )
    host = _object(parsed.get("host"), "measurements.posture.evidence.host")
    _keys(host, {"os", "kernel", "arch"}, "measurements.posture.evidence.host")
    activated = _bool(
        parsed.get("production_runtime_activated"),
        "measurements.posture.evidence.production_runtime_activated",
    )
    hostile = _bool(
        parsed.get("hostile_code_executed"), "measurements.posture.evidence.hostile_code_executed"
    )
    root_env = _bool(
        parsed.get("root_env_accessed"), "measurements.posture.evidence.root_env_accessed"
    )
    db_accessed = _bool(
        parsed.get("business_database_accessed"),
        "measurements.posture.evidence.business_database_accessed",
    )
    db_migrated = _bool(
        parsed.get("business_database_migrated"),
        "measurements.posture.evidence.business_database_migrated",
    )
    trusted = measured and sig_status == "verified" and measured_at <= now
    safety: dict[str, str] = {}
    safety["runtime_posture"] = f"measured:{source}" if trusted else "not_proven"
    safety["production_runtime_inactive"] = (
        "verified" if trusted and not activated else "not_proven"
    )
    safety["hostile_code_not_executed"] = "verified" if trusted and not hostile else "not_proven"
    safety["root_env_not_accessed"] = "verified" if trusted and not root_env else "not_proven"
    safety["business_database_not_accessed"] = (
        "verified" if trusted and not db_accessed else "not_proven"
    )
    safety["business_database_not_migrated"] = (
        "verified" if trusted and not db_migrated else "not_proven"
    )
    return posture["evidence"]["sha256"], safety


def _verify_attack_matrix(
    root: Path,
    value: object,
    policy: TrustPolicy,
    run_id: str,
    window: _RunWindow,
    issues: list[str],
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    attack = _object(value, "attack_matrix")
    _keys(attack, {"evidence", "signature"}, "attack_matrix")
    evidence_ref = _object(attack.get("evidence"), "attack_matrix.evidence")
    parsed, sig_status = _signed_file(
        root,
        evidence_ref,
        attack.get("signature"),
        policy.producer_key("runner"),
        "attack_matrix.evidence",
    )
    if sig_status != "verified":
        issues.append("signature:attack")
    _keys(
        parsed,
        {"schema", "producer", "run_id", "executed_at", "results", "inventory"},
        "attack_matrix.evidence",
    )
    if _string(parsed.get("schema"), "attack_matrix.evidence.schema") != ATTACK_SCHEMA:
        raise ConfigurationError("attack_matrix must use the frozen attack-matrix schema")
    if _string(parsed.get("producer"), "attack_matrix.evidence.producer") != "runner":
        raise ConfigurationError("attack_matrix producer must be runner")
    if _string(parsed.get("run_id"), "attack_matrix.evidence.run_id") != run_id:
        raise ConfigurationError("attack_matrix is bound to a different run_id")
    executed_at = _utc_instant(parsed.get("executed_at"), "attack_matrix.evidence.executed_at")
    _require_inside_run_window(executed_at, "attack_matrix.evidence.executed_at", window)
    results = _object(parsed.get("results"), "attack_matrix.evidence.results")
    if set(results) != set(_REQUIRED_ATTACKS):
        raise ConfigurationError(
            "attack_matrix.evidence.results must contain every required attack"
        )
    inventory = _list(parsed.get("inventory"), "attack_matrix.evidence.inventory")
    seen: set[str] = set()
    inventory_map: dict[str, str] = {}
    for index, item in enumerate(inventory):
        entry = _object(item, f"attack_matrix.evidence.inventory[{index}]")
        _keys(
            entry,
            {"attack_id", "outcome", "attempted_at", "evidence_digest"},
            f"attack_matrix.evidence.inventory[{index}]",
        )
        attack_id = _string(
            entry.get("attack_id"), f"attack_matrix.evidence.inventory[{index}].attack_id"
        )
        if attack_id in seen:
            raise ConfigurationError("attack_matrix.evidence.inventory contains duplicates")
        seen.add(attack_id)
        outcome = _string(
            entry.get("outcome"), f"attack_matrix.evidence.inventory[{index}].outcome"
        )
        attempted_at = _utc_instant(
            entry.get("attempted_at"), f"attack_matrix.evidence.inventory[{index}].attempted_at"
        )
        _require_inside_run_window(
            attempted_at, f"attack_matrix.evidence.inventory[{index}].attempted_at", window
        )
        _sha256(
            entry.get("evidence_digest"),
            f"attack_matrix.evidence.inventory[{index}].evidence_digest",
        )
        inventory_map[attack_id] = outcome
    blockers: list[str] = []
    for attack_name in _REQUIRED_ATTACKS:
        outcome = _string(results.get(attack_name), f"attack_matrix.evidence.results.{attack_name}")
        if outcome not in _ALLOWED_ATTACK_OUTCOMES or inventory_map.get(attack_name) != outcome:
            blockers.append(f"attack:{attack_name}")
    if set(inventory_map) != set(_REQUIRED_ATTACKS) or len(inventory_map) != len(inventory):
        blockers.append("attack:inventory")
    safety: dict[str, str] = {}
    if blockers or sig_status != "verified":
        safety["attack_results"] = "not_proven"
    else:
        safety["attack_results"] = "verified"
    return attack["evidence"]["sha256"], safety, tuple(blockers)


def _verify_cleanup(
    root: Path,
    value: object,
    policy: TrustPolicy,
    run_id: str,
    window: _RunWindow,
    issues: list[str],
) -> tuple[str, dict[str, str], tuple[str, ...]]:
    cleanup = _object(value, "cleanup")
    _keys(cleanup, {"evidence", "signature"}, "cleanup")
    evidence_ref = _object(cleanup.get("evidence"), "cleanup.evidence")
    parsed, sig_status = _signed_file(
        root,
        evidence_ref,
        cleanup.get("signature"),
        policy.producer_key("sealer"),
        "cleanup.evidence",
    )
    if sig_status != "verified":
        issues.append("signature:cleanup")
    _keys(
        parsed,
        {"schema", "producer", "run_id", "completed_at", "counts", "inventory"},
        "cleanup.evidence",
    )
    if _string(parsed.get("schema"), "cleanup.evidence.schema") != CLEANUP_SCHEMA:
        raise ConfigurationError("cleanup must use the frozen cleanup-inventory schema")
    if _string(parsed.get("producer"), "cleanup.evidence.producer") != "sealer":
        raise ConfigurationError("cleanup producer must be sealer")
    if _string(parsed.get("run_id"), "cleanup.evidence.run_id") != run_id:
        raise ConfigurationError("cleanup is bound to a different run_id")
    completed_at = _utc_instant(parsed.get("completed_at"), "cleanup.evidence.completed_at")
    _require_inside_run_window(completed_at, "cleanup.evidence.completed_at", window)
    counts = _object(parsed.get("counts"), "cleanup.evidence.counts")
    if set(counts) != set(_REQUIRED_CLEANUP_KEYS):
        raise ConfigurationError("cleanup.evidence.counts must contain every required class")
    tally: dict[str, int] = {key: 0 for key in _REQUIRED_CLEANUP_KEYS}
    inventory = _list(parsed.get("inventory"), "cleanup.evidence.inventory")
    for index, item in enumerate(inventory):
        entry = _object(item, f"cleanup.evidence.inventory[{index}]")
        _keys(entry, {"class", "item_id", "removed_at"}, f"cleanup.evidence.inventory[{index}]")
        class_name = _string(entry.get("class"), f"cleanup.evidence.inventory[{index}].class")
        if class_name not in _REQUIRED_CLEANUP_KEYS:
            raise ConfigurationError(f"cleanup.evidence.inventory[{index}].class is unknown")
        _string(entry.get("item_id"), f"cleanup.evidence.inventory[{index}].item_id")
        removed_at = _utc_instant(
            entry.get("removed_at"), f"cleanup.evidence.inventory[{index}].removed_at"
        )
        _require_inside_run_window(
            removed_at, f"cleanup.evidence.inventory[{index}].removed_at", window
        )
        tally[class_name] += 1
    blockers: list[str] = []
    for key in _REQUIRED_CLEANUP_KEYS:
        recorded = _int(counts.get(key), f"cleanup.evidence.counts.{key}")
        if recorded < 0 or recorded != tally[key] or recorded != 0:
            blockers.append(f"cleanup:{key}")
    safety: dict[str, str] = {}
    if blockers or sig_status != "verified":
        safety["cleanup_complete"] = "not_proven"
    else:
        safety["cleanup_complete"] = "verified"
    return cleanup["evidence"]["sha256"], safety, tuple(blockers)


def _verify_seal(
    root: Path,
    data: dict[str, Any],
    policy: TrustPolicy,
    run_id: str,
    object_format: str,
    commit: str,
    tree: str,
    window: _RunWindow,
    source_hash: str,
    artifact_hash: str,
    receipt_digests: dict[str, str],
    component_digests: dict[str, str],
    posture_digest: str,
    attack_digest: str,
    cleanup_digest: str,
    gates: dict[str, bool],
    pre_seal_safety: dict[str, str],
    issues: list[str],
) -> dict[str, str]:
    seal = _object(data.get("evidence_seal"), "evidence_seal")
    _keys(seal, {"producer", "binding_sha256", "signature"}, "evidence_seal")
    if _string(seal.get("producer"), "evidence_seal.producer") != "sealer":
        raise ConfigurationError("evidence_seal.producer must be sealer")
    binding = _seal_binding(
        data,
        run_id,
        object_format,
        commit,
        tree,
        window,
        source_hash,
        artifact_hash,
        receipt_digests,
        component_digests,
        posture_digest,
        attack_digest,
        cleanup_digest,
        gates,
        pre_seal_safety,
    )
    binding_bytes = _canonical(binding)
    recorded = _sha256(seal.get("binding_sha256"), "evidence_seal.binding_sha256")
    if hashlib.sha256(binding_bytes).hexdigest() != recorded:
        raise ConfigurationError("evidence_seal.binding_sha256 does not bind the verified chain")
    sig_status = "absent"
    signature_ref = seal.get("signature")
    if signature_ref is not None:
        sig_path = _file_ref(root, signature_ref, "evidence_seal.signature")
        ok = _verify_ed25519(policy.producer_key("sealer"), binding_bytes, sig_path.read_bytes())
        sig_status = "verified" if ok else "invalid"
    if sig_status != "verified":
        issues.append("signature:seal")
    return {"evidence_seal": "verified" if sig_status == "verified" else "not_proven"}


def _seal_binding(
    data: dict[str, Any],
    run_id: str,
    object_format: str,
    commit: str,
    tree: str,
    window: _RunWindow,
    source_hash: str,
    artifact_hash: str,
    receipt_digests: dict[str, str],
    component_digests: dict[str, str],
    posture_digest: str,
    attack_digest: str,
    cleanup_digest: str,
    gates: dict[str, bool],
    safety: dict[str, str],
) -> dict[str, Any]:
    """The canonical evidence-seal binding.

    It covers schema/schema_version, environment, disposable, the full
    provenance (repository, git object format, source_commit, source_tree,
    dirty), the complete evidence validity window (run_started_at,
    run_completed_at, evidence_issued_at, evidence_valid_until) and every
    current top-level security posture derived from the verified chain, in
    addition to the digest chain.  Because the verifier recomputes this binding
    from the verified data, any outer-field rewrite (environment,
    disposable, provenance, window, safety-relevant evidence) changes the
    recomputed bytes and fails the recorded binding digest / detached
    signature.
    """
    provenance = _object(data.get("provenance"), "provenance")
    return {
        "schema": SEAL_SCHEMA,
        "schema_version": _string(data.get("schema_version"), "schema_version"),
        "producer": "sealer",
        "run_id": run_id,
        "environment": _string(data.get("environment"), "environment"),
        "disposable": _bool(data.get("disposable"), "disposable"),
        "run_started_at": _utc_instant(data.get("run_started_at"), "run_started_at")
        .isoformat()
        .replace("+00:00", "Z"),
        "run_completed_at": _utc_instant(data.get("run_completed_at"), "run_completed_at")
        .isoformat()
        .replace("+00:00", "Z"),
        "evidence_issued_at": _utc_instant(data.get("evidence_issued_at"), "evidence_issued_at")
        .isoformat()
        .replace("+00:00", "Z"),
        "evidence_valid_until": _utc_instant(
            data.get("evidence_valid_until"), "evidence_valid_until"
        )
        .isoformat()
        .replace("+00:00", "Z"),
        "provenance": {
            "repository": _string(provenance.get("repository"), "provenance.repository"),
            "git_object_format": object_format,
            "source_commit": commit,
            "source_tree": tree,
            "dirty": _bool(provenance.get("dirty"), "provenance.dirty"),
        },
        "source_manifest_sha256": source_hash,
        "artifact_manifest_sha256": artifact_hash,
        "commands": dict(sorted(receipt_digests.items())),
        "components": dict(sorted(component_digests.items())),
        "posture_measurement": posture_digest,
        "attack_matrix": attack_digest,
        "cleanup": cleanup_digest,
        "migration_head": "0013",
        "feature_gates": dict(sorted(gates.items())),
        "safety": dict(sorted(safety.items())),
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_TOP_LEVEL_KEYS = {
    "schema",
    "schema_version",
    "run_id",
    "run_started_at",
    "run_completed_at",
    "evidence_issued_at",
    "evidence_valid_until",
    "environment",
    "disposable",
    "provenance",
    "source_manifest",
    "artifact_manifest",
    "commands",
    "components",
    "measurements",
    "migration_head",
    "feature_gates",
    "attack_matrix",
    "cleanup",
    "evidence_seal",
}


def validate_joint_evidence_contract(payload: object) -> JointGateReport:
    """Validate the static P34.7 joint-evidence schema and contract only.

    This mode never accepts inline evidence as direct execution proof and
    therefore always returns ``blocked/not_proven``.
    """
    data = _object(payload, "joint evidence contract")
    _keys(data, _TOP_LEVEL_KEYS, "joint evidence contract")
    run_id, _object_format, _commit, _tree, _window = _verify_run_envelope(data)
    return JointGateReport(
        status="blocked/not_proven",
        run_id=run_id,
        schema=_SCHEMA,
        source_manifest_sha256="not_proven",
        artifact_manifest_sha256="not_proven",
        blockers=("contract_mode_no_direct_evidence",),
        mode="validate-only",
    )


def _verify_trust_policy(
    trust_policy_path: Path | None,
    root: Path,
    repository: str,
    object_format: str,
) -> tuple[TrustPolicy | None, str, str]:
    if trust_policy_path is None:
        return None, "not_proven", "trust_policy_unavailable"
    policy_path = (
        trust_policy_path if trust_policy_path.is_absolute() else Path.cwd() / trust_policy_path
    )
    policy, raw_sha256 = load_trust_policy(policy_path)
    try:
        policy_path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        pass
    else:
        raise ConfigurationError("trust policy must be located outside the evidence run directory")
    if repository != policy.repository:
        raise ConfigurationError("provenance.repository does not match the trust policy")
    if object_format != policy.git_object_format:
        raise ConfigurationError(
            "trust policy git object format must match the evidence provenance "
            "(policy/evidence object-format drift fails closed)"
        )
    if raw_sha256 not in _APPROVED_TRUST_POLICY_SHA256:
        return policy, "not_proven", "trust_policy_not_approved"
    return policy, "verified", ""


def _chain_outcome_from_issues(issues: list[str]) -> _ChainOutcome:
    """Derive the aggregated safety/blockers from a full evidence chain."""
    safety: dict[str, str] = {}
    safety["signature_authenticity"] = (
        "not_proven" if any(i.startswith("signature:") for i in issues) else "verified"
    )
    safety["artifact_provenance"] = "not_proven" if "artifact_provenance" in issues else "verified"
    safety["command_semantics"] = "not_proven" if "command_semantics" in issues else "verified"
    safety["certificate_posture"] = "not_proven" if "certificate_posture" in issues else "verified"
    safety["replay_posture"] = "not_proven" if "replay_posture" in issues else "verified"
    safety["evidence_freshness"] = "not_proven" if "evidence_freshness" in issues else "verified"
    blockers = tuple(sorted(issues))
    return _ChainOutcome(safety=safety, blockers=blockers)


def _verify_freshness(
    window: _RunWindow, policy: TrustPolicy, now: datetime, issues: list[str]
) -> None:
    """Freshness of a signed but not-yet-expired bundle.

    ``now`` must satisfy ``evidence_issued_at <= now < evidence_valid_until``
    and the evidence age must not exceed the policy maximum; otherwise the
    bundle is stale and ``evidence_freshness`` becomes a blocker.  The validity
    window length itself is a structural property: a window longer than the
    policy maximum is a veto (``ConfigurationError``), never a pass."""
    if not (window.issued_at <= now < window.valid_until):
        issues.append("evidence_freshness")
    if (now - window.issued_at) > timedelta(seconds=policy.max_evidence_age_seconds):
        issues.append("evidence_freshness")
    if (window.valid_until - window.issued_at) > timedelta(seconds=policy.max_evidence_age_seconds):
        raise ConfigurationError(
            "evidence validity window exceeds the trust policy maximum "
            "(max_evidence_age_seconds)"
        )


def _derive_chain(
    root: Path,
    data: dict[str, Any],
    policy: TrustPolicy,
    run_id: str,
    object_format: str,
    commit: str,
    tree: str,
    window: _RunWindow,
    source_hash: str,
    artifact_hash: str,
    artifact_files: dict[str, tuple[int, str]],
    gates: dict[str, bool],
    now: datetime,
) -> tuple[
    list[str],
    dict[str, str],
    dict[str, str],
    str,
    str,
    str,
    dict[str, str],
    dict[str, str],
    dict[str, str],
    dict[str, str],
    tuple[str, ...],
    tuple[str, ...],
]:
    """Run every non-seal verification step of the evidence chain.

    Returns ``(issues, receipt_digests, component_digests, posture_digest,
    attack_digest, cleanup_digest, posture_safety, attack_safety,
    cleanup_safety, pre_seal_safety, attack_blockers, cleanup_blockers)``.
    The report safety is derived at the verification ``now`` (the UTC clock
    seam); the pre-seal safety the evidence seal must bind is derived at the
    ISSUE-TIME clock ``window.issued_at`` instead, so the recorded binding is
    deterministic from the evidence alone and never drifts when the same
    bundle is later re-verified at a different instant (an expired bundle
    keeps its valid seal and fails on ``evidence_freshness``; a certificate
    expiring exactly at ``now`` fails on ``certificate_posture``)."""
    issues: list[str] = []
    _verify_freshness(window, policy, now, issues)
    posture_digest, posture_safety = _verify_posture(
        root, data.get("measurements"), policy, run_id, window, issues, now
    )
    attack_digest, attack_safety, attack_blockers = _verify_attack_matrix(
        root, data.get("attack_matrix"), policy, run_id, window, issues
    )
    cleanup_digest, cleanup_safety, cleanup_blockers = _verify_cleanup(
        root, data.get("cleanup"), policy, run_id, window, issues
    )
    receipt_digests, receipt_executables = _verify_commands(
        root, data.get("commands"), policy, run_id, window, artifact_files, issues
    )
    component_digests = _verify_components(
        root,
        data.get("components"),
        policy,
        run_id,
        object_format,
        commit,
        tree,
        source_hash,
        artifact_hash,
        posture_digest,
        attack_digest,
        cleanup_digest,
        receipt_digests,
        receipt_executables,
        artifact_files,
        issues,
        now,
    )
    # Pre-seal safety: derive at the issue-time clock.  The window constraint
    # guarantees measured_at <= issued_at, and freshness always holds at
    # issued_at, so the binding posture is a pure function of the evidence.
    binding_issues: list[str] = []
    _verify_freshness(window, policy, window.issued_at, binding_issues)
    _binding_posture_digest, binding_posture_safety = _verify_posture(
        root,
        data.get("measurements"),
        policy,
        run_id,
        window,
        binding_issues,
        window.issued_at,
    )
    _verify_components(
        root,
        data.get("components"),
        policy,
        run_id,
        object_format,
        commit,
        tree,
        source_hash,
        artifact_hash,
        posture_digest,
        attack_digest,
        cleanup_digest,
        receipt_digests,
        receipt_executables,
        artifact_files,
        binding_issues,
        window.issued_at,
    )
    pre_seal_safety = dict(_chain_outcome_from_issues(binding_issues).safety)
    pre_seal_safety.update(binding_posture_safety)
    pre_seal_safety.update(attack_safety)
    pre_seal_safety.update(cleanup_safety)
    return (
        issues,
        receipt_digests,
        component_digests,
        posture_digest,
        attack_digest,
        cleanup_digest,
        posture_safety,
        attack_safety,
        cleanup_safety,
        pre_seal_safety,
        attack_blockers,
        cleanup_blockers,
    )


def _verify_bundle(
    root: Path,
    data: dict[str, Any],
    policy: TrustPolicy,
    run_id: str,
    object_format: str,
    commit: str,
    tree: str,
    window: _RunWindow,
    source_hash: str,
    artifact_hash: str,
    artifact_files: dict[str, tuple[int, str]],
    gates: dict[str, bool],
    now: datetime,
) -> _ChainOutcome:
    (
        issues,
        receipt_digests,
        component_digests,
        posture_digest,
        attack_digest,
        cleanup_digest,
        posture_safety,
        attack_safety,
        cleanup_safety,
        pre_seal_safety,
        attack_blockers,
        cleanup_blockers,
    ) = _derive_chain(
        root,
        data,
        policy,
        run_id,
        object_format,
        commit,
        tree,
        window,
        source_hash,
        artifact_hash,
        artifact_files,
        gates,
        now,
    )
    seal_safety = _verify_seal(
        root,
        data,
        policy,
        run_id,
        object_format,
        commit,
        tree,
        window,
        source_hash,
        artifact_hash,
        receipt_digests,
        component_digests,
        posture_digest,
        attack_digest,
        cleanup_digest,
        gates,
        pre_seal_safety,
        issues,
    )
    outcome = _chain_outcome_from_issues(issues)
    merged_safety = dict(outcome.safety)
    for partial in (posture_safety, attack_safety, cleanup_safety, seal_safety):
        merged_safety.update(partial)
    merged_blockers = tuple(
        sorted(set(outcome.blockers) | set(attack_blockers) | set(cleanup_blockers))
    )
    return _ChainOutcome(
        safety=merged_safety,
        blockers=merged_blockers,
        receipt_digests=receipt_digests,
        component_digests=component_digests,
        posture_digest=posture_digest,
        attack_digest=attack_digest,
        cleanup_digest=cleanup_digest,
    )


def _blocked_safety(base: dict[str, str]) -> dict[str, str]:
    safety = dict(base)
    for key in (
        "trust_policy",
        "source_provenance",
        "signature_authenticity",
        "artifact_provenance",
        "command_semantics",
        "runtime_posture",
        "production_runtime_inactive",
        "hostile_code_not_executed",
        "root_env_not_accessed",
        "business_database_not_accessed",
        "business_database_not_migrated",
        "attack_results",
        "cleanup_complete",
        "certificate_posture",
        "replay_posture",
        "evidence_freshness",
        "evidence_seal",
    ):
        safety.setdefault(key, "not_proven")
    return safety


def _finalize_report(
    run_id: str,
    source_hash: str,
    artifact_hash: str,
    safety: dict[str, str],
    extra_blockers: list[str],
    mode: str,
) -> JointGateReport:
    blockers = sorted(
        {key for key, value in safety.items() if value == "not_proven"} | set(extra_blockers)
    )
    status = "blocked/not_proven" if blockers else "passed"
    return JointGateReport(
        status=status,
        run_id=run_id,
        schema=_SCHEMA,
        source_manifest_sha256=source_hash,
        artifact_manifest_sha256=artifact_hash,
        blockers=tuple(blockers),
        mode=mode,
        safety=safety,
    )


def verify_joint_evidence(
    run_dir: Path,
    payload: object,
    trust_policy_path: Path | None = None,
    *,
    now: datetime | None = None,
) -> JointGateReport:
    """Verify one immutable, run-scoped P34.7 evidence bundle against the
    external trust policy.

    May return ``passed`` only when the trust policy is approved (its raw
    bytes hash to a digest pinned in this module), every detached signature
    verifies against a policy producer key, every canonical component schema
    parses and cross-binds, and every safety item is proven.  Any structural
    violation raises :class:`ConfigurationError` (``invalid/veto``); any
    authenticity or safety gap is reported as ``blocked/not_proven`` with a
    blocker.  Unsigned evidence can never pass.

    ``now`` is the UTC clock seam: it is read exactly once per verification
    and threaded through every time check (freshness, posture and certificate
    validity).  Tests may inject a fixed instant for deterministic boundary
    proofs; callers may omit it to use the wall clock.
    """
    clock = now if now is not None else _utc_now()
    root = run_dir.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ConfigurationError("run directory must be a regular directory")
    data = _object(payload, "joint evidence")
    _keys(data, _TOP_LEVEL_KEYS, "joint evidence")
    run_id, object_format, commit, tree, window = _verify_run_envelope(data)
    source_hash, _source_files = _verify_manifest(
        root, data.get("source_manifest"), name="source_manifest"
    )
    artifact_hash, artifact_files = _verify_manifest(
        root, data.get("artifact_manifest"), name="artifact_manifest"
    )
    safety, gates = _verify_repository_invariants(data)
    provenance = _object(data.get("provenance"), "provenance")
    repository = _string(provenance.get("repository"), "provenance.repository")
    policy, policy_status, policy_blocker = _verify_trust_policy(
        trust_policy_path, root, repository, object_format
    )
    extra_blockers: list[str] = [policy_blocker] if policy_blocker else []
    if policy is not None:
        outcome = _verify_bundle(
            root,
            data,
            policy,
            run_id,
            object_format,
            commit,
            tree,
            window,
            source_hash,
            artifact_hash,
            artifact_files,
            gates,
            clock,
        )
        safety.update(outcome.safety)
        extra_blockers.extend(outcome.blockers)
        if not (commit in policy.approved_commits and tree in policy.approved_trees):
            safety["source_provenance"] = "not_proven"
        else:
            safety["source_provenance"] = "verified"
    safety["trust_policy"] = policy_status
    return _finalize_report(
        run_id,
        source_hash,
        artifact_hash,
        _blocked_safety(safety),
        extra_blockers,
        "verify-evidence",
    )


def compute_seal_binding(
    run_dir: Path, payload: object, trust_policy_value: object
) -> dict[str, Any]:
    """Recompute the canonical evidence-seal binding the verifier derives for
    ``payload`` against the given trust-policy JSON object.

    This is review/test support: the adversarial forger and the test suite use
    it to produce seal-consistent bundles so that every other authenticity
    vector can be tested in isolation.  It never approves a policy, never
    verifies a signature and never returns an admission decision; the approval
    pin in :data:`_APPROVED_TRUST_POLICY_SHA256` still gates
    :func:`verify_joint_evidence`.  Structural violations raise
    :class:`ConfigurationError` exactly as the verifier would.
    """
    root = run_dir.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ConfigurationError("run directory must be a regular directory")
    data = _object(payload, "joint evidence")
    _keys(data, _TOP_LEVEL_KEYS, "joint evidence")
    run_id, object_format, commit, tree, window = _verify_run_envelope(data)
    source_hash, _source_files = _verify_manifest(
        root, data.get("source_manifest"), name="source_manifest"
    )
    artifact_hash, artifact_files = _verify_manifest(
        root, data.get("artifact_manifest"), name="artifact_manifest"
    )
    safety, gates = _verify_repository_invariants(data)
    policy = _parse_trust_policy(trust_policy_value)
    if object_format != policy.git_object_format:
        raise ConfigurationError(
            "trust policy git object format must match the evidence provenance "
            "(policy/evidence object-format drift fails closed)"
        )
    (
        _issues,
        receipt_digests,
        component_digests,
        posture_digest,
        attack_digest,
        cleanup_digest,
        _posture_safety,
        _attack_safety,
        _cleanup_safety,
        pre_seal_safety,
        _attack_blockers,
        _cleanup_blockers,
    ) = _derive_chain(
        root,
        data,
        policy,
        run_id,
        object_format,
        commit,
        tree,
        window,
        source_hash,
        artifact_hash,
        artifact_files,
        gates,
        _utc_now(),
    )
    return _seal_binding(
        data,
        run_id,
        object_format,
        commit,
        tree,
        window,
        source_hash,
        artifact_hash,
        receipt_digests,
        component_digests,
        posture_digest,
        attack_digest,
        cleanup_digest,
        gates,
        pre_seal_safety,
    )


def validate_joint_evidence(
    run_dir: Path, payload: object, trust_policy_path: Path | None = None
) -> JointGateReport:
    """Backwards-compatible entry point.

    Behaves like :func:`verify_joint_evidence` when a real run directory and
    bundle are supplied.  It never returns ``passed`` from inline assertions
    or unsigned evidence.
    """
    return verify_joint_evidence(run_dir, payload, trust_policy_path)


__all__ = [
    "JointGateReport",
    "TrustPolicy",
    "compute_seal_binding",
    "load_trust_policy",
    "validate_joint_evidence",
    "validate_joint_evidence_contract",
    "verify_joint_evidence",
]
