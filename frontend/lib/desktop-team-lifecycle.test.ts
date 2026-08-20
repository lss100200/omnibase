import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  beginDesktopTeamRun,
  createDesktopTeamLiveState,
  desktopTeamLiveProjection,
  desktopTeamStopVisible,
  failDesktopTeamPreStart,
  reduceDesktopTeamEvent,
  requestDesktopTeamCancel,
  switchDesktopTeamScope,
  type DesktopTeamRunEvent,
} from './desktop-team-lifecycle'
import {
  desktopTeamTranscriptHighlight,
  projectDesktopTeamEmployees,
} from './desktop-team-surface'

const WORKSPACE_A = `workspace_${'a'.repeat(32)}`
const WORKSPACE_B = `workspace_${'b'.repeat(32)}`
const CONVERSATION_A = `conversation_${'a'.repeat(32)}`
const CONVERSATION_B = `conversation_${'b'.repeat(32)}`
const TEAM_RUN = `teamrun_${'e'.repeat(32)}`
const NODE = `teamnode_${'f'.repeat(32)}`

function snapshot(overrides: Partial<DesktopTeamRunEvent> = {}): DesktopTeamRunEvent {
  return {
    type: 'snapshot',
    teamRunId: TEAM_RUN,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    planRevisionId: '',
    waveId: '',
    assignmentId: '',
    nodeId: '',
    sendEpoch: 0,
    state: 'preparing',
    ...overrides,
  }
}

test('team FSM is separate from single-invocation idle and keeps text statuses', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 16,
  })
  state = reduceDesktopTeamEvent(state, snapshot())
  state = reduceDesktopTeamEvent(
    state,
    snapshot({
      type: 'node_starting',
      planRevisionId: 'teamrev_1',
      waveId: 'wave-1',
      assignmentId: 'frontend-review',
      nodeId: NODE,
      nodeOrdinal: 1,
      employeeRoleId: 'frontend',
      invocationId: `invocation_${'1'.repeat(32)}`,
      sendEpoch: 2,
      nodeEpoch: 1,
    }),
  )
  const rows = projectDesktopTeamEmployees(state)
  assert.equal(rows.find((item) => item.roleId === 'frontend')?.statusText, '运行中')
  assert.equal(rows.find((item) => item.roleId === 'docs')?.statusText, '静默')
  assert.equal(desktopTeamStopVisible(state), true)
})

test('old team liveText does not paint a new workspace and Stop stays reachable', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 4,
    maximumProviderCalls: 8,
  })
  state = reduceDesktopTeamEvent(state, { ...snapshot(), rosterEpoch: 4 })
  state = reduceDesktopTeamEvent(state, snapshot({ type: 'node_delta', rosterEpoch: 4, employeeRoleId: 'parent', text: '旧团队流' }))
  state = switchDesktopTeamScope(state, WORKSPACE_B, CONVERSATION_B)
  const projection = desktopTeamLiveProjection(state, WORKSPACE_B, CONVERSATION_B)
  assert.equal(projection.visible, false)
  assert.equal(projection.parentLiveText, '')
  assert.equal(desktopTeamStopVisible(state), true)
  state = switchDesktopTeamScope(state, WORKSPACE_A, CONVERSATION_A)
  assert.equal(desktopTeamLiveProjection(state, WORKSPACE_A, CONVERSATION_A).parentLiveText, '旧团队流')
})

test('events must match team/roster/node/send epoch or they are dropped', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 2,
    maximumProviderCalls: 8,
  })
  state = reduceDesktopTeamEvent(state, { ...snapshot(), rosterEpoch: 2 })
  const drifted = reduceDesktopTeamEvent(state, snapshot({ type: 'completed', rosterEpoch: 9, parentFinalAnswer: 'should not appear' }))
  assert.equal(drifted.parentFinalAnswer, null)
  const otherRun = reduceDesktopTeamEvent(state, snapshot({ type: 'completed', teamRunId: `teamrun_${'9'.repeat(32)}`, rosterEpoch: 2, parentFinalAnswer: 'old run' }))
  assert.equal(otherRun.parentFinalAnswer, null)
})

test('parent final answer is the highlighted transcript on origin scope', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 8,
  })
  state = reduceDesktopTeamEvent(state, snapshot())
  state = reduceDesktopTeamEvent(state, snapshot({ type: 'completed', parentFinalAnswer: '父 Agent 汇总' }))
  assert.equal(
    desktopTeamTranscriptHighlight(state, WORKSPACE_A, CONVERSATION_A),
    '父 Agent 汇总',
  )
  assert.equal(desktopTeamTranscriptHighlight(state, WORKSPACE_B, CONVERSATION_B), null)
})

test('Stop request marks cancelling without requiring color-only status', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 8,
  })
  state = requestDesktopTeamCancel(state)
  assert.equal(state.phase, 'cancelling')
  assert.equal(desktopTeamStopVisible(state), true)
})

test('old wave events are dropped after a new wave starts', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 8,
  })
  state = reduceDesktopTeamEvent(state, snapshot())
  state = reduceDesktopTeamEvent(state, snapshot({ type: 'wave_starting', waveId: 'wave-2' }))
  const drifted = reduceDesktopTeamEvent(state, snapshot({ type: 'node_terminal', waveId: 'wave-1', nodeId: NODE, answer: 'old wave must not land' }))
  assert.equal(drifted.nodes.length, 0)
  assert.equal(drifted.waveId, 'wave-2')
})

test('waiting specialist stays 等待 after Stop; running becomes 正在停止', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 8,
  })
  state = reduceDesktopTeamEvent(state, snapshot())
  state = reduceDesktopTeamEvent(state, snapshot({ type: 'wave_starting', planRevisionId: 'teamrev_1', waveId: 'wave-1', assignmentIds: ['frontend-review', 'backend-review'], employeeRoleIds: ['frontend', 'backend'] }))
  state = reduceDesktopTeamEvent(state, snapshot({ type: 'node_starting', planRevisionId: 'teamrev_1', waveId: 'wave-1', assignmentId: 'frontend-review', nodeId: NODE, nodeOrdinal: 1, employeeRoleId: 'frontend', invocationId: `invocation_${'1'.repeat(32)}`, sendEpoch: 2, nodeEpoch: 1 }))
  state = reduceDesktopTeamEvent(state, snapshot({ type: 'cancelled' }))
  const frontend = state.nodes.find((item) => item.assignmentId === 'frontend-review')
  const backend = state.nodes.find((item) => item.assignmentId === 'backend-review')
  assert.equal(frontend?.statusText, '正在停止')
  assert.equal(backend?.statusText, '等待')
})

test('team-scope events still require roster/plan/wave; node identity is per-type', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 8,
  })
  state = reduceDesktopTeamEvent(state, snapshot())
  for (const key of ['rosterEpoch', 'planRevisionId', 'waveId'] as const) {
    const incomplete = { ...snapshot({ type: 'completed', parentFinalAnswer: `leak-${key}` }) }
    delete incomplete[key]
    const next = reduceDesktopTeamEvent(state, incomplete)
    assert.equal(next.parentFinalAnswer, null, `omitting ${key} must not project`)
    assert.notEqual(next.phase, 'completed', `omitting ${key} must not complete`)
  }
  const completed = reduceDesktopTeamEvent(state, {
    type: 'completed',
    teamRunId: TEAM_RUN,
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    planRevisionId: '',
    waveId: '',
    parentFinalAnswer: 'scope-only-ok',
  })
  assert.equal(completed.parentFinalAnswer, 'scope-only-ok')
})

test('pre-start failure converges preparing to idle instead of hanging', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 8,
  })
  assert.equal(state.phase, 'preparing')
  assert.equal(desktopTeamStopVisible(state), true)
  state = failDesktopTeamPreStart(state)
  assert.equal(state.phase, 'idle')
  assert.equal(desktopTeamStopVisible(state), false)
})

const PLAN_A = 'teamrev_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
const PLAN_B = 'teamrev_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
const INVOCATION = `invocation_${'1'.repeat(32)}`

function bound(overrides: Partial<DesktopTeamRunEvent> = {}): DesktopTeamRunEvent {
  return snapshot({
    planRevisionId: PLAN_A,
    waveId: 'wave-1',
    assignmentId: 'frontend-review',
    nodeId: NODE,
    nodeOrdinal: 1,
    employeeRoleId: 'frontend',
    invocationId: INVOCATION,
    sendEpoch: 2,
    nodeEpoch: 1,
    ...overrides,
  })
}

test('replan plan_transition accepts the new proposal and drops a stale old-plan filter', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 8,
  })
  state = reduceDesktopTeamEvent(state, snapshot())
  state = reduceDesktopTeamEvent(state, snapshot({ type: 'proposal', planRevisionId: PLAN_A }))
  assert.equal(state.planRevisionId, PLAN_A)
  const skipped = reduceDesktopTeamEvent(
    state,
    snapshot({ type: 'proposal', planRevisionId: PLAN_B, planSummary: 'must-not-land' }),
  )
  assert.equal(skipped.planRevisionId, PLAN_A)
  assert.equal(skipped.planSummary, null)
  state = reduceDesktopTeamEvent(
    state,
    snapshot({
      type: 'plan_transition',
      oldPlanRevisionId: PLAN_A,
      planRevisionId: PLAN_B,
    }),
  )
  assert.equal(state.planRevisionId, PLAN_B)
  state = reduceDesktopTeamEvent(
    state,
    snapshot({ type: 'proposal', planRevisionId: PLAN_B, planSummary: 'replan-ok' }),
  )
  assert.equal(state.planSummary, 'replan-ok')
  state = reduceDesktopTeamEvent(
    state,
    snapshot({
      type: 'wave_starting',
      planRevisionId: PLAN_B,
      waveId: 'wave-2',
      assignmentIds: ['frontend-review'],
      employeeRoleIds: ['frontend'],
    }),
  )
  assert.equal(state.waveId, 'wave-2')
  assert.equal(state.nodes.length, 1)
})

test('node_terminal missing or mismatched identity fields are dropped', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 8,
  })
  state = reduceDesktopTeamEvent(state, snapshot())
  state = reduceDesktopTeamEvent(state, bound({ type: 'node_starting' }))
  assert.equal(state.nodes[0]?.statusText, '运行中')
  const fields = [
    'assignmentId',
    'employeeRoleId',
    'invocationId',
    'waveId',
    'nodeEpoch',
    'sendEpoch',
  ] as const
  for (const key of fields) {
    const incomplete = { ...bound({ type: 'node_terminal', answer: `leak-${key}` }) } as DesktopTeamRunEvent &
      Record<string, unknown>
    delete incomplete[key]
    const dropped = reduceDesktopTeamEvent(state, incomplete)
    assert.equal(dropped.nodes[0]?.statusText, '运行中', `omitting ${key} must drop`)
    assert.equal(dropped.nodes[0]?.report, null)
  }
  const mismatched = reduceDesktopTeamEvent(
    state,
    bound({ type: 'node_terminal', sendEpoch: 99, answer: 'wrong-epoch' }),
  )
  assert.equal(mismatched.nodes[0]?.statusText, '运行中')
  const ok = reduceDesktopTeamEvent(state, bound({ type: 'node_terminal', answer: 'done' }))
  assert.equal(ok.nodes[0]?.statusText, '已完成')
  assert.equal(ok.nodes[0]?.report, 'done')
})

test('leaving origin parks team buffers and returning restores delta/terminal/final', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 8,
  })
  state = reduceDesktopTeamEvent(state, snapshot())
  state = reduceDesktopTeamEvent(
    state,
    snapshot({ type: 'node_delta', employeeRoleId: 'parent', text: 'origin-' }),
  )
  state = reduceDesktopTeamEvent(state, bound({ type: 'node_starting' }))
  state = switchDesktopTeamScope(state, WORKSPACE_B, CONVERSATION_B)
  const firstOnB = desktopTeamLiveProjection(state, WORKSPACE_B, CONVERSATION_B)
  assert.equal(firstOnB.visible, false)
  assert.equal(firstOnB.parentLiveText, '')
  assert.equal(state.parentLiveText, '')
  assert.equal(state.nodes.length, 0)
  const employeesOnB = projectDesktopTeamEmployees(state)
  assert.equal(employeesOnB.find((item) => item.roleId === 'frontend')?.statusText, '静默')
  state = reduceDesktopTeamEvent(
    state,
    snapshot({ type: 'node_delta', employeeRoleId: 'parent', text: 'parked' }),
  )
  state = reduceDesktopTeamEvent(
    state,
    bound({ type: 'node_terminal', answer: 'specialist-parked', collaborationLine: 'need ux' }),
  )
  state = reduceDesktopTeamEvent(
    state,
    snapshot({ type: 'completed', parentFinalAnswer: 'final-parked' }),
  )
  assert.equal(state.parentLiveText, '')
  assert.equal(state.parentFinalAnswer, null)
  assert.equal(state.nodes.length, 0)
  assert.equal(desktopTeamLiveProjection(state, WORKSPACE_B, CONVERSATION_B).parentLiveText, '')
  state = switchDesktopTeamScope(state, WORKSPACE_A, CONVERSATION_A)
  const restored = desktopTeamLiveProjection(state, WORKSPACE_A, CONVERSATION_A)
  assert.equal(restored.visible, true)
  assert.equal(restored.parentLiveText, 'final-parked')
  assert.equal(restored.parentFinalAnswer, 'final-parked')
  assert.equal(state.nodes[0]?.statusText, '已完成')
  assert.equal(state.nodes[0]?.report, 'specialist-parked')
  assert.deepEqual(state.collaborationLines, ['need ux'])
})
