<script setup lang="ts">
import { t } from '../i18n'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Search, ArrowRight, TopRight, Refresh, CircleCheck, Document, DataLine, Connection } from '@element-plus/icons-vue'
import { api, errorMessage } from '../api'
import { useSessionStore } from '../stores'
import type { CnStatus, Company, SyncJob } from '../types'
import SourceBadge from '../components/SourceBadge.vue'
import { displayTime } from '../utils'
import { activeMarket, companyIdentity, companyRoute } from '../market'
import MarketSelector from '../components/MarketSelector.vue'
const router = useRouter()
const session = useSessionStore()
const query = ref('')
const companies = ref<Company[]>([])
const loading = ref(true)
const error = ref('')
const cnStatus = ref<CnStatus | null>(null)
const directoryJob = ref<SyncJob | null>(null)
const directoryError = ref('')
const refreshingDirectory = ref(false)
let pollTimer: ReturnType<typeof setTimeout> | undefined
let disposed = false
let debounce: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
async function search() {
  controller?.abort(); controller = new AbortController()
  const signal = controller.signal
  loading.value = true; error.value = ''
  try { const response = await api.search(query.value.trim(), signal, activeMarket.value); if (!signal.aborted) companies.value = response.items }
  catch (cause) { if (!signal.aborted) error.value = errorMessage(cause) }
  finally { if (!signal.aborted) loading.value = false }
}
function visit(company: Company) { session.visit(company); router.push(companyRoute(company)) }
async function loadCnStatus() { try { cnStatus.value = await api.cnStatus() } catch (cause) { directoryError.value = errorMessage(cause) } }
async function pollDirectory(id: string, attempt = 0) {
  try {
    const result = await api.cnJob(id, session.adminToken)
    if (disposed) return
    directoryJob.value = result
    if (result.status === 'succeeded') { refreshingDirectory.value = false; await loadCnStatus(); if (activeMarket.value === 'CN') await search() }
    else if (result.status === 'failed') { refreshingDirectory.value = false; directoryError.value = result.error_message || '目录更新失败，已保存目录不受影响。' }
    else if (attempt >= 180) { refreshingDirectory.value = false; directoryError.value = '任务仍在运行，请稍后刷新目录状态。' }
    else pollTimer = setTimeout(() => pollDirectory(id, attempt + 1), 2000)
  } catch (cause) { if (!disposed) { refreshingDirectory.value = false; directoryError.value = errorMessage(cause) } }
}
async function refreshDirectory() {
  if (!session.adminToken) { directoryError.value = '请先在设置页输入本地管理员访问令牌。'; return }
  refreshingDirectory.value = true; directoryError.value = ''; directoryJob.value = null
  try { const result = await api.cnDirectorySync(session.adminToken); await pollDirectory(result.job_id) }
  catch (cause) { refreshingDirectory.value = false; directoryError.value = errorMessage(cause) }
}
watch(query, () => { clearTimeout(debounce); debounce = setTimeout(search, 280) })
watch(activeMarket, () => { clearTimeout(debounce); controller?.abort(); query.value = ''; companies.value = []; search(); if (activeMarket.value === 'CN') loadCnStatus() })
onMounted(() => { search(); if (activeMarket.value === 'CN') loadCnStatus() })
onBeforeUnmount(() => { disposed = true; clearTimeout(debounce); clearTimeout(pollTimer); controller?.abort() })
</script>
<template>
  <div class="page home-page">
    <section class="hero">
      <div class="eyebrow"><span></span>{{ t('研究工作台') }}</div>
      <h1>{{ t('企业研究') }}</h1>
      <p class="hero-description">{{ t('公司、财务与原始披露，汇聚在一个研究空间。') }}</p>
      <div class="home-market-row"><MarketSelector /><span>{{ t(activeMarket === 'CN' ? '沪深京 A 股 · 人民币财务' : '支持 SEC 10-K / 10-Q / 8-K 申报主体') }}</span></div>
      <form class="search-box" role="search" @submit.prevent="search"><el-icon class="search-icon"><Search /></el-icon><input v-model="query" :aria-label="t(activeMarket === 'CN' ? '搜索中文公司名称或证券代码' : '搜索公司、股票代码或 CIK')" :placeholder="t(activeMarket === 'CN' ? '搜索中文公司名称或证券代码' : '搜索公司、股票代码或 CIK')" autocomplete="off"><span v-if="!query" class="search-hint">{{ activeMarket }}</span><button type="submit" :aria-label="t('搜索')"><ArrowRight /></button></form>
      <div class="quick-search"><span> {{ t('快速查找') }} </span><button v-for="ticker in activeMarket === 'CN' ? ['600519', '000001', '300750'] : ['AAPL', 'MSFT', 'NVDA']" :key="ticker" @click="query = ticker">{{ ticker }} <el-icon><TopRight /></el-icon></button></div>
    </section>
    <section v-if="activeMarket === 'CN'" class="panel cn-directory-panel">
      <div><h2>{{ t('A 股公司目录') }} <span class="small-badge">{{ cnStatus?.directory_count ?? '—' }}</span></h2><p>{{ t('巨潮公司目录 · 结构化财务来自东方财富第三方整理，不能替代披露原文。') }}</p><p>{{ t('目录含历史证券标识，不是实时上市状态或行情。') }}</p><p class="field-hint">{{ t('目录更新') }} · {{ displayTime(cnStatus?.directory_updated_at) }}</p><p v-if="directoryJob" role="status">{{ t(directoryJob.status === 'succeeded' ? '目录已更新' : directoryJob.status === 'failed' ? '目录更新失败' : '目录更新任务正在处理') }}</p><p v-if="directoryError" class="cn-inline-error" role="alert">{{ t(directoryError) }}</p></div>
      <div class="cn-directory-actions"><el-button :loading="refreshingDirectory" @click="refreshDirectory">{{ t('更新 A 股目录') }}</el-button><RouterLink v-if="!session.adminToken" to="/settings" class="source-link">{{ t('前往设置授权') }} ↗</RouterLink></div>
    </section>
    <section class="company-section">
      <div class="section-heading"><div><h2>{{ t(query ? '搜索结果' : '公司档案列表') }} <span v-if="!loading && !error" class="count">{{ companies.length }}</span></h2><p>{{ t(query ? activeMarket === 'CN' ? '确认证券代码与交易所后，进入公司研究。' : '确认公司名称与 CIK 后，进入公司研究。' : '选择一家企业，开始探索它的财务表现。') }}</p></div><button class="text-button" @click="search"><el-icon><Refresh /></el-icon> {{ t('刷新') }} </button></div>
      <div v-if="loading" class="companies-grid"><div v-for="i in 3" :key="i" class="company-card"><el-skeleton :rows="4" animated /></div></div>
      <div v-else-if="error" class="empty-state error-state"><el-icon><Connection /></el-icon><h3> {{ t('暂时无法加载公司') }} </h3><p>{{ t(error) }}</p><el-button @click="search"> {{ t('重试') }} </el-button></div>
      <div v-else-if="!companies.length" class="empty-state"><el-icon><Search /></el-icon><h3>{{ t(query ? '没有匹配的公司' : '等待载入公司档案') }}</h3><p>{{ t(activeMarket === 'CN' ? '请尝试中文名称、六位代码或带交易所的代码；目录为空时先授权更新目录。' : query ? '请尝试英文公司名称、股票代码，或 10 位 CIK。' : '连接并初始化 SEC 数据后，公司档案将在这里显示。') }}</p></div>
      <div v-else class="companies-grid"><button v-for="company in companies" :key="companyIdentity(company)" class="company-card" @click="visit(company)"><div class="company-card-top"><div class="ticker-monogram">{{ (company.ticker || company.name).slice(0, 1) }}</div><span class="exchange">{{ company.exchange || company.market }}</span><el-icon class="card-arrow"><TopRight /></el-icon></div><h3>{{ company.ticker || '—' }}</h3><p class="company-name">{{ company.name }}</p><div class="company-identifiers">{{ company.market === 'CN' ? t('证券代码') : 'CIK' }} <span>{{ company.market === 'CN' ? company.symbol : company.cik }}</span></div><div class="company-card-footer"><SourceBadge :kind="company.source_kind || session.health?.source_kind" compact /><span> {{ t('查看档案') }} <el-icon><ArrowRight /></el-icon></span></div></button></div>
    </section>
    <section v-if="session.recent.length" class="recent-section"><div class="section-heading"><h2> {{ t('最近查看') }} </h2><span class="muted"> {{ t('仅保留本次会话') }} </span></div><div class="recent-list"><button v-for="company in session.recent" :key="companyIdentity(company)" @click="visit(company)"><strong>{{ company.ticker }} <small>{{ company.market }}</small></strong><span>{{ company.name }}</span><small>{{ displayTime(company.last_synced_at) }}</small><el-icon><ArrowRight /></el-icon></button></div></section>
    <section class="principles"><div><span class="principle-icon"><Document /></span><h3> {{ t('始于官方披露') }} </h3><p> {{ t(activeMarket === 'CN' ? '结构化数据来自第三方整理，应回到披露原文核对。' : '10-K 年报、10-Q 季报与 8-K 事件，回到信息的源头。') }} </p></div><div><span class="principle-icon"><DataLine /></span><h3> {{ t('数字有明确口径') }} </h3><p> {{ t('年度、期间与单位清晰呈现，缺失数据始终显示 N/A。') }} </p></div><div><span class="principle-icon"><CircleCheck /></span><h3> {{ t('每个指标可追溯') }} </h3><p> {{ t('查看原始概念、申报日期和计算规则，理解数据如何得出。') }} </p></div></section>
  </div>
</template>
