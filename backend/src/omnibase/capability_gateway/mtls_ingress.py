"""Server-owned mTLS ingress for the independent Capability Gateway.

Uvicorn does not expose the TLS peer certificate in the standard ASGI scope.
This module supplies a narrowly-scoped H11 protocol which extracts the peer
certificate from the asyncio TLS transport and passes it to an ASGI middleware.
Only that transport-derived DER certificate can create trusted Gateway evidence;
HTTP headers, cookies, source addresses, and bearer material are ignored.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from starlette.types import Receive, Scope, Send
from uvicorn.protocols.http.h11_impl import H11Protocol

from omnibase.capability_gateway.workload import TrustedGatewayPeerEvidence

AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]
_TRANSPORT_CERTIFICATE_KEY = "omnibase.transport_peer_certificate_der"
_KEY_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_RESERVED_HEADERS = frozenset(
    {
        b"x-omnibase-mtls-verified",
        b"x-omnibase-peer-kind",
        b"x-omnibase-workload-cert-sha256",
        b"x-omnibase-tenant-id",
        b"x-omnibase-workspace-id",
        b"x-omnibase-run-id",
        b"x-omnibase-node-id",
        b"x-omnibase-lease-id",
    }
)


class MtlsIngressRejected(RuntimeError):
    """A TLS peer cannot be promoted to trusted Gateway evidence."""


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("peer expiry must be timezone-aware")
    return value.astimezone(UTC)


def _parse_expiry(value: object) -> datetime:
    if not isinstance(value, str):
        raise ValueError("peer expiry must be an ISO-8601 string")
    return _aware_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))


def _canonical_digest(value: dict[str, object]) -> str:
    encoded = json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _uuid_text(value: str, name: str) -> str:
    try:
        return str(UUID(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


@dataclass(frozen=True, slots=True)
class ServerOwnedGatewayCredentialBinding:
    """Authorization inputs selected only by the server-owned peer registry."""

    grant_id: str
    expected_profile: Literal["read", "workspace_data"]
    key_id: str
    system_actor_id: str
    originating_user_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "grant_id", _uuid_text(self.grant_id, "grant_id"))
        if self.expected_profile not in {"read", "workspace_data"}:
            raise ValueError("expected_profile is invalid")
        object.__setattr__(
            self,
            "originating_user_id",
            _uuid_text(self.originating_user_id, "originating_user_id"),
        )
        if not isinstance(self.system_actor_id, str) or not self.system_actor_id:
            raise ValueError("system_actor_id is required")
        if not isinstance(self.key_id, str) or _KEY_ID.fullmatch(self.key_id) is None:
            raise ValueError("key_id is invalid")


@dataclass(frozen=True, slots=True)
class ServerOwnedGatewayPeer:
    """Non-secret allowlist record bound to one client-certificate digest."""

    peer_kind: str
    opaque_identity: str
    tenant_id: str
    workspace_id: str
    run_id: str
    runtime_instance_id: str
    node_id: str
    lease_id: str
    workspace_generation: int
    run_fencing_token: int
    node_fencing_token: int
    certificate_thumbprint: str
    expires_at: datetime
    grant_id: str
    expected_profile: Literal["read", "workspace_data"]
    key_id: str
    system_actor_id: str
    originating_user_id: str
    state: str = "active"

    @classmethod
    def from_json(cls, value: object) -> ServerOwnedGatewayPeer:
        if not isinstance(value, dict):
            raise ValueError("peer registry record must be an object")
        expected = {
            "peer_kind",
            "opaque_identity",
            "tenant_id",
            "workspace_id",
            "run_id",
            "runtime_instance_id",
            "node_id",
            "lease_id",
            "workspace_generation",
            "run_fencing_token",
            "node_fencing_token",
            "certificate_thumbprint",
            "expires_at",
            "grant_id",
            "expected_profile",
            "key_id",
            "system_actor_id",
            "originating_user_id",
            "state",
        }
        if set(value) != expected:
            raise ValueError("peer registry record has an invalid field set")
        state = value["state"]
        if state not in {"active", "revoked"}:
            raise ValueError("peer state must be active or revoked")
        return cls(
            peer_kind=str(value["peer_kind"]),
            opaque_identity=str(value["opaque_identity"]),
            tenant_id=str(value["tenant_id"]),
            workspace_id=str(value["workspace_id"]),
            run_id=str(value["run_id"]),
            runtime_instance_id=str(value["runtime_instance_id"]),
            node_id=str(value["node_id"]),
            lease_id=str(value["lease_id"]),
            workspace_generation=int(value["workspace_generation"]),
            run_fencing_token=int(value["run_fencing_token"]),
            node_fencing_token=int(value["node_fencing_token"]),
            certificate_thumbprint=str(value["certificate_thumbprint"]),
            expires_at=_parse_expiry(value["expires_at"]),
            grant_id=str(value["grant_id"]),
            expected_profile=str(value["expected_profile"]),  # type: ignore[arg-type]
            key_id=str(value["key_id"]),
            system_actor_id=str(value["system_actor_id"]),
            originating_user_id=str(value["originating_user_id"]),
            state=state,
        )

    def credential_binding(self) -> ServerOwnedGatewayCredentialBinding:
        return ServerOwnedGatewayCredentialBinding(
            grant_id=self.grant_id,
            expected_profile=self.expected_profile,
            key_id=self.key_id,
            system_actor_id=self.system_actor_id,
            originating_user_id=self.originating_user_id,
        )

    def trusted_evidence(self, *, now: datetime) -> TrustedGatewayPeerEvidence:
        now = _aware_utc(now)
        if self.state != "active" or self.expires_at <= now:
            raise MtlsIngressRejected("gateway_mtls_peer_rejected")
        payload: dict[str, object] = {
            "certificate_thumbprint": self.certificate_thumbprint,
            "expires_at": self.expires_at.isoformat(),
            "lease_id": self.lease_id,
            "node_fencing_token": self.node_fencing_token,
            "node_id": self.node_id,
            "opaque_identity": self.opaque_identity,
            "peer_kind": self.peer_kind,
            "run_fencing_token": self.run_fencing_token,
            "run_id": self.run_id,
            "runtime_instance_id": self.runtime_instance_id,
            "tenant_id": self.tenant_id,
            "workspace_generation": self.workspace_generation,
            "workspace_id": self.workspace_id,
        }
        return TrustedGatewayPeerEvidence(
            peer_kind=self.peer_kind,  # type: ignore[arg-type]
            opaque_identity=self.opaque_identity,
            tenant_id=self.tenant_id,
            workspace_id=self.workspace_id,
            run_id=self.run_id,
            runtime_instance_id=self.runtime_instance_id,
            node_id=self.node_id,
            lease_id=self.lease_id,
            workspace_generation=self.workspace_generation,
            run_fencing_token=self.run_fencing_token,
            node_fencing_token=self.node_fencing_token,
            certificate_thumbprint=self.certificate_thumbprint,
            evidence_digest=_canonical_digest(payload),
            expires_at=self.expires_at,
        )


class JsonMtlsPeerRegistry:
    """Reload a private, server-owned peer allowlist for every TLS connection."""

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] | None = None) -> None:
        self._path = Path(path)
        if not self._path.is_absolute():
            raise ValueError("mTLS peer registry path must be absolute")
        self._clock = clock or (lambda: datetime.now(UTC))

    def _read(self) -> list[ServerOwnedGatewayPeer]:
        metadata = os.lstat(self._path)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            raise MtlsIngressRejected("gateway_mtls_registry_untrusted")
        if os.name != "nt" and (
            metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077
        ):
            raise MtlsIngressRejected("gateway_mtls_registry_untrusted")
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MtlsIngressRejected("gateway_mtls_registry_unavailable") from exc
        if not isinstance(document, dict) or set(document) != {"schema_version", "peers"}:
            raise MtlsIngressRejected("gateway_mtls_registry_invalid")
        if document["schema_version"] != 1 or not isinstance(document["peers"], list):
            raise MtlsIngressRejected("gateway_mtls_registry_invalid")
        try:
            peers = [ServerOwnedGatewayPeer.from_json(item) for item in document["peers"]]
        except (TypeError, ValueError) as exc:
            raise MtlsIngressRejected("gateway_mtls_registry_invalid") from exc
        thumbprints = [item.certificate_thumbprint for item in peers]
        if len(thumbprints) != len(set(thumbprints)):
            raise MtlsIngressRejected("gateway_mtls_registry_invalid")
        return peers

    def resolve_peer(self, certificate_der: bytes) -> ServerOwnedGatewayPeer:
        if not isinstance(certificate_der, bytes) or not certificate_der:
            raise MtlsIngressRejected("gateway_mtls_certificate_missing")
        thumbprint = hashlib.sha256(certificate_der).hexdigest()
        for peer in self._read():
            if peer.certificate_thumbprint == thumbprint:
                peer.trusted_evidence(now=self._clock())
                return peer
        raise MtlsIngressRejected("gateway_mtls_peer_rejected")

    def resolve(self, certificate_der: bytes) -> TrustedGatewayPeerEvidence:
        return self.resolve_peer(certificate_der).trusted_evidence(now=self._clock())

    def resolve_entry(
        self, certificate_der: bytes
    ) -> tuple[TrustedGatewayPeerEvidence, ServerOwnedGatewayCredentialBinding]:
        peer = self.resolve_peer(certificate_der)
        return peer.trusted_evidence(now=self._clock()), peer.credential_binding()


class VerifiedMtlsGatewayIngress:
    """Promote only a transport-derived, allowlisted certificate into ASGI scope."""

    def __init__(self, app: AsgiApp, registry: JsonMtlsPeerRegistry) -> None:
        self._app = app
        self._registry = registry

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            await self._app(scope, receive, send)
            return
        if scope["type"] != "http":
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4401})
            return
        certificate_der = scope.pop(_TRANSPORT_CERTIFICATE_KEY, None)  # type: ignore[typeddict-item]
        try:
            evidence, binding = self._registry.resolve_entry(certificate_der)
        except (MtlsIngressRejected, TypeError, ValueError):
            await _reject_untrusted_peer(send)
            return
        trusted_scope = dict(scope)
        trusted_scope["headers"] = [
            (key, value)
            for key, value in scope.get("headers", [])
            if key.lower() not in _RESERVED_HEADERS
        ]
        trusted_scope["omnibase.mtls_verified"] = True  # type: ignore[typeddict-item]
        trusted_scope["omnibase.trusted_gateway_peer"] = evidence  # type: ignore[typeddict-item]
        trusted_scope["omnibase.gateway_credential_binding"] = binding  # type: ignore[typeddict-item]
        await self._app(trusted_scope, receive, send)


async def _reject_untrusted_peer(send: Send) -> None:
    body = b'{"error":{"code":"invalid_mtls_peer","message":"mTLS peer rejected"}}'
    await send(
        {
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class VerifiedMtlsH11Protocol(H11Protocol):
    """Uvicorn H11 protocol that copies only the verified TLS peer DER to ASGI."""

    def connection_made(self, transport: Any) -> None:
        super().connection_made(transport)
        ssl_object = transport.get_extra_info("ssl_object")
        certificate_der = (
            ssl_object.getpeercert(binary_form=True) if ssl_object is not None else None
        )
        original_app = self.app

        async def transport_bound_app(scope: Scope, receive: Receive, send: Send) -> None:
            bound_scope = dict(scope)
            if isinstance(certificate_der, bytes) and certificate_der:
                bound_scope[_TRANSPORT_CERTIFICATE_KEY] = certificate_der  # type: ignore[typeddict-item]
            await original_app(bound_scope, receive, send)

        self.app = transport_bound_app


__all__ = [
    "JsonMtlsPeerRegistry",
    "MtlsIngressRejected",
    "ServerOwnedGatewayCredentialBinding",
    "ServerOwnedGatewayPeer",
    "VerifiedMtlsGatewayIngress",
    "VerifiedMtlsH11Protocol",
]
