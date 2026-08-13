"""Browser-safe DTOs for user profiles and provider credentials."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from omnibase.user_settings.model_identifiers import validate_public_model_id


class UserProfileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    display_name: str
    locale: str
    theme: Literal["system", "light", "dark"]
    assistant_name: str
    assistant_tone: Literal["concise", "balanced", "detailed"]
    assistant_instructions: str
    version: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class UserProfileUpdate(BaseModel):
    expected_version: int = Field(ge=0)
    display_name: str = Field(min_length=1, max_length=120)
    locale: str = Field(min_length=2, max_length=16)
    theme: Literal["system", "light", "dark"]
    assistant_name: str = Field(min_length=1, max_length=80)
    assistant_tone: Literal["concise", "balanced", "detailed"]
    assistant_instructions: str = Field(default="", max_length=4000)

    @field_validator("display_name", "assistant_name")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value


ProviderTestStatus = Literal[
    "passed", "auth_failed", "timeout", "identity_mismatch", "unreachable", "failed"
]
EmployeeRoleId = Literal[
    "parent",
    "product",
    "ux",
    "frontend",
    "backend",
    "data",
    "security",
    "qa",
    "operations",
    "docs",
]
ModelFamily = Literal["deepseek", "glm", "kimi", "openai", "anthropic", "generic"]


class ProviderCredentialRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    display_name: str
    provider_id: str
    base_url: str
    model_id: str
    key_fingerprint: str | None
    secret_configured: bool
    is_default: bool
    is_active: bool
    version: int
    last_test_status: ProviderTestStatus | None
    last_test_latency_ms: int | None
    last_tested_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ProviderCredentialList(BaseModel):
    items: list[ProviderCredentialRead]
    total: int
    operator_fallback_available: bool


class ProviderCredentialCreate(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    display_name: str = Field(min_length=1, max_length=120)
    provider_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    base_url: str = Field(min_length=8, max_length=500)
    model_id: str = Field(min_length=1, max_length=200)
    api_key: SecretStr
    is_default: bool = True

    @field_validator("model_id")
    @classmethod
    def _safe_model_id(cls, value: str) -> str:
        return validate_public_model_id(value)


class ProviderCredentialUpdate(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True)

    expected_version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    provider_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    model_id: str | None = Field(default=None, min_length=1, max_length=200)
    is_default: bool | None = None

    @field_validator("model_id")
    @classmethod
    def _safe_model_id(cls, value: str | None) -> str | None:
        return None if value is None else validate_public_model_id(value)


class ProviderCredentialSecretUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    api_key: SecretStr


class ProviderCredentialActivate(BaseModel):
    expected_version: int = Field(ge=1)
    make_default: bool = True


class ProviderTestResult(BaseModel):
    status: ProviderTestStatus
    latency_ms: int | None
    requested_model_id: str
    actual_model_id: str | None


class ProviderRuntimePosture(BaseModel):
    credential_source: Literal["personal", "operator_default", "unavailable"]
    provider_id: str | None
    model_id: str | None
    credential_id: str | None


class AgentModelSettingWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)

    inherit_default: bool
    provider_credential_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    )
    requested_model_id: str | None = Field(default=None, min_length=1, max_length=200)
    family_override: ModelFamily | None = None
    expected_version: int = Field(ge=0)

    @field_validator("requested_model_id")
    @classmethod
    def _strip_model_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_public_model_id(value)


class AgentModelSettingRead(BaseModel):
    employee_role_id: EmployeeRoleId
    inherit_default: bool
    override_credential_id: str | None
    requested_model_id: str | None
    effective_provider_id: str | None
    effective_model_id: str | None
    family: ModelFamily
    family_source: Literal["model_name", "explicit_override", "unknown"]
    state: Literal["inherited", "pending", "active", "unavailable"]
    test_status: ProviderTestStatus | None = None
    tested_at: datetime | None = None
    version: int = Field(ge=0)


class AgentModelSettingList(BaseModel):
    items: list[AgentModelSettingRead]
    total: int


__all__ = [
    "AgentModelSettingList",
    "AgentModelSettingRead",
    "AgentModelSettingWrite",
    "EmployeeRoleId",
    "ModelFamily",
    "ProviderCredentialActivate",
    "ProviderCredentialCreate",
    "ProviderCredentialList",
    "ProviderCredentialRead",
    "ProviderCredentialSecretUpdate",
    "ProviderCredentialUpdate",
    "ProviderRuntimePosture",
    "ProviderTestResult",
    "ProviderTestStatus",
    "UserProfileRead",
    "UserProfileUpdate",
]
