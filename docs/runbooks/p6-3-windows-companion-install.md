# P6.3 Windows Companion 安装与全新环境预检

状态：**工程预览；未签名；OCI 镜像尚未发布；不是 production-ready 安装器。**

## 安装位置

Companion 提供三个显式安装范围，但不会自行提升权限：

| 范围 | 安装位置 | 配置位置 | 权限 |
|---|---|---|---|
| `user` | `%LOCALAPPDATA%\Programs\OmniBase` | `%LOCALAPPDATA%\OmniBase\config\operator.env` | 普通当前用户 |
| `machine` | `%ProgramFiles%\OmniBase` | `%ProgramData%\OmniBase\config\operator.env` | 仅规划；执行安装时需要调用方自行以管理员身份启动 |
| `custom` | 用户给出的绝对本地路径 | 默认仍使用当前用户配置位置 | 不接受相对、根、UNC、网络、ADS 或 reparse 路径 |

查看位置和生成无副作用安装计划：

```powershell
OmniBase.Setup.exe locations
OmniBase.Setup.exe locations --json
OmniBase.Setup.exe plan-install --scope user
OmniBase.Setup.exe plan-install --scope machine --json
OmniBase.Setup.exe plan-install --scope custom --target 'D:\Applications\OmniBase'
```

上述命令只输出路径和权限要求，不创建目录、不写配置、不提升 UAC，也不修改 PATH、
注册表、快捷方式、服务或防火墙。`plan-install` 要求目标尚不存在。

## 安装流程

发行 ZIP 与 Companion EXE 应作为同一工程预览制品交付。先核对外部发布页提供的
SHA-256，再执行：

```powershell
OmniBase.Setup.exe verify .\omnibase-windows-x64-preview.zip
OmniBase.Setup.exe install .\omnibase-windows-x64-preview.zip `
  "$env:LOCALAPPDATA\Programs\OmniBase"
OmniBase.Setup.exe init-config --output `
  "$env:LOCALAPPDATA\OmniBase\config\operator.env"
OmniBase.Setup.exe doctor `
  --install "$env:LOCALAPPDATA\Programs\OmniBase" `
  --env-file "$env:LOCALAPPDATA\OmniBase\config\operator.env"
```

安装仍使用经过验证的 staging 目录与最终原子移动。目标已存在时拒绝覆盖；本阶段不提供
就地升级或卸载。`init-config` 使用操作系统 CSPRNG 且拒绝覆盖已有配置，不在控制台回显
生成的秘密。

`doctor` 是只读诊断。它可以查询 Docker CLI/daemon、Compose 和 WSL 状态，但不会安装、
启动、升级或重启这些组件，不会拉取镜像，也不会修改 VHDX。由于正式 OCI digest 尚未
发布，正常工程预览结论仍可能是：

```text
RELEASE_IMAGES_NOT_PUBLISHED
NOT_READY_FOR_PULL
```

## 全新 Windows VM 的 fail-fast 预检

仓库提供一个只读宿主预检：

```powershell
powershell -NoProfile -File `
  scripts\release\probe_p6_3_clean_windows_vm.ps1 `
  -VmName OmniBase-P63-Clean-Windows
```

预检只允许检查精确命名的专用 Hyper-V VM。它不会创建、启动、停止、重启、配置或删除
VM，不会创建/应用 checkpoint，不会 mount、resize、compact、move 或修改 VHDX，也不会
启用 Windows Optional Feature。

以下任一条件不明确时立即停止：

- Hyper-V 只读 cmdlet 不可用；
- 精确命名的专用 VM 不存在或不唯一；
- VM 未关闭、不是 Generation 2 或存在 checkpoint；
- 不是单一、脱机、常规 `.vhdx`；
- VHDX 路径包含 reparse、owner 无法验证或宽泛主体拥有写权限；
- VHDX 所在卷是网络卷或宿主剩余空间低于 20 GiB；
- guest 新鲜度、登录方式或安装证据仍未证明。

无论结果如何，预检都会输出：

```text
CLEAN_WINDOWS_VM_ACCEPTANCE_NOT_PROVEN
NO_VM_OR_VIRTUAL_DISK_MUTATION_PERFORMED
```

只有宿主只读预检通过后，才可另行安排一次 guest 内的 EXE/ZIP 安装验收。当前脚本本身不
进入 guest，因此不能把 `CLEAN_WINDOWS_VM_PREFLIGHT_READY` 解释成安装验收通过。

## 未证明边界

- Authenticode / publisher identity 未证明；
- backend/frontend 与第三方 OCI image digest 未发布；
- Companion 不负责安装 Docker Desktop、WSL 或 Hyper-V；
- 没有 public clean-machine 完整产品部署证据；
- Runtime、Planner、Multi-Agent 与 MCP Runtime 仍必须保持关闭；
- `production_ready=false` 保持不变。

## P6.3 安装路径冻结说明

独立安全复核确认：仅在多个阶段重复按路径检查 reparse 与目录 identity，仍不能闭合
最终 rename 前的 check-then-use 竞态。在具备稳定的 handle-relative 创建、写入和
rename 实现及相应攻击测试前，`install` 命令会在解析路径、打开归档或创建任何目录
之前稳定返回 `install_path_identity_binding_not_implemented`，不会创建 staging、
解压文件或移动目录。

`verify`、`help`、`locations`、`plan-install`、`init-config` 与只读 `doctor` 保持可用。
custom 路径和 elevated/machine-scope 的安装验收仍未证明；machine scope 仍只提供
规划，Companion 不请求 UAC，也不扩大文件系统或系统权限。本次 forward fix 没有
再次运行 clean-Windows VM probe。
