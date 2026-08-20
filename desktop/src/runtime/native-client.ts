import type {
  DesktopAgentRole,
  DesktopAgentRoleIdInput,
  DesktopAgentRoleList,
  DesktopAgentRoleTestResult,
  DesktopAgentRoleUpdateInput,
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
const TEAM_NODE_ID_PATTERN = /^teamnode_[a-f0-9]{32}$/u;
const TEAM_REPORT_ID_PATTERN = /^teamrpt_[a-f0-9]{32}$/u;
const TEAM_REV_ID_PATTERN = /^teamrev_[a-f0-9]{32}$/u;
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

type FetchLike = typeof fetch;
type NativeMethod = "GET" | "POST" | "DELETE";

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
    if (
      !Number.isSafeInteger(parsed) ||
      parsed < 0 ||
      parsed > limit
    ) {
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
  if (!isRecord(value) || !hasExactKeys(value, ["agent"]) || !isRecord(value.agent)) {
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
  if (!isRecord(value) || !hasExactKeys(value, ["items"]) || !Array.isArray(value.items)) {
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
      if (typeof value.actual_model !== "string" || value.actual_model.length === 0) {
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
  if (!isRecord(value) || !hasExactKeys(value, ["items"]) || !Array.isArray(value.items)) {
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

function parseConversationCreated(
  value: unknown,
): { readonly created: true; readonly conversation: DesktopConversation } | null {
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
    (value.actual_model !== null && !isBoundedString(value.actual_model, 256)) ||
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
    (value.error_redacted !== null && typeof value.error_redacted !== "string") ||
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

function parseCancelResult(
  value: unknown,
): {
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
    messageId: typeof payload.message_id === "string" ? payload.message_id : undefined,
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
        typeof payload.provider_name === "string" ? payload.provider_name : undefined,
      requestedModel:
        typeof payload.requested_model === "string" ? payload.requested_model : undefined,
      family: typeof payload.family === "string" ? payload.family : undefined,
      gear: typeof payload.gear === "string" ? payload.gear : undefined,
      thinkingDepth:
        typeof payload.thinking_depth === "string" ? payload.thinking_depth : undefined,
    });
  }
  if (eventName === "done" || eventName === "cancelled" || eventName === "error") {
    return Object.freeze({
      type: eventName,
      ...scoped,
      answer: typeof payload.answer === "string" ? payload.answer : undefined,
      actualModel:
        payload.actual_model === null || typeof payload.actual_model === "string"
          ? payload.actual_model
          : undefined,
      status: typeof payload.status === "string" ? payload.status : undefined,
      durationMs: typeof payload.duration_ms === "number" ? payload.duration_ms : undefined,
      inputTokens:
        payload.input_tokens === null || typeof payload.input_tokens === "number"
          ? payload.input_tokens
          : undefined,
      outputTokens:
        payload.output_tokens === null || typeof payload.output_tokens === "number"
          ? payload.output_tokens
          : undefined,
      totalTokens:
        payload.total_tokens === null || typeof payload.total_tokens === "number"
          ? payload.total_tokens
          : undefined,
      errorCode: typeof payload.error_code === "string" ? payload.error_code : undefined,
      errorRedacted:
        typeof payload.error_redacted === "string" ? payload.error_redacted : undefined,
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

async function releaseStreamReader(reader: ReadableStreamDefaultReader<Uint8Array>): Promise<void> {
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
          if (line.startsWith("event:")) eventName = line.slice(6).trim() || "message";
          if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
        }
        const parsed = parseStreamEvent(eventName, dataLines.join("\n"));
        if (parsed === null) continue;
        const stamped = stampSendEpoch(parsed, sendEpoch);
        if (stamped.type === "identity") invocationId = stamped.invocationId;
        emit(stamped);
        if (stamped.type === "done" || stamped.type === "cancelled" || stamped.type === "error") {
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
  if (!isRecord(value) || !hasExactKeys(value, ["items"]) || !Array.isArray(value.items)) {
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
    (value.current_wave_id !== null && !isBoundedString(value.current_wave_id, 128)) ||
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

function parseTeamRunList(
  value: unknown,
): { readonly items: readonly DesktopTeamRun[] } | null {
  if (!isRecord(value) || !hasExactKeys(value, ["items"]) || !Array.isArray(value.items)) {
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

function parseProposalResult(value: unknown): DesktopTeamRunProposalResult | null {
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
  if (!isRecord(value) || !hasExactKeys(value, ["blackboard"]) || !isRecord(value.blackboard)) {
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
        fromAssignmentId: row.from_assignment_id,
        fromEmployeeRoleId: row.from_employee_role_id as SpecialistEmployeeId,
        targetRoleId: row.target_role_id as SpecialistEmployeeId,
        question: row.question,
        reason: row.reason,
        parentDecision: row.parent_decision as DesktopTeamCollaborationRequest["parentDecision"],
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

function parseTeamReportAck(value: unknown): { readonly recorded: true } | null {
  if (!isRecord(value) || !isRecord(value.report)) return null;
  return Object.freeze({ recorded: true as const });
}

function parseTeamNodeCreate(value: unknown): {
  readonly node: { readonly id: string; readonly ordinal: number; readonly invocationId: string };
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

  listProviders(): Promise<DesktopOperationResult<DesktopProviderList>> {
    return this.#request("GET", "/desktop/v1/providers", undefined, parseProviderList);
  }

  upsertProvider(
    body: Readonly<Record<string, unknown>>,
  ): Promise<DesktopOperationResult<DesktopProviderMutationResult>> {
    return this.#request("POST", "/desktop/v1/providers", body, parseProviderMutation);
  }

  deleteProvider(
    input: DesktopProviderIdInput,
  ): Promise<DesktopOperationResult<{ readonly deleted: true; readonly id: string }>> {
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

  createConversation(
    input: DesktopConversationCreateInput,
  ): Promise<
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

  cancelInvocation(
    invocationId: string,
  ): Promise<
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
        return failure(parseErrorCode(payload) ?? "desktop_native_request_failed");
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
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId) || !EMPLOYEE_ROLE_SET.has(input.roleId)) {
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
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId) || !EMPLOYEE_ROLE_SET.has(input.roleId)) {
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
    if (!WORKSPACE_ID_PATTERN.test(input.workspaceId) || !EMPLOYEE_ROLE_SET.has(input.roleId)) {
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
  ): Promise<DesktopOperationResult<{ readonly items: readonly DesktopTeamRun[] }>> {
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
          : { allowed_specialist_role_ids: [...input.allowedSpecialistRoleIds] }),
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
  ): Promise<DesktopOperationResult<{ readonly blackboard: PersonalTeamBlackboard }>> {
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

  consumeTeamProviderCall(input: DesktopTeamRunIdInput): Promise<
    DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>
  > {
    if (
      !WORKSPACE_ID_PATTERN.test(input.workspaceId) ||
      !TEAM_RUN_ID_PATTERN.test(input.teamRunId)
    ) {
      return Promise.resolve(failure("desktop_native_input_invalid"));
    }
    return this.#request(
      "POST",
      `/desktop/v1/workspaces/${input.workspaceId}/team-runs/${input.teamRunId}/consume-call`,
      undefined,
      parseTeamRunWrapper,
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
      readonly node: { readonly id: string; readonly ordinal: number; readonly invocationId: string };
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
  }): Promise<DesktopOperationResult<{ readonly updated: true; readonly id: string; readonly state: string }>> {
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
  }): Promise<DesktopOperationResult<{ readonly updated: true; readonly id: string; readonly state: string }>> {
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
        collaboration_requests: input.report.collaborationRequests.map((item) => ({
          targetRoleId: item.targetRoleId,
          question: item.question,
          reason: item.reason,
        })),
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
        collaboration_requests: input.report.collaborationRequests.map((item) => ({
          targetRoleId: item.targetRoleId,
          question: item.question,
          reason: item.reason,
        })),
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
