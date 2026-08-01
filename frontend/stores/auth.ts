'use client'

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { TenantPublic, UserPublic } from '@/lib/types'

/**
 * Auth state with localStorage persistence.
 *
 * Tokens are stored separately (lib/tokens.ts) because they need
 * SSR-safe access patterns (never read during SSR).
 * User + tenant info is persisted here for fast page hydration.
 */

interface AuthState {
  user: UserPublic | null
  tenant: TenantPublic | null
  isAuthenticated: boolean
  bootstrapStatus: 'pending' | 'ready' | 'unavailable'

  // Actions
  setSession: (payload: { user: UserPublic; tenant: TenantPublic }) => void
  syncUser: (user: UserPublic) => void
  clearSession: () => void
  setBootstrapStatus: (status: AuthState['bootstrapStatus']) => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      tenant: null,
      isAuthenticated: false,
      bootstrapStatus: 'pending',

      setSession: ({ user, tenant }) =>
        set({
          user,
          tenant,
          isAuthenticated: true,
          bootstrapStatus: 'ready',
        }),

      syncUser: (user) =>
        set({
          user,
          isAuthenticated: true,
        }),

      clearSession: () =>
        set({
          user: null,
          tenant: null,
          isAuthenticated: false,
          bootstrapStatus: 'ready',
        }),

      setBootstrapStatus: (bootstrapStatus) => set({ bootstrapStatus }),
    }),
    {
      name: 'omnibase.auth',
      // Only persist user + tenant + isAuthenticated; tokens handled by lib/tokens.ts
      partialize: (state) => ({
        user: state.user,
        tenant: state.tenant,
        isAuthenticated: state.isAuthenticated,
      }),
    },
  ),
)
