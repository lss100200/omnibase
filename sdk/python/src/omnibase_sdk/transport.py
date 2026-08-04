"""Credential and transport boundaries for the OmniBase Python SDK."""

from __future__ import annotations

import json
import math
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from omnibase_sdk.models import GatewayError, JsonValue, require_mapping

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# A 1 MiB artifact expands to roughly 1.4 MiB as canonical base64 plus JSON fields.
_DEFAULT_RESPONSE_LIMIT = 1_500_000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


@dataclass(frozen=True, slots=True)
class WorkloadCredential:
    """A short-lived in-memory capability; its value is excluded from repr."""

    token: str = field(repr=False)
    workload_identity: str
    expires_at: datetime

    def authorization_value(self) -> str:
        if not self.token or any(character.isspace() for character in self.token):
            raise ValueError("Capability token is empty or malformed")
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at <= datetime.now(UTC):
            raise ValueError("Capability credential is expired")
        if not self.workload_identity or len(self.workload_identity) > 128:
            raise ValueError("workload_identity is empty or malformed")
        return f"Capability {self.token}"


class WorkloadCredentialProvider(Protocol):
    """Fetch a fresh workload credential immediately before a gateway request."""

    def get_credential(self) -> WorkloadCredential: ...


@dataclass(frozen=True, slots=True)
class StaticCredentialProvider:
    """Test-only/in-memory provider; never serializes the supplied credential."""

    credential: WorkloadCredential = field(repr=False)

    def get_credential(self) -> WorkloadCredential:
        return self.credential


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    headers: dict[str, str]
    body: Any


class Transport(Protocol):
    def request(self, method: str, path: str, body: dict[str, JsonValue]) -> TransportResponse: ...


class HttpTransport:
    """Small synchronous HTTP transport with explicit mTLS configuration."""

    def __init__(
        self,
        base_url: str,
        credential_provider: WorkloadCredentialProvider,
        *,
        ssl_context: ssl.SSLContext | None = None,
        timeout_seconds: float = 10.0,
        allow_insecure_localhost: bool = False,
        max_response_bytes: int = _DEFAULT_RESPONSE_LIMIT,
    ) -> None:
        parsed = urlparse(base_url)
        localhost = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (allow_insecure_localhost and localhost):
            raise ValueError(
                "Gateway transport requires HTTPS except explicit localhost development"
            )
        if (
            parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError(
                "base_url must be an origin without credentials, path, query, or fragment"
            )
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and greater than zero")
        if not 1 <= max_response_bytes <= 2_000_000:
            raise ValueError("max_response_bytes must be between 1 and 2000000")
        self._base_url = base_url.rstrip("/")
        self._credential_provider = credential_provider
        self._ssl_context = ssl_context
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def request(self, method: str, path: str, body: dict[str, JsonValue]) -> TransportResponse:
        if method != "POST" or not path.startswith("/gateway/v1/"):
            raise ValueError("P34.2 transport only permits POST requests to /gateway/v1")
        credential = self._credential_provider.get_credential()
        request_id = str(uuid4())
        encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 - origin scheme validated in __init__
            f"{self._base_url}{path}",
            data=encoded,
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": credential.authorization_value(),
                "Content-Type": "application/json",
                "X-Omnibase-Workload-Identity": credential.workload_identity,
                "X-Request-Id": request_id,
            },
        )
        try:
            handlers: list[Any] = [_NoRedirect()]
            if self._ssl_context is not None:
                handlers.append(urllib.request.HTTPSHandler(context=self._ssl_context))
            opener = urllib.request.build_opener(*handlers)
            with opener.open(request, timeout=self._timeout_seconds) as response:
                response_body = _read_json_bounded(response, self._max_response_bytes)
                return TransportResponse(
                    status_code=response.status,
                    headers={key.casefold(): value for key, value in response.headers.items()},
                    body=response_body,
                )
        except urllib.error.HTTPError as exc:
            try:
                response_body = _read_json_bounded(exc, self._max_response_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                response_body = {
                    "error": {"code": "invalid_gateway_response", "message": "Gateway error"}
                }
            return TransportResponse(
                status_code=exc.code,
                headers={key.casefold(): value for key, value in exc.headers.items()},
                body=response_body,
            )


def _read_json_bounded(stream: Any, limit: int) -> Any:
    raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Gateway response exceeded the configured byte limit")
    return json.loads(raw.decode("utf-8"))


def raise_for_error(response: TransportResponse) -> None:
    if 200 <= response.status_code < 300:
        return
    envelope = require_mapping(response.body, "error response")
    error = require_mapping(envelope.get("error"), "error")
    code = error.get("code")
    message = error.get("message")
    if set(envelope) != {"error"} or set(error) != {"code", "message"}:
        code, message = "invalid_gateway_response", "Gateway returned an invalid error envelope"
    if not isinstance(code, str) or not isinstance(message, str):
        code, message = "invalid_gateway_response", "Gateway returned an invalid error envelope"
    request_id = response.headers.get("x-request-id")
    if request_id is not None and _REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        request_id = None
    raise GatewayError(response.status_code, code, message, request_id)
