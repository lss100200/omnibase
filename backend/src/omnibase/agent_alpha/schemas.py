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
    # P5.4C: honest single-mode disclosure. The Lite product loop supports
    # exactly `no_tool`; the formal P5.4B builder is formally connected to this
    # loop (formal_builder_integration is "proven_engineering_only"), but no mode
    # is ever claimed merely because a builder name is displayed — the proof is
    # engineering-only and never authorizes production activation.
    formal_builder: str = "build_engineering_single_agent_executor"
    alpha_builder: str = "build_engineering_agent_alpha"
    supported_invocation_modes: list[str] = Field(default_factory=lambda: ["no_tool"])
    formal_builder_integration: str = "proven_engineering_only"
    engineering_composition_ready: bool = True
    activation_allowed: bool = False
    expected_migration_head: str = "0012"


__all__ = [
    "AlphaCancelResponse",
    "AlphaInvokeRequest",
    "AlphaProfileList",
    "AlphaProfileRead",
    "AlphaStatusResponse",
]
