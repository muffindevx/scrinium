import {
  ArrowRight,
  BookOpen,
  CheckCircle2,
  Circle,
  Cpu,
  FolderGit2,
  ScanSearch,
  Sparkles,
} from "lucide-react"
import { type FormEvent, type ReactNode, useEffect, useState } from "react"
import { Link, useNavigate } from "react-router"
import { cn } from "cn"
import { EmptyState } from "@/components/page"
import { ModelStatusBadge, StatusBadge } from "@/components/status"
import { VERIFIED_STATUSES, sourceTone } from "@/lib/status"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/input"
import {
  type Project,
  type RuleSummary,
  type SecurityStatus,
  type Source,
  listRules,
  listSources,
} from "@/lib/api"
import { relativeTime } from "@/lib/time"
import { useErrors } from "@/screens/hooks"
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

export function HomeScreen({
  project,
  status,
  onSessionLost,
}: {
  project: Project | null
  status: SecurityStatus
  onSessionLost: (error: unknown) => void
}) {
  const navigate = useNavigate()
  const [sources, setSources] = useState<Source[] | null>(null)
  const [rules, setRules] = useState<RuleSummary[] | null>(null)
  const [question, setQuestion] = useState("")
  const { error, fail } = useErrors(onSessionLost)

  useEffect(() => {
    if (!project) return
    Promise.all([listSources(project.id), listRules(project.id)])
      .then(([loadedSources, loadedRules]) => {
        setSources(loadedSources)
        setRules(loadedRules)
      })
      .catch(fail)
  }, [project, fail])

  if (!project) {
    return (
      <EmptyState
        icon={FolderGit2}
        title="Empieza por tu proyecto"
        action={
          <Button render={<Link to="/fuentes" />} nativeButton={false}>
            Crear un proyecto
            <ArrowRight data-icon="inline-end" />
          </Button>
        }
      >
        Scrinium lee tu repositorio y tus documentos en este equipo y te explica qué hace el sistema,
        siempre con citas al código. Nada sale de aquí.
      </EmptyState>
    )
  }

  const ask = (event: FormEvent) => {
    event.preventDefault()
    if (question.trim()) navigate("/preguntar", { state: { question: question.trim() } })
  }

  const synced = lastSync(sources ?? [])
  const verified = rules?.filter((rule) =>
    VERIFIED_STATUSES.includes(rule.latest_version?.verification_status ?? "unverified"),
  ).length
  const proposed = rules?.filter((rule) => rule.latest_version?.verification_status === "unverified").length
  const hasSources = (sources?.length ?? 0) > 0
  const steps = [
    { done: hasSources, label: <Link to="/fuentes" className="hover:underline">Añade el repositorio o la carpeta del proyecto</Link> },
    { done: synced !== null, label: <Link to="/fuentes" className="hover:underline">Sincronízalo para preparar el conocimiento</Link> },
    { done: (rules?.length ?? 0) > 0, label: <Link to="/reglas" className="hover:underline">Extrae las reglas del código</Link> },
  ]
  const pendingSetup = sources !== null && rules !== null && steps.some((step) => !step.done)
  const syncing = sources?.some((source) => sourceTone(source)[0] === "in_progress")

  return (
    <>
      <header className="grid gap-1">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">{project.name}</h1>
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          {syncing ? (
            <StatusBadge tone="in_progress">Sincronizando…</StatusBadge>
          ) : synced ? (
            <>Sincronizado {relativeTime(synced)}</>
          ) : sources !== null ? (
            <>Todavía sin sincronizar</>
          ) : null}
          {project.description && <span>· {project.description}</span>}
        </p>
      </header>
      <ErrorText>{error}</ErrorText>

      <form onSubmit={ask} className="grid gap-3 rounded-xl border bg-card p-5">
        <label htmlFor="home-question" className="font-heading text-lg font-medium">
          ¿Qué quieres entender hoy?
        </label>
        <Textarea
          id="home-question"
          rows={2}
          maxLength={1000}
          placeholder="¿Cómo se calcula el total de una factura? ¿Quién puede cancelar un pedido?"
          className="resize-none text-base md:text-base"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault()
              event.currentTarget.form?.requestSubmit()
            }
          }}
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-xs text-muted-foreground">
            Responde solo con lo que hay en el proyecto y cita cada afirmación.
          </p>
          <Button type="submit" disabled={!question.trim()}>
            <Sparkles data-icon="inline-start" />
            Preguntar
          </Button>
        </div>
      </form>

      {pendingSetup && (
        <section className="grid gap-3 rounded-xl border border-sky-500/20 bg-sky-500/5 p-4">
          <h2 className="text-sm font-medium">Próximos pasos</h2>
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
          title="Reglas"
          footer={
            <Link to="/reglas" className="inline-flex items-center gap-1 text-sm font-medium hover:underline">
              Revisar reglas <ArrowRight className="size-3.5" />
            </Link>
          }
        >
          {rules === null ? (
            <p className="text-sm text-muted-foreground">Cargando…</p>
          ) : (
            <div className="grid gap-2">
              <p className="font-heading text-3xl font-semibold">{rules.length}</p>
              <div className="flex flex-wrap gap-1.5">
                <StatusBadge tone="verified">{verified} verificadas</StatusBadge>
                <StatusBadge tone="proposed">{proposed} sin verificar</StatusBadge>
              </div>
            </div>
          )}
        </HealthCard>
        <HealthCard
          icon={Cpu}
          title="Motor de IA local"
          footer={
            <Link to="/ajustes" className="inline-flex items-center gap-1 text-sm font-medium hover:underline">
              Ver el motor <ArrowRight className="size-3.5" />
            </Link>
          }
        >
          <dl className="grid gap-2 text-sm">
            <div className="flex items-center justify-between gap-2">
              <dt className="text-muted-foreground">Respuestas</dt>
              <dd>
                <ModelStatusBadge status={status.providers.llm} />
              </dd>
            </div>
            <div className="flex items-center justify-between gap-2">
              <dt className="text-muted-foreground">Búsqueda por significado</dt>
              <dd>
                <ModelStatusBadge status={status.providers.embeddings} />
              </dd>
            </div>
          </dl>
          <p className="text-xs text-muted-foreground">Todo se calcula en este equipo. Nada sale de aquí.</p>
        </HealthCard>
        <HealthCard
          icon={ScanSearch}
          title="Impacto de un cambio"
          footer={
            <Button variant="outline" size="sm" render={<Link to="/impacto" />} nativeButton={false}>
              Analizar un cambio
            </Button>
          }
        >
          <p className="text-sm text-muted-foreground">
            Pega un diff, una consulta SQL o la descripción de un bug y mira qué reglas y tests se ven
            afectados antes de tocar nada.
          </p>
        </HealthCard>
      </div>
    </>
  )
}
