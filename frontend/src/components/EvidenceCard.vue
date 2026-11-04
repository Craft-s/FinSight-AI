<script setup lang="ts">
import '../research.css'
import { computed, ref } from 'vue'
import type { EvidenceCitation, CnEvidenceCitation } from '../types'
import { t } from '../i18n'
import { safeSecUrl } from '../utils'
import { cnCitationPage, cnFilingTypeLabel, safeCnPdfUrl } from '../cnResearch'
const props = defineProps<{citation: EvidenceCitation|CnEvidenceCitation}>()
const expanded = ref(false)
const longExcerpt = computed(() => props.citation.excerpt.length > 700)
const cnCitation = computed(() => 'market' in props.citation && props.citation.market === 'CN' ? props.citation as CnEvidenceCitation : null)
const sourceUrl = computed(() => cnCitation.value ? safeCnPdfUrl(cnCitation.value.source_url,cnCitationPage(cnCitation.value)) : safeSecUrl(props.citation.source_url))
const identity = computed(() => cnCitation.value ? cnCitation.value.announcement_id : (props.citation as EvidenceCitation).accession)
const formLabel = computed(() => t(cnCitation.value ? cnFilingTypeLabel(cnCitation.value.form_type) : props.citation.form_type))
</script>
<template>
  <section :id="'citation-' + citation.citation_id" class="evidence-card">
    <div class="evidence-heading"><div><strong class="citation-id">{{ citation.citation_id }}</strong><span class="form-tag">{{ formLabel }}</span></div><span>{{ citation.filing_date }}</span></div>
    <h3 class="evidence-section">{{ citation.section || t('未标注章节') }}</h3>
    <blockquote :id="'excerpt-' + citation.citation_id" :class="{ 'is-collapsed': longExcerpt&&!expanded }">{{ citation.excerpt }}</blockquote>
    <button v-if="longExcerpt" class="source-link evidence-expand" :aria-expanded="expanded" :aria-controls="'excerpt-' + citation.citation_id" @click="expanded=!expanded">{{ t(expanded?'收起摘录':'展开完整摘录') }} {{ expanded?'−':'+' }}</button>
    <div class="evidence-footer"><span>{{ identity }}<template v-if="cnCitation"> · {{ cnCitation.symbol }} · {{ t('PDF 页码') }} {{ cnCitationPage(cnCitation) || '—' }}</template></span><a v-if="sourceUrl" :href="sourceUrl" target="_blank" rel="noopener noreferrer" class="source-link">{{ t(cnCitation?'PDF 原文':'SEC 原文') }} ↗</a></div>
    <code class="source-hash">SHA-256 {{ citation.document_sha256 }}</code>
    <details class="evidence-verification"><summary>{{ t('查看来源定位') }}</summary><dl class="evidence-meta"><div><dt>{{ t(cnCitation?'公告编号':'申报编号') }}</dt><dd>{{ identity }}</dd></div><div><dt>{{ t('定位') }}</dt><dd>{{ typeof citation.location === 'object' ? JSON.stringify(citation.location) : citation.location }}</dd></div><div><dt>{{ t('段落编号') }}</dt><dd>{{ citation.chunk_id }}</dd></div></dl></details>
  </section>
</template>
