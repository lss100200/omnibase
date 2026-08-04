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
- `backend/src/omnibase/migrations/versions/0007_p34_4_workspace_control_plane.py`

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

**为何存在**

P34.7 的生产结论必须能够从公开 clean checkout 重建，并精确绑定 Git commit/tree、受控 tracked-source manifest、部署配置和每份 evidence 的 SHA-256 与 JSON assertions。工作树 dirty、证据漂移、缺少当前源码证明或只存在历史报告时，状态只能是 `blocked/not_proven` 或 `invalid/veto`，不能靠人工文字改成 PASS。

**允许的改法**

- 扩展显式 source scope、evidence schema 或验证断言，同时保留根 `.env`、symlink/reparse、非 regular file 和仓库外路径拒绝。
- 为新的独立生产组件增加当前源码绑定的 evidence 项；缺失项保持 `not_proven`。
- 将验证与激活分离；Gate 通过只产生 admission decision，不自动启动服务或授予 authority。

**禁止的改法**

- 在 dirty checkout、未跟踪生产源码、证据哈希不匹配或 source manifest 不完整时发出 production PASS。
- 将 Docker Desktop、WSL、mock、test double、disposable Gate、旧 commit evidence 或端口可达性冒充当前生产证据。
- 读取、打印、散列或纳入根 `.env`，或让 evidence path 逃逸仓库/受控 operator 目录。

**必须运行的测试**

- `backend/tests/test_p34_7_production_composition.py`
- `python scripts/production/validate_p34_7_composition.py --validate-only`
- 提交后必须从 clean checkout 运行 `--verify`；外部证据未齐时预期为 `blocked/not_proven`，不是失败伪装。

**失败恢复**

把 `activation_requested` 恢复为 false，撤销受影响组件的 admission，保留原 evidence 和 manifest 取证。修复源码或重新采集证据后从新的 clean checkout 验证；不得删除 Veto、忽略 dirty scope 或复用旧哈希。

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
本不变量不得声称数据库约束、RBAC、并发安装或 Runtime 已经完成——那些
属于 P5.1B+，当前保持未实现。

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
Phase 5 Feature Gate 保持 false，migration head 保持 0010。

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
- 新增 AgentDefinition/AgentVersion 创建端点、migration 0011、打开任何
  Phase 5 Feature Gate、暴露 Invocation/Runtime/Orchestration 表面。
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
  （一次性 sentinel PostgreSQL：migration head 0010、API-backed
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

P5.2A 只是 P5.2 Agent Task/Run/Step/Attempt 账本的**离线合同预检**，不是
账本实现。合同必须保持逻辑化（无物理 locator/凭据）、不可变（sealed
manifest digest 基于 canonical 原始 UTF-8 字节）、无秘密（无 API
key/base_url/Authorization/cookie/token/私钥）且非运行态（无 P5.2
ORM/migration `0011`/router/Runtime/Planner/Executor/scheduler/worker/
模型/工具调用）。P34.7、P5.0、P5.1 production 任一未 `ready` 时，P5.2A 恒
`blocked/not_proven`；三个 Phase 5 Feature Gate 保持 false；**任何 gate
意外解析为 `true` 或 `activation_requested=true` 都是 veto**（比 P5.0/
P5.1A 的 blocker 更严格）。源码树出现任何 P5.2 ORM/migration/router/
runtime 包或 migration revision 集合漂移都是 veto。本不变量不得声称
Task 账本持久化、Task Lease 发放、预算 commit/release、cancellation
runtime 或 Agent Runtime 已经完成——那些属于 P5.2B+，当前保持未实现。

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

- 在本模块或 validator 中实现/预装 P5.2 ORM、migration `0011`、
  agent-invocation/agent-task router、Browser/Workload SDK、Agent
  Runtime、Planner、Executor、dispatcher、scheduler、worker、Celery
  task、polling/heartbeat loop、model/tool provider、Memory/Skill
  runtime 或 shell/SQL/HTTP tool；以"代码存在但 gate 关闭"为理由同样
  禁止。
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

- `backend/tests/test_p5_2a_task_ledger_contract.py`（50 项负向矩阵：
  DTO 闭集、hash profile、预算不变量、TTL 边界、fencing 单调、terminal
  resurrection、unknown no-replay、cancel 语义、identity stages、
  symlink/reparse `.env` 逃逸、dirty checkout、gate true veto、forbidden
  包/migration 0011、OpenAPI agent endpoint、仓库内 report、not_proven
  计数、safety negatives）
- `python scripts/production/validate_p5_2a_task_ledger_contract.py
  --validate-only`（合法合同 exit 0，永不 ready）
- 提交后从 fresh clean checkout 运行 `--verify`；当前正确结果是
  `blocked/not_proven`（exit 2，veto 0）。

**失败恢复**

保持 gate false、`activation_requested=false`，删除/回退任何意外出现的
P5.2 runtime/ORM/API 源码，从新的 clean checkout 重跑 validator。sealed
digest 漂移时保留原合同与 report 取证，更新证据或合同后重新封存并
re-verify。任何情况下都不得从该模块启动 Phase 5 运行时组件或访问业务
数据库。
