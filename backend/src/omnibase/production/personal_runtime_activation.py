"""Fail-closed personal Runtime canary activation ledger.

This module is the deployment-control half of the personal-edition Runtime
lane.  It does not start an Agent, mutate process environment variables, read
the root ``.env`` or connect to a database.  Instead it:

* validates one exact ``personal_single_owner`` canary scope;
* verifies the already-sealed Personal Owner readiness contract when a public
  repository root is available;
* produces a deterministic activation plan digest;
* records activate/rollback events in a run-scoped append-only hash chain;
* provides an independent, irreversible kill marker that wins even when the
  event ledger is corrupt.

The Browser/runtime composition consumes only an ACTIVE, unexpired ledger and
then independently revalidates the live Owner, migration head, provider and
request scope.  A ledger event is therefore an operator intent receipt, not a
replacement for live authorization.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from uuid import UUID, uuid4

from omnibase.production.personal_owner_gate import (
    PersonalGateConfigurationError,
    load_personal_owner_gate_config,
    verify_personal_engineering_evidence,
)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_EVENT_FILE = re.compile(r"^(?P<sequence>[0-9]{6})-(?P<event>activate|rollback)\.json$")
_KILL_FILE = "KILL_SWITCH.json"
_CONFIG_FIELDS = frozenset(
    {
        "agent_planner_enabled",
        "agent_version_id",
        "canary_id",
        "enterprise_approved_digest_present",
        "environment",
        "external_side_effects",
        "invocation_mode",
        "max_canary_seconds",
        "max_concurrent_invocations",
        "max_top_k",
        "migration_0013_created",
        "migration_head",
        "multi_agent_enabled",
        "network",
        "owner_readiness",
        "owner_user_id",
        "profile",
        "schema_version",
        "tenant_id",
        "workspace_id",
    }
)
_READINESS_ASSERTIONS = {
    "business_database_accessed": False,
    "business_database_migrated": False,
    "enterprise_production_approved": False,
    "enterprise_track_frozen": True,
    "migration_0013_created": True,
    "migration_head": "0013",
    "passed": True,
    "personal_owner_activation_ready": True,
    "production_runtime_activated": False,
    "profile": "personal_single_owner",
    "root_env_accessed": False,
}


class PersonalRuntimeConfigurationError(ValueError):
    """The canary contract, activation ledger or path is unsafe/ambiguous."""


class PersonalRuntimeState(StrEnum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    EXPIRED = "expired"
    ROLLED_BACK = "rolled_back"
    KILLED = "killed"
    INVALID = "invalid/veto"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _utc(value: datetime) -> datetime:
    aware = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return aware.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _utc(value).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, name: str) -> datetime:
    if type(value) is not str or not value.endswith("Z"):
        raise PersonalRuntimeConfigurationError(f"{name} must be a UTC Z timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise PersonalRuntimeConfigurationError(f"{name} is invalid") from exc
    return _utc(parsed)


def _object(value: object, *, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(type(key) is not str for key in value):
        raise PersonalRuntimeConfigurationError(f"{name} must be an object")
    return value


def _exact_fields(value: Mapping[str, object], expected: frozenset[str], *, name: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        raise PersonalRuntimeConfigurationError(
            f"{name} fields must be exact; missing={sorted(expected - actual)}, "
            f"unknown={sorted(actual - expected)}"
        )


def _string(value: Mapping[str, object], name: str) -> str:
    result = value.get(name)
    if type(result) is not str or not result:
        raise PersonalRuntimeConfigurationError(f"{name} must be a non-empty string")
    return result


def _boolean(value: Mapping[str, object], name: str) -> bool:
    result = value.get(name)
    if type(result) is not bool:
        raise PersonalRuntimeConfigurationError(f"{name} must be a JSON boolean")
    return result


def _integer(
    value: Mapping[str, object],
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    result = value.get(name)
    if type(result) is not int or result < minimum or result > maximum:
        raise PersonalRuntimeConfigurationError(
            f"{name} must be an integer in [{minimum}, {maximum}]"
        )
    return result


def _uuid(value: Mapping[str, object], name: str) -> str:
    raw = _string(value, name)
    try:
        canonical = str(UUID(raw))
    except ValueError as exc:
        raise PersonalRuntimeConfigurationError(f"{name} must be a canonical UUID") from exc
    if canonical != raw:
        raise PersonalRuntimeConfigurationError(f"{name} must be a canonical UUID")
    return canonical


def _digest(value: Mapping[str, object], name: str) -> str:
    raw = _string(value, name)
    if _SHA256.fullmatch(raw) is None:
        raise PersonalRuntimeConfigurationError(f"{name} must be a lowercase SHA-256")
    return raw


def _relative_path(value: Mapping[str, object], name: str) -> str:
    raw = _string(value, name).replace("\\", "/")
    path = PurePosixPath(raw)
    if (
        path.is_absolute()
        or not path.parts
        or ":" in path.parts[0]
        or any(part in {"", ".", ".."} for part in path.parts)
        or raw.lower() in {".env", "./.env"}
    ):
        raise PersonalRuntimeConfigurationError(f"{name} must be repository-relative")
    return path.as_posix()


def _safe_regular_file(path: Path, *, label: str) -> Path:
    try:
        metadata = os.lstat(path)
    except OSError as exc:
        raise PersonalRuntimeConfigurationError(f"{label} is unavailable") from exc
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise PersonalRuntimeConfigurationError(f"{label} must be a regular non-link file")
    return path


def _load_json_file(path: Path, *, label: str) -> tuple[Mapping[str, object], bytes]:
    content = _safe_regular_file(path, label=label).read_bytes()
    try:
        payload = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PersonalRuntimeConfigurationError(f"{label} must be canonical UTF-8 JSON") from exc
    return _object(payload, name=label), content


@dataclass(frozen=True, slots=True)
class SealedReadinessRef:
    path: str
    sha256: str

    @classmethod
    def from_mapping(cls, value: object) -> SealedReadinessRef:
        data = _object(value, name="owner_readiness")
        _exact_fields(data, frozenset({"path", "sha256"}), name="owner_readiness")
        return cls(
            path=_relative_path(data, "path"),
            sha256=_digest(data, "sha256"),
        )


@dataclass(frozen=True, slots=True)
class PersonalRuntimeCanaryConfig:
    schema_version: int
    profile: str
    canary_id: str
    environment: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    agent_version_id: str
    invocation_mode: str
    max_canary_seconds: int
    max_concurrent_invocations: int
    max_top_k: int
    network_default_deny: bool
    network_destinations: tuple[str, ...]
    external_side_effects: bool
    migration_head: str
    migration_0013_created: bool
    agent_planner_enabled: bool
    multi_agent_enabled: bool
    enterprise_approved_digest_present: bool
    owner_readiness: SealedReadinessRef

    @classmethod
    def from_mapping(cls, value: object) -> PersonalRuntimeCanaryConfig:
        data = _object(value, name="personal Runtime canary config")
        _exact_fields(data, _CONFIG_FIELDS, name="personal Runtime canary config")
        if _integer(data, "schema_version", minimum=1, maximum=1) != 1:
            raise PersonalRuntimeConfigurationError("unsupported schema_version")
        profile = _string(data, "profile")
        if profile != "personal_single_owner":
            raise PersonalRuntimeConfigurationError("profile must be personal_single_owner")
        environment = _string(data, "environment")
        if environment != "production":
            raise PersonalRuntimeConfigurationError(
                "personal canary environment must be production"
            )
        invocation_mode = _string(data, "invocation_mode")
        if invocation_mode != "no_tool":
            raise PersonalRuntimeConfigurationError("personal canary supports no_tool only")
        network = _object(data.get("network"), name="network")
        _exact_fields(network, frozenset({"default_deny", "destinations"}), name="network")
        default_deny = _boolean(network, "default_deny")
        destinations = network.get("destinations")
        if not default_deny or type(destinations) is not list or destinations:
            raise PersonalRuntimeConfigurationError(
                "no_tool personal canary requires default-deny with no workload destinations"
            )
        external_side_effects = _boolean(data, "external_side_effects")
        migration_0013_created = _boolean(data, "migration_0013_created")
        if not migration_0013_created:
            raise PersonalRuntimeConfigurationError(
                "personal canary requires the current migration 0013 to exist"
            )
        safety_flags = {
            name: _boolean(data, name)
            for name in (
                "agent_planner_enabled",
                "multi_agent_enabled",
                "enterprise_approved_digest_present",
            )
        }
        if external_side_effects or any(safety_flags.values()):
            raise PersonalRuntimeConfigurationError(
                "personal no_tool canary cannot carry side effects, Planner, multi-Agent, "
                "or enterprise approval"
            )
        migration_head = _string(data, "migration_head")
        if migration_head != "0013":
            raise PersonalRuntimeConfigurationError("personal canary requires migration head 0013")
        max_concurrent = _integer(
            data,
            "max_concurrent_invocations",
            minimum=1,
            maximum=1,
        )
        return cls(
            schema_version=1,
            profile=profile,
            canary_id=_uuid(data, "canary_id"),
            environment=environment,
            tenant_id=_uuid(data, "tenant_id"),
            workspace_id=_uuid(data, "workspace_id"),
            owner_user_id=_uuid(data, "owner_user_id"),
            agent_version_id=_uuid(data, "agent_version_id"),
            invocation_mode=invocation_mode,
            max_canary_seconds=_integer(
                data,
                "max_canary_seconds",
                minimum=60,
                maximum=86_400,
            ),
            max_concurrent_invocations=max_concurrent,
            max_top_k=_integer(data, "max_top_k", minimum=1, maximum=12),
            network_default_deny=True,
            network_destinations=(),
            external_side_effects=False,
            migration_head=migration_head,
            migration_0013_created=migration_0013_created,
            owner_readiness=SealedReadinessRef.from_mapping(data.get("owner_readiness")),
            **safety_flags,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_planner_enabled": self.agent_planner_enabled,
            "agent_version_id": self.agent_version_id,
            "canary_id": self.canary_id,
            "enterprise_approved_digest_present": self.enterprise_approved_digest_present,
            "environment": self.environment,
            "external_side_effects": self.external_side_effects,
            "invocation_mode": self.invocation_mode,
            "max_canary_seconds": self.max_canary_seconds,
            "max_concurrent_invocations": self.max_concurrent_invocations,
            "max_top_k": self.max_top_k,
            "migration_0013_created": self.migration_0013_created,
            "migration_head": self.migration_head,
            "multi_agent_enabled": self.multi_agent_enabled,
            "network": {
                "default_deny": self.network_default_deny,
                "destinations": list(self.network_destinations),
            },
            "owner_readiness": {
                "path": self.owner_readiness.path,
                "sha256": self.owner_readiness.sha256,
            },
            "owner_user_id": self.owner_user_id,
            "profile": self.profile,
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
        }

    def canonical_digest(self) -> str:
        return _sha256(_canonical_bytes(self.to_dict()))

    def activation_plan(self) -> PersonalRuntimeActivationPlan:
        return PersonalRuntimeActivationPlan(
            schema_version=1,
            canary_id=self.canary_id,
            config_sha256=self.canonical_digest(),
            profile=self.profile,
            environment=self.environment,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            owner_user_id=self.owner_user_id,
            agent_version_id=self.agent_version_id,
            invocation_mode=self.invocation_mode,
            max_canary_seconds=self.max_canary_seconds,
            max_concurrent_invocations=self.max_concurrent_invocations,
            max_top_k=self.max_top_k,
            required_feature_gates={
                "AGENT_RUNTIME_ENABLED": True,
                "AGENT_PLANNER_ENABLED": False,
                "MULTI_AGENT_ENABLED": False,
            },
            rollback_available=True,
            kill_switch_available=True,
        )


@dataclass(frozen=True, slots=True)
class PersonalRuntimeActivationPlan:
    schema_version: int
    canary_id: str
    config_sha256: str
    profile: str
    environment: str
    tenant_id: str
    workspace_id: str
    owner_user_id: str
    agent_version_id: str
    invocation_mode: str
    max_canary_seconds: int
    max_concurrent_invocations: int
    max_top_k: int
    required_feature_gates: Mapping[str, bool]
    rollback_available: bool
    kill_switch_available: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_version_id": self.agent_version_id,
            "canary_id": self.canary_id,
            "config_sha256": self.config_sha256,
            "environment": self.environment,
            "invocation_mode": self.invocation_mode,
            "kill_switch_available": self.kill_switch_available,
            "max_canary_seconds": self.max_canary_seconds,
            "max_concurrent_invocations": self.max_concurrent_invocations,
            "max_top_k": self.max_top_k,
            "owner_user_id": self.owner_user_id,
            "profile": self.profile,
            "required_feature_gates": dict(self.required_feature_gates),
            "rollback_available": self.rollback_available,
            "schema_version": self.schema_version,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
        }

    def canonical_digest(self) -> str:
        return _sha256(_canonical_bytes(self.to_dict()))


@dataclass(frozen=True, slots=True)
class PersonalRuntimeStatus:
    state: PersonalRuntimeState
    canary_id: str | None
    config_sha256: str | None
    plan_sha256: str | None
    activated_at: str | None
    expires_at: str | None
    terminal_reason: str | None
    last_event_sha256: str | None
    events: int
    blockers: tuple[str, ...] = ()
    vetoes: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return self.state is PersonalRuntimeState.ACTIVE

    def to_dict(self) -> dict[str, object]:
        return {
            "active": self.active,
            "activated_at": self.activated_at,
            "blockers": list(self.blockers),
            "canary_id": self.canary_id,
            "config_sha256": self.config_sha256,
            "events": self.events,
            "expires_at": self.expires_at,
            "last_event_sha256": self.last_event_sha256,
            "plan_sha256": self.plan_sha256,
            "profile": "personal_single_owner",
            "state": self.state.value,
            "terminal_reason": self.terminal_reason,
            "vetoes": list(self.vetoes),
        }


def load_personal_runtime_canary_config(
    path: Path,
    *,
    repo_root: Path | None = None,
    verify_owner_readiness: bool = True,
) -> PersonalRuntimeCanaryConfig:
    """Load a strict canary config and optionally verify the public readiness seal."""
    if not path.is_absolute():
        raise PersonalRuntimeConfigurationError("personal canary config path must be absolute")
    payload, content = _load_json_file(path, label="personal canary config")
    config = PersonalRuntimeCanaryConfig.from_mapping(payload)
    if content != _canonical_bytes(payload) + b"\n":
        raise PersonalRuntimeConfigurationError(
            "personal canary config must be canonical JSON with one trailing newline"
        )
    if verify_owner_readiness:
        if repo_root is None:
            raise PersonalRuntimeConfigurationError(
                "repo_root is required to verify Personal Owner readiness"
            )
        root = repo_root.resolve(strict=True)
        readiness_candidate = root / config.owner_readiness.path
        _safe_regular_file(readiness_candidate, label="owner readiness config")
        readiness_path = readiness_candidate.resolve(strict=True)
        try:
            readiness_path.relative_to(root)
        except ValueError as exc:
            raise PersonalRuntimeConfigurationError(
                "owner readiness config escaped the repository"
            ) from exc
        _, readiness_bytes = _load_json_file(readiness_path, label="owner readiness config")
        if _sha256(readiness_bytes) != config.owner_readiness.sha256:
            raise PersonalRuntimeConfigurationError("owner readiness config SHA-256 drifted")
        try:
            readiness = load_personal_owner_gate_config(readiness_path)
            verify_personal_engineering_evidence(root, readiness.evidence)
        except (PersonalGateConfigurationError, OSError, json.JSONDecodeError) as exc:
            raise PersonalRuntimeConfigurationError(f"owner readiness is invalid: {exc}") from exc
        assertions = dict(readiness.evidence.assertions)
        if any(
            assertions.get(name) != expected for name, expected in _READINESS_ASSERTIONS.items()
        ):
            raise PersonalRuntimeConfigurationError(
                "owner readiness evidence does not prove the frozen personal posture"
            )
    return config


def _ensure_state_directory(state_dir: Path) -> Path:
    if not state_dir.is_absolute():
        raise PersonalRuntimeConfigurationError("state directory must be absolute")
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    current = state_dir
    while current != current.parent:
        metadata = os.lstat(current)
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise PersonalRuntimeConfigurationError("state directory contains a link")
        current = current.parent
    resolved = state_dir.resolve(strict=True)
    if not resolved.is_dir():
        raise PersonalRuntimeConfigurationError("state directory must be a directory")
    return resolved


def _existing_state_directory(state_dir: Path) -> Path | None:
    if not state_dir.is_absolute():
        raise PersonalRuntimeConfigurationError("state directory must be absolute")
    if not os.path.lexists(state_dir):
        return None
    current = state_dir
    while current != current.parent:
        metadata = os.lstat(current)
        is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
        if stat.S_ISLNK(metadata.st_mode) or is_reparse:
            raise PersonalRuntimeConfigurationError("state directory contains a link")
        current = current.parent
    resolved = state_dir.resolve(strict=True)
    if not resolved.is_dir():
        raise PersonalRuntimeConfigurationError("state directory must be a directory")
    return resolved


def _remove_created_artifact(path: Path, *, directory_descriptor: int | None) -> None:
    if directory_descriptor is not None and os.unlink in os.supports_dir_fd:
        with suppress(FileNotFoundError):
            os.unlink(path.name, dir_fd=directory_descriptor)
        return
    path.unlink(missing_ok=True)


def _write_exclusive(path: Path, payload: Mapping[str, object]) -> bytes:
    content = _canonical_bytes(payload) + b"\n"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    directory = path.parent
    before = os.stat(directory, follow_symlinks=False)
    before_identity = (before.st_dev, before.st_ino)
    directory_descriptor: int | None = None
    descriptor: int | None = None
    created = False
    try:
        if os.open in os.supports_dir_fd:
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            directory_descriptor = os.open(directory, directory_flags)
            descriptor = os.open(
                path.name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
        else:
            descriptor = os.open(path, flags, 0o600)
        created = True
    except FileExistsError as exc:
        raise PersonalRuntimeConfigurationError(
            f"activation artifact already exists: {path.name}"
        ) from exc
    try:
        assert descriptor is not None
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        after = os.stat(directory, follow_symlinks=False)
        if (after.st_dev, after.st_ino) != before_identity:
            raise PersonalRuntimeConfigurationError(
                "activation state directory identity drifted during write"
            )
        if directory_descriptor is not None:
            os.fsync(directory_descriptor)
    except Exception:
        if created:
            _remove_created_artifact(path, directory_descriptor=directory_descriptor)
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if directory_descriptor is not None:
            os.close(directory_descriptor)
    return content


def _event_files(state_dir: Path) -> tuple[tuple[int, str, Path], ...]:
    files: list[tuple[int, str, Path]] = []
    for child in state_dir.iterdir():
        if child.name == _KILL_FILE:
            continue
        match = _EVENT_FILE.fullmatch(child.name)
        if match is None:
            raise PersonalRuntimeConfigurationError(
                f"activation state contains an unknown artifact: {child.name}"
            )
        files.append((int(match.group("sequence")), match.group("event"), child))
    files.sort(key=lambda item: item[0])
    expected = list(range(1, len(files) + 1))
    if [sequence for sequence, _, _ in files] != expected:
        raise PersonalRuntimeConfigurationError("activation event sequence is not contiguous")
    return tuple(files)


def _status(
    state: PersonalRuntimeState,
    *,
    canary_id: str | None = None,
    config_sha256: str | None = None,
    plan_sha256: str | None = None,
    activated_at: str | None = None,
    expires_at: str | None = None,
    terminal_reason: str | None = None,
    last_event_sha256: str | None = None,
    events: int = 0,
    blockers: tuple[str, ...] = (),
    vetoes: tuple[str, ...] = (),
) -> PersonalRuntimeStatus:
    return PersonalRuntimeStatus(
        state=state,
        canary_id=canary_id,
        config_sha256=config_sha256,
        plan_sha256=plan_sha256,
        activated_at=activated_at,
        expires_at=expires_at,
        terminal_reason=terminal_reason,
        last_event_sha256=last_event_sha256,
        events=events,
        blockers=blockers,
        vetoes=vetoes,
    )


def _killed_status(kill_path: Path) -> PersonalRuntimeStatus:
    canary_id: str | None = None
    reason = "emergency_kill_switch"
    try:
        kill, _ = _load_json_file(kill_path, label="kill switch")
        raw_canary = kill.get("canary_id")
        if type(raw_canary) is str:
            canary_id = raw_canary
        raw_reason = kill.get("reason_code")
        if type(raw_reason) is str and raw_reason:
            reason = raw_reason
    except PersonalRuntimeConfigurationError:
        pass
    return _status(
        PersonalRuntimeState.KILLED,
        canary_id=canary_id,
        terminal_reason=reason,
        blockers=("irreversible kill switch is present",),
    )


def _load_activation_event(
    record: tuple[int, str, Path],
    *,
    index: int,
    previous_digest: str | None,
    expected_fields: frozenset[str],
) -> tuple[Mapping[str, object], bytes, str]:
    filename_sequence, filename_event, path = record
    event, content = _load_json_file(path, label=f"activation event {index}")
    if content != _canonical_bytes(event) + b"\n":
        raise PersonalRuntimeConfigurationError(
            "activation event must be canonical JSON with one trailing newline"
        )
    _exact_fields(event, expected_fields, name="activation event")
    if (
        filename_sequence != index
        or event.get("schema_version") != 1
        or event.get("sequence") != index
    ):
        raise PersonalRuntimeConfigurationError("activation event identity drifted")
    if event.get("previous_event_sha256") != previous_digest:
        raise PersonalRuntimeConfigurationError("activation hash chain drifted")
    _uuid(event, "event_id")
    _uuid(event, "canary_id")
    _digest(event, "config_sha256")
    _digest(event, "plan_sha256")
    _parse_timestamp(event.get("occurred_at"), name="occurred_at")
    event_type = _string(event, "event_type")
    if event_type != filename_event:
        raise PersonalRuntimeConfigurationError("activation event type does not match its filename")
    return event, content, event_type


def _parse_event_chain(
    files: tuple[tuple[int, str, Path], ...],
) -> tuple[Mapping[str, object], Mapping[str, object] | None, str]:
    previous_digest: str | None = None
    activate: Mapping[str, object] | None = None
    rollback: Mapping[str, object] | None = None
    expected_fields = frozenset(
        {
            "canary_id",
            "config_sha256",
            "event_id",
            "event_type",
            "expires_at",
            "occurred_at",
            "plan_sha256",
            "previous_event_sha256",
            "reason_code",
            "schema_version",
            "sequence",
        }
    )
    for index, record in enumerate(files, start=1):
        event, content, event_type = _load_activation_event(
            record,
            index=index,
            previous_digest=previous_digest,
            expected_fields=expected_fields,
        )
        if index == 1:
            if event_type != "activate" or event.get("reason_code") is not None:
                raise PersonalRuntimeConfigurationError("first event must be activate")
            _parse_timestamp(event.get("expires_at"), name="expires_at")
            activate = event
        else:
            if index != 2 or event_type != "rollback" or event.get("expires_at") is not None:
                raise PersonalRuntimeConfigurationError(
                    "only one terminal rollback may follow activation"
                )
            reason_code = event.get("reason_code")
            if type(reason_code) is not str or not reason_code:
                raise PersonalRuntimeConfigurationError("rollback requires a reason code")
            assert activate is not None
            if any(
                event.get(field) != activate.get(field)
                for field in ("canary_id", "config_sha256", "plan_sha256")
            ):
                raise PersonalRuntimeConfigurationError("rollback binding drifted")
            if _parse_timestamp(event.get("occurred_at"), name="occurred_at") < _parse_timestamp(
                activate.get("occurred_at"), name="occurred_at"
            ):
                raise PersonalRuntimeConfigurationError("rollback predates activation")
            rollback = event
        previous_digest = _sha256(content)
    if activate is None or previous_digest is None:
        raise PersonalRuntimeConfigurationError("activation event chain is empty")
    return activate, rollback, previous_digest


def personal_runtime_status_binding_valid(
    config: PersonalRuntimeCanaryConfig,
    status: PersonalRuntimeStatus,
) -> bool:
    """Bind a verified ledger to the exact config and bounded time window."""
    if (
        status.events < 1
        or status.canary_id != config.canary_id
        or status.config_sha256 != config.canonical_digest()
        or status.plan_sha256 != config.activation_plan().canonical_digest()
        or status.activated_at is None
        or status.expires_at is None
    ):
        return False
    try:
        activated_at = _parse_timestamp(status.activated_at, name="activated_at")
        expires_at = _parse_timestamp(status.expires_at, name="expires_at")
    except PersonalRuntimeConfigurationError:
        return False
    return expires_at - activated_at == timedelta(seconds=config.max_canary_seconds)


def read_personal_runtime_status(
    state_dir: Path,
    *,
    now: datetime | None = None,
) -> PersonalRuntimeStatus:
    """Verify and derive the append-only canary state.

    Any kill-marker presence wins before event parsing.  A damaged kill marker
    therefore still disables the Runtime instead of accidentally reopening it.
    """
    try:
        directory = _existing_state_directory(state_dir)
    except PersonalRuntimeConfigurationError as exc:
        return _status(PersonalRuntimeState.INVALID, vetoes=(str(exc),))
    if directory is None:
        return _status(
            PersonalRuntimeState.INACTIVE,
            blockers=("canary has not been activated",),
        )
    kill_path = directory / _KILL_FILE
    if os.path.lexists(kill_path):
        return _killed_status(kill_path)
    try:
        files = _event_files(directory)
        if not files:
            return _status(
                PersonalRuntimeState.INACTIVE,
                blockers=("canary has not been activated",),
            )
        activate, rollback, previous_digest = _parse_event_chain(files)
        activated_at = _string(activate, "occurred_at")
        expires_at = _string(activate, "expires_at")
        if rollback is not None:
            return _status(
                PersonalRuntimeState.ROLLED_BACK,
                canary_id=_string(activate, "canary_id"),
                config_sha256=_string(activate, "config_sha256"),
                plan_sha256=_string(activate, "plan_sha256"),
                activated_at=activated_at,
                expires_at=expires_at,
                terminal_reason=_string(rollback, "reason_code"),
                last_event_sha256=previous_digest,
                events=len(files),
                blockers=("canary was rolled back",),
            )
        current = _utc(now or datetime.now(UTC))
        activation = _parse_timestamp(activated_at, name="activated_at")
        expiry = _parse_timestamp(expires_at, name="expires_at")
        if current < activation:
            return _status(
                PersonalRuntimeState.INVALID,
                canary_id=_string(activate, "canary_id"),
                config_sha256=_string(activate, "config_sha256"),
                plan_sha256=_string(activate, "plan_sha256"),
                activated_at=activated_at,
                expires_at=expires_at,
                last_event_sha256=previous_digest,
                events=len(files),
                vetoes=("canary activation occurs in the future",),
            )
        state = PersonalRuntimeState.ACTIVE if current < expiry else PersonalRuntimeState.EXPIRED
        blockers = () if state is PersonalRuntimeState.ACTIVE else ("canary activation expired",)
        return _status(
            state,
            canary_id=_string(activate, "canary_id"),
            config_sha256=_string(activate, "config_sha256"),
            plan_sha256=_string(activate, "plan_sha256"),
            activated_at=activated_at,
            expires_at=expires_at,
            terminal_reason=None,
            last_event_sha256=previous_digest,
            events=len(files),
            blockers=blockers,
        )
    except PersonalRuntimeConfigurationError as exc:
        return _status(PersonalRuntimeState.INVALID, vetoes=(str(exc),))


def activate_personal_runtime_canary(
    config: PersonalRuntimeCanaryConfig,
    *,
    state_dir: Path,
    confirmed_plan_sha256: str,
    now: datetime | None = None,
) -> PersonalRuntimeStatus:
    plan = config.activation_plan()
    plan_sha256 = plan.canonical_digest()
    if confirmed_plan_sha256 != plan_sha256:
        raise PersonalRuntimeConfigurationError("confirmed plan digest does not match")
    directory = _ensure_state_directory(state_dir)
    status = read_personal_runtime_status(directory, now=now)
    if status.state is not PersonalRuntimeState.INACTIVE:
        raise PersonalRuntimeConfigurationError(
            "activation requires a new empty run-scoped state directory"
        )
    occurred_at = _utc(now or datetime.now(UTC))
    event = {
        "canary_id": config.canary_id,
        "config_sha256": config.canonical_digest(),
        "event_id": str(uuid4()),
        "event_type": "activate",
        "expires_at": _timestamp(occurred_at + timedelta(seconds=config.max_canary_seconds)),
        "occurred_at": _timestamp(occurred_at),
        "plan_sha256": plan_sha256,
        "previous_event_sha256": None,
        "reason_code": None,
        "schema_version": 1,
        "sequence": 1,
    }
    _write_exclusive(directory / "000001-activate.json", event)
    result = read_personal_runtime_status(directory, now=occurred_at)
    if result.state is not PersonalRuntimeState.ACTIVE:
        raise PersonalRuntimeConfigurationError("activation receipt did not verify")
    return result


def rollback_personal_runtime_canary(
    config: PersonalRuntimeCanaryConfig,
    *,
    state_dir: Path,
    reason_code: str,
    now: datetime | None = None,
) -> PersonalRuntimeStatus:
    if not reason_code or len(reason_code) > 128 or not re.fullmatch(r"[a-z0-9._-]+", reason_code):
        raise PersonalRuntimeConfigurationError("rollback reason code is invalid")
    directory = _ensure_state_directory(state_dir)
    status = read_personal_runtime_status(directory, now=now)
    if status.state not in {PersonalRuntimeState.ACTIVE, PersonalRuntimeState.EXPIRED}:
        raise PersonalRuntimeConfigurationError("rollback requires an active or expired canary")
    if (
        status.canary_id != config.canary_id
        or status.config_sha256 != config.canonical_digest()
        or status.plan_sha256 != config.activation_plan().canonical_digest()
        or status.last_event_sha256 is None
    ):
        raise PersonalRuntimeConfigurationError("rollback binding drifted")
    occurred_at = _utc(now or datetime.now(UTC))
    event = {
        "canary_id": config.canary_id,
        "config_sha256": config.canonical_digest(),
        "event_id": str(uuid4()),
        "event_type": "rollback",
        "expires_at": None,
        "occurred_at": _timestamp(occurred_at),
        "plan_sha256": config.activation_plan().canonical_digest(),
        "previous_event_sha256": status.last_event_sha256,
        "reason_code": reason_code,
        "schema_version": 1,
        "sequence": 2,
    }
    _write_exclusive(directory / "000002-rollback.json", event)
    result = read_personal_runtime_status(directory, now=occurred_at)
    if result.state is not PersonalRuntimeState.ROLLED_BACK:
        raise PersonalRuntimeConfigurationError("rollback receipt did not verify")
    return result


def kill_personal_runtime_canary(
    *,
    state_dir: Path,
    canary_id: str,
    reason_code: str,
    now: datetime | None = None,
) -> PersonalRuntimeStatus:
    """Create an irreversible kill marker without trusting the event ledger."""
    try:
        canonical_canary_id = str(UUID(canary_id))
    except ValueError as exc:
        raise PersonalRuntimeConfigurationError("canary_id must be a UUID") from exc
    if canonical_canary_id != canary_id:
        raise PersonalRuntimeConfigurationError("canary_id must be canonical")
    if not reason_code or len(reason_code) > 128 or not re.fullmatch(r"[a-z0-9._-]+", reason_code):
        raise PersonalRuntimeConfigurationError("kill reason code is invalid")
    directory = _ensure_state_directory(state_dir)
    marker = directory / _KILL_FILE
    if not marker.exists():
        _write_exclusive(
            marker,
            {
                "canary_id": canary_id,
                "event_id": str(uuid4()),
                "event_type": "kill",
                "occurred_at": _timestamp(_utc(now or datetime.now(UTC))),
                "reason_code": reason_code,
                "schema_version": 1,
            },
        )
    return read_personal_runtime_status(directory, now=now)


__all__ = [
    "PersonalRuntimeActivationPlan",
    "PersonalRuntimeCanaryConfig",
    "PersonalRuntimeConfigurationError",
    "PersonalRuntimeState",
    "PersonalRuntimeStatus",
    "activate_personal_runtime_canary",
    "kill_personal_runtime_canary",
    "load_personal_runtime_canary_config",
    "personal_runtime_status_binding_valid",
    "read_personal_runtime_status",
    "rollback_personal_runtime_canary",
]
