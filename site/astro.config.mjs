// @ts-check
import { defineConfig } from "astro/config"

// The same build serves GitHub Pages and Codeberg Pages: each sets where the site lives.
//   GitHub:   SITE=https://muffindevx.github.io     BASE=/scrinium
//   Codeberg: SITE=https://muffindevx.codeberg.page BASE=/scrinium
export default defineConfig({
  site: process.env.SITE ?? "https://muffindevx.github.io",
  base: process.env.BASE ?? "/scrinium",
  trailingSlash: "ignore",
  build: { assets: "assets" },
})
