import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { PersonalTeamBlackboard } from './desktop-bridge'
import {
  createP7LiveSlotState,
  invalidateP7LiveSlot,
  p7HistoryBoardForSelection,
  p7LiveSlotViewProjection,
  p7SelectionStaleInWorkspace,
  reduceP7LiveSlotEvent,
  selectP7HistoryRun,
  type P7LiveSlotState,
} from './p7-live-slot'

const RUN_A = 'teamrun-a'
const RUN_B = 'teamrun-b'
const WORKSPACE = 'workspace'
const CONVERSATION_A = 'conversation-a'
const CONVERSATION_B = 'conversation-b'
const KEY_A = `${WORKSPACE}:${CONVERSATION_A}`

function event(overrides: Partial<Parameters<typeof reduceP7LiveSlotEvent>[1]> = {}) {
  return {
    eventRunId: RUN_A,
    eventWorkspaceId: WORKSPACE,
    eventConversationId: CONVERSATION_A,
    isTerminal: false,
    bindsLiveRun: true,
    viewWorkspaceId: WORKSPACE,
    viewConversationId: CONVERSATION_A,
    boardChanged: false,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// Terminal events never open a slot
// ---------------------------------------------------------------------------

test('slot: a terminal first event keeps the live slot closed but lands the board', () => {
  const { state, effects } = reduceP7LiveSlotEvent(
    createP7LiveSlotState(),
    event({ isTerminal: true }),
  )
  assert.equal(state.liveRunId, null, 'a terminal snapshot must not open the live slot')
  assert.equal(state.liveOriginKey, null)
  assert.equal(effects.loadLiveBoard, false, 'a terminal snapshot must not load a live board')
  assert.equal(
    state.historyRunId,
    RUN_A,
    'a terminal-first event still leaves an origin-scoped history selection',
  )
  assert.equal(state.historyOriginKey, KEY_A)
  assert.equal(effects.loadHistoryBoard, true, 'the final board is loaded for the selection')
  const view = p7LiveSlotViewProjection(state, WORKSPACE, CONVERSATION_A)
  assert.equal(view.selectionVisible, true)
  assert.equal(view.selectionRunId, RUN_A)
})

test('slot: a terminal first event while parked still lands an origin-scoped selection', () => {
  const { state, effects } = reduceP7LiveSlotEvent(
    createP7LiveSlotState(),
    event({ isTerminal: true, viewConversationId: CONVERSATION_B }),
  )
  assert.equal(state.liveRunId, null)
  assert.equal(state.historyRunId, RUN_A)
  assert.equal(state.historyOriginKey, KEY_A)
  assert.equal(effects.loadHistoryBoard, true)
  const inB = p7LiveSlotViewProjection(state, WORKSPACE, CONVERSATION_B)
  assert.equal(inB.selectionVisible, false, 'the projection hides it while viewing B')
  const backInA = p7LiveSlotViewProjection(state, WORKSPACE, CONVERSATION_A)
  assert.equal(backInA.selectionVisible, true)
  assert.equal(backInA.selectionRunId, RUN_A)
})

test('slot: a terminal first event never clobbers an existing manual selection', () => {
  let state = createP7LiveSlotState()
  state = selectP7HistoryRun(state, RUN_B, WORKSPACE, CONVERSATION_B)
  const terminal = reduceP7LiveSlotEvent(state, event({ isTerminal: true }))
  assert.equal(terminal.state.historyRunId, RUN_B, 'the manual selection is preserved')
  assert.equal(terminal.state.historyIsManual, true)
  assert.equal(terminal.effects.loadHistoryBoard, false)
})

test('slot: a terminal-first event replaces an old auto selection', () => {
  // run-a runs and terminates: auto selection stays run-a
  let state = reduceP7LiveSlotEvent(createP7LiveSlotState(), event({ eventRunId: RUN_A })).state
  state = reduceP7LiveSlotEvent(state, event({ eventRunId: RUN_A, isTerminal: true })).state
  assert.equal(state.historyRunId, RUN_A)
  assert.equal(state.historyIsManual, false)
  // run-b starts; its FIRST event is terminal
  const firstTerminalB = reduceP7LiveSlotEvent(
    state,
    event({ eventRunId: RUN_B, isTerminal: true }),
  )
  assert.equal(firstTerminalB.state.liveRunId, null, 'the live slot stays closed')
  assert.equal(
    firstTerminalB.state.historyRunId,
    RUN_B,
    'the terminal-first run replaces the old auto selection',
  )
  assert.equal(firstTerminalB.state.historyIsManual, false)
  assert.equal(firstTerminalB.effects.loadHistoryBoard, true, 'the final board is loaded')
})

// ---------------------------------------------------------------------------
// Cross-workspace background runs
// ---------------------------------------------------------------------------

test('slot: a background run in workspace A is hidden in workspace B and readable on return', () => {
  const WORKSPACE_B2 = `${WORKSPACE}-other`
  // run in A (workspace WORKSPACE) while parked in B2
  let state = reduceP7LiveSlotEvent(
    createP7LiveSlotState(),
    event({ viewWorkspaceId: WORKSPACE_B2 }),
  ).state
  assert.equal(state.liveRunId, RUN_A)
  const inB2 = p7LiveSlotViewProjection(state, WORKSPACE_B2, CONVERSATION_A)
  assert.equal(inB2.liveCurrent, false)
  assert.equal(inB2.selectionVisible, false, 'workspace B never displays workspace A content')
  const backInA = p7LiveSlotViewProjection(state, WORKSPACE, CONVERSATION_A)
  assert.equal(backInA.liveCurrent, true, 'the origin workspace reads the live slot again')
  assert.equal(backInA.selectionVisible, true)
})

test('slot: a background terminal in A survives and prefetches while viewing B', () => {
  const WORKSPACE_B2 = `${WORKSPACE}-other`
  let state = reduceP7LiveSlotEvent(
    createP7LiveSlotState(),
    event({ viewWorkspaceId: WORKSPACE_B2 }),
  ).state
  const terminal = reduceP7LiveSlotEvent(
    state,
    event({ isTerminal: true, viewWorkspaceId: WORKSPACE_B2 }),
  )
  assert.equal(terminal.effects.loadHistoryBoard, true, 'the final board is prefetched')
  assert.equal(terminal.state.liveRunId, null)
  const backInA = p7LiveSlotViewProjection(terminal.state, WORKSPACE, CONVERSATION_A)
  assert.equal(backInA.selectionVisible, true, 'returning to A recovers the selection')
  assert.equal(backInA.selectionRunId, RUN_A)
})

// ---------------------------------------------------------------------------
// Run-list reload validation
// ---------------------------------------------------------------------------

test('slot: a reload only validates selections belonging to the loaded workspace', () => {
  const WORKSPACE_B2 = `${WORKSPACE}-other`
  // auto selection established in A
  const state = reduceP7LiveSlotEvent(createP7LiveSlotState(), event()).state
  assert.equal(state.historyOriginWorkspaceId, WORKSPACE)
  // loading B's list must not clear A's background selection
  assert.equal(
    p7SelectionStaleInWorkspace({
      historyWorkspaceId: state.historyOriginWorkspaceId,
      loadedWorkspaceId: WORKSPACE_B2,
      historyRunId: state.historyRunId,
      runs: [{ id: 'teamrun-other' }],
    }),
    false,
    'a selection from another workspace survives a foreign reload',
  )
  // loading A's list validates it
  assert.equal(
    p7SelectionStaleInWorkspace({
      historyWorkspaceId: state.historyOriginWorkspaceId,
      loadedWorkspaceId: WORKSPACE,
      historyRunId: state.historyRunId,
      runs: [{ id: 'teamrun-other' }],
    }),
    true,
    'a missing run of the loaded workspace is stale',
  )
  assert.equal(
    p7SelectionStaleInWorkspace({
      historyWorkspaceId: state.historyOriginWorkspaceId,
      loadedWorkspaceId: WORKSPACE,
      historyRunId: state.historyRunId,
      runs: [{ id: RUN_A }],
    }),
    false,
  )
  assert.equal(
    p7SelectionStaleInWorkspace({
      historyWorkspaceId: WORKSPACE,
      loadedWorkspaceId: WORKSPACE,
      historyRunId: null,
      runs: [],
    }),
    false,
  )
})

test('slot: duplicate or late terminal events never reopen the slot', () => {
  let state = createP7LiveSlotState()
  // open the slot with a normal snapshot, then close it with the terminal
  const opened = reduceP7LiveSlotEvent(state, event()).state
  assert.equal(opened.liveRunId, RUN_A)
  const closed = reduceP7LiveSlotEvent(opened, event({ isTerminal: true }))
  assert.equal(closed.state.liveRunId, null)
  // the same terminal event arrives again (or later) after the close
  const repeated = reduceP7LiveSlotEvent(closed.state, event({ isTerminal: true }))
  assert.equal(repeated.state.liveRunId, null, 'a repeated terminal must not reopen the slot')
  assert.equal(repeated.effects.loadLiveBoard, false)
})

test('slot: a terminal for another run never touches the open slot', () => {
  const opened = reduceP7LiveSlotEvent(createP7LiveSlotState(), event()).state
  const terminalOther = reduceP7LiveSlotEvent(
    opened,
    event({ eventRunId: RUN_B, isTerminal: true }),
  )
  assert.equal(terminalOther.state.liveRunId, RUN_A, 'the live run stays open')
  assert.equal(terminalOther.state.liveOriginKey, KEY_A)
})

// ---------------------------------------------------------------------------
// Auto-follow is conversation-scoped
// ---------------------------------------------------------------------------

test('slot: A started, switch to B, background terminal prefetches but never displays in B', () => {
  let state = createP7LiveSlotState()
  // run starts while viewing A: auto-follow established
  state = reduceP7LiveSlotEvent(state, event()).state
  assert.equal(state.historyRunId, RUN_A)
  assert.equal(state.historyOriginKey, KEY_A)
  assert.equal(state.historyIsManual, false)
  // user switches to B (view change is pure projection, no state reset)
  const inB = p7LiveSlotViewProjection(state, WORKSPACE, CONVERSATION_B)
  assert.equal(inB.liveCurrent, false)
  assert.equal(inB.selectionVisible, false, 'A auto-follow must not highlight in B')
  // background terminal for A while viewing B
  const terminal = reduceP7LiveSlotEvent(
    state,
    event({
      isTerminal: true,
      viewWorkspaceId: WORKSPACE,
      viewConversationId: CONVERSATION_B,
    }),
  )
  assert.equal(
    terminal.effects.loadHistoryBoard,
    true,
    'the final board is prefetched even while the run finishes in the background',
  )
  assert.equal(terminal.state.liveRunId, null, 'the terminal still closes the live slot')
  const afterTerminalInB = p7LiveSlotViewProjection(terminal.state, WORKSPACE, CONVERSATION_B)
  assert.equal(afterTerminalInB.selectionVisible, false, 'B never displays the A board')
})

test('slot: returning to the origin after a background terminal reads the final board', () => {
  let state = reduceP7LiveSlotEvent(createP7LiveSlotState(), event()).state
  const terminal = reduceP7LiveSlotEvent(
    state,
    event({
      isTerminal: true,
      viewWorkspaceId: WORKSPACE,
      viewConversationId: CONVERSATION_B,
    }),
  )
  assert.equal(terminal.effects.loadHistoryBoard, true)
  // back in A: the selection is visible again and points at the finished run
  const backInA = p7LiveSlotViewProjection(terminal.state, WORKSPACE, CONVERSATION_A)
  assert.equal(backInA.selectionVisible, true)
  assert.equal(backInA.selectionRunId, RUN_A)
})

test('slot: a parked first event still establishes the auto-follow from its origin', () => {
  // run starts while the user is parked in B: the selection is established
  // from the event origin, and the projection hides it in B.
  const parked = reduceP7LiveSlotEvent(
    createP7LiveSlotState(),
    event({ viewConversationId: CONVERSATION_B }),
  )
  assert.equal(parked.state.liveRunId, RUN_A, 'the live slot still opens (identity gates)')
  assert.equal(parked.state.liveOriginKey, KEY_A)
  assert.equal(parked.state.historyRunId, RUN_A, 'auto-follow is established from the event origin')
  assert.equal(parked.state.historyOriginKey, KEY_A)
  const inB = p7LiveSlotViewProjection(parked.state, WORKSPACE, CONVERSATION_B)
  assert.equal(inB.selectionVisible, false, 'the projection hides it while viewing B')
})

test('slot: A start, first event while parked in B, background terminal, return to A', () => {
  // 首事件在 B 到达：auto-follow 仍以 A 为 origin 建立
  let state = reduceP7LiveSlotEvent(
    createP7LiveSlotState(),
    event({ viewConversationId: CONVERSATION_B }),
  ).state
  assert.equal(state.historyRunId, RUN_A)
  // 后台终态：预取最终黑板
  const terminal = reduceP7LiveSlotEvent(
    state,
    event({ isTerminal: true, viewConversationId: CONVERSATION_B }),
  )
  assert.equal(terminal.effects.loadHistoryBoard, true, 'terminal prefetches the final board')
  assert.equal(terminal.state.liveRunId, null)
  // 返回 A：选择可见，最终黑板可读
  const backInA = p7LiveSlotViewProjection(terminal.state, WORKSPACE, CONVERSATION_A)
  assert.equal(backInA.selectionVisible, true)
  assert.equal(backInA.selectionRunId, RUN_A)
})

test('slot: returning to the origin reads the correct live board', () => {
  const state = reduceP7LiveSlotEvent(
    createP7LiveSlotState(),
    event({ viewConversationId: CONVERSATION_B }),
  ).state
  const backInA = p7LiveSlotViewProjection(state, WORKSPACE, CONVERSATION_A)
  assert.equal(backInA.liveCurrent, true, 'live slot is current again in the origin view')
  assert.equal(
    backInA.selectionVisible,
    true,
    'the parked-run auto-follow is visible in its origin',
  )
  assert.equal(backInA.selectionRunId, RUN_A)
})

test('slot: terminal in A while viewing A lands the final history board', () => {
  let state = reduceP7LiveSlotEvent(createP7LiveSlotState(), event()).state
  assert.equal(state.historyRunId, RUN_A)
  const terminal = reduceP7LiveSlotEvent(state, event({ isTerminal: true }))
  assert.equal(
    terminal.effects.loadHistoryBoard,
    true,
    'origin-view terminal lands the final board',
  )
  assert.equal(terminal.state.liveRunId, null)
  const view = p7LiveSlotViewProjection(terminal.state, WORKSPACE, CONVERSATION_A)
  assert.equal(view.selectionVisible, true, 'the auto-followed run is now the history selection')
  assert.equal(view.selectionRunId, RUN_A)
})

// ---------------------------------------------------------------------------
// Manual selection
// ---------------------------------------------------------------------------

test('slot: manual selection is visible in any conversation of the workspace', () => {
  let state = createP7LiveSlotState()
  state = selectP7HistoryRun(state, RUN_B, WORKSPACE, CONVERSATION_B)
  assert.equal(state.historyIsManual, true)
  const inA = p7LiveSlotViewProjection(state, WORKSPACE, CONVERSATION_A)
  assert.equal(inA.selectionVisible, true)
  assert.equal(inA.selectionRunId, RUN_B)
})

test('slot: manual selection never projects across workspaces', () => {
  let state = createP7LiveSlotState()
  state = selectP7HistoryRun(state, RUN_B, WORKSPACE, CONVERSATION_B)
  const otherWorkspace = `${WORKSPACE}-other`
  const across = p7LiveSlotViewProjection(state, otherWorkspace, CONVERSATION_A)
  assert.equal(
    across.selectionVisible,
    false,
    'a manual selection must not cross the workspace boundary',
  )
  assert.equal(across.selectionRunId, null)
})

// ---------------------------------------------------------------------------
// History board display identity
// ---------------------------------------------------------------------------

test('slot: history board renders only under its own run selection', () => {
  const boardA: PersonalTeamBlackboard = {
    teamRunId: RUN_A,
    workspaceId: WORKSPACE,
    ownerObjective: 'A 任务',
    currentPlanRevisionId: null,
    assignments: [],
    reports: [],
    collaborationRequests: [],
  }
  assert.equal(p7HistoryBoardForSelection(RUN_A, boardA), boardA)
  assert.equal(p7HistoryBoardForSelection(RUN_B, boardA), null, 'stale payload must not render')
  assert.equal(p7HistoryBoardForSelection(null, boardA), null)
  assert.equal(p7HistoryBoardForSelection(RUN_A, null), null)
})

test('slot: manual selection suppresses later auto-follows', () => {
  let state = createP7LiveSlotState()
  state = selectP7HistoryRun(state, RUN_B, WORKSPACE, CONVERSATION_B)
  const started = reduceP7LiveSlotEvent(state, event())
  assert.equal(started.state.historyRunId, RUN_B, 'manual selection is preserved')
  assert.equal(started.state.liveRunId, RUN_A)
})

test('slot: manual selection terminal refresh works across conversations', () => {
  let state = createP7LiveSlotState()
  state = selectP7HistoryRun(state, RUN_A, WORKSPACE, CONVERSATION_B)
  const terminal = reduceP7LiveSlotEvent(
    state,
    event({
      isTerminal: true,
      viewWorkspaceId: WORKSPACE,
      viewConversationId: CONVERSATION_B,
    }),
  )
  assert.equal(terminal.effects.loadHistoryBoard, true, 'explicit choice keeps its refresh')
})

// ---------------------------------------------------------------------------
// Board refresh and invalidation
// ---------------------------------------------------------------------------

test('slot: board-changing events refresh the live board of the open run', () => {
  const opened = reduceP7LiveSlotEvent(createP7LiveSlotState(), event()).state
  assert.equal(
    reduceP7LiveSlotEvent(opened, event({ boardChanged: true })).effects.loadLiveBoard,
    true,
  )
  assert.equal(
    reduceP7LiveSlotEvent(opened, event({ boardChanged: false })).effects.loadLiveBoard,
    false,
    'plain progress events must not refetch the board',
  )
})

test('slot: invalidation clears the live slot but keeps the history selection', () => {
  let state = reduceP7LiveSlotEvent(createP7LiveSlotState(), event()).state
  state = invalidateP7LiveSlot(state)
  assert.equal(state.liveRunId, null)
  assert.equal(state.liveOriginKey, null)
  assert.equal(state.historyRunId, RUN_A, 'history selection survives a new run attempt')
})

test('slot: non-binding or incomplete events change nothing', () => {
  const base: P7LiveSlotState = {
    liveRunId: RUN_A,
    liveOriginKey: KEY_A,
    historyRunId: null,
    historyOriginKey: null,
    historyOriginWorkspaceId: null,
    historyIsManual: false,
  }
  const rejected = reduceP7LiveSlotEvent(base, event({ bindsLiveRun: false }))
  assert.deepEqual(rejected.state, base)
  assert.deepEqual(rejected.effects, {
    loadLiveBoard: false,
    loadHistoryBoard: false,
    refreshRunHistory: false,
  })
  const missingRun = reduceP7LiveSlotEvent(base, event({ eventRunId: null }))
  assert.deepEqual(missingRun.state, base)
})
