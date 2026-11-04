# 运行、部署与恢复

本指南描述当前私有单用户应用。命令从项目根目录执行；文件中的示例值不是凭据，不应直接用于真实连接。实际执行过的环境与结果见[测试记录](TEST_RESULTS.md)。未验证的平台步骤是运行指南，不代表已经在这些平台验收。

## 桌面交付目标

2026-11-04 用户明确以程序形式交付。主要交付方向改为桌面应用，先按当前 Windows 开发环境规划，其他系统的安装包需另行构建与验收。五阶段产品路线保留，桌面运行时与安装包工作归入第五阶段。后文 Compose、裸机和浏览器说明记录既有运行方式，继续用于开发、回归和诊断；公网部署不再是当前交付的必备目标。

目标体验：安装后从桌面或开始菜单打开 FinSight AI，在独立程序窗口完成公司搜索、财务阅读、来源追溯、模型设置和研究。程序负责本地服务的就绪检查、可见的启动错误和退出协调；用户正常使用时无需手动启动终端或开发服务器。已经保存的数据可在断网时阅读，在线同步与云模型仍需可用网络；Ollama 仍需已安装并可达的模型服务。

当前没有桌面宿主、内置后端运行时或安装包，现有 `start_local.ps1` 是开发恢复脚本。源码 ZIP 也不属于可双击运行的桌面发行包。桌面技术选择应先完成一个最小可运行验证，再锁定版本与打包工具：

- 复用 Vue 3 / TypeScript 构建产物和现有 FastAPI API，避免重写已验证的财务计算与证据逻辑。
- Electron 可沿用现有 JavaScript 工具链，但需承担其运行时体积与安全维护；Tauri 需要补充 Rust、Windows C++ 构建工具及 WebView2 构建/运行条件。当前项目没有安装这两套桌面依赖，未将任一方案标记为已实施。参见 [Electron 打包](https://www.electronjs.org/docs/latest/tutorial/tutorial-packaging)、[Electron 安全](https://www.electronjs.org/docs/latest/tutorial/security)和 [Tauri Windows 前置条件](https://tauri.app/start/prerequisites/)。
- PostgreSQL 的全文检索、事务和行锁，以及 Redis/RQ 的任务执行、共享限速与恢复是现有业务的一部分。桌面化须解决这些依赖的分发和生命周期；不能只替换连接字符串为 SQLite 或启用 inline 任务，就宣称完整桌面功能已经保留。当前 Windows 的真实队列路径仍使用 WSL/Linux，需要单独验证最终桌面运行方案。
- 正式发行的服务入口仅向本机开放，桌面宿主按最小权限限制导航、外链和原生调用；主密钥、API Key 与管理员凭据不得进入前端构建产物或公共安装资源。
- 数据、配置和脱敏日志写入用户数据目录，与安装目录分离。需要验证更新、失败回滚与卸载后数据保留策略，迁移现有真实数据库前先备份并在隔离副本演练。

桌面版的独立验收项：干净 Windows 环境安装、双击启动、无需开发服务器、真实财务与来源读取、实际异步同步、模型配置加密、重启后数据保留、重复启动与端口冲突、依赖故障/任务恢复、退出后进程清理、更新迁移及卸载。安装包签名与各系统验证按实际条件分别记录；既有 584 项后端、48 项前端与 14 项浏览器通过记录继续有效，但不能替代这些桌面验收。

## 环境选择

| 系统 | Compose 路径 | 不使用容器时 |
|---|---|---|
| Windows | Docker Desktop，启用 Linux 容器 | 使用 WSL2 Linux 运行 PostgreSQL、Redis、Python 3.12 API 与 RQ worker；前端可在 Windows 运行 |
| macOS | Docker Desktop | Python 3.12、PostgreSQL、Redis、Node 24、pnpm 11.25.0；在独立终端运行 API、worker 和前端 |
| Linux | Docker Engine 与 Compose plugin | 同上；长期进程交给部署者的进程管理工具 |

Docker Desktop 包含 Compose；Linux Engine 可安装 Compose plugin。安装方法见[Docker 官方说明](https://docs.docker.com/compose/install/)。本项目不依赖 Kubernetes 或云服务。

首次安装后检查 `docker version` 与 `docker compose version`。目前开发机的容器基础镜像下载受网络限制；重新尝试仍失败时记录原错误，不用一次配置校验代替容器运行验收。

## 配置与数据隔离

首次创建本地配置，已有 `.env` 时不要覆盖：

```powershell
# Windows PowerShell，首次运行
Copy-Item .env.example .env
```

```sh
# macOS / Linux，首次运行
cp .env.example .env
```

用本地编辑器填写配置，避免把秘密粘到聊天、命令参数或日志。`.env`、`.env.*`（示例除外）和 `work/` 已忽略提交。

| 配置 | 用途与约束 |
|---|---|
| `POSTGRES_PASSWORD` | Compose 数据库密码；建议独立随机 64 位十六进制值，避免直接拼入连接 URL 的保留字符 |
| `LOCAL_ADMIN_TOKEN` | 独立随机管理员令牌；应用至少要求 16 字符，建议使用独立的高熵随机值 |
| `DATA_MODE` | `sec_live` 正式在线资料；`sec_snapshot` 真实历史归档；`synthetic_fixture` 仅隔离测试 |
| `SEC_USER_AGENT` | 在线 SEC 请求使用应用名及部署者真实联系邮箱；历史归档读取不需要 |
| `DATABASE_URL` / `REDIS_URL` | 裸机服务连接；Compose 在容器环境中覆盖为内部服务地址 |
| `TASK_MODE` | 正常运行保持 `rq`；`inline` 和 SQLite 只允许显式开发测试 |
| `SNAPSHOT_DIRECTORY` | 裸机为当前系统可见的 `data/snapshots` 绝对路径；Compose 自动挂载 `/data/snapshots` |
| `MASTER_ENCRYPTION_KEY` | AI Key 加密主密钥；可缺省以演示无模型功能，启用云端配置前生成并安全备份 |
| `OLLAMA_BASE_URL` | 部署者控制的单一 Ollama origin；留空表示没有配置，不接受浏览器任意 URL |
| `CORS_ORIGINS` | 裸机允许的前端来源；非身份认证，远程私有部署需按实际 origin 配置 |

数据库第一次使用时绑定模式，之后不能通过修改 `DATA_MODE` 把同一库变成另一模式。在线、历史和测试分别使用独立数据库/Compose 项目。Compose 项目命名不同会隔离命名卷，但默认 HTTP 端口仍相同，因此默认配置下同一时间只启动一套前端/API。

## Compose：无 Key 的真实历史演示

将 `.env` 的 `DATA_MODE` 设为 `sec_snapshot`，填好数据库密码和管理员令牌：

```sh
docker compose -p finsight-snapshot up --build -d
docker compose -p finsight-snapshot ps
docker compose -p finsight-snapshot exec api python -m app.cli import-snapshot --directory /data/snapshots/0000320193
docker compose -p finsight-snapshot exec api python -m app.cli import-snapshot --directory /data/snapshots/0000789019
docker compose -p finsight-snapshot exec api python -m app.cli import-snapshot --directory /data/snapshots/0001045810
```

迁移由一次性 `migrate` 服务执行，API 等待迁移完成及 Redis 就绪，前端等待 API readiness。首次导入后浏览 [首页](http://127.0.0.1:8080)、[API 文档](http://127.0.0.1:8000/docs)。`GET /health` 展示数据库/Redis/模式，`GET /health/live` 仅检查进程，`GET /health/ready` 检查依赖；AI 不可用不影响财务 readiness。

导入已随仓库提供的三份真实正文：

```sh
docker compose -p finsight-snapshot exec api python -m app.cli import-document --manifest /data/documents/0000320193/0000320193-24-000123/manifest.json
docker compose -p finsight-snapshot exec api python -m app.cli import-document --manifest /data/documents/0000320193/0000320193-24-000081/manifest.json
docker compose -p finsight-snapshot exec api python -m app.cli import-document --manifest /data/documents/0001045810/0001045810-23-000017/manifest.json
```

这些文件来自第三方固定版本历史归档，原始字节及来源见[data说明](../data/README.md)和[正文说明](../data/documents/README.md)，不是实时 SEC 响应。导入检查哈希、所选公司/申报与模式；原文及解析版本相同则复用。没有真实 8-K/EX-99.1 正文样本，不能以事件元数据列表代替附件验收。

导入完成后可执行只读检查：`python scripts/smoke_http.py --base-url http://127.0.0.1:8080`。该脚本专门检查上述真实快照、Apple FY2024正文、PostgreSQL检索、代理深链接与私有读取鉴权，不提交同步或模型调用。CI的独立容器job也使用它；实际是否已在容器通过仍以测试记录为准。

停止时使用 `docker compose -p finsight-snapshot down`，命名卷保留。不要添加删除卷的选项来处理普通连接故障。模式切换应先停当前项目，并使用另一项目及其独立配置/卷。

## SEC 在线首次同步

使用全新的在线数据库或 Compose 项目，例如 `finsight-live`，将独立配置的 `DATA_MODE` 设为 `sec_live`。`SEC_USER_AGENT` 使用应用名称和你实际收信的邮箱，不能保留 `your-real-contact-email` 或样例域名。按 [SEC Fair Access](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) 声明请求身份、限制请求频率并只下载所需资料；项目已经共享限速、缓存、处理 403/429 冷却。

```sh
docker compose -p finsight-live up --build -d
docker compose -p finsight-live exec api python -m app.cli refresh-tickers
```

在设置页输入管理员令牌 → 搜索候选并确认 CIK → 公司页提交同步 → 观察任务成功后查看指标。财报/事件研究入口可另行准备所选正文。同步和正文准备需要 API、Redis、worker 同时运行；外部网络失败不会自动换成历史或合成资料。

本机尚缺真实 SEC 联系信息和直接在线验收条件。部署者完成实际请求、来源哈希和申报匹配核查前，保持该项为未验证。

## 裸机开发

先准备 Python 3.12、Node 24、pnpm 11.25.0、PostgreSQL 与 Redis。当前开发验证用 PostgreSQL 16，Compose 声明 PostgreSQL 17；这两种环境的实际执行范围见测试记录。Windows 的 RQ worker 应在 WSL/Linux 中运行，不能用 inline 模式冒充队列部署。

```sh
python3.12 -m venv .venv
# macOS / Linux
. .venv/bin/activate
python -m pip install -r backend/requirements.lock
python -m pip install --no-deps -e backend
```

Windows 直接运行 Python CLI 时用 `.venv\Scripts\Activate.ps1` 激活；WSL 中使用其独立 Linux 虚拟环境，不能复用 Windows `.venv`。为目标模式建立独立数据库，并在根 `.env` 填 `DATABASE_URL`、`REDIS_URL`、`SNAPSHOT_DIRECTORY` 等。密码若含 URL 保留字符，连接 URL 中须进行百分号编码，不能直接复制原密码拼接。

```sh
python scripts/manage.py migrate
# 仅 sec_snapshot：导入三份 JSON 归档
python scripts/manage.py import-snapshots
# 仅 sec_snapshot：按需导入上述正文，路径使用仓库相对路径
python scripts/manage.py import-document --manifest data/documents/0000320193/0000320193-24-000123/manifest.json
python scripts/manage.py serve
```

另开同一虚拟环境终端运行 `python scripts/manage.py worker`。前端终端：

```sh
cd frontend
pnpm install --frozen-lockfile
pnpm dev
```

访问 [开发首页](http://127.0.0.1:5173)。Vite 默认代理 `http://127.0.0.1:8000`。Windows 前端连接 WSL API 时，WSL 私有地址可能变化，需要设置 `API_PROXY_TARGET` 并让 API 绑定对 Windows 可达的地址；这是虚拟机预览用途，不是公网开放指令。

当前已有 Windows/WSL 开发环境可从根目录恢复：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start_local.ps1
```

脚本默认 Ubuntu-24.04、当前开发使用的 WSL root 用户和 `/var/tmp/finsight-venv/bin/python`；可通过 `-Distribution`、`-WslUser`、`-PythonPath` 覆盖。它不会安装环境、迁移/导入数据或创建系统开机自启。它复用已有进程、隐藏启动缺失的 API/worker/前端、探测 WSL IP 并检查代理；日志在 `work/local-*.log`。已有前端代理指向旧 IP 时会保留进程并报告失败，先关闭原开发前端再重跑。

## AI 配置与 Ollama 网络

无 Key 时可以演示财务、申报和已经导入正文的证据检索。启用云 Key 前，在已安装后端依赖的 Python 环境执行 `python scripts/init_ai_key.py`；它将主密钥写入本地 `.env`，不打印密钥，已有有效主密钥保持原值。Compose 仅做无模型演示不需要主密钥；启用 AI 前补齐该密钥并重新创建 API/worker，使二者读取同一值。

设置页流程为保存配置 → 自动列举或手填模型 ID → 小型实际文本测试 → 启用/选择已验证配置。Key 直接填入设置页，不发到聊天中。改变 Key、模型、参数或部署地址后重新测试。真实连接测试可能计费，列表可见不证明账户能调用。

| 后端位置 | `OLLAMA_BASE_URL` 示例 | 要点 |
|---|---|---|
| 与 Ollama 同系统裸机 | `http://127.0.0.1:11434` | 必须从后端进程所在系统可达；Windows 与 WSL 不一定等同 |
| Compose 后端，Ollama 在宿主 | `http://host.docker.internal:11434` | 已配置 host-gateway；宿主监听/防火墙需要允许容器到受控服务的连接 |
| 远程后端 | 部署者控制且后端可达的 origin | 不能访问浏览器用户电脑的 localhost；当前无客户端代理/隧道功能 |

Ollama 默认绑定 loopback，监听设置见[官方 FAQ](https://docs.ollama.com/faq)。不要为了连接容器把模型服务无保护地暴露到公网；按实际网络限制访问范围。FinSight 不自动安装 Ollama、下载模型或估算未知模型价格；模型 ID 以实际服务返回为准。

允许地址必须由部署者控制，不能把不可信域名当作 SSRF 安全策略。请求禁止重定向与环境代理，当前未实现 DNS 固定。除本机 loopback 开发访问外，管理员令牌和 Key 必须通过 HTTPS 私有入口传输；当前不存在公开多用户部署验收。

## 备份、更新与恢复演练

业务状态、原始字节和报告在 PostgreSQL；Redis 还包含未消费的队列任务。单独备份数据库不保证还原未执行队列。数据库和密钥备份应加密、限制访问，并与本地副本分开存放；主密钥丢失后不能从密文恢复云 Key。

下面以快照 Compose 项目为例，先在项目 `work/backups` 建目录（PowerShell 用 `New-Item -ItemType Directory -Force work/backups`，sh 用 `mkdir -p work/backups`），再导出：

```sh
docker compose -p finsight-snapshot exec -T postgres pg_dump -U finsight -d finsight -Fc -f /tmp/finsight.dump
docker compose -p finsight-snapshot cp postgres:/tmp/finsight.dump work/backups/finsight.dump
```

这里使用 [pg_dump 的 custom 归档](https://www.postgresql.org/docs/17/app-pgdump.html)和 [Compose 文件复制](https://docs.docker.com/reference/cli/docker/compose/cp/)，避免旧版 Windows PowerShell 的文本 `>` 重定向二进制 dump。另行安全备份 `.env` 中稳定的主密钥及配置；不要把它们混入源码压缩包。保留 `data/` 归档、manifest 和对应源码/迁移版本。[Docker 命名卷](https://docs.docker.com/engine/storage/volumes/)不会随普通容器删除自动消失，但持久卷本身不是离机备份。

恢复应先在新的隔离项目验证，不能直接覆盖原库。保留原项目数据并停止占端口的 API/前端；准备与备份匹配的数据模式/主密钥配置：

```sh
docker compose -p finsight-restore up -d postgres redis
docker compose -p finsight-restore cp work/backups/finsight.dump postgres:/tmp/finsight.dump
docker compose -p finsight-restore exec -T postgres pg_restore -U finsight -d finsight --no-owner --no-privileges --single-transaction /tmp/finsight.dump
docker compose -p finsight-restore up --build -d
```

目标必须是新的空数据库/卷，已经有同名项目时不要执行此示例。恢复参数含义见 [pg_restore 官方说明](https://www.postgresql.org/docs/17/app-pgrestore.html)。检查 readiness、公司数、申报/正文哈希、私有历史及解密状态，再决定是否使用恢复环境。部署绑定变化会使模型验证失效，需重测。不要盲目重放曾可能调用过付费模型的任务；Redis 遗失后残留的数据库任务应按取消/超期/重试流程处理。

应用更新前先备份并保留旧源码版本，停止 API/worker 后构建新镜像，执行 `docker compose -p finsight-snapshot run --rm migrate`，再 `up -d`；迁移失败就停止更新并检查，不用自动建表代替迁移。降级不保证数据库 schema 向后兼容，应在隔离恢复环境验证。以上备份/恢复命令是操作说明，是否已做实际恢复演练以测试记录为准。

## 排错

| 现象 | 检查与处理 |
|---|---|
| “暂时无法加载公司 / 无法连接数据服务” | 检查 API 与前端端口、`/health`、WSL IP 和 Vite 代理；当前环境可运行恢复脚本，刷新页面 |
| Compose 构建超时/基础镜像不可达 | 检查 Docker daemon 的实际出网、镜像源与证书；保留原错误，不误标容器已验收 |
| readiness 503 | 检查 PostgreSQL/Redis、连接 URL、迁移及数据模式；不要重建数据卷处理普通连接问题 |
| queued 一直不变 | 检查 Linux worker 日志及 Redis；研究任务相同提交超过恢复期限可标为超时，取消/重试前确认原调用状态 |
| SEC 403/429/联系信息错误 | 检查真实 User-Agent、网络及冷却；不要反复刷新绕过限速 |
| 管理操作 401 / ADMIN_NOT_CONFIGURED | 在设置页重新输入管理员令牌；刷新会清空内存令牌；服务端令牌至少 16 字符 |
| DATA_MODE 冲突 | 连接回原模式数据库，或另建独立库/项目；不要删除绑定以混入测试资料 |
| 没有正文/检索无命中 | 选择已有正文的申报或合法准备正文；检查检索范围与关键词，不推断原报告不存在该内容 |
| Key 无法解密/配置变为未验证 | 检查 API/worker 使用同一原主密钥和部署 origin，必要时重新保存 Key 并真实测试 |
| Ollama 不可达 | 从后端所在系统检查受控 origin；区分 Windows、WSL、容器、远程机器的 localhost |

排错日志可能含用户的研究问题和运营信息，分享前审查；不得公开 `.env`、管理员令牌、云 Key 或数据库备份。

## A 股运行配置

A股功能随同一API/worker部署，无需新增服务。升级先执行迁移到`f51a01`并重启API/worker；新建`cn_companies/cn_source_snapshots/cn_sync_jobs/cn_dataset_metadata`，不修改SEC公司的CIK。

| 变量 | 默认与作用 |
|---|---|
| `CN_DATA_MODE` | `cn_live`在线获取；`cn_snapshot`只读可信真实归档；`synthetic_fixture`仅隔离测试 |
| `CN_TIMEOUT_SECONDS` | 20秒，允许5–60，单次上游请求超时 |
| `CN_MAX_BYTES` | 10,000,000，允许1,000,000–30,000,000，每个解压响应大小上限 |

Compose的共享backend environment已转发这三项至迁移/API/worker；裸机管理脚本读取根`.env`。CN模式与CN表独立绑定，可与SEC历史归档表共用数据库；不能修改已有CN模式绑定来混入测试或归档。SEC测试模式下CN强制synthetic_fixture并禁用在线请求。A股在线链路目前无需SEC联系邮箱或模型Key，仍需可达网络和Redis/worker。

现有本机环境可先`python scripts/manage.py migrate`，重启后在首页选择A股并授权更新目录；或执行`cn-refresh-directory`和`cn-sync --symbol 600519.SH`。本机恢复脚本不自动迁移，因此新增迁移必须先完成。可信离线归档、来源许可边界及其他操作见[A股说明](A_SHARE_SUPPORT.md)。真实在线、本地回归、Compose配置和完整容器启动验收分别记录，不能互相替代。

PDF/研究扩展另需升级锁定依赖`pypdf==6.21.4`、迁移至`h52a02`并重启API/worker。`.env.example`与Compose已提供PDF字节、页数、下载/解析时限、文本与分块上限；具体默认值、文本型限制与用户流程见[A股正文研究](A_SHARE_RESEARCH.md)。真实PDF与数据库备份属于本地运行资料，不随源码分发。
