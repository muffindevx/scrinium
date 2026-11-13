import { describe, expect, it } from "vitest"
import { columns, layoutArchitecture } from "@/lib/architecture"

describe("columns", () => {
  it("puts callers to the left of what they call", () => {
    const found = columns(["api", "billing", "db"], [
      { from: "api", to: "billing" },
      { from: "billing", to: "db" },
      { from: "api", to: "db" },
    ])
    expect([found.get("api"), found.get("billing"), found.get("db")]).toEqual([0, 1, 2])
  })

  it("breaks a cycle the same way every time", () => {
    const edges = [
      { from: "a", to: "b" },
      { from: "b", to: "a" },
    ]
    expect(columns(["b", "a"], edges)).toEqual(columns(["a", "b"], edges))
  })
})

describe("layoutArchitecture", () => {
  it("stacks the modules of a column and sizes the drawing", () => {
    const layout = layoutArchitecture(["main", "game", "screens"], [
      { from: "main", to: "game" },
      { from: "main", to: "screens" },
    ])
    const game = layout.boxes.get("game")
    const screens = layout.boxes.get("screens")
    expect(game?.column).toBe(1)
    expect(screens?.column).toBe(1)
    expect(game?.x).toBe(screens?.x)
    expect(game?.y).not.toBe(screens?.y)
    expect(layout.width).toBeGreaterThan(2 * layout.boxWidth)
  })
})
