import { describe, expect, it } from "vitest"
import { EN } from "@/lib/i18n-en"
import { adoptLanguage, t } from "@/lib/i18n"

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
})
