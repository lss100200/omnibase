import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { chmod, mkdir, mkdtemp, symlink, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  buildChildEnvironments,
  loadConfig,
  requireDistinctArtifacts,
  verifiedFile,
} from "../../packaging/linux/omnibase-runtime-host.mjs";

function digest(value) {
  return createHash("sha256").update(value).digest("hex");
}

async function fixture(label) {
  const root = await mkdtemp(
    path.join(os.tmpdir(), `omnibase-linux-host-${label}-`),
  );
  const frontend = path.join(root, "frontend", "server.js");
  await mkdir(path.dirname(frontend), { recursive: true });
  await writeFile(frontend, "frontend");
  return { root, frontend };
}

test("Linux RuntimeHost treats frontend JavaScript as a readable artifact", async () => {
  const current = await fixture("frontend");
  const target = await verifiedFile(current.root, {
    path: "frontend/server.js",
    sha256: digest("frontend"),
  });
  assert.equal(target, current.frontend);
});

test("Linux RuntimeHost requires executable bits only for launched artifacts", async () => {
  const current = await fixture("executable");
  const descriptor = { path: "frontend/server.js", sha256: digest("frontend") };
  if (process.platform !== "linux") {
    await verifiedFile(current.root, descriptor, true);
    return;
  }
  await assert.rejects(
    () => verifiedFile(current.root, descriptor, true),
    /runtime_host_artifact_identity_invalid/u,
  );
  await chmod(current.frontend, 0o755);
  assert.equal(
    await verifiedFile(current.root, descriptor, true),
    current.frontend,
  );
});

test("Linux RuntimeHost rejects a symlink artifact before digest verification", async (t) => {
  if (process.platform === "win32") {
    t.skip("creating symlinks requires elevated Windows privileges");
    return;
  }
  const current = await fixture("symlink");
  const link = path.join(current.root, "frontend", "linked.js");
  await symlink(current.frontend, link);
  await assert.rejects(
    () =>
      verifiedFile(current.root, {
        path: "frontend/linked.js",
        sha256: digest("frontend"),
      }),
    /runtime_host_artifact_identity_invalid/u,
  );
});

test("Linux RuntimeHost rejects non-canonical relative artifact paths", async () => {
  const current = await fixture("path");
  await assert.rejects(
    () =>
      verifiedFile(current.root, {
        path: "frontend/../frontend/server.js",
        sha256: digest("frontend"),
      }),
    /runtime_host_path_invalid/u,
  );
});

test("Linux RuntimeHost configuration is a closed contract", async () => {
  const current = await fixture("config");
  const config = {
    schema_version: 1,
    backend: { path: "backend", sha256: digest("backend") },
    frontend: { path: "frontend/server.js", sha256: digest("frontend") },
    node: { path: "node", sha256: digest("node") },
    application_version: "1.0.0",
    backend_port: 41001,
    frontend_port: 41002,
    startup_timeout_seconds: 30,
    shutdown_timeout_seconds: 10,
  };
  await writeFile(
    path.join(current.root, "runtime-host.json"),
    JSON.stringify(config),
  );
  assert.equal((await loadConfig(current.root)).application_version, "1.0.0");
  await writeFile(
    path.join(current.root, "runtime-host.json"),
    JSON.stringify({ ...config, unexpected: true }),
  );
  await assert.rejects(
    () => loadConfig(current.root),
    /runtime_host_config_invalid/u,
  );
});

test("Linux RuntimeHost rejects duplicate artifact paths", async () => {
  assert.throws(
    () => requireDistinctArtifacts(["/runtime/a", "/runtime/a"]),
    /runtime_host_artifact_paths_must_differ/u,
  );
  assert.deepEqual(requireDistinctArtifacts(["/runtime/a", "/runtime/b"]), [
    "/runtime/a",
    "/runtime/b",
  ]);
});

test("Linux RuntimeHost keeps proof and control identities backend-only", () => {
  const token = "a".repeat(64);
  const environments = buildChildEnvironments({
    instanceToken: token,
    proofKey: "b".repeat(64),
    controlToken: "c".repeat(64),
    dataRoot: path.resolve("runtime-data"),
    backendPort: 8765,
    frontendPort: 3000,
  });
  assert.equal(
    environments.backend.OMNIBASE_DESKTOP_NATIVE_PROOF_KEY,
    "b".repeat(64),
  );
  assert.equal(
    environments.backend.OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN,
    "c".repeat(64),
  );
  assert.equal(environments.frontend.OMNIBASE_DESKTOP_INSTANCE_TOKEN, token);
  assert.equal(
    "OMNIBASE_DESKTOP_NATIVE_PROOF_KEY" in environments.frontend,
    false,
  );
  assert.equal(
    "OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN" in environments.frontend,
    false,
  );
});
