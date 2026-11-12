import { useEffect, useState } from "react"
import { type CodeThemeId, onCodeThemeChange, storedCodeTheme } from "@/lib/highlight"

export function useDarkMode(): boolean {
  const [dark, setDark] = useState(() => document.documentElement.classList.contains("dark"))
  useEffect(() => {
    const observer = new MutationObserver(() => setDark(document.documentElement.classList.contains("dark")))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] })
    return () => observer.disconnect()
  }, [])
  return dark
}

export function useCodeTheme(): CodeThemeId {
  const [theme, setTheme] = useState(storedCodeTheme)
  useEffect(() => onCodeThemeChange(() => setTheme(storedCodeTheme())), [])
  return theme
}
