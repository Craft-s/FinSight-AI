import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: { port: 5173, proxy: { '/api': { target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000', changeOrigin: true }, '/health': { target: process.env.API_PROXY_TARGET || 'http://127.0.0.1:8000' } } },
  build: { chunkSizeWarningLimit: 1200, rollupOptions: { output: { manualChunks: { charts: ['echarts'], ui: ['element-plus'] } } } },
})
