"""Strict Browser DTOs for the P6.1 native instruction-Skill catalog."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class SkillApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NativeSkillRead(SkillApiModel):
    stable_logical_key: str = Field(min_length=3, max_length=96)
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    category: str = Field(min_length=1, max_length=32)
    semantic_version: str = Field(min_length=1, max_length=64)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    kind: str = "instruction"
    first_party: bool = True
    tools_enabled: bool = False
    network_enabled: bool = False
    secrets_allowed: bool = False


class NativeSkillDetail(NativeSkillRead):
    instructions: str = Field(min_length=1, max_length=16_000)


class NativeSkillList(SkillApiModel):
    items: list[NativeSkillRead]
    total: int = Field(ge=0)


class NativeSkillInstallCreate(SkillApiModel):
    workspace_id: str = Field(pattern=_UUID)
    agent_version_id: str = Field(pattern=_UUID)
    expected_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class SkillInstallationRead(SkillApiModel):
    installation_id: str = Field(pattern=_UUID)
    workspace_id: str = Field(pattern=_UUID)
    agent_version_id: str = Field(pattern=_UUID)
    stable_logical_key: str
    display_name: str
    semantic_version: str
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    installation_state: str
    created_at: str | None = None
    disabled_at: str | None = None
    revoked_at: str | None = None


class SkillInstallationList(SkillApiModel):
    items: list[SkillInstallationRead]
    total: int = Field(ge=0)


__all__ = [
    "NativeSkillDetail",
    "NativeSkillInstallCreate",
    "NativeSkillList",
    "NativeSkillRead",
    "SkillInstallationList",
    "SkillInstallationRead",
]
