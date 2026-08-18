import { contextBridge, ipcRenderer } from "electron";

import type {
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
} as const);

const api: OmniBaseDesktopApi = Object.freeze({
  app: Object.freeze({
    getVersion: (): Promise<string> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.appGetVersion) as Promise<string>,
  }),
  runtime: Object.freeze({
    getStatus: (): Promise<RuntimeStatus> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.runtimeGetStatus) as Promise<RuntimeStatus>,
    retryStartup: (): Promise<RuntimeStatus> =>
      ipcRenderer.invoke(PRELOAD_IPC_CHANNELS.runtimeRetryStartup) as Promise<RuntimeStatus>,
  }),
});

contextBridge.exposeInMainWorld("omnibaseDesktop", api);
