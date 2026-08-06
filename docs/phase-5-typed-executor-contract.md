# P5.4A Typed Single-Agent Executor Contract

P5.4A is the first engineering-only execution slice after the compile-only
P5.3A Planner Proposal contract. It accepts one immutable `ValidatedPlan` and
one plan node, then permits exactly one read-only logical capability:

```text
planner tool binding: knowledge_search
capability name:      workspace.knowledge.search
effect class:         read_only
```

The executor is intentionally an internal typed seam. It is not mounted by the
Browser application and has no default adapter. An explicit engineering
composition must inject a Capability-Gateway-backed `KnowledgeSearchPort`; the
default builder returns `UnavailableTypedSingleAgentExecutor`.

## Boundary

At the execution boundary the service rechecks the immutable plan and the
server-owned invocation context. It rejects:

- an invalid, drifted or unbound `ValidatedPlan`;
- tenant, Workspace, generation, actor, Task, AgentVersion or proposal digest
  mismatches;
- more than one plan node;
- any tool set other than the exact `knowledge_search` binding;
- non-low-risk or non-read-only nodes;
- unsupported node kinds;
- a missing or exhausted tool budget;
- a search request exceeding the node's byte budget.

The request and result DTOs contain only logical identifiers and bounded data.
They never carry PostgreSQL schema/table/column names, object-store locators,
Provider credentials, Browser JWTs, process IDs, sockets, host paths or model
handles. Adapter failures are fail-closed and are not converted to successful
tool receipts.

## Deliberate non-goals

P5.4A does not add a Browser route, SDK surface, queue, worker, scheduler,
Planner Runtime, migration, Skill persistence/runtime, MCP, Shell, SQL,
arbitrary HTTP, file writes, Sandbox execution or multi-Agent orchestration.
All three Phase 5 Feature Gates remain false and production Runtime activation
still requires a separate admission decision.

The next engineering step is a Capability Gateway adapter and a disposable
end-to-end Gate. That Gate must prove the logical capability, tenant/Workspace
scope, budget, audit, fencing and `unknown` no-replay behavior before any
additional tool is considered.
