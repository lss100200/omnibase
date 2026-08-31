import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import {
  chmod,
  mkdir,
  mkdtemp,
  readFile,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  buildChildEnvironments,
  loadConfig,
  requireDistinctArtifacts,
  terminateChildGroups,
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

test("Linux RuntimeHost escalates timed-out process groups and waits for exit", async () => {
  let settle;
  let gone = false;
  const settlement = new Promise((resolve) => {
    settle = resolve;
  });
  const signals = [];
  const terminated = await terminateChildGroups(
    [{ pid: 101 }],
    [settlement],
    1,
    {
      finalWaitMs: 20,
      signalProcessGroup: (pid, signal) => {
        if (signal === 0) {
          if (gone) throw Object.assign(new Error("gone"), { code: "ESRCH" });
          return;
        }
        signals.push([pid, signal]);
        if (signal === "SIGKILL") {
          gone = true;
          settle();
        }
      },
    },
  );
  assert.equal(terminated, true);
  assert.deepEqual(signals, [
    [101, "SIGTERM"],
    [101, "SIGKILL"],
  ]);
});

test("Linux RuntimeHost continues group cleanup after a signal error", async () => {
  const signals = [];
  const terminated = await terminateChildGroups(
    [{ pid: 101 }, { pid: 102 }],
    [Promise.resolve(), Promise.resolve()],
    10,
    {
      signalProcessGroup: (pid, signal) => {
        if (signal === 0) {
          throw Object.assign(new Error("gone"), { code: "ESRCH" });
        }
        signals.push([pid, signal]);
        if (pid === 101) {
          throw Object.assign(new Error("denied"), { code: "EPERM" });
        }
      },
    },
  );
  assert.equal(terminated, false);
  assert.deepEqual(signals, [
    [101, "SIGTERM"],
    [102, "SIGTERM"],
  ]);
});

test("Linux RuntimeHost retains supervision when SIGKILL cannot settle", async () => {
  let retained = 0;
  const signals = [];
  const terminated = await terminateChildGroups(
    [{ pid: 101 }],
    [new Promise(() => undefined)],
    1,
    {
      finalWaitMs: 1,
      retainProcessGroupCleanup: () => (retained += 1),
      signalProcessGroup: (pid, signal) => {
        if (signal !== 0) signals.push([pid, signal]);
      },
    },
  );
  assert.equal(terminated, false);
  assert.equal(retained, 1);
  assert.deepEqual(signals, [
    [101, "SIGTERM"],
    [101, "SIGKILL"],
  ]);
});

test("Linux RuntimeHost kills a SIGTERM-resistant descendant before returning", async (t) => {
  if (process.platform !== "linux") {
    t.skip("POSIX process-group integration requires Linux");
    return;
  }
  const root = await mkdtemp(path.join(os.tmpdir(), "omnibase-linux-group-"));
  const helper = path.join(root, "leader.mjs");
  const pidFile = path.join(root, "descendant.pid");
  await writeFile(
    helper,
    `import { spawn } from "node:child_process";\n` +
      `import { writeFileSync } from "node:fs";\n` +
      `const child = spawn(process.execPath, ["-e", "process.on('SIGTERM', () => {}); console.log('ready'); setInterval(() => {}, 1000)"], { stdio: ["ignore", "pipe", "ignore"] });\n` +
      `process.on("SIGTERM", () => process.exit(0));\n` +
      `child.stdout.once("data", () => writeFileSync(process.argv[2], String(child.pid)));\n` +
      `setInterval(() => {}, 1000);\n`,
    "utf8",
  );
  const leader = spawn(process.execPath, [helper, pidFile], {
    detached: true,
    stdio: "ignore",
  });
  const settlement = new Promise((resolve) => {
    leader.once("error", resolve);
    leader.once("exit", resolve);
  });
  let descendantPid;
  try {
    const deadline = Date.now() + 5_000;
    while (Date.now() < deadline) {
      try {
        descendantPid = Number.parseInt(await readFile(pidFile, "utf8"), 10);
        break;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 25));
      }
    }
    assert.ok(Number.isInteger(descendantPid));
    assert.equal(
      await terminateChildGroups([leader], [settlement], 100, {
        finalWaitMs: 2_000,
      }),
      true,
    );
    assert.throws(
      () => process.kill(descendantPid, 0),
      (error) => error?.code === "ESRCH",
    );
  } finally {
    if (leader.pid !== undefined) {
      try {
        process.kill(-leader.pid, "SIGKILL");
      } catch {
        // The expected path already removed the complete process group.
      }
    }
    await rm(root, { force: true, recursive: true });
  }
});
