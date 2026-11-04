<script setup lang="ts">
import { computed } from 'vue'
import { locale, t } from '../i18n'
const props = defineProps<{usage: Record<string, unknown>}>()
const labels: Record<string, string> = {input_tokens: '输入 Token', output_tokens: '输出 Token', reasoning_tokens: '推理 Token', total_tokens: '合计 Token'}
const items = computed(() => Object.entries(labels).flatMap(([key, label]) => {
  const value = props.usage[key]
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? [{key, label, value}] : []
}))
</script>
<template>
  <dl v-if="items.length" class="usage-statistics"><div v-for="item in items" :key="item.key"><dt>{{ t(item.label) }}</dt><dd>{{ item.value.toLocaleString(locale) }}</dd></div></dl>
  <p v-else class="field-hint">{{ t('服务商未返回用量统计。') }}</p>
</template>
