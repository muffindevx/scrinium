import type { GraphEdge, GraphNode } from "@/lib/api"

// Columns from left to right: from intent (requirements, rules) to code and tests.
const COLUMN_ORDER = [
  "requirement",
  "architecture_decision",
  "business_rule",
  "business_flow",
  "user_journey",
  "architecture_flow",
  "data_flow",
  "service",
  "module",
  "api_endpoint",
  "code_symbol",
  "event",
  "data_entity",
  "test_case",
  "ticket",
  "document",
  "release",
]

export interface Layout {
  positions: Map<string, { x: number; y: number }>
  columns: { entityType: string; x: number }[]
  width: number
  height: number
  nodeWidth: number
  nodeHeight: number
  gapX: number
  gapY: number
}

/** Deterministic layout by columns: the same graph is always drawn the same way. */
export function layoutGraph(
  nodes: GraphNode[],
  { nodeWidth = 180, nodeHeight = 40, gapX = 60, gapY = 22, padding = 16, top = 28 } = {},
): Layout {
  const rank = (type: string) => {
    const index = COLUMN_ORDER.indexOf(type)
    return index === -1 ? COLUMN_ORDER.length : index
  }
  const types = [...new Set(nodes.map((node) => node.entity_type))].sort(
    (a, b) => rank(a) - rank(b) || a.localeCompare(b),
  )
  const positions = new Map<string, { x: number; y: number }>()
  let rows = 0
  const columns = types.map((entityType, column) => {
    const x = padding + column * (nodeWidth + gapX)
    const members = nodes
      .filter((node) => node.entity_type === entityType)
      .sort((a, b) => a.name.localeCompare(b.name))
    members.forEach((node, row) => positions.set(node.id, { x, y: top + padding + row * (nodeHeight + gapY) }))
    rows = Math.max(rows, members.length)
    return { entityType, x }
  })
  return {
    positions,
    columns,
    width: padding * 2 + Math.max(types.length, 1) * (nodeWidth + gapX) - gapX,
    height: top + padding * 2 + Math.max(rows, 1) * (nodeHeight + gapY) - gapY,
    nodeWidth,
    nodeHeight,
    gapX,
    gapY,
  }
}

/**
 * Path of an edge as an SVG path `d`.
 *
 * Between neighbouring columns it goes straight. If it skips columns, it goes around the nodes in
 * between through the gap under the source row: a straight line would pass under other boxes and
 * look like it connects them. In the same column, a curve on the right.
 */
export function edgePath(edge: GraphEdge, layout: Layout): string | null {
  const from = layout.positions.get(edge.from)
  const to = layout.positions.get(edge.to)
  if (!from || !to) return null
  const { nodeWidth: w, nodeHeight: h, gapX, gapY } = layout
  const y1 = from.y + h / 2
  const y2 = to.y + h / 2
  if (to.x === from.x) {
    const x = from.x + w
    return `M ${x} ${y1} C ${x + 40} ${y1}, ${x + 40} ${y2}, ${x} ${y2}`
  }
  const forward = to.x > from.x
  const x1 = forward ? from.x + w : from.x
  const x2 = forward ? to.x : to.x + w
  const span = Math.round(Math.abs(to.x - from.x) / (w + gapX))
  if (span <= 1) return `M ${x1} ${y1} L ${x2} ${y2}`
  const out = x1 + (forward ? gapX / 2 : -gapX / 2)
  const back = x2 - (forward ? gapX / 2 : -gapX / 2)
  const lane = from.y + h + gapY / 2
  return `M ${x1} ${y1} H ${out} V ${lane} H ${back} V ${y2} H ${x2}`
}
