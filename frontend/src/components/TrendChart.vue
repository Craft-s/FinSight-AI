<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import * as echarts from 'echarts/core'
import { BarChart, LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { Metric } from '../types'
import { locale, metricLabel, t, unitLabel } from '../i18n'
import { resolvedTheme } from '../theme'
import { missingReason } from '../utils'
import { chartLabelIndices, chartPeriod, chartRows, chartTooltip, chartValue } from '../chart'

echarts.use([BarChart, LineChart, GridComponent, TooltipComponent, LegendComponent, CanvasRenderer])
const props = defineProps<{ metrics: Metric[]; metricKey: string; market?: 'US'|'CN' }>()
const element = ref<HTMLElement>()
const reducedMotion = ref(false)
const rows = computed(() => chartRows(props.metrics, props.metricKey))
const hasValues = computed(() => rows.value.some(row => chartValue(row) !== null))
const description = computed(() => `${metricLabel(props.metricKey, rows.value[0]?.label, props.market)} · ${t(rows.value[0]?.period_type === 'quarterly' ? '单季度' : rows.value[0]?.period_type === 'cumulative' ? '年初至今' : '年度')} · ${t('趋势图；精确数值见下方表格')}`)
let chart: echarts.ECharts | undefined
let observer: ResizeObserver | undefined
let media: MediaQueryList | undefined
let frame = 0

function render() {
  if (!chart || !element.value) return
  const data = rows.value
  const width = element.value.clientWidth
  const compact = width < 520
  const style = getComputedStyle(element.value)
  const dark = resolvedTheme.value === 'dark'
  const token = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback
  const primary = token('--chart-primary', dark ? '#8ca0ff' : '#4f6bed')
  const soft = token('--chart-primary-soft', dark ? '#5669ad' : '#b4c1ff')
  const text = token('--text-primary', dark ? '#ecf0f7' : '#182230')
  const secondary = token('--text-secondary', dark ? '#a4afbf' : '#5e6d82')
  const tertiary = token('--text-tertiary', dark ? '#8692a3' : '#718096')
  const border = token('--border-subtle', dark ? '#293240' : '#e7ecf3')
  const surface = token('--surface-raised', dark ? '#202733' : '#ffffff')
  const family = style.fontFamily
  const labels = chartLabelIndices(data.length, width)
  const currency = ['USD','CNY'].includes(data[0]?.unit || '')
  const isBar = currency || ['assets', 'liabilities', 'equity', 'cash'].includes(props.metricKey)
  const name = metricLabel(props.metricKey, data[0]?.label, props.market)
  const axisUnit = currency ? t(data[0]?.unit === 'CNY' ? '十亿元人民币' : '十亿美元') : unitLabel(data[0]?.unit || '')
  const series = {
    name,
    data: data.map(row => ({ value: chartValue(row), itemStyle: { borderRadius: Number(row.value) < 0 ? [0, 0, 3, 3] : [3, 3, 0, 0] } })),
    emphasis: { focus: 'series' },
  }
  chart.setOption({
    animation: !reducedMotion.value, animationDuration: 280, animationDurationUpdate: 180,
    backgroundColor: 'transparent', textStyle: { fontFamily: family }, color: [primary],
    grid: { left: 16, right: compact ? 18 : 26, top: 60, bottom: 14, containLabel: true },
    legend: { show: true, left: 18, top: 2, selectedMode: false, icon: 'roundRect', itemWidth: 12, itemHeight: 4, textStyle: { color: secondary, fontSize: 12 }, data: [name] },
    tooltip: {
      trigger: 'axis', renderMode: 'html', confine: true, enterable: false,
      transitionDuration: reducedMotion.value ? 0 : 0.1,
      backgroundColor: surface, borderColor: border, borderWidth: 1, padding: [14, 16],
      shadowBlur: 18, shadowColor: dark ? 'rgba(0,0,0,.2)' : 'rgba(24,34,48,.09)', shadowOffsetY: 5,
      axisPointer: { type: isBar ? 'shadow' : 'line', shadowStyle: { color: dark ? 'rgba(140,160,255,.06)' : 'rgba(79,107,237,.045)' }, lineStyle: { color: tertiary, type: 'dashed', width: 1 } },
      textStyle: { color: text, fontFamily: family, fontSize: 12, lineHeight: 20 },
      formatter: (parameters: { dataIndex: number } | { dataIndex: number }[]) => {
        const index = (Array.isArray(parameters) ? parameters[0] : parameters)?.dataIndex
        const metric = data[index]
        if (!metric) return ''
        const content = document.createElement('div')
        content.className = 'chart-tooltip'
        content.style.maxWidth = `${Math.max(140, Math.min(320, width - 56))}px`
        for (const line of chartTooltip(metric, name, unitLabel(metric.unit), missingReason(metric.missing_reason), compact)) {
          const span = document.createElement('span')
          span.className = `chart-tooltip__${line.kind}`
          span.textContent = line.text
          content.appendChild(span)
        }
        return content
      },
    },
    xAxis: {
      type: 'category', data: data.map(chartPeriod), boundaryGap: isBar,
      axisLine: { show: true, lineStyle: { color: border } }, axisTick: { show: false },
      axisLabel: { color: secondary, fontSize: 11, margin: 16, hideOverlap: true, interval: (index: number) => labels.has(index) },
    },
    yAxis: {
      type: 'value', name: axisUnit, nameGap: 18,
      nameTextStyle: { color: tertiary, fontSize: 11, align: 'left', padding: [0, 0, 0, -2] },
      axisLabel: { color: tertiary, fontSize: 11, margin: 12, formatter: (value: number) => value.toLocaleString(locale.value, { maximumFractionDigits: Math.abs(value) >= 100 ? 0 : 2 }) },
      axisLine: { show: false }, axisTick: { show: false },
      splitLine: { lineStyle: { color: border, type: 'dashed', width: 1 } }, splitNumber: 4,
    },
    series: [isBar ? { ...series, type: 'bar', barMaxWidth: compact ? 32 : 42, barWidth: '48%',
      itemStyle: { color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [{ offset: 0, color: primary }, { offset: 1, color: soft }]) },
    } : { ...series, type: 'line', connectNulls: false, smooth: false, showSymbol: data.length < 14,
      symbol: 'circle', symbolSize: 6, lineStyle: { width: 2.5, color: primary },
      itemStyle: { color: surface, borderColor: primary, borderWidth: 2 },
    }],
  }, { notMerge: true, lazyUpdate: true })
}

function resize() {
  cancelAnimationFrame(frame)
  frame = requestAnimationFrame(() => { chart?.resize(); render() })
}
function motionChanged(event: MediaQueryListEvent) { reducedMotion.value = event.matches }
onMounted(() => {
  if (!element.value) return
  media = window.matchMedia('(prefers-reduced-motion: reduce)')
  reducedMotion.value = media.matches
  media.addEventListener('change', motionChanged)
  chart = echarts.init(element.value)
  observer = new ResizeObserver(resize)
  observer.observe(element.value)
  render()
})
watch(() => [props.metrics, props.metricKey, locale.value, resolvedTheme.value, reducedMotion.value], render, { deep: true, flush: 'post' })
onBeforeUnmount(() => { observer?.disconnect(); media?.removeEventListener('change', motionChanged); cancelAnimationFrame(frame); chart?.dispose() })
</script>

<template>
  <div class="trend-chart-region">
    <div ref="element" class="trend-chart" :class="{ 'chart-is-empty': !hasValues }" role="img" :aria-label="description"></div>
    <div v-if="!hasValues" class="chart-empty" role="status"><span class="chart-empty-mark" aria-hidden="true">—</span><p>{{ t('未找到可用数据') }}</p><small>{{ t('趋势图；精确数值见下方表格') }}</small></div>
  </div>
</template>

<style scoped>
.trend-chart-region { position: relative; min-width: 0; }
.trend-chart { height: 312px; width: 100%; min-width: 0; }
.trend-chart :deep(.chart-tooltip) { display: flex; flex-direction: column; gap: 3px; min-width: 140px; text-align: left; white-space: normal; line-height: 1.55; }
.trend-chart :deep(.chart-tooltip__period) { font-size: 12px; font-weight: 600; color: var(--text-primary); }
.trend-chart :deep(.chart-tooltip__muted) { font-size: 11px; color: var(--text-tertiary); overflow-wrap: anywhere; }
.trend-chart :deep(.chart-tooltip__label) { margin-top: 7px; font-size: 12px; color: var(--text-secondary); overflow-wrap: anywhere; }
.trend-chart :deep(.chart-tooltip__value) { font-size: 15px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--text-primary); overflow-wrap: anywhere; }
.chart-is-empty { visibility: hidden; }
.chart-empty { position: absolute; inset: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 8px; color: var(--text-secondary); }
.chart-empty p { font-size: 13px; }
.chart-empty small { font-size: 11px; color: var(--text-tertiary); }
.chart-empty-mark { display: grid; place-items: center; width: 36px; height: 36px; border: 1px solid var(--border-subtle); border-radius: 8px; margin-bottom: 4px; }
@media (max-width: 600px) { .trend-chart { height: 280px; } }
</style>
