"""Unit tests for auth.security (JWT + password hashing).

Pure unit tests - no DB, no HTTP. Validates the cryptographic primitives
and token lifecycle.

Integration tests (full register/login flow) live in tests/integration/.
"""

from __future__ import annotations

from datetime import UTC, timedelta
from datetime import datetime as dt

import pytest

from omnibase.auth.security import (
    TokenExpired,
    TokenInvalid,
    create_token_pair,
    decode_access_token,
    decode_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)


class TestPasswordHashing:
    """hash_password / verify_password (bcrypt)."""

    def test_hash_and_verify_roundtrip(self) -> None:
        """A password verifies against its own hash."""
        password = "CorrectHorseBatteryStaple123"
        hashed = hash_password(password)
        assert hashed != password
        assert verify_password(password, hashed) is True

    def test_wrong_password_fails(self) -> None:
        """A different password fails verification."""
        hashed = hash_password("RightPassword123")
        assert verify_password("WrongPassword456", hashed) is False

    def test_empty_password_raises(self) -> None:
        """Empty plaintext is rejected (not silently hashed)."""
        with pytest.raises(ValueError):
            hash_password("")

    def test_verify_empty_returns_false(self) -> None:
        """Empty inputs return False rather than raising."""
        assert verify_password("", "somehash") is False
        assert verify_password("anything", "") is False

    def test_verify_malformed_hash_returns_false(self) -> None:
        """Malformed hash strings return False (no exception)."""
        assert verify_password("anything", "not-a-real-hash") is False

    def test_hashes_are_unique(self) -> None:
        """Each call produces a different hash (bcrypt salt)."""
        h1 = hash_password("SamePassword123")
        h2 = hash_password("SamePassword123")
        assert h1 != h2
        # Both should still verify
        assert verify_password("SamePassword123", h1) is True
        assert verify_password("SamePassword123", h2) is True


class TestTokenIssuanceAndVerification:
    """create_token_pair / decode_* happy path."""

    def test_create_pair_returns_two_distinct_tokens(self) -> None:
        """Issue returns (access, refresh, access_exp, refresh_exp)."""
        access, refresh, access_exp, refresh_exp = create_token_pair(
            user_id="user-1",
            tenant_id="tenant-1",
            schema_name="tenant_a1b2c3d4",
            email="alice@example.com",
        )
        assert access != refresh
        assert isinstance(access, str)
        assert isinstance(refresh, str)
        assert access_exp > dt.now(UTC)
        assert refresh_exp > access_exp  # refresh lives longer

    def test_decode_access_succeeds(self) -> None:
        """Decoding a valid access token returns the right claims."""
        access, _, _, _ = create_token_pair(
            user_id="user-abc",
            tenant_id="tenant-xyz",
            schema_name="tenant_a1b2c3d4",
            email="bob@example.com",
        )
        payload = decode_access_token(access)
        assert payload.sub == "user-abc"
        assert payload.tenant_id == "tenant-xyz"
        assert payload.schema_name == "tenant_a1b2c3d4"
        assert payload.email == "bob@example.com"
        assert payload.typ == "access"
        assert payload.jti  # non-empty

    def test_decode_refresh_succeeds(self) -> None:
        """Decoding a valid refresh token returns the right claims."""
        _, refresh, _, _ = create_token_pair(
            user_id="user-def",
            tenant_id="tenant-uvw",
            schema_name="tenant_a1b2c3d4",
            email="carol@example.com",
        )
        payload = decode_refresh_token(refresh)
        assert payload.sub == "user-def"
        assert payload.typ == "refresh"

    def test_decode_access_rejects_refresh_token(self) -> None:
        """decode_access_token rejects a refresh token (typ mismatch)."""
        _, refresh, _, _ = create_token_pair(
            user_id="u1",
            tenant_id="t1",
            schema_name="tenant_a1b2c3d4",
            email="d@example.com",
        )
        with pytest.raises(TokenInvalid, match="Expected access token"):
            decode_access_token(refresh)

    def test_decode_refresh_rejects_access_token(self) -> None:
        """decode_refresh_token rejects an access token (typ mismatch)."""
        access, _, _, _ = create_token_pair(
            user_id="u1",
            tenant_id="t1",
            schema_name="tenant_a1b2c3d4",
            email="e@example.com",
        )
        with pytest.raises(TokenInvalid, match="Expected refresh token"):
            decode_refresh_token(access)


class TestTokenFailureModes:
    """decode_token error paths."""

    def test_garbage_token_invalid(self) -> None:
        """A random string fails as invalid (not expired)."""
        with pytest.raises(TokenInvalid):
            decode_token("not.a.real.token")

    def test_wrong_secret_invalid(self) -> None:
        """A token signed with a different secret fails verification."""
        access, _, _, _ = create_token_pair(
            user_id="u1",
            tenant_id="t1",
            schema_name="tenant_a1b2c3d4",
            email="f@example.com",
        )
        # Tamper with a significant signature character. The final base64url
        # character can contain unused padding bits, so changing it may decode
        # to the same signature bytes.
        header, payload, signature = access.split(".")
        tampered_signature = ("A" if signature[0] != "A" else "B") + signature[1:]
        tampered = f"{header}.{payload}.{tampered_signature}"
        with pytest.raises((TokenInvalid, TokenExpired)):
            decode_token(tampered)

    def test_expired_token_raises_expired(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A token whose exp is in the past raises TokenExpired."""
        # Issue a normal token, then monkeypatch decode to use a far-future "now"
        _access, _, _, _ = create_token_pair(
            user_id="u1",
            tenant_id="t1",
            schema_name="tenant_a1b2c3d4",
            email="g@example.com",
        )
        # We can't easily make time travel; instead issue with negative expiry
        # by monkeypatching create_token_pair's timedelta. Simpler: use jwt directly.
        from jose import jwt

        from omnibase.core.config import get_settings

        settings = get_settings()
        past_exp = dt.now(UTC) - timedelta(seconds=10)
        expired_token = jwt.encode(
            {
                "sub": "u1",
                "exp": past_exp,
                "iat": past_exp - timedelta(minutes=1),
                "typ": "access",
                "tenant_id": "t1",
                "schema": "tenant_a1b2c3d4",
                "email": "h@example.com",
                "jti": "xyz",
            },
            settings.jwt_secret,
            algorithm=settings.jwt_algorithm,
        )
        with pytest.raises(TokenExpired):
            decode_token(expired_token)

    def test_tampered_claims_invalid(self) -> None:
        """Changing any claim after signing invalidates the token."""
        from jose import jwt

        from omnibase.core.config import get_settings

        settings = get_settings()
        # Sign with correct key but tamper the result
        original, _, _, _ = create_token_pair(
            user_id="u1",
            tenant_id="t1",
            schema_name="tenant_a1b2c3d4",
            email="i@example.com",
        )
        # Decode without verify to get the payload, then re-encode with a change
        unverified = jwt.decode(original, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        unverified["sub"] = "tampered-user-id"
        tampered = jwt.encode(unverified, settings.jwt_secret, algorithm=settings.jwt_algorithm)
        # Wait - we re-signed with the same key, so this WILL verify. Tamper AFTER:
        # Split into header.payload.signature and replace payload, keep old signature.
        header, payload, signature = original.split(".")
        import base64
        import json

        decoded_payload = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        decoded_payload["sub"] = "tampered-user-id"
        new_payload_b64 = (
            base64.urlsafe_b64encode(json.dumps(decoded_payload).encode()).decode().rstrip("=")
        )
        tampered = f"{header}.{new_payload_b64}.{signature}"
        with pytest.raises((TokenInvalid, TokenExpired)):
            decode_token(tampered)
