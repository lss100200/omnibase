"""Server-owned authenticated encryption for user provider credentials."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from omnibase.core.config import Environment, Settings


class CredentialCryptoUnavailable(RuntimeError):
    """No valid server-owned encryption key is configured."""


class CredentialDecryptionError(RuntimeError):
    """Stored ciphertext, nonce or AAD no longer authenticates."""


def _decode_key(value: str) -> bytes:
    candidate = value.strip()
    if not candidate:
        raise ValueError("empty")
    if len(candidate) == 64:
        try:
            decoded = bytes.fromhex(candidate)
        except ValueError:
            decoded = b""
        if len(decoded) == 32:
            return decoded
    try:
        decoded = base64.urlsafe_b64decode(candidate + "=" * (-len(candidate) % 4))
    except ValueError as exc:
        raise ValueError("invalid base64url") from exc
    if len(decoded) != 32:
        raise ValueError("decoded key must contain exactly 32 bytes")
    return decoded


def _development_key(settings: Settings) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=b"omnibase/development/provider-credential/v1",
    ).derive(settings.jwt_secret.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class EncryptedSecret:
    ciphertext: bytes
    nonce: bytes
    fingerprint: str
    key_version: int = 1


class CredentialCipher:
    """AES-256-GCM with identity-bound AAD and a keyed short fingerprint."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("credential encryption key must contain exactly 32 bytes")
        self._key = key
        self._fingerprint_key = HKDF(
            algorithm=hashes.SHA256(),
            length=32,
            salt=None,
            info=b"omnibase/provider-credential/fingerprint/v1",
        ).derive(key)

    @classmethod
    def from_settings(cls, settings: Settings) -> CredentialCipher:
        configured = settings.provider_credential_encryption_key
        if configured:
            try:
                return cls(_decode_key(configured))
            except ValueError as exc:
                raise CredentialCryptoUnavailable("provider_credential_key_invalid") from exc
        if settings.env is Environment.DEVELOPMENT:
            return cls(_development_key(settings))
        raise CredentialCryptoUnavailable("provider_credential_key_missing")

    @staticmethod
    def aad(
        *,
        tenant_id: str,
        user_id: str,
        credential_id: str,
        provider_id: str,
        key_version: int,
    ) -> bytes:
        return "\x1f".join(
            (tenant_id, user_id, credential_id, provider_id, str(key_version))
        ).encode("utf-8")

    def encrypt(self, secret: str, *, aad: bytes) -> EncryptedSecret:
        value = secret.strip()
        if not value:
            raise ValueError("provider_secret_empty")
        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, value.encode("utf-8"), aad)
        digest = hmac.new(self._fingerprint_key, value.encode("utf-8"), hashlib.sha256).hexdigest()
        return EncryptedSecret(
            ciphertext=ciphertext,
            nonce=nonce,
            fingerprint=f"{digest[:6]}…{digest[-6:]}",
        )

    def decrypt(self, ciphertext: bytes, nonce: bytes, *, aad: bytes) -> str:
        try:
            plaintext = AESGCM(self._key).decrypt(nonce, ciphertext, aad)
            return plaintext.decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            raise CredentialDecryptionError("provider_credential_decryption_failed") from exc


__all__ = [
    "CredentialCipher",
    "CredentialCryptoUnavailable",
    "CredentialDecryptionError",
    "EncryptedSecret",
]
