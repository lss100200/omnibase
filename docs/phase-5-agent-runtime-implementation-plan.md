# Phase 5 Agent Runtime 与编排实施计划

> 状态：规划已冻结，运行时代码尚未解冻。
>
> 硬前置：P34.7 production total Gate 全部通过并有可复现证据前，只允许维护本计划、数据契约草案、威胁模型和离线验证器；不得启动自主 Planner、多 Agent 长循环、宿主级工具或连接 non-disposable tenant/RAG 的 Agent Runtime。

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

## 2. 解冻条件：P5.0 Admission Gate

P5.0 不运行 Agent，只验证 Phase 5 是否可以开始。下列条件必须同时成立：

- P34.5 Runner、Network Broker、Overlay、mTLS Gateway 的 clean-checkout/source-built Gate 可复现；目标 Linux Runner 的 non-root UID/GID、namespace、cgroup、seccomp、LSM、replay 和 kill proof 有最终证据。
- P34.6 Foundation/Contracts/Fail-closed primitives 已提交并通过 CI；Promotion/Restore 未完成的成功路径仍保持拒绝。
- P34.7 完成 production Core↔Runner/Broker/Gateway 联合装配、non-disposable 最小 tenant/RAG、真实成员 Overlay/DERP/node-compromise、provider-backed snapshot/restore rehearsal、reconciliation、容量/SLA 和 production smoke。
- Capability、Approval、Operation、Idempotency、Audit、quota、Run/Network Lease 与 recovery runbook 的文档和源码一致。
- 默认生产 composition 在缺少任一 attestation、adapter、key、registry、Grant 或 recovery component 时返回拒绝/不可用，而不是降级放行。
- 建立 `AGENT_RUNTIME_ENABLED=false`、`AGENT_PLANNER_ENABLED=false`、`MULTI_AGENT_ENABLED=false` 三个独立、默认关闭的 server-owned feature Gate；配置错误和未知值 fail-closed。

任一条件缺失时，Phase 5 只能停留在规划和离线契约阶段。

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

### 3.2 建议新增的维护者不变量

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

### 7.1 独立 Model Gateway

- Agent 只提交逻辑 `model_profile_id`；Provider、base URL、credential、允许模型、fallback policy 和费用策略由 server-owned registry 决定。
- 默认禁止 silent fallback；requested/actual model identity、provider policy digest、input/output/reasoning token、费用、finish reason 和 latency 进入结构化证据。
- Provider key、Authorization header 和真实 base URL 不进入 Sandbox、Prompt、Artifact、Audit、日志或公共 SDK。
- 模型调用同样先预留 token/cost/call/deadline budget；timeout、断线或 identity drift 不伪装成成功。
- Prompt、response 和 reasoning 正文不写 Audit；需要保留的正文保存为 scoped Artifact，Audit 只保存 digest、大小、逻辑 model identity、usage 和 code-only reason。
- 模型输出只能生成 Proposal 或 typed result，不能签发 Grant、通过 Approval、改变 budget 或直接调用 provider adapter。

建议新增逻辑 actions：`agent.plan.submit`、`agent.task.result.submit`、`agent.checkpoint.write`、`memory.view.read`、`memory.search`、`memory.candidate.create`、`model.invoke`、`skill.manifest.read` 和 `skill.invoke`。Promotion、Approval decision、Agent/Skill publish/revoke、长期 Memory publish/delete 和 canonical mutation 不进入 workload token。

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

Gate：跨用户/Workspace 记忆泄漏、删除后复现、恶意文档提示注入、来源/version drift、token budget 绕过和错误长期固化全部覆盖。

## 9. P5.6 原生 Skills 基础

Phase 5 只实现项目原生、受信、版本化 Skill 契约和少量内建 Skill；第三方 marketplace 与任意 MCP 安装后置到 Phase 6。

- `SkillDefinition/SkillVersion` manifest 声明用途、输入输出 schema、依赖工具、风险、预算、支持的 AgentVersion、验证命令和恢复方式。
- Skill 是计划模板/提示模板/typed tool choreography，不包含凭据、任意代码下载或绕过 Gateway 的执行路径。
- SkillVersion 内容不可变、按 digest 安装；升级生成新版本，运行中的 AgentRun 固定旧版本。
- 内建首批建议：`repository-inspector`、`maintainer-map-navigator`、`safe-test-runner`、`workspace-librarian`、`memory-curator`、`snapshot-auditor`。
- `safe-test-runner` 仍只能提交经过 P34.5 Sandbox contract 的 job；不能拿 Docker socket 或宿主 shell。

Gate：Skill 声明外工具、版本漂移、循环调用、隐藏网络、预算扩大、输入 schema 注入和卸载后继续运行全部拒绝。

## 10. P5.7 Specialist 与多 Agent DAG

首批 Specialist 采用窄职责模板：

- Librarian：只读检索、引用和知识组织。
- Curator：生成 MemoryCandidate，不直接写长期记忆。
- Archivist：Snapshot/lineage 审计，不执行 production restore。
- Engineer：在受控 Sandbox 中提出补丁和测试，不拥有发布、数据库或宿主权限。
- Reviewer：只读审查证据，不修改执行结果。

多 Agent 规则：

- Orchestrator 只能实例化计划中声明的 Specialist，不得动态下载新 Agent/Skill。
- 子任务使用新 Step/Attempt/Operation/Grant；禁止共享 bearer token、workload certificate 或可变内存对象。
- 输出通过 typed artifact/message channel 传递，携带 producer AgentVersion、Task/Step、digest、schema 和 sensitivity。
- 聚合器不能把低信任输出直接升级为高信任事实；关键结果需要独立验证或人工审批。
- 最大 fan-out、深度、循环次数、总 token/cost、并发 Sandbox 和 wall-clock 均为 server-owned 上限。

Gate：递归爆炸、Agent 自我复制、相互授权、confused deputy、跨 Workspace 消息、伪造工具证据、预算转移和死锁恢复。

## 11. P5.8 Checkpoint、恢复与 reconciliation

- Checkpoint 只引用已提交的逻辑 Resource/result；`pending|unknown` ToolCall 不能被标记为完成。
- 恢复创建新的 AgentRun/Attempt identity、Lease、runtime/workload identity 和 Grants；旧 PID、socket、process、connection、token、certificate 和 provider handle 永不恢复。
- DAG cursor 根据 committed Step result 重建；失败/unknown Step 进入人工 reconciliation 或创建全新 Operation，不能重置旧幂等键。
- 模型输出本身不是恢复权威；Operation、Effect、Audit、Resource/version 和 provider reconciliation 才是权威证据。
- Snapshot restore 后的 Workspace 必须先通过 P34.7 provider/subtype 验证，才能创建新的 AgentRun。

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

发布顺序固定：单 Agent + 只读工具 → 单 Agent + 受控 Artifact/Sandbox 工具 → MemoryCandidate/ContextCapsule → 静态 Specialist DAG → 有界动态 DAG。每一级独立 feature flag、独立回滚和独立证据，禁止一次性打开全部能力。

## 13. API 与 UI 解耦

- Browser API 负责 Agent 定义、安装、任务发起、Approval、暂停/取消和证据查看；不承载 workload ToolCall。
- Agent Runtime 使用独立 mTLS service 与 Capability Gateway，不能接受 Browser cookie/JWT 代替 workload identity。
- OpenAPI/SDK 只暴露逻辑 Agent/Task/Plan/Step/Resource/Operation ID、状态、预算和安全 reason code。
- UI 必须展示计划、工具、Resource scope、预算、Approval、实时状态、unknown/reconciliation 和 Audit link；不能把“模型正在思考”冒充真实执行进度。
- API contract 先于 UI；Python/TypeScript SDK snapshot、locator/credential leakage scan 和 backward compatibility Gate 同步更新。
- SDK 必须拆为 Browser/Admin Control Client 与 Workload Capability Client；前者只持用户会话并访问 `/api/v1`，后者只持短期 workload credential 并访问 `/gateway/v1`。不得创建同时持有 Browser JWT 和 workload token 的万能客户端。

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

Phase 5 只有在 P5.0–P5.9 的实际证据、维护者地图、runbook、OpenAPI/SDK、UI 和默认拒绝 composition 一致后才算完成。任何阶段不得因模型能力更强或余额充足而降低安全、可维护性和恢复要求。
