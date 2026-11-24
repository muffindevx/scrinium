import { t } from "@/lib/i18n"
import {
  Activity,
  ArrowRight,
  Check,
  CheckCircle2,
  Circle,
  Cloud,
  Cpu,
  GitCompareArrows,
  type LucideIcon,
  RefreshCw,
  Server,
  ShieldCheck,
  X,
} from "lucide-react"
import { type ReactNode, useCallback, useEffect, useState } from "react"
import { Link } from "react-router"
import { cn } from "cn"
import { StatusBadge } from "@/components/status"
import { sourceTone } from "@/lib/status"
import { Button } from "@/components/ui/button"
import {
  type AiSetup,
  type Analysis,
  type EmbeddingStatus,
  type Inbox,
  type IndexStatus,
  type Project,
  type RulebookHealth,
  type SecurityStatus,
  type Source,
  getAnalysis,
  getHealth,
  getInbox,
  getIndexStatus,
  listAnalyses,
  listSources,
} from "@/lib/api"
import { relativeTime } from "@/lib/time"
import { useErrors } from "@/screens/hooks"
import type { Drop } from "@/lib/folder-drop"
import { ModelSetup } from "@/screens/ModelSetup"
import { RulesStatus } from "@/screens/RulesStatus"
import { RecentActivity } from "@/screens/ActivityScreen"
import { QuickStart } from "@/screens/QuickStart"
import { ErrorText } from "@/screens/shared"

/** "01 / CONFIGURACIÓN" over a section title, as on the Home mock-ups. */
function SectionHead({
  number,
  eyebrow,
  title,
  description,
  action,
}: {
  number: string
  eyebrow: string
  title: string
  description?: ReactNode
  action?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="grid gap-1.5">
        <p className="text-[0.65rem] font-semibold tracking-[0.14em] text-muted-foreground uppercase">
          <span className="text-primary">{number}</span> / {eyebrow}
        </p>
        <h2 className="font-heading text-lg font-semibold tracking-tight">{title}</h2>
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      {action}
    </div>
  )
}

function MoreLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <Link to={to} className="inline-flex shrink-0 items-center gap-1.5 text-xs whitespace-nowrap text-foreground/80 hover:text-foreground hover:underline">
      {children} <ArrowRight className="size-3.5" aria-hidden="true" />
    </Link>
  )
}

const TILE = {
  plum: "bg-accent text-primary",
  plain: "bg-muted text-foreground/80",
  amber: "bg-amber-500/15 text-amber-700 dark:text-amber-400",
}

function IconTile({ icon: Icon, tone = "plain" }: { icon: LucideIcon; tone?: keyof typeof TILE }) {
  return (
    <span className={cn("grid size-7 shrink-0 place-items-center rounded-md", TILE[tone])}>
      <Icon className="size-4" aria-hidden="true" />
    </span>
  )
}

function Panel({ className, children }: { className?: string; children: ReactNode }) {
  return (
    <section
      className={cn(
        "flex min-w-0 flex-col gap-4 rounded-xl bg-card p-4 text-card-foreground shadow-[0_1px_2px_oklch(0_0_0/0.04)] ring-1 ring-border/70",
        className,
      )}
    >
      {children}
    </section>
  )
}

function Dot({ className }: { className?: string }) {
  return <span className={cn("inline-block size-1.5 shrink-0 rounded-full", className)} aria-hidden="true" />
}

function greeting(now = new Date()): string {
  const hour = now.getHours()
  if (hour >= 6 && hour < 13) return t("Buenos días")
  if (hour >= 13 && hour < 21) return t("Buenas tardes")
  return t("Buenas noches")
}

function ModelCheck({ status, children }: { status: EmbeddingStatus; children: ReactNode }) {
  const ready = status === "ready"
  const Icon = ready ? Check : X
  return (
    <span className={cn("inline-flex items-center gap-1.5", ready ? "text-primary" : "text-amber-700 dark:text-amber-400")}>
      <Icon className="size-3.5" aria-hidden="true" />
      {children}
    </span>
  )
}

/** Once chosen (ADR-013), Home shows the three modes with the one in use ticked; Ajustes changes it. */
function ModeCards({ setup, status }: { setup: AiSetup; status: SecurityStatus }) {
  const localModel = setup.local_server?.model ?? setup.local_model ?? status.providers.llm_model
  const cards: {
    choice: AiSetup["choice"]
    icon: LucideIcon
    eyebrow: string
    title: string
    text: string
    meta: ReactNode
    action: string
  }[] = [
    {
      choice: "provider",
      icon: Cloud,
      eyebrow: t("Recomendado"),
      title: t("Tu proveedor"),
      text: t("DeepSeek, GLM, Claude o cualquier API compatible con OpenAI, con tu clave. No necesita GPU."),
      meta: t("Compatible con OpenAI"),
      action: t("Configurar"),
    },
    {
      choice: "local_server",
      icon: Server,
      eyebrow: t("En tu equipo"),
      title: t("Modelo local"),
      text: t("Ollama, vLLM o llama-server en este equipo. El código no sale de aquí."),
      meta: localModel ? (
        <span className="inline-flex items-center gap-1.5">
          <span className="font-mono">{localModel}</span>
          <Dot className={status.providers.llm === "ready" ? "bg-primary" : "bg-amber-500"} />
          {status.providers.llm === "ready" ? t("Disponible") : t("No responde")}
        </span>
      ) : (
        t("Sin modelo elegido")
      ),
      action: t("Ver modelos"),
    },
    {
      choice: "isolated",
      icon: ShieldCheck,
      eyebrow: t("Máxima privacidad"),
      title: t("Modo aislado"),
      text: t("Ningún proveedor externo. Para repositorios con requisitos estrictos."),
      meta: t("Solo análisis local"),
      action: t("Activar"),
    },
  ]
  return (
    <div className="grid gap-3 md:grid-cols-3">
      {cards.map((card) => {
        const current = setup.choice === card.choice
        return (
          <Panel key={card.choice} className={cn("justify-between gap-6", current && "ring-primary/40")}>
            <div className="grid gap-3">
              <div className="flex items-center gap-2.5">
                <IconTile icon={card.icon} tone={current ? "plum" : "plain"} />
                <span className="flex-1 text-[0.62rem] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
                  {card.eyebrow}
                </span>
                {current && <Check className="size-4 text-primary" aria-label={t("En uso")} />}
              </div>
              <div className="grid gap-1">
                <h3 className="text-sm font-semibold">{card.title}</h3>
                <p className="text-xs leading-relaxed text-muted-foreground">{card.text}</p>
              </div>
            </div>
            <div className="flex items-center justify-between gap-2 text-[0.7rem] text-muted-foreground">
              <span className="min-w-0 truncate">{card.meta}</span>
              {current && card.choice === "isolated" ? (
                <span className="font-medium text-primary">{t("Activo")}</span>
              ) : (
                <MoreLink to="/ajustes/modelos">{card.action}</MoreLink>
              )}
            </div>
          </Panel>
        )
      })}
    </div>
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

interface DiffFile {
  path: string
  added: number
  removed: number
}

interface LastDiff {
  id: string
  at: string
  files: DiffFile[]
  pending: number
}

// A finding still waits for a person until it is confirmed, tested or dismissed.
const JUDGED = new Set(["human_confirmed", "test_verified", "false_positive"])

function lastDiffOf(run: Analysis): LastDiff {
  const raw = Array.isArray(run.result?.files) ? (run.result.files as Partial<DiffFile>[]) : []
  const files = raw
    .filter((file): file is DiffFile => typeof file.path === "string")
    .map((file) => ({ path: file.path, added: Number(file.added) || 0, removed: Number(file.removed) || 0 }))
  const pending = (run.findings ?? []).filter((finding) => !JUDGED.has(finding.assessment_status)).length
  return { id: run.id, at: run.created_at, files, pending }
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
  profileName = null,
  onSessionLost,
}: {
  project: Project | null
  status: SecurityStatus
  localOnly?: boolean
  setup?: AiSetup | null
  onSetupChosen?: (setup: AiSetup) => void
  profileName?: string | null
  onProjectCreated: (project: Project) => void
  drop: Drop | null
  onDropUsed: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [sources, setSources] = useState<Source[] | null>(null)
  const [rules, setRules] = useState<RulebookHealth | null>(null)
  // R2: after a restore (or a first sync) the vectors are rebuilt in the background.
  const [index, setIndex] = useState<IndexStatus | null>(null)
  const [lastDiff, setLastDiff] = useState<LastDiff | null>(null)
  // The inbox's priority counts and the proposals kept out of it, for "Qué necesita tu atención".
  const [inboxSummary, setInboxSummary] = useState<Inbox | null>(null)
  useEffect(() => {
    if (!project) return
    getInbox(project.id, 0, 1)
      .then(setInboxSummary)
      .catch(() => setInboxSummary(null))
  }, [project])
  const { error, fail } = useErrors(onSessionLost)
  // The last change analyzed, for the impact card: its files and what nobody judged yet.
  useEffect(() => {
    if (!project) return
    listAnalyses(project.id, "code_diff", 1)
      .then((runs) => (runs[0] ? getAnalysis(runs[0].id) : null))
      .then((run) => setLastDiff(run ? lastDiffOf(run) : null))
      .catch(() => setLastDiff(null))
  }, [project])

  const load = useCallback(() => {
    if (!project) return
    Promise.all([listSources(project.id), getHealth(project.id), getIndexStatus(project.id)])
      .then(([loadedSources, loadedRules, loadedIndex]) => {
        setSources(loadedSources)
        setRules(loadedRules)
        setIndex(loadedIndex)
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
    <section className="grid gap-5 border-t pt-8">
      <SectionHead
        number="01"
        eyebrow={t("Configuración")}
        title={t("¿Qué modelo escribe las reglas?")}
        description={t("La búsqueda, el índice y las reglas se quedan en este equipo. Solo el modelo que redacta las propuestas puede estar fuera, y nada se envía sin tu permiso. Puedes cambiarlo en Ajustes.")}
      />
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
    { done: hasSources, label: <Link to="/integraciones" className="hover:underline">{t("Añade el repositorio o la carpeta del proyecto")}</Link> },
    { done: synced !== null, label: <Link to="/integraciones" className="hover:underline">{t("Sincronízalo para preparar el conocimiento")}</Link> },
    { done: (rules?.rules ?? 0) > 0, label: <Link to="/reglas" className="hover:underline">{t("Extrae las reglas del código")}</Link> },
  ]
  const pendingSetup = sources !== null && rules !== null && steps.some((step) => !step.done)
  const rebuilding = index !== null && index.provider === "ready" && index.chunks > 0 && index.embedded < index.chunks
  const name = profileName?.trim()

  return (
    <div className="grid gap-14 *:min-w-0">
      <div className="grid gap-8">
        <header className="grid gap-7">
          <div className="grid gap-2">
            <p className="flex items-center gap-2 text-[0.65rem] font-semibold tracking-[0.14em] text-muted-foreground uppercase">
              <Dot className="bg-primary" />
              {t("Proyecto activo")} <span className="text-muted-foreground/50">/</span> {project.name}
            </p>
            <h1 className="font-heading text-[2.1rem] leading-tight font-bold tracking-[-0.035em] max-sm:text-[1.7rem]">
              {name ? `${greeting()}, ${name}` : greeting()}
              <span className="text-primary">.</span>
            </h1>
            <p className="text-sm text-muted-foreground">
              {project.description || t("Tu código, traducido en decisiones que el equipo entiende.")}
            </p>
            {/* Freshness in one line under the title: when the code was read and when a person last reviewed. */}
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1 text-xs text-muted-foreground">
              {syncing ? (
                <StatusBadge tone="in_progress">{t("Sincronizando…")}</StatusBadge>
              ) : synced ? (
                <span className="inline-flex items-center gap-1.5">
                  <RefreshCw className="size-3.5" aria-hidden="true" />
                  {t("Sincronizado")} {relativeTime(synced)}
                </span>
              ) : sources !== null ? (
                <span>{t("Todavía sin sincronizar")}</span>
              ) : null}
              {rules?.last_review_at && (
                <span className="inline-flex items-center gap-1.5">
                  <CheckCircle2 className="size-3.5" aria-hidden="true" />
                  {t("Última revisión {0}", relativeTime(rules.last_review_at))}
                </span>
              )}
            </p>
          </div>
          <RulesStatus rules={rules} inbox={inboxSummary} />
        </header>
        <ErrorText>{error}</ErrorText>
      {rebuilding && (
        <div role="status" className="flex flex-wrap items-center gap-4 rounded-xl bg-accent/70 px-4 py-3.5 dark:bg-accent/60">
          <Activity className="size-4 shrink-0 text-primary" aria-hidden="true" />
          <div className="grid min-w-0 flex-1 gap-0.5">
            <p className="text-xs font-semibold">{t("Tu índice semántico se está reconstruyendo")}</p>
            <p className="text-[0.7rem] text-muted-foreground">
              {t("{0} de {1} fragmentos listos · La búsqueda por palabras sigue disponible.", index.embedded, index.chunks)}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <div className="h-1 w-24 overflow-hidden rounded-full bg-foreground/10">
              <div className="h-full rounded-full bg-primary" style={{ width: `${Math.round((index.embedded / index.chunks) * 100)}%` }} />
            </div>
            <span className="w-8 text-right text-[0.7rem] text-muted-foreground tabular-nums">
              {Math.round((index.embedded / index.chunks) * 100)}%
            </span>
          </div>
        </div>
      )}

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
        <Panel className="gap-3 ring-primary/25">
          <h2 className="text-sm font-semibold">{t("Próximos pasos")}</h2>
          <ol className="grid gap-2">
            {steps.map((step, index) => (
              <Step key={index} done={step.done}>
                {step.label}
              </Step>
            ))}
          </ol>
        </Panel>
      )}
      </div>
      {firstRun}
      {setup !== null && setup.choice !== null && !setup.managed && (
        <section className="grid gap-5 border-t pt-8">
          <SectionHead
            number="01"
            eyebrow={t("Configuración")}
            title={t("¿Cómo quieres analizar el código?")}
            description={t("Elige dónde se procesa el modelo. Tus fuentes nunca se envían sin permiso.")}
            action={<MoreLink to="/ajustes/privacidad">{t("Privacidad y datos")}</MoreLink>}
          />
          <ModeCards setup={setup} status={status} />
        </section>
      )}
      <section className="grid gap-5 border-t pt-8">
        <SectionHead
          number="02"
          eyebrow={t("Vista general")}
          title={t("Lo que está pasando")}
          action={<MoreLink to="/actividad">{t("Ver actividad")}</MoreLink>}
        />
        <div className="grid gap-3 md:grid-cols-3">
          <RecentActivity project={project} />
          <Panel className="justify-between">
            <div className="grid gap-4">
              <div className="flex items-center justify-between gap-2">
                <IconTile icon={Cpu} />
                <span className="text-[0.62rem] font-semibold tracking-[0.12em] text-muted-foreground uppercase">
                  {localOnly ? t("LLM local") : t("Proveedor externo")}
                </span>
              </div>
              <div className="grid gap-1.5">
                <h3 className="text-sm font-semibold">{t("Motor de análisis")}</h3>
                <p className="flex items-center gap-1.5 text-[0.7rem] text-muted-foreground">
                  {status.providers.llm_model && <span className="font-mono">{status.providers.llm_model}</span>}
                  <Dot className={status.providers.llm === "ready" ? "bg-primary" : "bg-amber-500"} />
                  {status.providers.llm === "ready" ? t("en ejecución") : t("no responde")}
                </p>
              </div>
              <p className="flex flex-wrap gap-x-4 gap-y-1 text-[0.7rem]">
                <ModelCheck status={status.providers.llm}>{t("Respuestas")}</ModelCheck>
                <ModelCheck status={status.providers.embeddings}>{t("Índice semántico")}</ModelCheck>
              </p>
              <p className="text-[0.7rem] text-muted-foreground">
                {localOnly
                  ? t("Todo se calcula en este equipo. Nada sale de aquí.")
                  : t("La búsqueda se calcula aquí. Algunas tareas de este proyecto van a un proveedor externo: míralo en Ajustes.")}
              </p>
            </div>
            <MoreLink to="/ajustes/modelos">{t("Ver el motor")}</MoreLink>
          </Panel>
          <Panel className="justify-between">
            <div className="grid gap-4">
              <div className="flex items-center justify-between gap-2">
                <IconTile icon={GitCompareArrows} tone="amber" />
                {lastDiff !== null && lastDiff.pending > 0 && (
                  <Link
                    to={`/impacto/${lastDiff.id}`}
                    title={t("Hallazgos del último análisis que nadie ha confirmado ni descartado")}
                    className="text-[0.7rem] text-muted-foreground hover:text-foreground hover:underline"
                  >
                    {lastDiff.pending === 1 ? t("1 pendiente") : t("{0} pendientes", lastDiff.pending)}
                  </Link>
                )}
              </div>
              <div className="grid gap-1.5">
                <h3 className="text-sm font-semibold">{t("Impacto de cambios")}</h3>
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {t("Pega un diff o la descripción de un bug y mira qué reglas y tests se ven afectados antes de tocar nada.")}
                </p>
              </div>
              {lastDiff !== null && lastDiff.files.length > 0 && (
                <Link
                  to={`/impacto/${lastDiff.id}`}
                  title={t("Último cambio analizado, {0}", relativeTime(lastDiff.at))}
                  className="grid gap-1 rounded-md bg-muted px-2.5 py-2 font-mono text-[0.68rem] hover:bg-muted/70"
                >
                  <span className="flex items-center justify-between gap-3">
                    <span className="min-w-0 truncate">{lastDiff.files[0].path}</span>
                    <span className="shrink-0 tabular-nums">
                      <span className="text-emerald-700 dark:text-emerald-400">+{lastDiff.files[0].added}</span>{" "}
                      <span className="text-amber-700 dark:text-amber-400">−{lastDiff.files[0].removed}</span>
                    </span>
                  </span>
                  {lastDiff.files.length > 1 && (
                    <span className="font-sans text-muted-foreground">
                      {lastDiff.files.length === 2
                        ? t("y 1 archivo más")
                        : t("y {0} archivos más", lastDiff.files.length - 1)}
                    </span>
                  )}
                </Link>
              )}
            </div>
            <div>
              <Button size="sm" render={<Link to="/impacto" />} nativeButton={false}>
                {t("Analizar un cambio")}
              </Button>
            </div>
          </Panel>
        </div>
      </section>
    </div>
  )
}
