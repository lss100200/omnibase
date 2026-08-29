import type { IpcMain, IpcMainInvokeEvent } from "electron";

import { isAllowedIpcSender } from "./security/origin-policy.ts";
import {
  IPC_CHANNELS,
  IPC_EVENT_CHANNELS,
  PERSONAL_EMPLOYEE_IDS,
  SPECIALIST_EMPLOYEE_IDS,
  requireNoIpcArguments,
  teamEventIdentityComplete,
  type DesktopAgentRole,
  type DesktopAgentRoleIdInput,
  type DesktopAgentRoleList,
  type DesktopAgentRoleTestResult,
  type DesktopAgentRoleUpdateInput,
  type DesktopApplicationPreference,
  type DesktopApplicationPreferenceUpdateInput,
  type DesktopConversationArchiveInput,
  type DesktopConversationCancelInput,
  type DesktopConversationCreateInput,
  type DesktopConversationDetail,
  type DesktopConversationEvent,
  type DesktopConversationGetInput,
  type DesktopConversationList,
  type DesktopConversationSendInput,
  type DesktopConversation,
  type DesktopOperationResult,
  type DesktopOwnerBootstrapInput,
  type DesktopOwnerBootstrapResult,
  type DesktopOwnerStatus,
  type DesktopParentAgent,
  type DesktopProviderIdInput,
  type DesktopProviderList,
  type DesktopProviderMutationResult,
  type DesktopProviderTestResult,
  type DesktopProviderUpsertInput,
  type DesktopTeamCollaborationInput,
  type DesktopTeamCollaborationRequest,
  type DesktopTeamRun,
  type DesktopTeamRunAppendBudgetInput,
  type DesktopTeamRunEvent,
  type DesktopTeamRunExecuteInput,
  type DesktopTeamRunIdInput,
  type DesktopTeamRunProof,
  type DesktopTeamRunProposalResult,
  type DesktopTeamRunStartInput,
  type DesktopTeamRunSubmitProposalInput,
  type DesktopWorkspaceArchiveInput,
  type DesktopWorkspaceCompositionAssistantProposalInput,
  type DesktopWorkspaceCompositionDecisionInput,
  type DesktopWorkspaceCompositionDecisionResult,
  type DesktopWorkspaceCompositionOwnerProposalInput,
  type DesktopWorkspaceCompositionProfileValue,
  type DesktopWorkspaceCompositionProposalResult,
  type DesktopWorkspaceCompositionRollbackProposalInput,
  type DesktopWorkspaceCompositionSnapshot,
  type DesktopWorkspaceComponentActionInput,
  type DesktopWorkspaceComponentActionResult,
  type DesktopWorkspaceComponentAssistantPackageImportInput,
  type DesktopWorkspaceComponentAssistantProposalInput,
  type DesktopWorkspaceComponentOwnerPackageImportResult,
  type DesktopWorkspaceComponentDecisionInput,
  type DesktopWorkspaceComponentDecisionResult,
  type DesktopWorkspaceComponentEmergencyStopInput,
  type DesktopWorkspaceComponentEmergencyStopResult,
  type DesktopWorkspaceComponentInvokeInput,
  type DesktopWorkspaceComponentInvokeResult,
  type DesktopWorkspaceComponentProposalResult,
  type DesktopWorkspaceComponentProposeInput,
  type DesktopWorkspaceComponentReconcileInput,
  type DesktopWorkspaceComponentReconcileResult,
  type DesktopWorkspaceComponentSnapshot,
  type DesktopWorkspaceCreateInput,
  type DesktopWorkspaceFileAuthorization,
  type DesktopWorkspaceFileAuthorizeInput,
  type DesktopWorkspaceFileList,
  type DesktopWorkspaceFileListInput,
  type DesktopWorkspaceFileReadInput,
  type DesktopWorkspaceFileReadResult,
  type DesktopWorkspaceFileReleaseInput,
  type DesktopWorkspaceIdInput,
  type DesktopWorkspaceList,
  type DesktopWorkspaceMutationResult,
  type PersonalEmployeeId,
  type PersonalTeamBlackboard,
  type RuntimeStatus,
  type SpecialistEmployeeId,
  type TeamRunBudget,
} from "./shared/ipc-contract.ts";

export interface IpcMainLike {
  handle(
    channel: string,
    listener: (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown,
  ): void;
  removeHandler(channel: string): void;
}

export interface IpcDependencies {
  readonly getVersion: () => string;
  readonly getRuntimeStatus: () => RuntimeStatus;
  readonly retryRuntimeStartup: () => Promise<RuntimeStatus>;
  readonly getOwnerStatus: () => Promise<
    DesktopOperationResult<DesktopOwnerStatus>
  >;
  readonly bootstrapOwner: (
    input: DesktopOwnerBootstrapInput,
  ) => Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>>;
  readonly listWorkspaces: () => Promise<
    DesktopOperationResult<DesktopWorkspaceList>
  >;
  readonly createWorkspace: (
    input: DesktopWorkspaceCreateInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>;
  readonly archiveWorkspace: (
    input: DesktopWorkspaceArchiveInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>;
  readonly getWorkspaceAgent: (
    input: DesktopWorkspaceIdInput,
  ) => Promise<DesktopOperationResult<{ readonly agent: DesktopParentAgent }>>;
  readonly getApplicationPreference: () => Promise<
    DesktopOperationResult<{
      readonly preference: DesktopApplicationPreference;
    }>
  >;
  readonly updateApplicationPreference: (
    input: DesktopApplicationPreferenceUpdateInput,
  ) => Promise<
    DesktopOperationResult<{
      readonly preference: DesktopApplicationPreference;
    }>
  >;
  readonly getWorkspaceComposition: (
    input: DesktopWorkspaceIdInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceCompositionSnapshot>>;
  readonly proposeWorkspaceComposition: (
    input: DesktopWorkspaceCompositionOwnerProposalInput,
  ) => Promise<
    DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
  >;
  readonly proposeWorkspaceCompositionFromAssistant: (
    input: DesktopWorkspaceCompositionAssistantProposalInput,
  ) => Promise<
    DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
  >;
  readonly proposeWorkspaceCompositionRollback: (
    input: DesktopWorkspaceCompositionRollbackProposalInput,
  ) => Promise<
    DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
  >;
  readonly decideWorkspaceComposition: (
    input: DesktopWorkspaceCompositionDecisionInput,
  ) => Promise<
    DesktopOperationResult<DesktopWorkspaceCompositionDecisionResult>
  >;
  readonly getWorkspaceComponents: (
    input: DesktopWorkspaceIdInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceComponentSnapshot>>;
  readonly proposeWorkspaceComponent: (
    input: DesktopWorkspaceComponentProposeInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceComponentProposalResult>>;
  readonly proposeWorkspaceComponentFromAssistant: (
    input: DesktopWorkspaceComponentAssistantProposalInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceComponentProposalResult>>;
  readonly importOwnerWorkspaceComponentPackage: (
    input: DesktopWorkspaceIdInput,
  ) => Promise<
    DesktopOperationResult<DesktopWorkspaceComponentOwnerPackageImportResult>
  >;
  readonly importAssistantWorkspaceComponentPackage: (
    input: DesktopWorkspaceComponentAssistantPackageImportInput,
  ) => Promise<
    DesktopOperationResult<DesktopWorkspaceComponentOwnerPackageImportResult>
  >;
  readonly decideWorkspaceComponent: (
    input: DesktopWorkspaceComponentDecisionInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceComponentDecisionResult>>;
  readonly applyWorkspaceComponentAction: (
    input: DesktopWorkspaceComponentActionInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceComponentActionResult>>;
  readonly invokeWorkspaceComponent: (
    input: DesktopWorkspaceComponentInvokeInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceComponentInvokeResult>>;
  readonly emergencyStopWorkspaceComponents: (
    input: DesktopWorkspaceComponentEmergencyStopInput,
  ) => Promise<
    DesktopOperationResult<DesktopWorkspaceComponentEmergencyStopResult>
  >;
  readonly reconcileWorkspaceComponent: (
    input: DesktopWorkspaceComponentReconcileInput,
  ) => Promise<
    DesktopOperationResult<DesktopWorkspaceComponentReconcileResult>
  >;
  readonly authorizeWorkspaceFiles: (
    input: DesktopWorkspaceFileAuthorizeInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceFileAuthorization>>;
  readonly releaseWorkspaceFiles: (
    input: DesktopWorkspaceFileReleaseInput,
  ) => Promise<DesktopOperationResult<{ readonly released: true }>>;
  readonly listWorkspaceFiles: (
    input: DesktopWorkspaceFileListInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceFileList>>;
  readonly readWorkspaceFile: (
    input: DesktopWorkspaceFileReadInput,
  ) => Promise<DesktopOperationResult<DesktopWorkspaceFileReadResult>>;
  readonly listProviders: () => Promise<
    DesktopOperationResult<DesktopProviderList>
  >;
  readonly upsertProvider: (
    input: DesktopProviderUpsertInput,
  ) => Promise<DesktopOperationResult<DesktopProviderMutationResult>>;
  readonly deleteProvider: (
    input: DesktopProviderIdInput,
  ) => Promise<
    DesktopOperationResult<{ readonly deleted: true; readonly id: string }>
  >;
  readonly testProvider: (
    input: DesktopProviderIdInput,
  ) => Promise<DesktopOperationResult<DesktopProviderTestResult>>;
  readonly listConversations: (
    input: DesktopWorkspaceIdInput,
  ) => Promise<DesktopOperationResult<DesktopConversationList>>;
  readonly createConversation: (
    input: DesktopConversationCreateInput,
  ) => Promise<
    DesktopOperationResult<{
      readonly created: true;
      readonly conversation: DesktopConversation;
    }>
  >;
  readonly archiveConversation: (
    input: DesktopConversationArchiveInput,
  ) => Promise<
    DesktopOperationResult<{ readonly conversation: DesktopConversation }>
  >;
  readonly getConversation: (
    input: DesktopConversationGetInput,
  ) => Promise<DesktopOperationResult<DesktopConversationDetail>>;
  readonly sendConversation: (
    input: DesktopConversationSendInput,
    emit: (event: DesktopConversationEvent) => void,
  ) => Promise<DesktopOperationResult<DesktopConversationEvent>>;
  readonly cancelConversation: (
    input: DesktopConversationCancelInput,
  ) => Promise<
    DesktopOperationResult<{
      readonly cancelled: boolean;
      readonly id: string;
      readonly accepted: boolean;
    }>
  >;
  readonly abortInFlightSend: () => Promise<
    DesktopOperationResult<{ readonly aborted: boolean }>
  >;
  readonly listAgentRoles: (
    input: DesktopWorkspaceIdInput,
  ) => Promise<DesktopOperationResult<DesktopAgentRoleList>>;
  readonly getAgentRole: (
    input: DesktopAgentRoleIdInput,
  ) => Promise<DesktopOperationResult<{ readonly role: DesktopAgentRole }>>;
  readonly updateAgentRole: (
    input: DesktopAgentRoleUpdateInput,
  ) => Promise<DesktopOperationResult<{ readonly role: DesktopAgentRole }>>;
  readonly testAgentRole: (
    input: DesktopAgentRoleIdInput,
  ) => Promise<DesktopOperationResult<DesktopAgentRoleTestResult>>;
  readonly startTeamRun: (
    input: DesktopTeamRunStartInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ) => Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>>;
  readonly cancelTeamRun: (
    input: DesktopTeamRunIdInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ) => Promise<
    DesktopOperationResult<{
      readonly cancelled: boolean;
      readonly accepted: boolean;
      readonly teamRun: DesktopTeamRun;
    }>
  >;
  readonly getTeamRun: (
    input: DesktopTeamRunIdInput,
  ) => Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>>;
  readonly listTeamRuns: (
    input: DesktopWorkspaceIdInput,
  ) => Promise<
    DesktopOperationResult<{ readonly items: readonly DesktopTeamRun[] }>
  >;
  readonly submitTeamProposal: (
    input: DesktopTeamRunSubmitProposalInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ) => Promise<DesktopOperationResult<DesktopTeamRunProposalResult>>;
  readonly getTeamBlackboard: (
    input: DesktopTeamRunIdInput,
  ) => Promise<
    DesktopOperationResult<{ readonly blackboard: PersonalTeamBlackboard }>
  >;
  readonly recordTeamCollaboration: (
    input: DesktopTeamCollaborationInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ) => Promise<
    DesktopOperationResult<{
      readonly collaborationRequest: DesktopTeamCollaborationRequest;
    }>
  >;
  readonly executeTeamRun: (
    input: DesktopTeamRunExecuteInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ) => Promise<DesktopOperationResult<{ readonly proof: DesktopTeamRunProof }>>;
  readonly appendTeamRunBudget: (
    input: DesktopTeamRunAppendBudgetInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ) => Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>>;
}

const WORKSPACE_ID_PATTERN = /^workspace_[a-f0-9]{32}$/u;
const PROVIDER_ID_PATTERN = /^provider_[a-f0-9]{32}$/u;
const CONVERSATION_ID_PATTERN = /^conversation_[a-f0-9]{32}$/u;
const MESSAGE_ID_PATTERN = /^message_[a-f0-9]{32}$/u;
const INVOCATION_ID_PATTERN = /^invocation_[a-f0-9]{32}$/u;
const TEAM_RUN_ID_PATTERN = /^teamrun_[a-f0-9]{32}$/u;
const TEAM_NODE_ID_PATTERN = /^teamnode_[a-f0-9]{32}$/u;
const TEAM_REPORT_ID_PATTERN = /^teamrpt_[a-f0-9]{32}$/u;
const COMPOSITION_PROPOSAL_ID_PATTERN = /^proposal_[a-f0-9]{32}$/u;
const COMPONENT_PROPOSAL_ID_PATTERN = /^proposal_[a-f0-9]{32}$/u;
const COMPONENT_OPERATION_ID_PATTERN = /^compop_[a-f0-9]{32}$/u;
const COMPONENT_EFFECT_ID_PATTERN = /^effect_[a-f0-9]{32}$/u;
const COMPONENT_ID_PATTERN = /^[a-z][a-z0-9.-]{2,127}$/u;
const COMPONENT_OPERATION_PATTERN = /^[a-z][a-z0-9_.]{2,63}$/u;
const COMPONENT_VERSION_PATTERN = /^[0-9]+\.[0-9]+\.[0-9]+$/u;
const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9._:-]{8,128}$/u;
const LOGICAL_ID_PATTERN = /^[A-Za-z][A-Za-z0-9._:-]{2,127}$/u;
const ERROR_CODE_PATTERN = /^[a-z][a-z0-9_]{2,95}$/u;
const SHA256_PATTERN = /^[a-f0-9]{64}$/u;
const COMPOSITION_SLOT_IDS = Object.freeze([
  "agent.rail",
  "conversation.transcript",
  "event.agent-log",
  "event.output",
  "knowledge.ebook",
  "mcp.catalog",
  "provider.settings",
  "run.history",
  "sandbox.runtime",
  "settings.center",
  "skills.catalog",
  "source-control",
  "terminal",
  "workspace.brief",
  "workspace.explorer",
] as const);
const COMPOSITION_REQUIRED_SLOTS = new Set([
  "conversation.transcript",
  "settings.center",
] as const);
const COMPOSITION_UNAVAILABLE_SLOTS = new Set([
  "knowledge.ebook",
  "mcp.catalog",
  "sandbox.runtime",
  "skills.catalog",
  "source-control",
  "terminal",
] as const);
const ASSIGNMENT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9._-]{0,127}$/u;
const EMPLOYEE_ROLE_SET = new Set<string>(PERSONAL_EMPLOYEE_IDS);
const SPECIALIST_ROLE_SET = new Set<string>(SPECIALIST_EMPLOYEE_IDS);
const GEARS = new Set(["economy", "standard", "deep", "audit"]);
const DEPTHS = new Set(["disabled", "low", "medium", "high"]);
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/u;

function requireTrustedSender(event: IpcMainInvokeEvent): void {
  const senderUrl = event.senderFrame?.url ?? "";
  if (!isAllowedIpcSender(senderUrl)) {
    throw new Error("ipc_sender_not_allowed");
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  const sortedExpected = [...expected].sort();
  return (
    actual.length === sortedExpected.length &&
    actual.every((key, index) => key === sortedExpected[index])
  );
}

function hasRequiredAndOptionalKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[],
): boolean {
  const keys = Object.keys(value);
  const allowed = new Set([...required, ...optional]);
  return (
    required.every((key) => keys.includes(key)) &&
    keys.every((key) => allowed.has(key))
  );
}

function normalizedName(value: unknown): string | null {
  if (typeof value !== "string" || CONTROL_CHARACTER_PATTERN.test(value))
    return null;
  const normalized = value.trim();
  return normalized.length >= 1 && normalized.length <= 256 ? normalized : null;
}

function invalidInput<T>(): DesktopOperationResult<T> {
  return Object.freeze({
    ok: false,
    error: Object.freeze({ code: "desktop_native_input_invalid" }),
  });
}

function parseOwnerBootstrapInput(
  args: readonly unknown[],
): DesktopOwnerBootstrapInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["displayName"])
  ) {
    return null;
  }
  const displayName = normalizedName(args[0].displayName);
  return displayName === null ? null : Object.freeze({ displayName });
}

function parseWorkspaceCreateInput(
  args: readonly unknown[],
): DesktopWorkspaceCreateInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["name"])
  ) {
    return null;
  }
  const name = normalizedName(args[0].name);
  return name === null ? null : Object.freeze({ name });
}

function parseWorkspaceArchiveInput(
  args: readonly unknown[],
): DesktopWorkspaceArchiveInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["expectedRowVersion", "workspaceId"]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].expectedRowVersion !== "number" ||
    !Number.isInteger(args[0].expectedRowVersion) ||
    args[0].expectedRowVersion < 1 ||
    args[0].expectedRowVersion > 2_147_483_647
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    expectedRowVersion: args[0].expectedRowVersion,
  });
}

function parseWorkspaceIdInput(
  args: readonly unknown[],
): DesktopWorkspaceIdInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["workspaceId"]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId)
  ) {
    return null;
  }
  return Object.freeze({ workspaceId: args[0].workspaceId });
}

function validRevision(value: unknown): value is number {
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= 1 &&
    value <= 2_147_483_647
  );
}

function parseApplicationPreferenceUpdateInput(
  args: readonly unknown[],
): DesktopApplicationPreferenceUpdateInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["density", "expectedRowVersion", "reduceMotion"]) ||
    (args[0].density !== "compact" && args[0].density !== "comfortable") ||
    typeof args[0].reduceMotion !== "boolean" ||
    !validRevision(args[0].expectedRowVersion)
  ) {
    return null;
  }
  return Object.freeze({
    density: args[0].density,
    reduceMotion: args[0].reduceMotion,
    expectedRowVersion: args[0].expectedRowVersion,
  });
}

function parseCompositionProfile(
  value: unknown,
): DesktopWorkspaceCompositionProfileValue | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "appearance",
      "layout",
      "schemaVersion",
      "slots",
      "template",
    ]) ||
    value.schemaVersion !== 1 ||
    !isRecord(value.template) ||
    !hasExactKeys(value.template, ["id", "version"]) ||
    value.template.id !== "standard-workbench" ||
    value.template.version !== 1 ||
    !isRecord(value.appearance) ||
    !hasExactKeys(value.appearance, ["density", "quietChrome"]) ||
    (value.appearance.density !== "inherit" &&
      value.appearance.density !== "compact" &&
      value.appearance.density !== "comfortable") ||
    typeof value.appearance.quietChrome !== "boolean" ||
    !isRecord(value.layout) ||
    !hasExactKeys(value.layout, [
      "agentPanel",
      "bottomPanel",
      "focusMode",
      "sidebar",
    ]) ||
    (value.layout.agentPanel !== "open" &&
      value.layout.agentPanel !== "closed") ||
    (value.layout.bottomPanel !== "hidden" &&
      value.layout.bottomPanel !== "output" &&
      value.layout.bottomPanel !== "agent-log") ||
    typeof value.layout.focusMode !== "boolean" ||
    (value.layout.sidebar !== "explorer" &&
      value.layout.sidebar !== "run" &&
      value.layout.sidebar !== "blackboard" &&
      value.layout.sidebar !== "hidden") ||
    !isRecord(value.slots) ||
    !hasExactKeys(value.slots, COMPOSITION_SLOT_IDS) ||
    COMPOSITION_SLOT_IDS.some(
      (slotId) =>
        typeof (value.slots as Record<string, unknown>)[slotId] !== "boolean",
    )
  ) {
    return null;
  }
  const rawSlots = value.slots as Record<
    (typeof COMPOSITION_SLOT_IDS)[number],
    boolean
  >;
  if (
    [...COMPOSITION_REQUIRED_SLOTS].some((slotId) => !rawSlots[slotId]) ||
    [...COMPOSITION_UNAVAILABLE_SLOTS].some((slotId) => rawSlots[slotId]) ||
    (!rawSlots["agent.rail"] && value.layout.agentPanel !== "closed") ||
    (!rawSlots["workspace.explorer"] && value.layout.sidebar === "explorer") ||
    (!rawSlots["run.history"] && value.layout.sidebar === "run") ||
    (!rawSlots["workspace.brief"] && value.layout.sidebar === "blackboard") ||
    (!rawSlots["event.output"] && value.layout.bottomPanel === "output") ||
    (!rawSlots["event.agent-log"] && value.layout.bottomPanel === "agent-log")
  ) {
    return null;
  }
  return Object.freeze({
    schemaVersion: 1,
    template: Object.freeze({ id: "standard-workbench", version: 1 }),
    appearance: Object.freeze({
      density: value.appearance.density,
      quietChrome: value.appearance.quietChrome,
    }),
    layout: Object.freeze({
      agentPanel: value.layout.agentPanel,
      bottomPanel: value.layout.bottomPanel,
      focusMode: value.layout.focusMode,
      sidebar: value.layout.sidebar,
    }),
    slots: Object.freeze(
      Object.fromEntries(
        COMPOSITION_SLOT_IDS.map((slotId) => [slotId, rawSlots[slotId]]),
      ) as DesktopWorkspaceCompositionProfileValue["slots"],
    ),
  });
}

function parseCompositionOwnerProposalInput(
  args: readonly unknown[],
): DesktopWorkspaceCompositionOwnerProposalInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "desiredProfile",
      "expectedProfileSha256",
      "expectedRevision",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    !validRevision(args[0].expectedRevision) ||
    typeof args[0].expectedProfileSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].expectedProfileSha256)
  ) {
    return null;
  }
  const desiredProfile = parseCompositionProfile(args[0].desiredProfile);
  return desiredProfile === null
    ? null
    : Object.freeze({
        workspaceId: args[0].workspaceId,
        expectedRevision: args[0].expectedRevision,
        expectedProfileSha256: args[0].expectedProfileSha256,
        desiredProfile,
      });
}

function parseCompositionAssistantProposalInput(
  args: readonly unknown[],
): DesktopWorkspaceCompositionAssistantProposalInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "expectedProfileSha256",
      "expectedRevision",
      "messageId",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    !validRevision(args[0].expectedRevision) ||
    typeof args[0].expectedProfileSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].expectedProfileSha256) ||
    typeof args[0].messageId !== "string" ||
    !MESSAGE_ID_PATTERN.test(args[0].messageId)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    expectedRevision: args[0].expectedRevision,
    expectedProfileSha256: args[0].expectedProfileSha256,
    messageId: args[0].messageId,
  });
}

function parseCompositionRollbackProposalInput(
  args: readonly unknown[],
): DesktopWorkspaceCompositionRollbackProposalInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "expectedProfileSha256",
      "expectedRevision",
      "targetRevision",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    !validRevision(args[0].expectedRevision) ||
    typeof args[0].expectedProfileSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].expectedProfileSha256) ||
    !validRevision(args[0].targetRevision)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    expectedRevision: args[0].expectedRevision,
    expectedProfileSha256: args[0].expectedProfileSha256,
    targetRevision: args[0].targetRevision,
  });
}

function parseCompositionDecisionInput(
  args: readonly unknown[],
): DesktopWorkspaceCompositionDecisionInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "decision",
      "proposalId",
      "requestSha256",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].proposalId !== "string" ||
    !COMPOSITION_PROPOSAL_ID_PATTERN.test(args[0].proposalId) ||
    typeof args[0].requestSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].requestSha256) ||
    (args[0].decision !== "approve" && args[0].decision !== "reject")
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    proposalId: args[0].proposalId,
    requestSha256: args[0].requestSha256,
    decision: args[0].decision,
  });
}

const COMPONENT_LIFECYCLE_ACTIONS = new Set([
  "install",
  "bind",
  "activate",
  "disable",
  "upgrade",
  "rollback",
  "revoke",
  "uninstall",
]);
const COMPONENT_OPERATIONS = new Set([
  "ui.render",
  "skill.resolve",
  "mcp.call",
  "sandbox.run",
  "local_adapter.open",
]);
const MCP_TOOLS = new Set([
  "omnibase_files_list",
  "omnibase_files_read",
  "omnibase_files_hash",
  "omnibase_text_search",
]);

function validBoundedInteger(
  value: unknown,
  minimum: number,
  maximum: number,
): value is number {
  return (
    typeof value === "number" &&
    Number.isSafeInteger(value) &&
    value >= minimum &&
    value <= maximum
  );
}

function validOptionalLogicalId(value: unknown): value is string | undefined {
  return (
    value === undefined ||
    (typeof value === "string" && LOGICAL_ID_PATTERN.test(value))
  );
}

function parseComponentGrant(value: unknown) {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "action",
      "expiresInSeconds",
      "logicalResourceId",
      "logicalServiceId",
      "maximumBytesIn",
      "maximumBytesOut",
      "maximumCostUnits",
      "maximumInvocations",
      "maximumTokens",
      "maximumWallTimeMs",
      "resourceVersion",
    ]) ||
    typeof value.action !== "string" ||
    !COMPONENT_OPERATION_PATTERN.test(value.action) ||
    (value.logicalResourceId !== null &&
      (typeof value.logicalResourceId !== "string" ||
        !LOGICAL_ID_PATTERN.test(value.logicalResourceId))) ||
    (value.logicalServiceId !== null &&
      (typeof value.logicalServiceId !== "string" ||
        !LOGICAL_ID_PATTERN.test(value.logicalServiceId))) ||
    (value.resourceVersion !== null &&
      !validBoundedInteger(value.resourceVersion, 1, 2_147_483_647)) ||
    !validBoundedInteger(value.expiresInSeconds, 1, 86_400) ||
    !validBoundedInteger(value.maximumInvocations, 1, 10_000) ||
    !validBoundedInteger(value.maximumBytesIn, 0, 1_073_741_824) ||
    !validBoundedInteger(value.maximumBytesOut, 0, 1_073_741_824) ||
    !validBoundedInteger(value.maximumTokens, 0, 10_000_000) ||
    !validBoundedInteger(value.maximumWallTimeMs, 1, 86_400_000) ||
    !validBoundedInteger(value.maximumCostUnits, 0, 1_000_000)
  ) {
    return null;
  }
  return Object.freeze({
    action: value.action,
    logicalResourceId: value.logicalResourceId,
    resourceVersion: value.resourceVersion,
    logicalServiceId: value.logicalServiceId,
    expiresInSeconds: value.expiresInSeconds,
    maximumInvocations: value.maximumInvocations,
    maximumBytesIn: value.maximumBytesIn,
    maximumBytesOut: value.maximumBytesOut,
    maximumTokens: value.maximumTokens,
    maximumWallTimeMs: value.maximumWallTimeMs,
    maximumCostUnits: value.maximumCostUnits,
  });
}

function parseComponentJson(
  value: unknown,
  depth = 0,
): DesktopWorkspaceComponentProposeInput["desiredConfiguration"] | undefined {
  if (depth > 16) return undefined;
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") {
    return value.length <= 65_536 && !value.includes("\0") ? value : undefined;
  }
  if (typeof value === "number")
    return Number.isFinite(value) ? value : undefined;
  if (Array.isArray(value)) {
    if (value.length > 1024) return undefined;
    const parsed = value.map((item) => parseComponentJson(item, depth + 1));
    return parsed.some((item) => item === undefined)
      ? undefined
      : (Object.freeze(
          parsed,
        ) as DesktopWorkspaceComponentProposeInput["desiredConfiguration"]);
  }
  if (!isRecord(value) || Object.keys(value).length > 1024) return undefined;
  const result: Record<
    string,
    Exclude<ReturnType<typeof parseComponentJson>, undefined>
  > = {};
  for (const [key, item] of Object.entries(value)) {
    if (
      key.length < 1 ||
      key.length > 128 ||
      key === "__proto__" ||
      key === "prototype" ||
      key === "constructor"
    ) {
      return undefined;
    }
    const parsed = parseComponentJson(item, depth + 1);
    if (parsed === undefined) return undefined;
    result[key] = parsed;
  }
  return Object.freeze(result);
}

function parseComponentSlotBinding(value: unknown) {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "bindingKey",
      "configuration",
      "orderIndex",
      "slotId",
    ]) ||
    typeof value.slotId !== "string" ||
    !LOGICAL_ID_PATTERN.test(value.slotId) ||
    typeof value.bindingKey !== "string" ||
    !LOGICAL_ID_PATTERN.test(value.bindingKey) ||
    !validBoundedInteger(value.orderIndex, 0, 10_000)
  ) {
    return null;
  }
  const configuration = parseComponentJson(value.configuration);
  return configuration === undefined
    ? null
    : Object.freeze({
        slotId: value.slotId,
        bindingKey: value.bindingKey,
        orderIndex: value.orderIndex,
        configuration,
      });
}

function parseComponentDependency(value: unknown) {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "componentId",
      "policyManifestSha256",
      "manifestSha256",
      "packageSha256",
      "version",
    ]) ||
    typeof value.componentId !== "string" ||
    !COMPONENT_ID_PATTERN.test(value.componentId) ||
    typeof value.version !== "string" ||
    !COMPONENT_VERSION_PATTERN.test(value.version) ||
    typeof value.policyManifestSha256 !== "string" ||
    !SHA256_PATTERN.test(value.policyManifestSha256) ||
    typeof value.manifestSha256 !== "string" ||
    !SHA256_PATTERN.test(value.manifestSha256) ||
    typeof value.packageSha256 !== "string" ||
    !SHA256_PATTERN.test(value.packageSha256)
  ) {
    return null;
  }
  return Object.freeze({
    componentId: value.componentId,
    version: value.version,
    policyManifestSha256: value.policyManifestSha256,
    manifestSha256: value.manifestSha256,
    packageSha256: value.packageSha256,
  });
}

function parseWorkspaceComponentProposeInput(
  args: readonly unknown[],
): DesktopWorkspaceComponentProposeInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasRequiredAndOptionalKeys(
      args[0],
      [
        "changeKind",
        "componentId",
        "dependencyGraph",
        "desiredConfiguration",
        "desiredSlotBindings",
        "expectedRevision",
        "idempotencyKey",
        "requestedGrants",
        "targetVersion",
        "workspaceId",
      ],
      [],
    ) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].componentId !== "string" ||
    !COMPONENT_ID_PATTERN.test(args[0].componentId) ||
    typeof args[0].targetVersion !== "string" ||
    !COMPONENT_VERSION_PATTERN.test(args[0].targetVersion) ||
    typeof args[0].changeKind !== "string" ||
    !COMPONENT_LIFECYCLE_ACTIONS.has(args[0].changeKind) ||
    !validRevision(args[0].expectedRevision) ||
    !Array.isArray(args[0].requestedGrants) ||
    args[0].requestedGrants.length > 32 ||
    !Array.isArray(args[0].desiredSlotBindings) ||
    args[0].desiredSlotBindings.length > 64 ||
    !Array.isArray(args[0].dependencyGraph) ||
    args[0].dependencyGraph.length > 64 ||
    typeof args[0].idempotencyKey !== "string" ||
    !IDEMPOTENCY_KEY_PATTERN.test(args[0].idempotencyKey)
  ) {
    return null;
  }
  const grants = args[0].requestedGrants.map(parseComponentGrant);
  const desiredConfiguration = parseComponentJson(args[0].desiredConfiguration);
  const desiredSlotBindings = args[0].desiredSlotBindings.map(
    parseComponentSlotBinding,
  );
  const dependencyGraph = args[0].dependencyGraph.map(parseComponentDependency);
  if (
    grants.some((grant) => grant === null) ||
    desiredConfiguration === undefined ||
    desiredSlotBindings.some((binding) => binding === null) ||
    dependencyGraph.some((dependency) => dependency === null)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    componentId: args[0].componentId,
    targetVersion: args[0].targetVersion,
    changeKind: args[0]
      .changeKind as DesktopWorkspaceComponentProposeInput["changeKind"],
    expectedRevision: args[0].expectedRevision,
    requestedGrants: Object.freeze(
      grants as DesktopWorkspaceComponentProposeInput["requestedGrants"],
    ),
    desiredConfiguration,
    desiredSlotBindings: Object.freeze(
      desiredSlotBindings as DesktopWorkspaceComponentProposeInput["desiredSlotBindings"],
    ),
    dependencyGraph: Object.freeze(
      dependencyGraph as DesktopWorkspaceComponentProposeInput["dependencyGraph"],
    ),
    idempotencyKey: args[0].idempotencyKey,
  });
}

function parseWorkspaceComponentAssistantProposalInput(
  args: readonly unknown[],
): DesktopWorkspaceComponentAssistantProposalInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["idempotencyKey", "messageId", "workspaceId"]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].messageId !== "string" ||
    !MESSAGE_ID_PATTERN.test(args[0].messageId) ||
    typeof args[0].idempotencyKey !== "string" ||
    !IDEMPOTENCY_KEY_PATTERN.test(args[0].idempotencyKey)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    messageId: args[0].messageId,
    idempotencyKey: args[0].idempotencyKey,
  });
}

function parseWorkspaceComponentAssistantPackageImportInput(
  args: readonly unknown[],
): DesktopWorkspaceComponentAssistantPackageImportInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "conversationId",
      "manifestSha256",
      "messageId",
      "packageJson",
      "packageSha256",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].conversationId !== "string" ||
    !CONVERSATION_ID_PATTERN.test(args[0].conversationId) ||
    typeof args[0].messageId !== "string" ||
    !MESSAGE_ID_PATTERN.test(args[0].messageId) ||
    typeof args[0].packageJson !== "string" ||
    Buffer.byteLength(args[0].packageJson, "utf8") < 2 ||
    Buffer.byteLength(args[0].packageJson, "utf8") > 256 * 1024 ||
    args[0].packageJson.includes("\0") ||
    typeof args[0].manifestSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].manifestSha256) ||
    typeof args[0].packageSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].packageSha256)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    conversationId: args[0].conversationId,
    messageId: args[0].messageId,
    packageJson: args[0].packageJson,
    manifestSha256: args[0].manifestSha256,
    packageSha256: args[0].packageSha256,
  });
}

function parseWorkspaceComponentDecisionInput(
  args: readonly unknown[],
): DesktopWorkspaceComponentDecisionInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "decision",
      "proposalId",
      "requestSha256",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].proposalId !== "string" ||
    !COMPONENT_PROPOSAL_ID_PATTERN.test(args[0].proposalId) ||
    (args[0].decision !== "approve" && args[0].decision !== "reject") ||
    typeof args[0].requestSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].requestSha256)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    proposalId: args[0].proposalId,
    decision: args[0].decision,
    requestSha256: args[0].requestSha256,
  });
}

function parseWorkspaceComponentActionInput(
  args: readonly unknown[],
): DesktopWorkspaceComponentActionInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "action",
      "componentId",
      "expectedRevision",
      "idempotencyKey",
      "manifestSha256",
      "packageSha256",
      "proposalId",
      "requestSha256",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].componentId !== "string" ||
    !COMPONENT_ID_PATTERN.test(args[0].componentId) ||
    typeof args[0].action !== "string" ||
    !COMPONENT_LIFECYCLE_ACTIONS.has(args[0].action) ||
    typeof args[0].proposalId !== "string" ||
    !COMPONENT_PROPOSAL_ID_PATTERN.test(args[0].proposalId) ||
    typeof args[0].requestSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].requestSha256) ||
    !validRevision(args[0].expectedRevision) ||
    typeof args[0].manifestSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].manifestSha256) ||
    typeof args[0].packageSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].packageSha256) ||
    typeof args[0].idempotencyKey !== "string" ||
    !IDEMPOTENCY_KEY_PATTERN.test(args[0].idempotencyKey)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    componentId: args[0].componentId,
    action: args[0].action as DesktopWorkspaceComponentActionInput["action"],
    proposalId: args[0].proposalId,
    requestSha256: args[0].requestSha256,
    expectedRevision: args[0].expectedRevision,
    manifestSha256: args[0].manifestSha256,
    packageSha256: args[0].packageSha256,
    idempotencyKey: args[0].idempotencyKey,
  });
}

function validComponentInvokeBase(value: Record<string, unknown>): boolean {
  return (
    typeof value.workspaceId === "string" &&
    WORKSPACE_ID_PATTERN.test(value.workspaceId) &&
    typeof value.componentId === "string" &&
    COMPONENT_ID_PATTERN.test(value.componentId) &&
    typeof value.operation === "string" &&
    COMPONENT_OPERATIONS.has(value.operation) &&
    validRevision(value.expectedRevision) &&
    validRevision(value.bindingGeneration) &&
    typeof value.manifestSha256 === "string" &&
    SHA256_PATTERN.test(value.manifestSha256) &&
    typeof value.packageSha256 === "string" &&
    SHA256_PATTERN.test(value.packageSha256) &&
    typeof value.idempotencyKey === "string" &&
    IDEMPOTENCY_KEY_PATTERN.test(value.idempotencyKey) &&
    validOptionalLogicalId(value.logicalResourceId) &&
    (value.resourceVersion === undefined ||
      validRevision(value.resourceVersion)) &&
    validOptionalLogicalId(value.logicalServiceId) &&
    validBoundedInteger(value.bytesOutReserved, 0, 4_194_304) &&
    validBoundedInteger(value.tokensReserved, 0, 131_072) &&
    validBoundedInteger(value.wallTimeMs, 1, 600_000) &&
    validBoundedInteger(value.costUnits, 0, 1_000)
  );
}

function parseWorkspaceComponentInvokeArguments(
  operation: string,
  value: unknown,
): DesktopWorkspaceComponentInvokeInput["arguments"] | null {
  if (!isRecord(value)) return null;
  if (operation === "ui.render") {
    return hasExactKeys(value, ["slotId", "viewId"]) &&
      typeof value.slotId === "string" &&
      LOGICAL_ID_PATTERN.test(value.slotId) &&
      typeof value.viewId === "string" &&
      LOGICAL_ID_PATTERN.test(value.viewId)
      ? Object.freeze({ slotId: value.slotId, viewId: value.viewId })
      : null;
  }
  if (operation === "skill.resolve") {
    return hasExactKeys(value, ["skillId", "task"]) &&
      typeof value.skillId === "string" &&
      COMPONENT_ID_PATTERN.test(value.skillId) &&
      typeof value.task === "string" &&
      value.task.length >= 1 &&
      value.task.length <= 32_768 &&
      !CONTROL_CHARACTER_PATTERN.test(value.task)
      ? Object.freeze({ skillId: value.skillId, task: value.task })
      : null;
  }
  if (operation === "mcp.call") {
    const toolName = value.toolName;
    if (
      typeof toolName !== "string" ||
      !MCP_TOOLS.has(toolName) ||
      (toolName === "omnibase_files_list"
        ? !hasRequiredAndOptionalKeys(value, ["toolName"], ["path"]) ||
          (value.path !== undefined && !validLogicalPathValue(value.path, true))
        : toolName === "omnibase_text_search"
          ? !hasExactKeys(value, ["path", "query", "toolName"]) ||
            !validLogicalPathValue(value.path, false) ||
            typeof value.query !== "string" ||
            value.query.length < 1 ||
            value.query.length > 256 ||
            CONTROL_CHARACTER_PATTERN.test(value.query)
          : !hasExactKeys(value, ["path", "toolName"]) ||
            !validLogicalPathValue(value.path, false))
    )
      return null;
    return Object.freeze({
      toolName,
      ...(value.path === undefined ? {} : { path: value.path }),
      ...(value.query === undefined ? {} : { query: value.query }),
    }) as DesktopWorkspaceComponentInvokeInput["arguments"];
  }
  if (operation === "sandbox.run") {
    if (
      !hasExactKeys(value, ["inputArtifactIds", "workloadId"]) ||
      typeof value.workloadId !== "string" ||
      !LOGICAL_ID_PATTERN.test(value.workloadId) ||
      !Array.isArray(value.inputArtifactIds) ||
      value.inputArtifactIds.length > 64 ||
      !value.inputArtifactIds.every(
        (item) => typeof item === "string" && LOGICAL_ID_PATTERN.test(item),
      )
    )
      return null;
    return Object.freeze({
      workloadId: value.workloadId,
      inputArtifactIds: Object.freeze([
        ...value.inputArtifactIds,
      ]) as readonly string[],
    });
  }
  if (operation === "local_adapter.open") {
    if (
      !hasRequiredAndOptionalKeys(
        value,
        ["adapterId", "destination"],
        ["logicalId"],
      ) ||
      value.adapterId !== "knowledge.ebook" ||
      (value.destination !== "workspace" &&
        value.destination !== "phase" &&
        value.destination !== "document") ||
      (value.logicalId !== undefined &&
        (typeof value.logicalId !== "string" ||
          !LOGICAL_ID_PATTERN.test(value.logicalId)))
    )
      return null;
    return Object.freeze({
      adapterId: "knowledge.ebook" as const,
      destination: value.destination,
      ...(value.logicalId === undefined ? {} : { logicalId: value.logicalId }),
    });
  }
  return null;
}

function parseWorkspaceComponentInvokeInput(
  args: readonly unknown[],
): DesktopWorkspaceComponentInvokeInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasRequiredAndOptionalKeys(
      args[0],
      [
        "arguments",
        "bindingGeneration",
        "bytesOutReserved",
        "componentId",
        "costUnits",
        "expectedRevision",
        "idempotencyKey",
        "manifestSha256",
        "operation",
        "packageSha256",
        "tokensReserved",
        "wallTimeMs",
        "workspaceId",
      ],
      ["logicalResourceId", "logicalServiceId", "resourceVersion"],
    ) ||
    !validComponentInvokeBase(args[0])
  )
    return null;
  const parsedArguments = parseWorkspaceComponentInvokeArguments(
    args[0].operation as string,
    args[0].arguments,
  );
  if (parsedArguments === null) return null;
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    componentId: args[0].componentId,
    operation: args[0].operation,
    expectedRevision: args[0].expectedRevision,
    bindingGeneration: args[0].bindingGeneration,
    manifestSha256: args[0].manifestSha256,
    packageSha256: args[0].packageSha256,
    idempotencyKey: args[0].idempotencyKey,
    ...(args[0].logicalResourceId === undefined
      ? {}
      : { logicalResourceId: args[0].logicalResourceId }),
    ...(args[0].resourceVersion === undefined
      ? {}
      : { resourceVersion: args[0].resourceVersion }),
    ...(args[0].logicalServiceId === undefined
      ? {}
      : { logicalServiceId: args[0].logicalServiceId }),
    bytesOutReserved: args[0].bytesOutReserved,
    tokensReserved: args[0].tokensReserved,
    wallTimeMs: args[0].wallTimeMs,
    costUnits: args[0].costUnits,
    arguments: parsedArguments,
  }) as DesktopWorkspaceComponentInvokeInput;
}

function parseWorkspaceComponentEmergencyStopInput(
  args: readonly unknown[],
): DesktopWorkspaceComponentEmergencyStopInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["idempotencyKey", "reasonCode", "workspaceId"]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].idempotencyKey !== "string" ||
    !IDEMPOTENCY_KEY_PATTERN.test(args[0].idempotencyKey) ||
    typeof args[0].reasonCode !== "string" ||
    !ERROR_CODE_PATTERN.test(args[0].reasonCode)
  )
    return null;
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    idempotencyKey: args[0].idempotencyKey,
    reasonCode: args[0].reasonCode,
  });
}

function parseWorkspaceComponentReconcileInput(
  args: readonly unknown[],
): DesktopWorkspaceComponentReconcileInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "effectId",
      "evidenceSha256",
      "operationId",
      "outcome",
      "requestSha256",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].operationId !== "string" ||
    !COMPONENT_OPERATION_ID_PATTERN.test(args[0].operationId) ||
    typeof args[0].effectId !== "string" ||
    !COMPONENT_EFFECT_ID_PATTERN.test(args[0].effectId) ||
    typeof args[0].requestSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].requestSha256) ||
    (args[0].outcome !== "succeeded" && args[0].outcome !== "failed") ||
    typeof args[0].evidenceSha256 !== "string" ||
    !SHA256_PATTERN.test(args[0].evidenceSha256)
  )
    return null;
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    operationId: args[0].operationId,
    effectId: args[0].effectId,
    requestSha256: args[0].requestSha256,
    outcome: args[0].outcome,
    evidenceSha256: args[0].evidenceSha256,
  });
}

function validAuthorizationGeneration(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === "number" && value >= 1;
}

function validLogicalPathValue(
  value: unknown,
  allowRoot: boolean,
): value is string {
  return (
    typeof value === "string" &&
    (allowRoot || value.length > 0) &&
    value.length <= 4_096 &&
    !CONTROL_CHARACTER_PATTERN.test(value)
  );
}

function parseWorkspaceFileReleaseInput(
  args: readonly unknown[],
): DesktopWorkspaceFileReleaseInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["authorizationGeneration", "workspaceId"]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    !validAuthorizationGeneration(args[0].authorizationGeneration)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    authorizationGeneration: args[0].authorizationGeneration,
  });
}

function parseWorkspaceFileListInput(
  args: readonly unknown[],
): DesktopWorkspaceFileListInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "authorizationGeneration",
      "directoryPath",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    !validAuthorizationGeneration(args[0].authorizationGeneration) ||
    !validLogicalPathValue(args[0].directoryPath, true)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    authorizationGeneration: args[0].authorizationGeneration,
    directoryPath: args[0].directoryPath,
  });
}

function parseWorkspaceFileReadInput(
  args: readonly unknown[],
): DesktopWorkspaceFileReadInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "authorizationGeneration",
      "path",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    !validAuthorizationGeneration(args[0].authorizationGeneration) ||
    !validLogicalPathValue(args[0].path, false)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    authorizationGeneration: args[0].authorizationGeneration,
    path: args[0].path,
  });
}

function parseProviderIdInput(
  args: readonly unknown[],
): DesktopProviderIdInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["providerId"]) ||
    typeof args[0].providerId !== "string" ||
    !PROVIDER_ID_PATTERN.test(args[0].providerId)
  ) {
    return null;
  }
  return Object.freeze({ providerId: args[0].providerId });
}

function parseProviderUpsertInput(
  args: readonly unknown[],
): DesktopProviderUpsertInput | null {
  if (args.length !== 1 || !isRecord(args[0])) return null;
  const keys = Object.keys(args[0]);
  const allowed = new Set([
    "allowLoopbackHttp",
    "apiKey",
    "baseUrl",
    "displayName",
    "gear",
    "id",
    "isDefault",
    "isEnabled",
    "modelName",
    "thinkingDepth",
    "timeoutSeconds",
  ]);
  if (keys.some((key) => !allowed.has(key))) return null;
  const displayName = normalizedName(args[0].displayName);
  const modelName = normalizedName(args[0].modelName);
  if (
    displayName === null ||
    modelName === null ||
    typeof args[0].baseUrl !== "string" ||
    args[0].baseUrl.length < 8 ||
    args[0].baseUrl.length > 2048 ||
    typeof args[0].gear !== "string" ||
    !GEARS.has(args[0].gear) ||
    typeof args[0].thinkingDepth !== "string" ||
    !DEPTHS.has(args[0].thinkingDepth) ||
    typeof args[0].timeoutSeconds !== "number" ||
    !Number.isInteger(args[0].timeoutSeconds) ||
    args[0].timeoutSeconds < 5 ||
    args[0].timeoutSeconds > 120 ||
    typeof args[0].allowLoopbackHttp !== "boolean" ||
    typeof args[0].isDefault !== "boolean" ||
    typeof args[0].isEnabled !== "boolean"
  ) {
    return null;
  }
  if (
    args[0].id !== undefined &&
    (typeof args[0].id !== "string" || !PROVIDER_ID_PATTERN.test(args[0].id))
  ) {
    return null;
  }
  if (
    args[0].apiKey !== undefined &&
    (typeof args[0].apiKey !== "string" ||
      args[0].apiKey.length < 1 ||
      args[0].apiKey.length > 512 ||
      CONTROL_CHARACTER_PATTERN.test(args[0].apiKey))
  ) {
    return null;
  }
  return Object.freeze({
    ...(args[0].id === undefined ? {} : { id: args[0].id }),
    displayName,
    baseUrl: args[0].baseUrl.trim(),
    ...(args[0].apiKey === undefined ? {} : { apiKey: args[0].apiKey }),
    modelName,
    gear: args[0].gear as DesktopProviderUpsertInput["gear"],
    thinkingDepth: args[0]
      .thinkingDepth as DesktopProviderUpsertInput["thinkingDepth"],
    timeoutSeconds: args[0].timeoutSeconds,
    allowLoopbackHttp: args[0].allowLoopbackHttp,
    isDefault: args[0].isDefault,
    isEnabled: args[0].isEnabled,
  });
}

function parseConversationCreateInput(
  args: readonly unknown[],
): DesktopConversationCreateInput | null {
  if (args.length !== 1 || !isRecord(args[0])) return null;
  if (
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId)
  ) {
    return null;
  }
  if (args[0].title !== undefined) {
    const title = normalizedName(args[0].title);
    if (title === null || !hasExactKeys(args[0], ["title", "workspaceId"]))
      return null;
    return Object.freeze({ workspaceId: args[0].workspaceId, title });
  }
  if (!hasExactKeys(args[0], ["workspaceId"])) return null;
  return Object.freeze({ workspaceId: args[0].workspaceId });
}

function parseConversationGetInput(
  args: readonly unknown[],
): DesktopConversationGetInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["conversationId", "workspaceId"]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].conversationId !== "string" ||
    !CONVERSATION_ID_PATTERN.test(args[0].conversationId)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    conversationId: args[0].conversationId,
  });
}

function parseConversationArchiveInput(
  args: readonly unknown[],
): DesktopConversationArchiveInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "conversationId",
      "expectedRowVersion",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].conversationId !== "string" ||
    !CONVERSATION_ID_PATTERN.test(args[0].conversationId) ||
    typeof args[0].expectedRowVersion !== "number" ||
    !Number.isInteger(args[0].expectedRowVersion) ||
    args[0].expectedRowVersion < 1
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    conversationId: args[0].conversationId,
    expectedRowVersion: args[0].expectedRowVersion,
  });
}

function parseConversationSendInput(
  args: readonly unknown[],
): DesktopConversationSendInput | null {
  if (args.length !== 1 || !isRecord(args[0])) return null;
  if (
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].conversationId !== "string" ||
    !CONVERSATION_ID_PATTERN.test(args[0].conversationId) ||
    typeof args[0].content !== "string" ||
    args[0].content.length > 16_384 ||
    CONTROL_CHARACTER_PATTERN.test(args[0].content)
  ) {
    return null;
  }
  const allowed = new Set([
    "content",
    "conversationId",
    "providerId",
    "retryOfMessageId",
    "sendEpoch",
    "workspaceId",
  ]);
  if (Object.keys(args[0]).some((key) => !allowed.has(key))) return null;
  if (
    args[0].providerId !== undefined &&
    (typeof args[0].providerId !== "string" ||
      !PROVIDER_ID_PATTERN.test(args[0].providerId))
  ) {
    return null;
  }
  if (
    args[0].retryOfMessageId !== undefined &&
    (typeof args[0].retryOfMessageId !== "string" ||
      !MESSAGE_ID_PATTERN.test(args[0].retryOfMessageId))
  ) {
    return null;
  }
  if (
    args[0].sendEpoch !== undefined &&
    (typeof args[0].sendEpoch !== "number" ||
      !Number.isSafeInteger(args[0].sendEpoch) ||
      args[0].sendEpoch < 0)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    conversationId: args[0].conversationId,
    content: args[0].content,
    ...(args[0].providerId === undefined
      ? {}
      : { providerId: args[0].providerId }),
    ...(args[0].retryOfMessageId === undefined
      ? {}
      : { retryOfMessageId: args[0].retryOfMessageId }),
    ...(args[0].sendEpoch === undefined
      ? {}
      : { sendEpoch: args[0].sendEpoch }),
  });
}

function parseConversationCancelInput(
  args: readonly unknown[],
): DesktopConversationCancelInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["invocationId"]) ||
    typeof args[0].invocationId !== "string" ||
    !INVOCATION_ID_PATTERN.test(args[0].invocationId)
  ) {
    return null;
  }
  return Object.freeze({ invocationId: args[0].invocationId });
}

function parseAgentRoleIdInput(
  args: readonly unknown[],
): DesktopAgentRoleIdInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["roleId", "workspaceId"]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].roleId !== "string" ||
    !EMPLOYEE_ROLE_SET.has(args[0].roleId)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    roleId: args[0].roleId as PersonalEmployeeId,
  });
}

function parseAgentRoleUpdateInput(
  args: readonly unknown[],
): DesktopAgentRoleUpdateInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "expectedRowVersion",
      "gear",
      "modelNameOverride",
      "providerId",
      "roleId",
      "thinkingDepth",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].roleId !== "string" ||
    !EMPLOYEE_ROLE_SET.has(args[0].roleId) ||
    (args[0].providerId !== null &&
      (typeof args[0].providerId !== "string" ||
        !PROVIDER_ID_PATTERN.test(args[0].providerId))) ||
    (args[0].modelNameOverride !== null &&
      (typeof args[0].modelNameOverride !== "string" ||
        args[0].modelNameOverride.length < 1 ||
        args[0].modelNameOverride.length > 256)) ||
    typeof args[0].gear !== "string" ||
    !GEARS.has(args[0].gear) ||
    typeof args[0].thinkingDepth !== "string" ||
    !DEPTHS.has(args[0].thinkingDepth) ||
    typeof args[0].expectedRowVersion !== "number" ||
    !Number.isInteger(args[0].expectedRowVersion) ||
    args[0].expectedRowVersion < 1 ||
    args[0].expectedRowVersion > 2_147_483_647
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    roleId: args[0].roleId as PersonalEmployeeId,
    providerId: args[0].providerId,
    modelNameOverride: args[0].modelNameOverride,
    gear: args[0].gear as DesktopAgentRoleUpdateInput["gear"],
    thinkingDepth: args[0]
      .thinkingDepth as DesktopAgentRoleUpdateInput["thinkingDepth"],
    expectedRowVersion: args[0].expectedRowVersion,
  });
}

function parseTeamRunBudget(value: unknown): TeamRunBudget | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "maximumConcurrentCalls",
      "maximumInputCharacters",
      "maximumOutputCharacters",
      "maximumProviderCalls",
      "maximumWallTimeMs",
    ])
  ) {
    return null;
  }
  const maximumProviderCalls = value.maximumProviderCalls;
  const maximumWallTimeMs = value.maximumWallTimeMs;
  const maximumConcurrentCalls = value.maximumConcurrentCalls;
  const maximumInputCharacters = value.maximumInputCharacters;
  const maximumOutputCharacters = value.maximumOutputCharacters;
  if (
    typeof maximumProviderCalls !== "number" ||
    !Number.isInteger(maximumProviderCalls) ||
    maximumProviderCalls < 1 ||
    maximumProviderCalls > 128 ||
    typeof maximumWallTimeMs !== "number" ||
    !Number.isInteger(maximumWallTimeMs) ||
    maximumWallTimeMs < 1000 ||
    maximumWallTimeMs > 3_600_000 ||
    typeof maximumConcurrentCalls !== "number" ||
    !Number.isInteger(maximumConcurrentCalls) ||
    maximumConcurrentCalls < 1 ||
    maximumConcurrentCalls > 9 ||
    typeof maximumInputCharacters !== "number" ||
    !Number.isInteger(maximumInputCharacters) ||
    maximumInputCharacters < 1 ||
    maximumInputCharacters > 131_072 ||
    typeof maximumOutputCharacters !== "number" ||
    !Number.isInteger(maximumOutputCharacters) ||
    maximumOutputCharacters < 1 ||
    maximumOutputCharacters > 131_072
  ) {
    return null;
  }
  return Object.freeze({
    maximumProviderCalls,
    maximumWallTimeMs,
    maximumConcurrentCalls,
    maximumInputCharacters,
    maximumOutputCharacters,
  });
}

function parseTeamRunStartInput(
  args: readonly unknown[],
): DesktopTeamRunStartInput | null {
  if (args.length !== 1 || !isRecord(args[0])) return null;
  const keys = Object.keys(args[0]).sort();
  const withoutAllow = [
    "budget",
    "conversationId",
    "task",
    "teamMode",
    "workspaceId",
  ];
  const withAllow = [
    "allowedSpecialistRoleIds",
    "budget",
    "conversationId",
    "task",
    "teamMode",
    "workspaceId",
  ];
  if (
    !(
      keys.length === withoutAllow.length &&
      keys.every((key, index) => key === withoutAllow[index])
    ) &&
    !(
      keys.length === withAllow.length &&
      keys.every((key, index) => key === withAllow[index])
    )
  ) {
    return null;
  }
  const budget = parseTeamRunBudget(args[0].budget);
  if (
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].conversationId !== "string" ||
    !CONVERSATION_ID_PATTERN.test(args[0].conversationId) ||
    typeof args[0].task !== "string" ||
    CONTROL_CHARACTER_PATTERN.test(args[0].task) ||
    args[0].task.trim().length < 1 ||
    args[0].task.trim().length > 16384 ||
    args[0].teamMode !== true ||
    budget === null
  ) {
    return null;
  }
  let allowed: readonly SpecialistEmployeeId[] | undefined;
  if (args[0].allowedSpecialistRoleIds !== undefined) {
    if (
      !Array.isArray(args[0].allowedSpecialistRoleIds) ||
      args[0].allowedSpecialistRoleIds.length > 9 ||
      args[0].allowedSpecialistRoleIds.some(
        (role) => typeof role !== "string" || !SPECIALIST_ROLE_SET.has(role),
      )
    ) {
      return null;
    }
    allowed = args[0].allowedSpecialistRoleIds as SpecialistEmployeeId[];
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    conversationId: args[0].conversationId,
    task: args[0].task.trim(),
    teamMode: true as const,
    budget,
    ...(allowed === undefined ? {} : { allowedSpecialistRoleIds: allowed }),
  });
}

function parseTeamRunExecuteInput(
  args: readonly unknown[],
): DesktopTeamRunExecuteInput | null {
  if (args.length !== 1 || !isRecord(args[0])) return null;
  const started = parseTeamRunStartInput([
    {
      workspaceId: args[0].workspaceId,
      conversationId: args[0].conversationId,
      task: args[0].task,
      teamMode: args[0].teamMode,
      budget: args[0].budget,
      ...(args[0].allowedSpecialistRoleIds === undefined
        ? {}
        : { allowedSpecialistRoleIds: args[0].allowedSpecialistRoleIds }),
    },
  ]);
  if (started === null) return null;
  if (
    typeof args[0].rosterEpoch !== "number" ||
    !Number.isInteger(args[0].rosterEpoch) ||
    args[0].rosterEpoch < 1
  ) {
    return null;
  }
  return Object.freeze({
    ...started,
    rosterEpoch: args[0].rosterEpoch,
  });
}

function parseTeamRunAppendBudgetInput(
  args: readonly unknown[],
): DesktopTeamRunAppendBudgetInput | null {
  const identity = parseTeamRunIdInput(
    args.length === 1 && isRecord(args[0])
      ? [{ workspaceId: args[0].workspaceId, teamRunId: args[0].teamRunId }]
      : args,
  );
  if (identity === null || !isRecord(args[0])) return null;
  const budget = parseTeamRunBudget(args[0].budget);
  if (budget === null) return null;
  return Object.freeze({
    workspaceId: identity.workspaceId,
    teamRunId: identity.teamRunId,
    budget,
  });
}

function parseTeamRunIdInput(
  args: readonly unknown[],
): DesktopTeamRunIdInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["teamRunId", "workspaceId"]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].teamRunId !== "string" ||
    !TEAM_RUN_ID_PATTERN.test(args[0].teamRunId)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    teamRunId: args[0].teamRunId,
  });
}

function parseTeamRunSubmitProposalInput(
  args: readonly unknown[],
): DesktopTeamRunSubmitProposalInput | null {
  const identity = parseTeamRunIdInput(
    args.length === 1 && isRecord(args[0])
      ? [
          {
            workspaceId: args[0].workspaceId,
            teamRunId: args[0].teamRunId,
          },
        ]
      : args,
  );
  if (
    identity === null ||
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], ["proposal", "teamRunId", "workspaceId"]) ||
    !isRecord(args[0].proposal)
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: identity.workspaceId,
    teamRunId: identity.teamRunId,
    proposal: args[0].proposal as DesktopTeamRunSubmitProposalInput["proposal"],
  });
}

function parseTeamCollaborationInput(
  args: readonly unknown[],
): DesktopTeamCollaborationInput | null {
  if (
    args.length !== 1 ||
    !isRecord(args[0]) ||
    !hasExactKeys(args[0], [
      "fromAssignmentId",
      "fromEmployeeRoleId",
      "nodeId",
      "question",
      "reason",
      "reportId",
      "targetRoleId",
      "teamRunId",
      "workspaceId",
    ]) ||
    typeof args[0].workspaceId !== "string" ||
    !WORKSPACE_ID_PATTERN.test(args[0].workspaceId) ||
    typeof args[0].teamRunId !== "string" ||
    !TEAM_RUN_ID_PATTERN.test(args[0].teamRunId) ||
    typeof args[0].fromAssignmentId !== "string" ||
    !ASSIGNMENT_ID_PATTERN.test(args[0].fromAssignmentId) ||
    typeof args[0].fromEmployeeRoleId !== "string" ||
    !SPECIALIST_ROLE_SET.has(args[0].fromEmployeeRoleId) ||
    typeof args[0].targetRoleId !== "string" ||
    !SPECIALIST_ROLE_SET.has(args[0].targetRoleId) ||
    typeof args[0].nodeId !== "string" ||
    !TEAM_NODE_ID_PATTERN.test(args[0].nodeId) ||
    typeof args[0].reportId !== "string" ||
    !TEAM_REPORT_ID_PATTERN.test(args[0].reportId) ||
    typeof args[0].question !== "string" ||
    CONTROL_CHARACTER_PATTERN.test(args[0].question) ||
    args[0].question.trim().length < 1 ||
    args[0].question.trim().length > 16384 ||
    typeof args[0].reason !== "string" ||
    CONTROL_CHARACTER_PATTERN.test(args[0].reason) ||
    args[0].reason.trim().length < 1 ||
    args[0].reason.trim().length > 16384
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: args[0].workspaceId,
    teamRunId: args[0].teamRunId,
    fromAssignmentId: args[0].fromAssignmentId,
    fromEmployeeRoleId: args[0].fromEmployeeRoleId as SpecialistEmployeeId,
    targetRoleId: args[0].targetRoleId as SpecialistEmployeeId,
    question: args[0].question.trim(),
    reason: args[0].reason.trim(),
    nodeId: args[0].nodeId,
    reportId: args[0].reportId,
  });
}

function emitTeamRunEvent(
  event: IpcMainInvokeEvent,
  payload: DesktopTeamRunEvent,
): void {
  if (!teamEventIdentityComplete(payload)) {
    return;
  }
  if (event.sender.isDestroyed()) {
    throw new Error("desktop_renderer_destroyed");
  }
  event.sender.send(IPC_EVENT_CHANNELS.teamRunEvent, payload);
}

export function registerClosedIpcHandlers(
  ipcMain: IpcMainLike | IpcMain,
  dependencies: IpcDependencies,
): void {
  for (const channel of Object.values(IPC_CHANNELS)) {
    ipcMain.removeHandler(channel);
  }
  ipcMain.handle(
    IPC_CHANNELS.appGetVersion,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.getVersion();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.runtimeGetStatus,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.getRuntimeStatus();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.runtimeRetryStartup,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.retryRuntimeStartup();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.ownerGetStatus,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.getOwnerStatus();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.ownerBootstrap,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseOwnerBootstrapInput(args);
      return input === null
        ? invalidInput<DesktopOwnerBootstrapResult>()
        : dependencies.bootstrapOwner(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspacesList,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.listWorkspaces();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspacesCreate,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceCreateInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceMutationResult>()
        : dependencies.createWorkspace(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspacesArchive,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceArchiveInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceMutationResult>()
        : dependencies.archiveWorkspace(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceAgent,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceIdInput(args);
      return input === null
        ? invalidInput<{ readonly agent: DesktopParentAgent }>()
        : dependencies.getWorkspaceAgent(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workbenchSettingsGet,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.getApplicationPreference();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workbenchSettingsUpdate,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseApplicationPreferenceUpdateInput(args);
      return input === null
        ? invalidInput<{ readonly preference: DesktopApplicationPreference }>()
        : dependencies.updateApplicationPreference(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceCompositionGet,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceIdInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceCompositionSnapshot>()
        : dependencies.getWorkspaceComposition(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceCompositionPropose,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseCompositionOwnerProposalInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceCompositionProposalResult>()
        : dependencies.proposeWorkspaceComposition(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceCompositionProposeFromAssistant,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseCompositionAssistantProposalInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceCompositionProposalResult>()
        : dependencies.proposeWorkspaceCompositionFromAssistant(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceCompositionProposeRollback,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseCompositionRollbackProposalInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceCompositionProposalResult>()
        : dependencies.proposeWorkspaceCompositionRollback(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceCompositionDecide,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseCompositionDecisionInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceCompositionDecisionResult>()
        : dependencies.decideWorkspaceComposition(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceComponentsGet,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceIdInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceComponentSnapshot>()
        : dependencies.getWorkspaceComponents(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceComponentsPropose,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceComponentProposeInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceComponentProposalResult>()
        : dependencies.proposeWorkspaceComponent(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceComponentsProposeFromAssistant,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceComponentAssistantProposalInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceComponentProposalResult>()
        : dependencies.proposeWorkspaceComponentFromAssistant(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceComponentsImportOwnerPackage,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceIdInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceComponentOwnerPackageImportResult>()
        : dependencies.importOwnerWorkspaceComponentPackage(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceComponentsImportAssistantPackage,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceComponentAssistantPackageImportInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceComponentOwnerPackageImportResult>()
        : dependencies.importAssistantWorkspaceComponentPackage(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceComponentsDecide,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceComponentDecisionInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceComponentDecisionResult>()
        : dependencies.decideWorkspaceComponent(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceComponentsAction,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceComponentActionInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceComponentActionResult>()
        : dependencies.applyWorkspaceComponentAction(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceComponentsInvoke,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceComponentInvokeInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceComponentInvokeResult>()
        : dependencies.invokeWorkspaceComponent(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceComponentsEmergencyStop,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceComponentEmergencyStopInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceComponentEmergencyStopResult>()
        : dependencies.emergencyStopWorkspaceComponents(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceComponentsReconcile,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceComponentReconcileInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceComponentReconcileResult>()
        : dependencies.reconcileWorkspaceComponent(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceFilesAuthorize,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceIdInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceFileAuthorization>()
        : dependencies.authorizeWorkspaceFiles(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceFilesRelease,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceFileReleaseInput(args);
      return input === null
        ? invalidInput<{ readonly released: true }>()
        : dependencies.releaseWorkspaceFiles(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceFilesList,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceFileListInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceFileList>()
        : dependencies.listWorkspaceFiles(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.workspaceFilesRead,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceFileReadInput(args);
      return input === null
        ? invalidInput<DesktopWorkspaceFileReadResult>()
        : dependencies.readWorkspaceFile(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.providersList,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.listProviders();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.providersUpsert,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseProviderUpsertInput(args);
      return input === null
        ? invalidInput<DesktopProviderMutationResult>()
        : dependencies.upsertProvider(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.providersDelete,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseProviderIdInput(args);
      return input === null
        ? invalidInput<{ readonly deleted: true; readonly id: string }>()
        : dependencies.deleteProvider(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.providersTest,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseProviderIdInput(args);
      return input === null
        ? invalidInput<DesktopProviderTestResult>()
        : dependencies.testProvider(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.conversationsList,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceIdInput(args);
      return input === null
        ? invalidInput<DesktopConversationList>()
        : dependencies.listConversations(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.conversationsCreate,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseConversationCreateInput(args);
      return input === null
        ? invalidInput<{
            readonly created: true;
            readonly conversation: DesktopConversation;
          }>()
        : dependencies.createConversation(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.conversationsArchive,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseConversationArchiveInput(args);
      return input === null
        ? invalidInput<{ readonly conversation: DesktopConversation }>()
        : dependencies.archiveConversation(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.conversationsGet,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseConversationGetInput(args);
      return input === null
        ? invalidInput<DesktopConversationDetail>()
        : dependencies.getConversation(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.conversationSend,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseConversationSendInput(args);
      return input === null
        ? invalidInput<DesktopConversationEvent>()
        : dependencies.sendConversation(input, (payload) => {
            if (event.sender.isDestroyed()) {
              throw new Error("desktop_renderer_destroyed");
            }
            event.sender.send(IPC_EVENT_CHANNELS.conversationEvent, payload);
          });
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.conversationCancel,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseConversationCancelInput(args);
      return input === null
        ? invalidInput<{
            readonly cancelled: boolean;
            readonly id: string;
            readonly accepted: boolean;
          }>()
        : dependencies.cancelConversation(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.conversationAbortInFlightSend,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      requireNoIpcArguments(args);
      return dependencies.abortInFlightSend();
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.agentsRolesList,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceIdInput(args);
      return input === null
        ? invalidInput<DesktopAgentRoleList>()
        : dependencies.listAgentRoles(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.agentsRolesGet,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseAgentRoleIdInput(args);
      return input === null
        ? invalidInput<{ readonly role: DesktopAgentRole }>()
        : dependencies.getAgentRole(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.agentsRolesUpdate,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseAgentRoleUpdateInput(args);
      return input === null
        ? invalidInput<{ readonly role: DesktopAgentRole }>()
        : dependencies.updateAgentRole(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.agentsRolesTest,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseAgentRoleIdInput(args);
      return input === null
        ? invalidInput<DesktopAgentRoleTestResult>()
        : dependencies.testAgentRole(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.teamRunsStart,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseTeamRunStartInput(args);
      return input === null
        ? invalidInput<{ readonly teamRun: DesktopTeamRun }>()
        : dependencies.startTeamRun(input, (payload) => {
            emitTeamRunEvent(event, payload);
          });
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.teamRunsCancel,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseTeamRunIdInput(args);
      return input === null
        ? invalidInput<{
            readonly cancelled: boolean;
            readonly accepted: boolean;
            readonly teamRun: DesktopTeamRun;
          }>()
        : dependencies.cancelTeamRun(input, (payload) => {
            emitTeamRunEvent(event, payload);
          });
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.teamRunsGet,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseTeamRunIdInput(args);
      return input === null
        ? invalidInput<{ readonly teamRun: DesktopTeamRun }>()
        : dependencies.getTeamRun(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.teamRunsList,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseWorkspaceIdInput(args);
      return input === null
        ? invalidInput<{ readonly items: readonly DesktopTeamRun[] }>()
        : dependencies.listTeamRuns(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.teamRunsSubmitProposal,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseTeamRunSubmitProposalInput(args);
      return input === null
        ? invalidInput<DesktopTeamRunProposalResult>()
        : dependencies.submitTeamProposal(input, (payload) => {
            emitTeamRunEvent(event, payload);
          });
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.teamRunsGetBlackboard,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseTeamRunIdInput(args);
      return input === null
        ? invalidInput<{ readonly blackboard: PersonalTeamBlackboard }>()
        : dependencies.getTeamBlackboard(input);
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.teamRunsRecordCollaboration,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseTeamCollaborationInput(args);
      return input === null
        ? invalidInput<{
            readonly collaborationRequest: DesktopTeamCollaborationRequest;
          }>()
        : dependencies.recordTeamCollaboration(input, (payload) => {
            emitTeamRunEvent(event, payload);
          });
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.teamRunsExecute,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseTeamRunExecuteInput(args);
      return input === null
        ? invalidInput<{ readonly proof: DesktopTeamRunProof }>()
        : dependencies.executeTeamRun(input, (payload) => {
            emitTeamRunEvent(event, payload);
          });
    },
  );
  ipcMain.handle(
    IPC_CHANNELS.teamRunsAppendBudget,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => {
      requireTrustedSender(event);
      const input = parseTeamRunAppendBudgetInput(args);
      return input === null
        ? invalidInput<{ readonly teamRun: DesktopTeamRun }>()
        : dependencies.appendTeamRunBudget(input, (payload) => {
            emitTeamRunEvent(event, payload);
          });
    },
  );
}
