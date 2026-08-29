import { createHash } from "node:crypto";

import type {
  DesktopAgentRole,
  DesktopAgentRoleIdInput,
  DesktopAgentRoleList,
  DesktopAgentRoleTestResult,
  DesktopAgentRoleUpdateInput,
  DesktopApplicationPreference,
  DesktopApplicationPreferenceUpdateInput,
  DesktopWorkspaceCompositionAssistantProposalInput,
  DesktopWorkspaceCompositionAuditEvent,
  DesktopWorkspaceCompositionDecisionInput,
  DesktopWorkspaceCompositionDecisionResult,
  DesktopWorkspaceCompositionOwnerProposalInput,
  DesktopWorkspaceCompositionProfileValue,
  DesktopWorkspaceCompositionProposal,
  DesktopWorkspaceCompositionProposalResult,
  DesktopWorkspaceCompositionRevision,
  DesktopWorkspaceCompositionRollbackProposalInput,
  DesktopWorkspaceCompositionSnapshot,
  DesktopWorkspaceSlotCatalogItem,
  DesktopWorkspaceSlotId,
  DesktopConversation,
  DesktopConversationArchiveInput,
  DesktopConversationCreateInput,
  DesktopConversationDetail,
  DesktopConversationEvent,
  DesktopConversationGetInput,
  DesktopConversationList,
  DesktopConversationSendInput,
  DesktopInvocation,
  DesktopMessage,
  DesktopOperationResult,
  DesktopOwner,
  DesktopOwnerBootstrapInput,
  DesktopOwnerBootstrapResult,
  DesktopOwnerStatus,
  DesktopParentAgent,
  DesktopProvider,
  DesktopProviderIdInput,
  DesktopProviderList,
  DesktopProviderMutationResult,
  DesktopProviderTestResult,
  DesktopProviderUpsertInput,
  DesktopTeamCollaborationInput,
  DesktopTeamCollaborationRequest,
  DesktopTeamPlanRevision,
  DesktopTeamRun,
  DesktopTeamRunIdInput,
  DesktopTeamRunProposalResult,
  DesktopTeamRunStartInput,
  DesktopTeamRunSubmitProposalInput,
  DesktopWorkspace,
  DesktopWorkspaceArchiveInput,
  DesktopWorkspaceCreateInput,
  DesktopWorkspaceIdInput,
  DesktopWorkspaceList,
  DesktopWorkspaceMutationResult,
  DesktopWorkspaceComponentActionResult,
  DesktopWorkspaceComponentAssistantProposalInput,
  DesktopWorkspaceComponentNativeActionInput,
  DesktopWorkspaceComponentPackageAttestationInput,
  DesktopWorkspaceComponentPackageAttestationResult,
  DesktopWorkspaceComponentOwnerPackageRegisterInput,
  DesktopWorkspaceComponentOwnerPackageRegistration,
  DesktopWorkspaceComponentBeginInput,
  DesktopWorkspaceComponentBeginResult,
  DesktopWorkspaceComponentDecisionInput,
  DesktopWorkspaceComponentDecisionResult,
  DesktopWorkspaceComponentNativeEmergencyStopInput,
  DesktopWorkspaceComponentNativeEmergencyStopResult,
  DesktopWorkspaceComponentProposalResult,
  DesktopWorkspaceComponentProposeInput,
  DesktopWorkspaceComponentReconcileInput,
  DesktopWorkspaceComponentReconcileResult,
  DesktopWorkspaceComponentRecoverySettleInput,
  DesktopWorkspaceComponentRecoverySettleResult,
  DesktopWorkspaceComponentSettleInput,
  DesktopWorkspaceComponentSettleResult,
  DesktopWorkspaceComponentSnapshot,
  PersonalEmployeeId,
  PersonalTeamBlackboard,
  SpecialistEmployeeId,
  TeamRunBudget,
  TeamRunState,
} from "../shared/ipc-contract.ts";
import {
  PERSONAL_EMPLOYEE_IDS,
  SPECIALIST_EMPLOYEE_IDS,
  type EmployeeTeamReport,
} from "../shared/personal-team.ts";
import {
  WORKSPACE_COMPONENT_LIFECYCLE_ACTIONS,
  WORKSPACE_COMPONENT_OPERATIONS,
} from "../shared/workspace-components.ts";
import {
  parseWorkspaceComponentActionResult,
  parseWorkspaceComponentBeginResult,
  parseWorkspaceComponentDecisionResult,
  parseWorkspaceComponentEmergencyStopPrepareResult,
  parseWorkspaceComponentEmergencyStopSettleResult,
  parseWorkspaceComponentPackageAttestationResult,
  parseWorkspaceComponentOwnerPackageRegistration,
  parseWorkspaceComponentProposalResult,
  parseWorkspaceComponentReconcileResult,
  parseWorkspaceComponentRecoverySettleResult,
  parseWorkspaceComponentSettleResult,
  parseWorkspaceComponentSnapshot,
} from "./native-workspace-components.ts";

const TOKEN_PATTERN = /^[a-f0-9]{64}$/u;
const ERROR_CODE_PATTERN = /^[a-z][a-z0-9_]{2,95}$/u;
const OWNER_ID_PATTERN = /^owner_[a-f0-9]{32}$/u;
const WORKSPACE_ID_PATTERN = /^workspace_[a-f0-9]{32}$/u;
const PROVIDER_ID_PATTERN = /^provider_[a-f0-9]{32}$/u;
const CONVERSATION_ID_PATTERN = /^conversation_[a-f0-9]{32}$/u;
const AGENT_ID_PATTERN = /^agent_[a-f0-9]{32}$/u;
const MESSAGE_ID_PATTERN = /^message_[a-f0-9]{32}$/u;
const INVOCATION_ID_PATTERN = /^invocation_[a-f0-9]{32}$/u;
const TEAM_RUN_ID_PATTERN = /^teamrun_[a-f0-9]{32}$/u;
const TEAM_COLLABORATION_ID_PATTERN = /^teamcollab_[a-f0-9]{32}$/u;
const ASSIGNMENT_ID_PATTERN = /^[A-Za-z][A-Za-z0-9._-]{0,127}$/u;
const TEAM_NODE_ID_PATTERN = /^teamnode_[a-f0-9]{32}$/u;
const TEAM_REPORT_ID_PATTERN = /^teamrpt_[a-f0-9]{32}$/u;
const TEAM_REV_ID_PATTERN = /^teamrev_[a-f0-9]{32}$/u;
const COMPOSITION_PROPOSAL_ID_PATTERN = /^proposal_[a-f0-9]{32}$/u;
const COMPONENT_ID_PATTERN = /^[a-z][a-z0-9.-]{2,127}$/u;
const COMPONENT_OPERATION_ID_PATTERN = /^compop_[a-f0-9]{32}$/u;
const COMPONENT_EFFECT_ID_PATTERN = /^effect_[a-f0-9]{32}$/u;
const COMPONENT_RECOVERY_ID_PATTERN = /^recovery_[a-f0-9]{32}$/u;
const COMPONENT_RUNTIME_ID_PATTERN = /^runtime_[a-f0-9]{32}$/u;
const COMPONENT_VERSION_PATTERN = /^[0-9]+\.[0-9]+\.[0-9]+$/u;
const COMPONENT_ACTIONS = new Set<string>(
  WORKSPACE_COMPONENT_LIFECYCLE_ACTIONS,
);
const COMPONENT_OPERATIONS = new Set<string>(WORKSPACE_COMPONENT_OPERATIONS);
const COMPOSITION_ROLLBACK_REFERENCE_PATTERN = /^revision:([1-9][0-9]*)$/u;
const SHA256_PATTERN = /^[a-f0-9]{64}$/u;
const EMPLOYEE_ROLE_SET = new Set<string>(PERSONAL_EMPLOYEE_IDS);
const SPECIALIST_ROLE_SET = new Set<string>(SPECIALIST_EMPLOYEE_IDS);
const TEAM_RUN_STATES = new Set([
  "preparing",
  "running",
  "cancelling",
  "succeeded",
  "failed",
  "cancelled",
  "unknown",
  "budget_exhausted",
  "cannot_complete",
]);
const TEAM_PROVIDER_CALL_PURPOSES = new Set([
  "parent-propose",
  "parent-replan",
  "parent-synthesize",
  "employee",
]);
const TEAM_PARENT_CALL_PURPOSES = new Set([
  "parent-propose",
  "parent-replan",
  "parent-synthesize",
]);
const TEAM_PARENT_CALL_STATES = new Set([
  "pending",
  "succeeded",
  "failed",
  "cancelled",
  "unknown",
]);
const TEAM_PARENT_CALL_TERMINAL_STATES = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "unknown",
]);
const FAMILIES = new Set([
  "deepseek",
  "openai",
  "anthropic",
  "glm",
  "kimi",
  "generic-openai-compatible",
]);
const GEARS = new Set(["economy", "standard", "deep", "audit"]);
const DEPTHS = new Set(["disabled", "low", "medium", "high"]);
const MAX_RESPONSE_BYTES = 256 * 1024;
const MAX_CONVERSATION_BYTES = 1_048_576;
const MAX_WORKSPACES = 256;
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
] as const satisfies readonly DesktopWorkspaceSlotId[]);
const COMPOSITION_REQUIRED_SLOTS = new Set<DesktopWorkspaceSlotId>([
  "conversation.transcript",
  "settings.center",
]);
const COMPOSITION_UNAVAILABLE_SLOTS = new Set<DesktopWorkspaceSlotId>([
  "knowledge.ebook",
  "mcp.catalog",
  "sandbox.runtime",
  "skills.catalog",
  "source-control",
  "terminal",
]);
const COMPOSITION_SLOT_POSTURE = Object.freeze({
  "agent.rail": Object.freeze({ region: "right", posture: "admitted" }),
  "conversation.transcript": Object.freeze({
    region: "editor",
    posture: "required",
  }),
  "event.agent-log": Object.freeze({ region: "bottom", posture: "admitted" }),
  "event.output": Object.freeze({ region: "bottom", posture: "admitted" }),
  "knowledge.ebook": Object.freeze({
    region: "editor",
    posture: "unavailable",
  }),
  "mcp.catalog": Object.freeze({ region: "settings", posture: "unavailable" }),
  "provider.settings": Object.freeze({
    region: "settings",
    posture: "admitted",
  }),
  "run.history": Object.freeze({ region: "sidebar", posture: "admitted" }),
  "sandbox.runtime": Object.freeze({
    region: "settings",
    posture: "unavailable",
  }),
  "settings.center": Object.freeze({ region: "editor", posture: "required" }),
  "skills.catalog": Object.freeze({
    region: "settings",
    posture: "unavailable",
  }),
  "source-control": Object.freeze({
    region: "sidebar",
    posture: "unavailable",
  }),
  terminal: Object.freeze({ region: "bottom", posture: "unavailable" }),
  "workspace.brief": Object.freeze({ region: "editor", posture: "admitted" }),
  "workspace.explorer": Object.freeze({
    region: "sidebar",
    posture: "admitted",
  }),
} as const satisfies Readonly<
  Record<
    DesktopWorkspaceSlotId,
    Readonly<{
      region: DesktopWorkspaceSlotCatalogItem["region"];
      posture: DesktopWorkspaceSlotCatalogItem["posture"];
    }>
  >
>);

type FetchLike = typeof fetch;
type NativeMethod = "GET" | "POST" | "DELETE";

export interface DesktopTeamParentCallRecord {
  readonly invocationId: string;
  readonly teamRunId: string;
  readonly planRevisionId: string | null;
  readonly purpose: "parent-propose" | "parent-replan" | "parent-synthesize";
  readonly state: "pending" | "succeeded" | "failed" | "cancelled" | "unknown";
  readonly providerId: string;
  readonly requestedModel: string;
  readonly actualModel: string | null;
  readonly inputTokens: number | null;
  readonly outputTokens: number | null;
  readonly totalTokens: number | null;
  readonly outputSha256: string | null;
  readonly errorCode: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
}

function componentGrantBody(
  grant: DesktopWorkspaceComponentProposeInput["requestedGrants"][number],
): Readonly<Record<string, unknown>> {
  return Object.freeze({
    action: grant.action,
    logical_resource_id: grant.logicalResourceId,
    resource_version: grant.resourceVersion,
    logical_service_id: grant.logicalServiceId,
    expires_in_seconds: grant.expiresInSeconds,
    maximum_invocations: grant.maximumInvocations,
    maximum_bytes_in: grant.maximumBytesIn,
    maximum_bytes_out: grant.maximumBytesOut,
    maximum_tokens: grant.maximumTokens,
    maximum_wall_time_ms: grant.maximumWallTimeMs,
    maximum_cost_units: grant.maximumCostUnits,
  });
}

function componentBaseIdentityValid(input: {
  readonly workspaceId: string;
  readonly componentId: string;
  readonly expectedRevision: number;
  readonly manifestSha256: string;
  readonly packageSha256: string;
  readonly idempotencyKey: string;
}): boolean {
  return (
    WORKSPACE_ID_PATTERN.test(input.workspaceId) &&
    COMPONENT_ID_PATTERN.test(input.componentId) &&
    isNonNegativeInteger(input.expectedRevision) &&
    SHA256_PATTERN.test(input.manifestSha256) &&
    SHA256_PATTERN.test(input.packageSha256) &&
    isBoundedString(input.idempotencyKey, 128) &&
    input.idempotencyKey.length >= 8
  );
}

function componentGrantValid(
  grant: DesktopWorkspaceComponentProposeInput["requestedGrants"][number],
): boolean {
  return (
    isBoundedString(grant.action, 128) &&
    (grant.logicalResourceId === null ||
      isBoundedString(grant.logicalResourceId, 128)) &&
    (grant.resourceVersion === null ||
      isPositiveInteger(grant.resourceVersion)) &&
    (grant.logicalServiceId === null ||
      isBoundedString(grant.logicalServiceId, 128)) &&
    isPositiveInteger(grant.expiresInSeconds) &&
    isPositiveInteger(grant.maximumInvocations) &&
    isNonNegativeInteger(grant.maximumBytesIn) &&
    isNonNegativeInteger(grant.maximumBytesOut) &&
    isNonNegativeInteger(grant.maximumTokens) &&
    isPositiveInteger(grant.maximumWallTimeMs) &&
    isPositiveInteger(grant.maximumCostUnits)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasExactKeys(
  value: Record<string, unknown>,
  expected: readonly string[],
): boolean {
  const actual = Object.keys(value).sort();
  return (
    actual.length === expected.length &&
    actual.every((key, index) => key === [...expected].sort()[index])
  );
}

function isBoundedString(value: unknown, maximum: number): value is string {
  return (
    typeof value === "string" && value.length > 0 && value.length <= maximum
  );
}

function isNullableNonNegativeInteger(value: unknown): value is number | null {
  return (
    value === null ||
    (typeof value === "number" && Number.isInteger(value) && value >= 0)
  );
}

function isNonNegativeInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === "number" && value >= 0;
}

function isPositiveInteger(value: unknown): value is number {
  return Number.isSafeInteger(value) && typeof value === "number" && value >= 1;
}

function failure<T>(code: string): DesktopOperationResult<T> {
  return Object.freeze({
    ok: false,
    error: Object.freeze({ code }),
  });
}

function success<T>(value: T): DesktopOperationResult<T> {
  return Object.freeze({ ok: true, value });
}

function parseOwner(value: unknown): DesktopOwner | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["created_at", "display_name", "id", "updated_at"]) ||
    typeof value.id !== "string" ||
    !OWNER_ID_PATTERN.test(value.id) ||
    !isBoundedString(value.display_name, 256) ||
    !isBoundedString(value.created_at, 64) ||
    !isBoundedString(value.updated_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    displayName: value.display_name,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseOwnerStatus(value: unknown): DesktopOwnerStatus | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["initialized", "owner"]) ||
    typeof value.initialized !== "boolean"
  ) {
    return null;
  }
  const owner = value.owner === null ? null : parseOwner(value.owner);
  if (
    (value.initialized && owner === null) ||
    (!value.initialized && value.owner !== null)
  ) {
    return null;
  }
  return Object.freeze({ initialized: value.initialized, owner });
}

function parseOwnerBootstrap(
  value: unknown,
): DesktopOwnerBootstrapResult | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["created", "initialized", "owner"]) ||
    value.initialized !== true ||
    typeof value.created !== "boolean"
  ) {
    return null;
  }
  const owner = parseOwner(value.owner);
  if (owner === null) return null;
  return Object.freeze({
    initialized: true,
    created: value.created,
    owner,
  });
}

function parseWorkspace(value: unknown): DesktopWorkspace | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "created_at",
      "id",
      "name",
      "owner_id",
      "row_version",
      "state",
      "updated_at",
    ]) ||
    typeof value.id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(value.id) ||
    typeof value.owner_id !== "string" ||
    !OWNER_ID_PATTERN.test(value.owner_id) ||
    !isBoundedString(value.name, 256) ||
    (value.state !== "active" && value.state !== "archived") ||
    typeof value.row_version !== "number" ||
    !Number.isInteger(value.row_version) ||
    value.row_version < 1 ||
    value.row_version > 2_147_483_647 ||
    !isBoundedString(value.created_at, 64) ||
    !isBoundedString(value.updated_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    ownerId: value.owner_id,
    name: value.name,
    state: value.state,
    rowVersion: value.row_version,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseWorkspaceList(value: unknown): DesktopWorkspaceList | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["items"]) ||
    !Array.isArray(value.items) ||
    value.items.length > MAX_WORKSPACES
  ) {
    return null;
  }
  const items: DesktopWorkspace[] = [];
  const identifiers = new Set<string>();
  for (const candidate of value.items) {
    const workspace = parseWorkspace(candidate);
    if (workspace === null || identifiers.has(workspace.id)) return null;
    identifiers.add(workspace.id);
    items.push(workspace);
  }
  return Object.freeze({ items: Object.freeze(items) });
}

function parseWorkspaceCreate(
  value: unknown,
): DesktopWorkspaceMutationResult | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["created", "workspace"]) ||
    value.created !== true
  ) {
    return null;
  }
  const workspace = parseWorkspace(value.workspace);
  return workspace === null ? null : Object.freeze({ workspace });
}

function parseWorkspaceMutation(
  value: unknown,
): DesktopWorkspaceMutationResult | null {
  if (!isRecord(value) || !hasExactKeys(value, ["workspace"])) return null;
  const workspace = parseWorkspace(value.workspace);
  return workspace === null ? null : Object.freeze({ workspace });
}

function parseApplicationPreference(
  value: unknown,
): DesktopApplicationPreference | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "density",
      "reduce_motion",
      "row_version",
      "updated_at",
    ]) ||
    (value.density !== "compact" && value.density !== "comfortable") ||
    typeof value.reduce_motion !== "boolean" ||
    typeof value.row_version !== "number" ||
    !Number.isInteger(value.row_version) ||
    value.row_version < 1 ||
    value.row_version > 2_147_483_647 ||
    !isBoundedString(value.updated_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    density: value.density,
    reduceMotion: value.reduce_motion,
    rowVersion: value.row_version,
    updatedAt: value.updated_at,
  });
}

function parseApplicationPreferenceResult(
  value: unknown,
): { readonly preference: DesktopApplicationPreference } | null {
  if (!isRecord(value) || !hasExactKeys(value, ["preference"])) return null;
  const preference = parseApplicationPreference(value.preference);
  return preference === null ? null : Object.freeze({ preference });
}

function parseCompositionProfileValue(
  value: unknown,
): DesktopWorkspaceCompositionProfileValue | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "appearance",
      "layout",
      "schema_version",
      "slots",
      "template",
    ]) ||
    value.schema_version !== 1 ||
    !isRecord(value.template) ||
    !hasExactKeys(value.template, ["id", "version"]) ||
    value.template.id !== "standard-workbench" ||
    value.template.version !== 1 ||
    !isRecord(value.appearance) ||
    !hasExactKeys(value.appearance, ["density", "quiet_chrome"]) ||
    !["inherit", "compact", "comfortable"].includes(
      String(value.appearance.density),
    ) ||
    typeof value.appearance.quiet_chrome !== "boolean" ||
    !isRecord(value.layout) ||
    !hasExactKeys(value.layout, [
      "agent_panel",
      "bottom_panel",
      "focus_mode",
      "sidebar",
    ]) ||
    (value.layout.agent_panel !== "open" &&
      value.layout.agent_panel !== "closed") ||
    !["hidden", "output", "agent-log"].includes(
      String(value.layout.bottom_panel),
    ) ||
    typeof value.layout.focus_mode !== "boolean" ||
    !["explorer", "run", "blackboard", "hidden"].includes(
      String(value.layout.sidebar),
    ) ||
    !isRecord(value.slots) ||
    !hasExactKeys(value.slots, COMPOSITION_SLOT_IDS) ||
    COMPOSITION_SLOT_IDS.some(
      (slotId) =>
        typeof (value.slots as Record<string, unknown>)[slotId] !== "boolean",
    )
  ) {
    return null;
  }
  const density = value.appearance.density;
  const bottomPanel = value.layout.bottom_panel;
  const sidebar = value.layout.sidebar;
  const rawSlots = value.slots as Record<DesktopWorkspaceSlotId, boolean>;
  if (
    (density !== "inherit" &&
      density !== "compact" &&
      density !== "comfortable") ||
    (bottomPanel !== "hidden" &&
      bottomPanel !== "output" &&
      bottomPanel !== "agent-log") ||
    (sidebar !== "explorer" &&
      sidebar !== "run" &&
      sidebar !== "blackboard" &&
      sidebar !== "hidden") ||
    [...COMPOSITION_REQUIRED_SLOTS].some((slotId) => !rawSlots[slotId]) ||
    [...COMPOSITION_UNAVAILABLE_SLOTS].some((slotId) => rawSlots[slotId]) ||
    (!rawSlots["agent.rail"] && value.layout.agent_panel !== "closed") ||
    (!rawSlots["workspace.explorer"] && sidebar === "explorer") ||
    (!rawSlots["run.history"] && sidebar === "run") ||
    (!rawSlots["workspace.brief"] && sidebar === "blackboard") ||
    (!rawSlots["event.output"] && bottomPanel === "output") ||
    (!rawSlots["event.agent-log"] && bottomPanel === "agent-log")
  ) {
    return null;
  }
  const slots = Object.freeze(
    Object.fromEntries(
      COMPOSITION_SLOT_IDS.map((slotId) => [slotId, rawSlots[slotId]]),
    ) as Record<DesktopWorkspaceSlotId, boolean>,
  );
  return Object.freeze({
    schemaVersion: 1,
    template: Object.freeze({ id: "standard-workbench", version: 1 }),
    appearance: Object.freeze({
      density,
      quietChrome: value.appearance.quiet_chrome,
    }),
    layout: Object.freeze({
      agentPanel: value.layout.agent_panel,
      bottomPanel,
      focusMode: value.layout.focus_mode,
      sidebar,
    }),
    slots,
  });
}

function parseCompositionRevision(
  value: unknown,
): DesktopWorkspaceCompositionRevision | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "created_at",
      "profile_sha256",
      "proposal_id",
      "revision",
      "source_kind",
      "value",
      "workspace_id",
    ]) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(value.workspace_id) ||
    typeof value.revision !== "number" ||
    !Number.isInteger(value.revision) ||
    value.revision < 1 ||
    value.revision > 2_147_483_647 ||
    typeof value.profile_sha256 !== "string" ||
    !SHA256_PATTERN.test(value.profile_sha256) ||
    !["system", "owner", "assistant", "rollback"].includes(
      String(value.source_kind),
    ) ||
    (value.proposal_id !== null &&
      (typeof value.proposal_id !== "string" ||
        !COMPOSITION_PROPOSAL_ID_PATTERN.test(value.proposal_id))) ||
    !isBoundedString(value.created_at, 64)
  ) {
    return null;
  }
  const profile = parseCompositionProfileValue(value.value);
  if (
    profile === null ||
    compositionProfileSha256(profile) !== value.profile_sha256
  ) {
    return null;
  }
  const sourceKind = value.source_kind;
  if (
    sourceKind !== "system" &&
    sourceKind !== "owner" &&
    sourceKind !== "assistant" &&
    sourceKind !== "rollback"
  ) {
    return null;
  }
  if (
    (value.revision === 1 &&
      (sourceKind !== "system" || value.proposal_id !== null)) ||
    (value.revision > 1 &&
      (sourceKind === "system" || value.proposal_id === null))
  ) {
    return null;
  }
  return Object.freeze({
    workspaceId: value.workspace_id,
    revision: value.revision,
    profileSha256: value.profile_sha256,
    sourceKind,
    proposalId: value.proposal_id,
    value: profile,
    createdAt: value.created_at,
  });
}

function parseCompositionProposal(
  value: unknown,
): DesktopWorkspaceCompositionProposal | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "applied_revision",
      "base_profile_sha256",
      "base_revision",
      "created_at",
      "decided_at",
      "decision",
      "desired_profile",
      "desired_profile_sha256",
      "id",
      "request_sha256",
      "source_kind",
      "source_reference",
      "workspace_id",
    ]) ||
    typeof value.id !== "string" ||
    !COMPOSITION_PROPOSAL_ID_PATTERN.test(value.id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(value.workspace_id) ||
    typeof value.base_revision !== "number" ||
    !Number.isInteger(value.base_revision) ||
    value.base_revision < 1 ||
    typeof value.base_profile_sha256 !== "string" ||
    !SHA256_PATTERN.test(value.base_profile_sha256) ||
    (value.source_kind !== "owner" &&
      value.source_kind !== "assistant" &&
      value.source_kind !== "rollback") ||
    (value.source_reference !== null &&
      !isBoundedString(value.source_reference, 128)) ||
    typeof value.desired_profile_sha256 !== "string" ||
    !SHA256_PATTERN.test(value.desired_profile_sha256) ||
    typeof value.request_sha256 !== "string" ||
    !SHA256_PATTERN.test(value.request_sha256) ||
    (value.decision !== null &&
      value.decision !== "approved" &&
      value.decision !== "rejected") ||
    (value.applied_revision !== null &&
      (typeof value.applied_revision !== "number" ||
        !Number.isInteger(value.applied_revision) ||
        value.applied_revision < 2)) ||
    !isBoundedString(value.created_at, 64) ||
    (value.decided_at !== null && !isBoundedString(value.decided_at, 64))
  ) {
    return null;
  }
  if (
    (value.decision === null &&
      (value.applied_revision !== null || value.decided_at !== null)) ||
    (value.decision === "approved" &&
      (value.applied_revision === null || value.decided_at === null)) ||
    (value.decision === "rejected" &&
      (value.applied_revision !== null || value.decided_at === null))
  ) {
    return null;
  }
  const desiredProfile = parseCompositionProfileValue(value.desired_profile);
  const rollbackReference =
    typeof value.source_reference === "string"
      ? COMPOSITION_ROLLBACK_REFERENCE_PATTERN.exec(value.source_reference)
      : null;
  if (
    desiredProfile === null ||
    (value.source_kind === "owner" && value.source_reference !== null) ||
    (value.source_kind === "assistant" &&
      (typeof value.source_reference !== "string" ||
        !MESSAGE_ID_PATTERN.test(value.source_reference))) ||
    (value.source_kind === "rollback" &&
      (rollbackReference === null ||
        Number(rollbackReference[1]) >= value.base_revision)) ||
    (value.decision === "approved" &&
      value.applied_revision !== value.base_revision + 1) ||
    compositionProfileSha256(desiredProfile) !== value.desired_profile_sha256 ||
    compositionRequestSha256({
      workspaceId: value.workspace_id,
      baseRevision: value.base_revision,
      baseProfileSha256: value.base_profile_sha256,
      sourceKind: value.source_kind,
      sourceReference: value.source_reference,
      desiredProfileSha256: value.desired_profile_sha256,
    }) !== value.request_sha256
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    workspaceId: value.workspace_id,
    baseRevision: value.base_revision,
    baseProfileSha256: value.base_profile_sha256,
    sourceKind: value.source_kind,
    sourceReference: value.source_reference,
    desiredProfileSha256: value.desired_profile_sha256,
    requestSha256: value.request_sha256,
    desiredProfile,
    decision: value.decision,
    appliedRevision: value.applied_revision,
    createdAt: value.created_at,
    decidedAt: value.decided_at,
  });
}

function parseCompositionProposalResult(
  value: unknown,
): DesktopWorkspaceCompositionProposalResult | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["proposal", "replayed"]) ||
    typeof value.replayed !== "boolean"
  ) {
    return null;
  }
  const proposal = parseCompositionProposal(value.proposal);
  return proposal === null
    ? null
    : Object.freeze({ proposal, replayed: value.replayed });
}

function parseCompositionSlot(
  value: unknown,
): DesktopWorkspaceSlotCatalogItem | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["id", "label", "posture", "region"]) ||
    typeof value.id !== "string" ||
    !COMPOSITION_SLOT_IDS.includes(value.id as DesktopWorkspaceSlotId) ||
    !isBoundedString(value.label, 64) ||
    !["sidebar", "editor", "right", "settings", "bottom"].includes(
      String(value.region),
    ) ||
    !["required", "admitted", "unavailable"].includes(String(value.posture))
  ) {
    return null;
  }
  const id = value.id as DesktopWorkspaceSlotId;
  const region = value.region;
  const posture = value.posture;
  const expected = COMPOSITION_SLOT_POSTURE[id];
  if (
    (region !== "sidebar" &&
      region !== "editor" &&
      region !== "right" &&
      region !== "settings" &&
      region !== "bottom") ||
    (posture !== "required" &&
      posture !== "admitted" &&
      posture !== "unavailable") ||
    region !== expected.region ||
    posture !== expected.posture
  ) {
    return null;
  }
  return Object.freeze({ id, label: value.label, region, posture });
}

function parseCompositionAudit(
  value: unknown,
): DesktopWorkspaceCompositionAuditEvent | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["created_at", "event_type", "payload", "sequence"]) ||
    typeof value.sequence !== "number" ||
    !Number.isSafeInteger(value.sequence) ||
    value.sequence < 1 ||
    !isRecord(value.payload) ||
    !isBoundedString(value.created_at, 64)
  ) {
    return null;
  }
  const common = Object.freeze({
    sequence: value.sequence,
    createdAt: value.created_at,
  });
  if (value.event_type === "workspace_composition_proposed") {
    if (
      !hasExactKeys(value.payload, [
        "base_revision",
        "desired_profile_sha256",
        "proposal_id",
        "request_sha256",
        "source_kind",
      ]) ||
      typeof value.payload.base_revision !== "number" ||
      !Number.isSafeInteger(value.payload.base_revision) ||
      value.payload.base_revision < 1 ||
      value.payload.base_revision > 2_147_483_647 ||
      typeof value.payload.desired_profile_sha256 !== "string" ||
      !SHA256_PATTERN.test(value.payload.desired_profile_sha256) ||
      typeof value.payload.proposal_id !== "string" ||
      !COMPOSITION_PROPOSAL_ID_PATTERN.test(value.payload.proposal_id) ||
      typeof value.payload.request_sha256 !== "string" ||
      !SHA256_PATTERN.test(value.payload.request_sha256) ||
      (value.payload.source_kind !== "owner" &&
        value.payload.source_kind !== "assistant" &&
        value.payload.source_kind !== "rollback")
    ) {
      return null;
    }
    return Object.freeze({
      ...common,
      eventType: value.event_type,
      payload: Object.freeze({
        baseRevision: value.payload.base_revision,
        desiredProfileSha256: value.payload.desired_profile_sha256,
        proposalId: value.payload.proposal_id,
        requestSha256: value.payload.request_sha256,
        sourceKind: value.payload.source_kind,
      }),
    });
  }
  if (value.event_type === "workspace_composition_rejected") {
    if (
      !hasExactKeys(value.payload, ["proposal_id", "request_sha256"]) ||
      typeof value.payload.proposal_id !== "string" ||
      !COMPOSITION_PROPOSAL_ID_PATTERN.test(value.payload.proposal_id) ||
      typeof value.payload.request_sha256 !== "string" ||
      !SHA256_PATTERN.test(value.payload.request_sha256)
    ) {
      return null;
    }
    return Object.freeze({
      ...common,
      eventType: value.event_type,
      payload: Object.freeze({
        proposalId: value.payload.proposal_id,
        requestSha256: value.payload.request_sha256,
      }),
    });
  }
  if (value.event_type === "workspace_composition_applied") {
    if (
      !hasExactKeys(value.payload, [
        "profile_sha256",
        "proposal_id",
        "request_sha256",
        "revision",
        "source_kind",
      ]) ||
      typeof value.payload.profile_sha256 !== "string" ||
      !SHA256_PATTERN.test(value.payload.profile_sha256) ||
      typeof value.payload.proposal_id !== "string" ||
      !COMPOSITION_PROPOSAL_ID_PATTERN.test(value.payload.proposal_id) ||
      typeof value.payload.request_sha256 !== "string" ||
      !SHA256_PATTERN.test(value.payload.request_sha256) ||
      typeof value.payload.revision !== "number" ||
      !Number.isSafeInteger(value.payload.revision) ||
      value.payload.revision < 2 ||
      value.payload.revision > 2_147_483_647 ||
      (value.payload.source_kind !== "owner" &&
        value.payload.source_kind !== "assistant" &&
        value.payload.source_kind !== "rollback")
    ) {
      return null;
    }
    return Object.freeze({
      ...common,
      eventType: value.event_type,
      payload: Object.freeze({
        profileSha256: value.payload.profile_sha256,
        proposalId: value.payload.proposal_id,
        requestSha256: value.payload.request_sha256,
        revision: value.payload.revision,
        sourceKind: value.payload.source_kind,
      }),
    });
  }
  return null;
}

function parseCompositionSnapshot(
  value: unknown,
): DesktopWorkspaceCompositionSnapshot | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "audit",
      "profile",
      "proposals",
      "revisions",
      "slot_catalog",
    ]) ||
    !Array.isArray(value.revisions) ||
    value.revisions.length < 1 ||
    value.revisions.length > 25 ||
    !Array.isArray(value.proposals) ||
    value.proposals.length > 25 ||
    !Array.isArray(value.slot_catalog) ||
    value.slot_catalog.length !== COMPOSITION_SLOT_IDS.length ||
    !Array.isArray(value.audit) ||
    value.audit.length > 50
  ) {
    return null;
  }
  const profile = parseCompositionRevision(value.profile);
  const revisions = value.revisions.map(parseCompositionRevision);
  const proposals = value.proposals.map(parseCompositionProposal);
  const slotCatalog = value.slot_catalog.map(parseCompositionSlot);
  const audit = value.audit.map(parseCompositionAudit);
  if (
    profile === null ||
    revisions.some((item) => item === null) ||
    proposals.some((item) => item === null) ||
    slotCatalog.some((item) => item === null) ||
    audit.some((item) => item === null)
  ) {
    return null;
  }
  const parsedRevisions = revisions as DesktopWorkspaceCompositionRevision[];
  const parsedProposals = proposals as DesktopWorkspaceCompositionProposal[];
  const parsedSlots = slotCatalog as DesktopWorkspaceSlotCatalogItem[];
  const parsedAudit = audit as DesktopWorkspaceCompositionAuditEvent[];
  const firstRevision = parsedRevisions[0];
  if (
    firstRevision === undefined ||
    firstRevision.revision !== profile.revision ||
    firstRevision.profileSha256 !== profile.profileSha256 ||
    firstRevision.sourceKind !== profile.sourceKind ||
    firstRevision.proposalId !== profile.proposalId ||
    !sameCompositionProfile(firstRevision.value, profile.value) ||
    new Set(parsedRevisions.map((item) => item.revision)).size !==
      parsedRevisions.length ||
    parsedRevisions.some(
      (item, index) => item.revision !== profile.revision - index,
    ) ||
    new Set(parsedProposals.map((item) => item.id)).size !==
      parsedProposals.length ||
    new Set(parsedSlots.map((item) => item.id)).size !==
      COMPOSITION_SLOT_IDS.length ||
    new Set(parsedAudit.map((item) => item.sequence)).size !==
      parsedAudit.length ||
    parsedAudit.some(
      (item, index) =>
        index > 0 && item.sequence >= parsedAudit[index - 1]!.sequence,
    ) ||
    parsedAudit.some(
      (item) =>
        (item.eventType === "workspace_composition_proposed" &&
          item.payload.baseRevision > profile.revision) ||
        (item.eventType === "workspace_composition_applied" &&
          item.payload.revision > profile.revision),
    ) ||
    parsedRevisions.some((item) => item.workspaceId !== profile.workspaceId) ||
    parsedProposals.some((item) => item.workspaceId !== profile.workspaceId)
  ) {
    return null;
  }
  return Object.freeze({
    profile,
    revisions: Object.freeze(parsedRevisions),
    proposals: Object.freeze(parsedProposals),
    slotCatalog: Object.freeze(parsedSlots),
    audit: Object.freeze(parsedAudit),
  });
}

function parseCompositionDecisionResult(
  value: unknown,
): DesktopWorkspaceCompositionDecisionResult | null {
  if (
    !isRecord(value) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(value.workspace_id) ||
    typeof value.proposal_id !== "string" ||
    !COMPOSITION_PROPOSAL_ID_PATTERN.test(value.proposal_id) ||
    typeof value.request_sha256 !== "string" ||
    !SHA256_PATTERN.test(value.request_sha256)
  ) {
    return null;
  }
  if (
    hasExactKeys(value, [
      "applied_revision",
      "decision",
      "proposal_id",
      "request_sha256",
      "workspace_id",
    ]) &&
    value.decision === "rejected" &&
    value.applied_revision === null
  ) {
    return Object.freeze({
      workspaceId: value.workspace_id,
      proposalId: value.proposal_id,
      requestSha256: value.request_sha256,
      decision: "rejected",
      appliedRevision: null,
    });
  }
  if (
    !hasExactKeys(value, [
      "applied_revision",
      "decision",
      "profile",
      "proposal_id",
      "request_sha256",
      "workspace_id",
    ]) ||
    value.decision !== "approved" ||
    typeof value.applied_revision !== "number" ||
    !Number.isInteger(value.applied_revision) ||
    value.applied_revision < 2
  ) {
    return null;
  }
  const profile = parseCompositionRevision(value.profile);
  if (profile === null || profile.revision !== value.applied_revision)
    return null;
  return Object.freeze({
    workspaceId: value.workspace_id,
    proposalId: value.proposal_id,
    requestSha256: value.request_sha256,
    decision: "approved",
    appliedRevision: value.applied_revision,
    profile,
  });
}

function compositionProfilePayload(
  value: DesktopWorkspaceCompositionProfileValue,
): Readonly<Record<string, unknown>> {
  return Object.freeze({
    appearance: Object.freeze({
      density: value.appearance.density,
      quiet_chrome: value.appearance.quietChrome,
    }),
    layout: Object.freeze({
      agent_panel: value.layout.agentPanel,
      bottom_panel: value.layout.bottomPanel,
      focus_mode: value.layout.focusMode,
      sidebar: value.layout.sidebar,
    }),
    schema_version: value.schemaVersion,
    slots: Object.freeze(
      Object.fromEntries(
        COMPOSITION_SLOT_IDS.map((slotId) => [slotId, value.slots[slotId]]),
      ),
    ),
    template: Object.freeze({
      id: value.template.id,
      version: value.template.version,
    }),
  });
}

function sha256Json(value: Readonly<Record<string, unknown>>): string {
  return createHash("sha256")
    .update(JSON.stringify(value), "utf8")
    .digest("hex");
}

function compositionProfileSha256(
  value: DesktopWorkspaceCompositionProfileValue,
): string {
  return sha256Json(compositionProfilePayload(value));
}

function compositionRequestSha256(
  input: Readonly<{
    workspaceId: string;
    baseRevision: number;
    baseProfileSha256: string;
    sourceKind: "owner" | "assistant" | "rollback";
    sourceReference: string | null;
    desiredProfileSha256: string;
  }>,
): string {
  return sha256Json(
    Object.freeze({
      base_profile_sha256: input.baseProfileSha256,
      base_revision: input.baseRevision,
      desired_profile_sha256: input.desiredProfileSha256,
      schema_version: 1,
      source_kind: input.sourceKind,
      source_reference: input.sourceReference,
      template: Object.freeze({ id: "standard-workbench", version: 1 }),
      workspace_id: input.workspaceId,
    }),
  );
}

function validRevision(value: number): boolean {
  return Number.isInteger(value) && value >= 1 && value <= 2_147_483_647;
}

function sameCompositionProfile(
  left: DesktopWorkspaceCompositionProfileValue,
  right: DesktopWorkspaceCompositionProfileValue,
): boolean {
  return (
    JSON.stringify(compositionProfilePayload(left)) ===
    JSON.stringify(compositionProfilePayload(right))
  );
}

function parseErrorCode(value: unknown): string | null {
  if (
    !isRecord(value) ||
    !isRecord(value.error) ||
    typeof value.error.code !== "string" ||
    !ERROR_CODE_PATTERN.test(value.error.code)
  ) {
    return null;
  }
  return value.error.code;
}

function validateBackendOrigin(value: string): string {
  let parsed: URL;
  try {
    parsed = new URL(value);
  } catch {
    throw new Error("desktop_native_origin_invalid");
  }
  const port = Number(parsed.port);
  if (
    parsed.protocol !== "http:" ||
    parsed.hostname !== "127.0.0.1" ||
    parsed.username !== "" ||
    parsed.password !== "" ||
    parsed.pathname !== "/" ||
    parsed.search !== "" ||
    parsed.hash !== "" ||
    parsed.port === "" ||
    !Number.isInteger(port) ||
    port < 1 ||
    port > 65_535
  ) {
    throw new Error("desktop_native_origin_invalid");
  }
  return parsed.origin;
}

async function readBoundedJson(
  response: Response,
  limit: number = MAX_RESPONSE_BYTES,
): Promise<unknown> {
  const declared = response.headers.get("content-length");
  if (declared !== null) {
    const parsed = Number(declared);
    if (!Number.isSafeInteger(parsed) || parsed < 0 || parsed > limit) {
      throw new Error("desktop_native_response_invalid");
    }
  }
  if (response.body === null)
    throw new Error("desktop_native_response_invalid");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > limit) {
        await reader.cancel();
        throw new Error("desktop_native_response_invalid");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const payload = Buffer.concat(chunks, total).toString("utf8");
  try {
    return JSON.parse(payload) as unknown;
  } catch {
    throw new Error("desktop_native_response_invalid");
  }
}

function parseParentAgent(
  value: unknown,
): { readonly agent: DesktopParentAgent } | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["agent"]) ||
    !isRecord(value.agent)
  ) {
    return null;
  }
  const agent = value.agent;
  if (
    !hasExactKeys(agent, [
      "created_at",
      "display_name",
      "id",
      "role",
      "updated_at",
      "workspace_id",
    ]) ||
    typeof agent.id !== "string" ||
    !AGENT_ID_PATTERN.test(agent.id) ||
    typeof agent.workspace_id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(agent.workspace_id) ||
    agent.role !== "parent" ||
    !isBoundedString(agent.display_name, 256) ||
    !isBoundedString(agent.created_at, 64) ||
    !isBoundedString(agent.updated_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    agent: Object.freeze({
      id: agent.id,
      workspaceId: agent.workspace_id,
      role: "parent",
      displayName: agent.display_name,
      createdAt: agent.created_at,
      updatedAt: agent.updated_at,
    }),
  });
}

function parseProvider(value: unknown): DesktopProvider | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "allow_loopback_http",
      "base_url",
      "created_at",
      "display_name",
      "family",
      "gear",
      "has_secret",
      "id",
      "is_default",
      "is_enabled",
      "model_name",
      "thinking_depth",
      "timeout_seconds",
      "updated_at",
    ]) ||
    typeof value.id !== "string" ||
    !PROVIDER_ID_PATTERN.test(value.id) ||
    !isBoundedString(value.display_name, 256) ||
    !isBoundedString(value.base_url, 2048) ||
    !isBoundedString(value.model_name, 256) ||
    typeof value.family !== "string" ||
    !FAMILIES.has(value.family) ||
    typeof value.gear !== "string" ||
    !GEARS.has(value.gear) ||
    typeof value.thinking_depth !== "string" ||
    !DEPTHS.has(value.thinking_depth) ||
    typeof value.timeout_seconds !== "number" ||
    !Number.isInteger(value.timeout_seconds) ||
    value.timeout_seconds < 5 ||
    value.timeout_seconds > 120 ||
    typeof value.allow_loopback_http !== "boolean" ||
    typeof value.is_default !== "boolean" ||
    typeof value.is_enabled !== "boolean" ||
    value.has_secret !== true ||
    !isBoundedString(value.created_at, 64) ||
    !isBoundedString(value.updated_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    displayName: value.display_name,
    baseUrl: value.base_url,
    modelName: value.model_name,
    family: value.family as DesktopProvider["family"],
    gear: value.gear as DesktopProvider["gear"],
    thinkingDepth: value.thinking_depth as DesktopProvider["thinkingDepth"],
    timeoutSeconds: value.timeout_seconds,
    allowLoopbackHttp: value.allow_loopback_http,
    isDefault: value.is_default,
    isEnabled: value.is_enabled,
    hasSecret: true as const,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseProviderList(value: unknown): DesktopProviderList | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["items"]) ||
    !Array.isArray(value.items)
  ) {
    return null;
  }
  const items: DesktopProvider[] = [];
  const identifiers = new Set<string>();
  for (const candidate of value.items) {
    const provider = parseProvider(candidate);
    if (provider === null || identifiers.has(provider.id)) return null;
    identifiers.add(provider.id);
    items.push(provider);
  }
  return Object.freeze({ items: Object.freeze(items) });
}

function parseProviderMutation(
  value: unknown,
): DesktopProviderMutationResult | null {
  if (!isRecord(value) || !hasExactKeys(value, ["provider"])) return null;
  const provider = parseProvider(value.provider);
  return provider === null ? null : Object.freeze({ provider });
}

function parseProviderDeleted(
  value: unknown,
): { readonly deleted: true; readonly id: string } | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["deleted", "id"]) ||
    value.deleted !== true ||
    typeof value.id !== "string" ||
    !PROVIDER_ID_PATTERN.test(value.id)
  ) {
    return null;
  }
  return Object.freeze({ deleted: true as const, id: value.id });
}

function parseProviderVault(
  value: unknown,
): { encryptedSecretBlob: string } | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "credential_reference",
      "encrypted_secret_blob",
      "id",
    ]) ||
    typeof value.id !== "string" ||
    !PROVIDER_ID_PATTERN.test(value.id) ||
    value.credential_reference !== "electron-safe-storage:v1" ||
    !isBoundedString(value.encrypted_secret_blob, 8192)
  ) {
    return null;
  }
  return Object.freeze({ encryptedSecretBlob: value.encrypted_secret_blob });
}

function parseProviderTest(value: unknown): DesktopProviderTestResult | null {
  if (!isRecord(value) || typeof value.ok !== "boolean") return null;
  if (
    typeof value.provider_id !== "string" ||
    !PROVIDER_ID_PATTERN.test(value.provider_id) ||
    !isBoundedString(value.provider_name, 256) ||
    !isBoundedString(value.requested_model, 256) ||
    typeof value.family !== "string"
  ) {
    return null;
  }
  if (value.ok) {
    if (
      typeof value.identity_proven !== "boolean" ||
      typeof value.latency_ms !== "number"
    ) {
      return null;
    }
    if (value.identity_proven) {
      if (
        typeof value.actual_model !== "string" ||
        value.actual_model.length === 0
      ) {
        return null;
      }
    } else if (value.actual_model !== null) {
      return null;
    }
    const provenModel =
      value.identity_proven && typeof value.actual_model === "string"
        ? value.actual_model
        : null;
    return Object.freeze({
      ok: true,
      providerId: value.provider_id,
      providerName: value.provider_name,
      requestedModel: value.requested_model,
      actualModel: provenModel,
      identityProven: value.identity_proven,
      family: value.family,
      latencyMs: value.latency_ms,
    });
  }
  if (
    typeof value.error_code !== "string" ||
    typeof value.error_redacted !== "string"
  ) {
    return null;
  }
  return Object.freeze({
    ok: false,
    providerId: value.provider_id,
    providerName: value.provider_name,
    requestedModel: value.requested_model,
    actualModel: null,
    identityProven: false,
    family: value.family,
    errorCode: value.error_code,
    errorRedacted: value.error_redacted,
  });
}

function parsePinnedEndpoint(value: unknown): {
  readonly scheme: "http" | "https";
  readonly hostname: string;
  readonly port: number;
  readonly chatPath: string;
  readonly connectAddrs: readonly string[];
  readonly loopback: boolean;
} | null {
  if (
    !isRecord(value) ||
    (value.scheme !== "http" && value.scheme !== "https") ||
    typeof value.hostname !== "string" ||
    value.hostname.length === 0 ||
    typeof value.port !== "number" ||
    !Number.isInteger(value.port) ||
    value.port < 1 ||
    value.port > 65535 ||
    typeof value.chat_path !== "string" ||
    value.chat_path.length === 0 ||
    !Array.isArray(value.connect_addrs) ||
    value.connect_addrs.length === 0 ||
    typeof value.loopback !== "boolean"
  ) {
    return null;
  }
  const connectAddrs: string[] = [];
  for (const item of value.connect_addrs) {
    if (typeof item !== "string" || item.length === 0) return null;
    connectAddrs.push(item);
  }
  return Object.freeze({
    scheme: value.scheme,
    hostname: value.hostname,
    port: value.port,
    chatPath: value.chat_path,
    connectAddrs: Object.freeze(connectAddrs),
    loopback: value.loopback,
  });
}

function parseConversation(value: unknown): DesktopConversation | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "created_at",
      "id",
      "row_version",
      "state",
      "title",
      "updated_at",
      "workspace_id",
    ]) ||
    typeof value.id !== "string" ||
    !CONVERSATION_ID_PATTERN.test(value.id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(value.workspace_id) ||
    !isBoundedString(value.title, 256) ||
    (value.state !== "active" && value.state !== "archived") ||
    typeof value.row_version !== "number" ||
    !Number.isInteger(value.row_version) ||
    value.row_version < 1 ||
    !isBoundedString(value.created_at, 64) ||
    !isBoundedString(value.updated_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    workspaceId: value.workspace_id,
    title: value.title,
    state: value.state,
    rowVersion: value.row_version,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseConversationList(value: unknown): DesktopConversationList | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["items"]) ||
    !Array.isArray(value.items)
  ) {
    return null;
  }
  const items: DesktopConversation[] = [];
  for (const candidate of value.items) {
    const conversation = parseConversation(candidate);
    if (conversation === null) return null;
    items.push(conversation);
  }
  return Object.freeze({ items: Object.freeze(items) });
}

function parseConversationCreated(value: unknown): {
  readonly created: true;
  readonly conversation: DesktopConversation;
} | null {
  if (!isRecord(value) || value.created !== true) return null;
  const conversation = parseConversation(value.conversation);
  return conversation === null
    ? null
    : Object.freeze({ created: true as const, conversation });
}

function parseConversationArchived(
  value: unknown,
): { readonly conversation: DesktopConversation } | null {
  if (!isRecord(value) || !hasExactKeys(value, ["conversation"])) return null;
  const conversation = parseConversation(value.conversation);
  return conversation === null ? null : Object.freeze({ conversation });
}

function optionalNonNegative(value: unknown): number | null {
  if (value === null) return null;
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : Number.NaN;
}

function parseInvocation(value: unknown): DesktopInvocation | null {
  if (!isRecord(value)) return null;
  const duration = optionalNonNegative(value.duration_ms);
  const inputTokens = optionalNonNegative(value.input_tokens);
  const outputTokens = optionalNonNegative(value.output_tokens);
  const totalTokens = optionalNonNegative(value.total_tokens);
  if (
    typeof value.id !== "string" ||
    !INVOCATION_ID_PATTERN.test(value.id) ||
    typeof value.provider_id !== "string" ||
    !PROVIDER_ID_PATTERN.test(value.provider_id) ||
    !isBoundedString(value.requested_model, 256) ||
    (value.actual_model !== null &&
      !isBoundedString(value.actual_model, 256)) ||
    typeof value.family !== "string" ||
    typeof value.gear !== "string" ||
    typeof value.thinking_depth !== "string" ||
    (value.status !== "running" &&
      value.status !== "succeeded" &&
      value.status !== "failed" &&
      value.status !== "cancelled" &&
      value.status !== "unknown") ||
    Number.isNaN(duration) ||
    Number.isNaN(inputTokens) ||
    Number.isNaN(outputTokens) ||
    Number.isNaN(totalTokens) ||
    (value.error_code !== null && typeof value.error_code !== "string") ||
    (value.error_redacted !== null &&
      typeof value.error_redacted !== "string") ||
    (value.retry_of_invocation_id !== null &&
      (typeof value.retry_of_invocation_id !== "string" ||
        !INVOCATION_ID_PATTERN.test(value.retry_of_invocation_id))) ||
    !isBoundedString(value.created_at, 64) ||
    !isBoundedString(value.updated_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    providerId: value.provider_id,
    requestedModel: value.requested_model,
    actualModel: value.actual_model,
    family: value.family,
    gear: value.gear,
    thinkingDepth: value.thinking_depth,
    status: value.status,
    durationMs: duration,
    inputTokens,
    outputTokens,
    totalTokens,
    errorCode: value.error_code,
    errorRedacted: value.error_redacted,
    retryOfInvocationId: value.retry_of_invocation_id,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseMessage(value: unknown): DesktopMessage | null {
  if (!isRecord(value)) return null;
  const invocation =
    value.invocation === null ? null : parseInvocation(value.invocation);
  if (
    typeof value.id !== "string" ||
    !MESSAGE_ID_PATTERN.test(value.id) ||
    (value.role !== "user" && value.role !== "assistant") ||
    typeof value.content !== "string" ||
    value.content.length > 131072 ||
    (value.status !== "streaming" &&
      value.status !== "completed" &&
      value.status !== "cancelled" &&
      value.status !== "failed" &&
      value.status !== "unknown") ||
    (value.invocation_id !== null &&
      (typeof value.invocation_id !== "string" ||
        !INVOCATION_ID_PATTERN.test(value.invocation_id))) ||
    (value.retry_of_message_id !== null &&
      (typeof value.retry_of_message_id !== "string" ||
        !MESSAGE_ID_PATTERN.test(value.retry_of_message_id))) ||
    !isBoundedString(value.created_at, 64) ||
    (value.invocation !== null && invocation === null)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    role: value.role,
    content: value.content,
    status: value.status,
    invocationId: value.invocation_id,
    retryOfMessageId: value.retry_of_message_id,
    createdAt: value.created_at,
    invocation,
  });
}

function parseConversationDetail(
  value: unknown,
): DesktopConversationDetail | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["conversation", "messages"]) ||
    !Array.isArray(value.messages)
  ) {
    return null;
  }
  const conversation = parseConversation(value.conversation);
  if (conversation === null) return null;
  const messages: DesktopMessage[] = [];
  for (const candidate of value.messages) {
    const message = parseMessage(candidate);
    if (message === null) return null;
    messages.push(message);
  }
  return Object.freeze({
    conversation,
    messages: Object.freeze(messages),
  });
}

function parseCancelResult(value: unknown): {
  readonly cancelled: boolean;
  readonly id: string;
  readonly accepted: boolean;
} | null {
  if (
    !isRecord(value) ||
    typeof value.cancelled !== "boolean" ||
    typeof value.accepted !== "boolean" ||
    typeof value.id !== "string" ||
    !INVOCATION_ID_PATTERN.test(value.id)
  ) {
    return null;
  }
  return Object.freeze({
    cancelled: value.cancelled,
    id: value.id,
    accepted: value.accepted,
  });
}

function parseStreamEvent(
  eventName: string,
  data: string,
): DesktopConversationEvent | null {
  let payload: unknown;
  try {
    payload = JSON.parse(data) as unknown;
  } catch {
    return null;
  }
  if (
    !isRecord(payload) ||
    typeof payload.invocation_id !== "string" ||
    typeof payload.workspace_id !== "string" ||
    typeof payload.conversation_id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(payload.workspace_id) ||
    !CONVERSATION_ID_PATTERN.test(payload.conversation_id)
  ) {
    return null;
  }
  const scoped = {
    workspaceId: payload.workspace_id,
    conversationId: payload.conversation_id,
    invocationId: payload.invocation_id,
    messageId:
      typeof payload.message_id === "string" ? payload.message_id : undefined,
  };
  if (eventName === "delta") {
    if (typeof payload.text !== "string") return null;
    return Object.freeze({
      type: "delta",
      ...scoped,
      text: payload.text,
    });
  }
  if (eventName === "identity") {
    return Object.freeze({
      type: "identity",
      ...scoped,
      providerName:
        typeof payload.provider_name === "string"
          ? payload.provider_name
          : undefined,
      requestedModel:
        typeof payload.requested_model === "string"
          ? payload.requested_model
          : undefined,
      family: typeof payload.family === "string" ? payload.family : undefined,
      gear: typeof payload.gear === "string" ? payload.gear : undefined,
      thinkingDepth:
        typeof payload.thinking_depth === "string"
          ? payload.thinking_depth
          : undefined,
    });
  }
  if (
    eventName === "done" ||
    eventName === "cancelled" ||
    eventName === "error"
  ) {
    return Object.freeze({
      type: eventName,
      ...scoped,
      answer: typeof payload.answer === "string" ? payload.answer : undefined,
      actualModel:
        payload.actual_model === null ||
        typeof payload.actual_model === "string"
          ? payload.actual_model
          : undefined,
      status: typeof payload.status === "string" ? payload.status : undefined,
      durationMs:
        typeof payload.duration_ms === "number"
          ? payload.duration_ms
          : undefined,
      inputTokens:
        payload.input_tokens === null ||
        typeof payload.input_tokens === "number"
          ? payload.input_tokens
          : undefined,
      outputTokens:
        payload.output_tokens === null ||
        typeof payload.output_tokens === "number"
          ? payload.output_tokens
          : undefined,
      totalTokens:
        payload.total_tokens === null ||
        typeof payload.total_tokens === "number"
          ? payload.total_tokens
          : undefined,
      errorCode:
        typeof payload.error_code === "string" ? payload.error_code : undefined,
      errorRedacted:
        typeof payload.error_redacted === "string"
          ? payload.error_redacted
          : undefined,
    });
  }
  return null;
}

function stampSendEpoch(
  event: DesktopConversationEvent,
  sendEpoch: number | undefined,
): DesktopConversationEvent {
  return sendEpoch === undefined
    ? event
    : Object.freeze({ ...event, sendEpoch });
}

async function releaseStreamReader(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): Promise<void> {
  try {
    await reader.cancel();
  } catch {
    try {
      reader.releaseLock();
    } catch {
      return;
    }
  }
}

async function readConversationStream(
  response: Response,
  emit: (event: DesktopConversationEvent) => void,
  signal: AbortSignal,
  abandon: (invocationId: string) => Promise<void>,
  sendEpoch?: number,
): Promise<DesktopOperationResult<DesktopConversationEvent>> {
  if (response.body === null) return failure("desktop_native_response_invalid");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let terminal: DesktopConversationEvent | null = null;
  let invocationId: string | undefined;
  const onAbort = () => {
    void reader.cancel().catch(() => undefined);
  };
  signal.addEventListener("abort", onAbort, { once: true });
  try {
    if (signal.aborted) {
      return success(
        stampSendEpoch(
          Object.freeze({
            type: "cancelled",
            invocationId: "invocation_cancelled_locally",
            errorRedacted: "生成已停止",
          }) satisfies DesktopConversationEvent,
          sendEpoch,
        ),
      );
    }
    while (!signal.aborted) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      buffer = buffer.replaceAll("\r\n", "\n");
      while (buffer.includes("\n\n")) {
        const index = buffer.indexOf("\n\n");
        const raw = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        let eventName = "message";
        const dataLines: string[] = [];
        for (const line of raw.split("\n")) {
          if (line.startsWith("event:"))
            eventName = line.slice(6).trim() || "message";
          if (line.startsWith("data:"))
            dataLines.push(line.slice(5).trimStart());
        }
        const parsed = parseStreamEvent(eventName, dataLines.join("\n"));
        if (parsed === null) continue;
        const stamped = stampSendEpoch(parsed, sendEpoch);
        if (stamped.type === "identity") invocationId = stamped.invocationId;
        emit(stamped);
        if (
          stamped.type === "done" ||
          stamped.type === "cancelled" ||
          stamped.type === "error"
        ) {
          terminal = stamped;
        }
      }
    }
  } finally {
    signal.removeEventListener("abort", onAbort);
    await releaseStreamReader(reader);
    if (terminal === null && invocationId !== undefined) {
      try {
        await abandon(invocationId);
      } catch {
        // Backend disconnect terminalization is the durable fallback.
      }
    }
  }
  if (signal.aborted && terminal === null) {
    return success(
      stampSendEpoch(
        Object.freeze({
          type: "cancelled",
          invocationId: invocationId ?? "invocation_cancelled_locally",
          errorRedacted: "生成已停止",
        }) satisfies DesktopConversationEvent,
        sendEpoch,
      ),
    );
  }
  return terminal === null
    ? failure("desktop_native_request_failed")
    : success(terminal);
}

function parseAgentRole(value: unknown): DesktopAgentRole | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "default_state",
      "display_name",
      "gear",
      "has_secret",
      "id",
      "inherited_provider",
      "may_join_team",
      "model_name_override",
      "provider_id",
      "resolved_model_name",
      "resolved_provider_id",
      "responsibility",
      "row_version",
      "secret_fingerprint",
      "thinking_depth",
      "verification_state",
      "verified_actual_model",
    ]) ||
    typeof value.id !== "string" ||
    !EMPLOYEE_ROLE_SET.has(value.id) ||
    !isBoundedString(value.display_name, 256) ||
    !isBoundedString(value.responsibility, 256) ||
    (value.default_state !== "active" && value.default_state !== "dormant") ||
    typeof value.may_join_team !== "boolean" ||
    (value.provider_id !== null &&
      (typeof value.provider_id !== "string" ||
        !PROVIDER_ID_PATTERN.test(value.provider_id))) ||
    (value.model_name_override !== null &&
      !isBoundedString(value.model_name_override, 256)) ||
    !isBoundedString(value.gear, 32) ||
    !isBoundedString(value.thinking_depth, 32) ||
    typeof value.row_version !== "number" ||
    !Number.isInteger(value.row_version) ||
    value.row_version < 1 ||
    (value.verification_state !== "unverified" &&
      value.verification_state !== "binding_recorded" &&
      value.verification_state !== "stale") ||
    (value.verified_actual_model !== null &&
      !isBoundedString(value.verified_actual_model, 256)) ||
    typeof value.inherited_provider !== "boolean" ||
    (value.resolved_provider_id !== null &&
      (typeof value.resolved_provider_id !== "string" ||
        !PROVIDER_ID_PATTERN.test(value.resolved_provider_id))) ||
    (value.resolved_model_name !== null &&
      !isBoundedString(value.resolved_model_name, 256)) ||
    (value.secret_fingerprint !== null &&
      (typeof value.secret_fingerprint !== "string" ||
        value.secret_fingerprint.length !== 64)) ||
    typeof value.has_secret !== "boolean"
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id as PersonalEmployeeId,
    displayName: value.display_name,
    responsibility: value.responsibility,
    defaultState: value.default_state,
    mayJoinTeam: value.may_join_team,
    providerId: value.provider_id,
    modelNameOverride: value.model_name_override,
    gear: value.gear,
    thinkingDepth: value.thinking_depth,
    rowVersion: value.row_version,
    verificationState: value.verification_state,
    verifiedActualModel: value.verified_actual_model,
    inheritedProvider: value.inherited_provider,
    resolvedProviderId: value.resolved_provider_id,
    resolvedModelName: value.resolved_model_name,
    secretFingerprint: value.secret_fingerprint,
    hasSecret: value.has_secret,
  });
}

function parseAgentRoleList(value: unknown): DesktopAgentRoleList | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["items"]) ||
    !Array.isArray(value.items)
  ) {
    return null;
  }
  const items: DesktopAgentRole[] = [];
  for (const candidate of value.items) {
    const role = parseAgentRole(candidate);
    if (role === null) return null;
    items.push(role);
  }
  return Object.freeze({ items: Object.freeze(items) });
}

function parseAgentRoleWrapper(
  value: unknown,
): { readonly role: DesktopAgentRole } | null {
  if (!isRecord(value) || !hasExactKeys(value, ["role"])) return null;
  const role = parseAgentRole(value.role);
  return role === null ? null : Object.freeze({ role });
}

function parseAgentRoleTest(value: unknown): DesktopAgentRoleTestResult | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "identity_proven",
      "inherited_provider",
      "ok",
      "provider_id",
      "requested_model",
      "role_id",
      "secret_fingerprint",
      "verification_digest",
      "workspace_id",
    ]) ||
    value.ok !== true ||
    typeof value.role_id !== "string" ||
    !EMPLOYEE_ROLE_SET.has(value.role_id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(value.workspace_id) ||
    typeof value.provider_id !== "string" ||
    !PROVIDER_ID_PATTERN.test(value.provider_id) ||
    typeof value.inherited_provider !== "boolean" ||
    !isBoundedString(value.requested_model, 256) ||
    typeof value.secret_fingerprint !== "string" ||
    value.secret_fingerprint.length !== 64 ||
    typeof value.verification_digest !== "string" ||
    value.verification_digest.length !== 64 ||
    value.identity_proven !== false
  ) {
    return null;
  }
  return Object.freeze({
    ok: true as const,
    roleId: value.role_id as PersonalEmployeeId,
    workspaceId: value.workspace_id,
    providerId: value.provider_id,
    inheritedProvider: value.inherited_provider,
    requestedModel: value.requested_model,
    secretFingerprint: value.secret_fingerprint,
    verificationDigest: value.verification_digest,
    identityProven: false as const,
  });
}

function parseTeamRun(value: unknown): DesktopTeamRun | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "allowed_specialist_role_ids",
      "consumed_provider_calls",
      "conversation_id",
      "created_at",
      "current_plan_revision_id",
      "current_wave_id",
      "dispatched_participant_count",
      "id",
      "maximum_concurrent_calls",
      "maximum_input_characters",
      "maximum_output_characters",
      "maximum_provider_calls",
      "maximum_wall_time_ms",
      "mode",
      "staffing_authority",
      "state",
      "task",
      "updated_at",
      "workspace_id",
    ]) ||
    typeof value.id !== "string" ||
    !TEAM_RUN_ID_PATTERN.test(value.id) ||
    typeof value.workspace_id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(value.workspace_id) ||
    typeof value.conversation_id !== "string" ||
    !CONVERSATION_ID_PATTERN.test(value.conversation_id) ||
    (value.mode !== "single" && value.mode !== "team") ||
    typeof value.state !== "string" ||
    !TEAM_RUN_STATES.has(value.state) ||
    value.staffing_authority !== "parent_proposal" ||
    (value.current_plan_revision_id !== null &&
      (typeof value.current_plan_revision_id !== "string" ||
        !TEAM_REV_ID_PATTERN.test(value.current_plan_revision_id))) ||
    (value.current_wave_id !== null &&
      !isBoundedString(value.current_wave_id, 128)) ||
    (value.dispatched_participant_count !== null &&
      (typeof value.dispatched_participant_count !== "number" ||
        !Number.isInteger(value.dispatched_participant_count))) ||
    typeof value.maximum_provider_calls !== "number" ||
    typeof value.maximum_wall_time_ms !== "number" ||
    typeof value.maximum_concurrent_calls !== "number" ||
    typeof value.maximum_input_characters !== "number" ||
    typeof value.maximum_output_characters !== "number" ||
    typeof value.consumed_provider_calls !== "number" ||
    !isBoundedString(value.task, 16384) ||
    !Array.isArray(value.allowed_specialist_role_ids) ||
    value.allowed_specialist_role_ids.some(
      (role) => typeof role !== "string" || !SPECIALIST_ROLE_SET.has(role),
    ) ||
    !isBoundedString(value.created_at, 64) ||
    !isBoundedString(value.updated_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    workspaceId: value.workspace_id,
    conversationId: value.conversation_id,
    mode: value.mode,
    state: value.state as TeamRunState,
    staffingAuthority: "parent_proposal",
    currentPlanRevisionId: value.current_plan_revision_id,
    currentWaveId: value.current_wave_id,
    dispatchedParticipantCount: value.dispatched_participant_count,
    maximumProviderCalls: value.maximum_provider_calls,
    maximumWallTimeMs: value.maximum_wall_time_ms,
    maximumConcurrentCalls: value.maximum_concurrent_calls,
    maximumInputCharacters: value.maximum_input_characters,
    maximumOutputCharacters: value.maximum_output_characters,
    consumedProviderCalls: value.consumed_provider_calls,
    task: value.task,
    allowedSpecialistRoleIds: Object.freeze(
      value.allowed_specialist_role_ids as SpecialistEmployeeId[],
    ),
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseTeamRunWrapper(
  value: unknown,
): { readonly teamRun: DesktopTeamRun } | null {
  if (!isRecord(value) || !hasExactKeys(value, ["team_run"])) return null;
  const teamRun = parseTeamRun(value.team_run);
  return teamRun === null ? null : Object.freeze({ teamRun });
}

function parseTeamParentCall(
  value: unknown,
): DesktopTeamParentCallRecord | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "actual_model",
      "created_at",
      "error_code",
      "input_tokens",
      "invocation_id",
      "output_sha256",
      "output_tokens",
      "plan_revision_id",
      "provider_id",
      "purpose",
      "requested_model",
      "state",
      "team_run_id",
      "total_tokens",
      "updated_at",
    ]) ||
    typeof value.invocation_id !== "string" ||
    !INVOCATION_ID_PATTERN.test(value.invocation_id) ||
    typeof value.team_run_id !== "string" ||
    !TEAM_RUN_ID_PATTERN.test(value.team_run_id) ||
    (value.plan_revision_id !== null &&
      (typeof value.plan_revision_id !== "string" ||
        !TEAM_REV_ID_PATTERN.test(value.plan_revision_id))) ||
    typeof value.purpose !== "string" ||
    !TEAM_PARENT_CALL_PURPOSES.has(value.purpose) ||
    typeof value.state !== "string" ||
    !TEAM_PARENT_CALL_STATES.has(value.state) ||
    typeof value.provider_id !== "string" ||
    !PROVIDER_ID_PATTERN.test(value.provider_id) ||
    !isBoundedString(value.requested_model, 256) ||
    (value.actual_model !== null &&
      !isBoundedString(value.actual_model, 256)) ||
    !isNullableNonNegativeInteger(value.input_tokens) ||
    !isNullableNonNegativeInteger(value.output_tokens) ||
    !isNullableNonNegativeInteger(value.total_tokens) ||
    (value.output_sha256 !== null &&
      (typeof value.output_sha256 !== "string" ||
        !TOKEN_PATTERN.test(value.output_sha256))) ||
    (value.error_code !== null &&
      (typeof value.error_code !== "string" ||
        !ERROR_CODE_PATTERN.test(value.error_code))) ||
    !isBoundedString(value.created_at, 64) ||
    !isBoundedString(value.updated_at, 64)
  ) {
    return null;
  }
  const pendingProofValid =
    value.state !== "pending" ||
    (value.plan_revision_id === null &&
      value.actual_model === null &&
      value.input_tokens === null &&
      value.output_tokens === null &&
      value.total_tokens === null &&
      value.output_sha256 === null &&
      value.error_code === null);
  const usageValid =
    (value.input_tokens === null &&
      value.output_tokens === null &&
      value.total_tokens === null) ||
    (value.input_tokens !== null &&
      value.output_tokens !== null &&
      value.total_tokens !== null &&
      value.total_tokens === value.input_tokens + value.output_tokens);
  const successProofValid =
    value.state !== "succeeded" ||
    (value.plan_revision_id !== null &&
      value.actual_model === value.requested_model &&
      value.output_sha256 !== null &&
      value.error_code === null);
  const failureProofValid =
    (value.state !== "failed" &&
      value.state !== "cancelled" &&
      value.state !== "unknown") ||
    (value.output_sha256 === null && value.error_code !== null);
  if (
    !usageValid ||
    !pendingProofValid ||
    !successProofValid ||
    !failureProofValid
  ) {
    return null;
  }
  return Object.freeze({
    invocationId: value.invocation_id,
    teamRunId: value.team_run_id,
    planRevisionId: value.plan_revision_id,
    purpose: value.purpose as DesktopTeamParentCallRecord["purpose"],
    state: value.state as DesktopTeamParentCallRecord["state"],
    providerId: value.provider_id,
    requestedModel: value.requested_model,
    actualModel: value.actual_model,
    inputTokens: value.input_tokens,
    outputTokens: value.output_tokens,
    totalTokens: value.total_tokens,
    outputSha256: value.output_sha256,
    errorCode: value.error_code,
    createdAt: value.created_at,
    updatedAt: value.updated_at,
  });
}

function parseTeamProviderCallConsume(value: unknown): {
  readonly teamRun: DesktopTeamRun;
  readonly parentCall?: DesktopTeamParentCallRecord;
} | null {
  if (!isRecord(value) || !hasExactKeys(value, ["parent_call", "team_run"])) {
    return null;
  }
  const teamRun = parseTeamRun(value.team_run);
  if (teamRun === null) return null;
  if (value.parent_call === null) return Object.freeze({ teamRun });
  const parentCall = parseTeamParentCall(value.parent_call);
  return parentCall === null ? null : Object.freeze({ teamRun, parentCall });
}

function parseTeamParentCallWrapper(value: unknown): {
  readonly parentCall: DesktopTeamParentCallRecord;
} | null {
  if (!isRecord(value) || !hasExactKeys(value, ["parent_call"])) return null;
  const parentCall = parseTeamParentCall(value.parent_call);
  return parentCall === null ? null : Object.freeze({ parentCall });
}

function parseTeamRunList(
  value: unknown,
): { readonly items: readonly DesktopTeamRun[] } | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["items"]) ||
    !Array.isArray(value.items)
  ) {
    return null;
  }
  const items: DesktopTeamRun[] = [];
  for (const candidate of value.items) {
    const item = parseTeamRun(candidate);
    if (item === null) return null;
    items.push(item);
  }
  return Object.freeze({ items: Object.freeze(items) });
}

function parseTeamRunCancel(value: unknown): {
  readonly cancelled: boolean;
  readonly accepted: boolean;
  readonly teamRun: DesktopTeamRun;
} | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["accepted", "cancelled", "team_run"]) ||
    typeof value.cancelled !== "boolean" ||
    typeof value.accepted !== "boolean"
  ) {
    return null;
  }
  const teamRun = parseTeamRun(value.team_run);
  return teamRun === null
    ? null
    : Object.freeze({
        cancelled: value.cancelled,
        accepted: value.accepted,
        teamRun,
      });
}

function parsePlanRevision(value: unknown): DesktopTeamPlanRevision | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "created_at",
      "decision",
      "id",
      "proposal_json_sha256",
      "revision_ordinal",
      "validated",
      "validation_error_code",
    ]) ||
    typeof value.id !== "string" ||
    !TEAM_REV_ID_PATTERN.test(value.id) ||
    typeof value.revision_ordinal !== "number" ||
    !Number.isInteger(value.revision_ordinal) ||
    !isBoundedString(value.decision, 64) ||
    typeof value.proposal_json_sha256 !== "string" ||
    value.proposal_json_sha256.length !== 64 ||
    typeof value.validated !== "boolean" ||
    (value.validation_error_code !== null &&
      !isBoundedString(value.validation_error_code, 96)) ||
    !isBoundedString(value.created_at, 64)
  ) {
    return null;
  }
  return Object.freeze({
    id: value.id,
    revisionOrdinal: value.revision_ordinal,
    decision: value.decision,
    proposalJsonSha256: value.proposal_json_sha256,
    validated: value.validated,
    validationErrorCode: value.validation_error_code,
    createdAt: value.created_at,
  });
}

function parseProposalResult(
  value: unknown,
): DesktopTeamRunProposalResult | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, [
      "accepted",
      "plan_revision",
      "team_run",
      "validation_error_code",
    ]) ||
    typeof value.accepted !== "boolean" ||
    (value.validation_error_code !== null &&
      !isBoundedString(value.validation_error_code, 96))
  ) {
    return null;
  }
  const teamRun = parseTeamRun(value.team_run);
  const planRevision = parsePlanRevision(value.plan_revision);
  if (teamRun === null || planRevision === null) return null;
  return Object.freeze({
    accepted: value.accepted,
    validationErrorCode: value.validation_error_code,
    teamRun,
    planRevision,
  });
}

function parseBlackboard(
  value: unknown,
): { readonly blackboard: PersonalTeamBlackboard } | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["blackboard"]) ||
    !isRecord(value.blackboard)
  ) {
    return null;
  }
  const board = value.blackboard;
  if (
    !hasExactKeys(board, [
      "assignments",
      "collaboration_requests",
      "current_plan_revision_id",
      "owner_objective",
      "reports",
      "team_run_id",
      "workspace_id",
    ]) ||
    typeof board.team_run_id !== "string" ||
    !TEAM_RUN_ID_PATTERN.test(board.team_run_id) ||
    typeof board.workspace_id !== "string" ||
    !WORKSPACE_ID_PATTERN.test(board.workspace_id) ||
    !isBoundedString(board.owner_objective, 16384) ||
    (board.current_plan_revision_id !== null &&
      (typeof board.current_plan_revision_id !== "string" ||
        !TEAM_REV_ID_PATTERN.test(board.current_plan_revision_id))) ||
    !Array.isArray(board.assignments) ||
    !Array.isArray(board.reports) ||
    !Array.isArray(board.collaboration_requests)
  ) {
    return null;
  }
  const assignments = [];
  for (const row of board.assignments) {
    if (
      !isRecord(row) ||
      typeof row.assignment_id !== "string" ||
      typeof row.employee_role_id !== "string" ||
      !SPECIALIST_ROLE_SET.has(row.employee_role_id) ||
      !isBoundedString(row.objective, 16384) ||
      !isBoundedString(row.state, 64) ||
      !isBoundedString(row.wave_id, 128) ||
      !Array.isArray(row.depends_on_assignment_ids) ||
      !isBoundedString(row.expected_output, 16384)
    ) {
      return null;
    }
    assignments.push(
      Object.freeze({
        assignmentId: row.assignment_id,
        employeeRoleId: row.employee_role_id as SpecialistEmployeeId,
        objective: row.objective,
        state: row.state,
        waveId: row.wave_id,
        dependsOnAssignmentIds: Object.freeze(
          row.depends_on_assignment_ids.filter(
            (item): item is string => typeof item === "string",
          ),
        ),
        expectedOutput: row.expected_output,
      }),
    );
  }
  const collaborationRequests: DesktopTeamCollaborationRequest[] = [];
  for (const row of board.collaboration_requests) {
    if (
      !isRecord(row) ||
      typeof row.id !== "string" ||
      !isBoundedString(row.id, 128) ||
      typeof row.from_assignment_id !== "string" ||
      typeof row.from_employee_role_id !== "string" ||
      !SPECIALIST_ROLE_SET.has(row.from_employee_role_id) ||
      typeof row.target_role_id !== "string" ||
      !SPECIALIST_ROLE_SET.has(row.target_role_id) ||
      !isBoundedString(row.question, 16384) ||
      !isBoundedString(row.reason, 16384) ||
      typeof row.parent_decision !== "string"
    ) {
      return null;
    }
    collaborationRequests.push(
      Object.freeze({
        id: row.id,
        fromAssignmentId: row.from_assignment_id,
        fromEmployeeRoleId: row.from_employee_role_id as SpecialistEmployeeId,
        targetRoleId: row.target_role_id as SpecialistEmployeeId,
        question: row.question,
        reason: row.reason,
        parentDecision:
          row.parent_decision as DesktopTeamCollaborationRequest["parentDecision"],
        resolvedAssignmentId:
          row.resolved_assignment_id === null ||
          typeof row.resolved_assignment_id === "string"
            ? row.resolved_assignment_id
            : null,
      }),
    );
  }
  const reports: EmployeeTeamReport[] = [];
  for (const row of board.reports) {
    if (
      !isRecord(row) ||
      typeof row.assignment_id !== "string" ||
      typeof row.employee_role_id !== "string" ||
      !SPECIALIST_ROLE_SET.has(row.employee_role_id) ||
      (row.status !== "completed" &&
        row.status !== "needs_collaboration" &&
        row.status !== "blocked") ||
      !isBoundedString(row.report, 131072)
    ) {
      return null;
    }
    reports.push(
      Object.freeze({
        assignmentId: row.assignment_id,
        employeeRoleId: row.employee_role_id as SpecialistEmployeeId,
        status: row.status,
        report: row.report,
        collaborationRequests: Object.freeze([]),
      }),
    );
  }
  return Object.freeze({
    blackboard: Object.freeze({
      teamRunId: board.team_run_id,
      workspaceId: board.workspace_id,
      ownerObjective: board.owner_objective,
      currentPlanRevisionId: board.current_plan_revision_id,
      assignments: Object.freeze(assignments),
      reports: Object.freeze(reports),
      collaborationRequests: Object.freeze(collaborationRequests),
    }),
  });
}

function parseTeamReportAck(
  value: unknown,
): { readonly recorded: true } | null {
  if (!isRecord(value) || !isRecord(value.report)) return null;
  return Object.freeze({ recorded: true as const });
}

function parseTeamNodeCreate(value: unknown): {
  readonly node: {
    readonly id: string;
    readonly ordinal: number;
    readonly invocationId: string;
  };
} | null {
  if (!isRecord(value) || !isRecord(value.node)) return null;
  const node = value.node;
  if (
    typeof node.id !== "string" ||
    typeof node.ordinal !== "number" ||
    typeof node.invocation_id !== "string"
  ) {
    return null;
  }
  return Object.freeze({
    node: Object.freeze({
      id: node.id,
      ordinal: node.ordinal,
      invocationId: node.invocation_id,
    }),
  });
}

function parseTeamNodeUpdate(value: unknown): {
  readonly updated: true;
  readonly id: string;
  readonly state: string;
} | null {
  if (
    !isRecord(value) ||
    value.updated !== true ||
    typeof value.id !== "string" ||
    typeof value.state !== "string"
  ) {
    return null;
  }
  return Object.freeze({
    updated: true as const,
    id: value.id,
    state: value.state,
  });
}

function parseCollaborationWrapper(value: unknown): {
  readonly collaborationRequest: DesktopTeamCollaborationRequest;
} | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["collaboration_request"]) ||
    !isRecord(value.collaboration_request)
  ) {
    return null;
  }
  const row = value.collaboration_request;
  if (
    typeof row.from_assignment_id !== "string" ||
    typeof row.from_employee_role_id !== "string" ||
    !SPECIALIST_ROLE_SET.has(row.from_employee_role_id) ||
    typeof row.target_role_id !== "string" ||
    !SPECIALIST_ROLE_SET.has(row.target_role_id) ||
    !isBoundedString(row.question, 16384) ||
    !isBoundedString(row.reason, 16384)
  ) {
    return null;
  }
  return Object.freeze({
    collaborationRequest: Object.freeze({
      id: typeof row.id === "string" ? row.id : undefined,
      fromAssignmentId: row.from_assignment_id,
      fromEmployeeRoleId: row.from_employee_role_id as SpecialistEmployeeId,
      targetRoleId: row.target_role_id as SpecialistEmployeeId,
      question: row.question,
      reason: row.reason,
      parentDecision: "pending",
      resolvedAssignmentId: null,
    }),
  });
}

function parseCollaborationResolveAck(
  value: unknown,
): { readonly resolved: true } | null {
  if (
    !isRecord(value) ||
    !hasExactKeys(value, ["collaboration_request"]) ||
    !isRecord(value.collaboration_request)
  ) {
    return null;
  }
  const row = value.collaboration_request;
  if (
    typeof row.id !== "string" ||
    !isBoundedString(row.id, 128) ||
    (row.parent_decision !== "accept_start" &&
      row.parent_decision !== "handle_self" &&
      row.parent_decision !== "merge_existing" &&
      row.parent_decision !== "decline") ||
    (row.resolved_assignment_id !== null &&
      typeof row.resolved_assignment_id !== "string")
  ) {
    return null;
  }
  return Object.freeze({ resolved: true });
}

export class DesktopNativeClient {
  readonly #backendOrigin: string;
  readonly #nativeControlToken: string;
  readonly #fetch: FetchLike;

  constructor(options: {
    readonly backendOrigin: string;
    readonly nativeControlToken: string;
    readonly fetch?: FetchLike;
  }) {
    if (!TOKEN_PATTERN.test(options.nativeControlToken)) {
      throw new Error("desktop_native_control_token_invalid");
    }
    this.#backendOrigin = validateBackendOrigin(options.backendOrigin);
    this.#nativeControlToken = options.nativeControlToken;
    this.#fetch = options.fetch ?? fetch;
  }

  getOwnerStatus(): Promise<DesktopOperationResult<DesktopOwnerStatus>> {
    return this.#request(
      "GET",
      "/desktop/v1/owner",
      undefined,
      parseOwnerStatus,
    );
  }

  bootstrapOwner(
    input: DesktopOwnerBootstrapInput,
  ): Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>> {
    return this.#request(
      "POST",
      "/desktop/v1/owner/bootstrap",
      { display_name: input.displayName },
      parseOwnerBootstrap,
    );
  }

  listWorkspaces(): Promise<DesktopOperationResult<DesktopWorkspaceList>> {
    return this.#request(
      "GET",
      "/desktop/v1/workspaces",
      undefined,
      parseWorkspaceList,
    );
  }

  createWorkspace(
    input: DesktopWorkspaceCreateInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> {
    return this.#request(
      "POST",
      "/desktop/v1/workspaces",
      { name: input.name },
      parseWorkspaceCreate,
    );
  }

  archiveWorkspace(
    input: DesktopWorkspaceArchiveInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> {
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/archive`,
      { expected_row_version: input.expectedRowVersion },
      parseWorkspaceMutation,
    );
  }

  getWorkspaceAgent(
    input: DesktopWorkspaceIdInput,
  ): Promise<DesktopOperationResult<{ readonly agent: DesktopParentAgent }>> {
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/workspaces/${input.workspaceId}/agent`,
      undefined,
      parseParentAgent,
    );
  }

  getApplicationPreference(): Promise<
    DesktopOperationResult<{
      readonly preference: DesktopApplicationPreference;
    }>
  > {
    return this.#request(
      "GET",
      "/desktop/v1/settings/application",
      undefined,
      parseApplicationPreferenceResult,
    );
  }

  updateApplicationPreference(
    input: DesktopApplicationPreferenceUpdateInput,
  ): Promise<
    DesktopOperationResult<{
      readonly preference: DesktopApplicationPreference;
    }>
  > {
    if (!validRevision(input.expectedRowVersion)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      "/desktop/v1/settings/application",
      {
        density: input.density,
        reduce_motion: input.reduceMotion,
        expected_row_version: input.expectedRowVersion,
      },
      parseApplicationPreferenceResult,
    );
  }

  getWorkspaceComposition(
    input: DesktopWorkspaceIdInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceCompositionSnapshot>> {
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/workspaces/${input.workspaceId}/composition`,
      undefined,
      (value) => {
        const snapshot = parseCompositionSnapshot(value);
        return snapshot !== null &&
          snapshot.profile.workspaceId === input.workspaceId
          ? snapshot
          : null;
      },
    );
  }

  proposeWorkspaceComposition(
    input: DesktopWorkspaceCompositionOwnerProposalInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !validRevision(input.expectedRevision) ||
      !SHA256_PATTERN.test(input.expectedProfileSha256)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/composition/proposals`,
      {
        expected_revision: input.expectedRevision,
        expected_profile_sha256: input.expectedProfileSha256,
        desired_profile: compositionProfilePayload(input.desiredProfile),
      },
      (value) => {
        const result = parseCompositionProposalResult(value);
        const proposal = result?.proposal;
        return proposal !== undefined &&
          proposal.workspaceId === input.workspaceId &&
          proposal.baseRevision === input.expectedRevision &&
          proposal.baseProfileSha256 === input.expectedProfileSha256 &&
          proposal.sourceKind === "owner" &&
          proposal.sourceReference === null &&
          sameCompositionProfile(proposal.desiredProfile, input.desiredProfile)
          ? result
          : null;
      },
    );
  }

  proposeWorkspaceCompositionFromAssistant(
    input: DesktopWorkspaceCompositionAssistantProposalInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !validRevision(input.expectedRevision) ||
      !SHA256_PATTERN.test(input.expectedProfileSha256) ||
      !MESSAGE_ID_PATTERN.test(input.messageId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/composition/proposals/from-assistant`,
      {
        expected_revision: input.expectedRevision,
        expected_profile_sha256: input.expectedProfileSha256,
        message_id: input.messageId,
      },
      (value) => {
        const result = parseCompositionProposalResult(value);
        const proposal = result?.proposal;
        return proposal !== undefined &&
          proposal.workspaceId === input.workspaceId &&
          proposal.baseRevision === input.expectedRevision &&
          proposal.baseProfileSha256 === input.expectedProfileSha256 &&
          proposal.sourceKind === "assistant" &&
          proposal.sourceReference === input.messageId
          ? result
          : null;
      },
    );
  }

  proposeWorkspaceCompositionRollback(
    input: DesktopWorkspaceCompositionRollbackProposalInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceCompositionProposalResult>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !validRevision(input.expectedRevision) ||
      !SHA256_PATTERN.test(input.expectedProfileSha256) ||
      !validRevision(input.targetRevision)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/composition/proposals/rollback`,
      {
        expected_revision: input.expectedRevision,
        expected_profile_sha256: input.expectedProfileSha256,
        target_revision: input.targetRevision,
      },
      (value) => {
        const result = parseCompositionProposalResult(value);
        const proposal = result?.proposal;
        return proposal !== undefined &&
          proposal.workspaceId === input.workspaceId &&
          proposal.baseRevision === input.expectedRevision &&
          proposal.baseProfileSha256 === input.expectedProfileSha256 &&
          proposal.sourceKind === "rollback" &&
          proposal.sourceReference === `revision:${input.targetRevision}`
          ? result
          : null;
      },
    );
  }

  decideWorkspaceComposition(
    input: DesktopWorkspaceCompositionDecisionInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceCompositionDecisionResult>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !COMPOSITION_PROPOSAL_ID_PATTERN.test(input.proposalId) ||
      !SHA256_PATTERN.test(input.requestSha256)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/composition/proposals/${input.proposalId}/decision`,
      {
        decision: input.decision,
        request_sha256: input.requestSha256,
      },
      (value) => {
        const result = parseCompositionDecisionResult(value);
        if (
          result === null ||
          result.workspaceId !== input.workspaceId ||
          result.proposalId !== input.proposalId ||
          result.requestSha256 !== input.requestSha256 ||
          result.decision !==
            (input.decision === "approve" ? "approved" : "rejected")
        ) {
          return null;
        }
        if (result.decision === "rejected") return result;
        return result.profile.workspaceId === input.workspaceId &&
          result.profile.proposalId === input.proposalId
          ? result
          : null;
      },
    );
  }

  getWorkspaceComponents(
    input: DesktopWorkspaceIdInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentSnapshot>> {
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/workspaces/${input.workspaceId}/components`,
      undefined,
      (value) => {
        const parsed = parseWorkspaceComponentSnapshot(value);
        return parsed?.workspaceId === input.workspaceId ? parsed : null;
      },
    );
  }

  attestWorkspaceComponentPackage(
    input: DesktopWorkspaceComponentPackageAttestationInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceComponentPackageAttestationResult>
  > {
    if (
      !COMPONENT_ID_PATTERN.test(input.componentId) ||
      !COMPONENT_VERSION_PATTERN.test(input.version) ||
      !SHA256_PATTERN.test(input.policyManifestSha256) ||
      !SHA256_PATTERN.test(input.manifestSha256) ||
      !SHA256_PATTERN.test(input.packageSha256) ||
      !SHA256_PATTERN.test(input.inventorySha256)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      "/desktop/v1/components/catalog/attest",
      {
        component_id: input.componentId,
        version: input.version,
        adapter_id: input.adapterId,
        policy_manifest_sha256: input.policyManifestSha256,
        manifest_sha256: input.manifestSha256,
        package_sha256: input.packageSha256,
        inventory_sha256: input.inventorySha256,
      },
      (value) => {
        const parsed = parseWorkspaceComponentPackageAttestationResult(value);
        return parsed !== null &&
          parsed.componentId === input.componentId &&
          parsed.version === input.version &&
          parsed.adapterId === input.adapterId &&
          parsed.policyManifestSha256 === input.policyManifestSha256 &&
          parsed.manifestSha256 === input.manifestSha256 &&
          parsed.packageSha256 === input.packageSha256 &&
          parsed.inventorySha256 === input.inventorySha256
          ? parsed
          : null;
      },
    );
  }

  registerOwnerWorkspaceComponentPackage(
    input: DesktopWorkspaceComponentOwnerPackageRegisterInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceComponentOwnerPackageRegistration>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !SHA256_PATTERN.test(input.manifestSha256) ||
      !SHA256_PATTERN.test(input.packageSha256) ||
      !SHA256_PATTERN.test(input.inventorySha256)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/components/catalog/register-owner-package`,
      {
        manifest: input.manifest,
        manifest_sha256: input.manifestSha256,
        package_sha256: input.packageSha256,
        inventory_sha256: input.inventorySha256,
      },
      (value) => {
        const parsed = parseWorkspaceComponentOwnerPackageRegistration(value);
        return parsed !== null &&
          parsed.manifestSha256 === input.manifestSha256 &&
          parsed.packageSha256 === input.packageSha256
          ? parsed
          : null;
      },
    );
  }

  proposeWorkspaceComponent(
    input: DesktopWorkspaceComponentProposeInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentProposalResult>> {
    if (
      !componentBaseIdentityValid({
        ...input,
        manifestSha256: "0".repeat(64),
        packageSha256: "0".repeat(64),
      }) ||
      !COMPONENT_VERSION_PATTERN.test(input.targetVersion) ||
      !COMPONENT_ACTIONS.has(input.changeKind) ||
      input.requestedGrants.length > 64 ||
      input.requestedGrants.some((grant) => !componentGrantValid(grant)) ||
      input.desiredSlotBindings.length > 64 ||
      input.dependencyGraph.length > 64
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/components/proposals`,
      {
        component_id: input.componentId,
        target_version: input.targetVersion,
        change_kind: input.changeKind,
        expected_revision: input.expectedRevision,
        requested_grants: input.requestedGrants.map(componentGrantBody),
        desired_configuration: input.desiredConfiguration,
        desired_slot_bindings: input.desiredSlotBindings.map((binding) => ({
          slot_id: binding.slotId,
          binding_key: binding.bindingKey,
          order_index: binding.orderIndex,
          configuration: binding.configuration,
        })),
        dependency_graph: input.dependencyGraph.map((dependency) => ({
          component_id: dependency.componentId,
          version: dependency.version,
          policy_manifest_sha256: dependency.policyManifestSha256,
          manifest_sha256: dependency.manifestSha256,
          package_sha256: dependency.packageSha256,
        })),
        source_kind: "owner",
        source_reference: null,
        idempotency_key: input.idempotencyKey,
      },
      (value) => {
        const parsed = parseWorkspaceComponentProposalResult(value);
        return parsed?.proposal.workspaceId === input.workspaceId &&
          parsed.proposal.componentId === input.componentId &&
          parsed.proposal.sourceKind === "owner" &&
          parsed.proposal.sourceReference === null
          ? parsed
          : null;
      },
    );
  }

  proposeWorkspaceComponentFromAssistant(
    input: DesktopWorkspaceComponentAssistantProposalInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentProposalResult>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !MESSAGE_ID_PATTERN.test(input.messageId) ||
      !isBoundedString(input.idempotencyKey, 128) ||
      input.idempotencyKey.length < 8
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/components/proposals/from-assistant`,
      {
        message_id: input.messageId,
      },
      (value) => {
        const parsed = parseWorkspaceComponentProposalResult(value);
        return parsed !== null &&
          parsed.proposal.workspaceId === input.workspaceId &&
          parsed.proposal.sourceKind === "assistant" &&
          parsed.proposal.sourceReference === input.messageId
          ? parsed
          : null;
      },
    );
  }

  decideWorkspaceComponent(
    input: DesktopWorkspaceComponentDecisionInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentDecisionResult>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !COMPOSITION_PROPOSAL_ID_PATTERN.test(input.proposalId) ||
      (input.decision !== "approve" && input.decision !== "reject") ||
      !SHA256_PATTERN.test(input.requestSha256)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/components/proposals/${input.proposalId}/decide`,
      {
        decision: input.decision,
        request_sha256: input.requestSha256,
      },
      (value) => {
        const parsed = parseWorkspaceComponentDecisionResult(value);
        return parsed?.workspaceId === input.workspaceId &&
          parsed.proposalId === input.proposalId &&
          parsed.requestSha256 === input.requestSha256
          ? parsed
          : null;
      },
    );
  }

  applyWorkspaceComponentAction(
    input: DesktopWorkspaceComponentNativeActionInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentActionResult>> {
    if (
      !componentBaseIdentityValid(input) ||
      !COMPONENT_ACTIONS.has(input.action) ||
      !COMPOSITION_PROPOSAL_ID_PATTERN.test(input.proposalId) ||
      !SHA256_PATTERN.test(input.requestSha256)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/components/actions`,
      {
        component_id: input.componentId,
        action: input.action,
        proposal_id: input.proposalId,
        request_sha256: input.requestSha256,
        expected_revision: input.expectedRevision,
        manifest_sha256: input.manifestSha256,
        package_sha256: input.packageSha256,
        idempotency_key: input.idempotencyKey,
        phase: input.phase,
        operation_id: input.operationId,
        outcome: input.outcome,
        evidence_sha256: input.evidenceSha256,
        health_state: input.healthState,
        runtime_instance_id: input.runtimeInstanceId,
        workload_identity_digest: input.workloadIdentityDigest,
        error_code: input.errorCode,
      },
      (value) => {
        const parsed = parseWorkspaceComponentActionResult(value);
        return parsed?.operation.workspaceId === input.workspaceId &&
          parsed.operation.componentId === input.componentId
          ? parsed
          : null;
      },
    );
  }

  beginWorkspaceComponentInvocation(
    input: DesktopWorkspaceComponentBeginInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentBeginResult>> {
    if (
      !componentBaseIdentityValid(input) ||
      !COMPONENT_OPERATIONS.has(input.action) ||
      !SHA256_PATTERN.test(input.argumentsSha256) ||
      !isPositiveInteger(input.bindingGeneration) ||
      (input.logicalResourceId !== undefined &&
        !isBoundedString(input.logicalResourceId, 128)) ||
      (input.resourceVersion !== undefined &&
        !isPositiveInteger(input.resourceVersion)) ||
      (input.logicalServiceId !== undefined &&
        !isBoundedString(input.logicalServiceId, 128)) ||
      !isNonNegativeInteger(input.bytesIn) ||
      !isNonNegativeInteger(input.bytesOutReserved) ||
      !isNonNegativeInteger(input.tokensReserved) ||
      !isPositiveInteger(input.wallTimeMs) ||
      !isNonNegativeInteger(input.costUnits)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/components/invocations/begin`,
      {
        component_id: input.componentId,
        action: input.action,
        arguments_sha256: input.argumentsSha256,
        expected_revision: input.expectedRevision,
        binding_generation: input.bindingGeneration,
        manifest_sha256: input.manifestSha256,
        package_sha256: input.packageSha256,
        idempotency_key: input.idempotencyKey,
        logical_resource_id: input.logicalResourceId ?? null,
        resource_version: input.resourceVersion ?? null,
        logical_service_id: input.logicalServiceId ?? null,
        bytes_in: input.bytesIn,
        bytes_out_reserved: input.bytesOutReserved,
        tokens_reserved: input.tokensReserved,
        wall_time_ms: input.wallTimeMs,
        cost_units: input.costUnits,
      },
      (value) => {
        const parsed = parseWorkspaceComponentBeginResult(value);
        return parsed?.ticket.workspaceId === input.workspaceId &&
          parsed.ticket.componentId === input.componentId &&
          parsed.ticket.action === input.action &&
          parsed.ticket.bindingGeneration === input.bindingGeneration
          ? parsed
          : null;
      },
    );
  }

  settleWorkspaceComponentInvocation(
    input: DesktopWorkspaceComponentSettleInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentSettleResult>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !COMPONENT_OPERATION_ID_PATTERN.test(input.operationId) ||
      !SHA256_PATTERN.test(input.requestSha256) ||
      !["succeeded", "failed", "cancelled", "unknown"].includes(input.state) ||
      (input.resultSha256 !== undefined &&
        !SHA256_PATTERN.test(input.resultSha256)) ||
      !SHA256_PATTERN.test(input.evidenceSha256) ||
      (input.errorCode !== undefined &&
        !ERROR_CODE_PATTERN.test(input.errorCode)) ||
      !isNonNegativeInteger(input.actualBytesOut) ||
      !isNonNegativeInteger(input.actualTokens) ||
      !isNonNegativeInteger(input.actualWallTimeMs)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/components/invocations/${input.operationId}/settle`,
      {
        request_sha256: input.requestSha256,
        state: input.state,
        result_sha256: input.resultSha256 ?? null,
        evidence_sha256: input.evidenceSha256,
        error_code: input.errorCode ?? null,
        actual_bytes_out: input.actualBytesOut,
        actual_tokens: input.actualTokens,
        actual_wall_time_ms: input.actualWallTimeMs,
      },
      (value) => {
        const parsed = parseWorkspaceComponentSettleResult(value);
        return parsed?.operation.workspaceId === input.workspaceId &&
          parsed.operation.operationId === input.operationId &&
          parsed.operation.requestSha256 === input.requestSha256
          ? parsed
          : null;
      },
    );
  }

  emergencyStopWorkspaceComponents(
    input: DesktopWorkspaceComponentNativeEmergencyStopInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceComponentNativeEmergencyStopResult>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !isBoundedString(input.idempotencyKey, 128) ||
      input.idempotencyKey.length < 8 ||
      !ERROR_CODE_PATTERN.test(input.reasonCode) ||
      (input.phase === "settle" &&
        (!COMPONENT_ID_PATTERN.test(input.componentId) ||
          !COMPONENT_OPERATION_ID_PATTERN.test(input.operationId) ||
          !COMPONENT_EFFECT_ID_PATTERN.test(input.effectId) ||
          !SHA256_PATTERN.test(input.requestSha256) ||
          !["succeeded", "failed", "unknown"].includes(input.outcome) ||
          !SHA256_PATTERN.test(input.evidenceSha256) ||
          (input.errorCode !== null &&
            !ERROR_CODE_PATTERN.test(input.errorCode))))
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/components/emergency-stop`,
      input.phase === "prepare"
        ? {
            phase: "prepare",
            idempotency_key: input.idempotencyKey,
            reason_code: input.reasonCode,
          }
        : {
            phase: "settle",
            idempotency_key: input.idempotencyKey,
            reason_code: input.reasonCode,
            component_id: input.componentId,
            operation_id: input.operationId,
            effect_id: input.effectId,
            request_sha256: input.requestSha256,
            outcome: input.outcome,
            evidence_sha256: input.evidenceSha256,
            error_code: input.errorCode,
          },
      (value) => {
        const parsed =
          input.phase === "prepare"
            ? parseWorkspaceComponentEmergencyStopPrepareResult(value)
            : parseWorkspaceComponentEmergencyStopSettleResult(value);
        if (parsed?.workspaceId !== input.workspaceId) return null;
        if (
          input.phase === "settle" &&
          ("componentId" in parsed
            ? parsed.componentId !== input.componentId ||
              parsed.operation.operationId !== input.operationId ||
              parsed.effect.effectId !== input.effectId
            : true)
        ) {
          return null;
        }
        return parsed;
      },
    );
  }

  reconcileWorkspaceComponent(
    input: DesktopWorkspaceComponentReconcileInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceComponentReconcileResult>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !COMPONENT_OPERATION_ID_PATTERN.test(input.operationId) ||
      !COMPONENT_EFFECT_ID_PATTERN.test(input.effectId) ||
      !SHA256_PATTERN.test(input.requestSha256) ||
      (input.outcome !== "succeeded" && input.outcome !== "failed") ||
      !SHA256_PATTERN.test(input.evidenceSha256)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/components/reconciliations`,
      {
        operation_id: input.operationId,
        effect_id: input.effectId,
        request_sha256: input.requestSha256,
        outcome: input.outcome,
        evidence_sha256: input.evidenceSha256,
      },
      (value) => {
        const parsed = parseWorkspaceComponentReconcileResult(value);
        return parsed?.operation.workspaceId === input.workspaceId &&
          parsed.operation.operationId === input.operationId &&
          parsed.effect.effectId === input.effectId
          ? parsed
          : null;
      },
    );
  }

  settleWorkspaceComponentRecovery(
    input: DesktopWorkspaceComponentRecoverySettleInput,
  ): Promise<
    DesktopOperationResult<DesktopWorkspaceComponentRecoverySettleResult>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !COMPONENT_RECOVERY_ID_PATTERN.test(input.recoveryId) ||
      !COMPONENT_OPERATION_ID_PATTERN.test(input.operationId) ||
      !["succeeded", "failed", "unknown"].includes(input.outcome) ||
      !SHA256_PATTERN.test(input.evidenceSha256) ||
      (input.healthState !== null &&
        !["healthy", "unhealthy", "unknown"].includes(input.healthState)) ||
      !COMPONENT_RUNTIME_ID_PATTERN.test(input.runtimeInstanceId) ||
      !SHA256_PATTERN.test(input.workloadIdentityDigest) ||
      (input.errorCode !== null && !ERROR_CODE_PATTERN.test(input.errorCode)) ||
      (input.outcome === "succeeded" &&
        (input.healthState !== "healthy" || input.errorCode !== null)) ||
      (input.outcome === "failed" && input.errorCode === null)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/components/recoveries/${input.recoveryId}/settle`,
      {
        operation_id: input.operationId,
        outcome: input.outcome,
        evidence_sha256: input.evidenceSha256,
        health_state: input.healthState,
        runtime_instance_id: input.runtimeInstanceId,
        workload_identity_digest: input.workloadIdentityDigest,
        error_code: input.errorCode,
      },
      (value) => {
        const parsed = parseWorkspaceComponentRecoverySettleResult(value);
        return parsed?.recoveryId === input.recoveryId &&
          parsed.operation.workspaceId === input.workspaceId &&
          parsed.operation.operationId === input.operationId
          ? parsed
          : null;
      },
    );
  }

  listProviders(): Promise<DesktopOperationResult<DesktopProviderList>> {
    return this.#request(
      "GET",
      "/desktop/v1/providers",
      undefined,
      parseProviderList,
    );
  }

  upsertProvider(
    body: Readonly<Record<string, unknown>>,
  ): Promise<DesktopOperationResult<DesktopProviderMutationResult>> {
    return this.#request(
      "POST",
      "/desktop/v1/providers",
      body,
      parseProviderMutation,
    );
  }

  deleteProvider(
    input: DesktopProviderIdInput,
  ): Promise<
    DesktopOperationResult<{ readonly deleted: true; readonly id: string }>
  > {
    if (!PROVIDER_ID_PATTERN.test(input.providerId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "DELETE",
      `/desktop/v1/providers/${input.providerId}`,
      undefined,
      parseProviderDeleted,
    );
  }

  getProviderVault(
    providerId: string,
  ): Promise<DesktopOperationResult<{ encryptedSecretBlob: string }>> {
    if (!PROVIDER_ID_PATTERN.test(providerId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/providers/${providerId}/vault`,
      undefined,
      parseProviderVault,
    );
  }

  testProvider(
    providerId: string,
    secret: string,
  ): Promise<DesktopOperationResult<DesktopProviderTestResult>> {
    if (!PROVIDER_ID_PATTERN.test(providerId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/providers/${providerId}/test`,
      { secret },
      parseProviderTest,
      120_000,
    );
  }

  pinProviderEndpoint(input: {
    readonly baseUrl: string;
    readonly allowLoopbackHttp: boolean;
  }): Promise<
    DesktopOperationResult<{
      readonly scheme: "http" | "https";
      readonly hostname: string;
      readonly port: number;
      readonly chatPath: string;
      readonly connectAddrs: readonly string[];
      readonly loopback: boolean;
    }>
  > {
    return this.#request(
      "POST",
      "/desktop/v1/provider-endpoints/pin",
      {
        base_url: input.baseUrl,
        allow_loopback_http: input.allowLoopbackHttp,
      },
      parsePinnedEndpoint,
    );
  }

  listConversations(
    input: DesktopWorkspaceIdInput,
  ): Promise<DesktopOperationResult<DesktopConversationList>> {
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/workspaces/${input.workspaceId}/conversations`,
      undefined,
      parseConversationList,
    );
  }

  createConversation(input: DesktopConversationCreateInput): Promise<
    DesktopOperationResult<{
      readonly created: true;
      readonly conversation: DesktopConversation;
    }>
  > {
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/conversations`,
      input.title === undefined ? {} : { title: input.title },
      parseConversationCreated,
    );
  }

  archiveConversation(
    input: DesktopConversationArchiveInput,
  ): Promise<
    DesktopOperationResult<{ readonly conversation: DesktopConversation }>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !CONVERSATION_ID_PATTERN.test(input.conversationId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/conversations/${input.conversationId}/archive`,
      { expected_row_version: input.expectedRowVersion },
      parseConversationArchived,
    );
  }

  getConversation(
    input: DesktopConversationGetInput,
  ): Promise<DesktopOperationResult<DesktopConversationDetail>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !CONVERSATION_ID_PATTERN.test(input.conversationId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/workspaces/${input.workspaceId}/conversations/${input.conversationId}`,
      undefined,
      parseConversationDetail,
      5_000,
      MAX_CONVERSATION_BYTES,
    );
  }

  cancelInvocation(invocationId: string): Promise<
    DesktopOperationResult<{
      readonly cancelled: boolean;
      readonly id: string;
      readonly accepted: boolean;
    }>
  > {
    if (!INVOCATION_ID_PATTERN.test(invocationId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/invocations/${invocationId}/cancel`,
      {},
      parseCancelResult,
    );
  }

  async sendConversation(
    input: DesktopConversationSendInput,
    secret: string,
    emit: (event: DesktopConversationEvent) => void,
    signal: AbortSignal,
  ): Promise<DesktopOperationResult<DesktopConversationEvent>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !CONVERSATION_ID_PATTERN.test(input.conversationId)
    ) {
      return failure("desktop_native_input_invalid");
    }
    try {
      const response = await this.#fetch(
        `${this.#backendOrigin}/desktop/v1/workspaces/${input.workspaceId}/conversations/${input.conversationId}/messages`,
        {
          method: "POST",
          headers: {
            Accept: "text/event-stream",
            "Content-Type": "application/json",
            "x-omnibase-desktop-native-control": this.#nativeControlToken,
          },
          body: JSON.stringify({
            secret,
            content: input.content,
            ...(input.providerId === undefined
              ? {}
              : { provider_id: input.providerId }),
            ...(input.retryOfMessageId === undefined
              ? {}
              : { retry_of_message_id: input.retryOfMessageId }),
          }),
          cache: "no-store",
          redirect: "error",
          signal,
        },
      );
      const contentType = response.headers.get("content-type") ?? "";
      if (!contentType.includes("text/event-stream")) {
        const payload = await readBoundedJson(response, MAX_RESPONSE_BYTES);
        return failure(
          parseErrorCode(payload) ?? "desktop_native_request_failed",
        );
      }
      return await readConversationStream(
        response,
        emit,
        signal,
        async (invocationId) => {
          await this.cancelInvocation(invocationId);
        },
        input.sendEpoch,
      );
    } catch {
      if (signal.aborted) {
        return success(
          stampSendEpoch(
            Object.freeze({
              type: "cancelled",
              invocationId: "invocation_cancelled_locally",
              workspaceId: input.workspaceId,
              conversationId: input.conversationId,
              errorRedacted: "生成已停止",
            }) satisfies DesktopConversationEvent,
            input.sendEpoch,
          ),
        );
      }
      return failure("desktop_native_request_failed");
    }
  }

  listAgentRoles(
    input: DesktopWorkspaceIdInput,
  ): Promise<DesktopOperationResult<DesktopAgentRoleList>> {
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/workspaces/${input.workspaceId}/agent-roles`,
      undefined,
      parseAgentRoleList,
    );
  }

  getAgentRole(
    input: DesktopAgentRoleIdInput,
  ): Promise<DesktopOperationResult<{ readonly role: DesktopAgentRole }>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !EMPLOYEE_ROLE_SET.has(input.roleId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/workspaces/${input.workspaceId}/agent-roles/${input.roleId}`,
      undefined,
      parseAgentRoleWrapper,
    );
  }

  updateAgentRole(
    input: DesktopAgentRoleUpdateInput,
  ): Promise<DesktopOperationResult<{ readonly role: DesktopAgentRole }>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !EMPLOYEE_ROLE_SET.has(input.roleId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/agent-roles/${input.roleId}`,
      {
        provider_id: input.providerId,
        model_name_override: input.modelNameOverride,
        gear: input.gear,
        thinking_depth: input.thinkingDepth,
        expected_row_version: input.expectedRowVersion,
      },
      parseAgentRoleWrapper,
    );
  }

  testAgentRole(
    input: DesktopAgentRoleIdInput,
  ): Promise<DesktopOperationResult<DesktopAgentRoleTestResult>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !EMPLOYEE_ROLE_SET.has(input.roleId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/agent-roles/${input.roleId}/test`,
      undefined,
      parseAgentRoleTest,
    );
  }

  listTeamRuns(
    input: DesktopWorkspaceIdInput,
  ): Promise<
    DesktopOperationResult<{ readonly items: readonly DesktopTeamRun[] }>
  > {
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs`,
      undefined,
      parseTeamRunList,
    );
  }

  startTeamRun(
    input: DesktopTeamRunStartInput,
  ): Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>> {
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId)) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs`,
      {
        conversation_id: input.conversationId,
        task: input.task,
        team_mode: true,
        ...(input.allowedSpecialistRoleIds === undefined
          ? {}
          : {
              allowed_specialist_role_ids: [...input.allowedSpecialistRoleIds],
            }),
        maximum_provider_calls: input.budget.maximumProviderCalls,
        maximum_wall_time_ms: input.budget.maximumWallTimeMs,
        maximum_concurrent_calls: input.budget.maximumConcurrentCalls,
        maximum_input_characters: input.budget.maximumInputCharacters,
        maximum_output_characters: input.budget.maximumOutputCharacters,
      },
      parseTeamRunWrapper,
    );
  }

  getTeamRun(
    input: DesktopTeamRunIdInput,
  ): Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}`,
      undefined,
      parseTeamRunWrapper,
    );
  }

  cancelTeamRun(input: DesktopTeamRunIdInput): Promise<
    DesktopOperationResult<{
      readonly cancelled: boolean;
      readonly accepted: boolean;
      readonly teamRun: DesktopTeamRun;
    }>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/cancel`,
      undefined,
      parseTeamRunCancel,
    );
  }

  submitTeamProposal(
    input: DesktopTeamRunSubmitProposalInput,
  ): Promise<DesktopOperationResult<DesktopTeamRunProposalResult>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/proposals`,
      { proposal: input.proposal },
      parseProposalResult,
    );
  }

  getTeamBlackboard(
    input: DesktopTeamRunIdInput,
  ): Promise<
    DesktopOperationResult<{ readonly blackboard: PersonalTeamBlackboard }>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "GET",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/blackboard`,
      undefined,
      parseBlackboard,
    );
  }

  recordTeamCollaboration(input: DesktopTeamCollaborationInput): Promise<
    DesktopOperationResult<{
      readonly collaborationRequest: DesktopTeamCollaborationRequest;
    }>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId) ||
      !TEAM_NODE_ID_PATTERN.test(input.nodeId) ||
      !TEAM_REPORT_ID_PATTERN.test(input.reportId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/collaboration-requests`,
      {
        from_assignment_id: input.fromAssignmentId,
        from_employee_role_id: input.fromEmployeeRoleId,
        target_role_id: input.targetRoleId,
        question: input.question,
        reason: input.reason,
        node_id: input.nodeId,
        report_id: input.reportId,
      },
      parseCollaborationWrapper,
    );
  }

  resolveTeamCollaboration(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly requestId: string;
    readonly parentDecision:
      | "accept_start"
      | "handle_self"
      | "merge_existing"
      | "decline";
    readonly resolvedAssignmentId: string | null;
  }): Promise<DesktopOperationResult<{ readonly resolved: true }>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId) ||
      !TEAM_COLLABORATION_ID_PATTERN.test(input.requestId) ||
      (input.resolvedAssignmentId !== null &&
        !ASSIGNMENT_ID_PATTERN.test(input.resolvedAssignmentId))
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/collaboration-requests/${input.requestId}/resolve`,
      {
        parent_decision: input.parentDecision,
        resolved_assignment_id: input.resolvedAssignmentId,
      },
      parseCollaborationResolveAck,
    );
  }

  appendTeamRunBudget(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly budget: TeamRunBudget;
  }): Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/budget`,
      {
        maximum_provider_calls: input.budget.maximumProviderCalls,
        maximum_wall_time_ms: input.budget.maximumWallTimeMs,
        maximum_concurrent_calls: input.budget.maximumConcurrentCalls,
        maximum_input_characters: input.budget.maximumInputCharacters,
        maximum_output_characters: input.budget.maximumOutputCharacters,
      },
      parseTeamRunWrapper,
    );
  }

  setTeamRunState(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly state: string;
    readonly parentFinalAnswer?: string;
  }): Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>> {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/state`,
      {
        state: input.state,
        ...(input.parentFinalAnswer === undefined
          ? {}
          : { parent_final_answer: input.parentFinalAnswer }),
      },
      parseTeamRunWrapper,
    );
  }

  consumeTeamProviderCall(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly invocationId: string;
    readonly purpose:
      | "parent-propose"
      | "parent-replan"
      | "parent-synthesize"
      | "employee";
    readonly providerId: string;
    readonly requestedModel: string;
  }): Promise<
    DesktopOperationResult<{
      readonly teamRun: DesktopTeamRun;
      readonly parentCall?: DesktopTeamParentCallRecord;
    }>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId) ||
      !INVOCATION_ID_PATTERN.test(input.invocationId) ||
      !TEAM_PROVIDER_CALL_PURPOSES.has(input.purpose) ||
      !PROVIDER_ID_PATTERN.test(input.providerId) ||
      !isBoundedString(input.requestedModel, 256)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/consume-call`,
      {
        invocation_id: input.invocationId,
        purpose: input.purpose,
        provider_id: input.providerId,
        requested_model: input.requestedModel,
      },
      (value) => {
        const parsed = parseTeamProviderCallConsume(value);
        if (
          parsed === null ||
          parsed.teamRun.id !== input.teamRunId ||
          parsed.teamRun.workspaceId !== input.workspaceId
        ) {
          return null;
        }
        if (input.purpose === "employee") {
          return parsed.parentCall === undefined ? parsed : null;
        }
        return parsed.parentCall !== undefined &&
          parsed.parentCall.invocationId === input.invocationId &&
          parsed.parentCall.teamRunId === input.teamRunId &&
          parsed.parentCall.purpose === input.purpose &&
          parsed.parentCall.state === "pending" &&
          parsed.parentCall.providerId === input.providerId &&
          parsed.parentCall.requestedModel === input.requestedModel
          ? parsed
          : null;
      },
    );
  }

  settleTeamParentCall(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly invocationId: string;
    readonly purpose: "parent-propose" | "parent-replan" | "parent-synthesize";
    readonly providerId: string;
    readonly requestedModel: string;
    readonly state: "succeeded" | "failed" | "cancelled" | "unknown";
    readonly planRevisionId: string | null;
    readonly actualModel: string | null;
    readonly inputTokens: number | null;
    readonly outputTokens: number | null;
    readonly totalTokens: number | null;
    readonly outputSha256: string | null;
    readonly errorCode: string | null;
  }): Promise<
    DesktopOperationResult<{ readonly parentCall: DesktopTeamParentCallRecord }>
  > {
    const usageAllNull =
      input.inputTokens === null &&
      input.outputTokens === null &&
      input.totalTokens === null;
    const usageValid =
      usageAllNull ||
      (isNullableNonNegativeInteger(input.inputTokens) &&
        input.inputTokens !== null &&
        isNullableNonNegativeInteger(input.outputTokens) &&
        input.outputTokens !== null &&
        isNullableNonNegativeInteger(input.totalTokens) &&
        input.totalTokens !== null &&
        input.totalTokens === input.inputTokens + input.outputTokens);
    const successProofValid =
      input.state !== "succeeded" ||
      (input.planRevisionId !== null &&
        input.actualModel === input.requestedModel &&
        input.outputSha256 !== null &&
        TOKEN_PATTERN.test(input.outputSha256) &&
        input.errorCode === null);
    const failureProofValid =
      input.state === "succeeded" ||
      (input.outputSha256 === null &&
        input.errorCode !== null &&
        ERROR_CODE_PATTERN.test(input.errorCode));
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId) ||
      !INVOCATION_ID_PATTERN.test(input.invocationId) ||
      !TEAM_PARENT_CALL_PURPOSES.has(input.purpose) ||
      !TEAM_PARENT_CALL_TERMINAL_STATES.has(input.state) ||
      !PROVIDER_ID_PATTERN.test(input.providerId) ||
      !isBoundedString(input.requestedModel, 256) ||
      (input.planRevisionId !== null &&
        !TEAM_REV_ID_PATTERN.test(input.planRevisionId)) ||
      (input.actualModel !== null &&
        !isBoundedString(input.actualModel, 256)) ||
      !usageValid ||
      !successProofValid ||
      !failureProofValid
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/parent-calls/${input.invocationId}/settle`,
      {
        purpose: input.purpose,
        provider_id: input.providerId,
        requested_model: input.requestedModel,
        state: input.state,
        plan_revision_id: input.planRevisionId,
        actual_model: input.actualModel,
        input_tokens: input.inputTokens,
        output_tokens: input.outputTokens,
        total_tokens: input.totalTokens,
        output_sha256: input.outputSha256,
        error_code: input.errorCode,
      },
      (value) => {
        const parsed = parseTeamParentCallWrapper(value);
        if (parsed === null) return null;
        const parentCall = parsed.parentCall;
        return parentCall.invocationId === input.invocationId &&
          parentCall.teamRunId === input.teamRunId &&
          parentCall.purpose === input.purpose &&
          parentCall.providerId === input.providerId &&
          parentCall.requestedModel === input.requestedModel &&
          parentCall.state === input.state &&
          parentCall.planRevisionId === input.planRevisionId &&
          parentCall.actualModel === input.actualModel &&
          parentCall.inputTokens === input.inputTokens &&
          parentCall.outputTokens === input.outputTokens &&
          parentCall.totalTokens === input.totalTokens &&
          parentCall.outputSha256 === input.outputSha256 &&
          parentCall.errorCode === input.errorCode
          ? parsed
          : null;
      },
    );
  }

  createTeamNode(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly assignmentId: string;
    readonly employeeRoleId: SpecialistEmployeeId;
    readonly invocationId: string;
    readonly waveId: string;
    readonly nodeEpoch: number;
    readonly sendEpoch: number;
    readonly providerId: string;
    readonly requestedModel: string;
  }): Promise<
    DesktopOperationResult<{
      readonly node: {
        readonly id: string;
        readonly ordinal: number;
        readonly invocationId: string;
      };
    }>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/nodes`,
      {
        assignment_id: input.assignmentId,
        employee_role_id: input.employeeRoleId,
        invocation_id: input.invocationId,
        wave_id: input.waveId,
        node_epoch: input.nodeEpoch,
        send_epoch: input.sendEpoch,
        provider_id: input.providerId,
        requested_model: input.requestedModel,
      },
      parseTeamNodeCreate,
    );
  }

  updateTeamNode(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly nodeId: string;
    readonly state: string;
    readonly actualModel: string | null;
    readonly inputTokens: number | null;
    readonly outputTokens: number | null;
    readonly totalTokens: number | null;
    readonly answerSha256: string | null;
    readonly errorCode: string | null;
    readonly durationMs: number | null;
  }): Promise<
    DesktopOperationResult<{
      readonly updated: true;
      readonly id: string;
      readonly state: string;
    }>
  > {
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/nodes/${input.nodeId}`,
      {
        state: input.state,
        actual_model: input.actualModel,
        input_tokens: input.inputTokens,
        output_tokens: input.outputTokens,
        total_tokens: input.totalTokens,
        answer_sha256: input.answerSha256,
        error_code: input.errorCode,
        duration_ms: input.durationMs,
      },
      parseTeamNodeUpdate,
    );
  }

  settleTeamNode(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly nodeId: string;
    readonly invocationId: string;
    readonly state: string;
    readonly actualModel: string | null;
    readonly inputTokens: number | null;
    readonly outputTokens: number | null;
    readonly totalTokens: number | null;
    readonly answerSha256: string | null;
    readonly errorCode: string | null;
    readonly durationMs: number | null;
    readonly waveId: string;
    readonly nodeEpoch: number;
    readonly sendEpoch: number;
    readonly report: EmployeeTeamReport;
  }): Promise<
    DesktopOperationResult<{
      readonly updated: true;
      readonly id: string;
      readonly state: string;
    }>
  > {
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/nodes/${input.nodeId}/settle`,
      {
        state: input.state,
        actual_model: input.actualModel,
        input_tokens: input.inputTokens,
        output_tokens: input.outputTokens,
        total_tokens: input.totalTokens,
        answer_sha256: input.answerSha256,
        error_code: input.errorCode,
        duration_ms: input.durationMs,
        invocation_id: input.invocationId,
        assignment_id: input.report.assignmentId,
        employee_role_id: input.report.employeeRoleId,
        status: input.report.status,
        report: input.report.report,
        collaboration_requests: input.report.collaborationRequests.map(
          (item) => ({
            targetRoleId: item.targetRoleId,
            question: item.question,
            reason: item.reason,
          }),
        ),
        wave_id: input.waveId,
        node_epoch: input.nodeEpoch,
        send_epoch: input.sendEpoch,
      },
      parseTeamNodeUpdate,
    );
  }

  recordTeamReport(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly nodeId: string;
    readonly invocationId: string;
    readonly report: EmployeeTeamReport;
  }): Promise<DesktopOperationResult<{ readonly recorded: true }>> {
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/reports`,
      {
        assignment_id: input.report.assignmentId,
        employee_role_id: input.report.employeeRoleId,
        status: input.report.status,
        report: input.report.report,
        node_id: input.nodeId,
        invocation_id: input.invocationId,
        collaboration_requests: input.report.collaborationRequests.map(
          (item) => ({
            targetRoleId: item.targetRoleId,
            question: item.question,
            reason: item.reason,
          }),
        ),
      },
      parseTeamReportAck,
    );
  }

  async #request<T>(
    method: NativeMethod,
    requestPath: string,
    body: Readonly<Record<string, unknown>> | undefined,
    parse: (value: unknown) => T | null,
    timeoutMs = 5_000,
    maxBytes = MAX_RESPONSE_BYTES,
  ): Promise<DesktopOperationResult<T>> {
    try {
      const response = await this.#fetch(
        `${this.#backendOrigin}${requestPath}`,
        {
          method,
          headers: {
            Accept: "application/json",
            ...(body === undefined
              ? {}
              : { "Content-Type": "application/json" }),
            "x-omnibase-desktop-native-control": this.#nativeControlToken,
          },
          body: body === undefined ? undefined : JSON.stringify(body),
          cache: "no-store",
          redirect: "error",
          signal: AbortSignal.timeout(timeoutMs),
        },
      );
      if (
        response.headers.has("x-omnibase-desktop-native-control") ||
        response.headers.has("x-omnibase-desktop-instance") ||
        response.headers.has("x-omnibase-desktop-challenge") ||
        response.headers.has("x-omnibase-desktop-proof")
      ) {
        return failure("desktop_native_response_invalid");
      }
      if (
        response.headers
          .get("content-type")
          ?.split(";", 1)[0]
          ?.trim()
          .toLowerCase() !== "application/json"
      ) {
        return failure("desktop_native_response_invalid");
      }
      const payload = await readBoundedJson(response, maxBytes);
      if (!response.ok) {
        return failure(
          parseErrorCode(payload) ?? "desktop_native_request_failed",
        );
      }
      const parsed = parse(payload);
      return parsed === null
        ? failure("desktop_native_response_invalid")
        : success(parsed);
    } catch {
      return failure("desktop_native_request_failed");
    }
  }
}
