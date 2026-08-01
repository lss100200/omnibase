# PostgreSQL 备份与恢复到新数据库 Runbook

OmniBase 的恢复策略是“验证备份，恢复到新数据库，校验后切换”。脚本不会覆盖已有数据库，也不会自动删除失败的恢复目标。

## 凭据与工具

- 需要与服务端 major version 兼容的 `pg_dump`、`pg_restore`、`psql`、`createdb`。
- 脚本不读取 `.env`。通过受限 `PGPASSFILE`、平台 Secret 或临时 `PGPASSWORD` 注入凭据；不要把密码放入参数。
- 在仓库根诊断 Compose 时必须显式使用 `docker compose --env-file .env.example ...`；disposable restore/test overlay 必须使用它自己的专用 Compose/env 文件。禁止运行裸 `docker compose config --format json`，因为 Compose 会隐式读取根 `.env` 并把展开后的 secret 写入终端或 artifact。
- 备份目录必须位于 Git 工作树之外，并使用主机加密、访问控制和离线/异地副本。
- 加密密钥必须由部署方的 Secret/KMS 管理，不能和备份放在同一目录或同一仓库。

## 创建备份

```powershell
python scripts/database/backup.py `
  --database omnibase `
  --output-dir D:\omnibase-backups `
  --host 127.0.0.1 `
  --port 5432 `
  --username omnibase_backup `
  --label pre_0006
```

产物包括 `.dump`、`.manifest.json` 和 `.sha256`。manifest 只记录数据库名、文件名、checksum、时间与 `pg_dump` 版本，不记录密码或数据库 URL。将三者作为同一不可变备份集保留。

## 恢复到新数据库

目标名必须以 `omnibase_restore_` 开头，且必须不存在：

```powershell
python scripts/database/restore_to_new_database.py `
  --backup D:\omnibase-backups\omnibase_pre_0006_20260801T000000Z.dump `
  --manifest D:\omnibase-backups\omnibase_pre_0006_20260801T000000Z.manifest.json `
  --target-database omnibase_restore_20260801 `
  --maintenance-database postgres `
  --host 127.0.0.1 `
  --port 5432 `
  --username omnibase_restore_operator `
  --confirm CREATE_NEW_DATABASE_ONLY
```

脚本先验证 manifest 和 SHA-256，再确认目标数据库不存在，随后创建新库并以单事务执行 `pg_restore`。恢复失败时保留新库供调查；只有部署所有者确认目标后才能用显式数据库管理命令删除。

## 只读恢复校验

```powershell
python scripts/database/verify_restore.py `
  --database omnibase_restore_20260801 `
  --expected-revision 0006 `
  --host 127.0.0.1 `
  --port 5432 `
  --username omnibase_restore_auditor
```

校验包括目标库名、`omnibase_meta`、Alembic revision、Tenant registry、缺失 Tenant schema、Audit append-only trigger 和 Capability revocation trigger。之后仍需执行应用 smoke、抽样业务校验、对象存储一致性检查和性能检查。

## 保留、RPO 与 RTO

部署方必须明确写入自己的运维策略：

- RPO：可接受丢失的最大数据时间窗口。
- RTO：从故障到验证完成并切换到恢复库的最大时间。
- 全量/增量/WAL 归档频率、保留周期、异地副本数量和季度恢复演练频率。
- 备份加密、密钥轮换、访问审计和销毁流程。

仓库不虚构统一的生产 RPO/RTO。未完成定期恢复演练的备份不能被视为可恢复证据。
