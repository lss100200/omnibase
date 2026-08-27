"""Request-scoped personal Model Gateway selection for Agent Alpha."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from omnibase.agent_alpha.contracts import AlphaGatewaySelection, AlphaUserPreferences
from omnibase.core.config import Settings
from omnibase.core.db import (
    TENANT_CONTEXT_REQUIRED_SESSION_KEY,
    TENANT_SCHEMA_SESSION_KEY,
)
from omnibase.db.tenant import ModelProviderCredential, User, WorkspaceAgentModelOverride
from omnibase.model_gateway import ModelGateway, UnavailableModelGateway
from omnibase.model_gateway.endpoint_policy import (
    ProviderEndpointPolicyError,
    create_hardened_provider_client,
    resolve_provider_endpoint,
)
from omnibase.model_gateway.providers import OpenAICompatibleProvider
from omnibase.user_settings.crypto import CredentialCipher
from omnibase.user_settings.model_settings import (
    EMPLOYEE_ROLE_IDS,
    AgentModelSettingsService,
    _tested_configuration_digest,
    detect_model_family,
)


class PersonalModelGatewayUnavailable(RuntimeError):
    """A configured personal credential cannot be safely used."""


def _configuration_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _model_family(
    effective_model_id: str,
    override: WorkspaceAgentModelOverride | None,
) -> tuple[str, Literal["model_name", "explicit_override", "unknown"]]:
    detected = detect_model_family(effective_model_id)
    if detected != "generic":
        return detected, "model_name"
    if override is not None and override.family_override:
        return override.family_override, "explicit_override"
    return "generic", "unknown"


class UserModelGatewayResolver:
    """Prefer one tested personal default; otherwise use the explicit operator default."""

    def __init__(
        self,
        factory: sessionmaker[Any],
        *,
        settings: Settings,
        operator_gateway: ModelGateway | UnavailableModelGateway,
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._operator_gateway = operator_gateway

    def _personal_provider(self, row: ModelProviderCredential, secret: str):
        try:
            endpoint = resolve_provider_endpoint(
                row.base_url,
                allowed_hosts=self._settings.provider_endpoint_allowlist,
            )
        except ProviderEndpointPolicyError as exc:
            raise PersonalModelGatewayUnavailable(str(exc)) from exc
        provider = OpenAICompatibleProvider(
            provider_id=row.provider_id,
            api_key=secret,
            base_url=endpoint.base_url,
            client_factory=lambda **kwargs: __import__("openai").OpenAI(
                **kwargs,
                max_retries=0,
                http_client=create_hardened_provider_client(
                    endpoint,
                    timeout=float(kwargs.get("timeout", 30.0)),
                ),
            ),
        )
        return endpoint, provider

    def resolve(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        actor_user_id: str,
        workspace_id: str,
        agent_version_id: str,
        employee_role_id: str,
    ) -> AlphaGatewaySelection:
        session = self._factory()
        session.info[TENANT_SCHEMA_SESSION_KEY] = tenant_schema
        session.info[TENANT_CONTEXT_REQUIRED_SESSION_KEY] = True
        try:
            if employee_role_id not in EMPLOYEE_ROLE_IDS:
                raise PersonalModelGatewayUnavailable("agent_model_employee_role_invalid")
            scope = AgentModelSettingsService._validate_scope(
                session,
                tenant_id=tenant_id,
                user_id=actor_user_id,
                workspace_id=workspace_id,
                agent_version_id=agent_version_id,
                lock=False,
            )
            user = session.execute(
                select(User).where(User.id == actor_user_id, User.is_active.is_(True))
            ).scalar_one_or_none()
            if user is None:
                raise PersonalModelGatewayUnavailable("personal_model_gateway_user_inactive")
            override = session.execute(
                select(WorkspaceAgentModelOverride).where(
                    WorkspaceAgentModelOverride.user_id == actor_user_id,
                    WorkspaceAgentModelOverride.workspace_id == workspace_id,
                    WorkspaceAgentModelOverride.agent_version_id == agent_version_id,
                    WorkspaceAgentModelOverride.employee_role_id == employee_role_id,
                )
            ).scalar_one_or_none()
            credential_statement = select(ModelProviderCredential).where(
                ModelProviderCredential.user_id == actor_user_id,
                ModelProviderCredential.is_active.is_(True),
                ModelProviderCredential.revoked_at.is_(None),
            )
            if override is not None and override.credential_id is not None:
                credential_statement = credential_statement.where(
                    ModelProviderCredential.id == override.credential_id
                )
            else:
                credential_statement = credential_statement.where(
                    ModelProviderCredential.is_default.is_(True)
                ).order_by(ModelProviderCredential.created_at.desc())
            row = session.execute(credential_statement).scalar_one_or_none()
            if row is None:
                if override is not None:
                    raise PersonalModelGatewayUnavailable(
                        "agent_model_override_credential_unavailable"
                    )
                if isinstance(self._operator_gateway, UnavailableModelGateway):
                    raise PersonalModelGatewayUnavailable("model_gateway_unavailable")
                return AlphaGatewaySelection(
                    gateway=self._operator_gateway,
                    credential_source="operator_default",
                    configuration_digest=_configuration_digest(
                        {
                            "credential_source": "operator_default",
                            "provider_id": self._operator_gateway.provider_id,
                            "model_id": self._operator_gateway.model_id,
                        }
                    ),
                    credential_id=None,
                    employee_role_id=employee_role_id,
                )
            if row.last_test_status != "passed":
                raise PersonalModelGatewayUnavailable("personal_model_gateway_test_required")
            if not row.encrypted_api_key or not row.key_nonce:
                raise PersonalModelGatewayUnavailable("personal_model_gateway_secret_missing")
            aad = CredentialCipher.aad(
                tenant_id=tenant_id,
                user_id=actor_user_id,
                credential_id=str(row.id),
                provider_id=row.provider_id,
                key_version=row.key_version,
            )
            try:
                secret = CredentialCipher.from_settings(self._settings).decrypt(
                    row.encrypted_api_key,
                    row.key_nonce,
                    aad=aad,
                )
            except RuntimeError as exc:
                raise PersonalModelGatewayUnavailable(
                    "personal_model_gateway_decryption_failed"
                ) from exc
            endpoint, provider = self._personal_provider(row, secret)
            effective_model_id = (
                override.model_id if override and override.model_id else row.model_id
            )
            if override is not None and override.model_id is not None:
                expected_test_digest = _tested_configuration_digest(
                    row,
                    model_id=effective_model_id,
                    override_id=str(override.id),
                    override_version=override.version,
                    scope=scope,
                    endpoint_policy_digest=endpoint.policy_digest,
                )
                if (
                    override.last_test_status != "passed"
                    or override.tested_endpoint_policy_digest != endpoint.policy_digest
                    or override.tested_configuration_digest != expected_test_digest
                ):
                    raise PersonalModelGatewayUnavailable("agent_model_override_test_required")
            gateway = ModelGateway(provider=provider, model_id=effective_model_id)
            model_family, family_source = _model_family(effective_model_id, override)
            secret = ""
            return AlphaGatewaySelection(
                gateway=gateway,
                credential_source="personal",
                configuration_digest=_configuration_digest(
                    {
                        "credential_source": "personal",
                        "credential_id": str(row.id),
                        "credential_version": row.version,
                        "key_version": row.key_version,
                        "key_fingerprint": row.key_fingerprint,
                        "provider_id": row.provider_id,
                        "base_url": row.base_url,
                        "model_id": row.model_id,
                        "effective_model_id": effective_model_id,
                        "employee_role_id": employee_role_id,
                        "workspace_id": workspace_id,
                        "agent_version_id": agent_version_id,
                        "workspace_generation": scope.workspace_generation,
                        "workspace_agent_binding_id": scope.binding_id,
                        "agent_version_digest": scope.agent_version_digest,
                        "endpoint_policy_digest": endpoint.policy_digest,
                        "override_id": str(override.id) if override else None,
                        "override_version": override.version if override else None,
                        "override_credential_id": (
                            str(override.credential_id)
                            if override and override.credential_id
                            else None
                        ),
                        "model_family": model_family,
                        "family_source": family_source,
                    }
                ),
                credential_id=str(row.id),
                employee_role_id=employee_role_id,
                override_id=str(override.id) if override else None,
                override_version=override.version if override else None,
                model_family=model_family,
                family_source=family_source,
                workspace_generation=scope.workspace_generation,
                workspace_agent_binding_id=scope.binding_id,
                agent_version_digest=scope.agent_version_digest,
            )
        finally:
            session.close()

    def resolve_preferences(
        self,
        *,
        tenant_schema: str,
        actor_user_id: str,
    ) -> AlphaUserPreferences:
        from omnibase.db.tenant import UserProfile

        session = self._factory()
        session.info[TENANT_SCHEMA_SESSION_KEY] = tenant_schema
        session.info[TENANT_CONTEXT_REQUIRED_SESSION_KEY] = True
        try:
            profile = session.execute(
                select(UserProfile).where(UserProfile.user_id == actor_user_id)
            ).scalar_one_or_none()
            if profile is None:
                return AlphaUserPreferences(
                    assistant_name="Omni",
                    assistant_tone="balanced",
                    assistant_instructions="",
                )
            return AlphaUserPreferences(
                assistant_name=profile.assistant_name,
                assistant_tone=profile.assistant_tone,
                assistant_instructions=profile.assistant_instructions,
            )
        finally:
            session.close()


__all__ = [
    "PersonalModelGatewayUnavailable",
    "UserModelGatewayResolver",
]
