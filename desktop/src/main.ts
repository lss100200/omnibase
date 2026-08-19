import path from "node:path";
import { app, BrowserWindow, dialog, ipcMain, safeStorage, session } from "electron";

import { enforceSingleInstance } from "./app-lifecycle.ts";
import { registerClosedIpcHandlers } from "./ipc.ts";
import { DESKTOP_UI_ORIGIN } from "./security/origin-policy.ts";
import {
  buildSecureWindowOptions,
  installFailClosedPermissionPolicy,
  installNavigationPolicy,
} from "./security/window-policy.ts";
import { RuntimeManager } from "./runtime/runtime-manager.ts";
import { PINNED_RUNTIME_MANIFEST_SHA256 } from "./runtime/trusted-manifest.ts";
import type { DesktopOperationResult } from "./shared/ipc-contract.ts";

let mainWindow: BrowserWindow | null = null;
let runtimeManager: RuntimeManager | null = null;

function runtimeUnavailable<T>(): Promise<DesktopOperationResult<T>> {
  return Promise.resolve(
    Object.freeze({
      ok: false,
      error: Object.freeze({ code: "desktop_runtime_not_ready" }),
    }),
  );
}

const hasInstanceLock = enforceSingleInstance(app, () => mainWindow);

if (hasInstanceLock) {
  app.whenReady().then(async () => {
    const localAppData = process.env.LOCALAPPDATA;
    if (typeof localAppData !== "string" || !path.isAbsolute(localAppData)) {
      dialog.showErrorBox(
        "OmniBase data directory unavailable",
        "desktop_local_app_data_unavailable",
      );
      app.quit();
      return;
    }
    runtimeManager = new RuntimeManager({
      runtimeRoot: path.join(process.resourcesPath, "runtime"),
      expectedManifestSha256: PINNED_RUNTIME_MANIFEST_SHA256,
      uiOrigin: DESKTOP_UI_ORIGIN,
      dataRoot: path.join(localAppData, "OmniBase"),
      hostEnvironment: process.env,
      secretVault: safeStorage,
    });
    installFailClosedPermissionPolicy(session.defaultSession);
    registerClosedIpcHandlers(ipcMain, {
      getVersion: () => app.getVersion(),
      getRuntimeStatus: () =>
        runtimeManager?.getStatus() ?? {
          phase: "failed",
          attempts: 0,
          lastError: "runtime_not_initialized",
        },
      retryRuntimeStartup: () => {
        if (runtimeManager === null) {
          return Promise.resolve({
            phase: "failed",
            attempts: 0,
            lastError: "runtime_not_initialized",
          });
        }
        return runtimeManager.start();
      },
      getOwnerStatus: () =>
        runtimeManager?.getOwnerStatus() ?? runtimeUnavailable(),
      bootstrapOwner: (input) =>
        runtimeManager?.bootstrapOwner(input) ?? runtimeUnavailable(),
      listWorkspaces: () =>
        runtimeManager?.listWorkspaces() ?? runtimeUnavailable(),
      createWorkspace: (input) =>
        runtimeManager?.createWorkspace(input) ?? runtimeUnavailable(),
      archiveWorkspace: (input) =>
        runtimeManager?.archiveWorkspace(input) ?? runtimeUnavailable(),
      getWorkspaceAgent: (input) =>
        runtimeManager?.getWorkspaceAgent(input) ?? runtimeUnavailable(),
      listProviders: () =>
        runtimeManager?.listProviders() ?? runtimeUnavailable(),
      upsertProvider: (input) =>
        runtimeManager?.upsertProvider(input) ?? runtimeUnavailable(),
      deleteProvider: (input) =>
        runtimeManager?.deleteProvider(input) ?? runtimeUnavailable(),
      testProvider: (input) =>
        runtimeManager?.testProvider(input) ?? runtimeUnavailable(),
      listConversations: (input) =>
        runtimeManager?.listConversations(input) ?? runtimeUnavailable(),
      createConversation: (input) =>
        runtimeManager?.createConversation(input) ?? runtimeUnavailable(),
      archiveConversation: (input) =>
        runtimeManager?.archiveConversation(input) ?? runtimeUnavailable(),
      getConversation: (input) =>
        runtimeManager?.getConversation(input) ?? runtimeUnavailable(),
      sendConversation: (input, emit) =>
        runtimeManager?.sendConversation(input, emit) ?? runtimeUnavailable(),
      cancelConversation: (input) =>
        runtimeManager?.cancelConversation(input) ?? runtimeUnavailable(),
    });

    const runtimeStatus = await runtimeManager.start();
    if (runtimeStatus.phase !== "ready") {
      dialog.showErrorBox(
        "OmniBase runtime unavailable",
        runtimeStatus.lastError ?? "runtime_start_failed",
      );
      app.quit();
      return;
    }

    mainWindow = new BrowserWindow(
      buildSecureWindowOptions(path.join(__dirname, "preload.js")),
    );
    installNavigationPolicy(mainWindow.webContents);
    mainWindow.once("ready-to-show", () => mainWindow?.show());
    mainWindow.on("closed", () => {
      mainWindow = null;
    });
    await mainWindow.loadURL(DESKTOP_UI_ORIGIN);
  });

  app.on("window-all-closed", () => {
    runtimeManager?.stop();
    app.quit();
  });

  app.on("before-quit", () => {
    runtimeManager?.stop();
  });
}
