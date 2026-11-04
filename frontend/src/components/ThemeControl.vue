<script setup lang="ts">
import { t } from '../i18n'
import { isThemePreference, setTheme, themePreference } from '../theme'
function change(value: unknown) { if (isThemePreference(value)) setTheme(value) }
</script>
<template>
  <el-radio-group class="theme-control" :model-value="themePreference" :aria-label="t('选择主题')" @change="change">
    <el-radio-button value="light">{{ t('浅色') }}</el-radio-button>
    <el-radio-button value="dark">{{ t('深色') }}</el-radio-button>
    <el-radio-button value="system">{{ t('跟随系统') }}</el-radio-button>
  </el-radio-group>
</template>
