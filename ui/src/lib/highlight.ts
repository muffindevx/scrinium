import type { HighlighterCore, ThemedToken } from "shiki/core"

/**
 * Syntax highlighting for cited code, with the grammars and themes of VS Code (Shiki).
 * Everything loads on demand: a theme or a language costs nothing until a rule shows it.
 * Tokens are rendered as React text, never as HTML.
 */

export type CodeThemeId = "github" | "one-dark-pro" | "dracula"

type ThemeName = "github-light" | "github-dark" | "one-dark-pro" | "dracula"

export const CODE_THEMES: { id: CodeThemeId; label: string; hint: string }[] = [
  { id: "github", label: "GitHub", hint: "Claro u oscuro, como la aplicación" },
  { id: "one-dark-pro", label: "One Dark Pro", hint: "Oscuro" },
  { id: "dracula", label: "Dracula", hint: "Oscuro" },
]

const THEME_LOADERS: Record<ThemeName, () => Promise<{ default: unknown }>> = {
  "github-light": () => import("shiki/dist/themes/github-light.mjs"),
  "github-dark": () => import("shiki/dist/themes/github-dark.mjs"),
  "one-dark-pro": () => import("shiki/dist/themes/one-dark-pro.mjs"),
  dracula: () => import("shiki/dist/themes/dracula.mjs"),
}

// The languages Scrinium reads with a grammar, plus SQL.
const LANGUAGE_LOADERS: Record<string, () => Promise<{ default: unknown }>> = {
  python: () => import("shiki/dist/langs/python.mjs"),
  javascript: () => import("shiki/dist/langs/javascript.mjs"),
  jsx: () => import("shiki/dist/langs/jsx.mjs"),
  typescript: () => import("shiki/dist/langs/typescript.mjs"),
  tsx: () => import("shiki/dist/langs/tsx.mjs"),
  java: () => import("shiki/dist/langs/java.mjs"),
  go: () => import("shiki/dist/langs/go.mjs"),
  rust: () => import("shiki/dist/langs/rust.mjs"),
  csharp: () => import("shiki/dist/langs/csharp.mjs"),
  php: () => import("shiki/dist/langs/php.mjs"),
  ruby: () => import("shiki/dist/langs/ruby.mjs"),
  kotlin: () => import("shiki/dist/langs/kotlin.mjs"),
  sql: () => import("shiki/dist/langs/sql.mjs"),
}

const EXTENSIONS: Record<string, string> = {
  py: "python",
  js: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  jsx: "jsx",
  ts: "typescript",
  mts: "typescript",
  cts: "typescript",
  tsx: "tsx",
  java: "java",
  go: "go",
  rs: "rust",
  cs: "csharp",
  php: "php",
  rb: "ruby",
  kt: "kotlin",
  kts: "kotlin",
  sql: "sql",
}

export function languageFor(path: string): string | null {
  const name = path.slice(path.lastIndexOf("/") + 1)
  const dot = name.lastIndexOf(".")
  return dot > 0 ? (EXTENSIONS[name.slice(dot + 1).toLowerCase()] ?? null) : null
}

const CODE_THEME_KEY = "scrinium.codeTheme"
const CHANGE_EVENT = "scrinium:code-theme"

export function storedCodeTheme(): CodeThemeId {
  try {
    const value = window.localStorage.getItem(CODE_THEME_KEY)
    return CODE_THEMES.some((theme) => theme.id === value) ? (value as CodeThemeId) : "github"
  } catch {
    return "github"
  }
}

export function setCodeTheme(theme: CodeThemeId) {
  try {
    if (theme === "github") window.localStorage.removeItem(CODE_THEME_KEY)
    else window.localStorage.setItem(CODE_THEME_KEY, theme)
  } catch {
    // Without storage, the choice lasts as long as the window.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT))
}

export function onCodeThemeChange(listener: () => void): () => void {
  window.addEventListener(CHANGE_EVENT, listener)
  return () => window.removeEventListener(CHANGE_EVENT, listener)
}

/** The Shiki theme for a choice: GitHub follows the application's light or dark mode. */
export function themeName(theme: CodeThemeId, dark: boolean): ThemeName {
  if (theme === "github") return dark ? "github-dark" : "github-light"
  return theme
}

let highlighter: Promise<HighlighterCore> | null = null
const loaded = new Set<string>()

/** Shiki itself also loads on first use: it stays out of the application's main bundle. */
function core(): Promise<HighlighterCore> {
  highlighter ??= Promise.all([import("shiki/core"), import("shiki/engine/javascript")]).then(
    ([shiki, engine]) =>
      shiki.createHighlighterCore({ themes: [], langs: [], engine: engine.createJavaScriptRegexEngine() }),
  )
  return highlighter
}

export interface Highlighted {
  lines: ThemedToken[][]
  background: string
  foreground: string
}

/** Colored tokens per line, or null when the file's language has no grammar here. */
export async function highlight(code: string, path: string, theme: ThemeName): Promise<Highlighted | null> {
  const language = languageFor(path)
  if (!language) return null
  const shiki = await core()
  if (!loaded.has(theme)) {
    await shiki.loadTheme(THEME_LOADERS[theme]() as never)
    loaded.add(theme)
  }
  if (!loaded.has(language)) {
    await shiki.loadLanguage(LANGUAGE_LOADERS[language]() as never)
    loaded.add(language)
  }
  const result = shiki.codeToTokens(code, { lang: language, theme })
  return { lines: result.tokens, background: result.bg ?? "", foreground: result.fg ?? "" }
}
