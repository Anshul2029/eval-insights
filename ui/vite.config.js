import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 8080,
    strictPort: true,
    proxy: {
      '/traces': 'http://localhost:5001',
      '/trace': 'http://localhost:5001',
      '/evaluate': 'http://localhost:5001',
      '/health': 'http://localhost:5001',
      '/comparisons': 'http://localhost:5001',
      '/comparison': 'http://localhost:5001',
      '/insights': {
        target: 'http://localhost:8502',
        ws: true,
      },
    },
  },
})
