import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the page runs on Vite (port 5173) and the API on
// tradingagents-web (port 8765); the proxy keeps them one origin to the page.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: "http://127.0.0.1:8765" } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
