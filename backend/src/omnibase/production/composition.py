"""Fail-closed P34.7 production provenance and composition admission.

This module validates evidence and topology only.  It never starts a Runner,
opens a network route, reads a secret file, loads the root ``.env`` or touches a
database.  A deployment controller may consume a ``ready`` report in the
future, but no component in this module turns that report into authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT = re.compile(r"^[0-9a-f]{40}$")
_SPIFFE_IDENTITY = re.compile(r"^spiffe://omnibase/[a-z0-9][a-z0-9._/-]{2,254}$")

_ROLES = ("core", "runner", "broker", "gateway")
_EXPECTED_CHANNELS = {
    "core_runner_mtls": ("core", "runner", "mtls"),
    "runner_broker_private": ("runner", "broker", "private_unix_socket"),
    "runner_gateway_mtls": ("runner", "gateway", "mtls"),
    "broker_gateway_mtls": ("broker", "gateway", "mtls"),
}
_FORBIDDEN_EDGE_TARGETS = {"postgresql", "redis", "minio", "object_store", "host"}
_ALLOWED_CREDENTIALS_BY_ROLE = {
    "core": {
        "capability_signing",
        "database",
        "jwt_signing",
        "object_store",
        "redis",
    },
    "runner": {"runner_identity", "short_lived_workload_identity"},
    "broker": {"daemon_identity", "namespace_permit"},
    "gateway": {"capability_signing", "database_read_adapter", "peer_registry"},
}
_ROOT_ENV_NAMES = {".env", "./.env"}


class ConfigurationError(ValueError):
    """The production contract is unsafe or malformed."""


class AdmissionState(StrEnum):
    READY = "ready"
    BLOCKED = "blocked/not_proven"
    INVALID = "invalid/veto"


class EvidenceStatus(StrEnum):
    PASSED = "passed"
    BLOCKED = "blocked"
    NOT_PROVEN = "not_proven"


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
    root = repo_root.resolve(strict=True)
    candidate = root
    for part in PurePosixPath(relative_path).parts:
        candidate = candidate / part
        metadata = os.lstat(candidate)
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise ConfigurationError(
                f"evidence path contains a link or reparse point: {relative_path}"
            )
    candidate = candidate.resolve(strict=True)
    try:
        resolved_relative = candidate.relative_to(root).as_posix()
    except ValueError as exc:
        raise ConfigurationError("evidence path escaped the repository") from exc
    if resolved_relative.lower() in _ROOT_ENV_NAMES:
        raise ConfigurationError("evidence path resolved to the root .env")
    metadata = os.lstat(candidate)
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError(f"evidence must be a regular non-link file: {relative_path}")
    return candidate


def _nested_value(payload: object, dotted_path: str) -> object:
    current = payload
    for segment in dotted_path.split("."):
        if not isinstance(current, dict) or segment not in current:
            raise ConfigurationError(f"evidence assertion path is missing: {dotted_path}")
        current = current[segment]
    return current


@dataclass(frozen=True, slots=True)
class SourceScope:
    expected_repository: str
    tracked_pathspecs: tuple[str, ...]
    require_clean_checkout: bool

    @classmethod
    def from_mapping(cls, value: object) -> SourceScope:
        data = _strict_object(value, name="source")
        _only_keys(
            data,
            {"expected_repository", "tracked_pathspecs", "require_clean_checkout"},
            name="source",
        )
        expected_repository = _strict_string(
            data.get("expected_repository"), name="source.expected_repository"
        )
        if not expected_repository.startswith(("https://github.com/", "git@github.com:")):
            raise ConfigurationError("source.expected_repository must be an explicit GitHub remote")
        pathspecs = tuple(
            _relative_repo_path(item, name="source.tracked_pathspecs[]")
            for item in _strict_list(data.get("tracked_pathspecs"), name="source.tracked_pathspecs")
        )
        if not pathspecs or len(pathspecs) != len(set(pathspecs)):
            raise ConfigurationError("source.tracked_pathspecs must be non-empty and unique")
        required = {
            ".gitattributes",
            "AGENTS.md",
            "backend/pyproject.toml",
            "backend/uv.lock",
            "backend/src/omnibase/production",
            "deployment/production",
            "scripts/production",
        }
        if not required.issubset(pathspecs):
            raise ConfigurationError("source.tracked_pathspecs omits required provenance inputs")
        return cls(
            expected_repository=expected_repository,
            tracked_pathspecs=pathspecs,
            require_clean_checkout=_strict_bool(
                data.get("require_clean_checkout"), name="source.require_clean_checkout"
            ),
        )


@dataclass(frozen=True, slots=True)
class ComponentContract:
    role: str
    service_identity: str
    process_boundary: str
    accepts_browser_traffic: bool
    executes_workspace_code: bool
    credential_classes: tuple[str, ...]

    @classmethod
    def from_mapping(cls, role: str, value: object) -> ComponentContract:
        data = _strict_object(value, name=f"components.{role}")
        _only_keys(
            data,
            {
                "service_identity",
                "process_boundary",
                "accepts_browser_traffic",
                "executes_workspace_code",
                "credential_classes",
            },
            name=f"components.{role}",
        )
        identity = _strict_string(
            data.get("service_identity"), name=f"components.{role}.service_identity"
        )
        if _SPIFFE_IDENTITY.fullmatch(identity) is None:
            raise ConfigurationError(
                f"components.{role}.service_identity is not an OmniBase SPIFFE ID"
            )
        process_boundary = _strict_string(
            data.get("process_boundary"), name=f"components.{role}.process_boundary"
        )
        if process_boundary != "independent":
            raise ConfigurationError(f"components.{role} must use an independent process boundary")
        credentials = tuple(
            _strict_string(item, name=f"components.{role}.credential_classes[]")
            for item in _strict_list(
                data.get("credential_classes"), name=f"components.{role}.credential_classes"
            )
        )
        if len(credentials) != len(set(credentials)):
            raise ConfigurationError(f"components.{role}.credential_classes contains duplicates")
        component = cls(
            role=role,
            service_identity=identity,
            process_boundary=process_boundary,
            accepts_browser_traffic=_strict_bool(
                data.get("accepts_browser_traffic"),
                name=f"components.{role}.accepts_browser_traffic",
            ),
            executes_workspace_code=_strict_bool(
                data.get("executes_workspace_code"),
                name=f"components.{role}.executes_workspace_code",
            ),
            credential_classes=credentials,
        )
        component._validate_role_boundary()
        return component

    def _validate_role_boundary(self) -> None:
        if self.accepts_browser_traffic != (self.role == "core"):
            raise ConfigurationError("only Core may accept browser traffic")
        if self.executes_workspace_code != (self.role == "runner"):
            raise ConfigurationError("only Runner may execute workspace code")
        credentials = set(self.credential_classes)
        unexpected = credentials - _ALLOWED_CREDENTIALS_BY_ROLE[self.role]
        if unexpected:
            raise ConfigurationError(
                f"{self.role} contains forbidden or unknown credential classes: "
                + ", ".join(sorted(unexpected))
            )


@dataclass(frozen=True, slots=True)
class ChannelContract:
    name: str
    source: str
    target: str
    transport: str
    mutual_authentication: bool
    server_owned_peer_identity: bool
    logical_identifiers_only: bool
    carries_browser_credentials: bool

    @classmethod
    def from_mapping(cls, value: object) -> ChannelContract:
        data = _strict_object(value, name="channels[]")
        _only_keys(
            data,
            {
                "name",
                "source",
                "target",
                "transport",
                "mutual_authentication",
                "server_owned_peer_identity",
                "logical_identifiers_only",
                "carries_browser_credentials",
            },
            name="channels[]",
        )
        channel = cls(
            name=_strict_string(data.get("name"), name="channels[].name"),
            source=_strict_string(data.get("source"), name="channels[].source"),
            target=_strict_string(data.get("target"), name="channels[].target"),
            transport=_strict_string(data.get("transport"), name="channels[].transport"),
            mutual_authentication=_strict_bool(
                data.get("mutual_authentication"), name="channels[].mutual_authentication"
            ),
            server_owned_peer_identity=_strict_bool(
                data.get("server_owned_peer_identity"),
                name="channels[].server_owned_peer_identity",
            ),
            logical_identifiers_only=_strict_bool(
                data.get("logical_identifiers_only"), name="channels[].logical_identifiers_only"
            ),
            carries_browser_credentials=_strict_bool(
                data.get("carries_browser_credentials"),
                name="channels[].carries_browser_credentials",
            ),
        )
        expected = _EXPECTED_CHANNELS.get(channel.name)
        if expected != (channel.source, channel.target, channel.transport):
            raise ConfigurationError(f"channel {channel.name} does not match the sealed topology")
        if channel.source in _FORBIDDEN_EDGE_TARGETS or channel.target in _FORBIDDEN_EDGE_TARGETS:
            raise ConfigurationError("direct infrastructure routes are forbidden")
        if not channel.logical_identifiers_only or channel.carries_browser_credentials:
            raise ConfigurationError(
                "production channels require logical IDs and forbid Browser credentials"
            )
        if channel.transport == "mtls" and (
            not channel.mutual_authentication or not channel.server_owned_peer_identity
        ):
            raise ConfigurationError(
                "mTLS channels require mutual auth and a server-owned peer identity"
            )
        if channel.transport == "private_unix_socket" and (
            not channel.mutual_authentication or not channel.server_owned_peer_identity
        ):
            raise ConfigurationError(
                "private Broker transport requires peer authentication and pinned daemon identity"
            )
        return channel


@dataclass(frozen=True, slots=True)
class EvidenceReference:
    evidence_id: str
    status: EvidenceStatus
    path: str | None
    sha256: str | None
    assertions: tuple[tuple[str, object], ...]
    required_for_activation: bool

    @classmethod
    def from_mapping(cls, value: object) -> EvidenceReference:
        data = _strict_object(value, name="evidence[]")
        _only_keys(
            data,
            {
                "id",
                "status",
                "path",
                "sha256",
                "assertions",
                "required_for_activation",
            },
            name="evidence[]",
        )
        evidence_id = _strict_string(data.get("id"), name="evidence[].id")
        try:
            status_value = EvidenceStatus(
                _strict_string(data.get("status"), name="evidence[].status")
            )
        except ValueError as exc:
            raise ConfigurationError(f"evidence {evidence_id} has an invalid status") from exc
        path_value = data.get("path")
        path = (
            None if path_value is None else _relative_repo_path(path_value, name="evidence[].path")
        )
        digest_value = data.get("sha256")
        digest = (
            None
            if digest_value is None
            else _strict_string(digest_value, name="evidence[].sha256").lower()
        )
        if digest is not None and _SHA256.fullmatch(digest) is None:
            raise ConfigurationError(f"evidence {evidence_id} has an invalid SHA-256")
        assertions_object = _strict_object(data.get("assertions", {}), name="evidence[].assertions")
        assertions = tuple(sorted(assertions_object.items()))
        required = _strict_bool(
            data.get("required_for_activation"), name="evidence[].required_for_activation"
        )
        if status_value is EvidenceStatus.PASSED and (
            path is None or digest is None or not assertions
        ):
            raise ConfigurationError(
                f"passed evidence {evidence_id} requires a path, SHA-256 and assertions"
            )
        return cls(
            evidence_id=evidence_id,
            status=status_value,
            path=path,
            sha256=digest,
            assertions=assertions,
            required_for_activation=required,
        )


@dataclass(frozen=True, slots=True)
class ProductionCompositionConfig:
    schema_version: int
    phase: str
    activation_requested: bool
    source: SourceScope
    components: tuple[ComponentContract, ...]
    channels: tuple[ChannelContract, ...]
    evidence: tuple[EvidenceReference, ...]

    @classmethod
    def from_mapping(cls, value: object) -> ProductionCompositionConfig:
        data = _strict_object(value, name="configuration")
        _only_keys(
            data,
            {
                "schema_version",
                "phase",
                "activation_requested",
                "source",
                "components",
                "channels",
                "evidence",
            },
            name="configuration",
        )
        if data.get("schema_version") != 1:
            raise ConfigurationError("configuration.schema_version must be 1")
        if data.get("phase") != "P34.7A/B":
            raise ConfigurationError("configuration.phase must be P34.7A/B")
        component_data = _strict_object(data.get("components"), name="components")
        if set(component_data) != set(_ROLES):
            raise ConfigurationError(
                "components must contain exactly core, runner, broker and gateway"
            )
        components = tuple(
            ComponentContract.from_mapping(role, component_data[role]) for role in _ROLES
        )
        if len({component.service_identity for component in components}) != len(components):
            raise ConfigurationError("production component service identities must be unique")
        channels = tuple(
            ChannelContract.from_mapping(item)
            for item in _strict_list(data.get("channels"), name="channels")
        )
        if {channel.name for channel in channels} != set(_EXPECTED_CHANNELS) or len(
            channels
        ) != len(_EXPECTED_CHANNELS):
            raise ConfigurationError("channels must contain exactly the sealed production topology")
        evidence = tuple(
            EvidenceReference.from_mapping(item)
            for item in _strict_list(data.get("evidence"), name="evidence")
        )
        if not evidence or len({item.evidence_id for item in evidence}) != len(evidence):
            raise ConfigurationError("evidence IDs must be non-empty and unique")
        return cls(
            schema_version=1,
            phase="P34.7A/B",
            activation_requested=_strict_bool(
                data.get("activation_requested"), name="activation_requested"
            ),
            source=SourceScope.from_mapping(data.get("source")),
            components=components,
            channels=channels,
            evidence=evidence,
        )

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "activation_requested": self.activation_requested,
            "source": {
                "expected_repository": self.source.expected_repository,
                "tracked_pathspecs": list(self.source.tracked_pathspecs),
                "require_clean_checkout": self.source.require_clean_checkout,
            },
            "components": {
                component.role: {
                    "service_identity": component.service_identity,
                    "process_boundary": component.process_boundary,
                    "accepts_browser_traffic": component.accepts_browser_traffic,
                    "executes_workspace_code": component.executes_workspace_code,
                    "credential_classes": list(component.credential_classes),
                }
                for component in self.components
            },
            "channels": [
                {
                    "name": channel.name,
                    "source": channel.source,
                    "target": channel.target,
                    "transport": channel.transport,
                    "mutual_authentication": channel.mutual_authentication,
                    "server_owned_peer_identity": channel.server_owned_peer_identity,
                    "logical_identifiers_only": channel.logical_identifiers_only,
                    "carries_browser_credentials": channel.carries_browser_credentials,
                }
                for channel in self.channels
            ],
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
        }


@dataclass(frozen=True, slots=True)
class GitSourceProvenance:
    git_commit: str
    git_tree: str
    remote_origin: str
    clean: bool
    dirty_paths: tuple[str, ...]
    file_count: int
    files: tuple[tuple[str, int, str], ...]
    manifest_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "git_commit": self.git_commit,
            "git_tree": self.git_tree,
            "remote_origin": self.remote_origin,
            "clean": self.clean,
            "dirty_paths": list(self.dirty_paths),
            "file_count": self.file_count,
            "files": [
                {"path": path, "size": size, "sha256": digest} for path, size, digest in self.files
            ],
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class AdmissionReport:
    state: AdmissionState
    activation_allowed: bool
    configuration_sha256: str
    source: GitSourceProvenance | None
    passed_evidence: tuple[str, ...]
    blockers: tuple[str, ...]
    vetoes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "gate": "P34.7A/B production provenance and composition admission",
            "state": self.state.value,
            "activation_allowed": self.activation_allowed,
            "configuration_sha256": self.configuration_sha256,
            "source": None if self.source is None else self.source.to_dict(),
            "passed_evidence": list(self.passed_evidence),
            "blockers": list(self.blockers),
            "vetoes": list(self.vetoes),
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "hostile_code_executed": False,
        }


def _git(repo_root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *arguments],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
    )
    if completed.returncode != 0:
        raise ConfigurationError("Git provenance command failed")
    return completed.stdout.strip()


def _normalize_remote(value: str) -> str:
    normalized = value.strip().removesuffix("/").removesuffix(".git")
    if normalized.startswith("git@github.com:"):
        normalized = "https://github.com/" + normalized.split(":", 1)[1]
    return normalized.lower()


def build_git_source_provenance(repo_root: Path, scope: SourceScope) -> GitSourceProvenance:
    """Hash tracked source bytes without reading ignored/untracked secret files."""

    root = repo_root.resolve(strict=True)
    commit = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    if _GIT_OBJECT.fullmatch(commit) is None or _GIT_OBJECT.fullmatch(tree) is None:
        raise ConfigurationError("Git commit or tree identity is malformed")
    remote = _git(root, "config", "--get", "remote.origin.url")
    if _normalize_remote(remote) != _normalize_remote(scope.expected_repository):
        raise ConfigurationError("Git remote origin does not match the production source contract")
    status = _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    dirty_paths = tuple(line for line in status.splitlines() if line)

    raw_paths = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", *scope.tracked_pathspecs],
        check=False,
        capture_output=True,
    )
    if raw_paths.returncode != 0:
        raise ConfigurationError("Git tracked-source enumeration failed")
    paths = sorted(path.decode("utf-8") for path in raw_paths.stdout.split(b"\0") if path)
    if not paths:
        raise ConfigurationError("tracked source scope resolved to zero files")
    files: list[tuple[str, int, str]] = []
    for relative in paths:
        normalized = _relative_repo_path(relative, name="tracked source path")
        if normalized.lower() in _ROOT_ENV_NAMES:
            raise ConfigurationError("tracked provenance attempted to include the root .env")
        path = _safe_repo_file(root, normalized)
        content = path.read_bytes()
        files.append((normalized, len(content), _sha256_bytes(content)))
    manifest_payload = [
        {"path": path, "size": size, "sha256": digest} for path, size, digest in files
    ]
    return GitSourceProvenance(
        git_commit=commit,
        git_tree=tree,
        remote_origin=remote,
        clean=not dirty_paths,
        dirty_paths=dirty_paths,
        file_count=len(files),
        files=tuple(files),
        manifest_sha256=_sha256_bytes(_canonical_json(manifest_payload)),
    )


class ProductionCompositionGate:
    """Admit only a clean, fully evidenced and explicitly enabled topology."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve(strict=True)

    def validate_only(self, config: ProductionCompositionConfig) -> AdmissionReport:
        blockers = ["formal production evidence verification was not executed"]
        if not config.activation_requested:
            blockers.append("production activation remains explicitly disabled")
        blockers.extend(
            f"{item.evidence_id}: {item.status.value}"
            for item in config.evidence
            if item.required_for_activation and item.status is not EvidenceStatus.PASSED
        )
        return AdmissionReport(
            state=AdmissionState.BLOCKED,
            activation_allowed=False,
            configuration_sha256=config.canonical_digest(),
            source=None,
            passed_evidence=(),
            blockers=tuple(blockers),
            vetoes=(),
        )

    def verify(
        self,
        config: ProductionCompositionConfig,
        *,
        source: GitSourceProvenance | None = None,
    ) -> AdmissionReport:
        provenance = source or build_git_source_provenance(self._repo_root, config.source)
        blockers: list[str] = []
        vetoes: list[str] = []
        passed: list[str] = []
        if config.source.require_clean_checkout and not provenance.clean:
            vetoes.append("production Gate requires a clean checkout")
        if not config.activation_requested:
            blockers.append("production activation remains explicitly disabled")
        for reference in config.evidence:
            if reference.status is not EvidenceStatus.PASSED:
                if reference.required_for_activation:
                    blockers.append(f"{reference.evidence_id}: {reference.status.value}")
                continue
            try:
                self._verify_evidence(reference)
            except (ConfigurationError, json.JSONDecodeError, UnicodeDecodeError) as exc:
                vetoes.append(f"{reference.evidence_id}: {exc}")
            else:
                passed.append(reference.evidence_id)
        if vetoes:
            state = AdmissionState.INVALID
        elif blockers:
            state = AdmissionState.BLOCKED
        else:
            state = AdmissionState.READY
        return AdmissionReport(
            state=state,
            activation_allowed=state is AdmissionState.READY,
            configuration_sha256=config.canonical_digest(),
            source=provenance,
            passed_evidence=tuple(passed),
            blockers=tuple(blockers),
            vetoes=tuple(vetoes),
        )

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


def load_production_composition_config(path: Path) -> ProductionCompositionConfig:
    metadata = os.lstat(path)
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise ConfigurationError("production configuration must be a regular non-link file")
    payload = json.loads(path.read_text(encoding="utf-8"))
    return ProductionCompositionConfig.from_mapping(payload)


__all__ = [
    "AdmissionReport",
    "AdmissionState",
    "ConfigurationError",
    "EvidenceReference",
    "EvidenceStatus",
    "GitSourceProvenance",
    "ProductionCompositionConfig",
    "ProductionCompositionGate",
    "build_git_source_provenance",
    "load_production_composition_config",
]
