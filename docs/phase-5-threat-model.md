# Phase 5 威胁模型：P5.0 Admission Gate 与 Phase 5 解冻边界

> 状态：P5.0 admission gate 已实现（三个独立、默认关闭、fail-closed 的 Phase 5
> Feature Gate + P34.7 Evidence Manifest 验证器）。Phase 5 Runtime 本身（Planner、
> Executor、Agent Runtime、Multi-Agent DAG、Memory Compiler、Skill runtime、MCP）
> 仍是 `PLANNED / FROZEN`，本文件**不**为任何 Phase 5 运行时代码建立安全主张。
>
> 范围：P5.0 只允许威胁模型、配置合同、Feature Gate、Evidence Manifest
> validator、strict DTO/schema、负向 fixture、单元测试、clean-checkout
> validator、文档、维护者地图与 CI Gate。本威胁模型只覆盖这些 admission
> 面本身，以及"Phase 5 是否允许开始"这一决策的完整性。
>
> 安全目标：即使仓库被错误配置、环境变量被污染、证据文件被替换或 checkout
> 不干净，P5.0 也必须可复现地给出 `blocked/not_proven`，且永远不得把该
> 决策转化为任何 Agent/Planner/Executor 的启动、队列、worker 或调度器。

## 1. 资产与信任边界

受保护资产：

- P34.7 production total Gate 的正式状态（`ready | blocked/not_proven |
  invalid/veto`）与 `docs/evidence/p34-7/production-readiness-decision.md`；
- 三个 Phase 5 Feature Gate 的最终解析值（`AGENT_RUNTIME_ENABLED`、
  `AGENT_PLANNER_ENABLED`、`MULTI_AGENT_ENABLED`）；
- P5.0 admission 合同（`deployment/production/phase5-admission.example.json`）
  及其 canonical digest；
- Evidence Manifest 引用的 sealed 文件与 SHA-256（OpenAPI snapshot、SDK
  合同版本、production composition、runbook、P34.7 decision）；
- clean-checkout source provenance（commit/tree/dirty scope/source manifest）；
- 迁移链头部（Alembic revision 0001→0009）与 SDK 版本事实；
- admission report（`blocked/not_proven`、blockers、vetoes、safety
  negatives）作为未来 deployment controller 的输入。

信任区域：

```text
Zone O  Operator / server environment
  └─ Feature Gate 环境变量的唯一权威来源；不得来自客户端输入

Zone R  Public repository checkout
  └─ tracked source、migration、SDK、evidence 文件；必须 clean 且与 remote 匹配

Zone V  External production evidence
  └─ Runner 12/12、四条 production roundtrip、provider recovery、
     non-disposable tenant/RAG、双成员 Overlay/DERP、容量/SLA；
     当前全部 `not_proven`，只能由独立采集的 sealed artifact 进入

Zone A  Phase5AdmissionGate / validator
  └─ 只读决策层；无副作用，不启动任何运行时组件
```

决策层（Zone A）是纯函数式验证器：它读配置合同、环境变量映射、Git 状态与
sealed 文件，输出一个 JSON report。它不持有数据库 session、不导入 Agent
Runtime 代码、不创建进程/队列/网络连接，也没有任何"验证通过后自动启动"
的代码路径。报告中 `phase5_runtime_activated` 恒为 `false`。

## 2. 威胁主体

1. 配置错误的 operator（把 gate 写成 `TRUE`、`1`、`yes` 或带空格）；
2. 恶意或受污染的部署环境（`AGENT_*` 环境变量被注入）；
3. 篡改 P5.0/P34.7 合同或 sealed evidence 文件的本地攻击者；
4. 在 dirty checkout 或错误 remote 上误跑验证器的维护者；
5. 试图把"验证器存在"解释为"Phase 5 已解冻"的后续 Agent；
6. 替换 migration 文件、SDK 版本或 OpenAPI snapshot 的供应链攻击；
7. 试图让 validator 读取根 `.env` 或业务数据库的路径/参数注入。

宿主 operator 不是完全不可对抗攻击者，但其所有输入都必须经过 strict DTO
解析与 sealed digest 校验；任何输入都不能使 gate 意外打开。

## 3. 安全不变量（P5.0 生效）

1. **GATE-INDEPENDENCE**：三个 Feature Gate 独立解析；不存在一个总开关
   隐式开启另外两个。缺失、空值等于 `false`；未知值（含大小写、空白、
   非标准字符串）fail-closed 报配置错误，绝不猜测。
2. **GATE-DEPENDENCY**：`AGENT_PLANNER_ENABLED=true` 而
   `AGENT_RUNTIME_ENABLED=false` 必须拒绝；`MULTI_AGENT_ENABLED=true` 而
   Planner/Runtime 任一为 `false` 必须拒绝。
3. **GATE-DISABLED-BY-CONTRACT**：P5.0 完成定义要求三个 gate 保持关闭；
   admission 合同内声明任何 gate 为 `true` 都是无效合同（veto）。
4. **P34-7-GATE**：即使三个 gate 都显式 `true`，只要 P34.7 Evidence
   Manifest 不是 `ready`，P5.0 仍必须 `blocked/not_proven`。
5. **DECISION-ONLY**：Gate 只返回 admission decision；任何代码路径都不得
   自动启动 Agent、Planner、Executor、queue、worker 或 scheduler。
6. **SEALED-EVIDENCE**：Evidence Manifest 中的每个 sealed 文件都必须与
   记录 SHA-256 精确一致；`passed` 证据必须同时存在路径、摘要与非空
   assertions；`not_proven` 永不计数为通过。
7. **CLEAN-PROVENANCE**：`--verify` 只接受 clean checkout、精确匹配的
   Git remote 与 tracked source manifest；dirty 工作树是 veto。
8. **CHAIN-HEAD**：迁移链必须是完整连通、无循环且恰好有一个 head 的链，
   该 head 必须等于合同声明的 `expected_revision`（当前 `0010`）。
9. **NO-SECRET-NO-DB**：validator 永不读取根 `.env`、凭据、证书载荷、
   数据库或业务存储；report 固定输出 `root_env_accessed=false`、
   `business_database_accessed=false`、`business_database_migrated=false`。
10. **VETO-ZERO-REQUIRED**：合同要求 `critical_veto.expected = 0`；实际
    veto 计数与合同不一致或任何 veto 存在时不得 `ready`。

## 4. 攻击面与控制

### 4.1 Feature Gate 解析

威胁：`AGENT_RUNTIME_ENABLED=TRUE`、`1`、` yes`、`enabled`、`null` 被当作
真值；`bool("false")` 一类不安全解析；Planner/Multi-Agent 依赖关系被绕过。

控制：

- 只接受精确 `"true"` / `"false"`；缺失与空字符串解析为 `false`；
- 非字符串（如程序化传入 `True`）直接配置错误；
- 依赖规则在解析层强制：Planner→Runtime，Multi-Agent→两者；
- 解析结果只进入 report 与 blockers/vetoes，不进入任何启动路径。

### 4.2 合同与 DTO

威胁：向 `phase5-admission.example.json` 注入额外字段、把 gate 声明为
`true`、`critical_veto.expected` 非零、伪造 evidence 状态、把路径指向
根 `.env` 或仓库外。

控制：

- strict object/string/bool/list 解析，未知字段拒绝；
- `feature_gates` 闭集且必须全 `false`；`critical_veto.expected` 必须为 0；
- evidence 闭集状态机（`passed|blocked|not_proven`）与 P34.7 共用；
- 所有路径规范化、拒绝绝对路径、`..`、drive letter 与根 `.env`；
- 从仓库根到 sealed 文件的每个路径分量都必须是普通目录/文件，拒绝任何
  symlink/junction/reparse 别名，避免通过仓库内别名解析或散列根 `.env`。

### 4.3 Evidence 与 sealed 文件

威胁：替换 evidence JSON、篡改断言、删掉 digest、把旧 artifact 冒充当前
生产证据、让 `not_proven` 计数为通过。

控制：

- `passed` 证据必须同时有 path + SHA-256 + 非空 assertions；
- `--verify` 逐字节重算 SHA-256 并校验嵌套断言路径；
- `not_proven`/`blocked` 且 `required_for_activation` 的证据一律进入
  blockers，永不进入 `passed_evidence`；
- 当前 checked-in 合同中 Runner/Broker/Gateway/Overlay/Workspace-data/
  provider 生产证据全部为 `not_proven`，因此 P5.0 正确输出
  `blocked/not_proven`。

### 4.4 源码 provenance 与 dirty scope

威胁：dirty checkout、未跟踪源码、错误 remote、被替换的 tracked 文件、
把 P5.0 决策写成"已验证"但实际未在干净 checkout 上运行。

控制：

- `--verify` 调用 `build_git_source_provenance`：校验 commit/tree、remote
  与合同一致、`status --porcelain` 为空、只哈希 `git ls-files` 范围内的
  tracked 文件（永不触碰被 ignore 的根 `.env`）；
- dirty 是 veto；`validate-only` 与 `--verify` 严格分离，validate-only
  从不产生 `ready`；
- 提交后的 formal verify 必须在 fresh clean checkout 上运行，且当前正确
  结果是 `blocked/not_proven`。

### 4.5 迁移链、SDK 与 runbook 漂移

威胁：Alembic 链多 head、head 与合同不符、SDK 版本漂移、OpenAPI snapshot/
composition/runbook digest 漂移后仍声称 manifest 一致。

控制：

- 只读解析 migration 文件的 `revision`/`down_revision`，不导入任何
  migration 代码、不连接数据库；
- 迁移链必须完整连通且无循环，并且恰好一个 head 等于
  `expected_revision`，否则 veto；
- SDK 版本从 `pyproject.toml`/`package.json` 解析并与合同比对；
- OpenAPI snapshot、production composition、runbook、P34.7 decision 全部
  sealed digest 比对，漂移即 veto。

### 4.6 决策报告与 safety negatives

威胁：report 声称 root `.env` 被读取、业务数据库被迁移、或 runtime 被
激活却不被发现；把 blocked 结果伪装成 pass。

控制：

- report 固定输出五组 safety negatives：
  `root_env_accessed=false`、`business_database_accessed=false`、
  `business_database_migrated=false`、`hostile_code_executed=false`、
  `phase5_runtime_activated=false`；
- exit code 语义与 P34.7 一致：`0`=有效合同或 ready、`2`=blocked、
  `1`=invalid/veto；validator 自身没有任何自动启动或写数据库的路径。

## 5. P5.0 admission 攻击测试矩阵

| ID | 攻击 | 预期结果 | 自动化层 |
|---|---|---|---|
| P50-GATE-01 | gate 缺省/空值 | 解析为 false | unit |
| P50-GATE-02 | `TRUE`/` true`/`1`/`yes`/`on`/`null` | 配置错误，fail-closed | unit |
| P50-GATE-03 | 程序化传入 bool | 配置错误 | unit |
| P50-GATE-04 | Planner=true 而 Runtime=false | 依赖冲突拒绝 | unit |
| P50-GATE-05 | Multi-Agent=true 而 Planner/Runtime 任一 false | 依赖冲突拒绝 | unit |
| P50-GATE-06 | 三 gate 全 true + P34.7 非 ready | 仍 blocked/not_proven | unit |
| P50-GATE-07 | 合同内 gate 声明 true | 无效合同（veto） | unit |
| P50-CTR-01 | 合同额外字段/错误 phase/schema | 解析拒绝 | unit |
| P50-CTR-02 | `critical_veto.expected` 非 0 | 解析拒绝 | unit |
| P50-CTR-03 | evidence/source path 直接或经 symlink/reparse 指向根 `.env` | 解析/验证拒绝 | unit |
| P50-EV-01 | passed 证据缺 path/hash/assertions | 解析拒绝 | unit |
| P50-EV-02 | sealed evidence digest 漂移 | veto | unit |
| P50-EV-03 | evidence 断言漂移 | veto | unit |
| P50-EV-04 | `not_proven` 证据 | blocker，不计数为通过 | unit |
| P50-SRC-01 | dirty checkout | veto | unit/CLI |
| P50-SRC-02 | remote 与合同不符 | invalid/veto | unit/CLI |
| P50-SRC-03 | tracked scope 被替换 | source manifest 变化，dirty/veto | CLI |
| P50-MIG-01 | migration 多 head、隐藏循环/断链或 head 漂移 | veto | unit |
| P50-MIG-02 | down_revision 引用不存在 revision | veto | unit |
| P50-SDK-01 | Python/TS SDK 版本漂移 | veto | unit |
| P50-SDK-02 | OpenAPI snapshot digest 漂移 | veto | unit |
| P50-DOC-01 | production composition/runbook digest 漂移 | veto | unit |
| P50-DOC-02 | P34.7 decision digest 漂移 | veto | unit |
| P50-RPT-01 | report safety negatives 被改写 | 固定 false，测试断言 | unit |
| P50-RUN-01 | validator 被要求启动 runtime | 无任何启动路径；源码审计 | source audit |

## 6. 分批 Gate 与完成定义

P5.0 只允许：威胁模型、配置合同、三个独立 Feature Gate、Evidence
Manifest validator、strict DTO/schema、负向 fixture、单元测试、
clean-checkout validator、文档、维护者地图与 CI Gate。

P5.0 完成定义（与 `docs/phase-5-agent-runtime-implementation-plan.md` 一致）：

1. P34.7 所有 production Gate 已独立复验、Critical Veto 为 0、Evidence
   Manifest 与当前源码一致；
2. 三个 Feature Gate 仍保持关闭（`false`）；
3. focused tests、Backend non-integration、Mypy、Ruff、维护者地图与
   benchmark validator、`git diff --check`、secret scan 与静态验证全部通过；
4. fresh clean checkout 上 `--verify` 可复现 `blocked/not_proven`。

任何缺失外部证据时，正确输出是 `blocked/not_proven`；不得把该结果改写为
P5.0 PASS，也不得据此解冻 Phase 5 Runtime。

## 7. 非目标与残余风险

非目标：

- 不实现或预装 AgentDefinition/AgentVersion ORM、Agent Runtime、Planner
  service、Executor、Task dispatcher、Run scheduler、Tool execution、
  Model provider、Memory Compiler、Skill runtime、Specialist spawn、
  Multi-Agent DAG scheduler、MCP 或任意 shell/SQL/HTTP 工具；
- 不新增任何 Agent API route、Browser Agent UI、后台 worker 或 Celery task；
- 不以"代码存在但 Feature Gate 关闭"为理由实现上述内容；
- 不验证 Phase 5 运行时的提示注入、记忆泄露或 Skill 逃逸——那些属于
  P5.3+ 的威胁模型，当前保持计划状态。

残余风险：

- admission 决策依赖于 operator 在干净 checkout 与真实证据下运行
  `--verify`；validator 无法防止 operator 在 dirty 状态下伪造 report。
- 环境变量 gate 的最终权威在部署环境；本仓库只提供解析与决策逻辑，无法
  阻止部署方在 Phase 5 运行时实现完成后自行打开 gate（那属于 P5.1+ 的
  逐级授权边界）。
- P34.7 decision 文档与 P5.0 合同都由 digest 封存，但未来 P34.7 证据
  更新时必须在同一变更中同步更新 P5.0 合同与 re-verify，不能只改文档。

---

# Phase 5 威胁模型补充：P5.1A Agent Registry Contract Preflight

> 状态：P5.1A 离线合同预检已实现。P5.1B 内部持久化地基已实现（见下方
> 补充）；Browser API、Runtime installation 仍未实现；P5.1 production 为
> `blocked/not_proven`；P5.2+ 保持 frozen。本补充只覆盖 P5.1A 合同与
> validator 自身的安全属性，不建立任何 Agent Runtime 安全主张。

## P5.1A 资产与信任边界

受保护资产：

- AgentDefinition/AgentVersion/WorkspaceAgentBinding 三层离线合同与
  canonical digest（基于原始 UTF-8 字节，排除 `manifest_digest` 自指）；
- sealed contracts/fixture/threat model/maintainer map digest；
- P5.0 与 P34.7 formal state 与 decision digest；
- migration revision 集合（0001–0009）与 head；OpenAPI snapshot；
- safety negatives（10 项）作为"未实现/未运行"的离线证明。

信任区域与 P5.0 相同；决策层 `RegistryContractGate` 是纯函数验证器，无
数据库/网络/进程副作用，模块 import 白名单（stdlib + omnibase.production）
由 AST 测试强制。

## P5.1A 威胁与控制

| 威胁 | 控制 |
|---|---|
| 把合同当实现（"有合同=Registry 完成"） | report 恒输出 `registry_runtime_implemented=false`、`database_schema_applied=false`、`public_api_exposed=false`；源码边界扫描拒绝 forbidden 包 |
| binding 用不同 digest 绑定同一 version、引用未知 definition/version | `_validate_registry_references` exact digest 校验，漂移即拒绝 |
| 重复逻辑 ID/key/semver 造成索引覆盖或歧义 | definition/version/binding ID 唯一；tenant logical key 与 definition semver 复合唯一 |
| version/binding 跨 Tenant，或 binding 把 definition 与别的 version 拼接 | definition→version→binding 每条边复核 tenant 与 definition identity；Workspace scope 显式授权 |
| version 降低 definition risk 绕过 Approval | risk 只能保持或提高；Approval 使用已验证 version risk |
| high/critical 安装缺 Approval | `approval_policy` 强制 required；缺 `approval_id` 拒绝 |
| 预算 0/负数/超 ceiling/NaN/Infinity | `_strict_positive_int` + ceiling 校验 + `parse_constant` 拒绝非有限数 |
| wildcard/重复/空 tool ID、scope | 逻辑 key 闭集 + 去重 + 保留字拒绝 |
| JSON Schema 远程/文件 `$ref`、自定义命令字段 | 受控关键字闭集 + 本地 pointer 白名单 + 深度上限 |
| 借 `exclusiveMinimum`/`exclusiveMaximum` 等允许关键字嵌套对象 | 所有数值边界关键字必须是有限 number 且拒绝 bool/object |
| digest 大写/长度错误/内容漂移、CRLF 冒充原始字节 | 严格 lowercase 64 hex；canonical 重新序列化原始 UTF-8 字节 |
| 合同/evidence 经 symlink/reparse 指向根 `.env` | P5.0 修补后的逐分量 `_safe_repo_path` 规则复用 |
| dirty checkout / remote 不符 | clean-checkout veto；remote 精确匹配 |
| gate 被打开或写成 TRUE/yes/on/1 | gate true → blocker；truthy token → veto；合同内 gate true → 无效合同 |
| CLI `--verify` 忽略当前进程 Feature Gate 环境 | validator 显式采集三个 server-owned env 名并交给 fail-closed parser |
| 偷偷新增 ORM/migration/router/Celery/runtime 包 | forbidden source paths + migration revision 集合漂移 → veto |
| OpenAPI snapshot 被加入 agent endpoint | snapshot digest + path 扫描 → veto |
| report 写到仓库内 | `_write_report` 强制仓库外 |
| config 父目录或既有 report 目标是 symlink/reparse | 逐分量 `lstat`，拒绝 link/reparse 后才读写；不跟随既有 report symlink |
| `not_proven` 被计为 passed | evidence 处理与 P5.0 一致，只进 blockers |
| report 声称 runtime activated | 10 项 safety negatives 恒 false，由源码边界/import 约束/负向测试证明 |

## P5.1A 完成定义

1. 离线 strict DTO/closed-set 合同、validator、正/负向 fixture、威胁
   模型、维护者地图与 CI validate-only Gate 全部通过；
2. `--verify` 在 fresh clean checkout 可复现 `blocked/not_proven`
   （exit 2，veto 0）；
3. 未实现任何 ORM/migration/service/API/Runtime；三个 Feature Gate 保持
   false；P34.7/P5.0 保持 `blocked/not_proven`。

任何缺失外部证据时正确输出是 `blocked/not_proven`；不得把 P5.1A 写成
P5.1 PASS，也不得据此解冻 Phase 5 Runtime。

---

# Phase 5 威胁模型补充：P5.1B Agent Registry Persistence Foundation

> 状态：P5.1B 内部持久化地基已实现（ORM + migration 0010 + 内部事务
> service + disposable PostgreSQL Gate）。它不是公开 API，不建立任何
> Agent Runtime/安装/编排安全主张；P5.1 production 保持
> `blocked/not_proven`，P5.2+ 保持 frozen。

## P5.1B 资产与信任边界

受保护资产：

- `agent_definitions`/`agent_versions`/`workspace_agent_bindings` 三张
  全局控制面表与 0010 迁移（trigger 状态机、sealed 不可变、partial
  unique live index、populated downgrade fail-closed）；
- `RegistryPersistenceService` 的事务契约：幂等解析、approval 单次
  消费、`resource_registry` 登记、append-only 审计全部同事务；
- P5.1A 合同作为唯一输入契约（DTO 漂移即拒绝），逻辑标识符与物理
  locator 分离。

信任边界与 P5.0 相同；服务**只接受验证过的内部 principal 上下文**，
每次都在事务内重载 live tenant/workspace/registry 行，从不信任事务前
快照。

## P5.1B 威胁与控制

| 威胁 | 控制 |
|---|---|
| 跨租户引用 definition/version/workspace | 复合 `(id, tenant_id)` FK + trigger 内 `(id, tenant_id)` JOIN 校验，DB 层拒绝（55000/23503） |
| sealed version 内容被改写 | `agent_versions_seal_guard` 逐列比较，sealed 后任何内容变更即拒绝 |
| revoked/disabled 被解释为 active 或回退 | trigger 状态机：revoked 终态、disabled 只可转 revoked、sealed 只可转 deprecated/revoked |
| version 降低 definition risk 绕过 Approval | trigger 比较 risk rank，只允许保持或提高 |
| 同 workspace+definition 出现第二个 live binding | partial unique index（`pending_approval`/`installed`）单赢家；服务层 FOR UPDATE 检查 |
| 同一 approval 被消费两次 | approval 行 `state=consumed` + 版本号乐观更新，`rowcount!=1` 拒绝 |
| 幂等 key 复用但请求 digest 漂移 | `reserve_idempotency` 冲突转换为 `RegistryConflictError`（不 catch-and-ignore） |
| 审计/resource 引用不一致 | `register_resource` 与实体行同事务；审计 `resource_id` 必须存在于 `resource_registry` |
| 事务中途失败留下部分状态 | 全部变更单事务原子提交；集成测试验证 rollback 无残留 |
| 物理 schema/table/column locator 泄漏 | DTO 投影、错误消息、审计仅逻辑标识符；集成测试断言 locator 缺席 |
| 把 P5.1B 伪装成公开 API/Runtime | 无 router/OpenAPI/SDK/Invocation/Planner/Executor/Celery/Runtime；Feature Gate 恒 false；P5.1 合同 gate 恒 `blocked/not_proven` |
| 0010 populated downgrade 破坏数据 | `_downgrade_global` 检测非空表即 `RAISE 'P5.1B downgrade refused'`，事务回滚 |

## P5.1B 完成定义

1. 单元测试（14 项）与一次性 sentinel PostgreSQL 集成测试（26 项）全部
   通过：migration head、cross-tenant 拒绝、sealed 不可变、并发单赢家、
   exact replay 幂等、digest drift 冲突、stale generation、approval 单次
   消费、审计 append-only、回滚原子性、物理 locator 缺席、populated
   downgrade fail-closed；
2. `make test-p5-1b-registry` 与
   `scripts/production/run_p5_1b_registry_disposable_gate.py --run` 在
   一次性隔离数据库上通过并记录 sealed evidence；
3. 未实现任何 Browser API/Runtime/编排；三个 Feature Gate 保持 false；
   P34.7/P5.0/P5.1 production 保持 `blocked/not_proven`；P5.2+ frozen。

# Phase 5 威胁模型补充：P5.1C Browser Agent Registry Control API

> 状态：P5.1C Browser 控制 API 已实现（6 个只读 + 4 个 mutation 端点、
> fail-closed 默认依赖、Python/TypeScript SDK、一次性
> `omnibase_test_p51c_*` 数据库 Gate）。它**不**建立任何 Agent Runtime/
> 编排安全主张；P5.1 production 保持 `blocked/not_proven`，P5.2+ 保持
> frozen。生产默认拒绝 DB-backed control plane，直到显式装配。

## P5.1C 资产与信任边界

受保护资产：

- `/api/v1/agent-definitions*` 与
  `/api/v1/workspaces/{id}/agent-installations*` 共 10 个端点；
- `get_registry_control_plane` 默认注入 `UnavailableAgentRegistryControlPlane`
  （fail-closed：任何 registry 表访问前 503 `agent_registry_unavailable`）；
- 公共 DTO（`schemas.py`，`extra="forbid"`，仅逻辑标识符）；
- Python/TypeScript SDK 的传输与解析边界（Bearer JWT、`/api/v1` 专用、
  强制 `Idempotency-Key`、禁通配 scope、拒绝 URL normalization 逃逸与
  宽松 response coercion）。

信任边界：与 P5.0/P5.1B 相同；mutation 在调用者事务内重锁 live
Tenant/User/Workspace/WorkspaceMembership 后委托 sealed P5.1B 服务。
Browser principal（JWT）是必要非充分条件，role 以当前成员资格为准。

## P5.1C 威胁与控制

| 威胁 | 控制 |
|---|---|
| 未装配 DB-backed control plane 时访问 registry | 默认依赖 503 fail-closed，不创建 session、不触碰任何表；10 端点参数化单测 |
| 非成员/过期成员执行 mutation | `authorize_workspace_action("workspace.grants.manage", lock=True)` 事务内重锁；installation viewer 读使用 `workspace.read`；definitions/versions 是 tenant principal catalog |
| 事务前角色快照/裸 cookie 授权 | mutation 每次重载 live membership 行并加锁，不信任快照 |
| 跨租户读/写 definition/version/binding | 服务层恒带 `tenant_id` predicate + DB 复合 FK；catalog 端点 tenant 过滤（集成测试断言） |
| upgrade/rollback 指向非 sealed 或 digest 不符版本 | Browser 非锁定 `_load_sealed_target_version_snapshot` 早拒绝；P5.1B 在 Definition → Version → Binding 标准锁序下权威复核 |
| Browser 预锁 Version/Binding 导致锁序反转 | Browser 只读快照不加行锁；P5.1B service 独占权威行锁与状态判断 |
| 竞态覆盖新 binding（stale write） | `expected_binding_id` 不匹配 → 409 `registry_stale_binding`；首次执行的 live-state 校验由 P5.1B 完成 |
| upgrade/rollback 首次成功后 exact replay 被旧 Binding 非 live 提前拒绝 | Browser 快照不要求 live；P5.1B 在旧状态判断前解析 outer IdempotencyRecord，同 key 同 body返回原新 Binding |
| 任意 caller digest 绕过幂等/Approval payload 绑定 | service 只接受 `internal_full|browser_install|browser_upgrade|browser_rollback` 闭 profile 并自行计算 hash |
| install Approval 被 upgrade/rollback 重放 | Approval 同时绑定精确 action、old Binding ID（supersede）、deterministic payload 与 risk/workspace/requester；单次消费同事务 |
| 请求含通配/重复 scope 或未知字段 | `schemas.py` `extra="forbid"` + scope 闭集正则 + 客户端 SDK 同构校验（422） |
| SDK `/api/v1/../../gateway` 或编码/反斜杠逃逸 | 两 SDK 在 URL join 前拒绝 dot segment、`%`、反斜杠、query/fragment、重复斜杠并复核规范化 path |
| TypeScript SDK 把非法状态/字符串数字/额外 locator 强转为成功 DTO | exact keys + closed states + strict integer/finite validation；禁止 `String()`/`Number()` coercion |
| 非法路径 UUID 触发 500 | router 稳定返回 422 `invalid_logical_identifier`，不调用 control plane |
| 错误体/OpenAPI/SDK 泄漏物理 locator | 白名单投影 + 单测断言（omnibase_meta/postgresql/password 等缺席） |
| 通过 API 创建 Definition/Version 或开启 Runtime | 端点集合固定（OpenAPI 精确断言）；无创建端点；Feature Gate 恒 false；migration head 0010 |
| 生产默认挂载 DB-backed control plane | 只能经 `dependency_overrides` 显式注入；main.py 生产组合不装配 |

## P5.1C 完成定义

1. 单元/API 测试（22 项）通过：10 端点 fail-closed 503、rejecting
   authorizer、DTO 严格性、server-derived identity、OpenAPI 精确路径
   集合、无物理 locator、无 internal 请求字段；
2. 一次性 sentinel PostgreSQL 集成测试（24 项）覆盖 migration head
   0010、API-backed install/upgrade/disable/rollback、exact replay、
   digest drift、stale generation、cross-tenant、live membership、并发
   单赢家、upgrade/rollback exact replay、operation-bound Approval、
   approval 单次消费、审计 append-only、rollback 原子性、
   cleanup proof；
3. `make test-p5-1c-registry-api` 与
   `scripts/production/run_p5_1c_browser_registry_disposable_gate.py --run`
   在一次性隔离数据库上通过并记录 sealed evidence；
4. Python SDK 9 项、TypeScript SDK 全套 15 项通过，并覆盖 path escape、
   extra field、closed state、non-integer/NaN 负例；
5. 未实现 Definition/Version 创建、migration 0011、Runtime/编排；
   三个 Feature Gate 保持 false；P34.7/P5.0/P5.1 production 保持
   `blocked/not_proven`；P5.2+ frozen。

---

# Phase 5 威胁模型补充：P5.2A Agent Task Ledger Contract Preflight

> 状态：P5.2A 离线合同预检已实现。P5.2 persistence ledger、Agent Runtime、
> Planner、Executor、scheduler、worker、模型/工具调用**均未实现**；P5.2
> production 为 `blocked/not_proven`；P5.2B+ 保持 frozen。本补充只覆盖
> P5.2A 合同与 validator 自身的安全属性，不建立任何 Agent Runtime /
> Task 执行安全主张。

## P5.2A 资产与信任边界

受保护资产：

- AgentTask/AgentRun/AgentStep/AgentAttempt/TaskLease/Effect/Checkpoint
  离线 strict DTO 与 canonical digest；
- 8 个 hash profile 的封闭字段集与 exact-replay/stable-conflict 语义；
- 12 维预算账本与 limit/reserved/committed/released/remaining 不变量；
- Task Lease TTL 与 deadline/Run Lease/Node attestation/Grant/policy
  五组 expiry 的边界合同；
- identity stages 字段规则表（required/not-yet/immutable/core-generated/
  submittable/forbidden）；
- sealed contracts/threat model/maintainer map digest；
- P34.7/P5.0/P5.1 formal state 与 migration 基线（0001–0010）。

信任区域与 P5.0 相同；决策层 `TaskLedgerContractGate` 是纯函数验证器，
无数据库/网络/进程副作用，模块 import 白名单（stdlib +
omnibase.production）由 AST 测试强制。

## P5.2A 威胁与控制

| 威胁 | 控制 |
|---|---|
| 把合同当实现（"有合同=Task 账本完成"） | report 恒输出 `task_ledger_orm_created=false`、`task_ledger_migration_created=false`、`agent_invocation_api_exposed=false`、`task_execution_activated=false`；源码边界扫描拒绝 forbidden 包与 migration 0011 |
| Browser/Workload 提交 core-generated 或未生成字段（runtime_instance_id、workload thumbprint、request_hash、lease/fencing） | identity stages 闭集表：`core_generated`/`not_yet_generated`/`forbidden` 字段提交即拒绝，稳定 reason code |
| Browser JWT 混入 workload DTO | 身份字段宇宙闭集 + strict DTO `extra="forbid"`；未知字段（browser_jwt/authorization 等）拒绝 |
| 调用方 request_hash override 或 digest 漂移 | `request_hash_override` 字段拒绝；`request_hash` 必须等于 profile canonical digest |
| 同 key 复用不同 operation/payload 伪装 exact replay | `classify_replay` 稳定 conflict；只有同 key+同 operation+同 canonical payload 是 exact replay |
| Task Lease 越过 deadline/Run Lease/attestation/Grant/policy 最早 expiry | `LeaseExpiryBounds` 逐项比较，五个独立 reason code |
| Task/Node/Run fencing 回退或 stale holder 提交 | lease 与 run 的 run_fencing/node_fencing/workspace_generation 逐项一致校验；retry 必须提高 task fencing |
| terminal Run/Attempt/Effect 复活 | 状态机闭集：终态无出口；`unknown` 永不 replay |
| cancel 把跨 provider boundary 的 unknown 伪装成成功 | `validate_cancel_target`/`validate_cancel_attempt`：dispatching/running 进入 reconciliation；unknown effect 存在时禁止 cancel-success 伪装 |
| checkpoint 携带 runtime 状态 | checkpoint DTO 无 token/lease/PID/socket/handle 字段；PID/socket/provider_handle 提交即 "unexpected fields" |
| 模型输出被当作 committed evidence | `validate_committed_evidence` 只接受 operation/effect/audit ledger |
| 预算扩大/负数/浮点/NaN/Infinity/wildcard/未知维度 | strict 整数 + ceiling + 闭集维度 + parse_constant 拒绝非有限数；reserved/committed/released 不变量 |
| migration 0011 或 P5.2 ORM/router/runtime 源码出现 | forbidden source paths + migration revision 集合漂移 → veto |
| gate 被打开或写成 TRUE/yes/on/1 | gate true → **veto**（P5.2A 比 P5.0/P5.1A 更严格）；truthy token → veto；合同内 gate true → 无效合同 |
| `activation_requested=true` | 无效合同（解析拒绝）+ verify veto |
| 合同/evidence 经 symlink/reparse 指向根 `.env` | P5.0 修补后的逐分量 `_safe_repo_path` 规则复用；`sealed_contracts` 路径禁止根 `.env` |
| sealed digest 漂移 / dirty checkout / remote 不符 | clean-checkout veto；remote 精确匹配；sealed digest 逐字节重算 |
| OpenAPI 出现 agent-invocation/agent-task/gateway-agent 端点 | snapshot digest + 路径扫描 → veto |
| `not_proven` 被计为 passed | evidence 处理与 P5.0/P5.1A 一致，只进 blockers |

## P5.2A 完成定义

1. 离线 strict DTO/closed-set 合同、8 个 hash profile、validator、50 项
   负向矩阵、威胁模型、维护者地图与 CI validate-only Gate 全部通过；
2. `--verify` 在 fresh clean checkout 可复现 `blocked/not_proven`
   （exit 2，veto 0）；
3. 未实现任何 ORM/migration 0011/router/Runtime/Planner/Executor/
   scheduler/worker；三个 Feature Gate 保持 false；P34.7/P5.0/P5.1
   production 保持 `blocked/not_proven`；P5.2 persistence 未实现。

任何缺失外部证据时正确输出是 `blocked/not_proven`；不得把 P5.2A 写成
P5.2 PASS，也不得据此解冻 Phase 5 Runtime。

## P5.2A 复核修复补充（2026-08-04）

主 Agent 独立复核后的修复边界（仍在 P5.2A 纯离线合同层）：

| 威胁/缺口 | 修复控制 |
|---|---|
| 同 Task 第二个 Step 无法拥有 attempt_number=1 | `attempt_number` 按 (task_id, step_id) 分组校验；`task_fencing_token` 按 Task 级 created_at 排序单调校验；两个 Step 各含 Attempt 1 的正向测试 |
| pending/ready 携带 lease、leased/dispatching/running 无 lease、terminal 保留 lease | Attempt ↔ Task Lease 状态矩阵：pre-dispatch 无 lease、运行三态必须有、terminal（含 unknown）不得保留；历史 lease 由 append-only lease 记录（revoked/expired/completed）承载，Attempt 上无 active holder 引用 |
| Attempt 引用另一 Attempt 的 Lease / 同 Attempt 双 active Lease / stale lease 作为 current | 精确双向绑定：`attempt.task_lease_id` 必须解析到 attempt_id/task_id/agent_run_id 一致的 lease；非 terminal Attempt 必须指回；集合级单 active lease 扫描；运行态 Attempt 的 current lease 必须 active |
| AgentRun 绑定组不完整 | `run_lease_id/run_fencing_token/node_id/node_fencing_token` all-or-none + `runtime_instance_id/workload_identity_thumbprint` all-or-none；created 全空、leased/running/paused 全有、terminal 全空 |
| ceiling 收紧值未生效 | `deadline_ceiling_seconds`/`task_lease_ttl_ceiling_seconds` 传入每个 DTO 解析器逐实例校验；收紧到 60 秒即拒绝 12h Task/5min Lease 的负向测试 |
| Step 与 Task Plan 身份漂移、未知/跨 Task/跨 Run 依赖、环、重复 step_number | step.plan_id/plan_version/plan_digest 必须等于 task；dependency 必须存在、同 Task/Plan/AgentRun；step_number task 内唯一；DFS 无环 |
| 父子 deadline 未冻结 | `attempt.created_at < attempt.deadline <= task.deadline`；`task_lease.expires_at <= attempt.deadline <= task.deadline`（最后一条为防御性冗余，文档说明蕴含关系） |
| attempt hash 缺安全身份字段 | attempt_claim/heartbeat/finish profile 补齐 agent_run_id、node_id、run_lease_id/run_fencing_token、node_fencing_token、agent_version_digest、resource_scope_digest、budget_policy_digest；不进 hash 的字段（operation_id、runtime/workload 身份、lease 时间）由 durable 记录绑定并在文档表中逐项证明 |
| 报告把 safety negative 当运行证明 | `verification_evidence` 区分 static source-boundary assertion（本次 verify 实际执行）、import/AST assertion（由测试证明）、gate 本次未执行的行为、direct runtime execution（Gate 不执行） |
