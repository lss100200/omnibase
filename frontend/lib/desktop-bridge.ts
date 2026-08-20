'use client'

export interface DesktopOwner {
  readonly id: string
  readonly displayName: string
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopOwnerStatus {
  readonly initialized: boolean
  readonly owner: DesktopOwner | null
}

export interface DesktopOwnerBootstrapResult extends DesktopOwnerStatus {
  readonly initialized: true
  readonly created: boolean
  readonly owner: DesktopOwner
}

export type DesktopWorkspaceState = 'active' | 'archived'

export interface DesktopWorkspace {
  readonly id: string
  readonly ownerId: string
  readonly name: string
  readonly state: DesktopWorkspaceState
  readonly rowVersion: number
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopWorkspaceList {
  readonly items: readonly DesktopWorkspace[]
}

export interface DesktopWorkspaceMutationResult {
  readonly workspace: DesktopWorkspace
}

export type DesktopOperationResult<T> =
  | Readonly<{ ok: true; value: T }>
  | Readonly<{ ok: false; error: Readonly<{ code: string }> }>

export type DesktopProviderFamily =
  | 'deepseek'
  | 'openai'
  | 'anthropic'
  | 'glm'
  | 'kimi'
  | 'generic-openai-compatible'

export type DesktopReasoningGear = 'economy' | 'standard' | 'deep' | 'audit'
export type DesktopThinkingDepth = 'disabled' | 'low' | 'medium' | 'high'

export interface DesktopProvider {
  readonly id: string
  readonly displayName: string
  readonly baseUrl: string
  readonly modelName: string
  readonly family: DesktopProviderFamily
  readonly gear: DesktopReasoningGear
  readonly thinkingDepth: DesktopThinkingDepth
  readonly timeoutSeconds: number
  readonly allowLoopbackHttp: boolean
  readonly isDefault: boolean
  readonly isEnabled: boolean
  readonly hasSecret: true
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopParentAgent {
  readonly id: string
  readonly workspaceId: string
  readonly role: 'parent'
  readonly displayName: string
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopConversation {
  readonly id: string
  readonly workspaceId: string
  readonly title: string
  readonly state: 'active' | 'archived'
  readonly rowVersion: number
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopInvocation {
  readonly id: string
  readonly providerId: string
  readonly requestedModel: string
  readonly actualModel: string | null
  readonly family: string
  readonly gear: string
  readonly thinkingDepth: string
  readonly status: 'running' | 'succeeded' | 'failed' | 'cancelled' | 'unknown'
  readonly durationMs: number | null
  readonly inputTokens: number | null
  readonly outputTokens: number | null
  readonly totalTokens: number | null
  readonly errorCode: string | null
  readonly errorRedacted: string | null
  readonly retryOfInvocationId: string | null
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopMessage {
  readonly id: string
  readonly role: 'user' | 'assistant'
  readonly content: string
  readonly status: 'streaming' | 'completed' | 'cancelled' | 'failed' | 'unknown'
  readonly invocationId: string | null
  readonly retryOfMessageId: string | null
  readonly createdAt: string
  readonly invocation: DesktopInvocation | null
}

export interface DesktopConversationEvent {
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
}

export interface DesktopProviderTestResult {
  readonly ok: boolean
  readonly providerId: string
  readonly providerName: string
  readonly requestedModel: string
  readonly actualModel: string | null
  readonly identityProven: boolean
  readonly family: string
  readonly latencyMs?: number
  readonly errorCode?: string
  readonly errorRedacted?: string
}

export interface DesktopLiveStreamState {
  readonly workspaceId: string | null
  readonly conversationId: string | null
  readonly liveInvocation: string | null
  readonly liveText: string
  readonly liveMeta: DesktopConversationEvent | null
  readonly streaming: boolean
  readonly originWorkspaceId: string | null
  readonly originConversationId: string | null
  readonly parkedLiveText: string
  readonly parkedLiveMeta: DesktopConversationEvent | null
  readonly liveActive: boolean
  readonly cancelRequested: boolean
  readonly sendGeneration: number
}

export function createDesktopLiveStreamState(
  input: Partial<DesktopLiveStreamState> = {},
): DesktopLiveStreamState {
  const workspaceId = input.workspaceId ?? null
  const conversationId = input.conversationId ?? null
  const liveInvocation = input.liveInvocation ?? null
  const streaming = input.streaming ?? false
  return {
    workspaceId,
    conversationId,
    liveInvocation,
    liveText: input.liveText ?? '',
    liveMeta: input.liveMeta ?? null,
    streaming,
    originWorkspaceId: input.originWorkspaceId ?? workspaceId,
    originConversationId: input.originConversationId ?? conversationId,
    parkedLiveText: input.parkedLiveText ?? '',
    parkedLiveMeta: input.parkedLiveMeta ?? null,
    liveActive: input.liveActive ?? (streaming || liveInvocation !== null),
    cancelRequested: input.cancelRequested ?? false,
    sendGeneration: input.sendGeneration ?? 0,
  }
}

export function desktopLiveStopVisible(state: DesktopLiveStreamState): boolean {
  return state.liveActive
}

export function desktopLiveViewIsOrigin(state: DesktopLiveStreamState): boolean {
  return (
    state.workspaceId === state.originWorkspaceId &&
    state.conversationId === state.originConversationId
  )
}

function eventMatchesOrigin(
  state: DesktopLiveStreamState,
  event: DesktopConversationEvent,
): boolean {
  return (
    event.workspaceId === state.originWorkspaceId &&
    event.conversationId === state.originConversationId
  )
}

export function beginDesktopLiveSend(state: DesktopLiveStreamState): DesktopLiveStreamState {
  return {
    ...state,
    liveInvocation: null,
    liveMeta: null,
    liveText: '',
    streaming: true,
    liveActive: true,
    cancelRequested: false,
    originWorkspaceId: state.workspaceId,
    originConversationId: state.conversationId,
    parkedLiveText: '',
    parkedLiveMeta: null,
    sendGeneration: state.sendGeneration + 1,
  }
}

export function switchDesktopLiveScope(
  state: DesktopLiveStreamState,
  workspaceId: string | null,
  conversationId: string | null,
): DesktopLiveStreamState {
  if (state.workspaceId === workspaceId && state.conversationId === conversationId) {
    return state
  }
  const leavingOrigin = desktopLiveViewIsOrigin(state) && state.liveActive
  const enteringOrigin =
    workspaceId === state.originWorkspaceId &&
    conversationId === state.originConversationId &&
    state.liveActive
  if (leavingOrigin) {
    return {
      ...state,
      workspaceId,
      conversationId,
      parkedLiveText: state.liveText,
      parkedLiveMeta: state.liveMeta,
      liveText: '',
      liveMeta: null,
      streaming: false,
    }
  }
  if (enteringOrigin) {
    return {
      ...state,
      workspaceId,
      conversationId,
      liveText: state.parkedLiveText,
      liveMeta: state.parkedLiveMeta,
      parkedLiveText: '',
      parkedLiveMeta: null,
      streaming: true,
    }
  }
  return {
    ...state,
    workspaceId,
    conversationId,
    liveText: '',
    liveMeta: null,
    streaming: false,
  }
}

export function requestDesktopLiveCancel(state: DesktopLiveStreamState): DesktopLiveStreamState {
  if (!state.liveActive) return state
  return {
    ...state,
    liveActive: false,
    streaming: false,
    cancelRequested: true,
  }
}

export function completeDesktopLiveSend(
  state: DesktopLiveStreamState,
  sendGeneration: number,
): DesktopLiveStreamState {
  if (state.sendGeneration !== sendGeneration) return state
  if (!state.liveActive && state.liveInvocation === null) return state
  const scoped = desktopLiveViewIsOrigin(state)
  return {
    ...state,
    liveActive: false,
    streaming: false,
    liveInvocation: null,
    liveText: scoped ? '' : state.liveText,
    liveMeta: scoped ? state.liveMeta : null,
    parkedLiveText: '',
    parkedLiveMeta: null,
    cancelRequested: false,
  }
}

export function applyDesktopConversationEvent(
  state: DesktopLiveStreamState,
  event: DesktopConversationEvent,
): DesktopLiveStreamState {
  const scoped =
    event.workspaceId === state.workspaceId && event.conversationId === state.conversationId
  const originEvent = eventMatchesOrigin(state, event)
  const viewingOrigin = desktopLiveViewIsOrigin(state)
  const ours = state.liveInvocation !== null && event.invocationId === state.liveInvocation
  const pendingOurs = state.liveActive && state.liveInvocation === null && originEvent
  if (event.type === 'identity') {
    if (state.liveActive) {
      if (!originEvent) return state
    } else if (!scoped) {
      return state
    }
    return {
      ...state,
      liveInvocation: event.invocationId,
      liveMeta: viewingOrigin ? event : null,
      parkedLiveMeta: viewingOrigin ? null : event,
      liveText: viewingOrigin ? '' : state.liveText,
      parkedLiveText: viewingOrigin ? '' : state.parkedLiveText,
      streaming: viewingOrigin,
      liveActive: true,
      originWorkspaceId: state.liveActive ? state.originWorkspaceId : state.workspaceId,
      originConversationId: state.liveActive ? state.originConversationId : state.conversationId,
    }
  }
  if (event.type === 'delta') {
    if (!ours || !originEvent || !event.text) return state
    if (viewingOrigin) {
      return { ...state, liveText: state.liveText + event.text }
    }
    return { ...state, parkedLiveText: state.parkedLiveText + event.text }
  }
  if (event.type === 'done' || event.type === 'cancelled' || event.type === 'error') {
    if (!ours && !pendingOurs) return state
    return {
      ...state,
      liveInvocation: null,
      liveMeta: scoped ? event : null,
      streaming: false,
      liveText: scoped ? state.liveText : '',
      liveActive: false,
      parkedLiveText: '',
      parkedLiveMeta: null,
      cancelRequested: false,
    }
  }
  return state
}

export interface OmniBaseDesktopBridge {
  readonly app: {
    readonly getVersion: () => Promise<string>
  }
  readonly runtime: {
    readonly getStatus: () => Promise<{
      readonly phase: 'stopped' | 'starting' | 'ready' | 'failed'
      readonly attempts: number
      readonly lastError: string | null
    }>
    readonly retryStartup: () => Promise<{
      readonly phase: 'stopped' | 'starting' | 'ready' | 'failed'
      readonly attempts: number
      readonly lastError: string | null
    }>
  }
  readonly owner: {
    readonly getStatus: () => Promise<DesktopOperationResult<DesktopOwnerStatus>>
    readonly bootstrap: (input: {
      readonly displayName: string
    }) => Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>>
  }
  readonly workspaces: {
    readonly list: () => Promise<DesktopOperationResult<DesktopWorkspaceList>>
    readonly create: (input: {
      readonly name: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>
    readonly archive: (input: {
      readonly workspaceId: string
      readonly expectedRowVersion: number
    }) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>
    readonly agent: (input: {
      readonly workspaceId: string
    }) => Promise<DesktopOperationResult<{ readonly agent: DesktopParentAgent }>>
  }
  readonly providers: {
    readonly list: () => Promise<DesktopOperationResult<{ readonly items: readonly DesktopProvider[] }>>
    readonly upsert: (input: {
      readonly id?: string
      readonly displayName: string
      readonly baseUrl: string
      readonly apiKey?: string
      readonly modelName: string
      readonly gear: DesktopReasoningGear
      readonly thinkingDepth: DesktopThinkingDepth
      readonly timeoutSeconds: number
      readonly allowLoopbackHttp: boolean
      readonly isDefault: boolean
      readonly isEnabled: boolean
    }) => Promise<DesktopOperationResult<{ readonly provider: DesktopProvider }>>
    readonly delete: (input: {
      readonly providerId: string
    }) => Promise<DesktopOperationResult<{ readonly deleted: true; readonly id: string }>>
    readonly test: (input: {
      readonly providerId: string
    }) => Promise<DesktopOperationResult<DesktopProviderTestResult>>
  }
  readonly conversations: {
    readonly list: (input: {
      readonly workspaceId: string
    }) => Promise<DesktopOperationResult<{ readonly items: readonly DesktopConversation[] }>>
    readonly create: (input: {
      readonly workspaceId: string
      readonly title?: string
    }) => Promise<
      DesktopOperationResult<{ readonly created: true; readonly conversation: DesktopConversation }>
    >
    readonly archive: (input: {
      readonly workspaceId: string
      readonly conversationId: string
      readonly expectedRowVersion: number
    }) => Promise<DesktopOperationResult<{ readonly conversation: DesktopConversation }>>
    readonly get: (input: {
      readonly workspaceId: string
      readonly conversationId: string
    }) => Promise<
      DesktopOperationResult<{
        readonly conversation: DesktopConversation
        readonly messages: readonly DesktopMessage[]
      }>
    >
    readonly send: (input: {
      readonly workspaceId: string
      readonly conversationId: string
      readonly content: string
      readonly providerId?: string
      readonly retryOfMessageId?: string
    }) => Promise<DesktopOperationResult<DesktopConversationEvent>>
    readonly cancel: (input: {
      readonly invocationId: string
    }) => Promise<
      DesktopOperationResult<{
        readonly cancelled: boolean
        readonly id: string
        readonly accepted: boolean
      }>
    >
    readonly subscribe: (listener: (event: DesktopConversationEvent) => void) => () => void
  }
}

declare global {
  interface Window {
    readonly omnibaseDesktop?: OmniBaseDesktopBridge
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function hasFunction(value: Record<string, unknown>, name: string): boolean {
  return typeof value[name] === 'function'
}

export function resolveDesktopBridge(value: unknown): OmniBaseDesktopBridge | null {
  if (
    !isRecord(value) ||
    !isRecord(value.app) ||
    !hasFunction(value.app, 'getVersion') ||
    !isRecord(value.runtime) ||
    !hasFunction(value.runtime, 'getStatus') ||
    !hasFunction(value.runtime, 'retryStartup') ||
    !isRecord(value.owner) ||
    !hasFunction(value.owner, 'getStatus') ||
    !hasFunction(value.owner, 'bootstrap') ||
    !isRecord(value.workspaces) ||
    !hasFunction(value.workspaces, 'list') ||
    !hasFunction(value.workspaces, 'create') ||
    !hasFunction(value.workspaces, 'archive') ||
    !hasFunction(value.workspaces, 'agent') ||
    !isRecord(value.providers) ||
    !hasFunction(value.providers, 'list') ||
    !hasFunction(value.providers, 'upsert') ||
    !hasFunction(value.providers, 'delete') ||
    !hasFunction(value.providers, 'test') ||
    !isRecord(value.conversations) ||
    !hasFunction(value.conversations, 'list') ||
    !hasFunction(value.conversations, 'create') ||
    !hasFunction(value.conversations, 'archive') ||
    !hasFunction(value.conversations, 'get') ||
    !hasFunction(value.conversations, 'send') ||
    !hasFunction(value.conversations, 'cancel') ||
    !hasFunction(value.conversations, 'subscribe')
  ) {
    return null
  }
  return value as unknown as OmniBaseDesktopBridge
}

export function getDesktopBridge(): OmniBaseDesktopBridge | null {
  return typeof window === 'undefined' ? null : resolveDesktopBridge(window.omnibaseDesktop)
}
