# OmniBase Python SDK

This package is the project-local client for the isolated OmniBase workload
gateway. It intentionally exposes only four P34.2 read capabilities:

- `data.schema.read`
- `data.rows.read`
- `rag.search`
- `rag.citation.read`

The SDK accepts logical resource and column IDs only. It never accepts SQL,
tenant schema names, physical table names, object-store keys, provider handles,
or user JWTs.

```python
from datetime import datetime, timedelta, timezone

from omnibase_sdk import (
    OmniBaseClient,
    StaticCredentialProvider,
    WorkloadCredential,
)

# StaticCredentialProvider is intended for tests and short-lived process memory.
# Production runtimes should implement WorkloadCredentialProvider and fetch a
# fresh capability immediately before each request.
credential = WorkloadCredential(
    token=short_lived_token,
    workload_identity=runtime_instance_id,
    expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
)
client = OmniBaseClient.from_http(
    "https://gateway.internal",
    credential_provider=StaticCredentialProvider(credential),
    ssl_context=workload_mtls_context,
)
schema = client.read_schema(resource_id)
```

The `Authorization` scheme is `Capability`, never `Bearer`. Mutual TLS is
configured on the HTTP transport through an `ssl.SSLContext`; no certificate,
private key, capability token, PAT, or user JWT is persisted by this package.
`workload_identity` is only a non-secret correlation identifier. It is not
proof of identity. The gateway obtains the certificate thumbprint and runtime
scope exclusively from its trusted mTLS/runner attestor; a client-supplied
certificate or thumbprint header is never accepted.
