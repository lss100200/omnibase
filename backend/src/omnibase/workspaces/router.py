"""Browser-facing P34.4 Workspace governance API.

Node daemon, lease heartbeat, fencing, overlay activation, and collaboration
authority endpoints are intentionally not mounted on the Browser ASGI app.
"""

from __future__ import annotations

import re
from typing import Never
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnibase.onboarding import install_default_agent_for_workspace
from omnibase.tenants.dependencies import (
    TenantContext,
    get_current_tenant,
    get_tenant_db,
    require_tenant_admin,
)
from omnibase.workspaces.schemas import (
    LifecycleRequest,
    MembershipList,
    MembershipRead,
    MembershipWrite,
    RestoreRequest,
    RunCreate,
    RunList,
    RunRead,
    ScopeGrantCreate,
    ScopeGrantRead,
    SnapshotCreate,
    SnapshotRead,
    TemplateCreate,
    TemplateList,
    TemplateRead,
    WorkspaceCreate,
    WorkspaceList,
    WorkspaceRead,
)
from omnibase.workspaces.service import (
    LeaseRejected,
    TemplateRejected,
    WorkspaceConflict,
    WorkspaceNotFound,
    create_run,
    create_scope_grant,
    create_snapshot,
    create_workspace,
    get_workspace,
    list_memberships,
    list_runs,
    list_templates,
    list_workspaces,
    register_template,
    request_workspace_state,
    restore_snapshot_new_workspace,
    set_membership_state,
    upsert_membership,
)

router = APIRouter(prefix="/workspaces", tags=["workspaces"])
template_router = APIRouter(prefix="/workspace-templates", tags=["workspace-templates"])
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def _request_id(request: Request) -> str:
    candidate = getattr(request.state, "request_id", None)
    if isinstance(candidate, str) and _REQUEST_ID.fullmatch(candidate):
        return candidate
    candidate = request.headers.get("X-Request-Id", "").strip()
    return candidate if _REQUEST_ID.fullmatch(candidate) else uuid4().hex


def _raise_domain(exc: Exception) -> Never:
    if isinstance(exc, WorkspaceNotFound):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found") from exc
    if isinstance(exc, TemplateRejected):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Template or metadata rejected",
        ) from exc
    if isinstance(exc, (WorkspaceConflict, LeaseRejected, IntegrityError)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Workspace conflict"
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid workspace request",
        ) from exc
    raise exc


@template_router.get("", response_model=TemplateList)
def list_template_versions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> TemplateList:
    items, total = list_templates(db, tenant_id=ctx.tenant_id, limit=limit, offset=offset)
    return TemplateList(
        items=[TemplateRead.model_validate(item) for item in items],
        total=total,
    )


@template_router.post("", response_model=TemplateRead, status_code=status.HTTP_201_CREATED)
def register_template_version(
    payload: TemplateCreate,
    request: Request,
    ctx: TenantContext = Depends(require_tenant_admin),
    db: Session = Depends(get_tenant_db),
) -> TemplateRead:
    try:
        with db.begin():
            template = register_template(
                db,
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.user_id,
                template_key=payload.template_key,
                version=payload.version,
                display_name=payload.display_name,
                template_spec=payload.template_spec,
                supersedes_template_id=(
                    str(payload.supersedes_template_id)
                    if payload.supersedes_template_id is not None
                    else None
                ),
                request_id=_request_id(request),
            )
    except Exception as exc:
        _raise_domain(exc)
    return TemplateRead.model_validate(template)


@router.post("", response_model=WorkspaceRead, status_code=status.HTTP_201_CREATED)
def create_workspace_endpoint(
    payload: WorkspaceCreate,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> WorkspaceRead:
    try:
        with db.begin():
            workspace = create_workspace(
                db,
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.user_id,
                display_name=payload.display_name,
                template_id=str(payload.template_id),
                quota=payload.quota,
                parent_workspace_id=(
                    str(payload.parent_workspace_id)
                    if payload.parent_workspace_id is not None
                    else None
                ),
                idempotency_key=idempotency_key,
                request_id=_request_id(request),
            )
            install_default_agent_for_workspace(
                db,
                tenant_id=ctx.tenant_id,
                actor_user_id=ctx.user_id,
                workspace=workspace,
                request_id=_request_id(request),
            )
    except Exception as exc:
        _raise_domain(exc)
    return WorkspaceRead.model_validate(workspace)


@router.get("", response_model=WorkspaceList)
def list_workspace_endpoint(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> WorkspaceList:
    items, total = list_workspaces(
        db,
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        limit=limit,
        offset=offset,
    )
    return WorkspaceList(items=[WorkspaceRead.model_validate(item) for item in items], total=total)


@router.get("/{workspace_id}", response_model=WorkspaceRead)
def get_workspace_endpoint(
    workspace_id: UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> WorkspaceRead:
    try:
        workspace = get_workspace(
            db,
            tenant_id=ctx.tenant_id,
            workspace_id=str(workspace_id),
            user_id=ctx.user_id,
        )
    except Exception as exc:
        _raise_domain(exc)
    return WorkspaceRead.model_validate(workspace)


@router.get("/{workspace_id}/members", response_model=MembershipList)
def list_workspace_members(
    workspace_id: UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> MembershipList:
    try:
        items = list_memberships(
            db,
            tenant_id=ctx.tenant_id,
            workspace_id=str(workspace_id),
            actor_user_id=ctx.user_id,
        )
    except Exception as exc:
        _raise_domain(exc)
    return MembershipList(
        items=[MembershipRead.model_validate(item) for item in items],
        total=len(items),
    )


@router.post("/{workspace_id}/members", response_model=MembershipRead)
def add_or_update_workspace_member(
    workspace_id: UUID,
    payload: MembershipWrite,
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> MembershipRead:
    try:
        with db.begin():
            membership = upsert_membership(
                db,
                tenant_id=ctx.tenant_id,
                workspace_id=str(workspace_id),
                actor_user_id=ctx.user_id,
                target_user_id=str(payload.user_id),
                role=payload.role,
                expected_version=payload.expected_version,
                request_id=_request_id(request),
            )
    except Exception as exc:
        _raise_domain(exc)
    return MembershipRead.model_validate(membership)


@router.post("/{workspace_id}/members/{user_id}/suspend", response_model=MembershipRead)
def suspend_workspace_member(
    workspace_id: UUID,
    user_id: UUID,
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> MembershipRead:
    try:
        with db.begin():
            membership = set_membership_state(
                db,
                tenant_id=ctx.tenant_id,
                workspace_id=str(workspace_id),
                actor_user_id=ctx.user_id,
                target_user_id=str(user_id),
                state="suspended",
                request_id=_request_id(request),
            )
    except Exception as exc:
        _raise_domain(exc)
    return MembershipRead.model_validate(membership)


@router.post("/{workspace_id}/members/{user_id}/remove", response_model=MembershipRead)
def remove_workspace_member(
    workspace_id: UUID,
    user_id: UUID,
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> MembershipRead:
    try:
        with db.begin():
            membership = set_membership_state(
                db,
                tenant_id=ctx.tenant_id,
                workspace_id=str(workspace_id),
                actor_user_id=ctx.user_id,
                target_user_id=str(user_id),
                state="revoked",
                request_id=_request_id(request),
            )
    except Exception as exc:
        _raise_domain(exc)
    return MembershipRead.model_validate(membership)


@router.post("/{workspace_id}/scope-grants", response_model=ScopeGrantRead)
def create_workspace_scope_grant(
    workspace_id: UUID,
    payload: ScopeGrantCreate,
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> ScopeGrantRead:
    try:
        with db.begin():
            grant = create_scope_grant(
                db,
                tenant_id=ctx.tenant_id,
                target_workspace_id=str(workspace_id),
                actor_user_id=ctx.user_id,
                actor_is_tenant_admin=bool(ctx.user and ctx.user.is_tenant_admin),
                source_scope=payload.source_scope,
                source_owner_id=(
                    str(payload.source_owner_id) if payload.source_owner_id is not None else None
                ),
                resource_id=str(payload.resource_id),
                actions=payload.actions,
                expires_at=payload.expires_at,
                request_id=_request_id(request),
            )
    except Exception as exc:
        _raise_domain(exc)
    return ScopeGrantRead.model_validate(grant)


def _lifecycle_endpoint(
    *,
    workspace_id: UUID,
    payload: LifecycleRequest,
    desired_state: str,
    request: Request,
    ctx: TenantContext,
    db: Session,
) -> WorkspaceRead:
    try:
        with db.begin():
            workspace = request_workspace_state(
                db,
                tenant_id=ctx.tenant_id,
                workspace_id=str(workspace_id),
                actor_user_id=ctx.user_id,
                desired_state=desired_state,
                expected_version=payload.expected_version,
                request_id=_request_id(request),
            )
    except Exception as exc:
        _raise_domain(exc)
    return WorkspaceRead.model_validate(workspace)


@router.post("/{workspace_id}/start", response_model=WorkspaceRead)
def start_workspace(
    workspace_id: UUID,
    payload: LifecycleRequest,
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> WorkspaceRead:
    return _lifecycle_endpoint(
        workspace_id=workspace_id,
        payload=payload,
        desired_state="running",
        request=request,
        ctx=ctx,
        db=db,
    )


@router.post("/{workspace_id}/pause", response_model=WorkspaceRead)
def pause_workspace(
    workspace_id: UUID,
    payload: LifecycleRequest,
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> WorkspaceRead:
    return _lifecycle_endpoint(
        workspace_id=workspace_id,
        payload=payload,
        desired_state="paused",
        request=request,
        ctx=ctx,
        db=db,
    )


@router.post("/{workspace_id}/stop", response_model=WorkspaceRead)
def stop_workspace(
    workspace_id: UUID,
    payload: LifecycleRequest,
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> WorkspaceRead:
    return _lifecycle_endpoint(
        workspace_id=workspace_id,
        payload=payload,
        desired_state="stopped",
        request=request,
        ctx=ctx,
        db=db,
    )


@router.post("/{workspace_id}/archive", response_model=WorkspaceRead)
def archive_workspace(
    workspace_id: UUID,
    payload: LifecycleRequest,
    request: Request,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> WorkspaceRead:
    return _lifecycle_endpoint(
        workspace_id=workspace_id,
        payload=payload,
        desired_state="archived",
        request=request,
        ctx=ctx,
        db=db,
    )


@router.post("/{workspace_id}/runs", response_model=RunRead, status_code=status.HTTP_201_CREATED)
def create_workspace_run(
    workspace_id: UUID,
    payload: RunCreate,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> RunRead:
    try:
        with db.begin():
            run = create_run(
                db,
                tenant_id=ctx.tenant_id,
                workspace_id=str(workspace_id),
                actor_user_id=ctx.user_id,
                kind=payload.kind,
                expected_workspace_generation=payload.expected_workspace_generation,
                request_digest=payload.request_digest,
                idempotency_key=idempotency_key,
            )
    except Exception as exc:
        _raise_domain(exc)
    return RunRead.model_validate(run)


@router.get("/{workspace_id}/runs", response_model=RunList)
def list_workspace_runs(
    workspace_id: UUID,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> RunList:
    try:
        items = list_runs(
            db,
            tenant_id=ctx.tenant_id,
            workspace_id=str(workspace_id),
            actor_user_id=ctx.user_id,
        )
    except Exception as exc:
        _raise_domain(exc)
    return RunList(items=[RunRead.model_validate(item) for item in items], total=len(items))


@router.post("/{workspace_id}/snapshots", response_model=SnapshotRead)
def create_workspace_snapshot(
    workspace_id: UUID,
    payload: SnapshotCreate,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> SnapshotRead:
    try:
        with db.begin():
            snapshot = create_snapshot(
                db,
                tenant_id=ctx.tenant_id,
                workspace_id=str(workspace_id),
                actor_user_id=ctx.user_id,
                expected_workspace_generation=payload.expected_workspace_generation,
                manifest_digest=payload.manifest_digest,
                metadata=payload.metadata,
            )
    except Exception as exc:
        _raise_domain(exc)
    return SnapshotRead.model_validate(snapshot)


@router.post("/{workspace_id}/restore", response_model=WorkspaceRead)
def restore_workspace_snapshot(
    workspace_id: UUID,
    payload: RestoreRequest,
    ctx: TenantContext = Depends(get_current_tenant),
    db: Session = Depends(get_tenant_db),
) -> WorkspaceRead:
    try:
        with db.begin():
            workspace = restore_snapshot_new_workspace(
                db,
                tenant_id=ctx.tenant_id,
                source_workspace_id=str(workspace_id),
                snapshot_id=str(payload.snapshot_id),
                actor_user_id=ctx.user_id,
                display_name=payload.display_name,
            )
    except Exception as exc:
        _raise_domain(exc)
    return WorkspaceRead.model_validate(workspace)


__all__ = ["router", "template_router"]
