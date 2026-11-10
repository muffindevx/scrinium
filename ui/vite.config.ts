import path from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

const runtimeOrigin = `http://127.0.0.1:${process.env.SCRINIUM_PORT ?? "47615"}`

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  build: { outDir: "../runtime/src/scrinium/web_dist", emptyOutDir: true },
  server: {
    host: "127.0.0.1",
    // Development only: forwards to the runtime presenting the origin it accepts.
    proxy: {
      "/api": { target: runtimeOrigin, changeOrigin: true, headers: { Origin: runtimeOrigin } },
    },
  },
})
