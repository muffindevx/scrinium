import { History, MessageSquareText, RotateCcw, Search, Sparkles, Trash2 } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useRef, useState } from "react"
import { useLocation } from "react-router"
import { EvidenceCite } from "@/components/evidence-cite"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Input, Textarea } from "@/components/ui/input"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  ApiError,
  type IndexStatus,
  type Question,
  type SearchHit,
  askQuestion,
  deleteQuestion,
  getIndexStatus,
  getQuestion,
  listQuestions,
  rebuildIndex,
  searchProject,
} from "@/lib/api"
import { ERROR_LABELS } from "@/lib/labels"
import { POLL_MS } from "@/screens/hooks"
import { ErrorText, Eyebrow, Muted, type ScreenProps } from "@/screens/shared"

const FAILURES: Record<string, string> = {
  llm_unavailable: "El motor de IA local no está disponible. Si falta, instálalo con: scrinium tools install",
  model_error: "El modelo no devolvió una respuesta válida. Prueba a reformular la pregunta.",
  budget_exceeded:
    "El modelo agotó su límite razonando antes de responder. Prueba con una pregunta más concreta.",
  remote_budget_spent: ERROR_LABELS.remote_budget_spent,
  remote_auth: ERROR_LABELS.remote_auth,
  remote_unavailable: ERROR_LABELS.remote_unavailable,
  interrupted: "Se interrumpió al cerrar Scrinium.",
  internal_error: "Error interno.",
}

function pending(question: Question): boolean {
  return question.status === "queued" || question.status === "running"
}

// All model text is shown as plain text: never as HTML or Markdown.
function Answer({ question }: { question: Question }) {
  if (pending(question)) {
    return (
      <div className="grid gap-2">
        <StatusBadge tone="in_progress">Pensando…</StatusBadge>
        <Muted>Con el motor local suele tardar entre uno y dos minutos.</Muted>
      </div>
    )
  }
  if (question.status === "failed") {
    return (
      <StatusBadge tone="attention">
        {FAILURES[question.error_summary ?? ""] ?? "No se pudo responder."}
      </StatusBadge>
    )
  }
  const result = question.result ?? {}
  const answered = question.answer_status === "answered"
  return (
    <div className="grid gap-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        {answered ? (
          <StatusBadge tone="proposed">Respuesta con evidencia, sin verificar</StatusBadge>
        ) : (
          <StatusBadge tone="no_evidence">No se encontró evidencia en el código</StatusBadge>
        )}
        <span className="text-xs text-muted-foreground">
          Propuesta de {question.model_name ?? "la IA local"}: comprueba las citas.
        </span>
      </div>
      <p className="text-base whitespace-pre-wrap">{result.summary}</p>
      {result.model_explanation && (
        <p className="whitespace-pre-wrap text-muted-foreground">{result.model_explanation}</p>
      )}
      {(result.claims?.length ?? 0) > 0 && (
        <ul className="grid list-disc gap-1.5 pl-5">
          {result.claims?.map((claim, index) => (
            <li key={index} className="whitespace-pre-wrap">
              {claim.text}{" "}
              <span className="text-xs text-muted-foreground">
                {claim.sources.map((n) => `[${n}]`).join(" ")}
              </span>
            </li>
          ))}
        </ul>
      )}
      {(result.dropped_claims ?? 0) > 0 && (
        <p className="text-xs text-muted-foreground">
          Se quitaron {result.dropped_claims} afirmaciones del modelo que no citaban una fuente válida.
        </p>
      )}
      {(result.limitations?.length ?? 0) > 0 && (
        <div className="grid gap-1">
          <Eyebrow>Límites y deducciones del modelo</Eyebrow>
          <ul className="list-disc pl-5 text-muted-foreground">
            {result.limitations?.map((item, index) => (
              <li key={index} className="whitespace-pre-wrap">
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}
      {result.next_step && (
        <p className="text-muted-foreground">
          <span className="font-medium text-foreground">Siguiente comprobación: </span>
          {result.next_step}
        </p>
      )}
      {(result.sources?.length ?? 0) > 0 && (
        <div className="grid gap-2">
          <Eyebrow>Evidencia</Eyebrow>
          {result.sources?.map((source) => (
            <EvidenceCite
              key={source.number}
              label={`[${source.number}]`}
              path={source.path}
              startLine={source.start_line}
              endLine={source.end_line}
              commit={source.commit}
              revisionId={source.revision_id}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function AskPanel({ project, onSessionLost, initial }: ScreenProps & { initial: string | null }) {
  const [text, setText] = useState(initial ?? "")
  const [current, setCurrent] = useState<Question | null>(null)
  const [history, setHistory] = useState<Question[]>([])
  const [error, setError] = useState<string | null>(null)
  const askedInitial = useRef(false)

  const handleError = useCallback(
    (caught: unknown) => {
      if (caught instanceof ApiError) setError("No se pudo completar la operación.")
      else onSessionLost(caught)
    },
    [onSessionLost],
  )
  const refreshHistory = useCallback(() => {
    listQuestions(project.id).then(setHistory).catch(handleError)
  }, [project.id, handleError])
  useEffect(refreshHistory, [refreshHistory])

  const send = useCallback(
    (question: string) => {
      setError(null)
      askQuestion(project.id, question)
        .then((asked) => {
          setCurrent(asked)
          setText("")
          refreshHistory()
        })
        .catch(handleError)
    },
    [project.id, refreshHistory, handleError],
  )

  // A question typed on Home is sent on arrival, only once.
  useEffect(() => {
    if (initial && !askedInitial.current) {
      askedInitial.current = true
      send(initial)
    }
  }, [initial, send])

  const waiting = current !== null && pending(current)
  useEffect(() => {
    if (!waiting || current === null) return
    const timer = window.setInterval(() => {
      getQuestion(current.id)
        .then((question) => {
          setCurrent(question)
          if (!pending(question)) refreshHistory()
        })
        .catch(handleError)
    }, POLL_MS)
    return () => window.clearInterval(timer)
  }, [waiting, current, refreshHistory, handleError])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (text.trim()) send(text.trim())
  }
  const open = (id: string) => getQuestion(id).then(setCurrent).catch(handleError)
  const remove = (id: string) =>
    deleteQuestion(id)
      .then(() => {
        if (current?.id === id) setCurrent(null)
        refreshHistory()
      })
      .catch(handleError)

  return (
    <div className="grid gap-4 lg:grid-cols-[1fr_16rem]">
      <div className="grid content-start gap-4">
        <form className="grid gap-3 rounded-xl border bg-card p-4" onSubmit={submit}>
          <Textarea
            aria-label="Pregunta"
            required
            rows={2}
            maxLength={1000}
            placeholder="¿Cómo se calcula el total de una factura?"
            className="resize-none text-base md:text-base"
            value={text}
            onChange={(event) => setText(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault()
                event.currentTarget.form?.requestSubmit()
              }
            }}
          />
          <div className="flex items-center justify-between gap-2">
            <p className="text-xs text-muted-foreground">Intro para enviar, Mayús+Intro para otra línea.</p>
            <Button type="submit" disabled={waiting || !text.trim()}>
              <MessageSquareText data-icon="inline-start" />
              {waiting ? "Respondiendo…" : "Preguntar"}
            </Button>
          </div>
        </form>
        <ErrorText>{error}</ErrorText>
        {current ? (
          <Section>
            <p className="font-medium whitespace-pre-wrap">{current.question}</p>
            <Answer question={current} />
          </Section>
        ) : (
          <EmptyState icon={Sparkles} title="Pregunta lo que quieras entender">
            Scrinium responde solo con lo que hay en las fuentes del proyecto y cita cada afirmación. Si
            no encuentra evidencia, lo dice.
          </EmptyState>
        )}
      </div>
      <aside className="grid content-start gap-2">
        <p className="flex items-center gap-1.5 text-sm font-medium">
          <History className="size-4 text-muted-foreground" aria-hidden="true" />
          Historial
        </p>
        {history.length === 0 ? (
          <Muted>Sin preguntas todavía.</Muted>
        ) : (
          <ul className="grid gap-0.5">
            {history.map((question) => (
              <li
                key={question.id}
                className="group flex items-start justify-between gap-1 rounded-lg hover:bg-muted"
              >
                <button
                  type="button"
                  className="line-clamp-2 flex-1 px-2 py-1.5 text-left text-sm"
                  onClick={() => open(question.id)}
                  aria-current={current?.id === question.id}
                >
                  {question.question}
                </button>
                <Button
                  size="icon-xs"
                  variant="ghost"
                  className="mt-1 mr-1 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                  aria-label="Borrar del historial"
                  disabled={pending(question)}
                  onClick={() => remove(question.id)}
                >
                  <Trash2 />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </aside>
    </div>
  )
}

function preparation(status: IndexStatus): { tone: "verified" | "in_progress" | "attention" | "no_evidence"; text: string } {
  if (status.chunks === 0) return { tone: "no_evidence", text: "Todavía no hay nada preparado en este proyecto." }
  if (status.provider !== "ready") {
    return { tone: "attention", text: "Sin motor local: solo se busca por texto exacto." }
  }
  if (status.embedded < status.chunks) {
    return { tone: "in_progress", text: `Preparando el conocimiento: ${status.embedded} de ${status.chunks} citas de código.` }
  }
  return { tone: "verified", text: `Conocimiento preparado: ${status.chunks} citas de código.` }
}

function SearchPanel({ project, onSessionLost }: ScreenProps) {
  const [query, setQuery] = useState("")
  const [hits, setHits] = useState<SearchHit[] | null>(null)
  const [status, setStatus] = useState<IndexStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleError = useCallback(
    (caught: unknown) => {
      if (caught instanceof ApiError) setError("No se pudo completar la búsqueda.")
      else onSessionLost(caught)
    },
    [onSessionLost],
  )
  const refreshStatus = useCallback(() => {
    getIndexStatus(project.id).then(setStatus).catch(handleError)
  }, [project.id, handleError])
  useEffect(refreshStatus, [refreshStatus])

  const submit = (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    searchProject(project.id, query.trim())
      .then((result) => setHits(result.results))
      .then(refreshStatus)
      .catch(handleError)
      .finally(() => setBusy(false))
  }
  const rebuild = () => {
    setError(null)
    rebuildIndex().then(refreshStatus).catch(handleError)
  }
  const prep = status ? preparation(status) : null

  return (
    <div className="grid gap-4">
      <form className="flex flex-wrap gap-2 rounded-xl border bg-card p-4" onSubmit={submit}>
        <Input
          aria-label="Buscar en el código"
          required
          maxLength={500}
          className="h-9 min-w-48 flex-1"
          placeholder="calculate_invoice_total, o ¿dónde se calculan los impuestos?"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <Button type="submit" size="lg" disabled={busy}>
          <Search data-icon="inline-start" />
          Buscar
        </Button>
      </form>
      {prep && (
        <div className="flex flex-wrap items-center justify-between gap-2">
          <StatusBadge tone={prep.tone}>{prep.text}</StatusBadge>
          <Button size="sm" variant="ghost" onClick={rebuild}>
            <RotateCcw data-icon="inline-start" />
            Volver a preparar
          </Button>
        </div>
      )}
      <ErrorText>{error}</ErrorText>
      {hits !== null &&
        (hits.length === 0 ? (
          <EmptyState icon={Search} title="Sin resultados">
            Prueba con otras palabras o con el nombre de una función.
          </EmptyState>
        ) : (
          <div className="grid gap-2">
            {hits.map((hit) => (
              <EvidenceCite
                key={hit.chunk_id}
                path={hit.path}
                startLine={hit.start_line}
                endLine={hit.end_line}
                commit={hit.commit}
                excerpt={hit.text}
                defaultOpen
                label={hit.heading ?? undefined}
                extra={
                  <span className="flex gap-1">
                    {hit.matched_by.map((kind) => (
                      <StatusBadge key={kind} tone="no_evidence">
                        {kind === "lexical" ? "Texto exacto" : kind === "symbol" ? "Nombre de función" : "Significado"}
                      </StatusBadge>
                    ))}
                  </span>
                }
              />
            ))}
          </div>
        ))}
    </div>
  )
}

export function AskScreen(props: ScreenProps) {
  const location = useLocation()
  const initial = (location.state as { question?: string } | null)?.question ?? null
  return (
    <>
      <PageHeader
        title="Preguntar"
        description="Pregunta en lenguaje natural o busca directamente en el código. Todo se calcula en este equipo."
      />
      <Tabs defaultValue="ask">
        <TabsList>
          <TabsTrigger value="ask">
            <MessageSquareText />
            Preguntar a la IA
          </TabsTrigger>
          <TabsTrigger value="search">
            <Search />
            Buscar en el código
          </TabsTrigger>
        </TabsList>
        <TabsContent value="ask">
          <AskPanel {...props} initial={initial} />
        </TabsContent>
        <TabsContent value="search">
          <SearchPanel {...props} />
        </TabsContent>
      </Tabs>
    </>
  )
}
