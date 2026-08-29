import path from "node:path";
import { app, BrowserWindow, dialog, ipcMain, safeStorage, session } from "electron";
import type { OpenDialogOptions } from "electron";

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
import { WorkspaceFiles } from "./runtime/workspace-files.ts";
import type { DesktopOperationResult } from "./shared/ipc-contract.ts";

let mainWindow: BrowserWindow | null = null;
let runtimeManager: RuntimeManager | null = null;
let workspaceFiles: WorkspaceFiles | null = null;

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
    const workspaceFileService = new WorkspaceFiles({
      getWorkspaceAgent: (input) =>
        runtimeManager?.getWorkspaceAgent(input) ?? runtimeUnavailable(),
      chooseDirectory: async () => {
        const options: OpenDialogOptions = {
          title: "Open local project",
          buttonLabel: "Open Project",
          properties: ["openDirectory", "dontAddToRecent"],
        };
        const result =
          mainWindow === null
            ? await dialog.showOpenDialog(options)
            : await dialog.showOpenDialog(mainWindow, options);
        return result.canceled || result.filePaths.length !== 1
          ? null
          : (result.filePaths[0] ?? null);
      },
    });
    workspaceFiles = workspaceFileService;
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
      authorizeWorkspaceFiles: (input) => workspaceFileService.authorize(input),
      releaseWorkspaceFiles: (input) => workspaceFileService.release(input),
      listWorkspaceFiles: (input) => workspaceFileService.list(input),
      readWorkspaceFile: (input) => workspaceFileService.read(input),
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
      abortInFlightSend: () =>
        runtimeManager?.abortInFlightSend() ?? runtimeUnavailable(),
      listAgentRoles: (input) =>
        runtimeManager?.listAgentRoles(input) ?? runtimeUnavailable(),
      getAgentRole: (input) =>
        runtimeManager?.getAgentRole(input) ?? runtimeUnavailable(),
      updateAgentRole: (input) =>
        runtimeManager?.updateAgentRole(input) ?? runtimeUnavailable(),
      testAgentRole: (input) =>
        runtimeManager?.testAgentRole(input) ?? runtimeUnavailable(),
      startTeamRun: (input, emit) =>
        runtimeManager?.startTeamRun(input, emit) ?? runtimeUnavailable(),
      cancelTeamRun: (input, emit) =>
        runtimeManager?.cancelTeamRun(input, emit) ?? runtimeUnavailable(),
      getTeamRun: (input) =>
        runtimeManager?.getTeamRun(input) ?? runtimeUnavailable(),
      listTeamRuns: (input) =>
        runtimeManager?.listTeamRuns(input) ?? runtimeUnavailable(),
      submitTeamProposal: (input, emit) =>
        runtimeManager?.submitTeamProposal(input, emit) ?? runtimeUnavailable(),
      getTeamBlackboard: (input) =>
        runtimeManager?.getTeamBlackboard(input) ?? runtimeUnavailable(),
      recordTeamCollaboration: (input, emit) =>
        runtimeManager?.recordTeamCollaboration(input, emit) ??
        runtimeUnavailable(),
      executeTeamRun: (input, emit) =>
        runtimeManager?.executeTeamRun(input, emit) ?? runtimeUnavailable(),
      appendTeamRunBudget: (input, emit) =>
        runtimeManager?.appendTeamRunBudget(input, emit) ?? runtimeUnavailable(),
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
    mainWindow.webContents.on(
      "did-start-navigation",
      (_event, _url, isInPlace, isMainFrame) => {
        if (isMainFrame && !isInPlace) workspaceFileService.invalidate();
      },
    );
    mainWindow.webContents.on("render-process-gone", () => workspaceFileService.invalidate());
    installNavigationPolicy(mainWindow.webContents);
    mainWindow.once("ready-to-show", () => mainWindow?.show());
    mainWindow.on("closed", () => {
      workspaceFileService.invalidate();
      mainWindow = null;
    });
    await mainWindow.loadURL(DESKTOP_UI_ORIGIN);
  });

  app.on("window-all-closed", () => {
    runtimeManager?.stop();
    app.quit();
  });

  app.on("before-quit", () => {
    workspaceFiles?.invalidate();
    runtimeManager?.stop();
  });
}
