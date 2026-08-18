import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import path from "node:path";
import { PassThrough } from "node:stream";
import test from "node:test";

import {
  RuntimeSupervisor,
  redactRuntimeError,
  type RuntimeChild,
  type SpawnRuntime,
} from "../src/runtime/supervisor.ts";

class FakeChild extends EventEmitter implements RuntimeChild {
  readonly stdout = new PassThrough();
  readonly stderr = new PassThrough();
  killed = false;

  kill(): boolean {
    this.killed = true;
    return true;
  }
}

const command = path.resolve("C:/OmniBase/runtime/omnibase-runtime.exe");
const cwd = path.dirname(command);

test("supervisor uses an absolute command, shell=false, and a bounded retry policy", async () => {
  let now = 0;
  let probes = 0;
  let spawns = 0;
  const children: FakeChild[] = [];
  const spawnRuntime: SpawnRuntime = (actualCommand, args, options) => {
    spawns += 1;
    assert.equal(actualCommand, command);
    assert.deepEqual(args, ["serve"]);
    assert.equal(options.cwd, cwd);
    assert.equal(options.shell, false);
    assert.equal(options.windowsHide, true);
    assert.equal(options.stdio, "pipe");
    assert.deepEqual(options.env, { OMNIBASE_DESKTOP_MODE: "1" });
    const child = new FakeChild();
    children.push(child);
    return child;
  };
  const supervisor = new RuntimeSupervisor(
    {
      command,
      args: ["serve"],
      cwd,
      environment: { OMNIBASE_DESKTOP_MODE: "1" },
      readinessProbe: async () => {
        probes += 1;
        if (probes === 1) {
          throw new Error("first attempt unavailable");
        }
        return probes >= 3;
      },
      maxAttempts: 2,
      startupTimeoutMs: 10,
      probeIntervalMs: 1,
      retryDelayMs: 1,
    },
    {
      spawnRuntime,
      now: () => now,
      sleep: async (delay) => {
        now += Math.max(delay, 1);
      },
    },
  );

  const status = await supervisor.start();
  assert.equal(status.phase, "ready");
  assert.equal(status.attempts, 1);
  assert.equal(spawns, 1);
  assert.equal(children[0]?.killed, false);

  children[0]?.emit("exit", 7, null);
  assert.equal(supervisor.getStatus().phase, "failed");
  assert.match(
    supervisor.getStatus().lastError ?? "",
    /runtime_exited_before_ready code=7/u,
  );

  const stopped = supervisor.stop();
  assert.equal(stopped.phase, "stopped");
});

test("supervisor stops after three attempts and exposes only a redacted error", async () => {
  let now = 0;
  let spawns = 0;
  const spawnRuntime: SpawnRuntime = () => {
    spawns += 1;
    const child = new FakeChild();
    queueMicrotask(() => {
      child.stderr.write(
        `api_key=top-secret ${command} authorization=Bearer-private ${"a".repeat(64)}`,
      );
    });
    return child;
  };
  const supervisor = new RuntimeSupervisor(
    {
      command,
      cwd,
      environment: { OMNIBASE_DESKTOP_INSTANCE_TOKEN: "a".repeat(64) },
      readinessProbe: async () => false,
      maxAttempts: 3,
      startupTimeoutMs: 2,
      probeIntervalMs: 1,
      retryDelayMs: 0,
    },
    {
      spawnRuntime,
      now: () => now,
      sleep: async () => {
        now += 1;
        await Promise.resolve();
      },
    },
  );

  const status = await supervisor.start();
  assert.equal(status.phase, "failed");
  assert.equal(status.attempts, 3);
  assert.equal(spawns, 3);
  assert.doesNotMatch(status.lastError ?? "", /top-secret/u);
  assert.doesNotMatch(status.lastError ?? "", /a{64}/u);
  assert.doesNotMatch(status.lastError ?? "", /omnibase-runtime\.exe/u);
  assert.match(status.lastError ?? "", /\[REDACTED\]|runtime_readiness_timeout/u);
});

test("runtime error redaction removes bearer credentials, keys, URLs, and paths", () => {
  const raw = `Bearer abc.def api-key=secret https://user:pass@example.com ${command}`;
  const redacted = redactRuntimeError(raw, [command]);
  assert.doesNotMatch(redacted, /abc\.def|secret|user:pass|omnibase-runtime\.exe/u);
  assert.match(redacted, /\[REDACTED\]|\[PATH\]/u);
});

test("relative commands and unbounded retries are rejected before spawn", () => {
  assert.throws(
    () =>
      new RuntimeSupervisor({
        command: "runtime.exe",
        cwd,
        environment: {},
        readinessProbe: async () => true,
      }),
    /runtime_command_and_cwd_must_be_absolute/u,
  );
  assert.throws(
    () =>
      new RuntimeSupervisor({
        command,
        cwd,
        environment: {},
        readinessProbe: async () => true,
        maxAttempts: 4,
      }),
    /runtime_retry_policy_invalid/u,
  );
});
