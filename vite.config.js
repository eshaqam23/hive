import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rollupOptions: {
      // three.js is most of the weight; its own chunk caches apart from the app code
      output: { manualChunks: { three: ["three"] } },
    },
    chunkSizeWarningLimit: 700,
  },
});
