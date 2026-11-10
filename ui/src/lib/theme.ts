export type ThemePreference = "system" | "light" | "dark"

const THEME_KEY = "scrinium.theme"
const darkQuery = window.matchMedia("(prefers-color-scheme: dark)")

export function storedTheme(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_KEY)
    return value === "light" || value === "dark" ? value : "system"
  } catch {
    return "system"
  }
}

function apply(preference: ThemePreference) {
  const dark = preference === "dark" || (preference === "system" && darkQuery.matches)
  document.documentElement.classList.toggle("dark", dark)
}

/** Applies the stored theme and follows the system while the preference is "system". */
export function initTheme() {
  apply(storedTheme())
  darkQuery.addEventListener("change", () => apply(storedTheme()))
}

export function setTheme(preference: ThemePreference) {
  try {
    if (preference === "system") window.localStorage.removeItem(THEME_KEY)
    else window.localStorage.setItem(THEME_KEY, preference)
  } catch {
    // Without storage, the theme lasts as long as the window.
  }
  apply(preference)
}
