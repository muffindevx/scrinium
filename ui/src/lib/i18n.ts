import { createElement, Fragment, type ReactNode } from "react"
import { EN } from "@/lib/i18n-en"

/**
 * The console in Spanish or English (phase 5.5). The Spanish text is the key: `t("Bandeja")`.
 * Placeholders are numbered: `t("{0} reglas", n)`. The language is the workspace's setting
 * (it also fixes the language the models write in); this browser keeps a copy so the first
 * paint is already in the right language, and a change reloads the page.
 */
export type Language = "es" | "en"

const LANGUAGE_KEY = "scrinium.language"

function stored(): Language {
  if (typeof window === "undefined") return "es"
  try {
    return window.localStorage.getItem(LANGUAGE_KEY) === "en" ? "en" : "es"
  } catch {
    return "es"
  }
}

let current: Language = stored()
if (typeof document !== "undefined") document.documentElement.lang = current

export function language(): Language {
  return current
}

/** Keeps the workspace's language; true if it differs from the one on screen (reload). */
export function adoptLanguage(next: Language): boolean {
  try {
    window.localStorage.setItem(LANGUAGE_KEY, next)
  } catch {
    // Without storage the console follows the workspace on every load.
  }
  const changed = next !== current
  current = next
  return changed
}

export function t(key: string, ...args: (string | number | null | undefined)[]): string {
  const text = current === "en" ? (EN[key] ?? key) : key
  if (args.length === 0) return text
  return text.replace(/\{(\d+)\}/g, (match, index: string) => {
    const value = args[Number(index)]
    return value === undefined || value === null ? match : String(value)
  })
}

/**
 * `t` for a sentence with markup inside: `tn("Se leerán {0} archivos", <strong>{n}</strong>)`.
 * The whole sentence is one key, so each language puts the pieces in its own order.
 */
export function tn(key: string, ...nodes: ReactNode[]): ReactNode {
  const text = current === "en" ? (EN[key] ?? key) : key
  const parts = text.split(/\{(\d+)\}/).map((part, index) => (index % 2 === 1 ? nodes[Number(part)] : part))
  return createElement(Fragment, null, ...parts)
}
