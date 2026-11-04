<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { Search, Close, ArrowRight } from '@element-plus/icons-vue'
import { api, errorMessage } from '../api'
import { t } from '../i18n'
import type { Company } from '../types'
import { useSessionStore } from '../stores'
import { activeMarket, companyIdentity, companyRoute } from '../market'
import MarketSelector from './MarketSelector.vue'

const router = useRouter()
const session = useSessionStore()
const element = ref<HTMLElement>()
const input = ref<HTMLInputElement>()
const query = ref('')
const open = ref(false)
const loading = ref(false)
const results = ref<Company[]>([])
const error = ref('')
const selected = ref(-1)
const shortcutLabel = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform) ? '⌘ K' : 'Ctrl K'
let timer: ReturnType<typeof setTimeout> | undefined
let controller: AbortController | undefined
async function search() {
  controller?.abort()
  controller = new AbortController()
  const signal = controller.signal
  if (!query.value.trim()) { results.value = []; loading.value = false; return }
  loading.value = true; error.value = ''
  try { const response = await api.search(query.value.trim(), signal, activeMarket.value); if (!signal.aborted) results.value = response.items.slice(0, 8) }
  catch (cause) { if (!signal.aborted) error.value = errorMessage(cause) }
  finally { if (!signal.aborted) loading.value = false }
}
watch(query, () => { clearTimeout(timer); controller?.abort(); selected.value = -1; error.value = ''; results.value = []; loading.value = !!query.value.trim(); timer = setTimeout(search, 250) })
watch(activeMarket, () => { clearTimeout(timer); controller?.abort(); selected.value = -1; results.value = []; error.value = ''; loading.value = false; if (query.value.trim()) search() })
watch(selected, async value => { await nextTick(); if (value >= 0) document.getElementById(`global-company-${value}`)?.scrollIntoView({ block: 'nearest' }) })
watch(() => router.currentRoute.value.fullPath, () => { open.value = false })
function choose(company: Company) { session.visit(company); open.value = false; query.value = ''; router.push(companyRoute(company)) }
function keydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { open.value = false; input.value?.blur() }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault(); open.value = true
    if (results.value.length) selected.value = selected.value < 0 ? event.key === 'ArrowDown' ? 0 : results.value.length - 1 : (selected.value + (event.key === 'ArrowDown' ? 1 : -1) + results.value.length) % results.value.length
  }
  if (event.key === 'Enter' && results.value.length) { event.preventDefault(); choose(results.value[selected.value < 0 ? 0 : selected.value]!) }
}
function outside(event: PointerEvent) { if (event.target instanceof Node && !element.value?.contains(event.target)) open.value = false }
function shortcut(event: KeyboardEvent) { if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); input.value?.focus(); open.value = true } }
onMounted(() => { document.addEventListener('pointerdown', outside); document.addEventListener('keydown', shortcut) })
onBeforeUnmount(() => { clearTimeout(timer); controller?.abort(); document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', shortcut) })
</script>
<template>
  <div ref="element" class="global-search" role="search">
    <MarketSelector />
    <el-icon><Search /></el-icon>
    <input ref="input" v-model="query" role="combobox" :aria-label="t('全局公司搜索')" :placeholder="t('搜索股票代码或公司…')" :aria-expanded="open" aria-controls="global-search-results" aria-autocomplete="list" :aria-activedescendant="selected >= 0 ? `global-company-${selected}` : undefined" autocomplete="off" @focus="open = true" @keydown="keydown">
    <button v-if="query" class="icon-button search-clear" :aria-label="t('清除搜索')" @click="query = ''; input?.focus()"><el-icon><Close /></el-icon></button><kbd v-else>{{ shortcutLabel }}</kbd>
    <div v-if="open" class="global-search-popover">
      <div v-if="loading" class="global-search-message" role="status">{{ t('搜索中…') }}</div>
      <div v-else-if="error" class="global-search-message error-message" role="alert">{{ t(error) }}<button class="source-link" @click="search">{{ t('重试') }}</button></div>
      <div v-else-if="!query.trim()" class="global-search-message">{{ t(activeMarket === 'CN' ? '输入中文名称或六位证券代码' : '输入名称、股票代码或 CIK') }}</div>
      <div v-else-if="!results.length" class="global-search-message">{{ t('没有匹配的公司') }}</div>
      <div id="global-search-results" role="listbox" :aria-label="t('搜索公司候选')">
        <button v-for="(company, index) in results" :id="`global-company-${index}`" :key="companyIdentity(company)" class="global-search-result" :class="{ highlighted: selected === index }" role="option" :aria-selected="selected === index" @pointermove="selected = index" @click="choose(company)"><span class="search-result-symbol">{{ company.ticker }}</span><span>{{ company.name }}<small>{{ company.market === 'CN' ? company.symbol : `CIK ${company.cik}` }}</small></span><el-icon><ArrowRight /></el-icon></button>
      </div>
    </div>
  </div>
</template>
