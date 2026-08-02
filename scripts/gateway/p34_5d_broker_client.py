"""Minimal stdlib-only Broker client for the split P34.5D disposable Gate."""

from __future__ import annotations

import http.client
import json
import os
import ssl
import time
import warnings
from pathlib import Path

MATERIAL = Path(os.environ.get("P34_5D_CLIENT_MATERIAL", "/client-material"))
CONTROL = Path(os.environ.get("P34_5D_CONTROL", "/control"))


def _wait(path: Path, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        time.sleep(0.05)
    raise RuntimeError(f"timed out waiting for {path.name}")


def _context(cert: str | None = "client.crt", key: str | None = "client.key") -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=str(MATERIAL / "ca.crt"))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if cert is not None and key is not None:
        context.load_cert_chain(str(MATERIAL / cert), str(MATERIAL / key))
    return context


def _post_response(
    config: dict[str, object],
    path: str,
    payload: dict[str, object] | None,
    *,
    context: ssl.SSLContext,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object], dict[str, str]]:
    connection = http.client.HTTPSConnection(
        str(config["server_host"]),
        int(config["server_port"]),
        context=context,
        timeout=15,
    )
    body = b"" if payload is None else json.dumps(payload, separators=(",", ":")).encode()
    request_headers = {"content-type": "application/json", **(headers or {})}
    connection.request("POST", path, body=body, headers=request_headers)
    response = connection.getresponse()
    raw = response.read()
    response_headers = {key.lower(): value for key, value in response.getheaders()}
    connection.close()
    document = json.loads(raw.decode("utf-8")) if raw else {}
    return response.status, document, response_headers


def _post(
    config: dict[str, object],
    path: str,
    payload: dict[str, object] | None,
    *,
    context: ssl.SSLContext,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, object]]:
    status, document, _ = _post_response(config, path, payload, context=context, headers=headers)
    return status, document


def _signal(stage: str) -> None:
    (CONTROL / f"request-{stage}").write_text("1\n", encoding="ascii")
    _wait(CONTROL / f"ready-{stage}")


def _observed(stage: str) -> None:
    (CONTROL / f"observed-{stage}").write_text("1\n", encoding="ascii")
    _wait(CONTROL / f"restored-{stage}")


def main() -> int:  # noqa: C901 - explicit split-process attack/rejection matrix
    _wait(CONTROL / "gateway-ready")
    config = json.loads((MATERIAL / "client-config.json").read_text(encoding="utf-8"))
    forbidden_env = sorted(
        name
        for name in os.environ
        if name
        in {
            "DATABASE_URL",
            "TEST_DATABASE_URL",
            "REDIS_URL",
            "MINIO_ACCESS_KEY",
            "MINIO_SECRET_KEY",
            "JWT_SECRET",
            "OMNIBASE_GATEWAY_SIGNING_PRIVATE_KEY",
        }
    )
    if forbidden_env:
        raise RuntimeError("broker client received forbidden infrastructure environment")
    forbidden_files = [
        "/app/src/omnibase",
        "/app/tests",
        "/server-secrets",
        "/var/run/docker.sock",
    ]
    if any(Path(path).exists() for path in forbidden_files):
        raise RuntimeError("broker client received a forbidden source/secret/socket mount")

    context = _context()
    credential_status, credential, credential_headers = _post_response(
        config, "/gateway/v1/credential/read", None, context=context
    )
    if credential_status != 200:
        raise RuntimeError(f"credential vending failed: {credential_status}")
    if set(credential) != {"authorization_scheme", "expires_at", "opaque_identity", "token"}:
        raise RuntimeError("credential response field set is invalid")
    if credential["authorization_scheme"] != "Capability":
        raise RuntimeError("credential scheme is invalid")
    if credential["opaque_identity"] != config["opaque_identity"]:
        raise RuntimeError("credential identity is not bound")
    if credential_headers.get("cache-control") != "no-store":
        raise RuntimeError("credential response is cacheable")
    parameter_status, _ = _post(
        config,
        "/gateway/v1/credential/read",
        {"grant_id": "attacker-controlled"},
        context=context,
    )
    if parameter_status != 422:
        raise RuntimeError("credential vending accepted caller authorization parameters")
    headers = {
        "Authorization": f"Capability {credential['token']}",
        "X-Omnibase-Workload-Identity": str(config["opaque_identity"]),
    }
    outcomes: dict[str, object] = {
        "credential": credential_status,
        "credential_cache_control": credential_headers.get("cache-control"),
        "credential_expires_at": credential["expires_at"],
        "credential_parameter_body": parameter_status,
    }
    requests = [
        ("schema", "/gateway/v1/data/schema/read", {"resource_id": config["data_resource"]}),
        (
            "rows",
            "/gateway/v1/data/rows/read",
            {
                "resource_id": config["data_resource"],
                "query": {
                    "columns": [config["column"]],
                    "limit": 5,
                    "timeout_ms": 1000,
                    "max_bytes": 65536,
                },
            },
        ),
        (
            "rag_search",
            "/gateway/v1/rag/search",
            {
                "resource_id": config["rag_resource"],
                "query": "safety evidence",
                "top_k": 2,
                "timeout_ms": 5000,
                "max_bytes": 65536,
            },
        ),
        (
            "citation",
            "/gateway/v1/rag/citations/read",
            {
                "resource_id": config["rag_resource"],
                "citation_ids": [config["citation"]],
                "timeout_ms": 1000,
                "max_bytes": 65536,
            },
        ),
    ]
    response_documents: list[str] = []
    for name, path, payload in requests:
        status, document = _post(config, path, payload, context=context, headers=headers)
        outcomes[name] = status
        if status != 200:
            raise RuntimeError(f"{name} failed: {status}")
        response_documents.append(json.dumps(document, sort_keys=True))
    forbidden_markers = [
        "tenant_",
        "gateway_gate_rows",
        "PRIVATE KEY",
        "postgresql://",
        "redis://",
        "minio",
    ]
    if any(
        marker and marker in body for marker in forbidden_markers for body in response_documents
    ):
        raise RuntimeError("Gateway response leaked a forbidden physical locator or secret")

    for stage in (
        "cross-tenant",
        "node-attestation-revoked",
        "node-attestation-expired",
        "workspace-generation",
        "run-fencing",
        "node-fencing",
        "lease-revoked",
        "registry-revoked",
    ):
        _signal(stage)
        vend_status, _ = _post(config, "/gateway/v1/credential/read", None, context=context)
        read_status, _ = _post(
            config,
            "/gateway/v1/data/schema/read",
            {"resource_id": config["data_resource"]},
            context=context,
            headers=headers,
        )
        outcomes[stage] = {"credential": vend_status, "read": read_status}
        if vend_status != 401 or read_status != 401:
            raise RuntimeError(f"{stage} did not fail closed")
        _observed(stage)

    wrong_status, _ = _post(
        config,
        "/gateway/v1/credential/read",
        None,
        context=_context("wrong.crt", "wrong.key"),
    )
    outcomes["wrong_certificate"] = wrong_status
    if wrong_status != 401:
        raise RuntimeError("wrong certificate was not rejected")
    try:
        _post(config, "/gateway/v1/credential/read", None, context=_context(None, None))
    except (OSError, ssl.SSLError, http.client.HTTPException):
        outcomes["missing_certificate"] = "tls_rejected"
    else:
        raise RuntimeError("missing certificate reached HTTP")

    spoof_headers = {
        "X-Omnibase-Mtls-Verified": "true",
        "X-Omnibase-Tenant-Id": str(config["tenant_id"]),
    }
    try:
        _post(
            config,
            "/gateway/v1/credential/read",
            None,
            context=_context(None, None),
            headers=spoof_headers,
        )
    except (OSError, ssl.SSLError, http.client.HTTPException):
        outcomes["header_cookie_spoof"] = "tls_rejected"
    else:
        raise RuntimeError("header spoof reached HTTP without a client certificate")

    legacy_context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    legacy_context.check_hostname = False
    legacy_context.verify_mode = ssl.CERT_NONE
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        legacy_context.minimum_version = ssl.TLSVersion.TLSv1
        legacy_context.maximum_version = ssl.TLSVersion.TLSv1_1
    legacy_context.load_cert_chain(str(MATERIAL / "client.crt"), str(MATERIAL / "client.key"))
    try:
        _post(config, "/gateway/v1/credential/read", None, context=legacy_context)
    except (OSError, ssl.SSLError, http.client.HTTPException):
        outcomes["tls_below_1_2"] = "tls_rejected"
    else:
        raise RuntimeError("TLS below 1.2 reached HTTP")

    outcomes["client_environment_forbidden_keys"] = forbidden_env
    outcomes["client_forbidden_mounts_present"] = []
    (CONTROL / "client-results.json").write_text(
        json.dumps(outcomes, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (CONTROL / "client-done").write_text("1\n", encoding="ascii")
    _wait(CONTROL / "release-client")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
