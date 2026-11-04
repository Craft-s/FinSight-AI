# 实际验证记录

日期：2026-10-28（Asia/Shanghai）。本文件只记录实际执行结果，不把设计、契约测试和在线验证混为一谈。

## 阶段一：功能闭环通过，容器镜像构建受网络阻断

| 验证 | 命令/方式 | 实际结果 |
|---|---|---|
| 后端领域、API、迁移与队列测试 | WSL Python 3.12，专用 `finsight_test` PostgreSQL 库、Redis DB15，`pytest -q` | **91 passed**；包含真正 Linux RQ fork worker；1 条 Starlette TestClient 弃用警告 |
| 后端风格 | `ruff check .` | All checks passed |
| 依赖一致性 | WSL `pip check` | No broken requirements found；redis-py 6.4.0 已锁定 |
| 前端风格 | `pnpm lint` | 成功退出 |
| 前端单元测试 | `pnpm test` | **5 passed** |
| TypeScript 与生产构建 | `pnpm build`（vue-tsc + Vite） | 通过，2248 个模块，最后构建 7.31 秒；未据此宣称加载性能 |
| 数据库迁移 | `python scripts/manage.py migrate` | 真 PostgreSQL 成功执行 `2fdede418ca2`、`73b621` |
| 真实数据导入 | `python scripts/manage.py import-snapshots` | 三家公司真实归档响应导入成功，未使用合成金额 |
| 健康检查 | 经 Vue proxy 请求 `/api/v1/health` | database=ok、redis=ok、task_mode=rq、source_kind=sec_snapshot |
| 浏览器操作 | 本地 Vue → FastAPI → PostgreSQL/RQ | 搜索、公司页、年度指标/图表、来源、财报、8-K、授权后同步排队到完成均已检查 |
| 移动端 | 浏览器视口 390×844 | 根文档宽375、视口390，无页面横向溢出；截图保存 |
| 浏览器控制台 | 完成同步后读取 error 日志 | 空列表 |
| Compose 配置 | WSL Docker29.1.3 / Compose2.40.3，`docker compose config --quiet` | 通过 |
| Compose 构建 | `docker compose build` | **未通过**：Docker Hub Python 基础镜像连接被拒绝；Google 公共缓存尝试也超时。未完成容器启动验收 |
| 当前直接 SEC 同步 | 未提供真实联系邮箱，执行环境网络受限 | **未验证**；API/worker保留明确配置错误，不冒充成功 |
| GitHub Actions | 已提供 workflow，尚未推送运行 | **未验证云端运行** |

### 实际运行的三家公司数据

以下是经运行中的前端代理读取的真实历史快照结果，数据来自第三方固定版本 SEC 录制归档，非当前 SEC 直连、非实时行情。

| CIK | 公司 | 最近5年指标数 | 非空数 | 10-K/10-Q及修订 | 8-K及修订 |
|---|---|---:|---:|---:|---:|
| 0000320193 | AAPL | 50 | 50 | 44 | 107 |
| 0000789019 | MSFT | 50 | 50 | 25 | 66 |
| 0001045810 | NVDA | 50 | 50 | 25 | 61 |

每项指标为后端精确十进制字符串，保留原始概念、期间、accession、披露日期、快照哈希和归档来源。`submissions` 与 `companyfacts` 的采集日期不同，UI 将公司元数据快照时间和指标来源日期分别展示。原始文件与 manifest 见 `data/snapshots/`。

### 已修复的问题与环境处理

- WindowsApps Python 别名不可用，改用内置 Python 3.12；虚拟环境缺 ensurepip，通过已有 pip 安装依赖。
- Windows 沙箱阻断 asyncio 回环测试，API/数据库/队列验收迁移到现有 WSL；不是把失败测试写为通过。
- WSL 中安装真实 PostgreSQL16.15、Redis7.0.15，以及 Docker Engine/Compose。Windows→WSL 127.0.0.1 转发不可达；本地 Vue proxy 使用 WSL 私有地址连接 API。
- Windows共享盘的Linux虚拟环境安装很慢，运行测试使用 `/tmp/finsight-venv`。此临时路径不是交付必需，README给出普通可重建的环境步骤。
- 修复前端不存在的 `ArrowUpRight` 图标导入，改用实际可用图标；Nginx目标修正为Compose服务 `api`。
- 真实快照缺少当前网络验证，UI明确“历史归档”，重新导入显示“重新载入快照”，不宣称变成最新数据。

阶段一的真实服务闭环已完成。Docker联网构建、直接SEC在线调用继续列为部署验收缺口；这些缺口不由测试夹具替代。后续进入阶段二的季度与比率实现，继续保留以上限制。

## 阶段二：多期间、计算与版本

- 最终真实PostgreSQL/Redis/Linux RQ全套：**168 passed，21.44秒**；ruff通过，pip check无依赖冲突。仍有一条Starlette TestClient弃用提示。
- 财务相关93项测试包含实际财年、53周、直接单季、YTD差分、FY−9M、EPS不可加、时点值、重述、分母与币种/口径保护；三个目标公司完整财年季度收入之和与年度收入精确相等。
- 迁移`a28c52`已在运行中的专用快照数据库升级；年度/季度相同期末不会碰撞，历史版本查询不覆盖最新物化指标。
- 实际API最近5年：三家公司年度各70项（十项基础+四项比率/年）；季度各280项、20个实际季度。季度各275项非空，Q4无直接EPS证据时保留N/A；NVDA年度比率部分因披露口径不一致为N/A，未填造值。
- 浏览器实测：年度/季度切换、财季选择、四比率、同比、指定Apple FY2023 accession、截止2022-12-31仅返回当时已披露年度；最新口径可以恢复。Q4 EPS表格为N/A。
- 第二阶段390×844视口：根文档宽375，未横向溢出；截图`09-quarterly.jpg`、`10-as-filed.jpg`、`11-as-of.jpg`、`12-quarterly-mobile.jpg`已保存。
- 最终前端lint无警告、**6项测试通过**；`pnpm build`包含vue-tsc类型检查并成功生成生产资源，2248模块，最后构建8.00秒。

边界：Companyfacts不是完整XBRL维度/会计政策实例。跨申报YTD差分只接受相同concept、unit、财年起点、原始非修订表单、上界披露时已知下界，且所有可用历史无冲突重述；同accession优先。有矛盾或证据不足返回N/A。指定申报视图不会借用另一份申报金额补齐季度。as_of限制现存归档内的披露日期，不宣称具备完整市场历史点时数据库。

用户要求第二阶段结束后暂停，第三阶段未开始。

## 2026-10-29：中英文UI与精确千位分隔

- 实际执行`pnpm lint`无警告，`pnpm test` **14 passed**，`pnpm build`（含vue-tsc）成功，2253模块，最后构建6.76秒。
- 新增精确字符串分组测试：整数、负数、加号、零、长小数、尾随零、超出JavaScript安全整数范围的值、无效输入；不经Number转换。
- 语言测试覆盖指标/来源/单位翻译、英文B/M缩写、回切中文及带HTTP状态的动态提示。
- 浏览器实测`416161000000`显示为`416,161,000,000`，值列computed text-align=right；来源顶部同样分组。中文回切保留相同精确值。
- 刷新页面后仍为en-US，HTML lang与页面标题同步；首页、公司页、设置与研究记录均已检查。已知后端AppError固定提示的翻译扫描无遗漏。
- 英文日期控件显示英文星期；校验错误在中文/英文切换后即时更新。浏览器error日志为空。
- 英文390×844视口，根文档宽375，无页面横向溢出；语言按钮及底部导航均可见。
- 截图`13-english-company.jpg`到`17-english-mobile.jpg`保存于docs/screenshots。已有WSL服务已恢复；开发虚拟环境放在`/var/tmp/finsight-venv`以避免WSL清理/tmp导致失效。
- 本轮只修改前端与文档，后端168项测试为第二阶段既有记录，本轮未重复运行。第三阶段继续暂停。

## 2026-10-29：设置入口与可扩展语言选择

- 导航、面包屑和页面标题统一为“设置 / Settings”；语言选择移至“设置 → 界面语言”，点击下拉框展开简体中文和English选项。
- 语言列表、翻译表、页面标题、金额展示方式和Element Plus语言配置从同一份`LANGUAGE_DEFINITIONS`读取；测试覆盖语言注册一致性、无效及原型属性语言值拒绝。
- 实际执行`pnpm lint`通过，`pnpm test` **16 passed**，`pnpm build`含vue-tsc成功，2253模块，构建6.56秒。
- 浏览器实测下拉展开、选择中文、刷新保持、切换英文；访问旧`/settings/ai`自动跳转`/settings`。页面及控件语言同步，页头独立语言按钮已移除。
- 英文390×844视口根文档宽375，未横向溢出；移动导航显示Settings，语言下拉选项可展开选择。测试后已重置视口。
- 设置页浏览器error日志为空；实际运行截图`18-settings-language.jpg`已保存。
- 本轮未改动后端或数据，未重复后端测试；第三阶段继续暂停。

## 2026-10-29：第三阶段模型配置与结构化概览

用户已要求恢复第三阶段，并确认暂时没有云端API Key或可用Ollama。以下结果区分真实基础设施测试、合成协议响应与未验证在线推理。

| 验证 | 实际执行 | 结果 |
|---|---|---|
| 后端完整测试 | WSL Python3.12；专用`finsight_test` PostgreSQL、Redis DB15、真实Linux RQ worker | **331 passed，75.43秒**；一条既有Starlette TestClient弃用警告 |
| 四家Provider协议 | 官方OpenAI/Google SDK及httpx MockTransport；DeepSeek/Ollama独立协议 | 覆盖成功、实际小调用接口、模型列表/分页、错误映射、参数转换、拒答、截断、无效JSON、跨域/重定向/代理限制；响应均为测试夹具 |
| 配置与安全 | 上述全套中的数据库/API测试 | Fernet、owner/provider绑定、主密钥缺失与变更、乐观版本、测试CAS、单默认配置、缓存与URL限制均通过 |
| 概览真实队列 | `test_overview_rq.py`：真实PG/Redis/fork worker调用独立的**测试专用HTTP服务器** | 配置→文本调用协议→激活→排队→冻结事实→生成→引用/数值校验→持久化通过；只传job_id，重复任务复用；这不是实际Ollama模型推理 |
| 并发与证据边界 | PG NOWAIT竞争锁、部署binding缓存、无效引用/值/单位/JSON/中英文数字词/Unicode数值 | 通过；配置锁持有至结果发布提交，语义支持明确未自动验证 |
| 后端风格/依赖 | `ruff check backend scripts/init_ai_key.py`、WSL `pip check` | All checks passed；No broken requirements found |
| 生产快照库迁移 | `python scripts/manage.py migrate` | 真PG从`a28c52`升级`b31c93`、`c31d94`成功 |
| 前端 | `pnpm lint`、`pnpm test`、`pnpm build`含vue-tsc | **25 passed**，lint通过；2261模块，最终构建7.88秒 |
| 无Key浏览器联调 | 运行中Vue→FastAPI→PG；中英设置、会话授权、四Provider、云/本地模式、无已验证模型、基础财务 | 已检查，无Key时不生成假分析；Apple真实历史归档仍正常显示精确数值 |
| 移动端 | 390×844设置及AI概览页面 | 文档宽375，无页面横向溢出；已重置视口 |
| 最终服务预览 | 重启最终API后重新打开设置页，读取浏览器error日志 | 服务已连接，error为空；截图`22-model-settings-preview.jpg`已保存 |
| 部署配置 | `docker compose config --quiet` | 通过；本轮未完成Docker联网构建或容器启动验收 |
| 四家真实模型服务 | 用户确认暂无可用云Key/Ollama | **未验证**；不能把测试HTTP服务器或Fake响应当成在线通过 |

首次新增依赖下载成功但WSL虚拟环境目录属主不同导致安装失败，改用创建环境的用户完成安装并通过pip check。首次全套313项通过、4项因测试替身缺少validate_config而setup失败；补齐测试契约后全套324项通过，独立复查继续修正配置锁、部署绑定和文本数值边界，最终为上述331项通过，未隐藏失败。

主密钥初始化脚本已在本地运行，已有有效Key不会被轮换且不会输出明文。测试还覆盖无效Key不覆盖、现有配置保留。生产数据库未导入测试替身数据，生产适配器没有Fake模式。

截图`19-model-settings.jpg`、`20-ai-overview-no-key.jpg`、`21-ai-overview-mobile.jpg`来自真实运行页面。因为没有真实模型服务，没有制作或宣称真实AI生成结果截图。第四阶段未开始；SEC直连和容器镜像网络缺口沿用前述记录。

## 2026-10-29：财务卡片间距微调

仅将`.ratio-grid`的上外边距由0调整为16px，保留其余布局。浏览器桌面实测两组间距16px；390×844移动视口实测约16px、文档宽375，无页面横向溢出，随后恢复视口。`pnpm build`含vue-tsc通过，2261模块，7.65秒。截图`23-metric-spacing.jpg`已保存。此低影响CSS调整未重复后端及单元测试，前述331/25项为第三阶段既有记录。

## 2026-11-03：第四阶段正文、检索与研究

| 验证 | 实际执行与结果 |
|---|---|
| 后端最终全套 | WSL Python3.12、专用PostgreSQL `finsight_test`、Redis DB15、真实Linux fork worker：**437 passed，88.69秒**；一条既有Starlette TestClient弃用警告 |
| 风格与依赖 | `ruff check backend scripts/manage.py`通过；WSL `pip check`无依赖冲突 |
| 迁移 | 实际快照PG库由`c31d94`升级`d41a01`、`e41a02`成功，测试还执行空库升级与回滚 |
| 文档安全与解析 | 同accession URL、禁重定向/代理、类型/大小、受控EX-99.1、表格/章节/隐藏节点、原字节、版本/幂等/模式隔离、附件失败重试均覆盖 |
| 真PG FTS | 三份真实归档分别1241/584/1928段；中文“收入/风险/现金流”经术语扩展每份返回8个真实片段，模式为postgres_fts |
| 研究协议闭环 | 四种类型、引用/精确值/摘录/拒答/未知、冻结版本、缓存、取消后不发布、重试、历史/权限与Markdown通过；真实RQ研究测试调用**测试专用HTTP协议服务器**，不是实际Ollama推理 |
| 并发复查 | 文档版本清单按实际引用的immutable document构造，覆盖检索前后导入新版本的窗口；配置版本/部署binding在发布锁中再次核对 |
| worker空闲恢复 | 专用fresh连接池及出队Timeout后恢复；4项真实Redis/Linux测试包含空闲多个短周期后再入队并真实fork消费；生产缓存正文任务随后实际成功 |
| 前端 | **34 passed**；lint无警告，vue-tsc与Vite构建通过，2269模块，31.38秒 |
| 浏览器 | 实际Apple FY2024年报中文检索、章节/原文/定位/sha展示、授权后的空研究历史、无已验证模型禁用生成、正文RQ完成、英文界面均已检查；最终console error为空 |
| 手机 | 390×844真实证据页文档宽375，无横向溢出；测试后已重置视口 |
| Compose | `docker compose config --quiet`通过；本轮没有冒充Docker镜像联网构建或公网部署通过 |
| 尚未验证 | 缺云Key/Ollama与SEC真实联系信息：四家真实推理、实际AI报告语义、直接SEC正文与8-K附件在线路径仍未验收 |

### 实际正文数据

三份原始HTML来自EdgarTools固定commit的MIT公开归档，均匹配本地SEC申报身份；不是本次SEC直连或实时下载。实际从第三方下载时间与原申报日期分开记录，未知的原采集时间为null。每份存储raw_bytes的SHA-256与manifest一致，归档来源和许可证见`data/documents/README.md`。

| 公司/申报 | 实际段落数 | 原字节哈希核对 |
|---|---:|---|
| AAPL 0000320193-24-000123（10-K） | 1241 | 一致 |
| AAPL 0000320193-24-000081（10-Q） | 584 | 一致 |
| NVDA 0001045810-23-000017（10-K） | 1928 | 一致 |

本地真实快照库没有导入合成金融资料或Fake模型结果。研究历史实际为空，未制作真实模型报告截图。测试中的合成HTML/协议响应只在独立测试库和Redis DB15使用。

### 修复与环境记录

首轮430项通过，补齐正文附件重试和来源版本并发回归后433项通过。浏览器联调发现旧RQ worker空闲后因Redis读Timeout正常退出；补充专用连接池与出队恢复后最终437项通过。未精确归因原长时间网络超时，不把连接配置当成已证实的唯一原因，也不重放已经执行的任务。此前排队的实际正文准备任务已由修复后的worker完成。

3份XHTML/Inline XBRL导入时出现BeautifulSoup的XMLParsedAsHTMLWarning。解析有意使用HTML模式处理XHTML及不规则HTML，章节/表格/隐藏节点与原字节校验已通过；该提示未被全局屏蔽，不作为失败隐藏。WSL日志有壁钟回拨，未将RQ壁钟耗时当作性能结论。

截图`24-real-filing-evidence.jpg`至`28-document-task-complete.jpg`为真实运行页面：中文证据、空历史、手机、英文历史与正文任务完成。不含伪造AI结论。

## 2026-11-03 本地服务连接恢复

用户反馈公司页面无法连接数据服务。检查时Windows没有5173/8000监听，WSL没有项目API/worker进程；启动WSL后PostgreSQL和Redis正常。确认开发服务未运行，未发现数据丢失证据；没有足够旧日志判定此前退出原因。

新增`scripts/start_local.ps1`及README运行说明，复用现有开发环境、动态探测WSL地址、隐藏启动缺失进程并检查前端代理。首次复用验证发现进程参数分割及跨WSL用户检测问题，修正后停止验证中重复创建的worker。最终实际验证：

- 停止本次启动的API、worker和前端后，脚本重新启动三者并返回Ready；前端代理健康检查database=ok、redis=ok、task_mode=rq、source_kind=sec_snapshot。
- 再次执行脚本复用全部服务；WSL核对仅一个API和一个worker进程。
- 经前端代理读取Apple公司接口返回200和真实快照公司；浏览器刷新后显示“服务已连接”及实际年度财务指标，控制台无error。

仅验证本机已有依赖环境的启动恢复，未重新运行领域全套测试，也不代表已完成开机自启或生产部署。恢复截图保存于`outputs/FinSight_AI_Service_Restored.jpg`。

## 2026-11-03 第五阶段：测试、部署与展示交付

### 最终实际结果

| 检查 | 环境、命令与结果 |
|---|---|
| 后端完整回归 | WSL Python3.12、PostgreSQL16.15专用`finsight_test`、Redis15、真实Linux RQ；`pytest -q`：**456 passed，108.33秒**，1条既有Starlette TestClient弃用警告 |
| 后端风格与依赖 | `ruff check backend scripts/manage.py`、按后端配置检查`frontend/e2e/serve_api.py`均通过；WSL `pip check`无依赖冲突 |
| 前端单元/类型/构建 | Node24.22.1、pnpm11.25.0；`pnpm lint`、`pnpm test`：**34 passed**；`pnpm test:e2e:check`通过；`pnpm build`2269模块、**29.33秒**；`pnpm install --frozen-lockfile --offline`通过 |
| 自动浏览器（PG） | Playwright1.64.1、实际Edge156.0.4314.58隔离无头实例；Windows前端4173→WSL API18000；独立`finsight_phase5_e2e_test`真实归档、Redis14：**7 passed，20.0秒**，含真实PostgreSQL FTS引文/哈希 |
| 自动浏览器（默认路径） | harness自动新建临时SQLite并启动/停止服务器；同一Edge：**7 passed，1.1分钟**；检索分支验证明确`POSTGRES_REQUIRED`，不冒充PG FTS |
| Nginx主机实测 | 官方Ubuntu包解包运行Nginx1.24.0，替换测试端口/静态目录/upstream，不安装系统服务；配置语法、构建首页、SPA深链接、JS类型/缓存、安全头、真实API代理、401与API404、65秒独立延迟上游均通过 |
| 备份恢复 | PostgreSQL custom dump恢复至新建隔离测试库，**15张表行数和内容哈希全部匹配**；恢复后source_kind=sec_snapshot；源库未修改；归档10,701,327字节、权限0600，未放入交付包；临时恢复库已删除 |
| Compose配置 | API readiness、前端健康依赖、挂载/环境语法：`docker compose config --quiet`通过 |
| 可复用HTTP烟雾 | `python scripts/smoke_http.py --base-url http://127.0.0.1:5173`通过：真实AAPL、14项指标、8条PG证据、原文哈希、私有历史401与深链接；CI容器job复用该脚本，容器分支尚未执行 |
| 实际容器构建 | `timeout 180 docker compose --progress plain build`失败：Docker Hub `python:3.12-slim` metadata请求i/o timeout；未进入镜像构建/容器启动验收。Compose的Nginx1.28与上述主机1.24不是同一运行环境 |
| 当前展示服务 | 重启API/worker加载安全修复后，前端代理status=ok、database/redis=ok、task_mode=rq、source_kind=sec_snapshot；浏览器实际年度指标正常、console error为空；测试18000/4173进程已退出 |
| CI/文档 | workflow配置后端、前端、浏览器和容器四个job、只读权限、超时/并发取消与失败trace；本地完成原生进程检查，容器job仅配置，**没有远程Actions运行结果**。补齐PRD、部署、演示、安全及中英简历材料，README保留8张真实截图 |

### 新增覆盖与修复

新增19个后端case，重点覆盖18个私有端点的缺失/错误/非ASCII令牌、未知异常日志、依赖故障恢复、研究队列失败/重试/过期替换、CORS及SEC/Ollama URL边界。修复`compare_digest(str)`遇非ASCII抛异常、Starlette未捕获异常可能被服务器记录原文本、SEC JSON沿用环境代理、空userinfo及映射link-local地址的拒绝边界。原有owner、SQL注入、模型协议、真实RQ与取消发布测试仍在完整回归内。

浏览器使用真实第三方SEC历史归档，覆盖搜索→来源/精确数字→季度→申报与事件→语言/会话权限→无模型门禁→真实正文/哈希/检索、手机布局及DOM XSS。XSS只临时拦截一条测试响应的名称文本，未改文件/数据库/金融数值。未创建Fake生产研究报告；实际模型成功生成/下载仍不能由这些浏览器测试推断。

Nginx读等待从60s调整至150s，以覆盖后端最多120s的连接测试；增加API readiness及前端健康依赖。assets缓存位置重新声明安全头，避免Nginx继承规则导致安全头丢失。备份恢复只验证数据库状态，不证明Redis任务或真实云Key解密恢复。

### 未隐藏的失败与限制

针对性后端测试首次163/164通过，日志断言因Alembic反复初始化禁用了测试logger而失败，局部修正后通过；最终全套456通过。浏览器首轮5通过、2因Element Plus只读input被选中标签遮挡而超时；改为点击可见外壳，未使用force，完整重跑7通过。默认启动曾因专用PG harness仍占18000而明确拒绝；停止该隔离进程后默认SQLite完整7通过，自动清理已核对。

官方Chromium CDN下载停在0%，停止下载后使用本机已安装的正式Edge，未宣称已完成下载Chromium验收；CI仍配置官方Chromium下载。XMLParsedAsHTMLWarning和NO_COLOR/FORCE_COLOR提示保留，未为生成通过数字屏蔽。Git没有remote，因此未启动远程CI。完整容器、真实云模型/Ollama、直接SEC与8-K附件、macOS实机和公网部署仍未验收。可选阶段六不提前启动。

新增HTTP烟雾脚本首次将检索枚举误写为keyword_fts而断言失败；按现有实际API的postgres_fts修正后完整通过，未改动生产检索契约以迎合测试。

## 2026-11-04：UI/UX 重构最终验收

本轮仅修改前端展示与交互、前端测试和文档，保留现有业务/路由/API/数据结构和后端。先建立设计系统及公司概览，在真实浏览器完成桌面浅/深及手机截图审查后，再将同一规范扩展到设置、研究及其余页面。详见 [UI/UX 审查](UI_UX_REVIEW.md)与[设计系统](DESIGN_SYSTEM.md)。

| 检查 | 实际环境与最终结果 |
|---|---|
| 前端类型与风格 | Node24.22.1 / pnpm11.25.0；`pnpm typecheck`、`pnpm lint`全部通过，无Lint警告 |
| 前端单元 | `pnpm test`：**39 passed（5 files），2.06秒**；新增5项图表展示测试，覆盖单位/零与缺失、输入不可变、精确字符串、长数字及标签密度 |
| 浏览器测试类型/Lint | `pnpm test:e2e:check`通过 |
| 完整浏览器回归 | Playwright1.64.1，实际安装的Edge156.0.4314.58隔离无头实例（`FINSIGHT_E2E_CHANNEL=msedge`）；Windows4173 → WSL18000，专用`finsight_phase5_e2e_test` PostgreSQL、Redis14；**13 passed，24.8秒** |
| 生产构建 | `pnpm build`含vue-tsc；Vite6.4.5处理**2282模块，9.25秒**；生成HTML/CSS及分离的应用、图表和UI资源 |
| 真实数据烟雾 | `python scripts/smoke_http.py --base-url http://127.0.0.1:5173`通过；source_kind=sec_snapshot、AAPL、14项指标、8条真实PG证据、哈希、私有历史401及SPA深链接 |
| 实际浏览器视觉 | 1440×1000公司概览/趋势浅深主题、设置浅深、研究输入及实际FY2024 10-K中文“风险”证据；检查层次、对齐、间距、留白、长哈希、摘录展开及来源定位 |
| 响应式与语言 | 390×844英文公司/设置/研究、真实证据手机布局；独立Edge补查320/768/1024px英文公司/设置/研究记录。390px文档宽375，无页面横向溢出；修复后320px公司操作条scrollWidth=clientWidth=288，四服务商标题scrollWidth=clientWidth=64 |
| 运行状态 | 真实开发服务恢复并连接PG/Redis；最终实际页面console error为空，恢复默认视口。专用测试4173/18000进程另行清理，不影响5173/8000开发预览 |
| 业务保留复查 | 与第五阶段源码包对比，研究页核心脚本保留；新增状态为主题、全局搜索、展开/检索/配置展示等，不更改财务计算、API契约或服务端权限 |

### 浏览器回归范围与夹具隔离

原7项真实归档流程继续通过：公司搜索/精确数字/Tooltip/来源/季度、申报事件及研究预选、语言和令牌内存、无模型门禁、真实正文哈希/PG FTS、手机和DOM XSS。新增4项检查浅/深/系统主题和刷新保持、Ctrl K/初次ArrowUp/Enter/Escape、无匹配与测试503重试、公司骨架/跳至主要内容/错误恢复/授权Toast且不发送越权同步POST。

另外2项仅使用Playwright浏览器响应拦截：

- 报告标注`TEST ONLY`和`source_kind=synthetic_fixture`，检查事实/推论/未知/证据分区、模型方法、超安全整数精确值、XSS文本转义、手机宽度、带会话请求头的Markdown下载及刷新后清除授权。截图只保存于忽略的`frontend/test-results/`，不进入真实数据截图画廊。
- 模型设置使用显式虚构的`TEST-ONLY-NOT-A-REAL-KEY`及模型ID，验证保存后清空Key、列表与手填切换、配置payload/版本、未验证版本不能启用、本地部署未配置状态、无凭据进入浏览器存储。所有LLM端点由测试响应拦截，不产生后端配置或真实服务调用。

上述夹具仅证明前端布局、交互和协议消费，不代表真实模型生成、服务商枚举、真实报告下载或语义质量通过。生产历史库未插入夹具报告或模型配置。

### 发现并修复的问题

首轮恢复验收时，本地开发服务及隔离API已停止，4项测试在harness标记请求超时/拒绝连接后中止；恢复开发服务并启动专用API后再测。随后完整测试12项通过，第13项因测试的嵌套`has`选择器重复根作用域而超时；修正后单项还发现Element Plus隐藏switch input及被标签覆盖的radio input不适合作为点击目标，改为点击可见控件并继续断言ARIA状态，无`force`、未放宽业务。单项通过后，最终完整13项全部通过。

视觉复查修复初次ArrowUp候选定位、320px英文公司按钮及服务商卡片局部挤压、浅色状态文字对比和一处Period翻译。图表Tooltip从样式受限的方案调整为安全DOM节点，保留精确值且避免HTML解释。全局/研究/设置CSS按结构格式化，未增加依赖。保留NO_COLOR/FORCE_COLOR提示，不隐藏失败诊断。

截图`29-ui-company-light.jpg`至`41-ui-home-light.jpg`均来自实际开发服务的真实归档或真实配置/权限状态；不含合成AI研究成果。旧`01`–`28`截图保留为历史记录。没有把截图或计算过的核心文字对比当作完整WCAG或所有辅助技术认证。

### 延续的未验收项

本轮未重跑后端456项全套、默认SQLite浏览器7项、Nginx/备份恢复或完整Compose；上方为它们此前的实际记录。当前PG浏览器回归、前端构建和真实数据烟雾提供本轮证据。没有新增后端功能或数据迁移。

真实云Key/Ollama、在线模型列表/推理及报告语义、直接SEC/8-K附件、Docker完整启动、远程CI、公网部署和macOS实机仍未验证。使用Edge并不代表官方Chromium下载或远程CI已通过；这些既有环境缺口不会因UI重构而关闭。

## 2026-11-04：A 股基础支持

### 最终本地回归

| 检查 | 实际执行及结果 |
|---|---|
| 后端完整回归 | WSL Python3.12、专用PostgreSQL `finsight_test`、Redis15、真实Linux RQ；**584 passed，174.80秒**，1条既有Starlette TestClient弃用提示 |
| 新增财务与来源协议 | 41项CN纯财务、65项提供方协议/安全测试，连同API/持久化/归档及并发用例纳入上述完整结果；不把fixture当实际金融资料 |
| PG并发 | 4项使用两个真实session保留旧ORM对象、反方向发布，以及NOWAIT确认目录/财务发布均持公司行锁；检查提交后释放，全部通过，无SQLite替代 |
| 风格 | `python -m ruff check backend scripts/manage.py`通过 |
| 前端 | Node24.22.1 / pnpm11.25.0；类型、Lint及E2E类型/Lint通过；**48 passed，6 files，2.77秒** |
| 浏览器最终 | Playwright1.64.1、已安装Edge156.0.4314.58独立无头实例，4173→WSL18000专用PG/Redis14；**14 passed，40.8秒**；原13项保留并通过，新增明确TEST ONLY的CN交互 |
| 最终构建 | Vue类型检查及Vite6.4.5生产构建通过，**2289模块，36.90秒**；最后增加窄屏/标签修正后重新执行 |
| 实际迁移 | 现有资料库从`e41a02`升级至`f51a01`，仅新增四张CN表；测试重复升/降级亦通过，原SEC资料未混入CN公司 |
| 原SEC烟雾 | 经真实5173代理执行`smoke_http.py`通过：AAPL、14项指标、8条真实PG证据/哈希、私有历史401和SPA入口 |
| Compose | `docker compose config --quiet`通过；CN三变量已转发至API/worker共享配置；本轮未完成容器联网构建/启动 |

### 实际在线与 RQ 闭环

通过当前开发API提交已授权任务，真实Redis/RQ Linux worker获取公开资料，写入独立CN表。没有使用模型或虚构金融数据。目录实际导入**6,185条A股记录**，含沪深京及历史代码，不代表全部仍上市。每家公司同步完整财务分页后原子发布，真实结果如下：

| 证券 | 实际最新报告期 | 三季累计营业总收入（CNY元，仅记录实测返回） | 当前保存财报公告 |
|---|---|---:|---:|
| 600519.SH 贵州茅台 | 2026-09-30 | 133,283,652,508.95 | 30 |
| 000001.SZ 平安银行 | 2026-09-30 | 103,100,000,000 | 30 |
| 920000.BJ 安徽凤凰 | 2026-09-30 | 323,908,337.78 | 30 |

这些值来自当前取得的东方财富结构化响应，未宣称已逐份与PDF人工复核。每家公司默认5年累计查询返回342个指标记录，缺失与不适用保留N/A；这不是342个全部有值的独立财务指标。**26份实际来源版本的原UTF-8字节SHA-256全部与存储记录一致**。抓取时间、财报披露日期及报告期分别保存。

另在只读提供方验证中，茅台主要指标/利润/资产负债/现金流分别104/104/104/100条，银行123/123/120/104条，北交所41/41/38/41条，均包含完整分页。巨潮单次请求pageSize=100实际返回30条，记录上游total/hasMore并明确保存范围，没有把本地30当历史总量。已取得茅台2026三季报真实PDF，响应200、格式标记`%PDF`；未将其当作已经解析并参与AI验证的正文。

东财公开结构化端点和巨潮目录/公告当前在本机无需新增API Key或联系邮箱可达。保险、证券模板的上游只读探测成功，但完整API/RQ/PG的实际公司样例仅为上表三家，不能推断全目录全历史全部覆盖。原始在线响应保留在本地work及数据库，不附入源码交付包。

### 页面与交互验收

- 实际浏览器查看茅台年度/2026前三季累计、同截止日跨年趋势、精确字符串、分页来源/hash/字段、巨潮财报列表。截图42–46来自真实开发服务资料。
- 手机390×844与英文深色银行页面实查；320px包含桌面浏览器滚动条时，可用宽305，修正后文档宽也305，没有整页横滚。大额银行金额紧凑展示，精确来源仍保留全部位数。默认视口、中文与浅色偏好在验收后恢复。
- 新CN浏览器fixture明确`TEST ONLY`、`synthetic_fixture`，仅拦截响应验证市场/符号/币种/精度/来源/累计/公告/能力边界/320与390/无权限不POST；没有写入生产数据。实际网络/RQ证据来自上方独立在线同步，而不是该fixture。
- 保留US CIK路由及最近查看身份；CN代码与CIK相同数字不会相互覆盖。CN的PDF/AI尚未支持时明确展示范围，不发送SEC概览或研究请求。

### 发现与修复

初轮完整572项通过后，独立复查补充并修复公司目录与财务同时更新JSON来源清单可能覆盖新版本的窗口：两个入口都在公司行锁下刷新ORM，目录按symbol排序锁定。补拒绝整张空报表、归档顶层格式/回滚、队列失败及旧资料保留，最终全套为584项通过。

真实源探测发现通用/银行/保险/证券需要不同三表模板；紧凑表字段不足，改用完整G/B/I/S表。北交所巨潮栏目应为szse；新旧代码仅经目录确认的同orgId接受，并保留原公告代码。未按名称猜测或放宽到跨市场身份。F10没有可靠的全历史合并范围字段，采用供应商报表口径标注，不冒称已认证。

浏览器发现CN头部误继承横向容器布局、其他公告显示原始枚举、英文币种重复及320px body最小宽叠加滚动条导致局部横滚，均已修正并复核。累计图改为相同MM-DD跨年比较，避免不同累计长度造成误读。原始数值不受这些展示修改影响。

一次Windows目标pytest因既有插件自动加载没有完成而中止，未计为通过；转用明确禁自动插件的现有WSL完整PG测试命令。继续会话时开发/测试服务已停止，初次浏览器运行在harness标记处连接超时，4项失败后中止；恢复服务后重新完整14项通过。CUA第一次自动许可检查曾超时，重试后成功，未当作页面或安全失败。Element Plus原生radio/按键辅助操作不适用时改用实际可见控件，页面状态重新确认，未使用force。没有隐藏这些未完成或失败的尝试。

### 当前边界

A股尚不支持PDF正文解析、AI研究/事件证据链、单季度差分、历史as-of/as-filed、实时行情或全市场逐家公司验收；合并范围和金融结论未逐份原文核查。第三方公开接口可变，也不等于商业再分发授权。

真实云模型/Ollama、SEC直连/真实8-K附件、完整容器、远程CI、公网部署及其他平台实机等原有缺口保留。本轮新建表后的备份恢复未重演，旧15表备份演练仍为此前记录；不能把既有结果当新增CN表的恢复终验。

## 2026-11-04 GitHub 发布前隐私检查

- 对最终271个Git候选文件及实际暂存区检查个人目录、常见凭据格式和敏感产物；本机3个实际秘密在内存中精确比对，均无待处理发现，未回显值。
- 47张截图全部视觉/元数据检查，8张设置截图放大复核；英文/中文离线OCR共94份并与实际秘密比对，未见个人账号、路径或凭据。没有修改原图，OCR原文仅在被忽略的work目录。
- 补充忽略TypeScript缓存、签名材料、本机配置、数据库/队列备份、诊断和压缩包；确认.env、work、outputs及tsbuildinfo均不属于上传候选。
- 新增发布检查和CI public-files任务。10项标准库unittest与新增Python的Ruff检查通过，覆盖被强制追踪的私密文件、个人路径/占位符、私钥/Key、秘密不回显、二进制精确匹配和路径穿越，以及暂存后工作树被清理/删除仍检出原秘密。
- 初次Windows测试受沙箱默认临时目录权限限制失败；将本次进程TEMP/TMP指向项目work下临时目录后10项通过。未把初次失败记录为通过，公开记录不包含本机个人绝对路径。
- 仓库当前无提交、暂存或已追踪文件；没有执行远程上传/Actions。干净源码包按候选清单重新生成并校验成员、哈希和排除项，旧交付包留在忽略目录。
- CI YAML与项目本地Markdown链接检查通过；`git add --dry-run`预检通过，实际暂存区仍为空。首次Git预检因沙箱限制无法取得临时index.lock，按只预检方式重新执行后通过，没有暂存、提交或推送。
- 上传准备时发现Windows Git换行转换会影响后续检出归档；`.gitattributes`对真实JSON/HTML归档目录显式禁用text/eol转换，保留原始字节和来源哈希。该修正不改动任何原始归档内容。

本轮未改业务逻辑，不重新宣称金融/模型/桌面功能获得额外验收。完整范围、操作与限制见[隐私报告](PRIVACY_REVIEW.md)。

## 2026-11-04 GitHub Actions 首次运行与导入分组修正

实际远程运行：[41794674385](https://github.com/Craft-s/FinSight-AI/actions/runs/41794674385)，提交`02c51bf`。前端、浏览器、容器和发布隐私检查的结论均为success；后端在`ruff check --config pyproject.toml ../frontend/e2e/serve_api.py`失败，错误为I001，因此该次后端pytest尚未执行。此结果也补充了GitHub Ubuntu runner的完整容器启动与烟雾验证证据；不代表本机Docker下载限制已经消除。

原因：Ruff从不同工作目录运行时，对浏览器测试宿主中的`app`包分类不同。本机从项目根检查可通过，从backend目录按CI原命令可复现缺少第三方/项目导入分隔空行。

修正：在`backend/pyproject.toml`明确`known-first-party = ["app"]`，并在`frontend/e2e/serve_api.py`保留相应空行。动态导入仍在隔离环境设置之后，没有提前加载生产配置，也未放宽检查、跳过测试或修改业务逻辑。

本地验证：根目录、backend和frontend三个工作目录的宿主Ruff检查通过；完整后端及共享Python脚本Ruff、发布候选/实际暂存区检查通过。后续完整云端检查由修复提交触发，最终结果以[Actions运行列表](https://github.com/Craft-s/FinSight-AI/actions/workflows/ci.yml)对应提交为准；原始失败记录保留，不能用最初本地584项代替该次未执行的云端后端测试。
