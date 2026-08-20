import assert from 'node:assert/strict'
import { test } from 'node:test'

import { createDesktopTeamLiveState } from './desktop-team-lifecycle'
import { projectDesktopTeamBudget, projectDesktopTeamEmployees } from './desktop-team-surface'

test('team surface lists ten identities with explicit text status', () => {
  const rows = projectDesktopTeamEmployees(
    createDesktopTeamLiveState({ workspaceId: null, conversationId: null }),
  )
  assert.equal(rows.length, 10)
  assert.equal(rows[0]?.label, '父 Agent')
  assert.ok(rows.every((item) => item.statusText === '静默' || item.statusText === '等待'))
})

test('budget line is numeric remaining, not color-only', () => {
  const state = {
    ...createDesktopTeamLiveState({ workspaceId: null, conversationId: null }),
    consumedProviderCalls: 4,
    maximumProviderCalls: 16,
  }
  assert.equal(projectDesktopTeamBudget(state), '已用 4 / 上限 16 次调用')
})
