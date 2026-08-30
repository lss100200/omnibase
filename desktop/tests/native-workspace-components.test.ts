import assert from "node:assert/strict";
import { test } from "node:test";

import {
  parseWorkspaceComponentActionResult,
  parseWorkspaceComponentSnapshot,
} from "../src/runtime/native-workspace-components.ts";

const WORKSPACE_ID = `workspace_${"1".repeat(32)}`;
const SHA = "a".repeat(64);

function rawSnapshot(permission: unknown): Record<string, unknown> {
  return {
    workspace_id: WORKSPACE_ID,
    catalog: [
      {
        component_id: "builtin.workspace-canvas",
        version: "1.0.0",
        family: "declarative_ui",
        publisher_class: "source_owned",
        display_name: "Workspace Canvas",
        adapter_id: "builtin-ui.v1",
        policy_manifest_sha256: SHA,
        manifest_sha256: SHA,
        package_sha256: "b".repeat(64),
        operations: ["ui.render"],
        permissions: [permission],
        slots: [],
        dependencies: [],
        conflicts: [],
        budgets: {
          max_calls: 8,
          max_bytes_in: 1024,
          max_bytes_out: 2048,
          max_tokens: 0,
          max_wall_time_ms: 5000,
          max_cost_units: 4,
          max_retries: 0,
          max_concurrency: 1,
        },
        network: { required: false, service_classes: [] },
        recovery: {
          auto_replay_unknown: false,
          retention: "retain_workspace_data",
          safe_mode: "disable_component",
        },
        state_schema: { kind: "canonical_json", version: 1 },
        settings_schema: {
          kind: "closed_object",
          version: 1,
          additional_properties: false,
          properties: {},
          required: [],
        },
        available: true,
        unavailable_reason: null,
      },
    ],
    installations: [],
    proposals: [],
    operations: [],
    effects: [],
    grants: [],
    revocations: [],
    recoveries: [],
    reconciliations: [],
    audit: [],
  };
}

function rawInstallActionResult(
  includeVersion: boolean,
): Record<string, unknown> {
  const lifecycleTicket: Record<string, unknown> = {
    operation_id: `compop_${"2".repeat(32)}`,
    effect_id: `effect_${"3".repeat(32)}`,
    workspace_id: WORKSPACE_ID,
    component_id: "builtin.instruction-skill",
    action: "install",
    adapter_id: "instruction-skill.v1",
    installation_id: null,
    binding_generation: null,
    runtime_instance_id: null,
    workload_identity_digest: null,
    configuration: {},
    configuration_sha256: SHA,
    slot_bindings: [],
    slot_bindings_sha256: SHA,
    dependency_graph: [],
    dependency_graph_sha256: SHA,
    quiesce_timeout_ms: 5_000,
    request_sha256: SHA,
    manifest_sha256: SHA,
    package_sha256: "b".repeat(64),
  };
  if (includeVersion) lifecycleTicket.version = "1.0.0";
  return {
    operation: {
      operation_id: lifecycleTicket.operation_id,
      workspace_id: WORKSPACE_ID,
      component_id: lifecycleTicket.component_id,
      installation_id: null,
      action: "install",
      request_sha256: SHA,
      binding_generation: 0,
      state: "pending",
      result_sha256: null,
      evidence_sha256: null,
      error_code: null,
      created_at: "2026-08-30T00:00:00.000Z",
      updated_at: "2026-08-30T00:00:00.000Z",
    },
    installation: null,
    lifecycle_ticket: lifecycleTicket,
    replayed: false,
  };
}

function rawRecovery(): Record<string, unknown> {
  return {
    recovery_id: `recovery_${"4".repeat(32)}`,
    workspace_id: WORKSPACE_ID,
    component_id: "builtin.instruction-skill",
    installation_id: `installation_${"5".repeat(32)}`,
    binding_generation: 1,
    previous_runtime_instance_id: `runtime_${"6".repeat(32)}`,
    operation_id: `compop_${"7".repeat(32)}`,
    effect_id: `effect_${"8".repeat(32)}`,
    adapter_id: "instruction-skill.v1",
    runtime_instance_id: `runtime_${"9".repeat(32)}`,
    workload_identity_digest: "b".repeat(64),
    request_sha256: "c".repeat(64),
    manifest_sha256: "d".repeat(64),
    package_sha256: "e".repeat(64),
    state: "pending",
    reason_code: "startup_native_revalidation_required",
    created_at: "2026-08-30T00:00:00.000Z",
  };
}

test("component snapshot preserves the exact manifest permission classes", () => {
  const parsed = parseWorkspaceComponentSnapshot(
    rawSnapshot({
      action: "ui.render",
      data_scope: "workspace_logical",
      logical_resource_classes: ["workspace.component.input"],
      secret_reference_classes: [],
    }),
  );
  assert.ok(parsed);
  assert.deepEqual(parsed.catalog[0]?.permissions, [
    {
      action: "ui.render",
      dataScope: "workspace_logical",
      logicalResourceClasses: ["workspace.component.input"],
      secretReferenceClasses: [],
    },
  ]);
});

test("component snapshot rejects missing, mismatched, or open permission classes", () => {
  const missing = rawSnapshot({
    action: "ui.render",
    data_scope: "workspace_logical",
    logical_resource_classes: [],
    secret_reference_classes: [],
  });
  assert.equal(parseWorkspaceComponentSnapshot(missing), null);

  const mismatched = rawSnapshot({
    action: "mcp.call",
    data_scope: "workspace_logical",
    logical_resource_classes: ["workspace.component.input"],
    secret_reference_classes: [],
  });
  assert.equal(parseWorkspaceComponentSnapshot(mismatched), null);

  const unknown = rawSnapshot({
    action: "ui.render",
    data_scope: "ambient_host",
    logical_resource_classes: ["workspace.component.input"],
    secret_reference_classes: [],
  });
  assert.equal(parseWorkspaceComponentSnapshot(unknown), null);
});

test("component action parser requires the backend lifecycle ticket version", () => {
  const parsed = parseWorkspaceComponentActionResult(
    rawInstallActionResult(true),
  );
  assert.ok(parsed);
  assert.equal(parsed.lifecycleTicket.version, "1.0.0");
  assert.equal(
    parseWorkspaceComponentActionResult(rawInstallActionResult(false)),
    null,
  );
});

test("component snapshot accepts the exact backend recovery projection", () => {
  const recovery = rawRecovery();
  const input = rawSnapshot({
    action: "ui.render",
    data_scope: "workspace_logical",
    logical_resource_classes: ["workspace.component.input"],
    secret_reference_classes: [],
  });
  input.recoveries = [recovery];

  const parsed = parseWorkspaceComponentSnapshot(input);
  assert.ok(parsed);
  assert.equal(parsed.recoveries[0]?.recoveryId, recovery.recovery_id);

  input.recoveries = [{ ...recovery, configuration: {} }];
  assert.equal(parseWorkspaceComponentSnapshot(input), null);
});
