import type { LucideIcon } from "lucide-react"
import type { ReactNode } from "react"
import { cn } from "cn"

/** Header of each screen: title, short explanation and main actions. */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-3">
      <div className="grid max-w-2xl gap-1">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  )
}

/** A content block with a soft border. Replaces the stacked cards. */
export function Section({
  title,
  description,
  action,
  children,
  className,
}: {
  title?: ReactNode
  description?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={cn("grid content-start gap-4 rounded-xl border bg-card p-4 text-card-foreground", className)}>
      {(title || action) && (
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="grid gap-0.5">
            {title && <h2 className="font-heading text-base font-medium">{title}</h2>}
            {description && <p className="text-sm text-muted-foreground">{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  )
}

export function EmptyState({
  icon: Icon,
  title,
  children,
  action,
}: {
  icon: LucideIcon
  title: string
  children?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="grid justify-items-center gap-2 rounded-lg border border-dashed px-6 py-10 text-center">
      <span className="grid size-10 place-items-center rounded-full bg-muted">
        <Icon className="size-5 text-muted-foreground" aria-hidden="true" />
      </span>
      <p className="font-medium">{title}</p>
      {children && <p className="max-w-md text-sm text-muted-foreground">{children}</p>}
      {action}
    </div>
  )
}

/** Label-value row for status data. */
export function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 py-2 text-sm">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="text-right font-medium">{children}</dd>
    </div>
  )
}
