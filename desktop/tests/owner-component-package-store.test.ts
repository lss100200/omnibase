import assert from "node:assert/strict";
import {
  link,
  mkdir,
  mkdtemp,
  readdir,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test, { type TestContext } from "node:test";

import {
  OwnerComponentPackageStore,
  ownerComponentPackageFileIdentityMatches,
  type OwnerComponentPackageNativeBoundary,
} from "../src/runtime/owner-component-package-store.ts";
import type {
  DesktopConversationDetail,
  DesktopWorkspaceComponentAssistantPackageImportInput,
  DesktopWorkspaceComponentOwnerPackageRegisterInput,
} from "../src/shared/ipc-contract.ts";
import { canonicalJson, digestRaw } from "./component-package-fixture.ts";

const WORKSPACE = `workspace_${"a".repeat(32)}`;
const NOW = "2026-08-30T00:00:00.000Z";

function ownerPackage(): Record<string, unknown> {
  return {
    manifest: {
      budgets: {
        max_bytes_in: 1_024,
        max_bytes_out: 1_024,
        max_calls: 1,
        max_concurrency: 1,
        max_cost_units: 0,
        max_retries: 0,
        max_tokens: 0,
        max_wall_time_ms: 5_000,
      },
      compatibility: { desktop_schema_min: 11, host_api: "p7.3.v1" },
      component_id: "owner.reviewed-canvas",
      configuration_schema: {
        additional_properties: false,
        kind: "closed_object",
        properties: {},
        required: [],
        version: 1,
      },
      conflicts: [],
      dependencies: [],
      entrypoint: { adapter_id: "builtin-ui.v1", kind: "host_view_v1" },
      family: "declarative_ui",
      health: {
        kind: "native_receipt_v1",
        required_state: "healthy",
        timeout_ms: 5_000,
      },
      manifest_schema_version: 1,
      network: { required: false, service_classes: [] },
      operations: ["ui.render"],
      permissions: [
        {
          action: "ui.render",
          data_scope: "workspace_logical",
          logical_resource_classes: ["workspace.component.input"],
          secret_reference_classes: [],
        },
      ],
      publisher: { classification: "owner_reviewed", id: "owner.local" },
      quiesce_timeout_ms: 5_000,
      recovery: {
        auto_replay_unknown: false,
        retention: "retain_workspace_data",
        safe_mode: "disable_component",
      },
      slots: [
        {
          cardinality: "one",
          maximum_order: 10_000,
          minimum_order: 0,
          slot_id: "editor.component",
        },
      ],
      state_migration: {
        kind: "host_canonical_v1",
        requires_owner_review_on_schema_change: true,
      },
      state_schema: { kind: "canonical_json", version: 1 },
      uninstall: {
        retention: "retain_workspace_data",
        unbound_delete_forbidden: true,
      },
      version: "1.0.0",
    },
    schema_version: 1,
    view: {
      kind: "workspace_summary",
      sections: [{ id: "health", label: "Health", source: "health" }],
      title: "Owner canvas",
    },
  };
}

function encoded(value: unknown): Buffer {
  return Buffer.from(`${canonicalJson(value)}\n`, "utf8");
}

function assistantImportInput(
  value = ownerPackage(),
  messageId = `message_${"a".repeat(32)}`,
): DesktopWorkspaceComponentAssistantPackageImportInput {
  const packageRaw = encoded(value);
  const manifest = (value as Record<string, unknown>).manifest;
  return {
    workspaceId: WORKSPACE,
    conversationId: `conversation_${"a".repeat(32)}`,
    messageId,
    packageJson: packageRaw.toString("utf8"),
    manifestSha256: digestRaw(canonicalJson(manifest)),
    packageSha256: digestRaw(packageRaw),
  };
}

function assistantConversation(
  messages: readonly Readonly<{ id: string; content: string }>[],
  overrides: Partial<DesktopConversationDetail["conversation"]> = {},
): DesktopConversationDetail {
  const conversationId = `conversation_${"a".repeat(32)}`;
  return {
    conversation: {
      id: conversationId,
      workspaceId: WORKSPACE,
      title: "Component package review",
      state: "active",
      rowVersion: 1,
      createdAt: NOW,
      updatedAt: NOW,
      ...overrides,
    },
    messages: messages.map((message, index) => {
      const invocationId = `invocation_${String(index + 1).repeat(32)}`;
      return {
        id: message.id,
        role: "assistant",
        content: message.content,
        status: "completed",
        invocationId,
        retryOfMessageId: null,
        createdAt: NOW,
        invocation: {
          id: invocationId,
          providerId: `provider_${"a".repeat(32)}`,
          requestedModel: "review-model",
          actualModel: "review-model",
          family: "generic-openai-compatible",
          gear: "standard",
          thinkingDepth: "standard",
          status: "succeeded",
          durationMs: 1,
          inputTokens: 1,
          outputTokens: 1,
          totalTokens: 2,
          errorCode: null,
          errorRedacted: null,
          retryOfInvocationId: null,
          createdAt: NOW,
          updatedAt: NOW,
        },
      };
    }),
  };
}

function successfulRegistration(
  input: DesktopWorkspaceComponentOwnerPackageRegisterInput,
  replayed = false,
) {
  return {
    ok: true as const,
    value: {
      componentId: String(input.manifest.component_id),
      version: String(input.manifest.version),
      manifestSha256: input.manifestSha256,
      packageSha256: input.packageSha256,
      publisherClass: "owner_reviewed" as const,
      registeredAt: NOW,
      replayed,
    },
  };
}

function nativeBoundary(
  register: OwnerComponentPackageNativeBoundary["registerOwnerWorkspaceComponentPackage"],
): OwnerComponentPackageNativeBoundary {
  return {
    getConversation: async () => ({
      ok: false,
      error: { code: "unused" },
    }),
    registerOwnerWorkspaceComponentPackage: register,
  };
}

async function testRoot(t: TestContext): Promise<string> {
  const root = await mkdtemp(
    path.join(os.tmpdir(), "omnibase-p73-owner-store-"),
  );
  t.after(() => rm(root, { recursive: true, force: true }));
  return root;
}

async function writeReviewedPackage(
  root: string,
  value = ownerPackage(),
): Promise<string> {
  const selected = path.join(root, "owner-component.json");
  await writeFile(selected, encoded(value));
  return selected;
}

test("Owner-reviewed declarative UI imports, registers, promotes, and reads back", async (t) => {
  const root = await testRoot(t);
  const selected = await writeReviewedPackage(root);
  const registrations: DesktopWorkspaceComponentOwnerPackageRegisterInput[] =
    [];
  let store!: OwnerComponentPackageStore;
  store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => selected,
    native: nativeBoundary(async (input) => {
      assert.deepEqual(
        await store.readView(
          input.packageSha256,
          input.manifestSha256,
          "owner.reviewed-canvas",
        ),
        ownerPackage().view,
      );
      registrations.push(input);
      return successfulRegistration(input);
    }),
  });

  const result = await store.importPackage(WORKSPACE);

  assert.equal(result.ok, true);
  assert.equal(registrations.length, 1);
  const registration = registrations[0]!;
  assert.equal(registration.workspaceId, WORKSPACE);
  assert.equal(registration.manifest.component_id, "owner.reviewed-canvas");
  assert.deepEqual(
    await store.readView(
      registration.packageSha256,
      registration.manifestSha256,
      "owner.reviewed-canvas",
    ),
    ownerPackage().view,
  );
});

test("Owner package picker rejects relative paths and non-file targets", async (t) => {
  const root = await testRoot(t);
  const directoryTarget = path.join(root, "directory.json");
  await mkdir(directoryTarget);
  const native = nativeBoundary(async (input) => successfulRegistration(input));

  const relative = await new OwnerComponentPackageStore({
    dataRoot: path.join(root, "relative-data"),
    choosePackage: async () => "owner-component.json",
    native,
  }).importPackage(WORKSPACE);
  const directory = await new OwnerComponentPackageStore({
    dataRoot: path.join(root, "directory-data"),
    choosePackage: async () => directoryTarget,
    native,
  }).importPackage(WORKSPACE);

  assert.deepEqual(relative, {
    ok: false,
    error: { code: "desktop_component_owner_package_path_invalid" },
  });
  assert.deepEqual(directory, {
    ok: false,
    error: { code: "desktop_component_owner_package_identity_invalid" },
  });
});

test("Owner package picker rejects symbolic links", async (t) => {
  const root = await testRoot(t);
  const selected = await writeReviewedPackage(root);
  const symbolic = path.join(root, "symbolic.json");
  await symlink(selected, symbolic, "file");
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => symbolic,
    native: nativeBoundary(async (input) => successfulRegistration(input)),
  });

  assert.deepEqual(await store.importPackage(WORKSPACE), {
    ok: false,
    error: { code: "desktop_component_owner_package_identity_invalid" },
  });
});

test("Owner package picker rejects hard-linked files", async (t) => {
  const root = await testRoot(t);
  const selected = await writeReviewedPackage(root);
  const hardLinked = path.join(root, "hard-linked.json");
  await link(selected, hardLinked);
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => hardLinked,
    native: nativeBoundary(async (input) => successfulRegistration(input)),
  });

  assert.deepEqual(await store.importPackage(WORKSPACE), {
    ok: false,
    error: { code: "desktop_component_owner_package_identity_invalid" },
  });
});

test("Owner package parser rejects unknown keys", async (t) => {
  const root = await testRoot(t);
  const value = ownerPackage();
  value.unreviewed = true;
  const selected = await writeReviewedPackage(root, value);
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => selected,
    native: nativeBoundary(async (input) => successfulRegistration(input)),
  });

  assert.deepEqual(await store.importPackage(WORKSPACE), {
    ok: false,
    error: { code: "desktop_component_owner_package_shape_invalid" },
  });
});

for (const [label, title] of [
  ["script", "<script>alert(1)</script>"],
  ["URL", "https://unreviewed.invalid/view"],
  ["authority", "request bearer credential"],
] as const) {
  test(`Owner package parser rejects ${label} content`, async (t) => {
    const root = await testRoot(t);
    const value = ownerPackage();
    (value.view as Record<string, unknown>).title = title;
    const selected = await writeReviewedPackage(root, value);
    const store = new OwnerComponentPackageStore({
      dataRoot: path.join(root, "data"),
      choosePackage: async () => selected,
      native: nativeBoundary(async (input) => successfulRegistration(input)),
    });

    assert.deepEqual(await store.importPackage(WORKSPACE), {
      ok: false,
      error: { code: "desktop_component_owner_package_authority_forbidden" },
    });
  });
}

test("Owner package file identity rejects every TOCTOU-relevant drift", () => {
  const stable = { dev: 1, ino: 2, size: 3, mtimeMs: 4, nlink: 1 };
  assert.equal(ownerComponentPackageFileIdentityMatches(stable, stable), true);
  for (const changed of [
    { ...stable, dev: 9 },
    { ...stable, ino: 9 },
    { ...stable, size: 9 },
    { ...stable, mtimeMs: 9 },
    { ...stable, nlink: 2 },
  ]) {
    assert.equal(
      ownerComponentPackageFileIdentityMatches(stable, changed),
      false,
    );
  }
});

test("Owner package parser rejects oversized packages before registration", async (t) => {
  const root = await testRoot(t);
  const selected = path.join(root, "oversized.json");
  await writeFile(selected, Buffer.alloc(256 * 1024 + 1, 0x20));
  let registrations = 0;
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => selected,
    native: nativeBoundary(async (input) => {
      registrations += 1;
      return successfulRegistration(input);
    }),
  });

  assert.deepEqual(await store.importPackage(WORKSPACE), {
    ok: false,
    error: { code: "desktop_component_owner_package_size_invalid" },
  });
  assert.equal(registrations, 0);
});

test("duplicate registration reuses only the exact promoted package", async (t) => {
  const root = await testRoot(t);
  const selected = await writeReviewedPackage(root);
  let calls = 0;
  const registrations: DesktopWorkspaceComponentOwnerPackageRegisterInput[] =
    [];
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => selected,
    native: nativeBoundary(async (input) => {
      registrations.push(input);
      return successfulRegistration(input, calls++ > 0);
    }),
  });

  const first = await store.importPackage(WORKSPACE);
  const replay = await store.importPackage(WORKSPACE);

  assert.equal(first.ok, true);
  assert.equal(replay.ok, true);
  if (replay.ok) assert.equal(replay.value.registration?.replayed, true);
  assert.equal(calls, 2);
  const registered = registrations[0];
  assert.ok(registered);
  assert.deepEqual(
    await readdir(path.join(root, "data", "component-packages")),
    [`${registered.packageSha256}.json`],
  );
});

test("backend rejection preserves the published unreferenced package", async (t) => {
  const root = await testRoot(t);
  const selected = await writeReviewedPackage(root);
  const storeRoot = path.join(root, "data", "component-packages");
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => selected,
    native: nativeBoundary(async () => ({
      ok: false,
      error: { code: "desktop_component_owner_package_rejected" },
    })),
  });

  assert.deepEqual(await store.importPackage(WORKSPACE), {
    ok: false,
    error: { code: "desktop_component_owner_package_rejected" },
  });
  assert.equal((await readdir(storeRoot)).length, 1);
  assert.equal(
    (await readdir(storeRoot)).some((name) => name.endsWith(".tmp")),
    false,
  );
});

test("an ambiguous backend response preserves the package for explicit retry", async (t) => {
  const root = await testRoot(t);
  const value = ownerPackage();
  const selected = await writeReviewedPackage(root, value);
  const packageSha256 = digestRaw(encoded(value));
  const manifestSha256 = digestRaw(canonicalJson(value.manifest));
  let calls = 0;
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => selected,
    native: nativeBoundary(async (registration) => {
      calls += 1;
      if (calls === 1) {
        // The remote side may have committed before the response was lost.
        throw new Error("desktop_component_owner_package_response_lost");
      }
      return successfulRegistration(registration, true);
    }),
  });

  assert.deepEqual(await store.importPackage(WORKSPACE), {
    ok: false,
    error: { code: "desktop_component_owner_package_response_lost" },
  });
  assert.deepEqual(
    await store.readView(
      packageSha256,
      manifestSha256,
      "owner.reviewed-canvas",
    ),
    value.view,
  );

  const retried = await store.importPackage(WORKSPACE);

  assert.equal(retried.ok, true);
  assert.equal(calls, 2);
  assert.deepEqual(
    await readdir(path.join(root, "data", "component-packages")),
    [`${packageSha256}.json`],
  );
});

test("backend rejection never deletes an exact existing shared package", async (t) => {
  const root = await testRoot(t);
  const value = ownerPackage();
  const selected = await writeReviewedPackage(root, value);
  const storeRoot = path.join(root, "data", "component-packages");
  const packageRaw = encoded(value);
  const packageSha256 = digestRaw(packageRaw);
  await mkdir(storeRoot, { recursive: true });
  await writeFile(path.join(storeRoot, `${packageSha256}.json`), packageRaw);
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => selected,
    native: nativeBoundary(async () => ({
      ok: false,
      error: { code: "desktop_component_owner_package_rejected" },
    })),
  });

  assert.deepEqual(await store.importPackage(WORKSPACE), {
    ok: false,
    error: { code: "desktop_component_owner_package_rejected" },
  });
  assert.deepEqual(await readdir(storeRoot), [`${packageSha256}.json`]);
  assert.deepEqual(
    await store.readView(
      packageSha256,
      digestRaw(canonicalJson(value.manifest)),
      "owner.reviewed-canvas",
    ),
    value.view,
  );
});

test("promotion failure never registers and an exact retry closes the gap", async (t) => {
  const root = await testRoot(t);
  const value = ownerPackage();
  const selected = await writeReviewedPackage(root, value);
  const storeRoot = path.join(root, "data", "component-packages");
  const packageSha256 = digestRaw(encoded(value));
  const target = path.join(storeRoot, `${packageSha256}.json`);
  await mkdir(target, { recursive: true });
  const registrations: DesktopWorkspaceComponentOwnerPackageRegisterInput[] =
    [];
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => selected,
    native: nativeBoundary(async (input) => {
      registrations.push(input);
      return successfulRegistration(input);
    }),
  });

  assert.deepEqual(await store.importPackage(WORKSPACE), {
    ok: false,
    error: { code: "desktop_component_owner_package_promote_failed" },
  });
  assert.equal(registrations.length, 0);
  assert.equal(
    await store.readView(
      packageSha256,
      digestRaw(canonicalJson(value.manifest)),
      "owner.reviewed-canvas",
    ),
    null,
  );
  await rm(target, { recursive: true });

  const retried = await store.importPackage(WORKSPACE);

  assert.equal(retried.ok, true);
  assert.equal(registrations.length, 1);
  assert.deepEqual(
    await store.readView(
      packageSha256,
      digestRaw(canonicalJson(value.manifest)),
      "owner.reviewed-canvas",
    ),
    value.view,
  );
  assert.equal(
    (await readdir(storeRoot)).some((name) => name.endsWith(".tmp")),
    false,
  );
});

test("latest successful assistant package registers only after exact Owner review", async (t) => {
  const root = await testRoot(t);
  const input = assistantImportInput();
  const registrations: DesktopWorkspaceComponentOwnerPackageRegisterInput[] =
    [];
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => null,
    native: {
      getConversation: async () => ({
        ok: true,
        value: assistantConversation([
          { id: input.messageId, content: input.packageJson },
        ]),
      }),
      registerOwnerWorkspaceComponentPackage: async (registration) => {
        registrations.push(registration);
        return successfulRegistration(registration);
      },
    },
  });

  const result = await store.importAssistantPackage(input);

  assert.equal(result.ok, true);
  assert.equal(registrations.length, 1);
  assert.equal(registrations[0]?.workspaceId, WORKSPACE);
  assert.deepEqual(
    await store.readView(
      input.packageSha256,
      input.manifestSha256,
      "owner.reviewed-canvas",
    ),
    ownerPackage().view,
  );
});

test("assistant package rejects stale messages and cross-scope conversations", async (t) => {
  const root = await testRoot(t);
  const stale = assistantImportInput(
    ownerPackage(),
    `message_${"1".repeat(32)}`,
  );
  const latest = assistantImportInput(
    ownerPackage(),
    `message_${"2".repeat(32)}`,
  );
  const noRegister = async (
    registration: DesktopWorkspaceComponentOwnerPackageRegisterInput,
  ) => successfulRegistration(registration);
  const staleStore = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "stale"),
    choosePackage: async () => null,
    native: {
      getConversation: async () => ({
        ok: true,
        value: assistantConversation([
          { id: stale.messageId, content: stale.packageJson },
          { id: latest.messageId, content: latest.packageJson },
        ]),
      }),
      registerOwnerWorkspaceComponentPackage: noRegister,
    },
  });
  assert.deepEqual(await staleStore.importAssistantPackage(stale), {
    ok: false,
    error: { code: "desktop_component_assistant_package_message_stale" },
  });

  const otherWorkspace = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "cross-scope"),
    choosePackage: async () => null,
    native: {
      getConversation: async () => ({
        ok: true,
        value: assistantConversation(
          [{ id: stale.messageId, content: stale.packageJson }],
          { workspaceId: `workspace_${"b".repeat(32)}` },
        ),
      }),
      registerOwnerWorkspaceComponentPackage: noRegister,
    },
  });
  assert.deepEqual(await otherWorkspace.importAssistantPackage(stale), {
    ok: false,
    error: { code: "desktop_component_assistant_package_scope_invalid" },
  });

  const otherConversation = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "cross-conversation"),
    choosePackage: async () => null,
    native: {
      getConversation: async () => ({
        ok: true,
        value: assistantConversation(
          [{ id: stale.messageId, content: stale.packageJson }],
          { id: `conversation_${"b".repeat(32)}` },
        ),
      }),
      registerOwnerWorkspaceComponentPackage: noRegister,
    },
  });
  assert.deepEqual(await otherConversation.importAssistantPackage(stale), {
    ok: false,
    error: { code: "desktop_component_assistant_package_scope_invalid" },
  });
});

test("assistant package rejects canonical bytes, digest, and sensitive configuration drift", async (t) => {
  const root = await testRoot(t);
  const valid = assistantImportInput();
  const makeStore = (
    input: DesktopWorkspaceComponentAssistantPackageImportInput,
  ) =>
    new OwnerComponentPackageStore({
      dataRoot: path.join(root, input.packageSha256.slice(0, 8)),
      choosePackage: async () => null,
      native: {
        getConversation: async () => ({
          ok: true,
          value: assistantConversation([
            { id: input.messageId, content: valid.packageJson },
          ]),
        }),
        registerOwnerWorkspaceComponentPackage: async (registration) =>
          successfulRegistration(registration),
      },
    });
  assert.deepEqual(
    await makeStore(valid).importAssistantPackage({
      ...valid,
      packageJson: ` ${valid.packageJson}`,
    }),
    {
      ok: false,
      error: { code: "desktop_component_assistant_package_identity_drift" },
    },
  );
  assert.deepEqual(
    await makeStore(valid).importAssistantPackage({
      ...valid,
      manifestSha256: "0".repeat(64),
    }),
    {
      ok: false,
      error: { code: "desktop_component_assistant_package_identity_drift" },
    },
  );

  const sensitive = ownerPackage();
  const configuration = (sensitive.manifest as Record<string, unknown>)
    .configuration_schema as Record<string, unknown>;
  configuration.properties = {
    secret_token: { type: "string", max_length: 64 },
  };
  configuration.required = ["secret_token"];
  const sensitiveInput = assistantImportInput(sensitive);
  const sensitiveStore = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "sensitive"),
    choosePackage: async () => null,
    native: {
      getConversation: async () => ({
        ok: true,
        value: assistantConversation([
          { id: sensitiveInput.messageId, content: sensitiveInput.packageJson },
        ]),
      }),
      registerOwnerWorkspaceComponentPackage: async (registration) =>
        successfulRegistration(registration),
    },
  });
  assert.deepEqual(
    await sensitiveStore.importAssistantPackage(sensitiveInput),
    {
      ok: false,
      error: { code: "desktop_component_owner_package_shape_invalid" },
    },
  );
});

test("assistant backend rejection preserves the published unreferenced package", async (t) => {
  const root = await testRoot(t);
  const input = assistantImportInput();
  const storeRoot = path.join(root, "data", "component-packages");
  const store = new OwnerComponentPackageStore({
    dataRoot: path.join(root, "data"),
    choosePackage: async () => null,
    native: {
      getConversation: async () => ({
        ok: true,
        value: assistantConversation([
          { id: input.messageId, content: input.packageJson },
        ]),
      }),
      registerOwnerWorkspaceComponentPackage: async () => ({
        ok: false,
        error: { code: "desktop_component_owner_package_rejected" },
      }),
    },
  });

  assert.deepEqual(await store.importAssistantPackage(input), {
    ok: false,
    error: { code: "desktop_component_owner_package_rejected" },
  });
  assert.deepEqual(await readdir(storeRoot), [`${input.packageSha256}.json`]);
});

test("owner package canonical digest is stable across source key ordering", () => {
  const first = encoded(ownerPackage());
  const reordered = Object.fromEntries(
    Object.entries(ownerPackage()).reverse(),
  );
  assert.equal(digestRaw(first), digestRaw(encoded(reordered)));
});
