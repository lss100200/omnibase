# OmniBase

> 自托管、AI 原生的个人知识工作台。以数据库为底座，内置生产级 RAG、多智能体编排与 Skill/MCP 扩展生态。

[![Status](https://img.shields.io/badge/status-Public%20Preview-orange)](docs/handover-report.md)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)

> [!IMPORTANT]
> OmniBase 当前以 **Public Preview** 形式开放源码：认证、租户边界、受控数据、
> Capability Gateway、SDK、RAG、维护者地图，以及 P34.4 Workspace/Run/Node
> 元数据控制面已形成可验证基础设施；任意代码 Sandbox、真实 Overlay Network、
> 真实数据通道和 Agent Runtime 尚未交付。请勿把普通 Docker 容器或 P34.4 fake harness
> 当作可以安全运行敌对代码的生产沙箱。当前真实状态与已验证证据见
> [交接报告](docs/handover-report.md)；首次公开发布条件见
> [Public Preview Release Checklist](docs/public-preview-release-checklist.md)。

## ✨ 项目特色

- **🗄 数据库为底座**：PostgreSQL + pgvector 单库承载关系数据与向量数据，事务一致、运维单一。
- **🔍 生产级 RAG**：多格式解析 → 语义分块 → 混合检索（向量 + BM25 + 精确）→ 重排 → 引用回链。
- **🤖 多智能体编排**：Planner + Specialist（Librarian / Curator / Archivist / Engineer）协同，可视化任务面板。
- **🧩 Skill & MCP**：Skill 系统与 MCP 客户端作为一等公民，扩展协议稳定可开源。
- **📊 数据底座与受控管理方向**：当前提供安全的只读元数据浏览；Phase 3-4 将增加受控表设计、行级 CRUD、迁移预览和逻辑资源授权，始终不开放任意原始 SQL。

## 🚀 快速开始（5 步）

> 前置条件：[Docker Desktop](https://www.docker.com/products/docker-desktop/) 已安装并运行。

```bash
# 1. 克隆仓库
git clone https://github.com/lss100200/omnibase.git
cd omnibase

# 2. 复制环境变量模板
cp .env.example .env

# 3. 启动所有服务（首次会拉镜像，约 3-5 分钟）
make up

# 4. 执行数据库迁移
make migrate

# 5. 打开浏览器
#    前端：http://localhost:3000
#    后端 API 文档：http://localhost:8000/docs
```

首次访问前端，注册任意邮箱 + 密码即可登录（无需邮箱验证）。

### 可选：配置生成模型

知识检索和引用在未配置 LLM 时仍可运行，但 AI 问答会通过 SSE 返回明确的配置错误，不会伪造模型回答。如需完整流式问答，请在本地 `.env` 中配置 OpenAI-compatible Provider：

```env
LLM_API_KEY=<仅保存在本地，不要提交>
LLM_API_BASE_URL=https://api.deepseek.com/v1
LLM_MODEL=deepseek-chat
```

配置后重启 backend。CPU 冷缓存下的首次问答可能因加载 `bge-reranker-v2-m3` 耗时数分钟；本机实测约 350 秒，模型预热后的 provider-backed 流式验收约 4.1 秒。真实密钥、JWT 和授权请求不得写入 Git、日志或验收证据。

## 🛠 常用命令

```bash
make help          # 查看所有命令
make up            # 启动所有服务（后台）
make down          # 停止所有服务
make logs          # 查看实时日志
make ps            # 查看服务状态
make migrate       # 执行数据库迁移
make migrate-new m="add users table"  # 创建新迁移
make test          # 运行后端测试
make lint          # 运行所有 lint 检查
make backend-shell # 进入后端容器
make frontend-shell # 进入前端容器
```

## 📐 架构概览

```text
Next.js Web UI
    │  same-origin /api/v1
    ▼
Main FastAPI ── Auth / Tenant / Documents / Browser RAG
    │          Control Plane / Controlled Data / Workspace governance
    ├── PostgreSQL + pgvector
    ├── MinIO
    └── Redis + Celery

Trusted workload SDK
    │  /gateway/v1 + short-lived capability
    ▼
Independent Capability Gateway (rejecting by default)
    └── logical Resource resolution + bounded read adapters + audit
```

Main API 与 Capability Gateway 是两个独立 ASGI 边界。P34.4 已解冻 Browser
Workspace 治理 API，以及内部 lease/fencing、Node/Overlay 逻辑控制记录和无真实数据
协作 harness；这些组件不运行代码、不打开真实成员网络，也不连接真实 Workspace/RAG
数据。Agent Runtime、任意代码 Sandbox、真实 Overlay adapter/成员网络和公共任意 SQL
仍被冻结在 P34.5 及后续阶段。详见
[AI 维护者地图](docs/maintainers/ai-maintainer-map.md)。

P34.4 当前的 Network Lease 只是由 `network_lease_cursors` 单调分配 fencing token 的
逻辑授权，签发时不会调用任何真实或 fake provider。Run Lease 还绑定当前 Node fencing
token，并在使用时重新验证未过期的 attestation；Run 一旦进入 stopped/succeeded/failed/
cancelled 终态便不能被旧 holder 复活。上述控制面安全事实不等于已经交付 VPN、Overlay
数据面、Sandbox 或代码执行环境。

## 🗺 路线图

| Phase | 目标 | 状态 |
|---|---|---|
| **Phase 0** | 地基（认证、上传、元数据解析） | ✅ 完成 |
| Phase 1 | RAG 内核（分块、Embedding、混合检索、重排） | ✅ 完成 |
| **Phase 1.5** | RAG 硬化（异步 worker、可靠重试、生命周期保护、SSE 韧性、评估接缝） | ✅ 完成：确定性测试、异步摄取及 provider-backed SSE/citation 运行时验收通过 |
| **Phase 1.6** | Embedding/Index 双通道工程（BGE-M3/1024d 评估） | ✅ 工程与 CPU benchmark 完成；V1 仍为权威主通道，生产 V2 回填/cutover 冻结 |
| **Phase 2** | API 基础设施硬化（`/api/v1`、Request ID、请求边界、限流、实时主体/RBAC） | ✅ 已完成并本地封板 |
| **Phase 3-4** | **安全 AI 工作空间与能力平台 / Secure AI Workspace & Capability Platform**（受控数据、API/SDK 解耦、模板、沙箱、能力网关、审批与审计） | 🚧 P34.1–P34.3 已完成并封板；P34.4A–D 的 17 表 metadata control plane、Browser governance、lease/fencing 与 synthetic collaboration harness 已完成工程 Gate；P34.5 Sandbox/真实 Overlay/数据通道与 Agent Runtime 继续冻结 |
| Phase 5 | Agent 编排（作为工作空间内的受约束负载运行） | ⏳ 必须等待 Phase 3-4 总 Gate |
| Phase 6 | Skill + MCP 扩展生态 | ⏳ 待 Phase 3-4/5 |
| Phase 7 | 开源与发布工程（文档、Demo、部署脚本、版本治理） | 🚧 Public Preview 已启动，持续完善 |

详见 [Phase 1.6 及后续实施计划](docs/phase-1-6-and-beyond-implementation-plan.md)、[Phase 3-4 统一实施计划](docs/phase-3-4-secure-ai-workspace-implementation-plan.md)与 [Phase 3-4 威胁模型](docs/phase-3-4-threat-model.md)。

Phase 3-4 的固定顺序是：安全契约 → Resource Registry/Audit/Operation/Approval/Idempotency → 只读能力网关与 SDK → 结构化 CRUD/DDL → 模板与空沙箱 → 隔离 Gate 后接只读数据 → 私有写入与 promotion → 生产总验收。workspace 是长期逻辑资源，run/session 是可销毁执行实例；普通 Docker 仅作为开发基线，不声明可安全运行任意敌对代码。

## 🧭 AI 维护与故障恢复入口

OmniBase 将“换模型后仍能修复、下载源码后仍能恢复”视为源码完整性的一部分。
本地 AI 或新维护者应按以下顺序建立上下文：

1. [`AGENTS.md`](AGENTS.md)：仓库级维护契约、冻结边界和安全工作流。
2. [`maintenance-map.json`](docs/maintainers/maintenance-map.json)：13 个模块、入口、依赖、验证命令和恢复路径的机器可读地图。
3. [`security-invariants.md`](docs/maintainers/security-invariants.md)：16 条不可破坏的安全不变量。
4. [`ai-maintainer-map.md`](docs/maintainers/ai-maintainer-map.md)：调用链、API/鉴权/解耦入口、影响矩阵和故障恢复说明。
5. [`handover-report.md`](docs/handover-report.md)：当前阶段状态与实际验证证据。

维护者地图由 `scripts/maintenance/validate_maintainer_map.py` 验证并纳入 CI，避免
文件移动、模块新增或依赖变化后留下失效入口。任何改变公共接口、模块依赖、
安全不变量或恢复路径的提交，都应同步更新地图。

## 🤝 贡献

项目处于 Public Preview。Issue、文档改进、测试补强和边界清晰的小型修复均欢迎；
涉及鉴权、租户、数据库 lifecycle、Capability Gateway、迁移、恢复或 P34 冻结范围的
变更，请先阅读 [贡献指南](CONTRIBUTING.md)、[`AGENTS.md`](AGENTS.md) 和
[安全不变量](docs/maintainers/security-invariants.md)。安全问题请勿公开披露，按
[安全策略](SECURITY.md) 提交。

## 📄 协议

[Apache License 2.0](LICENSE) © 2026 OmniBase Contributors
