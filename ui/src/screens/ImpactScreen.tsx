import { ArrowLeft, Bug, Database, FileDiff, ScanSearch } from "lucide-react"
import { type FormEvent, useCallback, useState } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { PageHeader, Section } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Label, Textarea } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { type AnalysisType, analyzeDiff, analyzeSql, investigateBug } from "@/lib/api"
import { AnalysisHistory } from "@/screens/AnalysisHistory"
import { useErrors } from "@/screens/hooks"
import { AnalysisDetail, ErrorText, type ScreenProps } from "@/screens/shared"

type Mode = "diff" | "sql" | "bug"

const IMPACT_TYPES: AnalysisType[] = ["code_diff", "sql_review", "bug_investigation", "impact_analysis"]

const DIALECTS = [
  { value: "generic", label: "Genérico" },
  { value: "postgres", label: "PostgreSQL" },
  { value: "mysql", label: "MySQL" },
  { value: "sqlite", label: "SQLite" },
  { value: "tsql", label: "SQL Server" },
  { value: "oracle", label: "Oracle" },
  { value: "bigquery", label: "BigQuery" },
  { value: "snowflake", label: "Snowflake" },
  { value: "duckdb", label: "DuckDB" },
]

const MODES: Record<Mode, { label: string; field: string; placeholder: string; rows: number; max: number }> = {
  diff: {
    label: "Diff de Git",
    field: "Pega la salida de git diff",
    placeholder: "diff --git a/src/billing.py b/src/billing.py\n…",
    rows: 12,
    max: 500000,
  },
  sql: {
    label: "Consulta SQL",
    field: "Pega la consulta",
    placeholder: "UPDATE invoices SET total = 0 WHERE id = 1",
    rows: 8,
    max: 100000,
  },
  bug: {
    label: "Descripción de bug",
    field: "Describe qué falla",
    placeholder: "El total de la factura sale con demasiados decimales",
    rows: 4,
    max: 2000,
  },
}

function NewAnalysis({ project, onSessionLost }: ScreenProps) {
  const navigate = useNavigate()
  const [mode, setMode] = useState<Mode>("diff")
  const [text, setText] = useState("")
  const [dialect, setDialect] = useState("generic")
  const [schema, setSchema] = useState("")
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const current = MODES[mode]

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    const started =
      mode === "diff"
        ? analyzeDiff(project.id, text)
        : mode === "sql"
          ? analyzeSql(project.id, text, dialect === "generic" ? "" : dialect, schema)
          : investigateBug(project.id, text.trim())
    started
      .then((analysis) => navigate(`/impacto/${analysis.id}`))
      .catch(fail)
      .finally(() => setBusy(false))
  }

  return (
    <Section>
      <form className="grid gap-4" onSubmit={submit}>
        <Tabs value={mode} onValueChange={(value) => setMode(value as Mode)}>
          <TabsList>
            <TabsTrigger value="diff">
              <FileDiff />
              Diff de Git
            </TabsTrigger>
            <TabsTrigger value="sql">
              <Database />
              Consulta SQL
            </TabsTrigger>
            <TabsTrigger value="bug">
              <Bug />
              Descripción de bug
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <div className="grid gap-1.5">
          <Label htmlFor="analysis-input">{current.field}</Label>
          <Textarea
            id="analysis-input"
            required
            rows={current.rows}
            maxLength={current.max}
            className={mode === "bug" ? undefined : "font-mono text-xs md:text-xs"}
            placeholder={current.placeholder}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
        </div>
        {mode === "sql" && (
          <div className="grid gap-3 md:grid-cols-[auto_1fr]">
            <div className="grid content-start gap-1.5">
              <Label htmlFor="sql-dialect">Dialecto</Label>
              <Select id="sql-dialect" value={dialect} options={DIALECTS} onValueChange={setDialect} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="sql-schema">Esquema (opcional, CREATE TABLE …)</Label>
              <Textarea
                id="sql-schema"
                rows={3}
                maxLength={200000}
                className="font-mono text-xs md:text-xs"
                value={schema}
                onChange={(event) => setSchema(event.target.value)}
              />
            </div>
          </div>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={busy || !text.trim()}>
            <ScanSearch data-icon="inline-start" />
            {busy ? "Enviando…" : "Analizar impacto"}
          </Button>
          <p className="text-xs text-muted-foreground">
            Nada de lo que pegas se ejecuta ni se guarda entero.
          </p>
        </div>
        <ErrorText>{error}</ErrorText>
      </form>
    </Section>
  )
}

export function ImpactScreen(props: ScreenProps) {
  const { analysisId } = useParams()
  const [refresh, setRefresh] = useState(0)
  const finished = useCallback(() => setRefresh((value) => value + 1), [])
  return (
    <>
      <PageHeader
        title="Impacto"
        description="Antes de tocar nada: qué reglas, archivos y tests afecta un cambio, una consulta o un bug. Los hallazgos son hipótesis hasta que los revisas."
      />
      <div className="grid gap-6 lg:grid-cols-[1fr_16rem]">
        <div className="grid min-w-0 content-start gap-4">
          {analysisId ? (
            <>
              <Link
                to="/impacto"
                className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
              >
                <ArrowLeft className="size-4" />
                Nuevo análisis
              </Link>
              <Section>
                <AnalysisDetail
                  key={analysisId}
                  analysisId={analysisId}
                  onSessionLost={props.onSessionLost}
                  onFinished={finished}
                />
              </Section>
            </>
          ) : (
            <NewAnalysis {...props} />
          )}
        </div>
        <AnalysisHistory {...props} types={IMPACT_TYPES} basePath="/impacto" refresh={refresh} />
      </div>
    </>
  )
}
