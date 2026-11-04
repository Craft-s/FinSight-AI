# 数据字典与可信性约束

| 表 | 责任 | 关键约束 |
|---|---|---|
| dataset_metadata | 数据库绑定的数据模式 | 单例；禁止live与fixture混用 |
| companies | SEC申报主体 | market+10位CIK唯一，ticker可变 |
| ticker_aliases | 当前及历史股票代码 | company+ticker唯一，保留有效时间 |
| source_snapshots | 原始JSON和来源元数据 | 原文哈希、独立采集日期、SEC原URL与归档出处 |
| filings | 申报元数据 | accession唯一；修订表单单独标记；原文文件名来自SEC |
| financial_facts | 原始财务事实 | 指纹去重，保留concept/unit/start/end/filed/accn/raw API行 |
| normalized_metrics | 计算后展示视图 | 按公司、指标、明确期间和规则版本唯一；provenance保留输入 |
| sync_jobs | 同步执行状态 | 在途CIK唯一；失败后清除活动键并保留错误 |
| llm_configs | owner绑定的加密模型配置 | owner+provider唯一；版本、verified_version和部署binding一致才可调用；读取不返回密钥 |
| overview_jobs | 第三阶段指标概览任务与冻结结果 | owner、配置版本/binding、事实与语言确定缓存；队列仅传ID |
| documents | 第四阶段不可变正文/附件版本 | filing+source_url+sha256+parser_version唯一；保留原字节、来源模式与采集时间 |
| document_chunks | 章节/段落/表格片段 | document+chunk_index唯一；正文哈希、位置对象与所属申报可追溯；英文FTS索引 |
| research_jobs | 文档准备及四类研究的持久任务 | owner、配置版本/binding、输入快照、fingerprint、唯一active_key与终态；保留父重试ID |
| research_reports | 成功报告与导出内容 | job_id唯一；owner授权读取；冻结JSON证据、Markdown、模型与实际用量 |

事实缺失为null，不填零。USD、USD/shares、shares、percent、ratio各有独立语义。资产负债表是时点；收入/利润/现金流是期间；EPS不可通用差分。比率与同比必须在分母和口径检查通过后计算。

`raw_context_json`是companyfacts API行，不是完整XBRL实例的维度context；标准taxonomy缺失不能通过猜测标签补齐。历史事实不因最新物化视图更新而删除。

## 正文版本与定位（2026-11-03）

documents主要字段为id、filing_id、source_url、sha256、source_kind、fetched_at、created_at、title、role、parser_version、raw_html、raw_bytes、source_metadata。sha256针对**原始字节raw_bytes**计算，raw_html仅为解析/查看用的文本表示，不能通过重新编码raw_html替代原字节哈希。role为primary或exhibit，后者限已确认的EX-99.1 HTML附件；source_metadata记录归档地址/commit、许可证及附件覆盖状态等。fetched_at必须有时区，表示实际获取当前来源的时间，不等同于filing_date或未知的原SEC抓取时间。

document_chunks包含id、document_id、filing_id、section、chunk_index、content、content_sha256、location与created_at。location为JSON对象，包含block_index、tag、anchor、part_index；表格还记录table_index和row_index。该定位针对保存版本的解析结果，不是浏览器像素坐标。content含表格标题/列上下文时，其内容哈希也包含这些前缀。PostgreSQL使用`to_tsvector('english', content)`的GIN表达式索引，检索结果显示postgres_fts；没有将向量特征存成已实现字段。

报告引用包含citation_id、filing_id、accession、form_type、filing_date、section、source_url、excerpt、chunk_id、document_sha256、location，并可保留document_id、role、source_metadata等后台字段。citation_id仅在本次冻结上下文内有意义；模型不得提供或覆盖来源、摘录或哈希。文档hash标识后台保存版本，不代表仅凭一个摘录即可独立重算完整文档哈希。

## 研究快照与验证边界

research_jobs的task_type为summary/comparison/event/question或内部document_sync；status为queued/running/succeeded/failed/cancelled。input_json冻结filings、citations、facts、document_versions、source_kind、retrieval_mode、question、language、limitations、prompt_version及schema_version。config_id/version/binding记录当时的验证配置；指纹另外绑定owner，不能跨用户、资料版本或模型配置复用。config_id与report_id等历史标识不作为允许绕过当前权限或调用已删除配置的依据。

research_reports的content保存claims、引用和事实快照、未知项、证据不足标记及方法限制。每个claim包含kind、text、citation_ids、metric_refs、reported_values、evidence_check及semantic_support。disclosed_fact引用原文时要求连续摘录匹配；metric-only事实文本由backend生成。inference只确认引用身份，不能将其evidence_check=references_only解释为语义已证明。`validation.copied_fact_values`仅校验精确复制值与单位，`extractive_claims`只说明摘录匹配，semantic_support始终明确未自动验证。

金额保持十进制字符串；N/A仍是null来源的展示，不由模型估算。报告还保存provider、model_id、language、source_kind、usage、duration_ms和创建时间，Markdown是对同一冻结结果的安全导出。retry创建新任务并保留parent_job_id，不能覆盖原报告；取消不证明上游请求已停止。

真实HTML历史归档仅导入sec_snapshot专用数据库；synthetic_fixture测试HTML/模型响应只能进入隔离测试库。正文版本、JSON快照与研究任务来源模式必须一致，不能通过页面标签弥补后端混用。
## A 股独立数据域（f51a01）

| 表 | 关键字段 | 含义 |
|---|---|---|
| `cn_dataset_metadata` | singleton、source_kind | 绑定cn_live/cn_snapshot/synthetic_fixture，不改SEC绑定 |
| `cn_companies` | symbol、code、exchange、org_id、source_metadata、last_synced_at | 沪深京证券身份；source_metadata保存目录source IDs和current_source_ids分页manifest |
| `cn_source_snapshots` | scope、kind、sha256、raw_json、fetched_at、last_observed_at、metadata | 每响应页不可变原字节/版本，重复字节复用ID，仅更新观察时间 |
| `cn_sync_jobs` | scope、owner_id、active_key、status、attempts、error、result | 目录/公司异步同步，active_key去重，事务锁保护发布 |

symbol使用六位代码加.SH/.SZ/.BJ后缀，不能当作CIK。org_id来自巨潮目录，北交所历史代码只在同一orgId确认后关联；公告保留原始代码。目录可能包含历史证券，不代表当前仍上市或实时行情。

raw_json保留原UTF-8文本，重新编码可核对SHA-256（包括原有BOM）；JSON数值解码为Decimal。来源manifest按kind列出全部页ID，同批事务发布，避免只读末页或混用任务。直接值记录来源ID/字段/hash/日期，派生值保留操作数。statement_scope标注供应商报表范围尚未逐份独立核对。

CN指标币种CNY，EPS单位CNY/shares；年度与累计频率独立于SEC季度规则。18项字段及算法见[A股财务口径](A_SHARE_SUPPORT.md)，不套用US GAAP等价映射或自动历史回测口径。

