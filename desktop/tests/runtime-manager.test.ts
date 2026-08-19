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
