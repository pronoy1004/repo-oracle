import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In dev the UI runs on 5173 and the API on 8000; proxying keeps one origin so there is
// no CORS config to get wrong. In production both are served by the same FastAPI process.
const api = { target: process.env.ORACLE_API ?? 'http://localhost:8000', changeOrigin: true }

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/repos': api,
      '/chat': api,
      '/traces': api,
      '/healthz': api,
    },
  },
})
