import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import path from "node:path";
import test from "node:test";

import {
  ComponentRuntimeBroker,
  type ComponentNativeExecutionBoundary,
  type TrustedSandboxComponentAdapter,
} from "../src/runtime/component-runtime-broker.ts";
import { WorkspaceFiles } from "../src/runtime/workspace-files.ts";
import type {
  DesktopWorkspaceComponentBeginInput,
  DesktopWorkspaceComponentBeginResult,
  DesktopWorkspaceComponentActionInput,
  DesktopWorkspaceComponentActionResult,
  DesktopWorkspaceComponentExecutionTicket,
  DesktopWorkspaceComponentInvokeInput,
  DesktopWorkspaceComponentJsonValue,
  DesktopWorkspaceComponentSettleInput,
  DesktopWorkspaceComponentSettleResult,
  DesktopWorkspaceComponentNativeActionInput,
  DesktopWorkspaceComponentNativeEmergencyStopInput,
  DesktopWorkspaceComponentNativeEmergencyStopResult,
  DesktopWorkspaceComponentRecoverySettleInput,
  DesktopWorkspaceComponentRecoverySettleResult,
  DesktopWorkspaceComponentSnapshot,
} from "../src/shared/workspace-components.ts";

const WORKSPACE = `workspace_${"a".repeat(32)}`;
const COMPONENT = "builtin.sandbox-workload";
const OPERATION = `compop_${"b".repeat(32)}`;
const RUNTIME = `runtime_${"c".repeat(32)}`;
const MANIFEST = "d".repeat(64);
const PACKAGE = "e".repeat(64);
const EVIDENCE = "f".repeat(64);
const NOW = "2026-08-30T00:00:00.000Z";

function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const record = value as Readonly<Record<string, unknown>>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${canonicalJson(record[key])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}

function digest(value: unknown): string {
  return createHash("sha256")
    .update(canonicalJson(value), "utf8")
    .digest("hex");
}

function sandboxInput(): Extract<
  DesktopWorkspaceComponentInvokeInput,
  { readonly operation: "sandbox.run" }
> {
  return {
    workspaceId: WORKSPACE,
    componentId: COMPONENT,
    operation: "sandbox.run",
    expectedRevision: 2,
    bindingGeneration: 1,
    manifestSha256: MANIFEST,
    packageSha256: PACKAGE,
    idempotencyKey: "sandbox.invoke.1",
    bytesOutReserved: 4096,
    tokensReserved: 0,
    wallTimeMs: 60_000,
    costUnits: 1,
    arguments: {
      workloadId: "bounded-transform",
      inputArtifactIds: ["artifact.input.1"],
    },
  };
}

function requestSha256(input: DesktopWorkspaceComponentInvokeInput): string {
  return digest({
    action: input.operation,
    arguments_sha256: digest(input.arguments),
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

function actionInput(): DesktopWorkspaceComponentActionInput {
  return {
    workspaceId: WORKSPACE,
    componentId: COMPONENT,
    action: "activate",
    proposalId: `proposal_${"3".repeat(32)}`,
    requestSha256: "4".repeat(64),
    expectedRevision: 2,
    manifestSha256: MANIFEST,
    packageSha256: PACKAGE,
    idempotencyKey: "sandbox.activate.1",
  };
}

function lifecycleRequestSha256(
  input: DesktopWorkspaceComponentActionInput,
): string {
  return digest({
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

function actionResult(
  input: DesktopWorkspaceComponentActionInput,
  options: {
    readonly state?: "pending" | "succeeded" | "failed" | "unknown";
    readonly replayed?: boolean;
    readonly manifestSha256?: string;
    readonly quiesceTimeoutMs?: number;
  } = {},
): DesktopWorkspaceComponentActionResult {
  const state = options.state ?? "pending";
  return {
    operation: {
      operationId: OPERATION,
      workspaceId: input.workspaceId,
      componentId: input.componentId,
      installationId: `installation_${"5".repeat(32)}`,
      action: input.action,
      requestSha256: lifecycleRequestSha256(input),
      bindingGeneration: 1,
      state,
      resultSha256: state === "succeeded" ? EVIDENCE : null,
      evidenceSha256: state === "pending" ? null : EVIDENCE,
      errorCode: state === "failed" ? "desktop_component_adapter_failed" : null,
      createdAt: NOW,
      updatedAt: NOW,
    },
    installation: {
      installationId: `installation_${"5".repeat(32)}`,
      workspaceId: input.workspaceId,
      componentId: input.componentId,
      version: "1.0.0",
      manifestSha256: input.manifestSha256,
      packageSha256: input.packageSha256,
      state: state === "succeeded" ? "active" : "bound",
      revision: state === "succeeded" ? 3 : 2,
      bindingGeneration: 1,
      desiredConfiguration: {},
      currentSlotBindings: [],
      dependencyGraph: [],
      health: state === "succeeded" ? "healthy" : "unknown",
      lastErrorCode: null,
      updatedAt: NOW,
    },
    lifecycleTicket: {
      operationId: OPERATION,
      effectId: `effect_${"6".repeat(32)}`,
      workspaceId: input.workspaceId,
      componentId: input.componentId,
      version: "1.0.0",
      action: input.action,
      adapterId: "p34-sandbox.v1",
      installationId: `installation_${"5".repeat(32)}`,
      bindingGeneration: 1,
      runtimeInstanceId: RUNTIME,
      workloadIdentityDigest: "1".repeat(64),
      configuration: {},
      configurationSha256: digest({}),
      slotBindings: [],
      slotBindingsSha256: digest([]),
      dependencyGraph: [],
      dependencyGraphSha256: digest([]),
      quiesceTimeoutMs: options.quiesceTimeoutMs ?? 5_000,
      requestSha256: lifecycleRequestSha256(input),
      manifestSha256: options.manifestSha256 ?? input.manifestSha256,
      packageSha256: input.packageSha256,
    },
    replayed: options.replayed ?? false,
  };
}

function ticket(
  input: DesktopWorkspaceComponentInvokeInput,
  overrides: Partial<DesktopWorkspaceComponentExecutionTicket> = {},
): DesktopWorkspaceComponentExecutionTicket {
  return {
    operationId: OPERATION,
    workspaceId: input.workspaceId,
    componentId: input.componentId,
    version: "1.0.0",
    action: input.operation,
    requestSha256: requestSha256(input),
    argumentsSha256: digest(input.arguments),
    adapterId: "p34-sandbox.v1",
    configuration: {},
    configurationSha256: digest({}),
    slotBindings: [],
    slotBindingsSha256: digest([]),
    dependencyGraph: [],
    dependencyGraphSha256: digest([]),
    manifestSha256: input.manifestSha256,
    packageSha256: input.packageSha256,
    bindingGeneration: input.bindingGeneration,
    runtimeInstanceId: RUNTIME,
    workloadIdentityDigest: "1".repeat(64),
    workloadFencingToken: 1,
    networkFencingToken: null,
    expiresAt: "2099-01-01T00:00:00.000Z",
    ...overrides,
  };
}

function settled(
  input: DesktopWorkspaceComponentSettleInput,
): DesktopWorkspaceComponentSettleResult {
  return {
    operation: {
      operationId: input.operationId,
      workspaceId: input.workspaceId,
      componentId: COMPONENT,
      installationId: `installation_${"5".repeat(32)}`,
      action: "sandbox.run",
      requestSha256: input.requestSha256,
      bindingGeneration: 1,
      state: input.state,
      resultSha256: input.resultSha256 ?? null,
      evidenceSha256: input.evidenceSha256,
      errorCode: input.errorCode ?? null,
      createdAt: NOW,
      updatedAt: NOW,
    },
    effect: {
      effectId: `effect_${"2".repeat(32)}`,
      operationId: input.operationId,
      workspaceId: input.workspaceId,
      componentId: COMPONENT,
      state: input.state === "cancelled" ? "failed" : input.state,
      evidenceSha256: input.evidenceSha256,
      createdAt: NOW,
      updatedAt: NOW,
    },
    replayed: false,
  };
}

function emergencySettled(
  input: Extract<
    DesktopWorkspaceComponentNativeEmergencyStopInput,
    { phase: "settle" }
  >,
): DesktopWorkspaceComponentNativeEmergencyStopResult {
  return {
    workspaceId: WORKSPACE,
    componentId: input.componentId,
    operation: {
      operationId: input.operationId,
      workspaceId: WORKSPACE,
      componentId: input.componentId,
      installationId: `installation_${"5".repeat(32)}`,
      action: "emergency_stop",
      requestSha256: input.requestSha256,
      bindingGeneration: 1,
      state: input.outcome,
      resultSha256: null,
      evidenceSha256: input.evidenceSha256,
      errorCode: input.errorCode,
      createdAt: NOW,
      updatedAt: NOW,
    },
    effect: {
      effectId: input.effectId,
      operationId: input.operationId,
      workspaceId: WORKSPACE,
      componentId: input.componentId,
      state: input.outcome,
      evidenceSha256: input.evidenceSha256,
      createdAt: NOW,
      updatedAt: NOW,
    },
    replayed: false,
  };
}

function recoverySnapshot(
  componentId: string,
  adapterId: "builtin-ui.v1" | "p34-sandbox.v1",
): DesktopWorkspaceComponentSnapshot {
  const installationId = `installation_${"5".repeat(32)}`;
  const operationId = `compop_${"9".repeat(32)}`;
  const effectId = `effect_${"7".repeat(32)}`;
  return {
    workspaceId: WORKSPACE,
    catalog: [
      {
        componentId,
        version: "1.0.0",
        family:
          adapterId === "builtin-ui.v1" ? "declarative_ui" : "sandbox_workload",
        displayName: "Recovery component",
        publisherClass: "source_owned",
        adapterId,
        policyManifestSha256: MANIFEST,
        manifestSha256: MANIFEST,
        packageSha256: PACKAGE,
        operations: [
          adapterId === "builtin-ui.v1" ? "ui.render" : "sandbox.run",
        ],
        permissions: [
          {
            action: adapterId === "builtin-ui.v1" ? "ui.render" : "sandbox.run",
            dataScope: "workspace_logical",
            logicalResourceClasses: ["workspace.component.input"],
            secretReferenceClasses: [],
          },
        ],
        slots: [],
        dependencies: [],
        conflicts: [],
        budgets: {
          maxCalls: 1,
          maxBytesIn: 4096,
          maxBytesOut: 4096,
          maxTokens: 0,
          maxWallTimeMs: 60_000,
          maxCostUnits: 1,
          maxRetries: 0,
          maxConcurrency: 1,
        },
        network: { required: false, serviceClasses: [] },
        recovery: {
          autoReplayUnknown: false,
          retention: "retain_workspace_data",
          safeMode: "disable_component",
        },
        stateSchema: { kind: "canonical_json", version: 1 },
        settingsSchema: {
          kind: "closed_object",
          version: 1,
          additionalProperties: false,
          properties: {},
          required: [],
        },
        available: true,
        unavailableReason: null,
      },
    ],
    installations: [
      {
        installationId,
        workspaceId: WORKSPACE,
        componentId,
        version: "1.0.0",
        manifestSha256: MANIFEST,
        packageSha256: PACKAGE,
        state: "blocked",
        revision: 4,
        bindingGeneration: 1,
        desiredConfiguration: {},
        currentSlotBindings: [],
        dependencyGraph: [],
        health: "unavailable",
        lastErrorCode: "desktop_component_restart_recovery_required",
        updatedAt: NOW,
      },
    ],
    proposals: [],
    operations: [],
    effects: [],
    grants: [],
    revocations: [],
    recoveries: [
      {
        recoveryId: `recovery_${"4".repeat(32)}`,
        workspaceId: WORKSPACE,
        componentId,
        installationId,
        bindingGeneration: 1,
        previousRuntimeInstanceId: `runtime_${"3".repeat(32)}`,
        operationId,
        effectId,
        adapterId,
        runtimeInstanceId: RUNTIME,
        workloadIdentityDigest: "1".repeat(64),
        requestSha256: "8".repeat(64),
        manifestSha256: MANIFEST,
        packageSha256: PACKAGE,
        state: "pending",
        reasonCode: "desktop_component_restart_recovery_required",
        createdAt: NOW,
      },
    ],
    reconciliations: [],
    audit: [],
  };
}

function recoverySettled(
  input: DesktopWorkspaceComponentRecoverySettleInput,
  componentId: string,
): DesktopWorkspaceComponentRecoverySettleResult {
  return {
    recoveryId: input.recoveryId,
    operation: {
      operationId: input.operationId,
      workspaceId: input.workspaceId,
      componentId,
      installationId: `installation_${"5".repeat(32)}`,
      action: "recovery",
      requestSha256: "8".repeat(64),
      bindingGeneration: 1,
      state: input.outcome,
      resultSha256: input.outcome === "succeeded" ? input.evidenceSha256 : null,
      evidenceSha256: input.evidenceSha256,
      errorCode: input.errorCode,
      createdAt: NOW,
      updatedAt: NOW,
    },
    effect: {
      effectId: `effect_${"7".repeat(32)}`,
      operationId: input.operationId,
      workspaceId: input.workspaceId,
      componentId,
      state: input.outcome,
      evidenceSha256: input.evidenceSha256,
      createdAt: NOW,
      updatedAt: NOW,
    },
    replayed: false,
  };
}

function deferred<T>(): {
  readonly promise: Promise<T>;
  readonly resolve: (value: T) => void;
} {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function workspaceFiles(): WorkspaceFiles {
  return new WorkspaceFiles({
    chooseDirectory: async () => null,
    getWorkspaceAgent: async () => ({
      ok: false,
      error: { code: "desktop_workspace_unavailable" },
    }),
  });
}

function boundary(options: {
  readonly begin: (input: DesktopWorkspaceComponentBeginInput) => Promise<{
    readonly ok: true;
    readonly value: DesktopWorkspaceComponentBeginResult;
  }>;
  readonly emergency?: (
    input: DesktopWorkspaceComponentNativeEmergencyStopInput,
  ) => Promise<
    | {
        readonly ok: true;
        readonly value: DesktopWorkspaceComponentNativeEmergencyStopResult;
      }
    | { readonly ok: false; readonly error: { readonly code: string } }
  >;
  readonly settlements?: DesktopWorkspaceComponentSettleInput[];
  readonly settle?: (input: DesktopWorkspaceComponentSettleInput) => Promise<
    | {
        readonly ok: true;
        readonly value: DesktopWorkspaceComponentSettleResult;
      }
    | { readonly ok: false; readonly error: { readonly code: string } }
  >;
  readonly action?: (
    input: DesktopWorkspaceComponentNativeActionInput,
  ) => Promise<
    | {
        readonly ok: true;
        readonly value: DesktopWorkspaceComponentActionResult;
      }
    | { readonly ok: false; readonly error: { readonly code: string } }
  >;
}): ComponentNativeExecutionBoundary {
  return {
    applyWorkspaceComponentAction:
      options.action ??
      (async () => ({
        ok: false,
        error: { code: "unused" },
      })),
    beginWorkspaceComponentInvocation: options.begin,
    settleWorkspaceComponentInvocation:
      options.settle ??
      (async (input) => {
        options.settlements?.push(input);
        return { ok: true, value: settled(input) };
      }),
    emergencyStopWorkspaceComponents:
      options.emergency ??
      (async (input) =>
        input.phase === "prepare"
          ? {
              ok: true,
              value: {
                workspaceId: WORKSPACE,
                tickets: [
                  {
                    componentId: COMPONENT,
                    operationId: OPERATION,
                    effectId: `effect_${"7".repeat(32)}`,
                    requestSha256: "8".repeat(64),
                  },
                ],
                fencedComponentIds: [COMPONENT],
                replayed: false,
              },
            }
          : {
              ok: true,
              value: emergencySettled(input),
            }),
  };
}

test("broker rejects an operation/component mismatch before durable begin", async () => {
  let beginCalls = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        beginCalls += 1;
        throw new Error("must not begin");
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
  });
  const input = { ...sandboxInput(), componentId: "builtin.readonly-mcp" };
  const result = await broker.invoke(input);
  assert.deepEqual(result, {
    ok: false,
    error: { code: "desktop_component_adapter_identity_mismatch" },
  });
  assert.equal(beginCalls, 0);
  broker.dispose();
});

test("broker binds the durable ticket to the exact invocation arguments", async () => {
  const input = sandboxInput();
  let adapterCalls = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => ({
        ok: true,
        value: {
          ticket: ticket(input, { argumentsSha256: "9".repeat(64) }),
          replayed: false,
        },
      }),
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    sandboxAdapter: {
      execute: async () => {
        adapterCalls += 1;
        return {};
      },
    },
  });
  const result = await broker.invoke(input);
  assert.deepEqual(result, {
    ok: false,
    error: { code: "desktop_component_ticket_identity_mismatch" },
  });
  assert.equal(adapterCalls, 0);
  broker.dispose();
});

test("a replayed begin never dispatches the adapter again", async () => {
  const input = sandboxInput();
  let adapterCalls = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => ({
        ok: true,
        value: { ticket: ticket(input), replayed: true },
      }),
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    sandboxAdapter: {
      execute: async () => {
        adapterCalls += 1;
        return {};
      },
    },
  });
  const result = await broker.invoke(input);
  assert.deepEqual(result, {
    ok: false,
    error: { code: "desktop_component_invocation_reconciliation_required" },
  });
  assert.equal(adapterCalls, 0);
  broker.dispose();
});

test("an unknown external effect preserves the entire durable reservation", async () => {
  const input = sandboxInput();
  const settlements: DesktopWorkspaceComponentSettleInput[] = [];
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => ({
        ok: true,
        value: { ticket: ticket(input), replayed: false },
      }),
      settlements,
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    sandboxAdapter: {
      execute: async () => {
        throw new Error("dispatch outcome is ambiguous");
      },
    },
  });

  const result = await broker.invoke(input);

  assert.equal(result.ok, true);
  assert.equal(settlements.length, 1);
  assert.deepEqual(
    {
      state: settlements[0]?.state,
      actualBytesOut: settlements[0]?.actualBytesOut,
      actualTokens: settlements[0]?.actualTokens,
      actualWallTimeMs: settlements[0]?.actualWallTimeMs,
    },
    {
      state: "unknown",
      actualBytesOut: input.bytesOutReserved,
      actualTokens: input.tokensReserved,
      actualWallTimeMs: input.wallTimeMs,
    },
  );
  broker.dispose();
});

test("a replayed lifecycle prepare never dispatches its adapter again", async () => {
  const input = actionInput();
  let activations = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async () => ({
        ok: true,
        value: actionResult(input, { replayed: true }),
      }),
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    sandboxAdapter: {
      activate: async () => {
        activations += 1;
        return { health: "healthy", evidence: {} };
      },
      stop: async () => ({ evidence: { stopped: true } }),
      execute: async () => ({}),
    },
  });
  const result = await broker.applyAction(input);
  assert.equal(result.ok, true);
  if (result.ok) assert.equal(result.value.replayed, true);
  assert.equal(activations, 0);
  broker.dispose();
});

test("lifecycle dispatch rejects a native ticket with drifted package identity", async () => {
  const input = actionInput();
  let activations = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async () => ({
        ok: true,
        value: actionResult(input, { manifestSha256: "7".repeat(64) }),
      }),
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    sandboxAdapter: {
      activate: async () => {
        activations += 1;
        return { health: "healthy", evidence: {} };
      },
      stop: async () => ({ evidence: { stopped: true } }),
      execute: async () => ({}),
    },
  });
  const result = await broker.applyAction(input);
  assert.deepEqual(result, {
    ok: false,
    error: { code: "desktop_component_lifecycle_ticket_identity_mismatch" },
  });
  assert.equal(activations, 0);
  broker.dispose();
});

test("activation settles the backend-reserved runtime identity and health proof", async () => {
  const input = actionInput();
  const calls: DesktopWorkspaceComponentNativeActionInput[] = [];
  let adapterTicket: unknown;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async (call) => {
        calls.push(call);
        return {
          ok: true,
          value: actionResult(input, {
            state: call.phase === "prepare" ? "pending" : "succeeded",
          }),
        };
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      activate: async ({ ticket: lifecycleTicket }) => {
        adapterTicket = lifecycleTicket;
        return {
          health: "healthy",
          evidence: { adapter: "p34-sandbox.v1", ready: true },
        };
      },
      stop: async () => ({ evidence: { stopped: true } }),
      execute: async () => ({}),
    },
  });
  const result = await broker.applyAction(input);
  assert.equal(result.ok, true);
  assert.equal(calls.length, 2);
  assert.deepEqual(adapterTicket, actionResult(input).lifecycleTicket);
  const settledCall = calls[1];
  assert.equal(settledCall?.phase, "settle");
  if (settledCall?.phase === "settle") {
    assert.equal(settledCall.outcome, "succeeded");
    assert.equal(settledCall.healthState, "healthy");
    assert.equal(settledCall.runtimeInstanceId, RUNTIME);
    assert.equal(settledCall.workloadIdentityDigest, "1".repeat(64));
    assert.equal(settledCall.errorCode, null);
  }
  broker.dispose();
});

test("a successful uninstall accepts the backend's removed installation projection", async () => {
  const input: DesktopWorkspaceComponentActionInput = {
    ...actionInput(),
    action: "uninstall",
    idempotencyKey: "sandbox.uninstall.1",
  };
  let stops = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async (call) => {
        const result = actionResult(input, {
          state: call.phase === "prepare" ? "pending" : "succeeded",
        });
        return {
          ok: true,
          value:
            call.phase === "settle"
              ? { ...result, installation: null }
              : result,
        };
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      activate: async () => ({
        health: "healthy",
        evidence: { ready: true },
      }),
      stop: async () => {
        stops += 1;
        return { evidence: { stopped: true } };
      },
      execute: async () => ({}),
    },
  });

  const result = await broker.applyAction(input);

  assert.equal(result.ok, true);
  if (result.ok) {
    assert.equal(result.value.operation.state, "succeeded");
    assert.equal(result.value.installation, null);
  }
  assert.equal(stops, 1);
  broker.dispose();
});

test("a failed activation settlement stops the uncommitted sandbox runtime", async () => {
  const input = actionInput();
  let stops = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async (call) =>
        call.phase === "prepare"
          ? { ok: true, value: actionResult(input) }
          : {
              ok: false,
              error: { code: "desktop_component_lifecycle_settle_failed" },
            },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      activate: async () => ({
        health: "healthy",
        evidence: { ready: true },
      }),
      stop: async () => {
        stops += 1;
        return { evidence: { stopped: true } };
      },
      execute: async () => ({}),
    },
  });
  assert.deepEqual(await broker.applyAction(input), {
    ok: false,
    error: { code: "desktop_component_lifecycle_settle_failed" },
  });
  assert.equal(stops, 1);
  broker.dispose();
});

test("a drifted activation settlement stops the uncommitted sandbox runtime", async () => {
  const input = actionInput();
  let stops = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async (call) => ({
        ok: true,
        value: actionResult(input, {
          state: call.phase === "prepare" ? "pending" : "succeeded",
          manifestSha256:
            call.phase === "prepare" ? input.manifestSha256 : "7".repeat(64),
        }),
      }),
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      activate: async () => ({
        health: "healthy",
        evidence: { ready: true },
      }),
      stop: async () => {
        stops += 1;
        return { evidence: { stopped: true } };
      },
      execute: async () => ({}),
    },
  });
  assert.deepEqual(await broker.applyAction(input), {
    ok: false,
    error: {
      code: "desktop_component_lifecycle_settle_ticket_identity_mismatch",
    },
  });
  assert.equal(stops, 1);
  broker.dispose();
});

test("sandbox activation requires a kill path before the external boundary", async () => {
  const input = actionInput();
  const calls: DesktopWorkspaceComponentNativeActionInput[] = [];
  let activations = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async (call) => {
        calls.push(call);
        return {
          ok: true,
          value: actionResult(input, {
            state: call.phase === "prepare" ? "pending" : "failed",
          }),
        };
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      activate: async () => {
        activations += 1;
        return { health: "healthy", evidence: { ready: true } };
      },
      execute: async () => ({}),
    },
  });
  assert.equal((await broker.applyAction(input)).ok, true);
  assert.equal(activations, 0);
  assert.equal(calls[1]?.phase, "settle");
  if (calls[1]?.phase === "settle") {
    assert.equal(calls[1].outcome, "failed");
    assert.equal(
      calls[1].errorCode,
      "desktop_component_sandbox_runtime_unavailable",
    );
  }
  broker.dispose();
});

test("a failed activation compensation remains fail closed without an active pointer", async () => {
  const input = actionInput();
  let stops = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async (call) =>
        call.phase === "prepare"
          ? { ok: true, value: actionResult(input) }
          : {
              ok: false,
              error: { code: "desktop_component_lifecycle_settle_failed" },
            },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      activate: async () => ({
        health: "healthy",
        evidence: { ready: true },
      }),
      stop: async () => {
        stops += 1;
        throw new Error("stop failed");
      },
      execute: async () => ({}),
    },
  });
  assert.deepEqual(await broker.applyAction(input), {
    ok: false,
    error: { code: "desktop_component_lifecycle_compensation_failed" },
  });
  assert.equal(stops, 1);
  assert.equal(
    (
      await broker.emergencyStop({
        workspaceId: WORKSPACE,
        idempotencyKey: "emergency.stop.uncommitted",
        reasonCode: "owner_emergency_stop",
      })
    ).ok,
    true,
  );
  assert.equal(stops, 1);
  broker.dispose();
});

test("a malformed sandbox package fails before the external adapter boundary", async () => {
  const input = actionInput();
  const calls: DesktopWorkspaceComponentNativeActionInput[] = [];
  let activations = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async (call) => {
        calls.push(call);
        return {
          ok: true,
          value: actionResult(input, {
            state: call.phase === "prepare" ? "pending" : "failed",
          }),
        };
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "unreviewed-workload",
    }),
    sandboxAdapter: {
      activate: async () => {
        activations += 1;
        return { health: "healthy", evidence: {} };
      },
      stop: async () => ({ evidence: { stopped: true } }),
      execute: async () => ({}),
    },
  });
  const result = await broker.applyAction(input);
  assert.equal(result.ok, true);
  assert.equal(activations, 0);
  const settledCall = calls[1];
  assert.equal(settledCall?.phase, "settle");
  if (settledCall?.phase === "settle") {
    assert.equal(settledCall.outcome, "failed");
    assert.equal(settledCall.healthState, "unhealthy");
    assert.equal(
      settledCall.errorCode,
      "desktop_component_sandbox_package_invalid",
    );
  }
  broker.dispose();
});

test("a sandbox lifecycle crash becomes unknown and is never reported as failed", async () => {
  const input = actionInput();
  const calls: DesktopWorkspaceComponentNativeActionInput[] = [];
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async (call) => {
        calls.push(call);
        return {
          ok: true,
          value: actionResult(input, {
            state: call.phase === "prepare" ? "pending" : "unknown",
          }),
        };
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      activate: async () => {
        throw new Error("provider disconnected");
      },
      stop: async () => ({ evidence: { stopped: true } }),
      execute: async () => ({}),
    },
  });
  const result = await broker.applyAction(input);
  assert.equal(result.ok, true);
  const settledCall = calls[1];
  assert.equal(settledCall?.phase, "settle");
  if (settledCall?.phase === "settle") {
    assert.equal(settledCall.outcome, "unknown");
    assert.equal(settledCall.healthState, "unknown");
    assert.equal(
      settledCall.errorCode,
      "desktop_component_lifecycle_outcome_unknown",
    );
  }
  broker.dispose();
});

test("a lifecycle quiesce timeout cannot release authority as a success", async () => {
  const invocationInput = sandboxInput();
  const disableInput: DesktopWorkspaceComponentActionInput = {
    ...actionInput(),
    action: "disable",
    idempotencyKey: "sandbox.disable.quiesce-timeout",
  };
  const started = deferred<void>();
  const releaseInvocation = deferred<DesktopWorkspaceComponentJsonValue>();
  const lifecycleCalls: DesktopWorkspaceComponentNativeActionInput[] = [];
  let stops = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => ({
        ok: true,
        value: {
          ticket: ticket(invocationInput, {
            operationId: `compop_${"a".repeat(32)}`,
          }),
          replayed: false,
        },
      }),
      action: async (call) => {
        lifecycleCalls.push(call);
        return {
          ok: true,
          value: actionResult(disableInput, {
            state: call.phase === "prepare" ? "pending" : call.outcome,
            quiesceTimeoutMs: 1,
          }),
        };
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      execute: async () => {
        started.resolve(undefined);
        return await releaseInvocation.promise;
      },
      stop: async () => {
        stops += 1;
        return { evidence: { stopped: true } };
      },
    },
  });
  const invocation = broker.invoke(invocationInput);
  await started.promise;

  const result = await broker.applyAction(disableInput);

  assert.equal(result.ok, true);
  assert.equal(stops, 1);
  const settleCall = lifecycleCalls.at(-1);
  assert.equal(settleCall?.phase, "settle");
  if (settleCall?.phase === "settle") {
    assert.equal(settleCall.outcome, "unknown");
    assert.equal(
      settleCall.errorCode,
      "desktop_component_lifecycle_outcome_unknown",
    );
  }
  releaseInvocation.resolve({ completed: true });
  await invocation;
  broker.dispose();
});

for (const action of [
  "disable",
  "upgrade",
  "rollback",
  "revoke",
  "uninstall",
] as const) {
  test(`a late begin cannot dispatch after ${action} fences its admission`, async () => {
    const invocationInput = sandboxInput();
    const destructiveInput: DesktopWorkspaceComponentActionInput = {
      ...actionInput(),
      action,
      idempotencyKey: `sandbox.${action}.late-begin`,
    };
    const beginStarted = deferred<void>();
    const lateBegin = deferred<{
      readonly ok: true;
      readonly value: DesktopWorkspaceComponentBeginResult;
    }>();
    const settlements: DesktopWorkspaceComponentSettleInput[] = [];
    const lifecycleCalls: DesktopWorkspaceComponentNativeActionInput[] = [];
    let adapterCalls = 0;
    const broker = new ComponentRuntimeBroker({
      native: boundary({
        begin: async () => {
          beginStarted.resolve(undefined);
          return await lateBegin.promise;
        },
        settlements,
        action: async (call) => {
          lifecycleCalls.push(call);
          const result = actionResult(destructiveInput, {
            state: call.phase === "prepare" ? "pending" : call.outcome,
          });
          return {
            ok: true,
            value:
              action === "uninstall" &&
              call.phase === "settle" &&
              call.outcome === "succeeded"
                ? { ...result, installation: null }
                : result,
          };
        },
      }),
      workspaceFiles: workspaceFiles(),
      runtimeRoot: path.resolve("."),
      sandboxAdapter: {
        execute: async () => {
          adapterCalls += 1;
          return { dispatched: true };
        },
        stop: async () => ({ evidence: { stopped: true } }),
      },
    });
    const invoking = broker.invoke(invocationInput);
    await beginStarted.promise;

    const lifecycle = await broker.applyAction(destructiveInput);

    assert.equal(lifecycle.ok, true);
    assert.deepEqual(
      lifecycleCalls.map((call) => call.phase),
      ["prepare", "settle"],
    );
    lateBegin.resolve({
      ok: true,
      value: { ticket: ticket(invocationInput), replayed: false },
    });
    const invocation = await invoking;
    assert.equal(invocation.ok, true);
    if (invocation.ok) assert.equal(invocation.value.state, "unknown");
    assert.equal(adapterCalls, 0);
    assert.equal(settlements.length, 1);
    assert.equal(settlements[0]?.state, "unknown");
    assert.equal(
      settlements[0]?.errorCode,
      "desktop_component_invocation_fenced_before_dispatch",
    );
    broker.dispose();
  });
}

test("a lifecycle remains quiesce-visible until its durable settlement completes", async () => {
  const input = actionInput();
  const lifecycleSettleStarted = deferred<void>();
  const allowLifecycleSettle = deferred<void>();
  let emergencySettleCalls = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
      action: async (call) => {
        if (call.phase === "settle") {
          lifecycleSettleStarted.resolve(undefined);
          await allowLifecycleSettle.promise;
        }
        return {
          ok: true,
          value: actionResult(input, {
            state: call.phase === "prepare" ? "pending" : call.outcome,
          }),
        };
      },
      emergency: async (call) => {
        if (call.phase === "prepare") {
          return {
            ok: true,
            value: {
              workspaceId: WORKSPACE,
              tickets: [
                {
                  componentId: COMPONENT,
                  operationId: `compop_${"9".repeat(32)}`,
                  effectId: `effect_${"7".repeat(32)}`,
                  requestSha256: "8".repeat(64),
                },
              ],
              fencedComponentIds: [COMPONENT],
              replayed: false,
            },
          };
        }
        emergencySettleCalls += 1;
        return { ok: true, value: emergencySettled(call) };
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      activate: async () => ({ health: "healthy", evidence: {} }),
      execute: async () => ({}),
    },
  });
  const activation = broker.applyAction(input);
  await lifecycleSettleStarted.promise;

  const stopping = broker.emergencyStop({
    workspaceId: WORKSPACE,
    idempotencyKey: "emergency.stop.pending-lifecycle",
    reasonCode: "owner_emergency_stop",
  });
  await new Promise((resolve) => setTimeout(resolve, 10));
  assert.equal(emergencySettleCalls, 0);

  allowLifecycleSettle.resolve(undefined);
  assert.equal((await activation).ok, true);
  assert.equal((await stopping).ok, true);
  assert.equal(emergencySettleCalls, 1);
  broker.dispose();
});

test("emergency stop durably fences first and only then aborts host execution", async () => {
  const input = sandboxInput();
  const started = deferred<AbortSignal>();
  const emergency = deferred<{
    readonly ok: true;
    readonly value: DesktopWorkspaceComponentNativeEmergencyStopResult;
  }>();
  const emergencyCalls: DesktopWorkspaceComponentNativeEmergencyStopInput[] =
    [];
  const settlements: DesktopWorkspaceComponentSettleInput[] = [];
  const sandboxAdapter: TrustedSandboxComponentAdapter = {
    execute: async ({ signal }) => {
      started.resolve(signal);
      return await new Promise<never>((_resolve, reject) => {
        signal.addEventListener("abort", () => reject(new Error("aborted")), {
          once: true,
        });
      });
    },
  };
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => ({
        ok: true,
        value: { ticket: ticket(input), replayed: false },
      }),
      emergency: async (call) => {
        emergencyCalls.push(call);
        return call.phase === "prepare"
          ? emergency.promise
          : { ok: true, value: emergencySettled(call) };
      },
      settlements,
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter,
  });
  const invoke = broker.invoke(input);
  const signal = await started.promise;
  const stopping = broker.emergencyStop({
    workspaceId: WORKSPACE,
    idempotencyKey: "emergency.stop.1",
    reasonCode: "owner_emergency_stop",
  });
  assert.equal(signal.aborted, false);
  emergency.resolve({
    ok: true,
    value: {
      workspaceId: WORKSPACE,
      tickets: [
        {
          componentId: COMPONENT,
          operationId: `compop_${"9".repeat(32)}`,
          effectId: `effect_${"7".repeat(32)}`,
          requestSha256: "8".repeat(64),
        },
      ],
      fencedComponentIds: [COMPONENT],
      replayed: false,
    },
  });
  assert.equal((await stopping).ok, true);
  assert.deepEqual(
    emergencyCalls.map((call) => call.phase),
    ["prepare", "settle"],
  );
  assert.equal(signal.aborted, true);
  const result = await invoke;
  assert.equal(result.ok, true);
  if (result.ok) assert.equal(result.value.state, "unknown");
  assert.equal(settlements.at(-1)?.state, "unknown");
  broker.dispose();
});

test("a late begin cannot dispatch after emergency stop fences its admission", async () => {
  const input = sandboxInput();
  const beginStarted = deferred<void>();
  const lateBegin = deferred<{
    readonly ok: true;
    readonly value: DesktopWorkspaceComponentBeginResult;
  }>();
  const settlements: DesktopWorkspaceComponentSettleInput[] = [];
  const emergencyCalls: DesktopWorkspaceComponentNativeEmergencyStopInput[] =
    [];
  let adapterCalls = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        beginStarted.resolve(undefined);
        return await lateBegin.promise;
      },
      settlements,
      emergency: async (call) => {
        emergencyCalls.push(call);
        return call.phase === "prepare"
          ? {
              ok: true,
              value: {
                workspaceId: WORKSPACE,
                tickets: [
                  {
                    componentId: COMPONENT,
                    operationId: `compop_${"9".repeat(32)}`,
                    effectId: `effect_${"7".repeat(32)}`,
                    requestSha256: "8".repeat(64),
                  },
                ],
                fencedComponentIds: [COMPONENT],
                replayed: false,
              },
            }
          : { ok: true, value: emergencySettled(call) };
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    sandboxAdapter: {
      execute: async () => {
        adapterCalls += 1;
        return { dispatched: true };
      },
    },
  });
  const invoking = broker.invoke(input);
  await beginStarted.promise;

  const stopped = await broker.emergencyStop({
    workspaceId: WORKSPACE,
    idempotencyKey: "emergency.stop.late-begin",
    reasonCode: "owner_emergency_stop",
  });

  assert.equal(stopped.ok, true);
  assert.deepEqual(
    emergencyCalls.map((call) => call.phase),
    ["prepare", "settle"],
  );
  lateBegin.resolve({
    ok: true,
    value: { ticket: ticket(input), replayed: false },
  });
  const invocation = await invoking;
  assert.equal(invocation.ok, true);
  if (invocation.ok) assert.equal(invocation.value.state, "unknown");
  assert.equal(adapterCalls, 0);
  assert.equal(settlements.length, 1);
  assert.equal(settlements[0]?.state, "unknown");
  assert.equal(
    settlements[0]?.errorCode,
    "desktop_component_invocation_fenced_before_dispatch",
  );
  broker.dispose();
});

test("a replayed emergency prepare never repeats host cleanup", async () => {
  const input = sandboxInput();
  const started = deferred<AbortSignal>();
  const emergencyCalls: DesktopWorkspaceComponentNativeEmergencyStopInput[] =
    [];
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => ({
        ok: true,
        value: { ticket: ticket(input), replayed: false },
      }),
      emergency: async (call) => {
        emergencyCalls.push(call);
        if (call.phase !== "prepare") {
          throw new Error("replayed prepare must not settle again");
        }
        return {
          ok: true,
          value: {
            workspaceId: WORKSPACE,
            tickets: [
              {
                componentId: COMPONENT,
                operationId: `compop_${"9".repeat(32)}`,
                effectId: `effect_${"7".repeat(32)}`,
                requestSha256: "8".repeat(64),
              },
            ],
            fencedComponentIds: [COMPONENT],
            replayed: true,
          },
        };
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      execute: async ({ signal }) => {
        started.resolve(signal);
        return await new Promise<never>((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new Error("aborted")), {
            once: true,
          });
        });
      },
    },
  });
  const invocation = broker.invoke(input);
  const signal = await started.promise;

  const result = await broker.emergencyStop({
    workspaceId: WORKSPACE,
    idempotencyKey: "emergency.stop.replay",
    reasonCode: "owner_emergency_stop",
  });

  assert.deepEqual(result, {
    ok: false,
    error: { code: "desktop_component_emergency_stop_reconciliation_required" },
  });
  assert.equal(signal.aborted, false);
  assert.deepEqual(
    emergencyCalls.map((call) => call.phase),
    ["prepare"],
  );
  broker.stopAll();
  await invocation;
  broker.dispose();
});

test("a failed durable emergency fence does not pretend host revocation succeeded", async () => {
  const input = sandboxInput();
  const started = deferred<AbortSignal>();
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => ({
        ok: true,
        value: { ticket: ticket(input), replayed: false },
      }),
      emergency: async () => ({
        ok: false,
        error: { code: "desktop_component_emergency_stop_failed" },
      }),
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      execute: async ({ signal }) => {
        started.resolve(signal);
        return await new Promise<never>((_resolve, reject) => {
          signal.addEventListener("abort", () => reject(new Error("aborted")), {
            once: true,
          });
        });
      },
    },
  });
  const invoke = broker.invoke(input);
  const signal = await started.promise;
  const stopped = await broker.emergencyStop({
    workspaceId: WORKSPACE,
    idempotencyKey: "emergency.stop.2",
    reasonCode: "owner_emergency_stop",
  });
  assert.equal(stopped.ok, false);
  assert.equal(signal.aborted, false);
  broker.stopAll();
  await invoke;
  broker.dispose();
});

test("startup recovery revalidates a source UI package before durable success", async () => {
  const componentId = "builtin.workspace-canvas";
  const snapshot = recoverySnapshot(componentId, "builtin-ui.v1");
  const recovery = snapshot.recoveries[0]!;
  const settlements: DesktopWorkspaceComponentRecoverySettleInput[] = [];
  let payloadReads = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => {
      payloadReads += 1;
      return {
        component_id: componentId,
        schema_version: 1,
        version: "1.0.0",
        view: {
          kind: "workspace_summary",
          title: "Recovered Workspace",
          sections: [{ id: "health", label: "Health", source: "health" }],
        },
      };
    },
  });
  await broker.recoverStartup({
    snapshot,
    recovery,
    settle: async (input) => {
      settlements.push(input);
      return { ok: true, value: recoverySettled(input, componentId) };
    },
  });
  assert.equal(payloadReads, 1);
  assert.equal(settlements.length, 1);
  assert.equal(settlements[0]?.outcome, "succeeded");
  assert.equal(settlements[0]?.healthState, "healthy");
  broker.dispose();
});

test("ambiguous sandbox restart settles unknown once without replay", async () => {
  const snapshot = recoverySnapshot(COMPONENT, "p34-sandbox.v1");
  const recovery = snapshot.recoveries[0]!;
  let activations = 0;
  const settlements: DesktopWorkspaceComponentRecoverySettleInput[] = [];
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      activate: async () => {
        activations += 1;
        throw new Error("provider outcome unavailable");
      },
      stop: async () => ({ evidence: { stopped: true } }),
      execute: async () => ({}),
    },
  });
  await broker.recoverStartup({
    snapshot,
    recovery,
    settle: async (input) => {
      settlements.push(input);
      return { ok: true, value: recoverySettled(input, COMPONENT) };
    },
  });
  assert.equal(activations, 1);
  assert.equal(settlements.length, 1);
  assert.equal(settlements[0]?.outcome, "unknown");
  assert.equal(
    settlements[0]?.errorCode,
    "desktop_component_recovery_outcome_unknown",
  );
  broker.dispose();
});

test("a failed recovery settlement stops the newly started sandbox runtime", async () => {
  const snapshot = recoverySnapshot(COMPONENT, "p34-sandbox.v1");
  const recovery = snapshot.recoveries[0]!;
  let stops = 0;
  const broker = new ComponentRuntimeBroker({
    native: boundary({
      begin: async () => {
        throw new Error("unused");
      },
    }),
    workspaceFiles: workspaceFiles(),
    runtimeRoot: path.resolve("."),
    readSourceComponentPayload: async () => ({
      component_id: COMPONENT,
      input_contract: "logical_artifact_ids",
      output_contract: "artifact_inventory",
      provider: "p34-sandbox.v1",
      schema_version: 1,
      version: "1.0.0",
      workload_id: "bounded-transform",
    }),
    sandboxAdapter: {
      activate: async () => ({ health: "healthy", evidence: { ready: true } }),
      stop: async () => {
        stops += 1;
        return { evidence: { stopped: true } };
      },
      execute: async () => ({}),
    },
  });
  await assert.rejects(
    broker.recoverStartup({
      snapshot,
      recovery,
      settle: async () => ({
        ok: false,
        error: { code: "desktop_component_recovery_settle_failed" },
      }),
    }),
    /desktop_component_recovery_settle_failed/u,
  );
  assert.equal(stops, 1);
  broker.dispose();
});
