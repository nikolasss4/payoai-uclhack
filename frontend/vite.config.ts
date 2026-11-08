import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Builds into web/dist, which the API serves at "/". In development, Vite proxies /api to the API on API_PORT (default 8743).
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  build: { outDir: "../web/dist", emptyOutDir: true, assetsDir: "assets", sourcemap: false, assetsInlineLimit: 0 },
  server: { port: 5173, proxy: { "/api": { target: `http://127.0.0.1:${loadEnv(mode, ".", "API_").API_PORT || "8743"}`, changeOrigin: false } } },
}));
