# Phase 5 Agent Runtime 与受控编排统一实施计划

> 状态：`PLANNED / FROZEN`，运行时代码尚未解冻。
>
> 硬前置：P34.7 production total Gate 全部通过并有可复现证据前，只允许维护本计划、数据契约草案、威胁模型和离线验证器；不得启动自主 Planner、多 Agent 长循环、宿主级工具或连接 non-disposable tenant/RAG 的 Agent Runtime。
>
> 授权边界：用户批准继续 P34.7 和后续 Phase 5，不等于 P34.7 已经通过。P5.0 必须根据当前提交、部署哈希、迁移、证据和运行方式独立判定；任何缺项都使 Phase 5 继续保持冻结。

## 1. 目标与设计原则

Phase 5 的目标不是把一个大模型直接接到数据库、终端和网络，而是把 Agent 建模为 Workspace 内可撤销、可预算、可审计、可恢复的短期受约束 workload。用户拥有 Workspace 和长期资源，Agent Run 只拥有短期执行意图；每个工具调用都必须经过 Capability Gateway、Operation/Idempotency、预算、Audit、Lease/fencing 和显式恢复规则。

固定原则：

1. Planner 只生成计划，不执行副作用；Executor 不能扩大计划或自行签发能力。
2. Browser session、Tenant admin、Agent definition、模型身份和工具名称都不是授权依据；实时 Workspace membership、Run/Node/Lease、workload identity、Capability Grant 和 Resource/version binding 才是授权依据。
3. Agent、Sandbox、Runner 永不获得 PostgreSQL、Redis、MinIO、JWT/signing key、宿主 `.env`、Docker socket、宿主路径或成员 Overlay identity。
4. 所有外部效果采用 `pending -> committed|failed|unknown`；跨 provider boundary 后结果不明确时禁止自动 replay。
5. 长期记忆采用受治理的用户/Workspace 资源库与按需 RAG，不在每轮完整注入用户历史。
6. 多 Agent 只能在 server-validated DAG 中协作；子 Agent 的 Grant、预算、TTL、工具集合和 Resource scope 必须严格小于或等于父任务。
7. 产品 Skill 只是版本化、签名、可审计的能力声明与工作流模板，不能绕过 Gateway 或沙箱；任意 MCP/第三方 Skill marketplace 属于 Phase 6。

### 1.1 权威组件与调用方向

Phase 5 必须把自然语言推理与安全授权彻底分离：

```text
Browser / User
  -> Main ASGI Agent Control API
  -> Agent Invocation + Operation + Approval + Idempotency
  -> trusted Runtime Coordinator
  -> Planner Sandbox workload
  -> deterministic Plan Validator
  -> validated bounded DAG
  -> task-scoped Executor Sandbox workloads
  -> Capability Gateway / Model Gateway / Skill contracts
  -> Artifact / Workspace Data / Memory
```

- **Browser Agent Control Plane**：只负责 Definition/Version、Workspace installation、Invocation、Approval、Cancel、Reconciliation、Memory/Skill 治理和证据查询。它不直接调用 Sandbox provider，不接收 workload token，不代理模型 Provider Key，也不把 Browser JWT 转换为 workload identity。
- **Runtime Coordinator**：只推进状态机、计算 DAG readiness、分配 Lease/fencing、预留预算、装配短期 capability、持久化 dispatch intent、执行 revoke/cancel/reconciliation；它不运行 LLM，不执行 Workspace 命令，不把自然语言直接转换为副作用。
- **Planner workload**：只读取有界 Goal Artifact、AgentVersion、Skill Manifest 和 Context Capsule，并提交严格 `PlanProposal`。它不持有 write/promotion/admin/emergency-control capability，不决定审批、预算或 capability 是否通过。
- **Plan Validator**：运行在可信 Core 中，以确定性代码检查 schema、DAG、版本、预算、风险、capability、Approval 和 Resource scope。Planner 输出在 Validator 接受前没有执行权。
- **Executor workload**：每个 DAG 节点使用独立 Attempt、Run/runtime/workload identity、Task Lease、fencing、Grant 和预算；不能继承 Planner 或兄弟节点的 token、Lease、可写文件层或能力。
- **Aggregator**：只读取 committed Task outputs 并生成 Final Result Artifact；如需新增副作用，必须提交新的 Plan Amendment，再过 Validator、预算与 Approval。

### 1.2 继续冻结的非目标

- 任意 SQL、数据库连接串、物理 locator、Provider handle 或宿主路径下发。
- Sandbox/Runner 直连 PostgreSQL、Redis、MinIO、宿主 LAN 或成员 Overlay。
- Planner 自行签发 capability、增加预算、批准自己或修改安全策略。
- Memory、Skill、RAG、Artifact 或模型输出覆盖平台安全内核。
- 无界 ReAct、自主递归、无限工具调用、无限 replan 或无限子 Agent。
- Agent 自动执行 promotion、canonical mutation、发布、merge、push 或 deploy。
- 从 `pending|unknown` 自动 replay 外部副作用。
- 恢复旧 Run、Lease、token、runtime/workload identity、PID、socket、connection 或 provider handle。
- 第三方 MCP、开放 Marketplace、动态依赖下载和远程未审查 Skill；这些继续属于 Phase 6。

## 2. 解冻条件：P5.0 Admission Gate

P5.0 不运行 Agent，只验证 Phase 5 是否可以开始。下列条件必须同时成立：

- P34.5 Runner、Network Broker、Overlay、mTLS Gateway 的 clean-checkout/source-built Gate 可复现；目标 Linux Runner 的 non-root UID/GID、namespace、cgroup、seccomp、LSM、replay 和 kill proof 有最终证据。
- P34.6 Foundation/Contracts/Fail-closed primitives 已提交并通过 CI；Promotion/Restore 未完成的成功路径仍保持拒绝。
- P34.7 完成 production Core↔Runner/Broker/Gateway 联合装配、non-disposable 最小 tenant/RAG、真实成员 Overlay/DERP/node-compromise、provider-backed snapshot/restore rehearsal、reconciliation、容量/SLA 和 production smoke。
- Capability、Approval、Operation、Idempotency、Audit、quota、Run/Network Lease 与 recovery runbook 的文档和源码一致。
- 默认生产 composition 在缺少任一 attestation、adapter、key、registry、Grant 或 recovery component 时返回拒绝/不可用，而不是降级放行。
- 建立 `AGENT_RUNTIME_ENABLED=false`、`AGENT_PLANNER_ENABLED=false`、`MULTI_AGENT_ENABLED=false` 三个独立、默认关闭的 server-owned feature Gate；配置错误和未知值 fail-closed。
- 建立 P34.7 Evidence Manifest 验证器，至少绑定 source commit、dirty scope、Runner/Broker/Gateway/Overlay/Workspace-data/provider artifact digest、migration head、OpenAPI/SDK snapshot、production composition 和 runbook 版本；缺失、漂移或无法 clean-checkout 复现时不得解冻。
- P5.0 只允许威胁模型、契约、负向 fixture、validator 和文档工作；不得以“先实现但不开放”为理由预装自主 Planner、Executor 或多 Agent scheduler。

任一条件缺失时，Phase 5 只能停留在规划和离线契约阶段。

P5.0 完成定义：P34.7 的所有 production Gate 已独立复验、Critical Veto 为 0、Evidence Manifest 与当前源码一致，且 Phase 5 三个 feature Gate 仍保持关闭。只有随后单独进入 P5.1 时才允许新增 Agent Runtime 领域代码。

## 3. 数据模型与权威边界

建议新增以下全局治理实体；所有实体包含 `tenant_id`、版本、状态闭集、创建者和时间，并使用复合租户外键：

- `AgentDefinition`：稳定逻辑身份、所属 Tenant、允许安装范围和风险等级。
- `AgentVersion`：不可变模型/系统策略/工具声明/输入输出 schema 的规范摘要；新版本不能原地覆盖。
- `WorkspaceAgentBinding`：AgentVersion 安装到 Workspace 的显式 binding、启停状态、默认预算和 Resource scope。
- `AgentTask`：用户批准的任务意图、request hash、Workspace generation、风险等级和截止时间。
- `AgentRun`：绑定 P34.4 Run、runtime instance、workload identity、Node/Run fencing、模型配置摘要和全局预算。
- `AgentPlan`：Planner 输出的不可变 canonical DAG、schema version、输入摘要、审批状态和 plan digest。
- `AgentStep`：DAG 节点、依赖、AgentVersion、工具闭集、预算、超时、重试策略和状态。
- `AgentAttempt`：一次具体执行尝试；记录 lease/fencing、模型请求/响应摘要、token 使用和安全 reason code。
- `AgentToolCall`：精确绑定 Step/Attempt、Capability Grant、Operation、Idempotency、参数 hash、结果 digest 和 effect state。
- `AgentCheckpoint`：只保存可恢复的逻辑状态、已提交结果引用、DAG cursor 和预算账本摘要；不保存 credential、PID、socket、连接或 provider handle。
- `ContextCapsule`：Memory Compiler 生成的短期、不可委派、可过期上下文包，绑定 user/Workspace/Agent/Task、来源 Resource/version、token 预算和内容摘要。
- `MemoryCandidate`：Agent 提议写入长期记忆的候选项；默认不自动进入用户库，必须经策略过滤、去敏、归并和必要的人工确认。

建议对应物理表按职责拆分为：

- Registry：`agent_definitions`、`agent_versions`、`workspace_agent_installations`。
- Invocation/Plan：`agent_invocations`、`agent_plan_versions`、`agent_plan_nodes`、`agent_plan_edges`、`agent_plan_amendments`。
- Execution：`agent_task_attempts`、`agent_task_lease_cursors`、`agent_task_leases`、`agent_checkpoints`、`agent_reconciliation_cases`。
- Usage/effect：`agent_model_invocations`、`agent_tool_invocations`、`agent_usage_reservations`。
- Memory governance：`memory_candidates`、`memory_items`、`memory_item_versions`、`memory_conflicts`、`memory_context_capsules`、`memory_feedback`、`memory_deletion_jobs`。
- Skills：`skill_definitions`、`skill_versions`、`workspace_skill_installations`、`skill_evaluation_runs`、`skill_revocations`。

已有 `OperationRecord`、`ApprovalRequest`、`IdempotencyRecord`、`AuditEvent` 与 capability reservation 继续是安全生命周期的权威；上述 Agent 表只补充领域状态，禁止建立第二套可绕过的审批、审计或幂等系统。Alembic revision 编号只能在 P34.7 最终基线合并后确定，不得在计划中预先假定为固定 `0010`。

状态机必须由数据库 CHECK、唯一约束和必要的不可变 Trigger共同保护。终态 Run/Attempt/ToolCall 不得回到运行态；旧 generation、旧 fencing 或旧 workload identity 不能恢复执行。

### 3.1 身份层级与精确绑定

以下身份必须分离，知道上层 UUID 绝不自动产生下层权限：

```text
AgentDefinition
  -> AgentVersion
  -> WorkspaceAgentBinding
  -> AgentTask / Invocation
  -> AgentPlanVersion
  -> AgentStep
  -> AgentAttempt
  -> Workspace Run
  -> RuntimeInstance
  -> WorkloadIdentity
```

Planner/Executor 的每次模型或工具调用至少绑定：Tenant、Workspace、Workspace generation、AgentDefinition/Version/digest、Workspace binding、Task/Invocation generation、Plan ID/version/digest、Step、Attempt、Run、runtime instance、workload thumbprint、Node、Run Lease、Node/Run fencing、Task Lease、Task fencing、Capability Grant、Operation 和 deadline。任何 binding 缺失、过期、撤销或漂移都必须在 provider 调用前拒绝。

新增 Task Lease，但复用 P34.4 的实时 Node/Run attestation；Task Lease TTL 不得晚于 Run Lease、Node attestation、Task deadline 和 Capability Grant 中的最早 expiry。Retry 创建新的 Attempt 和更高 Task fencing；Pause、Cancel、Agent revoke、Workspace generation 变化、Run Lease revoke或 Node re-fence 立即使旧 Task Lease 失效。Task/Invocation generation 只能递增，不能为恢复旧 holder 而重置。

### 3.2 状态机

建议冻结以下闭集状态机，并由数据库 CHECK、部分唯一索引、复合外键和必要 Trigger 保护：

```text
AgentInvocation:
created -> planning -> awaiting_approval -> scheduled -> running
        -> paused | blocked_unknown -> succeeded | failed | cancelled

AgentPlanVersion:
proposed -> validated -> rejected | awaiting_approval -> active
         -> superseded | completed

AgentTaskAttempt:
pending -> ready -> leased -> dispatching -> running
        -> committed | failed | unknown | cancelled

AgentToolInvocation / AgentModelInvocation:
reserved -> dispatching -> committed | failed | unknown
```

- `succeeded|failed|cancelled|committed|unknown` 等终态不得回到运行态。
- `unknown` 只能进入 reconciliation；不能回到 `reserved|dispatching`。
- Plan Amendment 创建新 PlanVersion，不更新已批准的旧 DAG。
- Cancel 或 Plan Amendment 提高 Invocation generation，使旧 Plan/Task holder 被 fence。
- Exact committed replay 只返回原结果，不再次调用 model/tool/provider 或重复扣费。

### 3.3 建议新增的维护者不变量

- `INV-025 agent-identity-layering`：Definition、Version、Workspace binding、Task/Invocation、Attempt、Run、Runtime 与 Workload Identity 分别验证，能力不可隐式继承。
- `INV-026 planner-proposal-not-authority`：Planner 输出只是提案；只有确定性 Validator、预算和 Approval lifecycle 能使计划可调度。
- `INV-027 task-lease-monotonic-fencing`：Task heartbeat、结果和 checkpoint 精确绑定当前 Lease、generation 与 Node/Run/Task fencing；stale holder 永久拒绝。
- `INV-028 agent-tool-effect-no-replay`：provider boundary 前 durable reserve；`pending|unknown` 占用预算并阻止自动 replay。
- `INV-029 memory-scope-provenance-budget`：Memory 绑定 Tenant/User/Workspace/Agent、来源、版本、敏感级别和生命周期；Capsule/search 均有 token/调用预算。
- `INV-030 skill-provenance-confinement`：只运行已安装的 exact SkillVersion/digest；Skill 不得 wildcard capability、动态下载依赖或扩大网络/文件权限。
- `INV-031 bounded-multi-agent-dag`：节点、深度、并发、replan、模型/工具调用、成本和 deadline 均为硬上限。
- `INV-032 model-tool-secret-containment`：Provider key、base URL、service credential 和 signing key 只存在于 server-owned registry。
- `INV-033 checkpoint-restore-new-attempt`：Checkpoint 只保存逻辑状态；恢复创建新 Run/Attempt/Lease/fencing/runtime/workload identity。
- `INV-034 agent-control-workload-separation`：Browser Agent API 与 workload Gateway 独立；Browser JWT/cookie 不能调用 Planner/Executor 工具路由。

## 4. P5.1 Agent Registry 与安装治理

交付内容：

- AgentDefinition/Version/WorkspaceAgentBinding schema、ORM、migration、service 和只读 API。
- AgentVersion manifest 使用 closed-set schema：模型族策略、最大上下文、允许工具 ID、输入/输出 schema、风险等级、Memory policy、最大并发与默认预算。
- manifest 不包含 API key、环境变量值、物理 locator、宿主路径或任意启动命令。
- 安装、升级、禁用和回滚由 Browser control plane 发起；高风险 Agent 安装需要 Approval，并精确绑定 version digest 和 Workspace generation。
- 版本升级创建新 binding/version，不原地改变正在运行的 AgentRun。

Gate：跨 Tenant/Workspace 安装拒绝；同 key 不同 digest 冲突；禁用后新 Run 立即拒绝；旧 Run 不因版本漂移获得新工具；OpenAPI/SDK 无敏感字段。

## 5. P5.2 Task、Run、Lease 与执行账本

> **P5.2A 实施状态（2026-08-04）**：P5.2 的**离线合同预检**已实现并验证
> （`backend/src/omnibase/production/phase5_task_ledger_contract.py` +
> `deployment/production/phase5-task-ledger-contract.example.json` +
> `scripts/production/validate_p5_2a_task_ledger_contract.py` +
> `backend/tests/test_p5_2a_task_ledger_contract.py` +
> `docs/phase-5-task-ledger-contract.md`）。P5.2A 只冻结合同（身份层级、
> 状态机、Task Lease/fencing 复用规则、预算 12 维、8 个 hash profile、
> identity stages、checkpoint 限制），**不实现**任何 P5.2 ORM、migration
> `0011`、Agent Invocation 路由、Runtime/Planner/Executor/scheduler/
> worker、模型/工具调用，也不创建 Task Lease 或真实 Task/Run/Attempt。
> 完整 P5.2 尚未标记完成：P5.2B persistence ledger（ORM + migration +
> 事务服务 + guarded disposable PostgreSQL Gate）未实现，必须在主 Agent
> 独立复核 P5.2A 通过后才允许规划。P5.2A `--verify` 当前恒为
> `blocked/not_proven`（exit 2）；三个 Feature Gate 保持 false；
> `gate true` 或 `activation_requested=true` 是 veto。

- 创建 AgentTask 时冻结 request hash、Workspace generation、AgentVersion、模型策略、Resource scope、预算和 deadline。
- 启动 AgentRun 必须复用 P34.4 Run/Node/Lease/runtime/workload identity，不创建第二套独立身份系统。
- Run lease 与每个 Step lease 分离；Step claim 绑定当前 Run lease、Node fencing、Workspace generation 和 Attempt number。
- Operation、Idempotency、Audit 和 token/cost/tool budget 在任何模型或工具 provider 调用前预留。
- cancellation、deadline、Workspace pause、membership revoke、Agent disable、Grant revoke 或 fencing drift 必须阻止新 Step/ToolCall；已经跨 provider boundary 的调用进入 reconciliation，而不是假装取消成功。

Gate：双 worker claim 单赢家；stale holder 无法 heartbeat/finish；terminal Run 不可复活；预算并发不超额；cancel/revoke 延迟达到 P34.7 SLA。

## 6. P5.3 Planner：compile-only 计划生成

Planner 在本阶段只能调用只读 metadata/memory capability，不拥有写入、网络或 Sandbox execution 权限。输出必须是 server-validated DAG：

- 节点 ID、依赖、AgentVersion、输入引用、输出 schema、工具 allowlist、Resource scope、风险、预算、超时和 retry policy 均为闭集字段。
- 服务端验证无环、节点/边上限、总预算、最大并发、最大深度、工具兼容性、数据流 scope 和 Approval 要求。
- Planner 文本不能直接成为 shell、SQL、URL、文件路径或 provider 参数；必须先通过 typed tool schema。
- 计划改变时生成新 AgentPlan version 与 digest；已批准计划不能被静默改写。
- 高风险步骤在 DAG 编译后、执行前进入 Approval；审批精确绑定 plan/step/request/resource/version/tool schema digest。

Gate：循环 DAG、预算溢出、隐藏工具、scope escalation、审批漂移、同 ID 不同 hash、提示注入产生的额外节点全部拒绝。

初始 server-owned 上限建议：32 个节点、深度 8、fan-out 8、并发节点 4、replan 2 次、单节点 Attempt 2 次；总 wall-clock、token、cost、工具调用和 Artifact bytes 必须显式有界。调用方和模型只能请求更小值，不能扩大这些策略。

## 7. P5.4 Executor 与 Capability Tool Gateway

- Executor 只消费已验证 AgentPlan，不重新解释自然语言来扩大 DAG。
- 每个工具实现注册 server-owned `ToolDefinition/ToolVersion`：typed input/output schema、风险、超时、最大结果、Capability action、adapter 和 recovery policy。
- Agent 只能看到逻辑工具和 Resource ID；物理存储、网络地址、credentials 和 provider receipt 留在 Core/adapter 内。
- 工具调用顺序固定为：live attestation → lock/binding revalidation → Operation/Idempotency/budget/effect pending → provider call → fresh transaction revalidation → terminal state/Audit。
- 只有明确证明 provider boundary 尚未跨越的失败可以按策略重试；`unknown` 不自动 retry。
- 结果在进入模型前执行大小限制、MIME/schema 验证、内容安全分类、secret/locator redaction 和 prompt-injection 标记。

首批只允许低风险内建工具：Workspace Resource read/list、RAG search/citation、受控 Artifact read/write、受控 Sandbox job submit/status/cancel。任意 SQL、任意 HTTP、宿主文件、Docker、进程和 unrestricted shell 均不进入首批工具。

### 7.1 Capability profile 与 ToolDefinition

Agent 不获得一个“万能 Agent Token”。每个 Task Attempt 使用相互独立的最小短期 Grant：

- Sandbox lifecycle grant：继续 token-free，并由现有 Sandbox verifier 在线复核。
- Gateway read grant。
- Workspace-data grant。
- Memory view/search grant。
- Memory candidate write grant。
- Model invoke grant。
- Skill manifest/invoke grant。
- Task result/checkpoint submit grant。

Planner、Executor、Aggregator 和 Reviewer 必须使用不同 capability 模板；Planner 的 read-only profile 不能被 Executor 继承，Executor 的 write/tool profile 也不能被 Planner 或 Aggregator 使用。各 profile 即使同时属于一个 Task，也应由独立 Grant 与独立短期 credential 表达，不合并为 wildcard bearer token。

每个 `ToolVersion` 至少冻结：

- logical tool ID、exact version 和 digest；
- input/output JSON Schema；
- required action 和 Resource scope；
- effect class：`pure_read|idempotent_write|external_effect|sandbox_exec|human_action`；
- timeout、最大 input/output bytes、最大 calls 和预算维度；
- retry、Approval、Reconciliation、Artifact 与 redaction policy。

工具参数不得包含 SQL、physical schema/table/column、bucket/object key、presigned URL、host path、provider handle、API key、arbitrary base URL 或 raw credential reference。

建议新增逻辑 actions：`agent.plan.submit`、`agent.task.result.submit`、`agent.checkpoint.write`、`agent.event.append`、`memory.view.read`、`memory.search`、`memory.candidate.create`、`memory.feedback.create`、`model.invoke`、`model.stream.read`、`skill.manifest.read` 和 `skill.invoke`。Promotion、Approval decision、Agent/Skill publish/revoke、长期 Memory publish/delete 和 canonical mutation 不进入 workload token。

### 7.2 独立 Model Gateway

- Agent 只提交逻辑 `model_profile_id`；Provider、base URL、credential、允许模型、fallback policy 和费用策略由 server-owned registry 决定。
- 默认禁止 silent fallback；requested/actual model identity、provider policy digest、input/output/reasoning token、费用、finish reason 和 latency 进入结构化证据。
- Provider key、Authorization header 和真实 base URL 不进入 Sandbox、Prompt、Artifact、Audit、日志或公共 SDK。
- 模型调用同样先预留 token/cost/call/deadline budget；timeout、断线或 identity drift 不伪装成成功。
- Prompt、response 和 reasoning 正文不写 Audit；需要保留的正文保存为 scoped Artifact，Audit 只保存 digest、大小、逻辑 model identity、usage 和 code-only reason。
- 模型输出只能生成 Proposal 或 typed result，不能签发 Grant、通过 Approval、改变 budget 或直接调用 provider adapter。

## 8. P5.5 Memory Compiler 与长期用户智能库

长期记忆采用数据库治理 + RAG 的分层模型：

1. `user_private`：用户偏好、代码风格、审美、语言习惯；只对该用户和明确授权的 Agent 可见。
2. `workspace_private`：项目约定、架构、历史决策和任务上下文；受 Workspace membership/scope 控制。
3. `agent_private`：特定 Agent 的工作方法和局部 checkpoint；不能自动提升到用户或 Workspace 共享层。
4. `controlled_shared`：经显式审核、去敏和版本化后可共享的知识；不等于 canonical。

Memory Compiler 为每次 Task 生成有界 ContextCapsule：

- 先按 tenant/user/workspace/agent/task/action 过滤，再进行 RAG 检索和 rerank。
- Capsule 设置来源列表、Resource/version、过期时间、token 上限、敏感级别和内容 digest。
- 平台系统提示、用户偏好、Workspace 约定、当前任务和检索证据分别设预算，超过预算时按策略压缩，不能无限注入历史。
- 默认只注入小型 capsule；Agent 通过 `memory.search` 按需继续读取。
- 原始聊天、模型推断和工具输出不自动成为长期事实。MemoryCandidate 必须通过重复合并、矛盾检测、PII/secret 过滤、来源证明和用户删除/纠正机制。
- 删除/撤销必须使新 Capsule 不再包含目标记忆；旧 Capsule 到期且不可续签。

Prompt 层级固定为：

```text
Platform Security Kernel
  -> AgentVersion instructions
  -> approved first-party Skill instructions
  -> ContextCapsule（明确标记为不可信数据）
  -> current task input
  -> Tool protocol
```

Memory、RAG、Artifact、Skill 示例和用户内容都不能覆盖 Security Kernel。从检索结果中发现的“系统指令”“忽略规则”“调用隐藏工具”等文本必须按不可信内容处理。ContextCapsule 必须保存 selected Memory IDs/versions、selection reason、sensitivity summary、token count、compiler policy digest、expiry 和 evidence references。

两阶段预算至少包含 `memory_initial_budget_tokens`、`memory_retrieval_budget_tokens`、`max_memory_calls`、`max_memory_result_tokens`、`max_memory_items`、`max_sensitive_items` 和 `memory_deadline_ms`。超预算必须拒绝或明确截断，禁止静默完整注入。

Memory Candidate 生命周期建议为：

```text
candidate -> deduplicating -> conflict_review
          -> awaiting_confirmation | auto_eligible
          -> active -> superseded | expired | paused | deleted
```

- Agent 只能创建 Candidate，不能直接发布永久用户画像。
- 高敏感记忆必须用户确认；禁止自动推断心理、健康、政治、宗教等敏感属性。
- 每条记忆必须绑定 source Invocation/Task/Resource/Artifact、证据、置信度、scope、sensitivity、retention 和 conflict group。
- 删除同步处理结构化记录、独立向量 lane、分层摘要和缓存，仅保留 code-only tombstone/Audit。
- 建议在 tenant schema 使用独立 `user_memory_chunks_v1`、`workspace_memory_chunks_v1` 与 `memory_index_state`；不得复用或写入 canonical `documents/embeddings/embeddings_v2`、canonical index metadata 或 P34.6 `workspace_derived_chunks_v2`。

Gate：跨用户/Workspace 记忆泄漏、删除后复现、恶意文档提示注入、来源/version drift、token budget 绕过和错误长期固化全部覆盖。

## 9. P5.6 原生 Skills 基础

Phase 5 只实现项目原生、受信、版本化 Skill 契约和少量内建 Skill；第三方 marketplace 与任意 MCP 安装后置到 Phase 6。

- `SkillDefinition/SkillVersion` manifest 声明用途、输入输出 schema、依赖工具、风险、预算、支持的 AgentVersion、验证命令和恢复方式。
- Skill 是计划模板/提示模板/typed tool choreography，不包含凭据、任意代码下载或绕过 Gateway 的执行路径。
- SkillVersion 内容不可变、按 digest 安装；升级生成新版本，运行中的 AgentRun 固定旧版本。
- 内建首批建议：`repository-inspector`、`maintainer-map-navigator`、`safe-test-runner`、`workspace-librarian`、`memory-curator`、`snapshot-auditor`。
- `safe-test-runner` 仍只能提交经过 P34.5 Sandbox contract 的 job；不能拿 Docker socket 或宿主 shell。

Skill runtime 限定为三类：

- `instruction`：只增加经批准的有限 instruction，不产生新 capability。
- `workflow`：展开为 PlanProposal fragment，仍必须重新经过完整 DAG Validator。
- `script`：只能在 Sandbox 中作为独立 Task Attempt 执行，禁止在 Core 进程运行 Python/Node/shell。

Skill lifecycle 固定为 `draft -> tested -> approved -> published -> deprecated|revoked`。每个版本必须通过 Manifest closed-set、strict schema、dependency lock、source digest、SBOM/signature、secret scan、symlink/junction/realpath escape、capability/memory/network policy diff、有 Skill/无 Skill paired eval、安全负例、人工 review 和回滚演练。Skill 更新不会替换正在运行 Invocation 的 pinned version；revoke 使新 Task 无法使用，但历史证据继续保留。

Gate：Skill 声明外工具、版本漂移、循环调用、隐藏网络、预算扩大、输入 schema 注入和卸载后继续运行全部拒绝。

## 10. P5.7 Specialist 与多 Agent DAG

首批 Specialist 采用窄职责模板：

- Librarian：只读检索、引用和知识组织。
- Curator：生成 MemoryCandidate，不直接写长期记忆。
- Archivist：Snapshot/lineage 审计，不执行 production restore。
- Engineer：在受控 Sandbox 中提出补丁和测试，不拥有发布、数据库或宿主权限。
- Reviewer：只读审查证据，不修改执行结果。

多 Agent 规则：

- Orchestrator 只能实例化计划中声明、且已安装 exact version/digest 的 Specialist，不得动态下载新 Agent/Skill；Planner 只能在 Proposal 中引用 Specialist，不能直接 spawn 未验证进程。
- 子任务使用新 Step/Attempt/Operation/Grant；禁止共享 bearer token、workload certificate 或可变内存对象。
- 输出通过 typed artifact/message channel 传递，携带 producer AgentVersion、Task/Step、digest、schema 和 sensitivity。
- 聚合器不能把低信任输出直接升级为高信任事实；关键结果需要独立验证或人工审批。
- 最大 fan-out、深度、循环次数、总 token/cost、并发 Sandbox 和 wall-clock 均为 server-owned 上限。
- 代码任务不得共享可写 runtime filesystem；应使用独立 branch/worktree 或 content-addressed patch Artifact。合并是独立受控 Task，冲突不能自动 last-write-wins。
- 动态修改必须创建 immutable Plan Amendment；已 committed 节点可被引用但不可改写，新增风险、成本、网络或 write scope 时重新审批，旧 PlanVersion 由 Invocation generation/plan version fence。

Gate：递归爆炸、Agent 自我复制、相互授权、confused deputy、跨 Workspace 消息、伪造工具证据、预算转移和死锁恢复。

## 11. P5.8 Checkpoint、恢复与 reconciliation

- Checkpoint 只引用已提交的逻辑 Resource/result；`pending|unknown` ToolCall 不能被标记为完成。
- 恢复创建新的 AgentRun/Attempt identity、Lease、runtime/workload identity 和 Grants；旧 PID、socket、process、connection、token、certificate 和 provider handle 永不恢复。
- DAG cursor 根据 committed Step result 重建；失败/unknown Step 进入人工 reconciliation 或创建全新 Operation，不能重置旧幂等键。
- 模型输出本身不是恢复权威；Operation、Effect、Audit、Resource/version 和 provider reconciliation 才是权威证据。
- Snapshot restore 后的 Workspace 必须先通过 P34.7 provider/subtype 验证，才能创建新的 AgentRun。

Checkpoint 只允许记录 committed PlanVersion、committed dependency frontier、Task result Artifact IDs/digests、未决 Approval/unknown reconciliation、剩余预算、exact Agent/Skill versions 和 code-only summary。它不得保存 active token、Lease、runtime/workload identity、PID/socket、provider connection、进程内存、raw credential 或 host path。

Resume 必须重新验证 Workspace/Agent installation，创建新 Invocation generation 或 Task Attempt、新 Workspace Run/runtime identity、新 Run/Task Lease、更高 fencing、新短期 Grants 和新 ContextCapsule；只能复用 committed outputs，遇到 `unknown` 时继续 blocked。

Cancel 必须提高 Invocation generation、撤销 Task Lease/Grants/workload certificate/registry binding、取消未 dispatch Task，并经独立 `SandboxControlAuthorizer` stop/destroy 活跃 runtime。无法确认终止或 provider outcome 的任务进入 reconciliation-required，不能标记为成功或安全重试。

P34.6/P34.7 Snapshot restore 后，新 Workspace 不自动恢复旧 Invocation。AgentDefinition/SkillVersion 可以作为安全 metadata reference 重新安装，但用户必须显式创建新 Invocation，Memory View 重新授权，全部 Run/Lease/token/runtime/workload identity 均重新生成。

Gate：进程崩溃、Core 重启、Runner 丢失、网络分区、provider timeout、Audit failure、checkpoint corruption 和 stale lease recovery。

## 12. P5.9 攻击矩阵、容量和发布 Gate

最终发布前至少完成：

- Prompt injection、tool injection、memory poisoning、malicious Skill manifest、模型格式不服从和伪造 `commands_run/files_read`。
- Tenant/Workspace/User/Agent/Task/Run/Step/Grant cross-wire。
- stale generation/fencing、revoked membership/Grant/certificate、expired ContextCapsule。
- 无限 DAG、递归 fan-out、token/cost/bytes/Sandbox 并发耗尽、慢工具和大结果。
- provider commit 后断线、Audit/DB commit failure、unknown no-replay 和人工 reconciliation。
- Sandbox escape、Broker network bypass、metadata/loopback/private/public destination、Overlay peer impersonation。
- Memory 删除、矛盾、跨用户泄漏、过量注入和敏感内容日志泄漏。
- clean-checkout/source-built CI、disposable integration、目标 Linux attack Gate、production smoke、容量和 SLA。

统一测试矩阵：

| 类别 | 必测内容 | Critical veto |
|---|---|---|
| Contract | strict DTO、closed set、digest、version、schema | extra field、locator 或 secret 被接受 |
| Identity | Tenant/Workspace/User/Agent/Run/Task binding | 跨 scope 或身份隐式继承 |
| Lease/Fencing | expiry、revoke、Node re-fence、Task retry | stale holder 可提交 |
| Planner | cycle、无限循环、增权、自批、超预算 | Proposal 直接产生副作用 |
| Executor | task capability、runtime isolation、result binding | 使用他人 Grant/Lease |
| Tool/Model | request digest、identity、effect、timeout、budget | `unknown` replay、silent fallback、Key 泄漏 |
| Memory | scope、敏感、provenance、token、delete | 跨用户/Workspace 泄漏 |
| Skill | version、manifest、provenance、revoke、Sandbox | wildcard、路径逃逸或 Core 执行 |
| DAG | acyclic、bounds、parallelism、cancel、amendment | 无限 Agent 或旧 Plan 提交 |
| Audit | append-only、redaction、success atomicity | Audit 失败但业务成功 |
| Recovery | crash points、checkpoint、resume、reconcile | 旧 identity/Lease 复活 |
| Sandbox/Network | host/fs/process/network/credential attacks | 直连基础设施、Overlay 或逃逸 |
| API/SDK/UI | Browser/Gateway 分离、strict parser、evidence | credential 混用或 UI 绕过后端 |
| Migration/Capacity | fresh/downgrade/re-upgrade、预算/SLA | 普通业务库 destructive test 或超预算继续 |

必须固定为 Critical Veto 的事件包括：跨 Tenant/Workspace/User Memory 泄漏；Planner/Skill 扩大 capability；Browser JWT 被 workload route 接受；Sandbox/Runner 直连基础设施；stale holder 成功提交；`pending|unknown` 自动 replay；physical locator/Provider Key 泄漏；requester self-approval；无界 Agent 循环；terminal 状态复活；Skill script 在 Core 执行；Memory/Skill/RAG 覆盖安全内核；silent model fallback；Audit failure 被降级为成功；以及 Agent 声称执行了 Tool/Command、但 durable evidence 不存在。

发布顺序固定：单 Agent + 只读工具 → 单 Agent + 受控 Artifact/Sandbox 工具 → MemoryCandidate/ContextCapsule → 静态 Specialist DAG → 有界动态 DAG。每一级独立 feature flag、独立回滚和独立证据，禁止一次性打开全部能力。

## 13. API 与 UI 解耦

- Browser API 负责 Agent 定义、安装、任务发起、Approval、暂停/取消和证据查看；不承载 workload ToolCall。
- Agent Runtime 使用独立 mTLS service 与 Capability Gateway，不能接受 Browser cookie/JWT 代替 workload identity。
- OpenAPI/SDK 只暴露逻辑 Agent/Task/Plan/Step/Resource/Operation ID、状态、预算和安全 reason code。
- UI 必须展示计划、工具、Resource scope、预算、Approval、实时状态、unknown/reconciliation 和 Audit link；不能把“模型正在思考”冒充真实执行进度。
- API contract 先于 UI；Python/TypeScript SDK snapshot、locator/credential leakage scan 和 backward compatibility Gate 同步更新。
- SDK 必须拆为 Browser/Admin Control Client 与 Workload Capability Client；前者只持用户会话并访问 `/api/v1`，后者只持短期 workload credential 并访问 `/gateway/v1`。不得创建同时持有 Browser JWT 和 workload token 的万能客户端。

建议 Browser Control API 按逻辑资源组织：

```text
/api/v1/agent-definitions
/api/v1/agent-definitions/{id}/versions
/api/v1/workspaces/{workspace_id}/agent-installations
/api/v1/workspaces/{workspace_id}/agent-invocations
/api/v1/agent-invocations/{id}/plan
/api/v1/agent-invocations/{id}/tasks
/api/v1/agent-invocations/{id}/cancel
/api/v1/agent-invocations/{id}/approvals
/api/v1/agent-invocations/{id}/reconciliation
/api/v1/memory
/api/v1/memory/candidates
/api/v1/memory/export
/api/v1/skills
/api/v1/workspaces/{workspace_id}/skill-installations
```

建议 workload Gateway 保持独立 mTLS 入口：

```text
/gateway/v1/agent/plan/submit
/gateway/v1/agent/task/result
/gateway/v1/agent/checkpoints/write
/gateway/v1/memory/view/read
/gateway/v1/memory/search
/gateway/v1/memory/candidates/create
/gateway/v1/skills/manifest/read
/gateway/v1/skills/invoke
/gateway/v1/models/invoke
```

UI 至少包含 Agent Catalog、Version/Capability diff、Workspace installations、New Invocation、Plan DAG、Task Attempt/Lease/fencing、Budget/Token/Cost、Approvals、Unknown Reconciliation、Artifact/Evidence、Memory Center、Skill installation 和 Audit Timeline。UI 必须明确区分 proposed/validated/approved/running、committed/failed/unknown、exact replay/new attempt，以及 model claimed action 与 verified tool evidence；不得把 fake/disposable 结果显示为 production evidence。

## 14. 分批完成定义

| 批次 | 完成定义 |
|---|---|
| P5.0 | P34.7 证据与 Phase 5 feature Gate 验证完成；Runtime 仍关闭 |
| P5.1 | Agent Registry/Version/Workspace binding 与安装治理通过 migration、RBAC、并发和 SDK Gate |
| P5.2 | Task/Run/Step/Attempt ledger、Lease/fencing、预算和 cancellation 闭环通过 disposable PostgreSQL Gate |
| P5.3 | compile-only Planner 输出受验证、不可静默改写的 DAG；无执行能力 |
| P5.4 | 单 Agent Executor 仅通过 typed Capability Tool Gateway 执行，unknown no-replay |
| P5.5 | ContextCapsule 与 MemoryCandidate 生命周期、隐私和 token budget Gate 通过 |
| P5.6 | 内建原生 Skill 契约、版本、安装和回滚 Gate 通过；第三方生态仍关闭 |
| P5.7 | Specialist 与多 Agent DAG 的 scope、预算、消息和 fan-out Gate 通过 |
| P5.8 | Checkpoint/new-identity recovery 与 reconciliation 演练通过 |
| P5.9 | 攻击矩阵、clean-checkout CI、目标 runtime、容量/SLA 和 production smoke 全部通过 |

### 14.1 Phase 5 全阶段完成定义

Phase 5 只有同时满足以下条件才算完成：

1. P34.7 有独立、与当前提交一致的 PASS 证据。
2. Agent 只能作为 Workspace 内受约束 workload 运行。
3. Planner 只提出 Proposal，确定性 Validator 才拥有接受权。
4. Executor 每个任务拥有独立 Lease、fencing、runtime identity 和最小 capability。
5. 多 Agent 只执行有界、无环、预算化 DAG。
6. Model、Tool、Skill、Memory、Data 和 Network 全部使用逻辑 API。
7. Agent 无数据库、对象存储、Redis、宿主、Overlay 或 Provider 凭据。
8. Approval、Operation、Idempotency、Budget、Audit 和 effect no-replay 完整。
9. ContextCapsule 按 scope 和 token budget 生成，不存在整库注入。
10. Agent 只能创建 MemoryCandidate，不能自行固化敏感画像。
11. 第一方 Skill exact-version、可撤销、可评估、不可增权；第三方生态继续关闭。
12. Checkpoint 恢复创建新 Run/Attempt/Lease/identity，`unknown` 有人工 reconciliation。
13. Browser Control API、Workload Gateway、Python/TypeScript SDK 和 credential 类型完全分离。
14. fresh sentinel migration、non-integration、Mypy、Ruff、OpenAPI、SDK、Frontend、maintainer-map、benchmark、Compose 和 destructive Gates 全部通过。
15. 生产 Runner/Broker/Gateway/Workspace-data/Model 联合链路通过攻击、故障、容量、恢复和 revoke Gate。
16. 所有 Critical Veto 为 0，文档、迁移、源码、OpenAPI、Compose 和 evidence digest 一致。

### 14.2 实施顺序与并行边界

严格主链：

```text
P34.7 PASS
  -> P5.0
  -> P5.1
  -> P5.2
  -> P5.3
  -> P5.4
  -> P5.5
  -> P5.6
  -> P5.7
  -> P5.8
  -> P5.9
```

允许 P5.1 数据模型与 P5.3 Proposal Schema 并行设计，但 Validator 通过前不能 dispatch；P5.5 Memory 与 P5.6 Skill schema 可在 P5.4 单 Agent Executor Gate 后并行；UI 只能在对应 API contract 冻结后实现。禁止 P34.7 未通过时装配 Planner/Executor、单 Agent 未通过时启动多 Agent、Memory scope 未通过时注入用户库、Skill provenance 未通过时执行脚本、Reconciliation 未完成时开放外部写工具，或把 fake/disposable evidence 描述成 production。

### 14.3 维护资料同步

实施时必须同步更新 `AGENTS.md`、`docs/maintainers/maintenance-map.json`、`docs/maintainers/security-invariants.md`、`docs/maintainers/ai-maintainer-map.md`、`docs/roadmap.md`、`docs/handover-report.md`、Phase 5 threat model、Agent recovery/reconciliation/emergency revoke runbook、Model credential containment runbook、Memory privacy/delete/export runbook、Skill review/revoke/rollback runbook、OpenAPI snapshots 和 CI Gates。

维护者地图建议新增 `agent-control-plane`、`agent-runtime-coordinator`、`agent-planner-validator`、`agent-executor`、`agent-model-gateway`、`agent-memory`、`native-skills`、`agent-ui-sdk` 与 `agent-operations-recovery` 模块。

任何阶段不得因模型能力更强或余额充足而降低安全、可维护性和恢复要求。
