<script setup lang="ts">
import '../research.css'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { api, errorMessage } from '../api'
import { locale, t } from '../i18n'
import { useSessionStore } from '../stores'
import { isVerifiedConfig, PROVIDER_NAMES } from '../llm'
import { RESEARCH_TYPES, documentStatusLabel, researchRequestError } from '../research'
import type { DocumentJob, EvidenceCitation, Filing, FilingDocument, LLMConfig, ResearchTaskType } from '../types'
import { displayTime, safeSecUrl } from '../utils'
import SourceBadge from './SourceBadge.vue'
import EvidenceCard from './EvidenceCard.vue'

const props = defineProps<{cik:string; filings:Filing[]; preset?:{filing:Filing; type:ResearchTaskType; revision:number}|null}>()
const session = useSessionStore()
const router = useRouter()
const taskType = ref<ResearchTaskType>('summary')
const selectedFilings = ref<string[]>([])
const question = ref('')
const force = ref(false)
const configId = ref('')
const configs = ref<LLMConfig[]>([])
const loadingConfigs = ref(false)
const submitting = ref(false)
const error = ref('')
const docFiling = ref('')
const documents = ref<FilingDocument[]>([])
const documentLoading = ref(false)
const documentError = ref('')
const documentTask = ref<DocumentJob|null>(null)
const query = ref('')
const results = ref<EvidenceCitation[]>([])
const searchLoading = ref(false)
const searchError = ref('')
const retrievalMode = ref('')
const scopeNote = ref('')
const hasSearched = ref(false)
let timer:ReturnType<typeof setTimeout>|undefined
let docRevision = 0
let configRevision = 0
let searchRevision = 0
let submitRevision = 0
const allFilings = computed(() => [...new Map([...props.filings,...(props.preset ? [props.preset.filing] : [])].map(f=>[f.id,f])).values()])
const eligibleFilings = computed(() => allFilings.value.filter(f=>taskType.value === 'event' ? f.form_type.startsWith('8-K') : /^(10-K|10-Q)/.test(f.form_type)))
const verifiedConfigs = computed(()=>configs.value.filter(isVerifiedConfig))
const documentPending = computed(()=>documentTask.value?.status === 'queued' || documentTask.value?.status === 'running')
const requestBody = computed(()=>({cik:props.cik,task_type:taskType.value,filing_ids:selectedFilings.value,question:taskType.value==='question'?question.value.trim():undefined,config_id:configId.value,language:locale.value,force:force.value}))
const ready = computed(()=>!!session.adminToken && !submitting.value && !loadingConfigs.value && !researchRequestError(requestBody.value))
const filingLabel = (f:Filing)=>`${f.form_type} · ${f.report_date || f.filing_date} · ${f.accession_number}`

async function loadConfigs() {
  const revision=++configRevision
  configs.value=[]; configId.value=''
  if (!session.adminToken) {loadingConfigs.value=false;return}
  loadingConfigs.value=true
  try { const response=await api.llmConfigs(session.adminToken); if(revision===configRevision){configs.value=response.items;configId.value=verifiedConfigs.value.find(c=>c.is_active)?.id||verifiedConfigs.value[0]?.id||''} }
  catch(cause){if(revision===configRevision)error.value=errorMessage(cause)}
  finally{if(revision===configRevision)loadingConfigs.value=false}
}
async function loadDocuments() {
  const revision=++docRevision
  clearTimeout(timer); documents.value=[];results.value=[];hasSearched.value=false;scopeNote.value='';documentTask.value=null;documentError.value='';searchError.value=''
  if(!docFiling.value){documentLoading.value=false;return}
  documentLoading.value=true
  try{const response=await api.documents(docFiling.value);if(revision===docRevision){documents.value=response.items;retrievalMode.value=response.retrieval_mode}}
  catch(cause){if(revision===docRevision)documentError.value=errorMessage(cause)}
  finally{if(revision===docRevision)documentLoading.value=false}
}
async function pollDocuments(id:string,revision:number,attempt=0) {
  try{
    const response=await api.documentJob(id,session.adminToken)
    if(revision!==docRevision)return
    documentTask.value=response
    if(response.status==='succeeded'){
      const saved=await api.documents(docFiling.value)
      if(revision===docRevision){documents.value=saved.items;retrievalMode.value=saved.retrieval_mode}
    } else if(response.status==='queued'||response.status==='running'){
      if(attempt<180)timer=setTimeout(()=>pollDocuments(id,revision,attempt+1),2000)
      else documentError.value='已停止自动等待；后台任务可能仍在运行，可继续查询状态。'
    }
  }catch(cause){if(revision===docRevision)documentError.value=errorMessage(cause)}
}
async function syncDocuments() {
  if(!docFiling.value||!session.adminToken||documentPending.value)return
  const revision=docRevision;documentError.value=''
  try{const accepted=await api.syncDocuments(docFiling.value,session.adminToken);if(revision===docRevision){documentTask.value={id:accepted.job_id,job_id:accepted.job_id,status:accepted.status,error_code:null,error_message:null,created_at:'',finished_at:null};await pollDocuments(accepted.job_id,revision)}}
  catch(cause){if(revision===docRevision)documentError.value=errorMessage(cause)}
}
async function searchEvidence() {
  if(!query.value.trim())return
  const revision=++searchRevision;const selected=docFiling.value;hasSearched.value=true;searchLoading.value=true;searchError.value='';results.value=[]
  try{const response=await api.evidence(props.cik,query.value.trim(),selected||undefined);if(revision===searchRevision&&selected===docFiling.value){results.value=response.items;retrievalMode.value=response.retrieval_mode;scopeNote.value=response.scope_note||''}}
  catch(cause){if(revision===searchRevision)searchError.value=errorMessage(cause)}
  finally{if(revision===searchRevision)searchLoading.value=false}
}
async function submit() {
  if(!ready.value)return
  const revision=++submitRevision;submitting.value=true;error.value=''
  try{const accepted=await api.createResearch(requestBody.value,session.adminToken);if(revision===submitRevision)await router.push(`/research/jobs/${encodeURIComponent(accepted.job_id)}`)}
  catch(cause){if(revision===submitRevision)error.value=errorMessage(cause)}
  finally{if(revision===submitRevision)submitting.value=false}
}
watch(()=>session.adminToken,()=>{submitRevision++;searchRevision++;error.value='';submitting.value=false;loadConfigs()},{immediate:true})
watch(()=>props.preset, preset=>{if(preset){taskType.value=preset.type;selectedFilings.value=[preset.filing.id];docFiling.value=preset.filing.id}},{immediate:true})
watch(taskType,()=>{selectedFilings.value=selectedFilings.value.filter(id=>eligibleFilings.value.some(f=>f.id===id)).slice(0,taskType.value==='comparison'?2:1)})
watch(allFilings,()=>{if(!docFiling.value&&allFilings.value[0])docFiling.value=allFilings.value[0].id},{immediate:true})
watch(docFiling,()=>{searchRevision++;searchLoading.value=false;loadDocuments()},{immediate:true})
onBeforeUnmount(()=>{clearTimeout(timer);docRevision++;configRevision++;searchRevision++;submitRevision++})
</script>
<template>
  <section class="panel research-workbench">
    <div class="panel-heading research-section-heading"><div><div class="eyebrow">{{ t('研究工作区') }}</div><h2>{{ t('带引用的财报研究') }}</h2><p>{{ t('选择指定申报，区分披露事实、模型推论与仍未知事项。') }}</p></div><RouterLink to="/research" class="source-link">{{ t('研究记录') }} ↗</RouterLink></div>
    <form class="research-form" :aria-busy="submitting" @submit.prevent="submit">
      <section class="research-input-section">
        <div class="research-step-title"><span>01</span><div><h3>{{ t('定义研究范围') }}</h3><p>{{ t('先确定研究类型和申报，再选择用于分析的模型。') }}</p></div></div>
        <div class="research-form-grid">
          <label>{{ t('研究类型') }}<el-select v-model="taskType" :disabled="submitting" :aria-label="t('研究类型')"><el-option v-for="item in RESEARCH_TYPES" :key="item.value" :value="item.value" :label="t(item.label)" /></el-select></label>
          <label>{{ t(taskType==='comparison'?'两份对比申报':'研究申报') }}<el-select v-model="selectedFilings" multiple :multiple-limit="taskType==='comparison'?2:1" :aria-label="t('研究申报')" :placeholder="t('请选择申报')" :disabled="submitting"><el-option v-for="filing in eligibleFilings" :key="filing.id" :value="filing.id" :label="filingLabel(filing)" /></el-select></label>
        </div>
        <p class="field-hint">{{ t('选项来自当前已加载的财报与事件列表；可从列表中的研究入口指定其他申报。') }}</p>
        <label v-if="taskType==='question'" class="research-field">{{ t('研究问题') }}<el-input v-model="question" type="textarea" :rows="3" :maxlength="500" show-word-limit :aria-label="t('研究问题')" :placeholder="t('例如：管理层披露了哪些现金流风险？')" /></label>
      </section>
      <section class="research-input-section">
        <div class="research-step-title"><span>02</span><div><h3>{{ t('选择模型并开始') }}</h3><p>{{ t('只使用已通过连接测试的模型配置。') }}</p></div></div>
        <div class="research-model-row"><label class="research-field">{{ t('已验证的模型') }}<el-select v-model="configId" :loading="loadingConfigs" :disabled="!session.adminToken||loadingConfigs||submitting" :placeholder="t('选择已验证模型')" :aria-label="t('已验证的模型')"><el-option v-for="config in verifiedConfigs" :key="config.id" :value="config.id" :label="PROVIDER_NAMES[config.provider] + ' · ' + config.model_id" /></el-select></label><el-button :disabled="loadingConfigs||submitting||!session.adminToken" :loading="loadingConfigs" @click="loadConfigs">{{ t('刷新模型配置') }}</el-button></div>
        <div v-if="!session.adminToken||(!loadingConfigs&&!verifiedConfigs.length)" class="ai-setup-hint" role="status"><span class="research-state-icon" aria-hidden="true">◇</span><div><strong>{{ t(!session.adminToken?'连接研究能力':'尚无可用模型') }}</strong><p>{{ t(!session.adminToken?'AI 调用需要会话授权。请先在设置页输入访问令牌。':'没有已验证的模型配置。请先保存配置并通过真实连接测试。') }}</p></div><RouterLink to="/settings" class="source-link">{{ t('前往设置') }} ↗</RouterLink></div>
        <details class="research-advanced"><summary>{{ t('生成选项与资料边界') }}</summary><el-checkbox v-model="force" class="research-regenerate" :disabled="submitting">{{ t('重新生成，不复用已完成报告（可能产生额外用量）') }}</el-checkbox><p class="field-hint">{{ t('研究只使用选定申报与已保存证据，不保证覆盖全部披露。') }} {{ t('生成将调用所选模型；取消会停止采用结果，不能保证上游调用已终止。') }}</p></details>
      </section>
      <el-alert v-if="error" :title="t(error)" type="error" :closable="false" show-icon />
      <div class="research-actions research-submit-row"><el-button type="primary" native-type="submit" :disabled="!ready" :loading="submitting">{{ t('创建研究任务') }} <span aria-hidden="true">↗</span></el-button><p class="field-hint">{{ t('证据不足时会明确保留未知，不生成没有出处的结论。') }}</p></div>
    </form>
  </section>
  <section class="panel evidence-browser">
    <div class="panel-heading research-section-heading"><div><div class="eyebrow">{{ t('独立证据浏览') }}</div><h2>{{ t('财报正文与证据检索') }}</h2><p>{{ t('无需模型 Key 即可浏览已保存的正文来源与检索片段。') }}</p></div><span class="small-badge">{{ t('无需模型') }}</span></div>
    <div class="research-form">
      <div class="evidence-controls"><label>{{ t('正文申报') }}<el-select v-model="docFiling" :aria-label="t('正文申报')"><el-option v-for="filing in allFilings" :key="filing.id" :value="filing.id" :label="filingLabel(filing)" /></el-select></label><el-button :disabled="!session.adminToken||!docFiling||documentPending" :loading="documentPending" @click="syncDocuments">{{ t('同步所选正文') }}</el-button></div>
      <details class="research-method-note"><summary>{{ t('正文范围与同步说明') }}</summary><p>{{ t('正文同步需要会话授权和可用来源配置；历史快照模式不会自动改用在线或测试正文。') }}</p><p>{{ t('事件主文与附件的解析范围见来源；不保证覆盖全部附件。') }}</p></details>
      <el-alert v-if="documentError" :title="t(documentError)" type="error" :closable="false" show-icon />
      <div v-if="documentTask" class="document-job" :class="'research-status-' + documentTask.status" role="status"><span class="status-dot" aria-hidden="true"></span><strong>{{ t(documentStatusLabel(documentTask.status)) }}</strong><p v-if="documentTask.error_message">{{ t(documentTask.error_message) }}</p><el-button v-if="documentPending&&documentError" @click="pollDocuments(documentTask.job_id,docRevision)">{{ t('继续查询') }}</el-button></div>
      <div v-if="documentLoading" class="research-document-loading" aria-live="polite"><el-skeleton :rows="2" animated /></div>
      <div v-else-if="!documents.length" class="research-inline-empty"><span class="research-state-icon" aria-hidden="true">▤</span><p>{{ t('尚无已保存的财报正文。申报元数据不等于正文已解析。') }}</p></div>
      <div v-for="document in documents" :key="document.id" class="document-record"><div class="document-record-heading"><div><span class="document-type-icon" aria-hidden="true">▤</span><strong>{{ document.title || t('申报正文') }}</strong><span v-if="document.role" class="small-badge">{{ t(document.role==='exhibit'?'附件':'主文') }}</span></div><SourceBadge :kind="document.source_kind" compact /></div><div class="document-record-meta"><span>{{ t('采集时间') }} {{ displayTime(document.fetched_at) }}</span><a v-if="safeSecUrl(document.source_url)" :href="safeSecUrl(document.source_url)!" target="_blank" rel="noopener noreferrer" class="source-link">{{ t('SEC 原文') }} ↗</a></div><p v-if="document.source_metadata?.note" class="research-muted">{{ t(String(document.source_metadata.note)) }}</p><code class="source-hash">SHA-256 {{ document.sha256 }}</code></div>
      <form class="evidence-search" @submit.prevent="searchEvidence"><el-input v-model="query" :maxlength="500" :aria-label="t('证据搜索')" :placeholder="t('输入关键词，例如 revenue、现金流、风险')" clearable /><el-button native-type="submit" :loading="searchLoading" :disabled="!query.trim()">{{ t('检索证据') }}</el-button></form>
      <div class="evidence-search-meta"><span v-if="retrievalMode" class="small-badge">{{ t(retrievalMode==='postgres_fts'?'关键词全文检索':'测试关键词检索') }}</span><span v-if="hasSearched&&!searchLoading&&!searchError">{{ t('返回 {count} 条证据', {count:results.length}) }}</span></div>
      <p v-if="scopeNote" class="field-hint">{{ t(scopeNote) }}</p>
      <div v-if="searchLoading" class="research-search-loading" aria-live="polite"><el-skeleton :rows="4" animated /></div>
      <div v-else-if="searchError"><el-alert :title="t(searchError)" type="error" :closable="false" show-icon /><div class="research-error-actions"><el-button @click="searchEvidence">{{ t('重试') }}</el-button></div></div>
      <div v-else-if="hasSearched&&!results.length" class="research-inline-empty"><span class="research-state-icon" aria-hidden="true">⌕</span><p>{{ t('没有匹配片段；这不代表公司未披露相关信息。') }}</p></div>
      <div v-else-if="!hasSearched" class="evidence-start-hint"><span aria-hidden="true">⌕</span><p>{{ t('从披露原文开始，输入关键词查找可追溯证据。') }}</p></div>
      <EvidenceCard v-for="citation in results" :key="citation.citation_id" :citation="citation" />
    </div>
  </section>
</template>
