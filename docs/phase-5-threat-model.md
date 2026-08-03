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
   该 head 必须等于合同声明的 `expected_revision`（当前 `0009`）。
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

> 状态：P5.1A 离线合同预检已实现。Registry database foundation、Browser
> API、Runtime installation 均未实现；P5.1 production 为
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
| high/critical 安装缺 Approval | `approval_policy` 强制 required；缺 `approval_id` 拒绝 |
| 预算 0/负数/超 ceiling/NaN/Infinity | `_strict_positive_int` + ceiling 校验 + `parse_constant` 拒绝非有限数 |
| wildcard/重复/空 tool ID、scope | 逻辑 key 闭集 + 去重 + 保留字拒绝 |
| JSON Schema 远程/文件 `$ref`、自定义命令字段 | 受控关键字闭集 + 本地 pointer 白名单 + 深度上限 |
| digest 大写/长度错误/内容漂移、CRLF 冒充原始字节 | 严格 lowercase 64 hex；canonical 重新序列化原始 UTF-8 字节 |
| 合同/evidence 经 symlink/reparse 指向根 `.env` | P5.0 修补后的逐分量 `_safe_repo_path` 规则复用 |
| dirty checkout / remote 不符 | clean-checkout veto；remote 精确匹配 |
| gate 被打开或写成 TRUE/yes/on/1 | gate true → blocker；truthy token → veto；合同内 gate true → 无效合同 |
| 偷偷新增 ORM/migration/router/Celery/runtime 包 | forbidden source paths + migration revision 集合漂移 → veto |
| OpenAPI snapshot 被加入 agent endpoint | snapshot digest + path 扫描 → veto |
| report 写到仓库内 | `_write_report` 强制仓库外 |
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
