// Vite = the tool that runs the React app during development and builds it for production.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  // The charting library is large; that's fine for this app, so don't warn about bundle size.
  build: { chunkSizeWarningLimit: 1500 },
  server: {
    port: 5173,
    // During development the React app runs on :5173 and the Python API on :8000.
    // This forwards every /api/... request to the Python server, so no CORS setup is needed.
    proxy: { "/api": "http://localhost:8000" },
  },
});
