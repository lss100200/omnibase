"""Focused security and contract tests for real user/provider settings."""

from __future__ import annotations

import base64
import socket

import pytest

from omnibase.core.config import Settings
from omnibase.user_settings.crypto import (
    CredentialCipher,
    CredentialCryptoUnavailable,
    CredentialDecryptionError,
)
from omnibase.user_settings.schemas import ProviderCredentialCreate, ProviderCredentialRead
from omnibase.user_settings.service import UserSettingsError, _digest, validate_provider_base_url


def _settings(env: str = "development", *, encryption_key: str = "") -> Settings:
    return Settings(
        env=env,
        database_url="postgresql+psycopg://u:p@localhost:5432/db",
        minio_endpoint="localhost:9000",
        minio_access_key="k",
        minio_secret_key="s",  # noqa: S106 - synthetic non-secret test value
        redis_url="redis://localhost:6379/0",
        jwt_secret="x" * 40,
        provider_credential_encryption_key=encryption_key,
    )


def test_aes_gcm_round_trip_and_keyed_fingerprint() -> None:
    key = base64.urlsafe_b64encode(b"k" * 32).decode().rstrip("=")
    cipher = CredentialCipher.from_settings(_settings(encryption_key=key))
    aad = CredentialCipher.aad(
        tenant_id="tenant",
        user_id="user",
        credential_id="credential",
        provider_id="deepseek",
        key_version=1,
    )
    encrypted = cipher.encrypt("secret-api-key", aad=aad)
    assert encrypted.ciphertext != b"secret-api-key"
    assert "secret-api-key" not in encrypted.fingerprint
    assert cipher.decrypt(encrypted.ciphertext, encrypted.nonce, aad=aad) == "secret-api-key"


def test_ciphertext_or_aad_tamper_fails_closed() -> None:
    cipher = CredentialCipher(b"k" * 32)
    aad = b"bound-aad"
    encrypted = cipher.encrypt("secret", aad=aad)
    tampered = encrypted.ciphertext[:-1] + bytes([encrypted.ciphertext[-1] ^ 1])
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(tampered, encrypted.nonce, aad=aad)
    with pytest.raises(CredentialDecryptionError):
        cipher.decrypt(encrypted.ciphertext, encrypted.nonce, aad=b"other-aad")


def test_production_and_staging_require_independent_encryption_key() -> None:
    for env in ("production", "staging"):
        with pytest.raises(CredentialCryptoUnavailable, match="key_missing"):
            CredentialCipher.from_settings(_settings(env))


def test_development_has_explicit_deterministic_fallback() -> None:
    first = CredentialCipher.from_settings(_settings())
    second = CredentialCipher.from_settings(_settings())
    aad = b"aad"
    encrypted = first.encrypt("secret", aad=aad)
    assert second.decrypt(encrypted.ciphertext, encrypted.nonce, aad=aad) == "secret"


@pytest.mark.parametrize(
    "url,code",
    [
        ("http://api.deepseek.com/v1", "provider_base_url_invalid"),
        ("https://user@api.deepseek.com/v1", "provider_base_url_invalid"),
        ("https://api.deepseek.com/v1?token=x", "provider_base_url_invalid"),
        ("https://127.0.0.1/v1", "provider_base_url_ip_literal_forbidden"),
        ("https://example.com/v1", "provider_host_not_allowed"),
    ],
)
def test_provider_url_structural_ssrf_rejections(url: str, code: str) -> None:
    with pytest.raises(UserSettingsError, match=code):
        validate_provider_base_url(
            url,
            allowed_hosts=("api.deepseek.com",),
            resolve_dns=False,
        )


def test_provider_url_dns_private_address_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(UserSettingsError, match="private_address"):
        validate_provider_base_url(
            "https://api.deepseek.com/v1",
            allowed_hosts=("api.deepseek.com",),
            resolve_dns=True,
        )


def test_provider_url_public_address_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 443))],
    )
    assert (
        validate_provider_base_url(
            "https://api.deepseek.com/v1/",
            allowed_hosts=("api.deepseek.com",),
            resolve_dns=True,
        )
        == "https://api.deepseek.com/v1"
    )


def test_browser_read_contract_has_no_secret_or_ciphertext_fields() -> None:
    assert "api_key" not in ProviderCredentialRead.model_fields
    assert "encrypted_api_key" not in ProviderCredentialRead.model_fields
    assert "key_nonce" not in ProviderCredentialRead.model_fields
    payload = ProviderCredentialCreate(
        display_name="DeepSeek",
        provider_id="deepseek",
        base_url="https://api.deepseek.com/v1",
        model_id="deepseek-v4-flash",
        api_key="secret-value",
    )
    assert payload.api_key.get_secret_value() == "secret-value"
    assert payload.model_dump()["api_key"] != "secret-value"


def test_provider_test_configuration_digest_detects_key_or_model_drift() -> None:
    baseline = {
        "version": 3,
        "provider_id": "deepseek",
        "base_url": "https://api.deepseek.com/v1",
        "model_id": "deepseek-v4-flash",
        "key_version": 2,
        "key_fingerprint": "abc123",
        "is_active": True,
        "revoked_at": None,
    }
    rotated = dict(baseline, version=4, key_version=3, key_fingerprint="def456")
    changed_model = dict(baseline, version=4, model_id="deepseek-v4")

    assert _digest(baseline) != _digest(rotated)
    assert _digest(baseline) != _digest(changed_model)
