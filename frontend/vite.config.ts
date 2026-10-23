import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Electron loads the production build over file://, so assets must be relative.
  base: './',
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/docs': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/redoc': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      '/openapi.json': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
