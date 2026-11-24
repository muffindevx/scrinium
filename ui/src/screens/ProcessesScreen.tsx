import { ArrowRight, Bug, ChevronDown, ChevronRight, Clock, Globe, ListOrdered, MousePointerClick, Search, Workflow, Zap } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { cn } from "cn"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { RuleLink } from "@/components/rule-link"
import { StatusBadge } from "@/components/status"
import { Input } from "@/components/ui/input"
import { type Process, getProcesses } from "@/lib/api"
import { t } from "@/lib/i18n"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Loading, Muted, type ScreenProps } from "@/screens/shared"

const TRIGGERS: Record<Process["trigger"]["kind"], { label: string; icon: typeof Globe }> = {
  http: { label: t("Petición HTTP"), icon: Globe },
  action: { label: t("Acción de un usuario"), icon: MousePointerClick },
  schedule: { label: t("Tarea programada"), icon: Clock },
  handler: { label: t("Evento"), icon: Zap },
}

type Step = { id: string; title: string; statement: string; verified: boolean; where: string }

/** The process's rules once each, in the order a run meets them, with where each is applied. */
function rulesOf(process: Process): Step[] {
  const seen = new Set<string>()
  const steps: Step[] = []
  for (const step of process.steps) {
    for (const rule of step.rules) {
      if (seen.has(rule.id)) continue
      seen.add(rule.id)
      const name = step.function.split(".").pop() ?? step.function
      steps.push({ ...rule, where: `${name} · ${step.path}:${rule.start_line ?? step.start_line}` })
    }
  }
  return steps
}

/**
 * Processes (phase 8.3): what the business rules do when someone uses the system. Each process
 * starts at what triggers it (an HTTP request, a button, a scheduled job) and lists, in order,
 * the rules a run goes through. Found in the code by following its calls, without AI.
 */
export function ProcessesScreen({ project, onSessionLost }: ScreenProps) {
  const [found, setFound] = useState<Process[] | null>(null)
  const [query, setQuery] = useState("")
  const [open, setOpen] = useState<string | null>(null)
  const { error, fail, failure, attempt, retry } = useErrors(onSessionLost)
  useEffect(() => {
    getProcesses(project.id).then(setFound).catch(fail)
  }, [project.id, fail, attempt])
  const shown = useMemo(() => {
    const text = query.trim().toLowerCase()
    if (!found || !text) return found ?? []
    return found.filter(
      (process) =>
        process.name.toLowerCase().includes(text) ||
        process.steps.some((step) => step.rules.some((rule) => rule.title.toLowerCase().includes(text))),
    )
  }, [found, query])

  return (
    <>
      <PageHeader
        title={t("Procesos")}
        description={t("Qué reglas de negocio se aplican, y en qué orden, cuando alguien usa el sistema.")}
      />
      <Explainer />
      <ErrorText>{found !== null && error}</ErrorText>
      {found === null ? (
        <Loading failure={failure} onRetry={retry} />
      ) : found.length === 0 ? (
        <EmptyState icon={Workflow} title={t("Todavía no hay procesos")}>
          {t("Un proceso aparece cuando algo que dispara el sistema (una ruta HTTP, una acción, una tarea programada) llama a código con reglas. Extrae reglas primero; si tu proyecto usa otra forma de declarar sus entradas, aquí no se verá todavía.")}
        </EmptyState>
      ) : (
        <>
          {found.length > 6 && (
            <div className="relative max-w-sm">
              <Search className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" aria-hidden="true" />
              <Input
                className="pl-8"
                aria-label={t("Buscar un proceso o una regla")}
                placeholder={t("Buscar un proceso o una regla")}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
          )}
          <Section className="gap-0 p-0">
            <ul className="divide-y" aria-label={t("Procesos")}>
              {shown.map((process) => {
                const key = `${process.path}:${process.start_line}:${process.name}`
                return (
                  <ProcessRow
                    key={key}
                    process={process}
                    open={open === key}
                    onToggle={() => setOpen(open === key ? null : key)}
                  />
                )
              })}
            </ul>
          </Section>
          {shown.length === 0 && <Muted>{t("Ningún proceso coincide.")}</Muted>}
        </>
      )}
    </>
  )
}

const EXPLAINER_KEY = "scrinium.processesExplainer"

function explainerOpen(): boolean {
  try {
    // Open the first time, so the screen explains itself once; then as the person left it.
    return window.localStorage.getItem(EXPLAINER_KEY) !== "closed"
  } catch {
    return true
  }
}

/** What a process is, drawn as the three things the screen shows, and what it is for. Folds
 * away once read, so the list, the reason to come back, sits at the top. */
function Explainer() {
  const steps = [
    {
      number: 1,
      icon: Globe,
      title: t("Algo lo pone en marcha"),
      text: t("Una petición a la API, un botón que alguien pulsa o una tarea que corre sola."),
    },
    {
      number: 2,
      icon: Workflow,
      title: t("El código hace su recorrido"),
      text: t("Scrinium sigue las llamadas entre funciones, en el orden en que ocurren."),
    },
    {
      number: 3,
      icon: ListOrdered,
      title: t("Por el camino aplica reglas"),
      text: t("Las reglas de negocio que se cumplen en ese recorrido, una tras otra."),
    },
  ]
  return (
    <details
      open={explainerOpen()}
      onToggle={(event) => {
        try {
          window.localStorage.setItem(EXPLAINER_KEY, event.currentTarget.open ? "open" : "closed")
        } catch {
          // Without storage it opens again next time.
        }
      }}
      className="group rounded-xl border bg-card"
    >
      <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-3 text-sm font-medium">
        <ChevronRight className="size-4 text-muted-foreground transition-transform group-open:rotate-90" aria-hidden="true" />
        {t("Qué es un proceso y para qué sirve")}
      </summary>
    <section className="grid gap-4 border-t p-4" aria-label={t("Qué es un proceso")}>
      <ol className="grid gap-3 md:grid-cols-[1fr_auto_1fr_auto_1fr] md:items-stretch">
        {steps.map(({ number, icon: Icon, title, text }, index) => (
          <li key={number} className="contents">
            {index > 0 && (
              <ArrowRight className="hidden size-4 self-center text-muted-foreground md:block" aria-hidden="true" />
            )}
            <div className="grid content-start gap-2 rounded-lg bg-muted/40 p-3">
              <span className="flex items-center gap-2">
                <span className="grid size-6 shrink-0 place-items-center rounded-full bg-primary text-xs font-medium text-primary-foreground">
                  {number}
                </span>
                <Icon className="size-4 text-muted-foreground" aria-hidden="true" />
                <span className="text-sm font-medium">{title}</span>
              </span>
              <span className="text-sm text-muted-foreground">{text}</span>
            </div>
          </li>
        ))}
      </ol>
      <div className="grid gap-2 border-t pt-3 text-sm sm:grid-cols-2">
        <p className="flex gap-2">
          <Search className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span>
            <span className="font-medium">{t("Antes de cambiar algo:")}</span>{" "}
            <span className="text-muted-foreground">{t("ves qué reglas toca ese recorrido y qué podría romperse.")}</span>
          </span>
        </p>
        <p className="flex gap-2">
          <Bug className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <span>
            <span className="font-medium">{t("Cuando algo falla:")}</span>{" "}
            <span className="text-muted-foreground">{t("sabes en qué orden se aplican las reglas y dónde mirar.")}</span>
          </span>
        </p>
      </div>
      <p className="text-xs text-muted-foreground">{t("Todo sale del código, sin IA.")}</p>
    </section>
    </details>
  )
}

function ProcessRow({ process, open, onToggle }: { process: Process; open: boolean; onToggle: () => void }) {
  const trigger = TRIGGERS[process.trigger?.kind ?? "handler"]
  const Icon = trigger.icon
  const rules = rulesOf(process)
  const id = `process-${process.path}-${process.start_line}`.replace(/[^\w-]/g, "-")
  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={id}
        onClick={onToggle}
        className="grid w-full gap-1.5 px-4 py-3 text-left hover:bg-muted/50"
      >
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Icon className="size-3.5" aria-hidden="true" />
            {trigger.label}
          </span>
          <span className={cn("font-medium", process.trigger?.kind === "http" && "font-mono text-sm")}>{process.name}</span>
          <span className="ml-auto flex items-center gap-2 text-xs text-muted-foreground">
            {process.rules === 1 ? t("aplica 1 regla") : t("aplica {0} reglas", process.rules)}
            {process.verified > 0 && <StatusBadge tone="verified">{t("{0} verificadas", process.verified)}</StatusBadge>}
            <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden="true" />
          </span>
        </span>
        {!open && (
          <span className="flex min-w-0 flex-wrap items-center gap-1 text-xs text-muted-foreground">
            {rules.slice(0, 3).map((rule, index) => (
              <span key={rule.id} className="flex min-w-0 items-center gap-1">
                {index > 0 && <ArrowRight className="size-3 shrink-0" aria-hidden="true" />}
                <span className="truncate">{rule.title}</span>
              </span>
            ))}
            {rules.length > 3 && <span>{t("y {0} más", rules.length - 3)}</span>}
          </span>
        )}
      </button>
      {open && (
        <ol id={id} className="grid gap-0 px-4 pb-4">
          {rules.map((rule, index) => (
            <li key={rule.id} className="relative grid grid-cols-[1.75rem_1fr] gap-x-3 pb-4 last:pb-0">
              {index < rules.length - 1 && (
                <span aria-hidden="true" className="absolute top-7 bottom-0 left-[0.8125rem] w-px bg-border" />
              )}
              <span
                className={cn(
                  "z-10 grid size-7 place-items-center rounded-full border bg-background text-xs font-medium tabular-nums",
                  rule.verified && "border-emerald-500 text-emerald-600",
                )}
              >
                {index + 1}
              </span>
              <span className="grid min-w-0 gap-0.5 pt-1">
                <span className="flex flex-wrap items-center gap-2">
                  <RuleLink id={rule.id} className="font-medium hover:underline">
                    {rule.title}
                  </RuleLink>
                  {rule.verified && <StatusBadge tone="verified">{t("verificada")}</StatusBadge>}
                </span>
                {/* A statement not written yet is a placeholder with code: nothing to read. */}
                {rule.statement && !rule.statement.startsWith("Pendiente de redactar") && (
                  <span className="text-sm text-muted-foreground">{rule.statement}</span>
                )}
                <span className="font-mono text-xs break-all text-muted-foreground/80">{rule.where}</span>
              </span>
            </li>
          ))}
        </ol>
      )}
    </li>
  )
}
