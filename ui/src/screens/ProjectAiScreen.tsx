import { t } from "@/lib/i18n"
import { Activity, Cpu, Gauge, Info } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useMemo, useState } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { PageHeader, Section } from "@/components/page"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { InfoPopover } from "@/components/ui/info-popover"
import { Input, Label, Textarea } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { LOCAL, OUTCOME_LABELS, TASKS, TASK_LABELS, choiceOf, currentChoice, permission, tokens } from "@/lib/ai"
import {
  type AiRequestRecord,
  type AiTask,
  type ModelProvider,
  type Project,
  type ProjectAi,
  getProjectAi,
  listAiRequests,
  listModelProviders,
  revokeAiConsent,
  revokeAiConsents,
  setTaskDefault,
  taskToLocal,
  updateProjectAiSettings,
} from "@/lib/api"
import { relativeTime } from "@/lib/time"
import { RetentionDetails } from "@/screens/ai-shared"
import { ConsentDialog } from "@/screens/ConsentDialog"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted } from "@/screens/shared"

const TASK_HELP: Record<AiTask, string> = {
  questions: t("Responder preguntas sobre el proyecto, con citas."),
  rule_map: t("Leer el código y proponer reglas."),
  rule_statements: t("Redactar el enunciado de cada regla."),
  consistency: t("Buscar reglas duplicadas o contradictorias."),
  change_impact: t("Revisar qué reglas toca un cambio de código."),
  check_explain: t("Explicar en una frase cómo cambia una regla en scrinium check --explain."),
}

function TaskRow({
  task,
  ai,
  options,
  onChoose,
}: {
  task: AiTask
  ai: ProjectAi
  options: { value: string; label: string }[]
  onChoose: (task: AiTask, choice: string) => void
}) {
  const chosen = ai.task_models[task]
  const consent = chosen ? permission(ai, task, chosen.provider_id, chosen.model) : undefined
  return (
    <li className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 py-3 first:pt-0 last:pb-0">
      <span className="grid min-w-0 flex-1 gap-0.5">
        <span className="font-medium">{TASK_LABELS[task]}</span>
        <span className="text-xs text-muted-foreground">{TASK_HELP[task]}</span>
        {consent && (
          <span className="flex items-center gap-1 text-xs text-muted-foreground">
            {t("Permitido")} {relativeTime(consent.created_at)}
            {consent.consented_by ? t(" por {0}", consent.consented_by) : ""}
            <InfoPopover label={t("Aviso aceptado")} title={t("Lo que se aceptó de {0}", consent.provider_name)}>
              <RetentionDetails notice={consent.retention} />
            </InfoPopover>
          </span>
        )}
      </span>
      <Select
        aria-label={t("Modelo para {0}", TASK_LABELS[task])}
        value={currentChoice(ai, task)}
        options={options}
        onValueChange={(choice) => onChoose(task, choice)}
        className="w-60 max-sm:w-full"
      />
    </li>
  )
}

/** The models this project has allowed, for which tasks; each can be withdrawn. */
function Permissions({
  project,
  ai,
  onChanged,
  onSessionLost,
}: {
  project: Project
  ai: ProjectAi
  onChanged: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [confirming, setConfirming] = useState<string | null>(null)
  const { error, fail } = useErrors(onSessionLost)
  const withdraw = (consentId: string) =>
    revokeAiConsent(project.id, consentId)
      .then(() => {
        setConfirming(null)
        onChanged()
      })
      .catch(fail)
  return (
    <div className="grid gap-2 border-t pt-4">
      <span className="flex items-center gap-1 text-sm font-medium">
        {t("Modelos permitidos")}
        <InfoPopover label={t("Qué es un modelo permitido")} title={t("Modelos permitidos")}>
          <p>
            {t("Un modelo permitido puede elegirse al extraer reglas, en Opciones avanzadas, aunque no sea el de por defecto. Retirarlo hace que esas tareas vuelvan a este equipo.")}
          </p>
        </InfoPopover>
      </span>
      <ul className="grid gap-2">
        {ai.consents.map((consent) => (
          <li key={consent.id} className="grid gap-1 rounded-lg border p-3 text-sm">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="min-w-0">
                <span className="font-medium">{consent.provider_name}</span>{" "}
                <code className="text-xs text-muted-foreground">{consent.model}</code>
              </span>
              {confirming === consent.id ? (
                <span className="flex items-center gap-1">
                  <Button size="xs" variant="destructive" onClick={() => withdraw(consent.id)}>
                    {t("Retirar")}
                  </Button>
                  <Button size="xs" variant="ghost" onClick={() => setConfirming(null)}>
                    {t("Cancelar")}
                  </Button>
                </span>
              ) : (
                <Button size="xs" variant="ghost" onClick={() => setConfirming(consent.id)}>
                  {t("Retirar permiso")}
                </Button>
              )}
            </div>
            <span className="text-xs text-muted-foreground">
              {consent.tasks.map((task) => TASK_LABELS[task]).join(", ")} · {relativeTime(consent.created_at)}
              {consent.consented_by ? t(" por {0}", consent.consented_by) : ""}
            </span>
          </li>
        ))}
      </ul>
      <ErrorText>{error}</ErrorText>
    </div>
  )
}

function ModelsTab({
  project,
  ai,
  providers,
  onChanged,
  onSessionLost,
}: {
  project: Project
  ai: ProjectAi
  providers: ModelProvider[]
  onChanged: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [asking, setAsking] = useState<{ provider: ModelProvider; model: string; tasks: AiTask[] } | null>(null)
  const [confirming, setConfirming] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const options = useMemo(
    () => [
      { value: LOCAL, label: t("Local (este equipo)") },
      ...providers.flatMap((provider) =>
        provider.models.map((option) => ({
          value: choiceOf(provider.id, option.model),
          label: `${provider.name} · ${option.model}`,
        })),
      ),
    ],
    [providers],
  )
  const choose = (task: AiTask, choice: string) => {
    setError(null)
    if (choice === currentChoice(ai, task)) return
    if (choice === LOCAL) {
      // Nothing leaves: no consent needed.
      taskToLocal(project.id, task).then(onChanged).catch(fail)
      return
    }
    const [providerId, model] = [choice.slice(0, choice.indexOf(" ")), choice.slice(choice.indexOf(" ") + 1)]
    const provider = providers.find((candidate) => candidate.id === providerId)
    if (!provider) return
    if (permission(ai, task, providerId, model)) {
      // Already allowed for this task: it only becomes the default.
      setTaskDefault(project.id, task, providerId, model).then(onChanged).catch(fail)
      return
    }
    setAsking({ provider, model, tasks: [task] })
  }
  const allLocal = () =>
    revokeAiConsents(project.id)
      .then(() => {
        setConfirming(false)
        onChanged()
      })
      .catch(fail)
  const hasModels = options.length > 1
  return (
    <Section
      title={t("Modelo de cada tarea")}
      description={t("Sin elegir otro, todo se resuelve en este equipo. La búsqueda y los vectores se calculan siempre aquí.")}
      action={
        ai.consents.length > 0 ? (
          confirming ? (
            <span className="flex items-center gap-1">
              <Button size="sm" variant="destructive" onClick={allLocal}>
                {t("Sí, todo en local")}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
                {t("Cancelar")}
              </Button>
            </span>
          ) : (
            <Button size="sm" variant="outline" onClick={() => setConfirming(true)}>
              {t("Todo en local")}
            </Button>
          )
        ) : undefined
      }
    >
      {confirming && <Muted>{t("Desde la siguiente petición todo se resuelve aquí. Lo ya enviado no se puede recuperar.")}</Muted>}
      <ul className="grid divide-y">
        {TASKS.map((task) => (
          <TaskRow key={task} task={task} ai={ai} options={options} onChoose={choose} />
        ))}
      </ul>
      {ai.consents.length > 0 && (
        <Permissions project={project} ai={ai} onChanged={onChanged} onSessionLost={onSessionLost} />
      )}
      {!hasModels && (
        <p className="flex items-center gap-1.5 text-sm text-muted-foreground">
          <Info className="size-4 shrink-0" aria-hidden="true" />
          <span>
            {t("Para usar un modelo externo, añádelo antes en")}{" "}
            <Link to="/ajustes/modelos" className="font-medium text-foreground underline-offset-4 hover:underline">
              {t("Ajustes → Modelos de IA")}
            </Link>
            .
          </span>
        </p>
      )}
      <ErrorText>{error}</ErrorText>
      {asking && (
        <ConsentDialog
          project={project}
          provider={asking.provider}
          model={asking.model}
          tasks={asking.tasks}
          onClose={() => setAsking(null)}
          onGiven={() => {
            setAsking(null)
            onChanged()
          }}
          onSessionLost={onSessionLost}
        />
      )}
    </Section>
  )
}

function LimitsForm({
  project,
  ai,
  onSaved,
  onSessionLost,
}: {
  project: Project
  ai: ProjectAi
  onSaved: (ai: ProjectAi) => void
  onSessionLost: (error: unknown) => void
}) {
  const [paths, setPaths] = useState(ai.never_send.join("\n"))
  const [budget, setBudget] = useState(ai.remote_token_budget === null ? "" : String(ai.remote_token_budget))
  const [maskPersonal, setMaskPersonal] = useState(ai.mask_personal)
  const [maskLiterals, setMaskLiterals] = useState(ai.mask_literals)
  const [saved, setSaved] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const save = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setSaved(false)
    const patterns = paths
      .split("\n")
      .map((line) => line.trim())
      .filter(Boolean)
    const limit = budget.trim() === "" ? null : Number(budget.trim())
    if (limit !== null && (!Number.isInteger(limit) || limit < 0)) {
      setError(t("El presupuesto debe ser un número entero de tokens, o quedar vacío."))
      return
    }
    updateProjectAiSettings(project.id, {
      never_send: patterns,
      remote_token_budget: limit,
      mask_personal: maskPersonal,
      mask_literals: maskLiterals,
    })
      .then((updated) => {
        setSaved(true)
        onSaved(updated)
      })
      .catch(fail)
  }
  const spent = ai.remote_tokens_this_month
  const limit = ai.remote_token_budget
  return (
    <form className="grid gap-3" onSubmit={save}>
      <div className="grid gap-3 md:grid-cols-2">
        <div className="grid content-start gap-1.5">
          <Label htmlFor="never-send">{t("Rutas que nunca se envían")}</Label>
          <Textarea
            id="never-send"
            rows={4}
            spellCheck={false}
            placeholder={"contracts/\n*.sql"}
            value={paths}
            onChange={(e) => setPaths(e.target.value)}
          />
          <Muted>{t("Una por línea. Se indexan y se buscan aquí; una tarea que las necesite se resuelve en local.")}</Muted>
        </div>
        <div className="grid content-start gap-1.5">
          <Label htmlFor="token-budget">{t("Presupuesto mensual de tokens")}</Label>
          <Input
            id="token-budget"
            inputMode="numeric"
            placeholder={t("Sin límite")}
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
          <Muted>
            {t("Este mes:")} {tokens(spent)} tokens{limit !== null ? t(" de {0}", tokens(limit)) : ""}{t(". Al llegar al límite no se envía nada más hasta el mes siguiente.")}
          </Muted>
          {limit !== null && spent >= limit && <StatusBadge tone="attention">{t("Presupuesto agotado")}</StatusBadge>}
          {ai.cache_hits > 0 && (
            <Muted>
              {ai.cache_hits === 1 ? t("1 respuesta reutilizada") : t("{0} respuestas reutilizadas", tokens(ai.cache_hits))}{" "}
              {t("sin enviar nada: la misma petición ya tenía respuesta.")}
            </Muted>
          )}
        </div>
      </div>
      <fieldset className="grid gap-1.5">
        <legend className="mb-1.5 text-sm font-medium">{t("Enmascarar antes de enviar")}</legend>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={maskPersonal}
            onChange={(e) => setMaskPersonal(e.target.checked)}
          />
          {t("Datos personales: correos, teléfonos, direcciones IP y URL con usuario y contraseña")}
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={maskLiterals}
            onChange={(e) => setMaskLiterals(e.target.checked)}
          />
          {t("Textos largos entre comillas (24 caracteres o más)")}
        </label>
        <Muted>
          {t("Cada valor sale como un marcador (")}&lt;EMAIL_1&gt;{t(") y vuelve a su sitio en la respuesta. La tabla de equivalencias no sale de este equipo ni se guarda. Los nombres de funciones y tablas no se tocan.")}
        </Muted>
      </fieldset>
      <div className="flex items-center gap-3">
        <Button type="submit" variant="outline">
          {t("Guardar límites")}
        </Button>
        {saved && <StatusBadge tone="verified">{t("Guardado")}</StatusBadge>}
      </div>
      <ErrorText>{error}</ErrorText>
    </form>
  )
}

function RequestLog({ requests }: { requests: AiRequestRecord[] }) {
  if (requests.length === 0) return <Muted>{t("Todavía no se ha enviado nada desde este proyecto.")}</Muted>
  return (
    <ul className="grid divide-y rounded-lg border text-sm">
      {requests.map((request) => {
        const outcome = OUTCOME_LABELS[request.outcome] ?? { label: request.outcome, tone: "attention" as const }
        return (
          <li key={request.id} className="grid gap-1 p-3">
            <span className="flex flex-wrap items-center justify-between gap-2">
              <span>
                <span className="font-medium">{TASK_LABELS[request.task] ?? request.task}</span>{" "}
                <span className="text-muted-foreground">· {relativeTime(request.created_at)}</span>
              </span>
              <StatusBadge tone={outcome.tone}>{outcome.label}</StatusBadge>
            </span>
            <code className="text-xs break-words text-muted-foreground">
              {request.answered_by || request.model} · {request.host} · {tokens(request.prompt_tokens)} {t("tokens enviados")}
              {request.cached_tokens > 0 ? t(" ({0} en caché)", tokens(request.cached_tokens)) : ""} ·{" "}
              {tokens(request.completion_tokens)} {t("recibidos")}
              {request.masked > 0 ? ` · ${request.masked === 1 ? t("1 valor enmascarado") : t("{0} valores enmascarados", request.masked)}` : ""}
            </code>
            {request.sources.length > 0 && (
              <details className="text-xs">
                <summary className="cursor-pointer text-muted-foreground">
                  {request.sources.length} {request.sources.length === 1 ? "pasaje" : "pasajes"}
                </summary>
                <ul className="mt-1 grid gap-0.5">
                  {request.sources.map((source) => (
                    <li key={source}>
                      <code className="break-all">{source}</code>
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        )
      })}
    </ul>
  )
}

function ActivityTab({
  project,
  ai,
  onSessionLost,
}: {
  project: Project
  ai: ProjectAi
  onSessionLost: (error: unknown) => void
}) {
  const [requests, setRequests] = useState<AiRequestRecord[] | null>(null)
  const [task, setTask] = useState("all")
  const [outcome, setOutcome] = useState("all")
  const { error, fail } = useErrors(onSessionLost)
  useEffect(() => {
    listAiRequests(project.id).then(setRequests).catch(fail)
  }, [project.id, fail])
  const shown = (requests ?? []).filter(
    (request) => (task === "all" || request.task === task) && (outcome === "all" || request.outcome === outcome),
  )
  return (
    <Section
      title={t("Qué salió de este equipo")}
      description={t("Cada envío a un modelo externo: cuándo, a dónde, qué pasajes y cuántos tokens. Nunca el contenido.")}
    >
      <dl className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-3">
        <div className="grid gap-0.5 rounded-lg border p-3">
          <dt className="text-xs text-muted-foreground">{t("Tokens este mes")}</dt>
          <dd className="font-medium">
            {tokens(ai.remote_tokens_this_month)}
            {ai.remote_token_budget !== null && (
              <span className="font-normal text-muted-foreground"> {t("de")} {tokens(ai.remote_token_budget)}</span>
            )}
          </dd>
        </div>
        <div className="grid gap-0.5 rounded-lg border p-3">
          <dt className="text-xs text-muted-foreground">{t("Envíos registrados")}</dt>
          <dd className="font-medium">{tokens(requests?.length ?? 0)}</dd>
        </div>
        <div className="grid gap-0.5 rounded-lg border p-3">
          <dt className="text-xs text-muted-foreground">{t("Respuestas reutilizadas")}</dt>
          <dd className="font-medium">{tokens(ai.cache_hits)}</dd>
        </div>
      </dl>
      <div className="flex flex-wrap gap-2">
        <Select
          aria-label={t("Filtrar por tarea")}
          value={task}
          options={[{ value: "all", label: t("Todas las tareas") }, ...TASKS.map((value) => ({ value, label: TASK_LABELS[value] }))]}
          onValueChange={setTask}
        />
        <Select
          aria-label={t("Filtrar por resultado")}
          value={outcome}
          options={[
            { value: "all", label: t("Todos los resultados") },
            ...Object.entries(OUTCOME_LABELS).map(([value, { label }]) => ({ value, label })),
          ]}
          onValueChange={setOutcome}
        />
      </div>
      {requests && <RequestLog requests={shown} />}
      <ErrorText>{error}</ErrorText>
    </Section>
  )
}

const TABS = [
  { value: "modelos", label: t("Modelos"), icon: Cpu },
  { value: "limites", label: t("Límites"), icon: Gauge },
  { value: "actividad", label: t("Actividad"), icon: Activity },
] as const

/** The project's AI: which model each task uses, what never leaves, and what did. */
export function ProjectAiScreen({
  project,
  onAiChanged,
  onSessionLost,
}: {
  project: Project
  onAiChanged: () => void
  onSessionLost: (error: unknown) => void
}) {
  const { tab = "modelos" } = useParams()
  const navigate = useNavigate()
  const current = TABS.some((option) => option.value === tab) ? tab : "modelos"
  const [ai, setAi] = useState<ProjectAi | null>(null)
  const [providers, setProviders] = useState<ModelProvider[]>([])
  const { error, fail } = useErrors(onSessionLost)
  const load = useCallback(() => {
    getProjectAi(project.id).then(setAi).catch(fail)
    listModelProviders()
      .then((loaded) => setProviders(loaded.providers))
      .catch(fail)
  }, [project.id, fail])
  useEffect(load, [load])
  const changed = () => {
    load()
    onAiChanged()
  }
  return (
    <>
      <PageHeader
        title={t("IA del proyecto")}
        description={t("Qué modelo usa cada tarea de «{0}», qué nunca sale de este equipo y qué salió.", project.name)}
      />
      <Tabs value={current} onValueChange={(value) => navigate(`/ia/${value}`)}>
        <TabsList aria-label={t("Secciones de IA del proyecto")}>
          {TABS.map(({ value, label, icon: Icon }) => (
            <TabsTrigger key={value} value={value}>
              <Icon />
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {ai === null ? (
        error ? (
          <ErrorText>{error}</ErrorText>
        ) : (
          <Muted>{t("Cargando…")}</Muted>
        )
      ) : current === "modelos" ? (
        <ModelsTab project={project} ai={ai} providers={providers} onChanged={changed} onSessionLost={onSessionLost} />
      ) : current === "limites" ? (
        <Section
          title={t("Límites")}
          description={t("Se aplican a todo lo que este proyecto envíe a un modelo externo.")}
        >
          <LimitsForm key={project.id} project={project} ai={ai} onSaved={changed} onSessionLost={onSessionLost} />
        </Section>
      ) : (
        <ActivityTab project={project} ai={ai} onSessionLost={onSessionLost} />
      )}
    </>
  )
}
