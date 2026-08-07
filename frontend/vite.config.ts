import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const backendUrl = process.env.SHOPPING_AGENT_BACKEND_URL ?? "http://127.0.0.1:8000";
const backendWsUrl = backendUrl.replace(/^http/, "ws");

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: Number(process.env.SHOPPING_AGENT_FRONTEND_PORT ?? 5173),
    proxy: {
      "/api": {
        target: backendUrl,
        changeOrigin: true,
      },
      "/ws": {
        target: backendWsUrl,
        ws: true,
      },
    },
  },
});
