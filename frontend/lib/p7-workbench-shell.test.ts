import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import path from 'node:path'
import { test } from 'node:test'
import {
  P7_NO_TRUSTED_SOURCE_REASON,
  closeP7WorkspaceComponentHost,
  createP7ShellUiState,
  createP7WorkspaceShellUiState,
  expandP7Omnia,
  minimizeP7Omnia,
  openP7Blackboard,
  openP7Settings,
  openP7WorkspaceComponentHost,
  p7ActivityLabel,
  p7AssignmentStateLabel,
  p7BottomTabAvailability,
  p7BottomTabLabel,
  p7BriefBoardSelection,
  p7CenterViewLabel,
  p7CollaborationDecisionLabel,
  p7ConversationEventLogLine,
  p7DataSourcePresence,
  p7LiveActive,
  p7LivePendingCollaborations,
  p7NodeTone,
  p7OmniaStateForLive,
  p7RoleLabel,
  p7RunHistoryProjection,
  p7RunStateLabel,
  p7RunningCount,
  p7ShellLayoutClassNames,
  p7WorkbenchRootClassNames,
  p7WorkspaceShellUiProjection,
  p7TeamEventLogLine,
  p7TeamPhaseLabel,
  p7Truncate,
  p7ViewAvailability,
  projectP7AgentFeed,
  projectP7Blackboard,
  projectP7RunRows,
  projectP7ThreadRows,
  selectP7BottomTab,
  setP7AgentPanelOpen,
  setP7BottomOpen,
  setP7CenterView,
  setP7SidebarOpen,
  toggleP7Activity,
  toggleP7OmniaPopover,
  type P7AgentFeedItem,
} from './p7-workbench-shell'
import type {
  DesktopConversation,
  DesktopConversationEvent,
  DesktopTeamRun,
  DesktopTeamRunEvent,
  PersonalTeamBlackboard,
} from './desktop-bridge'
import type { DesktopTeamNodeView, DesktopTeamPhase, TeamRunState } from './desktop-team-lifecycle'
import type { DesktopInvocationPhase } from './desktop-invocation-lifecycle'

function node(overrides: Partial<DesktopTeamNodeView>): DesktopTeamNodeView {
  return {
    nodeId: 'node-1',
    assignmentId: 'assignment-1',
    invocationId: 'invocation-1',
    employeeRoleId: 'qa',
    ordinal: 1,
    waveId: 'wave-1',
    statusText: '运行中',
    durationMs: null,
    inputTokens: null,
    outputTokens: null,
    totalTokens: null,
    report: null,
    sendEpoch: 1,
    nodeEpoch: 1,
    ...overrides,
  }
}

// ---------------------------------------------------------------------------
// 1. Sidebar mutual exclusion
// ---------------------------------------------------------------------------

test('shell: activity click switches and re-click closes the sidebar', () => {
  const initial = createP7ShellUiState()
  assert.equal(initial.activity, 'explorer')
  assert.equal(initial.sidebarOpen, true)

  const switched = toggleP7Activity(initial, 'run')
  assert.equal(switched.activity, 'run')
  assert.equal(switched.sidebarOpen, true)
  assert.equal(initial.activity, 'explorer', 'input state must stay immutable')

  const closed = toggleP7Activity(switched, 'run')
  assert.equal(closed.activity, 'run')
  assert.equal(closed.sidebarOpen, false, 're-clicking the active item closes the sidebar')

  const reopened = toggleP7Activity(closed, 'run')
  assert.equal(reopened.sidebarOpen, true)
})

test('shell: clicking another activity while one is open switches, not stacks', () => {
  const initial = createP7ShellUiState()
  const run = toggleP7Activity(initial, 'run')
  const agents = toggleP7Activity(run, 'agents')
  assert.equal(agents.activity, 'agents')
  assert.equal(agents.sidebarOpen, true)
  assert.equal(agents.activity, 'agents', 'exactly one activity can be active')
})

test('shell: blackboard activity opens the brief center view', () => {
  const initial = createP7ShellUiState()
  const opened = openP7Blackboard(initial)
  assert.equal(opened.activity, 'blackboard')
  assert.equal(opened.sidebarOpen, true)
  assert.equal(opened.centerView, 'brief')
})

test('shell: settings owns the center and closes competing panels', () => {
  const opened = openP7Settings({
    ...createP7ShellUiState(),
    bottomOpen: true,
    agentPanelOpen: true,
  })
  assert.equal(opened.activity, 'settings')
  assert.equal(opened.centerView, 'settings')
  assert.equal(opened.sidebarOpen, false)
  assert.equal(opened.bottomOpen, false)
  assert.equal(opened.agentPanelOpen, false)
})

test('shell: sidebar and center view and agent panel toggles stay independent', () => {
  let state = createP7ShellUiState()
  state = setP7SidebarOpen(state, false)
  state = setP7CenterView(state, 'diff')
  state = setP7AgentPanelOpen(state, false)
  assert.equal(state.sidebarOpen, false)
  assert.equal(state.centerView, 'diff')
  assert.equal(state.agentPanelOpen, false)
  state = setP7SidebarOpen(state, true)
  assert.equal(state.sidebarOpen, true)
  assert.equal(state.centerView, 'diff', 'opening the sidebar must not reset the view')
})

// ---------------------------------------------------------------------------
// 2. Bottom panel expand/collapse
// ---------------------------------------------------------------------------

test('shell: bottom panel collapses and expands without changing the tab', () => {
  const initial = createP7ShellUiState()
  assert.equal(initial.bottomOpen, false)
  const opened = setP7BottomOpen(initial, true)
  assert.equal(opened.bottomOpen, true)
  assert.equal(opened.bottomTab, 'agent-log')
  const closed = setP7BottomOpen(opened, false)
  assert.equal(closed.bottomOpen, false)
  assert.equal(closed.bottomTab, 'agent-log', 'collapse keeps the selected tab')
})

test('shell: selecting a bottom tab always expands the panel', () => {
  const initial = createP7ShellUiState()
  const selected = selectP7BottomTab(initial, 'output')
  assert.equal(selected.bottomOpen, true)
  assert.equal(selected.bottomTab, 'output')
})

test('shell: profile-derived chrome never projects across Workspaces on the first frame', () => {
  const initial = createP7ShellUiState()
  const workspaceA = {
    ...initial,
    activity: 'run' as const,
    sidebarOpen: false,
    bottomOpen: true,
    bottomTab: 'output' as const,
    agentPanelOpen: false,
  }
  const scoped = createP7WorkspaceShellUiState(`workspace_${'a'.repeat(32)}`, workspaceA)

  assert.deepEqual(p7WorkspaceShellUiProjection(scoped, `workspace_${'a'.repeat(32)}`), workspaceA)
  assert.deepEqual(
    p7WorkspaceShellUiProjection(scoped, `workspace_${'b'.repeat(32)}`),
    createP7ShellUiState(),
  )
})

test('shell: conditional root and grid classes remain separate CSS tokens', () => {
  assert.equal(
    p7WorkbenchRootClassNames({
      density: 'compact',
      agentPanelVisible: false,
      quietChrome: true,
      reduceMotion: true,
    }),
    'p7-root p7-density-compact p7-agent-closed-body p7-quiet-chrome p7-reduce-motion',
  )
  assert.equal(
    p7ShellLayoutClassNames({ sidebarVisible: false, agentPanelVisible: false }),
    'p7-shell p7-sidebar-closed p7-agent-closed',
  )
})

test('shell: component host regions preserve the immutable workbench around their Slot', () => {
  const initial = { ...createP7ShellUiState(), bottomOpen: true, agentPanelOpen: true }
  assert.deepEqual(openP7WorkspaceComponentHost(initial, 'editor'), {
    ...initial,
    centerView: 'component',
    sidebarOpen: false,
    bottomOpen: false,
  })
  assert.deepEqual(
    openP7WorkspaceComponentHost({ ...initial, centerView: 'component' }, 'sidebar'),
    { ...initial, centerView: 'transcript', sidebarOpen: true },
  )
  assert.deepEqual(openP7WorkspaceComponentHost(initial, 'settings'), {
    ...initial,
    activity: 'settings',
    centerView: 'settings',
    sidebarOpen: false,
    bottomOpen: false,
    agentPanelOpen: false,
  })
  assert.equal(
    openP7WorkspaceComponentHost({ ...initial, centerView: 'component' }, 'status').centerView,
    'transcript',
  )
  assert.equal(
    closeP7WorkspaceComponentHost({ ...initial, centerView: 'component' }).centerView,
    'transcript',
  )
})

function mediaBlocks(css: string, query: string): string {
  const marker = `@media ${query}`
  const blocks: string[] = []
  let cursor = 0
  while (cursor < css.length) {
    const start = css.indexOf(marker, cursor)
    if (start < 0) break
    const open = css.indexOf('{', start + marker.length)
    assert.notEqual(open, -1, `${marker} must open a block`)
    let depth = 0
    let close = -1
    for (let index = open; index < css.length; index += 1) {
      if (css[index] === '{') depth += 1
      else if (css[index] === '}') {
        depth -= 1
        if (depth === 0) {
          close = index + 1
          break
        }
      }
    }
    assert.notEqual(close, -1, `${marker} must close its block`)
    blocks.push(css.slice(start, close))
    cursor = close
  }
  return blocks.join('\n')
}

test('shell: compact viewport CSS defines every visible panel combination', () => {
  const css = readFileSync(path.join(process.cwd(), 'app/desktop/p7-workbench.css'), 'utf8')
  const responsive = mediaBlocks(css, '(max-width: 920px)')
  assert.match(
    responsive,
    /\.p7-shell \{\s*grid-template-columns: 40px 180px minmax\(0, 1fr\) 252px;/,
  )
  assert.match(
    responsive,
    /\.p7-shell\.p7-sidebar-closed \{\s*grid-template-columns: 40px minmax\(0, 1fr\) 252px;/,
  )
  assert.match(
    responsive,
    /\.p7-shell\.p7-agent-closed \{\s*grid-template-columns: 40px 180px minmax\(0, 1fr\);/,
  )
  assert.match(
    responsive,
    /\.p7-shell\.p7-sidebar-closed\.p7-agent-closed \{\s*grid-template-columns: 40px minmax\(0, 1fr\);/,
  )
  assert.ok(css.includes('.p7-density-compact .p7-row'))
  assert.ok(css.includes('.p7-density-comfortable .p7-event-row'))
  assert.equal(css.includes('.p7-sidebar-row'), false)
  assert.equal(css.includes('.p7-agent-event'), false)
})

// ---------------------------------------------------------------------------
// 3. OMNIA widget expand/minimize
// ---------------------------------------------------------------------------

test('shell: OMNIA popover toggles and minimize keeps it closed', () => {
  const initial = createP7ShellUiState()
  assert.equal(initial.omniaPopoverOpen, false)
  assert.equal(initial.omniaMinimized, false)

  const opened = toggleP7OmniaPopover(initial)
  assert.equal(opened.omniaPopoverOpen, true)
  assert.equal(opened.omniaMinimized, false, 'opening must un-minimize')

  const closed = toggleP7OmniaPopover(opened)
  assert.equal(closed.omniaPopoverOpen, false)

  const minimized = minimizeP7Omnia(opened)
  assert.equal(minimized.omniaMinimized, true)
  assert.equal(minimized.omniaPopoverOpen, false)

  const expanded = expandP7Omnia(minimized)
  assert.equal(expanded.omniaMinimized, false)
  assert.equal(expanded.omniaPopoverOpen, true)
})

// ---------------------------------------------------------------------------
// 4. OMNIA real state mapping
// ---------------------------------------------------------------------------

test('omnia: idle when nothing is running and no collaboration is pending', () => {
  const snapshot = p7OmniaStateForLive({
    teamPhase: 'idle',
    teamRunState: null,
    livePhase: 'idle',
    liveVisible: true,
    pendingCollaborations: 0,
  })
  assert.equal(snapshot.state, 'idle')
  assert.equal(snapshot.dotTone, 'gray')
  assert.equal(snapshot.statusText, '空闲')
})

test('omnia: thinking while the parent proposes or replans', () => {
  const snapshot = p7OmniaStateForLive({
    teamPhase: 'parent_proposing',
    teamRunState: 'running',
    livePhase: 'idle',
    liveVisible: false,
    pendingCollaborations: 0,
  })
  assert.equal(snapshot.state, 'thinking')
  assert.equal(snapshot.statusText, '父 Agent 正在规划')
})

test('omnia: running while nodes execute', () => {
  const snapshot = p7OmniaStateForLive({
    teamPhase: 'node_running',
    teamRunState: 'running',
    livePhase: 'idle',
    liveVisible: false,
    pendingCollaborations: 0,
  })
  assert.equal(snapshot.state, 'running')
  assert.equal(snapshot.dotTone, 'purple')
})

test('omnia: running while a single-agent invocation streams', () => {
  const snapshot = p7OmniaStateForLive({
    teamPhase: 'idle',
    teamRunState: null,
    livePhase: 'running',
    liveVisible: true,
    pendingCollaborations: 0,
  })
  assert.equal(snapshot.state, 'running')
  assert.equal(snapshot.statusText, '正在生成')
})

test('omnia: completed only on a real succeeded team run', () => {
  const snapshot = p7OmniaStateForLive({
    teamPhase: 'completed',
    teamRunState: 'succeeded',
    livePhase: 'idle',
    liveVisible: false,
    pendingCollaborations: 0,
  })
  assert.equal(snapshot.state, 'completed')
  assert.equal(snapshot.dotTone, 'green')
  assert.equal(snapshot.statusText, '团队协作已完成')
})

test('omnia: blocked on every terminal failure state', () => {
  const terminal: readonly TeamRunState[] = [
    'failed',
    'cancelled',
    'budget_exhausted',
    'cannot_complete',
    'unknown',
  ]
  for (const runState of terminal) {
    const snapshot = p7OmniaStateForLive({
      teamPhase: 'failed',
      teamRunState: runState,
      livePhase: 'idle',
      liveVisible: false,
      pendingCollaborations: 0,
    })
    assert.equal(snapshot.state, 'blocked', `runState ${runState} must map to blocked`)
    assert.equal(snapshot.dotTone, 'red')
  }
})

test('omnia: review-required when the blackboard reports pending collaborations', () => {
  const snapshot = p7OmniaStateForLive({
    teamPhase: 'blackboard_updated',
    teamRunState: 'running',
    livePhase: 'idle',
    liveVisible: false,
    pendingCollaborations: 2,
  })
  assert.equal(snapshot.state, 'review-required')
  assert.equal(snapshot.dotTone, 'amber')
  assert.equal(snapshot.statusText, '2 个协作请求等待处理')
})

test('omnia: review-required outranks running while the run waits', () => {
  const snapshot = p7OmniaStateForLive({
    teamPhase: 'node_running',
    teamRunState: 'running',
    livePhase: 'idle',
    liveVisible: false,
    pendingCollaborations: 1,
  })
  assert.equal(snapshot.state, 'review-required')
})

test('omnia: an active live invocation shadows an old team terminal state', () => {
  const snapshot = p7OmniaStateForLive({
    teamPhase: 'completed',
    teamRunState: 'succeeded',
    livePhase: 'running',
    liveVisible: true,
    pendingCollaborations: 0,
  })
  assert.equal(snapshot.state, 'running')
  assert.equal(snapshot.statusText, '正在生成')
})

test('omnia: live invoking shadows a completed team run', () => {
  const snapshot = p7OmniaStateForLive({
    teamPhase: 'completed',
    teamRunState: 'succeeded',
    livePhase: 'starting_identity',
    liveVisible: true,
    pendingCollaborations: 0,
  })
  assert.equal(snapshot.state, 'thinking')
  assert.equal(snapshot.statusText, '正在发起调用')
})

test('omnia: old team terminal returns once the live stream finishes', () => {
  const snapshot = p7OmniaStateForLive({
    teamPhase: 'completed',
    teamRunState: 'succeeded',
    livePhase: 'terminal',
    liveVisible: true,
    pendingCollaborations: 0,
  })
  assert.equal(snapshot.state, 'completed')
})

test('omnia: never fabricates sleeping/surprised/goodbye from real state', () => {
  const phases: readonly DesktopTeamPhase[] = [
    'idle',
    'preparing',
    'parent_proposing',
    'host_validating',
    'wave_starting',
    'node_running',
    'blackboard_updated',
    'parent_replanning',
    'parent_synthesizing',
    'cancelling',
    'completed',
    'budget_exhausted',
    'cannot_complete',
    'cancelled',
    'unknown',
    'failed',
  ]
  const livePhases: readonly DesktopInvocationPhase[] = [
    'idle',
    'send',
    'starting_identity',
    'identity',
    'running',
    'cancelling',
    'cancelled',
    'terminal',
    'convergence',
  ]
  const runStates: readonly (TeamRunState | null)[] = [
    null,
    'preparing',
    'running',
    'cancelling',
    'succeeded',
    'failed',
    'cancelled',
    'unknown',
    'budget_exhausted',
    'cannot_complete',
  ]
  for (const teamPhase of phases) {
    for (const livePhase of livePhases) {
      for (const teamRunState of runStates) {
        const snapshot = p7OmniaStateForLive({
          teamPhase,
          teamRunState,
          livePhase,
          liveVisible: true,
          pendingCollaborations: 0,
        })
        assert.ok(
          snapshot.state !== 'sleeping' &&
            snapshot.state !== 'surprised' &&
            snapshot.state !== 'goodbye',
          `state ${teamPhase}/${livePhase}/${teamRunState} must not fabricate ${snapshot.state}`,
        )
      }
    }
  }
})

// ---------------------------------------------------------------------------
// 5. No fake file/diff/terminal when untrusted
// ---------------------------------------------------------------------------

test('views: code and diff are unavailable without a trusted file source', () => {
  const presence = p7DataSourcePresence()
  assert.equal(presence.files, false)
  assert.equal(presence.diff, false)

  const code = p7ViewAvailability('code', presence)
  assert.equal(code.available, false)
  assert.equal(code.reason, P7_NO_TRUSTED_SOURCE_REASON)

  const diff = p7ViewAvailability('diff', presence)
  assert.equal(diff.available, false)
  assert.equal(diff.reason, P7_NO_TRUSTED_SOURCE_REASON)
})

test('views: transcript and brief are always real views', () => {
  const presence = p7DataSourcePresence()
  assert.equal(p7ViewAvailability('transcript', presence).available, true)
  assert.equal(p7ViewAvailability('transcript', presence).reason, null)
  assert.equal(p7ViewAvailability('brief', presence).available, true)
  assert.equal(p7ViewAvailability('brief', presence).reason, null)
})

test('views: terminal and problems are unavailable without a trusted source', () => {
  const presence = p7DataSourcePresence()
  const terminal = p7BottomTabAvailability('terminal', presence)
  assert.equal(terminal.available, false)
  assert.equal(terminal.reason, P7_NO_TRUSTED_SOURCE_REASON)
  const problems = p7BottomTabAvailability('problems', presence)
  assert.equal(problems.available, false)
  assert.equal(problems.reason, P7_NO_TRUSTED_SOURCE_REASON)
})

test('views: output and agent-log are real (operation log and raw event stream)', () => {
  const presence = p7DataSourcePresence()
  assert.equal(p7BottomTabAvailability('output', presence).available, true)
  assert.equal(p7BottomTabAvailability('agent-log', presence).available, true)
})

test('views: flipping the presence flag unlocks the view', () => {
  const presence = { ...p7DataSourcePresence(), files: true }
  assert.equal(p7ViewAvailability('code', presence).available, true)
  assert.equal(p7ViewAvailability('code', presence).reason, null)
})

// ---------------------------------------------------------------------------
// 6. Agent panel state rendering (feed projection)
// ---------------------------------------------------------------------------

function feedItem(items: readonly P7AgentFeedItem[], key: string): P7AgentFeedItem | undefined {
  return items.find((item) => item.key === key)
}

function asTask(
  item: P7AgentFeedItem | undefined,
): Extract<P7AgentFeedItem, { kind: 'task' }> | undefined {
  return item?.kind === 'task' ? item : undefined
}

function asEvent(
  item: P7AgentFeedItem | undefined,
): Extract<P7AgentFeedItem, { kind: 'event' }> | undefined {
  return item?.kind === 'event' ? item : undefined
}

function asResult(
  item: P7AgentFeedItem | undefined,
): Extract<P7AgentFeedItem, { kind: 'result' }> | undefined {
  return item?.kind === 'result' ? item : undefined
}

test('feed: an active live invocation suppresses old team artifacts', () => {
  const items = projectP7AgentFeed({
    agentName: '父 Agent',
    teamPhase: 'completed',
    teamRunState: 'succeeded',
    taskText: '旧的团队任务',
    nodes: [node({ statusText: '已完成' })],
    collaborationLines: ['qa ← security：旧请求'],
    planRevisionId: 'plan-rev-old',
    waveId: 'wave-old',
    declaredExecution: 'serial',
    effectiveExecution: 'serial',
    planSummary: null,
    parentFinalAnswer: '旧的最终回答',
    liveText: '新的单 Agent 流…',
    liveVisible: true,
    liveActive: true,
    consumedProviderCalls: 5,
    maximumProviderCalls: 16,
  })
  const task = asTask(feedItem(items, 'task'))
  assert.ok(task)
  assert.equal(task.title, '单 Agent 生成')
  assert.equal(feedItem(items, 'plan-plan-rev-old'), undefined)
  assert.equal(feedItem(items, 'wave-wave-old'), undefined)
  assert.equal(feedItem(items, 'node-1'), undefined)
  assert.equal(feedItem(items, 'result'), undefined)
  const live = asEvent(feedItem(items, 'live'))
  assert.ok(live)
  assert.equal(live.meta, '新的单 Agent 流…')
})

test('feed: node report excerpt is truncated, never collapsed to a marker', () => {
  const longReport = 'r'.repeat(400)
  const items = projectP7AgentFeed({
    agentName: '父 Agent',
    teamPhase: 'node_running',
    teamRunState: 'running',
    taskText: '任务',
    nodes: [node({ statusText: '已完成', report: longReport })],
    collaborationLines: [],
    planRevisionId: null,
    waveId: null,
    declaredExecution: null,
    effectiveExecution: null,
    planSummary: null,
    parentFinalAnswer: null,
    liveText: '',
    liveVisible: false,
    liveActive: false,
    consumedProviderCalls: 0,
    maximumProviderCalls: 16,
  })
  const row = asEvent(feedItem(items, 'node-1'))
  assert.ok(row)
  assert.ok(row.meta?.startsWith(`${'r'.repeat(240)}…`))
  assert.ok(!row.meta?.includes('含报告'))
  assert.equal(p7Truncate('短文本', 240), '短文本')
  assert.equal(p7Truncate('12345', 3), '123…')
})

test('feed: no task, idle team renders a real empty task row', () => {
  const items = projectP7AgentFeed({
    agentName: '父 Agent',
    teamPhase: 'idle',
    teamRunState: null,
    taskText: null,
    nodes: [],
    collaborationLines: [],
    planRevisionId: null,
    waveId: null,
    declaredExecution: null,
    effectiveExecution: null,
    planSummary: null,
    parentFinalAnswer: null,
    liveText: '',
    liveVisible: false,
    consumedProviderCalls: 0,
    maximumProviderCalls: 0,
  })
  assert.equal(items.length, 1)
  const task = asTask(feedItem(items, 'task'))
  assert.ok(task)
  assert.equal(task.title, '还没有任务')
  assert.equal(task.detail, null)
})

test('feed: running team renders real node rows with current tone and tokens', () => {
  const items = projectP7AgentFeed({
    agentName: '父 Agent',
    teamPhase: 'node_running',
    teamRunState: 'running',
    taskText: '审阅桌面运行时启动链路',
    nodes: [
      node({
        nodeId: 'node-1',
        employeeRoleId: 'security',
        ordinal: 1,
        statusText: '已完成',
        totalTokens: 1200,
      }),
      node({ nodeId: 'node-2', employeeRoleId: 'qa', ordinal: 2, statusText: '运行中' }),
    ],
    collaborationLines: [],
    planRevisionId: 'plan-rev-1',
    waveId: 'wave-1',
    declaredExecution: 'serial',
    effectiveExecution: 'serial',
    planSummary: null,
    parentFinalAnswer: null,
    liveText: '',
    liveVisible: false,
    consumedProviderCalls: 2,
    maximumProviderCalls: 16,
  })
  const task = asTask(feedItem(items, 'task'))
  assert.ok(task)
  assert.equal(task.title, '审阅桌面运行时启动链路')
  const plan = asEvent(feedItem(items, 'plan-plan-rev-1'))
  assert.ok(plan)
  assert.equal(plan.meta, 'plan-rev-1')

  const security = asEvent(feedItem(items, 'node-1'))
  assert.ok(security)
  assert.equal(security.label, '安全架构师 · 已完成')
  assert.equal(security.tone, 'ok')
  const qa = asEvent(feedItem(items, 'node-2'))
  assert.ok(qa)
  assert.equal(qa.label, '测试工程师 · 运行中')
  assert.equal(qa.tone, 'current')
  assert.equal(qa.meta, null, 'running node has no fabricated duration')
})

test('feed: completed run renders the real final answer result', () => {
  const items = projectP7AgentFeed({
    agentName: '父 Agent',
    teamPhase: 'completed',
    teamRunState: 'succeeded',
    taskText: '任务',
    nodes: [node({ statusText: '已完成' })],
    collaborationLines: [],
    planRevisionId: null,
    waveId: null,
    declaredExecution: null,
    effectiveExecution: null,
    planSummary: null,
    parentFinalAnswer: '这是父 Agent 的真实最终回答',
    liveText: '',
    liveVisible: false,
    consumedProviderCalls: 7,
    maximumProviderCalls: 16,
  })
  const result = asResult(feedItem(items, 'result'))
  assert.ok(result)
  assert.equal(result.answer, '这是父 Agent 的真实最终回答')
  assert.equal(result.meta, '已用 7 / 16 次调用')
})

test('feed: failed node renders error tone', () => {
  const items = projectP7AgentFeed({
    agentName: '父 Agent',
    teamPhase: 'failed',
    teamRunState: 'failed',
    taskText: '任务',
    nodes: [node({ statusText: '失败', report: 'Provider 拒绝连接' })],
    collaborationLines: [],
    planRevisionId: null,
    waveId: null,
    declaredExecution: null,
    effectiveExecution: null,
    planSummary: null,
    parentFinalAnswer: null,
    liveText: '',
    liveVisible: false,
    consumedProviderCalls: 1,
    maximumProviderCalls: 16,
  })
  const failed = asEvent(feedItem(items, 'node-1'))
  assert.ok(failed)
  assert.equal(failed.tone, 'error')
  assert.ok(failed.meta?.includes('Provider 拒绝连接'))
})

test('feed: collaboration lines and live text render as real events', () => {
  const items = projectP7AgentFeed({
    agentName: '父 Agent',
    teamPhase: 'blackboard_updated',
    teamRunState: 'running',
    taskText: '任务',
    nodes: [],
    collaborationLines: ['qa ← security：需要测试矩阵'],
    planRevisionId: null,
    waveId: null,
    declaredExecution: null,
    effectiveExecution: null,
    planSummary: null,
    parentFinalAnswer: null,
    liveText: '正在流式输出…',
    liveVisible: true,
    consumedProviderCalls: 0,
    maximumProviderCalls: 16,
  })
  const collab = asEvent(items.find((item) => item.key.startsWith('collab-')))
  assert.ok(collab)
  assert.equal(collab.meta, 'qa ← security：需要测试矩阵')
  const live = asEvent(feedItem(items, 'live'))
  assert.ok(live)
  assert.equal(live.label, '正在生成')
  assert.equal(live.meta, '正在流式输出…')
})

test('feed: live text is hidden when the view is parked away from origin', () => {
  const items = projectP7AgentFeed({
    agentName: '父 Agent',
    teamPhase: 'idle',
    teamRunState: null,
    taskText: null,
    nodes: [],
    collaborationLines: [],
    planRevisionId: null,
    waveId: null,
    declaredExecution: null,
    effectiveExecution: null,
    planSummary: null,
    parentFinalAnswer: null,
    liveText: '不能显示的流',
    liveVisible: false,
    consumedProviderCalls: 0,
    maximumProviderCalls: 0,
  })
  assert.equal(feedItem(items, 'live'), undefined)
})

test('feed: node tone mapping is conservative', () => {
  assert.equal(p7NodeTone('运行中'), 'current')
  assert.equal(p7NodeTone('正在停止'), 'current')
  assert.equal(p7NodeTone('已完成'), 'ok')
  assert.equal(p7NodeTone('失败'), 'error')
  assert.equal(p7NodeTone('静默'), 'neutral')
  assert.equal(p7NodeTone('等待'), 'neutral')
  assert.equal(p7NodeTone('需要协作'), 'neutral')
  assert.equal(p7NodeTone('状态未知'), 'neutral')
})

// ---------------------------------------------------------------------------
// 7. Threads
// ---------------------------------------------------------------------------

const THREAD_CONVERSATIONS: readonly DesktopConversation[] = [
  {
    id: 'conv-a',
    workspaceId: 'ws-1',
    title: '会话 A',
    state: 'active',
    rowVersion: 1,
    createdAt: '2026-08-26T00:00:00Z',
    updatedAt: '2026-08-26T00:00:00Z',
  },
  {
    id: 'conv-b',
    workspaceId: 'ws-1',
    title: '会话 B',
    state: 'active',
    rowVersion: 1,
    createdAt: '2026-08-26T00:00:00Z',
    updatedAt: '2026-08-26T00:00:00Z',
  },
  {
    id: 'conv-archived',
    workspaceId: 'ws-1',
    title: '已归档',
    state: 'archived',
    rowVersion: 1,
    createdAt: '2026-08-26T00:00:00Z',
    updatedAt: '2026-08-26T00:00:00Z',
  },
]

test('threads: archived conversations never appear', () => {
  const rows = projectP7ThreadRows({
    conversations: THREAD_CONVERSATIONS,
    selectedConversationId: 'conv-a',
    teamPhase: 'idle',
    teamOriginConversationId: null,
    live: { conversationId: null, invocationId: null, phase: 'idle' },
  })
  assert.equal(rows.length, 2)
  assert.ok(rows.every((row) => row.conversationId !== 'conv-archived'))
})

test('threads: idle threads render real 空闲 status', () => {
  const rows = projectP7ThreadRows({
    conversations: THREAD_CONVERSATIONS,
    selectedConversationId: 'conv-a',
    teamPhase: 'idle',
    teamOriginConversationId: null,
    live: { conversationId: null, invocationId: null, phase: 'idle' },
  })
  assert.equal(rows[0]?.statusText, '空闲')
  assert.equal(rows[0]?.dotTone, 'gray')
  assert.equal(rows[0]?.active, true)
  assert.equal(rows[1]?.active, false)
})

test('threads: the team-bound conversation shows the real team phase', () => {
  const rows = projectP7ThreadRows({
    conversations: THREAD_CONVERSATIONS,
    selectedConversationId: null,
    teamPhase: 'node_running',
    teamOriginConversationId: 'conv-a',
    live: { conversationId: null, invocationId: null, phase: 'idle' },
  })
  assert.equal(rows[0]?.statusText, '运行中')
  assert.equal(rows[0]?.dotTone, 'purple')
  assert.equal(rows[1]?.statusText, '空闲', 'only the bound thread carries team status')
})

test('threads: the live-invocation conversation shows the real invocation phase', () => {
  const rows = projectP7ThreadRows({
    conversations: THREAD_CONVERSATIONS,
    selectedConversationId: null,
    teamPhase: 'idle',
    teamOriginConversationId: null,
    live: { conversationId: 'conv-b', invocationId: 'inv-9', phase: 'running' },
  })
  assert.equal(rows[1]?.statusText, '正在生成')
  assert.equal(rows[1]?.dotTone, 'purple')
})

test('threads: terminal team phase renders the real failure label', () => {
  const rows = projectP7ThreadRows({
    conversations: THREAD_CONVERSATIONS,
    selectedConversationId: null,
    teamPhase: 'budget_exhausted',
    teamOriginConversationId: 'conv-a',
    live: { conversationId: null, invocationId: null, phase: 'idle' },
  })
  assert.equal(rows[0]?.statusText, '预算耗尽')
})

// ---------------------------------------------------------------------------
// 8. Run history
// ---------------------------------------------------------------------------

function run(overrides: Partial<DesktopTeamRun>): DesktopTeamRun {
  return {
    id: 'teamrun-1',
    workspaceId: 'ws-1',
    conversationId: 'conv-a',
    mode: 'team',
    state: 'succeeded',
    staffingAuthority: 'parent_proposal',
    currentPlanRevisionId: 'plan-rev-1',
    currentWaveId: null,
    dispatchedParticipantCount: 3,
    maximumProviderCalls: 16,
    maximumWallTimeMs: 600000,
    maximumConcurrentCalls: 3,
    maximumInputCharacters: 16384,
    maximumOutputCharacters: 32768,
    consumedProviderCalls: 5,
    task: '任务',
    allowedSpecialistRoleIds: [],
    createdAt: '2026-08-26T00:00:00Z',
    updatedAt: '2026-08-26T00:00:00Z',
    ...overrides,
  }
}

test('runs: newest first with real state labels', () => {
  const rows = projectP7RunRows(
    [
      run({ id: 'old', createdAt: '2026-08-25T00:00:00Z', state: 'cancelled' }),
      run({ id: 'new', createdAt: '2026-08-26T00:00:00Z', state: 'failed' }),
    ],
    'new',
  )
  assert.equal(rows[0]?.run.id, 'new')
  assert.equal(rows[0]?.stateLabel, '失败')
  assert.equal(rows[0]?.active, true)
  assert.equal(rows[1]?.stateLabel, '已取消')
  assert.equal(rows[1]?.active, false)
})

test('runs: every terminal state has a real Chinese label', () => {
  assert.equal(p7RunStateLabel('preparing'), '准备中')
  assert.equal(p7RunStateLabel('running'), '运行中')
  assert.equal(p7RunStateLabel('cancelling'), '正在停止')
  assert.equal(p7RunStateLabel('succeeded'), '已完成')
  assert.equal(p7RunStateLabel('failed'), '失败')
  assert.equal(p7RunStateLabel('cancelled'), '已取消')
  assert.equal(p7RunStateLabel('unknown'), '状态未知')
  assert.equal(p7RunStateLabel('budget_exhausted'), '预算耗尽')
  assert.equal(p7RunStateLabel('cannot_complete'), '无法完成')
  assert.equal(p7RunStateLabel('mystery-state'), 'mystery-state', 'unknown degrades raw')
})

test('run-history: rows never project across workspaces, even on the first frame', () => {
  const rows = [run({ id: 'teamrun-1', task: '旧工作空间任务', state: 'running' })]
  const switched = p7RunHistoryProjection({
    historyWorkspaceId: 'ws-1',
    viewWorkspaceId: 'ws-2',
    status: 'ready',
    rows,
  })
  assert.equal(
    switched.rows.length,
    0,
    'the first frame after a workspace switch must not render previous rows',
  )
  assert.equal(switched.status, 'loading', 'the panel reads as loading, not ready')
  const matching = p7RunHistoryProjection({
    historyWorkspaceId: 'ws-1',
    viewWorkspaceId: 'ws-1',
    status: 'ready',
    rows,
  })
  assert.equal(matching.rows.length, 1)
  assert.equal(matching.status, 'ready')
  const failed = p7RunHistoryProjection({
    historyWorkspaceId: 'ws-1',
    viewWorkspaceId: 'ws-2',
    status: 'error',
    rows,
  })
  assert.equal(failed.rows.length, 0, 'error rows are workspace-bound too')
})

// ---------------------------------------------------------------------------
// 9. Blackboard projection
// ---------------------------------------------------------------------------

const BLACKBOARD: PersonalTeamBlackboard = {
  teamRunId: 'teamrun-1',
  workspaceId: 'ws-1',
  ownerObjective: '改进桌面工作台',
  currentPlanRevisionId: 'plan-rev-2',
  assignments: [
    {
      assignmentId: 'assignment-1',
      employeeRoleId: 'qa',
      objective: '构建测试矩阵',
      state: 'needs_collaboration',
      waveId: 'wave-1',
      dependsOnAssignmentIds: [],
      expectedOutput: '矩阵',
    },
  ],
  reports: [
    {
      assignmentId: 'assignment-1',
      employeeRoleId: 'qa',
      status: 'needs_collaboration',
      report: '需要安全侧输入',
    },
  ],
  collaborationRequests: [
    {
      id: 'teamcollab-qa_0',
      fromAssignmentId: 'assignment-1',
      fromEmployeeRoleId: 'qa',
      targetRoleId: 'security',
      question: '请提供威胁模型',
      reason: '测试依赖',
      parentDecision: 'pending',
      resolvedAssignmentId: null,
    },
  ],
}

test('blackboard: projects real fields with role and decision labels', () => {
  const section = projectP7Blackboard(BLACKBOARD)
  assert.equal(section.ownerObjective, '改进桌面工作台')
  assert.equal(section.currentPlanRevisionId, 'plan-rev-2')
  assert.equal(section.assignments[0]?.roleLabel, '测试工程师')
  assert.equal(section.assignments[0]?.stateLabel, '需要协作')
  assert.equal(section.reports[0]?.roleLabel, '测试工程师')
  assert.equal(section.collaborationRequests[0]?.fromRoleLabel, '测试工程师')
  assert.equal(section.collaborationRequests[0]?.targetRoleLabel, '安全架构师')
  assert.equal(section.collaborationRequests[0]?.decisionLabel, '待处理')
  assert.equal(section.collaborationRequests[0]?.pending, true)
})

test('blackboard: resolved collaboration is not pending', () => {
  const section = projectP7Blackboard({
    ...BLACKBOARD,
    collaborationRequests: [
      { ...BLACKBOARD.collaborationRequests[0]!, parentDecision: 'accept_start' },
    ],
  })
  assert.equal(section.collaborationRequests[0]?.decisionLabel, '已接受 · 新角色')
  assert.equal(section.collaborationRequests[0]?.pending, false)
})

test('blackboard: unknown roles and states degrade to raw values, never fabricated', () => {
  const section = projectP7Blackboard({
    ...BLACKBOARD,
    assignments: [
      {
        assignmentId: 'assignment-9',
        employeeRoleId: 'mystery-role',
        objective: '未知角色任务',
        state: 'mystery-state',
        waveId: null,
        dependsOnAssignmentIds: [],
        expectedOutput: '',
      },
    ],
  })
  assert.equal(section.assignments[0]?.roleLabel, 'mystery-role')
  assert.equal(section.assignments[0]?.stateLabel, 'mystery-state')
  assert.equal(p7AssignmentStateLabel('completed'), '已完成')
  assert.equal(p7AssignmentStateLabel('blocked'), '受阻')
})

test('blackboard: decision labels cover the closed set', () => {
  assert.equal(p7CollaborationDecisionLabel('pending'), '待处理')
  assert.equal(p7CollaborationDecisionLabel('accept_start'), '已接受 · 新角色')
  assert.equal(p7CollaborationDecisionLabel('handle_self'), '已自行处理')
  assert.equal(p7CollaborationDecisionLabel('merge_existing'), '已并入现有')
  assert.equal(p7CollaborationDecisionLabel('decline'), '已拒绝')
})

test('labels: role labels never fall back to English for known roles', () => {
  assert.equal(p7RoleLabel('parent'), '父 Agent')
  assert.equal(p7RoleLabel('frontend'), '前端工程师')
  assert.equal(p7RoleLabel('product'), '产品经理')
  assert.equal(p7RoleLabel('unknown-role'), 'unknown-role')
})

test('labels: activity, view and bottom tab labels are stable', () => {
  assert.equal(p7ActivityLabel('explorer'), '资源管理器')
  assert.equal(p7ActivityLabel('run'), '运行与调试')
  assert.equal(p7ActivityLabel('blackboard'), '计划与黑板')
  assert.equal(p7CenterViewLabel('transcript'), '会话记录')
  assert.equal(p7CenterViewLabel('brief'), '任务简报')
  assert.equal(p7CenterViewLabel('diff'), '审阅变更')
  assert.equal(p7BottomTabLabel('agent-log'), 'Agent Log')
})

// ---------------------------------------------------------------------------
// 10. Event log lines (real raw events)
// ---------------------------------------------------------------------------

test('agent-log: team event lines carry only real fields', () => {
  const event: DesktopTeamRunEvent = {
    type: 'node_terminal',
    workspaceId: 'ws-1',
    teamRunId: 'teamrun-1',
    nodeId: 'node-7',
    employeeRoleId: 'qa',
    totalTokens: 3400,
    consumedProviderCalls: 4,
    maximumProviderCalls: 16,
  }
  const line = p7TeamEventLogLine(event)
  assert.ok(line.includes('node_terminal'))
  assert.ok(line.includes('teamrun-1'))
  assert.ok(line.includes('node node-7'))
  assert.ok(line.includes('3400 tokens'))
  assert.ok(line.includes('calls 4/16'))
})

test('agent-log: conversation event lines carry only real fields', () => {
  const event: DesktopConversationEvent = {
    type: 'delta',
    invocationId: 'inv-3',
    text: '一段真实增量文本',
  }
  const line = p7ConversationEventLogLine(event)
  assert.ok(line.includes('invocation inv-3'))
  assert.ok(line.includes('delta 8 chars'))
})

test('agent-log: error codes and redacted errors are visible', () => {
  const team = p7TeamEventLogLine({
    type: 'unknown',
    workspaceId: 'ws-1',
    teamRunId: 'teamrun-2',
    errorCode: 'desktop_team_run_terminal',
  })
  assert.ok(team.includes('error desktop_team_run_terminal'))
  const conversation = p7ConversationEventLogLine({
    type: 'error',
    invocationId: 'inv-4',
    errorCode: 'desktop_provider_not_found',
    errorRedacted: 'Provider 不可用',
  })
  assert.ok(conversation.includes('error desktop_provider_not_found'))
  assert.ok(conversation.includes('Provider 不可用'))
})

// ---------------------------------------------------------------------------
// 12. Live-activity gating (P1-3: no invocation id required)
// ---------------------------------------------------------------------------

test('live-active: starting_identity without an invocation id is active', () => {
  assert.equal(
    p7LiveActive({
      liveVisible: true,
      live: { conversationId: 'conv-a', invocationId: null, phase: 'starting_identity' },
    }),
    true,
    'beginDesktopLiveSend enters starting_identity before any invocation id exists',
  )
  assert.equal(
    p7LiveActive({
      liveVisible: true,
      live: { conversationId: 'conv-a', invocationId: null, phase: 'send' },
    }),
    true,
  )
  assert.equal(
    p7LiveActive({
      liveVisible: true,
      live: { conversationId: 'conv-a', invocationId: null, phase: 'cancelling' },
    }),
    true,
  )
})

test('live-active: idle and terminal phases are not active', () => {
  assert.equal(
    p7LiveActive({
      liveVisible: true,
      live: { conversationId: 'conv-a', invocationId: null, phase: 'idle' },
    }),
    false,
  )
  assert.equal(
    p7LiveActive({
      liveVisible: true,
      live: { conversationId: 'conv-a', invocationId: 'inv-9', phase: 'terminal' },
    }),
    false,
  )
})

test('live-active: a parked view is never active even while running', () => {
  assert.equal(
    p7LiveActive({
      liveVisible: false,
      live: { conversationId: 'conv-a', invocationId: 'inv-9', phase: 'running' },
    }),
    false,
  )
})

// ---------------------------------------------------------------------------
// 13. OMNIA pending collaborations must come from the live run's board
// ---------------------------------------------------------------------------

test('omnia-pending: no live run and no board yield zero', () => {
  assert.equal(p7LivePendingCollaborations(true, null, null), 0)
  assert.equal(p7LivePendingCollaborations(true, 'teamrun-1', null), 0)
})

test('omnia-pending: a history board for another run never drives OMNIA', () => {
  const historyBoard: PersonalTeamBlackboard = {
    ...BLACKBOARD,
    teamRunId: 'teamrun-2',
    collaborationRequests: [{ ...BLACKBOARD.collaborationRequests[0]!, parentDecision: 'pending' }],
  }
  assert.equal(
    p7LivePendingCollaborations(true, 'teamrun-1', historyBoard),
    0,
    'browsing history must not move the live widget',
  )
})

test('omnia-pending: the matching live board drives the real count', () => {
  assert.equal(p7LivePendingCollaborations(true, 'teamrun-1', BLACKBOARD), 1)
})

test('omnia-pending: a stale board is ignored once a new run starts', () => {
  assert.equal(
    p7LivePendingCollaborations(true, 'teamrun-9', BLACKBOARD),
    0,
    'liveRunId advanced past the loaded board',
  )
})

test('omnia-pending: viewing another conversation zeroes the count', () => {
  assert.equal(
    p7LivePendingCollaborations(false, 'teamrun-1', BLACKBOARD),
    0,
    'the origin run board must not leak into another conversation',
  )
})

test('omnia-pending: viewing another conversation also zeroes a stale board', () => {
  assert.equal(p7LivePendingCollaborations(false, null, BLACKBOARD), 0)
})

// ---------------------------------------------------------------------------
// 14. Task-brief board selection (live slot lifecycle)
// ---------------------------------------------------------------------------

test('brief: the live board wins only while it is current for the view', () => {
  const selection = p7BriefBoardSelection({
    liveCurrent: true,
    liveBoard: BLACKBOARD,
    liveStatus: 'ready',
    historyBoard: null,
    historyStatus: 'idle',
  })
  assert.equal(selection.source, 'live')
  assert.equal(selection.board, BLACKBOARD)
})

test('brief: after the run ends, the browsed history board takes over', () => {
  const historyBoard: PersonalTeamBlackboard = { ...BLACKBOARD, teamRunId: 'teamrun-2' }
  const selection = p7BriefBoardSelection({
    liveCurrent: false,
    liveBoard: null,
    liveStatus: 'idle',
    historyBoard,
    historyStatus: 'ready',
  })
  assert.equal(selection.source, 'history')
  assert.equal(selection.board, historyBoard)
})

test('brief: a live board shown while current hides any history board', () => {
  const historyBoard: PersonalTeamBlackboard = { ...BLACKBOARD, teamRunId: 'teamrun-2' }
  const selection = p7BriefBoardSelection({
    liveCurrent: true,
    liveBoard: BLACKBOARD,
    liveStatus: 'ready',
    historyBoard,
    historyStatus: 'ready',
  })
  assert.equal(selection.source, 'live')
})

test('brief: switching conversation shows nothing from the origin run', () => {
  const selection = p7BriefBoardSelection({
    liveCurrent: false,
    liveBoard: BLACKBOARD,
    liveStatus: 'ready',
    historyBoard: null,
    historyStatus: 'idle',
  })
  assert.equal(selection.source, 'none')
  assert.equal(selection.board, null)
})

test('brief: a loading live slot keeps the view loading, not stale', () => {
  const historyBoard: PersonalTeamBlackboard = { ...BLACKBOARD, teamRunId: 'teamrun-2' }
  const selection = p7BriefBoardSelection({
    liveCurrent: true,
    liveBoard: null,
    liveStatus: 'loading',
    historyBoard,
    historyStatus: 'ready',
  })
  assert.equal(selection.source, 'live')
  assert.equal(selection.status, 'loading')
})

// ---------------------------------------------------------------------------
// 11. Status bar counters
// ---------------------------------------------------------------------------

test('statusbar: running count is real and additive', () => {
  assert.equal(
    p7RunningCount({
      teamPhase: 'idle',
      live: { conversationId: null, invocationId: null, phase: 'idle' },
    }),
    0,
  )
  assert.equal(
    p7RunningCount({
      teamPhase: 'node_running',
      live: { conversationId: null, invocationId: null, phase: 'idle' },
    }),
    1,
  )
  assert.equal(
    p7RunningCount({
      teamPhase: 'idle',
      live: { conversationId: 'conv-a', invocationId: 'inv-1', phase: 'running' },
    }),
    1,
  )
  assert.equal(
    p7RunningCount({
      teamPhase: 'idle',
      live: { conversationId: 'conv-a', invocationId: null, phase: 'starting_identity' },
    }),
    1,
    'the send window counts even before the invocation id exists',
  )
  assert.equal(
    p7RunningCount({
      teamPhase: 'node_running',
      live: { conversationId: 'conv-a', invocationId: 'inv-1', phase: 'running' },
    }),
    2,
  )
  assert.equal(
    p7RunningCount({
      teamPhase: 'completed',
      live: { conversationId: null, invocationId: null, phase: 'terminal' },
    }),
    0,
    'terminal phases are not counted as running',
  )
})

test('statusbar: team phase labels cover every phase', () => {
  assert.equal(p7TeamPhaseLabel('preparing'), '准备中')
  assert.equal(p7TeamPhaseLabel('parent_proposing'), '规划中')
  assert.equal(p7TeamPhaseLabel('parent_replanning'), '规划中')
  assert.equal(p7TeamPhaseLabel('wave_starting'), '运行中')
  assert.equal(p7TeamPhaseLabel('node_running'), '运行中')
  assert.equal(p7TeamPhaseLabel('parent_synthesizing'), '运行中')
  assert.equal(p7TeamPhaseLabel('blackboard_updated'), '运行中')
  assert.equal(p7TeamPhaseLabel('cancelling'), '正在停止')
  assert.equal(p7TeamPhaseLabel('completed'), '已完成')
  assert.equal(p7TeamPhaseLabel('budget_exhausted'), '预算耗尽')
  assert.equal(p7TeamPhaseLabel('cannot_complete'), '无法完成')
  assert.equal(p7TeamPhaseLabel('cancelled'), '已取消')
  assert.equal(p7TeamPhaseLabel('unknown'), '状态未知')
  assert.equal(p7TeamPhaseLabel('failed'), '失败')
  assert.equal(p7TeamPhaseLabel('idle'), '空闲')
})
