"""Fail-closed P5.0 Phase 5 admission gate.

P5.0 decides whether Phase 5 engineering may begin.  It never starts an
Agent, Planner, Executor, queue, worker or scheduler, never opens a network
route, never reads the root ``.env`` and never touches a database or
migration.  The three Phase 5 feature gates are independent, server-owned and
disabled by default; missing, empty, unknown or contradictory values fail
closed.  Even when all three gates are explicitly true, P5.0 stays blocked
while the P34.7 Evidence Manifest is not ready.

A future deployment controller may consume a ``ready`` report, but no
component in this module turns that report into authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

from omnibase.production.composition import (
    AdmissionState,
    ConfigurationError,
    EvidenceReference,
    EvidenceStatus,
    GitSourceProvenance,
    SourceScope,
    build_git_source_provenance,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVISION_LINE = re.compile(
    r'^\s*revision\s*(?::\s*str(?:\s*\|\s*None)?)?\s*=\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)
_DOWN_REVISION_LINE = re.compile(
    r"^\s*down_revision\s*(?::\s*(?:str\s*\|\s*None|str|None))?\s*="
    r'(?:\s*["\']([^"\']+)["\']|\s*None)',
    re.MULTILINE,
)
_PYPROJECT_VERSION_LINE = re.compile(r'^\s*version\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
_VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]*$")

_ROOT_ENV_NAMES = {".env", "./.env"}


class FeatureGateConfigurationError(ConfigurationError):
    """A Phase 5 feature gate value is unknown, contradictory or non-string."""


class FeatureGateName(StrEnum):
    AGENT_RUNTIME = "agent_runtime"
    AGENT_PLANNER = "agent_planner"
    MULTI_AGENT = "multi_agent"


FEATURE_GATE_ENV_NAMES: dict[FeatureGateName, str] = {
    FeatureGateName.AGENT_RUNTIME: "AGENT_RUNTIME_ENABLED",
    FeatureGateName.AGENT_PLANNER: "AGENT_PLANNER_ENABLED",
    FeatureGateName.MULTI_AGENT: "MULTI_AGENT_ENABLED",
}

_TRUE = "true"
_FALSE = "false"


class P347FormalState(StrEnum):
    READY = "ready"
    BLOCKED = "blocked/not_proven"
    INVALID = "invalid/veto"


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _strict_object(value: object, *, name: str) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ConfigurationError(f"{name} must be an object")
    return value


def _strict_list(value: object, *, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ConfigurationError(f"{name} must be an array")
    return value


def _strict_string(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ConfigurationError(f"{name} must be a non-empty string")
    return value


def _strict_bool(value: object, *, name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigurationError(f"{name} must be a boolean")
    return value


def _only_keys(value: dict[str, object], allowed: set[str], *, name: str) -> None:
    unexpected = sorted(set(value) - allowed)
    if unexpected:
        raise ConfigurationError(f"{name} has unexpected fields: {', '.join(unexpected)}")


def _relative_repo_path(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name).replace("\\", "/")
    path = PurePosixPath(text)
    if (
        path.is_absolute()
        or ":" in path.parts[0]
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ConfigurationError(f"{name} must be a normalized repository-relative path")
    if text.lower() in _ROOT_ENV_NAMES:
        raise ConfigurationError(f"{name} must never reference the root .env")
    return path.as_posix()


def _safe_repo_file(repo_root: Path, relative_path: str) -> Path:
    candidate = (repo_root / relative_path).resolve(strict=True)
    try:
        candidate.relative_to(repo_root.resolve(strict=True))
    except ValueError as exc:
        raise ConfigurationError("manifest path escaped the repository") from exc
    metadata = os.lstat(candidate)
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError(f"manifest file must be a regular non-link file: {relative_path}")
    return candidate


def _safe_repo_dir(repo_root: Path, relative_path: str) -> Path:
    candidate = (repo_root / relative_path).resolve(strict=True)
    try:
        candidate.relative_to(repo_root.resolve(strict=True))
    except ValueError as exc:
        raise ConfigurationError("manifest directory escaped the repository") from exc
    metadata = os.lstat(candidate)
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISDIR(metadata.st_mode):
        raise ConfigurationError(f"manifest directory must be a regular directory: {relative_path}")
    return candidate


def parse_feature_gate(raw: object, *, gate: FeatureGateName) -> bool:
    """Parse one Phase 5 feature gate with fail-closed semantics.

    ``None`` (missing) and ``""`` (empty) resolve to ``False``.  Only the exact
    strings ``"true"`` and ``"false"`` are accepted; case, whitespace and other
    truthy-looking values raise instead of being guessed.
    """
    if raw is None:
        return False
    if not isinstance(raw, str):
        raise FeatureGateConfigurationError(f"{gate.value} gate value must be a string or absent")
    if raw == "":
        return False
    if raw == _TRUE:
        return True
    if raw == _FALSE:
        return False
    raise FeatureGateConfigurationError(
        f"{gate.value} gate has an unsupported value; only 'true' or 'false' are accepted"
    )


@dataclass(frozen=True, slots=True)
class FeatureGateResolution:
    agent_runtime_enabled: bool
    agent_planner_enabled: bool
    multi_agent_enabled: bool

    @classmethod
    def from_mapping(cls, value: object) -> FeatureGateResolution:
        data = _strict_object(value, name="feature_gates")
        _only_keys(
            data,
            {"agent_runtime_enabled", "agent_planner_enabled", "multi_agent_enabled"},
            name="feature_gates",
        )
        return cls(
            agent_runtime_enabled=_strict_bool(
                data.get("agent_runtime_enabled"),
                name="feature_gates.agent_runtime_enabled",
            ),
            agent_planner_enabled=_strict_bool(
                data.get("agent_planner_enabled"),
                name="feature_gates.agent_planner_enabled",
            ),
            multi_agent_enabled=_strict_bool(
                data.get("multi_agent_enabled"),
                name="feature_gates.multi_agent_enabled",
            ),
        )

    @property
    def any_enabled(self) -> bool:
        return self.agent_runtime_enabled or self.agent_planner_enabled or self.multi_agent_enabled

    def to_dict(self) -> dict[str, bool]:
        return {
            "agent_runtime_enabled": self.agent_runtime_enabled,
            "agent_planner_enabled": self.agent_planner_enabled,
            "multi_agent_enabled": self.multi_agent_enabled,
        }


def resolve_feature_gates(values: Mapping[str, object]) -> FeatureGateResolution:
    """Resolve the three independent gates from an explicit server-owned mapping.

    ``values`` is keyed by the documented environment names
    (``AGENT_RUNTIME_ENABLED``, ``AGENT_PLANNER_ENABLED``, ``MULTI_AGENT_ENABLED``).
    The gates are independent: no master switch can implicitly open another.
    Dependency rules fail closed: Planner requires Runtime, and Multi-Agent
    requires both Runtime and Planner.
    """
    resolved: dict[FeatureGateName, bool] = {}
    for gate in FeatureGateName:
        env_name = FEATURE_GATE_ENV_NAMES[gate]
        resolved[gate] = parse_feature_gate(values.get(env_name), gate=gate)
    runtime = resolved[FeatureGateName.AGENT_RUNTIME]
    planner = resolved[FeatureGateName.AGENT_PLANNER]
    multi_agent = resolved[FeatureGateName.MULTI_AGENT]
    if planner and not runtime:
        raise FeatureGateConfigurationError(
            "AGENT_PLANNER_ENABLED=true requires AGENT_RUNTIME_ENABLED=true"
        )
    if multi_agent and (not planner or not runtime):
        raise FeatureGateConfigurationError(
            "MULTI_AGENT_ENABLED=true requires both AGENT_RUNTIME_ENABLED and "
            "AGENT_PLANNER_ENABLED to be true"
        )
    return FeatureGateResolution(
        agent_runtime_enabled=runtime,
        agent_planner_enabled=planner,
        multi_agent_enabled=multi_agent,
    )


@dataclass(frozen=True, slots=True)
class SealedFileRef:
    path: str
    sha256: str

    @classmethod
    def from_mapping(cls, value: object, *, name: str) -> SealedFileRef:
        data = _strict_object(value, name=name)
        _only_keys(data, {"path", "sha256"}, name=name)
        path = _relative_repo_path(data.get("path"), name=f"{name}.path")
        digest = _strict_string(data.get("sha256"), name=f"{name}.sha256").lower()
        if _SHA256_RE.fullmatch(digest) is None:
            raise ConfigurationError(f"{name}.sha256 must be a 64-character hex SHA-256")
        return cls(path=path, sha256=digest)


@dataclass(frozen=True, slots=True)
class SdkContractRef:
    path: str
    version: str

    @classmethod
    def from_mapping(cls, value: object, *, name: str) -> SdkContractRef:
        data = _strict_object(value, name=name)
        _only_keys(data, {"path", "version"}, name=name)
        path = _relative_repo_path(data.get("path"), name=f"{name}.path")
        version = _strict_string(data.get("version"), name=f"{name}.version")
        if _VERSION_PATTERN.fullmatch(version) is None:
            raise ConfigurationError(f"{name}.version must be a plain version string")
        return cls(path=path, version=version)


@dataclass(frozen=True, slots=True)
class P347ManifestRef:
    formal_state: P347FormalState
    decision: SealedFileRef

    @classmethod
    def from_mapping(cls, value: object) -> P347ManifestRef:
        data = _strict_object(value, name="p34_7")
        _only_keys(data, {"formal_state", "decision"}, name="p34_7")
        raw_state = _strict_string(data.get("formal_state"), name="p34_7.formal_state")
        try:
            state = P347FormalState(raw_state)
        except ValueError as exc:
            raise ConfigurationError("p34_7.formal_state has an invalid state") from exc
        return cls(
            formal_state=state,
            decision=SealedFileRef.from_mapping(data.get("decision"), name="p34_7.decision"),
        )


@dataclass(frozen=True, slots=True)
class MigrationHeadContract:
    directory: str
    expected_revision: str

    @classmethod
    def from_mapping(cls, value: object) -> MigrationHeadContract:
        data = _strict_object(value, name="migration_head")
        _only_keys(data, {"directory", "expected_revision"}, name="migration_head")
        directory = _relative_repo_path(data.get("directory"), name="migration_head.directory")
        revision = _strict_string(
            data.get("expected_revision"), name="migration_head.expected_revision"
        )
        if not re.fullmatch(r"[0-9a-zA-Z_-]+", revision):
            raise ConfigurationError(
                "migration_head.expected_revision must be a plain Alembic revision id"
            )
        return cls(directory=directory, expected_revision=revision)


@dataclass(frozen=True, slots=True)
class RunbookRef:
    path: str
    version: str
    sha256: str

    @classmethod
    def from_mapping(cls, value: object) -> RunbookRef:
        data = _strict_object(value, name="runbook")
        _only_keys(data, {"path", "version", "sha256"}, name="runbook")
        path = _relative_repo_path(data.get("path"), name="runbook.path")
        version = _strict_string(data.get("version"), name="runbook.version")
        if _VERSION_PATTERN.fullmatch(version) is None:
            raise ConfigurationError("runbook.version must be a plain version string")
        digest = _strict_string(data.get("sha256"), name="runbook.sha256").lower()
        if _SHA256_RE.fullmatch(digest) is None:
            raise ConfigurationError("runbook.sha256 must be a 64-character hex SHA-256")
        return cls(path=path, version=version, sha256=digest)


@dataclass(frozen=True, slots=True)
class CriticalVetoRequirement:
    expected: int

    @classmethod
    def from_mapping(cls, value: object) -> CriticalVetoRequirement:
        data = _strict_object(value, name="critical_veto")
        _only_keys(data, {"expected"}, name="critical_veto")
        expected = data.get("expected")
        if not isinstance(expected, int) or isinstance(expected, bool) or expected != 0:
            raise ConfigurationError("critical_veto.expected must be exactly 0")
        return cls(expected=0)


@dataclass(frozen=True, slots=True)
class Phase5AdmissionConfig:
    schema_version: int
    phase: str
    activation_requested: bool
    feature_gates: FeatureGateResolution
    p34_7: P347ManifestRef
    source: SourceScope
    evidence: tuple[EvidenceReference, ...]
    migration_head: MigrationHeadContract
    openapi_snapshot: SealedFileRef
    sdk_contracts: tuple[tuple[str, SdkContractRef], ...]
    production_composition: SealedFileRef
    runbook: RunbookRef
    critical_veto: CriticalVetoRequirement

    @classmethod
    def from_mapping(cls, value: object) -> Phase5AdmissionConfig:
        data = _strict_object(value, name="configuration")
        _only_keys(
            data,
            {
                "schema_version",
                "phase",
                "activation_requested",
                "feature_gates",
                "p34_7",
                "source",
                "evidence",
                "migration_head",
                "openapi_snapshot",
                "sdk_contracts",
                "production_composition",
                "runbook",
                "critical_veto",
            },
            name="configuration",
        )
        if data.get("schema_version") != 1:
            raise ConfigurationError("configuration.schema_version must be 1")
        if data.get("phase") != "P5.0":
            raise ConfigurationError("configuration.phase must be P5.0")
        feature_gates = FeatureGateResolution.from_mapping(data.get("feature_gates"))
        if feature_gates.any_enabled:
            raise ConfigurationError(
                "P5.0 admission contract requires every Phase 5 feature gate to be disabled"
            )
        sdk_data = _strict_object(data.get("sdk_contracts"), name="sdk_contracts")
        if set(sdk_data) != {"python", "typescript"}:
            raise ConfigurationError("sdk_contracts must contain exactly python and typescript")
        sdk_contracts = tuple(
            (
                name,
                SdkContractRef.from_mapping(sdk_data[name], name=f"sdk_contracts.{name}"),
            )
            for name in ("python", "typescript")
        )
        evidence = tuple(
            EvidenceReference.from_mapping(item)
            for item in _strict_list(data.get("evidence"), name="evidence")
        )
        if not evidence or len({item.evidence_id for item in evidence}) != len(evidence):
            raise ConfigurationError("evidence IDs must be non-empty and unique")
        return cls(
            schema_version=1,
            phase="P5.0",
            activation_requested=_strict_bool(
                data.get("activation_requested"), name="activation_requested"
            ),
            feature_gates=feature_gates,
            p34_7=P347ManifestRef.from_mapping(data.get("p34_7")),
            source=SourceScope.from_mapping(data.get("source")),
            evidence=evidence,
            migration_head=MigrationHeadContract.from_mapping(data.get("migration_head")),
            openapi_snapshot=SealedFileRef.from_mapping(
                data.get("openapi_snapshot"), name="openapi_snapshot"
            ),
            sdk_contracts=sdk_contracts,
            production_composition=SealedFileRef.from_mapping(
                data.get("production_composition"), name="production_composition"
            ),
            runbook=RunbookRef.from_mapping(data.get("runbook")),
            critical_veto=CriticalVetoRequirement.from_mapping(data.get("critical_veto")),
        )

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "activation_requested": self.activation_requested,
            "feature_gates": self.feature_gates.to_dict(),
            "p34_7": {
                "formal_state": self.p34_7.formal_state.value,
                "decision": {
                    "path": self.p34_7.decision.path,
                    "sha256": self.p34_7.decision.sha256,
                },
            },
            "source": {
                "expected_repository": self.source.expected_repository,
                "tracked_pathspecs": list(self.source.tracked_pathspecs),
                "require_clean_checkout": self.source.require_clean_checkout,
            },
            "evidence": [
                {
                    "id": evidence.evidence_id,
                    "status": evidence.status.value,
                    "path": evidence.path,
                    "sha256": evidence.sha256,
                    "assertions": dict(evidence.assertions),
                    "required_for_activation": evidence.required_for_activation,
                }
                for evidence in self.evidence
            ],
            "migration_head": {
                "directory": self.migration_head.directory,
                "expected_revision": self.migration_head.expected_revision,
            },
            "openapi_snapshot": {
                "path": self.openapi_snapshot.path,
                "sha256": self.openapi_snapshot.sha256,
            },
            "sdk_contracts": {
                name: {"path": reference.path, "version": reference.version}
                for name, reference in self.sdk_contracts
            },
            "production_composition": {
                "path": self.production_composition.path,
                "sha256": self.production_composition.sha256,
            },
            "runbook": {
                "path": self.runbook.path,
                "version": self.runbook.version,
                "sha256": self.runbook.sha256,
            },
            "critical_veto": {"expected": self.critical_veto.expected},
        }


@dataclass(frozen=True, slots=True)
class Phase5AdmissionReport:
    state: AdmissionState
    activation_allowed: bool
    configuration_sha256: str
    feature_gates: FeatureGateResolution
    p34_7_formal_state: str
    source: GitSourceProvenance | None
    passed_evidence: tuple[str, ...]
    blockers: tuple[str, ...]
    vetoes: tuple[str, ...]
    migration_head: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "gate": "P5.0 Phase 5 admission gate",
            "state": self.state.value,
            "activation_allowed": self.activation_allowed,
            "configuration_sha256": self.configuration_sha256,
            "feature_gates": self.feature_gates.to_dict(),
            "p34_7_formal_state": self.p34_7_formal_state,
            "source": None if self.source is None else self.source.to_dict(),
            "passed_evidence": list(self.passed_evidence),
            "blockers": list(self.blockers),
            "vetoes": list(self.vetoes),
            "migration_head": self.migration_head,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "hostile_code_executed": False,
            "phase5_runtime_activated": False,
        }


def discover_migration_head(repo_root: Path, directory: str) -> str:
    """Parse Alembic revision/down_revision pairs without importing migrations."""
    versions_dir = _safe_repo_dir(repo_root, directory)
    revisions: dict[str, str | None] = {}
    for path in sorted(versions_dir.glob("*.py")):
        text = path.read_text(encoding="utf-8")
        revision_match = _REVISION_LINE.search(text)
        if revision_match is None:
            raise ConfigurationError(f"migration file has no revision id: {path.name}")
        revision = revision_match.group(1)
        if revision in revisions:
            raise ConfigurationError(f"duplicate migration revision: {revision}")
        down_match = _DOWN_REVISION_LINE.search(text)
        down = down_match.group(1) if down_match is not None and down_match.group(1) else None
        revisions[revision] = down
    if not revisions:
        raise ConfigurationError(f"migration directory contains no revisions: {directory}")
    unknown = sorted(
        down for down in revisions.values() if down is not None and down not in revisions
    )
    if unknown:
        raise ConfigurationError(
            "migration chain references unknown revisions: " + ", ".join(unknown)
        )
    referenced_as_down = {down for down in revisions.values() if down is not None}
    heads = sorted(revision for revision in revisions if revision not in referenced_as_down)
    if len(heads) != 1:
        raise ConfigurationError(f"migration chain has {len(heads)} heads: {', '.join(heads)}")
    return heads[0]


def _extract_sdk_version(path: Path) -> str:
    if path.name.endswith(".toml"):
        match = _PYPROJECT_VERSION_LINE.search(path.read_text(encoding="utf-8"))
        if match is None:
            raise ConfigurationError(f"SDK contract has no version line: {path.name}")
        return match.group(1)
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = payload.get("version")
    if not isinstance(version, str) or not version:
        raise ConfigurationError(f"SDK contract has no version field: {path.name}")
    return version


class Phase5AdmissionGate:
    """Admit Phase 5 engineering only from a clean, fully evidenced manifest."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve(strict=True)

    def validate_only(self, config: Phase5AdmissionConfig) -> Phase5AdmissionReport:
        blockers = ["formal Phase 5 admission verification was not executed"]
        if not config.activation_requested:
            blockers.append("Phase 5 admission remains explicitly disabled")
        if config.p34_7.formal_state is not P347FormalState.READY:
            blockers.append(f"P34.7 formal state is not ready: {config.p34_7.formal_state.value}")
        blockers.extend(
            f"{item.evidence_id}: {item.status.value}"
            for item in config.evidence
            if item.required_for_activation and item.status is not EvidenceStatus.PASSED
        )
        return Phase5AdmissionReport(
            state=AdmissionState.BLOCKED,
            activation_allowed=False,
            configuration_sha256=config.canonical_digest(),
            feature_gates=config.feature_gates,
            p34_7_formal_state=config.p34_7.formal_state.value,
            source=None,
            passed_evidence=(),
            blockers=tuple(blockers),
            vetoes=(),
            migration_head=None,
        )

    def verify(
        self,
        config: Phase5AdmissionConfig,
        *,
        source: GitSourceProvenance | None = None,
        gate_values: Mapping[str, object] | None = None,
    ) -> Phase5AdmissionReport:
        provenance = source or build_git_source_provenance(self._repo_root, config.source)
        gates, blockers, vetoes, passed = self._collect_findings(config, provenance, gate_values)
        try:
            migration_head = self._verify_manifest_artifacts(config)
        except (ConfigurationError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            vetoes.append(f"evidence manifest: {exc}")
            migration_head = None
        if vetoes:
            state = AdmissionState.INVALID
        elif blockers:
            state = AdmissionState.BLOCKED
        else:
            state = AdmissionState.READY
        return Phase5AdmissionReport(
            state=state,
            activation_allowed=state is AdmissionState.READY,
            configuration_sha256=config.canonical_digest(),
            feature_gates=gates,
            p34_7_formal_state=config.p34_7.formal_state.value,
            source=provenance,
            passed_evidence=tuple(passed),
            blockers=tuple(blockers),
            vetoes=tuple(vetoes),
            migration_head=migration_head,
        )

    def _collect_findings(
        self,
        config: Phase5AdmissionConfig,
        provenance: GitSourceProvenance,
        gate_values: Mapping[str, object] | None,
    ) -> tuple[FeatureGateResolution, list[str], list[str], list[str]]:
        blockers: list[str] = []
        vetoes: list[str] = []
        if config.source.require_clean_checkout and not provenance.clean:
            vetoes.append("Phase 5 admission requires a clean checkout")
        if not config.activation_requested:
            blockers.append("Phase 5 admission remains explicitly disabled")
        try:
            gates = resolve_feature_gates(gate_values or {})
        except FeatureGateConfigurationError as exc:
            vetoes.append(f"feature gates: {exc}")
            gates = config.feature_gates
        else:
            if gates.any_enabled:
                blockers.append("Phase 5 feature gates must remain disabled for P5.0 admission")
        if config.p34_7.formal_state is not P347FormalState.READY:
            blockers.append(f"P34.7 formal state is not ready: {config.p34_7.formal_state.value}")
        passed, evidence_blockers, evidence_vetoes = self._verify_evidence_set(config)
        blockers.extend(evidence_blockers)
        vetoes.extend(evidence_vetoes)
        return gates, blockers, vetoes, passed

    def _verify_evidence_set(
        self, config: Phase5AdmissionConfig
    ) -> tuple[list[str], list[str], list[str]]:
        blockers: list[str] = []
        vetoes: list[str] = []
        passed: list[str] = []
        for reference in config.evidence:
            if reference.status is not EvidenceStatus.PASSED:
                if reference.required_for_activation:
                    blockers.append(f"{reference.evidence_id}: {reference.status.value}")
                continue
            try:
                self._verify_evidence(reference)
            except (ConfigurationError, json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
                vetoes.append(f"{reference.evidence_id}: {exc}")
            else:
                passed.append(reference.evidence_id)
        return passed, blockers, vetoes

    def _verify_evidence(self, reference: EvidenceReference) -> None:
        assert reference.path is not None
        assert reference.sha256 is not None
        path = _safe_repo_file(self._repo_root, reference.path)
        content = path.read_bytes()
        if _sha256_bytes(content) != reference.sha256:
            raise ConfigurationError("sealed evidence SHA-256 drifted")
        payload = json.loads(content.decode("utf-8"))
        for dotted_path, expected in reference.assertions:
            if _nested_value(payload, dotted_path) != expected:
                raise ConfigurationError(f"evidence assertion failed: {dotted_path}")

    def _verify_sealed_file(self, reference: SealedFileRef) -> None:
        path = _safe_repo_file(self._repo_root, reference.path)
        content = path.read_bytes()
        if _sha256_bytes(content) != reference.sha256:
            raise ConfigurationError(f"sealed manifest file drifted: {reference.path}")

    def _verify_sdk_version(self, reference: SdkContractRef, *, name: str) -> None:
        path = _safe_repo_file(self._repo_root, reference.path)
        actual = _extract_sdk_version(path)
        if actual != reference.version:
            raise ConfigurationError(
                f"{name} SDK contract version is {actual}, expected {reference.version}"
            )

    def _verify_manifest_artifacts(self, config: Phase5AdmissionConfig) -> str:
        migration_head = discover_migration_head(self._repo_root, config.migration_head.directory)
        if migration_head != config.migration_head.expected_revision:
            raise ConfigurationError(
                f"migration head is {migration_head}, "
                f"expected {config.migration_head.expected_revision}"
            )
        self._verify_sealed_file(config.p34_7.decision)
        self._verify_sealed_file(config.openapi_snapshot)
        self._verify_sealed_file(config.production_composition)
        self._verify_runbook(config.runbook)
        for name, reference in config.sdk_contracts:
            self._verify_sdk_version(reference, name=name)
        return migration_head

    def _verify_runbook(self, reference: RunbookRef) -> None:
        path = _safe_repo_file(self._repo_root, reference.path)
        content = path.read_bytes()
        if _sha256_bytes(content) != reference.sha256:
            raise ConfigurationError(f"sealed runbook drifted: {reference.path}")


def _nested_value(payload: object, dotted_path: str) -> object:
    current = payload
    for segment in dotted_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise ConfigurationError(f"evidence assertion path is missing: {dotted_path}")
        current = current[segment]
    return current


def load_phase5_admission_config(path: Path) -> Phase5AdmissionConfig:
    metadata = os.lstat(path)
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError("Phase 5 admission configuration must be a regular non-link file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return Phase5AdmissionConfig.from_mapping(payload)


__all__ = [
    "FEATURE_GATE_ENV_NAMES",
    "CriticalVetoRequirement",
    "FeatureGateConfigurationError",
    "FeatureGateName",
    "FeatureGateResolution",
    "MigrationHeadContract",
    "P347FormalState",
    "P347ManifestRef",
    "Phase5AdmissionConfig",
    "Phase5AdmissionGate",
    "Phase5AdmissionReport",
    "RunbookRef",
    "SdkContractRef",
    "SealedFileRef",
    "discover_migration_head",
    "load_phase5_admission_config",
    "parse_feature_gate",
    "resolve_feature_gates",
]
