import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// During development (npm run dev) the website runs on port 5173 and forwards
// all API and live-update traffic to the Python backend on port 8000.
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    proxy: {
      '/api': 'http://localhost:8000',
      '/admin/api-docs': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
