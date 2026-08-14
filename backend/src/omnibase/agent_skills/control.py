"""Audited Browser orchestration for P6.1 native instruction Skills."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from omnibase.agent_skills.limits import (
    MAX_LIVE_SKILL_INSTALLATIONS,
    MAX_SKILL_INSTRUCTION_BYTES,
    SkillBundleLimitError,
    validate_skill_bundle_limits,
)
from omnibase.agent_skills.models import (
    SkillDefinitionModel,
    SkillVersionModel,
    WorkspaceAgentSkillInstallationModel,
)
from omnibase.agent_skills.native_catalog import (
    NativeSkillCatalogItem,
    get_native_skill,
    materialize_native_skill,
)
from omnibase.agent_skills.schemas import SkillInstallationList, SkillInstallationRead
from omnibase.agent_skills.service import (
    SkillConflictError,
    SkillNotFoundError,
    SkillPersistenceService,
    SkillStateError,
)
from omnibase.control_plane.service import (
    IdempotencyConflict,
    append_audit_event,
    complete_idempotency,
    register_resource,
    reserve_idempotency,
)

_IDEMPOTENCY_TTL = timedelta(hours=24)


class NativeSkillControlError(ValueError):
    def __init__(self, code: str, *, status: int = 409) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


def _request_hash(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _project_installation(
    installation: WorkspaceAgentSkillInstallationModel,
    definition: SkillDefinitionModel,
    version: SkillVersionModel,
) -> SkillInstallationRead:
    return SkillInstallationRead(
        installation_id=installation.id,
        workspace_id=installation.workspace_id,
        agent_version_id=installation.agent_version_id,
        stable_logical_key=definition.stable_logical_key,
        display_name=definition.display_name,
        semantic_version=version.semantic_version,
        manifest_digest=version.manifest_digest,
        installation_state=installation.installation_state,
        created_at=installation.created_at.isoformat() if installation.created_at else None,
        disabled_at=installation.disabled_at.isoformat() if installation.disabled_at else None,
        revoked_at=installation.revoked_at.isoformat() if installation.revoked_at else None,
    )


def _assert_definition_matches_catalog(
    definition: SkillDefinitionModel,
    item: NativeSkillCatalogItem,
    *,
    tenant_id: str,
    owner_user_id: str,
) -> None:
    expected = item.definition
    if (
        definition.id != expected.skill_definition_id
        or definition.tenant_id != tenant_id
        or definition.stable_logical_key != expected.stable_logical_key
        or definition.display_name != expected.display_name
        or definition.description != expected.description
        or definition.definition_state != "active"
        or definition.installation_scopes != ["workspace"]
        or definition.first_party is not True
        or definition.created_by != owner_user_id
    ):
        raise NativeSkillControlError("native_skill_definition_catalog_drifted")


def _assert_version_matches_catalog(
    version: SkillVersionModel,
    item: NativeSkillCatalogItem,
    *,
    tenant_id: str,
    owner_user_id: str,
) -> None:
    expected = item.version
    if (
        version.id != expected.skill_version_id
        or version.tenant_id != tenant_id
        or version.definition_id != expected.skill_definition_id
        or version.semantic_version != expected.version
        or version.version_state != "sealed"
        or version.kind != "instruction"
        or version.manifest_payload != expected.to_dict()
        or version.manifest_digest != expected.canonical_digest()
        or version.instructions != expected.instructions
        or version.instructions_digest != expected.instructions_digest
        or version.required_tool_ids != []
        or version.capability_requirements != []
        or version.network_policy != "deny"
        or version.secrets_allowed is not False
        or version.max_tool_calls != 0
        or version.rollback_version_id != expected.rollback_version_id
        or version.created_by != owner_user_id
    ):
        raise NativeSkillControlError("native_skill_version_catalog_drifted")


class NativeSkillControlService:
    """One-Owner native Skill catalog registration and exact installation."""

    def __init__(self, session: Session) -> None:
        self._session = session
        self._persistence = SkillPersistenceService(session)

    def list_installations(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        workspace_id: str,
        agent_version_id: str,
    ) -> SkillInstallationList:
        self._persistence.validate_owner_workspace_agent(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
        )
        filters = (
            WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
            WorkspaceAgentSkillInstallationModel.owner_user_id == owner_user_id,
            WorkspaceAgentSkillInstallationModel.workspace_id == workspace_id,
            WorkspaceAgentSkillInstallationModel.agent_version_id == agent_version_id,
        )
        total = (
            self._session.scalar(
                select(func.count())
                .select_from(WorkspaceAgentSkillInstallationModel)
                .where(*filters)
            )
            or 0
        )
        rows = self._session.execute(
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
                    SkillDefinitionModel.tenant_id == WorkspaceAgentSkillInstallationModel.tenant_id
                ),
            )
            .join(
                SkillVersionModel,
                (SkillVersionModel.id == WorkspaceAgentSkillInstallationModel.skill_version_id)
                & (SkillVersionModel.tenant_id == WorkspaceAgentSkillInstallationModel.tenant_id),
            )
            .where(*filters)
            .order_by(
                WorkspaceAgentSkillInstallationModel.created_at.desc(),
                WorkspaceAgentSkillInstallationModel.id.desc(),
            )
        ).all()
        live_instructions = [
            version.instructions
            for installation, _, version in rows
            if installation.installation_state == "installed"
        ]
        try:
            live_count, live_instruction_bytes = validate_skill_bundle_limits(live_instructions)
        except SkillBundleLimitError as exc:
            raise NativeSkillControlError(str(exc)) from exc
        return SkillInstallationList(
            items=[_project_installation(*row) for row in rows],
            total=int(total),
            live_count=live_count,
            live_instruction_bytes=live_instruction_bytes,
            max_live_installations=MAX_LIVE_SKILL_INSTALLATIONS,
            max_instruction_bytes=MAX_SKILL_INSTRUCTION_BYTES,
        )

    def install_native(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        workspace_id: str,
        agent_version_id: str,
        stable_key: str,
        expected_manifest_digest: str,
        idempotency_key: str,
        request_id: str,
    ) -> SkillInstallationRead:
        try:
            item = get_native_skill(stable_key)
        except KeyError as exc:
            raise NativeSkillControlError("native_skill_not_found", status=404) from exc
        if item.version.canonical_digest() != expected_manifest_digest:
            raise NativeSkillControlError("native_skill_manifest_digest_mismatch")
        self._persistence.validate_owner_workspace_agent(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
        )
        intent = {
            "stable_key": stable_key,
            "manifest_digest": expected_manifest_digest,
            "workspace_id": workspace_id,
            "agent_version_id": agent_version_id,
        }
        digest = _request_hash(intent)
        record, created = reserve_idempotency(
            self._session,
            tenant_id=tenant_id,
            actor_scope=f"user:{owner_user_id}",
            operation_name="native_skill.install",
            key=idempotency_key,
            request_hash=digest,
            expires_at=datetime.now(UTC) + _IDEMPOTENCY_TTL,
        )
        if not created:
            if record.state != "completed" or not isinstance(record.response_ref, dict):
                raise NativeSkillControlError("native_skill_installation_in_progress")
            installation_id = record.response_ref.get("installation_id")
            if not isinstance(installation_id, str):
                raise NativeSkillControlError("native_skill_replay_receipt_invalid")
            return self._get_installation(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                installation_id=installation_id,
            )
        tenant_item = materialize_native_skill(item, tenant_id=tenant_id)
        definition_created, version_created = self._ensure_registered(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
            item=tenant_item,
        )
        installation = self._persistence.install(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            skill_version_id=tenant_item.version.skill_version_id,
        )
        receipt = _project_installation(
            installation,
            self._definition(tenant_id, tenant_item),
            self._version(tenant_id, tenant_item),
        )
        if definition_created:
            append_audit_event(
                self._session,
                tenant_id=tenant_id,
                request_id=request_id,
                actor_type="user",
                actor_id=owner_user_id,
                action="skills.native_definition_registered",
                decision="allowed",
                risk_level="R1",
                resource_id=tenant_item.definition.skill_definition_id,
                input_hash=digest,
                details={"stable_logical_key": stable_key},
            )
        if version_created:
            append_audit_event(
                self._session,
                tenant_id=tenant_id,
                request_id=request_id,
                actor_type="user",
                actor_id=owner_user_id,
                action="skills.native_version_sealed",
                decision="allowed",
                risk_level="R1",
                resource_id=tenant_item.version.skill_version_id,
                input_hash=digest,
                details={
                    "stable_logical_key": stable_key,
                    "manifest_digest": tenant_item.version.canonical_digest(),
                },
            )
        append_audit_event(
            self._session,
            tenant_id=tenant_id,
            request_id=request_id,
            actor_type="user",
            actor_id=owner_user_id,
            workspace_id=workspace_id,
            action="skills.native_installed",
            decision="allowed",
            risk_level="R1",
            input_hash=digest,
            status_code=201,
            row_count=1,
            details={
                "stable_logical_key": stable_key,
                "manifest_digest": expected_manifest_digest,
                "agent_version_id": agent_version_id,
            },
        )
        complete_idempotency(
            self._session,
            tenant_id=tenant_id,
            record_id=record.id,
            expected_version=record.version,
            response_ref={"installation_id": installation.id},
        )
        return receipt

    def disable(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        workspace_id: str,
        agent_version_id: str,
        installation_id: str,
        idempotency_key: str,
        request_id: str,
    ) -> SkillInstallationRead:
        self._persistence.validate_owner_workspace_agent(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
        )
        digest = _request_hash(
            {
                "installation_id": installation_id,
                "workspace_id": workspace_id,
                "agent_version_id": agent_version_id,
            }
        )
        record, created = reserve_idempotency(
            self._session,
            tenant_id=tenant_id,
            actor_scope=f"user:{owner_user_id}",
            operation_name="native_skill.disable",
            key=idempotency_key,
            request_hash=digest,
            expires_at=datetime.now(UTC) + _IDEMPOTENCY_TTL,
        )
        if not created:
            if record.state != "completed":
                raise NativeSkillControlError("native_skill_disable_in_progress")
            return self._get_installation(
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
                installation_id=installation_id,
            )
        current = self._get_installation_row(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            installation_id=installation_id,
        )
        if current.workspace_id != workspace_id or current.agent_version_id != agent_version_id:
            raise NativeSkillControlError("native_skill_installation_not_found", status=404)
        updated = self._persistence.disable(
            tenant_id=tenant_id,
            tenant_schema=tenant_schema,
            owner_user_id=owner_user_id,
            installation_id=installation_id,
        )
        append_audit_event(
            self._session,
            tenant_id=tenant_id,
            request_id=request_id,
            actor_type="user",
            actor_id=owner_user_id,
            workspace_id=workspace_id,
            action="skills.installation_disabled",
            decision="allowed",
            risk_level="R1",
            input_hash=digest,
            status_code=200,
            row_count=1,
            details={"installation_id": installation_id, "agent_version_id": agent_version_id},
        )
        complete_idempotency(
            self._session,
            tenant_id=tenant_id,
            record_id=record.id,
            expected_version=record.version,
            response_ref={"installation_id": installation_id},
        )
        return self._get_installation(
            tenant_id=tenant_id,
            owner_user_id=owner_user_id,
            installation_id=updated.id,
        )

    def _ensure_registered(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        owner_user_id: str,
        item: NativeSkillCatalogItem,
    ) -> tuple[bool, bool]:
        definition_created = False
        version_created = False
        definition = self._session.scalar(
            select(SkillDefinitionModel).where(
                SkillDefinitionModel.tenant_id == tenant_id,
                SkillDefinitionModel.stable_logical_key == item.definition.stable_logical_key,
            )
        )
        if definition is None:
            definition = self._persistence.register_definition(
                tenant_id=tenant_id,
                tenant_schema=tenant_schema,
                owner_user_id=owner_user_id,
                definition=item.definition,
            )
            register_resource(
                self._session,
                tenant_id=tenant_id,
                resource_id=definition.id,
                kind="skill_definition",
                owner_type="user",
                owner_id=owner_user_id,
                display_name=definition.display_name,
                policy_class="tenant_managed",
                created_by_actor_id=owner_user_id,
            )
            definition_created = True
        else:
            _assert_definition_matches_catalog(
                definition,
                item,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
            )
        version = self._session.scalar(
            select(SkillVersionModel).where(
                SkillVersionModel.tenant_id == tenant_id,
                SkillVersionModel.id == item.version.skill_version_id,
            )
        )
        if version is None:
            version = self._persistence.seal_version(
                tenant_id=tenant_id,
                tenant_schema=tenant_schema,
                owner_user_id=owner_user_id,
                version=item.version,
            )
            register_resource(
                self._session,
                tenant_id=tenant_id,
                resource_id=version.id,
                kind="skill_version",
                owner_type="user",
                owner_id=owner_user_id,
                parent_id=definition.id,
                display_name=f"{definition.display_name} {version.semantic_version}",
                policy_class="tenant_managed",
                created_by_actor_id=owner_user_id,
            )
            version_created = True
        else:
            _assert_version_matches_catalog(
                version,
                item,
                tenant_id=tenant_id,
                owner_user_id=owner_user_id,
            )
        return definition_created, version_created

    def _definition(self, tenant_id: str, item: NativeSkillCatalogItem) -> SkillDefinitionModel:
        definition = self._session.scalar(
            select(SkillDefinitionModel).where(
                SkillDefinitionModel.tenant_id == tenant_id,
                SkillDefinitionModel.id == item.definition.skill_definition_id,
            )
        )
        if definition is None:
            raise NativeSkillControlError("native_skill_definition_not_found")
        return definition

    def _version(self, tenant_id: str, item: NativeSkillCatalogItem) -> SkillVersionModel:
        version = self._session.scalar(
            select(SkillVersionModel).where(
                SkillVersionModel.tenant_id == tenant_id,
                SkillVersionModel.id == item.version.skill_version_id,
            )
        )
        if version is None:
            raise NativeSkillControlError("native_skill_version_not_found")
        return version

    def _get_installation_row(
        self, *, tenant_id: str, owner_user_id: str, installation_id: str
    ) -> WorkspaceAgentSkillInstallationModel:
        row = self._session.scalar(
            select(WorkspaceAgentSkillInstallationModel).where(
                WorkspaceAgentSkillInstallationModel.id == installation_id,
                WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
                WorkspaceAgentSkillInstallationModel.owner_user_id == owner_user_id,
            )
        )
        if row is None:
            raise NativeSkillControlError("native_skill_installation_not_found", status=404)
        return row

    def _get_installation(
        self, *, tenant_id: str, owner_user_id: str, installation_id: str
    ) -> SkillInstallationRead:
        row = self._session.execute(
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
                    SkillDefinitionModel.tenant_id == WorkspaceAgentSkillInstallationModel.tenant_id
                ),
            )
            .join(
                SkillVersionModel,
                (SkillVersionModel.id == WorkspaceAgentSkillInstallationModel.skill_version_id)
                & (SkillVersionModel.tenant_id == WorkspaceAgentSkillInstallationModel.tenant_id),
            )
            .where(
                WorkspaceAgentSkillInstallationModel.id == installation_id,
                WorkspaceAgentSkillInstallationModel.tenant_id == tenant_id,
                WorkspaceAgentSkillInstallationModel.owner_user_id == owner_user_id,
            )
        ).one_or_none()
        if row is None:
            raise NativeSkillControlError("native_skill_installation_not_found", status=404)
        return _project_installation(*row)


def translate_skill_error(exc: Exception) -> NativeSkillControlError:
    if isinstance(exc, NativeSkillControlError):
        return exc
    if isinstance(exc, IdempotencyConflict):
        return NativeSkillControlError("native_skill_idempotency_conflict")
    if isinstance(exc, (SkillConflictError, SkillNotFoundError, SkillStateError)):
        code = str(exc.args[0]) if exc.args else "native_skill_conflict"
        return NativeSkillControlError(code)
    raise exc


__all__ = ["NativeSkillControlError", "NativeSkillControlService", "translate_skill_error"]
