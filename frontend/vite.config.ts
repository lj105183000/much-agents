import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

// E2E runs the API on an isolated port so it cannot touch a developer's
// running stack. Production/dev keep the historical 8001 default.
const apiTarget = process.env.E2E_API_URL?.trim() || 'http://127.0.0.1:8001'

const ELECTRON_CSP = [
  "default-src 'self' file:",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "script-src 'self' file:",
  "style-src 'self' file: 'unsafe-inline'",
  "img-src 'self' file: data: blob:",
  "font-src 'self' file: data:",
  "connect-src 'self' file: http://127.0.0.1:* http://localhost:* https:",
  "worker-src 'self' blob:",
].join('; ')

function electronCsp(): Plugin {
  return {
    name: 'much-agents-electron-csp',
    apply: 'build',
    transformIndexHtml() {
      return [
        {
          tag: 'meta',
          attrs: {
            'http-equiv': 'Content-Security-Policy',
            content: ELECTRON_CSP,
          },
          injectTo: 'head',
        },
      ]
    },
  }
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), electronCsp()],
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
