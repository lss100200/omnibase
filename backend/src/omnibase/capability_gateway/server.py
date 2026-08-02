"""Standalone production-style mTLS server for the Capability Gateway."""

from __future__ import annotations

import json
import os
import ssl
import stat
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import uvicorn
from starlette.types import Receive, Scope, Send

from omnibase.capabilities.service import TrustedIssuerContext
from omnibase.capability_gateway.app import create_production_gateway_app
from omnibase.capability_gateway.mtls_ingress import (
    JsonMtlsPeerRegistry,
    ServerOwnedGatewayCredentialBinding,
    VerifiedMtlsGatewayIngress,
    VerifiedMtlsH11Protocol,
)
from omnibase.capability_gateway.security import CapabilityVerificationError
from omnibase.capability_gateway.workload import (
    GatewayCredentialIssueRequest,
    GatewayCredentialUnavailable,
    SqlAlchemyGatewayCredentialIssuer,
    SqlAlchemyRunLeaseWorkloadAttestor,
    TrustedGatewayPeerEvidence,
)
from omnibase.core.db import get_session_factory

AsgiApp = Callable[[Scope, Receive, Send], Awaitable[None]]


class HardenedGatewayUvicornConfig(uvicorn.Config):
    """Uvicorn TLS configuration with an explicit TLS 1.2 floor."""

    def load(self) -> None:
        super().load()
        if self.ssl is None:
            raise ValueError("Gateway TLS context is required")
        self.ssl.minimum_version = ssl.TLSVersion.TLSv1_2


def _private_file(value: str, name: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{name} must be a regular non-symlink file")
    if os.name != "nt" and (
        metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise ValueError(f"{name} must be owner-only")
    return path


def _public_trust_file(value: str, name: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError(f"{name} must be a regular non-symlink file")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o022:
        raise ValueError(f"{name} must not be group/world writable")
    return path


@dataclass(frozen=True, slots=True)
class GatewayServerConfig:
    host: str
    port: int
    server_certificate: Path
    server_private_key: Path
    client_ca: Path
    peer_registry: Path
    cursor_secret_file: Path
    signing_private_key: Path

    @classmethod
    def from_environment(cls) -> GatewayServerConfig:
        port_text = os.environ.get("OMNIBASE_GATEWAY_PORT", "8443")
        try:
            port = int(port_text)
        except ValueError as exc:
            raise ValueError("OMNIBASE_GATEWAY_PORT must be an integer") from exc
        if not 1 <= port <= 65535:
            raise ValueError("OMNIBASE_GATEWAY_PORT is outside the valid range")
        required = {
            "server_certificate": "OMNIBASE_GATEWAY_TLS_CERT",
            "server_private_key": "OMNIBASE_GATEWAY_TLS_KEY",
            "client_ca": "OMNIBASE_GATEWAY_CLIENT_CA",
            "peer_registry": "OMNIBASE_GATEWAY_PEER_REGISTRY",
            "cursor_secret_file": "OMNIBASE_GATEWAY_CURSOR_SECRET_FILE",
            "signing_private_key": "OMNIBASE_GATEWAY_SIGNING_PRIVATE_KEY",
        }
        missing = [env for env in required.values() if not os.environ.get(env)]
        if missing:
            raise ValueError("Gateway server configuration is incomplete")
        return cls(
            host=os.environ.get("OMNIBASE_GATEWAY_HOST", "127.0.0.1"),
            port=port,
            server_certificate=_public_trust_file(
                os.environ[required["server_certificate"]], "server certificate"
            ),
            server_private_key=_private_file(
                os.environ[required["server_private_key"]], "server private key"
            ),
            client_ca=_public_trust_file(os.environ[required["client_ca"]], "client CA"),
            peer_registry=_private_file(os.environ[required["peer_registry"]], "peer registry"),
            cursor_secret_file=_private_file(
                os.environ[required["cursor_secret_file"]], "cursor secret"
            ),
            signing_private_key=_private_file(
                os.environ[required["signing_private_key"]], "signing private key"
            ),
        )


class FileCapabilityPrivateKeyProvider:
    """Load the Core-only signing key only when the issuer requests it."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def load_private_key(self, key_id: str) -> bytes:
        if not key_id:
            raise GatewayCredentialUnavailable("gateway_signing_key_unavailable")
        value = self._path.read_bytes()
        if not value or len(value) > 64 * 1024:
            raise GatewayCredentialUnavailable("gateway_signing_key_unavailable")
        return value


class GatewayCredentialVendingApp:
    """mTLS-only, parameter-free credential delivery before the four read routes."""

    _PATH = "/gateway/v1/credential/read"

    def __init__(
        self,
        app: AsgiApp,
        *,
        attestor: SqlAlchemyRunLeaseWorkloadAttestor,
        issuer: SqlAlchemyGatewayCredentialIssuer,
    ) -> None:
        self._app = app
        self._attestor = attestor
        self._issuer = issuer

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != self._PATH:
            await self._app(scope, receive, send)
            return
        if scope.get("method") != "POST":
            await _json_response(send, 405, {"error": {"code": "method_not_allowed"}})
            return
        message = await receive()
        if (
            message.get("type") != "http.request"
            or message.get("body")
            or message.get("more_body", False)
        ):
            await _json_response(send, 422, {"error": {"code": "empty_request_required"}})
            return
        evidence = scope.get("omnibase.trusted_gateway_peer")  # type: ignore[typeddict-item]
        binding = scope.get("omnibase.gateway_credential_binding")  # type: ignore[typeddict-item]
        if not isinstance(evidence, TrustedGatewayPeerEvidence) or not isinstance(
            binding, ServerOwnedGatewayCredentialBinding
        ):
            await _json_response(send, 401, {"error": {"code": "invalid_mtls_peer"}})
            return
        try:
            trusted = self._attestor.attest(scope, evidence.opaque_identity)
            if (
                trusted.tenant_id != evidence.tenant_id
                or trusted.workspace_id != evidence.workspace_id
                or trusted.runtime_instance_id != evidence.runtime_instance_id
                or trusted.certificate_thumbprint != evidence.certificate_thumbprint
            ):
                raise CapabilityVerificationError
            peer_ttl = evidence.expires_at - datetime.now(UTC)
            if peer_ttl <= timedelta(0):
                raise CapabilityVerificationError
            credential = self._issuer.issue(
                GatewayCredentialIssueRequest(
                    tenant_id=evidence.tenant_id,
                    workspace_id=evidence.workspace_id,
                    run_id=evidence.run_id,
                    runtime_instance_id=evidence.runtime_instance_id,
                    node_id=evidence.node_id,
                    lease_id=evidence.lease_id,
                    grant_id=binding.grant_id,
                    key_id=binding.key_id,
                    opaque_identity=evidence.opaque_identity,
                    workspace_generation=evidence.workspace_generation,
                    run_fencing_token=evidence.run_fencing_token,
                    node_fencing_token=evidence.node_fencing_token,
                    certificate_thumbprint=evidence.certificate_thumbprint,
                ),
                issuer_context=TrustedIssuerContext(
                    tenant_id=evidence.tenant_id,
                    system_actor_id=binding.system_actor_id,
                    originating_user_id=binding.originating_user_id,
                ),
                ttl=min(timedelta(minutes=5), peer_ttl),
            )
        except (CapabilityVerificationError, GatewayCredentialUnavailable, TypeError, ValueError):
            await _json_response(send, 401, {"error": {"code": "credential_rejected"}})
            return
        await _json_response(
            send,
            200,
            {
                "authorization_scheme": "Capability",
                "token": credential.token,
                "opaque_identity": credential.opaque_identity,
                "expires_at": credential.expires_at.isoformat(),
            },
        )


async def _json_response(send: Send, status: int, payload: dict[str, object]) -> None:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def build_mtls_gateway(config: GatewayServerConfig) -> VerifiedMtlsGatewayIngress:
    secret = config.cursor_secret_file.read_bytes()
    if len(secret) < 32 or len(secret) > 4096:
        raise ValueError("Gateway cursor secret must contain between 32 and 4096 bytes")
    session_factory = get_session_factory()
    attestor = SqlAlchemyRunLeaseWorkloadAttestor(session_factory)
    app = create_production_gateway_app(
        workload_attestor=attestor,
        cursor_secret=secret,
    )
    vending = GatewayCredentialVendingApp(
        app,
        attestor=attestor,
        issuer=SqlAlchemyGatewayCredentialIssuer(
            session_factory,
            FileCapabilityPrivateKeyProvider(config.signing_private_key),
        ),
    )
    return VerifiedMtlsGatewayIngress(vending, JsonMtlsPeerRegistry(config.peer_registry))


def run(config: GatewayServerConfig | None = None) -> None:
    resolved = config or GatewayServerConfig.from_environment()
    server_config = HardenedGatewayUvicornConfig(
        build_mtls_gateway(resolved),
        host=resolved.host,
        port=resolved.port,
        http=VerifiedMtlsH11Protocol,
        ws="none",
        proxy_headers=False,
        server_header=False,
        date_header=False,
        access_log=False,
        ssl_certfile=str(resolved.server_certificate),
        ssl_keyfile=str(resolved.server_private_key),
        ssl_ca_certs=str(resolved.client_ca),
        ssl_cert_reqs=ssl.CERT_REQUIRED,
        timeout_keep_alive=5,
        limit_concurrency=128,
    )
    uvicorn.Server(server_config).run()


if __name__ == "__main__":
    run()


__all__ = [
    "FileCapabilityPrivateKeyProvider",
    "GatewayCredentialVendingApp",
    "GatewayServerConfig",
    "HardenedGatewayUvicornConfig",
    "build_mtls_gateway",
    "run",
]
