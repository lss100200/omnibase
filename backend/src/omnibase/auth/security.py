"""Security utilities: JWT tokens + password hashing.

Design:
- JWT signing: HS256 via python-jose (configurable algorithm via settings)
- Password hashing: bcrypt via passlib (industry standard, future-proofs argon2)
- Token types: access (short-lived) + refresh (long-lived, revocable via Redis)

Phase 0 scope:
- Issue access + refresh on login/register
- Verify access token signature (replaces the unsafe stub in tenants/dependencies.py)
- Refresh endpoint exchanges refresh token for new access token
- No email verification, no password reset (Phase 1+)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import bcrypt as _bcrypt
from jose import JWTError, jwt
from jose.exceptions import ExpiredSignatureError
from pydantic import BaseModel

from omnibase.core.config import Settings, get_settings
from omnibase.core.logging import get_logger

log = get_logger(__name__)


# -----------------------------------------------------------
# Password hashing (direct bcrypt, bypass passlib compatibility issues)
# -----------------------------------------------------------
# passlib 1.7.4 is incompatible with bcrypt 4.x (bcrypt removed __about__).
# We use bcrypt directly until passlib ships a fix.


def hash_password(plain: str) -> str:
    """Hash a password for storage. Returns a bcrypt hash string."""
    if not plain:
        raise ValueError("Password cannot be empty")
    # bcrypt has a 72-byte limit; truncate defensively (industry practice)
    password_bytes = plain.encode("utf-8")[:72]
    salt = _bcrypt.gensalt(rounds=12)
    return _bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    if not plain or not hashed:
        return False
    try:
        password_bytes = plain.encode("utf-8")[:72]
        hashed_bytes = hashed.encode("utf-8")
        return _bcrypt.checkpw(password_bytes, hashed_bytes)
    except Exception as exc:
        log.warning("password.verify_failed", error=str(exc))
        return False


# -----------------------------------------------------------
# JWT
# -----------------------------------------------------------
TokenType = Literal["access", "refresh"]


class TokenPayload(BaseModel):
    """Decoded JWT payload (typed view).

    Standard claims (RFC 7519):
    - sub: subject (user id)
    - exp: expiration time
    - iat: issued at
    - typ: token type ('access' | 'refresh')

    Custom claims:
    - tenant_id: tenant UUID (string)
    - schema_name: PostgreSQL schema name (e.g. tenant_abc123)
    - email:     user email (for audit logging)
    - jti:       token id (for revocation tracking via Redis)

    Note: field is named `schema_name` (not `schema`) to avoid shadowing
    Pydantic BaseModel's `.schema()` method.
    """

    sub: str
    exp: datetime
    iat: datetime
    typ: TokenType
    tenant_id: str
    schema_name: str
    email: str
    jti: str


class TokenError(Exception):
    """Base class for token-related errors."""


class TokenInvalid(TokenError):
    """Token failed signature verification or was malformed."""


class TokenExpired(TokenError):
    """Token is well-formed but past its expiration."""


class TokenRevoked(TokenError):
    """Token's jti is in the revocation set."""

    def __init__(self, jti: str) -> None:
        super().__init__(f"Token {jti} has been revoked")
        self.jti = jti


# -----------------------------------------------------------
# Token issuance
# -----------------------------------------------------------
def create_token_pair(
    *,
    user_id: str,
    tenant_id: str,
    schema_name: str,
    email: str,
    settings: Settings | None = None,
) -> tuple[str, str, datetime, datetime]:
    """Issue a fresh (access, refresh) token pair.

    Returns:
        (access_token, refresh_token, access_expires_at, refresh_expires_at)

    The caller is responsible for persisting the refresh token's jti in Redis
    if revocation is desired. Phase 0 keeps revocation optional.
    """
    settings = settings or get_settings()
    now = datetime.now(UTC)

    access_expires_at = now + timedelta(minutes=settings.access_token_expire_minutes)
    refresh_expires_at = now + timedelta(days=settings.refresh_token_expire_days)

    access_jti = str(uuid.uuid4())
    refresh_jti = str(uuid.uuid4())

    base_claims = {
        "iat": now,
        "tenant_id": tenant_id,
        "schema_name": schema_name,
        "email": email,
    }

    access_token = jwt.encode(
        {
            **base_claims,
            "sub": user_id,
            "exp": access_expires_at,
            "typ": "access",
            "jti": access_jti,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    refresh_token = jwt.encode(
        {
            **base_claims,
            "sub": user_id,
            "exp": refresh_expires_at,
            "typ": "refresh",
            "jti": refresh_jti,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )

    log.info(
        "token.pair_issued",
        user_id=user_id,
        tenant_id=tenant_id,
        access_jti=access_jti,
        refresh_jti=refresh_jti,
    )
    return access_token, refresh_token, access_expires_at, refresh_expires_at


# -----------------------------------------------------------
# Token verification
# -----------------------------------------------------------
def decode_token(token: str, settings: Settings | None = None) -> TokenPayload:
    """Decode and verify a JWT.

    Returns the typed payload on success.
    Raises TokenExpired if exp is past, TokenInvalid otherwise.
    """
    settings = settings or get_settings()

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except ExpiredSignatureError as exc:
        raise TokenExpired(str(exc)) from exc
    except JWTError as exc:
        raise TokenInvalid(str(exc)) from exc

    try:
        return TokenPayload.model_validate(payload)
    except Exception as exc:
        raise TokenInvalid(f"Malformed claims: {exc}") from exc


def decode_access_token(token: str, settings: Settings | None = None) -> TokenPayload:
    """Decode + verify an access token (enforces typ='access')."""
    payload = decode_token(token, settings)
    if payload.typ != "access":
        raise TokenInvalid(f"Expected access token, got {payload.typ!r}")
    return payload


def decode_refresh_token(token: str, settings: Settings | None = None) -> TokenPayload:
    """Decode + verify a refresh token (enforces typ='refresh')."""
    payload = decode_token(token, settings)
    if payload.typ != "refresh":
        raise TokenInvalid(f"Expected refresh token, got {payload.typ!r}")
    return payload


__all__ = [
    "TokenError",
    "TokenExpired",
    "TokenInvalid",
    "TokenPayload",
    "TokenRevoked",
    "TokenType",
    "create_token_pair",
    "decode_access_token",
    "decode_refresh_token",
    "decode_token",
    "hash_password",
    "verify_password",
]
