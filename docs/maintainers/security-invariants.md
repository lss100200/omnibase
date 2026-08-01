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

P34.4 Run Lease、Node fencing 和实时 attestation 只提供控制面授权事实，不能自行证明某个运行时可以安全执行代码。Sandbox 每次普通操作都必须重新绑定 tenant、Workspace、Run、runtime instance、Node、Lease、Workspace generation、Run/Node fencing、workload identity、action、有效期和在线 capability 状态；原始 UUID、provider handle、调用方声明或已经创建的 runtime 都不是持续授权。P34.2 read profile 与 Sandbox lifecycle profile 必须互斥；Sandbox Grant 必须短期、不可委派、绑定单一 Workspace/runtime/workload identity，并且不得签发为 Gateway bearer token。紧急 stop/destroy 必须使用独立可信 controller authorization，不能依赖已经撤销的 workload grant，也不能因为 workload 已撤销就匿名放行。任何副作用前还必须存在 operation-idempotent capability budget reservation、durable operation/transition/Audit、当前 Runner host/profile 证明和独立 transport；exact replay 不得重复扣费，binding drift 必须拒绝，dispatch 结果不确定时禁止自动重放。缺少可信 verifier/store/provider/controller/host/transport/Runner 时必须拒绝，A0-A3 的本地 harness 只能演练授权、状态机与调度顺序，不能产生任何进程、文件、容器、socket、网络、挂载或数据访问。

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
- `backend/tests/integration/test_p34_5_sandbox_persistence.py`（仅 guarded disposable `omnibase_test_*` sentinel PostgreSQL）
- `backend/tests/test_p34_4_workspace_service.py`
- `docker compose run --rm --no-deps backend mypy src/omnibase/sandbox src/omnibase/workspaces`
- `python scripts/sandbox/probe_runner_host.py`；结果不 ready 时不得通过降低 profile 或省略控制强行装配 provider。
- 对新增真实 provider 运行 P34.5 `RUN-03/04`、`FS-01/02/03`、`NET-01/02`、`PROC-01/02`、`HOST-01` 与 `CROSS-01` 攻击矩阵；A0 单元测试不能替代目标 Linux isolation Gate。

**失败恢复**

立即撤下真实 provider/Runner wiring，恢复 `UnavailableSandboxProvider`、`UnavailableSandboxRunner` 与全部 rejecting authorizer/verifier，撤销受影响 Run Lease/capability/workload identity，并通过独立可信控制通道停止对应 Runner。保留 operation transition、runtime、lease、fencing、审计和 provider 证据；ambiguous outcome 只允许 reconciliation，不允许猜测重放。无法证明副作用前完成在线验证时一律视为未授权。不得通过放宽路径、网络、资源或身份检查恢复服务，也不得把普通 Docker smoke 当作敌对代码隔离证明。
