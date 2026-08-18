import assert from "node:assert/strict";
import test from "node:test";

import type { IpcMainInvokeEvent } from "electron";

import {
  registerClosedIpcHandlers,
  type IpcMainLike,
} from "../src/ipc.ts";
import { DESKTOP_UI_ORIGIN } from "../src/security/origin-policy.ts";
import {
  IPC_CHANNEL_SET,
  IPC_CHANNELS,
  type RuntimeStatus,
} from "../src/shared/ipc-contract.ts";

test("preload/main IPC is a strict three-channel closed set", async () => {
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
  });

  assert.deepEqual(new Set(handlers.keys()), IPC_CHANNEL_SET);
  assert.deepEqual(new Set(removed), IPC_CHANNEL_SET);
  assert.equal(handlers.size, 3);

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
      getRuntimeStatus: () => ({ phase: "stopped", attempts: 0, lastError: null }),
      retryRuntimeStartup: async () => ({
        phase: "failed",
        attempts: 1,
        lastError: "failed",
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
});
