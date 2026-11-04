<script setup lang="ts">
import { activeMarket } from '../market'
import { t } from '../i18n'
</script>
<template>
  <select v-model="activeMarket" class="market-selector" :aria-label="t('研究市场')">
    <option value="US">{{ t('美股') }} · US</option>
    <option value="CN">{{ t('A 股') }} · CN</option>
  </select>
</template>
