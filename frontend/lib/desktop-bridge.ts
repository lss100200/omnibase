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

export type DesktopWorkbenchDensity = 'compact' | 'comfortable'
export type DesktopWorkspaceDensity = 'inherit' | DesktopWorkbenchDensity
export type DesktopWorkspaceSidebar = 'explorer' | 'run' | 'blackboard' | 'hidden'
export type DesktopWorkspaceBottomPanel = 'hidden' | 'output' | 'agent-log'
export type DesktopCompositionSourceKind = 'system' | 'owner' | 'assistant' | 'rollback'
export type DesktopCompositionDecision = 'approved' | 'rejected'
export type DesktopWorkspaceSlotPosture = 'required' | 'admitted' | 'unavailable'
export type DesktopWorkspaceSlotId =
  | 'agent.rail'
  | 'conversation.transcript'
  | 'event.agent-log'
  | 'event.output'
  | 'knowledge.ebook'
  | 'mcp.catalog'
  | 'provider.settings'
  | 'run.history'
  | 'sandbox.runtime'
  | 'settings.center'
  | 'skills.catalog'
  | 'source-control'
  | 'terminal'
  | 'workspace.brief'
  | 'workspace.explorer'

export interface DesktopApplicationPreference {
  readonly density: DesktopWorkbenchDensity
  readonly reduceMotion: boolean
  readonly rowVersion: number
  readonly updatedAt: string
}

export interface DesktopWorkspaceCompositionProfileValue {
  readonly schemaVersion: 1
  readonly template: Readonly<{ id: 'standard-workbench'; version: 1 }>
  readonly appearance: Readonly<{
    density: DesktopWorkspaceDensity
    quietChrome: boolean
  }>
  readonly layout: Readonly<{
    agentPanel: 'open' | 'closed'
    bottomPanel: DesktopWorkspaceBottomPanel
    focusMode: boolean
    sidebar: DesktopWorkspaceSidebar
  }>
  readonly slots: Readonly<Record<DesktopWorkspaceSlotId, boolean>>
}

export interface DesktopWorkspaceCompositionRevision {
  readonly workspaceId: string
  readonly revision: number
  readonly profileSha256: string
  readonly sourceKind: DesktopCompositionSourceKind
  readonly proposalId: string | null
  readonly value: DesktopWorkspaceCompositionProfileValue
  readonly createdAt: string
}

export interface DesktopWorkspaceCompositionProposal {
  readonly id: string
  readonly workspaceId: string
  readonly baseRevision: number
  readonly baseProfileSha256: string
  readonly sourceKind: Exclude<DesktopCompositionSourceKind, 'system'>
  readonly sourceReference: string | null
  readonly desiredProfileSha256: string
  readonly requestSha256: string
  readonly desiredProfile: DesktopWorkspaceCompositionProfileValue
  readonly decision: DesktopCompositionDecision | null
  readonly appliedRevision: number | null
  readonly createdAt: string
  readonly decidedAt: string | null
}

export interface DesktopWorkspaceSlotCatalogItem {
  readonly id: DesktopWorkspaceSlotId
  readonly label: string
  readonly region: 'sidebar' | 'editor' | 'right' | 'settings' | 'bottom'
  readonly posture: DesktopWorkspaceSlotPosture
}

export type DesktopWorkspaceCompositionAuditEvent =
  | Readonly<{
      sequence: number
      eventType: 'workspace_composition_proposed'
      payload: Readonly<{
        baseRevision: number
        desiredProfileSha256: string
        proposalId: string
        requestSha256: string
        sourceKind: Exclude<DesktopCompositionSourceKind, 'system'>
      }>
      createdAt: string
    }>
  | Readonly<{
      sequence: number
      eventType: 'workspace_composition_rejected'
      payload: Readonly<{
        proposalId: string
        requestSha256: string
      }>
      createdAt: string
    }>
  | Readonly<{
      sequence: number
      eventType: 'workspace_composition_applied'
      payload: Readonly<{
        profileSha256: string
        proposalId: string
        requestSha256: string
        revision: number
        sourceKind: Exclude<DesktopCompositionSourceKind, 'system'>
      }>
      createdAt: string
    }>

export interface DesktopWorkspaceCompositionSnapshot {
  readonly profile: DesktopWorkspaceCompositionRevision
  readonly revisions: readonly DesktopWorkspaceCompositionRevision[]
  readonly proposals: readonly DesktopWorkspaceCompositionProposal[]
  readonly slotCatalog: readonly DesktopWorkspaceSlotCatalogItem[]
  readonly audit: readonly DesktopWorkspaceCompositionAuditEvent[]
}

export interface DesktopWorkspaceCompositionProposalResult {
  readonly proposal: DesktopWorkspaceCompositionProposal
  readonly replayed: boolean
}

export type DesktopWorkspaceCompositionDecisionResult =
  | Readonly<{
      workspaceId: string
      proposalId: string
      requestSha256: string
      decision: 'rejected'
      appliedRevision: null
    }>
  | Readonly<{
      workspaceId: string
      proposalId: string
      requestSha256: string
      decision: 'approved'
      appliedRevision: number
      profile: DesktopWorkspaceCompositionRevision
    }>

export type DesktopWorkspaceComponentFamily =
  | 'declarative_ui'
  | 'instruction_skill'
  | 'mcp_connector'
  | 'sandbox_workload'
  | 'trusted_local_adapter'
export type DesktopWorkspaceComponentLifecycleAction =
  | 'install'
  | 'bind'
  | 'activate'
  | 'disable'
  | 'upgrade'
  | 'rollback'
  | 'revoke'
  | 'uninstall'
export type DesktopWorkspaceComponentOperation =
  | 'ui.render'
  | 'skill.resolve'
  | 'mcp.call'
  | 'sandbox.run'
  | 'local_adapter.open'

export interface DesktopWorkspaceComponentGrantRequest {
  readonly action: string
  readonly logicalResourceId: string | null
  readonly resourceVersion: number | null
  readonly logicalServiceId: string | null
  readonly expiresInSeconds: number
  readonly maximumInvocations: number
  readonly maximumBytesIn: number
  readonly maximumBytesOut: number
  readonly maximumTokens: number
  readonly maximumWallTimeMs: number
  readonly maximumCostUnits: number
}

export interface DesktopWorkspaceComponentSlotDescriptor {
  readonly slotId: string
  readonly cardinality: 'one' | 'many'
  readonly minimumOrder: number
  readonly maximumOrder: number
}

export interface DesktopWorkspaceComponentDependencyDescriptor {
  readonly componentId: string
  readonly version: string
  readonly policyManifestSha256: string
  readonly manifestSha256: string
  readonly packageSha256: string
}

export interface DesktopWorkspaceComponentSettingsProperty {
  readonly type: 'boolean' | 'integer' | 'number' | 'string'
  readonly default?: DesktopWorkspaceComponentJsonValue
  readonly enum?: readonly DesktopWorkspaceComponentJsonValue[]
  readonly minimum?: number
  readonly maximum?: number
  readonly maxLength?: number
}

export interface DesktopWorkspaceComponentSettingsSchema {
  readonly kind: 'closed_object'
  readonly version: number
  readonly additionalProperties: false
  readonly properties: Readonly<Record<string, DesktopWorkspaceComponentSettingsProperty>>
  readonly required: readonly string[]
}

export interface DesktopWorkspaceComponentCatalogItem {
  readonly componentId: string
  readonly version: string
  readonly family: DesktopWorkspaceComponentFamily
  readonly displayName: string
  readonly publisherClass: 'source_owned' | 'owner_reviewed'
  readonly adapterId:
    | 'builtin-ui.v1'
    | 'instruction-skill.v1'
    | 'readonly-mcp.v1'
    | 'p34-sandbox.v1'
    | 'trusted-local-app.v1'
  readonly policyManifestSha256: string
  readonly manifestSha256: string | null
  readonly packageSha256: string | null
  readonly operations: readonly DesktopWorkspaceComponentOperation[]
  readonly slots: readonly DesktopWorkspaceComponentSlotDescriptor[]
  readonly dependencies: readonly DesktopWorkspaceComponentDependencyDescriptor[]
  readonly conflicts: readonly string[]
  readonly budgets: Readonly<{
    maxCalls: number
    maxBytesIn: number
    maxBytesOut: number
    maxTokens: number
    maxWallTimeMs: number
    maxCostUnits: number
    maxRetries: number
    maxConcurrency: number
  }>
  readonly network: Readonly<{ required: boolean; serviceClasses: readonly string[] }>
  readonly recovery: Readonly<{
    autoReplayUnknown: false
    retention: 'retain_workspace_data' | 'delete_component_data'
    safeMode: 'disable_component'
  }>
  readonly stateSchema: Readonly<{ kind: 'canonical_json'; version: number }>
  readonly settingsSchema: DesktopWorkspaceComponentSettingsSchema
  readonly available: boolean
  readonly unavailableReason: 'package_not_attested' | null
}

export interface DesktopWorkspaceComponentInstallation {
  readonly installationId: string
  readonly workspaceId: string
  readonly componentId: string
  readonly version: string
  readonly manifestSha256: string
  readonly packageSha256: string
  readonly state:
    | 'installed'
    | 'bound'
    | 'active'
    | 'disabled'
    | 'blocked'
    | 'revoked'
    | 'uninstalled'
  readonly revision: number
  readonly bindingGeneration: number
  readonly desiredConfiguration: DesktopWorkspaceComponentJsonValue
  readonly currentSlotBindings: readonly DesktopWorkspaceComponentSlotBindingRequest[]
  readonly dependencyGraph: readonly DesktopWorkspaceComponentDependencyRequest[]
  readonly health: 'unknown' | 'healthy' | 'degraded' | 'unavailable'
  readonly lastErrorCode: string | null
  readonly updatedAt: string
}

export interface DesktopWorkspaceComponentProposal {
  readonly proposalId: string
  readonly workspaceId: string
  readonly componentId: string
  readonly targetVersion: string
  readonly changeKind: DesktopWorkspaceComponentLifecycleAction
  readonly baseRevision: number
  readonly manifestSha256: string
  readonly packageSha256: string
  readonly requestSha256: string
  readonly requestedGrants: readonly DesktopWorkspaceComponentGrantRequest[]
  readonly desiredConfiguration: DesktopWorkspaceComponentJsonValue
  readonly desiredSlotBindings: readonly DesktopWorkspaceComponentSlotBindingRequest[]
  readonly dependencyGraph: readonly DesktopWorkspaceComponentDependencyRequest[]
  readonly sourceKind: 'owner' | 'assistant'
  readonly sourceReference: string | null
  readonly decision: 'approved' | 'rejected' | null
  readonly createdAt: string
}

export interface DesktopWorkspaceComponentSlotBindingRequest {
  readonly slotId: string
  readonly bindingKey: string
  readonly orderIndex: number
  readonly configuration: DesktopWorkspaceComponentJsonValue
}

export interface DesktopWorkspaceComponentDependencyRequest {
  readonly componentId: string
  readonly version: string
  readonly policyManifestSha256: string
  readonly manifestSha256: string
  readonly packageSha256: string
}

export interface DesktopWorkspaceComponentOperationRecord {
  readonly operationId: string
  readonly workspaceId: string
  readonly componentId: string
  readonly installationId: string | null
  readonly action: string
  readonly requestSha256: string
  readonly bindingGeneration: number
  readonly state: 'pending' | 'succeeded' | 'failed' | 'cancelled' | 'unknown'
  readonly resultSha256: string | null
  readonly evidenceSha256: string | null
  readonly errorCode: string | null
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopWorkspaceComponentEffect {
  readonly effectId: string
  readonly operationId: string
  readonly workspaceId: string
  readonly componentId: string
  readonly state: 'none' | 'pending' | 'succeeded' | 'failed' | 'unknown'
  readonly evidenceSha256: string | null
  readonly createdAt: string
  readonly updatedAt: string
}

export interface DesktopWorkspaceComponentReconciliation {
  readonly reconciliationId: string
  readonly operationId: string
  readonly effectId: string
  readonly workspaceId: string
  readonly outcome: 'succeeded' | 'failed'
  readonly evidenceSha256: string
  readonly createdAt: string
}

export interface DesktopWorkspaceComponentBudgetProjection {
  readonly calls: number
  readonly bytesIn: number
  readonly bytesOut: number
  readonly tokens: number
  readonly wallTimeMs: number
  readonly costUnits: number
  readonly retries: number
  readonly concurrency?: number
}

export interface DesktopWorkspaceComponentGrant {
  readonly grantId: string
  readonly workspaceId: string
  readonly installationId: string
  readonly bindingGeneration: number
  readonly runtimeInstanceId: string
  readonly componentId: string
  readonly version: string
  readonly actions: readonly DesktopWorkspaceComponentOperation[]
  readonly scope: readonly DesktopWorkspaceComponentGrantRequest[]
  readonly requiresNetwork: boolean
  readonly state: 'active' | 'revoked' | 'expired'
  readonly notBefore: string
  readonly expiresAt: string
  readonly limits: DesktopWorkspaceComponentBudgetProjection
  readonly used: DesktopWorkspaceComponentBudgetProjection
  readonly remaining: DesktopWorkspaceComponentBudgetProjection
}

export interface DesktopWorkspaceComponentRevocation {
  readonly revocationId: string
  readonly workspaceId: string
  readonly installationId: string
  readonly componentId: string
  readonly bindingGeneration: number
  readonly runtimeInstanceId: string | null
  readonly grantId: string | null
  readonly reasonCode: string
  readonly actorType: 'owner' | 'system'
  readonly createdAt: string
}

export interface DesktopWorkspaceComponentRecovery {
  readonly recoveryId: string
  readonly workspaceId: string
  readonly componentId: string
  readonly installationId: string
  readonly bindingGeneration: number
  readonly previousRuntimeInstanceId: string
  readonly operationId: string
  readonly effectId: string
  readonly adapterId: DesktopWorkspaceComponentCatalogItem['adapterId']
  readonly runtimeInstanceId: string
  readonly workloadIdentityDigest: string
  readonly requestSha256: string
  readonly manifestSha256: string
  readonly packageSha256: string
  readonly state: 'pending' | 'succeeded' | 'failed' | 'unknown'
  readonly reasonCode: string
  readonly createdAt: string
}

export interface DesktopWorkspaceComponentSnapshot {
  readonly workspaceId: string
  readonly catalog: readonly DesktopWorkspaceComponentCatalogItem[]
  readonly installations: readonly DesktopWorkspaceComponentInstallation[]
  readonly proposals: readonly DesktopWorkspaceComponentProposal[]
  readonly operations: readonly DesktopWorkspaceComponentOperationRecord[]
  readonly effects: readonly DesktopWorkspaceComponentEffect[]
  readonly grants: readonly DesktopWorkspaceComponentGrant[]
  readonly revocations: readonly DesktopWorkspaceComponentRevocation[]
  readonly recoveries: readonly DesktopWorkspaceComponentRecovery[]
  readonly reconciliations: readonly DesktopWorkspaceComponentReconciliation[]
  readonly audit: readonly DesktopWorkspaceComponentAuditEvent[]
}

export interface DesktopWorkspaceComponentAuditEvent {
  readonly sequence: number
  readonly eventId: string
  readonly eventType:
    | 'workspace_component_proposed'
    | 'workspace_component_decided'
    | 'workspace_component_state_changed'
    | 'workspace_component_invocation_begun'
    | 'workspace_component_invocation_settled'
    | 'workspace_component_reconciled'
    | 'workspace_component_emergency_stopped'
    | 'workspace_component_recovery_blocked'
  readonly payload: DesktopWorkspaceComponentJsonValue
  readonly createdAt: string
}

export interface DesktopWorkspaceComponentProposeInput {
  readonly workspaceId: string
  readonly componentId: string
  readonly targetVersion: string
  readonly changeKind: DesktopWorkspaceComponentLifecycleAction
  readonly expectedRevision: number
  readonly requestedGrants: readonly DesktopWorkspaceComponentGrantRequest[]
  readonly desiredConfiguration: DesktopWorkspaceComponentJsonValue
  readonly desiredSlotBindings: readonly DesktopWorkspaceComponentSlotBindingRequest[]
  readonly dependencyGraph: readonly DesktopWorkspaceComponentDependencyRequest[]
  readonly idempotencyKey: string
}

export interface DesktopWorkspaceComponentAssistantProposalInput {
  readonly workspaceId: string
  readonly messageId: string
  readonly idempotencyKey: string
}

export interface DesktopWorkspaceComponentAssistantPackageImportInput {
  readonly workspaceId: string
  readonly conversationId: string
  readonly messageId: string
  readonly packageJson: string
  readonly manifestSha256: string
  readonly packageSha256: string
}

export interface DesktopWorkspaceComponentOwnerPackageRegistration {
  readonly componentId: string
  readonly version: string
  readonly manifestSha256: string
  readonly packageSha256: string
  readonly publisherClass: 'owner_reviewed'
  readonly registeredAt: string
  readonly replayed: boolean
}

export interface DesktopWorkspaceComponentOwnerPackageImportResult {
  readonly cancelled: boolean
  readonly registration: DesktopWorkspaceComponentOwnerPackageRegistration | null
}

export interface DesktopWorkspaceComponentProposalResult {
  readonly proposal: DesktopWorkspaceComponentProposal
  readonly replayed: boolean
}

export interface DesktopWorkspaceComponentDecisionResult {
  readonly workspaceId: string
  readonly proposalId: string
  readonly requestSha256: string
  readonly decision: 'approved' | 'rejected'
  readonly installationRevision: number
}

export interface DesktopWorkspaceComponentActionResult {
  readonly operation: DesktopWorkspaceComponentOperationRecord
  readonly installation: DesktopWorkspaceComponentInstallation | null
  readonly replayed: boolean
}

interface DesktopWorkspaceComponentInvokeBase {
  readonly workspaceId: string
  readonly componentId: string
  readonly expectedRevision: number
  readonly bindingGeneration: number
  readonly manifestSha256: string
  readonly packageSha256: string
  readonly idempotencyKey: string
  readonly logicalResourceId?: string
  readonly resourceVersion?: number
  readonly logicalServiceId?: string
  readonly bytesOutReserved: number
  readonly tokensReserved: number
  readonly wallTimeMs: number
  readonly costUnits: number
}

export type DesktopWorkspaceComponentInvokeInput =
  | (DesktopWorkspaceComponentInvokeBase &
      Readonly<{
        operation: 'ui.render'
        arguments: Readonly<{ slotId: string; viewId: string }>
      }>)
  | (DesktopWorkspaceComponentInvokeBase &
      Readonly<{
        operation: 'skill.resolve'
        arguments: Readonly<{ skillId: string; task: string }>
      }>)
  | (DesktopWorkspaceComponentInvokeBase &
      Readonly<{
        operation: 'mcp.call'
        arguments: Readonly<{
          toolName:
            | 'omnibase_files_list'
            | 'omnibase_files_read'
            | 'omnibase_files_hash'
            | 'omnibase_text_search'
          path?: string
          query?: string
        }>
      }>)
  | (DesktopWorkspaceComponentInvokeBase &
      Readonly<{
        operation: 'sandbox.run'
        arguments: Readonly<{ workloadId: string; inputArtifactIds: readonly string[] }>
      }>)
  | (DesktopWorkspaceComponentInvokeBase &
      Readonly<{
        operation: 'local_adapter.open'
        arguments: Readonly<{
          adapterId: 'knowledge.ebook'
          destination: 'workspace' | 'phase' | 'document'
          logicalId?: string
        }>
      }>)

export interface DesktopWorkspaceComponentSettleResult {
  readonly operation: DesktopWorkspaceComponentOperationRecord
  readonly effect: DesktopWorkspaceComponentEffect
  readonly replayed: boolean
}

export type DesktopWorkspaceComponentJsonValue =
  | null
  | boolean
  | number
  | string
  | readonly DesktopWorkspaceComponentJsonValue[]
  | Readonly<{ readonly [key: string]: DesktopWorkspaceComponentJsonValue }>

export interface DesktopWorkspaceComponentInvokeResult {
  readonly operationId: string
  readonly state: 'succeeded' | 'failed' | 'cancelled' | 'unknown'
  readonly output: DesktopWorkspaceComponentJsonValue
  readonly settlement: DesktopWorkspaceComponentSettleResult
}

export interface DesktopWorkspaceComponentEmergencyStopResult {
  readonly workspaceId: string
  readonly operationIds: readonly string[]
  readonly stoppedComponentIds: readonly string[]
  readonly replayed: boolean
}

export interface DesktopWorkspaceComponentReconcileResult {
  readonly operation: DesktopWorkspaceComponentOperationRecord
  readonly effect: DesktopWorkspaceComponentEffect
  readonly reconciliationId: string
  readonly replayed: boolean
}

export interface DesktopWorkspaceFileAuthorization {
  readonly workspaceId: string
  readonly rootName: string
  readonly authorizationGeneration: number
}

export interface DesktopWorkspaceFileEntry {
  readonly path: string
  readonly name: string
  readonly kind: 'file' | 'directory'
  readonly sizeBytes: number | null
  readonly lastModifiedMs: number
}

export interface DesktopWorkspaceFileList {
  readonly directoryPath: string
  readonly entries: readonly DesktopWorkspaceFileEntry[]
  readonly truncated: boolean
}

export interface DesktopWorkspaceFileRead {
  readonly path: string
  readonly content: string
  readonly sizeBytes: number
  readonly lastModifiedMs: number
  readonly sha256: string
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
  desktopTeamAppendBudgetTarget,
  desktopTeamLiveProjection,
  desktopTeamStopVisible,
  failDesktopTeamPreStart,
  pendingDurableTeamCancel,
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
  readonly workbenchSettings: {
    readonly get: () => Promise<
      DesktopOperationResult<{ readonly preference: DesktopApplicationPreference }>
    >
    readonly update: (input: {
      readonly density: DesktopWorkbenchDensity
      readonly reduceMotion: boolean
      readonly expectedRowVersion: number
    }) => Promise<DesktopOperationResult<{ readonly preference: DesktopApplicationPreference }>>
  }
  readonly workspaceComposition: {
    readonly get: (input: {
      readonly workspaceId: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceCompositionSnapshot>>
    readonly propose: (input: {
      readonly workspaceId: string
      readonly expectedRevision: number
      readonly expectedProfileSha256: string
      readonly desiredProfile: DesktopWorkspaceCompositionProfileValue
    }) => Promise<DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>>
    readonly proposeFromAssistant: (input: {
      readonly workspaceId: string
      readonly expectedRevision: number
      readonly expectedProfileSha256: string
      readonly messageId: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>>
    readonly proposeRollback: (input: {
      readonly workspaceId: string
      readonly expectedRevision: number
      readonly expectedProfileSha256: string
      readonly targetRevision: number
    }) => Promise<DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>>
    readonly decide: (input: {
      readonly workspaceId: string
      readonly proposalId: string
      readonly requestSha256: string
      readonly decision: 'approve' | 'reject'
    }) => Promise<DesktopOperationResult<DesktopWorkspaceCompositionDecisionResult>>
  }
  readonly workspaceComponents: {
    readonly get: (input: {
      readonly workspaceId: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceComponentSnapshot>>
    readonly propose: (
      input: DesktopWorkspaceComponentProposeInput,
    ) => Promise<DesktopOperationResult<DesktopWorkspaceComponentProposalResult>>
    readonly proposeFromAssistant: (
      input: DesktopWorkspaceComponentAssistantProposalInput,
    ) => Promise<DesktopOperationResult<DesktopWorkspaceComponentProposalResult>>
    readonly importOwnerPackage: (input: {
      readonly workspaceId: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceComponentOwnerPackageImportResult>>
    readonly importAssistantPackage: (
      input: DesktopWorkspaceComponentAssistantPackageImportInput,
    ) => Promise<DesktopOperationResult<DesktopWorkspaceComponentOwnerPackageImportResult>>
    readonly decide: (input: {
      readonly workspaceId: string
      readonly proposalId: string
      readonly decision: 'approve' | 'reject'
      readonly requestSha256: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceComponentDecisionResult>>
    readonly action: (input: {
      readonly workspaceId: string
      readonly componentId: string
      readonly action: DesktopWorkspaceComponentLifecycleAction
      readonly proposalId: string
      readonly requestSha256: string
      readonly expectedRevision: number
      readonly manifestSha256: string
      readonly packageSha256: string
      readonly idempotencyKey: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceComponentActionResult>>
    readonly invoke: (
      input: DesktopWorkspaceComponentInvokeInput,
    ) => Promise<DesktopOperationResult<DesktopWorkspaceComponentInvokeResult>>
    readonly emergencyStop: (input: {
      readonly workspaceId: string
      readonly idempotencyKey: string
      readonly reasonCode: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceComponentEmergencyStopResult>>
    readonly reconcile: (input: {
      readonly workspaceId: string
      readonly operationId: string
      readonly effectId: string
      readonly requestSha256: string
      readonly outcome: 'succeeded' | 'failed'
      readonly evidenceSha256: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceComponentReconcileResult>>
  }
  readonly workspaceFiles: {
    readonly authorize: (input: {
      readonly workspaceId: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceFileAuthorization>>
    readonly release: (input: {
      readonly workspaceId: string
      readonly authorizationGeneration: number
    }) => Promise<DesktopOperationResult<{ readonly released: true }>>
    readonly list: (input: {
      readonly workspaceId: string
      readonly authorizationGeneration: number
      readonly directoryPath: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceFileList>>
    readonly read: (input: {
      readonly workspaceId: string
      readonly authorizationGeneration: number
      readonly path: string
    }) => Promise<DesktopOperationResult<DesktopWorkspaceFileRead>>
  }
  readonly providers: {
    readonly list: () => Promise<
      DesktopOperationResult<{ readonly items: readonly DesktopProvider[] }>
    >
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
    readonly cancel: (input: { readonly invocationId: string }) => Promise<
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
      readonly nodeId: string
      readonly reportId: string
    }) => Promise<
      DesktopOperationResult<{ readonly collaborationRequest: DesktopTeamCollaborationRequest }>
    >
    readonly execute: (input: {
      readonly workspaceId: string
      readonly conversationId: string
      readonly task: string
      readonly teamMode: true
      readonly rosterEpoch: number
      readonly budget: DesktopTeamRunBudget
      readonly allowedSpecialistRoleIds?: readonly string[]
    }) => Promise<
      DesktopOperationResult<{
        readonly proof: {
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
        }
      }>
    >
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
    !isRecord(value.workbenchSettings) ||
    !hasFunction(value.workbenchSettings, 'get') ||
    !hasFunction(value.workbenchSettings, 'update') ||
    !isRecord(value.workspaceComposition) ||
    !hasFunction(value.workspaceComposition, 'get') ||
    !hasFunction(value.workspaceComposition, 'propose') ||
    !hasFunction(value.workspaceComposition, 'proposeFromAssistant') ||
    !hasFunction(value.workspaceComposition, 'proposeRollback') ||
    !hasFunction(value.workspaceComposition, 'decide') ||
    !isRecord(value.workspaceComponents) ||
    !hasFunction(value.workspaceComponents, 'get') ||
    !hasFunction(value.workspaceComponents, 'propose') ||
    !hasFunction(value.workspaceComponents, 'proposeFromAssistant') ||
    !hasFunction(value.workspaceComponents, 'importOwnerPackage') ||
    !hasFunction(value.workspaceComponents, 'importAssistantPackage') ||
    !hasFunction(value.workspaceComponents, 'decide') ||
    !hasFunction(value.workspaceComponents, 'action') ||
    !hasFunction(value.workspaceComponents, 'invoke') ||
    !hasFunction(value.workspaceComponents, 'emergencyStop') ||
    !hasFunction(value.workspaceComponents, 'reconcile') ||
    !isRecord(value.workspaceFiles) ||
    !hasFunction(value.workspaceFiles, 'authorize') ||
    !hasFunction(value.workspaceFiles, 'release') ||
    !hasFunction(value.workspaceFiles, 'list') ||
    !hasFunction(value.workspaceFiles, 'read') ||
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
