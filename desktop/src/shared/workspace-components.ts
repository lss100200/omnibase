export const WORKSPACE_COMPONENT_FAMILIES = Object.freeze([
  "declarative_ui",
  "instruction_skill",
  "mcp_connector",
  "sandbox_workload",
  "trusted_local_adapter",
] as const);

export const WORKSPACE_COMPONENT_LIFECYCLE_ACTIONS = Object.freeze([
  "install",
  "bind",
  "activate",
  "disable",
  "upgrade",
  "rollback",
  "revoke",
  "uninstall",
] as const);

export const WORKSPACE_COMPONENT_OPERATIONS = Object.freeze([
  "ui.render",
  "skill.resolve",
  "mcp.call",
  "sandbox.run",
  "local_adapter.open",
] as const);

export type DesktopWorkspaceComponentFamily =
  (typeof WORKSPACE_COMPONENT_FAMILIES)[number];
export type DesktopWorkspaceComponentLifecycleAction =
  (typeof WORKSPACE_COMPONENT_LIFECYCLE_ACTIONS)[number];
export type DesktopWorkspaceComponentOperation =
  (typeof WORKSPACE_COMPONENT_OPERATIONS)[number];
export type DesktopWorkspaceComponentDecision = "approve" | "reject";
export type DesktopWorkspaceComponentTerminalState =
  | "succeeded"
  | "failed"
  | "cancelled"
  | "unknown";
export type DesktopWorkspaceComponentEffectState =
  | "none"
  | "pending"
  | "succeeded"
  | "failed"
  | "unknown";

export interface DesktopWorkspaceComponentGrantRequest {
  readonly action: string;
  readonly logicalResourceId: string | null;
  readonly resourceVersion: number | null;
  readonly logicalServiceId: string | null;
  readonly expiresInSeconds: number;
  readonly maximumInvocations: number;
  readonly maximumBytesIn: number;
  readonly maximumBytesOut: number;
  readonly maximumTokens: number;
  readonly maximumWallTimeMs: number;
  readonly maximumCostUnits: number;
}

export interface DesktopWorkspaceComponentSlotBindingRequest {
  readonly slotId: string;
  readonly bindingKey: string;
  readonly orderIndex: number;
  readonly configuration: DesktopWorkspaceComponentJsonValue;
}

export interface DesktopWorkspaceComponentDependencyRequest {
  readonly componentId: string;
  readonly version: string;
  readonly policyManifestSha256: string;
  readonly manifestSha256: string;
  readonly packageSha256: string;
}

export interface DesktopWorkspaceComponentSlotDescriptor {
  readonly slotId: string;
  readonly cardinality: "one" | "many";
  readonly minimumOrder: number;
  readonly maximumOrder: number;
}

export interface DesktopWorkspaceComponentSettingsProperty {
  readonly type: "boolean" | "integer" | "number" | "string";
  readonly default?: DesktopWorkspaceComponentJsonValue;
  readonly enum?: readonly DesktopWorkspaceComponentJsonValue[];
  readonly minimum?: number;
  readonly maximum?: number;
  readonly maxLength?: number;
}

export interface DesktopWorkspaceComponentSettingsSchema {
  readonly kind: "closed_object";
  readonly version: number;
  readonly additionalProperties: false;
  readonly properties: Readonly<
    Record<string, DesktopWorkspaceComponentSettingsProperty>
  >;
  readonly required: readonly string[];
}

export interface DesktopWorkspaceComponentCatalogItem {
  readonly componentId: string;
  readonly version: string;
  readonly family: DesktopWorkspaceComponentFamily;
  readonly displayName: string;
  readonly publisherClass: "source_owned" | "owner_reviewed";
  readonly adapterId:
    | "builtin-ui.v1"
    | "instruction-skill.v1"
    | "readonly-mcp.v1"
    | "p34-sandbox.v1"
    | "trusted-local-app.v1";
  readonly policyManifestSha256: string;
  readonly manifestSha256: string | null;
  readonly packageSha256: string | null;
  readonly operations: readonly DesktopWorkspaceComponentOperation[];
  readonly slots: readonly DesktopWorkspaceComponentSlotDescriptor[];
  readonly dependencies: readonly DesktopWorkspaceComponentDependencyRequest[];
  readonly conflicts: readonly string[];
  readonly budgets: Readonly<{
    maxCalls: number;
    maxBytesIn: number;
    maxBytesOut: number;
    maxTokens: number;
    maxWallTimeMs: number;
    maxCostUnits: number;
    maxRetries: number;
    maxConcurrency: number;
  }>;
  readonly network: Readonly<{
    required: boolean;
    serviceClasses: readonly string[];
  }>;
  readonly recovery: Readonly<{
    autoReplayUnknown: false;
    retention: "retain_workspace_data" | "delete_component_data";
    safeMode: "disable_component";
  }>;
  readonly stateSchema: Readonly<{
    kind: "canonical_json";
    version: number;
  }>;
  readonly settingsSchema: DesktopWorkspaceComponentSettingsSchema;
  readonly available: boolean;
  readonly unavailableReason: "package_not_attested" | null;
}

export interface DesktopWorkspaceComponentInstallation {
  readonly installationId: string;
  readonly workspaceId: string;
  readonly componentId: string;
  readonly version: string;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly state:
    | "installed"
    | "bound"
    | "active"
    | "blocked"
    | "disabled"
    | "revoked"
    | "uninstalled";
  readonly revision: number;
  readonly bindingGeneration: number;
  readonly desiredConfiguration: DesktopWorkspaceComponentJsonValue;
  readonly currentSlotBindings: readonly DesktopWorkspaceComponentSlotBindingRequest[];
  readonly dependencyGraph: readonly DesktopWorkspaceComponentDependencyRequest[];
  readonly health: "unknown" | "healthy" | "degraded" | "unavailable";
  readonly lastErrorCode: string | null;
  readonly updatedAt: string;
}

export interface DesktopWorkspaceComponentProposal {
  readonly proposalId: string;
  readonly workspaceId: string;
  readonly componentId: string;
  readonly targetVersion: string;
  readonly changeKind: DesktopWorkspaceComponentLifecycleAction;
  readonly baseRevision: number;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly requestSha256: string;
  readonly requestedGrants: readonly DesktopWorkspaceComponentGrantRequest[];
  readonly desiredConfiguration: DesktopWorkspaceComponentJsonValue;
  readonly desiredSlotBindings: readonly DesktopWorkspaceComponentSlotBindingRequest[];
  readonly dependencyGraph: readonly DesktopWorkspaceComponentDependencyRequest[];
  readonly sourceKind: "owner" | "assistant";
  readonly sourceReference: string | null;
  readonly decision: "approved" | "rejected" | null;
  readonly createdAt: string;
}

export interface DesktopWorkspaceComponentOperationRecord {
  readonly operationId: string;
  readonly workspaceId: string;
  readonly componentId: string;
  readonly installationId: string | null;
  readonly action: string;
  readonly requestSha256: string;
  readonly bindingGeneration: number;
  readonly state: "pending" | DesktopWorkspaceComponentTerminalState;
  readonly resultSha256: string | null;
  readonly evidenceSha256: string | null;
  readonly errorCode: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopWorkspaceComponentUsageDimensions {
  readonly calls: number;
  readonly bytesIn: number;
  readonly bytesOut: number;
  readonly tokens: number;
  readonly wallTimeMs: number;
  readonly costUnits: number;
  readonly retries: number;
  readonly concurrency?: number;
}

export interface DesktopWorkspaceComponentGrant {
  readonly grantId: string;
  readonly workspaceId: string;
  readonly installationId: string;
  readonly bindingGeneration: number;
  readonly runtimeInstanceId: string;
  readonly componentId: string;
  readonly version: string;
  readonly actions: readonly DesktopWorkspaceComponentOperation[];
  readonly scope: readonly DesktopWorkspaceComponentGrantRequest[];
  readonly requiresNetwork: boolean;
  readonly state: "active" | "revoked" | "expired";
  readonly notBefore: string;
  readonly expiresAt: string;
  readonly limits: DesktopWorkspaceComponentUsageDimensions;
  readonly used: DesktopWorkspaceComponentUsageDimensions;
  readonly remaining: DesktopWorkspaceComponentUsageDimensions;
}

export interface DesktopWorkspaceComponentRevocation {
  readonly revocationId: string;
  readonly workspaceId: string;
  readonly installationId: string;
  readonly componentId: string;
  readonly bindingGeneration: number;
  readonly runtimeInstanceId: string | null;
  readonly grantId: string | null;
  readonly reasonCode: string;
  readonly actorType: "owner" | "system";
  readonly createdAt: string;
}

export interface DesktopWorkspaceComponentRecovery {
  readonly recoveryId: string;
  readonly workspaceId: string;
  readonly componentId: string;
  readonly installationId: string;
  readonly bindingGeneration: number;
  readonly previousRuntimeInstanceId: string;
  readonly operationId: string;
  readonly effectId: string;
  readonly adapterId: DesktopWorkspaceComponentCatalogItem["adapterId"];
  readonly runtimeInstanceId: string;
  readonly workloadIdentityDigest: string;
  readonly requestSha256: string;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly state: "pending" | "succeeded" | "failed" | "unknown";
  readonly reasonCode: string;
  readonly createdAt: string;
}

export interface DesktopWorkspaceComponentRecoverySettleInput {
  readonly workspaceId: string;
  readonly recoveryId: string;
  readonly operationId: string;
  readonly outcome: "succeeded" | "failed" | "unknown";
  readonly evidenceSha256: string;
  readonly healthState: "healthy" | "unhealthy" | "unknown" | null;
  readonly runtimeInstanceId: string;
  readonly workloadIdentityDigest: string;
  readonly errorCode: string | null;
}

export interface DesktopWorkspaceComponentRecoverySettleResult {
  readonly recoveryId: string;
  readonly operation: DesktopWorkspaceComponentOperationRecord;
  readonly effect: DesktopWorkspaceComponentEffect;
  readonly replayed: boolean;
}

export interface DesktopWorkspaceComponentEffect {
  readonly effectId: string;
  readonly operationId: string;
  readonly workspaceId: string;
  readonly componentId: string;
  readonly state: DesktopWorkspaceComponentEffectState;
  readonly evidenceSha256: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopWorkspaceComponentReconciliation {
  readonly reconciliationId: string;
  readonly operationId: string;
  readonly effectId: string;
  readonly workspaceId: string;
  readonly outcome: "succeeded" | "failed";
  readonly evidenceSha256: string;
  readonly createdAt: string;
}

export type DesktopWorkspaceComponentAuditEventType =
  | "workspace_component_proposed"
  | "workspace_component_decided"
  | "workspace_component_state_changed"
  | "workspace_component_invocation_begun"
  | "workspace_component_invocation_settled"
  | "workspace_component_reconciled"
  | "workspace_component_emergency_stopped"
  | "workspace_component_recovery_blocked";

export interface DesktopWorkspaceComponentAuditEvent {
  readonly sequence: number;
  readonly eventId: string;
  readonly eventType: DesktopWorkspaceComponentAuditEventType;
  readonly payload: DesktopWorkspaceComponentJsonValue;
  readonly createdAt: string;
}

export interface DesktopWorkspaceComponentSnapshot {
  readonly workspaceId: string;
  readonly catalog: readonly DesktopWorkspaceComponentCatalogItem[];
  readonly installations: readonly DesktopWorkspaceComponentInstallation[];
  readonly proposals: readonly DesktopWorkspaceComponentProposal[];
  readonly operations: readonly DesktopWorkspaceComponentOperationRecord[];
  readonly effects: readonly DesktopWorkspaceComponentEffect[];
  readonly grants: readonly DesktopWorkspaceComponentGrant[];
  readonly revocations: readonly DesktopWorkspaceComponentRevocation[];
  readonly recoveries: readonly DesktopWorkspaceComponentRecovery[];
  readonly reconciliations: readonly DesktopWorkspaceComponentReconciliation[];
  readonly audit: readonly DesktopWorkspaceComponentAuditEvent[];
}

export interface DesktopWorkspaceComponentProposeInput {
  readonly workspaceId: string;
  readonly componentId: string;
  readonly targetVersion: string;
  readonly changeKind: DesktopWorkspaceComponentLifecycleAction;
  readonly expectedRevision: number;
  readonly requestedGrants: readonly DesktopWorkspaceComponentGrantRequest[];
  readonly desiredConfiguration: DesktopWorkspaceComponentJsonValue;
  readonly desiredSlotBindings: readonly DesktopWorkspaceComponentSlotBindingRequest[];
  readonly dependencyGraph: readonly DesktopWorkspaceComponentDependencyRequest[];
  readonly idempotencyKey: string;
}

export interface DesktopWorkspaceComponentAssistantProposalInput {
  readonly workspaceId: string;
  readonly messageId: string;
  readonly idempotencyKey: string;
}

export interface DesktopWorkspaceComponentProposalResult {
  readonly proposal: DesktopWorkspaceComponentProposal;
  readonly replayed: boolean;
}

export interface DesktopWorkspaceComponentDecisionInput {
  readonly workspaceId: string;
  readonly proposalId: string;
  readonly decision: DesktopWorkspaceComponentDecision;
  readonly requestSha256: string;
}

export interface DesktopWorkspaceComponentDecisionResult {
  readonly workspaceId: string;
  readonly proposalId: string;
  readonly requestSha256: string;
  readonly decision: "approved" | "rejected";
  readonly installationRevision: number;
}

export interface DesktopWorkspaceComponentActionInput {
  readonly workspaceId: string;
  readonly componentId: string;
  readonly action: DesktopWorkspaceComponentLifecycleAction;
  readonly proposalId: string;
  readonly requestSha256: string;
  readonly expectedRevision: number;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly idempotencyKey: string;
}

export interface DesktopWorkspaceComponentActionResult {
  readonly operation: DesktopWorkspaceComponentOperationRecord;
  readonly installation: DesktopWorkspaceComponentInstallation | null;
  readonly lifecycleTicket: DesktopWorkspaceComponentLifecycleTicket;
  readonly replayed: boolean;
}

export interface DesktopWorkspaceComponentLifecycleTicket {
  readonly operationId: string;
  readonly effectId: string;
  readonly workspaceId: string;
  readonly componentId: string;
  readonly version: string;
  readonly action: DesktopWorkspaceComponentLifecycleAction;
  readonly adapterId: DesktopWorkspaceComponentCatalogItem["adapterId"];
  readonly installationId: string | null;
  readonly bindingGeneration: number | null;
  readonly runtimeInstanceId: string | null;
  readonly workloadIdentityDigest: string | null;
  readonly configuration: DesktopWorkspaceComponentJsonValue;
  readonly configurationSha256: string;
  readonly slotBindings: readonly DesktopWorkspaceComponentSlotBindingRequest[];
  readonly slotBindingsSha256: string;
  readonly dependencyGraph: readonly DesktopWorkspaceComponentDependencyRequest[];
  readonly dependencyGraphSha256: string;
  readonly quiesceTimeoutMs: number;
  readonly requestSha256: string;
  readonly manifestSha256: string;
  readonly packageSha256: string;
}

export type DesktopWorkspaceComponentNativeActionInput =
  | Readonly<
      DesktopWorkspaceComponentActionInput & {
        readonly phase: "prepare";
        readonly operationId: null;
        readonly outcome: null;
        readonly evidenceSha256: null;
        readonly healthState: null;
        readonly runtimeInstanceId: null;
        readonly workloadIdentityDigest: null;
        readonly errorCode: null;
      }
    >
  | Readonly<
      DesktopWorkspaceComponentActionInput & {
        readonly phase: "settle";
        readonly operationId: string;
        readonly outcome: "succeeded" | "failed" | "unknown";
        readonly evidenceSha256: string;
        readonly healthState: "healthy" | "unhealthy" | "unknown";
        readonly runtimeInstanceId: string | null;
        readonly workloadIdentityDigest: string | null;
        readonly errorCode: string | null;
      }
    >;

interface DesktopWorkspaceComponentInvokeBase {
  readonly workspaceId: string;
  readonly componentId: string;
  readonly expectedRevision: number;
  readonly bindingGeneration: number;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly idempotencyKey: string;
  readonly logicalResourceId?: string;
  readonly resourceVersion?: number;
  readonly logicalServiceId?: string;
  readonly bytesOutReserved: number;
  readonly tokensReserved: number;
  readonly wallTimeMs: number;
  readonly costUnits: number;
}

export interface DesktopWorkspaceComponentUiInvokeInput
  extends DesktopWorkspaceComponentInvokeBase {
  readonly operation: "ui.render";
  readonly arguments: Readonly<{
    slotId: string;
    viewId: string;
  }>;
}

export interface DesktopWorkspaceComponentSkillInvokeInput
  extends DesktopWorkspaceComponentInvokeBase {
  readonly operation: "skill.resolve";
  readonly arguments: Readonly<{
    skillId: string;
    task: string;
  }>;
}

export interface DesktopWorkspaceComponentMcpInvokeInput
  extends DesktopWorkspaceComponentInvokeBase {
  readonly operation: "mcp.call";
  readonly arguments: Readonly<{
    toolName:
      | "omnibase_files_list"
      | "omnibase_files_read"
      | "omnibase_files_hash"
      | "omnibase_text_search";
    path?: string;
    query?: string;
  }>;
}

export interface DesktopWorkspaceComponentSandboxInvokeInput
  extends DesktopWorkspaceComponentInvokeBase {
  readonly operation: "sandbox.run";
  readonly arguments: Readonly<{
    workloadId: string;
    inputArtifactIds: readonly string[];
  }>;
}

export interface DesktopWorkspaceComponentLocalAdapterInvokeInput
  extends DesktopWorkspaceComponentInvokeBase {
  readonly operation: "local_adapter.open";
  readonly arguments: Readonly<{
    adapterId: "knowledge.ebook";
    destination: "workspace" | "phase" | "document";
    logicalId?: string;
  }>;
}

export type DesktopWorkspaceComponentInvokeInput =
  | DesktopWorkspaceComponentUiInvokeInput
  | DesktopWorkspaceComponentSkillInvokeInput
  | DesktopWorkspaceComponentMcpInvokeInput
  | DesktopWorkspaceComponentSandboxInvokeInput
  | DesktopWorkspaceComponentLocalAdapterInvokeInput;

export interface DesktopWorkspaceComponentBeginInput {
  readonly workspaceId: string;
  readonly componentId: string;
  readonly action: DesktopWorkspaceComponentOperation;
  readonly argumentsSha256: string;
  readonly expectedRevision: number;
  readonly bindingGeneration: number;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly idempotencyKey: string;
  readonly logicalResourceId?: string;
  readonly resourceVersion?: number;
  readonly logicalServiceId?: string;
  readonly bytesIn: number;
  readonly bytesOutReserved: number;
  readonly tokensReserved: number;
  readonly wallTimeMs: number;
  readonly costUnits: number;
}

export interface DesktopWorkspaceComponentExecutionTicket {
  readonly operationId: string;
  readonly workspaceId: string;
  readonly componentId: string;
  readonly version: string;
  readonly action: DesktopWorkspaceComponentOperation;
  readonly requestSha256: string;
  readonly argumentsSha256: string;
  readonly adapterId: DesktopWorkspaceComponentCatalogItem["adapterId"];
  readonly configuration: DesktopWorkspaceComponentJsonValue;
  readonly configurationSha256: string;
  readonly slotBindings: readonly DesktopWorkspaceComponentSlotBindingRequest[];
  readonly slotBindingsSha256: string;
  readonly dependencyGraph: readonly DesktopWorkspaceComponentDependencyRequest[];
  readonly dependencyGraphSha256: string;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly bindingGeneration: number;
  readonly runtimeInstanceId: string;
  readonly workloadIdentityDigest: string;
  readonly workloadFencingToken: number;
  readonly networkFencingToken: number | null;
  readonly expiresAt: string;
}

export interface DesktopWorkspaceComponentBeginResult {
  readonly ticket: DesktopWorkspaceComponentExecutionTicket;
  readonly replayed: boolean;
}

export interface DesktopWorkspaceComponentSettleInput {
  readonly workspaceId: string;
  readonly operationId: string;
  readonly requestSha256: string;
  readonly state: DesktopWorkspaceComponentTerminalState;
  readonly resultSha256?: string;
  readonly evidenceSha256: string;
  readonly errorCode?: string;
  readonly actualBytesOut: number;
  readonly actualTokens: number;
  readonly actualWallTimeMs: number;
}

export interface DesktopWorkspaceComponentSettleResult {
  readonly operation: DesktopWorkspaceComponentOperationRecord;
  readonly effect: DesktopWorkspaceComponentEffect;
  readonly replayed: boolean;
}

export type DesktopWorkspaceComponentJsonValue =
  | null
  | boolean
  | number
  | string
  | readonly DesktopWorkspaceComponentJsonValue[]
  | Readonly<{ readonly [key: string]: DesktopWorkspaceComponentJsonValue }>;

export interface DesktopWorkspaceComponentInvokeResult {
  readonly operationId: string;
  readonly state: DesktopWorkspaceComponentTerminalState;
  readonly output: DesktopWorkspaceComponentJsonValue;
  readonly settlement: DesktopWorkspaceComponentSettleResult;
}

export interface DesktopWorkspaceComponentEmergencyStopInput {
  readonly workspaceId: string;
  readonly idempotencyKey: string;
  readonly reasonCode: string;
}

export interface DesktopWorkspaceComponentEmergencyStopTicket {
  readonly componentId: string;
  readonly operationId: string;
  readonly effectId: string;
  readonly requestSha256: string;
}

export type DesktopWorkspaceComponentNativeEmergencyStopInput =
  | Readonly<
      DesktopWorkspaceComponentEmergencyStopInput & {
        readonly phase: "prepare";
      }
    >
  | Readonly<
      DesktopWorkspaceComponentEmergencyStopInput &
        DesktopWorkspaceComponentEmergencyStopTicket & {
          readonly phase: "settle";
          readonly outcome: "succeeded" | "failed" | "unknown";
          readonly evidenceSha256: string;
          readonly errorCode: string | null;
        }
    >;

export interface DesktopWorkspaceComponentEmergencyStopPrepareResult {
  readonly workspaceId: string;
  readonly tickets: readonly DesktopWorkspaceComponentEmergencyStopTicket[];
  readonly fencedComponentIds: readonly string[];
  readonly replayed: boolean;
}

export interface DesktopWorkspaceComponentEmergencyStopSettleResult {
  readonly workspaceId: string;
  readonly componentId: string;
  readonly operation: DesktopWorkspaceComponentOperationRecord;
  readonly effect: DesktopWorkspaceComponentEffect;
  readonly replayed: boolean;
}

export type DesktopWorkspaceComponentNativeEmergencyStopResult =
  | DesktopWorkspaceComponentEmergencyStopPrepareResult
  | DesktopWorkspaceComponentEmergencyStopSettleResult;

export interface DesktopWorkspaceComponentEmergencyStopResult {
  readonly workspaceId: string;
  readonly operationIds: readonly string[];
  readonly stoppedComponentIds: readonly string[];
  readonly replayed: boolean;
}

export interface DesktopWorkspaceComponentReconcileInput {
  readonly workspaceId: string;
  readonly operationId: string;
  readonly effectId: string;
  readonly requestSha256: string;
  readonly outcome: "succeeded" | "failed";
  readonly evidenceSha256: string;
}

export interface DesktopWorkspaceComponentReconcileResult {
  readonly operation: DesktopWorkspaceComponentOperationRecord;
  readonly effect: DesktopWorkspaceComponentEffect;
  readonly reconciliationId: string;
  readonly replayed: boolean;
}

export interface DesktopWorkspaceComponentPackageAttestationInput {
  readonly componentId: string;
  readonly version: string;
  readonly adapterId: DesktopWorkspaceComponentCatalogItem["adapterId"];
  readonly policyManifestSha256: string;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly inventorySha256: string;
}

export interface DesktopWorkspaceComponentPackageAttestationResult
  extends DesktopWorkspaceComponentPackageAttestationInput {
  readonly attestedBy: "runtime_manifest";
  readonly createdAt: string;
  readonly replayed: boolean;
}

export interface DesktopWorkspaceComponentOwnerPackageRegisterInput {
  readonly workspaceId: string;
  readonly manifest: Readonly<{
    readonly [key: string]: DesktopWorkspaceComponentJsonValue;
  }>;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly inventorySha256: string;
}

export interface DesktopWorkspaceComponentOwnerPackageRegistration {
  readonly componentId: string;
  readonly version: string;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly publisherClass: "owner_reviewed";
  readonly registeredAt: string;
  readonly replayed: boolean;
}

export interface DesktopWorkspaceComponentOwnerPackageImportResult {
  readonly cancelled: boolean;
  readonly registration: DesktopWorkspaceComponentOwnerPackageRegistration | null;
}

export interface DesktopWorkspaceComponentAssistantPackageImportInput {
  readonly workspaceId: string;
  readonly conversationId: string;
  readonly messageId: string;
  readonly packageJson: string;
  readonly manifestSha256: string;
  readonly packageSha256: string;
}
