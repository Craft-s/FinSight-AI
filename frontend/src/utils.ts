import type { SourceKind } from './types'
import { activeLanguage, locale, t } from './i18n'

export function sourceInfo(kind?: SourceKind) {
  if (kind === 'synthetic_fixture') return { label: t('合成测试数据'), detail: t('仅用于功能测试 · 不代表公司的真实财务情况'), tone: 'warning' }
  if (kind === 'sec_snapshot') return { label: t('SEC 历史快照'), detail: t('已保存 SEC 历史响应 · 非实时更新，采集时间见来源'), tone: 'snapshot' }
  if (kind === 'sec_live') return { label: t('SEC 官方数据'), detail: t('来源于 SEC EDGAR · 请以最后同步时间为准'), tone: 'live' }
  if (kind === 'cn_live') return { label: t('A 股公开资料'), detail: t('财务：东方财富第三方整理 · 公告：巨潮披露'), tone: 'live' }
  if (kind === 'cn_snapshot') return { label: t('A 股历史快照'), detail: t('已保存的 A 股公开资料归档，不代表最新披露。'), tone: 'snapshot' }
  return { label: t('来源待确认'), detail: t('连接数据服务后显示数据模式'), tone: 'unknown' }
}

/** Group the authoritative decimal string without a floating-point conversion. */
export function formatExact(value: string | null | undefined): string {
  if (typeof value !== 'string') return 'N/A'
  const match = /^([+-]?)(\d+)(\.\d+)?$/.exec(value.trim())
  if (!match) return 'N/A'
  return match[1] + match[2].replace(/\B(?=(\d{3})+(?!\d))/g, ',') + (match[3] || '')
}

export function displayValue(value: string | null | undefined, unit = 'USD'): string {
  if (value == null || value === '' || !Number.isFinite(Number(value))) return 'N/A'
  if (unit === 'percent') return `${Number(value).toLocaleString(locale.value, { maximumFractionDigits: 2 })}%`
  if (unit === 'ratio') return `${Number(value).toLocaleString(locale.value, { maximumFractionDigits: 3 })} ${t('倍')}`
  if (!['USD','CNY'].includes(unit)) return Number(value).toLocaleString(locale.value, { maximumFractionDigits: 4 })
  const number = Number(value)
  if (activeLanguage.value.amountFormat === 'international') {
    const scale = [[1e12, 'T'], [1e9, 'B'], [1e6, 'M'], [1e3, 'K']] as const
    const compact = scale.find(([threshold]) => Math.abs(number) >= threshold)
    return compact ? `${(number / compact[0]).toLocaleString(locale.value, { maximumFractionDigits: 2 })}${compact[1]}` : number.toLocaleString(locale.value, { maximumFractionDigits: 2 })
  }
  if (unit === 'CNY' && Math.abs(number) >= 1e12) return `${(number / 1e12).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 万亿`
  if (Math.abs(number) >= 1e8) return `${(number / 1e8).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 亿`
  if (Math.abs(number) >= 1e4) return `${(number / 1e4).toLocaleString('zh-CN', { maximumFractionDigits: 2 })} 万`
  return number.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
export function displayTime(value?: string | null): string {
  if (!value) return t('尚未同步')
  const date = new Date(/Z$|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`)
  return Number.isNaN(date.getTime()) ? value : `${date.toLocaleString(locale.value, { timeZone: 'Asia/Shanghai', hour12: false })} UTC+8`
}
export function safeSecUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null
  try { const url = new URL(value); return url.protocol === 'https:' && ['www.sec.gov', 'data.sec.gov'].includes(url.hostname) && !url.username && !url.password ? url.href : null } catch { return null }
}
export function safeCnSourceUrl(value: unknown): string | null {
  if (typeof value !== 'string') return null
  try {
    const url = new URL(value)
    const hosts = ['www.cninfo.com.cn','static.cninfo.com.cn','data.eastmoney.com','datacenter.eastmoney.com','datacenter-web.eastmoney.com','np-anotice-stock.eastmoney.com','pdf.dfcfw.com','emweb.securities.eastmoney.com']
    return url.protocol === 'https:' && (!url.port || url.port === '443') && hosts.includes(url.hostname) && !url.username && !url.password ? url.href : null
  } catch { return null }
}
export function missingReason(value: string | null): string {
  const reasons: Record<string, string> = { NO_VALID_ANNUAL_FACT: '缺少同口径年度事实', CONCEPT_NOT_FOUND: '未找到可用指标标签', UNIT_MISMATCH: '单位不兼容', MISSING_FACT: '未披露或缺少可用事实', MISSING_CONCEPT_OR_PERIOD: '未披露该标签或期间', NO_COMPARABLE_ANNUAL_FACT: '缺少可比年度事实', CONFLICTING_FACTS: '同日披露数值存在冲突', CONFLICTING_CONCEPTS: '候选财务标签存在口径冲突', AMBIGUOUS_ANNUAL_PERIOD: '年度期间无法可靠确定', NON_ADDITIVE_METRIC: '该指标不可通过期间差分推导', INCOMPATIBLE_BASIS: '财务口径不可比' }
  Object.assign(reasons, { MISSING_PRIOR_PERIOD: '缺少上年同期', NON_POSITIVE_PRIOR_VALUE: '基期为零或负数，不计算增长率', MISSING_CURRENT_VALUE: '缺少本期数值', MISSING_PRIOR_VALUE: '缺少上期数值', MISSING_CUMULATIVE_INPUT: '缺少累计期间输入', MISSING_OPERAND: '缺少计算操作数', INCOMPATIBLE_DISCLOSURE_BASIS: '披露口径不兼容', MISSING_SOURCE_FACTS: '缺少来源事实', INCOMPATIBLE_PERIOD: '期间不可比', INCOMPATIBLE_CONCEPT: '财务标签不可比', INCOMPATIBLE_SELECTION_POLICY: '版本口径不兼容', INCOMPATIBLE_RESTATEMENT_BASIS: '重述口径不兼容', UNKNOWN_FISCAL_PERIOD: '财务期间未知', AMBIGUOUS_COMPARISON_PERIOD: '比较期间不明确', NON_POSITIVE_DENOMINATOR: '分母为零或负数', AMBIGUOUS_OPERAND: '计算操作数不明确', DERIVED_RATIO_YOY_NOT_CALCULATED: '派生比率不计算同比', AMBIGUOUS_QUARTER_PERIOD: '季度期间无法可靠确定' })
  Object.assign(reasons, { MISSING_STATEMENT_PERIOD: '供应商缺少该期间报表', MISSING_SOURCE_FIELD: '供应商报表缺少该字段', INVALID_SOURCE_NUMBER: '来源数值不可用', CONFLICTING_PROVIDER_RECORDS: '同版本供应商记录存在冲突', FINANCIAL_INDUSTRY_GROSS_PROFIT_UNSUPPORTED: '金融业报表不适用该毛利计算' })
  return value ? t(reasons[value] || value) : t('未找到可用数据')
}
