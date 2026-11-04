<script setup lang="ts">
import '../research.css'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { DataAnalysis, Refresh } from '@element-plus/icons-vue'
import { api, errorMessage } from '../api'
import { useSessionStore } from '../stores'
import { locale, metricLabel, t, unitLabel } from '../i18n'
import { displayTime, formatExact, safeSecUrl } from '../utils'
import { isVerifiedConfig, PROVIDER_NAMES } from '../llm'
import SourceBadge from './SourceBadge.vue'
import UsageStats from './UsageStats.vue'
import type { LLMConfig, OverviewFact, OverviewJob } from '../types'

const props = defineProps<{cik: string; query: {frequency: string; selection_policy: string; as_of?: string; accession?: string}}>()
const session = useSessionStore()
const configs = ref<LLMConfig[]>([])
const selectedConfig = ref('')
const loading = ref(false)
const running = ref(false)
const error = ref('')
const job = ref<OverviewJob | null>(null)
const pollingPaused = ref(false)
const sourceFact = ref<OverviewFact | null>(null)
const sourceOpen = ref(false)
let generation = 0
let configGeneration = 0
let timer: ReturnType<typeof setTimeout> | undefined
const verifiedConfigs = computed(() => configs.value.filter(isVerifiedConfig))
const ready = computed(() => !!session.adminToken && !!selectedConfig.value && !loading.value && !running.value && (props.query.selection_policy !== 'as_of' || !!props.query.as_of) && (props.query.selection_policy !== 'as_filed' || !!props.query.accession))
const result = computed(() => job.value?.result)
const jobLabel = computed(() => t(({queued: 'AI 概览已排队', running: '正在生成并核对 AI 概览', succeeded: 'AI 概览已完成', failed: 'AI 概览生成失败'} as Record<string, string>)[job.value?.status || ''] || '正在提交任务'))
function factLabel(id: string) { const fact = result.value?.facts.find(f => f.id === id); return fact ? `${id} · ${metricLabel(fact.metric_key, fact.label)}` : id }
function showSource(fact: OverviewFact) { sourceFact.value = fact; sourceOpen.value = true }
function readable(value: unknown): string { return typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value ?? 'N/A') }
async function loadConfigs() {
  const revision = ++configGeneration
  if (!session.adminToken) { configs.value = []; selectedConfig.value = ''; return }
  loading.value = true; error.value = ''
  try {
    const response = await api.llmConfigs(session.adminToken)
    if (revision !== configGeneration) return
    configs.value = response.items
    if (!verifiedConfigs.value.some(c => c.id === selectedConfig.value)) selectedConfig.value = verifiedConfigs.value.find(c => c.is_active)?.id || verifiedConfigs.value[0]?.id || ''
  } catch (cause) { if (revision === configGeneration) error.value = errorMessage(cause) }
  finally { if (revision === configGeneration) loading.value = false }
}
async function poll(id: string, revision: number, attempt = 0) {
  try {
    const response = await api.overviewJob(id, session.adminToken)
    if (revision !== generation) return
    job.value = response
    if (response.status === 'succeeded' || response.status === 'failed') { running.value = false; pollingPaused.value = false }
    else if (attempt >= 180) { running.value = false; pollingPaused.value = true }
    else timer = setTimeout(() => poll(id, revision, attempt + 1), 2000)
  } catch (cause) { if (revision === generation) { running.value = false; pollingPaused.value = true; error.value = errorMessage(cause) } }
}
async function generate() {
  if (!ready.value) return
  const revision = ++generation
  clearTimeout(timer); running.value = true; pollingPaused.value = false; error.value = ''; job.value = null
  try {
    const response = await api.createOverview(props.cik, {...props.query, config_id: selectedConfig.value, language: locale.value}, session.adminToken)
    if (revision !== generation) return
    job.value = response
    const id = response.job_id || response.id
    if (!id) throw new Error('AI 任务未返回有效编号。')
    await poll(id, revision)
  } catch (cause) { if (revision === generation) { running.value = false; error.value = errorMessage(cause) } }
}
async function resume() {
  const id = job.value?.job_id || job.value?.id
  if (!id || !session.adminToken) return
  clearTimeout(timer); running.value = true; pollingPaused.value = false; error.value = ''
  await poll(id, generation)
}
watch(() => session.adminToken, () => { generation++; clearTimeout(timer); running.value = false; job.value = null; configs.value = []; selectedConfig.value = ''; loadConfigs() })
watch(() => JSON.stringify([props.cik, props.query]), () => { generation++; clearTimeout(timer); job.value = null; running.value = false; pollingPaused.value = false; error.value = '' })
onMounted(loadConfigs)
onBeforeUnmount(() => { generation++; configGeneration++; clearTimeout(timer) })
</script>
<template>
  <div class="ai-overview">
    <section class="panel ai-overview-controls">
      <div class="panel-heading research-section-heading"><div><div class="eyebrow">{{ t('指标辅助解读') }}</div><h2>{{ t('结构化财务 AI 概览') }}</h2><p>{{ t('使用所选口径最近已披露期间的指标，不分析财报正文或事件。') }}</p></div><el-icon class="research-heading-icon"><DataAnalysis /></el-icon></div>
      <div class="ai-overview-body">
        <div v-if="!session.adminToken || (!loading && !verifiedConfigs.length)" class="ai-setup-hint"><span class="research-state-icon" aria-hidden="true">◇</span><div><strong>{{ t('连接研究能力') }}</strong><p>{{ t(!session.adminToken ? 'AI 调用需要会话授权。请先在设置页输入访问令牌。' : '没有已验证的模型配置。请先保存配置并通过真实连接测试。') }}</p></div><RouterLink class="source-link" to="/settings">{{ t('前往设置') }} ↗</RouterLink></div>
        <div v-else class="ai-run-row"><div class="ai-model-picker"><label for="overview-config">{{ t('已验证的模型') }}</label><el-select id="overview-config" v-model="selectedConfig" :loading="loading" :disabled="running || loading" :aria-label="t('已验证的模型')"><el-option v-for="config in verifiedConfigs" :key="config.id" :value="config.id" :label="PROVIDER_NAMES[config.provider] + ' · ' + config.model_id + (config.is_active ? ' · ' + t('默认配置') : '')" /></el-select></div><el-button type="primary" :disabled="!ready" :loading="running" @click="generate">{{ t('生成 AI 概览') }}</el-button></div>
        <div class="ai-context"><span>{{ t('期间') }} <b>{{ t(query.frequency === 'annual' ? '年度' : '单季度') }}</b></span><span>{{ t('数据口径') }} <b>{{ t(query.selection_policy === 'as_of' ? '截止披露日期' : query.selection_policy === 'as_filed' ? '指定申报' : '最新有效口径') }}</b></span><span v-if="query.as_of">{{ query.as_of }}</span><span v-if="query.accession">{{ query.accession }}</span><el-button :icon="Refresh" text :disabled="running || loading || !session.adminToken" :loading="loading" @click="loadConfigs">{{ t('刷新模型配置') }}</el-button></div>
        <details class="research-method-note"><summary>{{ t('生成说明与验证边界') }}</summary><p>{{ t('生成结果需通过引用与数值校验，解读含模型推论。') }} {{ t('事实数值由后端填充；引用存在不等于模型解读已得到独立语义验证。') }}</p><p>{{ t('生成会向所选服务商发送公开公司指标与来源标识，并产生模型用量；不会发送管理员令牌。') }}</p></details>
        <el-alert v-if="error" :title="t(error)" type="error" :closable="false" show-icon />
        <el-alert v-if="job" :title="jobLabel" :type="job.status === 'failed' ? 'error' : job.status === 'succeeded' ? 'success' : 'info'" :description="job.error_message ? t(job.error_message) : t('任务编号：') + ' ' + (job.job_id || job.id)" :closable="false" show-icon />
        <div v-if="pollingPaused" class="ai-poll-paused"><p>{{ t('已停止自动等待；后台任务可能仍在运行，可继续查询状态。') }}</p><el-button @click="resume">{{ t('继续查询') }}</el-button></div>
      </div>
    </section>
    <div v-if="running && !result" class="panel loading-panel" aria-live="polite"><el-skeleton :rows="5" animated /></div>
    <article v-if="result" class="panel ai-result">
      <div class="panel-heading research-section-heading"><div><div class="eyebrow">{{ t('结构化指标解读') }}</div><h2>{{ result.title }}</h2><p>{{ result.provider }} · {{ result.model_id }} · {{ t('配置版本') }} {{ result.config_version }} · {{ result.duration_ms.toLocaleString(locale) }} ms</p></div><SourceBadge :kind="result.source_kind" compact /></div>
      <div class="ai-result-body">
<p class="ai-summary">{{ result.summary }}</p><UsageStats :usage="result.usage" /><div class="ai-observations"><section v-for="(observation, index) in result.observations" :key="index"><p>{{ observation.text }}</p><div class="ai-reference-list"><button v-for="id in observation.metric_refs" :key="id" class="small-badge" @click="result.facts.find(f => f.id === id) && showSource(result.facts.find(f => f.id === id)!)">{{ factLabel(id) }} ↗</button></div></section></div>
        <h3>{{ t('后端核对的事实与来源') }}</h3><div class="ai-facts"><button v-for="fact in result.facts" :key="fact.id" @click="showSource(fact)"><span class="tiny-label">{{ fact.id }} · {{ fact.period }}</span><span>{{ metricLabel(fact.metric_key, fact.label) }}</span><strong class="number-cell">{{ formatExact(fact.value) }} <small>{{ unitLabel(fact.unit) }}</small></strong><span class="source-link">{{ t('查看来源') }} ↗</span></button></div>
        <div v-if="result.limitations.length" class="ai-limitations"><h3>{{ t('资料范围与限制') }}</h3><ul><li v-for="item in result.limitations" :key="item">{{ t(item) }}</li></ul></div><p class="field-hint">{{ t('生成时间') }}: {{ job?.finished_at ? displayTime(job.finished_at) : 'N/A' }} · {{ t('报告语言') }}: {{ result.language }}</p><p class="ai-disclaimer">{{ t(result.disclaimer) }}</p>
      </div>
    </article>
    <el-drawer v-model="sourceOpen" :title="t('指标来源与计算口径')" size="min(600px, 94vw)"><template v-if="sourceFact"><div class="source-metric-title"><span>{{ metricLabel(sourceFact.metric_key, sourceFact.label) }} · {{ sourceFact.period }}</span><strong class="number-cell">{{ formatExact(sourceFact.value) }} <small>{{ unitLabel(sourceFact.unit) }}</small></strong></div><dl class="source-definition"><template v-for="(value, key) in sourceFact.provenance" :key="key"><dt>{{ t(String(key)) }}</dt><dd><a v-if="safeSecUrl(value)" :href="safeSecUrl(value)!" target="_blank" rel="noopener noreferrer" class="source-link">{{ t('SEC 原文') }} ↗</a><pre v-else-if="typeof value === 'object'">{{ readable(value) }}</pre><span v-else>{{ readable(value) }}</span></dd></template></dl></template></el-drawer>
  </div>
</template>
