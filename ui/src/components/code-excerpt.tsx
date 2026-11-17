import { t } from "@/lib/i18n"
import { type ReactNode, useEffect, useRef, useState } from "react"
import { cn } from "cn"
import { getCitedLines } from "@/lib/api"
import { type Highlighted, highlight, themeName } from "@/lib/highlight"
import { useCodeTheme, useDarkMode } from "@/lib/use-code-theme"

/** Lines shown around the cited ones, so the rule reads in its context. */
export const CONTEXT_LINES = 4

type Loaded = { start: number; lines: string[] } | "error" | null

/** True once the element has come near the screen: long lists load code only as they scroll. */
function useNearScreen<T extends Element>(): [React.RefObject<T | null>, boolean] {
  const ref = useRef<T | null>(null)
  // Without IntersectionObserver (old browsers, tests), everything loads at once.
  const [near, setNear] = useState(() => typeof IntersectionObserver === "undefined")
  useEffect(() => {
    const element = ref.current
    if (!element || near) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) setNear(true)
      },
      { rootMargin: "400px" },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [near])
  return [ref, near]
}

/**
 * The cited lines of a rule with a few lines around them, numbered and highlighted like in the
 * editor. The cited ones are marked. Read from the stored revision, so they match the citation.
 */
export function CodeExcerpt({
  revisionId,
  path,
  startLine,
  endLine,
  context = CONTEXT_LINES,
  window,
  className,
}: {
  revisionId: string
  path: string
  startLine: number
  endLine: number
  context?: number
  /** Lines to show instead of the context around the citation: a whole function. */
  window?: [number, number]
  className?: string
}) {
  const [ref, near] = useNearScreen<HTMLDivElement>()
  const [loaded, setLoaded] = useState<Loaded>(null)

  // Primitives, so the effect does not run again on every render with a new array.
  const from = window ? window[0] : Math.max(1, startLine - context)
  const to = window ? window[1] : endLine + context
  useEffect(() => {
    if (!near) return
    const live = { current: true }
    getCitedLines(revisionId, from, to)
      .then((lines) => live.current && setLoaded({ start: lines.start, lines: lines.lines }))
      .catch(() => live.current && setLoaded("error"))
    return () => {
      live.current = false
    }
  }, [near, revisionId, from, to])

  const colored = useHighlighted(loaded === null || loaded === "error" ? null : loaded.lines, path)

  return (
    <div ref={ref}>
      {loaded === null ? (
        <CodeFrame className={className}>
          <p className="px-3 text-muted-foreground">{t("Cargando código…")}</p>
        </CodeFrame>
      ) : loaded === "error" ? (
        <CodeFrame className={className}>
          <p className="px-3 text-muted-foreground">{t("Este archivo ya no está en las fuentes: puede que se haya borrado.")}</p>
        </CodeFrame>
      ) : (
        <CodeTable
          className={className}
          start={loaded.start}
          lines={loaded.lines}
          colored={colored}
          marked={[startLine, endLine]}
        />
      )}
    </div>
  )
}

/** Colored tokens for these lines in the chosen theme; null until ready or without a grammar. */
function useHighlighted(lines: string[] | null, path: string): Highlighted | null {
  const [colored, setColored] = useState<Highlighted | null>(null)
  const choice = useCodeTheme()
  const dark = useDarkMode()
  const theme = themeName(choice, dark)
  useEffect(() => {
    if (lines === null) return
    const live = { current: true }
    highlight(lines.join("\n"), path, theme)
      .then((result) => live.current && setColored(result))
      .catch(() => live.current && setColored(null))
    return () => {
      live.current = false
    }
  }, [lines, path, theme])
  return colored
}

function CodeFrame({ children, className, colored }: { children: ReactNode; className?: string; colored?: Highlighted | null }) {
  const style = colored ? { background: colored.background, color: colored.foreground } : undefined
  return (
    <div
      className={cn("overflow-x-auto rounded-lg border bg-muted/50 py-2 font-mono text-xs leading-relaxed", className)}
      style={style}
    >
      {children}
    </div>
  )
}

function CodeTable({
  start,
  lines,
  colored,
  marked,
  className,
}: {
  start: number
  lines: string[]
  colored: Highlighted | null
  marked?: [number, number]
  className?: string
}) {
  return (
    <CodeFrame className={className} colored={colored}>
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, index) => {
            const number = start + index
            const cited = marked !== undefined && number >= marked[0] && number <= marked[1]
            return (
              <tr
                key={number}
                className={cn(cited && "bg-primary/15 shadow-[inset_3px_0_0_var(--color-primary)]")}
                aria-current={cited ? "true" : undefined}
              >
                <td className="w-12 pr-3 pl-3 text-right align-top opacity-50 select-none">{number}</td>
                <td className="pr-3 whitespace-pre">
                  {colored?.lines[index]?.length
                    ? colored.lines[index].map((token, position) => (
                        <span key={position} style={{ color: token.color }}>
                          {token.content}
                        </span>
                      ))
                    : line || " "}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </CodeFrame>
  )
}

const SAMPLE = [
  "// Ejemplo",
  "export function discount(total: number, vip: boolean): number {",
  "  if (vip && total >= 100) return total * 0.1",
  "  return 0",
  "}",
]

/** A short sample in the chosen theme, for Settings. */
export function CodeThemePreview() {
  const colored = useHighlighted(SAMPLE, "ejemplo.ts")
  return <CodeTable start={1} lines={SAMPLE} colored={colored} marked={[3, 3]} />
}
