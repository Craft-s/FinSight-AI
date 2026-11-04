<script setup lang="ts">
import { computed } from 'vue'
import { sourceInfo } from '../utils'
const props = defineProps<{ kind?: string; compact?: boolean }>()
const info = computed(() => sourceInfo(props.kind))
</script>
<template><div class="source-badge" :class="[info.tone, { compact }]" :title="info.detail"><span class="status-dot"></span><span>{{ info.label }}</span><span v-if="!compact" class="source-detail">{{ info.detail }}</span></div></template>
