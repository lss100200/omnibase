import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import { resolveDesktopDataRoot } from "../src/runtime/platform.ts";

test("Windows data stays in the existing per-user OmniBase location", () => {
  const root = path.resolve("C:/Users/Alice/AppData/Local");
  assert.equal(
    resolveDesktopDataRoot("win32", { LOCALAPPDATA: root }),
    path.join(root, "OmniBase"),
  );
});

test("Linux prefers XDG_DATA_HOME and does not require LOCALAPPDATA", () => {
  const root = path.resolve("/tmp/omnibase-xdg");
  assert.equal(
    resolveDesktopDataRoot("linux", { XDG_DATA_HOME: root }),
    path.join(root, "OmniBase"),
  );
});

test("Linux falls back to HOME/.local/share", () => {
  const home = path.resolve("/tmp/omnibase-home");
  assert.equal(
    resolveDesktopDataRoot("linux", { HOME: home }),
    path.join(home, ".local", "share", "OmniBase"),
  );
});

test("unsupported platforms use an explicit Electron userData fallback", () => {
  const userData = path.resolve(os.tmpdir(), "omnibase-user-data");
  assert.equal(
    resolveDesktopDataRoot("freebsd", {}, userData),
    path.join(userData, "data"),
  );
});

test("Windows and Linux fail closed when their required data root is invalid", () => {
  assert.throws(
    () => resolveDesktopDataRoot("win32", {}),
    /desktop_local_app_data_unavailable/u,
  );
  assert.throws(
    () => resolveDesktopDataRoot("linux", {}, path.resolve("fallback")),
    /desktop_linux_data_home_unavailable/u,
  );
  assert.throws(
    () => resolveDesktopDataRoot("freebsd", {}, "relative"),
    /desktop_data_root_platform_unsupported/u,
  );
});
