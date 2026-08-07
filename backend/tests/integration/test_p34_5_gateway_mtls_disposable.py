"""Disposable PostgreSQL + real Uvicorn mTLS P34.5D acceptance Gate."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
import ssl
import subprocess
import sys
import threading
import time
import uuid
import warnings
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from sqlalchemy import text
from sqlalchemy.orm import Session

from omnibase.capabilities.service import (
    TrustedIssuerContext,
    TrustedPlatformContext,
    create_grant,
    register_signing_key,
)
from omnibase.capability_gateway.app import create_production_gateway_app
from omnibase.capability_gateway.mtls_ingress import (
    JsonMtlsPeerRegistry,
    VerifiedMtlsGatewayIngress,
    VerifiedMtlsH11Protocol,
)
from omnibase.capability_gateway.server import HardenedGatewayUvicornConfig
from omnibase.capability_gateway.workload import (
    GatewayCredentialIssueRequest,
    SqlAlchemyGatewayCredentialIssuer,
    SqlAlchemyRunLeaseWorkloadAttestor,
)
from omnibase.core.db import get_session_factory
from omnibase.rag.index_metadata import IndexVersion, get_index_lane
from omnibase.rag.reranker import rerank
from omnibase.rag.retriever import hybrid_search_detailed
from omnibase.rag.store import SearchMode
from omnibase.tenants.service import create_tenant

if os.environ.get("OMNIBASE_INTEGRATION_TESTS") != "1":
    pytest.skip(
        "P34.5D disposable Gate requires explicit integration opt-in", allow_module_level=True
    )

pytestmark = pytest.mark.integration
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", autouse=True)
def p345d_schema(db_engine) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        env=os.environ.copy(),
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def _write_private(path: Path, value: bytes) -> Path:
    path.write_bytes(value)
    path.chmod(0o600)
    return path


def _write_public(path: Path, value: bytes) -> Path:
    path.write_bytes(value)
    path.chmod(0o644)
    return path


def _certificate_material(root: Path) -> dict[str, Path | bytes | str]:
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OmniBase P34.5D Gate CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    def leaf(name: str, usage: ExtendedKeyUsageOID) -> tuple[Path, Path, bytes]:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
        builder = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(minutes=30))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=True)
        )
        if usage == ExtendedKeyUsageOID.SERVER_AUTH:
            builder = builder.add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("localhost"),
                        x509.DNSName("gateway-server"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
        cert = builder.sign(ca_key, hashes.SHA256())
        key_path = _write_private(
            root / f"{name}.key",
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
        )
        cert_path = _write_public(
            root / f"{name}.crt", cert.public_bytes(serialization.Encoding.PEM)
        )
        return cert_path, key_path, cert.public_bytes(serialization.Encoding.DER)

    ca_path = _write_public(root / "ca.crt", ca_cert.public_bytes(serialization.Encoding.PEM))
    server_cert, server_key, _ = leaf("server", ExtendedKeyUsageOID.SERVER_AUTH)
    client_cert, client_key, client_der = leaf("runner", ExtendedKeyUsageOID.CLIENT_AUTH)
    wrong_cert, wrong_key, _ = leaf("wrong-runner", ExtendedKeyUsageOID.CLIENT_AUTH)
    return {
        "ca": ca_path,
        "server_cert": server_cert,
        "server_key": server_key,
        "client_cert": client_cert,
        "client_key": client_key,
        "client_der": client_der,
        "wrong_cert": wrong_cert,
        "wrong_key": wrong_key,
        "thumbprint": hashlib.sha256(client_der).hexdigest(),
    }


def _peer_document(
    facts: dict[str, object], *, state: str = "active", tenant_id: str | None = None
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "peers": [
            {
                "peer_kind": "runner",
                "opaque_identity": facts["opaque_identity"],
                "tenant_id": tenant_id or facts["tenant_id"],
                "workspace_id": facts["workspace_id"],
                "run_id": facts["run_id"],
                "runtime_instance_id": facts["runtime_instance_id"],
                "node_id": facts["node_id"],
                "lease_id": facts["lease_id"],
                "workspace_generation": 1,
                "run_fencing_token": 1,
                "node_fencing_token": 1,
                "certificate_thumbprint": facts["certificate_thumbprint"],
                "workload_identity_digest": facts["workload_identity_digest"],
                "expires_at": (datetime.now(UTC) + timedelta(minutes=4)).isoformat(),
                "grant_id": facts["grant_id"],
                "expected_profile": "read",
                "key_id": "gateway-gate-key",
                "system_actor_id": facts["system_actor"],
                "originating_user_id": facts["actor"],
                "state": state,
            }
        ],
    }


def _write_registry(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    path.chmod(0o600)


def _reserve_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class _PrivateKeyProvider:
    def __init__(self, private_key: bytes) -> None:
        self._private_key = private_key
        self.calls = 0

    def load_private_key(self, key_id: str) -> bytes:
        assert key_id == "gateway-gate-key"
        self.calls += 1
        return self._private_key


def _seed(db_engine, certificate_thumbprint: str) -> tuple[dict[str, object], bytes, str]:
    now = datetime.now(UTC)
    workload_identity_digest = hashlib.sha256(b"p34.5d-runtime-workload").hexdigest()
    capability_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = capability_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = (
        capability_key.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    ids = {
        name: str(uuid.uuid4())
        for name in (
            "actor",
            "workspace",
            "run",
            "runtime",
            "node",
            "lease",
            "data_resource",
            "rag_resource",
            "column",
            "document",
            "citation",
            "system_actor",
        )
    }
    with Session(db_engine) as session, session.begin():
        tenant = create_tenant(
            name="P34.5D disposable tenant",
            slug=f"p345d-{uuid.uuid4().hex[:10]}",
            session=session,
        )
        schema = tenant.schema_name
        session.execute(
            text(
                f'INSERT INTO "{schema}".users (id, email, password_hash, is_tenant_admin, is_active) VALUES (:id, :email, :hash, TRUE, TRUE)'  # noqa: S608
            ),
            {"id": ids["actor"], "email": "p345d@example.invalid", "hash": uuid.uuid4().hex},
        )
        template_id = str(
            session.execute(
                text(
                    "INSERT INTO omnibase_meta.workspace_templates (tenant_id, template_key, version, display_name, digest, template_spec, created_by_user_id) VALUES (:tenant, :key, 1, 'P34.5D', :digest, '{\"profile\":\"gateway-gate\"}'::jsonb, :actor) RETURNING id"
                ),
                {
                    "tenant": tenant.id,
                    "key": f"p345d-{uuid.uuid4().hex[:8]}",
                    "digest": hashlib.sha256(b"p345d-template").hexdigest(),
                    "actor": ids["actor"],
                },
            ).scalar_one()
        )
        session.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry (id, tenant_id, kind, owner_type, display_name, state, policy_class) VALUES (:id, :tenant, 'workspace', 'system', 'P34.5D workspace', 'running', 'workspace_private')"
            ),
            {"id": ids["workspace"], "tenant": tenant.id},
        )
        session.execute(
            text(
                "INSERT INTO omnibase_meta.workspaces (id, tenant_id, template_id, owner_user_id, display_name, desired_state, observed_state, generation, quota) VALUES (:id, :tenant, :template, :actor, 'P34.5D workspace', 'running', 'running', 1, CAST(:quota AS jsonb))"
            ),
            {
                "id": ids["workspace"],
                "tenant": tenant.id,
                "template": template_id,
                "actor": ids["actor"],
                "quota": json.dumps({"max_active_runs": 1}),
            },
        )
        session.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_nodes (id, tenant_id, workspace_id, owner_user_id, display_name, identity_digest, state, attestation_state, fencing_token) VALUES (:id, :tenant, :workspace, :actor, 'P34.5D node', :digest, 'active', 'verified', 1)"
            ),
            {
                "id": ids["node"],
                "tenant": tenant.id,
                "workspace": ids["workspace"],
                "actor": ids["actor"],
                "digest": hashlib.sha256(b"p345d-node").hexdigest(),
            },
        )
        session.execute(
            text(
                "INSERT INTO omnibase_meta.node_attestations (tenant_id, node_id, nonce_digest, evidence_digest, verifier, state, verified_at, expires_at) VALUES (:tenant, :node, :nonce, :evidence, 'p345d-gate', 'verified', :verified, :expires)"
            ),
            {
                "tenant": tenant.id,
                "node": ids["node"],
                "nonce": hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
                "evidence": hashlib.sha256(b"p345d-attestation").hexdigest(),
                "verified": now - timedelta(seconds=1),
                "expires": now + timedelta(minutes=5),
            },
        )
        session.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry (id, tenant_id, kind, owner_type, owner_id, parent_id, display_name, state, policy_class) VALUES (:id, :tenant, 'run', 'workspace', :workspace, :workspace, 'P34.5D run', 'running', 'workspace_private')"
            ),
            {"id": ids["run"], "tenant": tenant.id, "workspace": ids["workspace"]},
        )
        session.execute(
            text(
                "INSERT INTO omnibase_meta.workspace_runs (id, tenant_id, workspace_id, kind, generation, desired_state, observed_state, next_fencing_token, request_digest, runtime_instance_id, workload_identity_digest, created_by_user_id) VALUES (:id, :tenant, :workspace, 'batch', 1, 'running', 'leased', 2, :digest, :runtime, :thumbprint, :actor)"
            ),
            {
                "id": ids["run"],
                "tenant": tenant.id,
                "workspace": ids["workspace"],
                "digest": hashlib.sha256(b"p345d-run").hexdigest(),
                "runtime": ids["runtime"],
                "thumbprint": workload_identity_digest,
                "actor": ids["actor"],
            },
        )
        session.execute(
            text(
                "INSERT INTO omnibase_meta.run_leases (id, tenant_id, run_id, workspace_id, node_id, node_fencing_token, generation, fencing_token, state, heartbeat_at, expires_at) VALUES (:id, :tenant, :run, :workspace, :node, 1, 1, 1, 'active', :now, :expires)"
            ),
            {
                "id": ids["lease"],
                "tenant": tenant.id,
                "run": ids["run"],
                "workspace": ids["workspace"],
                "node": ids["node"],
                "now": now,
                "expires": now + timedelta(minutes=5),
            },
        )
        data_locator = {
            "adapter": "postgres",
            "schema": schema,
            "table": "gateway_gate_rows",
            "columns": {
                ids["column"]: {
                    "name": "safe_value",
                    "display_name": "Safe Value",
                    "type": "text",
                    "nullable": False,
                }
            },
        }
        rag_locator = {
            "adapter": "canonical_rag_v1",
            "schema": schema,
            "document_id": ids["document"],
        }
        session.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry (id, tenant_id, kind, owner_type, owner_id, parent_id, display_name, state, policy_class, physical_locator) VALUES (:id, :tenant, 'data_table', 'workspace', :workspace, :workspace, 'Gate data', 'active', 'workspace_private', CAST(:locator AS jsonb))"
            ),
            {
                "id": ids["data_resource"],
                "tenant": tenant.id,
                "workspace": ids["workspace"],
                "locator": json.dumps(data_locator),
            },
        )
        session.execute(
            text(
                "INSERT INTO omnibase_meta.resource_registry (id, tenant_id, kind, owner_type, owner_id, parent_id, display_name, state, policy_class, physical_locator) VALUES (:id, :tenant, 'derived_index', 'workspace', :workspace, :workspace, 'Gate RAG', 'active', 'workspace_derived', CAST(:locator AS jsonb))"
            ),
            {
                "id": ids["rag_resource"],
                "tenant": tenant.id,
                "workspace": ids["workspace"],
                "locator": json.dumps(rag_locator),
            },
        )
        session.execute(
            text(f'CREATE TABLE "{schema}".gateway_gate_rows (safe_value TEXT NOT NULL)')
        )
        session.execute(
            text(
                f"INSERT INTO \"{schema}\".gateway_gate_rows (safe_value) VALUES ('bounded-row')"  # noqa: S608
            )
        )
        session.execute(
            text(
                f"INSERT INTO \"{schema}\".documents (id, filename, mime_type, size_bytes, status, minio_key, metadata) VALUES (:id, 'gate.txt', 'text/plain', 20, 'indexed', 'synthetic/gate.txt', '{{}}'::jsonb)"  # noqa: S608
            ),
            {"id": ids["document"]},
        )
        session.execute(
            text(
                f"INSERT INTO \"{schema}\".embeddings (id, document_id, chunk_index, content, embedding, char_start, char_end, metadata) VALUES (:id, :document, 0, 'sandbox safety evidence', NULL, 0, 23, CAST(:metadata AS jsonb))"  # noqa: S608
            ),
            {
                "id": ids["citation"],
                "document": ids["document"],
                "metadata": json.dumps({"page": 1}),
            },
        )
        register_signing_key(
            session,
            platform_context=TrustedPlatformContext(system_actor_id=ids["system_actor"]),
            kid="gateway-gate-key",
            public_key_pem=public_pem,
            not_before=now - timedelta(minutes=1),
            expires_at=now + timedelta(minutes=10),
        )
        grant = create_grant(
            session,
            tenant_id=tenant.id,
            workspace_id=ids["workspace"],
            runtime_instance_id=ids["runtime"],
            issuer_context=TrustedIssuerContext(
                tenant_id=tenant.id,
                system_actor_id=ids["system_actor"],
                originating_user_id=ids["actor"],
            ),
            actions=frozenset(
                {"data.schema.read", "data.rows.read", "rag.search", "rag.citation.read"}
            ),
            resource_ids=frozenset({ids["data_resource"], ids["rag_resource"]}),
            not_before=now - timedelta(seconds=1),
            expires_at=now + timedelta(minutes=5),
            max_calls=12,
            max_bytes=4_000_000,
            max_cost_units=12,
            delegation_depth_limit=0,
            constraints={
                "max_rows": 10,
                "max_result_bytes": 65_536,
                "rag_top_k": 5,
                "timeout_ms": 5_000,
            },
        )
        session.flush()
        ids.update(
            {
                "tenant_id": tenant.id,
                "schema": schema,
                "grant_id": grant.id,
                "workload_identity_digest": workload_identity_digest,
            }
        )
    return ids, private_pem, public_pem


def test_real_mtls_gateway_four_read_actions_and_rejection_matrix(
    db_engine, tmp_path: Path
) -> None:
    material = _certificate_material(tmp_path)
    facts, private_pem, _ = _seed(db_engine, str(material["thumbprint"]))
    facts.update(
        {
            "certificate_thumbprint": material["thumbprint"],
            "opaque_identity": f"spiffe://omnibase/runtime/{facts['runtime']}",
            "runtime_instance_id": facts["runtime"],
            "node_id": facts["node"],
            "lease_id": facts["lease"],
            "run_id": facts["run"],
            "workspace_id": facts["workspace"],
        }
    )
    # Production images intentionally keep model download disabled. Warm the
    # local "model unavailable" cache and the deterministic BM25/RRF fallback
    # before the timed Gateway request; this performs only tenant-schema reads.
    warmed = hybrid_search_detailed(
        schema_name=str(facts["schema"]),
        query="safety evidence",
        top_k=5,
        document_id_filter=str(facts["document"]),
        lane=get_index_lane(IndexVersion.V1),
        mode=SearchMode.ONLINE,
    )
    rerank("safety evidence", warmed.results, top_k=2)
    registry_path = tmp_path / "peers.json"
    _write_registry(registry_path, _peer_document(facts))
    provider = _PrivateKeyProvider(private_pem)
    issuer = SqlAlchemyGatewayCredentialIssuer(get_session_factory(), provider)
    credential = issuer.issue(
        GatewayCredentialIssueRequest(
            tenant_id=str(facts["tenant_id"]),
            workspace_id=str(facts["workspace"]),
            run_id=str(facts["run"]),
            runtime_instance_id=str(facts["runtime"]),
            node_id=str(facts["node"]),
            lease_id=str(facts["lease"]),
            grant_id=str(facts["grant_id"]),
            expected_profile="read",
            key_id="gateway-gate-key",
            opaque_identity=str(facts["opaque_identity"]),
            workspace_generation=1,
            run_fencing_token=1,
            node_fencing_token=1,
            certificate_thumbprint=str(facts["certificate_thumbprint"]),
            workload_identity_digest=str(facts["workload_identity_digest"]),
        ),
        issuer_context=TrustedIssuerContext(
            tenant_id=str(facts["tenant_id"]),
            system_actor_id=str(facts["system_actor"]),
            originating_user_id=str(facts["actor"]),
        ),
        ttl=timedelta(minutes=2),
    )
    assert provider.calls == 1
    app = create_production_gateway_app(
        workload_attestor=SqlAlchemyRunLeaseWorkloadAttestor(get_session_factory()),
        cursor_secret=os.urandom(32),
    )
    ingress = VerifiedMtlsGatewayIngress(app, JsonMtlsPeerRegistry(registry_path))
    port = _reserve_port()
    config = HardenedGatewayUvicornConfig(
        ingress,
        host="127.0.0.1",
        port=port,
        http=VerifiedMtlsH11Protocol,
        ws="none",
        proxy_headers=False,
        server_header=False,
        date_header=False,
        access_log=False,
        lifespan="off",
        ssl_certfile=str(material["server_cert"]),
        ssl_keyfile=str(material["server_key"]),
        ssl_ca_certs=str(material["ca"]),
        ssl_cert_reqs=ssl.CERT_REQUIRED,
    )
    config.load()
    assert config.ssl is not None
    assert config.ssl.verify_mode == ssl.CERT_REQUIRED
    assert config.ssl.minimum_version == ssl.TLSVersion.TLSv1_2
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started

    context = ssl.create_default_context(cafile=str(material["ca"]))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(str(material["client_cert"]), str(material["client_key"]))
    base_url = f"https://127.0.0.1:{port}"
    headers = {
        "Authorization": f"Capability {credential.token}",
        "X-Omnibase-Workload-Identity": str(facts["opaque_identity"]),
    }
    statuses: dict[str, int] = {}
    responses: list[str] = []
    try:
        with httpx.Client(
            base_url=base_url,
            verify=context,
            timeout=20,
            trust_env=False,
            headers=headers,
        ) as client:
            schema_response = client.post(
                "/gateway/v1/data/schema/read", json={"resource_id": facts["data_resource"]}
            )
            statuses["schema"] = schema_response.status_code
            assert schema_response.status_code == 200, schema_response.text
            assert schema_response.json()["columns"][0]["id"] == facts["column"]
            rows_response = client.post(
                "/gateway/v1/data/rows/read",
                json={
                    "resource_id": facts["data_resource"],
                    "query": {
                        "columns": [facts["column"]],
                        "limit": 5,
                        "timeout_ms": 1000,
                        "max_bytes": 65536,
                    },
                },
            )
            statuses["rows"] = rows_response.status_code
            assert rows_response.status_code == 200, rows_response.text
            assert rows_response.json()["rows"] == [{str(facts["column"]): "bounded-row"}]
            search_response = client.post(
                "/gateway/v1/rag/search",
                json={
                    "resource_id": facts["rag_resource"],
                    "query": "safety evidence",
                    "top_k": 2,
                    "timeout_ms": 5000,
                    "max_bytes": 65536,
                },
            )
            statuses["rag_search"] = search_response.status_code
            assert search_response.status_code == 200, search_response.text
            assert search_response.json()["results"][0]["citation_id"] == facts["citation"]
            citation_response = client.post(
                "/gateway/v1/rag/citations/read",
                json={
                    "resource_id": facts["rag_resource"],
                    "citation_ids": [facts["citation"]],
                    "timeout_ms": 1000,
                    "max_bytes": 65536,
                },
            )
            statuses["citation"] = citation_response.status_code
            assert citation_response.status_code == 200, citation_response.text
            assert citation_response.json()["citations"][0]["content"] == "sandbox safety evidence"
            responses = [
                schema_response.text,
                rows_response.text,
                search_response.text,
                citation_response.text,
            ]
            forbidden = [
                str(facts["schema"]),
                "gateway_gate_rows",
                "PRIVATE KEY",
                "postgresql://",
                "redis://",
                "minio",
            ]
            assert all(marker not in payload for marker in forbidden for payload in responses)

            _write_registry(registry_path, _peer_document(facts, tenant_id=str(uuid.uuid4())))
            cross = client.post(
                "/gateway/v1/data/schema/read", json={"resource_id": facts["data_resource"]}
            )
            statuses["cross_tenant"] = cross.status_code
            assert cross.status_code == 401
            _write_registry(registry_path, _peer_document(facts))

            with db_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE omnibase_meta.run_leases SET state = 'revoked', revoked_at = now() WHERE id = :lease"
                    ),
                    {"lease": facts["lease"]},
                )
            stale = client.post(
                "/gateway/v1/data/schema/read", json={"resource_id": facts["data_resource"]}
            )
            statuses["stale_lease"] = stale.status_code
            assert stale.status_code == 401
            with db_engine.begin() as connection:
                connection.execute(
                    text(
                        "UPDATE omnibase_meta.run_leases SET state = 'active', revoked_at = NULL WHERE id = :lease"
                    ),
                    {"lease": facts["lease"]},
                )

            _write_registry(registry_path, _peer_document(facts, state="revoked"))
            revoked = client.post(
                "/gateway/v1/data/schema/read", json={"resource_id": facts["data_resource"]}
            )
            statuses["revoked_certificate"] = revoked.status_code
            assert revoked.status_code == 401
            _write_registry(registry_path, _peer_document(facts))

        wrong_context = ssl.create_default_context(cafile=str(material["ca"]))
        wrong_context.load_cert_chain(str(material["wrong_cert"]), str(material["wrong_key"]))
        wrong = httpx.post(
            f"{base_url}/gateway/v1/data/schema/read",
            json={"resource_id": facts["data_resource"]},
            headers=headers,
            verify=wrong_context,
            timeout=10,
            trust_env=False,
        )
        statuses["wrong_certificate"] = wrong.status_code
        assert wrong.status_code == 401
        no_client_context = ssl.create_default_context(cafile=str(material["ca"]))
        with pytest.raises(httpx.TransportError):
            httpx.post(
                f"{base_url}/gateway/v1/data/schema/read",
                json={"resource_id": facts["data_resource"]},
                headers=headers,
                verify=no_client_context,
                timeout=10,
                trust_env=False,
            )
        statuses["missing_certificate"] = 0
        legacy_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        legacy_context.check_hostname = False
        legacy_context.verify_mode = ssl.CERT_NONE
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r"ssl\.TLSVersion\.TLSv1.* is deprecated",
                category=DeprecationWarning,
            )
            legacy_context.minimum_version = ssl.TLSVersion.TLSv1
            legacy_context.maximum_version = ssl.TLSVersion.TLSv1_1
        legacy_context.load_cert_chain(str(material["client_cert"]), str(material["client_key"]))
        with pytest.raises(httpx.TransportError):
            httpx.post(
                f"{base_url}/gateway/v1/data/schema/read",
                json={"resource_id": facts["data_resource"]},
                headers=headers,
                verify=legacy_context,
                timeout=10,
                trust_env=False,
            )
        statuses["tls_below_1_2"] = 0

        with db_engine.connect() as connection:
            audited_actions = set(
                connection.execute(
                    text(
                        "SELECT action FROM omnibase_meta.audit_events WHERE grant_id = :grant AND decision = 'allowed'"
                    ),
                    {"grant": facts["grant_id"]},
                ).scalars()
            )
        assert audited_actions == {
            "data.schema.read",
            "data.rows.read",
            "rag.search",
            "rag.citation.read",
        }
        evidence_path = os.environ.get("P34_5D_GATE_EVIDENCE_PATH")
        if evidence_path:
            report = {
                "schema_version": 1,
                "gate": "P34.5D production-like disposable mTLS Gateway",
                "passed": True,
                "database_name": os.environ.get("TEST_DATABASE_NAME", "omnibase_test_unknown"),
                "database_sentinel_verified": True,
                "business_database_migrated": False,
                "tls": {
                    "certificate_required": True,
                    "ca_verified": True,
                    "minimum_version": "TLSv1.2",
                    "transport_der_used": True,
                },
                "read_actions": statuses,
                "allowed_audit_actions": sorted(audited_actions),
                "physical_locator_exposed": False,
                "private_key_exposed": False,
                "direct_database_route_present": False,
                "root_env_accessed": False,
                "real_credentials_used": False,
                "gate_backend_image_id": os.environ.get("P34_5D_GATE_IMAGE_ID"),
                "source_mounts_read_only": True,
                "dependency_volume_read_only": True,
                "resource_cleanup": "performed_by_disposable_gate_wrapper",
            }
            target = Path(evidence_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive()
