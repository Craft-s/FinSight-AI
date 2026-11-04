# 项目展示与简历素材

以下描述对应已实现代码和可追溯资料。投递前用[测试记录](TEST_RESULTS.md)核对最新验收状态；没有实际在线运行的部分不写成“已上线/验证成功”。

## 中文项目描述

**FinSight AI｜可追溯财务与 AI 研究工作台**  
Vue 3、TypeScript、FastAPI、PostgreSQL、Redis/RQ、SQLAlchemy/Alembic、ECharts、Docker Compose

- 开发面向 SEC 申报主体的全栈研究应用，实现公司搜索、年度/季度指标、历史财务口径、申报/事件列表、中英界面和逐项来源追溯。
- 使用 Decimal、实际财年期间及兼容披露版本计算同比、利润率和 YTD/Q4 差分，对 EPS、非可加指标、缺失和不可比资料进行显式保护。
- 实现四种模型 adapter、加密配置与实际连接测试接口，以 RQ 执行异步研究；冻结资料、文档哈希和配置版本，支持幂等、取消、重试、私有历史及 Markdown 导出。
- 构建 PostgreSQL 全文证据检索和结构化输出校验，区分披露摘录、精确事实、推论与未知；引用匹配不冒充语义正确性验证。使用真实历史归档和隔离协议测试验证不同层次的行为。

可根据岗位精简为 2–3 条。最后一条的“验证”仅指实际测试记录覆盖的层次，不代表真实云模型、Ollama 或 SEC 在线验收已完成。

## English project description

**FinSight AI — Traceable financial research workbench**  
Vue 3, TypeScript, FastAPI, PostgreSQL, Redis/RQ, SQLAlchemy/Alembic, ECharts, Docker Compose

- Built a full-stack SEC filing research application with company search, annual and quarterly metrics, historical disclosure policies, filing/event lists, bilingual UI, and metric-level provenance.
- Implemented deterministic financial calculations with Decimal arithmetic, actual fiscal periods, compatible disclosure versions, and explicit handling of missing values and non-additive metrics.
- Developed four LLM adapters and encrypted, versioned configurations; used RQ jobs with frozen evidence, idempotency, cancellation, retry, private history, and Markdown export.
- Added PostgreSQL full-text retrieval and validation of citation identity, exact copied values, and extractive quotations while keeping semantic support explicitly unverified. Separated real archived filings from synthetic protocol tests.

## 面试时可展开的工程取舍

| 话题 | 可以展示的具体证据 |
|---|---|
| 金融精度与前端展示 | NUMERIC/Decimal → API 字符串 → 原精度千位分隔；图表单独换算单位；来源详情可解释操作数 |
| 历史可重现性 | 资料模式隔离、不可变正文版本、原始字节 SHA-256、指定 accession 的事实与冻结研究输入 |
| AI 应用可信边界 | 严格 schema、引用身份/值/摘录校验，说明为什么这些校验仍不能证明模型推论正确 |
| 并发与异步 | 数据库唯一活动键、RQ 仅传 ID、配置版本/binding、发布事务锁顺序、取消与重试边界 |
| 模块化单体 | 沿用 PostgreSQL FTS 与 Redis/RQ，无强制向量模型或额外服务；缺 Key 仍能展示核心产品 |
| 安全与可运行性 | 管理员鉴权、固定目的地址、Key 加密/脱敏、模式绑定、实际故障记录与本地恢复脚本 |

## 不能使用的表述

当前不写“实时美股金融数据平台”“已部署公网多租户 SaaS”“四家真实模型均验收通过”“AI 结论经过自动事实证明”或没有实际依据的准确率、QPS、降本/提升百分比。CI 配置存在不等于远程 GitHub Actions 已成功运行；Compose 配置或独立 Nginx 验证不等于完整容器部署通过。

演示使用三家公司真实 SEC 历史 JSON 归档及三份真实 HTML，来源为固定版本的公开第三方归档，不能称为本次 SEC 直连；合成测试响应不作为生产研究报告。未提供服务器/域名和真实模型服务，因此公网部署与在线推理继续列为验收缺口。

对外发布前核查源码包不含 `.env`、Key、令牌或数据库备份，保留第三方数据来源与许可证。仓库地址、演示地址、测试数字和个人职责只填写可验证的实际信息，不使用占位内容伪装成已完成的成果。
