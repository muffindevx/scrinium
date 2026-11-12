import { ShieldAlert } from "lucide-react"
import { Link } from "react-router"
import { EvidenceCite } from "@/components/evidence-cite"
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
    <Link className="hover:underline" to={`/reglas/${str(row.entity_id)}`}>
      {str(row.name)}
    </Link>
  )
}

function minutes(seconds: number) {
  if (seconds < 90) return "un minuto"
  const total = Math.round(seconds / 60)
  return total < 90 ? `${total} minutos` : `${(total / 60).toFixed(1)} horas`
}

function ConsistencyResult({ analysis }: { analysis: Analysis }) {
  const result = analysis.result ?? {}
  return (
    <div className="grid gap-2 text-sm">
      <p>
        {str(result.rules)} reglas revisadas: {str(result.duplicates)} posibles duplicados,{" "}
        {str(result.contradictions)} contradicciones y {str(result.ambiguous)} reglas ambiguas.
      </p>
      <Muted>Los hallazgos son propuestas: confírmalos o descártalos abajo.</Muted>
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
          En pausa: lo que falta sigue en la cola. Pulsa «Continuar la extracción» para seguir.
        </p>
      )}
      {strings(result.kept).length + strings(result.adjusted).length + strings(result.broken).length > 0 && (
        <p>
          Reglas cuyo código cambió: {strings(result.kept).length} se mantienen,{" "}
          {strings(result.adjusted).length} se ajustaron (versión nueva sin verificar) y{" "}
          {strings(result.broken).length} ya no se cumplen.
        </p>
      )}
      {Number(result.rules_code_removed) > 0 && (
        <Muted>{str(result.rules_code_removed)} reglas perdieron el código que las implementaba.</Muted>
      )}
      {Number(result.already_done) > 0 && (
        <Muted>{str(result.already_done)} trozos ya se habían analizado y no cambiaron: no se repiten.</Muted>
      )}
      <p>
        {created} reglas nuevas, {updated} con versión nueva y {unchanged} sin cambios. Se
        descartaron {str(result.dropped_rules)} propuestas sin una cita válida.
      </p>
      <Muted>
        De {str(result.functions_total)} funciones, la IA leyó {str(result.candidates)} trozos con
        señales de regla en {str(result.batches)} lotes
        {Number(result.batches_failed) > 0 && ` (${str(result.batches_failed)} fallaron)`}.{" "}
        {Number(result.below_threshold) > 0 &&
          `${str(result.below_threshold)} trozos sin señales no se leyeron. `}
        La documentación y la configuración no se usan para extraer reglas. Todas nacen como
        propuestas sin verificar.
      </Muted>
      <Link className="text-sm font-medium hover:underline" to="/reglas">
        Ver las reglas
      </Link>
    </div>
  )
}

function DiffResult({ analysis }: { analysis: Analysis }) {
  const result = analysis.result ?? {}
  return (
    <div className="grid gap-3 text-sm">
      <Section title="Archivos">
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
      <Section title="Reglas afectadas">
        {rows(result.impacted_rules).length === 0 ? (
          <Muted>Ninguna regla conocida tiene evidencia en estos archivos.</Muted>
        ) : (
          <ul className="grid gap-1">
            {rows(result.impacted_rules).map((rule) => (
              <li key={str(rule.entity_id)} className="flex flex-wrap items-center gap-2">
                <RuleLink row={rule} />
                <StatusBadge tone={rule.match === "cited_lines" ? "attention" : "no_evidence"}>
                  {rule.match === "cited_lines" ? "Toca las líneas citadas" : "Mismo archivo"}
                </StatusBadge>
              </li>
            ))}
          </ul>
        )}
      </Section>
      {strings(result.suggested_tests).length > 0 && (
        <Section title="Tests que conviene ejecutar">
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
  write_without_where: "Escribe sin WHERE",
  destructive_ddl: "Borra estructura o datos",
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
            {dangerous.length === 1 ? "Una sentencia es peligrosa" : `${dangerous.length} sentencias son peligrosas`}
          </AlertTitle>
          <AlertDescription>
            Cambian o borran datos sin filtro, o eliminan estructura. Revísalas antes de ejecutarlas.
          </AlertDescription>
        </Alert>
      )}
      <Muted>
        El SQL no se ha ejecutado: se ha leído como texto (
        {result.dialect === "generic" ? "dialecto genérico" : str(result.dialect)}).
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
              Lee: {strings(statement.reads).join(", ") || "—"} · Escribe:{" "}
              {strings(statement.writes).join(", ") || "—"}
            </p>
            {strings(statement.unknown_columns).length + strings(statement.unknown_tables).length >
              0 && (
              <p className="text-xs text-destructive">
                Fuera del esquema:{" "}
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
  added: "Añadida",
  modified: "Modificada",
  retired: "Retirada",
}
const ASSESSMENT_CHANGE: Record<string, string> = {
  supported: "Respaldada",
  unverifiable: "No verificable",
  contradictory: "Contradictoria",
  retired: "Retirada",
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
      <Section title="Reglas">
        {!knowledge.available ? (
          <Muted>Falta guardar el estado de alguna de las dos versiones: no se pueden comparar las reglas.</Muted>
        ) : rows(knowledge.changes).length === 0 ? (
          <Muted>Ninguna regla cambió ({str(knowledge.unchanged)} iguales).</Muted>
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
                  Evidencia: {rows(change.base_evidence).length} antes,{" "}
                  {rows(change.target_evidence).length} después
                </p>
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section title="Código">
        {rows(code.changed_files).length === 0 ? (
          <Muted>Sin archivos cambiados, o sin comparación de código.</Muted>
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
        <Section title="Reglas cuyo código cambió">
          <ul className="grid gap-1">
            {rows(result.rules_with_changed_code).map((rule) => (
              <li key={str(rule.entity_id)} className="flex flex-wrap items-center gap-2">
                <RuleLink row={rule} />
                {rule.knowledge_changed === false && <Badge variant="outline">La regla no cambió</Badge>}
                {rule.knowledge_changed === null && (
                  <Badge variant="outline">Sin estado guardado: no se sabe si la regla cambió</Badge>
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
      <Section title="Commits relacionados">
        {rows(result.candidates).length === 0 ? (
          <Muted>Ningún commit coincide con la descripción.</Muted>
        ) : (
          <ul className="divide-y">
            {rows(result.candidates).map((candidate) => (
              <li key={str(candidate.commit)} className="grid gap-1 py-2">
                <div className="flex flex-wrap items-center gap-2">
                  <code className="text-xs">{shortSha(str(candidate.commit))}</code>
                  <span>{str(candidate.subject)}</span>
                  {Boolean(candidate.fix_like) && <Badge variant="outline">Corrección</Badge>}
                </div>
                <p className="text-xs text-muted-foreground">
                  {strings(candidate.files).join(", ")}
                  {strings(candidate.tests_changed).length === 0 && " · sin tests"}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Section>
      {rows(result.related_rules).length > 0 && (
        <Section title="Reglas con evidencia en esos archivos">
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
        <Section title="Código relacionado">
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
      Tokens: {format(read)} leídos{cached > 0 && ` (${format(cached)} de caché)`} · {format(written)} escritos
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
            Lote {done} de {total} · {found} reglas propuestas hasta ahora
            {result.eta_seconds != null && done > 0 && ` · quedan unos ${minutes(Number(result.eta_seconds))}`}
            . Las reglas aparecen en la lista a medida que salen; si cierras Scrinium, continúa al
            volver a abrirlo.
          </Muted>
          <TokenUsage tokens={result.tokens} />
        </div>
      )
    }
    return <Muted>Analizando… Con el motor local puede tardar un par de minutos.</Muted>
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
