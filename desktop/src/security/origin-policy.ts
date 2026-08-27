export const DESKTOP_UI_ORIGIN = "http://127.0.0.1:3000";

export function isAllowedDesktopUrl(
  candidate: string,
  allowedOrigin = DESKTOP_UI_ORIGIN,
): boolean {
  try {
    const parsed = new URL(candidate);
    const allowed = new URL(allowedOrigin);
    return (
      parsed.origin === allowed.origin &&
      parsed.protocol === "http:" &&
      parsed.hostname === "127.0.0.1" &&
      parsed.username === "" &&
      parsed.password === ""
    );
  } catch {
    return false;
  }
}

export function isAllowedIpcSender(
  senderUrl: string,
  allowedOrigin = DESKTOP_UI_ORIGIN,
): boolean {
  return isAllowedDesktopUrl(senderUrl, allowedOrigin);
}
