export const IPC_CHANNELS = Object.freeze({
  appGetVersion: "omnibase:app:get-version",
  runtimeGetStatus: "omnibase:runtime:get-status",
  runtimeRetryStartup: "omnibase:runtime:retry-startup",
  ownerGetStatus: "omnibase:owner:get-status",
  ownerBootstrap: "omnibase:owner:bootstrap",
  workspacesList: "omnibase:workspaces:list",
  workspacesCreate: "omnibase:workspaces:create",
  workspacesArchive: "omnibase:workspaces:archive",
  workspaceAgent: "omnibase:workspace:agent",
  providersList: "omnibase:providers:list",
  providersUpsert: "omnibase:providers:upsert",
  providersDelete: "omnibase:providers:delete",
  providersTest: "omnibase:providers:test",
  conversationsList: "omnibase:conversations:list",
  conversationsCreate: "omnibase:conversations:create",
  conversationsArchive: "omnibase:conversations:archive",
  conversationsGet: "omnibase:conversations:get",
  conversationSend: "omnibase:conversation:send",
  conversationCancel: "omnibase:conversation:cancel",
  conversationAbortInFlightSend: "omnibase:conversation:abort-in-flight-send",
} as const);

export const IPC_EVENT_CHANNELS = Object.freeze({
  conversationEvent: "omnibase:conversation:event",
} as const);

export type RuntimePhase = "stopped" | "starting" | "ready" | "failed";

export interface RuntimeStatus {
  readonly phase: RuntimePhase;
  readonly attempts: number;
  readonly lastError: string | null;
}

export interface DesktopOwner {
  readonly id: string;
  readonly displayName: string;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopOwnerStatus {
  readonly initialized: boolean;
  readonly owner: DesktopOwner | null;
}

export interface DesktopOwnerBootstrapResult extends DesktopOwnerStatus {
  readonly initialized: true;
  readonly created: boolean;
  readonly owner: DesktopOwner;
}

export type DesktopWorkspaceState = "active" | "archived";

export interface DesktopWorkspace {
  readonly id: string;
  readonly ownerId: string;
  readonly name: string;
  readonly state: DesktopWorkspaceState;
  readonly rowVersion: number;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopWorkspaceList {
  readonly items: readonly DesktopWorkspace[];
}

export interface DesktopWorkspaceMutationResult {
  readonly workspace: DesktopWorkspace;
}

export type DesktopOperationResult<T> =
  | Readonly<{ ok: true; value: T }>
  | Readonly<{ ok: false; error: Readonly<{ code: string }> }>;

export interface DesktopOwnerBootstrapInput {
  readonly displayName: string;
}

export interface DesktopWorkspaceCreateInput {
  readonly name: string;
}

export interface DesktopWorkspaceArchiveInput {
  readonly workspaceId: string;
  readonly expectedRowVersion: number;
}

export type DesktopProviderFamily =
  | "deepseek"
  | "openai"
  | "anthropic"
  | "glm"
  | "kimi"
  | "generic-openai-compatible";

export type DesktopReasoningGear = "economy" | "standard" | "deep" | "audit";
export type DesktopThinkingDepth = "disabled" | "low" | "medium" | "high";

export interface DesktopProvider {
  readonly id: string;
  readonly displayName: string;
  readonly baseUrl: string;
  readonly modelName: string;
  readonly family: DesktopProviderFamily;
  readonly gear: DesktopReasoningGear;
  readonly thinkingDepth: DesktopThinkingDepth;
  readonly timeoutSeconds: number;
  readonly allowLoopbackHttp: boolean;
  readonly isDefault: boolean;
  readonly isEnabled: boolean;
  readonly hasSecret: true;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopProviderList {
  readonly items: readonly DesktopProvider[];
}

export interface DesktopProviderMutationResult {
  readonly provider: DesktopProvider;
}

export interface DesktopProviderTestResult {
  readonly ok: boolean;
  readonly providerId: string;
  readonly providerName: string;
  readonly requestedModel: string;
  readonly actualModel: string | null;
  readonly identityProven: boolean;
  readonly family: string;
  readonly latencyMs?: number;
  readonly errorCode?: string;
  readonly errorRedacted?: string;
}

export interface DesktopProviderUpsertInput {
  readonly id?: string;
  readonly displayName: string;
  readonly baseUrl: string;
  readonly apiKey?: string;
  readonly modelName: string;
  readonly gear: DesktopReasoningGear;
  readonly thinkingDepth: DesktopThinkingDepth;
  readonly timeoutSeconds: number;
  readonly allowLoopbackHttp: boolean;
  readonly isDefault: boolean;
  readonly isEnabled: boolean;
}

export interface DesktopProviderIdInput {
  readonly providerId: string;
}

export interface DesktopWorkspaceIdInput {
  readonly workspaceId: string;
}

export interface DesktopParentAgent {
  readonly id: string;
  readonly workspaceId: string;
  readonly role: "parent";
  readonly displayName: string;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopConversation {
  readonly id: string;
  readonly workspaceId: string;
  readonly title: string;
  readonly state: "active" | "archived";
  readonly rowVersion: number;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopInvocation {
  readonly id: string;
  readonly providerId: string;
  readonly requestedModel: string;
  readonly actualModel: string | null;
  readonly family: string;
  readonly gear: string;
  readonly thinkingDepth: string;
  readonly status: "running" | "succeeded" | "failed" | "cancelled" | "unknown";
  readonly durationMs: number | null;
  readonly inputTokens: number | null;
  readonly outputTokens: number | null;
  readonly totalTokens: number | null;
  readonly errorCode: string | null;
  readonly errorRedacted: string | null;
  readonly retryOfInvocationId: string | null;
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopMessage {
  readonly id: string;
  readonly role: "user" | "assistant";
  readonly content: string;
  readonly status: "streaming" | "completed" | "cancelled" | "failed" | "unknown";
  readonly invocationId: string | null;
  readonly retryOfMessageId: string | null;
  readonly createdAt: string;
  readonly invocation: DesktopInvocation | null;
}

export interface DesktopConversationList {
  readonly items: readonly DesktopConversation[];
}

export interface DesktopConversationDetail {
  readonly conversation: DesktopConversation;
  readonly messages: readonly DesktopMessage[];
}

export interface DesktopConversationCreateInput {
  readonly workspaceId: string;
  readonly title?: string;
}

export interface DesktopConversationArchiveInput {
  readonly workspaceId: string;
  readonly conversationId: string;
  readonly expectedRowVersion: number;
}

export interface DesktopConversationGetInput {
  readonly workspaceId: string;
  readonly conversationId: string;
}

export interface DesktopConversationSendInput {
  readonly workspaceId: string;
  readonly conversationId: string;
  readonly content: string;
  readonly providerId?: string;
  readonly retryOfMessageId?: string;
  readonly sendEpoch?: number;
}

export interface DesktopConversationCancelInput {
  readonly invocationId: string;
}

export interface DesktopConversationEvent {
  readonly type: "identity" | "delta" | "done" | "cancelled" | "error";
  readonly invocationId: string;
  readonly workspaceId?: string;
  readonly conversationId?: string;
  readonly messageId?: string;
  readonly text?: string;
  readonly answer?: string;
  readonly providerName?: string;
  readonly requestedModel?: string;
  readonly actualModel?: string | null;
  readonly family?: string;
  readonly gear?: string;
  readonly thinkingDepth?: string;
  readonly status?: string;
  readonly durationMs?: number;
  readonly inputTokens?: number | null;
  readonly outputTokens?: number | null;
  readonly totalTokens?: number | null;
  readonly errorCode?: string;
  readonly errorRedacted?: string;
  readonly sendEpoch?: number;
}

export interface OmniBaseDesktopApi {
  readonly app: {
    readonly getVersion: () => Promise<string>;
  };
  readonly runtime: {
    readonly getStatus: () => Promise<RuntimeStatus>;
    readonly retryStartup: () => Promise<RuntimeStatus>;
  };
  readonly owner: {
    readonly getStatus: () => Promise<
      DesktopOperationResult<DesktopOwnerStatus>
    >;
    readonly bootstrap: (
      input: DesktopOwnerBootstrapInput,
    ) => Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>>;
  };
  readonly workspaces: {
    readonly list: () => Promise<DesktopOperationResult<DesktopWorkspaceList>>;
    readonly create: (
      input: DesktopWorkspaceCreateInput,
    ) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>;
    readonly archive: (
      input: DesktopWorkspaceArchiveInput,
    ) => Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>>;
    readonly agent: (
      input: DesktopWorkspaceIdInput,
    ) => Promise<DesktopOperationResult<{ readonly agent: DesktopParentAgent }>>;
  };
  readonly providers: {
    readonly list: () => Promise<DesktopOperationResult<DesktopProviderList>>;
    readonly upsert: (
      input: DesktopProviderUpsertInput,
    ) => Promise<DesktopOperationResult<DesktopProviderMutationResult>>;
    readonly delete: (
      input: DesktopProviderIdInput,
    ) => Promise<DesktopOperationResult<{ readonly deleted: true; readonly id: string }>>;
    readonly test: (
      input: DesktopProviderIdInput,
    ) => Promise<DesktopOperationResult<DesktopProviderTestResult>>;
  };
  readonly conversations: {
    readonly list: (
      input: DesktopWorkspaceIdInput,
    ) => Promise<DesktopOperationResult<DesktopConversationList>>;
    readonly create: (
      input: DesktopConversationCreateInput,
    ) => Promise<DesktopOperationResult<{ readonly created: true; readonly conversation: DesktopConversation }>>;
    readonly archive: (
      input: DesktopConversationArchiveInput,
    ) => Promise<DesktopOperationResult<{ readonly conversation: DesktopConversation }>>;
    readonly get: (
      input: DesktopConversationGetInput,
    ) => Promise<DesktopOperationResult<DesktopConversationDetail>>;
    readonly send: (
      input: DesktopConversationSendInput,
    ) => Promise<DesktopOperationResult<DesktopConversationEvent>>;
    readonly cancel: (
      input: DesktopConversationCancelInput,
    ) => Promise<DesktopOperationResult<{ readonly cancelled: boolean; readonly id: string; readonly accepted: boolean }>>;
    readonly abortInFlightSend: () => Promise<
      DesktopOperationResult<{ readonly aborted: boolean }>
    >;
    readonly subscribe: (
      listener: (event: DesktopConversationEvent) => void,
    ) => () => void;
  };
}

export const IPC_CHANNEL_SET: ReadonlySet<string> = new Set(
  Object.values(IPC_CHANNELS),
);

export function requireNoIpcArguments(args: readonly unknown[]): void {
  if (args.length !== 0) {
    throw new Error("ipc_arguments_not_allowed");
  }
}
