"""P5.1C Browser Agent Registry public DTOs (logical identifiers only).

These are the only request/response shapes exposed on the Browser ``/api/v1``
surface.  They never carry tenant schema names, PostgreSQL locators, provider
handles, credentials, runtime identity, idempotency internals or audit
internals; every server-derived field (``tenant_id``, ``installed_by``,
operation ids) stays out of the client-visible contract.  Request models are
strict (``extra="forbid"``) so unknown fields are rejected.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

_UUID_RE_PATTERN = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
_DIGEST_RE_PATTERN = r"^[0-9a-f]{64}$"
_SCOPE_RE = __import__("re").compile(r"^[a-z][a-z0-9_]{1,63}$")

_ALLOWED_RISK_LEVELS = ("low", "medium", "high", "critical")
_ALLOWED_DEFINITION_STATES = ("draft", "active", "disabled", "revoked")
_ALLOWED_VERSION_STATES = ("draft", "sealed", "deprecated", "revoked")
_ALLOWED_BINDING_STATES = ("pending_approval", "installed", "disabled", "superseded", "revoked")
_BUDGET_KEYS = ("max_tokens", "max_cost_units", "max_wall_clock_seconds", "max_tool_calls")


class RegistryApiModel(BaseModel):
    """Strict logical Browser DTO base: no unknown fields, no wildcards."""

    model_config = ConfigDict(extra="forbid")


class DefaultBudgetPolicyRead(RegistryApiModel):
    """Public budget projection; positive bounded integers only."""

    max_tokens: int = Field(ge=1)
    max_cost_units: int = Field(ge=1)
    max_wall_clock_seconds: int = Field(ge=1)
    max_tool_calls: int = Field(ge=1)


class AgentDefinitionRead(RegistryApiModel):
    """Public AgentDefinition catalog projection (logical only)."""

    agent_definition_id: str = Field(pattern=_UUID_RE_PATTERN)
    stable_logical_key: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    risk_level: str
    definition_state: str
    metadata_version: int = Field(ge=1)
    created_at: str | None = None


class AgentDefinitionList(RegistryApiModel):
    items: list[AgentDefinitionRead]
    total: int = Field(ge=0)


class AgentVersionRead(RegistryApiModel):
    """Public AgentVersion catalog projection (manifest digest is the anchor)."""

    agent_version_id: str = Field(pattern=_UUID_RE_PATTERN)
    agent_definition_id: str = Field(pattern=_UUID_RE_PATTERN)
    version: str = Field(min_length=1, max_length=64)
    version_state: str
    manifest_digest: str = Field(pattern=_DIGEST_RE_PATTERN)
    instructions_digest: str = Field(pattern=_DIGEST_RE_PATTERN)
    risk_level: str
    max_context_tokens: int = Field(ge=1)
    allowed_tool_ids: list[str] = Field(min_length=0, max_length=64)
    max_concurrency: int = Field(ge=1)
    created_at: str | None = None


class AgentVersionList(RegistryApiModel):
    items: list[AgentVersionRead]
    total: int = Field(ge=0)


class AgentInstallationRead(RegistryApiModel):
    """Public Workspace agent installation projection."""

    binding_id: str = Field(pattern=_UUID_RE_PATTERN)
    workspace_id: str = Field(pattern=_UUID_RE_PATTERN)
    workspace_generation: int = Field(ge=1)
    agent_definition_id: str = Field(pattern=_UUID_RE_PATTERN)
    agent_version_id: str = Field(pattern=_UUID_RE_PATTERN)
    agent_version_digest: str = Field(pattern=_DIGEST_RE_PATTERN)
    binding_state: str
    resource_scopes: list[str] = Field(min_length=1, max_length=32)
    default_budget_policy: DefaultBudgetPolicyRead
    created_at: str | None = None
    disabled_at: str | None = None
    superseded_by: str | None = Field(default=None, pattern=_UUID_RE_PATTERN)


class AgentInstallationList(RegistryApiModel):
    items: list[AgentInstallationRead]
    total: int = Field(ge=0)


class DefaultBudgetPolicyWrite(RegistryApiModel):
    """Client budget expression; server ceilings still apply at install time."""

    max_tokens: int = Field(ge=1)
    max_cost_units: int = Field(ge=1)
    max_wall_clock_seconds: int = Field(ge=1)
    max_tool_calls: int = Field(ge=1)

    def as_mapping(self) -> dict[str, int]:
        return {key: getattr(self, key) for key in _BUDGET_KEYS}


class AgentInstallCreate(RegistryApiModel):
    """Install request: only logical, closed-set, server-validated fields."""

    agent_definition_id: str = Field(pattern=_UUID_RE_PATTERN)
    agent_version_id: str = Field(pattern=_UUID_RE_PATTERN)
    agent_version_digest: str = Field(pattern=_DIGEST_RE_PATTERN)
    workspace_generation: int = Field(ge=1)
    resource_scopes: list[str] = Field(min_length=1, max_length=32)
    default_budget_policy: DefaultBudgetPolicyWrite
    approval_id: str | None = Field(default=None, pattern=_UUID_RE_PATTERN)

    @field_validator("resource_scopes")
    @classmethod
    def _closed_scopes(cls, value: list[str]) -> list[str]:
        seen: set[str] = set()
        for scope in value:
            if scope in ("*", "all", "any"):
                raise ValueError("wildcard resource scopes are forbidden")
            if _SCOPE_RE.fullmatch(scope) is None:
                raise ValueError("resource scope is not a logical identifier")
            if scope in seen:
                raise ValueError("duplicate resource scopes are forbidden")
            seen.add(scope)
        return value


class AgentUpgradeRequest(RegistryApiModel):
    """Upgrade request: pin the exact target version/digest and optional current binding."""

    target_agent_version_id: str = Field(pattern=_UUID_RE_PATTERN)
    target_agent_version_digest: str = Field(pattern=_DIGEST_RE_PATTERN)
    expected_binding_id: str | None = Field(default=None, pattern=_UUID_RE_PATTERN)
    approval_id: str | None = Field(default=None, pattern=_UUID_RE_PATTERN)


class AgentRollbackRequest(RegistryApiModel):
    """Rollback request: pin an existing sealed old version/digest."""

    rollback_agent_version_id: str = Field(pattern=_UUID_RE_PATTERN)
    rollback_agent_version_digest: str = Field(pattern=_DIGEST_RE_PATTERN)
    expected_binding_id: str | None = Field(default=None, pattern=_UUID_RE_PATTERN)
    approval_id: str | None = Field(default=None, pattern=_UUID_RE_PATTERN)


class RegistryApiError(RegistryApiModel):
    code: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=200)


class RegistryApiErrorEnvelope(RegistryApiModel):
    error: RegistryApiError


def _validate_closed(value: str, allowed: tuple[str, ...], name: str) -> str:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {', '.join(allowed)}")
    return value


def validate_definition_state(value: str) -> str:
    return _validate_closed(value, _ALLOWED_DEFINITION_STATES, "definition_state")


def validate_version_state(value: str) -> str:
    return _validate_closed(value, _ALLOWED_VERSION_STATES, "version_state")


def validate_binding_state(value: str) -> str:
    return _validate_closed(value, _ALLOWED_BINDING_STATES, "binding_state")


def validate_risk_level(value: str) -> str:
    return _validate_closed(value, _ALLOWED_RISK_LEVELS, "risk_level")


def project_definition(model: Any) -> AgentDefinitionRead:
    """Public projection from the internal AgentDefinitionModel (whitelist only)."""
    data = model.to_registry_dict()
    return AgentDefinitionRead(
        agent_definition_id=data["agent_definition_id"],
        stable_logical_key=data["stable_logical_key"],
        display_name=data["display_name"],
        description=data.get("description"),
        risk_level=validate_risk_level(data["risk_level"]),
        definition_state=validate_definition_state(data["definition_state"]),
        metadata_version=data["metadata_version"],
        created_at=data.get("created_at"),
    )


def project_version(model: Any) -> AgentVersionRead:
    """Public projection from the internal AgentVersionModel (whitelist only)."""
    data = model.to_registry_dict()
    return AgentVersionRead(
        agent_version_id=data["agent_version_id"],
        agent_definition_id=data["agent_definition_id"],
        version=data["version"],
        version_state=validate_version_state(data["version_state"]),
        manifest_digest=data["manifest_digest"],
        instructions_digest=data["instructions_digest"],
        risk_level=validate_risk_level(data["risk_level"]),
        max_context_tokens=data["max_context_tokens"],
        allowed_tool_ids=[str(item) for item in data["allowed_tool_ids"]],
        max_concurrency=data["max_concurrency"],
        created_at=data.get("created_at"),
    )


def project_binding(model: Any) -> AgentInstallationRead:
    """Public projection from the internal WorkspaceAgentBindingModel (whitelist only)."""
    data = model.to_registry_dict()
    return AgentInstallationRead(
        binding_id=data["workspace_agent_binding_id"],
        workspace_id=data["workspace_id"],
        workspace_generation=data["workspace_generation"],
        agent_definition_id=data["agent_definition_id"],
        agent_version_id=data["agent_version_id"],
        agent_version_digest=data["agent_version_digest"],
        binding_state=validate_binding_state(data["binding_state"]),
        resource_scopes=[str(item) for item in data["resource_scopes"]],
        default_budget_policy=DefaultBudgetPolicyRead(**data["default_budget_policy"]),
        created_at=data.get("created_at"),
        disabled_at=data.get("disabled_at"),
        superseded_by=data.get("superseded_by"),
    )


__all__ = [
    "AgentDefinitionList",
    "AgentDefinitionRead",
    "AgentInstallCreate",
    "AgentInstallationList",
    "AgentInstallationRead",
    "AgentRollbackRequest",
    "AgentUpgradeRequest",
    "AgentVersionList",
    "AgentVersionRead",
    "DefaultBudgetPolicyRead",
    "DefaultBudgetPolicyWrite",
    "RegistryApiError",
    "RegistryApiErrorEnvelope",
    "project_binding",
    "project_definition",
    "project_version",
]
