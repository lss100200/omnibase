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

const hasInstanceLock = enforceSingleInstance(app, () => mainWindow);

if (hasInstanceLock) {
  const runtimeManager = new RuntimeManager({
    runtimeRoot: path.join(process.resourcesPath, "runtime"),
    expectedManifestSha256: PINNED_RUNTIME_MANIFEST_SHA256,
    uiOrigin: DESKTOP_UI_ORIGIN,
  });

  app.whenReady().then(async () => {
    installFailClosedPermissionPolicy(session.defaultSession);
    registerClosedIpcHandlers(ipcMain, {
      getVersion: () => app.getVersion(),
      getRuntimeStatus: () => runtimeManager.getStatus(),
      retryRuntimeStartup: () => runtimeManager.start(),
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
    runtimeManager.stop();
    app.quit();
  });

  app.on("before-quit", () => {
    runtimeManager.stop();
  });
}
