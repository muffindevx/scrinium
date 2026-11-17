import { t } from "@/lib/i18n"
import { ShieldAlert } from "lucide-react"
import { Link } from "react-router"
import { EvidenceCite } from "@/components/evidence-cite"
import { RuleLink as SharedRuleLink } from "@/components/rule-link"
import { StatusBadge } from "@/components/status"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import type { Analysis } from "@/lib/api"
import { shortSha } from "@/lib/labels"
import { Eyebrow, Limitations, Muted } from "@/screens/shared"

// Each analysis type's result is free JSON from the backend: it is read with minimal types.
type Row = Record<string, unknown>
const rows = (value: unknown) => (Array.isArray(value) ? (value as Row[]) : [])
const strings = (value: unknown) => (Array.isArray(value) ? (value as string[]) : [])
const str = (value: unknown) => (value === null || value === undefined ? "" : String(value))

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-2">
      <Eyebrow>{title}</Eyebrow>
      {children}
    </div>
  )
}

function RuleLink({ row }: { row: Row }) {
  return (
    <SharedRuleLink className="hover:underline" id={str(row.entity_id)}>
      {str(row.name)}
    </SharedRuleLink>
  )
}

function minutes(seconds: number) {
  if (seconds < 90) return t("un minuto")
  const total = Math.round(seconds / 60)
  return total < 90 ? t("{0} minutos", total) : t("{0} horas", (total / 60).toFixed(1))
}

function ConsistencyResult({ analysis }: { analysis: Analysis }) {
  const result = analysis.result ?? {}
  return (
    <div className="grid gap-2 text-sm">
      <p>
        {str(result.rules)} {t("reglas revisadas:")} {str(result.duplicates)} {t("posibles duplicados,")}{" "}
        {str(result.contradictions)} {t("contradicciones y")} {str(result.ambiguous)} {t("reglas ambiguas.")}
      </p>
      <Muted>{t("Los hallazgos son propuestas: confírmalos o descártalos abajo.")}</Muted>
    </div>
  )
}

function ExtractionResult({ analysis }: { analysis: Analysis }) {
  const result = analysis.result ?? {}
  if (result.kind === "consistency") return <ConsistencyResult analysis={analysis} />
  const created = strings(result.created).length
  const updated = strings(result.updated).length
  const unchanged = strings(result.unchanged).length
  return (
    <div className="grid gap-2 text-sm">
      {analysis.status === "cancelled" && (
        <p className="font-medium">
          {t("En pausa: lo que falta sigue en la cola. Pulsa «Continuar la extracción» para seguir.")}
        </p>
      )}
      {strings(result.kept).length + strings(result.adjusted).length + strings(result.broken).length > 0 && (
        <p>
          {t("Reglas cuyo código cambió:")} {strings(result.kept).length} {t("se mantienen,")}{" "}
          {strings(result.adjusted).length} {t("se ajustaron (versión nueva sin verificar) y")}{" "}
          {strings(result.broken).length} {t("ya no se cumplen.")}
        </p>
      )}
      {Number(result.rules_value_changed) > 0 && (
        <p className="font-medium">
          {str(result.rules_value_changed)} {t("reglas cambiaron: su código decide ahora con otros valores.")}
        </p>
      )}
      {Number(result.rules_code_removed) > 0 && (
        <Muted>{str(result.rules_code_removed)} {t("reglas perdieron el código que las implementaba.")}</Muted>
      )}
      {Number(result.already_done) > 0 && (
        <Muted>{str(result.already_done)} {t("trozos ya se habían analizado y no cambiaron: no se repiten.")}</Muted>
      )}
      <p>
        {created} {t("reglas nuevas,")} {updated} {t("con versión nueva y")} {unchanged} {t("sin cambios. Se descartaron")} {str(result.dropped_rules)} {t("propuestas sin una cita válida.")}
      </p>
      <Muted>
        {t("De")} {str(result.functions_total)} {t("funciones, la IA leyó")} {str(result.candidates)} {t("trozos con señales de regla en")} {str(result.batches)} {t("lotes")}
        {Number(result.batches_failed) > 0 && t(" ({0} fallaron)", str(result.batches_failed))}.{" "}
        {Number(result.below_threshold) > 0 &&
          t("{0} trozos sin señales no se leyeron. ", str(result.below_threshold))}
        {t("La documentación y la configuración no se usan para extraer reglas. Todas nacen como propuestas sin verificar.")}
      </Muted>
      <Link className="text-sm font-medium hover:underline" to="/reglas">
        {t("Ver las reglas")}
      </Link>
    </div>
  )
}

function DiffResult({ analysis }: { analysis: Analysis }) {
  const result = analysis.result ?? {}
  return (
    <div className="grid gap-3 text-sm">
      <Section title={t("Archivos")}>
        <ul className="grid gap-1">
          {rows(result.files).map((file) => (
            <li key={str(file.path)} className="flex flex-wrap items-baseline gap-2">
              <code className="text-xs">{str(file.path)}</code>
              <span className="text-xs text-muted-foreground">
                {str(file.status)}, +{str(file.added)} −{str(file.removed)}
              </span>
              {strings(file.symbols).map((symbol) => (
                <Badge key={symbol} variant="outline">
                  {symbol}
                </Badge>
              ))}
            </li>
          ))}
        </ul>
      </Section>
      <Section title={t("Reglas afectadas")}>
        {rows(result.impacted_rules).length === 0 ? (
          <Muted>{t("Ninguna regla conocida tiene evidencia en estos archivos.")}</Muted>
        ) : (
          <ul className="grid gap-1">
            {rows(result.impacted_rules).map((rule) => (
              <li key={str(rule.entity_id)} className="flex flex-wrap items-center gap-2">
                <RuleLink row={rule} />
                <StatusBadge tone={rule.match === "cited_lines" ? "attention" : "no_evidence"}>
                  {rule.match === "cited_lines" ? t("Toca las líneas citadas") : t("Mismo archivo")}
                </StatusBadge>
              </li>
            ))}
          </ul>
        )}
      </Section>
      {strings(result.suggested_tests).length > 0 && (
        <Section title={t("Tests que conviene ejecutar")}>
          <ul className="list-disc pl-5">
            {strings(result.suggested_tests).map((test) => (
              <li key={test}>
                <code className="text-xs">{test}</code>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  )
}

const RISK_LABELS: Record<string, string> = {
  write_without_where: t("Escribe sin WHERE"),
  destructive_ddl: t("Borra estructura o datos"),
  select_star: "SELECT *",
}

const DANGEROUS = new Set(["write_without_where", "destructive_ddl"])

function SqlResult({ analysis }: { analysis: Analysis }) {
  const result = analysis.result ?? {}
  const dangerous = rows(result.statements).filter((statement) =>
    strings(statement.risks).some((risk) => DANGEROUS.has(risk)),
  )
  return (
    <div className="grid gap-3 text-sm">
      {dangerous.length > 0 && (
        <Alert variant="destructive">
          <ShieldAlert />
          <AlertTitle>
            {dangerous.length === 1 ? t("Una sentencia es peligrosa") : t("{0} sentencias son peligrosas", dangerous.length)}
          </AlertTitle>
          <AlertDescription>
            {t("Cambian o borran datos sin filtro, o eliminan estructura. Revísalas antes de ejecutarlas.")}
          </AlertDescription>
        </Alert>
      )}
      <Muted>
        {t("El SQL no se ha ejecutado: se ha leído como texto (")}
        {result.dialect === "generic" ? t("dialecto genérico") : str(result.dialect)}).
      </Muted>
      <ul className="divide-y">
        {rows(result.statements).map((statement) => (
          <li key={str(statement.index)} className="grid gap-1 py-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">
                {str(statement.index)}. {str(statement.kind).toUpperCase()}
              </span>
              {strings(statement.risks).map((risk) => (
                <StatusBadge key={risk} tone={DANGEROUS.has(risk) ? "discarded" : "attention"}>
                  {RISK_LABELS[risk] ?? risk}
                </StatusBadge>
              ))}
            </div>
            <p className="text-xs text-muted-foreground">
              {t("Lee:")} {strings(statement.reads).join(", ") || "—"} {t("· Escribe:")}{" "}
              {strings(statement.writes).join(", ") || "—"}
            </p>
            {strings(statement.unknown_columns).length + strings(statement.unknown_tables).length >
              0 && (
              <p className="text-xs text-destructive">
                {t("Fuera del esquema:")}{" "}
                {[...strings(statement.unknown_tables), ...strings(statement.unknown_columns)].join(", ")}
              </p>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}

const CHANGE_LABELS: Record<string, string> = {
  added: t("Añadida"),
  modified: t("Modificada"),
  retired: t("Retirada"),
}
const ASSESSMENT_CHANGE: Record<string, string> = {
  supported: t("Respaldada"),
  unverifiable: t("No verificable"),
  contradictory: t("Contradictoria"),
  retired: t("Retirada"),
}

function ReleaseResult({ analysis }: { analysis: Analysis }) {
  const result = analysis.result ?? {}
  const knowledge = (result.knowledge ?? {}) as Row
  const code = (result.code ?? {}) as Row
  const base = (result.base ?? {}) as Row
  const target = (result.target ?? {}) as Row
  return (
    <div className="grid gap-3 text-sm">
      <p>
        <code className="text-xs">{shortSha(str(base.commit))}</code> →{" "}
        <code className="text-xs">{shortSha(str(target.commit))}</code>
      </p>
      <Section title={t("Reglas")}>
        {!knowledge.available ? (
          <Muted>{t("Falta guardar el estado de alguna de las dos versiones: no se pueden comparar las reglas.")}</Muted>
        ) : rows(knowledge.changes).length === 0 ? (
          <Muted>{t("Ninguna regla cambió (")}{str(knowledge.unchanged)} {t("iguales).")}</Muted>
        ) : (
          <ul className="divide-y">
            {rows(knowledge.changes).map((change) => (
              <li key={str(change.entity_id)} className="grid gap-1 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Badge variant="outline">{CHANGE_LABELS[str(change.change)]}</Badge>
                  <StatusBadge
                    tone={
                      change.assessment === "supported"
                        ? "verified"
                        : change.assessment === "contradictory"
                          ? "discarded"
                          : "no_evidence"
                    }
                  >
                    {ASSESSMENT_CHANGE[str(change.assessment)]}
                  </StatusBadge>
                  <RuleLink row={change} />
                  <span className="text-xs text-muted-foreground">
                    v{str(change.base_version) || "—"} → v{str(change.target_version) || "—"}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground">
                  {t("Evidencia:")} {rows(change.base_evidence).length} {t("antes,")}{" "}
                  {rows(change.target_evidence).length} {t("después")}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title={t("Código")}>
        {rows(code.changed_files).length === 0 ? (
          <Muted>{t("Sin archivos cambiados, o sin comparación de código.")}</Muted>
        ) : (
          <ul className="grid gap-0.5">
            {rows(code.changed_files).map((file) => (
              <li key={str(file.path)}>
                <code className="text-xs">
                  {str(file.status)} {str(file.path)}
                </code>
              </li>
            ))}
          </ul>
        )}
      </Section>
      {rows(result.rules_with_changed_code).length > 0 && (
        <Section title={t("Reglas cuyo código cambió")}>
          <ul className="grid gap-1">
            {rows(result.rules_with_changed_code).map((rule) => (
              <li key={str(rule.entity_id)} className="flex flex-wrap items-center gap-2">
                <RuleLink row={rule} />
                {rule.knowledge_changed === false && <Badge variant="outline">{t("La regla no cambió")}</Badge>}
                {rule.knowledge_changed === null && (
                  <Badge variant="outline">{t("Sin estado guardado: no se sabe si la regla cambió")}</Badge>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  )
}

function BugResult({ analysis }: { analysis: Analysis }) {
  const result = analysis.result ?? {}
  return (
    <div className="grid gap-3 text-sm">
      <Section title={t("Commits relacionados")}>
        {rows(result.candidates).length === 0 ? (
          <Muted>{t("Ningún commit coincide con la descripción.")}</Muted>
        ) : (
          <ul className="divide-y">
            {rows(result.candidates).map((candidate) => (
              <li key={str(candidate.commit)} className="grid gap-1 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-xs">{shortSha(str(candidate.commit))}</code>
                  <span>{str(candidate.subject)}</span>
                  {Boolean(candidate.fix_like) && <Badge variant="outline">{t("Corrección")}</Badge>}
                </div>
                <p className="text-xs text-muted-foreground">
                  {strings(candidate.files).join(", ")}
                  {strings(candidate.tests_changed).length === 0 && t(" · sin tests")}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Section>
      {rows(result.related_rules).length > 0 && (
        <Section title={t("Reglas con evidencia en esos archivos")}>
          <ul className="grid gap-1">
            {rows(result.related_rules).map((rule) => (
              <li key={str(rule.entity_id)}>
                <RuleLink row={rule} />
              </li>
            ))}
          </ul>
        </Section>
      )}
      {rows(result.related_fragments).length > 0 && (
        <Section title={t("Código relacionado")}>
          <ul className="grid gap-2">
            {rows(result.related_fragments).map((fragment) => (
              <li key={str(fragment.chunk_id)}>
                <EvidenceCite
                  path={str(fragment.path)}
                  startLine={Number(fragment.start_line) || undefined}
                  endLine={Number(fragment.end_line) || undefined}
                  revisionId={fragment.revision_id ? str(fragment.revision_id) : null}
                />
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  )
}

/** What the analysis spent on the model: read (and how many from cache) and written. */
function TokenUsage({ tokens }: { tokens: unknown }) {
  if (!tokens || typeof tokens !== "object") return null
  const usage = tokens as Record<string, unknown>
  const read = Number(usage.input) || 0
  const written = Number(usage.output) || 0
  const cached = Number(usage.cached) || 0
  if (read + written === 0) return null
  const format = (value: number) => value.toLocaleString("es")
  return (
    <Muted>
      {t("Tokens:")} {format(read)} {t("leídos")}{cached > 0 && t(" ({0} de caché)", format(cached))} · {format(written)} {t("escritos")}
    </Muted>
  )
}

export function AnalysisResult({ analysis }: { analysis: Analysis }) {
  if (analysis.status === "queued" || analysis.status === "running") {
    const result = analysis.result ?? {}
    if (analysis.analysis_type === "knowledge_extraction" && Number(result.batches_total) > 0) {
      const done = Number(result.batches)
      const total = Number(result.batches_total)
      const found = strings(result.created).length + strings(result.updated).length
      return (
        <div className="grid gap-2">
          <div className="h-2 overflow-hidden rounded-full bg-muted">
            <progress className="sr-only" value={done} max={total} />
            <div className="h-full bg-sky-500 transition-all" style={{ width: `${(done / total) * 100}%` }} />
          </div>
          <Muted>
            {t("Lote")} {done} {t("de")} {total} · {found} {t("reglas propuestas hasta ahora")}
            {result.eta_seconds != null && done > 0 && t(" · quedan unos {0}", minutes(Number(result.eta_seconds)))}
            {t(". Las reglas aparecen en la lista a medida que salen; si cierras Scrinium, continúa al volver a abrirlo.")}
          </Muted>
          <TokenUsage tokens={result.tokens} />
        </div>
      )
    }
    return <Muted>{t("Analizando… Con el motor local puede tardar un par de minutos.")}</Muted>
  }
  if (analysis.status === "failed") return null
  const body = {
    knowledge_extraction: ExtractionResult,
    code_diff: DiffResult,
    sql_review: SqlResult,
    release_comparison: ReleaseResult,
    bug_investigation: BugResult,
    impact_analysis: DiffResult,
  }[analysis.analysis_type]
  const Body = body
  return (
    <div className="grid gap-3">
      <Body analysis={analysis} />
      <Limitations items={analysis.result?.limitations} />
      <TokenUsage tokens={analysis.result?.tokens} />
    </div>
  )
}
