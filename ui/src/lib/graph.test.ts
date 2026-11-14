import { describe, expect, it } from "vitest"
import type { GraphEdge, GraphNode } from "./api"
import { connectedGroups, edgePath, layoutLayers } from "./graph"

const node = (id: string, entity_type: string, name = id): GraphNode => ({
  id,
  name,
  entity_type,
  canonical_key: id,
  lifecycle_status: "active",
  state: "inferred",
})

const edge = (from: string, to: string, relation_type = "depends_on"): GraphEdge => ({
  id: `${from}-${to}`,
  from,
  to,
  relation_type,
  origin: "inferred",
  basis: null,
  evidence_id: null,
})

describe("connectedGroups", () => {
  it("separa los grupos sin relacion y pone primero el mayor", () => {
    const nodes = ["a", "b", "c", "d", "e"].map((id) => node(id, "business_rule"))
    const groups = connectedGroups(nodes, [edge("a", "b"), edge("d", "e", "related_to"), edge("e", "c", "related_to")])
    expect(groups.map((group) => group.nodes.map((n) => n.id).sort())).toEqual([["c", "d", "e"], ["a", "b"]])
    expect(groups[1].edges.map((e) => e.id)).toEqual(["a-b"])
  })
})

describe("layoutLayers", () => {
  it("pone a la izquierda quien depende y no se cuelga con ciclos", () => {
    const nodes = ["a", "b", "c", "d"].map((id) => node(id, "business_rule"))
    const layout = layoutLayers(nodes, [edge("a", "b"), edge("b", "c"), edge("a", "d", "related_to")])
    const x = (id: string) => layout.positions.get(id)?.x ?? -1
    expect(x("a")).toBeLessThan(x("b"))
    expect(x("b")).toBeLessThan(x("c"))
    expect(x("d")).toBe(x("a"))
    const cycle = layoutLayers(nodes.slice(0, 2), [edge("a", "b"), edge("b", "a")])
    expect(cycle.positions.size).toBe(2)
  })
})

describe("edgePath", () => {
  it("une capas vecinas en recta, rodea las intermedias y curva dentro de la misma capa", () => {
    // a -> b -> c, and a also calls c: c ends two layers to the right of a.
    const nodes = ["a", "b", "c", "d"].map((id) => node(id, "business_rule"))
    const layout = layoutLayers(nodes, [edge("a", "b"), edge("b", "c"), edge("a", "c")])
    const a = layout.positions.get("a")!
    const b = layout.positions.get("b")!
    expect(edgePath(edge("a", "b"), layout)).toBe(
      `M ${a.x + layout.nodeWidth} ${a.y + layout.nodeHeight / 2} L ${b.x} ${b.y + layout.nodeHeight / 2}`,
    )
    const lane = a.y + layout.nodeHeight + layout.gapY / 2
    expect(edgePath(edge("a", "c"), layout)).toContain(`V ${lane}`)
    expect(edgePath(edge("a", "d", "related_to"), layout)).toMatch(/^M .* C /)
    expect(edgePath(edge("a", "zz"), layout)).toBeNull()
  })
})
