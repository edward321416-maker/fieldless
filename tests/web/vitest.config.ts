import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const here = (path: string) => fileURLToPath(new URL(path, import.meta.url));

// The app's sources are read from web/src, with the packages installed here, so web/ itself needs no change.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      react: here("./node_modules/react"),
      "react-dom": here("./node_modules/react-dom"),
      "maplibre-gl": here("./node_modules/maplibre-gl"),
    },
  },
  server: { fs: { allow: [here("../..")] } },
  test: { environment: "jsdom", include: ["*.test.tsx"] },
});
