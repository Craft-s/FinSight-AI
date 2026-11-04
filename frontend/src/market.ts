import { ref } from 'vue'
import type { CnCompany, Company, Metric } from './types'

export type ResearchMarket = 'US'|'CN'
// A market is navigation context, never an authentication or source-mode setting.
export const activeMarket = ref<ResearchMarket>('US')
export function companyIdentity(company: Pick<Company, 'market'|'cik'|'symbol'>): string {
  return `${company.market}:${company.market === 'CN' ? company.symbol : company.cik}`
}
export function companyRoute(company: Pick<Company, 'market'|'cik'|'symbol'>): string {
  return company.market === 'CN' ? `/cn/company/${encodeURIComponent(company.symbol || '')}` : `/company/${encodeURIComponent(company.cik)}`
}
export function cnSearchCompany(company: CnCompany): Company {
  return { ...company, cik: '', ticker: company.code }
}

/** Compare like-length YTD flows; never join March, June and September totals. */
export function comparableCnTrend(metrics: readonly Metric[], frequency: 'annual'|'cumulative', selectedPeriod: string): Metric[] {
  if (frequency === 'annual') return metrics.filter(metric => metric.period_type === 'annual')
  if (!/^\d{4}-\d{2}-\d{2}$/.test(selectedPeriod)) return []
  const end = selectedPeriod.slice(5)
  return metrics.filter(metric => metric.period_type === 'cumulative' && metric.period_end.slice(5) === end)
}
