# A 股 PDF 正文与研究

A 股正文使用独立证券标识、公告身份和文档版本。解析文本型 PDF 后，可按关键词检索页码证据，并使用已验证的模型配置创建总结、两期对比或问答。模型在线推理是否通过与代码/协议测试分别记录。

## 使用流程

1. 升级前备份数据库，执行 `python scripts/manage.py migrate` 至 `h52a02`，安装更新后的锁定依赖并重启 API/worker。
2. 选择 A 股公司，在“财报与公告”或“财报正文与 AI 研究”中选择报告。准备新正文需要在设置页输入管理员令牌，读取已保存正文和证据不需要模型 Key。
3. 点击“准备 PDF 正文”，API 返回持久化任务 ID，RQ worker 下载、隔离解析并原子保存。失败时可重新提交，已有版本保留。
4. 输入“营业收入”“经营现金流”“风险”等关键词或问题。证据显示公告、证券、文档哈希及 PDF 页码，原文链接可跳到对应页。
5. 配置可用模型并通过连接测试后，选择一份报告做总结或问答，选择两份不同定期报告做对比。研究历史、取消、重试及 Markdown 下载沿用单用户权限边界。

当前支持 `summary`、`comparison`、`question`，没有把任意公告归类为定期报告，也没有将 A 股证券代码传给 SEC CIK 接口。A 股事件研究仍留后续。

## 解析与证据规则

- 仅从已保存并核对证券/orgId 的巨潮公告索引取 URL，固定为 `https://static.cninfo.com.cn/finalpage/YYYY-MM-DD/数字.PDF`；不接受用户任意 URL、文件上传或本地路径。
- PDF 原始字节、SHA-256、公告来源快照、下载时间与解析器版本独立保存。同一文件经新解析器处理形成新版本，旧正文及冻结研究输入不被覆盖。
- 文本按页提取，分块保留页码、文本偏移、章节和片段哈希。PDF 文件页序号从 1 开始，可能与财报自行印刷的页码不同。
- 检索是有界的中英文关键词匹配，不是向量或语义检索。一般问题从常见财务词中提取检索词；无匹配返回空结果，研究用章节回退时显式标识。
- 文本型表格保留提取后的文本布局，不自动认证单元格结构、列对齐、阅读顺序或金额含义。财务计算仍以结构化来源及其口径为准。
- 无文本的扫描件返回 `CN_PDF_OCR_REQUIRED`；加密、损坏、超页数/字节/文本预算及解析超时分别报告。部分无文字页列在 `empty_or_image_pages`；每页有文字也不等于内容完整或提取质量已获认证。

## 安全与资源预算

PDF 获取复用 Redis 的共享限速/冷却，禁止重定向及环境代理，拒绝压缩传输、异常 Content-Type、超长响应和非 PDF 内容。解析放在短生命周期子进程中，设墙钟超时与 pypdf 过滤器/对象解析预算；Linux 另设地址空间及 CPU 上限。不执行 PDF 的 JavaScript、附件、超链接或外部解码程序。

| 配置 | 默认值 |
|---|---:|
| `CN_PDF_MAX_BYTES` | 20,000,000 |
| `CN_PDF_MAX_PAGES` | 600 |
| `CN_PDF_TIMEOUT_SECONDS` | 30 |
| `CN_PDF_PARSE_TIMEOUT_SECONDS` | 90 |
| `CN_PDF_MAX_CHARS` | 2,000,000 |
| `CN_PDF_MAX_CHUNKS` | 4,000 |

默认的每流解压预算为 8 MB；Linux 解析进程地址空间为 1.5 GB。Windows 保留子进程墙钟和解析器解码预算，但没有声称实现相同的操作系统内存限制。

解析库使用 [pypdf 6.21.4](https://pypi.org/project/pypdf/6.21.4/)（BSD-3-Clause），资源与文本提取边界参考[官方安全说明](https://pypdf.readthedocs.io/en/stable/user/security.html)和[文本提取说明](https://pypdf.readthedocs.io/en/stable/user/extract-text.html)。

## AI 研究边界

CN 研究冻结已选公告、PDF 版本、页码、片段、模型配置与提示/schema 版本。重试保留原证据；过期上下文、模型配置变化、取消或任务失效会阻止结果发布。Redis 仅传任务 ID，不传凭据或原始文档。

结构化财务的最新供应商快照并非所选 PDF 的逐项提取事实，因此 CN 研究上下文的 `facts` 保持为空，不用它补齐原文数据。模型只收到所选 PDF 摘录；引用身份、连续摘录及页范围受校验，模型推论仍须人工判断。PDF 中的文字和问题都视为不可信数据，不作为工具或系统指令执行。

测试中的 PDF 和模型响应明确标记 `TEST ONLY`，只在隔离数据库或浏览器拦截中使用。没有真实模型服务时不生成生产示例报告，也不把协议替身称为实际模型。

## 接口与存储

所有路由在 `/api/v1/cn` 下：

| 方法与路径 | 权限与用途 |
|---|---|
| GET `/companies/{symbol}/filings/{filing_id}/documents` | 已保存文档元数据，不返回原始二进制 |
| POST 同上 `/sync` | 管理员；准备 PDF 的异步任务 |
| GET `/documents/jobs/{id}` | 管理员/owner；任务状态与结果 |
| GET `/companies/{symbol}/evidence?q=&filing_id=` | 指定公告或最近十份已准备正文的关键词检索 |
| POST `/research/jobs` | 管理员；提交 `symbol/task_type/filing_ids/config_id/language`，问答另需 `question` |
| GET `/research/jobs/{id}`；POST `/cancel`、`/retry` | owner；研究状态、取消与重试 |
| GET `/research/history` | owner；A 股研究历史 |
| GET `/research/reports/{id}`、`/download?format=md` | owner；报告与 Markdown |

新增 `cn_documents`、`cn_document_chunks`、`cn_document_jobs`、`cn_research_jobs` 和 `cn_research_reports` 五表，复用原 PostgreSQL、Redis/RQ 和模型配置服务，无新增微服务、OCR 或向量数据库。API 详细错误与实测结果见[接口](API.md)和[测试记录](TEST_RESULTS.md)。
