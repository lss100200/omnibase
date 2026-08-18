import path from "node:path";
import { app, BrowserWindow, dialog, ipcMain, session } from "electron";

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

let mainWindow: BrowserWindow | null = null;
let runtimeManager: RuntimeManager | null = null;

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
      dataRoot: path.join(localAppData, "OmniBase", "data"),
      hostEnvironment: process.env,
    });
    installFailClosedPermissionPolicy(session.defaultSession);
    registerClosedIpcHandlers(ipcMain, {
      getVersion: () => app.getVersion(),
      getRuntimeStatus: () => runtimeManager?.getStatus() ?? {
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
