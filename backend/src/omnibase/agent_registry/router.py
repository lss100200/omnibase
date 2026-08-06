"""P5.1C Browser Agent Registry control API (logical, fail-closed).

All routes live under the Browser ``/api/v1`` control plane.  The default
dependency composition rejects every operation with ``agent_registry_unavailable``
(HTTP 503) before any registry table is accessed; tests and the disposable Gate
override ``get_registry_control_plane`` with the DB-backed service.
"""

from __future__ import annotations

import re
from typing import Never
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from omnibase.agent_registry.control import (
    AgentRegistryControlError,
    AgentRegistryControlService,
    RegistryControlPlaneUnavailable,
    UnavailableAgentRegistryControlPlane,
)
from omnibase.agent_registry.schemas import (
    AgentBuilderCreate,
    AgentBuilderCreateResult,
    AgentDefinitionList,
    AgentDefinitionRead,
    AgentInstallationList,
    AgentInstallationRead,
    AgentInstallCreate,
    AgentRollbackRequest,
    AgentUpgradeRequest,
    AgentVersionList,
    AgentVersionRead,
    RegistryApiError,
    RegistryApiErrorEnvelope,
)
from omnibase.agent_registry.service import (
    RegistryConflictError,
    RegistryNotFoundError,
    RegistryStateError,
)
from omnibase.tenants.dependencies import TenantContext, get_current_tenant, get_tenant_db
from omnibase.workspaces.service import WorkspacePolicyDenied

router = APIRouter(prefix="/agent-definitions", tags=["agent-definitions"])
installation_router = APIRouter(
    prefix="/workspaces/{workspace_id}/agent-installations",
    tags=["agent-installations"],
)
builder_router = APIRouter(
    prefix="/workspaces/{workspace_id}/agents",
    tags=["agent-builder"],
)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _request_id(request: Request) -> str:
    candidate = getattr(request.state, "request_id", None)
    if isinstance(candidate, str) and _REQUEST_ID.fullmatch(candidate):
        return candidate
    candidate = request.headers.get("X-Request-Id", "").strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else uuid4().hex


def get_registry_control_plane() -> UnavailableAgentRegistryControlPlane:
    """Default fail-closed wiring: reject before any registry table is touched.

    Tests and the disposable Gate override this dependency with the DB-backed
    ``AgentRegistryControlService`` (constructed from their own session).
    """
    return UnavailableAgentRegistryControlPlane()


@builder_router.post(
    "",
    response_model=AgentBuilderCreateResult,
    status_code=status.HTTP_201_CREATED,
)
def create_custom_agent(
    workspace_id: str,
    payload: AgentBuilderCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> AgentBuilderCreateResult:
    """Create a sealed, low-risk, tool-free Agent and optionally install it."""

    try:
        return AgentRegistryControlService(db).create_custom_agent(
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            workspace_id=_as_uuid(workspace_id),
            request_id=_request_id(request),
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        _raise_control(exc)


def _http_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(
        status_code=status_code,
        detail=RegistryApiErrorEnvelope(
            error=RegistryApiError(code=code, message=message)
        ).model_dump(),
    )


def _raise_control(exc: Exception) -> Never:
    if isinstance(exc, RegistryControlPlaneUnavailable):
        raise _http_error(503, exc.code, exc.message) from exc
    if isinstance(exc, AgentRegistryControlError):
        raise _http_error(exc.status, exc.code, exc.message) from exc
    if isinstance(exc, (RegistryNotFoundError, WorkspacePolicyDenied)):
        raise _http_error(404, "not_found", "Not found") from exc
    if isinstance(exc, (RegistryConflictError, RegistryStateError)):
        code = str(exc.args[0]) if exc.args else "conflict"
        raise _http_error(409, code, code.replace("_", " ")) from exc
    raise exc


def _as_uuid(value: str) -> str:
    try:
        return str(UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise _http_error(
            422,
            "invalid_logical_identifier",
            "Logical identifier must be a valid UUID",
        ) from exc


@router.get("", response_model=AgentDefinitionList)
def list_agent_definitions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    control: AgentRegistryControlService | UnavailableAgentRegistryControlPlane = Depends(
        get_registry_control_plane
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentDefinitionList:
    try:
        return control.list_definitions(tenant_id=ctx.tenant_id, limit=limit, offset=offset)
    except Exception as exc:
        _raise_control(exc)


@router.get("/{agent_definition_id}", response_model=AgentDefinitionRead)
def get_agent_definition(
    agent_definition_id: str,
    control: AgentRegistryControlService | UnavailableAgentRegistryControlPlane = Depends(
        get_registry_control_plane
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentDefinitionRead:
    try:
        return control.get_definition(
            tenant_id=ctx.tenant_id, definition_id=_as_uuid(agent_definition_id)
        )
    except Exception as exc:
        _raise_control(exc)


@router.get("/{agent_definition_id}/versions", response_model=AgentVersionList)
def list_agent_versions(
    agent_definition_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    control: AgentRegistryControlService | UnavailableAgentRegistryControlPlane = Depends(
        get_registry_control_plane
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentVersionList:
    try:
        return control.list_versions(
            tenant_id=ctx.tenant_id,
            definition_id=_as_uuid(agent_definition_id),
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        _raise_control(exc)


@router.get("/{agent_definition_id}/versions/{agent_version_id}", response_model=AgentVersionRead)
def get_agent_version(
    agent_definition_id: str,
    agent_version_id: str,
    control: AgentRegistryControlService | UnavailableAgentRegistryControlPlane = Depends(
        get_registry_control_plane
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentVersionRead:
    try:
        return control.get_version(
            tenant_id=ctx.tenant_id,
            definition_id=_as_uuid(agent_definition_id),
            version_id=_as_uuid(agent_version_id),
        )
    except Exception as exc:
        _raise_control(exc)


@installation_router.get("", response_model=AgentInstallationList)
def list_agent_installations(
    workspace_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    control: AgentRegistryControlService | UnavailableAgentRegistryControlPlane = Depends(
        get_registry_control_plane
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentInstallationList:
    try:
        return control.list_installations(
            tenant_id=ctx.tenant_id,
            workspace_id=_as_uuid(workspace_id),
            user_id=ctx.user_id,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        _raise_control(exc)


@installation_router.get("/{binding_id}", response_model=AgentInstallationRead)
def get_agent_installation(
    workspace_id: str,
    binding_id: str,
    control: AgentRegistryControlService | UnavailableAgentRegistryControlPlane = Depends(
        get_registry_control_plane
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentInstallationRead:
    try:
        return control.get_installation(
            tenant_id=ctx.tenant_id,
            workspace_id=_as_uuid(workspace_id),
            user_id=ctx.user_id,
            binding_id=_as_uuid(binding_id),
        )
    except Exception as exc:
        _raise_control(exc)


@installation_router.post(
    "", response_model=AgentInstallationRead, status_code=status.HTTP_201_CREATED
)
def install_agent(
    workspace_id: str,
    payload: AgentInstallCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    control: AgentRegistryControlService | UnavailableAgentRegistryControlPlane = Depends(
        get_registry_control_plane
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentInstallationRead:
    try:
        return control.install(
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            workspace_id=_as_uuid(workspace_id),
            request_id=_request_id(request),
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        _raise_control(exc)


@installation_router.post("/{binding_id}/disable", response_model=AgentInstallationRead)
def disable_agent_installation(
    workspace_id: str,
    binding_id: str,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    control: AgentRegistryControlService | UnavailableAgentRegistryControlPlane = Depends(
        get_registry_control_plane
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentInstallationRead:
    try:
        return control.disable(
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            workspace_id=_as_uuid(workspace_id),
            binding_id=_as_uuid(binding_id),
            request_id=_request_id(request),
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        _raise_control(exc)


@installation_router.post("/{binding_id}/upgrade", response_model=AgentInstallationRead)
def upgrade_agent_installation(
    workspace_id: str,
    binding_id: str,
    payload: AgentUpgradeRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    control: AgentRegistryControlService | UnavailableAgentRegistryControlPlane = Depends(
        get_registry_control_plane
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentInstallationRead:
    try:
        return control.upgrade(
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            workspace_id=_as_uuid(workspace_id),
            binding_id=_as_uuid(binding_id),
            request_id=_request_id(request),
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        _raise_control(exc)


@installation_router.post("/{binding_id}/rollback", response_model=AgentInstallationRead)
def rollback_agent_installation(
    workspace_id: str,
    binding_id: str,
    payload: AgentRollbackRequest,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    control: AgentRegistryControlService | UnavailableAgentRegistryControlPlane = Depends(
        get_registry_control_plane
    ),
    ctx: TenantContext = Depends(get_current_tenant),
) -> AgentInstallationRead:
    try:
        return control.rollback(
            tenant_id=ctx.tenant_id,
            actor_user_id=ctx.user_id,
            workspace_id=_as_uuid(workspace_id),
            binding_id=_as_uuid(binding_id),
            request_id=_request_id(request),
            payload=payload,
            idempotency_key=idempotency_key,
        )
    except Exception as exc:
        _raise_control(exc)


__all__ = [
    "builder_router",
    "get_registry_control_plane",
    "installation_router",
    "router",
]
