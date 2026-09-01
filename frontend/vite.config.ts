import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  envDir: '../', // Đọc biến môi trường từ file .env ở thư mục gốc (P-118)
  server: {
    port: 5173,
    proxy: {
      // Proxy API về backend FastAPI (cổng mặc định 8000 trong docker-compose).
      // Trong dev, frontend gọi /api/... và Vite chuyển tiếp sang backend,
      // tránh CORS và không cần hardcode base URL.
      '/api': {
        target: process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
