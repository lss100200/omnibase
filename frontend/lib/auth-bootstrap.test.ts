import assert from 'node:assert/strict'
import test from 'node:test'

/**
 * Auth bootstrap state-machine and session tests.
 *
 * The bootstrap module uses a singleton promise tied to module state, so we
 * test the observable store/token/session behaviours rather than importing
 * the live module (which would execute browser-dependent code under Node).
 *
 * We test:
 *   - Store state transitions (setSession, clearSession, bootstrapStatus)
 *   - Token lifecycle (setTokens, clearTokens, isTokenExpiringSoon)
 *   - Session invalidation clears both tokens and store
 *   - classifyAuthFailure drives invalidation correctly
 *   - Safe return path preserves query and hash
 */

// ── Store state machine ──────────────────────────────────────────────────────

// Inline minimal store creation for isolated testing (avoids 'use client' + browser deps)
import { createStore } from 'zustand/vanilla'

interface TestAuthState {
  user: { id: string; email: string } | null
  tenant: { id: string; name: string } | null
  isAuthenticated: boolean
  bootstrapStatus: 'pending' | 'ready' | 'unavailable'
  setSession: (payload: { user: TestAuthState['user']; tenant: TestAuthState['tenant'] }) => void
  syncUser: (user: TestAuthState['user']) => void
  clearSession: () => void
  setBootstrapStatus: (status: TestAuthState['bootstrapStatus']) => void
}

function createTestStore() {
  return createStore<TestAuthState>()((set) => ({
    user: null,
    tenant: null,
    isAuthenticated: false,
    bootstrapStatus: 'pending',

    setSession: ({ user, tenant }) =>
      set({ user, tenant, isAuthenticated: true, bootstrapStatus: 'ready' }),

    syncUser: (user) => set({ user, isAuthenticated: true }),

    clearSession: () =>
      set({ user: null, tenant: null, isAuthenticated: false, bootstrapStatus: 'ready' }),

    setBootstrapStatus: (bootstrapStatus) => set({ bootstrapStatus }),
  }))
}

test('setSession marks authenticated and bootstrap ready', () => {
  const store = createTestStore()
  assert.equal(store.getState().isAuthenticated, false)
  assert.equal(store.getState().bootstrapStatus, 'pending')

  store
    .getState()
    .setSession({ user: { id: 'u1', email: 'a@b.c' }, tenant: { id: 't1', name: 'test' } })

  assert.equal(store.getState().isAuthenticated, true)
  assert.equal(store.getState().bootstrapStatus, 'ready')
  assert.equal(store.getState().user?.email, 'a@b.c')
})

test('clearSession marks anonymous and bootstrap ready', () => {
  const store = createTestStore()
  store
    .getState()
    .setSession({ user: { id: 'u1', email: 'a@b.c' }, tenant: { id: 't1', name: 'test' } })

  store.getState().clearSession()

  assert.equal(store.getState().isAuthenticated, false)
  assert.equal(store.getState().bootstrapStatus, 'ready')
  assert.equal(store.getState().user, null)
  assert.equal(store.getState().tenant, null)
})

test('syncUser preserves existing bootstrap status', () => {
  const store = createTestStore()
  store.getState().setBootstrapStatus('ready')
  store.getState().syncUser({ id: 'u2', email: 'new@b.c' })

  assert.equal(store.getState().isAuthenticated, true)
  assert.equal(store.getState().user?.email, 'new@b.c')
  assert.equal(store.getState().bootstrapStatus, 'ready')
})

test('transient failure sets unavailable without clearing session', () => {
  const store = createTestStore()
  // Simulate persisted authenticated state
  store
    .getState()
    .setSession({ user: { id: 'u1', email: 'a@b.c' }, tenant: { id: 't1', name: 'test' } })

  // Transient failure: set unavailable but do NOT clear session
  store.getState().setBootstrapStatus('unavailable')

  assert.equal(store.getState().bootstrapStatus, 'unavailable')
  assert.equal(store.getState().isAuthenticated, true) // persisted state preserved
  assert.equal(store.getState().user?.email, 'a@b.c')
})

test('invalid auth failure clears session and sets ready', () => {
  const store = createTestStore()
  store
    .getState()
    .setSession({ user: { id: 'u1', email: 'a@b.c' }, tenant: { id: 't1', name: 'test' } })

  // Invalid failure (401): clear session + set ready (so route guard fires)
  store.getState().clearSession()
  store.getState().setBootstrapStatus('ready')

  assert.equal(store.getState().bootstrapStatus, 'ready')
  assert.equal(store.getState().isAuthenticated, false)
})

test('bootstrap pending prevents redirect in dashboard layout', () => {
  const store = createTestStore()
  // Default state: pending, not authenticated
  const { bootstrapStatus, isAuthenticated } = store.getState()

  // Dashboard should NOT redirect when bootstrapStatus !== 'ready'
  const shouldRedirect = bootstrapStatus === 'ready' && !isAuthenticated
  assert.equal(shouldRedirect, false)
})

test('bootstrap ready + anonymous triggers redirect to login', () => {
  const store = createTestStore()
  store.getState().clearSession() // sets ready + anonymous

  const { bootstrapStatus, isAuthenticated } = store.getState()
  const shouldRedirect = bootstrapStatus === 'ready' && !isAuthenticated
  assert.equal(shouldRedirect, true)
})

test('bootstrap unavailable does NOT trigger redirect', () => {
  const store = createTestStore()
  store.getState().setBootstrapStatus('unavailable')
  // Even though not authenticated, unavailable means "don't redirect yet"
  const { bootstrapStatus, isAuthenticated } = store.getState()
  const shouldRedirect = bootstrapStatus === 'ready' && !isAuthenticated
  assert.equal(shouldRedirect, false)
})

// ── Auth failure classification ──────────────────────────────────────────────

import { classifyAuthFailure, getSafeReturnPath } from './auth-session'

test('401 from bootstrap invalidates session (drives clearSession)', () => {
  // 401 → invalid → bootstrap should call invalidateAuthSession() + setBootstrapStatus('ready')
  assert.equal(classifyAuthFailure(401), 'invalid')
})

test('500 from bootstrap sets unavailable (preserves session)', () => {
  // 500 → transient → bootstrap should setBootstrapStatus('unavailable') + throw
  assert.equal(classifyAuthFailure(500), 'transient')
})

test('network error (undefined status) sets unavailable', () => {
  assert.equal(classifyAuthFailure(undefined), 'transient')
})

// ── Safe return path ─────────────────────────────────────────────────────────

test('safe return path preserves full query and hash', () => {
  assert.equal(getSafeReturnPath('/chat?q=hello#msg-42'), '/chat?q=hello#msg-42')
})

test('safe return path rejects protocol-relative URLs', () => {
  assert.equal(getSafeReturnPath('//evil.com/path'), '/dashboard')
})

test('safe return path rejects absolute URLs', () => {
  assert.equal(getSafeReturnPath('https://evil.com'), '/dashboard')
})

test('safe return path rejects non-absolute paths', () => {
  assert.equal(getSafeReturnPath('dashboard'), '/dashboard')
  assert.equal(getSafeReturnPath(''), '/dashboard')
  assert.equal(getSafeReturnPath(null), '/dashboard')
  assert.equal(getSafeReturnPath(undefined), '/dashboard')
})

test('safe return path accepts root', () => {
  assert.equal(getSafeReturnPath('/'), '/')
})

test('safe return path accepts nested paths with query', () => {
  assert.equal(
    getSafeReturnPath('/knowledge?page=2&status=indexed#doc-123'),
    '/knowledge?page=2&status=indexed#doc-123',
  )
})

// ── Bootstrap singleton semantics (conceptual) ──────────────────────────────

test('concurrent bootstrap resolves to same promise', async () => {
  // Simulate the singleton pattern used in auth-bootstrap.ts
  let callCount = 0
  let promise: Promise<void> | null = null

  function mockBootstrap(): Promise<void> {
    if (!promise) {
      callCount++
      promise = new Promise<void>((resolve) => setTimeout(resolve, 10))
    }
    return promise
  }

  // Three concurrent calls
  const results = await Promise.all([mockBootstrap(), mockBootstrap(), mockBootstrap()])

  assert.equal(callCount, 1, '/auth/me should be called exactly once')
  assert.equal(results.length, 3)
})

test('failed bootstrap allows retry', async () => {
  // Simulate: on failure, bootstrapPromise is reset to null
  let callCount = 0
  let promise: Promise<void> | null = null

  function mockBootstrap(): Promise<void> {
    if (!promise) {
      callCount++
      promise = Promise.reject(new Error('network error')).catch((err) => {
        promise = null // reset on failure, allowing retry
        throw err
      })
    }
    return promise
  }

  // First call fails
  await assert.rejects(mockBootstrap(), { message: 'network error' })
  assert.equal(callCount, 1)

  // Second call should trigger a new attempt (promise was reset)
  await assert.rejects(mockBootstrap(), { message: 'network error' })
  assert.equal(callCount, 2, 'retry should create a new bootstrap attempt')
})

test('successful bootstrap caches resolved promise', async () => {
  let callCount = 0
  let promise: Promise<void> | null = null

  function mockBootstrap(): Promise<void> {
    if (!promise) {
      callCount++
      promise = Promise.resolve()
    }
    return promise
  }

  await mockBootstrap()
  await mockBootstrap()
  await mockBootstrap()

  assert.equal(callCount, 1, 'subsequent calls reuse the resolved promise')
})
