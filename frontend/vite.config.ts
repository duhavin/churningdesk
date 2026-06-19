import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev server proxies /api to the FastAPI backend on :8000.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // Listen on all interfaces and accept any Host header so temporary tunnels
    // (e.g. Cloudflare quick tunnels, ngrok) can reach the dev server without
    // needing each random *.trycloudflare.com host added here.
    host: true,
    allowedHosts: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
  },
});
