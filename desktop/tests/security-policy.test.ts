import assert from "node:assert/strict";
import test from "node:test";

import { enforceSingleInstance } from "../src/app-lifecycle.ts";
import {
  DESKTOP_UI_ORIGIN,
  isAllowedDesktopUrl,
} from "../src/security/origin-policy.ts";
import {
  buildSecureWindowOptions,
  installFailClosedPermissionPolicy,
  installNavigationPolicy,
  type NavigationWebContents,
  type PermissionSession,
} from "../src/security/window-policy.ts";

test("desktop URL policy accepts only the fixed loopback origin", () => {
  assert.equal(isAllowedDesktopUrl(`${DESKTOP_UI_ORIGIN}/dashboard?tab=runs#latest`), true);
  assert.equal(isAllowedDesktopUrl("http://localhost:3000/dashboard"), false);
  assert.equal(isAllowedDesktopUrl("http://127.0.0.1:3001/dashboard"), false);
  assert.equal(isAllowedDesktopUrl("https://127.0.0.1:3000/dashboard"), false);
  assert.equal(isAllowedDesktopUrl("file:///C:/OmniBase/index.html"), false);
  assert.equal(isAllowedDesktopUrl("javascript:alert(1)"), false);
  assert.equal(isAllowedDesktopUrl("http://user@127.0.0.1:3000/"), false);
});

test("BrowserWindow options retain the required Electron isolation settings", () => {
  const options = buildSecureWindowOptions("C:\\OmniBase\\preload.js");
  assert.equal(options.webPreferences?.contextIsolation, true);
  assert.equal(options.webPreferences?.sandbox, true);
  assert.equal(options.webPreferences?.nodeIntegration, false);
  assert.equal(options.webPreferences?.nodeIntegrationInWorker, false);
  assert.equal(options.webPreferences?.nodeIntegrationInSubFrames, false);
  assert.equal(options.webPreferences?.webviewTag, false);
  assert.equal(options.webPreferences?.allowRunningInsecureContent, false);
  assert.equal(options.webPreferences?.devTools, false);
  assert.equal(options.webPreferences?.preload, "C:\\OmniBase\\preload.js");
});

test("navigation, popups, and every renderer permission fail closed", () => {
  let navigate:
    | ((event: { preventDefault(): void }, url: string) => void)
    | undefined;
  let popupHandler: (() => { action: "deny" }) | undefined;
  const contents = {
    on: (_event, listener) => {
      navigate = listener;
    },
    setWindowOpenHandler: (handler) => {
      popupHandler = handler as () => { action: "deny" };
    },
  } satisfies NavigationWebContents;
  installNavigationPolicy(contents);

  let prevented = false;
  navigate?.({ preventDefault: () => (prevented = true) }, `${DESKTOP_UI_ORIGIN}/login`);
  assert.equal(prevented, false);
  navigate?.({ preventDefault: () => (prevented = true) }, "https://example.com/");
  assert.equal(prevented, true);
  assert.deepEqual(popupHandler?.(), { action: "deny" });

  let requestHandler:
    | ((_contents: never, _permission: string, callback: (granted: boolean) => void) => void)
    | undefined;
  let checkHandler: (() => boolean) | undefined;
  const permissionSession = {
    setPermissionRequestHandler: (handler) => {
      requestHandler = handler as unknown as typeof requestHandler;
    },
    setPermissionCheckHandler: (handler) => {
      checkHandler = handler as () => boolean;
    },
  } satisfies PermissionSession;
  installFailClosedPermissionPolicy(permissionSession);
  let granted: boolean | undefined;
  requestHandler?.(undefined as never, "clipboard-read", (value) => (granted = value));
  assert.equal(granted, false);
  assert.equal(checkHandler?.(), false);
});

test("single-instance policy quits a duplicate and focuses the accepted instance", () => {
  let quitCount = 0;
  let secondInstanceListener: (() => void) | undefined;
  const rejected = enforceSingleInstance(
    {
      requestSingleInstanceLock: () => false,
      quit: () => {
        quitCount += 1;
      },
      on: () => assert.fail("duplicate instance must not install a listener"),
    },
    () => null,
  );
  assert.equal(rejected, false);
  assert.equal(quitCount, 1);

  let restoreCount = 0;
  let focusCount = 0;
  const accepted = enforceSingleInstance(
    {
      requestSingleInstanceLock: () => true,
      quit: () => assert.fail("primary instance must not quit"),
      on: (_event, listener) => {
        secondInstanceListener = listener;
      },
    },
    () => ({
      isMinimized: () => true,
      restore: () => {
        restoreCount += 1;
      },
      focus: () => {
        focusCount += 1;
      },
    }),
  );
  assert.equal(accepted, true);
  secondInstanceListener?.();
  assert.equal(restoreCount, 1);
  assert.equal(focusCount, 1);
});
