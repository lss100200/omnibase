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
    }) => Promise<
      DesktopOperationResult<{
        readonly ok: boolean
        readonly providerId: string
        readonly providerName: string
        readonly requestedModel: string
        readonly actualModel: string | null
        readonly family: string
        readonly latencyMs?: number
        readonly errorCode?: string
        readonly errorRedacted?: string
      }>
    >
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
