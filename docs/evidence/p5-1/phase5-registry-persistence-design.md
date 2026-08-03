# P5.1B Agent Registry Persistence Foundation — 设计判定

> 日期：2026-08-03。本判定依据当前源码、Alembic revision graph、P34.4/
> P34.6 控制面表模式与 P5.1A 合同作出；不猜测数据落点。

## 1. Agent Registry 表位于 global scope 还是 tenant scope

**global scope（`omnibase_meta`）**，与 P34.1–P34.6 全部控制面/治理实体一致。

源码证据：

- `0007_p34_4_workspace_control_plane.py` 的 17 张 Workspace 控制面表全部
  `schema=_SCHEMA`（`omnibase_meta`），tenant scope 显式 no-op；
- `control_plane`（resource_registry、operations、approval_requests、
  idempotency_records、audit_events）、`capabilities`、`workspace_data`
  同样全部位于 `omnibase_meta`；
- tenant schema（`tenant_*`）只承载用户业务数据：users、documents、
  embeddings、受控动态表、`workspace_derived_chunks_v2`；
- `backend/src/omnibase/db/models.py`：`GLOBAL_SCHEMA = "omnibase_meta"`，
  `GLOBAL_METADATA = MetaData(schema=GLOBAL_SCHEMA)`，`Base.metadata =
  GLOBAL_METADATA`。

## 2. 为什么该选择符合 schema-per-tenant 与控制平面模型

AgentDefinition/AgentVersion/WorkspaceAgentBinding 是**跨租户治理实体**
（与 workspace_templates、workspaces、resource_registry 同层），不是租户
业务数据：

- binding 需要引用 Workspace aggregate（global `workspaces`）与 Approval
  （global `approval_requests`）与 Audit（global `audit_events`）；
- 未来跨 Workspace/跨 Tenant 分享需要全局可见的 registry 元数据；
- schema-per-tenant 的隔离语义通过**每行 tenant_id 物理列 + composite
  tenant-bound FK** 表达（与 P34.4 一致），而不是把治理表复制进每个
  tenant schema。

## 3. 三个实体的 tenant_id 是否需要物理列

**需要**。与 `0007` 的 `_tenant_id_column(foreign_key=True)` 模式一致：
每张表带 `tenant_id` 物理列（FK → `omnibase_meta.tenants.id`），并建立
`(tenant_id, id)` composite unique 约束，使引用方可使用
`(fk_id, tenant_id)` composite FK——单列 UUID FK 不足以证明跨租户拒绝。

## 4. 如何用数据库约束防止跨 Tenant 引用

- 每张表：`UniqueConstraint(tenant_id, id)`；
- `agent_versions` → `agent_definitions`：
  `ForeignKeyConstraint([definition_id, tenant_id],
  [agent_definitions.id, agent_definitions.tenant_id])`；
- `workspace_agent_bindings` → `agent_definitions` / `agent_versions` /
  `workspaces`：同样的 `(id, tenant_id)` composite FK（RESTRICT）；
- `agent_bindings_integrity_guard` trigger 内跨表校验一律以
  `(id, tenant_id)` 复合条件 JOIN，任何租户不匹配都 RAISE；
- 服务层查询恒带 `tenant_id` predicate（tenant schema 解析仍由
  server-owned Tenant Registry 决定，调用方不得提交 schema 名）。

## 5. 如何与 Workspace aggregate 和 workspace_generation 对齐

- `workspace_agent_bindings.workspace_id` + `tenant_id` composite FK →
  `workspaces(id, tenant_id)`（RESTRICT）；
- `workspace_generation` 正整数 CHECK（`generation >= 1`）；
- 安装/supersede 路径锁序与 P34.4 兼容：Tenant → tenant User → Workspace
  aggregate（`SELECT ... FOR UPDATE`）→ 在锁内重读当前
  `workspaces.generation`，与请求携带的 generation 精确比对，stale
  generation 拒绝（任务 §7 并发场景 4）。

## 6. 如何实现 immutable sealed version

- `id`/`tenant_id`/`created_by`/`created_at` 身份列与
  `manifest_payload`/`manifest_digest` 等安全列在 `version_state =
  'sealed'|'deprecated'|'revoked'` 后由数据库 trigger
  `agent_versions_seal_guard`（BEFORE UPDATE）拒绝任何安全列变化；
- 状态机由同一 trigger 约束：`draft → sealed`（一次性）、
  `sealed → deprecated → revoked` 单向；`deprecated|revoked` 为终态，
  不得恢复为 `sealed|draft`；
- `manifest_digest` CHECK `~ '^[0-9a-f]{64}$'`；digest 值由服务层用
  P5.1A canonical JSON 原始 UTF-8 字节计算，数据库不自行 hash JSON 文本
  （任务 B.4）；
- 不允许 catch-and-ignore IntegrityError：唯一约束冲突与 trigger 拒绝都
  转换为受控领域错误码。

## 7. 哪些状态转换由数据库约束保证

| 实体 | 转换 | 约束 |
|---|---|---|
| agent_definitions | draft/active → disabled → revoked；revoked 终态 | CHECK 闭集 + `agent_definitions_state_guard` trigger（revoked 不可恢复；disabled 只能→revoked） |
| agent_versions | draft → sealed → deprecated → revoked | CHECK 闭集 + seal guard trigger |
| workspace_agent_bindings | pending_approval/installed → disabled → superseded/revoked；superseded/revoked 终态；disabled_at/superseded_by 与状态一致 | CHECK 闭集 + `agent_bindings_integrity_guard` trigger（含安装 payload 不可重连及 disabled_at/superseded_by 一致性） |

## 8. 哪些跨行约束必须由事务服务和锁保证

- Approval 消费（`approval_requests.state → consumed`）必须与 binding
  安装同事务原子完成；approval 有效性（state=approved、未过期、未消费、
  requester/action/workspace/risk 绑定）在锁内重验；数据库另以
  `(approval_id, tenant_id)` composite FK 和 trigger 阻断跨租户、已消费或
  身份漂移的 approval；
- 幂等记录（`idempotency_records`）的创建/比对与状态变更同事务；
- 审计（`audit_events`）写入与状态变更同事务；
- live binding 单赢家：部分唯一索引
  `UNIQUE (tenant_id, workspace_id, agent_definition_id) WHERE
  binding_state IN ('pending_approval','installed')` 由数据库保证，服务
  层在锁序内重查后给出确定性 replay/conflict；
- 并发注册/封存：`(tenant_id, stable_logical_key)` 与
  `(tenant_id, definition_id, version)` UNIQUE 由数据库保证单赢家。

## 9. 如何保证并发注册、封存、安装只有一个赢家

统一 caller-owned 事务 + 确定性锁序：

```text
Tenant -> tenant User(actor) -> Workspace aggregate(install/supersede)
  -> AgentDefinition -> AgentVersion -> live Binding
  -> IdempotencyRecord -> ApprovalRequest(first execution only)
  -> 目标行 INSERT/UPDATE -> AuditEvent
```

- 注册定义：先按 `(tenant_id, stable_logical_key)` `SELECT ... FOR UPDATE`
  锁已存在行；不存在则 INSERT（UNIQUE 兜底）；存在则做 exact replay
  比对（digest/payload 一致 → 幂等返回原行；漂移 → conflict）；
- 封存版本：按 `(tenant_id, definition_id, version)` 同法；
- 安装 binding：先锁 Workspace aggregate + 重读 generation，再按
  `(tenant_id, workspace_id, agent_definition_id)` 查 live binding；
  无则 INSERT（部分唯一索引兜底单赢家）；有则 exact replay 或 conflict。
  幂等记录在 approval 之前解析，使已经消费过 approval 的 exact replay
  能直接返回原 binding，而不会被误判为重复消费；
- supersede 使用独立 `agent_binding.supersede` 幂等记录封装“旧 binding
  终态化 + 新 binding 安装”，同 key 同 payload 返回原新 binding，同 key
  语义漂移稳定 conflict；`superseded_by` 由同租户、deferred self-FK 兜底。

## 10. 如何让 exact replay 成功，但 semantic drift replay 拒绝

复用 `idempotency_records`（global，`(tenant_id, actor_scope,
operation_name, key)` UNIQUE）：

- 首次操作：写入 `pending` 幂等记录（含规范化 request digest）→ 执行
  状态变更 → 同事务更新为 `completed` 并记录 result_ref；
- exact replay：request digest 与记录一致 → 返回原结果（不重复执行）；
- semantic drift：同 key 不同 request digest → 409 conflict；
- 幂等记录创建/比对在锁序内完成，与状态变更、审计同事务。
- P5.1B 内部 `install_binding`/`supersede_binding` 使用 `internal_full`
  profile，保持完整 DTO digest 语义。P5.1C 复用该 service 时只能选择
  `browser_install|browser_upgrade|browser_rollback` 闭集，由 service
  自行计算 operation-bound digest；supersede 额外绑定 old Binding ID。
  任意 caller-provided digest 禁止进入幂等或 Approval 校验。
- Binding Approval 的数据库 action 闭集为 `agent.install|agent.upgrade|
  agent.rollback`；应用服务必须匹配精确 operation、workspace、request
  hash、requester、risk 与单次消费状态，数据库闭集不能替代服务语义校验。

## 11. 如何保证 public DTO 永不出现物理 locator

- 本阶段**不新增任何 public API/OpenAPI/SDK**；服务只接受 P5.1A 的
  strict DTO（`phase5_registry_contract.AgentDefinition /
  AgentVersionManifest / WorkspaceAgentBinding`），其解析层拒绝
  schema/table/column/connection string/SQL/secret 等未知字段；
- ORM 映射只发生在服务内部；错误码是稳定逻辑码，数据库异常在服务边界
  转换为受控领域错误，绝不透出 raw IntegrityError、SQL 或 locator；
- audit 只记录逻辑 ID 与状态转换。

## 12. migration 的 scope 如何显式声明并 fail-closed

与 `0007`/`0009` 完全一致：`migration_schema_scope` 闭集
`global | tenant`，从 `op.get_context().config.attributes` 读取；缺失或
未知值直接 `RuntimeError`；upgrade/downgrade 中 tenant scope 显式 no-op，
global scope 才建表。下一个 revision 编号由 revision graph 计算：
当前唯一 head 为 `0009`（`discover_migration_head` 已证明无并发分支），
因此本迁移为 `0010_p5_1b_agent_registry`，`down_revision = "0009"`，
且不改写任何历史 revision。

## 结论

Agent Registry 持久化采用 **global `omnibase_meta` + 每行 tenant_id 物理
列 + (id, tenant_id) composite unique + 全 composite FK + 三张表的
BEFORE INSERT/UPDATE trigger（状态机、sealed 不可变、跨租户/跨行完整性）+
部分唯一 live-binding 索引**。该设计与 P34.4/P34.6 的既有证据一致，不
发明混合模型。

P5.1B disposable Gate 在任何 Alembic/pytest 前必须实际运行
`backend/tests/destructive_preflight.py`，验证 `omnibase_test_*` 名称、
sentinel 与受限 non-owner role；仅预检 exit 0 后才记录
`database_sentinel_verified=true`。canonical evidence 允许以同目录临时
文件和 `os.replace` 原子更新，但 source/destination symlink 必须拒绝，
并且只有 labeled container/network/volume 全部为 0 才能发布 passed。
