# P5.3A Planner Proposal Contract

**Engineering decision:** `ACCEPTED_ENGINEERING_ONLY_COMPILE_ONLY_PRODUCTION_BLOCKED`

## Purpose

P5.3A freezes the offline contract for the Planner Proposal DAG. It establishes
the boundary between a model's proposed plan and the server's deterministic
validation of that plan.

**Core principle:** The model can only *propose* a PlanProposal. A deterministic
server-side Validator accepts or rejects it. Even a validated proposal cannot
be executed, dispatched or scheduled by P5.3A.

## Scope

P5.3A freezes:

- `PlanProposal` identity bound to a frozen Task, Workspace, Tenant, Actor
  and root AgentVersion;
- The closed DAG structure: bounded nodes, no cycles, depth/fan-out/concurrency
  limits, deterministic topological order and canonical graph digest;
- Typed input bindings referencing only Task input, declared dependency output
  or allowed logical resource;
- AgentVersion snapshot verification against the server-provided registry;
- Tool allowlist intersection of AgentVersion, Workspace binding and Planner
  policy; no wildcard, no hidden tools, no shell/SQL/arbitrary HTTP;
- Resource scope intersection; no wildcard, no cross-tenant, no read-to-write
  escalation;
- Twelve-dimensional budget matching P5.2A semantics with worst-case
  retry/replan included;
- Risk and approval closed set matching the Registry/Operation/Approval system;
- Retry policy closed set; unknown effect never auto-retry;
- Portability: no Hyper-V/KVM/Docker/WSL/host path/physical provider fields.

## What P5.3A does NOT do

P5.3A does not create:

- Executor, Scheduler, Worker, Dispatcher
- DAG execution or plan dispatch
- Background task, thread, Celery or event loop worker
- Model or Provider calls
- External network requests
- Tool Runtime, MCP, Skills Runtime, Sandbox job
- Multi-Agent or Delegation Runtime
- Browser Planner API or OpenAPI Planner endpoint
- Python/TypeScript Planner SDK
- Migration 0012 or ORM Planner tables

## Contract objects

| Object | Purpose |
|--------|---------|
| `PlannerPolicy` | Server-owned policy with ceilings, approval rules |
| `PlannerCeilings` | Tightened server ceilings (max_nodes, max_depth, etc.) |
| `PlanProposal` | The model's proposed plan with identity and nodes |
| `PlanNodeProposal` | One node in the DAG with budget, scope, tools |
| `PlanInputBinding` | Typed input reference (task_input, dependency_output, etc.) |
| `PlanOutputContract` | Output JSON Schema and digest |
| `PlanNodeBudget` | 12-dimensional budget per node |
| `PlanRetryPolicy` | Closed-set retry policy |
| `PlanApprovalRequirement` | Compile-time approval binding |
| `AgentVersionSnapshot` | Server-provided agent version snapshot |
| `ToolVersionSnapshot` | Server-provided tool version snapshot |
| `WorkspaceScopeSnapshot` | Server-provided workspace scope |
| `FrozenTaskSnapshot` | Server-provided frozen task context |
| `ValidatedPlan` | Immutable validated plan version |
| `PlanValidationFinding` | One validation finding (error/warning) |
| `PlanValidationReport` | Complete validation report |
| `ExecutionRequirement` | Provider-neutral execution requirements |

## DAG Validator

The `PlanProposalValidator` performs deterministic server-side validation:

1. **Identity**: tenant, workspace, task match frozen snapshot
2. **DAG structure**: no cycles (Kahn's algorithm), depth/fan-out/concurrency limits
3. **Data flow**: input bindings reference only declared dependencies or allowed resources
4. **AgentVersion**: exists, sealed, same tenant, digest matches
5. **Tool allowlist**: intersection of AgentVersion, Workspace and Policy
6. **Resource scope**: intersection of Task, Workspace and AgentVersion
7. **Budget**: node aggregate with worst-case retry within plan budget within task budget
8. **Risk/Approval**: high/critical require compile-time approval binding
9. **Retry/Deadline**: unknown effect no retry, non-idempotent effect no retry
10. **Portability**: no provider-specific tokens in serialized proposal
11. **Forbidden fields**: no executable fields (command, shell, sql, url, etc.)
12. **Digests**: proposal and node digests match canonical payloads

## Canonical hashing

- Canonical JSON: UTF-8, sorted keys, no whitespace, no NaN/Infinity
- Nodes sorted by `node_id` in proposal canonical payload
- `depends_on` sorted in node canonical payload
- `allowed_tool_ids` and `resource_scopes` sorted in node canonical payload
- `approval_requirement` excluded from node digest (approval binds *to* digest)
- Same semantic content, different input order → same digest

## Portability constraint

The current Hyper-V Linux Runner is one engineering-Gate-passed Provider
implementation, not a platform-level requirement. PlanProposal and
ExecutionRequirement contain only provider-neutral fields:
`isolation_class`, `untrusted_code`, `os_architecture`, `network_policy`,
`workspace_data_access_mode`, `artifact_policy`, `resource_ceilings` and
`required_logical_capabilities`.

Future Linux KVM, macOS VM and remote Runner implementations must be
achievable without modifying the Plan canonical semantics.

## Feature gates

```
AGENT_RUNTIME_ENABLED=false
AGENT_PLANNER_ENABLED=false
MULTI_AGENT_ENABLED=false
```

## Migration head

P5.3A does not create migration 0012. Migration head remains `0011`.

## Formal gate

`scripts/production/validate_p5_3a_planner_contract.py`

- `--validate-only`: parse contract, validate proposals, exit 0
- `--verify`: hash checkout, resolve gates, check sealed digests, exit 2 (blocked)

Expected `--verify` state: `blocked/not_proven`, `contract_valid=true`,
`activation_allowed=false`, exit 2.
