import { expect, test } from '@playwright/test'

test.beforeEach(async ({ request }) => {
  const response = await request.get('/api/v1/_e2e/status')
  expect(response.ok(), 'Only the isolated E2E server may be used').toBeTruthy()
  expect(await response.json()).toMatchObject({harness:'finsight-isolated-e2e',source_kind:'sec_snapshot'})
})

test('TEST ONLY CN PDF preparation, page citations and research remain isolated from SEC',async({page})=>{
  // Every PDF, model configuration and report response below is browser-only
  // TEST ONLY data. Nothing is downloaded from CNINFO or written to the API.
  const token='finsight-e2e-disposable-only-token'
  const symbol='600000.SH',filingId='TEST-ONLY-CN-FILING',jobId='TEST-ONLY-CN-RESEARCH',reportId='TEST-ONLY-CN-REPORT'
  const pdf='https://static.cninfo.com.cn/finalpage/2025-01-01/123456.PDF'
  const hash='f'.repeat(64),excerpt='TEST ONLY PDF 摘录：本段仅用于浏览器验证，精确数值为 9,007,199,254,740,993.1200 元。'
  const company={symbol,code:'600000',name:'TEST ONLY PDF 公司',market:'CN',exchange:'SH',currency:'CNY',source_kind:'synthetic_fixture'}
  const filing={id:filingId,title:'TEST ONLY PDF 年度报告',form_type:'annual',filing_date:'2025-01-01',report_date:'2024-12-31',source_url:pdf}
  const citation={citation_id:'CIT-001',market:'CN',symbol,filing_id:filingId,announcement_id:filingId,document_id:'TEST-ONLY-DOCUMENT',form_type:'annual',filing_date:filing.filing_date,section:'TEST ONLY 第一节',source_url:pdf,excerpt,chunk_id:'TEST-ONLY-CHUNK',document_sha256:hash,location:{page_start:2,page_end:2},retrieval_mode:'cn_keyword'}
  const job={id:jobId,job_id:jobId,market:'CN',symbol,task_type:'summary',status:'succeeded',error_code:null,error_message:null,report_id:reportId,created_at:'2025-01-01T00:00:00Z',finished_at:'2025-01-01T00:00:01Z'}
  const report={id:reportId,job_id:jobId,market:'CN',symbol,task_type:'summary',title:'TEST ONLY A股 PDF 报告',language:'zh-CN',created_at:job.created_at,provider:'openai',model_id:'TEST-ONLY-MODEL',source_kind:'synthetic_fixture',duration_ms:1,usage:{},content:{claims:[{kind:'disclosed_fact',text:excerpt,citation_ids:['CIT-001'],metric_refs:[],reported_values:[]}],citations:[citation],facts:[],filings:[filing],uncertainties:['TEST ONLY 模型未实际调用。'],insufficient_evidence:false,validation:{},limitations:['TEST ONLY browser fixture'],retrieval_mode:'cn_keyword'}}
  let prepared=false,modelAvailable=false
  const forbidden:string[]=[],submitted:Record<string,unknown>[]=[]
  page.on('request',request=>{if(/\/api\/v1\/(?:research|documents|filings|companies\/600000)/.test(new URL(request.url()).pathname))forbidden.push(request.url())})
  await page.route('**/api/v1/llm/configs',route=>route.fulfill({json:{items:modelAvailable?[{id:'TEST-ONLY-CONFIG',provider:'openai',model_id:'TEST-ONLY-MODEL',has_api_key:true,version:1,verified_version:1,is_active:true,is_verified:true}]:[]}}))
  await page.route('**/api/v1/cn/**',async route=>{
    const request=route.request(),url=new URL(request.url()),path=url.pathname
    if(request.method()==='POST'){
      expect(request.headers()['x-admin-token']).toBe(token)
      if(path.endsWith('/documents/sync')){prepared=true;await route.fulfill({json:{job_id:'TEST-ONLY-PDF-JOB',status:'queued',reused:false}});return}
      if(path.endsWith('/research/jobs')){submitted.push(request.postDataJSON());await route.fulfill({json:{job_id:jobId,status:'queued',reused:false}});return}
      await route.fulfill({status:418,json:{error:{message:'TEST ONLY unexpected write'}}});return
    }
    if(path.endsWith('/documents'))await route.fulfill({json:{items:prepared?[{id:'TEST-ONLY-DOCUMENT',symbol,filing_id:filingId,title:filing.title,source_url:pdf,sha256:hash,source_kind:'synthetic_fixture',fetched_at:job.created_at,parser_version:'TEST-ONLY',page_count:3,coverage:{text_pages:2,empty_or_image_pages:[3],complete_text_extraction:false}}]:[],retrieval_mode:'cn_keyword'}})
    else if(path.includes('/documents/jobs/'))await route.fulfill({json:{id:'TEST-ONLY-PDF-JOB',job_id:'TEST-ONLY-PDF-JOB',status:'succeeded',error_code:null,error_message:null}})
    else if(path.endsWith('/evidence')){expect(url.searchParams.get('filing_id')).toBe(filingId);await route.fulfill({json:{items:[citation],retrieval_mode:'cn_keyword',scope_note:'TEST ONLY saved PDF pages'}})}
    else if(path.endsWith('/filings'))await route.fulfill({json:{items:[filing],total:1,page:1,page_size:10,limitations:[]}})
    else if(path.endsWith('/metrics'))await route.fulfill({json:{items:[],frequency:'annual',currency:'CNY',source_kind:'synthetic_fixture',selection_policy:'latest_provider_snapshot',limitations:[]}})
    else if(path.endsWith('/research/history'))await route.fulfill({json:{items:[job],total:1,page:1,page_size:20}})
    else if(path.includes('/research/jobs/'))await route.fulfill({json:job})
    else if(path.endsWith('/download'))await route.fulfill({contentType:'text/markdown',body:'# TEST ONLY CN report\n'+excerpt})
    else if(path.includes('/research/reports/'))await route.fulfill({json:report})
    else await route.fulfill({json:company})
  })
  await page.goto('/settings')
  await page.getByLabel('本地管理员访问令牌',{exact:true}).fill(token)
  await page.getByRole('button',{name:'在本次会话中使用',exact:true}).click()
  await page.goto(`/cn/company/${symbol}`)
  // A full reload deliberately clears authorization. Authorize via the same SPA.
  await page.getByRole('link',{name:'设置',exact:true}).first().click()
  await page.getByLabel('本地管理员访问令牌',{exact:true}).fill(token)
  await page.getByRole('button',{name:'在本次会话中使用',exact:true}).click()
  await page.locator('.sidebar-recent').getByRole('link').first().click()
  await page.getByRole('tab',{name:'财报与公告',exact:true}).click()
  await page.getByRole('button',{name:'正文与研究',exact:true}).click()
  await expect(page.getByRole('heading',{name:'A 股 PDF 财报研究',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'创建研究任务',exact:false})).toBeDisabled()
  await page.getByRole('button',{name:'准备 PDF 正文',exact:true}).click()
  await expect(page.locator('.document-record')).toContainText('PDF 页数 3')
  await expect(page.locator('.document-record')).toContainText('已提取文字页 2 / 3')
  await expect(page.locator('.cn-pdf-coverage-warning')).toContainText('无文字页：3')
  await page.getByRole('textbox',{name:'证据搜索',exact:true}).fill('TEST ONLY')
  await page.getByRole('button',{name:'检索证据',exact:true}).click()
  await expect(page.locator('.evidence-card')).toContainText(excerpt)
  await expect(page.locator('.evidence-card').getByRole('link',{name:'PDF 原文 ↗',exact:true})).toHaveAttribute('href',pdf+'#page=2')
  await expect(page.locator('.evidence-card .form-tag')).toHaveText('年报')
  modelAvailable=true
  await page.getByRole('button',{name:'刷新模型配置',exact:true}).click()
  await expect(page.getByRole('button',{name:'创建研究任务',exact:false})).toBeEnabled()
  await page.getByRole('button',{name:'创建研究任务',exact:false}).click()
  await expect(page).toHaveURL(new RegExp(`/cn/research/jobs/${jobId}$`))
  await expect(page.getByRole('heading',{name:report.title,exact:true})).toBeVisible()
  await expect(page.locator('#report-facts')).toContainText(excerpt)
  await expect(page.getByRole('link',{name:'公司档案 ↗',exact:true})).toHaveAttribute('href',`/cn/company/${symbol}`)
  const downloadPromise=page.waitForEvent('download')
  await page.getByRole('button',{name:'下载 Markdown',exact:true}).click()
  expect((await downloadPromise).suggestedFilename()).toContain('FinSight-CN-600000.SH')
  await page.getByRole('link',{name:'研究记录',exact:true}).first().click()
  await expect(page).toHaveURL(/\/cn\/research$/)
  await expect(page.locator('.research-history')).toContainText(symbol)
  await expect(page.locator('.main-nav > a.active')).toHaveCount(1)
  await page.setViewportSize({width:320,height:740})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth)).toBeTruthy()
  expect(submitted).toHaveLength(1)
  expect(submitted[0]).toMatchObject({symbol,filing_ids:[filingId],task_type:'summary'})
  expect(submitted[0]).not.toHaveProperty('cik')
  expect(forbidden).toEqual([])
  await page.setViewportSize({width:1280,height:900})
  await page.locator('.main-nav').getByRole('link',{name:'设置',exact:true}).click()
  await page.locator('.language-preference .el-select__wrapper').click()
  await page.getByRole('option',{name:'English',exact:true}).click()
  await page.locator('.sidebar-recent').getByRole('link').first().click()
  await page.getByRole('tab',{name:'Filings & AI research',exact:true}).click()
  await expect(page.getByRole('heading',{name:'A-share PDF research',exact:true})).toBeVisible()
  await expect(page.locator('.document-record')).toContainText('Text extracted from 2 / 3 pages')
  await expect(page.locator('.cn-pdf-coverage-warning')).toContainText('Pages without extractable text: 3')
  await page.setViewportSize({width:320,height:740})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth)).toBeTruthy()
})

test('TEST ONLY A-share UI keeps symbols, currencies, cumulative periods and source links separate from SEC', async ({ page }) => {
  // All CN responses are synthetic browser-only fixtures. No real financial
  // data, provider call, backend record or model result is created by this test.
  const company={symbol:'600000.SH',code:'600000',name:'TEST ONLY A股公司',market:'CN',exchange:'SH',currency:'CNY',source_kind:'synthetic_fixture',last_synced_at:'2025-01-01T00:00:00Z'}
  const sourceId='TEST-ONLY-CN-source'
  const sourceUrl='https://datacenter.eastmoney.com/securities/api/data/v1/get'
  const pdf='https://static.cninfo.com.cn/finalpage/2025-01-01/TEST-ONLY.PDF'
  const requests:string[]=[]
  const writes:string[]=[]
  const makeMetrics=(frequency:string)=>['cn_total_revenue','parent_net_income','operating_cash_flow','assets','liabilities','equity'].map((key,index)=>({
    metric_key:key,label:key,period_type:frequency,fiscal_year:2025,fiscal_quarter:frequency==='cumulative'?3:null,
    period_start:index<3?'2025-01-01':null,period_end:frequency==='cumulative'?'2025-09-30':'2025-12-31',
    value:index===0?'9007199254740993.1200':'1000000000',unit:'CNY',calc_method:'TEST ONLY synthetic fixture',
    provenance:{source_snapshot_id:sourceId,source_field:'TEST ONLY FIELD',source_kind:'synthetic_fixture'},missing_reason:null,yoy:null,yoy_reason:'MISSING_PRIOR_PERIOD',
  }))
  page.on('request', request=>{if(request.url().includes('/api/v1/companies/600000'))requests.push(request.url())})
  await page.route('**/api/v1/cn/**',async route=>{
    const url=new URL(route.request().url())
    if(route.request().method()!=='GET'){writes.push(url.pathname);await route.fulfill({status:418,json:{error:{message:'TEST ONLY: writes forbidden'}}});return}
    if(url.pathname.endsWith('/status'))await route.fulfill({json:{source_kind:'synthetic_fixture',directory_count:1,directory_updated_at:'2025-01-01T00:00:00Z',limitations:[]}})
    else if(url.pathname.endsWith('/search'))await route.fulfill({json:{items:[company],source_kind:'synthetic_fixture'}})
    else if(url.pathname.endsWith('/metrics')){
      const frequency=url.searchParams.get('frequency')||'annual'
      await route.fulfill({json:{items:makeMetrics(frequency),source_kind:'synthetic_fixture',currency:'CNY',frequency,selection_policy:'latest_provider_snapshot',limitations:[]}})
    }else if(url.pathname.endsWith('/documents'))await route.fulfill({json:{items:[],retrieval_mode:'cn_keyword'}})
    else if(url.pathname.endsWith('/filings'))await route.fulfill({json:{items:[{id:'TEST-ONLY-FILING',title:'TEST ONLY 年度报告',filing_date:'2025-01-01',report_date:'2024-12-31',form_type:'annual',source_url:pdf}],total:1,page:1,page_size:10,source_kind:'synthetic_fixture',limitations:[]}})
    else if(url.pathname.includes('/sources/'))await route.fulfill({json:{id:sourceId,kind:'TEST ONLY financial',source_kind:'synthetic_fixture',source_url:sourceUrl,sha256:'0'.repeat(64),fetched_at:'2025-01-01T00:00:00Z',parser_version:'TEST ONLY',source_metadata:{fixture:true}}})
    else await route.fulfill({json:company})
  })
  await page.goto('/')
  await page.locator('.home-market-row').getByRole('combobox',{name:'研究市场',exact:true}).selectOption('CN')
  await expect(page.getByRole('heading',{name:'A 股公司目录',exact:false})).toBeVisible()
  await expect(page.getByText('目录含历史证券标识，不是实时上市状态或行情。',{exact:true})).toBeVisible()
  await page.locator('.company-card').filter({hasText:company.name}).click()
  await expect(page).toHaveURL(/\/cn\/company\/600000\.SH$/)
  await expect(page.getByRole('heading',{name:company.name,exact:true})).toBeVisible()
  await expect(page.locator('.cn-company-page .source-badge').first()).toContainText('合成测试数据')
  await expect(page.getByRole('button',{name:'AI 财务概览',exact:true})).toHaveCount(0)
  await expect(page.locator('.metrics-table-panel')).toContainText('9,007,199,254,740,993.1200')
  await page.locator('.metrics-table-panel').getByRole('button',{name:'查看来源 ↗',exact:true}).first().click()
  const drawer=page.getByRole('dialog',{name:'A 股指标来源与计算依据',exact:true})
  await expect(drawer).toContainText('9,007,199,254,740,993.1200')
  await expect(drawer).toContainText('SHA-256 '+'0'.repeat(64))
  await expect(drawer.getByRole('link',{name:'查看数据来源 ↗',exact:true})).toHaveAttribute('href',sourceUrl)
  await page.keyboard.press('Escape')
  await expect(drawer).not.toBeVisible()
  await page.getByRole('radio',{name:'年初至今',exact:true}).locator('..').click()
  await expect(page.getByRole('heading',{name:'年初至今财务表现',exact:true})).toBeVisible()
  await expect(page.locator('.cn-basis-note')).toContainText('不能当作单季度')
  await expect(page.locator('.metrics-table-panel')).toContainText('2025-09-30')
  await page.getByRole('tab',{name:'财报与公告',exact:true}).click()
  await expect(page.getByRole('heading',{name:'TEST ONLY 年度报告',exact:true})).toBeVisible()
  await expect(page.locator('.cn-filing .form-tag')).toHaveText('年报')
  await expect(page.getByRole('link',{name:'查看披露原文 ↗',exact:true})).toHaveAttribute('href',pdf)
  await page.getByRole('tab',{name:'财报正文与 AI 研究',exact:true}).click()
  await expect(page.getByRole('heading',{name:'A 股 PDF 财报研究',exact:true})).toBeVisible()
  await expect(page.getByRole('button',{name:'准备 PDF 正文',exact:true})).toBeDisabled()
  await page.setViewportSize({width:390,height:844})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth)).toBeTruthy()
  await page.setViewportSize({width:320,height:740})
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=document.documentElement.clientWidth)).toBeTruthy()
  await page.getByRole('tab',{name:'财务概览',exact:true}).click()
  await page.getByRole('button',{name:'同步最新 A 股资料',exact:true}).first().click()
  await expect(page.getByRole('alert')).toContainText('请先在设置页输入本地管理员访问令牌。')
  expect(writes).toEqual([])
  expect(requests).toEqual([])
})
