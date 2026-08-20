export type DesktopTeamRunEvent = {
  readonly type: string
  readonly teamRunId: string
  readonly workspaceId: string
  readonly conversationId?: string
  readonly state?: string
  readonly planRevisionId?: string | null
  readonly waveId?: string | null
  readonly assignmentId?: string
  readonly rosterEpoch?: number
  readonly nodeId?: string
  readonly nodeOrdinal?: number
  readonly employeeRoleId?: string
  readonly invocationId?: string
  readonly sendEpoch?: number
  readonly nodeEpoch?: number
  readonly text?: string
  readonly answer?: string
  readonly durationMs?: number
  readonly inputTokens?: number | null
  readonly outputTokens?: number | null
  readonly totalTokens?: number | null
  readonly errorCode?: string
  readonly parentFinalAnswer?: string
  readonly consumedProviderCalls?: number
  readonly maximumProviderCalls?: number
  readonly collaborationLine?: string
  readonly reportStatus?: string
  readonly assignmentIds?: readonly string[]
  readonly employeeRoleIds?: readonly string[]
  readonly planSummary?: string
  readonly declaredExecution?: 'serial' | 'parallel'
  readonly effectiveExecution?: 'serial' | 'parallel'
}

export type PersonalEmployeeId =
  | 'parent'
  | 'product'
  | 'ux'
  | 'frontend'
  | 'backend'
  | 'data'
  | 'security'
  | 'qa'
  | 'operations'
  | 'docs'

export type TeamRunState =
  | 'preparing'
  | 'running'
  | 'cancelling'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'unknown'
  | 'budget_exhausted'
  | 'cannot_complete'

export type DesktopTeamPhase =
  | 'idle'
  | 'preparing'
  | 'parent_proposing'
  | 'host_validating'
  | 'wave_starting'
  | 'node_running'
  | 'blackboard_updated'
  | 'parent_replanning'
  | 'parent_synthesizing'
  | 'cancelling'
  | 'completed'
  | 'budget_exhausted'
  | 'cannot_complete'
  | 'cancelled'
  | 'unknown'
  | 'failed'

export type DesktopTeamNodeStatusText =
  | '静默'
  | '等待'
  | '运行中'
  | '正在停止'
  | '已完成'
  | '失败'
  | '需要协作'
  | '状态未知'

export interface DesktopTeamNodeView {
  readonly nodeId: string
  readonly assignmentId: string
  readonly invocationId: string
  readonly employeeRoleId: PersonalEmployeeId
  readonly ordinal: number
  readonly waveId: string
  readonly statusText: DesktopTeamNodeStatusText
  readonly durationMs: number | null
  readonly inputTokens: number | null
  readonly outputTokens: number | null
  readonly totalTokens: number | null
  readonly report: string | null
  readonly sendEpoch: number
  readonly nodeEpoch: number
}

export interface DesktopTeamLiveState {
  readonly workspaceId: string | null
  readonly conversationId: string | null
  readonly originWorkspaceId: string | null
  readonly originConversationId: string | null
  readonly teamRunId: string | null
  readonly rosterEpoch: number
  readonly planRevisionId: string | null
  readonly waveId: string | null
  readonly phase: DesktopTeamPhase
  readonly runState: TeamRunState | null
  readonly cancelRequested: boolean
  readonly parentFinalAnswer: string | null
  readonly parentLiveText: string
  readonly nodes: readonly DesktopTeamNodeView[]
  readonly collaborationLines: readonly string[]
  readonly consumedProviderCalls: number
  readonly maximumProviderCalls: number
  readonly parkedParentLiveText: string
  readonly parkedParentFinalAnswer: string | null
  readonly planSummary: string | null
  readonly declaredExecution: 'serial' | 'parallel' | null
  readonly effectiveExecution: 'serial' | 'parallel' | null
}

export function createDesktopTeamLiveState(input: {
  readonly workspaceId: string | null
  readonly conversationId: string | null
}): DesktopTeamLiveState {
  return {
    workspaceId: input.workspaceId,
    conversationId: input.conversationId,
    originWorkspaceId: input.workspaceId,
    originConversationId: input.conversationId,
    teamRunId: null,
    rosterEpoch: 0,
    planRevisionId: null,
    waveId: null,
    phase: 'idle',
    runState: null,
    cancelRequested: false,
    parentFinalAnswer: null,
    parentLiveText: '',
    nodes: [],
    collaborationLines: [],
    consumedProviderCalls: 0,
    maximumProviderCalls: 0,
    parkedParentLiveText: '',
    parkedParentFinalAnswer: null,
    planSummary: null,
    declaredExecution: null,
    effectiveExecution: null,
  }
}

function viewingOrigin(state: DesktopTeamLiveState): boolean {
  return (
    state.workspaceId === state.originWorkspaceId &&
    state.conversationId === state.originConversationId
  )
}

function eventIdentityComplete(event: DesktopTeamRunEvent): boolean {
  return (
    typeof event.workspaceId === 'string' &&
    event.workspaceId.length > 0 &&
    typeof event.conversationId === 'string' &&
    event.conversationId.length > 0 &&
    typeof event.teamRunId === 'string' &&
    event.teamRunId.length > 0 &&
    typeof event.rosterEpoch === 'number' &&
    Number.isInteger(event.rosterEpoch) &&
    typeof event.planRevisionId === 'string' &&
    typeof event.waveId === 'string' &&
    typeof event.assignmentId === 'string' &&
    typeof event.nodeId === 'string' &&
    typeof event.sendEpoch === 'number' &&
    Number.isInteger(event.sendEpoch)
  )
}

function eventMatches(state: DesktopTeamLiveState, event: DesktopTeamRunEvent): boolean {
  if (!eventIdentityComplete(event)) return false
  if (state.teamRunId === null) return false
  if (event.teamRunId !== state.teamRunId) return false
  if (event.workspaceId !== state.originWorkspaceId) return false
  if (event.conversationId !== state.originConversationId) return false
  if (event.rosterEpoch !== state.rosterEpoch) return false
  if (
    state.planRevisionId !== null &&
    state.planRevisionId !== '' &&
    event.planRevisionId !== state.planRevisionId
  ) {
    return false
  }
  if (
    (event.type === 'node_starting' ||
      event.type === 'node_identity' ||
      event.type === 'node_delta' ||
      event.type === 'node_terminal') &&
    state.waveId !== null &&
    event.waveId !== state.waveId
  ) {
    return false
  }
  return true
}

export function beginDesktopTeamRun(
  state: DesktopTeamLiveState,
  input: {
    readonly workspaceId: string
    readonly conversationId: string
    readonly rosterEpoch: number
    readonly maximumProviderCalls: number
  },
): DesktopTeamLiveState {
  if (state.phase !== 'idle' && state.phase !== 'completed' && state.phase !== 'cancelled' && state.phase !== 'failed' && state.phase !== 'budget_exhausted' && state.phase !== 'cannot_complete' && state.phase !== 'unknown') {
    return state
  }
  return {
    ...state,
    workspaceId: input.workspaceId,
    conversationId: input.conversationId,
    originWorkspaceId: input.workspaceId,
    originConversationId: input.conversationId,
    teamRunId: null,
    rosterEpoch: input.rosterEpoch,
    planRevisionId: null,
    waveId: null,
    phase: 'preparing',
    runState: 'preparing',
    cancelRequested: false,
    parentFinalAnswer: null,
    parentLiveText: '',
    nodes: [],
    collaborationLines: [],
    consumedProviderCalls: 0,
    maximumProviderCalls: input.maximumProviderCalls,
    parkedParentLiveText: '',
    parkedParentFinalAnswer: null,
    planSummary: null,
    declaredExecution: null,
    effectiveExecution: null,
  }
}

export function requestDesktopTeamCancel(state: DesktopTeamLiveState): DesktopTeamLiveState {
  if (state.phase === 'idle' || state.phase === 'completed' || state.phase === 'cancelled') {
    return state
  }
  return { ...state, cancelRequested: true, phase: 'cancelling' }
}

export function switchDesktopTeamScope(
  state: DesktopTeamLiveState,
  workspaceId: string | null,
  conversationId: string | null,
): DesktopTeamLiveState {
  const same = state.workspaceId === workspaceId && state.conversationId === conversationId
  if (same) return state
  const leavingOrigin = viewingOrigin(state)
  const returningOrigin =
    workspaceId === state.originWorkspaceId && conversationId === state.originConversationId
  return {
    ...state,
    workspaceId,
    conversationId,
    parentLiveText: leavingOrigin
      ? ''
      : returningOrigin
        ? state.parkedParentLiveText
        : state.parentLiveText,
    parentFinalAnswer: leavingOrigin
      ? null
      : returningOrigin
        ? state.parkedParentFinalAnswer
        : state.parentFinalAnswer,
    parkedParentLiveText: leavingOrigin
      ? state.parentLiveText
      : returningOrigin
        ? ''
        : state.parkedParentLiveText,
    parkedParentFinalAnswer: leavingOrigin
      ? state.parentFinalAnswer
      : returningOrigin
        ? null
        : state.parkedParentFinalAnswer,
  }
}

export function desktopTeamStopVisible(state: DesktopTeamLiveState): boolean {
  return (
    state.phase === 'preparing' ||
    state.phase === 'parent_proposing' ||
    state.phase === 'host_validating' ||
    state.phase === 'wave_starting' ||
    state.phase === 'node_running' ||
    state.phase === 'parent_replanning' ||
    state.phase === 'parent_synthesizing' ||
    state.phase === 'cancelling'
  )
}

export function desktopTeamLiveProjection(
  state: DesktopTeamLiveState,
  workspaceId: string | null,
  conversationId: string | null,
): {
  readonly visible: boolean
  readonly parentLiveText: string
  readonly parentFinalAnswer: string | null
} {
  const visible =
    state.originWorkspaceId === workspaceId && state.originConversationId === conversationId
  return {
    visible,
    parentLiveText: visible ? state.parentLiveText : '',
    parentFinalAnswer: visible ? state.parentFinalAnswer : null,
  }
}

export function desktopTeamStatusForRole(
  state: DesktopTeamLiveState,
  roleId: PersonalEmployeeId,
): DesktopTeamNodeStatusText {
  if (state.cancelRequested) {
    const live = state.nodes.find((node) => node.employeeRoleId === roleId && node.statusText === '运行中')
    if (live) return '正在停止'
  }
  const nodes = state.nodes.filter((node) => node.employeeRoleId === roleId)
  if (nodes.length === 0) {
    if (roleId === 'parent' && (state.phase === 'parent_proposing' || state.phase === 'parent_replanning' || state.phase === 'parent_synthesizing')) {
      return '运行中'
    }
    return roleId === 'parent' && state.phase !== 'idle' ? '等待' : '静默'
  }
  return nodes[nodes.length - 1]!.statusText
}

function upsertNode(
  nodes: readonly DesktopTeamNodeView[],
  next: DesktopTeamNodeView,
): readonly DesktopTeamNodeView[] {
  const index = nodes.findIndex((item) => item.nodeId === next.nodeId)
  if (index < 0) return [...nodes, next]
  const copy = [...nodes]
  copy[index] = next
  return copy
}

function asRole(value: string | undefined): PersonalEmployeeId | undefined {
  if (
    value === 'parent' ||
    value === 'product' ||
    value === 'ux' ||
    value === 'frontend' ||
    value === 'backend' ||
    value === 'data' ||
    value === 'security' ||
    value === 'qa' ||
    value === 'operations' ||
    value === 'docs'
  ) {
    return value
  }
  return undefined
}

function asRunState(value: string | undefined, fallback: TeamRunState | null): TeamRunState | null {
  if (
    value === 'preparing' ||
    value === 'running' ||
    value === 'cancelling' ||
    value === 'succeeded' ||
    value === 'failed' ||
    value === 'cancelled' ||
    value === 'unknown' ||
    value === 'budget_exhausted' ||
    value === 'cannot_complete'
  ) {
    return value
  }
  return fallback
}

function withBudget(state: DesktopTeamLiveState, event: DesktopTeamRunEvent): DesktopTeamLiveState {
  return {
    ...state,
    consumedProviderCalls: event.consumedProviderCalls ?? state.consumedProviderCalls,
    maximumProviderCalls: event.maximumProviderCalls ?? state.maximumProviderCalls,
  }
}

export function reduceDesktopTeamEvent(
  state: DesktopTeamLiveState,
  event: DesktopTeamRunEvent,
): DesktopTeamLiveState {
  if (state.phase === 'preparing' && event.type === 'snapshot' && event.rosterEpoch === state.rosterEpoch) {
    if (!eventIdentityComplete(event)) return state
    return {
      ...state,
      teamRunId: event.teamRunId,
      runState: asRunState(event.state, state.runState),
      consumedProviderCalls: event.consumedProviderCalls ?? state.consumedProviderCalls,
      maximumProviderCalls: event.maximumProviderCalls ?? state.maximumProviderCalls,
    }
  }
  if (!eventMatches(state, event)) return state
  if (event.type === 'parent_proposing') {
    return withBudget(
      { ...state, phase: 'parent_proposing', runState: asRunState(event.state, state.runState) },
      event,
    )
  }
  if (event.type === 'host_validating') {
    return { ...state, phase: 'host_validating', planRevisionId: event.planRevisionId ?? state.planRevisionId }
  }
  if (event.type === 'proposal') {
    return {
      ...state,
      planRevisionId: event.planRevisionId ?? state.planRevisionId,
      runState: asRunState(event.state, state.runState),
      planSummary: event.planSummary ?? state.planSummary,
    }
  }
  if (event.type === 'wave_starting') {
    const waiting = (event.assignmentIds ?? []).flatMap((assignmentId, index) => {
      if (state.nodes.some((item) => item.assignmentId === assignmentId && item.statusText !== '等待')) {
        return []
      }
      const roleId = asRole(event.employeeRoleIds?.[index])
      if (roleId === undefined || event.waveId === undefined || event.waveId === null) return []
      const existing = state.nodes.find((item) => item.assignmentId === assignmentId)
      if (existing !== undefined) return []
      const node: DesktopTeamNodeView = {
        nodeId: `pending:${assignmentId}`,
        assignmentId,
        invocationId: '',
        employeeRoleId: roleId,
        ordinal: state.nodes.length + index + 1,
        waveId: event.waveId,
        statusText: '等待',
        durationMs: null,
        inputTokens: null,
        outputTokens: null,
        totalTokens: null,
        report: null,
        sendEpoch: 0,
        nodeEpoch: 0,
      }
      return [node]
    })
    return {
      ...state,
      phase: 'wave_starting',
      waveId: event.waveId ?? state.waveId,
      planSummary: event.planSummary ?? state.planSummary,
      declaredExecution: event.declaredExecution ?? state.declaredExecution,
      effectiveExecution: event.effectiveExecution ?? state.effectiveExecution,
      nodes: [...state.nodes, ...waiting],
    }
  }
  if (event.type === 'node_starting' || event.type === 'node_identity') {
    const roleId = asRole(event.employeeRoleId)
    if (
      event.nodeId === undefined ||
      event.assignmentId === undefined ||
      event.invocationId === undefined ||
      roleId === undefined ||
      event.sendEpoch === undefined ||
      event.nodeEpoch === undefined ||
      event.waveId === undefined ||
      event.waveId === null ||
      event.nodeOrdinal === undefined
    ) {
      return withBudget(state, event)
    }
    const node: DesktopTeamNodeView = {
      nodeId: event.nodeId,
      assignmentId: event.assignmentId,
      invocationId: event.invocationId,
      employeeRoleId: roleId,
      ordinal: event.nodeOrdinal,
      waveId: event.waveId,
      statusText: state.cancelRequested ? '正在停止' : '运行中',
      durationMs: null,
      inputTokens: null,
      outputTokens: null,
      totalTokens: null,
      report: null,
      sendEpoch: event.sendEpoch,
      nodeEpoch: event.nodeEpoch,
    }
    const withoutPending = state.nodes.filter(
      (item) => item.nodeId !== `pending:${event.assignmentId}` && item.nodeId !== event.nodeId,
    )
    return withBudget(
      { ...state, phase: 'node_running', nodes: upsertNode(withoutPending, node) },
      event,
    )
  }
  if (event.type === 'node_delta' && event.employeeRoleId === 'parent' && viewingOrigin(state)) {
    return { ...state, parentLiveText: `${state.parentLiveText}${event.text ?? ''}` }
  }
  if (event.type === 'node_terminal' && event.nodeId !== undefined) {
    const existing = state.nodes.find((item) => item.nodeId === event.nodeId)
    if (existing === undefined) return state
    if (event.sendEpoch !== undefined && event.sendEpoch !== existing.sendEpoch) return state
    if (event.invocationId !== undefined && event.invocationId !== existing.invocationId) return state
    const statusText: DesktopTeamNodeStatusText =
      event.errorCode === 'desktop_invocation_cancelled'
        ? '正在停止'
        : event.errorCode
          ? '失败'
          : event.reportStatus === 'needs_collaboration'
            ? '需要协作'
            : event.reportStatus === 'blocked'
              ? '失败'
              : '已完成'
    return withBudget(
      {
        ...state,
        collaborationLines:
          event.collaborationLine === undefined || event.collaborationLine === ''
            ? state.collaborationLines
            : [...state.collaborationLines, event.collaborationLine],
        nodes: upsertNode(state.nodes, {
          ...existing,
          statusText,
          durationMs: event.durationMs ?? existing.durationMs,
          inputTokens: event.inputTokens ?? existing.inputTokens,
          outputTokens: event.outputTokens ?? existing.outputTokens,
          totalTokens: event.totalTokens ?? existing.totalTokens,
          report: event.answer ?? existing.report,
        }),
      },
      event,
    )
  }
  if (event.type === 'blackboard') {
    return { ...state, phase: 'blackboard_updated' }
  }
  if (event.type === 'parent_replanning') {
    return { ...state, phase: 'parent_replanning' }
  }
  if (event.type === 'parent_synthesizing') {
    return { ...state, phase: 'parent_synthesizing' }
  }
  if (event.type === 'completed') {
    const finalAnswer = event.parentFinalAnswer ?? state.parentLiveText
    return withBudget(
      {
        ...state,
        phase: 'completed',
        runState: 'succeeded',
        parentFinalAnswer: viewingOrigin(state) ? finalAnswer : state.parentFinalAnswer,
        parentLiveText: viewingOrigin(state) ? finalAnswer : '',
      },
      event,
    )
  }
  if (event.type === 'budget_exhausted') {
    return withBudget({ ...state, phase: 'budget_exhausted', runState: 'budget_exhausted' }, event)
  }
  if (event.type === 'cancelled') {
    return {
      ...state,
      phase: 'cancelled',
      runState: 'cancelled',
      nodes: state.nodes.map((node) =>
        node.statusText === '运行中' ? { ...node, statusText: '正在停止' } : node,
      ),
    }
  }
  if (event.type === 'unknown') {
    return { ...state, phase: 'unknown', runState: 'unknown' }
  }
  if (event.type === 'failed') {
    return { ...state, phase: 'failed', runState: asRunState(event.state, 'failed') }
  }
  return state
}

export function completeDesktopTeamRun(state: DesktopTeamLiveState): DesktopTeamLiveState {
  if (
    state.phase === 'completed' ||
    state.phase === 'cancelled' ||
    state.phase === 'failed' ||
    state.phase === 'budget_exhausted' ||
    state.phase === 'cannot_complete' ||
    state.phase === 'unknown'
  ) {
    return { ...state, phase: 'idle', cancelRequested: false }
  }
  return state
}

export function failDesktopTeamPreStart(state: DesktopTeamLiveState): DesktopTeamLiveState {
  if (state.phase === 'idle') return state
  const terminal = completeDesktopTeamRun(state)
  if (terminal.phase === 'idle') return terminal
  return completeDesktopTeamRun({
    ...state,
    phase: 'failed',
    runState: 'failed',
  })
}
