import assert from "node:assert/strict";
import { mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test, { type TestContext } from "node:test";

import {
  ComponentRuntimeBroker,
  type ComponentNativeExecutionBoundary,
} from "../src/runtime/component-runtime-broker.ts";
import { P34SandboxComponentAdapter } from "../src/runtime/p34-sandbox-adapter.ts";
import { WorkspaceFiles } from "../src/runtime/workspace-files.ts";
import type {
  DesktopWorkspaceComponentActionInput,
  DesktopWorkspaceComponentActionResult,
  DesktopWorkspaceComponentBeginInput,
  DesktopWorkspaceComponentExecutionTicket,
  DesktopWorkspaceComponentInvokeInput,
  DesktopWorkspaceComponentLifecycleTicket,
  DesktopWorkspaceComponentNativeActionInput,
  DesktopWorkspaceComponentSettleInput,
  DesktopWorkspaceComponentSettleResult,
} from "../src/shared/workspace-components.ts";
import {
  canonicalJson,
  createSourceComponentRuntimeFixture,
  digestRaw,
  type SourceComponentRuntimeFixture,
  type SourcePackageFixtureEntry,
  writeNodeLauncherFixture,
} from "./component-package-fixture.ts";

const WORKSPACE = `workspace_${"a".repeat(32)}`;
const RUNTIME = `runtime_${"b".repeat(32)}`;
const NOW = "2026-08-30T00:00:00.000Z";
const NODE_PATH = process.platform === "win32" ? "node/node.exe" : "node/node";
const HELPER_PATH = "component-host/p34-sandbox-helper.js";

const SANDBOX_HELPER = String.raw`
const { createHash } = require("node:crypto");
function send(value) { process.stdout.write(JSON.stringify(value) + "\n"); }
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
  send({
    binding: {
      network_fencing_token: request.network_fencing_token,
      operation_id: request.operation_id,
      request_sha256: request.request_sha256,
      runtime_instance_id: request.runtime_instance_id,
      workload_fencing_token: request.workload_fencing_token,
      workspace_id: request.workspace_id
    },
    output: {
      adapter: "p34-sandbox.v1",
      component_id: request.component_id,
      input_artifact_ids: request.input_artifact_ids,
      result: {
        artifact_count: request.input_artifact_ids.length,
        fingerprint_sha256: createHash("sha256")
          .update(request.component_version + JSON.stringify(request.input_artifact_ids))
          .digest("hex"),
        kind: "artifact_inventory",
        transform_value: request.input_artifact_ids.length ^ 202
      },
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

function digestJson(value: unknown): string {
  return digestRaw(canonicalJson(value));
}

function requestSha256(input: DesktopWorkspaceComponentInvokeInput): string {
  return digestJson({
    action: input.operation,
    arguments_sha256: digestJson(input.arguments),
    binding_generation: input.bindingGeneration,
    bytes_in: Buffer.byteLength(canonicalJson(input.arguments), "utf8"),
    bytes_out_reserved: input.bytesOutReserved,
    component_id: input.componentId,
    cost_units: input.costUnits,
    expected_revision: input.expectedRevision,
    logical_resource_id: input.logicalResourceId ?? null,
    logical_service_id: input.logicalServiceId ?? null,
    manifest_sha256: input.manifestSha256,
    package_sha256: input.packageSha256,
    resource_version: input.resourceVersion ?? null,
    tokens_reserved: input.tokensReserved,
    wall_time_ms: input.wallTimeMs,
    workspace_id: input.workspaceId,
  });
}

function inputFor(
  entry: SourcePackageFixtureEntry,
  suffix: string,
): DesktopWorkspaceComponentInvokeInput {
  const base = {
    workspaceId: WORKSPACE,
    componentId: entry.componentId,
    expectedRevision: 2,
    bindingGeneration: 1,
    manifestSha256: entry.manifestSha256,
    packageSha256: entry.packageSha256,
    idempotencyKey: `package-backed.${suffix}`,
    bytesOutReserved: 4 * 1024 * 1024,
    tokensReserved: 0,
    wallTimeMs: 30_000,
    costUnits: 1,
  } as const;
  switch (entry.family) {
    case "declarative_ui":
      return {
        ...base,
        operation: "ui.render",
        arguments: {
          slotId: "editor.component",
          viewId: entry.componentId,
        },
      };
    case "instruction_skill":
      return {
        ...base,
        operation: "skill.resolve",
        arguments: {
          skillId: entry.componentId,
          task: "Summarize the reviewed evidence.",
        },
      };
    case "mcp_connector":
      return {
        ...base,
        operation: "mcp.call",
        arguments: {
          toolName: "omnibase_files_read",
          path: "src/main.ts",
        },
      };
    case "sandbox_workload":
      return {
        ...base,
        operation: "sandbox.run",
        arguments: {
          workloadId: "bounded-transform",
          inputArtifactIds: ["artifact.input.one"],
        },
      };
    case "trusted_local_adapter":
      return {
        ...base,
        operation: "local_adapter.open",
        arguments: {
          adapterId: "knowledge.ebook",
          destination: "workspace",
        },
      };
  }
}

function ticketFor(
  input: DesktopWorkspaceComponentInvokeInput,
  entry: SourcePackageFixtureEntry,
  operationId: string,
): DesktopWorkspaceComponentExecutionTicket {
  const slotBindings =
    input.operation === "ui.render"
      ? [
          {
            slotId: "editor.component",
            bindingKey: "source.canvas",
            orderIndex: 0,
            configuration: {},
          },
        ]
      : [];
  return {
    operationId,
    workspaceId: input.workspaceId,
    componentId: input.componentId,
    version: entry.version,
    action: input.operation,
    requestSha256: requestSha256(input),
    argumentsSha256: digestJson(input.arguments),
    adapterId: entry.adapterId,
    configuration: {},
    configurationSha256: digestJson({}),
    slotBindings,
    slotBindingsSha256: digestJson(
      slotBindings.map((binding) => ({
        binding_key: binding.bindingKey,
        configuration: binding.configuration,
        order_index: binding.orderIndex,
        slot_id: binding.slotId,
      })),
    ),
    dependencyGraph: [],
    dependencyGraphSha256: digestJson([]),
    manifestSha256: entry.manifestSha256,
    packageSha256: entry.packageSha256,
    bindingGeneration: input.bindingGeneration,
    runtimeInstanceId: RUNTIME,
    workloadIdentityDigest: "c".repeat(64),
    workloadFencingToken: 1,
    networkFencingToken: null,
    expiresAt: "2099-01-01T00:00:00.000Z",
  };
}

function settlement(
  input: DesktopWorkspaceComponentSettleInput,
  invocation: DesktopWorkspaceComponentInvokeInput,
): DesktopWorkspaceComponentSettleResult {
  return {
    operation: {
      operationId: input.operationId,
      workspaceId: input.workspaceId,
      componentId: invocation.componentId,
      installationId: `installation_${"d".repeat(32)}`,
      action: invocation.operation,
      requestSha256: input.requestSha256,
      bindingGeneration: invocation.bindingGeneration,
      state: input.state,
      resultSha256: input.resultSha256 ?? null,
      evidenceSha256: input.evidenceSha256,
      errorCode: input.errorCode ?? null,
      createdAt: NOW,
      updatedAt: NOW,
    },
    effect: {
      effectId: `effect_${"e".repeat(32)}`,
      operationId: input.operationId,
      workspaceId: input.workspaceId,
      componentId: invocation.componentId,
      state: input.state === "cancelled" ? "failed" : input.state,
      evidenceSha256: input.evidenceSha256,
      createdAt: NOW,
      updatedAt: NOW,
    },
    replayed: false,
  };
}

function invocationBoundary(
  invocation: DesktopWorkspaceComponentInvokeInput,
  entry: SourcePackageFixtureEntry,
  operationId: string,
  begins: DesktopWorkspaceComponentBeginInput[],
  settlements: DesktopWorkspaceComponentSettleInput[],
): ComponentNativeExecutionBoundary {
  return {
    applyWorkspaceComponentAction: async () => ({
      ok: false,
      error: { code: "unused" },
    }),
    beginWorkspaceComponentInvocation: async (input) => {
      begins.push(input);
      return {
        ok: true,
        value: {
          ticket: ticketFor(invocation, entry, operationId),
          replayed: false,
        },
      };
    },
    settleWorkspaceComponentInvocation: async (input) => {
      settlements.push(input);
      return { ok: true, value: settlement(input, invocation) };
    },
    emergencyStopWorkspaceComponents: async () => ({
      ok: false,
      error: { code: "unused" },
    }),
  };
}

async function workspaceFiles(t: TestContext): Promise<WorkspaceFiles> {
  const base = await mkdtemp(
    path.join(os.tmpdir(), "omnibase-p73-broker-files-"),
  );
  t.after(() => rm(base, { recursive: true, force: true }));
  const root = path.join(base, "project");
  await mkdir(path.join(root, "src"), { recursive: true });
  await writeFile(
    path.join(root, "src", "main.ts"),
    "export const version = 73;\n",
  );
  const service = new WorkspaceFiles({
    chooseDirectory: async () => root,
    homeDirectory: path.join(base, "home"),
    getWorkspaceAgent: async ({ workspaceId }) => ({
      ok: true,
      value: {
        agent: {
          id: `agent_${"f".repeat(32)}`,
          workspaceId,
          role: "parent",
          displayName: "Parent",
          createdAt: NOW,
          updatedAt: NOW,
        },
      },
    }),
  });
  assert.equal((await service.authorize({ workspaceId: WORKSPACE })).ok, true);
  return service;
}

async function installSandboxRuntime(
  fixture: SourceComponentRuntimeFixture,
): Promise<P34SandboxComponentAdapter> {
  const nodePath = path.join(fixture.root, ...NODE_PATH.split("/"));
  await writeNodeLauncherFixture(nodePath);
  await fixture.addDeclaredFile(
    HELPER_PATH,
    Buffer.from(SANDBOX_HELPER, "utf8"),
  );
  const nodeRaw = await readFile(nodePath);
  await fixture.addDeclaredFile(NODE_PATH, nodeRaw);
  return new P34SandboxComponentAdapter({
    runtimeRoot: fixture.root,
    executableRelativePath: NODE_PATH,
    helperRelativePath: HELPER_PATH,
    getVerifiedRuntimeFileSha256: fixture.verifiedSha256,
    timeoutMs: 5_000,
    maxOutputBytes: 64 * 1024,
  });
}

function outputMarker(
  operation: DesktopWorkspaceComponentInvokeInput["operation"],
  output: unknown,
) {
  const value = output as Record<string, any>;
  switch (operation) {
    case "ui.render":
      return value.view.title;
    case "skill.resolve":
      return value.instructions;
    case "mcp.call":
      return value.content;
    case "sandbox.run":
      return value.result.fingerprint_sha256;
    case "local_adapter.open":
      return value.catalog.source_snapshot_sha256;
  }
}

function lifecycleRequestSha256(
  input: DesktopWorkspaceComponentActionInput,
): string {
  return digestJson({
    action: input.action,
    component_id: input.componentId,
    expected_revision: input.expectedRevision,
    manifest_sha256: input.manifestSha256,
    package_sha256: input.packageSha256,
    proposal_id: input.proposalId,
    request_sha256: input.requestSha256,
    workspace_id: input.workspaceId,
  });
}

function lifecycleTicketFor(
  input: DesktopWorkspaceComponentActionInput,
  entry: SourcePackageFixtureEntry,
  operationId: string,
): DesktopWorkspaceComponentLifecycleTicket {
  const slotBindings = [
    {
      slotId: "editor.component",
      bindingKey: "source.canvas",
      orderIndex: 0,
      configuration: {},
    },
  ];
  return {
    operationId,
    effectId: `effect_${"1".repeat(32)}`,
    workspaceId: input.workspaceId,
    componentId: input.componentId,
    version: entry.version,
    action: input.action,
    adapterId: entry.adapterId,
    installationId: `installation_${"2".repeat(32)}`,
    bindingGeneration: 2,
    runtimeInstanceId: RUNTIME,
    workloadIdentityDigest: "3".repeat(64),
    configuration: {},
    configurationSha256: digestJson({}),
    slotBindings,
    slotBindingsSha256: digestJson(
      slotBindings.map((binding) => ({
        binding_key: binding.bindingKey,
        configuration: binding.configuration,
        order_index: binding.orderIndex,
        slot_id: binding.slotId,
      })),
    ),
    dependencyGraph: [],
    dependencyGraphSha256: digestJson([]),
    quiesceTimeoutMs: 1_000,
    requestSha256: lifecycleRequestSha256(input),
    manifestSha256: entry.manifestSha256,
    packageSha256: entry.packageSha256,
  };
}

function lifecycleResult(
  input: DesktopWorkspaceComponentActionInput,
  entry: SourcePackageFixtureEntry,
  ticket: DesktopWorkspaceComponentLifecycleTicket,
  state: "pending" | "succeeded" | "failed" | "unknown",
): DesktopWorkspaceComponentActionResult {
  return {
    operation: {
      operationId: ticket.operationId,
      workspaceId: input.workspaceId,
      componentId: input.componentId,
      installationId: ticket.installationId,
      action: input.action,
      requestSha256: ticket.requestSha256,
      bindingGeneration: ticket.bindingGeneration ?? 0,
      state,
      resultSha256: state === "succeeded" ? "4".repeat(64) : null,
      evidenceSha256: state === "pending" ? null : "5".repeat(64),
      errorCode: state === "failed" ? "desktop_component_adapter_failed" : null,
      createdAt: NOW,
      updatedAt: NOW,
    },
    installation: {
      installationId: ticket.installationId!,
      workspaceId: input.workspaceId,
      componentId: input.componentId,
      version: entry.version,
      manifestSha256: entry.manifestSha256,
      packageSha256: entry.packageSha256,
      state:
        state === "succeeded" && input.action === "activate"
          ? "active"
          : "bound",
      revision:
        state === "succeeded"
          ? input.expectedRevision + 1
          : input.expectedRevision,
      bindingGeneration: ticket.bindingGeneration!,
      desiredConfiguration: {},
      currentSlotBindings: ticket.slotBindings,
      dependencyGraph: [],
      health:
        state === "succeeded" && input.action === "activate"
          ? "healthy"
          : "unknown",
      lastErrorCode: null,
      updatedAt: NOW,
    },
    lifecycleTicket: ticket,
    replayed: false,
  };
}

function lifecycleBoundary(
  action: DesktopWorkspaceComponentActionInput,
  entry: SourcePackageFixtureEntry,
  operationId: string,
  calls: DesktopWorkspaceComponentNativeActionInput[],
): ComponentNativeExecutionBoundary {
  const ticket = lifecycleTicketFor(action, entry, operationId);
  return {
    applyWorkspaceComponentAction: async (input) => {
      calls.push(input);
      return {
        ok: true,
        value: lifecycleResult(
          action,
          entry,
          ticket,
          input.phase === "prepare" ? "pending" : input.outcome,
        ),
      };
    },
    beginWorkspaceComponentInvocation: async () => ({
      ok: false,
      error: { code: "unused" },
    }),
    settleWorkspaceComponentInvocation: async () => ({
      ok: false,
      error: { code: "unused" },
    }),
    emergencyStopWorkspaceComponents: async () => ({
      ok: false,
      error: { code: "unused" },
    }),
  };
}

test("all five families use package-backed begin, real adapter, and settle paths for 1.0 and 1.1", async (t) => {
  const fixture = await createSourceComponentRuntimeFixture();
  t.after(fixture.dispose);
  const files = await workspaceFiles(t);
  const sandboxAdapter = await installSandboxRuntime(fixture);
  const components = [
    "builtin.workspace-canvas",
    "builtin.instruction-skill",
    "builtin.readonly-mcp",
    "builtin.sandbox-workload",
    "knowledge.ebook",
  ] as const;

  let sequence = 1;
  for (const componentId of components) {
    const markers = new Map<string, unknown>();
    const packages = new Map<string, string>();
    for (const version of ["1.0.0", "1.1.0"] as const) {
      const entry = fixture.entries.get(`${componentId}@${version}`);
      assert.ok(entry);
      const invocation = inputFor(entry, `${sequence}.${version}`);
      const begins: DesktopWorkspaceComponentBeginInput[] = [];
      const settlements: DesktopWorkspaceComponentSettleInput[] = [];
      const operationId = `compop_${sequence.toString(16).padStart(32, "0")}`;
      sequence += 1;
      const broker = new ComponentRuntimeBroker({
        native: invocationBoundary(
          invocation,
          entry,
          operationId,
          begins,
          settlements,
        ),
        workspaceFiles: files,
        runtimeRoot: fixture.root,
        getVerifiedRuntimeFileSha256: fixture.verifiedSha256,
        sandboxAdapter,
      });

      const result = await broker.invoke(invocation);
      broker.dispose();

      assert.equal(result.ok, true);
      if (!result.ok) continue;
      assert.equal(result.value.state, "succeeded");
      assert.equal(begins.length, 1);
      assert.equal(settlements.length, 1);
      assert.equal(settlements[0]?.state, "succeeded");
      assert.equal(begins[0]?.manifestSha256, entry.manifestSha256);
      assert.equal(begins[0]?.packageSha256, entry.packageSha256);
      packages.set(version, entry.packageSha256);
      markers.set(
        version,
        outputMarker(invocation.operation, result.value.output),
      );
    }
    assert.notEqual(packages.get("1.0.0"), packages.get("1.1.0"));
    if (componentId !== "builtin.readonly-mcp") {
      assert.notEqual(markers.get("1.0.0"), markers.get("1.1.0"));
    } else {
      assert.equal(markers.get("1.0.0"), "export const version = 73;\n");
      assert.equal(markers.get("1.1.0"), "export const version = 73;\n");
    }
  }
});

test("knowledge ebook returns the acceptance-sized catalog within the 4 MiB per-call boundary", async (t) => {
  const fixture = await createSourceComponentRuntimeFixture({
    acceptanceSizedKnowledgeCatalog: true,
  });
  t.after(fixture.dispose);
  const files = await workspaceFiles(t);
  const entry = fixture.entries.get("knowledge.ebook@1.0.0");
  assert.ok(entry);
  const invocation = inputFor(entry, "acceptance-sized-catalog");
  const begins: DesktopWorkspaceComponentBeginInput[] = [];
  const settlements: DesktopWorkspaceComponentSettleInput[] = [];
  const broker = new ComponentRuntimeBroker({
    native: invocationBoundary(
      invocation,
      entry,
      `compop_${"e".repeat(32)}`,
      begins,
      settlements,
    ),
    workspaceFiles: files,
    runtimeRoot: fixture.root,
    getVerifiedRuntimeFileSha256: fixture.verifiedSha256,
  });
  t.after(() => broker.dispose());

  const result = await broker.invoke(invocation);
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.equal(result.value.state, "succeeded");
  const outputBytes = Buffer.byteLength(
    canonicalJson(result.value.output),
    "utf8",
  );
  assert.ok(outputBytes > 3_400_000);
  assert.ok(outputBytes <= invocation.bytesOutReserved);
  assert.equal(settlements[0]?.actualBytesOut, outputBytes);
});

test("upgrade selects the 1.1 package payload and rollback restores the 1.0 payload", async (t) => {
  const fixture = await createSourceComponentRuntimeFixture();
  t.after(fixture.dispose);
  const files = await workspaceFiles(t);
  const v1 = fixture.entries.get("builtin.workspace-canvas@1.0.0");
  const v11 = fixture.entries.get("builtin.workspace-canvas@1.1.0");
  assert.ok(v1);
  assert.ok(v11);
  let sequence = 64;

  const apply = async (
    actionName: "upgrade" | "rollback" | "activate",
    entry: SourcePackageFixtureEntry,
  ) => {
    const action: DesktopWorkspaceComponentActionInput = {
      workspaceId: WORKSPACE,
      componentId: entry.componentId,
      action: actionName,
      proposalId: `proposal_${sequence.toString(16).padStart(32, "0")}`,
      requestSha256: sequence.toString(16).padStart(64, "0"),
      expectedRevision: sequence,
      manifestSha256: entry.manifestSha256,
      packageSha256: entry.packageSha256,
      idempotencyKey: `lifecycle.${actionName}.${entry.version}`,
    };
    const calls: DesktopWorkspaceComponentNativeActionInput[] = [];
    const operationId = `compop_${sequence.toString(16).padStart(32, "0")}`;
    sequence += 1;
    const broker = new ComponentRuntimeBroker({
      native: lifecycleBoundary(action, entry, operationId, calls),
      workspaceFiles: files,
      runtimeRoot: fixture.root,
      getVerifiedRuntimeFileSha256: fixture.verifiedSha256,
    });
    const result = await broker.applyAction(action);
    broker.dispose();
    assert.equal(result.ok, true);
    assert.deepEqual(
      calls.map((call) => call.phase),
      ["prepare", "settle"],
    );
    assert.equal(calls[1]?.manifestSha256, entry.manifestSha256);
    assert.equal(calls[1]?.packageSha256, entry.packageSha256);
  };

  const invokeTitle = async (
    entry: SourcePackageFixtureEntry,
  ): Promise<string> => {
    const invocation = inputFor(entry, `lifecycle-proof.${entry.version}`);
    const begins: DesktopWorkspaceComponentBeginInput[] = [];
    const settlements: DesktopWorkspaceComponentSettleInput[] = [];
    const operationId = `compop_${sequence.toString(16).padStart(32, "0")}`;
    sequence += 1;
    const broker = new ComponentRuntimeBroker({
      native: invocationBoundary(
        invocation,
        entry,
        operationId,
        begins,
        settlements,
      ),
      workspaceFiles: files,
      runtimeRoot: fixture.root,
      getVerifiedRuntimeFileSha256: fixture.verifiedSha256,
    });
    const result = await broker.invoke(invocation);
    broker.dispose();
    assert.equal(result.ok, true);
    assert.equal(begins.length, 1);
    assert.equal(settlements.length, 1);
    return String(outputMarker(invocation.operation, result.value.output));
  };

  assert.equal(await invokeTitle(v1), "Canvas 1.0");
  await apply("upgrade", v11);
  await apply("activate", v11);
  assert.equal(await invokeTitle(v11), "Canvas 1.1");
  await apply("rollback", v1);
  await apply("activate", v1);
  assert.equal(await invokeTitle(v1), "Canvas 1.0");
});
