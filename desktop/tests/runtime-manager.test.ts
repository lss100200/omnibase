import assert from "node:assert/strict";
import test from "node:test";

import path from "node:path";

import { createHmac } from "node:crypto";
import { lstat, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";

import {
  buildRuntimeEnvironment,
  matchesRuntimeInstanceProof,
  prepareRuntimeDataRoot,
  RuntimeManager,
} from "../src/runtime/runtime-manager.ts";
import { encryptProviderSecret } from "../src/runtime/secret-vault.ts";
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

function testProviderList(): DesktopProviderList {
  return Object.freeze({
    items: Object.freeze([
      Object.freeze({
        id: PROVIDER_ID,
        displayName: "loopback",
        baseUrl: "http://127.0.0.1:9",
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
