"""Request-scoped personal Model Gateway selection for Agent Alpha."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from omnibase.agent_alpha.contracts import AlphaGatewaySelection, AlphaUserPreferences
from omnibase.core.config import Settings
from omnibase.core.db import (
    TENANT_CONTEXT_REQUIRED_SESSION_KEY,
    TENANT_SCHEMA_SESSION_KEY,
)
from omnibase.db.tenant import ModelProviderCredential, User
from omnibase.model_gateway import ModelGateway, UnavailableModelGateway
from omnibase.model_gateway.providers import OpenAICompatibleProvider
from omnibase.user_settings.crypto import CredentialCipher


class PersonalModelGatewayUnavailable(RuntimeError):
    """A configured personal credential cannot be safely used."""


def _configuration_digest(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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

    def resolve(
        self,
        *,
        tenant_id: str,
        tenant_schema: str,
        actor_user_id: str,
    ) -> AlphaGatewaySelection:
        session = self._factory()
        session.info[TENANT_SCHEMA_SESSION_KEY] = tenant_schema
        session.info[TENANT_CONTEXT_REQUIRED_SESSION_KEY] = True
        try:
            user = session.execute(
                select(User).where(User.id == actor_user_id, User.is_active.is_(True))
            ).scalar_one_or_none()
            if user is None:
                raise PersonalModelGatewayUnavailable("personal_model_gateway_user_inactive")
            row = session.execute(
                select(ModelProviderCredential)
                .where(
                    ModelProviderCredential.user_id == actor_user_id,
                    ModelProviderCredential.is_active.is_(True),
                    ModelProviderCredential.is_default.is_(True),
                    ModelProviderCredential.revoked_at.is_(None),
                )
                .order_by(ModelProviderCredential.created_at.desc())
            ).scalar_one_or_none()
            if row is None:
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
            provider = OpenAICompatibleProvider(
                provider_id=row.provider_id,
                api_key=secret,
                base_url=row.base_url,
            )
            gateway = ModelGateway(provider=provider, model_id=row.model_id)
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
                    }
                ),
                credential_id=str(row.id),
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
