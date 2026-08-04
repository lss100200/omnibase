# P34.5D independent mTLS Capability Gateway

The Gateway is a separate, non-browser ASGI service. Production-style startup
uses `python -m omnibase.capability_gateway.server` with these explicit files:

- server certificate and owner-only private key;
- trusted client CA;
- owner-only server peer registry keyed by client certificate SHA-256;
- owner-only cursor secret file;
- a Core database connection available only to the Gateway process.

The server requires a CA-verified client certificate (`CERT_REQUIRED`), enforces
TLS 1.2 or later, disables WebSocket upgrade, extracts the peer certificate DER
from the Uvicorn/asyncio TLS transport, and creates `TrustedGatewayPeerEvidence`
only after a server-owned registry lookup. Headers and cookies cannot create or
override this evidence. Revocation is applied by changing the registry record to
`revoked`; the registry is reloaded on every request/connection boundary.

Run the isolated acceptance Gate from the repository root:

```text
python scripts/gateway/run_p34_5d_disposable_gate.py --validate-only
python scripts/gateway/run_p34_5d_disposable_gate.py
```

`--validate-only` is non-destructive: it validates the Compose/static
containment contract with a generated disposable env file, creates a source
manifest under the git-ignored `.tmp/p34-5d-gateway/` directory, and does not
build images or start PostgreSQL. The source manifest binds the Gate to the Git
commit/tree, dirty filename scope, and SHA-256/size of every regular file copied
from `backend/src` and `backend/tests`, plus the lock/config, Compose,
Dockerfiles, wrapper, and client. Build-input symlinks fail closed, and cache
files are excluded from both the Docker context and manifest. The root `.env`
is explicitly outside that scope.

The formal wrapper resolves the Python, uv, and PostgreSQL bases to immutable
repository digests. It then builds a dedicated Gateway Gate image from the
clean checkout's `backend/pyproject.toml`, `backend/uv.lock`, migrations,
source, and tests using `uv sync --frozen`; it never reuses
`omnibase-backend:latest` or an external virtualenv. A second minimal image is
built with only the stdlib broker client. The split Gate runs an independent
`gateway-server` and `broker-client`; the client has no backend source,
database/Redis/MinIO environment, signing key, server-secret volume, host bind
mount, or container socket. It receives only its CA/client certificate
material and logical fixture identifiers.

The parameter-free `/gateway/v1/credential/read` transport path is outside the
four data routes. It accepts no grant, key, tenant, user, or actor input from the
client: the mTLS registry owns those bindings. The server first derives the DER
certificate from the TLS transport, reloads the registry, verifies the complete
live Run/Node/Lease/generation/fencing binding, and only then loads the signing
key. The returned read credential is `Cache-Control: no-store`, lasts at most
five minutes, and is additionally clipped to both the live Run Lease and peer
registry evidence expiry.

The Gate creates an `omnibase_test_*` tmpfs PostgreSQL instance and applies
migrations only there. It executes credential vending plus schema, rows, RAG,
and citation reads, then mutates the disposable truth source to verify
cross-tenant, Node attestation revoked/expired, Workspace generation, Run and
Node fencing, Run Lease revocation, registry revocation, wrong/missing client
certificate, header/cookie spoof, and TLS below 1.2 rejection. It writes
redacted evidence, then removes all disposable containers, the internal
network, volumes, and temporary env file. It never reads the repository root
`.env`. The final evidence embeds the exact source manifest and the immutable
base/built image identities so a passing result cannot be detached from the
checkout that produced it.

The Gateway/Sandbox contract never returns PostgreSQL schema/table names,
database/Redis/MinIO locators, signing private keys, or member Overlay identity.
