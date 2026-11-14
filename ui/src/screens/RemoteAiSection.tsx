import { Cloud, KeyRound, Trash2 } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useState } from "react"
import { InfoRow, Section } from "@/components/page"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Input, Label, Textarea } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import {
  type AiConsent,
  type AiRequestRecord,
  type AiTask,
  type ModelProvider,
  type Project,
  type ProjectAi,
  type ProviderKind,
  type RetentionNotice,
  checkModelProvider,
  getProjectAi,
  getRetention,
  giveAiConsent,
  listAiRequests,
  listModelProviders,
  registerModelProvider,
  removeModelProvider,
  revokeAiConsents,
  updateProjectAiSettings,
} from "@/lib/api"
import { relativeTime } from "@/lib/time"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Eyebrow, Muted } from "@/screens/shared"

const TASK_LABELS: Record<AiTask, string> = {
  questions: "Preguntas",
  rule_map: "Extracción de reglas",
  rule_statements: "Redacción de reglas",
  consistency: "Revisión de consistencia",
  change_impact: "Impacto de cambios",
}
const TASKS = Object.keys(TASK_LABELS) as AiTask[]

const KIND_LABELS: Record<ProviderKind, string> = {
  openai_compatible: "Compatible con OpenAI",
  anthropic: "Anthropic (Claude)",
}

// Starting points only: the person can change any field before registering.
const PRESETS: { label: string; name: string; kind: ProviderKind; base_url: string }[] = [
  { label: "DeepSeek", name: "DeepSeek", kind: "openai_compatible", base_url: "https://api.deepseek.com" },
  { label: "GLM (Z.ai)", name: "GLM", kind: "openai_compatible", base_url: "https://api.z.ai/api/paas/v4" },
  { label: "Claude", name: "Claude", kind: "anthropic", base_url: "https://api.anthropic.com" },
]

const OUTCOME_LABELS: Record<string, { label: string; tone: "verified" | "attention" | "discarded" }> = {
  completed: { label: "Completada", tone: "verified" },
  length: { label: "Sin espacio para responder", tone: "attention" },
  refused: { label: "Rechazada por el proveedor", tone: "discarded" },
  invalid: { label: "Respuesta no válida", tone: "attention" },
  unavailable: { label: "No respondió", tone: "attention" },
  budget: { label: "Presupuesto agotado", tone: "attention" },
  error: { label: "Error", tone: "discarded" },
}

const CHECK_LABELS = {
  ready: "Responde",
  model_missing: "Rechazó la clave o el modelo",
  unavailable: "No responde",
} as const

function tokens(value: number) {
  return value.toLocaleString("es")
}

function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(timer)
  }, [value, ms])
  return debounced
}

/** What the provider says it keeps, read before consenting (ADR-010). */
function RetentionBox({ name, notice }: { name: string; notice: RetentionNotice }) {
  return (
    <div className="grid gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">Qué guarda {name}</span>
        {notice.verified ? (
          <StatusBadge tone={notice.stale ? "attention" : "verified"}>
            {notice.stale ? "Comprobado hace más de 6 meses" : `Comprobado el ${notice.checked_on}`}
          </StatusBadge>
        ) : (
          <StatusBadge tone="attention">Sin verificar</StatusBadge>
        )}
      </div>
      <p className="whitespace-pre-line">{notice.notice}</p>
      {notice.sources.length > 0 && (
        <ul className="grid gap-0.5 text-xs text-muted-foreground">
          {notice.sources.map((source) => (
            <li key={source} className="break-all">
              Fuente: {source}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

function ProvidersSection({
  providers,
  keyring,
  onChanged,
  onSessionLost,
}: {
  providers: ModelProvider[]
  keyring: boolean
  onChanged: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [name, setName] = useState("")
  const [kind, setKind] = useState<ProviderKind>("openai_compatible")
  const [baseUrl, setBaseUrl] = useState("")
  const [apiKey, setApiKey] = useState("")
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const preset = (index: number) => {
    const chosen = PRESETS[index]
    setName(chosen.name)
    setKind(chosen.kind)
    setBaseUrl(chosen.base_url)
  }
  const register = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    registerModelProvider({ name: name.trim(), kind, base_url: baseUrl.trim(), api_key: apiKey.trim() })
      .then(() => {
        setName("")
        setBaseUrl("")
        onChanged()
      })
      .catch(fail)
      .finally(() => {
        // The key leaves the screen's memory whatever the result.
        setApiKey("")
        setBusy(false)
      })
  }
  const remove = (provider: ModelProvider) => {
    setError(null)
    removeModelProvider(provider.id).then(onChanged).catch(fail)
  }
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <KeyRound className="size-4" />
          Proveedores de IA externos
        </span>
      }
      description="Opcional. Registrar un proveedor no envía nada: cada proyecto decide después si lo usa y para qué. Las claves se guardan en el llavero del sistema, nunca en los datos de Scrinium."
    >
      {providers.length > 0 && (
        <ul className="grid gap-2">
          {providers.map((provider) => (
            <li
              key={provider.id}
              className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm"
            >
              <span className="grid min-w-0">
                <span className="font-medium">
                  {provider.name} <span className="font-normal text-muted-foreground">· {KIND_LABELS[provider.kind]}</span>
                </span>
                <code className="truncate text-xs text-muted-foreground">
                  {provider.base_url}
                  {provider.has_key ? ` · clave …${provider.key_hint ?? ""}` : " · sin clave"}
                </code>
              </span>
              <Button size="sm" variant="ghost" onClick={() => remove(provider)} aria-label={`Quitar ${provider.name}`}>
                <Trash2 data-icon="inline-start" />
                Quitar
              </Button>
            </li>
          ))}
        </ul>
      )}
      {keyring ? (
        <form className="grid gap-3" onSubmit={register} autoComplete="off">
          <div className="flex flex-wrap items-center gap-2">
            <Eyebrow>Registrar</Eyebrow>
            {PRESETS.map((option, index) => (
              <Button key={option.label} type="button" size="xs" variant="outline" onClick={() => preset(index)}>
                {option.label}
              </Button>
            ))}
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="grid gap-1.5">
              <Label htmlFor="provider-name">Nombre</Label>
              <Input id="provider-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="provider-kind">Tipo de API</Label>
              <Select
                id="provider-kind"
                value={kind}
                options={Object.entries(KIND_LABELS).map(([value, label]) => ({ value, label }))}
                onValueChange={(value) => setKind(value as ProviderKind)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="provider-url">Dirección (https)</Label>
              <Input
                id="provider-url"
                required
                type="url"
                placeholder="https://api.ejemplo.com/v1"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
              />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="provider-key">Clave de API</Label>
              <Input
                id="provider-key"
                required
                type="password"
                autoComplete="new-password"
                spellCheck={false}
                value={apiKey}
                onChange={(e) => setApiKey(e.target.value)}
              />
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" variant="outline" disabled={busy || !name.trim() || !baseUrl.trim() || !apiKey.trim()}>
              {busy ? "Guardando…" : "Registrar"}
            </Button>
            <Muted>La clave se envía una vez, va al llavero y no vuelve a mostrarse.</Muted>
          </div>
        </form>
      ) : (
        <StatusBadge tone="attention">
          No hay llavero del sistema: sin él no se pueden registrar proveedores externos.
        </StatusBadge>
      )}
      <ErrorText>{error}</ErrorText>
    </Section>
  )
}

function ConsentForm({
  project,
  providers,
  onGiven,
  onSessionLost,
}: {
  project: Project
  providers: ModelProvider[]
  onGiven: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [providerId, setProviderId] = useState(providers[0]?.id ?? "")
  const [model, setModel] = useState("")
  const [tasks, setTasks] = useState<AiTask[]>(["questions"])
  const [loaded, setLoaded] = useState<{ key: string; notice: RetentionNotice } | null>(null)
  const [acknowledged, setAcknowledged] = useState(false)
  const [check, setCheck] = useState<keyof typeof CHECK_LABELS | "running" | null>(null)
  const { error, setError, fail } = useErrors(onSessionLost)
  const provider = providers.find((candidate) => candidate.id === providerId) ?? null
  const current = `${providerId}\n${model.trim()}`
  const chosen = useDebounced(current, 400)
  // The notice shown is always the one for what is chosen now: a stale one is never shown.
  const notice = loaded?.key === current ? loaded.notice : null

  // Choosing another provider or model asks to read its notice and test it again.
  const changeProvider = (id: string) => {
    setProviderId(id)
    setAcknowledged(false)
    setCheck(null)
  }
  const changeModel = (name: string) => {
    setModel(name)
    setAcknowledged(false)
    setCheck(null)
  }
  useEffect(() => {
    const [id, name] = chosen.split("\n")
    if (!id || !name) return
    let live = true
    getRetention(id, name)
      .then((retention) => live && setLoaded({ key: chosen, notice: retention }))
      .catch(fail)
    return () => {
      live = false
    }
  }, [chosen, fail])

  const toggle = (task: AiTask, on: boolean) =>
    setTasks((current) => (on ? [...current, task] : current.filter((value) => value !== task)))
  const runCheck = () => {
    if (!provider) return
    setError(null)
    setCheck("running")
    checkModelProvider(provider.id, model.trim())
      .then((result) => setCheck(result.status))
      .catch((failure: unknown) => {
        setCheck(null)
        fail(failure)
      })
  }
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!provider) return
    setError(null)
    giveAiConsent(project.id, {
      provider_id: provider.id,
      model: model.trim(),
      tasks,
      retention_acknowledged: acknowledged,
    })
      .then(() => {
        changeModel("")
        onGiven()
      })
      .catch(fail)
  }
  const ready = provider !== null && model.trim() !== "" && notice !== null

  return (
    <form className="grid gap-3 rounded-lg border p-3" onSubmit={submit}>
      <Eyebrow>Usar un proveedor en este proyecto</Eyebrow>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="grid gap-1.5">
          <Label htmlFor="consent-provider">Proveedor</Label>
          <Select
            id="consent-provider"
            value={providerId}
            options={providers.map((candidate) => ({ value: candidate.id, label: candidate.name }))}
            onValueChange={changeProvider}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="consent-model">Modelo</Label>
          <Input
            id="consent-model"
            required
            maxLength={200}
            spellCheck={false}
            placeholder={provider?.kind === "anthropic" ? "p. ej. claude-sonnet-5-5" : "p. ej. deepseek-chat"}
            value={model}
            onChange={(e) => changeModel(e.target.value)}
          />
        </div>
      </div>
      <fieldset className="grid gap-1.5">
        <legend className="mb-1.5 text-sm font-medium">Tareas que salen a este modelo</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1.5">
          {TASKS.map((task) => (
            <label key={task} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                className="size-4 accent-primary"
                checked={tasks.includes(task)}
                onChange={(e) => toggle(task, e.target.checked)}
              />
              {TASK_LABELS[task]}
            </label>
          ))}
        </div>
        <Muted>La búsqueda y los vectores se calculan siempre aquí. Las demás tareas siguen en local.</Muted>
      </fieldset>
      {provider && notice && <RetentionBox name={provider.name} notice={notice} />}
      {provider && notice && (
        <p className="text-sm">
          Trata lo que se envíe como compartido con un tercero: los pasajes de código y documentos que
          necesite cada tarea salen a <strong>{provider.host}</strong>. Scrinium quita antes las claves que
          reconoce, nunca envía las rutas que marques abajo y guarda aquí un registro de cada envío.
        </p>
      )}
      {notice && (
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={acknowledged}
            onChange={(e) => setAcknowledged(e.target.checked)}
          />
          He leído qué guarda el proveedor y acepto enviarle esas tareas de este proyecto.
        </label>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={!ready || !acknowledged || tasks.length === 0}>
          Permitir
        </Button>
        <Button type="button" variant="outline" disabled={!ready || check === "running"} onClick={runCheck}>
          {check === "running" ? "Probando…" : "Probar conexión"}
        </Button>
        {check !== null && check !== "running" && (
          <StatusBadge tone={check === "ready" ? "verified" : "attention"}>{CHECK_LABELS[check]}</StatusBadge>
        )}
      </div>
      {ready && <Muted>Probar conexión envía una sola frase fija, nada del proyecto.</Muted>}
      <ErrorText>{error}</ErrorText>
    </form>
  )
}

function ConsentList({ consents }: { consents: AiConsent[] }) {
  return (
    <ul className="grid gap-2">
      {consents.map((consent) => (
        <li key={consent.id} className="grid gap-1 rounded-lg border p-3 text-sm">
          <span className="flex flex-wrap items-center gap-2">
            <span className="font-medium">{consent.provider_name}</span>
            <code className="text-xs text-muted-foreground">
              {consent.model} · {consent.host}
            </code>
          </span>
          <span>{consent.tasks.map((task) => TASK_LABELS[task]).join(", ")}</span>
          <span className="text-xs text-muted-foreground">
            Permitido {relativeTime(consent.created_at)}
            {consent.consented_by ? ` por ${consent.consented_by}` : ""}
          </span>
          <details className="text-xs">
            <summary className="cursor-pointer text-muted-foreground">Qué guarda el proveedor (aviso aceptado)</summary>
            <p className="mt-1 whitespace-pre-line">{consent.retention.notice}</p>
          </details>
        </li>
      ))}
    </ul>
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
      setError("El presupuesto debe ser un número entero de tokens, o quedar vacío.")
      return
    }
    updateProjectAiSettings(project.id, { never_send: patterns, remote_token_budget: limit })
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
          <Label htmlFor="never-send">Rutas que nunca se envían</Label>
          <Textarea
            id="never-send"
            rows={4}
            spellCheck={false}
            placeholder={"contracts/\n*.sql"}
            value={paths}
            onChange={(e) => setPaths(e.target.value)}
          />
          <Muted>Una por línea. Se indexan y se buscan aquí; una tarea que las necesite se resuelve en local.</Muted>
        </div>
        <div className="grid content-start gap-1.5">
          <Label htmlFor="token-budget">Presupuesto mensual de tokens</Label>
          <Input
            id="token-budget"
            inputMode="numeric"
            placeholder="Sin límite"
            value={budget}
            onChange={(e) => setBudget(e.target.value)}
          />
          <Muted>
            Este mes: {tokens(spent)} tokens{limit !== null ? ` de ${tokens(limit)}` : ""}. Al llegar al límite no
            se envía nada más hasta el mes siguiente.
          </Muted>
          {limit !== null && spent >= limit && <StatusBadge tone="attention">Presupuesto agotado</StatusBadge>}
        </div>
      </div>
      <div className="flex items-center gap-3">
        <Button type="submit" variant="outline">
          Guardar límites
        </Button>
        {saved && <StatusBadge tone="verified">Guardado</StatusBadge>}
      </div>
      <ErrorText>{error}</ErrorText>
    </form>
  )
}

function RequestLog({ requests }: { requests: AiRequestRecord[] }) {
  if (requests.length === 0) return <Muted>Todavía no se ha enviado nada desde este proyecto.</Muted>
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
            <code className="text-xs text-muted-foreground">
              {request.answered_by || request.model} · {request.host} · {tokens(request.prompt_tokens)} tokens enviados
              {request.cached_tokens > 0 ? ` (${tokens(request.cached_tokens)} en caché)` : ""} ·{" "}
              {tokens(request.completion_tokens)} recibidos
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

function ProjectAiSection({
  project,
  providers,
  ai,
  onAiChanged,
  onSessionLost,
}: {
  project: Project
  providers: ModelProvider[]
  ai: ProjectAi
  onAiChanged: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [requests, setRequests] = useState<AiRequestRecord[] | null>(null)
  const [confirming, setConfirming] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  useEffect(() => {
    listAiRequests(project.id).then(setRequests).catch(fail)
  }, [project.id, ai, fail])
  const revoke = () => {
    setError(null)
    revokeAiConsents(project.id)
      .then(() => {
        setConfirming(false)
        onAiChanged()
      })
      .catch(fail)
  }
  const remote = ai.consents.length > 0
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <Cloud className="size-4" />
          IA de este proyecto
        </span>
      }
      description={`Qué tareas de «${project.name}» pueden usar un modelo externo. Sin permiso, todo se resuelve en este equipo.`}
    >
      <dl className="divide-y">
        <InfoRow label="Modo">
          {remote ? (
            <StatusBadge tone="attention">Envía tareas a un proveedor externo</StatusBadge>
          ) : (
            <StatusBadge tone="verified">Solo local</StatusBadge>
          )}
        </InfoRow>
      </dl>
      {remote && (
        <div className="grid gap-2">
          <ConsentList consents={ai.consents} />
          <div className="flex flex-wrap items-center gap-2">
            {confirming ? (
              <>
                <Button variant="destructive" onClick={revoke}>
                  Sí, volver a solo local
                </Button>
                <Button variant="ghost" onClick={() => setConfirming(false)}>
                  Cancelar
                </Button>
              </>
            ) : (
              <Button variant="outline" onClick={() => setConfirming(true)}>
                Volver a solo local
              </Button>
            )}
            <Muted>Desde la siguiente petición todo vuelve a resolverse aquí. Lo ya enviado no se puede recuperar.</Muted>
          </div>
        </div>
      )}
      {providers.length > 0 ? (
        <ConsentForm project={project} providers={providers} onGiven={onAiChanged} onSessionLost={onSessionLost} />
      ) : (
        <Muted>Para usar un modelo externo, registra antes un proveedor.</Muted>
      )}
      <div className="grid gap-2">
        <Eyebrow>Límites</Eyebrow>
        <LimitsForm key={project.id} project={project} ai={ai} onSaved={onAiChanged} onSessionLost={onSessionLost} />
      </div>
      <div className="grid gap-2">
        <Eyebrow>Registro de envíos</Eyebrow>
        <Muted>Qué salió de este equipo: cuándo, a dónde, qué pasajes y cuántos tokens. Nunca el contenido.</Muted>
        {requests && <RequestLog requests={requests} />}
      </div>
      <ErrorText>{error}</ErrorText>
    </Section>
  )
}

/** Remote providers (workspace) and the current project's consent, limits and record. */
export function RemoteAiSections({
  project,
  onAiChanged,
  onSessionLost,
}: {
  project: Project | null
  onAiChanged: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [providers, setProviders] = useState<ModelProvider[]>([])
  const [keyring, setKeyring] = useState(true)
  const [ai, setAi] = useState<ProjectAi | null>(null)
  const { error, fail } = useErrors(onSessionLost)
  const loadProviders = useCallback(() => {
    listModelProviders()
      .then((loaded) => {
        setProviders(loaded.providers)
        setKeyring(loaded.keyring)
      })
      .catch(fail)
  }, [fail])
  const loadAi = useCallback(() => {
    if (!project) return
    getProjectAi(project.id).then(setAi).catch(fail)
  }, [project, fail])
  useEffect(loadProviders, [loadProviders])
  useEffect(loadAi, [loadAi])
  const changed = () => {
    loadProviders()
    loadAi()
    onAiChanged()
  }
  return (
    <>
      {project && ai && (
        <ProjectAiSection
          key={project.id}
          project={project}
          providers={providers}
          ai={ai}
          onAiChanged={changed}
          onSessionLost={onSessionLost}
        />
      )}
      <ProvidersSection providers={providers} keyring={keyring} onChanged={changed} onSessionLost={onSessionLost} />
      <ErrorText>{error}</ErrorText>
    </>
  )
}
