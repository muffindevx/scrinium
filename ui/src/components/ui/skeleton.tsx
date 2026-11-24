import { cn } from "cn"

/** A grey block that pulses where content is on its way. Never a "Cargando…" text. */
function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden="true" className={cn("animate-pulse rounded-md bg-foreground/[0.07]", className)} />
}

export type SkeletonShape = "list" | "cards" | "text" | "line"

/**
 * The outline of what is coming: rows of a list, a row of cards, a few lines of text or one
 * line. Screen readers hear "Cargando" once, from the label; nothing is shown as text.
 */
function Placeholder({ shape = "list", rows = 5, label }: { shape?: SkeletonShape; rows?: number; label: string }) {
  return (
    <div role="status" aria-label={label} aria-busy="true" className="grid gap-3">
      {shape === "line" && <Skeleton className="h-4 w-40" />}
      {shape === "text" && (
        <div className="grid gap-2">
          {Array.from({ length: rows }, (_, index) => (
            <Skeleton key={index} className={cn("h-3.5", index === rows - 1 ? "w-2/3" : "w-full")} />
          ))}
        </div>
      )}
      {shape === "cards" && (
        <div className="grid gap-3 md:grid-cols-3">
          {Array.from({ length: Math.min(rows, 3) }, (_, index) => (
            <div key={index} className="grid gap-3 rounded-xl bg-card p-4 ring-1 ring-border/70">
              <Skeleton className="size-7" />
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="h-3 w-full" />
              <Skeleton className="h-3 w-3/4" />
            </div>
          ))}
        </div>
      )}
      {shape === "list" && (
        <div className="grid divide-y overflow-hidden rounded-xl bg-card ring-1 ring-border/70">
          {Array.from({ length: rows }, (_, index) => (
            <div key={index} className="flex items-center gap-3 px-4 py-3">
              <Skeleton className="size-7 shrink-0" />
              <div className="grid flex-1 gap-1.5">
                <Skeleton className="h-3.5 w-2/5" />
                <Skeleton className="h-3 w-3/5" />
              </div>
              <Skeleton className="h-3 w-14" />
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export { Placeholder, Skeleton }
