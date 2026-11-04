# 文档获取与检索边界

`DocumentClient`仅处理本地已选申报的官方accession目录，继承SecClient共享Redis限速与冷却状态。必须有真实联系邮箱、TLS官方域名、指定目录和HTML类型，拒绝跨目录、任意URL、重定向和环境代理；大小有上界。8-K附件需同时由目录index.json列出且由申报索引标记EX-99.1；仅支持最多两份HTML附件，缺失或失败记录在primary.source_metadata，不冒充完整事件资料。

原始字节`Document.raw_bytes`是SHA-256与重放依据；`raw_html`仅是UTF8尽力解码的便览副本，不用于哈希。BeautifulSoup/lxml清理脚本、隐藏Inline XBRL与导航，保留可见段落、章节、表格列标题/行及DOM位置。版本按filing、URL、原文hash、parser版本去重，旧版本与chunks不覆盖。

正式检索采用PostgreSQL英文FTS（GIN表达式索引）和小型中英金融术语表。未知中文词无匹配时返回空列表，不把低召回说成财报没有风险。空查询可显式返回section_fallback；SQLite关键词分支仅允许DEVELOPMENT=true且DATA_MODE=synthetic_fixture，并标记test_keyword。未下载embedding/向量模型，没有隐藏依赖或新服务。

核查资料：[SEC Fair Access与User-Agent](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)、[PostgreSQL全文解析/查询/排序](https://www.postgresql.org/docs/current/textsearch-controls.html)、[BeautifulSoup文档](https://www.crummy.com/software/BeautifulSoup/bs4/doc/)。当前真实HTML归档及来源见`data/documents/README.md`，测试夹具与真实归档严格隔离。
