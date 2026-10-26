import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const api = process.env.BAWBEE_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': api,
      '/health': api,
    },
  },
})
