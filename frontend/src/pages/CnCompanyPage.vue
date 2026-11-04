<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { ArrowLeft, Refresh, InfoFilled, Warning, Document, DataAnalysis } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '../api'
import { locale, metricLabel, t, unitLabel } from '../i18n'
import { cnSearchCompany, comparableCnTrend } from '../market'
import { useSessionStore } from '../stores'
import type { CnCompany, CnFiling, CnMetrics, CnSource, CnResearchTaskType, Metric, Page, SyncJob } from '../types'
import { displayTime, displayValue, formatExact, missingReason, safeCnSourceUrl } from '../utils'
import TrendChart from '../components/TrendChart.vue'
import SourceBadge from '../components/SourceBadge.vue'
import CnResearchWorkbench from '../components/CnResearchWorkbench.vue'

const cnMetricLabel = (key: string, fallback?: string) => metricLabel(key, fallback, 'CN')
const formLabel = (type: string) => t(({annual:'年报',semiannual:'半年报',q1:'一季报',q3:'三季报'} as Record<string,string>)[type] || '其他财报公告')
const route = useRoute()
const session = useSessionStore()
const symbol = computed(() => String(route.params.symbol))
const company = ref<CnCompany | null>(null)
const metrics = ref<CnMetrics | null>(null)
const filings = ref<Page<CnFiling> & {limitations?:string[]}>({items:[],total:0,page:1,page_size:10})
const frequency = ref<'annual'|'cumulative'>('annual')
const selectedPeriod = ref('')
const chartMetric = ref('cn_total_revenue')
const activeTab = ref('overview')
const researchPreset = ref<{filing:CnFiling;type:CnResearchTaskType;revision:number}|null>(null)
function openResearch(filing:CnFiling) { researchPreset.value={filing,type:['annual','semiannual','q1','q3'].includes(filing.form_type)?'summary':'question',revision:(researchPreset.value?.revision||0)+1};activeTab.value='research' }
const loading = ref(true)
const metricLoading = ref(false)
const filingLoading = ref(false)
const error = ref('')
const metricError = ref('')
const filingError = ref('')
const running = ref(false)
const job = ref<SyncJob | null>(null)
const sourceMetric = ref<Metric | null>(null)
const sourceOpen = ref(false)
const sources = ref<CnSource[]>([])
const sourceError = ref('')
const sourceLoading = ref(false)
let generation = 0
let metricGeneration = 0
let filingGeneration = 0
let sourceGeneration = 0
let timer: ReturnType<typeof setTimeout> | undefined
const periods = computed(() => [...new Set((metrics.value?.items || []).map(m=>m.period_end))].sort().reverse())
const currentMetrics = computed(() => (metrics.value?.items || []).filter(m=>m.period_end === selectedPeriod.value))
const trendMetrics = computed(() => comparableCnTrend(metrics.value?.items || [], frequency.value, selectedPeriod.value))
const heroKeys = ['cn_total_revenue','parent_net_income','operating_cash_flow','assets','liabilities','equity']
const cards = computed(() => heroKeys.map(key=>currentMetrics.value.find(m=>m.metric_key===key)).filter((m):m is Metric=>!!m))
const ratios = computed(() => currentMetrics.value.filter(m=>['percent','ratio'].includes(m.unit)))
const options = computed(() => [...new Map((metrics.value?.items || []).map(m=>[m.metric_key,m])).values()])
const jobText = computed(() => t(job.value?.status === 'succeeded' ? 'A 股同步完成' : job.value?.status === 'failed' ? 'A 股同步失败' : job.value?.status === 'running' ? '正在读取 A 股公开资料' : '同步任务已排队'))
function setMetrics(value: CnMetrics) { metrics.value=value; if(!periods.value.includes(selectedPeriod.value)) selectedPeriod.value=periods.value[0] || ''; if (!options.value.some(m=>m.metric_key===chartMetric.value)) chartMetric.value=options.value[0]?.metric_key || 'cn_total_revenue' }
function yoy(value: string | null | undefined) { return value == null ? 'N/A' : `${Number(value)>0?'+':''}${Number(value).toLocaleString(locale.value,{maximumFractionDigits:2})}%` }
function tone(value: string | null | undefined) { return value == null || Number(value)===0 ? '' : Number(value)>0?'positive':'negative' }
async function load() {
  const revision=++generation
  loading.value=true; error.value=''; metricError.value=''; filingError.value=''
  const result=await Promise.allSettled([api.cnCompany(symbol.value),api.cnMetrics(symbol.value,frequency.value),api.cnFilings(symbol.value)])
  if(revision!==generation)return
  if(result[0].status==='fulfilled'){company.value=result[0].value;session.visit(cnSearchCompany(result[0].value))}else error.value=errorMessage(result[0].reason)
  if(result[1].status==='fulfilled')setMetrics(result[1].value);else metricError.value=errorMessage(result[1].reason)
  if(result[2].status==='fulfilled')filings.value=result[2].value;else filingError.value=errorMessage(result[2].reason)
  loading.value=false
}
async function loadMetrics() {
  const revision=generation, current=++metricGeneration
  metricLoading.value=true;metricError.value=''
  try{const value=await api.cnMetrics(symbol.value,frequency.value);if(revision===generation&&current===metricGeneration)setMetrics(value)}
  catch(cause){if(revision===generation&&current===metricGeneration)metricError.value=errorMessage(cause)}
  finally{if(revision===generation&&current===metricGeneration)metricLoading.value=false}
}
async function loadFilings(page=1) {
  const revision=generation, current=++filingGeneration
  filingLoading.value=true;filingError.value=''
  try{const value=await api.cnFilings(symbol.value,page);if(revision===generation&&current===filingGeneration)filings.value=value}
  catch(cause){if(revision===generation&&current===filingGeneration)filingError.value=errorMessage(cause)}
  finally{if(revision===generation&&current===filingGeneration)filingLoading.value=false}
}
async function poll(id:string, revision:number, attempt=0) {
  try{
    const value=await api.cnJob(id,session.adminToken)
    if(revision!==generation)return
    job.value=value
    if(value.status==='succeeded'){running.value=false;ElMessage.success(t('A 股同步完成'));await load()}
    else if(value.status==='failed')running.value=false
    else if(attempt>=180){running.value=false;ElMessage.info(t('同步仍在运行，可稍后刷新查看数据。'))}
    else timer=setTimeout(()=>poll(id,revision,attempt+1),2000)
  }catch(cause){if(revision===generation){running.value=false;ElMessage.error(t(errorMessage(cause)))}}
}
async function sync() {
  if(!session.adminToken){ElMessage.warning(t('请先在设置页输入本地管理员访问令牌。'));return}
  running.value=true;job.value=null
  const revision=generation
  try{const value=await api.cnSync(symbol.value,session.adminToken);if(revision===generation)await poll(value.job_id,revision)}
  catch(cause){if(revision===generation){running.value=false;ElMessage.error(t(errorMessage(cause)))}}
}
async function showSource(metric:Metric) {
  sourceMetric.value=metric;sourceOpen.value=true;sources.value=[];sourceError.value='';sourceLoading.value=false
  const revision=++sourceGeneration
  const ids = new Set<string>()
  const collect=(value: unknown) => { if(typeof value==='string')ids.add(value) }
  collect(metric.provenance.source_snapshot_id)
  if(Array.isArray(metric.provenance.source_snapshot_ids))metric.provenance.source_snapshot_ids.forEach(collect)
  if(Array.isArray(metric.provenance.source_facts))for(const fact of metric.provenance.source_facts){if(fact&&typeof fact==='object'&&'source_snapshot_id' in fact)collect(fact.source_snapshot_id)}
  if(!ids.size)return
  sourceLoading.value=true
  try{const values=await Promise.all([...ids].map(id=>api.cnSource(id)));if(revision===sourceGeneration)sources.value=values}
  catch(cause){if(revision===sourceGeneration)sourceError.value=errorMessage(cause)}
  finally{if(revision===sourceGeneration)sourceLoading.value=false}
}
watch(symbol,()=>{clearTimeout(timer);running.value=false;job.value=null;company.value=null;metrics.value=null;filings.value={items:[],total:0,page:1,page_size:10};selectedPeriod.value='';activeTab.value='overview';researchPreset.value=null;sourceOpen.value=false;load()},{immediate:true})
watch(frequency,()=>{selectedPeriod.value='';if(company.value)loadMetrics()})
onBeforeUnmount(()=>{generation++;sourceGeneration++;clearTimeout(timer)})
</script>
<template>
  <div class="page company-page cn-company-page">
    <RouterLink to="/" class="back-link"><el-icon><ArrowLeft /></el-icon>{{ t('全部公司') }}</RouterLink>
    <div v-if="loading && !company" class="panel loading-panel" role="status" :aria-label="t('正在载入 A 股公司')"><el-skeleton :rows="9" animated /></div>
    <div v-else-if="error" class="panel empty-state error-state"><el-icon><Warning /></el-icon><h3>{{ t('无法打开 A 股公司') }}</h3><p>{{ t(error) }}</p><el-button @click="load">{{ t('重新加载') }}</el-button></div>
    <template v-else-if="company">
      <section class="company-header">
        <div class="company-title-row"><div class="company-monogram">{{ company.name.slice(0,1) }}</div><div><div class="company-title-main"><h1>{{ company.name }}</h1><span class="exchange-badge">{{ company.exchange }}</span></div><p>{{ company.symbol }} · {{ t('A 股') }}</p></div><div class="company-actions"><el-button :icon="Refresh" :loading="running" @click="sync">{{ t('同步最新 A 股资料') }}</el-button></div></div>
        <div class="company-meta"><span>{{ t('证券代码') }} <b>{{ company.symbol }}</b></span><span>{{ t('记账币种') }} <b>{{ unitLabel('CNY') === 'CNY' ? 'CNY' : `${unitLabel('CNY')} · CNY` }}</b></span><span>{{ t('最后同步') }} <b>{{ displayTime(company.last_synced_at) }}</b></span></div>
        <div class="data-notice"><SourceBadge :kind="company.source_kind" /><span class="stale-hint">{{ t('以来源同步时间为准，不是实时行情。') }}</span></div>
      </section>
      <div v-if="job" class="panel cn-job-state" role="status"><span class="job-status" :class="job.status">{{ jobText }}</span><p v-if="job.error_message" class="cn-inline-error">{{ t(job.error_message) }}</p></div>
      <el-tabs v-model="activeTab" class="company-tabs">
        <el-tab-pane :label="t('财务概览')" name="overview">
          <div class="overview-controls"><div><h2>{{ t(frequency==='annual'?'年度财务表现':'年初至今财务表现') }}</h2><p>{{ t('中国企业会计准则口径 · 第三方最新已保存数据') }}</p></div><div class="period-controls"><el-radio-group v-model="frequency" :aria-label="t('A 股财务期间')"><el-radio-button value="annual">{{ t('年度') }}</el-radio-button><el-radio-button value="cumulative">{{ t('年初至今') }}</el-radio-button></el-radio-group><el-select v-model="selectedPeriod" :aria-label="t('选择财务期间')" style="width:160px"><el-option v-for="period in periods" :key="period" :label="period" :value="period" /></el-select></div></div>
          <div class="panel cn-basis-note"><span class="small-badge">{{ t('最新供应商快照') }}</span><p>{{ t(frequency==='annual'?'年度为完整会计年度，资产负债类为期末时点。':'收入、利润与现金流为当年一月一日起累计值，不能当作单季度；资产负债类为期末时点。') }}</p><p>{{ t('暂不提供历史当时已知口径；不将中国准则指标与美股同名指标视为完全可比。') }}</p><p>{{ t('报表合并范围尚未逐份与 PDF 核对。') }}</p></div>
          <div v-if="metricLoading" class="metrics-grid"><div v-for="i in 6" :key="i" class="metric-card"><el-skeleton :rows="3" animated /></div></div>
          <div v-else-if="metricError" class="panel empty-state error-state"><el-icon><Warning /></el-icon><h3>{{ t('财务资料暂时不可用') }}</h3><p>{{ t(metricError) }}</p><el-button @click="loadMetrics">{{ t('重试') }}</el-button></div>
          <div v-else-if="!metrics?.items.length" class="panel empty-state"><el-icon><DataAnalysis /></el-icon><h3>{{ t('尚无 A 股财务资料') }}</h3><p>{{ t('授权同步真实公开资料后展示，缺失值不会由示例数据填充。') }}</p><el-button :loading="running" @click="sync">{{ t('同步最新 A 股资料') }}</el-button></div>
          <template v-else>
            <div class="metrics-grid"><button v-for="metric in cards" :key="metric.metric_key" class="metric-card" @click="showSource(metric)"><div class="metric-label"><span>{{ cnMetricLabel(metric.metric_key,metric.label) }}</span><el-icon><InfoFilled /></el-icon></div><div class="metric-value" :class="{unavailable:metric.value==null}">{{ displayValue(metric.value,metric.unit) }}<span v-if="metric.value!=null">{{ unitLabel(metric.unit) }}</span></div><div class="metric-card-footer"><div class="metric-period">{{ metric.period_end }}</div><span class="metric-change" :class="tone(metric.yoy)" :title="metric.yoy_reason?missingReason(metric.yoy_reason):t('基于可比上一年同期计算')">{{ yoy(metric.yoy) }} {{ t('同比') }}</span></div></button></div>
            <div v-if="ratios.length" class="ratio-grid"><button v-for="metric in ratios" :key="metric.metric_key" class="panel ratio-card" @click="showSource(metric)"><span>{{ cnMetricLabel(metric.metric_key,metric.label) }}</span><strong>{{ displayValue(metric.value,metric.unit) }}</strong><small>{{ metric.value==null?missingReason(metric.missing_reason):t('查看计算依据 ↗') }}</small></button></div>
            <section class="panel chart-panel"><div class="panel-heading chart-heading"><div><span class="tiny-label">{{ t('人民币财务趋势') }}</span><h2>{{ t(frequency === 'cumulative' ? '历年相同累计期比较' : '历史趋势') }}</h2></div><el-select v-model="chartMetric" :aria-label="t('选择趋势指标')" style="width:220px"><el-option v-for="metric in options" :key="metric.metric_key" :label="cnMetricLabel(metric.metric_key,metric.label)" :value="metric.metric_key" /></el-select></div><TrendChart :metrics="trendMetrics" :metric-key="chartMetric" market="CN" /><p class="chart-note">{{ t(frequency === 'cumulative' ? '仅比较历年相同累计截至日，不混合不同累计长度。' : '显示已保存年度序列；缺失值留空。') }}</p></section>
            <section class="panel metrics-table-panel"><div class="panel-heading"><div><h2>{{ t('指标明细') }} <span class="muted">{{ selectedPeriod }}</span></h2><p>{{ t('原始精度展示，点击来源查看选取规则与申报信息。') }}</p></div><span class="small-badge">CNY</span></div><p class="table-scroll-hint">{{ t('横向滚动查看完整表格') }} →</p><el-table :data="currentMetrics"><el-table-column :label="t('财务指标')" min-width="220"><template #default="{row}">{{ cnMetricLabel(row.metric_key,row.label) }}</template></el-table-column><el-table-column :label="t('精确数值')" min-width="190" align="right"><template #default="{row}"><span class="number-cell">{{ formatExact(row.value) }}</span></template></el-table-column><el-table-column :label="t('单位')" width="100"><template #default="{row}">{{ unitLabel(row.unit) }}</template></el-table-column><el-table-column :label="t('同比')" min-width="100" align="right"><template #default="{row}"><span :title="missingReason(row.yoy_reason)">{{ yoy(row.yoy) }}</span></template></el-table-column><el-table-column :label="t('数据来源')" min-width="130"><template #default="{row}"><button class="source-link" @click="showSource(row)">{{ t('查看来源 ↗') }}</button></template></el-table-column></el-table></section>
            <div v-if="metrics.limitations?.length" class="panel cn-coverage"><h3>{{ t('数据范围与限制') }}</h3><ul><li v-for="item in metrics.limitations" :key="item">{{ t(item) }}</li></ul></div>
          </template>
        </el-tab-pane>
        <el-tab-pane :label="t('财报与公告')" name="filings">
          <section class="panel"><div class="panel-heading"><div><h2>{{ t('财报与公告') }}</h2><p>{{ t('从巨潮原文准备 PDF 正文，按页检索证据并创建研究。') }}</p></div><el-button text :icon="Refresh" :loading="filingLoading" @click="loadFilings(filings.page)">{{ t('刷新') }}</el-button></div><div v-if="filingLoading" class="loading-panel"><el-skeleton :rows="5" animated /></div><div v-else-if="filingError" class="empty-state error-state"><p>{{ t(filingError) }}</p><el-button @click="loadFilings(filings.page)">{{ t('重试') }}</el-button></div><div v-else-if="!filings.items.length" class="empty-state"><el-icon><Document /></el-icon><h3>{{ t('尚无已保存公告') }}</h3><p>{{ t('同步后显示真实公告；列表缺失不表示公司没有披露。') }}</p><el-button :loading="running" @click="sync">{{ t('同步最新 A 股资料') }}</el-button></div><template v-else><div class="cn-filing-list"><article v-for="filing in filings.items" :key="filing.id" class="cn-filing"><div><span class="form-tag">{{ formLabel(filing.form_type) }}</span><h3>{{ filing.title }}</h3><p>{{ t('披露日期') }} {{ filing.filing_date }} <span v-if="filing.report_date"> · {{ t('报告期') }} {{ filing.report_date }}</span></p></div><div class="cn-filing-actions"><el-button @click="openResearch(filing)">{{ t('正文与研究') }}</el-button><a v-if="safeCnSourceUrl(filing.source_url)" :href="safeCnSourceUrl(filing.source_url)!" class="source-link" target="_blank" rel="noopener noreferrer">{{ t('查看披露原文') }} ↗</a><span v-else class="field-hint">{{ t('原文链接不可用') }}</span></div></article></div><div class="table-pagination"><el-pagination background layout="prev, pager, next" :current-page="filings.page" :page-size="filings.page_size" :total="filings.total" @current-change="loadFilings" /></div></template></section>
          <div v-if="filings.limitations?.length" class="panel cn-coverage"><ul><li v-for="item in filings.limitations" :key="item">{{ t(item) }}</li></ul></div>
        </el-tab-pane>
        <el-tab-pane :label="t('财报正文与 AI 研究')" name="research" lazy><CnResearchWorkbench :key="symbol" :symbol="symbol" :filings="filings.items" :preset="researchPreset" /></el-tab-pane>
      </el-tabs>
    </template>
    <el-drawer v-model="sourceOpen" :title="t('A 股指标来源与计算依据')" size="min(620px, 100vw)">
      <template v-if="sourceMetric"><SourceBadge :kind="company?.source_kind" /><h2 class="cn-source-title">{{ cnMetricLabel(sourceMetric.metric_key,sourceMetric.label) }}</h2><p class="source-exact number-cell">{{ formatExact(sourceMetric.value) }} {{ unitLabel(sourceMetric.unit) }}</p><p v-if="sourceMetric.missing_reason" class="cn-inline-error">{{ missingReason(sourceMetric.missing_reason) }}</p><p class="field-hint">{{ sourceMetric.period_start || '—' }} → {{ sourceMetric.period_end }} · {{ t(sourceMetric.calc_method) }}</p><div v-if="sourceLoading" class="loading-panel"><el-skeleton :rows="4" animated /></div><div v-if="sourceError" role="alert" class="cn-inline-error">{{ t(sourceError) }}</div><section v-for="source in sources" :key="source.id" class="cn-source-record"><h3>{{ t('原始响应快照') }}</h3><p>{{ source.kind }} · {{ displayTime(source.fetched_at) }}</p><p class="cn-source-hash">SHA-256 {{ source.sha256 }}</p><a v-if="safeCnSourceUrl(source.source_url)" :href="safeCnSourceUrl(source.source_url)!" class="source-link" target="_blank" rel="noopener noreferrer">{{ t('查看数据来源') }} ↗</a><details><summary>{{ t('来源元数据') }}</summary><pre>{{ JSON.stringify(source.source_metadata,null,2) }}</pre></details></section><h3 class="cn-source-title">{{ t('字段与计算追溯') }}</h3><pre class="cn-provenance">{{ JSON.stringify(sourceMetric.provenance,null,2) }}</pre></template>
    </el-drawer>
  </div>
</template>
