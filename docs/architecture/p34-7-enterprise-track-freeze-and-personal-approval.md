# P34.7 企业安全轨道冻结与个人版审批演进

日期：2026-08-10

状态：

```text
ENTERPRISE_SECURITY_TRACK_PRESERVED_AND_FROZEN
PERSONAL_EDITION_APPROVAL_TRACK_ACTIVE
P34_7_ENGINEERING_ASSETS_PRESERVED
TRUST_POLICY_R0_CANDIDATE_ONLY_NOT_APPROVED
TRUST_POLICY_R1_A_IMPLEMENTED_NOT_AUTHENTICATED
PRODUCTION_RUNTIME_DISABLED
```

## 1. 决策摘要

OmniBase 当前首先交付的是个人版：一个经过认证的 Owner，拥有任意数量的
个人 AI 空间，并让这些空间共享受控的 Sandbox/Runner 基础设施。团队版和
企业版必须建立在个人版产品闭环稳定之后，而不是反过来让企业治理要求阻塞
个人版。

P34.7 已完成和正在进行的工作不是无效工作。本次决策不删除、不回退、不弱化
这些成果，而是把它们分为两条轨道：

1. **个人版活动轨道**：参考 Codex 和主流 AI IDE 已成熟的
   `sandbox mode + approval policy + network policy` 模型，由唯一 Owner
   对越界、联网和外部副作用进行审批；OmniBase 在此基础上增加 AI 空间、
   Capability、Workload Identity、Lease/fencing、预算、审计和 reconciliation。
2. **团队/企业冻结轨道**：保留 P34.7 joint evidence、Trust Policy R0、
   R1-A assignment、多人 authority separation、独立签名、真实生产环境证据、
   key ceremony、custody attestation、Overlay/DERP、容量与 SLA 等设计和代码，
   但在个人版完成前不继续扩展或执行。

冻结的含义是：

- 已有源码、测试、合同、runbook、evidence 和安全不变量全部保留；
- 不把尚未完成的企业工作伪装成已完成；
- 不批准 Trust Policy digest，不运行真实 key ceremony；
- 不因冻结而打开 Runtime、Planner 或 Multi-Agent；
- 不再为了个人版引入额外的多人审批、企业密钥托管和生产 SLA 前置条件；
- 个人版稳定后，从本文件记录的恢复点继续演进，而不是重新设计一遍。

## 2. 产品演进顺序

OmniBase 的产品顺序固定为：

```text
个人版
  ↓
多人/团队版
  ↓
企业版
  ↓
定制化与受监管部署
```

个人版不是企业版的删减演示，而是完整产品的原点：

- 一个 Owner；
- 多个隔离的 AI 空间；
- 共享的 Sandbox/Runner 资源池；
- 每个 Run 独立的身份、Capability、Lease、预算和审计记录；
- Owner 可以明确共享知识和资源，也可以保持空间隔离；
- AI 永远不能替 Owner 批准自己、扩大自己的权限或关闭审计。

团队版将在此基础上增加成员、角色、共享 Workspace、团队预算和协作审批；
企业版再增加多人职责分离、独立 custody、组织级 policy、合规证据、SLA 和
灾难恢复。

## 3. Codex 参考基线

本决策参考 OpenAI 官方文档中 Codex 的两层安全模型：

- **Sandbox mode** 决定 Agent 在技术上能够触达和修改什么；
- **Approval policy** 决定 Agent 在什么情况下必须停止并向用户请求批准；
- 网络默认关闭，需要显式启用，并可叠加 destination allowlist；
- workspace-write 可以在工作区内自动工作，越出工作区或访问网络时请求批准；
- destructive app/MCP side effect 仍需要批准；
- 用户是默认 approval reviewer。

参考：

- <https://learn.chatgpt.com/docs/agent-approvals-security>

OmniBase 复用的是这个产品交互模型，不复制 Codex 的内部实现，也不放弃自身
已经建立的安全边界。

## 4. OmniBase 在参考模型上增加的能力

### 4.1 AI 空间是第一层产品隔离单元

个人版可以拥有任意数量的 AI 空间，例如：

```text
代码开发空间
日常助理空间
研究空间
内容运营空间
邮件与消息空间
家庭服务器空间
个人知识空间
```

每个空间至少独立绑定：

- `tenant_id` 与 Owner；
- `workspace_id` / AI-space identity；
- 允许的逻辑资源；
- 允许的 Capability；
- 文件可写范围；
- 网络 destination allowlist；
- Provider/model allowlist；
- 预算和最大执行时间；
- 并发上限；
- 可持久化 Memory 范围；
- 外部副作用策略。

空间之间只有在 Owner 显式建立共享 Resource、Knowledge View 或 Capability 时
才共享内容。相同 Owner 不等于所有空间自动共享可写目录、临时凭据、Memory
或运行状态。

### 4.2 共享 Sandbox 基础设施，但不共享运行身份

多个 AI 空间可以共享同一个 Sandbox 服务、Runner 节点、镜像缓存、网络代理
和机器资源池，以降低个人设备的资源消耗。共享的是基础设施，不是一个无边界
的可变执行上下文。

每个 Run 继续独立获得：

- Workload Identity；
- 短期、不可委派的 Capability；
- Task Lease 和 Run Lease；
- generation 与 fencing token；
- 预算 reservation；
- 临时运行目录或隔离 mount；
- 网络授权；
- 审计与 reconciliation 关联。

不同空间不得无条件共用：

- 同一个可写工作目录；
- 同一份临时 Workload Credential；
- 同一个 Run/Task Lease；
- 同一个不可区分来源的长期 shell 进程；
- 未经 Owner 授权的 Memory、Artifact 或知识索引；
- 无法按空间和 Run 追踪的预算。

### 4.3 Capability 而不只是命令审批

OmniBase 的批准对象不仅是一条 shell command，还可以是一个稳定的逻辑能力：

```text
workspace.read
workspace.write
knowledge.search
browser.read
browser.submit
network.connect:<logical-service>
github.read
github.push
email.read
email.send
model.invoke
sandbox.execute
deployment.trigger
```

Capability 必须继续绑定 tenant、AI 空间、runtime、action、logical resource、
version、有效期、预算和可撤销状态。浏览器 cookie、原始 ID、模型输出或一段
“已经批准”的文字都不能替代服务端验证。

### 4.4 Lease、fencing、预算与 reconciliation

主流 AI IDE 的审批 UX 解决“用户是否允许”，OmniBase 还要解决长时间运行和
多 AI 空间中的并发正确性：

- stale holder 不能 heartbeat、finish 或覆盖新 holder；
- terminal Run 不能复活；
- Task Lease 与 Run Lease 必须独立 fenced；
- pending/unknown external effect 不能自动重放；
- 预算先 reservation，再按确定结果 commit/release；
- 超时、断线、取消和 ambiguous outcome 进入 reconciliation；
- Owner 可以通过 emergency control 撤销能力并终止 Run。

这些是 OmniBase 的核心差异化，不因采用成熟审批交互而删除。

## 5. 个人版审批模型

个人版只有一个最终人类 Authority：经过实时认证和角色复核的 Owner。

“独立验证”在个人版中指技术组件之间的分离，而不是要求多个自然人：

```text
Owner                         表达批准意图
Policy/Permission Validator   验证规则、scope、digest 和有效期
Capability Gateway            验证每次实际调用
Credential Service            签发短期 Workload Credential
OS secure storage             保管长期或安装级秘密
Audit Ledger                  记录 append-only receipt
Sandbox/Runner                执行受限 workload
```

同一个 Owner 可以批准、撤销和调整个人空间权限，但候选输入、浏览器 DTO、AI
输出和 Sandbox workload 都不能自报 `VERIFIED`、`PROVEN` 或
`activation_allowed=true`。

### 5.1 建议的四个权限 Profile

| Profile | 文件范围 | 网络 | 自动执行 | 典型用途 |
| --- | --- | --- | --- | --- |
| `observe` | 只读 | 关闭 | 只读检查 | 阅读、解释、计划、审查 |
| `workspace_auto` | 当前 AI 空间可写 | 默认关闭 | 空间内低风险操作 | 默认开发和日常工作 |
| `workspace_network_scoped` | 当前 AI 空间可写 | allowlist | 已批准域名和能力 | Provider、GitHub、网页、MCP |
| `owner_full_control` | Owner 明确指定 | Owner 明确指定 | 仍受硬性安全边界 | 长期自主任务和维护 |

`owner_full_control` 不是“关闭所有安全系统”。以下边界仍不可由 Agent 自己
绕过：

- 不读取未授权 secret；
- 不向日志、聊天或 evidence 输出 secret；
- 不修改或删除自己的 approval/audit receipt；
- 不扩大路径、网络、Capability 或预算范围；
- 不批准新节点或新 trust root；
- 不跨 AI 空间读取未共享数据；
- 不复活过期或被替换的 Lease；
- 不把 unknown、断流或 ambiguous outcome 写成 success；
- 不让 Sandbox/Runner 直连 PostgreSQL、Redis 或 MinIO；
- 不把物理数据库 locator 暴露给 workload。

### 5.2 Owner 的批准选项

产品 UI 可以统一使用：

```text
允许一次
本任务允许
此 AI 空间允许
始终允许（绑定精确 scope，可撤销）
拒绝
停止并撤销
```

批准记录至少绑定：

- authenticated Owner；
- AI 空间；
- action/Capability；
- logical resource；
- request/plan/tool schema digest；
- Provider 或 destination scope；
- 有效期；
- 预算上限；
- 是否允许外部副作用；
- receipt 和 revocation 状态。

### 5.3 风险分类

个人版可自动允许：

- 当前空间内的读取、搜索和普通文件修改；
- 运行已知测试、formatter 和只读诊断；
- 已授权的只读 knowledge search；
- 已在 allowlist 中、无外部副作用的 Capability；
- 已批准 Provider/model 范围内的调用。

个人版必须请求 Owner 批准：

- 写当前空间以外的文件；
- 新增网络域名、私网地址或 Unix socket；
- 发送邮件、消息、帖子或提交表单；
- push、PR、merge、deploy；
- 安装软件或提升权限；
- destructive Git/文件操作；
- 数据库 migration 或数据删除；
- 使用新的 Provider credential；
- 增加预算、并发或运行时长；
- 打开长期后台任务；
- 访问其他 AI 空间的数据；
- 调用标记为 side-effecting/destructive 的 MCP 或插件。

个人版始终拒绝普通 Agent 流程：

- AI 自己批准自己；
- 修改 approval/audit history；
- 导出长期私钥；
- 关闭审计或伪造 evidence；
- 把 disposable/mock 声明成 production evidence；
- 绕过 Capability、Lease、fencing 或预算；
- 未授权读取根 `.env`；
- 直接访问物理数据存储。

## 6. 已完成并保留的企业级资产

以下成果保留为长期资产，也继续保护个人版：

| 资产 | 当前成果 | 个人版复用 | 企业版恢复用途 |
| --- | --- | --- | --- |
| Workspace governance | membership、generation、Run/Network Lease、fencing、trusted node | 多 AI 空间隔离和并发正确性 | 多成员治理和节点控制 |
| Capability system | logical resource、短期 grant、预算、撤销、审计 | Owner 授权的能力边界 | 组织级 policy 和细粒度委派 |
| Linux Runner | namespace/seccomp/LSM/cgroup、bounded kill、mTLS identity | 本机/独立节点安全执行 | 独立生产 Runner 集群 |
| Network Broker | logical service、default deny、DNS/IP 分类、durable budget | 空间级网络 allowlist | 团队/企业 egress policy |
| Overlay adapter | provider-neutral membership、Node Daemon seam、revoke/rotate | 个人多设备扩展 | 多成员节点、DERP、失陷恢复 |
| Capability Gateway | 独立 ASGI、mTLS、live Lease/fencing、短期 credential | Agent 只通过逻辑能力访问数据 | 独立 Core/Runner/Broker/Gateway topology |
| Workspace data | private/derived、copy-on-publish、restore-new-identity | 个人知识与恢复 | 团队数据治理和发布 |
| P34.7 joint Gate | source/artifact/receipt/signature/freshness/certificate binding | 防止本机配置和 evidence 自我声明 | 企业生产 admission |
| Trust Policy R0 | candidate-only、secret scanner、role/scope、rotation/revocation | 保留为高级/企业 profile validator | 真实多角色 policy 和 custody |
| Trust Policy R1-A | authority/custody/resource/blocker assignment closed set | 不作为个人版硬前置 | 团队/企业 authority registry 的起点 |

这些资产不得因个人版采用轻量审批模型而删除或放宽。修复真实 P0/P1 安全漏洞、
保持测试可运行和适配公共接口仍然允许；新增企业治理功能则进入冻结范围。

## 7. 已完成但不构成生产批准的状态

冻结快照基于工作线：

```text
branch = codex/p34-7-trust-policy-r1
HEAD = 8261b3730836473b954e9b0004ca1fe09fdaeda1
main-derived base = eb0a173
```

已完成：

- P34.7 hardened joint Gate 已进入主线；
- Trust Policy Candidate R0 已进入主线；
- R0 attack matrix、raw-byte binding、lifecycle、rotation/revocation、secret
  scanning 已完成；
- R1-A authority/custody/15-resource/11-blocker assignment 合同已实现；
- R1-A 已修复自报 `VERIFIED/PROVEN`、production equivalence 和资源映射漂移；
- R1-A canonical example 保持 `UNASSIGNED / NOT_ASSESSED`；
- R1-A 最高能力状态仅为 `complete_not_authenticated`。

仍未完成且冻结：

- independently pinned authority registry；
- detached、replay-bound review receipt；
- 真实多人 policy review；
- 七角色真实 key ceremony；
- custody attestation、HSM/KMS/offline device 证明；
- audited approved-digest change；
- 15 项真实 target-environment assignment；
- 11 项生产 blocker evidence campaign；
- 两个独立 Linux Overlay member 和 DERP；
- compromise/rejoin、dual independent signatures；
- non-disposable tenant/RAG 与 data-owner admission；
- production capacity/fault/SLA 证明；
- 企业级 production activation。

正式安全姿态保持：

```text
_APPROVED_TRUST_POLICY_SHA256 = frozenset()
P34.7 production total Gate = blocked/not_proven
activation_allowed = false
AGENT_RUNTIME_ENABLED = false
AGENT_PLANNER_ENABLED = false
MULTI_AGENT_ENABLED = false
migration head = 0012
migration 0013 = absent
```

## 8. 冻结范围

个人版完成前，默认不继续以下工作：

- 扩展 R1-A 的多人 authority/custody 状态；
- 建立真实 authority registry 或多人签名服务；
- 执行 R1-B/R1-C key ceremony；
- 将真实 digest 写入 `_APPROVED_TRUST_POLICY_SHA256`；
- 采集企业 target-environment production evidence；
- 建立 HSM/KMS、多 custody owner 或企业密钥恢复；
- 完成双成员 Overlay/DERP/node-compromise Gate；
- 建立企业容量、故障注入和 SLA campaign；
- 为团队/企业角色增加 migration、API、UI 或 SDK；
- 因纯治理完备性继续开启新的 review-fix 轮次。

冻结期间仍允许：

- 修复会导致越权、跨空间泄漏、沙箱逃逸、秘密泄漏、错误成功、无法撤销、
  stale holder 覆盖新 holder 或永久占用资源的 P0/P1；
- 保持现有测试、依赖和公开接口兼容；
- 更新文档以避免把 engineering evidence 写成 production PASS；
- 复用成熟开源的 OS sandbox、namespace、seccomp、cgroup、mTLS、policy
  evaluator 和网络代理，而不是继续自造底层机制；
- 为个人版实现 Owner approval profile 和 UI；
- 执行受限、可回滚的个人版 Canary。

## 9. 企业轨道的恢复条件

只有满足以下条件后，才建议恢复企业轨道：

1. 个人版的 configure Provider → 创建空间 → 安装 Agent → Invoke → stream →
   cancel → ledger/reconciliation 闭环稳定；
2. 个人版连续运行、断网、重启、超时和撤销测试稳定；
3. Owner approval profile 和审计记录已经成为正式产品接口；
4. 多 AI 空间共享 Sandbox 的隔离和预算边界有持续 Gate；
5. 团队版已经出现明确成员、角色、共享资源和协作需求；
6. 至少有两个真实 authority 或组织管理角色，职责分离具有现实意义；
7. 有真实非 disposable 目标环境、节点、证书、数据 Owner 和运维窗口；
8. 产品确实需要企业 SLA、合规、HSM/KMS 或多方签名，而不是为了文档完整而
   假设这些需求。

恢复必须从新的主线状态重新审查，不得直接把冻结期间的旧 evidence 当作新
环境证明。

## 10. 恢复后的建议里程碑

### E0 — 冻结状态审计

- 对照当前 main 检查本文件列出的代码、测试和 evidence 是否仍然存在；
- 重新运行 R0/R1-A/joint Gate；
- 记录依赖、schema 和主线 drift；
- 不自动批准 digest。

### E1 — 团队身份与共享空间

- Workspace Owner/Maintainer/Member；
- 团队共享 Resource/Knowledge View；
- 成员离开、角色变更、审批撤销；
- 团队预算和审计查询。

### E2 — 团队审批 Profile

- Owner 单批、双批或风险级别审批；
- plan/step/tool/resource/version digest binding；
- 可撤销的团队级“始终允许”；
- 审批不能绕过 Capability Gateway。

### E3 — 企业 Authority 与 custody

- 恢复 R1-A；
- independently pinned authority registry；
- detached review receipts；
- HSM/KMS/offline custody attestations；
- key ceremony、rotation、revocation 和 incident authority。

### E4 — 企业 P34.7 生产证据

- 15 项 target resource；
- 11 项 blocker evidence；
- 两成员 Overlay/DERP/node-compromise；
- non-disposable tenant/RAG；
- provider recovery、capacity/fault/SLA；
- fresh、signed、current-source evidence bundle。

### E5 — 企业激活

- audited approved-digest change；
- P34.7 total Gate；
- 独立 production activation decision；
- canary、rollback、kill switch、SLA observation；
- Runtime、Planner、Multi-Agent 分阶段开放，不一次全开。

## 11. 兼容性原则

个人版和未来企业版不应形成两套互相冲突的授权系统。建议使用同一概念模型，
通过 profile 增强：

```text
personal_single_owner
team_multi_member
enterprise_separated_authority
```

共同保留：

- logical Capability；
- exact resource/version binding；
- time/budget bounds；
- revocation；
- append-only audit；
- Lease/fencing；
- Workload Identity；
- fail-closed unknown semantics。

差异只体现在谁可以批准、需要几方批准、密钥如何托管、哪些 evidence 必须存在，
而不是重新实现一套 Runtime 或 Sandbox。

企业 profile 只能在个人 profile 基础上增加约束，不能通过 profile 切换降低
已经生效的隔离、秘密、Lease、fencing、预算或审计边界。

## 12. 当前下一步

当前主线优先级为：

1. 完成 P5.4D Product Acceptance 的真实产品 P1 修复；
2. 定义个人版 `sandbox mode + approval policy + network policy` 产品合同；
3. 把 Owner approval 与现有 Capability Gateway、Lease、预算和 Audit 接通；
4. 在 Runtime/Planner/Multi-Agent 默认关闭的前提下运行个人版受限 Canary；
5. 个人版稳定后，再依据第 9 节恢复团队/企业轨道。

本文件本身不授权生产激活，不批准 Trust Policy digest，不执行 key ceremony，
不创建 migration `0013`，不访问业务数据库，也不改变任何 Feature Gate。
