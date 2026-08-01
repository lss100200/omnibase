# OmniBase Phase 1.6 收口与后续实施计划

> **日期**：2026-07-31
> **主要事实源**：`docs/handover-report.md`
> **代码核对基线**：分支 `phase-1-6-bge-m3-dual-index`，HEAD `4a3655c`
> **规划状态**：供执行与验收使用；涉及 V2 回填、生产切换、数据迁移或破坏性操作时，必须再次取得用户明确授权。

## 一、当前结论

OmniBase 当前已经完成 Phase 0、0.5、1、1.5，以及 P0 安全加固、前端性能/认证重构和 Phase 1.6 的代码与 CPU runtime benchmark。

Phase 1.6 必须分成两个层次理解：

1. **工程和评估能力已经完成**：V2 schema、1024 维 BGE-M3 embedding、双通道 store/retriever、shadow write、可恢复回填任务、版本比较框架、cutover gate、本地模型路径和 runtime benchmark 均已落地。
2. **生产采用尚未开始，也未被授权**：V1 仍是唯一权威主通道；没有执行全租户 V2 回填，没有在真实完整语料上完成生产质量验收，没有把线上检索切换到 V2，也不能删除或破坏 V1。

因此，Phase 1.6 的准确状态是：

> **代码与评估工具 100% 完成；生产回填与 cutover 保持冻结。**

## 二、统一后的阶段编号

交接报告和旧路线图存在阶段编号偏移。本计划采用以下统一编号：

| Phase | 内容 | 当前状态 |
|---|---|---|
| Phase 0 | Docker、多租户、JWT、文档、基础 UI | 已完成 |
| Phase 0.5 | 技术债清理与部署稳定化 | 已完成 |
| Phase 1 | RAG 内核、SSE、citation | 已完成 |
| Phase 1.5 | Celery 异步摄取、生命周期与可靠性硬化 | 已完成 |
| Phase 1.6 | BGE-M3 双索引工程与评估 | 代码/benchmark 完成；生产采用冻结 |
| Phase 2 | API 基础设施硬化 | ✅ 2026-07-31 工程完成、待原子提交 |
| Phase 3-4 | 安全 AI 工作空间与能力平台 / Secure AI Workspace & Capability Platform | 下一实施阶段；P34.0–P34.7 不可跳过 |
| Phase 5 | Agent 编排基础 | 待 Phase 3-4 P34.7 总 Gate |
| Phase 6 | Skill + MCP 扩展生态 | 待 Phase 3-4/5 |
| Phase 7 | 开源准备 | 远期 |

## 三、Phase 1.6 收口计划

### 3.1 立即收口：事实和文档一致性

目标是把“代码完成”和“生产未切换”同时表达清楚，避免后续 AI 或开发者误操作。

任务：

1. 更新 `docs/handover-report.md` 的 Git HEAD、阶段状态和历史遗留描述。
2. 更新 `docs/roadmap.md` 总览中的 `55%`、依赖图中的“暂停”和详细章节中的 `100%`，统一为“工程完成、生产 cutover 冻结”。
3. 更新 `README.md` 的 Phase 1.6 “规划中”旧口径。
4. 明确区分：
   - benchmark 通过不等于检索质量 gate 通过；
   - shadow write 可用不等于所有文档已经覆盖 V2；
   - V2 可查询不等于 V2 可以成为 primary；
   - 生产切换必须保留 V1 回滚能力。
5. 按关注点创建原子提交，排除 `.env`、模型权重、`.omo/`、`.zcode/`、运行 continuation、字体缓存和临时统计脚本。

### 3.2 保持冻结的事项

在用户明确批准生产评估前，不执行：

- 全租户 V2 批量回填；
- 把 `embedding_index_version` 切换为 V2；
- 删除 `embeddings`、512 维索引或 V1 模型；
- 让 V2 shadow write 失败阻断 V1 主流程；
- 在常规测试库或真实用户数据上运行 cleanup/破坏性命令；
- 把 BGE-M3 权重加入 Git 或生产镜像源码层。

### 3.3 可选生产采用：只有用户明确授权后执行

生产采用分为五个 gate，任何一个未通过都不得 cutover。

#### Gate A：模型与资源准备

- 建立 BGE-M3 模型制品来源、校验和、版本和挂载路径记录。
- 验证 worker、backend 和 benchmark 环境加载的是同一模型版本。
- 测量单模型与双模型并存时的 RSS、CPU、冷启动和并发退化。
- 设置 worker 并发与队列隔离，避免回填抢占 V1 摄取和在线问答。

#### Gate B：隔离回填与覆盖率

- 先使用专用测试租户，再选择一个可回滚的 canary 租户。
- 回填任务必须可恢复、可重复运行且不产生重复 chunk。
- 对每个文档核对 `rag_document_index_state`：readiness、chunk_count、attempt_count、error_detail。
- 要求 V2 覆盖率 100%，文档数和 chunk 数差异为 0，失败文档有可审计原因。

#### Gate C：真实质量评估

- 使用代表真实中文知识库场景的固定评估集，而不是仅使用合成样本。
- 固定 query、期望文档、相关性标注、版本和运行参数。
- 四项 cutover gate 全部通过：
  - V2 `recall@5` 达到既定阈值；
  - V2 不低于 V1；
  - 关键问题无不可接受退化；
  - 每项结论都有可复现证据。
- 单独检查 citation 正确性、长文档、中文短 query、代码片段和低召回问题。

#### Gate D：运行时与故障演练

- 验证 warm query、cold query、batch 32、并发查询和回填并行时的延迟。
- 验证模型不可用、内存不足、worker 重启、Redis 中断和数据库瞬态故障。
- 验证 V2 失败不会污染或中断 V1 权威路径。
- 建立监控指标：V2 coverage、backfill failure、query latency、RSS、queue depth、质量对比结果。

#### Gate E：灰度、回滚和 cutover 决策

- 先做只读 shadow query 和结果对比，不直接影响用户结果。
- 灰度必须能按租户、请求或配置快速关闭。
- cutover 后至少一个稳定周期内保留 V1 索引和模型。
- 回滚只允许切回 V1，不允许临时重建或删除 V1。
- 最终切换必须由用户明确批准，并形成独立变更记录。

## 四、Phase 2：API 基础设施硬化

> **预计工期**：2–3 周
> **目标**：在数据库管理、AI 工作空间、Agent 和扩展生态之前，建立稳定、可观测、可授权、可限流的 API 边界。

### 4.1 P2.0：端点与权限盘点

交付物：

- 当前全部 API 的方法、路径、鉴权、租户上下文、输入上限和调用方矩阵。
- 前端 `lib/api.ts`、SSE、上传、worker 回调和测试中的旧路径清单。
- 标记匿名、普通用户、租户管理员和内部 worker 的权限边界。
- 确认 P0 禁止面继续保持 404，例如原始 SQL 和未授权租户管理接口。

验收：任何路径迁移前都有完整兼容清单，不能靠全仓盲目替换。

### 4.2 P2.1：API 版本化

任务：

- 把公开接口统一迁移到 `/api/v1/`。
- 同一原子变更中更新后端 router、前端客户端、SSE 调用、测试和文档。
- 明确旧路径策略：无外部消费者时优先一次性迁移；如保留兼容层，必须设置移除日期并避免双实现。
- 健康检查 `/health` 和前端 `/healthz` 不强制纳入业务版本前缀。

验收：

- 新路径测试全绿；
- 前端开发和生产模式均能登录、刷新 token、上传、检索和问答；
- 已禁用的旧特权路径仍返回 404；
- 不出现重复 OpenAPI operation id 或两套路由逻辑漂移。

### 4.3 P2.2：Request ID 与结构化访问日志

任务：

- middleware 生成或校验 `X-Request-Id`，并回写响应头。
- Request ID 注入 structlog context、异常日志和关键 Celery dispatch 日志。
- 对 SSE 记录开始、首 token、终止状态和总耗时，不记录 prompt、token、凭据或文档正文。
- 定义日志字段：method、route template、status、duration、tenant logical id、user id、request id。

验收：浏览器响应、后端日志和异步任务可以通过安全标识关联；日志中不存在 JWT、密码、API key 和物理租户敏感信息。

### 4.4 P2.3：请求边界与 CORS

任务：

- CORS method/header/origin 改为显式 allowlist。
- 全局 multipart/upload 限制在完整读取 body 前拒绝超限请求。
- 明确 JSON body、查询参数、分页上限和 SSE 连接时长。
- 保持生产端口仅绑定 loopback，默认不暴露到外网。

验收：

- 允许的预检成功，未允许的 origin/method/header 被拒绝；
- 超限上传稳定返回 413，不产生 MinIO 残留对象和数据库半成品；
- chunked request 和伪造 Content-Length 不能绕过限制。

### 4.5 P2.4：Redis 速率限制

建议初始策略：

- 登录/注册：每来源约 5 次/分钟；
- RAG search/ask：每租户或用户约 10 次/分钟；
- 上传：按用户和并发任务数限制；
- 健康检查不进入业务限流桶。

设计要求：

- 限流 key 必须包含正确的租户/用户维度，不能跨租户串桶。
- 明确 Redis 故障时 fail-open 或 fail-closed；认证防暴力接口与普通读接口可以采用不同策略。
- 返回标准 429、`Retry-After` 和不泄露内部实现的错误体。

验收：429 可稳定复现，窗口恢复正常；并发和代理来源处理符合部署模型。

### 4.6 P2.5：租户内 RBAC

任务：

- 正式使用现有 `is_tenant_admin`，定义普通成员与租户管理员权限矩阵。
- 授权必须从当前租户数据库状态读取，不能只相信 JWT 自声明角色。
- DDL、成员管理、迁移和未来扩展安装默认要求管理员权限。
- 对跨租户 ID、伪造 schema claim、停用用户和停用租户全部 fail-closed。

验收：

- 普通用户不能执行管理员操作；
- 管理员权限不能跨租户继承；
- 修改角色后权限在合理时间内生效；
- 安全回归覆盖水平越权和垂直越权。

### 4.7 P2.6：Phase 2 总验收

必须通过：

- 后端和前端现有测试基线不回退；
- typecheck、lint、production build、Compose config 全绿；
- 开发前端和 standalone 生产前端 smoke 全绿；
- 跨租户、伪造 JWT、CORS、上传、限流和 RBAC 安全矩阵通过；
- 关键 API 具备 request-id、结构化耗时和错误分类；
- 无凭据、模型、用户数据或本地运行工件进入 Git。

**2026-07-31 验收结果**：上述工程门禁已通过。后端 320 个非集成测试、sentinel 隔离环境 11 个集成测试、前端 43 个测试以及 typecheck/lint/production build 均通过；3001 standalone 生产前端仅连接独立网络并完成真实登录 smoke。Phase 2 当前是“工程完成、待原子提交”，不得把尚未提交的工作树误写成已进入 Git 历史。Reranker 本地制品当前缺失，因此运行时默认禁止隐式外网下载并快速降级到 RRF；该制品安装与启动预热列为 Phase 3-4 前的部署优化，不阻塞 P2 安全边界收口。

## 五、Phase 3-4：安全 AI 工作空间与能力平台 / Secure AI Workspace & Capability Platform

> **预计工期**：6–9 周，按 P34.0–P34.7 独立、可演示增量推进。
> **前置条件**：Phase 2 版本化、Request ID、请求边界、限流和 RBAC 工程验收完成，并先形成可回滚的原子提交。
> **架构定位**：受控数据能力、API/SDK 解耦、模板、沙箱和能力网关不是两个可分离的产品阶段，而是同一授权闭环。先让人工任务在安全空间中完成真实数据闭环，再开发完整 Agent 编排。

### P34.0：威胁模型、逻辑资源与契约冻结

- 定义宿主、租户、工作空间、规范数据、工作空间私有数据和派生数据边界。
- 定义逻辑资源 ID、能力动作词汇、审批风险级别、OpenAPI、统一错误和审计事件契约。
- 覆盖提示注入、恶意代码、目录穿越、符号链接逃逸、内网探测、凭据窃取、跨租户访问和资源耗尽。
- 先建立攻击测试和 fail-closed 契约，再选择或开放具体运行时能力。

Gate：物理 tenant schema、MinIO 内部定位、宿主凭据和规范数据写路径不得出现在公开 API、SDK、模板、日志或客户端状态中。

### P34.1：Resource Registry、Audit、Operation、Approval 与 Idempotency 基座

- 建立逻辑 Resource Registry：记录资源类型、租户/工作空间所有权、数据分类、生命周期、版本和不透明 backing reference；公开层只使用逻辑 resource ID。
- 建立 append-only Audit Event：统一记录 request、actor、tenant、workspace、run/session、operation、capability、结果和拒绝原因。
- 建立 Operation 状态机：`pending_approval → queued → running → succeeded/failed/cancelled → compensating/compensated`，支持查询、取消和关联证据。
- 建立 Approval 对象：记录请求能力、精确 scope、风险等级、理由、有效期、批准者以及“仅本次/限时/缩小范围”决策。
- 建立 Idempotency Ledger：绑定 tenant、actor、operation、idempotency key 和 payload hash，防止重复提交与参数漂移。
- 本增量只建立安全元数据和状态基础，不开放新的 CRUD、DDL 或工作空间数据访问能力。

Gate：资源枚举越权、审计篡改、操作状态非法跃迁、重复审批、idempotency key 重放和 payload 冲突全部 fail-closed；后续所有读写能力必须复用这些基础对象。

### P34.2：只读能力网关与 SDK 契约

- `/api/v1` 只通过 Resource Registry 接受逻辑 resource ID，不暴露物理 schema、表名映射、MinIO 定位或内部索引实现。
- 首批 capability 仅允许 `data.schema.read`、`data.rows.read`、`rag.search` 和 `rag.citations.read`；不开放任何写入、DDL、网络或配额提升能力。
- 网关在每次调用重新执行 RBAC、tenant scope、资源状态、capability scope、次数/流量限制和撤销检查，并写入 Audit/Operation。
- 冻结 TypeScript/Python SDK 只读契约、统一错误结构、request ID、cursor 分页、SSE/citation 和 OpenAPI breaking-change 检查。
- 人类和外部程序可使用有 scope、有效期、即时撤销、哈希存储和一次展示的 PAT；workspace/run 不使用 PAT，只能由 broker 获取短期 capability。

Gate：伪造 capability、逻辑资源 ID、用户或 tenant claim 均 fail-closed；撤销后新请求立即失败；SDK 和 API 无法越过只读边界或推断物理资源。

### P34.3：结构化 CRUD、DDL plan/apply、审批与补偿

- 在 P34.1/P34.2 基础上建立白名单类型系统和 DDL builder，禁止拼接或执行任意用户 SQL。
- 所有写操作拆成结构化 plan/apply：先生成变更 diff、锁风险、影响范围、所需 capability 和补偿策略，再进入审批或执行。
- 提供参数化行级 CRUD、cursor 分页、排序、过滤、乐观并发、幂等写入和有界 CSV/JSON 导出。
- DDL 支持表创建、列增加/重命名/删除和安全类型变更；危险或不可逆变更必须经过 Approval，并以 Operation 跟踪执行和补偿。
- 失败后执行明确的 compensation 或进入 `manual_intervention_required`，不得伪报成功或自动重试非幂等步骤。

Gate：SQL/标识符注入、越权资源、危险 DDL、长事务、锁竞争、大导出、重复 apply、并发覆盖和补偿失败测试全部通过；原始 SQL API 继续保持不可达。

### P34.4：模板、工作空间生命周期控制面与空沙箱

- **workspace 是长期逻辑资源**：保存身份、模板来源、配置、资源绑定意图、能力申请、私有状态和 lineage；暂停或没有运行实例时仍然存在。
- **run/session 是可销毁执行实例**：每次运行拥有独立 ID、短期凭据、配额、日志和终止状态，可随时销毁并重新创建，不等同于 workspace。
- 从版本化、脱敏、无凭据模板创建 workspace；模板内置 SDK、manifest、测试和能力声明，不复制宿主运行态。
- 建立创建、启动、暂停、恢复、终止、升级、归档和可恢复删除控制面；先运行无真实数据、无宿主能力的空沙箱任务。
- 普通 Docker 容器只作为开发基线，用于验证模板和生命周期，不宣称能够安全运行任意敌对代码。

Gate：模板可复现、可校验、可升级和可回滚；workspace 与 run/session 生命周期不会混淆；空沙箱不含宿主凭据、租户数据、真实 RAG/数据库绑定或隐式网络权限。

### P34.5：沙箱隔离 Gate 后接入只读能力网关

- 在接入任何真实数据前，完成文件系统、挂载、符号链接、进程、系统调用、网络、身份、secret 注入和资源配额的隔离实现与攻击测试。
- 网络默认拒绝；禁止 Docker socket、metadata service、宿主私网和未授权外部目标；运行实例非 root、drop capabilities、no-new-privileges。
- 限制 CPU、内存、磁盘、文件数、进程数、执行时间、并发、网络流量和输出大小，run/session 超限时有界终止。
- 只有隔离 Gate 通过的运行时才能接入 P34.2 的只读能力网关，且仅允许指定 `data.schema.read`、`data.rows.read`、`rag.search` 和 citation。
- 普通 Docker 基线不能作为敌对代码安全证明；若所选强化运行时未达到 P34.0 威胁模型，必须保持无真实数据连接。

Gate：路径/符号链接/挂载/网络/进程逃逸、凭据窃取、跨 workspace/tenant、capability 伪造和资源耗尽探针全部失败；恶意 run/session 不影响核心 `/health/ready`。

### P34.6：工作空间私有写入、派生数据、lineage 与 promotion

- 首先只开放 workspace 私有存储、私有表、私有派生索引、私有记忆和实验状态写入；不得直接写规范 RAG、权威 embedding 或租户核心表。
- 每项派生数据记录来源 resource、源版本、生成 run/session、模板/代码版本、操作链和 citation lineage。
- 从私有区进入租户规范数据必须走显式 promotion plan：展示 diff、目标资源、风险、审批、幂等 key、Operation 和补偿方案。
- promotion 只能调用 P34.3 的结构化 apply 能力，不能由 workspace 自行扩权或绕过 Approval/Resource Registry。
- UI 初步提供私有数据、派生物、lineage、promotion 请求、能力、审批、资源和审计查看。

Gate：私有写入物理隔离；伪造 lineage、重复 promotion、越权目标、撤销后写入、失败补偿和规范数据覆写测试全部通过。

### P34.7：快照恢复、完整 UI/SDK、安全攻击与生产总验收

- 完成数据、AI 空间、开发者和审批中心导航，以及 workspace 概览、run/session、文件、数据、能力、资源、日志、快照和设置页面。
- 日志分 runtime、gateway、audit 和 security denial，统一 request/run/workspace/operation ID，敏感字段服务端脱敏。
- 快照覆盖 workspace 文件、配置、私有数据和派生索引 lineage；恢复保留历史并创建新恢复点。capability grant、token 和存活 run/session 不进入快照。
- 完成 TS/Python SDK quickstart、API Explorer、curl/代码示例、错误跳转审批和从只读到私有写入/promotion 的端到端契约测试。
- 在候选生产运行时完成完整攻击矩阵、配额终止、撤销、审批、补偿、快照恢复、升级、回滚和核心可用性验收。

最小可用闭环：

1. 用户从 `RAG Research Lab` 模板创建空间。
2. 在已通过 P34.5 隔离 Gate 的 run/session 中绑定一个只读知识集合和一个 workspace 私有 `findings` 表。
3. 网络默认关闭，并设置 CPU、内存、磁盘和运行时间配额。
4. 人工触发任务；SDK 经只读能力网关检索 RAG，并携带 citation 写入私有 `findings`。
5. 任务发起从私有 findings 到受控租户资源的 promotion；用户仅本次批准或拒绝，网关记录完整 Operation/Audit/lineage 并可补偿。
6. 用户创建 workspace 快照、修改后恢复；授权、token 和旧 run/session 不随快照恢复。

总 Gate 必须同时证明：

- 宿主 `.env`、Docker socket、其他工作空间、其他租户、物理 schema 和核心凭据不可访问；
- 路径、符号链接、网络、capability、逻辑 ID、SQL/标识符和资源耗尽攻击全部 fail-closed；
- 规范 RAG 与核心数据库不可被覆写，派生数据具有来源、版本和 lineage；
- 所有高风险行为可审批、可撤销、可审计、可暂停、可终止、可恢复；
- 工作空间恶意代码、崩溃和超额运行不影响核心工作台可用性；
- workspace 长期状态与可销毁 run/session 边界、凭据和资源均正确隔离；
- 不把普通 Docker 开发基线表述为可安全运行任意敌对代码的生产沙箱；
- 生产构建、SDK quickstart、跨租户安全矩阵和真实最小闭环 smoke 全部通过。

**不可跳过规则**：P34.0–P34.7 可以分批演示，但任何增量不得绕过前置 Gate 暂时开放直连数据库、宿主文件、无限网络或长期凭据。P34.7 未全部通过前，不得启动 Phase 5 自主 Planner、多 Agent 长循环或宿主级工具执行。

## 六、Phase 5：Agent 编排基础

> **预计工期**：3–4 周
> **前置条件**：Phase 3-4 P34.7 的沙箱、能力网关、审批、配额、审计和恢复总 Gate 全部通过。

架构原则：

- Agent 默认创建并运行在 AI 工作空间内，而不是作为宿主后端中的无限权限组件。
- Planner、Specialist 和工具调用只能看到工作空间被授予的能力。
- “自由发展”指在工作空间边界内自由生成代码、状态、工具和协作策略，不代表获得宿主或租户数据的隐式权限。

实施顺序：

1. Agent manifest、能力声明和工作空间绑定。
2. Planner 输出受验证的任务 DAG，而不是直接执行任意宿主代码。
3. Librarian、Curator、Archivist、Engineer specialist 的最小能力集。
4. 工具参数 schema、capability 检查、超时、重试和结果大小限制。
5. 会话状态、checkpoint、恢复和人工确认节点。
6. 工作空间内的任务 DAG、取消、结果聚合和资源配额。
7. 审计日志、成本/调用次数、失败可观测性和行为回放。
8. Agent 生成的新工具或代码先进入工作空间私有区，通过评估后才能申请更高能力。

Gate：Agent 不得绕过工作空间和能力网关、直接使用物理 schema、读取 `.env`、把文件 bytes 或请求上下文传入队列，也不得执行未经批准的破坏性动作。

## 七、Phase 6：Skill + MCP 扩展生态

> **预计工期**：2–3 周
> **前置条件**：Phase 3-4 工作空间安全边界和 Phase 5 Agent 工具协议、权限模型、状态管理稳定。

实施顺序：

1. MCP client：在工作空间边界内完成连接、工具发现、超时、错误隔离和认证。
2. MCP server：通过能力网关暴露 RAG、文档和数据库能力，不直接暴露宿主内部实现。
3. Skill/plugin manifest、版本、依赖、能力声明和适用工作空间范围。
4. Hook/event bus，所有事件 payload 脱敏并设置大小限制。
5. 扩展安装、启用、禁用、升级、隔离测试和回滚。
6. Agent 可以在私有工作空间内创建实验 Skill，但发布到共享层前必须经过人工审核和安全评估。
7. 前端扩展管理界面。

Gate：扩展默认最小权限，不能获得直接宿主文件系统、凭据、物理租户 schema、Docker socket 或无限网络访问；扩展故障只能影响所属工作空间。

## 八、Phase 7：开源准备

> **预计工期**：1–2 周
> **前置条件**：核心功能和安全边界稳定。

交付：

- README、架构、部署、API、贡献和安全文档；
- 一键 Demo 和脱敏示例数据；
- CI：lint、typecheck、test、build、镜像和安全扫描；
- secret scan、依赖和镜像漏洞扫描；
- Issue/PR 模板、Code of Conduct 和发布流程；
- 第三方安全审查与已知限制声明。

## 九、跨阶段永久约束

以下约束不因阶段变化而放宽：

1. V1 未经批准不得删除或破坏。
2. 常规测试库不得运行 destructive cleanup。
3. 不开放任意原始 SQL、默认租户枚举或跨租户管理面。
4. 服务端口默认只绑定 loopback。
5. Git、日志和证据中不得出现密码、JWT、API key、授权头、活跃 `.env` 或模型权重。
6. Celery payload 只传持久标识，不传文件 bytes、向量、凭据、HTTP headers 或请求上下文。
7. 所有租户数据操作必须在明确的 tenant scope 内执行。
8. 工作空间是 Agent、Skill 和 MCP 的默认执行边界；它们均通过能力网关和授权层访问核心资源，不得直接绕过 API、RBAC 或沙箱。
9. workspace 是长期逻辑资源；run/session 是带短期凭据和配额、可销毁重建的执行实例，两者身份、状态、权限和生命周期不得混淆。
10. 普通 Docker 只作为开发和空沙箱生命周期基线，不声称可以安全运行任意敌对代码；未通过 P34.5 隔离 Gate 的运行时不得连接真实数据。
11. 每个阶段必须具备可回滚、可观测、可审计和有界资源使用。

## 十、建议的近期执行顺序

1. 保护并复核 Phase 1.6 与 Phase 2 已验收工作树，按关注点完成原子提交；Phase 2 在提交前不得标记为已进入 Git 历史。
2. V1 继续作为权威主通道，不执行未经授权的 V2 回填或 cutover。
3. 启动 Phase 3-4 P34.0，先冻结威胁模型、逻辑资源、能力词汇和 API/SDK 契约。
4. P34.1 先建立 Resource Registry、Audit、Operation、Approval 和 Idempotency；P34.2 只开放只读能力网关与 SDK 契约。
5. P34.3 才开放结构化 CRUD/DDL plan/apply；P34.4 建立模板、workspace 控制面和不接真实数据的空沙箱。
6. P34.5 通过隔离攻击 Gate 后接入只读能力；P34.6 再开放 workspace 私有写入、lineage 和 promotion。
7. 以 P34.7 快照恢复、完整 UI/SDK、最小闭环和生产攻击矩阵收口 Phase 3-4。
8. 只有 P34.7 总 Gate 全部通过，才启动 Phase 5 Agent 编排；Phase 6 继续等待 Phase 3-4/5 稳定。

## 十一、阶段完成定义

一个阶段只有同时满足以下条件才可标记完成：

- 代码实现完成；
- 单元、集成、安全和必要的 live smoke 通过；
- 生产构建与运行方式验证；
- 性能和资源边界有数据；
- 回滚路径可执行；
- 文档、Git HEAD 和路线图口径一致；
- 用户需要批准的高风险动作已经获得明确授权；
- 不存在必须由下一阶段兜底的未记录关键风险。
