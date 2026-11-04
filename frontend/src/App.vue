<script setup lang="ts">
import { computed, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { Search, DataAnalysis, Setting, TopRight, Connection, Document, Sunny, Moon } from '@element-plus/icons-vue'
import { useSessionStore } from './stores'
import { activeLanguage, t } from './i18n'
import GlobalSearch from './components/GlobalSearch.vue'
import { resolvedTheme, setTheme } from './theme'
import { activeMarket, companyIdentity, companyRoute } from './market'
import './cn.css'
const session = useSessionStore()
const route = useRoute()
const elementLocale = computed(() => activeLanguage.value.elementLocale)
const pageName = computed(() => t(route.path.startsWith('/cn/company') ? 'A 股公司研究' : route.path.startsWith('/company') ? '公司研究' : route.path.startsWith('/settings') ? '设置' : route.path.startsWith('/research') ? '研究记录' : '发现公司'))
watch(() => route.path, path => { if (path.startsWith('/cn/company')) activeMarket.value = 'CN'; else if (path.startsWith('/company')) activeMarket.value = 'US' }, {immediate:true})
onMounted(session.checkHealth)
</script>
<template>
  <el-config-provider :locale="elementLocale">
    <a class="skip-link" href="#main-content">{{ t('跳至主要内容') }}</a>
    <div class="app-shell">
      <aside class="sidebar">
        <RouterLink class="brand" to="/"><span class="brand-mark">F<span>·</span></span><span>FinSight <small>AI</small></span></RouterLink>
        <div class="workspace-label"><span class="workspace-dot"></span>{{ t('个人研究空间') }}<span class="workspace-plan">{{ t('私有') }}</span></div>
        <p class="nav-label">{{ t('研究工具') }}</p>
        <nav class="main-nav" :aria-label="t('主导航')">
          <RouterLink to="/" :aria-label="`${t('公司搜索')} ↗`" :title="t('公司搜索')" :class="{ active: route.path === '/' || route.path.startsWith('/company') || route.path.startsWith('/cn/company') }"><el-icon><Search /></el-icon><span>{{ t('公司搜索') }}</span><span class="nav-arrow">↗</span></RouterLink>
          <RouterLink to="/research" :aria-label="t('研究记录')" :title="t('研究记录')" :class="{ active: route.path.startsWith('/research') }"><el-icon><DataAnalysis /></el-icon><span>{{ t('研究记录') }}</span></RouterLink>
          <RouterLink to="/settings" :aria-label="t('设置')" :title="t('设置')" :class="{ active: route.path.startsWith('/settings') }"><el-icon><Setting /></el-icon><span>{{ t('设置') }}</span></RouterLink>
        </nav>
        <div v-if="session.recent.length" class="sidebar-recent"><p class="nav-label">{{ t('最近查看') }}</p><RouterLink v-for="company in session.recent.slice(0, 4)" :key="companyIdentity(company)" :to="companyRoute(company)"><span class="recent-dot">{{ (company.ticker || company.name).slice(0, 1) }}</span><span>{{ company.ticker }}<small>{{ company.name }}</small></span></RouterLink></div>
        <div class="sidebar-bottom">
          <div class="source-card"><span class="tiny-label">{{ t('从一手信息出发') }}</span><div class="source-card-title"><el-icon><Document /></el-icon> {{ activeMarket === 'CN' ? t('巨潮资讯') : 'SEC EDGAR' }}</div><p>{{ t('公开披露，可追溯的研究起点。') }}</p><a :href="activeMarket === 'CN' ? 'https://www.cninfo.com.cn/' : 'https://www.sec.gov/edgar/search/'" target="_blank" rel="noopener noreferrer">{{ t('访问官方档案') }} <el-icon><TopRight /></el-icon></a></div>
          <div class="workspace-user"><div class="user-avatar">F</div><div><strong>{{ t('个人研究空间') }}</strong><span>{{ t('私有单用户 · v0.1') }}</span></div></div>
        </div>
      </aside>
      <div class="workspace">
        <header class="topbar"><div class="breadcrumb"><span class="breadcrumb-root">{{ t('工作区') }}</span><span class="breadcrumb-divider">/</span><strong>{{ pageName }}</strong></div><GlobalSearch /><div class="topbar-right"><button class="icon-button theme-toggle" :aria-label="t(resolvedTheme === 'dark' ? '切换为浅色主题' : '切换为深色主题')" :title="t(resolvedTheme === 'dark' ? '切换为浅色主题' : '切换为深色主题')" @click="setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')"><el-icon><Sunny v-if="resolvedTheme === 'dark'" /><Moon v-else /></el-icon></button><button class="connection-state" :class="{ disconnected: !session.connected }" :title="t('重新检查服务连接')" @click="session.checkHealth"><span class="status-dot"></span><span>{{ t(session.connected ? '服务已连接' : '检查连接') }}</span><el-icon class="connection-icon"><Connection /></el-icon></button></div></header>
        <main id="main-content" tabindex="-1"><RouterView /></main>
        <nav class="mobile-nav" :aria-label="t('移动端主导航')"><RouterLink to="/" :class="{ active: route.path === '/' || route.path.startsWith('/company') || route.path.startsWith('/cn/company') }"><el-icon><Search /></el-icon>{{ t('公司搜索') }}</RouterLink><RouterLink to="/research" :class="{ active: route.path.startsWith('/research') }"><el-icon><DataAnalysis /></el-icon>{{ t('研究记录') }}</RouterLink><RouterLink to="/settings" :class="{ active: route.path.startsWith('/settings') }"><el-icon><Setting /></el-icon>{{ t('设置') }}</RouterLink></nav>
        <footer class="footer"><span>FINSIGHT AI <span class="footer-separator">/</span> EVIDENCE BEFORE INSIGHT</span><span>{{ t('仅供研究和教育，不构成投资建议。') }}</span></footer>
      </div>
    </div>
  </el-config-provider>
</template>
