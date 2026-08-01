/**
 * Token storage with SSR safety.
 *
 * - On the server (no window): always returns null (no SSRF risk)
 * - On the client: persists to localStorage + keeps an in-memory mirror
 *   so reads don't pay the localStorage deserialization cost on every render
 *
 * The "expire at" timestamp is stored alongside the token so we can
 * pre-emptively refresh before the API returns 401 (improves UX).
 */

const ACCESS_TOKEN_KEY = 'omnibase.access_token'
const REFRESH_TOKEN_KEY = 'omnibase.refresh_token'
const EXPIRES_AT_KEY = 'omnibase.token_expires_at'

// In-memory mirror (avoid repeated localStorage hits)
let cachedAccessToken: string | null = null
let cachedRefreshToken: string | null = null
let cachedExpiresAt: number | null = null

/**
 * True only when running in the browser (where localStorage exists).
 * During SSR / SSG this returns false and all read operations return null.
 */
function isBrowser(): boolean {
  return typeof window !== 'undefined' && typeof window.localStorage !== 'undefined'
}

export function getAccessToken(): string | null {
  if (!isBrowser()) return null
  if (cachedAccessToken !== null) return cachedAccessToken
  cachedAccessToken = window.localStorage.getItem(ACCESS_TOKEN_KEY)
  return cachedAccessToken
}

export function getRefreshToken(): string | null {
  if (!isBrowser()) return null
  if (cachedRefreshToken !== null) return cachedRefreshToken
  cachedRefreshToken = window.localStorage.getItem(REFRESH_TOKEN_KEY)
  return cachedRefreshToken
}

export function getTokenExpiresAt(): number | null {
  if (!isBrowser()) return null
  if (cachedExpiresAt !== null) return cachedExpiresAt
  const raw = window.localStorage.getItem(EXPIRES_AT_KEY)
  cachedExpiresAt = raw ? Number(raw) : null
  return cachedExpiresAt
}

/**
 * Persist tokens to localStorage + update the in-memory mirror.
 * expiresAt is a Unix epoch in milliseconds.
 */
export function setTokens(accessToken: string, refreshToken: string, expiresAt: number): void {
  if (!isBrowser()) return
  cachedAccessToken = accessToken
  cachedRefreshToken = refreshToken
  cachedExpiresAt = expiresAt
  window.localStorage.setItem(ACCESS_TOKEN_KEY, accessToken)
  window.localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken)
  window.localStorage.setItem(EXPIRES_AT_KEY, String(expiresAt))
}

/**
 * Wipe all stored tokens (logout).
 */
export function clearTokens(): void {
  if (!isBrowser()) return
  cachedAccessToken = null
  cachedRefreshToken = null
  cachedExpiresAt = null
  window.localStorage.removeItem(ACCESS_TOKEN_KEY)
  window.localStorage.removeItem(REFRESH_TOKEN_KEY)
  window.localStorage.removeItem(EXPIRES_AT_KEY)
}

/**
 * Check whether the access token is close to expiring (within `thresholdMs`).
 * Returns true if no token exists or it's about to expire.
 */
export function isTokenExpiringSoon(thresholdMs = 60_000): boolean {
  const expiresAt = getTokenExpiresAt()
  if (!expiresAt) return true
  return Date.now() + thresholdMs >= expiresAt
}
