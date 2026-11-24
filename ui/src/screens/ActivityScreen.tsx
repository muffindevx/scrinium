import {
  Activity,
  BookCheck,
  BookX,
  Bug,
  FileSearch,
  GitCompareArrows,
  type LucideIcon,
  MessageSquare,
  RefreshCw,
  Sparkles,
  Tag,
} from "lucide-react"
import { useEffect, useState } from "react"
import { Link } from "react-router"
import { cn } from "cn"
import { EmptyState, PageHeader } from "@/components/page"
import { Placeholder } from "@/components/ui/skeleton"
import { type ActivityEvent, type Project, getActivity } from "@/lib/api"
import { language, t } from "@/lib/i18n"
import { relativeTime } from "@/lib/time"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Loading, type ScreenProps } from "@/screens/shared"

interface Shown {
  icon: LucideIcon
  text: string
  to: string | null
  failed: boolean
}

const FAILED = new Set(["failed", "failure", "denied"])

/** One event as a sentence a person reads, with where to look at it. */
export function describe(event: ActivityEvent): Shown {
  const failed = FAILED.has(event.status)
  const running = event.status === "queued" || event.status === "running"
  const state = failed ? t(" (falló)") : running ? t(" (en curso)") : ""
  switch (event.kind) {
    case "analysis.knowledge_extraction":
      return { icon: Sparkles, text: t("Extracción de reglas") + state, to: "/reglas", failed }
    case "analysis.code_diff":
      return { icon: GitCompareArrows, text: t("Impacto de un cambio") + state, to: `/impacto/${event.ref}`, failed }
    case "analysis.bug_investigation":
      return { icon: Bug, text: t("Triaje de un bug") + state, to: `/impacto/${event.ref}`, failed }
    case "analysis.release_comparison":
      return { icon: FileSearch, text: t("Comparación de versiones") + state, to: `/versiones/${event.ref}`, failed }
    case "analysis.question":
      return { icon: MessageSquare, text: t("Pregunta") + state, to: null, failed }
    case "sync": {
      const counts = event.counts
      const changed = counts ? t(": {0} nuevos, {1} cambiados, {2} borrados", counts.created, counts.updated, counts.deleted) : ""
      return { icon: RefreshCw, text: (failed ? t("Sincronización fallida") : t("Sincronización")) + changed, to: "/integraciones", failed }
    }
    case "knowledge_version.verify":
      return event.to === "rejected"
        ? { icon: BookX, text: t("Regla rechazada"), to: `/reglas/${event.ref}`, failed: false }
        : event.to === "human_verified"
          ? { icon: BookCheck, text: t("Regla verificada"), to: `/reglas/${event.ref}`, failed: false }
          : { icon: BookCheck, text: t("Regla devuelta a revisión"), to: `/reglas/${event.ref}`, failed: false }
    case "ticket_link.confirm":
      return { icon: Tag, text: t("Ticket {0} enlazado", event.ticket ?? ""), to: `/reglas/${event.ref}`, failed: false }
    case "ticket_link.dismiss":
      return { icon: Tag, text: t("Ticket propuesto descartado"), to: `/reglas/${event.ref}`, failed: false }
    case "ticket_link.remove":
      return { icon: Tag, text: t("Ticket desenlazado"), to: `/reglas/${event.ref}`, failed: false }
    default:
      return { icon: Activity, text: event.kind + state, to: null, failed }
  }
}

function day(iso: string): string {
  return new Date(iso).toLocaleDateString(language(), { weekday: "long", day: "numeric", month: "long" })
}

/** What happened in the project, newest first: analyses, syncs, reviews and ticket links. */
export function ActivityScreen({ project, onSessionLost }: ScreenProps) {
  const [events, setEvents] = useState<ActivityEvent[] | null>(null)
  const { error, fail, failure, attempt, retry } = useErrors(onSessionLost)
  useEffect(() => {
    getActivity(project.id, 100).then(setEvents).catch(fail)
  }, [project.id, fail, attempt])

  const days: { day: string; events: ActivityEvent[] }[] = []
  for (const event of events ?? []) {
    const label = event.at ? day(event.at) : t("Sin fecha")
    const last = days.at(-1)
    if (last?.day === label) last.events.push(event)
    else days.push({ day: label, events: [event] })
  }

  return (
    <>
      <PageHeader
        title={t("Actividad")}
        description={t("Lo que ha pasado en el proyecto: análisis, sincronizaciones, reglas revisadas y tickets enlazados.")}
      />
      <ErrorText>{events !== null && error}</ErrorText>
      {events === null ? (
        <Loading failure={failure} onRetry={retry} />
      ) : events.length === 0 ? (
        <EmptyState icon={Activity} title={t("Todavía no hay actividad")}>
          {t("Aparecerá aquí en cuanto sincronices una fuente, extraigas reglas o revises alguna.")}
        </EmptyState>
      ) : (
        days.map((group) => (
          <section key={group.day} className="grid gap-2">
            <h2 className="text-[0.65rem] font-semibold tracking-[0.14em] text-muted-foreground uppercase">{group.day}</h2>
            <ol className="grid rounded-xl bg-card ring-1 ring-border/70">
              {group.events.map((event, index) => {
                const shown = describe(event)
                const Icon = shown.icon
                const body = (
                  <>
                    <span
                      className={cn(
                        "grid size-7 shrink-0 place-items-center rounded-md",
                        shown.failed ? "bg-destructive/10 text-destructive" : "bg-muted text-foreground/80",
                      )}
                    >
                      <Icon className="size-4" aria-hidden="true" />
                    </span>
                    <span className="grid min-w-0 flex-1 gap-0.5">
                      <span className="text-sm">{shown.text}</span>
                      {event.subject && <span className="truncate text-xs text-muted-foreground">{event.subject}</span>}
                    </span>
                    {event.at && (
                      <time dateTime={event.at} className="shrink-0 text-xs text-muted-foreground">
                        {relativeTime(event.at)}
                      </time>
                    )}
                  </>
                )
                return (
                  <li key={`${event.kind}-${event.ref}-${index}`} className="border-b last:border-b-0">
                    {shown.to ? (
                      <Link to={shown.to} className="flex items-center gap-3 px-4 py-3 hover:bg-muted/50">
                        {body}
                      </Link>
                    ) : (
                      <div className="flex items-center gap-3 px-4 py-3">{body}</div>
                    )}
                  </li>
                )
              })}
            </ol>
          </section>
        ))
      )}
    </>
  )
}

/** Home's card: the last three things that happened, each a way to where it happened. */
export function RecentActivity({ project }: { project: Project }) {
  const [events, setEvents] = useState<ActivityEvent[] | null>(null)
  useEffect(() => {
    getActivity(project.id, 3)
      .then(setEvents)
      .catch(() => setEvents([]))
  }, [project.id])
  return (
    <section className="flex min-w-0 flex-col gap-4 rounded-xl bg-card p-4 text-card-foreground shadow-[0_1px_2px_oklch(0_0_0/0.04)] ring-1 ring-border/70">
      <div className="grid gap-3">
        <div className="flex items-center gap-2.5">
          <span className="grid size-7 place-items-center rounded-md bg-accent text-primary">
            <Activity className="size-4" aria-hidden="true" />
          </span>
          <h3 className="text-sm font-semibold">{t("Última actividad")}</h3>
        </div>
        {events === null ? (
          <Placeholder shape="text" rows={3} label={t("Cargando")} />
        ) : events.length === 0 ? (
          <p className="text-xs text-muted-foreground">{t("Todavía no hay actividad")}</p>
        ) : (
          <ol className="grid gap-2.5">
            {events.map((event, index) => {
              const shown = describe(event)
              const Icon = shown.icon
              const body = (
                <>
                  <Icon className={cn("mt-0.5 size-3.5 shrink-0", shown.failed ? "text-destructive" : "text-muted-foreground")} aria-hidden="true" />
                  <span className="grid min-w-0 flex-1 gap-0.5">
                    <span className="truncate text-xs">{shown.text}</span>
                    <span className="truncate text-[0.7rem] text-muted-foreground">
                      {event.at ? relativeTime(event.at) : ""}
                      {event.subject ? ` · ${event.subject}` : ""}
                    </span>
                  </span>
                </>
              )
              return (
                <li key={`${event.kind}-${event.ref}-${index}`}>
                  {shown.to ? (
                    <Link to={shown.to} className="flex items-start gap-2 hover:underline">
                      {body}
                    </Link>
                  ) : (
                    <div className="flex items-start gap-2">{body}</div>
                  )}
                </li>
              )
            })}
          </ol>
        )}
      </div>
    </section>
  )
}
