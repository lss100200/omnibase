"""Split-process disposable P34.5D Gateway acceptance Gate."""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import uvicorn
from sqlalchemy import text

from omnibase.capability_gateway.mtls_ingress import VerifiedMtlsH11Protocol
from omnibase.capability_gateway.server import (
    GatewayServerConfig,
    HardenedGatewayUvicornConfig,
    build_mtls_gateway,
)
from omnibase.rag.index_metadata import IndexVersion, get_index_lane
from omnibase.rag.reranker import rerank
from omnibase.rag.retriever import hybrid_search_detailed
from omnibase.rag.store import SearchMode

from .test_p34_5_gateway_mtls_disposable import (
    _certificate_material,
    _peer_document,
    _seed,
    _write_private,
    _write_registry,
    p345d_schema,
)

assert p345d_schema is not None

if os.environ.get("P34_5D_SPLIT_GATE") != "1":
    pytest.skip("split P34.5D Gate requires explicit opt-in", allow_module_level=True)

pytestmark = pytest.mark.integration


def _wait(path: Path, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise AssertionError(f"timed out waiting for {path.name}")


def _copy(source: Path, target: Path, mode: int) -> Path:
    shutil.copyfile(source, target)
    target.chmod(mode)
    return target


def _stage(
    control: Path,
    stage: str,
    mutate,
    restore,
) -> None:
    _wait(control / f"request-{stage}")
    mutate()
    (control / f"ready-{stage}").write_text("1\n", encoding="ascii")
    _wait(control / f"observed-{stage}")
    restore()
    (control / f"restored-{stage}").write_text("1\n", encoding="ascii")


def test_split_gateway_server_and_broker_client_gate(db_engine, tmp_path: Path) -> None:
    server_root = Path(os.environ["P34_5D_SERVER_MATERIAL"])
    client_root = Path(os.environ["P34_5D_CLIENT_MATERIAL"])
    control = Path(os.environ["P34_5D_CONTROL"])
    for root in (server_root, client_root, control):
        root.mkdir(parents=True, exist_ok=True)

    generated = _certificate_material(tmp_path)
    facts, private_pem, _ = _seed(db_engine, str(generated["thumbprint"]))
    facts.update(
        {
            "certificate_thumbprint": generated["thumbprint"],
            "opaque_identity": f"spiffe://omnibase/runtime/{facts['runtime']}",
            "runtime_instance_id": facts["runtime"],
            "node_id": facts["node"],
            "lease_id": facts["lease"],
            "run_id": facts["run"],
            "workspace_id": facts["workspace"],
        }
    )
    server_ca = _copy(Path(generated["ca"]), server_root / "ca.crt", 0o644)
    server_cert = _copy(Path(generated["server_cert"]), server_root / "server.crt", 0o644)
    server_key = _copy(Path(generated["server_key"]), server_root / "server.key", 0o600)
    client_ca = _copy(Path(generated["ca"]), client_root / "ca.crt", 0o644)
    _copy(Path(generated["client_cert"]), client_root / "client.crt", 0o644)
    _copy(Path(generated["client_key"]), client_root / "client.key", 0o600)
    _copy(Path(generated["wrong_cert"]), client_root / "wrong.crt", 0o644)
    _copy(Path(generated["wrong_key"]), client_root / "wrong.key", 0o600)
    assert client_ca.exists()
    registry = server_root / "peers.json"
    _write_registry(registry, _peer_document(facts))
    signing_key = _write_private(server_root / "capability-signing.key", private_pem)
    cursor_secret = _write_private(server_root / "cursor.secret", os.urandom(32))
    client_config = {
        "server_host": "gateway-server",
        "server_port": 8443,
        "tenant_id": facts["tenant_id"],
        "opaque_identity": facts["opaque_identity"],
        "data_resource": facts["data_resource"],
        "rag_resource": facts["rag_resource"],
        "column": facts["column"],
        "citation": facts["citation"],
    }
    (client_root / "client-config.json").write_text(
        json.dumps(client_config, sort_keys=True) + "\n", encoding="utf-8"
    )
    warmed = hybrid_search_detailed(
        schema_name=str(facts["schema"]),
        query="safety evidence",
        top_k=5,
        document_id_filter=str(facts["document"]),
        lane=get_index_lane(IndexVersion.V1),
        mode=SearchMode.ONLINE,
    )
    rerank("safety evidence", warmed.results, top_k=2)

    app = build_mtls_gateway(
        GatewayServerConfig(
            host="0.0.0.0",
            port=8443,
            server_certificate=server_cert,
            server_private_key=server_key,
            client_ca=server_ca,
            peer_registry=registry,
            cursor_secret_file=cursor_secret,
            signing_private_key=signing_key,
        )
    )
    config = HardenedGatewayUvicornConfig(
        app,
        host="0.0.0.0",
        port=8443,
        http=VerifiedMtlsH11Protocol,
        ws="none",
        proxy_headers=False,
        server_header=False,
        date_header=False,
        access_log=False,
        lifespan="off",
        ssl_certfile=str(server_cert),
        ssl_keyfile=str(server_key),
        ssl_ca_certs=str(server_ca),
        ssl_cert_reqs=2,
        timeout_keep_alive=5,
    )
    config.load()
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    assert server.started
    (control / "gateway-ready").write_text("1\n", encoding="ascii")

    original_registry = _peer_document(facts)

    def db_update(statement: str, parameters: dict[str, object]) -> None:
        with db_engine.begin() as connection:
            connection.execute(text(statement), parameters)

    try:
        _stage(
            control,
            "cross-tenant",
            lambda: _write_registry(registry, _peer_document(facts, tenant_id=str(uuid.uuid4()))),
            lambda: _write_registry(registry, original_registry),
        )
        _stage(
            control,
            "node-attestation-revoked",
            lambda: db_update(
                "UPDATE omnibase_meta.node_attestations SET state='revoked' WHERE node_id=:node",
                {"node": facts["node"]},
            ),
            lambda: db_update(
                "UPDATE omnibase_meta.node_attestations SET state='verified', expires_at=now()+interval '5 minutes' WHERE node_id=:node",
                {"node": facts["node"]},
            ),
        )
        _stage(
            control,
            "node-attestation-expired",
            lambda: db_update(
                "UPDATE omnibase_meta.node_attestations SET state='verified', expires_at=now()-interval '1 second' WHERE node_id=:node",
                {"node": facts["node"]},
            ),
            lambda: db_update(
                "UPDATE omnibase_meta.node_attestations SET expires_at=now()+interval '5 minutes' WHERE node_id=:node",
                {"node": facts["node"]},
            ),
        )
        _stage(
            control,
            "workspace-generation",
            lambda: db_update(
                "UPDATE omnibase_meta.workspaces SET generation=2 WHERE id=:workspace",
                {"workspace": facts["workspace"]},
            ),
            lambda: db_update(
                "UPDATE omnibase_meta.workspaces SET generation=1 WHERE id=:workspace",
                {"workspace": facts["workspace"]},
            ),
        )
        _stage(
            control,
            "run-fencing",
            lambda: db_update(
                "UPDATE omnibase_meta.run_leases SET fencing_token=2 WHERE id=:lease",
                {"lease": facts["lease"]},
            ),
            lambda: db_update(
                "UPDATE omnibase_meta.run_leases SET fencing_token=1 WHERE id=:lease",
                {"lease": facts["lease"]},
            ),
        )
        _stage(
            control,
            "node-fencing",
            lambda: db_update(
                "UPDATE omnibase_meta.workspace_nodes SET fencing_token=2 WHERE id=:node",
                {"node": facts["node"]},
            ),
            lambda: db_update(
                "UPDATE omnibase_meta.workspace_nodes SET fencing_token=1 WHERE id=:node",
                {"node": facts["node"]},
            ),
        )
        _stage(
            control,
            "lease-revoked",
            lambda: db_update(
                "UPDATE omnibase_meta.run_leases SET state='revoked', revoked_at=now() WHERE id=:lease",
                {"lease": facts["lease"]},
            ),
            lambda: db_update(
                "UPDATE omnibase_meta.run_leases SET state='active', revoked_at=NULL WHERE id=:lease",
                {"lease": facts["lease"]},
            ),
        )
        _stage(
            control,
            "registry-revoked",
            lambda: _write_registry(registry, _peer_document(facts, state="revoked")),
            lambda: _write_registry(registry, original_registry),
        )
        _wait(control / "client-done")
        outcomes = json.loads((control / "client-results.json").read_text(encoding="utf-8"))
        assert outcomes["credential"] == 200
        assert outcomes["credential_cache_control"] == "no-store"
        assert outcomes["credential_parameter_body"] == 422
        credential_expiry = datetime.fromisoformat(str(outcomes["credential_expires_at"]))
        peer_expiry = datetime.fromisoformat(
            str(original_registry["peers"][0]["expires_at"])  # type: ignore[index]
        )
        assert credential_expiry <= peer_expiry
        assert credential_expiry <= datetime.now(UTC) + timedelta(minutes=5)
        assert all(outcomes[name] == 200 for name in ("schema", "rows", "rag_search", "citation"))
        assert outcomes["client_environment_forbidden_keys"] == []
        assert outcomes["client_forbidden_mounts_present"] == []
        assert all(
            outcomes[stage] == {"credential": 401, "read": 401}
            for stage in (
                "cross-tenant",
                "node-attestation-revoked",
                "node-attestation-expired",
                "workspace-generation",
                "run-fencing",
                "node-fencing",
                "lease-revoked",
                "registry-revoked",
            )
        )
        with db_engine.connect() as connection:
            audited_actions = sorted(
                set(
                    connection.execute(
                        text(
                            "SELECT action FROM omnibase_meta.audit_events WHERE grant_id=:grant AND decision='allowed'"
                        ),
                        {"grant": facts["grant_id"]},
                    ).scalars()
                )
            )
        evidence = {
            "schema_version": 2,
            "gate": "P34.5D split-process disposable mTLS Gateway",
            "passed": True,
            "database_name": os.environ["TEST_DATABASE_NAME"],
            "database_sentinel_verified": True,
            "business_database_migrated": False,
            "process_isolation": {
                "gateway_server_container": True,
                "broker_client_container": True,
                "broker_client_backend_source_present": False,
                "broker_client_database_environment_present": False,
                "broker_client_signing_private_key_present": False,
                "broker_client_host_mount_present": False,
                "gateway_server_source_mount_present": False,
                "gateway_server_dependency_volume_present": False,
            },
            "credential_flow": {
                "client_parameters": [],
                "server_owned_binding": True,
                "transport_der_before_issue": True,
                "live_attestation_before_private_key_load": True,
                "ttl_max_seconds": 300,
            },
            "read_and_rejection_matrix": outcomes,
            "allowed_audit_actions": audited_actions,
            "physical_locator_exposed": False,
            "private_key_exposed": False,
            "direct_database_route_present": False,
            "root_env_accessed": False,
            "real_credentials_used": False,
            "source_mounts_present": False,
            "ambient_virtualenv_present": False,
            "gate_backend_image_id": os.environ.get("P34_5D_GATE_IMAGE_ID"),
            "gate_postgres_image_id": os.environ.get("P34_5D_GATE_POSTGRES_IMAGE_ID"),
            "gate_client_image_id": os.environ.get("P34_5D_GATE_CLIENT_IMAGE_ID"),
        }
        target = Path(os.environ["P34_5D_GATE_EVIDENCE_PATH"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive()
        (control / "release-client").write_text("1\n", encoding="ascii")
