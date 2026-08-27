import type {
  BrowserWindowConstructorOptions,
  HandlerDetails,
  Session,
  WebContents,
} from "electron";

import { isAllowedDesktopUrl } from "./origin-policy.ts";

export function buildSecureWindowOptions(
  preloadPath: string,
): BrowserWindowConstructorOptions {
  return {
    width: 1440,
    height: 960,
    minWidth: 1024,
    minHeight: 700,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: "#111111",
    webPreferences: {
      preload: preloadPath,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      nodeIntegrationInWorker: false,
      nodeIntegrationInSubFrames: false,
      webviewTag: false,
      allowRunningInsecureContent: false,
      devTools: false,
      spellcheck: true,
    },
  };
}

export interface NavigationWebContents {
  on(
    event: "will-navigate",
    listener: (event: { preventDefault(): void }, url: string) => void,
  ): void;
  setWindowOpenHandler(
    handler: (details: HandlerDetails) => { action: "deny" },
  ): void;
}

export function installNavigationPolicy(
  contents: NavigationWebContents | WebContents,
  allowedOrigin?: string,
): void {
  contents.on("will-navigate", (event: { preventDefault(): void }, url: string) => {
    if (!isAllowedDesktopUrl(url, allowedOrigin)) {
      event.preventDefault();
    }
  });
  contents.setWindowOpenHandler(() => ({ action: "deny" }));
}

export interface PermissionSession {
  setPermissionRequestHandler(
    handler: (
      webContents: WebContents,
      permission: string,
      callback: (permissionGranted: boolean) => void,
      details: object,
    ) => void,
  ): void;
  setPermissionCheckHandler(
    handler: (
      webContents: WebContents | null,
      permission: string,
      requestingOrigin: string,
      details: object,
    ) => boolean,
  ): void;
}

export function installFailClosedPermissionPolicy(
  targetSession: PermissionSession | Session,
): void {
  targetSession.setPermissionRequestHandler(
    (
      _webContents: WebContents,
      _permission: string,
      callback: (permissionGranted: boolean) => void,
    ) => callback(false),
  );
  targetSession.setPermissionCheckHandler(() => false);
}
