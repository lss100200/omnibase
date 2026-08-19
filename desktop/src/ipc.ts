import type { IpcMain, IpcMainInvokeEvent } from "electron";

import { isAllowedIpcSender } from "./security/origin-policy.ts";
import {
  IPC_CHANNELS,
  requireNoIpcArguments,
  type DesktopOperationResult,
  type DesktopOwnerBootstrapInput,
  type DesktopOwnerBootstrapResult,
  type DesktopOwnerStatus,
  type DesktopWorkspaceArchiveInput,
  type DesktopWorkspaceCreateInput,
  type DesktopWorkspaceList,
  type DesktopWorkspaceMutationResult,
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
  readonly getOwnerStatus: () => Promise<
    DesktopOperationResult<DesktopOwnerStatus>
  >;
  readonly bootstrapOwner: (
    input: DesktopOwnerBootstrapInput,
  ) => Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>>;
  readonly listWorkspaces: () => Promise<
    DesktopOperationResult<DesktopWorkspaceList>
  >;
  readonly createWorkspace: (
    input: DesktopWorkspaceCreateInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>;
  readonly archiveWorkspace: (
    input: DesktopWorkspaceArchiveInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>;
}

const WORKSPACE_ID_PATTERN = /^workspace_[a-f0-9]{32}$/u;
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/u;

function requireTrustedSender(event: IpcMainInvokeEvent): void {
  const senderUrl = event.senderFrame?.url ?? "";
  if (!isAllowedIpcSender(senderUrl)) {
    throw new Error("ipc_sender_not_allowed");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  return (
    actual.length === sortedExpected.length &&
    actual.every((key, index) => key === sortedExpected[index])
  );
}

function normalizedName(value: unknown): string | null {
  if (typeof value !== "string" || CONTROL_CHARACTER_PATTERN.test(value))
    return null;
  const normalized = value.trim();
  return normalized.length >= 1 && normalized.length <= 256 ? normalized : null;
}

function invalidInput<T>(): DesktopOperationResult<T> {
  return Object.freeze({
    ok: false,
    error: Object.freeze({ code: "desktop_native_input_invalid" }),
  });
}

function parseOwnerBootstrapInput(
  args: readonly unknown[],
): DesktopOwnerBootstrapInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["displayName"])
  ) {
    return null;
  }
  const displayName = normalizedName(args[0].displayName);
  return displayName === null ? null : Object.freeze({ displayName });
}

function parseWorkspaceCreateInput(
  args: readonly unknown[],
): DesktopWorkspaceCreateInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["name"])
  ) {
    return null;
  }
  const name = normalizedName(args[0].name);
  return name === null ? null : Object.freeze({ name });
}

function parseWorkspaceArchiveInput(
  args: readonly unknown[],
): DesktopWorkspaceArchiveInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["expectedRowVersion", "workspaceId"]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].expectedRowVersion !== "number" ||
    !Number.isInteger(args[0].expectedRowVersion) ||
    args[0].expectedRowVersion < 1 ||
    args[0].expectedRowVersion > 2_147_483_647
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    expectedRowVersion: args[0].expectedRowVersion,
  });
}

export function registerClosedIpcHandlers(
  ipcMain: IpcMainLike | IpcMain,
  dependencies: IpcDependencies,
): void {
  for (const channel of Object.values(IPC_CHANNELS)) {
    ipcMain.removeHandler(channel);
  }
  ipcMain.handle(
    IPC_CHANNELS.appGetVersion,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.getVersion();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.runtimeGetStatus,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.getRuntimeStatus();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.runtimeRetryStartup,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.retryRuntimeStartup();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.ownerGetStatus,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.getOwnerStatus();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.ownerBootstrap,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseOwnerBootstrapInput(args);
      return input === null
        ? invalidInput<DesktopOwnerBootstrapResult>()
        : dependencies.bootstrapOwner(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspacesList,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.listWorkspaces();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspacesCreate,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceCreateInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceMutationResult>()
        : dependencies.createWorkspace(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspacesArchive,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceArchiveInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceMutationResult>()
        : dependencies.archiveWorkspace(input);
    },
  );
}
