import assert from "node:assert/strict";
import test from "node:test";

import type { DesktopNativeClient } from "../src/runtime/native-client.ts";
import { createNativePersonalTeamHost } from "../src/runtime/personal-team-native-host.ts";
import type { DesktopSafeStorage } from "../src/runtime/secret-vault.ts";
import type {
  TeamParentCallRecord,
  TeamProviderCallPurpose,
} from "../src/runtime/personal-team-coordinator.ts";
import type { DesktopTeamRun } from "../src/shared/personal-team.ts";

const WORKSPACE_ID = `workspace_${"a".repeat(32)}`;
const TEAM_RUN_ID = `teamrun_${"b".repeat(32)}`;
const INVOCATION_ID = `invocation_${"c".repeat(32)}`;
const PROVIDER_ID = `provider_${"d".repeat(32)}`;
const PLAN_REVISION_ID = `teamrev_${"e".repeat(32)}`;

function teamRun(): DesktopTeamRun {
  return {
    id: TEAM_RUN_ID,
    workspaceId: WORKSPACE_ID,
    conversationId: `conversation_${"f".repeat(32)}`,
    mode: "team",
    state: "running",
    staffingAuthority: "parent_proposal",
    currentPlanRevisionId: null,
    currentWaveId: null,
    dispatchedParticipantCount: null,
    maximumProviderCalls: 8,
    maximumWallTimeMs: 60_000,
    maximumConcurrentCalls: 2,
    maximumInputCharacters: 100_000,
    maximumOutputCharacters: 100_000,
    consumedProviderCalls: 1,
    task: "native host delegation",
    allowedSpecialistRoleIds: ["frontend"],
    createdAt: "2026-08-24T00:00:00Z",
    updatedAt: "2026-08-24T00:00:01Z",
  };
}

function parentCall(
  purpose: Exclude<TeamProviderCallPurpose, "employee">,
  state: TeamParentCallRecord["state"],
): TeamParentCallRecord {
  const succeeded = state === "succeeded";
  return {
    invocationId: INVOCATION_ID,
    teamRunId: TEAM_RUN_ID,
    planRevisionId: succeeded ? PLAN_REVISION_ID : null,
    purpose,
    state,
    providerId: PROVIDER_ID,
    requestedModel: "loopback-team",
    actualModel: succeeded ? "loopback-team" : null,
    inputTokens: succeeded ? 11 : null,
    outputTokens: succeeded ? 7 : null,
    totalTokens: succeeded ? 18 : null,
    outputSha256: succeeded ? "1".repeat(64) : null,
    errorCode: null,
    createdAt: "2026-08-24T00:00:00Z",
    updatedAt: "2026-08-24T00:00:01Z",
  };
}

test("native personal team host delegates consume and settle without adding vault material", async () => {
  const calls: unknown[] = [];
  const pending = parentCall("parent-propose", "pending");
  const settled = parentCall("parent-propose", "succeeded");
  const client = {
    consumeTeamProviderCall: async (input: unknown) => {
      calls.push(input);
      return { ok: true as const, value: { teamRun: teamRun(), parentCall: pending } };
    },
    settleTeamParentCall: async (input: unknown) => {
      calls.push(input);
      return { ok: true as const, value: { parentCall: settled } };
    },
  } as unknown as DesktopNativeClient;
  const vault = {
    isEncryptionAvailable: () => true,
  } as unknown as DesktopSafeStorage;
  const host = createNativePersonalTeamHost({ client, vault });

  const consumed = await host.consumeProviderCall({
    workspaceId: WORKSPACE_ID,
    teamRunId: TEAM_RUN_ID,
    invocationId: INVOCATION_ID,
    purpose: "parent-propose",
    providerId: PROVIDER_ID,
    requestedModel: "loopback-team",
  });
  const settlement = {
    workspaceId: WORKSPACE_ID,
    teamRunId: TEAM_RUN_ID,
    invocationId: INVOCATION_ID,
    purpose: "parent-propose" as const,
    providerId: PROVIDER_ID,
    requestedModel: "loopback-team",
    state: "succeeded" as const,
    planRevisionId: PLAN_REVISION_ID,
    actualModel: "loopback-team",
    inputTokens: 11,
    outputTokens: 7,
    totalTokens: 18,
    outputSha256: "1".repeat(64),
    errorCode: null,
  };
  const result = await host.settleParentCall(settlement);

  assert.equal(consumed.parentCall?.state, "pending");
  assert.equal(result.parentCall.state, "succeeded");
  assert.deepEqual(calls[1], settlement);
  const serialized = JSON.stringify(calls);
  assert.equal(serialized.includes("secret"), false);
  assert.equal(serialized.includes("encrypted"), false);
  assert.equal(serialized.includes("vault"), false);
});
