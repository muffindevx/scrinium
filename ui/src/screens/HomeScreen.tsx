import { t } from "@/lib/i18n"
import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Circle,
  Cpu,
  ScanSearch,
} from "lucide-react"
import { type ReactNode, useCallback, useEffect, useState } from "react"
import { Link } from "react-router"
import { cn } from "cn"
import { ModelStatusBadge, StatusBadge } from "@/components/status"
import { sourceTone } from "@/lib/status"
import { Button } from "@/components/ui/button"
import {
  type AiSetup,
  type Project,
  type RulebookHealth,
  type SecurityStatus,
  type Source,
  getHealth,
  listSources,
} from "@/lib/api"
import { relativeTime } from "@/lib/time"
import { useErrors } from "@/screens/hooks"
import type { Drop } from "@/lib/folder-drop"
import { ModelSetup } from "@/screens/ModelSetup"
import { QuickStart } from "@/screens/QuickStart"
import { ErrorText } from "@/screens/shared"

function HealthCard({
  icon: Icon,
  title,
  children,
  footer,
}: {
  icon: typeof Cpu
  title: string
  children: ReactNode
  footer: ReactNode
}) {
  return (
    <section className="flex flex-col justify-between gap-4 rounded-xl border bg-card p-4">
      <div className="grid gap-3">
        <h2 className="flex items-center gap-2 text-sm font-medium text-muted-foreground">
          <Icon className="size-4" aria-hidden="true" />
          {title}
        </h2>
        {children}
      </div>
      <div>{footer}</div>
    </section>
  )
}

function Step({ done, children }: { done: boolean; children: ReactNode }) {
  const Icon = done ? CheckCircle2 : Circle
  return (
    <li className={cn("flex items-center gap-2 text-sm", done && "text-muted-foreground line-through")}>
      <Icon className={cn("size-4 shrink-0", done ? "text-emerald-500" : "text-muted-foreground")} aria-hidden="true" />
      {children}
    </li>
  )
}

function lastSync(sources: Source[]): string | null {
  const times = sources.map((source) => source.last_successful_sync_at).filter((at): at is string => Boolean(at))
  if (times.length === 0) return null
  return times.sort().at(-1) ?? null
}

// While a source syncs, Home looks again this often to say when it is done.
const SYNC_POLL_MS = 3000

export function HomeScreen({
  project,
  status,
  localOnly = true,
  onProjectCreated,
  drop,
  onDropUsed,
  setup = null,
  onSetupChosen = () => undefined,
  onSessionLost,
}: {
  project: Project | null
  status: SecurityStatus
  localOnly?: boolean
  setup?: AiSetup | null
  onSetupChosen?: (setup: AiSetup) => void
  onProjectCreated: (project: Project) => void
  drop: Drop | null
  onDropUsed: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [sources, setSources] = useState<Source[] | null>(null)
  const [rules, setRules] = useState<RulebookHealth | null>(null)
  const { error, fail } = useErrors(onSessionLost)

  const load = useCallback(() => {
    if (!project) return
    Promise.all([listSources(project.id), getHealth(project.id)])
      .then(([loadedSources, loadedRules]) => {
        setSources(loadedSources)
        setRules(loadedRules)
      })
      .catch(fail)
  }, [project, fail])
  useEffect(load, [load])
  const syncing = sources?.some((source) => sourceTone(source)[0] === "in_progress") ?? false
  useEffect(() => {
    if (!syncing) return
    const timer = window.setInterval(load, SYNC_POLL_MS)
    return () => window.clearInterval(timer)
  }, [syncing, load])

  // ADR-013: until someone chooses where the writing model lives, Home asks first.
  const firstRun = setup !== null && setup.choice === null && (
    <section className="grid gap-3">
      <div className="grid gap-1">
        <h2 className="font-heading text-lg font-medium">{t("¿Qué modelo escribe las reglas?")}</h2>
        <p className="text-sm text-muted-foreground">
          {t("La búsqueda, el índice y las reglas se quedan en este equipo. Solo el modelo que redacta las propuestas puede estar fuera, y nada se envía sin tu permiso. Puedes cambiarlo en Ajustes.")}
        </p>
      </div>
      <ModelSetup setup={setup} onChosen={onSetupChosen} onSessionLost={onSessionLost} />
    </section>
  )

  if (!project) {
    return (
      <>
        <header className="grid gap-1">
          <h1 className="font-heading text-2xl font-semibold tracking-tight">{t("Te damos la bienvenida a Scrinium")}</h1>
          <p className="max-w-2xl text-sm text-muted-foreground">
            {t("Lee tu repositorio y tus documentos en este equipo y te explica qué hace el sistema, siempre con citas al código. Nada sale de aquí sin tu permiso.")}
          </p>
        </header>
        {firstRun}
        <QuickStart
          key={drop?.at}
          project={null}
          drop={drop}
          onProjectCreated={onProjectCreated}
          onAdded={onDropUsed}
          onSessionLost={onSessionLost}
        />
      </>
    )
  }

  const synced = lastSync(sources ?? [])
  const hasSources = (sources?.length ?? 0) > 0
  const steps = [
    { done: hasSources, label: <Link to="/fuentes" className="hover:underline">{t("Añade el repositorio o la carpeta del proyecto")}</Link> },
    { done: synced !== null, label: <Link to="/fuentes" className="hover:underline">{t("Sincronízalo para preparar el conocimiento")}</Link> },
    { done: (rules?.rules ?? 0) > 0, label: <Link to="/reglas" className="hover:underline">{t("Extrae las reglas del código")}</Link> },
  ]
  const pendingSetup = sources !== null && rules !== null && steps.some((step) => !step.done)

  return (
    <>
      <header className="grid gap-1">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">{project.name}</h1>
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          {syncing ? (
            <StatusBadge tone="in_progress">{t("Sincronizando…")}</StatusBadge>
          ) : synced ? (
            <>{t("Sincronizado")} {relativeTime(synced)}</>
          ) : sources !== null ? (
            <>{t("Todavía sin sincronizar")}</>
          ) : null}
          {project.description && <span>· {project.description}</span>}
        </p>
      </header>
      <ErrorText>{error}</ErrorText>
      {firstRun}

      {sources !== null && (!hasSources || drop !== null) && (
        <QuickStart
          key={drop?.at}
          project={project}
          drop={drop}
          onProjectCreated={onProjectCreated}
          onAdded={() => {
            onDropUsed()
            load()
          }}
          onSessionLost={onSessionLost}
        />
      )}

      {pendingSetup && (
        <section className="grid gap-3 rounded-xl border border-sky-500/20 bg-sky-500/5 p-4">
          <h2 className="text-sm font-medium">{t("Próximos pasos")}</h2>
          <ol className="grid gap-2">
            {steps.map((step, index) => (
              <Step key={index} done={step.done}>
                {step.label}
              </Step>
            ))}
          </ol>
        </section>
      )}

      <div className="grid gap-4 md:grid-cols-3">
        <HealthCard
          icon={BookOpen}
          title={t("Reglas")}
          footer={
            <Link to="/reglas" className="inline-flex items-center gap-1 text-sm font-medium hover:underline">
              {t("Revisar reglas")} <ArrowRight className="size-3.5" />
            </Link>
          }
        >
          {rules === null ? (
            <p className="text-sm text-muted-foreground">{t("Cargando…")}</p>
          ) : (
            <div className="grid gap-2">
              <p className="font-heading text-3xl font-semibold">{rules.rules}</p>
              <div className="flex flex-wrap gap-1.5">
                <StatusBadge tone="verified">{rules.verified} {t("verificadas")}</StatusBadge>
                <StatusBadge tone="proposed">{rules.unreviewed} {t("sin revisar")}</StatusBadge>
                {rules.drifted > 0 && <StatusBadge tone="attention">{rules.drifted} {t("cambiaron")}</StatusBadge>}
              </div>
              {rules.inbox > 0 && (
                <Link to="/bandeja" className="text-sm hover:underline">
                  {rules.inbox === 1 ? t("1 regla en la bandeja") : t("{0} reglas en la bandeja", rules.inbox)}
                </Link>
              )}
              <p className="text-xs text-muted-foreground">
                {rules.last_review_at ? t("Última revisión {0}", relativeTime(rules.last_review_at)) : t("Nada revisado aún")}
                {rules.oldest_unreviewed_at &&
                  t(" · la propuesta más antigua sin revisar es de {0}", relativeTime(rules.oldest_unreviewed_at))}
              </p>
            </div>
          )}
        </HealthCard>
        <HealthCard
          icon={Cpu}
          title={t("Motor de IA local")}
          footer={
            <Link to="/ajustes/modelos" className="inline-flex items-center gap-1 text-sm font-medium hover:underline">
              {t("Ver el motor")} <ArrowRight className="size-3.5" />
            </Link>
          }
        >
          <dl className="grid gap-2 text-sm">
            <div className="flex items-center justify-between gap-2">
              <dt className="text-muted-foreground">{t("Respuestas")}</dt>
              <dd>
                <ModelStatusBadge status={status.providers.llm} />
              </dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt className="text-muted-foreground">{t("Búsqueda por significado")}</dt>
              <dd>
                <ModelStatusBadge status={status.providers.embeddings} />
              </dd>
            </div>
          </dl>
          <p className="text-xs text-muted-foreground">
            {localOnly
              ? t("Todo se calcula en este equipo. Nada sale de aquí.")
              : t("La búsqueda se calcula aquí. Algunas tareas de este proyecto van a un proveedor externo: míralo en Ajustes.")}
          </p>
        </HealthCard>
        <HealthCard
          icon={ScanSearch}
          title={t("Impacto de un cambio")}
          footer={
            <Button variant="outline" size="sm" render={<Link to="/impacto" />} nativeButton={false}>
              {t("Analizar un cambio")}
            </Button>
          }
        >
          <p className="text-sm text-muted-foreground">
            {t("Pega un diff o la descripción de un bug y mira qué reglas y tests se ven afectados antes de tocar nada.")}
          </p>
        </HealthCard>
      </div>
    </>
  )
}
