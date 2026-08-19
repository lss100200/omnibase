import { contextBridge, ipcRenderer } from "electron";

import type {
  DesktopOperationResult,
  DesktopOwnerBootstrapInput,
  DesktopOwnerBootstrapResult,
  DesktopOwnerStatus,
  DesktopWorkspaceArchiveInput,
  DesktopWorkspaceCreateInput,
  DesktopWorkspaceList,
  DesktopWorkspaceMutationResult,
  OmniBaseDesktopApi,
  RuntimeStatus,
} from "./shared/ipc-contract.ts";

// A sandboxed Electron preload may use Electron's limited built-in bridge but
// must not depend on a local CommonJS require chain. Keep this runtime object
// self-contained; ipc.test.ts pins the corresponding main-process closed set.
const PRELOAD_IPC_CHANNELS = Object.freeze({
  appGetVersion: "omnibase:app:get-version",
  runtimeGetStatus: "omnibase:runtime:get-status",
  runtimeRetryStartup: "omnibase:runtime:retry-startup",
  ownerGetStatus: "omnibase:owner:get-status",
  ownerBootstrap: "omnibase:owner:bootstrap",
  workspacesList: "omnibase:workspaces:list",
  workspacesCreate: "omnibase:workspaces:create",
  workspacesArchive: "omnibase:workspaces:archive",
} as const);

const api: OmniBaseDesktopApi = Object.freeze({
  app: Object.freeze({
    getVersion: (): Promise<string> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.appGetVersion) as Promise<string>,
  }),
  runtime: Object.freeze({
    getStatus: (): Promise<RuntimeStatus> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.runtimeGetStatus,
      ) as Promise<RuntimeStatus>,
    retryStartup: (): Promise<RuntimeStatus> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.runtimeRetryStartup,
      ) as Promise<RuntimeStatus>,
  }),
  owner: Object.freeze({
    getStatus: (): Promise<DesktopOperationResult<DesktopOwnerStatus>> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.ownerGetStatus) as Promise<
        DesktopOperationResult<DesktopOwnerStatus>
      >,
    bootstrap: (
      input: DesktopOwnerBootstrapInput,
    ): Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.ownerBootstrap, input) as Promise<
        DesktopOperationResult<DesktopOwnerBootstrapResult>
      >,
  }),
  workspaces: Object.freeze({
    list: (): Promise<DesktopOperationResult<DesktopWorkspaceList>> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.workspacesList) as Promise<
        DesktopOperationResult<DesktopWorkspaceList>
      >,
    create: (
      input: DesktopWorkspaceCreateInput,
    ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspacesCreate,
        input,
      ) as Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>,
    archive: (
      input: DesktopWorkspaceArchiveInput,
    ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> =>
      ipcRenderer.invoke(
        PRELOAD_IPC_CHANNELS.workspacesArchive,
        input,
      ) as Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>,
  }),
});

contextBridge.exposeInMainWorld("omnibaseDesktop", api);
