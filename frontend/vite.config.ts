import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        // Stable vendor chunks keep their hash across app-only deploys, so returning users re-download less.
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          query: ["@tanstack/react-query"],
          icons: ["lucide-react"],
        },
      },
    },
  },
  server: {
    proxy: {
      "/api": "http://localhost:7979",
    },
  },
});
