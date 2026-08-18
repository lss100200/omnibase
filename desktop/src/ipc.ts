import type { IpcMain, IpcMainInvokeEvent } from "electron";

import { isAllowedIpcSender } from "./security/origin-policy.ts";
import {
  IPC_CHANNELS,
  requireNoIpcArguments,
  type RuntimeStatus,
} from "./shared/ipc-contract.ts";

export interface IpcMainLike {
  handle(
    channel: string,
    listener: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown,
  ): void;
  removeHandler(channel: string): void;
}

export interface IpcDependencies {
  readonly getVersion: () => string;
  readonly getRuntimeStatus: () => RuntimeStatus;
  readonly retryRuntimeStartup: () => Promise<RuntimeStatus>;
}

function requireTrustedSender(event: IpcMainInvokeEvent): void {
  const senderUrl = event.senderFrame?.url ?? "";
  if (!isAllowedIpcSender(senderUrl)) {
    throw new Error("ipc_sender_not_allowed");
  }
}

export function registerClosedIpcHandlers(
  ipcMain: IpcMainLike | IpcMain,
  dependencies: IpcDependencies,
): void {
  for (const channel of Object.values(IPC_CHANNELS)) {
    ipcMain.removeHandler(channel);
  }
  ipcMain.handle(IPC_CHANNELS.appGetVersion, (event: IpcMainInvokeEvent, ...args: unknown[]) => {
    requireTrustedSender(event);
    requireNoIpcArguments(args);
    return dependencies.getVersion();
  });
  ipcMain.handle(IPC_CHANNELS.runtimeGetStatus, (event: IpcMainInvokeEvent, ...args: unknown[]) => {
    requireTrustedSender(event);
    requireNoIpcArguments(args);
    return dependencies.getRuntimeStatus();
  });
  ipcMain.handle(IPC_CHANNELS.runtimeRetryStartup, (event: IpcMainInvokeEvent, ...args: unknown[]) => {
    requireTrustedSender(event);
    requireNoIpcArguments(args);
    return dependencies.retryRuntimeStartup();
  });
}
