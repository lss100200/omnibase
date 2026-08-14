"""Internal single-Owner persistence service for personal instruction Skills.

The service never commits.  Every method runs inside the caller-owned
transaction, revalidates the live tenant and Owner, and locks the exact
Workspace/AgentVersion/Skill rows before mutation.  There is intentionally no
Browser router, SDK surface, network/tool capability or secret seam here.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from omnibase.agent_registry.models import (
    AgentVersionModel,
    WorkspaceAgentBindingModel,
)
from omnibase.agent_skills.limits import SkillBundleLimitError, validate_skill_bundle_limits
from omnibase.agent_skills.models import (
    SkillDefinitionModel,
    SkillVersionModel,
    WorkspaceAgentSkillInstallationModel,
)
from omnibase.db.models import Tenant
from omnibase.db.tenant import User
from omnibase.production.phase5_skill_contract import (
    SkillContractError,
    SkillDefinition,
    SkillDefinitionState,
    SkillKind,
    SkillVersion,
    SkillVersionState,
)
from omnibase.tenants.schema_manager import set_search_path
from omnibase.workspaces.models import Workspace, WorkspaceMembership

_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)" r"(?:-[0-9A-Za-z.-]+)?$"
)


class SkillPersistenceError(ValueError):
    """Stable internal Skill persistence error."""


class SkillNotFoundError(SkillPersistenceError):
    """An exact Skill, Workspace or AgentVersion row was not found."""


class SkillConflictError(SkillPersistenceError):
    """A unique installation or immutable version already exists."""


class SkillStateError(SkillPersistenceError):
    """A personal/non-escalating/state invariant failed closed."""


def _semver_release(value: str) -> tuple[int, int, int]:
    if _SEMVER_RE.fullmatch(value) is None:
        raise SkillStateError("skill_semantic_version_invalid")
    core = value.split("-", 1)[0]
    major, minor, patch = core.split(".")
    return int(major), int(minor), int(patch)


def _lock_owner(
    session: Session,
    *,
    tenant_id: str,
    tenant_schema: str,
    owner_user_id: str,
) -> None:
    tenant = session.scalar(
        select(Tenant)
        .where(
            Tenant.id == tenant_id,
            Tenant.schema_name == tenant_schema,
            Tenant.is_active.is_(True),
        )
        .with_for_update()
    )
    if tenant is None:
        raise SkillStateError("skill_tenant_binding_invalid")
    set_search_path(session, tenant_schema)
    owner = session.scalar(
        select(User)
        .where(
            User.id == owner_user_id,
            User.is_active.is_(True),
            User.is_tenant_admin.is_(True),
        )
        .with_for_update()
    )
    if owner is None:
        raise SkillStateError("skill_owner_inactive_or_missing")


def _lock_workspace_agent(
    session: Session,
    *,
    tenant_id: str,
    owner_user_id: str,
    workspace_id: str,
    agent_version_id: str,
) -> tuple[Workspace, AgentVersionModel]:
    workspace = session.scalar(
        select(Workspace)
        .where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
            Workspace.owner_user_id == owner_user_id,
        )
        .with_for_update()
    )
    membership = session.scalar(
        select(WorkspaceMembership)
        .where(
            WorkspaceMembership.tenant_id == tenant_id,
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == owner_user_id,
            WorkspaceMembership.role == "owner",
            WorkspaceMembership.state == "active",
        )
        .with_for_update()
    )
    agent_version = session.scalar(
        select(AgentVersionModel)
        .where(
            AgentVersionModel.id == agent_version_id,
            AgentVersionModel.tenant_id == tenant_id,
            AgentVersionModel.version_state == "sealed",
        )
        .with_for_update()
    )
    agent_binding = session.scalar(
        select(WorkspaceAgentBindingModel)
        .where(
            WorkspaceAgentBindingModel.tenant_id == tenant_id,
            WorkspaceAgentBindingModel.workspace_id == workspace_id,
            WorkspaceAgentBindingModel.agent_version_id == agent_version_id,
            WorkspaceAgentBindingModel.binding_state == "installed",
        )
        .with_for_update()
    )
    if workspace is None or membership is None:
        raise SkillStateError("skill_workspace_owner_binding_invalid")
    if agent_version is None:
        raise SkillNotFoundError("skill_agent_version_not_found")
    if agent_binding is None or agent_binding.agent_version_digest != agent_version.manifest_digest:
        raise SkillStateError("skill_workspace_agent_binding_invalid")
    return workspace, agent_version


def _validate_definition(definition: SkillDefinition) -> None:
    if (
        definition.definition_state is not SkillDefinitionState.ACTIVE
        or definition.first_party is not True
        or definition.allowed_installation_scopes != ("workspace",)
    ):
        raise SkillStateError("skill_definition_personal_posture_invalid")


def _validate_version(version: SkillVersion) -> None:
    if (
        version.version_state is not SkillVersionState.TESTED
        or version.kind is not SkillKind.INSTRUCTION
        or version.required_tool_ids
        or version.capability_requirements
        or version.network_policy != "deny"
        or version.secrets_allowed is not False
        or version.budget.max_tool_calls != 0
    ):
        raise SkillStateError("skill_version_non_escalating_posture_invalid")


def _validate_prospective_bundle_limits(
    session: Session,
    *,
    tenant_id: str,
    workspace_id: str,
    agent_version_id: str,
    new_instructions: str,
) -> None:
    live_instructions = session.scalars(
        select(SkillVersionModel.instructions)
        .join(
            WorkspaceAgentSkillInstallationModel,
            (WorkspaceAgentSkillInstallationModel.skill_version_id == SkillVersionModel.id)
            & (WorkspaceAgentSkillInstallationModel.tenant_id == SkillVersionModel.tenant_id),
        )
        .where(
            WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
            WorkspaceAgentSkillInstallationModel.workspace_id == workspace_id,
            WorkspaceAgentSkillInstallationModel.agent_version_id == agent_version_id,
            WorkspaceAgentSkillInstallationModel.installation_state == "installed",
        )
        .order_by(WorkspaceAgentSkillInstallationModel.id)
    ).all()
    try:
        validate_skill_bundle_limits((*live_instructions, new_instructions))
    except SkillBundleLimitError as exc:
        if str(exc) == "skill_bundle_live_limit_exceeded":
            raise SkillStateError("skill_installation_live_limit_exceeded") from exc
        raise SkillStateError("skill_installation_instruction_budget_exceeded") from exc


class SkillPersistenceService:
    """Caller-owned transaction service; only the live personal Owner may mutate."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def validate_owner_workspace_agent(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        workspace_id: str,
        agent_version_id: str,
    ) -> None:
        """Revalidate and lock the live personal Owner/Workspace/Agent binding.

        Browser orchestration calls this before reserving an idempotency key so
        an invalid or stale logical target cannot create control-plane state.
        Mutation methods repeat the same validation inside their own boundary
        as defence in depth.
        """

        _lock_owner(
            self._session,
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
        )
        _lock_workspace_agent(
            self._session,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
        )

    def register_definition(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        definition: SkillDefinition,
    ) -> SkillDefinitionModel:
        _validate_definition(definition)
        _lock_owner(
            self._session,
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
        )
        existing = self._session.scalar(
            select(SkillDefinitionModel)
            .where(
                SkillDefinitionModel.tenant_id == tenant_id,
                SkillDefinitionModel.stable_logical_key == definition.stable_logical_key,
            )
            .with_for_update()
        )
        if existing is not None:
            raise SkillConflictError("skill_definition_natural_key_conflict")
        model = SkillDefinitionModel(
            id=definition.skill_definition_id,
            tenant_id=tenant_id,
            stable_logical_key=definition.stable_logical_key,
            display_name=definition.display_name,
            description=definition.description,
            definition_state="active",
            installation_scopes=["workspace"],
            first_party=True,
            created_by=owner_user_id,
        )
        try:
            self._session.add(model)
            self._session.flush()
        except IntegrityError as exc:
            raise SkillConflictError("skill_definition_conflict") from exc
        return model

    def seal_version(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        version: SkillVersion,
    ) -> SkillVersionModel:
        _validate_version(version)
        _lock_owner(
            self._session,
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
        )
        definition = self._session.scalar(
            select(SkillDefinitionModel)
            .where(
                SkillDefinitionModel.id == version.skill_definition_id,
                SkillDefinitionModel.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if definition is None:
            raise SkillNotFoundError("skill_definition_not_found")
        if definition.definition_state != "active" or definition.first_party is not True:
            raise SkillStateError("skill_definition_not_active")
        existing = self._session.scalar(
            select(SkillVersionModel)
            .where(
                SkillVersionModel.tenant_id == tenant_id,
                SkillVersionModel.definition_id == version.skill_definition_id,
                SkillVersionModel.semantic_version == version.version,
            )
            .with_for_update()
        )
        if existing is not None:
            raise SkillConflictError("skill_version_immutable_conflict")
        if version.rollback_version_id is not None:
            rollback = self._session.scalar(
                select(SkillVersionModel)
                .where(
                    SkillVersionModel.id == version.rollback_version_id,
                    SkillVersionModel.tenant_id == tenant_id,
                    SkillVersionModel.definition_id == version.skill_definition_id,
                    SkillVersionModel.version_state == "sealed",
                )
                .with_for_update()
            )
            if rollback is None or _semver_release(rollback.semantic_version) >= _semver_release(
                version.version
            ):
                raise SkillStateError("skill_rollback_version_invalid")
        model = SkillVersionModel(
            id=version.skill_version_id,
            tenant_id=tenant_id,
            definition_id=version.skill_definition_id,
            semantic_version=version.version,
            version_state="sealed",
            kind="instruction",
            manifest_payload=version.to_dict(),
            manifest_digest=version.canonical_digest(),
            instructions=version.instructions,
            instructions_digest=version.instructions_digest,
            required_tool_ids=[],
            capability_requirements=[],
            network_policy="deny",
            secrets_allowed=False,
            max_tool_calls=0,
            rollback_version_id=version.rollback_version_id,
            created_by=owner_user_id,
        )
        try:
            self._session.add(model)
            self._session.flush()
        except IntegrityError as exc:
            raise SkillConflictError("skill_version_conflict") from exc
        return model

    def install(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        workspace_id: str,
        agent_version_id: str,
        skill_version_id: str,
        installation_id: str | None = None,
        previous_installation_id: str | None = None,
    ) -> WorkspaceAgentSkillInstallationModel:
        _lock_owner(
            self._session,
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
        )
        _, agent_version = _lock_workspace_agent(
            self._session,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
        )
        version = self._session.scalar(
            select(SkillVersionModel)
            .where(
                SkillVersionModel.id == skill_version_id,
                SkillVersionModel.tenant_id == tenant_id,
                SkillVersionModel.version_state == "sealed",
            )
            .with_for_update()
        )
        if version is None:
            raise SkillNotFoundError("skill_version_not_found")
        definition = self._session.scalar(
            select(SkillDefinitionModel)
            .where(
                SkillDefinitionModel.id == version.definition_id,
                SkillDefinitionModel.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        if definition is None or definition.definition_state != "active":
            raise SkillStateError("skill_definition_not_active")
        if definition.first_party is not True or definition.installation_scopes != ["workspace"]:
            raise SkillStateError("skill_definition_personal_posture_invalid")
        try:
            manifest = SkillVersion.from_mapping(version.manifest_payload)
        except SkillContractError as exc:
            raise SkillStateError("skill_manifest_payload_invalid") from exc
        if manifest.canonical_digest() != version.manifest_digest:
            raise SkillStateError("skill_manifest_digest_drifted")
        _validate_version(manifest)
        if manifest.supported_agent_version_digests and (
            agent_version.manifest_digest not in manifest.supported_agent_version_digests
        ):
            raise SkillStateError("skill_agent_version_digest_not_supported")
        live = self._session.scalar(
            select(WorkspaceAgentSkillInstallationModel)
            .where(
                WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
                WorkspaceAgentSkillInstallationModel.workspace_id == workspace_id,
                WorkspaceAgentSkillInstallationModel.agent_version_id == agent_version_id,
                WorkspaceAgentSkillInstallationModel.skill_definition_id == definition.id,
                WorkspaceAgentSkillInstallationModel.installation_state == "installed",
            )
            .with_for_update()
        )
        if live is not None:
            raise SkillConflictError("skill_installation_already_live")
        _validate_prospective_bundle_limits(
            self._session,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            new_instructions=version.instructions,
        )
        if previous_installation_id is not None:
            previous = self._session.scalar(
                select(WorkspaceAgentSkillInstallationModel)
                .where(
                    WorkspaceAgentSkillInstallationModel.id == previous_installation_id,
                    WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
                    WorkspaceAgentSkillInstallationModel.owner_user_id == owner_user_id,
                    WorkspaceAgentSkillInstallationModel.workspace_id == workspace_id,
                    WorkspaceAgentSkillInstallationModel.agent_version_id == agent_version_id,
                    WorkspaceAgentSkillInstallationModel.skill_definition_id == definition.id,
                    WorkspaceAgentSkillInstallationModel.installation_state.in_(
                        ("disabled", "superseded")
                    ),
                )
                .with_for_update()
            )
            if previous is None:
                raise SkillStateError("skill_previous_installation_binding_invalid")
        model = WorkspaceAgentSkillInstallationModel(
            id=installation_id or str(uuid4()),
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            skill_definition_id=definition.id,
            skill_version_id=version.id,
            skill_manifest_digest=version.manifest_digest,
            installation_state="installed",
            previous_installation_id=previous_installation_id,
            installed_by=owner_user_id,
        )
        try:
            self._session.add(model)
            self._session.flush()
        except IntegrityError as exc:
            raise SkillConflictError("skill_installation_conflict") from exc
        return model

    def disable(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        installation_id: str,
    ) -> WorkspaceAgentSkillInstallationModel:
        return self._transition(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
            installation_id=installation_id,
            target_state="disabled",
        )

    def revoke(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        installation_id: str,
    ) -> WorkspaceAgentSkillInstallationModel:
        return self._transition(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
            installation_id=installation_id,
            target_state="revoked",
        )

    def _transition(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        installation_id: str,
        target_state: str,
    ) -> WorkspaceAgentSkillInstallationModel:
        _lock_owner(
            self._session,
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
        )
        installation_scope = self._session.scalar(
            select(WorkspaceAgentSkillInstallationModel).where(
                WorkspaceAgentSkillInstallationModel.id == installation_id,
                WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
                WorkspaceAgentSkillInstallationModel.owner_user_id == owner_user_id,
            )
        )
        if installation_scope is None:
            raise SkillNotFoundError("skill_installation_not_found")
        _lock_workspace_agent(
            self._session,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            workspace_id=installation_scope.workspace_id,
            agent_version_id=installation_scope.agent_version_id,
        )
        installation = self._session.scalar(
            select(WorkspaceAgentSkillInstallationModel)
            .where(
                WorkspaceAgentSkillInstallationModel.id == installation_id,
                WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
                WorkspaceAgentSkillInstallationModel.owner_user_id == owner_user_id,
                WorkspaceAgentSkillInstallationModel.workspace_id
                == installation_scope.workspace_id,
                WorkspaceAgentSkillInstallationModel.agent_version_id
                == installation_scope.agent_version_id,
            )
            .with_for_update()
        )
        if installation is None:
            raise SkillNotFoundError("skill_installation_not_found")
        if target_state == "disabled" and installation.installation_state != "installed":
            raise SkillStateError("skill_installation_disable_invalid")
        if target_state == "revoked" and installation.installation_state not in {
            "installed",
            "disabled",
        }:
            raise SkillStateError("skill_installation_revoke_invalid")
        now = datetime.now(UTC)
        installation.installation_state = target_state
        if target_state == "disabled":
            installation.disabled_at = now
        else:
            installation.revoked_at = now
        self._session.flush()
        return installation

    def rollback(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        current_installation_id: str,
        target_skill_version_id: str,
        new_installation_id: str | None = None,
    ) -> WorkspaceAgentSkillInstallationModel:
        _lock_owner(
            self._session,
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
        )
        current_scope = self._session.scalar(
            select(WorkspaceAgentSkillInstallationModel).where(
                WorkspaceAgentSkillInstallationModel.id == current_installation_id,
                WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
                WorkspaceAgentSkillInstallationModel.owner_user_id == owner_user_id,
            )
        )
        if current_scope is None:
            raise SkillNotFoundError("skill_installation_not_found")
        _lock_workspace_agent(
            self._session,
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            workspace_id=current_scope.workspace_id,
            agent_version_id=current_scope.agent_version_id,
        )
        current = self._session.scalar(
            select(WorkspaceAgentSkillInstallationModel)
            .where(
                WorkspaceAgentSkillInstallationModel.id == current_installation_id,
                WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
                WorkspaceAgentSkillInstallationModel.owner_user_id == owner_user_id,
                WorkspaceAgentSkillInstallationModel.workspace_id == current_scope.workspace_id,
                WorkspaceAgentSkillInstallationModel.agent_version_id
                == current_scope.agent_version_id,
            )
            .with_for_update()
        )
        if current is None:
            raise SkillNotFoundError("skill_installation_not_found")
        if current.installation_state not in {"installed", "disabled"}:
            raise SkillStateError("skill_installation_rollback_invalid")
        current_version = self._session.scalar(
            select(SkillVersionModel)
            .where(
                SkillVersionModel.id == current.skill_version_id,
                SkillVersionModel.tenant_id == tenant_id,
            )
            .with_for_update()
        )
        target_version = self._session.scalar(
            select(SkillVersionModel)
            .where(
                SkillVersionModel.id == target_skill_version_id,
                SkillVersionModel.tenant_id == tenant_id,
                SkillVersionModel.definition_id == current.skill_definition_id,
                SkillVersionModel.version_state == "sealed",
            )
            .with_for_update()
        )
        if current_version is None or target_version is None:
            raise SkillNotFoundError("skill_rollback_version_not_found")
        if current_version.rollback_version_id != target_version.id or _semver_release(
            target_version.semantic_version
        ) >= _semver_release(current_version.semantic_version):
            raise SkillStateError("skill_rollback_target_invalid")
        if current.installation_state == "installed":
            current.installation_state = "superseded"
            current.disabled_at = datetime.now(UTC)
            self._session.flush()
        return self.install(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
            workspace_id=current.workspace_id,
            agent_version_id=current.agent_version_id,
            skill_version_id=target_version.id,
            installation_id=new_installation_id,
            previous_installation_id=current.id,
        )


__all__ = [
    "SkillConflictError",
    "SkillNotFoundError",
    "SkillPersistenceError",
    "SkillPersistenceService",
    "SkillStateError",
]
