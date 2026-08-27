# OmniBase Windows 工程预览 Companion

这是 P6.2-D 的个人版工程预览，不是已签名并正式发布的 v1.0。Companion 设计为
Windows x64 self-contained 单文件程序，目标是不要求用户预装 .NET Runtime。P6.3-D
在原有四个执行命令之外增加了安装帮助和无副作用位置规划：

```powershell
OmniBase.Setup.exe help
OmniBase.Setup.exe locations [--json]
OmniBase.Setup.exe plan-install --scope user|machine|custom [--target <绝对本地路径>] [--json]
OmniBase.Setup.exe verify <release.zip>
OmniBase.Setup.exe install <release.zip> <全新绝对本地目标目录>
OmniBase.Setup.exe init-config --output <安装目录外的 operator.env>
OmniBase.Setup.exe doctor --install <安装目录> [--env-file <operator.env>] [--json]
```

默认位置遵循常见 Windows IDE/工作台约定：

```text
user.install  = %LOCALAPPDATA%\Programs\OmniBase
user.config   = %LOCALAPPDATA%\OmniBase\config\operator.env
machine.install = %ProgramFiles%\OmniBase
machine.config  = %ProgramData%\OmniBase\config\operator.env
```

`machine` 只提供规划，不自动发起 UAC 或提权。`custom` 必须是全限定本地路径。安装目标
拒绝盘符根、UNC/网络位置、ADS、reparse 路径、尾随点/空格组件和任何已经存在的目标。
Companion 不写 PATH、注册表、快捷方式、服务或防火墙。

`verify` 和 `install` 继续执行发行文件闭集、manifest、长度和 SHA-256 校验。安装采用
staging + 最终原子目录移动，目标已存在时拒绝覆盖。

`init-config` 使用操作系统 CSPRNG 生成数据库、Redis、MinIO、JWT 和两个相互独立的
32-byte base64url 加密密钥；命令不会在输出中回显秘密，也不会覆盖既有文件。镜像 digest
属于发布者供应链元数据，因此仍保留占位符，不会用 mutable tag 或随意查询的 digest 代填。

`doctor` 默认离线、只读，分层检查 `RELEASE_INTEGRITY`、`HOST`、`CONFIG`、
`IMAGE_METADATA` 和 Feature Gates。它只查询 Docker CLI/daemon、Compose、WSL2 和磁盘；
不会安装、启动或升级 Docker/WSL，不会 pull/up 容器，不会修改 VHDX、PATH、防火墙或系统
服务。当前六个镜像 digest 未发布时，稳定结论是：

```text
RELEASE_IMAGES_NOT_PUBLISHED
NOT_READY_FOR_PULL
```

这属于发布方阻塞，不是普通用户配置错误。即使未来输出 `READY_FOR_PULL`，它也只表示可以在
人工确认后进入 exact-digest `docker compose pull`，不表示 production-ready、Publisher
verified、已启动或已健康。

当前必须按实报告：

- `production_ready=false`；
- Authenticode 未签名，Publisher signature 未证明；
- OCI backend/frontend 和第三方镜像真实 digest 未发布；
- Runtime、Planner、Multi-Agent 与 MCP Runtime 默认全部关闭；
- 包内不含 Docker image tar、数据库、模型、`.env`、密钥、`node_modules`、`.next` 或 VHDX；
- 当前机器没有 .NET SDK 时，只能验证源码和安全契约，不能声称 self-contained EXE 已构建。

完整的安装说明和只读 clean-Windows VM fail-fast 预检见
`docs/runbooks/p6-3-windows-companion-install.md`。预检不创建、启动或修改 VM/VHDX；任何
Hyper-V cmdlet、专用 VM、owner/ACL、磁盘或 guest 新鲜度事实不明确时，必须停止并输出
`CLEAN_WINDOWS_VM_ACCEPTANCE_NOT_PROVEN` 与
`NO_VM_OR_VIRTUAL_DISK_MUTATION_PERFORMED`。
