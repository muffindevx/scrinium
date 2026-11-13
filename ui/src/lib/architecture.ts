/** Layout of the architecture view: modules in columns by dependency, callers on the left. */

export interface ModuleBox {
  key: string
  x: number
  y: number
  column: number
}

export interface ArchitectureLayout {
  boxes: Map<string, ModuleBox>
  width: number
  height: number
  boxWidth: number
  boxHeight: number
}

/**
 * Column of each module: the longest chain of calls that reaches it. Cycles are broken
 * deterministically (by name), so the same code is always drawn the same way.
 */
export function columns(keys: string[], edges: { from: string; to: string }[]): Map<string, number> {
  const known = new Set(keys)
  const out = new Map<string, string[]>()
  const indegree = new Map<string, number>(keys.map((key) => [key, 0]))
  for (const { from, to } of edges) {
    if (!known.has(from) || !known.has(to) || from === to) continue
    out.set(from, [...(out.get(from) ?? []), to])
    indegree.set(to, (indegree.get(to) ?? 0) + 1)
  }
  const column = new Map<string, number>(keys.map((key) => [key, 0]))
  const done = new Set<string>()
  const sorted = [...keys].sort()
  while (done.size < keys.length) {
    // The next module with nothing left pointing at it; in a cycle, the one with the fewest.
    let next = sorted.find((key) => !done.has(key) && (indegree.get(key) ?? 0) === 0)
    if (next === undefined) {
      next = sorted
        .filter((key) => !done.has(key))
        .reduce((best, key) => ((indegree.get(key) ?? 0) < (indegree.get(best) ?? 0) ? key : best))
    }
    done.add(next)
    for (const target of out.get(next) ?? []) {
      if (done.has(target)) continue
      indegree.set(target, (indegree.get(target) ?? 0) - 1)
      column.set(target, Math.max(column.get(target) ?? 0, (column.get(next) ?? 0) + 1))
    }
  }
  return column
}

export function layoutArchitecture(
  keys: string[],
  edges: { from: string; to: string }[],
  { boxWidth = 200, boxHeight = 60, gapX = 96, gapY = 14, padding = 16 } = {},
): ArchitectureLayout {
  const column = columns(keys, edges)
  const byColumn = new Map<number, string[]>()
  for (const key of [...keys].sort()) {
    const index = column.get(key) ?? 0
    byColumn.set(index, [...(byColumn.get(index) ?? []), key])
  }
  const boxes = new Map<string, ModuleBox>()
  let tallest = 0
  for (const [index, members] of byColumn) {
    members.forEach((key, row) => {
      boxes.set(key, {
        key,
        column: index,
        x: padding + index * (boxWidth + gapX),
        y: padding + row * (boxHeight + gapY),
      })
    })
    tallest = Math.max(tallest, members.length)
  }
  const width = padding * 2 + Math.max(1, byColumn.size) * (boxWidth + gapX) - gapX
  const height = padding * 2 + Math.max(1, tallest) * (boxHeight + gapY) - gapY
  return { boxes, width, height, boxWidth, boxHeight }
}

/** A curve from the right side of one box to the left side of another (or back, above). */
export function dependencyPath(from: ModuleBox, to: ModuleBox, w: number, h: number): string {
  const x1 = from.x + w
  const y1 = from.y + h / 2
  const x2 = to.x
  const y2 = to.y + h / 2
  if (x2 > x1) {
    const bend = (x2 - x1) / 2
    return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`
  }
  // Backwards or in the same column: loop over the top so it does not cross the boxes.
  const top = Math.min(from.y, to.y) - 12
  return `M ${from.x + w / 2} ${from.y} C ${from.x + w / 2} ${top}, ${to.x + w / 2} ${top}, ${to.x + w / 2} ${to.y}`
}
