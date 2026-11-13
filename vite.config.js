import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // the backend only accepts requests from these origins (HIVE_ALLOWED_ORIGINS), so fail loudly
  // instead of silently moving to another port
  server: { port: 5173, strictPort: true },
  preview: { port: 4173, strictPort: true },
  build: {
    rollupOptions: {
      // three.js is most of the weight; its own chunk caches apart from the app code
      output: { manualChunks: { three: ["three"] } },
    },
    chunkSizeWarningLimit: 700,
  },
});
