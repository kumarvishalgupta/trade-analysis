import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  // GitHub Pages serves the site at https://<username>.github.io/<repo>/
  // For production builds we read the repo name from VITE_BASE (set by the
  // GitHub Actions workflow). For local `npm run dev` we keep the root path.
  base: command === 'build' ? (process.env.VITE_BASE || '/trade-analysis/') : '/',
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
  },
}))
