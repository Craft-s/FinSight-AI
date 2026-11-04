import { beforeEach, describe, expect, it } from 'vitest'
import { displayValue, displayTime, formatExact, safeSecUrl, sourceInfo } from './utils'
import { activeLanguage, isSupportedLocale, locale, metricLabel, setLocale, SUPPORTED_LANGUAGES, t, unitLabel } from './i18n'

beforeEach(() => setLocale('zh-CN'))

describe('Financial display safety', () => {
  it('distinguishes missing values from genuine zero and rejects nonnumeric data', () => {
    expect(displayValue(null)).toBe('N/A')
    expect(displayValue('0')).toBe('0')
    expect(displayValue('NaN')).toBe('N/A')
    expect(displayValue('')).toBe('N/A')
  })
  it('does not label EPS as dollar amounts in billions', () => {
    expect(displayValue('6.42', 'USD/shares')).toBe('6.42')
    expect(displayValue('-2500000000', 'USD')).toBe('-25 亿')
  })
  it('shows percentages and cash-flow multiples with their own units', () => {
    expect(displayValue('45.12345678', 'percent')).toBe('45.12%')
    expect(displayValue('1.25', 'ratio')).toBe('1.25 倍')
    expect(displayValue(null, 'percent')).toBe('N/A')
  })
  it('clearly distinguishes synthetic fixtures from official snapshots', () => {
    expect(sourceInfo('synthetic_fixture').label).toContain('测试')
    expect(sourceInfo('sec_snapshot').detail).toContain('非实时')
    expect(sourceInfo(undefined).label).toContain('待确认')
  })
  it('never turns untrusted source text into executable or non-SEC links', () => {
    for (const value of ['javascript:alert(1)', 'https://www.sec.gov.evil.test/x', 'http://www.sec.gov/x', 'https://evil.test/?u=sec.gov', 'https://user:password@www.sec.gov/x']) expect(safeSecUrl(value)).toBeNull()
    expect(safeSecUrl('https://www.sec.gov/Archives/edgar/data/320193/x')).toBe('https://www.sec.gov/Archives/edgar/data/320193/x')
  })
  it('labels synchronization timestamps with the explicit display timezone', () => {
    expect(displayTime(null)).toBe('尚未同步')
    expect(displayTime('2025-01-01T00:00:00Z')).toContain('UTC+8')
    expect(displayTime('2025-01-01T00:00:00Z')).toContain('08:00:00')
  })
})

describe('Exact financial figures', () => {
  it('groups integer figures without rounding', () => {
    expect(formatExact('416161000000')).toBe('416,161,000,000')
    expect(formatExact('1000')).toBe('1,000')
    expect(formatExact('999')).toBe('999')
  })
  it('preserves decimal places and trailing zeros exactly', () => {
    expect(formatExact('1234567.890000')).toBe('1,234,567.890000')
    expect(formatExact('0.00000000000000100')).toBe('0.00000000000000100')
  })
  it('preserves signs and genuine zeros', () => {
    expect(formatExact('-1234567.80')).toBe('-1,234,567.80')
    expect(formatExact('+1234567.80')).toBe('+1,234,567.80')
    expect(formatExact('0')).toBe('0')
    expect(formatExact('-0.0000')).toBe('-0.0000')
  })
  it('never converts values beyond JavaScript integer precision to Number', () => {
    expect(formatExact('9007199254740993')).toBe('9,007,199,254,740,993')
    expect(formatExact('123456789012345678901234567890.12345678901234567890')).toBe('123,456,789,012,345,678,901,234,567,890.12345678901234567890')
  })
  it('keeps missing and invalid values distinct from zero', () => {
    for (const value of [null, undefined, '', 'NaN', 'Infinity', '1e9', '12x', '1,234']) expect(formatExact(value)).toBe('N/A')
  })
})

describe('Bilingual financial UI', () => {
  it('drives language choices and component translations from the language registry', () => {
    expect(SUPPORTED_LANGUAGES).toEqual(expect.arrayContaining([{code:'zh-CN',nativeName:'简体中文'}, {code:'en-US',nativeName:'English'}]))
    setLocale('en-US')
    expect(activeLanguage.value.elementLocale.name).toBe('en')
    setLocale('zh-CN')
    expect(activeLanguage.value.elementLocale.name).toBe('zh-cn')
  })
  it('does not accept unsupported stored language preferences', () => {
    for (const value of [null, undefined, '', '__proto__', 'constructor', 'not-a-locale']) expect(isSupportedLocale(value)).toBe(false)
    expect(isSupportedLocale('en-US')).toBe(true)
  })
  it('switches financial labels, sources and units together', () => {
    setLocale('en-US')
    expect(locale.value).toBe('en-US')
    expect(metricLabel('revenue')).toBe('Revenue')
    expect(metricLabel('eps_diluted')).toBe('Diluted EPS')
    expect(t('精确数值')).toBe('Exact value')
    expect(sourceInfo('sec_snapshot').label).toBe('SEC historical snapshot')
    expect(unitLabel('USD/shares')).toBe('USD/share')
    setLocale('zh-CN')
    expect(metricLabel('revenue')).toBe('营业收入')
    expect(t('精确数值')).toBe('精确数值')
  })
  it('uses English compact amounts without altering authoritative figures', () => {
    setLocale('en-US')
    expect(displayValue('416161000000')).toBe('416.16B')
    expect(displayValue('-2500000')).toBe('-2.5M')
    expect(displayValue('1.25', 'ratio')).toBe('1.25 ×')
    expect(formatExact('416161000000')).toBe('416,161,000,000')
  })
  it('supports message interpolation without HTML interpretation', () => {
    setLocale('en-US')
    expect(t('请求失败（HTTP {status}），请稍后重试。', {status:503})).toBe('Request failed (HTTP 503). Please try again.')
    expect(t('请求失败（HTTP 404），请稍后重试。')).toBe('Request failed (HTTP 404). Please try again.')
  })
})
