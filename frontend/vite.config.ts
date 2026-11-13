import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The API runs on :8000 in development. CORS is configured on the backend (cors_origins).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
