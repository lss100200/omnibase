"""Tool-free single-Agent Alpha orchestration.

The service deliberately has no shell, SQL, HTTP, MCP, Skill, planner or
multi-Agent port.  Registry, knowledge, ledger and model access are explicit
injected boundaries so the default Browser composition can reject before any
state or provider is touched.

Server-owned limits (``AlphaLimits``) cap the message, output, context and
wall-clock budget of every invocation; one invocation makes exactly one Model
Gateway call.  The cancellation registry is a process-local signal only:
durable terminal state is always written through the injected ledger, and an
SSE disconnect never fabricates a ``cancelled`` outcome -- the provider
outcome is recorded as ``unknown`` with a reconciliation case.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from threading import Event, Lock

from omnibase.agent_alpha.adapters import AlphaAdapterError, AlphaAdapterUnavailable
from omnibase.agent_alpha.contracts import (
    AlphaAgentProfile,
    AlphaContextChunk,
    AlphaGatewayResolver,
    AlphaGatewaySelection,
    AlphaInvocationIdentity,
    AlphaInvocationLedger,
    AlphaKnowledgeRetriever,
    AlphaProfileResolver,
    AlphaStreamEvent,
    AlphaUserPreferences,
    AlphaUserPreferencesResolver,
)
from omnibase.model_gateway import ModelGateway, ModelMessage, ModelUsage
from omnibase.model_gateway.providers import ModelProviderError


class AgentAlphaError(RuntimeError):
    code = "agent_alpha_error"
    status = 409

    def __init__(self, code: str | None = None, *, status: int | None = None) -> None:
        stable_code = code or self.code
        super().__init__(stable_code)
        self.code = stable_code
        if status is not None:
            self.status = status


class AgentAlphaUnavailable(AgentAlphaError):
    code = "agent_alpha_unavailable"
    status = 503


class AgentAlphaCancelled(AgentAlphaError):
    code = "agent_alpha_cancelled"


class AgentAlphaProviderOutcomeUnknown(AgentAlphaError):
    """The provider boundary was crossed but its durable outcome is ambiguous."""


class UnavailableAgentAlpha:
    """Production default: never touches registry, ledger, RAG or a model provider."""

    def list_profiles(self, **_: object) -> tuple[AlphaAgentProfile, ...]:
        raise AgentAlphaUnavailable("agent_alpha_unavailable")

    def invoke(self, **_: object) -> Iterator[AlphaStreamEvent]:
        raise AgentAlphaUnavailable("agent_alpha_unavailable")

    def cancel(self, **_: object) -> bool:
        raise AgentAlphaUnavailable("agent_alpha_unavailable")


@dataclass(frozen=True, slots=True)
class AlphaLimits:
    """Server-owned ceilings; callers may only tighten, never widen."""

    max_message_characters: int = 32_000
    max_output_tokens: int = 4_096
    max_rag_chunks: int = 8
    max_rag_chunk_characters: int = 1_200
    max_rag_context_characters: int = 8_000
    invocation_deadline_seconds: float = 75.0


def _digest(payload: object) -> str:
    value = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _context_message(chunks: tuple[AlphaContextChunk, ...]) -> str:
    if not chunks:
        return "No workspace knowledge context was retrieved. State uncertainty explicitly."
    parts = [f"[{index}] {chunk.content[:1200]}" for index, chunk in enumerate(chunks, start=1)]
    return "Workspace knowledge context:\n\n" + "\n\n".join(parts)


# Process-local cancellation signal registry.  The router composes a fresh
# AgentAlphaService per request, so the registry must be shared module state:
# one in-flight invoke registers its (tenant, workspace, actor, Event) here
# and the cancel endpoint signals the same Event.  It is a signal only --
# durable terminal state is always written through the ledger.
_CANCELLATION_REGISTRY: dict[str, tuple[str, str, str, Event]] = {}
_CANCELLATION_LOCK = Lock()


class AgentAlphaService:
    """One installed AgentVersion, one model stream and one durable invocation."""

    def __init__(
        self,
        *,
        profiles: AlphaProfileResolver,
        knowledge: AlphaKnowledgeRetriever,
        ledger: AlphaInvocationLedger,
        gateway: ModelGateway,
        gateway_resolver: AlphaGatewayResolver | None = None,
        preferences_resolver: AlphaUserPreferencesResolver | None = None,
        runtime_guard: Callable[[], None] | None = None,
        limits: AlphaLimits | None = None,
    ) -> None:
        self._profiles = profiles
        self._knowledge = knowledge
        self._ledger = ledger
        self._gateway = gateway
        self._gateway_resolver = gateway_resolver
        self._preferences_resolver = preferences_resolver
        self._runtime_guard = runtime_guard
        self._limits = limits or AlphaLimits()

    def _verify_runtime_guard(self) -> None:
        if self._runtime_guard is None:
            return
        try:
            self._runtime_guard()
        except AgentAlphaError:
            raise
        except RuntimeError as exc:
            raise AgentAlphaUnavailable("agent_alpha_runtime_guard_unavailable") from exc

    def list_profiles(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
    ) -> tuple[AlphaAgentProfile, ...]:
        try:
            return self._profiles.list_available(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
            )
        except AlphaAdapterUnavailable as exc:
            raise AgentAlphaUnavailable(str(exc)) from exc
        except AlphaAdapterError as exc:
            raise AgentAlphaError(str(exc)) from exc

    def invoke(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        workspace_id: str,
        actor_user_id: str,
        agent_version_id: str,
        message: str,
        top_k: int,
        idempotency_key: str,
        retry_of: str | None,
    ) -> Iterator[AlphaStreamEvent]:
        self._verify_runtime_guard()
        if len(message) > self._limits.max_message_characters:
            raise AgentAlphaError("agent_alpha_message_too_large")
        if top_k < 1 or top_k > self._limits.max_rag_chunks:
            raise AgentAlphaError("agent_alpha_top_k_exceeded")
        try:
            selection = (
                self._gateway_resolver.resolve(
                    tenant_id=tenant_id,
                    tenant_schema=tenant_schema,
                    actor_user_id=actor_user_id,
                )
                if self._gateway_resolver is not None
                else AlphaGatewaySelection(
                    gateway=self._gateway,
                    credential_source="operator_default",
                    configuration_digest=_digest(
                        {
                            "credential_source": "operator_default",
                            "provider_id": self._gateway.provider_id,
                            "model_id": self._gateway.model_id,
                        }
                    ),
                )
            )
        except RuntimeError as exc:
            raise AgentAlphaUnavailable(str(exc)) from exc
        try:
            preferences = (
                self._preferences_resolver.resolve_preferences(
                    tenant_schema=tenant_schema,
                    actor_user_id=actor_user_id,
                )
                if self._preferences_resolver is not None
                else AlphaUserPreferences(
                    assistant_name="Omni",
                    assistant_tone="balanced",
                    assistant_instructions="",
                )
            )
        except RuntimeError as exc:
            raise AgentAlphaUnavailable(str(exc)) from exc
        profile, chunks, identity = self._reserve_invocation(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            agent_version_id=agent_version_id,
            message=message,
            top_k=top_k,
            idempotency_key=idempotency_key,
            retry_of=retry_of,
            preferences=preferences,
            selection=selection,
        )
        cancellation = Event()
        with _CANCELLATION_LOCK:
            _CANCELLATION_REGISTRY[identity.invocation_id] = (
                tenant_id,
                workspace_id,
                actor_user_id,
                cancellation,
            )
        return self._stream(
            identity=identity,
            profile=profile,
            chunks=chunks,
            message=message,
            cancellation=cancellation,
            selection=selection,
            preferences=preferences,
        )

    def _stream(
        self,
        *,
        identity: AlphaInvocationIdentity,
        profile: AlphaAgentProfile,
        chunks: tuple[AlphaContextChunk, ...],
        message: str,
        cancellation: Event,
        selection: AlphaGatewaySelection,
        preferences: AlphaUserPreferences,
    ) -> Iterator[AlphaStreamEvent]:
        """SSE event stream for one durable invocation (or its exact replay)."""
        try:
            self._verify_runtime_guard()
            yield AlphaStreamEvent(
                kind="meta",
                payload={
                    "invocation_id": identity.invocation_id,
                    "task_id": identity.task_id,
                    "agent_definition_id": profile.agent_definition_id,
                    "agent_version_id": profile.agent_version_id,
                    "agent_name": profile.display_name,
                    "assistant_name": preferences.assistant_name,
                    "provider_id": selection.gateway.provider_id,
                    "requested_model_id": selection.gateway.model_id,
                    "credential_source": selection.credential_source,
                    "tools_enabled": False,
                },
            )
            if identity.replayed_state is not None:
                # Exact replay of a terminal invocation: re-expose the
                # durable identity without calling the provider or writing
                # any new ledger rows.
                yield AlphaStreamEvent(
                    kind="error",
                    payload={"code": "agent_alpha_exact_replay"},
                )
                return
            yield AlphaStreamEvent(
                kind="citations",
                payload={
                    "citations": [
                        {
                            "index": index,
                            "chunk_id": chunk.chunk_id,
                            "document_id": chunk.document_id,
                            "snippet": chunk.content[:200],
                            "page_number": chunk.page_number,
                            "score": chunk.score,
                        }
                        for index, chunk in enumerate(chunks, start=1)
                    ]
                },
            )
            self._verify_runtime_guard()
            yield from self._emit_provider_stream(
                identity=identity,
                profile=profile,
                chunks=chunks,
                message=message,
                cancellation=cancellation,
                selection=selection,
                preferences=preferences,
            )
        except ModelProviderError:
            self._ledger.fail(
                identity=identity,
                outcome="unknown",
                error_code="agent_alpha_provider_outcome_unknown",
            )
            yield AlphaStreamEvent(
                kind="error",
                payload={"code": "agent_alpha_provider_unavailable"},
            )
        except AgentAlphaProviderOutcomeUnknown as exc:
            self._ledger.fail(
                identity=identity,
                outcome="unknown",
                error_code=exc.code,
            )
            yield AlphaStreamEvent(kind="error", payload={"code": exc.code})
        except AgentAlphaError as exc:
            self._ledger.fail(
                identity=identity,
                outcome="failed",
                error_code=exc.code,
            )
            yield AlphaStreamEvent(kind="error", payload={"code": exc.code})
        except Exception:
            self._ledger.fail(
                identity=identity,
                outcome="failed",
                error_code="agent_alpha_execution_failed",
            )
            raise
        except GeneratorExit:
            # Client closed the SSE stream before a terminal event.  The
            # provider outcome is unknown at this point: record it as
            # unknown/reconciliation, never as a fabricated cancellation.
            self._ledger.fail(
                identity=identity,
                outcome="unknown",
                error_code="agent_alpha_sse_disconnected",
            )
            raise
        finally:
            with _CANCELLATION_LOCK:
                _CANCELLATION_REGISTRY.pop(identity.invocation_id, None)

    def _emit_provider_stream(
        self,
        *,
        identity: AlphaInvocationIdentity,
        profile: AlphaAgentProfile,
        chunks: tuple[AlphaContextChunk, ...],
        message: str,
        cancellation: Event,
        selection: AlphaGatewaySelection,
        preferences: AlphaUserPreferences,
    ) -> Iterator[AlphaStreamEvent]:
        """One Model Gateway stream bounded by the server-owned deadline."""
        answer: list[str] = []
        usage = ModelUsage(input_tokens=0, output_tokens=0, total_tokens=0)
        actual_model_id: str | None = None
        deadline = time.monotonic() + self._limits.invocation_deadline_seconds
        tone_instruction = {
            "concise": "Keep answers concise and direct.",
            "balanced": "Balance a direct conclusion with enough supporting explanation.",
            "detailed": "Give a detailed answer with explicit reasoning and structure.",
        }[preferences.assistant_tone]
        personalized_instructions = (
            f"{profile.instructions}\n\n"
            f"Your user-facing name is {preferences.assistant_name}. {tone_instruction}"
        )
        if preferences.assistant_instructions:
            personalized_instructions += (
                "\nUser-specific instructions (lower priority than safety and AgentVersion rules):\n"
                + preferences.assistant_instructions
            )
        messages = (
            ModelMessage(role="system", content=personalized_instructions),
            ModelMessage(role="system", content=_context_message(chunks)),
            ModelMessage(role="user", content=message),
        )
        self._verify_runtime_guard()
        for chunk in selection.gateway.stream(
            messages,
            max_output_tokens=min(profile.max_context_tokens, self._limits.max_output_tokens),
            temperature=0.2,
        ):
            self._verify_runtime_guard()
            if time.monotonic() > deadline:
                raise AgentAlphaProviderOutcomeUnknown("agent_alpha_invocation_deadline_exceeded")
            actual_model_id = chunk.actual_model_id
            if cancellation.is_set():
                self._ledger.fail(
                    identity=identity,
                    outcome="cancelled",
                    error_code="agent_alpha_cancelled",
                )
                yield AlphaStreamEvent(
                    kind="cancelled",
                    payload={"invocation_id": identity.invocation_id},
                )
                return
            if chunk.content:
                answer.append(chunk.content)
                yield AlphaStreamEvent(kind="chunk", payload={"content": chunk.content})
            if chunk.usage is not None:
                usage = chunk.usage
        final_answer = "".join(answer)
        if actual_model_id is None:
            raise AgentAlphaProviderOutcomeUnknown("agent_alpha_model_identity_missing")
        result_digest = _digest(
            {
                "answer": final_answer,
                "provider_id": selection.gateway.provider_id,
                "model_id": actual_model_id,
            }
        )
        self._ledger.complete(
            identity=identity,
            result_digest=result_digest,
            usage=usage,
        )
        yield AlphaStreamEvent(
            kind="usage",
            payload={
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "total_tokens": usage.total_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
            },
        )
        yield AlphaStreamEvent(
            kind="done",
            payload={
                "invocation_id": identity.invocation_id,
                "answer": final_answer,
                "provider_id": selection.gateway.provider_id,
                "actual_model_id": actual_model_id,
                "credential_source": selection.credential_source,
                "usage": {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                    "reasoning_tokens": usage.reasoning_tokens,
                },
            },
        )

    def _reserve_invocation(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        workspace_id: str,
        actor_user_id: str,
        agent_version_id: str,
        message: str,
        top_k: int,
        idempotency_key: str,
        retry_of: str | None,
        preferences: AlphaUserPreferences,
        selection: AlphaGatewaySelection,
    ) -> tuple[AlphaAgentProfile, tuple[AlphaContextChunk, ...], AlphaInvocationIdentity]:
        """Resolve the live tool-free profile and durably reserve the task.

        Runs entirely before the provider boundary: the caller-owned ledger
        transaction persists Task/Run/Step/Attempt/Lease/Budget/Effect or
        returns the exact replay identity, and adapter failures are mapped to
        stable service errors.
        """
        try:
            profile = self._profiles.resolve(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                agent_version_id=agent_version_id,
            )
            if profile.allowed_tool_ids:
                raise AgentAlphaError("agent_alpha_tools_forbidden")
            request_hash = _digest(
                {
                    "workspace_id": workspace_id,
                    "agent_version_id": profile.agent_version_id,
                    "agent_version_digest": profile.agent_version_digest,
                    "message": message,
                    "top_k": top_k,
                    "retry_of": retry_of,
                    "assistant_name": preferences.assistant_name,
                    "assistant_tone": preferences.assistant_tone,
                    "assistant_instructions_digest": _digest(preferences.assistant_instructions),
                    "credential_source": selection.credential_source,
                    "credential_id": selection.credential_id,
                    "provider_id": selection.gateway.provider_id,
                    "requested_model_id": selection.gateway.model_id,
                    "provider_configuration_digest": selection.configuration_digest,
                }
            )
            identity = self._ledger.begin(
                tenant_id=tenant_id,
                workspace_id=workspace_id,
                actor_user_id=actor_user_id,
                profile=profile,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
                retry_of=retry_of,
            )
            if identity.replayed_state is not None:
                chunks: tuple[AlphaContextChunk, ...] = ()
            else:
                chunks = self._knowledge.retrieve(
                    tenant_id=tenant_id,
                    tenant_schema=tenant_schema,
                    workspace_id=workspace_id,
                    query=message,
                    top_k=top_k,
                )
        except AlphaAdapterUnavailable as exc:
            raise AgentAlphaUnavailable(str(exc)) from exc
        except AlphaAdapterError as exc:
            raise AgentAlphaError(str(exc)) from exc
        return profile, chunks, identity

    def cancel(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        actor_user_id: str,
        invocation_id: str,
    ) -> bool:
        with _CANCELLATION_LOCK:
            record = _CANCELLATION_REGISTRY.get(invocation_id)
        if record is None:
            return False
        owner_tenant_id, owner_workspace_id, owner_actor_user_id, cancellation = record
        if (tenant_id, workspace_id, actor_user_id) != (
            owner_tenant_id,
            owner_workspace_id,
            owner_actor_user_id,
        ):
            return False
        cancellation.set()
        return True


__all__ = [
    "AgentAlphaCancelled",
    "AgentAlphaError",
    "AgentAlphaProviderOutcomeUnknown",
    "AgentAlphaService",
    "AgentAlphaUnavailable",
    "AlphaLimits",
    "UnavailableAgentAlpha",
]
