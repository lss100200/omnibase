import assert from "node:assert/strict";
import test from "node:test";

import path from "node:path";

import {
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

test("runtime manager does not expose an absolute bundle path on verification failure", async () => {
  const missingRoot = path.resolve(
    `C:/omnibase-missing-runtime-${process.pid}-${Date.now()}`,
  );
  const manager = new RuntimeManager({
    runtimeRoot: missingRoot,
    expectedManifestSha256: "0".repeat(64),
    uiOrigin: "http://127.0.0.1:3000",
  });
  const status = await manager.start();
  assert.equal(status.phase, "failed");
  assert.doesNotMatch(status.lastError ?? "", /omnibase-missing-runtime/u);
  assert.match(status.lastError ?? "", /\[PATH\]/u);
});
