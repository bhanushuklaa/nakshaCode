import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the API runs on :8000; in production nginx serves both.
export default defineConfig({
  plugins: [react()],
  build: { chunkSizeWarningLimit: 800 },
  preview: { proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } } },
  server: {
    proxy: { "/api": { target: "http://localhost:8000", changeOrigin: true } },
  },
});
