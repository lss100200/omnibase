"""Fixed-algorithm P34.2 capability token primitives.

Only server-side registered PEM keys are accepted.  The decoder intentionally
does not implement JWKS, ``jku``, ``x5u``, embedded ``jwk``, or any network key
discovery mechanism.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import JWTError, jwt

ALGORITHM = "RS256"
TOKEN_TYPE = "omnibase-capability+jwt"
ISSUER = "omnibase-capability-issuer"
AUDIENCE = "omnibase-capability-gateway"
MAX_TOKEN_TTL = timedelta(minutes=5)
_FORBIDDEN_KEY_HEADERS = frozenset({"jku", "x5u", "jwk", "x5c"})
_THUMBPRINT_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
JTI_PATTERN = re.compile(r"^[A-Za-z0-9._-]{16,128}$")
_REQUIRED_CLAIMS = frozenset(
    {
        "iss",
        "aud",
        "jti",
        "sub",
        "tenant_id",
        "workspace_id",
        "actor_user_id",
        "grant_id",
        "grant_version",
        "delegation_depth",
        "cnf",
        "iat",
        "nbf",
        "exp",
    }
)
_ALLOWED_CLAIMS = _REQUIRED_CLAIMS | frozenset({"approval_id"})


class CapabilityTokenError(Exception):
    """A token is malformed, untrusted, expired, or cryptographically invalid."""


@dataclass(frozen=True)
class CapabilityTokenClaims:
    """Strict, normalized claims accepted by the capability verifier."""

    jti: str
    subject: str
    tenant_id: str
    workspace_id: str
    actor_user_id: str
    grant_id: str
    grant_version: int
    delegation_depth: int
    workload_thumbprint: str
    issued_at: int
    not_before: int
    expires_at: int
    approval_id: str | None


def public_key_fingerprint(public_key_pem: str | bytes) -> str:
    """Return SHA-256 of canonical DER SubjectPublicKeyInfo bytes."""

    raw = public_key_pem.encode("ascii") if isinstance(public_key_pem, str) else public_key_pem
    try:
        key = serialization.load_pem_public_key(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("public_key_pem must contain one PEM public key") from exc
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size < 2048:
        raise ValueError("capability keys must be RSA public keys of at least 2048 bits")
    der = key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def private_key_fingerprint(private_key_pem: str | bytes) -> str:
    """Return the corresponding public-key fingerprint without exposing key data."""

    raw = private_key_pem.encode("ascii") if isinstance(private_key_pem, str) else private_key_pem
    try:
        key = serialization.load_pem_private_key(raw, password=None)
    except (TypeError, ValueError) as exc:
        raise ValueError("private signing key is invalid or encrypted") from exc
    if not isinstance(key, rsa.RSAPrivateKey) or key.key_size < 2048:
        raise ValueError("capability signing keys must be RSA keys of at least 2048 bits")
    public_der = key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(public_der).hexdigest()


def encode_capability_token(
    *,
    private_key_pem: str | bytes,
    kid: str,
    jti: str,
    subject: str,
    tenant_id: str,
    workspace_id: str,
    actor_user_id: str,
    grant_id: str,
    grant_version: int,
    delegation_depth: int,
    workload_thumbprint: str,
    issued_at: datetime,
    expires_at: datetime,
    approval_id: str | None,
) -> str:
    """Encode a short-lived, proof-of-possession-bound capability token."""

    issued_at = _aware(issued_at)
    expires_at = _aware(expires_at)
    if not JTI_PATTERN.fullmatch(jti):
        raise ValueError("capability token jti has an invalid format")
    if not _THUMBPRINT_PATTERN.fullmatch(workload_thumbprint):
        raise ValueError("workload thumbprint must be unpadded base64url SHA-256")
    if expires_at <= issued_at or expires_at - issued_at > MAX_TOKEN_TTL:
        raise ValueError("capability token lifetime must be positive and at most five minutes")
    issued = int(issued_at.timestamp())
    payload: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "jti": jti,
        "sub": subject,
        "tenant_id": tenant_id,
        "workspace_id": workspace_id,
        "actor_user_id": actor_user_id,
        "grant_id": grant_id,
        "grant_version": grant_version,
        "delegation_depth": delegation_depth,
        "cnf": {"x5t#S256": workload_thumbprint},
        "iat": issued,
        "nbf": issued,
        "exp": int(expires_at.timestamp()),
    }
    if approval_id is not None:
        payload["approval_id"] = approval_id
    return jwt.encode(
        payload,
        private_key_pem,
        algorithm=ALGORITHM,
        headers={"alg": ALGORITHM, "kid": kid, "typ": TOKEN_TYPE},
    )


def get_trusted_kid(token: str) -> str:
    """Validate the protected header and return only a local-registry key id."""

    try:
        header = jwt.get_unverified_header(token)
    except JWTError as exc:
        raise CapabilityTokenError("invalid capability") from exc
    if not isinstance(header, dict):
        raise CapabilityTokenError("invalid capability")
    if _FORBIDDEN_KEY_HEADERS.intersection(header):
        raise CapabilityTokenError("invalid capability")
    if set(header) != {"alg", "kid", "typ"}:
        raise CapabilityTokenError("invalid capability")
    if header.get("alg") != ALGORITHM or header.get("typ") != TOKEN_TYPE:
        raise CapabilityTokenError("invalid capability")
    kid = header.get("kid")
    if not isinstance(kid, str) or not 8 <= len(kid) <= 64:
        raise CapabilityTokenError("invalid capability")
    return kid


def decode_capability_token(*, token: str, public_key_pem: str) -> CapabilityTokenClaims:
    """Cryptographically decode a token under the fixed capability contract."""

    try:
        payload = jwt.decode(
            token,
            public_key_pem,
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            options={
                "require_aud": True,
                "require_exp": True,
                "require_iat": True,
                "require_iss": True,
                "require_nbf": True,
                "require_sub": True,
                "verify_aud": True,
                "verify_exp": True,
                "verify_iat": True,
                "verify_iss": True,
                "verify_nbf": True,
                "verify_signature": True,
                "verify_sub": True,
            },
        )
    except JWTError as exc:
        raise CapabilityTokenError("invalid capability") from exc
    return _normalize_payload(payload)


def _normalize_payload(payload: object) -> CapabilityTokenClaims:
    """Reject claim type confusion and normalize the closed claim set."""

    if not isinstance(payload, dict):
        raise CapabilityTokenError("invalid capability")
    _validate_claim_shape(payload)
    cnf = payload["cnf"]
    assert isinstance(cnf, dict)
    thumbprint = cnf["x5t#S256"]
    assert isinstance(thumbprint, str)
    approval_id = payload.get("approval_id")
    assert approval_id is None or isinstance(approval_id, str)
    return CapabilityTokenClaims(
        jti=payload["jti"],
        subject=payload["sub"],
        tenant_id=payload["tenant_id"],
        workspace_id=payload["workspace_id"],
        actor_user_id=payload["actor_user_id"],
        grant_id=payload["grant_id"],
        grant_version=payload["grant_version"],
        delegation_depth=payload["delegation_depth"],
        workload_thumbprint=thumbprint,
        issued_at=payload["iat"],
        not_before=payload["nbf"],
        expires_at=payload["exp"],
        approval_id=approval_id,
    )


def _validate_claim_shape(payload: dict[str, object]) -> None:
    """Validate the closed claim vocabulary and primitive JSON types."""

    if set(payload) - _ALLOWED_CLAIMS or not set(payload) >= _REQUIRED_CLAIMS:
        raise CapabilityTokenError("invalid capability")
    if payload.get("iss") != ISSUER or payload.get("aud") != AUDIENCE:
        raise CapabilityTokenError("invalid capability")

    jti = payload.get("jti")
    if not isinstance(jti, str) or not jti or not JTI_PATTERN.fullmatch(jti):
        raise CapabilityTokenError("invalid capability")
    strings = (
        "sub",
        "tenant_id",
        "workspace_id",
        "actor_user_id",
        "grant_id",
    )
    if any(not isinstance(payload.get(name), str) or not payload[name] for name in strings):
        raise CapabilityTokenError("invalid capability")
    grant_version = payload.get("grant_version")
    delegation_depth = payload.get("delegation_depth")
    issued_at = payload.get("iat")
    not_before = payload.get("nbf")
    expires_at = payload.get("exp")
    if (
        isinstance(grant_version, bool)
        or not isinstance(grant_version, int)
        or isinstance(delegation_depth, bool)
        or not isinstance(delegation_depth, int)
        or isinstance(issued_at, bool)
        or not isinstance(issued_at, int)
        or isinstance(not_before, bool)
        or not isinstance(not_before, int)
        or isinstance(expires_at, bool)
        or not isinstance(expires_at, int)
    ):
        raise CapabilityTokenError("invalid capability")
    if grant_version < 1 or delegation_depth < 0:
        raise CapabilityTokenError("invalid capability")
    if not_before < issued_at or expires_at <= not_before:
        raise CapabilityTokenError("invalid capability")
    if expires_at - issued_at > int(MAX_TOKEN_TTL.total_seconds()):
        raise CapabilityTokenError("invalid capability")
    cnf = payload.get("cnf")
    if not isinstance(cnf, dict) or set(cnf) != {"x5t#S256"}:
        raise CapabilityTokenError("invalid capability")
    thumbprint = cnf.get("x5t#S256")
    if not isinstance(thumbprint, str) or not _THUMBPRINT_PATTERN.fullmatch(thumbprint):
        raise CapabilityTokenError("invalid capability")
    approval_id = payload.get("approval_id")
    if approval_id is not None:
        # P34.2 is a fixed R0 read surface.  Approval-bearing grants/tokens are
        # introduced only with the later controlled-mutation phases.
        raise CapabilityTokenError("invalid capability")


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


__all__ = [
    "ALGORITHM",
    "AUDIENCE",
    "ISSUER",
    "JTI_PATTERN",
    "MAX_TOKEN_TTL",
    "TOKEN_TYPE",
    "CapabilityTokenClaims",
    "CapabilityTokenError",
    "decode_capability_token",
    "encode_capability_token",
    "get_trusted_kid",
    "private_key_fingerprint",
    "public_key_fingerprint",
]
