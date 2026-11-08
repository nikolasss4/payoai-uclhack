import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Builds into web/dist, which the API serves at "/". In development, Vite proxies /api to the API on port 8000.
export default defineConfig({
  plugins: [react()],
  build: { outDir: "../web/dist", emptyOutDir: true, assetsDir: "assets", sourcemap: false, assetsInlineLimit: 0 },
  server: { port: 5173, proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } } },
});
