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
  type DesktopWorkspaceCreateInput,
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
  readonly listProviders: () => Promise<DesktopOperationResult<DesktopProviderList>>;
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
  ) => Promise<DesktopOperationResult<{ readonly items: readonly DesktopTeamRun[] }>>;
  readonly submitTeamProposal: (
    input: DesktopTeamRunSubmitProposalInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ) => Promise<DesktopOperationResult<DesktopTeamRunProposalResult>>;
  readonly getTeamBlackboard: (
    input: DesktopTeamRunIdInput,
  ) => Promise<DesktopOperationResult<{ readonly blackboard: PersonalTeamBlackboard }>>;
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
  if (args[0].id !== undefined && (typeof args[0].id !== "string" || !PROVIDER_ID_PATTERN.test(args[0].id))) {
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
    thinkingDepth: args[0].thinkingDepth as DesktopProviderUpsertInput["thinkingDepth"],
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
    if (title === null || !hasExactKeys(args[0], ["title", "workspaceId"])) return null;
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
    !hasExactKeys(args[0], ["conversationId", "expectedRowVersion", "workspaceId"]) ||
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
    ...(args[0].providerId === undefined ? {} : { providerId: args[0].providerId }),
    ...(args[0].retryOfMessageId === undefined
      ? {}
      : { retryOfMessageId: args[0].retryOfMessageId }),
    ...(args[0].sendEpoch === undefined ? {} : { sendEpoch: args[0].sendEpoch }),
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
    thinkingDepth: args[0].thinkingDepth as DesktopAgentRoleUpdateInput["thinkingDepth"],
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
      "question",
      "reason",
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
