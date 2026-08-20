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
  readonly sendEpoch?: number
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

export interface DesktopAgentRole {
  readonly id: string
  readonly displayName: string
  readonly responsibility: string
  readonly defaultState: 'active' | 'dormant'
  readonly mayJoinTeam: boolean
  readonly providerId: string | null
  readonly modelNameOverride: string | null
  readonly gear: string
  readonly thinkingDepth: string
  readonly rowVersion: number
  readonly verificationState: 'unverified' | 'binding_recorded' | 'stale'
  readonly verifiedActualModel: string | null
  readonly inheritedProvider: boolean
  readonly resolvedProviderId: string | null
  readonly resolvedModelName: string | null
  readonly secretFingerprint: string | null
  readonly hasSecret: boolean
}

export interface DesktopAgentRoleTestResult {
  readonly ok: true
  readonly roleId: string
  readonly workspaceId: string
  readonly providerId: string
  readonly inheritedProvider: boolean
  readonly requestedModel: string
  readonly secretFingerprint: string
  readonly verificationDigest: string
  readonly identityProven: false
}

export interface DesktopTeamRunBudget {
  readonly maximumProviderCalls: number
  readonly maximumWallTimeMs: number
  readonly maximumConcurrentCalls: number
  readonly maximumInputCharacters: number
  readonly maximumOutputCharacters: number
}

export interface DesktopTeamRun {
  readonly id: string
  readonly workspaceId: string
  readonly conversationId: string
  readonly mode: 'single' | 'team'
  readonly state: string
  readonly staffingAuthority: 'parent_proposal'
  readonly currentPlanRevisionId: string | null
  readonly currentWaveId: string | null
  readonly dispatchedParticipantCount: number | null
  readonly maximumProviderCalls: number
  readonly maximumWallTimeMs: number
  readonly maximumConcurrentCalls: number
  readonly maximumInputCharacters: number
  readonly maximumOutputCharacters: number
  readonly consumedProviderCalls: number
  readonly task: string
  readonly allowedSpecialistRoleIds: readonly string[]
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopTeamRunProposalResult {
  readonly accepted: boolean
  readonly validationErrorCode: string | null
  readonly teamRun: DesktopTeamRun
  readonly planRevision: {
    readonly id: string
    readonly revisionOrdinal: number
    readonly decision: string
    readonly proposalJsonSha256: string
    readonly validated: boolean
    readonly validationErrorCode: string | null
    readonly createdAt: string
  }
}

export interface DesktopTeamCollaborationRequest {
  readonly id?: string
  readonly fromAssignmentId: string
  readonly fromEmployeeRoleId: string
  readonly targetRoleId: string
  readonly question: string
  readonly reason: string
  readonly parentDecision: string
  readonly resolvedAssignmentId: string | null
}

export interface PersonalTeamBlackboard {
  readonly teamRunId: string
  readonly workspaceId: string
  readonly ownerObjective: string
  readonly currentPlanRevisionId: string | null
  readonly assignments: readonly Record<string, unknown>[]
  readonly reports: readonly Record<string, unknown>[]
  readonly collaborationRequests: readonly DesktopTeamCollaborationRequest[]
}

export interface DesktopTeamRunEvent {
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

export type {
  DesktopInvocationEventResult,
  DesktopInvocationLiveProjection,
  DesktopInvocationPhase,
  DesktopLiveStreamState,
} from './desktop-invocation-lifecycle'
export {
  applyDesktopConversationEvent,
  beginDesktopLiveSend,
  completeDesktopLiveSend,
  createDesktopLiveStreamState,
  desktopInvocationCanSend,
  desktopInvocationCancelTarget,
  desktopInvocationIsStopping,
  desktopInvocationLiveProjection,
  desktopInvocationNeedsStreamAbort,
  desktopInvocationStopVisible,
  desktopLiveSendBlocked,
  desktopLiveStopVisible,
  desktopLiveViewIsOrigin,
  markDesktopInvocationCancelDispatched,
  reduceDesktopInvocationEvent,
  requestDesktopLiveCancel,
  switchDesktopLiveScope,
} from './desktop-invocation-lifecycle'
export {
  beginDesktopTeamRun,
  completeDesktopTeamRun,
  createDesktopTeamLiveState,
  desktopTeamLiveProjection,
  desktopTeamStopVisible,
  failDesktopTeamPreStart,
  reduceDesktopTeamEvent,
  requestDesktopTeamCancel,
  switchDesktopTeamScope,
} from './desktop-team-lifecycle'
export {
  TEAM_ROLE_LABELS,
  desktopTeamTranscriptHighlight,
  projectDesktopTeamBudget,
  projectDesktopTeamEmployees,
  projectDesktopTeamTimeline,
} from './desktop-team-surface'

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
      readonly sendEpoch?: number
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
    readonly abortInFlightSend: () => Promise<
      DesktopOperationResult<{
        readonly aborted: boolean
      }>
    >
    readonly subscribe: (listener: (event: DesktopConversationEvent) => void) => () => void
  }
  readonly agents: {
    readonly roles: {
      readonly list: (input: {
        readonly workspaceId: string
      }) => Promise<DesktopOperationResult<{ readonly items: readonly DesktopAgentRole[] }>>
      readonly get: (input: {
        readonly workspaceId: string
        readonly roleId: string
      }) => Promise<DesktopOperationResult<{ readonly role: DesktopAgentRole }>>
      readonly update: (input: {
        readonly workspaceId: string
        readonly roleId: string
        readonly providerId: string | null
        readonly modelNameOverride: string | null
        readonly gear: DesktopReasoningGear
        readonly thinkingDepth: DesktopThinkingDepth
        readonly expectedRowVersion: number
      }) => Promise<DesktopOperationResult<{ readonly role: DesktopAgentRole }>>
      readonly test: (input: {
        readonly workspaceId: string
        readonly roleId: string
      }) => Promise<DesktopOperationResult<DesktopAgentRoleTestResult>>
    }
  }
  readonly teamRuns: {
    readonly start: (input: {
      readonly workspaceId: string
      readonly conversationId: string
      readonly task: string
      readonly teamMode: true
      readonly budget: DesktopTeamRunBudget
      readonly allowedSpecialistRoleIds?: readonly string[]
    }) => Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>>
    readonly cancel: (input: {
      readonly workspaceId: string
      readonly teamRunId: string
    }) => Promise<
      DesktopOperationResult<{
        readonly cancelled: boolean
        readonly accepted: boolean
        readonly teamRun: DesktopTeamRun
      }>
    >
    readonly get: (input: {
      readonly workspaceId: string
      readonly teamRunId: string
    }) => Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>>
    readonly list: (input: {
      readonly workspaceId: string
    }) => Promise<DesktopOperationResult<{ readonly items: readonly DesktopTeamRun[] }>>
    readonly submitProposal: (input: {
      readonly workspaceId: string
      readonly teamRunId: string
      readonly proposal: Record<string, unknown>
    }) => Promise<DesktopOperationResult<DesktopTeamRunProposalResult>>
    readonly getBlackboard: (input: {
      readonly workspaceId: string
      readonly teamRunId: string
    }) => Promise<DesktopOperationResult<{ readonly blackboard: PersonalTeamBlackboard }>>
    readonly recordCollaboration: (input: {
      readonly workspaceId: string
      readonly teamRunId: string
      readonly fromAssignmentId: string
      readonly fromEmployeeRoleId: string
      readonly targetRoleId: string
      readonly question: string
      readonly reason: string
    }) => Promise<DesktopOperationResult<{ readonly collaborationRequest: DesktopTeamCollaborationRequest }>>
    readonly execute: (input: {
      readonly workspaceId: string
      readonly conversationId: string
      readonly task: string
      readonly teamMode: true
      readonly rosterEpoch: number
      readonly budget: DesktopTeamRunBudget
      readonly allowedSpecialistRoleIds?: readonly string[]
    }) => Promise<DesktopOperationResult<{ readonly proof: {
      readonly teamRunId: string
      readonly state: string
      readonly providerCallCount: number
      readonly executedNodeCount: number
      readonly parentCallCount: number
      readonly uniqueInvocationIds: readonly string[]
      readonly uniqueNodeIds: readonly string[]
      readonly uniqueAssignmentIds: readonly string[]
      readonly parentWasLastWhenSynthesizing: boolean
      readonly hiddenCalls: false
      readonly parentFinalAnswer: string | null
    } }>>
    readonly appendBudget: (input: {
      readonly workspaceId: string
      readonly teamRunId: string
      readonly budget: DesktopTeamRunBudget
    }) => Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>>
    readonly subscribe: (listener: (event: DesktopTeamRunEvent) => void) => () => void
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
    !hasFunction(value.conversations, 'abortInFlightSend') ||
    !hasFunction(value.conversations, 'subscribe') ||
    !isRecord(value.agents) ||
    !isRecord(value.agents.roles) ||
    !hasFunction(value.agents.roles, 'list') ||
    !hasFunction(value.agents.roles, 'get') ||
    !hasFunction(value.agents.roles, 'update') ||
    !hasFunction(value.agents.roles, 'test') ||
    !isRecord(value.teamRuns) ||
    !hasFunction(value.teamRuns, 'start') ||
    !hasFunction(value.teamRuns, 'cancel') ||
    !hasFunction(value.teamRuns, 'get') ||
    !hasFunction(value.teamRuns, 'list') ||
    !hasFunction(value.teamRuns, 'submitProposal') ||
    !hasFunction(value.teamRuns, 'getBlackboard') ||
    !hasFunction(value.teamRuns, 'recordCollaboration') ||
    !hasFunction(value.teamRuns, 'execute') ||
    !hasFunction(value.teamRuns, 'appendBudget') ||
    !hasFunction(value.teamRuns, 'subscribe')
  ) {
    return null
  }
  return value as unknown as OmniBaseDesktopBridge
}

export function getDesktopBridge(): OmniBaseDesktopBridge | null {
  return typeof window === 'undefined' ? null : resolveDesktopBridge(window.omnibaseDesktop)
}
