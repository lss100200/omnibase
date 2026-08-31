import path from "node:path";
import {
  app,
  BrowserWindow,
  dialog,
  ipcMain,
  safeStorage,
  session,
} from "electron";
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
import { ComponentRuntimeBroker } from "./runtime/component-runtime-broker.ts";
import { OwnerComponentPackageStore } from "./runtime/owner-component-package-store.ts";
import { P34SandboxComponentAdapter } from "./runtime/p34-sandbox-adapter.ts";
import { resolveDesktopDataRoot } from "./runtime/platform.ts";
import { PINNED_RUNTIME_MANIFEST_SHA256 } from "./runtime/trusted-manifest.ts";
import { WorkspaceFiles } from "./runtime/workspace-files.ts";
import type { DesktopOperationResult } from "./shared/ipc-contract.ts";

let mainWindow: BrowserWindow | null = null;
let runtimeManager: RuntimeManager | null = null;
let workspaceFiles: WorkspaceFiles | null = null;
let componentRuntimeBroker: ComponentRuntimeBroker | null = null;

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
    let dataRoot: string;
    try {
      dataRoot = resolveDesktopDataRoot(
        process.platform,
        process.env,
        app.getPath("userData"),
      );
    } catch {
      dialog.showErrorBox(
        "OmniBase data directory unavailable",
        "desktop_local_app_data_unavailable",
      );
      app.quit();
      return;
    }
    const runtimeRoot = path.join(process.resourcesPath, "runtime");
    runtimeManager = new RuntimeManager({
      runtimeRoot,
      expectedManifestSha256: PINNED_RUNTIME_MANIFEST_SHA256,
      uiOrigin: DESKTOP_UI_ORIGIN,
      dataRoot,
      hostEnvironment: process.env,
      secretVault: safeStorage,
    });
    const manager = runtimeManager;
    const sandboxAdapter = new P34SandboxComponentAdapter({
      runtimeRoot,
      getVerifiedRuntimeFileSha256: (relativePath) =>
        manager.getVerifiedRuntimeFileSha256(relativePath),
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
    const ownerPackageStore = new OwnerComponentPackageStore({
      dataRoot,
      native: manager,
      choosePackage: async () => {
        const options: OpenDialogOptions = {
          title: "Import reviewed Workspace component",
          buttonLabel: "Review Package",
          filters: [
            { name: "OmniBase component package", extensions: ["json"] },
          ],
          properties: ["openFile", "dontAddToRecent"],
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
    const componentBroker = new ComponentRuntimeBroker({
      native: manager,
      workspaceFiles: workspaceFileService,
      runtimeRoot,
      getVerifiedRuntimeFileSha256: (relativePath) =>
        manager.getVerifiedRuntimeFileSha256(relativePath),
      ownerPackageStore,
      sandboxAdapter,
    });
    componentRuntimeBroker = componentBroker;
    manager.setComponentRecoveryHandler((input) =>
      componentBroker.recoverStartup(input),
    );
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
      getApplicationPreference: () =>
        runtimeManager?.getApplicationPreference() ?? runtimeUnavailable(),
      updateApplicationPreference: (input) =>
        runtimeManager?.updateApplicationPreference(input) ??
        runtimeUnavailable(),
      getWorkspaceComposition: (input) =>
        runtimeManager?.getWorkspaceComposition(input) ?? runtimeUnavailable(),
      proposeWorkspaceComposition: (input) =>
        runtimeManager?.proposeWorkspaceComposition(input) ??
        runtimeUnavailable(),
      proposeWorkspaceCompositionFromAssistant: (input) =>
        runtimeManager?.proposeWorkspaceCompositionFromAssistant(input) ??
        runtimeUnavailable(),
      proposeWorkspaceCompositionRollback: (input) =>
        runtimeManager?.proposeWorkspaceCompositionRollback(input) ??
        runtimeUnavailable(),
      decideWorkspaceComposition: (input) =>
        runtimeManager?.decideWorkspaceComposition(input) ??
        runtimeUnavailable(),
      getWorkspaceComponents: (input) =>
        runtimeManager?.getWorkspaceComponents(input) ?? runtimeUnavailable(),
      proposeWorkspaceComponent: (input) =>
        runtimeManager?.proposeWorkspaceComponent(input) ??
        runtimeUnavailable(),
      proposeWorkspaceComponentFromAssistant: (input) =>
        runtimeManager?.proposeWorkspaceComponentFromAssistant(input) ??
        runtimeUnavailable(),
      importOwnerWorkspaceComponentPackage: (input) =>
        ownerPackageStore.importPackage(input.workspaceId),
      importAssistantWorkspaceComponentPackage: (input) =>
        ownerPackageStore.importAssistantPackage(input),
      decideWorkspaceComponent: (input) =>
        runtimeManager?.decideWorkspaceComponent(input) ?? runtimeUnavailable(),
      applyWorkspaceComponentAction: (input) =>
        componentBroker.applyAction(input),
      invokeWorkspaceComponent: (input) => componentBroker.invoke(input),
      emergencyStopWorkspaceComponents: (input) =>
        componentBroker.emergencyStop(input),
      reconcileWorkspaceComponent: (input) =>
        runtimeManager?.reconcileWorkspaceComponent(input) ??
        runtimeUnavailable(),
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
        runtimeManager?.appendTeamRunBudget(input, emit) ??
        runtimeUnavailable(),
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
        if (isMainFrame && !isInPlace) {
          componentBroker.stopAll();
          workspaceFileService.invalidate();
        }
      },
    );
    mainWindow.webContents.on("render-process-gone", () => {
      componentBroker.stopAll();
      workspaceFileService.invalidate();
    });
    installNavigationPolicy(mainWindow.webContents);
    mainWindow.once("ready-to-show", () => mainWindow?.show());
    mainWindow.on("closed", () => {
      componentBroker.stopAll();
      workspaceFileService.invalidate();
      mainWindow = null;
    });
    await mainWindow.loadURL(DESKTOP_UI_ORIGIN);
  });

  app.on("window-all-closed", () => {
    componentRuntimeBroker?.stopAll();
    runtimeManager?.stop();
    app.quit();
  });

  app.on("before-quit", () => {
    componentRuntimeBroker?.dispose();
    componentRuntimeBroker = null;
    workspaceFiles?.invalidate();
    runtimeManager?.stop();
  });
}
