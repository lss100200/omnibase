# Phase 3–4 统一实施计划：受控能力平面与安全 AI 工作空间

> 状态：P34.0 契约基线
>
> 事实基线：Phase 1.6 双索引工程与 benchmark 已完成，V2 生产回填/cutover 冻结，V1 继续作为权威主通道；Phase 2 已提供 `/api/v1`、Request ID、请求体边界、显式 CORS、Redis 限流和数据库实时 RBAC。
>
> 目标：把“受控数据库能力/API 解耦”和“AI 工作空间/沙箱/能力网关”作为同一个架构阶段设计、分批实现，为后续 Agent、Skill 和 MCP 提供默认安全执行边界。

## 1. 阶段原则

Phase 3–4 不把数据库连接交给工作空间，也不先创建高权限 Agent 再补限制。它交付统一能力平面：

```text
用户 / 租户管理员
        │
        ▼
公开控制面 /api/v1
        │
        ├── Resource Registry
        ├── RBAC / Policy / Approval
        ├── Idempotency / Operation / Audit
        └── Workspace Control Plane
                    │
                    ▼
            Capability Issuer
                    │
          workload identity + token
                    │
                    ▼
             Capability Gateway
        ┌───────────┼─────────────┐
        ▼           ▼             ▼
  Controlled DB   Canonical RAG   Artifact Adapter
        ▲           ▲             ▲
        └───────────┴─────────────┘
                    │
              Sandbox Runtime
```

永久约束：

1. 工作空间、Agent、Skill 和 MCP 永远不获得 PostgreSQL、MinIO root、宿主文件系统或 Docker socket 凭据。
2. 外部 API 只接受逻辑资源 ID，不接受物理 schema、表名、对象 key、宿主路径或 SQL 字符串。
3. 规范 RAG 默认只读；workspace-derived 数据必须物理/逻辑隔离并记录 lineage。
4. V1 不得因本阶段开发被删除、覆盖或隐式切换；V2 cutover 仍需独立批准。
5. 所有 mutation 必须可授权、可审计、可幂等、可取消或可恢复；高风险操作必须绑定人工审批。
6. Agent 编排不属于本阶段；只有 P34.7 总 Gate 通过后才允许启动。

### 1.1 Tenant、AI Space 与执行实例的正式分层

`Tenant` 不应被理解为只有商业 SaaS 才需要的“付费租户”。在 OmniBase 中，它是一个部署、组织或本地安装实例的**顶层信任域**，负责根管理员、成员身份、签名与撤销根、平台策略、规范数据边界和审计归属。开源单机安装默认可以自动创建一个单 Tenant，但不得因此删除 tenant scope 或把客户端提供的 tenant 当成可信输入。

面向用户的 **AI Space** 与当前内部模型中的 `Workspace` 统一为同一个长期逻辑资源，不再额外制造两个含义重叠的层级。AI Space/Workspace 才是日常协作和 AI 隔离的主要单位，负责成员与角色、项目文件、Workspace 私有数据、派生 RAG/记忆、Artifact、能力授权、网络策略、配额和快照。处于同一 Tenant 只表示共享根信任域，**绝不意味着可以读取同 Tenant 的任意 Workspace**。

```text
Deployment / Organization / Local Installation
                    │
             Tenant trust domain
                    │
          AI Space / Workspace boundary
                    │
       Run / Interactive Session / Sandbox
```

数据 scope 固定分为：

- `platform_internal`：实例根配置、签名与基础设施治理；
- `tenant_shared`：组织明确共享的规范资源；
- `user_private`：用户长期偏好和个人记忆事实层；
- `workspace_private|workspace_shared`：某个 AI Space 的文件、记忆、派生数据和协作成果；
- `run_ephemeral`：单次执行的临时文件、凭据、缓存、日志和连接，Run 销毁后默认清除。

用户级长期记忆不得自动复制到每个 Workspace；Memory Compiler 只能通过显式的 `memory.view`/能力投影，把与当前任务、Workspace、Agent 和 token 预算匹配的最小上下文交给运行实例。跨 Workspace 分享必须创建独立 grant、审计和可撤销引用。

### 1.2 成员设备 Overlay 与 Sandbox 网络分层

“没有中心业务服务器时成员仍可协作”作为 P34.4/P34.5 的正式方向，但网络必须拆成两个互不等价的平面：

```text
成员设备加密 Overlay 控制网
  Node Daemon / peer discovery / mTLS / lease / service advertisement
                         │
               Workspace Network Broker
                         │
每个 Workspace/Run 独立的 Sandbox 数据网
  network namespace / default deny / gateway-only / short-lived identity
```

- 成员设备可以通过 WireGuard 系 Overlay 形成加密点对点网络；控制面只依赖可替换的 `PeerOverlayProvider`，不绑定 Tailscale、Headscale、NetBird、ZeroTier 或单一厂商。
- Sandbox Runtime **不得直接加入成员设备 Overlay**，也不得获得成员节点的长期 WireGuard/Tailscale/ZeroTier 身份。它只能进入独立 network namespace，经 `WorkspaceNetworkBroker` 和 Capability Gateway 访问被明确发布的逻辑服务。
- 网络授权以 tenant/workspace/node/workload identity、短期证书、grant、lease 和 fencing token 为事实源，不以来源 IP 或“已经连入虚拟局域网”为授权依据。
- 默认禁止访问宿主 LAN、其他成员私网、其他 Workspace、数据库/Redis/MinIO 管理端口、metadata、Docker socket 和任意外网；每个服务暴露必须显式声明 action、方向、端口/协议、流量预算、有效期和审计策略。
- 数据协作优先使用 Git/内容寻址 Artifact/追加事件与受控 RAG 同步，不在早期引入跨成员设备 PostgreSQL 多主复制。初始模型允许一个 Workspace authority node 负责串行化写入；authority 离线时进入只读或等待恢复，后续再评估有明确一致性协议的接管。
- “无业务服务器”不等于“绝对无协调基础设施”：跨 NAT 的首次发现、密钥轮换和中继通常仍需要轻量 coordination/relay。自托管 Headscale/NetBird、托管控制面或未来自研协调器都只能作为 adapter；核心 Workspace、权限和审计事实不能委托给 Overlay 厂商。

## 2. 统一设计与实现依赖

必须同阶段统一设计：

- Resource Registry、逻辑 ID、资源树和 lineage；
- capability action vocabulary、RBAC、风险等级和审批规则；
- CRUD/DDL、RAG、artifact 与 workspace 共用的审计、幂等和 operation 协议；
- canonical/derived 数据分类；
- 用户控制面、内部签发面和 workload gateway 的 API 分层；
- workspace 生命周期、capability 撤销和 runtime desired/observed state；
- 错误 envelope、资源版本、并发、取消和恢复语义。

必须顺序实现：

1. Phase 2 安全边界先于 P34。
2. Registry、Audit、Idempotency、Approval 先于 mutation。
3. 只读资源解析和 gateway 先于 CRUD/DDL。
4. CRUD 先于 DDL apply；DDL plan/validate 先于 approval/apply。
5. workspace record/reconciler 先于 runtime。
6. 沙箱攻击 Gate 先于执行不可信代码。
7. capability gateway 先于工作空间访问 DB、RAG 或 artifact。
8. P34.7 先于 Agent、Skill、MCP。

## 3. Resource Registry 契约

以下为领域 schema，不预先绑定 ORM；实现时必须生成迁移并保持 tenant scope：

```text
resource_registry
  id                    opaque UUID/ULID，API 唯一标识
  kind                  workspace|run|interactive_session|data_table|data_view|
                        document|corpus|artifact|derived_index|snapshot|operation
  tenant_id             仅服务端从 CurrentPrincipal/capability 得出
  owner_type            user|workspace|system
  owner_id              逻辑 user/workspace id
  parent_id             nullable logical resource id
  display_name          用户可见名称，不作为 SQL/path 标识符
  state                 kind-specific lifecycle state
  version               乐观并发整数
  policy_class          canonical_readonly|tenant_managed|workspace_private|
                        controlled_shared|system_internal
  physical_locator      加密/受限内部字段，禁止进入 API、日志和 token
  metadata              有 schema/version 的受控 JSON
  lineage_source_ids    逻辑来源 ID 集合
  created_by_actor_id
  created_at / updated_at / archived_at

resource_grant
  id, resource_id, principal_type, principal_id, actions,
  constraints, version, state, expires_at

resource_lineage
  source_resource_id, derived_resource_id, relation, source_version,
  transform_digest, created_by_operation_id
```

约束：

- ID 必须随机、不连续且不编码 tenant、schema 或敏感资源类型。
- 查找顺序固定为“认证上下文确定 tenant → tenant scope 内查 ID”；不存在与无权访问统一返回 404。
- `physical_locator` 只能由 adapter/resolver 读取。
- 表和列均拥有稳定逻辑 ID；rename 不改变 ID。
- 用户显示名不直接成为 PostgreSQL identifier、目录名或 MinIO key。
- canonical 系统表不得注册为可变更 data-table capability。

### 3.1 Action vocabulary

动作使用 `<domain>.<verb>`，不允许 wildcard：

| Domain | Actions |
|---|---|
| resource | `resource.read`, `resource.list`, `resource.share`, `resource.archive` |
| data | `data.schema.read`, `data.rows.read`, `data.rows.insert`, `data.rows.update`, `data.rows.delete`, `data.schema.plan`, `data.schema.apply`, `data.export` |
| rag | `rag.search`, `rag.citation.read`, `rag.ask`, `rag.derived.create`, `rag.derived.delete` |
| artifact | `artifact.read`, `artifact.write`, `artifact.publish`, `artifact.delete` |
| network | `network.request` |
| workspace | `workspace.read`, `workspace.run`, `workspace.pause`, `workspace.snapshot`, `workspace.restore`, `workspace.archive` |
| capability | `capability.request`, `capability.delegate`, `capability.revoke` |
| approval | `approval.read`, `approval.decide` |
| operation | `operation.read`, `operation.cancel` |

新增动作需要安全评审和契约版本升级；委托只能取原动作集合的严格子集。

## 4. Canonical / Derived 数据分类

| 分类 | 示例 | 默认能力 | 写入规则 |
|---|---|---|---|
| `system_internal` | users、tenant registry、refresh-token state | 不向 workspace 暴露 | 仅核心服务 |
| `canonical_readonly` | 文档、权威 chunk/citation、V1/V2 index metadata | `resource.read`, `rag.search`, 受控 `rag.ask` | workspace 不得写 |
| `tenant_managed` | 用户创建的逻辑表、视图 | 按 RBAC/grant | 受控 CRUD/DDL |
| `controlled_shared` | 经批准共享的数据集/工具输出 | 显式 grant | 版本化 mutation |
| `workspace_private` | 私有 artifact、memory、派生表、实验状态 | 所属 workspace | capability + 配额 |
| `workspace_derived` | 派生索引、摘要、转换结果 | 所属 workspace/显式分享 | 必须 lineage，不得冒充 canonical |

canonical 与 derived RAG 必须使用不同 policy class、physical locator 和删除流程。任何 promotion 都必须创建新资源、人工审批并保留来源，不能原地改分类。

## 5. Capability Token + Ledger

### 5.1 Token 契约

capability token 与用户 JWT 分离，使用短期非对称签名和 `kid` 轮换：

```json
{
  "iss": "omnibase-capability-issuer",
  "aud": "omnibase-capability-gateway",
  "kid": "active-signing-key-id",
  "jti": "opaque-token-id",
  "sub": "workspace-runtime-instance-id",
  "tenant_id": "logical-tenant-id",
  "workspace_id": "logical-workspace-id",
  "actor_user_id": "originating-user-id",
  "grant_id": "server-side-grant-id",
  "grant_version": 7,
  "delegation_depth": 0,
  "cnf": {"x5t#S256": "workload-identity-thumbprint"},
  "approval_id": null,
  "iat": 0,
  "nbf": 0,
  "exp": 0
}
```

token 禁止包含物理 schema/path/key、数据库/MinIO 凭据、用户 JWT、文件内容、prompt、SQL 或完整资源内容。

### 5.2 在线 Ledger

```text
capability_grant
  id, tenant_id, workspace_id, runtime_instance_id,
  actor_user_id, actions, resource_ids, constraints,
  version, state(active|revoked|expired), expires_at,
  max_calls, max_bytes, max_cost, delegation_depth_limit,
  approval_id, created_at, revoked_at

capability_usage
  grant_id, window_or_bucket, calls, bytes_in, bytes_out, cost_units

capability_revocation
  grant_id, token_jti(optional), reason, actor_id, revoked_at
```

网关校验顺序：

1. 签名、`iss/aud/kid/exp/nbf/jti`；
2. workload identity 与 `cnf/sub`；
3. ledger grant active、version 一致、未撤销；
4. token tenant/workspace 与 runtime observed state；
5. action/resource 在 grant scope；
6. Resource Registry tenant/owner/state/version；
7. 当前 RBAC、审批和约束；
8. 原子扣减次数/预算；
9. 执行 adapter 并写审计。

workspace 无权签发；委托只能减少 action、resource、约束、有效期、预算和深度。

## 6. API 分层

用户控制面：

```text
/api/v1/resources/*
/api/v1/data/tables/*
/api/v1/data/operations/*
/api/v1/workspaces/*
/api/v1/approvals/*
/api/v1/operations/*
/api/v1/audit/*
```

认证：用户 access JWT + CurrentPrincipal + tenant RBAC；浏览器只能访问此层。

内部签发面：

```text
/internal/capability/v1/grants/*
/internal/capability/v1/tokens/*
/internal/capability/v1/revocations/*
```

仅核心服务可访问；无浏览器 CORS、不暴露公网端口。

Workload Gateway：

```text
/gateway/v1/resources/*
/gateway/v1/data/*
/gateway/v1/rag/*
/gateway/v1/artifacts/*
```

只接受 workload identity/mTLS + capability token，拒绝用户 JWT。

内部调用层次：

```text
HTTP DTO
  → Application Service
  → Policy / Approval / Idempotency
  → Resource Resolver
  → Capability Enforcement
  → Domain Adapter
  → PostgreSQL / RAG / MinIO / Sandbox Runtime
```

外部 DTO 不直接复用 SQLAlchemy model；只有 adapter 可见 physical locator。

## 7. 受控 CRUD / DDL

Query/CRUD：

- 查询只接受列逻辑 ID、过滤 AST、排序、cursor、limit；禁止 SQL fragment。
- 服务端设置最大行数、结果 bytes、statement timeout 和并发上限。
- insert/update 执行类型、nullable、constraint 和资源版本校验。
- update/delete 必须有受控 filter；无条件全表 mutation 必须升级为高风险 operation。
- mutation 强制 `Idempotency-Key`，并支持 `If-Match`/resource version。
- 大型 import/export/batch 转 operation，队列只传 durable identifiers。

DDL 固定流程：

```text
schema.plan → validate → risk classify → approval(if required)
            → schema.apply → audit/outbox → completed|failed
```

初始允许创建逻辑表、增加 nullable 列、受控索引和 rename 显示名。drop、类型收窄、nullable 收紧、大表索引和批量 rewrite 需在风险 Gate 后逐项开放。物理 identifier 由系统生成并 quote；不开放任意 SQL console。

## 8. Audit / Idempotency / Approval / Operation

Audit 至少记录 request ID、actor、workspace/runtime、grant/jti、resource/action、policy decision、approval、输入 hash、before/after version、结果类别、行数/bytes、耗时和拒绝原因。禁止记录授权头、token、密码、prompt、正文、SQL、凭据、physical locator 或文件 bytes。mutation 与 audit/outbox 必须同事务或保证最终一致。

```text
idempotency_record
  tenant_id, actor_scope, operation_name, key,
  request_hash, state(pending|completed|failed),
  response_ref, operation_id, expires_at
```

同 key/不同 hash 返回 409；pending 返回原 operation；completed 返回安全重放结果。重放仍需验证当前 actor/grant。

审批状态：

```text
draft → pending → approved | rejected | expired | cancelled
                         approved → consumed
```

审批绑定 action、resource ID/version、request hash、risk、grant、审批人和有效期。资源或请求变化后审批失效；workspace/AI 不得批准自己的请求。

Operation：

```text
queued → running → succeeded | failed | cancelled
              └→ cancelling → cancelled
```

operation 必须有进度、deadline、取消点、重试上限、结果引用和审计关联。Celery payload 不得包含 HTTP context、文件 bytes、JWT 或凭据。

## 9. Workspace、Run 与生命周期

### 9.1 长期逻辑资源与短期执行实例

`Workspace` 是长期逻辑资源：它持有模板版本、资源树、策略、artifact、derived 数据、审计关系和用户可见身份。停止 runtime、重启宿主或销毁某次执行，均不得删除 Workspace。

`Run` 与 `Interactive Session` 是短期、可销毁的执行实例：

- 每次执行拥有独立 `run_id`、`runtime_instance_id`、workload identity、lease 和 fencing token；
- Run 只引用 Workspace 的逻辑资源和当前 generation，不成为长期权限容器；
- Run 终止后必须撤销临时 capability、关闭连接、终止进程并释放 writable runtime layer；
- Interactive Session 只是可交互 Run，不得获得比普通 Run 更宽的默认能力；
- Workspace 可以没有任何活跃 Run；同一 Workspace 的并发 Run 数由显式配额控制。

快照恢复必须创建新的 Workspace generation 和新的 Run/runtime instance。恢复内容只包括契约允许的持久文件、artifact、derived 数据和元数据；禁止恢复旧 capability token、workload identity、进程、PID、socket、网络连接、数据库连接、Redis/Celery 状态或内存中的凭据。

### 9.2 Workspace 状态机

采用 desired/observed state 和幂等 reconciler：

```text
provisioning → stopped → starting → running
      │           ▲          │          │
      └→ failed   │          └→ failed  ├→ pausing → paused
                  │                     ├→ snapshotting → paused|running
                  └──── stopping ←──────┘

stopped|paused → archiving → archived → purge_pending → purged
```

Workspace 记录：

- tenant、owner、parent workspace；
- template ID/version/digest；
- desired state、observed state、generation；
- runtime instance/workload identity；
- CPU、内存、磁盘、文件数、时间、并发、网络、输出配额；
- active grant IDs；
- snapshot/artifact/derived-resource IDs；
- last transition、failure class、retry count。

归档/删除顺序：停止新任务 → 撤销 capability → 停止 runtime → 快照/资源清单 → archived → retention 后 purge。`purged` 不可恢复，必须独立审批。

### 9.3 独立 Sandbox Runner

Sandbox Runner 与现有 Celery worker 是两类不同信任角色：

- Celery 继续执行受信任的核心摄取、索引和控制面任务，并可能持有核心服务连接；它不得执行 workspace 生成的不可信代码。
- Sandbox Runner 部署在 Linux 执行节点，只负责实现 runtime lifecycle 和受限 exec；不得持有 PostgreSQL、MinIO、Redis、JWT、provider 或 capability signing key。
- Runner 通过控制面 `pull lease` 获取待执行 Run，周期 heartbeat，所有状态变更必须携带单调递增 fencing token。
- lease 过期、generation 变化或 fencing token 落后时，旧 Runner 必须停止提交结果并有界终止 runtime。
- Runner 不直连 DB、MinIO 或 Redis；sandbox 只能通过 Capability Gateway 使用逻辑资源。
- 控制面不得把用户 JWT、核心凭据、HTTP headers 或 physical locator 放入 runner task。

### 9.4 可替换 Runtime 接口

控制面只依赖 `SandboxProvider`/`RuntimeDriver` 抽象，不直接耦合 Docker、Incus、Kata、Firecracker 或云厂商：

```text
prepare(workspace_generation, template_digest, policy)
create(run_id, workload_identity, quotas, network_policy)
start(runtime_handle)
exec(runtime_handle, command_spec, deadline)
cancel(runtime_handle, execution_id)
logs(runtime_handle, cursor, byte_limit)
stats(runtime_handle)
snapshot(runtime_handle, snapshot_spec)
restore_new_generation(snapshot_id, new_generation, new_identity)
stop(runtime_handle, grace_deadline)
destroy(runtime_handle)
```

接口要求：

- 所有 mutation 幂等并接受 operation/fencing token；
- `logs`、`stats` 有大小、速率和敏感信息边界；
- `restore_new_generation` 永远生成新 identity，不存在恢复原 runtime 的接口；
- provider handle 是内部定位，不得进入公开 API 或 capability；
- provider 失败不得导致 capability、资源配额或 workspace state fail-open。

## 10. 风险与审批矩阵

| 等级 | 示例 | 默认主体 | 审批 | 额外控制 |
|---|---|---|---|---|
| R0 只读低风险 | metadata、有限 query、canonical RAG search | 普通用户/workspace | 无 | 限流、结果上限、审计 |
| R1 私有可逆写 | private insert、artifact write | 用户/workspace grant | 无或策略批准 | 幂等、配额、版本 |
| R2 共享/结构变更 | create table、add column、分享 derived | tenant admin | 策略或人工 | plan、dry-run、锁/成本预算 |
| R3 破坏/高成本 | drop、类型收窄、批量 delete、外网扩权、purge | tenant admin 发起 | 明确人工 | 请求 hash、冷静期、恢复点 |
| R4 平台级 | runtime 基线、签名 key、宿主网络策略 | platform admin | 双人/离线 | 独立审计和回滚 |

AI/workspace 发起的 R2+ 请求必须等待人类决定。审批不能扩大原 grant，批准后仍需 capability 和资源版本校验。

## 11. Runtime ADR 候选、当前推荐与决定点

### 11.1 当前推荐：方案 B

当前推荐采用“OmniBase 自研控制面 + Capability Gateway + 可替换 SandboxProvider”的方案 B：

- OmniBase 自己拥有 Workspace/Run 模型、resource/capability/approval/audit 契约和 runner 协议，不把核心权限模型委托给外部平台。
- 开发环境可使用 hardened Docker RuntimeDriver 验证接口、生命周期、配额和 gateway 功能，但它只是功能基线。
- Linux standard profile 推荐 gVisor；需要传统系统容器/更完整 workspace 体验时可评估 Incus VM 作为可选 profile。
- strong isolation profile 推荐 Kata Containers。
- Firecracker/Cloud Hypervisor 仅作为未来 provider 底层候选，不允许控制面直接依赖其 API。
- E2B 仅作为未来远程 SandboxProvider adapter 候选，不成为权限事实源。
- Dagger 仅用于构建、测试和镜像流水线，不作为不可信 AI 代码的生产安全边界。

普通共享内核 Docker 不得被文档、UI 或产品承诺描述为能够安全运行任意敌对代码。若威胁模型包含恶意多租户代码，必须选择通过 P34.5 攻击 Gate 的 Linux standard/strong profile。

### 11.2 候选比较

| 候选 | 优点 | 主要不足 | 定位 |
|---|---|---|---|
| Rootless Docker + hardened profile | 当前环境易落地，镜像/配额成熟 | 共享内核，不能承诺抵御任意敌对代码 | 开发功能基线 |
| Podman rootless | daemonless/rootless | Windows/运维兼容需验证 | 本地替代候选 |
| gVisor | syscall 隔离更强 | 平台、性能、GPU 兼容成本 | Linux standard 推荐 |
| Incus VM | VM 隔离、生命周期完整 | 镜像、密度、Windows 控制面适配成本 | Linux standard 可选 |
| Kata Containers / microVM | 强隔离 | 资源和运维成本高 | strong 推荐 |
| Firecracker / Cloud Hypervisor | 微虚机底座 | 编排、设备、网络和快照工作量大 | provider 底层候选，不直连控制面 |
| E2B | 托管 sandbox 和 SDK | 外部依赖、数据驻留、成本和契约锁定 | 未来远程 adapter |
| Dagger | 构建 DAG、缓存、CI 体验 | 不是敌对代码隔离边界 | 仅构建流水线 |
| WASI/Wasmtime | capability-oriented | Python/Node/系统工具兼容不足 | 受限工具子通道 |

决定点：

1. 威胁主体是否包含恶意多租户代码。
2. Windows 开发与 Linux 生产是否允许不同 adapter。
3. 是否需要 GPU、浏览器、编译器、长进程和网络。
4. 共享内核是否满足目标安全等级。
5. 快照、冷启动、内存密度、可观测性和运维预算。
6. escape harness 是否能在目标平台稳定通过。
7. Runner pull lease、heartbeat、fencing 和 workload identity 如何映射到 provider。

无论 runtime 为何，都必须 non-root、no-new-privileges、capability drop、只读基础镜像、独立 writable root、默认拒绝网络、禁止 Docker socket/宿主目录/metadata service，并具有资源配额和有界终止。

### 11.3 Overlay/协作网络 ADR 候选

网络实现也必须放在可替换 adapter 后面，不能让 Workspace 权限模型依赖某个 VPN 产品：

| 候选 | 优点 | 主要不足 | 建议定位 |
|---|---|---|---|
| WireGuard + 自研轻量协调 | 协议底座简单、开源、控制力最高 | NAT 穿透、密钥轮换、中继、ACL 和运维工作量最大 | 长期底层候选，不作为 P34.4 首发 |
| Headscale + Tailscale client | 开源自托管协调面，WireGuard 数据面成熟 | 客户端/协议兼容与版本治理需验证；DERP 仍需规划 | 首个自托管 Overlay adapter 候选 |
| NetBird | 开源控制面、管理与策略较完整 | 组件和运维面更大，需验证嵌入与升级边界 | 团队部署候选 |
| Tailscale 托管控制面 | NAT 穿透和使用体验成熟 | 外部 SaaS 依赖、账号/元数据驻留和成本 | 可选便捷 adapter，不作为默认开源事实源 |
| ZeroTier | 成熟虚拟网络和跨平台能力 | 自有网络模型，策略与 OmniBase capability 映射需验证 | 可选兼容 adapter |
| libp2p/自研 P2P | 可塑性与去中心化能力强 | 协议、安全、NAT、中继、升级和可观测性工程极大 | 远期研究，不进入 P34.4 关键路径 |

P34.4 首先冻结 `PeerOverlayProvider` 契约和 fake/local harness，再选择一个自托管 adapter 做最小闭环；选型 Gate 包括 Windows/Linux 客户端、NAT/relay、密钥轮换、节点撤销、ACL 映射、离线行为、升级兼容和元数据泄露。无论采用哪种 Overlay，Sandbox 都只经 Network Broker 接入，不直接成为 Overlay peer。

## 12. P34.0–P34.7 批次

### P34.0：契约与威胁模型

交付本计划、威胁模型、资源/action/risk/API/lifecycle 契约、runtime ADR 模板。

Gate：公开 DTO 无 physical locator；端点—action—角色矩阵完整；攻击测试有自动化路径；非目标明确。

### P34.1：能力平面基础

实现 Registry、lineage、Audit、Idempotency、Approval、Operation 的迁移、domain service 和 `/api/v1` 只读端点。

Gate：跨租户/workspace、枚举、并发写、重复 key、审批过期、审计脱敏测试通过；无任意 SQL。

### P34.2：只读数据能力与 Gateway

把 metadata browser 映射为逻辑 table resource；实现 query AST、issuer/ledger/revoke 和 gateway 只读路径。

Gate：伪造、过期、撤销、scope escalation、wrong audience、workload mismatch 均 fail-closed；query 有行数/时间/bytes 上限。

### P34.3：受控 CRUD 与 DDL

依次实现私有 CRUD、共享 mutation、DDL plan/validate/apply、风险分类、审批和 operation。

Gate：SQL/identifier 注入、无条件 mutation、锁/statement timeout、rollback、角色变化、审批重放和版本冲突测试通过。

### P34.4：Workspace 控制面

拆成四个可独立验收的增量，仍属于同一个 P34.4：

1. **P34.4A — AI Space 权限与资源域**：统一 AI Space/Workspace 命名；实现 membership、Workspace RBAC、user-private/workspace-private/tenant-shared scope、资源树和跨 scope grant。
2. **P34.4B — Workspace/Run 生命周期**：模板 registry、Workspace/Run 分离、desired/observed state、reconciler、pull lease/heartbeat/fencing、配额、归档和 snapshot metadata。
3. **P34.4C — 成员节点与 Overlay 控制面**：Node Registry、Node Attestation、Peer Grant、Service Advertisement、Network Lease、`PeerOverlayProvider`、fake/local provider 和撤销状态机；不连接 Sandbox。
4. **P34.4D — 无真实数据协作 harness**：在受信 Node Daemon 之间验证点对点 Artifact/Git/事件同步、authority node 离线行为和冲突拒绝；不执行不可信代码，不开放数据库或规范 RAG。

Gate：同 Tenant 跨 Workspace 默认拒绝；重复 create/run/pause/archive 幂等；旧 fencing token 无法提交；失败可恢复；撤销后不可调度/不可发布服务；restore 产生新 generation 且无 token/进程/连接；模板无凭据/活跃数据；伪造 Node/Peer/Service/Network Lease 全部 fail-closed；authority 离线时不产生双写。

### P34.5：Sandbox Runtime

拆成四个可独立验收的增量，仍属于同一个 P34.5：

1. **P34.5A — Runner 与执行隔离**：独立 Linux Sandbox Runner、`SandboxProvider`/`RuntimeDriver`、non-root、cgroup、只读 root、独立 writable layer、文件/进程/资源限制和有界终止。
2. **P34.5B — 双平面网络**：每 Workspace/Run 独立 network namespace、默认拒绝 egress、Workspace Network Broker、短期 mTLS workload identity；Sandbox 不直接加入成员 Overlay。
3. **P34.5C — 首个 Overlay adapter**：将经过 P34.4 Gate 的自托管/可替换 provider 接到受信 Node Daemon，只向 Broker 发布显式服务；验证撤销、relay、掉线、重连和节点失陷边界。
4. **P34.5D — 只读能力最小闭环**：攻击矩阵通过后，才让 Sandbox 经 Broker + Capability Gateway 访问 P34.2 只读 schema/rows/RAG/citation；继续关闭 Runtime 写 capability。

Gate：Runner 无核心凭据且不直连 DB/MinIO/Redis；Sandbox 无成员 Overlay identity；默认无法访问宿主 LAN、成员私网、metadata、管理端口、其他 Workspace 和任意外网；Broker 只转发 grant 明确允许的逻辑服务；威胁模型中的 lease/fencing、逃逸、宿主访问、DNS rebinding、relay 滥用、凭据、资源耗尽和跨 workspace 测试全部通过。Docker profile 仅可标记为开发功能基线。

### P34.6：Workspace 数据与 RAG 通道

接入 private CRUD/artifact/derived index、canonical RAG 只读、高风险审批和 snapshot/restore lineage。

Gate：canonical 不可覆写；workspace 隔离；derived promotion 不自动发生；runtime 崩溃不影响核心服务；回滚成功。

### P34.7：总验收与 Agent 前置 Gate

执行生产构建、目标 runtime smoke、故障注入、撤销延迟、备份恢复、审计查询、性能/容量和安全回归。

Gate：全部批次 Gate 通过；已知风险有接受者；文档、代码、迁移、OpenAPI、运行方式一致；此后才允许 Agent 编排。

## 13. 非目标

- 自主 Planner、多 Agent DAG、长循环自我改写；
- Skill marketplace、MCP 任意外部 server 安装；
- 任意 SQL console、数据库直连或凭据下发；
- workspace 自动提升 derived 数据为 canonical；
- workspace 自行扩大 capability 或自我批准；
- V2 全量回填、V1 删除或 BGE-M3 生产 cutover；
- 默认开放互联网、宿主私网、宿主文件系统或 Docker socket；
- 把普通 Docker 宣称为可安全运行任意敌对代码的生产边界；
- 未批准的不可恢复 purge、破坏性 DDL 或跨租户管理。

## 14. 阶段完成定义

1. Registry、capability、approval、audit、operation 和 workspace lifecycle 契约实现一致。
2. 用户控制面与 gateway 完全解耦，外部接口不暴露物理定位。
3. CRUD/DDL 不接受任意 SQL，破坏性操作审批和恢复可执行。
4. 沙箱攻击矩阵和资源耗尽 Gate 全绿。
5. canonical/derived 边界经安全测试证明。
6. capability 撤销、角色变更和 workspace 暂停在 SLA 内生效。
7. production 构建、运行、备份恢复和故障注入通过。
8. Agent 尚未获得任何绕过 workspace/gateway 的宿主能力。
