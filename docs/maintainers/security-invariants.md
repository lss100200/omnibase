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
- `backend/src/omnibase/tenants/migrations.py`
- `backend/src/omnibase/migrations/versions/0004_p34_1_control_plane_foundation.py`
- `backend/src/omnibase/migrations/versions/0005_p34_2_capability_ledger.py`
- `backend/src/omnibase/migrations/versions/0006_p34_3_controlled_data.py`
- `backend/src/omnibase/migrations/versions/0007_p34_4_workspace_control_plane.py`

**为何存在**

OmniBase 同时迁移 global registry 和 tenant schema。迁移若猜测 scope、把未知值当 global，可能在错误 schema 创建或删除安全表。`migration_schema_scope` 必须是显式闭集 `global | tenant`，缺失、大小写错误和未知值在 upgrade/downgrade 都必须中止。

**允许的改法**

- 通过 `env.py` 显式设置 scope，并在每个双 scope migration 中使用同一闭集验证。
- 在 fresh sentinel 数据库执行 global/tenant upgrade、downgrade、re-upgrade。
- 为新 scope 先设计全仓迁移契约和测试，再一次性扩展闭集。
- 新 Tenant 必须在创建 Tenant registry row 和物理 schema 的同一事务内，仅对该 registry 绑定 schema 执行 tenant-scope Alembic 到当前 head；任何 revision 失败必须回滚 registry row、schema 与 bootstrap DDL，不能留下低于当前 head 的可登录 Tenant。

**禁止的改法**

- 使用默认 scope、truthy 判断、未知值回退或从 schema 名猜 scope。
- 只在 upgrade 校验而让 downgrade 宽松执行。
- 对普通业务数据库试跑未验收 migration。
- 为通过测试跳过 Alembic revision、trigger 或约束验证。

**必须运行的测试**

- `backend/tests/test_migration_scope_fail_closed.py`
- `backend/tests/integration/test_phase_1_6_tenant_foundation.py`
- `backend/tests/test_p34_3_controlled_data_foundation.py`
- `backend/tests/integration/test_p34_1_control_plane_foundation.py`
- `backend/tests/integration/test_p34_2_capability_foundation.py`
- `backend/tests/integration/test_p34_3_controlled_data_foundation.py`
- `backend/tests/integration/test_p34_4_workspace_foundation.py`
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
- 仓库根目录的 Compose diagnostic/config/run/exec/up/logs/ps 命令必须显式传入 `--env-file .env.example`；若是 disposable overlay，必须使用其专用 Compose/env 文件。该规则防止 Compose 隐式读取并在 JSON/config 输出中展开根 `.env`。
- 让维护者地图验证器反向扫描可由 AST 无歧义识别的 FastAPI 组合入口：
  顶层 `APIRouter`/`FastAPI` 赋值、直接创建并返回 `FastAPI` 的顶层工厂，
  以及同文件对该工厂的顶层实例化。该 Gate 只证明这些 HTTP 入口已被某个
  module `entrypoints` 覆盖，不把所有 public function 或 route handler 当成
  架构入口。

**禁止的改法**

- 直接编辑 `.venv`、`site-packages`、`node_modules`、运行中容器或镜像层代替源码修复。
- 依赖未提交文件、用户 `.env`、本机绝对路径、预热缓存或手工数据库状态才能通过。
- 在仓库根运行裸 `docker compose config --format json`，或任何未显式指定安全 env file 的 Compose diagnostic/config/run/exec；不得把根 `.env` 展开到终端、日志、artifact 或维护证据。
- 只更新生成 SDK/OpenAPI/构建产物而不更新权威源码。
- 为通过 CI 删除测试、降低 Gate、增加全局 `ignore_missing_imports`/宽泛 ignore 或跳过安全检查。

**必须运行的测试**

- 按 `.github/workflows/infrastructure-gates.yml` 运行 Backend Ruff、Mypy、compileall 和非 integration tests。
- 对受影响 P34 migration 运行 fresh sentinel integration tests。
- 对前端改动运行 `pnpm test`、TypeScript 检查和 production build。
- 运行 `docker compose --env-file .env.example config`，确认 clean build 不依赖本机私有文件。
- 运行维护者地图 validator，并用临时未映射 `APIRouter` 做负向验证，确认错误
  明确列出 `unmapped discovered entrypoint`。

**失败恢复**

撤销不可复现的环境内改动，从源码重新应用最小修复并重建 clean 环境。若只有运行环境中的修改有效，应将其视为尚未修复：先提取可审计的源码差异和测试，再销毁临时环境重验；不得把临时容器或本机缓存发布为权威交付物。

## INV-011 workspace-scope-membership

**权威源码**

- `backend/src/omnibase/workspaces/models.py`
- `backend/src/omnibase/workspaces/service.py`
- `backend/src/omnibase/workspaces/router.py`
- `backend/src/omnibase/migrations/versions/0007_p34_4_workspace_control_plane.py`

**为何存在**

Tenant 只是根信任域，不是 Workspace 内容的通行证。Workspace 私有资源必须同时满足实时用户、Tenant、membership/RBAC、资源 scope 和显式 scope grant；同 Tenant、已知逻辑 UUID 或 tenant-admin 身份均不能自动得到另一个 Workspace 的私有资源访问权。

**允许的改法**

- 从实时 `TenantContext` 派生 actor，再以 `(tenant_id, workspace_id, user_id)` 查询 active membership。
- 收窄角色、动作或 scope；新增跨 scope 分享时使用结构化、限时、可撤销 grant。
- 在数据库增加 workspace/tenant 复合外键、部分唯一索引和闭集 CHECK。
- membership mutation 必须先锁 tenant-bound Workspace aggregate，再在锁内重新读取并锁定 actor/target membership，最后判断 active-owner 不变量；不得用事务外角色快照或未串行化的 owner count 决定写入。改变现有 owner 只能由当前 owner 执行，且不能留下零个 active owner。
- 模板注册仅允许实时 tenant admin；Browser dependency 只做早期拒绝，`register_template()` 还必须在同一 caller-owned transaction 锁定并重验 active tenant-admin User。模板、membership 与 scope-grant mutation 必须在该事务写脱敏 Audit。scope-grant action 当前只允许 `resource.read|resource.list`。
- 模板自然幂等使用 PostgreSQL `(tenant_id, template_key, version)` 唯一键与 `INSERT ... ON CONFLICT DO NOTHING`；只有 `template_spec/display_name/supersedes_template_id` 和 canonical digest 全部一致才 replay，同 key/version 的任何语义差异必须返回 conflict，不能靠捕获并吞掉 `IntegrityError`。

**禁止的改法**

- 信任 JWT、Header、请求体或 query 中的 tenant/workspace/role 声明。
- 把 tenant-admin、资源 owner 字段或知道 UUID 等同于 Workspace membership。
- 缺失 membership/scope binding 时回退到 tenant-wide allow。
- 在锁 Workspace aggregate 之前按旧 actor membership 执行成员写入，或让两个 owner 并发通过未锁定的 last-owner 检查。
- 在公共 DTO、错误或 Audit 中暴露物理 locator、宿主路径、provider handle 或凭据。

**必须运行的测试**

- `backend/tests/test_p34_4_workspace_service.py`
- `backend/tests/test_p34_4_api_contract.py`
- `backend/tests/integration/test_p34_4_workspace_foundation.py`

**失败恢复**

立即关闭 `/api/v1/workspaces*` 受影响写入口，撤销可疑 scope grant，并保留 append-only Audit。先在 fresh sentinel 中复现同 Tenant 跨 Workspace 场景并恢复复合约束；不得用 tenant-wide fallback 或删除 membership 历史止血。

## INV-012 run-generation-lifecycle

**权威源码**

- `backend/src/omnibase/workspaces/contracts.py`
- `backend/src/omnibase/workspaces/models.py`
- `backend/src/omnibase/workspaces/service.py`

**为何存在**

Workspace 是长期逻辑资源，Run 是短期、可销毁的执行意图。desired/observed state、generation、版本和幂等状态转换必须阻止重复启动、stale reconciler 覆盖新状态，以及把 Run 或 runtime identity 误当成长期权限事实。P34.4 reconciler 只允许推进元数据，不运行不可信代码。

**允许的改法**

- 使用命名 lifecycle action、expected version、caller-owned transaction 和幂等 replay。
- 新建 Workspace 必须默认 stopped；创建逻辑资源不能隐式启动 runtime、分配 lease 或调用 provider。
- 通过 `WorkspaceReconciler` 注入实现；生产默认保持 `UnavailableWorkspaceReconciler`，测试只使用 `FakeMetadataWorkspaceReconciler`。
- restore 创建新的 Workspace identity/generation，并只恢复安全模板、摘要和元数据引用。
- Run lease 必须绑定创建时的 Workspace generation、当前 Node fencing token 和当前仍有效的 Node attestation；Node 重新 fencing 后旧 Run lease 立即失效。
- Run 进入 `stopped|succeeded|failed|cancelled` 后必须关闭或撤销 lease，清除 `runtime_instance_id` 与 `workload_identity_digest`，并拒绝旧 holder 把 observed state 恢复到 starting/running。

**禁止的改法**

- 提供任意 `PATCH state`、忽略 generation/version 或让多个 active Run 绕过唯一约束。
- 允许 terminal Run 回到非终态，或让 stale holder 在终态后继续 heartbeat、提交结果或保留 runtime/workload identity。
- 把 Celery、普通 Docker 或 fake reconciler 描述为安全 Sandbox Runner。
- 在 Run/模板/snapshot 中保存 command、env、JWT、数据库 URL、宿主路径、进程、PID、socket、连接或 runtime/provider handle。

**必须运行的测试**

- `backend/tests/test_p34_4_workspace_service.py`
- `backend/tests/test_p34_4_api_contract.py`
- `backend/tests/integration/test_p34_4_workspace_foundation.py`

**失败恢复**

切回拒绝型 reconciler，停止新的 Run claim，将异常 Workspace 标为可审计的 failed/stopped 目标并撤销活跃 lease。不得手工降低 generation、复用旧 Run 身份或把 observed state 伪改为成功。

## INV-013 trusted-node-attestation

**权威源码**

- `backend/src/omnibase/workspaces/overlay.py`
- `backend/src/omnibase/workspaces/models.py`
- `backend/src/omnibase/migrations/versions/0007_p34_4_workspace_control_plane.py`

**为何存在**

成员 Node、Peer Grant、Service Advertisement 和 Network Lease 是内部受信 Node Daemon 控制事实，不是 Browser 身份。建立了加密连接、拥有 IP 或客户端提供 node ID 都不能证明授权；撤销 Node 后，其 peer、service、network 和 authority 能力必须立即失效。

**允许的改法**

- 只从受信 attestation 上下文注册节点，并保存 thumbprint/evidence digest 等非秘密证明；每次 Run/Network/Authority/Peer/Service 使用都必须按数据库时钟重新确认仍存在未过期的 verified attestation，不能只信 `WorkspaceNode.attestation_state` 快照。
- 让 `PeerOverlayProvider` 保持可替换；生产缺少真实 adapter 时使用拒绝型实现，测试只使用不打开 socket 的 fake/local provider。
- authority claim/commit、Peer Grant、Service Advertisement、Network Lease 与 Node revoke 的权威锁阶段统一先锁 Workspace，再按稳定 node ID 顺序锁当前 live-attested Node，最后锁 authority/peer/service/cursor/lease 等领域记录。Node revoke 还必须在同一调用方事务中提高 Node fencing 并级联撤销逻辑授权、Run lease 和 authority。
- `acquire_network_lease()` 只签发逻辑授权并从 `network_lease_cursors` 分配单调 fencing token；它不得调用真实或 fake provider 的 activate/revoke。`PeerOverlayProvider` 只是隔离的 P34.5 adapter 契约/fake harness，不是 P34.4 Network Lease 签发的一部分。

**禁止的改法**

- 在 Browser API 暴露 attestation、heartbeat、lease、fencing 或 provider activation。
- 保存私钥、原始敏感 attestation、IP/route、VPN credential 或 provider handle。
- 只检查 Node 行的 `verified` 字段而不复核 attestation expiry，或在签发逻辑 Network Lease 时产生 socket、route、VPN/Overlay peer 等副作用。
- 让 Sandbox 成为成员 Overlay peer，或把 Overlay membership 当成 Workspace RBAC。

**必须运行的测试**

- `backend/tests/test_p34_4_overlay_collaboration.py`
- `backend/tests/test_p34_4_api_contract.py`
- `backend/tests/integration/test_p34_4_workspace_foundation.py`

**失败恢复**

撤销受影响 Node 并级联关闭其 peer/service/network/authority 逻辑状态，保持真实 adapter 未装配或停用。不得通过重用旧 attestation、放宽到来源 IP 或跳过 Workspace 绑定恢复服务。

## INV-014 lease-expiry-binding

**权威源码**

- `backend/src/omnibase/workspaces/service.py`
- `backend/src/omnibase/workspaces/overlay.py`
- `backend/src/omnibase/workspaces/collaboration.py`
- `backend/src/omnibase/workspaces/models.py`

**为何存在**

Run、Network 和 Workspace authority lease 都是短期授权。每次 heartbeat、状态提交、网络使用或协作提交必须重新验证 tenant/workspace、主体、generation/epoch、状态、实时 attestation 和数据库时钟内的有效期。Run Lease 还绑定 Node fencing token；Network Lease 同时绑定 `network_lease_cursors.current_fencing_token`。持有旧 lease UUID 不能在 Node 重新 fencing、cursor 前进、到期或撤销后继续工作。

**允许的改法**

- 缩短 TTL、收紧续租窗口、使用数据库时钟并在锁内复核当前状态。
- 为每类 lease 维持最多一个 active holder 的部分唯一约束。
- Run claim 将当前 `WorkspaceNode.fencing_token` 固化到 `RunLease.node_fencing_token`；Network claim 在锁定 cursor 后分配并推进当前/下一 fencing token，使用时同时比较 lease 与 cursor。
- 将过期、撤销和 holder 不匹配转换为稳定、无敏感信息的拒绝。

**禁止的改法**

- 依赖调用方时钟、缓存中的 expiry 或异步最终撤销。
- heartbeat 时只按 lease ID 查询而省略 tenant/workspace/run/node/generation/epoch。
- Run heartbeat 忽略 `node_fencing_token`/实时 attestation，或 Network Lease 只比较 lease token 而不比较 cursor 当前 token。
- 到期后隐式续租、复活或复用同一 authority epoch。

**必须运行的测试**

- `backend/tests/test_p34_4_workspace_service.py`
- `backend/tests/test_p34_4_overlay_collaboration.py`
- `backend/tests/integration/test_p34_4_workspace_foundation.py`

**失败恢复**

停止新 claim/heartbeat，标记受影响 lease 为 revoked/expired，并保持下游提交 fail-closed；在 fresh sentinel 中验证 DB clock 和唯一 active 约束后再开放。不得延长旧 lease 伪造连续性。

## INV-015 monotonic-fencing

**权威源码**

- `backend/src/omnibase/workspaces/service.py`
- `backend/src/omnibase/workspaces/overlay.py`
- `backend/src/omnibase/workspaces/collaboration.py`
- `backend/src/omnibase/migrations/versions/0007_p34_4_workspace_control_plane.py`

**为何存在**

分布式 holder 可能在暂停、网络分区或进程恢复后继续提交。Run、Node/Network 和 authority 的 fencing token/epoch 必须单调增加，并与当前 generation、实时 attestation 和 active lease 原子校验。Run lease 固化 Node fencing；Network lease token 由持久化 cursor 分配。旧 holder 即使重新上线也不能覆盖新状态、复活 terminal Run 或制造双写。

**允许的改法**

- 在锁住权威 aggregate 后分配下一 token，并让数据库唯一约束拒绝重复 token。
- 所有提交同时携带并验证逻辑 ID、generation/epoch、lease、holder Node fencing 和当前 cursor/authority token。
- 对 stale token 返回冲突并写 code-only Audit/安全事件。

**禁止的改法**

- 重置、递减、复用或由客户端选择 fencing token/authority epoch。
- 从 `max(existing token)` 临时猜测 Network token，或删除 `network_lease_cursors` 后从 1 重新签发。
- 先执行副作用再验证 token，或只比较“较新时间戳”。
- restore 后沿用旧 token、holder 或 runtime identity。

**必须运行的测试**

- `backend/tests/test_p34_4_workspace_service.py`
- `backend/tests/test_p34_4_overlay_collaboration.py`
- `backend/tests/integration/test_p34_4_workspace_foundation.py`

**失败恢复**

冻结相关 mutation，撤销所有无法证明当前性的 holder，并以更高 token/epoch 创建新的显式 lease。不得修改历史 token、删除冲突记录或让两个 authority 临时并行写入。

## INV-016 authority-single-writer

**权威源码**

- `backend/src/omnibase/workspaces/collaboration.py`
- `backend/src/omnibase/workspaces/models.py`
- `backend/src/omnibase/migrations/versions/0007_p34_4_workspace_control_plane.py`

**为何存在**

P34.4D 的协作 harness 只验证内容摘要、Git ref 元数据和追加事件的单写协议。每个 Workspace 最多一个 active authority；authority 离线、lease 过期、摘要链或 sequence 冲突时必须只读/拒绝，不自动选举、自动 merge 或产生双写。

**允许的改法**

- 在 DB clock 确认旧 authority 过期后显式 claim 更高 epoch。
- 验证 `previous_digest`、单调 sequence、event type 和当前 authority lease。
- authority claim/commit 先锁 tenant-bound Workspace aggregate，再锁当前 live-attested authority Node，最后锁 authority/event chain；Node revoke 使用相同的 Workspace→Node 前缀，因此撤销和新 claim/commit 不能交叉穿越。
- fake/local transport 只保存合成元数据，不接触真实 Git credential、文件内容、业务数据库、MinIO、Redis 或 canonical RAG。

**禁止的改法**

- authority 离线时由多个 Node 乐观写入或无一致性协议自动接管。
- 对同 sequence 不同 digest、错误 previous digest 或旧 epoch 自动 last-write-wins。
- 将 P34.4D harness 接入真实成员网络、真实用户数据或不可信代码执行。

**必须运行的测试**

- `backend/tests/test_p34_4_overlay_collaboration.py`
- `backend/tests/integration/test_p34_4_workspace_foundation.py`

**失败恢复**

将 Workspace 协作面切为只读，保留冲突事件与摘要证据，撤销现有 authority 后由人工选择可信链并以新 epoch 恢复。不得删除冲突、改写旧摘要或自动合并两条权威链。

## INV-017 sandbox-default-deny

**权威源码**

- `backend/src/omnibase/sandbox/contracts.py`
- `backend/src/omnibase/sandbox/authorization.py`
- `backend/src/omnibase/sandbox/control.py`
- `backend/src/omnibase/sandbox/coordinator.py`
- `backend/src/omnibase/sandbox/host.py`
- `backend/src/omnibase/sandbox/operations.py`
- `backend/src/omnibase/sandbox/models.py`
- `backend/src/omnibase/sandbox/persistence.py`
- `backend/src/omnibase/sandbox/provider.py`
- `backend/src/omnibase/sandbox/runner.py`
- `backend/src/omnibase/sandbox/transport.py`
- `backend/src/omnibase/workspaces/service.py`
- `scripts/sandbox/probe_runner_host.py`
- `backend/tests/test_p34_5_sandbox_foundation.py`
- `backend/tests/test_p34_5_sandbox_a1_control.py`
- `backend/tests/test_p34_5_sandbox_a2_dispatch.py`
- `backend/tests/test_p34_5_sandbox_a3_persistence.py`
- `backend/tests/integration/test_p34_5_sandbox_persistence.py`

**为何存在**

P34.4 Run Lease、Node fencing 和实时 attestation 只提供控制面授权事实，不能自行证明某个运行时可以安全执行代码。Sandbox 每次普通操作都必须重新绑定 tenant、Workspace、Run、runtime instance、Node、Lease、Workspace generation、Run/Node fencing、workload identity、action、有效期和在线 capability 状态；原始 UUID、provider handle、调用方声明或已经创建的 runtime 都不是持续授权。P34.2 read profile 与 Sandbox lifecycle profile 必须互斥；Sandbox Grant 必须短期、不可委派、绑定单一 Workspace/runtime/workload identity，并且不得签发为 Gateway bearer token。紧急 stop/destroy 必须使用独立可信 controller authorization，不能依赖已经撤销的 workload grant，也不能因为 workload 已撤销就匿名放行。任何副作用前还必须存在 operation-idempotent capability budget reservation、durable operation/transition/Audit、当前 Runner host/profile 证明和独立 transport；exact replay 不得重复扣费，binding drift 必须拒绝，dispatch 结果不确定时禁止自动重放。缺少可信 verifier/store/provider/controller/host/transport/Runner 时必须拒绝。A0-A3 的本地 harness 只能演练授权、状态机与调度顺序；A4 即使具备真实 Linux Runner seam，也必须由目标宿主攻击 Gate 决定是否可装配，源码存在不能替代 cgroup/namespace/seccomp/LSM 和有界强杀证据。

**允许的改法**

- 收紧 `SandboxOperationRequest`、资源配额、相对路径、结构化 argv、只读 root、non-root、`no_new_privileges`、capability drop 和默认拒绝网络契约。
- 新增真实 provider 前先保持 `RejectingSandboxAuthorizer` 与 `UnavailableSandboxProvider` 为默认，并以显式可信 wiring 注入实现。
- 使用 `ComposedSandboxAuthorizer` 组合独立的 live P34.4 lease/Node/fencing verifier 与 P34.2 capability verifier；任一结果缺失、过期或 binding 不一致都拒绝。
- 使用 `SqlAlchemySandboxLeaseVerifier` 在每次操作的新数据库事务中调用 `verify_run_lease_for_sandbox()`，同时重验 Run Lease、实时 Node attestation、Workspace generation、Run/Node fencing、runtime instance 与 workload identity；禁止缓存上一次接受结果。
- 使用 `create_sandbox_grant()` 只创建 `sandbox.prepare/create/start/exec/cancel/logs/stats/snapshot/restore/stop/destroy` 闭集权限；Grant 必须单 Workspace、runtime/workload-bound、不可委派、最长五分钟，并由 `SqlAlchemySandboxCapabilityVerifier` 每次新事务在线复核。
- 使用 `verify_and_reserve_sandbox_capability()` 按 operation ID 追加 `capability_usage_reservations`；exact replay 返回稳定 evidence 且只扣一次 calls/cost，operation 与 tenant/grant/workspace/runtime/action 任一漂移都拒绝。
- 让 emergency stop/destroy 使用 `SandboxControlRequest`、独立 `SandboxControlAuthorizer`、controller identity、deadline、runtime handle、generation 与 Run/Node fencing；默认 `RejectingSandboxControlAuthorizer`。
- 在任何 Runner/provider 副作用前按 operation ID 与 request/spec digest 预留 durable operation；exact replay 返回原记录，payload drift 冲突，ambiguous outcome 进入 reconciliation-required 且不得自动重放，terminal operation 不可复活。
- production 使用 `SqlAlchemySandboxOperationStore`，让 current pointer、append-only transition 与 redacted Audit 在同一事务提交；Workspace/Run/Grant 必须由复合 tenant 外键约束，transition/reservation 的 UPDATE/DELETE 必须由数据库 trigger 拒绝。
- 使用 `SandboxExecutionCoordinator` 固定 operation reservation → live authorization → Runner host attestation → dispatch marker → independent transport → receipt binding 的顺序；宿主 fencing/profile/evidence 不一致或 receipt operation ID 漂移必须 fail-closed。
- 保持 `UnavailableSandboxRunner` 为生产默认；Runner isolation profile 只有在目标 Linux 上真实证明 cgroup v2、user/PID/mount/network namespace、seccomp、LSM 和有界强杀后才能装配。
- 使用 test-only `InMemorySandboxAuthorizer` 和 `FakeInMemorySandboxProvider` 验证完整 binding、过期、撤销、stale fencing、状态转换、provider-owned snapshot provenance 和 restore-new-identity；同一 Run 即使 runtime 已销毁也不得重新 create/restore，`exec`/`cancel` 必须继续返回 `sandbox_execution_not_unlocked`。
- 将未来 Linux provider 放在独立 Runner 进程/节点，且每次 mutation 在副作用前完成在线 lease/capability verification。

**禁止的改法**

- 把 Browser JWT、请求体字段、raw capability token、来源 IP、provider handle 或“runtime 已存在”当作授权事实。
- 省略 `runtime_instance_id`、复用旧 runtime identity、缓存 verifier 结果、混合 read 与 Sandbox action profile、委派 Sandbox Grant，或把 Sandbox Grant 签发成 Gateway bearer token。
- 在 exact replay 时重复扣 capability budget、删除/更新 reservation/transition、用相同 operation ID 搭配不同 tenant/grant/workspace/runtime/action，或将 Sandbox operation 写到不属于同一 tenant/Workspace 的 Run。
- 使用 workload capability 代替 emergency controller authorization，或在没有可信 controller identity/current fencing/deadline 时执行 stop/destroy。
- 对 ambiguous provider outcome 自动重跑、复用同一 operation ID 搭配不同 request/spec，或让 terminal operation/runtime 回到 dispatching/running。
- 在 Core API/Celery 进程中执行 workspace 命令，或给 Sandbox/Runner 注入 PostgreSQL、MinIO、Redis、JWT、签名 key、宿主 `.env`、Docker/Podman socket、宿主目录或成员 Overlay identity。
- 在 A0 fake/provider 中调用 Docker、subprocess、shell、socket、外部 HTTP、文件系统或真实 runtime/network provider，并将 metadata-only 状态误报为代码已执行。
- 允许绝对路径、Windows drive、`..`、symlink 逃逸、shell command string、任意 env、host mount、device、runtime socket、非 deny-all 网络或无界 CPU/内存/PID/磁盘/inode/输出/时间。
- restore 复用旧 Run、generation、workload identity、token、进程、PID、socket、连接或 runtime handle。
- 接受调用方伪造或篡改的 snapshot metadata，或让已产生过 runtime 的 terminal Run 重新 create/restore。

**必须运行的测试**

- `backend/tests/test_p34_5_sandbox_foundation.py`
- `backend/tests/test_p34_5_sandbox_a1_control.py`
- `backend/tests/test_p34_5_sandbox_a2_dispatch.py`
- `backend/tests/test_p34_5_sandbox_a3_persistence.py`
- `backend/tests/test_p34_5_sandbox_a4_runtime.py`
- `backend/tests/test_p34_5_sandbox_a4_transport.py`
- `backend/tests/integration/test_p34_5_sandbox_persistence.py`（仅 guarded disposable `omnibase_test_*` sentinel PostgreSQL）
- `backend/tests/test_p34_4_workspace_service.py`
- `docker compose --env-file .env.example run --rm --no-deps backend mypy src/omnibase/sandbox src/omnibase/workspaces`
- `python scripts/sandbox/probe_runner_host.py`；结果不 ready 时不得通过降低 profile 或省略控制强行装配 provider。
- 对新增真实 provider 运行 P34.5 `RUN-03/04/05`、`FS-01/02/03`、`NET-01/02`、`PROC-01/02`、`HOST-01` 与 `CROSS-01` 攻击矩阵；`RUN-05` 必须证明非法或 root-like workload UID/GID 被拒绝，并且合法请求实际以请求的 non-root UID/GID、空 supplementary groups、精确单项 uid/gid map 与 `setgroups=deny` 执行。A0 单元测试不能替代目标 Linux isolation Gate。

**失败恢复**

立即撤下真实 provider/Runner wiring，恢复 `UnavailableSandboxProvider`、`UnavailableSandboxRunner` 与全部 rejecting authorizer/verifier，撤销受影响 Run Lease/capability/workload identity，并通过独立可信控制通道停止对应 Runner。保留 operation transition、runtime、lease、fencing、审计和 provider 证据；ambiguous outcome 只允许 reconciliation，不允许猜测重放。无法证明副作用前完成在线验证时一律视为未授权。不得通过放宽路径、网络、资源或身份检查恢复服务，也不得把普通 Docker smoke 当作敌对代码隔离证明。

## INV-018 attested-linux-runtime

**权威源码**

- `backend/src/omnibase/sandbox/dispatch_digest.py`
- `backend/src/omnibase/sandbox/runner.py`
- `backend/src/omnibase/sandbox/runner_service.py`
- `backend/src/omnibase/sandbox/runtime_driver.py`
- `backend/src/omnibase/sandbox/runtime_probe.py`
- `backend/src/omnibase/sandbox/transport_auth.py` (`TrustedRunnerMtlsPeer`, `MtlsRunnerTransportAuthenticator`, `SqliteRunnerReplayStore`)
- `backend/src/omnibase/sandbox/transport_service.py`
- `backend/tests/test_p34_5_sandbox_deployment_launcher.py`
- `scripts/sandbox/probe_linux_runtime.py`
- `scripts/sandbox/run_a4_attack_matrix.py`
- `deployment/sandbox/**`
- `docs/evidence/p34-5/linux-runner-attack-gate.json`

**为何存在**

“命令在 Linux 上运行”不等于“命令被安全隔离”。Runner 必须在副作用前证明受信 launcher、受信 runner root、cgroup v2、独立 user/PID/mount/network namespace、seccomp、LSM、身份映射和 profile digest，并把这些证据与 operation、Run/runtime、Runner/Node 和 fencing 绑定。Coordinator 与 Runner 必须使用同一个 canonical execution-binding digest；非零退出、截断、超时、输出溢出、receipt 漂移或无法证明 cgroup 已空都不能写成成功。杀死 launcher PID 也不能代替杀死整个 operation cgroup。

**允许的改法**

- 在纯 Core `dispatch_digest.py` 中维护 canonical request/spec/execution binding，Runner 侧只能委托或证明完全一致。
- 通过固定路径、固定摘要、无 shell 的 launcher 接收结构化 JSON；stdin 写入和 stdout/stderr 捕获必须受 deadline 与字节上限约束。
- production Runner transport 只接受可信 mTLS ingress 注入的 `TrustedRunnerMtlsPeer`，并将证书指纹、Runner/Node identity、Node fencing、有效期与 envelope binding 一起验证；peer certificate thumbprint 必须与 `VerifiedRunnerHost.runner_identity_thumbprint` 精确一致，不能只比较 `runner_id`/`node_id`；普通 Header、来源 IP 或调用方对象不能替代 mTLS peer evidence。
- 使用私有显式路径的 `SqliteRunnerReplayStore` 持久拒绝 nonce 与 sequence replay；父目录/文件必须通过 owner、mode、regular-file 和 no-symlink 检查，进程重启不能清空 replay 边界。
- 每个 operation 使用独立 cgroup；超时、输出溢出、spawn/pipe/selector/communicate/metadata/evidence 任一异常都先写该 operation 的 `cgroup.kill`，等待 `cgroup.events` 明确 `populated 0`，随后才清理 launcher process group、cgroup 与本次 runtime 目录。无法证明为空时必须保留现场并 fail-closed。
- 使用真实、可验证的非 root user-namespace UID/GID 映射与 namespace inode 作为 user namespace 证据。允许 dedicated Runner 的单项 outer service UID/GID → 请求的 non-root inner UID/GID 映射，或经目标宿主 Gate 证明的 subordinate range；两种方案都必须在 workload `exec` 前精确核对 real/effective/saved UID/GID、supplementary groups、`uid_map`、`gid_map` 与 `setgroups=deny`，不得接受调用方绑定的身份却静默运行成 namespace root，也不得因为宿主策略禁止裸 `unshare -Ur` 就忽略真实映射。
- host namespace reference 只有两种可信形式：直接 VM 上的 `/proc/1/ns/{user,pid,mnt,net}` namespace symlink handle，或 `/run/omnibase-host-ns/{user,pid,mnt,net}` 下由 root 拥有、非 group/world-writable 的 regular snapshot，且内容必须严格为对应 host namespace 的 `device:inode`。runtime probe 比较的是 namespace identity，不是 snapshot 普通文件自身的 inode。
- 目标 Linux profile 可使用 AppArmor 或等价受支持 LSM，但必须在目标节点证明 loaded/enforced，而不是只读取配置文件名。

**禁止的改法**

- 在 Core API、Celery、Browser 进程或普通宿主 shell 中直接执行 workspace command。
- 用 PID kill、超时返回、进程退出或 `docker stop` 文本代替整个 cgroup 已终止的证据。
- 对缺失 cgroup、namespace、seccomp、LSM、launcher digest、runner-root digest、Node fencing 或 receipt binding 的宿主降级通过。
- 把任意普通文件的 inode 当作 host namespace identity，接受非 root-owned/可写 snapshot、非严格 `dev:ino` 内容，或让 runtime 与 host namespace 相同时继续执行。
- 在 production 使用 `HmacRunnerTransportAuthenticator` 或 `InMemoryRunnerReplayStore`，把普通请求 Header 转成 trusted peer，只绑定 `runner_id`/`node_id` 而不核对 host identity thumbprint，使用共享/可写/symlink SQLite 路径，或在进程重启后接受旧 nonce/sequence。
- 把 Docker Desktop、WSL、rootful 容器或共享宿主的成功 smoke 自动描述为 production hostile-code isolation。
- 让 Runner 持有数据库、Redis、MinIO、JWT、签名私钥、宿主 `.env`、容器 socket、宿主工作区或成员 Overlay credential。

**必须运行的测试**

- `backend/tests/test_p34_5_sandbox_a2_dispatch.py`
- `backend/tests/test_p34_5_sandbox_a4_runtime.py`
- `backend/tests/test_p34_5_sandbox_a4_transport.py`
- `backend/tests/test_p34_5_sandbox_deployment_launcher.py`
- `python scripts/sandbox/probe_linux_runtime.py --config <target-host-config>`
- `python scripts/sandbox/run_a4_attack_matrix.py <target-host-config>`；只有真实目标宿主产生的 artifact 才能作为攻击 Gate 证据。

**失败恢复**

撤下 `AttestedLinuxSandboxRunner`/RuntimeDriver wiring，恢复 unavailable transport/Runner，撤销相关 Run Lease 与 workload identity，并通过独立控制通道按 operation cgroup 停止遗留 workload。保留 attestation、receipt、cgroup、stderr/stdout 摘要和 ambiguous operation 历史。无法证明 cgroup 为空时按仍有敌对进程处理，不能标记成功或自动重放。

## INV-019 logical-service-network

**权威源码**

- `backend/src/omnibase/sandbox/network.py`
- `backend/src/omnibase/sandbox/broker.py`
- `backend/src/omnibase/sandbox/network_ledger.py`
- `backend/src/omnibase/sandbox/network_runtime.py`
- `backend/src/omnibase/sandbox/overlay_publication.py`
- `backend/tests/test_p34_5_sandbox_network_broker.py`
- `backend/tests/test_p34_5_sandbox_network_durable.py`
- `backend/tests/test_p34_5_sandbox_network_runtime.py`
- `backend/tests/test_p34_5_overlay_adapter.py`
- `backend/tests/test_p34_5_network_broker_daemon.py`
- `deployment/network-broker/**`
- `scripts/network-broker/**`
- `docs/evidence/p34-5/network-broker-attack-gate.{json,md}`

**为何存在**

Sandbox 需要使用 Workspace 服务或只读 Gateway，不代表它可以加入成员 Overlay、解析任意地址或获得公网/LAN 通行证。网络授权必须绑定 Tenant、Workspace、Run、runtime、workload identity、Run/Node/Network fencing、Network Lease、logical service、协议/端口、deadline 和预算。Broker 在独立 network namespace 证据下把 logical service 解析为 server-owned destination，并在连接前再次解析以拒绝 DNS rebinding；物理地址、路由、provider handle 和凭据都不能进入 Sandbox DTO。

**允许的改法**

- 扩展逻辑服务类型、协议或预算时，同时更新绑定 digest、resolver、policy、receipt 和负向测试。
- 使用 operation-idempotent durable ledger 预留 connection/bytes 预算；exact committed replay 不重复扣费或再次调用 transport。
- durable reservation 的状态只能从 `pending` 单向进入 `committed` 或 `unknown`；`pending/unknown` 都继续占用预算并阻止自动重放。Ledger 必须使用显式绝对私有路径、原子 `O_EXCL/O_NOFOLLOW` 创建、`BEGIN IMMEDIATE` aggregate CAS、不可 DELETE/改写的 SQLite trigger，且它不是业务数据库。
- 在授权后和连接前分别解析，并对两次目的地分类和 resolution digest 做一致性检查。
- production namespace attestor 只读取可信 daemon 私有目录中的当前 runtime 证据，使用 `O_NOFOLLOW`/`fstat` 防路径替换，绑定 Runner/namespace ID、可信 PID/starttime、live `/proc/<pid>/ns/net` `dev:ino`、全部 Run/Node/Network fencing 与 workload identity，并在 durable reservation/transport 前重新验证。host reference 只允许精确 `/proc/1/ns/net` 或 root-owned private `/run/omnibase-host-ns/net` strict `dev:ino` snapshot。
- 最小本地 Broker transport 只连接显式私有 `AF_UNIX` socket；daemon 必须使用与调用方不同的专用 UID/GID，连接前后 socket `dev:ino` 必须连续，Linux `SO_PEERCRED` PID/UID/GID 与 PID starttime 必须稳定，并通过独立 pinned key 的 nonce challenge-response 证明应用层 daemon identity。任何公网或成员 Overlay route 仍由 policy 在 transport 前拒绝。
- 独立 Linux Broker daemon 必须从 `PrivateNetwork=yes` 的空网络 namespace 启动，只允许短生命周期 worker 在重新打开并验证 live `/proc/<pid>/ns/net`、PID starttime、positive `dev:ino`、非 host namespace 与最长五分钟 root-owned permit 后执行一次 `setns` 和 TCP connect。任何网络副作用前先以 durable `O_EXCL` marker 消费 operation；receipt 只能报告实际 socket connection/bytes，并绑定 challenge、operation 与 plan digest。
- Overlay adapter 只能发布 `OverlayLogicalServicePublication`，再经 verified mapper 生成 Broker 的 `LogicalNetworkService`。

**禁止的改法**

- 允许 Sandbox 直接访问 loopback、metadata、link-local、RFC1918、IPv6 ULA、multicast/reserved、宿主 LAN、成员私网、管理端口或任意公网。
- 让 Sandbox 传入 IP、URL、route、DNS answer、provider handle、credential reference 或 member identity。
- 用 allowlist 域名跳过解析后 IP 检查，跟随重定向进入私网，或在两次解析漂移时继续连接。
- 在缺失 authorizer/resolver/namespace attestor/budget ledger/transport 时默认开放，或把 test-only in-memory ledger 当成 production durable evidence。
- 在 transport 已可能接收请求后删除 pending charge、把 commit 异常当作未发生、从 `unknown` 回退到 pending，或在没有人工 reconciliation 的情况下再次连接。
- 用普通文件 path、可写 evidence、任意 `/proc/*/ns/net`、非 root snapshot、调用方声明的 namespace UUID、同 UID 近似、source IP 或 Header 代替 live namespace identity、专用 daemon UID、`SO_PEERCRED` 与 pinned-key challenge。
- 让 Broker supervisor 自身拥有宿主网络、让 Sandbox 直接持有 daemon key/permit/AF_UNIX socket、在 consumed marker 后自动重放，或用普通 Docker bridge 的成功连接代替独立 Linux Runner 的真实 `setns`/default-deny attack Gate。

**必须运行的测试**

- `backend/tests/test_p34_5_sandbox_network_broker.py`
- `backend/tests/test_p34_5_sandbox_network_durable.py`
- `backend/tests/test_p34_5_sandbox_network_runtime.py`
- `backend/tests/test_p34_5_overlay_adapter.py`
- `backend/tests/test_p34_5_network_broker_daemon.py`
- 独立 Linux Runner Network Broker Gate：真实 namespace-only connect、direct public/host deny、public/member/address-class deny、connection/bytes budget、challenge forgery、stale PID/starttime/netns、host/cross-runtime、socket impersonation/continuity、durable replay 与 cleanup。
- Threat matrix `RUN-03/04/05`、`NET-01/02` 与 `CROSS-01`。

**失败恢复**

恢复全部 rejecting/unavailable Broker 组件，停止独立 daemon，撤销受影响 Network Lease、logical service publication 与 workload identity，并阻断对应 network namespace。保留 budget reservation、daemon consumed marker、resolution、namespace 和 receipt 证据；目的地或结果不明确时不自动重试。daemon、systemd、permit、namespace、transport 或 Gate 脚本哈希变化后必须在独立 Linux Runner 重跑攻击 Gate，普通 Docker/WSL 结果不能替代。

## INV-020 overlay-adapter-binding

**权威源码**

- `backend/src/omnibase/workspaces/overlay_adapters/contracts.py`
- `backend/src/omnibase/workspaces/overlay_adapters/headscale.py`
- `backend/src/omnibase/workspaces/overlay_adapters/ledger.py`
- `backend/src/omnibase/workspaces/overlay_adapters/transport.py`
- `backend/src/omnibase/sandbox/overlay_publication.py`
- `backend/tests/test_p34_5_overlay_adapter.py`
- `backend/tests/test_p34_5_overlay_ledger.py`
- `backend/tests/test_p34_5_overlay_disposable_gate.py`
- `scripts/overlay/**`
- `deployment/overlay/**`
- `docs/evidence/p34-5/overlay-disposable-gate.json`

**为何存在**

Overlay 是成员受信 Node Daemon 之间的基础设施，不是 Workspace 或 Sandbox 的授权事实。每次 activate/rotate/revoke/status 必须在线绑定 Tenant、Workspace、Peer Grant、Service Advertisement、Network Lease、双 Node attestation、Workspace/service generation、Peer/Network/双 Node fencing 和 credential generation。Sandbox subject 与 direct endpoint publication 在 intent 构造阶段即拒绝；最终只向 Broker 发布不含地址、route、provider handle 或凭据的 logical service。

**允许的改法**

- 在 provider-neutral 契约后新增 adapter；provider 特有 key、地址和命令只留在受信 Node Daemon transport 内。
- production mutation 使用可跨进程重启的 `OverlayOperationLedger`；reserve 必须发生在 credential issuance 和 daemon transport 之前。
- credential 只通过短期 opaque reference 传递，generation 必须严格高于 live generation。
- daemon status/receipt 必须重新核对完整 binding；active publication 必须绑定 live binding、active receipt、fencing、generation、expiry 和 digest。

**禁止的改法**

- 将 Sandbox 注册为 Overlay peer，向 Sandbox 发布直接 endpoint，或把成员设备身份复制给 workload。
- 持久化或返回 raw Headscale/Tailscale auth key、Overlay IP/route、provider handle、Node Daemon credential 或 URL 内嵌 secret。
- 对已经可能跨过 transport boundary 的 mutation 自动重放，或用相同 operation ID 搭配不同 action/binding。
- 重置 credential generation、Peer/Network/Node fencing，或把 `InMemoryOverlayOperationLedger` 描述为生产 durability。

**必须运行的测试**

- `backend/tests/test_p34_5_overlay_adapter.py`
- `backend/tests/test_p34_5_overlay_ledger.py`
- `backend/tests/test_p34_5_overlay_disposable_gate.py`
- `backend/tests/test_p34_4_overlay_collaboration.py`
- P34.5 工程 Gate 必须使用真实 disposable Headscale control plane，并通过 mTLS Node Daemon 对真实 provider record 完成 activate/status/rotate/revoke、ambiguous no-replay、离线/重连、凭据 containment 与完整清理；单元测试或只修改 test-double 本地状态不能代替该 Gate。
- disposable Gate 必须从 public clean checkout 构建专用 Runner，source manifest 必须覆盖 `.gitattributes`、锁文件、Dockerfiles、完整复制的源码/测试和 Gate wrapper，并拒绝 ambient backend image、ambient virtualenv、symlink build input 与 dirty scored checkout。历史证据验证只允许后续 evidence/docs commit 改变 Git HEAD，不能容忍任何已封存 source byte 漂移。
- P34.7 production Gate 继续要求 hardened production Node Daemon、两个真实成员节点的数据面、强制 DERP relay/故障恢复，以及节点失陷、真实 node revoke 与 credential theft 攻击矩阵。P34.5 的 provider control-plane Gate 不得冒充这些生产数据面证据。

**失败恢复**

恢复 rejecting verifier/attestor/credential issuer/ledger/transport/mapper，撤销 Peer Grant、Service Advertisement、Network Lease 和短期 credential reference。对 ambiguous mutation 先读可信 daemon status 并人工 reconciliation，不删除 ledger reservation，也不使用同一 operation ID 猜测重放。

## INV-021 gateway-workload-identity

**权威源码**

- `backend/src/omnibase/capability_gateway/workload.py`
- `backend/src/omnibase/capability_gateway/app.py`
- `backend/src/omnibase/capability_gateway/mtls_ingress.py`
- `backend/src/omnibase/capability_gateway/server.py`
- `backend/src/omnibase/capability_gateway/thumbprints.py`
- `backend/src/omnibase/capability_gateway/security.py`
- `backend/src/omnibase/capabilities/service.py`
- `backend/src/omnibase/workspaces/service.py`
- `backend/tests/test_p34_5_gateway_workload.py`
- `backend/tests/test_p34_5_gateway_mtls_ingress.py`
- `backend/tests/integration/test_p34_5_gateway_mtls_split_disposable.py`
- `scripts/gateway/run_p34_5d_disposable_gate.py`
- `deployment/gateway/compose.disposable.yml`
- `docs/evidence/p34-5/gateway-mtls-disposable-gate.{json,md}`

**为何存在**

Gateway bearer token 只代表 P34.2 read capability，不能证明发起请求的当前 Sandbox runtime 仍受授权。可信 Runner/Broker mTLS transport 必须在 ASGI scope 注入不可由普通 Header 构造的 peer evidence；Gateway 每次请求都重新读取 live P34.4 Run Lease、Node attestation、Workspace generation、Run/Node fencing、runtime 与证书指纹。Core 只有在完整 binding 当前有效后才能签发最长五分钟且不晚于 Lease expiry 的 read token；Runner/Sandbox 永不持有签名私钥。

**允许的改法**

- 支持可信 Runner 或 Network Broker peer kind，但必须由 server-owned mTLS ingress 构造 `TrustedGatewayPeerEvidence`。
- credential-vending 请求体必须为空；grant、key、issuer、tenant、Workspace、Run、Runtime、Node、Lease、generation、fencing 与 originating user 只能来自 server-owned registry 和 live attestation，不能由调用方提交。
- 复用 P34.2 `CoreCapabilityVerifier`、只读 PostgreSQL/RAG adapter 和 append-only Audit；保持 Gateway 与 Browser ASGI 分离。
- 缩短 token TTL、加强证书轮换和 revocation，但不能缓存 live lease acceptance 跨请求；TTL 必须同时不晚于五分钟、peer evidence expiry 与 Run Lease expiry。
- disposable split-process Gate 必须从 public clean checkout 构建独立 Gateway 与最小 stdlib broker client 镜像；source manifest 必须覆盖 `.gitattributes`、`pyproject.toml`、`uv.lock`、完整复制的 `backend/src`/`backend/tests`、Dockerfiles、Compose、wrapper 与 client。不得依赖 ambient backend image、外部 venv volume 或 host source mount，且 Windows clean checkout 中 Linux init `.sh` 必须由 Git 属性和 Gate validator 双重证明为 LF-only。
- token 和私钥字段必须 redacted/repr-safe，错误与 Audit 不得包含原始凭据或物理 locator。

**禁止的改法**

- 从 HTTP Header、cookie、source IP、opaque runtime ID 或 bearer token 自身构造 trusted peer evidence。
- 在 live lease/Node/fencing 复核前加载私钥或签发 token，或让 TTL 超过五分钟/Run Lease expiry。
- 给 Runner/Sandbox 数据库 session、连接串、Redis/MinIO credential、JWT/signing private key 或 direct infrastructure route。
- 把 Gateway 挂入 Browser app，接受 Browser JWT 代替 workload mTLS，或开放 Runtime write capability。
- 让持有数据库/签名私钥的 Gateway 进程同时充当受限 workload client，或把 Backend 源码、数据库/Redis/MinIO/JWT 配置、signing key、server secret、宿主 mount、容器 socket交给 broker-client。

**必须运行的测试**

- `backend/tests/test_p34_5_gateway_workload.py`
- `backend/tests/test_p34_5_gateway_mtls_ingress.py`
- `backend/tests/test_p34_2_gateway_api.py`
- `backend/tests/test_p34_2_gateway_query.py`
- `backend/tests/integration/test_p34_5_gateway_mtls_split_disposable.py`（仅 guarded disposable `omnibase_test_*` sentinel PostgreSQL）
- `scripts/gateway/run_p34_5d_disposable_gate.py`；必须验证独立 server/client、真实 TLS handshake、parameter-free credential vending、四项只读调用、cross-tenant/stale/revocation/certificate/TLS 负例与 containers/networks/volumes 全清理。

**失败恢复**

恢复 rejecting workload attestor、capability verifier、credential issuer 和 private-key provider，撤销短期 token/Run Lease/workload certificate，并保留 code-only denied/error Audit。无法证明 mTLS scope 或 live lease 当前性时返回不可用/拒绝，不能回退到 Browser cookie 或静态 service secret。

## INV-022 canonical-derived-storage-boundary

**权威源代码**

- `backend/src/omnibase/workspace_data/**`
- `backend/src/omnibase/capability_gateway/write_adapters.py`
- `backend/src/omnibase/capability_gateway/write_service.py`
- `backend/src/omnibase/migrations/versions/0009_p34_6_workspace_data.py`

**为何存在**

Workspace private/derived 数据允许被受约束 workload 创建，但它不能因此获得 canonical 写权限。Artifact、derived index、canonical RAG 和物理对象定位必须处于不同 policy、表、adapter 与 storage namespace；derived build、读取、publication 和 restore 的任何路径都不得更新 canonical `documents`、`embeddings`、`embeddings_v2` 或 index metadata。物理 schema、table、bucket、object key、presigned URL 和 provider receipt 只存在于 server-owned adapter/storage binding，不能进入 DTO、SDK、Audit、日志或错误。

**允许的改法**

- 为 Workspace private/derived 增加新的逻辑 kind、不可变 generation、typed internal storage binding 和受控 adapter。
- Artifact 或 derived output 的“修改”创建新 Resource，并追加 `derived_from`/`transformed_from` lineage。
- 在数据库、adapter 和测试中增加更严格的 canonical read-only trigger、role privilege 和 before/after digest 证明。

**禁止的改法**

- 复用或写入 canonical 表、index state 或 locator namespace保存 derived output。
- 允许任何 existing Resource 原地进入或离开 `canonical_readonly`/`workspace_derived`。
- 让 Browser JWT、read token、Sandbox lifecycle grant 或 caller-supplied locator 调用 workload write adapter。
- 将 physical locator、对象存储凭据、正文、embedding/vector 或任意 SQL 暴露到公共契约。

**必须运行的测试**

- P34.6 Workspace Data、Gateway write、derived RAG focused tests。
- OpenAPI/Python/TypeScript SDK locator 泄漏扫描。
- guarded sentinel PostgreSQL 中的 canonical UPDATE/DELETE/TRUNCATE/policy flip 和 derived-before/after canonical digest 测试。

**失败恢复**

立即撤下 Workspace write/derived adapter，恢复 unavailable/rejecting composition，撤销相关 workload-data Grant，并保留 effect、operation、idempotency、Audit 和 lineage 证据。不能通过删除 derived 记录、重写 canonical 行、暴露 locator 或让 Sandbox 直连存储止血。

## INV-023 promotion-approval-atomicity

**权威源代码**

- `backend/src/omnibase/workspace_data/models.py`
- `backend/src/omnibase/workspace_data/service.py`
- `backend/src/omnibase/control_plane/service.py`
- `backend/src/omnibase/migrations/versions/0009_p34_6_workspace_data.py`

**为何存在**

Promotion 是把 Workspace 私有/派生资源复制到更广共享范围的高风险操作，不是资源标签修改。它必须精确绑定 tenant、Workspace、source Resource/version/digest、target scope、request hash、Operation、Approval、Grant 与 Idempotency；requester 不能自批。P34.6 只允许创建新的 `controlled_shared` target Resource 和 `published_from` lineage，不允许直接创建或修改 `canonical_readonly`，也不能把 source 原地改为 shared/canonical。

**允许的改法**

- 收紧 risk、审批角色、source/target closed set、TTL、digest、quota 或 reconciliation 规则。
- 在同一数据库事务中发布 target visibility、lineage、Operation/Idempotency、quota commit 与 success Audit。
- 对对象存储 copy 使用 `pending -> committed|failed|unknown` effect，provider boundary 后结果不明确时等待显式 reconciliation。

**禁止的改法**

- 将 promotion action 放进 runtime bearer token 或 Gateway workload route。
- 无审批、过期/已消费审批、requester self-approval、stale membership/generation/version/digest 时继续执行。
- 原地修改 source policy/locator/version，复用已有 target，或在 Audit/lineage失败后仍让 target 可见。
- 从 `unknown` 自动重放 provider mutation、删除 reservation/evidence 后伪造 fresh attempt。

**必须运行的测试**

- self-approval、非管理员、过期/重复审批、cross-tenant/workspace、source drift、same-key-different-hash 与并发单赢家。
- provider commit 后断线进入 `unknown` 且不自动 replay。
- target/new identity、source 不变、唯一 `published_from`、Audit/lineage failure 全回滚。

**失败恢复**

关闭 promotion executor/adapter，撤销待处理 Approval/Grant，保持 publication/effect 为当前单向状态并人工 reconciliation。不能把 `unknown` 改回 pending、重置幂等键、直接 UPDATE Operation/Approval/Audit/lineage，或把 source policy 原地修成目标值。

## INV-024 snapshot-restore-lineage

**权威源代码**

- `backend/src/omnibase/workspace_data/models.py`
- `backend/src/omnibase/workspace_data/service.py`
- `backend/src/omnibase/workspaces/service.py`
- `backend/src/omnibase/migrations/versions/0009_p34_6_workspace_data.py`

**为何存在**

Snapshot 只有在服务端生成并核验完整 resource/version/digest/size inventory 后才能作为恢复依据；调用方提交单个 manifest digest 不能证明数据一致。Restore 必须创建新的 Workspace identity/generation 和新的 private/derived Resource ID，并追加 `restored_from` lineage。Run、Lease、token、runtime/workload identity、PID、socket、连接、provider handle、进程内存和成员 Overlay identity 都是短期运行态，绝不能随 snapshot 复活。

**允许的改法**

- 增加 manifest schema version、entry count/bytes、content-addressed payload、storage verifier 和 forward-compatible closed-set adapter。
- 在一致性 barrier 下拒绝 active lease、pending write/build/promotion，或把 snapshot 留在 building/failed。
- Restore 使用 durable journal/effect，并在全部 entry 验证完成前保持新 Workspace stopped/unavailable。

**禁止的改法**

- 缺 item/object、digest/size/version/generation drift 时猜测恢复或把 snapshot 标 ready。
- 覆盖原 Workspace、复用旧 Resource ID，或复制任何旧 token/Run/Lease/runtime/网络身份。
- partial restore 可见、unknown effect 自动 replay、修改 ready manifest/entry 或删除 lineage 证据。

**必须运行的测试**

- snapshot 与 private write/index/promotion 并发 barrier。
- manifest 增删/重排/替换、missing/truncated/swapped blob、未知 schema/index format。
- restore-new Workspace/resource identity、generation 单调、旧 token/lease/runtime identity 全部失效。
- populated `0009` downgrade fail-closed 与 guarded restore-new sentinel verification。

**失败恢复**

停止 snapshot/restore worker，将不完整新 Workspace 保持 stopped/unavailable，保留 manifest、effect、journal、Audit 与 lineage 证据。重新验证对象和 digest 后走 forward-fix 或 restore 到新的 identity；不得覆盖原 Workspace、删除未知 effect、恢复旧运行态或对普通业务数据库执行破坏性试验。

## INV-035 production-evidence-provenance

**权威源码**

- `backend/src/omnibase/production/**`
- `deployment/production/**`
- `scripts/production/**`
- `backend/tests/test_p34_7_production_composition.py`
- `backend/tests/test_p34_7_joint_gate.py`

**为何存在**

P34.7 的生产结论必须能够从公开 clean checkout 重建，并精确绑定 Git commit/tree、受控 tracked-source manifest、部署配置和每份 evidence 的 SHA-256 与 JSON assertions。工作树 dirty、证据漂移、缺少当前源码证明或只存在历史报告时，状态只能是 `blocked/not_proven` 或 `invalid/veto`，不能靠人工文字改成 PASS。

哈希只证明 operator 写入的字节未被改写，不证明证据真实性。自伪造的完整 bundle（所有文件与哈希都由同一 operator 生成）绝不能得到 `passed`：component/attack/cleanup/posture evidence 必须是解析过的 canonical JSON 并绑定 run id、producer、source/artifact identity、command receipt、peer identities、measurements 与 results；每条 evidence 与 command receipt 都需要能对照**证据目录之外的独立 trust policy**（allowlisted producer Ed25519 公钥、approved source seal、approved artifact manifest、精确 argv 模板、env allowlist、gateway certificate pins）验证的 detached signature。policy 的原始字节必须命中代码内 pin 的 approved digest（当前为空集，因此任何 bundle 都保持 `blocked/not_proven`）；bundle 内携带的公钥不是信任锚。攻击与清理结果必须从已签名 evidence 解析并与 inventory 交叉核对，不得用内联 status/count 字段替代。

执行体必须三重绑定：receipt 声明的 executable digest、policy pin 的 digest 与**实际文件字节**的 SHA-256 必须一致，且每个 executable 必须出现在 approved artifact manifest 中（manifest 的 path/size/sha256 条目逐项对照真实字节）。任何只在 receipt/policy 声明中存在、或磁盘字节与签名 receipt 声明漂移的 executable 都是 `artifact_provenance=not_proven` 阻塞项。

evidence seal 的 canonical binding 必须覆盖 schema/schema_version、environment、disposable、完整 provenance（repository/source_commit/source_tree/dirty）以及验证链派生的全部当前顶层安全姿态（signature_authenticity、artifact_provenance、command_semantics、certificate_posture、replay_posture、runtime_posture、production_runtime_inactive、hostile_code_not_executed、root_env_not_accessed、business_database_not_accessed、business_database_not_migrated、attack_results、cleanup_complete）；外层字段的任何改写（environment `staging`→`production`、`disposable` `true`→`false`、`dirty` `true`→`false` 等）都会使重算 binding 与 recorded digest/签名不符而失败。

policy 的七个 producer 角色（六个组件 + sealer）公钥必须全部唯一，至少 sealer 必须与所有 producer 不同；重复公钥在 policy 解析时 fail-closed。gateway 证书必须满足 `valid_from <= now < valid_until`；`valid_until == now` 已过期（`valid_until <= now` 拒绝），`valid_from == now` 允许；issuer/SAN/最大有效期/吊销/replay 检查保持强制。

Git source provenance 必须绑定显式 object format（闭集 `sha1 | sha256`）：`provenance.git_object_format`、trust-policy `source_seal.git_object_format` 与每个 component evidence `git_object_format` 必须一致；`sha1` 只接受 40 位小写十六进制、`sha256` 只接受 64 位小写十六进制；commit/tree 保留原始 Git OID，不得自行二次 SHA-256；source/artifact manifest 继续使用原始字节 SHA-256，不得弱化。未知 format、长度不匹配、大小写错误、provenance/policy/component/seal format drift 全部 fail-closed。

Evidence 必须绑定冻结的有效期窗口：`run_started_at <= run_completed_at <= evidence_issued_at < evidence_valid_until`；每条 command receipt 与 posture/attack/cleanup 时间戳必须位于 run window 内；`now` 必须满足 `evidence_issued_at <= now < evidence_valid_until`；evidence age 与窗口长度均不得超过 trust policy 的 bounded `max_evidence_age_seconds`。验证只允许在单次调用内读取一次时钟（`verify_joint_evidence` 的 `now` clock seam）；四个时间字段与 object format 必须进入 evidence seal canonical binding；外层时间字段改写不重签、跨窗口 receipt、过期/未来 issued/超长窗口 bundle、policy max-age drift 全部拒绝；同一未过期 bundle 可幂等离线复验，过期 bundle 永不重判 PASS（`evidence_freshness` 变 blocker）。seal 绑定的 posture 以签发时刻时钟推导，保证复验不使有效 seal 失效。

Integration R1（2026-08-08）：本不变量随 P34.7 Integration R1 移植到最新 main-derived engineering branch（`codex/p34-7-joint-gate-integration-r1`，base = PR #18 merge commit `dfd4b20`）。这只是 Gate 代码进入统一主线：`joint_gate._APPROVED_TRUST_POLICY_SHA256` 仍为空集，P34.7 仍 `blocked/not_proven`，production activation 仍关闭，migration 0013 未创建，三个 Phase 5 Feature Gates 保持 false；本不变量的每一条强制执行要求不因移植而放宽。Review-Fix Round 2（2026-08-08）在此基础上关闭 object format、freshness window 与证书精确过期边界三个发现，本段前四段即为该轮新增的强制执行要求；`_APPROVED_TRUST_POLICY_SHA256` 仍为空，P34.7 仍 `blocked/not_proven`。

**允许的改法**

- 扩展显式 source scope、evidence schema 或验证断言，同时保留根 `.env`、symlink/reparse、非 regular file 和仓库外路径拒绝。
- 为新的独立生产组件增加当前源码绑定的 evidence 项；缺失项保持 `not_proven`。
- 将验证与激活分离；Gate 通过只产生 admission decision，不自动启动服务或授予 authority。
- 增加更强制的证据真实性要求（签名、canonical JSON schema、外部 trust policy、inventory 交叉核对、UTC instant 时间比较、每个路径组件的 junction/reparse 检查）；`_APPROVED_TRUST_POLICY_SHA256` 只有在真实独立 producer 链建立并审计后才允许追加 digest。

**禁止的改法**

- 在 dirty checkout、未跟踪生产源码、证据哈希不匹配或 source manifest 不完整时发出 production PASS。
- 从同一 untrusted bundle 内同时信任字段与其 sidecar 哈希；接受 bundle 内自带的公钥/trust root；把未签名或验签失败的 evidence 当作真实性证明；把 `runtime_posture.measured=false` 或其他 `not_proven` safety 项当作非阻塞信息。
- 将 Docker Desktop、WSL、mock、test double、disposable Gate、旧 commit evidence 或端口可达性冒充当前生产证据。
- 读取、打印、散列或纳入根 `.env`，或让 evidence path 逃逸仓库/受控 operator 目录。

**必须运行的测试**

- `backend/tests/test_p34_7_production_composition.py`
- `backend/tests/test_p34_7_joint_gate.py`（含 `scripts/production/forge_p34_7_evidence_bundle.py` 生成的自伪造完整 bundle：unsigned/forged signature/bundle-supplied trust root/swapped producer key/cross-run replay/cross-component replay/stale certificate/modified raw bytes/safety evidence absence 均必须 `blocked/not_proven`，永不 `passed`；并含唯一的 TRUE positive control —— 测试内 monkeypatch 临时批准 policy digest 后完整签名、manifest 绑定、seal 一致的链可达到 `passed`，monkeypatch 不落入 production approved set —— 以及 post-approval 攻击矩阵：替换实际 executable 字节、executable 缺席 artifact manifest、environment/disposable/dirty 外层改写不重签、七角色共用一把 key、sealer 与 producer 共用 key、valid_from 在未来、executable/manifest/receipt 三方 digest 漂移，全部必须 `passed=false` 或 `ConfigurationError`）
- `python scripts/production/validate_p34_7_composition.py --validate-only`
- 提交后必须从 clean checkout 运行 `--verify`；外部证据未齐时预期为 `blocked/not_proven`，不是失败伪装。

**失败恢复**

把 `activation_requested` 恢复为 false，撤销受影响组件的 admission，保留原 evidence 和 manifest 取证。修复源码或重新采集证据后从新的 clean checkout 验证；不得删除 Veto、忽略 dirty scope 或复用旧哈希。签名/trust policy 相关失败必须保持 `blocked/not_proven`，不得降级为 warning 或改为 passed。

## INV-036 production-composition-separation

**权威源码**

- `backend/src/omnibase/production/composition.py`
- `deployment/production/composition.example.json`
- `backend/src/omnibase/sandbox/**`
- `backend/src/omnibase/capability_gateway/**`
- `backend/tests/test_p34_7_production_composition.py`

**为何存在**

Core、Runner、Broker 与 Gateway 是四个独立信任边界。只有 Core 接受 Browser 流量，只有 Runner 执行 Workspace 代码；Runner/Broker 不得持有数据库、Redis、对象存储、JWT、签名私钥、宿主环境或成员 Overlay identity。固定内部通道必须使用 logical identifiers 和独立 peer identity，Browser cookie/JWT 不能沿内部通道传播。

**允许的改法**

- 为组件增加更窄的 credential class allowlist、独立 SPIFFE identity、mTLS 验证或 AF_UNIX peer/pinned-daemon identity。
- 新增 provider 时映射到既有 Core→Runner、Runner→Broker、Runner→Gateway、Broker→Gateway 边界，默认 unavailable。
- 缩短凭据 TTL、加强轮换、revocation 和 per-request live revalidation。

**禁止的改法**

- 合并 Core/Runner/Broker/Gateway 进程以绕过身份或网络验证。
- 让 Browser、Runner 或 Sandbox 直连 PostgreSQL、Redis、MinIO、object store、Docker socket、宿主路径或成员 Overlay endpoint。
- 用 bearer token、cookie、source IP、静态 service secret 或调用方提交的 peer identity 代替 mTLS/daemon-owned evidence。

**必须运行的测试**

- `backend/tests/test_p34_7_production_composition.py`
- P34.5 Runner transport、Broker、Gateway workload/mTLS focused tests。
- clean-checkout composition `--verify`，并逐项报告未证明的 production roundtrip。

**失败恢复**

停止 production activation，恢复 unavailable Runner、rejecting Broker/Gateway 和 server-owned credential registry。撤销 Run/Network Lease、workload certificate 与组件身份；不得把 workload 转交 Core、Celery 或宿主 shell 执行。

## INV-037 provider-commit-admission

**权威源码**

- `backend/src/omnibase/workspace_data/provider_adapters.py`
- `scripts/workspace-data/run_p34_7_provider_gate.py`
- `backend/tests/test_p34_7_workspace_provider.py`

**为何存在**

外部对象已经物理落盘不等于操作已提交。Artifact、Derived、copy-on-publish、Snapshot 与 Restore 必须绑定 tenant/workspace/operation/grant/version/action/resource/version/digest/size/generation，并经过 append-only effect journal 的 committed marker 后才可见。`pending|unknown` 永不自动 replay；non-disposable tenant/RAG 还需要短期、精确绑定的数据所有者准入事实。

**允许的改法**

- 新增生产 provider adapter，但必须保留 typed plan/grant/quota/receipt、content-addressed verification、committed visibility 和 reconciliation。
- 增加新的 storage lane 时保持 `canonical_readonly` 不可作为 provider write target。
- Restore 继续创建新 Workspace、Resource identity 和更高 generation；copy-on-publish 继续创建新 `controlled_shared` target。

**禁止的改法**

- 以对象存在、provider success HTTP、临时 receipt 或本地参考 adapter 作为 committed/production 事实。
- 自动重放 `pending|unknown`、覆盖 source、复用旧 Resource/Workspace identity 或恢复旧 Run/Lease/token/runtime/network identity。
- 在没有数据所有者授权时访问 non-disposable tenant/RAG，或把 physical locator/provider handle 暴露到 Browser、SDK、日志、Audit 或错误。

**必须运行的测试**

- `backend/tests/test_p34_7_workspace_provider.py`
- `scripts/workspace-data/run_p34_7_provider_gate.py` 的 disposable reference Gate。
- P34.6 Workspace-data、Promotion、Snapshot/Restore focused 与 guarded sentinel tests。

**失败恢复**

移除生产 adapter，恢复 unavailable/rejecting composition；保留 journal、receipt、object digest 和 reconciliation 状态。对 `unknown` 只允许人工读取可信 provider 状态后 forward-fix，不得删除 reservation 后伪装 fresh attempt。

## INV-038 overlay-production-evidence

**权威源码**

- `deployment/overlay/production/**`
- `scripts/overlay/p34_7_overlay_common.py`
- `scripts/overlay/p34_7_production_gate.py`
- `scripts/overlay/p34_7_sla_report.py`
- `backend/tests/test_p34_7_overlay_production_gate.py`
- `backend/tests/test_p34_7_overlay_sla.py`
- `docs/runbooks/p34-7-overlay-sla.md`

**为何存在**

真实成员 Overlay 不能由 Headscale control-plane test double 或单机 disposable evidence 推断。生产准入至少需要两个独立 Linux 成员节点、独立 production Node Daemon、独立 DERP、current-source Runner 12/12、Broker 两轮 26/26、真实 revoke/credential-theft/no-replay/cleanup、容量与 SLA 样本，并由两个成员节点分别对同一 canonical payload 做独立 Ed25519 签名。

**允许的改法**

- 增加新的真实成员、DERP、故障注入或 SLA scenario，并为最小样本数、成功率、p95、并发度和 allowed outcome 设置更严格阈值。
- 轮换成员 attestation key，但 topology pin、public-key digest 和 evidence signature 必须同步更新并可验证。
- 将后续 evidence/docs-only commit 与冻结 source scope 分离；任何受控生产源字节变化都必须重新采集证据。

**禁止的改法**

- 让 Sandbox 成为 Overlay peer，向 workload 暴露物理 endpoint/route/key，或允许直达数据库、Redis、MinIO、provider、host route。
- 接受 placeholder 节点、重复 signer、未签名 payload、direct path 未关闭的“强制 DERP”或历史 11/11 artifact。
- 把缺样本、超 SLA、节点未独立、credential theft 未拒绝或 cleanup 非零降级为 warning。

**必须运行的测试**

- `backend/tests/test_p34_7_overlay_production_gate.py`
- `backend/tests/test_p34_7_overlay_sla.py`
- `python scripts/overlay/p34_7_production_gate.py --validate-only ...`
- 真实 production Gate 必须验证双成员签名、DERP、node compromise、current-source 12/12、两轮 26/26 和完整 SLA observation。

**失败恢复**

隔离受影响 Node Daemon/成员节点，撤销 node credential、Peer Grant、Service Advertisement 与 Network Lease，停止 service publication，并保留签名 evidence 与 observation。rejoin 必须创建新 identity 和新 fencing；不得恢复旧 credential 或自动重放 ambiguous mutation。

## INV-039 phase5-admission-fail-closed

**权威源码**

- `backend/src/omnibase/production/phase5_admission.py`
- `deployment/production/phase5-admission.example.json`
- `scripts/production/validate_p5_0_admission.py`
- `docs/phase-5-threat-model.md`
- `backend/tests/test_p5_0_admission.py`

**为何存在**

Phase 5 是否允许开始不能由"验证器存在"或"模型很强大"决定。P5.0 必须由
三个独立、server-owned、默认关闭的 Feature Gate 与 P34.7 Evidence Manifest
共同 fail-closed：缺失/空 gate 等于 `false`，未知值（大小写、空白、非标准
字符串、非字符串）必须配置错误，Planner 依赖 Runtime、Multi-Agent 依赖
Planner+Runtime，即使三 gate 全为 `true` 只要 P34.7 不是 `ready` 仍必须
`blocked/not_proven`。Gate 只返回 admission decision，不启动任何 Agent/
Planner/Executor/queue/worker/scheduler，也不读取根 `.env` 或业务数据库。

**允许的改法**

- 收紧 gate 解析（更小的接受集）、合同闭集或 evidence 断言。
- 为新的独立生产证据增加 sealed evidence 项；缺失项保持 `not_proven`。
- 在 fresh clean checkout 上重跑 `--verify`，并把 `blocked/not_proven`
  作为外部证据未齐时的唯一正确结果。
- 更新 P34.7 decision、OpenAPI/SDK/composition/runbook 时，在同一变更中
  同步更新 P5.0 合同的 sealed digest 并重新验证。

**禁止的改法**

- 用 `bool("false")` 一类不安全解析、大小写/空白容忍或默认真值开启 gate。
- 用总开关隐式开启两个以上 gate，或在 P34.7 非 `ready` 时把 P5.0 写成
  PASS/ready。
- 让 validator 读取、打印、散列或提交根 `.env`、凭据、证书载荷、数据库
  或业务存储；让 evidence/合同路径逃逸仓库或指向根 `.env`。
- 在 P5.0 模块中预装 AgentDefinition/AgentVersion ORM、Planner、Executor、
  dispatcher、scheduler、Tool/Model provider、Memory/Skill runtime、
  Multi-Agent DAG、MCP 或任意 shell/SQL/HTTP 工具；新增 Agent API、UI、
  后台 worker 或 Celery task；以"代码存在但 gate 关闭"为理由实现上述内容。
- 把 `not_proven` 证据计为通过、容忍 dirty checkout、忽略 migration
  head/SDK/OpenAPI/composition/runbook 漂移，或把 `critical_veto.expected`
  写成非 0。

**必须运行的测试**

- `backend/tests/test_p5_0_admission.py`（gate 解析负向、合同闭集、证据
  digest/断言漂移、migration head、SDK/OpenAPI/composition/runbook 漂移、
  dirty veto、三 gate 全 true 仍 blocked、report safety negatives）
- `python scripts/production/validate_p5_0_admission.py --validate-only`
- 提交后从 fresh clean checkout 运行 `--verify`；当前正确结果是
  `blocked/not_proven`（P34.7 未 ready），不是失败伪装。

**失败恢复**

把三个 gate 恢复为 `false`、`activation_requested` 恢复为 `false`，修复
合同后从新的 clean checkout 重跑 validator。gate 解析或依赖冲突视为配置
错误（veto），不得静默降级为 true；sealed digest 漂移时保留原合同与
report 取证，更新证据或合同后重新封存并 re-verify。任何情况下都不得从
该模块启动 Phase 5 运行时组件。

## INV-040 p51a-registry-contract-preflight

**权威源码**

- `backend/src/omnibase/production/phase5_registry_contract.py`
- `deployment/production/phase5-registry-contract.example.json`
- `scripts/production/validate_p5_1_registry_contract.py`
- `docs/phase-5-agent-registry-contract.md`
- `backend/tests/test_p5_1_registry_contract.py`

**为何存在**

P5.1A 只是 Agent Registry 的离线合同预检，不是 Registry 实现。合同必须
保持逻辑化（无物理 locator/凭据）、不可变（sealed manifest digest 基于
canonical 原始 UTF-8 字节）、无秘密（无 API key/base_url/Authorization/
cookie/token/私钥）且非运行态（无 ORM/migration/service/API/Planner/
Executor/worker/scheduler）。P34.7 或 P5.0 未 `ready` 时，P5.1A 恒
`blocked/not_proven`；三个 Phase 5 Feature Gate 保持 false；源码树中
出现任何 forbidden runtime/ORM/API 包或 migration revision 漂移都是 veto。
本不变量不得用 P5.1A 的离线结果证明数据库约束、RBAC、并发安装或
Runtime；P5.1B/P5.1C 的独立 engineering 实现与 Gate 必须单独取证。

**允许的改法**

- 收紧 DTO 闭集、budget ceiling、JSON Schema 子集或 approval policy。
- 为新的离线语义增加负向 fixture；digest 始终按 canonical JSON 原始
  UTF-8 字节计算，不接受换行归一化解码文本冒充。
- 更新 P34.7/P5.0 evidence 或合同文档时，在同一变更中同步更新
  P5.1A 合同的 sealed digest 并重新验证。

**禁止的改法**

- 在本模块或 validator 中实现/预装 ORM、migration、registry service、
  Browser API、SDK 调用、Planner/Executor/dispatcher/scheduler、Model/
  Tool/Memory/Skill runtime、Celery task、Agent Runtime 或 shell/SQL/HTTP
  tool；以"代码存在但 gate 关闭"为理由同样禁止。
- 让 binding 以不同 digest 绑定同一 version ID、引用未知 definition/
  version、缺少 high/critical risk 所需的 approval、使用通配符 tool ID
  或把 revoked/disabled 状态解释为 active。
- 接受重复 definition/version/binding ID、重复 tenant logical key 或重复
  definition semver；允许 version/binding 跨 Tenant、binding 指向不属于
  其 definition 的 version、Workspace binding 绕过 installation scope，
  或让 version 降低 definition risk 以绕过 Approval。
- 让 validator 读取根 `.env`、凭据、数据库、migration 或外网；把 report
  写到仓库内；把 `not_proven` 计为 passed；把 safety negatives 写死为
  true 而不经源码边界/import 约束/负向测试证明。
- 在 `--verify` 时忽略真实 server Feature Gate 环境；只检查配置文件最终
  分量而允许父目录 symlink/reparse，或跟随既有 symlink report 目标。
- 把 P5.1A 写成 P5.1 PASS；把 P34.7 改成 ready；打开 Phase 5 Feature Gate；
  把 P5.1B 的 ORM/migration/内部 service 伪装成已完成 Runtime 或公开 API。

**必须运行的测试**

- `backend/tests/test_p5_1_registry_contract.py`（60 项负向清单：DTO
  闭集、digest 长度/大小写/漂移、CRLF 原始字节、JSON Schema `$ref`、
  budget/NaN/Infinity、symlink/reparse `.env` 逃逸、dirty checkout、
  remote mismatch、gate true/truthy、forbidden 包/migration/router、
  OpenAPI agent endpoint、仓库内 report、not_proven 计数、safety
  negatives）
- `python scripts/production/validate_p5_1_registry_contract.py
  --validate-only`（合法合同 exit 0，永不 ready）
- 提交后从 fresh clean checkout 运行 `--verify`；当前正确结果是
  `blocked/not_proven`（exit 2，veto 0）。

**失败恢复**

保持 gate false、删除/回退任何意外出现的 runtime/ORM/API 源码，从新的
clean checkout 重跑 validator。sealed digest 漂移时保留原合同与 report
取证，更新证据或合同后重新封存并 re-verify。任何情况下都不得从该模块
启动 Phase 5 运行时组件或访问业务数据库。

## INV-041 p51b-registry-persistence-foundation

**权威源码**

- `backend/src/omnibase/agent_registry/models.py`
- `backend/src/omnibase/agent_registry/service.py`
- `backend/src/omnibase/migrations/versions/0010_p5_1b_agent_registry.py`
- `docs/evidence/p5-1/phase5-registry-persistence-design.md`
- `scripts/production/run_p5_1b_registry_disposable_gate.py`

**为何存在**

P5.1B 是 Agent Registry 的**内部持久化地基**：三张全局控制面表
（`agent_definitions`、`agent_versions`、`workspace_agent_bindings`）、
一个 scoped 迁移（`0010`）和唯一的内部事务服务
（`RegistryPersistenceService`）。它不是公开 API：没有 FastAPI router、
OpenAPI endpoint、SDK surface、Invocation/Task/Run/Plan/Step/Attempt、
Planner/Executor/Dispatcher/Scheduler、Celery、Agent Runtime、
Model/Tool/Memory/Skill Runtime、MCP 或 shell/SQL/HTTP tools。三个 Phase 5
Feature Gate 保持 false，P34.7/P5.0/P5.1 production 保持 blocked/not_proven。

数据库本身执行不变量，ORM 纪律不是唯一保护：复合 `(id, tenant_id)` FK 阻断
跨租户引用；trigger 执行 definition/version/binding 状态机、sealed version
内容不可变、revoked 终态、risk 不降级、tool ID 数组闭集、approval 有效性；
partial unique index 保证每 workspace+definition 只有一个 live binding。
sealed version 的身份列与内容列都不可变；binding 安装身份/payload 不可
重连，只允许受控状态列变化；approval 与 superseded target 都使用同租户
复合 FK，不能只靠服务层检查。

**允许的改法**

- 在同一事务内扩展内部事务操作（幂等解析、approval 消费、append-only
  审计、resource_registry 登记一起提交）；保持锁序
  Tenant -> tenant User(actor) -> Workspace -> Definition -> Version ->
  live Binding -> IdempotencyRecord -> ApprovalRequest（首次执行）-> target
  row -> AuditEvent。exact replay 必须在 approval 重验前返回已提交结果。
- P5.1B 内部调用继续使用 `internal_full` 完整 DTO hash。面向 P5.1C 的
  install/upgrade/rollback profile 必须由 service 自行计算，禁止任意
  caller digest；数据库 trigger 对 Binding Approval action 只接受
  `agent.install|agent.upgrade|agent.rollback` 闭集，service 再校验精确
  operation/hash 并单次消费。
- 收紧 DTO 校验、ceiling、trigger 状态机或新增 fail-closed 集成测试；
  任何 contract/迁移/测试变更必须同步更新 sealed digest 并重验。

**禁止的改法**

- 新增任何 Browser `/api/v1/agents` 路由、OpenAPI agent endpoint、SDK
  client、前端页面或 Invocation/Runtime/Orchestration 表面；以"内部可用"
  为由暴露亦然。
- 让实体/审计/resource_registry 暴露物理 schema/table/column locator；
  DTO、错误与日志只允许逻辑标识符。
- 跨租户引用 definition/version/workspace；sealed version 内容被改写；
  revoked/disabled 被解释为 active；version 降低 definition risk；
  high/critical binding 无 approval；同一 approval 被消费两次；live
  binding 并发出现第二个赢家。
- catch-and-ignore `IntegrityError`；用 ORM 层纪律代替数据库 trigger；
  在无 idempotency/audit 的事务外单独更新 binding 行；对 0010 做
  populated in-place destructive downgrade。
- 把 P5.1B 写成 P5.1 production 就绪、P34.7/P5.0 ready，或打开任何
  Phase 5 Feature Gate。

**必须运行的测试**

- `backend/tests/test_p5_1b_agent_registry.py`（映射/幂等/冲突/approval/
  revoke 的 MagicMock 单元测试）
- `backend/tests/integration/test_p5_1b_agent_registry_foundation.py`
  （一次性 sentinel PostgreSQL：migration head、cross-tenant 拒绝、
  sealed 不可变、并发单赢家、exact replay、digest drift、stale
  generation、approval 单次消费、审计 append-only、回滚无部分状态、
  物理 locator 缺席、0010 populated downgrade fail-closed）
- `make test-p5-1b-registry` 与
  `python scripts/production/run_p5_1b_registry_disposable_gate.py --run`
  （一次性隔离数据库 Gate，evidence 记录到
  `docs/evidence/p5-1/phase5-registry-persistence-disposable-gate.json`）

**失败恢复**

- 任何 tenant/actor、状态机、幂等、approval、audit、cleanup evidence 或
  source seal 缺陷出现时，立即冻结 Registry mutation，保持三个 Phase 5
  Feature Gate 为 false，P34.7/P5.0/P5.1 production 为
  `blocked/not_proven`，P5.2+ frozen。
- 不得对已 populated 的 `0010` 做 destructive downgrade；只允许
  forward-fix，或恢复到新的 `omnibase_restore_*` 数据库进行核验。
- 仅在新的 `omnibase_test_*` sentinel 数据库上重跑 migration 与 Gate；
  Gate 必须在 Alembic 前实际执行 `backend/tests/destructive_preflight.py`；
  Compose 必须显式 `--env-file .env.example`，且只有容器、网络、卷清理计数
  全部为 0 后才能发布 passed evidence。不得触碰业务数据库。

## INV-042 p51c-browser-registry-control-api

**权威源码**

- `backend/src/omnibase/agent_registry/control.py`
- `backend/src/omnibase/agent_registry/router.py`
- `backend/src/omnibase/agent_registry/schemas.py`
- `backend/src/omnibase/main.py`（`agent_registry_router` +
  `agent_installation_router` 挂载）
- `sdk/python/src/omnibase_sdk/browser_registry.py`
- `sdk/typescript/src/registry-browser.ts`
- `docs/evidence/p5-1/phase5-browser-registry-api-design.md`
- `scripts/production/run_p5_1c_browser_registry_disposable_gate.py`

**为何存在**

P5.1C 在 Browser `/api/v1` 上暴露 Agent Registry 的**受控只读目录与
Workspace 安装生命周期**：6 个只读端点（definitions/versions/
installations）+ 4 个 mutation（install/disable/upgrade/rollback）。
生产默认 fail-closed：未装配 DB-backed control plane 时，任何端点都在
接触任何 registry 表之前返回 HTTP 503 `agent_registry_unavailable`
（`get_registry_control_plane` 默认注入 `UnavailableAgentRegistryControlPlane`）。

每个受保护请求都必须通过 `get_current_principal`；每个 mutation 在调用者
拥有的事务内重新验证 live Tenant/User/role/WorkspaceMembership
（`authorize_workspace_action(action, lock=True)`），锁序为 Tenant ->
User(actor) -> Workspace -> WorkspaceMembership；Browser 对 Version/Binding
只做非锁定快照，随后由 P5.1B sealed 服务按 Definition -> Version ->
live Binding -> IdempotencyRecord -> ApprovalRequest -> target row ->
Resource -> AuditEvent 的权威锁序复核。API 层不得创建
AgentDefinition/AgentVersion：定义注册与版本 sealed 仍 internal，三个
Phase 5 Feature Gate 保持 false。P5.1C 不拥有迁移；仓库 migration head 已因
单独授权的 P5.2B engineering migration 推进到 `0011`。

**允许的改法**

- 在同一事务内扩展只读投影或 mutation 校验；definitions/versions 是
  live Tenant principal 下的 tenant-wide catalog，installation 读使用
  `workspace.read`，mutation 使用 `workspace.grants.manage`；upgrade/rollback 必须通过
  sealed 目标版本校验（digest 精确匹配）与 `expected_binding_id` 期望
  绑定校验，最终复用 `supersede_binding` 的原子语义。
- 幂等/Approval hash 只能由 service 的封闭 profile 计算：`internal_full`
  保持 P5.1B 原始完整 DTO 语义；Browser install/upgrade/rollback 分别绑定
  `agent.install|agent.upgrade|agent.rollback`，supersede 摘要还必须包含
  `old_binding_id`。禁止任意 caller-provided digest。同 key 同 body精确
  replay，同 key 不同 body 409，且 upgrade/rollback replay 必须先到达
  IdempotencyRecord，再判断旧 Binding 是否仍 live。
- 收紧公共 DTO 的 `extra="forbid"`、scope 闭集或新增 fail-closed 测试；
  任何 contract/测试变更必须同步更新 sealed digest 并重验。

**禁止的改法**

- 移除或绕过 fail-closed 默认依赖（production 默认必须 503）；把 DB-backed
  control plane 直接装配进 main.py 生产组合而不经显式注入。
- 新增 AgentDefinition/AgentVersion 创建端点、由 P5.1C 创建新的 migration、
  打开任何 Phase 5 Feature Gate、暴露未授权 Runtime/Orchestration 表面。
- 在公共 DTO/OpenAPI/SDK/错误体中出现物理 schema/table/column locator、
  凭据或审计内部字段；请求只用逻辑标识。
- 用事务前角色快照、cookie 或裸资源 id 替代 mutation 内的 live
  membership 重锁；跨租户返回 definition/version/binding。
- 让同 key 同 body 的 replay 误判为 drift，或让高风险的 install 绕过
  approval 单次消费；让 install Approval 被 upgrade/rollback 使用，或让
  upgrade 与 rollback 共用不含 operation/old Binding 的摘要。
- 在 Browser 层先锁 Version/Binding 后再调用 P5.1B，形成与标准
  Definition -> Version -> Binding 顺序相反的锁序。
- SDK 只用字符串前缀判断 Browser path，允许 dot segment、反斜杠、编码
  或 query/fragment 在 URL 规范化后逃逸 `/api/v1/`；SDK response parser
  必须拒绝 extra field、非法 closed state、非整数与 `NaN`。

**必须运行的测试**

- `backend/tests/test_p5_1c_registry_api.py`（10 端点 fail-closed 503、
  rejecting authorizer、DTO 严格性、OpenAPI 精确路径集合、无物理
  locator、无 internal 请求字段、非法 UUID 稳定 422）
- `backend/tests/integration/test_p5_1c_browser_registry_api_foundation.py`
  （一次性 sentinel PostgreSQL：migration head 0011、API-backed
  install/upgrade/disable/rollback、exact replay、digest drift、stale
  generation、cross-tenant、live membership、并发单赢家、install/
  upgrade/rollback operation-bound approval、upgrade/rollback exact replay、
  approval 单次消费、审计 append-only、rollback 原子性、cleanup proof）
- `make test-p5-1c-registry-api` 与
  `python scripts/production/run_p5_1c_browser_registry_disposable_gate.py --run`
  （`omnibase_test_p51c_*` 一次性隔离数据库 Gate，evidence 记录到
  `docs/evidence/p5-1/phase5-browser-registry-api-disposable-gate.json`）
- Python SDK `sdk/python/tests/test_registry_browser_client.py` 与
  TypeScript SDK `sdk/typescript/tests/registry-browser.test.mjs`（含 path
  normalization escape 与严格 response parsing 负例）

**失败恢复**

- 任何 fail-closed、授权边界、幂等、approval、audit、cleanup evidence
  或 source seal 缺陷出现时，立即冻结 Browser registry mutation，保持
  三个 Phase 5 Feature Gate 为 false，P34.7/P5.0/P5.1 production 为
  `blocked/not_proven`。
- 不得对已 populated 的 `0010` 做 destructive downgrade；只允许
  forward-fix，或恢复到新的 `omnibase_restore_*` 数据库进行核验。
- 仅在新的 `omnibase_test_*` sentinel 数据库上重跑 migration 与 Gate；
  Gate 必须在 Alembic 前实际执行 `backend/tests/destructive_preflight.py`；
  Compose 必须显式 `--env-file .env.example`，且只有容器、网络、卷清理计数
  全部为 0 后才能发布 passed evidence。不得触碰业务数据库。

## INV-043 phase5-task-ledger-contract-preflight

**权威源码**

- `backend/src/omnibase/production/phase5_task_ledger_contract.py`
- `deployment/production/phase5-task-ledger-contract.example.json`
- `scripts/production/validate_p5_2a_task_ledger_contract.py`
- `docs/phase-5-task-ledger-contract.md`
- `backend/tests/test_p5_2a_task_ledger_contract.py`

**为何存在**

P5.2A 仍是 P5.2 Agent Task/Run/Step/Attempt 账本的**离线合同预检**，不是
运行时组合根。合同必须保持逻辑化（无物理 locator/凭据）、不可变（sealed
manifest digest 基于 canonical 原始 UTF-8 字节）、无秘密（无 API
key/base_url/Authorization/cookie/token/私钥）且非运行态。用户已显式批准
P5 Fast Track，因此 migration `0011`、P5.2B durable ledger、内部 Model
Gateway 与默认不可用的无工具单 Agent Alpha 是允许的 engineering source；
它们不改变 P5.2A 的离线性质。P34.7、P5.0、P5.1 production 任一未 `ready`
时，P5.2A 恒
`blocked/not_proven`；三个 Phase 5 Feature Gate 保持 false；**任何 gate
意外解析为 `true` 或 `activation_requested=true` 都是 veto**（比 P5.0/
P5.1A 的 blocker 更严格）。源码树出现生产 Runtime wiring、Planner/
Executor/scheduler/worker、真实工具/MCP/Skill 执行或未批准的 migration
未经用户批准的 `0013+` 是 veto。本不变量现在承认 P5.2B 持久化地基、migration
`0012` 的用户资料/Provider 凭据控制面与 Alpha engineering
slice；不得声称 Task dispatch/worker、生产 Agent Runtime 或多 Agent 已完成。

**允许的改法**

- 收紧 DTO 闭集、hash profile 字段集、budget ceiling、deadline/TTL
  上限或 identity stage 规则；digest 始终按 canonical JSON 原始 UTF-8
  字节计算。
- 为新的离线语义增加负向 fixture（50 项矩阵）；每项必须断言稳定 reason
  code。
- 更新 P34.7/P5.0/P5.1 evidence 或合同文档时，在同一变更中同步更新
  P5.2A 合同的 sealed digest（含 P5.1 registry contract digest）并重新
  验证；P5.2A 修改的 sealed 文档（threat-model、maintainer map、
  security-invariants）必须先同步 P5.1A 合同的 sealed digest。

**禁止的改法**

- 在本模块或 validator 中启动 Agent、访问数据库或 provider；把已授权的
  P5.2B/Model Gateway/Alpha source 自动装配成生产 Runtime；新增 Planner、
  Executor、dispatcher、scheduler、worker、Celery 长循环、Memory/Skill
  runtime、MCP 或 shell/SQL/arbitrary-HTTP tool；新增未经批准的 migration `0013+`
  而无新的用户授权。
- 允许 Task Lease 越过 deadline/Run Lease/Node attestation/Grant/
  policy 的最早 expiry；允许 Task/Node/Run fencing 或 attempt number
  回退；允许 terminal Run/Attempt/Effect 复活；允许 `unknown` 自动
  replay；允许 cancel 伪装 unknown 为成功；允许 checkpoint 携带
  token/lease/PID/socket/provider handle；允许模型输出作为 committed
  evidence；允许调用方扩大预算或覆盖 request hash。
- 允许 attempt_number 跨 Step 混排（它按 (task_id, step_id) 分组、必须从
  1 起精确连续：重复/回退/跳号/非 1 起始均拒绝）或 Task fencing 跨 Step
  回退；允许把 task_fencing_token 拍平为系统级或 Run 级共享序列（它必须是
  per-Task 序列：同一 Task 内跨 Step 单调，不同 Task 各自独立、可各从 1
  开始）；允许以 Attempt 记录作为 fencing 的权威数据源（**必须是
  append-only TaskLease 账本**：`active`/`completed`/`revoked`/`expired`
  全部参与、按 `task_lease.created_at` 排序，terminal Attempt 清空
  `task_lease_id`/`task_fencing_token` 不抹除其历史 Lease；Attempt 只用于
  active Attempt ↔ active Task Lease 双向绑定、状态矩阵与 token 一致性，
  不能充当历史 fencing 账本）；允许按 `task_lease.created_at` 的**原始
  ISO-8601 字符串**排序判定 fencing 单调（时间轴必须是
  `_parse_utc_timestamp` 归一化后的 UTC instant：`Z`/`+HH:MM`/`-HH:MM` 都
  合法，字符串顺序不等于真实 UTC 顺序，按字符串排序会把非法 token 回退
  "整理"成升序）；允许同一 Task 内两条 Lease 归一化为**同一 UTC instant**
  时仍按任意顺序通过（合同没有可信第二排序字段，必须 fail closed：不得
  依赖输入数组顺序、不得用 `task_lease_id`/`attempt_id` 字典序或 token
  自身排序把歧义整理为合法）；允许 timestamp offset 越界被静默接受或泄漏
  原生异常（offset 是闭集：小时 `00–23`、分钟 `00–59`，`+01:60`/`+00:99`
  显式拒绝，不依赖 `datetime.fromisoformat` 归一化；任何解析、offset 运算
  或 UTC 归一化失败——含 `0001-01-01T00:00:00+23:59` 与
  `9999-12-31T23:59:59-23:59` 的年份边界溢出——都必须稳定转换为
  `TaskLedgerContractError`，不得泄漏 `ValueError`/`OverflowError`）；允许
  `task_lease.created_at` 早于其绑定的 `attempt.created_at`（这会允许后来低
  token holder 通过 backdate claim 时间把真实回退重新排序为表面递增）；允许
  `completed`/`revoked`/`expired` 历史 Lease 绕过 `expires_at > created_at` 或
  `task_lease_ttl_ceiling_seconds`（每条 append-only Lease 都是在签发时受同一
  ceiling 约束的授权记录）；允许非空 `heartbeat_at` 落在 Lease 创建/过期
  区间之外；允许
  pending/ready 携带 lease、leased/dispatching/running 缺失
  lease、terminal（含 unknown）保留 lease；允许 Attempt 引用另一 Attempt
  的 Lease、同 Attempt 双 active Lease 或 stale/revoked/expired lease 作为
  current；允许 active Task Lease 绑定 ready/pending/terminal Attempt
  （孤儿 active lease）或其 Attempt 未指回/未共享 fencing（active Lease 必
  须绑定恰好一个 leased/dispatching/running Attempt 且 Attempt 指回并共享
  fencing token）。
- 把 `--verify` 的 `evidence_references_verified` 无条件写成 true 而不实际
  校验 config.evidence[].path/sha256/assertions；允许 passed evidence
  指向不存在文件、digest 漂移或 assertion 不匹配仍报告 verified。evidence
  引用必须真实验证（路径仓库内相对 regular 非链接文件、raw-byte SHA-256 与
  sealed digest 一致、assertions 作为机器可验证闭集逐项解析），只有实际
  执行并通过的项（`evidence_path_verified`/`evidence_digest_verified`/
  `evidence_assertions_verified`/聚合 `evidence_references_verified`）才为
  true；未执行或失败必须为 false/not_executed 且 fail closed（veto）。
- 允许 AgentRun 四元运行绑定组（run_lease_id/run_fencing_token/
  node_id/node_fencing_token）或 runtime/workload 身份组不完整（all-or-
  none 状态矩阵：created 全空、leased/running/paused 全有、terminal
  全空）。
- 允许 config 收紧值（deadline_ceiling_seconds、
  task_lease_ttl_ceiling_seconds）不作用于每个 DTO，或允许 config 扩大
  server-owned ceiling。
- 允许 Step 与父 Task 的 plan_id/plan_version/plan_digest 漂移；允许
  dependency 引用未知/跨 Task/跨 Run 节点、step_number 重复或依赖图有环。
- 允许 attempt.deadline 晚于 task.deadline，或 task lease expiry 晚于
  attempt/task deadline。
- 允许 attempt_claim/heartbeat/finish hash profile 缺失安全相关
  immutable identity（agent_run_id、node_id、run_lease_id/
  run_fencing_token、node_fencing_token、agent_version_digest、
  resource_scope_digest、budget_policy_digest）。
- 把固定输出的 safety negative 当作运行时证明：报告必须区分 static
  source-boundary assertion、import/AST assertion、Gate 本次未执行的
  行为与直接运行证据（`verification_evidence`）。
- 允许 Browser 提交 core-generated/未生成字段（runtime_instance_id、
  workload_identity_thumbprint、request_hash、lease/fencing），或允许
  Browser JWT 进入 workload DTO。
- 让 validator 读取根 `.env`、凭据、数据库、migration 或外网；把 report
  写到仓库内；把 `not_proven` 计为 passed；把 safety negatives 写死为
  true 而不经源码边界/import 约束/负向测试证明。
- 把 P5.2A 写成 P5.2 PASS；把 P34.7 改成 ready；打开 Phase 5 Feature
  Gate；把 P5.2B 的 ORM/migration/内部 service 伪装成已完成 Runtime 或
  公开 API。

**必须运行的测试**

- `backend/tests/test_p5_2a_task_ledger_contract.py`（完整负向矩阵：
  DTO 闭集、hash profile、预算不变量、TTL 边界、fencing 单调、terminal
  resurrection、unknown no-replay、cancel 语义、identity stages、
  symlink/reparse `.env` 逃逸、dirty checkout、gate true veto、forbidden
  未批准的 runtime 包/migration 0013+、OpenAPI 边界、仓库内 report、not_proven
  计数、safety negatives）
- `python scripts/production/validate_p5_2a_task_ledger_contract.py
  --validate-only`（合法合同 exit 0，永不 ready）
- 提交后从 fresh clean checkout 运行 `--verify`；当前正确结果是
  `blocked/not_proven`（exit 2，veto 0）。

**失败恢复**

保持 gate false、`activation_requested=false`，禁用任何意外生产 wiring，
保留已批准的 P5.2B/Model Gateway/Alpha engineering source，并从新的 clean
checkout 重跑 validator。sealed
digest 漂移时保留原合同与 report 取证，更新证据或合同后重新封存并
re-verify。任何情况下都不得从该模块启动 Phase 5 运行时组件或访问业务
数据库。

## INV-044 p52b-durable-task-ledger

P5.2B 是 engineering-only 的持久化地基。Migration `0011` 在
`omnibase_meta` 创建 11 张 Agent Task/Run/Step/Attempt/TaskLease/Budget/
Effect/Checkpoint/Reconciliation 表；tenant scope 只推进 revision、不得复制
global ledger。所有聚合引用都必须使用包含 `tenant_id` 的复合外键，Attempt
到 current TaskLease 的环必须由 `DEFERRABLE INITIALLY DEFERRED` 外键与
constraint trigger 在事务提交时双向核对。

Task fencing 只能由按 Task 锁定的 cursor 分配，并使用数据库
`clock_timestamp()` 固化 chronology；TaskLease 历史 append-only，terminal
Attempt 清空 current lease 不能抹除历史。Effect `unknown` 是终态，禁止自动
replay。服务只参加调用方拥有的事务，不自行 commit，不调用模型、provider 或
工具。Populated `0011` downgrade 必须以 SQLSTATE `55000` fail closed；恢复
只能 forward-fix 或 restore 到新的 `omnibase_restore_*` 数据库。

Task Lease 窗口是 Attempt 的唯一存活授权：数据库时钟（锁内）是唯一时钟，
terminalize 时刻 `now >= expires_at` 的 lease 绝不允许 settled 为
`committed`/succeeded —— `settle_terminal_outcome` 必须把这种 late
terminalization 派生为 `unknown`（终态、只开 reconciliation、禁止自动
replay），并且 Lease/Attempt/Task/AgentRun/WorkspaceRun 在同一个事务里用
同一个 settled outcome 原子收口，不得遗留 active lease、running attempt、
running task/run 或 workspace slot。heartbeat 可以固定在 `expires_at`
边界，但不得借此延长或复活授权；stale/replaced lease id 或 fencing token
的 finish 必须继续拒绝。

当 Workspace Run Lease 也同时过期/被撤销时，`submit_run_state` 的严格校验
不得放宽；terminalize 只能通过 server-owned 的历史 holder 收口路径
`close_historical_run_holder`：只接受 `failed`/`cancelled`（unknown 映射为
failed），绝不允许把过期授权解释为 `succeeded`/committed；必须在锁内校验
精确的历史 holder（WorkspaceRun、RunLease、node binding、workspace
generation、run fencing、旧 Lease node fencing），并重新验证当前持久化
WorkspaceNode 仍 active、attestation 仍 verified 且未过期、当前 Node fencing
仍与历史 Lease 完全一致；stale/replaced lease、generation drift、Node fencing
推进、Node revoke/attestation 失效、错误 node/workspace 一律 fail closed。
该路径不是任意 `LeaseRejected` 的兜底：RunLease 必须已是 revoked/expired，或
仍为 active 但数据库 `clock_timestamp()` 已到/超过 `expires_at`；active 且未过期
的 holder 绝不能由历史路径关闭。RunLease 不得续期、不得复活、不得回到
active；WorkspaceRun 终态化并清空 runtime/workload binding，释放 interactive
slot；TaskLedger、WorkspaceRun 与 reconciliation 在同一事务原子提交，任一
后续失败整体回滚。

个人版重启恢复只能在下一次同 Tenant/Workspace/Owner 调用或 exact replay 中
处理数据库时钟已经过期的旧 active TaskLease。它必须复用上述原子收口路径：
Attempt/Effect -> `unknown`、Task -> `blocked_unknown`、Run 终态化并打开恰好一个
reconciliation；不得调用 Provider、重新读取可变 RAG/Memory/Skill、创建第二个
Effect、复活旧 Lease/fencing/runtime identity 或把不确定结果改写为成功。显式
`retry_of` 是一个全新调用，只能指向同范围且处于
`blocked_unknown|failed|cancelled` 的旧 Task；新调用必须获得全新的
Task/Attempt/Lease/Run/Effect/Operation/identity，旧账本永久保留。

所有 Phase 5 Feature Gates 必须继续为 false；migration `0011` 与 disposable
Gate 通过都不授权生产 Runtime。验证只能使用 `omnibase_test_p52b_*` sentinel
数据库，先运行 destructive preflight，最后证明容器/网络/卷 `0/0/0`，并对
source/evidence 做 raw-byte SHA-256 seal。不得读取根 `.env`、访问或迁移业务
数据库。

## INV-045 model-gateway-and-tool-free-agent-alpha

Model Gateway 的 provider credential、base URL 和 Authorization header 都是
server-owned，不得进入 Browser DTO、SDK、日志、错误体、Task ledger 或审计
详情。请求模型 ID 必须与 provider 返回的 actual model ID 精确一致；缺失或
不一致一律 fail closed，禁止静默 fallback。Provider 原始错误必须转换为稳定、
脱敏的 reason code。输入、输出、并发和 timeout 都必须有服务端上限。

本阶段 payload 不得包含 `tools`/`tool_choice`，AgentVersion 的
`allowed_tool_ids` 必须为空。Alpha 只允许一个已安装 sealed AgentVersion、一个
Model Gateway stream 与只读 Workspace knowledge；没有 shell、SQL、任意 HTTP、
MCP、Skill、Planner、DAG 或多 Agent 端口。取消权绑定 tenant、workspace、actor
与 invocation identity，猜到另一主体的 invocation ID 不得取消。

生产依赖必须继续返回 `UnavailableAgentAlpha`，即 Browser API 默认
`503 agent_alpha_unavailable`；只有测试/engineering dependency override 可以
装配实现。实际模型身份必须写入最终事件和结果 digest；provider outcome 不明确
时只能记录 unknown/reconciliation，不得伪装成功或自动重放。生产 Runtime
激活、Feature Gate 开启与 provider production wiring 均需要新的显式批准。

## INV-046 agent-alpha-engineering-runtime

Engineering-only Agent Alpha 只能通过 `AGENT_ALPHA_ENGINEERING_ENABLED`
严格解析（true/false，禁止 pydantic 布尔 coercion）+ `ENV=development` +
三个 Phase 5 Feature Gate 均通过同样的严格闭集解析且全 false（缺失/空值/
精确 `false` 为关闭，精确 `true` 为开启，任何其他拼写都是配置错误）+
Model Gateway 已装配 + migration head
`0014` 才能通过 `build_engineering_agent_alpha()` 装配 DB-backed service；
任何一步不满足都返回 `UnavailableAgentAlpha`（fail closed，且不触碰
registry/ledger/RAG/provider）。该 seam 不激活生产 Agent Runtime，不开启
`AGENT_RUNTIME_ENABLED`/`AGENT_PLANNER_ENABLED`/`MULTI_AGENT_ENABLED`。

Task/Run/Step/Attempt/Lease/Budget/Effect 只通过 migration `0011` 的
`TaskLedgerPersistenceService` 写入：transaction A 在 provider 边界前完成
durable reservation（task `created->scheduled->running`、run
`leased->running`、attempt `leased->dispatching`、effect
`reserved->dispatching` 分别跨 flush 走 guard 允许的转换），transaction B
重新加锁校验后 terminalize（effect/attempt/run/task 按 outcome 转换，终态
run 必须清空全部 lease/fencing/runtime binding）。禁止绕过 guard 或一次性
把状态机跳到终态。

Exact replay 必须逐字节复现 task_create canonical payload，并把稳定的
Browser 调用意图哈希（workspace、冻结 AgentVersion、message、top_k、retry_of、
用户个性摘要、credential source/ID/version/key fingerprint/provider/model 的非秘密
configuration digest）
纳入 canonical payload：task id 与
server-assigned deadline 从已提交的 idempotency record（response_ref）与其
durable task 恢复，同 key 同 payload 只返回原 task，绝不重复调用 provider、
不创建新 Attempt、不重复扣费，也不得重新执行可变 RAG 检索；同 key 不同
payload 是 stable conflict。RAG 命中 ID 不得进入调用意图哈希，否则索引漂移
会破坏合法 exact replay。
In-flight 重复（attempt 仍在 active 状态）必须拒绝二次 dispatch。`unknown`
outcome 只进入 reconciliation，绝不自动重放。

若 in-flight Attempt 的精确 TaskLease 已按数据库时钟过期，exact replay 可以在
同一事务内把原 invocation 收敛为 `blocked_unknown` 并返回原 identity，但不能
再次 dispatch。新 invocation 在占用 Workspace slot 前可以收口同
Tenant/Workspace/Owner 的过期旧 holder。`retry_of` 必须精确绑定旧 Task 的
Owner、Workspace、AgentVersion/binding、scope 与 budget digest；live Task、
跨范围 Task 或缺少 open reconciliation 的 `blocked_unknown` Task 都不可重试。

取消注册表是进程内 signal（module-level），cancel endpoint 通过
tenant/workspace/actor/invocation 四元组匹配；durable 终态永远来自 ledger，
SSE disconnect、Provider deadline、Provider 返回缺失 actual model identity 均只
记录 unknown/reconciliation，绝不伪造 deterministic failure/cancelled。RAG 检索
只能读取当前 tenant + Workspace 下 `ready` 的 P34.6 derived-index generation；
禁止退回 tenant-wide canonical RAG，top_k 与 context 有服务端上限；工具型
AgentVersion（`allowed_tool_ids`
非空）在 adapter 与 service 双层拒绝且返回稳定 409。disposable Gate 只使用
`omnibase-p52c-*` project / `omnibase_test_p52c_*` 数据库与角色；生产
Runtime 激活、Feature Gate 开启与 provider production wiring 均需要新的
显式批准。Fresh invocation 必须创建短期 P34 WorkspaceRun/RunLease，并把同一
server-owned runtime identity 与非占位 workload digest 绑定到 P34 WorkspaceRun
和 P5 AgentRun；Provider/Agent deadline、TaskLease TTL、Workspace RunLease TTL
必须严格留出终结余量。Server-created Model Gateway Node identity 绑定 deployment
instance，attestation 为短期；revoked/rejected Node 不得被原地复活。

## INV-050 p54b-engineering-composition

P5.4B is an **engineering-only** composition seam over the P5.4A typed
single-Agent Executor. `build_engineering_single_agent_executor()` must remain
fail closed unless the explicit engineering flag is enabled, migration head is
exactly `0012`, all three Phase 5 Feature Gates are false, and the Gateway,
server-owned workload credential seam and session factory are explicitly
injected. The builder never migrates or connects merely to inspect the head.
Production Runtime activation remains disabled and migration `0013` is not
created.

The composition exposes only `knowledge_search -> workspace.knowledge.search`.
The Gateway adapter accepts server-owned `WorkloadCredential` material and
bounded logical DTOs only; Browser JWTs, physical PostgreSQL/object-store
locators, provider secrets, host paths, process/socket handles and arbitrary
tool expansion remain forbidden. `LiveRuntimeAuthorityValidator` must read
live Workspace, Task, sealed AgentVersion, installed binding, Agent Run,
Workspace RunLease and Workspace Node facts in a fresh session before each
Gateway call. Task actor, plan/version/scope/budget digests, generation,
runtime/workload identity, current WorkspaceRun fencing cursor, database-clock
lease expiry, verified Node and exact Run/Node fencing must all agree. The mTLS
certificate thumbprint and workload identity digest are distinct mandatory
server-owned SHA-256 facts; the certificate binds transport/token `cnf`, while
the workload digest binds persisted execution authority.

The P5.4B disposable Gate may use only an isolated `omnibase_test_p54b_*`
sentinel and must pin the sentinel migration head to `0012`. Gate v2 records
production/runtime and feature gates disabled, migration `0013` absent, root
`.env` and business database untouched, workload-container egress denied,
local-only image acquisition enforced by pull-never, and cleanup `0/0/0` under
a unique run-scoped directory. It preserves the legacy evidence
chain as superseded/incomplete, captures raw command/exit-code sidecars, and
independently seals source, artifact and evidence bytes. Image/venv/package
measurements are sealed but explicitly ambient-runtime-dependent. Digest drift stops
admission and requires a forward fix from a clean checkout; historical chains
must not be rewritten or replaced.

Credential attestation, the live P5.4B validator and Gateway Core verification
are layered separate transactions, not an atomic authority closure. The
residual revocation race must be documented; database locks must not be held
across arbitrary RAG/provider work, and production admission remains
blocked/not_proven.

**Allowed changes**

- Tighten the engineering composition's closed flag, migration, identity,
  fencing, DTO or fail-closed checks.
- Add focused negative tests and maintainer/evidence documentation without
  adding Browser/API, SDK, persistence or production Runtime authority.
- Add a new isolated disposable evidence run only with explicit sentinel
  prefixes, explicit cleanup and a newly sealed manifest.

**Forbidden changes**

- Enabling production Runtime or any Phase 5 Feature Gate, creating migration
  `0013`, or treating a disposable Gate as production admission.
- Falling back to direct database/RAG access, Browser credentials, provider
  clients, arbitrary tools, queue/worker scheduling or a second capability.
- Mutating historical sealed evidence, bypassing clean-checkout/source digest
  checks, reading the root `.env`, or touching the business database.
- Changing production composition implementation or a sealed/disposable Gate
  script as a documentation-only maintenance task.

**Required verification**

- `backend/tests/test_p34_7_production_composition.py`
- `backend/tests/test_p5_4a_typed_executor.py`
- `backend/tests/test_p5_4a_gateway_adapter.py`
- `backend/tests/test_p5_4b_gate_v2.py`
- `backend/tests/integration/test_p5_4b_engineering_composition_foundation.py`
- `python scripts/production/run_p5_4b_engineering_composition_disposable_gate.py --validate-only`
- Maintainer map and benchmark validators
- Disposable Gate `--verify-evidence` against its own sealed report, when run

**Recovery**

On flag, migration-head, feature-gate, identity, lease/fencing, source-manifest
or evidence drift, return the seam to unavailable and keep production disabled.
Preserve the old sealed chain, capture the failing report, and forward-fix in a
new reviewed commit or isolated sentinel run. Do not downgrade the business
database, create `0013`, retry an unknown provider outcome, or activate a
production component while evidence is incomplete.

## INV-051 p54c-lite-agent-product-loop

P5.4C is the **engineering-only product surface** for the single-Agent loop.
`AGENT_LITE_ENGINEERING_ENABLED` is an independent closed-set gate that defaults
off; any token other than exactly `true` or `false` (including missing, empty,
`TRUE`, `1`, `yes`, `on`, `enabled`) must fail closed. The gate is a *product*
entry guard, never an authorization fact: passing it only opens the Lite
Browser surface in a development/engineering deployment. It never authorizes
production Agent Runtime, Planner, multi-Agent execution, arbitrary tools,
migration `0013`, or any of the three Phase 5 production Feature Gates, which
must remain exactly `false`.

The Lite product loop supports exactly one invocation mode: `no_tool`, carried
by the P5.2C Alpha seam `build_engineering_agent_alpha` (the `/invoke` route
always dispatches through that seam; `AlphaInvokeRequest` has no mode field).
The formal P5.4B builder `build_engineering_single_agent_executor` (which
installs `LiveRuntimeAuthorityValidator` and
`CapabilityGatewayKnowledgeSearchPort`) is formally connected to this product
loop (`formal_builder_integration = proven_engineering_only`) through a proven
engineering integration fixture that exercises the real persisted authority
chain (AgentVersion, AgentTask, AgentRun, WorkspaceRun, RunLease,
WorkspaceNode, NodeAttestation, server-owned WorkloadCredential with bound
workload identity digest) and resolves AgentRun → WorkspaceRun via
`AgentRunModel.workspace_run_id`. The proof is **engineering-only**
(`engineering_composition_ready = true`, `activation_allowed = false`): it is
never assembled in the Browser request path, never routed, never
production-selectable, and a builder name in a status DTO is never a supported
mode. The formal composition remains a separate P5.4B engineering seam whose
only authority is the P5.4B disposable PostgreSQL Gate with real persisted
runtime/lease facts. `lite_agent_posture()` is read-only and non-authorizing:
it only describes which builder the UI should label; assembly decisions stay
in the fail-closed builders. The status DTO must not leak provider secrets,
physical locators, credentials, migration internals or runtime handles.

The pure parser `resolve_lite_agent_flag(raw)` is independent of the ambient
host environment: `None` is documented to mean "the variable is absent" and
resolves to `False` even when a stray `AGENT_LITE_ENGINEERING_ENABLED` is set
in the process environment, and the parser never calls `os.environ` itself.
The runtime resolver `runtime_lite_agent_enabled()` is the only place the gate
reads `os.environ.get(AGENT_LITE_ENGINEERING_ENABLED)` and passes the value
into the parser; the Browser dependency `router.get_agent_alpha()` and the live
posture must use it, so setting the flag to `true` genuinely enables the route.
`lite_agent_posture()` with `env=None` resolves the Lite flag through the
runtime resolver and must never read the flag from `os.environ` directly; only
an explicit `env` mapping or an explicit `raw` argument feeds the pure parser.
`docker-compose.yml` passes `AGENT_LITE_ENGINEERING_ENABLED` (and the closed
`P5_4B_ENGINEERING_ENABLED`) to the backend environment explicitly with
fail-closed defaults of `false`; `.env.example` documents both, and
`docker compose --env-file .env.example config` must show `"false"` by default
and `"true"` only under an explicit engineering override. Tests must isolate
environment state with `monkeypatch`, proving absent -> off, false -> off,
true -> on, invalid -> fail closed, ambient-variable independence of the pure
parser, that the runtime resolver reads the patched environment, and that the
`env=None` posture never reads the Lite flag from `os.environ` itself.
API-level tests must prove that the flag reaches the assembled or unavailable
Alpha dependency as appropriate instead of always returning the
Lite-gate-disabled path.

The P5.4C disposable Gate is run-scoped and engineering-only. It executes the
focused Lite posture suite **and** the P5.4B formal engineering-composition
suite before an executed gate probe patches the process environment and
measures the runtime resolver, the live posture and the single supported mode
inside the backend container. The formal suite exercises
`build_engineering_single_agent_executor`, `LiveRuntimeAuthorityValidator`, the
AgentRun-to-WorkspaceRun distinction and workload-identity-digest drift
negatives. The Gate then seals the tested source
bytes, command receipts and measurements under unique raw-byte SHA-256
sidecars. Every claim in the report is derived from an executed receipt or a
sealed file measurement, or is reported `not_proven`; the
root-env/business-database negatives are re-derived from the recorded command
vectors and the migration head is re-discovered from the repository files, so
nothing is a hardcoded measurement. The sealed source manifest is a **closed
set** covering every file that decides Compose Lite-flag wiring
(`docker-compose.yml`, `.env.example`), frontend `canInvoke`
(`frontend/lib/lite-gate.ts` and `frontend/lib/lite-gate.test.ts`) and Gate
admission, and the Gate tests assert that the maintenance-map
`lite-agent-product-loop` module / `INV-051` source paths stay a subset of the
closure. The Gate only PASSES when the closed-set
admission decision holds: `lite_gate_default_off`, `absent_off`, `false_off`,
`true_on`, `invalid_fail_closed`, `live_posture_reflects_env`, `no_tool`-only,
`formal_builder_named` and `engineering_composition_ready` all `true`;
`root_env_accessed`, `business_database_accessed`, `business_database_migrated`,
`production_runtime_activated`, `formal_builder_posture_not_integrated` and
`activation_allowed` all `false`; `formal_builder_integration` stays
`proven_engineering_only`. The integration claim is admissible only because the
same sealed `lite-unit-suite` receipt includes the formal composition suite; a
posture constant without that executed target is not proof. A single mismatch
makes `passed=false`. The two
formal-builder claims are **independent**: `formal_builder_integration =
proven_engineering_only` means the formal P5.4B builder is formally connected
to this product loop through a proven integration fixture, while
`formal_builder_posture_not_integrated = false` requires the executed probe to
genuinely report `proven_engineering_only` (not `not_integrated`). The probe's
token is recorded **honestly** — `proven_engineering_only` is recorded verbatim;
a tampered probe reporting `not_integrated` is rewritten to `not_proven` as
defence-in-depth and fails the admission expectation; a probe reporting
`integrated`/`enabled`/`available`/`selectable`/empty/unknown is recorded
verbatim and fails the admission decision (`--run` produces `passed=false`
and `--verify-evidence` rejects). The run directory is **preserved**
on success and on failure and can be re-verified with `--verify-evidence`
after the process exits; the Gate never deletes its own evidence and never
claims production admission. `--verify-evidence` validates the **exact argv
template** of every recorded command (the explicit `.env.example` path, the
closed production engineering flags and the exact Lite/formal-composition test
targets / probe source —
a drifted vector that exited 0 is rejected), strictly parses every
`commands/*.exitcode` sidecar (exactly one decimal exit code that must equal
the receipt `returncode`; non-integer, multi-line, missing and 0/1-drifted
sidecars are all rejected) and **re-executes the same
closed-set admission decision** that `--run` computed: verifying is not just
"report equals derived values", because derived values that miss an admission
expectation (e.g. `true_on=false`, `invalid_fail_closed=false`,
`live_posture=false`, `engineering_composition_ready=false`,
`activation_allowed=true`, `formal_builder_posture_not_integrated=true`, mode
drift, command-vector drift or exitcode-sidecar drift) must reject the
evidence.

Round-5 hardens the receipt and sidecar binding so a fabricated-but-
self-consistent evidence tree cannot pass. The verifier requires each
receipt's `returncode` to be a **strict `int`** (`type(value) is int`, not
`isinstance(value, int)`) equal to `0`; this rejects JSON `false`/`true`
(Python `bool`, which `isinstance(value, int)` would wrongly accept because
`False == 0`), floats like `0.0`, strings like `"0"`, `null`, negative and
non-zero integers. The command keys must form the **exact closed set**
(`lite-unit-suite`, `lite-gate-probes`) with no missing, duplicate, extra or
unknown key and no re-order. Each command key binds its **own** sidecar by
**exact POSIX path literal**: the receipt's `stdout` path must be exactly
`commands/{key}.stdout` and its `exitcode` path exactly
`commands/{key}.exitcode`, compared **before** any filesystem resolution; this
rejects absolute paths, backslash alternatives, `.`/`..` segments, repeated
separators, case aliases, URL/drive paths and every lexical alias
(`commands/../commands/{key}.stdout`, `commands/./{key}.stdout`) so two
commands cannot share or swap stdout/exitcode artefacts and a unit receipt
cannot point at the probe's stdout (or vice versa). Only after the literal
matches does the verifier resolve and check run-dir containment, regular-file,
non-symlink and digest; symlink sidecars are rejected outright
(platform-dependent: on platforms without symlink support the test is
documented as skipped). No two commands may share the same stdout or exitcode
literal, and the resolved artefacts must have distinct inodes where the
platform exposes them. Finally, the verifier **re-derives the unit summary**
from the precisely-bound `commands/lite-unit-suite.stdout` bytes (not the
receipt's recorded stdout string) by calling the formal
`_parse_test_summary()`, and compares the re-derived `passed`/`failed`/
`skipped`/`deselected` counts field-by-field with strict `type(value) is int`
equality against **both** the top-level `lite_unit_summary` and
`measurements["lite_unit_summary"]`; a missing/extra field, a boolean-as-int,
a count that disagrees with the sealed stdout, or a top-level-vs-measurements
drift rejects the evidence. The probe is re-parsed from the precisely-bound
`commands/lite-gate-probes.stdout`; `formal_builder_integration =
proven_engineering_only` and `formal_builder_posture_not_integrated = false`
stay two independent claims, and `not_integrated`/`integrated`/`enabled`/
`available`/`selectable`/empty/unknown tokens continue to be recorded (with
`not_integrated` rewritten to `not_proven` as defence-in-depth) and rejected.

The sealed evidence is a **self-contained integrity receipt**: it
proves run-scoped byte integrity of the recorded source manifest, command
receipts and measurements, but without an independent trust anchor it proves
**no external authenticity** (it cannot authenticate who produced the bytes)
and is never production admission; the report records this scope
(`integrity_receipt.external_authenticity=false`,
`integrity_receipt.trust_anchor=null`) and the verifier enforces the wording.

**Allowed changes**

- Tighten the Lite gate's closed-set parser, runtime resolver, fail-closed
  defaults, posture disclosure or UI state labels without adding Browser
  authorization, SDK, persistence or production Runtime authority.
- Add focused negative tests, API-level reachability tests, maintainer/evidence
  documentation and the disposable Gate's source seal without enabling
  production admission.
- Tighten the formal-builder disclosure (identity, DTO or fail-closed checks)
  and the formal integration fixture without adding Browser authorization,
  production Runtime authority, or treating the engineering proof as production
  proof.
- Require the closed-set admission decision and the exact command-vector
  templates in both `--run` and `--verify-evidence`, and describe the evidence
  with self-contained integrity-receipt wording (run-scoped byte integrity,
  no external authenticity, no trust anchor).

**Forbidden changes**

- Enabling production Runtime or any Phase 5 Feature Gate, creating migration
  `0013`, or treating the disposable Lite Gate as production admission.
- Presenting the P5.2C Alpha seam as the knowledge-search authority path,
  advertising `knowledge_search_read_only` as a supported mode, using a
  fake `_Authority`/fake authority object or the weaker
  `build_engineering_typed_executor` bypass in the formal integration path,
  or assembling the formal composition in the Browser request path instead
  of leaving assembly to the P5.4B disposable Gate.
- Letting the gate parser depend on the ambient host environment, accepting a
  non-exact token through loose bool coercion, or wiring the Browser dependency
  or live posture to anything other than `runtime_lite_agent_enabled()`.
- Leaking provider secrets, physical locators, credentials, migration internals
  or runtime handles into browser state, logs, diagnostics, errors or DTOs.
- Mutating historical sealed evidence, deleting preserved Gate evidence,
  bypassing clean-checkout/source digest checks, reading the root `.env`, or
  touching the business database.

**Required verification**

- `backend/tests/test_p5_4c_lite_gate.py`
- `backend/tests/test_p5_4c_lite_agent_product_gate.py`
- `backend/tests/test_p5_4b_engineering_composition.py` (formal builder
  integration fixture with real persisted authority chain)
- `backend/tests/test_agent_alpha_engineering.py`
- `python scripts/production/run_p5_4c_lite_agent_product_disposable_gate.py --validate-only`
- Frontend `pnpm typecheck`, `pnpm lint`, `pnpm test`, `NODE_ENV=production pnpm build`
- Maintainer map and benchmark validators
- Disposable Gate `--verify-evidence` against its own sealed report, when run

**Recovery**

On gate, posture, source-manifest or evidence drift, close the Lite product
surface (return `UnavailableAgentAlpha` from `get_agent_alpha`) and keep
production disabled. Preserve the old sealed chain and the Gate evidence,
capture the failing report, and forward-fix in a new reviewed commit or
isolated run. Do not enable a Phase 5 production Feature Gate, create `0013`,
retry an unknown provider outcome, or present a disposable Lite Gate as
production admission while evidence is incomplete.

## INV-049 p54a-typed-single-agent-executor

P5.4A 是 engineering-only 的第一个 typed Executor 切片。它只能接收一份
通过 P5.3A Validator 的 immutable `ValidatedPlan`，并且只能执行一个节点、
一个固定的只读逻辑能力：`knowledge_search` →
`workspace.knowledge.search`。Planner 的“提案已通过”不是执行授权；Executor
在边界上必须再次核对 proposal digest、Tenant、Workspace、generation、Actor、
Task、Run fencing、AgentVersion digest 和 node identity。

P5.4A 的 node 必须是 low risk、`read_only` effect、未扩大 tool allowlist，且
tool budget 与 response bytes 不能超过 server-owned ceilings。结果只能通过
注入的 Capability-Gateway-backed `KnowledgeSearchPort` 获得；当前唯一实现
`CapabilityGatewayKnowledgeSearchPort` 必须使用 server-owned `WorkloadCredential`、
独立 Gateway 的 `rag_search` 和每次调用前的 runtime/lease/fencing validator；默认 builder 必须
返回 `UnavailableTypedSingleAgentExecutor`，不能因为缺少 adapter、attestor、
verifier 或 Gateway wiring 而回退为直连数据库/RAG、允许执行或宽松鉴权。

Executor DTO 只接受 bounded logical identifiers 和 bounded search data，禁止
physical PostgreSQL/object-store locator、Browser JWT、Provider credential、
process/socket/host path、model handle 以及任意 `tools`/`tool_choice` 扩展。适配器
异常必须 fail closed，不能生成成功 receipt；未来的 timeout、断线和未知 effect
必须进入 durable Task/Effect reconciliation，不能自动 replay。

P5.4A 不创建 migration `0013`，不挂载 Browser route/SDK，不启用 Planner Runtime、
queue、worker、scheduler、Skill/MCP、Shell、SQL、任意 HTTP、Sandbox 或 multi-Agent。
三个 Phase 5 Feature Gates 继续为 false，production Runtime activation 仍需单独
准入。

## INV-047 user-profile-and-personal-provider-credentials

**Authoritative source**

- `backend/src/omnibase/user_settings/`
- `backend/src/omnibase/db/tenant.py`
- `backend/src/omnibase/migrations/versions/0012_user_profiles_provider_credentials.py`
- `backend/src/omnibase/tenants/dependencies.py:get_current_principal`
- `frontend/app/(dashboard)/settings/page.tsx`

**Why it exists**

User preferences and personal model credentials are now a real Browser control
plane, not a placeholder. Every request must still revalidate the live Tenant
and live User and must operate only in that tenant schema. Provider secrets are
server-owned after submission: Browser responses expose only a keyed masked
fingerprint and posture. AES-256-GCM AAD binds tenant, user, credential,
provider and key version, so ciphertext cannot be copied across identities.

Provider endpoint testing is an outbound security boundary. The accepted URL
is exact-host allowlisted HTTPS without userinfo, query, fragment or IP literal;
resolved addresses must be globally routable. Tests use `trust_env=false`, do
not follow redirects, have a fixed timeout and require exact requested/actual
model identity. Provider body, headers, request IDs, trace IDs and raw errors
are neither returned nor audited.

The outbound request runs without holding the first database transaction. The
service captures a non-secret credential configuration digest, releases the
connection, performs the bounded request, then re-locks the live user and
credential in a new transaction. Any version, provider, URL, model, key
version/fingerprint, active/default or revocation drift returns a stable
conflict and must not write PASS. The endpoint is additionally protected by a
fail-closed Redis rate limit scoped to tenant, user and credential.

Agent Alpha resolves a tested active personal default on every invocation. If a
personal default exists but is untested, corrupted or undecryptable, invocation
fails closed; it must never silently fall back to the operator provider. Only
the absence of a personal default permits the explicitly labelled operator
default. User assistant name, tone and instruction digest are part of the
invocation intent so idempotency detects preference drift.

**Allowed changes**

- Add logical provider presets or profile fields with closed schemas, bounded
  lengths, tenant/user ownership checks, optimistic versioning and append-only
  audit in the same transaction.
- Rotate ciphertext by incrementing `key_version` and recomputing identity-bound
  AAD; production/staging must use an independent
  `PROVIDER_CREDENTIAL_ENCRYPTION_KEY`.
- Tighten the hostname/DNS allowlist, timeout, response parser or test-state
  classification without exposing provider material.

**Forbidden changes**

- Returning, logging, tracing, exporting, committing or placing an API key,
  ciphertext, nonce, Authorization header or provider response body in an
  artifact.
- Trusting JWT ownership without the live principal and tenant session, or
  reading another user/tenant credential by raw UUID possession.
- Allowing HTTP, arbitrary hosts, userinfo, query/fragment, IP literals,
  private/loopback/link-local/reserved DNS answers, redirects or proxy
  inheritance for personal Provider tests.
- Silent model/provider fallback, model identity aliasing, or using an untested
  personal default.
- Populated destructive downgrade of migration `0012`; global scope must first
  preflight every retained server-owned tenant schema before the global revision
  row can move, preventing a populated tenant from leaving split global/tenant
  heads. Use a forward fix or a verified new `omnibase_restore_*` database.

**Required verification**

- `backend/tests/test_user_settings.py`
- `backend/tests/test_agent_alpha.py`
- `backend/tests/test_agent_alpha_engineering.py`
- Frontend `pnpm typecheck`, `pnpm lint`, `pnpm build`
- Maintainer map and benchmark validators
- Pre-0012 backup checksum plus restore-to-new-database verification before the
  business/development database migration

**Recovery**

Revoke the affected credential, which clears ciphertext and nonce; restore the
operator default only as an explicitly labelled fallback. If the encryption key
is lost or ciphertext authentication fails, do not guess or bypass GCM: require
the user to submit a new secret. If migration recovery is required, retain the
source database and restore the verified pre-0012 dump to a new database.

## INV-048 user-created-tool-free-agent-builder

**Authoritative source**

- `backend/src/omnibase/agent_registry/control.py:create_custom_agent`
- `backend/src/omnibase/agent_registry/router.py:builder_router`
- `backend/src/omnibase/production/phase5_registry_contract.py:AgentVersionManifest`
- `backend/src/omnibase/agent_alpha/adapters.py:RegistryProfileResolver`
- `frontend/app/(dashboard)/agents/page.tsx`

**Why it exists**

An authenticated Workspace member may create a real user-owned Agent, but the
first public builder is deliberately narrower than the general Registry
contract. One caller-owned transaction revalidates the live Tenant, live User,
Workspace generation and live `workspace.grants.manage` membership; registers
the AgentDefinition; seals a Version whose manifest contains the real system
instructions and matching SHA-256; optionally installs the Version; registers
logical Resources; completes idempotency records; and appends Audit records.

The complete client intent is the idempotency anchor. Server-generated UUIDs
and timestamps must not make exact replay drift, while the same key with a
different name, role, instructions, tone, budget, Workspace, Provider policy or
knowledge policy must conflict. Raw Browser fields never supply a digest.

The accepted profile is a closed set: `provider_policy=user_default`,
`knowledge_mode=workspace_read_only`, low risk, one sealed Version, no memory
runtime, and an empty `allowed_tool_ids`. The runtime re-hashes stored
instructions before use. Tools, shell, SQL, arbitrary HTTP, MCP, Skills,
Planner, multi-Agent execution and hostile-code Sandbox access remain absent;
all three Phase 5 Feature Gates remain false.

**Allowed changes**

- Add bounded presentation fields or additional sealed versions while keeping
  the manifest digest, instruction digest, idempotency and Audit lifecycle
  atomic.
- Add server-owned, closed Provider or knowledge policies after their runtime
  semantics and negative tests exist.

**Forbidden changes**

- Saving only an instruction digest while displaying a claim that custom
  instructions execute, or using unsealed mutable text at invocation time.
- Accepting Browser-provided tool IDs, physical locators, Provider secrets,
  arbitrary URLs, Planner graphs or multi-Agent topology.
- Creating Definition, Version and Binding in separate committed transactions,
  or authorizing from a JWT/workspace snapshot without live row revalidation.
- Replaying an old Agent after any client-intent drift under the same
  Idempotency-Key.

**Required verification**

- `backend/tests/test_p5_1c_registry_api.py`
- `backend/tests/test_p5_1_registry_contract.py`
- `backend/tests/integration/test_p5_1c_browser_registry_api_foundation.py`
- `backend/tests/test_agent_alpha.py`
- Frontend `pnpm test`, `pnpm typecheck`, `pnpm lint`, `pnpm build`
- Browser E2E: create, install, select and invoke one custom Agent in both themes

**Recovery**

Disable the Builder route while preserving all Registry rows and Audit history.
Disable or revoke the affected binding/version through existing lifecycle
transitions; never edit a sealed manifest or delete ledger evidence to make a
profile appear valid.

## INV-049 first-party-native-skill-contract

**Authoritative source**

- `backend/src/omnibase/production/phase5_skill_contract.py`
- `deployment/production/phase5-skill-contract.example.json`
- `scripts/production/validate_p5_6a_skill_contract.py`
- `docs/phase-5-native-skill-contract.md`
- `docs/runbooks/skill-review-revoke-rollback.md`

**Why it exists**

P5.6A freezes the product Skill identity, schema, provenance, budget and
rollback vocabulary before any persistence or runtime exists. A Skill is a
first-party, Workspace-scoped, exact-version behavior package; it is not a raw
plugin, credential container, dynamic download, MCP server or authority grant.

The compile-only contract must remain incapable of claiming production
publication. P5.6A accepts at most `tested` behavior; `approved|published`
requires later sealed source, dependency lock, SBOM, signature, secret scan,
paired evaluation, human review and rollback evidence. Instruction Skills have
zero tools, capabilities and tool-call budget. Workflow and script manifests
may be parsed as tested metadata only; they are never expanded or executed.

**Allowed changes**

- Tighten the closed manifest, JSON Schema subset, digest rules, server-owned
  ceilings or rollback validation.
- Add first-party `draft|tested` instruction fixtures whose digest fields are
  explicitly identified as contract fixtures rather than release evidence.
- Extend offline negative tests and maintainer documentation without creating
  persistence, API routes or runtime authority.

**Forbidden changes**

- Creating migration `0013`, Skill ORM/service/router/SDK/UI installation or a
  runtime under P5.6A without a separately authorized phase.
- Treating a SHA-256-shaped value or `signature_status=verified` as proof of
  review/publication.
- Allowing third-party definitions, non-Workspace installation, secrets,
  network access, wildcard tools/capabilities, external/cyclic schema refs, or
  an instruction Skill that expands AgentVersion authority.
- Executing `verification_commands` through Core shell, executing script Skills
  in Core, or using a workflow Skill without the complete P5.3/P5.4 Validator
  and Executor gates.
- Enabling any Phase 5 Feature Gate, MCP or Marketplace because this contract
  parses successfully.

**Required verification**

- `backend/tests/test_p5_6a_skill_contract.py`
- `python scripts/production/validate_p5_6a_skill_contract.py --validate-only`
- `python scripts/production/validate_p5_6a_skill_contract.py --verify` from a
  committed clean checkout; expected state is `blocked/not_proven`, exit 2
- Focused Ruff and Mypy for the contract, test and CLI
- Maintainer map and benchmark validators
- Compose config with explicit `.env.example`

**Recovery**

If the contract, Git source, migration head or example manifest drifts, stop
admission and restore the last reviewed compile-only contract or forward-fix it
in a new commit. Keep every Phase 5 Feature Gate false. Do not create a database
rollback or runtime fallback: P5.6A has no database and executes no Skill.

## INV-052 desktop-diagnostics-redaction

**Authoritative source**

- `backend/src/omnibase/runtime/diagnostics.py`
- `backend/src/omnibase/runtime/capabilities.py`
- `backend/src/omnibase/runtime/lifecycle.py`
- `backend/tests/test_runtime_redaction_attacks.py`
- `backend/tests/test_runtime_capabilities.py`
- `backend/tests/test_runtime_lifecycle.py`

**Why it exists**

The desktop diagnostics redactor is the privacy boundary between operator
support bundles and secrets. It must redact secrets recursively through
mappings, lists and tuples, match sensitive keys case-insensitively
(authorization, cookie/set-cookie, api key/token/secret/password/private-key/
credential variants and repository-specific provider credential names), bound
depth/collection size/rendered string length, and handle cycles deterministically
without recursion crashes or leaking cycle contents. The public payload must stay
JSON-serializable and deterministic, and the typed signature must never forward
untyped `*args/**kwargs` into the payload builder.

The sensitive-name policy is a **normalized token/full-field closed set plus a
bounded `_`-delimited suffix policy with no arbitrary substring matching**:
`monkey`, `keyboard_layout`, `design` and `session_count` are preserved while
`api_key`, `access_token`, `signature`, `session_token` and provider variants
are redacted. Keys are tokenized at **acronym-aware** case boundaries: both
lower/digit -> upper (`stripeA` -> `stripe_A`) and the end of an all-caps
acronym run before a Capitalized word (`APIKey` -> `API_Key`), so
`stripeAPIKey` -> `stripe_api_key`, `OPENAIApiKey` -> `openai_api_key`,
`openAIApiKey` -> `open_ai_api_key`, `azureADAccessToken` ->
`azure_ad_access_token`, `myTOKEN` -> `my_token`, `providerPASSWORD` ->
`provider_password` and `xAPIKey` -> `x_api_key` are redacted while non-secret
controls (`sortKey`, `cacheID`, `apiVersion`, `foreignKey`, `keyboardLayout`,
`monkey`) are preserved. The `_key` suffix rule is **narrow**: `sort_key`,
`cache_key`, `foreign_key`, `keyboard_layout` and `monkey` are PRESERVED while
`api_key`, `secret_key`, `access_key`, `signing_key`, `private_key`,
`encryption_key` and provider variants are REDACTED.

Scalar strings must additionally pass through a bounded, deterministic line
tokenizer that removes credentials from common structures **without relying on
keyword-bearing samples**: URI/DSN userinfo passwords for any scheme
(`scheme://user:password@host`), sensitive query keys and fragments (`key`,
`api_key`, `token`, `access_token`, `signature`, `sig`, `credential`,
`password` and provider variants), `NAME=value` assignments, CLI
`--name=value` forms, `Name: value` headers and quoted JSON-ish log lines, all
with the same normalized sensitive-name policy. **Any bounded horizontal
whitespace** around separators is recognized (`NAME = value`, `--name =
value`, `Name : value`), so "more than 8 spaces means pass-through" must never
hold; parser state beyond the bounded horizontal-whitespace limit fails
closed as a whole `[REDACTED]` item. **Quoted assignment values are consumed
completely** through the closing quote (`OPENAI_API_KEY = "q7x9opaque
rest8v"` keeps neither the tail nor the quotes); the quoted scanner is
**escape-aware** — a quote terminates the value only when the preceding run of
backslashes is even, so `\\` (escaped backslash) and escaped quotes inside
the value (`OPENAI_API_KEY="q7x9\"rest8v"`) never leave a secret tail; an
unterminated, over-long or state-uncertain quoted value fails closed as a
whole item. **Once a sensitive Header is confirmed, the entire Header value
is consumed to the physical line end** — `{`, `}`, `;`, quotes, commas and
whitespace are NOT early-stop boundaries, so `Authorization: q7x9{rest8v}`,
`Authorization: q7x9}rest8v}` and `X-Api-Key: q7x9;rest8v,more` never keep a
tail (a JSON right-brace is sacrificed rather than risking a secret tail).
Sequences additionally redact **cross-element CLI argument pairs** through an
explicit, deterministic inline-flag state machine: a sensitive flag element
such as `--api-key` redacts the following array element as one whole item
(`["--api-key", "SECRET"]`) **even when that value starts with `-` or `--`**
(`["--api-key", "--q7x9opaque"]`, `["--token", "-opaque"]`,
`["--password", "--"]`); a sensitive flag with no value fails closed on its
own, and a following element that deterministically belongs to another
allowlisted flag — including its inline `--name=value` form
(`--profile=lite`, `--service=backend`) or a sensitive inline flag
(`--token=value`) that belongs to its own structure — is never swallowed —
the flag has no value there and is redacted itself while the other flag's
structure is preserved (`["--api-key", "--profile=lite"]` ->
`["[REDACTED]", "--profile=lite"]`, `["--api-key", "--token=value"]` ->
`["[REDACTED]", "--token=[REDACTED]"]`); unknown or ambiguous state fails
closed.
Provider-key shapes are covered through the value of a sensitive name, never
through guessing secret prefixes. All parsing is bounded and linear (no
nested or unbounded quantifiers, no catastrophic backtracking). Sensitive
Header/JSON/assignment values that exceed the single-item parse limit **fail
closed as a whole item**: the entire item is replaced with `[REDACTED]`, never
a truncated prefix that would leak the tail. `LifecycleResult` stdout/stderr,
status/health/log text, exception text and serialized diagnostics all pass
through this protection.

Capability facts must carry provenance and an evidence state. A hostname is not
network evidence; Docker/Podman/WSL/Hyper-V executable presence is not
hostile-code isolation proof; and Hardened mode stays fail-closed and
`blocked/not_proven` unless independently sealed Runner/Broker/Gateway evidence
is injected and verified. The capability probe and the lifecycle wrapper share
**one container-engine resolution contract** (`resolve_engine_resolution`:
Docker first, then Podman, then `none`) that **never infers Compose Local
capability from `shutil.which` alone**: each candidate runs a bounded,
`shell=False`, short-timeout probe of `docker compose version` /
`podman compose version` whose stdout/stderr are discarded to `DEVNULL` (the
probe needs only the exit code, so a replaced or malicious executable cannot
exhaust memory by streaming huge output before exit), and **only exit 0
declares the compose provider verified**. The probe captures the canonical
absolute path and a stable file identity (stat dev/ino/size/mtime/ctime +
symlink flag) of the verified executable; the lifecycle uses that path as
`argv[0]` and **re-verifies the identity before building any Compose command**,
never re-resolving `PATH` via `shutil.which`, so a TOCTOU that swaps the
`which` result after probe time cannot redirect execution. Deletion,
replacement, symlink/reparse drift or any stat change fails closed
(`container_engine_identity_drift`) before any subprocess. The report
distinguishes `executable_detected` (which presence only),
`compose_provider_verified` (exit-0 probe) and `local_mode_available` (only
when a provider is verified); a Podman executable without a verified compose
provider is reported as `detected`/`not_proven` and Local is never claimed.
The negative matrix covers Docker-only, Podman-only, both present with
compose failing, timeout, not-found, neither present, TOCTOU
trusted-path→replacement-`which`, verified-executable deletion/replacement/
identity drift, and compose-version/probe output overflow, on both the probe
and the lifecycle sides. Subprocess output is bounded **during reading** with
independent per-stream and combined-total byte caps and a
terminated-on-exceed process (never buffered unbounded into memory or a temp
file first and then truncated); timeout and byte caps are two independent
constraints. Evidence from one host is never generalized to another platform.

**Allowed changes**

- Tighten redaction key tokens/suffixes, bounds, deterministic markers or the
  evidence/provenance vocabulary.
- Add attack tests for nested sequences, mixed case, bearer/basic credentials,
  URLs, DSNs, multiline exceptions, cycles, excessive depth/width and oversized
  strings, cross-element CLI argument pairs (including dash-prefixed value
  slots such as `["--api-key", "--q7x9opaque"]` and the allowlisted-flag
  not-swallowed cases), inline `--name=value` flag structures (allowlisted
  `--profile=lite`/`--service=backend` preserved and sensitive
  `--token=value` redacted on its own), wide bounded-whitespace assignment
  forms, quoted assignment values, escaped quotes/backslashes, unterminated
  quotes, header values with `{`/`}`/`;`/comma tails consumed to the physical
  line end, acronym-aware camelCase tokens (`stripeAPIKey`, `OPENAIApiKey`,
  `openAIApiKey`, `azureADAccessToken`, `myTOKEN`, `providerPASSWORD`,
  `xAPIKey`) and the narrowed `_key` suffix rule, asserting forbidden markers
  are absent from structured output and serialized JSON.
  Attack samples must include opaque secrets that contain no token/secret/
  password keyword (URI userinfo, DSN userinfo, sensitive query keys/fragments,
  `NAME=value`, CLI `--name=value` / `--name value`, `Name: value` headers and
  JSON-ish log lines) and must assert absence from both structured results and
  serialized JSON.
- Add focused lifecycle tests that mock the subprocess boundary and prove exact
  argument arrays with explicit `--env-file .env.example` for every verb, no
  shell invocation, profile/service/verb allowlists, Hardened rejection,
  timeout and executable-not-found behavior, bounded/redacted stdout and
  stderr, start bind-failure propagation, `logs --tail` bounds, status/health
  failure behavior, Windows path handling without command injection, that the
  root `.env` is never selected, the container-engine resolution matrix
  (Docker-only, Podman-only, both present with compose failing, timeout,
  not-found, neither present), the verified-absolute-path `argv[0]`/no-`which`
  TOCTOU defense, identity-drift/deleted/replaced rejection, and per-stream/
  total byte-cap truncation during reading, on both the probe and lifecycle
  sides.
- Extend lifecycle verbs only through the allowlisted Compose argument-array
  wrapper with explicit `--env-file .env.example` and the shared
  `resolve_engine_resolution` contract.
- Tighten the engine probe (shorter timeout, explicit stdout/stderr bounding,
  per-engine probe records) as long as only exit 0 of the bounded
  `docker compose version` / `podman compose version` probe declares Compose
  Local available and `executable_detected` / `compose_provider_verified` /
  `local_mode_available` remain distinct facts, and the lifecycle uses the
  verified canonical absolute path as `argv[0]` with identity re-verification
  and byte-bounded output read during reading.

**Forbidden changes**

- Returning secrets embedded in nested sequences, exception representations,
  command arguments, environment values, URLs/query strings, headers or
  connection strings, or leaking a truncated prefix of an oversized sensitive
  item while leaving its tail visible.
- Matching sensitive names by arbitrary substring (which would redact `monkey`,
  `keyboard_layout`, `design` or `session_count`), or keeping a generic `_key`
  suffix rule that would redact `sort_key`, `cache_key` or `foreign_key`.
- Letting "more than 8 spaces means pass-through" escape: bounded horizontal
  whitespace around `=` / `:` separators must be recognized up to the bound,
  and over-limit parser state must fail closed as a whole item.
- Retaining the tail of a quoted assignment value (including after an escaped
  quote that was wrongly treated as a closing quote) or of a confirmed
  sensitive Header (a `{`/`}`/`;`/quote/comma stopping the value early, or
  preserving a JSON right-brace at the cost of leaking a secret tail).
- Tokenizing only at lower/digit -> upper boundaries and missing the
  acronym -> Capitalized word boundary (so `stripeAPIKey` / `OPENAIApiKey`
  never become `stripe_api_key` / `openai_api_key` and leak).
- Swallowing an entire following structure that deterministically belongs to
  another allowlisted flag (including its inline `--name=value` form) or to a
  sensitive inline flag's own structure, or leaving a sensitive flag's
  dash-prefixed value slot visible.
- Inferring network availability from hostname, or claiming Hardened/Local
  capability from executable presence alone (`shutil.which` is never a compose
  provider probe), or letting the probe and lifecycle resolve container
  engines from different contracts, or declaring Compose Local available
  without an exit-0 bounded `compose version` probe.
- Re-resolving `PATH` via `shutil.which` in the lifecycle after probe time
  (a TOCTOU that swaps the `which` result could redirect execution), skipping
  identity re-verification, or using a bare engine name as `argv[0]`.
- Buffering subprocess output unbounded into memory or a temp file and then
  truncating to claim bounded output, or treating timeout as the only output
  bound.
- Building shell command strings from user input, exposing arbitrary command
  execution, or running Compose without `--env-file .env.example`.
- Creating migration `0013`, activating production Runtime, or opening any
  Phase 5 Feature Gate from desktop diagnostics or lifecycle behavior.

**Required verification**

- `backend/tests/test_runtime_capabilities.py`
- `backend/tests/test_runtime_redaction_attacks.py`
- `backend/tests/test_runtime_lifecycle.py`
- `backend/tests/test_rag_performance.py`
- Focused Ruff check/format and Mypy for `backend/src/omnibase/runtime/**` and
  `backend/src/omnibase/rag/performance.py`
- `PYTHONPATH=backend/src python scripts/runtime/omnibase_desktop.py doctor`
- CLI negative test: `start --profile hardened` must be rejected
- Maintainer map and benchmark validators
- Compose config with explicit `.env.example`

**Recovery**

If redaction leaks a secret or a capability fact over-claims from executable
presence, stop use of the diagnostics bundle, fix the redactor/detector in a
new commit, and re-run the attack matrix. Keep Hardened `blocked/not_proven`
and every Phase 5 Feature Gate false.

## INV-053 trust-policy-candidate-governance

**权威源码**

- `backend/src/omnibase/production/trust_policy_candidate.py`
- `backend/tests/test_p34_7_trust_policy_candidate.py`
- `scripts/production/validate_p34_7_trust_policy_candidate.py`
- `deployment/production/p34-7-trust-policy-candidate.example.json`
- `deployment/production/p34-7-trust-policy-approval-packet.example.json`
- `docs/architecture/p34-7-trust-policy-r0.md`
- `docs/runbooks/p34-7-trust-policy-ceremony.md`
- `docs/runbooks/p34-7-trust-policy-rotation-revocation.md`
- `docs/evidence/p34-7/trust-policy-r0-decision.md`

**为何存在**

P34.7 的信任锚是独立安装、独立审批的 trust policy；在 R0 阶段只允许建立
candidate 治理合同，任何 candidate 都不得自我批准、不得由 producer 批准、
不得改变 `joint_gate._APPROVED_TRUST_POLICY_SHA256`（保持空集）。R0 的最高
正向状态是 `candidate/valid_not_approved`；`production_approved`、
`approved_digest_written`、`activation_allowed` 必须恒为 false。

candidate 必须且只能包含七个 producer 角色（core/runner/broker/gateway/
overlay/recovery_sla/sealer），第七个角色 sealer 的密钥不得与任何 producer
共用；七把 Ed25519 公钥必须全部不同、恰好 64 位小写 hex、非全零。每个角色
只能声明自己冻结行的 signing scope（core_runtime_posture、core_runner_
request_identity 等，见 `ROLE_SIGNING_SCOPES`），禁止 wildcard 与任意扩展
scope。producer 不得声明其他 producer 的 scope；sealer 只能签 evidence seal
与 cleanup/seal 边界。

Git source seal 复用 joint gate 的 object-format 语义：`git_object_format`
闭集 `sha1 | sha256`；sha1 恰好 40 位小写 hex、sha256 恰好 64 位小写 hex；
commit/tree 保留原始 Git OID 不二次哈希；unknown format、长度错误、大小写
错误、跨格式 drift 全部 fail-closed。candidate 的 approved_commits/
approved_trees 只是候选 source set，绝不构成 production approval。

任何 DTO 都不得包含 private_key、seed、mnemonic、passphrase、api_key、
bearer token、database password、provider credential、root `.env` locator
等秘密形态字段；递归 forbidden-field 扫描必须覆盖大小写、snake_case、
camelCase、kebab-case 与嵌套对象，命中即 fail-closed 且错误不泄露值。

Approval packet 是独立于 candidate 的外部文件，candidate 不得内嵌自己的
批准根，packet 不得内嵌 trust root；`candidate_policy_raw_sha256` 必须与
candidate 实际原始字节一致——对象级入口（`validate_trust_policy_candidate`）
没有原始字节，永远不得声明 `candidate_digest_verified=true`，只能报告
`candidate/structural_valid` + blocker `candidate_digest_unverified`；只有
文件级入口（`validate_trust_policy_candidate_files`）在完成 raw-byte 校验、
repo-root containment 校验（两个文件都必须解析在仓库根内）和
`candidate_policy_path` 与实际仓库相对 POSIX 路径一致校验后，才能构造
`candidate/valid_not_approved`。CLI 只在 status==candidate/valid_not_approved
时 exit 0。author 不得出现在 reviewer_ids、reviewer 不得重复、reviewer 不得
是任何 producer owner 或 backup owner（producer 级与 key 级）、
review_completed_at 不得早于 review_started_at、review_started_at 不得早于
candidate.created_at、decision 只能在 `draft/candidate/rejected/superseded/
revoked` 闭集内（approved/approved_for_production/production_ready/passed/
published 一律拒绝）；packet.decision 必须等于 candidate.lifecycle_state，
只有 candidate/candidate 组合才产生 `candidate/valid_not_approved`，其余
闭集状态报告 `<lifecycle>/not_approved` + blocker `lifecycle_not_candidate`；
superseded 必须携带完整 supersession link（supersedes_policy_sha256 +
superseded_at + reason）且 packet.supersedes_policy_sha256 一致；revoked
必须携带非空 revocation_records 且 packet.rollback_policy_sha256 非空；
空 decision reason 拒绝。allowed_env_names 按大小写/分隔符归一化后禁止
敏感 token（openai_api_key/OpenAiApiKey/postgres_password/DATABASE_URL/
bearer_token 等），argv 与 env name 中的 root `.env` locator（`/`、`\`、
Windows drive、大小写变体）一律拒绝；artifact_approvals 必须恰好覆盖六个
必需 joint command 各一次（缺项/重复/未知/路径与 key 漂移全部 fail-closed），
且每项 `path` 必须等于其 map key。

密钥生命周期闭集为 `generated/registered/candidate/active/rotating/revoked/
archived`；R0 candidate 文件中的当前密钥状态最多到 candidate，validator 不得
构造新的 active/rotating；合法迁移闭集固定为 generated->registered、
registered->candidate、candidate->rejected|superseded|revoked、
active->rotating|revoked、rotating->active（仅 replacement key）|revoked、
revoked->archived；拒绝 revoked->active、archived->active、rejected->active、
candidate->active、自我替换、rotation cycle、跨角色 replacement、新旧公钥
相同、删除历史 revocation、改写历史 policy bytes 伪装新 candidate。
revoked lifecycle 可达（历史 revoked key 模型）：仅当 candidate
lifecycle_state=="revoked" 时，被 revocation record 引用的历史 key 可声明
lifecycle_state=="revoked"、allowed_signing_scopes 为空（不再持有签名权，
不得出现在 producer signing allowlist）、revocation_record_id 非空；当前
key（generated/registered/candidate）仍必须精确持有自身角色的冻结 scope
矩阵，且非 revoked key 的 revocation_record_id 必须严格为 null（悬空
token 一律 fail-closed）；record 与 revoked key 必须 1:1 闭合绑定（同 role、
同 key_id、同 revocation_record_id、record id 唯一、key 引用唯一、计数相等）；
missing record、duplicate record id、重复 key 引用、record-id/role/key-id
drift、revoked key 保留 scope、非 revoked candidate 嵌入 revoked key、record
指向 candidate key 全部 fail-closed；revoked 仍需 packet.rollback_policy_sha256。
replacement/successor 三者精确一致：RevocationRecord.superseded_by_key_id
（被取代的 revoked key 视角）、successor key 的 replaces_key_id（successor
视角）、rotation entry 的 replaces_key_id（被替换 key 视角）——任一非空时
其余声明必须一致；successor 必须真实存在、同 role、非 self、非 revoked/
archived、公钥不同；unknown/self/cross-role/revoked/same-public-key/drift
全部拒绝。revoked role 的 key 结构闭合：单 key（revoked）= 无 successor
的历史 key（record.superseded_by_key_id 必须 null，不得有 successor
registration 或 replacement plan 指向它）；双 key = 恰好 1 revoked + 1
successor，且三处绑定必须齐全（record 必须指名第二把 key、第二把 key 的
replaces_key_id 必须指回、revoked key 的 rotation entry 必须存在并指名
successor），任何缺失或"无关系第二把 key"一律 fail-closed。successor 在
revocation event 时已生效：lifecycle_state 必须为 candidate、created_at <=
candidate_from <= revoked_at、planned_expiry 为 null 或严格晚于 revoked_at
（candidate_from == revoked_at 允许）。revoked key 的 current-state rotation
entry：planned_at >= 匹配 RevocationRecord.revoked_at（inclusive）。
rotation plan 冻结为当前状态直接转换语义：entry.from_state 必须精确等于
key.lifecycle_state；每个 key_id 至多一条 entry（完全/部分/冲突重复全部
拒绝）；planned_at 必须落在 key 有效窗口内（max(candidate.created_at,
key.created_at, key.candidate_from) <= planned_at < planned_expiry，
planned_expiry 非空时，下界 inclusive 上界 exclusive）；key-level 与
plan-level replaces_key_id 必须引用真实、同 role、不同 key 与公钥并双向
精确一致。key registration 完整有效区间：created_at <= candidate_from <
planned_expiry（planned_expiry 非空时严格）；key.created_at 不得晚于
policy candidate.created_at；candidate/revoked key 的 candidate_from 不得
晚于 candidate.created_at（generated/registered key 允许未来 candidate_from，
仅表示计划，不声称已进入 candidate）；所有时间戳必须是显式 UTC instant
（Z/+00:00，非零 offset 视为歧义拒绝）。
生命周期时间顺序闭合：superseded_at / 每条 record 的 revoked_at 必须落在
review window 内（review_started_at <= event <= review_completed_at）且不早于
candidate.created_at；所有比较在归一化 UTC datetime 上进行，边界 inclusive
（等价 UTC instant 允许）。command 模板内部 command 必须精确等于其 map key
（六个 map key 与六个内部 command 各自形成 _REQUIRED_COMMANDS 精确闭集，
swap/内部重复/缺失/未知全部拒绝，重算全部 digest 也不能绕过）；同一
artifact 内 command 重复（["core_runner","core_runner"]）与跨 artifact 重复
覆盖都拒绝；allowed_env_names 在 frozenset 转换前拒绝重复值（section
digest 绑定重复列表也不能接受）。custody_kind 只是计划性元数据
（operator_offline/hsm_planned/kms_planned/remote_runner_local/
external_signing_service_planned），不得当作实际 HSM/KMS 证明；未真实证明
的 custody posture 必须报告 not_proven。

**允许的改法**

- 扩展 candidate/packet 合同字段，同时保持闭集解析与秘密字段扫描不降级。
- 增加新角色或 scope 时必须在同一 change 中更新冻结矩阵与全部测试。
- 为未来的真实 key ceremony 增加独立审批流程文档，不改变 R0 的
  candidate-only 边界。

**禁止的改法**

- 让任何 candidate 或 packet 产生 approved/passed/published 或
  activation_allowed=true。
- 向 `_APPROVED_TRUST_POLICY_SHA256` 写入任何 digest（含示例/测试 digest）。
- 生成、打印、提交或上传真实或占位私钥；放宽秘密字段扫描。
- 放宽七角色闭集、scope 矩阵、object format、digest、lifecycle、轮换/
  撤销、approval separation 中任何一项。
- 创建 migration 0013 或打开任何 Phase 5 Feature Gate。

**必须运行的测试**

- `backend/tests/test_p34_7_trust_policy_candidate.py`（负向矩阵：缺失/第八
  角色、重复/全零/畸形 key、秘密字段、wildcard/越权 scope、object format
  drift、raw-digest/canonical-bytes bypass、lifecycle/decision binding、
  command map-key swap（含全 digest 重算文件级）、supersession/revocation
  完整性（1:1 record-key 绑定、双 key role 强制 successor 三方绑定、单 key
  role 禁止 successor、successor event 有效性、revoked_at/planned_at 顺序、
  非 revoked key 悬空 record id、rotation 语义：entry 唯一/from_state drift/
  planned_at 窗口/replaces 双向绑定、完整 key 有效区间、key-policy 时间
  绑定）、repo containment/packet path binding、artifact coverage 闭合
  （含 artifact 内 command 重复）、env allowlist 重复、backup owner
  approver、敏感 env name、路径/link 攻击、migration/Feature Gate posture；
  正向：七角色唯一、真实 SHA-1 main commit/tree 进入 source seal、文件级
  raw-byte digest 验证、身份分离、lifecycle candidate、revoked/not_approved
  文件级正向控制（含单 key 无 successor、双 key 完整绑定、successor 边界
  等价 instant）、合法 rotation 正向控制、generated/registered 未来
  candidate_from、planned_expiry null、`candidate/valid_not_approved`、
  production Gate 仍 blocked/not_proven）
- `python scripts/production/validate_p34_7_trust_policy_candidate.py --candidate
  deployment/production/p34-7-trust-policy-candidate.example.json
  --approval-packet deployment/production/p34-7-trust-policy-approval-packet.example.json
  --validate-only`（预期 exit 0、candidate/valid_not_approved；只有该 status
  exit 0，structural-only 或非 candidate 状态一律 exit 1）

**失败恢复**

candidate 或 packet 出现 drift/违例时：冻结 candidate，保留 packet 与
历史记录取证，从新的 clean checkout 重新验证；不得删除 veto、不得把
candidate 改成 approved、不得写入 approved digest、不得打开 Runtime。

## INV-054 trust-policy-r1-assignment

**权威源码**

- `backend/src/omnibase/production/trust_policy_r1_assignment.py`
- `backend/tests/test_p34_7_trust_policy_r1_assignment.py`
- `scripts/production/validate_p34_7_trust_policy_r1_assignment.py`
- `deployment/production/p34-7-trust-policy-r1-assignment.example.json`
- `docs/architecture/p34-7-trust-policy-r1-assignment.md`
- `docs/evidence/p34-7/trust-policy-r1-assignment-decision.md`
- `docs/p34-7-trust-policy-r1-preparation-plan.md`

**为何存在**

R0 证明了候选策略文件的结构、原始字节、七角色、scope、命令、artifact、
时间线与轮换/撤销合同，但 logical reviewer label、custody 计划字符串和资源
名称都不是现实身份认证、托管证明或生产环境证据。R1-A 因此必须把 authority、
custody、目标环境资源以及 P34.7 的 11 个 blocker 变成独立、离线、严格闭集
的机器可读 assignment 合同。在真实人员、服务、托管设施和目标环境被独立
验证前，所有事实必须保持 `UNASSIGNED` 或 `NOT_ASSESSED`，不能从当前用户、
Codex、外部 AI、本机 Docker/WSL、mock、test double 或 disposable fixture
猜测填充。

合同必须且只能包含：两名 policy reviewer、七个 producer owner、七个 backup
owner、ceremony operator、两名 observer、七个 custody attestation issuer、
digest-change approver 与 incident/revocation authority；七个 custody role；15
个目标环境资源槽；11 个 production blocker。unknown、缺失、重复或第八角色
一律 fail-closed。真实 assignment 使用 canonical subject 与认证凭据摘要比较，
不能只比较 display label；author/reviewer/producer/backup、operator/observer、
digest approver、incident authority 和每个 custody issuer 的分离矩阵必须通过。
`UNASSIGNED` 不得携带真实 identity、subject 或认证引用；R1-A v1 只允许把真实
slot 填到 `ASSIGNED_NOT_VERIFIED`。输入自报的 `VERIFIED` 即使携带格式正确的
content-addressed 引用也必须拒绝，因为 proposal 没有 independently pinned
authority registry 或 detached review receipt verifier。分离矩阵通过只能派生
`authority_separation_contract_valid=true`，不能派生现实身份认证或已验证分离。

目标环境状态闭集固定为 `NOT_ASSESSED | MISSING | PLANNED |
AVAILABLE_NOT_PROVEN | EVIDENCE_COLLECTED_NOT_REVIEWED | PROVEN | REJECTED`。
`AVAILABLE_NOT_PROVEN` 或 `EVIDENCE_COLLECTED_NOT_REVIEWED` 永远不得算作
`PROVEN`；R1-A v1 不接受任何输入自报的 resource/blocker `PROVEN`，也不接受
`production_equivalent=true`。这些结论属于后续独立签名 evidence gate，而非
assignment proposal。Overlay member A/B 与 independent DERP 必须位于不同
security domain；non-disposable tenant/RAG 必须有独立 data-owner authority；
Docker、WSL、mock、fixture、test double 或 disposable 环境不得出现在任何已
填写的目标资源 assignment 中，更不得冒充 production resource。11 个 blocker
中 Overlay 的真实双成员、compromise/rejoin、双独立
签名必须保持三项独立事实，即便下游 composition 目前聚合一个 evidence ID。

文件入口只接受仓库内 regular、non-link、non-reparse、canonical UTF-8 JSON
bytes；复用 R0/joint-gate 的 strict parser、secret scanner、migration discovery
与路径规则，不复制一套会漂移的低层语义。任何 private key、seed、mnemonic、
passphrase、API key、bearer token、数据库 credential、provider credential 或
root `.env` locator 都必须拒绝且错误不得泄露值。CLI 不访问网络、数据库、
业务存储或目标环境，不启动服务，不执行 key ceremony，不收集 production
evidence。

R1-A 的最高状态只允许 `r1_assignment/complete_not_authenticated`；它只表示
authority slots、custody choices 和 target inventory 已填写，仍然不是现实身份
认证、独立 review、Trust Policy approval、approved digest installation、P34.7
PASS 或 Runtime activation。无论是 `valid_incomplete` 还是
`complete_not_authenticated`，报告都必须固定：
`authority_separation_verified=false`、
`authority_authentication_verified=false`、
`independent_review_receipts_verified=false`、
`custody_attestations_verified=false`、
`environment_evidence_verified=false`、
`production_blockers_closed=false`、`trust_policy_approved=false`、
`approved_digest_written=false`、`key_ceremony_authorized=false`、
`production_evidence_authorized=false`、`activation_allowed=false`、P34.7
`blocked/not_proven`。`_APPROVED_TRUST_POLICY_SHA256` 保持空集，migration
head 保持 `0012`，`0013` 不存在，三个 Phase 5 Feature Gate 保持 false。

**允许的改法**

- 在不放宽 closed set、identity separation、canonical bytes、秘密扫描和
  non-authorizing 状态语义的前提下扩展 proof requirements。
- 增加未来独立 authority registry 与 detached review receipt 合同；registry
  的 trust pin 必须是另一项独立批准，不能由 proposal 自带。
- 为真实 R1-B ceremony runbook 做单独设计，但执行必须另获明确批准。

**禁止的改法**

- 把 logical label、placeholder custody、fixture、端口可达或容器存在当作现实
  identity/custody/production proof。
- 让输入中的 `ready`、`approved`、`passed` 或 activation 布尔值决定派生状态。
- 仅凭 proposal 自带的 identity/custody/evidence digest 接受 `VERIFIED`、`PROVEN`
  或 production equivalence。
- 写入 approved trust-policy digest、生成/显示/提交私钥、创建 migration 0013、
  打开 Feature Gate、启动 Runtime，或访问非 disposable 目标环境/业务数据库。

**必须运行的测试**

- `backend/tests/test_p34_7_trust_policy_r1_assignment.py`
- R0 candidate 与 P34.7 joint-gate 回归
- 新模块 Mypy 与显式路径 Ruff check/format
- Maintainer map/benchmark validator
- P5.1A/P5.2A/P5.3A sealed contract 回归；修改 maintenance map 或本文件时按
  raw-byte SHA-256 顺序重封 registry -> task-ledger -> planner 合同

**失败恢复**

任何 assignment、custody、resource、blocker、canonical byte 或 repository
posture 漂移时，保留原 proposal 取证，从新 clean checkout 建立 forward-fix；
不得通过改写历史、删除 blocker、伪造 reviewer 或启用 Runtime 来获得 ready。

## INV-056 personal-runtime-canary-activation

**Authority sources**

- `backend/src/omnibase/production/personal_runtime_activation.py`
- `backend/src/omnibase/production/personal_owner_gate.py`
- `backend/src/omnibase/production/__init__.py`
- `backend/src/omnibase/agent_alpha/personal.py`
- `backend/src/omnibase/agent_alpha/adapters.py`
- `backend/src/omnibase/agent_alpha/router.py`
- `backend/src/omnibase/agent_alpha/schemas.py`
- `backend/src/omnibase/agent_alpha/service.py`
- `backend/src/omnibase/task_ledger/service.py`
- `scripts/production/manage_p5_personal_runtime.py`
- `Makefile`
- `docker-compose.yml`
- `docker-compose.destructive-tests.yml`
- `.env.example`
- `deployment/production/personal-runtime-canary.example.json`
- `deployment/production/personal-runtime-canary.compose.example.yml`
- `frontend/app/(dashboard)/agents/page.tsx`
- `frontend/lib/api.ts`
- `frontend/lib/personal-runtime-gate.ts`
- `docs/architecture/p5-personal-runtime-activation-r0.md`
- `docs/runbooks/p5-personal-runtime-canary.md`

The personal edition production canary is an exact closed scope: one live
Tenant, one Workspace, exactly one active Owner who is also tenant
administrator, one AgentVersion and one interactive no-tool invocation. It
requires production environment, Runtime=true, Planner=false,
Multi-Agent=false, migration 0012 and no migration 0013.

The canonical config binds all scope UUIDs, concurrency=1, a bounded lifetime,
the `top_k` ceiling, default-deny network with no destinations, no external
side effects and the raw digest of the Personal Owner readiness config.
Unknown fields, non-canonical bytes, unsafe flags, path escape or readiness
drift fail closed. Activation requires exact confirmation of the deterministic
plan SHA-256.

Every activation uses a new absolute run-scoped directory. Activate and
rollback events form a canonical append-only hash chain. Rollback and expiry
never reopen the directory. `KILL_SWITCH.json` is independent, irreversible
and wins before ledger parsing, including when the marker or ledger is corrupt.
The chain is an integrity/lifecycle receipt, not an external signature or
separate authenticity root.

Every Browser request independently binds config/plan/ledger, feature gates,
migration, Tenant/Workspace/Owner/AgentVersion and Model Gateway. The exact
live Owner, tenant-admin and AgentVersion scope is rechecked inside Task Ledger
transaction A before reservation. Invalid profile tokens may not fall back to
engineering Lite. Public status exposes no credentials, Approval/Capability,
lease/fencing, physical locator or workload identity material.

This R0 authorizes no tools, Planner, Multi-Agent, Sandbox hostile code, shell,
SQL, arbitrary HTTP, MCP, Skills or workload network destination. It uses the
Core-owned read-only RAG adapter, not formal P5.4B Capability Gateway Browser
composition. The full Personal Owner Approval/Capability Gate remains required
for future high-risk or Sandbox execution.

**Required tests**

- config/plan closed set, canonical bytes, readiness seal and unsafe
  gate/network/side-effect attacks;
- activation, expiry, rollback, event tamper, unknown artifact, path attacks
  and corrupt-ledger kill behavior;
- exact/empty/invalid Router profile selection, inactive/wrong-scope/active
  posture and feature-gate drift;
- disposable PostgreSQL verification of migration 0012, one Owner,
  transaction-A revalidation and durable no-tool convergence before any
  production claim;
- frontend exact-conjunction and login-401 preservation, Mypy, explicit Ruff,
  full non-integration, maintainer validators, Compose default-off rendering
  and sealed P5 contract regression.

**Failure recovery**

Write the kill marker, remove the operator overlay or restore Runtime=false,
and preserve all config/event/kill bytes. Never edit an event, delete a kill
marker, reuse a terminal directory, create migration 0013, enable Planner or
Multi-Agent, install an enterprise approved digest or convert unknown to
success. Reactivation requires a new reviewed config/plan and state directory.

## INV-057 personal-production-target-recovery

**Authority sources**

- `backend/Dockerfile.production`
- `frontend/Dockerfile`
- `deployment/personal-production/compose.yml`
- `deployment/personal-production/operator.env.example`
- `scripts/production/manage_p5_personal_target.py`
- `scripts/production/manage_p5_personal_backup.py`
- `docs/architecture/p5-personal-production-target-r1.md`
- `docs/runbooks/p5-personal-production-target.md`

The personal production target has exactly one loopback host entrypoint: the
frontend. PostgreSQL, Redis, MinIO and the backend have no host-published port.
There are no source bind mounts. Production application images run non-root,
without reload, and the final backend image contains no compiler, Git or
download tool. Runtime is false in the base target; Planner and Multi-Agent are
false in every personal-target base state.

Operator secrets and writable state live outside Git. The target doctor must
validate an exact env key set, secret posture without echoing values, host path
and ACL/permission boundaries, PostgreSQL/Redis service-coordinate binding,
loopback CORS binding, migration `0012`, absence of `0013`, clean public Git
provenance and byte digests of the production packaging/controllers. A release
receipt is not valid if it binds the development Compose or Dockerfile instead.

A recoverable release treats PostgreSQL and MinIO as authoritative and Redis as
transient. Cold backup happens after admission/writer stop and binds the release
receipt, PostgreSQL custom dump, complete MinIO inventory and personal Runtime
config/state/readiness assets. Restore creates a new `omnibase_restore_*`
database and a new MinIO root. It never overwrites the source, restores Redis as
authority, replays ambiguous work, or activates Runtime on the restored target.

An upgrade is A-to-B with a verified cold-backup barrier. B uses new target
identities, starts with Runtime=false, runs migration/structural verification and
authenticated product smoke, and requires an explicit Owner cutover. A and its
backup remain recoverable until B is accepted.

**Required tests**

- packaging source tests and rendered Compose exposure/gate assertions;
- target controller attacks for path/link/ACL, secret/env binding, dirty or
  non-public Git provenance and receipt drift;
- backup controller attacks for path escape, symlink/junction, duplicate or
  unknown inventory, unrecorded files, digest drift, Redis archive and
  restore-in-place;
- final production image inspection, first-boot health, stop/restart,
  cold-backup/restore-new and A-to-B upgrade rehearsals before a production
  closure claim;
- explicit Ruff, Mypy where applicable, maintainer map/benchmark, frontend
  production build and wider backend regression.

**Failure recovery**

Restore Runtime=false and keep Planner/Multi-Agent false. Preserve release,
image, lifecycle, backup and failure receipts. Do not edit a manifest into a
passing state, overwrite the old database/MinIO root, delete unknown effects or
reuse a terminal Runtime state directory. Recover with a forward fix or a new
verified restore target.

## INV-058 p55a-memory-scope-provenance-budget-contract

**Authoritative source**

- `backend/src/omnibase/production/phase5_memory_contract.py`
- `deployment/production/phase5-memory-contract.example.json`
- `scripts/production/validate_p5_5a_memory_contract.py`
- `backend/tests/test_p5_5a_memory_contract.py`
- `docs/phase-5-memory-context-capsule-contract.md`
- `docs/runbooks/memory-privacy-delete-export.md`

P5.5A is the compile-only contract for Memory Policy, ContextCapsule and
MemoryCandidate. It creates no migration, ORM, database table, Browser API,
vector lane, worker or Runtime injection. Migration head stays `0012`, migration
`0013` stays absent and Runtime/Planner/Multi-Agent stay false.

The contract vocabulary permits a zero-item, zero-token Capsule only as the
P5.9P first-Memory bootstrap audit anchor. Its sensitivity summary is all zero,
its identity/provenance/TTL bindings remain complete, and it grants no
authority. A non-empty Capsule keeps the normal item, token and digest closure.

Every Capsule is bound to one exact Tenant, human Owner, Workspace,
AgentVersion, Task and Invocation. Selected Memory identities and versions are
unique and ordered by continuous server-owned positions. Each item binds the
same Tenant/Owner, a logical source Resource/version, evidence references,
content digest, scope, sensitivity, selection reason and token count.
`user_private` carries no Workspace or AgentVersion; `workspace_private` and
`controlled_shared` bind the Capsule Workspace without an AgentVersion;
`agent_private` binds both. A controlled-shared item additionally binds a
canonical Owner approval record by ID and digest to the exact Tenant,
Workspace, Memory ID/version and content digest. Capsule TTL,
token/item/sensitive-item totals and compiler policy digest are independently
recomputed. Capsules are non-delegable and untrusted data and cannot override
the Security Kernel.

An Agent may create a Candidate but may not activate a long-term memory.
Secrets, active-memory IDs and inferred biometric/financial/health/political/
religious/sexual-orientation attributes are rejected. Sensitive and
`controlled_shared` candidates require explicit Owner confirmation. Every
Candidate carries source Resource/version, evidence references, retention,
confidence, scope and sensitivity, and must bind the exact same Tenant, Owner,
Workspace, AgentVersion, Task, Invocation and policy as an existing Capsule.

**Allowed changes**

- Tighten closed scopes, sensitivity vocabulary, identity/provenance binding,
  timestamp rules or server-owned budget ceilings.
- Add attack fixtures or example Capsules/Candidates that remain digest-only,
  contract-only and non-authoritative.
- Implement P5.5B persistence only as a separately reviewed migration/service
  increment that also updates every migration-pinned personal/Phase-5 Gate and
  proves delete/export/restore-new behavior.

**Forbidden changes**

- Cross-Tenant, cross-User, cross-Workspace or cross-Agent private selection;
  unsealed or mismatched controlled-shared review evidence; tenant-wide
  fallback; physical locator, Provider credential or Authorization material.
- Treating Memory/RAG/user content as system instructions, delegable authority
  or a way to expand AgentVersion/Skill/tool capability.
- Silent full-history injection, caller-expanded budget, missing source/version,
  duplicate identities, digest/accounting drift or renewable expired Capsule.
- Agent self-publication of a Candidate, automatic sensitive profiling, hidden
  deletion failure or converting `pending|unknown` into success/replay.
- Creating migration `0013`, Memory persistence/API/runtime or enabling any
  Phase 5 Feature Gate under the P5.5A contract-only label.

**Required verification**

- `backend/tests/test_p5_5a_memory_contract.py`
- `python scripts/production/validate_p5_5a_memory_contract.py --validate-only`
- clean-checkout `--verify`, expected `blocked/not_proven` and exit 2
- explicit Ruff and Mypy for the module/test/CLI
- maintainer map/benchmark and P5.1A/P5.2A/P5.3A sealed-contract regression

**Failure recovery**

Keep all Phase 5 Feature Gates false, preserve the invalid config/report and
forward-fix from a new clean checkout. If a future persistence or deletion
operation is ambiguous, immediately block selection and injection, preserve
append-only Audit/tombstone evidence and use restore-new; never edit old
Capsules or destructively downgrade a populated database.

## INV-059 p55b-memory-persistence-delete-export-contract

**Authoritative source**

- `backend/src/omnibase/migrations/versions/0013_memory_context_capsules.py`
- `backend/src/omnibase/migrations/versions/0015_p5_9p_empty_context_capsules.py`
- `backend/src/omnibase/agent_memory/models.py`
- `backend/src/omnibase/agent_memory/service.py`
- `backend/src/omnibase/control_plane/service.py`
- `scripts/production/manage_p5_personal_backup.py`
- `backend/tests/test_p5_5b_memory_migration_contract.py`
- `backend/tests/test_p5_9p_empty_context_capsules_migration_contract.py`
- `backend/tests/test_p5_5b_memory_service.py`
- `backend/tests/integration/test_p5_5b_memory_persistence_foundation.py`
- `scripts/production/test_manage_p5_personal_backup.py`
- `docs/phase-5-memory-context-capsule-contract.md`
- `docs/runbooks/memory-privacy-delete-export.md`
- `docs/evidence/p5-5/memory-persistence-r0-decision.md`

P5.5B advances the reviewed repository and personal production migration head
to `0013_memory_context_capsules.py`; migration `0014` or higher remains
unreviewed and must fail closed. Runtime, Planner and Multi-Agent remain false.
P5.5B creates no Browser Memory endpoint, compiler/search worker, prompt
injection path or production Runtime authority. P5.5C is a separate increment.

An Agent may create only a Candidate. Candidate acceptance must bind the exact
source Capsule, Task, Agent Definition, Tenant, Workspace, logical Resource and
version. The high-risk `memory.candidate.accept` Operation requester is the
exact `task.agent_definition_id`; the sole live human Owner, who is also the
live tenant administrator and active Workspace Owner, is the only decider. The
Approval must bind the same Operation/request hash, be decided by that Owner
and be atomically consumed through the existing Control Plane lifecycle.

Publication is a caller-owned atomic transition. Candidate acceptance,
Memory/first-version insertion, publication Effect and append-only Audit must
close together, and the Candidate-to-Memory-to-Version deferred constraints
must be forced to immediate verification before the service returns. A
controlled-shared Memory or Capsule item additionally requires exact current
Owner Review evidence bound to the same Tenant, Workspace, Memory, version and
content digest; an arbitrary review identifier is never sufficient.

Deletion is an atomic privacy lifecycle. It blocks selection, binds one
committed delete Effect, creates a code-only tombstone, erases accepted
Candidate ciphertext/nonce, deletes every MemoryVersion and both vector lanes,
then leaves only a deleted Memory identity with `current_version=NULL` plus
append-only Audit. Pending or unknown outcomes stay blocked for reconciliation
and are never silently converted to success or automatically replayed. Export
is Owner-initiated and contains logical metadata and digests only; plaintext,
ciphertext, nonce, vector values, physical schema/table/column/object locators,
database URLs and Provider credentials are forbidden.

Cold backup is one barrier-bound evidence system. The dump must be created
first while writers are stopped. `capture-postgres-inventory` is the only
online backup-controller command, requires an explicitly injected
`DATABASE_URL`, executes a repeatable-read read-only transaction and must never
load or print the root `.env`. Its canonical inventory binds the exact dump
SHA-256, global and tenant migration heads, server-owned tenant registry/schema
mapping, ten Memory tables, required semantic and tenant-schema triggers, and
the `vector(1024)`/`vector(1536)` lanes. Offline sealing binds those inventory
bytes. Restore verification uses a distinct `omnibase_restore_*` database and a
new `restore_new_evidence` inventory; source evidence is never edited or
relabelled.

The current personal head is `0016`. Migration `0015` changes only
`context_capsules_tokens_check` so `total_tokens=0` is valid while
`max_tokens>=1` remains mandatory. Backup and restore bind the raw bytes of
`0013`, `0014`, `0015` and `0016`; the reviewed compatibility entries are the
closed `0014 -> 0015` Memory-bootstrap upgrade and the personal-model-settings
`0015 -> 0016` upgrade. Migration `0015` downgrade refuses when any zero-token
Capsule exists. Migration `0017+` remains absent.

**Allowed changes**

- Tighten migration triggers, ORM checks, transaction ordering, live Owner or
  requester binding, logical export vocabulary and backup inventory closure.
- Add attack cases, restore-new evidence or vector-lane versions through a new
  reviewed migration and versioned evidence format.
- Implement P5.5C compiler/search/injection only as a separate bounded change
  that preserves this persistence and privacy lifecycle.

**Forbidden changes**

- Agent self-acceptance, inactive/non-Owner approval, Operation/Approval/
  requester/request-hash cross-wiring or publication that escapes the same
  transaction.
- Mutable accepted content without a new version/effect/audit, uncontrolled
  shared selection, content-bearing tombstones, hidden deletion failure or
  physical locators/secrets in export, logs or errors.
- Caller-provided tenant schemas, partial tenant inventory, dump/inventory
  drift, online sealing, reuse of source inventory as restore evidence, or
  destructive in-place downgrade of populated `0013` data.
- Browser API, compiler, search, injection, Runtime/Planner/Multi-Agent
  activation or any successor migration under the P5.5B label; reviewed
  `0014` and `0015` remain separately authorized increments.

**Required verification**

- migration/service/control-plane/backup focused tests and explicit Ruff/Mypy
- guarded disposable PostgreSQL P5.5B Gate, including formal service journey,
  cross-wire attacks, delete rollback and live inventory capture
- personal backup source/restore-new attacks and canonical inventory checks
- full non-integration backend regression, frontend production checks, Compose,
  maintainer map/benchmark and `git diff --check`
- final-byte P5.1A/P5.2A/P5.3A reseal and clean-HEAD Phase 5/P34 regressions

**Failure recovery**

Keep Runtime, Planner and Multi-Agent false. Preserve Candidate, Operation,
Approval, Effect, Audit, tombstone, backup and restore evidence. Revoke or block
the affected Memory surface, require a new exact Owner decision for a new
request and use a forward fix or restore-new database. Never repair by editing
append-only history, restoring erased content into the old identity, marking
unknown deletion as committed or downgrading a populated business database.

## INV-060 p55c-memory-compiler-runtime-boundary

**Authoritative source**

- `backend/src/omnibase/agent_memory/compiler.py`
- `backend/src/omnibase/agent_memory/crypto.py`
- `backend/src/omnibase/agent_alpha/contracts.py`
- `backend/src/omnibase/agent_alpha/service.py`
- `backend/src/omnibase/agent_alpha/personal.py`
- `backend/src/omnibase/core/config.py`
- `backend/tests/test_p5_5c_memory_compiler.py`
- `backend/tests/test_agent_alpha.py`
- `backend/tests/test_agent_alpha_personal.py`
- `backend/tests/test_agent_alpha_personal_router.py`
- `backend/tests/integration/test_p5_5c_memory_runtime.py`
- `docs/phase-5-memory-context-capsule-contract.md`
- `docs/runbooks/memory-privacy-delete-export.md`
- `docs/evidence/p5-5/memory-runtime-r0-decision.md`

P5.5C enables bounded Memory compilation only inside the exact INV-056 personal
single-Owner canary composition. Runtime remains false by default, Planner and
Multi-Agent remain false everywhere. The current personal repository head is
`0016`; `0013` still owns Memory persistence, `0014` owns instruction Skills,
`0015` owns only the empty-Capsule token lower bound, and `0016` owns only
user-scoped per-role model preferences. Migration `0017+`, Browser
Memory CRUD, tools, shell, SQL, arbitrary HTTP, MCP, workflow/script Skill
execution and enterprise Runtime authority are not created by this increment.

The compiler may select only committed, active, non-deleted Memory at its exact
current version. Every read revalidates the live Tenant and server-owned tenant
schema, the same active tenant-admin Owner, the exact Workspace and Owner
membership, the sealed AgentVersion, and the current running Task/Invocation.
The four scope shapes remain closed: `user_private` is Owner-wide with no
Workspace or AgentVersion, `workspace_private` and `controlled_shared` bind the
exact Workspace with no AgentVersion, and `agent_private` binds the exact
Workspace and AgentVersion. Controlled-shared selection additionally requires
current approved Owner review evidence bound to the same Memory/version/content
digest.

Selection is deterministic and bounded before decryption or prompt projection.
The database candidate set is capped, Candidate retention/TTL and current
version are rechecked, ordering is stable, and the P5.5A item/token/sensitive
budgets are enforced. Memory content uses an independent AES-256-GCM key and
domain-separated authenticated data bound to Tenant, Owner, Workspace,
AgentVersion, historical Task/Invocation, policy, source Resource/version,
content digest and key version. Decryption, UTF-8, plaintext-size or SHA-256
ambiguity fails the whole compilation; it never falls back to unverified text.

For a fresh invocation, `ledger.begin()` reserves the exact request hash,
including the Memory policy digest, before compilation. The exact
ContextCapsule and contiguous item rows are persisted and committed before the
provider boundary. Exact terminal replay calls neither compiler, RAG nor
provider and creates no second Capsule. A compiler failure terminalizes the
reserved invocation as `failed/agent_alpha_memory_compile_failed`; provider or
disconnect ambiguity continues to use the existing unknown/reconciliation
lifecycle and must never be replayed as success.

When the selected set is empty on a fresh invocation, the compiler must still
persist and commit exactly one zero-item/zero-token Capsule, then return no
Memory projection. It must not add an empty prompt message or Memory SSE
metadata. That audit Capsule is the exact source binding for the first real
MemoryCandidate. Faking a Memory, writing one token, weakening Candidate binding
or creating a second Capsule on exact replay is forbidden.

Memory plaintext exists only in the in-process prompt projection. It is a
separate system message explicitly labelled untrusted reference data below the
Platform Security Kernel and AgentVersion instructions; text inside it can
never grant authority or become executable instructions. SSE metadata may
expose only Capsule ID, canonical digest and item count. It must not expose
plaintext, Memory/version/source/review identities, encryption material,
physical locators or internal provenance.

**Allowed changes**

- Tighten exact-scope selection, cryptographic binding, deterministic ranking,
  policy budgets, transaction ordering, replay handling and safe prompt
  projection.
- Add focused attacks or disposable migration-0013/0015 journeys without widening
  Runtime, Planner, Multi-Agent, tool or network authority.
- Add a Browser governance surface only as a separately reviewed Owner-scoped
  increment; the compiler itself never becomes a public search endpoint.

**Forbidden changes**

- Cross-Tenant/Owner/Workspace/AgentVersion selection, stale/deleted/non-current
  Memory, uncontrolled shared Memory, missing live review evidence or caller-
  supplied physical locators.
- Using Provider/JWT keys as the production Memory key, unauthenticated
  decryption, plaintext persistence/logging/SSE, or treating Memory as trusted
  instructions.
- Compilation before durable reservation, provider dispatch before Capsule
  commit, compile-on-replay, a second Capsule on exact replay, or leaving a
  compiler failure in a running ledger state.
- Enabling Runtime outside the exact personal canary, enabling Planner or
  Multi-Agent, modifying the separately owned Skill migration `0014`, creating
  unauthorized migration `0017+`, or smuggling tool/Skill/MCP/HTTP/SQL authority
  through Memory content.

**Required verification**

- focused compiler attacks and Agent Alpha request-hash/replay/failure/meta tests
- personal composition tests for exact canary-only compiler injection
- one guarded disposable PostgreSQL migration-0013 journey proving encrypted
  Memory selection, Capsule/item persistence, untrusted prompt injection,
  incremental SSE and cancellation convergence
- changed-path Ruff/format, Mypy, Compose config, maintainer map/benchmark and
  `git diff --check`; required GitHub CI is the full regression authority

**Failure recovery**

Disable the personal Memory compiler composition and restore Runtime=false
outside the disposable canary. Preserve append-only Capsule, ledger and Audit
evidence and block the affected Memory scope. Never replay an unknown provider
outcome, restore erased plaintext, edit an old Capsule, relax exact scope or
fall back to unencrypted/unreviewed Memory. Use a forward fix or restore-new
database and keep Planner/Multi-Agent false.

## INV-061 p56p-personal-instruction-skill-runtime-boundary

**Authoritative source**

- `backend/src/omnibase/agent_skills/models.py`
- `backend/src/omnibase/agent_skills/service.py`
- `backend/src/omnibase/agent_skills/resolver.py`
- `backend/src/omnibase/migrations/versions/0014_p5_6p_personal_instruction_skills.py`
- `backend/src/omnibase/agent_alpha/contracts.py`
- `backend/src/omnibase/agent_alpha/service.py`
- `backend/src/omnibase/agent_alpha/personal.py`
- `backend/tests/test_p5_6p_instruction_skills.py`
- `backend/tests/test_p5_6p_instruction_skills_migration_contract.py`
- `backend/tests/test_agent_alpha.py`
- `backend/tests/test_agent_alpha_personal.py`
- `backend/tests/test_agent_alpha_personal_router.py`
- `backend/tests/integration/test_p5_6p_personal_instruction_skill_runtime.py`
- `docs/architecture/p5-6p-personal-instruction-skills-r0.md`
- `docs/runbooks/skill-review-revoke-rollback.md`
- `docs/evidence/p5-6/personal-instruction-skills-r0-decision.md`

P5.6P is the personal-edition successor to the historical P5.6A compile-only
contract. It authorizes only first-party, sealed, instruction-only Skills for
one live human Owner and one exact installed Workspace/AgentVersion binding.
It does not authorize Browser Skill CRUD, workflow/script execution, tools,
Capability expansion, network access, secrets, MCP, Marketplace, Planner,
Multi-Agent or enterprise P34.7 activation.

Migration `0014` stores global-control-plane Skill definitions, immutable
versions and installation history. The service and database trigger must both
revalidate the active Tenant and server-owned tenant schema, active
tenant-admin Owner, Workspace ownership and active Owner membership, sealed
AgentVersion, and the exact live Workspace Agent binding with a matching
manifest digest. Cross-Tenant, cross-Workspace, cross-Owner, uninstalled Agent
and digest-drifted references fail closed. A populated `0014` downgrade is
forbidden; recovery is a forward fix or restore into a new
`omnibase_restore_*` database.

Only the exact INV-056 personal canary composition receives the SQL-backed
Skill resolver. Default and engineering compositions remain Skill-free.
Resolution is a deterministic snapshot admission point for a fresh invocation:
disable, revoke and rollback affect subsequent resolutions and do not rewrite
an already reserved invocation. The canonical non-empty bundle digest is part
of the invocation request hash. An empty bundle preserves the historical
no-Skill request hash and SSE shape. Exact replay does not resolve Skills or
call the Provider again.

Prompt precedence remains Platform Security Kernel and sealed AgentVersion,
then sealed instruction Skills, Workspace RAG, untrusted Memory ContextCapsule
and current user input. SSE may expose only the bundle digest and item count;
it must not expose instructions, Skill identifiers, database locators or
review material. Runtime remains false by default, while Planner and
Multi-Agent remain false. Local evidence is intentionally bounded to focused
tests, changed-path static checks and one random `omnibase_test_*` disposable
PostgreSQL journey; GitHub required CI is the full regression authority.

## INV-055 personal-single-owner-admission

**权威源码**

- `backend/src/omnibase/production/personal_owner_gate.py`
- `backend/tests/test_p34_7_personal_owner_gate.py`
- `backend/tests/integration/test_p34_7_personal_owner_gate.py`
- `scripts/production/validate_p34_7_personal_owner_gate.py`
- `scripts/production/run_p34_7_personal_owner_disposable_gate.py`
- `deployment/production/personal-single-owner.example.json`
- `docs/architecture/p34-7-enterprise-track-freeze-and-personal-approval.md`

**为什么存在**

个人版只有一个最终人类 Authority，但这不等于 Agent 可以自批或绕过服务器安全
系统。`personal_single_owner` Gate 只接受一个实时 active Workspace Owner，且该
Owner 必须是当前 tenant schema 中的 active tenant-admin。存在第二个 active
Owner、Member、Maintainer、Operator 或 Viewer 时，该 AI 空间不再属于个人单用户
profile，必须 fail closed；团队和企业 profile 不能借用此快捷路径。

Owner 只表达批准意图。每次准入仍必须在同一服务端事务中重新锁定并核对：

- Agent/Run/System requester 与人类 Owner 身份分离；User requester 永远不能进入
  personal Gate；
- `OperationRecord`、`ApprovalRequest`、logical Resource/version、request/plan/tool
  schema digest、Workspace、Run、action 与 `CapabilityGrant` 精确一致；
- approval 已由该唯一 Owner 决定、未过期、未消费、非 R4，且 metadata 只包含
  frozen personal profile、sandbox mode、approval policy、network-policy digest、
  plan digest、tool-schema digest 与 side-effect 布尔值；
- Capability active、未过期、未撤销、non-delegable、绑定同一 runtime/workload
  identity、Owner、Workspace、action/resource，且剩余 calls/bytes/cost budget 足够；
- WorkspaceRun、RunLease、WorkspaceNode、NodeAttestation、workspace generation、
  run/node fencing token 与 Workload Identity 仍实时有效；
- network 始终 `default_deny=true`，destination 只能是排序、无重复的 logical service
  identifier；wildcard、URL、原始 IP、localhost、Unix/Windows socket、root `.env`、
  PostgreSQL、Redis、MinIO 或其他物理基础设施 locator 必须拒绝；
- migration head 固定为 `0012`、`0013` 不存在，Runtime/Planner/Multi-Agent 三个
  Feature Gate 与 enterprise approved digest 在 readiness 证明阶段全部保持 false。

Gate 的 `personal/ready_for_activation` 只表示 Owner 授权的个人 canary 已具备工程
前置，不会把 `activation_allowed` 或任何生产 Feature Gate 改为 true。实际高风险
执行仍须调用既有 `authorize_operation` 原子消费 approval，并在 Capability 使用前
完成预算预留；Gate 本身不得修改 approval、audit、grant、lease 或 runtime flags。

**必须运行的测试**

- focused/attack matrix：配置 closed set、布尔 coercion、AI 自批、第二成员、Owner
  失活、binding/version/digest 漂移、approval consumed/expired、Grant revoke/delegate/
  budget exhaustion、network locator 攻击、enterprise profile shortcut、evidence byte
  drift 与 stale RunLease；
- disposable PostgreSQL Gate：0012 真实 schema 中持久化 Owner、Membership、Agent、
  Operation、Approval、Capability usage、WorkspaceRun、Node attestation 和 RunLease；
  正向必须 READY，第二成员、fencing drift、approval reuse 必须 fail closed；
- Mypy、显式路径 Ruff、P34.7 R0/R1-A/joint 回归、全量 non-integration、maintainer
  map/benchmark，以及 P5 sealed-contract chain 重封与 clean-HEAD verifier。

**失败恢复**

任何 live binding 或 evidence 不确定时，保留 approval/audit/operation/grant/lease
记录，撤销受影响的 Grant/Lease，要求 Owner 对新的 exact request 重新批准；不得直接
改行、复活 Lease、重置预算、把 unknown 写成 success、写 enterprise approved digest
或自动打开 Runtime。企业轨道继续依照冻结文档保存并保持 blocked/not_proven。

## INV-062 p59p-personal-production-like-acceptance

**Authoritative source**

- `deployment/personal-production/acceptance.compose.yml`
- `scripts/production/p5_9p_fake_provider.py`
- `scripts/production/p5_9p_acceptance_fixture.py`
- `scripts/production/run_p5_9p_personal_acceptance.py`
- `scripts/production/test_run_p5_9p_personal_acceptance.py`
- `.github/workflows/infrastructure-gates.yml`
- `docs/architecture/p5-9p-personal-acceptance-r0.md`
- `docs/evidence/p5-9/personal-acceptance-r0-decision.md`

P5.9P is the final production-like engineering acceptance for the single-human
Owner personal edition. It exercises the loopback frontend Route Handler, API,
ledger, internal model adapter and disposable PostgreSQL/Redis/MinIO
composition. The Provider is an internal deterministic test double with no
host port and no real credential. The fixture is bind-mounted only for the
disposable run and is never copied into a production image.

The journey must prove sealed no-tool Agent installation, first-party sealed
instruction Skill projection, encrypted exact-scope Memory publication through
the real Candidate/Operation/Owner Approval/Grant/Effect/Audit lifecycle,
incremental SSE, durable cancellation, Core SIGKILL, a real TaskLease expiry,
restart convergence to `blocked_unknown`, no automatic Provider replay and an
explicit same-scope Owner `retry_of` with all-new execution and fencing
identities. A missing terminal SSE event or EOF is a veto.

The initial no-Memory invocation must persist one zero-item/zero-token audit
Capsule without changing Provider prompt or SSE Memory metadata. Publication of
the first real Memory must bind that Capsule, and the following invocation must
project exactly one item. The receipt must also report durable cancel terminal
event and Task state as `cancelled`. The current migration head is `0016`;
`0017+` is absent. Migration `0016` adds only personal per-role model
preferences and does not alter this Memory/restart receipt.

The Core container must not restart itself during the interruption window. The
Provider call counter must remain unchanged across restart and exact replay.
The old Task, Attempt, Effect, Lease, Run and runtime/workload identities remain
historical and cannot be revived. The retry receives new identities and may
finish only as a new invocation.

The kill switch must prevent any later Provider call. The deployment is then
recreated without the Runtime overlay, and Runtime must report false. Planner
and Multi-Agent remain false throughout.

Cold recovery must stop writers, create and list a custom-format dump, and
restore with `--no-owner --no-privileges` into a distinct Compose project and a
new `omnibase_restore_*` database. The restored Owner must authenticate and see
the Workspace while Runtime remains unavailable. A stable source-database
fingerprint must be equal before and after restore; name comparison alone is
not proof of restore-new isolation.

Both disposable projects, containers, networks and volumes must be removed.
Run-scoped operator env files, canary state and database dumps must be deleted.
Only a redacted receipt may remain or be uploaded. The receipt must not contain
credentials, Authorization, JWT, database/Redis locators, prompt text, Memory
plaintext or Skill instructions.

P5.9P PASS requires the GitHub Ubuntu production-like job from a clean checkout.
Local syntax or protocol tests cannot substitute for that evidence when Docker
is unavailable. A PASS permits the small P6.0 Personal Admission record; it is
not a public deployment, enterprise P34.7 activation, Marketplace, MCP,
workflow/script Skill, Planner or Multi-Agent admission.

## INV-063 p60-personal-workbench-session-and-employee-boundary

**Authoritative source**

- `frontend/lib/p6-workbench.ts`
- `frontend/lib/p6-workbench.test.ts`
- `frontend/components/workbench/personal-engineering-workbench.tsx`
- `frontend/lib/agent-alpha-stream.ts`
- `frontend/lib/invocation-state.ts`
- `frontend/lib/personal-runtime-gate.ts`
- `docs/architecture/p6-0-personal-engineering-workbench.md`

P6.0-A is a single-human Owner workbench over the existing bounded personal
Agent Runtime. It is not Multi-Agent orchestration. Exactly one parent Agent is
active by default. Nine specialist role definitions are dormant until an
Owner-authored message explicitly names exactly one of them. Unknown,
duplicate, broadcast or multiple employee mentions fail before any network
request. Agent output is display data and can never wake another employee.

The initial session projection is browser-local and must be scoped by the live
tenant and user identity. It may store terminal conversation text and local
timeline events, but must never store auth tokens, Provider secrets,
Capability material, physical infrastructure locators or partial SSE chunks as
successful messages. Corrupt or unsupported schema versions fail safe. The
projection is bounded and does not reconstruct, edit or infer Task, Run,
Attempt, Lease, Memory, ContextCapsule or append-only audit history.

P6.0-A reuses the existing Agent Alpha Runtime status, profile, SSE and cancel
boundaries. InvocationGuard still owns one in-flight request, EOF without a
terminal stays an error, and browser history never automatically replays a
Provider call. Planner and Multi-Agent remain false. Migration `0016` stores
only personal per-role model preferences; Task/Run, Memory and that preference
table remain forbidden as conversation-persistence substitutes.

**Required verification**

- `cd frontend && pnpm test`
- `cd frontend && pnpm typecheck`
- `cd frontend && pnpm lint`
- `cd frontend && NODE_ENV=production pnpm build`
- maintainer map and benchmark validators

**Failure recovery**

Disable the `/dashboard` P6 projection or remove only the exact scoped
`omnibase.p6.workbench.v1:<tenant>:<user>` browser record. Keep the existing
`/agents` diagnostic surface and server ledgers intact. Never repair local
conversation history by mutating execution, Memory or audit records, enabling
Planner/Multi-Agent, or replaying an unknown Provider outcome.

## INV-064 virtual-disk-ownership-backup-and-offline-maintenance

**Authoritative source**

- `AGENTS.md`
- `docs/maintainers/ai-maintainer-map.md`
- platform documentation for Docker Desktop, WSL and Hyper-V disk maintenance

Virtual disks are stateful infrastructure even when most allocated space is
rebuildable cache. A maintainer must distinguish a Docker Desktop system disk
from its container-data disk and inventory running containers, referenced
images, named volumes and business-data risk before changing either. A large
host VHDX is not evidence that its contents are unused, and deleting the image
is never a substitute for inspecting the engine object graph.

Cleanup proceeds inside-out: remove only proven rebuildable BuildKit cache,
unreferenced images and explicitly named unreferenced dependency-cache volumes;
preserve PostgreSQL, Redis, MinIO and unknown volumes. Stop all writers, shut
down the owning engine and WSL distribution, verify the exact resolved path and
mount state, create a length-checked backup on a different disk, and prove a
rollback path before offline maintenance. Compacting a dynamic disk can return
sparse blocks to the host but does not impose a hard capacity limit.

Never shrink, truncate, convert, replace or delete a mounted or unidentified
VHD/VHDX/VDI/VMDK. A capacity change is allowed only when a supported tool can
first validate and resize the guest filesystem and then validate the container
format. If any layer cannot prove a minimum safe size, stop instead of forcing
a container-level shrink. Restoring a copied VHDX must also restore the owning
VM service ACL/SID; administrator access alone does not prove WSL or Hyper-V can
attach it.

**Required verification**

- guest filesystem check reports clean before and after an offline resize;
- container-format inspection reports the intended logical and physical size;
- the owning engine starts and enumerates the expected containers and volumes;
- application worktrees remain unchanged except for the intended patch;
- recovery artifacts are reported explicitly and are not silently deleted.

**Failure recovery**

Stop repeated boot attempts, keep the failed image, restore the length-checked
pre-change backup to the canonical path, restore its VM service ACL/SID,
restart the WSL/virtualization service, and verify engine plus volume inventory.
Do not troubleshoot a virtual-disk failure by mutating OmniBase source, database
migrations or business data.

## INV-065 p60-authorized-file-and-context-boundary

Local files require a direct Owner directory-picker gesture. Handles stay in
memory and are cleared on tenant or Workspace change. Secret/traversal names
must be rejected before `getFile()`, enumeration is lazy under one monotonic
tree budget, and no absolute path or file body is persisted. OPEN is preview;
CONTEXT/PINNED text binds a reviewed SHA-256 digest, is re-read before dispatch,
and enters only as bounded untrusted JSON data. Image/PDF is preview-only and
other binary content is metadata-only. System-default opening remains disabled
without a native bridge.

## INV-066 p60-local-changeset-and-rollback-boundary

Agent Alpha has no local file tool. A P6 ChangeSet records only an
Owner-reviewed text edit after a successful Task/invocation, binds tenant,
Workspace, Task and invocation, and seals Before/After content. Writes require
snapshot CAS and post-write digest verification. Rollback validates owner,
manifest and current content, performs bounded three-way merge, and refuses
overlap or drift. Browser writes are not cross-file atomic; interrupted writes
are recovery-required and may restore only under exact digest comparison.

## INV-067 p60-model-adaptation-and-cost-honesty-boundary

The user-entered model name is the first conservative family-classification
input; observed Provider model identity remains the exact runtime evidence.
Conflicting family tokens resolve to generic. An explicit family override is a
fallback only and a Provider/base-URL hint must never override a recognized
model name. Classification selects prompt guidance, not native capability.
Gears control only real Agent Alpha top-k, local context budget and prompt
guidance. Native reasoning, target output control, Tools, MCP, CLI, Vision and
autonomous delegation remain unavailable and visibly disclosed. The final
assembled request must fit 32,000 characters. Usage must be finite/non-negative
and cost remains unknown without explicit rates.

## INV-068 p60d2-per-role-model-selection-and-migration-boundary

**Authoritative source**

- `backend/src/omnibase/user_settings/model_settings.py`
- `backend/src/omnibase/user_settings/gateway.py`
- `backend/src/omnibase/user_settings/router.py`
- `backend/src/omnibase/user_settings/schemas.py`
- `backend/src/omnibase/db/tenant.py`
- `backend/src/omnibase/migrations/versions/0016_p6_0_workspace_agent_model_overrides.py`
- `frontend/components/workbench/personal-engineering-workbench.tsx`
- `frontend/lib/p6-model-profiles.ts`

One parent and nine dormant specialists remain request-scoped roles over one
personal Agent Alpha Runtime. All ten inherit the user's default saved Provider
credential and model unless a role references another credential or overrides
its model name. The override table stores only logical user, Workspace,
AgentVersion, employee-role and credential IDs, model/family metadata, an
optimistic version and exact-test evidence. API keys, ciphertext and nonces stay
in `model_provider_credentials` and never enter the override table, audit detail
or Browser DTO. Model-name fields reject secret-shaped values, authenticated
URLs, sensitive environment assignments, `.env` locators and absolute physical
paths before ORM mutation or Audit.

Every read, mutation, test and Runtime resolution revalidates the live Tenant,
active tenant User, non-archived tenant-bound Workspace, active membership,
installed exact AgentVersion binding and Workspace generation. Mutations use
the repository lock order and require an exact `expected_version`, including
zero for first creation. Credential ownership is enforced by the composite
`(credential_id, user_id)` foreign key. A custom model is unusable until its
exact requested/actual identity test passes. That evidence binds the override
ID/version, credential/key/provider/base-URL/model state, Workspace generation,
installed Binding ID, AgentVersion digest and endpoint-policy digest. Probe and
personal Runtime share an HTTPS-only, port-443, allowlisted, public-DNS,
no-environment-proxy and no-redirect transport; the verified address set is
frozen for that client while TLS keeps the original hostname/SNI. Runtime
resolves the policy again before dispatch. Concurrent mutation,
generation/binding replacement, credential drift, allowlist change or DNS-set
change invalidates the old evidence. Invocation identity binds the employee
role and resolved scope/configuration so an idempotency key cannot cross role,
model, Workspace generation or Binding.

Migration `0016` is tenant-scoped and authorizes only this personal preference
surface. Its exact `0016 -> 0015` downgrade is tenant-first: all retained
tenants must atomically reach head `0015` and remove both the override table and
the credential ownership unique constraint before the global revision may
move. A populated tenant or any global-first attempt fails closed and rolls
back without partial head movement; recovery is forward-fix or restore-new.
The current migration fact does not enable Planner, Multi-Agent,
Skills, MCP, CLI, Vision, arbitrary tools, enterprise Trust Policy approval,
production evidence or a public deployment.

## INV-069 p61-native-skill-browser-boundary

The P6.1 native catalog is a source-owned closed set of first-party,
instruction-only Skill manifests. Browser list/detail never accepts instruction
text, URL, ZIP or path. Install/disable locks the live Tenant, active personal
Owner, owned Workspace, active owner membership, sealed AgentVersion and exact
installed binding before idempotency reserve; persistence repeats validation.
Mutation, idempotency completion and append-only Audit share one transaction.
Catalog source IDs are not reused as global database primary keys: Definition
and Version rows use deterministic tenant-scoped UUIDs, and first
materialization registers logical resources plus explicit Definition/Version
Audit evidence in the same transaction.
Native Skills have no tools, network, secrets, MCP, Planner, Multi-Agent or
Capability expansion, and catalog digest drift fails closed.

## INV-070 p61-model-native-parameter-and-cache-boundary

Only one unambiguous effective model name selects DeepSeek or GPT native request
fields; base URL/provider label never override it. Unknown/conflicting and
compatible/proxy/emulator claims receive no native controls. The current Chat
Completions boundary never sends the Responses-only `verbosity` field. A stable server-owned system prefix precedes sealed
Agent/Skill/context messages and changing user data remains last. Reasoning gear
is a closed API value bound into invocation intent/replay identity. DeepSeek
cache hit/miss and reasoning tokens are bounded usage observations, never
authorization or proof that caching occurred. Actual model identity must still
equal requested identity; tools, MCP, Planner and Multi-Agent remain disabled.

## INV-071 p61-read-only-mcp-preview-boundary

The P6.1 MCP server is an explicitly launched local stdio preview, not mounted
into Agent Alpha. Existing `no_tool` semantics remain unchanged and
`MCP_RUNTIME_ENABLED` stays false. It exposes exactly authorized-file list,
authorized UTF-8 read and metadata-only Git status/log inspection. Roots,
repository and Git binary identities are captured and revalidated for each
call. They must be
regular non-link/non-reparse objects. Paths reject traversal, absolute/drive,
UNC, ADS, links and reparse escape; secret-like, binary and oversized files
fail closed. File handles are checked after open and reads are incrementally
bounded. Git argv/environment/time/output are fixed and bounded while the
process runs; arbitrary
flags, shell, writes, network operations and credentials are absent.

## INV-072 p61-reproducible-windows-release-boundary

The canonical Windows artifact is a deterministic ZIP with closed manifest,
fixed sort/time/mode/stored encoding and per-file SHA-256. It contains no root
`.env`, populated operator env, secret, DB, model, image tar, VHDX/WSL data,
`node_modules`, `.next` or virtualenv. Release Compose has no `build:` and every
release payload byte is read from the declared clean commit's fixed-path Git
blob under a closed Git environment; ambient repository-overriding `GIT_*`
variables and mutable worktree bytes are not provenance. Every image value is
an operator-supplied immutable `image@sha256` reference verified
by an offline repository/digest closed-set preflight; normal
hosts use pull plus `up --no-build`. The EXE is a thin verifier/extractor of the
same ZIP. Release Compose preserves personal migration, storage initialization,
health and least-privilege lifecycle without host source builds. Install/doctor may report virtual-disk posture but must never compact,
truncate, relocate or delete VHDX or unknown volumes. Unsigned/unbuilt EXE and
placeholder image digests are not a release.

## INV-073 literal-target-recursive-delete-boundary

**Authoritative source**

- `AGENTS.md`
- `docs/maintainers/ai-maintainer-map.md`
- `docs/maintainers/maintenance-map.json`

Recursive deletion is a high-impact external-state mutation even when the
requested object is called a backup, archive, cache, temporary directory or
empty long-path tree. A directory name, approximate location, size or apparent
rebuildability is not proof that its descendants are isolated from repositories,
worktrees, game libraries, release artifacts or unrelated user data.

Before any recursive delete, the maintainer must use read-only checks to resolve
the literal absolute target, inspect every path component for symlink, junction
or reparse behavior, prove the target is contained by the user-authorized parent
and prove it is not that parent itself or an ancestor of any retained object.
The intended entries and aggregate scope must be inventoried using the same
shell and literal-path semantics as the mutation. One command may operate on
only one verified target. Globs, unresolved variables, constructed command
strings, parent-directory cleanup and cross-shell path handoff are forbidden.
In particular, PowerShell discovery must never feed `cmd.exe rd`, batch builtins
or another shell: quoting, backslash and extended-path reinterpretation can
silently widen the deletion boundary.

The default cleanup action is a recoverable same-volume move to a uniquely named
quarantine/trash location after proving the destination is outside the source.
Permanent deletion requires a separate explicit user authorization naming the
exact resolved target after the inventory and recovery consequence are shown.
Long paths, access-denied files or apparently empty parents never justify
switching shells, deleting an ancestor or bypassing containment checks. If any
ownership, containment, link state, target identity or recovery fact changes
between inventory and mutation, fail closed and repeat the read-only proof.

**Required verification**

- capture the exact resolved literal target and authorized parent;
- confirm the target exists, is not the filesystem/workspace/repository root,
  and is not an ancestor of retained repositories, worktrees or user libraries;
- enumerate link/reparse state and intended descendants without following links;
- record whether the action is recoverable and the exact quarantine or backup;
- after mutation, verify only the authorized target moved or disappeared and
  report the recovery status explicitly.

**Failure recovery**

Stop all further cleanup immediately, preserve logs and surviving storage, and
do not delete secondary recovery or forensic material. Identify affected paths,
free space and authoritative remote copies with read-only checks. Prefer restore
from version control, verified backups or a new destination; never conceal the
scope, invent recovered evidence or continue destructive work to make the tree
look tidy. Any follow-up deletion requires a fresh authorization and a fresh
literal-target proof.

**Required verification**

- `backend/tests/test_p6_0_d2_model_settings.py`
- `backend/tests/test_rate_limit.py`
- focused Agent Alpha personal/engineering tests
- disposable `omnibase_test_*` PostgreSQL migration and concurrent mutation tests
- frontend test, typecheck, lint and production build
- maintainer map and benchmark validators

**Failure recovery**

Disable the role override or restore inheritance, keep the personal Runtime and
all enterprise gates fail closed, and preserve append-only audit evidence. Do
not copy or expose a key, accept a stale test result, edit a populated schema
backward or enable Planner/Multi-Agent to repair a model setting. Use a reviewed
forward fix or restore into a new `omnibase_restore_*` database.
