import { t } from "@/lib/i18n"
import { ArrowLeft, Bug, FileDiff, ListTree, ScanSearch, Ticket } from "lucide-react"
import { type FormEvent, useCallback, useState } from "react"
import { Link, useNavigate, useParams, useSearchParams } from "react-router"
import { PageHeader, Section } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Label, Textarea } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { type AnalysisType, type Triage, analyzeDiff, investigateBug, triageTrace } from "@/lib/api"
import { TicketTriage } from "@/screens/TicketTriage"
import { TraceResult } from "@/screens/TraceResult"
import { AnalysisHistory } from "@/screens/AnalysisHistory"
import { useErrors } from "@/screens/hooks"
import { AnalysisDetail, ErrorText, type ScreenProps } from "@/screens/shared"

type Mode = "trace" | "diff" | "bug" | "ticket"

// SQL review left the screen; its past analyses stay readable in the history.
const IMPACT_TYPES: AnalysisType[] = ["code_diff", "sql_review", "bug_investigation", "impact_analysis"]


const MODES: Record<Exclude<Mode, "ticket">, { label: string; field: string; placeholder: string; rows: number; max: number }> = {
  trace: {
    label: t("Traza de error"),
    field: t("Pega la traza (Python, JavaScript o Java/Kotlin)"),
    placeholder: 'Traceback (most recent call last):\n  File "/srv/app/src/pagos.py", line 12, in comision',
    rows: 10,
    max: 20000,
  },
  diff: {
    label: t("Diff de Git"),
    field: t("Pega la salida de git diff"),
    placeholder: "diff --git a/src/billing.py b/src/billing.py\n…",
    rows: 12,
    max: 500000,
  },
  bug: {
    label: t("Descripción de bug"),
    field: t("Describe qué falla"),
    placeholder: t("El total de la factura sale con demasiados decimales"),
    rows: 4,
    max: 2000,
  },
}

function NewAnalysis({ project, onSessionLost }: ScreenProps) {
  const navigate = useNavigate()
  // The tab lives in the URL, so going back from a result returns to the same one.
  const [params, setParams] = useSearchParams()
  const asked = params.get("modo")
  const mode: Mode = asked === "bug" || asked === "diff" || asked === "ticket" ? asked : "trace"
  const setMode = (value: Mode) => setParams(value === "trace" ? {} : { modo: value }, { replace: true })
  const [traced, setTraced] = useState<Triage | null>(null)
  const [text, setText] = useState("")
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const current = MODES[mode === "ticket" ? "trace" : mode]

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    if (mode === "trace") {
      triageTrace(project.id, text)
        .then(setTraced)
        .catch(fail)
        .finally(() => setBusy(false))
      return
    }
    const started = mode === "diff" ? analyzeDiff(project.id, text) : investigateBug(project.id, text.trim())
    started
      .then((analysis) => navigate(`/impacto/${analysis.id}`))
      .catch(fail)
      .finally(() => setBusy(false))
  }

  const tabs = (
        <Tabs value={mode} onValueChange={(value) => setMode(value as Mode)}>
          <TabsList>
            <TabsTrigger value="trace">
              <ListTree />
              {t("Traza de error")}
            </TabsTrigger>
            <TabsTrigger value="diff">
              <FileDiff />
              {t("Diff de Git")}
            </TabsTrigger>
            <TabsTrigger value="bug">
              <Bug />
              {t("Descripción de bug")}
            </TabsTrigger>
            <TabsTrigger value="ticket">
              <Ticket />
              {t("Ticket de bug")}
            </TabsTrigger>
          </TabsList>
        </Tabs>
  )
  if (mode === "ticket") {
    return (
      <Section>
        <div className="grid gap-4">
          {tabs}
          <TicketTriage projectId={project.id} onSessionLost={onSessionLost} />
        </div>
      </Section>
    )
  }
  return (
    <Section>
      <form className="grid gap-4" onSubmit={submit}>
        {tabs}
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
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={busy || !text.trim()}>
            <ScanSearch data-icon="inline-start" />
            {busy ? t("Enviando…") : mode === "trace" ? t("Seguir la traza") : t("Analizar impacto")}
          </Button>
          <p className="text-xs text-muted-foreground">
            {t("Nada de lo que pegas se ejecuta ni se guarda entero.")}
          </p>
        </div>
        <ErrorText>{error}</ErrorText>
      </form>
      {mode === "trace" && traced && <TraceResult result={traced} />}
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
        title={t("Triaje")}
        description={t("Antes de tocar nada: a qué funciones, reglas y cambios recientes lleva una traza de error, y qué reglas y tests afecta un cambio o un bug. Los hallazgos son hipótesis hasta que los revisas.")}
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
                {t("Nuevo análisis")}
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
