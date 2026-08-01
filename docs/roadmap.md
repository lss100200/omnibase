# OmniBase 分阶段路线图

> **日期**：2026-07-31
> **基于**：Phase 1.6 工程与 CPU benchmark 完成后的代码状态（`4a3655c`）
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
| API 基础设施 | ✅ 100%（待提交） | `/api/v1`、Request ID/访问日志、显式 CORS、请求边界、Redis 限流、实时主体/RBAC、离线模型边界；工程验收已通过，尚待原子提交 |
| Phase 3-4 安全 AI 工作空间与能力平台 | ⬜ 0% | 当前只有只读数据库元数据浏览；受控数据、API/SDK、模板、沙箱、能力网关与工作空间 UI 待建设 |
| Agent 编排 | ⬜ 0% | 仅 Celery 摄取任务 |
| Skill/MCP 扩展 | ⬜ 0% | 无插件系统、无 MCP |

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
| **P34.4 模板与空沙箱控制面** | HIGH | workspace 作为长期逻辑资源，run/session 作为可销毁执行实例；版本化模板、生命周期和无真实数据的空沙箱 |
| **P34.5 沙箱隔离后接只读网关** | HIGH | 文件/网络/进程/身份/资源隔离 Gate 先通过，再接 P34.2 只读能力；普通 Docker 仅作开发基线 |
| **P34.6 私有写入与 promotion** | HIGH | workspace 私有表/索引/记忆、完整 lineage，以及经审批、幂等和补偿进入规范资源的 promotion 流程 |
| **P34.7 生产总验收** | HIGH | 快照恢复、完整 UI/SDK、攻击矩阵、配额/撤销/补偿和生产 smoke；P34.7 通过前不得启动 Agent |

**不可跳过 Gate**：P34.0–P34.7 可以分批演示，但不能绕过前一增量的安全契约直接开放高权限能力；P34.7 未通过前，不得启动 Phase 5 Agent 编排。

长期硬约束：workspace 保存身份、配置、资源意图和私有状态，是长期逻辑资源；run/session 是带短期凭据与配额、可随时销毁重建的执行实例。普通 Docker 容器只能作为开发和生命周期验证基线，不代表能够安全运行任意敌对代码；未通过 P34.5 隔离 Gate 的运行时不得连接真实数据。

详细契约见 [Phase 3-4 统一实施计划](phase-3-4-secure-ai-workspace-implementation-plan.md)与 [Phase 3-4 威胁模型](phase-3-4-threat-model.md)。

---

## Phase 5: Agent 编排基础

> **前置条件**：Phase 3-4 的 P34.7 总 Gate（沙箱、能力网关、审批、配额、审计和恢复）全部通过
> **预估工期**：3-4 周
> **目标**：让 Agent 作为工作空间内的受约束负载执行复杂任务

| 任务 | 复杂度 | 说明 |
|---|---|---|
| **Agent 注册表** | HIGH | agent 类型、工作空间绑定、能力声明和工具绑定 |
| **Planner/Orchestrator** | HIGH | 输出受验证的任务 DAG；只调用工作空间已获授权能力 |
| **Specialist Agents** | HIGH | Librarian、Curator、Archivist、Engineer 的最小能力集 |
| **Tool 注册 + 调用** | HIGH | 参数 schema、capability 校验、超时、重试、结果大小和人工确认 |
| **Agent 状态管理** | MEDIUM | 会话记忆、任务进度、checkpoint、恢复和行为回放 |
| **工作流执行** | MEDIUM | 工作空间内的任务依赖、取消、结果聚合和资源配额 |

---

## Phase 6: Skill + MCP 扩展生态

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
| Agent 编排严格后置 | P34.7 未通过前不得实现自主 Planner、多 Agent 长循环或宿主级工具；Agent 必须作为工作空间内的受约束负载运行 |
| MCP 在工作空间和 Agent 之后 | 工作空间提供隔离边界，Agent 框架定义工具协议，MCP 是扩展实现之一 |
