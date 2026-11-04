# API 说明

自动生成OpenAPI位于后端 `/docs`。业务前缀为 `/api/v1`，错误统一包含 `error.code/message/details` 和 `request_id`。金额为精确十进制字符串或null；日期来源见数据字典。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /health | 数据库/Redis/模式/AI可用性，不含秘密 |
| GET | /companies/search?q=&market=US | 候选列表，按CIK确认，不自动解决歧义 |
| GET | /companies/{cik} | 公司资料、来源模式和陈旧状态 |
| POST | /companies/{cik}/sync | 需要X-Admin-Token；202返回job_id，在途复用 |
| GET | /sync/jobs/{id} | queued/running/succeeded/failed及错误 |
| GET | /companies/{cik}/metrics | 平坦指标序列及来源 |
| GET | /companies/{cik}/filings | 定期报告分页，type可选10-K/10-Q及修订 |
| GET | /companies/{cik}/events | 8-K与修订分页 |
| GET | /filings/{id} | 单份申报元数据和原始链接 |
| GET | /sources/{id} | 快照哈希、采集日期、来源及归档元数据 |

阶段二metrics参数契约：`frequency=annual|quarterly`，`years=1..10`。`selection_policy=latest_restated`默认使用保存资料中的最新有效披露；`as_of`需同时提供ISO日期`as_of=YYYY-MM-DD`；`as_filed`需同时提供该公司的定期报告`accession`。不兼容参数应拒绝，而不是静默回退。

每项包含metric_key、label、period_type、fiscal_year、fiscal_quarter、period_start/end、value、unit、calc_method、provenance、missing_reason。第二阶段额外提供yoy与yoy_reason：百分数形式字符串，例如`10.5`表示10.5%。利润率以percent指标项返回，经营现金流/净利以ratio返回。

示例（以下为请求用法，不是服务器实际响应）：

```sh
curl 'http://127.0.0.1:8000/api/v1/companies/search?q=AAPL'
curl 'http://127.0.0.1:8000/api/v1/companies/0000320193/metrics?frequency=annual&years=5'
curl 'http://127.0.0.1:8000/api/v1/companies/0000320193/filings?type=10-K&page=1'
```

## 第三阶段模型配置

除公开能力查询`GET /llm/status`外，以下配置和概览端点均要求`X-Admin-Token`，所有权由后端的私有单用户身份决定，不接受客户端传入owner。对外部署必须使用HTTPS。模型Key只接收写入，不在读取、错误或测试响应中返回。

| 方法 | 路径（/api/v1前缀） | 说明 |
|---|---|---|
| GET | /llm/status | available、encryption_ready、ollama_configured与四服务商的协议参数选项；不含Key或地址 |
| GET | /llm/configs | 当前owner的配置列表`{items: [...]}` |
| POST | /llm/configs | 创建或按id/version更新配置；每owner每服务商一份 |
| POST | /llm/configs/{id}/models | `{refresh: false}`；返回`{items: [...], cached: false}`，refresh可绕过缓存 |
| POST | /llm/configs/{id}/test | `{version: 1}`；小型实际文本调用，成功返回`{config, usage, duration_ms}` |
| POST | /llm/configs/{id}/activate | `{version: 1}`；仅启用当前版本已通过测试的配置；同owner仅一个默认启用配置 |
| POST | /llm/configs/{id}/delete | `{version: 1}`；删除配置及对应缓存，返回`{deleted: true}` |
| POST | /companies/{cik}/overview | 创建结构化指标概览，202返回`{job_id, status, reused}` |
| GET | /overview/jobs/{id} | 当前owner的概览状态、脱敏错误与成功result |

配置写入字段：`id?`、`version?`、`provider`（openai/deepseek/gemini/ollama）、`model_id`、`api_key?`、`options?`。模型ID允许空字符串以便先保存Key再枚举；连接测试和激活要求非空。更新必须提供id与当前version，provider不能被修改；省略或null的api_key保留原Key，空字符串清除Key，云配置不允许缺Key。Ollama不接收云Key或base_url，连接origin完全由部署者环境配置。

`options`包括`max_output_tokens`（16..8192，默认2048）、`timeout_seconds`（5..120，默认30）、可选`temperature`（0..2）与`reasoning_effort`。OpenAI协议接受none/minimal/low/medium/high/xhigh，DeepSeek接受none/low/high/max且temperature仅配合effort=none；Gemini/Ollama不接受reasoning选项。协议选项不等于每个模型都支持，实际小调用会检查保存的模型与参数组合。连接测试有独立的小输出预算，其用量以厂商返回值为准。

配置响应仅包含`id, provider, model_id, version, verified_version, last_tested_at, is_verified, is_active, has_api_key, options, created_at, updated_at`。配置变更使验证失效；测试完成时使用版本比较更新，不能为测试过程中修改的新配置背书。主密钥或Ollama部署地址改变，也会使旧验证失效。已测试但未设为默认的配置仍可通过明确config_id用于概览。

统一模型记录为`id, display_name, provider, supports_text_generation, context_window`，未知能力为null，不猜测模型价格或窗口。模型列表缓存按owner、配置ID、版本及部署绑定隔离；枚举失败仍允许保存手动ID。Gemini列表会处理分页并筛选支持generateContent的记录。

厂商错误归一为`AUTH_INVALID`、`MODEL_NOT_FOUND`、`MODEL_NOT_SUPPORTED`、`RATE_LIMITED`、`PROVIDER_UNAVAILABLE`、`TIMEOUT`、`INVALID_RESPONSE`；配置错误另有`ENCRYPTION_NOT_CONFIGURED`、`CREDENTIAL_UNREADABLE`、`OLLAMA_NOT_CONFIGURED`、`CONFIG_VERSION_CONFLICT`、`LLM_NOT_VERIFIED`等。所有错误使用本文开头的统一信封，不返回厂商原始错误正文。

## 指标概览请求与结果

示例是请求结构，不是已联网生成的模型结果：

```json
{
  "config_id": "已保存且通过测试的配置ID",
  "frequency": "annual",
  "selection_policy": "latest_restated",
  "language": "zh-CN"
}
```

frequency支持annual/quarterly；口径参数沿用指标API的互斥规则，as_of/as_filed分别要求对应截止日期/accession；language支持zh-CN/en-US。任务在创建时冻结所选口径最近已披露期间的结构化指标和来源。Redis/RQ只传job_id，模型Key不进入任务参数。相同已验证配置版本、部署绑定、事实上下文、提示规则和语言可复用结果；worker执行前后同时比较配置版本与绑定，部署地址或主密钥变化不会借用旧结果。队列不可用会明确失败，生产不会静默降级到同步执行或Fake模型。

任务状态为queued/running/succeeded/failed。成功result包含observations、facts、limitations、provider、model_id、config_version和用量等；每个observation必须引用当次上下文的metric_refs，并提供与冻结事实完全一致的reported_values。数值或单位不符、引用缺失、无效JSON、拒答、截断均不会保存为成功分析。自由文本禁止数字字符和常见中英文金额/比例数字表达，数值由facts显示；这不是完整自然语言语义验证。`validation.copied_fact_values=verified`仅表示复制的结构化数值一致，`citation_ids=verified`仅表示引用存在，`semantic_support=not_automatically_verified`始终明示语义支持未自动验证。最终发布事务锁定配置并再次核对版本与部署绑定。以下第四阶段接口提供独立的正文研究与报告流程。

四家在线推理当前缺少实际Key/Ollama而未验证；离线协议、迁移、数据库和队列的执行结果见`TEST_RESULTS.md`。

## 第四阶段正文与研究（2026-11-03）

公开读取端点仅查询已保存的申报正文元数据和证据；正文准备、模型研究、任务控制、历史与报告下载均检查`X-Admin-Token`及owner。不接收任意远程URL或客户端owner。原始HTML不会通过页面作为可执行内容渲染。

| 方法 | 路径（/api/v1前缀） | 请求与响应 |
|---|---|---|
| GET | /filings/{filing_id}/documents | `{items: DocumentOut[], retrieval_mode}`，已保存文档版本 |
| POST | /filings/{filing_id}/documents/sync | 鉴权；空对象或省略body；202 `{job_id,status,reused}`，RQ准备正文 |
| GET | /documents/jobs/{job_id} | 鉴权；该owner的文档任务状态 |
| GET | /companies/{cik}/evidence?q=&filing_id= | `q`为1..500字符；可选具体申报；返回`{items: CitationOut[],retrieval_mode,scope_note}` |
| POST | /research/jobs | 鉴权；创建研究，202 `{job_id,status,reused}` |
| GET | /research/jobs/{job_id} | 鉴权；研究状态及成功后的`report_id` |
| POST | /research/jobs/{job_id}/cancel | 鉴权；body `{}`；返回当前任务状态 |
| POST | /research/jobs/{job_id}/retry | 鉴权；body `{}`；失败/取消任务以原上下文创建新任务，202接受响应 |
| GET | /research/history?page=1&page_size=20 | 鉴权；`page>=1`、`page_size=1..50`，返回`{items,total,page,page_size}`；不含文档准备任务 |
| GET | /research/reports/{report_id} | 鉴权；报告元数据、结构化`content`、实际`usage`与`duration_ms` |
| GET | /research/reports/{report_id}/download?format=md | 鉴权；只支持md，返回`text/markdown`附件 |

`DocumentOut`包含id、filing_id、title、source_url、sha256、source_kind、fetched_at、parser_version、role及source_metadata；role为primary/exhibit，附件覆盖说明可在source_metadata中呈现。`CitationOut`包含citation_id、filing_id、accession、form_type、filing_date、section、source_url、excerpt、chunk_id、document_sha256及**JSON对象location**，不是位置字符串。位置对象保留block_index、tag、anchor、part_index，表格还可包含table_index、row_index等字段。

公司证据检索在指定filing_id时验证公司归属，只查该申报。未指定时仅查询当前模式下最近十份已有正文的申报，最多返回八个片段，并通过scope_note说明范围。实际生产模式为postgres_fts；section_fallback表示章节候选而非语义命中，test_keyword仅用于显式SQLite合成测试环境。没有命中不得被解释为财报不存在该事实。

研究请求示例仅说明结构，不是已在线生成的报告：

```json
{
  "cik": "0000320193",
  "task_type": "summary",
  "filing_ids": ["所选公司已有申报的ID"],
  "config_id": "当前已通过连接测试的模型配置ID",
  "language": "zh-CN",
  "force": false
}
```

task_type支持summary、comparison、event、question；summary/comparison只接受10-K/10-Q及其修订，event只接受8-K及其修订。comparison必须选择两份不同申报，其他类型选择一份；所有申报必须属于所选CIK。question类型要求非空question且不超过500字符；其他类型不得夹带question。language支持zh-CN/en-US。所选申报必须已有可检索正文；缺少正文或证据时明确返回错误，不在付费研究请求中暗中下载、换数据或调用Fake。

提交时冻结所选片段及来源、指定accession的可用结构化指标、文档哈希/解析版本、prompt/schema版本和语言。相同owner、冻结上下文、配置版本及部署binding可以复用在途任务或成功结果；force=true绕过成功缓存，但不重复执行同一在途任务。队列只传job_id。配置在执行前后重新验证，发布结果的事务锁定配置及任务行。

JobOut包含id、job_id、cik、task_type、status、error_code、error_message、report_id、created_at、started_at、finished_at。status为queued/running/succeeded/failed/cancelled。取消queued/running任务会阻止成功报告发布，已完成任务保持终态；上游调用可能已经执行，因此不保证取消其费用。只有failed/cancelled可以retry；重试保留原input_json和父任务ID，要求原模型配置版本/binding仍有效，不会悄悄换模型或采用新检索资料。

ReportOut包含id、job_id、cik、task_type、title、language、created_at、provider、model_id、source_kind、content、usage、duration_ms。content保存claims、citations、facts、filings、uncertainties、insufficient_evidence、limitations与validation等。claim区分disclosed_fact和inference：原文披露项要求与每个所引excerpt连续匹配，metric-only披露项由后端格式化；模型推论只验证其引用存在，不把推论标成已证明。模型不能提交或更改source_url/excerpt/hash。reported_values必须逐项对应metric_refs，精确值字符串和单位完全一致；null事实不能以0补齐。insufficient_evidence可携带空claims，明确表示证据不足。

`validation`分别记录citation_ids=context_bound、copied_fact_values=verified、extractive_claims=substring_matched和semantic_support=not_automatically_verified。摘录匹配只能确定文本存在，不能证明原文语境完整、问题相关性或模型结论正确。Markdown使用后台冻结证据，转义不可信HTML/Markdown，只链接通过accession路径校验的SEC来源。

正文错误包括DOCUMENT_NOT_READY、DOCUMENT_URL_REJECTED、DOCUMENT_TYPE_UNSUPPORTED、DOCUMENT_MANIFEST_INVALID、SOURCE_TOO_LARGE；研究另有RESEARCH_FILINGS_INVALID、EVIDENCE_NOT_FOUND、EVIDENCE_VALIDATION_FAILED、RESEARCH_NOT_RETRYABLE、CONFIG_CHANGED、CONTEXT_TOO_LARGE和QUEUE_UNAVAILABLE。错误正文不包含API Key、上游原始响应或待分析全文。真实模型与直接SEC的在线验证缺口见TEST_RESULTS.md。
## A 股扩展（2026-11-04）

美股既有端点保持不变。A股统一使用`/api/v1/cn`，证券标识为`600519.SH`、`000001.SZ`、`920000.BJ`，不能以六位代码调用SEC CIK端点。`SH/SZ/BJ`是交易所标识，`market=CN`、币种为`CNY`。

| 方法/路径（相对`/api/v1/cn`） | 行为与参数 | 权限 |
|---|---|---|
| `GET /status` | 模式、目录数量/时间、提供方和限制；不执行联网同步 | 公开读取 |
| `GET /companies/search` | `q`中文名/六位代码/完整symbol，`exchange=SH|SZ|BJ`可选，最多20项 | 公开读取 |
| `GET /companies/{symbol}` | 独立证券身份、目录和最后财务同步时间、陈旧状态 | 公开读取 |
| `GET /companies/{symbol}/metrics` | `frequency=annual|cumulative`、`years=1..10`；精确字符串、同比、操作数和来源 | 公开读取 |
| `GET /companies/{symbol}/filings` | 已保存公告分页，`page>=1`、`page_size=1..100`；不是上游历史总量 | 公开读取 |
| `GET /sources/{id}` | 原响应SHA-256、首抓取/最后观察时间、URL、参数/分页元数据，不返回raw内容 | 公开读取 |
| `POST /directory/sync` | 提交目录任务，空JSON即可 | `X-Admin-Token` |
| `POST /companies/{symbol}/sync` | 提交完整财务和财报公告同步；必须先有目录身份 | `X-Admin-Token` |
| `GET /sync/jobs/{id}` | owner任务状态、错误、结果；终态失败不覆盖旧财务 | `X-Admin-Token` |

提交返回202及`{job_id,status,reused}`。生产模式复用RQ worker，Redis只传任务ID；相同scope在途任务复用，超时可释放后另建，队列失败持久化错误。`cn_snapshot`或测试模式不能通过同步端点外联。

指标响应为`{items,source_kind,frequency,currency:'CNY',selection_policy:'latest_provider_snapshot',limitations}`。`items`沿用Metric字段，原始值/同比均为十进制字符串；累计为`period_type=cumulative`，不是quarterly。不支持的`as_of`、`accession`及其他selection_policy明确422，不能用当前供应商重述资料冒充历史当时信息。

财务18项定义、归母与报表净利、营业总收入与营业收入、缺失和毛利保护见[A股说明](A_SHARE_SUPPORT.md)。每个来源事实绑定准确分页快照ID/URL/hash及字段；派生值记录全部操作数。公告返回`title/form_type/filing_date/report_date/source_url/source_snapshot_id`，并可带原始`source_security_code/source_org_id`；PDF尚未在应用中解析。前端只打开固定巨潮来源链接。

主要错误：`CN_COMPANY_NOT_FOUND`、`CN_DATA_MODE_MISMATCH`、`CN_HISTORY_UNSUPPORTED`、`CN_FINANCIAL_DATA_INCOMPLETE`、`CN_SOURCE_MANIFEST_INVALID`、`CN_RATE_LIMITED`、`CN_NETWORK_ERROR`、`CN_SOURCE_COMPANY_MISMATCH`、`CN_COVERAGE_LIMIT`。错误信封、request_id及管理员身份约束继续沿用既有协议。

