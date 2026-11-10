import { describe, expect, it } from "vitest"
import type { GraphNode } from "./api"
import { edgePath, layoutGraph } from "./graph"

const node = (id: string, entity_type: string, name = id): GraphNode => ({
  id,
  name,
  entity_type,
  canonical_key: id,
  lifecycle_status: "active",
  state: "inferred",
})

describe("layoutGraph", () => {
  it("ordena las columnas de la intencion al codigo y no solapa nodos", () => {
    const layout = layoutGraph([
      node("t", "test_case"),
      node("r2", "business_rule", "b"),
      node("s", "service"),
      node("r1", "business_rule", "a"),
    ])
    expect(layout.columns.map((c) => c.entityType)).toEqual(["business_rule", "service", "test_case"])
    const r1 = layout.positions.get("r1")!
    const r2 = layout.positions.get("r2")!
    expect(r1.x).toBe(r2.x)
    expect(r2.y - r1.y).toBeGreaterThanOrEqual(layout.nodeHeight)
    expect(layout.positions.get("t")!.x).toBeGreaterThan(layout.positions.get("s")!.x)
  })

  it("es determinista y no falla sin nodos", () => {
    const nodes = [node("a", "module"), node("b", "unknown_kind")]
    expect(layoutGraph(nodes)).toEqual(layoutGraph([...nodes].reverse()))
    expect(layoutGraph([]).width).toBeGreaterThan(0)
  })

  it("une columnas vecinas en recta y rodea las columnas intermedias", () => {
    const layout = layoutGraph([
      node("a", "business_rule"),
      node("b", "business_rule"),
      node("f", "business_flow"),
      node("s", "service"),
    ])
    const edge = (from: string, to: string) => ({
      id: `${from}-${to}`, from, to, relation_type: "x", origin: "inferred" as const, evidence_id: null,
    })
    const a = layout.positions.get("a")!
    const f = layout.positions.get("f")!
    expect(edgePath(edge("a", "f"), layout)).toBe(
      `M ${a.x + layout.nodeWidth} ${a.y + layout.nodeHeight / 2} L ${f.x} ${f.y + layout.nodeHeight / 2}`,
    )
    // From the rule to the service it skips the flow column: it goes through the gap under the row.
    const around = edgePath(edge("a", "s"), layout)!
    const lane = a.y + layout.nodeHeight + layout.gapY / 2
    expect(around).toContain(`V ${lane}`)
    expect(lane).toBeGreaterThan(f.y + layout.nodeHeight)
    expect(edgePath(edge("a", "b"), layout)).toMatch(/^M .* C /)
    expect(edgePath(edge("a", "zz"), layout)).toBeNull()
  })
})
