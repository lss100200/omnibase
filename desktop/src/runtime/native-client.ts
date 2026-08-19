import type {
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
  DesktopWorkspace,
  DesktopWorkspaceArchiveInput,
  DesktopWorkspaceCreateInput,
  DesktopWorkspaceIdInput,
  DesktopWorkspaceList,
  DesktopWorkspaceMutationResult,
} from "../shared/ipc-contract.ts";

const TOKEN_PATTERN = /^[a-f0-9]{64}$/u;
const ERROR_CODE_PATTERN = /^[a-z][a-z0-9_]{2,95}$/u;
const OWNER_ID_PATTERN = /^owner_[a-f0-9]{32}$/u;
const WORKSPACE_ID_PATTERN = /^workspace_[a-f0-9]{32}$/u;
const PROVIDER_ID_PATTERN = /^provider_[a-f0-9]{32}$/u;
const CONVERSATION_ID_PATTERN = /^conversation_[a-f0-9]{32}$/u;
const AGENT_ID_PATTERN = /^agent_[a-f0-9]{32}$/u;
const MESSAGE_ID_PATTERN = /^message_[a-f0-9]{32}$/u;
const INVOCATION_ID_PATTERN = /^invocation_[a-f0-9]{32}$/u;
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
        Object.freeze({
          type: "cancelled",
          invocationId: "invocation_cancelled_locally",
          errorRedacted: "生成已停止",
        }) satisfies DesktopConversationEvent,
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
        if (parsed.type === "identity") invocationId = parsed.invocationId;
        emit(parsed);
        if (parsed.type === "done" || parsed.type === "cancelled" || parsed.type === "error") {
          terminal = parsed;
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
      Object.freeze({
        type: "cancelled",
        invocationId: invocationId ?? "invocation_cancelled_locally",
        errorRedacted: "生成已停止",
      }) satisfies DesktopConversationEvent,
    );
  }
  return terminal === null
    ? failure("desktop_native_request_failed")
    : success(terminal);
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
      );
    } catch {
      if (signal.aborted) {
        return success(
          Object.freeze({
            type: "cancelled",
            invocationId: "invocation_cancelled_locally",
            workspaceId: input.workspaceId,
            conversationId: input.conversationId,
            errorRedacted: "生成已停止",
          }) satisfies DesktopConversationEvent,
        );
      }
      return failure("desktop_native_request_failed");
    }
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
