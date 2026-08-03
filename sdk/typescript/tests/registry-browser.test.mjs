import assert from "node:assert/strict";
import test from "node:test";

import { AgentRegistryBrowserClient, RegistryBrowserError } from "../dist/index.js";

const workspaceId = "11111111-1111-4111-8111-111111111111";
const definitionId = "22222222-2222-4222-8222-222222222222";
const versionId = "33333333-3333-4333-8333-333333333333";
const bindingId = "44444444-4444-4444-8444-444444444444";
const digest = "4b5a26ba3980e80216db50d8d069a6c052ca472954c33247baa1b81ec69f91ca";

const bindingBody = {
  binding_id: bindingId,
  workspace_id: workspaceId,
  workspace_generation: 1,
  agent_definition_id: definitionId,
  agent_version_id: versionId,
  agent_version_digest: digest,
  binding_state: "installed",
  resource_scopes: ["workspace_private_read"],
  default_budget_policy: {
    max_tokens: 50000,
    max_cost_units: 500,
    max_wall_clock_seconds: 300,
    max_tool_calls: 50,
  },
  created_at: "2026-08-03T00:00:00Z",
  disabled_at: null,
  superseded_by: null,
};

const definitionBody = {
  agent_definition_id: definitionId,
  stable_logical_key: "agent-gate",
  display_name: "Gate Agent",
  description: null,
  risk_level: "low",
  definition_state: "active",
  metadata_version: 1,
  created_at: "2026-08-03T00:00:00Z",
};

const versionBody = {
  agent_version_id: versionId,
  agent_definition_id: definitionId,
  version: "1.0.0",
  version_state: "sealed",
  manifest_digest: digest,
  instructions_digest: digest,
  risk_level: "low",
  max_context_tokens: 200000,
  allowed_tool_ids: ["rag_search"],
  max_concurrency: 2,
  created_at: "2026-08-03T00:00:00Z",
};

function fakeClient(response) {
  const calls = [];
  const client = new AgentRegistryBrowserClient({
    async request(method, path, body, idempotencyKey) {
      calls.push({ method, path, body, idempotencyKey });
      return { status: response.status, headers: response.headers ?? {}, body: response.body };
    },
  });
  return { client, calls };
}

test("catalog reads use logical paths and parse projections", async () => {
  const { client, calls } = fakeClient({ status: 200, body: definitionBody });
  const definition = await client.getAgentDefinition(definitionId);
  assert.equal(definition.agent_definition_id, definitionId);
  assert.equal(calls[0].path, `/api/v1/agent-definitions/${definitionId}`);
  assert.equal(calls[0].method, "GET");
  assert.equal(calls[0].body, undefined);

  const versions = fakeClient({
    status: 200,
    body: { items: [versionBody], total: 1 },
  });
  const result = await versions.client.listAgentVersions(definitionId);
  assert.equal(result.total, 1);
  assert.equal(result.items[0].manifest_digest, digest);

  const installations = fakeClient({ status: 200, body: bindingBody });
  const binding = await installations.client.getInstallation(workspaceId, bindingId);
  assert.equal(binding.binding_state, "installed");
  assert.equal(binding.default_budget_policy.max_tokens, 50000);
});

test("install sends deterministic body with idempotency key", async () => {
  const { client, calls } = fakeClient({ status: 201, body: bindingBody });
  const result = await client.install({
    workspaceId,
    idempotencyKey: "p51c-sdk-key-0001",
    payload: {
      agent_definition_id: definitionId,
      agent_version_id: versionId,
      agent_version_digest: digest,
      workspace_generation: 1,
      resource_scopes: ["workspace_private_read"],
      default_budget_policy: {
        max_tokens: 50000,
        max_cost_units: 500,
        max_wall_clock_seconds: 300,
        max_tool_calls: 50,
      },
    },
  });
  assert.equal(result.binding_id, bindingId);
  assert.equal(calls[0].method, "POST");
  assert.equal(calls[0].idempotencyKey, "p51c-sdk-key-0001");
  assert.equal(calls[0].body.workspace_generation, 1);
  assert.equal("approval_id" in calls[0].body, false);
});

test("upgrade and disable use frozen mutation paths", async () => {
  const upgrade = fakeClient({ status: 200, body: bindingBody });
  await upgrade.client.upgrade({
    workspaceId,
    bindingId,
    idempotencyKey: "p51c-upgrade-key-0001",
    payload: {
      target_agent_version_id: versionId,
      target_agent_version_digest: digest,
      expected_binding_id: bindingId,
    },
  });
  assert.equal(
    upgrade.calls[0].path,
    `/api/v1/workspaces/${workspaceId}/agent-installations/${bindingId}/upgrade`,
  );
  assert.equal(upgrade.calls[0].body.expected_binding_id, bindingId);

  const disable = fakeClient({ status: 200, body: bindingBody });
  await disable.client.disable({
    workspaceId,
    bindingId,
    idempotencyKey: "p51c-disable-key-0001",
  });
  assert.equal(disable.calls[0].path.endsWith("/disable"), true);
  assert.equal(disable.calls[0].body, undefined);
});

test("registry error preserves envelope code and request id", async () => {
  const { client } = fakeClient({
    status: 503,
    headers: { "x-request-id": "req-unavailable" },
    body: { error: { code: "agent_registry_unavailable", message: "Not assembled" } },
  });
  await assert.rejects(
    client.listAgentDefinitions(),
    (error) =>
      error instanceof RegistryBrowserError &&
      error.code === "agent_registry_unavailable" &&
      error.requestId === "req-unavailable",
  );
});

test("invalid error envelope and wildcard scopes are rejected", async () => {
  const invalid = fakeClient({
    status: 409,
    headers: {},
    body: { error: { code: "conflict" } },
  });
  await assert.rejects(
    invalid.client.install({
      workspaceId,
      idempotencyKey: "p51c-invalid-key-0001",
      payload: {
        agent_definition_id: definitionId,
        agent_version_id: versionId,
        agent_version_digest: digest,
        workspace_generation: 1,
        resource_scopes: ["workspace_private_read"],
        default_budget_policy: {
          max_tokens: 1,
          max_cost_units: 1,
          max_wall_clock_seconds: 1,
          max_tool_calls: 1,
        },
      },
    }),
    (error) => error instanceof RegistryBrowserError && error.code === "invalid_browser_response",
  );

  const wildcard = fakeClient({ status: 201, body: bindingBody });
  await assert.rejects(
    wildcard.client.install({
      workspaceId,
      idempotencyKey: "p51c-scope-key-0001",
      payload: {
        agent_definition_id: definitionId,
        agent_version_id: versionId,
        agent_version_digest: digest,
        workspace_generation: 1,
        resource_scopes: ["*"],
        default_budget_policy: {
          max_tokens: 1,
          max_cost_units: 1,
          max_wall_clock_seconds: 1,
          max_tool_calls: 1,
        },
      },
    }),
    /wildcard/u,
  );
});
