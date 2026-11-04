<script setup lang="ts">
import '../research.css'
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { api, errorMessage } from '../api'
import { useSessionStore } from '../stores'
import { displayTime } from '../utils'
import { locale, t } from '../i18n'
import { isResearchPending, researchStatusLabel, researchTypeLabel } from '../research'
import type { Page, ResearchJob, CnResearchJob } from '../types'
import { useRoute, useRouter } from 'vue-router'
import { researchCompanyIdentity, researchCompanyRoute } from '../cnResearch'
const session=useSessionStore()
const route=useRoute(),router=useRouter()
const isCn=computed(()=>route.path.startsWith('/cn/'))
const researchBase=computed(()=>isCn.value?'/cn/research':'/research')
const selectedMarket=computed({get:()=>isCn.value?'CN':'US',set:value=>{router.push(value==='CN'?'/cn/research':'/research')}})
const records=ref<Page<ResearchJob|CnResearchJob>>({items:[],total:0,page:1,page_size:20})
const page=ref(1)
const loading=ref(false)
const error=ref('')
let revision=0
let timer:ReturnType<typeof setTimeout>|undefined
async function load(){
  clearTimeout(timer);const current=++revision;error.value=''
  if(!session.adminToken){records.value={items:[],total:0,page:1,page_size:20};loading.value=false;return}
  loading.value=true
  try{const response=await (isCn.value?api.cnResearchHistory:api.researchHistory)(session.adminToken,page.value);if(current===revision){records.value=response;if(response.items.some(item=>isResearchPending(item.status)))timer=setTimeout(load,15000)}}
  catch(cause){if(current===revision)error.value=errorMessage(cause)}
  finally{if(current===revision)loading.value=false}
}
watch(()=>[session.adminToken,isCn.value],()=>{page.value=1;records.value={items:[],total:0,page:1,page_size:20};load()},{immediate:true})
onBeforeUnmount(()=>{revision++;clearTimeout(timer)})
</script>
<template>
  <div class="page research-page">
    <div class="research-library-header"><div><div class="eyebrow">{{ t('私有研究库') }}</div><h1 class="page-title">{{ t('研究记录') }}</h1><p class="page-intro">{{ t('有证据的分析，值得被保存。') }}</p></div><RouterLink class="primary-link" to="/">{{ t('选择公司并创建研究') }} ↗</RouterLink></div>
    <div class="panel cn-history-market"><label>{{ t('研究市场') }}<select v-model="selectedMarket" :aria-label="t('研究市场')"><option value="US">{{ t('美股 · US') }}</option><option value="CN">{{ t('A 股 · CN') }}</option></select></label><p class="field-hint">{{ t(isCn?'A 股报告只使用已保存的 PDF 页码证据，不将最新供应商指标冒充原报告事实。':'研究记录仅显示当前所选市场。') }}</p></div>
    <div v-if="!session.adminToken" class="panel empty-state research-library-empty"><span class="research-empty-symbol" aria-hidden="true">◇</span><h3>{{ t('研究记录需要会话授权') }}</h3><p>{{ t('请在设置页输入访问令牌后查看当前私有工作区的研究任务。') }}</p><RouterLink to="/settings" class="primary-link">{{ t('前往设置') }} ↗</RouterLink></div>
    <div v-else-if="error" class="panel empty-state error-state"><span class="research-empty-symbol" aria-hidden="true">!</span><h3>{{ t('研究记录暂时不可用') }}</h3><p>{{ t(error) }}</p><el-button @click="load">{{ t('重试') }}</el-button></div>
    <div v-else class="panel research-history">
      <div class="research-history-toolbar"><div><h2>{{ t('全部研究任务') }}</h2><span class="research-muted">{{ t('共') }} {{ records.total.toLocaleString(locale) }} {{ t('项研究任务') }}</span></div><el-button :loading="loading" @click="load">{{ t('刷新记录') }}</el-button></div>
      <div v-if="loading&&!records.items.length" class="research-history-skeleton"><el-skeleton :rows="6" animated /></div>
      <template v-else>
        <el-table v-loading="loading" :data="records.items" :empty-text="t('尚无研究任务。请从公司财报或事件列表开始。')">
          <el-table-column :label="t('研究类型')" min-width="180"><template #default="{row}"><span class="research-table-title">{{ t(researchTypeLabel(row.task_type)) }}</span></template></el-table-column>
          <el-table-column :label="isCn ? t('股票代码') : 'CIK'" min-width="130"><template #default="{row}"><RouterLink class="source-link" :to="researchCompanyRoute(row)">{{ researchCompanyIdentity(row) }}</RouterLink></template></el-table-column>
          <el-table-column :label="t('任务状态')" min-width="140"><template #default="{row}"><span class="small-badge" :class="'research-status-' + row.status"><span class="status-dot" aria-hidden="true"></span>{{ t(researchStatusLabel(row.status)) }}</span></template></el-table-column>
          <el-table-column :label="t('创建时间')" min-width="210"><template #default="{row}">{{ displayTime(row.created_at) }}</template></el-table-column>
          <el-table-column :label="t('操作')" width="140" align="right"><template #default="{row}"><RouterLink class="source-link" :to="researchBase + '/jobs/' + encodeURIComponent(row.job_id||row.id)">{{ t(row.report_id?'查看报告':'查看任务') }} ↗</RouterLink></template></el-table-column>
          <template #empty><div class="research-table-empty"><span class="research-empty-symbol" aria-hidden="true">▤</span><h3>{{ t('你的研究将在这里归档') }}</h3><p>{{ t('尚无研究任务。请从公司财报或事件列表开始。') }}</p></div></template>
        </el-table>
        <div class="pagination-row"><span>{{ t('共') }} {{ records.total.toLocaleString(locale) }} {{ t('项研究任务') }}</span><el-pagination v-model:current-page="page" :page-size="records.page_size" :total="records.total" layout="prev, pager, next" @current-change="load" /></div>
      </template>
    </div>
  </div>
</template>
