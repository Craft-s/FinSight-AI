import { computed, ref, watch } from 'vue'

export type ThemePreference = 'light' | 'dark' | 'system'
const STORAGE_KEY = 'finsight.theme'
export function isThemePreference(value: unknown): value is ThemePreference {
  return value === 'light' || value === 'dark' || value === 'system'
}
function initialPreference(): ThemePreference {
  try {
    const value = typeof window === 'undefined' ? null : window.localStorage.getItem(STORAGE_KEY)
    return isThemePreference(value) ? value : 'light'
  } catch { return 'light' }
}
const systemDark = ref(false)
export const themePreference = ref<ThemePreference>(initialPreference())
export const resolvedTheme = computed<'light' | 'dark'>(() => themePreference.value === 'system' ? systemDark.value ? 'dark' : 'light' : themePreference.value)
let initialized = false
export function setTheme(value: ThemePreference) {
  if (!isThemePreference(value)) return
  themePreference.value = value
  try { window.localStorage.setItem(STORAGE_KEY, value) } catch { /* Preferences are optional. */ }
}
export function initializeTheme() {
  if (initialized || typeof window === 'undefined') return
  initialized = true
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  systemDark.value = media.matches
  media.addEventListener('change', event => { systemDark.value = event.matches })
  window.addEventListener('storage', event => {
    if (event.key === STORAGE_KEY) themePreference.value = isThemePreference(event.newValue) ? event.newValue : 'light'
  })
  watch(resolvedTheme, value => {
    document.documentElement.classList.toggle('dark', value === 'dark')
    document.documentElement.dataset.theme = value
    document.documentElement.style.colorScheme = value
    document.querySelector('meta[name="theme-color"]')?.setAttribute('content', value === 'dark' ? '#101318' : '#f6f8fb')
  }, { immediate: true, flush: 'sync' })
}
