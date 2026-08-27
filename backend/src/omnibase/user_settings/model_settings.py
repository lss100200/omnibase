"""Server-owned P6 role model-selection settings and scope validation."""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from omnibase.agent_registry.models import WorkspaceAgentBindingModel
from omnibase.control_plane.models import AuditEvent
from omnibase.core.config import Settings
from omnibase.db.models import Tenant
from omnibase.db.tenant import (
    ModelProviderCredential,
    User,
    WorkspaceAgentModelOverride,
)
from omnibase.model_gateway.endpoint_policy import (
    ProviderEndpointPolicyError,
    ResolvedProviderEndpoint,
    create_hardened_provider_client,
    resolve_provider_endpoint,
)
from omnibase.user_settings.crypto import CredentialCipher
from omnibase.user_settings.model_identifiers import (
    ModelIdentifierError,
    validate_public_model_id,
)
from omnibase.user_settings.schemas import (
    AgentModelSettingList,
    AgentModelSettingRead,
    AgentModelSettingWrite,
    EmployeeRoleId,
    ModelFamily,
    ProviderTestResult,
    ProviderTestStatus,
)
from omnibase.user_settings.service import (
    UserSettingsError,
    UserSettingsNotFound,
)
from omnibase.workspaces.models import Workspace, WorkspaceMembership

EMPLOYEE_ROLE_IDS: tuple[EmployeeRoleId, ...] = (
    "parent",
    "product",
    "ux",
    "frontend",
    "backend",
    "data",
    "security",
    "qa",
    "operations",
    "docs",
)
_ALLOWED_MEMBER_ROLES = frozenset({"member", "operator", "maintainer", "owner"})


@dataclass(frozen=True, slots=True)
class AgentModelScopeSnapshot:
    workspace_generation: int
    binding_id: str
    agent_version_digest: str


_FAMILY_TOKENS: tuple[tuple[ModelFamily, tuple[str, ...]], ...] = (
    ("deepseek", ("deepseek",)),
    ("glm", ("zhipu", "bigmodel", "chatglm", "glm")),
    ("kimi", ("moonshot", "kimi")),
    ("openai", ("openai", "gpt", "o1", "o3", "o4")),
    ("anthropic", ("anthropic", "claude")),
)


def _digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def detect_model_family(model_id: str | None) -> ModelFamily:
    """Return a display/adaptation family without treating it as provider identity."""
    if not model_id:
        return "generic"
    value = re.sub(
        r"[_.:/\\\s]+",
        "-",
        unicodedata.normalize("NFKC", model_id).casefold(),
    )
    matches = {
        family
        for family, tokens in _FAMILY_TOKENS
        if any(re.search(rf"(?:^|-){re.escape(token)}(?:-|$)", value) for token in tokens)
    }
    return next(iter(matches)) if len(matches) == 1 else "generic"


def _tested_configuration_digest(
    credential: ModelProviderCredential,
    *,
    model_id: str,
    override_id: str,
    override_version: int,
    scope: AgentModelScopeSnapshot,
    endpoint_policy_digest: str,
) -> str:
    return _digest(
        {
            "override_id": override_id,
            "override_version": override_version,
            "workspace_generation": scope.workspace_generation,
            "workspace_agent_binding_id": scope.binding_id,
            "agent_version_digest": scope.agent_version_digest,
            "endpoint_policy_digest": endpoint_policy_digest,
            "credential_id": str(credential.id),
            "credential_version": credential.version,
            "provider_id": credential.provider_id,
            "base_url": credential.base_url,
            "model_id": model_id,
            "key_version": credential.key_version,
            "key_fingerprint": credential.key_fingerprint,
            "is_active": credential.is_active,
            "revoked_at": (
                credential.revoked_at.isoformat() if credential.revoked_at is not None else None
            ),
        }
    )


class AgentModelSettingsService:
    """Manage logical overrides while revalidating global Workspace authority."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _endpoint(
        self,
        credential: ModelProviderCredential | None,
        *,
        settings: Settings | None = None,
    ) -> ResolvedProviderEndpoint | None:
        if credential is None:
            return None
        active_settings = settings or self._settings
        if active_settings is None:
            return None
        try:
            return resolve_provider_endpoint(
                credential.base_url,
                allowed_hosts=active_settings.provider_endpoint_allowlist,
            )
        except ProviderEndpointPolicyError:
            return None

    @staticmethod
    def _validate_scope(
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        workspace_id: str,
        agent_version_id: str,
        lock: bool,
    ) -> AgentModelScopeSnapshot:
        user_stmt = select(User).where(User.id == user_id, User.is_active.is_(True))
        tenant_stmt = select(Tenant).where(Tenant.id == tenant_id, Tenant.is_active.is_(True))
        workspace_stmt = select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.tenant_id == tenant_id,
            Workspace.observed_state != "archived",
        )
        membership_stmt = select(WorkspaceMembership).where(
            WorkspaceMembership.tenant_id == tenant_id,
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user_id,
            WorkspaceMembership.state == "active",
        )
        binding_stmt = select(WorkspaceAgentBindingModel).where(
            WorkspaceAgentBindingModel.tenant_id == tenant_id,
            WorkspaceAgentBindingModel.workspace_id == workspace_id,
            WorkspaceAgentBindingModel.agent_version_id == agent_version_id,
            WorkspaceAgentBindingModel.binding_state == "installed",
        )
        if lock:
            tenant_stmt = tenant_stmt.with_for_update()
            user_stmt = user_stmt.with_for_update()
            workspace_stmt = workspace_stmt.with_for_update()
            membership_stmt = membership_stmt.with_for_update()
            binding_stmt = binding_stmt.with_for_update()
        tenant = session.execute(tenant_stmt).scalar_one_or_none()
        user = session.execute(user_stmt).scalar_one_or_none()
        workspace = session.execute(workspace_stmt).scalar_one_or_none()
        membership = session.execute(membership_stmt).scalar_one_or_none()
        binding = session.execute(binding_stmt).scalar_one_or_none()
        if tenant is None:
            raise UserSettingsNotFound("tenant_not_found")
        if user is None:
            raise UserSettingsNotFound("user_not_found")
        if workspace is None:
            raise UserSettingsNotFound("workspace_not_found")
        if membership is None or membership.role not in _ALLOWED_MEMBER_ROLES:
            raise UserSettingsError("workspace_membership_insufficient", status=403)
        if binding is None or binding.workspace_generation != workspace.generation:
            raise UserSettingsNotFound("agent_binding_not_live")
        return AgentModelScopeSnapshot(
            workspace_generation=workspace.generation,
            binding_id=str(binding.id),
            agent_version_digest=binding.agent_version_digest,
        )

    @staticmethod
    def _credential(
        session: Session,
        *,
        user_id: str,
        credential_id: str | None,
    ) -> ModelProviderCredential | None:
        if credential_id is None:
            statement = (
                select(ModelProviderCredential)
                .where(
                    ModelProviderCredential.user_id == user_id,
                    ModelProviderCredential.is_active.is_(True),
                    ModelProviderCredential.is_default.is_(True),
                    ModelProviderCredential.revoked_at.is_(None),
                )
                .order_by(ModelProviderCredential.created_at.desc())
            )
        else:
            statement = select(ModelProviderCredential).where(
                ModelProviderCredential.id == credential_id,
                ModelProviderCredential.user_id == user_id,
                ModelProviderCredential.is_active.is_(True),
                ModelProviderCredential.revoked_at.is_(None),
            )
        return session.execute(statement).scalar_one_or_none()

    @classmethod
    def _read(
        cls,
        *,
        role: EmployeeRoleId,
        override: WorkspaceAgentModelOverride | None,
        credential: ModelProviderCredential | None,
        scope: AgentModelScopeSnapshot,
        endpoint: ResolvedProviderEndpoint | None,
    ) -> AgentModelSettingRead:
        effective_model = override.model_id if override and override.model_id else None
        if effective_model is None and credential is not None:
            effective_model = credential.model_id
        family_override = (
            cast(ModelFamily, override.family_override)
            if override and override.family_override
            else None
        )
        detected_family = detect_model_family(effective_model)
        family: ModelFamily
        family_source: Literal["model_name", "explicit_override", "unknown"]
        if detected_family != "generic":
            family = detected_family
            family_source = "model_name"
        elif family_override:
            family = family_override
            family_source = "explicit_override"
        else:
            family = "generic"
            family_source = "unknown"
        if credential is None:
            state: Literal["inherited", "pending", "active", "unavailable"] = "unavailable"
        elif not credential.encrypted_api_key or not credential.key_nonce:
            state = "unavailable"
        elif override is None:
            state = "inherited" if credential.last_test_status == "passed" else "pending"
        elif override.model_id is None:
            state = "active" if credential.last_test_status == "passed" else "pending"
        else:
            assert effective_model is not None
            expected_digest = _tested_configuration_digest(
                credential,
                model_id=effective_model,
                override_id=str(override.id),
                override_version=override.version,
                scope=scope,
                endpoint_policy_digest=endpoint.policy_digest if endpoint is not None else "",
            )
            state = (
                "active"
                if override.last_test_status == "passed"
                and endpoint is not None
                and override.tested_endpoint_policy_digest == endpoint.policy_digest
                and override.tested_configuration_digest == expected_digest
                else "pending"
            )
        return AgentModelSettingRead(
            employee_role_id=role,
            inherit_default=override is None,
            override_credential_id=str(override.credential_id)
            if override and override.credential_id
            else None,
            requested_model_id=override.model_id if override else None,
            effective_provider_id=credential.provider_id if credential else None,
            effective_model_id=effective_model,
            family=family,
            family_source=family_source,
            state=state,
            test_status=(
                cast(ProviderTestStatus, override.last_test_status)
                if override and override.model_id and override.last_test_status
                else None
            ),
            tested_at=(override.last_tested_at if override and override.model_id else None),
            version=override.version if override else 0,
        )

    def list_settings(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        workspace_id: str,
        agent_version_id: str,
    ) -> AgentModelSettingList:
        scope = self._validate_scope(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            lock=False,
        )
        overrides = {
            row.employee_role_id: row
            for row in session.execute(
                select(WorkspaceAgentModelOverride).where(
                    WorkspaceAgentModelOverride.user_id == user_id,
                    WorkspaceAgentModelOverride.workspace_id == workspace_id,
                    WorkspaceAgentModelOverride.agent_version_id == agent_version_id,
                )
            ).scalars()
        }
        default = self._credential(session, user_id=user_id, credential_id=None)
        credentials: dict[str, ModelProviderCredential | None] = {}
        items: list[AgentModelSettingRead] = []
        for role in EMPLOYEE_ROLE_IDS:
            override = overrides.get(role)
            credential = default
            if override is not None and override.credential_id is not None:
                key = str(override.credential_id)
                if key not in credentials:
                    credentials[key] = self._credential(session, user_id=user_id, credential_id=key)
                credential = credentials[key]
            items.append(
                self._read(
                    role=role,
                    override=override,
                    credential=credential,
                    scope=scope,
                    endpoint=self._endpoint(credential),
                )
            )
        return AgentModelSettingList(items=items, total=len(items))

    def put_setting(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        workspace_id: str,
        agent_version_id: str,
        employee_role_id: EmployeeRoleId,
        payload: AgentModelSettingWrite,
        request_id: str,
    ) -> AgentModelSettingRead:
        scope = self._validate_scope(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            lock=True,
        )
        row = session.execute(
            select(WorkspaceAgentModelOverride)
            .where(
                WorkspaceAgentModelOverride.user_id == user_id,
                WorkspaceAgentModelOverride.workspace_id == workspace_id,
                WorkspaceAgentModelOverride.agent_version_id == agent_version_id,
                WorkspaceAgentModelOverride.employee_role_id == employee_role_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        current_version = row.version if row else 0
        if payload.expected_version is not None and payload.expected_version != current_version:
            raise UserSettingsError("agent_model_setting_version_conflict")
        if payload.inherit_default:
            if row is not None:
                session.delete(row)
                session.flush()
                self._audit(
                    session,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    request_id=request_id,
                    action="agent_model_setting.delete",
                    workspace_id=workspace_id,
                    agent_version_id=agent_version_id,
                    employee_role_id=employee_role_id,
                    before_version=current_version,
                    after_version=None,
                    credential_id=str(row.credential_id) if row.credential_id else None,
                    model_id=row.model_id,
                )
            default = self._credential(session, user_id=user_id, credential_id=None)
            return self._read(
                role=employee_role_id,
                override=None,
                credential=default,
                scope=scope,
                endpoint=self._endpoint(default),
            )
        if payload.provider_credential_id is None and payload.requested_model_id is None:
            raise UserSettingsError("agent_model_setting_selection_required", status=422)
        credential = self._credential(
            session, user_id=user_id, credential_id=payload.provider_credential_id
        )
        if payload.provider_credential_id is not None and credential is None:
            raise UserSettingsNotFound("provider_credential_not_found")
        if credential is None:
            credential = self._credential(session, user_id=user_id, credential_id=None)
        if row is None:
            row = WorkspaceAgentModelOverride(
                id=str(uuid4()),
                user_id=user_id,
                workspace_id=workspace_id,
                agent_version_id=agent_version_id,
                employee_role_id=employee_role_id,
                version=1,
            )
            session.add(row)
        else:
            row.version += 1
        row.credential_id = payload.provider_credential_id
        try:
            row.model_id = (
                validate_public_model_id(payload.requested_model_id)
                if payload.requested_model_id is not None
                else None
            )
        except ModelIdentifierError as exc:
            raise UserSettingsError(str(exc), status=422) from exc
        row.family_override = payload.family_override
        row.last_test_status = None
        row.last_tested_at = None
        row.tested_configuration_digest = None
        row.tested_endpoint_policy_digest = None
        row.updated_at = datetime.now(UTC)
        session.flush()
        self._audit(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=request_id,
            action=(
                "agent_model_setting.create"
                if current_version == 0
                else "agent_model_setting.update"
            ),
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            employee_role_id=employee_role_id,
            before_version=current_version or None,
            after_version=row.version,
            credential_id=str(row.credential_id) if row.credential_id else None,
            model_id=row.model_id,
        )
        return self._read(
            role=employee_role_id,
            override=row,
            credential=credential,
            scope=scope,
            endpoint=self._endpoint(credential),
        )

    def delete_setting(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        workspace_id: str,
        agent_version_id: str,
        employee_role_id: EmployeeRoleId,
        expected_version: int,
        request_id: str,
    ) -> AgentModelSettingRead:
        payload = AgentModelSettingWrite(
            inherit_default=True,
            expected_version=expected_version,
        )
        return self.put_setting(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            employee_role_id=employee_role_id,
            payload=payload,
            request_id=request_id,
        )

    def test_setting(
        self,
        session: Session,
        *,
        settings: Settings,
        tenant_id: str,
        user_id: str,
        workspace_id: str,
        agent_version_id: str,
        employee_role_id: EmployeeRoleId,
        request_id: str,
    ) -> ProviderTestResult:
        tested_scope = self._validate_scope(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            lock=False,
        )
        row = session.execute(
            select(WorkspaceAgentModelOverride).where(
                WorkspaceAgentModelOverride.user_id == user_id,
                WorkspaceAgentModelOverride.workspace_id == workspace_id,
                WorkspaceAgentModelOverride.agent_version_id == agent_version_id,
                WorkspaceAgentModelOverride.employee_role_id == employee_role_id,
            )
        ).scalar_one_or_none()
        if row is None or row.model_id is None:
            raise UserSettingsError("agent_model_setting_test_not_required", status=422)
        credential = self._credential(
            session,
            user_id=user_id,
            credential_id=str(row.credential_id) if row.credential_id else None,
        )
        if credential is None or not credential.encrypted_api_key or not credential.key_nonce:
            raise UserSettingsError("agent_model_setting_credential_unavailable")
        candidate_model_id = row.model_id
        try:
            tested_endpoint = resolve_provider_endpoint(
                credential.base_url,
                allowed_hosts=settings.provider_endpoint_allowlist,
            )
        except ProviderEndpointPolicyError as exc:
            raise UserSettingsError(str(exc), status=422) from exc
        tested_override_id = str(row.id)
        tested_override_version = row.version
        tested_digest = _tested_configuration_digest(
            credential,
            model_id=candidate_model_id,
            override_id=tested_override_id,
            override_version=tested_override_version,
            scope=tested_scope,
            endpoint_policy_digest=tested_endpoint.policy_digest,
        )
        aad = CredentialCipher.aad(
            tenant_id=tenant_id,
            user_id=user_id,
            credential_id=str(credential.id),
            provider_id=credential.provider_id,
            key_version=credential.key_version,
        )
        try:
            secret = CredentialCipher.from_settings(settings).decrypt(
                credential.encrypted_api_key,
                credential.key_nonce,
                aad=aad,
            )
        except RuntimeError as exc:
            raise UserSettingsError("agent_model_setting_credential_unavailable") from exc

        session.commit()
        started = time.monotonic()
        status_value = "failed"
        actual_model_id: str | None = None
        try:
            with create_hardened_provider_client(tested_endpoint, timeout=10.0) as client:
                response = client.post(
                    "chat/completions",
                    headers={"Authorization": f"Bearer {secret}"},
                    json={
                        "model": candidate_model_id,
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                        "temperature": 0,
                        "max_tokens": 8,
                        "stream": False,
                    },
                )
            if response.status_code in {401, 403}:
                status_value = "auth_failed"
            elif response.is_success:
                try:
                    actual_model_id = str(response.json().get("model") or "")
                except (ValueError, AttributeError):
                    status_value = "failed"
                else:
                    status_value = (
                        "passed" if actual_model_id == candidate_model_id else "identity_mismatch"
                    )
        except httpx.TimeoutException:
            status_value = "timeout"
        except httpx.RequestError:
            status_value = "unreachable"
        finally:
            secret = ""
        latency_ms = max(0, int((time.monotonic() - started) * 1000))

        locked_scope = self._validate_scope(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            lock=True,
        )
        locked = session.execute(
            select(WorkspaceAgentModelOverride)
            .where(
                WorkspaceAgentModelOverride.user_id == user_id,
                WorkspaceAgentModelOverride.workspace_id == workspace_id,
                WorkspaceAgentModelOverride.agent_version_id == agent_version_id,
                WorkspaceAgentModelOverride.employee_role_id == employee_role_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        locked_credential = self._credential(
            session,
            user_id=user_id,
            credential_id=(
                str(locked.credential_id) if locked is not None and locked.credential_id else None
            ),
        )
        if (
            locked is None
            or str(locked.id) != tested_override_id
            or locked.version != tested_override_version
            or locked.model_id is None
            or locked_credential is None
            or locked_scope != tested_scope
            or _tested_configuration_digest(
                locked_credential,
                model_id=locked.model_id,
                override_id=str(locked.id),
                override_version=locked.version,
                scope=locked_scope,
                endpoint_policy_digest=tested_endpoint.policy_digest,
            )
            != tested_digest
        ):
            raise UserSettingsError("agent_model_setting_changed_during_test")
        locked.last_test_status = status_value
        locked.last_tested_at = datetime.now(UTC)
        locked.tested_configuration_digest = tested_digest
        locked.tested_endpoint_policy_digest = tested_endpoint.policy_digest
        locked.updated_at = locked.last_tested_at
        session.flush()
        self._audit(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=request_id,
            action="agent_model_setting.test",
            workspace_id=workspace_id,
            agent_version_id=agent_version_id,
            employee_role_id=employee_role_id,
            before_version=locked.version,
            after_version=locked.version,
            credential_id=str(locked_credential.id),
            model_id=locked.model_id,
            test_status=status_value,
        )
        return ProviderTestResult(
            status=status_value,  # type: ignore[arg-type]
            latency_ms=latency_ms,
            requested_model_id=candidate_model_id,
            actual_model_id=actual_model_id or None,
        )

    @staticmethod
    def _audit(
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        request_id: str,
        action: str,
        workspace_id: str,
        agent_version_id: str,
        employee_role_id: EmployeeRoleId,
        before_version: int | None,
        after_version: int | None,
        credential_id: str | None,
        model_id: str | None,
        test_status: str | None = None,
    ) -> None:
        session.add(
            AuditEvent(
                tenant_id=tenant_id,
                request_id=request_id,
                actor_type="user",
                actor_id=user_id,
                workspace_id=workspace_id,
                action=action,
                decision="allowed",
                risk_level="R1",
                input_hash=_digest(
                    {
                        "workspace_id": workspace_id,
                        "agent_version_id": agent_version_id,
                        "employee_role_id": employee_role_id,
                        "credential_id": credential_id,
                        "model_id": model_id,
                        "version": after_version,
                    }
                ),
                before_version=before_version,
                after_version=after_version,
                status_code=200,
                details={
                    "agent_version_id": agent_version_id,
                    "employee_role_id": employee_role_id,
                    "credential_id": credential_id,
                    "model_id": model_id,
                    "test_status": test_status,
                },
            )
        )


__all__ = ["EMPLOYEE_ROLE_IDS", "AgentModelSettingsService", "detect_model_family"]
