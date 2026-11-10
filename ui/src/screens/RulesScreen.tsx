import { ArrowLeft, BookOpen, Check, ChevronDown, ListChecks, Pause, PenLine, Sparkles, X } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useState } from "react"
import { Link, useParams } from "react-router"
import { cn } from "cn"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { StatusBadge, VerificationStatusBadge } from "@/components/status"
import { VERIFIED_STATUSES } from "@/lib/status"
import { Button } from "@/components/ui/button"
import { Input, Label, Textarea } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  ApiError,
  type ExtractionStatus,
  type NewRule,
  type RuleCategory,
  type RuleDetail,
  type RuleSummary,
  type RuleVersion,
  type VerificationStatus,
  cancelAnalysis,
  createRule,
  getExtractionStatus,
  getRule,
  PENDING_STATEMENT_PREFIX,
  writeStatement,
  listRules,
  setVerification,
  startConsistencyReview,
  startExtraction,
} from "@/lib/api"
import { ASSESSMENT_LABELS, errorMessage } from "@/lib/labels"
import { useErrors } from "@/screens/hooks"
import { AnalysisDetail, ErrorText, EvidenceItem, Eyebrow, Muted, type ScreenProps } from "@/screens/shared"

const CATEGORY_LABELS: Record<RuleCategory, string> = {
  invariant: "Invariante",
  validation: "Validación",
  authorization: "Permisos",
  calculation: "Cálculo",
  state_transition: "Cambio de estado",
  workflow: "Proceso",
  integration_contract: "Integración",
  performance_constraint: "Rendimiento",
  security_constraint: "Seguridad",
  other: "Otra",
}
const CRITICALITY_LABELS: Record<RuleSummary["criticality"], string> = {
  low: "Baja",
  medium: "Media",
  high: "Alta",
  critical: "Crítica",
}
const ORIGIN_LABELS: Record<RuleVersion["origin"], string> = {
  human: "Escrita por una persona",
  source_extraction: "Propuesta por la IA a partir del código",
  derived_analysis: "Propuesta por un análisis",
  import: "Importada",
}
const PROFILE_LABELS: Record<keyof RuleVersion["profile"], string> = {
  requested: "Pedida",
  documented: "Documentada",
  implemented: "Implementada",
  tested: "Probada",
  contradicted: "Contradicha",
}
const REVIEW_LABELS: Record<string, string> = {
  source_deleted: "Revisar: se borró una fuente que la respaldaba",
  code_changed: "Revisar: cambió el código que la implementa",
  code_removed: "Revisar: se eliminó el código que la implementa",
  code_contradicts: "El código ya no la cumple",
  possible_duplicate: "Revisar: posible duplicado",
  contradiction: "Revisar: contradice otra regla",
  ambiguous: "Revisar: regla ambigua",
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
            Ir a Ajustes
          </Link>
        </>
      )}
    </ErrorText>
  )
}

const STATUS_REFRESH_MS = 5000

function ExtractionPanel({ project, onSessionLost, onFinished }: ScreenProps & { onFinished: () => void }) {
  const [prefix, setPrefix] = useState("")
  const [allFunctions, setAllFunctions] = useState(false)
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

  const start = () => {
    setError(null)
    setPausing(false)
    startExtraction(project.id, prefix.trim(), allFunctions)
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
      title="Extraer reglas automáticamente"
      description="Scrinium busca en el código las funciones con señales de regla de negocio y la IA local propone reglas a partir de ellas, citando las líneas exactas. Lo ya analizado no se repite. Todas nacen sin verificar: tú decides."
      action={
        running ? (
          <Button size="sm" variant="outline" disabled={pausing} onClick={pause}>
            <Pause data-icon="inline-start" />
            {pausing ? "Pausando…" : "Pausar"}
          </Button>
        ) : undefined
      }
    >
      {running && status?.waiting_for_memory && (
        <p role="status" className="text-sm text-muted-foreground">
          En pausa: al equipo le queda poca memoria libre. Scrinium sigue solo cuando haya sitio,
          para no dejar sin memoria tus otras aplicaciones.
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
              {status.done} trozos de código ya analizados
              {pending > 0 && `, ${pending} pendientes`}
              {status.failed > 0 && `, ${status.failed} que fallaron dos veces`}.
            </p>
          )}
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">Opciones avanzadas</summary>
            <div className="mt-2 grid max-w-md gap-1.5">
              <Label htmlFor="extract-prefix">Solo esta carpeta</Label>
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
                Analizar también las funciones sin señales de regla
                <span className="block text-xs text-muted-foreground">
                  Mucho más lento. Por defecto solo se leen las que tienen condiciones, validaciones,
                  cálculos, permisos o estados.
                </span>
              </span>
            </label>
          </details>
          <Button type="submit" className="justify-self-start">
            <Sparkles data-icon="inline-start" />
            {pending > 0 ? `Continuar la extracción (${pending} pendientes)` : "Empezar la extracción"}
          </Button>
          <ErrorText>{error}</ErrorText>
        </form>
      )}
    </Section>
  )
}

function ConsistencyPanel({ project, onSessionLost, onFinished }: ScreenProps & { onFinished: () => void }) {
  const [analysisId, setAnalysisId] = useState<string | null>(null)
  const { error, setError, fail } = useErrors(onSessionLost)
  const start = () => {
    setError(null)
    startConsistencyReview(project.id)
      .then((analysis) => setAnalysisId(analysis.id))
      .catch(fail)
  }
  return (
    <Section
      title="Revisar consistencia"
      description="Busca reglas duplicadas, reglas que se contradicen y reglas ambiguas, y liga cada regla con los tests que llaman a su código. Todo queda como hallazgo para que decidas."
    >
      {analysisId ? (
        <AnalysisDetail analysisId={analysisId} onSessionLost={onSessionLost} onFinished={onFinished} />
      ) : (
        <div className="grid gap-2">
          <Button className="justify-self-start" onClick={start}>
            <ListChecks data-icon="inline-start" />
            Empezar la revisión
          </Button>
          <ErrorText>{error}</ErrorText>
        </div>
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
        if (caught instanceof ApiError && caught.code === "invalid_request") setError("Revisa el título y la descripción.")
        else fail(caught)
      })
  }
  return (
    <Section
      title="Crear regla manual"
      description="Una regla que conoces aunque no esté en el código. Queda registrada con tu nombre del perfil."
    >
      <form className="grid gap-3" onSubmit={submit}>
        <div className="grid gap-1.5">
          <Label htmlFor="rule-title">Título</Label>
          <Input
            id="rule-title"
            required
            maxLength={300}
            placeholder="Comisión en pagos internacionales"
            value={rule.title}
            onChange={(event) => setRule({ ...rule, title: event.target.value })}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="rule-statement">Qué debe pasar</Label>
          <Textarea
            id="rule-statement"
            required
            maxLength={4000}
            placeholder="La comisión del 2 % se aplica solo si la transacción es internacional."
            value={rule.statement}
            onChange={(event) => setRule({ ...rule, statement: event.target.value })}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="rule-rationale">Por qué (opcional)</Label>
          <Textarea
            id="rule-rationale"
            maxLength={4000}
            value={rule.rationale}
            onChange={(event) => setRule({ ...rule, rationale: event.target.value })}
          />
        </div>
        <div className="flex flex-wrap gap-3">
          <div className="grid gap-1.5">
            <Label htmlFor="rule-category">Tipo</Label>
            <Select
              id="rule-category"
              value={rule.category}
              options={CATEGORY_OPTIONS}
              onValueChange={(value) => setRule({ ...rule, category: value as RuleCategory })}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="rule-criticality">Importancia</Label>
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
          Guardar regla
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
  if (!rule) return <Muted>Cargando…</Muted>
  const evidence = rule.versions[0]?.evidence ?? []
  if (evidence.length === 0) return <Muted>Esta regla no tiene citas al código.</Muted>
  return (
    <div className="grid gap-2">
      {evidence.map((item) => (
        <EvidenceItem key={item.id} evidence={item} />
      ))}
    </div>
  )
}

function RuleItem({ rule, onChanged, ...props }: ScreenProps & { rule: RuleSummary; onChanged: () => void }) {
  const [open, setOpen] = useState(false)
  const { error, setError, fail } = useErrors(props.onSessionLost)
  const version = rule.latest_version
  const review = (status: VerificationStatus) => {
    if (!version) return
    setError(null)
    setVerification(version.id, status).then(onChanged).catch(fail)
  }
  return (
    <li className="grid gap-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="grid gap-1.5">
          <div className="flex flex-wrap items-center gap-2">
            {version && <VerificationStatusBadge status={version.verification_status} />}
            {rule.review_reason && (
              <StatusBadge tone="attention">{REVIEW_LABELS[rule.review_reason] ?? "Revisar"}</StatusBadge>
            )}
            <span className="text-xs text-muted-foreground">
              {CATEGORY_LABELS[rule.rule_category as RuleCategory] ?? rule.rule_category} · importancia{" "}
              {CRITICALITY_LABELS[rule.criticality].toLowerCase()}
            </span>
          </div>
          <Link to={`/reglas/${rule.entity_id}`} className="font-medium hover:underline">
            {rule.name}
          </Link>
        </div>
        {version && (
          <div className="flex gap-1">
            <Button
              size="sm"
              variant="outline"
              disabled={version.verification_status === "human_verified"}
              onClick={() => review("human_verified")}
            >
              <Check data-icon="inline-start" />
              Confirmar
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={version.verification_status === "rejected"}
              onClick={() => review("rejected")}
            >
              <X data-icon="inline-start" />
              Falso positivo
            </Button>
          </div>
        )}
      </div>
      <p className="line-clamp-3 text-sm text-muted-foreground">{rule.summary}</p>
      <ReviewError error={error} />
      <div>
        <button
          type="button"
          className="inline-flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
        >
          <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
          Evidencia ({rule.evidence_count})
        </button>
        {open && (
          <div className="mt-2">
            <RuleEvidence {...props} entityId={rule.entity_id} />
          </div>
        )}
      </div>
    </li>
  )
}

function RuleList(props: ScreenProps) {
  const { project, onSessionLost } = props
  const [rules, setRules] = useState<RuleSummary[] | null>(null)
  const [filter, setFilter] = useState<Filter>("all")
  const [query, setQuery] = useState("")
  const [panel, setPanel] = useState<"extract" | "manual" | "consistency" | null>(null)
  const { error, fail } = useErrors(onSessionLost)
  const load = useCallback(() => {
    listRules(project.id, { q: query.trim() || undefined })
      .then(setRules)
      .catch(fail)
  }, [project.id, query, fail])
  useEffect(load, [load])

  const shown = rules?.filter((rule) => matches(rule, filter)) ?? []
  const count = (value: Filter) => rules?.filter((rule) => matches(rule, value)).length ?? 0
  const toggle = (next: "extract" | "manual" | "consistency") => setPanel(panel === next ? null : next)

  return (
    <>
      <PageHeader
        title="Reglas"
        description="El libro de reglas del proyecto: lo que el sistema debe hacer, con citas al código. La IA propone y tú decides."
        actions={
          <>
            <Button
              variant={panel === "consistency" ? "secondary" : "outline"}
              onClick={() => toggle("consistency")}
            >
              <ListChecks data-icon="inline-start" />
              Revisar consistencia
            </Button>
            <Button variant={panel === "manual" ? "secondary" : "outline"} onClick={() => toggle("manual")}>
              <PenLine data-icon="inline-start" />
              Crear regla manual
            </Button>
            <Button variant={panel === "extract" ? "secondary" : "default"} onClick={() => toggle("extract")}>
              <Sparkles data-icon="inline-start" />
              Extraer reglas automáticamente
            </Button>
          </>
        }
      />
      {panel === "extract" && <ExtractionPanel {...props} onFinished={load} />}
      {panel === "consistency" && <ConsistencyPanel {...props} onFinished={load} />}
      {panel === "manual" && (
        <ManualRuleForm
          {...props}
          onCreated={() => {
            setPanel(null)
            load()
          }}
        />
      )}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Tabs value={filter} onValueChange={(value) => setFilter(value as Filter)}>
          <TabsList>
            <TabsTrigger value="all">Todas ({count("all")})</TabsTrigger>
            <TabsTrigger value="review">Por revisar ({count("review")})</TabsTrigger>
            <TabsTrigger value="unverified">Sin verificar ({count("unverified")})</TabsTrigger>
            <TabsTrigger value="verified">Verificadas ({count("verified")})</TabsTrigger>
            <TabsTrigger value="rejected">Falsos positivos ({count("rejected")})</TabsTrigger>
          </TabsList>
        </Tabs>
        <Input
          aria-label="Buscar reglas"
          maxLength={200}
          placeholder="Buscar reglas…"
          className="max-w-64"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <ErrorText>{error}</ErrorText>
      {rules === null ? (
        <Muted>Cargando…</Muted>
      ) : shown.length === 0 ? (
        <EmptyState
          icon={BookOpen}
          title={rules.length === 0 ? "Todavía no hay reglas" : "Ninguna regla en este filtro"}
          action={
            rules.length === 0 && (
              <Button onClick={() => setPanel("extract")}>
                <Sparkles data-icon="inline-start" />
                Extraer reglas automáticamente
              </Button>
            )
          }
        >
          {rules.length === 0 && "La IA local puede proponerlas a partir del código, o puedes escribirlas tú."}
        </EmptyState>
      ) : (
        <ul className="grid gap-3">
          {shown.map((rule) => (
            <RuleItem key={rule.entity_id} {...props} rule={rule} onChanged={load} />
          ))}
        </ul>
      )}
    </>
  )
}

const REVIEW_ACTIONS: { status: VerificationStatus; label: string }[] = [
  { status: "human_verified", label: "Confirmar" },
  { status: "source_supported", label: "Respaldada por el código" },
  { status: "test_supported", label: "Respaldada por un test" },
  { status: "rejected", label: "Falso positivo" },
  { status: "unverified", label: "Volver a sin verificar" },
]

function VersionBlock({
  version,
  latest,
  onChanged,
  onSessionLost,
}: {
  version: RuleVersion
  latest: boolean
  onChanged: () => void
  onSessionLost: (error: unknown) => void
}) {
  const { error, setError, fail } = useErrors(onSessionLost)
  const review = (status: VerificationStatus) => {
    setError(null)
    setVerification(version.id, status).then(onChanged).catch(fail)
  }
  const content = version.content
  return (
    <Section
      title={
        <span className="flex flex-wrap items-center gap-2">
          Versión {version.version_number}
          <VerificationStatusBadge status={version.verification_status} />
          {latest && <StatusBadge tone="no_evidence">Vigente</StatusBadge>}
        </span>
      }
      description={
        <>
          {ORIGIN_LABELS[version.origin]}
          {version.model_name && ` (${version.model_name})`} · {new Date(version.created_at).toLocaleString()}
          {version.reviewed_by && ` · Revisada por ${version.reviewed_by}`}
        </>
      }
    >
      {content && (
        <div className="grid gap-3 text-sm">
          <p className="text-base whitespace-pre-wrap">{content.statement}</p>
          {content.rationale && <Muted>{content.rationale}</Muted>}
          {content.expected_behavior.length > 0 && (
            <div className="grid gap-1">
              <Eyebrow>Comportamiento esperado</Eyebrow>
              <ul className="list-disc pl-5">
                {content.expected_behavior.map((item) => (
                  <li key={item.description}>{item.description}</li>
                ))}
              </ul>
            </div>
          )}
          {content.exceptions.length > 0 && (
            <div className="grid gap-1">
              <Eyebrow>Excepciones</Eyebrow>
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
      <div className="grid gap-2">
        <Eyebrow>Evidencia</Eyebrow>
        {version.evidence.length === 0 ? (
          <Muted>No se encontró evidencia en el código.</Muted>
        ) : (
          version.evidence.map((evidence) => <EvidenceItem key={evidence.id} evidence={evidence} />)
        )}
      </div>
      {version.findings.length > 0 && (
        <div className="grid gap-1">
          <Eyebrow>Hallazgos sobre esta versión</Eyebrow>
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
          <Eyebrow>Verificación</Eyebrow>
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
          <p className="text-xs text-muted-foreground">
            "Respaldada" exige una cita comprobada; una propuesta de la IA no basta. Confirmar y descartar
            registran tu nombre del perfil.
          </p>
          <ReviewError error={error} />
        </div>
      )}
    </Section>
  )
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
  if (error) return <ErrorText>{error}</ErrorText>
  if (rule === null) return <Muted>Cargando…</Muted>
  return (
    <>
      <Link to="/reglas" className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="size-4" />
        Reglas
      </Link>
      <PageHeader
        title={rule.name}
        description={
          <>
            {CATEGORY_LABELS[rule.rule_category as RuleCategory] ?? rule.rule_category} · importancia{" "}
            {CRITICALITY_LABELS[rule.criticality].toLowerCase()} · <code className="text-xs">{rule.canonical_key}</code>
          </>
        }
      />
      {rule.review_reason && (
        <StatusBadge tone="attention">{REVIEW_LABELS[rule.review_reason] ?? "Revisar"}</StatusBadge>
      )}
      {pending && (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <StatusBadge tone="attention">Sin enunciado</StatusBadge>
          {writing === "writing" && <Muted>Redactando el enunciado…</Muted>}
          {writing === "failed" && (
            <>
              <Muted>No se pudo redactar ahora; se hará en segundo plano.</Muted>
              <Button size="sm" variant="outline" onClick={write}>
                Reintentar
              </Button>
            </>
          )}
        </div>
      )}
      {rule.relationships.length > 0 && (
        <Section title="Relaciones">
          <ul className="grid gap-1 text-sm">
            {rule.relationships.map((relation) => (
              <li key={relation.id}>
                {relation.direction === "out" ? "→" : "←"} {relation.relation_type}{" "}
                <span className="font-medium">{relation.other.name}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}
      {rule.versions.map((version, index) => (
        <VersionBlock
          key={version.id}
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
