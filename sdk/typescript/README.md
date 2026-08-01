# OmniBase TypeScript SDK

This project-local package is the browser-inaccessible client for OmniBase
workloads. It exposes only the P34.2 read actions `data.schema.read`,
`data.rows.read`, `rag.search`, and `rag.citation.read`.

```ts
import { OmniBaseClient } from "@omnibase/sdk";

const client = OmniBaseClient.fromFetch({
  baseUrl: "https://gateway.internal",
  credentialProvider: {
    async getCredential() {
      return runtimeCredentialBroker.getShortLivedCapability();
    },
  },
});

const schema = await client.readSchema(resourceId);
```

`getCredential()` is called immediately before every request. The SDK never
writes capabilities to local storage, files, logs, cookies, environment
variables, or serialized configuration. Production mTLS is terminated by the
gateway/runtime network layer and is not represented as a spoofable public
header. The authorization scheme is `Capability`, never `Bearer`.
The SDK's workload identity value is a non-secret opaque correlation value,
not proof of identity. Certificate thumbprints, tenant scope, workspace scope,
and runtime scope must be injected into the Gateway ASGI context by the trusted
mTLS/runner attestor and can never be asserted by an HTTP request header.
