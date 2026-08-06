"""Authenticated Browser routes for real profile and provider-key management."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TypeVar
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from omnibase.core.config import Settings, get_settings
from omnibase.core.rate_limit import enforce_provider_test_rate_limit
from omnibase.tenants.dependencies import CurrentPrincipal, get_current_principal, get_tenant_db
from omnibase.user_settings.schemas import (
    ProviderCredentialActivate,
    ProviderCredentialCreate,
    ProviderCredentialList,
    ProviderCredentialRead,
    ProviderCredentialSecretUpdate,
    ProviderCredentialUpdate,
    ProviderRuntimePosture,
    ProviderTestResult,
    UserProfileRead,
    UserProfileUpdate,
)
from omnibase.user_settings.service import UserSettingsError, UserSettingsService

router = APIRouter(tags=["user-settings"])
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
T = TypeVar("T")


def _request_id(request: Request) -> str:
    candidate = getattr(request.state, "request_id", None)
    if isinstance(candidate, str) and _REQUEST_ID.fullmatch(candidate):
        return candidate
    candidate = request.headers.get("X-Request-Id", "").strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else uuid4().hex


def _service(settings: Settings) -> UserSettingsService:
    return UserSettingsService(settings=settings)


def _as_uuid(value: UUID) -> str:
    return str(value)


def _run_mutation(db: Session, operation: Callable[[], T]) -> T:
    try:
        value = operation()
        db.commit()
        return value
    except UserSettingsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=exc.status,
            detail={"error": {"code": exc.code, "message": exc.code.replace("_", " ")}},
        ) from exc
    except Exception:
        db.rollback()
        raise


def _run_read(operation: Callable[[], T]) -> T:
    try:
        return operation()
    except UserSettingsError as exc:
        raise HTTPException(
            status_code=exc.status,
            detail={"error": {"code": exc.code, "message": exc.code.replace("_", " ")}},
        ) from exc


@router.get("/users/me/profile", response_model=UserProfileRead)
def get_profile(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
    settings: Settings = Depends(get_settings),
) -> UserProfileRead:
    return _run_read(lambda: _service(settings).get_profile(db, user_id=str(principal.user.id)))


@router.patch("/users/me/profile", response_model=UserProfileRead)
def update_profile(
    payload: UserProfileUpdate,
    request: Request,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
    settings: Settings = Depends(get_settings),
) -> UserProfileRead:
    return _run_mutation(
        db,
        lambda: _service(settings).update_profile(
            db,
            tenant_id=str(principal.tenant.id),
            user_id=str(principal.user.id),
            request_id=_request_id(request),
            payload=payload,
        ),
    )


@router.get("/model-provider-credentials", response_model=ProviderCredentialList)
def list_credentials(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
    settings: Settings = Depends(get_settings),
) -> ProviderCredentialList:
    return _run_read(
        lambda: _service(settings).list_credentials(db, user_id=str(principal.user.id))
    )


@router.post(
    "/model-provider-credentials",
    response_model=ProviderCredentialRead,
    status_code=status.HTTP_201_CREATED,
)
def create_credential(
    payload: ProviderCredentialCreate,
    request: Request,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
    settings: Settings = Depends(get_settings),
) -> ProviderCredentialRead:
    return _run_mutation(
        db,
        lambda: _service(settings).create_credential(
            db,
            tenant_id=str(principal.tenant.id),
            user_id=str(principal.user.id),
            request_id=_request_id(request),
            payload=payload,
        ),
    )


@router.patch("/model-provider-credentials/{credential_id}", response_model=ProviderCredentialRead)
def update_credential(
    credential_id: UUID,
    payload: ProviderCredentialUpdate,
    request: Request,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
    settings: Settings = Depends(get_settings),
) -> ProviderCredentialRead:
    return _run_mutation(
        db,
        lambda: _service(settings).update_credential(
            db,
            tenant_id=str(principal.tenant.id),
            user_id=str(principal.user.id),
            credential_id=_as_uuid(credential_id),
            request_id=_request_id(request),
            payload=payload,
        ),
    )


@router.put(
    "/model-provider-credentials/{credential_id}/secret",
    response_model=ProviderCredentialRead,
)
def replace_credential_secret(
    credential_id: UUID,
    payload: ProviderCredentialSecretUpdate,
    request: Request,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
    settings: Settings = Depends(get_settings),
) -> ProviderCredentialRead:
    return _run_mutation(
        db,
        lambda: _service(settings).replace_secret(
            db,
            tenant_id=str(principal.tenant.id),
            user_id=str(principal.user.id),
            credential_id=_as_uuid(credential_id),
            request_id=_request_id(request),
            payload=payload,
        ),
    )


@router.post(
    "/model-provider-credentials/{credential_id}/activate",
    response_model=ProviderCredentialRead,
)
def activate_credential(
    credential_id: UUID,
    payload: ProviderCredentialActivate,
    request: Request,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
    settings: Settings = Depends(get_settings),
) -> ProviderCredentialRead:
    return _run_mutation(
        db,
        lambda: _service(settings).activate(
            db,
            tenant_id=str(principal.tenant.id),
            user_id=str(principal.user.id),
            credential_id=_as_uuid(credential_id),
            request_id=_request_id(request),
            payload=payload,
        ),
    )


@router.delete(
    "/model-provider-credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT
)
def revoke_credential(
    credential_id: UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    _run_mutation(
        db,
        lambda: _service(settings).revoke(
            db,
            tenant_id=str(principal.tenant.id),
            user_id=str(principal.user.id),
            credential_id=_as_uuid(credential_id),
            request_id=_request_id(request),
        ),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/model-provider-credentials/{credential_id}/test",
    response_model=ProviderTestResult,
    dependencies=[Depends(enforce_provider_test_rate_limit)],
)
def test_credential(
    credential_id: UUID,
    request: Request,
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
    settings: Settings = Depends(get_settings),
) -> ProviderTestResult:
    return _run_mutation(
        db,
        lambda: _service(settings).test_credential(
            db,
            tenant_id=str(principal.tenant.id),
            user_id=str(principal.user.id),
            credential_id=_as_uuid(credential_id),
            request_id=_request_id(request),
        ),
    )


@router.get("/model-provider-runtime", response_model=ProviderRuntimePosture)
def provider_runtime_posture(
    principal: CurrentPrincipal = Depends(get_current_principal),
    db: Session = Depends(get_tenant_db),
    settings: Settings = Depends(get_settings),
) -> ProviderRuntimePosture:
    return _run_read(lambda: _service(settings).runtime_posture(db, user_id=str(principal.user.id)))


__all__ = ["router"]
