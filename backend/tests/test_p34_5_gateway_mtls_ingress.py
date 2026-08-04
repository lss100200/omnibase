"""P34.5D server-owned mTLS Gateway ingress tests."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from omnibase.capability_gateway import mtls_ingress
from omnibase.capability_gateway.mtls_ingress import (
    JsonMtlsPeerRegistry,
    MtlsIngressRejected,
    VerifiedMtlsGatewayIngress,
)
from omnibase.capability_gateway.thumbprints import certificate_thumbprint_to_x5t_s256

TENANT = "10000000-0000-0000-0000-000000000001"
WORKSPACE = "20000000-0000-0000-0000-000000000001"
RUN = "30000000-0000-0000-0000-000000000001"
RUNTIME = "40000000-0000-0000-0000-000000000001"
NODE = "50000000-0000-0000-0000-000000000001"
LEASE = "60000000-0000-0000-0000-000000000001"
GRANT = "70000000-0000-0000-0000-000000000001"
ACTOR = "80000000-0000-0000-0000-000000000001"
NOW = datetime(2026, 8, 2, 2, 0, tzinfo=UTC)
CERTIFICATE_DER = b"synthetic-client-certificate-der"
THUMBPRINT = hashlib.sha256(CERTIFICATE_DER).hexdigest()


def _registry(path: Path, *, state: str = "active", thumbprint: str = THUMBPRINT) -> Path:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "peers": [
                    {
                        "peer_kind": "runner",
                        "opaque_identity": f"spiffe://omnibase/runtime/{RUNTIME}",
                        "tenant_id": TENANT,
                        "workspace_id": WORKSPACE,
                        "run_id": RUN,
                        "runtime_instance_id": RUNTIME,
                        "node_id": NODE,
                        "lease_id": LEASE,
                        "workspace_generation": 3,
                        "run_fencing_token": 11,
                        "node_fencing_token": 7,
                        "certificate_thumbprint": thumbprint,
                        "expires_at": (NOW + timedelta(minutes=2)).isoformat(),
                        "grant_id": GRANT,
                        "expected_profile": "read",
                        "key_id": "gateway-key-2026-08",
                        "system_actor_id": "gateway-credential-broker",
                        "originating_user_id": ACTOR,
                        "state": state,
                    }
                ],
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    if os.name != "nt":
        path.chmod(0o600)
    return path


def test_registry_promotes_only_matching_active_certificate(tmp_path: Path) -> None:
    registry = JsonMtlsPeerRegistry(_registry(tmp_path / "peers.json"), clock=lambda: NOW)

    evidence = registry.resolve(CERTIFICATE_DER)

    assert evidence.certificate_thumbprint == THUMBPRINT
    assert evidence.opaque_identity == f"spiffe://omnibase/runtime/{RUNTIME}"
    assert evidence.evidence_digest != THUMBPRINT
    with pytest.raises(MtlsIngressRejected):
        registry.resolve(b"different-certificate")


def test_registry_reload_applies_revocation_without_process_restart(tmp_path: Path) -> None:
    path = _registry(tmp_path / "peers.json")
    registry = JsonMtlsPeerRegistry(path, clock=lambda: NOW)
    registry.resolve(CERTIFICATE_DER)

    _registry(path, state="revoked")

    with pytest.raises(MtlsIngressRejected):
        registry.resolve(CERTIFICATE_DER)


def test_headers_and_cookie_cannot_forge_transport_certificate(tmp_path: Path) -> None:
    inner = FastAPI()

    @inner.get("/")
    def trusted() -> dict[str, bool]:
        return {"trusted": True}

    app = VerifiedMtlsGatewayIngress(
        inner, JsonMtlsPeerRegistry(_registry(tmp_path / "peers.json"), clock=lambda: NOW)
    )
    with TestClient(app) as client:
        response = client.get(
            "/",
            headers={
                "X-Omnibase-Mtls-Verified": "true",
                "X-Omnibase-Workload-Cert-Sha256": THUMBPRINT,
                "X-Omnibase-Tenant-Id": TENANT,
                "Cookie": "omnibase.mtls_verified=true",
            },
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_mtls_peer"


def test_transport_certificate_injects_server_owned_evidence_and_strips_reserved_headers(
    tmp_path: Path,
) -> None:
    inner = FastAPI()

    @inner.get("/")
    def trusted(request: Request) -> dict[str, object]:
        evidence = request.scope["omnibase.trusted_gateway_peer"]
        headers = {key.decode(): value.decode() for key, value in request.scope["headers"]}
        return {
            "mtls_verified": request.scope["omnibase.mtls_verified"],
            "tenant_id": evidence.tenant_id,
            "reserved_present": "x-omnibase-tenant-id" in headers,
        }

    ingress = VerifiedMtlsGatewayIngress(
        inner, JsonMtlsPeerRegistry(_registry(tmp_path / "peers.json"), clock=lambda: NOW)
    )

    async def transport(scope, receive, send) -> None:
        scope[mtls_ingress._TRANSPORT_CERTIFICATE_KEY] = CERTIFICATE_DER
        await ingress(scope, receive, send)

    with TestClient(transport) as client:
        response = client.get("/", headers={"X-Omnibase-Tenant-Id": "attacker-controlled"})

    assert response.status_code == 200
    assert response.json() == {
        "mtls_verified": True,
        "tenant_id": TENANT,
        "reserved_present": False,
    }


def test_hex_certificate_digest_converts_to_jwt_x5t_s256() -> None:
    encoded = certificate_thumbprint_to_x5t_s256(THUMBPRINT)
    assert len(encoded) == 43
    assert "=" not in encoded
    with pytest.raises(ValueError):
        certificate_thumbprint_to_x5t_s256("A" * 64)


@pytest.mark.asyncio
async def test_non_http_scope_cannot_bypass_mtls_ingress(tmp_path: Path) -> None:
    inner_called = False
    messages: list[dict[str, object]] = []

    async def inner(scope, receive, send) -> None:
        nonlocal inner_called
        inner_called = True

    async def receive() -> dict[str, object]:
        return {"type": "websocket.connect"}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    ingress = VerifiedMtlsGatewayIngress(
        inner, JsonMtlsPeerRegistry(_registry(tmp_path / "peers.json"), clock=lambda: NOW)
    )
    await ingress({"type": "websocket"}, receive, send)  # type: ignore[arg-type]
    assert inner_called is False
    assert messages == [{"type": "websocket.close", "code": 4401}]
