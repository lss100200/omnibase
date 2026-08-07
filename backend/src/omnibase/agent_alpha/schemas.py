"""Browser DTOs for the engineering-only Agent Alpha surface."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_UUID = r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"


class AlphaApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AlphaInvokeRequest(AlphaApiModel):
    agent_version_id: str = Field(pattern=_UUID)
    message: str = Field(min_length=1, max_length=32_000)
    top_k: int = Field(default=5, ge=1, le=12)
    retry_of: str | None = Field(default=None, pattern=_UUID)


class AlphaCancelResponse(AlphaApiModel):
    invocation_id: str = Field(pattern=_UUID)
    cancellation_requested: bool


class AlphaProfileRead(AlphaApiModel):
    agent_definition_id: str = Field(pattern=_UUID)
    agent_version_id: str = Field(pattern=_UUID)
    agent_version_digest: str = Field(min_length=64, max_length=64)
    workspace_agent_binding_id: str = Field(pattern=_UUID)
    display_name: str


class AlphaProfileList(AlphaApiModel):
    items: list[AlphaProfileRead]
    total: int = Field(ge=0)


class AlphaStatusResponse(AlphaApiModel):
    engineering_implemented: bool
    lite_gate_enabled: bool = False
    engineering_assembled: bool = False
    engineering_flag_enabled: bool = False
    environment_allowed: bool = False
    phase5_gates_all_false: bool = True
    production_activation_allowed: bool
    tools_enabled: bool
    multi_agent_enabled: bool
    # P5.4C review-fix: honest builder-chain and capability disclosure.
    knowledge_search_read_only_enabled: bool = False
    formal_builder: str = "build_engineering_single_agent_executor"
    alpha_builder: str = "build_engineering_agent_alpha"
    supported_invocation_modes: list[str] = Field(
        default_factory=lambda: ["no_tool", "knowledge_search_read_only"]
    )
    formal_builder_flag_enabled: bool = False
    expected_migration_head: str = "0012"


__all__ = [
    "AlphaCancelResponse",
    "AlphaInvokeRequest",
    "AlphaProfileList",
    "AlphaProfileRead",
    "AlphaStatusResponse",
]
