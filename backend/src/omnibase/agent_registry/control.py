"""P5.1C Browser Agent Registry control plane (application service).

The Browser API is served by an explicit control-plane object injected through
``get_registry_control_plane``.  The default production composition injects
``UnavailableAgentRegistryControlPlane``, which rejects every operation with a
stable ``agent_registry_unavailable`` (HTTP 503) before any registry table is
touched.  Tests and the disposable Gate override the dependency with the
DB-backed ``AgentRegistryControlService``.

Every mutation re-validates, inside the caller-owned transaction: the live
tenant, the live actor user, the Workspace row and generation, and the live
WorkspaceMembership (P34.4 ``authorize_workspace_action`` with row locks)
before delegating the registry mutation to ``RegistryPersistenceService``
which locks Definition -> Version -> live Binding -> IdempotencyRecord ->
ApprovalRequest -> target row -> resource registration -> append-only Audit.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Never

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnibase.agent_registry.models import (
    AgentDefinitionModel,
    AgentVersionModel,
    WorkspaceAgentBindingModel,
)
from omnibase.agent_registry.schemas import (
    AgentDefinitionList,
    AgentDefinitionRead,
    AgentInstallationList,
    AgentInstallationRead,
    AgentInstallCreate,
    AgentRollbackRequest,
    AgentUpgradeRequest,
    AgentVersionList,
    AgentVersionRead,
    project_binding,
    project_definition,
    project_version,
)
from omnibase.agent_registry.service import (
    RegistryPersistenceService,
    _lock_actor_user,
    _lock_tenant,
)
from omnibase.production.phase5_registry_contract import (
    BindingState,
    DefaultBudgetPolicy,
    WorkspaceAgentBinding,
)
from omnibase.workspaces.models import Workspace as WorkspaceModel
from omnibase.workspaces.service import authorize_workspace_action

_MUTATION_ACTION = "workspace.grants.manage"
_READ_ACTION = "workspace.read"


class AgentRegistryControlError(ValueError):
    """Stable Browser control-plane error with a public reason code."""

    def __init__(self, code: str, message: str, status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


class RegistryControlPlaneUnavailable(AgentRegistryControlError):
    """Default fail-closed rejection before any registry access."""

    def __init__(self) -> None:
        super().__init__(
            "agent_registry_unavailable",
            "Agent Registry control plane is not wired",
            503,
        )


class _RejectingRegistryAuthorizer:
    """Reject every mutation without touching the database (production default)."""

    def _reject(self) -> Never:
        raise RegistryControlPlaneUnavailable()

    def list_definitions(self, **_: Any) -> Never:
        self._reject()

    def get_definition(self, **_: Any) -> Never:
        self._reject()

    def list_versions(self, **_: Any) -> Never:
        self._reject()

    def get_version(self, **_: Any) -> Never:
        self._reject()

    def list_installations(self, **_: Any) -> Never:
        self._reject()

    def get_installation(self, **_: Any) -> Never:
        self._reject()

    def install(self, **_: Any) -> Never:
        self._reject()

    def upgrade(self, **_: Any) -> Never:
        self._reject()

    def disable(self, **_: Any) -> Never:
        self._reject()

    def rollback(self, **_: Any) -> Never:
        self._reject()


class AgentRegistryControlService:
    """DB-backed Browser control plane; every call runs in one caller-owned transaction."""

    def __init__(self, session: Session) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Catalog reads (tenant-scoped, deterministic)
    # ------------------------------------------------------------------

    def list_definitions(
        self,
        *,
        tenant_id: str,
        limit: int,
        offset: int,
    ) -> AgentDefinitionList:
        base = select(AgentDefinitionModel).where(AgentDefinitionModel.tenant_id == tenant_id)
        total = int(
            self._session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        )
        rows = self._session.execute(
            base.order_by(
                AgentDefinitionModel.stable_logical_key,
                AgentDefinitionModel.id,
            )
            .limit(limit)
            .offset(offset)
        ).scalars()
        return AgentDefinitionList(
            items=[project_definition(row) for row in rows],
            total=total,
        )

    def get_definition(self, *, tenant_id: str, definition_id: str) -> AgentDefinitionRead:
        row = self._session.execute(
            select(AgentDefinitionModel).where(
                AgentDefinitionModel.tenant_id == tenant_id,
                AgentDefinitionModel.id == definition_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise AgentRegistryControlError(
                "agent_definition_not_found", "Agent definition not found", 404
            )
        return project_definition(row)

    def list_versions(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        limit: int,
        offset: int,
    ) -> AgentVersionList:
        definition = self._session.execute(
            select(AgentDefinitionModel.id).where(
                AgentDefinitionModel.tenant_id == tenant_id,
                AgentDefinitionModel.id == definition_id,
            )
        ).scalar_one_or_none()
        if definition is None:
            raise AgentRegistryControlError(
                "agent_definition_not_found", "Agent definition not found", 404
            )
        base = select(AgentVersionModel).where(
            AgentVersionModel.tenant_id == tenant_id,
            AgentVersionModel.definition_id == definition_id,
        )
        total = int(
            self._session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        )
        rows = self._session.execute(
            base.order_by(AgentVersionModel.version, AgentVersionModel.id)
            .limit(limit)
            .offset(offset)
        ).scalars()
        return AgentVersionList(
            items=[project_version(row) for row in rows],
            total=total,
        )

    def get_version(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        version_id: str,
    ) -> AgentVersionRead:
        row = self._session.execute(
            select(AgentVersionModel).where(
                AgentVersionModel.tenant_id == tenant_id,
                AgentVersionModel.definition_id == definition_id,
                AgentVersionModel.id == version_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise AgentRegistryControlError(
                "agent_version_not_found", "Agent version not found", 404
            )
        return project_version(row)

    # ------------------------------------------------------------------
    # Workspace installation reads (live membership required)
    # ------------------------------------------------------------------

    def _require_membership(self, *, tenant_id: str, workspace_id: str, user_id: str) -> None:
        authorize_workspace_action(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            action=_READ_ACTION,
            lock=False,
        )

    def list_installations(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        limit: int,
        offset: int,
    ) -> AgentInstallationList:
        self._require_membership(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
        base = select(WorkspaceAgentBindingModel).where(
            WorkspaceAgentBindingModel.tenant_id == tenant_id,
            WorkspaceAgentBindingModel.workspace_id == workspace_id,
        )
        total = int(
            self._session.execute(select(func.count()).select_from(base.subquery())).scalar_one()
        )
        rows = self._session.execute(
            base.order_by(
                WorkspaceAgentBindingModel.created_at,
                WorkspaceAgentBindingModel.id,
            )
            .limit(limit)
            .offset(offset)
        ).scalars()
        return AgentInstallationList(
            items=[project_binding(row) for row in rows],
            total=total,
        )

    def get_installation(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        user_id: str,
        binding_id: str,
    ) -> AgentInstallationRead:
        self._require_membership(tenant_id=tenant_id, workspace_id=workspace_id, user_id=user_id)
        row = self._session.execute(
            select(WorkspaceAgentBindingModel).where(
                WorkspaceAgentBindingModel.tenant_id == tenant_id,
                WorkspaceAgentBindingModel.workspace_id == workspace_id,
                WorkspaceAgentBindingModel.id == binding_id,
            )
        ).scalar_one_or_none()
        if row is None:
            raise AgentRegistryControlError(
                "agent_installation_not_found", "Agent installation not found", 404
            )
        return project_binding(row)

    # ------------------------------------------------------------------
    # Mutations (live membership re-locked inside the transaction)
    # ------------------------------------------------------------------

    def _lock_workspace_actor(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        workspace_id: str,
    ) -> WorkspaceModel:
        _lock_tenant(self._session, tenant_id=tenant_id)
        _lock_actor_user(self._session, actor_user_id=actor_user_id)
        workspace = self._session.execute(
            select(WorkspaceModel)
            .where(
                WorkspaceModel.tenant_id == tenant_id,
                WorkspaceModel.id == workspace_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if workspace is None:
            raise AgentRegistryControlError("workspace_not_found", "Workspace not found", 404)
        authorize_workspace_action(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=actor_user_id,
            action=_MUTATION_ACTION,
            lock=True,
        )
        return workspace

    def _load_sealed_target_version_snapshot(
        self,
        *,
        tenant_id: str,
        definition_id: str,
        version_id: str,
        digest: str,
    ) -> AgentVersionModel:
        version = self._session.execute(
            select(AgentVersionModel).where(
                AgentVersionModel.tenant_id == tenant_id,
                AgentVersionModel.id == version_id,
                AgentVersionModel.definition_id == definition_id,
            )
        ).scalar_one_or_none()
        if version is None:
            raise AgentRegistryControlError(
                "agent_version_not_found", "Agent version not found", 404
            )
        if version.version_state != "sealed":
            raise AgentRegistryControlError(
                "agent_version_not_sealed",
                "Agent version is not sealed",
                409,
            )
        if version.manifest_digest != digest:
            raise AgentRegistryControlError(
                "agent_version_digest_mismatch",
                "Agent version digest mismatch",
                409,
            )
        return version

    def _load_binding_snapshot(
        self,
        *,
        tenant_id: str,
        workspace_id: str,
        binding_id: str,
        expected_binding_id: str | None,
    ) -> WorkspaceAgentBindingModel:
        binding = self._session.execute(
            select(WorkspaceAgentBindingModel).where(
                WorkspaceAgentBindingModel.tenant_id == tenant_id,
                WorkspaceAgentBindingModel.workspace_id == workspace_id,
                WorkspaceAgentBindingModel.id == binding_id,
            )
        ).scalar_one_or_none()
        if binding is None:
            raise AgentRegistryControlError(
                "agent_installation_not_found", "Agent installation not found", 404
            )
        if expected_binding_id is not None and binding.id != expected_binding_id:
            raise AgentRegistryControlError(
                "registry_stale_binding",
                "Current binding changed since the request was prepared",
                409,
            )
        return binding

    def _new_binding_dto(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        workspace: WorkspaceModel,
        definition_id: str,
        version_id: str,
        version_digest: str,
        resource_scopes: tuple[str, ...],
        budget: DefaultBudgetPolicy,
        approval_id: str | None,
    ) -> WorkspaceAgentBinding:
        return WorkspaceAgentBinding(
            schema_version=1,
            workspace_agent_binding_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            workspace_id=str(workspace.id),
            workspace_generation=workspace.generation,
            agent_definition_id=definition_id,
            agent_version_id=version_id,
            agent_version_digest=version_digest,
            installation_state=BindingState("installed"),
            resource_scopes=resource_scopes,
            default_budget_policy=budget,
            installed_by=actor_user_id,
            approval_id=approval_id,
            created_at=datetime.now(UTC).isoformat(),
            disabled_at=None,
            superseded_by=None,
        )

    def install(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        workspace_id: str,
        request_id: str,
        payload: AgentInstallCreate,
        idempotency_key: str,
    ) -> AgentInstallationRead:
        workspace = self._lock_workspace_actor(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
        )
        if workspace.generation != payload.workspace_generation:
            raise AgentRegistryControlError(
                "registry_workspace_generation_stale",
                "Workspace generation is stale",
                409,
            )
        self._load_sealed_target_version_snapshot(
            tenant_id=tenant_id,
            definition_id=payload.agent_definition_id,
            version_id=payload.agent_version_id,
            digest=payload.agent_version_digest,
        )
        binding = self._new_binding_dto(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            workspace=workspace,
            definition_id=payload.agent_definition_id,
            version_id=payload.agent_version_id,
            version_digest=payload.agent_version_digest,
            resource_scopes=tuple(payload.resource_scopes),
            budget=DefaultBudgetPolicy.from_mapping(
                payload.default_budget_policy.as_mapping(),
                name="default_budget_policy",
                ceilings=_BUDGET_CEILINGS,
            ),
            approval_id=payload.approval_id,
        )
        try:
            model = RegistryPersistenceService(self._session).install_binding(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                request_id=request_id,
                binding=binding,
                idempotency_key=idempotency_key,
                request_hash_profile="browser_install",
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return project_binding(model)

    def upgrade(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        workspace_id: str,
        binding_id: str,
        request_id: str,
        payload: AgentUpgradeRequest,
        idempotency_key: str,
    ) -> AgentInstallationRead:
        workspace = self._lock_workspace_actor(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
        )
        current = self._load_binding_snapshot(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            binding_id=binding_id,
            expected_binding_id=payload.expected_binding_id,
        )
        self._load_sealed_target_version_snapshot(
            tenant_id=tenant_id,
            definition_id=current.agent_definition_id,
            version_id=payload.target_agent_version_id,
            digest=payload.target_agent_version_digest,
        )
        new_binding = self._new_binding_dto(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            workspace=workspace,
            definition_id=current.agent_definition_id,
            version_id=payload.target_agent_version_id,
            version_digest=payload.target_agent_version_digest,
            resource_scopes=tuple(current.resource_scopes),
            budget=DefaultBudgetPolicy.from_mapping(
                dict(current.default_budget_policy),
                name="default_budget_policy",
                ceilings=_BUDGET_CEILINGS,
            ),
            approval_id=payload.approval_id,
        )
        try:
            model = RegistryPersistenceService(self._session).supersede_binding(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                request_id=request_id,
                old_binding_id=current.id,
                new_binding=new_binding,
                idempotency_key=idempotency_key,
                request_hash_profile="browser_upgrade",
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return project_binding(model)

    def disable(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        workspace_id: str,
        binding_id: str,
        request_id: str,
        idempotency_key: str,
    ) -> AgentInstallationRead:
        self._lock_workspace_actor(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
        )
        try:
            model = RegistryPersistenceService(self._session).disable_binding(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                request_id=request_id,
                binding_id=binding_id,
                idempotency_key=idempotency_key,
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return project_binding(model)

    def rollback(
        self,
        *,
        tenant_id: str,
        actor_user_id: str,
        workspace_id: str,
        binding_id: str,
        request_id: str,
        payload: AgentRollbackRequest,
        idempotency_key: str,
    ) -> AgentInstallationRead:
        workspace = self._lock_workspace_actor(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            workspace_id=workspace_id,
        )
        current = self._load_binding_snapshot(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            binding_id=binding_id,
            expected_binding_id=payload.expected_binding_id,
        )
        self._load_sealed_target_version_snapshot(
            tenant_id=tenant_id,
            definition_id=current.agent_definition_id,
            version_id=payload.rollback_agent_version_id,
            digest=payload.rollback_agent_version_digest,
        )
        new_binding = self._new_binding_dto(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            workspace=workspace,
            definition_id=current.agent_definition_id,
            version_id=payload.rollback_agent_version_id,
            version_digest=payload.rollback_agent_version_digest,
            resource_scopes=tuple(current.resource_scopes),
            budget=DefaultBudgetPolicy.from_mapping(
                dict(current.default_budget_policy),
                name="default_budget_policy",
                ceilings=_BUDGET_CEILINGS,
            ),
            approval_id=payload.approval_id,
        )
        try:
            model = RegistryPersistenceService(self._session).supersede_binding(
                tenant_id=tenant_id,
                actor_user_id=actor_user_id,
                request_id=request_id,
                old_binding_id=current.id,
                new_binding=new_binding,
                idempotency_key=idempotency_key,
                request_hash_profile="browser_rollback",
            )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return project_binding(model)


_BUDGET_CEILINGS = {
    "max_tokens": 10_000_000,
    "max_cost_units": 100_000,
    "max_wall_clock_seconds": 3_600,
    "max_tool_calls": 1_000,
}


__all__ = [
    "AgentRegistryControlError",
    "AgentRegistryControlService",
    "RegistryControlPlaneUnavailable",
    "UnavailableAgentRegistryControlPlane",
]


UnavailableAgentRegistryControlPlane = _RejectingRegistryAuthorizer
