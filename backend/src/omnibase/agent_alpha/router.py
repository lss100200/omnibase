"""Fail-closed Browser API for the engineering-only single-Agent Alpha."""

from __future__ import annotations

import json
import os
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
    runtime_lite_agent_enabled,
)
from omnibase.agent_alpha.personal import (
    PERSONAL_RUNTIME_PROFILE_ENV,
    PersonalAlphaConfigurationError,
    PersonalCanaryAgentAlpha,
    build_personal_agent_alpha,
    personal_alpha_posture,
    resolve_personal_runtime_profile,
)
from omnibase.agent_alpha.schemas import (
    AlphaCancelResponse,
    AlphaInvokeRequest,
    AlphaPracticeRunRequest,
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
from omnibase.agent_practice.alpha_coordinator import DurablePersonalPracticeCoordinator
from omnibase.agent_practice.posture import personal_practice_posture
from omnibase.tenants.dependencies import TenantContext, get_current_tenant

router = APIRouter(prefix="/workspaces/{workspace_id}/agent-alpha", tags=["agent-alpha"])
_SAFE_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


def get_agent_alpha(
    workspace_id: str,
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentAlphaService | PersonalCanaryAgentAlpha | UnavailableAgentAlpha:
    """Keep the Lite product entry point independently fail-closed.

    The Lite gate is a *product* entry guard, never an authorization fact. The
    only supported invocation mode is ``no_tool``, carried by the older P5.2C
    ``build_engineering_agent_alpha`` seam; the formal P5.4B builder
    ``build_engineering_single_agent_executor`` is formally connected to this
    product loop (proven through a formal integration fixture with the real
    persisted authority chain) but is never assembled in the Browser request
    path — the P5.4B disposable Gate assembles it separately with real
    persisted authority.

    The gate is resolved through ``runtime_lite_agent_enabled()``, which
    explicitly reads ``AGENT_LITE_ENGINEERING_ENABLED`` from the process
    environment, so setting the flag genuinely enables the route. When the
    gate is open this factory delegates to the Alpha engineering seam, which
    itself remains fail-closed until every P5.2C dependency (environment,
    Phase 5 gates, provider gateway, migration head 0016) holds.
    ``lite_agent_posture`` exposes the honest single-mode posture to the
    status endpoint without authorizing anything.
    """
    selected_profile = os.environ.get(PERSONAL_RUNTIME_PROFILE_ENV)
    try:
        personal_selected = resolve_personal_runtime_profile(selected_profile)
    except PersonalAlphaConfigurationError:
        return UnavailableAgentAlpha()
    if personal_selected:
        return build_personal_agent_alpha(
            tenant_id=ctx.tenant_id,
            workspace_id=_logical_uuid(workspace_id),
            actor_user_id=ctx.user_id,
        )
    if not runtime_lite_agent_enabled():
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
    workspace_id: str,
    ctx: TenantContext = Depends(get_current_tenant),
) -> AlphaStatusResponse:
    posture = engineering_alpha_status()
    lite = lite_agent_posture()
    selected_profile = os.environ.get(PERSONAL_RUNTIME_PROFILE_ENV)
    try:
        personal_profile_selected = resolve_personal_runtime_profile(selected_profile)
        personal_profile_invalid = False
    except PersonalAlphaConfigurationError:
        personal_profile_selected = False
        personal_profile_invalid = True
    personal = None
    if personal_profile_selected or personal_profile_invalid:
        personal = personal_alpha_posture(
            tenant_id=ctx.tenant_id,
            workspace_id=_logical_uuid(workspace_id),
            actor_user_id=ctx.user_id,
            profile=selected_profile,
        )
    practice = personal_practice_posture(os.environ, participant_count=1)
    return AlphaStatusResponse(
        engineering_implemented=True,
        lite_gate_enabled=bool(lite["lite_gate_enabled"]),
        engineering_assembled=posture["assembled"],
        engineering_flag_enabled=posture["engineering_flag_enabled"],
        environment_allowed=posture["environment_allowed"],
        phase5_gates_all_false=posture["phase5_gates_all_false"],
        production_activation_allowed=bool(personal and personal.assembled),
        tools_enabled=False,
        multi_agent_enabled=False,
        formal_builder=FORMAL_BUILDER_NAME,
        alpha_builder=ALPHA_BUILDER_NAME,
        supported_invocation_modes=list(SUPPORTED_INVOCATION_MODES),
        formal_builder_integration=str(lite["formal_builder_integration"]),
        engineering_composition_ready=bool(lite["engineering_composition_ready"]),
        activation_allowed=bool(lite["activation_allowed"]),
        expected_migration_head=str(lite["expected_migration_head"]),
        runtime_profile=(
            "personal_single_owner"
            if personal_profile_selected
            else "engineering_lite"
            if bool(lite["lite_gate_enabled"]) and not personal_profile_invalid
            else "locked"
        ),
        personal_runtime_state=(
            "invalid/veto"
            if personal_profile_invalid
            else personal.canary_state
            if personal is not None
            else "inactive"
        ),
        personal_runtime_active=bool(personal and personal.assembled),
        personal_canary_id=personal.canary_id if personal is not None else None,
        personal_canary_expires_at=(personal.canary_expires_at if personal is not None else None),
        personal_practice_active=bool(
            personal is not None and personal.assembled and practice.activation_allowed
        ),
        personal_practice_blockers=list(practice.blockers),
    )


@router.get("/profiles", response_model=AlphaProfileList)
def list_alpha_profiles(
    workspace_id: str,
    alpha: AgentAlphaService | PersonalCanaryAgentAlpha | UnavailableAgentAlpha = Depends(
        get_agent_alpha
    ),
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
    alpha: AgentAlphaService | PersonalCanaryAgentAlpha | UnavailableAgentAlpha = Depends(
        get_agent_alpha
    ),
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
            reasoning_gear=payload.reasoning_gear,
            idempotency_key=key,
            retry_of=payload.retry_of,
            employee_role_id=payload.employee_role_id,
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


@router.post("/practice")
def run_alpha_practice(
    workspace_id: str,
    payload: AlphaPracticeRunRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    alpha: AgentAlphaService | PersonalCanaryAgentAlpha | UnavailableAgentAlpha = Depends(
        get_agent_alpha
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> StreamingResponse:
    """Run one Owner-declared serial personal roster over durable Alpha calls."""

    if payload.participant_count != len(payload.specialist_roles) + 1:
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "practice_roster_count_mismatch",
                    "message": "Participant count must include all specialists and the parent",
                }
            },
        )
    if len(set(payload.specialist_roles)) != len(payload.specialist_roles):
        raise HTTPException(
            status_code=422,
            detail={
                "error": {
                    "code": "practice_duplicate_role",
                    "message": "Practice specialist roles must be unique",
                }
            },
        )
    posture = personal_practice_posture(
        os.environ,
        participant_count=payload.participant_count,
    )
    if not posture.activation_allowed:
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "personal_practice_unavailable",
                    "message": "Personal Agent practice is unavailable",
                }
            },
        )
    if not isinstance(alpha, PersonalCanaryAgentAlpha):
        raise HTTPException(
            status_code=503,
            detail={
                "error": {
                    "code": "personal_practice_unavailable",
                    "message": "Personal Agent practice is unavailable",
                }
            },
        )
    key = idempotency_key or uuid4().hex
    if _SAFE_KEY.fullmatch(key) is None:
        raise HTTPException(
            status_code=422,
            detail={"error": {"code": "invalid_idempotency_key", "message": "Invalid key"}},
        )
    coordinator = DurablePersonalPracticeCoordinator(alpha)

    def _practice_stream() -> Iterator[str]:
        try:
            for event in coordinator.run(
                tenant_id=ctx.tenant_id,
                tenant_schema=ctx.schema_name,
                workspace_id=_logical_uuid(workspace_id),
                actor_user_id=ctx.user_id,
                agent_version_id=payload.agent_version_id,
                scenario=payload.scenario,
                specialist_roles=tuple(payload.specialist_roles),
                task=payload.task,
                top_k=payload.top_k,
                idempotency_key=key,
            ):
                yield (
                    f"event: {event.kind}\n"
                    f"data: {json.dumps(event.payload, ensure_ascii=False)}\n\n"
                )
        except (AgentAlphaError, RuntimeError, ValueError) as exc:
            code = str(exc)
            if not code.startswith("practice_"):
                code = "personal_practice_failed"
            yield f"event: error\ndata: {json.dumps({'code': code})}\n\n"

    return StreamingResponse(_practice_stream(), media_type="text/event-stream")


@router.post("/invocations/{invocation_id}/cancel", response_model=AlphaCancelResponse)
def cancel_alpha(
    workspace_id: str,
    invocation_id: str,
    alpha: AgentAlphaService | PersonalCanaryAgentAlpha | UnavailableAgentAlpha = Depends(
        get_agent_alpha
    ),
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
