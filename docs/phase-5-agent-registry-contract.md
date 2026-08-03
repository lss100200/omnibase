# P5.1A Agent Registry Contract（离线合同预检）

> 状态：`P5.1A offline contract preflight: implemented / verified`。
> P5.1 database foundation、Browser API、Runtime installation 均
> **未实现**；P5.1 production 为 `blocked / not_proven`；P5.2+ 保持 frozen。
>
> 本文件是 AgentDefinition → AgentVersion → WorkspaceAgentBinding 三层
> 离线合同的定义，不是 ORM、migration、service 或 API 的实现。任何读到
> 本文件的人都不得把"有合同"理解为"Registry 已完成"。

## 1. 目的与边界

P5.1A 只允许：离线 strict DTO/schema、closed-set JSON 合同、纯离线
validator、正/负向 fixture、威胁模型、维护者地图、CI validate-only Gate
与 clean-checkout `blocked/not_proven` evidence。

明确禁止并持续保持未实现：

- SQLAlchemy ORM model、Alembic migration、数据库表；
- AgentDefinition/AgentVersion repository/service；
- Workspace 安装、升级、禁用、回滚的实际写操作；
- Agent Invocation、Task、Run、Plan、Step、Attempt；
- Planner、Executor、dispatcher、scheduler；
- Model Gateway、Tool Gateway、Memory runtime、Skill runtime；
- Celery task、worker、queue、后台服务；
- Agent Browser API、FastAPI router、前端页面、SDK Agent 调用；
- 任何 shell/SQL/HTTP tool 或 Agent Runtime 进程。

## 2. 身份层级与逻辑标识符

合同严格区分三层身份，知道上层 ID 不自动获得下层权限：

```text
AgentDefinition
  -> AgentVersion
  -> WorkspaceAgentBinding
```

未来 binding 精确绑定：`tenant_id`、`workspace_id`、
`workspace_generation`、`agent_definition_id`、`agent_version_id`、
`agent_version_digest`、installation state、logical resource scope、
default budget policy、installer logical user ID、必要时的 Approval
logical ID。

所有 ID 都是逻辑标识符（严格小写 UUID）；逻辑 key 使用
`^[a-z0-9][a-z0-9_-]{1,63}$`。合同中禁止出现：PostgreSQL
schema/table/column、connection string、Redis key、MinIO bucket/key、
filesystem path、Docker socket、provider handle、API key、
Authorization header、Browser cookie、workload token、certificate
private key 或 host command——这些字段在 strict DTO 解析层就被拒绝
（unknown-field fail-closed）。

## 3. AgentDefinition 合同

闭集：

- definition state：`draft | active | disabled | revoked`（大小写/空格/
  未知值一律拒绝；revoked/disabled 不得被解释为 active）；
- risk level：`low | medium | high | critical`；
- installation scope：`tenant | workspace`（非空、无重复）。

字段：`schema_version`、`agent_definition_id`、`tenant_id`、
`stable_logical_key`（显式格式、无通配符）、`display_name`、
`description`（可选）、`risk_level`、`allowed_installation_scopes`、
`definition_state`、`created_by`、`created_at`、`metadata_version`
（正整数）。

## 4. AgentVersion immutable manifest 合同

版本状态闭集：`draft | sealed | deprecated | revoked`。

字段：`agent_version_id`、`agent_definition_id`、`tenant_id`、
`version`（严格 semver 风格）、`manifest_digest`、
`model_policy_id`（逻辑 profile ID）、`instructions_digest`（只保存
digest，不嵌入秘密系统提示词）、`max_context_tokens`、
`allowed_tool_ids`（逻辑 ID，禁止 `*`/`all`/`any`、无重复）、
`input_schema`/`output_schema`（受控 JSON Schema 子集）、`risk_level`、
`memory_policy_id`（逻辑 ID 或 null=disabled）、`max_concurrency`、
`default_budget`（token/cost/time/tool-call 硬上限）、`version_state`、
`created_by`、`created_at`。

不可变语义：

- sealed 版本不可原地修改：`manifest_digest` 必须等于对 canonical JSON
  原始 UTF-8 字节计算的 SHA-256（排除 `manifest_digest` 字段自身，避免
  自指方程）；任何内容变化都导致 digest 不匹配并拒绝；
- 新版本必须产生新的 version ID 与 digest；
- digest 必须是精确 lowercase 64 字符 SHA-256（大写、长度错误拒绝）；
- 不得使用经过换行归一化的解码文本冒充原始字节摘要——digest 始终基于
  canonical 重新序列化后的原始 UTF-8 字节（CRLF/LF 输入得到相同
  canonical digest，但文件原文哈希永远不等于 digest）。

受控 JSON Schema 子集（`_validate_controlled_json_schema`）：

- 关键字闭集：`type/title/description/properties/items/required/enum/
  const/minimum/maximum/exclusiveMinimum/exclusiveMaximum/minLength/
  maxLength/pattern/format/oneOf/anyOf/allOf/not/$ref/$defs/definitions`；
- `$ref` 只允许本地 JSON pointer（`#/definitions/...`、`#/$defs/...`），
  拒绝远程 URL 与文件引用；schema 无法通过自定义字段携带 command、
  env、secret 或 locator（未知关键字拒绝）；
- 递归深度上限 20；`enum` 无重复；`type` 闭集。

## 5. WorkspaceAgentBinding 合同

安装状态闭集：`pending_approval | installed | disabled | superseded |
revoked`。

字段：`workspace_agent_binding_id`、`tenant_id`、`workspace_id`、
`workspace_generation`（正整数）、`agent_definition_id`、
`agent_version_id`、`agent_version_digest`（exact digest）、
`installation_state`、`resource_scopes`（逻辑 scope，非空无重复）、
`default_budget_policy`、`installed_by`、`approval_id`（可为 null，但
按 approval policy 高风险必须存在）、`created_at`、`disabled_at`、
`superseded_by`。

exact binding 规则：

- binding 必须精确绑定注册表中一个 immutable AgentVersion：
  `(agent_version_id, agent_version_digest)` 必须与版本 manifest 的
  canonical digest 完全一致，同 version ID 配不同 digest 拒绝；
- binding 引用的 definition/version 必须存在于注册表；
- 引用 revoked definition 的 binding 拒绝；
- `disabled` 状态必须带 `disabled_at`；`superseded` 必须带
  `superseded_by`（且不能引用自身）；其他状态不得携带这些字段；
- `disabled/revoked` binding 不得被解释为可创建新 Run（离线语义）；
- upgrade 创建新 binding/version；rollback 只能显式绑定既有、未 revoked
  的 exact version/digest（离线 validator 只验证合同语义，不声称已证明
  数据库并发单赢家——跨 Tenant/Workspace 数据库拒绝属于未来 disposable
  PostgreSQL Gate，当前明确未验证）。

Approval policy（合同顶层 `approval_policy`）：

- `high`/`critical` 必须 `required`（binding 引用的版本 risk 为
  high/critical 且缺 `approval_id` 时拒绝）；
- `low`/`medium` 由合同显式声明（当前 `optional`），不做猜测。

## 6. Budget 与 wildcard 拒绝

- 合同顶层 `budget_ceilings` 是 server-owned 硬上限（只能收紧）：
  `max_tokens/max_cost_units/max_wall_clock_seconds/max_tool_calls/
  max_concurrency/max_context_tokens`；
- 版本 `default_budget` 与 binding `default_budget_policy` 每维度必须为
  正整数且 ≤ ceiling；0/负数/超上限/NaN/Infinity 一律拒绝；
- `allowed_tool_ids`、`resource_scopes`、logical key 全部拒绝
  `*`/`all`/`any` 与通配符形态。

## 7. 验证器

`scripts/production/validate_p5_1_registry_contract.py`：

- `--validate-only`：只解析 strict contract，不读 gate、不跑 Git、
  永不返回 `ready`；report 恒输出 `contract_valid=true`（合法时）、
  `runtime_activation_allowed=false`、`registry_runtime_implemented=false`、
  `database_schema_applied=false`、`public_api_exposed=false`；
- `--verify`：复用 P5.0 修补后的安全路径规则（逐分量 symlink/reparse
  检查、解析后不得指向根 `.env`、`git ls-files` tracked scope、report
  必须写到仓库外），并额外校验：P5.0 admission 与 P34.7 formal state
  仍为 `blocked/not_proven`、三个 Feature Gate 仍为 false、sealed
  contracts/fixture/threat model/maintainer map digest、migration
  revision 集合与 head、forbidden source paths 不存在、OpenAPI
  snapshot 无 agent endpoint；
- safety negatives 恒为 false：`root_env_accessed`、
  `business_database_accessed`、`business_database_migrated`、
  `external_network_accessed`、`agent_registry_runtime_created`、
  `agent_api_exposed`、`agent_runtime_activated`、`planner_activated`、
  `executor_activated`、`worker_or_scheduler_started`——这些由源码
  import 约束（模块不 import SQLAlchemy/FastAPI/Celery/网络库）、
  源码边界扫描与负向测试共同证明，不是写死的字段。

缺少 P34.7 ready evidence 时 `--verify` 正确输出
`state=blocked/not_proven`、`activation_allowed=false`、exit code 2。

## 8. 未证明项与解冻条件

当前明确未证明/未实现（`blocked_by_p34_7_production_admission`）：

- AgentDefinition/AgentVersion ORM 与数据库约束（复合外键、部分唯一
  索引、闭集 CHECK、append-only lineage trigger）；
- migration（编号不得预先假定为 `0010`，由 P34.7 最终基线合并后确定）；
- registry service 与跨 Tenant/Workspace 数据库并发单赢家；
- Browser Agent API 与 SDK 公共调用；
- Workspace 安装/升级/禁用/回滚的真实写操作；
- Agent Invocation/Task/Run/Plan/Step/Attempt 与 Planner/Executor/
  dispatcher/scheduler；
- 跨租户数据库隔离证据（未来 disposable PostgreSQL Gate）。

解冻条件（与 `docs/phase-5-agent-runtime-implementation-plan.md` 一致）：
P34.7 production total Gate 独立 PASS 后，才能按 P5.1B+ 逐级实现
database foundation 与 API；本文件与 `phase5-registry-contract.example.json`
必须在同一变更中同步更新并重新封存 digest。
