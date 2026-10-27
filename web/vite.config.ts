import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// During development the API runs on port 8000 (uvicorn); Vite forwards /api to it.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
  build: { outDir: "dist" },
  test: { environment: "node" },
});
