import { expect, test, type Page } from '@playwright/test'

const disposableToken = 'finsight-e2e-disposable-only-token'
const apple = '/company/0000320193'
const accession = '0000320193-24-000123'
const documentHash = 'ba4222c4fbd8ddfbd63982bcef63ffefba232bf079a89d628418be5f10935af0'
const exact = (value: string) => value.replace(/\B(?=(\d{3})+(?!\d))/g, ',')

async function authorize(page: Page) {
  await page.goto('/settings')
  await page.getByLabel('本地管理员访问令牌', { exact: true }).fill(disposableToken)
  await page.getByRole('button', { name: '在本次会话中使用', exact: true }).click()
  await expect(page.getByText('已存入会话，操作时验证权限', { exact: true })).toBeVisible()
}

test.beforeEach(async ({ request }) => {
  // Fail closed if an operator accidentally points these tests at the real app.
  const response = await request.get('/api/v1/_e2e/status')
  expect(response.ok(), 'Only the isolated e2e/serve_api.py server may be used').toBeTruthy()
  expect(await response.json()).toMatchObject({ harness: 'finsight-isolated-e2e', source_kind: 'sec_snapshot' })
})

test('real archived company search, exact numbers, provenance and quarterly periods', async ({ page, request }) => {
  await page.goto('/')
  await page.getByRole('textbox', { name: '搜索公司、股票代码或 CIK' }).fill('AAPL')
  await page.locator('.company-card').filter({ hasText: 'Apple Inc.' }).click()
  await expect(page).toHaveURL(new RegExp(`${apple}$`))
  await expect(page.getByRole('heading', { name: '年度财务表现', exact: true })).toBeVisible()
  await expect(page.locator('.data-notice')).toContainText('历史归档 · 不代表最新披露')
  const row = page.locator('.metrics-table-panel .el-table__body tr').filter({ hasText: '营业收入' })
  await expect(row.locator('.number-cell').first()).toHaveText('416,161,000,000')
  await expect(row.locator('.number-cell').first()).toHaveAttribute('title', '416161000000')
  const chart = page.locator('.trend-chart')
  await chart.scrollIntoViewIfNeeded()
  const chartBox = await chart.boundingBox()
  expect(chartBox?.width).toBeGreaterThan(180)
  // Axis-triggered tooltip near the final period; no chart internals are read.
  await chart.hover({ position: { x: chartBox!.width - 48, y: 120 } })
  await expect(chart.locator('.chart-tooltip__period')).toHaveText('FY2025')
  await expect(chart.locator('.chart-tooltip__value')).toHaveText('416,161,000,000')
  await expect(chart.locator('.chart-tooltip')).toContainText('2025-09-27')
  await row.getByRole('button', { name: '查看来源 ↗' }).click()
  const drawer = page.getByRole('dialog', { name: '指标来源与计算口径' })
  await expect(drawer).toContainText('416,161,000,000')
  await expect(drawer).toContainText('2025-09-27')
  await expect(drawer.getByRole('link', { name: /打开 SEC 来源/ }).first()).toHaveAttribute('href', /^https:\/\/www\.sec\.gov\/Archives\/edgar\/data\//)
  await page.keyboard.press('Escape')
  await expect(drawer).toBeHidden()
  await page.getByRole('button', { name: '季度', exact: true }).click()
  await expect(page.getByRole('heading', { name: '单季度财务表现', exact: true })).toBeVisible()
  await expect(page.locator('.period-controls')).toContainText(/FY\d{4} Q[1-4]/)
  const response = await request.get('/api/v1/companies/0000320193/metrics?frequency=quarterly&years=5')
  expect(response.ok()).toBeTruthy()
  const metrics = (await response.json()).items as { metric_key: string; period_end: string; value: string | null }[]
  const latestRevenue = metrics.filter(item => item.metric_key === 'revenue').sort((a, b) => b.period_end.localeCompare(a.period_end))[0]
  expect(typeof latestRevenue?.value).toBe('string')
  await expect(row.locator('.number-cell').first()).toHaveText(exact(latestRevenue.value!))
})

test('filings and events retain original SEC links and research selection', async ({ page }) => {
  await page.goto(apple)
  await page.getByRole('tab', { name: '财报列表', exact: true }).click()
  const filings = page.getByRole('tabpanel', { name: '财报列表', exact: true })
  await expect(filings.getByRole('link', { name: 'SEC 原文 ↗' }).first()).toHaveAttribute('href', /^https:\/\/www\.sec\.gov\/Archives\/edgar\/data\//)
  await filings.getByRole('button', { name: '分析此份财报 ↗' }).first().click()
  await expect(page.getByRole('tab', { name: 'AI 研究', exact: true })).toHaveAttribute('aria-selected', 'true')
  await expect(page.locator('.research-form').first()).toContainText('AI 调用需要会话授权')
  await expect(page.getByRole('button', { name: '创建研究任务', exact: true })).toBeDisabled()
  await page.getByRole('tab', { name: '金融事件', exact: true }).click()
  const events = page.getByRole('tabpanel', { name: '金融事件', exact: true })
  await expect(events.getByRole('heading', { name: '8-K 事件披露', exact: true })).toBeVisible()
  await expect(events.getByRole('link', { name: 'SEC 原文 ↗' }).first()).toHaveAttribute('href', /^https:\/\/www\.sec\.gov\/Archives\/edgar\/data\//)
  await events.getByRole('button', { name: '解读事件 ↗' }).first().click()
  await expect(page.locator('.research-workbench')).toContainText('事件解读')
  await expect(page.getByRole('button', { name: '创建研究任务', exact: true })).toBeDisabled()
})

test('language menu persists while authorization stays in session memory', async ({ page }) => {
  await authorize(page)
  // Element Plus' selected-label layer covers the readonly combobox input.
  // Click the visible control wrapper, as a pointer user does, without force.
  await page.locator('.language-preference .el-select__wrapper').click()
  await page.getByRole('option', { name: 'English', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Settings', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: 'Clear token', exact: true })).toHaveCount(0)
  const storage = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(storage.local['finsight.locale']).toBe('en-US')
  expect(JSON.stringify(storage)).not.toContain(disposableToken)
  await page.getByRole('link', { name: 'Research library', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Research history requires session authorization', exact: true })).toBeVisible()
  await page.goto('/settings/ai')
  await expect(page).toHaveURL(/\/settings$/)
})

test('no configured model blocks generation after valid workspace authorization', async ({ page }) => {
  await authorize(page)
  await page.getByRole('link', { name: '公司搜索 ↗', exact: true }).click()
  await page.locator('.company-card').filter({ hasText: 'Apple Inc.' }).click()
  await page.getByRole('tab', { name: 'AI 研究', exact: true }).click()
  await expect(page.locator('.research-workbench')).toContainText('没有已验证的模型配置。请先保存配置并通过真实连接测试。')
  await expect(page.getByRole('button', { name: '创建研究任务', exact: true })).toBeDisabled()
  await page.getByRole('link', { name: '研究记录', exact: true }).first().click()
  await expect(page.getByText('尚无研究任务。请从公司财报或事件列表开始。', { exact: true })).toBeVisible()
})

test('archived filing body retains hash and evidence search uses the supported database', async ({ page, request }) => {
  const status = await (await request.get('/api/v1/_e2e/status')).json()
  await page.goto(apple)
  await page.getByRole('tab', { name: 'AI 研究', exact: true }).click()
  await page.locator('.evidence-controls .el-select__wrapper').click()
  await page.getByRole('option', { name: new RegExp(accession) }).click()
  await expect(page.locator('.document-record')).toContainText(documentHash)
  await expect(page.locator('.document-record')).toContainText('SEC 历史快照')
  await page.getByRole('textbox', { name: '证据搜索', exact: true }).fill('风险')
  await page.getByRole('button', { name: '检索证据', exact: true }).click()
  if (status.database === 'postgresql') {
    const evidence = page.locator('.evidence-card').first()
    await expect(evidence).toBeVisible()
    await expect(evidence).toContainText(accession)
    await expect(evidence).toContainText(documentHash)
    await expect(evidence.locator('blockquote')).not.toBeEmpty()
    await expect(evidence.getByRole('link', { name: 'SEC 原文 ↗' })).toHaveAttribute('href', /000032019324000123\/aapl-20240928\.htm$/)
    await expect(page.locator('.evidence-browser')).toContainText('关键词全文检索')
  } else {
    // SQLite must not silently masquerade as the production PostgreSQL search.
    await expect(page.getByText('生产正文检索需要PostgreSQL全文搜索。', { exact: true })).toBeVisible()
    await expect(page.locator('.evidence-card')).toHaveCount(0)
  }
})

test('mobile archive layout remains usable', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto(apple)
  await expect(page.getByRole('heading', { name: '年度财务表现', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
  await page.getByRole('tab', { name: 'AI 研究', exact: true }).click()
  await expect(page.getByRole('heading', { name: '财报正文与证据检索', exact: true })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBeTruthy()
})

test('test-only metadata injection is rendered as text without DOM XSS or external requests', async ({ page }) => {
  // Temporary response interception only: no source file/database is modified.
  const injectedName = '<img src="https://xss.invalid/finsight" onerror="alert(1)"><script>alert(1)</script>'
  const dialogs: string[] = []
  const externalRequests: string[] = []
  page.on('dialog', async dialog => { dialogs.push(dialog.message()); await dialog.dismiss() })
  page.on('request', request => { if (request.url().includes('xss.invalid')) externalRequests.push(request.url()) })
  await page.route(/\/api\/v1\/companies\/0000320193$/, async route => {
    const response = await route.fetch()
    const company = await response.json()
    await route.fulfill({ response, json: { ...company, name: injectedName } })
  })
  await page.goto(apple)
  await expect(page.locator('.company-heading p')).toHaveText(injectedName)
  await expect(page.locator('img[src*="xss.invalid"]')).toHaveCount(0)
  await expect(page.locator('script').filter({ hasText: 'alert(1)' })).toHaveCount(0)
  expect(dialogs).toEqual([])
  expect(externalRequests).toEqual([])
})

test('theme controls persist across reloads and remain usable on mobile', async ({ page }) => {
  await page.goto(apple)
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  await page.getByRole('button', { name: '切换为深色主题', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.locator('html')).toHaveClass(/\bdark\b/)
  expect(await page.evaluate(() => localStorage.getItem('finsight.theme'))).toBe('dark')
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark')
  await expect(page.getByRole('heading', { name: '年度财务表现', exact: true })).toBeVisible()
  await expect(page.locator('.metrics-table-panel .el-table__body tr').filter({ hasText: '营业收入' }).locator('.number-cell').first()).toHaveText('416,161,000,000')
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: '切换为浅色主题', exact: true }).click()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()
  await page.reload()
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light')
  expect(await page.evaluate(() => localStorage.getItem('finsight.theme'))).toBe('light')
})

test('global search supports shortcut, initial ArrowUp, keyboard selection and Escape', async ({ page }) => {
  await page.goto('/settings')
  await page.keyboard.press('ControlOrMeta+k')
  const search = page.getByRole('combobox', { name: '全局公司搜索', exact: true })
  await expect(search).toBeFocused()
  await search.fill('a')
  const candidates = page.getByRole('listbox', { name: '搜索公司候选', exact: true }).getByRole('option')
  await expect.poll(() => candidates.count()).toBeGreaterThan(1)
  await search.press('ArrowUp')
  await expect(candidates.last()).toHaveAttribute('aria-selected', 'true')
  await search.press('ArrowDown')
  await expect(candidates.first()).toHaveAttribute('aria-selected', 'true')
  await search.fill('MSFT')
  const microsoft = candidates.filter({ hasText: 'CIK 0000789019' })
  await expect(microsoft).toBeVisible()
  await search.press('ArrowDown')
  await expect(microsoft).toHaveAttribute('aria-selected', 'true')
  await search.press('Enter')
  await expect(page).toHaveURL(/\/company\/0000789019$/)
  await expect(page.getByRole('heading', { name: 'MSFT', exact: true })).toBeVisible()
  await expect(search).toHaveValue('')
  await expect(search).toHaveAttribute('aria-expanded', 'false')
  await page.keyboard.press('ControlOrMeta+k')
  await search.fill('AAPL')
  await expect(candidates.filter({ hasText: 'CIK 0000320193' })).toBeVisible()
  await search.press('Escape')
  await expect(search).toHaveAttribute('aria-expanded', 'false')
  await expect(page.locator('.global-search-popover')).toBeHidden()
  await expect(page).toHaveURL(/\/company\/0000789019$/)
})

test('global search distinguishes no matches from a recoverable request failure', async ({ page }) => {
  let attempts = 0
  await page.route(/\/api\/v1\/companies\/search(?:\?|$)/, async route => {
    if (new URL(route.request().url()).searchParams.get('q') === 'MSFT' && ++attempts === 1) {
      // Test-only service failure. Successful retry uses the actual archive API.
      await route.fulfill({ status: 503, json: { error: { code: 'E2E_UNAVAILABLE', message: '测试专用：搜索服务暂不可用。' } } })
    } else await route.continue()
  })
  await page.goto(apple)
  const search = page.getByRole('combobox', { name: '全局公司搜索', exact: true })
  await search.fill('NO_MATCH_E2E_ONLY_987')
  const popover = page.locator('.global-search-popover')
  await expect(popover.getByText('没有匹配的公司', { exact: true })).toBeVisible()
  await expect(popover.getByRole('option')).toHaveCount(0)
  await search.fill('MSFT')
  const error = popover.getByRole('alert')
  await expect(error).toContainText('测试专用：搜索服务暂不可用。')
  await error.getByRole('button', { name: '重试', exact: true }).click()
  await expect(popover.getByRole('option').filter({ hasText: 'CIK 0000789019' })).toBeVisible()
  await expect(error).toHaveCount(0)
  expect(attempts).toBe(2)
  await page.getByRole('button', { name: '清除搜索', exact: true }).click()
  await expect(search).toHaveValue('')
  await expect(popover.getByText('输入名称、股票代码或 CIK', { exact: true })).toBeVisible()
})

test('company skeleton, keyboard skip link, error recovery and authorization toast are actionable', async ({ page }) => {
  let release: () => void = () => undefined
  const gate = new Promise<void>(resolve => { release = resolve })
  let attempts = 0
  const syncRequests: string[] = []
  page.on('request', request => {
    if (request.method() === 'POST' && /\/api\/v1\/companies\/0000320193\/sync$/.test(request.url())) syncRequests.push(request.url())
  })
  await page.route(/\/api\/v1\/companies\/0000320193$/, async route => {
    if (++attempts === 1) {
      // Hold the response long enough to inspect a real, transient loading state.
      await gate
      await route.fulfill({ status: 503, json: { error: { code: 'E2E_UNAVAILABLE', message: '测试专用：公司加载失败。' } } })
    } else await route.continue()
  })
  try {
    await page.goto(apple)
    await expect(page.locator('.loading-panel .el-skeleton')).toBeVisible()
    await page.keyboard.press('Tab')
    const skip = page.getByRole('link', { name: '跳至主要内容', exact: true })
    await expect(skip).toBeFocused()
    await skip.press('Enter')
    await expect(page.locator('#main-content')).toBeFocused()
    release()
    await expect(page.getByRole('heading', { name: '无法打开公司档案', exact: true })).toBeVisible()
    await expect(page.locator('.error-state')).toContainText('测试专用：公司加载失败。')
    await page.getByRole('button', { name: '重新加载', exact: true }).click()
    await expect(page.getByRole('heading', { name: '年度财务表现', exact: true })).toBeVisible()
    await expect(page.locator('.loading-panel')).toHaveCount(0)
    expect(attempts).toBe(2)
    await page.getByRole('button', { name: '重新载入快照', exact: true }).click()
    await expect(page.getByRole('alert').filter({ hasText: '请先在设置页输入本地管理员访问令牌。' })).toBeVisible()
    expect(syncRequests).toEqual([])
  } finally { release() }
})

test('TEST ONLY synthetic report separates results and evidence, escapes text and downloads with session authorization', async ({ page }, testInfo) => {
  // Browser-only response fixtures. They are never inserted into a database,
  // passed off as model output, or captured for production-report screenshots.
  const jobId = 'e2e-test-only-job'
  const reportId = 'e2e-test-only-report'
  const testCik = '0000000001'
  const citationId = 'CIT-TEST-ONLY'
  const factId = 'FACT-TEST-ONLY'
  const unsafeText = 'TEST ONLY quotation <img src="https://e2e-xss.invalid/report" onerror="alert(1)"><script>alert(1)</script>'
  const syntheticHash = '0'.repeat(64)
  const syntheticSource = 'https://www.sec.gov/Archives/edgar/data/1/000000000100000001/test-only-fixture.htm'
  const syntheticJob = {
    id: jobId, job_id: jobId, cik: testCik, task_type: 'question', status: 'succeeded',
    error_code: null, error_message: null, report_id: reportId,
    created_at: '2026-01-01T00:00:00Z', started_at: '2026-01-01T00:00:01Z', finished_at: '2026-01-01T00:00:02Z',
  }
  const syntheticReport = {
    id: reportId, job_id: jobId, cik: testCik, task_type: 'question',
    title: 'TEST ONLY · Synthetic research layout', language: 'zh-CN',
    created_at: '2026-01-01T00:00:02Z', provider: 'TEST ONLY provider', model_id: 'TEST ONLY model',
    source_kind: 'synthetic_fixture', usage: { input_tokens: 123, output_tokens: 45 }, duration_ms: 12,
    content: {
      claims: [
        { text: unsafeText, kind: 'disclosed_fact', citation_ids: [citationId], metric_refs: [factId], reported_values: [] },
        { text: 'TEST ONLY inference; this is not an actual model conclusion.', kind: 'inference', citation_ids: [citationId], metric_refs: [], reported_values: [] },
      ],
      facts: [{ id: factId, metric_key: 'test_only_metric', label: 'TEST ONLY synthetic amount',
        value: '9007199254740993.1200', unit: 'USD', period: 'TEST ONLY period',
        provenance: { source_kind: 'synthetic_fixture', note: 'TEST ONLY source; not actual company financials' } }],
      citations: [{ citation_id: citationId, filing_id: 'TEST-ONLY-FILING', accession: '0000000001-00-000001',
        form_type: '10-K', filing_date: '2026-01-01', section: 'TEST ONLY synthetic source',
        source_url: syntheticSource, excerpt: unsafeText, chunk_id: 'TEST-ONLY-PARAGRAPH',
        document_sha256: syntheticHash, location: { note: 'TEST ONLY paragraph location' } }],
      uncertainties: ['TEST ONLY unknown: this fixture cannot establish real financial findings.'],
      limitations: ['TEST ONLY UI fixture; no SEC retrieval or model inference was performed.'],
      insufficient_evidence: true, validation: { semantic_support: 'not_automatically_verified' },
      retrieval_mode: 'test_keyword',
      filings: [{ id: 'TEST-ONLY-FILING', accession: '0000000001-00-000001', form_type: '10-K', filing_date: '2026-01-01' }],
    },
  }
  const markdown = '# TEST ONLY synthetic report\n\nIsolated UI fixture, not actual company data or model inference.\n\nSynthetic amount: 9,007,199,254,740,993.1200 USD\n'
  const authorizedRequests: string[] = []
  const dialogs: string[] = []
  const externalRequests: string[] = []
  let reportRequests = 0
  let downloadRequests = 0
  page.on('dialog', async dialog => { dialogs.push(dialog.message()); await dialog.dismiss() })
  page.on('request', request => { if (request.url().includes('e2e-xss.invalid')) externalRequests.push(request.url()) })
  await page.route(/\/api\/v1\/research\/(?:history|jobs\/e2e-test-only-job|reports\/e2e-test-only-report)(?:\/download)?(?:\?|$)/, async route => {
    const url = new URL(route.request().url())
    const token = route.request().headers()['x-admin-token']
    if (token !== disposableToken) {
      await route.fulfill({ status: 401, json: { error: { code: 'AUTH_REQUIRED', message: 'TEST ONLY session authorization required.' } } })
      return
    }
    authorizedRequests.push(url.pathname)
    if (url.pathname.endsWith('/download')) {
      downloadRequests++
      expect(url.searchParams.get('format')).toBe('md')
      expect(url.searchParams.has('token')).toBe(false)
      await route.fulfill({ status: 200, contentType: 'text/markdown; charset=utf-8',
        headers: { 'Content-Disposition': 'attachment; filename="TEST_ONLY_fixture.md"' }, body: markdown })
    } else if (url.pathname.endsWith('/history')) {
      await route.fulfill({ json: { items: [syntheticJob], total: 1, page: 1, page_size: 20 } })
    } else if (url.pathname.includes('/jobs/')) await route.fulfill({ json: syntheticJob })
    else { reportRequests++; await route.fulfill({ json: syntheticReport }) }
  })

  await page.goto(`/research/reports/${reportId}`)
  await expect(page.getByRole('heading', { name: '研究记录需要会话授权', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '下载 Markdown', exact: true })).toHaveCount(0)
  expect(reportRequests).toBe(0)
  await authorize(page)
  await page.getByRole('link', { name: '研究记录', exact: true }).first().click()
  await page.getByRole('link', { name: '查看报告 ↗', exact: true }).click()
  await expect(page.getByRole('heading', { name: syntheticReport.title, exact: true })).toBeVisible()
  const report = page.locator('.research-report')
  await expect(report.locator('.source-badge')).toContainText('合成测试数据')
  await expect(report.locator('#report-facts')).toContainText(unsafeText)
  await expect(report.locator('#report-inferences')).toContainText('TEST ONLY inference')
  await expect(report.locator('#report-unknowns')).toContainText('TEST ONLY unknown')
  await expect(report.locator('.research-section-heading p')).toContainText('TEST ONLY provider · TEST ONLY model')
  await expect(report.getByRole('complementary', { name: '研究范围与方法', exact: true })).toContainText('测试关键词检索')
  await expect(report.locator('.research-fact .number-cell')).toContainText('9,007,199,254,740,993.1200')
  await expect(report.getByText('证据不足', { exact: true })).toBeVisible()
  const evidence = report.locator('.evidence-card')
  await expect(evidence.locator('blockquote')).toHaveText(unsafeText)
  await expect(evidence.locator('.source-hash')).toContainText(syntheticHash)
  await expect(evidence.getByRole('link', { name: 'SEC 原文 ↗', exact: true })).toHaveAttribute('href', syntheticSource)
  await evidence.locator('.evidence-verification > summary').click()
  await expect(evidence.locator('.evidence-meta')).toBeVisible()
  await expect(evidence.locator('.evidence-meta')).toContainText('TEST ONLY paragraph location')
  await expect(report.locator('img[src*="e2e-xss.invalid"], script')).toHaveCount(0)
  expect(dialogs).toEqual([])
  expect(externalRequests).toEqual([])
  // This review artifact stays in ignored test-results, never the production gallery.
  const reviewImage = testInfo.outputPath('TEST-ONLY-synthetic-report-layout.png')
  await page.getByRole('heading', { name: syntheticReport.title, exact: true }).scrollIntoViewIfNeeded()
  await page.screenshot({ path: reviewImage, fullPage: false, animations: 'disabled' })
  await testInfo.attach('TEST ONLY synthetic report layout; not a real model report', { path: reviewImage, contentType: 'image/png' })
  await page.setViewportSize({ width: 390, height: 844 })
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy()

  const downloadEvent = page.waitForEvent('download')
  await page.getByRole('button', { name: '下载 Markdown', exact: true }).click()
  const download = await downloadEvent
  expect(download.suggestedFilename()).toBe(`FinSight-${testCik}-${reportId}.md`)
  const stream = await download.createReadStream()
  if (!stream) throw new Error('TEST ONLY download did not produce a readable file')
  const chunks: Buffer[] = []
  for await (const chunk of stream) chunks.push(Buffer.from(chunk))
  expect(Buffer.concat(chunks).toString('utf8')).toBe(markdown)
  expect(downloadRequests).toBe(1)
  expect(reportRequests).toBe(1)
  expect(authorizedRequests).toContain(`/api/v1/research/reports/${reportId}/download`)
  await page.reload()
  await expect(page.getByRole('heading', { name: '研究记录需要会话授权', exact: true })).toBeVisible()
  await expect(page.getByRole('button', { name: '下载 Markdown', exact: true })).toHaveCount(0)
  expect(downloadRequests).toBe(1)
})

test('TEST ONLY model settings save a disposable key, list models and accept manual IDs without enabling an unverified configuration', async ({ page }) => {
  // All LLM endpoints are intercepted in this browser context. No real key,
  // encryption configuration, backend record or provider call is involved.
  const testKey = 'TEST-ONLY-NOT-A-REAL-KEY'
  const configId = 'TEST-ONLY-config'
  const saves: Record<string, unknown>[] = []
  const unexpectedOperations: string[] = []
  let modelLists = 0
  let saved: Record<string, unknown> | null = null
  await page.route(/\/api\/v1\/llm\//, async route => {
    const path = new URL(route.request().url()).pathname
    if (path === '/api/v1/llm/status') {
      await route.fulfill({ json: { available: false, encryption_ready: true, ollama_configured: false,
        providers: ['openai', 'deepseek', 'gemini', 'ollama'].map(id => ({
          id, label: `TEST ONLY ${id}`, mode: id === 'ollama' ? 'local' : 'cloud', configured: false, options: [],
        })) } })
      return
    }
    if (route.request().headers()['x-admin-token'] !== disposableToken) {
      await route.fulfill({ status: 401, json: { error: { code: 'AUTH_REQUIRED', message: 'TEST ONLY authorization required.' } } })
      return
    }
    if (path === '/api/v1/llm/configs' && route.request().method() === 'GET') {
      await route.fulfill({ json: { items: saved ? [saved] : [] } })
    } else if (path === '/api/v1/llm/configs' && route.request().method() === 'POST') {
      const body = route.request().postDataJSON() as Record<string, unknown>
      saves.push(body)
      saved = { id: configId, provider: body.provider, model_id: body.model_id, options: body.options,
        has_api_key: true, version: saves.length, verified_version: null, is_active: false,
        last_tested_at: null, is_verified: false, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:01Z' }
      await route.fulfill({ json: saved })
    } else if (path === `/api/v1/llm/configs/${configId}/models`) {
      modelLists++
      expect(route.request().postDataJSON()).toEqual({ refresh: true })
      await route.fulfill({ json: { cached: false, items: [
        { id: 'fake-model', display_name: 'TEST ONLY listed model', provider: 'openai', supports_text_generation: true },
        { id: 'fake-model-unsupported', display_name: 'TEST ONLY unsupported model', provider: 'openai', supports_text_generation: false },
      ] } })
    } else {
      unexpectedOperations.push(path)
      await route.fulfill({ status: 418, json: { error: { code: 'TEST_ONLY_BLOCKED', message: 'TEST ONLY: connection tests and activation must not run.' } } })
    }
  })
  await authorize(page)
  const settings = page.locator('.model-settings')
  await expect(settings.locator('.settings-loading')).toHaveCount(0)
  await expect(settings.getByRole('radio', { name: '云端 API', exact: true })).toBeChecked()
  const key = settings.getByLabel('API Key', { exact: true })
  const save = settings.getByRole('button', { name: '保存配置', exact: true })
  const list = settings.getByRole('button', { name: '刷新模型', exact: true })
  const activate = settings.getByRole('button', { name: '设为默认', exact: true })
  await expect(list).toBeDisabled()
  await expect(save).toBeDisabled()
  await key.fill(testKey)
  await expect(save).toBeEnabled()
  await save.click()
  await expect(key).toHaveValue('')
  await expect(settings.getByText('已加密保存', { exact: true })).toBeVisible()
  expect(saves[0]).toEqual({ provider: 'openai', model_id: '', api_key: testKey, options: {} })
  await expect(activate).toBeDisabled()
  await list.click()
  const picker = settings.getByRole('combobox', { name: '选择模型', exact: true })
  await expect(picker).toBeVisible()
  await settings.locator('.model-field').filter({ has: page.getByRole('combobox', { name: '选择模型', exact: true }) }).locator('.el-select__wrapper').click()
  await page.getByRole('option', { name: 'TEST ONLY listed model', exact: true }).click()
  await expect(settings.getByText('有未保存的更改，请先保存再刷新模型、测试或启用。', { exact: true })).toBeVisible()
  await save.click()
  await expect(settings.locator('.configured-model-id')).toHaveText('fake-model')
  expect(saves[1]).toEqual({ id: configId, version: 1, provider: 'openai', model_id: 'fake-model', options: {} })
  await expect(activate).toBeDisabled()
  await list.click()
  const manual = settings.getByRole('switch', { name: '手动输入', exact: true })
  await expect(manual).not.toBeChecked()
  await settings.locator('.model-label-row .el-switch').click()
  await expect(manual).toBeChecked()
  const manualId = settings.locator('input#llm-model')
  await expect(manualId).toHaveValue('fake-model')
  await manualId.fill('fake-model-manual')
  await expect(settings.getByRole('button', { name: '测试连接', exact: true })).toBeDisabled()
  await save.click()
  await expect(settings.locator('.configured-model-id')).toHaveText('fake-model-manual')
  expect(saves[2]).toEqual({ id: configId, version: 2, provider: 'openai', model_id: 'fake-model-manual', options: {} })
  await expect(key).toHaveValue('')
  await expect(activate).toBeDisabled()
  await expect(settings.locator('.connection-state-badge')).toHaveText('待验证')
  expect(modelLists).toBe(2)

  await settings.locator('.model-mode').getByText('Ollama 本地', { exact: true }).click()
  await expect(settings.getByRole('radio', { name: 'Ollama 本地', exact: true })).toBeChecked()
  await expect(settings.getByText('部署者尚未配置允许的 Ollama 地址。', { exact: true })).toBeVisible()
  await expect(key).toHaveCount(0)
  await expect(save).toBeDisabled()
  await expect(settings.locator('input[type="url"]')).toHaveCount(0)
  await settings.locator('.model-mode').getByText('云端 API', { exact: true }).click()
  await expect(settings.getByRole('radio', { name: '云端 API', exact: true })).toBeChecked()
  await expect(key).toHaveValue('')
  await expect(settings.locator('.configured-model-id')).toHaveText('fake-model-manual')
  const storage = await page.evaluate(() => ({ local: { ...localStorage }, session: { ...sessionStorage } }))
  expect(JSON.stringify(storage)).not.toContain(testKey)
  expect(JSON.stringify(storage)).not.toContain(disposableToken)
  expect(unexpectedOperations).toEqual([])
})
