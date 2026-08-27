import assert from "node:assert/strict";
import test from "node:test";

import { DesktopNativeClient } from "../src/runtime/native-client.ts";

const CONTROL_TOKEN = "e".repeat(64);
const OWNER_ID = `owner_${"a".repeat(32)}`;
const WORKSPACE_ID = `workspace_${"b".repeat(32)}`;
const TEAM_CONVERSATION_ID = `conversation_${"c".repeat(32)}`;
const PROVIDER_ID = `provider_${"d".repeat(32)}`;
const TEAM_RUN_ID = `teamrun_${"e".repeat(32)}`;
const TEAM_INVOCATION_ID = `invocation_${"f".repeat(32)}`;
const PLAN_REVISION_ID = `teamrev_${"1".repeat(32)}`;

function rawTeamRun(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: TEAM_RUN_ID,
    workspace_id: WORKSPACE_ID,
    conversation_id: TEAM_CONVERSATION_ID,
    mode: "team",
    state: "running",
    staffing_authority: "parent_proposal",
    current_plan_revision_id: null,
    current_wave_id: null,
    dispatched_participant_count: null,
    maximum_provider_calls: 24,
    maximum_wall_time_ms: 60_000,
    maximum_concurrent_calls: 2,
    maximum_input_characters: 100_000,
    maximum_output_characters: 100_000,
    consumed_provider_calls: 1,
    task: "native parent call contract",
    allowed_specialist_role_ids: ["frontend"],
    created_at: "2026-08-24T00:00:00Z",
    updated_at: "2026-08-24T00:00:01Z",
    ...overrides,
  };
}

function rawParentCall(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    invocation_id: TEAM_INVOCATION_ID,
    team_run_id: TEAM_RUN_ID,
    plan_revision_id: null,
    purpose: "parent-propose",
    state: "pending",
    provider_id: PROVIDER_ID,
    requested_model: "loopback-team",
    actual_model: null,
    input_tokens: null,
    output_tokens: null,
    total_tokens: null,
    output_sha256: null,
    error_code: null,
    created_at: "2026-08-24T00:00:00Z",
    updated_at: "2026-08-24T00:00:01Z",
    ...overrides,
  };
}

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

test("native client maps parent consume identity and parses its pending proof", async () => {
  let seenUrl = "";
  let seenBody = "";
  const client = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async (input: URL | RequestInfo, init?: RequestInit) => {
      seenUrl = String(input);
      seenBody = String(init?.body ?? "");
      return jsonResponse({
        team_run: rawTeamRun(),
        parent_call: rawParentCall(),
      });
    }) as typeof fetch,
  });
  const result = await client.consumeTeamProviderCall({
    workspaceId: WORKSPACE_ID,
    teamRunId: TEAM_RUN_ID,
    invocationId: TEAM_INVOCATION_ID,
    purpose: "parent-propose",
    providerId: PROVIDER_ID,
    requestedModel: "loopback-team",
  });
  assert.equal(result.ok, true);
  assert.equal(result.ok && result.value.parentCall?.state, "pending");
  assert.equal(
    seenUrl,
    `http://127.0.0.1:47431/desktop/v1/workspaces/${WORKSPACE_ID}/team-runs/${TEAM_RUN_ID}/consume-call`,
  );
  assert.deepEqual(JSON.parse(seenBody), {
    invocation_id: TEAM_INVOCATION_ID,
    purpose: "parent-propose",
    provider_id: PROVIDER_ID,
    requested_model: "loopback-team",
  });
});

test("native client requires the exact v9 employee consume wrapper", async () => {
  const consumeEmployee = (payload: unknown) =>
    new DesktopNativeClient({
      backendOrigin: "http://127.0.0.1:47431",
      nativeControlToken: CONTROL_TOKEN,
      fetch: (async () => jsonResponse(payload)) as typeof fetch,
    }).consumeTeamProviderCall({
      workspaceId: WORKSPACE_ID,
      teamRunId: TEAM_RUN_ID,
      invocationId: TEAM_INVOCATION_ID,
      purpose: "employee",
      providerId: PROVIDER_ID,
      requestedModel: "loopback-team",
    });

  const valid = await consumeEmployee({
    team_run: rawTeamRun(),
    parent_call: null,
  });
  assert.equal(valid.ok, true);
  assert.equal(valid.ok && valid.value.parentCall, undefined);

  for (const payload of [
    { team_run: rawTeamRun() },
    { team_run: rawTeamRun(), parent_call: rawParentCall() },
  ]) {
    assert.deepEqual(await consumeEmployee(payload), {
      ok: false,
      error: { code: "desktop_native_response_invalid" },
    });
  }
});

test("native client maps parent settle exactly and binds every response identity field", async () => {
  const outputSha256 = "2".repeat(64);
  let seenUrl = "";
  let seenBody = "";
  const settled = rawParentCall({
    plan_revision_id: PLAN_REVISION_ID,
    state: "succeeded",
    actual_model: "loopback-team",
    input_tokens: 11,
    output_tokens: 7,
    total_tokens: 18,
    output_sha256: outputSha256,
    updated_at: "2026-08-24T00:00:02Z",
  });
  const client = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async (input: URL | RequestInfo, init?: RequestInit) => {
      seenUrl = String(input);
      seenBody = String(init?.body ?? "");
      return jsonResponse({ parent_call: settled });
    }) as typeof fetch,
  });
  const input = {
    workspaceId: WORKSPACE_ID,
    teamRunId: TEAM_RUN_ID,
    invocationId: TEAM_INVOCATION_ID,
    purpose: "parent-propose" as const,
    providerId: PROVIDER_ID,
    requestedModel: "loopback-team",
    state: "succeeded" as const,
    planRevisionId: PLAN_REVISION_ID,
    actualModel: "loopback-team",
    inputTokens: 11,
    outputTokens: 7,
    totalTokens: 18,
    outputSha256,
    errorCode: null,
  };
  const result = await client.settleTeamParentCall(input);
  assert.equal(result.ok, true);
  assert.equal(
    seenUrl,
    `http://127.0.0.1:47431/desktop/v1/workspaces/${WORKSPACE_ID}/team-runs/${TEAM_RUN_ID}/parent-calls/${TEAM_INVOCATION_ID}/settle`,
  );
  assert.deepEqual(JSON.parse(seenBody), {
    purpose: "parent-propose",
    provider_id: PROVIDER_ID,
    requested_model: "loopback-team",
    state: "succeeded",
    plan_revision_id: PLAN_REVISION_ID,
    actual_model: "loopback-team",
    input_tokens: 11,
    output_tokens: 7,
    total_tokens: 18,
    output_sha256: outputSha256,
    error_code: null,
  });

  const mismatchedResponses: readonly [string, unknown][] = [
    [
      "invocation ID",
      {
        parent_call: {
          ...settled,
          invocation_id: `invocation_${"0".repeat(32)}`,
        },
      },
    ],
    [
      "team Run ID",
      {
        parent_call: {
          ...settled,
          team_run_id: `teamrun_${"0".repeat(32)}`,
        },
      },
    ],
    ["purpose", { parent_call: { ...settled, purpose: "parent-replan" } }],
    [
      "Provider ID",
      {
        parent_call: {
          ...settled,
          provider_id: `provider_${"0".repeat(32)}`,
        },
      },
    ],
    [
      "requested model",
      { parent_call: { ...settled, requested_model: "other-team" } },
    ],
    ["state", { parent_call: { ...settled, state: "failed" } }],
    [
      "plan revision",
      {
        parent_call: {
          ...settled,
          plan_revision_id: `teamrev_${"0".repeat(32)}`,
        },
      },
    ],
    [
      "actual model",
      { parent_call: { ...settled, actual_model: "other-team" } },
    ],
    [
      "input usage",
      { parent_call: { ...settled, input_tokens: 12, total_tokens: 19 } },
    ],
    [
      "output usage",
      { parent_call: { ...settled, output_tokens: 8, total_tokens: 19 } },
    ],
    ["total usage", { parent_call: { ...settled, total_tokens: 19 } }],
    [
      "digest",
      { parent_call: { ...settled, output_sha256: "3".repeat(64) } },
    ],
    [
      "error",
      {
        parent_call: {
          ...settled,
          error_code: "desktop_team_response_mismatch",
        },
      },
    ],
    ["wrapper keys", { parent_call: settled, extra: true }],
  ];
  for (const [field, payload] of mismatchedResponses) {
    const mismatched = new DesktopNativeClient({
      backendOrigin: "http://127.0.0.1:47431",
      nativeControlToken: CONTROL_TOKEN,
      fetch: (async () => jsonResponse(payload)) as typeof fetch,
    });
    assert.deepEqual(
      await mismatched.settleTeamParentCall(input),
      { ok: false, error: { code: "desktop_native_response_invalid" } },
      `settle must bind ${field}`,
    );
  }

  for (const malformedInput of [
    { ...input, state: "pending" as never },
    { ...input, outputTokens: null, totalTokens: null },
    { ...input, totalTokens: 19 },
  ]) {
    assert.deepEqual(await client.settleTeamParentCall(malformedInput), {
      ok: false,
      error: { code: "desktop_native_input_invalid" },
    });
  }
});

test("native client rejects malformed or extra-key parent call proofs", async () => {
  for (const payload of [
    { team_run: rawTeamRun(), parent_call: { ...rawParentCall(), extra: true } },
    { team_run: rawTeamRun(), parent_call: { ...rawParentCall(), invocation_id: "invocation_bad" } },
    { team_run: rawTeamRun(), parent_call: { ...rawParentCall(), state: "succeeded" } },
    {
      team_run: rawTeamRun({ id: `teamrun_${"9".repeat(32)}` }),
      parent_call: rawParentCall(),
    },
    {
      team_run: rawTeamRun({ workspace_id: `workspace_${"8".repeat(32)}` }),
      parent_call: rawParentCall(),
    },
    {
      team_run: rawTeamRun(),
      parent_call: rawParentCall({ provider_id: `provider_${"7".repeat(32)}` }),
    },
  ]) {
    const client = new DesktopNativeClient({
      backendOrigin: "http://127.0.0.1:47431",
      nativeControlToken: CONTROL_TOKEN,
      fetch: (async () =>
        jsonResponse(payload)) as typeof fetch,
    });
    assert.deepEqual(
      await client.consumeTeamProviderCall({
        workspaceId: WORKSPACE_ID,
        teamRunId: TEAM_RUN_ID,
        invocationId: TEAM_INVOCATION_ID,
        purpose: "parent-propose",
        providerId: PROVIDER_ID,
        requestedModel: "loopback-team",
      }),
      { ok: false, error: { code: "desktop_native_response_invalid" } },
    );
  }
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

const CONVERSATION_ID = `conversation_${"d".repeat(32)}`;
const INVOCATION_ID = `invocation_${"e".repeat(32)}`;
const MESSAGE_ID = `message_${"f".repeat(32)}`;

function sseResponse(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: { "Content-Type": "text/event-stream" },
  });
}

function scopedEvent(eventName: string, extra: Record<string, unknown> = {}): string {
  return (
    `event: ${eventName}\n` +
    `data: ${JSON.stringify({
      workspace_id: WORKSPACE_ID,
      conversation_id: CONVERSATION_ID,
      invocation_id: INVOCATION_ID,
      message_id: MESSAGE_ID,
      ...extra,
    })}\n\n`
  );
}

test("native client drops unscoped stream events and cancels the backend on abort", async () => {
  const seen: string[] = [];
  const emitted: Array<{ type: string; text?: string }> = [];
  const client = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async (input: URL | RequestInfo, init?: RequestInit) => {
      seen.push(`${init?.method ?? "GET"} ${String(input)}`);
      if (String(input).includes("/cancel")) {
        return jsonResponse({
          cancelled: true,
          id: INVOCATION_ID,
          accepted: true,
        });
      }
      const unscoped =
        'event: identity\ndata: {"invocation_id":"' +
        INVOCATION_ID +
        '"}\n\n' +
        'event: delta\ndata: {"invocation_id":"' +
        INVOCATION_ID +
        '","text":"leak"}\n\n';
      const scoped =
        scopedEvent("identity") +
        scopedEvent("delta", { text: "ok" }) +
        scopedEvent("done", { status: "succeeded", answer: "ok" });
      if (seen.some((item) => item.includes("/messages")) && seen.filter((item) => item.includes("/messages")).length === 1) {
        return sseResponse(unscoped);
      }
      if (String(input).includes("/messages")) {
        return sseResponse(scoped);
      }
      return jsonResponse({ ok: false });
    }) as typeof fetch,
  });

  const unscoped = await client.sendConversation(
    { workspaceId: WORKSPACE_ID, conversationId: CONVERSATION_ID, content: "hi" },
    "isolation-secret",
    (event) => emitted.push(event),
    new AbortController().signal,
  );
  assert.equal(unscoped.ok, false);
  assert.equal(emitted.length, 0);
  assert.equal(
    seen.some((item) => item.includes(`/desktop/v1/invocations/${INVOCATION_ID}/cancel`)),
    false,
  );

  emitted.length = 0;
  const scoped = await client.sendConversation(
    { workspaceId: WORKSPACE_ID, conversationId: CONVERSATION_ID, content: "hi" },
    "isolation-secret",
    (event) => emitted.push(event),
    new AbortController().signal,
  );
  assert.equal(scoped.ok, true);
  assert.equal(emitted[0]?.type, "identity");
  assert.equal(emitted[1]?.type, "delta");
  assert.equal(emitted[1]?.text, "ok");

  let cancelSeen = false;
  const aborting = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async (input: URL | RequestInfo) => {
      if (String(input).includes("/cancel")) {
        cancelSeen = true;
        return jsonResponse({
          cancelled: true,
          id: INVOCATION_ID,
          accepted: true,
        });
      }
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(
              new TextEncoder().encode(scopedEvent("identity")),
            );
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      );
    }) as typeof fetch,
  });
  const controller = new AbortController();
  const pending = aborting.sendConversation(
    { workspaceId: WORKSPACE_ID, conversationId: CONVERSATION_ID, content: "hi" },
    "isolation-secret",
    () => {
      controller.abort();
    },
    controller.signal,
  );
  const aborted = await pending;
  assert.equal(aborted.ok, true);
  if (aborted.ok) assert.equal(aborted.value.type, "cancelled");
  assert.equal(cancelSeen, true);
});

test("abort before identity does not call invocation cancel and stamps sendEpoch", async () => {
  let cancelSeen = false;
  const emitted: Array<{ type: string; sendEpoch?: number }> = [];
  const hanging = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async (input: URL | RequestInfo) => {
      if (String(input).includes("/cancel")) {
        cancelSeen = true;
        return jsonResponse({
          cancelled: true,
          id: INVOCATION_ID,
          accepted: true,
        });
      }
      return new Response(
        new ReadableStream({
          start() {
            return;
          },
        }),
        { status: 200, headers: { "Content-Type": "text/event-stream" } },
      );
    }) as typeof fetch,
  });
  const controller = new AbortController();
  const pending = hanging.sendConversation(
    {
      workspaceId: WORKSPACE_ID,
      conversationId: CONVERSATION_ID,
      content: "hi",
      sendEpoch: 7,
    },
    "isolation-secret",
    (event) => emitted.push(event),
    controller.signal,
  );
  queueMicrotask(() => controller.abort());
  const aborted = await pending;
  assert.equal(aborted.ok, true);
  if (aborted.ok) {
    assert.equal(aborted.value.type, "cancelled");
    assert.equal(aborted.value.sendEpoch, 7);
  }
  assert.equal(cancelSeen, false);
});

test("native stream identity events carry the sendEpoch from the owning send", async () => {
  const emitted: Array<{ type: string; sendEpoch?: number }> = [];
  const client = new DesktopNativeClient({
    backendOrigin: "http://127.0.0.1:47431",
    nativeControlToken: CONTROL_TOKEN,
    fetch: (async () =>
      sseResponse(
        scopedEvent("identity") +
          scopedEvent("delta", { text: "ok" }) +
          scopedEvent("done", { status: "succeeded", answer: "ok" }),
      )) as typeof fetch,
  });
  const result = await client.sendConversation(
    {
      workspaceId: WORKSPACE_ID,
      conversationId: CONVERSATION_ID,
      content: "hi",
      sendEpoch: 3,
    },
    "isolation-secret",
    (event) => emitted.push(event),
    new AbortController().signal,
  );
  assert.equal(result.ok, true);
  assert.equal(emitted[0]?.type, "identity");
  assert.equal(emitted[0]?.sendEpoch, 3);
  assert.equal(emitted[1]?.sendEpoch, 3);
  if (result.ok) assert.equal(result.value.sendEpoch, 3);
});
