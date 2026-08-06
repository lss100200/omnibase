# P5.2B Task Ledger Persistence — engineering implementation and production boundary

> 日期：2026-08-04。状态：**ENGINEERING IMPLEMENTED / FORMAL GATE PENDING**。
>
> 本文档是 P5.2B（AgentTask/AgentRun/AgentStep/AgentAttempt/TaskLease 持久化
> 账本）的设计与实现边界。用户随后显式批准 P5 Fast Track，因此 ORM、
> migration `0011`、调用方事务服务与 disposable Gate 已进入 engineering
> 实现；本文仍然**不是**生产 Agent Runtime 激活授权。
>
> 正式 admission 判定见
> `docs/evidence/p5-2/p5-2b-admission-decision.md`（当前结论：
> **历史结论 BLOCKED_NOT_AUTHORIZED，已被用户授权 supersede**）。本文档严格区分“允许实现内部持久化地基”与
> “允许激活 Runtime/生产流量”：前者的机器条件见 §I.1，后者的生产条件见
> §I.2。当前授权只覆盖 engineering-only migration/ORM/service/Gate；所有
> Phase 5 Feature Gates 继续关闭，production Runtime 仍需单独批准。
>
> 本文档依据的运行时证据：`AGENTS.md`、maintenance-map、security-invariants、
> `docs/phase-5-agent-runtime-implementation-plan.md` §5、P5.2A 合同
> （`phase5_task_ledger_contract.py` + `phase5-task-ledger-contract.example.json`
> + 191 项 focused 测试）、P5.1B 持久化地基（`agent_registry/models.py` +
> `service.py` + migration `0010` + disposable Gate）、P34.4 Workspace 控制面
> （`workspaces/models.py` 17 表）、control-plane（`resource_registry`/
> `operations`/`approval_requests`/`idempotency_records`/`audit_events`）与
> capabilities（`capability_grants` 等）。文档与源码冲突时以源码为准；本轮
> 修正了 P5.2B0 草案自身的设计漂移，但未改写现有权威运行合同（见 §J）。

---

## A. 数据实体与表边界

P5.2B 只持久化 P5.2A 已冻结的**离线合同语义**；物理 schema/table/column、
MinIO object key、provider handle、connection string 与宿主路径**永不出现**
在公共 DTO、OpenAPI、SDK、错误消息或 Audit 中（沿用 P5.1B/P5.1C 的投影
白名单纪律与 locator-absence 断言测试）。

### A.1 实现表清单（11 张表；migration 0011）

| 逻辑实体 | 建议物理表（global `omnibase_meta`） | 关键列 |
|---|---|---|
| AgentTask / Invocation | `agent_tasks` | `id`, `tenant_id`, `workspace_id`, `workspace_generation`, `actor_user_id`, `agent_definition_id`, `agent_version_id`, `agent_version_digest`, `workspace_agent_binding_id`, `task_generation`, `plan_id/plan_version/plan_digest`, `deadline`, `state`, `resource_scope_digest`, `budget_policy_digest`, `request_hash`, `approval_id`, `creation_operation_id`, `created_at` |
| AgentRun | `agent_runs` | `id`, `tenant_id`, `task_id`, `workspace_id`, `workspace_generation`, `workspace_run_id`（P34.4 `workspace_runs.id`）, `runtime_instance_id`, `workload_identity_digest`, `node_id`, `node_fencing_token`, `run_lease_id`, `run_fencing_token`, `state`, `created_at` |
| AgentStep | `agent_steps` | `id`, `tenant_id`, `task_id`, `agent_run_id`, `step_number`, `plan_id/version/digest`, `state`, `created_at` |
| Step dependency edge | `agent_step_dependencies` | `tenant_id`, `task_id`, `step_id`, `depends_on_step_id`；两端必须属于同一 Task/Run；复合主键拒绝重复边，DAG 环检测仍由锁内服务校验 |
| AgentAttempt | `agent_attempts` | `id`, `tenant_id`, `task_id`, `step_id`, `agent_run_id`, `attempt_number`, `state`, `task_lease_id`（可空，terminal 清空）, `task_fencing_token`（可空，terminal 清空）, `expected_previous_state`, `deadline`, `created_at` |
| TaskLease（历史保留账本） | `agent_task_leases` | `id`, `tenant_id`, `task_id`, `attempt_id`, `agent_run_id`, `run_lease_id`, `run_fencing_token`, `node_id`, `node_fencing_token`, `workspace_generation`, `task_fencing_token`, `state`（`active/expired/revoked/completed`）, `expires_at`, `heartbeat_at`, `created_at` |
| Task fencing cursor | `agent_task_fencing_cursors` | `task_id`, `tenant_id`, `next_fencing_token`, `last_claimed_at`（每 Task 单调分配与严格时间序；复合主键） |
| Task budget ledger | `agent_task_budget_ledgers` | `task_id`, `tenant_id`, `dimension`（12 维闭集）, `limit_value/reserved/committed/released/remaining`, `policy_digest`；复合主键 `(task_id, tenant_id, dimension)` |
| Task effect ledger | `agent_task_effects` | `id`, `tenant_id`, `task_id`, `attempt_id`, `operation_id`, `request_hash`, `result_digest`, `state`（`reserved/dispatching/committed/failed/unknown`）, `created_at` |
| Task checkpoint reference | `agent_checkpoints` | `id`, `tenant_id`, `task_id`, `attempt_id`（必须 committed）, `committed_plan_version/digest`, `committed_attempt_results`, `budget_ledger` 摘要, `created_at`；**禁止** token/lease/PID/socket/provider handle |
| reconciliation record | `agent_reconciliation_cases` | `id`, `tenant_id`, `task_id`, `attempt_id`, `effect_id`（可空）, `state`（`open/resolved`）, `reason_code`, `created_at`, `resolved_at`；`unknown` 只创建 reconciliation，不改写为可 replay 状态 |

`task_generation` 直接保存在 `agent_tasks` 并由 trigger 单调约束，不创建
二选一的 `agent_task_generations` 表。`plan_id/version/digest` 在 P5.2B 只是
冻结的逻辑身份列；**不创建 `agent_plan_versions` 表**，因为 Planner 与其
持久化所有权属于 P5.3，不能用“引用占位”提前越过阶段边界。

### A.2 global / tenant scope 划分

- **global（`omnibase_meta`）**：全部 Task 账本控制面表。理由与 P5.1B
  `agent_definitions/agent_versions/workspace_agent_bindings` 一致：这些实体
  引用 global `workspaces`、`workspace_runs`、`workspace_nodes`、
  `run_leases`、`resource_registry`、`approval_requests`、
  `idempotency_records`、`audit_events`、`capability_grants`，且
  schema-per-tenant 的隔离语义通过**每行 `tenant_id` 物理列 + composite
  tenant-bound FK** 表达（与 migration `0007`/`0010` 模式一致）。
- **tenant scope**：未来若出现"Task 结果/证据"这类租户业务内容，才考虑
  tenant schema；本设计明确**不在 tenant schema 复制控制面表**。
- migration scope 闭集 `global | tenant` 沿用 `0009`/`0010` 的
  `_migration_schema_scope()` fail-closed 模式。

---

## B. 复合租户外键与身份绑定

所有能够落到 `omnibase_meta` 权威表的跨实体引用采用 **composite
`(id, tenant_id)` FK**（P5.1B 三表 + P34.4 控制面表的既有模式）。唯一
例外是 tenant schema 内的 `users.id`：global 表无法对动态 tenant schema
建立静态 FK，因此 `actor_user_id` 由事务服务锁内加载 live User 并写入
Audit，但不得伪称数据库存在跨 schema User FK。

| 引用 | 目标表 | 绑定字段 |
|---|---|---|
| 每张 P5.2B 表 → tenant | `tenants` | `tenant_id`；历史账本使用 `RESTRICT`，不得由 tenant 级联删除抹去 |
| task → workspace | `workspaces` | `(workspace_id, tenant_id)`，`workspace_generation` 锁内一致 |
| task → definition/version/binding | P5.1B 三表 | 三个独立 composite FK；Definition→Version→live Binding 锁内复核 definition/version/digest/workspace/generation 全部一致 |
| task → approval/creation operation | `approval_requests` / `operations` | 各自 `(id, tenant_id)`；action/workspace/request_hash/risk 精确绑定，低风险 approval 可空 |
| run → task/P34.4 run/node/run lease | `agent_tasks` / `workspace_runs` / `workspace_nodes` / `run_leases` | 全部 composite FK；workspace generation、runtime/workload identity、Run/Node fencing 锁内一致 |
| step → task/run | `agent_tasks` / `agent_runs` | composite FK；task/run identity 一致 |
| dependency edge → step pair | `agent_steps` | `(step_id, tenant_id)` 与 `(depends_on_step_id, tenant_id)`；trigger/服务保证两端同 Task/Run |
| attempt → task/step/run/current lease | P5.2B 主链 | composite FK；current lease FK 在 lease 表创建后 `ALTER TABLE` 添加，并设 `DEFERRABLE INITIALLY DEFERRED` |
| lease → task/attempt/run/P34.4 run lease/node | P5.2B + P34.4 | composite FK；Task/Run/Attempt/workspace generation 与三种 fencing 精确一致 |
| cursor/budget/effect/checkpoint/reconciliation → task | `agent_tasks` | `(task_id, tenant_id)`；effect/checkpoint/reconciliation 还绑定同 Task Attempt，reconciliation 的 effect 可空 |
| effect → operation | `operations` | `(operation_id, tenant_id)`；provider boundary 前必须先提交 reserved Effect/Operation/Idempotency/Audit |

规则：

- **裸 UUID 授权一律拒绝**：任何"已知 UUID 即有权"的设计无效。每次读取/写入
  都以 `(id, tenant_id)` 谓词 + 数据库 composite FK 为边界；服务层恒带
  `tenant_id` predicate（P5.1B 集成测试已证明 DB 层拒绝跨租户引用）。
- `tenant_id`/`workspace_id` 一致性：task/run/step/attempt/lease 五层
  tenant 与 workspace 必须逐层一致（P5.2A `crosses the task/run/step binding
  boundary` 语义）。
- Workspace generation：三方一致（task ↔ run ↔ lease），stale generation
  拒绝（P34.4 与 P5.2A 合同同语义）。
- Attempt ↔ current TaskLease 是有意的循环引用：不能靠任意建表顺序或两个
  互相阻塞的 `BEFORE` trigger 假装可实现。migration 先创建 Attempt（仅列，
  尚无 current-lease FK），再创建 Lease，随后添加 deferred composite FK；
  两表上的 `DEFERRABLE INITIALLY DEFERRED` constraint trigger 在事务结束时
  校验 active Attempt ↔ active Lease 双向绑定。
- 只凭 Browser cookie/JWT 或事务前角色快照不得产生任何授权（见 §E）。

---

## C. 状态机与数据库约束映射

P5.2A 冻结的闭集状态机必须由**数据库层**强制（CHECK + partial unique +
trigger），不能只靠 ORM 纪律（migration `0006` 起的安全惯例；P5.1B `0010`
的 trigger 模式是模板）：

| P5.2A 规则 | 数据库设计 |
|---|---|
| Task 10 态 / Step 6 态 / Attempt 9 态 / Effect 5 态 / AgentRun 7 态闭集 | 每表 `state IN (...)` CHECK |
| 终态不可复活 | 五个表分别使用自身闭集 trigger：Task/Run 的 `succeeded|failed|cancelled`，Step 的 `succeeded|failed|cancelled`，Attempt 的 `committed|failed|unknown|cancelled`，Effect 的 `committed|failed|unknown` 均无任何出边；不能把不同表的终态并成一条含混规则 |
| `unknown` no-replay | Attempt/Effect 的 `unknown` 是终态，**不能**更新为 `failed` 或任何可执行状态；reconciliation 必须新增 `agent_reconciliation_cases` 行，必要的重试创建新 Attempt + 更高 Task fencing token |
| AgentRun binding all-or-none | created 的 Run Lease/Node/runtime/workload 绑定全空；`leased|running|paused` 全部存在；terminal 全空。trigger 同时拒绝 terminal Run 复活或保留 runtime/workload identity |
| Task/Step plan identity | Step 的 `plan_id/version/digest` 必须与父 Task 精确一致；dependency edge 不得跨 Tenant/Task/Run、自环、重复，DAG 环在 Task/Step 锁内检测 |
| Effect result | `committed` 必须有 lowercase SHA-256 `result_digest`，其他状态不得有；`unknown` 只能创建 reconciliation，不自动 dispatch |
| Checkpoint | 只引用同 Task 的 committed Attempt；plan identity 与 Task 一致；budget policy digest 一致；禁止 token/lease/runtime/provider/locator 字段 |
| pending/ready 无 lease、运行三态必须有、terminal 清空 | `agent_attempts` CHECK：`(state IN ('pending','ready') AND task_lease_id IS NULL AND task_fencing_token IS NULL) OR (state IN ('leased','dispatching','running') AND task_lease_id IS NOT NULL AND task_fencing_token IS NOT NULL) OR (state IN ('committed','failed','unknown','cancelled') AND task_lease_id IS NULL AND task_fencing_token IS NULL)` |
| active Lease 单赢家 | `agent_task_leases` partial unique index：`WHERE state='active'` 上 `(attempt_id, tenant_id)` 唯一（同一 Attempt 至多一个 active lease）；Task 允许受 `max_parallel_steps` 约束的不同 Step 并行，因此不得错误改成每 Task 只能一条 active lease |
| Task Lease TTL ≤ ceiling（**全部状态**，Round 5） | CHECK：`expires_at > created_at`；`expires_at - created_at <= interval '300 seconds'`（值由 server-owned ceiling 配置，只能收紧）；`heartbeat_at IS NULL OR (heartbeat_at >= created_at AND heartbeat_at <= expires_at)`（Round 5） |
| Lease 不得 backdate（Round 5） | trigger/服务双保险：`agent_task_leases.created_at >= agent_attempts.created_at`（跨表，由 `agent_task_leases_attempt_bound_guard` BEFORE INSERT/UPDATE trigger JOIN 校验） |
| `completed` lease 必须 heartbeat | CHECK：`state <> 'completed' OR heartbeat_at IS NOT NULL` |
| Deadline/expiry 最小值 | Attempt deadline 不晚于 Task deadline；Task Lease expiry 不晚于 Attempt/Task deadline、live Run Lease、Node attestation、Capability Grant 与 Workspace policy 的最早 expiry。跨表/live 事实由锁内服务验证并以稳定 reason code 拒绝，不能只依赖离线 DTO |
| per-(task_id, step_id) attempt_number 从 1 连续 | **服务层 + 悲观锁**（数据库难以表达"无空洞连续"）：`(task_id, step_id, tenant_id)` 组内以 step 行锁串行化后校验 `next = max+1`；数据库侧加 `(task_id, step_id, attempt_number, tenant_id)` 唯一约束防止重复 |
| Task-wide fencing 严格递增（权威 = 历史保留 lease 账本） | cursor 行先 `FOR UPDATE`，再以 `UPDATE ... RETURNING` 分配并递增 `next_fencing_token`；`agent_task_leases` 上 `(task_id, task_fencing_token, tenant_id)` 唯一。事务回滚可以复用**从未提交、从未向 worker 暴露**的 token；任何已提交 token 永不复用。Lease/凭据只能在 commit 后交付，provider/model/tool 调用不得发生在 claim 事务提交前 |
| 不同 Task fencing 独立 | cursor 按 `(task_id, tenant_id)` 分键，天然独立 |
| 相同 UTC instant 的历史 Lease chronology fail closed | 服务层按 `task_lease.created_at` 归一化 UTC instant 排序；同 instant 拒绝（数据库层由唯一 cursor 间接保证 token 不复用，瞬时歧义由服务层裁决） |
| Lease 历史保留与定向转换 | 一条 Lease claim 只 INSERT 一次且永不 DELETE；identity、token、created/expires 不可变。只允许 `active -> completed|revoked|expired`，以及有效区间内的单调 heartbeat 更新；terminal lease 不可再更新。这里的 “append-only ledger” 指**历史 claim 行不被清除/复用**，不是声称 active 行永远不能落终态。每次定向转换同时追加 AuditEvent |

Attempt↔Lease 的跨表最终状态不能由普通 `BEFORE` row trigger 独立完成：claim
事务需要先插入引用 ready Attempt 的 active Lease，再把 Attempt 更新为 leased；
finish/revoke 则需要把 Lease 置 terminal 并清空 Attempt current pointer。两表
使用 deferred composite FK + deferred constraint trigger，在 commit 时验证：

- active Lease 恰好对应一个 `leased|dispatching|running` Attempt；
- active Attempt 指回同一 active Lease，并共享 Task fencing token；
- terminal/pre-dispatch Attempt 不保留 current lease/token；
- terminal Lease 仍保留 attempt/task/run/token/created_at 历史，不被清除。

`created_at` 是 server-owned 数据库时间，不能接受调用方值。claim 在取得 cursor
行锁后读取 `clock_timestamp()`，并要求其严格晚于 cursor.last_claimed_at；
相等或倒退时本次 claim fail/retry，不能使用 transaction-start `now()` 或
客户端时间制造 backdate。cursor 的 token 与 `last_claimed_at` 在同一事务
推进，从而使 P5.2A 的 UTC chronology 在持久化层可重建。

migration `0011` 的 trigger 与 partial unique 全部沿用 `0010` 的
`CREATE OR REPLACE FUNCTION` + `CREATE TRIGGER ... BEFORE INSERT OR UPDATE`
模式与 `populated downgrade fail-closed` 惯例。

---

## D. P5.2A Round 1–5 已修复边界（设计必须继承，不得回退）

1. Attempt 序列按 `(task_id, step_id)` 分组并从 1 连续递增（重复/回退/跳号/
   非 1 起始拒绝）；
2. Task fencing 按 `task_id` 独立，不得系统级/Run 级拍平；
3. active Attempt ↔ active TaskLease 双向绑定（active lease 必须绑定恰好
   一个 leased/dispatching/running Attempt 且 Attempt 指回并共享 token）；
4. passed evidence 必须真实校验 path/digest/assertions（`--verify` 的
   `evidence_*_verified` 拆分语义）；
5. timestamp 严格 offset 闭集校验（`Z`/`+HH:MM`/`-HH:MM`，小时 00–23、
   分钟 00–59）+ UTC normalization，溢出稳定转 `TaskLedgerContractError`；
6. append-only TaskLease 是历史 fencing 的**权威账本**（active/completed/
   revoked/expired 全部参与）；
7. terminal Attempt 清除当前 lease/token 不得抹去历史 Lease；
8. `TaskLease.created_at` 不得早于 bound `Attempt.created_at`（backdate
   拒绝）；
9. 所有 lease 状态都必须 `expires_at > created_at`；
10. 所有历史态同样受 configured TTL ceiling；
11. `heartbeat_at ∈ [created_at, expires_at]`；
12. backdated Lease 不得重排真实 Attempt chronology。

---

## E. 锁序与事务边界

确定性锁序必须保留 P34.4 与 P5.1B 的共同前缀。下面是 mutation 的总序；
具体操作可省略不参与的行，但不得交换仍参与的行：

```text
Tenant
  -> User/Actor（tenant schema，live + is_active）
  -> Workspace aggregate
  -> WorkspaceMembership（live 成员资格行）
  -> AgentDefinition
  -> AgentVersion
  -> live WorkspaceAgentBinding
  -> AgentTask
  -> AgentRun
  -> AgentStep
  -> AgentAttempt
  -> Task fencing cursor（agent_task_fencing_cursors 行锁）
  -> active TaskLease（agent_task_leases 行锁）
  -> Idempotency（idempotency_records 行锁）
  -> Approval（approval_requests 行锁 + version 乐观更新）
  -> Operation/Effect
  -> Resource（register_resource 同事务登记）
  -> Audit（audit_events append-only 同事务写入）
```

必须解释的问题：

- **为什么无 TOCTOU**：每次 mutation 在**调用者拥有的事务内**重新
  `SELECT ... FOR UPDATE` Tenant → User → Workspace → Membership →
  Definition → Version → Binding → Task → Run → Step → Attempt → cursor →
  Lease，从不信任事务前角色/成员资格快照（P5.1B
  `_lock_tenant`/`_lock_actor_user` 与 P5.1C
  `authorize_workspace_action(action, lock=True)` 的既有语义）。
- **Task Lease 单赢家**：cursor 行锁 + `agent_task_leases` 的
  `(attempt_id) WHERE state='active'` partial unique + 服务层 `rowcount`
  校验三重保证；同一 Attempt 的并发 claim 只有一个提交成功，其余得到稳定
  conflict。不同 Step 可以并行，但仍串行经过 per-Task cursor 获得不同 token。
- **fencing token 不回退不复用**：`next_fencing_token` 原子递增
  （locked row + `UPDATE ... RETURNING`），DB 唯一约束兜底；任何已提交 token
  的回退/复用触发 IntegrityError，**绝不 catch-and-ignore**。回滚事务中的
  token 从未成为权威 Lease，也不得在 commit 前返回给 worker，因此可由下一
  事务重新分配而不构成已提交 token 复用。heartbeat/finish 校验的是绑定的
  active Lease、Run/Node fencing 与 expiry；不能简单要求 token 等于 cursor
  的“最新值”，否则一个新 Step claim 会错误击穿其他仍合法的并行 Step。
- **幂等/审批/状态/Audit 同事务**：reserve idempotency → 校验/消费 approval
  （`state='consumed'` + `version` 乐观更新，`rowcount != 1` 即拒绝）→
  业务行写入 → `register_resource` → `append_audit_event`，全部在一个
  transaction 内由调用方 `commit`（服务**不自行 commit**，P5.1B 模式）。
- **IntegrityError 处理**：flush 时 `IntegrityError` 按类型区分——唯一约束
  冲突 = 确定性冲突（重复 key、重复 attempt_number、重复 fencing token、
  重复 live lease）→ 稳定 conflict；FK 违反 = 引用漂移/跨租户 → 稳定
  reference 错误；两者都**不得**被吞掉后重试写入。
- **exact replay / digest drift / stale generation 区分**：
  - exact replay：同 idempotency key + 同 canonical request hash →
    返回原结果，不再次执行副作用（P5.1B `_replay_target` 模式）；
  - digest drift：同 key + 不同 hash → `idempotency_input_mismatch`
    冲突（不 catch-and-ignore）；
  - stale generation/fencing：live 行对比 `workspace_generation` /
    `run_fencing_token` / `node_fencing_token` / `task_fencing_token` 漂移 →
    stale 错误，绝不静默覆盖。

幂等记录虽然位于总锁序后段，但 exact replay 的判定必须发生在当前 mutation
对“旧 live 状态已变化”作失败判断之前；这与 P5.1B/P5.1C 的 replay 语义一致。
不得为了早查 idempotency 而在未完成 Tenant/User/Workspace/Registry 授权
重验前返回跨租户结果。

---

## F. migration 0011 实现边界

> 下列内容最初作为设计冻结；用户授权后已由
> `backend/src/omnibase/migrations/versions/0011_p5_2b_task_ledger.py` 实现。

- **scope**：`global`（tenant scope 显式 no-op，`_migration_schema_scope()`
  fail-closed）。
- **表与创建顺序**：`agent_tasks` → `agent_runs` → `agent_steps` →
  `agent_attempts`（先建 nullable current-lease 列，不加该 FK）→
  `agent_task_leases` → `ALTER agent_attempts` 添加 deferred current-lease FK →
  `agent_step_dependencies` → `agent_task_fencing_cursors` →
  `agent_task_budget_ledgers` → `agent_task_effects` → `agent_checkpoints` →
  `agent_reconciliation_cases`。共 11 张表；不创建 P5.3
  `agent_plan_versions`，也不创建二选一 generation 表。
- **主键形态**：拥有独立逻辑身份的表使用 `id UUID PK` +
  `UNIQUE(id, tenant_id)`；cursor 使用 `(task_id, tenant_id)` 复合主键；budget
  使用 `(task_id, tenant_id, dimension)`；dependency edge 使用
  `(step_id, depends_on_step_id, tenant_id)`。不能用“每表都有 id”掩盖真实 DDL。
- **共同约束**：状态闭集 CHECK；sha256 列
  `~ '^[0-9a-f]{64}$'` CHECK；可落到 global 表的引用使用 composite
  `(ref_id, tenant_id)` FK（历史账本 `ondelete RESTRICT`）。tenant User 只做
  锁内 live 重验，不伪造跨动态 schema FK。
- **UNIQUE**：`agent_attempts (task_id, step_id, attempt_number, tenant_id)`；
  `agent_task_leases (task_id, task_fencing_token, tenant_id)`。Task create 的
  exact replay 由现有 `idempotency_records` 的 actor scope + operation + key +
  canonical request hash 决定，不凭空发明 P5.2A 未冻结的 natural key。
- **partial unique**：`agent_task_leases_active_attempt_uq ON
  agent_task_leases (attempt_id, tenant_id) WHERE state = 'active'`。
- **trigger/constraint trigger**：状态机 guard ×5
  （tasks/runs/steps/attempts/effects）；lease `active -> terminal` 与 identity
  immutability guard；lease-attempt no-backdate guard；heartbeat/TTL CHECK；
  task_generation 单调 guard；dependency same-task/run guard；checkpoint
  committed-attempt guard；Attempt↔active Lease 双向关系使用 deferred
  constraint trigger，在事务最终状态校验，不能用互相阻塞的普通 BEFORE
  trigger。
- **budget CHECK**：12 个 dimension 必须各恰好一行；`0 <= committed <=
  reserved <= limit_value`、`0 <= released <= committed`、`remaining =
  limit_value - reserved`，policy digest 与 Task 冻结值一致。所有 reserve/
  commit/release 使用锁行/CAS，不能先调用 provider 再补记预算。
- **upgrade 顺序**：按上述建表顺序消解 Attempt↔Lease 的 DDL 环；普通
  CHECK/FK/index 在目标表存在后安装，最后安装跨表 deferred constraint
  trigger。migration 必须在 fresh sentinel 中证明 claim、finish、revoke 三条
  路径都能提交，反向缺边在 commit 时 fail closed。
- **downgrade 策略**：populated downgrade **fail closed**——任一 P5.2B 表
  非空即 `RAISE 'P5.2B downgrade refused' USING ERRCODE='55000'`（`0010`
  模式）；空表时才按依赖逆序 drop trigger → index → table。
- **global/tenant revision 行为**：global upgrade 创建 11 表；tenant scope
  显式 no-op，但 Alembic 仍把每个 retained tenant schema 的 version table
  从 `0010` 推进到 `0011`。global populated downgrade 中止并保留 revision
  `0011`；仅在 global 表全空时允许 drop 并回到 `0010`。tenant downgrade
  仅执行 no-op revision 回退，不创建/删除业务表。
- **migration head 的精确语义**：工程开始前的授权基线必须是唯一 head
  `0010`。一旦真正加入 `revision='0011', down_revision='0010'`，新的唯一 head
  必须是 `0011`；同一变更必须更新 P5.0/P5.1A/P5.2A 的 migration baseline/
  sealed digests 与对应测试。不能写成“创建 0011 后 head 仍为 0010”。
- **授权边界**：用户已单独授权 engineering-only `0011`。P34.7/P5.0/P5.1
  production 未 ready 与 P5.2A 的预期 `blocked/not_proven` 继续禁止 Runtime/
  production activation；Feature Gate 开启与 production wiring 仍需要新的
  单独授权。

---

## G. 内部服务边界（TaskLedgerPersistenceService）

- **caller-owned transaction**：服务只接收 session，不自行 commit/rollback；
  幂等、审批、状态机、Effect、Audit、Resource 登记由调用方统一提交。
- **live 重验**：事务内 `FOR UPDATE` 重载 Tenant/User/Workspace/
  Membership/Definition/Version/Binding（P5.1B 同款），不接受裸 tenant_id
  或事务前快照。
- **server-generated logical IDs**：task/run/step/attempt/lease/effect/
  checkpoint id 由服务生成（`gen_random_uuid()` 或服务端 UUID）；调用方
  提供的 id 只允许合同声明为 caller-preassigned 的字段（如 `task_id`）。
- **exact canonical request hash**：闭集 hash profile（P5.2A 8 profile），
  canonical JSON 原始 UTF-8 字节，服务自行计算，绝不接受调用方 digest
  override。
- **approval 单次消费**：`approval_requests` 行锁 + `state='consumed'` +
  `version` 乐观更新，`rowcount != 1` → 拒绝（P5.1B 模式）。
- **Idempotency exact replay**：同 key 同 hash 返回原结果；同 key 异 hash →
  冲突；不 catch-and-ignore 唯一约束冲突。
- **Audit 同事务**：`append_audit_event` 与业务状态同一事务，Audit 失败即
  业务失败（append-only DB 层保护由 migration `0006` 安装）。
- **register_resource 同事务**：AgentTask 必须登记为逻辑 Resource；需要独立
  授权/分享的长期 Checkpoint 可登记。Attempt/Lease/Effect 默认是 Task 的
  内部子账本事实，不为每条短期记录制造独立 Resource；它们始终通过 Task
  resource scope、Operation、Audit 与 tenant/workspace FK 治理。
- **不暴露 locator**：公共投影只含逻辑 id/状态/预算/安全 reason code；
  物理 schema/表/列、MinIO key、provider handle、connection string、宿主
  路径永不出现（P5.1B/P5.1C locator-absence 测试模式复用）。
- **不运行 Agent**：服务不调用模型、工具、Sandbox、Planner、Executor、
  scheduler 或 worker。它可以在授权后的 caller-owned transaction 中持久化
  synthetic/production claim，但不把提交前 Lease 暴露给 workload，也不自行
  dispatch。
- **provider boundary 两阶段**：claim/budget/effect reserve、Operation、
  Idempotency 与 Audit 必须先在数据库事务中提交；只有 commit 成功后，独立
  Runtime 才能拿到 Lease 并跨 model/tool/provider boundary。结果提交使用新
  事务重新验证 active Lease、Run/Node/Task fencing、deadline、Grant 与预算。
  crash/timeout 无法证明结果时 Effect/Attempt 进入 `unknown` + reconciliation，
  不自动 replay，也不把 cancellation 伪装成成功。

---

## H. disposable Gate 实现

- **独立 Compose project**：名称必须 `omnibase-p52b-*`；
- **数据库**：`omnibase_test_p52b_*` sentinel 命名；**受限非 owner role**
  运行迁移与应用（`destructive_preflight` 前置，Makefile 守卫）；
- **从空数据库迁移到 head**：Gate 必须期待 global 与 retained tenant version
  table 全部为 `0011`，并验证空表 downgrade→`0010`→re-upgrade→`0011`；
- 必测矩阵（每条断言稳定 reason code，不 catch-and-ignore）：
  cross-tenant 引用拒绝、并发 Task/Lease single-winner、stale
  generation/fencing 拒绝、exact replay 幂等、digest drift 冲突、
  approval 单次消费、Audit append-only、rollback 原子性（无部分状态）、
  unknown no-replay、历史 Lease chronology（含相同 UTC instant fail
  closed）、populated downgrade fail-closed、backdated lease 拒绝、
  heartbeat 窗口、per-(task_id, step_id) attempt_number 连续；
- **cleanup proof**：容器/网络/卷清理计数 0/0/0；
- **安全边界**：root `.env` 未访问；业务数据库未访问/迁移；Gate 只产出
  evidence，不启动任何 Runtime。

---

## I. 解冻条件（工程与生产必须分开判定）

### I.1 P5.2B 内部持久化工程 admission

以下条件全部成立才允许 `AUTHORIZED_FOR_ENGINEERING`：

1. 主 Agent 独立复核 P5.2A，确认 `contract_valid=true`、
   `sealed_digests_verified=true`、vetoes=`[]`；P5.2A 的 formal state 仍为
   `blocked/not_proven` 是当前**预期结果**，因为 validator 自身明确列出
   “persistence ledger is not implemented”。要求它在实现 P5.2B 之前先变成
   ready 会形成循环依赖，禁止作为工程前置条件；
2. clean source baseline，唯一 migration head=`0011`，无未授权
   migration revision 或 P5.2 runtime package；
3. 三个 Phase 5 Feature Gate 仍为 `false/false/false`，
   `activation_requested=false`；
4. P5.0/P5.1A/P5.2A 当前 sealed digest 与 source-boundary 校验通过，
   Critical Veto=0；
5. **用户显式授权**创建 migration `0011` 与 P5.2B ORM/事务服务/disposable
   Gate。设计文档、独立审查或 `blocked/not_proven` 报告都不构成该授权。

P34.7/P5.0/P5.1 production 尚未 ready 不阻止“engineering-only、默认关闭、
disposable sentinel 验证”的内部持久化工作；此前 P5.1B/P5.1C 也是这种边界。
它们仍然阻止任何 Runtime/生产激活。

### I.2 Runtime / production activation

在 I.1 之外还必须全部满足：P34.7 formal state=`ready`；P5.0 admission 与
P5.1 production=`ready`；P5.2B disposable PostgreSQL Gate 通过且 source/evidence
sealed；migration/head/合同已一致更新为 `0011`；Task Lease 发放、预算、
reconciliation 与 Runtime 独立 Gate 已实现并通过；三个 Feature Gate 按正式
release decision 显式开启。任一缺失时 production 仍为
`blocked/not_proven`，且默认 wiring 必须 unavailable/rejecting。

当前（2026-08-04）用户已显式授权 engineering-only P5 Fast Track：
`0011`/ORM/事务服务/disposable Gate 可以实现和封存。P34.7、P5.0、P5.1
的 blocked 状态继续是生产 blocker，不是内部工程的循环前置条件。生产
Runtime、Feature Gate 开启、worker/Planner/tools/multi-Agent 仍未授权。

---

## J. 文档漂移核查

按"文档与可执行行为冲突时以源码为准"的规则核查了 roadmap、
implementation plan §5、P5.2A 合同文档、threat-model 补充、handover
P34.7/P5.0/P5.1A/B/C/P5.2A Round 1–5 记录与 migration `0009`/`0010` 源码：
本轮独立复核发现原始 P5.2B0 草案把 P5.2A formal ready 写成 P5.2B 工程
前置条件，与 P5.2A validator 的固定 blocker 及合同文档“独立复核后允许规划
P5.2B”冲突；同时发现 DDL 环、锁序和 Lease immutability 表述不闭合。以上
已在设计中纠正；Fast Track 实现又同步更新了 maintenance-map、security
invariants、ai-maintainer-map 与 Phase 5 example contracts，并重算 sealed
digests。正式 clean-source Gate 与 evidence seal 决定本轮是否工程封板。

---

*本文记录 P5.2B engineering 实现边界；它不构成 production ready 或 Runtime
激活声明。*
