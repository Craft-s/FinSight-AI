<script setup lang="ts">
import '../research.css'
import { computed, ref } from 'vue'
import type { EvidenceCitation } from '../types'
import { t } from '../i18n'
import { safeSecUrl } from '../utils'
const props = defineProps<{citation: EvidenceCitation}>()
const expanded = ref(false)
const longExcerpt = computed(() => props.citation.excerpt.length > 700)
</script>
<template>
  <section :id="'citation-' + citation.citation_id" class="evidence-card">
    <div class="evidence-heading"><div><strong class="citation-id">{{ citation.citation_id }}</strong><span class="form-tag">{{ citation.form_type }}</span></div><span>{{ citation.filing_date }}</span></div>
    <h3 class="evidence-section">{{ citation.section || t('未标注章节') }}</h3>
    <blockquote :id="'excerpt-' + citation.citation_id" :class="{ 'is-collapsed': longExcerpt&&!expanded }">{{ citation.excerpt }}</blockquote>
    <button v-if="longExcerpt" class="source-link evidence-expand" :aria-expanded="expanded" :aria-controls="'excerpt-' + citation.citation_id" @click="expanded=!expanded">{{ t(expanded?'收起摘录':'展开完整摘录') }} {{ expanded?'−':'+' }}</button>
    <div class="evidence-footer"><span>{{ citation.accession }}</span><a v-if="safeSecUrl(citation.source_url)" :href="safeSecUrl(citation.source_url)!" target="_blank" rel="noopener noreferrer" class="source-link">{{ t('SEC 原文') }} ↗</a></div>
    <code class="source-hash">SHA-256 {{ citation.document_sha256 }}</code>
    <details class="evidence-verification"><summary>{{ t('查看来源定位') }}</summary><dl class="evidence-meta"><div><dt>{{ t('申报编号') }}</dt><dd>{{ citation.accession }}</dd></div><div><dt>{{ t('定位') }}</dt><dd>{{ typeof citation.location === 'object' ? JSON.stringify(citation.location) : citation.location }}</dd></div><div><dt>{{ t('段落编号') }}</dt><dd>{{ citation.chunk_id }}</dd></div></dl></details>
  </section>
</template>
