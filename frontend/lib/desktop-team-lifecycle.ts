export type DesktopTeamRunEvent = {
  readonly type: string
  readonly teamRunId: string
  readonly workspaceId: string
  readonly conversationId?: string
  readonly state?: string
  readonly planRevisionId?: string | null
  readonly oldPlanRevisionId?: string | null
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
  readonly parkedNodes: readonly DesktopTeamNodeView[]
  readonly parkedCollaborationLines: readonly string[]
  readonly parkedPhase: DesktopTeamPhase
  readonly parkedRunState: TeamRunState | null
  readonly parkedPlanRevisionId: string | null
  readonly parkedWaveId: string | null
  readonly parkedPlanSummary: string | null
  readonly parkedDeclaredExecution: 'serial' | 'parallel' | null
  readonly parkedEffectiveExecution: 'serial' | 'parallel' | null
  readonly parkedConsumedProviderCalls: number
  readonly parkedMaximumProviderCalls: number
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
    parkedNodes: [],
    parkedCollaborationLines: [],
    parkedPhase: 'idle',
    parkedRunState: null,
    parkedPlanRevisionId: null,
    parkedWaveId: null,
    parkedPlanSummary: null,
    parkedDeclaredExecution: null,
    parkedEffectiveExecution: null,
    parkedConsumedProviderCalls: 0,
    parkedMaximumProviderCalls: 0,
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

function originPhase(state: DesktopTeamLiveState): DesktopTeamPhase {
  return viewingOrigin(state) ? state.phase : state.parkedPhase
}

function originPlanRevisionId(state: DesktopTeamLiveState): string | null {
  return viewingOrigin(state) ? state.planRevisionId : state.parkedPlanRevisionId
}

function originWaveId(state: DesktopTeamLiveState): string | null {
  return viewingOrigin(state) ? state.waveId : state.parkedWaveId
}

function originConsumedProviderCalls(state: DesktopTeamLiveState): number {
  return viewingOrigin(state) ? state.consumedProviderCalls : state.parkedConsumedProviderCalls
}

function originMaximumProviderCalls(state: DesktopTeamLiveState): number {
  return viewingOrigin(state) ? state.maximumProviderCalls : state.parkedMaximumProviderCalls
}

function originRunState(state: DesktopTeamLiveState): TeamRunState | null {
  return viewingOrigin(state) ? state.runState : state.parkedRunState
}

function originPlanSummary(state: DesktopTeamLiveState): string | null {
  return viewingOrigin(state) ? state.planSummary : state.parkedPlanSummary
}

function originDeclaredExecution(state: DesktopTeamLiveState): 'serial' | 'parallel' | null {
  return viewingOrigin(state) ? state.declaredExecution : state.parkedDeclaredExecution
}

function originEffectiveExecution(state: DesktopTeamLiveState): 'serial' | 'parallel' | null {
  return viewingOrigin(state) ? state.effectiveExecution : state.parkedEffectiveExecution
}

const HIDDEN_ORIGIN_CHROME = {
  phase: 'idle' as const,
  runState: null,
  planRevisionId: null,
  waveId: null,
  planSummary: null,
  declaredExecution: null,
  effectiveExecution: null,
  consumedProviderCalls: 0,
  maximumProviderCalls: 0,
}

function withOriginChrome(
  state: DesktopTeamLiveState,
  patch: {
    readonly phase?: DesktopTeamPhase
    readonly runState?: TeamRunState | null
    readonly planRevisionId?: string | null
    readonly waveId?: string | null
    readonly planSummary?: string | null
    readonly declaredExecution?: 'serial' | 'parallel' | null
    readonly effectiveExecution?: 'serial' | 'parallel' | null
    readonly consumedProviderCalls?: number
    readonly maximumProviderCalls?: number
  },
): Pick<
  DesktopTeamLiveState,
  | 'phase'
  | 'runState'
  | 'planRevisionId'
  | 'waveId'
  | 'planSummary'
  | 'declaredExecution'
  | 'effectiveExecution'
  | 'consumedProviderCalls'
  | 'maximumProviderCalls'
  | 'parkedPhase'
  | 'parkedRunState'
  | 'parkedPlanRevisionId'
  | 'parkedWaveId'
  | 'parkedPlanSummary'
  | 'parkedDeclaredExecution'
  | 'parkedEffectiveExecution'
  | 'parkedConsumedProviderCalls'
  | 'parkedMaximumProviderCalls'
> {
  const next = {
    phase: patch.phase ?? originPhase(state),
    runState: patch.runState !== undefined ? patch.runState : originRunState(state),
    planRevisionId: patch.planRevisionId !== undefined ? patch.planRevisionId : originPlanRevisionId(state),
    waveId: patch.waveId !== undefined ? patch.waveId : originWaveId(state),
    planSummary: patch.planSummary !== undefined ? patch.planSummary : originPlanSummary(state),
    declaredExecution:
      patch.declaredExecution !== undefined ? patch.declaredExecution : originDeclaredExecution(state),
    effectiveExecution:
      patch.effectiveExecution !== undefined ? patch.effectiveExecution : originEffectiveExecution(state),
    consumedProviderCalls: patch.consumedProviderCalls ?? originConsumedProviderCalls(state),
    maximumProviderCalls: patch.maximumProviderCalls ?? originMaximumProviderCalls(state),
  }
  if (viewingOrigin(state)) {
    return {
      ...next,
      parkedPhase: state.parkedPhase,
      parkedRunState: state.parkedRunState,
      parkedPlanRevisionId: state.parkedPlanRevisionId,
      parkedWaveId: state.parkedWaveId,
      parkedPlanSummary: state.parkedPlanSummary,
      parkedDeclaredExecution: state.parkedDeclaredExecution,
      parkedEffectiveExecution: state.parkedEffectiveExecution,
      parkedConsumedProviderCalls: state.parkedConsumedProviderCalls,
      parkedMaximumProviderCalls: state.parkedMaximumProviderCalls,
    }
  }
  return {
    ...HIDDEN_ORIGIN_CHROME,
    parkedPhase: next.phase,
    parkedRunState: next.runState,
    parkedPlanRevisionId: next.planRevisionId,
    parkedWaveId: next.waveId,
    parkedPlanSummary: next.planSummary,
    parkedDeclaredExecution: next.declaredExecution,
    parkedEffectiveExecution: next.effectiveExecution,
    parkedConsumedProviderCalls: next.consumedProviderCalls,
    parkedMaximumProviderCalls: next.maximumProviderCalls,
  }
}

function workingNodes(state: DesktopTeamLiveState): readonly DesktopTeamNodeView[] {
  return viewingOrigin(state) ? state.nodes : state.parkedNodes
}

function workingCollaboration(state: DesktopTeamLiveState): readonly string[] {
  return viewingOrigin(state) ? state.collaborationLines : state.parkedCollaborationLines
}

function workingParentLiveText(state: DesktopTeamLiveState): string {
  return viewingOrigin(state) ? state.parentLiveText : state.parkedParentLiveText
}

function commitVisible(
  state: DesktopTeamLiveState,
  patch: {
    readonly nodes?: readonly DesktopTeamNodeView[]
    readonly parentLiveText?: string
    readonly parentFinalAnswer?: string | null
    readonly collaborationLines?: readonly string[]
  },
): Pick<
  DesktopTeamLiveState,
  | 'nodes'
  | 'parentLiveText'
  | 'parentFinalAnswer'
  | 'collaborationLines'
  | 'parkedParentLiveText'
  | 'parkedParentFinalAnswer'
  | 'parkedNodes'
  | 'parkedCollaborationLines'
> {
  if (viewingOrigin(state)) {
    return {
      nodes: patch.nodes ?? state.nodes,
      parentLiveText: patch.parentLiveText ?? state.parentLiveText,
      parentFinalAnswer: patch.parentFinalAnswer !== undefined ? patch.parentFinalAnswer : state.parentFinalAnswer,
      collaborationLines: patch.collaborationLines ?? state.collaborationLines,
      parkedParentLiveText: state.parkedParentLiveText,
      parkedParentFinalAnswer: state.parkedParentFinalAnswer,
      parkedNodes: state.parkedNodes,
      parkedCollaborationLines: state.parkedCollaborationLines,
    }
  }
  return {
    nodes: [],
    parentLiveText: '',
    parentFinalAnswer: null,
    collaborationLines: [],
    parkedParentLiveText: patch.parentLiveText ?? state.parkedParentLiveText,
    parkedParentFinalAnswer:
      patch.parentFinalAnswer !== undefined ? patch.parentFinalAnswer : state.parkedParentFinalAnswer,
    parkedNodes: patch.nodes ?? state.parkedNodes,
    parkedCollaborationLines: patch.collaborationLines ?? state.parkedCollaborationLines,
  }
}

function eventIdentityComplete(event: DesktopTeamRunEvent): boolean {
  const scope =
    typeof event.workspaceId === 'string' &&
    event.workspaceId.length > 0 &&
    typeof event.conversationId === 'string' &&
    event.conversationId.length > 0 &&
    typeof event.teamRunId === 'string' &&
    event.teamRunId.length > 0 &&
    typeof event.rosterEpoch === 'number' &&
    Number.isInteger(event.rosterEpoch)
  if (!scope) return false
  if (event.type === 'plan_transition') {
    return (
      typeof event.oldPlanRevisionId === 'string' &&
      event.oldPlanRevisionId.length > 0 &&
      typeof event.planRevisionId === 'string' &&
      event.planRevisionId.length > 0 &&
      event.oldPlanRevisionId !== event.planRevisionId
    )
  }
  if (
    event.type === 'node_starting' ||
    event.type === 'node_identity' ||
    event.type === 'node_delta' ||
    event.type === 'node_terminal'
  ) {
    if (event.employeeRoleId === 'parent') {
      return typeof event.planRevisionId === 'string' && typeof event.waveId === 'string'
    }
    return (
      typeof event.planRevisionId === 'string' &&
      event.planRevisionId.length > 0 &&
      typeof event.waveId === 'string' &&
      event.waveId.length > 0 &&
      typeof event.assignmentId === 'string' &&
      event.assignmentId.length > 0 &&
      typeof event.nodeId === 'string' &&
      event.nodeId.length > 0 &&
      typeof event.invocationId === 'string' &&
      event.invocationId.length > 0 &&
      typeof event.employeeRoleId === 'string' &&
      typeof event.nodeEpoch === 'number' &&
      Number.isInteger(event.nodeEpoch) &&
      typeof event.sendEpoch === 'number' &&
      Number.isInteger(event.sendEpoch)
    )
  }
  return typeof event.planRevisionId === 'string' && typeof event.waveId === 'string'
}

function eventMatches(state: DesktopTeamLiveState, event: DesktopTeamRunEvent): boolean {
  if (!eventIdentityComplete(event)) return false
  if (state.teamRunId === null) return false
  if (event.teamRunId !== state.teamRunId) return false
  if (event.workspaceId !== state.originWorkspaceId) return false
  if (event.conversationId !== state.originConversationId) return false
  if (event.rosterEpoch !== state.rosterEpoch) return false
  if (event.type === 'plan_transition') {
    return event.oldPlanRevisionId === originPlanRevisionId(state) && event.planRevisionId !== originPlanRevisionId(state)
  }
  const planRevisionId = originPlanRevisionId(state)
  if (
    planRevisionId !== null &&
    planRevisionId !== '' &&
    event.planRevisionId !== planRevisionId
  ) {
    return false
  }
  const waveId = originWaveId(state)
  if (
    (event.type === 'node_starting' ||
      event.type === 'node_identity' ||
      event.type === 'node_delta' ||
      event.type === 'node_terminal') &&
    waveId !== null &&
    event.waveId !== waveId
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
  if (
    originPhase(state) !== 'idle' &&
    originPhase(state) !== 'completed' &&
    originPhase(state) !== 'cancelled' &&
    originPhase(state) !== 'failed' &&
    originPhase(state) !== 'budget_exhausted' &&
    originPhase(state) !== 'cannot_complete' &&
    originPhase(state) !== 'unknown'
  ) {
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
    parkedNodes: [],
    parkedCollaborationLines: [],
    parkedPhase: 'idle',
    parkedRunState: null,
    parkedPlanRevisionId: null,
    parkedWaveId: null,
    parkedPlanSummary: null,
    parkedDeclaredExecution: null,
    parkedEffectiveExecution: null,
    parkedConsumedProviderCalls: 0,
    parkedMaximumProviderCalls: 0,
    planSummary: null,
    declaredExecution: null,
    effectiveExecution: null,
  }
}

export function requestDesktopTeamCancel(state: DesktopTeamLiveState): DesktopTeamLiveState {
  const phase = originPhase(state)
  if (phase === 'idle' || phase === 'completed' || phase === 'cancelled') {
    return state
  }
  return { ...state, cancelRequested: true, ...withOriginChrome(state, { phase: 'cancelling' }) }
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
  if (leavingOrigin) {
    return {
      ...state,
      workspaceId,
      conversationId,
      parentLiveText: '',
      parentFinalAnswer: null,
      nodes: [],
      collaborationLines: [],
      parkedParentLiveText: state.parentLiveText,
      parkedParentFinalAnswer: state.parentFinalAnswer,
      parkedNodes: state.nodes,
      parkedCollaborationLines: state.collaborationLines,
      parkedPhase: state.phase,
      parkedRunState: state.runState,
      parkedPlanRevisionId: state.planRevisionId,
      parkedWaveId: state.waveId,
      parkedPlanSummary: state.planSummary,
      parkedDeclaredExecution: state.declaredExecution,
      parkedEffectiveExecution: state.effectiveExecution,
      parkedConsumedProviderCalls: state.consumedProviderCalls,
      parkedMaximumProviderCalls: state.maximumProviderCalls,
      ...HIDDEN_ORIGIN_CHROME,
    }
  }
  if (returningOrigin) {
    return {
      ...state,
      workspaceId,
      conversationId,
      parentLiveText: state.parkedParentLiveText,
      parentFinalAnswer: state.parkedParentFinalAnswer,
      nodes: state.parkedNodes,
      collaborationLines: state.parkedCollaborationLines,
      phase: state.parkedPhase,
      runState: state.parkedRunState,
      planRevisionId: state.parkedPlanRevisionId,
      waveId: state.parkedWaveId,
      planSummary: state.parkedPlanSummary,
      declaredExecution: state.parkedDeclaredExecution,
      effectiveExecution: state.parkedEffectiveExecution,
      consumedProviderCalls: state.parkedConsumedProviderCalls,
      maximumProviderCalls: state.parkedMaximumProviderCalls,
      parkedParentLiveText: '',
      parkedParentFinalAnswer: null,
      parkedNodes: [],
      parkedCollaborationLines: [],
      parkedPhase: 'idle',
      parkedRunState: null,
      parkedPlanRevisionId: null,
      parkedWaveId: null,
      parkedPlanSummary: null,
      parkedDeclaredExecution: null,
      parkedEffectiveExecution: null,
      parkedConsumedProviderCalls: 0,
      parkedMaximumProviderCalls: 0,
    }
  }
  return { ...state, workspaceId, conversationId }
}

export function desktopTeamStopVisible(state: DesktopTeamLiveState): boolean {
  const phase = originPhase(state)
  return (
    phase === 'preparing' ||
    phase === 'parent_proposing' ||
    phase === 'host_validating' ||
    phase === 'wave_starting' ||
    phase === 'node_running' ||
    phase === 'parent_replanning' ||
    phase === 'parent_synthesizing' ||
    phase === 'cancelling'
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
  if (!viewingOrigin(state)) return '静默'
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

function withBudget(state: DesktopTeamLiveState, event: DesktopTeamRunEvent, chrome: {
  readonly phase?: DesktopTeamPhase
  readonly runState?: TeamRunState | null
  readonly planRevisionId?: string | null
  readonly waveId?: string | null
  readonly planSummary?: string | null
  readonly declaredExecution?: 'serial' | 'parallel' | null
  readonly effectiveExecution?: 'serial' | 'parallel' | null
} = {}): DesktopTeamLiveState {
  return {
    ...state,
    ...withOriginChrome(state, {
      ...chrome,
      consumedProviderCalls: event.consumedProviderCalls ?? originConsumedProviderCalls(state),
      maximumProviderCalls: event.maximumProviderCalls ?? originMaximumProviderCalls(state),
    }),
  }
}

function teamTerminalLatched(state: DesktopTeamLiveState): boolean {
  const phase = originPhase(state)
  const runState = originRunState(state)
  return (
    phase === 'completed' ||
    phase === 'cancelled' ||
    phase === 'failed' ||
    phase === 'unknown' ||
    phase === 'budget_exhausted' ||
    phase === 'cannot_complete' ||
    runState === 'succeeded' ||
    runState === 'cancelled' ||
    runState === 'failed' ||
    runState === 'unknown' ||
    runState === 'budget_exhausted' ||
    runState === 'cannot_complete'
  )
}

function terminalPhaseForRunState(runState: TeamRunState | null): DesktopTeamPhase | null {
  switch (runState) {
    case 'succeeded':
      return 'completed'
    case 'failed':
      return 'failed'
    case 'cancelled':
      return 'cancelled'
    case 'unknown':
      return 'unknown'
    case 'budget_exhausted':
      return 'budget_exhausted'
    case 'cannot_complete':
      return 'cannot_complete'
    default:
      return null
  }
}

function firstSnapshotEligible(state: DesktopTeamLiveState): boolean {
  const phase = originPhase(state)
  return phase === 'preparing' || (phase === 'cancelling' && state.cancelRequested)
}

function firstSnapshotMatchesOrigin(
  state: DesktopTeamLiveState,
  event: DesktopTeamRunEvent,
): boolean {
  return (
    event.workspaceId === state.originWorkspaceId &&
    event.conversationId === state.originConversationId
  )
}

export function pendingDurableTeamCancel(
  state: DesktopTeamLiveState,
  lastCancelledTeamRunId: string | null,
): string | null {
  if (state.teamRunId === null) return null
  if (!state.cancelRequested) return null
  if (state.teamRunId === lastCancelledTeamRunId) return null
  return state.teamRunId
}

export function desktopTeamAppendBudgetTarget(
  state: DesktopTeamLiveState,
): { readonly workspaceId: string; readonly teamRunId: string } | null {
  if (state.teamRunId === null || state.originWorkspaceId === null) return null
  if (!viewingOrigin(state)) return null
  return { workspaceId: state.originWorkspaceId, teamRunId: state.teamRunId }
}

export function reduceDesktopTeamEvent(
  state: DesktopTeamLiveState,
  event: DesktopTeamRunEvent,
): DesktopTeamLiveState {
  if (
    event.type === 'snapshot' &&
    event.rosterEpoch === state.rosterEpoch &&
    firstSnapshotEligible(state)
  ) {
    if (
      !eventIdentityComplete(event) ||
      !firstSnapshotMatchesOrigin(state, event) ||
      (state.teamRunId !== null && state.teamRunId !== event.teamRunId)
    ) {
      return state
    }
    const snapshotRunState = asRunState(event.state, originRunState(state))
    const terminalPhase = terminalPhaseForRunState(snapshotRunState)
    return {
      ...state,
      teamRunId: event.teamRunId,
      ...withOriginChrome(state, {
        ...(terminalPhase === null ? {} : { phase: terminalPhase }),
        runState: snapshotRunState,
        consumedProviderCalls: event.consumedProviderCalls ?? originConsumedProviderCalls(state),
        maximumProviderCalls: event.maximumProviderCalls ?? originMaximumProviderCalls(state),
      }),
    }
  }
  if (!eventMatches(state, event)) return state
  if (teamTerminalLatched(state)) return state
  if (event.type === 'parent_proposing') {
    return withBudget(state, event, {
      phase: 'parent_proposing',
      runState: asRunState(event.state, originRunState(state)),
    })
  }
  if (event.type === 'host_validating') {
    return {
      ...state,
      ...withOriginChrome(state, {
        phase: 'host_validating',
        planRevisionId: event.planRevisionId ?? originPlanRevisionId(state),
      }),
    }
  }
  if (event.type === 'plan_transition') {
    return {
      ...state,
      ...withOriginChrome(state, {
        planRevisionId: event.planRevisionId ?? originPlanRevisionId(state),
        waveId: null,
      }),
    }
  }
  if (event.type === 'proposal') {
    return {
      ...state,
      ...withOriginChrome(state, {
        planRevisionId: event.planRevisionId ?? originPlanRevisionId(state),
        runState: asRunState(event.state, originRunState(state)),
        planSummary: event.planSummary ?? originPlanSummary(state),
      }),
    }
  }
  if (event.type === 'wave_starting') {
    const currentNodes = workingNodes(state)
    const waiting = (event.assignmentIds ?? []).flatMap((assignmentId, index) => {
      if (currentNodes.some((item) => item.assignmentId === assignmentId && item.statusText !== '等待')) {
        return []
      }
      const roleId = asRole(event.employeeRoleIds?.[index])
      if (roleId === undefined || event.waveId === undefined || event.waveId === null) return []
      const existing = currentNodes.find((item) => item.assignmentId === assignmentId)
      if (existing !== undefined) return []
      const node: DesktopTeamNodeView = {
        nodeId: `pending:${assignmentId}`,
        assignmentId,
        invocationId: '',
        employeeRoleId: roleId,
        ordinal: currentNodes.length + index + 1,
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
      ...withBudget(state, event, {
        phase: 'wave_starting',
        waveId: event.waveId ?? originWaveId(state),
        planSummary: event.planSummary ?? originPlanSummary(state),
        declaredExecution: event.declaredExecution ?? originDeclaredExecution(state),
        effectiveExecution: event.effectiveExecution ?? originEffectiveExecution(state),
      }),
      ...commitVisible(state, { nodes: [...currentNodes, ...waiting] }),
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
    const withoutPending = workingNodes(state).filter(
      (item) => item.nodeId !== `pending:${event.assignmentId}` && item.nodeId !== event.nodeId,
    )
    return {
      ...withBudget(state, event, { phase: 'node_running' }),
      ...commitVisible(state, { nodes: upsertNode(withoutPending, node) }),
    }
  }
  if (event.type === 'node_delta' && event.employeeRoleId === 'parent') {
    return {
      ...state,
      ...commitVisible(state, { parentLiveText: `${workingParentLiveText(state)}${event.text ?? ''}` }),
    }
  }
  if (event.type === 'node_terminal' && event.nodeId !== undefined) {
    const existing = workingNodes(state).find((item) => item.nodeId === event.nodeId)
    if (existing === undefined) return state
    if (event.assignmentId !== existing.assignmentId) return state
    if (event.employeeRoleId !== existing.employeeRoleId) return state
    if (event.invocationId !== existing.invocationId) return state
    if (event.waveId !== existing.waveId) return state
    if (event.nodeEpoch !== existing.nodeEpoch) return state
    if (event.sendEpoch !== existing.sendEpoch) return state
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
    const collaboration =
      event.collaborationLine === undefined || event.collaborationLine === ''
        ? workingCollaboration(state)
        : [...workingCollaboration(state), event.collaborationLine]
    return {
      ...withBudget(state, event),
      ...commitVisible(state, {
        collaborationLines: collaboration,
        nodes: upsertNode(workingNodes(state), {
          ...existing,
          statusText,
          durationMs: event.durationMs ?? existing.durationMs,
          inputTokens: event.inputTokens ?? existing.inputTokens,
          outputTokens: event.outputTokens ?? existing.outputTokens,
          totalTokens: event.totalTokens ?? existing.totalTokens,
          report: event.answer ?? existing.report,
        }),
      }),
    }
  }
  if (event.type === 'blackboard') {
    return { ...state, ...withOriginChrome(state, { phase: 'blackboard_updated' }) }
  }
  if (event.type === 'parent_replanning') {
    return { ...state, ...withOriginChrome(state, { phase: 'parent_replanning' }) }
  }
  if (event.type === 'parent_synthesizing') {
    return { ...state, ...withOriginChrome(state, { phase: 'parent_synthesizing' }) }
  }
  if (event.type === 'completed') {
    const finalAnswer = event.parentFinalAnswer ?? workingParentLiveText(state)
    return {
      ...withBudget(state, event, { phase: 'completed', runState: 'succeeded' }),
      ...commitVisible(state, { parentFinalAnswer: finalAnswer, parentLiveText: finalAnswer }),
    }
  }
  if (event.type === 'budget_exhausted') {
    return withBudget(state, event, { phase: 'budget_exhausted', runState: 'budget_exhausted' })
  }
  if (event.type === 'cancelled') {
    return {
      ...state,
      ...withOriginChrome(state, { phase: 'cancelled', runState: 'cancelled' }),
      ...commitVisible(state, {
        nodes: workingNodes(state).map((node) =>
          node.statusText === '运行中' ? { ...node, statusText: '正在停止' } : node,
        ),
      }),
    }
  }
  if (event.type === 'unknown') {
    return { ...state, ...withOriginChrome(state, { phase: 'unknown', runState: 'unknown' }) }
  }
  if (event.type === 'failed') {
    return {
      ...state,
      ...withOriginChrome(state, { phase: 'failed', runState: asRunState(event.state, 'failed') }),
    }
  }
  return state
}

export function completeDesktopTeamRun(state: DesktopTeamLiveState): DesktopTeamLiveState {
  const phase = originPhase(state)
  if (
    phase === 'completed' ||
    phase === 'cancelled' ||
    phase === 'failed' ||
    phase === 'budget_exhausted' ||
    phase === 'cannot_complete' ||
    phase === 'unknown'
  ) {
    return { ...state, cancelRequested: false, ...withOriginChrome(state, { phase: 'idle' }) }
  }
  return state
}

export function failDesktopTeamPreStart(state: DesktopTeamLiveState): DesktopTeamLiveState {
  if (originPhase(state) === 'idle') return state
  const terminal = completeDesktopTeamRun(state)
  if (originPhase(terminal) === 'idle') return terminal
  return completeDesktopTeamRun({
    ...state,
    ...withOriginChrome(state, { phase: 'failed', runState: 'failed' }),
  })
}
