"""Compile-only P5.5A Memory / ContextCapsule contract admission.

P5.5A freezes the privacy, provenance, identity and budget vocabulary for
``ContextCapsule`` and ``MemoryCandidate`` objects.  It deliberately creates no
database table, migration, Browser API, vector index, background worker or
runtime injection path.  A valid contract therefore remains
``blocked/not_proven`` until the later persistence and runtime increments pass
their own gates.
"""

from __future__ import annotations

import json
import os
import re
import stat
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
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
_UTC_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_ALLOWED_SCOPES = frozenset(
    {"user_private", "workspace_private", "agent_private", "controlled_shared"}
)
_ALLOWED_SENSITIVITY = frozenset({"standard", "personal", "sensitive", "restricted"})
_SENSITIVE_LEVELS = frozenset({"sensitive", "restricted"})
_ALLOWED_SELECTION_REASONS = frozenset(
    {"explicit_user", "current_task", "pinned", "semantic_match", "workspace_policy"}
)
_ALLOWED_CANDIDATE_STATES = frozenset({"candidate", "awaiting_confirmation"})
_ALLOWED_REVIEW_DECISIONS = frozenset({"approved"})
_REQUIRED_FORBIDDEN_INFERENCE_CATEGORIES = frozenset(
    {
        "biometric",
        "financial",
        "health",
        "political",
        "religious",
        "sexual_orientation",
    }
)

_MAX_INITIAL_TOKENS = 4096
_MAX_RETRIEVAL_TOKENS = 8192
_MAX_MEMORY_CALLS = 8
_MAX_RESULT_TOKENS = 4096
_MAX_MEMORY_ITEMS = 64
_MAX_SENSITIVE_ITEMS = 8
_MAX_DEADLINE_MS = 5000
_MAX_CAPSULE_TTL_SECONDS = 86400
_MAX_RETENTION_DAYS = 3650


class MemoryContractError(ConfigurationError):
    """The P5.5A memory contract is malformed, unsafe or drifted."""


def _strict_uuid(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _UUID_RE.fullmatch(text) is None:
        raise MemoryContractError(f"{name} must be a canonical lowercase UUID")
    return text


def _optional_uuid(value: object, *, name: str) -> str | None:
    if value is None:
        return None
    return _strict_uuid(value, name=name)


def _strict_digest(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _SHA256_RE.fullmatch(text) is None:
        raise MemoryContractError(f"{name} must be a lowercase SHA-256 digest")
    return text


def _strict_logical_id(value: object, *, name: str) -> str:
    text = _strict_string(value, name=name)
    if _LOGICAL_ID_RE.fullmatch(text) is None:
        raise MemoryContractError(f"{name} must be a bounded logical identifier")
    return text


def _strict_int(
    value: object,
    *,
    name: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise MemoryContractError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise MemoryContractError(f"{name} must be between {minimum} and {maximum}")
    return value


def _closed_value(value: object, *, name: str, allowed: frozenset[str]) -> str:
    text = _strict_string(value, name=name)
    if text not in allowed:
        raise MemoryContractError(f"{name} contains an unsupported value")
    return text


def _strict_utc_timestamp(value: object, *, name: str) -> tuple[str, datetime]:
    text = _strict_string(value, name=name)
    if _UTC_TIMESTAMP_RE.fullmatch(text) is None:
        raise MemoryContractError(f"{name} must use canonical UTC seconds with Z")
    try:
        parsed = datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError as exc:
        raise MemoryContractError(f"{name} is not a valid UTC timestamp") from exc
    return text, parsed


def _unique_uuid_list(value: object, *, name: str, minimum: int = 0) -> tuple[str, ...]:
    items = tuple(_strict_uuid(item, name=f"{name}[]") for item in _strict_list(value, name=name))
    if len(items) < minimum:
        raise MemoryContractError(f"{name} must contain at least {minimum} item(s)")
    if len(items) != len(set(items)):
        raise MemoryContractError(f"{name} must not contain duplicates")
    return tuple(sorted(items))


@dataclass(frozen=True, slots=True)
class MemoryBudget:
    initial_budget_tokens: int
    retrieval_budget_tokens: int
    max_memory_calls: int
    max_memory_result_tokens: int
    max_memory_items: int
    max_sensitive_items: int
    memory_deadline_ms: int
    default_capsule_ttl_seconds: int
    max_capsule_ttl_seconds: int

    @classmethod
    def from_mapping(cls, value: object) -> MemoryBudget:
        data = _strict_object(value, name="memory_budget")
        _only_keys(
            data,
            {
                "initial_budget_tokens",
                "retrieval_budget_tokens",
                "max_memory_calls",
                "max_memory_result_tokens",
                "max_memory_items",
                "max_sensitive_items",
                "memory_deadline_ms",
                "default_capsule_ttl_seconds",
                "max_capsule_ttl_seconds",
            },
            name="memory_budget",
        )
        budget = cls(
            initial_budget_tokens=_strict_int(
                data.get("initial_budget_tokens"),
                name="memory_budget.initial_budget_tokens",
                minimum=1,
                maximum=_MAX_INITIAL_TOKENS,
            ),
            retrieval_budget_tokens=_strict_int(
                data.get("retrieval_budget_tokens"),
                name="memory_budget.retrieval_budget_tokens",
                minimum=0,
                maximum=_MAX_RETRIEVAL_TOKENS,
            ),
            max_memory_calls=_strict_int(
                data.get("max_memory_calls"),
                name="memory_budget.max_memory_calls",
                minimum=0,
                maximum=_MAX_MEMORY_CALLS,
            ),
            max_memory_result_tokens=_strict_int(
                data.get("max_memory_result_tokens"),
                name="memory_budget.max_memory_result_tokens",
                minimum=1,
                maximum=_MAX_RESULT_TOKENS,
            ),
            max_memory_items=_strict_int(
                data.get("max_memory_items"),
                name="memory_budget.max_memory_items",
                minimum=1,
                maximum=_MAX_MEMORY_ITEMS,
            ),
            max_sensitive_items=_strict_int(
                data.get("max_sensitive_items"),
                name="memory_budget.max_sensitive_items",
                minimum=0,
                maximum=_MAX_SENSITIVE_ITEMS,
            ),
            memory_deadline_ms=_strict_int(
                data.get("memory_deadline_ms"),
                name="memory_budget.memory_deadline_ms",
                minimum=1,
                maximum=_MAX_DEADLINE_MS,
            ),
            default_capsule_ttl_seconds=_strict_int(
                data.get("default_capsule_ttl_seconds"),
                name="memory_budget.default_capsule_ttl_seconds",
                minimum=1,
                maximum=_MAX_CAPSULE_TTL_SECONDS,
            ),
            max_capsule_ttl_seconds=_strict_int(
                data.get("max_capsule_ttl_seconds"),
                name="memory_budget.max_capsule_ttl_seconds",
                minimum=1,
                maximum=_MAX_CAPSULE_TTL_SECONDS,
            ),
        )
        if budget.default_capsule_ttl_seconds > budget.max_capsule_ttl_seconds:
            raise MemoryContractError("default capsule TTL exceeds the policy ceiling")
        if budget.max_sensitive_items > budget.max_memory_items:
            raise MemoryContractError("sensitive item ceiling exceeds total item ceiling")
        return budget

    def to_dict(self) -> dict[str, int]:
        return {
            "initial_budget_tokens": self.initial_budget_tokens,
            "retrieval_budget_tokens": self.retrieval_budget_tokens,
            "max_memory_calls": self.max_memory_calls,
            "max_memory_result_tokens": self.max_memory_result_tokens,
            "max_memory_items": self.max_memory_items,
            "max_sensitive_items": self.max_sensitive_items,
            "memory_deadline_ms": self.memory_deadline_ms,
            "default_capsule_ttl_seconds": self.default_capsule_ttl_seconds,
            "max_capsule_ttl_seconds": self.max_capsule_ttl_seconds,
        }


@dataclass(frozen=True, slots=True)
class MemoryPolicy:
    memory_policy_id: str
    stable_logical_key: str
    allowed_scopes: tuple[str, ...]
    budget: MemoryBudget
    auto_activate_candidates: bool
    high_sensitivity_requires_confirmation: bool
    secret_storage_allowed: bool
    inferred_sensitive_attributes_allowed: bool
    treat_memory_as_untrusted_data: bool
    security_kernel_precedence: bool
    source_evidence_required: bool
    forbidden_inference_categories: tuple[str, ...]

    @classmethod
    def from_mapping(cls, value: object) -> MemoryPolicy:
        data = _strict_object(value, name="memory_policy")
        _only_keys(
            data,
            {
                "memory_policy_id",
                "stable_logical_key",
                "allowed_scopes",
                "budget",
                "auto_activate_candidates",
                "high_sensitivity_requires_confirmation",
                "secret_storage_allowed",
                "inferred_sensitive_attributes_allowed",
                "treat_memory_as_untrusted_data",
                "security_kernel_precedence",
                "source_evidence_required",
                "forbidden_inference_categories",
            },
            name="memory_policy",
        )
        scopes = tuple(
            _closed_value(item, name="memory_policy.allowed_scopes[]", allowed=_ALLOWED_SCOPES)
            for item in _strict_list(
                data.get("allowed_scopes"), name="memory_policy.allowed_scopes"
            )
        )
        if not scopes or len(scopes) != len(set(scopes)) or scopes != tuple(sorted(scopes)):
            raise MemoryContractError("memory_policy.allowed_scopes must be sorted and unique")
        categories = tuple(
            _strict_logical_id(item, name="memory_policy.forbidden_inference_categories[]")
            for item in _strict_list(
                data.get("forbidden_inference_categories"),
                name="memory_policy.forbidden_inference_categories",
            )
        )
        if categories != tuple(sorted(set(categories))):
            raise MemoryContractError(
                "memory_policy.forbidden_inference_categories must be sorted and unique"
            )
        if not _REQUIRED_FORBIDDEN_INFERENCE_CATEGORIES.issubset(categories):
            raise MemoryContractError("memory policy omits required sensitive inference bans")
        policy = cls(
            memory_policy_id=_strict_uuid(
                data.get("memory_policy_id"), name="memory_policy.memory_policy_id"
            ),
            stable_logical_key=_strict_logical_id(
                data.get("stable_logical_key"), name="memory_policy.stable_logical_key"
            ),
            allowed_scopes=scopes,
            budget=MemoryBudget.from_mapping(data.get("budget")),
            auto_activate_candidates=_strict_bool(
                data.get("auto_activate_candidates"),
                name="memory_policy.auto_activate_candidates",
            ),
            high_sensitivity_requires_confirmation=_strict_bool(
                data.get("high_sensitivity_requires_confirmation"),
                name="memory_policy.high_sensitivity_requires_confirmation",
            ),
            secret_storage_allowed=_strict_bool(
                data.get("secret_storage_allowed"),
                name="memory_policy.secret_storage_allowed",
            ),
            inferred_sensitive_attributes_allowed=_strict_bool(
                data.get("inferred_sensitive_attributes_allowed"),
                name="memory_policy.inferred_sensitive_attributes_allowed",
            ),
            treat_memory_as_untrusted_data=_strict_bool(
                data.get("treat_memory_as_untrusted_data"),
                name="memory_policy.treat_memory_as_untrusted_data",
            ),
            security_kernel_precedence=_strict_bool(
                data.get("security_kernel_precedence"),
                name="memory_policy.security_kernel_precedence",
            ),
            source_evidence_required=_strict_bool(
                data.get("source_evidence_required"),
                name="memory_policy.source_evidence_required",
            ),
            forbidden_inference_categories=categories,
        )
        if (
            policy.auto_activate_candidates
            or policy.secret_storage_allowed
            or policy.inferred_sensitive_attributes_allowed
            or not policy.high_sensitivity_requires_confirmation
            or not policy.treat_memory_as_untrusted_data
            or not policy.security_kernel_precedence
            or not policy.source_evidence_required
        ):
            raise MemoryContractError("memory policy weakens the P5.5A safety posture")
        return policy

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_policy_id": self.memory_policy_id,
            "stable_logical_key": self.stable_logical_key,
            "allowed_scopes": list(self.allowed_scopes),
            "budget": self.budget.to_dict(),
            "auto_activate_candidates": self.auto_activate_candidates,
            "high_sensitivity_requires_confirmation": (self.high_sensitivity_requires_confirmation),
            "secret_storage_allowed": self.secret_storage_allowed,
            "inferred_sensitive_attributes_allowed": (self.inferred_sensitive_attributes_allowed),
            "treat_memory_as_untrusted_data": self.treat_memory_as_untrusted_data,
            "security_kernel_precedence": self.security_kernel_precedence,
            "source_evidence_required": self.source_evidence_required,
            "forbidden_inference_categories": list(self.forbidden_inference_categories),
        }


@dataclass(frozen=True, slots=True)
class MemoryReviewEvidence:
    review_evidence_id: str
    tenant_id: str
    reviewer_user_id: str
    workspace_id: str
    memory_id: str
    memory_version: int
    content_sha256: str
    decision: str
    reviewed_at: str

    @classmethod
    def from_mapping(cls, value: object) -> MemoryReviewEvidence:
        data = _strict_object(value, name="memory_review_evidence")
        _only_keys(
            data,
            {
                "review_evidence_id",
                "tenant_id",
                "reviewer_user_id",
                "workspace_id",
                "memory_id",
                "memory_version",
                "content_sha256",
                "decision",
                "reviewed_at",
            },
            name="memory_review_evidence",
        )
        reviewed_at, _ = _strict_utc_timestamp(
            data.get("reviewed_at"), name="memory_review_evidence.reviewed_at"
        )
        return cls(
            review_evidence_id=_strict_uuid(
                data.get("review_evidence_id"),
                name="memory_review_evidence.review_evidence_id",
            ),
            tenant_id=_strict_uuid(data.get("tenant_id"), name="memory_review_evidence.tenant_id"),
            reviewer_user_id=_strict_uuid(
                data.get("reviewer_user_id"),
                name="memory_review_evidence.reviewer_user_id",
            ),
            workspace_id=_strict_uuid(
                data.get("workspace_id"), name="memory_review_evidence.workspace_id"
            ),
            memory_id=_strict_uuid(data.get("memory_id"), name="memory_review_evidence.memory_id"),
            memory_version=_strict_int(
                data.get("memory_version"),
                name="memory_review_evidence.memory_version",
                minimum=1,
                maximum=2**31 - 1,
            ),
            content_sha256=_strict_digest(
                data.get("content_sha256"),
                name="memory_review_evidence.content_sha256",
            ),
            decision=_closed_value(
                data.get("decision"),
                name="memory_review_evidence.decision",
                allowed=_ALLOWED_REVIEW_DECISIONS,
            ),
            reviewed_at=reviewed_at,
        )

    def canonical_digest(self) -> str:
        return _sha256_bytes(_canonical_json(self.to_dict()))

    def to_dict(self) -> dict[str, object]:
        return {
            "review_evidence_id": self.review_evidence_id,
            "tenant_id": self.tenant_id,
            "reviewer_user_id": self.reviewer_user_id,
            "workspace_id": self.workspace_id,
            "memory_id": self.memory_id,
            "memory_version": self.memory_version,
            "content_sha256": self.content_sha256,
            "decision": self.decision,
            "reviewed_at": self.reviewed_at,
        }


@dataclass(frozen=True, slots=True)
class MemorySelection:
    position: int
    memory_id: str
    memory_version: int
    scope: str
    tenant_id: str
    owner_user_id: str
    workspace_id: str | None
    agent_version_id: str | None
    review_evidence_id: str | None
    review_evidence_sha256: str | None
    source_resource_id: str
    source_resource_version: int
    evidence_reference_ids: tuple[str, ...]
    content_sha256: str
    selection_reason: str
    sensitivity: str
    token_count: int

    @classmethod
    def from_mapping(cls, value: object) -> MemorySelection:
        data = _strict_object(value, name="memory_selection")
        _only_keys(
            data,
            {
                "position",
                "memory_id",
                "memory_version",
                "scope",
                "tenant_id",
                "owner_user_id",
                "workspace_id",
                "agent_version_id",
                "review_evidence_id",
                "review_evidence_sha256",
                "source_resource_id",
                "source_resource_version",
                "evidence_reference_ids",
                "content_sha256",
                "selection_reason",
                "sensitivity",
                "token_count",
            },
            name="memory_selection",
        )
        return cls(
            position=_strict_int(
                data.get("position"), name="memory_selection.position", minimum=1, maximum=64
            ),
            memory_id=_strict_uuid(data.get("memory_id"), name="memory_selection.memory_id"),
            memory_version=_strict_int(
                data.get("memory_version"),
                name="memory_selection.memory_version",
                minimum=1,
                maximum=2**31 - 1,
            ),
            scope=_closed_value(
                data.get("scope"), name="memory_selection.scope", allowed=_ALLOWED_SCOPES
            ),
            tenant_id=_strict_uuid(data.get("tenant_id"), name="memory_selection.tenant_id"),
            owner_user_id=_strict_uuid(
                data.get("owner_user_id"), name="memory_selection.owner_user_id"
            ),
            workspace_id=_optional_uuid(
                data.get("workspace_id"), name="memory_selection.workspace_id"
            ),
            agent_version_id=_optional_uuid(
                data.get("agent_version_id"), name="memory_selection.agent_version_id"
            ),
            review_evidence_id=_optional_uuid(
                data.get("review_evidence_id"), name="memory_selection.review_evidence_id"
            ),
            review_evidence_sha256=(
                None
                if data.get("review_evidence_sha256") is None
                else _strict_digest(
                    data.get("review_evidence_sha256"),
                    name="memory_selection.review_evidence_sha256",
                )
            ),
            source_resource_id=_strict_uuid(
                data.get("source_resource_id"), name="memory_selection.source_resource_id"
            ),
            source_resource_version=_strict_int(
                data.get("source_resource_version"),
                name="memory_selection.source_resource_version",
                minimum=1,
                maximum=2**31 - 1,
            ),
            evidence_reference_ids=_unique_uuid_list(
                data.get("evidence_reference_ids"),
                name="memory_selection.evidence_reference_ids",
                minimum=1,
            ),
            content_sha256=_strict_digest(
                data.get("content_sha256"), name="memory_selection.content_sha256"
            ),
            selection_reason=_closed_value(
                data.get("selection_reason"),
                name="memory_selection.selection_reason",
                allowed=_ALLOWED_SELECTION_REASONS,
            ),
            sensitivity=_closed_value(
                data.get("sensitivity"),
                name="memory_selection.sensitivity",
                allowed=_ALLOWED_SENSITIVITY,
            ),
            token_count=_strict_int(
                data.get("token_count"),
                name="memory_selection.token_count",
                minimum=1,
                maximum=_MAX_RESULT_TOKENS,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "position": self.position,
            "memory_id": self.memory_id,
            "memory_version": self.memory_version,
            "scope": self.scope,
            "tenant_id": self.tenant_id,
            "owner_user_id": self.owner_user_id,
            "workspace_id": self.workspace_id,
            "agent_version_id": self.agent_version_id,
            "review_evidence_id": self.review_evidence_id,
            "review_evidence_sha256": self.review_evidence_sha256,
            "source_resource_id": self.source_resource_id,
            "source_resource_version": self.source_resource_version,
            "evidence_reference_ids": list(self.evidence_reference_ids),
            "content_sha256": self.content_sha256,
            "selection_reason": self.selection_reason,
            "sensitivity": self.sensitivity,
            "token_count": self.token_count,
        }


@dataclass(frozen=True, slots=True)
class ContextCapsule:
    capsule_id: str
    tenant_id: str
    owner_user_id: str
    workspace_id: str
    agent_version_id: str
    task_id: str
    invocation_id: str
    memory_policy_id: str
    compiler_policy_sha256: str
    issued_at: str
    expires_at: str
    max_tokens: int
    total_tokens: int
    delegable: bool
    trusted_instructions: bool
    sensitivity_summary: tuple[tuple[str, int], ...]
    selected_memories: tuple[MemorySelection, ...]

    @classmethod
    def from_mapping(cls, value: object) -> ContextCapsule:
        data = _strict_object(value, name="context_capsule")
        _only_keys(
            data,
            {
                "capsule_id",
                "tenant_id",
                "owner_user_id",
                "workspace_id",
                "agent_version_id",
                "task_id",
                "invocation_id",
                "memory_policy_id",
                "compiler_policy_sha256",
                "issued_at",
                "expires_at",
                "max_tokens",
                "total_tokens",
                "delegable",
                "trusted_instructions",
                "sensitivity_summary",
                "selected_memories",
            },
            name="context_capsule",
        )
        issued_text, issued = _strict_utc_timestamp(
            data.get("issued_at"), name="context_capsule.issued_at"
        )
        expires_text, expires = _strict_utc_timestamp(
            data.get("expires_at"), name="context_capsule.expires_at"
        )
        if expires <= issued:
            raise MemoryContractError("context_capsule.expires_at must be after issued_at")
        summary_data = _strict_object(
            data.get("sensitivity_summary"), name="context_capsule.sensitivity_summary"
        )
        _only_keys(
            summary_data,
            set(_ALLOWED_SENSITIVITY),
            name="context_capsule.sensitivity_summary",
        )
        summary = tuple(
            (
                level,
                _strict_int(
                    summary_data.get(level),
                    name=f"context_capsule.sensitivity_summary.{level}",
                    minimum=0,
                    maximum=_MAX_MEMORY_ITEMS,
                ),
            )
            for level in sorted(_ALLOWED_SENSITIVITY)
        )
        selections = tuple(
            sorted(
                (
                    MemorySelection.from_mapping(item)
                    for item in _strict_list(
                        data.get("selected_memories"),
                        name="context_capsule.selected_memories",
                    )
                ),
                key=lambda item: item.position,
            )
        )
        if not selections:
            raise MemoryContractError("context_capsule must select at least one memory")
        positions = tuple(item.position for item in selections)
        if positions != tuple(range(1, len(selections) + 1)):
            raise MemoryContractError("context capsule positions must be continuous from one")
        identities = {(item.memory_id, item.memory_version) for item in selections}
        if len(identities) != len(selections):
            raise MemoryContractError("context capsule repeats a memory identity/version")
        capsule = cls(
            capsule_id=_strict_uuid(data.get("capsule_id"), name="context_capsule.capsule_id"),
            tenant_id=_strict_uuid(data.get("tenant_id"), name="context_capsule.tenant_id"),
            owner_user_id=_strict_uuid(
                data.get("owner_user_id"), name="context_capsule.owner_user_id"
            ),
            workspace_id=_strict_uuid(
                data.get("workspace_id"), name="context_capsule.workspace_id"
            ),
            agent_version_id=_strict_uuid(
                data.get("agent_version_id"), name="context_capsule.agent_version_id"
            ),
            task_id=_strict_uuid(data.get("task_id"), name="context_capsule.task_id"),
            invocation_id=_strict_uuid(
                data.get("invocation_id"), name="context_capsule.invocation_id"
            ),
            memory_policy_id=_strict_uuid(
                data.get("memory_policy_id"), name="context_capsule.memory_policy_id"
            ),
            compiler_policy_sha256=_strict_digest(
                data.get("compiler_policy_sha256"),
                name="context_capsule.compiler_policy_sha256",
            ),
            issued_at=issued_text,
            expires_at=expires_text,
            max_tokens=_strict_int(
                data.get("max_tokens"),
                name="context_capsule.max_tokens",
                minimum=1,
                maximum=_MAX_INITIAL_TOKENS,
            ),
            total_tokens=_strict_int(
                data.get("total_tokens"),
                name="context_capsule.total_tokens",
                minimum=1,
                maximum=_MAX_INITIAL_TOKENS,
            ),
            delegable=_strict_bool(data.get("delegable"), name="context_capsule.delegable"),
            trusted_instructions=_strict_bool(
                data.get("trusted_instructions"),
                name="context_capsule.trusted_instructions",
            ),
            sensitivity_summary=summary,
            selected_memories=selections,
        )
        if capsule.delegable or capsule.trusted_instructions:
            raise MemoryContractError("ContextCapsule must be non-delegable untrusted data")
        return capsule

    def content_sha256(self) -> str:
        return _sha256_bytes(_canonical_json(self._content_payload()))

    def _content_payload(self) -> dict[str, object]:
        return {
            "capsule_id": self.capsule_id,
            "tenant_id": self.tenant_id,
            "owner_user_id": self.owner_user_id,
            "workspace_id": self.workspace_id,
            "agent_version_id": self.agent_version_id,
            "task_id": self.task_id,
            "invocation_id": self.invocation_id,
            "memory_policy_id": self.memory_policy_id,
            "compiler_policy_sha256": self.compiler_policy_sha256,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "max_tokens": self.max_tokens,
            "total_tokens": self.total_tokens,
            "delegable": self.delegable,
            "trusted_instructions": self.trusted_instructions,
            "sensitivity_summary": dict(self.sensitivity_summary),
            "selected_memories": [item.to_dict() for item in self.selected_memories],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self._content_payload(), "content_sha256": self.content_sha256()}


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    candidate_id: str
    tenant_id: str
    owner_user_id: str
    workspace_id: str
    agent_version_id: str
    task_id: str
    invocation_id: str
    memory_policy_id: str
    requested_scope: str
    sensitivity: str
    lifecycle_state: str
    content_sha256: str
    source_resource_id: str
    source_resource_version: int
    evidence_reference_ids: tuple[str, ...]
    confidence_millis: int
    retention_days: int
    requires_user_confirmation: bool
    contains_secret: bool
    inferred_sensitive_categories: tuple[str, ...]
    active_memory_id: str | None
    candidate_created_by: str

    @classmethod
    def from_mapping(cls, value: object) -> MemoryCandidate:
        data = _strict_object(value, name="memory_candidate")
        _only_keys(
            data,
            {
                "candidate_id",
                "tenant_id",
                "owner_user_id",
                "workspace_id",
                "agent_version_id",
                "task_id",
                "invocation_id",
                "memory_policy_id",
                "requested_scope",
                "sensitivity",
                "lifecycle_state",
                "content_sha256",
                "source_resource_id",
                "source_resource_version",
                "evidence_reference_ids",
                "confidence_millis",
                "retention_days",
                "requires_user_confirmation",
                "contains_secret",
                "inferred_sensitive_categories",
                "active_memory_id",
                "candidate_created_by",
            },
            name="memory_candidate",
        )
        categories = tuple(
            _strict_logical_id(item, name="memory_candidate.inferred_sensitive_categories[]")
            for item in _strict_list(
                data.get("inferred_sensitive_categories"),
                name="memory_candidate.inferred_sensitive_categories",
            )
        )
        candidate = cls(
            candidate_id=_strict_uuid(
                data.get("candidate_id"), name="memory_candidate.candidate_id"
            ),
            tenant_id=_strict_uuid(data.get("tenant_id"), name="memory_candidate.tenant_id"),
            owner_user_id=_strict_uuid(
                data.get("owner_user_id"), name="memory_candidate.owner_user_id"
            ),
            workspace_id=_strict_uuid(
                data.get("workspace_id"), name="memory_candidate.workspace_id"
            ),
            agent_version_id=_strict_uuid(
                data.get("agent_version_id"), name="memory_candidate.agent_version_id"
            ),
            task_id=_strict_uuid(data.get("task_id"), name="memory_candidate.task_id"),
            invocation_id=_strict_uuid(
                data.get("invocation_id"), name="memory_candidate.invocation_id"
            ),
            memory_policy_id=_strict_uuid(
                data.get("memory_policy_id"), name="memory_candidate.memory_policy_id"
            ),
            requested_scope=_closed_value(
                data.get("requested_scope"),
                name="memory_candidate.requested_scope",
                allowed=_ALLOWED_SCOPES,
            ),
            sensitivity=_closed_value(
                data.get("sensitivity"),
                name="memory_candidate.sensitivity",
                allowed=_ALLOWED_SENSITIVITY,
            ),
            lifecycle_state=_closed_value(
                data.get("lifecycle_state"),
                name="memory_candidate.lifecycle_state",
                allowed=_ALLOWED_CANDIDATE_STATES,
            ),
            content_sha256=_strict_digest(
                data.get("content_sha256"), name="memory_candidate.content_sha256"
            ),
            source_resource_id=_strict_uuid(
                data.get("source_resource_id"), name="memory_candidate.source_resource_id"
            ),
            source_resource_version=_strict_int(
                data.get("source_resource_version"),
                name="memory_candidate.source_resource_version",
                minimum=1,
                maximum=2**31 - 1,
            ),
            evidence_reference_ids=_unique_uuid_list(
                data.get("evidence_reference_ids"),
                name="memory_candidate.evidence_reference_ids",
                minimum=1,
            ),
            confidence_millis=_strict_int(
                data.get("confidence_millis"),
                name="memory_candidate.confidence_millis",
                minimum=0,
                maximum=1000,
            ),
            retention_days=_strict_int(
                data.get("retention_days"),
                name="memory_candidate.retention_days",
                minimum=1,
                maximum=_MAX_RETENTION_DAYS,
            ),
            requires_user_confirmation=_strict_bool(
                data.get("requires_user_confirmation"),
                name="memory_candidate.requires_user_confirmation",
            ),
            contains_secret=_strict_bool(
                data.get("contains_secret"), name="memory_candidate.contains_secret"
            ),
            inferred_sensitive_categories=categories,
            active_memory_id=_optional_uuid(
                data.get("active_memory_id"), name="memory_candidate.active_memory_id"
            ),
            candidate_created_by=_strict_string(
                data.get("candidate_created_by"), name="memory_candidate.candidate_created_by"
            ),
        )
        if candidate.candidate_created_by != "agent":
            raise MemoryContractError("P5.5A examples only permit Agent-created candidates")
        if candidate.contains_secret or candidate.inferred_sensitive_categories:
            raise MemoryContractError(
                "MemoryCandidate cannot retain secrets or inferred sensitive traits"
            )
        if candidate.active_memory_id is not None:
            raise MemoryContractError("P5.5A candidate cannot reference an active memory")
        confirmation_required = (
            candidate.sensitivity in _SENSITIVE_LEVELS
            or candidate.requested_scope == "controlled_shared"
        )
        if confirmation_required and not candidate.requires_user_confirmation:
            raise MemoryContractError("sensitive or shared candidate requires user confirmation")
        if (
            candidate.lifecycle_state == "awaiting_confirmation"
            and not candidate.requires_user_confirmation
        ):
            raise MemoryContractError("awaiting_confirmation candidate must require confirmation")
        return candidate

    def to_dict(self) -> dict[str, object]:
        return {
            "candidate_id": self.candidate_id,
            "tenant_id": self.tenant_id,
            "owner_user_id": self.owner_user_id,
            "workspace_id": self.workspace_id,
            "agent_version_id": self.agent_version_id,
            "task_id": self.task_id,
            "invocation_id": self.invocation_id,
            "memory_policy_id": self.memory_policy_id,
            "requested_scope": self.requested_scope,
            "sensitivity": self.sensitivity,
            "lifecycle_state": self.lifecycle_state,
            "content_sha256": self.content_sha256,
            "source_resource_id": self.source_resource_id,
            "source_resource_version": self.source_resource_version,
            "evidence_reference_ids": list(self.evidence_reference_ids),
            "confidence_millis": self.confidence_millis,
            "retention_days": self.retention_days,
            "requires_user_confirmation": self.requires_user_confirmation,
            "contains_secret": self.contains_secret,
            "inferred_sensitive_categories": list(self.inferred_sensitive_categories),
            "active_memory_id": self.active_memory_id,
            "candidate_created_by": self.candidate_created_by,
        }


@dataclass(frozen=True, slots=True)
class MemoryContractConfig:
    schema_version: int
    phase: str
    feature_gates: FeatureGateResolution
    source: SourceScope
    migration_baseline: str
    memory_persistence_authorized: bool
    memory_runtime_authorized: bool
    memory_browser_api_exposed: bool
    policies: tuple[MemoryPolicy, ...]
    review_evidence: tuple[MemoryReviewEvidence, ...]
    capsules: tuple[ContextCapsule, ...]
    candidates: tuple[MemoryCandidate, ...]

    @classmethod
    def from_mapping(cls, value: object) -> MemoryContractConfig:
        data = _strict_object(value, name="memory_contract")
        _only_keys(
            data,
            {
                "schema_version",
                "phase",
                "feature_gates",
                "source",
                "migration_baseline",
                "memory_persistence_authorized",
                "memory_runtime_authorized",
                "memory_browser_api_exposed",
                "memory_policies",
                "memory_review_evidence_examples",
                "context_capsule_examples",
                "memory_candidate_examples",
            },
            name="memory_contract",
        )
        if data.get("schema_version") != 1 or data.get("phase") != "P5.5A":
            raise MemoryContractError("memory contract must declare schema_version 1 and P5.5A")
        gates = FeatureGateResolution.from_mapping(data.get("feature_gates"))
        if gates.any_enabled:
            raise MemoryContractError("all Phase 5 feature gates must remain false in P5.5A")
        persistence = _strict_bool(
            data.get("memory_persistence_authorized"),
            name="memory_contract.memory_persistence_authorized",
        )
        runtime = _strict_bool(
            data.get("memory_runtime_authorized"),
            name="memory_contract.memory_runtime_authorized",
        )
        browser_api = _strict_bool(
            data.get("memory_browser_api_exposed"),
            name="memory_contract.memory_browser_api_exposed",
        )
        if persistence or runtime or browser_api:
            raise MemoryContractError(
                "P5.5A cannot authorize persistence, API or runtime injection"
            )
        config = cls(
            schema_version=1,
            phase="P5.5A",
            feature_gates=gates,
            source=SourceScope.from_mapping(data.get("source")),
            migration_baseline=_strict_string(
                data.get("migration_baseline"), name="memory_contract.migration_baseline"
            ),
            memory_persistence_authorized=persistence,
            memory_runtime_authorized=runtime,
            memory_browser_api_exposed=browser_api,
            policies=tuple(
                sorted(
                    (
                        MemoryPolicy.from_mapping(item)
                        for item in _strict_list(
                            data.get("memory_policies"), name="memory_contract.memory_policies"
                        )
                    ),
                    key=lambda item: item.memory_policy_id,
                )
            ),
            review_evidence=tuple(
                sorted(
                    (
                        MemoryReviewEvidence.from_mapping(item)
                        for item in _strict_list(
                            data.get("memory_review_evidence_examples"),
                            name="memory_contract.memory_review_evidence_examples",
                        )
                    ),
                    key=lambda item: item.review_evidence_id,
                )
            ),
            capsules=tuple(
                sorted(
                    (
                        ContextCapsule.from_mapping(item)
                        for item in _strict_list(
                            data.get("context_capsule_examples"),
                            name="memory_contract.context_capsule_examples",
                        )
                    ),
                    key=lambda item: item.capsule_id,
                )
            ),
            candidates=tuple(
                sorted(
                    (
                        MemoryCandidate.from_mapping(item)
                        for item in _strict_list(
                            data.get("memory_candidate_examples"),
                            name="memory_contract.memory_candidate_examples",
                        )
                    ),
                    key=lambda item: item.candidate_id,
                )
            ),
        )
        if not config.source.require_clean_checkout:
            raise MemoryContractError("P5.5A source provenance must require a clean checkout")
        if config.migration_baseline != "0012":
            raise MemoryContractError("P5.5A migration_baseline must remain exactly 0012")
        if not config.policies or not config.capsules or not config.candidates:
            raise MemoryContractError("P5.5A requires policy, capsule and candidate examples")
        config._validate_references()
        return config

    def _validate_references(self) -> None:
        policies = {item.memory_policy_id: item for item in self.policies}
        reviews = {item.review_evidence_id: item for item in self.review_evidence}
        capsule_contexts = {(item.task_id, item.invocation_id): item for item in self.capsules}
        self._validate_unique_contract_ids(policies, reviews, capsule_contexts)
        for capsule in self.capsules:
            policy = policies.get(capsule.memory_policy_id)
            if policy is None:
                raise MemoryContractError("ContextCapsule references an unknown memory policy")
            self._validate_capsule(capsule, policy, reviews)
        for candidate in self.candidates:
            policy = policies.get(candidate.memory_policy_id)
            if policy is None:
                raise MemoryContractError("MemoryCandidate references an unknown memory policy")
            if candidate.requested_scope not in policy.allowed_scopes:
                raise MemoryContractError("MemoryCandidate requests a scope outside its policy")
            bound_capsule = capsule_contexts.get((candidate.task_id, candidate.invocation_id))
            if bound_capsule is None:
                raise MemoryContractError(
                    "MemoryCandidate must bind an existing ContextCapsule invocation"
                )
            self._validate_candidate_binding(candidate, bound_capsule)

    def _validate_unique_contract_ids(
        self,
        policies: Mapping[str, MemoryPolicy],
        reviews: Mapping[str, MemoryReviewEvidence],
        capsule_contexts: Mapping[tuple[str, str], ContextCapsule],
    ) -> None:
        checks = (
            (len(policies), len(self.policies), "memory policy IDs must be unique"),
            (
                len({item.stable_logical_key for item in self.policies}),
                len(self.policies),
                "memory policy logical keys must be unique",
            ),
            (
                len({item.capsule_id for item in self.capsules}),
                len(self.capsules),
                "ContextCapsule IDs must be unique",
            ),
            (
                len(reviews),
                len(self.review_evidence),
                "Memory review evidence IDs must be unique",
            ),
            (
                len({item.candidate_id for item in self.candidates}),
                len(self.candidates),
                "MemoryCandidate IDs must be unique",
            ),
            (
                len(capsule_contexts),
                len(self.capsules),
                "ContextCapsule task/invocation bindings must be unique",
            ),
        )
        for observed, expected, message in checks:
            if observed != expected:
                raise MemoryContractError(message)

    @staticmethod
    def _validate_capsule(
        capsule: ContextCapsule,
        policy: MemoryPolicy,
        reviews: Mapping[str, MemoryReviewEvidence],
    ) -> None:
        if capsule.compiler_policy_sha256 != policy.canonical_digest():
            raise MemoryContractError("ContextCapsule compiler policy digest drifted")
        MemoryContractConfig._validate_capsule_budget(capsule, policy)
        MemoryContractConfig._validate_capsule_items(capsule, policy, reviews)

    @staticmethod
    def _validate_candidate_binding(candidate: MemoryCandidate, capsule: ContextCapsule) -> None:
        if candidate.tenant_id != capsule.tenant_id:
            raise MemoryContractError("MemoryCandidate crossed Tenant scope")
        if candidate.owner_user_id != capsule.owner_user_id:
            raise MemoryContractError("MemoryCandidate crossed Owner scope")
        if candidate.workspace_id != capsule.workspace_id:
            raise MemoryContractError("MemoryCandidate crossed Workspace scope")
        if candidate.agent_version_id != capsule.agent_version_id:
            raise MemoryContractError("MemoryCandidate crossed AgentVersion scope")
        if candidate.memory_policy_id != capsule.memory_policy_id:
            raise MemoryContractError("MemoryCandidate crossed Memory Policy scope")

    @staticmethod
    def _validate_capsule_budget(capsule: ContextCapsule, policy: MemoryPolicy) -> None:
        issued = datetime.strptime(capsule.issued_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        expires = datetime.strptime(capsule.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
        ttl_seconds = int((expires - issued).total_seconds())
        if ttl_seconds > policy.budget.max_capsule_ttl_seconds:
            raise MemoryContractError("ContextCapsule TTL exceeds the policy ceiling")
        if capsule.max_tokens > policy.budget.initial_budget_tokens:
            raise MemoryContractError("ContextCapsule max_tokens exceeds the initial budget")
        observed_tokens = sum(item.token_count for item in capsule.selected_memories)
        if capsule.total_tokens != observed_tokens or observed_tokens > capsule.max_tokens:
            raise MemoryContractError("ContextCapsule token accounting drifted")
        if len(capsule.selected_memories) > policy.budget.max_memory_items:
            raise MemoryContractError("ContextCapsule item count exceeds the policy ceiling")
        observed_summary = Counter(item.sensitivity for item in capsule.selected_memories)
        if dict(capsule.sensitivity_summary) != {
            level: observed_summary.get(level, 0) for level in sorted(_ALLOWED_SENSITIVITY)
        }:
            raise MemoryContractError("ContextCapsule sensitivity summary drifted")
        sensitive_count = sum(
            count for level, count in capsule.sensitivity_summary if level in _SENSITIVE_LEVELS
        )
        if sensitive_count > policy.budget.max_sensitive_items:
            raise MemoryContractError("ContextCapsule sensitive item count exceeds policy")

    @staticmethod
    def _validate_capsule_items(
        capsule: ContextCapsule,
        policy: MemoryPolicy,
        reviews: Mapping[str, MemoryReviewEvidence],
    ) -> None:
        for item in capsule.selected_memories:
            if item.scope not in policy.allowed_scopes:
                raise MemoryContractError("ContextCapsule includes a scope outside its policy")
            if item.tenant_id != capsule.tenant_id:
                raise MemoryContractError("ContextCapsule contains a cross-Tenant memory")
            if item.owner_user_id != capsule.owner_user_id:
                raise MemoryContractError("ContextCapsule contains a cross-user memory")
            MemoryContractConfig._validate_selection_scope(item, capsule)
            MemoryContractConfig._validate_selection_review(item, capsule, reviews)

    @staticmethod
    def _validate_selection_scope(item: MemorySelection, capsule: ContextCapsule) -> None:
        if item.scope == "user_private":
            if item.workspace_id is not None or item.agent_version_id is not None:
                raise MemoryContractError(
                    "user-private memory must not carry Workspace or AgentVersion scope"
                )
            return
        if item.workspace_id != capsule.workspace_id:
            raise MemoryContractError("ContextCapsule contains a cross-Workspace memory")
        if item.scope == "agent_private":
            if item.agent_version_id != capsule.agent_version_id:
                raise MemoryContractError("agent-private memory crossed AgentVersion scope")
        elif item.agent_version_id is not None:
            raise MemoryContractError("non-agent-private memory must not bind AgentVersion")

    @staticmethod
    def _validate_selection_review(
        item: MemorySelection,
        capsule: ContextCapsule,
        reviews: Mapping[str, MemoryReviewEvidence],
    ) -> None:
        if item.scope != "controlled_shared":
            if item.review_evidence_id is not None or item.review_evidence_sha256 is not None:
                raise MemoryContractError(
                    "private memory must not carry controlled-shared review evidence"
                )
            return
        if item.review_evidence_id is None or item.review_evidence_sha256 is None:
            raise MemoryContractError("controlled-shared memory requires sealed review evidence")
        if item.review_evidence_id not in item.evidence_reference_ids:
            raise MemoryContractError(
                "controlled-shared memory must include its review in evidence references"
            )
        review = reviews.get(item.review_evidence_id)
        if review is None:
            raise MemoryContractError("controlled-shared memory review evidence is unknown")
        if item.review_evidence_sha256 != review.canonical_digest():
            raise MemoryContractError("controlled-shared memory review evidence digest drifted")
        if (
            review.tenant_id != capsule.tenant_id
            or review.reviewer_user_id != capsule.owner_user_id
            or review.workspace_id != capsule.workspace_id
            or review.memory_id != item.memory_id
            or review.memory_version != item.memory_version
            or review.content_sha256 != item.content_sha256
        ):
            raise MemoryContractError("controlled-shared review evidence binding drifted")
        reviewed_at = datetime.strptime(review.reviewed_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
        capsule_issued_at = datetime.strptime(capsule.issued_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=UTC
        )
        if reviewed_at > capsule_issued_at:
            raise MemoryContractError(
                "controlled-shared review evidence must predate Capsule issuance"
            )

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
            "memory_persistence_authorized": self.memory_persistence_authorized,
            "memory_runtime_authorized": self.memory_runtime_authorized,
            "memory_browser_api_exposed": self.memory_browser_api_exposed,
            "memory_policies": [item.to_dict() for item in self.policies],
            "memory_review_evidence_examples": [item.to_dict() for item in self.review_evidence],
            "context_capsule_examples": [item.to_dict() for item in self.capsules],
            "memory_candidate_examples": [item.to_dict() for item in self.candidates],
        }


@dataclass(frozen=True, slots=True)
class MemoryContractReport:
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
            "gate": "P5.5A Memory / ContextCapsule contract admission",
            "state": self.state.value,
            "contract_valid": self.contract_valid,
            "activation_allowed": self.activation_allowed,
            "configuration_sha256": self.configuration_sha256,
            "feature_gates": self.feature_gates.to_dict(),
            "source": None if self.source is None else self.source.to_dict(),
            "migration_head": self.migration_head,
            "blockers": list(self.blockers),
            "vetoes": list(self.vetoes),
            "context_capsule_contract_created": True,
            "memory_candidate_contract_created": True,
            "memory_persistence_created": False,
            "memory_browser_api_exposed": False,
            "memory_runtime_created": False,
            "memory_injection_executed": False,
            "migration_created": False,
            "root_env_accessed": False,
            "business_database_accessed": False,
            "business_database_migrated": False,
            "external_network_accessed": False,
        }


class MemoryContractGate:
    """Validate P5.5A without creating persistence or runtime authority."""

    _FORBIDDEN_PATHS = (
        "backend/src/omnibase/agent_memory",
        "backend/src/omnibase/agent_memory.py",
        "backend/src/omnibase/api/memory.py",
        "backend/src/omnibase/migrations/versions/0013_p5_5_agent_memory.py",
    )

    def __init__(self, repo_root: Path) -> None:
        self._repo_root = repo_root.resolve(strict=True)

    def validate_only(self, config: MemoryContractConfig) -> MemoryContractReport:
        return MemoryContractReport(
            state=AdmissionState.BLOCKED,
            contract_valid=True,
            activation_allowed=False,
            configuration_sha256=config.canonical_digest(),
            feature_gates=config.feature_gates,
            source=None,
            migration_head=None,
            blockers=(
                "formal P5.5A verification was not executed",
                "Memory persistence, Browser governance API and Runtime injection are absent",
                "Provider-backed durable personal-target acceptance remains not proven",
            ),
            vetoes=(),
        )

    def verify(
        self,
        config: MemoryContractConfig,
        *,
        gate_values: Mapping[str, object] | None = None,
        source: GitSourceProvenance | None = None,
    ) -> MemoryContractReport:
        vetoes: list[str] = []
        provenance = source
        if provenance is None:
            try:
                provenance = build_git_source_provenance(self._repo_root, config.source)
            except (ConfigurationError, OSError, UnicodeError) as exc:
                vetoes.append(f"source provenance: {exc}")
        if provenance is not None and not provenance.clean:
            vetoes.append("P5.5A verification requires a clean checkout")
        try:
            gates = resolve_feature_gates(gate_values or {})
        except FeatureGateConfigurationError as exc:
            vetoes.append(f"feature gates: {exc}")
            gates = config.feature_gates
        else:
            if gates.any_enabled:
                vetoes.append("all Phase 5 feature gates must remain false in P5.5A")
        migration_head: str | None
        try:
            migration_head = discover_migration_head(
                self._repo_root, "backend/src/omnibase/migrations/versions"
            )
        except (ConfigurationError, OSError, ValueError) as exc:
            migration_head = None
            vetoes.append(f"migration baseline: {exc}")
        else:
            if migration_head != config.migration_baseline:
                vetoes.append(
                    "migration head drifted: "
                    f"expected {config.migration_baseline}, got {migration_head}"
                )
        for relative in self._FORBIDDEN_PATHS:
            try:
                os.lstat(self._repo_root / relative)
            except FileNotFoundError:
                continue
            vetoes.append(f"forbidden Memory runtime or migration path exists: {relative}")
        blockers = (
            "P5.5 persistence and deletion/export lifecycle Gate is not proven",
            "ContextCapsule compiler and Runtime injection Gate is not proven",
            "Provider-backed durable personal-target acceptance remains not proven",
        )
        return MemoryContractReport(
            state=AdmissionState.INVALID if vetoes else AdmissionState.BLOCKED,
            contract_valid=not vetoes,
            activation_allowed=False,
            configuration_sha256=config.canonical_digest(),
            feature_gates=gates,
            source=provenance,
            migration_head=migration_head,
            blockers=blockers,
            vetoes=tuple(vetoes),
        )


def load_memory_contract_config(path: Path) -> MemoryContractConfig:
    metadata = path.lstat()
    is_reparse = bool(getattr(metadata, "st_file_attributes", 0) & 0x400)
    if stat.S_ISLNK(metadata.st_mode) or is_reparse or not stat.S_ISREG(metadata.st_mode):
        raise MemoryContractError("P5.5A configuration must be a regular non-link file")

    def _reject_constant(value: str) -> None:
        raise MemoryContractError(f"configuration contains a non-finite number: {value}")

    payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant)
    return MemoryContractConfig.from_mapping(payload)


__all__ = [
    "ContextCapsule",
    "MemoryBudget",
    "MemoryCandidate",
    "MemoryContractConfig",
    "MemoryContractError",
    "MemoryContractGate",
    "MemoryContractReport",
    "MemoryPolicy",
    "MemoryReviewEvidence",
    "MemorySelection",
    "load_memory_contract_config",
]
