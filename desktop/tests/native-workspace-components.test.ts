import assert from "node:assert/strict";
import { test } from "node:test";

import { parseWorkspaceComponentSnapshot } from "../src/runtime/native-workspace-components.ts";

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
