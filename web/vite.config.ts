import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxies /api -> FastAPI on :8000 so the browser makes same-origin calls
// (no CORS dance in dev). Change the target here if the backend runs elsewhere.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
