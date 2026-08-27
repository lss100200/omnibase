import {
  SPECIALIST_EMPLOYEE_IDS,
  type ParentCollaborationDecision,
  type ParentReplanDecision,
  type ParentTeamDecision,
  type SpecialistEmployeeId,
  type TeamAssignmentProposal,
  type TeamRunBudget,
  type TeamWaveProposal,
} from "../shared/personal-team.ts";

export interface TeamValidateOk<T> {
  readonly ok: true;
  readonly value: T;
}

export interface TeamValidateFail {
  readonly ok: false;
  readonly code: string;
}

export type TeamValidateResult<T> = TeamValidateOk<T> | TeamValidateFail;

const SPECIALIST = new Set<string>(SPECIALIST_EMPLOYEE_IDS);
const ASSIGNMENT_ID = /^[A-Za-z][A-Za-z0-9._-]{0,127}$/u;
const WAVE_ID = /^[A-Za-z][A-Za-z0-9._-]{0,127}$/u;
const CONTROL = /[\u0000-\u001f\u007f]/u;
const SECRET =
  /api[_-]?key|\bsecret\b|\bbearer\s+\S+|\bsk-[A-Za-z0-9]{8,}|ciphertext|\bnonce\b|dpapi|vault[_-]?handle|encrypted_secret_blob|native[_-]?control[_-]?token/iu;
const PATH = /[A-Za-z]:\\|\\\\[^\\\s]+\\|\/(?:etc|home|root|usr|var|tmp)\/|file:\/\//iu;
const LOCATOR = /\bpostgres(?:ql)?:\/\/|\bmongodb:\/\//iu;
const WORKSPACE = /workspace_[0-9a-f]{32}/gu;
const FORBIDDEN_KEYS = new Set([
  "tools",
  "tool",
  "tool_choice",
  "toolChoice",
  "mcp",
  "shell",
  "sandbox",
  "side_effects",
  "sideEffects",
  "functions",
  "function_call",
  "functionCall",
  "plugins",
  "skills",
  "dispatch",
  "direct_launch",
  "directLaunch",
  "launch_employee",
  "launchEmployee",
  "api_key",
  "apiKey",
  "ciphertext",
  "nonce",
  "dpapi",
  "vault",
  "vault_handle",
  "vaultHandle",
  "encrypted_secret_blob",
  "encryptedSecretBlob",
  "credential_reference",
  "credentialReference",
  "secret",
  "password",
]);
const INFINITE_REPLAN_KEYS = new Set([
  "replanCap",
  "replan_cap",
  "unlimitedReplan",
  "unlimited_replan",
  "infiniteReplan",
  "infinite_replan",
]);
const BUDGET_BOUNDS: Readonly<Record<keyof TeamRunBudget, readonly [number, number]>> =
  Object.freeze({
    maximumProviderCalls: [1, 128],
    maximumWallTimeMs: [1_000, 3_600_000],
    maximumConcurrentCalls: [1, 9],
    maximumInputCharacters: [1, 131_072],
    maximumOutputCharacters: [1, 131_072],
  });
const MAX_WAVES = 16;
const MAX_ASSIGNMENTS = 128;
const MAX_PLAN_REVISIONS = 32;

function fail(code: string): TeamValidateFail {
  return { ok: false, code };
}

function boundText(value: unknown, maximum: number): string | null {
  if (typeof value !== "string" || CONTROL.test(value)) return null;
  const normalized = value.trim();
  if (normalized.length === 0 || normalized.length > maximum) return null;
  return normalized;
}

function walkForbidden(value: unknown): string | null {
  if (Array.isArray(value)) {
    for (const child of value) {
      const found = walkForbidden(child);
      if (found !== null) return found;
    }
    return null;
  }
  if (typeof value !== "object" || value === null) return null;
  for (const [key, child] of Object.entries(value)) {
    if (FORBIDDEN_KEYS.has(key)) {
      if (
        key === "dispatch" ||
        key === "direct_launch" ||
        key === "directLaunch" ||
        key === "launch_employee" ||
        key === "launchEmployee"
      ) {
        return "desktop_team_employee_direct_launch";
      }
      if (
        key === "tools" ||
        key === "tool" ||
        key === "tool_choice" ||
        key === "toolChoice" ||
        key === "mcp" ||
        key === "shell" ||
        key === "sandbox" ||
        key === "side_effects" ||
        key === "sideEffects" ||
        key === "functions" ||
        key === "function_call" ||
        key === "functionCall" ||
        key === "plugins" ||
        key === "skills"
      ) {
        return "desktop_team_tools_forbidden";
      }
      return "desktop_team_secret_or_path_forbidden";
    }
    const nested = walkForbidden(child);
    if (nested !== null) return nested;
  }
  return null;
}

function scanText(value: string, workspaceId: string | null): string | null {
  if (SECRET.test(value) || PATH.test(value) || LOCATOR.test(value)) {
    return "desktop_team_secret_or_path_forbidden";
  }
  if (workspaceId !== null) {
    for (const match of value.match(WORKSPACE) ?? []) {
      if (match !== workspaceId) return "desktop_team_cross_workspace";
    }
  }
  return null;
}

function walkSensitive(value: unknown, workspaceId: string | null): string | null {
  if (typeof value === "string") return scanText(value, workspaceId);
  if (Array.isArray(value)) {
    for (const child of value) {
      const found = walkSensitive(child, workspaceId);
      if (found !== null) return found;
    }
    return null;
  }
  if (typeof value === "object" && value !== null) {
    for (const child of Object.values(value)) {
      const found = walkSensitive(child, workspaceId);
      if (found !== null) return found;
    }
  }
  return null;
}

function hasCycle(graph: ReadonlyMap<string, readonly string[]>): boolean {
  const visiting = new Set<string>();
  const visited = new Set<string>();
  const visit = (node: string): boolean => {
    if (visited.has(node)) return false;
    if (visiting.has(node)) return true;
    visiting.add(node);
    for (const dependency of graph.get(node) ?? []) {
      if (visit(dependency)) return true;
    }
    visiting.delete(node);
    visited.add(node);
    return false;
  };
  for (const node of graph.keys()) {
    if (visit(node)) return true;
  }
  return false;
}

export function validateTeamRunBudget(budget: unknown): TeamValidateResult<TeamRunBudget> {
  if (typeof budget !== "object" || budget === null) {
    return fail("desktop_team_infinite_budget");
  }
  const record = budget as Record<string, unknown>;
  const expected = Object.keys(BUDGET_BOUNDS).sort();
  const actual = Object.keys(record).sort();
  if (expected.join(",") !== actual.join(",")) return fail("desktop_team_infinite_budget");
  const normalized = {} as Record<keyof TeamRunBudget, number>;
  for (const key of Object.keys(BUDGET_BOUNDS) as (keyof TeamRunBudget)[]) {
    const value = record[key];
    const bounds = BUDGET_BOUNDS[key];
    if (typeof value !== "number" || !Number.isInteger(value) || value < bounds[0] || value > bounds[1]) {
      return fail("desktop_team_infinite_budget");
    }
    normalized[key] = value;
  }
  return { ok: true, value: Object.freeze(normalized) as TeamRunBudget };
}

function validateAssignment(
  assignment: unknown,
  budget: TeamRunBudget,
  allowed: ReadonlySet<string>,
  workspaceId: string | null,
): TeamValidateResult<TeamAssignmentProposal> {
  if (typeof assignment !== "object" || assignment === null) {
    return fail("desktop_team_proposal_invalid");
  }
  const record = assignment as Record<string, unknown>;
  const expected = [
    "assignmentId",
    "employeeRoleId",
    "objective",
    "dependsOnAssignmentIds",
    "expectedOutput",
    "contextRequirements",
  ];
  if (Object.keys(record).sort().join(",") !== expected.sort().join(",")) {
    const extra = new Set(Object.keys(record));
    for (const key of expected) extra.delete(key);
    if (extra.has("dispatch") || extra.has("directLaunch") || extra.has("direct_launch")) {
      return fail("desktop_team_employee_direct_launch");
    }
    if ([...extra].some((key) => FORBIDDEN_KEYS.has(key))) {
      return fail(
        extra.has("tools") || extra.has("mcp")
          ? "desktop_team_tools_forbidden"
          : "desktop_team_secret_or_path_forbidden",
      );
    }
    return fail("desktop_team_proposal_invalid");
  }
  const assignmentId = record.assignmentId;
  const roleId = record.employeeRoleId;
  if (typeof assignmentId !== "string" || !ASSIGNMENT_ID.test(assignmentId)) {
    return fail("desktop_team_proposal_invalid");
  }
  if (roleId === "parent") return fail("desktop_team_parent_not_specialist");
  if (typeof roleId !== "string" || !SPECIALIST.has(roleId) || !allowed.has(roleId)) {
    return fail("desktop_team_unknown_role");
  }
  const objective = boundText(record.objective, budget.maximumInputCharacters);
  const expectedOutput = boundText(record.expectedOutput, budget.maximumOutputCharacters);
  if (objective === null) return fail("desktop_team_input_budget_exceeded");
  if (expectedOutput === null) return fail("desktop_team_output_budget_exceeded");
  if (!Array.isArray(record.dependsOnAssignmentIds) || !Array.isArray(record.contextRequirements)) {
    return fail("desktop_team_proposal_invalid");
  }
  if (
    !record.dependsOnAssignmentIds.every(
      (item) => typeof item === "string" && ASSIGNMENT_ID.test(item),
    )
  ) {
    return fail("desktop_team_missing_dependency");
  }
  const payload: TeamAssignmentProposal = Object.freeze({
    assignmentId,
    employeeRoleId: roleId as SpecialistEmployeeId,
    objective,
    dependsOnAssignmentIds: Object.freeze([...record.dependsOnAssignmentIds] as string[]),
    expectedOutput,
    contextRequirements: Object.freeze(
      record.contextRequirements.filter((item): item is string => typeof item === "string"),
    ),
  });
  const sensitive = walkSensitive(payload, workspaceId);
  if (sensitive !== null) return fail(sensitive);
  return { ok: true, value: payload };
}

function validateWave(
  wave: unknown,
  budget: TeamRunBudget,
  allowed: ReadonlySet<string>,
  workspaceId: string | null,
): TeamValidateResult<TeamWaveProposal> {
  if (typeof wave !== "object" || wave === null) return fail("desktop_team_proposal_invalid");
  const record = wave as Record<string, unknown>;
  if (
    Object.keys(record).sort().join(",") !==
    ["assignments", "execution", "waveId"].sort().join(",")
  ) {
    return fail("desktop_team_proposal_invalid");
  }
  if (typeof record.waveId !== "string" || !WAVE_ID.test(record.waveId)) {
    return fail("desktop_team_proposal_invalid");
  }
  if (record.execution !== "serial" && record.execution !== "parallel") {
    return fail("desktop_team_proposal_invalid");
  }
  if (!Array.isArray(record.assignments) || record.assignments.length === 0) {
    return fail("desktop_team_proposal_invalid");
  }
  if (record.assignments.length > MAX_ASSIGNMENTS) return fail("desktop_team_proposal_invalid");
  const assignments: TeamAssignmentProposal[] = [];
  for (const item of record.assignments) {
    const result = validateAssignment(item, budget, allowed, workspaceId);
    if (!result.ok) return result;
    assignments.push(result.value);
  }
  return {
    ok: true,
    value: Object.freeze({
      waveId: record.waveId,
      execution: record.execution,
      assignments: Object.freeze(assignments),
    }),
  };
}

function checkGraph(
  waves: readonly TeamWaveProposal[],
  known: ReadonlySet<string>,
): TeamValidateFail | null {
  const graph = new Map<string, readonly string[]>();
  const seen = new Set(known);
  for (const wave of waves) {
    const waveIds = wave.assignments.map((item) => item.assignmentId);
    if (new Set(waveIds).size !== waveIds.length || waveIds.some((id) => seen.has(id))) {
      return fail("desktop_team_duplicate_assignment_id");
    }
    for (const assignment of wave.assignments) {
      const missing = assignment.dependsOnAssignmentIds.filter(
        (item) => !seen.has(item) && !waveIds.includes(item),
      );
      if (missing.length > 0) return fail("desktop_team_missing_dependency");
      graph.set(assignment.assignmentId, assignment.dependsOnAssignmentIds);
      seen.add(assignment.assignmentId);
    }
  }
  if (hasCycle(graph)) return fail("desktop_team_dependency_cycle");
  return null;
}

export function validateParentTeamDecision(
  proposal: unknown,
  budget: TeamRunBudget,
  allowed: ReadonlySet<string>,
  workspaceId: string | null,
): TeamValidateResult<ParentTeamDecision> {
  const forbidden = walkForbidden(proposal);
  if (forbidden !== null) return fail(forbidden);
  const sensitive = walkSensitive(proposal, workspaceId);
  if (sensitive !== null) return fail(sensitive);
  if (typeof proposal !== "object" || proposal === null || !("decision" in proposal)) {
    return fail("desktop_team_proposal_invalid");
  }
  const record = proposal as Record<string, unknown>;
  if (typeof record.sourceRoleId === "string" && SPECIALIST.has(record.sourceRoleId)) {
    return fail("desktop_team_employee_direct_launch");
  }
  if (record.decision === "answer_directly") {
    if (Object.keys(record).sort().join(",") !== ["answer", "decision", "reason"].sort().join(",")) {
      return fail("desktop_team_proposal_invalid");
    }
    const answer = boundText(record.answer, budget.maximumOutputCharacters);
    const reason = boundText(record.reason, budget.maximumInputCharacters);
    if (answer === null) return fail("desktop_team_output_budget_exceeded");
    if (reason === null) return fail("desktop_team_input_budget_exceeded");
    return {
      ok: true,
      value: Object.freeze({ decision: "answer_directly", answer, reason }),
    };
  }
  if (record.decision !== "delegate") return fail("desktop_team_proposal_invalid");
  if (
    Object.keys(record).sort().join(",") !==
    ["decision", "finalSynthesisRequired", "objective", "waves"].sort().join(",")
  ) {
    return fail("desktop_team_proposal_invalid");
  }
  if (record.finalSynthesisRequired !== true) return fail("desktop_team_proposal_invalid");
  const objective = boundText(record.objective, budget.maximumInputCharacters);
  if (objective === null) return fail("desktop_team_input_budget_exceeded");
  if (!Array.isArray(record.waves) || record.waves.length === 0 || record.waves.length > MAX_WAVES) {
    return fail("desktop_team_proposal_invalid");
  }
  const waves: TeamWaveProposal[] = [];
  for (const wave of record.waves) {
    const result = validateWave(wave, budget, allowed, workspaceId);
    if (!result.ok) return result;
    waves.push(result.value);
  }
  const graphError = checkGraph(waves, new Set());
  if (graphError !== null) return graphError;
  const assignmentCount = waves.reduce((sum, wave) => sum + wave.assignments.length, 0);
  if (assignmentCount > budget.maximumProviderCalls) {
    return fail("desktop_team_call_budget_exceeded");
  }
  return {
    ok: true,
    value: Object.freeze({
      decision: "delegate",
      objective,
      waves: Object.freeze(waves),
      finalSynthesisRequired: true as const,
    }),
  };
}

export type PendingCollaboration = {
  readonly id: string;
  readonly targetRoleId: string;
};

function hasReplanKeys(
  record: Record<string, unknown>,
  base: readonly string[],
): boolean {
  const allowed = new Set<string>([...base, "collaborationDecisions"]);
  return base.every((key) => key in record) && Object.keys(record).every((key) => allowed.has(key));
}

function validateCollaborationDecisions(
  record: Record<string, unknown>,
  pending: readonly PendingCollaboration[],
  newAssignments: readonly TeamAssignmentProposal[],
  knownAssignmentIds: ReadonlySet<string>,
): TeamValidateResult<readonly ParentCollaborationDecision[] | null> {
  const raw = record.collaborationDecisions;
  if (pending.length === 0) {
    if (raw === undefined) return { ok: true, value: null };
    return fail("desktop_team_proposal_invalid");
  }
  if (!Array.isArray(raw)) return fail("desktop_team_collaboration_undecided");
  const pendingIds = new Set(pending.map((item) => item.id));
  const decided = new Set<string>();
  const normalized: ParentCollaborationDecision[] = [];
  for (const item of raw) {
    if (typeof item !== "object" || item === null) return fail("desktop_team_proposal_invalid");
    const entry = item as Record<string, unknown>;
    const keys = Object.keys(entry);
    if (
      !keys.every((key) => key === "requestId" || key === "decision" || key === "resolvedAssignmentId")
    ) {
      return fail("desktop_team_proposal_invalid");
    }
    if (typeof entry.requestId !== "string" || typeof entry.decision !== "string") {
      return fail("desktop_team_proposal_invalid");
    }
    const requestId = entry.requestId;
    const decision = entry.decision;
    const resolved = entry.resolvedAssignmentId;
    if (!pendingIds.has(requestId) || decided.has(requestId)) {
      return fail("desktop_team_proposal_invalid");
    }
    if (decision === "accept_start" || decision === "merge_existing") {
      if (typeof resolved !== "string") return fail("desktop_team_proposal_invalid");
    } else if (decision === "handle_self" || decision === "decline") {
      if (resolved !== undefined && resolved !== null) {
        return fail("desktop_team_proposal_invalid");
      }
    } else {
      return fail("desktop_team_proposal_invalid");
    }
    if (decision === "accept_start") {
      const target = pending.find((item) => item.id === requestId)?.targetRoleId ?? null;
      const match =
        target === null
          ? undefined
          : newAssignments.find(
              (assignment) => assignment.assignmentId === resolved && assignment.employeeRoleId === target,
            );
      if (match === undefined) return fail("desktop_team_collaboration_identity_mismatch");
    }
    if (decision === "merge_existing" && typeof resolved === "string") {
      if (!knownAssignmentIds.has(resolved)) {
        return fail("desktop_team_collaboration_identity_mismatch");
      }
    }
    decided.add(requestId);
    normalized.push(
      Object.freeze({
        requestId,
        decision,
        ...(resolved === undefined || resolved === null ? {} : { resolvedAssignmentId: resolved }),
      }),
    );
  }
  if (decided.size !== pendingIds.size || [...pendingIds].some((id) => !decided.has(id))) {
    return fail("desktop_team_collaboration_undecided");
  }
  return { ok: true, value: Object.freeze(normalized) };
}

export function validateParentReplanDecision(
  proposal: unknown,
  budget: TeamRunBudget,
  allowed: ReadonlySet<string>,
  workspaceId: string | null,
  knownAssignmentIds: ReadonlySet<string>,
  revisionOrdinal: number,
  pendingCollaborations: readonly PendingCollaboration[] = [],
): TeamValidateResult<ParentReplanDecision> {
  if (typeof proposal === "object" && proposal !== null) {
    if (Object.keys(proposal).some((key) => INFINITE_REPLAN_KEYS.has(key))) {
      return fail("desktop_team_infinite_replan");
    }
  }
  if (revisionOrdinal > MAX_PLAN_REVISIONS) return fail("desktop_team_infinite_replan");
  const forbidden = walkForbidden(proposal);
  if (forbidden !== null) return fail(forbidden);
  const sensitive = walkSensitive(proposal, workspaceId);
  if (sensitive !== null) return fail(sensitive);
  if (typeof proposal !== "object" || proposal === null || !("decision" in proposal)) {
    return fail("desktop_team_proposal_invalid");
  }
  const record = proposal as Record<string, unknown>;
  if (record.decision === "finish" || record.decision === "cannot_complete") {
    if (!hasReplanKeys(record, ["decision", "reason"])) {
      return fail("desktop_team_proposal_invalid");
    }
    const decisions = validateCollaborationDecisions(
      record,
      pendingCollaborations,
      [],
      knownAssignmentIds,
    );
    if (!decisions.ok) return decisions;
    const reason = boundText(record.reason, budget.maximumInputCharacters);
    if (reason === null) return fail("desktop_team_input_budget_exceeded");
    return {
      ok: true,
      value: Object.freeze({
        decision: record.decision,
        reason,
        ...(decisions.value === null ? {} : { collaborationDecisions: decisions.value }),
      }),
    };
  }
  if (record.decision === "continue") {
    if (!hasReplanKeys(record, ["decision", "nextWave"])) {
      return fail("desktop_team_proposal_invalid");
    }
    const wave = validateWave(record.nextWave, budget, allowed, workspaceId);
    if (!wave.ok) return wave;
    const decisions = validateCollaborationDecisions(
      record,
      pendingCollaborations,
      wave.value.assignments,
      knownAssignmentIds,
    );
    if (!decisions.ok) return decisions;
    const graphError = checkGraph([wave.value], knownAssignmentIds);
    if (graphError !== null) return graphError;
    if (knownAssignmentIds.size + wave.value.assignments.length > budget.maximumProviderCalls) {
      return fail("desktop_team_call_budget_exceeded");
    }
    return {
      ok: true,
      value: Object.freeze({
        decision: "continue",
        nextWave: wave.value,
        ...(decisions.value === null ? {} : { collaborationDecisions: decisions.value }),
      }),
    };
  }
  if (record.decision !== "request_followup") return fail("desktop_team_proposal_invalid");
  if (!hasReplanKeys(record, ["decision", "assignments"])) {
    return fail("desktop_team_proposal_invalid");
  }
  if (!Array.isArray(record.assignments) || record.assignments.length === 0) {
    return fail("desktop_team_proposal_invalid");
  }
  const assignments: TeamAssignmentProposal[] = [];
  const seen = new Set<string>();
  for (const item of record.assignments) {
    const result = validateAssignment(item, budget, allowed, workspaceId);
    if (!result.ok) return result;
    if (seen.has(result.value.assignmentId) || knownAssignmentIds.has(result.value.assignmentId)) {
      return fail("desktop_team_duplicate_assignment_id");
    }
    if (
      result.value.dependsOnAssignmentIds.some(
        (dependency) => !knownAssignmentIds.has(dependency) && !seen.has(dependency),
      )
    ) {
      return fail("desktop_team_missing_dependency");
    }
    seen.add(result.value.assignmentId);
    assignments.push(result.value);
  }
  const decisions = validateCollaborationDecisions(
    record,
    pendingCollaborations,
    assignments,
    knownAssignmentIds,
  );
  if (!decisions.ok) return decisions;
  if (knownAssignmentIds.size + assignments.length > budget.maximumProviderCalls) {
    return fail("desktop_team_call_budget_exceeded");
  }
  return {
    ok: true,
    value: Object.freeze({
      decision: "request_followup",
      assignments: Object.freeze(assignments),
      ...(decisions.value === null ? {} : { collaborationDecisions: decisions.value }),
    }),
  };
}

export function extractJsonObject(text: string): unknown | null {
  const start = text.indexOf("{");
  const end = text.lastIndexOf("}");
  if (start < 0 || end <= start) return null;
  try {
    return JSON.parse(text.slice(start, end + 1)) as unknown;
  } catch {
    return null;
  }
}
