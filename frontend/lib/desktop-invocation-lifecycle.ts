/**
 * Personal-edition desktop invocation lifecycle (P6.8-A).
 *
 * One global live invocation is represented as a finite state, not a loose
 * combination of liveActive / streaming / cancelRequested / liveInvocation.
 *
 * idle → send → starting_identity → identity → running
 *   → Stop → cancelling → cancelled|terminal → convergence → idle
 *
 * Membership of identity, delta and terminal events is by sendEpoch plus the
 * bound invocation id. Workspace or conversation identity alone never ends or
 * rebinds a call. Scope switches do not move origin*.
 */

export type DesktopInvocationPhase =
  | 'idle'
  | 'send'
  | 'starting_identity'
  | 'identity'
  | 'running'
  | 'cancelling'
  | 'cancelled'
  | 'terminal'
  | 'convergence'

export interface DesktopInvocationStreamEvent {
  readonly type: 'identity' | 'delta' | 'done' | 'cancelled' | 'error'
  readonly invocationId: string
  readonly workspaceId?: string
  readonly conversationId?: string
  readonly messageId?: string
  readonly text?: string
  readonly answer?: string
  readonly providerName?: string
  readonly requestedModel?: string
  readonly actualModel?: string | null
  readonly family?: string
  readonly gear?: string
  readonly thinkingDepth?: string
  readonly status?: string
  readonly durationMs?: number
  readonly inputTokens?: number | null
  readonly outputTokens?: number | null
  readonly totalTokens?: number | null
  readonly errorCode?: string
  readonly errorRedacted?: string
  readonly sendEpoch?: number
}

export interface DesktopLiveStreamState {
  readonly workspaceId: string | null
  readonly conversationId: string | null
  readonly originWorkspaceId: string | null
  readonly originConversationId: string | null
  readonly sendEpoch: number
  readonly sendGeneration: number
  readonly invocationId: string | null
  readonly liveInvocation: string | null
  readonly phase: DesktopInvocationPhase
  readonly cancelRequested: boolean
  readonly cancelDispatched: boolean
  readonly terminalStatus: string | null
  readonly promiseOpen: boolean
  readonly retiredInvocationIds: readonly string[]
  readonly liveText: string
  readonly liveMeta: DesktopInvocationStreamEvent | null
  readonly parkedLiveText: string
  readonly parkedLiveMeta: DesktopInvocationStreamEvent | null
  readonly streaming: boolean
  readonly liveActive: boolean
}

export interface DesktopInvocationEventResult {
  readonly state: DesktopLiveStreamState
  readonly cancelInvocationId: string | null
}

export interface DesktopInvocationLiveProjection {
  readonly liveText: string
  readonly liveMeta: DesktopInvocationStreamEvent | null
  readonly visible: boolean
}

const MAX_RETIRED_INVOCATION_IDS = 32

const AWAITING_IDENTITY: ReadonlySet<DesktopInvocationPhase> = new Set([
  'send',
  'starting_identity',
])

const STOPPABLE: ReadonlySet<DesktopInvocationPhase> = new Set([
  'send',
  'starting_identity',
  'identity',
  'running',
])

const LIVE: ReadonlySet<DesktopInvocationPhase> = new Set([
  'send',
  'starting_identity',
  'identity',
  'running',
  'cancelling',
])

const STREAMING: ReadonlySet<DesktopInvocationPhase> = new Set([
  'send',
  'starting_identity',
  'identity',
  'running',
])

const TERMINAL_PHASES: ReadonlySet<DesktopInvocationPhase> = new Set([
  'cancelled',
  'terminal',
  'convergence',
  'idle',
])

function retireInvocationId(
  ids: readonly string[],
  invocationId: string | null | undefined,
): readonly string[] {
  if (invocationId === null || invocationId === undefined || invocationId === '') {
    return ids
  }
  if (ids.includes(invocationId)) return ids
  const next = [...ids, invocationId]
  return next.length > MAX_RETIRED_INVOCATION_IDS
    ? next.slice(next.length - MAX_RETIRED_INVOCATION_IDS)
    : next
}

function viewingOrigin(state: Pick<
  DesktopLiveStreamState,
  'workspaceId' | 'conversationId' | 'originWorkspaceId' | 'originConversationId'
>): boolean {
  return (
    state.workspaceId === state.originWorkspaceId &&
    state.conversationId === state.originConversationId
  )
}

function eventMatchesOrigin(
  state: DesktopLiveStreamState,
  event: DesktopInvocationStreamEvent,
): boolean {
  return (
    event.workspaceId === state.originWorkspaceId &&
    event.conversationId === state.originConversationId
  )
}

function eventMatchesSendEpoch(
  state: DesktopLiveStreamState,
  event: DesktopInvocationStreamEvent,
): boolean {
  return event.sendEpoch === undefined || event.sendEpoch === state.sendEpoch
}

function isRetired(state: DesktopLiveStreamState, invocationId: string): boolean {
  return state.retiredInvocationIds.includes(invocationId)
}

function finalize(
  state: Omit<DesktopLiveStreamState, 'liveInvocation' | 'sendGeneration' | 'streaming' | 'liveActive'>,
): DesktopLiveStreamState {
  const originView = viewingOrigin(state)
  return {
    ...state,
    liveInvocation: state.invocationId,
    sendGeneration: state.sendEpoch,
    liveActive: LIVE.has(state.phase),
    streaming: originView && STREAMING.has(state.phase),
  }
}

function inferPhase(input: {
  readonly phase?: DesktopInvocationPhase
  readonly streaming: boolean
  readonly liveActive?: boolean
  readonly invocationId: string | null
  readonly cancelRequested: boolean
}): DesktopInvocationPhase {
  if (input.phase !== undefined) return input.phase
  if (input.cancelRequested && (input.liveActive !== false || input.invocationId !== null)) {
    return 'cancelling'
  }
  if (input.streaming || input.liveActive === true || input.invocationId !== null) {
    return input.invocationId === null ? 'starting_identity' : 'running'
  }
  return 'idle'
}

export function createDesktopLiveStreamState(
  input: Partial<DesktopLiveStreamState> = {},
): DesktopLiveStreamState {
  const workspaceId = input.workspaceId ?? null
  const conversationId = input.conversationId ?? null
  const invocationId = input.invocationId ?? input.liveInvocation ?? null
  const cancelRequested = input.cancelRequested ?? false
  const phase = inferPhase({
    phase: input.phase,
    streaming: input.streaming ?? false,
    liveActive: input.liveActive,
    invocationId,
    cancelRequested,
  })
  return finalize({
    workspaceId,
    conversationId,
    originWorkspaceId: input.originWorkspaceId ?? workspaceId,
    originConversationId: input.originConversationId ?? conversationId,
    sendEpoch: input.sendEpoch ?? input.sendGeneration ?? 0,
    invocationId,
    phase,
    cancelRequested,
    cancelDispatched: input.cancelDispatched ?? false,
    terminalStatus: input.terminalStatus ?? null,
    promiseOpen: input.promiseOpen ?? LIVE.has(phase),
    retiredInvocationIds: input.retiredInvocationIds ?? [],
    liveText: input.liveText ?? '',
    liveMeta: input.liveMeta ?? null,
    parkedLiveText: input.parkedLiveText ?? '',
    parkedLiveMeta: input.parkedLiveMeta ?? null,
  })
}

export function desktopLiveViewIsOrigin(state: DesktopLiveStreamState): boolean {
  return viewingOrigin(state)
}

export function desktopInvocationCanSend(state: DesktopLiveStreamState): boolean {
  return state.phase === 'idle'
}

export function desktopLiveSendBlocked(state: DesktopLiveStreamState): boolean {
  return state.phase !== 'idle'
}

export function desktopInvocationStopVisible(state: DesktopLiveStreamState): boolean {
  return STOPPABLE.has(state.phase)
}

export function desktopLiveStopVisible(state: DesktopLiveStreamState): boolean {
  return desktopInvocationStopVisible(state)
}

export function desktopInvocationIsStopping(state: DesktopLiveStreamState): boolean {
  return state.phase === 'cancelling' || (state.cancelRequested && state.phase !== 'idle')
}

export function desktopInvocationCancelTarget(state: DesktopLiveStreamState): string | null {
  if (!state.cancelRequested || state.cancelDispatched || state.invocationId === null) {
    return null
  }
  return state.invocationId
}

export function desktopInvocationLiveProjection(
  state: DesktopLiveStreamState,
  workspaceId: string | null,
  conversationId: string | null,
): DesktopInvocationLiveProjection {
  const visible =
    state.originWorkspaceId === workspaceId &&
    state.originConversationId === conversationId &&
    state.phase !== 'idle'
  if (!visible) {
    return { liveText: '', liveMeta: null, visible: false }
  }
  const onOriginView = viewingOrigin(state)
  return {
    liveText: onOriginView ? state.liveText : state.parkedLiveText || state.liveText,
    liveMeta: onOriginView ? state.liveMeta : state.parkedLiveMeta ?? state.liveMeta,
    visible: true,
  }
}

export function markDesktopInvocationCancelDispatched(
  state: DesktopLiveStreamState,
): DesktopLiveStreamState {
  if (state.cancelDispatched || state.invocationId === null) return state
  return finalize({
    ...state,
    cancelDispatched: true,
    phase: state.phase === 'running' || state.phase === 'identity' ? 'cancelling' : state.phase,
    cancelRequested: true,
  })
}

export function beginDesktopLiveSend(state: DesktopLiveStreamState): DesktopLiveStreamState {
  if (state.phase === 'cancelling' || state.phase === 'convergence') {
    return state
  }
  if (state.cancelRequested && state.phase !== 'idle') {
    return state
  }
  const retired = retireInvocationId(state.retiredInvocationIds, state.invocationId)
  return finalize({
    workspaceId: state.workspaceId,
    conversationId: state.conversationId,
    originWorkspaceId: state.workspaceId,
    originConversationId: state.conversationId,
    sendEpoch: state.sendEpoch + 1,
    invocationId: null,
    phase: 'starting_identity',
    cancelRequested: false,
    cancelDispatched: false,
    terminalStatus: null,
    promiseOpen: true,
    retiredInvocationIds: retired,
    liveText: '',
    liveMeta: null,
    parkedLiveText: '',
    parkedLiveMeta: null,
  })
}

export function switchDesktopLiveScope(
  state: DesktopLiveStreamState,
  workspaceId: string | null,
  conversationId: string | null,
): DesktopLiveStreamState {
  if (state.workspaceId === workspaceId && state.conversationId === conversationId) {
    return state
  }
  const leavingOrigin = viewingOrigin(state) && LIVE.has(state.phase)
  const enteringOrigin =
    workspaceId === state.originWorkspaceId &&
    conversationId === state.originConversationId &&
    LIVE.has(state.phase)
  if (leavingOrigin) {
    return finalize({
      ...state,
      workspaceId,
      conversationId,
      parkedLiveText: state.liveText,
      parkedLiveMeta: state.liveMeta,
      liveText: '',
      liveMeta: null,
    })
  }
  if (enteringOrigin) {
    return finalize({
      ...state,
      workspaceId,
      conversationId,
      liveText: state.parkedLiveText,
      liveMeta: state.parkedLiveMeta,
      parkedLiveText: '',
      parkedLiveMeta: null,
    })
  }
  return finalize({
    ...state,
    workspaceId,
    conversationId,
    liveText: '',
    liveMeta: null,
  })
}

export function requestDesktopLiveCancel(state: DesktopLiveStreamState): DesktopLiveStreamState {
  if (!LIVE.has(state.phase)) return state
  if (state.phase === 'cancelling') {
    return finalize({ ...state, cancelRequested: true })
  }
  return finalize({
    ...state,
    phase: 'cancelling',
    cancelRequested: true,
  })
}

export function completeDesktopLiveSend(
  state: DesktopLiveStreamState,
  sendGeneration: number,
): DesktopLiveStreamState {
  if (state.sendEpoch !== sendGeneration) return state
  if (state.phase === 'idle' && !state.promiseOpen) return state
  const originView = viewingOrigin(state)
  const cancelled = state.cancelRequested || state.phase === 'cancelling' || state.phase === 'cancelled'
  const retired = retireInvocationId(state.retiredInvocationIds, state.invocationId)
  return finalize({
    ...state,
    phase: 'idle',
    promiseOpen: false,
    invocationId: null,
    cancelRequested: false,
    cancelDispatched: false,
    terminalStatus: cancelled
      ? 'cancelled'
      : state.terminalStatus,
    retiredInvocationIds: retired,
    liveText: originView ? '' : state.liveText,
    liveMeta: originView ? state.liveMeta : null,
    parkedLiveText: '',
    parkedLiveMeta: null,
  })
}

function bindIdentity(
  state: DesktopLiveStreamState,
  event: DesktopInvocationStreamEvent,
): DesktopInvocationEventResult {
  if (!AWAITING_IDENTITY.has(state.phase) && !(state.phase === 'cancelling' && state.invocationId === null)) {
    return {
      state: finalize({
        ...state,
        retiredInvocationIds: retireInvocationId(state.retiredInvocationIds, event.invocationId),
      }),
      cancelInvocationId: null,
    }
  }
  if (!eventMatchesOrigin(state, event) || !eventMatchesSendEpoch(state, event)) {
    return {
      state: finalize({
        ...state,
        retiredInvocationIds: retireInvocationId(state.retiredInvocationIds, event.invocationId),
      }),
      cancelInvocationId: null,
    }
  }
  const originView = viewingOrigin(state)
  const nextPhase = state.cancelRequested ? 'cancelling' : 'running'
  const bound = finalize({
    ...state,
    invocationId: event.invocationId,
    phase: nextPhase,
    liveMeta: originView ? event : null,
    parkedLiveMeta: originView ? null : event,
    liveText: originView ? state.liveText : state.liveText,
    parkedLiveText: originView ? state.parkedLiveText : state.parkedLiveText,
  })
  if (bound.cancelRequested && !bound.cancelDispatched) {
    return {
      state: markDesktopInvocationCancelDispatched(bound),
      cancelInvocationId: event.invocationId,
    }
  }
  return { state: bound, cancelInvocationId: null }
}

function applyDelta(
  state: DesktopLiveStreamState,
  event: DesktopInvocationStreamEvent,
): DesktopLiveStreamState {
  if (state.invocationId === null || event.invocationId !== state.invocationId) {
    return state
  }
  if (TERMINAL_PHASES.has(state.phase)) return state
  if (!eventMatchesOrigin(state, event) || !event.text) return state
  if (state.phase !== 'running' && state.phase !== 'identity' && state.phase !== 'cancelling') {
    return state
  }
  const originView = viewingOrigin(state)
  if (originView) {
    return finalize({ ...state, liveText: state.liveText + event.text })
  }
  return finalize({ ...state, parkedLiveText: state.parkedLiveText + event.text })
}

function applyTerminal(
  state: DesktopLiveStreamState,
  event: DesktopInvocationStreamEvent,
): DesktopLiveStreamState {
  if (isRetired(state, event.invocationId)) {
    return state
  }
  if (state.invocationId === null || event.invocationId !== state.invocationId) {
    return finalize({
      ...state,
      retiredInvocationIds: retireInvocationId(state.retiredInvocationIds, event.invocationId),
    })
  }
  if (state.phase === 'idle') return state
  if (state.phase === 'cancelled' || state.phase === 'terminal' || state.phase === 'convergence') {
    return state
  }
  const cancelWon =
    state.cancelRequested ||
    state.cancelDispatched ||
    state.phase === 'cancelling' ||
    event.type === 'cancelled'
  const terminalStatus = cancelWon
    ? 'cancelled'
    : (event.status ?? (event.type === 'error' ? 'failed' : 'succeeded'))
  const phase: DesktopInvocationPhase = state.promiseOpen
    ? 'convergence'
    : cancelWon
      ? 'cancelled'
      : 'terminal'
  const originView = viewingOrigin(state)
  const scoped =
    event.workspaceId === state.workspaceId && event.conversationId === state.conversationId
  const displayEvent: DesktopInvocationStreamEvent = cancelWon
    ? { ...event, type: 'cancelled', status: 'cancelled' }
    : event
  return finalize({
    ...state,
    phase,
    invocationId: null,
    terminalStatus,
    cancelRequested: state.cancelRequested || cancelWon,
    retiredInvocationIds: retireInvocationId(state.retiredInvocationIds, event.invocationId),
    liveMeta: originView || scoped ? displayEvent : null,
    liveText: scoped ? state.liveText : '',
    parkedLiveText: '',
    parkedLiveMeta: null,
  })
}

export function reduceDesktopInvocationEvent(
  state: DesktopLiveStreamState,
  event: DesktopInvocationStreamEvent,
): DesktopInvocationEventResult {
  if (isRetired(state, event.invocationId)) {
    return { state, cancelInvocationId: null }
  }
  if (event.type === 'identity') {
    if (state.invocationId !== null) {
      if (event.invocationId === state.invocationId) {
        return { state, cancelInvocationId: null }
      }
      return {
        state: finalize({
          ...state,
          retiredInvocationIds: retireInvocationId(state.retiredInvocationIds, event.invocationId),
        }),
        cancelInvocationId: null,
      }
    }
    return bindIdentity(state, event)
  }
  if (event.type === 'delta') {
    return { state: applyDelta(state, event), cancelInvocationId: null }
  }
  if (event.type === 'done' || event.type === 'cancelled' || event.type === 'error') {
    return { state: applyTerminal(state, event), cancelInvocationId: null }
  }
  return { state, cancelInvocationId: null }
}

export function applyDesktopConversationEvent(
  state: DesktopLiveStreamState,
  event: DesktopInvocationStreamEvent,
): DesktopLiveStreamState {
  return reduceDesktopInvocationEvent(state, event).state
}
