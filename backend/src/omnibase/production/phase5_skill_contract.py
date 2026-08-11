"""Compile-only P5.6A native Skill contract admission.

P5.6A establishes strict, provider-neutral contracts for first-party
``SkillDefinition`` and immutable ``SkillVersion`` manifests.  It deliberately
does not create persistence, Browser APIs, Workspace installation, a Skill
runtime, MCP, Planner dispatch, a worker, a scheduler, a migration or any code
execution path.

The gate remains ``blocked/not_proven`` even when the static contract is valid.
P34.7, the typed single-Agent executor, Skill provenance, persistence and
runtime activation must be proven independently before a Skill can execute.
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
from pathlib import Path

from omnibase.production.composition import (
    AdmissionState,
    ConfigurationError,
    GitSourceProvenance,
    SourceScope,
    build_git_source_provenance,
)
from omnibase.production.phase5_admission import (
    FeatureGateConfigurationError,
    FeatureGateResolution,
    _canonical_json,
    _only_keys,
    _sha256_bytes,
    _strict_bool,
    _strict_list,
    _strict_object,
    _strict_string,
    discover_migration_head,
    resolve_feature_gates,
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_LOGICAL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,95}$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?$"
)
_FORBIDDEN_IDENTIFIERS = frozenset({"*", "all", "any", "root", "host"})
_ALLOWED_RISK_LEVELS = frozenset({"low", "medium", "high", "critical"})
_ALLOWED_INSTALLATION_SCOPES = frozenset({"workspace"})
_ALLOWED_DEFINITION_STATES = frozenset({"draft", "active", "disabled", "revoked"})
_ALLOWED_VERSION_STATES = frozenset(
    {"draft", "tested", "approved", "published", "deprecated", "revoked"}
)
_ALLOWED_SKILL_KINDS = frozenset({"instruction", "workflow", "script"})
_ALLOWED_SIGNATURE_STATES = frozenset({"unverified", "verified"})
_ALLOWED_NETWORK_POLICIES = frozenset({"deny"})
_ALLOWED_SCHEMA_TYPES = frozenset(
    {"string", "number", "integer", "boolean", "object", "array", "null"}
)
_ALLOWED_SCHEMA_KEYS = frozenset(
    {
        "$defs",
        "$ref",
        "additionalProperties",
        "const",
        "description",
        "enum",
        "items",
        "maxItems",
        "maxLength",
        "maximum",
        "minItems",
        "minLength",
        "minimum",
        "pattern",
        "properties",
        "required",
        "title",
        "type",
    }
)
_MAX_SCHEMA_DEPTH = 16
_MAX_INSTRUCTION_CHARS = 16_000
_MAX_PATTERN_CHARS = 256
_MAX_SCHEMA_COLLECTION_SIZE = 256
_MAX_VERIFICATION_ARGUMENT_CHARS = 256
_MAX_VERIFICATION_ARGUMENTS = 32
_FORBIDDEN_ARGUMENT_TOKENS = (
    "\n",
    "\r",
    ";",
    "&&",
    "||",
    "`",
    "$(",
    ">",
    "<",
    "http://",
    "https://",
)
_ALLOWED_VERIFICATION_PROFILES = frozenset(
    {
        "paired-eval",
        "pytest",
        "python-validator",
        "rollback-rehearsal",
        "sbom-verify",
        "secret-scan",
        "signature-verify",
    }
)


class SkillContractError(ConfigurationError):
    """The P5.6A Skill contract is malformed, unsafe or drifted."""


class SkillKind(StrEnum):
    INSTRUCTION = "instruction"
    WORKFLOW = "workflow"
    SCRIPT = "script"


class SkillDefinitionState(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    REVOKED = "revoked"


class SkillVersionState(StrEnum):
    DRAFT = "draft"
    TESTED = "tested"
    APPROVED = "approved"
    PUBLISHED = "published"
    DEPRECATED = "deprecated"
    REVOKED = "revoked"


def _strict_uuid(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _UUID_RE.fullmatch(text) is None:
        raise SkillContractError(f"{name} must be a strict lowercase UUID")
    return text


def _strict_digest(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _SHA256_RE.fullmatch(text) is None:
        raise SkillContractError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _strict_logical_id(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _LOGICAL_ID_RE.fullmatch(text) is None or text in _FORBIDDEN_IDENTIFIERS:
        raise SkillContractError(f"{name} must be a bounded logical identifier without wildcards")
    return text


def _strict_positive_int(value: object, *, name: str, ceiling: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SkillContractError(f"{name} must be a positive integer")
    if value > ceiling:
        raise SkillContractError(f"{name} exceeds the server-owned ceiling {ceiling}")
    return value


def _strict_non_negative_int(value: object, *, name: str, ceiling: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise SkillContractError(f"{name} must be a non-negative integer")
    if value > ceiling:
        raise SkillContractError(f"{name} exceeds the server-owned ceiling {ceiling}")
    return value


def _semver_release(value: str) -> tuple[int, int, int]:
    core = value.split("-", 1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)


def _closed_value(value: object, *, name: str, allowed: frozenset[str]) -> str:
    text = _strict_string(value, name=name)
    if text not in allowed:
        raise SkillContractError(f"{name} must be one of: {', '.join(sorted(allowed))}")
    return text


def _unique_logical_ids(value: object, *, name: str) -> tuple[str, ...]:
    items = tuple(
        _strict_logical_id(item, name=f"{name}[]") for item in _strict_list(value, name=name)
    )
    if len(items) != len(set(items)):
        raise SkillContractError(f"{name} must not contain duplicates")
    return tuple(sorted(items))


def _unique_digests(value: object, *, name: str) -> tuple[str, ...]:
    items = tuple(_strict_digest(item, name=f"{name}[]") for item in _strict_list(value, name=name))
    if len(items) != len(set(items)):
        raise SkillContractError(f"{name} must not contain duplicates")
    return tuple(sorted(items))


def _validate_schema_bounds(data: dict[str, object], *, name: str) -> None:
    for lower, upper in (
        ("minimum", "maximum"),
        ("minLength", "maxLength"),
        ("minItems", "maxItems"),
    ):
        for key in (lower, upper):
            current = data.get(key)
            if key in data and (not isinstance(current, int) or isinstance(current, bool)):
                raise SkillContractError(f"{name}.{key} must be an integer")
            if isinstance(current, int) and not isinstance(current, bool) and current < 0:
                raise SkillContractError(f"{name}.{key} must be non-negative")
        lower_value = data.get(lower)
        upper_value = data.get(upper)
        if (
            isinstance(lower_value, int)
            and isinstance(upper_value, int)
            and lower_value > upper_value
        ):
            raise SkillContractError(f"{name}.{lower} must not exceed {name}.{upper}")


def _validate_schema_literals(data: dict[str, object], *, name: str) -> None:
    pattern = data.get("pattern")
    if pattern is not None:
        if not isinstance(pattern, str) or len(pattern) > _MAX_PATTERN_CHARS:
            raise SkillContractError(f"{name}.pattern must be a bounded string")
        try:
            re.compile(pattern)
        except re.error as exc:
            raise SkillContractError(f"{name}.pattern is not a valid regular expression") from exc
    enum = data.get("enum")
    if enum is not None:
        values = _strict_list(enum, name=f"{name}.enum")
        if not values or len(values) > _MAX_SCHEMA_COLLECTION_SIZE:
            raise SkillContractError(f"{name}.enum must be non-empty and bounded")
        encoded = [_canonical_json(item) for item in values]
        if len(encoded) != len(set(encoded)):
            raise SkillContractError(f"{name}.enum must not contain duplicates")


def _validate_schema_shape(data: dict[str, object], *, name: str) -> None:
    schema_type = data.get("type")
    if data.get("additionalProperties") not in (None, False):
        raise SkillContractError(f"{name}.additionalProperties must be false when present")
    if schema_type == "object" and data.get("additionalProperties") is not False:
        raise SkillContractError(f"{name}.additionalProperties must be false for object schemas")
    if data.get("properties") is not None and schema_type != "object":
        raise SkillContractError(f"{name}.properties requires type=object")
    if data.get("items") is not None and schema_type != "array":
        raise SkillContractError(f"{name}.items requires type=array")


def _validate_schema_children(data: dict[str, object], *, name: str, depth: int) -> set[str]:
    references: set[str] = set()
    properties = data.get("properties")
    if properties is not None:
        prop_data = _strict_object(properties, name=f"{name}.properties")
        for key, schema in prop_data.items():
            _strict_logical_id(key, name=f"{name}.properties key")
            child_refs = _validate_json_schema(
                schema, name=f"{name}.properties.{key}", depth=depth + 1
            )
            references.update(child_refs)
    definitions_value = data.get("$defs")
    if definitions_value is not None:
        definition_data = _strict_object(definitions_value, name=f"{name}.$defs")
        for key, schema in definition_data.items():
            _strict_logical_id(key, name=f"{name}.$defs key")
            child_refs = _validate_json_schema(schema, name=f"{name}.$defs.{key}", depth=depth + 1)
            references.update(child_refs)
    items = data.get("items")
    if items is not None:
        child_refs = _validate_json_schema(items, name=f"{name}.items", depth=depth + 1)
        references.update(child_refs)
    return references


def _validate_json_schema(value: object, *, name: str, depth: int = 0) -> set[str]:
    if depth > _MAX_SCHEMA_DEPTH:
        raise SkillContractError(f"{name} exceeds the maximum JSON Schema depth")
    data = _strict_object(value, name=name)
    unexpected = sorted(set(data) - _ALLOWED_SCHEMA_KEYS)
    if unexpected:
        raise SkillContractError(
            f"{name} uses non-controlled JSON Schema fields: {', '.join(unexpected)}"
        )
    schema_type = data.get("type")
    if schema_type is not None and (
        not isinstance(schema_type, str) or schema_type not in _ALLOWED_SCHEMA_TYPES
    ):
        raise SkillContractError(f"{name}.type must be a closed-set JSON Schema type")
    _validate_schema_shape(data, name=name)
    _validate_schema_literals(data, name=name)
    properties = data.get("properties")
    required = data.get("required")
    if required is not None:
        required_items = tuple(
            _strict_logical_id(item, name=f"{name}.required[]")
            for item in _strict_list(required, name=f"{name}.required")
        )
        if len(required_items) != len(set(required_items)):
            raise SkillContractError(f"{name}.required must not contain duplicates")
        property_names = (
            set(_strict_object(properties, name=f"{name}.properties"))
            if properties is not None
            else set()
        )
        if not set(required_items).issubset(property_names):
            raise SkillContractError(f"{name}.required must reference declared properties")
    ref = data.get("$ref")
    if ref is not None and (
        not isinstance(ref, str) or re.fullmatch(r"#/\$defs/[A-Za-z0-9._-]+", ref) is None
    ):
        raise SkillContractError(f"{name}.$ref must remain inside the local $defs object")
    _validate_schema_bounds(data, name=name)
    references = _validate_schema_children(data, name=name, depth=depth)
    if isinstance(ref, str):
        references.add(ref.removeprefix("#/$defs/"))
    return references


def _strict_json_schema(value: object, *, name: str) -> dict[str, object]:
    schema = _strict_object(value, name=name)
    references = _validate_json_schema(schema, name=name)
    definitions_value = schema.get("$defs", {})
    definitions = set(_strict_object(definitions_value, name=f"{name}.$defs"))
    missing = sorted(references - definitions)
    if missing:
        raise SkillContractError(
            f"{name} references missing local definitions: {', '.join(missing)}"
        )
    definition_data = _strict_object(definitions_value, name=f"{name}.$defs")
    edges = {
        key: _validate_json_schema(definition, name=f"{name}.$defs.{key}", depth=1) & definitions
        for key, definition in definition_data.items()
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise SkillContractError(f"{name} contains a cyclic local $ref graph")
        if key in visited:
            return
        visiting.add(key)
        for dependency in edges[key]:
            visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for key in sorted(definitions):
        visit(key)
    return schema


@dataclass(frozen=True, slots=True)
class SkillBudget:
    max_context_tokens: int
    max_output_tokens: int
    max_tool_calls: int
    max_wall_clock_seconds: int
    max_cost_units: int

    @classmethod
    def from_mapping(cls, value: object) -> SkillBudget:
        data = _strict_object(value, name="skill_version.budget")
        _only_keys(
            data,
            {
                "max_context_tokens",
                "max_output_tokens",
                "max_tool_calls",
                "max_wall_clock_seconds",
                "max_cost_units",
            },
            name="skill_version.budget",
        )
        return cls(
            max_context_tokens=_strict_positive_int(
                data.get("max_context_tokens"),
                name="skill_version.budget.max_context_tokens",
                ceiling=131_072,
            ),
            max_output_tokens=_strict_positive_int(
                data.get("max_output_tokens"),
                name="skill_version.budget.max_output_tokens",
                ceiling=16_384,
            ),
            max_tool_calls=_strict_non_negative_int(
                data.get("max_tool_calls"),
                name="skill_version.budget.max_tool_calls",
                ceiling=64,
            ),
            max_wall_clock_seconds=_strict_positive_int(
                data.get("max_wall_clock_seconds"),
                name="skill_version.budget.max_wall_clock_seconds",
                ceiling=900,
            ),
            max_cost_units=_strict_positive_int(
                data.get("max_cost_units"),
                name="skill_version.budget.max_cost_units",
                ceiling=100_000,
            ),
        )

    def to_dict(self) -> dict[str, int]:
        return {
            "max_context_tokens": self.max_context_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_tool_calls": self.max_tool_calls,
            "max_wall_clock_seconds": self.max_wall_clock_seconds,
            "max_cost_units": self.max_cost_units,
        }


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    action_id: str
    resource_kind: str
    resource_scope: str
    required: bool

    @classmethod
    def from_mapping(cls, value: object) -> CapabilityRequirement:
        data = _strict_object(value, name="skill_version.capability_requirements[]")
        _only_keys(
            data,
            {"action_id", "resource_kind", "resource_scope", "required"},
            name="skill_version.capability_requirements[]",
        )
        return cls(
            action_id=_strict_logical_id(
                data.get("action_id"), name="skill_version.capability_requirements[].action_id"
            ),
            resource_kind=_strict_logical_id(
                data.get("resource_kind"),
                name="skill_version.capability_requirements[].resource_kind",
            ),
            resource_scope=_closed_value(
                data.get("resource_scope"),
                name="skill_version.capability_requirements[].resource_scope",
                allowed=frozenset({"workspace", "run"}),
            ),
            required=_strict_bool(
                data.get("required"),
                name="skill_version.capability_requirements[].required",
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "resource_kind": self.resource_kind,
            "resource_scope": self.resource_scope,
            "required": self.required,
        }


@dataclass(frozen=True, slots=True)
class VerificationCommand:
    command_id: str
    profile: str
    arguments: tuple[str, ...]
    network_allowed: bool

    @classmethod
    def from_mapping(cls, value: object) -> VerificationCommand:
        data = _strict_object(value, name="skill_versions[].verification_commands[]")
        _only_keys(
            data,
            {"command_id", "profile", "arguments", "network_allowed"},
            name="skill_versions[].verification_commands[]",
        )
        arguments = tuple(
            _strict_string(item, name="skill_versions[].verification_commands[].arguments[]")
            for item in _strict_list(
                data.get("arguments"),
                name="skill_versions[].verification_commands[].arguments",
            )
        )
        if len(arguments) > _MAX_VERIFICATION_ARGUMENTS:
            raise SkillContractError("verification command has too many arguments")
        for argument in arguments:
            normalized = argument.replace("\\", "/")
            if (
                len(argument) > _MAX_VERIFICATION_ARGUMENT_CHARS
                or any(token in argument for token in _FORBIDDEN_ARGUMENT_TOKENS)
                or ".." in normalized.split("/")
                or normalized.lower() in {".env", "./.env"}
            ):
                raise SkillContractError(
                    "verification command arguments must be bounded, local and shell-free"
                )
        network_allowed = _strict_bool(
            data.get("network_allowed"),
            name="skill_versions[].verification_commands[].network_allowed",
        )
        if network_allowed:
            raise SkillContractError("P5.6A verification commands cannot use the network")
        return cls(
            command_id=_strict_logical_id(
                data.get("command_id"),
                name="skill_versions[].verification_commands[].command_id",
            ),
            profile=_closed_value(
                data.get("profile"),
                name="skill_versions[].verification_commands[].profile",
                allowed=_ALLOWED_VERIFICATION_PROFILES,
            ),
            arguments=arguments,
            network_allowed=False,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "command_id": self.command_id,
            "profile": self.profile,
            "arguments": list(self.arguments),
            "network_allowed": self.network_allowed,
        }


def _parse_instructions(data: dict[str, object]) -> tuple[str, str]:
    instructions = _strict_string(data.get("instructions"), name="skill_versions[].instructions")
    if len(instructions) > _MAX_INSTRUCTION_CHARS:
        raise SkillContractError("skill_versions[].instructions exceeds the bounded size")
    digest = _strict_digest(
        data.get("instructions_digest"), name="skill_versions[].instructions_digest"
    )
    if digest != hashlib.sha256(instructions.encode("utf-8")).hexdigest():
        raise SkillContractError("skill_versions[].instructions_digest does not match UTF-8 bytes")
    return instructions, digest


def _parse_capability_requirements(data: dict[str, object]) -> tuple[CapabilityRequirement, ...]:
    requirements = tuple(
        CapabilityRequirement.from_mapping(item)
        for item in _strict_list(
            data.get("capability_requirements"),
            name="skill_versions[].capability_requirements",
        )
    )
    keys = {(item.action_id, item.resource_kind, item.resource_scope) for item in requirements}
    if len(keys) != len(requirements):
        raise SkillContractError("skill_versions[].capability_requirements contains duplicates")
    return tuple(
        sorted(
            requirements,
            key=lambda item: (
                item.action_id,
                item.resource_kind,
                item.resource_scope,
                item.required,
            ),
        )
    )


def _parse_verification_commands(
    data: dict[str, object],
) -> tuple[VerificationCommand, ...]:
    commands = tuple(
        VerificationCommand.from_mapping(item)
        for item in _strict_list(
            data.get("verification_commands"), name="skill_versions[].verification_commands"
        )
    )
    command_ids = {item.command_id for item in commands}
    if not commands or len(command_ids) != len(commands):
        raise SkillContractError(
            "skill_versions[].verification_commands must be non-empty with unique command IDs"
        )
    return tuple(sorted(commands, key=lambda item: item.command_id))


def _validate_kind_and_state(
    *,
    kind: SkillKind,
    state: SkillVersionState,
    required_tools: tuple[str, ...],
    requirements: tuple[CapabilityRequirement, ...],
    budget: SkillBudget,
) -> None:
    if kind is SkillKind.INSTRUCTION and (
        required_tools or requirements or budget.max_tool_calls != 0
    ):
        raise SkillContractError(
            "instruction Skills cannot request tools, capabilities or tool-call budget"
        )
    if kind is not SkillKind.INSTRUCTION and state in {
        SkillVersionState.APPROVED,
        SkillVersionState.PUBLISHED,
    }:
        raise SkillContractError(
            "workflow/script Skills cannot be approved or published before P5.4 runtime proof"
        )
    if state in {SkillVersionState.APPROVED, SkillVersionState.PUBLISHED}:
        raise SkillContractError(
            "P5.6A cannot claim approved or published Skill versions without sealed review evidence"
        )


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    skill_definition_id: str
    stable_logical_key: str
    display_name: str
    description: str
    definition_state: SkillDefinitionState
    allowed_installation_scopes: tuple[str, ...]
    first_party: bool

    @classmethod
    def from_mapping(cls, value: object) -> SkillDefinition:
        data = _strict_object(value, name="skill_definitions[]")
        _only_keys(
            data,
            {
                "skill_definition_id",
                "stable_logical_key",
                "display_name",
                "description",
                "definition_state",
                "allowed_installation_scopes",
                "first_party",
            },
            name="skill_definitions[]",
        )
        scopes = tuple(
            _closed_value(
                item,
                name="skill_definitions[].allowed_installation_scopes[]",
                allowed=_ALLOWED_INSTALLATION_SCOPES,
            )
            for item in _strict_list(
                data.get("allowed_installation_scopes"),
                name="skill_definitions[].allowed_installation_scopes",
            )
        )
        if scopes != ("workspace",):
            raise SkillContractError("P5.6A definitions must be installable only in a Workspace")
        first_party = _strict_bool(data.get("first_party"), name="skill_definitions[].first_party")
        if not first_party:
            raise SkillContractError("P5.6A accepts first-party Skill definitions only")
        return cls(
            skill_definition_id=_strict_uuid(
                data.get("skill_definition_id"), name="skill_definitions[].skill_definition_id"
            ),
            stable_logical_key=_strict_logical_id(
                data.get("stable_logical_key"), name="skill_definitions[].stable_logical_key"
            ),
            display_name=_strict_string(
                data.get("display_name"), name="skill_definitions[].display_name"
            ),
            description=_strict_string(
                data.get("description"), name="skill_definitions[].description"
            ),
            definition_state=SkillDefinitionState(
                _closed_value(
                    data.get("definition_state"),
                    name="skill_definitions[].definition_state",
                    allowed=_ALLOWED_DEFINITION_STATES,
                )
            ),
            allowed_installation_scopes=scopes,
            first_party=first_party,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "skill_definition_id": self.skill_definition_id,
            "stable_logical_key": self.stable_logical_key,
            "display_name": self.display_name,
            "description": self.description,
            "definition_state": self.definition_state.value,
            "allowed_installation_scopes": list(self.allowed_installation_scopes),
            "first_party": self.first_party,
        }


@dataclass(frozen=True, slots=True)
class SkillVersion:
    skill_version_id: str
    skill_definition_id: str
    version: str
    version_state: SkillVersionState
    kind: SkillKind
    instructions: str
    instructions_digest: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    required_tool_ids: tuple[str, ...]
    capability_requirements: tuple[CapabilityRequirement, ...]
    supported_agent_version_digests: tuple[str, ...]
    risk_level: str
    budget: SkillBudget
    network_policy: str
    secrets_allowed: bool
    source_sha256: str
    dependency_lock_sha256: str
    sbom_sha256: str
    signature_status: str
    verification_commands: tuple[VerificationCommand, ...]
    rollback_version_id: str | None

    @classmethod
    def from_mapping(cls, value: object) -> SkillVersion:
        data = _strict_object(value, name="skill_versions[]")
        _only_keys(
            data,
            {
                "skill_version_id",
                "skill_definition_id",
                "version",
                "version_state",
                "kind",
                "instructions",
                "instructions_digest",
                "input_schema",
                "output_schema",
                "required_tool_ids",
                "capability_requirements",
                "supported_agent_version_digests",
                "risk_level",
                "budget",
                "network_policy",
                "secrets_allowed",
                "source_sha256",
                "dependency_lock_sha256",
                "sbom_sha256",
                "signature_status",
                "verification_commands",
                "rollback_version_id",
            },
            name="skill_versions[]",
        )
        version = _strict_string(data.get("version"), name="skill_versions[].version")
        if _SEMVER_RE.fullmatch(version) is None:
            raise SkillContractError("skill_versions[].version must be strict SemVer")
        kind = SkillKind(
            _closed_value(
                data.get("kind"), name="skill_versions[].kind", allowed=_ALLOWED_SKILL_KINDS
            )
        )
        state = SkillVersionState(
            _closed_value(
                data.get("version_state"),
                name="skill_versions[].version_state",
                allowed=_ALLOWED_VERSION_STATES,
            )
        )
        instructions, instructions_digest = _parse_instructions(data)
        required_tools = _unique_logical_ids(
            data.get("required_tool_ids"), name="skill_versions[].required_tool_ids"
        )
        requirements = _parse_capability_requirements(data)
        budget = SkillBudget.from_mapping(data.get("budget"))
        _validate_kind_and_state(
            kind=kind,
            state=state,
            required_tools=required_tools,
            requirements=requirements,
            budget=budget,
        )
        secrets_allowed = _strict_bool(
            data.get("secrets_allowed"), name="skill_versions[].secrets_allowed"
        )
        if secrets_allowed:
            raise SkillContractError("Skill manifests must not request or contain secrets")
        signature_status = _closed_value(
            data.get("signature_status"),
            name="skill_versions[].signature_status",
            allowed=_ALLOWED_SIGNATURE_STATES,
        )
        verification_commands = _parse_verification_commands(data)
        if state is SkillVersionState.PUBLISHED and signature_status != "verified":
            raise SkillContractError("published Skill versions require verified provenance")
        rollback_raw = data.get("rollback_version_id")
        rollback_version_id = (
            None
            if rollback_raw is None
            else _strict_uuid(rollback_raw, name="skill_versions[].rollback_version_id")
        )
        return cls(
            skill_version_id=_strict_uuid(
                data.get("skill_version_id"), name="skill_versions[].skill_version_id"
            ),
            skill_definition_id=_strict_uuid(
                data.get("skill_definition_id"), name="skill_versions[].skill_definition_id"
            ),
            version=version,
            version_state=state,
            kind=kind,
            instructions=instructions,
            instructions_digest=instructions_digest,
            input_schema=_strict_json_schema(
                data.get("input_schema"), name="skill_versions[].input_schema"
            ),
            output_schema=_strict_json_schema(
                data.get("output_schema"), name="skill_versions[].output_schema"
            ),
            required_tool_ids=required_tools,
            capability_requirements=requirements,
            supported_agent_version_digests=_unique_digests(
                data.get("supported_agent_version_digests"),
                name="skill_versions[].supported_agent_version_digests",
            ),
            risk_level=_closed_value(
                data.get("risk_level"),
                name="skill_versions[].risk_level",
                allowed=_ALLOWED_RISK_LEVELS,
            ),
            budget=budget,
            network_policy=_closed_value(
                data.get("network_policy"),
                name="skill_versions[].network_policy",
                allowed=_ALLOWED_NETWORK_POLICIES,
            ),
            secrets_allowed=secrets_allowed,
            source_sha256=_strict_digest(
                data.get("source_sha256"), name="skill_versions[].source_sha256"
            ),
            dependency_lock_sha256=_strict_digest(
                data.get("dependency_lock_sha256"),
                name="skill_versions[].dependency_lock_sha256",
            ),
            sbom_sha256=_strict_digest(
                data.get("sbom_sha256"), name="skill_versions[].sbom_sha256"
            ),
            signature_status=signature_status,
            verification_commands=verification_commands,
            rollback_version_id=rollback_version_id,
        )

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "skill_version_id": self.skill_version_id,
            "skill_definition_id": self.skill_definition_id,
            "version": self.version,
            "version_state": self.version_state.value,
            "kind": self.kind.value,
            "instructions": self.instructions,
            "instructions_digest": self.instructions_digest,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "required_tool_ids": list(self.required_tool_ids),
            "capability_requirements": [item.to_dict() for item in self.capability_requirements],
            "supported_agent_version_digests": list(self.supported_agent_version_digests),
            "risk_level": self.risk_level,
            "budget": self.budget.to_dict(),
            "network_policy": self.network_policy,
            "secrets_allowed": self.secrets_allowed,
            "source_sha256": self.source_sha256,
            "dependency_lock_sha256": self.dependency_lock_sha256,
            "sbom_sha256": self.sbom_sha256,
            "signature_status": self.signature_status,
            "verification_commands": [item.to_dict() for item in self.verification_commands],
            "rollback_version_id": self.rollback_version_id,
        }


def _validate_version_reference(
    version: SkillVersion,
    *,
    definitions: Mapping[str, SkillDefinition],
    versions: Mapping[str, SkillVersion],
) -> None:
    definition = definitions.get(version.skill_definition_id)
    if definition is None:
        raise SkillContractError(
            f"SkillVersion {version.skill_version_id} references an unknown definition"
        )
    if (
        definition.definition_state is SkillDefinitionState.REVOKED
        and version.version_state not in {SkillVersionState.DEPRECATED, SkillVersionState.REVOKED}
    ):
        raise SkillContractError("revoked definitions cannot retain an active SkillVersion")
    if version.rollback_version_id is None:
        return
    if version.rollback_version_id == version.skill_version_id:
        raise SkillContractError("rollback_version_id cannot reference itself")
    rollback = versions.get(version.rollback_version_id)
    if rollback is None or rollback.skill_definition_id != version.skill_definition_id:
        raise SkillContractError("rollback_version_id must reference the same definition")
    if _semver_release(rollback.version) >= _semver_release(version.version):
        raise SkillContractError("rollback_version_id must reference a strictly older release")
    if rollback.version_state not in {
        SkillVersionState.APPROVED,
        SkillVersionState.PUBLISHED,
        SkillVersionState.DEPRECATED,
    }:
        raise SkillContractError("rollback_version_id must reference a reviewed version")


@dataclass(frozen=True, slots=True)
class SkillContractConfig:
    schema_version: int
    phase: str
    feature_gates: FeatureGateResolution
    source: SourceScope
    migration_baseline: str
    skill_runtime_authorized: bool
    mcp_enabled: bool
    third_party_marketplace_enabled: bool
    definitions: tuple[SkillDefinition, ...]
    versions: tuple[SkillVersion, ...]

    @classmethod
    def from_mapping(cls, value: object) -> SkillContractConfig:
        data = _strict_object(value, name="skill_contract")
        _only_keys(
            data,
            {
                "schema_version",
                "phase",
                "feature_gates",
                "source",
                "migration_baseline",
                "skill_runtime_authorized",
                "mcp_enabled",
                "third_party_marketplace_enabled",
                "skill_definitions",
                "skill_versions",
            },
            name="skill_contract",
        )
        if data.get("schema_version") != 1:
            raise SkillContractError("skill_contract.schema_version must be 1")
        if data.get("phase") != "P5.6A":
            raise SkillContractError("skill_contract.phase must be P5.6A")
        gates = FeatureGateResolution.from_mapping(data.get("feature_gates"))
        if gates.any_enabled:
            raise SkillContractError("all Phase 5 feature gates must remain false in P5.6A")
        runtime_authorized = _strict_bool(
            data.get("skill_runtime_authorized"), name="skill_contract.skill_runtime_authorized"
        )
        mcp_enabled = _strict_bool(data.get("mcp_enabled"), name="skill_contract.mcp_enabled")
        marketplace_enabled = _strict_bool(
            data.get("third_party_marketplace_enabled"),
            name="skill_contract.third_party_marketplace_enabled",
        )
        if runtime_authorized or mcp_enabled or marketplace_enabled:
            raise SkillContractError("P5.6A cannot authorize Skill runtime, MCP or marketplace")
        definitions = tuple(
            SkillDefinition.from_mapping(item)
            for item in _strict_list(
                data.get("skill_definitions"), name="skill_contract.skill_definitions"
            )
        )
        versions = tuple(
            SkillVersion.from_mapping(item)
            for item in _strict_list(
                data.get("skill_versions"), name="skill_contract.skill_versions"
            )
        )
        definitions = tuple(sorted(definitions, key=lambda item: item.skill_definition_id))
        versions = tuple(sorted(versions, key=lambda item: item.skill_version_id))
        if not definitions or not versions:
            raise SkillContractError("P5.6A requires at least one definition and version")
        config = cls(
            schema_version=1,
            phase="P5.6A",
            feature_gates=gates,
            source=SourceScope.from_mapping(data.get("source")),
            migration_baseline=_strict_string(
                data.get("migration_baseline"), name="skill_contract.migration_baseline"
            ),
            skill_runtime_authorized=runtime_authorized,
            mcp_enabled=mcp_enabled,
            third_party_marketplace_enabled=marketplace_enabled,
            definitions=definitions,
            versions=versions,
        )
        if not config.source.require_clean_checkout:
            raise SkillContractError("P5.6A source provenance must require a clean checkout")
        if config.migration_baseline != "0014":
            raise SkillContractError("P5.6A migration_baseline must remain exactly 0014")
        config._validate_references()
        return config

    def _validate_references(self) -> None:
        definitions = {item.skill_definition_id: item for item in self.definitions}
        if len(definitions) != len(self.definitions):
            raise SkillContractError("SkillDefinition IDs must be unique")
        logical_keys = {item.stable_logical_key for item in self.definitions}
        if len(logical_keys) != len(self.definitions):
            raise SkillContractError("SkillDefinition logical keys must be unique")
        versions = {item.skill_version_id: item for item in self.versions}
        if len(versions) != len(self.versions):
            raise SkillContractError("SkillVersion IDs must be unique")
        natural_versions = {(item.skill_definition_id, item.version) for item in self.versions}
        if len(natural_versions) != len(self.versions):
            raise SkillContractError("SkillVersion SemVer values must be unique per definition")
        for version in self.versions:
            _validate_version_reference(version, definitions=definitions, versions=versions)

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "phase": self.phase,
            "feature_gates": self.feature_gates.to_dict(),
            "source": {
                "expected_repository": self.source.expected_repository,
                "tracked_pathspecs": list(self.source.tracked_pathspecs),
                "require_clean_checkout": self.source.require_clean_checkout,
            },
            "migration_baseline": self.migration_baseline,
            "skill_runtime_authorized": self.skill_runtime_authorized,
            "mcp_enabled": self.mcp_enabled,
            "third_party_marketplace_enabled": self.third_party_marketplace_enabled,
            "skill_definitions": [item.to_dict() for item in self.definitions],
            "skill_versions": [item.to_dict() for item in self.versions],
        }


@dataclass(frozen=True, slots=True)
class SkillContractReport:
    state: AdmissionState
    contract_valid: bool
    activation_allowed: bool
    configuration_sha256: str
    feature_gates: FeatureGateResolution
    source: GitSourceProvenance | None
    migration_head: str | None
    blockers: tuple[str, ...]
    vetoes: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "gate": "P5.6A native Skill contract admission",
            "state": self.state.value,
            "contract_valid": self.contract_valid,
            "activation_allowed": self.activation_allowed,
            "configuration_sha256": self.configuration_sha256,
            "feature_gates": self.feature_gates.to_dict(),
            "source": None if self.source is None else self.source.to_dict(),
            "migration_head": self.migration_head,
            "blockers": list(self.blockers),
            "vetoes": list(self.vetoes),
            "skill_definition_contract_created": True,
            "skill_version_contract_created": True,
            "skill_persistence_created": False,
            "skill_browser_api_exposed": False,
            "skill_runtime_created": False,
            "skill_installation_executed": False,
            "mcp_enabled": False,
            "third_party_marketplace_enabled": False,
            "migration_created": False,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "external_network_accessed": False,
        }


class SkillContractGate:
    """Validate P5.6A without creating runtime authority."""

    _FORBIDDEN_PATHS = (
        "backend/src/omnibase/skill_runtime",
        "backend/src/omnibase/skill_runtime.py",
        "backend/src/omnibase/skills",
        "backend/src/omnibase/skills.py",
        "backend/src/omnibase/api/skills.py",
        "backend/src/omnibase/migrations/versions/0013_p5_6_skill_registry.py",
    )

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve(strict=True)

    def validate_only(self, config: SkillContractConfig) -> SkillContractReport:
        return SkillContractReport(
            state=AdmissionState.BLOCKED,
            contract_valid=True,
            activation_allowed=False,
            configuration_sha256=config.canonical_digest(),
            feature_gates=config.feature_gates,
            source=None,
            migration_head=None,
            blockers=(
                "formal P5.6A verification was not executed",
                "P5.4 typed single-Agent executor Gate is not proven",
                "P5.6A remains compile-only and does not itself authorize the P5.6P personal successor",
            ),
            vetoes=(),
        )

    def _resolve_provenance(
        self,
        config: SkillContractConfig,
        source: GitSourceProvenance | None,
        vetoes: list[str],
    ) -> GitSourceProvenance | None:
        provenance = source
        if provenance is None:
            try:
                provenance = build_git_source_provenance(self._repo_root, config.source)
            except (ConfigurationError, OSError, UnicodeError) as exc:
                vetoes.append(f"source provenance: {exc}")
        if provenance is not None and not provenance.clean:
            vetoes.append("P5.6A verification requires a clean checkout")
        return provenance

    def _resolve_migration_head(self, config: SkillContractConfig, vetoes: list[str]) -> str | None:
        try:
            head = discover_migration_head(
                self._repo_root, "backend/src/omnibase/migrations/versions"
            )
        except (ConfigurationError, OSError, ValueError) as exc:
            vetoes.append(f"migration baseline: {exc}")
            return None
        if head != config.migration_baseline:
            vetoes.append(
                f"migration head drifted: expected {config.migration_baseline}, got {head}"
            )
        return head

    def _find_forbidden_paths(self) -> tuple[str, ...]:
        found: list[str] = []
        for relative in self._FORBIDDEN_PATHS:
            try:
                os.lstat(self._repo_root / relative)
            except FileNotFoundError:
                continue
            found.append(relative)
        return tuple(found)

    def verify(
        self,
        config: SkillContractConfig,
        *,
        gate_values: Mapping[str, object] | None = None,
        source: GitSourceProvenance | None = None,
    ) -> SkillContractReport:
        blockers = [
            "P34.7 production total Gate remains blocked/not_proven",
            "P5.4 typed single-Agent executor Gate is not proven",
            "P5.6A remains compile-only and does not itself authorize the P5.6P personal successor",
        ]
        vetoes: list[str] = []
        provenance = self._resolve_provenance(config, source, vetoes)
        try:
            gates = resolve_feature_gates(gate_values or {})
        except FeatureGateConfigurationError as exc:
            vetoes.append(f"feature gates: {exc}")
            gates = config.feature_gates
        else:
            if gates.any_enabled:
                vetoes.append("all Phase 5 feature gates must remain false in P5.6A")
        migration_head = self._resolve_migration_head(config, vetoes)
        vetoes.extend(
            f"forbidden Skill runtime or migration path exists: {relative}"
            for relative in self._find_forbidden_paths()
        )
        return SkillContractReport(
            state=AdmissionState.INVALID if vetoes else AdmissionState.BLOCKED,
            contract_valid=not vetoes,
            activation_allowed=False,
            configuration_sha256=config.canonical_digest(),
            feature_gates=gates,
            source=provenance,
            migration_head=migration_head,
            blockers=tuple(blockers),
            vetoes=tuple(vetoes),
        )


def load_skill_contract_config(path: Path) -> SkillContractConfig:
    metadata = path.lstat()
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise SkillContractError("P5.6A configuration must be a regular non-link file")

    def _reject_constant(value: str) -> None:
        raise SkillContractError(f"configuration contains a non-finite number: {value}")

    payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    return SkillContractConfig.from_mapping(payload)


__all__ = [
    "CapabilityRequirement",
    "SkillBudget",
    "SkillContractConfig",
    "SkillContractError",
    "SkillContractGate",
    "SkillContractReport",
    "SkillDefinition",
    "SkillDefinitionState",
    "SkillKind",
    "SkillVersion",
    "SkillVersionState",
    "VerificationCommand",
    "load_skill_contract_config",
]
