<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, Refresh, Document, InfoFilled, DataAnalysis, Warning } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '../api'
import type { Company, Filing, Metric, MetricsResponse, Page, SyncJob, ResearchTaskType } from '../types'
import { useSessionStore } from '../stores'
import SourceBadge from '../components/SourceBadge.vue'
import TrendChart from '../components/TrendChart.vue'
import FinancialOverview from '../components/FinancialOverview.vue'
import ResearchWorkbench from '../components/ResearchWorkbench.vue'
import { displayValue, displayTime, formatExact, missingReason, safeSecUrl } from '../utils'
import { locale, metricLabel, t, unitLabel } from '../i18n'

const route = useRoute()
const session = useSessionStore()
const cik = computed(() => String(route.params.cik))
const company = ref<Company | null>(null)
const metrics = ref<MetricsResponse | null>(null)
const filings = ref<Page<Filing>>({ items: [], total: 0, page: 1, page_size: 10 })
const events = ref<Page<Filing>>({ items: [], total: 0, page: 1, page_size: 10 })
const loading = ref(true)
const error = ref('')
const metricError = ref('')
const filingError = ref('')
const eventError = ref('')
const tableLoading = ref(false)
const activeTab = ref('overview')
const filingType = ref('')
const filingPage = ref(1)
const eventPage = ref(1)
const selectedPeriod = ref('')
const frequency = ref('annual')
const selectionPolicy = ref('latest_restated')
const asOf = ref('')
const accession = ref('')
const metricLoading = ref(false)
const chartMetric = ref('revenue')
const sourceMetric = ref<Metric | null>(null)
const sourceOpen = ref(false)
const job = ref<SyncJob | null>(null)
const syncing = ref(false)
const researchPreset = ref<{filing:Filing;type:ResearchTaskType;revision:number}|null>(null)
const researchChoices = ref<Filing[]>([])
const researchFilings = computed(()=>[...new Map([...filings.value.items,...events.value.items,...researchChoices.value].map(f=>[f.id,f])).values()])
function startResearch(filing:Filing,type:ResearchTaskType){researchChoices.value=[...researchChoices.value.filter(f=>f.id!==filing.id),filing];researchPreset.value={filing,type,revision:Date.now()};activeTab.value='ai'}
watch(cik,()=>{researchPreset.value=null;researchChoices.value=[]})
let pollTimer: ReturnType<typeof setTimeout> | undefined
let generation = 0
let metricGeneration = 0
function periodLabel(metric: Metric) { return metric.fiscal_year == null ? `${t('截至')} ${metric.period_end}` : `FY${metric.fiscal_year}${metric.fiscal_quarter ? ` Q${metric.fiscal_quarter}` : ''}` }
const periods = computed(() => [...new Map((metrics.value?.items || []).map(m => [m.period_end, { value: m.period_end, label: periodLabel(m) }])).values()].sort((a,b) => b.value.localeCompare(a.value)))
const selectedLabel = computed(() => periods.value.find(p => p.value === selectedPeriod.value)?.label || t('暂无期间'))
const currentMetrics = computed(() => (metrics.value?.items || []).filter(m => m.period_end === selectedPeriod.value))
const heroKeys = ['revenue', 'net_income', 'operating_cash_flow', 'assets', 'liabilities', 'gross_profit']
const metricCards = computed(() => heroKeys.map(key => currentMetrics.value.find(m => m.metric_key === key)).filter((m): m is Metric => !!m))
const previewFilings = computed(() => filings.value.items.slice(0, 3))
function yoyText(metric: Metric) { return metric.yoy == null ? 'N/A' : `${Number(metric.yoy) > 0 ? '+' : ''}${Number(metric.yoy).toLocaleString(locale.value, { maximumFractionDigits: 2 })}%` }
function yoyTone(metric: Metric) { return metric.yoy == null || Number(metric.yoy) === 0 ? '' : Number(metric.yoy) > 0 ? 'positive' : 'negative' }
const ratioMetrics = computed(() => currentMetrics.value.filter(m => ['percent', 'ratio'].includes(m.unit)))
const metricOptions = computed(() => [...new Map((metrics.value?.items || []).map(m => [m.metric_key, { key: m.metric_key, label: metricLabel(m.metric_key, m.label) }])).values()])
const sourceKind = computed(() => company.value?.source_kind || metrics.value?.source_kind || session.health?.source_kind)
const provenancePairs = computed(() => Object.entries(sourceMetric.value?.provenance || {}))
const jobLabel = computed(() => t({ queued: '同步任务已排队', running: '正在获取并规范化 SEC 数据', succeeded: '同步已完成', failed: '同步失败' }[job.value?.status || ''] || '正在提交任务'))
function showSource(metric: Metric) { sourceMetric.value = metric; sourceOpen.value = true }
function readable(value: unknown) { return typeof value === 'object' ? JSON.stringify(value, null, 2) : t(String(value ?? 'N/A')) }
function metricQuery() { return { frequency: frequency.value, selection_policy: selectionPolicy.value, ...(selectionPolicy.value === 'as_of' ? {as_of: asOf.value} : {}), ...(selectionPolicy.value === 'as_filed' ? {accession: accession.value.trim()} : {}) } }
function setMetrics(response: MetricsResponse) { metrics.value = response; if (!periods.value.some(p => p.value === selectedPeriod.value)) selectedPeriod.value = periods.value[0]?.value || ''; if (!metricOptions.value.some(m => m.key === chartMetric.value)) chartMetric.value = metricOptions.value[0]?.key || 'revenue' }
async function loadMetrics() {
  if (selectionPolicy.value === 'as_of' && !asOf.value) { metricError.value = '请选择截止披露日期。'; return }
  if (selectionPolicy.value === 'as_filed' && !accession.value.trim()) { metricError.value = '请输入指定申报的 accession 编号。'; return }
  const revision = generation
  const metricRevision = ++metricGeneration
  metricLoading.value = true; metricError.value = ''
  try { const response = await api.metrics(cik.value, metricQuery()); if (revision === generation && metricRevision === metricGeneration) setMetrics(response) }
  catch (cause) { if (revision === generation && metricRevision === metricGeneration) metricError.value = errorMessage(cause) }
  finally { if (revision === generation && metricRevision === metricGeneration) metricLoading.value = false }
}

async function load() {
  const revision = ++generation
  const selectedCik = cik.value
  loading.value = true; error.value = ''; metricError.value = ''; filingError.value = ''; eventError.value = ''
  const result = await Promise.allSettled([api.company(selectedCik), api.metrics(selectedCik, metricQuery()), api.filings(selectedCik, filingType.value, filingPage.value), api.events(selectedCik, eventPage.value)])
  if (revision !== generation) return
  const [companyResult, metricResult, filingResult, eventResult] = result
  if (companyResult.status === 'fulfilled') { company.value = companyResult.value; session.visit(companyResult.value) } else error.value = errorMessage(companyResult.reason)
  if (metricResult.status === 'fulfilled') setMetrics(metricResult.value); else metricError.value = errorMessage(metricResult.reason)
  if (filingResult.status === 'fulfilled') filings.value = filingResult.value; else filingError.value = errorMessage(filingResult.reason)
  if (eventResult.status === 'fulfilled') events.value = eventResult.value; else eventError.value = errorMessage(eventResult.reason)
  loading.value = false
}
async function loadFilings() { tableLoading.value = true; filingError.value = ''; try { filings.value = await api.filings(cik.value, filingType.value, filingPage.value) } catch (cause) { filingError.value = errorMessage(cause) } finally { tableLoading.value = false } }
async function loadEvents() { tableLoading.value = true; eventError.value = ''; try { events.value = await api.events(cik.value, eventPage.value) } catch (cause) { eventError.value = errorMessage(cause) } finally { tableLoading.value = false } }
async function poll(id: string, revision: number, attempt = 0) {
  try {
    const response = await api.job(id)
    if (revision !== generation) return
    job.value = response
    if (response.status === 'succeeded') { syncing.value = false; ElMessage.success(t('数据同步完成')); await load(); await session.checkHealth() }
    else if (response.status === 'failed') syncing.value = false
    else if (attempt >= 180) { syncing.value = false; ElMessage.info(t('同步仍在运行，可稍后刷新查看数据。')) }
    else pollTimer = setTimeout(() => poll(id, revision, attempt + 1), 2000)
  } catch (cause) { if (revision === generation) { syncing.value = false; ElMessage.error(t(errorMessage(cause))) } }
}
async function sync() {
  if (!session.adminToken) { ElMessage.warning(t('请先在设置页输入本地管理员访问令牌。')); return }
  syncing.value = true; job.value = null
  try { const response = await api.sync(cik.value, session.adminToken); job.value = response; const id = response.job_id || response.id; if (!id) throw new Error('同步任务未返回有效编号'); await poll(id, generation) }
  catch (cause) { syncing.value = false; ElMessage.error(t(errorMessage(cause))) }
}
watch(cik, () => { clearTimeout(pollTimer); syncing.value = false; job.value = null; selectedPeriod.value = ''; frequency.value = 'annual'; selectionPolicy.value = 'latest_restated'; company.value = null; metrics.value = null; filingPage.value = 1; eventPage.value = 1; activeTab.value = 'overview'; load() }, { immediate: true })
watch(frequency, () => { selectedPeriod.value = ''; if (company.value) loadMetrics() })
watch(filingType, () => { filingPage.value = 1; loadFilings() })
onBeforeUnmount(() => { clearTimeout(pollTimer); generation++ })
</script>
<template>
  <div class="page company-page">
    <RouterLink to="/" class="back-link"><el-icon><ArrowLeft /></el-icon>{{ t('全部公司') }}</RouterLink>
    <div v-if="loading && !company" class="panel loading-panel"><el-skeleton :rows="8" animated /></div>
    <div v-else-if="error" class="panel empty-state error-state">
      <el-icon><Warning /></el-icon><h3>{{ t('无法打开公司档案') }}</h3><p>{{ t(error) }}</p><el-button @click="load">{{ t('重新加载') }}</el-button>
    </div>
    <template v-else-if="company">
      <section class="company-header">
        <div class="company-heading"><div class="ticker-monogram large">{{ (company.ticker || company.name).slice(0, 1) }}</div><div><div class="company-title-row"><h1>{{ company.ticker || t('公司档案') }}</h1><span class="market-tag">{{ company.exchange || 'US' }}</span></div><p>{{ company.name }}</p></div></div>
        <div class="company-actions">
          <el-button :loading="syncing" :icon="Refresh" @click="sync">{{ t(sourceKind === 'sec_snapshot' ? '重新载入快照' : '同步 SEC 数据') }}</el-button>
          <el-button type="primary" @click="activeTab = 'ai'"><el-icon><DataAnalysis /></el-icon>{{ t('AI 财务概览') }}</el-button>
        </div>
      </section>
      <div class="company-meta">
        <span>CIK <b>{{ company.cik }}</b></span><span>{{ t('财年末') }} <b>{{ company.fiscal_year_end ? company.fiscal_year_end.slice(0, 2) + '/' + company.fiscal_year_end.slice(2) : 'N/A' }}</b></span>
        <span>{{ t(sourceKind === 'sec_snapshot' ? '元数据快照' : '最后同步') }} <b>{{ displayTime(company.last_synced_at) }}</b></span>
      </div>
      <div class="data-notice">
        <SourceBadge :kind="sourceKind" /><span v-if="sourceKind === 'sec_snapshot'" class="stale-hint">{{ t('历史归档 · 不代表最新披露') }}</span>
        <span v-else-if="company.is_stale" class="stale-hint"><el-icon><Warning /></el-icon>{{ t('缓存已过期，请同步') }}</span><span v-else class="muted">{{ t('数值均来自后端规范化') }}</span>
      </div>
      <el-alert v-if="job" class="job-alert" :title="jobLabel" :type="job.status === 'failed' ? 'error' : job.status === 'succeeded' ? 'success' : 'info'" :description="job.error_message ? t(job.error_message) : job.error_code ? `${t('错误代码：')} ${job.error_code}` : `${t('任务编号：')} ${job.job_id || job.id || '—'}`" :closable="!syncing" show-icon />
      <el-tabs v-model="activeTab" class="company-tabs">
        <el-tab-pane :label="t('财务概览')" name="overview">
          <div class="overview-controls">
            <div><h2>{{ t(frequency === 'annual' ? '年度财务表现' : '单季度财务表现') }}</h2><p>{{ t('按实际财年期间归属 · 所有派生值均保留来源和计算依据') }}</p></div>
            <div class="period-controls">
              <div class="segmented"><button :class="{selected: frequency === 'annual'}" @click="frequency = 'annual'">{{ t('年度') }}</button><button :class="{selected: frequency === 'quarterly'}" @click="frequency = 'quarterly'">{{ t('季度') }}</button></div>
              <el-select v-model="selectedPeriod" :aria-label="t('选择财务期间')" :placeholder="t('暂无期间')" style="width: 160px"><el-option v-for="period in periods" :key="period.value" :label="period.label" :value="period.value" /></el-select>
            </div>
          </div>
          <form class="version-controls panel" @submit.prevent="loadMetrics">
            <label>{{ t('数据口径') }}<el-select v-model="selectionPolicy" :aria-label="t('数据口径')" style="width:190px"><el-option :label="t('最新重述口径')" value="latest_restated" /><el-option :label="t('截至披露日期')" value="as_of" /><el-option :label="t('指定申报口径')" value="as_filed" /></el-select></label>
            <el-date-picker v-if="selectionPolicy === 'as_of'" v-model="asOf" type="date" value-format="YYYY-MM-DD" :aria-label="t('截止披露日期')" :placeholder="t('截止披露日期')" style="width:190px" />
            <el-input v-if="selectionPolicy === 'as_filed'" v-model="accession" :aria-label="t('申报编号')" placeholder="0000320193-25-000079" :maxlength="20" style="width:235px" />
            <el-button native-type="submit" :loading="metricLoading">{{ t('应用口径') }}</el-button>
            <span class="field-hint">{{ t(selectionPolicy === 'as_filed' ? '仅使用指定申报中披露的事实；缺少依据时显示 N/A。' : selectionPolicy === 'as_of' ? '只使用截止日期之前已披露的事实。' : '使用当前保存资料中的最新有效披露，不代表实时更新。') }}</span>
          </form>
          <div v-if="metricLoading" :aria-label="t('正在载入财务指标')" role="status"><div class="metrics-grid"><div v-for="i in 6" :key="i" class="metric-card"><el-skeleton :rows="3" animated /></div></div><div class="panel loading-panel" style="margin-top:24px"><el-skeleton :rows="6" animated /></div></div>
          <div v-else-if="metricError" class="empty-state error-state"><el-icon><Warning /></el-icon><h3>{{ t('重新加载财务数据') }}</h3><p>{{ t(metricError) }}</p><el-button @click="loadMetrics">{{ t('重试') }}</el-button></div>
          <div v-else-if="!metrics?.items.length" class="panel empty-state"><el-icon><DataAnalysis /></el-icon><h3>{{ t('尚无可用财务指标') }}</h3><p>{{ t('同步 SEC 数据，或检查该公司的披露是否包含可用的标准财务事实。') }}</p><el-button :loading="syncing" @click="sync">{{ t('同步数据') }}</el-button></div>
          <template v-else>
            <div class="metrics-grid">
              <button v-for="metric in metricCards" :key="metric.metric_key" class="metric-card" @click="showSource(metric)">
                <div class="metric-label"><span>{{ metricLabel(metric.metric_key, metric.label) }}</span><el-icon><InfoFilled /></el-icon></div>
                <div class="metric-value" :class="{ unavailable: metric.value == null }">{{ displayValue(metric.value, metric.unit) }}<span v-if="metric.value != null">{{ unitLabel(metric.unit) }}</span></div>
                <div class="metric-card-footer"><div class="metric-period">{{ metric.value == null ? missingReason(metric.missing_reason) : `${periodLabel(metric)} · ${metric.period_end}` }}</div><span class="metric-change" :class="yoyTone(metric)" :title="metric.yoy_reason ? missingReason(metric.yoy_reason) : t('基于可比上一年同期计算')">{{ yoyText(metric) }} <span>{{ t('同比') }}</span></span></div>
              </button>
            </div>
            <div v-if="ratioMetrics.length" class="ratio-grid"><button v-for="metric in ratioMetrics" :key="metric.metric_key" class="panel ratio-card" @click="showSource(metric)"><span>{{ metricLabel(metric.metric_key, metric.label) }}</span><strong>{{ displayValue(metric.value, metric.unit) }}</strong><small>{{ metric.value == null ? missingReason(metric.missing_reason) : t('查看计算依据 ↗') }}</small></button></div>
            <div class="overview-analysis">
<div class="panel chart-panel">
              <div class="panel-heading chart-heading"><div><span class="tiny-label">THE LONG VIEW</span><h2>{{ t('历史趋势') }}</h2></div><el-select v-model="chartMetric" :aria-label="t('选择趋势指标')" style="width: 210px"><el-option v-for="option in metricOptions" :key="option.key" :label="option.label" :value="option.key" /></el-select></div>
              <TrendChart :metrics="metrics.items" :metric-key="chartMetric" /><p class="chart-note">{{ t('显示最近可用财年；缺失值留空。各财年实际起止日期见指标来源，周历差异可能影响跨期比较。') }}</p>
            </div>
            <aside class="panel filing-preview"><div class="panel-heading"><div><span class="tiny-label">FILINGS</span><h2>{{ t('定期财务披露') }}</h2></div><el-icon><Document /></el-icon></div><div v-if="filingError" class="empty-state"><p>{{ t(filingError) }}</p><button class="source-link" @click="loadFilings">{{ t('重试') }}</button></div><div v-else-if="!previewFilings.length" class="empty-state"><p>{{ t('尚无定期财报，请先同步数据') }}</p></div><div v-for="filing in previewFilings" v-else :key="filing.id" class="filing-preview-item"><span class="form-tag">{{ filing.form_type }}</span><div><p>{{ t('报告期') }} {{ filing.report_date || 'N/A' }}</p><small>{{ t('披露日期') }} {{ filing.filing_date }}</small></div><button class="source-link" :aria-label="`${t('研究此财报')} ${filing.accession_number}`" @click="startResearch(filing, 'summary')">↗</button></div><div class="filing-preview-footer"><button class="source-link" @click="activeTab = 'filings'">{{ t('查看全部财报') }} <span>→</span></button></div></aside>
</div>
            <div class="panel metrics-table-panel">
              <div class="panel-heading"><div><h2>{{ t('指标明细') }} <span class="muted">{{ selectedLabel }}</span></h2><p>{{ t('原始精度展示，点击来源查看选取规则与申报信息。') }}</p></div><span class="small-badge">{{ t(metrics.selection_policy === 'latest_restated' ? '最新重述口径' : metrics.selection_policy === 'as_filed' ? '指定申报口径' : '截至披露日期') }}</span></div>
              <p class="table-scroll-hint">{{ t('横向滚动查看完整表格') }} →</p>
              <el-table :data="currentMetrics" style="width:100%">
                <el-table-column :label="t('财务指标')" min-width="190"><template #default="{ row }">{{ metricLabel(row.metric_key, row.label) }}</template></el-table-column>
                <el-table-column :label="t('精确数值')" min-width="220" align="right"><template #default="{ row }"><span class="number-cell" :title="row.value ?? 'N/A'">{{ formatExact(row.value) }}</span></template></el-table-column>
                <el-table-column :label="t('单位')" width="115"><template #default="{ row }">{{ unitLabel(row.unit) }}</template></el-table-column>
                <el-table-column :label="t('同比')" width="130" align="right"><template #default="{ row }"><span class="number-cell" :title="row.yoy_reason ? missingReason(row.yoy_reason) : t('基于可比上一年同期计算')">{{ row.yoy == null ? 'N/A' : Number(row.yoy).toLocaleString(locale, {maximumFractionDigits:2}) + '%' }}</span></template></el-table-column>
                <el-table-column prop="period_end" :label="t('财务期末')" width="135" />
                <el-table-column :label="t('数据来源')" width="120" align="right"><template #default="{ row }"><button class="source-link" @click="showSource(row)">{{ t('查看来源') }} ↗</button></template></el-table-column>
              </el-table>
            </div>
          </template>
        </el-tab-pane>
        <el-tab-pane :label="t('财报列表')" name="filings">
          <div class="overview-controls"><div><h2>{{ t('定期财务披露') }}</h2><p>{{ t('按披露日期排序 · 日期沿用 SEC 申报记录，不作时区换算') }}</p></div><el-select v-model="filingType" :aria-label="t('筛选财报类型')" :placeholder="t('全部定期报告')" style="width: 190px"><el-option :label="t('全部定期报告')" value="" /><el-option :label="t('10-K 年报')" value="10-K" /><el-option :label="t('10-Q 季报')" value="10-Q" /></el-select></div>
          <div v-if="filingError" class="empty-state error-state"><p>{{ t(filingError) }}</p><el-button @click="loadFilings">{{ t('重试') }}</el-button></div>
          <div v-else class="panel filing-panel">
            <el-table v-loading="tableLoading" :data="filings.items" :empty-text="t('尚无定期财报，请先同步数据')">
              <el-table-column :label="t('表单')" width="135"><template #default="{ row }"><span class="form-tag">{{ row.form_type }}</span><span v-if="row.amended" class="amended-label">{{ t('修订') }}</span></template></el-table-column>
              <el-table-column prop="report_date" :label="t('报告期间')" width="145" /><el-table-column prop="filing_date" :label="t('披露日期')" width="140" /><el-table-column prop="accession_number" :label="t('申报编号')" min-width="200" />
              <el-table-column :label="t('研究')" width="130"><template #default="{row}"><button class="source-link" @click="startResearch(row,'summary')">{{ t('分析此份财报') }} ↗</button></template></el-table-column>
              <el-table-column :label="t('原始披露')" width="140" align="right"><template #default="{ row }"><a v-if="safeSecUrl(row.source_url)" :href="safeSecUrl(row.source_url)!" target="_blank" rel="noopener noreferrer" class="source-link">{{ t('SEC 原文') }} ↗</a><span v-else>N/A</span></template></el-table-column>
            </el-table>
            <div class="pagination-row"><span>{{ t('共') }} {{ filings.total.toLocaleString(locale) }} {{ t('份报告') }}</span><el-pagination v-model:current-page="filingPage" layout="prev, pager, next" :page-size="filings.page_size || 10" :total="filings.total" @current-change="loadFilings" /></div>
          </div>
        </el-tab-pane>
        <el-tab-pane :label="t('金融事件')" name="events">
          <div class="overview-controls"><div><h2>{{ t('8-K 事件披露') }}</h2><p>{{ t('事件元数据来自 SEC；事项本身不代表确定的股价影响。') }}</p></div><span class="small-badge">{{ t('披露日期 · SEC 原始日期') }}</span></div>
          <div v-if="eventError" class="empty-state error-state"><p>{{ t(eventError) }}</p><el-button @click="loadEvents">{{ t('重试') }}</el-button></div>
          <div v-else class="panel filing-panel">
            <el-table v-loading="tableLoading" :data="events.items" :empty-text="t('尚无事件披露，请先同步数据')">
              <el-table-column :label="t('事件表单')" width="140"><template #default="{ row }"><span class="form-tag event">{{ row.form_type }}</span></template></el-table-column>
              <el-table-column prop="filing_date" :label="t('披露日期')" width="140" /><el-table-column :label="t('披露项目')" min-width="200"><template #default="{ row }">{{ Array.isArray(row.items) ? row.items.join(' · ') : row.items || t('未提供事项编号') }}</template></el-table-column>
              <el-table-column :label="t('研究')" width="130"><template #default="{row}"><button class="source-link" @click="startResearch(row,'event')">{{ t('解读事件') }} ↗</button></template></el-table-column>
              <el-table-column :label="t('原始披露')" width="140" align="right"><template #default="{ row }"><a v-if="safeSecUrl(row.source_url)" :href="safeSecUrl(row.source_url)!" target="_blank" rel="noopener noreferrer" class="source-link">{{ t('SEC 原文') }} ↗</a><span v-else>N/A</span></template></el-table-column>
            </el-table>
            <div class="pagination-row"><span>{{ t('共') }} {{ events.total.toLocaleString(locale) }} {{ t('条事件') }}</span><el-pagination v-model:current-page="eventPage" layout="prev, pager, next" :page-size="events.page_size || 10" :total="events.total" @current-change="loadEvents" /></div>
          </div>
        </el-tab-pane>
        <el-tab-pane :label="t('AI 研究')" name="ai" lazy><ResearchWorkbench :key="`research-${cik}`" :cik="cik" :filings="researchFilings" :preset="researchPreset" /><FinancialOverview :key="cik" :cik="cik" :query="metricQuery()" /></el-tab-pane>
      </el-tabs>
    </template>
    <el-drawer v-model="sourceOpen" :title="t('指标来源与计算口径')" size="min(600px, 94vw)">
      <template v-if="sourceMetric">
        <SourceBadge :kind="sourceKind" /><div class="source-metric-title"><span>{{ metricLabel(sourceMetric.metric_key, sourceMetric.label) }} · {{ periodLabel(sourceMetric) }}</span><strong class="number-cell">{{ formatExact(sourceMetric.value) }} <small>{{ unitLabel(sourceMetric.unit) }}</small></strong></div>
        <dl class="source-definition"><dt>{{ t('财务期间') }}</dt><dd>{{ sourceMetric.period_start || t('期末时点值') }} → {{ sourceMetric.period_end }}</dd><dt>{{ t('计算 / 选择方式') }}</dt><dd>{{ sourceMetric.calc_method }}</dd><template v-if="sourceMetric.missing_reason"><dt>{{ t('缺失原因') }}</dt><dd>{{ missingReason(sourceMetric.missing_reason) }}</dd></template></dl>
        <div class="drawer-divider"></div><h3 class="drawer-section-title"><el-icon><Document /></el-icon>{{ t('原始事实与申报') }}</h3>
        <dl class="source-definition"><template v-for="[key, value] in provenancePairs" :key="key"><dt>{{ key }}</dt><dd><a v-if="safeSecUrl(value)" :href="safeSecUrl(value)!" target="_blank" rel="noopener noreferrer" class="source-link">{{ t('打开 SEC 来源') }} ↗</a><pre v-else-if="typeof value === 'object'">{{ readable(value) }}</pre><span v-else>{{ readable(value) }}</span></dd></template></dl>
        <p class="field-hint">{{ t('数值由后端保存和计算。来源存在不代表 AI 结论已经得到验证。') }}</p>
      </template>
    </el-drawer>
  </div>
</template>
