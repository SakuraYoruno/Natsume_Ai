import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  base: '/monitor/',
  build: {
    outDir: '../static/monitor',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('/react-router-dom/')
          ) return 'react-vendor'
          if (id.includes('/@xyflow/react/')) return 'flow-vendor'
          if (id.includes('/echarts/') || id.includes('/echarts-for-react/')) return 'echarts-vendor'
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
    proxy: {
      '/obs': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        ws: true,
      },
      '/metrics': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
      '/ui': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
