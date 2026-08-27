import assert from "node:assert/strict";
import http from "node:http";
import { AddressInfo } from "node:net";
import path from "node:path";
import test from "node:test";

import type { IpcMainInvokeEvent } from "electron";

import { registerClosedIpcHandlers, type IpcMainLike } from "../src/ipc.ts";
import {
  createInMemoryPersonalTeamHost,
  eventMatchesTeamIdentity,
  PersonalTeamCoordinator,
  teamEventIdentityComplete,
} from "../src/runtime/personal-team-coordinator.ts";
import { createNativePersonalTeamHost } from "../src/runtime/personal-team-native-host.ts";
import type { DesktopNativeClient } from "../src/runtime/native-client.ts";
import {
  createOpenAiCompatibleTransport,
  resolvePinnedTeamEndpoint,
} from "../src/runtime/personal-team-provider.ts";
import { RuntimeManager } from "../src/runtime/runtime-manager.ts";
import { encryptProviderSecret } from "../src/runtime/secret-vault.ts";
import { DESKTOP_UI_ORIGIN } from "../src/security/origin-policy.ts";
import {
  IPC_CHANNELS,
  type DesktopAgentRole,
  type DesktopOperationResult,
  type DesktopProvider,
  type DesktopTeamRunEvent,
  type DesktopTeamRunExecuteInput,
  type RuntimeStatus,
} from "../src/shared/ipc-contract.ts";
import { DEFAULT_TEAM_RUN_BUDGET } from "../src/shared/personal-team.ts";

const WORKSPACE = `workspace_${"a".repeat(32)}`;
const CONVERSATION = `conversation_${"b".repeat(32)}`;
const OTHER_CONVERSATION = `conversation_${"c".repeat(32)}`;
const PROVIDER_ID = `provider_${"d".repeat(32)}`;
const SECRET = "loopback-secret-not-for-git";

function coded(error: unknown): string {
  if (typeof error === "object" && error !== null && "code" in error) {
    return String((error as { code?: unknown }).code);
  }
  return "unknown";
}

async function rejectCode(run: Promise<unknown>, expected: string): Promise<void> {
  try {
    await run;
    assert.fail(`expected ${expected}`);
  } catch (error) {
    assert.equal(coded(error), expected);
  }
}

function memoryVault() {
  const store = new Map<string, string>();
  return {
    isEncryptionAvailable: () => true,
    encryptString: (plainText: string) => {
      const token = Buffer.from(`dpapi:${plainText}`, "utf8");
      store.set(token.toString("base64"), plainText);
      return token;
    },
    decryptString: (encrypted: Buffer) => {
      const restored = store.get(encrypted.toString("base64"));
      if (restored === undefined) throw new Error("vault_miss");
      return restored;
    },
  };
}

function executeInput(
  overrides: Partial<DesktopTeamRunExecuteInput> = {},
): DesktopTeamRunExecuteInput {
  return {
    workspaceId: WORKSPACE,
    conversationId: CONVERSATION,
    task: "[p69-scenario:one_specialist] review the desktop team design",
    teamMode: true,
    rosterEpoch: 1,
    budget: { ...DEFAULT_TEAM_RUN_BUDGET, maximumProviderCalls: 24 },
    ...overrides,
  };
}

function startLoopbackChat(options: { readonly bodyFor?: (role: string) => unknown } = {}) {
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      const body = Buffer.concat(chunks).toString("utf8");
      const parsed = JSON.parse(body) as { messages?: { role: string; content: string }[] };
      const system = parsed.messages?.find((item) => item.role === "system")?.content ?? "";
      const role = /\[omnibase-team-role:([^\]]+)\]/u.exec(system)?.[1] ?? "parent";
      if (options.bodyFor) {
        const custom = options.bodyFor(role);
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(typeof custom === "string" ? custom : JSON.stringify(custom));
        return;
      }
      let content = "ok";
      if (role === "parent-propose") {
        content = JSON.stringify({
          decision: "delegate",
          objective: "review the desktop team design",
          waves: [
            {
              waveId: "wave-1",
              execution: "serial",
              assignments: [
                {
                  assignmentId: "frontend-review",
                  employeeRoleId: "frontend",
                  objective: "subtask for frontend",
                  dependsOnAssignmentIds: [],
                  expectedOutput: "report",
                  contextRequirements: [],
                },
              ],
            },
          ],
          finalSynthesisRequired: true,
        });
      } else if (role.startsWith("employee:")) {
        content = JSON.stringify({
          assignmentId: "frontend-review",
          employeeRoleId: "frontend",
          status: "completed",
          report: "frontend completed frontend-review",
          collaborationRequests: [],
        });
      } else if (role === "parent-replan") {
        content = JSON.stringify({ decision: "finish", reason: "Staffing is complete." });
      } else if (role === "parent-synthesize") {
        content = "综合结论：父 Agent 已汇总各专业员工报告。";
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          model: "loopback-team",
          choices: [{ message: { role: "assistant", content } }],
          usage: { prompt_tokens: 11, completion_tokens: 7, total_tokens: 18 },
        }),
      );
    });
  });
  return {
    listen(): Promise<{ baseUrl: string; close: () => Promise<void> }> {
      return new Promise((resolve) => {
        server.listen(0, "127.0.0.1", () => {
          const address = server.address() as AddressInfo;
          resolve({
            baseUrl: `http://127.0.0.1:${address.port}/v1`,
            close: () => new Promise((done) => server.close(() => done())),
          });
        });
      });
    },
  };
}

function credentials(baseUrl: string) {
  return {
    providerId: PROVIDER_ID,
    model: "loopback-team",
    baseUrl,
    secret: SECRET,
    allowLoopbackHttp: true,
    timeoutMs: 5_000,
  };
}

function sampleRole(overrides: Partial<DesktopAgentRole> = {}): DesktopAgentRole {
  return {
    id: "frontend",
    displayName: "前端",
    responsibility: "桌面与前端实现",
    defaultState: "dormant",
    mayJoinTeam: true,
    providerId: PROVIDER_ID,
    modelNameOverride: "loopback-team",
    gear: "standard",
    thinkingDepth: "low",
    rowVersion: 1,
    verificationState: "unverified",
    verifiedActualModel: null,
    inheritedProvider: false,
    resolvedProviderId: PROVIDER_ID,
    resolvedModelName: "loopback-team",
    secretFingerprint: "a".repeat(64),
    hasSecret: true,
    ...overrides,
  };
}

function sampleProvider(overrides: Partial<DesktopProvider> = {}): DesktopProvider {
  return {
    id: PROVIDER_ID,
    displayName: "loopback",
    baseUrl: "http://127.0.0.1:9",
    modelName: "loopback-team",
    family: "generic-openai-compatible",
    gear: "standard",
    thinkingDepth: "low",
    timeoutSeconds: 5,
    allowLoopbackHttp: true,
    isDefault: true,
    isEnabled: true,
    hasSecret: true,
    createdAt: "2026-08-20T00:00:00Z",
    updatedAt: "2026-08-20T00:00:00Z",
    ...overrides,
  };
}

test("DNS rebinding to loopback, private, or link-local is rejected and public pins keep the hostname", async () => {
  const lookups = 0;
  const rejected = [
    "127.0.0.1",
    "10.1.2.3",
    "192.168.1.20",
    "172.16.0.4",
    "169.254.10.2",
    "100.64.0.1",
    "192.0.2.1",
    "198.51.100.1",
    "203.0.113.1",
    "198.18.0.1",
    "240.0.0.1",
    "224.0.0.1",
    "fe80::1",
    "fc00::1",
    "2001:db8::1",
    "ff02::1",
    "::ffff:10.0.0.1",
    "::ffff:100.64.0.1",
  ];
  for (const address of rejected) {
    await rejectCode(
      resolvePinnedTeamEndpoint("https://api.example.test/v1", false, {
        lookup: async () => [address],
      }),
      "desktop_provider_endpoint_invalid",
    );
  }
  await rejectCode(
    resolvePinnedTeamEndpoint("https://api.example.test/v1", false, {
      lookup: async () => ["8.8.8.8", "100.64.0.1"],
    }),
    "desktop_provider_endpoint_invalid",
  );
  await rejectCode(
    resolvePinnedTeamEndpoint("https://api.example.test/v1", false, {
      lookup: async () => ["8.8.8.8", "127.0.0.1"],
    }),
    "desktop_provider_endpoint_invalid",
  );
  let lookupCount = lookups;
  const pinned = await resolvePinnedTeamEndpoint("https://api.example.test/v1", false, {
    lookup: async () => {
      lookupCount += 1;
      return ["8.8.8.8"];
    },
  });
  assert.equal(lookupCount, 1);
  assert.deepEqual(pinned.connectAddrs, ["8.8.8.8"]);
  assert.equal(pinned.hostname, "api.example.test");
  assert.equal(pinned.scheme, "https");
});

test("explicit disabled Provider fails closed instead of inheriting another", async () => {
  const vault = memoryVault();
  const host = createNativePersonalTeamHost({
    client: {
      getAgentRole: async () => ({ ok: true, value: { role: sampleRole() } }),
      listProviders: async () => ({
        ok: true,
        value: { items: [sampleProvider({ isEnabled: false })] },
      }),
      getProviderVault: async () => {
        throw new Error("must-not-open-vault");
      },
    } as unknown as DesktopNativeClient,
    vault,
  });
  await rejectCode(
    host.resolveCredentials(WORKSPACE, "frontend", new AbortController().signal),
    "desktop_provider_disabled",
  );
});

test("missing or mismatched actual model fails the chat instead of succeeding", async () => {
  const missing = startLoopbackChat({
    bodyFor: () => ({
      choices: [{ message: { role: "assistant", content: "ok" } }],
    }),
  });
  const mismatched = startLoopbackChat({
    bodyFor: () => ({
      model: "other-model",
      choices: [{ message: { role: "assistant", content: "ok" } }],
    }),
  });
  const empty = startLoopbackChat({ bodyFor: () => ({}) });
  const missingServer = await missing.listen();
  const mismatchedServer = await mismatched.listen();
  const emptyServer = await empty.listen();
  const transport = createOpenAiCompatibleTransport();
  const signal = new AbortController().signal;
  try {
    await rejectCode(
      transport.complete({ ...credentials(missingServer.baseUrl), messages: [{ role: "user", content: "hi" }] }, signal),
      "desktop_provider_model_identity_drift",
    );
    await rejectCode(
      transport.complete(
        { ...credentials(mismatchedServer.baseUrl), messages: [{ role: "user", content: "hi" }] },
        signal,
      ),
      "desktop_provider_model_identity_drift",
    );
    await rejectCode(
      transport.complete({ ...credentials(emptyServer.baseUrl), messages: [{ role: "user", content: "hi" }] }, signal),
      "desktop_provider_response_invalid",
    );
  } finally {
    await missingServer.close();
    await mismatchedServer.close();
    await emptyServer.close();
  }
});

test("Provider failure after node creation fail-stops without fake success", async () => {
  const server = startLoopbackChat({
    bodyFor: (role) => {
      if (role.startsWith("employee:")) {
        return JSON.stringify({ error: { message: "provider failed" } });
      }
      if (role === "parent-propose") {
        return {
          model: "loopback-team",
          choices: [
            {
              message: {
                role: "assistant",
                content: JSON.stringify({
                  decision: "delegate",
                  objective: "review",
                  waves: [
                    {
                      waveId: "wave-1",
                      execution: "serial",
                      assignments: [
                        {
                          assignmentId: "frontend-review",
                          employeeRoleId: "frontend",
                          objective: "subtask for frontend",
                          dependsOnAssignmentIds: [],
                          expectedOutput: "report",
                          contextRequirements: [],
                        },
                      ],
                    },
                  ],
                  finalSynthesisRequired: true,
                }),
              },
            },
          ],
        };
      }
      return {
        model: "loopback-team",
        choices: [{ message: { role: "assistant", content: "ok" } }],
      };
    },
  });
  const listening = await server.listen();
  const host = createInMemoryPersonalTeamHost({ credentials: credentials(listening.baseUrl) });
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  try {
    const proof = await coordinator.execute(executeInput(), () => undefined);
    assert.notEqual(proof.state, "succeeded");
    assert.equal(host.nodes.length, 1);
    assert.equal(host.reports.length, 0);
  } finally {
    await listening.close();
  }
});

test("incomplete Provider body after node creation is unknown, not success", async () => {
  const server = startLoopbackChat({
    bodyFor: (role) => {
      if (role.startsWith("employee:")) return {};
      if (role === "parent-propose") {
        return {
          model: "loopback-team",
          choices: [
            {
              message: {
                role: "assistant",
                content: JSON.stringify({
                  decision: "delegate",
                  objective: "review",
                  waves: [
                    {
                      waveId: "wave-1",
                      execution: "serial",
                      assignments: [
                        {
                          assignmentId: "frontend-review",
                          employeeRoleId: "frontend",
                          objective: "subtask for frontend",
                          dependsOnAssignmentIds: [],
                          expectedOutput: "report",
                          contextRequirements: [],
                        },
                      ],
                    },
                  ],
                  finalSynthesisRequired: true,
                }),
              },
            },
          ],
        };
      }
      return {
        model: "loopback-team",
        choices: [{ message: { role: "assistant", content: "finish" } }],
      };
    },
  });
  const listening = await server.listen();
  const host = createInMemoryPersonalTeamHost({ credentials: credentials(listening.baseUrl) });
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  try {
    const proof = await coordinator.execute(executeInput(), () => undefined);
    assert.equal(proof.state, "unknown");
    assert.equal(host.nodes.length, 1);
    assert.equal(host.reports.length, 0);
  } finally {
    await listening.close();
  }
});

test("settle/audit failure after node creation is not success", async () => {
  const server = startLoopbackChat();
  const listening = await server.listen();
  const host = createInMemoryPersonalTeamHost({ credentials: credentials(listening.baseUrl) });
  host.failNextSettle = "desktop_team_node_settle_failed";
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  try {
    const proof = await coordinator.execute(executeInput(), () => undefined);
    assert.notEqual(proof.state, "succeeded");
    assert.equal(host.nodes.length, 1);
    assert.equal(host.reports.length, 0);
    assert.equal(host.audits.some((item) => item.startsWith("team_node_settled:")), false);
  } finally {
    await listening.close();
  }
});

test("Stop during createNode latches abort and does not emit node identity", async () => {
  const server = startLoopbackChat();
  const listening = await server.listen();
  const host = createInMemoryPersonalTeamHost({ credentials: credentials(listening.baseUrl) });
  let releaseCreate!: () => void;
  const held = new Promise<void>((resolve) => {
    releaseCreate = resolve;
  });
  let createStarted!: () => void;
  const started = new Promise<void>((resolve) => {
    createStarted = resolve;
  });
  const original = host.createNode.bind(host);
  host.createNode = async (input) => {
    createStarted();
    await held;
    return original(input);
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  const events: DesktopTeamRunEvent[] = [];
  const running = coordinator.execute(executeInput(), (event) => events.push(event));
  await started;
  coordinator.requestStop();
  releaseCreate();
  try {
    const proof = await running;
    assert.equal(proof.state, "cancelled");
    assert.equal(
      events.some(
        (item) =>
          item.type === "node_starting" ||
          (item.type === "node_identity" && item.employeeRoleId === "frontend"),
      ),
      false,
    );
  } finally {
    await listening.close();
  }
});

test("missing roster, plan, wave, assignment, node, or send epoch each fails identity match", () => {
  const current = {
    workspaceId: WORKSPACE,
    conversationId: CONVERSATION,
    teamRunId: `teamrun_${"e".repeat(32)}`,
    rosterEpoch: 3,
    planRevisionId: "teamrev_1",
    waveId: "wave-1",
    assignmentId: "frontend-review",
    nodeId: `teamnode_${"f".repeat(32)}`,
    sendEpoch: 4,
  };
  const valid: DesktopTeamRunEvent = {
    type: "node_delta",
    teamRunId: current.teamRunId,
    workspaceId: WORKSPACE,
    conversationId: CONVERSATION,
    rosterEpoch: 3,
    planRevisionId: "teamrev_1",
    waveId: "wave-1",
    assignmentId: "frontend-review",
    nodeId: current.nodeId,
    sendEpoch: 4,
    nodeEpoch: 1,
    invocationId: `invocation_${"1".repeat(32)}`,
    employeeRoleId: "frontend",
    text: "ok",
  };
  assert.equal(eventMatchesTeamIdentity(current, valid), true);
  const keys = ["rosterEpoch", "planRevisionId", "waveId", "assignmentId", "nodeId", "sendEpoch", "nodeEpoch", "invocationId", "employeeRoleId"] as const;
  for (const key of keys) {
    const incomplete = { ...valid } as DesktopTeamRunEvent & Record<string, unknown>;
    delete incomplete[key];
    assert.equal(teamEventIdentityComplete(incomplete), false, `missing ${key} must be incomplete`);
    assert.equal(eventMatchesTeamIdentity(current, incomplete), false, `missing ${key} must drop`);
  }
});

test("strict team wall-time stops further nodes without fake success", async () => {
  const server = startLoopbackChat();
  const listening = await server.listen();
  const host = createInMemoryPersonalTeamHost({ credentials: credentials(listening.baseUrl) });
  let now = 1_000;
  const original = host.consumeProviderCall.bind(host);
  host.consumeProviderCall = async (input) => {
    const result = await original(input);
    now = 700_000;
    return result;
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
    now: () => now,
  });
  const events: DesktopTeamRunEvent[] = [];
  try {
    const proof = await coordinator.execute(executeInput(), (event) => events.push(event));
    assert.equal(proof.state, "budget_exhausted");
    assert.equal(events.some((item) => item.type === "node_starting"), false);
    assert.equal(events.some((item) => item.type === "completed"), false);
    assert.equal(host.nodes.length, 0);
  } finally {
    await listening.close();
  }
});

test("empty specialist allow-list fails closed before any team run", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: credentials("http://127.0.0.1:9/v1"),
  });
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  await rejectCode(
    coordinator.execute(executeInput({ allowedSpecialistRoleIds: [] }), () => undefined),
    "desktop_team_allow_list_empty",
  );
  assert.equal(host.runs.length, 0);
});

test("raw start bound to conversation A cannot attach to conversation B", async () => {
  const server = startLoopbackChat();
  const listening = await server.listen();
  const host = createInMemoryPersonalTeamHost({ credentials: credentials(listening.baseUrl) });
  const original = host.startTeamRun.bind(host);
  host.startTeamRun = async (input) => {
    const started = await original(input);
    return { teamRun: { ...started.teamRun, conversationId: OTHER_CONVERSATION } };
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  try {
    await rejectCode(coordinator.execute(executeInput(), () => undefined), "desktop_team_conversation_identity_mismatch");
    assert.equal(host.runs[0]?.state, "failed");
  } finally {
    await listening.close();
  }
});

test("RuntimeManager plus loopback Provider completes a parent-directed team journey", async () => {
  const server = startLoopbackChat();
  const listening = await server.listen();
  const vault = memoryVault();
  const encrypted = encryptProviderSecret(SECRET, vault);
  const memory = createInMemoryPersonalTeamHost({ credentials: credentials(listening.baseUrl) });
  const wrap = async <T>(run: () => Promise<T>): Promise<DesktopOperationResult<T>> => {
    try {
      return { ok: true, value: await run() };
    } catch (error) {
      return { ok: false, error: { code: coded(error) } };
    }
  };
  const client = {
    listProviders: async () => ({ ok: true as const, value: { items: [sampleProvider({ baseUrl: listening.baseUrl })] } }),
    getProviderVault: async () => ({ ok: true as const, value: { encryptedSecretBlob: encrypted.encryptedSecretBlob } }),
    sendConversation: async () => ({ ok: false as const, error: { code: "must-not-send" } }),
    startTeamRun: (input: DesktopTeamRunExecuteInput) => wrap(() => memory.startTeamRun(input)),
    submitTeamProposal: (input: Parameters<typeof memory.submitProposal>[0]) => wrap(() => memory.submitProposal(input)),
    getTeamBlackboard: (input: Parameters<typeof memory.getBlackboard>[0]) => wrap(() => memory.getBlackboard(input)),
    consumeTeamProviderCall: (input: Parameters<typeof memory.consumeProviderCall>[0]) =>
      wrap(() => memory.consumeProviderCall(input)),
    settleTeamParentCall: (input: Parameters<typeof memory.settleParentCall>[0]) =>
      wrap(() => memory.settleParentCall(input)),
    setTeamRunState: (input: Parameters<typeof memory.setRunState>[0]) => wrap(() => memory.setRunState(input)),
    createTeamNode: (input: Parameters<typeof memory.createNode>[0]) => wrap(() => memory.createNode(input)),
    updateTeamNode: (input: Parameters<typeof memory.updateNode>[0]) =>
      wrap(async () => {
        await memory.updateNode(input);
        return { updated: true as const, id: input.nodeId, state: input.state };
      }),
    settleTeamNode: (input: Parameters<typeof memory.settleNode>[0]) =>
      wrap(async () => {
        await memory.settleNode(input);
        return { updated: true as const, id: input.nodeId, state: input.state };
      }),
    recordTeamReport: (input: Parameters<typeof memory.recordReport>[0]) =>
      wrap(async () => {
        await memory.recordReport(input);
        return { recorded: true as const };
      }),
    getAgentRole: async () => ({
      ok: true as const,
      value: { role: sampleRole({ providerId: null, inheritedProvider: true }) },
    }),
  };
  const manager = new RuntimeManager({
    runtimeRoot: path.resolve(`C:/omnibase-missing-runtime-team-${process.pid}`),
    expectedManifestSha256: "0".repeat(64),
    uiOrigin: "http://127.0.0.1:3000",
    dataRoot: path.resolve("C:/Users/Alice/AppData/Local/OmniBase"),
    secretVault: vault,
    nativeClientForTests: client,
  });
  const events: DesktopTeamRunEvent[] = [];
  try {
    const result = await manager.executeTeamRun(executeInput(), (event) => events.push(event));
    assert.equal(result.ok, true);
    if (!result.ok) return;
    assert.equal(result.value.proof.state, "succeeded");
    assert.equal(result.value.proof.executedNodeCount, 1);
    assert.equal(memory.reports.length, 1);
    assert.ok(events.some((item) => item.type === "node_starting"));
    assert.ok(events.every((item) => teamEventIdentityComplete(item)));
  } finally {
    await listening.close();
  }
});

test("IPC forwards an explicit empty allow-list instead of parse-failing it", async () => {
  const handlers = new Map<string, (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown>();
  const ipcMain: IpcMainLike = {
    handle: (channel, listener) => {
      handlers.set(channel, listener);
    },
    removeHandler: () => undefined,
  };
  const ready: RuntimeStatus = Object.freeze({ phase: "ready", attempts: 1, lastError: null });
  const unused = async () => ({ ok: false as const, error: { code: "must-not-run" } });
  let received: unknown;
  registerClosedIpcHandlers(ipcMain, {
    getVersion: () => "1.0.0",
    getRuntimeStatus: () => ready,
    retryRuntimeStartup: async () => ready,
    getOwnerStatus: unused,
    bootstrapOwner: unused,
    listWorkspaces: unused,
    createWorkspace: unused,
    archiveWorkspace: unused,
    getWorkspaceAgent: unused,
    listProviders: async () => ({ ok: true as const, value: { items: [] } }),
    upsertProvider: unused,
    deleteProvider: unused,
    testProvider: unused,
    listConversations: unused,
    createConversation: unused,
    archiveConversation: unused,
    getConversation: unused,
    sendConversation: unused,
    cancelConversation: unused,
    abortInFlightSend: unused,
    listAgentRoles: unused,
    getAgentRole: unused,
    updateAgentRole: unused,
    testAgentRole: unused,
    startTeamRun: async (input) => {
      received = input;
      return { ok: false, error: { code: "desktop_team_allow_list_empty" } };
    },
    cancelTeamRun: unused,
    getTeamRun: unused,
    listTeamRuns: unused,
    submitTeamProposal: unused,
    getTeamBlackboard: unused,
    recordTeamCollaboration: unused,
    executeTeamRun: unused,
    appendTeamRunBudget: unused,
  });
  const trustedEvent = {
    sender: { isDestroyed: () => false, send: () => undefined },
    senderFrame: { url: `${DESKTOP_UI_ORIGIN}/` },
  } as unknown as IpcMainInvokeEvent;
  const result = await handlers.get(IPC_CHANNELS.teamRunsStart)?.(trustedEvent, {
    workspaceId: WORKSPACE,
    conversationId: CONVERSATION,
    task: "review",
    teamMode: true,
    budget: {
      maximumProviderCalls: 16,
      maximumWallTimeMs: 600000,
      maximumConcurrentCalls: 2,
      maximumInputCharacters: 16384,
      maximumOutputCharacters: 32768,
    },
    allowedSpecialistRoleIds: [],
  });
  assert.deepEqual(received, {
    workspaceId: WORKSPACE,
    conversationId: CONVERSATION,
    task: "review",
    teamMode: true,
    budget: {
      maximumProviderCalls: 16,
      maximumWallTimeMs: 600000,
      maximumConcurrentCalls: 2,
      maximumInputCharacters: 16384,
      maximumOutputCharacters: 32768,
    },
    allowedSpecialistRoleIds: [],
  });
  assert.deepEqual(result, { ok: false, error: { code: "desktop_team_allow_list_empty" } });
});
