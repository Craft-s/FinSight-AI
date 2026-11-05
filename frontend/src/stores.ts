import { defineStore } from 'pinia'
import { ref } from 'vue'
import type { Company, Health } from './types'
import { api } from './api'
import { companyIdentity } from './market'

export const useSessionStore = defineStore('session', () => {
  const desktopSession = typeof window !== 'undefined' && Reflect.get(window, 'finsightDesktop') === true
  // The native host recognizes this marker and keeps the real credential outside WebView2.
  const adminToken = ref(desktopSession ? 'desktop-session' : '')
  const health = ref<Health | null>(null)
  const connected = ref(false)
  const recent = ref<Company[]>([])
  function visit(company: Company) { recent.value = [company, ...recent.value.filter(c => companyIdentity(c) !== companyIdentity(company))].slice(0, 6) }
  async function checkHealth() { try { health.value = await api.health(); connected.value = true } catch { connected.value = false } }
  return { adminToken, desktopSession, health, connected, recent, visit, checkHealth }
})
