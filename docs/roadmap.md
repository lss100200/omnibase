# OmniBase 分阶段路线图

> **状态同步日期**：2026-08-10
> **基于**：`main` `eb0a173`（PR #18 P5 Consolidation、PR #19 P34.7 joint Gate、PR #20 Trust Policy Candidate R0、PR #21 repository introduction、PR #22 R1 preparation 均已合并；R1-A implementation 仍在独立未推送工作线）
> **远景对齐**："数据库 + RAG + 自持生态 + Agent"

本文档覆盖从当前状态到产品远景规划的完整路径。每个阶段都列出了前置条件、关键任务和预估复杂度。

---

## 当前状态总览

| 模块 | 完成度 | 说明 |
|---|---|---|
| 多租户隔离 + JWT 认证 | ✅ 100% | schema-per-tenant, Pool checkout, ContextVar |
| RAG (V1: bge-small-zh, 512d) | ✅ 100% | HNSW + BM25 + RRF + reranker + SSE |
| 文档异步摄取 | ✅ 100% | Celery, 5-state lifecycle, 409 delete protection |
| P0 安全加固 | ✅ 100% | 端口 loopback, 原始 SQL 移除, 破坏性测试隔离 |
| 前端性能 + 认证 | ✅ 100% | Bootstrap singleton, 节流, 分页, 生产镜像 |
| Phase 1.6 双索引工程 | ✅ 100% | 工程与 CPU benchmark 完成；V1 权威，生产 V2 回填/cutover 冻结 |
| API 基础设施 | ✅ 已进入 `main` | `/api/v1`、Request ID/访问日志、显式 CORS、请求边界、Redis 限流、实时主体/RBAC 与离线模型边界持续由 CI 验证 |
| Phase 3-4 安全 AI 工作空间与能力平台 | 🟡 企业 P34.7 轨道冻结保留；个人版审批轨道启动 | P34.1–P34.6、P34.7 hardened joint Gate、Trust Policy R0 和 R1-A assignment 均作为长期安全资产保留。企业多人 authority/key ceremony/15-resource/11-blocker evidence campaign 在个人版完成前冻结；个人版参考主流 AI IDE 的 Sandbox/Approval/Network 模型，并叠加 OmniBase Capability、Workload Identity、Lease/fencing、预算、审计和多 AI 空间隔离。当前 production Gate 仍为 `blocked/not_proven`，Feature Gates 仍关闭 |
| Agent 编排 | 🟡 P5.8P personal restart recovery 等待 PR CI | P5.5C 已通过 PR #30 合并；P5.6P 已通过 PR #31 合并到 `main@9809c3e`；过期 invocation 的 next-request/exact-replay 收口与显式 all-new retry 已实现，本地 73 项 focused 全绿，等待 P5.8P PostgreSQL sentinel 验收。Runtime 默认关闭，仅 exact personal canary 可启用，Planner/Multi-Agent 仍关闭 |
| Skill/MCP 扩展 | ✅ P5.6P first-party instruction Skill 已合并 | migration `0014`、sealed instruction-only persistence、Workspace/AgentVersion 精确安装和 personal Agent Alpha 投影已通过 PR #31 required CI；workflow/script、第三方 Marketplace 与 MCP 继续冻结 |

---

## Phase 1.6: BGE-M3 双索引评估（工程与 CPU benchmark 完成；生产采用冻结）

> **前置条件**：P0 安全 + 性能 gate 已通过 ✅
> **代码状态**：✅ 工程完成（HEAD `4a3655c`）
> **Runtime benchmark**：✅ 已执行，V2 满足接受阈值
> **生产状态**：V1 仍为权威主通道；未执行生产 V2 回填或 cutover

| 任务 | 完成度 | 说明 |
|---|---|---|
| V2 index metadata + config | ✅ 100% | `IndexVersion`, `IndexMetadata`, `IndexLane`, `INDEX_REGISTRY` |
| BGE-M3 query instruction prefix | ✅ 100% | `_V2_QUERY_INSTRUCTION` applied to V2 queries only |
| 双通道 schema + migration | ✅ 100% | `embeddings_v2` + `rag_document_index_state` (migration 0003) |
| 版本感知 embedding 适配器 | ✅ 100% | Per-version singletons, strict APIs, typed exceptions |
| 双通道 store + retriever | ✅ 100% | Lane-aware write/read, shadow write, dimension validation |
| Celery backfill task | ✅ 100% | `backfill_document_v2_task` + `backfill_all_documents_v2_task` |
| 可恢复回填 + idempotency | ✅ 100% | `ON CONFLICT` upsert, resume-without-duplicate test |
| 评估比较框架 + cutover gate | ✅ 100% | `compare_versions()` with 4 gates |
| Benchmark 工具 + runtime 证据 | ✅ 100% | V1 vs V2 CPU benchmark 已执行 |
| 本地模型路径支持 | ✅ 100% | `_LOCAL_MODEL_PATHS` + ModelScope cache 集成 |

### Runtime Benchmark 证据 (CPU, 2026-07-31)

| 指标 | V1 (bge-small-zh-v1.5, 512d) | V2 (BGE-M3, 1024d) | 接受阈值 |
|---|---|---|---|
| 模型加载 | 1.5s | 16.5s | — |
| Cold batch (8 docs) | 117ms | 7,488ms | — |
| Warm batch avg (8 docs) | 155ms | 1,181ms | < 120s (batch 32) ✅ |
| Single query avg | 139.9ms | 356.0ms | < 10s ✅ |
| Peak RSS (both loaded) | — | 2,316 MB | — |

### 模型来源

BGE-M3 模型权重（2.2GB `pytorch_model.bin`）通过 ModelScope 下载，存储于：
`<local-model-cache>\BAAI--bge-m3\snapshots\master\`

容器内通过本地路径加载：`/app/models/bge-m3/`

**代码完成度：100%**

CPU benchmark 只证明当前模型运行时满足性能阈值，不等于真实语料检索质量 gate 已通过。生产采用还必须完成真实语料 V1/V2 质量比较、V2 覆盖率、资源并发、灰度和回滚验证，并取得用户明确授权；在此之前不得切换 V2 primary 或删除/破坏 V1。

---

## Phase 2: API 基础设施硬化

> **前置条件**：Phase 1.6 工程收口完成；生产 V2 采用不阻塞本阶段
> **状态**：✅ 2026-07-31 工程完成、待原子提交；已通过非集成、sentinel 隔离集成和独立生产前端 smoke
> **目标**：为后续 Agent/扩展生态建立安全的 API 基础

| 任务 | 复杂度 | 说明 |
|---|---|---|
| **API 版本化** (`/api/v1/`) | LOW | ✅ 后端、Axios、refresh、SSE 与生产 rewrite 已统一；旧 `/api/*` 业务路径返回 404 |
| **速率限制** | MEDIUM | ✅ Redis 原子固定窗口；auth 按可信客户端来源，RAG/upload 按 tenant+user；429 保留 `Retry-After` |
| **Request ID + 请求日志** | LOW | ✅ 安全接收/生成 ID，注入 structlog context，所有响应回写，记录 route/status/duration |
| **RBAC 租户内角色** | MEDIUM | ✅ 每次请求从租户数据库重读 active user/role；`require_tenant_admin` 已实现并测试 |
| **CORS 收紧** | LOW | ✅ method/header/origin 显式 allowlist，允许/拒绝 preflight 已测试 |
| **全局上传大小中间件** | LOW | ✅ 同时校验声明长度与实际流式字节数；超限在 endpoint 副作用前返回 413 |
| **模型网络边界** | MEDIUM | ✅ 默认禁止运行时隐式下载；本地模型优先，reranker 缺失时快速降级到 RRF |

验收证据：320 个后端非集成测试、11 个隔离集成测试、43 个前端测试通过；typecheck/lint/production build/Compose config 通过；3001 独立生产容器完成真实登录 smoke。

---

## Phase 3-4：安全 AI 工作空间与能力平台 / Secure AI Workspace & Capability Platform

> **前置条件**：Phase 2 API/RBAC 工程验收完成并形成原子提交
> **预估工期**：6–9 周，按 P34.0–P34.7 独立增量交付
> **目标**：把受控数据能力、API/SDK 解耦、工作空间模板、沙箱和能力网关建设成一个闭环；先提供人工任务 harness，再开发 Agent。

| 增量 | 复杂度 | 交付与 Gate |
|---|---|---|
| **P34.0 边界与契约** | HIGH | 威胁模型、逻辑资源模型、能力词汇、OpenAPI/错误契约和攻击测试先行；物理 schema、宿主凭据和规范数据边界不可暴露 |
| **P34.1 安全状态基座** | HIGH | Resource Registry、append-only Audit、Operation 状态机、Approval 和 Idempotency Ledger；本增量不开放 CRUD/DDL |
| **P34.2 只读网关与 SDK 契约** | HIGH | 只开放 data schema/rows read、`rag.search`/citation；每次调用复核 RBAC/tenant/capability，冻结 TS/Python 只读契约 |
| **P34.3 结构化写入** | HIGH | 参数化 CRUD、DDL plan/apply、风险审批、幂等执行、Operation 跟踪和失败补偿；继续禁止任意 SQL |
| **P34.4 Workspace 控制面** | HIGH | ✅ 工程 Gate：17 张 global metadata 表；Workspace aggregate 串行化 membership/last-owner；模板事务内实时 admin 重验与 PostgreSQL 自然幂等；Run Lease 绑定 Node fencing 和实时 attestation；终态 Run 不可复活；Network Lease 仅签逻辑授权且不调用 provider；Node/Peer/Service/Authority 统一锁序与撤销；不执行代码、不接真实网络或数据 |
| **P34.5 沙箱隔离后接只读网关** | HIGH | ✅ A0-A3/B/C/D 工程 Gate；A4 current-source target-host 12/12 `pending/not_proven`：Network Broker 两轮 26/26、真实 Headscale control-plane + mTLS Node-Daemon test-double、split-process mTLS Gateway disposable Gate 已通过；旧 11/11 不适用于 UID/GID-hardened launcher。production Core↔Runner/Broker/Gateway 联合装配、真实成员数据面、DERP、节点失陷、容量和 SLA 留给 P34.7 |
| **P34.6 私有写入、promotion 与 snapshot 基础** | HIGH | ✅ Foundation / Contracts / Fail-closed primitives：final `cc48baa` ordinary clone 的 Overlay/Gateway source-built Gates、164 related tests、Mypy、OpenAPI 与 Ruff clean-checkout Gate 通过；Workspace-private/derived 逻辑写入、独立 Artifact/Derived RAG、lineage、`pending|unknown` no-replay、Approval/Publication metadata 与 server-generated snapshot inventory 已通过隔离 Gate。Promotion/Restore `COMMITTED`、`controlled_shared` 成功可见性、真实 provider copy/restore 和 production snapshot barrier 继续拒绝，禁止原地修改 source 或创建/改写 `canonical_readonly` |
| **P34.7 生产总验收** | HIGH | 🟡 企业轨道冻结保留：PR #19 hardened joint Gate、PR #20 Trust Policy R0 与当前 R1-A assignment 均保留，canonical example 仍为 `UNASSIGNED / NOT_ASSESSED`，R0 最高 `candidate/valid_not_approved`，R1-A 最高能力 `complete_not_authenticated`，approved digest 为空。独立 authority registry、key ceremony/custody、15-resource/11-blocker、双成员 Overlay/DERP 和 SLA campaign 等企业工作暂停，待个人版稳定后从冻结文档恢复。当前活动方向是 Personal Owner Approval Gate，不改变现有 production `BLOCKED / NOT_PROVEN` |
| **P5 personal product slice** | MEDIUM | 🟢 P5.3A–P5.5B 已进入主线；P5.5C bounded Memory compiler/ContextCapsule injection 正在收口。migration head 为 `0013`、`0014` absent；Runtime 默认 false，仅 exact personal canary 可开启，Planner/Multi-Agent false；真实工具、workflow/script Skill、MCP 与企业高风险 authority 未授权 |

**不可跳过 Gate**：P34.0–P34.6 的工程契约和隔离验证已经依次完成，但这些证据不等于 production 联合激活。团队版/企业版继续使用完整 P34.7 total Gate。个人版不再等待多人 authority、双成员 Overlay/DERP 和企业 SLA，但必须先实现并独立验证 Personal Owner Approval Gate；该 Gate 仍需绑定 Sandbox、Capability Gateway、Workload Identity、Lease/fencing、预算、审计、网络 allowlist 和 kill switch。在新 Gate 完成前，不得启动 production Agent 编排。

长期硬约束：workspace 保存身份、配置、资源意图和私有状态，是长期逻辑资源；run/session 是带短期凭据与配额、可随时销毁重建的执行实例。普通 Docker 容器只能作为开发和生命周期验证基线，不代表能够安全运行任意敌对代码；未通过 P34.5 隔离 Gate 的运行时不得连接真实数据。

详细契约见 [Phase 3-4 统一实施计划](phase-3-4-secure-ai-workspace-implementation-plan.md)与 [Phase 3-4 威胁模型](phase-3-4-threat-model.md)。

---

## Phase 5: Agent 编排基础

> **前置条件**：团队版/企业版必须通过完整 P34.7 总 Gate。个人版使用已经实现的单 Owner Gate 与 exact no-tool canary；Runtime 默认关闭，只能在该 canary 中临时开启，Planner/Multi-Agent 继续关闭。企业 P34.7 不阻塞个人产品推进
> **预估工期**：3-4 周
> **目标**：让 Agent 作为工作空间内的受约束负载执行复杂任务
>
> **详细实施契约**：`docs/phase-5-agent-runtime-implementation-plan.md`。该文档将 Phase 5 拆为 P5.0–P5.9：P5.0 只验证 P34.7 Evidence Manifest 和默认关闭的解冻 Gate；其后依次建设 Registry/identity、Task Lease/fencing、compile-only Planner、确定性 Validator、Executor/Model/Tool Gateway、长期 Memory、第一方原生 Skill、有界多 Agent DAG、恢复/reconciliation、UI/SDK 与生产总验收。当前仍为 `PLANNED / FROZEN`；P34.7 PASS 前不得据此提前启动 Agent Runtime。
>
> **合同链进度**：P5.0、P5.1A/B/C、P5.2A/B/C、P5.3A 与 P5.4A/B/C/D 已形成 fail-closed 工程与产品链。P5.5A Memory 合同、P5.5B migration `0013` persistence/delete/export/backup inventory 与 P5.5C bounded compiler/ContextCapsule injection 已通过 PR #30 进入主线；P5.6P migration `0014` sealed instruction-only Skill 已通过 PR #31 进入主线，P5.8P restart/no-replay recovery 已通过 PR #32 进入 `main@559febd`。P5.9P 已在 clean GitHub Ubuntu run `31608502738` 完成 production-like acceptance，receipt 闭集与 artifact digest 已独立复核；本次只收尾 P5.9P，未开始 P6.0。P5.7 Multi-Agent、workflow/script Skill、MCP 与企业 P34.7 继续冻结。

| 任务 | 复杂度 | 说明 |
|---|---|---|
| **Agent 注册表** | HIGH | agent 类型、工作空间绑定、能力声明和工具绑定 |
| **Planner/Orchestrator** | HIGH | 输出受验证的任务 DAG；只调用工作空间已获授权能力 |
| **Specialist Agents** | HIGH | Librarian、Curator、Archivist、Engineer 的最小能力集 |
| **Tool 注册 + 调用** | HIGH | 参数 schema、capability 校验、超时、重试、结果大小和人工确认 |
| **Agent 状态管理** | MEDIUM | 会话记忆、任务进度、checkpoint、恢复和行为回放 |
| **工作流执行** | MEDIUM | 工作空间内的任务依赖、取消、结果聚合和资源配额 |
| **Memory Compiler** | HIGH | 按 user/workspace/agent/task 与 token 预算生成短期 Context Capsule；长期记忆候选需治理、去敏和纠正 |
| **原生 Skill 契约** | HIGH | 版本化 manifest、typed tool choreography、能力/预算声明和回滚；第三方 marketplace/MCP 后置 Phase 6 |
| **恢复与 reconciliation** | HIGH | 新 Run/Attempt/Lease/identity 恢复，`pending|unknown` 不自动 replay，checkpoint 只引用 committed 逻辑结果 |

> **P5.6A / P5.6P 当前状态（2026-08-12）**：P5.6A 已建立 first-party-only 的
> `SkillDefinition/SkillVersion` compile-only 合同、严格 schema/digest/budget/
> rollback Gate、示例 `Workspace Librarian` 与离线 validator。正式状态保持
> `blocked/not_proven`；最多接受 `tested` manifest，不接受无真实 sealed
> evidence 的全局 `approved/published`。P5.6P 是明确授权的个人版 successor：
> migration `0014` 新建独立 Skill 表，提供 first-party sealed instruction 类型的
> internal install/disable/revoke/rollback，并只在 exact personal canary 中投影到
> Agent Alpha。仍不暴露 `/api/v1/skills`，不执行 script/workflow，不增加工具、
> Capability、网络或秘密。Trust Policy R1-B–R1-F 与 P34.7 企业证据轨道冻结保留。

> **P5.8P 当前状态（2026-08-12）**：个人版只增加 next-request/exact-replay
> recovery，不建设 scheduler/worker/企业 recovery coordinator。数据库时钟确认
> TaskLease 过期后，旧 Attempt/Effect 收敛为 `unknown`、Task 为
> `blocked_unknown`，保留一条 reconciliation 且不自动重放 Provider。Owner 的
> `retry_of` 只能指向同范围的 retryable 终态，并创建全新的 ledger/runtime
> identity。个人 target/backup 同步到 migration `0014`，restore-new 仅增加闭集
> `0013 -> 0014` Skill compatibility。PR #32 已合并，required GitHub CI 全绿。

> **P5.9P 当前状态（2026-08-12）**：最终个人版 production-like engineering
> acceptance 已通过。PR #33 exact head `9eb7238c` 的 clean GitHub Ubuntu run
> `31608502738` 中 backend、frontend/TypeScript SDK、Compose、guarded disposable
> PostgreSQL 与 personal acceptance 全绿。唯一 receipt artifact 的 ZIP SHA-256 为
> `fe3e4822…745f`，JSON SHA-256 为 `1174f6da…cc57`；闭集复核证明真实 loopback
> frontend SSE、sealed Agent/Skill、加密 scoped Memory、cancel、Core SIGKILL、真实
> Lease 过期、no-auto-replay、Owner 显式 `retry_of` 全新身份、kill switch、Runtime
> 恢复 false、custom-format cold dump 与 `omnibase_restore_*` restore-new 均通过，
> cleanup 成功。该 PASS 不等于部署或 P6.0；本次未开始任何 P6 工作。

---

## Phase 6: Personal Engineering Workbench

P6 has been redirected from an extension-ecosystem-first plan to a
front-end-first personal engineering workbench. The single human Owner remains
the only approver. Enterprise P34.7, Planner and autonomous Multi-Agent remain
frozen and are not P6.0 prerequisites.

P6.0 is split into four bounded product increments:

- **P6.0-A — workbench, conversations and employees**: IDE-style workbench,
  browser-local session create/search/pin/archive/restore, append-only session
  timeline, one active parent Agent and nine dormant specialist employees. A
  user message may explicitly `@` exactly one specialist. Employees cannot
  self-trigger, wake one another or run in the background.
- **P6.0-B — file tree, open modes and context**: authorized Workspace tree,
  internal viewers, system opening, and distinct `OPEN` / `CONTEXT` / `PINNED`
  states with visible context cost. No whole-home or secret-directory scan.
- **P6.0-C — Agent changes, diff and rollback**: task-owned ChangeSets,
  file/hunk review and three-way rollback that preserves pre-existing user
  dirty changes and fails closed on conflict.
- **P6.0-D — provider adaptation, gears and product acceptance**: DeepSeek,
  GLM, Kimi, GPT and Claude capability profiles, economic/standard/deep/audit
  gears, provider-specific context compilation, unified streaming/cancel/usage
  and visible token/cost budgets.

P6.1+ owns Skills, MCP, SQL visualization, AI CLI adapters, Email/remote
messaging, browser/desktop control and controlled self-modification. Those are
not silently included in P6.0.

> **P6.0-A implementation start (2026-08-13)**: branch
> `codex/p6-0-personal-engineering-workbench` starts from merged P5.9P
> `main@46bc894`. `/dashboard` now mounts the first Personal Engineering
> Workbench implementation. The initial slice uses a versioned tenant/user
> scoped browser store because Task/Run is an execution ledger and Memory is
> curated context, not a conversation database. Migration `0016` remains
> absent. The work is implemented and locally verified but has not yet been
> independently reviewed, pushed, merged or deployed.

## Phase 6.x: Skills + MCP extension ecosystem

> **P6.1 A-D engineering R0 (2026-08-13):** the personal line now has an
> authenticated six-Skill first-party catalog, model-name-first DeepSeek/GPT
> request adaptation, a separately launched three-tool read-only MCP preview
> whose Git surface is metadata-only status/log, and a deterministic Windows
> ZIP/no-build release preview with offline immutable-image preflight. MCP is not mounted
> into Agent Alpha, migration remains `0016`, and EXE build/sign plus real OCI
> digests remain not proven. This is not Marketplace, arbitrary MCP, production
> release or enterprise P34.7 activation.

> **P6.2 A-D personal capability center engineering R0 (2026-08-14):** the
> personal line now consolidates the native Skill catalog, ten-role model
> posture and exact read-only MCP boundary in one Chinese capability center;
> adds Owner-triggered scan-only local Skill discovery; injects only bounded,
> redacted terminal history from the same browser session; and restores
> tenant/Workspace-scoped ChangeSet metadata plus bounded local Before/After
> recovery content after refresh. P6.2-D upgrades the Windows source contract
> to a self-contained Companion with exact release/installed-byte verification,
> atomic install, CSPRNG config initialization and offline doctor. Migration
> remains `0016`; unknown Skills are not installed, MCP is not connected to
> Agent Alpha, and Runtime/Planner/Multi-Agent/MCP gates remain closed. Real OCI
> digests, Authenticode, clean-machine public release acceptance and production
> deployment remain separate future evidence.

> **P6.3 A-D personal extensions engineering R0 (2026-08-14):** the
> first-party catalog expands from six to fifteen instruction-only Skills with
> exact registration comparison plus live-count and aggregate-instruction
> budgets. The standalone read-only MCP preview expands from three to six tools
> with bounded SHA-256, literal text search and Git diff metadata; it remains
> disconnected from Agent Alpha. GLM and Claude gain exact model-name-first
> prompt/context profiles while the current Chat Completions transport honestly
> leaves native thinking, caching, strict tools and MCP unproven. GitHub and
> `/public-preview` source now describe the P6 personal workbench instead of the
> old P5-only snapshot. The Windows Companion adds help and safe install-location
> planning without auto-elevation or host mutation. Migration stays `0016`,
> `0017` is absent, and Runtime/Planner/Multi-Agent/MCP Runtime remain disabled.
> Live `omnibase.chat`, published OCI digests, Authenticode and clean-Windows VM
> acceptance remain separate external evidence.

> **前置条件**：Phase 3-4 工作空间边界 + Phase 5 Agent 工具协议完成
> **预估工期**：2-3 周
> **目标**：让用户、Agent 和第三方在工作空间安全边界内扩展能力

| 任务 | 复杂度 | 说明 |
|---|---|---|
| **MCP Server 客户端** | HIGH | 工作空间内连接外部 MCP；工具发现、认证、超时和错误隔离 |
| **MCP Server 宿主** | HIGH | 通过能力网关暴露 RAG、文档和数据库能力 |
| **Plugin/Skill 注册系统** | HIGH | manifest、版本、依赖、能力声明、隔离测试和回滚 |
| **事件总线/Hook 系统** | MEDIUM | 脱敏、有界 payload；工作空间内 before/after hooks |
| **实验到发布流程** | MEDIUM | Agent 可在私有空间创建实验 Skill；共享前需人工审核和安全评估 |
| **扩展管理界面** | MEDIUM | 浏览、安装、启用、禁用、升级和权限可视化 |

---

## Phase 7: 开源准备

> **前置条件**：Phase 6 完成或核心功能稳定
> **预估工期**：1-2 周

| 任务 | 说明 |
|---|---|
| 文档完善 | README、架构文档、API 文档、贡献指南 |
| Demo 环境 | 一键部署脚本 + 示例数据集 + 教程 |
| CI/CD | GitHub Actions: lint + test + build + deploy |
| 安全审计 | 第三方安全审查 + 漏洞扫描 |
| 许可证 | Apache 2.0（已配置） |
| 社区准备 | Issue 模板、PR 模板、Code of Conduct |

---

## 阶段依赖关系

```
Phase 1.6 工程与 CPU benchmark 完成（生产 V2 回填/cutover 冻结）
    ↓
Phase 2: API 基础设施（工程完成，待原子提交）
    ↓
Phase 3-4: 安全 AI 工作空间与能力平台（P34.0–P34.7）
    ↓
Phase 5: Agent 编排（工作空间内运行）
    ↓
Phase 6: Skill/MCP 扩展生态
    ↓
Phase 7: 开源准备
```

## 决策记录

| 决策 | 原因 |
|---|---|
| API 版本化在 Phase 2 而非 Phase 1 | Phase 0/1 无外部消费者；Phase 2 是最后低成本窗口 |
| RBAC 在工作空间嵌套前 | 嵌套模型需要角色继承/覆盖；先有角色系统再做层级 |
| Phase 1.6 生产采用不阻塞后续 | 双索引工程已完成；V1 仍为权威主通道，V2 回填/cutover 须另过真实语料质量与生产安全 gate |
| 受控数据与 AI 工作空间合并为 Phase 3-4 | 数据 API、逻辑资源、SDK、能力网关和沙箱是同一授权闭环；分成两个独立阶段容易形成可绕过的临时直连面 |
| 安全元数据与只读能力先于写入 | Resource Registry、Audit、Operation、Approval、Idempotency 和只读 gateway 是 CRUD/DDL、私有写入与 promotion 的强制基础，不允许以后补票 |
| workspace 与 run/session 分离 | workspace 是长期逻辑资源；run/session 是可销毁执行实例，短期凭据、资源配额和攻击影响不得沉淀为 workspace 宿主权限 |
| 普通 Docker 不是敌对代码安全声明 | Docker 可用于开发和空沙箱生命周期基线；连接真实数据前必须以 P34.0 威胁模型通过 P34.5 隔离 Gate |
| P5 Fast Track 与生产激活分离 | 允许 engineering-only P5.2B ledger、Model Gateway 和无工具单 Agent Alpha；P34.7 未通过且未获单独批准前，仍不得启用生产 Runtime、自主 Planner、多 Agent 长循环或宿主级工具 |
| MCP 在工作空间和 Agent 之后 | 工作空间提供隔离边界，Agent 框架定义工具协议，MCP 是扩展实现之一 |
## 2026-08-11 personal production target update

PR #25 merged the bounded personal Runtime product path. PR #26 then merged the
recoverable personal production target and PR #27 fixed release-receipt
durability across post-merge remote-ref deletion. `main@f4b77a1` has passed the
required remote CI and the preserved release receipt verified again after both
temporary feature refs were removed.

The personal base target is now startable, stoppable, cold-backup capable,
restore-new capable and A-to-B upgradeable while Runtime defaults false and
Planner/Multi-Agent remain false. This does not reopen the frozen enterprise
P34.7 evidence campaign. A fresh Provider-backed no-tool journey and explicit
Owner acceptance/cutover of a durable non-disposable target are still required
before the final personal production-acceptance claim.

P5.6P and P5.8P are merged through PR #31 and PR #32. The active personal
sequence is now P5.9P personal production-like acceptance followed by the
bounded P6.0 personal admission record. P5.7 Multi-Agent DAG, workflow/script
Skills, Marketplace/MCP and enterprise P34.7 are not personal admission
prerequisites. P5.5B advanced the reviewed head to `0013`; P5.5C merged through
PR #30 and compiles committed exact-scope Memory, persists a short-lived
ContextCapsule and injects it as untrusted prompt data only inside the exact
personal canary. Browser Memory CRUD remains absent; Runtime defaults false and
Planner/Multi-Agent remain false.

PR #33 Linux acceptance exposed and fixed the last personal bootstrap defect:
the first successful invocation had no Capsule, so the first Memory could not
be published. Migration `0015` permits one zero-item/zero-token audit Capsule
without an empty prompt or SSE Memory metadata. The first real Memory binds
that Capsule and is retrieved on the next invocation. The final clean GitHub
Ubuntu run `31608502738` passed the guarded PostgreSQL and product acceptance
jobs and uploaded the independently verified receipt. Current personal head is
`0015`; `0016+`, tools, Planner, Multi-Agent, MCP and enterprise P34.7 remain
outside this acceptance. No P6 work was started in this PR.

## Personal P6.0 admission profile

Personal P6.0 means the single human Owner can operate a bounded, no-tool Agent
with durable identity/ledger, encrypted scoped Memory, one sealed first-party
instruction Skill, restart-safe no-replay recovery, incremental SSE/cancel,
kill switch and restore-new recovery. It does not mean the Phase 6 Marketplace/
MCP ecosystem or enterprise P34.7 has been activated. Those remain later
expansions after the personal product is stable.
