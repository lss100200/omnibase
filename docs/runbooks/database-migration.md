# OmniBase 数据库迁移 Runbook

本 Runbook 适用于自托管 OmniBase。任何业务数据库迁移都需要部署所有者单独授权；代码合并、CI 通过或一次性 sentinel 测试通过，不等于已经获得业务数据库迁移授权。

## 迁移前置条件

1. 固定目标版本和迁移链，确认 Alembic 只有一个 head，当前基线为 `0006`。
2. 确认应用版本、数据库版本、pgvector 版本和 PostgreSQL 客户端工具版本已记录。
3. 明确维护窗口、负责人、观察人、RPO、RTO、停止条件和切换回旧应用的条件。
4. 确认数据库磁盘、WAL、备份目录和恢复目标实例都有足够空间；不得把备份写进 Git 工作树。
5. 使用 `scripts/database/backup.py` 生成 custom-format 备份、manifest 和 SHA-256。
6. 将备份恢复到名称以 `omnibase_restore_` 开头的新数据库，并运行 `verify_restore.py`。未通过恢复演练时不得迁移业务数据库。
7. 在 staging 的非空、脱敏、生产形态数据上执行完整 `0003 -> 0006` 演练，记录总耗时、最长锁等待、磁盘/WAL 增长和失败恢复结果。
8. 检查所有活跃及停用 Tenant：停用 Tenant 的 schema 仍会迁移，因为停用是软删除，不代表可丢弃数据。

## 迁移作用域约束

- `migration_schema_scope` 只接受 `global` 或 `tenant`；缺失、大小写错误和未知值一律中止。
- global 阶段只处理 `omnibase_meta`；tenant 阶段逐一处理注册表中的 Tenant schema。
- `alembic upgrade head --sql` 只生成 global SQL，不生成 Tenant schema SQL；它不能替代 online staging 演练。
- 禁止手工修改 `search_path` 来绕过迁移环境，禁止在普通应用连接中直接执行 migration revision。

## 建议执行顺序

```text
应用进入维护/只读窗口
  -> 再次确认备份与 checksum
  -> 记录 alembic current/heads
  -> 执行 alembic upgrade head
  -> 检查 global 与每个 tenant 的 revision
  -> 运行只读结构/租户/审计检查
  -> 启动新应用并执行 smoke
  -> 解除维护窗口
```

执行命令必须在已明确绑定目标数据库的受控运维环境中运行。不要从仓库根目录自动读取 `.env`，不要把数据库 URL、密码或 token 写进命令输出、报告或 shell history；优先使用受限的 `PGPASSFILE` 或平台 Secret 注入。

## 停止条件

出现以下任一情况立即停止后续步骤，不得盲目重试：

- 目标数据库、主机、端口或部署环境与变更单不一致。
- backup manifest/checksum 不一致，或恢复演练未通过。
- Alembic 出现多 head、revision 缺失或作用域异常。
- 锁等待、WAL/磁盘增长或迁移耗时超过已批准阈值。
- 任一 Tenant schema revision 落后、缺失，或 Tenant registry 与物理 schema 不一致。
- append-only Audit/Capability trigger 缺失，或应用 smoke 出现跨 Tenant、授权、幂等错误。

## 迁移后检查

- global 与所有 Tenant 的 revision 与目标一致。
- Tenant registry 中的 schema 全部存在，停用 Tenant 数据仍保留。
- `audit_events_append_only` 和 `capability_revocations_append_only` trigger 存在。
- `/health`、认证、Tenant 上下文、只读 capability、P34.3 默认 503 边界符合预期。
- 观察错误率、连接池、慢查询、锁、WAL、磁盘和后台任务至少一个完整业务周期。
- 将证据写入变更记录；不得把凭据、真实用户数据、原始 SQL 参数或完整生产日志写入仓库。
