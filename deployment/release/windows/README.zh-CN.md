# OmniBase v1.0 Windows 发布预备包

这是 P6.1-D 的预备发布边界，不是已经签名并正式发布的 v1.0。当前 ZIP 的可复现构建与完整性校验已有工程证据；发布者签名、Authenticode、真实镜像 digest、SBOM 和最终 Windows 安装验收仍为 `NOT_PROVEN`。

使用原则：

1. 将 `operator.env.template` 复制到源码与安装目录之外，再填写密钥和不可变镜像 digest。
2. 在联网拉取镜像前先离线运行：
   从解压目录运行 `python scripts/release/validate_windows_release_config.py --compose deployment/release/windows/compose.yml --env-file <精确文件>`。
3. 预检通过后，只运行 `docker compose --env-file <精确文件> -f deployment/release/windows/compose.yml pull --quiet` 与 `up -d --no-build`；不得省略 `--env-file`。
4. Compose 复用个人生产目标的 PostgreSQL、Redis、MinIO 初始化、Alembic migration、健康检查、只读文件系统、最小 capability 与 fail-closed Feature Gate 生命周期，但全部应用镜像只能来自预检白名单并绑定 `sha256` digest；包内没有 `build:`。
5. Runtime、Planner、Multi-Agent、Agent Alpha engineering 与 MCP Runtime 默认全部关闭。
6. 安装器不得自动清理、压缩、迁移、截断或删除 Docker/WSL VHDX。
7. 包内不含 Docker image tar、数据库、模型、`.env`、密钥、`node_modules` 或 `.next`。

离线预检只验证 Compose 中声明的每一个镜像变量都精确匹配内置仓库白名单与 64 位小写 SHA-256；它不会联网、拉取镜像、启动容器、读取根 `.env` 或证明镜像发布者身份。正式发布仍须在受控流水线中加入镜像来源证明、SBOM、发布者签名与 Authenticode。
