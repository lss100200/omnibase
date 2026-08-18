import assert from "node:assert/strict";
import test from "node:test";

import path from "node:path";

import {
  buildRuntimeEnvironment,
  matchesRuntimeInstanceToken,
  RuntimeManager,
} from "../src/runtime/runtime-manager.ts";

test("runtime health requires the exact per-launch instance token", () => {
  const expected = "a".repeat(64);
  assert.equal(matchesRuntimeInstanceToken(expected, expected), true);
  assert.equal(matchesRuntimeInstanceToken("b".repeat(64), expected), false);
  assert.equal(matchesRuntimeInstanceToken(null, expected), false);
  assert.equal(matchesRuntimeInstanceToken("not-hex", expected), false);
  assert.equal(matchesRuntimeInstanceToken(expected, "not-hex"), false);
});

test("runtime environment is an explicit safe closed set", () => {
  const token = "a".repeat(64);
  const dataRoot = path.resolve("C:/Users/Alice/AppData/Local/OmniBase/data");
  const environment = buildRuntimeEnvironment(token, dataRoot, {
    SystemRoot: "C:\\Windows",
    TEMP: "C:\\Temp",
    PATH: "C:\\attacker-controlled-bin",
    OPENAI_API_KEY: "must-not-pass",
  });
  assert.deepEqual(environment, {
    OMNIBASE_DESKTOP_MODE: "1",
    OMNIBASE_BIND_HOST: "127.0.0.1",
    OMNIBASE_DESKTOP_INSTANCE_TOKEN: token,
    OMNIBASE_DESKTOP_DATA_ROOT: dataRoot,
    SystemRoot: "C:\\Windows",
    TEMP: "C:\\Temp",
  });
  assert.equal("PATH" in environment, false);
  assert.equal("OPENAI_API_KEY" in environment, false);
});

test("runtime manager does not expose an absolute bundle path on verification failure", async () => {
  const missingRoot = path.resolve(
    `C:/omnibase-missing-runtime-${process.pid}-${Date.now()}`,
  );
  const manager = new RuntimeManager({
    runtimeRoot: missingRoot,
    expectedManifestSha256: "0".repeat(64),
    uiOrigin: "http://127.0.0.1:3000",
    dataRoot: path.resolve("C:/Users/Alice/AppData/Local/OmniBase/data"),
  });
  const status = await manager.start();
  assert.equal(status.phase, "failed");
  assert.doesNotMatch(status.lastError ?? "", /omnibase-missing-runtime/u);
  assert.match(status.lastError ?? "", /\[PATH\]/u);
});
