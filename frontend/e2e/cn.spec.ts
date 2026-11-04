import { expect, test } from '@playwright/test'

test.beforeEach(async ({ request }) => {
  const response = await request.get('/api/v1/_e2e/status')
  expect(response.ok(), 'Only the isolated E2E server may be used').toBeTruthy()
  expect(await response.json()).toMatchObject({harness:'finsight-isolated-e2e',source_kind:'sec_snapshot'})
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
    }else if(url.pathname.endsWith('/filings'))await route.fulfill({json:{items:[{id:'TEST-ONLY-FILING',title:'TEST ONLY 年度报告',filing_date:'2025-01-01',report_date:'2024-12-31',form_type:'annual',source_url:pdf}],total:1,page:1,page_size:10,source_kind:'synthetic_fixture',limitations:[]}})
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
  await page.getByRole('tab',{name:'研究能力',exact:true}).click()
  await expect(page.locator('#pane-coverage')).toContainText('尚未接通')
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
