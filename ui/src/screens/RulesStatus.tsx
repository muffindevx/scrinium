import { CheckCircle2, ChevronRight, Clock, FileWarning, GitCompareArrows, type LucideIcon, TriangleAlert } from "lucide-react"
import type { ReactNode } from "react"
import { Link } from "react-router"
import { cn } from "cn"
import { Placeholder } from "@/components/ui/skeleton"
import type { Inbox, RulebookHealth } from "@/lib/api"
import { t } from "@/lib/i18n"
import { relativeTime } from "@/lib/time"

/**
 * Home's rulebook block, built to answer two questions instead of listing totals: how far can
 * the rulebook be trusted (verified out of all, and the week's pace), and what to do now (the
 * few things that need a person, most urgent first, each a way into the inbox).
 */
export function RulesStatus({ rules, inbox }: { rules: RulebookHealth | null; inbox: Inbox | null }) {
  if (rules === null) {
    return (
      <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <Placeholder shape="text" rows={4} label={t("Cargando")} />
        <Placeholder shape="text" rows={4} label={t("Cargando")} />
      </div>
    )
  }
  return (
    <div className="grid gap-3 md:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
      <Progress rules={rules} />
      <Attention rules={rules} inbox={inbox} />
    </div>
  )
}

function Card({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="grid content-start gap-3 rounded-xl bg-card p-5 ring-1 ring-border/70">
      <h2 className="text-xs font-medium text-muted-foreground">{title}</h2>
      {children}
    </section>
  )
}

function percent(part: number, whole: number): number {
  if (whole === 0) return 0
  const exact = (part / whole) * 100
  // A share above zero never shows as 0 %, and one below the whole never as 100 %.
  return part > 0 && exact < 1 ? 1 : part < whole && exact > 99 ? 99 : Math.round(exact)
}

/** Verified out of all, as one bar: a part of a whole, not three separate numbers. */
function Progress({ rules }: { rules: RulebookHealth }) {
  const width = (part: number) => `${rules.rules ? (part / rules.rules) * 100 : 0}%`
  const parts = [
    { value: rules.verified, label: t("verificadas"), swatch: "bg-primary", to: "/reglas?filtro=verified" },
    { value: rules.rejected, label: t("rechazadas"), swatch: "bg-muted-foreground/60", to: "/reglas?filtro=rejected" },
    { value: rules.unreviewed, label: t("sin revisar"), swatch: "bg-foreground/10", to: "/reglas?filtro=unverified" },
  ]
  return (
    <Card title={t("Libro de reglas verificado")}>
      <p className="flex flex-wrap items-baseline gap-x-2 leading-none">
        <span className="text-3xl font-semibold tracking-tight">{rules.verified}</span>
        <span className="text-sm text-muted-foreground">
          {t("de {0} · {1} %", rules.rules, percent(rules.verified, rules.rules))}
        </span>
      </p>
      <div
        role="img"
        aria-label={t("{0} verificadas, {1} rechazadas y {2} sin revisar", rules.verified, rules.rejected, rules.unreviewed)}
        className="flex h-2 gap-0.5 overflow-hidden rounded-full bg-foreground/10"
      >
        {rules.verified > 0 && <div className="h-full min-w-1 bg-primary" style={{ width: width(rules.verified) }} />}
        {rules.rejected > 0 && <div className="h-full min-w-1 bg-muted-foreground/60" style={{ width: width(rules.rejected) }} />}
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {parts.map((part) => (
          <li key={part.label}>
            <Link to={part.to} className="inline-flex items-center gap-1.5 hover:text-foreground hover:underline">
              <span className={cn("size-2 rounded-[2px]", part.swatch)} aria-hidden="true" />
              {part.value} {part.label}
            </Link>
          </li>
        ))}
      </ul>
      <p className="border-t pt-3 text-xs text-muted-foreground">
        {t(
          "Esta semana: {0} revisadas · {1} propuestas nuevas",
          rules.reviewed_this_week ?? 0,
          rules.new_this_week ?? 0,
        )}
      </p>
    </Card>
  )
}

interface Need {
  key: string
  icon: LucideIcon
  tone: "warning" | "plain"
  text: ReactNode
  hint?: string
  to: string
}

/** What needs a person now, most urgent first; a row only when it has something. */
function Attention({ rules, inbox }: { rules: RulebookHealth; inbox: Inbox | null }) {
  const needs: Need[] = []
  if (rules.drifted > 0)
    needs.push({
      key: "drift",
      icon: GitCompareArrows,
      tone: "warning",
      text: rules.drifted === 1 ? t("1 regla cambió en el código") : t("{0} reglas cambiaron en el código", rules.drifted),
      hint: t("Confirma si la regla sigue siendo cierta"),
      to: "/bandeja?pestana=attention",
    })
  const high = inbox?.buckets?.high ?? 0
  if (high > 0)
    needs.push({
      key: "high",
      icon: TriangleAlert,
      tone: "warning",
      text: high === 1 ? t("1 de importancia alta espera revisión") : t("{0} de importancia alta esperan revisión", high),
      hint: t("Dinero, permisos y seguridad primero"),
      to: "/bandeja?pestana=high",
    })
  const unanchored = inbox?.unanchored_total ?? 0
  if (unanchored > 0)
    needs.push({
      key: "unanchored",
      icon: FileWarning,
      tone: "plain",
      text:
        unanchored === 1
          ? t("1 propuesta cita un dato que el código no tiene")
          : t("{0} propuestas citan un dato que el código no tiene", unanchored),
      hint: t("Fuera de la bandeja hasta corregirlas"),
      to: "/reglas?filtro=unverified",
    })
  if (rules.oldest_unreviewed_at)
    needs.push({
      key: "oldest",
      icon: Clock,
      tone: "plain",
      text: t("La propuesta más antigua sin revisar es de {0}", relativeTime(rules.oldest_unreviewed_at)),
      to: "/bandeja",
    })

  return (
    <Card title={t("Qué necesita tu atención")}>
      {needs.length === 0 ? (
        <p className="flex items-center gap-2 text-sm">
          <CheckCircle2 className="size-4 text-primary" aria-hidden="true" />
          {t("Todo al día: no hay nada esperando revisión.")}
        </p>
      ) : (
        <ul className="-mx-2 grid">
          {needs.map((need) => {
            const Icon = need.icon
            return (
              <li key={need.key} className="border-b last:border-b-0">
                <Link to={need.to} className="flex items-center gap-3 rounded-md px-2 py-2.5 hover:bg-muted/50">
                  <span
                    className={cn(
                      "grid size-7 shrink-0 place-items-center rounded-md",
                      need.tone === "warning"
                        ? "bg-amber-500/15 text-amber-700 dark:text-amber-400"
                        : "bg-muted text-muted-foreground",
                    )}
                  >
                    <Icon className="size-4" aria-hidden="true" />
                  </span>
                  <span className="grid min-w-0 flex-1 gap-0.5">
                    <span className="text-sm">{need.text}</span>
                    {need.hint && <span className="text-xs text-muted-foreground">{need.hint}</span>}
                  </span>
                  <ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                </Link>
              </li>
            )
          })}
        </ul>
      )}
    </Card>
  )
}
