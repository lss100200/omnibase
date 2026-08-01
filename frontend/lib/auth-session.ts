'use client'

import { clearTokens } from '@/lib/tokens'
import { useAuthStore } from '@/stores/auth'

export type AuthFailureKind = 'invalid' | 'transient'

export function classifyAuthFailure(status: number | undefined): AuthFailureKind {
  if (status === 400 || status === 401 || status === 403) return 'invalid'
  return 'transient'
}

export function getSafeReturnPath(
  value: string | null | undefined,
  fallback = '/dashboard',
): string {
  if (!value || !value.startsWith('/') || value.startsWith('//')) return fallback

  try {
    const url = new URL(value, 'http://localhost')
    if (url.origin !== 'http://localhost') return fallback
    return `${url.pathname}${url.search}${url.hash}`
  } catch {
    return fallback
  }
}

export function getCurrentReturnPath(): string {
  if (typeof window === 'undefined') return '/dashboard'
  return getSafeReturnPath(
    `${window.location.pathname}${window.location.search}${window.location.hash}`,
  )
}

export function invalidateAuthSession(): void {
  clearTokens()
  useAuthStore.getState().clearSession()
}

export function redirectToLogin(returnPath = getCurrentReturnPath()): void {
  if (typeof window === 'undefined') return

  const currentPath = window.location.pathname
  if (currentPath.startsWith('/login') || currentPath.startsWith('/register')) return

  window.location.assign(`/login?from=${encodeURIComponent(getSafeReturnPath(returnPath))}`)
}
