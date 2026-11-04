import { describe, expect, it } from 'vitest'
import type { Metric } from './types'
import { chartLabelIndices, chartPeriod, chartRows, chartTooltip, chartValue, wrapChartValue } from './chart'

const metric = (overrides: Partial<Metric> = {}): Metric => ({
  metric_key: 'revenue', label: 'Revenue', period_type: 'annual', fiscal_year: 2025,
  period_start: '2024-09-29', period_end: '2025-09-27', value: '416161000000',
  unit: 'USD', calc_method: 'test-only', provenance: {}, missing_reason: null, ...overrides,
})

describe('Financial trend presentation', () => {
  it('scales only USD plot coordinates and keeps zero distinct from missing', () => {
    expect(chartValue(metric())).toBe(416.161)
    expect(chartValue(metric({ value: '7.4600', unit: 'USD/shares' }))).toBe(7.46)
    expect(chartValue(metric({ value: '31.97079976', unit: 'percent' }))).toBe(31.97079976)
    expect(chartValue(metric({ value: '0' }))).toBe(0)
    for (const value of [null, '', 'NaN', 'Infinity', '1e9']) expect(chartValue(metric({ value }))).toBeNull()
  })
  it('orders the selected series without mutating authoritative inputs', () => {
    const input = [metric(), metric({ period_end: '2023-09-30', fiscal_year: 2023 }), metric({ metric_key: 'assets' })]
    expect(chartRows(input, 'revenue').map(row => row.fiscal_year)).toEqual([2023, 2025])
    expect(input.map(row => row.fiscal_year)).toEqual([2025, 2023, 2025])
    expect(chartPeriod(metric({ period_type: 'quarterly', fiscal_quarter: 3 }))).toBe('FY2025 Q3')
  })
  it('shows exact strings, units and actual dates in tooltip text', () => {
    const result = chartTooltip(metric({ value: '9007199254740993.1200' }), 'Revenue', 'USD', 'Missing').map(line => line.text).join('\n')
    expect(result).toContain('9,007,199,254,740,993.1200')
    expect(result).toContain('2024-09-29 → 2025-09-27')
    expect(result).toContain('USD')
    expect(result).not.toContain('9,007,199,254,740,994')
    expect(chartTooltip(metric({ value: null }), 'Revenue', 'USD', 'Not disclosed')).toContainEqual({ kind: 'muted', text: 'Not disclosed' })
  })
  it('separates untrusted text from fixed style kinds and wraps figures without losing digits', () => {
    const result = chartTooltip(metric(), '{value|spoof}\n<img onerror=alert(1)>', 'USD', 'Missing')
    expect(result).toContainEqual({ kind: 'label', text: '{value|spoof} <img onerror=alert(1)>' })
    expect(new Set(result.map(line => line.kind))).toEqual(new Set(['period', 'muted', 'label', 'value']))
    const value = '-123,456,789,012,345,678,901,234,567.890000'
    const lines = wrapChartValue(value, 16)
    expect(lines.length).toBeGreaterThan(1)
    expect(lines.join('')).toBe(value)
  })
  it('keeps first/last periods visible with bounded mobile label density', () => {
    const indices = chartLabelIndices(20, 320)
    expect(indices.size).toBeLessThanOrEqual(3)
    expect(indices.has(0)).toBe(true)
    expect(indices.has(19)).toBe(true)
    expect(chartLabelIndices(5, 900).size).toBe(5)
    expect(chartLabelIndices(0, 0).size).toBe(0)
  })
})
