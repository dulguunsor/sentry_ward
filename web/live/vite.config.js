import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The build goes straight into the Python package, which `ward live` serves.
// `npm run dev` proxies the API to a running `ward live` on port 8765.
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: { outDir: "../../src/sentryward/web/live", emptyOutDir: true },
  server: {
    proxy: { "/api": "http://localhost:8765", "/dashboard": "http://localhost:8765" },
  },
  test: { environment: "node" },
});
