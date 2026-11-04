<script setup lang="ts">
import { ref } from 'vue'
import { Lock, CircleCheck, Monitor } from '@element-plus/icons-vue'
import { isSupportedLocale, locale, setLocale, SUPPORTED_LANGUAGES, t } from '../i18n'
import { useSessionStore } from '../stores'
import ModelSettings from '../components/ModelSettings.vue'
import ThemeControl from '../components/ThemeControl.vue'
import '../settings.css'
const session = useSessionStore()
const draftToken = ref('')
const saved = ref(false)
function save() { session.adminToken = draftToken.value; draftToken.value = ''; saved.value = true }
function changeLanguage(value: unknown) { if (isSupportedLocale(value)) setLocale(value) }
</script>
<template>
  <div class="page settings-page">
    <header class="settings-page-header">
      <div><div class="eyebrow">WORKSPACE / PREFERENCES</div><h1 class="page-title">{{ t('设置') }}</h1><p class="page-intro">{{ t('管理界面语言、当前会话权限与模型能力。') }}</p></div>
      <span class="settings-private-label"><el-icon><Lock /></el-icon>{{ t('私有研究工作区') }}</span>
    </header>
    <nav class="settings-section-nav" :aria-label="t('设置分区')"><a href="#preferences">{{ t('偏好设置') }}</a><a href="#session-access">{{ t('会话权限') }}</a><a href="#model-connections">{{ t('模型连接') }}</a></nav>
    <div class="settings-overview-grid">
      <section id="preferences" class="panel settings-panel preferences-panel">
        <div class="panel-heading"><span class="settings-section-icon"><el-icon><Monitor /></el-icon></span><div><h2>{{ t('偏好设置') }}</h2><p>{{ t('调整阅读体验，即时生效。') }}</p></div></div>
        <div class="appearance-preference"><label>{{ t('外观主题') }}</label><ThemeControl /><p class="field-hint">{{ t('选择浅色、深色，或跟随系统外观。') }}</p></div>
        <div class="language-preference">
          <label for="interface-language">{{ t('界面语言') }}</label>
          <el-select id="interface-language" :model-value="locale" :aria-label="t('选择语言')" @change="changeLanguage"><el-option v-for="language in SUPPORTED_LANGUAGES" :key="language.code" :label="language.nativeName" :value="language.code" /></el-select>
          <p class="field-hint">{{ t('语言偏好仅保存在当前浏览器，不影响财务数据或计算口径。') }}</p>
        </div>
      </section>
      <section id="session-access" class="panel settings-panel session-panel">
        <div class="panel-heading"><span class="settings-section-icon"><el-icon><Lock /></el-icon></span><div><h2>{{ t('同步操作授权') }}</h2><p>{{ t('查询已保存的数据无需授权。重新同步需要部署者配置的访问令牌。') }}</p></div></div>
        <form class="session-access-form" @submit.prevent="save">
          <div class="model-label-row"><label for="admin-token">{{ t('本地管理员访问令牌') }}</label><span class="session-memory-badge" :class="{ loaded: session.adminToken }">{{ t(session.adminToken ? '已载入令牌' : '未载入令牌') }}</span></div>
          <el-input id="admin-token" v-model="draftToken" type="password" show-password :placeholder="t('输入 LOCAL_ADMIN_TOKEN')" autocomplete="off" :maxlength="512" />
          <p class="field-hint">{{ t('仅保存在当前页面会话内存中，刷新页面后清除。此处不接受模型 API Key。') }}</p>
          <div class="settings-actions"><el-button type="primary" native-type="submit" :disabled="!draftToken.trim()">{{ t('在本次会话中使用') }}</el-button><el-button v-if="session.adminToken" @click="session.adminToken = ''; saved = false">{{ t('清除令牌') }}</el-button></div>
          <p v-if="saved && session.adminToken" class="settings-confirmation" role="status"><el-icon><CircleCheck /></el-icon>{{ t('已存入会话，操作时验证权限') }}</p>
          <div class="session-privacy-note"><el-icon><Lock /></el-icon><span>{{ t('会话令牌与模型 API Key 分开管理。') }}</span></div>
        </form>
      </section>
    </div>
    <ModelSettings />
  </div>
</template>
