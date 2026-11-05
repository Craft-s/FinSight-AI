# FinSight AI Windows 桌面版

桌面版使用 WinForms 和 WebView2 显示 FinSight AI。应用打开后会启动独立的 PostgreSQL、Redis、FastAPI 和 RQ worker，完成就绪检查后进入工作台。首次运行导入随包提供的真实 SEC 历史归档，A 股仍使用在线同步。

## 首次初始化

1. 使用 Windows x64，安装 [WebView2 Evergreen Runtime](https://developer.microsoft.com/microsoft-edge/webview2/) 和 [.NET 8 Desktop Runtime x64](https://dotnet.microsoft.com/download/dotnet/8.0)。已安装相应运行时可以跳过。
2. 启用 WSL2 并安装 Ubuntu 24.04。在 PowerShell 执行 `wsl --install -d Ubuntu-24.04`，按系统提示重启或完成 Ubuntu 初始设置。用 `wsl --list --verbose` 确认发行版使用 WSL 2。
3. 如果 Ubuntu 缺少 Linux 软件包，在 Ubuntu 内执行以下命令。

   ```sh
   sudo apt update
   sudo apt install python3-venv postgresql-16 redis-server
   ```

4. 解压完整的 Windows 发布包到本机目录，双击 `Setup-FinSight.cmd`。初始化使用 Ubuntu 中的专用非 root 用户 `finsight-desktop`，安装项目锁定的 Python 依赖。首次安装依赖需要网络。
5. 双击 `FinSight.Desktop.exe`。首次启动要初始化数据库并导入资料，请等待窗口进入工作台。搜索 `AAPL` 可以浏览真实历史财务、财报列表和 Apple 10-K 原文证据。以后打开程序无需再运行初始化脚本。

## 使用与数据

- 桌面会话由宿主管理授权，设置页直接显示授权状态。模型 Key 仍在设置页填写。真实云模型可用性由该服务决定。
- 桌面资料与已有开发数据库分开。数据库、队列、加密配置和启动日志保存在 Ubuntu 专用账户的 `~/.local/share/finsight-ai/desktop`；Windows 的 WebView2 配置保存在当前用户目录。
- 窗口与 WSL 通过本次运行的标准流通信，后端、数据库和队列通过 Linux 私有 Unix 套接字访问。财报外链在系统浏览器打开。
- 关闭窗口会停止本次启动的桌面服务并保留资料。仍在运行的任务可能被中断。更新前先关闭程序并备份 Linux 数据目录，再解压新版到单独目录并重新运行 `Setup-FinSight.cmd`。按私密资料保管备份中的配置文件。
- 初始化失败时根据 `Setup-FinSight.cmd` 的提示检查 WSL 发行版、Linux 软件包和依赖下载。启动失败可在窗口查看提示，并检查专用数据目录中的 `runtime.log`。

## 从源码构建

在有 Git、.NET 8 SDK、Node 和 pnpm 的 Windows x64 环境中，从仓库根目录执行。

```powershell
pnpm --dir frontend install --frozen-lockfile
powershell.exe -NoProfile -ExecutionPolicy Bypass -File desktop/build-windows.ps1
```

构建脚本会生成前端生产资源、发布 Windows 宿主，并把可运行目录放在 `outputs/FinSight-AI-Windows-x64-*`。发布目录包含程序、初始化脚本及运行所需文件，需作为完整目录保存。当前交付为文件夹式桌面包，独立安装器、签名和自动更新留待后续。
