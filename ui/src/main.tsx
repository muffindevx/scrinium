import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import "./index.css"
import App from "./App"
import { initTheme } from "@/lib/theme"

initTheme()

// After an update the old chunks are gone: a tab opened before it can no longer load the code
// colors or a screen. Reload once to pick up the new build, instead of failing quietly.
window.addEventListener("vite:preloadError", (event) => {
  const key = "scrinium.reloadedForUpdate"
  try {
    if (window.sessionStorage.getItem(key) === document.querySelector("script[type=module]")?.getAttribute("src")) return
    window.sessionStorage.setItem(key, document.querySelector("script[type=module]")?.getAttribute("src") ?? "")
  } catch {
    return
  }
  event.preventDefault()
  window.location.reload()
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
