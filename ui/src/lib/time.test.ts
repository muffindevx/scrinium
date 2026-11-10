import { describe, expect, it } from "vitest"
import { relativeTime } from "./time"

describe("relativeTime", () => {
  const now = Date.parse("2026-11-09T12:00:00Z")

  it("habla en español y redondea a la unidad mayor", () => {
    expect(relativeTime("2026-11-09T11:55:00Z", now)).toBe("hace 5 minutos")
    expect(relativeTime("2026-11-09T09:00:00Z", now)).toBe("hace 3 horas")
    expect(relativeTime("2026-11-08T12:00:00Z", now)).toBe("ayer")
  })

  it("lo muy reciente no muestra segundos", () => {
    expect(relativeTime("2026-11-09T11:59:40Z", now)).toBe("hace un momento")
  })
})
