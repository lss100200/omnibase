# P5.1C Browser Agent Registry Control API — 设计判定

> 日期：2026-08-03。本判定依据当前源码、P5.1B sealed 持久化服务、
> P34.4 Workspace 治理、P5.1A 合同与既有 Browser `/api/v1` 惯例作出；
> 不猜测数据落点。所有 mutation 沿用 P5.1B 的原子生命周期语义与
> P34.4 的 aggregate 锁序，API 层只新增"浏览器入口 + 授权边界 + 投影"。

## 1. 为什么走 Browser `/api/v1` 而非 Capability Gateway

P5.1C 是**成员对 Workspace 的治理操作**（安装/禁用/升级/回滚 agent），
不是数据中心读。Browser 面已验证 JWT principal（`get_current_principal`
→ 重新验证 live Tenant/User/role），而 Gateway 是 workload 数据面。
选择 Browser 面：

- 6 个只读端点与 4 个 mutation 端点全部挂在
  `backend/src/omnibase/main.py` 的受控 router 组（
  `agent_registry_router` + `agent_installation_router`）；
- 请求仍使用逻辑标识（UUID），物理 schema/表名永不出现在公共 DTO、
  OpenAPI、SDK 或错误体（单测 `test_openapi_has_no_physical_locators_or_secrets`
  断言）。
- 与 P34.4 workspace 端点（`/api/v1/workspaces/...`）同前缀惯例。

## 2. 为什么默认 fail-closed（503）而不是空列表/路由缺失

任务要求：生产默认不得启用 DB-backed control plane；未装配时返回
HTTP 503 `agent_registry_unavailable`，且**在访问任何 registry table 前
拒绝**。实现：

- `router.get_registry_control_plane()` 默认返回
  `UnavailableAgentRegistryControlPlane`（`_RejectingRegistryAuthorizer`），
  不创建 session、不触碰任何表；
- DB-backed `AgentRegistryControlService` 只由 `dependency_overrides`
  注入（测试/一次性 Gate）；
- 单测对 10 个端点参数化断言 503 + `agent_registry_unavailable`。

## 3. 授权边界：为什么 mutation 在事务内重锁 membership

P34.4 的 `authorize_workspace_action(action, lock=True)` 在调用者拥有的
事务内重新读取 Tenant → User → Workspace → WorkspaceMembership 并加行锁，
然后才允许 mutation。P5.1C 的锁序为：

    Tenant -> tenant User(actor) -> Workspace aggregate
      -> WorkspaceMembership -> AgentDefinition -> AgentVersion
      -> live Binding -> IdempotencyRecord -> ApprovalRequest
      -> target row -> Resource -> AuditEvent

- 不使用事务前角色快照、不信任浏览器 cookie；
- definitions/versions catalog 是 live Tenant principal 下的 tenant-wide
  只读目录；installation 读端点使用 `workspace.read`（viewer 可读），
  mutation 使用 `workspace.grants.manage`（maintainer）授权；
- Browser 层只读取 target Version/current Binding 的非锁定快照，用于早期
  404/409 与构造逻辑 DTO；权威行锁和状态复核全部由 P5.1B service 按
  Definition → Version → live Binding 的标准锁序执行，避免 Browser
  Version/Binding 预锁导致锁序反转；
- `expected_binding_id` 是期望绑定校验。Browser 快照不在进入 P5.1B
  幂等分支前要求旧 Binding 仍 live，因此 upgrade/rollback 在首次成功把
  旧 Binding 标记为 superseded 后，同 key 同 body 仍可精确 replay。

## 4. 为什么 upgrade/rollback 复用 P5.1B 的 supersede 而非新建状态机

`supersede_binding` 已是"旧 binding 移出 live 集 → 新 binding 原子
安装 → 失败整体回滚"的实现，P5.1C 的 upgrade/rollback 只是目标版本
不同（目标 sealed 版本 vs 历史 sealed 版本）。复用避免第二套
生命周期状态机；API 层只补充：

- `_load_sealed_target_version_snapshot`：Browser 早期快照要求目标 sealed
  且 digest 精确匹配，P5.1B service 在标准锁序下再次权威复核
  （升级/回滚都不能指向 draft/revoked）；
- `expected_binding_id` 不匹配 → 409 `registry_stale_binding`。

## 5. 幂等 replay 的确定性 hash 锚点

P5.1B 的内部幂等 hash 基于完整 binding（含 server 生成的 binding_id /
created_at）。若 Browser 层每次请求都重新生成 binding，则同 key 同
body 的 replay 会被误判为 input drift。P5.1C 不允许调用方传任意 digest；
service 只接受封闭的服务端 hash profile：

- `internal_full`：保持 P5.1B install 与 supersede 的原始完整 DTO hash
  语义，不改变内部调用方；
- `browser_install`：service 计算
  `{operation: "agent.install", binding: deterministic_payload}`；
- `browser_upgrade` / `browser_rollback`：service 计算
  `{operation, old_binding_id, binding: deterministic_payload}`；
- deterministic payload 只去掉 `workspace_agent_binding_id` 与 `created_at`
  两个 server 生成字段。同 key 同 body 精确 replay，同 key 不同 body 409；
- Approval 同时校验精确 action（`agent.install|agent.upgrade|agent.rollback`）
  和上述 operation-bound request hash，因此不能把 install Approval 重放为
  upgrade/rollback，也不能在两个 supersede 操作之间互换；消费仍与 Binding
  insert、Idempotency 和 Audit 同事务且只发生一次。

## 6. 为什么不在 P5.1C 创建 AgentDefinition/AgentVersion

定义注册与版本 sealed 仍是 internal（P5.1B 服务层），任务明确禁止
通过 API 创建。P5.1C 只暴露 catalog 读（tenant 过滤）+ 安装生命周期。
三个 Feature Gate（`agent_runtime_enabled` 等）保持 false，migration
head 保持 0010，禁止新增 migration。

## 7. 错误合同

- 服务层稳定 reason code 透传：`RegistryConflictError` /
  `RegistryStateError` 的 `args[0]` 成为 HTTP 409 的 `error.code`
  （如 `registry_approval_required`、`registry_stale_binding`）；
- 非法路径 UUID 返回稳定 422 `invalid_logical_identifier`；404 统一
  `not_found`；503 `agent_registry_unavailable`；
- 错误体严格 `{"error": {"code", "message"}}`，经 main.py 风格 handler
  透传，不带任何物理 locator。

## 8. SDK 边界

- Python `omnibase_sdk.browser_registry`：Bearer JWT transport、
  严格字段集合/闭集/整数解析（服务器 `extra="forbid"` 的镜像）、mutation
  强制 `Idempotency-Key`、禁止通配 scope；
- TypeScript `registry-browser.ts`：与 Python 同构，不做 `String()`/
  `Number()` 宽松转换，不接受 extra field、非法状态或 `NaN`；
- 两个 HTTP transport 都在拼接 URL 前拒绝 dot segment、反斜杠、百分号
  编码、query/fragment 与重复斜杠，并复核规范化 path 仍精确位于
  `/api/v1/`，避免 `/api/v1/../../gateway/...` 归一化逃逸；
- SDK 只携带逻辑标识，不携带 tenant schema / 物理 locator / 凭据。

## 9. Gate 边界（与 P5.1B 同构）

`scripts/production/run_p5_1c_browser_registry_disposable_gate.py`：
一次性 Compose 项目 `omnibase-p51c-*`、`omnibase_test_p51c_*` 数据库与
受限非 owner 角色；在任何 Alembic/pytest 前实际运行
`backend/tests/destructive_preflight.py`，验证数据库名、sentinel 与受限
non-owner role 后才把 evidence 的 `database_sentinel_verified` 标记为 true；
随后迁移到 head 0010、跑 guarded integration suite
（API-backed install/upgrade/disable/rollback、精确 replay、digest
drift、跨租户、live membership、并发单赢家、approval 单次消费、
audit append-only、rollback 原子性、cleanup proof）、记录 fail-closed
evidence 并证明零残留资源。canonical evidence 只允许以同目录临时文件 +
`os.replace` 原子更新，拒绝 source/destination symlink，旧版本可由 Git
历史恢复；复验时只允许这两份 canonical evidence 是 dirty，出现任何其他
dirty path 仍判 source drift。P5.1B Gate 使用相同预检与发布规则。
