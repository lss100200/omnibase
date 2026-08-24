import { createHash, randomBytes } from "node:crypto";

import {
  SPECIALIST_EMPLOYEE_IDS,
  teamEventIdentityComplete,
  type DesktopTeamPlanRevision,
  type DesktopTeamRun,
  type DesktopTeamRunEvent,
  type DesktopTeamRunExecuteInput,
  type DesktopTeamRunProof,
  type DesktopTeamRunProposalResult,
  type EmployeeTeamReport,
  type ParentReplanDecision,
  type ParentTeamDecision,
  type PersonalEmployeeId,
  type PersonalTeamBlackboard,
  type SpecialistEmployeeId,
  type TeamAssignmentProposal,
  type TeamRunBudget,
  type TeamRunState,
  type TeamWaveExecution,
  type TeamWaveProposal,
} from "../shared/personal-team.ts";
import {
  assertRequestedModelIdentity,
  type TeamChatMessage,
  type TeamChatResult,
  type TeamChatTransport,
} from "./personal-team-provider.ts";
import {
  extractJsonObject,
  validateParentReplanDecision,
  validateParentTeamDecision,
  validateTeamRunBudget,
} from "./personal-team-validate.ts";

export const ROLE_DUTY: Readonly<Record<PersonalEmployeeId, string>> = Object.freeze({
  parent: "项目负责人：判断编制、校验后的提案、wave 之间 replan、最终汇总。",
  product: "产品目标与范围",
  ux: "交互与视觉",
  frontend: "桌面与前端实现",
  backend: "SQLite、IPC 与数据模型",
  data: "数据与检索",
  security: "身份、取消与权限边界",
  qa: "攻击矩阵与回归",
  operations: "发布与运行稳定性",
  docs: "产品与维护者文档",
});

const SEND_ABORTED = Object.freeze({ aborted: true as const });

export interface TeamRoleCredentials {
  readonly providerId: string;
  readonly model: string;
  readonly baseUrl: string;
  readonly secret: string;
  readonly allowLoopbackHttp: boolean;
  readonly timeoutMs: number;
}

export type TeamProviderCallPurpose =
  | "parent-propose"
  | "parent-replan"
  | "parent-synthesize"
  | "employee";

export type TeamParentCallPurpose = Exclude<TeamProviderCallPurpose, "employee">;
export type TeamParentCallTerminalState =
  | "succeeded"
  | "failed"
  | "cancelled"
  | "unknown";

export interface TeamParentCallRecord {
  readonly invocationId: string;
  readonly teamRunId: string;
  readonly planRevisionId: string | null;
  readonly purpose: TeamParentCallPurpose;
  readonly state: "pending" | TeamParentCallTerminalState;
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

export interface PersonalTeamHost {
  startTeamRun(
    input: DesktopTeamRunExecuteInput,
  ): Promise<{ readonly teamRun: DesktopTeamRun }>;
  submitProposal(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly proposal: ParentTeamDecision | ParentReplanDecision;
  }): Promise<DesktopTeamRunProposalResult>;
  getBlackboard(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
  }): Promise<{ readonly blackboard: PersonalTeamBlackboard }>;
  consumeProviderCall(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly invocationId: string;
    readonly purpose: TeamProviderCallPurpose;
    readonly providerId: string;
    readonly requestedModel: string;
  }): Promise<{
    readonly teamRun: DesktopTeamRun;
    readonly parentCall?: TeamParentCallRecord;
  }>;
  settleParentCall(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly invocationId: string;
    readonly purpose: TeamParentCallPurpose;
    readonly providerId: string;
    readonly requestedModel: string;
    readonly state: TeamParentCallTerminalState;
    readonly planRevisionId: string | null;
    readonly actualModel: string | null;
    readonly inputTokens: number | null;
    readonly outputTokens: number | null;
    readonly totalTokens: number | null;
    readonly outputSha256: string | null;
    readonly errorCode: string | null;
  }): Promise<{ readonly parentCall: TeamParentCallRecord }>;
  setRunState(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly state: TeamRunState;
    readonly parentFinalAnswer?: string;
  }): Promise<{ readonly teamRun: DesktopTeamRun }>;
  createNode(input: {
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
  }): Promise<{
    readonly node: {
      readonly id: string;
      readonly ordinal: number;
      readonly invocationId: string;
    };
  }>;
  updateNode(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly nodeId: string;
    readonly state: "failed" | "cancelled" | "unknown";
    readonly actualModel: string | null;
    readonly inputTokens: number | null;
    readonly outputTokens: number | null;
    readonly totalTokens: number | null;
    readonly answerSha256: string | null;
    readonly errorCode: string | null;
    readonly durationMs: number | null;
  }): Promise<void>;
  settleNode(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly nodeId: string;
    readonly invocationId: string;
    readonly state: "succeeded" | "failed" | "cancelled" | "unknown";
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
  }): Promise<void>;
  recordReport(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly nodeId: string;
    readonly invocationId: string;
    readonly report: EmployeeTeamReport;
  }): Promise<void>;
  resolveCollaboration(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly requestId: string;
    readonly parentDecision: "accept_start" | "handle_self" | "merge_existing" | "decline";
    readonly resolvedAssignmentId: string | null;
  }): Promise<void>;
  resolveCredentials(
    workspaceId: string,
    roleId: PersonalEmployeeId,
    signal: AbortSignal,
  ): Promise<TeamRoleCredentials>;
}

export interface PersonalTeamCoordinatorOptions {
  readonly host: PersonalTeamHost;
  readonly transport: TeamChatTransport;
  readonly now?: () => number;
  readonly newId?: (prefix: string) => string;
}

export interface TeamProviderCallRecord {
  readonly purpose: TeamProviderCallPurpose;
  readonly roleId: PersonalEmployeeId;
  readonly invocationId: string;
  readonly nodeId: string | null;
  readonly assignmentId: string | null;
}

interface ParentProviderCallSuccess {
  readonly kind: "ok";
  readonly text: string;
  readonly result: TeamChatResult;
  readonly invocationId: string;
  readonly purpose: TeamParentCallPurpose;
  readonly providerId: string;
  readonly requestedModel: string;
}

const ABORT_CODES = new Set(["desktop_invocation_cancelled"]);
const CONTROL_CHARACTER_PATTERN = /[\u0000-\u001f\u007f]/u;

function isAborted(value: unknown): value is typeof SEND_ABORTED {
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

function sha256Text(value: string): string {
  return createHash("sha256").update(value, "utf8").digest("hex");
}

function normalizeParentFinalText(value: string, maximum: number): string | null {
  if (CONTROL_CHARACTER_PATTERN.test(value)) return null;
  const normalized = value.trim();
  return normalized.length > 0 && normalized.length <= maximum ? normalized : null;
}

function defaultNewId(prefix: string): string {
  return `${prefix}_${randomBytes(16).toString("hex")}`;
}

function errorCode(error: unknown): string {
  if (typeof error === "object" && error !== null && "code" in error) {
    const code = (error as { code?: unknown }).code;
    if (typeof code === "string") return code;
  }
  return "desktop_invocation_failed";
}

function normalizedUsage(result: TeamChatResult | undefined): {
  readonly inputTokens: number | null;
  readonly outputTokens: number | null;
  readonly totalTokens: number | null;
} {
  if (
    result !== undefined &&
    typeof result.inputTokens === "number" &&
    Number.isInteger(result.inputTokens) &&
    result.inputTokens >= 0 &&
    typeof result.outputTokens === "number" &&
    Number.isInteger(result.outputTokens) &&
    result.outputTokens >= 0 &&
    typeof result.totalTokens === "number" &&
    Number.isInteger(result.totalTokens) &&
    result.totalTokens === result.inputTokens + result.outputTokens
  ) {
    return {
      inputTokens: result.inputTokens,
      outputTokens: result.outputTokens,
      totalTokens: result.totalTokens,
    };
  }
  return { inputTokens: null, outputTokens: null, totalTokens: null };
}

export class TeamAbortRegistry {
  #controllers = new Map<string, AbortController>();
  #pending = false;

  reset(): void {
    this.#controllers.clear();
    this.#pending = false;
  }

  arm(key: string): AbortController {
    this.#controllers.get(key)?.abort();
    const controller = new AbortController();
    if (this.#pending) {
      this.#pending = false;
      controller.abort();
    }
    this.#controllers.set(key, controller);
    return controller;
  }

  release(key: string, controller: AbortController): void {
    if (this.#controllers.get(key) === controller) this.#controllers.delete(key);
  }

  abortAll(): boolean {
    this.#pending = true;
    for (const controller of this.#controllers.values()) controller.abort();
    return true;
  }

  get pending(): boolean {
    return this.#pending;
  }

  get size(): number {
    return this.#controllers.size;
  }
}

interface StoredAssignment {
  readonly assignment: TeamAssignmentProposal;
  readonly waveId: string;
  readonly declaredExecution: TeamWaveExecution;
  effectiveExecution: TeamWaveExecution;
  state: "pending" | "running" | "completed" | "failed" | "cancelled" | "blocked" | "needs_collaboration";
}

interface StoredNode {
  readonly nodeId: string;
  readonly assignmentId: string;
  readonly invocationId: string;
  readonly employeeRoleId: SpecialistEmployeeId;
  readonly waveId: string;
  readonly ordinal: number;
  readonly nodeEpoch: number;
  readonly sendEpoch: number;
  state: string;
  durationMs: number | null;
  inputTokens: number | null;
  outputTokens: number | null;
  totalTokens: number | null;
  report: EmployeeTeamReport | null;
}

export { teamEventIdentityComplete };

export function eventMatchesTeamIdentity(
  current: {
    readonly workspaceId: string;
    readonly conversationId: string;
    readonly teamRunId: string;
    readonly rosterEpoch: number;
    readonly planRevisionId?: string | null;
    readonly waveId?: string | null;
    readonly assignmentId?: string | null;
    readonly nodeId?: string | null;
    readonly sendEpoch?: number | null;
    readonly invocationId?: string | null;
    readonly nodeEpoch?: number | null;
    readonly employeeRoleId?: string | null;
  },
  event: DesktopTeamRunEvent,
): boolean {
  if (!teamEventIdentityComplete(event)) return false;
  if (event.workspaceId !== current.workspaceId) return false;
  if (event.conversationId !== current.conversationId) return false;
  if (event.teamRunId !== current.teamRunId) return false;
  if (event.rosterEpoch !== current.rosterEpoch) return false;
  if (event.type === "plan_transition") {
    return (
      typeof event.oldPlanRevisionId === "string" &&
      event.oldPlanRevisionId === (current.planRevisionId ?? "") &&
      typeof event.planRevisionId === "string" &&
      event.planRevisionId !== event.oldPlanRevisionId
    );
  }
  if (
    current.planRevisionId !== undefined &&
    current.planRevisionId !== null &&
    current.planRevisionId !== "" &&
    event.planRevisionId !== current.planRevisionId
  ) {
    return false;
  }
  if (current.waveId && event.waveId !== current.waveId) return false;
  if (current.assignmentId && event.assignmentId !== current.assignmentId) return false;
  if (current.nodeId && event.nodeId !== current.nodeId) return false;
  if (
    current.sendEpoch !== undefined &&
    current.sendEpoch !== null &&
    event.sendEpoch !== current.sendEpoch
  ) {
    return false;
  }
  if (
    current.invocationId &&
    event.invocationId !== undefined &&
    event.invocationId !== current.invocationId
  ) {
    return false;
  }
  if (
    event.type === "node_terminal" &&
    current.nodeEpoch !== undefined &&
    current.nodeEpoch !== null &&
    event.nodeEpoch !== current.nodeEpoch
  ) {
    return false;
  }
  if (
    event.type === "node_terminal" &&
    current.employeeRoleId &&
    event.employeeRoleId !== current.employeeRoleId
  ) {
    return false;
  }
  return true;
}

export class PersonalTeamCoordinator {
  readonly #host: PersonalTeamHost;
  readonly #transport: TeamChatTransport;
  readonly #now: () => number;
  readonly #newId: (prefix: string) => string;
  readonly abort = new TeamAbortRegistry();
  #cancelled = false;
  #executionStarted = false;
  #live = false;
  #successCommitStarted = false;
  #successCommit: Promise<boolean> | null = null;
  #quietCommitStarted = false;
  #quietCommit: Promise<boolean> | null = null;
  #wall = new AbortController();
  #wallTimer: ReturnType<typeof setTimeout> | null = null;
  #wallDeadlineMs = 0;
  #startedAt = 0;
  #nextNodeEpoch = 0;
  #nextSendEpoch = 0;

  constructor(options: PersonalTeamCoordinatorOptions) {
    this.#host = options.host;
    this.#transport = options.transport;
    this.#now = options.now ?? Date.now;
    this.#newId = options.newId ?? defaultNewId;
  }

  get live(): boolean {
    return this.#live;
  }

  requestStop(): boolean {
    if (
      this.#successCommitStarted ||
      this.#quietCommitStarted ||
      (this.#executionStarted && !this.#live)
    ) {
      return false;
    }
    this.#cancelled = true;
    this.abort.abortAll();
    return true;
  }

  get successCommitStarted(): boolean {
    return this.#successCommitStarted;
  }

  async waitForSuccessCommit(): Promise<boolean> {
    return (await this.#successCommit) ?? false;
  }

  get quietCommitStarted(): boolean {
    return this.#quietCommitStarted;
  }

  async waitForQuietCommit(): Promise<boolean> {
    return (await this.#quietCommit) ?? false;
  }

  /**
   * The backend success closure refuses `succeeded` while any collaboration
   * request is still pending. Pending requests are decided per request by
   * the parent at replan time; anything still pending here means the run
   * must fail closed instead of committing success over an undecided
   * collaboration.
   */
  async #requireNoPendingCollaborations(
    input: DesktopTeamRunExecuteInput,
    teamRunId: string,
  ): Promise<void> {
    const { blackboard } = await this.#host.getBlackboard({
      workspaceId: input.workspaceId,
      teamRunId,
    });
    if (
      blackboard.collaborationRequests.some(
        (request) => request.parentDecision === "pending",
      )
    ) {
      throw Object.assign(new Error("desktop_team_collaboration_pending"), {
        code: "desktop_team_collaboration_pending",
      });
    }
  }

  async #parentProposalBoundaryTerminal(
    call: ParentProviderCallSuccess,
    input: DesktopTeamRunExecuteInput,
    teamRunId: string,
    calls: readonly TeamProviderCallRecord[],
    nodes: readonly StoredNode[],
    parentFinal: string | null,
    emit: (
      event: Omit<
        DesktopTeamRunEvent,
        "teamRunId" | "workspaceId" | "rosterEpoch" | "conversationId"
      >,
    ) => void,
  ): Promise<DesktopTeamRunProof | null> {
    if (this.#cancelled) {
      return this.#cancelledProof(input, teamRunId, calls, nodes, parentFinal, emit);
    }
    if (!this.#wallExceeded()) return null;
    try {
      await this.#settleParentProviderCall(call, input, teamRunId, {
        state: "failed",
        planRevisionId: null,
        outputSha256: null,
        errorCode: "desktop_team_wall_time_exceeded",
      });
    } catch (error) {
      if (this.#cancelled) {
        return this.#cancelledProof(input, teamRunId, calls, nodes, parentFinal, emit);
      }
      throw error;
    }
    if (this.#cancelled) {
      return this.#cancelledProof(input, teamRunId, calls, nodes, parentFinal, emit);
    }
    return this.#budgetProof(input, teamRunId, calls, nodes, parentFinal, emit);
  }

  async #markNodeCancelled(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly nodeId: string;
    readonly errorCode: string;
    readonly durationMs: number | null;
  }): Promise<void> {
    try {
      await this.#host.updateNode({
        workspaceId: input.workspaceId,
        teamRunId: input.teamRunId,
        nodeId: input.nodeId,
        state: "cancelled",
        actualModel: null,
        inputTokens: null,
        outputTokens: null,
        totalTokens: null,
        answerSha256: null,
        errorCode: input.errorCode,
        durationMs: input.durationMs,
      });
    } catch (error) {
      const code = errorCode(error);
      if (
        code === "desktop_team_run_terminal" ||
        code === "desktop_team_node_terminal" ||
        code === "desktop_team_node_not_running"
      ) {
        return;
      }
      throw error;
    }
  }

  #armWall(ms: number): void {
    this.#clearWall();
    this.#wall = new AbortController();
    this.#wallDeadlineMs = ms;
    this.#startedAt = this.#now();
    this.#wallTimer = setTimeout(() => {
      this.#wall.abort();
    }, ms);
  }

  #clearWall(): void {
    if (this.#wallTimer !== null) {
      clearTimeout(this.#wallTimer);
      this.#wallTimer = null;
    }
  }

  #wallExceeded(): boolean {
    return this.#wall.signal.aborted || this.#now() - this.#startedAt > this.#wallDeadlineMs;
  }

  #abortKind(): "cancelled" | "budget" {
    if (this.#wallExceeded() && !this.#cancelled) return "budget";
    return "cancelled";
  }

  #requestSignal(node: AbortSignal): AbortSignal {
    if (typeof AbortSignal.any === "function") {
      return AbortSignal.any([node, this.#wall.signal]);
    }
    const combined = new AbortController();
    const abort = () => combined.abort();
    if (node.aborted || this.#wall.signal.aborted) {
      combined.abort();
      return combined.signal;
    }
    node.addEventListener("abort", abort, { once: true });
    this.#wall.signal.addEventListener("abort", abort, { once: true });
    return combined.signal;
  }

  async execute(
    input: DesktopTeamRunExecuteInput,
    emit: (event: DesktopTeamRunEvent) => void,
  ): Promise<DesktopTeamRunProof> {
    if (this.#live) {
      throw Object.assign(new Error("desktop_team_run_already_active"), {
        code: "desktop_team_run_already_active",
      });
    }
    if (this.#executionStarted) {
      throw Object.assign(new Error("desktop_team_coordinator_already_executed"), {
        code: "desktop_team_coordinator_already_executed",
      });
    }
    if (
      input.allowedSpecialistRoleIds !== undefined &&
      input.allowedSpecialistRoleIds.length === 0
    ) {
      throw Object.assign(new Error("desktop_team_allow_list_empty"), {
        code: "desktop_team_allow_list_empty",
      });
    }
    const budgetCheck = validateTeamRunBudget(input.budget);
    if (!budgetCheck.ok) {
      throw Object.assign(new Error(budgetCheck.code), { code: budgetCheck.code });
    }
    this.#executionStarted = true;
    this.#live = true;
    if (!this.#cancelled) this.abort.reset();
    this.#armWall(input.budget.maximumWallTimeMs);
    this.#nextNodeEpoch = 0;
    this.#nextSendEpoch = 0;
    const started = this.#startedAt;
    const calls: TeamProviderCallRecord[] = [];
    const nodes: StoredNode[] = [];
    const assignments = new Map<string, StoredAssignment>();
    const reports: EmployeeTeamReport[] = [];
    let parentFinal: string | null = null;
    let synthesizing = false;
    let teamRun: DesktopTeamRun | null = null;
    try {
      const startedRun = await this.#host.startTeamRun(input);
      teamRun = startedRun.teamRun;
      if (
        teamRun.workspaceId !== input.workspaceId ||
        teamRun.conversationId !== input.conversationId
      ) {
        throw Object.assign(new Error("desktop_team_conversation_identity_mismatch"), {
          code: "desktop_team_conversation_identity_mismatch",
        });
      }
      const identity = {
        workspaceId: input.workspaceId,
        conversationId: input.conversationId,
        teamRunId: teamRun.id,
        rosterEpoch: input.rosterEpoch,
        planRevisionId: "",
        waveId: "",
        assignmentId: "",
        nodeId: "",
        sendEpoch: 0,
      };
      const emitBound = (
        event: Omit<
          DesktopTeamRunEvent,
          "teamRunId" | "workspaceId" | "rosterEpoch" | "conversationId"
        >,
      ) => {
        emit({
          planRevisionId: identity.planRevisionId,
          waveId: identity.waveId,
          assignmentId: identity.assignmentId,
          nodeId: identity.nodeId,
          sendEpoch: identity.sendEpoch,
          ...event,
          teamRunId: identity.teamRunId,
          workspaceId: identity.workspaceId,
          conversationId: identity.conversationId,
          rosterEpoch: identity.rosterEpoch,
        });
      };
      emitBound({ type: "snapshot", state: teamRun.state, maximumProviderCalls: input.budget.maximumProviderCalls, consumedProviderCalls: 0 });
      if (this.#cancelled) return this.#cancelledProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);

      await this.#host.setRunState({
        workspaceId: input.workspaceId,
        teamRunId: teamRun.id,
        state: "running",
      });

      emitBound({ type: "parent_proposing", employeeRoleId: "parent", state: "running" });
      const parentFirst = await this.#invokeParent({
        input,
        teamRun,
        purpose: "parent-propose",
        messages: this.#parentProposeMessages(input),
        emit: emitBound,
        calls,
        started,
      });
      if (parentFirst.kind !== "ok") {
        return this.#terminalFromInvoke(parentFirst, teamRun.id, calls, nodes, parentFinal, input, synthesizing, emitBound);
      }

      emitBound({ type: "host_validating", employeeRoleId: "parent" });
      const validationTerminal = await this.#parentProposalBoundaryTerminal(
        parentFirst,
        input,
        teamRun.id,
        calls,
        nodes,
        parentFinal,
        emitBound,
      );
      if (validationTerminal !== null) return validationTerminal;
      const parsed = extractJsonObject(parentFirst.text);
      const allowed = new Set(
        input.allowedSpecialistRoleIds ?? SPECIALIST_EMPLOYEE_IDS,
      );
      const validated = validateParentTeamDecision(
        parsed,
        input.budget,
        allowed,
        input.workspaceId,
      );
      let submitted: DesktopTeamRunProposalResult;
      try {
        submitted = await this.#host.submitProposal({
          workspaceId: input.workspaceId,
          teamRunId: teamRun.id,
          proposal: (validated.ok ? validated.value : parsed) as ParentTeamDecision,
        });
      } catch (error) {
        const boundaryTerminal = await this.#parentProposalBoundaryTerminal(
          parentFirst,
          input,
          teamRun.id,
          calls,
          nodes,
          parentFinal,
          emitBound,
        );
        if (boundaryTerminal !== null) return boundaryTerminal;
        try {
          await this.#settleParentProviderCall(parentFirst, input, teamRun.id, {
            state: "unknown",
            planRevisionId: null,
            outputSha256: null,
            errorCode: "desktop_team_parent_proposal_submit_unknown",
          });
        } catch (settleError) {
          if (this.#cancelled) {
            return this.#cancelledProof(
              input,
              teamRun.id,
              calls,
              nodes,
              parentFinal,
              emitBound,
            );
          }
          throw settleError;
        }
        return this.#terminalFromInvoke(
          {
            kind: "unknown",
            code: "desktop_team_parent_proposal_submit_unknown",
          },
          teamRun.id,
          calls,
          nodes,
          parentFinal,
          input,
          synthesizing,
          emitBound,
        );
      }
      identity.planRevisionId = submitted.planRevision.id;
      emitBound({
        type: "proposal",
        planRevisionId: submitted.planRevision.id,
        state: submitted.teamRun.state,
      });
      const proposalTerminal = await this.#parentProposalBoundaryTerminal(
        parentFirst,
        input,
        teamRun.id,
        calls,
        nodes,
        parentFinal,
        emitBound,
      );
      if (proposalTerminal !== null) return proposalTerminal;
      if (!submitted.accepted || !validated.ok) {
        await this.#settleParentProviderCall(parentFirst, input, teamRun.id, {
          state: "failed",
          planRevisionId: null,
          outputSha256: null,
          errorCode:
            submitted.validationErrorCode ??
            (validated.ok ? "desktop_team_proposal_invalid" : validated.code),
        });
        const quietCommit = this.#startQuietCommit({
          workspaceId: input.workspaceId,
          teamRunId: teamRun.id,
          state: "failed",
        });
        if (quietCommit === null) {
          return this.#cancelledProof(
            input,
            teamRun.id,
            calls,
            nodes,
            parentFinal,
            emitBound,
          );
        }
        await quietCommit;
        emitBound({
          type: "failed",
          state: "failed",
          errorCode: submitted.validationErrorCode ?? "desktop_team_proposal_invalid",
        });
        return this.#proof(teamRun.id, "failed", calls, nodes, parentFinal, false);
      }

      try {
        await this.#settleParentProviderCall(parentFirst, input, teamRun.id, {
          state: "succeeded",
          planRevisionId: submitted.planRevision.id,
          outputSha256: submitted.planRevision.proposalJsonSha256,
          errorCode: null,
        });
      } catch (error) {
        if (this.#cancelled) {
          return this.#cancelledProof(
            input,
            teamRun.id,
            calls,
            nodes,
            parentFinal,
            emitBound,
          );
        }
        throw error;
      }
      if (this.#cancelled) {
        return this.#cancelledProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
      }
      if (this.#wallExceeded()) {
        return this.#budgetProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
      }

      const decision = validated.value;
      if (decision.decision === "answer_directly") {
        parentFinal = decision.answer;
        await this.#requireNoPendingCollaborations(input, teamRun.id);
        if (this.#cancelled) {
          return this.#cancelledProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
        }
        if (this.#wallExceeded()) {
          return this.#budgetProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
        }
        const successCommit = this.#startSuccessCommit({
          workspaceId: input.workspaceId,
          teamRunId: teamRun.id,
          state: "succeeded",
          parentFinalAnswer: parentFinal,
        });
        if (successCommit === null) {
          return this.#cancelledProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
        }
        await successCommit;
        emitBound({
          type: "completed",
          state: "succeeded",
          parentFinalAnswer: parentFinal,
          employeeRoleId: "parent",
        });
        return this.#proof(teamRun.id, "succeeded", calls, nodes, parentFinal, false);
      }

      this.#rememberWaves(assignments, decision.waves);
      let pendingWaves: TeamWaveProposal[] = [...decision.waves];
      let revisionOrdinal = submitted.planRevision.revisionOrdinal;
      let lastPlan = submitted.planRevision;

      while (pendingWaves.length > 0) {
        if (this.#cancelled) {
          return this.#cancelledProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
        }
        if (this.#wallExceeded()) {
          return this.#budgetProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
        }
        const wave = pendingWaves.shift();
        if (wave === undefined) break;
        identity.waveId = wave.waveId;
        identity.assignmentId = "";
        identity.nodeId = "";
        const completedIds = new Set(
          [...assignments.values()]
            .filter((item) => item.state === "completed" || item.state === "needs_collaboration")
            .map((item) => item.assignment.assignmentId),
        );
        const waveResult = await this.#executeWave({
          input,
          teamRun,
          wave,
          assignments,
          completedIds,
          nodes,
          reports,
          calls,
          emit: emitBound,
          started,
        });
        if (waveResult.kind !== "ok") {
          return this.#terminalFromInvoke(waveResult, teamRun.id, calls, nodes, parentFinal, input, synthesizing, emitBound);
        }

        emitBound({ type: "blackboard" });
        if (this.#cancelled) return this.#cancelledProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
        if (pendingWaves.length > 0) continue;

        emitBound({ type: "parent_replanning", employeeRoleId: "parent" });
        const pendingBoard = await this.#host.getBlackboard({
          workspaceId: input.workspaceId,
          teamRunId: teamRun.id,
        });
        const pendingCollaboration = pendingBoard.blackboard.collaborationRequests.filter(
          (request): request is typeof request & { id: string } =>
            request.parentDecision === "pending" && typeof request.id === "string",
        );
        const replanCall = await this.#invokeParent({
          input,
          teamRun,
          purpose: "parent-replan",
          messages: this.#parentReplanMessages(input, reports, assignments, pendingCollaboration),
          emit: emitBound,
          calls,
          started,
        });
        if (replanCall.kind !== "ok") {
          return this.#terminalFromInvoke(replanCall, teamRun.id, calls, nodes, parentFinal, input, synthesizing, emitBound);
        }
        revisionOrdinal += 1;
        const replanParsed = extractJsonObject(replanCall.text);
        const replanValidated = validateParentReplanDecision(
          replanParsed,
          input.budget,
          allowed,
          input.workspaceId,
          new Set(assignments.keys()),
          revisionOrdinal,
          pendingCollaboration.map((request) => ({
            id: request.id,
            targetRoleId: request.targetRoleId,
          })),
        );
        let replanSubmitted: DesktopTeamRunProposalResult;
        try {
          replanSubmitted = await this.#host.submitProposal({
            workspaceId: input.workspaceId,
            teamRunId: teamRun.id,
            proposal: (replanValidated.ok ? replanValidated.value : replanParsed) as ParentReplanDecision,
          });
        } catch (error) {
          const boundaryTerminal = await this.#parentProposalBoundaryTerminal(
            replanCall,
            input,
            teamRun.id,
            calls,
            nodes,
            parentFinal,
            emitBound,
          );
          if (boundaryTerminal !== null) return boundaryTerminal;
          try {
            await this.#settleParentProviderCall(replanCall, input, teamRun.id, {
              state: "unknown",
              planRevisionId: null,
              outputSha256: null,
              errorCode: "desktop_team_parent_proposal_submit_unknown",
            });
          } catch (settleError) {
            if (this.#cancelled) {
              return this.#cancelledProof(
                input,
                teamRun.id,
                calls,
                nodes,
                parentFinal,
                emitBound,
              );
            }
            throw settleError;
          }
          return this.#terminalFromInvoke(
            {
              kind: "unknown",
              code: "desktop_team_parent_proposal_submit_unknown",
            },
            teamRun.id,
            calls,
            nodes,
            parentFinal,
            input,
            synthesizing,
            emitBound,
          );
        }
        lastPlan = replanSubmitted.planRevision;
        emitBound({
          type: "plan_transition",
          oldPlanRevisionId: identity.planRevisionId,
          planRevisionId: lastPlan.id,
        });
        identity.planRevisionId = lastPlan.id;
        const transitionTerminal = await this.#parentProposalBoundaryTerminal(
          replanCall,
          input,
          teamRun.id,
          calls,
          nodes,
          parentFinal,
          emitBound,
        );
        if (transitionTerminal !== null) return transitionTerminal;
        emitBound({
          type: "proposal",
          planRevisionId: lastPlan.id,
          state: replanSubmitted.teamRun.state,
        });
        const replanTerminal = await this.#parentProposalBoundaryTerminal(
          replanCall,
          input,
          teamRun.id,
          calls,
          nodes,
          parentFinal,
          emitBound,
        );
        if (replanTerminal !== null) return replanTerminal;
        if (!replanSubmitted.accepted || !replanValidated.ok) {
          await this.#settleParentProviderCall(replanCall, input, teamRun.id, {
            state: "failed",
            planRevisionId: null,
            outputSha256: null,
            errorCode:
              replanSubmitted.validationErrorCode ??
              (replanValidated.ok
                ? "desktop_team_proposal_invalid"
                : replanValidated.code),
          });
          const quietCommit = this.#startQuietCommit({
            workspaceId: input.workspaceId,
            teamRunId: teamRun.id,
            state: "failed",
          });
          if (quietCommit === null) {
            return this.#cancelledProof(
              input,
              teamRun.id,
              calls,
              nodes,
              parentFinal,
              emitBound,
            );
          }
          await quietCommit;
          emitBound({
            type: "failed",
            state: "failed",
            errorCode: replanSubmitted.validationErrorCode ?? "desktop_team_proposal_invalid",
          });
          return this.#proof(teamRun.id, "failed", calls, nodes, parentFinal, false);
        }
        try {
          await this.#settleParentProviderCall(replanCall, input, teamRun.id, {
            state: "succeeded",
            planRevisionId: replanSubmitted.planRevision.id,
            outputSha256: replanSubmitted.planRevision.proposalJsonSha256,
            errorCode: null,
          });
        } catch (error) {
          if (this.#cancelled) {
            return this.#cancelledProof(
              input,
              teamRun.id,
              calls,
              nodes,
              parentFinal,
              emitBound,
            );
          }
          throw error;
        }
        if (this.#cancelled) {
          return this.#cancelledProof(
            input,
            teamRun.id,
            calls,
            nodes,
            parentFinal,
            emitBound,
          );
        }
        if (this.#wallExceeded()) {
          return this.#budgetProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
        }
        const replan = replanValidated.value;
        for (const decision of replan.collaborationDecisions ?? []) {
          await this.#host.resolveCollaboration({
            workspaceId: input.workspaceId,
            teamRunId: teamRun.id,
            requestId: decision.requestId,
            parentDecision: decision.decision,
            resolvedAssignmentId: decision.resolvedAssignmentId ?? null,
          });
        }
        if (replan.decision === "continue") {
          this.#rememberWaves(assignments, [replan.nextWave]);
          pendingWaves = [replan.nextWave];
          continue;
        }
        if (replan.decision === "request_followup") {
          const follow: TeamWaveProposal = Object.freeze({
            waveId: `followup-${revisionOrdinal}`,
            execution: "serial",
            assignments: replan.assignments,
          });
          this.#rememberWaves(assignments, [follow]);
          pendingWaves = [follow];
          continue;
        }
        if (replan.decision === "cannot_complete") {
          const quietCommit = this.#startQuietCommit({
            workspaceId: input.workspaceId,
            teamRunId: teamRun.id,
            state: "cannot_complete",
          });
          if (quietCommit === null) {
            return this.#cancelledProof(
              input,
              teamRun.id,
              calls,
              nodes,
              parentFinal,
              emitBound,
            );
          }
          await quietCommit;
          emitBound({ type: "failed", state: "cannot_complete", errorCode: "desktop_team_cannot_complete" });
          return this.#proof(teamRun.id, "cannot_complete", calls, nodes, parentFinal, false);
        }
        break;
      }

      if (this.#cancelled) return this.#cancelledProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
      synthesizing = true;
      emitBound({ type: "parent_synthesizing", employeeRoleId: "parent" });
      const synthesis = await this.#invokeParent({
        input,
        teamRun,
        purpose: "parent-synthesize",
        messages: this.#parentSynthesisMessages(input, reports),
        emit: emitBound,
        calls,
        started,
      });
      if (synthesis.kind !== "ok") {
        return this.#terminalFromInvoke(synthesis, teamRun.id, calls, nodes, parentFinal, input, synthesizing, emitBound);
      }
      if (!lastPlan.validated || lastPlan.decision !== "finish") {
        await this.#settleParentProviderCall(synthesis, input, teamRun.id, {
          state: "failed",
          planRevisionId: null,
          outputSha256: null,
          errorCode: "desktop_team_parent_call_plan_mismatch",
        });
        return this.#terminalFromInvoke(
          { kind: "failed", code: "desktop_team_parent_call_plan_mismatch" },
          teamRun.id,
          calls,
          nodes,
          parentFinal,
          input,
          synthesizing,
          emitBound,
        );
      }
      await this.#settleParentProviderCall(synthesis, input, teamRun.id, {
        state: "succeeded",
        planRevisionId: lastPlan.id,
        outputSha256: sha256Text(synthesis.text),
        errorCode: null,
      });
      parentFinal = synthesis.text;
      await this.#requireNoPendingCollaborations(input, teamRun.id);
      if (this.#cancelled) {
        return this.#cancelledProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
      }
      if (this.#wallExceeded()) {
        return this.#budgetProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
      }
      const successCommit = this.#startSuccessCommit({
        workspaceId: input.workspaceId,
        teamRunId: teamRun.id,
        state: "succeeded",
        parentFinalAnswer: parentFinal,
      });
      if (successCommit === null) {
        return this.#cancelledProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
      }
      await successCommit;
      emitBound({
        type: "completed",
        state: "succeeded",
        parentFinalAnswer: parentFinal,
        employeeRoleId: "parent",
      });
      return this.#proof(teamRun.id, "succeeded", calls, nodes, parentFinal, true);
    } catch (error) {
      const code = errorCode(error);
      if (teamRun !== null && (code === "desktop_team_call_budget_exceeded" || code === "desktop_team_input_budget_exceeded" || code === "desktop_team_output_budget_exceeded")) {
        const quietCommit = this.#startQuietCommit({
          workspaceId: input.workspaceId,
          teamRunId: teamRun.id,
          state: "budget_exhausted",
        });
        if (quietCommit === null) {
          await this.#host.setRunState({
            workspaceId: input.workspaceId,
            teamRunId: teamRun.id,
            state: "cancelled",
          });
          return this.#proof(
            teamRun.id,
            "cancelled",
            calls,
            nodes,
            synthesizing ? null : parentFinal,
            false,
          );
        }
        await quietCommit;
        return this.#proof(teamRun.id, "budget_exhausted", calls, nodes, parentFinal, synthesizing);
      }
      if (teamRun !== null) {
        if (this.#cancelled) {
          await this.#host.setRunState({
            workspaceId: input.workspaceId,
            teamRunId: teamRun.id,
            state: "cancelled",
          }).catch(() => undefined);
        } else {
          const quietCommit = this.#startQuietCommit({
            workspaceId: input.workspaceId,
            teamRunId: teamRun.id,
            state: "failed",
          });
          if (quietCommit === null) {
            await this.#host.setRunState({
              workspaceId: input.workspaceId,
              teamRunId: teamRun.id,
              state: "cancelled",
            }).catch(() => undefined);
          } else {
            await quietCommit.catch(() => undefined);
          }
        }
      }
      throw error;
    } finally {
      this.#clearWall();
      this.#live = false;
    }
  }

  #rememberWaves(store: Map<string, StoredAssignment>, waves: readonly TeamWaveProposal[]): void {
    for (const wave of waves) {
      for (const assignment of wave.assignments) {
        store.set(assignment.assignmentId, {
          assignment,
          waveId: wave.waveId,
          declaredExecution: wave.execution,
          effectiveExecution: wave.execution,
          state: "pending",
        });
      }
    }
  }

  #startSuccessCommit(
    input: Parameters<PersonalTeamHost["setRunState"]>[0],
  ): Promise<{ readonly teamRun: DesktopTeamRun }> | null {
    if (this.#cancelled) return null;
    this.#successCommitStarted = true;
    const commit = this.#host.setRunState(input);
    this.#successCommit = commit.then(
      () => true,
      () => {
        this.#successCommitStarted = false;
        return false;
      },
    );
    return commit;
  }

  #startQuietCommit(
    input: Parameters<PersonalTeamHost["setRunState"]>[0],
  ): Promise<{ readonly teamRun: DesktopTeamRun }> | null {
    if (this.#cancelled) return null;
    this.#quietCommitStarted = true;
    const commit = this.#host.setRunState(input);
    this.#quietCommit = commit.then(
      () => true,
      () => {
        this.#quietCommitStarted = false;
        return false;
      },
    );
    return commit;
  }

  async #executeWave(args: {
    readonly input: DesktopTeamRunExecuteInput;
    readonly teamRun: DesktopTeamRun;
    readonly wave: TeamWaveProposal;
    readonly assignments: Map<string, StoredAssignment>;
    readonly completedIds: Set<string>;
    readonly nodes: StoredNode[];
    readonly reports: EmployeeTeamReport[];
    readonly calls: TeamProviderCallRecord[];
    readonly emit: (event: Omit<DesktopTeamRunEvent, "teamRunId" | "workspaceId" | "rosterEpoch" | "conversationId">) => void;
    readonly started: number;
  }): Promise<{ kind: "ok" } | { kind: "cancelled" | "unknown" | "failed" | "budget"; code?: string }> {
    const pending = [...args.wave.assignments];
    const done = new Set(args.completedIds);
    const planSummary = args.wave.assignments
      .map(
        (item) =>
          `${item.employeeRoleId}:${item.assignmentId}${
            item.dependsOnAssignmentIds.length > 0
              ? ` deps=${item.dependsOnAssignmentIds.join(",")}`
              : ""
          }`,
      )
      .join("; ");
    args.emit({
      type: "wave_starting",
      waveId: args.wave.waveId,
      declaredExecution: args.wave.execution,
      assignmentIds: args.wave.assignments.map((item) => item.assignmentId),
      employeeRoleIds: args.wave.assignments.map((item) => item.employeeRoleId),
      planSummary,
    });
    while (pending.length > 0) {
      if (this.#cancelled) return { kind: "cancelled" };
      if (this.#wallExceeded()) {
        return { kind: "budget" };
      }
      const ready = pending.filter((item) =>
        item.dependsOnAssignmentIds.every((dependency) => done.has(dependency)),
      );
      if (ready.length === 0) return { kind: "failed", code: "desktop_team_dependency_cycle" };
      const demote =
        args.wave.execution === "serial" ||
        ready.some((item) =>
          item.dependsOnAssignmentIds.some((dependency) =>
            pending.some((other) => other.assignmentId === dependency),
          ),
        ) ||
        ready.length > args.input.budget.maximumConcurrentCalls;
      const batch = demote ? [ready[0]!] : ready.slice(0, args.input.budget.maximumConcurrentCalls);
      const effective: TeamWaveExecution = demote ? "serial" : "parallel";
      args.emit({
        type: "wave_starting",
        waveId: args.wave.waveId,
        declaredExecution: args.wave.execution,
        effectiveExecution: effective,
        assignmentIds: args.wave.assignments.map((item) => item.assignmentId),
        employeeRoleIds: args.wave.assignments.map((item) => item.employeeRoleId),
        planSummary,
      });
      const results = await Promise.all(
        batch.map((assignment) =>
          this.#executeNode({
            ...args,
            assignment,
            effectiveExecution: effective,
          }),
        ),
      );
      for (const result of results) {
        if (result.kind !== "ok") return result;
        done.add(result.assignmentId);
        const index = pending.findIndex((item) => item.assignmentId === result.assignmentId);
        if (index >= 0) pending.splice(index, 1);
      }
    }
    return { kind: "ok" };
  }

  async #executeNode(args: {
    readonly input: DesktopTeamRunExecuteInput;
    readonly teamRun: DesktopTeamRun;
    readonly wave: TeamWaveProposal;
    readonly assignment: TeamAssignmentProposal;
    readonly assignments: Map<string, StoredAssignment>;
    readonly nodes: StoredNode[];
    readonly reports: EmployeeTeamReport[];
    readonly calls: TeamProviderCallRecord[];
    readonly emit: (event: Omit<DesktopTeamRunEvent, "teamRunId" | "workspaceId" | "rosterEpoch" | "conversationId">) => void;
    readonly started: number;
    readonly effectiveExecution: TeamWaveExecution;
  }): Promise<
    | { kind: "ok"; assignmentId: string }
    | { kind: "cancelled" | "unknown" | "failed" | "budget"; code?: string }
  > {
    if (this.#cancelled) return { kind: "cancelled" };
    if (this.#wallExceeded()) {
      return { kind: "budget" };
    }
    const stored = args.assignments.get(args.assignment.assignmentId);
    if (stored) stored.effectiveExecution = args.effectiveExecution;
    const invocationId = this.#newId("invocation");
    const nodeEpoch = ++this.#nextNodeEpoch;
    const sendEpoch = ++this.#nextSendEpoch;
    const key = invocationId;
    const controller = this.abort.arm(key);
    const requestSignal = this.#requestSignal(controller.signal);
    let createdId: string | null = null;
    try {
      if (controller.signal.aborted || this.#cancelled) return { kind: this.#abortKind() };
      const credentials = await raceAbort(
        this.#host.resolveCredentials(
          args.input.workspaceId,
          args.assignment.employeeRoleId,
          requestSignal,
        ),
        requestSignal,
      );
      if (isAborted(credentials) || controller.signal.aborted || this.#wallExceeded()) {
        return { kind: this.#abortKind() };
      }
      const consumed = await this.#host.consumeProviderCall({
        workspaceId: args.input.workspaceId,
        teamRunId: args.teamRun.id,
        invocationId,
        purpose: "employee",
        providerId: credentials.providerId,
        requestedModel: credentials.model,
      });
      if (consumed.parentCall !== undefined) {
        throw Object.assign(new Error("desktop_team_parent_call_unexpected"), {
          code: "desktop_team_parent_call_unexpected",
        });
      }
      const callIndex = args.calls.length;
      args.calls.push({
        purpose: "employee",
        roleId: args.assignment.employeeRoleId,
        invocationId,
        nodeId: null,
        assignmentId: args.assignment.assignmentId,
      });
      if (controller.signal.aborted || this.#cancelled || this.#wallExceeded()) {
        return { kind: this.#abortKind() };
      }
      const created = await this.#host.createNode({
        workspaceId: args.input.workspaceId,
        teamRunId: args.teamRun.id,
        assignmentId: args.assignment.assignmentId,
        employeeRoleId: args.assignment.employeeRoleId,
        invocationId,
        waveId: args.wave.waveId,
        nodeEpoch,
        sendEpoch,
        providerId: credentials.providerId,
        requestedModel: credentials.model,
      });
      createdId = created.node.id;
      if (controller.signal.aborted || this.#cancelled || this.#wallExceeded()) {
        const kind = this.#abortKind();
        await this.#markNodeCancelled({
          workspaceId: args.input.workspaceId,
          teamRunId: args.teamRun.id,
          nodeId: created.node.id,
          errorCode: kind === "budget" ? "desktop_team_wall_time_exceeded" : "desktop_invocation_cancelled",
          durationMs: null,
        });
        return { kind };
      }
      const node: StoredNode = {
        nodeId: created.node.id,
        assignmentId: args.assignment.assignmentId,
        invocationId,
        employeeRoleId: args.assignment.employeeRoleId,
        waveId: args.wave.waveId,
        ordinal: created.node.ordinal,
        nodeEpoch,
        sendEpoch,
        state: "running",
        durationMs: null,
        inputTokens: null,
        outputTokens: null,
        totalTokens: null,
        report: null,
      };
      args.nodes.push(node);
      args.calls[callIndex] = {
        purpose: "employee",
        roleId: args.assignment.employeeRoleId,
        invocationId,
        nodeId: created.node.id,
        assignmentId: args.assignment.assignmentId,
      };
      args.emit({
        type: "node_starting",
        waveId: args.wave.waveId,
        assignmentId: args.assignment.assignmentId,
        nodeId: created.node.id,
        nodeOrdinal: created.node.ordinal,
        employeeRoleId: args.assignment.employeeRoleId,
        invocationId,
        sendEpoch,
        nodeEpoch,
        consumedProviderCalls: args.calls.length,
        maximumProviderCalls: args.input.budget.maximumProviderCalls,
      });
      args.emit({
        type: "node_identity",
        waveId: args.wave.waveId,
        assignmentId: args.assignment.assignmentId,
        nodeId: created.node.id,
        nodeOrdinal: created.node.ordinal,
        employeeRoleId: args.assignment.employeeRoleId,
        invocationId,
        sendEpoch,
        nodeEpoch,
      });
      const nodeStarted = this.#now();
      const chat = await raceAbort(
        this.#transport.complete(
          {
            baseUrl: credentials.baseUrl,
            secret: credentials.secret,
            model: credentials.model,
            messages: this.#employeeMessages(args.input, args.assignment, args.reports),
            timeoutMs: credentials.timeoutMs,
            allowLoopbackHttp: credentials.allowLoopbackHttp,
          },
          requestSignal,
        ),
        requestSignal,
      );
      if (isAborted(chat) || controller.signal.aborted || this.#cancelled || this.#wallExceeded()) {
        const kind = this.#abortKind();
        node.state = "cancelled";
        await this.#markNodeCancelled({
          workspaceId: args.input.workspaceId,
          teamRunId: args.teamRun.id,
          nodeId: created.node.id,
          errorCode: kind === "budget" ? "desktop_team_wall_time_exceeded" : "desktop_invocation_cancelled",
          durationMs: this.#now() - nodeStarted,
        });
        args.emit({
          type: "node_terminal",
          waveId: args.wave.waveId,
          assignmentId: args.assignment.assignmentId,
          nodeId: created.node.id,
          invocationId,
          sendEpoch,
          nodeEpoch,
          employeeRoleId: args.assignment.employeeRoleId,
          errorCode: kind === "budget" ? "desktop_team_wall_time_exceeded" : "desktop_invocation_cancelled",
        });
        return { kind };
      }
      const durationMs = this.#now() - nodeStarted;
      assertRequestedModelIdentity(credentials.model, chat.actualModel);
      const report = this.#parseEmployeeReport(chat, args.assignment);
      node.durationMs = durationMs;
      node.inputTokens = chat.inputTokens;
      node.outputTokens = chat.outputTokens;
      node.totalTokens = chat.totalTokens;
      await this.#host.settleNode({
        workspaceId: args.input.workspaceId,
        teamRunId: args.teamRun.id,
        nodeId: created.node.id,
        invocationId,
        state: "succeeded",
        actualModel: chat.actualModel,
        inputTokens: chat.inputTokens,
        outputTokens: chat.outputTokens,
        totalTokens: chat.totalTokens,
        answerSha256: sha256Text(chat.text),
        errorCode: null,
        durationMs,
        waveId: args.wave.waveId,
        nodeEpoch,
        sendEpoch,
        report,
      });
      node.state = "succeeded";
      node.report = report;
      args.reports.push(report);
      if (stored) stored.state = report.status === "completed" ? "completed" : report.status;
      args.emit({
        type: "node_terminal",
        waveId: args.wave.waveId,
        assignmentId: args.assignment.assignmentId,
        nodeId: created.node.id,
        nodeOrdinal: created.node.ordinal,
        employeeRoleId: args.assignment.employeeRoleId,
        invocationId,
        sendEpoch,
        nodeEpoch,
        durationMs,
        inputTokens: chat.inputTokens,
        outputTokens: chat.outputTokens,
        totalTokens: chat.totalTokens,
        answer: report.report,
        reportStatus: report.status,
        collaborationLine:
          report.collaborationRequests.length > 0
            ? report.collaborationRequests
                .map((item) => `${args.assignment.employeeRoleId} → ${item.targetRoleId}: ${item.question}`)
                .join("\n")
            : undefined,
        consumedProviderCalls: args.calls.length,
        maximumProviderCalls: args.input.budget.maximumProviderCalls,
      });
      return { kind: "ok", assignmentId: args.assignment.assignmentId };
    } catch (error) {
      const code = errorCode(error);
      if (createdId !== null && (ABORT_CODES.has(code) || this.#cancelled || this.#wallExceeded())) {
        await this.#markNodeCancelled({
          workspaceId: args.input.workspaceId,
          teamRunId: args.teamRun.id,
          nodeId: createdId,
          errorCode:
            this.#wallExceeded() ? "desktop_team_wall_time_exceeded" : "desktop_invocation_cancelled",
          durationMs: null,
        }).catch(() => undefined);
      }
      if (createdId !== null && !ABORT_CODES.has(code) && !this.#cancelled && !this.#wallExceeded()) {
        const terminal =
          code === "desktop_invocation_interrupted" ||
          code === "desktop_provider_stream_incomplete" ||
          code === "desktop_provider_response_invalid"
            ? "unknown"
            : "failed";
        await this.#host
          .updateNode({
            workspaceId: args.input.workspaceId,
            teamRunId: args.teamRun.id,
            nodeId: createdId,
            state: terminal,
            actualModel: null,
            inputTokens: null,
            outputTokens: null,
            totalTokens: null,
            answerSha256: null,
            errorCode: code,
            durationMs: null,
          })
          .catch(() => undefined);
      }
      if (ABORT_CODES.has(code) || this.#cancelled || this.#wallExceeded()) return { kind: this.#abortKind() };
      if (
        code === "desktop_invocation_interrupted" ||
        code === "desktop_provider_stream_incomplete" ||
        code === "desktop_provider_response_invalid" ||
        code === "desktop_native_response_invalid"
      ) {
        return { kind: "unknown", code };
      }
      if (code === "desktop_team_call_budget_exceeded") return { kind: "budget", code };
      return { kind: "failed", code };
    } finally {
      this.abort.release(key, controller);
    }
  }

  async #settleParentProviderCall(
    call: {
      readonly invocationId: string;
      readonly purpose: TeamParentCallPurpose;
      readonly providerId: string;
      readonly requestedModel: string;
      readonly result?: TeamChatResult;
    },
    input: DesktopTeamRunExecuteInput,
    teamRunId: string,
    settlement: {
      readonly state: TeamParentCallTerminalState;
      readonly planRevisionId: string | null;
      readonly outputSha256: string | null;
      readonly errorCode: string | null;
    },
  ): Promise<void> {
    const result = settlement.state === "cancelled" ? undefined : call.result;
    const usage = normalizedUsage(result);
    await this.#host.settleParentCall({
      workspaceId: input.workspaceId,
      teamRunId,
      invocationId: call.invocationId,
      purpose: call.purpose,
      providerId: call.providerId,
      requestedModel: call.requestedModel,
      state: settlement.state,
      planRevisionId: settlement.planRevisionId,
      actualModel: result?.actualModel ?? null,
      inputTokens: usage.inputTokens,
      outputTokens: usage.outputTokens,
      totalTokens: usage.totalTokens,
      outputSha256: settlement.outputSha256,
      errorCode: settlement.errorCode,
    });
  }

  async #invokeParent(args: {
    readonly input: DesktopTeamRunExecuteInput;
    readonly teamRun: DesktopTeamRun;
    readonly purpose: TeamParentCallPurpose;
    readonly messages: readonly TeamChatMessage[];
    readonly emit: (event: Omit<DesktopTeamRunEvent, "teamRunId" | "workspaceId" | "rosterEpoch" | "conversationId">) => void;
    readonly calls: TeamProviderCallRecord[];
    readonly started: number;
  }): Promise<
    | ParentProviderCallSuccess
    | { kind: "cancelled" | "unknown" | "failed" | "budget"; code?: string }
  > {
    if (this.#cancelled) return { kind: "cancelled" };
    if (this.#wallExceeded()) return { kind: "budget" };
    const invocationId = this.#newId("invocation");
    const controller = this.abort.arm(invocationId);
    const requestSignal = this.#requestSignal(controller.signal);
    let identity: {
      readonly invocationId: string;
      readonly purpose: TeamParentCallPurpose;
      readonly providerId: string;
      readonly requestedModel: string;
    } | null = null;
    let providerResult: TeamChatResult | undefined;
    let settlementAttempted = false;
    let consumeConfirmed = false;
    try {
      if (controller.signal.aborted) return { kind: this.#abortKind() };
      const credentials = await raceAbort(
        this.#host.resolveCredentials(args.input.workspaceId, "parent", requestSignal),
        requestSignal,
      );
      if (isAborted(credentials) || controller.signal.aborted || this.#wallExceeded()) {
        return { kind: this.#abortKind() };
      }
      identity = {
        invocationId,
        purpose: args.purpose,
        providerId: credentials.providerId,
        requestedModel: credentials.model,
      };
      const consumed = await this.#host.consumeProviderCall({
        workspaceId: args.input.workspaceId,
        teamRunId: args.teamRun.id,
        invocationId,
        purpose: args.purpose,
        providerId: credentials.providerId,
        requestedModel: credentials.model,
      });
      if (
        consumed.parentCall === undefined ||
        consumed.parentCall.invocationId !== invocationId ||
        consumed.parentCall.teamRunId !== args.teamRun.id ||
        consumed.parentCall.purpose !== args.purpose ||
        consumed.parentCall.providerId !== credentials.providerId ||
        consumed.parentCall.requestedModel !== credentials.model ||
        consumed.parentCall.state !== "pending"
      ) {
        throw Object.assign(new Error("desktop_native_response_invalid"), {
          code: "desktop_native_response_invalid",
        });
      }
      consumeConfirmed = true;
      args.calls.push({
        purpose: args.purpose,
        roleId: "parent",
        invocationId,
        nodeId: null,
        assignmentId: null,
      });
      if (controller.signal.aborted || this.#cancelled || this.#wallExceeded()) {
        const kind = this.#abortKind();
        settlementAttempted = true;
        await this.#settleParentProviderCall(identity, args.input, args.teamRun.id, {
          state: kind === "cancelled" ? "cancelled" : "failed",
          planRevisionId: null,
          outputSha256: null,
          errorCode:
            kind === "cancelled"
              ? "desktop_invocation_cancelled"
              : "desktop_team_wall_time_exceeded",
        });
        return { kind };
      }
      args.emit({
        type: "node_identity",
        employeeRoleId: "parent",
        invocationId,
        sendEpoch: args.calls.length,
        consumedProviderCalls: args.calls.length,
        maximumProviderCalls: args.input.budget.maximumProviderCalls,
      });
      const chat = await raceAbort(
        this.#transport.complete(
          {
            baseUrl: credentials.baseUrl,
            secret: credentials.secret,
            model: credentials.model,
            messages: args.messages,
            timeoutMs: credentials.timeoutMs,
            allowLoopbackHttp: credentials.allowLoopbackHttp,
          },
          requestSignal,
        ),
        requestSignal,
      );
      if (isAborted(chat) || controller.signal.aborted || this.#cancelled || this.#wallExceeded()) {
        const kind = this.#abortKind();
        settlementAttempted = true;
        await this.#settleParentProviderCall(identity, args.input, args.teamRun.id, {
          state: kind === "cancelled" ? "cancelled" : "failed",
          planRevisionId: null,
          outputSha256: null,
          errorCode:
            kind === "cancelled"
              ? "desktop_invocation_cancelled"
              : "desktop_team_wall_time_exceeded",
        });
        return { kind };
      }
      providerResult = chat;
      assertRequestedModelIdentity(credentials.model, chat.actualModel);
      const outputText =
        args.purpose === "parent-synthesize"
          ? normalizeParentFinalText(
              chat.text,
              args.input.budget.maximumOutputCharacters,
            )
          : chat.text;
      if (outputText === null) {
        settlementAttempted = true;
        await this.#settleParentProviderCall(
          { ...identity, result: chat },
          args.input,
          args.teamRun.id,
          {
            state: "failed",
            planRevisionId: null,
            outputSha256: null,
            errorCode: "desktop_team_output_budget_exceeded",
          },
        );
        return {
          kind: "budget",
          code: "desktop_team_output_budget_exceeded",
        };
      }
      if (args.purpose === "parent-synthesize" && this.#cancelled) {
        settlementAttempted = true;
        await this.#settleParentProviderCall(
          { ...identity, result: chat },
          args.input,
          args.teamRun.id,
          {
            state: "cancelled",
            planRevisionId: null,
            outputSha256: null,
            errorCode: "desktop_invocation_cancelled",
          },
        );
        return { kind: "cancelled" };
      }
      args.emit({
        type: "node_delta",
        employeeRoleId: "parent",
        invocationId,
        sendEpoch: args.calls.length,
        text: outputText,
      });
      if (this.#cancelled || this.#wallExceeded()) {
        const kind = this.#abortKind();
        settlementAttempted = true;
        await this.#settleParentProviderCall(
          { ...identity, result: chat },
          args.input,
          args.teamRun.id,
          {
            state: kind === "cancelled" ? "cancelled" : "failed",
            planRevisionId: null,
            outputSha256: null,
            errorCode:
              kind === "cancelled"
                ? "desktop_invocation_cancelled"
                : "desktop_team_wall_time_exceeded",
          },
        );
        return { kind };
      }
      return {
        kind: "ok",
        text: outputText,
        result: chat,
        ...identity,
      };
    } catch (error) {
      const code = errorCode(error);
      if (identity !== null && !settlementAttempted) {
        settlementAttempted = true;
        const aborted = ABORT_CODES.has(code) || this.#cancelled || this.#wallExceeded();
        const kind = aborted ? this.#abortKind() : null;
        const unknown =
          code === "desktop_invocation_interrupted" ||
          code === "desktop_provider_stream_incomplete" ||
          code === "desktop_provider_response_invalid" ||
          code === "desktop_native_response_invalid";
        const settlement = {
          state:
            kind === "cancelled"
              ? ("cancelled" as const)
              : unknown || !consumeConfirmed
                ? ("unknown" as const)
                : ("failed" as const),
          planRevisionId: null,
          outputSha256: null,
          errorCode:
            kind === "cancelled"
              ? "desktop_invocation_cancelled"
              : kind === "budget"
                ? "desktop_team_wall_time_exceeded"
                : !consumeConfirmed
                  ? "desktop_team_parent_call_consume_unknown"
                  : code,
        };
        try {
          await this.#settleParentProviderCall(
            { ...identity, ...(providerResult === undefined ? {} : { result: providerResult }) },
            args.input,
            args.teamRun.id,
            settlement,
          );
          if (!consumeConfirmed) {
            return {
              kind: "unknown",
              code: "desktop_team_parent_call_consume_unknown",
            };
          }
        } catch (settleError) {
          if (consumeConfirmed) throw settleError;
        }
      }
      if (ABORT_CODES.has(code) || this.#cancelled || this.#wallExceeded()) return { kind: this.#abortKind() };
      if (
        code === "desktop_invocation_interrupted" ||
        code === "desktop_provider_stream_incomplete" ||
        code === "desktop_provider_response_invalid" ||
        code === "desktop_native_response_invalid"
      ) {
        return { kind: "unknown", code };
      }
      if (code === "desktop_team_call_budget_exceeded") return { kind: "budget", code };
      return { kind: "failed", code };
    } finally {
      this.abort.release(invocationId, controller);
    }
  }

  #parseEmployeeReport(
    chat: TeamChatResult,
    assignment: TeamAssignmentProposal,
  ): EmployeeTeamReport {
    const parsed = extractJsonObject(chat.text);
    if (typeof parsed === "object" && parsed !== null) {
      const record = parsed as Record<string, unknown>;
      const status =
        record.status === "needs_collaboration" || record.status === "blocked"
          ? record.status
          : "completed";
      const requests = Array.isArray(record.collaborationRequests)
        ? record.collaborationRequests.flatMap((item) => {
            if (typeof item !== "object" || item === null) return [];
            const row = item as Record<string, unknown>;
            if (
              typeof row.targetRoleId !== "string" ||
              !SPECIALIST_EMPLOYEE_IDS.includes(row.targetRoleId as SpecialistEmployeeId) ||
              typeof row.question !== "string" ||
              typeof row.reason !== "string"
            ) {
              return [];
            }
            return [
              {
                targetRoleId: row.targetRoleId as SpecialistEmployeeId,
                question: row.question,
                reason: row.reason,
              },
            ];
          })
        : [];
      if (record.status === "needs_collaboration" && requests.length === 0) {
        throw Object.assign(new Error("desktop_team_report_invalid"), {
          code: "desktop_team_report_invalid",
        });
      }
      return Object.freeze({
        assignmentId: assignment.assignmentId,
        employeeRoleId: assignment.employeeRoleId,
        status,
        report: typeof record.report === "string" ? record.report : chat.text,
        collaborationRequests: Object.freeze(requests),
      });
    }
    return Object.freeze({
      assignmentId: assignment.assignmentId,
      employeeRoleId: assignment.employeeRoleId,
      status: "completed",
      report: chat.text,
      collaborationRequests: Object.freeze([]),
    });
  }

  #parentProposeMessages(input: DesktopTeamRunExecuteInput): readonly TeamChatMessage[] {
    return [
      {
        role: "system",
        content:
          "[omnibase-team-role:parent-propose]\nYou are the OmniBase parent Agent. Team mode is on. Output ONLY JSON ParentTeamDecision with decision answer_directly or delegate. Never dispatch directly. Never invent roles. Never include tools, secrets, or paths.",
      },
      {
        role: "user",
        content: `Owner objective:\n${input.task}\nAllowed specialists: ${(input.allowedSpecialistRoleIds ?? SPECIALIST_EMPLOYEE_IDS).join(", ")}`,
      },
    ];
  }

  #parentReplanMessages(
    input: DesktopTeamRunExecuteInput,
    reports: readonly EmployeeTeamReport[],
    assignments: Map<string, StoredAssignment>,
    pendingCollaboration: readonly {
      id: string;
      targetRoleId: string;
      question: string;
      reason: string;
    }[],
  ): readonly TeamChatMessage[] {
    return [
      {
        role: "system",
        content:
          "[omnibase-team-role:parent-replan]\nOutput ONLY JSON ParentReplanDecision: continue | request_followup | finish | cannot_complete. Every pending collaboration request must be decided exactly once in collaborationDecisions: [{requestId, decision: accept_start|handle_self|merge_existing|decline, resolvedAssignmentId}] — accept_start binds a NEW same-role assignment from this proposal, handle_self/decline carry no assignment. Employees cannot launch peers. New assignment IDs required for reinvoke.",
      },
      {
        role: "user",
        content: JSON.stringify({
          objective: input.task,
          reports,
          pendingCollaboration,
          knownAssignmentIds: [...assignments.keys()],
        }),
      },
    ];
  }

  #parentSynthesisMessages(
    input: DesktopTeamRunExecuteInput,
    reports: readonly EmployeeTeamReport[],
  ): readonly TeamChatMessage[] {
    return [
      {
        role: "system",
        content:
          "[omnibase-team-role:parent-synthesize]\nWrite the Owner-facing final answer. Do not mention secrets, vaults, or keys. Do not call more employees.",
      },
      {
        role: "user",
        content: JSON.stringify({ objective: input.task, reports }),
      },
    ];
  }

  #employeeMessages(
    input: DesktopTeamRunExecuteInput,
    assignment: TeamAssignmentProposal,
    reports: readonly EmployeeTeamReport[],
  ): readonly TeamChatMessage[] {
    const predecessors = reports.filter((item) =>
      assignment.dependsOnAssignmentIds.includes(item.assignmentId),
    );
    return [
      {
        role: "system",
        content: `[omnibase-team-role:employee:${assignment.employeeRoleId}]\nYou are ${assignment.employeeRoleId}. Duty: ${ROLE_DUTY[assignment.employeeRoleId]}. Output ONLY JSON EmployeeTeamReport. You cannot launch another employee. Collaboration requests return to the parent.`,
      },
      {
        role: "user",
        content: JSON.stringify({
          ownerObjective: input.task,
          roleDuty: ROLE_DUTY[assignment.employeeRoleId],
          assignmentId: assignment.assignmentId,
          assignedSubtask: assignment.objective,
          expectedOutput: assignment.expectedOutput,
          predecessorReports: predecessors,
          structuredProgress: reports.map((item) => ({
            assignmentId: item.assignmentId,
            employeeRoleId: item.employeeRoleId,
            status: item.status,
          })),
        }),
      },
    ];
  }

  async #terminalFromInvoke(
    result: { kind: "cancelled" | "unknown" | "failed" | "budget"; code?: string },
    teamRunId: string,
    calls: readonly TeamProviderCallRecord[],
    nodes: readonly StoredNode[],
    parentFinal: string | null,
    input: DesktopTeamRunExecuteInput,
    synthesizing: boolean,
    emit: (event: Omit<DesktopTeamRunEvent, "teamRunId" | "workspaceId" | "rosterEpoch" | "conversationId">) => void,
  ): Promise<DesktopTeamRunProof> {
    if (this.#cancelled) {
      return this.#cancelledProof(input, teamRunId, calls, nodes, parentFinal, emit);
    }
    if (result.kind === "cancelled") {
      await this.#host.setRunState({
        workspaceId: input.workspaceId,
        teamRunId,
        state: "cancelled",
      });
      emit({ type: "cancelled", state: "cancelled" });
      return this.#proof(teamRunId, "cancelled", calls, nodes, synthesizing ? null : parentFinal, false);
    }
    if (result.kind === "budget") {
      const quietCommit = this.#startQuietCommit({
        workspaceId: input.workspaceId,
        teamRunId,
        state: "budget_exhausted",
      });
      if (quietCommit === null) {
        return this.#cancelledProof(input, teamRunId, calls, nodes, parentFinal, emit);
      }
      await quietCommit;
      emit({ type: "budget_exhausted", state: "budget_exhausted" });
      return this.#proof(teamRunId, "budget_exhausted", calls, nodes, parentFinal, false);
    }
    if (result.kind === "unknown") {
      const quietCommit = this.#startQuietCommit({
        workspaceId: input.workspaceId,
        teamRunId,
        state: "unknown",
      });
      if (quietCommit === null) {
        return this.#cancelledProof(input, teamRunId, calls, nodes, parentFinal, emit);
      }
      await quietCommit;
      emit({ type: "unknown", state: "unknown" });
      return this.#proof(teamRunId, "unknown", calls, nodes, parentFinal, false);
    }
    const quietCommit = this.#startQuietCommit({
      workspaceId: input.workspaceId,
      teamRunId,
      state: "failed",
    });
    if (quietCommit === null) {
      return this.#cancelledProof(input, teamRunId, calls, nodes, parentFinal, emit);
    }
    await quietCommit;
    emit({ type: "failed", state: "failed", errorCode: result.code });
    return this.#proof(teamRunId, "failed", calls, nodes, parentFinal, false);
  }

  async #budgetProof(
    input: DesktopTeamRunExecuteInput,
    teamRunId: string,
    calls: readonly TeamProviderCallRecord[],
    nodes: readonly StoredNode[],
    parentFinal: string | null,
    emit: (event: Omit<DesktopTeamRunEvent, "teamRunId" | "workspaceId" | "rosterEpoch" | "conversationId">) => void,
  ): Promise<DesktopTeamRunProof> {
    if (this.#cancelled) {
      return this.#cancelledProof(input, teamRunId, calls, nodes, parentFinal, emit);
    }
    const quietCommit = this.#startQuietCommit({
      workspaceId: input.workspaceId,
      teamRunId,
      state: "budget_exhausted",
    });
    if (quietCommit === null) {
      return this.#cancelledProof(input, teamRunId, calls, nodes, parentFinal, emit);
    }
    await quietCommit;
    emit({ type: "budget_exhausted", state: "budget_exhausted" });
    return this.#proof(teamRunId, "budget_exhausted", calls, nodes, parentFinal, false);
  }

  async #cancelledProof(
    input: DesktopTeamRunExecuteInput,
    teamRunId: string,
    calls: readonly TeamProviderCallRecord[],
    nodes: readonly StoredNode[],
    parentFinal: string | null,
    emit: (event: Omit<DesktopTeamRunEvent, "teamRunId" | "workspaceId" | "rosterEpoch" | "conversationId">) => void,
  ): Promise<DesktopTeamRunProof> {
    await this.#host.setRunState({
      workspaceId: input.workspaceId,
      teamRunId,
      state: "cancelled",
    });
    emit({ type: "cancelled", state: "cancelled" });
    return this.#proof(teamRunId, "cancelled", calls, nodes, parentFinal, false);
  }

  #proof(
    teamRunId: string,
    state: TeamRunState,
    calls: readonly TeamProviderCallRecord[],
    nodes: readonly StoredNode[],
    parentFinal: string | null,
    synthesized: boolean,
  ): DesktopTeamRunProof {
    const invocationIds = calls.map((item) => item.invocationId);
    const nodeIds = nodes.map((item) => item.nodeId);
    const assignmentIds = nodes.map((item) => item.assignmentId);
    const last = calls[calls.length - 1];
    return Object.freeze({
      teamRunId,
      state,
      providerCallCount: calls.length,
      executedNodeCount: nodes.length,
      parentCallCount: calls.filter((item) => item.roleId === "parent").length,
      uniqueInvocationIds: Object.freeze([...new Set(invocationIds)]),
      uniqueNodeIds: Object.freeze([...new Set(nodeIds)]),
      uniqueAssignmentIds: Object.freeze([...new Set(assignmentIds)]),
      parentWasLastWhenSynthesizing: synthesized ? last?.purpose === "parent-synthesize" : true,
      hiddenCalls: false,
      parentFinalAnswer: parentFinal,
    });
  }
}

export interface MemoryTeamHostOptions {
  readonly credentials: TeamRoleCredentials;
}

export function createInMemoryPersonalTeamHost(
  options: MemoryTeamHostOptions,
): PersonalTeamHost & {
  readonly runs: DesktopTeamRun[];
  readonly nodes: { id: string; invocationId: string; assignmentId: string; state: string }[];
  readonly reports: EmployeeTeamReport[];
  readonly audits: readonly string[];
  readonly parentCalls: TeamParentCallRecord[];
  readonly planRevisions: DesktopTeamPlanRevision[];
  readonly assignmentStates: ReadonlyMap<string, string>;
  failNextSettle: string | null;
} {
  const runs: DesktopTeamRun[] = [];
  const nodes: { id: string; invocationId: string; assignmentId: string; nodeEpoch: number; sendEpoch: number; state: string }[] = [];
  const reports: EmployeeTeamReport[] = [];
  const audits: string[] = [];
  const parentCalls: TeamParentCallRecord[] = [];
  const planRevisions: DesktopTeamPlanRevision[] = [];
  const assignmentStates = new Map<
    string,
    | "pending"
    | "ready"
    | "running"
    | "completed"
    | "needs_collaboration"
    | "blocked"
    | "cancelled"
  >();
  const consumedInvocations = new Set<string>();
  const assignments = new Map<string, TeamAssignmentProposal>();
  const collaborationDecisions = new Map<
    string,
    { parentDecision: "accept_start" | "handle_self" | "merge_existing" | "decline"; resolvedAssignmentId: string | null }
  >();
  let revision = 0;
  const allowed = new Set<string>(SPECIALIST_EMPLOYEE_IDS);

  const host: PersonalTeamHost & {
    readonly runs: DesktopTeamRun[];
    readonly nodes: { id: string; invocationId: string; assignmentId: string; state: string }[];
    readonly reports: EmployeeTeamReport[];
    readonly audits: string[];
    readonly parentCalls: TeamParentCallRecord[];
    readonly planRevisions: DesktopTeamPlanRevision[];
    readonly assignmentStates: ReadonlyMap<string, string>;
    failNextSettle: string | null;
  } = {
    runs,
    nodes,
    reports,
    audits,
    parentCalls,
    planRevisions,
    assignmentStates,
    failNextSettle: null,
    async startTeamRun(input) {
      if (input.allowedSpecialistRoleIds !== undefined && input.allowedSpecialistRoleIds.length === 0) {
        throw Object.assign(new Error("desktop_team_allow_list_empty"), {
          code: "desktop_team_allow_list_empty",
        });
      }
      if (runs.some((item) => item.state === "preparing" || item.state === "running" || item.state === "cancelling")) {
        throw Object.assign(new Error("desktop_team_run_already_active"), {
          code: "desktop_team_run_already_active",
        });
      }
      const teamRun: DesktopTeamRun = {
        id: `teamrun_${randomBytes(16).toString("hex")}`,
        workspaceId: input.workspaceId,
        conversationId: input.conversationId,
        mode: "team",
        state: "preparing",
        staffingAuthority: "parent_proposal",
        currentPlanRevisionId: null,
        currentWaveId: null,
        dispatchedParticipantCount: null,
        maximumProviderCalls: input.budget.maximumProviderCalls,
        maximumWallTimeMs: input.budget.maximumWallTimeMs,
        maximumConcurrentCalls: input.budget.maximumConcurrentCalls,
        maximumInputCharacters: input.budget.maximumInputCharacters,
        maximumOutputCharacters: input.budget.maximumOutputCharacters,
        consumedProviderCalls: 0,
        task: input.task,
        allowedSpecialistRoleIds: input.allowedSpecialistRoleIds ?? SPECIALIST_EMPLOYEE_IDS,
        createdAt: new Date().toISOString(),
        updatedAt: new Date().toISOString(),
      };
      runs.push(teamRun);
      return { teamRun };
    },
    async submitProposal(input) {
      const run = runs.find((item) => item.id === input.teamRunId);
      if (run === undefined) {
        throw Object.assign(new Error("desktop_team_run_not_found"), { code: "desktop_team_run_not_found" });
      }
      const budget: TeamRunBudget = {
        maximumProviderCalls: run.maximumProviderCalls,
        maximumWallTimeMs: run.maximumWallTimeMs,
        maximumConcurrentCalls: run.maximumConcurrentCalls,
        maximumInputCharacters: run.maximumInputCharacters,
        maximumOutputCharacters: run.maximumOutputCharacters,
      };
      revision += 1;
      const pendingCollaboration = reports
        .flatMap((item, reportIndex) =>
          item.collaborationRequests.map((request, requestIndex) => ({
            id: `teamcollab_${item.assignmentId}_${requestIndex}`,
            targetRoleId: request.targetRoleId,
            reportIndex,
            requestIndex,
          })),
        )
        .filter((entry) => !collaborationDecisions.has(entry.id))
        .map((entry) => ({ id: entry.id, targetRoleId: entry.targetRoleId }));
      const validated =
        assignments.size === 0
          ? validateParentTeamDecision(input.proposal, budget, allowed, run.workspaceId)
          : validateParentReplanDecision(
              input.proposal,
              budget,
              allowed,
              run.workspaceId,
              new Set(assignments.keys()),
              revision,
              pendingCollaboration,
            );
      const planRevision: DesktopTeamPlanRevision = {
        id: `teamrev_${randomBytes(16).toString("hex")}`,
        revisionOrdinal: revision,
        decision:
          typeof input.proposal === "object" &&
          input.proposal !== null &&
          "decision" in input.proposal &&
          typeof input.proposal.decision === "string"
            ? input.proposal.decision
            : "cannot_complete",
        proposalJsonSha256: sha256Text(JSON.stringify(input.proposal)),
        validated: validated.ok,
        validationErrorCode: validated.ok ? null : validated.code,
        createdAt: new Date().toISOString(),
      };
      planRevisions.push(planRevision);
      if (validated.ok && validated.value.decision === "delegate") {
        for (const wave of validated.value.waves) {
          for (const assignment of wave.assignments) {
            assignments.set(assignment.assignmentId, assignment);
            assignmentStates.set(assignment.assignmentId, "pending");
          }
        }
      }
      if (validated.ok && validated.value.decision === "continue") {
        for (const assignment of validated.value.nextWave.assignments) {
          assignments.set(assignment.assignmentId, assignment);
          assignmentStates.set(assignment.assignmentId, "pending");
        }
      }
      if (validated.ok && validated.value.decision === "request_followup") {
        for (const assignment of validated.value.assignments) {
          assignments.set(assignment.assignmentId, assignment);
          assignmentStates.set(assignment.assignmentId, "pending");
        }
      }
      const currentRun = validated.ok
        ? { ...run, currentPlanRevisionId: planRevision.id }
        : run;
      if (validated.ok) runs[runs.indexOf(run)] = currentRun;
      return {
        accepted: validated.ok,
        validationErrorCode: validated.ok ? null : validated.code,
        teamRun: currentRun,
        planRevision,
      };
    },
    async getBlackboard(input) {
      const run = runs.find((item) => item.id === input.teamRunId);
      if (run === undefined) {
        throw Object.assign(new Error("desktop_team_run_not_found"), { code: "desktop_team_run_not_found" });
      }
      return {
        blackboard: {
          teamRunId: run.id,
          workspaceId: run.workspaceId,
          ownerObjective: run.task,
          currentPlanRevisionId: run.currentPlanRevisionId,
          assignments: [...assignments.values()].map((item) => ({
            assignmentId: item.assignmentId,
            employeeRoleId: item.employeeRoleId,
            objective: item.objective,
            state: assignmentStates.get(item.assignmentId) ?? "pending",
            waveId: "wave",
            dependsOnAssignmentIds: item.dependsOnAssignmentIds,
            expectedOutput: item.expectedOutput,
          })),
          reports,
          collaborationRequests: reports.flatMap((item) =>
            item.collaborationRequests.map((request, requestIndex) => {
              const id = `teamcollab_${item.assignmentId}_${requestIndex}`;
              const decision = collaborationDecisions.get(id);
              return {
                id,
                fromAssignmentId: item.assignmentId,
                fromEmployeeRoleId: item.employeeRoleId,
                targetRoleId: request.targetRoleId,
                question: request.question,
                reason: request.reason,
                parentDecision: decision?.parentDecision ?? ("pending" as const),
                resolvedAssignmentId: decision?.resolvedAssignmentId ?? null,
              };
            }),
          ),
        },
      };
    },
    async consumeProviderCall(input) {
      const run = runs.find((item) => item.id === input.teamRunId);
      if (run === undefined) {
        throw Object.assign(new Error("desktop_team_run_not_found"), { code: "desktop_team_run_not_found" });
      }
      if (run.consumedProviderCalls >= run.maximumProviderCalls) {
        throw Object.assign(new Error("desktop_team_call_budget_exceeded"), {
          code: "desktop_team_call_budget_exceeded",
        });
      }
      if (consumedInvocations.has(input.invocationId)) {
        throw Object.assign(new Error("desktop_team_duplicate_invocation"), {
          code: "desktop_team_duplicate_invocation",
        });
      }
      consumedInvocations.add(input.invocationId);
      const next = { ...run, consumedProviderCalls: run.consumedProviderCalls + 1 };
      const index = runs.indexOf(run);
      runs[index] = next;
      if (input.purpose === "employee") return { teamRun: next };
      const now = new Date().toISOString();
      const parentCall: TeamParentCallRecord = {
        invocationId: input.invocationId,
        teamRunId: input.teamRunId,
        planRevisionId: null,
        purpose: input.purpose,
        state: "pending",
        providerId: input.providerId,
        requestedModel: input.requestedModel,
        actualModel: null,
        inputTokens: null,
        outputTokens: null,
        totalTokens: null,
        outputSha256: null,
        errorCode: null,
        createdAt: now,
        updatedAt: now,
      };
      parentCalls.push(parentCall);
      return { teamRun: next, parentCall };
    },
    async settleParentCall(input) {
      const run = runs.find((item) => item.id === input.teamRunId);
      if (run === undefined) {
        throw Object.assign(new Error("desktop_team_run_not_found"), {
          code: "desktop_team_run_not_found",
        });
      }
      const existing = parentCalls.find(
        (item) =>
          item.invocationId === input.invocationId &&
          item.teamRunId === input.teamRunId,
      );
      if (existing === undefined) {
        throw Object.assign(new Error("desktop_team_parent_call_not_found"), {
          code: "desktop_team_parent_call_not_found",
        });
      }
      if (existing.state !== "pending") {
        const exactReplay =
          existing.purpose === input.purpose &&
          existing.providerId === input.providerId &&
          existing.requestedModel === input.requestedModel &&
          existing.state === input.state &&
          existing.planRevisionId === input.planRevisionId &&
          existing.actualModel === input.actualModel &&
          existing.inputTokens === input.inputTokens &&
          existing.outputTokens === input.outputTokens &&
          existing.totalTokens === input.totalTokens &&
          existing.outputSha256 === input.outputSha256 &&
          existing.errorCode === input.errorCode;
        if (exactReplay) return { parentCall: existing };
        throw Object.assign(new Error("desktop_team_parent_call_settle_conflict"), {
          code: "desktop_team_parent_call_settle_conflict",
        });
      }
      if (
        existing.purpose !== input.purpose ||
        existing.providerId !== input.providerId ||
        existing.requestedModel !== input.requestedModel
      ) {
        throw Object.assign(new Error("desktop_team_parent_call_identity_mismatch"), {
          code: "desktop_team_parent_call_identity_mismatch",
        });
      }
      if (input.state === "succeeded") {
        const plan = planRevisions.find((item) => item.id === input.planRevisionId);
        const purposeMatches =
          plan !== undefined &&
          plan.validated &&
          ((input.purpose === "parent-propose" &&
            plan.revisionOrdinal === 1 &&
            (plan.decision === "answer_directly" || plan.decision === "delegate")) ||
            (input.purpose === "parent-replan" &&
              plan.revisionOrdinal > 1 &&
              (plan.decision === "continue" ||
                plan.decision === "request_followup" ||
                plan.decision === "finish" ||
                plan.decision === "cannot_complete")) ||
            (input.purpose === "parent-synthesize" && plan.decision === "finish"));
        if (
          !purposeMatches ||
          input.actualModel !== input.requestedModel ||
          input.outputSha256 === null ||
          input.errorCode !== null ||
          (input.purpose !== "parent-synthesize" &&
            input.outputSha256 !== plan?.proposalJsonSha256)
        ) {
          throw Object.assign(new Error("desktop_team_parent_call_proof_invalid"), {
            code: "desktop_team_parent_call_proof_invalid",
          });
        }
      } else if (input.outputSha256 !== null || input.errorCode === null) {
        throw Object.assign(new Error("desktop_team_parent_call_proof_invalid"), {
          code: "desktop_team_parent_call_proof_invalid",
        });
      }
      const settled: TeamParentCallRecord = {
        ...existing,
        planRevisionId: input.planRevisionId,
        state: input.state,
        actualModel: input.actualModel,
        inputTokens: input.inputTokens,
        outputTokens: input.outputTokens,
        totalTokens: input.totalTokens,
        outputSha256: input.outputSha256,
        errorCode: input.errorCode,
        updatedAt: new Date().toISOString(),
      };
      parentCalls[parentCalls.indexOf(existing)] = settled;
      audits.push(`team_parent_call_settled:${input.invocationId}:${input.state}`);
      return { parentCall: settled };
    },
    async setRunState(input) {
      const run = runs.find((item) => item.id === input.teamRunId);
      if (run === undefined) {
        throw Object.assign(new Error("desktop_team_run_not_found"), { code: "desktop_team_run_not_found" });
      }
      const pending = parentCalls.filter(
        (item) => item.teamRunId === input.teamRunId && item.state === "pending",
      );
      if (input.state === "cancelled") {
        for (const call of pending) {
          const cancelled: TeamParentCallRecord = {
            ...call,
            state: "cancelled",
            errorCode: "desktop_invocation_cancelled",
            updatedAt: new Date().toISOString(),
          };
          parentCalls[parentCalls.indexOf(call)] = cancelled;
        }
        for (const node of nodes) {
          if (node.state === "pending" || node.state === "running") {
            node.state = "cancelled";
          }
        }
        for (const [assignmentId, state] of assignmentStates) {
          if (state === "pending" || state === "ready" || state === "running") {
            assignmentStates.set(assignmentId, "cancelled");
          }
        }
      } else if (
        input.state === "failed" ||
        input.state === "unknown" ||
        input.state === "budget_exhausted" ||
        input.state === "cannot_complete"
      ) {
        const liveNode = nodes.some(
          (node) => node.state === "pending" || node.state === "running",
        );
        const runningAssignment = [...assignmentStates.values()].some(
          (state) => state === "running",
        );
        if (liveNode || runningAssignment || pending.length > 0) {
          throw Object.assign(new Error("desktop_team_run_children_live"), {
            code: "desktop_team_run_children_live",
          });
        }
        for (const [assignmentId, state] of assignmentStates) {
          if (state === "pending" || state === "ready") {
            assignmentStates.set(assignmentId, "blocked");
          }
        }
      } else if (
        input.state !== "preparing" &&
        input.state !== "running" &&
        input.state !== "cancelling" &&
        pending.length > 0
      ) {
        throw Object.assign(new Error("desktop_team_run_children_live"), {
          code: "desktop_team_run_children_live",
        });
      }
      if (input.state === "succeeded") {
        const plan = planRevisions.find((item) => item.id === run.currentPlanRevisionId);
        const proposalPurpose =
          plan?.decision === "answer_directly" ? "parent-propose" : "parent-replan";
        const proposalProven = parentCalls.some(
          (item) =>
            item.teamRunId === run.id &&
            item.planRevisionId === plan?.id &&
            item.purpose === proposalPurpose &&
            item.state === "succeeded" &&
            item.outputSha256 === plan?.proposalJsonSha256,
        );
        const synthesisProven =
          plan?.decision !== "finish" ||
          (typeof input.parentFinalAnswer === "string" &&
            input.parentFinalAnswer.length > 0 &&
            parentCalls.some(
              (item) =>
                item.teamRunId === run.id &&
                item.planRevisionId === plan.id &&
                item.purpose === "parent-synthesize" &&
                item.state === "succeeded" &&
                item.outputSha256 === sha256Text(input.parentFinalAnswer ?? ""),
            ));
        if (
          plan === undefined ||
          !plan.validated ||
          (plan.decision !== "answer_directly" && plan.decision !== "finish") ||
          !proposalProven ||
          !synthesisProven
        ) {
          throw Object.assign(new Error("desktop_team_success_closure_open"), {
            code: "desktop_team_success_closure_open",
          });
        }
      }
      const next = { ...run, state: input.state };
      const index = runs.indexOf(run);
      runs[index] = next;
      return { teamRun: next };
    },
    async resolveCollaboration(input) {
      const run = runs.find((item) => item.id === input.teamRunId);
      if (run === undefined) {
        throw Object.assign(new Error("desktop_team_run_not_found"), { code: "desktop_team_run_not_found" });
      }
      if (run.state !== "preparing" && run.state !== "running") {
        throw Object.assign(new Error("desktop_team_run_terminal"), { code: "desktop_team_run_terminal" });
      }
      const requiresAssignment =
        input.parentDecision === "accept_start" || input.parentDecision === "merge_existing";
      if (requiresAssignment !== (input.resolvedAssignmentId !== null)) {
        throw Object.assign(new Error("desktop_native_input_invalid"), {
          code: "desktop_native_input_invalid",
        });
      }
      const known = reports.some((item) =>
        item.collaborationRequests.some(
          (_, requestIndex) =>
            `teamcollab_${item.assignmentId}_${requestIndex}` === input.requestId,
        ),
      );
      if (requiresAssignment && input.resolvedAssignmentId !== null) {
        const targetRole = reports
          .flatMap((item) =>
            item.collaborationRequests.map((request, requestIndex) => ({
              id: `teamcollab_${item.assignmentId}_${requestIndex}`,
              targetRoleId: request.targetRoleId,
            })),
          )
          .find((entry) => entry.id === input.requestId)?.targetRoleId;
        const bound = assignments.get(input.resolvedAssignmentId);
        if (
          targetRole === undefined ||
          bound === undefined ||
          bound.employeeRoleId !== targetRole
        ) {
          throw Object.assign(new Error("desktop_team_collaboration_identity_mismatch"), {
            code: "desktop_team_collaboration_identity_mismatch",
          });
        }
      }
      const existing = collaborationDecisions.get(input.requestId);
      if (existing !== undefined) {
        if (
          existing.parentDecision !== input.parentDecision ||
          existing.resolvedAssignmentId !== input.resolvedAssignmentId
        ) {
          throw Object.assign(new Error("desktop_team_collaboration_resolve_conflict"), {
            code: "desktop_team_collaboration_resolve_conflict",
          });
        }
        return;
      }
      if (!known) {
        throw Object.assign(new Error("desktop_team_collaboration_not_found"), {
          code: "desktop_team_collaboration_not_found",
        });
      }
      collaborationDecisions.set(input.requestId, {
        parentDecision: input.parentDecision,
        resolvedAssignmentId: input.resolvedAssignmentId,
      });
    },
    async createNode(input) {
      const run = runs.find((item) => item.id === input.teamRunId);
      if (run === undefined) {
        throw Object.assign(new Error("desktop_team_run_not_found"), { code: "desktop_team_run_not_found" });
      }
      if (run.state !== "preparing" && run.state !== "running") {
        throw Object.assign(new Error("desktop_team_run_terminal"), { code: "desktop_team_run_terminal" });
      }
      if (nodes.some((item) => item.invocationId === input.invocationId)) {
        throw Object.assign(new Error("desktop_team_duplicate_invocation"), {
          code: "desktop_team_duplicate_invocation",
        });
      }
      if (nodes.some((item) => item.nodeEpoch === input.nodeEpoch || item.sendEpoch === input.sendEpoch)) {
        throw Object.assign(new Error("desktop_team_epoch_reused"), { code: "desktop_team_epoch_reused" });
      }
      const node = {
        id: `teamnode_${randomBytes(16).toString("hex")}`,
        invocationId: input.invocationId,
        assignmentId: input.assignmentId,
        nodeEpoch: input.nodeEpoch,
        sendEpoch: input.sendEpoch,
        state: "running",
      };
      nodes.push(node);
      assignmentStates.set(input.assignmentId, "running");
      return { node: { id: node.id, ordinal: nodes.length, invocationId: node.invocationId } };
    },
    async updateNode(input) {
      if (input.state === ("succeeded" as string)) {
        throw Object.assign(new Error("desktop_team_success_requires_settle"), {
          code: "desktop_team_success_requires_settle",
        });
      }
      const node = nodes.find((item) => item.id === input.nodeId);
      if (node && node.state !== "running") {
        throw Object.assign(new Error("desktop_team_node_terminal"), { code: "desktop_team_node_terminal" });
      }
      if (node) {
        node.state = input.state;
        assignmentStates.set(
          node.assignmentId,
          input.state === "cancelled" ? "cancelled" : "blocked",
        );
      }
    },
    async settleNode(input) {
      const node = nodes.find((item) => item.id === input.nodeId);
      if (node && node.state !== "running") {
        throw Object.assign(new Error("desktop_team_node_terminal"), { code: "desktop_team_node_terminal" });
      }
      const blob = JSON.stringify(input.report);
      if (/api[_-]?key|\bsk-[A-Za-z0-9]{8,}|ciphertext|\bnonce\b|encrypted_secret_blob/iu.test(blob)) {
        throw Object.assign(new Error("desktop_team_secret_or_path_forbidden"), {
          code: "desktop_team_secret_or_path_forbidden",
        });
      }
      if (host.failNextSettle !== null) {
        const code = host.failNextSettle;
        host.failNextSettle = null;
        throw Object.assign(new Error(code), { code });
      }
      reports.push(input.report);
      audits.push(`team_node_settled:${input.nodeId}:${input.invocationId}`);
      if (node) {
        node.state = "succeeded";
        assignmentStates.set(node.assignmentId, input.report.status);
      }
    },
    async recordReport(input) {
      const node = nodes.find((item) => item.id === input.nodeId);
      if (node === undefined || node.state !== "succeeded") {
        throw Object.assign(new Error("desktop_team_report_requires_settle"), {
          code: "desktop_team_report_requires_settle",
        });
      }
      const blob = JSON.stringify(input.report);
      if (/api[_-]?key|\bsk-[A-Za-z0-9]{8,}|ciphertext|\bnonce\b|encrypted_secret_blob/iu.test(blob)) {
        throw Object.assign(new Error("desktop_team_secret_or_path_forbidden"), {
          code: "desktop_team_secret_or_path_forbidden",
        });
      }
      reports.push(input.report);
    },
    async resolveCredentials(_workspaceId, _roleId, signal) {
      if (signal.aborted) {
        throw Object.assign(new Error("desktop_invocation_cancelled"), {
          code: "desktop_invocation_cancelled",
        });
      }
      return options.credentials;
    },
  };
  return host;
}
