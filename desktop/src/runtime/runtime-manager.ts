import { createHmac, randomBytes, timingSafeEqual } from "node:crypto";
import { lstat, mkdir } from "node:fs/promises";
import path from "node:path";

import type {
  DesktopConversation,
  DesktopConversationArchiveInput,
  DesktopConversationCancelInput,
  DesktopConversationCreateInput,
  DesktopConversationDetail,
  DesktopConversationEvent,
  DesktopConversationGetInput,
  DesktopConversationList,
  DesktopConversationSendInput,
  DesktopOperationResult,
  DesktopOwnerBootstrapInput,
  DesktopOwnerBootstrapResult,
  DesktopOwnerStatus,
  DesktopParentAgent,
  DesktopProviderIdInput,
  DesktopProviderList,
  DesktopProviderMutationResult,
  DesktopProviderTestResult,
  DesktopProviderUpsertInput,
  DesktopAgentRole,
  DesktopAgentRoleIdInput,
  DesktopAgentRoleList,
  DesktopAgentRoleTestResult,
  DesktopAgentRoleUpdateInput,
  DesktopTeamCollaborationInput,
  DesktopTeamCollaborationRequest,
  DesktopTeamRun,
  DesktopTeamRunEvent,
  DesktopTeamRunExecuteInput,
  DesktopTeamRunIdInput,
  DesktopTeamRunProof,
  DesktopTeamRunProposalResult,
  DesktopTeamRunStartInput,
  DesktopTeamRunSubmitProposalInput,
  DesktopWorkspaceArchiveInput,
  DesktopWorkspaceCreateInput,
  DesktopWorkspaceIdInput,
  DesktopWorkspaceList,
  DesktopWorkspaceMutationResult,
  PersonalTeamBlackboard,
  RuntimeStatus,
} from "../shared/ipc-contract.ts";
import { verifyRuntimeBundle } from "./manifest.ts";
import { DesktopNativeClient } from "./native-client.ts";
import { PersonalTeamCoordinator } from "./personal-team-coordinator.ts";
import { createNativePersonalTeamHost } from "./personal-team-native-host.ts";
import { createOpenAiCompatibleTransport } from "./personal-team-provider.ts";

import {
  decryptProviderSecret,
  encryptProviderSecret,
  type DesktopSafeStorage,
} from "./secret-vault.ts";
import { redactRuntimeError, RuntimeSupervisor } from "./supervisor.ts";

export interface RuntimeManagerNativeSendClient {
  readonly listProviders: DesktopNativeClient["listProviders"];
  readonly getProviderVault: DesktopNativeClient["getProviderVault"];
  readonly sendConversation: DesktopNativeClient["sendConversation"];
}

export interface RuntimeManagerOptions {
  readonly runtimeRoot: string;
  readonly expectedManifestSha256: string;
  readonly uiOrigin: string;
  readonly dataRoot: string;
  readonly hostEnvironment?: Readonly<Record<string, string | undefined>>;
  readonly secretVault?: DesktopSafeStorage;
  readonly nativeClientForTests?: RuntimeManagerNativeSendClient;
}

const SEND_ABORTED = Object.freeze({ aborted: true as const });

function teamEventFromRun(
  teamRun: DesktopTeamRun,
  event: Omit<DesktopTeamRunEvent, "teamRunId" | "workspaceId"> &
    Partial<Pick<DesktopTeamRunEvent, "teamRunId" | "workspaceId">>,
): DesktopTeamRunEvent {
  return {
    planRevisionId: teamRun.currentPlanRevisionId ?? "",
    waveId: teamRun.currentWaveId ?? "",
    assignmentId: "",
    nodeId: "",
    sendEpoch: 0,
    rosterEpoch: 0,
    conversationId: teamRun.conversationId,
    ...event,
    teamRunId: teamRun.id,
    workspaceId: teamRun.workspaceId,
  };
}

function isSendAborted(
  value: unknown,
): value is typeof SEND_ABORTED {
  return value === SEND_ABORTED;
}

async function raceAbort<T>(
  promise: Promise<T>,
  signal: AbortSignal,
): Promise<T | typeof SEND_ABORTED> {
  if (signal.aborted) {
    void promise.catch(() => undefined);
    return SEND_ABORTED;
  }
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      signal.removeEventListener("abort", onAbort);
      void promise.catch(() => undefined);
      resolve(SEND_ABORTED);
    };
    signal.addEventListener("abort", onAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", onAbort);
        resolve(value);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error);
      },
    );
  });
}

const SAFE_HOST_ENVIRONMENT_KEYS = Object.freeze([
  "SystemRoot",
  "WINDIR",
  "TEMP",
  "TMP",
] as const);

export function buildRuntimeEnvironment(
  nativeProofKey: string,
  nativeControlToken: string,
  dataRoot: string,
  hostEnvironment: Readonly<Record<string, string | undefined>> = process.env,
): Readonly<Record<string, string>> {
  if (
    !/^[a-f0-9]{64}$/u.test(nativeProofKey) ||
    !/^[a-f0-9]{64}$/u.test(nativeControlToken) ||
    !path.isAbsolute(dataRoot)
  ) {
    throw new Error("runtime_environment_invalid");
  }
  const environment: Record<string, string> = {
    OMNIBASE_DESKTOP_MODE: "1",
    OMNIBASE_BIND_HOST: "127.0.0.1",
    OMNIBASE_DESKTOP_NATIVE_PROOF_KEY: nativeProofKey,
    OMNIBASE_DESKTOP_NATIVE_CONTROL_TOKEN: nativeControlToken,
    OMNIBASE_DESKTOP_DATA_ROOT: dataRoot,
  };
  for (const key of SAFE_HOST_ENVIRONMENT_KEYS) {
    const value = hostEnvironment[key];
    if (
      typeof value === "string" &&
      value.length > 0 &&
      value.length <= 32_767 &&
      !value.includes("\0") &&
      !value.includes("\r") &&
      !value.includes("\n")
    ) {
      environment[key] = value;
    }
  }
  return Object.freeze(environment);
}

export function matchesRuntimeInstanceProof(
  actual: string | null,
  challenge: string,
  instanceToken: string,
): boolean {
  if (
    actual === null ||
    !/^[a-f0-9]{64}$/u.test(actual) ||
    !/^[a-f0-9]{64}$/u.test(challenge) ||
    !/^[a-f0-9]{64}$/u.test(instanceToken)
  ) {
    return false;
  }
  const expected = createHmac("sha256", Buffer.from(instanceToken, "hex"))
    .update(challenge, "ascii")
    .digest();
  return timingSafeEqual(Buffer.from(actual, "hex"), expected);
}

export async function prepareRuntimeDataRoot(dataRoot: string): Promise<void> {
  if (
    !path.isAbsolute(dataRoot) ||
    path.normalize(dataRoot) === path.parse(dataRoot).root
  ) {
    throw new Error("runtime_data_root_invalid");
  }
  try {
    await mkdir(dataRoot, { recursive: false, mode: 0o700 });
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== "EEXIST") {
      throw new Error("runtime_data_root_unavailable");
    }
  }
  try {
    const metadata = await lstat(dataRoot);
    if (!metadata.isDirectory() || metadata.isSymbolicLink()) {
      throw new Error("runtime_data_root_identity_invalid");
    }
  } catch (error) {
    if (
      error instanceof Error &&
      error.message === "runtime_data_root_identity_invalid"
    ) {
      throw error;
    }
    throw new Error("runtime_data_root_unavailable");
  }
}

export class RuntimeManager {
  readonly #options: RuntimeManagerOptions;
  #supervisor: RuntimeSupervisor | null = null;
  #nativeClient: DesktopNativeClient | null = null;
  #streamAbort: AbortController | null = null;
  #sendInFlight = false;
  #pendingAbort = false;
  #teamCoordinator: PersonalTeamCoordinator | null = null;
  #teamInFlight = false;
  #pendingTeamAbort = false;
  #generation = 0;
  #startOperation: {
    readonly generation: number;
    readonly promise: Promise<RuntimeStatus>;
  } | null = null;
  #status: RuntimeStatus = Object.freeze({
    phase: "stopped",
    attempts: 0,
    lastError: null,
  });

  constructor(options: RuntimeManagerOptions) {
    if (!path.isAbsolute(options.dataRoot)) {
      throw new Error("runtime_data_root_must_be_absolute");
    }
    this.#options = options;
  }

  getStatus(): RuntimeStatus {
    return this.#supervisor?.getStatus() ?? this.#status;
  }

  getOwnerStatus(): Promise<DesktopOperationResult<DesktopOwnerStatus>> {
    const client = this.#readyNativeClient();
    return (
      client?.getOwnerStatus() ??
      Promise.resolve(this.#nativeUnavailable<DesktopOwnerStatus>())
    );
  }

  bootstrapOwner(
    input: DesktopOwnerBootstrapInput,
  ): Promise<DesktopOperationResult<DesktopOwnerBootstrapResult>> {
    const client = this.#readyNativeClient();
    return (
      client?.bootstrapOwner(input) ??
      Promise.resolve(this.#nativeUnavailable<DesktopOwnerBootstrapResult>())
    );
  }

  listWorkspaces(): Promise<DesktopOperationResult<DesktopWorkspaceList>> {
    const client = this.#readyNativeClient();
    return (
      client?.listWorkspaces() ??
      Promise.resolve(this.#nativeUnavailable<DesktopWorkspaceList>())
    );
  }

  createWorkspace(
    input: DesktopWorkspaceCreateInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> {
    const client = this.#readyNativeClient();
    return (
      client?.createWorkspace(input) ??
      Promise.resolve(this.#nativeUnavailable<DesktopWorkspaceMutationResult>())
    );
  }

  archiveWorkspace(
    input: DesktopWorkspaceArchiveInput,
  ): Promise<DesktopOperationResult<DesktopWorkspaceMutationResult>> {
    const client = this.#readyNativeClient();
    return (
      client?.archiveWorkspace(input) ??
      Promise.resolve(this.#nativeUnavailable<DesktopWorkspaceMutationResult>())
    );
  }

  getWorkspaceAgent(
    input: DesktopWorkspaceIdInput,
  ): Promise<DesktopOperationResult<{ readonly agent: DesktopParentAgent }>> {
    const client = this.#readyNativeClient();
    return (
      client?.getWorkspaceAgent(input) ??
      Promise.resolve(
        this.#nativeUnavailable<{ readonly agent: DesktopParentAgent }>(),
      )
    );
  }

  listProviders(): Promise<DesktopOperationResult<DesktopProviderList>> {
    const client = this.#readyNativeClient();
    return (
      client?.listProviders() ??
      Promise.resolve(this.#nativeUnavailable<DesktopProviderList>())
    );
  }

  upsertProvider(
    input: DesktopProviderUpsertInput,
  ): Promise<DesktopOperationResult<DesktopProviderMutationResult>> {
    const client = this.#readyNativeClient();
    const vault = this.#options.secretVault;
    if (client === null) {
      return Promise.resolve(
        this.#nativeUnavailable<DesktopProviderMutationResult>(),
      );
    }
    try {
      const body: Record<string, unknown> = {
        id: input.id,
        display_name: input.displayName,
        base_url: input.baseUrl,
        model_name: input.modelName,
        gear: input.gear,
        thinking_depth: input.thinkingDepth,
        timeout_seconds: input.timeoutSeconds,
        allow_loopback_http: input.allowLoopbackHttp,
        is_default: input.isDefault,
        is_enabled: input.isEnabled,
      };
      if (input.apiKey !== undefined) {
        if (vault === undefined) {
          return Promise.resolve(this.#nativeUnavailable<DesktopProviderMutationResult>());
        }
        const encrypted = encryptProviderSecret(input.apiKey, vault);
        body.credential_reference = encrypted.credentialReference;
        body.encrypted_secret_blob = encrypted.encryptedSecretBlob;
        body.secret_fingerprint = encrypted.secretFingerprint;
      }
      if (input.id === undefined) delete body.id;
      return client.upsertProvider(body);
    } catch {
      return Promise.resolve(
        Object.freeze({
          ok: false,
          error: Object.freeze({ code: "desktop_secret_vault_unavailable" }),
        }),
      );
    }
  }

  deleteProvider(
    input: DesktopProviderIdInput,
  ): Promise<
    DesktopOperationResult<{ readonly deleted: true; readonly id: string }>
  > {
    const client = this.#readyNativeClient();
    return (
      client?.deleteProvider(input) ??
      Promise.resolve(
        this.#nativeUnavailable<{ readonly deleted: true; readonly id: string }>(),
      )
    );
  }

  async testProvider(
    input: DesktopProviderIdInput,
  ): Promise<DesktopOperationResult<DesktopProviderTestResult>> {
    const client = this.#readyNativeClient();
    const vault = this.#options.secretVault;
    if (client === null || vault === undefined) {
      return this.#nativeUnavailable<DesktopProviderTestResult>();
    }
    const material = await client.getProviderVault(input.providerId);
    if (!material.ok) return material;
    try {
      const secret = decryptProviderSecret(material.value.encryptedSecretBlob, vault);
      return await client.testProvider(input.providerId, secret);
    } catch {
      return Object.freeze({
        ok: false,
        error: Object.freeze({ code: "desktop_secret_vault_unavailable" }),
      });
    }
  }

  listConversations(
    input: DesktopWorkspaceIdInput,
  ): Promise<DesktopOperationResult<DesktopConversationList>> {
    const client = this.#readyNativeClient();
    return (
      client?.listConversations(input) ??
      Promise.resolve(this.#nativeUnavailable<DesktopConversationList>())
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
    const client = this.#readyNativeClient();
    return (
      client?.createConversation(input) ??
      Promise.resolve(
        this.#nativeUnavailable<{
          readonly created: true;
          readonly conversation: DesktopConversation;
        }>(),
      )
    );
  }

  archiveConversation(
    input: DesktopConversationArchiveInput,
  ): Promise<
    DesktopOperationResult<{ readonly conversation: DesktopConversation }>
  > {
    const client = this.#readyNativeClient();
    return (
      client?.archiveConversation(input) ??
      Promise.resolve(
        this.#nativeUnavailable<{ readonly conversation: DesktopConversation }>(),
      )
    );
  }

  getConversation(
    input: DesktopConversationGetInput,
  ): Promise<DesktopOperationResult<DesktopConversationDetail>> {
    const client = this.#readyNativeClient();
    return (
      client?.getConversation(input) ??
      Promise.resolve(this.#nativeUnavailable<DesktopConversationDetail>())
    );
  }

  async sendConversation(
    input: DesktopConversationSendInput,
    emit: (event: DesktopConversationEvent) => void,
  ): Promise<DesktopOperationResult<DesktopConversationEvent>> {
    const client = this.#readySendClient();
    const vault = this.#options.secretVault;
    if (client === null || vault === undefined) {
      return this.#nativeUnavailable<DesktopConversationEvent>();
    }
    const controller = this.#armStreamAbort();
    try {
      if (controller.signal.aborted) {
        return this.#cancelledSendResult(input);
      }
      const providers = await raceAbort(client.listProviders(), controller.signal);
      if (isSendAborted(providers)) {
        return this.#cancelledSendResult(input);
      }
      if (controller.signal.aborted) {
        return this.#cancelledSendResult(input);
      }
      if (!providers.ok) return providers;
      const selected =
        input.providerId === undefined
          ? providers.value.items.find((item) => item.isDefault && item.isEnabled)
          : providers.value.items.find((item) => item.id === input.providerId);
      if (selected === undefined) {
        return Object.freeze({
          ok: false,
          error: Object.freeze({ code: "desktop_provider_ambiguous" }),
        });
      }
      const material = await raceAbort(
        client.getProviderVault(selected.id),
        controller.signal,
      );
      if (isSendAborted(material)) {
        return this.#cancelledSendResult(input);
      }
      if (controller.signal.aborted) {
        return this.#cancelledSendResult(input);
      }
      if (!material.ok) return material;
      let secret: string;
      try {
        secret = decryptProviderSecret(material.value.encryptedSecretBlob, vault);
      } catch {
        return Object.freeze({
          ok: false,
          error: Object.freeze({ code: "desktop_secret_vault_unavailable" }),
        });
      }
      if (controller.signal.aborted) {
        return this.#cancelledSendResult(input);
      }
      const emitWithEpoch = (event: DesktopConversationEvent) => {
        emit(
          input.sendEpoch === undefined
            ? event
            : Object.freeze({ ...event, sendEpoch: input.sendEpoch }),
        );
      };
      const result = await client.sendConversation(
        input,
        secret,
        emitWithEpoch,
        controller.signal,
      );
      if (!result.ok || input.sendEpoch === undefined) return result;
      return Object.freeze({
        ok: true as const,
        value: Object.freeze({ ...result.value, sendEpoch: input.sendEpoch }),
      });
    } finally {
      this.#releaseStreamAbort(controller);
    }
  }

  async cancelConversation(
    input: DesktopConversationCancelInput,
  ): Promise<
    DesktopOperationResult<{
      readonly cancelled: boolean;
      readonly id: string;
      readonly accepted: boolean;
    }>
  > {
    this.#requestStreamAbort();
    const client = this.#readyNativeClient();
    return (
      client?.cancelInvocation(input.invocationId) ??
      Promise.resolve(
        this.#nativeUnavailable<{
          readonly cancelled: boolean;
          readonly id: string;
          readonly accepted: boolean;
        }>(),
      )
    );
  }

  abortInFlightSend(): Promise<
    DesktopOperationResult<{ readonly aborted: boolean }>
  > {
    const sendAborted = this.#requestStreamAbort();
    this.#teamCoordinator?.requestStop();
    let teamAborted = this.#teamCoordinator?.live === true;
    if (!teamAborted && this.#teamInFlight) {
      this.#pendingTeamAbort = true;
      teamAborted = true;
    }
    return Promise.resolve(
      Object.freeze({
        ok: true,
        value: Object.freeze({ aborted: sendAborted || teamAborted }),
      }),
    );
  }

  listAgentRoles(
    input: DesktopWorkspaceIdInput,
  ): Promise<DesktopOperationResult<DesktopAgentRoleList>> {
    const client = this.#readyNativeClient();
    return (
      client?.listAgentRoles(input) ??
      Promise.resolve(this.#nativeUnavailable<DesktopAgentRoleList>())
    );
  }

  getAgentRole(
    input: DesktopAgentRoleIdInput,
  ): Promise<DesktopOperationResult<{ readonly role: DesktopAgentRole }>> {
    const client = this.#readyNativeClient();
    return (
      client?.getAgentRole(input) ??
      Promise.resolve(this.#nativeUnavailable<{ readonly role: DesktopAgentRole }>())
    );
  }

  updateAgentRole(
    input: DesktopAgentRoleUpdateInput,
  ): Promise<DesktopOperationResult<{ readonly role: DesktopAgentRole }>> {
    const client = this.#readyNativeClient();
    return (
      client?.updateAgentRole(input) ??
      Promise.resolve(this.#nativeUnavailable<{ readonly role: DesktopAgentRole }>())
    );
  }

  testAgentRole(
    input: DesktopAgentRoleIdInput,
  ): Promise<DesktopOperationResult<DesktopAgentRoleTestResult>> {
    const client = this.#readyNativeClient();
    return (
      client?.testAgentRole(input) ??
      Promise.resolve(this.#nativeUnavailable<DesktopAgentRoleTestResult>())
    );
  }

  async startTeamRun(
    input: DesktopTeamRunStartInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ): Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>> {
    const client = this.#readyNativeClient();
    if (client === null) {
      return this.#nativeUnavailable<{ readonly teamRun: DesktopTeamRun }>();
    }
    const result = await client.startTeamRun(input);
    if (result.ok) {
      emit(
        teamEventFromRun(result.value.teamRun, {
          type: "snapshot",
          conversationId: result.value.teamRun.conversationId,
          state: result.value.teamRun.state,
        }),
      );
    }
    return result;
  }

  async cancelTeamRun(
    input: DesktopTeamRunIdInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ): Promise<
    DesktopOperationResult<{
      readonly cancelled: boolean;
      readonly accepted: boolean;
      readonly teamRun: DesktopTeamRun;
    }>
  > {
    const client = this.#readyNativeClient();
    if (client === null) {
      return this.#nativeUnavailable<{
        readonly cancelled: boolean;
        readonly accepted: boolean;
        readonly teamRun: DesktopTeamRun;
      }>();
    }
    this.#teamCoordinator?.requestStop();
    const result = await client.cancelTeamRun(input);
    if (result.ok) {
      emit(
        teamEventFromRun(result.value.teamRun, {
          type: "cancelled",
          conversationId: result.value.teamRun.conversationId,
          state: result.value.teamRun.state,
        }),
      );
    }
    return result;
  }

  async executeTeamRun(
    input: DesktopTeamRunExecuteInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ): Promise<DesktopOperationResult<{ readonly proof: DesktopTeamRunProof }>> {
    const sendClient = this.#readySendClient();
    const vault = this.#options.secretVault;
    if (sendClient === null || vault === undefined) {
      return this.#nativeUnavailable<{ readonly proof: DesktopTeamRunProof }>();
    }
    if (this.#teamCoordinator?.live === true) {
      return Object.freeze({
        ok: false,
        error: Object.freeze({ code: "desktop_team_run_already_active" }),
      });
    }
    const hostClient =
      "startTeamRun" in sendClient &&
      typeof (sendClient as { startTeamRun?: unknown }).startTeamRun === "function"
        ? (sendClient as unknown as DesktopNativeClient)
        : this.#readyNativeClient();
    if (hostClient === null || !("startTeamRun" in hostClient)) {
      return this.#nativeUnavailable<{ readonly proof: DesktopTeamRunProof }>();
    }
    this.#teamInFlight = true;
    const pinEndpoint =
      typeof hostClient.pinProviderEndpoint === "function"
        ? async (baseUrl: string, allowLoopbackHttp: boolean) => {
            const pinned = await hostClient.pinProviderEndpoint({
              baseUrl,
              allowLoopbackHttp,
            });
            if (!pinned.ok) {
              throw Object.assign(new Error(pinned.error.code), { code: pinned.error.code });
            }
            return pinned.value;
          }
        : undefined;
    const coordinator = new PersonalTeamCoordinator({
      host: createNativePersonalTeamHost({ client: hostClient, vault }),
      transport: createOpenAiCompatibleTransport(
        pinEndpoint === undefined ? {} : { pinEndpoint },
      ),
    });
    this.#teamCoordinator = coordinator;
    if (this.#pendingTeamAbort) {
      this.#pendingTeamAbort = false;
      coordinator.requestStop();
    }
    try {
      const proof = await coordinator.execute(input, emit);
      return Object.freeze({ ok: true as const, value: Object.freeze({ proof }) });
    } catch (error) {
      const code =
        typeof error === "object" && error !== null && "code" in error
          ? String((error as { code?: unknown }).code ?? "desktop_team_run_failed")
          : "desktop_team_run_failed";
      return Object.freeze({
        ok: false,
        error: Object.freeze({ code }),
      });
    } finally {
      this.#teamInFlight = false;
      this.#pendingTeamAbort = false;
      if (this.#teamCoordinator === coordinator) this.#teamCoordinator = null;
    }
  }

  async appendTeamRunBudget(
    input: {
      readonly workspaceId: string;
      readonly teamRunId: string;
      readonly budget: DesktopTeamRunExecuteInput["budget"];
    },
    emit: (event: DesktopTeamRunEvent) => void,
  ): Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>> {
    const client = this.#readyNativeClient();
    if (client === null) {
      return this.#nativeUnavailable<{ readonly teamRun: DesktopTeamRun }>();
    }
    const result = await client.appendTeamRunBudget(input);
    if (result.ok) {
      emit(
        teamEventFromRun(result.value.teamRun, {
          type: "snapshot",
          conversationId: result.value.teamRun.conversationId,
          state: result.value.teamRun.state,
          consumedProviderCalls: result.value.teamRun.consumedProviderCalls,
          maximumProviderCalls: result.value.teamRun.maximumProviderCalls,
        }),
      );
    }
    return result;
  }

  getTeamRun(
    input: DesktopTeamRunIdInput,
  ): Promise<DesktopOperationResult<{ readonly teamRun: DesktopTeamRun }>> {
    const client = this.#readyNativeClient();
    return (
      client?.getTeamRun(input) ??
      Promise.resolve(this.#nativeUnavailable<{ readonly teamRun: DesktopTeamRun }>())
    );
  }

  listTeamRuns(
    input: DesktopWorkspaceIdInput,
  ): Promise<DesktopOperationResult<{ readonly items: readonly DesktopTeamRun[] }>> {
    const client = this.#readyNativeClient();
    return (
      client?.listTeamRuns(input) ??
      Promise.resolve(
        this.#nativeUnavailable<{ readonly items: readonly DesktopTeamRun[] }>(),
      )
    );
  }

  async submitTeamProposal(
    input: DesktopTeamRunSubmitProposalInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ): Promise<DesktopOperationResult<DesktopTeamRunProposalResult>> {
    const client = this.#readyNativeClient();
    if (client === null) {
      return this.#nativeUnavailable<DesktopTeamRunProposalResult>();
    }
    const result = await client.submitTeamProposal(input);
    if (result.ok) {
      emit({
        type: "proposal",
        teamRunId: result.value.teamRun.id,
        workspaceId: result.value.teamRun.workspaceId,
        state: result.value.teamRun.state,
      });
    }
    return result;
  }

  getTeamBlackboard(
    input: DesktopTeamRunIdInput,
  ): Promise<DesktopOperationResult<{ readonly blackboard: PersonalTeamBlackboard }>> {
    const client = this.#readyNativeClient();
    return (
      client?.getTeamBlackboard(input) ??
      Promise.resolve(
        this.#nativeUnavailable<{ readonly blackboard: PersonalTeamBlackboard }>(),
      )
    );
  }

  async recordTeamCollaboration(
    input: DesktopTeamCollaborationInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ): Promise<
    DesktopOperationResult<{
      readonly collaborationRequest: DesktopTeamCollaborationRequest;
    }>
  > {
    const client = this.#readyNativeClient();
    if (client === null) {
      return this.#nativeUnavailable<{
        readonly collaborationRequest: DesktopTeamCollaborationRequest;
      }>();
    }
    const result = await client.recordTeamCollaboration(input);
    if (result.ok) {
      emit({
        type: "blackboard",
        teamRunId: input.teamRunId,
        workspaceId: input.workspaceId,
      });
    }
    return result;
  }

  start(): Promise<RuntimeStatus> {
    if (this.#supervisor?.getStatus().phase === "ready") {
      return Promise.resolve(this.#supervisor.getStatus());
    }
    if (
      this.#startOperation !== null &&
      this.#startOperation.generation === this.#generation
    ) {
      return this.#startOperation.promise;
    }
    const generation = ++this.#generation;
    const promise = this.#start(generation).finally(() => {
      if (this.#startOperation?.generation === generation) {
        this.#startOperation = null;
      }
    });
    this.#startOperation = { generation, promise };
    return promise;
  }

  async #start(generation: number): Promise<RuntimeStatus> {
    this.#nativeClient = null;
    this.#status = Object.freeze({
      phase: "starting",
      attempts: 0,
      lastError: null,
    });
    try {
      const bundle = await verifyRuntimeBundle({
        bundleRoot: this.#options.runtimeRoot,
        manifestPath: path.join(
          this.#options.runtimeRoot,
          "runtime-manifest.json",
        ),
        expectedManifestSha256: this.#options.expectedManifestSha256,
      });
      if (generation !== this.#generation) return this.#status;
      await prepareRuntimeDataRoot(this.#options.dataRoot);
      if (generation !== this.#generation) return this.#status;
      const nativeProofKey = randomBytes(32).toString("hex");
      const nativeControlToken = randomBytes(32).toString("hex");
      const supervisor = new RuntimeSupervisor({
        command: bundle.command,
        args: bundle.args,
        cwd: bundle.root,
        environment: buildRuntimeEnvironment(
          nativeProofKey,
          nativeControlToken,
          this.#options.dataRoot,
          this.#options.hostEnvironment,
        ),
        readinessProbe: async () => {
          const challenge = randomBytes(32).toString("hex");
          const response = await fetch(`${this.#options.uiOrigin}/health`, {
            method: "GET",
            headers: {
              "x-omnibase-desktop-challenge": challenge,
            },
            cache: "no-store",
            redirect: "error",
            signal: AbortSignal.timeout(1_000),
          });
          return (
            response.ok &&
            matchesRuntimeInstanceProof(
              response.headers.get("x-omnibase-desktop-proof"),
              challenge,
              nativeProofKey,
            )
          );
        },
        startupTimeoutMs: bundle.startupTimeoutMs,
      });
      if (generation !== this.#generation) {
        supervisor.stop();
        return this.#status;
      }
      this.#supervisor = supervisor;
      const status = await supervisor.start();
      if (generation !== this.#generation) {
        supervisor.stop();
        return this.#status;
      }
      this.#status = status;
      if (status.phase === "ready") {
        this.#nativeClient = new DesktopNativeClient({
          backendOrigin: `http://127.0.0.1:${bundle.backendPort}`,
          nativeControlToken,
        });
      }
    } catch (error) {
      if (generation !== this.#generation) return this.#status;
      this.#nativeClient = null;
      this.#status = Object.freeze({
        phase: "failed",
        attempts: 0,
        lastError: redactRuntimeError(error, [this.#options.runtimeRoot]),
      });
    }
    return this.#status;
  }

  stop(): RuntimeStatus {
    this.#generation += 1;
    this.#nativeClient = null;
    this.#status =
      this.#supervisor?.stop() ??
      Object.freeze({
        phase: "stopped",
        attempts: this.#status.attempts,
        lastError: null,
      });
    this.#supervisor = null;
    return this.#status;
  }

  #nativeUnavailable<T>(): DesktopOperationResult<T> {
    return Object.freeze({
      ok: false,
      error: Object.freeze({ code: "desktop_runtime_not_ready" }),
    });
  }

  #readySendClient(): RuntimeManagerNativeSendClient | null {
    return this.#options.nativeClientForTests ?? this.#readyNativeClient();
  }

  #readyNativeClient(): DesktopNativeClient | null {
    if (this.#supervisor?.getStatus().phase !== "ready") {
      this.#nativeClient = null;
      return null;
    }
    return this.#nativeClient;
  }

  #armStreamAbort(): AbortController {
    this.#streamAbort?.abort();
    this.#sendInFlight = true;
    const controller = new AbortController();
    if (this.#pendingAbort) {
      this.#pendingAbort = false;
      controller.abort();
    }
    this.#streamAbort = controller;
    return controller;
  }

  #releaseStreamAbort(controller: AbortController): void {
    if (this.#streamAbort === controller) this.#streamAbort = null;
    this.#sendInFlight = false;
    this.#pendingAbort = false;
  }

  #requestStreamAbort(): boolean {
    const controller = this.#streamAbort;
    if (controller !== null) {
      controller.abort();
      return true;
    }
    if (this.#sendInFlight) {
      this.#pendingAbort = true;
      return true;
    }
    return false;
  }

  #cancelledSendResult(
    input: DesktopConversationSendInput,
  ): DesktopOperationResult<DesktopConversationEvent> {
    return Object.freeze({
      ok: true as const,
      value: Object.freeze({
        type: "cancelled" as const,
        invocationId: "invocation_cancelled_locally",
        workspaceId: input.workspaceId,
        conversationId: input.conversationId,
        errorRedacted: "生成已停止",
        ...(input.sendEpoch === undefined ? {} : { sendEpoch: input.sendEpoch }),
      }),
    });
  }
}
