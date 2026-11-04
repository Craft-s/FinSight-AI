import { defineConfig, devices } from '@playwright/test'

const apiUrl = process.env.FINSIGHT_E2E_API_URL || 'http://127.0.0.1:18000'
const externalApi = process.env.FINSIGHT_E2E_EXTERNAL_API === '1'
const channel = process.env.FINSIGHT_E2E_CHANNEL
if (channel && !['chromium', 'chrome', 'msedge'].includes(channel)) throw new Error('Unsupported E2E browser channel')

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  workers: 1,
  timeout: 45000,
  expect: { timeout: 15000 },
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'], ...(channel ? { channel } : {}) } }],
  webServer: [
    ...externalApi ? [] : [{
      command: 'node e2e/start-api.mjs',
      url: `${apiUrl}/api/v1/_e2e/status`,
      reuseExistingServer: false,
      timeout: 180000,
      stdout: 'pipe' as const,
      stderr: 'pipe' as const,
    }],
    {
      command: 'node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 4173 --strictPort',
      url: 'http://127.0.0.1:4173',
      reuseExistingServer: false,
      env: { API_PROXY_TARGET: apiUrl },
      stdout: 'pipe',
      stderr: 'pipe',
    },
  ],
})
