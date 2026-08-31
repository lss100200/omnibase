import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { mkdir, mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { after, before, test } from "node:test";

import {
  P34SandboxAdapterError,
  P34SandboxComponentAdapter,
} from "../src/runtime/p34-sandbox-adapter.ts";
import { runP34SandboxHelperRequest } from "../src/runtime/p34-sandbox-helper.ts";
import type { TrustedSandboxWorkload } from "../src/runtime/component-runtime-broker.ts";
import type {
  DesktopWorkspaceComponentExecutionTicket,
  DesktopWorkspaceComponentLifecycleTicket,
} from "../src/shared/ipc-contract.ts";
import { writeNodeLauncherFixture } from "./component-package-fixture.ts";

const SHA = "a".repeat(64);
const RUNTIME_ID = "runtime_0123456789abcdef0123456789abcdef";
const WORKSPACE_ID = "ws_0123456789abcdef0123456789abcdef";
const COMPONENT_ID = "builtin.sandbox-workload";
const EXECUTABLE_PATH =
  process.platform === "win32" ? "node/node.exe" : "node/node";
const HELPER_PATH = "component-host/p34-sandbox-helper.js";
const WORKLOAD_PREFIX =
  "0061736d0100000001060160017f017f03020100070d01097472616e73666f726d00000a0a010800200041";
const WORKLOAD_SUFFIX = "00730b";

function workload(constant: "ca" | "cb" = "ca"): TrustedSandboxWorkload {
  const bytes = Buffer.from(
    `${WORKLOAD_PREFIX}${constant}${WORKLOAD_SUFFIX}`,
    "hex",
  );
  return Object.freeze({
    bytes,
    entrypoint: "transform" as const,
    memoryMaxBytes: 65_536 as const,
    network: "no_imports" as const,
    sha256: createHash("sha256").update(bytes).digest("hex"),
  });
}

const WORKLOAD_1_0 = workload();
const WORKLOAD_1_1 = workload("cb");

function workloadEnvelope(value: TrustedSandboxWorkload) {
  return {
    entrypoint: value.entrypoint,
    memory_max_bytes: value.memoryMaxBytes,
    network: value.network,
    workload_base64: Buffer.from(value.bytes).toString("base64"),
    workload_sha256: value.sha256,
  };
}

let runtimeRoot = "";
let digests = new Map<string, string>();

const FIXTURE = String.raw`
const { createHash } = require("node:crypto");
function send(value) { process.stdout.write(JSON.stringify(value) + "\n"); }
if (process.argv.length !== 3 || process.argv[2] !== "--p7-sandbox-helper") process.exit(9);
let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => { raw += chunk; });
process.stdin.on("end", () => {
  const request = JSON.parse(raw);
  if (request.kind === "probe") {
    send({
      adapter: "p34-sandbox.v1",
      isolation: {
        execution: "package_bound_zero_import_webassembly",
        host_capabilities: "none",
        memory_max_bytes: 65536,
        network: "no_imports",
        workload_sha256: request.workload_sha256
      },
      schema_version: 1,
      status: "ready"
    });
    return;
  }
  if (request.component_version === "1.0.1") { setInterval(() => {}, 1000); return; }
  if (request.component_version === "1.0.2") { process.stdout.write("x".repeat(131072)); return; }
  if (request.component_version === "1.0.3") { process.stdout.write("{malformed}\n"); return; }
  const result = {
    artifact_count: request.input_artifact_ids.length,
    fingerprint_sha256: createHash("sha256").update(JSON.stringify(request)).digest("hex"),
    kind: "artifact_inventory",
    transform_value: request.input_artifact_ids.length ^ 202
  };
  send({
    binding: {
      network_fencing_token: request.network_fencing_token,
      operation_id: request.component_version === "1.0.4" ? "op_forged" : request.operation_id,
      request_sha256: request.request_sha256,
      runtime_instance_id: request.runtime_instance_id,
      workload_fencing_token: request.workload_fencing_token,
      workspace_id: request.workspace_id
    },
    output: {
      adapter: "p34-sandbox.v1",
      component_id: request.component_id,
      input_artifact_ids: request.input_artifact_ids,
      result,
      runtime_instance_id: request.runtime_instance_id,
      schema_version: 1,
      status: "completed",
      usage: { bytes_in: 1, bytes_out: 1, wall_time_ms: 1 },
      workload_id: request.workload_id,
      workload_sha256: request.workload_sha256
    }
  });
});
`;

async function sha256(file: string): Promise<string> {
  return createHash("sha256")
    .update(await import("node:fs/promises").then((fs) => fs.readFile(file)))
    .digest("hex");
}

function executionTicket(
  version = "1.0.0",
): DesktopWorkspaceComponentExecutionTicket {
  return Object.freeze({
    operationId: `op_${version.replaceAll(".", "")}`,
    workspaceId: WORKSPACE_ID,
    componentId: COMPONENT_ID,
    version,
    action: "sandbox.run",
    requestSha256: SHA,
    argumentsSha256: SHA,
    adapterId: "p34-sandbox.v1",
    configuration: Object.freeze({}),
    configurationSha256: SHA,
    slotBindings: Object.freeze([]),
    slotBindingsSha256: SHA,
    dependencyGraph: Object.freeze([]),
    dependencyGraphSha256: SHA,
    manifestSha256: SHA,
    packageSha256: SHA,
    bindingGeneration: 1,
    runtimeInstanceId: RUNTIME_ID,
    workloadIdentityDigest: "b".repeat(64),
    workloadFencingToken: 1,
    networkFencingToken: null,
    expiresAt: "2099-01-01T00:00:00.000Z",
  });
}

function lifecycleTicket(): DesktopWorkspaceComponentLifecycleTicket {
  return Object.freeze({
    operationId: "op_activate",
    effectId: "effect_activate",
    workspaceId: WORKSPACE_ID,
    componentId: COMPONENT_ID,
    version: "1.0.0",
    action: "activate",
    adapterId: "p34-sandbox.v1",
    installationId: "install_sandbox",
    bindingGeneration: 1,
    runtimeInstanceId: RUNTIME_ID,
    workloadIdentityDigest: "b".repeat(64),
    configuration: Object.freeze({}),
    configurationSha256: SHA,
    slotBindings: Object.freeze([]),
    slotBindingsSha256: SHA,
    dependencyGraph: Object.freeze([]),
    dependencyGraphSha256: SHA,
    quiesceTimeoutMs: 1000,
    requestSha256: SHA,
    manifestSha256: SHA,
    packageSha256: SHA,
  });
}

function adapter(timeoutMs = 5_000): P34SandboxComponentAdapter {
  return new P34SandboxComponentAdapter({
    runtimeRoot,
    executableRelativePath: EXECUTABLE_PATH,
    helperRelativePath: HELPER_PATH,
    getVerifiedRuntimeFileSha256: (relativePath) =>
      digests.get(relativePath) ?? null,
    maxOutputBytes: 4 * 1024,
    timeoutMs,
  });
}

async function expectCode(
  promise: Promise<unknown>,
  code: string,
): Promise<void> {
  await assert.rejects(
    promise,
    (error: unknown) =>
      error instanceof P34SandboxAdapterError && error.code === code,
  );
}

before(async () => {
  runtimeRoot = await mkdtemp(path.join(os.tmpdir(), "omnibase-p73-sandbox-"));
  await mkdir(path.join(runtimeRoot, "node"), { recursive: true });
  await mkdir(path.join(runtimeRoot, "component-host"), { recursive: true });
  await writeNodeLauncherFixture(
    path.join(runtimeRoot, ...EXECUTABLE_PATH.split("/")),
  );
  await writeFile(
    path.join(runtimeRoot, ...HELPER_PATH.split("/")),
    FIXTURE,
    "utf8",
  );
  digests = new Map([
    [
      EXECUTABLE_PATH,
      await sha256(path.join(runtimeRoot, ...EXECUTABLE_PATH.split("/"))),
    ],
    [
      HELPER_PATH,
      await sha256(path.join(runtimeRoot, ...HELPER_PATH.split("/"))),
    ],
  ]);
});

after(async () => {
  await rm(runtimeRoot, { force: true, recursive: true });
});

test("source-owned helper has a zero-import bounded positive journey", () => {
  const first = runP34SandboxHelperRequest({
    kind: "run",
    schema_version: 1,
    component_id: COMPONENT_ID,
    component_version: "1.0.0",
    workload_id: "bounded-transform",
    runtime_instance_id: RUNTIME_ID,
    workspace_id: WORKSPACE_ID,
    operation_id: "op_direct_first",
    request_sha256: SHA,
    workload_fencing_token: 1,
    network_fencing_token: null,
    input_artifact_ids: ["artifact.alpha"],
    ...workloadEnvelope(WORKLOAD_1_0),
  });
  const second = runP34SandboxHelperRequest({
    kind: "run",
    schema_version: 1,
    component_id: COMPONENT_ID,
    component_version: "1.1.0",
    workload_id: "bounded-transform",
    runtime_instance_id: RUNTIME_ID,
    workspace_id: WORKSPACE_ID,
    operation_id: "op_direct_second",
    request_sha256: SHA,
    workload_fencing_token: 1,
    network_fencing_token: null,
    input_artifact_ids: ["artifact.alpha"],
    ...workloadEnvelope(WORKLOAD_1_1),
  });
  assert.equal(
    (first as { output: { status: string } }).output.status,
    "completed",
  );
  assert.notEqual(
    (first as { output: { result: { fingerprint_sha256: string } } }).output
      .result.fingerprint_sha256,
    (second as { output: { result: { fingerprint_sha256: string } } }).output
      .result.fingerprint_sha256,
  );
  assert.equal(
    (first as { output: { result: { transform_value: number } } }).output.result
      .transform_value,
    75,
  );
  assert.equal(
    (second as { output: { result: { transform_value: number } } }).output
      .result.transform_value,
    74,
  );
  assert.throws(
    () =>
      runP34SandboxHelperRequest({
        kind: "run",
        schema_version: 1,
        component_id: COMPONENT_ID,
        component_version: "1.0.0",
        workload_id: "bounded-transform",
        runtime_instance_id: RUNTIME_ID,
        workspace_id: WORKSPACE_ID,
        operation_id: "op_direct_host_path",
        request_sha256: SHA,
        workload_fencing_token: 1,
        network_fencing_token: null,
        input_artifact_ids: ["C:\\host\\secret"],
        ...workloadEnvelope(WORKLOAD_1_0),
      }),
    /sandbox_helper_request_invalid/u,
  );
});

test("helper and adapter reject workload identity or contract drift", async () => {
  assert.throws(
    () =>
      runP34SandboxHelperRequest({
        kind: "probe",
        schema_version: 1,
        ...workloadEnvelope({ ...WORKLOAD_1_0, sha256: "0".repeat(64) }),
      }),
    /sandbox_helper_workload_identity_invalid/u,
  );
  const changedBytes = Buffer.from(WORKLOAD_1_0.bytes);
  changedBytes[changedBytes.length - 1] =
    (changedBytes[changedBytes.length - 1] ?? 0) ^ 1;
  await expectCode(
    adapter().execute({
      ticket: executionTicket(),
      workloadId: "bounded-transform",
      workload: Object.freeze({ ...WORKLOAD_1_0, bytes: changedBytes }),
      inputArtifactIds: [],
      signal: new AbortController().signal,
    }),
    "desktop_component_sandbox_workload_invalid",
  );
  const wrongExport = Buffer.from(
    "0061736d0100000001060160017f017f030201000707010372756e00000a0601040020000b",
    "hex",
  );
  await expectCode(
    adapter().execute({
      ticket: executionTicket(),
      workloadId: "bounded-transform",
      workload: Object.freeze({
        ...WORKLOAD_1_0,
        bytes: wrongExport,
        sha256: createHash("sha256").update(wrongExport).digest("hex"),
      }),
      inputArtifactIds: [],
      signal: new AbortController().signal,
    }),
    "desktop_component_sandbox_workload_invalid",
  );
});

test("adapter preflight and subprocess execution return the exact completed DTO", async () => {
  const sandbox = adapter();
  await sandbox.preflight(WORKLOAD_1_0);
  const activated = await sandbox.activate({
    ticket: lifecycleTicket(),
    workload: WORKLOAD_1_0,
    signal: new AbortController().signal,
  });
  assert.equal(activated.health, "healthy");
  const output = await sandbox.execute({
    ticket: executionTicket(),
    workloadId: "bounded-transform",
    workload: WORKLOAD_1_0,
    inputArtifactIds: ["artifact.alpha"],
    signal: new AbortController().signal,
  });
  assert.deepEqual(Object.keys(output as object).sort(), [
    "adapter",
    "component_id",
    "input_artifact_ids",
    "result",
    "runtime_instance_id",
    "schema_version",
    "status",
    "usage",
    "workload_id",
    "workload_sha256",
  ]);
  assert.equal((output as { status: string }).status, "completed");
});

test("adapter kills a timed out subprocess and releases the single slot", async () => {
  const sandbox = adapter(1_000);
  await expectCode(
    sandbox.execute({
      ticket: executionTicket("1.0.1"),
      workloadId: "bounded-transform",
      workload: WORKLOAD_1_0,
      inputArtifactIds: [],
      signal: new AbortController().signal,
    }),
    "desktop_component_sandbox_timeout",
  );
  const output = await sandbox.execute({
    ticket: executionTicket(),
    workloadId: "bounded-transform",
    workload: WORKLOAD_1_0,
    inputArtifactIds: [],
    signal: new AbortController().signal,
  });
  assert.equal((output as { status: string }).status, "completed");
});

test("adapter abort kills the subprocess and concurrency remains one", async () => {
  const sandbox = adapter();
  const controller = new AbortController();
  const first = sandbox.execute({
    ticket: executionTicket("1.0.1"),
    workloadId: "bounded-transform",
    workload: WORKLOAD_1_0,
    inputArtifactIds: [],
    signal: controller.signal,
  });
  const firstAssertion = expectCode(
    first,
    "desktop_component_sandbox_cancelled",
  );
  await expectCode(
    sandbox.execute({
      ticket: executionTicket(),
      workloadId: "bounded-transform",
      workload: WORKLOAD_1_0,
      inputArtifactIds: [],
      signal: new AbortController().signal,
    }),
    "desktop_component_sandbox_concurrency_exceeded",
  );
  controller.abort();
  await firstAssertion;
});

test("adapter reserves the one slot before cold runtime verification completes", async () => {
  const sandbox = adapter();
  const controller = new AbortController();
  const first = sandbox.execute({
    ticket: executionTicket("1.0.1"),
    workloadId: "bounded-transform",
    workload: WORKLOAD_1_0,
    inputArtifactIds: [],
    signal: controller.signal,
  });
  const firstAssertion = expectCode(
    first,
    "desktop_component_sandbox_cancelled",
  );
  await expectCode(
    sandbox.execute({
      ticket: executionTicket(),
      workloadId: "bounded-transform",
      workload: WORKLOAD_1_0,
      inputArtifactIds: [],
      signal: new AbortController().signal,
    }),
    "desktop_component_sandbox_concurrency_exceeded",
  );
  controller.abort();
  await firstAssertion;
});

test("adapter rejects oversized and malformed subprocess output, then cleans up", async () => {
  const sandbox = adapter();
  await expectCode(
    sandbox.execute({
      ticket: executionTicket("1.0.2"),
      workloadId: "bounded-transform",
      workload: WORKLOAD_1_0,
      inputArtifactIds: [],
      signal: new AbortController().signal,
    }),
    "desktop_component_sandbox_output_limit_exceeded",
  );
  await expectCode(
    sandbox.execute({
      ticket: executionTicket("1.0.3"),
      workloadId: "bounded-transform",
      workload: WORKLOAD_1_0,
      inputArtifactIds: [],
      signal: new AbortController().signal,
    }),
    "desktop_component_sandbox_response_invalid",
  );
  const output = await sandbox.execute({
    ticket: executionTicket(),
    workloadId: "bounded-transform",
    workload: WORKLOAD_1_0,
    inputArtifactIds: [],
    signal: new AbortController().signal,
  });
  assert.equal((output as { status: string }).status, "completed");
});

test("adapter rejects a subprocess response with drifted operation binding", async () => {
  const sandbox = adapter();
  await expectCode(
    sandbox.execute({
      ticket: executionTicket("1.0.4"),
      workloadId: "bounded-transform",
      workload: WORKLOAD_1_0,
      inputArtifactIds: ["artifact.alpha"],
      signal: new AbortController().signal,
    }),
    "desktop_component_sandbox_response_invalid",
  );
});

test("adapter fails closed on unattested helper identity", async () => {
  const sandbox = new P34SandboxComponentAdapter({
    runtimeRoot,
    executableRelativePath: EXECUTABLE_PATH,
    helperRelativePath: HELPER_PATH,
    getVerifiedRuntimeFileSha256: (relativePath) =>
      relativePath === EXECUTABLE_PATH
        ? (digests.get(relativePath) ?? null)
        : null,
  });
  await expectCode(
    sandbox.preflight(WORKLOAD_1_0),
    "desktop_component_sandbox_runtime_unavailable",
  );
});
