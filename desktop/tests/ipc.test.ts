import assert from "node:assert/strict";
import test from "node:test";

import type { IpcMainInvokeEvent } from "electron";

import { registerClosedIpcHandlers, type IpcMainLike } from "../src/ipc.ts";
import { DESKTOP_UI_ORIGIN } from "../src/security/origin-policy.ts";
import {
  IPC_CHANNEL_SET,
  IPC_CHANNELS,
  type RuntimeStatus,
} from "../src/shared/ipc-contract.ts";

const unused = async () => ({
  ok: false as const,
  error: { code: "must-not-run" },
});

const productStubs = {
  getWorkspaceAgent: unused,
  getApplicationPreference: unused,
  updateApplicationPreference: unused,
  getWorkspaceComposition: unused,
  proposeWorkspaceComposition: unused,
  proposeWorkspaceCompositionFromAssistant: unused,
  proposeWorkspaceCompositionRollback: unused,
  decideWorkspaceComposition: unused,
  getWorkspaceComponents: unused,
  proposeWorkspaceComponent: unused,
  proposeWorkspaceComponentFromAssistant: unused,
  importOwnerWorkspaceComponentPackage: unused,
  importAssistantWorkspaceComponentPackage: unused,
  decideWorkspaceComponent: unused,
  applyWorkspaceComponentAction: unused,
  invokeWorkspaceComponent: unused,
  emergencyStopWorkspaceComponents: unused,
  reconcileWorkspaceComponent: unused,
  authorizeWorkspaceFiles: unused,
  releaseWorkspaceFiles: unused,
  listWorkspaceFiles: unused,
  readWorkspaceFile: unused,
  listProviders: async () => ({ ok: true as const, value: { items: [] } }),
  upsertProvider: unused,
  deleteProvider: unused,
  testProvider: unused,
  listConversations: unused,
  createConversation: unused,
  archiveConversation: unused,
  getConversation: unused,
  sendConversation: unused,
  cancelConversation: unused,
  abortInFlightSend: unused,
  listAgentRoles: unused,
  getAgentRole: unused,
  updateAgentRole: unused,
  testAgentRole: unused,
  startTeamRun: unused,
  cancelTeamRun: unused,
  getTeamRun: unused,
  listTeamRuns: unused,
  submitTeamProposal: unused,
  getTeamBlackboard: unused,
  recordTeamCollaboration: unused,
  executeTeamRun: unused,
  appendTeamRunBudget: unused,
};

const WORKSPACE_ID = `workspace_${"1".repeat(32)}`;
const PROFILE_SHA256 = "2".repeat(64);
const REQUEST_SHA256 = "3".repeat(64);
const COMPOSITION_PROPOSAL_ID = `proposal_${"4".repeat(32)}`;
const COMPOSITION_SLOT_IDS = [
  "agent.rail",
  "conversation.transcript",
  "event.agent-log",
  "event.output",
  "knowledge.ebook",
  "mcp.catalog",
  "provider.settings",
  "run.history",
  "sandbox.runtime",
  "settings.center",
  "skills.catalog",
  "source-control",
  "terminal",
  "workspace.brief",
  "workspace.explorer",
] as const;

function compositionProfile() {
  return {
    schemaVersion: 1,
    template: { id: "standard-workbench", version: 1 },
    appearance: { density: "inherit", quietChrome: false },
    layout: {
      agentPanel: "open",
      bottomPanel: "output",
      focusMode: false,
      sidebar: "explorer",
    },
    slots: Object.fromEntries(
      COMPOSITION_SLOT_IDS.map((slotId) => [
        slotId,
        ![
          "knowledge.ebook",
          "mcp.catalog",
          "sandbox.runtime",
          "skills.catalog",
          "source-control",
          "terminal",
        ].includes(slotId),
      ]),
    ),
  };
}

test("preload/main IPC is a closed product channel set", async () => {
  const handlers = new Map<
    string,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown
  >();
  const removed: string[] = [];
  const ipcMain: IpcMainLike = {
    handle: (channel, listener) => {
      handlers.set(channel, listener);
    },
    removeHandler: (channel) => {
      removed.push(channel);
      handlers.delete(channel);
    },
  };
  const ready: RuntimeStatus = Object.freeze({
    phase: "ready",
    attempts: 1,
    lastError: null,
  });
  registerClosedIpcHandlers(ipcMain, {
    getVersion: () => "1.0.0",
    getRuntimeStatus: () => ready,
    retryRuntimeStartup: async () => ready,
    getOwnerStatus: async () => ({
      ok: true,
      value: { initialized: false, owner: null },
    }),
    bootstrapOwner: async (input) => ({
      ok: true,
      value: {
        initialized: true,
        created: true,
        owner: {
          id: "owner_0123456789abcdef0123456789abcdef",
          displayName: input.displayName,
          createdAt: "2026-08-19T00:00:00Z",
          updatedAt: "2026-08-19T00:00:00Z",
        },
      },
    }),
    listWorkspaces: async () => ({ ok: true, value: { items: [] } }),
    createWorkspace: async (input) => ({
      ok: true,
      value: {
        workspace: {
          id: "workspace_0123456789abcdef0123456789abcdef",
          ownerId: "owner_0123456789abcdef0123456789abcdef",
          name: input.name,
          state: "active",
          rowVersion: 1,
          createdAt: "2026-08-19T00:00:00Z",
          updatedAt: "2026-08-19T00:00:00Z",
        },
      },
    }),
    archiveWorkspace: async (input) => ({
      ok: true,
      value: {
        workspace: {
          id: input.workspaceId,
          ownerId: "owner_0123456789abcdef0123456789abcdef",
          name: "Archived",
          state: "archived",
          rowVersion: input.expectedRowVersion + 1,
          createdAt: "2026-08-19T00:00:00Z",
          updatedAt: "2026-08-19T00:01:00Z",
        },
      },
    }),
    ...productStubs,
  });

  assert.deepEqual(new Set(handlers.keys()), IPC_CHANNEL_SET);
  assert.deepEqual(new Set(removed), IPC_CHANNEL_SET);
  assert.equal(handlers.size, IPC_CHANNEL_SET.size);

  const trustedEvent = {
    senderFrame: { url: `${DESKTOP_UI_ORIGIN}/dashboard` },
  } as IpcMainInvokeEvent;
  assert.equal(
    await handlers.get(IPC_CHANNELS.appGetVersion)?.(trustedEvent),
    "1.0.0",
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.runtimeGetStatus)?.(trustedEvent),
    ready,
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.runtimeRetryStartup)?.(trustedEvent),
    ready,
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.ownerGetStatus)?.(trustedEvent),
    { ok: true, value: { initialized: false, owner: null } },
  );
  const owner = await handlers.get(IPC_CHANNELS.ownerBootstrap)?.(
    trustedEvent,
    {
      displayName: "  Personal Owner  ",
    },
  );
  assert.equal(
    (owner as { value: { owner: { displayName: string } } }).value.owner
      .displayName,
    "Personal Owner",
  );
  const created = await handlers.get(IPC_CHANNELS.workspacesCreate)?.(
    trustedEvent,
    {
      name: "  Primary  ",
    },
  );
  assert.equal(
    (created as { value: { workspace: { name: string } } }).value.workspace
      .name,
    "Primary",
  );
});

test("Workspace composition IPC normalizes the closed profile and rejects widened authority", async () => {
  const handlers = new Map<
    string,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown
  >();
  let settingsInput: unknown;
  let compositionGetInput: unknown;
  let ownerProposalInput: unknown;
  let assistantProposalInput: unknown;
  let rollbackProposalInput: unknown;
  let decisionInput: unknown;
  registerClosedIpcHandlers(
    {
      handle: (
        channel: string,
        listener: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown,
      ) => handlers.set(channel, listener),
      removeHandler: () => undefined,
    },
    {
      getVersion: () => "1.0.0",
      getRuntimeStatus: () => ({
        phase: "ready",
        attempts: 1,
        lastError: null,
      }),
      retryRuntimeStartup: async () => ({
        phase: "ready",
        attempts: 1,
        lastError: null,
      }),
      getOwnerStatus: unused,
      bootstrapOwner: unused,
      listWorkspaces: unused,
      createWorkspace: unused,
      archiveWorkspace: unused,
      ...productStubs,
      updateApplicationPreference: async (input) => {
        settingsInput = input;
        return unused();
      },
      getWorkspaceComposition: async (input) => {
        compositionGetInput = input;
        return unused();
      },
      proposeWorkspaceComposition: async (input) => {
        ownerProposalInput = input;
        return unused();
      },
      proposeWorkspaceCompositionFromAssistant: async (input) => {
        assistantProposalInput = input;
        return unused();
      },
      proposeWorkspaceCompositionRollback: async (input) => {
        rollbackProposalInput = input;
        return unused();
      },
      decideWorkspaceComposition: async (input) => {
        decisionInput = input;
        return unused();
      },
    },
  );
  const trustedEvent = {
    senderFrame: { url: `${DESKTOP_UI_ORIGIN}/desktop` },
  } as IpcMainInvokeEvent;
  const profile = compositionProfile();

  await handlers.get(IPC_CHANNELS.workbenchSettingsUpdate)?.(trustedEvent, {
    density: "compact",
    reduceMotion: true,
    expectedRowVersion: 1,
  });
  await handlers.get(IPC_CHANNELS.workspaceCompositionGet)?.(trustedEvent, {
    workspaceId: WORKSPACE_ID,
  });
  await handlers.get(IPC_CHANNELS.workspaceCompositionPropose)?.(trustedEvent, {
    workspaceId: WORKSPACE_ID,
    expectedRevision: 1,
    expectedProfileSha256: PROFILE_SHA256,
    desiredProfile: profile,
  });
  await handlers.get(IPC_CHANNELS.workspaceCompositionProposeFromAssistant)?.(
    trustedEvent,
    {
      workspaceId: WORKSPACE_ID,
      expectedRevision: 1,
      expectedProfileSha256: PROFILE_SHA256,
      messageId: `message_${"5".repeat(32)}`,
    },
  );
  await handlers.get(IPC_CHANNELS.workspaceCompositionProposeRollback)?.(
    trustedEvent,
    {
      workspaceId: WORKSPACE_ID,
      expectedRevision: 2,
      expectedProfileSha256: PROFILE_SHA256,
      targetRevision: 1,
    },
  );
  await handlers.get(IPC_CHANNELS.workspaceCompositionDecide)?.(trustedEvent, {
    workspaceId: WORKSPACE_ID,
    proposalId: COMPOSITION_PROPOSAL_ID,
    requestSha256: REQUEST_SHA256,
    decision: "approve",
  });

  assert.deepEqual(settingsInput, {
    density: "compact",
    reduceMotion: true,
    expectedRowVersion: 1,
  });
  assert.deepEqual(compositionGetInput, { workspaceId: WORKSPACE_ID });
  assert.deepEqual(ownerProposalInput, {
    workspaceId: WORKSPACE_ID,
    expectedRevision: 1,
    expectedProfileSha256: PROFILE_SHA256,
    desiredProfile: profile,
  });
  assert.deepEqual(assistantProposalInput, {
    workspaceId: WORKSPACE_ID,
    expectedRevision: 1,
    expectedProfileSha256: PROFILE_SHA256,
    messageId: `message_${"5".repeat(32)}`,
  });
  assert.deepEqual(rollbackProposalInput, {
    workspaceId: WORKSPACE_ID,
    expectedRevision: 2,
    expectedProfileSha256: PROFILE_SHA256,
    targetRevision: 1,
  });
  assert.deepEqual(decisionInput, {
    workspaceId: WORKSPACE_ID,
    proposalId: COMPOSITION_PROPOSAL_ID,
    requestSha256: REQUEST_SHA256,
    decision: "approve",
  });

  ownerProposalInput = undefined;
  const widened = await handlers.get(
    IPC_CHANNELS.workspaceCompositionPropose,
  )?.(trustedEvent, {
    workspaceId: WORKSPACE_ID,
    expectedRevision: 1,
    expectedProfileSha256: PROFILE_SHA256,
    desiredProfile: {
      ...profile,
      slots: { ...profile.slots, "plugin.arbitrary": true },
    },
  });
  assert.deepEqual(widened, {
    ok: false,
    error: { code: "desktop_native_input_invalid" },
  });
  assert.equal(ownerProposalInput, undefined);

  for (const slots of [
    { ...profile.slots, terminal: true },
    { ...profile.slots, "settings.center": false },
  ]) {
    const widenedCapability = await handlers.get(
      IPC_CHANNELS.workspaceCompositionPropose,
    )?.(trustedEvent, {
      workspaceId: WORKSPACE_ID,
      expectedRevision: 1,
      expectedProfileSha256: PROFILE_SHA256,
      desiredProfile: { ...profile, slots },
    });
    assert.deepEqual(widenedCapability, {
      ok: false,
      error: { code: "desktop_native_input_invalid" },
    });
    assert.equal(ownerProposalInput, undefined);
  }

  decisionInput = undefined;
  const wrongDigest = await handlers.get(
    IPC_CHANNELS.workspaceCompositionDecide,
  )?.(trustedEvent, {
    workspaceId: WORKSPACE_ID,
    proposalId: COMPOSITION_PROPOSAL_ID,
    requestSha256: "not-a-digest",
    decision: "approve",
  });
  assert.deepEqual(wrongDigest, {
    ok: false,
    error: { code: "desktop_native_input_invalid" },
  });
  assert.equal(decisionInput, undefined);
});

test("assistant component package IPC accepts one exact bounded identity DTO", async () => {
  const handlers = new Map<
    string,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown
  >();
  let received: unknown;
  let calls = 0;
  registerClosedIpcHandlers(
    {
      handle: (
        channel: string,
        listener: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown,
      ) => handlers.set(channel, listener),
      removeHandler: () => undefined,
    },
    {
      getVersion: () => "1.0.0",
      getRuntimeStatus: () => ({
        phase: "ready",
        attempts: 1,
        lastError: null,
      }),
      retryRuntimeStartup: async () => ({
        phase: "ready",
        attempts: 1,
        lastError: null,
      }),
      getOwnerStatus: unused,
      bootstrapOwner: unused,
      listWorkspaces: unused,
      createWorkspace: unused,
      archiveWorkspace: unused,
      ...productStubs,
      importAssistantWorkspaceComponentPackage: async (input) => {
        calls += 1;
        received = input;
        return unused();
      },
    },
  );
  const trustedEvent = {
    senderFrame: { url: `${DESKTOP_UI_ORIGIN}/desktop` },
  } as IpcMainInvokeEvent;
  const valid = {
    workspaceId: WORKSPACE_ID,
    conversationId: `conversation_${"2".repeat(32)}`,
    messageId: `message_${"3".repeat(32)}`,
    packageJson: "{}",
    manifestSha256: "4".repeat(64),
    packageSha256: "5".repeat(64),
  };
  const handler = handlers.get(
    IPC_CHANNELS.workspaceComponentsImportAssistantPackage,
  );

  await handler?.(trustedEvent, valid);
  assert.deepEqual(received, valid);
  assert.equal(calls, 1);

  for (const invalid of [
    { ...valid, extra: true },
    { ...valid, workspaceId: "workspace_invalid" },
    { ...valid, conversationId: "conversation_invalid" },
    { ...valid, messageId: "message_invalid" },
    { ...valid, packageJson: "x".repeat(256 * 1024 + 1) },
    { ...valid, manifestSha256: "G".repeat(64) },
    { ...valid, packageSha256: "0".repeat(63) },
  ]) {
    assert.deepEqual(await handler?.(trustedEvent, invalid), {
      ok: false,
      error: { code: "desktop_native_input_invalid" },
    });
  }
  assert.equal(calls, 1);
});

test("component proposal IPC accepts manifest-scoped resource and service defaults", async () => {
  const handlers = new Map<
    string,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown
  >();
  let received: unknown;
  registerClosedIpcHandlers(
    {
      handle: (
        channel: string,
        listener: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown,
      ) => handlers.set(channel, listener),
      removeHandler: () => undefined,
    },
    {
      getVersion: () => "1.0.0",
      getRuntimeStatus: () => ({
        phase: "ready",
        attempts: 1,
        lastError: null,
      }),
      retryRuntimeStartup: async () => ({
        phase: "ready",
        attempts: 1,
        lastError: null,
      }),
      getOwnerStatus: unused,
      bootstrapOwner: unused,
      listWorkspaces: unused,
      createWorkspace: unused,
      archiveWorkspace: unused,
      ...productStubs,
      proposeWorkspaceComponent: async (input) => {
        received = input;
        return unused();
      },
    },
  );
  const trustedEvent = {
    senderFrame: { url: `${DESKTOP_UI_ORIGIN}/desktop` },
  } as IpcMainInvokeEvent;
  const valid = {
    workspaceId: WORKSPACE_ID,
    componentId: "builtin.readonly-mcp",
    targetVersion: "1.0.0",
    changeKind: "install",
    expectedRevision: 0,
    requestedGrants: [
      {
        action: "mcp.call",
        logicalResourceId: "workspace.component.input",
        resourceVersion: 1,
        logicalServiceId: "reviewed_https",
        expiresInSeconds: 3600,
        maximumInvocations: 8,
        maximumBytesIn: 1024,
        maximumBytesOut: 2048,
        maximumTokens: 0,
        maximumWallTimeMs: 5000,
        maximumCostUnits: 4,
      },
    ],
    desiredConfiguration: {},
    desiredSlotBindings: [],
    dependencyGraph: [],
    idempotencyKey: `p73_propose_install_${"1".repeat(32)}`,
  };
  const handler = handlers.get(IPC_CHANNELS.workspaceComponentsPropose);
  await handler?.(trustedEvent, valid);
  assert.deepEqual(received, valid);
  assert.deepEqual(
    await handler?.(trustedEvent, { ...valid, expectedRevision: -1 }),
    {
      ok: false,
      error: { code: "desktop_native_input_invalid" },
    },
  );
  assert.deepEqual(
    await handler?.(trustedEvent, { ...valid, changeKind: "upgrade" }),
    {
      ok: false,
      error: { code: "desktop_native_input_invalid" },
    },
  );
  assert.deepEqual(await handler?.(trustedEvent, { ...valid, extra: true }), {
    ok: false,
    error: { code: "desktop_native_input_invalid" },
  });
});

test("IPC rejects unexpected arguments and non-loopback senders", async () => {
  const handlers = new Map<
    string,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown
  >();
  registerClosedIpcHandlers(
    {
      handle: (
        channel: string,
        listener: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown,
      ) => {
        handlers.set(channel, listener);
      },
      removeHandler: () => undefined,
    },
    {
      getVersion: () => "1.0.0",
      getRuntimeStatus: () => ({
        phase: "stopped",
        attempts: 0,
        lastError: null,
      }),
      retryRuntimeStartup: async () => ({
        phase: "failed",
        attempts: 1,
        lastError: "failed",
      }),
      getOwnerStatus: async () => ({
        ok: true,
        value: { initialized: false, owner: null },
      }),
      bootstrapOwner: async () => ({
        ok: false,
        error: { code: "must-not-run" },
      }),
      listWorkspaces: async () => ({ ok: true, value: { items: [] } }),
      createWorkspace: async () => ({
        ok: false,
        error: { code: "must-not-run" },
      }),
      archiveWorkspace: async () => ({
        ok: false,
        error: { code: "must-not-run" },
      }),
      ...productStubs,
    },
  );
  const trustedEvent = {
    senderFrame: { url: `${DESKTOP_UI_ORIGIN}/` },
  } as IpcMainInvokeEvent;
  assert.throws(
    () => handlers.get(IPC_CHANNELS.appGetVersion)?.(trustedEvent, "extra"),
    /ipc_arguments_not_allowed/u,
  );
  const hostileEvent = {
    senderFrame: { url: "https://example.com/" },
  } as IpcMainInvokeEvent;
  assert.throws(
    () => handlers.get(IPC_CHANNELS.runtimeGetStatus)?.(hostileEvent),
    /ipc_sender_not_allowed/u,
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.ownerBootstrap)?.(trustedEvent, {
      displayName: "invalid\nname",
    }),
    { ok: false, error: { code: "desktop_native_input_invalid" } },
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.workspacesArchive)?.(trustedEvent, {
      workspaceId: "not-a-workspace",
      expectedRowVersion: 1,
    }),
    { ok: false, error: { code: "desktop_native_input_invalid" } },
  );
});

test("workspace file IPC accepts only the exact scoped generation contract", async () => {
  const handlers = new Map<
    string,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown
  >();
  const calls: unknown[] = [];
  registerClosedIpcHandlers(
    {
      handle: (
        channel: string,
        listener: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown,
      ) => handlers.set(channel, listener),
      removeHandler: () => undefined,
    },
    {
      getVersion: () => "1.0.0",
      getRuntimeStatus: () => ({
        phase: "ready",
        attempts: 1,
        lastError: null,
      }),
      retryRuntimeStartup: async () => ({
        phase: "ready",
        attempts: 1,
        lastError: null,
      }),
      getOwnerStatus: unused,
      bootstrapOwner: unused,
      listWorkspaces: unused,
      createWorkspace: unused,
      archiveWorkspace: unused,
      ...productStubs,
      authorizeWorkspaceFiles: async (input) => {
        calls.push(input);
        return {
          ok: true,
          value: {
            workspaceId: input.workspaceId,
            rootName: "project",
            authorizationGeneration: 7,
          },
        };
      },
      releaseWorkspaceFiles: async (input) => {
        calls.push(input);
        return { ok: true, value: { released: true } };
      },
      listWorkspaceFiles: async (input) => {
        calls.push(input);
        return {
          ok: true,
          value: {
            directoryPath: input.directoryPath,
            entries: [],
            truncated: false,
          },
        };
      },
      readWorkspaceFile: async (input) => {
        calls.push(input);
        return {
          ok: true,
          value: {
            path: input.path,
            content: "hello\n",
            sizeBytes: 6,
            lastModifiedMs: 1,
            sha256: "0".repeat(64),
          },
        };
      },
    },
  );
  const trustedEvent = {
    senderFrame: { url: `${DESKTOP_UI_ORIGIN}/desktop` },
  } as IpcMainInvokeEvent;
  const workspaceId = `workspace_${"a".repeat(32)}`;

  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.workspaceFilesAuthorize)?.(trustedEvent, {
      workspaceId,
    }),
    {
      ok: true,
      value: { workspaceId, rootName: "project", authorizationGeneration: 7 },
    },
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.workspaceFilesList)?.(trustedEvent, {
      workspaceId,
      authorizationGeneration: 7,
      directoryPath: "src",
    }),
    {
      ok: true,
      value: { directoryPath: "src", entries: [], truncated: false },
    },
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.workspaceFilesRead)?.(trustedEvent, {
      workspaceId,
      authorizationGeneration: 7,
      path: "src/main.ts",
    }),
    {
      ok: true,
      value: {
        path: "src/main.ts",
        content: "hello\n",
        sizeBytes: 6,
        lastModifiedMs: 1,
        sha256: "0".repeat(64),
      },
    },
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.workspaceFilesRelease)?.(trustedEvent, {
      workspaceId,
      authorizationGeneration: 7,
    }),
    { ok: true, value: { released: true } },
  );
  assert.deepEqual(calls, [
    { workspaceId },
    { workspaceId, authorizationGeneration: 7, directoryPath: "src" },
    { workspaceId, authorizationGeneration: 7, path: "src/main.ts" },
    { workspaceId, authorizationGeneration: 7 },
  ]);

  for (const [channel, payload] of [
    [IPC_CHANNELS.workspaceFilesAuthorize, { workspaceId, extra: true }],
    [
      IPC_CHANNELS.workspaceFilesRelease,
      { workspaceId, authorizationGeneration: 1.5 },
    ],
    [
      IPC_CHANNELS.workspaceFilesList,
      { workspaceId, authorizationGeneration: 7, directoryPath: "bad\npath" },
    ],
    [
      IPC_CHANNELS.workspaceFilesRead,
      { workspaceId, authorizationGeneration: 7, path: "" },
    ],
  ] as const) {
    assert.deepEqual(await handlers.get(channel)?.(trustedEvent, payload), {
      ok: false,
      error: { code: "desktop_native_input_invalid" },
    });
  }
  assert.equal(calls.length, 4);
});

test("destroyed renderer cannot receive conversation stream events", async () => {
  const handlers = new Map<
    string,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown
  >();
  const ipcMain: IpcMainLike = {
    handle: (channel, listener) => {
      handlers.set(channel, listener);
    },
    removeHandler: () => undefined,
  };
  const ready: RuntimeStatus = Object.freeze({
    phase: "ready",
    attempts: 1,
    lastError: null,
  });
  let sent = 0;
  registerClosedIpcHandlers(ipcMain, {
    getVersion: () => "1.0.0",
    getRuntimeStatus: () => ready,
    retryRuntimeStartup: async () => ready,
    getOwnerStatus: unused,
    bootstrapOwner: unused,
    listWorkspaces: unused,
    createWorkspace: unused,
    archiveWorkspace: unused,
    ...productStubs,
    sendConversation: async (_input, emit) => {
      emit({
        type: "delta",
        invocationId: `invocation_${"d".repeat(32)}`,
        workspaceId: `workspace_${"b".repeat(32)}`,
        conversationId: `conversation_${"c".repeat(32)}`,
        text: "must-not-arrive",
      });
      return { ok: false as const, error: { code: "must-not-complete" } };
    },
  });
  const destroyed = {
    senderFrame: { url: `${DESKTOP_UI_ORIGIN}/desktop` },
    sender: {
      isDestroyed: () => true,
      send: () => {
        sent += 1;
      },
    },
  } as unknown as IpcMainInvokeEvent;
  await assert.rejects(
    async () =>
      handlers.get(IPC_CHANNELS.conversationSend)?.(destroyed, {
        workspaceId: `workspace_${"b".repeat(32)}`,
        conversationId: `conversation_${"c".repeat(32)}`,
        content: "hello",
      }),
    /desktop_renderer_destroyed/u,
  );
  assert.equal(sent, 0);
});

test("abort-in-flight send does not require an invocation id; durable cancel still does", async () => {
  const handlers = new Map<
    string,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown
  >();
  let aborted = 0;
  registerClosedIpcHandlers(
    {
      handle: (
        channel: string,
        listener: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown,
      ) => {
        handlers.set(channel, listener);
      },
      removeHandler: () => undefined,
    },
    {
      getVersion: () => "1.0.0",
      getRuntimeStatus: () => ({
        phase: "ready",
        attempts: 1,
        lastError: null,
      }),
      retryRuntimeStartup: async () => ({
        phase: "ready",
        attempts: 1,
        lastError: null,
      }),
      getOwnerStatus: unused,
      bootstrapOwner: unused,
      listWorkspaces: unused,
      createWorkspace: unused,
      archiveWorkspace: unused,
      ...productStubs,
      abortInFlightSend: async () => {
        aborted += 1;
        return { ok: true as const, value: { aborted: true } };
      },
    },
  );
  const trustedEvent = {
    senderFrame: { url: `${DESKTOP_UI_ORIGIN}/desktop` },
  } as IpcMainInvokeEvent;
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.conversationCancel)?.(trustedEvent, {}),
    { ok: false, error: { code: "desktop_native_input_invalid" } },
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.conversationCancel)?.(trustedEvent, {
      invocationId: "not-an-invocation",
    }),
    { ok: false, error: { code: "desktop_native_input_invalid" } },
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.conversationAbortInFlightSend)?.(
      trustedEvent,
    ),
    { ok: true, value: { aborted: true } },
  );
  assert.equal(aborted, 1);
});
