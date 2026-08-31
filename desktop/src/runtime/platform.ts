import path from "node:path";

/** Resolve the per-user desktop data directory without importing platform paths into callers. */
export function resolveDesktopDataRoot(
  platform: NodeJS.Platform,
  environment: Readonly<Record<string, string | undefined>>,
  userDataPath?: string,
): string {
  if (platform === "win32") {
    const localAppData = environment.LOCALAPPDATA;
    if (typeof localAppData !== "string" || !path.isAbsolute(localAppData)) {
      throw new Error("desktop_local_app_data_unavailable");
    }
    return path.join(localAppData, "OmniBase");
  }
  if (platform === "linux") {
    const xdgDataHome = environment.XDG_DATA_HOME;
    if (typeof xdgDataHome === "string" && path.isAbsolute(xdgDataHome)) {
      return path.join(xdgDataHome, "OmniBase");
    }
    const home = environment.HOME;
    if (typeof home === "string" && path.isAbsolute(home)) {
      return path.join(home, ".local", "share", "OmniBase");
    }
    throw new Error("desktop_linux_data_home_unavailable");
  }
  if (userDataPath !== undefined && path.isAbsolute(userDataPath)) {
    return path.join(userDataPath, "data");
  }
  throw new Error("desktop_data_root_platform_unsupported");
}
