import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: the API runs on :8000; Vite proxies /api and /auth so cookies stay same-origin.
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 5000 },  // plotly chunk is lazy-loaded on the student page
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://localhost:8000',
      '/auth': 'http://localhost:8000',
    },
  },
})
