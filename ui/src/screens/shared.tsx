import { t } from "@/lib/i18n"
import { FolderX, RotateCw, TriangleAlert } from "lucide-react"
import { Link } from "react-router"
import { type ReactNode, useEffect, useState } from "react"
import { AnalysisStatusBadge, StatusBadge } from "@/components/status"
import { type Tone } from "@/lib/status"
import { Button } from "@/components/ui/button"
import { EvidenceCite } from "@/components/evidence-cite"
import {
  ApiError,
  type AssessmentStatus,
  type Evidence,
  type Finding,
  type Project,
  isFinished,
  mergeRules,
  sendFeedback,
} from "@/lib/api"
import { ANALYSIS_TYPE_LABELS, METHOD_LABELS, SEVERITY_LABELS, errorMessage } from "@/lib/labels"
import { AnalysisResult } from "@/screens/AnalysisResult"
import { useErrors, usePolledAnalysis } from "@/screens/hooks"

export interface ScreenProps {
  project: Project
  onSessionLost: (error: unknown) => void
}

export function ErrorText({ children }: { children: ReactNode }) {
  if (!children) return null
  return (
    <p role="alert" className="flex items-center gap-1 text-sm text-destructive">
      <TriangleAlert className="size-3.5" />
      {children}
    </p>
  )
}

// Errors whose fix is in Integraciones: the source's folder or its Git repository.
const SOURCE_ERRORS = new Set(["source_path_missing", "git_failed", "no_git_source"])

/**
 * What a screen shows while its first load is on its way, or why it failed: never "Cargando…"
 * for ever. Says what happened, the path when it is a folder that is gone, and offers to try
 * again or to go where it can be fixed.
 */
export function Loading({ failure, onRetry }: { failure: ApiError | null; onRetry?: () => void }) {
  if (failure === null) return <Muted>{t("Cargando…")}</Muted>
  const missing = failure.code === "source_path_missing"
  const path = typeof failure.details.path === "string" ? failure.details.path : null
  const Icon = missing ? FolderX : TriangleAlert
  return (
    <div role="alert" className="grid justify-items-start gap-3 rounded-xl bg-card p-5 ring-1 ring-destructive/25">
      <div className="flex items-start gap-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-md bg-destructive/10 text-destructive">
          <Icon className="size-4" aria-hidden="true" />
        </span>
        <div className="grid gap-1">
          <p className="text-sm font-semibold">{missing ? t("No se encuentra el repositorio") : t("No se pudo cargar")}</p>
          <p className="text-sm text-muted-foreground">{errorMessage(failure.code)}</p>
          {path && <code className="w-fit rounded bg-muted px-1.5 py-0.5 font-mono text-xs break-all">{path}</code>}
          {missing && (
            <p className="text-xs text-muted-foreground">
              {t("Si la carpeta está en otra ruta, vuelve a conectar el disco o añade la fuente con la ruta nueva en Integraciones. Antes de borrar la antigua, Scrinium te dice qué reglas afecta.")}
            </p>
          )}
        </div>
      </div>
      <div className="flex flex-wrap gap-2 pl-11">
        <Button size="sm" variant="outline" onClick={onRetry ?? (() => window.location.reload())}>
          <RotateCw data-icon="inline-start" />
          {t("Reintentar")}
        </Button>
        {SOURCE_ERRORS.has(failure.code ?? "") && (
          <Button size="sm" variant="outline" render={<Link to="/integraciones" />} nativeButton={false}>
            {t("Ir a Integraciones")}
          </Button>
        )}
      </div>
    </div>
  )
}

export function Muted({ children }: { children: ReactNode }) {
  return <p className="text-sm text-muted-foreground">{children}</p>
}

/** Small title inside a block. */
export function Eyebrow({ children }: { children: ReactNode }) {
  return <p className="text-xs font-medium tracking-wide text-muted-foreground uppercase">{children}</p>
}

export function EvidenceItem({ evidence }: { evidence: Evidence }) {
  const { locator } = evidence
  if (!locator.path) {
    return (
      <p className="text-sm text-muted-foreground">
        {METHOD_LABELS[evidence.method]}
        {locator.reviewer && t(" por {0}", locator.reviewer)}
      </p>
    )
  }
  return (
    <EvidenceCite
      path={locator.path}
      startLine={locator.start_line}
      endLine={locator.end_line}
      commit={locator.commit}
      revisionId={evidence.revision_id}
      excerpt={evidence.excerpt}
      extra={<span className="text-xs text-muted-foreground">{METHOD_LABELS[evidence.method]}</span>}
    />
  )
}

const SEVERITY_TONE: Record<Finding["severity"], Tone> = {
  info: "no_evidence",
  low: "no_evidence",
  medium: "proposed",
  high: "attention",
  critical: "discarded",
}

const ASSESSMENT: Record<AssessmentStatus, [Tone, string]> = {
  hypothesis: ["proposed", t("Hipótesis sin verificar")],
  evidence_supported: ["verified", t("Con evidencia")],
  test_verified: ["verified", t("Comprobado con test")],
  human_confirmed: ["verified", t("Confirmado")],
  false_positive: ["discarded", t("Descartado (falso positivo)")],
  unresolved: ["no_evidence", t("Sin resolver")],
}

const FEEDBACK: { status: AssessmentStatus; label: string }[] = [
  { status: "human_confirmed", label: t("Confirmar") },
  { status: "false_positive", label: t("Falso positivo") },
  { status: "unresolved", label: t("Sin resolver") },
]

function MergeButton({
  projectId,
  rules,
  onSessionLost,
}: {
  projectId: string
  rules: string[]
  onSessionLost: (error: unknown) => void
}) {
  const [done, setDone] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  if (done) return <span className="text-xs text-muted-foreground">{t("Fusionadas: se conserva la primera.")}</span>
  return (
    <>
      <Button
        size="xs"
        variant="outline"
        onClick={() => {
          setError(null)
          mergeRules(projectId, rules[0], rules[1])
            .then(() => setDone(true))
            .catch(fail)
        }}
      >
        {t("Fusionar (conservar la primera)")}
      </Button>
      <ErrorText>{error}</ErrorText>
    </>
  )
}

export function FindingList({
  findings,
  onSessionLost,
  projectId,
}: {
  findings: Finding[]
  onSessionLost: (error: unknown) => void
  projectId?: string
}) {
  const [items, setItems] = useState(findings)
  const { error, setError, fail } = useErrors(onSessionLost)
  if (items.length === 0) return <Muted>{t("Sin hallazgos.")}</Muted>
  const review = (finding: Finding, status: AssessmentStatus) => {
    setError(null)
    sendFeedback(finding.id, status)
      .then((updated) => setItems((all) => all.map((f) => (f.id === updated.id ? { ...f, ...updated } : f))))
      .catch(fail)
  }
  return (
    <div className="grid gap-2">
      <ErrorText>{error}</ErrorText>
      <ul className="grid gap-2">
        {items.map((finding) => {
          const [tone, label] = ASSESSMENT[finding.assessment_status]
          return (
            <li key={finding.id} className="grid gap-2 rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge tone={SEVERITY_TONE[finding.severity]}>
                  {t("Gravedad")} {SEVERITY_LABELS[finding.severity].toLowerCase()}
                </StatusBadge>
                <StatusBadge tone={tone}>{label}</StatusBadge>
                <span className="text-sm font-medium">{finding.title}</span>
              </div>
              <p className="text-sm whitespace-pre-wrap">{finding.description}</p>
              {finding.recommended_next_step && (
                <p className="text-sm text-muted-foreground">
                  <span className="font-medium text-foreground">{t("Siguiente comprobación:")} </span>
                  {finding.recommended_next_step}
                </p>
              )}
              <div className="flex flex-wrap items-center gap-1">
                {FEEDBACK.map(({ status, label: action }) => (
                  <Button
                    key={status}
                    size="xs"
                    variant="outline"
                    disabled={finding.assessment_status === status}
                    onClick={() => review(finding, status)}
                  >
                    {action}
                  </Button>
                ))}
                {projectId &&
                  finding.location?.kind === "duplicate" &&
                  Array.isArray(finding.location.rules) &&
                  finding.location.rules.length === 2 && (
                    <MergeButton
                      projectId={projectId}
                      rules={finding.location.rules as string[]}
                      onSessionLost={onSessionLost}
                    />
                  )}
                {finding.reviewed_by && (
                  <span className="text-xs text-muted-foreground">{t("Revisado por")} {finding.reviewed_by}</span>
                )}
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}

export function Limitations({ items }: { items: unknown }) {
  const list = Array.isArray(items) ? (items as string[]) : []
  if (list.length === 0) return null
  return (
    <div className="grid gap-1">
      <Eyebrow>{t("Límites de este análisis")}</Eyebrow>
      <ul className="list-disc pl-5 text-sm text-muted-foreground">
        {list.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
    </div>
  )
}

/** An analysis of any type: status, result and findings. Refreshes itself until it finishes. */
export function AnalysisDetail({
  analysisId,
  onSessionLost,
  onFinished,
}: {
  analysisId: string
  onSessionLost: (error: unknown) => void
  onFinished?: () => void
}) {
  const analysis = usePolledAnalysis(analysisId, onSessionLost)
  const finished = analysis !== null && isFinished(analysis)
  useEffect(() => {
    if (finished) onFinished?.()
  }, [finished, onFinished])
  if (analysis === null) return <Muted>{t("Cargando…")}</Muted>
  return (
    <div className="grid gap-4">
      <div className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="font-heading text-base font-medium">{ANALYSIS_TYPE_LABELS[analysis.analysis_type]}</h2>
          <AnalysisStatusBadge status={analysis.status} />
          {analysis.model_name && (
            <span className="text-xs text-muted-foreground">
              {t("Con")} {analysis.model_name}{t(": son propuestas que hay que verificar.")}
            </span>
          )}
        </div>
        {analysis.question && (
          <p className="line-clamp-3 text-sm whitespace-pre-wrap text-muted-foreground">{analysis.question}</p>
        )}
        {analysis.status === "failed" && <ErrorText>{errorMessage(analysis.error_summary)}</ErrorText>}
      </div>
      <AnalysisResult analysis={analysis} />
      {analysis.findings && analysis.findings.length > 0 && (
        <div className="grid gap-2">
          <Eyebrow>{t("Hallazgos")}</Eyebrow>
          <FindingList findings={analysis.findings} onSessionLost={onSessionLost} projectId={analysis.project_id} />
        </div>
      )}
    </div>
  )
}
