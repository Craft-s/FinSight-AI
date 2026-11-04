# FinSight AI — 智能财报研究与金融事件分析平台
## Codex 开发执行说明书（MVP → 可展示版本）

> 你是本项目的主开发 Agent。请将本文件视为产品需求、技术规范与验收标准。使用中文输出说明和文档，使用英文命名代码、数据库字段和 API 路径。直接在当前仓库实现可运行的软件，不要只生成设计稿或伪代码。先检查仓库，再建立任务清单，按里程碑逐步编码、启动、测试和修复。没有网络、API Key 或 Ollama 时仍应使用真实结构的测试夹具完成可验证的离线开发；不要将伪造数据当成实时数据。重要决策写入 docs/DECISIONS.md，不要无故扩展功能范围。

## 0. 项目定位与边界

**产品名称：** FinSight AI

**一句话定位：** 输入美股股票代码或公司名称，自动获取 SEC 官方财报和披露事件，对财务指标进行可复现计算、可视化与多期比较，并让用户通过自己选择的云端 AI 或本地 Ollama 进行带引用的研究问答和报告生成。

**主要展示价值：** Python/FastAPI 后端、数据采集与清洗、SQL 数据建模、RAG、LLM 适配器、异步任务、前端可视化、自动化测试、安全与容器化部署。

**必须遵循：**
1. **V1 仅支持能稳定从 SEC EDGAR 取得 10-K/10-Q/8-K 的美股申报主体。** 以 AAPL、MSFT、NVDA 为演示测试对象；不要宣称支持全部美股或全球市场。
2. **不需要付费金融数据 API。** 财务数据以 SEC 官方公开数据为准；股票价格、实时行情、A 股/港股、社交舆情不在 V1 必做范围。
3. **没有大模型也能运行。** 公司查询、申报检索、结构化财务指标、历史趋势、数值计算、8-K 元数据列表不依赖 AI API。
4. 用户可选 **云端大模型** 或 **Ollama 本地推理**；模型列表优先自动拉取，必须允许手动指定精确模型 ID。
5. AI 只负责证据归纳、定性比较、风险提示及自然语言解释；数字、增长率、比率由后端计算，不让 AI 凭空计算、编造。
6. 所有研究结论需要能追踪至原始财报或结构化数据记录。缺失值必须呈现为 N/A，不能填 0 或凭推测补齐。
7. 本项目是信息研究工具，不提供个性化投资建议；不得把事件发生后的价格影响推断成确定的因果结论。

**优先级：** P0 = 第一版必做；P1 = V1 完成后可加；P2 = 后续迭代。不要为了 P1/P2 牺牲 P0 可运行性。

## 1. 技术栈与项目结构

- 前端：Vue 3 + TypeScript + Vite + Element Plus + Pinia + Vue Router + Axios + ECharts。
- 后端：Python 3.12 + FastAPI + Pydantic v2 + SQLAlchemy 2 + Alembic + httpx。
- 数据库：PostgreSQL；全文搜索可先用 PostgreSQL FTS；RAG 向量层用 pgvector（可在后续阶段启用）。
- 后台任务：Redis + RQ 或 Celery，二者择一、统一封装；AI 长任务不得阻塞 Web 请求线程。
- 文档提取：SEC HTML 解析（BeautifulSoup / lxml），保留章节、来源及定位；PDF 上传属于 P1。
- 向量模型：可选本地 sentence-transformers 多语言嵌入，默认不依赖付费 Embeddings API。模型不可下载时退化到 FTS，并在 UI 明示检索模式。
- LLM：OpenAI、DeepSeek、Gemini、Ollama；P1 支持自定义 OpenAI-compatible URL。
- 工程质量：pytest、ruff、前端 eslint、Docker Compose、GitHub Actions、`.env.example`、清晰 README。

建议目录：

```text
finsight-ai/
  frontend/
    src/{api,assets,components,composables,layouts,pages,router,stores,types,utils}/
  backend/
    app/
      api/v1/
      core/{config,security,errors}/
      db/{models,migrations,session}/
      schemas/
      repositories/
      services/
        sec/{client,ticker_lookup,filings,companyfacts,normalizer}/
        finance/{metrics,periods,comparisons}/
        documents/{fetcher,parser,chunker,retriever}/
        llm/{base,factory,openai_provider,deepseek_provider,gemini_provider,ollama_provider}/
        research/{context_builder,prompts,output_validator,report_writer}/
      workers/
      tests/{unit,integration,fixtures}/
    alembic.ini
    pyproject.toml
  docs/{PRD,ARCHITECTURE,API,DATA_DICTIONARY,TEST_PLAN,DECISIONS}.md
  infra/
  docker-compose.yml
  .env.example
  README.md
```

## 2. 产品页面及交互（P0）

### 2.1 首页 `/`
- 顶部 Logo、导航：公司搜索 / 研究记录 / AI 模型设置。
- 搜索框支持股票代码、英文公司名称和 CIK 搜索（CIK 作为高级输入）；输入 AAPL、Apple、MSFT、NVIDIA 可得到对应候选项。
- 候选项显示：ticker、公司名、市场、CIK。用户确认选择后进入公司主页；有歧义时不可自动猜测公司。
- 最近查看公司、数据来源标识（SEC）、最近同步时间和错误提示。

### 2.2 公司页 `/company/:cik`
- 公司概况：公司名称、股票代码、CIK、交易所、财年末、数据最近同步时间（若可得）。
- Tab：`财务概览`、`财报列表`、`金融事件`、`AI 研究`。
- 财务概览：年度/季度切换、期间选择、币种/单位、财务指标卡、历史趋势图、原值与同比变化；图表需标注数据口径。
- 财报列表：10-K、10-Q，按披露日期降序排列，显示财报期间、披露日期、原始 SEC 链接；提供“同步”和“分析此份财报”。
- 金融事件：8-K 按时间排序，显示披露日期、事件项目（如可提取）、SEC 原文链接，支持“AI 解读事件”；AI 解读必须明确区分披露事实与推测风险。

### 2.3 研究页 `/research/:id`
- 分析类型：单份财报总结 / 两期财报对比 / 事件分析 / 针对公司财报提问。
- 展示引用证据卡片：文档类型、披露日期、报告章节、SEC 原链接、被引用的短摘录。
- 报告结构：摘要、财务表现（结构化数据）、管理层表述、主要风险与不确定因素、引用及方法说明。
- 任务状态：排队/处理中/成功/失败、重试按钮；生成完成后支持 Markdown 下载（PDF 导出 P1）。
- 历史记录可重新查看，不重复消耗 Token；相同任务支持缓存或手动重新生成。

### 2.4 设置页 `/settings/ai`
- 选择模式：`云端 API` / `Ollama 本地`。
- 云端服务商：OpenAI / DeepSeek / Gemini；P1 扩展其他 OpenAI-compatible。
- 云端展示 Key 输入框、服务商、模型选择框、`刷新模型`、`手动输入模型 ID`、`测试连接`、`保存`。
- Ollama 展示部署形态说明、受控服务地址、已安装模型列表、手动输入模型 ID、测试连接。
- 自动模型列表失败时给出具体错误并允许用户手动填写；手动填写成功与否以实际低成本调用测试为准。
- 高级选项（仅兼容模型才可配置）：temperature、max output tokens、reasoning effort、超时；不要把某厂商参数不加区别地发给所有模型。
- 明确说明“模型列表中的模型未必都支持财报文本分析或当前账户调用”。
- 密钥遮蔽显示；永远不要从读取配置接口返回明文 Key。
- Demo 无 Key 状态下只禁用 AI 分析按钮，不禁用基础金融功能。

### 2.5 UI 规范
- 简洁专业的金融终端设计，中文优先，响应式布局，浅色/深色任选其一先完整做好。
- 必须具备加载骨架屏、空态、请求失败重试、数据缺失、数据陈旧提示；所有日期清晰注明时区/时间来源。
- 首页、公司页、设置页、研究页均使用真实 API 数据，不可把硬编码演示值伪装成最新值。

## 3. 业务核心流程

### 3.1 从 Ticker 到财报

```text
用户输入 AAPL 或 Apple
  → 从本地已缓存的 SEC ticker/CIK 映射表查找并列候选
  → 用户选中 CIK（例：0000320193）
  → 查询本地公司与财报缓存
  → 缓存不存在/过期：后端调用 SEC submissions 与 companyfacts
  → 解析并保存公司信息、申报元数据、财务事实
  → 规范化财务指标和时间序列
  → 前端呈现财务概览、10-K、10-Q、8-K
  → 用户可选择报告或事件进行 AI 研究
```

### 3.2 AI 研究

```text
用户选公司 + 指定报表/比较期间 + 选择已验证 LLM 配置
  → 创建研究任务（job）
  → 确认所需财务事实、报告正文和来源可用
  → 文档章节分块/关键词或向量检索
  → 构建紧凑事实表 + 引用片段的可信上下文
  → 调用云端/本地模型生成结构化 JSON
  → Pydantic 校验，数字交叉核验，引用 ID 真实性检查
  → 持久化结果与出处，返回前端
```

## 4. SEC 数据源与抓取要求

### 4.1 官方接口

```text
股票代码与 CIK 映射：
https://www.sec.gov/files/company_tickers.json

交易所增强映射（可选）：
https://www.sec.gov/files/company_tickers_exchange.json

指定公司申报历史：
https://data.sec.gov/submissions/CIK0000320193.json

指定公司 XBRL 事实：
https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json
```

- SEC data.sec.gov 公共 API 不要求 API Key；后端统一请求，禁止从前端直接抓 SEC。
- 设置可配置且有明确联系信息的 SEC User-Agent（如 `FinSightAI contact@example.com`，须由部署者填写真实邮箱）；符合 SEC 公平访问政策，整体限速 **严格低于每秒 10 次请求**，建议 3–5 QPS；有缓存、指数退避、429/403/5xx 错误处理，尊重 Retry-After。
- 定期更新 ticker 映射；不能假定 ticker 永远不变或全局唯一，以 `market + CIK` 为系统主识别方式。
- 申报 `recent` 为列式数组：正确对齐 `form`、`accessionNumber`、`filingDate`、`reportDate`、`primaryDocument` 等字段。翻页时如有历史分片应可扩展，不需要 V1 一次下载全部历史。
- 构造原始申报 URL 时使用 SEC 官方档案路径与准确 accession 编号（去掉连字符），保留正确 primaryDocument；不要猜测文件名。
- `10-K` 为年度、`10-Q` 为季度、`8-K` 为事件。10-K/A、10-Q/A 等修订表单可单独标记，不能悄悄与原文混合。
- 先列出来源，再按需抓取指定财报正文；控制 HTML 大小、抓取频率和缓存 TTL。
- 不要把 8-K 全部等同于利润变化：事件分类需根据表单实际项目或文档内容并注明证据。

### 4.2 结构化财务数据与可复现口径

必须实现至少下列指标（适当的 US-GAAP concept 候选映射、单位/期间校验与缺失返回）：

| 指标 | 参考 concept（非唯一） |
|---|---|
| 营业收入 | `RevenueFromContractWithCustomerExcludingAssessedTax`, `Revenues`, `SalesRevenueNet` |
| 净利润 | `NetIncomeLoss` |
| 毛利润 | `GrossProfit` |
| 营业利润 | `OperatingIncomeLoss` |
| 经营现金流 | `NetCashProvidedByUsedInOperatingActivities` |
| 总资产 | `Assets` |
| 总负债 | `Liabilities` |
| 股东权益 | `StockholdersEquity` |
| 现金及等价物 | `CashAndCashEquivalentsAtCarryingValue` |
| 稀释每股收益 | `EarningsPerShareDiluted` |

计算：同比增长率、毛利率、净利率、营业利润率、经营现金流/净利润。要检查分母零值、币种、单位、负数分母的增长率语义，不适用时返回 N/A。

**关键：公司事实不能靠 `fy/fp` 直接当季度值。** 每条事实要依据 `start`、`end`、`form`、`filed`、`accn`、`unit`、可能的 `frame`、持续时间与财年归属来判定：
- 年报年度值与季度值严格区分；季度损益/现金流可能在 10-Q 中以年初累计（YTD）呈现，需用同口径数据做差分得到单季度，不能把 YTD 直接放进季度图。
- Q4 常需要从 FY 全年减去同一财年 Q1–Q3 累计（9M）得出；若缺少一致口径不得硬算。
- 资产负债表是期末时点值，不作累计差分；利润表/现金流是期间值，区分 3M/6M/9M/12M。
- 允许同一期间多次修订或不同披露时间，使用明确的“最新有效申报/修订”选择策略，保留原来源 `accn`、`filed`、`start`、`end`；不跨不兼容单位和口径混算。
- 考虑不同公司财年末不同（例如 NVDA）；财年 Q1–Q4 不等于自然年季度。
- 股份/每股数据的 USD/share、USD、shares 单位严格区分。
- 若公司使用自定义 XBRL taxonomy、缺少常见 us-gaap tag 或存在 53 周财年，优先展示明确提示，而不是编造映射结果。
- 每个指标显示来源、财务期、申报日期和计算/选择规则；`financial_metrics` 包含 provenance 字段。

## 5. AI 模型统一适配层

### 5.1 支持范围（P0）

| Provider | 读取模型列表 | 运行推理 |
|---|---|---|
| OpenAI | `GET /v1/models` | 使用官方 SDK / 正确支持的文本推理 API |
| DeepSeek | `GET /models` | 使用官方支持的协议 |
| Gemini | `models.list` | 使用官方 Google GenAI SDK |
| Ollama | `GET /api/tags` | `POST /api/chat` 或兼容 API |

- 使用 Adapter/Strategy 模式，规范共同能力：`list_models()`, `test_connection(model_id)`, `generate_structured(prompt, context, options)`, `normalize_error()`。
- `list_models()` 返回统一类型：`id`, `display_name`, `provider`, `capabilities?`, `context_window?`, `supports_text_generation?`。元数据不可靠/不完整时，用未知状态，不要臆造。
- Gemini 枚举须处理分页和 `supportedGenerationMethods`（或对应 SDK 字段），只优先呈现支持 `generateContent` 的模型；其他提供商如缺乏可靠能力标签，允许手动尝试。
- 自动枚举失败（403、超时、厂商不支持、网络不可达）允许手动填写模型 ID；调用前做小型实际测试；列表缓存短时有效。
- 不要硬编码模型型号/价格当成最新；正式模型 ID 以 provider 返回或用户填写为准。
- 统一错误码：`AUTH_INVALID`, `MODEL_NOT_FOUND`, `MODEL_NOT_SUPPORTED`, `RATE_LIMITED`, `PROVIDER_UNAVAILABLE`, `TIMEOUT`, `INVALID_RESPONSE`。
- 所有调用有合理超时、最多有限次数重试、取消能力（如可行）、Token/时长与用量统计（如厂商返回）；严格控制研究输入长度。

### 5.2 本地与远程部署

- 在开发机运行 backend + Ollama 时默认 `http://127.0.0.1:11434`；若 backend 在 Docker 中，需在 README 告知 Docker 容器内 `localhost` 指容器本身，并提供适用宿主系统的 `host.docker.internal`/host-gateway 连接配置。
- **云端托管的 backend 不能直接访问浏览器用户的 `localhost`**，在线版的“本地模式”应显示限制说明，并禁用不安全的客户端任意地址直连。未来若接入客户端 Agent/安全隧道，作为 P2 单独设计。
- P0 Ollama URL 限制为部署者配置的允许地址，普通用户不可让后端任意请求 URL（防 SSRF）。P1 自定义 provider 只允许管理员受控配置，并执行 host/IP 校验、DNS 重绑定防护、禁止内网/元数据端点等。

### 5.3 Key 与模型配置安全

- API Key 通过 HTTPS 传给后端；永不记录在日志、前端持久存储、浏览器 LocalStorage、错误栈或 Git 中。
- 私有配置可使用服务端环境变量作为单用户 MVP，若支持用户 BYOK，必须按用户分离并加密保存（服务端加密密钥通过环境变量注入）；查询时仅返回 `has_api_key: true/false` 和已遮蔽状态。
- 安全密钥校验失败不破坏其他功能；禁止将 Key 转发到非对应服务商域名。
- Basic RBAC：至少普通用户/管理员；如果 MVP 从单用户模式开始，在文档明确限制，公开多用户版再启用独立配置与访问控制。
- 对报告内容与 SEC 文章视为**不可信上下文**：禁止让文档内的指令覆盖系统指令，不执行财报文本中的命令、不随意调用外部 URL。

## 6. RAG 与研究报告结构

### 6.1 文档处理

- 对 SEC 原始 HTML 保留标题、章节、段落文本及原始 URL；过滤脚本、样式、导航杂项；分块保留 `filing_id`, `section`, `chunk_index`, `source_url`, `text`。
- 优先实现可解释的混合检索：关键词/FTS + 可选向量检索；缺少 embedding 模型时仍可进行关键词检索。
- 引用对象：`citation_id`, `filing_id`, `accession`, `section`, `source_url`, `excerpt`, `chunk_id`。对 AI 返回的引用 ID 逐条校验是否存在于当次上下文。
- 文档引用要求来自真的来源；无法找到直接证据时输出“未在已检索资料中找到依据”，不可编造。

### 6.2 模型输出 schema（示意）

```json
{
  "summary": "简要结论",
  "financial_highlights": [
    {"metric_key": "revenue", "period": "FY2025", "commentary": "解释变化但不重复计算", "citation_ids": ["CIT-001"]}
  ],
  "management_commentary": [
    {"claim": "管理层的主要表述", "citation_ids": ["CIT-002"]}
  ],
  "risks": [
    {"description": "披露的风险或合理的不确定性", "type": "disclosed_fact", "citation_ids": ["CIT-003"]}
  ],
  "uncertainties": ["信息限制"],
  "disclaimer": "仅供研究和教育，不构成投资建议。"
}
```

- 别强行把所有厂商都要求为同一原生 JSON schema：优先使用厂商支持的结构化输出，否则在后端解析/校验并有失败重试与错误说明。
- 数据事实引用由后台从数据库强制填充，AI 不拥有修改事实值的权限。
- 事件分析将“已披露事实”“模型推论/可能影响”“仍未知”分成不同字段，禁止宣称确定股价方向。

## 7. 数据库设计（建议表）

使用 Alembic migrations 管理，尽可能使用 UUID 主键，保留唯一约束和必要索引：

1. `users`: id, email, password_hash, role, created_at。
2. `companies`: id, market, cik, ticker, name, exchange, fiscal_year_end, last_synced_at；`(market,cik)` 唯一。
3. `ticker_aliases`: company_id, ticker, valid_from, valid_to, is_current，避免改名丢记录。
4. `filings`: id, company_id, accession_number, form_type, filing_date, report_date, primary_document, source_url, content_status, amended, fetched_at；accession 唯一。
5. `financial_facts`: id, company_id, taxonomy, concept, unit, value, start_date, end_date, filed_at, accession_number, fiscal_year, fiscal_period, form_type, frame, raw_context_json；按公司/概念/期间/申报建立索引和去重规则。
6. `normalized_metrics`: id, company_id, metric_key, period_type, fiscal_year, fiscal_quarter, period_start, period_end, value, unit, calc_method, provenance_json, last_updated_at；唯一键覆盖公司、指标和明确期间。
7. `document_chunks`: id, filing_id, section, chunk_index, content, source_url, optional_embedding, created_at。
8. `llm_configs`: id, owner_id, provider, model_id, base_url_identifier, encrypted_key, options_json, last_tested_at, is_active；不可回传 `encrypted_key`。
9. `research_jobs`: id, owner_id, company_id, filing_ids_json, task_type, status, provider, model_id, error_code, created_at, started_at, finished_at。
10. `research_reports`: id, job_id, structured_result_json, markdown, citations_json, snapshot_metrics_json, generated_at。
11. `audit_logs`: id, actor_id, operation, target_type, target_id, metadata_json, created_at（敏感参数脱敏）。

注意：P0 可按交付节奏将 `users`、`audit_logs` 简化，但不要省略来源/期间/申报追溯字段。保持单一迁移来源，不使用启动时自动 `create_all()` 代替 Alembic。

## 8. REST API 规格（建议 `/api/v1`）

| Method | Endpoint | 功能 |
|---|---|---|
| GET | `/health` | 服务健康检查（可区分 db/redis 状态） |
| GET | `/companies/search?q=AAPL&market=US` | 公司候选搜索 |
| GET | `/companies/{cik}` | 公司概况 |
| POST | `/companies/{cik}/sync` | 提交 SEC 同步任务 |
| GET | `/companies/{cik}/filings?type=10-K&page=1` | 申报分页 |
| GET | `/companies/{cik}/metrics?frequency=annual&years=5` | 财务指标时序 |
| GET | `/companies/{cik}/events?type=8-K&page=1` | 金融事件元数据 |
| GET | `/filings/{filing_id}` | 财报内容元数据与来源 |
| GET | `/ai/providers` | 可用提供商及能力信息 |
| POST | `/ai/models/list` | 使用选中 provider 读取模型列表 |
| POST | `/ai/configs/test` | 验证模型和凭据 |
| GET | `/ai/configs` | 列出用户保存配置，脱敏 |
| POST | `/ai/configs` | 保存加密配置 |
| DELETE | `/ai/configs/{id}` | 删除用户配置 |
| POST | `/research/jobs` | 创建研究任务 |
| GET | `/research/jobs/{id}` | 查询任务状态 |
| GET | `/research/reports/{id}` | 查询研究报告和引用 |
| GET | `/research/reports/{id}/download?format=md` | Markdown 下载 |
| GET | `/research/history` | 用户历史研究列表 |

规范：
- 所有 HTTP 请求/响应使用 Pydantic schema，OpenAPI `/docs` 自动生成。
- JSON 错误统一为 `{ "error": { "code": "...", "message": "...", "details": {} }, "request_id": "..." }`。
- 金额精度在后端保留 Decimal/精确数值，前端仅负责显示格式化；空值为 `null`，不是 `0`。
- 大任务返回 202 + job_id，前端轮询状态（P1 可改 SSE）；重复点击要有幂等控制。
- 所有可修改资源必须检查登录用户权限，不得按用户传入的 `owner_id` 直接操作数据。

## 9. 测试与验收（必须）

### 9.1 无密钥可验收路径
- `docker compose up --build` 可启动前后端、数据库、任务服务；README 有 Windows/macOS/Linux 指南。
- 未配置 AI API Key 时，首页公司搜索、公司详情、历史报表、财务趋势图、8-K 事件列表可用。
- 当 SEC 在线不可用或 CI 无网络时，使用**明确标识为 fixture/test data 的 SEC JSON 测试快照**完成自动化测试；界面不可误标为最新真实数据。
- AI 配置界面正确呈现无模型 / 无密钥 / Ollama 不可达状态，并可恢复。

### 9.2 单元测试
- Ticker → CIK（包括前导 0、别名、同名歧义）。
- SEC submissions 列式数据正确对齐；不同表单识别；原报告链接构造。
- revenue concept 回退映射；分币种和单位；跨申报去重；修订与 filed 时间选择。
- 年报 vs 单季度 vs YTD；Q4 = FY - 9M；资产负债表 point-in-time；非 12 月财年；缺失值返回 null；零分母防护。
- AI model list 自动拉取/空列表/权限错误/超时/手动 ID/连接测试。
- LLM 返回非 JSON、无效 citation_id、超时、敏感信息不泄露。
- SSRF 防护、用户隔离、权限检查、SQL 注入/XSS 基础防护。

### 9.3 集成与 UI 测试
- 三个目标示例公司 AAPL、MSFT、NVDA 至少覆盖两种不同财年末。
- 端到端：搜索公司 → 查看财报 → 财务图表 → 选择模型 → 启动研究 → 结果含引用 → 下载 Markdown。
- 不接外部 Key 的 CI 使用 `FakeLLMProvider` 测试适配层，不伪装其输出为生产模型结论。
- 启动测试命令、代码风格检查与构建检查必须在 Codex 输出中实际执行并报告结果，不能臆称通过。

### 9.4 可交付标准
- 系统有实际可运行的 API 和前端，不得只有静态 mock 页面。
- 读我文档包含环境变量、SEC 用量政策、首次数据同步、AI 模式切换、Ollama 安装和 Docker 网络区别、部署警告、常见报错。
- README 至少 4 张真实运行截图（运行成功后拍摄），架构图、API 示例、测试说明、演示步骤。
- 没有 `.env`、API Key、数据库密码或其他秘密被提交。
- 当数据不支持、没有来源、财报口径无法统一时，提供清晰“不支持/不可比/数据缺失”解释，不伪造值。
- 请输出实际实现清单、运行命令、测试结果、已知限制与下一阶段任务。

## 10. 分阶段编码指令（强制按顺序）

### 阶段 1：项目骨架与无 AI 数据闭环（P0）
交付：仓库结构、Docker Compose、配置、DB migrations、公司搜索、SEC 客户端、companyfacts 解析、至少五项基础财务指标、年度图表、10-K/10-Q/8-K 列表、基本前端页面、后端测试。**阶段验收：无 Key、无 Ollama 时可完整展示数据闭环。**

### 阶段 2：多期间数据、财务口径与来源（P0）
交付：季度和年度转换、同比、利润率、指标来源追踪、财年差异与修订逻辑、清晰缺失值、历史报表详情、单元测试和测试快照。**阶段验收：AAPL、MSFT、NVDA 数据口径可解释，关键转换有测试。**

### 阶段 3：AI 双模式与模型配置（P0）
交付：4 个 Provider Adapter、自动模型列表、手动模型 ID、连接测试、加密配置、Ollama 网络说明、统一错误处理、最简财务概览 AI 总结、Fake Provider 测试。**阶段验收：能真实切换任一已配置可用的云端 provider 与本地 Ollama，而无需修改业务代码。**

### 阶段 4：检索、事件解读和研究报告（P0）
交付：指定财报 HTML 清洗和段落块、全文检索、可选 pgvector、引用 ID 校验、10-K/10-Q 研究、8-K 事件解读、异步任务、历史记录和 Markdown 下载。**阶段验收：至少一份研究报告每个实质事实可回溯来源；无法验证时明确不足。**

### 阶段 5：部署与简历展示（P0）
交付：完整测试、权限安全检查、失败场景、Docker 文档、CI、README 截图、架构图、Demo 操作流程。不能在无服务器/域名资源时声称公网已部署。

### 阶段 6：可选增强（P1/P2，不阻塞前述工作）
- P1：用户上传 PDF 财报、PDF 报告导出、公司间指标对比、模型结果比较、外部 OpenAI-compatible 受控 API、成本估算与调用预算。
- P2：A 股/港股数据适配器、历史股价叠加、定时披露追踪、邮件提醒、在线访问本地 LLM 的安全桌面代理、多租户 SaaS。

## 11. 对 Codex 的执行规则

1. 首先扫描当前仓库：若为空则初始化 monorepo；若已有项目则保留已有配置并说明改造方案。
2. 开始时创建 `docs/TASKS.md`，列出阶段任务和完成状态；每阶段至少完成一条真实可运行的端到端路径。
3. 不要一次性生成全部代码却不运行；每个阶段分别执行启动、lint、测试、构建，修复错误再推进。
4. 有第三方 API 但当前执行环境网络不可用时，使用契约测试 + 官方响应结构的 fixtures；把在线集成标为待人工验证，不得假称已实际联网调用。
5. 不可凭空写测试覆盖率或性能数字；只展示真实终端输出和实际结果。
6. 对合理未指定项自主选择工程上简单可靠的默认方案，并在 `docs/DECISIONS.md` 记录，除非出现真正阻断问题，否则不用反复询问。
7. 优先完成阶段 1；若一次会话无法完成所有阶段，结束时留下明确的已完成文件、运行状态、失败原因、下个任务，方便后续 Codex 继续。
8. 所有生成的演示内容均需标记 demo / fixture，不能冒充实时 SEC 财务结果或真实模型分析。

## 12. 必须参考的官方资料

- SEC EDGAR APIs：https://www.sec.gov/search-filings/edgar-application-programming-interfaces
- SEC Accessing EDGAR Data：https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data
- SEC Fair Access：https://www.sec.gov/about/developer-resources
- OpenAI list models：https://developers.openai.com/api/reference/resources/models/methods/list
- DeepSeek list models：https://api-docs.deepseek.com/api/list-models/
- Gemini models.list：https://ai.google.dev/api/models
- Ollama list local models：https://docs.ollama.com/api/tags
- Ollama OpenAI compatibility：https://docs.ollama.com/api/openai-compatibility

## 13. 第一条执行指令（请立即开始）

请不要只回复“方案可行”。立即执行**阶段 1**：建立可启动的仓库基础设施、FastAPI 健康检查、PostgreSQL + Alembic、SEC ticker/CIK 搜索、submissions/companyfacts 获取及缓存、基础年度财务指标接口、Vue 公司搜索/公司详情/历史图表/财报事件列表。使用官方结构的本地测试夹具保证离线测试可运行。最后运行测试和构建，给出真实结果，再按照阶段任务继续推进。如果环境缺失外部凭据，按文档将依赖它的功能延后，不要阻断无 AI 的功能。
