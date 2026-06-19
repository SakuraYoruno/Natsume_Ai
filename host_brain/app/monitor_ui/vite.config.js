import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

const devApiTarget = process.env.VITE_DEV_API_TARGET || 'http://127.0.0.1:18080'
const devObsApiTarget = process.env.VITE_DEV_OBS_API_TARGET || 'http://127.0.0.1:18082'
const devHostApiTarget = process.env.VITE_DEV_HOST_API_TARGET || devApiTarget

const apiProxy = {
  '/obs': {
    target: devObsApiTarget,
    changeOrigin: true,
    ws: true,
  },
  '/api': {
    target: devObsApiTarget,
    changeOrigin: true,
  },
  '/metrics': {
    target: devHostApiTarget,
    changeOrigin: true,
  },
  '/ui': {
    target: devObsApiTarget,
    changeOrigin: true,
  },
  '/prompt-config': {
    target: devHostApiTarget,
    changeOrigin: true,
  },
  '/canon': {
    target: devHostApiTarget,
    changeOrigin: true,
  },
  '/bond': {
    target: devHostApiTarget,
    changeOrigin: true,
  },
  '/tools': {
    target: devHostApiTarget,
    changeOrigin: true,
  },
  '/scheduler': {
    target: devHostApiTarget,
    changeOrigin: true,
  },
  '/v1': {
    target: devHostApiTarget,
    changeOrigin: true,
  },
  '/internal': {
    target: devHostApiTarget,
    changeOrigin: true,
  },
}

export default defineConfig({
  plugins: [react()],
  base: '/monitor/',
  build: {
    outDir: '../static/monitor',
    emptyOutDir: true,
    modulePreload: false,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (id.includes('/echarts-for-react/')) return 'echarts-react'
          if (
            id.includes('/echarts/lib/chart/') ||
            id.includes('/echarts/charts.js')
          ) return 'echarts-chart'
          if (
            id.includes('/echarts/lib/component/') ||
            id.includes('/echarts/components.js')
          ) return 'echarts-component'
          if (id.includes('/echarts/')) return 'echarts-core'
          if (
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('/react-router-dom/')
          ) return 'react-vendor'
          if (id.includes('/@xyflow/react/')) return 'flow-vendor'
          if (id.includes('/zustand/') || id.includes('/lucide-react/')) return 'ui-vendor'
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: apiProxy,
  },
  preview: {
    proxy: apiProxy,
  },
})
