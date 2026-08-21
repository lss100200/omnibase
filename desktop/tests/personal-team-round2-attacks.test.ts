import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { AddressInfo } from "node:net";
import test from "node:test";

import { DesktopNativeClient } from "../src/runtime/native-client.ts";
import {
  createInMemoryPersonalTeamHost,
  PersonalTeamCoordinator,
} from "../src/runtime/personal-team-coordinator.ts";
import { createNativePersonalTeamHost } from "../src/runtime/personal-team-native-host.ts";
import { isGlobalUnicastAddress } from "../src/runtime/global-unicast.ts";
import {
  createOpenAiCompatibleTransport,
  resolvePinnedTeamEndpoint,
} from "../src/runtime/personal-team-provider.ts";
import { RuntimeManager } from "../src/runtime/runtime-manager.ts";
import { encryptProviderSecret } from "../src/runtime/secret-vault.ts";
import type {
  DesktopAgentRole,
  DesktopOperationResult,
  DesktopProvider,
  DesktopTeamRunEvent,
  DesktopTeamRunExecuteInput,
} from "../src/shared/ipc-contract.ts";
import { DEFAULT_TEAM_RUN_BUDGET } from "../src/shared/personal-team.ts";

const WORKSPACE = `workspace_${"a".repeat(32)}`;
const CONVERSATION = `conversation_${"b".repeat(32)}`;
const PROVIDER_ID = `provider_${"d".repeat(32)}`;
const SECRET = "loopback-secret-not-for-git";

function findRepoRoot(): string {
  let current = path.resolve(process.cwd());
  for (let index = 0; index < 6; index += 1) {
    if (fs.existsSync(path.join(current, "backend", "src", "omnibase"))) {
      return current;
    }
    current = path.resolve(current, "..");
  }
  throw new Error("cannot locate OmniBase repo root");
}

const REPO_ROOT = findRepoRoot();

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

function startLoopbackChat() {
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      const body = Buffer.concat(chunks).toString("utf8");
      const parsed = JSON.parse(body) as { messages?: { role: string; content: string }[] };
      const system = parsed.messages?.find((item) => item.role === "system")?.content ?? "";
      const role = /\[omnibase-team-role:([^\]]+)\]/u.exec(system)?.[1] ?? "parent";
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

function startHungChat() {
  const server = http.createServer(() => undefined);
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

function credentials(baseUrl: string, timeoutMs = 5_000) {
  return {
    providerId: PROVIDER_ID,
    model: "loopback-team",
    baseUrl,
    secret: SECRET,
    allowLoopbackHttp: true,
    timeoutMs,
  };
}

function sampleRole(overrides: Partial<DesktopAgentRole> = {}): DesktopAgentRole {
  return {
    id: "parent",
    displayName: "父 Agent",
    responsibility: "提案与汇总",
    defaultState: "active",
    mayJoinTeam: false,
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

test("authoritative global-unicast rejects reserved CGNAT benchmark docs multicast and link-local", () => {
  const rejected = [
    "0.0.0.1",
    "10.1.2.3",
    "100.64.0.1",
    "127.0.0.1",
    "169.254.10.2",
    "172.16.0.4",
    "192.0.2.1",
    "192.168.1.20",
    "198.18.0.1",
    "198.51.100.1",
    "203.0.113.1",
    "224.0.0.1",
    "240.0.0.1",
    "fe80::1",
    "fc00::1",
    "2001:db8::1",
    "ff02::1",
    "::ffff:100.64.0.1",
  ];
  for (const address of rejected) {
    assert.equal(isGlobalUnicastAddress(address), false, address);
  }
  assert.equal(isGlobalUnicastAddress("8.8.8.8"), true);
  assert.equal(isGlobalUnicastAddress("192.0.0.9"), true);
  assert.equal(isGlobalUnicastAddress("2001:4860:4860::8888"), true);
});

test("team transport pin hook uses backend connect addrs instead of the TS replica", async () => {
  const pinned = await resolvePinnedTeamEndpoint("https://api.example.test/v1", false, {
    pinEndpoint: async () => ({
      scheme: "https",
      hostname: "api.example.test",
      port: 443,
      chatPath: "/v1/chat/completions",
      connectAddrs: ["8.8.8.8"],
      loopback: false,
    }),
  });
  assert.deepEqual(pinned.connectAddrs, ["8.8.8.8"]);
  assert.equal(pinned.hostname, "api.example.test");
});

test("TS fallback replica still fail-closes mixed public and benchmark DNS", async () => {
  await rejectCode(
    resolvePinnedTeamEndpoint("https://api.example.test/v1", false, {
      lookup: async () => ["8.8.8.8", "198.18.0.1"],
    }),
    "desktop_provider_endpoint_invalid",
  );
});

test("TS BlockList pin is not CPython is_global_unicast; extra-rejects are examples not an exhaustive IANA list", () => {
  // Production team HTTPS asks desktop-local pin (endpoint.py is_global_unicast).
  // The TS replica is a fallback. These addresses are examples of extra-rejects
  // vs CPython (2001::/23 vs narrower TEREDO/ORCHID specials), not a complete
  // IANA disagreement inventory.
  const exampleExtraRejectsVsCpython = ["2001:1::1", "2001:3::1", "2001:20::1"];
  for (const address of exampleExtraRejectsVsCpython) {
    assert.equal(isGlobalUnicastAddress(address), false, address);
  }
});

test("independent wall AbortController expires to budget_exhausted without using Provider timeout", async () => {
  const hung = startHungChat();
  const listening = await hung.listen();
  const host = createInMemoryPersonalTeamHost({
    credentials: credentials(listening.baseUrl, 5_000),
  });
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  const started = Date.now();
  try {
    const proof = await coordinator.execute(
      executeInput({
        budget: { ...DEFAULT_TEAM_RUN_BUDGET, maximumWallTimeMs: 1_000, maximumProviderCalls: 8 },
      }),
      () => undefined,
    );
    const elapsed = Date.now() - started;
    assert.equal(proof.state, "budget_exhausted");
    assert.ok(elapsed < 4_000, `wall should win before Provider timeout, elapsed=${elapsed}`);
  } finally {
    await listening.close();
  }
});

test("Provider HTTP timeout is not reported as team wall budget_exhausted", async () => {
  const hung = startHungChat();
  const listening = await hung.listen();
  const host = createInMemoryPersonalTeamHost({
    credentials: credentials(listening.baseUrl, 80),
  });
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  try {
    const proof = await coordinator.execute(
      executeInput({
        budget: { ...DEFAULT_TEAM_RUN_BUDGET, maximumWallTimeMs: 600_000, maximumProviderCalls: 8 },
      }),
      () => undefined,
    );
    assert.notEqual(proof.state, "budget_exhausted");
    assert.notEqual(proof.state, "succeeded");
  } finally {
    await listening.close();
  }
});

test("SSE model drift mid-stream fails the node instead of succeeding", async () => {
  const server = http.createServer((_req, res) => {
    res.writeHead(200, { "Content-Type": "text/event-stream" });
    res.end(
      'data: {"model":"loopback-team","choices":[{"delta":{"content":"hel"}}]}\n\n' +
        'data: {"model":"other-model","choices":[{"delta":{"content":"lo"}}]}\n\n' +
        "data: [DONE]\n\n",
    );
  });
  const listening = await new Promise<{ baseUrl: string; close: () => Promise<void> }>((resolve) => {
    server.listen(0, "127.0.0.1", () => {
      const address = server.address() as AddressInfo;
      resolve({
        baseUrl: `http://127.0.0.1:${address.port}/v1`,
        close: () => new Promise((done) => server.close(() => done())),
      });
    });
  });
  const transport = createOpenAiCompatibleTransport();
  try {
    await rejectCode(
      transport.complete(
        { ...credentials(listening.baseUrl), messages: [{ role: "user", content: "hi" }] },
        new AbortController().signal,
      ),
      "desktop_provider_model_identity_drift",
    );
  } finally {
    await listening.close();
  }
});

test("legacy success update is rejected; settle is the only success path", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: credentials("http://127.0.0.1:9/v1"),
  });
  const started = await host.startTeamRun(executeInput());
  await host.submitProposal({
    workspaceId: WORKSPACE,
    teamRunId: started.teamRun.id,
    proposal: {
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
              objective: "subtask",
              dependsOnAssignmentIds: [],
              expectedOutput: "report",
              contextRequirements: [],
            },
          ],
        },
      ],
      finalSynthesisRequired: true,
    },
  });
  const created = await host.createNode({
    workspaceId: WORKSPACE,
    teamRunId: started.teamRun.id,
    assignmentId: "frontend-review",
    employeeRoleId: "frontend",
    invocationId: `invocation_${"a".repeat(32)}`,
    waveId: "wave-1",
    nodeEpoch: 1,
    sendEpoch: 1,
    providerId: PROVIDER_ID,
    requestedModel: "loopback-team",
  });
  await rejectCode(
    host.updateNode({
      workspaceId: WORKSPACE,
      teamRunId: started.teamRun.id,
      nodeId: created.node.id,
      state: "succeeded" as "failed" | "cancelled" | "unknown",
      actualModel: "loopback-team",
      inputTokens: 1,
      outputTokens: 1,
      totalTokens: 2,
      answerSha256: "a".repeat(64),
      errorCode: null,
      durationMs: 1,
    }),
    "desktop_team_success_requires_settle",
  );
  await host.settleNode({
    workspaceId: WORKSPACE,
    teamRunId: started.teamRun.id,
    nodeId: created.node.id,
    invocationId: `invocation_${"a".repeat(32)}`,
    state: "succeeded",
    actualModel: "loopback-team",
    inputTokens: 1,
    outputTokens: 1,
    totalTokens: 2,
    answerSha256: "a".repeat(64),
    errorCode: null,
    durationMs: 1,
    waveId: "wave-1",
    nodeEpoch: 1,
    sendEpoch: 1,
    report: {
      assignmentId: "frontend-review",
      employeeRoleId: "frontend",
      status: "completed",
      report: "frontend completed frontend-review",
      collaborationRequests: [],
    },
  });
  await rejectCode(
    host.settleNode({
      workspaceId: WORKSPACE,
      teamRunId: started.teamRun.id,
      nodeId: created.node.id,
      invocationId: `invocation_${"a".repeat(32)}`,
      state: "succeeded",
      actualModel: "loopback-team",
      inputTokens: 1,
      outputTokens: 1,
      totalTokens: 2,
      answerSha256: "a".repeat(64),
      errorCode: null,
      durationMs: 1,
      waveId: "wave-1",
      nodeEpoch: 1,
      sendEpoch: 1,
      report: {
        assignmentId: "frontend-review",
        employeeRoleId: "frontend",
        status: "completed",
        report: "frontend completed frontend-review",
        collaborationRequests: [],
      },
    }),
    "desktop_team_node_terminal",
  );
});

test("POST report on a running in-memory node fails closed without a settle audit", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: credentials("http://127.0.0.1:9/v1"),
  });
  const started = await host.startTeamRun(executeInput());
  await host.submitProposal({
    workspaceId: WORKSPACE,
    teamRunId: started.teamRun.id,
    proposal: {
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
              objective: "subtask",
              dependsOnAssignmentIds: [],
              expectedOutput: "report",
              contextRequirements: [],
            },
          ],
        },
      ],
      finalSynthesisRequired: true,
    },
  });
  const created = await host.createNode({
    workspaceId: WORKSPACE,
    teamRunId: started.teamRun.id,
    assignmentId: "frontend-review",
    employeeRoleId: "frontend",
    invocationId: `invocation_${"a".repeat(32)}`,
    waveId: "wave-1",
    nodeEpoch: 1,
    sendEpoch: 1,
    providerId: PROVIDER_ID,
    requestedModel: "loopback-team",
  });
  await rejectCode(
    host.recordReport({
      workspaceId: WORKSPACE,
      teamRunId: started.teamRun.id,
      nodeId: created.node.id,
      invocationId: `invocation_${"a".repeat(32)}`,
      report: {
        assignmentId: "frontend-review",
        employeeRoleId: "frontend",
        status: "completed",
        report: "frontend completed frontend-review",
        collaborationRequests: [],
      },
    }),
    "desktop_team_report_requires_settle",
  );
});

test("stale list-enabled then vault-disabled fails closed without decrypt", async () => {
  const vault = memoryVault();
  let decrypted = false;
  const wrapped = {
    ...vault,
    decryptString: (encrypted: Buffer) => {
      decrypted = true;
      return vault.decryptString(encrypted);
    },
  };
  const host = createNativePersonalTeamHost({
    client: {
      getAgentRole: async () => ({ ok: true, value: { role: sampleRole() } }),
      listProviders: async () => ({
        ok: true,
        value: { items: [sampleProvider({ isEnabled: true })] },
      }),
      getProviderVault: async () => ({
        ok: false as const,
        error: { code: "desktop_provider_disabled" },
      }),
    } as unknown as DesktopNativeClient,
    vault: wrapped,
  });
  await rejectCode(
    host.resolveCredentials(WORKSPACE, "parent", new AbortController().signal),
    "desktop_provider_disabled",
  );
  assert.equal(decrypted, false);
});

function unwrap<T>(result: DesktopOperationResult<T>, label: string): T {
  if (!result.ok) {
    throw new Error(`${label} failed: ${result.error.code}`);
  }
  return result.value;
}

async function waitForOwner(client: DesktopNativeClient): Promise<void> {
  const deadline = Date.now() + 20_000;
  let last = "desktop_native_request_failed";
  while (Date.now() < deadline) {
    const status = await client.getOwnerStatus();
    if (status.ok) return;
    last = status.error.code;
    await new Promise((resolve) => setTimeout(resolve, 150));
  }
  throw new Error(`desktop-local did not become ready: ${last}`);
}

test(
  "RuntimeManager DesktopNativeClient desktop-local HTTP SQLite journey records report and audit",
  { timeout: 60_000 },
  async () => {
    const python = process.env.PYTHON ?? "python";
    const dataRoot = fs.mkdtempSync(
      path.join(process.env.LOCALAPPDATA ?? os.tmpdir(), "omnibase-p69-r2-"),
    );
    const token = "a".repeat(64);
    const proof = "c".repeat(64);
    const control = "e".repeat(64);
    const port = 49_100 + (process.pid % 500);
    const chat = startLoopbackChat();
    const listening = await chat.listen();
    const child = spawn(
      python,
      ["-m", "omnibase.desktop_local.app", "--data-root", dataRoot, "--port", String(port)],
      {
        cwd: REPO_ROOT,
        env: {
          ...process.env,
          PYTHONPATH: [path.join(REPO_ROOT, "backend", "src"), process.env.PYTHONPATH ?? ""]
            .filter((item) => item.length > 0)
            .join(path.delimiter),
          OMNIBASE_DESKTOP_INSTANCE_TOKEN: token,
          OMNIBASE_DESKTOP_NATIVE_PROOF_KEY: proof,
          OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN: control,
        },
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    const stderr: Buffer[] = [];
    child.stderr?.on("data", (chunk: Buffer) => stderr.push(chunk));
    const vault = memoryVault();
    const encrypted = encryptProviderSecret(SECRET, vault);
    const client = new DesktopNativeClient({
      backendOrigin: `http://127.0.0.1:${port}`,
      nativeControlToken: control,
    });
    const events: DesktopTeamRunEvent[] = [];
    try {
      await waitForOwner(client);
      unwrap(await client.bootstrapOwner({ displayName: "Local Owner" }), "bootstrap");
      const workspace = unwrap(await client.createWorkspace({ name: "Team Space" }), "workspace");
      const conversation = unwrap(
        await client.createConversation({ workspaceId: workspace.workspace.id, title: "团队任务" }),
        "conversation",
      );
      const provider = unwrap(
        await client.upsertProvider({
          display_name: "loopback",
          base_url: listening.baseUrl,
          model_name: "loopback-team",
          gear: "standard",
          thinking_depth: "low",
          timeout_seconds: 15,
          allow_loopback_http: true,
          is_default: true,
          is_enabled: true,
          credential_reference: encrypted.credentialReference,
          encrypted_secret_blob: encrypted.encryptedSecretBlob,
          secret_fingerprint: encrypted.secretFingerprint,
        }),
        "provider",
      );
      const manager = new RuntimeManager({
        runtimeRoot: path.join(dataRoot, "missing-runtime"),
        expectedManifestSha256: "0".repeat(64),
        uiOrigin: "http://127.0.0.1:3000",
        dataRoot,
        secretVault: vault,
        nativeClientForTests: client,
      });
      const result = await manager.executeTeamRun(
        {
          workspaceId: workspace.workspace.id,
          conversationId: conversation.conversation.id,
          task: "review the desktop team design",
          teamMode: true,
          rosterEpoch: 1,
          budget: { ...DEFAULT_TEAM_RUN_BUDGET, maximumProviderCalls: 24 },
        },
        (event) => events.push(event),
      );
      assert.equal(result.ok, true, result.ok ? "" : result.error.code);
      if (!result.ok) return;
      assert.equal(result.value.proof.state, "succeeded");
      assert.equal(result.value.proof.executedNodeCount, 1);
      const dbPath = path.join(dataRoot, "state", "omnibase.sqlite3");
      const probed = spawnSync(
        python,
        [
          "-c",
          "import json,sqlite3,sys; db=sqlite3.connect(sys.argv[1]); print(json.dumps({" +
            '"reports": db.execute("SELECT COUNT(*) FROM team_employee_report").fetchone()[0],' +
            '"audits": db.execute("SELECT COUNT(*) FROM audit_event WHERE event_type=\'team_node_settled\'").fetchone()[0],' +
            '"schema": db.execute("PRAGMA user_version").fetchone()[0]}))',
          dbPath,
        ],
        { encoding: "utf8" },
      );
      assert.equal(probed.status, 0, probed.stderr);
      const counts = JSON.parse(probed.stdout) as {
        reports: number;
        audits: number;
        schema: number;
      };
      assert.equal(counts.schema, 6);
      assert.equal(counts.reports, 1);
      assert.equal(counts.audits, 1);
      assert.ok(events.some((item) => item.type === "node_starting"));
      assert.equal(provider.provider.isEnabled, true);
    } finally {
      await listening.close();
      if (child.pid !== undefined) {
        child.kill();
      }
      if (stderr.length > 0 && child.exitCode && child.exitCode !== 0) {
        process.stderr.write(Buffer.concat(stderr));
      }
    }
  },
);
