import { describe, expect, it } from "vitest"
import { EN } from "@/lib/i18n-en"
import { isValidElement, type ReactElement } from "react"
import { adoptLanguage, t, tn } from "@/lib/i18n"

describe("i18n", () => {
  it("fills numbered placeholders", () => {
    adoptLanguage("es")
    expect(t("{0} reglas revisadas", 3)).toBe("3 reglas revisadas")
    adoptLanguage("en")
    expect(t("{0} reglas revisadas", 3)).toBe("3 rules reviewed")
    expect(t("Texto que nadie tradujo")).toBe("Texto que nadie tradujo")
    adoptLanguage("es")
  })

  it("keeps every placeholder in the English text", () => {
    const holes = (text: string) => (text.match(/\{\d+\}/g) ?? []).sort().join()
    for (const [key, value] of Object.entries(EN)) expect([key, holes(value)]).toEqual([key, holes(key)])
  })

  it("puts markup where each language puts it", () => {
    const children = (node: unknown) => (isValidElement(node) ? (node as ReactElement<{ children: unknown[] }>).props.children : [])
    adoptLanguage("en")
    expect(children(tn("Se leerán {0} archivos ({1}) y se excluirán {2}.", "A", "B", "C"))).toEqual([
      "", "A", " files (", "B", ") will be read and ", "C", " excluded.",
    ])
    adoptLanguage("es")
    expect(children(tn("Escribe {0} para confirmar", "X"))).toEqual(["Escribe ", "X", " para confirmar"])
  })

  it("keeps a leading or trailing space when the Spanish key has one", () => {
    for (const [key, value] of Object.entries(EN)) {
      expect([key, value.startsWith(" "), value.endsWith(" ")]).toEqual([key, key.startsWith(" "), key.endsWith(" ")])
    }
  })
})
