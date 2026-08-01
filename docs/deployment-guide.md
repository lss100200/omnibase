# OmniBase 部署指南

> 记录 Phase 0 开发过程中遇到的所有部署坑及解决方案。新人按此文档可在 1 小时内完成部署。

---

## 目录

1. [环境要求](#1-环境要求)
2. [Docker Desktop + WSL2 配置（Windows）](#2-docker-desktop--wsl2-配置windows)
3. [国内网络优化](#3-国内网络优化)
4. [镜像获取策略](#4-镜像获取策略)
5. [启动服务](#5-启动服务)
6. [数据库迁移](#6-数据库迁移)
7. [常见问题排查](#7-常见问题排查)
8. [已知技术约束](#8-已知技术约束)

---

## 1. 环境要求

| 组件 | 最低版本 | 说明 |
|---|---|---|
| Docker Desktop | 4.30+ | 含 Docker Engine 29+ 和 Compose v2 |
| WSL 2 | 2.0+ | Windows 必须；Linux/macOS 不需要 |
| 内存 | 8 GB | 推荐 16 GB（pgvector + embedding 会吃内存） |
| 磁盘 | 10 GB | 镜像 ~3 GB + 数据卷 |
| 浏览器 | Chrome 90+ / Firefox 88+ / Edge 90+ | 需支持 ES2020 |

---

## 2. Docker Desktop + WSL2 配置（Windows）

### 2.1 BIOS 虚拟化

**症状**：Docker Desktop 启动后报 "Docker Desktop is unable to start"。

**原因**：BIOS 中硬件虚拟化（VT-x / AMD-V）未开启。

**解决**：
1. 重启电脑，进 BIOS（华硕按 F2，联想按 F1，戴尔按 F2）
2. 找到虚拟化选项：
   - Intel CPU：`Intel Virtualization Technology` / `VT-x`
   - AMD CPU：`SVM Mode` / `AMD-V`
3. 设为 `Enabled`，保存退出（F10）

**验证**：任务管理器 → 性能 → CPU，查看"虚拟化"是否为"已启用"。

### 2.2 WSL 2 内核未安装

**症状**：Docker Desktop 报 "WSL 2 installation is incomplete" 或 "used by another program"。

**解决**：
```powershell
# 方法 1：winget 安装（推荐，国内可用）
winget install --id Microsoft.WSL --accept-package-agreements

# 方法 2：手动下载内核更新包
# https://wslstorestorage.blob.core.windows.net/wslblob/wsl_update_x64.msi
```

安装后重启 Docker Desktop。

### 2.3 Docker daemon.json 配置

配置文件位置：`%USERPROFILE%\.docker\daemon.json`

```json
{
  "builder": { "gc": { "defaultKeepStorage": "20GB", "enabled": true } },
  "experimental": false,
  "registry-mirrors": [
    "https://bpepfdl5.mirror.aliyuncs.com",
    "https://docker.1ms.run",
    "https://docker.m.daocloud.io"
  ]
}
```

修改后重启 Docker Desktop 使配置生效。

---

## 3. 国内网络优化

### 3.1 Docker 镜像加速

在 `daemon.json` 中添加 `registry-mirrors`（见 2.3）。推荐：
- **阿里云个人加速器**：`https://<你的ID>.mirror.aliyuncs.com`（去 https://cr.console.aliyun.com 获取）
- **docker.1ms.run**：公共镜像代理
- **docker.m.daocloud.io**：DaoCloud 公共镜像

**注意**：阿里云个人加速器对 `library/*` 官方镜像只做 proxy 回源，不缓存。对大镜像（pgvector 400MB）效果有限。

### 3.2 Dockerfile 国内源优化

本项目的 Dockerfile 已内置国内源优化：

**后端**（`backend/Dockerfile`）：
- apt 源 → 清华 TUNA
- uv 安装 → jsDelivr CDN（`cdn.jsdelivr.net`）
- PyPI → 清华 TUNA（`UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple`）

**前端**（`frontend/Dockerfile`）：
- apk 源 → 清华 TUNA
- npm registry → npmmirror（`registry.npmmirror.com`）
- pnpm registry → npmmirror

### 3.3 网络速度诊断

如果构建/拉取极慢，先诊断：

```powershell
# 测国内 CDN 速度
Invoke-WebRequest -Uri "https://registry.npmmirror.com/-/binary/node/v20.10.0/node-v20.10.0-win-x64.7z" -OutFile "$env:TEMP\test.7z"
# 如果 < 1 MB/s，说明你的国际+国内带宽都差，需要考虑离线方案

# 查公网 IP 和运营商
Invoke-RestMethod -Uri "https://ipinfo.io/json"
```

---

## 4. 镜像获取策略

### 4.1 正常拉取（网络好时）

```bash
docker pull pgvector/pgvector:0.8.5-pg15-bookworm
docker pull minio/minio:RELEASE.2024-10-13T13-34-11Z
docker pull redis:7.4-alpine
docker pull python:3.11-slim-bookworm
docker pull node:20-alpine
```

### 4.2 离线导入（网络差时）

如果拉取持续失败（国内带宽 < 1 MB/s），用 tar 导入方案：

**步骤 1：在另一台网络好的机器上导出**

```bash
docker pull pgvector/pgvector:0.8.5-pg15-bookworm
docker save pgvector/pgvector:0.8.5-pg15-bookworm -o pgvector.tar

docker pull node:20-alpine
docker save node:20-alpine -o node.tar
```

**步骤 2：传到目标机器**

通过 U 盘、网盘、微信文件传输助手等方式传到目标机器。

**步骤 3：导入**

```bash
docker load -i pgvector.tar
docker load -i node.tar
```

**步骤 3 替代方案：用在线服务下载 tar**

如果找不到另一台 Docker 机器，用在线服务：
- **Harpoon**：https://harpoon.jlustri.dev/ （输入镜像名，Submit，等处理完下载 tar）
- **Repoflow**：https://www.repoflow.io/tools/docker-save （浏览器内转换+下载）

### 4.3 所需镜像清单

| 镜像 | 版本 | 大小 | 用途 |
|---|---|---|---|
| pgvector/pgvector | 0.8.5-pg15-bookworm | ~820 MB | 数据库 + 向量扩展 |
| minio/minio | RELEASE.2024-10-13T13-34-11Z | ~227 MB | 对象存储 |
| redis | 7.4-alpine | ~58 MB | 缓存 + 任务队列 |
| python | 3.11-slim-bookworm | ~198 MB | 后端基础镜像 |
| node | 20-alpine | ~283 MB | 前端基础镜像 |

---

## 5. 启动服务

### 5.1 配置 .env

```bash
cp .env.example .env
```

编辑 `.env`，**必须修改**：
- `POSTGRES_PASSWORD`：改成强密码
- `MINIO_ROOT_PASSWORD`：改成强密码（至少 8 位）
- `JWT_SECRET`：生成 64 字符随机字符串

```powershell
# 生成 JWT_SECRET
-join ((48..122) | Get-Random -Count 64 | ForEach-Object {[char]$_})
```

### 5.2 启动

```bash
make up
# 或
docker compose up -d --build
```

首次构建后端约 5-10 分钟（下载 Python 依赖），前端约 2 分钟。

### 5.3 验证

```bash
make ps
# 所有 5 个服务应显示 "healthy"
```

访问：
- 前端：http://localhost:3000
- 后端 API 文档：http://localhost:8000/docs
- MinIO 控制台：http://localhost:9001

---

## 6. 数据库迁移

```bash
make migrate
# 或
docker compose exec backend alembic upgrade head
```

首次运行会创建 `omnibase_meta` schema + `tenants` 表。注册新用户时自动创建租户 schema + 业务表（users / documents / embeddings）。

> **安全要求**：不得在常规开发或生产数据库运行 `backend/tests/cleanup.py`。任何可能执行 DROP/DELETE 的集成测试都必须使用专用 `TEST_DATABASE_URL`、`OMNIBASE_INTEGRATION_TESTS=1`、测试 sentinel、受限数据库角色和隔离的测试 Compose 环境；禁止按 `tenant_%` 通配符清理租户资源。

---

## 7. 常见问题排查

### 7.1 CORS_ORIGINS 格式错误

**症状**：后端启动报 `SettingsError: error parsing value for field "cors_origins"`。

**原因**：pydantic-settings 要求 `list[str]` 类型从环境变量读取时必须是 JSON 数组格式。

**解决**：确保 `.env` 和 `docker-compose.yml` 中 `CORS_ORIGINS` 用 JSON 数组：
```json
CORS_ORIGINS=["http://localhost:3000"]
```

在 `docker-compose.yml` 中用引号包裹（避免 YAML 解析问题）：
```yaml
CORS_ORIGINS: '["http://localhost:3000"]'
```

### 7.2 Redis 启动失败

**症状**：redis 容器持续重启，日志报 `wrong number of arguments`。

**原因**：`REDIS_PASSWORD` 为空时，`--requirepass` 后面的空值被当成下一个参数。

**解决**：本项目已用 shell 条件判断修复（见 `docker-compose.yml` redis 服务的 `command`）。

### 7.3 pgvector extension 装错 schema

**症状**：注册用户时报 `type "vector" does not exist`。

**原因**：`CREATE EXTENSION vector` 没指定 schema，被装到了当前 search_path 的第一个 schema（可能是 tenant schema），导致全局不可见。

**解决**：本项目已在 `tenants/service.py` 和 `main.py` 中用 `CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public` 修复。

**如果已经装错**，手动修复：
```sql
DROP EXTENSION IF EXISTS vector CASCADE;
CREATE EXTENSION vector WITH SCHEMA public;
```

### 7.4 后端报 `ModuleNotFoundError: No module named 'omnibase'`

**症状**：容器内 uvicorn 启动失败。

**原因**：volume mount 覆盖了容器内 `/app`，但 Python 找不到 `src/omnibase` 包。

**解决**：确保 `docker-compose.yml` 中 backend 服务的 environment 包含：
```yaml
PYTHONPATH: /app/src
```

### 7.5 bcrypt / passlib 不兼容

**症状**：注册时报 `password cannot be longer than 72 bytes` 或 `(trapped) error reading bcrypt version`。

**原因**：passlib 1.7.4 与 bcrypt 4.x 不兼容（bcrypt 4 移除了 `__about__` 属性）。

**解决**：本项目已改用 `bcrypt` 库直接调用（绕过 passlib），见 `auth/security.py`。

### 7.6 前端 API 代理 502 / ECONNREFUSED

**症状**：前端访问 `/api/*` 返回 500 或 502。

**原因**：`next.config.js` 的 `rewrites()` 在容器内执行，`localhost:8000` 指向容器自己。

**解决**：`next.config.js` 中 rewrites 的 destination 必须用 docker compose 服务名：
```js
const apiBaseUrl = process.env.API_PROXY_URL || 'http://backend:8000'
```

`docker-compose.yml` 中 frontend 服务的 environment：
```yaml
API_PROXY_URL: http://backend:8000
```

### 7.7 search_path 在 commit 后失效

**症状**：注册/查询时报 `relation "users" does not exist`。

**原因**：SQLAlchemy 的 session 在 commit 后可能归还连接到池，`SET search_path` 不持久。

**解决**：本项目已用 SQLAlchemy `Pool.checkout` 事件钩子 + contextvars 统一修复（见 `core/db.py` + `tenants/context.py`）。所有 session 从连接池借出连接时自动根据当前 tenant contextvar 设置 search_path。

### 7.8 pnpm store 位置冲突

**症状**：在 frontend 容器内 `pnpm add` 报 `ERR_PNPM_UNEXPECTED_STORE`。

**原因**：volume mount 导致 host 和容器的 pnpm store 路径不一致。

**解决**：
```bash
docker compose exec frontend pnpm install --config.store-dir=/app/.pnpm-store
```

---

## 9. 前端开发 vs 生产镜像

### 9.1 多阶段 Dockerfile

`frontend/Dockerfile` 包含三个阶段：

| 阶段 | 用途 | 说明 |
|---|---|---|
| `dev` | 日常开发 | `next dev`，源码挂载，HMR 热更新 |
| `builder` | 构建产物 | `pnpm build` + standalone 输出 |
| `production` | 生产运行 | 非 root 用户，只含 standalone + static |

开发 Compose 显式指定 `target: dev`：
```yaml
frontend:
  build:
    target: dev
```

### 9.2 生产镜像构建与基准测试

```bash
# 构建独立生产镜像（不替换开发前端）
docker compose -f docker-compose.frontend-production.yml build

# 启动隔离基准容器（随机 loopback 端口）
FRONTEND_PROD_PORT=3001 docker compose -f docker-compose.frontend-production.yml up -d

# 验证
curl http://127.0.0.1:3001/healthz
# → {"status":"ok"}

# 停止并清理（不删除共享 volume）
docker compose -f docker-compose.frontend-production.yml down
```

生产镜像特性：
- 非 root 用户（`nextjs:1001`）
- 只读文件系统 + tmpfs
- `cap_drop: ALL` + `no-new-privileges`
- 无源码/bind mount/node_modules
- standalone Next.js server（约 87 KB 共享 JS）

### 9.3 Windows Docker Bind Mount 性能

Windows → Linux Docker bind mount（`./frontend:/app`）会显著增加：
- 文件变更通知延迟（FS watcher 跨 OS）
- 源码遍历时间（webpack/turbopack 扫描）
- source map 和 HMR 开销

**建议**：
- 日常开发使用 `dev` target（接受冷编译延迟）
- 性能测试务必使用 `production` target（无 bind mount）
- 如开发体验卡顿严重，考虑在 WSL2 内运行 Docker

### 9.4 前端 Liveness 路由

`/healthz` 是轻量前端存活检查，不依赖后端：
```typescript
// frontend/app/healthz/route.ts
export function GET() {
  return NextResponse.json({ status: 'ok' }, { headers: { 'Cache-Control': 'no-store' } })
}
```

注意：Next.js 开发模式可能需要重启容器才能发现新增的路由处理器文件。生产构建始终包含此路由。

### 9.5 Loopback 安全约束

所有服务端口绑定 `127.0.0.1`，不暴露到网络：
```yaml
ports:
  - "127.0.0.1:${FRONTEND_PORT:-3000}:3000"
  - "127.0.0.1:${BACKEND_PORT:-8000}:8000"
```

如需远程访问，必须通过反向代理（Nginx/Caddy）并配置 TLS。

### 8.1 Dockerfile 中的国内源

后端 Dockerfile 用 jsDelivr CDN 下载 uv，用清华源下载 PyPI 包。**换到境外服务器时**这些优化仍然可用（jsDelivr 和清华源都全球可达），但如果境外构建慢可以改回官方源。

### 8.2 mypy 非严格模式

Phase 0.5 将 mypy 从 `strict=true` 降级为宽松模式（`check_untyped_defs=true` 但不强制 `disallow_untyped_defs`）。Phase 1+ 会逐步收紧。

### 8.3 测试覆盖率

当前单元测试 90 个全绿，集成测试 7 个全绿。覆盖率未达标（未配置 CI 强制）。Phase 1 会增加 RAG 模块测试。

### 8.4 单租户退化

虽然架构支持多租户（schema-per-tenant），但 Phase 0 的 login 流程假设 1 用户 = 1 租户（遍历所有租户 schema 查找用户）。Phase 2 会增加多租户成员关系表。
