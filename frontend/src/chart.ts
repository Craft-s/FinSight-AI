import type { Metric } from './types'
import { formatExact } from './utils'
import { t } from './i18n'

/** Plot coordinates may be approximate; displayed exact values remain strings. */
export function chartValue(metric: Pick<Metric, 'value' | 'unit'>): number | null {
  if (formatExact(metric.value) === 'N/A') return null
  const value = Number(metric.value) / (['USD','CNY'].includes(metric.unit) ? 1e9 : 1)
  return Number.isFinite(value) ? value : null
}

export function chartRows(metrics: Metric[], key: string): Metric[] {
  return metrics.filter(metric => metric.metric_key === key)
    .sort((left, right) => left.period_end.localeCompare(right.period_end))
}

export function chartPeriod(metric: Pick<Metric, 'fiscal_year' | 'fiscal_quarter' | 'period_end'> & Partial<Pick<Metric,'period_type'>>): string {
  if (metric.period_type === 'cumulative') return `${t('累计截至')} ${metric.period_end}`
  if (metric.fiscal_year == null) return metric.period_end
  return `FY${metric.fiscal_year}${metric.fiscal_quarter ? ` Q${metric.fiscal_quarter}` : ''}`
}

/** Horizontal labels include both endpoints without crowding on phones. */
export function chartLabelIndices(length: number, width: number): Set<number> {
  if (!length) return new Set()
  const maximum = Math.max(2, Math.min(10, Math.floor(Math.max(0, width - 64) / 72)))
  if (length <= maximum) return new Set(Array.from({ length }, (_, index) => index))
  return new Set(Array.from({ length: maximum }, (_, index) => Math.round(index * (length - 1) / (maximum - 1))))
}

// Tooltip strings are only assigned to DOM textContent, never parsed as HTML.
function literal(value: string): string {
  return value.replace(/[\r\n\t]/g, ' ')
}

/** Wrap comma groups without dropping digits, signs or decimal places. */
export function wrapChartValue(value: string, maximum = 42): string[] {
  const lines: string[] = []
  let line = ''
  for (const part of value.split(',')) {
    const token = line ? `,${part}` : part
    if (line && line.length + token.length > maximum) { lines.push(line + ','); line = part }
    else line += token
  }
  if (line) lines.push(line)
  return lines
}

export interface ChartTooltipLine { kind: 'period' | 'muted' | 'label' | 'value'; text: string }
export function chartTooltip(metric: Metric, label: string, unit: string, missing: string, compact = false): ChartTooltipLine[] {
  const dates = metric.period_start ? `${metric.period_start} → ${metric.period_end}` : metric.period_end
  const value = formatExact(metric.value)
  const values = value === 'N/A' ? ['N/A'] : wrapChartValue(value, compact ? 28 : 42)
  return [
    { kind: 'period', text: literal(chartPeriod(metric)) },
    { kind: 'muted', text: literal(dates) },
    { kind: 'label', text: literal(label) },
    ...values.map(text => ({ kind: 'value' as const, text })),
    { kind: 'muted', text: literal(value === 'N/A' ? missing : unit) },
  ]
}
