import { createRouter, createWebHistory } from 'vue-router'
import HomePage from './pages/HomePage.vue'
import CompanyPage from './pages/CompanyPage.vue'
import SettingsPage from './pages/SettingsPage.vue'
import ResearchPage from './pages/ResearchPage.vue'
import ResearchDetailPage from './pages/ResearchDetailPage.vue'
import CnCompanyPage from './pages/CnCompanyPage.vue'

export default createRouter({ history: createWebHistory(), routes: [
  { path: '/', component: HomePage },
  { path: '/company/:cik', component: CompanyPage },
  { path: '/cn/company/:symbol', component: CnCompanyPage },
  { path: '/settings', component: SettingsPage },
  { path: '/settings/ai', redirect: '/settings' },
  { path: '/research', component: ResearchPage },
  { path: '/research/jobs/:jobId', component: ResearchDetailPage },
  { path: '/research/reports/:reportId', component: ResearchDetailPage },
  { path: '/:pathMatch(.*)*', redirect: '/' },
], scrollBehavior: () => ({ top: 0 }) })
