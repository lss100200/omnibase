# Migration 回退与 Forward-fix Runbook

P34 基线默认采用 forward-fix。`make migrate-down` 只是受确认词保护的本地开发工具，不是生产恢复策略。

## 为什么不能依赖 downgrade

- `0006` 在存在动态资源、待处理 payload 或相关状态时会拒绝 downgrade，以避免静默丢失数据。
- schema downgrade 无法自动恢复迁移期间产生的新业务写入、外部对象存储状态、任务队列状态或应用行为变化。
- 在多 Tenant schema 上部分成功、部分失败时，盲目 downgrade 会放大不一致。

## 故障处置优先级

1. 停止继续迁移和新版本写入，保留数据库、应用、锁、WAL 和错误证据。
2. 判定迁移事务是否整体回滚，以及 global/各 Tenant 当前 revision；不要根据应用启动状态猜测。
3. 若 schema 已兼容旧应用，切回旧应用版本并保持数据库不动，同时准备 forward-fix revision。
4. 若数据库不可继续服务，使用迁移前已验证备份恢复到新的 `omnibase_restore_*` 数据库。
5. 对恢复库执行 `verify_restore.py`、应用 smoke、Tenant 隔离和数据抽样检查，再通过配置/连接切换；不要覆盖原库。
6. 保留原故障库为只读取证对象，直到事故复盘、数据对账和部署所有者批准销毁。

## Forward-fix 要求

- 新 revision 只能修复已知失败状态，不得重写已发布 revision 文件。
- 必须能识别“未执行、部分执行、已执行”状态，并在每种状态下保持幂等或明确 fail-closed。
- 不得删除/改写 append-only Audit 或 Capability revocation 记录。
- 必须在 fresh sentinel 和从非空脱敏备份恢复出的 staging 数据库上测试。
- 记录锁、耗时、磁盘/WAL 增长、受影响 Tenant、应用兼容窗口和停止条件。

## 明确禁止

- 未确认目标数据库时执行 downgrade、drop、restore 或清理。
- 恢复覆盖原数据库。
- 修改既有 migration revision 来“让当前库通过”。
- 把 `alembic_version` 手工改成期望值而不执行/验证实际 DDL。
- 在普通业务数据库运行 `backend/tests/cleanup.py` 或 destructive integration。
- 把数据库 URL、密码、token、真实用户行或原始生产日志写入 Git 证据。
