import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import test from "node:test";

import path from "node:path";

import { createHmac } from "node:crypto";
import { lstat, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";

import {
  buildRuntimeEnvironment,
  matchesRuntimeInstanceProof,
  prepareRuntimeDataRoot,
  recoverWorkspaceComponentsOnStartup,
  RuntimeManager,
  type RuntimeManagerNativeClientForTests,
} from "../src/runtime/runtime-manager.ts";
import { encryptProviderSecret } from "../src/runtime/secret-vault.ts";
import type {
  DesktopTeamRun,
  DesktopTeamRunEvent,
  TeamRunState,
} from "../src/shared/personal-team.ts";
import type {
  DesktopConversationEvent,
  DesktopConversationSendInput,
  DesktopOperationResult,
  DesktopProviderList,
} from "../src/shared/ipc-contract.ts";

test("runtime health requires an HMAC proof without exposing the instance token", () => {
  const token = "a".repeat(64);
  const challenge = "b".repeat(64);
  const proof = createHmac("sha256", Buffer.from(token, "hex"))
    .update(challenge, "ascii")
    .digest("hex");
  assert.equal(matchesRuntimeInstanceProof(proof, challenge, token), true);
  assert.equal(
    matchesRuntimeInstanceProof("c".repeat(64), challenge, token),
    false,
  );
  assert.equal(matchesRuntimeInstanceProof(null, challenge, token), false);
  assert.equal(matchesRuntimeInstanceProof("not-hex", challenge, token), false);
  assert.equal(matchesRuntimeInstanceProof(proof, "not-hex", token), false);
  assert.equal(matchesRuntimeInstanceProof(proof, challenge, "not-hex"), false);
});

test("runtime environment is an explicit safe closed set", () => {
  const proofKey = "a".repeat(64);
  const controlToken = "b".repeat(64);
  const dataRoot = path.resolve("C:/Users/Alice/AppData/Local/OmniBase");
  const environment = buildRuntimeEnvironment(
    proofKey,
    controlToken,
    dataRoot,
    {
      SystemRoot: "C:\\Windows",
      TEMP: "C:\\Temp",
      PATH: "C:\\attacker-controlled-bin",
      OPENAI_API_KEY: "must-not-pass",
    },
  );
  assert.deepEqual(environment, {
    OMNIBASE_DESKTOP_MODE: "1",
    OMNIBASE_BIND_HOST: "127.0.0.1",
    OMNIBASE_DESKTOP_NATIVE_PROOF_KEY: proofKey,
    OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN: controlToken,
    OMNIBASE_DESKTOP_DATA_ROOT: dataRoot,
    SystemRoot: "C:\\Windows",
    TEMP: "C:\\Temp",
  });
  assert.equal("PATH" in environment, false);
  assert.equal("OPENAI_API_KEY" in environment, false);
  assert.throws(
    () => buildRuntimeEnvironment(proofKey, "invalid", dataRoot, {}),
    /runtime_environment_invalid/u,
  );
});

test("runtime data root is created once and must remain an ordinary directory", async () => {
  const parent = await mkdtemp(path.join(os.tmpdir(), "omnibase-data-root-"));
  const dataRoot = path.join(parent, "OmniBase");
  try {
    await prepareRuntimeDataRoot(dataRoot);
    await prepareRuntimeDataRoot(dataRoot);
    const metadata = await lstat(dataRoot);
    assert.equal(metadata.isDirectory(), true);
    assert.equal(metadata.isSymbolicLink(), false);

    const invalid = path.join(parent, "not-a-directory");
    await writeFile(invalid, "not a data directory", { encoding: "utf8" });
    await assert.rejects(
      () => prepareRuntimeDataRoot(invalid),
      /runtime_data_root_identity_invalid/u,
    );
  } finally {
    await rm(parent, { recursive: true, force: true });
  }
});

test("fresh runtime without an Owner skips Workspace component recovery", async () => {
  let workspaceListCalls = 0;
  let recoveryCalls = 0;
  await recoverWorkspaceComponentsOnStartup(
    {
      getOwnerStatus: async () => ({
        ok: true,
        value: { initialized: false, owner: null },
      }),
      listWorkspaces: async () => {
        workspaceListCalls += 1;
        return { ok: true, value: { items: [] } };
      },
      getWorkspaceComponents: async () => {
        throw new Error("fresh_runtime_must_not_read_components");
      },
      settleWorkspaceComponentRecovery: async () => {
        throw new Error("fresh_runtime_must_not_settle_recovery");
      },
    },
    async () => {
      recoveryCalls += 1;
    },
  );
  assert.equal(workspaceListCalls, 0);
  assert.equal(recoveryCalls, 0);
});

test("initialized runtime preserves fail-closed Workspace component recovery", async () => {
  const calls: string[] = [];
  const activeWorkspace = {
    id: WORKSPACE_ID,
    ownerId: `owner_${"0".repeat(32)}`,
    name: "Active",
    state: "active" as const,
    rowVersion: 1,
    createdAt: "2026-08-30T00:00:00Z",
    updatedAt: "2026-08-30T00:00:00Z",
  };
  const archivedWorkspace = {
    ...activeWorkspace,
    id: `workspace_${"a".repeat(32)}`,
    name: "Archived",
    state: "archived" as const,
  };
  const snapshot = {
    workspaceId: WORKSPACE_ID,
    catalog: [],
    proposals: [],
    installations: [],
    grants: [],
    operations: [],
    effects: [],
    reconciliations: [],
    revocations: [],
    recoveries: [
      {
        recoveryId: `recovery_${"1".repeat(32)}`,
        workspaceId: WORKSPACE_ID,
        componentId: "omnibase.source.declarative-ui",
        installationId: `installation_${"2".repeat(32)}`,
        bindingGeneration: 1,
        previousRuntimeInstanceId: `runtime_${"3".repeat(32)}`,
        operationId: `operation_${"4".repeat(32)}`,
        effectId: `effect_${"a".repeat(32)}`,
        adapterId: "builtin-ui.v1" as const,
        requestSha256: "5".repeat(64),
        manifestSha256: "6".repeat(64),
        packageSha256: "7".repeat(64),
        state: "pending" as const,
        reasonCode: "startup_native_revalidation_required",
        runtimeInstanceId: `runtime_${"8".repeat(32)}`,
        workloadIdentityDigest: "9".repeat(64),
        createdAt: "2026-08-30T00:00:00Z",
      },
    ],
    audit: [],
  };
  await recoverWorkspaceComponentsOnStartup(
    {
      getOwnerStatus: async () => ({
        ok: true,
        value: {
          initialized: true,
          owner: {
            id: `owner_${"0".repeat(32)}`,
            displayName: "Owner",
            createdAt: "2026-08-30T00:00:00Z",
            updatedAt: "2026-08-30T00:00:00Z",
          },
        },
      }),
      listWorkspaces: async () => ({
        ok: true,
        value: { items: [activeWorkspace, archivedWorkspace] },
      }),
      getWorkspaceComponents: async (input) => {
        calls.push(`snapshot:${input.workspaceId}`);
        return { ok: true, value: snapshot };
      },
      settleWorkspaceComponentRecovery: async () => {
        throw new Error("settlement_is_owned_by_handler");
      },
    },
    async ({ recovery }) => {
      calls.push(`recovery:${recovery.recoveryId}`);
    },
  );
  assert.deepEqual(calls, [
    `snapshot:${WORKSPACE_ID}`,
    `recovery:recovery_${"1".repeat(32)}`,
  ]);
});

test("runtime manager does not expose an absolute bundle path on verification failure", async () => {
  const missingRoot = path.resolve(
    `C:/omnibase-missing-runtime-${process.pid}-${Date.now()}`,
  );
  const manager = new RuntimeManager({
    runtimeRoot: missingRoot,
    expectedManifestSha256: "0".repeat(64),
    uiOrigin: "http://127.0.0.1:3000",
    dataRoot: path.resolve("C:/Users/Alice/AppData/Local/OmniBase"),
  });
  const status = await manager.start();
  assert.equal(status.phase, "failed");
  assert.doesNotMatch(status.lastError ?? "", /omnibase-missing-runtime/u);
  assert.match(status.lastError ?? "", /\[PATH\]/u);
});

test("runtime manager start is single-flight and stop cancels verification", async () => {
  const missingRoot = path.resolve(
    `C:/omnibase-missing-runtime-single-flight-${process.pid}-${Date.now()}`,
  );
  const manager = new RuntimeManager({
    runtimeRoot: missingRoot,
    expectedManifestSha256: "0".repeat(64),
    uiOrigin: "http://127.0.0.1:3000",
    dataRoot: path.resolve("C:/Users/Alice/AppData/Local/OmniBase"),
  });

  const first = manager.start();
  const second = manager.start();
  assert.equal(first, second);
  assert.equal(manager.stop().phase, "stopped");
  assert.equal((await first).phase, "stopped");
  assert.equal(manager.getStatus().phase, "stopped");
});

test("abortInFlightSend without a live stream does not require an invocation id", async () => {
  const missingRoot = path.resolve(
    `C:/omnibase-missing-runtime-abort-${process.pid}-${Date.now()}`,
  );
  const manager = new RuntimeManager({
    runtimeRoot: missingRoot,
    expectedManifestSha256: "0".repeat(64),
    uiOrigin: "http://127.0.0.1:3000",
    dataRoot: path.resolve("C:/Users/Alice/AppData/Local/OmniBase"),
  });
  const result = await manager.abortInFlightSend();
  assert.equal(result.ok, true);
  if (result.ok) assert.equal(result.value.aborted, false);
});

const WORKSPACE_ID = `workspace_${"b".repeat(32)}`;
const CONVERSATION_ID = `conversation_${"c".repeat(32)}`;
const PROVIDER_ID = `provider_${"d".repeat(32)}`;
const PROVIDER_SECRET = "isolation-provider-secret-not-for-git";

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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function testProviderList(baseUrl = "http://127.0.0.1:9"): DesktopProviderList {
  return Object.freeze({
    items: Object.freeze([
      Object.freeze({
        id: PROVIDER_ID,
        displayName: "loopback",
        baseUrl,
        modelName: "fake-model",
        family: "generic-openai-compatible" as const,
        gear: "standard" as const,
        thinkingDepth: "disabled" as const,
        timeoutSeconds: 30,
        allowLoopbackHttp: true,
        isDefault: true,
        isEnabled: true,
        hasSecret: true as const,
        createdAt: "2026-08-20T00:00:00Z",
        updatedAt: "2026-08-20T00:00:00Z",
      }),
    ]),
  });
}

function sendInput(sendEpoch = 1): DesktopConversationSendInput {
  return {
    workspaceId: WORKSPACE_ID,
    conversationId: CONVERSATION_ID,
    content: "hello",
    sendEpoch,
  };
}

function createSendManager(options: {
  readonly listProviders: () => Promise<DesktopOperationResult<DesktopProviderList>>;
  readonly getProviderVault: (
    providerId: string,
  ) => Promise<DesktopOperationResult<{ encryptedSecretBlob: string }>>;
  readonly sendConversation: (
    input: DesktopConversationSendInput,
    secret: string,
    emit: (event: DesktopConversationEvent) => void,
    signal: AbortSignal,
  ) => Promise<DesktopOperationResult<DesktopConversationEvent>>;
  readonly secretVault?: ReturnType<typeof memoryVault>;
}) {
  const missingRoot = path.resolve(
    `C:/omnibase-missing-runtime-send-${process.pid}-${Date.now()}-${Math.random().toString(16).slice(2)}`,
  );
  return new RuntimeManager({
    runtimeRoot: missingRoot,
    expectedManifestSha256: "0".repeat(64),
    uiOrigin: "http://127.0.0.1:3000",
    dataRoot: path.resolve("C:/Users/Alice/AppData/Local/OmniBase"),
    secretVault: options.secretVault ?? memoryVault(),
    nativeClientForTests: options,
  });
}

test("abortInFlightSend during getProviderVault prevents later messages fetch and settles send", async () => {
  const vault = memoryVault();
  const encrypted = encryptProviderSecret(PROVIDER_SECRET, vault);
  const vaultEntered = deferred<void>();
  const vaultGate = deferred<DesktopOperationResult<{ encryptedSecretBlob: string }>>();
  let messagesFetchStarted = 0;
  const manager = createSendManager({
    listProviders: async () => ({ ok: true, value: testProviderList() }),
    getProviderVault: async () => {
      vaultEntered.resolve();
      return vaultGate.promise;
    },
    sendConversation: async (_input, _secret, _emit, signal) => {
      messagesFetchStarted += 1;
      if (signal.aborted) {
        return {
          ok: true,
          value: { type: "cancelled", invocationId: "invocation_cancelled_locally" },
        };
      }
      await new Promise<void>(() => undefined);
      return {
        ok: false,
        error: { code: "desktop_native_request_failed" },
      };
    },
    secretVault: vault,
  });

  const pending = manager.sendConversation(sendInput(4), () => undefined);
  await vaultEntered.promise;
  const abortResult = await manager.abortInFlightSend();
  assert.equal(abortResult.ok, true);
  if (abortResult.ok) assert.equal(abortResult.value.aborted, true);

  vaultGate.resolve({
    ok: true,
    value: { encryptedSecretBlob: encrypted.encryptedSecretBlob },
  });

  const sendResult = await pending;
  assert.equal(sendResult.ok, true);
  if (sendResult.ok) {
    assert.equal(sendResult.value.type, "cancelled");
    assert.equal(sendResult.value.sendEpoch, 4);
  }
  assert.equal(messagesFetchStarted, 0);
});

test("abortInFlightSend during hung listProviders settles send without starting messages fetch", async () => {
  let messagesFetchStarted = 0;
  const manager = createSendManager({
    listProviders: () => new Promise(() => undefined),
    getProviderVault: async () => {
      throw new Error("vault_must_not_run");
    },
    sendConversation: async () => {
      messagesFetchStarted += 1;
      return {
        ok: false,
        error: { code: "desktop_native_request_failed" },
      };
    },
  });

  const pending = manager.sendConversation(sendInput(2), () => undefined);
  await Promise.resolve();
  const abortResult = await manager.abortInFlightSend();
  assert.equal(abortResult.ok, true);
  if (abortResult.ok) assert.equal(abortResult.value.aborted, true);
  const sendResult = await pending;
  assert.equal(sendResult.ok, true);
  if (sendResult.ok) assert.equal(sendResult.value.type, "cancelled");
  assert.equal(messagesFetchStarted, 0);
});

test("idle abortInFlightSend does not latch and poison the next send", async () => {
  const vault = memoryVault();
  const encrypted = encryptProviderSecret(PROVIDER_SECRET, vault);
  let messagesFetchStarted = 0;
  const manager = createSendManager({
    listProviders: async () => ({ ok: true, value: testProviderList() }),
    getProviderVault: async () => ({
      ok: true,
      value: { encryptedSecretBlob: encrypted.encryptedSecretBlob },
    }),
    sendConversation: async (input, _secret, _emit, signal) => {
      messagesFetchStarted += 1;
      if (signal.aborted) {
        return {
          ok: true,
          value: {
            type: "cancelled",
            invocationId: "invocation_cancelled_locally",
            sendEpoch: input.sendEpoch,
          },
        };
      }
      return {
        ok: true,
        value: {
          type: "done",
          invocationId: `invocation_${"e".repeat(32)}`,
          sendEpoch: input.sendEpoch,
        },
      };
    },
    secretVault: vault,
  });

  const idleAbort = await manager.abortInFlightSend();
  assert.equal(idleAbort.ok, true);
  if (idleAbort.ok) assert.equal(idleAbort.value.aborted, false);

  const sendResult = await manager.sendConversation(sendInput(9), () => undefined);
  assert.equal(sendResult.ok, true);
  if (sendResult.ok) assert.equal(sendResult.value.type, "done");
  assert.equal(messagesFetchStarted, 1);
});

const TEAM_RUN_ID = `teamrun_${"f".repeat(32)}`;
const TEAM_PLAN_ID = `teamrev_${"1".repeat(32)}`;

function testTeamRun(state: TeamRunState): DesktopTeamRun {
  return Object.freeze({
    id: TEAM_RUN_ID,
    workspaceId: WORKSPACE_ID,
    conversationId: CONVERSATION_ID,
    mode: "team" as const,
    state,
    staffingAuthority: "parent_proposal" as const,
    currentPlanRevisionId: state === "preparing" ? null : TEAM_PLAN_ID,
    currentWaveId: null,
    dispatchedParticipantCount: 0,
    maximumProviderCalls: 4,
    maximumWallTimeMs: 5_000,
    maximumConcurrentCalls: 1,
    maximumInputCharacters: 4_096,
    maximumOutputCharacters: 4_096,
    consumedProviderCalls: state === "preparing" ? 0 : 1,
    task: "runtime manager stop linearization",
    allowedSpecialistRoleIds: Object.freeze([
      "product",
      "ux",
      "frontend",
      "backend",
      "data",
      "security",
      "qa",
      "operations",
      "docs",
    ] as const),
    createdAt: "2026-08-24T00:00:00Z",
    updatedAt: "2026-08-24T00:00:00Z",
  });
}

test("RuntimeManager preserves Stop in the coordinator pre-start handoff gap", async () => {
  let manager!: RuntimeManager;
  let abortResult: ReturnType<RuntimeManager["abortInFlightSend"]> | null = null;
  let run = testTeamRun("preparing");
  let startCalls = 0;
  let terminalCalls = 0;
  let providerBoundaryCalls = 0;
  const baseClient = {
    async listProviders() {
      providerBoundaryCalls += 1;
      return { ok: true as const, value: testProviderList() };
    },
    async getProviderVault() {
      providerBoundaryCalls += 1;
      return { ok: false as const, error: { code: "must_not_resolve_credentials" } };
    },
    async sendConversation() {
      return { ok: false as const, error: { code: "unused_single_agent_send" } };
    },
    async startTeamRun() {
      startCalls += 1;
      return { ok: true as const, value: { teamRun: run } };
    },
    async setTeamRunState(input: { state: TeamRunState }) {
      terminalCalls += 1;
      run = Object.freeze({ ...run, state: input.state });
      return { ok: true as const, value: { teamRun: run } };
    },
  };
  let trapped = false;
  const client = new Proxy(baseClient, {
    get(target, property, receiver) {
      if (property === "pinProviderEndpoint" && !trapped) {
        trapped = true;
        abortResult = manager.abortInFlightSend();
      }
      return Reflect.get(target, property, receiver);
    },
  }) as unknown as RuntimeManagerNativeClientForTests;
  manager = new RuntimeManager({
    runtimeRoot: path.resolve("C:/omnibase-runtime-manager-prestart-stop-test"),
    expectedManifestSha256: "0".repeat(64),
    uiOrigin: "http://127.0.0.1:3000",
    dataRoot: path.resolve("C:/Users/Alice/AppData/Local/OmniBase"),
    secretVault: memoryVault(),
    nativeClientForTests: client,
  });

  const executed = await manager.executeTeamRun(
    {
      workspaceId: WORKSPACE_ID,
      conversationId: CONVERSATION_ID,
      task: "pre-start handoff Stop",
      teamMode: true,
      rosterEpoch: 10,
      budget: {
        maximumProviderCalls: 4,
        maximumWallTimeMs: 5_000,
        maximumConcurrentCalls: 1,
        maximumInputCharacters: 4_096,
        maximumOutputCharacters: 4_096,
      },
    },
    () => undefined,
  );
  assert.notEqual(abortResult, null);
  const stopped = await abortResult!;
  assert.equal(stopped.ok, true);
  if (stopped.ok) assert.equal(stopped.value.aborted, true);
  assert.equal(executed.ok, true);
  if (executed.ok) assert.equal(executed.value.proof.state, "cancelled");
  assert.equal(run.state, "cancelled");
  assert.equal(startCalls, 1);
  assert.equal(terminalCalls, 1);
  assert.equal(providerBoundaryCalls, 0);
});

async function createTeamSuccessHarness() {
  const server = createServer((_request, response) => {
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(
      JSON.stringify({
        model: "fake-model",
        choices: [
          {
            message: {
              role: "assistant",
              content: JSON.stringify({
                decision: "answer_directly",
                answer: "validated runtime-manager answer",
                reason: "no specialist needed",
              }),
            },
          },
        ],
        usage: { prompt_tokens: 2, completion_tokens: 3, total_tokens: 5 },
      }),
    );
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as AddressInfo;
  const baseUrl = `http://127.0.0.1:${address.port}/v1`;
  const vault = memoryVault();
  const encrypted = encryptProviderSecret(PROVIDER_SECRET, vault);
  const successEntered = deferred<void>();
  const successGate = deferred<void>();
  let run = testTeamRun("preparing");
  let nativeCancelCalls = 0;
  let pendingParentCall: {
    invocationId: string;
    purpose: "parent-propose" | "parent-replan" | "parent-synthesize";
    providerId: string;
    requestedModel: string;
    createdAt: string;
  } | null = null;

  const client: RuntimeManagerNativeClientForTests = {
    async listProviders() {
      return { ok: true, value: testProviderList(baseUrl) };
    },
    async getProviderVault() {
      return {
        ok: true,
        value: { encryptedSecretBlob: encrypted.encryptedSecretBlob },
      };
    },
    async sendConversation() {
      return { ok: false, error: { code: "unused_single_agent_send" } };
    },
    async startTeamRun(input) {
      run = Object.freeze({
        ...testTeamRun("preparing"),
        task: input.task,
        allowedSpecialistRoleIds:
          input.allowedSpecialistRoleIds ?? testTeamRun("preparing").allowedSpecialistRoleIds,
      });
      return { ok: true, value: { teamRun: run } };
    },
    async submitTeamProposal(input) {
      run = Object.freeze({
        ...run,
        currentPlanRevisionId: TEAM_PLAN_ID,
        updatedAt: "2026-08-24T00:00:01Z",
      });
      return {
        ok: true,
        value: {
          accepted: true,
          validationErrorCode: null,
          teamRun: run,
          planRevision: {
            id: TEAM_PLAN_ID,
            revisionOrdinal: 1,
            decision: input.proposal.decision,
            proposalJsonSha256: "2".repeat(64),
            validated: true,
            validationErrorCode: null,
            createdAt: "2026-08-24T00:00:01Z",
          },
        },
      };
    },
    async getTeamBlackboard() {
      return {
        ok: true,
        value: {
          blackboard: {
            teamRunId: TEAM_RUN_ID,
            workspaceId: WORKSPACE_ID,
            ownerObjective: run.task,
            currentPlanRevisionId: run.currentPlanRevisionId,
            assignments: [],
            reports: [],
            collaborationRequests: [],
          },
        },
      };
    },
    async consumeTeamProviderCall(input) {
      run = Object.freeze({ ...run, consumedProviderCalls: run.consumedProviderCalls + 1 });
      if (input.purpose === "employee") {
        return { ok: true, value: { teamRun: run } };
      }
      pendingParentCall = {
        invocationId: input.invocationId,
        purpose: input.purpose,
        providerId: input.providerId,
        requestedModel: input.requestedModel,
        createdAt: "2026-08-24T00:00:00Z",
      };
      return {
        ok: true,
        value: {
          teamRun: run,
          parentCall: {
            invocationId: input.invocationId,
            teamRunId: TEAM_RUN_ID,
            planRevisionId: null,
            purpose: input.purpose,
            state: "pending" as const,
            providerId: input.providerId,
            requestedModel: input.requestedModel,
            actualModel: null,
            inputTokens: null,
            outputTokens: null,
            totalTokens: null,
            outputSha256: null,
            errorCode: null,
            createdAt: "2026-08-24T00:00:00Z",
            updatedAt: "2026-08-24T00:00:00Z",
          },
        },
      };
    },
    async settleTeamParentCall(input) {
      assert.notEqual(pendingParentCall, null);
      assert.equal(input.invocationId, pendingParentCall?.invocationId);
      assert.equal(input.purpose, pendingParentCall?.purpose);
      assert.equal(input.providerId, pendingParentCall?.providerId);
      assert.equal(input.requestedModel, pendingParentCall?.requestedModel);
      const createdAt = pendingParentCall?.createdAt ?? "2026-08-24T00:00:00Z";
      pendingParentCall = null;
      return {
        ok: true,
        value: {
          parentCall: {
            invocationId: input.invocationId,
            teamRunId: TEAM_RUN_ID,
            planRevisionId: input.planRevisionId,
            purpose: input.purpose,
            state: input.state,
            providerId: input.providerId,
            requestedModel: input.requestedModel,
            actualModel: input.actualModel,
            inputTokens: input.inputTokens,
            outputTokens: input.outputTokens,
            totalTokens: input.totalTokens,
            outputSha256: input.outputSha256,
            errorCode: input.errorCode,
            createdAt,
            updatedAt: "2026-08-24T00:00:01Z",
          },
        },
      };
    },
    async setTeamRunState(input) {
      if (input.state === "succeeded") {
        successEntered.resolve();
        await successGate.promise;
      }
      const state = input.state as TeamRunState;
      run = Object.freeze({ ...run, state, updatedAt: "2026-08-24T00:00:02Z" });
      return { ok: true, value: { teamRun: run } };
    },
    async getAgentRole(input) {
      return {
        ok: true,
        value: {
          role: {
            id: input.roleId,
            displayName: "Parent",
            responsibility: "Coordinate",
            defaultState: "active",
            mayJoinTeam: false,
            providerId: null,
            modelNameOverride: null,
            gear: "standard",
            thinkingDepth: "disabled",
            rowVersion: 1,
            verificationState: "unverified",
            verifiedActualModel: null,
            inheritedProvider: true,
            resolvedProviderId: PROVIDER_ID,
            resolvedModelName: "fake-model",
            secretFingerprint: "3".repeat(16),
            hasSecret: true,
          },
        },
      };
    },
    async cancelTeamRun() {
      nativeCancelCalls += 1;
      if (run.state === "succeeded") {
        return {
          ok: true,
          value: { cancelled: false, accepted: false, teamRun: run },
        };
      }
      run = Object.freeze({ ...run, state: "cancelled" });
      return {
        ok: true,
        value: { cancelled: true, accepted: true, teamRun: run },
      };
    },
  };
  const manager = new RuntimeManager({
    runtimeRoot: path.resolve("C:/omnibase-runtime-manager-team-test"),
    expectedManifestSha256: "0".repeat(64),
    uiOrigin: "http://127.0.0.1:3000",
    dataRoot: path.resolve("C:/Users/Alice/AppData/Local/OmniBase"),
    secretVault: vault,
    nativeClientForTests: client,
  });
  const execute = (emit: (event: DesktopTeamRunEvent) => void) =>
    manager.executeTeamRun(
      {
        workspaceId: WORKSPACE_ID,
        conversationId: CONVERSATION_ID,
        task: "runtime manager stop linearization",
        teamMode: true,
        rosterEpoch: 9,
        budget: {
          maximumProviderCalls: 4,
          maximumWallTimeMs: 5_000,
          maximumConcurrentCalls: 1,
          maximumInputCharacters: 4_096,
          maximumOutputCharacters: 4_096,
        },
      },
      emit,
    );
  return {
    manager,
    execute,
    successEntered: successEntered.promise,
    releaseSuccess: () => successGate.resolve(),
    nativeCancelCalls: () => nativeCancelCalls,
    close: () => new Promise<void>((resolve, reject) => server.close((error) => (error ? reject(error) : resolve()))),
  };
}

test("team abort waits for an entered success commit and does not report a false local acceptance", async () => {
  const harness = await createTeamSuccessHarness();
  const execution = harness.execute(() => undefined);
  try {
    await harness.successEntered;
    let stopSettled = false;
    const stop = harness.manager.abortInFlightSend().then((result) => {
      stopSettled = true;
      return result;
    });
    await Promise.resolve();
    assert.equal(stopSettled, false);
    harness.releaseSuccess();
    const stopResult = await stop;
    assert.equal(stopResult.ok, true);
    if (stopResult.ok) assert.equal(stopResult.value.aborted, false);
    const executed = await execution;
    assert.equal(executed.ok, true);
    if (executed.ok) assert.equal(executed.value.proof.state, "succeeded");
    assert.equal(harness.nativeCancelCalls(), 0);
  } finally {
    harness.releaseSuccess();
    await execution;
    await harness.close();
  }
});

test("team cancel waits for entered success then preserves quiet-terminal accepted=false without a cancelled event", async () => {
  const harness = await createTeamSuccessHarness();
  const execution = harness.execute(() => undefined);
  try {
    await harness.successEntered;
    const events: DesktopTeamRunEvent[] = [];
    let cancelSettled = false;
    const cancel = harness.manager
      .cancelTeamRun(
        { workspaceId: WORKSPACE_ID, teamRunId: TEAM_RUN_ID },
        (event) => events.push(event),
      )
      .then((result) => {
        cancelSettled = true;
        return result;
      });
    await Promise.resolve();
    assert.equal(cancelSettled, false);
    assert.equal(harness.nativeCancelCalls(), 0);
    harness.releaseSuccess();
    const result = await cancel;
    assert.equal(result.ok, true);
    if (result.ok) {
      assert.equal(result.value.accepted, false);
      assert.equal(result.value.cancelled, false);
      assert.equal(result.value.teamRun.state, "succeeded");
    }
    assert.equal(events.some((event) => event.type === "cancelled"), false);
    const executed = await execution;
    assert.equal(executed.ok, true);
    if (executed.ok) assert.equal(executed.value.proof.state, "succeeded");
    assert.equal(harness.nativeCancelCalls(), 1);
  } finally {
    harness.releaseSuccess();
    await execution;
    await harness.close();
  }
});

test("team cancel emits only for a durable accepted cancelling or cancelled response", async () => {
  for (const variant of [
    { accepted: false, cancelled: false, state: "succeeded" as const, emits: false },
    { accepted: false, cancelled: true, state: "cancelled" as const, emits: false },
    { accepted: true, cancelled: false, state: "running" as const, emits: false },
    { accepted: true, cancelled: true, state: "cancelling" as const, emits: true },
    { accepted: true, cancelled: true, state: "cancelled" as const, emits: true },
  ]) {
    const run = testTeamRun(variant.state);
    const client: RuntimeManagerNativeClientForTests = {
      async listProviders() {
        return { ok: true, value: testProviderList() };
      },
      async getProviderVault() {
        return { ok: false, error: { code: "unused_vault" } };
      },
      async sendConversation() {
        return { ok: false, error: { code: "unused_send" } };
      },
      async cancelTeamRun() {
        return {
          ok: true,
          value: {
            accepted: variant.accepted,
            cancelled: variant.cancelled,
            teamRun: run,
          },
        };
      },
    };
    const manager = new RuntimeManager({
      runtimeRoot: path.resolve("C:/omnibase-runtime-manager-cancel-test"),
      expectedManifestSha256: "0".repeat(64),
      uiOrigin: "http://127.0.0.1:3000",
      dataRoot: path.resolve("C:/Users/Alice/AppData/Local/OmniBase"),
      nativeClientForTests: client,
    });
    const events: DesktopTeamRunEvent[] = [];
    const result = await manager.cancelTeamRun(
      { workspaceId: WORKSPACE_ID, teamRunId: TEAM_RUN_ID },
      (event) => events.push(event),
    );
    assert.equal(result.ok, true, variant.state);
    assert.equal(events.length, variant.emits ? 1 : 0, variant.state);
    if (variant.emits) {
      assert.equal(events[0]?.type, "cancelled");
      assert.equal(events[0]?.state, variant.state);
    }
  }
});
