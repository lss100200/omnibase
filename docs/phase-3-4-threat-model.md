# Phase 3–4 威胁模型：受控数据库能力、AI 工作空间与能力网关

> 状态：P34.0–P34.3 已封板；P34.4 Workspace/Run/Node/lease/fencing/authority 元数据逻辑控制面与 fake/local harness 已完成工程封板；P34.5A0-A3 已落地拒绝型 Sandbox、DB-backed Run lease/runtime proof、互斥 Sandbox capability、幂等预算与 SQLAlchemy durable operation/transition/Audit，真实执行与网络隔离继续冻结
>
> 范围：Resource Registry、受控 CRUD/DDL、Capability Issuer/Ledger/Gateway、Workspace Control Plane、Sandbox Runner/Runtime、RAG/Artifact 通道。
>
> 安全目标：允许工作空间内部自由生成代码、文件、工具和派生状态，同时证明其无法越过 tenant、workspace、canonical-data 和宿主边界。

## 1. 资产与信任边界

保护资产：

- 用户身份、tenant membership、`is_tenant_admin` 和平台管理员能力；
- canonical 文档、chunk、citation、V1/V2 embedding/index metadata；
- tenant-managed 表和 workspace-private 数据；
- Resource Registry physical locator；
- capability signing key、grant ledger、revocation/usage state；
- MinIO 对象、宿主 `.env`、provider key、JWT secret；
- PostgreSQL、Redis、Celery、runtime control socket；
- Sandbox Runner lease、heartbeat、fencing token 和 workload identity；
- workspace 模板、artifact、snapshot、derived index 和 lineage；
- 审计、审批、幂等和 operation 状态。

信任区域：

```text
Zone A  Browser/User
  └─ 不可信输入；只有用户 access JWT

Zone B  Core API Control Plane
  └─ 可信代码；执行 CurrentPrincipal、RBAC、审批、资源解析

Zone C  Capability Issuer/Ledger
  └─ 高敏感控制面；持有签名权与即时撤销状态

Zone D  Capability Gateway
  └─ 唯一允许 sandbox 访问核心能力的入口

Zone E  Sandbox Runtime/Workspace
  └─ 默认不可信；代码、依赖、prompt 和工具均可能恶意

Zone F  Sandbox Runner
  └─ 有限可信 Linux 执行代理；无核心凭据，不直连 DB/MinIO/Redis

Zone G  Data Adapters
  └─ PostgreSQL/RAG/MinIO；只有 adapter 可见 locator/凭据

Zone H  Host/Runtime Control
  └─ 最高敏感；socket、宿主文件、网络和签名 key 不得进入 E
```

Zone E 只能经 Zone D 调用逻辑能力；Zone D 不相信 E 提供的 tenant、用户、schema、path 或 runtime 身份。Zone F 只通过 pull lease/heartbeat/fencing 与控制面协调，不得成为 capability issuer 或 data adapter。

### 1.1 信任域、协作域与网络域不得混同

- `Tenant` 是 Deployment/Organization/Local Installation 的顶层信任域；开源单机版可默认只有一个 Tenant，但 tenant scope、根身份和审计归属仍保留。
- 面向产品的 AI Space 与内部 `Workspace` 是同一个长期协作/隔离域；同 Tenant 成员不自动获得其他 Workspace 的文件、记忆、Artifact、派生数据或 capability。
- `Run`、`Interactive Session` 与 `Sandbox Runtime` 是短期不可信执行实例，不成为成员、授权或长期网络身份的事实源。
- 用户长期记忆与 Workspace 记忆属于不同 scope；跨 scope 读取必须通过可撤销能力投影，不能因“属于同一用户/租户”而全量注入。
- 成员设备 Overlay 是受信 Node Daemon 之间的加密控制网；Sandbox 数据网是每 Workspace/Run 独立、默认拒绝的 network namespace。进入 Overlay 只证明节点建立了加密连接，不代表它拥有任何资源权限。
- Sandbox 不得直接持有成员设备 Overlay identity，也不得把 Overlay 当成可任意横向访问的传统局域网；所有可达服务仍须经过 Workspace Network Broker、mTLS workload identity、Capability Gateway、grant/lease/fencing 和审计。
- 跨 Deployment/Tenant 协作不得合并 tenant ID、共享物理 schema 或互相信任管理员。未来 Federation 必须使用显式外部主体、签名邀请、最小资源副本/引用和可撤销跨域 capability。

## 2. 威胁主体

1. 未认证来源；
2. 已认证普通租户用户；
3. 恶意或被入侵的 tenant admin；
4. 提示注入控制的 workspace/未来 Agent；
5. 恶意生成代码、依赖、文档或 artifact；
6. 持有被盗 capability token 的进程；
7. 被攻陷的 sandbox runtime；
8. 配置错误或有缺陷的核心服务/operator；
9. 同机其他 tenant/workspace；
10. Redis、PostgreSQL、MinIO、runtime 局部故障。

宿主 operator 不作为完全不可对抗攻击者，但其高风险操作必须最小权限并保留独立审计。

## 3. 安全不变量

1. tenant 只能由验证后的 user JWT 或 capability ledger 确定，不能由 body/query/header 自声明。
2. workspace 不能读取其他 workspace/tenant 的资源，即使知道逻辑 ID。
3. 逻辑 ID 永不直接解释为 SQL identifier、文件路径或对象 key。
4. workspace 不拥有数据库、MinIO、JWT、provider 或宿主凭据。
5. canonical 默认只读；derived 不能覆盖或冒充 canonical。
6. capability 不能扩大、不能自签发、可即时撤销，并绑定 workload identity。
7. mutation 必须授权、幂等、审计；R2+ 必须满足审批矩阵。
8. sandbox 崩溃、恶意代码或资源耗尽不能影响核心和其他 workspace。
9. 暂停/归档后不得继续签发 token、发起新操作或保留无限后台任务。
10. 拒绝响应不得泄露跨租户资源是否存在。
11. Workspace 是长期逻辑资源；Run/Interactive Session 是短期可销毁实例，不得成为长期权限事实源。
12. restore 必须创建新 generation、identity 和 runtime，禁止恢复 token、进程、socket、连接或内存凭据。

## 4. 攻击面与控制

### 4.1 身份与 RBAC

威胁：伪造/过期 JWT、停用用户继续访问、角色缓存、跨 tenant claim、混用 user JWT 与 capability。

控制：

- 复用 Phase 2 `CurrentPrincipal`，从 tenant DB 读取 active user 和实时角色；
- 用户控制面只接受 user JWT；gateway 只接受 workload identity + capability；
- 严格验证 `iss/aud/typ/kid/exp/nbf`；
- 角色、tenant、workspace 停用在规定 SLA 内生效；
- 不存在与无权访问统一 404。

### 4.2 Resource Registry / IDOR

威胁：枚举 ID、伪造 parent/owner、修改 locator、跨 workspace 分享、错误差异泄露存在性。

控制：

- opaque random ID，tenant scope 内解析；
- owner、parent、policy 和 lineage 由服务端验证；
- locator 不进入 API/token/log；
- share/promotion 使用独立 operation 和审批；
- 状态码和错误 envelope 统一。

### 4.3 Capability

威胁：盗用、重放、扩大 scope、旧 grant 继续使用、签名 key 泄露、`kid` 注入、wrong audience、delegation loop、次数竞争。

控制：

- 非对称固定算法 allowlist；服务端 key registry，不从 token URL 拉 key；
- token 短期有效且 ledger 每次在线检查；
- `cnf/sub` 绑定 runtime identity；
- grant version、revocation、usage 原子更新；
- 委托只能严格缩权并限制深度；
- key rotation、compromise revoke、emergency stop 有 runbook。

### 4.4 CRUD / Query

威胁：SQL/identifier 注入、复杂过滤耗尽资源、无界导出、越权列读取、全表 mutation、TOCTOU。

控制：

- 结构化 AST、列逻辑 ID、值参数化；
- identifier 仅由 registry/adapter 生成；
- operator/type/function allowlist；
- cursor、行数、bytes、timeout、并发和成本上限；
- mutation 要求 filter、resource version 和 Idempotency-Key；
- 执行前在事务边界重新校验 capability/policy。

### 4.5 DDL

威胁：锁死共享库、类型重写、大索引耗尽磁盘、破坏 internal table、approval 重放、plan/apply 不一致。

控制：

- `plan → validate → risk → approve → apply`；
- managed physical namespace 与 canonical 分离；
- lock/statement timeout、成本估算、配额和 deadline；
- approval 绑定 request hash/resource version；
- apply 前核对 plan digest；
- destructive DDL 默认禁用，开放时必须有恢复点。

### 4.6 Approval / Idempotency / Audit

威胁：AI 自我批准、审批内容替换、同 key 不同请求、pending 双执行、审计漏写、日志泄密、删除审计。

控制：

- requester/approver 分离，workspace 永不能审批；
- 审批绑定 action/resource/version/hash/grant/expiry；
- idempotency 唯一约束和事务状态机；
- mutation + audit/outbox 同事务；
- 审计 append-only、脱敏、受限访问；
- 审计清理属于 R4 平台流程。

### 4.7 Workspace 生命周期

威胁：并发启动多个 runtime、暂停后任务继续、归档未撤销 token、stale reconciler 覆盖新状态、残留对象、snapshot 不一致。

控制：

- desired/observed state、generation、compare-and-swap；
- workspace 级唯一 active runtime；
- pause/archive 先停调度并撤销 capability；
- heartbeat/lease 和幂等 reconcile；
- purge 前资源清单和 retention；
- snapshot 使用一致性 barrier，明确 FS/DB/RAG 边界。

P34.4 当前只实现 17 张 global 表上的控制面元数据：版本化模板、membership/RBAC/scope grant、desired/observed state、generation、Run/Node/Network fencing、snapshot metadata 与 restore-new-identity。Membership mutation 先锁 Workspace aggregate，再锁后重验 actor/target，避免并发移除最后 owner。Run Lease 绑定当前 Node fencing 和实时未过期 attestation；terminal Run 关闭/撤销 lease、清除 runtime/workload identity 后不可复活。`FakeMetadataWorkspaceReconciler` 不创建 runtime；生产默认 `UnavailableWorkspaceReconciler` fail-closed。文件、进程、网络和数据一致性 barrier 仍属于 P34.5/P34.6，不得由 P34.4 的 metadata-only snapshot 冒充。

### 4.8 Sandbox Runner 与 RuntimeDriver

威胁：把 Celery 当作不可信代码执行器、Runner 持有核心凭据、旧 lease 重复提交、双 Runner 控制同一 Run、stale runtime 越权、provider handle 泄露、restore 复活旧身份。

控制：

- Celery 只执行受信任核心任务；不运行 workspace 生成代码。
- 独立 Sandbox Runner 部署在 Linux 节点，无 DB/MinIO/Redis/JWT/provider/signing-key 凭据。
- Runner 只 pull lease，周期 heartbeat；所有 mutation 携带 generation 和单调 fencing token。
- 旧 Run fencing、Node fencing、过期 attestation、过期 lease 或 workspace generation 不匹配时拒绝结果并终止 runtime。
- terminal Run 不接受任何回到 starting/running 的状态提交；终态转换关闭/撤销 lease并清除 runtime/workload identity。
- Runner 通过可替换 `SandboxProvider`/`RuntimeDriver` 执行 `prepare/create/start/exec/cancel/logs/stats/snapshot/restore_new_generation/stop/destroy`。
- sandbox 只访问 Capability Gateway；Runner 不代理任意数据库、对象存储或 Redis 命令。
- `restore_new_generation` 创建新 workload identity，旧 token、进程、连接、PID、socket 和 runtime handle 一律失效。
- provider handle 只存内部控制面，不进入公开 API、token、日志或 workspace。

P34.5A0 已新增严格 `SandboxProvider`/`SandboxAuthorizer` seam、拒绝型生产默认、完整 lease/generation/Run/Node fencing/workload identity/action binding、资源/路径/结构化 argv/只读 root/default-deny network 合约，以及 metadata-only fake harness。P34.5A1 又增加 live lease + capability 组合授权 seam、独立于 workload grant 的 emergency stop/destroy controller authorization、operation ID/request-spec digest exact replay、ambiguous outcome reconciliation 状态机、目标 Linux isolation profile 合约与 `UnavailableSandboxRunner`。P34.5A2 进一步加入 runtime instance 单次绑定、每次新事务重验 P34.4 Run/Node/Lease/fencing/attestation 的 SQLAlchemy verifier、Runner host/profile/identity attestation、独立 transport 和 no-auto-replay coordinator。P34.5A3 用 `0008` 将 read 与 Sandbox lifecycle capability profile 变成数据库级互斥闭集，Sandbox Grant 强制单 Workspace、runtime/workload-bound、不可委派、最长五分钟且不能签发 Gateway bearer token；`capability_usage_reservations` 按 operation ID 幂等扣费，`SqlAlchemySandboxOperationStore` 将 current pointer、append-only transition 与 redacted Audit 同事务持久化，并以 tenant/Workspace/Run/Grant 复合外键和数据库 trigger 拒绝漂移、更新与删除。当前 Docker Desktop 探针仍明确缺少 rootless/userns 与 LSM，因此 provider 仍不装配。这些实现仍不创建进程、文件、容器、socket、网络、挂载或 provider 资源；只证明缺少真实实现时 fail-closed，不能证明独立 Runner、cgroup、seccomp/AppArmor、gVisor/Kata、workload mTLS、有界强杀或敌对代码隔离已经实现。

### 4.9 文件系统与 Artifact

威胁：路径穿越、symlink/hardlink 逃逸、device 文件、压缩炸弹、超大文件、覆盖模板或其他 workspace。

控制：

- API 只接受 artifact ID，adapter 生成 key；
- resolve 后验证仍位于 workspace root；
- 禁止/限制 symlink、hardlink、device、setuid；
- 文件数、大小、解压比、磁盘和输出配额；
- 基础镜像只读，writable layer 独立；
- 上传下载经 gateway。

### 4.10 网络

威胁：访问 Docker socket、metadata、宿主私网、PostgreSQL/Redis/MinIO 管理端口、DNS rebinding、SSRF、无限下载/外传。

控制：

- egress 默认拒绝，只允许 gateway；
- gateway 专用内部网络和服务身份；
- 禁止 loopback、private/link-local、metadata 和 runtime socket；
- 域名解析前后均校验，限制 redirect；
- 连接数、流量、响应大小、DNS 和时间上限；
- 外网扩权属于 R3 限时审批。

### 4.11 进程与资源耗尽

威胁：root escape、危险 syscall、fork bomb、OOM、inode/日志耗尽、孤儿进程、持久化后台任务。

控制：

- non-root、user namespace、no-new-privileges、capability drop；
- seccomp/AppArmor/等价 profile；
- PID、CPU、内存、swap、磁盘、inode、时间、并发和输出限制；
- 终止整个 cgroup/job object；
- 禁止宿主 cron/service/socket 挂载；
- 核心与 workspace 使用独立资源池。

### 4.12 RAG 与提示注入

威胁：文档指令诱导越权、citation 伪造、derived 污染 canonical、prompt/正文进入日志、模型资源耗尽。

控制：

- 检索内容视为不可信数据，不能改变 capability；
- tool invocation 由模型外 gateway 强制授权；
- citation 由 canonical resource/chunk ID 生成验证；
- canonical/derived 分 locator/policy；
- prompt、正文和 token 不写审计；
- query/top-k/并发/模型时间/输出有上限；
- workspace 不得修改 Phase 1.6 V1/V2 索引。

### 4.13 供应链与模板

威胁：恶意镜像、依赖 post-install、模板夹带 `.env`/key、未固定版本、workspace 污染模板。

控制：

- 镜像/template digest 固定、签名、SBOM 和扫描；
- 模板 POST 在 caller-owned transaction 内锁定并重验 active tenant admin；`(tenant_id, template_key, version)` 使用 PostgreSQL 原子 conflict handling，仅完整相同语义可 replay。
- 生成器 allowlist，不复制活跃目录；
- secret scan；
- 模板只读，workspace copy-on-write；
- 依赖安装默认无网络或经受控代理；
- 升级产生 operation/snapshot，不静默原地替换。

## 5. 风险与审批安全矩阵

| 操作 | 风险 | 最低主体 | 审批 | 必需保护 |
|---|---:|---|---|---|
| canonical RAG search/read | R0 | 有 grant 的用户/workspace | 无 | 限流、top-k/bytes、审计 |
| workspace-private CRUD | R1 | owner/grant | 策略允许可无 | 幂等、配额、版本 |
| artifact 可恢复写/删 | R1 | owner/grant | 无 | size/hash、retention |
| create table/add nullable column | R2 | tenant admin | 策略或人工 | plan、cost、timeout |
| share derived resource | R2 | owner + tenant admin | 人工 | lineage、目标 scope |
| drop/type narrowing/bulk delete | R3 | tenant admin | 明确人工 | hash、版本、恢复点 |
| enable external network | R3 | tenant admin | 明确人工/限时 | domain/IP/流量约束 |
| purge workspace/snapshot | R3 | tenant admin | 明确人工 | retention、inventory |
| runtime baseline/key/network policy | R4 | platform admin | 双人/离线 | 独立审计和回滚 |

## 6. 攻击测试矩阵

| ID | 攻击 | 预期结果 | 自动化层 | Gate |
|---|---|---|---|---|
| AUTH-01 | 伪造/过期/wrong-audience user JWT | 401，标准错误 | API unit/integration | P34.1 |
| AUTH-02 | 用户停用/角色降级后复用 token | SLA 内失效 | DB integration | P34.1 |
| IDOR-01 | tenant A 读取 tenant B resource ID | 统一 404 | security integration | P34.1 |
| IDOR-02 | workspace A 读取 workspace B 私有资源 | 404 | security integration | P34.1 |
| CAP-01 | 修改 action/resource/grant version | 401/403 并审计 | integration | P34.2 |
| CAP-02 | 被盗 token 从不同 runtime 使用 | identity mismatch | gateway integration | P34.2 |
| CAP-03 | revoke 后继续调用 | 撤销 SLA 内拒绝 | integration | P34.2 |
| CAP-04 | 并发超出 calls/budget | 原子拒绝，不超发 | concurrency | P34.2 |
| CAP-05 | 委托扩大 scope/超深度 | 403 | property/unit | P34.2 |
| SQL-01 | filter/value SQL injection | 当普通值处理 | integration | P34.2 |
| SQL-02 | display/column name identifier injection | locator 不受影响 | migration integration | P34.3 |
| SQL-03 | 无条件 update/delete | 422/403 或审批 operation | API contract | P34.3 |
| SQL-04 | 复杂 query/大结果 | timeout/limit | load/integration | P34.3 |
| DDL-01 | 操作 canonical/internal table | 404/403 | DB integration | P34.3 |
| DDL-02 | 审批后替换请求/资源变化 | 409/403，审批失效 | integration | P34.3 |
| DDL-03 | 大表锁/索引耗尽 | timeout/cancel/rollback | fault test | P34.3 |
| IDEM-01 | 同 key/hash 并发提交 | 仅执行一次 | concurrency | P34.1 |
| IDEM-02 | 同 key 不同 hash | 409 | API unit | P34.1 |
| APR-01 | workspace 自我审批 | 403 | policy unit | P34.1 |
| AUD-01 | success/fail/reject mutation | 均有脱敏审计 | integration | P34.1 |
| AUD-02 | 输入含 JWT/key/password | 日志无原值 | secret regression | P34.1 |
| LIFE-01 | 并发 start | 最多一个 runtime | concurrency | P34.4 |
| LIFE-02 | pause/archive 与任务竞态 | 撤销、停调度、有界终止 | fault test | P34.4 |
| LIFE-03 | stale reconciler generation | 不覆盖新状态 | integration | P34.4 |
| RUN-01 | 两个 Runner 获取/续租同一 Run | 仅最高 fencing token 可提交 | concurrency/fault | P34.4 |
| RUN-02 | lease/attestation 过期或 Node 已重新 fencing 的 Runner 提交结果 | 拒绝并有界终止旧 runtime | integration | P34.4 |
| RUN-TERM-01 | terminal Run 被旧 holder 改回 starting/running | 409；lease 已关闭/撤销，runtime/workload identity 已清除 | unit/integration | P34.4 |
| WS-01 | 同 Tenant 成员读取其他 Workspace | 404/fail-closed，无隐式 membership | unit/integration | P34.4 |
| WS-OWNER-01 | 两个 owner 并发自降级/互相停用 | Workspace aggregate 串行化；至少保留一个 active owner | concurrency/integration | P34.4 |
| WS-02 | 模板/snapshot 夹带凭据、宿主路径或 runtime handle | 422/拒绝，安全元数据不落入危险字段 | contract/unit | P34.4 |
| WS-TPL-01 | 已通过依赖检查的 admin 在写事务前被撤权，或两个请求并发复用同 key/version | 事务内重验；只有完整相同语义 replay，不同内容 409 | concurrency/integration | P34.4 |
| NODE-01 | Browser/Header 伪造 Node attestation | 无内部 Node/lease 路由；typed service 拒绝 | contract/unit | P34.4 |
| NODE-02 | 撤销 Node 与新 authority/peer/service/lease 并发 | Workspace→Node→领域记录统一锁序；Run/peer/service/network/authority 同步失效 | unit/integration | P34.4 |
| LEASE-01 | 过期/撤销/错误 holder 续租或提交 | 409/fail-closed，DB clock 为准 | unit/integration | P34.4 |
| NET-LEASE-01 | 旧 logical Network Lease token 重放或签发时诱导 provider 副作用 | cursor 当前 token 不匹配即拒绝；签发不调用 provider、不产生 socket/route/peer | unit/integration | P34.4 |
| FENCE-01 | 旧 token/epoch 在新 holder 后提交 | 409，不能覆盖新状态 | concurrency/unit | P34.4 |
| AUTH-01 | authority 离线或过期后两个 Node 写入 | 只读/拒绝，不自动选举、不双写 | unit/integration | P34.4 |
| AUTH-02 | 同 sequence 不同 digest 或错误 previous digest | conflict/fail-closed，不自动 merge | unit | P34.4 |
| SBX-A0-01 | 缺失可信 authorizer/provider 仍请求 prepare/create/start/exec | code-only unavailable/reject，无副作用 | unit | P34.5A0 |
| SBX-A0-02 | stale Run/Node fencing、过期/撤销 lease、action/binding 不匹配 | 每次调用在线拒绝，不以 handle/UUID 作为授权 | unit | P34.5A0 |
| SBX-A0-03 | 绝对/drive/traversal/保留路径、单字符串 command/任意 env、无限资源、非 deny-all 网络 | 构造期拒绝 | contract/unit | P34.5A0 |
| SBX-A0-04 | metadata-only fake 被要求执行/取消命令、恢复伪造 snapshot、重放 restore 或让已销毁 Run 重建 | hard deny/conflict，零进程/文件/socket/provider side effect，terminal Run 不复活 | unit/source audit | P34.5A0 |
| SBX-A1-01 | live lease 与 capability 的 tenant/workspace/run/identity/action/expiry 任一不一致 | 组合 authorizer 拒绝，provider/Runner 无副作用 | unit | P34.5A1 |
| SBX-A1-02 | workload grant 撤销后继续普通 lifecycle，或伪造 controller 请求 destroy | 普通路径拒绝；只有独立可信 controller + current generation/fencing/deadline 可授权 emergency control | unit | P34.5A1 |
| SBX-A1-03 | 同 operation ID payload/spec drift、ambiguous provider outcome 自动重跑、terminal operation 复活 | conflict；只能显式 reconciliation；terminal 不可转换 | unit | P34.5A1 |
| SBX-A1-04 | 未装配真实 Linux Runner 或 isolation profile 不完整时 execute/terminate | `sandbox_runner_unavailable`/构造期拒绝，零 runtime side effect | contract/source audit | P34.5A1 |
| SBX-A2-01 | Run Lease 当前但 runtime instance/workload identity 未绑定、已漂移或终态已清除 | DB-backed verifier 拒绝，transport 前无副作用 | unit | P34.5A2 |
| SBX-A2-02 | Runner host 的 Node fencing、profile digest、identity、有效期或 evidence 不匹配 | host attestation 拒绝，operation 记为 failed，不 dispatch | unit/probe | P34.5A2 |
| SBX-A2-03 | dispatch 后 timeout、进程崩溃或 receipt operation ID 漂移 | operation 标记 ambiguous/reconciliation-required，同 operation ID 禁止自动重放 | unit | P34.5A2 |
| SBX-A2-04 | Docker Desktop 缺少 rootless/userns 或 LSM 仍尝试装配 hardened provider | host probe not-ready，保持 provider/transport unavailable，不降低 profile | operator/source audit | P34.5A2 |
| SBX-A3-01 | 在同一 Grant 混合 read 与 Sandbox action、缺少 workload digest、扩大到多 Workspace 或委派 Sandbox Grant | service 与 `0008` CHECK 双重拒绝；Sandbox Grant 不签发 Gateway bearer token | unit/sentinel PostgreSQL | P34.5A3 |
| SBX-A3-02 | 同 operation ID 重放导致重复扣 budget，或复用 operation ID 搭配不同 tenant/grant/workspace/runtime/action | exact replay 只保留一条 reservation/一次 calls+cost；binding drift 拒绝 | unit/concurrency sentinel | P34.5A3 |
| SBX-A3-03 | 跨 tenant/Workspace/Run 写 operation、并发重复 claim dispatch、直接修改/删除 transition/reservation | 复合 FK、行锁状态机、append-only trigger；并发只有一个 dispatch winner | sentinel PostgreSQL | P34.5A3 |
| SBX-A3-04 | operation transition 成功但 Audit 丢失，或 populated `0008` 被降级抹去证据 | transition/current pointer/Audit 同事务；存在 Grant/reservation/operation/transition 时 downgrade fail-closed | sentinel PostgreSQL | P34.5A3 |
| RUN-03 | Runner 尝试直连 DB/MinIO/Redis | 网络和凭据均不可用 | sandbox harness | P34.5 |
| RUN-04 | Runner task 夹带 JWT/locator/凭据 | 控制面拒绝，审计脱敏 | contract/security | P34.5 |
| FS-01 | `../`/绝对路径/编码绕过 | 拒绝，root 外无变化 | sandbox harness | P34.5 |
| FS-02 | symlink/hardlink/device escape | 拒绝 | sandbox harness | P34.5 |
| FS-03 | zip bomb/inode exhaustion | 有界终止 | sandbox harness | P34.5 |
| NET-01 | socket/host/private/metadata | 无法连接 | sandbox harness | P34.5 |
| NET-02 | DNS rebinding/redirect 私网 | 拒绝 | proxy test | P34.5 |
| PROC-01 | fork bomb/孤儿进程 | 全 cgroup/job 终止 | sandbox harness | P34.5 |
| PROC-02 | CPU/OOM/无限输出 | workspace 终止，核心健康 | load/fault | P34.5 |
| HOST-01 | 读取 `.env`/key/model cache | 无路径/权限 | sandbox harness | P34.5 |
| CROSS-01 | 访问其他 workspace layer | 无法读取 | sandbox harness | P34.5 |
| RAG-01 | prompt injection 扩大 capability | gateway 拒绝 | adversarial E2E | P34.6 |
| RAG-02 | derived 覆写 canonical/V1/V2 | 403，canonical 不变 | integration | P34.6 |
| SNAP-01 | 不一致/跨版本 restore | 安全失败或按契约恢复 | recovery | P34.6 |
| SNAP-02 | restore 试图复活 token/进程/socket/连接 | 新 generation/identity，旧运行态均失效 | recovery/security | P34.6 |
| CRASH-01 | runtime/Redis/DB/MinIO 故障 | 核心可用、无越权 fail-open | chaos | P34.7 |

## 7. 分批 Gate

P34.0：

- 威胁、资产、边界、action、risk 和攻击矩阵经评审；
- 高风险操作有审批/恢复策略；
- runtime ADR 决定点明确；
- 未开始 Agent 编排。

P34.1–P34.3：

- 跨 tenant/workspace、IDOR、SQL/identifier 注入、审批重放全绿；
- 不存在任意 SQL 或公开 locator；
- mutation 审计、幂等闭环；
- PostgreSQL 锁、查询和磁盘风险有界。

P34.4：

- Workspace membership/RBAC/scope grant 与同 Tenant 跨 Workspace默认拒绝通过；
- Workspace aggregate last-owner、模板事务内 admin 重验/PostgreSQL 自然幂等、desired/observed state、generation、Node-fenced Run lease、terminal 不可复活、snapshot metadata 和 restore-new-identity Gate 通过；
- 实时 attestation、Network lease cursor、无 provider 副作用的逻辑签发、Node/Peer/Service/Network/Authority 统一锁序撤销与无真实数据协作冲突 Gate 通过；
- Browser API 不暴露 attestation、heartbeat、lease、fencing、provider activation 或 authority；
- 不执行不可信代码，不访问真实成员网络、业务数据、MinIO、Redis 或 canonical RAG。
- 当前验证证据为 focused `83 passed`（Workspace service `48` + Overlay/Collaboration `27` + API contract `8`）、Backend 非 integration `767 passed / 9 skipped / 11 deselected`、Mypy `105 source files / 0 issues`、fresh R6 `1 + 4 + 57 passed / 1 deselected`；这些数字不构成真实 Overlay、VPN 或 Sandbox 安全声明。

P34.5–P34.6：

- P34.5A0 先证明拒绝型默认、严格 DTO、在线 authorization seam、metadata-only harness 与零真实执行/联网副作用；它不是 runtime isolation Gate；
- 生命周期并发和撤销稳定；
- sandbox 逃逸、网络、文件、进程、资源矩阵全绿；
- workspace 只能访问 gateway；
- canonical/derived 隔离和恢复演练通过。

P34.7：

- 全矩阵在目标生产 runtime 重跑；
- capability 撤销、暂停、故障恢复达到 SLA；
- 恶意 workspace 负载下核心保持健康；
- Git/日志/审计无凭据、locator、正文或运行工件；
- 全绿后才允许 Agent、Skill、MCP。

## 8. Runtime ADR 当前推荐与必答问题

当前推荐方案 B：OmniBase 自研 Workspace/Run 控制面、Capability Gateway、Runner 协议和可替换 SandboxProvider。

- dev：hardened Docker 仅验证功能和接口，不构成任意敌对代码安全保证。
- Linux standard：推荐 gVisor；需要传统系统容器/VM 生命周期时可选 Incus VM。
- strong：推荐 Kata Containers。
- Firecracker/Cloud Hypervisor：未来 provider 底层候选，控制面不得直接耦合。
- E2B：未来远程 provider adapter，不作为权限事实源。
- Dagger：仅构建/测试流水线，不作为 runtime 安全边界。

普通共享内核 Docker 不得宣称能够安全运行任意敌对代码。进入多租户不可信代码生产前，必须在目标 Linux standard/strong profile 上通过 P34.5/P34.7 全攻击矩阵。

1. 目标攻击者是否包含恶意多租户代码；共享内核是否可接受。
2. Windows 开发与 Linux 生产是否使用不同 adapter，安全语义如何一致。
3. Docker/Podman/gVisor/Kata/Firecracker/WASI 的 escape、性能和运维证据。
4. workload identity、默认拒绝网络和 gateway 专用通道如何实现。
5. PID/CPU/内存/磁盘/inode/输出/时间如何有界终止。
6. snapshot 的文件、数据库、artifact、derived index 一致性边界。
7. runtime control plane 失联时必须如何 fail-closed。
8. pull lease、heartbeat、fencing token 和 restore-new-generation 如何映射到每个 provider。

## 9. 非目标与残余风险

非目标：

- 不证明宿主 operator 恶意时的机密性；
- 不实现自主 Agent、多 Agent、Skill marketplace 或开放 MCP；
- 不承诺支持任意语言、syscall、GPU；兼容面由 runtime ADR 决定；
- 不承诺所有 DDL 自动回滚；不安全类型必须禁用或要求恢复点；
- 不自动提升 derived 为 canonical；
- 不改变 Phase 1.6 的 V1 权威地位或触发 V2 cutover。
- 不把普通 Docker、Dagger 或 Celery 描述为敌对代码安全沙箱。

残余风险必须在 P34.7 前记录接受者、影响、监控、应急停止和回滚方案。未明确接受的跨 tenant、凭据泄露、宿主逃逸、canonical 覆写或无限资源风险一律阻断阶段完成。
