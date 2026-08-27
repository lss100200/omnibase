"""Strict Browser DTOs for the P6.1 native instruction-Skill catalog."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class SkillApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NativeSkillRead(SkillApiModel):
    stable_logical_key: str = Field(min_length=3, max_length=96)
    display_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    category: str = Field(min_length=1, max_length=32)
    tags: list[str] = Field(min_length=2, max_length=5)
    recommended_roles: list[str] = Field(min_length=1, max_length=4)
    instructions_bytes: int = Field(ge=1, le=16_000 * 4)
    semantic_version: str = Field(min_length=1, max_length=64)
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    source: Literal["omnibase_first_party"] = "omnibase_first_party"
    review_state: Literal["sealed"] = "sealed"
    kind: Literal["instruction"] = "instruction"
    first_party: Literal[True] = True
    tools_enabled: Literal[False] = False
    network_enabled: Literal[False] = False
    secrets_allowed: Literal[False] = False


class NativeSkillDetail(NativeSkillRead):
    instructions: str = Field(min_length=1, max_length=16_000)


class NativeSkillList(SkillApiModel):
    schema_version: Literal[1] = 1
    catalog_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    catalog_total: int = Field(ge=0)
    categories: list[str]
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
    live_count: int = Field(ge=0)
    live_instruction_bytes: int = Field(ge=0)
    max_live_installations: int = Field(ge=1)
    max_instruction_bytes: int = Field(ge=1)


__all__ = [
    "NativeSkillDetail",
    "NativeSkillInstallCreate",
    "NativeSkillList",
    "NativeSkillRead",
    "SkillInstallationList",
    "SkillInstallationRead",
]
