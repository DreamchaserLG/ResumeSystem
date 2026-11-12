import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': {
        // 宿主开发默认 8001；compose 容器内通过 API_PROXY_TARGET=http://api:8000 覆盖
        target: process.env.API_PROXY_TARGET ?? 'http://localhost:8001',
        changeOrigin: true,
      },
    },
  },
})
