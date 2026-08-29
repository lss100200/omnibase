import assert from "node:assert/strict";
import test from "node:test";

import type { IpcMainInvokeEvent } from "electron";

import { registerClosedIpcHandlers, type IpcMainLike } from "../src/ipc.ts";
import { DESKTOP_UI_ORIGIN } from "../src/security/origin-policy.ts";
import {
  IPC_CHANNEL_SET,
  IPC_CHANNELS,
  type RuntimeStatus,
} from "../src/shared/ipc-contract.ts";

const unused = async () => ({
  ok: false as const,
  error: { code: "must-not-run" },
});

const productStubs = {
  getWorkspaceAgent: unused,
  getApplicationPreference: unused,
  updateApplicationPreference: unused,
  getWorkspaceComposition: unused,
  proposeWorkspaceComposition: unused,
  proposeWorkspaceCompositionFromAssistant: unused,
  proposeWorkspaceCompositionRollback: unused,
  decideWorkspaceComposition: unused,
  authorizeWorkspaceFiles: unused,
  releaseWorkspaceFiles: unused,
  listWorkspaceFiles: unused,
  readWorkspaceFile: unused,
  listProviders: async () => ({ ok: true as const, value: { items: [] } }),
  upsertProvider: unused,
  deleteProvider: unused,
  testProvider: unused,
  listConversations: unused,
  createConversation: unused,
  archiveConversation: unused,
  getConversation: unused,
  sendConversation: unused,
  cancelConversation: unused,
  abortInFlightSend: unused,
  listAgentRoles: unused,
  getAgentRole: unused,
  updateAgentRole: unused,
  testAgentRole: unused,
  startTeamRun: unused,
  cancelTeamRun: unused,
  getTeamRun: unused,
  listTeamRuns: unused,
  submitTeamProposal: unused,
  getTeamBlackboard: unused,
  recordTeamCollaboration: unused,
  executeTeamRun: unused,
  appendTeamRunBudget: unused,
};

function register(): Map<
  string,
  (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown
> {
  const handlers = new Map<
    string,
    (event: IpcMainInvokeEvent, ...args: unknown[]) => unknown
  >();
  const ipcMain: IpcMainLike = {
    handle: (channel, listener) => {
      handlers.set(channel, listener);
    },
    removeHandler: () => undefined,
  };
  const ready: RuntimeStatus = Object.freeze({
    phase: "ready",
    attempts: 1,
    lastError: null,
  });
  registerClosedIpcHandlers(ipcMain, {
    getVersion: () => "1.0.0",
    getRuntimeStatus: () => ready,
    retryRuntimeStartup: async () => ready,
    getOwnerStatus: unused,
    bootstrapOwner: unused,
    listWorkspaces: unused,
    createWorkspace: unused,
    archiveWorkspace: unused,
    ...productStubs,
  });
  return handlers;
}

const trustedEvent = {
  senderFrame: { url: `${DESKTOP_UI_ORIGIN}/desktop` },
} as IpcMainInvokeEvent;

test("closed IPC catalog includes role and team-run channels and still has send", () => {
  const handlers = register();
  assert.deepEqual(new Set(handlers.keys()), IPC_CHANNEL_SET);
  assert.equal(handlers.has(IPC_CHANNELS.agentsRolesList), true);
  assert.equal(handlers.has(IPC_CHANNELS.agentsRolesGet), true);
  assert.equal(handlers.has(IPC_CHANNELS.agentsRolesUpdate), true);
  assert.equal(handlers.has(IPC_CHANNELS.agentsRolesTest), true);
  assert.equal(handlers.has(IPC_CHANNELS.teamRunsStart), true);
  assert.equal(handlers.has(IPC_CHANNELS.teamRunsCancel), true);
  assert.equal(handlers.has(IPC_CHANNELS.teamRunsGet), true);
  assert.equal(handlers.has(IPC_CHANNELS.teamRunsList), true);
  assert.equal(handlers.has(IPC_CHANNELS.teamRunsSubmitProposal), true);
  assert.equal(handlers.has(IPC_CHANNELS.teamRunsGetBlackboard), true);
  assert.equal(handlers.has(IPC_CHANNELS.teamRunsRecordCollaboration), true);
  assert.equal(handlers.has(IPC_CHANNELS.teamRunsExecute), true);
  assert.equal(handlers.has(IPC_CHANNELS.teamRunsAppendBudget), true);
  assert.equal(handlers.has(IPC_CHANNELS.conversationSend), true);
});

test("IPC rejects unknown role, infinite budget, and employee dispatch envelopes", async () => {
  const handlers = register();
  const workspaceId = `workspace_${"b".repeat(32)}`;
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.agentsRolesGet)?.(trustedEvent, {
      workspaceId,
      roleId: "super-agent",
    }),
    { ok: false, error: { code: "desktop_native_input_invalid" } },
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.teamRunsStart)?.(trustedEvent, {
      workspaceId,
      conversationId: `conversation_${"c".repeat(32)}`,
      task: "review",
      teamMode: true,
      budget: {
        maximumProviderCalls: 0,
        maximumWallTimeMs: 600000,
        maximumConcurrentCalls: 2,
        maximumInputCharacters: 16384,
        maximumOutputCharacters: 32768,
      },
    }),
    { ok: false, error: { code: "desktop_native_input_invalid" } },
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.teamRunsRecordCollaboration)?.(
      trustedEvent,
      {
        workspaceId,
        teamRunId: `teamrun_${"d".repeat(32)}`,
        fromAssignmentId: "security-review",
        fromEmployeeRoleId: "security",
        targetRoleId: "qa",
        question: "design the matrix",
        reason: "need coverage",
        directLaunch: true,
      },
    ),
    { ok: false, error: { code: "desktop_native_input_invalid" } },
  );
});

test("IPC rejects missing, malformed, and tampered node/report identity fields", async () => {
  const handlers = register();
  const workspaceId = `workspace_${"b".repeat(32)}`;
  const envelope = {
    workspaceId,
    teamRunId: `teamrun_${"d".repeat(32)}`,
    fromAssignmentId: "security-review",
    fromEmployeeRoleId: "security",
    targetRoleId: "qa",
    question: "design the matrix",
    reason: "need coverage",
    nodeId: `teamnode_${"e".repeat(32)}`,
    reportId: `teamrpt_${"e".repeat(32)}`,
  };
  const rejection = {
    ok: false,
    error: { code: "desktop_native_input_invalid" },
  };

  const { nodeId: _omittedNodeId, ...withoutNodeId } = envelope;
  void _omittedNodeId;
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.teamRunsRecordCollaboration)?.(
      trustedEvent,
      withoutNodeId,
    ),
    rejection,
  );
  const { reportId: _omittedReportId, ...withoutReportId } = envelope;
  void _omittedReportId;
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.teamRunsRecordCollaboration)?.(
      trustedEvent,
      withoutReportId,
    ),
    rejection,
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.teamRunsRecordCollaboration)?.(
      trustedEvent,
      { ...envelope, nodeId: `teamnodes_${"e".repeat(32)}` },
    ),
    rejection,
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.teamRunsRecordCollaboration)?.(
      trustedEvent,
      { ...envelope, nodeId: `teamnode_${"e".repeat(31)}` },
    ),
    rejection,
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.teamRunsRecordCollaboration)?.(
      trustedEvent,
      { ...envelope, nodeId: `teamnode_${"E".repeat(32)}` },
    ),
    rejection,
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.teamRunsRecordCollaboration)?.(
      trustedEvent,
      { ...envelope, reportId: `teamreport_${"e".repeat(32)}` },
    ),
    rejection,
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.teamRunsRecordCollaboration)?.(
      trustedEvent,
      { ...envelope, reportId: `teamrpt_${"e".repeat(33)}` },
    ),
    rejection,
  );
  assert.deepEqual(
    await handlers.get(IPC_CHANNELS.teamRunsRecordCollaboration)?.(
      trustedEvent,
      { ...envelope, reportId: `teamrpt_${"E".repeat(32)}` },
    ),
    rejection,
  );
});
