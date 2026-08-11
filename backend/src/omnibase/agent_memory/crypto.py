"""Server-owned authenticated encryption for persisted Memory content."""

from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from omnibase.core.config import Environment, Settings


class MemoryCryptoUnavailable(RuntimeError):
    """No valid server-owned Memory encryption key is configured."""


class MemoryDecryptionError(RuntimeError):
    """Stored Memory ciphertext, nonce or identity binding did not authenticate."""


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
        info=b"omnibase/development/memory-content/v1",
    ).derive(settings.jwt_secret.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class EncryptedMemoryContent:
    ciphertext: bytes
    nonce: bytes
    content_sha256: str
    key_version: int = 1


class MemoryContentCipher:
    """AES-256-GCM with immutable Candidate identity encoded as AAD."""

    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError("Memory encryption key must contain exactly 32 bytes")
        self._key = key

    @classmethod
    def from_settings(cls, settings: Settings) -> MemoryContentCipher:
        configured = settings.memory_content_encryption_key
        if configured:
            try:
                return cls(_decode_key(configured))
            except ValueError as exc:
                raise MemoryCryptoUnavailable("memory_content_key_invalid") from exc
        if settings.env is Environment.DEVELOPMENT:
            return cls(_development_key(settings))
        raise MemoryCryptoUnavailable("memory_content_key_missing")

    @staticmethod
    def aad(
        *,
        tenant_id: str,
        owner_user_id: str,
        workspace_id: str,
        agent_version_id: str,
        task_id: str,
        invocation_id: str,
        memory_policy_id: str,
        source_resource_id: str,
        source_resource_version: int,
        content_sha256: str,
        key_version: int,
    ) -> bytes:
        return "\x1f".join(
            (
                "omnibase-memory-content-v1",
                tenant_id,
                owner_user_id,
                workspace_id,
                agent_version_id,
                task_id,
                invocation_id,
                memory_policy_id,
                source_resource_id,
                str(source_resource_version),
                content_sha256,
                str(key_version),
            )
        ).encode("utf-8")

    def encrypt(self, content: str, *, aad: bytes, key_version: int = 1) -> EncryptedMemoryContent:
        plaintext = content.encode("utf-8")
        if not plaintext:
            raise ValueError("memory_content_empty")
        nonce = os.urandom(12)
        return EncryptedMemoryContent(
            ciphertext=AESGCM(self._key).encrypt(nonce, plaintext, aad),
            nonce=nonce,
            content_sha256=hashlib.sha256(plaintext).hexdigest(),
            key_version=key_version,
        )

    def decrypt(self, ciphertext: bytes, nonce: bytes, *, aad: bytes) -> bytes:
        try:
            return AESGCM(self._key).decrypt(nonce, ciphertext, aad)
        except (InvalidTag, ValueError) as exc:
            raise MemoryDecryptionError("memory_content_decryption_failed") from exc


__all__ = [
    "EncryptedMemoryContent",
    "MemoryContentCipher",
    "MemoryCryptoUnavailable",
    "MemoryDecryptionError",
]
