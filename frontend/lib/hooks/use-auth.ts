'use client'

import { useCallback } from 'react'
import { authApi } from '@/lib/api'
import { invalidateAuthSession } from '@/lib/auth-session'
import { setTokens } from '@/lib/tokens'
import { useAuthStore } from '@/stores/auth'

/** Auth state and explicit auth actions. Global bootstrap lives in AuthBootstrap. */
export function useAuth() {
  const user = useAuthStore((state) => state.user)
  const tenant = useAuthStore((state) => state.tenant)
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)
  const bootstrapStatus = useAuthStore((state) => state.bootstrapStatus)
  const setSession = useAuthStore((state) => state.setSession)

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await authApi.login({ email, password })
      const expiresAt = Date.now() + result.expires_in * 1000
      setTokens(result.access_token, result.refresh_token, expiresAt)
      setSession({ user: result.user, tenant: result.tenant })
      return result
    },
    [setSession],
  )

  const register = useCallback(
    async (email: string, password: string, tenantName?: string) => {
      const result = await authApi.register({ email, password, tenant_name: tenantName })
      const expiresAt = Date.now() + result.expires_in * 1000
      setTokens(result.access_token, result.refresh_token, expiresAt)
      setSession({ user: result.user, tenant: result.tenant })
      return result
    },
    [setSession],
  )

  const logout = useCallback(() => {
    invalidateAuthSession()
  }, [])

  return {
    user,
    tenant,
    isAuthenticated,
    bootstrapStatus,
    isLoading: bootstrapStatus === 'pending',
    login,
    register,
    logout,
  }
}
