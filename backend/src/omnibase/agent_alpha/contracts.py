"""Closed logical contracts for the tool-free single-Agent Alpha."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from omnibase.model_gateway import ModelGateway, ModelUsage


@dataclass(frozen=True, slots=True)
class AlphaAgentProfile:
    agent_definition_id: str
    agent_version_id: str
    agent_version_digest: str
    display_name: str
    instructions: str
    instructions_digest: str
    max_context_tokens: int
    allowed_tool_ids: tuple[str, ...]
    workspace_agent_binding_id: str
    resource_scope_digest: str
    budget_policy_digest: str


@dataclass(frozen=True, slots=True)
class AlphaContextChunk:
    chunk_id: str
    document_id: str
    content: str
    score: float
    page_number: int = 1


@dataclass(frozen=True, slots=True)
class AlphaInvocationIdentity:
    """Durable ledger identity of one Alpha invocation.

    ``invocation_id`` equals the durable task id; the remaining fields let the
    ledger adapter reopen its caller-owned transaction without trusting any
    caller-supplied locator.
    """

    invocation_id: str
    task_id: str
    attempt_id: str
    effect_id: str
    tenant_id: str
    workspace_id: str
    actor_user_id: str
    # Exact replay of a terminal invocation: the durable task state that the
    # replay re-exposes (succeeded/failed/blocked_unknown/cancelled).  ``None``
    # means this identity was freshly reserved and must execute.
    replayed_state: str | None = None


@dataclass(frozen=True, slots=True)
class AlphaStreamEvent:
    kind: Literal["meta", "citations", "chunk", "usage", "done", "error", "cancelled"]
    payload: dict[str, object]


class AlphaProfileResolver(Protocol):
    def list_available(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
    ) -> tuple[AlphaAgentProfile, ...]: ...

    def resolve(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
        agent_version_id: str,
    ) -> AlphaAgentProfile: ...


class AlphaKnowledgeRetriever(Protocol):
    def retrieve(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        workspace_id: str,
        query: str,
        top_k: int,
    ) -> tuple[AlphaContextChunk, ...]: ...


@dataclass(frozen=True, slots=True)
class AlphaGatewaySelection:
    gateway: ModelGateway
    credential_source: Literal["personal", "operator_default"]
    configuration_digest: str
    credential_id: str | None = None


class AlphaGatewayResolver(Protocol):
    def resolve(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        actor_user_id: str,
    ) -> AlphaGatewaySelection: ...


@dataclass(frozen=True, slots=True)
class AlphaUserPreferences:
    assistant_name: str
    assistant_tone: Literal["concise", "balanced", "detailed"]
    assistant_instructions: str


class AlphaUserPreferencesResolver(Protocol):
    def resolve_preferences(
        self,
        *,
        tenant_schema: str,
        actor_user_id: str,
    ) -> AlphaUserPreferences: ...


class AlphaInvocationLedger(Protocol):
    def begin(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
        profile: AlphaAgentProfile,
        idempotency_key: str,
        request_hash: str,
        retry_of: str | None,
    ) -> AlphaInvocationIdentity: ...

    def complete(
        self,
        *,
        identity: AlphaInvocationIdentity,
        result_digest: str,
        usage: ModelUsage,
    ) -> None: ...

    def fail(
        self,
        *,
        identity: AlphaInvocationIdentity,
        outcome: Literal["failed", "unknown", "cancelled"],
        error_code: str,
    ) -> None: ...


__all__ = [
    "AlphaAgentProfile",
    "AlphaContextChunk",
    "AlphaGatewayResolver",
    "AlphaGatewaySelection",
    "AlphaInvocationIdentity",
    "AlphaInvocationLedger",
    "AlphaKnowledgeRetriever",
    "AlphaProfileResolver",
    "AlphaStreamEvent",
    "AlphaUserPreferences",
    "AlphaUserPreferencesResolver",
]
