# P5.2A Agent Task / Run / Lease / Fencing Ledger Contract（离线合同预检）

> 状态：`P5.2A offline task ledger contract preflight: implemented / verified`
> （engineering-only）。P5.2 persistence ledger、Agent Runtime、Planner、
> Executor、scheduler、worker、模型/工具调用**均未实现**；P5.2 production
> 为 `blocked / not_proven`；`AGENT_RUNTIME_ENABLED`、
> `AGENT_PLANNER_ENABLED`、`MULTI_AGENT_ENABLED` 三个 Feature Gate 保持
> `false`；P34.7/P5.0/P5.1 production 恒 `blocked/not_proven`。
>
> 本文件是 P5.2 的**离线合同冻结**：它定义 AgentTask / Invocation →
> AgentRun → AgentStep → AgentAttempt → P34.4 Workspace Run →
> RuntimeInstance → WorkloadIdentity 的身份层级、状态机、Task Lease /
> Task fencing、预算账本、canonical hash profile、checkpoint 限制与
> Browser/Workload/Core 身份分离规则。任何读到本文件的人都不得把
> "有合同"理解为"Task 账本已实现"。

## 1. 目的与边界

P5.2A 只允许：离线 strict DTO、closed-set schema、canonical hashing、
离线 validator、正/负向 fixture、威胁模型补充、维护者地图、CI
validate-only Gate、测试与 handover 记录。

P5.2A 明确不交付（且合同在 `--verify` 中把出现下列源码视为 veto）：

- P5.2 ORM、Alembic revision `0011` 或任何新 revision、数据库表/trigger；
- `/api/v1/agent-invocations`、`/api/v1/agent-tasks`、
  `/gateway/v1/agent/*` 等 Agent Invocation 路由；
- Browser Invocation SDK、Workload SDK；
- Agent Runtime、Runtime Coordinator、Planner、Plan Validator、Executor、
  Dispatcher、Scheduler、worker、Celery task、Task polling/heartbeat
  loop、后台 coroutine；
- Model provider、Model Gateway、Tool Gateway、ToolDefinition、Memory
  Runtime、ContextCapsule、Skill Runtime、MCP、shell/SQL/HTTP tool、
  Docker socket、宿主进程执行；
- Agent UI、DAG、多 Agent 编排、生产配置解冻。

P5.2A 不运行 Agent，不启动 Planner，不创建 Executor，不调用模型，不提交
Sandbox job，不产生 Task Lease，不创建真实 Task/Run/Attempt。

## 2. 身份层级与逻辑标识符

P5.2A 明确区分以下身份层级；知道上层 UUID 绝不自动获得下层权限：

```text
AgentDefinition
  -> AgentVersion
  -> WorkspaceAgentBinding
  -> AgentTask / Invocation
  -> AgentRun
  -> AgentPlanVersion（仅 identity reference，本阶段不实现 Planner）
  -> AgentStep
  -> AgentAttempt
  -> P34.4 Workspace Run
  -> RuntimeInstance
  -> WorkloadIdentity
```

P5.2 不创建第二套 Node/Run/Runtime/Workload 身份系统：AgentRun 绑定现有
P34.4 Workspace Run（`workspace_run_id`），复用 `node_id`、
`node_fencing_token`、`run_lease_id`、`run_fencing_token`、
`runtime_instance_id` 与 `workload_identity_thumbprint`（x509 证书
thumbprint / workload digest 语义与 P34.5D Gateway 一致）。

合同冻结的绑定字段（全部为逻辑标识符，出现即校验）：

`tenant_id`、`workspace_id`、`workspace_generation`、`actor_user_id`、
`agent_definition_id`、`agent_version_id`、`agent_version_digest`、
`workspace_agent_binding_id`、`task_id`（=`invocation_id`）、
`task_generation`（=`invocation_generation`）、`plan_id`、`plan_version`、
`plan_digest`、`step_id`、`attempt_id`、`attempt_number`、
`workspace_run_id`、`runtime_instance_id`、`workload_identity_thumbprint`、
`node_id`、`node_fencing_token`、`run_lease_id`、`run_fencing_token`、
`task_lease_id`、`task_fencing_token`、`operation_id`、`request_hash`、
`deadline`、`lease_expiry`、`resource_scope_digest`、
`budget_policy_digest`、`expected_previous_state`、`cancellation_target`、
`reconciliation_target`、`effect_id`、`checkpoint_id`。

禁止出现在任何公共 DTO、错误、日志与 evidence 中：PostgreSQL
schema/table/column、`DATABASE_URL`、`REDIS_URL`、MinIO endpoint/bucket
locator、宿主路径、Docker socket、provider base URL、provider API key、
Authorization header、Browser cookie、JWT、signing key、raw workload
token、证书私钥、PID、socket、provider handle 与任意物理 locator——
strict DTO 在解析层拒绝这些字段（unknown-field fail-closed）。

### 2.1 生命周期阶段的字段规则（identity stages）

合同为 9 个阶段各冻结一张闭集字段表（`identity_stages`）：

`task_create`、`task_run_claim`、`attempt_claim`、`attempt_heartbeat`、
`attempt_finish`、`task_cancel`、`task_pause`、`task_resume_request`、
`reconciliation_request`。

每个阶段明确：

- `required_fields`：该阶段必须存在的字段；
- `not_yet_generated_fields`：该阶段尚未生成的字段（提交即拒绝）；
- `immutable_fields`：一旦生成不可变的字段；
- `core_generated_fields`：必须由可信 Core 生成、不得由 Browser/Workload
  提交的字段（如 `operation_id`、`request_hash`）；
- `browser_submittable_fields` / `workload_submittable_fields`：该 origin
  允许提交的字段；
- `forbidden_fields`：该阶段永远禁止出现的字段。

规则表本身必须闭集一致：`required ∩ not_yet_generated = ∅`、
`required ∩ forbidden = ∅`、`not_yet_generated ∩ immutable = ∅`、
`core_generated ∩ submittable = ∅`、`forbidden ∩ submittable = ∅`，
且每个字段必须属于 §2 的字段宇宙；违反即无效合同。

关键语义：

- Browser 只能提交 `task_create` / `task_cancel` / `task_pause` /
  `task_resume_request` 的声明字段；`runtime_instance_id`、
  `workload_identity_thumbprint`、`task_lease_id`、`task_fencing_token`、
  `request_hash` 等任何阶段都不得由 Browser 提交；
- Workload 只能提交 `attempt_claim` / `attempt_heartbeat` /
  `attempt_finish` 的声明字段；`runtime_instance_id`、
  `workload_identity_thumbprint` 永不出现在 workload DTO；
- `request_hash` 恒为 core-generated，任何调用方提供的 digest 覆盖都被
  拒绝（同 P5.1B/P5.1C 的 closed hash profile 语义）。

## 3. AgentTask / Invocation 合同

状态闭集（`TaskState`）：`created | planning | awaiting_approval |
scheduled | running | paused | blocked_unknown | succeeded | failed |
cancelled`。大小写、空白与未知值一律拒绝；终态
`succeeded|failed|cancelled` 不可回到运行态。

`AgentTaskInvocation` 字段：`schema_version`、`task_id`（或
`invocation_id` 别名，二选一且必须一致）、`tenant_id`、`workspace_id`、
`workspace_generation`（正整数）、`actor_user_id`、
`agent_definition_id`、`agent_version_id`、`agent_version_digest`、
`workspace_agent_binding_id`、`task_generation`（正整数）、
`plan_id|plan_version|plan_digest`（三者同现或同缺）、`deadline`、
`state`、`resource_scope_digest`、`budget_policy_digest`、`request_hash`、
`created_by`、`created_at`。

- `task_id` 是调用方预先分配的逻辑身份（可进入 hash）；`task_generation`
  由 Core 在创建时生成，只能递增；
- `deadline` 必须晚于 `created_at`，且 `deadline - created_at` 不得超过
  server-owned `deadline_ceiling_seconds`（config 只能收紧）；
- `request_hash` 必须等于 `task_create` hash profile 的 canonical
  digest（见 §7），任何漂移拒绝。

状态转移闭集（离线语义）：

```text
created -> planning
planning -> awaiting_approval | failed | cancelled
awaiting_approval -> scheduled | cancelled
scheduled -> running | paused | blocked_unknown | cancelled
running -> paused | blocked_unknown | succeeded | failed | cancelled
paused -> running | failed | cancelled
blocked_unknown -> failed | cancelled
succeeded|failed|cancelled -> （无出口）
```

## 4. AgentRun、AgentStep、AgentAttempt 合同

### 4.1 AgentRun（AgentRunBinding）

状态闭集：`created | leased | running | paused | succeeded | failed |
cancelled`。

字段：`agent_run_id`、`task_id`、`tenant_id`、`workspace_id`、
`workspace_generation`、`workspace_run_id`（P34.4 Workspace Run）、
`runtime_instance_id`（可空，未绑定前不存在）、
`workload_identity_thumbprint`（可空）、`node_id`、`node_fencing_token`、
`run_lease_id`、`run_fencing_token`、`state`、`created_at`。

规则（**all-or-none 状态矩阵**）：

- `run_lease_id`、`run_fencing_token`、`node_id`、`node_fencing_token`
  组成一个严格运行绑定组：四者必须同现或同缺；只有 ID 无 fencing、
  只有 fencing 无 ID、只有 Run 组无 Node 组、只有 Node 组无 Run 组
  一律拒绝；
- `runtime_instance_id` 与 `workload_identity_thumbprint` 组成第二个组，
  必须同现或同缺；
- 状态矩阵：
  - `created`：两组必须全空；
  - `leased`/`running`/`paused`：两组必须全有（P34.4 leased 态单次绑定、
    不可变更）；
  - `succeeded`/`failed`/`cancelled`：两组必须全空（P34.4 terminal 清理
    语义：lease completed/revoked + 身份清空）；
- 终态 AgentRun 不可回到运行态；checkpoint/resume 不得复活 terminal
  P34.4 Run（P34.4 复用规则 #12）；
- 恢复必须创建新的 lease/runtime/workload identity，旧
  `run_lease_id`/`runtime_instance_id`/thumbprint 一律不得恢复。

### 4.2 AgentStep

状态闭集：`pending | ready | running | succeeded | failed | cancelled`。

字段：`step_id`、`task_id`、`agent_run_id`、`step_number`（正整数）、
`plan_id`、`plan_version`、`plan_digest`、`dependencies`（逻辑引用闭集、
无重复、不得自引用、拒绝 `*`/`all`/`any`/路径技巧）、`state`、
`created_at`。`AgentPlanVersion` 本阶段仅作 identity reference（不实现
Planner）。

### 4.3 AgentAttempt

状态闭集（`AttemptState`）：`pending | ready | leased | dispatching |
running | committed | failed | unknown | cancelled`。

字段：`attempt_id`、`task_id`、`step_id`、`agent_run_id`、
`attempt_number`（正整数）、`state`、`task_lease_id`（可空）、
`task_fencing_token`（可空，与 lease 同现）、`expected_previous_state`
（TaskState 闭集）、`deadline`、`created_at`。

规则（**Attempt ↔ Task Lease 状态矩阵**）：

- `pending`/`ready`（pre-dispatch）：不得携带 `task_lease_id` /
  `task_fencing_token`；
- `leased`/`dispatching`/`running`：必须同时携带 `task_lease_id` +
  `task_fencing_token`；
- `committed`/`failed`/`unknown`/`cancelled`（terminal）：不得携带
  lease/fencing——历史 Lease identity 由 append-only lease 记录本身
  （revoked/expired/completed）作为不可变引用保留，Attempt 上不出现
  active holder 引用；`unknown` 进入 reconciliation，绝不自动 replay；
- `created_at < deadline`，且 `deadline <= task.deadline`（父子 deadline
  见 §8）；
- retry 必须创建**新** Attempt（新 `attempt_id`）并提高 Task fencing。

**作用域冻结**：

- `attempt_number` 是 **per-(task_id, step_id)** 闭集序列：同一 Task 的每个
  Step 都从 **1** 开始，同 Step 的 retry 必须 **精确等于前一个 + 1**，禁止
  重复、回退、跳号或从 1 以外的值起始（单个 `attempt_number=2` 或 `1 → 3`
  均拒绝）；不同 Step 的序列相互独立，两个 Step 都可各自从 1 开始。排序
  仅用于确定校验顺序，不得把不合法序列"整理"为合法序列；
- `task_fencing_token` 是 **per-Task**（Task 级）单调序列：同一 Task 内
  （跨所有 Step）每次 Task Lease claim 的 token 必须严格高于此前所有（按
  Attempt `created_at` 顺序校验），旧 holder 无法用旧 token 复活；**不同
  Task 拥有互相独立的 fencing 序列**，Task A 与 Task B 可各自从 token 1
  开始，不得把 task_fencing_token 拍平为系统级或 Run 级共享序列；
- Task Lease 是 Task 级逻辑授权（绑定一个 Attempt 与当前 Run Lease/
  Node fencing/Workspace generation），每次 claim 分配更高 token。

## 5. Task Lease 与 Task fencing

`TaskLeaseContract` 字段：`task_lease_id`、`task_id`、`attempt_id`、
`agent_run_id`、`run_lease_id`、`run_fencing_token`、`node_id`、
`node_fencing_token`、`workspace_generation`、`task_fencing_token`、
`state`（`active | expired | revoked | completed`）、`expires_at`、
`heartbeat_at`（completed 必填）、`created_at`。

### 5.1 复用规则（P5.2 不建第二套身份）

1. AgentRun 绑定现有 P34.4 Workspace Run；
2. Task Lease 独立于 Run Lease，但依赖当前 Run Lease：`run_lease_id`、
   `run_fencing_token`、`node_id`、`node_fencing_token`、
   `workspace_generation` 必须与 AgentRunBinding 完全一致；
3. Task Lease 不能替代 Node attestation（P34.4 每次使用仍要求 live
   `verified` attestation）；
4. Task Lease 不能替代 Run Lease（Run Lease 是 P34.4 事实，Task Lease
   只在其上叠加）；
5. **Task Lease TTL 不得晚于以下最早值**：Task deadline、Run Lease
   expiry、Node attestation expiry、Capability Grant expiry、
   Workspace/Invocation policy expiry（`LeaseExpiryBounds` 逐项比较，
   每个违反都有稳定 reason code）；
6. Node re-fence 立即使旧 Task holder 失效（`node_fencing_token` 漂移
   拒绝）；
7. Run re-fence 或 Run Lease revoke 立即使旧 Task holder 失效
   （`run_fencing_token`/lease 状态漂移拒绝）；
8. Workspace generation 改变立即使旧 holder 失效（三方 generation 必须
   一致：task ↔ run ↔ lease）；
9. Task retry 创建新 Attempt 和更高 Task fencing；
10. 不允许为恢复旧 holder 而重置 generation 或 fencing；
11. checkpoint/resume 必须创建新 Run/Attempt/Lease/runtime/workload
    identity（`validate_identity_restart`）；
12. terminal P34.4 Run 不可因 Agent Task 恢复而复活。

`task_lease_ttl_ceiling_seconds` 冻结为 ≤ P34.4 Run Lease TTL 域上限
（300s）；`active` lease 必须 `expires_at > created_at` 且 TTL ≤ ceiling。
配置收紧值（`deadline_ceiling_seconds`、`task_lease_ttl_ceiling_seconds`）
**真正作用于每个 DTO**：AgentTaskInvocation 与 TaskLeaseContract 的
解析器接收 config 值并逐实例校验，不是只验证 config 值不超过模块默认
上限；config 只能收紧，不能扩大 server-owned ceiling。

### 5.2 失效路径（离线合同必须拒绝的输入）

任何以下输入都属于 `TaskLedgerContractError`：

- Attempt ↔ TaskLease 精确**双向绑定**（两个方向都校验）：
  - `attempt.task_lease_id` 必须解析到 `attempt_id`/`task_id`/
    `agent_run_id` 完全一致的 lease；非 terminal Attempt 必须指回它引用的
    lease；Attempt 引用另一 Attempt 的 Lease 拒绝；
  - `active` TaskLease 必须绑定**恰好一个**处于 `leased`/`dispatching`/
    `running` 的 Attempt，且该 Attempt 的 `task_lease_id` 必须指回这条
    lease、`task_fencing_token` 必须与 lease 一致；`active` lease 绑定
    `ready`/`pending`/terminal Attempt（孤儿 active lease）、Attempt 清空
    lease 引用、或 Attempt 指向另一 lease 一律拒绝；
- 同一 Attempt 最多一个 `active` Task Lease（集合级扫描，先于逐条校验）；
- leased/dispatching/running Attempt 引用的 lease 必须是 `active`——
  stale/revoked/expired lease 作为 current 拒绝；
- lease 未绑定当前 run lease / 当前 run fencing / 当前 node fencing /
  当前 workspace generation（四组逐一比较）；
- lease 的 `task_fencing_token` 与 attempt 不一致；
- lease `expires_at` 与 `lease_expiry_bounds.task_lease_expiry` 不一致；
- lease TTL 超出 ceiling；`active` lease 立即过期；
- `completed` lease 无最终 heartbeat。

## 6. 预算合同

预算字段 strict：禁止负数、浮点当整数、boolean 当整数、NaN、Infinity、
超过 server-owned ceiling、未知维度、wildcard 与调用方扩大
server-owned policy。

12 个逻辑维度（闭集 `BudgetDimension`）：

`input_tokens`、`output_tokens`、`reasoning_tokens`、`total_tokens`、
`cost_micros`、`model_calls`、`tool_calls`、`wall_clock_ms`、
`artifact_bytes`、`sandbox_jobs`、`max_attempts`、`max_parallel_steps`。

每维度账本（`BudgetDimensionLedger`）区分 5 个账户：

- `limit`（server-owned 上限，config 只能收紧）；
- `reserved`（预留）；
- `committed`（已提交）；
- `released`（已释放）；
- `remaining`（派生值 = `limit - reserved`，不可是提交字段）。

基本不变量（违反即稳定 reason code）：

```text
reserved >= committed
reserved >= 0
committed >= 0
released >= 0
reserved <= limit          （committed + active_reservation <= limit）
released <= committed
remaining == limit - reserved
```

`BudgetLedgerSnapshot` 必须恰好覆盖全部 12 个维度（缺一、重复、未知维度
一律拒绝），并绑定 `policy_digest`（task 的 `budget_policy_digest`）。
checkpoint 的预算账本 digest 必须与 task 的预算策略 digest 一致，否则
拒绝。

## 7. Canonical hash 与幂等

复用项目既有 canonical JSON（`json.dumps(..., separators=(",", ":"),
sort_keys=True)`）+ SHA-256 语义，基于原始 UTF-8 字节。

8 个封闭 hash profile（`hash_profiles`，config 必须恰好覆盖闭集）：

```text
task_create
task_cancel
task_pause
task_resume_request
attempt_claim
attempt_heartbeat
attempt_finish
reconciliation_request
```

每个 profile 定义精确的稳定字段闭集（模块内 `_HASH_PROFILE_FIELDS` 是
权威）：

- `task_create`：operation、task_id、tenant/workspace/generation、
  definition/version/digest/binding、resource scope digest、budget
  policy digest、deadline；
- `task_cancel`：operation、tenant/workspace/generation、task_id、
  task_generation、expected_previous_state、cancellation_target；
- `task_pause` / `task_resume_request`：operation、
  tenant/workspace/generation、task_id、task_generation、
  expected_previous_state；
- `attempt_claim`：operation、tenant/workspace/generation、task_id、
  task_generation、agent_run_id、step_id、attempt_id、attempt_number、
  expected_previous_state、run_lease_id、run_fencing_token、node_id、
  node_fencing_token、agent_version_digest、resource_scope_digest、
  budget_policy_digest、deadline；
- `attempt_heartbeat`：operation、tenant/workspace/generation、task_id、
  task_generation、agent_run_id、step_id、attempt_id、attempt_number、
  run_lease_id、run_fencing_token、node_id、node_fencing_token、
  task_lease_id、task_fencing_token、agent_version_digest、
  resource_scope_digest、budget_policy_digest；
- `attempt_finish`：operation、tenant/workspace/generation、task_id、
  task_generation、agent_run_id、step_id、attempt_id、attempt_number、
  run_lease_id、run_fencing_token、node_id、node_fencing_token、
  task_lease_id、task_fencing_token、agent_version_digest、
  resource_scope_digest、budget_policy_digest、expected_previous_state、
  outcome、result_digest、budget_ledger；
- `reconciliation_request`：operation、tenant/workspace/generation、
  task_id、task_generation、attempt_id、reconciliation_target。

hash 必须包含与操作语义有关的全部稳定字段（含 expected previous state、
deadline、scope、budget、cancellation/reconciliation target）；**不得**包含
每次请求随机生成的 server timestamp 或随机 UUID（除非该 UUID 是预先分配
的逻辑身份，如 `task_id`/`attempt_id`）。

**进 hash 与 durable 绑定的字段分工**（不是把所有字段塞进 hash）：

| 字段 | 进 hash？ | 不进的绑定证明 |
|---|---|---|
| operation、tenant/workspace/generation、task/step/attempt identity、agent_run_id、attempt_number | 是（请求稳定字段） | — |
| agent_version_digest、resource_scope_digest、budget_policy_digest | 是（不可变身份引用，防漂移） | — |
| run_lease_id/run_fencing_token、node_id/node_fencing_token | 是（attempt_claim 提交的授权引用） | — |
| task_lease_id/task_fencing_token | 是（heartbeat/finish 的 holder 声明） | 服务端同时按 durable Task Lease 记录 + 当前 Attempt 状态矩阵重验（active、attempt 指回、fencing 一致） |
| expected_previous_state、deadline、outcome、result_digest、budget_ledger、cancellation/reconciliation target | 是（语义字段） | — |
| operation_id | 否 | Core 生成并持久化于 OperationRecord；effect 通过 operation_id 绑定，调用方无法提交 |
| workspace_run_id、runtime_instance_id、workload_identity_thumbprint | 否 | server-owned P34.4 Run/attestation 记录解析（`bind_run_runtime_identity`/`verify_run_lease_for_sandbox` 语义），workload 不提交 |
| lease expires_at/created_at/heartbeat_at | 否 | 服务器时钟签发并持久化于 lease 记录；`expires_at <= attempt.deadline <= task.deadline` 由 durable 记录校验 |
| request_hash | 否 | hash 输出本身 |

幂等语义（`classify_replay`）：

- 同 idempotency key + 同 operation + 同 canonical payload → **exact
  replay**（只返回原结果，不重复执行、不重复扣费、不创建新 Attempt）；
- 同 key + 不同 operation 或不同 payload → **stable conflict**（reason:
  `idempotency key was reused with a different operation or payload`）。

禁止任何允许调用方覆盖最终 request hash 的参数（`request_hash_override`
字段在 strict DTO 层被拒绝；`AgentTaskInvocation.request_hash` 必须等于
task_create profile 的 canonical digest）。

## 8. 状态机与 effect 语义

Effect / Provider call 状态闭集（`EffectState`）：`reserved |
dispatching | committed | failed | unknown`。

- `succeeded|failed|cancelled|committed|unknown` 等终态不能回到运行态；
- `unknown` 不能回到 `reserved|dispatching|running`；
- exact committed replay 只返回原结果（不创建新 Attempt）；
- retry 必须创建新 Attempt 且提高 Task fencing；
- cancel/deadline/revoke/pause 只阻止**新 dispatch**；已经跨过 provider
  boundary 的请求进入 reconciliation（`validate_cancel_target` /
  `validate_cancel_attempt`）；
- cancel 不得把跨过 provider boundary 的未知结果伪装成 cancelled success；
- 模型输出不构成 authoritative committed evidence——只有
  `operation_ledger | effect_ledger | audit_event` 是合法 committed
  evidence kind；`model_output` 与裸 `provider_receipt` 被拒绝。

`ProviderEffect` 字段：`effect_id`、`attempt_id`、`state`、`operation_id`、
`request_hash`、`result_digest`（仅 committed 可携带且必须携带）、
`created_at`。

**父子 deadline 关系（冻结）**：

```text
attempt.created_at < attempt.deadline
attempt.deadline   <= task.deadline
task_lease.expires_at <= attempt.deadline
task_lease.expires_at <= task.deadline
```

`task_lease.expires_at <= task.deadline` 在
`attempt.deadline <= task.deadline` 与
`task_lease.expires_at <= attempt.deadline` 同时成立时自动蕴含，但作为
独立防御性检查保留（防止未来任一约束被移除）。Task Lease 不晚于 Run
Lease / Node attestation / Capability Grant / Workspace policy 最早
expiry 的约束继续由 `LeaseExpiryBounds` 强制。

## 9. Checkpoint 限制

`CheckpointReference` 只允许引用 committed logical state：

- `committed_plan_version` / `committed_plan_digest`；
- `committed_attempt_results`（引用 committed Attempt 的逻辑结果引用，
  非空、无重复、拒绝 wildcard/路径技巧）；
- `budget_ledger`（必须与 task 预算策略 digest 一致）。

checkpoint 不得保存：active token、lease/fencing、runtime/workload
identity、PID、socket、连接、provider handle、进程内存、raw credential
或 host path（strict DTO 拒绝这些字段；负向测试覆盖）。

恢复必须创建新的 Run/Attempt/Lease/runtime/workload identity，只能复用
committed outputs，遇到 `unknown` 保持 blocked。

## 10. 验证器

`scripts/production/validate_p5_2a_task_ledger_contract.py`：

- `--validate-only`：只解析 strict contract，不读 gate、不跑 Git、永不
  返回 `ready`；report 恒输出 `contract_valid=true`（合法时）、
  `activation_allowed=false`；
- `--verify`：复用 P5.0 修补后的安全路径规则（逐分量 symlink/reparse
  检查、解析后不得指向根 `.env`、`git ls-files` tracked scope、report
  必须写到仓库外），并额外校验：
  - P34.7、P5.0、P5.1 formal state 仍为 `blocked/not_proven`；
  - 三个 Feature Gate 从当前 server process environment 显式解析；
    **任何 gate 解析为 true 都是 veto**（不同于 P5.0/P5.1A 的 blocker）；
  - `activation_requested` 必须为 false（true 即 veto）；
  - sealed contracts（合同文档、合同模块、测试、威胁模型、维护者地图、
    安全不变量）+ P34.7 decision + P5.0 admission + P5.1 registry
    contract digest；
  - migration head == `0010` 且 revision 集合 == 封存基线（出现 `0011`
    即 veto）；
  - forbidden source paths 不存在（出现 P5.2 ORM/Planner/Executor/
    Dispatcher/Scheduler/Runtime 包即 veto）；
  - OpenAPI snapshot 无 agent-invocation/agent-task/gateway-agent 端点；
  - clean-checkout provenance（dirty 即 veto）。
- safety negatives 恒为 false：`task_ledger_orm_created`、
  `task_ledger_migration_created`、`agent_invocation_api_exposed`、
  `agent_runtime_created`、`planner_created`、`executor_created`、
  `scheduler_or_worker_started`、`model_or_tool_invoked`、
  `task_execution_activated`、`root_env_accessed`、
  `business_database_accessed`、`business_database_migrated`、
  `external_network_accessed`——由模块 import 白名单（AST 测试）、源码
  边界扫描与负向测试共同证明，不是写死的字段。

**报告语义（`verification_evidence`）**：固定输出的 safety negative 不是
运行时证明。报告区分四类证据：

- `static_source_boundary`：本次 `--verify` **实际执行**的 forbidden
  source paths / migration 集合与 head / OpenAPI snapshot 扫描（checked
  与 verified 布尔值）；
- `import_ast_analysis`：模块 import 白名单由
  `tests/test_p5_2a_task_ledger_contract.py` 的 AST 测试证明，Gate 本身
  不执行（`proven_by_tests_not_by_gate`）；
- `gate_execution`：本次模式（validate_only/verify）、合同解析、
  feature gate 解析、sealed digest 校验与 evidence 引用校验的实际执行
  状态。evidence 引用校验**真实验证**每条 `status=passed` 的引用：路径
  必须是仓库内相对 regular 非链接文件（`_safe_repo_file` 拒绝绝对路径、
  `..` 穿越、symlink、reparse point、非 regular 文件与根 `.env`），按 raw
  bytes 计算 SHA-256 必须与 sealed digest 完全一致，assertions 作为机器可
  验证闭集逐项解析。报告因此拆分为
  `evidence_path_verified`、`evidence_digest_verified`、
  `evidence_assertions_verified` 与聚合 `evidence_references_verified`，
  其中只有实际执行并通过的项才为 true；`not_proven`/blocked 引用不写成
  verified，没有 passed 引用时聚合为 false。一条 passed 引用 path 缺失 /
  digest 漂移 / assertion 不匹配均为 veto（合同作废、fail closed），绝不
  无条件写 `true`；
- `direct_runtime_execution`：本 Gate 不运行 pytest/runtime
  （`not_executed_by_gate`）——运行证据只来自独立的测试与 Gate 报告，
  不写进 validator 输出冒充。

缺少 P34.7 ready evidence 时 `--verify` 正确输出
`state=blocked/not_proven`、`activation_allowed=false`、exit code 2
（与 P5.0/P5.1A 的 blocked 约定一致）。

## 11. 负向测试矩阵

`backend/tests/test_p5_2a_task_ledger_contract.py` 覆盖 50 项负例并断言
稳定 reason code，至少包括：extra field、unknown/case-drifted state、
boolean-as-int、fractional integer、负预算、预算 overflow、未知预算维度、
wildcard capability/scope、invalid UUID、invalid digest、naive datetime、
Task Lease expiry 晚于 deadline/Run Lease/Grant/attestation/policy、
missing/stale Workspace generation、missing Node/Run/Task fencing、
Task fencing 回退、Attempt number 回退、terminal resurrection、
unknown→dispatching、exact replay 改变 operation、同 key payload drift、
caller request_hash override、Browser 提交 runtime_instance_id /
workload credential、Browser JWT 进入 workload DTO、provider URL/API key、
DATABASE_URL、PostgreSQL physical locator、宿主路径、Docker socket、
PID/socket/provider handle 出现在 checkpoint、cancel 伪装 unknown、
retry 复用旧 Attempt/Task fencing、恢复旧 Run Lease/runtime/workload
identity、模型输出作为 committed evidence、parser 静默丢弃未知字段、
合同配置 symlink/junction、配置路径 realpath 逃逸、sealed digest 漂移、
根 `.env` 进入 manifest、migration 0011、未授权 Agent Runtime 源路径。

第二轮独立复核新增反例（task fencing 作用域、双向绑定、连续序列、evidence
真实校验）：per-Task fencing 正向（两 Task 各从 token 1 开始通过）、同 Task
内跨 Step fencing 回退拒绝、active lease 绑定 ready/pending/terminal
Attempt 拒绝、active lease 的 Attempt 未指回/指向另一 lease/fencing token
不一致拒绝、Attempt `attempt_number=2`（无邻居）拒绝、`1 → 3` 跳号拒绝、
两 Step 各自 `1 → 2` 通过、passed evidence path 缺失 / digest 漂移 /
assertion 不匹配均不报告 verified 且 fail closed（veto）、passed 引用真实验证
通过时聚合 `evidence_references_verified=true`。

## 12. 决策语义

P5.2A 当前不能返回 `ready`：

```text
contract_valid=true
activation_allowed=false
formal_state=blocked/not_proven
```

`--validate-only`：验证合同自身，exit 0，不读数据库、不读根 `.env`、不
查询网络、不启动 Runtime、不因合同有效而返回 Phase 5 ready。
`--verify`：在 clean checkout 上组合 P34.7/P5.0/P5.1 formal decision、
三个 Feature Gate、migration 基线/head、P5.2A sealed source digest、
forbidden source paths、未授权 P5.2 Runtime/ORM/router/migration、required
docs/tests/validator 与 clean-checkout 可复现条件；当前预期
`formal_state=blocked/not_proven`、`activation_allowed=false`、exit 2。

## 13. 未证明项与解冻条件

当前明确未证明/未实现：

- P5.2 Agent Task/Run/Step/Attempt 持久化账本（P5.2B：ORM + migration +
  事务服务 + guarded disposable PostgreSQL Gate，未实现）；
- Task Lease 发放、Task fencing、预算 reservation/commit/release 的
  runtime 实现；
- Agent Runtime、Planner、Executor、scheduler、worker、模型/工具调用；
- `/api/v1/agent-invocations`、`/api/v1/agent-tasks`、
  `/gateway/v1/agent/*`、Browser/Workload SDK；
- 生产 Core↔Runner/Broker/Gateway 激活（P34.7 production total Gate
  仍 `blocked/not_proven`）。

解冻条件（与 `docs/phase-5-agent-runtime-implementation-plan.md` 一致）：
主 Agent 独立复核 P5.2A 通过后，才允许规划 P5.2B 持久化账本；P34.7
production total Gate 独立 PASS 前，任何 P5.2 Runtime 保持冻结。本文件与
`phase5-task-ledger-contract.example.json` 必须在同一变更中同步更新并
重新封存 digest。
