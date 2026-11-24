import { Dialog as DialogPrimitive } from "@base-ui/react/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { ArrowLeft, ExternalLink, X } from "lucide-react"
import { type ReactNode, useEffect, useState } from "react"
import { cn } from "cn"
import { CodeExcerpt } from "@/components/code-excerpt"
import { RuleLink } from "@/components/rule-link"
import { getRule, type ImpactedEntity, type RuleCategory, type RuleDetail, type RuleImpact } from "@/lib/api"
import { t } from "@/lib/i18n"
import { CATEGORY_LABELS, CRITICALITY_LABELS, VERIFICATION_LABELS } from "@/lib/labels"
import { where } from "@/lib/relations"

type Dependent = RuleImpact["dependents"][number]

/** Rules that share a name go together: the name once, its places as chips. */
function byName<T extends ImpactedEntity>(items: T[]): { name: string; items: T[] }[] {
  const groups = new Map<string, T[]>()
  for (const item of items) groups.set(item.name, [...(groups.get(item.name) ?? []), item])
  return [...groups].map(([name, grouped]) => ({ name, items: grouped }))
}

/** Dependents by why they may break: they call this rule, its copy, or another rule that does. */
function byReason(dependents: Dependent[], copies: Map<string, ImpactedEntity>): { label: string; items: Dependent[] }[] {
  const groups = new Map<string, Dependent[]>()
  for (const dependent of dependents) {
    const copy = dependent.via ? copies.get(dependent.via.id) : undefined
    const label =
      dependent.via === null
        ? t("Llaman a esta regla")
        : copy
          ? t("Llaman a su copia en {0}", where(copy.citation) ?? copy.name)
          : t("Llaman a «{0}», que la usa", dependent.via.name)
    groups.set(label, [...(groups.get(label) ?? []), dependent])
  }
  return [...groups].map(([label, items]) => ({ label, items }))
}

const isHigh = (item: ImpactedEntity) => item.criticality === "high" || item.criticality === "critical"

/** One row per name: the name, and where it lives; several places become chips. */
function Rows<T extends ImpactedEntity>({ items, onOpen }: { items: T[]; onOpen: (id: string) => void }) {
  return (
    <ul className="divide-y overflow-hidden rounded-lg border">
      {byName(items).map((group) => (
        <li key={group.name} className={cn("flex flex-wrap items-center gap-x-3 gap-y-1.5", group.items.length > 1 && "px-3 py-2")}>
          {group.items.length === 1 ? (
            // The whole row opens the rule: it is not a disclosure, so no chevron.
            <button
              type="button"
              onClick={() => onOpen(group.items[0].id)}
              title={t("Ver la regla sin salir de aquí")}
              className="flex min-w-0 flex-1 items-center gap-3 px-3 py-2 text-left outline-none hover:bg-muted/60 focus-visible:bg-muted/60"
            >
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{group.name}</span>
              {isHigh(group.items[0]) && <HighDot />}
              <span className="shrink-0 font-mono text-xs text-muted-foreground">{where(group.items[0].citation)}</span>
            </button>
          ) : (
            <>
              <span className="min-w-0 flex-1 truncate text-sm font-medium">{group.name}</span>
              {group.items.some(isHigh) && <HighDot />}
              <span className="flex w-full flex-wrap gap-1.5">
                {group.items.map((item) => (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => onOpen(item.id)}
                    title={t("Ver la regla sin salir de aquí")}
                    className="rounded-md border bg-muted/40 px-2 py-0.5 font-mono text-xs text-muted-foreground outline-none hover:border-foreground/30 hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
                  >
                    {where(item.citation) ?? item.name}
                  </button>
                ))}
              </span>
            </>
          )}
        </li>
      ))}
    </ul>
  )
}

function HighDot() {
  return (
    <span className="flex shrink-0 items-center gap-1 text-xs text-orange-600 dark:text-orange-400" title={t("Importancia alta")}>
      <span className="size-1.5 rounded-full bg-current" aria-hidden="true" />
      {t("alta")}
    </span>
  )
}

function Block({
  title,
  count,
  hint,
  children,
}: {
  title: string
  count: string
  hint: string
  children: ReactNode
}) {
  return (
    <div className="grid gap-2">
      <div className="grid gap-0.5">
        <p className="flex items-center gap-2 text-sm font-medium">
          {title}
          <span className="rounded-full bg-muted px-1.5 text-xs font-normal text-muted-foreground tabular-nums">{count}</span>
        </p>
        <p className="text-xs text-muted-foreground">{hint}</p>
      </div>
      <div className="grid gap-3">{children}</div>
    </div>
  )
}

/**
 * N7 in words: what has to change with this rule, and what may stop working, each with where it
 * lives, grouped by why. Opening one calls `onOpen`, so the screen can show it without leaving.
 */
export function ImpactList({
  impact,
  onOpen,
  limit = 8,
}: {
  impact: RuleImpact
  onOpen: (id: string) => void
  limit?: number
}) {
  const [all, setAll] = useState(false)
  const copies = new Map(impact.same_decision.map((copy) => [copy.id, copy]))
  const shown = all ? impact.dependents : impact.dependents.slice(0, limit)
  if (impact.same_decision.length === 0 && impact.dependents.length === 0) {
    return <p className="text-sm text-muted-foreground">{t("Según el código, ninguna otra regla depende de esta: puedes cambiarla sin tocar otras.")}</p>
  }
  return (
    <div className="grid gap-5">
      {impact.same_decision.length > 0 && (
        <Block
          title={t("Cambia con ella")}
          count={String(impact.same_decision.length)}
          hint={t("La misma decisión, escrita en otro sitio. Cámbialas juntas.")}
        >
          <Rows items={impact.same_decision} onOpen={onOpen} />
        </Block>
      )}
      {impact.dependents.length > 0 && (
        <Block
          title={t("Puede dejar de funcionar")}
          count={`${impact.dependents.length}${impact.truncated ? "+" : ""}`}
          hint={t("Su código usa esta regla. Revísalas si la cambias.")}
        >
          {byReason(shown, copies).map((group) => (
            <div key={group.label} className="grid gap-1.5">
              <p className="text-xs font-medium text-muted-foreground">{group.label}</p>
              <Rows items={group.items} onOpen={onOpen} />
            </div>
          ))}
          {impact.dependents.length > limit && (
            <button type="button" className="w-fit text-xs text-muted-foreground hover:text-foreground hover:underline" onClick={() => setAll(!all)}>
              {all ? t("Mostrar menos") : t("Mostrar {0} más", impact.dependents.length - limit)}
            </button>
          )}
          {impact.truncated && (
            <p className="text-xs text-muted-foreground">{t("Hay más; se muestran las más cercanas (hasta {0} pasos).", impact.max_depth)}</p>
          )}
        </Block>
      )}
    </div>
  )
}

/**
 * A rule shown from the side, without leaving the screen: what it says, its code and what it
 * affects. Opening another rule from inside keeps a trail back.
 */
export function RuleDrawer({
  projectId,
  entityId,
  onClose,
}: {
  projectId: string
  entityId: string | null
  onClose: () => void
}) {
  const [trail, setTrail] = useState<{ start: string | null; ids: string[] }>({ start: entityId, ids: [] })
  // A new rule opened from the screen starts a new trail.
  const ids = trail.start === entityId ? trail.ids : []
  const current = ids.at(-1) ?? entityId
  const go = (next: string[]) => setTrail({ start: entityId, ids: next })
  const [loaded, setLoaded] = useState<{ id: string; rule: RuleDetail | "error" } | null>(null)
  useEffect(() => {
    if (current === null) return
    const live = { current: true }
    getRule(projectId, current)
      .then((found) => live.current && setLoaded({ id: current, rule: found }))
      .catch(() => live.current && setLoaded({ id: current, rule: "error" }))
    return () => {
      live.current = false
    }
  }, [projectId, current])
  const failed = loaded?.id === current && loaded.rule === "error"
  const rule = loaded?.id === current && loaded.rule !== "error" ? loaded.rule : null
  const version = rule?.versions[0]
  const citation = version?.evidence.find((evidence) => evidence.locator.start_line !== undefined && evidence.revision_id)
  const back = ids.length > 0
  return (
    <DialogPrimitive.Root open={entityId !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogPrimitive.Portal>
        <DialogPrimitive.Backdrop className="fixed inset-0 z-50 bg-black/30" />
        <DialogPrimitive.Popup
          className="fixed inset-y-0 right-0 z-50 grid w-[min(40rem,100vw)] grid-cols-[minmax(0,1fr)] grid-rows-[auto_minmax(0,1fr)_auto] border-l bg-popover text-popover-foreground shadow-xl outline-none"
        >
          <header className="grid gap-3 border-b px-5 pt-4 pb-4">
            <div className="flex items-center justify-between gap-3">
              {back ? (
                <button
                  type="button"
                  className="flex w-fit items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
                  onClick={() => go(ids.slice(0, -1))}
                >
                  <ArrowLeft className="size-3.5" aria-hidden="true" />
                  {t("Volver a la anterior")}
                </button>
              ) : (
                <span className="text-xs text-muted-foreground">{t("Regla")}</span>
              )}
              <DialogPrimitive.Close
                aria-label={t("Cerrar")}
                className="grid size-7 shrink-0 place-items-center rounded-md text-muted-foreground outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <X className="size-4" />
              </DialogPrimitive.Close>
            </div>
            <DialogPrimitive.Title className="font-heading text-lg leading-snug font-medium">
              {rule?.name ?? <Skeleton className="h-6 w-2/3" />}
            </DialogPrimitive.Title>
            {rule && (
              <DialogPrimitive.Description render={<div />} className="flex flex-wrap gap-1.5">
                <Pill>{CATEGORY_LABELS[rule.rule_category as RuleCategory] ?? rule.rule_category}</Pill>
                <Pill strong={rule.criticality === "high" || rule.criticality === "critical"}>
                  {t("importancia {0}", CRITICALITY_LABELS[rule.criticality].toLowerCase())}
                </Pill>
                {version && <Pill>{VERIFICATION_LABELS[version.verification_status]}</Pill>}
              </DialogPrimitive.Description>
            )}
          </header>
          <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-7 overflow-x-hidden overflow-y-auto px-5 py-5">
            {failed && <p className="text-sm text-destructive">{t("No se pudo cargar la regla.")}</p>}
            {rule && (
              <>
                {version?.content && (
                  <Part title={t("Qué dice")}>
                    <p className="text-[15px] leading-relaxed">{version.content.statement}</p>
                  </Part>
                )}
                {citation?.revision_id && citation.locator.path && citation.locator.start_line !== undefined && (
                  <Part title={t("Dónde está")} aside={<span className="truncate font-mono">{where({ path: citation.locator.path, start_line: citation.locator.start_line, end_line: citation.locator.start_line, revision_id: citation.revision_id })}</span>}>
                    <CodeExcerpt
                      revisionId={citation.revision_id}
                      path={citation.locator.path}
                      startLine={citation.locator.start_line}
                      endLine={citation.locator.end_line ?? citation.locator.start_line}
                    />
                  </Part>
                )}
                <Part title={t("Si cambias esta regla")}>
                  <ImpactList impact={rule.impact} onOpen={(id) => go([...ids, id])} />
                </Part>
              </>
            )}
          </div>
          <footer className={cn("flex justify-end border-t px-5 py-3", !rule && "invisible")}>
            {current && (
              <RuleLink
                id={current}
                className="flex w-fit items-center gap-1.5 rounded-md border px-3 py-1.5 text-sm hover:bg-muted"
              >
                <ExternalLink className="size-3.5" aria-hidden="true" />
                {t("Abrir la ficha completa en Reglas")}
              </RuleLink>
            )}
          </footer>
        </DialogPrimitive.Popup>
      </DialogPrimitive.Portal>
    </DialogPrimitive.Root>
  )
}

function Pill({ children, strong = false }: { children: ReactNode; strong?: boolean }) {
  return (
    <span
      className={cn(
        "rounded-full border px-2 py-0.5 text-xs",
        strong ? "border-orange-500/30 bg-orange-500/10 text-orange-600 dark:text-orange-400" : "text-muted-foreground",
      )}
    >
      {children}
    </span>
  )
}

/** A part of the drawer: a small title, then its content. */
function Part({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-2.5">
      <div className="flex min-w-0 items-baseline justify-between gap-3 text-xs">
        <h3 className="font-medium tracking-wide text-muted-foreground uppercase">{title}</h3>
        {aside && <span className="min-w-0 text-muted-foreground">{aside}</span>}
      </div>
      {children}
    </section>
  )
}
