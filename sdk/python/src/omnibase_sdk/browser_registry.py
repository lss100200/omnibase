"""P5.1C Browser Agent Registry control-plane SDK (logical Browser surface only).

Talks to the Browser ``/api/v1`` agent-registry endpoints with a Bearer JWT.
Mirrors the server DTOs field-for-field (``extra="forbid"`` on the server means
the client rejects unknown fields too).  Never sends tenant schemas, physical
locators, credentials or audit internals; every request carries an explicit
``Idempotency-Key`` on mutations.
"""

from __future__ import annotations

import json
import math
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4

from omnibase_sdk.models import (
    JsonValue,
    require_exact_keys,
    require_integer,
    require_mapping,
    require_string,
)

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_SCOPE_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_DEFAULT_RESPONSE_LIMIT = 1_000_000
_RISK_LEVELS = {"low", "medium", "high", "critical"}
_DEFINITION_STATES = {"draft", "active", "disabled", "revoked"}
_VERSION_STATES = {"draft", "sealed", "deprecated", "revoked"}
_BINDING_STATES = {"pending_approval", "installed", "disabled", "superseded", "revoked"}

_DEFINITION_KEYS = {
    "agent_definition_id",
    "stable_logical_key",
    "display_name",
    "description",
    "risk_level",
    "definition_state",
    "metadata_version",
    "created_at",
}
_VERSION_KEYS = {
    "agent_version_id",
    "agent_definition_id",
    "version",
    "version_state",
    "manifest_digest",
    "instructions_digest",
    "risk_level",
    "max_context_tokens",
    "allowed_tool_ids",
    "max_concurrency",
    "created_at",
}
_BINDING_KEYS = {
    "binding_id",
    "workspace_id",
    "workspace_generation",
    "agent_definition_id",
    "agent_version_id",
    "agent_version_digest",
    "binding_state",
    "resource_scopes",
    "default_budget_policy",
    "created_at",
    "disabled_at",
    "superseded_by",
}
_BUDGET_KEYS = {
    "max_tokens",
    "max_cost_units",
    "max_wall_clock_seconds",
    "max_tool_calls",
}


def _require_uuid(value: Any, label: str) -> str:
    text = require_string(value, label)
    if _UUID_RE.fullmatch(text) is None:
        raise ValueError(f"{label} must be a lowercase UUID")
    return text


def _require_digest(value: Any, label: str) -> str:
    text = require_string(value, label)
    if _DIGEST_RE.fullmatch(text) is None:
        raise ValueError(f"{label} must be a lowercase 64-character SHA-256")
    return text


def _require_closed(value: Any, allowed: set[str], label: str) -> str:
    text = require_string(value, label)
    if text not in allowed:
        raise ValueError(f"{label} is outside the closed set")
    return text


def _validate_browser_path(path: str) -> str:
    """Reject URL normalization tricks before joining a Browser API path."""
    if not isinstance(path, str) or not path.startswith("/api/v1/"):
        raise ValueError("Browser transport only permits GET/POST under /api/v1")
    if any(character in path for character in ("\\", "%", "?", "#", "\x00")):
        raise ValueError("Browser transport path must be an unencoded absolute API path")
    if "//" in path or any(segment in {".", ".."} for segment in path.split("/")):
        raise ValueError("Browser transport path contains forbidden dot or empty segments")
    parsed = urlparse(path)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or parsed.path != path:
        raise ValueError("Browser transport path is not a confined API path")
    return path


def _require_scope_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or not value or len(value) > 32:
        raise ValueError(f"{label} must be a non-empty list of at most 32 scopes")
    seen: set[str] = set()
    for item in value:
        text = require_string(item, label)
        if text in ("*", "all", "any") or _SCOPE_RE.fullmatch(text) is None:
            raise ValueError(f"{label} contains a wildcard or invalid scope")
        if text in seen:
            raise ValueError(f"{label} must not contain duplicates")
        seen.add(text)
    return value


class RegistryBrowserError(RuntimeError):
    """A safe Browser registry error with the envelope code attached."""

    def __init__(self, status_code: int, code: str, message: str, request_id: str | None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.request_id = request_id
        super().__init__(f"{code}: {message} (request_id={request_id or 'unavailable'})")


# ---------------------------------------------------------------------------
# Models (mirror of backend/src/omnibase/agent_registry/schemas.py)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DefaultBudgetPolicyRead:
    max_tokens: int
    max_cost_units: int
    max_wall_clock_seconds: int
    max_tool_calls: int

    @classmethod
    def from_dict(cls, raw: Any) -> DefaultBudgetPolicyRead:
        value = require_mapping(raw, "default_budget_policy")
        require_exact_keys(value, _BUDGET_KEYS, set(), "default_budget_policy")
        return cls(
            max_tokens=require_integer(value["max_tokens"], "max_tokens", minimum=1),
            max_cost_units=require_integer(value["max_cost_units"], "max_cost_units", minimum=1),
            max_wall_clock_seconds=require_integer(
                value["max_wall_clock_seconds"], "max_wall_clock_seconds", minimum=1
            ),
            max_tool_calls=require_integer(value["max_tool_calls"], "max_tool_calls", minimum=1),
        )


@dataclass(frozen=True, slots=True)
class AgentDefinitionRead:
    agent_definition_id: str
    stable_logical_key: str
    display_name: str
    description: str | None
    risk_level: str
    definition_state: str
    metadata_version: int
    created_at: str | None

    @classmethod
    def from_dict(cls, raw: Any) -> AgentDefinitionRead:
        value = require_mapping(raw, "agent definition")
        require_exact_keys(value, _DEFINITION_KEYS, set(), "agent definition")
        return cls(
            agent_definition_id=_require_uuid(value["agent_definition_id"], "agent_definition_id"),
            stable_logical_key=require_string(value["stable_logical_key"], "stable_logical_key"),
            display_name=require_string(value["display_name"], "display_name"),
            description=_optional_string(value.get("description"), "description"),
            risk_level=_require_closed(value["risk_level"], _RISK_LEVELS, "risk_level"),
            definition_state=_require_closed(
                value["definition_state"], _DEFINITION_STATES, "definition_state"
            ),
            metadata_version=require_integer(
                value["metadata_version"], "metadata_version", minimum=1
            ),
            created_at=_optional_string(value.get("created_at"), "created_at"),
        )


@dataclass(frozen=True, slots=True)
class AgentVersionRead:
    agent_version_id: str
    agent_definition_id: str
    version: str
    version_state: str
    manifest_digest: str
    instructions_digest: str
    risk_level: str
    max_context_tokens: int
    allowed_tool_ids: tuple[str, ...]
    max_concurrency: int
    created_at: str | None

    @classmethod
    def from_dict(cls, raw: Any) -> AgentVersionRead:
        value = require_mapping(raw, "agent version")
        require_exact_keys(value, _VERSION_KEYS, set(), "agent version")
        if not isinstance(value["allowed_tool_ids"], list) or len(value["allowed_tool_ids"]) > 64:
            raise ValueError("allowed_tool_ids must be a list of at most 64 strings")
        return cls(
            agent_version_id=_require_uuid(value["agent_version_id"], "agent_version_id"),
            agent_definition_id=_require_uuid(value["agent_definition_id"], "agent_definition_id"),
            version=require_string(value["version"], "version"),
            version_state=_require_closed(value["version_state"], _VERSION_STATES, "version_state"),
            manifest_digest=_require_digest(value["manifest_digest"], "manifest_digest"),
            instructions_digest=_require_digest(
                value["instructions_digest"], "instructions_digest"
            ),
            risk_level=_require_closed(value["risk_level"], _RISK_LEVELS, "risk_level"),
            max_context_tokens=require_integer(
                value["max_context_tokens"], "max_context_tokens", minimum=1
            ),
            allowed_tool_ids=tuple(
                require_string(item, "allowed_tool_ids") for item in value["allowed_tool_ids"]
            ),
            max_concurrency=require_integer(value["max_concurrency"], "max_concurrency", minimum=1),
            created_at=_optional_string(value.get("created_at"), "created_at"),
        )


@dataclass(frozen=True, slots=True)
class AgentInstallationRead:
    binding_id: str
    workspace_id: str
    workspace_generation: int
    agent_definition_id: str
    agent_version_id: str
    agent_version_digest: str
    binding_state: str
    resource_scopes: tuple[str, ...]
    default_budget_policy: DefaultBudgetPolicyRead
    created_at: str | None
    disabled_at: str | None
    superseded_by: str | None

    @classmethod
    def from_dict(cls, raw: Any) -> AgentInstallationRead:
        value = require_mapping(raw, "agent installation")
        require_exact_keys(value, _BINDING_KEYS, set(), "agent installation")
        scopes = _require_scope_list(value["resource_scopes"], "resource_scopes")
        superseded_by = value.get("superseded_by")
        return cls(
            binding_id=_require_uuid(value["binding_id"], "binding_id"),
            workspace_id=_require_uuid(value["workspace_id"], "workspace_id"),
            workspace_generation=require_integer(
                value["workspace_generation"], "workspace_generation", minimum=1
            ),
            agent_definition_id=_require_uuid(value["agent_definition_id"], "agent_definition_id"),
            agent_version_id=_require_uuid(value["agent_version_id"], "agent_version_id"),
            agent_version_digest=_require_digest(
                value["agent_version_digest"], "agent_version_digest"
            ),
            binding_state=_require_closed(value["binding_state"], _BINDING_STATES, "binding_state"),
            resource_scopes=tuple(scopes),
            default_budget_policy=DefaultBudgetPolicyRead.from_dict(value["default_budget_policy"]),
            created_at=_optional_string(value.get("created_at"), "created_at"),
            disabled_at=_optional_string(value.get("disabled_at"), "disabled_at"),
            superseded_by=(
                _require_uuid(superseded_by, "superseded_by") if superseded_by is not None else None
            ),
        )


def _optional_string(value: Any, label: str) -> str | None:
    if value is None:
        return None
    return require_string(value, label)


def _list_response(raw: Any, label: str) -> tuple[list[Any], int]:
    value = require_mapping(raw, label)
    require_exact_keys(value, {"items", "total"}, set(), label)
    if not isinstance(value["items"], list):
        raise ValueError(f"{label}.items must be a list")
    return value["items"], require_integer(value["total"], "total", minimum=0)


# ---------------------------------------------------------------------------
# Transport (Bearer JWT over the Browser /api/v1 surface)
# ---------------------------------------------------------------------------


class AccessTokenProvider(Protocol):
    """Fetch a fresh Browser access token immediately before a request."""

    def get_access_token(self) -> str: ...


@dataclass(frozen=True, slots=True)
class StaticAccessTokenProvider:
    """Test-only provider; the supplied token is never serialized."""

    access_token: str

    def get_access_token(self) -> str:
        if not self.access_token or any(character.isspace() for character in self.access_token):
            raise ValueError("Access token is empty or malformed")
        return self.access_token


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    headers: dict[str, str]
    body: Any


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


class BrowserHttpTransport:
    """Small synchronous HTTP transport for the Browser ``/api/v1`` surface."""

    def __init__(
        self,
        base_url: str,
        access_token_provider: AccessTokenProvider,
        *,
        timeout_seconds: float = 10.0,
        allow_insecure_localhost: bool = False,
        max_response_bytes: int = _DEFAULT_RESPONSE_LIMIT,
    ) -> None:
        parsed = urlparse(base_url)
        localhost = parsed.hostname in {"127.0.0.1", "localhost", "::1"}
        if parsed.scheme != "https" and not (allow_insecure_localhost and localhost):
            raise ValueError(
                "Browser transport requires HTTPS except explicit localhost development"
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
        self._token_provider = access_token_provider
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def request(
        self,
        method: str,
        path: str,
        body: dict[str, JsonValue] | None,
        *,
        idempotency_key: str | None = None,
    ) -> TransportResponse:
        if method not in {"GET", "POST"}:
            raise ValueError("Browser transport only permits GET/POST under /api/v1")
        path = _validate_browser_path(path)
        token = self._token_provider.get_access_token()
        request_id = str(uuid4())
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "X-Request-Id": request_id,
        }
        encoded: bytes | None = None
        if body is not None:
            encoded = json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if idempotency_key is not None:
            if not isinstance(idempotency_key, str) or not 8 <= len(idempotency_key) <= 128:
                raise ValueError("idempotency_key must contain between 8 and 128 characters")
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(  # noqa: S310 - origin scheme validated in __init__
            f"{self._base_url}{path}",
            data=encoded,
            method=method,
            headers=headers,
        )
        try:
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(request, timeout=self._timeout_seconds) as response:
                return TransportResponse(
                    status_code=response.status,
                    headers={key.casefold(): value for key, value in response.headers.items()},
                    body=_read_json_bounded(response, self._max_response_bytes),
                )
        except urllib.error.HTTPError as exc:
            try:
                response_body = _read_json_bounded(exc, self._max_response_bytes)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
                response_body = {
                    "error": {"code": "invalid_browser_response", "message": "Browser error"}
                }
            return TransportResponse(
                status_code=exc.code,
                headers={key.casefold(): value for key, value in exc.headers.items()},
                body=response_body,
            )


def _read_json_bounded(stream: Any, limit: int) -> Any:
    raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError("Browser response exceeded the configured byte limit")
    return json.loads(raw.decode("utf-8"))


def raise_for_error(response: TransportResponse) -> None:
    if 200 <= response.status_code < 300:
        return
    envelope = require_mapping(response.body, "error response")
    error = require_mapping(envelope.get("error"), "error")
    code = error.get("code")
    message = error.get("message")
    if set(envelope) != {"error"} or set(error) != {"code", "message"}:
        code, message = "invalid_browser_response", "Browser returned an invalid error envelope"
    if not isinstance(code, str) or not isinstance(message, str):
        code, message = "invalid_browser_response", "Browser returned an invalid error envelope"
    request_id = response.headers.get("x-request-id")
    if request_id is not None and _REQUEST_ID_PATTERN.fullmatch(request_id) is None:
        request_id = None
    raise RegistryBrowserError(response.status_code, code, message, request_id)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class AgentRegistryBrowserClient:
    """Browser Agent Registry control client (read-only catalog + workspace mutations)."""

    def __init__(self, transport: BrowserHttpTransport) -> None:
        self._transport = transport

    @classmethod
    def from_http(
        cls,
        base_url: str,
        access_token_provider: AccessTokenProvider,
        *,
        timeout_seconds: float = 10.0,
        allow_insecure_localhost: bool = False,
    ) -> AgentRegistryBrowserClient:
        return cls(
            BrowserHttpTransport(
                base_url,
                access_token_provider,
                timeout_seconds=timeout_seconds,
                allow_insecure_localhost=allow_insecure_localhost,
            )
        )

    # -- Catalog (read-only) ------------------------------------------------

    def list_agent_definitions(self) -> tuple[list[AgentDefinitionRead], int]:
        response = self._transport.request("GET", "/api/v1/agent-definitions", None)
        raise_for_error(response)
        items, total = _list_response(response.body, "agent definitions")
        return [AgentDefinitionRead.from_dict(item) for item in items], total

    def get_agent_definition(self, agent_definition_id: str) -> AgentDefinitionRead:
        agent_definition_id = _require_uuid(agent_definition_id, "agent_definition_id")
        response = self._transport.request(
            "GET", f"/api/v1/agent-definitions/{agent_definition_id}", None
        )
        raise_for_error(response)
        return AgentDefinitionRead.from_dict(response.body)

    def list_agent_versions(self, agent_definition_id: str) -> tuple[list[AgentVersionRead], int]:
        agent_definition_id = _require_uuid(agent_definition_id, "agent_definition_id")
        response = self._transport.request(
            "GET", f"/api/v1/agent-definitions/{agent_definition_id}/versions", None
        )
        raise_for_error(response)
        items, total = _list_response(response.body, "agent versions")
        return [AgentVersionRead.from_dict(item) for item in items], total

    def get_agent_version(
        self, agent_definition_id: str, agent_version_id: str
    ) -> AgentVersionRead:
        agent_definition_id = _require_uuid(agent_definition_id, "agent_definition_id")
        agent_version_id = _require_uuid(agent_version_id, "agent_version_id")
        response = self._transport.request(
            "GET",
            f"/api/v1/agent-definitions/{agent_definition_id}/versions/{agent_version_id}",
            None,
        )
        raise_for_error(response)
        return AgentVersionRead.from_dict(response.body)

    def list_installations(self, workspace_id: str) -> tuple[list[AgentInstallationRead], int]:
        workspace_id = _require_uuid(workspace_id, "workspace_id")
        response = self._transport.request(
            "GET", f"/api/v1/workspaces/{workspace_id}/agent-installations", None
        )
        raise_for_error(response)
        items, total = _list_response(response.body, "agent installations")
        return [AgentInstallationRead.from_dict(item) for item in items], total

    def get_installation(self, workspace_id: str, binding_id: str) -> AgentInstallationRead:
        workspace_id = _require_uuid(workspace_id, "workspace_id")
        binding_id = _require_uuid(binding_id, "binding_id")
        response = self._transport.request(
            "GET",
            f"/api/v1/workspaces/{workspace_id}/agent-installations/{binding_id}",
            None,
        )
        raise_for_error(response)
        return AgentInstallationRead.from_dict(response.body)

    # -- Workspace mutations --------------------------------------------------

    def install(
        self,
        *,
        workspace_id: str,
        idempotency_key: str,
        agent_definition_id: str,
        agent_version_id: str,
        agent_version_digest: str,
        workspace_generation: int,
        resource_scopes: list[str],
        default_budget_policy: dict[str, int],
        approval_id: str | None = None,
    ) -> AgentInstallationRead:
        workspace_id = _require_uuid(workspace_id, "workspace_id")
        body: dict[str, JsonValue] = {
            "agent_definition_id": _require_uuid(agent_definition_id, "agent_definition_id"),
            "agent_version_id": _require_uuid(agent_version_id, "agent_version_id"),
            "agent_version_digest": _require_digest(agent_version_digest, "agent_version_digest"),
            "workspace_generation": require_integer(
                workspace_generation, "workspace_generation", minimum=1
            ),
            "resource_scopes": _require_scope_list(resource_scopes, "resource_scopes"),
            "default_budget_policy": _budget_payload(default_budget_policy),
        }
        if approval_id is not None:
            body["approval_id"] = _require_uuid(approval_id, "approval_id")
        response = self._transport.request(
            "POST",
            f"/api/v1/workspaces/{workspace_id}/agent-installations",
            body,
            idempotency_key=idempotency_key,
        )
        raise_for_error(response)
        return AgentInstallationRead.from_dict(response.body)

    def disable(
        self, *, workspace_id: str, binding_id: str, idempotency_key: str
    ) -> AgentInstallationRead:
        workspace_id = _require_uuid(workspace_id, "workspace_id")
        binding_id = _require_uuid(binding_id, "binding_id")
        response = self._transport.request(
            "POST",
            f"/api/v1/workspaces/{workspace_id}/agent-installations/{binding_id}/disable",
            None,
            idempotency_key=idempotency_key,
        )
        raise_for_error(response)
        return AgentInstallationRead.from_dict(response.body)

    def upgrade(
        self,
        *,
        workspace_id: str,
        binding_id: str,
        idempotency_key: str,
        target_agent_version_id: str,
        target_agent_version_digest: str,
        expected_binding_id: str | None = None,
        approval_id: str | None = None,
    ) -> AgentInstallationRead:
        workspace_id = _require_uuid(workspace_id, "workspace_id")
        binding_id = _require_uuid(binding_id, "binding_id")
        body: dict[str, JsonValue] = {
            "target_agent_version_id": _require_uuid(
                target_agent_version_id, "target_agent_version_id"
            ),
            "target_agent_version_digest": _require_digest(
                target_agent_version_digest, "target_agent_version_digest"
            ),
        }
        if expected_binding_id is not None:
            body["expected_binding_id"] = _require_uuid(expected_binding_id, "expected_binding_id")
        if approval_id is not None:
            body["approval_id"] = _require_uuid(approval_id, "approval_id")
        response = self._transport.request(
            "POST",
            f"/api/v1/workspaces/{workspace_id}/agent-installations/{binding_id}/upgrade",
            body,
            idempotency_key=idempotency_key,
        )
        raise_for_error(response)
        return AgentInstallationRead.from_dict(response.body)

    def rollback(
        self,
        *,
        workspace_id: str,
        binding_id: str,
        idempotency_key: str,
        rollback_agent_version_id: str,
        rollback_agent_version_digest: str,
        expected_binding_id: str | None = None,
        approval_id: str | None = None,
    ) -> AgentInstallationRead:
        workspace_id = _require_uuid(workspace_id, "workspace_id")
        binding_id = _require_uuid(binding_id, "binding_id")
        body: dict[str, JsonValue] = {
            "rollback_agent_version_id": _require_uuid(
                rollback_agent_version_id, "rollback_agent_version_id"
            ),
            "rollback_agent_version_digest": _require_digest(
                rollback_agent_version_digest, "rollback_agent_version_digest"
            ),
        }
        if expected_binding_id is not None:
            body["expected_binding_id"] = _require_uuid(expected_binding_id, "expected_binding_id")
        if approval_id is not None:
            body["approval_id"] = _require_uuid(approval_id, "approval_id")
        response = self._transport.request(
            "POST",
            f"/api/v1/workspaces/{workspace_id}/agent-installations/{binding_id}/rollback",
            body,
            idempotency_key=idempotency_key,
        )
        raise_for_error(response)
        return AgentInstallationRead.from_dict(response.body)


def _budget_payload(value: dict[str, int]) -> dict[str, JsonValue]:
    data = require_mapping(value, "default_budget_policy")
    require_exact_keys(data, _BUDGET_KEYS, set(), "default_budget_policy")
    return {key: require_integer(data[key], key, minimum=1) for key in _BUDGET_KEYS}


__all__ = [
    "AccessTokenProvider",
    "AgentDefinitionRead",
    "AgentInstallationRead",
    "AgentRegistryBrowserClient",
    "AgentVersionRead",
    "BrowserHttpTransport",
    "DefaultBudgetPolicyRead",
    "RegistryBrowserError",
    "StaticAccessTokenProvider",
    "TransportResponse",
    "raise_for_error",
]
