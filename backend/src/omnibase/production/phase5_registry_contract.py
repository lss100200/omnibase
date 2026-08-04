"""Offline P5.1A Agent Registry contract preflight.

P5.1A validates the AgentDefinition -> AgentVersion -> WorkspaceAgentBinding
contracts **without** any ORM, migration, database service, Browser API, SDK
call, Planner, Executor, worker, scheduler or runtime process.  It is a pure
offline contract gate: strict DTOs, closed-set JSON contracts, canonical
digests over raw UTF-8 bytes and fail-closed negative semantics.

The three Phase 5 feature gates stay disabled by default and P5.1A remains
``blocked/not_proven`` while P34.7 and P5.0 are not ``ready``.  This module
never reads the root ``.env``, never connects to a database or network, never
imports SQLAlchemy/FastAPI/Celery and never starts anything.
"""

from __future__ import annotations

import json
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from omnibase.production.composition import (
    AdmissionState,
    ConfigurationError,
    EvidenceReference,
    EvidenceStatus,
    GitSourceProvenance,
    SourceScope,
    build_git_source_provenance,
)
from omnibase.production.phase5_admission import (
    _REVISION_LINE,
    P347FormalState,
    _canonical_json,
    _only_keys,
    _relative_repo_path,
    _safe_repo_dir,
    _safe_repo_file,
    _sha256_bytes,
    _strict_list,
    _strict_object,
    _strict_string,
    discover_migration_head,
    resolve_feature_gates,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LOGICAL_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:Z|[+-][0-9]{2}:[0-9]{2})$"
)
_JSON_POINTER_REF_RE = re.compile(r"^#/(?:definitions|\$defs)/[A-Za-z0-9_.-]+$")

# Server-owned ceilings; a P5.1A contract may only tighten these.
_DEFAULT_CEILINGS = {
    "max_tokens": 10_000_000,
    "max_cost_units": 100_000,
    "max_wall_clock_seconds": 3_600,
    "max_tool_calls": 1_000,
    "max_concurrency": 64,
    "max_context_tokens": 2_000_000,
}

_BUDGET_KEYS = ("max_tokens", "max_cost_units", "max_wall_clock_seconds", "max_tool_calls")

_ALLOWED_INSTALLATION_SCOPES = frozenset({"tenant", "workspace"})
_ALLOWED_DEFINITION_STATES = frozenset({"draft", "active", "disabled", "revoked"})
_ALLOWED_VERSION_STATES = frozenset({"draft", "sealed", "deprecated", "revoked"})
_ALLOWED_BINDING_STATES = frozenset(
    {"pending_approval", "installed", "disabled", "superseded", "revoked"}
)
_ALLOWED_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
_RISK_RANK = {"low": 0, "medium": 1, "high": 2, "critical": 3}
_FORBIDDEN_TOOL_IDS = frozenset({"all", "any", "*"})
_APPROVAL_POLICY_VALUES = frozenset({"optional", "required"})

_JSON_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "title",
        "description",
        "properties",
        "items",
        "required",
        "enum",
        "const",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "format",
        "oneOf",
        "anyOf",
        "allOf",
        "not",
        "$ref",
        "$defs",
        "definitions",
    }
)
_JSON_SCHEMA_TYPES = frozenset(
    {"string", "number", "integer", "boolean", "object", "array", "null"}
)
_JSON_SCHEMA_MAX_DEPTH = 20


class RegistryContractError(ConfigurationError):
    """A P5.1A registry contract is unsafe, malformed or drifted."""


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class DefinitionState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


class VersionState(StrEnum):
    DRAFT = "draft"
    SEALED = "sealed"
    DEPRECATED = "deprecated"
    REVOKED = "revoked"


class BindingState(StrEnum):
    PENDING_APPROVAL = "pending_approval"
    INSTALLED = "installed"
    DISABLED = "disabled"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"


def _strict_uuid(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _UUID_RE.fullmatch(text) is None:
        raise RegistryContractError(f"{name} must be a strict lowercase UUID")
    return text


def _strict_positive_int(value: object, *, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise RegistryContractError(f"{name} must be a positive integer")
    return value


def _strict_timestamp(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _TIMESTAMP_RE.fullmatch(text) is None:
        raise RegistryContractError(f"{name} must be an ISO-8601 UTC timestamp")
    return text


def _strict_digest(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _SHA256_RE.fullmatch(text) is None:
        raise RegistryContractError(f"{name} must be a lowercase 64-character SHA-256")
    return text


def _strict_logical_key(value: object, *, name: str, forbidden: frozenset[str]) -> str:
    text = _strict_string(value, name=name)
    if _LOGICAL_KEY_RE.fullmatch(text) is None or text in forbidden:
        raise RegistryContractError(f"{name} must be a plain logical identifier without wildcards")
    return text


def _closed_state(value: object, *, name: str, allowed: frozenset[str]) -> str:
    text = _strict_string(value, name=name)
    if text not in allowed:
        raise RegistryContractError(
            f"{name} has an unknown or malformed state; allowed: {', '.join(sorted(allowed))}"
        )
    return text


def _unique_strings(values: object, *, name: str) -> tuple[str, ...]:
    items = tuple(
        _strict_string(item, name=f"{name}[]") for item in _strict_list(values, name=name)
    )
    if len(items) != len(set(items)):
        raise RegistryContractError(f"{name} must not contain duplicates")
    return items


def _validate_controlled_json_schema(
    value: object, *, name: str, depth: int = 0
) -> dict[str, object]:
    """Validate a JSON Schema restricted to a safe offline subset."""
    if depth > _JSON_SCHEMA_MAX_DEPTH:
        raise RegistryContractError(f"{name} exceeds the maximum JSON Schema depth")
    if not isinstance(value, dict):
        raise RegistryContractError(f"{name} must be a JSON Schema object")
    unexpected = sorted(set(value) - _JSON_SCHEMA_KEYWORDS)
    if unexpected:
        raise RegistryContractError(
            f"{name} uses non-controlled JSON Schema keywords: {', '.join(unexpected)}"
        )
    schema_type = value.get("type")
    if schema_type is not None and (
        not isinstance(schema_type, str) or schema_type not in _JSON_SCHEMA_TYPES
    ):
        raise RegistryContractError(f"{name}.type must be a closed-set JSON Schema type")
    for key in ("title", "description", "pattern", "format"):
        if key in value and (not isinstance(value[key], str) or not value[key]):
            raise RegistryContractError(f"{name}.{key} must be a non-empty string")
    for key in (
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
    ):
        if key in value and (
            not isinstance(value[key], (int, float))
            or isinstance(value[key], bool)
            or value[key] != value[key]  # NaN
            or value[key] in (float("inf"), float("-inf"))
        ):
            raise RegistryContractError(f"{name}.{key} must be a finite number")
    ref = value.get("$ref")
    if ref is not None and (
        not isinstance(ref, str) or _JSON_POINTER_REF_RE.fullmatch(ref) is None
    ):
        raise RegistryContractError(
            f"{name}.$ref must be a local JSON pointer, not a URL or file reference"
        )
    _validate_schema_children(value, name=name, depth=depth)
    return value


def _validate_schema_children(value: dict[str, object], *, name: str, depth: int) -> None:
    for key in ("properties", "$defs", "definitions"):
        if key not in value:
            continue
        subschemas = value[key]
        if not isinstance(subschemas, dict) or not subschemas:
            raise RegistryContractError(f"{name}.{key} must be a non-empty object")
        for sub_name, sub_schema in subschemas.items():
            if not isinstance(sub_name, str) or not sub_name:
                raise RegistryContractError(f"{name}.{key} keys must be non-empty strings")
            _validate_controlled_json_schema(
                sub_schema, name=f"{name}.{key}.{sub_name}", depth=depth + 1
            )
    _validate_schema_branches(value, name=name, depth=depth)
    _validate_schema_scalars(value, name=name)


def _validate_schema_branches(value: dict[str, object], *, name: str, depth: int) -> None:
    for key in ("items", "not"):
        if key in value:
            _validate_controlled_json_schema(value[key], name=f"{name}.{key}", depth=depth + 1)
    for key in ("oneOf", "anyOf", "allOf"):
        if key not in value:
            continue
        branches = _strict_list(value[key], name=f"{name}.{key}")
        if not branches:
            raise RegistryContractError(f"{name}.{key} must not be empty")
        for index, branch in enumerate(branches):
            _validate_controlled_json_schema(branch, name=f"{name}.{key}[{index}]", depth=depth + 1)


def _validate_schema_scalars(value: dict[str, object], *, name: str) -> None:
    if "required" in value:
        required = _unique_strings(value["required"], name=f"{name}.required")
        for item in required:
            if not _LOGICAL_KEY_RE.fullmatch(item):
                raise RegistryContractError(f"{name}.required contains an invalid property name")
    if "enum" in value:
        enum_values = _strict_list(value["enum"], name=f"{name}.enum")
        if not enum_values:
            raise RegistryContractError(f"{name}.enum must not be empty")
        seen: set[str] = set()
        for index, scalar in enumerate(enum_values):
            if not isinstance(scalar, (str, int, float)) or isinstance(scalar, bool):
                raise RegistryContractError(f"{name}.enum[{index}] must be a scalar value")
            canonical = repr(scalar)
            if canonical in seen:
                raise RegistryContractError(f"{name}.enum contains duplicates")
            seen.add(canonical)
    if "const" in value and not isinstance(value["const"], (str, int, float)):
        raise RegistryContractError(f"{name}.const must be a scalar value")


@dataclass(frozen=True, slots=True)
class DefaultBudgetPolicy:
    max_tokens: int
    max_cost_units: int
    max_wall_clock_seconds: int
    max_tool_calls: int

    @classmethod
    def from_mapping(
        cls, value: object, *, name: str, ceilings: Mapping[str, int]
    ) -> DefaultBudgetPolicy:
        data = _strict_object(value, name=name)
        _only_keys(data, set(_BUDGET_KEYS), name=name)
        parsed: dict[str, int] = {}
        for key in _BUDGET_KEYS:
            parsed[key] = _strict_positive_int(data.get(key), name=f"{name}.{key}")
            if parsed[key] > ceilings[key]:
                raise RegistryContractError(
                    f"{name}.{key} exceeds the server-owned ceiling {ceilings[key]}"
                )
        return cls(**parsed)

    def to_dict(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in _BUDGET_KEYS}


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    schema_version: int
    agent_definition_id: str
    tenant_id: str
    stable_logical_key: str
    display_name: str
    description: str | None
    risk_level: RiskLevel
    allowed_installation_scopes: tuple[str, ...]
    definition_state: DefinitionState
    created_by: str
    created_at: str
    metadata_version: int

    @classmethod
    def from_mapping(cls, value: object) -> AgentDefinition:
        data = _strict_object(value, name="agent_definition")
        _only_keys(
            data,
            {
                "schema_version",
                "agent_definition_id",
                "tenant_id",
                "stable_logical_key",
                "display_name",
                "description",
                "risk_level",
                "allowed_installation_scopes",
                "definition_state",
                "created_by",
                "created_at",
                "metadata_version",
            },
            name="agent_definition",
        )
        if data.get("schema_version") != 1:
            raise RegistryContractError("agent_definition.schema_version must be 1")
        scopes = tuple(
            _closed_state(
                item,
                name="agent_definition.allowed_installation_scopes[]",
                allowed=_ALLOWED_INSTALLATION_SCOPES,
            )
            for item in _strict_list(
                data.get("allowed_installation_scopes"),
                name="agent_definition.allowed_installation_scopes",
            )
        )
        if not scopes or len(scopes) != len(set(scopes)):
            raise RegistryContractError(
                "agent_definition.allowed_installation_scopes must be non-empty and unique"
            )
        risk_text = _closed_state(
            data.get("risk_level"),
            name="agent_definition.risk_level",
            allowed=_ALLOWED_RISK_LEVELS,
        )
        description = data.get("description")
        if description is not None and (not isinstance(description, str) or not description):
            raise RegistryContractError("agent_definition.description must be a non-empty string")
        return cls(
            schema_version=1,
            agent_definition_id=_strict_uuid(
                data.get("agent_definition_id"), name="agent_definition.agent_definition_id"
            ),
            tenant_id=_strict_uuid(data.get("tenant_id"), name="agent_definition.tenant_id"),
            stable_logical_key=_strict_logical_key(
                data.get("stable_logical_key"),
                name="agent_definition.stable_logical_key",
                forbidden=frozenset(),
            ),
            display_name=_strict_string(
                data.get("display_name"), name="agent_definition.display_name"
            ),
            description=description,
            risk_level=RiskLevel(risk_text),
            allowed_installation_scopes=scopes,
            definition_state=DefinitionState(
                _closed_state(
                    data.get("definition_state"),
                    name="agent_definition.definition_state",
                    allowed=_ALLOWED_DEFINITION_STATES,
                )
            ),
            created_by=_strict_uuid(data.get("created_by"), name="agent_definition.created_by"),
            created_at=_strict_timestamp(
                data.get("created_at"), name="agent_definition.created_at"
            ),
            metadata_version=_strict_positive_int(
                data.get("metadata_version"), name="agent_definition.metadata_version"
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "agent_definition_id": self.agent_definition_id,
            "tenant_id": self.tenant_id,
            "stable_logical_key": self.stable_logical_key,
            "display_name": self.display_name,
            "description": self.description,
            "risk_level": self.risk_level.value,
            "allowed_installation_scopes": list(self.allowed_installation_scopes),
            "definition_state": self.definition_state.value,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "metadata_version": self.metadata_version,
        }

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))


@dataclass(frozen=True, slots=True)
class AgentVersionManifest:
    schema_version: int
    agent_version_id: str
    agent_definition_id: str
    tenant_id: str
    version: str
    manifest_digest: str
    model_policy_id: str
    instructions_digest: str
    max_context_tokens: int
    allowed_tool_ids: tuple[str, ...]
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    risk_level: RiskLevel
    memory_policy_id: str | None
    max_concurrency: int
    default_budget: DefaultBudgetPolicy
    version_state: VersionState
    created_by: str
    created_at: str

    @classmethod
    def from_mapping(cls, value: object, *, ceilings: Mapping[str, int]) -> AgentVersionManifest:
        data = _strict_object(value, name="agent_version")
        _only_keys(
            data,
            {
                "schema_version",
                "agent_version_id",
                "agent_definition_id",
                "tenant_id",
                "version",
                "manifest_digest",
                "model_policy_id",
                "instructions_digest",
                "max_context_tokens",
                "allowed_tool_ids",
                "input_schema",
                "output_schema",
                "risk_level",
                "memory_policy_id",
                "max_concurrency",
                "default_budget",
                "version_state",
                "created_by",
                "created_at",
            },
            name="agent_version",
        )
        if data.get("schema_version") != 1:
            raise RegistryContractError("agent_version.schema_version must be 1")
        version_text = _strict_string(data.get("version"), name="agent_version.version")
        if _SEMVER_RE.fullmatch(version_text) is None:
            raise RegistryContractError("agent_version.version must be a strict version string")
        declared_digest = _strict_digest(
            data.get("manifest_digest"), name="agent_version.manifest_digest"
        )
        memory_policy = data.get("memory_policy_id")
        if memory_policy is not None:
            memory_policy = _strict_uuid(memory_policy, name="agent_version.memory_policy_id")
        tool_ids = tuple(
            _strict_logical_key(
                item,
                name="agent_version.allowed_tool_ids[]",
                forbidden=_FORBIDDEN_TOOL_IDS,
            )
            for item in _strict_list(
                data.get("allowed_tool_ids"), name="agent_version.allowed_tool_ids"
            )
        )
        if len(tool_ids) != len(set(tool_ids)):
            raise RegistryContractError(
                "agent_version.allowed_tool_ids must not contain duplicates"
            )
        context_tokens = _strict_positive_int(
            data.get("max_context_tokens"), name="agent_version.max_context_tokens"
        )
        if context_tokens > ceilings["max_context_tokens"]:
            raise RegistryContractError(
                "agent_version.max_context_tokens exceeds the server-owned ceiling"
            )
        concurrency = _strict_positive_int(
            data.get("max_concurrency"), name="agent_version.max_concurrency"
        )
        if concurrency > ceilings["max_concurrency"]:
            raise RegistryContractError(
                "agent_version.max_concurrency exceeds the server-owned ceiling"
            )
        input_schema = _validate_controlled_json_schema(
            data.get("input_schema"), name="agent_version.input_schema"
        )
        output_schema = _validate_controlled_json_schema(
            data.get("output_schema"), name="agent_version.output_schema"
        )
        instructions_digest = _strict_digest(
            data.get("instructions_digest"), name="agent_version.instructions_digest"
        )
        manifest = cls(
            schema_version=1,
            agent_version_id=_strict_uuid(
                data.get("agent_version_id"), name="agent_version.agent_version_id"
            ),
            agent_definition_id=_strict_uuid(
                data.get("agent_definition_id"), name="agent_version.agent_definition_id"
            ),
            tenant_id=_strict_uuid(data.get("tenant_id"), name="agent_version.tenant_id"),
            version=version_text,
            manifest_digest=declared_digest,
            model_policy_id=_strict_uuid(
                data.get("model_policy_id"), name="agent_version.model_policy_id"
            ),
            instructions_digest=instructions_digest,
            max_context_tokens=context_tokens,
            allowed_tool_ids=tool_ids,
            input_schema=input_schema,
            output_schema=output_schema,
            risk_level=RiskLevel(
                _closed_state(
                    data.get("risk_level"),
                    name="agent_version.risk_level",
                    allowed=_ALLOWED_RISK_LEVELS,
                )
            ),
            memory_policy_id=memory_policy,
            max_concurrency=concurrency,
            default_budget=DefaultBudgetPolicy.from_mapping(
                data.get("default_budget"),
                name="agent_version.default_budget",
                ceilings=ceilings,
            ),
            version_state=VersionState(
                _closed_state(
                    data.get("version_state"),
                    name="agent_version.version_state",
                    allowed=_ALLOWED_VERSION_STATES,
                )
            ),
            created_by=_strict_uuid(data.get("created_by"), name="agent_version.created_by"),
            created_at=_strict_timestamp(data.get("created_at"), name="agent_version.created_at"),
        )
        actual_digest = manifest.canonical_digest()
        if actual_digest != declared_digest:
            raise RegistryContractError(
                "agent_version.manifest_digest does not match the canonical manifest bytes"
            )
        return manifest

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "agent_version_id": self.agent_version_id,
            "agent_definition_id": self.agent_definition_id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "manifest_digest": self.manifest_digest,
            "model_policy_id": self.model_policy_id,
            "instructions_digest": self.instructions_digest,
            "max_context_tokens": self.max_context_tokens,
            "allowed_tool_ids": list(self.allowed_tool_ids),
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "risk_level": self.risk_level.value,
            "memory_policy_id": self.memory_policy_id,
            "max_concurrency": self.max_concurrency,
            "default_budget": self.default_budget.to_dict(),
            "version_state": self.version_state.value,
            "created_by": self.created_by,
            "created_at": self.created_at,
        }

    def canonical_digest(self) -> str:
        payload = dict(self.to_dict())
        payload.pop("manifest_digest")
        return _sha256_bytes(_canonical_json(payload))


@dataclass(frozen=True, slots=True)
class WorkspaceAgentBinding:
    schema_version: int
    workspace_agent_binding_id: str
    tenant_id: str
    workspace_id: str
    workspace_generation: int
    agent_definition_id: str
    agent_version_id: str
    agent_version_digest: str
    installation_state: BindingState
    resource_scopes: tuple[str, ...]
    default_budget_policy: DefaultBudgetPolicy
    installed_by: str
    approval_id: str | None
    created_at: str
    disabled_at: str | None
    superseded_by: str | None

    @classmethod
    def from_mapping(cls, value: object, *, ceilings: Mapping[str, int]) -> WorkspaceAgentBinding:
        data = _strict_object(value, name="workspace_agent_binding")
        _only_keys(
            data,
            {
                "schema_version",
                "workspace_agent_binding_id",
                "tenant_id",
                "workspace_id",
                "workspace_generation",
                "agent_definition_id",
                "agent_version_id",
                "agent_version_digest",
                "installation_state",
                "resource_scopes",
                "default_budget_policy",
                "installed_by",
                "approval_id",
                "created_at",
                "disabled_at",
                "superseded_by",
            },
            name="workspace_agent_binding",
        )
        if data.get("schema_version") != 1:
            raise RegistryContractError("workspace_agent_binding.schema_version must be 1")
        generation = data.get("workspace_generation")
        if not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
            raise RegistryContractError(
                "workspace_agent_binding.workspace_generation must be a positive integer"
            )
        scopes = tuple(
            _strict_logical_key(
                item,
                name="workspace_agent_binding.resource_scopes[]",
                forbidden=frozenset(),
            )
            for item in _strict_list(
                data.get("resource_scopes"), name="workspace_agent_binding.resource_scopes"
            )
        )
        if not scopes or len(scopes) != len(set(scopes)):
            raise RegistryContractError(
                "workspace_agent_binding.resource_scopes must be non-empty and unique"
            )
        approval = data.get("approval_id")
        if approval is not None:
            approval = _strict_uuid(approval, name="workspace_agent_binding.approval_id")
        disabled_at = data.get("disabled_at")
        if disabled_at is not None:
            disabled_at = _strict_timestamp(disabled_at, name="workspace_agent_binding.disabled_at")
        superseded_by = data.get("superseded_by")
        if superseded_by is not None:
            superseded_by = _strict_uuid(
                superseded_by, name="workspace_agent_binding.superseded_by"
            )
        state = BindingState(
            _closed_state(
                data.get("installation_state"),
                name="workspace_agent_binding.installation_state",
                allowed=_ALLOWED_BINDING_STATES,
            )
        )
        binding = cls(
            schema_version=1,
            workspace_agent_binding_id=_strict_uuid(
                data.get("workspace_agent_binding_id"),
                name="workspace_agent_binding.workspace_agent_binding_id",
            ),
            tenant_id=_strict_uuid(data.get("tenant_id"), name="workspace_agent_binding.tenant_id"),
            workspace_id=_strict_uuid(
                data.get("workspace_id"), name="workspace_agent_binding.workspace_id"
            ),
            workspace_generation=generation,
            agent_definition_id=_strict_uuid(
                data.get("agent_definition_id"),
                name="workspace_agent_binding.agent_definition_id",
            ),
            agent_version_id=_strict_uuid(
                data.get("agent_version_id"), name="workspace_agent_binding.agent_version_id"
            ),
            agent_version_digest=_strict_digest(
                data.get("agent_version_digest"),
                name="workspace_agent_binding.agent_version_digest",
            ),
            installation_state=state,
            resource_scopes=scopes,
            default_budget_policy=DefaultBudgetPolicy.from_mapping(
                data.get("default_budget_policy"),
                name="workspace_agent_binding.default_budget_policy",
                ceilings=ceilings,
            ),
            installed_by=_strict_uuid(
                data.get("installed_by"), name="workspace_agent_binding.installed_by"
            ),
            approval_id=approval,
            created_at=_strict_timestamp(
                data.get("created_at"), name="workspace_agent_binding.created_at"
            ),
            disabled_at=disabled_at,
            superseded_by=superseded_by,
        )
        binding._validate_state_fields()
        return binding

    def _validate_state_fields(self) -> None:
        if self.installation_state is BindingState.DISABLED and self.disabled_at is None:
            raise RegistryContractError(
                "workspace_agent_binding.disabled_at is required when installation_state is disabled"
            )
        if self.installation_state is not BindingState.DISABLED and self.disabled_at is not None:
            raise RegistryContractError(
                "workspace_agent_binding.disabled_at is only allowed when installation_state is disabled"
            )
        if self.installation_state is BindingState.SUPERSEDED and self.superseded_by is None:
            raise RegistryContractError(
                "workspace_agent_binding.superseded_by is required when installation_state is superseded"
            )
        if (
            self.installation_state is not BindingState.SUPERSEDED
            and self.superseded_by is not None
        ):
            raise RegistryContractError(
                "workspace_agent_binding.superseded_by is only allowed when installation_state is superseded"
            )
        if self.superseded_by == self.workspace_agent_binding_id:
            raise RegistryContractError(
                "workspace_agent_binding.superseded_by must not reference itself"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "workspace_agent_binding_id": self.workspace_agent_binding_id,
            "tenant_id": self.tenant_id,
            "workspace_id": self.workspace_id,
            "workspace_generation": self.workspace_generation,
            "agent_definition_id": self.agent_definition_id,
            "agent_version_id": self.agent_version_id,
            "agent_version_digest": self.agent_version_digest,
            "installation_state": self.installation_state.value,
            "resource_scopes": list(self.resource_scopes),
            "default_budget_policy": self.default_budget_policy.to_dict(),
            "installed_by": self.installed_by,
            "approval_id": self.approval_id,
            "created_at": self.created_at,
            "disabled_at": self.disabled_at,
            "superseded_by": self.superseded_by,
        }

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))


@dataclass(frozen=True, slots=True)
class BudgetCeilings:
    max_tokens: int
    max_cost_units: int
    max_wall_clock_seconds: int
    max_tool_calls: int
    max_concurrency: int
    max_context_tokens: int

    @classmethod
    def from_mapping(cls, value: object) -> BudgetCeilings:
        data = _strict_object(value, name="budget_ceilings")
        _only_keys(data, set(_DEFAULT_CEILINGS), name="budget_ceilings")
        parsed: dict[str, int] = {}
        for key, ceiling in _DEFAULT_CEILINGS.items():
            parsed[key] = _strict_positive_int(data.get(key), name=f"budget_ceilings.{key}")
            if parsed[key] > ceiling:
                raise RegistryContractError(
                    f"budget_ceilings.{key} may only tighten the server-owned ceiling {ceiling}"
                )
        return cls(**parsed)

    def as_mapping(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in _DEFAULT_CEILINGS}


@dataclass(frozen=True, slots=True)
class RegistryContractConfig:
    schema_version: int
    phase: str
    feature_gates: dict[str, object]
    p34_7_formal_state: P347FormalState
    p34_7_decision: str  # SealedFileRef serialized below
    p34_7_decision_sha256: str
    p5_0_formal_state: P347FormalState
    p5_0_admission_path: str
    p5_0_admission_sha256: str
    source: SourceScope
    evidence: tuple[EvidenceReference, ...]
    ceilings: BudgetCeilings
    approval_policy: dict[str, str]
    forbidden_source_paths: tuple[str, ...]
    baseline_migration_revisions: tuple[str, ...]
    sealed_contracts: tuple[tuple[str, str, str], ...]  # (name, path, sha256)
    openapi_snapshot_path: str
    openapi_snapshot_sha256: str
    definitions: tuple[AgentDefinition, ...]
    versions: tuple[AgentVersionManifest, ...]
    bindings: tuple[WorkspaceAgentBinding, ...]
    critical_veto: int

    @classmethod
    def from_mapping(cls, value: object) -> RegistryContractConfig:
        data = _strict_object(value, name="configuration")
        _only_keys(
            data,
            {
                "schema_version",
                "phase",
                "feature_gates",
                "p34_7",
                "p5_0",
                "source",
                "evidence",
                "budget_ceilings",
                "approval_policy",
                "forbidden_source_paths",
                "baseline_migration_revisions",
                "sealed_contracts",
                "openapi_snapshot",
                "registry_contracts",
                "critical_veto",
            },
            name="configuration",
        )
        if data.get("schema_version") != 1:
            raise RegistryContractError("configuration.schema_version must be 1")
        if data.get("phase") != "P5.1A":
            raise RegistryContractError("configuration.phase must be P5.1A")
        gates = _strict_object(data.get("feature_gates"), name="feature_gates")
        _only_keys(
            gates,
            {"agent_runtime_enabled", "agent_planner_enabled", "multi_agent_enabled"},
            name="feature_gates",
        )
        if gates.get("agent_runtime_enabled") is not False:
            raise RegistryContractError(
                "P5.1A contract requires the Agent Runtime feature gate to be disabled"
            )
        if (
            gates.get("agent_planner_enabled") is not False
            or gates.get("multi_agent_enabled") is not False
        ):
            raise RegistryContractError(
                "P5.1A contract requires every Phase 5 feature gate to be disabled"
            )
        p34_7 = _strict_object(data.get("p34_7"), name="p34_7")
        _only_keys(p34_7, {"formal_state", "decision"}, name="p34_7")
        p34_7_state = _parse_formal_state(p34_7.get("formal_state"), name="p34_7.formal_state")
        p34_7_decision = _strict_object(p34_7.get("decision"), name="p34_7.decision")
        _only_keys(p34_7_decision, {"path", "sha256"}, name="p34_7.decision")
        p5_0 = _strict_object(data.get("p5_0"), name="p5_0")
        _only_keys(p5_0, {"formal_state", "admission_contract"}, name="p5_0")
        p5_0_state = _parse_formal_state(p5_0.get("formal_state"), name="p5_0.formal_state")
        admission_contract = _strict_object(
            p5_0.get("admission_contract"), name="p5_0.admission_contract"
        )
        _only_keys(admission_contract, {"path", "sha256"}, name="p5_0.admission_contract")
        ceilings = BudgetCeilings.from_mapping(data.get("budget_ceilings"))
        approval_policy = _parse_approval_policy(data.get("approval_policy"))
        forbidden = tuple(
            _relative_repo_path(item, name="forbidden_source_paths[]")
            for item in _strict_list(
                data.get("forbidden_source_paths"), name="forbidden_source_paths"
            )
        )
        if not forbidden or len(forbidden) != len(set(forbidden)):
            raise RegistryContractError("forbidden_source_paths must be non-empty and unique")
        baseline = tuple(
            _strict_string(item, name="baseline_migration_revisions[]")
            for item in _strict_list(
                data.get("baseline_migration_revisions"),
                name="baseline_migration_revisions",
            )
        )
        if not baseline or len(baseline) != len(set(baseline)):
            raise RegistryContractError("baseline_migration_revisions must be non-empty and unique")
        sealed = tuple(
            _parse_sealed_contract(item)
            for item in _strict_list(data.get("sealed_contracts"), name="sealed_contracts")
        )
        if not sealed or len({name for name, _, _ in sealed}) != len(sealed):
            raise RegistryContractError("sealed_contracts must be non-empty with unique names")
        openapi = _strict_object(data.get("openapi_snapshot"), name="openapi_snapshot")
        _only_keys(openapi, {"path", "sha256"}, name="openapi_snapshot")
        registry = _strict_object(data.get("registry_contracts"), name="registry_contracts")
        _only_keys(
            registry,
            {"agent_definitions", "agent_versions", "workspace_agent_bindings"},
            name="registry_contracts",
        )
        definitions = tuple(
            AgentDefinition.from_mapping(item)
            for item in _strict_list(
                registry.get("agent_definitions"), name="registry_contracts.agent_definitions"
            )
        )
        versions = tuple(
            AgentVersionManifest.from_mapping(item, ceilings=ceilings.as_mapping())
            for item in _strict_list(
                registry.get("agent_versions"), name="registry_contracts.agent_versions"
            )
        )
        bindings = tuple(
            WorkspaceAgentBinding.from_mapping(item, ceilings=ceilings.as_mapping())
            for item in _strict_list(
                registry.get("workspace_agent_bindings"),
                name="registry_contracts.workspace_agent_bindings",
            )
        )
        if not definitions or not versions or not bindings:
            raise RegistryContractError(
                "registry_contracts must include at least one definition, version and binding"
            )
        evidence = tuple(
            EvidenceReference.from_mapping(item)
            for item in _strict_list(data.get("evidence"), name="evidence")
        )
        if not evidence or len({item.evidence_id for item in evidence}) != len(evidence):
            raise RegistryContractError("evidence IDs must be non-empty and unique")
        critical = _strict_object(data.get("critical_veto"), name="critical_veto")
        _only_keys(critical, {"expected"}, name="critical_veto")
        if critical.get("expected") != 0:
            raise RegistryContractError("critical_veto.expected must be exactly 0")
        config = cls(
            schema_version=1,
            phase="P5.1A",
            feature_gates=gates,
            p34_7_formal_state=p34_7_state,
            p34_7_decision=_strict_string(p34_7_decision.get("path"), name="p34_7.decision.path"),
            p34_7_decision_sha256=_strict_digest(
                p34_7_decision.get("sha256"), name="p34_7.decision.sha256"
            ),
            p5_0_formal_state=p5_0_state,
            p5_0_admission_path=_relative_repo_path(
                admission_contract.get("path"), name="p5_0.admission_contract.path"
            ),
            p5_0_admission_sha256=_strict_digest(
                admission_contract.get("sha256"), name="p5_0.admission_contract.sha256"
            ),
            source=SourceScope.from_mapping(data.get("source")),
            evidence=evidence,
            ceilings=ceilings,
            approval_policy=approval_policy,
            forbidden_source_paths=forbidden,
            baseline_migration_revisions=baseline,
            sealed_contracts=sealed,
            openapi_snapshot_path=_relative_repo_path(
                openapi.get("path"), name="openapi_snapshot.path"
            ),
            openapi_snapshot_sha256=_strict_digest(
                openapi.get("sha256"), name="openapi_snapshot.sha256"
            ),
            definitions=definitions,
            versions=versions,
            bindings=bindings,
            critical_veto=0,
        )
        config._validate_registry_references()
        return config

    def _validate_registry_references(self) -> None:
        definitions_by_id = {item.agent_definition_id: item for item in self.definitions}
        versions_by_id = {item.agent_version_id: item for item in self.versions}
        binding_ids = {item.workspace_agent_binding_id for item in self.bindings}
        if len(definitions_by_id) != len(self.definitions):
            raise RegistryContractError("agent_definition IDs must be unique")
        if len(versions_by_id) != len(self.versions):
            raise RegistryContractError("agent_version IDs must be unique")
        if len(binding_ids) != len(self.bindings):
            raise RegistryContractError("workspace_agent_binding IDs must be unique")

        self._validate_registry_natural_keys()
        for version in self.versions:
            self._validate_version_reference(version, definitions_by_id)
        for binding in self.bindings:
            self._validate_binding_reference(binding, definitions_by_id, versions_by_id)
        self._validate_revoked_definition_bindings()

    def _validate_registry_natural_keys(self) -> None:
        definition_keys = {(item.tenant_id, item.stable_logical_key) for item in self.definitions}
        if len(definition_keys) != len(self.definitions):
            raise RegistryContractError(
                "agent_definition stable_logical_key values must be unique within a tenant"
            )
        version_keys = {
            (item.tenant_id, item.agent_definition_id, item.version) for item in self.versions
        }
        if len(version_keys) != len(self.versions):
            raise RegistryContractError(
                "agent_version version values must be unique within a tenant definition"
            )

    @staticmethod
    def _validate_version_reference(
        version: AgentVersionManifest,
        definitions_by_id: Mapping[str, AgentDefinition],
    ) -> None:
        definition = definitions_by_id.get(version.agent_definition_id)
        if definition is None:
            raise RegistryContractError(
                f"agent_version {version.agent_version_id} references an unknown "
                f"agent_definition {version.agent_definition_id}"
            )
        if version.tenant_id != definition.tenant_id:
            raise RegistryContractError(
                f"agent_version {version.agent_version_id} crosses the tenant boundary of "
                f"agent_definition {version.agent_definition_id}"
            )
        if _RISK_RANK[version.risk_level.value] < _RISK_RANK[definition.risk_level.value]:
            raise RegistryContractError(
                f"agent_version {version.agent_version_id} must not downgrade the risk level "
                f"of agent_definition {version.agent_definition_id}"
            )

    def _validate_binding_reference(
        self,
        binding: WorkspaceAgentBinding,
        definitions_by_id: Mapping[str, AgentDefinition],
        versions_by_id: Mapping[str, AgentVersionManifest],
    ) -> None:
        definition = definitions_by_id.get(binding.agent_definition_id)
        if definition is None:
            raise RegistryContractError(
                f"workspace_agent_binding {binding.workspace_agent_binding_id} references an "
                f"unknown agent_definition {binding.agent_definition_id}"
            )
        bound_version = versions_by_id.get(binding.agent_version_id)
        if bound_version is None:
            raise RegistryContractError(
                f"workspace_agent_binding {binding.workspace_agent_binding_id} references an "
                f"unknown agent_version {binding.agent_version_id}"
            )
        if binding.agent_definition_id != bound_version.agent_definition_id:
            raise RegistryContractError(
                f"workspace_agent_binding {binding.workspace_agent_binding_id} binds an "
                "agent_version from a different agent_definition"
            )
        if (
            binding.tenant_id != definition.tenant_id
            or binding.tenant_id != bound_version.tenant_id
        ):
            raise RegistryContractError(
                f"workspace_agent_binding {binding.workspace_agent_binding_id} crosses a "
                "tenant boundary"
            )
        if "workspace" not in definition.allowed_installation_scopes:
            raise RegistryContractError(
                f"workspace_agent_binding {binding.workspace_agent_binding_id} references an "
                "agent_definition that does not allow workspace installation"
            )
        if bound_version.canonical_digest() != binding.agent_version_digest:
            raise RegistryContractError(
                f"workspace_agent_binding {binding.workspace_agent_binding_id} binds "
                f"agent_version {binding.agent_version_id} to a drifted digest"
            )
        if (
            self.approval_policy[bound_version.risk_level.value] == "required"
            and binding.approval_id is None
        ):
            raise RegistryContractError(
                f"workspace_agent_binding {binding.workspace_agent_binding_id} requires an "
                f"approval_id for risk level {bound_version.risk_level.value}"
            )

    def _validate_revoked_definition_bindings(self) -> None:
        for definition in self.definitions:
            if definition.definition_state is DefinitionState.REVOKED:
                for binding in self.bindings:
                    if binding.agent_definition_id == definition.agent_definition_id:
                        raise RegistryContractError(
                            f"workspace_agent_binding {binding.workspace_agent_binding_id} "
                            f"references revoked agent_definition {definition.agent_definition_id}"
                        )

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "feature_gates": self.feature_gates,
            "p34_7": {
                "formal_state": self.p34_7_formal_state.value,
                "decision": {
                    "path": self.p34_7_decision,
                    "sha256": self.p34_7_decision_sha256,
                },
            },
            "p5_0": {
                "formal_state": self.p5_0_formal_state.value,
                "admission_contract": {
                    "path": self.p5_0_admission_path,
                    "sha256": self.p5_0_admission_sha256,
                },
            },
            "source": {
                "expected_repository": self.source.expected_repository,
                "tracked_pathspecs": list(self.source.tracked_pathspecs),
                "require_clean_checkout": self.source.require_clean_checkout,
            },
            "evidence": [
                {
                    "id": item.evidence_id,
                    "status": item.status.value,
                    "path": item.path,
                    "sha256": item.sha256,
                    "assertions": dict(item.assertions),
                    "required_for_activation": item.required_for_activation,
                }
                for item in self.evidence
            ],
            "budget_ceilings": self.ceilings.as_mapping(),
            "approval_policy": dict(self.approval_policy),
            "forbidden_source_paths": list(self.forbidden_source_paths),
            "baseline_migration_revisions": list(self.baseline_migration_revisions),
            "sealed_contracts": [
                {"name": name, "path": path, "sha256": digest}
                for name, path, digest in self.sealed_contracts
            ],
            "openapi_snapshot": {
                "path": self.openapi_snapshot_path,
                "sha256": self.openapi_snapshot_sha256,
            },
            "registry_contracts": {
                "agent_definitions": [item.to_dict() for item in self.definitions],
                "agent_versions": [item.to_dict() for item in self.versions],
                "workspace_agent_bindings": [item.to_dict() for item in self.bindings],
            },
            "critical_veto": {"expected": self.critical_veto},
        }


def _parse_formal_state(value: object, *, name: str) -> P347FormalState:
    text = _strict_string(value, name=name)
    try:
        return P347FormalState(text)
    except ValueError as exc:
        raise RegistryContractError(f"{name} has an invalid state") from exc


def _parse_approval_policy(value: object) -> dict[str, str]:
    data = _strict_object(value, name="approval_policy")
    _only_keys(data, {"low", "medium", "high", "critical"}, name="approval_policy")
    policy: dict[str, str] = {}
    for level in ("low", "medium", "high", "critical"):
        raw = _strict_string(data.get(level), name=f"approval_policy.{level}")
        if raw not in _APPROVAL_POLICY_VALUES:
            raise RegistryContractError(f"approval_policy.{level} must be optional or required")
        policy[level] = raw
    if policy["high"] != "required" or policy["critical"] != "required":
        raise RegistryContractError(
            "approval_policy.high and approval_policy.critical must be required"
        )
    return policy


def _parse_sealed_contract(value: object) -> tuple[str, str, str]:
    data = _strict_object(value, name="sealed_contracts[]")
    _only_keys(data, {"name", "path", "sha256"}, name="sealed_contracts[]")
    name = _strict_string(data.get("name"), name="sealed_contracts[].name")
    path = _relative_repo_path(data.get("path"), name="sealed_contracts[].path")
    digest = _strict_digest(data.get("sha256"), name="sealed_contracts[].sha256")
    return name, path, digest


def discover_migration_revisions(repo_root: Path, directory: str) -> tuple[str, ...]:
    """Parse every revision id in the migration directory without importing files."""
    versions_dir = _safe_repo_dir(repo_root, directory)
    root = repo_root.resolve(strict=True)
    revisions: list[str] = []
    for discovered_path in sorted(versions_dir.glob("*.py")):
        relative_path = discovered_path.relative_to(root).as_posix()
        path = _safe_repo_file(root, relative_path)
        match = _REVISION_LINE.search(path.read_text(encoding="utf-8"))
        if match is None:
            raise RegistryContractError(f"migration file has no revision id: {path.name}")
        revisions.append(match.group(1))
    if not revisions:
        raise RegistryContractError(f"migration directory contains no revisions: {directory}")
    if len(revisions) != len(set(revisions)):
        raise RegistryContractError("migration chain contains duplicate revision ids")
    return tuple(revisions)


@dataclass(frozen=True, slots=True)
class RegistryContractReport:
    state: AdmissionState
    activation_allowed: bool
    contract_valid: bool
    configuration_sha256: str
    feature_gates: dict[str, bool]
    p34_7_formal_state: str
    p5_0_formal_state: str
    source: GitSourceProvenance | None
    passed_evidence: tuple[str, ...]
    blockers: tuple[str, ...]
    vetoes: tuple[str, ...]
    migration_head: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "gate": "P5.1A Agent Registry contract preflight",
            "state": self.state.value,
            "activation_allowed": self.activation_allowed,
            "contract_valid": self.contract_valid,
            "configuration_sha256": self.configuration_sha256,
            "feature_gates": self.feature_gates,
            "p34_7_formal_state": self.p34_7_formal_state,
            "p5_0_formal_state": self.p5_0_formal_state,
            "source": None if self.source is None else self.source.to_dict(),
            "passed_evidence": list(self.passed_evidence),
            "blockers": list(self.blockers),
            "vetoes": list(self.vetoes),
            "migration_head": self.migration_head,
            "registry_runtime_implemented": False,
            "database_schema_applied": False,
            "public_api_exposed": False,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "external_network_accessed": False,
            "agent_registry_runtime_created": False,
            "agent_api_exposed": False,
            "agent_runtime_activated": False,
            "planner_activated": False,
            "executor_activated": False,
            "worker_or_scheduler_started": False,
        }


class RegistryContractGate:
    """Offline P5.1A preflight; never ready until P34.7/P5.0 are ready."""

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve(strict=True)

    def validate_only(self, config: RegistryContractConfig) -> RegistryContractReport:
        blockers = [
            "formal Phase 5 registry contract verification was not executed",
            "Agent Registry production database schema is not applied/proven",
            "Agent Invocation/Runtime API is not implemented",
            "Workspace installation public/runtime surface is not implemented",
        ]
        if config.p34_7_formal_state is not P347FormalState.READY:
            blockers.append(f"P34.7 formal state is not ready: {config.p34_7_formal_state.value}")
        if config.p5_0_formal_state is not P347FormalState.READY:
            blockers.append(
                f"P5.0 admission formal state is not ready: {config.p5_0_formal_state.value}"
            )
        blockers.extend(
            f"{item.evidence_id}: {item.status.value}"
            for item in config.evidence
            if item.required_for_activation and item.status is not EvidenceStatus.PASSED
        )
        return RegistryContractReport(
            state=AdmissionState.BLOCKED,
            activation_allowed=False,
            contract_valid=True,
            configuration_sha256=config.canonical_digest(),
            feature_gates=_feature_gates_dict(config),
            p34_7_formal_state=config.p34_7_formal_state.value,
            p5_0_formal_state=config.p5_0_formal_state.value,
            source=None,
            passed_evidence=(),
            blockers=tuple(blockers),
            vetoes=(),
            migration_head=None,
        )

    def verify(
        self,
        config: RegistryContractConfig,
        *,
        source: GitSourceProvenance | None = None,
        gate_values: Mapping[str, object] | None = None,
    ) -> RegistryContractReport:
        provenance = source or build_git_source_provenance(self._repo_root, config.source)
        blockers: list[str] = []
        vetoes: list[str] = []
        if config.source.require_clean_checkout and not provenance.clean:
            vetoes.append("Phase 5 registry contract requires a clean checkout")
        try:
            gates = resolve_feature_gates(gate_values or {})
        except ConfigurationError as exc:
            vetoes.append(f"feature gates: {exc}")
            resolved = {
                "agent_runtime_enabled": False,
                "agent_planner_enabled": False,
                "multi_agent_enabled": False,
            }
        else:
            resolved = gates.to_dict()
            if gates.any_enabled:
                blockers.append("Phase 5 feature gates must remain disabled")
            else:
                blockers.append(
                    "Agent Runtime gate remains disabled: runtime activation is not authorized"
                )
        if config.p34_7_formal_state is not P347FormalState.READY:
            blockers.append(f"P34.7 formal state is not ready: {config.p34_7_formal_state.value}")
        if config.p5_0_formal_state is not P347FormalState.READY:
            blockers.append(
                f"P5.0 admission formal state is not ready: {config.p5_0_formal_state.value}"
            )
        blockers.extend(
            f"{item.evidence_id}: {item.status.value}"
            for item in config.evidence
            if item.required_for_activation and item.status is not EvidenceStatus.PASSED
        )
        blockers.extend(
            [
                "Agent Registry production database schema is not applied/proven",
                "Agent Invocation/Runtime API is not implemented",
                "Workspace installation public/runtime surface is not implemented",
            ]
        )
        try:
            migration_head = self._verify_source_boundaries(config)
        except (
            RegistryContractError,
            ConfigurationError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
        ) as exc:
            vetoes.append(f"source boundaries: {exc}")
            migration_head = None
        try:
            self._verify_sealed_files(config)
        except (
            RegistryContractError,
            ConfigurationError,
            json.JSONDecodeError,
            UnicodeDecodeError,
            OSError,
        ) as exc:
            vetoes.append(f"sealed contracts: {exc}")
        # P5.1A is a preflight: runtime, database and API surfaces are not
        # implemented, so blocked/not_proven is the only correct state.
        state = AdmissionState.INVALID if vetoes else AdmissionState.BLOCKED
        return RegistryContractReport(
            state=state,
            activation_allowed=False,
            contract_valid=not vetoes,
            configuration_sha256=config.canonical_digest(),
            feature_gates=resolved,
            p34_7_formal_state=config.p34_7_formal_state.value,
            p5_0_formal_state=config.p5_0_formal_state.value,
            source=provenance,
            passed_evidence=(),
            blockers=tuple(blockers),
            vetoes=tuple(vetoes),
            migration_head=migration_head,
        )

    def _verify_source_boundaries(self, config: RegistryContractConfig) -> str:
        migration_head = discover_migration_head(
            self._repo_root, "backend/src/omnibase/migrations/versions"
        )
        current_revisions = discover_migration_revisions(
            self._repo_root, "backend/src/omnibase/migrations/versions"
        )
        if set(current_revisions) != set(config.baseline_migration_revisions):
            raise RegistryContractError("migration revision set drifted from the sealed baseline")
        for relative in config.forbidden_source_paths:
            candidate = self._repo_root / relative
            if _lexists(candidate):
                raise RegistryContractError(
                    f"forbidden source path exists: {relative} (attempted runtime/ORM/API)"
                )
        openapi_path = _safe_repo_file(self._repo_root, config.openapi_snapshot_path)
        content = openapi_path.read_bytes()
        if _sha256_bytes(content) != config.openapi_snapshot_sha256:
            raise RegistryContractError("openapi snapshot SHA-256 drifted")
        payload = json.loads(content.decode("utf-8"))
        paths = payload.get("paths")
        if isinstance(paths, dict):
            for path_name in paths:
                if "agent" in path_name.lower():
                    raise RegistryContractError(
                        f"openapi snapshot exposes an agent endpoint: {path_name}"
                    )
        return migration_head

    def _verify_sealed_files(self, config: RegistryContractConfig) -> None:
        for name, relative, digest in config.sealed_contracts:
            path = _safe_repo_file(self._repo_root, relative)
            if _sha256_bytes(path.read_bytes()) != digest:
                raise RegistryContractError(f"sealed contract drifted: {name}")
        for relative, digest in (
            (config.p34_7_decision, config.p34_7_decision_sha256),
            (config.p5_0_admission_path, config.p5_0_admission_sha256),
        ):
            path = _safe_repo_file(self._repo_root, relative)
            if _sha256_bytes(path.read_bytes()) != digest:
                raise RegistryContractError(f"sealed reference drifted: {relative}")


def _lexists(path: Path) -> bool:
    try:
        path.lstat()
    except OSError:
        return False
    return True


def _feature_gates_dict(config: RegistryContractConfig) -> dict[str, bool]:
    return {
        "agent_runtime_enabled": False,
        "agent_planner_enabled": False,
        "multi_agent_enabled": False,
    }


def load_registry_contract_config(path: Path) -> RegistryContractConfig:
    metadata = path.lstat()
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise RegistryContractError("P5.1A contract configuration must be a regular non-link file")

    def _reject_constant(value: str) -> None:
        raise RegistryContractError(f"contract contains a non-finite number: {value}")

    payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    return RegistryContractConfig.from_mapping(payload)


__all__ = [
    "AgentDefinition",
    "AgentVersionManifest",
    "BindingState",
    "BudgetCeilings",
    "DefaultBudgetPolicy",
    "DefinitionState",
    "RegistryContractConfig",
    "RegistryContractError",
    "RegistryContractGate",
    "RegistryContractReport",
    "RiskLevel",
    "VersionState",
    "WorkspaceAgentBinding",
    "discover_migration_revisions",
    "load_registry_contract_config",
]
