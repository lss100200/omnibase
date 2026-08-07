"""Fail-closed Browser API for the engineering-only single-Agent Alpha."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Never
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse

from omnibase.agent_alpha.engineering import build_engineering_agent_alpha, engineering_alpha_status
from omnibase.agent_alpha.lite import (
    ALPHA_BUILDER_NAME,
    FORMAL_BUILDER_NAME,
    SUPPORTED_INVOCATION_MODES,
    lite_agent_posture,
    resolve_lite_agent_flag,
)
from omnibase.agent_alpha.schemas import (
    AlphaCancelResponse,
    AlphaInvokeRequest,
    AlphaProfileList,
    AlphaProfileRead,
    AlphaStatusResponse,
)
from omnibase.agent_alpha.service import (
    AgentAlphaError,
    AgentAlphaService,
    AgentAlphaUnavailable,
    UnavailableAgentAlpha,
)
from omnibase.tenants.dependencies import TenantContext, get_current_tenant

router = APIRouter(prefix="/workspaces/{workspace_id}/agent-alpha", tags=["agent-alpha"])
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def get_agent_alpha() -> AgentAlphaService | UnavailableAgentAlpha:
    """Keep the Lite product entry point independently fail-closed.

    The Lite gate is a *product* entry guard, never an authorization fact. The
    knowledge-search-capable path is served by the formal P5.4B composition
    builder ``build_engineering_single_agent_executor`` (installed with
    ``LiveRuntimeAuthorityValidator`` and ``CapabilityGatewayKnowledgeSearchPort``
    by ``agent_executor.engineering``); the older P5.2C
    ``build_engineering_agent_alpha`` seam only carries the tool-free
    RAG-retrieval product loop and never authorizes knowledge search on its own.

    This factory returns ``UnavailableAgentAlpha`` whenever the Lite gate is
    closed. When the gate is open it delegates to the Alpha engineering seam,
    which itself remains fail-closed until every P5.2C dependency
    (environment, Phase 5 gates, provider gateway, migration head 0012) holds;
    the formal P5.4B builder is assembled separately by its own disposable
    Gate and is not constructed here. ``lite_agent_posture`` exposes the
    honest builder chain to the status endpoint without authorizing anything.
    """
    if not resolve_lite_agent_flag():
        return UnavailableAgentAlpha()
    return build_engineering_agent_alpha()


def _logical_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "invalid_logical_identifier",
                    "message": "Logical identifier must be a UUID",
                }
            },
        ) from exc


def _raise_alpha(exc: Exception) -> Never:
    if isinstance(exc, AgentAlphaError):
        raise HTTPException(
            status_code=exc.status,
            detail={"error": {"code": exc.code, "message": exc.code.replace("_", " ")}},
        ) from exc
    raise exc


@router.get("/status", response_model=AlphaStatusResponse)
def alpha_status(
    ctx: TenantContext = Depends(get_current_tenant),
) -> AlphaStatusResponse:
    del ctx
    posture = engineering_alpha_status()
    lite = lite_agent_posture()
    return AlphaStatusResponse(
        engineering_implemented=True,
        lite_gate_enabled=bool(lite["lite_gate_enabled"]),
        engineering_assembled=posture["assembled"],
        engineering_flag_enabled=posture["engineering_flag_enabled"],
        environment_allowed=posture["environment_allowed"],
        phase5_gates_all_false=posture["phase5_gates_all_false"],
        production_activation_allowed=False,
        tools_enabled=False,
        multi_agent_enabled=False,
        knowledge_search_read_only_enabled=bool(lite["knowledge_search_read_only_enabled"]),
        formal_builder=FORMAL_BUILDER_NAME,
        alpha_builder=ALPHA_BUILDER_NAME,
        supported_invocation_modes=list(SUPPORTED_INVOCATION_MODES),
        formal_builder_flag_enabled=bool(lite["formal_builder_flag_enabled"]),
        expected_migration_head=str(lite["expected_migration_head"]),
    )


@router.get("/profiles", response_model=AlphaProfileList)
def list_alpha_profiles(
    workspace_id: str,
    alpha: AgentAlphaService | UnavailableAgentAlpha = Depends(get_agent_alpha),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AlphaProfileList:
    try:
        profiles = alpha.list_profiles(
            tenant_id=ctx.tenant_id,
            workspace_id=_logical_uuid(workspace_id),
            actor_user_id=ctx.user_id,
        )
    except Exception as exc:
        _raise_alpha(exc)
    items = [
        AlphaProfileRead(
            agent_definition_id=profile.agent_definition_id,
            agent_version_id=profile.agent_version_id,
            agent_version_digest=profile.agent_version_digest,
            workspace_agent_binding_id=profile.workspace_agent_binding_id,
            display_name=profile.display_name,
        )
        for profile in profiles
    ]
    return AlphaProfileList(items=items, total=len(items))


@router.post("/invoke")
def invoke_alpha(
    workspace_id: str,
    payload: AlphaInvokeRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    alpha: AgentAlphaService | UnavailableAgentAlpha = Depends(get_agent_alpha),
    ctx: TenantContext = Depends(get_current_tenant),
) -> StreamingResponse:
    key = idempotency_key or uuid4().hex
    if _SAFE_KEY.fullmatch(key) is None:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_idempotency_key", "message": "Invalid key"}},
        )
    try:
        events = alpha.invoke(
            tenant_id=ctx.tenant_id,
            tenant_schema=ctx.schema_name,
            workspace_id=_logical_uuid(workspace_id),
            actor_user_id=ctx.user_id,
            agent_version_id=payload.agent_version_id,
            message=payload.message,
            top_k=payload.top_k,
            idempotency_key=key,
            retry_of=payload.retry_of,
        )
    except Exception as exc:
        _raise_alpha(exc)

    def _stream() -> Iterator[str]:
        try:
            for event in events:
                yield (
                    f"event: {event.kind}\n"
                    f"data: {json.dumps(event.payload, ensure_ascii=False)}\n\n"
                )
        except AgentAlphaUnavailable as exc:
            yield ("event: error\n" f"data: {json.dumps({'code': exc.code})}\n\n")

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.post("/invocations/{invocation_id}/cancel", response_model=AlphaCancelResponse)
def cancel_alpha(
    workspace_id: str,
    invocation_id: str,
    alpha: AgentAlphaService | UnavailableAgentAlpha = Depends(get_agent_alpha),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AlphaCancelResponse:
    try:
        requested = alpha.cancel(
            tenant_id=ctx.tenant_id,
            workspace_id=_logical_uuid(workspace_id),
            actor_user_id=ctx.user_id,
            invocation_id=_logical_uuid(invocation_id),
        )
    except Exception as exc:
        _raise_alpha(exc)
    return AlphaCancelResponse(
        invocation_id=_logical_uuid(invocation_id),
        cancellation_requested=requested,
    )


__all__ = ["get_agent_alpha", "router"]
