'use client'

import axios from 'axios'
import { authApi } from '@/lib/api'
import { classifyAuthFailure, invalidateAuthSession } from '@/lib/auth-session'
import { getAccessToken, getRefreshToken } from '@/lib/tokens'
import { useAuthStore } from '@/stores/auth'

let bootstrapPromise: Promise<void> | null = null

async function runAuthBootstrap(): Promise<void> {
  const store = useAuthStore.getState()

  if (!getAccessToken() && !getRefreshToken()) {
    store.clearSession()
    store.setBootstrapStatus('ready')
    return
  }

  try {
    const user = await authApi.me()
    useAuthStore.getState().syncUser(user)
    useAuthStore.getState().setBootstrapStatus('ready')
  } catch (error) {
    const status = axios.isAxiosError(error) ? error.response?.status : undefined
    if (classifyAuthFailure(status) === 'invalid') {
      invalidateAuthSession()
      useAuthStore.getState().setBootstrapStatus('ready')
      return
    }

    // Preserve tokens and the persisted session on network/5xx failures. The
    // unavailable state prevents route guards from treating this as anonymous.
    useAuthStore.getState().setBootstrapStatus('unavailable')
    throw error
  }
}

/** Validate the persisted auth session once; transient failures may be retried. */
export function bootstrapAuth(): Promise<void> {
  if (!bootstrapPromise) {
    bootstrapPromise = runAuthBootstrap().catch((error) => {
      bootstrapPromise = null
      throw error
    })
  }
  return bootstrapPromise
}
