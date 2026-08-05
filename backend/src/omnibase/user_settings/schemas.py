"""Browser-safe DTOs for user profiles and provider credentials."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


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
    display_name: str = Field(min_length=1, max_length=120)
    provider_id: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    base_url: str = Field(min_length=8, max_length=500)
    model_id: str = Field(min_length=1, max_length=200)
    api_key: SecretStr
    is_default: bool = True


class ProviderCredentialUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    provider_id: str | None = Field(
        default=None, min_length=1, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]*$"
    )
    base_url: str | None = Field(default=None, min_length=8, max_length=500)
    model_id: str | None = Field(default=None, min_length=1, max_length=200)
    is_default: bool | None = None


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


__all__ = [
    "ProviderCredentialActivate",
    "ProviderCredentialCreate",
    "ProviderCredentialList",
    "ProviderCredentialRead",
    "ProviderCredentialSecretUpdate",
    "ProviderCredentialUpdate",
    "ProviderRuntimePosture",
    "ProviderTestResult",
    "UserProfileRead",
    "UserProfileUpdate",
]
