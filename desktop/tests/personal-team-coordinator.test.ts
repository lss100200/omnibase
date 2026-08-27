import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import http from "node:http";
import { AddressInfo } from "node:net";
import test from "node:test";

import {
  createInMemoryPersonalTeamHost,
  eventMatchesTeamIdentity,
  PersonalTeamCoordinator,
} from "../src/runtime/personal-team-coordinator.ts";
import { createOpenAiCompatibleTransport } from "../src/runtime/personal-team-provider.ts";
import {
  DEFAULT_TEAM_RUN_BUDGET,
  SPECIALIST_EMPLOYEE_IDS,
  type DesktopTeamRunEvent,
  type TeamAssignmentProposal,
  type TeamRunBudget,
} from "../src/shared/personal-team.ts";

const WORKSPACE = `workspace_${"a".repeat(32)}`;
const CONVERSATION = `conversation_${"b".repeat(32)}`;
const OTHER_WORKSPACE = `workspace_${"c".repeat(32)}`;

type Scenario =
  | "answer_directly"
  | "one_specialist"
  | "many"
  | "all_nine"
  | "parallel_pair"
  | "deps"
  | "mid_run_add"
  | "reinvoke"
  | "accept_collab_qa"
  | "collab_undecided"
  | "finish_early"
  | "unknown_role"
  | "dup_assignment"
  | "missing_dep"
  | "cycle_dep"
  | "tools"
  | "cross_workspace"
  | "employee_launch"
  | "secret_collab"
  | "hang"
  | "hang_serial"
  | "partial_fail"
  | "parallel_unknown"
  | "empty_collab"
  | "synthesis_whitespace";

function assignment(
  id: string,
  role: string,
  depends: string[] = [],
): TeamAssignmentProposal {
  return {
    assignmentId: id,
    employeeRoleId: role as TeamAssignmentProposal["employeeRoleId"],
    objective: `subtask for ${role}`,
    dependsOnAssignmentIds: depends,
    expectedOutput: "report",
    contextRequirements: depends,
  };
}

function parentDelegate(scenario: Scenario): string {
  const waves = () => {
    switch (scenario) {
      case "one_specialist":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [assignment("frontend-review", "frontend")],
          },
        ];
      case "many":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [
              assignment("product-scope", "product"),
              assignment("frontend-review", "frontend"),
              assignment("backend-review", "backend"),
            ],
          },
        ];
      case "all_nine":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: SPECIALIST_EMPLOYEE_IDS.map((role) =>
              assignment(`${role}-pass`, role),
            ),
          },
        ];
      case "parallel_pair":
      case "deps":
      case "accept_collab_qa":
      case "collab_undecided":
      case "mid_run_add":
      case "reinvoke":
      case "hang":
        return [
          {
            waveId: "wave-1",
            execution: "parallel",
            assignments: [
              assignment("frontend-review", "frontend"),
              assignment("backend-review", "backend"),
            ],
          },
        ];
      case "finish_early":
      case "empty_collab":
      case "synthesis_whitespace":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [assignment("frontend-review", "frontend")],
          },
        ];
      case "unknown_role":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [assignment("pentest", "network-pentest")],
          },
        ];
      case "dup_assignment":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [
              assignment("frontend-review", "frontend"),
              assignment("frontend-review", "backend"),
            ],
          },
        ];
      case "missing_dep":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [assignment("security-review", "security", ["missing"])],
          },
        ];
      case "cycle_dep":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [
              assignment("a-review", "frontend", ["b-review"]),
              assignment("b-review", "backend", ["a-review"]),
            ],
          },
        ];
      case "tools":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [
              {
                ...assignment("frontend-review", "frontend"),
                tools: [{ name: "shell" }],
              } as unknown as TeamAssignmentProposal,
            ],
          },
        ];
      case "cross_workspace":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [
              assignment(
                "frontend-review",
                "frontend",
              ),
            ],
          },
        ];
      case "employee_launch":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [
              {
                ...assignment("frontend-review", "frontend"),
                directLaunch: true,
              } as unknown as TeamAssignmentProposal,
            ],
          },
        ];
      case "secret_collab":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [assignment("security-review", "security")],
          },
        ];
      case "hang_serial":
        return [
          {
            waveId: "wave-1",
            execution: "serial",
            assignments: [
              assignment("frontend-review", "frontend"),
              assignment("backend-review", "backend"),
            ],
          },
        ];
      case "partial_fail":
      case "parallel_unknown":
        return [
          {
            waveId: "wave-1",
            execution: "parallel",
            assignments: [
              assignment("frontend-review", "frontend"),
              assignment("backend-review", "backend"),
            ],
          },
        ];
      default:
        return [];
    }
  };
  if (scenario === "answer_directly") {
    return JSON.stringify({
      decision: "answer_directly",
      answer: "No specialists required.",
      reason: "The question is a clarification.",
    });
  }
  const objective =
    scenario === "cross_workspace"
      ? `use ${OTHER_WORKSPACE} files`
      : `review the desktop team design for ${scenario}`;
  return JSON.stringify({
    decision: "delegate",
    objective,
    waves: waves(),
    finalSynthesisRequired: true,
  });
}

function employeeReport(role: string, assignmentId: string, scenario: Scenario): string {
  if (scenario === "empty_collab") {
    return JSON.stringify({
      assignmentId,
      employeeRoleId: role,
      status: "needs_collaboration",
      report: "Collaboration is required but no valid request was supplied.",
      collaborationRequests: [],
    });
  }
  if (scenario === "secret_collab" && role === "security") {
    return JSON.stringify({
      assignmentId,
      employeeRoleId: role,
      status: "needs_collaboration",
      report: "Need a second look.",
      collaborationRequests: [
        {
          targetRoleId: "qa",
          question: "Please use api_key sk-live-not-real",
          reason: "verify cancel",
        },
      ],
    });
  }
  const collab =
    (scenario === "accept_collab_qa" || scenario === "collab_undecided") &&
    role === "security"
      ? [
          {
            targetRoleId: "qa",
            question: "Please design the Stop and restart attack matrix.",
            reason: "Need recovery evidence.",
          },
        ]
      : [];
  return JSON.stringify({
    assignmentId,
    employeeRoleId: role,
    status: collab.length > 0 ? "needs_collaboration" : "completed",
    report: `${role} completed ${assignmentId}`,
    collaborationRequests: collab,
  });
}

function parentReplan(scenario: Scenario, known: string[]): string {
  if (scenario === "finish_early") {
    return JSON.stringify({ decision: "finish", reason: "First report is enough." });
  }
  if (scenario === "deps" && !known.includes("security-review")) {
    return JSON.stringify({
      decision: "continue",
      nextWave: {
        waveId: "wave-2",
        execution: "serial",
        assignments: [assignment("security-review", "security", ["frontend-review", "backend-review"])],
      },
    });
  }
  if (scenario === "mid_run_add" && !known.includes("docs-pass")) {
    return JSON.stringify({
      decision: "request_followup",
      assignments: [assignment("docs-pass", "docs", ["frontend-review"])],
    });
  }
  if (scenario === "reinvoke" && !known.includes("frontend-followup")) {
    return JSON.stringify({
      decision: "request_followup",
      assignments: [assignment("frontend-followup", "frontend", ["frontend-review"])],
    });
  }
  if (
    (scenario === "accept_collab_qa" || scenario === "collab_undecided") &&
    known.includes("frontend-review") &&
    !known.includes("security-review")
  ) {
    return JSON.stringify({
      decision: "continue",
      nextWave: {
        waveId: "wave-2",
        execution: "serial",
        assignments: [assignment("security-review", "security", ["frontend-review", "backend-review"])],
      },
    });
  }
  if (
    (scenario === "accept_collab_qa" || scenario === "collab_undecided") &&
    !known.includes("qa-matrix")
  ) {
    const nextWave = {
      waveId: "wave-qa",
      execution: "serial",
      assignments: [assignment("qa-matrix", "qa", ["security-review"])],
    };
    if (scenario === "collab_undecided") {
      return JSON.stringify({ decision: "continue", nextWave });
    }
    return JSON.stringify({
      decision: "continue",
      nextWave,
      collaborationDecisions: [
        {
          requestId: "teamcollab_security-review_0",
          decision: "accept_start",
          resolvedAssignmentId: "qa-matrix",
        },
      ],
    });
  }
  return JSON.stringify({ decision: "finish", reason: "Staffing is complete." });
}

function parseMessages(body: string): { roleStamp: string; user: string } {
  const parsed = JSON.parse(body) as { messages?: { role: string; content: string }[] };
  const system = parsed.messages?.find((item) => item.role === "system")?.content ?? "";
  const user = parsed.messages?.find((item) => item.role === "user")?.content ?? "";
  const stamp = /\[omnibase-team-role:([^\]]+)\]/u.exec(system)?.[1] ?? "";
  return { roleStamp: stamp, user };
}

function startFakeOpenAi(calls: { path: string; authorization: string | undefined; body: string }[]) {
  let scenario: Scenario = "answer_directly";
  const delayed = new Map<http.ServerResponse, ReturnType<typeof setTimeout>>();
  const server = http.createServer((req, res) => {
    const chunks: Buffer[] = [];
    req.on("data", (chunk: Buffer) => chunks.push(chunk));
    req.on("end", () => {
      const body = Buffer.concat(chunks).toString("utf8");
      calls.push({
        path: req.url ?? "",
        authorization: req.headers.authorization,
        body,
      });
      const parsed = parseMessages(body);
      const scenarioMatch = /\[p69-scenario:([a-z0-9_]+)\]/u.exec(parsed.user);
      if (scenarioMatch?.[1]) scenario = scenarioMatch[1] as Scenario;
      let content = "ok";
      if (parsed.roleStamp === "parent-propose") {
        content = parentDelegate(scenario);
      } else if (parsed.roleStamp.startsWith("employee:")) {
        const role = parsed.roleStamp.slice("employee:".length);
        let assignmentId = `${role}-pass`;
        try {
          const user = JSON.parse(parsed.user) as { assignmentId?: string };
          if (typeof user.assignmentId === "string") assignmentId = user.assignmentId;
        } catch {
          assignmentId = `${role}-review`;
        }
        content = employeeReport(role, assignmentId, scenario);
        if (scenario === "hang" || (scenario === "hang_serial" && role === "frontend")) {
          const timer = setTimeout(() => {
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(
              JSON.stringify({
                model: "loopback-team",
                choices: [{ message: { content } }],
                usage: { prompt_tokens: 11, completion_tokens: 7, total_tokens: 18 },
              }),
            );
          }, 5_000);
          delayed.set(res, timer);
          req.on("close", () => {
            const pending = delayed.get(res);
            if (pending) clearTimeout(pending);
          });
          return;
        }
        if (scenario === "partial_fail" && role === "backend") {
          res.writeHead(500, { "Content-Type": "application/json" });
          res.end(JSON.stringify({ error: { message: "provider failed" } }));
          return;
        }
        if (scenario === "parallel_unknown" && role === "backend") {
          res.writeHead(200, { "Content-Type": "application/json" });
          res.end("{}");
          return;
        }
      } else if (parsed.roleStamp === "parent-replan") {
        let known: string[] = [];
        try {
          known = (JSON.parse(parsed.user) as { knownAssignmentIds?: string[] }).knownAssignmentIds ?? [];
        } catch {
          known = [];
        }
        content = parentReplan(scenario, known);
      } else if (parsed.roleStamp === "parent-synthesize") {
        content =
          scenario === "synthesis_whitespace"
            ? "   综合结论：父 Agent 已汇总各专业员工报告。   "
            : "综合结论：父 Agent 已汇总各专业员工报告。";
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(
        JSON.stringify({
          model: "loopback-team",
          choices: [{ message: { content } }],
          usage: { prompt_tokens: 11, completion_tokens: 7, total_tokens: 18 },
        }),
      );
    });
  });
  return {
    calls,
    listen(): Promise<{ baseUrl: string; close: () => Promise<void> }> {
      return new Promise((resolve) => {
        server.listen(0, "127.0.0.1", () => {
          const address = server.address() as AddressInfo;
          resolve({
            baseUrl: `http://127.0.0.1:${address.port}/v1`,
            close: () =>
              new Promise((done) => {
                server.close(() => done());
              }),
          });
        });
      });
    },
  };
}

function budget(overrides: Partial<TeamRunBudget> = {}): TeamRunBudget {
  return { ...DEFAULT_TEAM_RUN_BUDGET, maximumProviderCalls: 24, ...overrides };
}

async function runScenario(scenario: Scenario, overrides: Partial<TeamRunBudget> = {}) {
  const recorder = startFakeOpenAi([]);
  const server = await recorder.listen();
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: server.baseUrl,
      secret: "loopback-secret-not-for-git",
      allowLoopbackHttp: true,
      timeoutMs: 5_000,
    },
  });
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  const events: DesktopTeamRunEvent[] = [];
  try {
    const proof = await coordinator.execute(
      {
        workspaceId: WORKSPACE,
        conversationId: CONVERSATION,
        task: `[p69-scenario:${scenario}] review the desktop team design`,
        teamMode: true,
        rosterEpoch: 1,
        budget: budget(overrides),
      },
      (event) => events.push(event),
    );
    return { proof, events, calls: recorder.calls, host, coordinator };
  } finally {
    await server.close();
  }
}

function assertNoSecretLeak(calls: { authorization?: string; body: string }[]): void {
  for (const call of calls) {
    assert.match(call.authorization ?? "", /^Bearer /u);
    assert.equal(call.body.includes("loopback-secret-not-for-git"), false);
  }
}

test("answer_directly uses one parent Provider call and a revision-bound durable proof", async () => {
  const { proof, calls, host } = await runScenario("answer_directly");
  assert.equal(proof.state, "succeeded");
  assert.equal(proof.providerCallCount, 1);
  assert.equal(proof.executedNodeCount, 0);
  assert.equal(proof.parentCallCount, 1);
  assert.equal(proof.uniqueInvocationIds.length, 1);
  assert.equal(proof.hiddenCalls, false);
  assert.equal(calls.length, proof.providerCallCount);
  assertNoSecretLeak(calls);
  assert.equal(host.parentCalls.length, 1);
  const parentCall = host.parentCalls[0]!;
  const revision = host.planRevisions[0]!;
  assert.equal(parentCall.purpose, "parent-propose");
  assert.equal(parentCall.state, "succeeded");
  assert.equal(parentCall.planRevisionId, revision.id);
  assert.equal(parentCall.outputSha256, revision.proposalJsonSha256);
});

test("partial parent usage is normalized to an all-null optional receipt", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async () => ({
        text: parentDelegate("answer_directly"),
        actualModel: "loopback-team",
        inputTokens: 11,
        outputTokens: null,
        totalTokens: 18,
      }),
    },
  });
  const proof = await coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "normalize partial optional Provider usage",
      teamMode: true,
      rosterEpoch: 90,
      budget: budget(),
    },
    () => undefined,
  );
  assert.equal(proof.state, "succeeded");
  assert.equal(host.runs[0]?.state, "succeeded");
  assert.equal(host.parentCalls[0]?.inputTokens, null);
  assert.equal(host.parentCalls[0]?.outputTokens, null);
  assert.equal(host.parentCalls[0]?.totalTokens, null);
  assert.equal(host.parentCalls[0]?.state, "succeeded");
});

test("one specialist is an independent Provider call with unique node identity", async () => {
  const { proof, calls, host } = await runScenario("one_specialist");
  assert.equal(proof.state, "succeeded");
  assert.equal(proof.executedNodeCount, 1);
  assert.equal(proof.uniqueNodeIds.length, 1);
  assert.equal(proof.uniqueInvocationIds.length, proof.providerCallCount);
  assert.equal(proof.parentWasLastWhenSynthesizing, true);
  assert.equal(calls.length, proof.providerCallCount);
  assert.equal(proof.providerCallCount, proof.parentCallCount + proof.executedNodeCount);
  assert.equal(host.runs[0]?.consumedProviderCalls, proof.providerCallCount);
  assert.equal(host.parentCalls.length, proof.parentCallCount);
  assert.equal(host.parentCalls.some((item) => item.state === "pending"), false);
  const finishRevision = host.planRevisions.find((item) => item.decision === "finish");
  assert.ok(finishRevision);
  const replan = host.parentCalls.find((item) => item.purpose === "parent-replan");
  assert.equal(replan?.planRevisionId, finishRevision.id);
  assert.equal(replan?.outputSha256, finishRevision.proposalJsonSha256);
  const synthesis = host.parentCalls.find((item) => item.purpose === "parent-synthesize");
  assert.equal(synthesis?.planRevisionId, finishRevision.id);
  assert.equal(synthesis?.outputSha256, createHash("sha256").update(proof.parentFinalAnswer ?? "", "utf8").digest("hex"));
});

test("synthesis final text is canonical across proof digest, event, and durable answer", async () => {
  const { proof, events, host } = await runScenario("synthesis_whitespace");
  const canonical = "综合结论：父 Agent 已汇总各专业员工报告。";
  assert.equal(proof.state, "succeeded");
  assert.equal(proof.parentFinalAnswer, canonical);
  assert.equal(host.runs[0]?.state, "succeeded");
  const synthesis = host.parentCalls.find((item) => item.purpose === "parent-synthesize");
  assert.equal(
    synthesis?.outputSha256,
    createHash("sha256").update(canonical, "utf8").digest("hex"),
  );
  assert.equal(
    events.some(
      (event) =>
        event.type === "node_delta" &&
        event.employeeRoleId === "parent" &&
        event.text === canonical,
    ),
    true,
  );
  assert.equal(
    events.some(
      (event) =>
        event.type === "completed" && event.parentFinalAnswer === canonical,
    ),
    true,
  );
});

test("a coordinator is one-shot and rejects a second execute with a stable code", async () => {
  const { coordinator } = await runScenario("answer_directly");
  await assert.rejects(
    coordinator.execute(
      {
        workspaceId: WORKSPACE,
        conversationId: CONVERSATION,
        task: "second execute must use a new coordinator",
        teamMode: true,
        rosterEpoch: 100,
        budget: budget(),
      },
      () => undefined,
    ),
    (error: unknown) =>
      typeof error === "object" &&
      error !== null &&
      "code" in error &&
      error.code === "desktop_team_coordinator_already_executed",
  );
  assert.equal(coordinator.requestStop(), false);
});

test("many specialists and all nine keep unique invocation and node IDs", async () => {
  const many = await runScenario("many");
  assert.equal(many.proof.executedNodeCount, 3);
  assert.equal(many.proof.uniqueInvocationIds.length, many.proof.providerCallCount);
  const nine = await runScenario("all_nine");
  assert.equal(nine.proof.executedNodeCount, 9);
  assert.equal(new Set(nine.proof.uniqueNodeIds).size, 9);
  assert.equal(nine.proof.parentWasLastWhenSynthesizing, true);
  assert.equal(nine.calls.length, nine.proof.providerCallCount);
});

test("parallel pair uses two specialist nodes and does not hide Provider calls", async () => {
  const { proof, events, calls } = await runScenario("parallel_pair");
  assert.equal(proof.executedNodeCount, 2);
  assert.equal(calls.length, proof.providerCallCount);
  const nodeStarts = events.filter((item) => item.type === "node_starting");
  assert.equal(nodeStarts.length, 2);
  assert.notEqual(nodeStarts[0]?.invocationId, nodeStarts[1]?.invocationId);
  assert.notEqual(nodeStarts[0]?.nodeId, nodeStarts[1]?.nodeId);
});

test("dependent security wave waits for predecessor reports then parent synthesizes last", async () => {
  const { proof, events } = await runScenario("deps");
  assert.equal(proof.executedNodeCount, 3);
  assert.equal(proof.uniqueAssignmentIds.length, 3);
  const terminals = events.filter((item) => item.type === "node_terminal" && item.employeeRoleId !== "parent");
  const security = terminals.find((item) => item.employeeRoleId === "security");
  const frontend = terminals.find((item) => item.employeeRoleId === "frontend");
  assert.ok(security);
  assert.ok(frontend);
  assert.ok((events.indexOf(security!) ?? 0) > (events.indexOf(frontend!) ?? 0));
  assert.equal(proof.parentWasLastWhenSynthesizing, true);
});

test("mid-run add and same-specialist reinvoke create new assignment/node/invocation", async () => {
  const added = await runScenario("mid_run_add");
  assert.equal(added.proof.executedNodeCount, 3);
  assert.ok(added.proof.uniqueAssignmentIds.includes("docs-pass"));
  const again = await runScenario("reinvoke");
  assert.equal(again.proof.executedNodeCount, 3);
  const frontendNodes = again.host.nodes.filter((item) => item.assignmentId.startsWith("frontend"));
  assert.equal(frontendNodes.length, 2);
  assert.notEqual(frontendNodes[0]?.invocationId, frontendNodes[1]?.invocationId);
  assert.notEqual(frontendNodes[0]?.id, frontendNodes[1]?.id);
  assert.equal(again.host.reports.length, 3);
});

test("parent accepts collaboration and starts QA as a new validated assignment", async () => {
  const { proof, host } = await runScenario("accept_collab_qa");
  assert.ok(proof.uniqueAssignmentIds.includes("qa-matrix"));
  assert.ok(host.reports.some((item) => item.employeeRoleId === "qa"));
  assert.equal(proof.parentWasLastWhenSynthesizing, true);
  assert.equal(proof.state, "succeeded");
  const run = host.runs[0]!;
  const { blackboard } = await host.getBlackboard({
    workspaceId: run.workspaceId,
    teamRunId: run.id,
  });
  assert.ok(blackboard.collaborationRequests.length > 0);
  for (const request of blackboard.collaborationRequests) {
    assert.equal(request.parentDecision, "accept_start");
    assert.equal(request.resolvedAssignmentId, "qa-matrix");
  }
});

test("an undecided collaboration request fails the replan instead of being auto-resolved", async () => {
  const { proof, host } = await runScenario("collab_undecided");
  assert.equal(proof.state, "failed");
  const run = host.runs[0]!;
  const { blackboard } = await host.getBlackboard({
    workspaceId: run.workspaceId,
    teamRunId: run.id,
  });
  assert.ok(blackboard.collaborationRequests.length > 0);
  for (const request of blackboard.collaborationRequests) {
    assert.equal(request.parentDecision, "pending");
    assert.equal(request.resolvedAssignmentId, null);
  }
});

test("a completed journey with no collaboration requests performs no resolves", async () => {
  const { proof, host } = await runScenario("one_specialist");
  assert.equal(proof.state, "succeeded");
  const run = host.runs[0]!;
  const { blackboard } = await host.getBlackboard({
    workspaceId: run.workspaceId,
    teamRunId: run.id,
  });
  assert.equal(blackboard.collaborationRequests.length, 0);
});

test("finish early skips synthesis-only extra specialists and still has unique IDs", async () => {
  const { proof } = await runScenario("finish_early");
  assert.equal(proof.executedNodeCount, 1);
  assert.equal(proof.state, "succeeded");
  assert.equal(proof.uniqueInvocationIds.length, proof.providerCallCount);
});

test("illegal parent proposals fail closed without creating specialist nodes", async () => {
  for (const scenario of [
    "unknown_role",
    "dup_assignment",
    "missing_dep",
    "cycle_dep",
    "tools",
    "cross_workspace",
    "employee_launch",
  ] as const) {
    const { proof, host } = await runScenario(scenario);
    assert.equal(proof.state, "failed", scenario);
    assert.equal(proof.executedNodeCount, 0, scenario);
    assert.equal(host.parentCalls.length, 1, scenario);
    assert.equal(host.parentCalls[0]?.state, "failed", scenario);
    assert.equal(host.parentCalls[0]?.outputSha256, null, scenario);
    assert.equal(host.parentCalls.some((item) => item.state === "pending"), false, scenario);
  }
});

test("needs_collaboration with no valid requests fails closed and settles the node failed", async () => {
  const { proof, host } = await runScenario("empty_collab");
  assert.equal(proof.state, "failed");
  assert.equal(host.reports.length, 0);
  assert.equal(host.nodes.length, 1);
  assert.equal(host.nodes[0]?.state, "failed");
  assert.equal(host.parentCalls.some((item) => item.state === "pending"), false);
});

test("old run reports cannot enter a new run", async () => {
  const first = await runScenario("one_specialist");
  const second = await runScenario("one_specialist");
  assert.notEqual(first.proof.teamRunId, second.proof.teamRunId);
  assert.equal(second.host.reports.every((item) => first.host.reports.includes(item)), false);
  assert.equal(second.host.nodes.some((node) => first.host.nodes.some((old) => old.id === node.id)), false);
});

test("Stop aborts active nodes, skips waiting nodes, and does not synthesize", async () => {
  const recorder = startFakeOpenAi([]);
  const server = await recorder.listen();
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: server.baseUrl,
      secret: "loopback-secret-not-for-git",
      allowLoopbackHttp: true,
      timeoutMs: 5_000,
    },
  });
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  const events: DesktopTeamRunEvent[] = [];
  const running = coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "[p69-scenario:hang] parallel stop",
      teamMode: true,
      rosterEpoch: 7,
      budget: budget({ maximumConcurrentCalls: 2 }),
    },
    (event) => events.push(event),
  );
  await new Promise((resolve) => setTimeout(resolve, 40));
  coordinator.requestStop();
  const proof = await running;
  await server.close();
  assert.equal(proof.state, "cancelled");
  assert.equal(proof.parentFinalAnswer, null);
  assert.equal(events.some((item) => item.type === "parent_synthesizing"), false);
  assert.ok(proof.executedNodeCount <= 2);
});

test("Stop latches before execute, cancels without a Provider call, and rejects post-terminal Stop", async () => {
  let providerCalls = 0;
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async () => {
        providerCalls += 1;
        throw new Error("pre-start Stop must not reach Provider");
      },
    },
  });
  assert.equal(coordinator.requestStop(), true);
  const proof = await coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "pre-start Stop latch",
      teamMode: true,
      rosterEpoch: 89,
      budget: budget(),
    },
    () => undefined,
  );
  assert.equal(proof.state, "cancelled");
  assert.equal(host.runs[0]?.state, "cancelled");
  assert.equal(providerCalls, 0);
  assert.equal(host.parentCalls.length, 0);
  assert.equal(coordinator.requestStop(), false);
});

test("Stop immediately after durable parent consume settles cancelled before the Run", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  let coordinator!: PersonalTeamCoordinator;
  const consume = host.consumeProviderCall.bind(host);
  host.consumeProviderCall = async (input) => {
    const consumed = await consume(input);
    if (input.purpose === "parent-propose") {
      assert.equal(coordinator.requestStop(), true);
    }
    return consumed;
  };
  coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async () => {
        throw new Error("provider must not be reached after Stop");
      },
    },
  });
  const proof = await coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "stop immediately after durable consume",
      teamMode: true,
      rosterEpoch: 91,
      budget: budget(),
    },
    () => undefined,
  );
  assert.equal(proof.state, "cancelled");
  assert.equal(host.runs[0]?.state, "cancelled");
  const parentCall = host.parentCalls[0]!;
  assert.equal(parentCall.state, "cancelled");
  assert.equal(parentCall.actualModel, null);
  assert.equal(parentCall.totalTokens, null);
  assert.equal(parentCall.errorCode, "desktop_invocation_cancelled");
  assert.equal(host.parentCalls.some((item) => item.state === "pending"), false);
  const replay = await host.settleParentCall({
    workspaceId: WORKSPACE,
    teamRunId: proof.teamRunId,
    invocationId: parentCall.invocationId,
    purpose: parentCall.purpose,
    providerId: parentCall.providerId,
    requestedModel: parentCall.requestedModel,
    state: "cancelled",
    planRevisionId: null,
    actualModel: null,
    inputTokens: null,
    outputTokens: null,
    totalTokens: null,
    outputSha256: null,
    errorCode: "desktop_invocation_cancelled",
  });
  assert.equal(replay.parentCall.state, "cancelled");
});

test("Stop while an accepted parent proposal response is delayed preserves bulk cancellation", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  const submit = host.submitProposal.bind(host);
  let signalSubmitted!: () => void;
  let releaseResponse!: () => void;
  const submitted = new Promise<void>((resolve) => {
    signalSubmitted = resolve;
  });
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  host.submitProposal = async (input) => {
    const result = await submit(input);
    signalSubmitted();
    await responseGate;
    return result;
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async () => ({
        text: parentDelegate("answer_directly"),
        actualModel: "loopback-team",
        inputTokens: 3,
        outputTokens: 2,
        totalTokens: 5,
      }),
    },
  });
  const running = coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "Stop while accepted parent proposal response is delayed",
      teamMode: true,
      rosterEpoch: 95,
      budget: budget(),
    },
    () => undefined,
  );
  await submitted;
  assert.equal(coordinator.requestStop(), true);
  await host.setRunState({
    workspaceId: WORKSPACE,
    teamRunId: host.runs[0]!.id,
    state: "cancelled",
  });
  releaseResponse();
  const proof = await running;
  assert.equal(proof.state, "cancelled");
  assert.equal(host.runs[0]?.state, "cancelled");
  assert.equal(host.parentCalls[0]?.state, "cancelled");
  assert.equal(host.parentCalls[0]?.actualModel, null);
  assert.equal(host.parentCalls[0]?.totalTokens, null);
  assert.equal(host.parentCalls[0]?.errorCode, "desktop_invocation_cancelled");
});

test("Stop while parent proposal submission rejects returns a cancelled proof", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  let signalSubmitted!: () => void;
  let releaseResponse!: () => void;
  const submitted = new Promise<void>((resolve) => {
    signalSubmitted = resolve;
  });
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  host.submitProposal = async () => {
    signalSubmitted();
    await responseGate;
    throw Object.assign(new Error("desktop_team_run_terminal"), {
      code: "desktop_team_run_terminal",
    });
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async () => ({
        text: parentDelegate("answer_directly"),
        actualModel: "loopback-team",
        inputTokens: null,
        outputTokens: null,
        totalTokens: null,
      }),
    },
  });
  const running = coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "Stop while parent proposal submission rejects",
      teamMode: true,
      rosterEpoch: 96,
      budget: budget(),
    },
    () => undefined,
  );
  await submitted;
  assert.equal(coordinator.requestStop(), true);
  await host.setRunState({
    workspaceId: WORKSPACE,
    teamRunId: host.runs[0]!.id,
    state: "cancelled",
  });
  releaseResponse();
  const proof = await running;
  assert.equal(proof.state, "cancelled");
  assert.equal(host.runs[0]?.state, "cancelled");
  assert.equal(host.parentCalls[0]?.state, "cancelled");
  assert.equal(host.parentCalls[0]?.errorCode, "desktop_invocation_cancelled");
});

test("Stop from submit-unknown settlement callback wins before quiet Run commit", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  host.submitProposal = async () => {
    throw Object.assign(new Error("desktop_native_response_invalid"), {
      code: "desktop_native_response_invalid",
    });
  };
  const settleParentCall = host.settleParentCall.bind(host);
  let coordinator!: PersonalTeamCoordinator;
  host.settleParentCall = async (input) => {
    const result = await settleParentCall(input);
    if (input.state === "unknown") {
      assert.equal(coordinator.requestStop(), true);
    }
    return result;
  };
  coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async () => ({
        text: parentDelegate("answer_directly"),
        actualModel: "loopback-team",
        inputTokens: 3,
        outputTokens: 2,
        totalTokens: 5,
      }),
    },
  });
  const proof = await coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "Stop after parent submit became unknown",
      teamMode: true,
      rosterEpoch: 104,
      budget: budget(),
    },
    () => undefined,
  );
  assert.equal(proof.state, "cancelled");
  assert.equal(host.runs[0]?.state, "cancelled");
  assert.equal(host.parentCalls[0]?.state, "unknown");
  assert.equal(
    host.parentCalls[0]?.errorCode,
    "desktop_team_parent_proposal_submit_unknown",
  );
});

test("Stop after quiet Run commit starts is rejected by the local linearization", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  host.submitProposal = async () => {
    throw Object.assign(new Error("desktop_native_response_invalid"), {
      code: "desktop_native_response_invalid",
    });
  };
  const setRunState = host.setRunState.bind(host);
  let signalQuietCommit!: () => void;
  let releaseQuietCommit!: () => void;
  const quietCommitEntered = new Promise<void>((resolve) => {
    signalQuietCommit = resolve;
  });
  const quietCommitGate = new Promise<void>((resolve) => {
    releaseQuietCommit = resolve;
  });
  host.setRunState = async (input) => {
    if (input.state === "unknown") {
      signalQuietCommit();
      await quietCommitGate;
    }
    return setRunState(input);
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async () => ({
        text: parentDelegate("answer_directly"),
        actualModel: "loopback-team",
        inputTokens: 3,
        outputTokens: 2,
        totalTokens: 5,
      }),
    },
  });
  const running = coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "quiet terminal linearization",
      teamMode: true,
      rosterEpoch: 105,
      budget: budget(),
    },
    () => undefined,
  );
  await quietCommitEntered;
  assert.equal(coordinator.requestStop(), false);
  releaseQuietCommit();
  assert.equal(await coordinator.waitForQuietCommit(), true);
  const proof = await running;
  assert.equal(proof.state, "unknown");
  assert.equal(host.runs[0]?.state, "unknown");
});

test("Stop after an invalid initial proposal proof wins before failed Run commit", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  const settleParentCall = host.settleParentCall.bind(host);
  let coordinator!: PersonalTeamCoordinator;
  host.settleParentCall = async (input) => {
    const result = await settleParentCall(input);
    if (input.purpose === "parent-propose" && input.state === "failed") {
      assert.equal(coordinator.requestStop(), true);
    }
    return result;
  };
  coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async () => ({
        text: parentDelegate("unknown_role"),
        actualModel: "loopback-team",
        inputTokens: 3,
        outputTokens: 2,
        totalTokens: 5,
      }),
    },
  });

  const proof = await coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "Stop after an invalid initial proposal proof",
      teamMode: true,
      rosterEpoch: 106,
      budget: budget(),
    },
    () => undefined,
  );
  assert.equal(proof.state, "cancelled");
  assert.equal(host.runs[0]?.state, "cancelled");
  assert.equal(host.parentCalls[0]?.state, "failed");
});

test("Stop after an invalid replan proof wins before failed Run commit", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  const settleParentCall = host.settleParentCall.bind(host);
  let coordinator!: PersonalTeamCoordinator;
  host.settleParentCall = async (input) => {
    const result = await settleParentCall(input);
    if (input.purpose === "parent-replan" && input.state === "failed") {
      assert.equal(coordinator.requestStop(), true);
    }
    return result;
  };
  coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async (input) => {
        const role = input.messages[0]?.content ?? "";
        const text = role.includes("parent-propose")
          ? parentDelegate("one_specialist")
          : role.includes("employee:frontend")
            ? employeeReport("frontend", "frontend-review", "one_specialist")
            : JSON.stringify({ decision: "continue", reason: "missing nextWave" });
        return {
          text,
          actualModel: "loopback-team",
          inputTokens: 3,
          outputTokens: 2,
          totalTokens: 5,
        };
      },
    },
  });

  const proof = await coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "Stop after an invalid replan proof",
      teamMode: true,
      rosterEpoch: 107,
      budget: budget(),
    },
    () => undefined,
  );
  assert.equal(proof.state, "cancelled");
  assert.equal(host.runs[0]?.state, "cancelled");
  assert.equal(
    host.parentCalls.find((call) => call.purpose === "parent-replan")?.state,
    "failed",
  );
});

test("wall expiry while an accepted parent proposal response is delayed fails the proof", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  const submit = host.submitProposal.bind(host);
  let now = 10;
  let signalSubmitted!: () => void;
  let releaseResponse!: () => void;
  const submitted = new Promise<void>((resolve) => {
    signalSubmitted = resolve;
  });
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  host.submitProposal = async (input) => {
    const result = await submit(input);
    signalSubmitted();
    await responseGate;
    return result;
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    now: () => now,
    transport: {
      complete: async () => ({
        text: parentDelegate("answer_directly"),
        actualModel: "loopback-team",
        inputTokens: 3,
        outputTokens: 2,
        totalTokens: 5,
      }),
    },
  });
  const running = coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "wall expiry while accepted parent proposal response is delayed",
      teamMode: true,
      rosterEpoch: 97,
      budget: budget({ maximumWallTimeMs: 60_000 }),
    },
    () => undefined,
  );
  await submitted;
  now = 60_011;
  releaseResponse();
  const proof = await running;
  assert.equal(proof.state, "budget_exhausted");
  assert.equal(host.runs[0]?.state, "budget_exhausted");
  assert.equal(host.parentCalls[0]?.state, "failed");
  assert.equal(host.parentCalls[0]?.planRevisionId, null);
  assert.equal(host.parentCalls[0]?.outputSha256, null);
  assert.equal(host.parentCalls[0]?.errorCode, "desktop_team_wall_time_exceeded");
});

test("accepted delegate wall expiry blocks unstarted assignments before budget terminal", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  const submit = host.submitProposal.bind(host);
  let now = 10;
  let signalSubmitted!: () => void;
  let releaseResponse!: () => void;
  const submitted = new Promise<void>((resolve) => {
    signalSubmitted = resolve;
  });
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  host.submitProposal = async (input) => {
    const result = await submit(input);
    signalSubmitted();
    await responseGate;
    return result;
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    now: () => now,
    transport: {
      complete: async () => ({
        text: parentDelegate("one_specialist"),
        actualModel: "loopback-team",
        inputTokens: 3,
        outputTokens: 2,
        totalTokens: 5,
      }),
    },
  });
  const running = coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "accepted delegate wall convergence",
      teamMode: true,
      rosterEpoch: 101,
      budget: budget({ maximumWallTimeMs: 60_000 }),
    },
    () => undefined,
  );
  await submitted;
  now = 60_011;
  releaseResponse();
  const proof = await running;
  assert.equal(proof.state, "budget_exhausted");
  assert.equal(host.runs[0]?.state, "budget_exhausted");
  assert.equal(host.parentCalls[0]?.state, "failed");
  assert.equal(host.parentCalls[0]?.errorCode, "desktop_team_wall_time_exceeded");
  assert.equal(host.nodes.length, 0);
  assert.equal(host.assignmentStates.get("frontend-review"), "blocked");
});

test("wall expiry while parent proposal submission rejects returns a budget proof", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  let now = 10;
  let signalSubmitted!: () => void;
  let releaseResponse!: () => void;
  const submitted = new Promise<void>((resolve) => {
    signalSubmitted = resolve;
  });
  const responseGate = new Promise<void>((resolve) => {
    releaseResponse = resolve;
  });
  host.submitProposal = async () => {
    signalSubmitted();
    await responseGate;
    throw Object.assign(new Error("desktop_native_response_invalid"), {
      code: "desktop_native_response_invalid",
    });
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    now: () => now,
    transport: {
      complete: async () => ({
        text: parentDelegate("answer_directly"),
        actualModel: "loopback-team",
        inputTokens: null,
        outputTokens: null,
        totalTokens: null,
      }),
    },
  });
  const running = coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "wall expiry while parent proposal submission rejects",
      teamMode: true,
      rosterEpoch: 98,
      budget: budget({ maximumWallTimeMs: 60_000 }),
    },
    () => undefined,
  );
  await submitted;
  now = 60_011;
  releaseResponse();
  const proof = await running;
  assert.equal(proof.state, "budget_exhausted");
  assert.equal(host.runs[0]?.state, "budget_exhausted");
  assert.equal(host.parentCalls[0]?.state, "failed");
  assert.equal(host.parentCalls[0]?.errorCode, "desktop_team_wall_time_exceeded");
});

test("Stop from a parent node_delta callback prevents proposal submission", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  const submit = host.submitProposal.bind(host);
  let proposalSubmissions = 0;
  host.submitProposal = async (input) => {
    proposalSubmissions += 1;
    return submit(input);
  };
  let coordinator!: PersonalTeamCoordinator;
  coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async () => ({
        text: parentDelegate("answer_directly"),
        actualModel: "loopback-team",
        inputTokens: 3,
        outputTokens: 2,
        totalTokens: 5,
      }),
    },
  });
  const proof = await coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "Stop synchronously from parent node delta",
      teamMode: true,
      rosterEpoch: 99,
      budget: budget(),
    },
    (event) => {
      if (event.type === "node_delta" && event.employeeRoleId === "parent") {
        assert.equal(coordinator.requestStop(), true);
      }
    },
  );
  assert.equal(proof.state, "cancelled");
  assert.equal(host.runs[0]?.state, "cancelled");
  assert.equal(proposalSubmissions, 0);
  assert.equal(host.planRevisions.length, 0);
  assert.equal(host.parentCalls[0]?.state, "cancelled");
  assert.equal(host.parentCalls[0]?.actualModel, null);
  assert.equal(host.parentCalls[0]?.totalTokens, null);
  assert.equal(host.parentCalls[0]?.errorCode, "desktop_invocation_cancelled");
});

for (const scenario of [
  { code: "desktop_role_provider_unresolved", state: "failed" },
  { code: "desktop_provider_response_invalid", state: "unknown" },
] as const) {
  test(`pre-node ${scenario.state} blocks the accepted assignment before terminalizing`, async () => {
    const host = createInMemoryPersonalTeamHost({
      credentials: {
        providerId: `provider_${"d".repeat(32)}`,
        model: "loopback-team",
        baseUrl: "http://127.0.0.1:1/v1",
        secret: "x",
        allowLoopbackHttp: true,
        timeoutMs: 10,
      },
    });
    const resolveCredentials = host.resolveCredentials.bind(host);
    host.resolveCredentials = async (workspaceId, roleId, signal) => {
      if (roleId === "parent") {
        return resolveCredentials(workspaceId, roleId, signal);
      }
      throw Object.assign(new Error(scenario.code), { code: scenario.code });
    };
    const coordinator = new PersonalTeamCoordinator({
      host,
      transport: {
        complete: async () => ({
          text: parentDelegate("one_specialist"),
          actualModel: "loopback-team",
          inputTokens: 3,
          outputTokens: 2,
          totalTokens: 5,
        }),
      },
    });
    const proof = await coordinator.execute(
      {
        workspaceId: WORKSPACE,
        conversationId: CONVERSATION,
        task: `pre-node ${scenario.state} convergence`,
        teamMode: true,
        rosterEpoch: scenario.state === "failed" ? 102 : 103,
        budget: budget(),
      },
      () => undefined,
    );
    assert.equal(proof.state, scenario.state);
    assert.equal(host.runs[0]?.state, scenario.state);
    assert.equal(host.runs[0]?.consumedProviderCalls, 1);
    assert.equal(host.nodes.length, 0);
    assert.equal(host.parentCalls[0]?.state, "succeeded");
    assert.equal(host.assignmentStates.get("frontend-review"), "blocked");
  });
}

test("employee consume remains visible on Stop without creating a parent proof", async () => {
  const recorder = startFakeOpenAi([]);
  const server = await recorder.listen();
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: server.baseUrl,
      secret: "loopback-secret-not-for-git",
      allowLoopbackHttp: true,
      timeoutMs: 5_000,
    },
  });
  let coordinator!: PersonalTeamCoordinator;
  const consume = host.consumeProviderCall.bind(host);
  host.consumeProviderCall = async (input) => {
    const consumed = await consume(input);
    if (input.purpose === "employee") {
      assert.equal(coordinator.requestStop(), true);
    }
    return consumed;
  };
  coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  try {
    const proof = await coordinator.execute(
      {
        workspaceId: WORKSPACE,
        conversationId: CONVERSATION,
        task: "[p69-scenario:one_specialist] Stop after employee consume",
        teamMode: true,
        rosterEpoch: 95,
        budget: budget(),
      },
      () => undefined,
    );
    assert.equal(proof.state, "cancelled");
    assert.equal(host.runs[0]?.state, "cancelled");
    assert.equal(host.runs[0]?.consumedProviderCalls, 2);
    assert.equal(proof.providerCallCount, 2);
    assert.equal(proof.executedNodeCount, 0);
    assert.equal(host.parentCalls.length, 1);
    assert.equal(host.parentCalls[0]?.purpose, "parent-propose");
    assert.equal(host.parentCalls.some((item) => item.state === "pending"), false);
  } finally {
    await server.close();
  }
});

test("wall expiry immediately after durable parent consume settles failed before budget terminal", async () => {
  let now = 1_000;
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  const consume = host.consumeProviderCall.bind(host);
  host.consumeProviderCall = async (input) => {
    const consumed = await consume(input);
    now = 700_000;
    return consumed;
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    now: () => now,
    transport: {
      complete: async () => {
        throw new Error("provider must not be reached after wall expiry");
      },
    },
  });
  const proof = await coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "wall expires after durable consume",
      teamMode: true,
      rosterEpoch: 92,
      budget: budget({ maximumWallTimeMs: 60_000 }),
    },
    () => undefined,
  );
  assert.equal(proof.state, "budget_exhausted");
  assert.equal(host.runs[0]?.state, "budget_exhausted");
  assert.equal(host.parentCalls[0]?.state, "failed");
  assert.equal(host.parentCalls[0]?.errorCode, "desktop_team_wall_time_exceeded");
  assert.equal(host.parentCalls.some((item) => item.state === "pending"), false);
});

for (const scenario of [
  { code: "desktop_provider_request_failed", state: "failed" },
  { code: "desktop_provider_response_invalid", state: "unknown" },
] as const) {
  test(`parent Provider ${scenario.code} settles ${scenario.state} and keeps Run/proof consistent`, async () => {
    const host = createInMemoryPersonalTeamHost({
      credentials: {
        providerId: `provider_${"d".repeat(32)}`,
        model: "loopback-team",
        baseUrl: "http://127.0.0.1:1/v1",
        secret: "x",
        allowLoopbackHttp: true,
        timeoutMs: 10,
      },
    });
    const coordinator = new PersonalTeamCoordinator({
      host,
      transport: {
        complete: async () => {
          throw Object.assign(new Error(scenario.code), { code: scenario.code });
        },
      },
    });
    const proof = await coordinator.execute(
      {
        workspaceId: WORKSPACE,
        conversationId: CONVERSATION,
        task: `parent failure ${scenario.code}`,
        teamMode: true,
        rosterEpoch: 93,
        budget: budget(),
      },
      () => undefined,
    );
    assert.equal(proof.state, scenario.state);
    assert.equal(host.runs[0]?.state, scenario.state);
    assert.equal(host.parentCalls[0]?.state, scenario.state);
    assert.equal(host.parentCalls[0]?.errorCode, scenario.code);
    assert.equal(host.parentCalls.some((item) => item.state === "pending"), false);
  });
}

test("malformed consume projection reconciles its durable parent proof as unknown", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  const consume = host.consumeProviderCall.bind(host);
  host.consumeProviderCall = async (input) => {
    const consumed = await consume(input);
    if (consumed.parentCall === undefined) return consumed;
    return {
      ...consumed,
      parentCall: {
        ...consumed.parentCall,
        invocationId: `invocation_${"0".repeat(32)}`,
      },
    };
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: {
      complete: async () => {
        throw new Error("provider must not be reached after malformed consume");
      },
    },
  });
  const proof = await coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "malformed durable consume projection",
      teamMode: true,
      rosterEpoch: 94,
      budget: budget(),
    },
    () => undefined,
  );
  assert.equal(proof.state, "unknown");
  assert.equal(host.runs[0]?.state, "unknown");
  assert.equal(host.parentCalls[0]?.state, "unknown");
  assert.equal(
    host.parentCalls[0]?.errorCode,
    "desktop_team_parent_call_consume_unknown",
  );
  assert.equal(host.parentCalls.some((item) => item.state === "pending"), false);
});

test("Stop before the answer_directly success linearization prevents the commit", async () => {
  const recorder = startFakeOpenAi([]);
  const server = await recorder.listen();
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: server.baseUrl,
      secret: "loopback-secret-not-for-git",
      allowLoopbackHttp: true,
      timeoutMs: 5_000,
    },
  });
  const original = host.getBlackboard.bind(host);
  let releaseCloseOut!: () => void;
  let signalCloseOut!: () => void;
  const closeOutEntered = new Promise<void>((resolve) => {
    signalCloseOut = resolve;
  });
  const closeOutGate = new Promise<void>((resolve) => {
    releaseCloseOut = resolve;
  });
  host.getBlackboard = async (input) => {
    signalCloseOut();
    await closeOutGate;
    return original(input);
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  const running = coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "[p69-scenario:answer_directly] stop race",
      teamMode: true,
      rosterEpoch: 1,
      budget: budget({}),
    },
    () => undefined,
  );
  await closeOutEntered;
  assert.equal(coordinator.requestStop(), true);
  releaseCloseOut();
  const proof = await running;
  await server.close();
  assert.equal(proof.state, "cancelled");
  assert.equal(host.runs[0]!.state, "cancelled");
});

test("Stop before the synthesis success linearization prevents the commit", async () => {
  const recorder = startFakeOpenAi([]);
  const server = await recorder.listen();
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: server.baseUrl,
      secret: "loopback-secret-not-for-git",
      allowLoopbackHttp: true,
      timeoutMs: 5_000,
    },
  });
  const original = host.getBlackboard.bind(host);
  let releaseCloseOut!: () => void;
  let signalCloseOut!: () => void;
  const closeOutEntered = new Promise<void>((resolve) => {
    signalCloseOut = resolve;
  });
  const closeOutGate = new Promise<void>((resolve) => {
    releaseCloseOut = resolve;
  });
  let blackboardCalls = 0;
  host.getBlackboard = async (input) => {
    blackboardCalls += 1;
    if (blackboardCalls === 1) return original(input);
    signalCloseOut();
    await closeOutGate;
    return original(input);
  };
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  const running = coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "[p69-scenario:one_specialist] stop race",
      teamMode: true,
      rosterEpoch: 1,
      budget: budget({}),
    },
    () => undefined,
  );
  await closeOutEntered;
  assert.equal(coordinator.requestStop(), true);
  releaseCloseOut();
  const proof = await running;
  await server.close();
  assert.equal(proof.state, "cancelled");
  assert.equal(host.runs[0]!.state, "cancelled");
});

for (const scenario of ["answer_directly", "one_specialist"] as const) {
  test(`Stop after the ${scenario} success linearization is rejected and cannot contradict success`, async () => {
    const recorder = startFakeOpenAi([]);
    const server = await recorder.listen();
    const host = createInMemoryPersonalTeamHost({
      credentials: {
        providerId: `provider_${"d".repeat(32)}`,
        model: "loopback-team",
        baseUrl: server.baseUrl,
        secret: "loopback-secret-not-for-git",
        allowLoopbackHttp: true,
        timeoutMs: 5_000,
      },
    });
    const original = host.setRunState.bind(host);
    let releaseSuccess!: () => void;
    let signalSuccessEntered!: () => void;
    const successEntered = new Promise<void>((resolve) => {
      signalSuccessEntered = resolve;
    });
    const successGate = new Promise<void>((resolve) => {
      releaseSuccess = resolve;
    });
    host.setRunState = async (input) => {
      if (input.state === "succeeded") {
        signalSuccessEntered();
        await successGate;
      }
      return original(input);
    };
    const coordinator = new PersonalTeamCoordinator({
      host,
      transport: createOpenAiCompatibleTransport(),
    });
    const running = coordinator.execute(
      {
        workspaceId: WORKSPACE,
        conversationId: CONVERSATION,
        task: `[p69-scenario:${scenario}] stop after success linearization`,
        teamMode: true,
        rosterEpoch: 1,
        budget: budget({}),
      },
      () => undefined,
    );
    await successEntered;
    assert.equal(coordinator.requestStop(), false);
    const commitWait = coordinator.waitForSuccessCommit();
    releaseSuccess();
    assert.equal(await commitWait, true);
    const proof = await running;
    await server.close();
    assert.equal(proof.state, "succeeded");
    assert.equal(host.runs[0]!.state, "succeeded");
  });
}

test("team events missing roster/node/send epoch must not match the live identity", () => {
  const current = {
    workspaceId: WORKSPACE,
    conversationId: CONVERSATION,
    teamRunId: `teamrun_${"e".repeat(32)}`,
    rosterEpoch: 3,
    waveId: "wave-1",
    nodeId: `teamnode_${"f".repeat(32)}`,
    sendEpoch: 4,
    invocationId: `invocation_${"1".repeat(32)}`,
  };
  const valid: DesktopTeamRunEvent = {
    type: "node_delta",
    teamRunId: current.teamRunId,
    workspaceId: WORKSPACE,
    conversationId: CONVERSATION,
    rosterEpoch: 3,
    planRevisionId: "teamrev_1",
    waveId: "wave-1",
    assignmentId: "frontend-review",
    nodeId: current.nodeId,
    sendEpoch: 4,
    nodeEpoch: 1,
    employeeRoleId: "frontend",
    invocationId: current.invocationId,
    text: "ok",
  };
  assert.equal(eventMatchesTeamIdentity(current, valid), true);
  assert.equal(eventMatchesTeamIdentity(current, { ...valid, rosterEpoch: 9 }), false);
  assert.equal(eventMatchesTeamIdentity(current, { ...valid, waveId: "wave-2" }), false);
  assert.equal(eventMatchesTeamIdentity(current, { ...valid, sendEpoch: 1 }), false);
  assert.equal(
    eventMatchesTeamIdentity(current, { ...valid, invocationId: `invocation_${"2".repeat(32)}` }),
    false,
  );
  assert.equal(
    eventMatchesTeamIdentity(current, { ...valid, workspaceId: OTHER_WORKSPACE }),
    false,
  );
});

test("secret in collaboration is rejected without a fake success", async () => {
  const { proof } = await runScenario("secret_collab");
  assert.equal(proof.state, "failed");
  assert.equal(proof.parentFinalAnswer, null);
});

test("tokens are recorded per specialist node and match Provider call count", async () => {
  const { proof, events, calls } = await runScenario("one_specialist");
  const terminals = events.filter((item) => item.type === "node_terminal" && item.employeeRoleId !== "parent");
  assert.equal(terminals.length, 1);
  assert.equal(terminals[0]?.totalTokens, 18);
  assert.equal(calls.length, proof.providerCallCount);
  assert.equal(proof.hiddenCalls, false);
});

test("Stop on a serial hang skips waiting nodes and does not synthesize", async () => {
  const recorder = startFakeOpenAi([]);
  const server = await recorder.listen();
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: server.baseUrl,
      secret: "loopback-secret-not-for-git",
      allowLoopbackHttp: true,
      timeoutMs: 5_000,
    },
  });
  const coordinator = new PersonalTeamCoordinator({
    host,
    transport: createOpenAiCompatibleTransport(),
  });
  const events: DesktopTeamRunEvent[] = [];
  const running = coordinator.execute(
    {
      workspaceId: WORKSPACE,
      conversationId: CONVERSATION,
      task: "[p69-scenario:hang_serial] serial stop",
      teamMode: true,
      rosterEpoch: 8,
      budget: budget({ maximumConcurrentCalls: 1 }),
    },
    (event) => events.push(event),
  );
  await new Promise((resolve) => setTimeout(resolve, 60));
  coordinator.requestStop();
  const proof = await running;
  await server.close();
  assert.equal(proof.state, "cancelled");
  assert.equal(proof.parentFinalAnswer, null);
  assert.equal(events.some((item) => item.type === "parent_synthesizing"), false);
  const started = events.filter((item) => item.type === "node_starting");
  assert.equal(started.length, 1);
  assert.equal(started[0]?.employeeRoleId, "frontend");
  assert.equal(events.some((item) => item.type === "node_starting" && item.employeeRoleId === "backend"), false);
});

test("partial Provider failure fail-stops without retry or fake success", async () => {
  const { proof } = await runScenario("partial_fail");
  assert.equal(["failed", "unknown"].includes(proof.state), true);
  assert.equal(proof.parentFinalAnswer, null);
  assert.equal(proof.parentWasLastWhenSynthesizing, true);
});

test("parallel incomplete Provider response marks unknown without replay", async () => {
  const { proof } = await runScenario("parallel_unknown");
  assert.equal(proof.state, "unknown");
  assert.equal(proof.parentFinalAnswer, null);
});

test("second invoke cannot reuse an old invocation id on the host", async () => {
  const host = createInMemoryPersonalTeamHost({
    credentials: {
      providerId: `provider_${"d".repeat(32)}`,
      model: "loopback-team",
      baseUrl: "http://127.0.0.1:1/v1",
      secret: "x",
      allowLoopbackHttp: true,
      timeoutMs: 10,
    },
  });
  await host.startTeamRun({
    workspaceId: WORKSPACE,
    conversationId: CONVERSATION,
    task: "x",
    teamMode: true,
    rosterEpoch: 1,
    budget: budget(),
  });
  const invocationId = `invocation_${"9".repeat(32)}`;
  await host.createNode({
    workspaceId: WORKSPACE,
    teamRunId: host.runs[0]!.id,
    assignmentId: "frontend-review",
    employeeRoleId: "frontend",
    invocationId,
    waveId: "wave-1",
    nodeEpoch: 1,
    sendEpoch: 1,
    providerId: `provider_${"d".repeat(32)}`,
    requestedModel: "loopback-team",
  });
  await assert.rejects(
    () =>
      host.createNode({
        workspaceId: WORKSPACE,
        teamRunId: host.runs[0]!.id,
        assignmentId: "frontend-followup",
        employeeRoleId: "frontend",
        invocationId,
        waveId: "wave-2",
        nodeEpoch: 2,
        sendEpoch: 2,
        providerId: `provider_${"d".repeat(32)}`,
        requestedModel: "loopback-team",
      }),
    /desktop_team_duplicate_invocation/u,
  );
});
