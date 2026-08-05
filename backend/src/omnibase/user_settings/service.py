"""Tenant-scoped profile and encrypted provider-credential lifecycle."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from omnibase.control_plane.models import AuditEvent
from omnibase.core.config import Settings
from omnibase.db.tenant import ModelProviderCredential, User, UserProfile
from omnibase.user_settings.crypto import CredentialCipher
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


class UserSettingsError(RuntimeError):
    code = "user_settings_error"
    status = 409

    def __init__(self, code: str | None = None, *, status: int | None = None) -> None:
        self.code = code or self.code
        self.status = status or self.status
        super().__init__(self.code)


class UserSettingsNotFound(UserSettingsError):
    code = "not_found"
    status = 404


class UserSettingsUnavailable(UserSettingsError):
    code = "provider_credentials_unavailable"
    status = 503


def validate_provider_base_url(
    base_url: str,
    *,
    allowed_hosts: tuple[str, ...],
    resolve_dns: bool,
) -> str:
    """Normalize an HTTPS OpenAI-compatible base URL and reject SSRF pivots."""
    candidate = base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise UserSettingsError("provider_base_url_invalid", status=422) from exc
    host = (parsed.hostname or "").lower().rstrip(".")
    if (
        parsed.scheme != "https"
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (port is not None and port != 443)
    ):
        raise UserSettingsError("provider_base_url_invalid", status=422)
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UserSettingsError("provider_base_url_ip_literal_forbidden", status=422)
    allowlist = {item.lower().rstrip(".") for item in allowed_hosts}
    if host not in allowlist:
        raise UserSettingsError("provider_host_not_allowed", status=422)
    if resolve_dns:
        try:
            answers = socket.getaddrinfo(host, port or 443, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise UserSettingsError("provider_host_unreachable", status=422) from exc
        addresses = {item[4][0] for item in answers}
        if not addresses:
            raise UserSettingsError("provider_host_unreachable", status=422)
        for address in addresses:
            try:
                parsed_address = ipaddress.ip_address(address)
            except ValueError as exc:
                raise UserSettingsError("provider_dns_address_invalid", status=422) from exc
            if not parsed_address.is_global:
                raise UserSettingsError("provider_dns_private_address_forbidden", status=422)
    return candidate


def _digest(payload: object) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class UserSettingsService:
    def __init__(self, *, settings: Settings) -> None:
        self._settings = settings

    def _cipher(self) -> CredentialCipher:
        try:
            return CredentialCipher.from_settings(self._settings)
        except RuntimeError as exc:
            raise UserSettingsUnavailable(str(exc), status=503) from exc

    @staticmethod
    def _live_user(session: Session, user_id: str, *, lock: bool) -> User:
        statement = select(User).where(User.id == user_id, User.is_active.is_(True))
        if lock:
            statement = statement.with_for_update()
        user = session.execute(statement).scalar_one_or_none()
        if user is None:
            raise UserSettingsNotFound("user_not_found")
        return user

    @staticmethod
    def _profile_default(user: User) -> UserProfileRead:
        display_name = user.email.split("@", 1)[0] or "OmniBase User"
        return UserProfileRead(
            user_id=str(user.id),
            display_name=display_name,
            locale="zh-CN",
            theme="system",
            assistant_name="Omni",
            assistant_tone="balanced",
            assistant_instructions="",
            version=0,
        )

    def get_profile(self, session: Session, *, user_id: str) -> UserProfileRead:
        user = self._live_user(session, user_id, lock=False)
        profile = session.get(UserProfile, user_id)
        if profile is None:
            return self._profile_default(user)
        return UserProfileRead.model_validate(profile)

    def update_profile(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        request_id: str,
        payload: UserProfileUpdate,
    ) -> UserProfileRead:
        self._live_user(session, user_id, lock=True)
        profile = session.execute(
            select(UserProfile).where(UserProfile.user_id == user_id).with_for_update()
        ).scalar_one_or_none()
        before_version = profile.version if profile is not None else None
        if profile is None:
            if payload.expected_version != 0:
                raise UserSettingsError("profile_version_conflict")
            profile = UserProfile(user_id=user_id, version=1)
            session.add(profile)
        else:
            if profile.version != payload.expected_version:
                raise UserSettingsError("profile_version_conflict")
            profile.version += 1
        profile.display_name = payload.display_name.strip()
        profile.locale = payload.locale
        profile.theme = payload.theme
        profile.assistant_name = payload.assistant_name.strip()
        profile.assistant_tone = payload.assistant_tone
        profile.assistant_instructions = payload.assistant_instructions
        profile.updated_at = datetime.now(UTC)
        session.flush()
        self._audit(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=request_id,
            action="user.profile.update",
            before_version=before_version,
            after_version=profile.version,
            input_hash=_digest({"user_id": user_id, "version": profile.version}),
            details={},
        )
        return UserProfileRead.model_validate(profile)

    @staticmethod
    def _credential_read(row: ModelProviderCredential) -> ProviderCredentialRead:
        return ProviderCredentialRead(
            id=str(row.id),
            display_name=row.display_name,
            provider_id=row.provider_id,
            base_url=row.base_url,
            model_id=row.model_id,
            key_fingerprint=row.key_fingerprint,
            secret_configured=bool(row.encrypted_api_key and row.key_nonce),
            is_default=row.is_default,
            is_active=row.is_active,
            version=row.version,
            last_test_status=row.last_test_status,  # type: ignore[arg-type]
            last_test_latency_ms=row.last_test_latency_ms,
            last_tested_at=row.last_tested_at,
            revoked_at=row.revoked_at,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def list_credentials(self, session: Session, *, user_id: str) -> ProviderCredentialList:
        self._live_user(session, user_id, lock=False)
        rows = session.execute(
            select(ModelProviderCredential)
            .where(ModelProviderCredential.user_id == user_id)
            .order_by(ModelProviderCredential.created_at.desc())
        ).scalars()
        items = [self._credential_read(row) for row in rows]
        return ProviderCredentialList(
            items=items,
            total=len(items),
            operator_fallback_available=bool(self._settings.llm_api_key),
        )

    @staticmethod
    def _lock_user_credentials(session: Session, user_id: str) -> list[ModelProviderCredential]:
        return list(
            session.execute(
                select(ModelProviderCredential)
                .where(ModelProviderCredential.user_id == user_id)
                .order_by(ModelProviderCredential.id)
                .with_for_update()
            ).scalars()
        )

    @staticmethod
    def _owned_credential(
        session: Session,
        *,
        user_id: str,
        credential_id: str,
        lock: bool,
    ) -> ModelProviderCredential:
        statement = select(ModelProviderCredential).where(
            ModelProviderCredential.id == credential_id,
            ModelProviderCredential.user_id == user_id,
        )
        if lock:
            statement = statement.with_for_update()
        row = session.execute(statement).scalar_one_or_none()
        if row is None:
            raise UserSettingsNotFound("provider_credential_not_found")
        return row

    def create_credential(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        request_id: str,
        payload: ProviderCredentialCreate,
    ) -> ProviderCredentialRead:
        self._live_user(session, user_id, lock=True)
        rows = self._lock_user_credentials(session, user_id)
        base_url = validate_provider_base_url(
            payload.base_url,
            allowed_hosts=self._settings.provider_endpoint_allowlist,
            resolve_dns=False,
        )
        credential_id = str(uuid4())
        key_version = 1
        aad = CredentialCipher.aad(
            tenant_id=tenant_id,
            user_id=user_id,
            credential_id=credential_id,
            provider_id=payload.provider_id,
            key_version=key_version,
        )
        encrypted = self._cipher().encrypt(payload.api_key.get_secret_value(), aad=aad)
        if payload.is_default:
            for current in rows:
                current.is_default = False
                current.version += 1
                current.updated_at = datetime.now(UTC)
        row = ModelProviderCredential(
            id=credential_id,
            user_id=user_id,
            display_name=payload.display_name.strip(),
            provider_id=payload.provider_id,
            base_url=base_url,
            model_id=payload.model_id.strip(),
            encrypted_api_key=encrypted.ciphertext,
            key_nonce=encrypted.nonce,
            key_version=key_version,
            key_fingerprint=encrypted.fingerprint,
            is_default=payload.is_default or not any(item.is_active for item in rows),
            is_active=True,
            version=1,
        )
        session.add(row)
        session.flush()
        self._audit(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=request_id,
            action="model_provider_credential.create",
            before_version=None,
            after_version=1,
            input_hash=_digest(
                {
                    "credential_id": credential_id,
                    "provider_id": row.provider_id,
                    "model_id": row.model_id,
                }
            ),
            details={"credential_id": credential_id, "provider_id": row.provider_id},
        )
        return self._credential_read(row)

    def update_credential(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        credential_id: str,
        request_id: str,
        payload: ProviderCredentialUpdate,
    ) -> ProviderCredentialRead:
        self._live_user(session, user_id, lock=True)
        rows = self._lock_user_credentials(session, user_id)
        row = next((item for item in rows if str(item.id) == credential_id), None)
        if row is None:
            raise UserSettingsNotFound("provider_credential_not_found")
        if row.version != payload.expected_version or not row.is_active:
            raise UserSettingsError("provider_credential_version_conflict")
        before_version = row.version
        next_provider_id = payload.provider_id or row.provider_id
        if next_provider_id != row.provider_id and row.encrypted_api_key is not None:
            raise UserSettingsError("provider_id_change_requires_secret_rotation")
        if payload.display_name is not None:
            row.display_name = payload.display_name.strip()
        if payload.provider_id is not None:
            row.provider_id = payload.provider_id
        if payload.base_url is not None:
            row.base_url = validate_provider_base_url(
                payload.base_url,
                allowed_hosts=self._settings.provider_endpoint_allowlist,
                resolve_dns=False,
            )
        if payload.model_id is not None:
            row.model_id = payload.model_id.strip()
        if payload.is_default is True:
            for current in rows:
                current.is_default = current is row
        elif payload.is_default is False:
            row.is_default = False
        row.version += 1
        row.last_test_status = None
        row.last_test_latency_ms = None
        row.last_tested_at = None
        row.updated_at = datetime.now(UTC)
        session.flush()
        self._audit(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=request_id,
            action="model_provider_credential.update",
            before_version=before_version,
            after_version=row.version,
            input_hash=_digest({"credential_id": credential_id, "version": row.version}),
            details={"credential_id": credential_id, "provider_id": row.provider_id},
        )
        return self._credential_read(row)

    def replace_secret(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        credential_id: str,
        request_id: str,
        payload: ProviderCredentialSecretUpdate,
    ) -> ProviderCredentialRead:
        self._live_user(session, user_id, lock=True)
        row = self._owned_credential(
            session, user_id=user_id, credential_id=credential_id, lock=True
        )
        if row.version != payload.expected_version or not row.is_active:
            raise UserSettingsError("provider_credential_version_conflict")
        before_version = row.version
        row.key_version += 1
        aad = CredentialCipher.aad(
            tenant_id=tenant_id,
            user_id=user_id,
            credential_id=credential_id,
            provider_id=row.provider_id,
            key_version=row.key_version,
        )
        encrypted = self._cipher().encrypt(payload.api_key.get_secret_value(), aad=aad)
        row.encrypted_api_key = encrypted.ciphertext
        row.key_nonce = encrypted.nonce
        row.key_fingerprint = encrypted.fingerprint
        row.version += 1
        row.last_test_status = None
        row.last_test_latency_ms = None
        row.last_tested_at = None
        row.updated_at = datetime.now(UTC)
        session.flush()
        self._audit(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=request_id,
            action="model_provider_credential.secret.rotate",
            before_version=before_version,
            after_version=row.version,
            input_hash=_digest({"credential_id": credential_id, "key_version": row.key_version}),
            details={"credential_id": credential_id, "provider_id": row.provider_id},
        )
        return self._credential_read(row)

    def activate(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        credential_id: str,
        request_id: str,
        payload: ProviderCredentialActivate,
    ) -> ProviderCredentialRead:
        self._live_user(session, user_id, lock=True)
        rows = self._lock_user_credentials(session, user_id)
        row = next((item for item in rows if str(item.id) == credential_id), None)
        if row is None:
            raise UserSettingsNotFound("provider_credential_not_found")
        if row.version != payload.expected_version or row.revoked_at is not None:
            raise UserSettingsError("provider_credential_version_conflict")
        if not row.encrypted_api_key or not row.key_nonce:
            raise UserSettingsError("provider_credential_secret_missing")
        before_version = row.version
        row.is_active = True
        if payload.make_default:
            for current in rows:
                current.is_default = current is row
        row.version += 1
        row.updated_at = datetime.now(UTC)
        session.flush()
        self._audit(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=request_id,
            action="model_provider_credential.activate",
            before_version=before_version,
            after_version=row.version,
            input_hash=_digest({"credential_id": credential_id, "default": row.is_default}),
            details={"credential_id": credential_id, "provider_id": row.provider_id},
        )
        return self._credential_read(row)

    def revoke(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        credential_id: str,
        request_id: str,
    ) -> None:
        self._live_user(session, user_id, lock=True)
        row = self._owned_credential(
            session, user_id=user_id, credential_id=credential_id, lock=True
        )
        if row.revoked_at is not None:
            return
        before_version = row.version
        row.is_active = False
        row.is_default = False
        row.encrypted_api_key = None
        row.key_nonce = None
        row.version += 1
        row.revoked_at = datetime.now(UTC)
        row.updated_at = row.revoked_at
        session.flush()
        self._audit(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=request_id,
            action="model_provider_credential.revoke",
            before_version=before_version,
            after_version=row.version,
            input_hash=_digest({"credential_id": credential_id, "revoked": True}),
            details={"credential_id": credential_id, "provider_id": row.provider_id},
        )

    def _decrypt(self, row: ModelProviderCredential, *, tenant_id: str, user_id: str) -> str:
        if not row.encrypted_api_key or not row.key_nonce:
            raise UserSettingsError("provider_credential_secret_missing")
        aad = CredentialCipher.aad(
            tenant_id=tenant_id,
            user_id=user_id,
            credential_id=str(row.id),
            provider_id=row.provider_id,
            key_version=row.key_version,
        )
        try:
            return self._cipher().decrypt(row.encrypted_api_key, row.key_nonce, aad=aad)
        except RuntimeError as exc:
            raise UserSettingsUnavailable("provider_credential_decryption_failed") from exc

    def test_credential(
        self,
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        credential_id: str,
        request_id: str,
    ) -> ProviderTestResult:
        self._live_user(session, user_id, lock=False)
        row = self._owned_credential(
            session, user_id=user_id, credential_id=credential_id, lock=False
        )
        if not row.is_active or row.revoked_at is not None:
            raise UserSettingsError("provider_credential_inactive")
        base_url = validate_provider_base_url(
            row.base_url,
            allowed_hosts=self._settings.provider_endpoint_allowlist,
            resolve_dns=True,
        )
        secret = self._decrypt(row, tenant_id=tenant_id, user_id=user_id)
        requested_model_id = row.model_id
        tested_configuration = {
            "version": row.version,
            "provider_id": row.provider_id,
            "base_url": row.base_url,
            "model_id": row.model_id,
            "key_version": row.key_version,
            "key_fingerprint": row.key_fingerprint,
            "is_active": row.is_active,
            "revoked_at": row.revoked_at.isoformat() if row.revoked_at is not None else None,
        }
        tested_configuration_digest = _digest(tested_configuration)

        # Release the tenant DB connection before the bounded external call.
        # The result is accepted only in a new transaction after re-locking and
        # comparing the exact non-secret configuration snapshot.
        session.commit()
        started = time.monotonic()
        status_value = "failed"
        actual_model_id: str | None = None
        try:
            with httpx.Client(timeout=10.0, follow_redirects=False, trust_env=False) as client:
                response = client.post(
                    f"{base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {secret}"},
                    json={
                        "model": requested_model_id,
                        "messages": [{"role": "user", "content": "Reply with OK."}],
                        "temperature": 0,
                        "max_tokens": 8,
                        "stream": False,
                    },
                )
            if response.status_code in {401, 403}:
                status_value = "auth_failed"
            elif 300 <= response.status_code < 400:
                status_value = "failed"
            elif response.is_success:
                try:
                    body = response.json()
                    actual_model_id = str(body.get("model") or "")
                except (ValueError, AttributeError):
                    status_value = "failed"
                else:
                    status_value = (
                        "passed" if actual_model_id == requested_model_id else "identity_mismatch"
                    )
            else:
                status_value = "failed"
        except httpx.TimeoutException:
            status_value = "timeout"
        except httpx.RequestError:
            status_value = "unreachable"
        finally:
            secret = ""
        latency_ms = max(0, int((time.monotonic() - started) * 1000))

        self._live_user(session, user_id, lock=True)
        locked = self._owned_credential(
            session, user_id=user_id, credential_id=credential_id, lock=True
        )
        locked_configuration_digest = _digest(
            {
                "version": locked.version,
                "provider_id": locked.provider_id,
                "base_url": locked.base_url,
                "model_id": locked.model_id,
                "key_version": locked.key_version,
                "key_fingerprint": locked.key_fingerprint,
                "is_active": locked.is_active,
                "revoked_at": (
                    locked.revoked_at.isoformat() if locked.revoked_at is not None else None
                ),
            }
        )
        if locked_configuration_digest != tested_configuration_digest:
            raise UserSettingsError("provider_credential_changed_during_test")
        locked.last_test_status = status_value
        locked.last_test_latency_ms = latency_ms
        locked.last_tested_at = datetime.now(UTC)
        locked.updated_at = locked.last_tested_at
        session.flush()
        self._audit(
            session,
            tenant_id=tenant_id,
            user_id=user_id,
            request_id=request_id,
            action="model_provider_credential.test",
            before_version=locked.version,
            after_version=locked.version,
            input_hash=_digest({"credential_id": credential_id, "model_id": locked.model_id}),
            details={
                "credential_id": credential_id,
                "provider_id": locked.provider_id,
                "test_status": status_value,
            },
        )
        return ProviderTestResult(
            status=status_value,  # type: ignore[arg-type]
            latency_ms=latency_ms,
            requested_model_id=requested_model_id,
            actual_model_id=actual_model_id or None,
        )

    def runtime_posture(self, session: Session, *, user_id: str) -> ProviderRuntimePosture:
        self._live_user(session, user_id, lock=False)
        row = session.execute(
            select(ModelProviderCredential)
            .where(
                ModelProviderCredential.user_id == user_id,
                ModelProviderCredential.is_active.is_(True),
                ModelProviderCredential.is_default.is_(True),
                ModelProviderCredential.revoked_at.is_(None),
            )
            .order_by(ModelProviderCredential.created_at.desc())
        ).scalar_one_or_none()
        if row is not None:
            return ProviderRuntimePosture(
                credential_source="personal",
                provider_id=row.provider_id,
                model_id=row.model_id,
                credential_id=str(row.id),
            )
        if self._settings.llm_api_key:
            return ProviderRuntimePosture(
                credential_source="operator_default",
                provider_id=self._settings.llm_provider,
                model_id=self._settings.llm_model,
                credential_id=None,
            )
        return ProviderRuntimePosture(
            credential_source="unavailable",
            provider_id=None,
            model_id=None,
            credential_id=None,
        )

    @staticmethod
    def _audit(
        session: Session,
        *,
        tenant_id: str,
        user_id: str,
        request_id: str,
        action: str,
        before_version: int | None,
        after_version: int | None,
        input_hash: str,
        details: dict[str, object],
    ) -> None:
        session.add(
            AuditEvent(
                tenant_id=tenant_id,
                request_id=request_id,
                actor_type="user",
                actor_id=user_id,
                action=action,
                decision="allowed",
                risk_level="R1",
                input_hash=input_hash,
                before_version=before_version,
                after_version=after_version,
                status_code=200,
                details=details,
            )
        )


__all__ = [
    "UserSettingsError",
    "UserSettingsNotFound",
    "UserSettingsService",
    "UserSettingsUnavailable",
    "validate_provider_base_url",
]
