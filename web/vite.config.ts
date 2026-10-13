import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Relative asset paths, so the built dist/ can be hosted under any path.
export default defineConfig({
  plugins: [react()],
  base: './',
})
