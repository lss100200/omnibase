"""Fail-closed resolution of installed personal instruction Skills."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from omnibase.agent_registry.models import (
    AgentVersionModel,
    WorkspaceAgentBindingModel,
)
from omnibase.agent_skills.models import (
    SkillDefinitionModel,
    SkillVersionModel,
    WorkspaceAgentSkillInstallationModel,
)
from omnibase.db.models import Tenant
from omnibase.db.tenant import User
from omnibase.production.phase5_skill_contract import SkillContractError, SkillVersion
from omnibase.production.phase5_skill_contract import SkillKind as ContractSkillKind
from omnibase.tenants.schema_manager import set_search_path
from omnibase.workspaces.models import Workspace, WorkspaceMembership


class SkillResolutionError(RuntimeError):
    """Installed Skill state was missing, cross-wired or digest-drifted."""


@dataclass(frozen=True, slots=True)
class SkillInstruction:
    skill_definition_id: str
    skill_version_id: str
    stable_logical_key: str
    version: str
    manifest_digest: str
    instructions_digest: str
    instructions: str


@dataclass(frozen=True, slots=True)
class SkillInstructionBundle:
    canonical_digest: str
    items: tuple[SkillInstruction, ...]


class SkillResolver(Protocol):
    def resolve(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        workspace_id: str,
        agent_version_id: str,
    ) -> SkillInstructionBundle: ...


def _canonical_digest(items: tuple[SkillInstruction, ...]) -> str:
    payload = [asdict(item) for item in items]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_personal_identity(
    session: Session,
    *,
    tenant_id: str,
    tenant_schema: str,
    owner_user_id: str,
    workspace_id: str,
    agent_version_id: str,
) -> str:
    tenant = session.scalar(
        select(Tenant).where(
            Tenant.id == tenant_id,
            Tenant.schema_name == tenant_schema,
            Tenant.is_active.is_(True),
        )
    )
    if tenant is None:
        raise SkillResolutionError("skill_tenant_binding_invalid")
    set_search_path(session, tenant_schema)
    owner = session.scalar(
        select(User).where(
            User.id == owner_user_id,
            User.is_active.is_(True),
            User.is_tenant_admin.is_(True),
        )
    )
    workspace = session.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
            Workspace.owner_user_id == owner_user_id,
        )
    )
    membership = session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.tenant_id == tenant_id,
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == owner_user_id,
            WorkspaceMembership.role == "owner",
            WorkspaceMembership.state == "active",
        )
    )
    agent_version = session.scalar(
        select(AgentVersionModel).where(
            AgentVersionModel.id == agent_version_id,
            AgentVersionModel.tenant_id == tenant_id,
            AgentVersionModel.version_state == "sealed",
        )
    )
    agent_binding = session.scalar(
        select(WorkspaceAgentBindingModel).where(
            WorkspaceAgentBindingModel.tenant_id == tenant_id,
            WorkspaceAgentBindingModel.workspace_id == workspace_id,
            WorkspaceAgentBindingModel.agent_version_id == agent_version_id,
            WorkspaceAgentBindingModel.binding_state == "installed",
        )
    )
    if (
        owner is None
        or workspace is None
        or membership is None
        or agent_version is None
        or agent_binding is None
        or agent_binding.agent_version_digest != agent_version.manifest_digest
    ):
        raise SkillResolutionError("skill_runtime_identity_invalid")
    return agent_version.manifest_digest


def _instruction_from_row(
    installation: WorkspaceAgentSkillInstallationModel,
    definition: SkillDefinitionModel,
    version: SkillVersionModel,
    *,
    agent_version_digest: str,
) -> SkillInstruction:
    if (
        installation.tenant_id != definition.tenant_id
        or installation.tenant_id != version.tenant_id
        or installation.skill_definition_id != definition.id
        or installation.skill_definition_id != version.definition_id
        or installation.skill_version_id != version.id
        or installation.skill_manifest_digest != version.manifest_digest
    ):
        raise SkillResolutionError("skill_installation_binding_drifted")
    if (
        definition.definition_state != "active"
        or definition.first_party is not True
        or definition.installation_scopes != ["workspace"]
        or version.version_state != "sealed"
        or version.kind != "instruction"
        or version.required_tool_ids != []
        or version.capability_requirements != []
        or version.network_policy != "deny"
        or version.secrets_allowed is not False
        or version.max_tool_calls != 0
    ):
        raise SkillResolutionError("skill_non_escalating_posture_invalid")
    try:
        manifest = SkillVersion.from_mapping(version.manifest_payload)
    except SkillContractError as exc:
        raise SkillResolutionError("skill_manifest_payload_invalid") from exc
    if (
        manifest.canonical_digest() != version.manifest_digest
        or manifest.skill_definition_id != definition.id
        or manifest.skill_version_id != version.id
        or manifest.version != version.semantic_version
        or manifest.instructions != version.instructions
        or manifest.instructions_digest != version.instructions_digest
        or manifest.kind is not ContractSkillKind.INSTRUCTION
        or manifest.required_tool_ids
        or manifest.capability_requirements
        or manifest.network_policy != "deny"
        or manifest.secrets_allowed is not False
        or manifest.budget.max_tool_calls != 0
        or (
            manifest.supported_agent_version_digests
            and agent_version_digest not in manifest.supported_agent_version_digests
        )
    ):
        raise SkillResolutionError("skill_manifest_digest_drifted")
    if (
        hashlib.sha256(version.instructions.encode("utf-8")).hexdigest()
        != version.instructions_digest
    ):
        raise SkillResolutionError("skill_instructions_digest_drifted")
    return SkillInstruction(
        skill_definition_id=definition.id,
        skill_version_id=version.id,
        stable_logical_key=definition.stable_logical_key,
        version=version.semantic_version,
        manifest_digest=version.manifest_digest,
        instructions_digest=version.instructions_digest,
        instructions=version.instructions,
    )


class SqlAlchemySkillResolver:
    """Resolve the exact installed bundle from a short-lived DB session."""

    def __init__(self, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def resolve(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        workspace_id: str,
        agent_version_id: str,
    ) -> SkillInstructionBundle:
        with self._session_factory() as session:
            agent_version_digest = _validate_personal_identity(
                session,
                tenant_id=tenant_id,
                tenant_schema=tenant_schema,
                owner_user_id=owner_user_id,
                workspace_id=workspace_id,
                agent_version_id=agent_version_id,
            )
            rows = session.execute(
                select(
                    WorkspaceAgentSkillInstallationModel,
                    SkillDefinitionModel,
                    SkillVersionModel,
                )
                .join(
                    SkillDefinitionModel,
                    (
                        SkillDefinitionModel.id
                        == WorkspaceAgentSkillInstallationModel.skill_definition_id
                    )
                    & (
                        SkillDefinitionModel.tenant_id
                        == WorkspaceAgentSkillInstallationModel.tenant_id
                    ),
                )
                .join(
                    SkillVersionModel,
                    (SkillVersionModel.id == WorkspaceAgentSkillInstallationModel.skill_version_id)
                    & (
                        SkillVersionModel.tenant_id
                        == WorkspaceAgentSkillInstallationModel.tenant_id
                    ),
                )
                .where(
                    WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
                    WorkspaceAgentSkillInstallationModel.owner_user_id == owner_user_id,
                    WorkspaceAgentSkillInstallationModel.workspace_id == workspace_id,
                    WorkspaceAgentSkillInstallationModel.agent_version_id == agent_version_id,
                    WorkspaceAgentSkillInstallationModel.installation_state == "installed",
                )
                .order_by(
                    SkillDefinitionModel.stable_logical_key,
                    SkillVersionModel.semantic_version,
                    SkillVersionModel.id,
                )
            ).all()
            items = tuple(
                _instruction_from_row(*row, agent_version_digest=agent_version_digest)
                for row in rows
            )
            if len({item.stable_logical_key for item in items}) != len(items):
                raise SkillResolutionError("skill_bundle_contains_duplicate_definition")
            return SkillInstructionBundle(
                canonical_digest=_canonical_digest(items),
                items=items,
            )


__all__ = [
    "SkillInstruction",
    "SkillInstructionBundle",
    "SkillResolutionError",
    "SkillResolver",
    "SqlAlchemySkillResolver",
]
