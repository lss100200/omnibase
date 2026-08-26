import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  beginDesktopTeamRun,
  completeDesktopTeamRun,
  createDesktopTeamLiveState,
  desktopTeamAppendBudgetTarget,
  desktopTeamEventBindsLiveRun,
  desktopTeamLiveProjection,
  desktopTeamStopVisible,
  failDesktopTeamPreStart,
  pendingDurableTeamCancel,
  reduceDesktopTeamEvent,
  requestDesktopTeamCancel,
  switchDesktopTeamScope,
  type DesktopTeamLiveState,
  type DesktopTeamRunEvent,
} from './desktop-team-lifecycle'
import {
  desktopTeamTranscriptHighlight,
  projectDesktopTeamBudget,
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
  state = reduceDesktopTeamEvent(
    state,
    snapshot({ type: 'node_delta', rosterEpoch: 4, employeeRoleId: 'parent', text: '旧团队流' }),
  )
  state = switchDesktopTeamScope(state, WORKSPACE_B, CONVERSATION_B)
  const projection = desktopTeamLiveProjection(state, WORKSPACE_B, CONVERSATION_B)
  assert.equal(projection.visible, false)
  assert.equal(projection.parentLiveText, '')
  assert.equal(desktopTeamStopVisible(state), true)
  state = switchDesktopTeamScope(state, WORKSPACE_A, CONVERSATION_A)
  assert.equal(
    desktopTeamLiveProjection(state, WORKSPACE_A, CONVERSATION_A).parentLiveText,
    '旧团队流',
  )
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
  const drifted = reduceDesktopTeamEvent(
    state,
    snapshot({ type: 'completed', rosterEpoch: 9, parentFinalAnswer: 'should not appear' }),
  )
  assert.equal(drifted.parentFinalAnswer, null)
  const otherRun = reduceDesktopTeamEvent(
    state,
    snapshot({
      type: 'completed',
      teamRunId: `teamrun_${'9'.repeat(32)}`,
      rosterEpoch: 2,
      parentFinalAnswer: 'old run',
    }),
  )
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
  state = reduceDesktopTeamEvent(
    state,
    snapshot({ type: 'completed', parentFinalAnswer: '父 Agent 汇总' }),
  )
  assert.equal(desktopTeamTranscriptHighlight(state, WORKSPACE_A, CONVERSATION_A), '父 Agent 汇总')
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
  const drifted = reduceDesktopTeamEvent(
    state,
    snapshot({
      type: 'node_terminal',
      waveId: 'wave-1',
      nodeId: NODE,
      answer: 'old wave must not land',
    }),
  )
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
  state = reduceDesktopTeamEvent(
    state,
    snapshot({
      type: 'wave_starting',
      planRevisionId: 'teamrev_1',
      waveId: 'wave-1',
      assignmentIds: ['frontend-review', 'backend-review'],
      employeeRoleIds: ['frontend', 'backend'],
    }),
  )
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
    const incomplete = {
      ...bound({ type: 'node_terminal', answer: `leak-${key}` }),
    } as DesktopTeamRunEvent & Record<string, unknown>
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

test('leaving origin parks phase plan and budget so B has no A team chrome', () => {
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
    snapshot({
      type: 'proposal',
      planRevisionId: PLAN_A,
      planSummary: 'frontend-review',
      consumedProviderCalls: 4,
      maximumProviderCalls: 8,
    }),
  )
  state = reduceDesktopTeamEvent(
    state,
    snapshot({
      type: 'parent_proposing',
      planRevisionId: PLAN_A,
      consumedProviderCalls: 4,
      maximumProviderCalls: 8,
    }),
  )
  assert.equal(state.phase, 'parent_proposing')
  assert.equal(state.planRevisionId, PLAN_A)
  assert.equal(state.planSummary, 'frontend-review')
  assert.equal(projectDesktopTeamBudget(state), '已用 4 / 上限 8 次调用')
  assert.equal(
    projectDesktopTeamEmployees(state).find((item) => item.roleId === 'parent')?.statusText,
    '运行中',
  )
  state = switchDesktopTeamScope(state, WORKSPACE_B, CONVERSATION_B)
  assert.equal(state.phase, 'idle')
  assert.equal(state.planRevisionId, null)
  assert.equal(state.planSummary, null)
  assert.equal(state.waveId, null)
  assert.equal(state.consumedProviderCalls, 0)
  assert.equal(state.maximumProviderCalls, 0)
  assert.equal(projectDesktopTeamBudget(state), '已用 0 / 上限 0 次调用')
  assert.equal(
    projectDesktopTeamEmployees(state).find((item) => item.roleId === 'parent')?.statusText,
    '静默',
  )
  assert.equal(desktopTeamStopVisible(state), true)
  state = switchDesktopTeamScope(state, WORKSPACE_A, CONVERSATION_A)
  assert.equal(state.phase, 'parent_proposing')
  assert.equal(state.planRevisionId, PLAN_A)
  assert.equal(state.planSummary, 'frontend-review')
  assert.equal(projectDesktopTeamBudget(state), '已用 4 / 上限 8 次调用')
  assert.equal(
    projectDesktopTeamEmployees(state).find((item) => item.roleId === 'parent')?.statusText,
    '运行中',
  )
})

test('cancelled failed unknown and budget_exhausted stay latched against a late completed event', () => {
  const terminals = [
    { type: 'cancelled', phase: 'cancelled' },
    { type: 'failed', phase: 'failed' },
    { type: 'unknown', phase: 'unknown' },
    { type: 'budget_exhausted', phase: 'budget_exhausted' },
  ] as const
  for (const terminal of terminals) {
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
    state = reduceDesktopTeamEvent(state, snapshot({ type: terminal.type }))
    assert.equal(state.phase, terminal.phase, `${terminal.type} should land`)
    const late = reduceDesktopTeamEvent(
      state,
      snapshot({ type: 'completed', parentFinalAnswer: 'late-success' }),
    )
    assert.equal(late.phase, terminal.phase, `${terminal.type} must not resurrect`)
    assert.notEqual(late.runState, 'succeeded', `${terminal.type} must not become succeeded`)
    assert.notEqual(late.parentFinalAnswer, 'late-success')
  }
})

function landedTerminal(kind: string): DesktopTeamLiveState {
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
  if (kind === 'cannot_complete') {
    return reduceDesktopTeamEvent(state, snapshot({ state: 'cannot_complete' }))
  }
  state = reduceDesktopTeamEvent(state, snapshot())
  state = reduceDesktopTeamEvent(state, bound({ type: 'proposal', planRevisionId: PLAN_A }))
  state = reduceDesktopTeamEvent(
    state,
    bound({
      type: 'wave_starting',
      waveId: 'wave-1',
      assignmentIds: ['frontend-review'],
      employeeRoleIds: ['frontend'],
    }),
  )
  state = reduceDesktopTeamEvent(state, bound({ type: 'node_starting' }))
  state = reduceDesktopTeamEvent(
    state,
    bound({ type: 'node_terminal', answer: 'node-report', reportStatus: 'completed' }),
  )
  if (kind === 'completed') {
    return reduceDesktopTeamEvent(
      state,
      bound({ type: 'completed', parentFinalAnswer: 'final-ok' }),
    )
  }
  return reduceDesktopTeamEvent(state, bound({ type: kind }))
}

test('all six terminals absorb every late mutable event', () => {
  const terminals = [
    'completed',
    'cancelled',
    'failed',
    'unknown',
    'budget_exhausted',
    'cannot_complete',
  ]
  const lateEvents: readonly DesktopTeamRunEvent[] = [
    bound({ type: 'proposal', planRevisionId: PLAN_A, planSummary: 'late-plan' }),
    bound({ type: 'plan_transition', oldPlanRevisionId: PLAN_A, planRevisionId: PLAN_B }),
    bound({
      type: 'wave_starting',
      waveId: 'wave-9',
      assignmentIds: ['frontend-review'],
      employeeRoleIds: ['frontend'],
    }),
    bound({
      type: 'node_starting',
      nodeId: `teamnode_${'9'.repeat(32)}`,
      nodeOrdinal: 2,
      invocationId: `invocation_${'9'.repeat(32)}`,
      sendEpoch: 9,
      nodeEpoch: 9,
    }),
    bound({ type: 'node_delta', employeeRoleId: 'parent', text: 'LATE' }),
    bound({ type: 'node_terminal', answer: 'late-answer', reportStatus: 'completed' }),
    bound({ type: 'blackboard' }),
    bound({ type: 'parent_replanning' }),
    bound({ type: 'parent_synthesizing' }),
    bound({ type: 'completed', parentFinalAnswer: 'late-success' }),
    bound({ type: 'cancelled' }),
    bound({ type: 'failed' }),
  ]
  for (const terminal of terminals) {
    const state = landedTerminal(terminal)
    assert.notEqual(state.phase, 'preparing', `${terminal} should land`)
    for (const late of lateEvents) {
      const after = reduceDesktopTeamEvent(state, late)
      assert.equal(after.phase, state.phase, `${terminal} + ${late.type} must keep phase`)
      assert.equal(after.runState, state.runState, `${terminal} + ${late.type} must keep runState`)
      assert.equal(
        after.parentLiveText,
        state.parentLiveText,
        `${terminal} + ${late.type} must keep parent text`,
      )
      assert.equal(
        after.parentFinalAnswer,
        state.parentFinalAnswer,
        `${terminal} + ${late.type} must keep final answer`,
      )
      assert.deepEqual(after.nodes, state.nodes, `${terminal} + ${late.type} must keep nodes`)
      assert.deepEqual(
        after.collaborationLines,
        state.collaborationLines,
        `${terminal} + ${late.type} must keep collaboration`,
      )
      assert.equal(
        after.consumedProviderCalls,
        state.consumedProviderCalls,
        `${terminal} + ${late.type} must keep budget`,
      )
      assert.equal(
        after.planRevisionId,
        state.planRevisionId,
        `${terminal} + ${late.type} must keep plan`,
      )
      assert.equal(after.waveId, state.waveId, `${terminal} + ${late.type} must keep wave`)
    }
  }
})

test('first snapshot from origin A binds parked identity while viewing workspace B', () => {
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
  assert.equal(state.teamRunId, null)
  state = switchDesktopTeamScope(state, WORKSPACE_B, CONVERSATION_B)
  const boundState = reduceDesktopTeamEvent(state, snapshot())
  assert.equal(boundState.teamRunId, TEAM_RUN)
  assert.equal(boundState.phase, 'idle')
  assert.equal(boundState.parkedPhase, 'preparing')
  assert.equal(boundState.originWorkspaceId, WORKSPACE_A)
  const stillB = desktopTeamLiveProjection(boundState, WORKSPACE_B, CONVERSATION_B)
  assert.equal(stillB.visible, false)
  assert.equal(stillB.parentLiveText, '')
  const parkedDelta = reduceDesktopTeamEvent(
    boundState,
    bound({ type: 'node_delta', employeeRoleId: 'parent', text: 'parked-text' }),
  )
  assert.equal(parkedDelta.parentLiveText, '')
  assert.equal(parkedDelta.parkedParentLiveText, 'parked-text')
  const returned = switchDesktopTeamScope(parkedDelta, WORKSPACE_A, CONVERSATION_A)
  assert.equal(returned.teamRunId, TEAM_RUN)
  assert.equal(returned.phase, 'preparing')
  assert.equal(returned.parentLiveText, 'parked-text')
})

test('a bound run does not rebind a different team run id', () => {
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
  assert.equal(state.teamRunId, TEAM_RUN)
  const foreign = reduceDesktopTeamEvent(
    state,
    snapshot({ teamRunId: `teamrun_${'9'.repeat(32)}` }),
  )
  assert.equal(foreign.teamRunId, TEAM_RUN)
  assert.equal(foreign.runState, 'preparing')
})

test('stop before identity binds the late snapshot and cancels exactly once', () => {
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
  state = switchDesktopTeamScope(state, WORKSPACE_B, CONVERSATION_B)
  state = requestDesktopTeamCancel(state)
  assert.equal(state.cancelRequested, true)
  assert.equal(state.parkedPhase, 'cancelling')
  assert.equal(pendingDurableTeamCancel(state, null), null)
  state = reduceDesktopTeamEvent(state, snapshot({ state: 'running' }))
  assert.equal(state.teamRunId, TEAM_RUN)
  assert.equal(state.parkedPhase, 'cancelling')
  assert.equal(state.parkedRunState, 'running')
  assert.equal(state.phase, 'idle')
  assert.equal(pendingDurableTeamCancel(state, null), TEAM_RUN)
  assert.equal(pendingDurableTeamCancel(state, TEAM_RUN), null)
  state = reduceDesktopTeamEvent(state, snapshot({ type: 'cancelled' }))
  assert.equal(state.parkedPhase, 'cancelled')
  assert.equal(state.parkedRunState, 'cancelled')
  const returned = switchDesktopTeamScope(state, WORKSPACE_A, CONVERSATION_A)
  assert.equal(returned.phase, 'cancelled')
  assert.equal(returned.runState, 'cancelled')
  assert.equal(pendingDurableTeamCancel(returned, TEAM_RUN), null)
})

test('snapshot first then stop still dispatches durable cancel exactly once', () => {
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
  state = switchDesktopTeamScope(state, WORKSPACE_B, CONVERSATION_B)
  state = reduceDesktopTeamEvent(state, snapshot({ state: 'running' }))
  assert.equal(pendingDurableTeamCancel(state, null), null)
  state = requestDesktopTeamCancel(state)
  assert.equal(pendingDurableTeamCancel(state, null), TEAM_RUN)
  assert.equal(pendingDurableTeamCancel(state, TEAM_RUN), null)
})

test('a snapshot from an older roster epoch does not rebind a new run', () => {
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
  state = reduceDesktopTeamEvent(state, snapshot({ type: 'completed', parentFinalAnswer: 'done' }))
  state = completeDesktopTeamRun(state)
  assert.equal(state.phase, 'idle')
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 2,
    maximumProviderCalls: 8,
  })
  assert.equal(state.teamRunId, null)
  const stale = reduceDesktopTeamEvent(state, snapshot())
  assert.equal(stale.teamRunId, null)
  assert.equal(stale.phase, 'preparing')
})

test('append budget target is origin-only and uses the origin workspace id', () => {
  let state = createDesktopTeamLiveState({
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
  })
  assert.equal(desktopTeamAppendBudgetTarget(state), null)
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 1,
    maximumProviderCalls: 8,
  })
  assert.equal(desktopTeamAppendBudgetTarget(state), null)
  state = reduceDesktopTeamEvent(state, snapshot({ state: 'running' }))
  assert.deepEqual(desktopTeamAppendBudgetTarget(state), {
    workspaceId: WORKSPACE_A,
    teamRunId: TEAM_RUN,
  })
  state = switchDesktopTeamScope(state, WORKSPACE_B, CONVERSATION_B)
  assert.equal(desktopTeamAppendBudgetTarget(state), null)
  state = switchDesktopTeamScope(state, WORKSPACE_A, CONVERSATION_A)
  assert.deepEqual(desktopTeamAppendBudgetTarget(state), {
    workspaceId: WORKSPACE_A,
    teamRunId: TEAM_RUN,
  })
  state = reduceDesktopTeamEvent(state, snapshot({ type: 'cancelled' }))
  assert.equal(desktopTeamAppendBudgetTarget(state), null)
  state = completeDesktopTeamRun(state)
  state = beginDesktopTeamRun(state, {
    workspaceId: WORKSPACE_A,
    conversationId: CONVERSATION_A,
    rosterEpoch: 2,
    maximumProviderCalls: 8,
  })
  state = reduceDesktopTeamEvent(state, snapshot({ rosterEpoch: 2, state: 'running' }))
  state = reduceDesktopTeamEvent(
    state,
    snapshot({ rosterEpoch: 2, type: 'completed', parentFinalAnswer: 'done' }),
  )
  assert.equal(desktopTeamAppendBudgetTarget(state), null)
})

test('a terminal first snapshot latches before a late cancelled event', () => {
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
  state = switchDesktopTeamScope(state, WORKSPACE_B, CONVERSATION_B)
  state = requestDesktopTeamCancel(state)
  state = reduceDesktopTeamEvent(state, snapshot({ state: 'succeeded' }))
  assert.equal(state.teamRunId, TEAM_RUN)
  assert.equal(state.parkedPhase, 'completed')
  assert.equal(state.parkedRunState, 'succeeded')
  assert.equal(pendingDurableTeamCancel(state, null), TEAM_RUN)
  const lateCancelled = reduceDesktopTeamEvent(state, snapshot({ type: 'cancelled' }))
  assert.equal(lateCancelled.parkedPhase, 'completed')
  assert.equal(lateCancelled.parkedRunState, 'succeeded')
  const returned = switchDesktopTeamScope(lateCancelled, WORKSPACE_A, CONVERSATION_A)
  assert.equal(returned.phase, 'completed')
  assert.equal(returned.runState, 'succeeded')
})

// ---------------------------------------------------------------------------
// desktopTeamEventBindsLiveRun: identity-only side-effect acceptance
// ---------------------------------------------------------------------------

test('binds-live-run: a waiting state accepts its own snapshot identity', () => {
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
  assert.equal(state.teamRunId, null)
  assert.equal(desktopTeamEventBindsLiveRun(state, snapshot()), true)
  // While unbound, the roster epoch identifies the single live attempt (the
  // coordinator executes once per roster); the run id is not yet known, so
  // any event of this roster and origin may establish the live slot.
  assert.equal(
    desktopTeamEventBindsLiveRun(state, snapshot({ teamRunId: `teamrun_${'d'.repeat(32)}` })),
    true,
    'an unbound state accepts this roster attempt regardless of the future run id',
  )
})

test('binds-live-run: events for another roster or scope never bind', () => {
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
  assert.equal(
    desktopTeamEventBindsLiveRun(state, snapshot({ rosterEpoch: 2 })),
    false,
    'a different roster is a different run attempt',
  )
  assert.equal(desktopTeamEventBindsLiveRun(state, snapshot({ workspaceId: WORKSPACE_B })), false)
  assert.equal(
    desktopTeamEventBindsLiveRun(state, snapshot({ conversationId: CONVERSATION_B })),
    false,
  )
})

test('binds-live-run: incomplete events never bind', () => {
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
  assert.equal(
    desktopTeamEventBindsLiveRun(state, {
      type: 'parent_proposing',
      teamRunId: TEAM_RUN,
    } as DesktopTeamRunEvent),
    false,
  )
})

test('binds-live-run: after binding, only the same run binds', () => {
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
  const bound = reduceDesktopTeamEvent(state, snapshot())
  assert.equal(bound.teamRunId, TEAM_RUN)
  assert.equal(desktopTeamEventBindsLiveRun(bound, snapshot()), true)
  assert.equal(
    desktopTeamEventBindsLiveRun(bound, snapshot({ teamRunId: `teamrun_${'d'.repeat(32)}` })),
    false,
    'a late event for a previous run must not hijack the current identity',
  )
})

test('binds-live-run: parked views still bind legitimate snapshots', () => {
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
  const parked = switchDesktopTeamScope(state, WORKSPACE_B, CONVERSATION_B)
  assert.equal(parked.phase, 'idle', 'visible phase is hidden while parked')
  assert.equal(
    desktopTeamEventBindsLiveRun(parked, snapshot()),
    true,
    'identity gates must not depend on the hidden visible phase',
  )
})
