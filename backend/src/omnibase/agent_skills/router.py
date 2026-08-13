"""Authenticated Browser routes for P6.1 native instruction Skills."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from omnibase.agent_skills.control import NativeSkillControlService, translate_skill_error
from omnibase.agent_skills.native_catalog import list_native_skills
from omnibase.agent_skills.schemas import (
    NativeSkillDetail,
    NativeSkillInstallCreate,
    NativeSkillList,
    NativeSkillRead,
    SkillInstallationList,
    SkillInstallationRead,
)
from omnibase.tenants.dependencies import CurrentPrincipal, get_current_principal, get_tenant_db

router = APIRouter(tags=["native-skills"])
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
T = TypeVar("T")


def _request_id(request: Request) -> str:
    candidate = getattr(request.state, "request_id", None)
    if isinstance(candidate, str) and _REQUEST_ID.fullmatch(candidate):
        return candidate
    candidate = request.headers.get("X-Request-Id", "").strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else uuid4().hex


def _uuid(value: UUID) -> str:
    return str(value)


def _mutation(db: Session, operation: Callable[[], T]) -> T:
    try:
        value = operation()
        db.commit()
        return value
    except Exception as exc:
        db.rollback()
        error = translate_skill_error(exc)
        raise HTTPException(
            status_code=error.status,
            detail={"error": {"code": error.code, "message": error.code.replace("_", " ")}},
        ) from exc


@router.get("/skills", response_model=NativeSkillList)
def list_skills(
    principal: CurrentPrincipal = Depends(get_current_principal),
) -> NativeSkillList:
    del principal
    items = [
        NativeSkillRead(
            stable_logical_key=item.definition.stable_logical_key,
            display_name=item.definition.display_name,
            description=item.summary,
            category=item.category,
            semantic_version=item.version.version,
            manifest_digest=item.version.canonical_digest(),
        )
        for item in list_native_skills()
    ]
    return NativeSkillList(items=items, total=len(items))


@router.get("/skills/{stable_key}", response_model=NativeSkillDetail)
def get_skill(
    stable_key: str,
    principal: CurrentPrincipal = Depends(get_current_principal),
) -> NativeSkillDetail:
    del principal
    item = next(
        (
            candidate
            for candidate in list_native_skills()
            if candidate.definition.stable_logical_key == stable_key
        ),
        None,
    )
    if item is None:
        raise HTTPException(
            status_code=404,
            detail={"error": {"code": "native_skill_not_found", "message": "not found"}},
        )
    return NativeSkillDetail(
        stable_logical_key=item.definition.stable_logical_key,
        display_name=item.definition.display_name,
        description=item.summary,
        category=item.category,
        semantic_version=item.version.version,
        manifest_digest=item.version.canonical_digest(),
        instructions=item.version.instructions,
    )


@router.get(
    "/workspaces/{workspace_id}/agents/{agent_version_id}/skill-installations",
    response_model=SkillInstallationList,
)
def list_skill_installations(
    workspace_id: UUID,
    agent_version_id: UUID,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
) -> SkillInstallationList:
    return NativeSkillControlService(db).list_installations(
        tenant_id=principal.tenant_id,
        tenant_schema=principal.schema_name,
        owner_user_id=principal.user_id,
        workspace_id=_uuid(workspace_id),
        agent_version_id=_uuid(agent_version_id),
    )


@router.post(
    "/skills/{stable_key}/install",
    response_model=SkillInstallationRead,
    status_code=status.HTTP_201_CREATED,
)
def install_native_skill(
    stable_key: str,
    payload: NativeSkillInstallCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
) -> SkillInstallationRead:
    return _mutation(
        db,
        lambda: NativeSkillControlService(db).install_native(
            tenant_id=principal.tenant_id,
            tenant_schema=principal.schema_name,
            owner_user_id=principal.user_id,
            workspace_id=payload.workspace_id,
            agent_version_id=payload.agent_version_id,
            stable_key=stable_key,
            expected_manifest_digest=payload.expected_manifest_digest,
            idempotency_key=idempotency_key,
            request_id=_request_id(request),
        ),
    )


@router.post(
    "/workspaces/{workspace_id}/agents/{agent_version_id}/skill-installations/"
    "{installation_id}/disable",
    response_model=SkillInstallationRead,
)
def disable_skill_installation(
    workspace_id: UUID,
    agent_version_id: UUID,
    installation_id: UUID,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
) -> SkillInstallationRead:
    return _mutation(
        db,
        lambda: NativeSkillControlService(db).disable(
            tenant_id=principal.tenant_id,
            tenant_schema=principal.schema_name,
            owner_user_id=principal.user_id,
            workspace_id=_uuid(workspace_id),
            agent_version_id=_uuid(agent_version_id),
            installation_id=_uuid(installation_id),
            idempotency_key=idempotency_key,
            request_id=_request_id(request),
        ),
    )


__all__ = ["router"]
