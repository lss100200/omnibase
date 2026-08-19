import assert from 'node:assert/strict'
import { test } from 'node:test'

import { resolveDesktopBridge } from './desktop-bridge'

function bridgeFixture() {
  return {
    app: { getVersion: async () => '1.0.0' },
    runtime: {
      getStatus: async () => ({ phase: 'ready', attempts: 1, lastError: null }),
      retryStartup: async () => ({ phase: 'ready', attempts: 1, lastError: null }),
    },
    owner: {
      getStatus: async () => ({ ok: true, value: { initialized: false, owner: null } }),
      bootstrap: async () => ({ ok: false, error: { code: 'not-called' } }),
    },
    workspaces: {
      list: async () => ({ ok: true, value: { items: [] } }),
      create: async () => ({ ok: false, error: { code: 'not-called' } }),
      archive: async () => ({ ok: false, error: { code: 'not-called' } }),
    },
  }
}

test('desktop bridge detection requires the complete closed product surface', () => {
  const complete = bridgeFixture()
  assert.equal(resolveDesktopBridge(complete), complete)
  assert.equal(resolveDesktopBridge(undefined), null)
  assert.equal(resolveDesktopBridge({}), null)
  assert.equal(
    resolveDesktopBridge({
      ...complete,
      workspaces: { ...complete.workspaces, archive: undefined },
    }),
    null,
  )
  assert.equal(
    resolveDesktopBridge({
      ...complete,
      owner: { ...complete.owner, bootstrap: 'not-a-function' },
    }),
    null,
  )
})
