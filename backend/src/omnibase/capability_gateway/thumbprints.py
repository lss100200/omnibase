"""Certificate thumbprint encodings shared by mTLS and capability tokens."""

from __future__ import annotations

import base64
import re

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def certificate_thumbprint_to_x5t_s256(thumbprint: str) -> str:
    """Convert lowercase hexadecimal SHA-256 into unpadded base64url."""

    if not isinstance(thumbprint, str) or _SHA256_HEX.fullmatch(thumbprint) is None:
        raise ValueError("certificate_thumbprint must be a lowercase SHA-256 digest")
    return base64.urlsafe_b64encode(bytes.fromhex(thumbprint)).decode("ascii").rstrip("=")


__all__ = ["certificate_thumbprint_to_x5t_s256"]
