<script setup lang="ts">
import '../research.css'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { api, errorMessage } from '../api'
import { useSessionStore } from '../stores'
import { locale, metricLabel, t, unitLabel } from '../i18n'
import { displayTime, formatExact } from '../utils'
import { isResearchPending, reportFilename, researchStatusLabel, researchTypeLabel } from '../research'
import type { ResearchJob, ResearchReport, CnResearchJob, CnResearchReport } from '../types'
import EvidenceCard from '../components/EvidenceCard.vue'
import SourceBadge from '../components/SourceBadge.vue'
import UsageStats from '../components/UsageStats.vue'
import { cnFilingTypeLabel, cnReportFilename, researchCompanyIdentity, researchCompanyRoute } from '../cnResearch'

const route=useRoute(),router=useRouter(),session=useSessionStore()
const isCn=computed(()=>route.path.startsWith('/cn/'))
const researchBase=computed(()=>isCn.value?'/cn/research':'/research')
const requestApi=computed(()=>isCn.value?{job:api.cnResearchJob,report:api.cnResearchReport,retry:api.cnRetryResearch,cancel:api.cnCancelResearch,download:api.cnDownloadReport}:{job:api.researchJob,report:api.researchReport,retry:api.retryResearch,cancel:api.cancelResearch,download:api.downloadReport})
const job=ref<ResearchJob|CnResearchJob|null>(null)
const report=ref<ResearchReport|CnResearchReport|null>(null)
const filingIdentity=(filing:{id:string;accession?:string;announcement_id?:string})=>filing.accession||filing.announcement_id||filing.id
const loading=ref(false),acting=ref(false),downloading=ref(false),paused=ref(false)
const error=ref('')
let generation=0
let timer:ReturnType<typeof setTimeout>|undefined
const claims=computed(()=>report.value?.content.claims||[])
const disclosed=computed(()=>claims.value.filter(c=>c.kind==='disclosed_fact'))
const inferences=computed(()=>claims.value.filter(c=>c.kind==='inference'))
const reportId=computed(()=>route.params.reportId?String(route.params.reportId):job.value?.report_id)

async function fetchJob(id:string,current:number,attempt=0){
  const response=await requestApi.value.job(id,session.adminToken)
  if(current!==generation)return
  job.value=response
  if(response.report_id){const saved=await requestApi.value.report(response.report_id,session.adminToken);if(current===generation)report.value=saved}
  if(isResearchPending(response.status)){
    if(attempt<180)timer=setTimeout(()=>poll(id,current,attempt+1),2000)
    else paused.value=true
  }else paused.value=false
}
async function poll(id:string,current:number,attempt=0){
  try{await fetchJob(id,current,attempt)}catch(cause){if(current===generation){error.value=errorMessage(cause);paused.value=true}}
}
async function load(){
  clearTimeout(timer);const current=++generation;job.value=null;report.value=null;error.value='';paused.value=false;acting.value=false;downloading.value=false
  if(!session.adminToken){loading.value=false;return}
  loading.value=true
  try{
    if(route.params.reportId){const saved=await requestApi.value.report(String(route.params.reportId),session.adminToken);if(current!==generation)return;report.value=saved;await fetchJob(saved.job_id,current)}
    else await fetchJob(String(route.params.jobId),current)
  }catch(cause){if(current===generation)error.value=errorMessage(cause)}
  finally{if(current===generation)loading.value=false}
}
async function retry(){
  if(!job.value||acting.value)return
  const current=generation,request=requestApi.value,base=researchBase.value,id=job.value.job_id
  acting.value=true;error.value=''
  try{const accepted=await request.retry(id,session.adminToken);if(current!==generation)return;if(route.params.jobId===accepted.job_id)await load();else await router.push(`${base}/jobs/${encodeURIComponent(accepted.job_id)}`)}
  catch(cause){if(current===generation)error.value=errorMessage(cause)}finally{if(current===generation)acting.value=false}
}
async function cancel(){
  if(!job.value||acting.value)return
  const current=generation,request=requestApi.value,id=job.value.job_id
  acting.value=true;error.value=''
  try{const response=await request.cancel(id,session.adminToken);if(current!==generation)return;clearTimeout(timer);job.value=response;if(isResearchPending(response.status))await poll(response.job_id,current)}
  catch(cause){if(current===generation)error.value=errorMessage(cause)}finally{if(current===generation)acting.value=false}
}
async function download(){
  if(!report.value||!reportId.value||downloading.value)return
  const current=generation,request=requestApi.value,id=reportId.value,saved=report.value,cn=isCn.value
  downloading.value=true;error.value=''
  try{const data=await request.download(id,session.adminToken);if(current!==generation)return;const url=URL.createObjectURL(new Blob([data],{type:'text/markdown;charset=utf-8'}));const anchor=document.createElement('a');anchor.href=url;anchor.download=cn?cnReportFilename(researchCompanyIdentity(saved),saved.id):reportFilename(researchCompanyIdentity(saved),saved.id);document.body.appendChild(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000)}
  catch(cause){if(current===generation)error.value=errorMessage(cause)}finally{if(current===generation)downloading.value=false}
}
watch(()=>[route.path,route.params.jobId,route.params.reportId,session.adminToken],load,{immediate:true})
onBeforeUnmount(()=>{generation++;clearTimeout(timer)})
</script>
<template>
  <div class="page research-detail">
    <RouterLink :to="researchBase" class="back-link">← {{ t('研究记录') }}</RouterLink><div class="eyebrow">{{ t(report?'研究报告':'分析过程') }}</div><h1 class="page-title">{{ report?.title || t(job?researchTypeLabel(job.task_type):'研究任务') }}</h1>
    <div v-if="!session.adminToken" class="panel empty-state research-library-empty"><span class="research-empty-symbol" aria-hidden="true">◇</span><h3>{{ t('研究记录需要会话授权') }}</h3><p>{{ t('请在设置页输入访问令牌后查看当前私有工作区的研究任务。') }}</p><RouterLink to="/settings" class="primary-link">{{ t('前往设置') }} ↗</RouterLink></div>
    <div v-else>
      <el-alert v-if="error" :title="t(error)" type="error" :closable="false" show-icon />
      <div v-if="error&&!job&&!report&&!loading" class="research-error-actions"><el-button @click="load">{{ t('重试') }}</el-button><RouterLink to="/settings" class="source-link">{{ t('前往设置') }} ↗</RouterLink></div>
      <div v-if="loading" class="panel loading-panel" aria-live="polite"><el-skeleton :rows="5" animated /></div>
      <section v-if="job" class="panel research-job-panel"><div class="research-job-summary"><span class="small-badge" :class="'research-status-' + job.status"><span class="status-dot" aria-hidden="true"></span>{{ t(researchStatusLabel(job.status)) }}</span><div class="research-job-meta"><span>{{ t('创建时间') }} {{ displayTime(job.created_at) }}</span><span v-if="job.finished_at">{{ t('完成时间') }} {{ displayTime(job.finished_at) }}</span></div><details class="research-method-note"><summary>{{ t('任务详情') }}</summary><code>{{ job.job_id }}</code></details><p v-if="job.error_message" class="research-error">{{ t(job.error_message) }}</p></div><div class="research-actions"><RouterLink :to="researchCompanyRoute(job)" class="source-link">{{ t('公司档案') }} ↗</RouterLink><el-button v-if="isResearchPending(job.status)" :loading="acting" @click="cancel">{{ t('取消任务') }}</el-button><el-button v-if="job.status==='failed'||job.status==='cancelled'" :loading="acting" @click="retry">{{ t('重试研究') }}</el-button><el-button v-if="report" type="primary" :loading="downloading" @click="download">{{ t('下载 Markdown') }}</el-button></div></section>
      <div v-if="job&&isResearchPending(job.status)" class="panel research-wait" role="status"><div class="research-wait-heading"><span class="research-pulse" aria-hidden="true"></span><h2>{{ t(researchStatusLabel(job.status)) }}</h2></div><p>{{ t(paused?'已停止自动等待；后台任务可能仍在运行，可继续查询状态。':'正在检索证据、调用模型并校验结果。') }}</p><div v-if="!paused" class="research-wait-skeleton"><el-skeleton :rows="3" animated /></div><p class="field-hint">{{ t('结果只在完成引用与数值校验后发布。') }} {{ t('取消会停止采用结果，不能保证上游调用已终止。') }}</p><el-button v-if="paused" @click="load">{{ t('继续查询') }}</el-button></div>
      <article v-if="report" class="panel research-report">
        <div class="panel-heading research-section-heading"><div><div class="eyebrow">{{ t('研究结论') }}</div><h2>{{ t('事实、推论与证据') }}</h2><p>{{ report.provider }} · {{ report.model_id }} · {{ t('报告语言') }} {{ report.language }} · {{ report.duration_ms.toLocaleString(locale) }} ms</p></div><SourceBadge :kind="report.source_kind" compact /></div>
        <nav class="research-report-nav" :aria-label="t('报告章节')"><a href="#report-facts">{{ t('披露事实') }}</a><a href="#report-inferences">{{ t('模型推论') }}</a><a href="#report-unknowns">{{ t('仍未知与资料限制') }}</a><a href="#report-evidence">{{ t('原文引用与出处') }}</a></nav>
        <div class="research-report-body">
          <el-alert :title="t('事实与推论分开展示，引用存在不等于推论已获语义验证。')" type="info" :closable="false" />
          <el-alert v-if="report.content.insufficient_evidence" :title="t('证据不足')" :description="t('已保存资料不足以支持完整回答。请查看仍未知事项，不要把缺少依据理解为否定结论。')" type="warning" :closable="false" show-icon />
          <div class="research-report-layout">
<div class="research-report-main">
            <section id="report-facts" class="research-claim-section"><div class="research-section-label"><span class="research-kind-mark fact" aria-hidden="true">✓</span><h2>{{ t('披露事实') }}</h2><span class="research-section-count">{{ disclosed.length }}</span></div><p v-if="!disclosed.length" class="research-muted">{{ t('没有通过校验的披露事实。') }}</p><div v-for="(claim,index) in disclosed" :key="index" class="research-claim"><p>{{ claim.text }}</p><div class="research-ref-row"><a v-for="id in claim.citation_ids" :key="id" :href="'#citation-' + id" class="small-badge">{{ id }} ↓</a><span v-for="refId in claim.metric_refs" :key="refId" class="small-badge">{{ refId }}</span></div></div></section>
            <section id="report-inferences" class="research-claim-section"><div class="research-section-label"><span class="research-kind-mark" aria-hidden="true">◇</span><h2>{{ t('模型推论') }}</h2><span class="research-section-count">{{ inferences.length }}</span></div><p class="field-hint">{{ t('以下为模型解读，尚未自动验证语义支持，不构成确定的因果或投资判断。') }}</p><p v-if="!inferences.length" class="research-muted">{{ t('未生成模型推论。') }}</p><div v-for="(claim,index) in inferences" :key="index" class="research-claim inference"><p>{{ claim.text }}</p><div class="research-ref-row"><a v-for="id in claim.citation_ids" :key="id" :href="'#citation-' + id" class="small-badge">{{ id }} ↓</a><span v-for="refId in claim.metric_refs" :key="refId" class="small-badge">{{ refId }}</span></div></div></section>
            <section id="report-unknowns" class="research-claim-section"><div class="research-section-label"><span class="research-kind-mark unknown" aria-hidden="true">?</span><h2>{{ t('仍未知与资料限制') }}</h2></div><ul v-if="report.content.uncertainties.length" class="research-uncertainties"><li v-for="item in report.content.uncertainties" :key="item">{{ item }}</li></ul><p v-else class="research-muted">{{ t('模型未列出其他未知事项，不表示资料完整。') }}</p></section>
            <section v-if="report.content.limitations?.length" class="research-claim-section"><h2>{{ t('方法限制') }}</h2><ul class="research-uncertainties"><li v-for="item in report.content.limitations" :key="item">{{ t(item) }}</li></ul></section>
            <section v-if="report.content.facts.length" class="research-claim-section"><h2>{{ t('后端核对的事实与来源') }}</h2><div class="ai-facts"><div v-for="fact in report.content.facts" :key="fact.id" class="research-fact"><span class="tiny-label">{{ fact.id }} · {{ fact.period }}</span><span>{{ metricLabel(fact.metric_key,fact.label) }}</span><strong class="number-cell">{{ formatExact(fact.value) }} <small>{{ unitLabel(fact.unit) }}</small></strong><details><summary>{{ t('查看来源') }}</summary><pre>{{ JSON.stringify(fact.provenance,null,2) }}</pre></details></div></div></section>
          </div><aside class="research-report-aside" :aria-label="t('研究范围与方法')"><h3>{{ t('研究范围与方法') }}</h3><p v-if="report.content.retrieval_mode" class="field-hint">{{ t('检索方式') }}<br><strong>{{ t(report.content.retrieval_mode==='section_fallback'?'章节候选检索（未命中关键词）':report.content.retrieval_mode==='postgres_fts'?'关键词全文检索':report.content.retrieval_mode==='test_keyword'?'测试关键词检索':report.content.retrieval_mode) }}</strong></p><ul v-if="report.content.filings?.length" class="research-scope-list"><li v-for="filing in report.content.filings" :key="filing.id"><b>{{ isCn?t(cnFilingTypeLabel(filing.form_type)):filing.form_type }}</b> · {{ filing.report_date||filing.filing_date }}<code>{{ filingIdentity(filing) }}</code></li></ul><h3>{{ t('模型用量') }}</h3><UsageStats :usage="report.usage" /></aside>
</div>
          <section id="report-evidence" class="research-claim-section research-evidence-section"><div class="research-section-label"><h2>{{ t('原文引用与出处') }}</h2><span class="research-section-count">{{ report.content.citations.length }}</span></div><p class="field-hint">{{ t('短摘录、定位与哈希来自保存的正文；原文可能已经修订。') }}</p><div class="report-evidence-grid"><EvidenceCard v-for="citation in report.content.citations" :key="citation.citation_id" :citation="citation" /></div><p v-if="!report.content.citations.length" class="research-muted">{{ t('没有可展示的正文引用。') }}</p></section>
          <p class="ai-disclaimer">{{ t('仅供研究和教育，不构成投资建议。') }}</p>
        </div>
      </article>
    </div>
  </div>
</template>
