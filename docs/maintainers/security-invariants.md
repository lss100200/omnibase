# OmniBase 安全不变量维护者地图

本文是面向维护者和自动修复 Agent 的强制契约。修复类型错误、升级依赖、重构服务、调整数据库或补测试时，不得以“测试通过”为理由削弱这些不变量。若改动触及多个不变量，必须运行各条目测试集合的并集。

文中的“权威源码”指真正实施约束的位置；报告、路线图和本文本身都不能替代源码、数据库约束与测试。

## INV-001 live-principal

**权威源码**

- `backend/src/omnibase/tenants/dependencies.py`
- `backend/src/omnibase/auth/security.py`
- `backend/src/omnibase/core/db.py`

**为何存在**

访问令牌只证明令牌签发时的身份，不能证明当前 Tenant/User 仍存在、仍启用或仍拥有原角色。每次受保护请求都必须从 registry Tenant 和明确绑定的 tenant schema 重新读取实时主体；停用和降权不能等到 JWT 过期才生效。

**允许的改法**

- 缓存签名验证等不改变授权事实的纯计算结果。
- 在同一请求内传递已经由 `get_current_principal()` 建立的可信主体对象。
- 优化查询 projection、线程池调用或 Session 生命周期，但必须保留实时数据库复核。

**禁止的改法**

- 仅信任 JWT 中的 tenant、schema、active 或 role claim。
- 从客户端 Header、请求体或 query 参数构造 Tenant/User 上下文。
- 使用锁前、缓存中或其他 Tenant schema 的 User 状态代替最终授权复核。
- 将“找不到/已停用”降级成匿名、默认 Tenant 或默认管理员。

**必须运行的测试**

- `backend/tests/test_tenants.py`
- `backend/tests/integration/test_auth_e2e.py`
- `backend/tests/integration/test_p34_3_controlled_data_lock_races.py`
- `backend/tests/test_p0_exposure_lockdown.py`

**失败恢复**

立即关闭受影响 Router 或恢复拒绝型依赖；撤销可能仍有效的会话/令牌，恢复数据库实时查询后再重新开放。不得通过延长 Token TTL、硬编码角色或跳过 Tenant/User 状态检查止血。

## INV-002 tenant-schema-binding

**权威源码**

- `backend/src/omnibase/core/db.py`
- `backend/src/omnibase/tenants/dependencies.py`
- `backend/src/omnibase/controlled_data/executor.py`
- `backend/src/omnibase/controlled_data/ddl.py`
- `backend/src/omnibase/capability_gateway/resolver.py`

**为何存在**

Tenant ID、registry `schema_name`、Session tenant context、Resource/Binding tenant 字段和物理 locator 必须指向同一信任域。仅依赖 `search_path`、调用方提供的 schema 或 UUID 全局唯一性，会把跨 Tenant 误读/误写变成静默数据泄露。

**允许的改法**

- 使用 registry Tenant 的 `schema_name` 显式限定 tenant 表查询。
- 在同一事务内锁定 Tenant/Resource/Binding 后重建可信 locator。
- 增加 composite tenant 约束、精确 tenant predicate 和 decoy-schema 测试。

**禁止的改法**

- 接受客户端 schema、表名、列名或 `search_path` 作为授权依据。
- 查询 User、Workspace、payload 或数据表时省略 Tenant/schema 绑定。
- 因 UUID 看似唯一而删除 `tenant_id` predicate、复合外键或 registry schema 比对。
- 在锁外解析 locator 后不做锁内版本和绑定复核。

**必须运行的测试**

- `backend/tests/test_tenants.py`
- `backend/tests/test_p34_2_gateway_query.py`
- `backend/tests/test_p34_3_controlled_data_executor.py`
- `backend/tests/integration/test_p34_3_controlled_data_executor.py`
- `backend/tests/integration/test_p34_3_controlled_data_concurrency.py`
- `backend/tests/integration/test_p34_3_controlled_data_router.py`

**失败恢复**

停止受影响读写入口并撤销相关 capability；使用 registry 数据重新解析绑定，在隔离的新测试数据库中复现 decoy schema/同 UUID 场景。不得直接修补普通业务库中的 `search_path` 来掩盖源码绑定缺陷。

## INV-003 logical-identifiers-only

**权威源码**

- `backend/src/omnibase/control_plane/schemas.py`
- `backend/src/omnibase/capability_gateway/contracts.py`
- `backend/src/omnibase/capability_gateway/query.py`
- `backend/src/omnibase/capability_gateway/resolver.py`
- `backend/src/omnibase/controlled_data/schemas.py`
- `backend/src/omnibase/controlled_data/crud_contracts.py`
- `backend/src/omnibase/controlled_data/identifiers.py`
- `backend/src/omnibase/controlled_data/router.py`

**为何存在**

公共 API 只能表达逻辑 Resource/Column UUID、结构化 predicate、版本和预算。物理 schema/table/column、SQL、CTID、provider handle、AuthorizationContext 和 Operation 都是服务器内部能力；向客户端暴露或接受这些字段会绕过 Registry、RBAC、审计和迁移边界。

**允许的改法**

- 扩展经过闭集验证的逻辑 DTO、逻辑类型或结构化操作。
- 由服务器从逻辑 UUID 确定性派生物理 identifier。
- 在内部可信对象中携带深冻结 locator，并在执行前重新验证。

**禁止的改法**

- 公共 DTO 接受任意 SQL、SQL fragment、schema/table/column 名、CTID 或 locator。
- 在响应、OpenAPI、Audit details 或错误中返回物理定位信息。
- 用 display name、用户字符串或 Binding ID 直接生成物理 identifier。
- 为方便调试开放“管理员 raw SQL”旁路。

**必须运行的测试**

- `backend/tests/test_p34_1_control_plane_api.py`
- `backend/tests/test_p34_2_gateway_api.py`
- `backend/tests/test_p34_2_gateway_query.py`
- `backend/tests/test_p34_3_controlled_data_api.py`
- `backend/tests/test_p34_3_controlled_data_crud.py`
- `backend/tests/test_p34_3_controlled_data_foundation.py`
- `backend/tests/integration/test_p34_3_controlled_data_router.py`

**失败恢复**

立即从 Router/OpenAPI 撤下新增物理字段，轮换可能泄露的 locator/cursor/capability，恢复逻辑 DTO 后重新生成 SDK 契约。不得仅在 UI 隐藏字段而保留后端接受能力。

## INV-004 capability-scope-budget

**权威源码**

- `backend/src/omnibase/capabilities/models.py`
- `backend/src/omnibase/capabilities/token.py`
- `backend/src/omnibase/capabilities/service.py`
- `backend/src/omnibase/capability_gateway/security.py`
- `backend/src/omnibase/capability_gateway/policy.py`
- `backend/src/omnibase/capability_gateway/service.py`

**为何存在**

Capability 必须同时绑定 issuer/audience、Tenant、Workspace、Runtime、workload thumbprint、Grant/version、action、Resource、委派深度、有效期、撤销状态和在线预算。仅验证 JWT 签名或仅在响应后统计预算会允许 scope escalation、撤销竞争和超额消耗。

**允许的改法**

- 收窄 action/resource/constraint/TTL/budget。
- 在 caller-owned 事务内按固定祖先顺序锁 Grant，并原子保留 Usage。
- 增加更严格的 attestation、proof-of-possession 和预算 preflight。

**禁止的改法**

- 接受远程 JWKS、`jku`、`x5u`、嵌入式 JWK 或客户端提供的验证密钥。
- 允许子 Grant 扩大父 Grant 的 scope、预算、期限或委派深度。
- 在未验证 workload、撤销、祖先状态和 Resource scope 前调用 adapter。
- 将预算更新改为异步“最终统计”或失败后继续执行。

**必须运行的测试**

- `backend/tests/test_p34_2_capability_models.py`
- `backend/tests/test_p34_2_capability_service.py`
- `backend/tests/test_p34_2_gateway_api.py`
- `backend/tests/test_p34_2_gateway_query.py`
- `backend/tests/integration/test_p34_2_capability_foundation.py`

**失败恢复**

撤销受影响 Grant/token JTI，切回 `RejectingWorkloadAttestor` 和 `RejectingCapabilityVerifier`，停止真实 Gateway adapter。修复后必须在 fresh sentinel 数据库中验证撤销、并发预算和祖先锁；不得通过重置 Usage 或删除 revocation 记录恢复服务。

## INV-005 fail-closed-boundaries

**权威源码**

- `backend/src/omnibase/capability_gateway/app.py`
- `backend/src/omnibase/capability_gateway/security.py`
- `backend/src/omnibase/capability_gateway/adapters.py`
- `backend/src/omnibase/controlled_data/router.py`
- `backend/src/omnibase/controlled_data/execution_service.py`
- `backend/src/omnibase/core/config.py`
- `backend/src/omnibase/core/rate_limit.py`

**为何存在**

缺失 attestor/verifier/executor/adapter、无效配置、未知异常或依赖故障不能自动扩大权限。默认装配必须不可用；只有显式安装满足原子和可信标记的实现后，对应能力才能开放。

**允许的改法**

- 返回稳定的 401/403/409/429/503/504 和无敏感信息的 reason code。
- 为不同边界设计显式、可审计的 fail-open 配置，但默认值、适用范围和风险必须清晰；授权与写执行边界不得 fail-open。
- 使用拒绝型默认组件和运行时 Protocol/marker 检查。

**禁止的改法**

- 组件缺失时回退到“允许”、空鉴权、默认 Tenant 或直接 adapter/executor。
- 捕获异常后返回伪成功、空成功或跳过 Audit。
- 仅凭对象具有同名方法就绕过 atomic-lifecycle/attestation 契约。
- 将原始 SQL、Token、locator、credential 或驱动错误写入 HTTP/Audit。

**必须运行的测试**

- `backend/tests/test_p34_2_gateway_api.py`
- `backend/tests/test_p34_3_controlled_data_api.py`
- `backend/tests/test_p34_3_controlled_data_execution_service.py`
- `backend/tests/test_rate_limit.py`
- `backend/tests/test_p0_exposure_lockdown.py`
- `backend/tests/integration/test_p34_3_controlled_data_timeouts.py`

**失败恢复**

先禁用对应 feature wiring 或恢复拒绝型默认组件，再修复根因。若无法证明请求未越权，撤销相关 capability/session 并保留审计证据；不得临时安装宽松 mock 或关闭错误检查投入生产。

## INV-006 append-only-audit

**权威源码**

- `backend/src/omnibase/control_plane/models.py`
- `backend/src/omnibase/control_plane/service.py`
- `backend/src/omnibase/migrations/versions/0004_p34_1_control_plane_foundation.py`
- `backend/src/omnibase/migrations/versions/0005_p34_2_capability_ledger.py`
- `backend/src/omnibase/controlled_data/execution_service.py`

**为何存在**

成功、拒绝和失败的安全决策必须可追溯。AuditEvent 和 Capability revocation 不能被 UPDATE/DELETE、级联清理或测试 teardown 改写；成功 Audit 必须与受控写、Operation 和 Idempotency 同事务，失败 Audit 必须在写事务回滚后以独立事务持久化。

**允许的改法**

- 追加新的 code-only、字段白名单 Audit 事件。
- 增加数据库 trigger、RESTRICT 外键和只读查询索引。
- 在一次性测试数据库销毁时整体清理测试证据。

**禁止的改法**

- UPDATE/DELETE Audit 或 revocation，或关闭 trigger 以方便 cleanup。
- 使用 CASCADE 删除 Tenant/Grant 来绕过 append-only 证据。
- 成功提交后再“尽力写 Audit”，或在失败 Audit 中持久化原始异常/SQL/locator。
- 将审计写失败降级为业务成功。

**必须运行的测试**

- `backend/tests/test_p34_1_control_plane_models.py`
- `backend/tests/test_p34_1_control_plane_service.py`
- `backend/tests/test_p34_3_controlled_data_execution_service.py`
- `backend/tests/test_destructive_test_safety.py`
- `backend/tests/integration/test_p34_1_control_plane_foundation.py`
- `backend/tests/integration/test_p34_2_capability_foundation.py`
- `backend/tests/integration/test_p34_3_controlled_data_executor.py`

**失败恢复**

冻结受影响写入口，保留数据库和日志快照，并恢复 append-only trigger/RESTRICT 约束。缺失事件只能通过新的补偿 Audit 说明，禁止伪造原时间或修改旧行。测试清理冲突时销毁整个 disposable sentinel 数据库，不得削弱生产约束。

## INV-007 approval-operation-binding

**权威源码**

- `backend/src/omnibase/control_plane/models.py`
- `backend/src/omnibase/control_plane/service.py`
- `backend/src/omnibase/controlled_data/ddl.py`
- `backend/src/omnibase/controlled_data/operation_service.py`
- `backend/src/omnibase/migrations/versions/0004_p34_1_control_plane_foundation.py`

**为何存在**

高风险 Approval 不是可转移的通行证。它必须精确绑定 requester、decider role、Tenant/Workspace/Run、Operation、action、risk、request hash、Resource/version、Grant 和有效期；消费 Approval 与 Operation 入队必须原子发生。

**允许的改法**

- 增加更严格的风险等级、审批角色或绑定字段。
- 在同一 caller-owned 事务中锁定并消费 Approval、校验 Operation 后排队。
- 对失效、已消费或 cross-wired Approval fail-closed。

**禁止的改法**

- 复用一个 Approval 授权不同 Operation、Resource、版本或 payload。
- 在 Operation 已改变状态/risk/hash 后沿用旧审批结果。
- 将审批消费和 Operation 状态转换拆成可部分提交的事务。
- 允许高风险 Operation 绕过 `pending_approval`/`authorize_operation()`。

**必须运行的测试**

- `backend/tests/test_p34_1_control_plane_models.py`
- `backend/tests/test_p34_1_control_plane_service.py`
- `backend/tests/test_p34_3_controlled_data_ddl.py`
- `backend/tests/test_p34_3_create_table_bootstrap.py`
- `backend/tests/integration/test_p34_3_controlled_data_concurrency.py`

**失败恢复**

取消或 fail-close 受影响的 pending/running Operation，废弃关联 Approval，并重新生成完整 request hash 后重新审批。不得手工把 Approval 标记回未消费，也不得直接把 Operation 改成 queued/running。

## INV-008 migration-scope-closed-set

**权威源码**

- `backend/src/omnibase/migrations/env.py`
- `backend/src/omnibase/migrations/versions/0004_p34_1_control_plane_foundation.py`
- `backend/src/omnibase/migrations/versions/0005_p34_2_capability_ledger.py`
- `backend/src/omnibase/migrations/versions/0006_p34_3_controlled_data.py`

**为何存在**

OmniBase 同时迁移 global registry 和 tenant schema。迁移若猜测 scope、把未知值当 global，可能在错误 schema 创建或删除安全表。`migration_schema_scope` 必须是显式闭集 `global | tenant`，缺失、大小写错误和未知值在 upgrade/downgrade 都必须中止。

**允许的改法**

- 通过 `env.py` 显式设置 scope，并在每个双 scope migration 中使用同一闭集验证。
- 在 fresh sentinel 数据库执行 global/tenant upgrade、downgrade、re-upgrade。
- 为新 scope 先设计全仓迁移契约和测试，再一次性扩展闭集。

**禁止的改法**

- 使用默认 scope、truthy 判断、未知值回退或从 schema 名猜 scope。
- 只在 upgrade 校验而让 downgrade 宽松执行。
- 对普通业务数据库试跑未验收 migration。
- 为通过测试跳过 Alembic revision、trigger 或约束验证。

**必须运行的测试**

- `backend/tests/test_migration_scope_fail_closed.py`
- `backend/tests/test_p34_3_controlled_data_foundation.py`
- `backend/tests/integration/test_p34_1_control_plane_foundation.py`
- `backend/tests/integration/test_p34_2_capability_foundation.py`
- `backend/tests/integration/test_p34_3_controlled_data_foundation.py`
- `backend/tests/test_destructive_test_safety.py`

**失败恢复**

立即停止 migration 和应用写流量，保存 revision/schema/object 清单，并按 `docs/runbooks/migration-rollback-forward-fix.md` 选择 forward-fix 或已验证 downgrade。所有恢复先在新 sentinel 数据库演练；不得猜测当前 scope 或删除 Alembic revision 伪造成功。

## INV-009 restore-to-new-database

**权威源码**

- `scripts/database/backup.py`
- `scripts/database/restore_to_new_database.py`
- `scripts/database/verify_restore.py`
- `docs/runbooks/postgresql-backup-restore.md`

**为何存在**

恢复操作具有覆盖和不可逆风险。OmniBase 恢复只允许创建名称为 `omnibase_restore_*` 的新数据库，禁止覆盖已存在数据库；恢复后必须独立验证 revision、Tenant schema 和 append-only trigger，再由人工决定切换。

**允许的改法**

- 增强备份清单、校验和、目标库存在性检查和 restore verification。
- 失败时保留新数据库供取证，并明确报告下一步。
- 在隔离环境中演练完整 backup → restore-new → verify 流程。

**禁止的改法**

- 恢复到当前业务数据库、已存在数据库或任意非 `omnibase_restore_*` 目标。
- 自动 drop/rename 原数据库，或恢复成功后自动切流。
- 跳过 `verify_restore.py`，仅凭 `pg_restore` 退出码判定可用。
- 为清理失败恢复而自动删除唯一副本。

**必须运行的测试**

- `python -m compileall -q scripts/database`
- `python scripts/database/backup.py --help`
- `python scripts/database/restore_to_new_database.py --help`
- `python scripts/database/verify_restore.py --help`
- 按 `docs/runbooks/postgresql-backup-restore.md` 在一次性隔离 PostgreSQL 上执行 restore-to-new 和 verify；禁止把普通业务数据库作为测试目标。

**失败恢复**

保持原数据库不变，保留失败的新目标库和 restore 输出用于检查；修复脚本或备份后创建另一个全新 `omnibase_restore_*` 数据库重试。不得在失败目标上反复覆盖，也不得回写原库“补齐”对象。

## INV-010 source-complete-repairability

**权威源码**

- `backend/pyproject.toml`
- `backend/uv.lock`
- `backend/Dockerfile`
- `frontend/package.json`
- `frontend/pnpm-lock.yaml`
- `frontend/Dockerfile`
- `docker-compose.yml`
- `.github/workflows/infrastructure-gates.yml`

**为何存在**

OmniBase 是可下载、可重建的开源项目。修复必须完整存在于源码、依赖声明、锁文件、迁移和测试中；仅修改运行容器、虚拟环境、`site-packages`、`node_modules`、本机数据库或缓存，会产生无法复现、无法审计且下一次构建即丢失的“幽灵修复”。

**允许的改法**

- 同时更新源码、必要依赖声明/lock、测试和可重建配置。
- 在 clean checkout、全新依赖环境和一次性数据库中验证修复。
- 将生成物从提交中排除，并让生成步骤由源码确定性重建。
- 让维护者地图验证器反向扫描可由 AST 无歧义识别的 FastAPI 组合入口：
  顶层 `APIRouter`/`FastAPI` 赋值、直接创建并返回 `FastAPI` 的顶层工厂，
  以及同文件对该工厂的顶层实例化。该 Gate 只证明这些 HTTP 入口已被某个
  module `entrypoints` 覆盖，不把所有 public function 或 route handler 当成
  架构入口。

**禁止的改法**

- 直接编辑 `.venv`、`site-packages`、`node_modules`、运行中容器或镜像层代替源码修复。
- 依赖未提交文件、用户 `.env`、本机绝对路径、预热缓存或手工数据库状态才能通过。
- 只更新生成 SDK/OpenAPI/构建产物而不更新权威源码。
- 为通过 CI 删除测试、降低 Gate、增加全局 `ignore_missing_imports`/宽泛 ignore 或跳过安全检查。

**必须运行的测试**

- 按 `.github/workflows/infrastructure-gates.yml` 运行 Backend Ruff、Mypy、compileall 和非 integration tests。
- 对受影响 P34 migration 运行 fresh sentinel integration tests。
- 对前端改动运行 `pnpm test`、TypeScript 检查和 production build。
- 运行 `docker compose config`，确认 clean build 不依赖本机私有文件。
- 运行维护者地图 validator，并用临时未映射 `APIRouter` 做负向验证，确认错误
  明确列出 `unmapped discovered entrypoint`。

**失败恢复**

撤销不可复现的环境内改动，从源码重新应用最小修复并重建 clean 环境。若只有运行环境中的修改有效，应将其视为尚未修复：先提取可审计的源码差异和测试，再销毁临时环境重验；不得把临时容器或本机缓存发布为权威交付物。
