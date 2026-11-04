# Contributing to FinSight AI

欢迎提交可复现的问题和范围明确的改进。开始较大的功能前，先通过 Issue 说明问题、预期行为和方案，避免重复工作。

## 报告问题

请提供版本或提交号、运行系统、复现步骤、预期/实际结果及已脱敏的日志。标明数据来自 SEC 在线、历史归档、CN 在线或隔离测试；不要上传 `.env`、API Key、访问令牌、个人路径、真实数据库、研究历史或未确认授权的财报文件。

安全问题请按 [SECURITY.md](SECURITY.md) 私下报告，不在公开 Issue 中粘贴漏洞细节或凭据。

## 提交修改

1. Fork 仓库并创建功能分支，保持每个 PR 聚焦一个问题。
2. 按 [运行指南](docs/DEPLOYMENT.md) 配置独立开发环境，使用项目锁文件。
3. 对有行为变化的计算、来源、权限和异步任务增加必要测试；修改文档时核对链接和实际能力。
4. 说明改了什么、为什么改、如何验证及剩余限制，保留第三方来源和许可说明。

常用验证命令：

```sh
# 项目根目录
python scripts/check_public_files.py
python -m unittest discover -s scripts/tests

# backend 目录
python -m ruff check .
python -m pytest -q

# frontend 目录
pnpm typecheck
pnpm lint
pnpm test
pnpm build
pnpm test:e2e:check
pnpm test:e2e
```

完整集成测试需要独立 PostgreSQL/Redis，浏览器测试需要 Playwright 运行环境，详见[测试计划](docs/TEST_PLAN.md)和[E2E说明](frontend/e2e/README.md)。测试会清理专用测试库，不能指向真实资料库或生产队列。依赖缺失导致的跳过和未执行检查应如实说明。

## 数据与 AI 改动

- 财务计算保持 Decimal 精度和期间/口径约束，界面不重新计算金融指标。
- 不用虚构财务、合成正文或测试模型响应填补生产缺失。夹具必须明确 `TEST ONLY` 并与真实资料隔离。
- 保留原始来源、哈希、版本和引用页码；不得把引用匹配描述为已证明模型推论正确。
- 不绕过上游限流、验证码或访问控制，不开放任意 URL 抓取。

提交内容适用本项目的 [MIT License](LICENSE)，第三方材料保留各自许可与归属，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
