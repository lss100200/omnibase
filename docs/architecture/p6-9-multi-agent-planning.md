# P6.9 Personal Multi-Agent Team R0

```text
P6.9 Personal Multi-Agent Team R0
P6.9 个人 AI 员工团队核心
```

Status: **planned; A2 contract/schema/IPC complete**. This document is the
official P6.9 R0 product law. Coordinator live Provider waves and workbench
team UI are **not** implemented. Do not claim
`PERSONAL_MULTI_AGENT_IMPLEMENTED`.

P6.9 adds Owner-started personal AI team collaboration on the already-accepted
desktop single-parent Agent (P6.7/P6.8). It is not enterprise Planner/DAG, not
`MULTI_AGENT_ENABLED`, and not a background daemon of ten agents.

**Binding one-sentence direction:**

P6.9 不再要求用户亲自组队；用户只需要决定是否开启团队模式。团队模式开启后，父 Agent可以自由判断、调动、组织和协调九名专业员工，OmniBase 只负责让这种协作在可见预算、严格身份、单一 Workspace、可取消、可恢复、无权限越界的环境中稳定运行。

**Codex tightening (now-authoritative):** 父 Agent 输出必须是受限结构化 Proposal，不是直接调度权限。Parent remains the dispatch **center**. Host remains the only runtime that may **execute**. Collaboration goes through the **Personal Team Blackboard**. Parent decides accept / handle / merge / decline. Host validates identity, budget, deps, and concurrency.

**NOW (already true):**

```text
PERSONAL_MULTI_AGENT_PLANNED
P6_9_A2_CONTRACT_SCHEMA_IPC_COMPLETE
ENTERPRISE_MULTI_AGENT_DISABLED
```

`PERSONAL_MULTI_AGENT_PLANNED` is the **current** product-law flag, not a
claim reserved for after P6.9-D. Keep it until D. A2 replaces
`P6_9_NOT_STARTED` with `P6_9_A2_CONTRACT_SCHEMA_IPC_COMPLETE`.

**Only after P6.9-D engineering acceptance** may one consider:

```text
PERSONAL_MULTI_AGENT_IMPLEMENTED
ENTERPRISE_MULTI_AGENT_DISABLED
```

Do **not** claim `PERSONAL_MULTI_AGENT_IMPLEMENTED` in A2.

## Recording status (2026-08-20)

| Item | Value |
|---|---|
| Official name (EN) | P6.9 Personal Multi-Agent Team R0 |
| Official name (ZH) | P6.9 个人 AI 员工团队核心 |
| Status | planned; A2 contract/schema/IPC complete |
| P6.8 acceptance line | `cursor/p6-8-desktop-single-agent-hardening-r0` |
| P6.8 HEAD (verified, unchanged) | `d2a2db04c0fbfc1ee5d398e40710495c388c21b4` (`d2a2db0`) |
| P6.8 Codex pointer | `codex/p6-8-desktop-single-agent-hardening-r0` left at `2d3b56e` |
| Product-law source | `cursor/p6-9-multi-agent-planning-r0` (`01f9d3b`) |
| Execution branch | `cursor/p6-9-personal-multi-agent-team-r0` |
| Prior planning SHA | `95fa6d6` (Owner-loosened staffing; **superseded** by this Codex contract) |
| P6.9 Codex pointer | `codex/p6-9-personal-multi-agent-team-r0` left at `d2a2db0` (empty; not committed on) |
| Next execution | P6.9-B coordinator (live waves). Not A2. |
| Push / PR / EXE | not approved |

This file remains product law. P6.9-A2 schema, typed Proposal contracts, closed
IPC and host validators shipped on the execution branch. Coordinator live
waves and workbench team UI are not part of A2. P6.8 product worktree and
`d2a2db0` are not modified by A2.

---

# Honest history / 对 Codex 与 Cursor 的事后诚实说明

This direction changed **twice**. Codex must implement **this** contract, not
Cursor’s first draft, and not the incomplete Owner-loosened “raw dispatch”
wording in `95fa6d6`.

## 1. First Cursor draft (withdrawn)

Owner-declared 2–5 UI roster, serial-only, 3–6 calls, no A2A.

中文：Cursor 最初把 P6.9 R0 写成 Owner 在 UI 勾选闭集 2–5 名专业员工；父
Agent 不能加成员；禁止 A2A；禁止父 Agent 自选团队；默认固定串行；沿用 P6.4
的 3–6 参与者上限。

## 2. Owner change (necessary, but incomplete as runtime law)

Parent may staff and collaborate; host only runtime safety.

中文：Owner 随后改方向——不再要求用户亲自组队；打开团队模式后，父 Agent 可以
判断编制与协作。宿主只保证运行时安全。已撤回“闭集 Owner roster 是唯一合法
编制”。`95fa6d6` 记录了这一步，但把“父 Agent 可 dispatch”写得过宽，读起来像
父 Agent 直接获得调度权限。

## 3. Codex contract (now-authoritative P6.9 law)

Parent does **not** get raw dispatch. Parent emits a **restricted structured
Proposal**. Host validates identity / budget / deps / concurrency.
Collaboration via **Personal Team Blackboard**. Parent remains the dispatch
center. Replan between waves. Serial **and** parallel **and** mixed waves;
host may **serialize** a parallel wave but must not parallelize declared deps.
P6.4 participant cap of 3–6 is **withdrawn**. All 9 specialists may
participate; call count can exceed 10 but is budget-bound. Same employee may
be reinvoked only with **new** assignment / node / invocation / epochs; never
overwrite old reports.

中文：父 Agent 输出必须是受限结构化 Proposal，不是直接调度权限。员工协作必须
经团队黑板回到父 Agent 决策。宿主校验身份、预算、依赖和并发，不评价父 Agent
为什么选前端而不是产品经理。

Withdrawn or superseded flags / wording (do not re-litigate as current law):

```text
WITHDRAWN: P6_9_OWNER_DECLARED_ROSTER_ONLY
WITHDRAWN: closed 2–5 checkbox roster as the only legal team staffing
WITHDRAWN: “团队成员不能由模型自行决定”
WITHDRAWN: “Agent 自己选择团队” as a hard non-goal in team mode
WITHDRAWN: P6_9_SERIAL_3_TO_6_CALLS as a staffing or call-count cap
WITHDRAWN: serial-only as the only legal wave shape
WITHDRAWN: P6.4 1/3/4/5/6 participant contract as a P6.9 product cap
SUPERSEDED: P6_9_TEAM_MODE_PARENT_MAY_DISPATCH as raw dispatch
            → parent emits Proposal; host validates and executes
SUPERSEDED: “open serial-vs-concurrent Stop conflict, default serial”
            → serial and parallel and mixed waves are allowed;
              host may demote parallel→serial; multi-node Stop is P6.9-B/C work
```

`P6_9_SERIAL_3_TO_6_CALLS` remains withdrawn as a **staffing cap**. Call count
is now **budget-bound**, not frozen at 3–6.

---

# 核心关系

```text
唯一 Owner
   │
   ├─ 普通消息（未开团队、无 @）──→ 父 Agent          [模式 1：P6.7/P6.8 不变]
   │
   ├─ @一名员工 ──────────────────→ 单一专业 Agent    [模式 2：一次调用]
   │
   └─ 显式开启“团队协作” ─────────→ 父 Agent 输出受限 Proposal
                                      │
                                      ├─ Host 校验身份 / 预算 / 依赖 / 并发
                                      ├─ Host 按 wave 执行（可把并行降为串行）
                                      ├─ 员工报告写入 Personal Team Blackboard
                                      ├─ 协作请求回到父 Agent 决策
                                      └─ 父 Agent 在 wave 之间 replan
```

- Unique Owner.
- Three modes stay strictly separated.
- Team mode: Owner opens 团队协作 (task-level delegation approval). Parent
  **proposes** staffing and collaboration. Host **validates and executes**.
- Do **not** require a closed 2–5 checkbox roster as the only legal staffing.
- Do **not** give the parent a raw `dispatch(employee)` primitive.

审批者：唯一 Owner（打开团队模式 / 发送任务 / Stop / 追加预算）
团队编制：父 Agent 在团队模式内判断（Proposal），宿主执行
未开团队时的自主唤醒：禁止
广播 @所有人 / @all：禁止
后台守护进程：禁止
无限循环：禁止
员工绕过父 Agent 直接启动另一名员工：禁止

---

# P6.9 的产品边界

P6.9 固定沿用现有十个角色（1 父 Agent + 9 名专业员工）。角色目录由源码所有。
父 Agent 只能从这九名专业员工里调动，不能发明新角色类型，也不能动态下载角色。
不允许按角色配置独立 API Key / MCP / Shell / 文件工具。

| ID | 产品角色 | 默认状态 |
|---|---|---|
| `parent` | 父 Agent / 项目负责人 | 活动 |
| `product` | 产品经理 | 静默 |
| `ux` | UI/UX 设计师 | 静默 |
| `frontend` | 前端工程师 | 静默 |
| `backend` | 后端工程师 | 静默 |
| `data` | 数据工程师 | 静默 |
| `security` | 安全架构师 | 静默 |
| `qa` | 测试工程师 | 静默 |
| `operations` | 运维/发布工程师 | 静默 |
| `docs` | 文档工程师 | 静默 |

Parent is active. Nine specialists are dormant until the Owner `@` one of them,
or until team mode is on and the host executes a validated parent Proposal that
names them.

三种运行方式必须严格区分。

### 1. 父 Agent 模式

用户不输入 `@`，也不打开团队模式：

```text
用户 → 父 Agent → 最终回答
```

Parent mode = P6.7/P6.8 current behavior. No compatibility regression.
No unsolicited specialist wake while team mode is off.
P6.0 “员工不自启动” still applies to ordinary mode.

### 2. 单员工模式

用户输入：

```text
@安全架构师 检查这个设计
```

只调用安全架构师一次：

```text
用户 → 安全架构师 → 直接回答
```

Single `@` = one specialist call. The parent Agent must not secretly call
again. No hidden parent follow-up. No hidden extra cost. This path stays.

仍然遵守：

- 一次只能 `@` 一名员工；
- 多个 `@` 继续失败关闭；
- `@所有人`、`@all`、广播继续拒绝；
- 该路径上，被 `@` 的员工回答不能再触发另一名员工；
- 员工完成后恢复静默。

Mode 2 is **not** team mode. Parent Proposal / blackboard / replan happen
only in mode 3.

### 3. 团队协作模式（binding）

用户必须主动打开“团队协作”。打开之后，Owner 给出的是 **任务级委托授权**，
不是每次任务的手工 roster。

```text
Owner 开启团队协作 + 发出任务
        ↓
父 Agent 输出受限结构化 Proposal
  (answer_directly | delegate + waves)
        ↓
宿主校验：九人闭集、assignment 唯一、依赖存在且无环、
         并发容量、调用预算、输入长度、
         不跨 Workspace、无工具/副作用、无秘密/路径/未授权 locator
        ↓
宿主执行 wave（可将 parallel 安全降为 serial；不得非法并行化依赖）
        ↓
每个专业节点 = 一次独立 Provider invocation
        ↓
员工报告 + collaborationRequests 写入 Personal Team Blackboard
        ↓
父 Agent replan（continue | request_followup | finish | cannot_complete）
        ↓
最终父 Agent 汇总（delegate 时 finalSynthesisRequired: true）
```

- Team roster is **not** a closed 2–5 UI checkbox set.
- Optional Workspace-level allow-list (default: all nine) is a preference, not
  a per-task roster form.
- Parent picks from the nine specialists only. Unknown / invented / downloaded
  roles fail closed.
- Broadcast `@all` stays illegal in every mode.

---

# Binding contract: structured Proposal, not raw dispatch

父 Agent 输出必须是受限的结构化 Proposal，而不是直接获得调度权限。

## ParentTeamDecision

```ts
type ParentTeamDecision =
  | {
      decision: 'answer_directly'
      answer: string
      reason: string
    }
  | {
      decision: 'delegate'
      objective: string
      waves: readonly TeamWaveProposal[]
      finalSynthesisRequired: true
    }

interface TeamWaveProposal {
  waveId: string
  execution: 'serial' | 'parallel'
  assignments: readonly TeamAssignmentProposal[]
}

interface TeamAssignmentProposal {
  assignmentId: string
  employeeRoleId: SpecialistEmployeeId
  objective: string
  dependsOnAssignmentIds: readonly string[]
  expectedOutput: string
  contextRequirements: readonly string[]
}
```

`SpecialistEmployeeId` is the closed nine: `product` | `ux` | `frontend` |
`backend` | `data` | `security` | `qa` | `operations` | `docs`. Parent cannot
appear as `employeeRoleId`. Parent cannot invent a tenth specialist.

父 Agent 可以输出：

```json
{
  "decision": "delegate",
  "objective": "审查并完善桌面端多 Agent 设计",
  "waves": [
    {
      "waveId": "wave-1",
      "execution": "parallel",
      "assignments": [
        {
          "assignmentId": "frontend-review",
          "employeeRoleId": "frontend",
          "objective": "检查桌面端状态投影和交互入口",
          "dependsOnAssignmentIds": [],
          "expectedOutput": "前端实现风险和建议",
          "contextRequirements": []
        },
        {
          "assignmentId": "backend-review",
          "employeeRoleId": "backend",
          "objective": "检查 SQLite、IPC 和协调器数据模型",
          "dependsOnAssignmentIds": [],
          "expectedOutput": "后端实现风险和建议",
          "contextRequirements": []
        }
      ]
    },
    {
      "waveId": "wave-2",
      "execution": "serial",
      "assignments": [
        {
          "assignmentId": "security-review",
          "employeeRoleId": "security",
          "objective": "基于前后端报告检查取消、身份和权限边界",
          "dependsOnAssignmentIds": [
            "frontend-review",
            "backend-review"
          ],
          "expectedOutput": "安全审查结论",
          "contextRequirements": [
            "frontend-review",
            "backend-review"
          ]
        }
      ]
    }
  ],
  "finalSynthesisRequired": true
}
```

## Host validates (runtime law)

宿主只负责验证：

- 角色是否属于固定九人闭集；
- assignment ID 是否唯一；
- 依赖是否引用已存在节点；
- 不存在无法收敛的依赖（无环）；
- 并发是否在系统安全容量内；
- 总调用预算是否足够；
- 输入长度是否在预算内；
- 是否越过 Workspace；
- 是否要求工具或外部副作用；
- 是否包含秘密、物理路径或未授权 locator。

非法 Proposal 失败关闭。宿主不得“好心改写”成另一种团队拓扑后再执行。

## Host must not second-guess (product judgment)

宿主不应该评价：

- 父 Agent 为什么选择前端而不是产品经理；
- 为什么需要四个员工而不是三个；
- 为什么先并行后串行；
- 是否应该再让测试工程师复查。

这些属于父 Agent 的任务判断能力。Host does not micromanage collaboration
topology.

## Personal Team Blackboard

允许员工协作，但通过受控团队消息总线。新方向下，员工不再是完全孤立的一次性
调用。

建议引入：

```text
Personal Team Blackboard
个人团队任务黑板
```

每个员工可以读取：

- 用户原始目标；
- 自己的职责说明；
- 父 Agent 分配给自己的子任务；
- 父 Agent 明确提供的前序员工报告；
- 当前 Team Run 的结构化进展；
- 与自己任务有关的协作消息。

员工可以输出：

```ts
interface EmployeeTeamReport {
  assignmentId: string
  employeeRoleId: SpecialistEmployeeId
  status: 'completed' | 'needs_collaboration' | 'blocked'

  report: string

  collaborationRequests: readonly {
    targetRoleId: SpecialistEmployeeId
    question: string
    reason: string
  }[]
}
```

例如安全架构师可以说：

```json
{
  "assignmentId": "security-review",
  "employeeRoleId": "security",
  "status": "needs_collaboration",
  "report": "当前 IPC 身份边界基本成立，但取消恢复路径需要测试确认。",
  "collaborationRequests": [
    {
      "targetRoleId": "qa",
      "question": "请设计应用重启、Stop 与迟到事件的攻击矩阵。",
      "reason": "需要验证恢复语义。"
    }
  ]
}
```

这不应该直接让安全架构师绕过父 Agent 无限调用测试工程师。

正确过程是：

```text
安全员工提出协作请求
        ↓
宿主记录到团队黑板
        ↓
父 Agent 查看请求
        ↓
父 Agent 决定：
  - 接受并启动 QA
  - 自己处理
  - 合并给现有 QA 节点
  - 认为不需要
        ↓
宿主验证预算和身份
        ↓
执行
```

因此员工之间可以充分协作，但父 Agent 仍是团队调动中心。Employee cannot bypass
parent to launch another employee.

## Dynamic replan

父 Agent 不能只在开始时规划一次。它可以在每一批员工完成后进行重规划：

```text
Initial planning
   ↓
Wave 1
   ↓
Parent replan
   ↓
Wave 2
   ↓
Parent replan
   ↓
Additional employee / follow-up / cross-review
   ↓
Final synthesis
```

重规划合同：

```ts
type ParentReplanDecision =
  | {
      decision: 'continue'
      nextWave: TeamWaveProposal
    }
  | {
      decision: 'request_followup'
      assignments: readonly TeamAssignmentProposal[]
    }
  | {
      decision: 'finish'
      reason: string
    }
  | {
      decision: 'cannot_complete'
      reason: string
    }
```

父 Agent 可以多次调用同一个员工，但每次必须创建新的：

```text
assignment ID
node ID
invocation ID
node epoch
send epoch
```

不能覆盖之前的员工报告。

## Serial, parallel, and mixed waves

上一版“默认固定串行”需要修订。新的规则是：

- 父 Agent 可以提出串行执行；
- 父 Agent 可以提出并行执行；
- 父 Agent 可以提出混合 wave；
- 宿主根据本机容量、Provider 限速和用户预算决定实际最大并发；
- 宿主可以把父 Agent 提出的并行组安全地降级为串行执行；
- 降级并不改变依赖和最终结果语义；
- 宿主不能把父 Agent 声明有依赖的节点错误地并行化。

例如父 Agent 计划：

```text
前端 ─┐
      ├─ 并行 → 安全复核 → 父 Agent 汇总
后端 ─┘
```

系统可以实际执行：

```text
并发容量足够：
前端 + 后端并行

并发容量不足：
前端 → 后端
```

但不能执行：

```text
安全复核和前端/后端同时开始
```

因为安全节点依赖前两份报告。

P6.8 global Stop covers one live stream. Multi-node Stop (cancel every active
node in a parallel wave, do not start waiting nodes) is **required P6.9-B/C
work**. Do not claim P6.8 already does it. Do not leave parallel unauthorized.

## 不再固定 3–6 个参与者

P6.4 的 1/3/4/5/6 participant contract 是特定产品练习 Gate，不能继续拿来限制
P6.9。

P6.9 允许：

```text
父 Agent 自行回答：
1 个 Agent 身份

父 Agent + 1 名员工：
2 个 Agent 身份

父 Agent + 2–9 名员工：
3–10 个 Agent 身份
```

同一员工可以进行多轮调用，所以：

```text
角色数最多：10
Provider 调用数：可以大于 10
但调用数必须受本次 Team Run 的预算约束。
```

## 安全约束不是“限制协作能力”

为了保证运行稳定，仍需要以下平台约束。它们限制的是资源消耗、失控循环和权限
扩张，不限制父 Agent 选择谁、如何分工或如何讨论。

### 1. 身份闭集

只能调动既有九名专业员工。

P6.9 R0 不允许模型动态创建：

```text
“网络渗透专家”
“临时管理员”
“超级 Agent”
“无限研究员”
```

后续可以由用户在设置中创建自定义角色，但不能由 Agent 自己创建带权限的角色。

### 2. 调用预算

每个 Team Run 必须有：

```text
maximumProviderCalls
maximumWallTimeMs
maximumConcurrentCalls
maximumInputCharacters
maximumOutputCharacters
```

```ts
interface TeamRunBudget {
  maximumProviderCalls: number
  maximumWallTimeMs: number
  maximumConcurrentCalls: number
  maximumInputCharacters: number
  maximumOutputCharacters: number
}
```

父 Agent 可以在预算内自由调动员工。

预算耗尽后：

- 不伪造完成；
- 不自动扩大预算；
- 不偷偷继续调用；
- 而是向用户报告：团队已经使用完本次协作预算；当前已完成哪些工作；还有哪些
  员工建议继续调用；是否由用户批准追加预算。

### 3. 循环收敛

允许员工复查、追问和多轮协作，但每次必须消耗可见预算。

禁止：

- A 永久要求 B 检查；
- B 永久要求 A 补充；
- 父 Agent 无限重新规划；
- 未知结果自动重试；
- 后台无限研究。

这不是禁止 A/B 协作，而是保证任何循环都必须在用户可见预算内收敛。

### 4. Provider 并发稳定

逻辑上允许父 Agent 调动所有九名员工。

物理上同时发出的 Provider 请求数量要考虑：

- Provider rate limit；
- 用户网络；
- 本机内存；
- Electron 主进程事件压力；
- API 成本；
- 各中转站稳定性。

因此应把“团队规模”和“实时并发”分开：

```text
团队规模：最多九名专业员工全部参与
实时并发：由用户设置和运行时稳定性限制
```

即使并发上限为 2，九名员工仍可以完整参与，只是按 wave 执行。

### 5. 权限不继承

员工之间传递的是：

- 报告
- 问题
- 引用
- 摘要
- 依赖关系

不能传递：

- API Key
- Vault handle
- native control token
- 文件系统 handle
- Sandbox token
- MCP session
- Capability
- Shell process
- 数据库 locator

每个员工节点都要重新解析自身模型配置和当前授权。No capability inheritance.

## Runtime safety envelope（仍然成立）

P6.9 R0 只保证员工运行时安全与稳定。下列约束约束的是 **runtime**，不是把
dispatch/collaboration 冻死在 Owner 闭集 roster 上，也不是把父 Agent 冻成
不能判断编制。

- Next 继续 product-blind。
- Closed IPC only；no generic `ipc.invoke(methodName, arbitraryPayload)`.
- Never copy API keys into role config. Vault stays Electron main. Paid key
  only via Vault UI, never from chat history.
- Unique invocation per node. One Provider call must not impersonate multiple
  agents.
- Epoch isolation: workspace / conversation / teamRun / planRevision / wave /
  assignment / node / invocation / send epoch. Missing or drifted identity
  must not paint the current node.
- Fail-stop or bounded budget. No infinite loops. No auto-replay of
  cancelled/unknown. Restart: `running|starting|streaming` → `unknown`.
- No background daemon. No autonomous wake when the Owner did **not** open
  team mode.
- R0 still no-tool: no MCP / Skills / Shell / RAG / per-agent sandbox.
  `sharedExecutionSessionId = null`, `tools_enabled = false`.
- No enterprise `MULTI_AGENT_ENABLED` / Planner DAG / P34.7.
- No new EXE / Authenticode / P7 visual / OSelf.
- Parent output is untrusted text until host validates it as a Proposal.

---

# P6.9-A：动态委托合同与持久化

目标是先建立真正独立的多 Agent 身份、Proposal 合同和持久化结构，不先做复杂
UI。

This recording does **not** implement A2 schema, A3 tests, or A4 IPC.
Next execution starts with P6.9-A only. A must encode **parent Proposal + host
validation + blackboard**, not the withdrawn closed Owner roster, and not raw
parent dispatch.

主要交付：

- 新增 INV-085 `p69-personal-parent-directed-team-boundary`；
- 明确 P6.0 的“员工不自启动”仍适用于普通模式；
- 明确 P6.9 团队模式获得 Owner 的任务级委托授权；
- Desktop SQLite schema v3；
- role model config；
- team run / team assignment / team node / collaboration request；
- 团队预算；
- 父 Agent planning/replanning DTO；
- 关闭式 IPC/API；
- Vault 仍不进入 renderer。

## A1. 固定角色目录

角色定义继续由源码所有。不允许 Provider 或模型输出**新的角色类型**。父
Agent 在团队模式里可以从目录中选择已有员工，不能创造第十一个工种。

每个角色至少具有：

```ts
interface PersonalAgentRole {
  id: EmployeeId
  displayName: string
  responsibility: string
  systemRoleFragment: string
  defaultState: 'active' | 'dormant'
  mayJoinTeam: boolean
}
```

禁止：

- 自定义脚本角色（R0；后续用户创建设置另议，且不得由 Agent 创建带权限角色）；
- 动态网络下载角色；
- 模型自己生成**新角色类型**；
- 角色要求额外系统权限；
- 角色自带 API Key；
- 角色自带 MCP、Shell 或文件工具。

`mayJoinTeam` is catalog metadata plus the Owner Workspace allow-list.
It does not restore Owner-only per-task staffing.

## A2. Desktop SQLite schema v3

建议新增：

```text
desktop_0003_personal_agent_team
```

不改 Alembic `0016`，不创建 `0017`。这是桌面本地 SQLite migration。
**NOT** Alembic 0016/0017.

建议增加数据实体：

```text
workspace_agent_role_config
team_run
team_plan_revision
team_assignment
team_node
team_collaboration_request
```

These tables record **what parent proposed, what host validated, and what
actually ran**. They are not a closed Owner checkbox contract, and they are
not a license for unvalidated model JSON to mutate runtime.

### `workspace_agent_role_config`

保存每个 Workspace、每个角色的模型选择：

```text
owner_id
workspace_id
employee_role_id
provider_id nullable
model_name_override nullable
gear
thinking_depth
row_version
verification_state
verified_actual_model nullable
verification_digest nullable
created_at
updated_at
```

语义：

- 没有配置行：继承默认 Provider；
- `provider_id=null`：继承默认 Provider 的 URL 和 Key；
- `model_name_override=null`：继承 Provider 的默认模型；
- 指定 provider：使用该已保存 Provider 的 URL/Key；
- 指定模型名：使用同一个 Provider 的凭据，但请求该模型；
- 绝不复制 API Key；
- 绝不把 Key、ciphertext、nonce 或 DPAPI blob 放入角色配置表。

```text
默认：父 Agent + 九个专业 Agent 共用同一 URL/Key/模型
自定义：任何单一 Agent 可以选择不同 Provider 或不同模型
```

### `team_run`

记录一次 Owner 明确打开团队模式后启动的团队任务：

```text
id
owner_id
workspace_id
conversation_id
mode = single | team
state = preparing | running | cancelling |
        succeeded | failed | cancelled | unknown |
        budget_exhausted | cannot_complete
staffing_authority = parent_proposal
current_plan_revision_id nullable
dispatched_participant_count nullable
current_wave_id nullable
maximum_provider_calls
maximum_wall_time_ms
maximum_concurrent_calls
maximum_input_characters
maximum_output_characters
consumed_provider_calls
created_at
updated_at
```

Exact columns are A2 work. This recording forbids treating Owner checkbox
cardinality as the schema’s purpose, and forbids a column that means “parent
already dispatched without a validated Proposal”.

### `team_plan_revision`

每次父 Agent 的 initial Proposal 或 replan 落盘为一版计划：

```text
id
team_run_id
revision_ordinal
decision = answer_directly | delegate | continue |
           request_followup | finish | cannot_complete
proposal_json_sha256
validated = 0 | 1
validation_error_code nullable
created_at
```

未通过宿主校验的 Proposal 仍可审计，但不得变成可执行 assignment。

### `team_assignment`

```text
id
team_run_id
plan_revision_id
wave_id
assignment_id
employee_role_id
objective
depends_on_assignment_ids
expected_output
context_requirements
declared_execution = serial | parallel
effective_execution = serial | parallel
state
created_at
updated_at
```

`declared_execution` is what the parent proposed. `effective_execution` is
what the host ran (may be serial after a safe demotion). Dependency semantics
must match the Proposal.

### `team_node`

记录每个独立 Agent 调用：

```text
id
team_run_id
assignment_id
ordinal
employee_role_id
invocation_id nullable
state
provider_id
requested_model
actual_model nullable
input_tokens nullable
output_tokens nullable
total_tokens nullable
answer_sha256 nullable
error_code nullable
created_at
updated_at
```

每个 node 必须对应一个独立 invocation。禁止一个 Provider 调用伪装成多个 Agent。
禁止同角色第二次调用复用旧 invocation / 旧报告行。

### `team_collaboration_request`

```text
id
team_run_id
from_assignment_id
from_employee_role_id
target_role_id
question
reason
parent_decision = pending | accept_start | handle_self |
                  merge_existing | decline
resolved_assignment_id nullable
created_at
updated_at
```

员工请求不得直接变成对新员工的 invocation。必须经父 Agent 决策，再经宿主
预算/身份校验。

## A3. 角色模型测试

用户为角色设置不同模型时，应有“测试该员工模型”按钮。

验证绑定至少包括：

```text
Workspace
role ID
role config row_version
Provider ID
Provider secret fingerprint
Base URL
requested model
actual model
endpoint policy
verification digest
```

配置、Provider、Key、URL、模型或 Workspace 发生变化后，旧验证立即失效。

## A4. IPC/API

Next 继续 product-blind。所有产品操作继续走：

```text
Renderer
→ closed preload bridge
→ origin-checked Electron IPC
→ Electron main
→ /desktop/v1
→ SQLite backend
```

建议新增关闭式 IPC（`agents.roles.*`, `teamRuns.*`）：

```text
agents.roles.list
agents.roles.get
agents.roles.update
agents.roles.test

teamRuns.start
teamRuns.cancel
teamRuns.get
teamRuns.list
teamRuns.subscribe
teamRuns.appendBudget
```

`teamRuns.start` starts **team mode** (Owner opened 团队协作 + task). It must
**not** require a closed `specialistRoles: 2–5` payload as the only legal
start. Parent Proposal happens after start. Host validates before any
specialist invocation.

不允许通用：

```text
ipc.invoke(methodName, arbitraryPayload)
```

---

# P6.9-B：父 Agent 动态协调器

这是 P6.9 的运行核心。

建议新增一个独立协调器，而不是把多 Agent 状态继续塞入
`RuntimeManager.sendConversation()`：

```text
desktop/src/runtime/personal-team-coordinator.ts
```

`personal-team-coordinator.ts` is separate from `sendConversation`. 单 Agent
原有路径保持不动。

The coordinator **hosts** validated Proposals, waves, blackboard, and
specialist invocations under the safety envelope. It is **not** an
Owner-roster police, and it is **not** a raw-dispatch executor for untrusted
model JSON.

主要交付：

- 父 Agent 初次任务分析；
- `answer_directly` | `delegate` 决策；
- 动态 roster；
- 串行/并行 wave；
- 依赖验证；
- 父 Agent 重规划；
- 员工多轮调用；
- 员工协作请求；
- 团队黑板；
- 最终父 Agent 汇总；
- 每次 Provider 调用独立计费与身份；
- 无自动重试；
- unknown 不自动重放。

## B1. Owner 打开团队模式；parent proposes

启动时，UI 产生团队模式请求，而不是闭集编制法令：

```ts
interface PersonalTeamRunRequest {
  workspaceId: string
  conversationId: string
  task: string
  teamMode: true
  rosterEpoch: number
  budget: TeamRunBudget
  allowedSpecialistRoleIds: readonly SpecialistEmployeeId[]
}
```

`allowedSpecialistRoleIds` defaults to all nine. It is the Owner Workspace
allow-list, not a per-task checkbox roster.

校验（runtime，不是 2–5 编制法）：

- Owner 已显式打开团队协作；
- task 有明确长度上限；
- 不允许广播 / `@all`；
- 同一时间一个 workspace/conversation 上的 live team run 边界由 A/B 钉死
  （stability）；
- 随后父 Agent 的 Proposal 只能引用九名专业员工，且须落在 allow-list 内；
- 未知角色、重复 assignment ID、环状依赖、角色类型发明、工具/跨 Workspace/
  秘密/路径：失败关闭。

Withdrawn B1 law (do not implement):

```ts
// WITHDRAWN — was Cursor's first draft, not current Owner/Codex law
interface WithdrawnOwnerDeclaredRoster {
  specialistRoles: readonly SpecialistEmployeeId[]
  participantCount: 3 | 4 | 5 | 6
}
// WITHDRAWN checks: specialists must be 2–5; parent cannot appear then add;
// participantCount === specialists.length + 1 as the only legal shape.

// WITHDRAWN — Owner-loosened wording in 95fa6d6, too wide
interface WithdrawnRawParentDispatch {
  dispatch(employeeRoleId: SpecialistEmployeeId): void
}
```

## B2. 真实独立调用

Validated parent Proposal must still become **real independent invocations**:

```text
parent (team mode) emits ParentTeamDecision
    → host validates identity / budget / deps / concurrency
    → host creates durable plan revision + assignments + nodes
    → invoke Provider (serial, parallel, or mixed; may demote parallel)
    → verify node identity
    → store terminal report on the blackboard
    → parent may replan, request follow-up, finish, or cannot_complete
       within visible budget
```

每个节点必须具有不同的：

```text
assignment ID
node ID
invocation ID
send epoch / node epoch
provider usage receipt
terminal result
answer digest
```

## B3. 协作：黑板，不是直连 A2A

Collaboration is allowed in team mode. Direct employee-to-employee launch is
not.

R0 needs **safe host mediation** so identity, budgets, and Stop stay real:

- Host invokes each node. Model output is untrusted text.
- Parent and specialists read host-built blackboard views, not raw peer
  sockets.
- Specialists must not: request tool/MCP/shell rights; inject system prompts;
  invent new role types; copy Vault secrets; start a background daemon;
  loop forever; launch another employee.
- Specialists **may** emit `collaborationRequests`. Parent **decides**.
  Host **validates** then executes.

## B4. 失败策略

采用 fail-stop **或** bounded budget. No auto-retry. No infinite loops.

| 节点/计划结果 | 团队行为 |
|---|---|
| succeeded | 允许父 Agent replan / 进入下一 wave / 结束 |
| needs_collaboration | 写入黑板，等待父 Agent 决策，不直接启动目标员工 |
| failed | 停止或按已声明的 bounded policy 收敛；不得假装成功 |
| cancelled | 整个 team run cancelled |
| unknown | 整个 team run unknown，禁止自动重放 |
| budget_exhausted | 向用户报告，不伪造完成，不静默扩预算 |
| cannot_complete | 向用户报告原因与已完成工作 |
| model identity drift | 失败关闭 |
| epoch mismatch | 丢弃事件，不改变当前节点 |
| duplicate invocation ID | 失败关闭 |
| Token/字符预算超限 | 停止，不截断成“成功” |
| 非法 Proposal | 失败关闭，不执行 |
| 宿主将 parallel 降为 serial | 合法；不改变依赖语义 |

## B5. 重试

禁止自动重试。

用户可以显式选择重试失败员工或重新运行团队。两者都必须创建新的 invocation
ID。重新运行父 Agent 汇总也必须是新的 parent invocation，不能改写旧记录。
同角色第二次调用必须是新 assignment / node / invocation / epochs。

---

# P6.9-C：团队运行时与桌面 UI

P7 才做全面视觉打磨，所以 P6.9-C 只做实用、清晰的团队控制，不重做整个桌面端
外观。

主要交付：

- 团队模式开关；
- 预算设置；
- 父 Agent 当前计划展示；
- 动态员工状态；
- wave/依赖关系展示；
- 员工协作请求；
- 节点报告；
- Token/调用次数/耗时；
- 全局 Stop；
- Workspace/Conversation 切换恢复；
- parent/team/node/roster/send epoch 全绑定；
- 重启后 running → unknown；
- 用户追加预算入口。

用户不再手工勾选 roster。

## C1. 员工面板

在当前桌面工作台增加一个紧凑面板：

```text
AI 员工
────────────────
● 父 Agent          默认
○ 产品经理          静默
○ UI/UX             静默
○ 前端工程师         静默
○ 后端工程师         静默
○ 数据工程师         静默
○ 安全架构师         静默
○ 测试工程师         静默
○ 运维工程师         静默
○ 文档工程师         静默
```

状态颜色不要成为唯一提示，应同时显示文字：

```text
静默
等待
运行中
正在停止
已完成
失败
需要协作
状态未知
```

The panel shows catalog + live node state. It is not an Owner-only staffing
form.

## C2. 团队模式入口

输入区增加：

```text
[ ] 团队协作
```

打开后的诚实文案是 **Owner 开启团队模式，随后由父 Agent 判断编制；宿主按
Proposal 校验后执行**，而不是“必须勾选 2–5 名专业员工才算合法”，也不是
“父 Agent 已获得原始调度权限”。

可选：显示当前预算剩余、建议成本、wave/并发提示。这些是 UX，不是编制法。

可以提供一个可选高级设置（Workspace 偏好，默认全部允许）：

```text
允许父 Agent 使用的员工：
☑ 产品
☑ UX
☑ 前端
☑ 后端
☑ 数据
☑ 安全
☑ QA
☑ 运维
☑ 文档
```

默认全部允许。这是用户的权限/偏好设置，不是每次任务的手工 roster。

原有规则保持：

```text
一个 @ → 单员工模式（模式 2，一次调用）
多个 @ → 拒绝
@all / 广播 → 拒绝
团队模式 → Owner 打开开关；parent proposes from the nine roles;
           host validates and executes
```

## C3. 团队执行视图

运行时展示 wave 与节点时间线（人数由 validated parent Proposal 决定，不是
固定 4 格）：

```text
团队任务 #team_xxx
编制：父 Agent Proposal（宿主已校验）
Wave 1 parallel（或已降为 serial）
  frontend-review  前端工程师    已完成    8.2s    1,520 tokens
  backend-review   后端工程师    运行中    [停止]
Wave 2 serial
  security-review  安全架构师    等待（依赖 frontend+backend）
协作请求
  security → qa    待父 Agent 决策
父 Agent           汇总或 replan
预算               已用 4 / 上限 N 次调用
```

用户可以展开每个员工报告。主会话默认突出父 Agent 的当前/最终回答。

## C4. 新的 team lifecycle

不建议把团队状态硬塞进现有单 invocation reducer。新增：

```text
frontend/lib/desktop-team-lifecycle.ts
frontend/lib/desktop-team-surface.ts
```

建议状态机：

```text
idle
→ preparing
→ parent_proposing
→ host_validating
→ wave_starting
→ node_starting
→ node_identity
→ node_running          (one or more; bound by maximumConcurrentCalls)
→ node_terminal
→ blackboard_updated
→ parent_replanning | parent_synthesizing
→ completed | budget_exhausted | cannot_complete

任何运行态
→ cancelling
→ cancelled | unknown
→ idle
```

事件至少绑定：

```text
workspaceId
conversationId
teamRunId
planRevisionId
waveId
assignmentId
rosterEpoch
nodeId
nodeOrdinal
employeeRoleId
invocationId
sendEpoch
```

缺少或漂移任一身份字段，不得投影到当前节点。

## C5. Stop 行为

全局 Stop 在整个 team run 期间一直可见。

Stop 必须：

1. 立即把 UI 切换为“正在停止”；
2. 中止**每一个当前 live** provider stream（P6.8 覆盖 one stream；并行 wave
   需要新的 N-stream abort）；
3. 对已有 invocation ID 发 durable cancel；
4. 当前节点收敛为 cancelled 或 unknown；
5. 尚未开始的 assignment / 等待节点不得再启动；
6. 不允许自动重放；
7. team Promise 收敛以后才能重新发送。

Stop inherits P6.8 arm-abort-first: abort must be armed before Provider/Vault
await.

If a wave has overlapping in-flight calls, Stop for N streams is **new
P6.9-B/C work**. Do not claim P6.8 already does it.

## C6. Workspace/Conversation 切换

团队任务可以在后台保持**已开始的**调用状态，但不能在 Owner 未开团队模式时
后台自主产生新任务。

用户切换 Workspace 时（scope switch keeps global Stop, no old stream paint）：

- 当前团队调用身份仍由全局 lifecycle 保存；
- 新 Workspace 不显示旧团队的流文本；
- 全局 Stop 仍可达；
- 返回原 Workspace/Conversation 后恢复节点状态；
- 迟到事件必须同时匹配 team/planRevision/wave/assignment/node/send epoch；
- 旧列表、旧详情、旧节点结果不得覆盖当前视图。

---

# P6.9-D：动态团队产品验收

P6.9-D 的目标不是“测试函数能循环三次”，而是证明产品真的进行了多次独立模型
调用，并且 **team mode 的编制来自校验后的父 Agent Proposal**，不是 Owner 闭集
checkbox，也不是未校验的 raw dispatch。

## D1. 父 Agent 决策能力

必须新增以下真实矩阵：

- 判断无需员工，直接回答；
- 判断只需要一名员工；
- 判断需要多名员工；
- 判断需要全部九名员工；
- 判断部分员工可以并行；
- 判断某员工依赖另一员工报告；
- 中途追加员工；
- 要求已有员工二次补充；
- 根据员工协作请求启动新员工；
- 判断信息已经足够并提前结束。

每次 team 证明必须包括：

- Provider call count 与 **实际节点** 相等（无隐藏调用）；
- 每个 invocation ID 唯一；
- 每个 node ID 唯一；
- 每个 assignment ID 唯一；
- 每个角色来自九专业员工闭集；
- 父 Agent 输出过可校验 Proposal（不是 UI 预勾选冒充 parent，也不是 raw
  dispatch 日志）；
- Token 使用按节点和总量显示；
- 旧 run 的报告不能进入新 run；
- 同角色二次调用使用新 identity，旧报告仍在。

1/3/4/6 仍可用作 **example journeys**（复用 P6.4 证伪经验），但它们不是唯一
合法编制，也不是 Owner checkbox 验收。

## D2. 模型配置矩阵

至少验证：

1. 十个角色都未配置覆盖，全部继承默认 Provider；
2. 一个专业员工使用不同模型名、同 URL/Key；
3. 一个专业员工选择不同已保存 Provider；
4. 中转站 URL + `deepseek-*` 模型名称仍按 DeepSeek profile；
5. 中转站 URL + `gpt-*` 模型名称仍按 GPT profile；
6. requested/actual model 不一致时该节点失败关闭；
7. Provider 被删除或禁用后旧角色配置不可偷偷回退；
8. API Key 从不进入 renderer、日志、team receipt 或 SQLite role config。

## D3. 取消与竞态矩阵

至少覆盖：

- 第一个节点 identity 前 Stop；
- 专业员工 delta 中 Stop；
- 专业员工完成、下一节点开始前 Stop；
- 并行 wave 中 Stop 同时取消多个 active node；
- 父 Agent 汇总或 replan 中 Stop；
- Stop 后迟到 identity；
- Stop 后迟到 success；
- Workspace A → B → A；
- Conversation A → B → A；
- 旧 team terminal 到达新 team；
- 旧 wave 事件进入新 wave；
- 缺失 rosterEpoch / planRevisionId / waveId / assignmentId；
- 错误 node ordinal；
- 正确 invocation ID 但错误 sendEpoch；
- renderer 被销毁；
- 应用进程重启。

重启以后：

```text
running / starting / streaming
→ unknown
```

禁止自动继续剩余员工。

## D4. 安全验证

至少覆盖：

- 父 Agent 输出未知角色；
- 父 Agent 输出重复 assignment ID；
- 父 Agent 输出不存在的依赖；
- 父 Agent 输出循环依赖；
- 父 Agent 要求工具；
- 父 Agent 要求跨 Workspace；
- 父 Agent 要求无限预算；
- 父 Agent 不断重规划；
- 员工要求直接启动另一员工；
- 员工协作请求包含秘密；
- 并发节点混淆 identity；
- 旧 wave 事件进入新 wave；
- 同角色第二次调用复用旧 invocation；
- Stop 同时取消多个 active node；
- Stop 后迟到 success；
- renderer 销毁；
- 应用重启；
- Provider 部分失败；
- 部分并行节点 unknown；
- 宿主把有依赖的节点错误并行化（必须失败/禁止）；
- 宿主把无依赖并行组降为串行（必须合法且语义不变）；
- SQLite audit append 失败；
- role config CAS 冲突。

Do **not** treat “roster 重复角色” or “participantCount 必须等于勾选数” as
Owner-roster crimes. Duplicate **assignment / invocation** identity is a
runtime crime. Re-dispatching the same catalog role as a **new** assignment
and invocation is legal follow-up.

## D5. 产品旅程

最终至少完成一次 production-mode 桌面旅程：

```text
用户开启团队模式
→ 提交一个复杂任务
→ 父 Agent 决定调用前端、后端、安全
→ 前端/后端并行
→ 安全读取两份报告后复核
→ 安全建议 QA 设计测试
→ 父 Agent 接受请求并启动 QA
→ QA 返回攻击矩阵
→ 父 Agent 最终综合
→ 用户查看完整团队时间线、模型、Token、耗时
→ 第二次运行中点击 Stop
→ 所有活动节点取消，等待节点不再启动
→ 重启应用
→ cancelled/unknown 不自动重放
```

工程验收可以使用 deterministic loopback Provider。

真实付费 Provider 验证应作为单独产品证据，并且只能通过界面存入 Vault；不从
聊天历史复用任何 Key。

---

# 与企业 Multi-Agent 的区别

为避免文档产生新的误解，必须诚实写明：

P6.9 计划成为实际的个人多 Agent 协作，不再只是假角色或固定测试 roster。
**今天**产品法仍是规划（`PERSONAL_MULTI_AGENT_PLANNED`）；A2 已完成合同、
schema 与 IPC（`P6_9_A2_CONTRACT_SCHEMA_IPC_COMPLETE`）。它仍不等于企业版，
也还不是 `PERSONAL_MULTI_AGENT_IMPLEMENTED`。

| P6.9 个人团队 | 企业 Multi-Agent |
|---|---|
| 唯一 Owner | 多用户与多审批人 |
| 用户开启团队模式即授权 | 组织策略与分级审批 |
| 父 Agent 动态调动九名员工（经 Proposal） | 动态注册和组织级 Agent |
| 单一 Workspace | 跨团队/跨租户资源 |
| 单一本机 Vault | 企业密钥托管与轮换 |
| 一个共享执行环境原则 | 多节点、多 Sandbox、分布式调度 |
| 本机协调器 | 企业 Planner/Scheduler/Worker |
| 用户可见预算 | 部门预算与配额系统 |
| 无后台自治 | 可治理的长期任务和队列 |
| 本地 SQLite | 生产分布式持久化与恢复 |

所以正式状态写成：

```text
PERSONAL_MULTI_AGENT_PLANNED
ENTERPRISE_MULTI_AGENT_DISABLED
```

二者不冲突。`PERSONAL_MULTI_AGENT_IMPLEMENTED` 是 P6.9-D 之后的未来产品声明，
不是本规划修订的当前事实。

---

# P6.9 明确不做什么

为了避免再次把个人版做成企业治理工程，P6.9 R0 不做：

- 企业 `MULTI_AGENT_ENABLED` 激活；
- 企业自主 Planner / 任意 DAG / P34.7 Trust Policy；
- 未开团队模式时的自主唤醒、背景定时唤醒、后台守护进程；
- 无限循环；
- 广播 `@all`；
- 并行 Provider **洪泛**（unbounded fan-out）。Parallel waves are allowed
  inside `maximumConcurrentCalls`；host may serialize. Unbounded fan-out is
  not；
- 给父 Agent 原始调度权限 / 未校验模型 JSON 直接 invoke；
- 员工绕过父 Agent 启动另一员工；
- 每 Agent 独立 Sandbox；
- Shell/SQL/任意 HTTP；
- MCP；
- Skills 执行；
- RAG/文件树扩张；
- 新 EXE 重打包；
- Authenticode；
- P7 视觉重构；
- OSelf 集成。

**Withdrawn for team mode (not a hard non-goal anymore):**

- “Agent 自己选择团队”
- “父 Agent 不能在执行中添加成员”
- “禁止 parent-directed specialist dispatch”
- “A2A / 协作能力整包冻结”
- “默认固定串行，并行尚未授权”

OSelf 继续是独立从属项目，不进入 P6.9 验收条件。

---

# 关于“共享一个 Sandbox”

P6.9 的架构应明确：

```text
一个 Owner
一个 Workspace
一个未来的 task-scoped Sandbox / execution session
多个 Agent role
```

不是：

```text
十个 Agent
十台虚拟机
十套文件副本
十个数据库
```

P6.9 R0 本身仍是 no-tool 多 Agent，因此不应该伪造一个“已经运行的 Sandbox”。

本阶段只冻结接口原则：

```ts
interface PersonalTeamExecutionContext {
  workspaceId: string
  teamRunId: string
  sharedExecutionSessionId: string | null
}
```

R0 中：

```text
sharedExecutionSessionId = null
tools_enabled = false
sandbox_active = false
```

以后恢复文件和工具能力时，所有成员引用同一个 task-scoped execution session，
并通过同一份 ChangeSet/审计记录，不允许每个 Agent 私自创建自己的沙箱。

---

# 建议提交顺序

建议保持 forward-only，小提交分层：

```text
P6.9-A1
docs(p6.9): define personal multi-agent team boundary
  (95fa6d6: Owner amendment — parent may staff in team mode)
docs(p6.9): record parent proposal contract and team blackboard
  (this revision: Codex contract is now-authoritative)

P6.9-A2
feat(desktop-local): add personal team schema and role settings
  (include team_plan_revision / team_assignment / team_collaboration_request)

P6.9-A3
feat(desktop): expose closed role/team IPC contracts

P6.9-B1
feat(desktop): implement team coordinator with parent Proposal + host validation
  (serial, parallel, mixed waves; blackboard; replan)

P6.9-B2
fix(desktop): bind team node events to plan/wave/assignment/node/send epochs

P6.9-C1
feat(workbench): add team-mode control, budget, and node/wave timeline
  (Owner opens 团队协作; allow-list default all nine;
   do not ship closed 2–5 roster as the only law)

P6.9-C2
fix(workbench): preserve Stop and projection scope across team runs
  (including N-stream abort for parallel waves)

P6.9-D1
test(p6.9): prove validated parent-Proposal independent invocations
            + mode 1/2 regression + decision/safety matrices

P6.9-D2
docs(p6.9): record personal multi-agent engineering acceptance
  (only then may PERSONAL_MULTI_AGENT_IMPLEMENTED be considered)
```

不建议在同一个提交里同时做 migration、协调器、UI 和验收文档。

This recording is **A1 only**. Do not start A2 schema on the planning branch
or on the empty Codex pointer.

---

# 预计周期

因为已有以下可复用基础：

- P6.0 的 1 父 9 子角色目录；
- `@` 单员工解析；
- P6.0-D2 per-role 模型选择思路；
- P6.4 的串行节点 / 独立 invocation 经验（runtime，不是 Owner 编制法）；
- P6.7 的本机 Vault/Provider/SQLite conversation；
- P6.8 的 stream/Stop/epoch/scope 状态机（one live stream；N-stream 仍需新做）；

P6.9 不需要从零开始。Proposal 校验、黑板、replan 与 N-stream Stop 要在 A/B/C
钉死，所以周期仍按个人版窄边界估计：

| 阶段 | 预计 |
|---|---:|
| P6.9-A | 0.5–1 个有效开发日 |
| P6.9-B | 1–1.5 个有效开发日 |
| P6.9-C | 1–1.5 个有效开发日 |
| P6.9-D | 0.5–1 个有效开发日 |
| 合计 | 约 3–5 个有效开发日 |

这里不包含：

- 新 EXE 重打包；
- Authenticode；
- P7 UI 打磨；
- 外部 Provider 故障或网络等待；
- OSelf；
- 文件/RAG/Sandbox 工具接入。

---

# Planning flags

Authoritative for this revision:

```text
P6_9_DIRECTION_CHANGE_OWNER_APPROVED
P6_9_PARENT_DYNAMIC_DELEGATION_AUTHORIZED
P6_9_PARENT_SELECTS_EMPLOYEE_COUNT_AND_ROSTER
P6_9_PARENT_MAY_REPLAN_AND_REQUEST_FOLLOWUP
P6_9_EMPLOYEE_COLLABORATION_ALLOWED
P6_9_SERIAL_PARALLEL_AND_MIXED_WAVES_ALLOWED
P6_9_ALL_NINE_SPECIALISTS_MAY_PARTICIPATE
P6_9_EMPLOYEE_REINVOCATION_ALLOWED_WITH_NEW_IDENTITY
P6_9_OWNER_TEAM_MODE_IS_TASK_LEVEL_DELEGATION_APPROVAL
P6_9_HOST_ENFORCES_IDENTITY_BUDGET_AUTHORITY_AND_RECOVERY
P6_9_HOST_DOES_NOT_MICROMANAGE_COLLABORATION_TOPOLOGY
P6_9_ENTERPRISE_PLANNER_GATE_REMAINS_FALSE
P6_9_ENTERPRISE_MULTI_AGENT_GATE_REMAINS_FALSE
P6_9_TOOL_AND_EXTERNAL_EFFECT_AUTHORITY_NOT_IMPLIED
P6_9_PARENT_OUTPUT_IS_STRUCTURED_PROPOSAL_NOT_RAW_DISPATCH
P6_9_A2_CONTRACT_SCHEMA_IPC_COMPLETE
P6_8_BASE_HEAD_D2A2DB0_UNCHANGED
P6_8_SINGLE_AGENT_PATH_NOT_REGRESSED
PERSONAL_MULTI_AGENT_PLANNED
ENTERPRISE_MULTI_AGENT_DISABLED
```

Still true, not replaced:

```text
P6_8_SMALL_CLOSEOUT_ACCEPTED
P6_9_PERSONAL_MULTI_AGENT_TEAM_R0_PLANNED
P6_9_ONE_PARENT_NINE_DORMANT_SPECIALISTS
P6_9_PER_ROLE_MODEL_CONFIGURATION_PLANNED
P6_9_SINGLE_SHARED_WORKSPACE_CONTEXT
P6_9_AUTONOMOUS_WAKE_DISABLED_OUTSIDE_TEAM_MODE
P6_9_INSTALLER_REBUILD_DEFERRED
ENGINEERING_ACCEPTANCE_RESERVED_FOR_CODEX
REPACKAGE_NOT_APPROVED
PUSH_PR_NOT_APPROVED
```

Replaced (do not keep as live law):

```text
P6_9_OWNER_DECLARED_ROSTER_ONLY          → withdrawn
P6_9_SERIAL_3_TO_6_CALLS                 → withdrawn as staffing/call cap
P6_9_AUTONOMOUS_DELEGATION_DISABLED      → replaced by
                                           P6_9_AUTONOMOUS_WAKE_DISABLED_OUTSIDE_TEAM_MODE
P6_9_TEAM_MODE_PARENT_MAY_DISPATCH       → superseded by
                                           P6_9_PARENT_OUTPUT_IS_STRUCTURED_PROPOSAL_NOT_RAW_DISPATCH
P6_9_DISPATCH_AND_COLLABORATION_NOT_ARTIFICIALLY_CAPPED
                                         → kept in spirit; restated as
                                           host identity/budget/authority envelope,
                                           not a frozen Owner roster
PERSONAL_MULTI_AGENT_IMPLEMENTED         → reserved for after P6.9-D
                                           engineering acceptance; PLANNED is
                                           already current during A/B/C
```

下一次开始执行时，从已验收的 P6.8 HEAD `d2a2db0` 使用独立空指针分支：

```text
codex/p6-9-personal-multi-agent-team-r0
```

并先只完成 P6.9-A 的合同、schema 和关闭式 IPC。合同必须是 **parent structured
Proposal + host validation + Personal Team Blackboard + replan**，不得把撤回
的 closed Owner roster 写进 schema 当唯一合法 start，也不得把未校验模型输出
当成 raw dispatch。P6.9-A implementation is **not started** by this docs
recording.
