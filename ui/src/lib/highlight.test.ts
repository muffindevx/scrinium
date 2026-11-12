import { describe, expect, it } from "vitest"
import { languageFor, themeName } from "@/lib/highlight"

describe("languageFor", () => {
  it("maps the languages Scrinium reads to their grammar", () => {
    expect(languageFor("src/game/door.ts")).toBe("typescript")
    expect(languageFor("web/App.TSX")).toBe("tsx")
    expect(languageFor("drizzle/0021_fn_soa_approve.sql")).toBe("sql")
    expect(languageFor("app/models.py")).toBe("python")
  })

  it("leaves files without a grammar as plain text", () => {
    expect(languageFor("README.md")).toBeNull()
    expect(languageFor("Makefile")).toBeNull()
    expect(languageFor("config/.env")).toBeNull()
  })
})

describe("themeName", () => {
  it("follows light or dark mode only for GitHub", () => {
    expect(themeName("github", false)).toBe("github-light")
    expect(themeName("github", true)).toBe("github-dark")
    expect(themeName("dracula", false)).toBe("dracula")
    expect(themeName("one-dark-pro", true)).toBe("one-dark-pro")
  })
})
