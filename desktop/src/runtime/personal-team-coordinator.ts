import { createHash, randomBytes } from "node:crypto";

import {
  SPECIALIST_EMPLOYEE_IDS,
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
  }): Promise<{ readonly teamRun: DesktopTeamRun }>;
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
    readonly state: "succeeded" | "failed" | "cancelled" | "unknown";
    readonly actualModel: string | null;
    readonly inputTokens: number | null;
    readonly outputTokens: number | null;
    readonly totalTokens: number | null;
    readonly answerSha256: string | null;
    readonly errorCode: string | null;
    readonly durationMs: number | null;
  }): Promise<void>;
  recordReport(input: {
    readonly workspaceId: string;
    readonly teamRunId: string;
    readonly nodeId: string;
    readonly invocationId: string;
    readonly report: EmployeeTeamReport;
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
  readonly purpose: "parent-propose" | "employee" | "parent-replan" | "parent-synthesize";
  readonly roleId: PersonalEmployeeId;
  readonly invocationId: string;
  readonly nodeId: string | null;
  readonly assignmentId: string | null;
}

const ABORT_CODES = new Set(["desktop_invocation_cancelled"]);

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

export class TeamAbortRegistry {
  #controllers = new Map<string, AbortController>();
  #pending = false;

  arm(key: string): AbortController {
    this.#controllers.get(key)?.abort();
    const controller = new AbortController();
    if (this.#pending) controller.abort();
    this.#controllers.set(key, controller);
    return controller;
  }

  release(key: string, controller: AbortController): void {
    if (this.#controllers.get(key) === controller) this.#controllers.delete(key);
  }

  abortAll(): boolean {
    const keys = [...this.#controllers.keys()];
    for (const controller of this.#controllers.values()) controller.abort();
    this.#pending = keys.length === 0 ? true : this.#pending;
    if (keys.length === 0) return this.#pending;
    return true;
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

export function eventMatchesTeamIdentity(
  current: {
    readonly workspaceId: string;
    readonly conversationId: string;
    readonly teamRunId: string;
    readonly rosterEpoch: number;
    readonly waveId?: string | null;
    readonly nodeId?: string | null;
    readonly sendEpoch?: number | null;
    readonly invocationId?: string | null;
  },
  event: DesktopTeamRunEvent,
): boolean {
  if (event.workspaceId !== current.workspaceId) return false;
  if (event.conversationId !== undefined && event.conversationId !== current.conversationId) {
    return false;
  }
  if (event.teamRunId !== current.teamRunId) return false;
  if (event.rosterEpoch !== undefined && event.rosterEpoch !== current.rosterEpoch) return false;
  if (current.waveId && event.waveId !== undefined && event.waveId !== current.waveId) return false;
  if (current.nodeId && event.nodeId !== undefined && event.nodeId !== current.nodeId) return false;
  if (
    current.sendEpoch !== undefined &&
    current.sendEpoch !== null &&
    event.sendEpoch !== undefined &&
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
  return true;
}

export class PersonalTeamCoordinator {
  readonly #host: PersonalTeamHost;
  readonly #transport: TeamChatTransport;
  readonly #now: () => number;
  readonly #newId: (prefix: string) => string;
  readonly abort = new TeamAbortRegistry();
  #cancelled = false;
  #live = false;

  constructor(options: PersonalTeamCoordinatorOptions) {
    this.#host = options.host;
    this.#transport = options.transport;
    this.#now = options.now ?? Date.now;
    this.#newId = options.newId ?? defaultNewId;
  }

  get live(): boolean {
    return this.#live;
  }

  requestStop(): void {
    this.#cancelled = true;
    this.abort.abortAll();
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
    const budgetCheck = validateTeamRunBudget(input.budget);
    if (!budgetCheck.ok) {
      throw Object.assign(new Error(budgetCheck.code), { code: budgetCheck.code });
    }
    this.#live = true;
    this.#cancelled = false;
    const started = this.#now();
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
      const identity = {
        workspaceId: input.workspaceId,
        conversationId: input.conversationId,
        teamRunId: teamRun.id,
        rosterEpoch: input.rosterEpoch,
      };
      const emitBound = (event: Omit<DesktopTeamRunEvent, "teamRunId" | "workspaceId" | "rosterEpoch" | "conversationId">) => {
        emit({
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
      const submitted = await this.#host.submitProposal({
        workspaceId: input.workspaceId,
        teamRunId: teamRun.id,
        proposal: (validated.ok ? validated.value : parsed) as ParentTeamDecision,
      });
      emitBound({
        type: "proposal",
        planRevisionId: submitted.planRevision.id,
        state: submitted.teamRun.state,
      });
      if (!submitted.accepted || !validated.ok) {
        await this.#host.setRunState({
          workspaceId: input.workspaceId,
          teamRunId: teamRun.id,
          state: "failed",
        });
        emitBound({
          type: "failed",
          state: "failed",
          errorCode: submitted.validationErrorCode ?? "desktop_team_proposal_invalid",
        });
        return this.#proof(teamRun.id, "failed", calls, nodes, parentFinal, false);
      }

      const decision = validated.value;
      if (decision.decision === "answer_directly") {
        parentFinal = decision.answer;
        await this.#host.setRunState({
          workspaceId: input.workspaceId,
          teamRunId: teamRun.id,
          state: "succeeded",
          parentFinalAnswer: parentFinal,
        });
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
        if (this.#now() - started > input.budget.maximumWallTimeMs) {
          return this.#budgetProof(input, teamRun.id, calls, nodes, parentFinal, emitBound);
        }
        const wave = pendingWaves.shift();
        if (wave === undefined) break;
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
        const replanCall = await this.#invokeParent({
          input,
          teamRun,
          purpose: "parent-replan",
          messages: this.#parentReplanMessages(input, reports, assignments),
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
        );
        const replanSubmitted = await this.#host.submitProposal({
          workspaceId: input.workspaceId,
          teamRunId: teamRun.id,
          proposal: (replanValidated.ok ? replanValidated.value : replanParsed) as ParentReplanDecision,
        });
        lastPlan = replanSubmitted.planRevision;
        emitBound({
          type: "proposal",
          planRevisionId: lastPlan.id,
          state: replanSubmitted.teamRun.state,
        });
        if (!replanSubmitted.accepted || !replanValidated.ok) {
          await this.#host.setRunState({
            workspaceId: input.workspaceId,
            teamRunId: teamRun.id,
            state: "failed",
          });
          emitBound({
            type: "failed",
            state: "failed",
            errorCode: replanSubmitted.validationErrorCode ?? "desktop_team_proposal_invalid",
          });
          return this.#proof(teamRun.id, "failed", calls, nodes, parentFinal, false);
        }
        const replan = replanValidated.value;
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
          await this.#host.setRunState({
            workspaceId: input.workspaceId,
            teamRunId: teamRun.id,
            state: "cannot_complete",
          });
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
      parentFinal = synthesis.text;
      await this.#host.setRunState({
        workspaceId: input.workspaceId,
        teamRunId: teamRun.id,
        state: "succeeded",
        parentFinalAnswer: parentFinal,
      });
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
        await this.#host.setRunState({
          workspaceId: input.workspaceId,
          teamRunId: teamRun.id,
          state: "budget_exhausted",
        }).catch(() => undefined);
        return this.#proof(teamRun.id, "budget_exhausted", calls, nodes, parentFinal, synthesizing);
      }
      if (teamRun !== null) {
        await this.#host.setRunState({
          workspaceId: input.workspaceId,
          teamRunId: teamRun.id,
          state: this.#cancelled ? "cancelled" : "failed",
        }).catch(() => undefined);
      }
      throw error;
    } finally {
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
      if (this.#now() - args.started > args.input.budget.maximumWallTimeMs) {
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
    const stored = args.assignments.get(args.assignment.assignmentId);
    if (stored) stored.effectiveExecution = args.effectiveExecution;
    const invocationId = this.#newId("invocation");
    const nodeEpoch = args.nodes.length + 1;
    const sendEpoch = args.calls.length + 1;
    const key = invocationId;
    const controller = this.abort.arm(key);
    try {
      if (controller.signal.aborted || this.#cancelled) return { kind: "cancelled" };
      const consumed = await raceAbort(
        this.#host.consumeProviderCall({
          workspaceId: args.input.workspaceId,
          teamRunId: args.teamRun.id,
        }),
        controller.signal,
      );
      if (isAborted(consumed) || controller.signal.aborted) return { kind: "cancelled" };
      const credentials = await raceAbort(
        this.#host.resolveCredentials(
          args.input.workspaceId,
          args.assignment.employeeRoleId,
          controller.signal,
        ),
        controller.signal,
      );
      if (isAborted(credentials) || controller.signal.aborted) return { kind: "cancelled" };
      const created = await raceAbort(
        this.#host.createNode({
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
        }),
        controller.signal,
      );
      if (isAborted(created) || controller.signal.aborted) return { kind: "cancelled" };
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
      args.calls.push({
        purpose: "employee",
        roleId: args.assignment.employeeRoleId,
        invocationId,
        nodeId: created.node.id,
        assignmentId: args.assignment.assignmentId,
      });
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
      const started = this.#now();
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
          controller.signal,
        ),
        controller.signal,
      );
      if (isAborted(chat) || controller.signal.aborted || this.#cancelled) {
        node.state = "cancelled";
        await this.#host.updateNode({
          workspaceId: args.input.workspaceId,
          teamRunId: args.teamRun.id,
          nodeId: created.node.id,
          state: "cancelled",
          actualModel: null,
          inputTokens: null,
          outputTokens: null,
          totalTokens: null,
          answerSha256: null,
          errorCode: "desktop_invocation_cancelled",
          durationMs: this.#now() - started,
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
          errorCode: "desktop_invocation_cancelled",
        });
        return { kind: "cancelled" };
      }
      const durationMs = this.#now() - started;
      assertRequestedModelIdentity(credentials.model, chat.actualModel);
      const report = this.#parseEmployeeReport(chat, args.assignment);
      node.durationMs = durationMs;
      node.inputTokens = chat.inputTokens;
      node.outputTokens = chat.outputTokens;
      node.totalTokens = chat.totalTokens;
      node.state = "succeeded";
      node.report = report;
      args.reports.push(report);
      if (stored) stored.state = report.status === "completed" ? "completed" : report.status;
      await this.#host.updateNode({
        workspaceId: args.input.workspaceId,
        teamRunId: args.teamRun.id,
        nodeId: created.node.id,
        state: "succeeded",
        actualModel: chat.actualModel,
        inputTokens: chat.inputTokens,
        outputTokens: chat.outputTokens,
        totalTokens: chat.totalTokens,
        answerSha256: sha256Text(chat.text),
        errorCode: null,
        durationMs,
      });
      await this.#host.recordReport({
        workspaceId: args.input.workspaceId,
        teamRunId: args.teamRun.id,
        nodeId: created.node.id,
        invocationId,
        report,
      });
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
      if (ABORT_CODES.has(code) || this.#cancelled) return { kind: "cancelled" };
      if (
        code === "desktop_invocation_interrupted" ||
        code === "desktop_provider_stream_incomplete" ||
        code === "desktop_provider_response_invalid"
      ) {
        return { kind: "unknown", code };
      }
      if (code === "desktop_team_call_budget_exceeded") return { kind: "budget", code };
      return { kind: "failed", code };
    } finally {
      this.abort.release(key, controller);
    }
  }

  async #invokeParent(args: {
    readonly input: DesktopTeamRunExecuteInput;
    readonly teamRun: DesktopTeamRun;
    readonly purpose: "parent-propose" | "parent-replan" | "parent-synthesize";
    readonly messages: readonly TeamChatMessage[];
    readonly emit: (event: Omit<DesktopTeamRunEvent, "teamRunId" | "workspaceId" | "rosterEpoch" | "conversationId">) => void;
    readonly calls: TeamProviderCallRecord[];
    readonly started: number;
  }): Promise<
    | { kind: "ok"; text: string; result: TeamChatResult }
    | { kind: "cancelled" | "unknown" | "failed" | "budget"; code?: string }
  > {
    if (this.#cancelled) return { kind: "cancelled" };
    if (this.#now() - args.started > args.input.budget.maximumWallTimeMs) return { kind: "budget" };
    const invocationId = this.#newId("invocation");
    const controller = this.abort.arm(invocationId);
    try {
      if (controller.signal.aborted) return { kind: "cancelled" };
      const consumed = await raceAbort(
        this.#host.consumeProviderCall({
          workspaceId: args.input.workspaceId,
          teamRunId: args.teamRun.id,
        }),
        controller.signal,
      );
      if (isAborted(consumed) || controller.signal.aborted) return { kind: "cancelled" };
      const credentials = await raceAbort(
        this.#host.resolveCredentials(args.input.workspaceId, "parent", controller.signal),
        controller.signal,
      );
      if (isAborted(credentials) || controller.signal.aborted) return { kind: "cancelled" };
      args.calls.push({
        purpose: args.purpose,
        roleId: "parent",
        invocationId,
        nodeId: null,
        assignmentId: null,
      });
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
          controller.signal,
        ),
        controller.signal,
      );
      if (isAborted(chat) || controller.signal.aborted || this.#cancelled) {
        return { kind: "cancelled" };
      }
      assertRequestedModelIdentity(credentials.model, chat.actualModel);
      if (args.purpose === "parent-synthesize" && this.#cancelled) {
        return { kind: "cancelled" };
      }
      args.emit({
        type: "node_delta",
        employeeRoleId: "parent",
        invocationId,
        sendEpoch: args.calls.length,
        text: chat.text,
      });
      return { kind: "ok", text: chat.text, result: chat };
    } catch (error) {
      const code = errorCode(error);
      if (ABORT_CODES.has(code) || this.#cancelled) return { kind: "cancelled" };
      if (
        code === "desktop_invocation_interrupted" ||
        code === "desktop_provider_stream_incomplete" ||
        code === "desktop_provider_response_invalid"
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
  ): readonly TeamChatMessage[] {
    return [
      {
        role: "system",
        content:
          "[omnibase-team-role:parent-replan]\nOutput ONLY JSON ParentReplanDecision: continue | request_followup | finish | cannot_complete. Collaboration requests must be decided by you; employees cannot launch peers. New assignment IDs required for reinvoke.",
      },
      {
        role: "user",
        content: JSON.stringify({
          objective: input.task,
          reports,
          pendingCollaboration: reports.flatMap((item) => item.collaborationRequests),
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
    if (result.kind === "cancelled") {
      await this.#host.setRunState({
        workspaceId: input.workspaceId,
        teamRunId,
        state: "cancelled",
      }).catch(() => undefined);
      emit({ type: "cancelled", state: "cancelled" });
      return this.#proof(teamRunId, "cancelled", calls, nodes, synthesizing ? null : parentFinal, false);
    }
    if (result.kind === "budget") {
      await this.#host.setRunState({
        workspaceId: input.workspaceId,
        teamRunId,
        state: "budget_exhausted",
      }).catch(() => undefined);
      emit({ type: "budget_exhausted", state: "budget_exhausted" });
      return this.#proof(teamRunId, "budget_exhausted", calls, nodes, parentFinal, false);
    }
    if (result.kind === "unknown") {
      await this.#host.setRunState({
        workspaceId: input.workspaceId,
        teamRunId,
        state: "unknown",
      }).catch(() => undefined);
      emit({ type: "unknown", state: "unknown" });
      return this.#proof(teamRunId, "unknown", calls, nodes, parentFinal, false);
    }
    await this.#host.setRunState({
      workspaceId: input.workspaceId,
      teamRunId,
      state: "failed",
    }).catch(() => undefined);
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
    await this.#host.setRunState({
      workspaceId: input.workspaceId,
      teamRunId,
      state: "budget_exhausted",
    }).catch(() => undefined);
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
    }).catch(() => undefined);
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
  readonly nodes: { id: string; invocationId: string; assignmentId: string }[];
  readonly reports: EmployeeTeamReport[];
} {
  const runs: DesktopTeamRun[] = [];
  const nodes: { id: string; invocationId: string; assignmentId: string }[] = [];
  const reports: EmployeeTeamReport[] = [];
  const assignments = new Map<string, TeamAssignmentProposal>();
  let revision = 0;
  const allowed = new Set<string>(SPECIALIST_EMPLOYEE_IDS);

  const host: PersonalTeamHost & {
    readonly runs: DesktopTeamRun[];
    readonly nodes: { id: string; invocationId: string; assignmentId: string }[];
    readonly reports: EmployeeTeamReport[];
  } = {
    runs,
    nodes,
    reports,
    async startTeamRun(input) {
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
      if (validated.ok && validated.value.decision === "delegate") {
        for (const wave of validated.value.waves) {
          for (const assignment of wave.assignments) assignments.set(assignment.assignmentId, assignment);
        }
      }
      if (validated.ok && validated.value.decision === "continue") {
        for (const assignment of validated.value.nextWave.assignments) {
          assignments.set(assignment.assignmentId, assignment);
        }
      }
      if (validated.ok && validated.value.decision === "request_followup") {
        for (const assignment of validated.value.assignments) {
          assignments.set(assignment.assignmentId, assignment);
        }
      }
      return {
        accepted: validated.ok,
        validationErrorCode: validated.ok ? null : validated.code,
        teamRun: run,
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
            state: "pending",
            waveId: "wave",
            dependsOnAssignmentIds: item.dependsOnAssignmentIds,
            expectedOutput: item.expectedOutput,
          })),
          reports,
          collaborationRequests: reports.flatMap((item) =>
            item.collaborationRequests.map((request) => ({
              fromAssignmentId: item.assignmentId,
              fromEmployeeRoleId: item.employeeRoleId,
              targetRoleId: request.targetRoleId,
              question: request.question,
              reason: request.reason,
              parentDecision: "pending" as const,
              resolvedAssignmentId: null,
            })),
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
      const next = { ...run, consumedProviderCalls: run.consumedProviderCalls + 1 };
      const index = runs.indexOf(run);
      runs[index] = next;
      return { teamRun: next };
    },
    async setRunState(input) {
      const run = runs.find((item) => item.id === input.teamRunId);
      if (run === undefined) {
        throw Object.assign(new Error("desktop_team_run_not_found"), { code: "desktop_team_run_not_found" });
      }
      const next = { ...run, state: input.state };
      const index = runs.indexOf(run);
      runs[index] = next;
      return { teamRun: next };
    },
    async createNode(input) {
      if (nodes.some((item) => item.invocationId === input.invocationId)) {
        throw Object.assign(new Error("desktop_team_duplicate_invocation"), {
          code: "desktop_team_duplicate_invocation",
        });
      }
      const node = {
        id: `teamnode_${randomBytes(16).toString("hex")}`,
        invocationId: input.invocationId,
        assignmentId: input.assignmentId,
      };
      nodes.push(node);
      return { node: { id: node.id, ordinal: nodes.length, invocationId: node.invocationId } };
    },
    async updateNode() {
      return;
    },
    async recordReport(input) {
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
