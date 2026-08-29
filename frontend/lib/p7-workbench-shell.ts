/**
 * P7.0 Wave 1 — editor-first workbench shell.
 *
 * Pure, side-effect-free UI state and projection helpers for the desktop
 * workbench shell. Kept outside React so every rule below is unit-testable
 * with the repository's node:test convention.
 *
 * Rules enforced here (and by the tests in p7-workbench-shell.test.ts):
 * - Sidebar activities are mutually exclusive; re-clicking the active item
 *   closes the sidebar (IDE behavior from the approved editor-first design).
 * - Views without a trusted data source (Code/Diff/Terminal/Problems/Output/
 *   Search/Source) render a real unavailable state and never simulated
 *   content. The availability flags are derived from a data-source presence
 *   record that only the wiring layer may flip.
 * - The OMNIA widget state is derived exclusively from real live state:
 *   team-run phase/run state, invocation phase, and the authoritative pending
 *   collaboration count from the blackboard. The mapper never emits
 *   sleeping/surprised/goodbye, because no real trigger exists in Wave 1.
 * - Feed, thread, run-history and blackboard projections render only real
 *   fields; unknown identifiers degrade to their raw value instead of a
 *   fabricated label.
 */

import type {
  DesktopConversation,
  DesktopConversationEvent,
  DesktopTeamCollaborationRequest,
  DesktopTeamRun,
  DesktopTeamRunEvent,
  DesktopWorkbenchDensity,
  PersonalTeamBlackboard,
} from './desktop-bridge'
import type { DesktopInvocationPhase } from './desktop-invocation-lifecycle'
import {
  type DesktopTeamNodeView,
  type DesktopTeamNodeStatusText,
  type DesktopTeamPhase,
  type PersonalEmployeeId,
  type TeamRunState,
} from './desktop-team-lifecycle'
import { TEAM_ROLE_LABELS } from './desktop-team-surface'

// ---------------------------------------------------------------------------
// Shell UI state
// ---------------------------------------------------------------------------

export const P7_ACTIVITIES = [
  'explorer',
  'search',
  'source',
  'run',
  'agents',
  'blackboard',
  'settings',
] as const
export type P7Activity = (typeof P7_ACTIVITIES)[number]

export const P7_CENTER_VIEWS = ['transcript', 'brief', 'code', 'diff', 'settings'] as const
export type P7CenterView = (typeof P7_CENTER_VIEWS)[number]

export const P7_BOTTOM_TABS = ['terminal', 'problems', 'output', 'agent-log'] as const
export type P7BottomTab = (typeof P7_BOTTOM_TABS)[number]

export type P7OmniaState =
  | 'idle'
  | 'thinking'
  | 'running'
  | 'completed'
  | 'blocked'
  | 'review-required'
  | 'sleeping'
  | 'surprised'
  | 'goodbye'

export interface P7ShellUiState {
  readonly activity: P7Activity
  readonly sidebarOpen: boolean
  readonly centerView: P7CenterView
  readonly bottomOpen: boolean
  readonly bottomTab: P7BottomTab
  readonly agentPanelOpen: boolean
  readonly omniaPopoverOpen: boolean
  readonly omniaMinimized: boolean
}

export interface P7WorkspaceShellUiState {
  readonly workspaceId: string | null
  readonly ui: P7ShellUiState
}

export function createP7ShellUiState(): P7ShellUiState {
  return {
    activity: 'explorer',
    sidebarOpen: true,
    centerView: 'transcript',
    bottomOpen: false,
    // Agent Log is the only bottom tab with a real data source in Wave 1.
    bottomTab: 'agent-log',
    agentPanelOpen: true,
    omniaPopoverOpen: false,
    omniaMinimized: false,
  }
}

export function p7WorkbenchRootClassNames(input: {
  readonly density: DesktopWorkbenchDensity
  readonly agentPanelVisible: boolean
  readonly quietChrome: boolean
  readonly reduceMotion: boolean
}): string {
  return [
    'p7-root',
    `p7-density-${input.density}`,
    input.agentPanelVisible ? null : 'p7-agent-closed-body',
    input.quietChrome ? 'p7-quiet-chrome' : null,
    input.reduceMotion ? 'p7-reduce-motion' : null,
  ]
    .filter((name): name is string => name !== null)
    .join(' ')
}

export function p7ShellLayoutClassNames(input: {
  readonly sidebarVisible: boolean
  readonly agentPanelVisible: boolean
}): string {
  return [
    'p7-shell',
    input.sidebarVisible ? null : 'p7-sidebar-closed',
    input.agentPanelVisible ? null : 'p7-agent-closed',
  ]
    .filter((name): name is string => name !== null)
    .join(' ')
}

export function createP7WorkspaceShellUiState(
  workspaceId: string | null,
  ui: P7ShellUiState = createP7ShellUiState(),
): P7WorkspaceShellUiState {
  return { workspaceId, ui }
}

/** A Workspace switch must synchronously discard the prior profile-derived chrome. */
export function p7WorkspaceShellUiProjection(
  state: P7WorkspaceShellUiState,
  viewWorkspaceId: string | null,
): P7ShellUiState {
  return state.workspaceId === viewWorkspaceId ? state.ui : createP7ShellUiState()
}

/**
 * IDE activity-bar semantics: activities are mutually exclusive and
 * re-clicking the active item toggles the sidebar closed.
 */
export function toggleP7Activity(state: P7ShellUiState, activity: P7Activity): P7ShellUiState {
  if (state.activity === activity && state.sidebarOpen) {
    return { ...state, sidebarOpen: false }
  }
  return { ...state, activity, sidebarOpen: true }
}

export function setP7SidebarOpen(state: P7ShellUiState, open: boolean): P7ShellUiState {
  return { ...state, sidebarOpen: open }
}

export function setP7CenterView(state: P7ShellUiState, centerView: P7CenterView): P7ShellUiState {
  return { ...state, centerView }
}

export function setP7BottomOpen(state: P7ShellUiState, open: boolean): P7ShellUiState {
  return { ...state, bottomOpen: open }
}

/** Selecting a bottom tab always expands the panel (IDE behavior). */
export function selectP7BottomTab(state: P7ShellUiState, bottomTab: P7BottomTab): P7ShellUiState {
  return { ...state, bottomTab, bottomOpen: true }
}

export function setP7AgentPanelOpen(state: P7ShellUiState, open: boolean): P7ShellUiState {
  return { ...state, agentPanelOpen: open }
}

export function toggleP7OmniaPopover(state: P7ShellUiState): P7ShellUiState {
  return { ...state, omniaPopoverOpen: !state.omniaPopoverOpen, omniaMinimized: false }
}

export function minimizeP7Omnia(state: P7ShellUiState): P7ShellUiState {
  return { ...state, omniaMinimized: true, omniaPopoverOpen: false }
}

export function expandP7Omnia(state: P7ShellUiState): P7ShellUiState {
  return { ...state, omniaMinimized: false, omniaPopoverOpen: true }
}

/** The approved design opens the blackboard view when switching to it. */
export function openP7Blackboard(state: P7ShellUiState): P7ShellUiState {
  return {
    ...state,
    activity: 'blackboard',
    sidebarOpen: true,
    centerView: 'brief',
  }
}

export function openP7Settings(state: P7ShellUiState): P7ShellUiState {
  return {
    ...state,
    activity: 'settings',
    sidebarOpen: false,
    centerView: 'settings',
    bottomOpen: false,
    agentPanelOpen: false,
  }
}

// ---------------------------------------------------------------------------
// Data-source presence and view availability
// ---------------------------------------------------------------------------

export const P7_NO_TRUSTED_SOURCE_REASON = '该功能没有可信的数据源；本界面不会显示任何模拟内容。'

export interface P7DataSourcePresence {
  readonly files: boolean
  readonly diff: boolean
  readonly terminal: boolean
  readonly problems: boolean
  readonly output: boolean
  readonly search: boolean
  readonly source: boolean
}

/**
 * Wave 1 wiring exposes no file/diff/terminal/problems/search/source catalog
 * through the desktop bridge. When a real catalog exists, the wiring layer
 * flips the matching flag and the availability projection unlocks the view
 * without any other change.
 */
export function p7DataSourcePresence(): P7DataSourcePresence {
  return {
    files: false,
    diff: false,
    terminal: false,
    problems: false,
    output: false,
    search: false,
    source: false,
  }
}

export interface P7ViewAvailability {
  readonly available: boolean
  readonly reason: string | null
}

export function p7ViewAvailability(
  view: P7CenterView,
  presence: P7DataSourcePresence,
): P7ViewAvailability {
  switch (view) {
    case 'transcript':
    case 'brief':
    case 'settings':
      return { available: true, reason: null }
    case 'code':
      return presence.files
        ? { available: true, reason: null }
        : { available: false, reason: P7_NO_TRUSTED_SOURCE_REASON }
    case 'diff':
      return presence.diff
        ? { available: true, reason: null }
        : { available: false, reason: P7_NO_TRUSTED_SOURCE_REASON }
  }
}

export function p7BottomTabAvailability(
  tab: P7BottomTab,
  presence: P7DataSourcePresence,
): P7ViewAvailability {
  switch (tab) {
    case 'terminal':
      return presence.terminal
        ? { available: true, reason: null }
        : { available: false, reason: P7_NO_TRUSTED_SOURCE_REASON }
    case 'problems':
      return presence.problems
        ? { available: true, reason: null }
        : { available: false, reason: P7_NO_TRUSTED_SOURCE_REASON }
    case 'output':
      // Output is the real operation log written by the wiring layer.
      return { available: true, reason: null }
    case 'agent-log':
      // Agent Log is the real raw event stream from the bridge subscriptions.
      return { available: true, reason: null }
  }
}

// ---------------------------------------------------------------------------
// OMNIA widget
// ---------------------------------------------------------------------------

export interface P7OmniaSnapshot {
  readonly state: P7OmniaState
  readonly dotTone: 'amber' | 'purple' | 'green' | 'red' | 'gray'
  readonly statusText: string
  readonly altText: string
}

/**
 * The OMNIA pending count may only come from the current live run's board,
 * and only while the user is viewing that run's origin conversation. A board
 * loaded while browsing history, a stale board for a previous run, or the
 * same board viewed from another conversation must never drive the widget.
 */
export function p7LivePendingCollaborations(
  current: boolean,
  liveRunId: string | null,
  liveBlackboard: PersonalTeamBlackboard | null,
): number {
  if (!current || liveRunId === null || liveBlackboard === null) return 0
  if (liveBlackboard.teamRunId !== liveRunId) return 0
  return liveBlackboard.collaborationRequests.filter(
    (request) => request.parentDecision === 'pending',
  ).length
}

/**
 * Which board the task-brief view renders. The live slot wins only while it
 * is current for the viewed conversation; once the run ends (or the view
 * moves away) the browsed history run's board takes over, and when neither
 * exists the view is empty. This is the pure rule behind the workbench
 * wiring, so terminal/conversation transitions are unit-testable.
 */
export interface P7BriefBoardSelection {
  readonly board: PersonalTeamBlackboard | null
  readonly status: 'idle' | 'loading' | 'ready' | 'error'
  readonly source: 'live' | 'history' | 'none'
}

export function p7BriefBoardSelection(input: {
  readonly liveCurrent: boolean
  readonly liveBoard: PersonalTeamBlackboard | null
  readonly liveStatus: 'idle' | 'loading' | 'ready' | 'error'
  readonly historyBoard: PersonalTeamBlackboard | null
  readonly historyStatus: 'idle' | 'loading' | 'ready' | 'error'
}): P7BriefBoardSelection {
  if (input.liveCurrent) {
    return { board: input.liveBoard, status: input.liveStatus, source: 'live' }
  }
  if (input.historyBoard !== null || input.historyStatus !== 'idle') {
    return { board: input.historyBoard, status: input.historyStatus, source: 'history' }
  }
  return { board: null, status: 'idle', source: 'none' }
}

export const P7_OMNIA_IMAGES: Readonly<Record<P7OmniaState, string>> = {
  idle: '/omnia/oself-idle.png',
  thinking: '/omnia/oself-thinking.png',
  running: '/omnia/oself-running.png',
  completed: '/omnia/oself-completed.png',
  blocked: '/omnia/oself-blocked.png',
  'review-required': '/omnia/oself-review-required.png',
  sleeping: '/omnia/oself-sleeping.png',
  surprised: '/omnia/oself-surprised.png',
  goodbye: '/omnia/oself-goodbye.png',
}

const TEAM_THINKING_PHASES: ReadonlySet<DesktopTeamPhase> = new Set([
  'parent_proposing',
  'host_validating',
  'parent_replanning',
])

const TEAM_RUNNING_PHASES: ReadonlySet<DesktopTeamPhase> = new Set([
  'preparing',
  'wave_starting',
  'node_running',
  'blackboard_updated',
  'parent_synthesizing',
  'cancelling',
])

const TERMINAL_TEAM_RUN_STATES: ReadonlySet<TeamRunState> = new Set([
  'failed',
  'cancelled',
  'budget_exhausted',
  'cannot_complete',
  'unknown',
])

const LIVE_INVOKING_PHASES: ReadonlySet<DesktopInvocationPhase> = new Set([
  'send',
  'starting_identity',
  'identity',
])

const LIVE_RUNNING_PHASES: ReadonlySet<DesktopInvocationPhase> = new Set(['running', 'convergence'])

const LIVE_ACTIVE_PHASES: ReadonlySet<DesktopInvocationPhase> = new Set([
  'send',
  'starting_identity',
  'identity',
  'running',
  'convergence',
  'cancelling',
])

/**
 * A live invocation is active purely from the phase: `beginDesktopLiveSend`
 * enters `starting_identity` before any invocation id exists, so requiring an
 * invocation id here would let old team state leak into the feed/OMNIA during
 * the send window. Visibility still gates on the origin view.
 */
export function p7LiveActive(input: {
  readonly liveVisible: boolean
  readonly live: P7LiveReference
}): boolean {
  return input.liveVisible && LIVE_ACTIVE_PHASES.has(input.live.phase)
}

/**
 * The only real trigger mapping for the OMNIA widget. Every branch reads a
 * live field; there is no timer, no fake activity and no success fabrication.
 *
 * Priority: an active single-agent invocation shadows the team run display,
 * so a new stream is never suppressed by an old team terminal state in the
 * same conversation. Team states are considered only when no live invocation
 * is active. The mapper never emits sleeping/surprised/goodbye, because no
 * real trigger exists in Wave 1.
 */
export function p7OmniaStateForLive(input: {
  readonly teamPhase: DesktopTeamPhase
  readonly teamRunState: TeamRunState | null
  readonly livePhase: DesktopInvocationPhase
  readonly liveVisible: boolean
  readonly pendingCollaborations: number
}): P7OmniaSnapshot {
  const liveActive =
    input.liveVisible &&
    (LIVE_INVOKING_PHASES.has(input.livePhase) ||
      LIVE_RUNNING_PHASES.has(input.livePhase) ||
      input.livePhase === 'cancelling')
  if (liveActive) {
    if (input.livePhase === 'cancelling') {
      return {
        state: 'running',
        dotTone: 'purple',
        statusText: '正在停止',
        altText: 'OMNIA · 正在停止',
      }
    }
    if (LIVE_RUNNING_PHASES.has(input.livePhase)) {
      return {
        state: 'running',
        dotTone: 'purple',
        statusText: '正在生成',
        altText: 'OMNIA · 正在生成',
      }
    }
    return {
      state: 'thinking',
      dotTone: 'purple',
      statusText: '正在发起调用',
      altText: 'OMNIA · 正在发起调用',
    }
  }
  if (input.teamRunState !== null && TERMINAL_TEAM_RUN_STATES.has(input.teamRunState)) {
    return {
      state: 'blocked',
      dotTone: 'red',
      statusText: `团队协作未完成（${p7RunStateLabel(input.teamRunState)}）`,
      altText: 'OMNIA · 团队协作未完成',
    }
  }
  if (input.teamRunState === 'succeeded' || input.teamPhase === 'completed') {
    return {
      state: 'completed',
      dotTone: 'green',
      statusText: '团队协作已完成',
      altText: 'OMNIA · 团队协作已完成',
    }
  }
  if (input.pendingCollaborations > 0) {
    return {
      state: 'review-required',
      dotTone: 'amber',
      statusText: `${input.pendingCollaborations} 个协作请求等待处理`,
      altText: 'OMNIA · 协作请求等待处理',
    }
  }
  if (TEAM_RUNNING_PHASES.has(input.teamPhase)) {
    return {
      state: 'running',
      dotTone: 'purple',
      statusText: input.teamPhase === 'cancelling' ? '正在停止' : 'Agent 正在运行',
      altText: 'OMNIA · Agent 正在运行',
    }
  }
  if (TEAM_THINKING_PHASES.has(input.teamPhase)) {
    return {
      state: 'thinking',
      dotTone: 'purple',
      statusText: '父 Agent 正在规划',
      altText: 'OMNIA · 父 Agent 正在规划',
    }
  }
  return {
    state: 'idle',
    dotTone: 'gray',
    statusText: '空闲',
    altText: 'OMNIA · 空闲',
  }
}

// ---------------------------------------------------------------------------
// Agent feed
// ---------------------------------------------------------------------------

export type P7AgentFeedItem =
  | {
      readonly kind: 'task'
      readonly key: string
      readonly title: string
      readonly detail: string | null
    }
  | {
      readonly kind: 'event'
      readonly key: string
      readonly label: string
      readonly meta: string | null
      readonly tone: 'current' | 'ok' | 'error' | 'neutral'
    }
  | {
      readonly kind: 'result'
      readonly key: string
      readonly answer: string
      readonly meta: string | null
    }

export function p7NodeTone(
  statusText: DesktopTeamNodeStatusText,
): 'current' | 'ok' | 'error' | 'neutral' {
  switch (statusText) {
    case '运行中':
    case '正在停止':
      return 'current'
    case '已完成':
      return 'ok'
    case '失败':
      return 'error'
    default:
      return 'neutral'
  }
}

export function p7Truncate(text: string, maxCharacters: number): string {
  if (text.length <= maxCharacters) return text
  return `${text.slice(0, maxCharacters)}…`
}

/**
 * Renders the real live state as an ordered feed: task, plan revision, wave,
 * dependency plan, node rows (by ordinal), collaboration lines, live text,
 * final answer. No item is fabricated; unknown roles fall back to their raw id.
 *
 * When a single-agent invocation is active in the current view, team-derived
 * items (plan/wave/nodes/collaborations and the old final answer) are
 * suppressed so a new stream never mixes with an old team run in the same
 * conversation.
 */
export function projectP7AgentFeed(input: {
  readonly agentName: string
  readonly teamPhase: DesktopTeamPhase
  readonly teamRunState: TeamRunState | null
  readonly taskText: string | null
  readonly nodes: readonly DesktopTeamNodeView[]
  readonly collaborationLines: readonly string[]
  readonly planRevisionId: string | null
  readonly waveId: string | null
  readonly declaredExecution: 'serial' | 'parallel' | null
  readonly effectiveExecution: 'serial' | 'parallel' | null
  readonly planSummary: string | null
  readonly parentFinalAnswer: string | null
  readonly liveText: string
  readonly liveVisible: boolean
  readonly liveActive?: boolean
  readonly consumedProviderCalls: number
  readonly maximumProviderCalls: number
}): readonly P7AgentFeedItem[] {
  const items: P7AgentFeedItem[] = []
  const teamActive = input.teamPhase !== 'idle'
  const suppressTeam = input.liveActive === true
  if (suppressTeam) {
    items.push({
      kind: 'task',
      key: 'task',
      title: '单 Agent 生成',
      detail: input.agentName,
    })
  } else {
    items.push({
      kind: 'task',
      key: 'task',
      title: input.taskText ?? (teamActive ? '团队协作进行中' : '还没有任务'),
      detail: teamActive ? `${input.agentName} · 本地工作区` : null,
    })
  }
  if (!suppressTeam && input.planRevisionId !== null) {
    items.push({
      kind: 'event',
      key: `plan-${input.planRevisionId}`,
      label: '计划修订',
      meta: input.planRevisionId,
      tone: 'neutral',
    })
  }
  if (!suppressTeam && input.waveId !== null) {
    items.push({
      kind: 'event',
      key: `wave-${input.waveId}`,
      label: 'wave 开始',
      meta:
        input.declaredExecution !== null
          ? `${input.waveId} · ${input.declaredExecution}${
              input.effectiveExecution !== null &&
              input.effectiveExecution !== input.declaredExecution
                ? `（宿主降为 ${input.effectiveExecution}）`
                : ''
            }`
          : input.waveId,
      tone: 'current',
    })
  }
  if (!suppressTeam && input.planSummary !== null && input.planSummary !== '') {
    items.push({
      kind: 'event',
      key: 'deps',
      label: '依赖计划',
      meta: input.planSummary,
      tone: 'neutral',
    })
  }
  const nodes = [...input.nodes].sort((left, right) => left.ordinal - right.ordinal)
  for (const node of nodes) {
    if (suppressTeam) break
    items.push({
      kind: 'event',
      key: node.nodeId,
      label: `${p7RoleLabel(node.employeeRoleId)} · ${node.statusText}`,
      meta:
        [
          node.durationMs !== null ? `${Math.round(node.durationMs / 100) / 10}s` : null,
          node.totalTokens !== null ? `${node.totalTokens} tokens` : null,
          node.report !== null && node.report !== '' ? p7Truncate(node.report, 240) : null,
        ]
          .filter((part): part is string => part !== null)
          .join(' · ') || null,
      tone: p7NodeTone(node.statusText),
    })
  }
  if (!suppressTeam) {
    input.collaborationLines.forEach((line, index) => {
      items.push({
        kind: 'event',
        key: `collab-${index}-${line}`,
        label: '协作请求',
        meta: line,
        tone: 'neutral',
      })
    })
  }
  if (input.liveVisible && input.liveText !== '') {
    items.push({
      kind: 'event',
      key: 'live',
      label: '正在生成',
      meta: input.liveText,
      tone: 'current',
    })
  }
  if (!suppressTeam && input.parentFinalAnswer !== null && input.parentFinalAnswer !== '') {
    items.push({
      kind: 'result',
      key: 'result',
      answer: input.parentFinalAnswer,
      meta:
        input.maximumProviderCalls > 0
          ? `已用 ${input.consumedProviderCalls} / ${input.maximumProviderCalls} 次调用`
          : null,
    })
  }
  return items
}

// ---------------------------------------------------------------------------
// Threads (Agent 线程 sidebar panel)
// ---------------------------------------------------------------------------

export interface P7ThreadRow {
  readonly conversationId: string
  readonly title: string
  readonly statusText: string
  readonly meta: string | null
  readonly active: boolean
  readonly dotTone: 'purple' | 'green' | 'red' | 'gray'
}

export function p7TeamPhaseLabel(phase: DesktopTeamPhase): string {
  switch (phase) {
    case 'preparing':
      return '准备中'
    case 'parent_proposing':
    case 'host_validating':
    case 'parent_replanning':
      return '规划中'
    case 'wave_starting':
    case 'node_running':
    case 'parent_synthesizing':
    case 'blackboard_updated':
      return '运行中'
    case 'cancelling':
      return '正在停止'
    case 'completed':
      return '已完成'
    case 'budget_exhausted':
      return '预算耗尽'
    case 'cannot_complete':
      return '无法完成'
    case 'cancelled':
      return '已取消'
    case 'unknown':
      return '状态未知'
    case 'failed':
      return '失败'
    case 'idle':
      return '空闲'
  }
}

export function p7RunStateLabel(state: string): string {
  switch (state) {
    case 'preparing':
      return '准备中'
    case 'running':
      return '运行中'
    case 'cancelling':
      return '正在停止'
    case 'succeeded':
      return '已完成'
    case 'failed':
      return '失败'
    case 'cancelled':
      return '已取消'
    case 'unknown':
      return '状态未知'
    case 'budget_exhausted':
      return '预算耗尽'
    case 'cannot_complete':
      return '无法完成'
    default:
      return state
  }
}

function livePhaseLabel(phase: DesktopInvocationPhase): string {
  switch (phase) {
    case 'send':
    case 'starting_identity':
    case 'identity':
      return '正在发起'
    case 'running':
    case 'convergence':
      return '正在生成'
    case 'cancelling':
      return '正在停止'
    case 'cancelled':
      return '已取消'
    case 'terminal':
      return '已完成'
    default:
      return '空闲'
  }
}

export interface P7LiveReference {
  readonly conversationId: string | null
  readonly invocationId: string | null
  readonly phase: DesktopInvocationPhase
}

export function projectP7ThreadRows(input: {
  readonly conversations: readonly DesktopConversation[]
  readonly selectedConversationId: string | null
  readonly teamPhase: DesktopTeamPhase
  readonly teamOriginConversationId: string | null
  readonly live: P7LiveReference
}): readonly P7ThreadRow[] {
  const active = input.conversations.filter((item) => item.state === 'active')
  return active.map((conversation) => {
    const boundToTeam =
      conversation.id === input.teamOriginConversationId && input.teamPhase !== 'idle'
    const boundToLive =
      conversation.id === input.live.conversationId && input.live.invocationId !== null
    if (boundToTeam) {
      const running = p7TeamPhaseLabel(input.teamPhase)
      return {
        conversationId: conversation.id,
        title: conversation.title,
        statusText: running,
        meta: null,
        active: conversation.id === input.selectedConversationId,
        dotTone:
          running === '运行中' || running === '规划中' || running === '准备中'
            ? 'purple'
            : running === '已完成'
              ? 'green'
              : running === '失败' || running === '已取消' || running === '无法完成'
                ? 'red'
                : 'gray',
      }
    }
    if (boundToLive) {
      const label = livePhaseLabel(input.live.phase)
      return {
        conversationId: conversation.id,
        title: conversation.title,
        statusText: label,
        meta: null,
        active: conversation.id === input.selectedConversationId,
        dotTone:
          label === '正在生成' || label === '正在发起' || label === '正在停止'
            ? 'purple'
            : label === '已取消' || label === '失败'
              ? 'red'
              : 'green',
      }
    }
    return {
      conversationId: conversation.id,
      title: conversation.title,
      statusText: '空闲',
      meta: null,
      active: conversation.id === input.selectedConversationId,
      dotTone: 'gray',
    }
  })
}

// ---------------------------------------------------------------------------
// Run history (运行与调试 sidebar panel)
// ---------------------------------------------------------------------------

export interface P7RunRow {
  readonly run: DesktopTeamRun
  readonly stateLabel: string
  readonly meta: string | null
  readonly active: boolean
}

export function projectP7RunRows(
  runs: readonly DesktopTeamRun[],
  selectedRunId: string | null,
): readonly P7RunRow[] {
  return [...runs]
    .sort((left, right) => right.createdAt.localeCompare(left.createdAt))
    .map((run) => ({
      run,
      stateLabel: p7RunStateLabel(run.state),
      meta:
        run.consumedProviderCalls > 0 || run.maximumProviderCalls > 0
          ? `已用 ${run.consumedProviderCalls} / ${run.maximumProviderCalls} 次调用`
          : null,
      active: run.id === selectedRunId,
    }))
}

/**
 * Workspace identity gate for the run history panel. The history list is
 * bound to the workspace it was loaded for; while the view shows another
 * workspace (including the first frame right after a switch, before the
 * reload effect runs) no row from the previous workspace may render or be
 * clickable.
 */
export function p7RunHistoryProjection(input: {
  readonly historyWorkspaceId: string | null
  readonly viewWorkspaceId: string | null
  readonly status: 'idle' | 'loading' | 'ready' | 'error'
  readonly rows: readonly DesktopTeamRun[]
}): {
  readonly status: 'idle' | 'loading' | 'ready' | 'error'
  readonly rows: readonly DesktopTeamRun[]
} {
  if (input.historyWorkspaceId !== input.viewWorkspaceId) {
    return { status: 'loading', rows: [] }
  }
  return { status: input.status, rows: input.rows }
}

// ---------------------------------------------------------------------------
// Blackboard (计划与黑板 panel + 任务简报 view)
// ---------------------------------------------------------------------------

export function p7AssignmentStateLabel(state: string): string {
  switch (state) {
    case 'pending':
      return '等待'
    case 'running':
      return '运行中'
    case 'completed':
      return '已完成'
    case 'needs_collaboration':
      return '需要协作'
    case 'blocked':
      return '受阻'
    case 'cancelled':
      return '已取消'
    default:
      return state
  }
}

export function p7CollaborationDecisionLabel(decision: string): string {
  switch (decision) {
    case 'pending':
      return '待处理'
    case 'accept_start':
      return '已接受 · 新角色'
    case 'handle_self':
      return '已自行处理'
    case 'merge_existing':
      return '已并入现有'
    case 'decline':
      return '已拒绝'
    default:
      return decision
  }
}

export interface P7BlackboardSection {
  readonly teamRunId: string
  readonly ownerObjective: string
  readonly currentPlanRevisionId: string | null
  readonly assignments: readonly {
    readonly assignmentId: string
    readonly roleLabel: string
    readonly stateLabel: string
    readonly waveId: string | null
    readonly objective: string
  }[]
  readonly reports: readonly {
    readonly assignmentId: string
    readonly roleLabel: string
    readonly status: string
    readonly report: string
  }[]
  readonly collaborationRequests: readonly {
    readonly id: string | null
    readonly fromRoleLabel: string
    readonly targetRoleLabel: string
    readonly question: string
    readonly decisionLabel: string
    readonly pending: boolean
  }[]
}

export function projectP7Blackboard(blackboard: PersonalTeamBlackboard): P7BlackboardSection {
  const assignments = blackboard.assignments.map((row) => {
    const assignment = row as {
      readonly assignmentId?: unknown
      readonly employeeRoleId?: unknown
      readonly state?: unknown
      readonly waveId?: unknown
      readonly objective?: unknown
    }
    const roleId = typeof assignment.employeeRoleId === 'string' ? assignment.employeeRoleId : ''
    return {
      assignmentId: typeof assignment.assignmentId === 'string' ? assignment.assignmentId : '—',
      roleLabel: roleId === '' ? '未知角色' : p7RoleLabel(roleId),
      stateLabel: p7AssignmentStateLabel(
        typeof assignment.state === 'string' ? assignment.state : '未知',
      ),
      waveId: typeof assignment.waveId === 'string' ? assignment.waveId : null,
      objective: typeof assignment.objective === 'string' ? assignment.objective : '',
    }
  })
  const reports = blackboard.reports.map((row) => {
    const report = row as {
      readonly assignmentId?: unknown
      readonly employeeRoleId?: unknown
      readonly status?: unknown
      readonly report?: unknown
    }
    const roleId = typeof report.employeeRoleId === 'string' ? report.employeeRoleId : ''
    return {
      assignmentId: typeof report.assignmentId === 'string' ? report.assignmentId : '—',
      roleLabel: roleId === '' ? '未知角色' : p7RoleLabel(roleId),
      status: p7AssignmentStateLabel(typeof report.status === 'string' ? report.status : '未知'),
      report: typeof report.report === 'string' ? report.report : '',
    }
  })
  const collaborationRequests = blackboard.collaborationRequests.map(
    (request: DesktopTeamCollaborationRequest) => ({
      id: request.id ?? null,
      fromRoleLabel: p7RoleLabel(request.fromEmployeeRoleId),
      targetRoleLabel: p7RoleLabel(request.targetRoleId),
      question: request.question,
      decisionLabel: p7CollaborationDecisionLabel(request.parentDecision),
      pending: request.parentDecision === 'pending',
    }),
  )
  return {
    teamRunId: blackboard.teamRunId,
    ownerObjective: blackboard.ownerObjective,
    currentPlanRevisionId: blackboard.currentPlanRevisionId,
    assignments,
    reports,
    collaborationRequests,
  }
}

// ---------------------------------------------------------------------------
// Event log lines (Agent Log bottom tab)
// ---------------------------------------------------------------------------

export function p7TeamEventLogLine(event: DesktopTeamRunEvent): string {
  const parts: string[] = [event.type]
  if (event.teamRunId !== undefined) parts.push(event.teamRunId)
  if (event.nodeId !== undefined) parts.push(`node ${event.nodeId}`)
  if (event.employeeRoleId !== undefined) parts.push(event.employeeRoleId)
  if (event.planRevisionId !== undefined) parts.push(`rev ${event.planRevisionId}`)
  if (event.waveId !== undefined) parts.push(`wave ${event.waveId}`)
  if (event.assignmentId !== undefined) parts.push(`assignment ${event.assignmentId}`)
  if (event.errorCode !== undefined) parts.push(`error ${event.errorCode}`)
  if (event.text !== undefined) parts.push(`delta ${event.text.length} chars`)
  if (event.answer !== undefined) parts.push(`answer ${event.answer.length} chars`)
  if (event.totalTokens !== undefined) parts.push(`${event.totalTokens} tokens`)
  if (event.consumedProviderCalls !== undefined)
    parts.push(`calls ${event.consumedProviderCalls}/${event.maximumProviderCalls ?? '?'}`)
  return parts.join(' · ')
}

export function p7ConversationEventLogLine(event: DesktopConversationEvent): string {
  const parts: string[] = [`invocation ${event.invocationId}`, event.type]
  if (event.answer !== undefined) parts.push(`answer ${event.answer.length} chars`)
  if (event.text !== undefined) parts.push(`delta ${event.text.length} chars`)
  if (event.totalTokens !== undefined) parts.push(`${event.totalTokens} tokens`)
  if (event.errorCode !== undefined) parts.push(`error ${event.errorCode}`)
  if (event.errorRedacted !== undefined) parts.push(event.errorRedacted)
  return parts.join(' · ')
}

// ---------------------------------------------------------------------------
// Labels
// ---------------------------------------------------------------------------

export function p7RoleLabel(roleId: string): string {
  return TEAM_ROLE_LABELS[roleId as PersonalEmployeeId] ?? roleId
}

export function p7ActivityLabel(activity: P7Activity): string {
  switch (activity) {
    case 'explorer':
      return '资源管理器'
    case 'search':
      return '搜索'
    case 'source':
      return '源代码管理'
    case 'run':
      return '运行与调试'
    case 'agents':
      return 'Agent 线程'
    case 'blackboard':
      return '计划与黑板'
    case 'settings':
      return '设置'
  }
}

export function p7CenterViewLabel(view: P7CenterView): string {
  switch (view) {
    case 'transcript':
      return '会话记录'
    case 'brief':
      return '任务简报'
    case 'code':
      return '代码'
    case 'diff':
      return '审阅变更'
    case 'settings':
      return '设置'
  }
}

export function p7BottomTabLabel(tab: P7BottomTab): string {
  switch (tab) {
    case 'terminal':
      return '终端'
    case 'problems':
      return '问题'
    case 'output':
      return '输出'
    case 'agent-log':
      return 'Agent Log'
  }
}

/** Real live counters for the status bar. */
export function p7RunningCount(input: {
  readonly teamPhase: DesktopTeamPhase
  readonly live: P7LiveReference
}): number {
  let count = 0
  if (
    input.teamPhase === 'preparing' ||
    input.teamPhase === 'parent_proposing' ||
    input.teamPhase === 'host_validating' ||
    input.teamPhase === 'wave_starting' ||
    input.teamPhase === 'node_running' ||
    input.teamPhase === 'parent_replanning' ||
    input.teamPhase === 'parent_synthesizing' ||
    input.teamPhase === 'blackboard_updated' ||
    input.teamPhase === 'cancelling'
  ) {
    count += 1
  }
  if (LIVE_ACTIVE_PHASES.has(input.live.phase)) {
    count += 1
  }
  return count
}
