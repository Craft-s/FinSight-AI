import { computed, ref } from 'vue'
import enLocale from 'element-plus/es/locale/lang/en'
import zhLocale from 'element-plus/es/locale/lang/zh-cn'
import { baseTranslations } from './locales/base'
import { pageTranslations } from './locales/pages'
import { companyTranslations } from './locales/company'
import { llmTranslations } from './locales/llm'
import { researchTranslations } from './locales/research'
import { designTranslations } from './locales/design'
import { designSettingsTranslations } from './locales/design-settings'
import { designResearchTranslations } from './locales/design-research'
import { cnTranslations } from './locales/cn'
import { cnResearchTranslations } from './locales/cn-research'

const englishMessages: Record<string, string> = { ...baseTranslations, ...pageTranslations, ...companyTranslations, ...llmTranslations, ...researchTranslations, ...designTranslations, ...designSettingsTranslations, ...designResearchTranslations, ...cnTranslations, ...cnResearchTranslations }

/** Add a language here with its messages, control locale, and display conventions. */
export const LANGUAGE_DEFINITIONS = {
  'zh-CN': { nativeName: '简体中文', title: 'FinSight AI · 财报研究工作台', messages: {} as Record<string, string>, elementLocale: zhLocale, amountFormat: 'chinese' },
  'en-US': { nativeName: 'English', title: 'FinSight AI · Financial Research Workspace', messages: englishMessages, elementLocale: enLocale, amountFormat: 'international' },
} as const

export type Locale = keyof typeof LANGUAGE_DEFINITIONS
export const SUPPORTED_LANGUAGES = (Object.keys(LANGUAGE_DEFINITIONS) as Locale[]).map(code => ({code, nativeName: LANGUAGE_DEFINITIONS[code].nativeName}))
export function isSupportedLocale(value: unknown): value is Locale { return typeof value === 'string' && Object.hasOwn(LANGUAGE_DEFINITIONS, value) }
const STORAGE_KEY = 'finsight.locale'

function initialLocale(): Locale {
  try {
    const saved = typeof window !== 'undefined' ? window.localStorage.getItem(STORAGE_KEY) : null
    return isSupportedLocale(saved) ? saved : 'zh-CN'
  }
  catch { return 'zh-CN' }
}

export const locale = ref<Locale>(initialLocale())
export const activeLanguage = computed(() => LANGUAGE_DEFINITIONS[locale.value])

export function t(message: string, values: Record<string, string | number> = {}): string {
  const httpError = /^请求失败（HTTP (\d+)），请稍后重试。$/.exec(message)
  const template = httpError ? '请求失败（HTTP {status}），请稍后重试。' : message
  const parameters = httpError ? {...values, status: httpError[1]} : values
  const translated = activeLanguage.value.messages[template] || template
  return translated.replace(/\{(\w+)\}/g, (match, key: string) => parameters[key] == null ? match : String(parameters[key]))
}

function updateDocument() {
  if (typeof document === 'undefined') return
  document.documentElement.lang = locale.value
  document.title = activeLanguage.value.title
}

export function setLocale(value: Locale) {
  if (!isSupportedLocale(value)) return
  locale.value = value
  try { if (typeof window !== 'undefined') window.localStorage.setItem(STORAGE_KEY, value) } catch { /* A blocked preference store must not break the UI. */ }
  updateDocument()
}

const metricNames: Record<string, string> = {
  revenue: '营业收入', net_income: '净利润', gross_profit: '毛利润', operating_income: '营业利润',
  operating_cash_flow: '经营现金流', assets: '总资产', liabilities: '总负债', equity: '股东权益',
  cash: '现金及等价物', eps_diluted: '稀释每股收益', gross_margin: '毛利率', net_margin: '净利率',
  operating_margin: '营业利润率', operating_cash_flow_to_net_income: '经营现金流 / 净利润',
}

const cnMetricNames: Record<string,string> = {
  cn_total_revenue: '营业总收入', parent_net_income: '归母净利润', net_income: '净利润（供应商报表口径）',
  equity: '股东权益（供应商报表口径）', monetary_funds: '货币资金', cn_operating_profit: '营业利润（中国准则）',
  eps_basic: '基本每股收益', cash: '期末现金及现金等价物',
  cn_net_margin: '报表净利润 / 营业总收入', cn_operating_profit_margin: '中国准则营业利润率', debt_to_assets: '资产负债率',
}
export function metricLabel(key: string, fallback = key, market: 'US'|'CN' = 'US'): string {
  const base = key.endsWith('_yoy') ? key.slice(0, -4) : key
  return t((market === 'CN' ? cnMetricNames[base] : undefined) || metricNames[base] || fallback) + (base !== key ? ` ${t('同比')}` : '')
}
export function unitLabel(unit: string): string {
  if (unit === 'USD') return t('美元')
  if (unit === 'USD/shares') return t('美元/股')
  if (unit === 'CNY') return t('人民币')
  if (unit === 'CNY/shares') return t('人民币/股')
  if (unit === 'shares') return t('股')
  if (unit === 'percent') return '%'
  if (unit === 'ratio') return t('倍')
  return unit
}

updateDocument()
