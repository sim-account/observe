import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

export default defineConfig({
  plugins: [
    {
      name: 'supported-log-mime-types',
      enforce: 'pre',
      transform(code, id) {
        if (!id.includes('/file-selector/') || !id.endsWith('/file.js')) return null
        // Uploads accept only CSV and Excel. Avoid bundling the unrelated vendor format registry.
        const replacement = 'COMMON_MIME_TYPES = new Map([["csv", "text/csv"], ["xls", "application/vnd.ms-excel"], ["xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"]]);'
        return code.replace(/COMMON_MIME_TYPES = new Map\(\[[\s\S]*?\]\);/, replacement)
      },
    },
    react(), tailwindcss(),
  ],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
