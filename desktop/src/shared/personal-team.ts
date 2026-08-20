export const SPECIALIST_EMPLOYEE_IDS = Object.freeze([
  "product",
  "ux",
  "frontend",
  "backend",
  "data",
  "security",
  "qa",
  "operations",
  "docs",
] as const);

export type SpecialistEmployeeId = (typeof SPECIALIST_EMPLOYEE_IDS)[number];
export type PersonalEmployeeId = SpecialistEmployeeId | "parent";

export const PERSONAL_EMPLOYEE_IDS = Object.freeze([
  "parent",
  ...SPECIALIST_EMPLOYEE_IDS,
] as const);

export type TeamWaveExecution = "serial" | "parallel";

export interface TeamAssignmentProposal {
  readonly assignmentId: string;
  readonly employeeRoleId: SpecialistEmployeeId;
  readonly objective: string;
  readonly dependsOnAssignmentIds: readonly string[];
  readonly expectedOutput: string;
  readonly contextRequirements: readonly string[];
}

export interface TeamWaveProposal {
  readonly waveId: string;
  readonly execution: TeamWaveExecution;
  readonly assignments: readonly TeamAssignmentProposal[];
}

export type ParentTeamDecision =
  | {
      readonly decision: "answer_directly";
      readonly answer: string;
      readonly reason: string;
    }
  | {
      readonly decision: "delegate";
      readonly objective: string;
      readonly waves: readonly TeamWaveProposal[];
      readonly finalSynthesisRequired: true;
    };

export type ParentReplanDecision =
  | {
      readonly decision: "continue";
      readonly nextWave: TeamWaveProposal;
    }
  | {
      readonly decision: "request_followup";
      readonly assignments: readonly TeamAssignmentProposal[];
    }
  | {
      readonly decision: "finish";
      readonly reason: string;
    }
  | {
      readonly decision: "cannot_complete";
      readonly reason: string;
    };

export interface EmployeeCollaborationRequest {
  readonly targetRoleId: SpecialistEmployeeId;
  readonly question: string;
  readonly reason: string;
}

export interface EmployeeTeamReport {
  readonly assignmentId: string;
  readonly employeeRoleId: SpecialistEmployeeId;
  readonly status: "completed" | "needs_collaboration" | "blocked";
  readonly report: string;
  readonly collaborationRequests: readonly EmployeeCollaborationRequest[];
}

export interface TeamRunBudget {
  readonly maximumProviderCalls: number;
  readonly maximumWallTimeMs: number;
  readonly maximumConcurrentCalls: number;
  readonly maximumInputCharacters: number;
  readonly maximumOutputCharacters: number;
}

export type TeamRunState =
  | "preparing"
  | "running"
  | "cancelling"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "unknown"
  | "budget_exhausted"
  | "cannot_complete";

export interface DesktopAgentRole {
  readonly id: PersonalEmployeeId;
  readonly displayName: string;
  readonly responsibility: string;
  readonly defaultState: "active" | "dormant";
  readonly mayJoinTeam: boolean;
  readonly providerId: string | null;
  readonly modelNameOverride: string | null;
  readonly gear: string;
  readonly thinkingDepth: string;
  readonly rowVersion: number;
  readonly verificationState: "unverified" | "binding_recorded" | "stale";
  readonly verifiedActualModel: string | null;
  readonly inheritedProvider: boolean;
  readonly resolvedProviderId: string | null;
  readonly resolvedModelName: string | null;
  readonly secretFingerprint: string | null;
  readonly hasSecret: boolean;
}

export interface DesktopAgentRoleList {
  readonly items: readonly DesktopAgentRole[];
}

export interface DesktopAgentRoleUpdateInput {
  readonly workspaceId: string;
  readonly roleId: PersonalEmployeeId;
  readonly providerId: string | null;
  readonly modelNameOverride: string | null;
  readonly gear: "economy" | "standard" | "deep" | "audit";
  readonly thinkingDepth: "disabled" | "low" | "medium" | "high";
}

export interface DesktopAgentRoleIdInput {
  readonly workspaceId: string;
  readonly roleId: PersonalEmployeeId;
}

export interface DesktopAgentRoleTestResult {
  readonly ok: true;
  readonly roleId: PersonalEmployeeId;
  readonly workspaceId: string;
  readonly providerId: string;
  readonly inheritedProvider: boolean;
  readonly requestedModel: string;
  readonly secretFingerprint: string;
  readonly verificationDigest: string;
  readonly identityProven: false;
}

export interface DesktopTeamRun {
  readonly id: string;
  readonly workspaceId: string;
  readonly conversationId: string;
  readonly mode: "single" | "team";
  readonly state: TeamRunState;
  readonly staffingAuthority: "parent_proposal";
  readonly currentPlanRevisionId: string | null;
  readonly currentWaveId: string | null;
  readonly dispatchedParticipantCount: number | null;
  readonly maximumProviderCalls: number;
  readonly maximumWallTimeMs: number;
  readonly maximumConcurrentCalls: number;
  readonly maximumInputCharacters: number;
  readonly maximumOutputCharacters: number;
  readonly consumedProviderCalls: number;
  readonly task: string;
  readonly allowedSpecialistRoleIds: readonly SpecialistEmployeeId[];
  readonly createdAt: string;
  readonly updatedAt: string;
}

export interface DesktopTeamPlanRevision {
  readonly id: string;
  readonly revisionOrdinal: number;
  readonly decision: string;
  readonly proposalJsonSha256: string;
  readonly validated: boolean;
  readonly validationErrorCode: string | null;
  readonly createdAt: string;
}

export interface DesktopTeamRunStartInput {
  readonly workspaceId: string;
  readonly conversationId: string;
  readonly task: string;
  readonly teamMode: true;
  readonly budget: TeamRunBudget;
  readonly allowedSpecialistRoleIds?: readonly SpecialistEmployeeId[];
}

export interface DesktopTeamRunIdInput {
  readonly workspaceId: string;
  readonly teamRunId: string;
}

export interface DesktopTeamRunSubmitProposalInput {
  readonly workspaceId: string;
  readonly teamRunId: string;
  readonly proposal: ParentTeamDecision | ParentReplanDecision;
}

export interface DesktopTeamRunProposalResult {
  readonly accepted: boolean;
  readonly validationErrorCode: string | null;
  readonly teamRun: DesktopTeamRun;
  readonly planRevision: DesktopTeamPlanRevision;
}

export interface DesktopTeamCollaborationInput {
  readonly workspaceId: string;
  readonly teamRunId: string;
  readonly fromAssignmentId: string;
  readonly fromEmployeeRoleId: SpecialistEmployeeId;
  readonly targetRoleId: SpecialistEmployeeId;
  readonly question: string;
  readonly reason: string;
}

export interface DesktopTeamCollaborationRequest {
  readonly id?: string;
  readonly fromAssignmentId: string;
  readonly fromEmployeeRoleId: SpecialistEmployeeId;
  readonly targetRoleId: SpecialistEmployeeId;
  readonly question: string;
  readonly reason: string;
  readonly parentDecision: "pending" | "accept_start" | "handle_self" | "merge_existing" | "decline";
  readonly resolvedAssignmentId: string | null;
}

export interface PersonalTeamBlackboard {
  readonly teamRunId: string;
  readonly workspaceId: string;
  readonly ownerObjective: string;
  readonly currentPlanRevisionId: string | null;
  readonly assignments: readonly {
    readonly assignmentId: string;
    readonly employeeRoleId: SpecialistEmployeeId;
    readonly objective: string;
    readonly state: string;
    readonly waveId: string;
    readonly dependsOnAssignmentIds: readonly string[];
    readonly expectedOutput: string;
  }[];
  readonly reports: readonly EmployeeTeamReport[];
  readonly collaborationRequests: readonly DesktopTeamCollaborationRequest[];
}

export interface DesktopTeamRunEvent {
  readonly type: "snapshot" | "cancelled" | "proposal" | "blackboard";
  readonly teamRunId: string;
  readonly workspaceId: string;
  readonly state?: TeamRunState;
}
