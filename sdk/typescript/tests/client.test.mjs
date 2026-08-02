import assert from "node:assert/strict";
import test from "node:test";

import { FetchTransport, GatewayError, OmniBaseClient } from "../dist/index.js";

const resourceId = "11111111-1111-4111-8111-111111111111";
const columnId = "22222222-2222-4222-8222-222222222222";
const operationId = "33333333-3333-4333-8333-333333333333";

test("schema read uses frozen path and logical ID only", async () => {
  const calls = [];
  const client = new OmniBaseClient({
    async request(method, path, body) {
      calls.push({ method, path, body });
      return {
        status: 200,
        headers: { "x-request-id": "req-1" },
        body: {
          resource_id: resourceId,
          resource_version: 2,
          columns: [{ id: columnId, display_name: "Title", type: "text", nullable: false }],
        },
      };
    },
  });
  const result = await client.readSchema(resourceId);
  assert.equal(result.resourceVersion, 2);
  assert.deepEqual(calls, [
    { method: "POST", path: "/gateway/v1/data/schema/read", body: { resource_id: resourceId } },
  ]);
});

test("rows query preserves opaque cursor and rejects SQL escape hatch", async () => {
  let sent;
  const client = new OmniBaseClient({
    async request(_method, _path, body) {
      sent = body;
      return {
        status: 200,
        headers: {},
        body: {
          resource_id: resourceId,
          resource_version: 1,
          rows: [],
          row_count: 0,
          bytes_out: 0,
          truncated: false,
        },
      };
    },
  });
  await client.readRows(resourceId, { columns: [columnId], cursor: "opaque.cursor/value==" });
  assert.equal(sent.query.cursor, "opaque.cursor/value==");
  await assert.rejects(
    client.readRows(resourceId, { columns: [columnId], filter: { raw_sql: "select 1" } }),
    /raw_sql/u,
  );
});

test("safe error includes request ID but suppresses extra token detail", async () => {
  const client = new OmniBaseClient({
    async request() {
      return {
        status: 401,
        headers: { "x-request-id": "req-denied" },
        body: { error: { code: "invalid_capability", message: "Invalid", token: "secret" } },
      };
    },
  });
  await assert.rejects(client.readSchema(resourceId), (error) => {
    assert.ok(error instanceof GatewayError);
    assert.equal(error.code, "invalid_gateway_response");
    assert.equal(error.requestId, "req-denied");
    assert.doesNotMatch(error.message, /secret/u);
    return true;
  });
});

test("fetch transport requests a fresh Capability credential and omits cookies", async () => {
  let credentialCalls = 0;
  let init;
  const transport = new FetchTransport({
    baseUrl: "http://127.0.0.1:8001",
    allowInsecureLocalhost: true,
    credentialProvider: {
      getCredential() {
        credentialCalls += 1;
        return {
          token: "short-lived-secret",
          workloadIdentity: "runtime-1",
          expiresAt: new Date(Date.now() + 60_000),
        };
      },
    },
    async fetch(_url, suppliedInit) {
      init = suppliedInit;
      return new Response(JSON.stringify({ resource_id: resourceId, resource_version: 1, columns: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json", "X-Request-Id": "req-1" },
      });
    },
  });
  await transport.request("POST", "/gateway/v1/data/schema/read", { resource_id: resourceId });
  assert.equal(credentialCalls, 1);
  assert.equal(init.headers.Authorization, "Capability short-lived-secret");
  assert.equal(init.headers["X-Omnibase-Workload-Identity"], "runtime-1");
  assert.equal(init.credentials, "omit");
  assert.ok(init.signal instanceof AbortSignal);
});

test("typescript SDK rejects non-finite filters and fractional response integers", async () => {
  const client = new OmniBaseClient({
    async request() {
      return {
        status: 200,
        headers: {},
        body: {
          resource_id: resourceId,
          resource_version: 1,
          rows: [],
          row_count: 0.5,
          bytes_out: 0,
          truncated: false,
        },
      };
    },
  });
  await assert.rejects(
    client.readRows(resourceId, {
      columns: [columnId],
      filter: { kind: "compare", column_id: columnId, op: "eq", value: Number.NaN },
    }),
    /NaN or Infinity/u,
  );
  await assert.rejects(client.readRows(resourceId, { columns: [columnId] }), /integer/u);
});

test("success DTO fails closed on physical locator regression", async () => {
  const client = new OmniBaseClient({
    async request() {
      return {
        status: 200,
        headers: {},
        body: {
          resource_id: resourceId,
          resource_version: 1,
          columns: [],
          physical_locator: "tenant_schema.secret_table",
        },
      };
    },
  });
  await assert.rejects(client.readSchema(resourceId), /physical_locator/u);
});

test("fetch transport rejects oversized responses before JSON parsing", async () => {
  const transport = new FetchTransport({
    baseUrl: "http://127.0.0.1:8001",
    allowInsecureLocalhost: true,
    maxResponseBytes: 8,
    credentialProvider: {
      getCredential() {
        return {
          token: "short-lived-secret",
          workloadIdentity: "runtime-1",
          expiresAt: new Date(Date.now() + 60_000),
        };
      },
    },
    async fetch() {
      return new Response('{"value":"too-large"}', { status: 200 });
    },
  });
  await assert.rejects(
    transport.request("POST", "/gateway/v1/data/schema/read", { resource_id: resourceId }),
    /byte limit/u,
  );
});

test("artifact and derived helpers use logical gateway routes and bind content digests", async () => {
  const calls = [];
  const client = new OmniBaseClient({
    async request(method, path, body) {
      calls.push({ method, path, body });
      if (path.endsWith("artifacts/write")) {
        return {
          status: 200,
          headers: {},
          body: {
            operation_id: operationId,
            resource_id: resourceId,
            resource_version: 1,
            media_type: "text/plain",
            size_bytes: 5,
            content_sha256: body.content_sha256,
            replayed: false,
            request_id: "req-write",
          },
        };
      }
      if (path.endsWith("derived/create")) {
        return {
          status: 200,
          headers: {},
          body: {
            operation_id: operationId,
            resource_id: resourceId,
            resource_version: 1,
            chunk_count: 1,
            replayed: false,
            request_id: "req-derived",
          },
        };
      }
      throw new Error(`unexpected path ${path}`);
    },
  });

  const written = await client.writeArtifact({
    idempotencyKey: "artifact-write-1",
    displayName: "note",
    mediaType: "text/plain",
    content: new TextEncoder().encode("hello"),
  });
  assert.equal(written.sizeBytes, 5);
  assert.match(calls[0].body.content_sha256, /^[0-9a-f]{64}$/u);
  assert.equal(calls[0].body.content_base64, "aGVsbG8=");

  const derived = await client.createDerived({
    idempotencyKey: "derived-create-1",
    displayName: "summary",
    sourceResourceIds: [resourceId],
    chunks: [{ content: "summary", sourceResourceId: resourceId }],
  });
  assert.equal(derived.chunkCount, 1);
  assert.deepEqual(calls.map((call) => call.path), [
    "/gateway/v1/artifacts/write",
    "/gateway/v1/rag/derived/create",
  ]);
  assert.equal("workspace_id" in calls[1].body, false);
});
