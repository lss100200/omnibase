import assert from "node:assert/strict";
import test from "node:test";

import { DesktopNativeClient } from "../src/runtime/native-client.ts";

const CONTROL_TOKEN = "e".repeat(64);
const OWNER_ID = `owner_${"a".repeat(32)}`;
const WORKSPACE_ID = `workspace_${"b".repeat(32)}`;

function jsonResponse(
  value: unknown,
  status = 200,
  headers?: HeadersInit,
): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: {
      "Content-Type": "application/json",
      ...Object.fromEntries(new Headers(headers).entries()),
    },
  });
}

test("native client authenticates direct backend calls and validates owner DTOs", async () => {
  const seen: Array<{ url: string; init: RequestInit | undefined }> = [];
  const fakeFetch = (async (input: URL | RequestInfo, init?: RequestInit) => {
    seen.push({ url: String(input), init });
    return jsonResponse({
      initialized: true,
      owner: {
        id: OWNER_ID,
        display_name: "Personal Owner",
        created_at: "2026-08-19T00:00:00Z",
        updated_at: "2026-08-19T00:00:00Z",
      },
    });
  }) as typeof fetch;
  const client = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: fakeFetch,
  });

  const result = await client.getOwnerStatus();

  assert.deepEqual(result, {
    ok: true,
    value: {
      initialized: true,
      owner: {
        id: OWNER_ID,
        displayName: "Personal Owner",
        createdAt: "2026-08-19T00:00:00Z",
        updatedAt: "2026-08-19T00:00:00Z",
      },
    },
  });
  assert.equal(seen[0]?.url, "http://127.0.0.1:47431/desktop/v1/owner");
  assert.equal(
    new Headers(seen[0]?.init?.headers).get(
      "x-omnibase-desktop-native-control",
    ),
    CONTROL_TOKEN,
  );
  assert.equal(seen[0]?.init?.method, "GET");
  assert.equal(seen[0]?.init?.body, undefined);
});

test("native client maps workspace mutations without exposing control identity", async () => {
  const bodies: string[] = [];
  const fakeFetch = (async (_input: URL | RequestInfo, init?: RequestInit) => {
    bodies.push(String(init?.body ?? ""));
    const archived = bodies.length === 2;
    return jsonResponse({
      ...(archived ? {} : { created: true }),
      workspace: {
        id: WORKSPACE_ID,
        owner_id: OWNER_ID,
        name: "Primary",
        state: archived ? "archived" : "active",
        row_version: archived ? 2 : 1,
        created_at: "2026-08-19T00:00:00Z",
        updated_at: archived ? "2026-08-19T00:01:00Z" : "2026-08-19T00:00:00Z",
      },
    });
  }) as typeof fetch;
  const client = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: fakeFetch,
  });

  const created = await client.createWorkspace({ name: "Primary" });
  const archived = await client.archiveWorkspace({
    workspaceId: WORKSPACE_ID,
    expectedRowVersion: 1,
  });

  assert.equal(created.ok && created.value.workspace.state, "active");
  assert.equal(archived.ok && archived.value.workspace.state, "archived");
  assert.deepEqual(JSON.parse(bodies[0] ?? ""), { name: "Primary" });
  assert.deepEqual(JSON.parse(bodies[1] ?? ""), { expected_row_version: 1 });
  assert.equal(JSON.stringify(created).includes(CONTROL_TOKEN), false);
  assert.equal(JSON.stringify(archived).includes(CONTROL_TOKEN), false);
});

test("native client preserves stable backend errors and rejects malformed responses", async () => {
  const conflict = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async () =>
      jsonResponse(
        {
          error: {
            code: "desktop_workspace_version_conflict",
            message: "Desktop request rejected",
          },
        },
        409,
      )) as typeof fetch,
  });
  assert.deepEqual(
    await conflict.archiveWorkspace({
      workspaceId: WORKSPACE_ID,
      expectedRowVersion: 1,
    }),
    {
      ok: false,
      error: { code: "desktop_workspace_version_conflict" },
    },
  );

  const reflected = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async () =>
      jsonResponse({ initialized: false, owner: null }, 200, {
        "x-omnibase-desktop-native-control": CONTROL_TOKEN,
      })) as typeof fetch,
  });
  assert.deepEqual(await reflected.getOwnerStatus(), {
    ok: false,
    error: { code: "desktop_native_response_invalid" },
  });

  const malformed = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async () =>
      jsonResponse({
        initialized: false,
        owner: { id: "bad" },
      })) as typeof fetch,
  });
  assert.deepEqual(await malformed.getOwnerStatus(), {
    ok: false,
    error: { code: "desktop_native_response_invalid" },
  });

  const wrongContentType = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async () =>
      new Response(JSON.stringify({ initialized: false, owner: null }), {
        headers: { "Content-Type": "text/plain" },
      })) as typeof fetch,
  });
  assert.deepEqual(await wrongContentType.getOwnerStatus(), {
    ok: false,
    error: { code: "desktop_native_response_invalid" },
  });
});

test("native client bounds and de-duplicates the workspace projection", async () => {
  const rawWorkspace = (index: number) => ({
    id: `workspace_${index.toString(16).padStart(32, "0")}`,
    owner_id: OWNER_ID,
    name: `Workspace ${index}`,
    state: "active",
    row_version: 1,
    created_at: "2026-08-19T00:00:00Z",
    updated_at: "2026-08-19T00:00:00Z",
  });
  const clientFor = (items: readonly unknown[]) =>
    new DesktopNativeClient({
      backendOrigin: "http://127.0.0.1:47431",
      nativeControlToken: CONTROL_TOKEN,
      fetch: (async () => jsonResponse({ items })) as typeof fetch,
    });

  assert.deepEqual(
    await clientFor(Array.from({ length: 257 }, (_, index) => rawWorkspace(index))).listWorkspaces(),
    {
      ok: false,
      error: { code: "desktop_native_response_invalid" },
    },
  );
  assert.deepEqual(
    await clientFor([rawWorkspace(1), rawWorkspace(1)]).listWorkspaces(),
    {
      ok: false,
      error: { code: "desktop_native_response_invalid" },
    },
  );
});

test("native client accepts only a fixed IPv4-loopback origin and canonical token", () => {
  for (const backendOrigin of [
    "http://localhost:47431",
    "http://[::1]:47431",
    "https://127.0.0.1:47431",
    "http://user@127.0.0.1:47431",
    "http://127.0.0.1:47431/private",
    "http://127.0.0.1:0",
  ]) {
    assert.throws(
      () =>
        new DesktopNativeClient({
          backendOrigin,
          nativeControlToken: CONTROL_TOKEN,
        }),
      /desktop_native_origin_invalid/u,
    );
  }
  assert.throws(
    () =>
      new DesktopNativeClient({
        backendOrigin: "http://127.0.0.1:47431",
        nativeControlToken: "invalid",
      }),
    /desktop_native_control_token_invalid/u,
  );
});

test("native client maps provider list without secret material", async () => {
  const providerId = `provider_${"c".repeat(32)}`;
  const seen: string[] = [];
  const client = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async (input: URL | RequestInfo) => {
      seen.push(String(input));
      return jsonResponse({
        items: [
          {
            id: providerId,
            display_name: "Loopback",
            base_url: "http://127.0.0.1:9/v1",
            model_name: "deepseek-chat",
            family: "deepseek",
            gear: "standard",
            thinking_depth: "medium",
            timeout_seconds: 30,
            allow_loopback_http: true,
            is_default: true,
            is_enabled: true,
            has_secret: true,
            created_at: "2026-08-19T00:00:00Z",
            updated_at: "2026-08-19T00:00:00Z",
          },
        ],
      });
    }) as typeof fetch,
  });
  const result = await client.listProviders();
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.value.items[0]?.id, providerId);
  assert.equal(result.value.items[0]?.hasSecret, true);
  assert.equal(
    JSON.stringify(result.value).includes("encrypted"),
    false,
  );
  assert.equal(JSON.stringify(result.value).includes("isolation"), false);
  assert.equal(seen[0], "http://127.0.0.1:47431/desktop/v1/providers");
});
