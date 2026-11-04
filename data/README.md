# SEC 历史归档快照

`snapshots/` 保存 AAPL、MSFT、NVDA 的真实历史 SEC JSON 响应正文，供离线演示与集成测试使用。

这些文件来自公开项目 [dgunning/edgartools](https://github.com/dgunning/edgartools) 的 VCR HTTP 记录，固定到 commit `3931b22077eee1e0d2b77a8c5a61ab415d51ae0b`。FinSight AI 从 GitHub 归档提取 JSON，**没有将本次下载描述成直接从 SEC 获取，也没有修改金额、日期或申报列表**。

- 数据模式：`sec_snapshot`，历史快照；不是实时 SEC 同步。
- 原始正文保持原样，每份文件的 SHA-256、原 SEC URL、原 HTTP Date、镜像路径与 commit 均在公司目录的 `manifest.json` 中。
- `downloaded_at` 是本次下载镜像的时间；`sources.*.fetched_at` 是录制的 SEC HTTP 响应日期。
- 同一公司的 submissions 和 companyfacts 可能采集于不同日期。顶层 `fetched_at` 保守取两者较早时间，只是快照包时间，不代表所有事实当时均已披露；具体事实必须看自己的 `filed` 和对应来源日期。
- 归档完整性哈希能验证文件与固定版本一致，不能替代对第三方录制真实性的独立认证。在线正式运行仍应使用部署者提供的合规 SEC User-Agent 重新同步。
- 与生产 `sec_live` 数据库隔离；禁止在在线失败时静默切换到这些快照。
- 财务金额构造测试位于 `backend/tests/fixtures/synthetic/`，不会混入本目录。

重新获取同一组归档：

```powershell
.\.venv\Scripts\python.exe scripts/fetch_archived_snapshots.py
```

脚本需要 PyYAML，仅访问固定 GitHub archive URL，不访问 SEC。读取的 YAML 使用 SafeLoader，写出的 JSON 是原始响应正文，没有重新生成财务数据。
