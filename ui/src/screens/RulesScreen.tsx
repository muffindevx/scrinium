import { t } from "@/lib/i18n"
import { ArrowLeft, BookOpen, Check, ChevronRight, Pause, PenLine, Sparkles, X } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useState } from "react"
import { Link, useParams, useSearchParams } from "react-router"
import { cn } from "cn"
import { CONTEXT_LINES, CodeExcerpt } from "@/components/code-excerpt"
import { RuleLink } from "@/components/rule-link"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { StatusBadge, VerificationStatusBadge } from "@/components/status"
import { VERIFIED_STATUSES } from "@/lib/status"
import { Button } from "@/components/ui/button"
import { Input, Label, Textarea } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  ApiError,
  type Evidence,
  type FunctionRef,
  type ExtractionStatus,
  type NewRule,
  type RuleCategory,
  type RuleDetail,
  type RuleImpact,
  type Relationship,
  type RuleSummary,
  type RulebookSource,
  type RuleVersion,
  type VerificationStatus,
  cancelAnalysis,
  correctRule,
  createRule,
  getExtractionStatus,
  getNeighbours,
  getRule,
  getRulebook,
  PENDING_STATEMENT_PREFIX,
  writeStatement,
  listRules,
  setVerification,
  type ModelChoice,
  startExtraction,
  type ExtractionEstimate,
  getExtractionEstimate,
} from "@/lib/api"
import { ASSESSMENT_LABELS, errorMessage } from "@/lib/labels"
import { RuleHistory } from "@/screens/RuleHistory"
import { useBackTarget, useErrors } from "@/screens/hooks"
import { ModelPicker } from "@/screens/ModelPicker"
import { AnalysisDetail, ErrorText, EvidenceItem, Eyebrow, Muted, type ScreenProps } from "@/screens/shared"

const CATEGORY_LABELS: Record<RuleCategory, string> = {
  invariant: t("Invariante"),
  validation: t("Validación"),
  authorization: t("Permisos"),
  calculation: t("Cálculo"),
  state_transition: t("Cambio de estado"),
  workflow: t("Proceso"),
  integration_contract: t("Integración"),
  performance_constraint: t("Rendimiento"),
  security_constraint: t("Seguridad"),
  other: t("Otra"),
}
const CRITICALITY_LABELS: Record<RuleSummary["criticality"], string> = {
  low: t("Baja"),
  medium: t("Media"),
  high: t("Alta"),
  critical: t("Crítica"),
}
const ORIGIN_LABELS: Record<RuleVersion["origin"], string> = {
  human: t("Escrita por una persona"),
  source_extraction: t("Propuesta por la IA a partir del código"),
  derived_analysis: t("Propuesta por un análisis"),
  import: t("Importada"),
}
const PROFILE_LABELS: Record<keyof RuleVersion["profile"], string> = {
  requested: t("Pedida"),
  documented: t("Documentada"),
  implemented: t("Implementada"),
  tested: t("Probada"),
  contradicted: t("Contradicha"),
}
const REVIEW_LABELS: Record<string, string> = {
  source_deleted: t("Revisar: se borró una fuente que la respaldaba"),
  rule_changed: t("La regla cambió: el código decide con otros valores"),
  code_changed: t("Revisar: cambió el código que la implementa"),
  code_removed: t("Retirada: se eliminó el código que la implementa"),
  code_contradicts: t("El código ya no la cumple"),
  possible_duplicate: t("Revisar: posible duplicado"),
  contradiction: t("Revisar: contradice otra regla"),
  ambiguous: t("Revisar: regla ambigua"),
}

type Filter = "all" | "review" | "unverified" | "verified" | "rejected"

function matches(rule: RuleSummary, filter: Filter) {
  const status = rule.latest_version?.verification_status ?? "unverified"
  if (filter === "all") return true
  if (filter === "review") return rule.review_reason !== null
  if (filter === "verified") return VERIFIED_STATUSES.includes(status)
  return status === filter
}

/** Review error message, with the shortcut to the profile when the name is missing. */
function ReviewError({ error }: { error: string | null }) {
  if (!error) return null
  return (
    <ErrorText>
      {error}
      {error === errorMessage("profile_name_required") && (
        <>
          {" "}
          <Link to="/ajustes" className="underline">
            {t("Ir a Ajustes")}
          </Link>
        </>
      )}
    </ErrorText>
  )
}

const STATUS_REFRESH_MS = 5000

/** V8: an extraction takes minutes. Permission is asked when one starts (a click), never on load. */
function askToNotify() {
  if (typeof Notification === "undefined" || Notification.permission !== "default") return
  Notification.requestPermission().catch(() => undefined)
}

/** A system notice only if the person is looking elsewhere; on screen the panel already says it. */
function notifyIfAway(title: string, body: string) {
  if (typeof Notification === "undefined" || Notification.permission !== "granted") return
  if (document.visibilityState === "visible") return
  try {
    new Notification(title, { body, tag: "scrinium-extraction" })
  } catch {
    // Some browsers only allow notifications from a service worker: the panel is enough.
  }
}

function count(value: number) {
  return value.toLocaleString("es")
}

/** What the extraction would send to a remote model, read before starting it (ADR-010). */
function RemoteEstimate({ estimate }: { estimate: Extract<ExtractionEstimate, { remote: true }> }) {
  if (estimate.pending === 0) return null
  const total = estimate.input_tokens + estimate.max_output_tokens
  const left = estimate.budget === null ? null : Math.max(0, estimate.budget - estimate.spent_this_month)
  return (
    <div role="status" className="grid gap-1.5 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">
      {estimate.remote_batches > 0 ? (
        <p>
          {t("Se enviarán unos")} <strong>{count(estimate.input_tokens)} tokens</strong> {t("de")} {count(estimate.files)}{" "}
          {estimate.files === 1 ? t("archivo") : t("archivos")} {t("a")} <strong>{estimate.host}</strong> ({estimate.model}{t(") en")}{" "}
          {count(estimate.remote_batches)} {estimate.remote_batches === 1 ? t("lote") : t("lotes")}{t(", y volverán como mucho")}{" "}
          {count(estimate.max_output_tokens)} {t("de respuesta.")}
        </p>
      ) : (
        <p>{t("Todos los lotes tocan rutas que nunca se envían: esta extracción se hará en este equipo.")}</p>
      )}
      {estimate.local_batches > 0 && estimate.remote_batches > 0 && (
        <p className="text-muted-foreground">
          {count(estimate.local_batches)} {estimate.local_batches === 1 ? t("lote se hará") : t("lotes se harán")} {t("en local por las rutas que nunca se envían.")}
        </p>
      )}
      {left !== null && estimate.remote_batches > 0 && total > left && (
        <p className="font-medium text-amber-700 dark:text-amber-400">
          {left === 0
            ? t("El presupuesto de este mes está agotado: no se enviará nada y todo seguirá en la cola.")
            : t("Quedan {0} tokens del presupuesto de este mes: la extracción se detendrá a medias y lo demás esperará en la cola.", count(left))}
        </p>
      )}
      {estimate.statements_remote && (
        <p className="text-muted-foreground">
          {t("Después, la redacción de los enunciados también irá al proveedor externo y gastará más tokens.")}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        {estimate.measured
          ? t("Calculado con la proporción de tus envíos anteriores.")
          : t("Aproximado: todavía no hay envíos con los que medirlo.")}{" "}
        {t("Puede ser menos si alguna petición ya tiene respuesta guardada.")}
      </p>
    </div>
  )
}

function ExtractionPanel({ project, onSessionLost, onFinished }: ScreenProps & { onFinished: () => void }) {
  const [prefix, setPrefix] = useState("")
  const [allFunctions, setAllFunctions] = useState(false)
  // The model for this extraction: undefined is the project's default for the rule map.
  const [model, setModel] = useState<ModelChoice>(undefined)
  const [analysisId, setAnalysisId] = useState<string | null>(null)
  const [status, setStatus] = useState<ExtractionStatus | null>(null)
  const [pausing, setPausing] = useState(false)
  const [running, setRunning] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const loadStatus = useCallback(() => {
    getExtractionStatus(project.id)
      .then((loaded) => {
        setStatus(loaded)
        // A running extraction (including the one that resumes at startup): it is shown.
        if (loaded.running_analysis_id) {
          setAnalysisId(loaded.running_analysis_id)
          setRunning(true)
        }
      })
      .catch(fail)
  }, [project.id, fail])
  useEffect(loadStatus, [loadStatus])

  // Only shown when the rule map goes to a remote model; recalculated as the options change.
  const [estimate, setEstimate] = useState<{ key: string; value: ExtractionEstimate } | null>(null)
  const estimateKey = `${prefix.trim()}\n${allFunctions}\n${model ?? ""}`
  useEffect(() => {
    if (running) return
    let live = true
    const timer = window.setTimeout(() => {
      getExtractionEstimate(project.id, prefix.trim(), allFunctions, model)
        .then((value) => live && setEstimate({ key: estimateKey, value }))
        .catch(() => live && setEstimate(null))
    }, 400)
    return () => {
      live = false
      window.clearTimeout(timer)
    }
  }, [project.id, prefix, allFunctions, model, estimateKey, running])
  const shown = estimate?.key === estimateKey && estimate.value.remote ? estimate.value : null

  const start = () => {
    setError(null)
    setPausing(false)
    askToNotify()
    startExtraction(project.id, prefix.trim(), allFunctions, model)
      .then((analysis) => {
        setAnalysisId(analysis.id)
        setRunning(true)
      })
      .catch((failure: unknown) => {
        // One is already running (e.g. the one that resumes at startup): that one is shown, and
        // Pause stops it.
        if (failure instanceof ApiError && failure.code === "extraction_running") loadStatus()
        else fail(failure)
      })
  }
  const pause = () => {
    if (!analysisId) return
    setPausing(true)
    cancelAnalysis(analysisId).catch(fail)
  }
  const finished = useCallback(() => {
    notifyIfAway(t("La extracción de reglas terminó"), t("Las reglas nuevas ya están en Reglas."))
    setRunning(false)
    setPausing(false)
    onFinished()
    loadStatus()
  }, [onFinished, loadStatus])
  // While it works, the status refreshes: that shows whether it is waiting for memory.
  useEffect(() => {
    if (!running) return
    const timer = setInterval(loadStatus, STATUS_REFRESH_MS)
    return () => clearInterval(timer)
  }, [running, loadStatus])
  const pending = status?.pending ?? 0
  return (
    <Section
      title={t("Extraer reglas automáticamente")}
      description={t("Scrinium busca en el código las funciones con señales de regla de negocio y {0} propone reglas a partir de ellas, citando las líneas exactas. Lo ya analizado no se repite. Todas nacen sin verificar: tú decides.", shown ? `el modelo externo ${shown.model}` : "la IA local")}
      action={
        running ? (
          <Button size="sm" variant="outline" disabled={pausing} onClick={pause}>
            <Pause data-icon="inline-start" />
            {pausing ? t("Pausando…") : t("Pausar")}
          </Button>
        ) : undefined
      }
    >
      {running && status?.waiting_for_memory && (
        <p role="status" className="text-sm text-muted-foreground">
          {t("En pausa: al equipo le queda poca memoria libre. Scrinium sigue solo cuando haya sitio, para no dejar sin memoria tus otras aplicaciones.")}
        </p>
      )}
      {analysisId && (
        <AnalysisDetail key={analysisId} analysisId={analysisId} onSessionLost={onSessionLost} onFinished={finished} />
      )}
      {!running && (
        <form
          className="grid gap-3"
          onSubmit={(event: FormEvent) => {
            event.preventDefault()
            start()
          }}
        >
          {status && status.done + pending > 0 && (
            <p className="text-sm text-muted-foreground">
              {status.done} {t("trozos de código ya analizados")}
              {pending > 0 && t(", {0} pendientes", pending)}
              {status.failed > 0 && t(", {0} que fallaron dos veces", status.failed)}.
            </p>
          )}
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">{t("Opciones avanzadas")}</summary>
            <div className="mt-2 grid max-w-md gap-1.5">
              <Label htmlFor="extract-prefix">{t("Solo esta carpeta")}</Label>
              <Input
                id="extract-prefix"
                maxLength={500}
                placeholder="src/facturacion/"
                value={prefix}
                onChange={(event) => setPrefix(event.target.value)}
              />
            </div>
            <label className="mt-3 flex items-start gap-2">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={allFunctions}
                onChange={(event) => setAllFunctions(event.target.checked)}
              />
              <span>
                {t("Analizar también las funciones sin señales de regla")}
                <span className="block text-xs text-muted-foreground">
                  {t("Mucho más lento. Por defecto solo se leen las que tienen condiciones, validaciones, cálculos, permisos o estados.")}
                </span>
              </span>
            </label>
            <div className="mt-3">
              <ModelPicker
                project={project}
                task="rule_map"
                value={model}
                onChange={setModel}
                onSessionLost={onSessionLost}
              />
            </div>
          </details>
          {shown && <RemoteEstimate estimate={shown} />}
          <Button type="submit" className="justify-self-start">
            <Sparkles data-icon="inline-start" />
            {pending > 0 ? t("Continuar la extracción ({0} pendientes)", pending) : t("Empezar la extracción")}
          </Button>
          <ErrorText>{error}</ErrorText>
        </form>
      )}
    </Section>
  )
}

const CATEGORY_OPTIONS = Object.entries(CATEGORY_LABELS).map(([value, label]) => ({ value, label }))
const CRITICALITY_OPTIONS = Object.entries(CRITICALITY_LABELS).map(([value, label]) => ({ value, label }))

function ManualRuleForm({
  project,
  onSessionLost,
  onCreated,
}: ScreenProps & { onCreated: (rule: RuleDetail) => void }) {
  const [rule, setRule] = useState<NewRule>({
    title: "",
    statement: "",
    rationale: "",
    category: "other",
    criticality: "medium",
  })
  const { error, setError, fail } = useErrors(onSessionLost)
  const submit = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    createRule(project.id, { ...rule, rationale: rule.rationale?.trim() || undefined })
      .then(onCreated)
      .catch((caught: unknown) => {
        if (caught instanceof ApiError && caught.code === "invalid_request") setError(t("Revisa el título y la descripción."))
        else fail(caught)
      })
  }
  return (
    <Section
      title={t("Crear regla manual")}
      description={t("Una regla que conoces aunque no esté en el código. Queda registrada con tu nombre del perfil.")}
    >
      <form className="grid gap-3" onSubmit={submit}>
        <div className="grid gap-1.5">
          <Label htmlFor="rule-title">{t("Título")}</Label>
          <Input
            id="rule-title"
            required
            maxLength={300}
            placeholder={t("Comisión en pagos internacionales")}
            value={rule.title}
            onChange={(event) => setRule({ ...rule, title: event.target.value })}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="rule-statement">{t("Qué debe pasar")}</Label>
          <Textarea
            id="rule-statement"
            required
            maxLength={4000}
            placeholder={t("La comisión del 2 % se aplica solo si la transacción es internacional.")}
            value={rule.statement}
            onChange={(event) => setRule({ ...rule, statement: event.target.value })}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="rule-rationale">{t("Por qué (opcional)")}</Label>
          <Textarea
            id="rule-rationale"
            maxLength={4000}
            value={rule.rationale}
            onChange={(event) => setRule({ ...rule, rationale: event.target.value })}
          />
        </div>
        <div className="flex flex-wrap gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="rule-category">{t("Tipo")}</Label>
            <Select
              id="rule-category"
              value={rule.category}
              options={CATEGORY_OPTIONS}
              onValueChange={(value) => setRule({ ...rule, category: value as RuleCategory })}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="rule-criticality">{t("Importancia")}</Label>
            <Select
              id="rule-criticality"
              value={rule.criticality}
              options={CRITICALITY_OPTIONS}
              onValueChange={(value) => setRule({ ...rule, criticality: value as NewRule["criticality"] })}
            />
          </div>
        </div>
        <ReviewError error={error} />
        <Button type="submit" className="justify-self-start" disabled={!rule.title.trim() || !rule.statement.trim()}>
          <PenLine data-icon="inline-start" />
          {t("Guardar regla")}
        </Button>
      </form>
    </Section>
  )
}

export function RuleEvidence({ project, entityId, onSessionLost }: ScreenProps & { entityId: string }) {
  const [rule, setRule] = useState<RuleDetail | null>(null)
  const { error, fail } = useErrors(onSessionLost)
  useEffect(() => {
    getRule(project.id, entityId).then(setRule).catch(fail)
  }, [project.id, entityId, fail])
  if (error) return <ErrorText>{error}</ErrorText>
  if (!rule) return <Muted>{t("Cargando…")}</Muted>
  const evidence = rule.versions[0]?.evidence ?? []
  if (evidence.length === 0) return <Muted>{t("Esta regla no tiene citas al código.")}</Muted>
  return (
    <div className="grid gap-2">
      {evidence.map((item) => (
        <EvidenceItem key={item.id} evidence={item} />
      ))}
    </div>
  )
}

/** Left edge of a rule card: green once a person confirms it, red when it is not a rule. */
function reviewEdge(status: VerificationStatus | undefined) {
  if (status && VERIFIED_STATUSES.includes(status)) return "border-l-emerald-600 dark:border-l-emerald-400"
  if (status === "rejected") return "border-l-destructive"
  return "border-l-border"
}

function TicketChip({ ticket }: { ticket: string }) {
  return (
    <span
      className="rounded-full border px-2 py-0.5 font-mono text-xs text-muted-foreground"
      title={t("Ticket nombrado por el commit que introdujo o cambió estas líneas")}
    >
      {ticket}
    </span>
  )
}

function citationRange(citation: NonNullable<RuleSummary["citation"]>) {
  const { start_line: start, end_line: end } = citation
  return end !== start ? `${start}-${end}` : `${start}`
}

/**
 * One rule as a row: status, title, what it says in one line, where it lives and how important
 * it is. Opened, it shows the code around the cited lines and three answers: it is valid, it is
 * real but badly worded (rewrite it), or it is not a rule.
 */
function RuleItem({ rule, onChanged, ...props }: ScreenProps & { rule: RuleSummary; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(rule.summary)
  const { error, setError, fail } = useErrors(props.onSessionLost)
  const version = rule.latest_version
  const status = version?.verification_status
  const citation = rule.citation
  const review = (next: VerificationStatus) => {
    if (!version) return
    setError(null)
    setVerification(version.id, next).then(onChanged).catch(fail)
  }
  const save = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    correctRule(props.project.id, rule.entity_id, text.trim())
      .then(() => {
        setEditing(false)
        onChanged()
      })
      .catch(fail)
  }
  const bodyId = `rule-body-${rule.entity_id}`
  const important = rule.criticality === "high" || rule.criticality === "critical"
  return (
    <li className={cn("min-w-0 border-l-4", reviewEdge(status))}>
      <button
        type="button"
        className="grid w-full min-w-0 grid-cols-[auto_minmax(0,1fr)] items-start gap-x-2 px-3 py-2.5 text-left hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen(!open)}
      >
        <ChevronRight
          aria-hidden="true"
          className={cn("mt-0.5 size-4 text-muted-foreground transition-transform", open && "rotate-90")}
        />
        <span className="grid min-w-0 gap-1">
          <span className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
            <span className="font-medium">{rule.name}</span>
            {status && status !== "unverified" && <VerificationStatusBadge status={status} />}
            {rule.review_reason && (
              <StatusBadge tone="attention">{REVIEW_LABELS[rule.review_reason] ?? t("Revisar")}</StatusBadge>
            )}
            {rule.tickets.map((ticket) => (
              <TicketChip key={ticket} ticket={ticket} />
            ))}
          </span>
          <span className={cn("text-sm text-muted-foreground", !open && "line-clamp-1")}>{rule.summary}</span>
          <span className="flex min-w-0 flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
            <span className={cn(important && "font-medium text-foreground")}>
              {t("Importancia")} {CRITICALITY_LABELS[rule.criticality].toLowerCase()}
            </span>
            <span>{CATEGORY_LABELS[rule.rule_category as RuleCategory] ?? rule.rule_category}</span>
            {citation && (
              <code className="min-w-0 truncate" title={`${citation.path}:${citationRange(citation)}`}>
                {citation.path.split("/").pop()}:{citationRange(citation)}
              </code>
            )}
          </span>
        </span>
      </button>
      {open && (
        <div id={bodyId} className="grid min-w-0 gap-3 px-3 pt-1 pb-4 sm:pl-9">
          {citation && (
            <CodeExcerpt
              revisionId={citation.revision_id}
              path={citation.path}
              startLine={citation.start_line}
              endLine={citation.end_line}
            />
          )}
          {version && (
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t("Revisión de {0}", rule.name)}>
              <Button
                size="sm"
                variant={status === "human_verified" ? "default" : "outline"}
                aria-pressed={status === "human_verified"}
                onClick={() => review("human_verified")}
              >
                <Check data-icon="inline-start" />
                {t("Válida")}
              </Button>
              <Button
                size="sm"
                variant={editing ? "secondary" : "outline"}
                aria-expanded={editing}
                onClick={() => {
                  setText(rule.summary)
                  setEditing(!editing)
                }}
              >
                <PenLine data-icon="inline-start" />
                {t("Mal dicha")}
              </Button>
              <Button
                size="sm"
                variant={status === "rejected" ? "destructive" : "outline"}
                aria-pressed={status === "rejected"}
                onClick={() => review("rejected")}
              >
                <X data-icon="inline-start" />
                {t("No es regla")}
              </Button>
              <RuleLink
                id={rule.entity_id}
                className="ml-auto text-xs font-medium text-muted-foreground hover:text-foreground hover:underline"
              >
                {rule.evidence_count > 1 ? t("{0} citas · ", rule.evidence_count) : ""}{t("Ver la regla completa")}
              </RuleLink>
            </div>
          )}
          {editing && (
            <form className="grid gap-2" onSubmit={save}>
              <Label htmlFor={`fix-${rule.entity_id}`}>{t("Cómo debería decirse")}</Label>
              <Textarea
                id={`fix-${rule.entity_id}`}
                required
                maxLength={4000}
                value={text}
                onChange={(event) => setText(event.target.value)}
              />
              <p className="text-xs text-muted-foreground">
                {t("Se guarda como una versión nueva con tu nombre del perfil, con las mismas citas al código, y queda confirmada.")}
              </p>
              <div className="flex gap-2">
                <Button type="submit" size="sm" disabled={!text.trim() || text.trim() === rule.summary}>
                  {t("Guardar")}
                </Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
                  {t("Cancelar")}
                </Button>
              </div>
            </form>
          )}
          <ReviewError error={error} />
        </div>
      )}
    </li>
  )
}

type Grouping = "file" | "criticality" | "none"
const GROUPING_OPTIONS = [
  { value: "file", label: t("Agrupar por archivo") },
  { value: "criticality", label: t("Agrupar por importancia") },
  { value: "none", label: t("Sin agrupar") },
]
const CRITICALITY_RANK: Record<RuleSummary["criticality"], number> = { critical: 0, high: 1, medium: 2, low: 3 }
const GROUPING_KEY = "scrinium.rules.grouping"
// Up to this many rules, every group starts open; past it, they start closed.
const OPEN_GROUPS_UP_TO = 40

const FILTERS: Filter[] = ["all", "review", "unverified", "verified", "rejected"]

function readFilter(value: string | null): Filter {
  return FILTERS.find((filter) => filter === value) ?? "all"
}

function readGrouping(): Grouping {
  try {
    const saved = localStorage.getItem(GROUPING_KEY)
    if (saved === "file" || saved === "criticality" || saved === "none") return saved
  } catch {
    // Without storage the default is used.
  }
  return "file"
}

/** Most important first, then by name: what matters most is read first in every group. */
function byImportance(a: RuleSummary, b: RuleSummary) {
  return CRITICALITY_RANK[a.criticality] - CRITICALITY_RANK[b.criticality] || a.name.localeCompare(b.name, "es")
}

function groupRules(rules: RuleSummary[], grouping: Grouping): { key: string; label: string; rules: RuleSummary[] }[] {
  const sorted = [...rules].sort(byImportance)
  if (grouping === "none") return [{ key: "all", label: "", rules: sorted }]
  const groups = new Map<string, RuleSummary[]>()
  for (const rule of sorted) {
    const key =
      grouping === "criticality"
        ? rule.criticality
        : (rule.citation?.path ?? "")
    groups.set(key, [...(groups.get(key) ?? []), rule])
  }
  const label = (key: string) =>
    grouping === "criticality"
      ? t("Importancia {0}", CRITICALITY_LABELS[key as RuleSummary["criticality"]].toLowerCase())
      : key || t("Sin cita al código")
  return [...groups.entries()]
    .map(([key, rules]) => ({ key, label: label(key), rules }))
    .sort((a, b) =>
      grouping === "criticality"
        ? CRITICALITY_RANK[a.key as RuleSummary["criticality"]] - CRITICALITY_RANK[b.key as RuleSummary["criticality"]]
        : (a.key === "" ? 1 : 0) - (b.key === "" ? 1 : 0) || a.key.localeCompare(b.key, "es"),
    )
}

function RuleGroup({
  label,
  rules,
  startOpen,
  code,
  ...props
}: ScreenProps & {
  label: string
  rules: RuleSummary[]
  startOpen: boolean
  code: boolean
  onChanged: () => void
}) {
  const [open, setOpen] = useState(startOpen)
  const important = rules.filter((rule) => CRITICALITY_RANK[rule.criticality] <= 1).length
  return (
    <section className="overflow-hidden rounded-xl border bg-card">
      <h2>
        <button
          type="button"
          className="flex w-full items-center gap-2 px-3 py-2.5 text-left hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:outline-none"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          <ChevronRight
            aria-hidden="true"
            className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-90")}
          />
          <span className={cn("min-w-0 truncate text-sm font-medium", code && "font-mono")}>{label}</span>
          <span className="shrink-0 text-xs text-muted-foreground">
            {plural(rules.length, t("regla"), t("reglas"))}
            {important > 0 && t(" · {0} de importancia alta", important)}
          </span>
        </button>
      </h2>
      {open && (
        <ul className="divide-y border-t">
          {rules.map((rule) => (
            <RuleItem key={rule.entity_id} {...props} rule={rule} />
          ))}
        </ul>
      )}
    </section>
  )
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`

/** Reviewed rules not yet in the repository's .scrinium/rules/ (ADR-012): CI cannot see them. */
function UnexportedNotice({
  projectId,
  projectName,
  refresh,
}: {
  projectId: string
  projectName: string
  refresh: unknown
}) {
  const [sources, setSources] = useState<RulebookSource[]>([])
  useEffect(() => {
    getRulebook(projectId)
      .then(setSources)
      .catch(() => setSources([]))
  }, [projectId, refresh])
  const pending = sources.filter((s) => s.available && (s.unexported > 0 || s.extra > 0))
  if (pending.length === 0) return null
  const several = sources.length > 1
  return (
    <div role="status" className="grid gap-1 rounded-md border px-3 py-2 text-sm">
      {pending.map((source) => (
        <p key={source.source_id}>
          {source.unexported > 0 && (
            <>
              {source.unexported === 1
                ? t("1 regla revisada no está exportada")
                : t("{0} reglas revisadas no están exportadas", source.unexported)}
            </>
          )}
          {source.unexported > 0 && source.extra > 0 && " y "}
          {source.extra > 0 &&
            (source.extra === 1
              ? t("1 archivo de regla sobra")
              : t("{0} archivos de reglas sobran", source.extra))}
          {several && t(" en {0}", source.name)}{t(". El check de CI no las ve hasta que ejecutes")}{" "}
          <code className="font-mono text-xs">
            scrinium rules export --project "{projectName}"{several && ` --source "${source.name}"`}
          </code>{" "}
          {t("y hagas commit de")} <code className="font-mono text-xs">.scrinium/rules/</code>.
        </p>
      ))}
    </div>
  )
}

function RuleList(props: ScreenProps) {
  const { project, onSessionLost } = props
  const [rules, setRules] = useState<RuleSummary[] | null>(null)
  // Filter and search live in the URL: back from a rule returns to the same list.
  const [params, setParams] = useSearchParams()
  const filter = readFilter(params.get("filtro"))
  const query = params.get("q") ?? ""
  const setListParam = (name: "filtro" | "q", value: string, empty: string) =>
    setParams(
      (current) => {
        const next = new URLSearchParams(current)
        if (value === empty) next.delete(name)
        else next.set(name, value)
        return next
      },
      { replace: true },
    )
  const setFilter = (value: Filter) => setListParam("filtro", value, "all")
  const setQuery = (value: string) => setListParam("q", value, "")
  const [grouping, setGrouping] = useState<Grouping>(readGrouping)
  const [panel, setPanel] = useState<"extract" | "manual" | null>(null)
  const { error, fail } = useErrors(onSessionLost)
  const load = useCallback(() => {
    listRules(project.id, { q: query.trim() || undefined })
      .then(setRules)
      .catch(fail)
  }, [project.id, query, fail])
  useEffect(load, [load])

  const shown = rules?.filter((rule) => matches(rule, filter)) ?? []
  const count = (value: Filter) => rules?.filter((rule) => matches(rule, value)).length ?? 0
  const toggle = (next: "extract" | "manual") => setPanel(panel === next ? null : next)
  const group = (value: string) => {
    setGrouping(value as Grouping)
    try {
      localStorage.setItem(GROUPING_KEY, value)
    } catch {
      // Only a convenience: without storage it is not remembered.
    }
  }
  const groups = groupRules(shown, grouping)

  return (
    <>
      <PageHeader
        title={t("Reglas")}
        description={t("El libro de reglas del proyecto: lo que el sistema debe hacer, con citas al código. La IA propone y tú decides.")}
        actions={
          <>
            <Button variant={panel === "manual" ? "secondary" : "outline"} onClick={() => toggle("manual")}>
              <PenLine data-icon="inline-start" />
              {t("Crear regla manual")}
            </Button>
            <Button variant={panel === "extract" ? "secondary" : "default"} onClick={() => toggle("extract")}>
              <Sparkles data-icon="inline-start" />
              {t("Extraer reglas automáticamente")}
            </Button>
          </>
        }
      />
      {panel === "extract" && <ExtractionPanel {...props} onFinished={load} />}
      {panel === "manual" && (
        <ManualRuleForm
          {...props}
          onCreated={() => {
            setPanel(null)
            load()
          }}
        />
      )}
      <UnexportedNotice projectId={project.id} projectName={project.name} refresh={rules} />
      <div className="grid gap-3">
        <Tabs value={filter} onValueChange={(value) => setFilter(value as Filter)}>
          <TabsList className="flex-wrap">
            <TabsTrigger value="all">{t("Todas (")}{count("all")})</TabsTrigger>
            <TabsTrigger value="review">{t("Por revisar (")}{count("review")})</TabsTrigger>
            <TabsTrigger value="unverified">{t("Sin verificar (")}{count("unverified")})</TabsTrigger>
            <TabsTrigger value="verified">{t("Verificadas (")}{count("verified")})</TabsTrigger>
            <TabsTrigger value="rejected">{t("Descartadas (")}{count("rejected")})</TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="flex flex-wrap items-center gap-2">
          <Input
            aria-label={t("Buscar reglas")}
            maxLength={200}
            placeholder={t("Buscar por texto o ticket…")}
            className="max-w-72 min-w-0 flex-1"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <Select aria-label={t("Agrupar reglas")} value={grouping} options={GROUPING_OPTIONS} onValueChange={group} />
        </div>
      </div>
      <ErrorText>{error}</ErrorText>
      {rules === null ? (
        <Muted>{t("Cargando…")}</Muted>
      ) : shown.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title={rules.length === 0 ? t("Todavía no hay reglas") : t("Ninguna regla en este filtro")}
          action={
            rules.length === 0 && (
              <Button onClick={() => setPanel("extract")}>
                <Sparkles data-icon="inline-start" />
                {t("Extraer reglas automáticamente")}
              </Button>
            )
          }
        >
          {rules.length === 0 && t("La IA puede proponerlas a partir del código, o puedes escribirlas tú.")}
        </EmptyState>
      ) : (
        <div className="grid gap-3">
          {groups.map((item) =>
            grouping === "none" ? (
              <ul key={item.key} className="divide-y overflow-hidden rounded-xl border bg-card">
                {item.rules.map((rule) => (
                  <RuleItem key={rule.entity_id} {...props} rule={rule} onChanged={load} />
                ))}
              </ul>
            ) : (
              <RuleGroup
                // A new filter or search reopens the groups by the new count.
                key={`${grouping}:${item.key}:${filter}:${query}`}
                {...props}
                label={item.label}
                code={grouping === "file" && item.key !== ""}
                rules={item.rules}
                startOpen={shown.length <= OPEN_GROUPS_UP_TO || groups.length === 1}
                onChanged={load}
              />
            ),
          )}
        </div>
      )}
    </>
  )
}

/** Two decisions (phase 4.5): a person confirms or rejects. "Backed by the code" is not a
 * decision: it is what the checked citations already say (the evidence profile). */
const REVIEW_ACTIONS: { status: VerificationStatus; label: string }[] = [
  { status: "human_verified", label: t("Confirmar") },
  { status: "rejected", label: t("Rechazar") },
]
const REVIEWED: VerificationStatus[] = ["human_verified", "rejected"]

/** A list of functions; each one opens its code below. */
function FunctionList({ label, items }: { label: string; items: FunctionRef[] }) {
  const [open, setOpen] = useState<string | null>(null)
  if (items.length === 0) return null
  const shown = items.find((item) => item.symbol_id === open)
  return (
    <div className="grid min-w-0 gap-1.5">
      <div className="flex flex-wrap items-center gap-1.5">
        <Eyebrow>{label}</Eyebrow>
        {items.map((item) => (
          <Button
            key={item.symbol_id}
            size="xs"
            variant={open === item.symbol_id ? "secondary" : "outline"}
            aria-expanded={open === item.symbol_id}
            title={`${item.path}:${item.start_line}`}
            onClick={() => setOpen(open === item.symbol_id ? null : item.symbol_id)}
          >
            <code>{item.name}</code>
          </Button>
        ))}
      </div>
      {shown && (
        <div className="grid gap-1">
          <code className="text-xs text-muted-foreground">
            {shown.path}:{shown.start_line}-{shown.end_line}
          </code>
          <CodeExcerpt
            revisionId={shown.revision_id}
            path={shown.path}
            startLine={0}
            endLine={0}
            window={[shown.start_line, shown.end_line]}
            className="max-h-80 overflow-y-auto"
          />
        </div>
      )}
    </div>
  )
}

/** The function's place in the code: what it calls and what calls it. */
function CallLists({ projectId, symbolId }: { projectId: string; symbolId: string }) {
  const [found, setFound] = useState<{ calls: FunctionRef[]; called_by: FunctionRef[] } | null>(null)
  useEffect(() => {
    const live = { current: true }
    getNeighbours(projectId, symbolId)
      .then((result) => live.current && setFound(result))
      .catch(() => live.current && setFound(null))
    return () => {
      live.current = false
    }
  }, [projectId, symbolId])
  if (!found) return null
  return (
    <div className="grid gap-2">
      <FunctionList label={t("Llama a")} items={found.calls} />
      <FunctionList label={t("Llamada por")} items={found.called_by} />
    </div>
  )
}

/** A citation with its context; "Ver función completa" opens the whole function that holds it. */
function CitedCode({ evidence, projectId }: { evidence: Evidence; projectId: string }) {
  const [whole, setWhole] = useState(false)
  const start = evidence.locator.start_line ?? 1
  const end = evidence.locator.end_line ?? start
  const symbol = evidence.symbol
  // Only worth offering when the function is longer than what the context already shows.
  const longer = symbol && (symbol.start_line < start - CONTEXT_LINES || symbol.end_line > end + CONTEXT_LINES)
  return (
    <div className="grid min-w-0 gap-1.5">
      {symbol && (
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>
            {t("En")} <code>{symbol.name}</code>
            {symbol.nested_in && (
              <>
                {" "}
                {t("(dentro de")} <code>{symbol.nested_in}</code>)
              </>
            )}{" "}
            {t("· líneas")} {symbol.start_line}-{symbol.end_line}
          </span>
          {longer && (
            <Button size="xs" variant="ghost" aria-expanded={whole} onClick={() => setWhole(!whole)}>
              {whole ? t("Ver solo la regla") : t("Ver función completa")}
            </Button>
          )}
        </div>
      )}
      <CodeExcerpt
        revisionId={evidence.revision_id ?? ""}
        path={evidence.locator.path ?? ""}
        startLine={start}
        endLine={end}
        window={whole && symbol ? [symbol.start_line, symbol.end_line] : undefined}
        className={whole ? "max-h-[32rem] overflow-y-auto" : undefined}
      />
      {/* The call graph knows top-level functions; a nested one's calls are not separated yet. */}
      {symbol && !symbol.nested_in && <CallLists projectId={projectId} symbolId={symbol.id} />}
    </div>
  )
}

function VersionBlock({
  projectId,
  version,
  latest,
  onChanged,
  onSessionLost,
}: {
  projectId: string
  version: RuleVersion
  latest: boolean
  onChanged: () => void
  onSessionLost: (error: unknown) => void
}) {
  const { error, setError, fail } = useErrors(onSessionLost)
  const [note, setNote] = useState("")
  const review = (status: VerificationStatus) => {
    setError(null)
    setVerification(version.id, status, note)
      .then(() => {
        setNote("")
        onChanged()
      })
      .catch(fail)
  }
  const content = version.content
  return (
    <Section
      title={
        <span className="flex flex-wrap items-center gap-2">
          {t("Versión")} {version.version_number}
          <VerificationStatusBadge status={version.verification_status} />
          {latest && <StatusBadge tone="no_evidence">{t("Vigente")}</StatusBadge>}
        </span>
      }
      description={
        <>
          {ORIGIN_LABELS[version.origin]}
          {version.model_name && ` (${version.model_name})`} · {new Date(version.created_at).toLocaleString()}
          {version.reviewed_by && t(" · Revisada por {0}", version.reviewed_by)}
        </>
      }
    >
      {content && (
        <div className="grid gap-3 text-sm">
          <p className="text-base whitespace-pre-wrap">{content.statement}</p>
          {content.rationale && <Muted>{content.rationale}</Muted>}
          {content.trigger_conditions.length > 0 && (
            <dl className="grid gap-2 sm:grid-cols-2">
              <div className="grid content-start gap-1">
                <dt>
                  <Eyebrow>{t("Cuándo")}</Eyebrow>
                </dt>
                {content.trigger_conditions.map((item) => (
                  <dd key={item.description}>{item.description}</dd>
                ))}
              </div>
              <div className="grid content-start gap-1">
                <dt>
                  <Eyebrow>{t("Entonces")}</Eyebrow>
                </dt>
                {content.expected_behavior.map((item) => (
                  <dd key={item.description}>{item.description}</dd>
                ))}
              </div>
            </dl>
          )}
          {content.exceptions.length > 0 && (
            <div className="grid gap-1">
              <Eyebrow>{t("Excepciones")}</Eyebrow>
              <ul className="list-disc pl-5">
                {content.exceptions.map((item) => (
                  <li key={item.condition}>
                    {item.condition}: {item.behavior}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
      <div className="flex flex-wrap gap-1">
        {Object.entries(version.profile)
          .filter(([, count]) => count > 0)
          .map(([kind, count]) => (
            <StatusBadge key={kind} tone={kind === "contradicted" ? "discarded" : "no_evidence"}>
              {PROFILE_LABELS[kind as keyof RuleVersion["profile"]]}: {count}
            </StatusBadge>
          ))}
      </div>
      {latest &&
        version.evidence
          .filter((item) => item.revision_id && item.locator.path && item.locator.start_line && item.type !== "test")
          .map((item) => <CitedCode key={`code-${item.id}`} evidence={item} projectId={projectId} />)}
      <div className="grid gap-2">
        <Eyebrow>{t("Evidencia")}</Eyebrow>
        {version.evidence.length === 0 ? (
          <Muted>{t("No se encontró evidencia en el código.")}</Muted>
        ) : (
          version.evidence.map((evidence) => <EvidenceItem key={evidence.id} evidence={evidence} />)
        )}
      </div>
      {version.findings.length > 0 && (
        <div className="grid gap-1">
          <Eyebrow>{t("Hallazgos sobre esta versión")}</Eyebrow>
          <ul className="grid gap-1 text-sm">
            {version.findings.map((finding) => (
              <li key={finding.id}>
                <Link className="hover:underline" to={`/impacto/${finding.analysis_id}`}>
                  {finding.title}
                </Link>{" "}
                <span className="text-xs text-muted-foreground">({ASSESSMENT_LABELS[finding.assessment_status]})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {latest && (
        <div className="grid gap-2 border-t pt-4">
          <Eyebrow>{t("Verificación")}</Eyebrow>
          <div className="flex flex-wrap gap-1">
            {REVIEW_ACTIONS.map(({ status, label }) => (
              <Button
                key={status}
                size="sm"
                variant={status === "human_verified" ? "default" : status === "rejected" ? "destructive" : "outline"}
                disabled={version.verification_status === status}
                onClick={() => review(status)}
              >
                {label}
              </Button>
            ))}
          </div>
          <Input
            aria-label={t("Motivo de la revisión")}
            maxLength={2000}
            placeholder={t("Motivo (opcional; al rechazar, viaja con la regla al repositorio)")}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
          {REVIEWED.includes(version.verification_status) && (
            <Button size="xs" variant="ghost" className="justify-self-start" onClick={() => review("unverified")}>
              {t("Deshacer la revisión")}
            </Button>
          )}
          <p className="text-xs text-muted-foreground">
            {t("Confirmar y rechazar registran tu nombre del perfil. Que el código la respalde lo dicen sus citas comprobadas (arriba), no un botón.")}
          </p>
          <ReviewError error={error} />
        </div>
      )}
    </Section>
  )
}

/** A relationship in words, from this rule's side. */
function relationLabel(relation: Relationship): string {
  const [kind, detail] = (relation.basis ?? "").split(":", 2)
  if (kind === "duplicate") return t("Misma decisión, en otro sitio:")
  if (kind === "same_function") return t("Junto a ella en {0}:", detail)
  if (kind === "constant") return t("Comparte {0} con:", detail)
  if (kind === "constraint") return t("Misma restricción ({0}) en otro sitio:", detail)
  if (kind === "permission") return t("Exige el mismo permiso «{0}» que:", detail)
  if (kind === "same_data") return t("Sobre la misma tabla ({0}):", detail)
  if (kind === "calls") {
    const hops = detail === "1" ? "directamente" : t("a través de {0} función(es)", Number(detail) - 1)
    return relation.direction === "out" ? t("Su código llama, {0}, al de:", hops) : t("La usa, {0}:", hops)
  }
  if (relation.relation_type === "depends_on") return relation.direction === "out" ? t("Depende de:") : t("Dependen de ella:")
  if (relation.relation_type === "contradicts") return t("Contradice:")
  return t("Relacionada con:")
}

function ImpactName({ id, name, entityType }: { id: string; name: string; entityType: string }) {
  return entityType === "business_rule" ? (
    <RuleLink id={id} className="font-medium hover:underline">
      {name}
    </RuleLink>
  ) : (
    <span className="font-medium">{name}</span>
  )
}

/** N7: what may break if this rule changes. The relationships, read backwards; no AI. */
function RuleImpactSection({ impact }: { impact: RuleImpact }) {
  const nothing =
    impact.same_decision.length === 0 && impact.dependents.length === 0 && impact.flows.length === 0
  return (
    <Section
      title={t("Si cambia esta regla")}
      description={t("Lo que depende de ella según el código: las reglas cuyo código llama al suyo, los procesos que pasan por ellas y sus tickets. Sin IA.")}
    >
      <div className="grid gap-4 text-sm">
        {impact.tickets.length > 0 && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-muted-foreground">{t("Tickets que la nombran:")}</span>
            {impact.tickets.map((ticket) => (
              <TicketChip key={ticket} ticket={ticket} />
            ))}
          </div>
        )}
        {nothing && <Muted>{t("Según el código, ninguna otra regla ni proceso depende de esta.")}</Muted>}
        {impact.same_decision.length > 0 && (
          <div className="grid gap-1">
            <Eyebrow>{t("La misma decisión en otro sitio: cambia con ella")}</Eyebrow>
            <ul className="grid gap-1">
              {impact.same_decision.map((other) => (
                <li key={other.id} className="flex flex-wrap items-baseline gap-x-2">
                  <ImpactName id={other.id} name={other.name} entityType={other.entity_type} />
                  {other.tickets.map((ticket) => (
                    <TicketChip key={ticket} ticket={ticket} />
                  ))}
                </li>
              ))}
            </ul>
          </div>
        )}
        {impact.dependents.length > 0 && (
          <div className="grid gap-1">
            <Eyebrow>{t("Dependen de ella (")}{impact.dependents.length}{impact.truncated ? "+" : ""})</Eyebrow>
            <ul className="grid gap-1">
              {impact.dependents.map((dependent) => (
                <li key={dependent.id} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <ImpactName id={dependent.id} name={dependent.name} entityType={dependent.entity_type} />
                  {dependent.criticality && (
                    <span
                      className={cn(
                        "text-xs",
                        dependent.criticality === "high" || dependent.criticality === "critical"
                          ? "font-medium text-foreground"
                          : "text-muted-foreground",
                      )}
                    >
                      {t("importancia")} {CRITICALITY_LABELS[dependent.criticality].toLowerCase()}
                    </span>
                  )}
                  {dependent.via && (
                    <span className="text-xs text-muted-foreground">
                      {t("a través de")}{" "}
                      <RuleLink id={dependent.via.id} className="hover:underline">
                        {dependent.via.name}
                      </RuleLink>
                    </span>
                  )}
                  {dependent.tickets.map((ticket) => (
                    <TicketChip key={ticket} ticket={ticket} />
                  ))}
                </li>
              ))}
            </ul>
            {impact.truncated && (
              <Muted>{t("Hay más; se muestran las más cercanas (hasta")} {impact.max_depth} {t("pasos).")}</Muted>
            )}
          </div>
        )}
        {impact.flows.length > 0 && (
          <div className="grid gap-1">
            <Eyebrow>{t("Procesos afectados")}</Eyebrow>
            <ul className="grid gap-1">
              {impact.flows.map((flow) => (
                <li key={flow.id} className="flex flex-wrap items-baseline gap-x-2">
                  <span className="font-medium">{flow.name}</span>
                  <span className="text-xs text-muted-foreground">{t("por")} {flow.through.join(", ")}</span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </Section>
  )
}

function shownAsImpact(relation: Relationship): boolean {
  const kind = (relation.basis ?? "").split(":", 1)[0]
  return (relation.relation_type === "depends_on" && relation.direction === "in") || kind === "duplicate" || kind === "constraint"
}

const isPending = (rule: RuleDetail | null) =>
  rule?.versions[0]?.content?.statement.startsWith(PENDING_STATEMENT_PREFIX) ?? false

function RuleView({ project, onSessionLost, entityId }: ScreenProps & { entityId: string }) {
  const [rule, setRule] = useState<RuleDetail | null>(null)
  const [writing, setWriting] = useState<"idle" | "writing" | "failed">("idle")
  const { error, fail } = useErrors(onSessionLost)
  const write = useCallback(() => {
    setWriting("writing")
    writeStatement(project.id, entityId)
      .then((updated) => {
        setRule(updated)
        // If it still has no statement, it is not retried automatically: avoids a request loop.
        setWriting(isPending(updated) ? "failed" : "idle")
      })
      .catch(() => setWriting("failed"))
  }, [project.id, entityId])
  const load = useCallback(() => {
    getRule(project.id, entityId)
      .then((loaded) => {
        setRule(loaded)
        // Opened without a statement: it is written now, without waiting for background extraction.
        if (isPending(loaded)) write()
      })
      .catch(fail)
  }, [project.id, entityId, fail, write])
  useEffect(load, [load])
  const pending = isPending(rule)
  const back = useBackTarget()
  if (error) return <ErrorText>{error}</ErrorText>
  if (rule === null) return <Muted>{t("Cargando…")}</Muted>
  // What depends on this rule, and the same decision elsewhere, are under "Si cambia esta regla".
  const relationships = rule.relationships.filter((relation) => !shownAsImpact(relation))
  return (
    <>
      <Link to={back.to} className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" />
        {back.label}
      </Link>
      <PageHeader
        title={rule.name}
        description={
          <>
            {CATEGORY_LABELS[rule.rule_category as RuleCategory] ?? rule.rule_category} {t("· importancia")}{" "}
            {CRITICALITY_LABELS[rule.criticality].toLowerCase()}
            {!VERIFIED_STATUSES.includes(rule.versions[0]?.verification_status ?? "unverified") && " (propuesta)"} ·{" "}
            <code className="text-xs">{rule.canonical_key}</code>
          </>
        }
      />
      {rule.review_reason && (
        <StatusBadge tone="attention">{REVIEW_LABELS[rule.review_reason] ?? t("Revisar")}</StatusBadge>
      )}
      {pending && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <StatusBadge tone="attention">{t("Sin enunciado")}</StatusBadge>
          {writing === "writing" && <Muted>{t("Redactando el enunciado…")}</Muted>}
          {writing === "failed" && (
            <>
              <Muted>{t("No se pudo redactar ahora; se hará en segundo plano.")}</Muted>
              <Button size="sm" variant="outline" onClick={write}>
                {t("Reintentar")}
              </Button>
            </>
          )}
        </div>
      )}
      {relationships.length > 0 && (
        <Section
          title={t("Relaciones")}
          description={t("Lo que el código dice de cómo se relaciona esta regla con otras. Sale del análisis del código, sin IA.")}
        >
          <ul className="grid gap-2 text-sm">
            {relationships.map((relation) => (
              <li key={relation.id} className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                <span className="text-muted-foreground">{relationLabel(relation)}</span>
                {relation.other.entity_type === "business_rule" ? (
                  <RuleLink id={relation.other.id} className="font-medium hover:underline">
                    {relation.other.name}
                  </RuleLink>
                ) : (
                  <span className="font-medium">{relation.other.name}</span>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
      <RuleImpactSection impact={rule.impact} />
      <RuleHistory projectId={project.id} entityId={entityId} onSessionLost={onSessionLost} />
      {rule.versions.map((version, index) => (
        <VersionBlock
          key={version.id}
          projectId={project.id}
          version={version}
          latest={index === 0}
          onChanged={load}
          onSessionLost={onSessionLost}
        />
      ))}
    </>
  )
}

export function RulesScreen(props: ScreenProps) {
  const { entityId } = useParams()
  return entityId ? <RuleView {...props} entityId={entityId} /> : <RuleList {...props} />
}
