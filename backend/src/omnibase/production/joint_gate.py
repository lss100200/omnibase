"""Fail-closed immutable run-scoped evidence contract for P34.7 hardened joint gates.

This module implements the *evidence authenticity* boundary for the P34.7
production joint gate.  It deliberately never derives a production ``passed``
result from operator-authored inline assertions.  Instead every proof must be a
real, regular, non-link file inside an immutable run directory whose raw bytes
are hashed and cross-bound by sidecar manifests.

Two operating modes are exposed and must never be blurred:

* :func:`validate_joint_evidence_contract` parses the static schema and an
  operator-supplied bundle layout but never accepts inline evidence as direct
  execution proof.  It always returns ``blocked/not_proven`` because direct
  evidence was not executed.
* :func:`verify_joint_evidence` may return ``passed`` only when every mandatory
  real, sealed, component-specific artifact exists under ``run_dir`` and every
  cross-component identity, hash, chronology, semantics, attack result and
  cleanup check verifies against the actual file bytes.

The verifier is offline: it never starts a service, opens a network connection,
reads the root ``.env``, accesses a database, executes code or activates the
production Runtime.  It fails closed on unknown fields, missing files,
symlinks/reparse points, duplicate IDs, path traversal, absolute escape,
mutable references, hash/size mismatch, schema/version mismatch, temporal
inconsistency, command-order inconsistency, identity mismatch, certificate
stale/revoked/replayed posture or cleanup uncertainty.

Safety claims (root ``.env`` not accessed, business database not accessed,
business database not migrated, production Runtime inactive, hostile code not
executed, cleanup residue) are never hardcoded into a ``passed`` result: they
are only reported as ``not_proven`` unless an approved sealed measurement proves
them, in which case ``not_proven`` blocks ``passed``.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from omnibase.production.composition import ConfigurationError

_SCHEMA = "omnibase.p34-7.hardened-joint-evidence.v2"
_SCHEMA_VERSIONS = frozenset({"1", "2"})
_HEX = set("0123456789abcdef")
_REQUIRED_COMMANDS = (
    "core_runner",
    "runner_broker",
    "runner_gateway",
    "broker_gateway",
    "overlay_data_plane",
    "recovery_sla",
)
_REQUIRED_COMPONENTS = (
    "core",
    "runner",
    "broker",
    "gateway",
    "overlay",
    "recovery_sla",
)
_REQUIRED_FEATURE_GATES = (
    "agent_runtime_enabled",
    "agent_planner_enabled",
    "multi_agent_enabled",
)
_REQUIRED_CLEANUP_KEYS = ("containers", "networks", "processes", "volumes")
_ROOT_ENV_NAMES = frozenset({".env", "./.env"})


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
    """Resolve ``relative`` under ``root`` refusing links, reparse points, escape."""
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


def _canonical(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _iso_timestamp(value: object, name: str) -> str:
    text = _string(value, name)
    if not text.endswith(("Z", "+00:00")) and "+" not in text[10:] and "-" not in text[10:]:
        # require an explicit UTC marker or offset; bare local time is ambiguous
        raise ConfigurationError(f"{name} must carry an explicit UTC offset")
    if "T" not in text:
        raise ConfigurationError(f"{name} must be an ISO-8601 UTC timestamp")
    return text


# ---------------------------------------------------------------------------
# Report contract
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JointGateReport:
    """Outcome of a P34.7 joint-evidence check.

    ``status`` is one of ``passed`` (only from :func:`verify_joint_evidence` with
    a fully verified real evidence chain), ``blocked/not_proven`` (the only
    correct state when direct evidence is absent or incomplete) or
    ``invalid/veto`` (raised as :class:`ConfigurationError` by the validators).
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


# ---------------------------------------------------------------------------
# Manifest verification (source/artifact raw-byte SHA-256 binding)
# ---------------------------------------------------------------------------


def _verify_manifest(root: Path, value: object, *, name: str) -> str:
    manifest = _object(value, name)
    _keys(manifest, {"raw_sha256", "files"}, name)
    raw = _sha256(manifest.get("raw_sha256"), f"{name}.raw_sha256")
    files = _list(manifest.get("files"), f"{name}.files")
    if not files:
        raise ConfigurationError(f"{name}.files must be non-empty")
    seen: set[str] = set()
    for index, item in enumerate(files):
        entry = _object(item, f"{name}.files[{index}]")
        _keys(entry, {"path", "size", "sha256"}, f"{name}.files[{index}]")
        relative = _relative_path(entry.get("path"), f"{name}.files[{index}].path")
        if relative in seen:
            raise ConfigurationError(f"{name} contains duplicate file paths")
        seen.add(relative)
        size = _int(entry.get("size"), f"{name}.files[{index}].size")
        if size < 0:
            raise ConfigurationError(f"{name}.files[{index}].size is invalid")
        digest = _sha256(entry.get("sha256"), f"{name}.files[{index}].sha256")
        path = _real_file(root, relative, f"{name}.files[{index}]")
        if path.stat().st_size != size:
            raise ConfigurationError(f"{name}.files[{index}] size drifted")
        if _hash_file(path) != digest:
            raise ConfigurationError(f"{name}.files[{index}] raw hash drifted")
    canonical = _canonical(files)
    if hashlib.sha256(canonical).hexdigest() != raw:
        raise ConfigurationError(f"{name}.raw_sha256 does not bind its files")
    return raw


# ---------------------------------------------------------------------------
# Run-envelope verification (run id, schema version, source/tree provenance)
# ---------------------------------------------------------------------------


def _verify_run_envelope(data: dict[str, Any]) -> tuple[str, str, str]:
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
        {"source_commit", "source_tree", "dirty", "repository"},
        "provenance",
    )
    commit = _sha256(provenance.get("source_commit"), "provenance.source_commit")
    tree = _sha256(provenance.get("source_tree"), "provenance.source_tree")
    if _bool(provenance.get("dirty"), "provenance.dirty") is not False:
        raise ConfigurationError("production evidence requires a clean checkout")
    repository = _string(provenance.get("repository"), "provenance.repository")
    if not repository.startswith(("https://github.com/", "git@github.com:")):
        raise ConfigurationError("provenance.repository must be an explicit GitHub remote")
    return run_id, commit, tree


# ---------------------------------------------------------------------------
# Command-record verification (executable digest, argv, ordering, exit code)
# ---------------------------------------------------------------------------


def _verify_command_record(
    root: Path, name: str, value: object, *, previous_end: str | None
) -> tuple[str, str]:
    record = _object(value, f"commands.{name}")
    _keys(
        record,
        {
            "order",
            "executable_digest",
            "executable_path",
            "argv",
            "working_directory",
            "env_manifest",
            "started_at",
            "ended_at",
            "timeout_seconds",
            "exit_code",
            "stdout",
            "stderr",
        },
        f"commands.{name}",
    )
    executable = _relative_path(record.get("executable_path"), f"commands.{name}.executable_path")
    exec_digest = _sha256(record.get("executable_digest"), f"commands.{name}.executable_digest")
    exec_path = _real_file(root, executable, f"commands.{name}.executable_path")
    if _hash_file(exec_path) != exec_digest:
        raise ConfigurationError(f"commands.{name}.executable digest drifted")
    argv = _list(record.get("argv"), f"commands.{name}.argv")
    if not argv or not all(isinstance(a, str) and a for a in argv):
        raise ConfigurationError(f"commands.{name}.argv must be non-empty strings")
    cwd = _string(record.get("working_directory"), f"commands.{name}.working_directory")
    if cwd not in {"/workspace", "/run/omnibase"} and not cwd.startswith("/run/omnibase/"):
        raise ConfigurationError(f"commands.{name}.working_directory must be the approved run root")
    env_manifest = _object(record.get("env_manifest"), f"commands.{name}.env_manifest")
    _keys(env_manifest, {"names", "secret_free"}, f"commands.{name}.env_manifest")
    names = _list(env_manifest.get("names"), f"commands.{name}.env_manifest.names")
    if not all(isinstance(n, str) and n for n in names):
        raise ConfigurationError(f"commands.{name}.env_manifest.names must be strings")
    forbidden_env = {
        ".env",
        "JWT_SECRET",
        "LLM_API_KEY",
        "POSTGRES_PASSWORD",
        "MINIO_ROOT_PASSWORD",
    }
    if any(n in forbidden_env for n in names):
        raise ConfigurationError(f"commands.{name}.env_manifest must not include secret names")
    if (
        _bool(env_manifest.get("secret_free"), f"commands.{name}.env_manifest.secret_free")
        is not True
    ):
        raise ConfigurationError(f"commands.{name}.env_manifest must assert secret_free")
    started = _iso_timestamp(record.get("started_at"), f"commands.{name}.started_at")
    ended = _iso_timestamp(record.get("ended_at"), f"commands.{name}.ended_at")
    if ended < started:
        raise ConfigurationError(f"commands.{name} ended before it started")
    if previous_end is not None and started < previous_end:
        raise ConfigurationError("command chronology is inconsistent with the required order")
    timeout = _int(record.get("timeout_seconds"), f"commands.{name}.timeout_seconds")
    if timeout <= 0 or timeout > 3600:
        raise ConfigurationError(f"commands.{name}.timeout_seconds must be bounded")
    exit_code = _int(record.get("exit_code"), f"commands.{name}.exit_code")
    stdout = _object(record.get("stdout"), f"commands.{name}.stdout")
    stderr = _object(record.get("stderr"), f"commands.{name}.stderr")
    exit_ok, stdout_size, stderr_size = _verify_command_output(
        root, name, stdout, stderr, exit_code
    )
    if not exit_ok:
        raise ConfigurationError(f"commands.{name}.exit_code must be 0 for passed evidence")
    return ended, f"{name}:{exec_digest}:{stdout_size}:{stderr_size}"


def _verify_command_output(
    root: Path, name: str, stdout: dict[str, Any], stderr: dict[str, Any], exit_code: int
) -> tuple[bool, int, int]:
    for stream_name, stream in (("stdout", stdout), ("stderr", stderr)):
        _keys(stream, {"path", "size", "sha256"}, f"commands.{name}.{stream_name}")
        relative = _relative_path(stream.get("path"), f"commands.{name}.{stream_name}.path")
        size = _int(stream.get("size"), f"commands.{name}.{stream_name}.size")
        digest = _sha256(stream.get("sha256"), f"commands.{name}.{stream_name}.sha256")
        path = _real_file(root, relative, f"commands.{name}.{stream_name}.path")
        if path.stat().st_size != size or _hash_file(path) != digest:
            raise ConfigurationError(f"commands.{name}.{stream_name} raw bytes drifted")
    return exit_code == 0, stdout["size"], stderr["size"]


def _verify_commands(root: Path, value: object) -> list[str]:
    commands = _object(value, "commands")
    if set(commands) != set(_REQUIRED_COMMANDS):
        raise ConfigurationError("commands must contain every required joint boundary exactly once")
    ordered: list[str] = []
    previous_end: str | None = None
    for index, name in enumerate(_REQUIRED_COMMANDS):
        record = commands.get(name)
        if not isinstance(record, dict):
            raise ConfigurationError(f"commands.{name} must be an object")
        order = _int(record.get("order"), f"commands.{name}.order") if "order" in record else index
        if order != index:
            raise ConfigurationError("command order must match the required sequence exactly")
        previous_end, marker = _verify_command_record(root, name, record, previous_end=previous_end)
        ordered.append(marker)
    return ordered


# ---------------------------------------------------------------------------
# Component-evidence verification (frozen per-component schema, identity binding)
# ---------------------------------------------------------------------------


def _verify_component(root: Path, name: str, value: object, run_id: str) -> str:
    record = _object(value, f"components.{name}")
    allowed = {
        "schema",
        "producer",
        "component_run_id",
        "identity",
        "trust_roots",
        "fingerprint",
        "evidence",
        "host",
    }
    if name == "gateway":
        allowed |= {"certificate", "replay"}
    _keys(record, allowed, f"components.{name}")
    if _string(record.get("schema"), f"components.{name}.schema") != f"omnibase.p34-7.{name}.v1":
        raise ConfigurationError(
            f"components.{name}.schema must be the frozen per-component schema"
        )
    if _string(record.get("producer"), f"components.{name}.producer") != name:
        raise ConfigurationError(f"components.{name}.producer must equal the component name")
    if _string(record.get("component_run_id"), f"components.{name}.component_run_id") != run_id:
        raise ConfigurationError(f"components.{name} bound to a different run_id")
    identity = _object(record.get("identity"), f"components.{name}.identity")
    _keys(identity, {"kind", "value"}, f"components.{name}.identity")
    if _string(identity.get("kind"), f"components.{name}.identity.kind") != "sha256":
        raise ConfigurationError(f"components.{name}.identity must be a sha256 digest")
    _sha256(identity.get("value"), f"components.{name}.identity.value")
    trust_roots = _list(record.get("trust_roots"), f"components.{name}.trust_roots")
    for index, root_ref in enumerate(trust_roots):
        _sha256(root_ref, f"components.{name}.trust_roots[{index}]")
    if name == "gateway":
        _verify_certificate_posture(record, name)
    fingerprint = _opt_string(record.get("fingerprint"), f"components.{name}.fingerprint")
    if fingerprint is not None:
        _sha256(fingerprint, f"components.{name}.fingerprint")
    evidence = _verify_component_evidence(root, name, record.get("evidence"))
    host = _object(record.get("host"), f"components.{name}.host")
    _keys(host, {"os", "kernel", "arch"}, f"components.{name}.host")
    _string(host.get("os"), f"components.{name}.host.os")
    _string(host.get("kernel"), f"components.{name}.host.kernel")
    _string(host.get("arch"), f"components.{name}.host.arch")
    return evidence


def _verify_certificate_posture(record: dict[str, Any], name: str) -> None:
    cert = _object(record.get("certificate"), f"components.{name}.certificate")
    _keys(
        cert,
        {"public_fingerprint", "issuer", "san", "valid_from", "valid_until", "revoked"},
        f"components.{name}.certificate",
    )
    _sha256(cert.get("public_fingerprint"), f"components.{name}.certificate.public_fingerprint")
    _sha256(cert.get("issuer"), f"components.{name}.certificate.issuer")
    _string(cert.get("san"), f"components.{name}.certificate.san")
    valid_from = _iso_timestamp(cert.get("valid_from"), f"components.{name}.certificate.valid_from")
    valid_until = _iso_timestamp(
        cert.get("valid_until"), f"components.{name}.certificate.valid_until"
    )
    if valid_until <= valid_from:
        raise ConfigurationError(f"components.{name}.certificate validity window is empty")
    if _bool(cert.get("revoked"), f"components.{name}.certificate.revoked") is not False:
        raise ConfigurationError(f"components.{name}.certificate must not be revoked")
    replay = _object(record.get("replay"), f"components.{name}.replay")
    _keys(replay, {"replayed", "sequence"}, f"components.{name}.replay")
    if _bool(replay.get("replayed"), f"components.{name}.replay.replayed") is not False:
        raise ConfigurationError(f"components.{name}.replay must not be replayed")


def _verify_component_evidence(root: Path, name: str, value: object) -> str:
    evidence = _object(value, f"components.{name}.evidence")
    _keys(evidence, {"path", "size", "sha256"}, f"components.{name}.evidence")
    relative = _relative_path(evidence.get("path"), f"components.{name}.evidence.path")
    size = _int(evidence.get("size"), f"components.{name}.evidence.size")
    digest = _sha256(evidence.get("sha256"), f"components.{name}.evidence.sha256")
    path = _real_file(root, relative, f"components.{name}.evidence.path")
    if path.stat().st_size != size or _hash_file(path) != digest:
        raise ConfigurationError(f"components.{name}.evidence raw bytes drifted")
    return digest


def _verify_components(root: Path, value: object, run_id: str) -> list[str]:
    components = _object(value, "components")
    if set(components) != set(_REQUIRED_COMPONENTS):
        raise ConfigurationError("components must contain all six joint gates exactly once")
    return [
        _verify_component(root, name, components.get(name), run_id) for name in _REQUIRED_COMPONENTS
    ]


# ---------------------------------------------------------------------------
# Repository-invariant verification (migration head, feature gates, posture)
# ---------------------------------------------------------------------------


def _verify_repository_invariants(data: dict[str, Any]) -> dict[str, str]:
    safety: dict[str, str] = {}
    migration_head = _string(data.get("migration_head"), "migration_head")
    if migration_head != "0012":
        raise ConfigurationError("migration head must remain 0012")
    safety["migration_head"] = migration_head
    gates = _object(data.get("feature_gates"), "feature_gates")
    if set(gates) != set(_REQUIRED_FEATURE_GATES):
        raise ConfigurationError("feature_gates must contain exactly the three Phase 5 gates")
    for gate_name in _REQUIRED_FEATURE_GATES:
        if _bool(gates.get(gate_name), f"feature_gates.{gate_name}") is not False:
            raise ConfigurationError("Phase 5 feature gates must remain false")
        safety[gate_name] = "false"
    posture = _object(data.get("runtime_posture"), "runtime_posture")
    _keys(
        posture,
        {
            "production_runtime_activated",
            "hostile_code_executed",
            "measured",
            "measurement_source",
        },
        "runtime_posture",
    )
    for key in ("production_runtime_activated", "hostile_code_executed"):
        if _bool(posture.get(key), f"runtime_posture.{key}") is not False:
            raise ConfigurationError("production Runtime and hostile code must remain inactive")
        safety[key] = "false"
    if _bool(posture.get("measured"), "runtime_posture.measured") is not True:
        safety["runtime_posture"] = "not_proven"
    else:
        source = _string(posture.get("measurement_source"), "runtime_posture.measurement_source")
        if source not in {"process_config", "service_config"}:
            raise ConfigurationError("runtime_posture.measurement_source must be an approved kind")
        safety["runtime_posture"] = f"measured:{source}"
    return safety


# ---------------------------------------------------------------------------
# Attack matrix and cleanup verification
# ---------------------------------------------------------------------------


def _verify_attack_matrix(root: Path, value: object) -> tuple[bool, list[str]]:
    attack = _object(value, "attack_matrix")
    _keys(attack, {"status", "results", "evidence"}, "attack_matrix")
    status = _string(attack.get("status"), "attack_matrix.status")
    results = _object(attack.get("results"), "attack_matrix.results")
    required_attacks = (
        "node_compromise",
        "credential_theft",
        "revocation_replay",
        "derp_failover",
        "cross_component_replay",
    )
    for attack_name in required_attacks:
        outcome = _string(results.get(attack_name), f"attack_matrix.results.{attack_name}")
        if outcome not in {"rejected", "contained", "failed_attack"}:
            return False, [f"attack:{attack_name}"]
    evidence = _object(attack.get("evidence"), "attack_matrix.evidence")
    _keys(evidence, {"path", "size", "sha256"}, "attack_matrix.evidence")
    relative = _relative_path(evidence.get("path"), "attack_matrix.evidence.path")
    size = _int(evidence.get("size"), "attack_matrix.evidence.size")
    digest = _sha256(evidence.get("sha256"), "attack_matrix.evidence.sha256")
    path = _real_file(root, relative, "attack_matrix.evidence.path")
    if path.stat().st_size != size or _hash_file(path) != digest:
        raise ConfigurationError("attack_matrix.evidence raw bytes drifted")
    if status != "passed":
        return False, ["attack_matrix.status"]
    return True, []


def _verify_cleanup(root: Path, value: object) -> tuple[bool, list[str]]:
    cleanup = _object(value, "cleanup")
    _keys(
        cleanup,
        {
            "containers",
            "networks",
            "processes",
            "volumes",
            "databases",
            "test_identities",
            "evidence",
        },
        "cleanup",
    )
    for key in (*_REQUIRED_CLEANUP_KEYS, "databases", "test_identities"):
        count = _int(cleanup.get(key), f"cleanup.{key}")
        if count != 0:
            return False, [f"cleanup:{key}"]
    evidence = _object(cleanup.get("evidence"), "cleanup.evidence")
    _keys(evidence, {"path", "size", "sha256"}, "cleanup.evidence")
    relative = _relative_path(evidence.get("path"), "cleanup.evidence.path")
    size = _int(evidence.get("size"), "cleanup.evidence.size")
    digest = _sha256(evidence.get("sha256"), "cleanup.evidence.sha256")
    path = _real_file(root, relative, "cleanup.evidence.path")
    if path.stat().st_size != size or _hash_file(path) != digest:
        raise ConfigurationError("cleanup.evidence raw bytes drifted")
    return True, []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_joint_evidence_contract(payload: object) -> JointGateReport:
    """Validate the static P34.7 joint-evidence schema and contract only.

    This mode never accepts inline evidence as direct execution proof and
    therefore always returns ``blocked/not_proven``.  It is the safe operating
    mode when no real run directory exists.
    """
    data = _object(payload, "joint evidence contract")
    _keys(
        data,
        {
            "schema",
            "schema_version",
            "run_id",
            "environment",
            "disposable",
            "provenance",
            "source_manifest",
            "artifact_manifest",
            "commands",
            "components",
            "migration_head",
            "feature_gates",
            "runtime_posture",
            "attack_matrix",
            "cleanup",
            "evidence_seal",
        },
        "joint evidence contract",
    )
    run_id, _commit, _tree = _verify_run_envelope(data)
    return JointGateReport(
        status="blocked/not_proven",
        run_id=run_id,
        schema=_SCHEMA,
        source_manifest_sha256="not_proven",
        artifact_manifest_sha256="not_proven",
        blockers=("contract_mode_no_direct_evidence",),
        mode="validate-only",
    )


def verify_joint_evidence(run_dir: Path, payload: object) -> JointGateReport:
    """Verify one immutable, run-scoped P34.7 evidence bundle.

    May return ``passed`` only when every mandatory real, sealed,
    component-specific artifact exists under ``run_dir`` and all cross-component
    identities, hashes, chronology, semantics, attack results and cleanup
    checks verify against the actual file bytes.  Any forgery vector raises
    :class:`ConfigurationError` (treated as ``invalid/veto`` by callers).
    """
    root = run_dir.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise ConfigurationError("run directory must be a regular directory")
    data = _object(payload, "joint evidence")
    _keys(
        data,
        {
            "schema",
            "schema_version",
            "run_id",
            "environment",
            "disposable",
            "provenance",
            "source_manifest",
            "artifact_manifest",
            "commands",
            "components",
            "migration_head",
            "feature_gates",
            "runtime_posture",
            "attack_matrix",
            "cleanup",
            "evidence_seal",
        },
        "joint evidence",
    )
    run_id, commit, tree = _verify_run_envelope(data)
    source_hash = _verify_manifest(root, data.get("source_manifest"), name="source_manifest")
    artifact_hash = _verify_manifest(root, data.get("artifact_manifest"), name="artifact_manifest")
    _verify_commands(root, data.get("commands"))
    _verify_components(root, data.get("components"), run_id)
    safety = _verify_repository_invariants(data)
    attack_ok, attack_blockers = _verify_attack_matrix(root, data.get("attack_matrix"))
    cleanup_ok, cleanup_blockers = _verify_cleanup(root, data.get("cleanup"))
    seal = _object(data.get("evidence_seal"), "evidence_seal")
    _keys(seal, {"status", "run_id", "source_commit", "source_tree"}, "evidence_seal")
    if _string(seal.get("status"), "evidence_seal.status") != "passed":
        raise ConfigurationError("evidence_seal.status must be passed for a verified bundle")
    if seal.get("run_id") != run_id:
        raise ConfigurationError("evidence_seal.run_id must match the envelope run_id")
    if seal.get("source_commit") != commit or seal.get("source_tree") != tree:
        raise ConfigurationError("evidence_seal provenance must match the envelope provenance")
    blockers: list[str] = []
    if not attack_ok:
        blockers.extend(attack_blockers)
    if not cleanup_ok:
        blockers.extend(cleanup_blockers)
    status = "blocked/not_proven" if blockers else "passed"
    return JointGateReport(
        status=status,
        run_id=run_id,
        schema=_SCHEMA,
        source_manifest_sha256=source_hash,
        artifact_manifest_sha256=artifact_hash,
        blockers=tuple(sorted(blockers)),
        mode="verify-evidence",
        safety=safety,
    )


def validate_joint_evidence(run_dir: Path, payload: object) -> JointGateReport:
    """Backwards-compatible entry point.

    Behaves like :func:`verify_joint_evidence` when a real run directory and
    bundle are supplied.  It never returns ``passed`` from inline assertions:
    every proof must be a real hashed file under ``run_dir``.
    """
    return verify_joint_evidence(run_dir, payload)


__all__ = [
    "JointGateReport",
    "validate_joint_evidence",
    "validate_joint_evidence_contract",
    "verify_joint_evidence",
]
