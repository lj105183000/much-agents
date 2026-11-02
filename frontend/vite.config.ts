import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// E2E runs the API on an isolated port so it cannot touch a developer's
// running stack. Production/dev keep the historical 8001 default.
const apiTarget = process.env.E2E_API_URL?.trim() || 'http://127.0.0.1:8001'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Electron loads the production build over file://, so assets must be relative.
  base: './',
  server: {
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/docs': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/redoc': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/openapi.json': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
