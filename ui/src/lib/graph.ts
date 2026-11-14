import type { GraphEdge, GraphNode } from "@/lib/api"

export interface Layout {
  positions: Map<string, { x: number; y: number }>
  width: number
  height: number
  nodeWidth: number
  nodeHeight: number
  gapX: number
  gapY: number
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

/** Groups of nodes joined by edges, largest first; each keeps its own edges. */
export function connectedGroups(nodes: GraphNode[], edges: GraphEdge[]): { nodes: GraphNode[]; edges: GraphEdge[] }[] {
  const parent = new Map(nodes.map((node) => [node.id, node.id]))
  const root = (id: string): string => {
    let current = id
    while (parent.get(current) !== current) current = parent.get(current) ?? current
    return current
  }
  for (const edge of edges) {
    if (parent.has(edge.from) && parent.has(edge.to)) parent.set(root(edge.from), root(edge.to))
  }
  const groups = new Map<string, { nodes: GraphNode[]; edges: GraphEdge[] }>()
  for (const node of nodes) {
    const key = root(node.id)
    if (!groups.has(key)) groups.set(key, { nodes: [], edges: [] })
    groups.get(key)?.nodes.push(node)
  }
  for (const edge of edges) groups.get(root(edge.from))?.edges.push(edge)
  return [...groups.values()].sort((a, b) => b.nodes.length - a.nodes.length)
}

/**
 * Deterministic layout of one group in layers: whoever depends on (calls) another is to its left,
 * so a chain reads from left to right. Other relationships do not move nodes; they are drawn
 * between the layers the dependencies give.
 */
export function layoutLayers(
  nodes: GraphNode[],
  edges: GraphEdge[],
  { nodeWidth = 200, nodeHeight = 40, gapX = 56, gapY = 16, padding = 12, top = 0 } = {},
): Layout {
  const ids = new Set(nodes.map((node) => node.id))
  const depends = edges.filter((edge) => edge.relation_type === "depends_on" && ids.has(edge.from) && ids.has(edge.to))
  const layer = new Map(nodes.map((node) => [node.id, 0]))
  // Longest path, bounded: a cycle stops growing after as many rounds as there are nodes.
  for (let round = 0; round < nodes.length; round++) {
    let changed = false
    for (const edge of depends) {
      const next = (layer.get(edge.from) ?? 0) + 1
      if (next > (layer.get(edge.to) ?? 0) && next < nodes.length) {
        layer.set(edge.to, next)
        changed = true
      }
    }
    if (!changed) break
  }
  const layers = [...new Set(layer.values())].sort((a, b) => a - b)
  const positions = new Map<string, { x: number; y: number }>()
  let rows = 0
  layers.forEach((value, column) => {
    const members = nodes.filter((node) => layer.get(node.id) === value).sort((a, b) => a.name.localeCompare(b.name, "es"))
    const x = padding + column * (nodeWidth + gapX)
    members.forEach((node, row) => positions.set(node.id, { x, y: top + padding + row * (nodeHeight + gapY) }))
    rows = Math.max(rows, members.length)
  })
  return {
    positions,
    // Room on the right for the curves between nodes of the last column.
    width: padding * 2 + Math.max(layers.length, 1) * (nodeWidth + gapX) - gapX + 44,
    height: top + padding * 2 + Math.max(rows, 1) * (nodeHeight + gapY) - gapY,
    nodeWidth,
    nodeHeight,
    gapX,
    gapY,
  }
}
