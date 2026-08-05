# P5.2B Task Ledger Persistence — Admission Decision

> 日期：2026-08-04。
> 审查对象：是否可以开始 P5.2B（AgentTask/AgentRun/AgentStep/AgentAttempt/
> TaskLease 持久化账本）的工程实现。
> 决策闭集：`AUTHORIZED_FOR_ENGINEERING | BLOCKED_NOT_AUTHORIZED |
> INVALID_VETO`。
> 本决策是独立 admission/readiness 审查，不是 P5.2B 实现授权；设计封板见
> `docs/phase-5-task-ledger-persistence-design.md`。

## 1. 决策

```text
DECISION: BLOCKED_NOT_AUTHORIZED
```

P5.2B 持久化账本（ORM + migration `0011` + 事务服务 + disposable Gate）
**不得**在本次审查基础上开始工程实现。直接原因是尚未获得用户对 migration
`0011`、ORM、事务服务与 disposable Gate 的显式实现授权。

独立复核已修正原草案的一项循环条件：P5.2A `--verify` 在 P5.2B 尚未实现时
会明确报告 “persistence ledger is not implemented”，因此 formal state 为
`blocked/not_proven` 是预期结果，不能要求它先 ready 才允许实现 P5.2B。
P34.7/P5.0/P5.1 未 ready 继续阻止 Runtime/production activation，但不自动
否决默认关闭、sentinel-only 的内部持久化工程。精确双层条件见设计 §I。

## 2. 逐项证据（2026-08-04，在 clean worktree 上实测）

| # | 解冻条件 | 当前事实 | 证据 |
|---|---|---|---|
| 1 | P34.7 formal state = ready | **blocked/not_proven**；production blocker，不是 engineering-only veto | handover `### P34.7 production readiness`；`docs/evidence/p34-7/production-readiness-decision.md` sealed |
| 2 | P5.0 admission = ready | **blocked/not_proven**；production blocker | `validate_p5_0_admission.py --verify` → exit 2，vetoes=`[]` |
| 3 | P5.1 production = ready | **blocked/not_proven**；production blocker | `validate_p5_1_registry_contract.py --verify` → exit 2，`contract_valid=true`，vetoes=`[]` |
| 4 | P5.2A contract valid/sealed/veto-free | **满足合同层条件**；formal blocked 是预期 | `validate_p5_2a_task_ledger_contract.py --verify` → exit 2，`contract_valid=true`，`sealed_digests_verified=true`，vetoes=`[]`；blocker 包含 persistence ledger 未实现 |
| 5 | migration head = 0010 | **0010** | 三份 `--verify` report 均 `migration_head=0010`；migration `0011` 不存在 |
| 6 | Feature Gates false/false/false | **false/false/false** | 三份 report `feature_gates` 全 false |
| 7 | source clean | **clean** | 三份 report `source.clean=true`、`dirty_paths=[]` |
| 8 | sealed digests 通过 | **通过** | P5.2A `gate_execution.sealed_digests_verified=true`；P5.1A/P5.0 无 veto（digest 漂移即 veto） |
| 9 | production required evidence 全部存在 | **not_proven（缺生产证据）**；继续阻止激活 | 三份 report blockers 含 P34.7/P5.0/P5.1 未 ready 与 evidence `not_proven` |
| 10 | Critical Veto = 0 | **0** | 三份 report `vetoes=[]`（满足，但不足以免除其余条件） |
| 11 | 用户显式授权 migration 0011 + P5.2B ORM | **未授权** | 本任务说明书明确只允许"设计 migration 0011，不得创建 migration 文件"；无实现授权 |

## 3. 判定逻辑

- 条件 1–3 与 9 不成立，故 Runtime/production activation 必须保持 blocked。
- 条件 4–8、10 证明 docs-only 设计可以被独立审查，但没有替代条件 11 的
  用户授权。P5.2A formal blocked 不再被错误计为 engineering veto。
- 因此本任务只产出：设计封板文档 + 本决策记录 + handover 记录；不创建任何
  实现文件，不重算任何 sealed digest（未触碰权威/封存文件，见
  `docs/phase-5-task-ledger-persistence-design.md` §J）。

## 4. 安全与边界声明（实测）

- `root_env_accessed=false`、`business_database_accessed=false`、
  `business_database_migrated=false`（三份 `--verify` report）；
- 未创建/修改 migration 文件；未创建测试数据库；未访问 PostgreSQL/Redis/
  MinIO；未运行任何迁移；
- 未实现/未解锁 P5.2B ORM、Task Lease 发放器、Agent Runtime、Planner、
  Executor、Dispatcher、Scheduler、Worker、Task API/SDK、Model/Tool
  Gateway、Memory/Skill Runtime、MCP、多 Agent DAG、Celery Agent worker；
- 未 push/PR/merge；未修改其他 worktree；未使用 `git add .`。

## 5. 未来解冻路径

1. 主 Agent 接受 P5.2A 的合同有效性与本设计修订；
2. 用户显式授权创建 migration `0011` 与 P5.2B ORM/事务服务/disposable
   Gate；随后可进行 engineering-only 实现，但 Feature Gates 与 production
   wiring 继续关闭；
3. 实现分支把 migration head、P5.0/P5.1A/P5.2A baseline/sealed digest 同步
   更新为 `0011`，并通过 fresh sentinel Gate；
4. P34.7 production total Gate、P5.0 admission、P5.1 production 及后续
   Runtime Gate 全部 ready 后，才讨论 Task Lease/Runtime 生产激活。

完整机器可验证清单见设计文档 §I。

---

*本决策不构成 P5.2B 已实现、已授权或 production ready 的任何声明。*
