import { beforeEach, describe, expect, it } from 'vitest'
import { cnSearchCompany, companyIdentity, companyRoute, comparableCnTrend } from './market'
import { chartPeriod, chartValue } from './chart'
import { displayValue, formatExact, safeCnSourceUrl, safeSecUrl, sourceInfo } from './utils'
import { metricLabel, setLocale, unitLabel } from './i18n'
import type { Metric } from './types'

beforeEach(() => setLocale('zh-CN'))
describe('A-share market and accounting boundaries', () => {
  it('compares the same YTD cutoff across years without mutating authoritative rows', () => {
    const metric = (date:string, period_type:string):Metric=>({metric_key:'cn_total_revenue',label:'TEST ONLY',period_type,period_end:date,period_start:date.slice(0,4)+'-01-01',fiscal_year:Number(date.slice(0,4)),value:'1000',unit:'CNY',calc_method:'TEST ONLY',provenance:{},missing_reason:null})
    const rows=Object.freeze([metric('2025-03-31','cumulative'),metric('2025-06-30','cumulative'),metric('2025-09-30','cumulative'),metric('2024-09-30','cumulative'),metric('2024-12-31','annual')])
    const before=JSON.stringify(rows)
    expect(comparableCnTrend(rows,'cumulative','2025-09-30').map(row=>row.period_end)).toEqual(['2025-09-30','2024-09-30'])
    expect(comparableCnTrend(rows,'cumulative','2025-06-30').map(row=>row.period_end)).toEqual(['2025-06-30'])
    expect(comparableCnTrend(rows,'cumulative','')).toEqual([])
    expect(comparableCnTrend(rows,'annual','2024-12-31').map(row=>row.period_end)).toEqual(['2024-12-31'])
    expect(JSON.stringify(rows)).toBe(before)
  })
  it('keeps stock symbols separate from SEC CIKs and recent-history keys', () => {
    const cn = cnSearchCompany({symbol:'000001.SZ',code:'000001',name:'TEST ONLY',market:'CN',exchange:'SZ',currency:'CNY',source_kind:'synthetic_fixture'})
    const us = {cik:'0000000001',market:'US'}
    expect(cn.cik).toBe('')
    expect(companyRoute(cn)).toBe('/cn/company/000001.SZ')
    expect(companyRoute(us)).toBe('/company/0000000001')
    expect(companyIdentity(cn)).not.toBe(companyIdentity(us))
    expect(companyIdentity(cn)).not.toBe(companyIdentity({...cn,symbol:'000001.SH'}))
    expect(companyRoute({...cn,symbol:'bad/path'})).toBe('/cn/company/bad%2Fpath')
  })
  it('scales CNY amounts without scaling per-share values or changing exact strings', () => {
    expect(displayValue('1000000000','CNY')).toBe('10 亿')
    expect(displayValue('40000000000000','CNY')).toBe('40 万亿')
    expect(displayValue('7.2500','CNY/shares')).toBe('7.25')
    expect(chartValue({value:'1000000000',unit:'CNY'})).toBe(1)
    expect(chartValue({value:'7.2500',unit:'CNY/shares'})).toBe(7.25)
    expect(formatExact('9007199254740993.1200')).toBe('9,007,199,254,740,993.1200')
    expect(unitLabel('CNY')).toBe('人民币')
    setLocale('en-US')
    expect(displayValue('1000000000','CNY')).toBe('1B')
    expect(unitLabel('CNY/shares')).toBe('CNY/share')
  })
  it('labels cumulative periods and Chinese accounting definitions explicitly', () => {
    expect(chartPeriod({period_type:'cumulative',fiscal_year:2025,fiscal_quarter:3,period_end:'2025-09-30'})).toBe('累计截至 2025-09-30')
    expect(metricLabel('net_income')).toBe('净利润')
    expect(metricLabel('net_income',undefined,'CN')).toBe('净利润（供应商报表口径）')
    expect(metricLabel('parent_net_income',undefined,'CN')).toBe('归母净利润')
    expect(metricLabel('monetary_funds',undefined,'CN')).toBe('货币资金')
    setLocale('en-US')
    expect(metricLabel('net_income',undefined,'CN')).toBe('Net profit (provider reporting scope)')
    expect(metricLabel('cn_operating_profit',undefined,'CN')).toContain('CAS')
  })
  it('does not present third-party compiled financials as SEC or regulatory facts', () => {
    expect(sourceInfo('cn_live').detail).toContain('第三方')
    expect(sourceInfo('cn_snapshot').detail).toContain('不代表最新')
    expect(sourceInfo('synthetic_fixture').label).toContain('测试')
  })
  it('only permits explicit HTTPS disclosure hosts without weakening SEC links', () => {
    expect(safeCnSourceUrl('https://static.cninfo.com.cn/finalpage/2025-01-01/1.PDF')).toContain('static.cninfo.com.cn')
    expect(safeCnSourceUrl('https://datacenter.eastmoney.com/securities/api/data/v1/get')).toContain('datacenter.eastmoney.com')
    for (const value of ['http://static.cninfo.com.cn/a.PDF','https://static.cninfo.com.cn.evil.test/a','https://user:secret@static.cninfo.com.cn/a','https://static.cninfo.com.cn:444/a','javascript:alert(1)','https://evil.test/?next=cninfo.com.cn']) expect(safeCnSourceUrl(value)).toBeNull()
    expect(safeSecUrl('https://static.cninfo.com.cn/a.PDF')).toBeNull()
  })
})
