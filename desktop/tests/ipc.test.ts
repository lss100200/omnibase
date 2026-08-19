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

test("preload/main IPC is a strict eight-channel closed set", async () => {
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
  });

  assert.deepEqual(new Set(handlers.keys()), IPC_CHANNEL_SET);
  assert.deepEqual(new Set(removed), IPC_CHANNEL_SET);
  assert.equal(handlers.size, 8);

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
