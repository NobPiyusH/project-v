import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev server on localhost only; /api is proxied to the Spring Boot control plane.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    // 127.0.0.1, not localhost: the control plane runs with preferIPv4Stack and Node would try ::1 first.
    proxy: { '/api': 'http://127.0.0.1:8080' },
  },
})
