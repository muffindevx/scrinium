import { Cloud, KeyRound, Pencil, Plus, Trash2 } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useState } from "react"
import { InfoRow, Section } from "@/components/page"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Input, Label, Textarea } from "@/components/ui/input"
import { Dialog } from "@/components/ui/dialog"
import { InfoPopover } from "@/components/ui/info-popover"
import { Select } from "@/components/ui/select"
import {
  type AiConsent,
  type AiRequestRecord,
  type AiTask,
  type ModelProvider,
  type Project,
  type ProjectAi,
  type ProviderCheck,
  type ProviderKind,
  type RetentionNotice,
  addProviderModel,
  checkModelProvider,
  getProjectAi,
  getRetention,
  giveAiConsent,
  listAiRequests,
  listModelProviders,
  registerModelProvider,
  removeModelProvider,
  removeProviderModel,
  revokeAiConsents,
  updateModelProvider,
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

const CHECK_LABELS: Record<ProviderCheck["reason"], string> = {
  ok: "Responde",
  key_rejected: "Rechazó la clave: revísala o vuelve a registrar el proveedor",
  model_not_found: "No encuentra ese modelo, o tu clave no tiene acceso a él",
  request_rejected: "Rechazó la petición",
  unavailable: "No responde ahora",
  no_room: "No le bastó el espacio de respuesta",
  no_answer: "No dio una respuesta válida",
}

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

function RetentionBadge({ notice }: { notice: RetentionNotice }) {
  if (!notice.verified) return <StatusBadge tone="attention">Sin verificar</StatusBadge>
  return (
    <StatusBadge tone={notice.stale ? "attention" : "verified"}>
      {notice.stale ? "Comprobado hace más de 6 meses" : `Comprobado el ${notice.checked_on}`}
    </StatusBadge>
  )
}

/** What the provider says it keeps, read before consenting (ADR-010), in a popover. */
function RetentionDetails({ notice }: { notice: RetentionNotice }) {
  return (
    <>
      <RetentionBadge notice={notice} />
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
    </>
  )
}

// Hosts whose own API Scrinium speaks; any other provider is reached through its
// OpenAI-compatible endpoint, which nearly all of them offer.
function kindFor(url: string): ProviderKind {
  try {
    const host = new URL(url).hostname
    return host === "anthropic.com" || host.endsWith(".anthropic.com") ? "anthropic" : "openai_compatible"
  } catch {
    return "openai_compatible"
  }
}

const API_TYPE_HELP = (
  <>
    <p>
      Es el formato en que Scrinium habla con el proveedor, no la marca del modelo. Se elige solo al escribir la
      dirección.
    </p>
    <p>
      <strong>Compatible con OpenAI</strong> es el formato que ofrecen casi todos: DeepSeek, GLM (Z.ai), Mistral,
      Groq, OpenRouter, Together y muchos más. Su documentación lo dice como «OpenAI compatible» o muestra una
      dirección que termina en <code>/v1</code> con <code>/chat/completions</code>.
    </p>
    <p>
      <strong>Anthropic</strong> es solo para Claude, con su API propia.
    </p>
    <p className="text-muted-foreground">
      Si un proveedor solo tiene su propia API, sin modo compatible con OpenAI, Scrinium todavía no puede usarlo.
    </p>
  </>
)

/** Add or edit a provider. Editing keeps the key unless a new one is typed; another host needs
 * one, since a key goes only to the host it was given for. */
function ProviderDialog({
  open,
  provider,
  onClose,
  onSaved,
  onSessionLost,
}: {
  open: boolean
  provider: ModelProvider | null
  onClose: () => void
  onSaved: (notice: string | null) => void
  onSessionLost: (error: unknown) => void
}) {
  const [name, setName] = useState(provider?.name ?? "")
  const [kind, setKind] = useState<ProviderKind>(provider?.kind ?? "openai_compatible")
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? "")
  const [apiKey, setApiKey] = useState("")
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const editing = provider !== null
  let newHost = false
  try {
    newHost = editing && baseUrl.trim() !== "" && new URL(baseUrl.trim()).hostname !== provider.host
  } catch {
    newHost = false
  }
  const preset = (index: number) => {
    const chosen = PRESETS[index]
    setName(chosen.name)
    setKind(chosen.kind)
    setBaseUrl(chosen.base_url)
  }
  const save = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    const done = editing
      ? updateModelProvider(provider.id, {
          name: name.trim(),
          kind,
          base_url: baseUrl.trim(),
          ...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
        }).then((updated) =>
          onSaved(
            updated.consents_revoked > 0
              ? `Cambió el servidor: se retiraron ${updated.consents_revoked} permisos de proyectos, que tendrán que darse de nuevo.`
              : null,
          ),
        )
      : registerModelProvider({ name: name.trim(), kind, base_url: baseUrl.trim(), api_key: apiKey.trim() }).then(
          () => onSaved(null),
        )
    done.catch(fail).finally(() => {
      // The key leaves the screen's memory whatever the result.
      setApiKey("")
      setBusy(false)
    })
  }
  const keyNeeded = !editing || newHost
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => !next && onClose()}
      title={editing ? `Editar ${provider.name}` : "Añadir proveedor"}
      description={
        editing
          ? undefined
          : "Registrar un proveedor no envía nada: cada proyecto decide después si lo usa y para qué."
      }
    >
      <form className="grid gap-3" onSubmit={save} autoComplete="off">
        {!editing && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs text-muted-foreground">Rellenar con</span>
            {PRESETS.map((option, index) => (
              <Button key={option.label} type="button" size="xs" variant="outline" onClick={() => preset(index)}>
                {option.label}
              </Button>
            ))}
          </div>
        )}
        <div className="grid gap-1.5">
          <Label htmlFor="provider-name">Nombre</Label>
          <Input id="provider-name" required maxLength={100} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="provider-url">Dirección (https)</Label>
          <Input
            id="provider-url"
            required
            type="url"
            placeholder="https://api.ejemplo.com/v1"
            value={baseUrl}
            onChange={(e) => {
              setBaseUrl(e.target.value)
              setKind(kindFor(e.target.value))
            }}
          />
        </div>
        <div className="grid gap-1.5">
          <span className="flex items-center gap-1">
            <Label htmlFor="provider-kind">Tipo de API</Label>
            <InfoPopover label="Qué tipo de API elegir" title="Qué tipo de API elegir">
              {API_TYPE_HELP}
            </InfoPopover>
          </span>
          <Select
            id="provider-kind"
            value={kind}
            options={Object.entries(KIND_LABELS).map(([value, label]) => ({ value, label }))}
            onValueChange={(value) => setKind(value as ProviderKind)}
          />
        </div>
        <div className="grid gap-1.5">
          <Label htmlFor="provider-key">{editing ? "Nueva clave de API" : "Clave de API"}</Label>
          <Input
            id="provider-key"
            required={keyNeeded}
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            placeholder={editing && !newHost ? `Vacía: se mantiene la clave …${provider.key_hint ?? ""}` : undefined}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
          />
          <Muted>
            {newHost
              ? "Cambia el servidor: hace falta su clave, y los proyectos tendrán que volver a dar permiso."
              : "Va al llavero del sistema y no vuelve a mostrarse."}
          </Muted>
        </div>
        <ErrorText>{error}</ErrorText>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            Cancelar
          </Button>
          <Button type="submit" disabled={busy || !name.trim() || !baseUrl.trim() || (keyNeeded && !apiKey.trim())}>
            {busy ? "Guardando…" : editing ? "Guardar" : "Añadir"}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

/** Add a model of a provider, with a connection test before saving it. */
function ModelDialog({
  provider,
  onClose,
  onSaved,
  onSessionLost,
}: {
  provider: ModelProvider | null
  onClose: () => void
  onSaved: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [model, setModel] = useState("")
  const [check, setCheck] = useState<ProviderCheck | "running" | null>(null)
  const { error, setError, fail } = useErrors(onSessionLost)
  if (!provider) return null
  const runCheck = () => {
    setError(null)
    setCheck("running")
    checkModelProvider(provider.id, model.trim())
      .then(setCheck)
      .catch((failure: unknown) => {
        setCheck(null)
        fail(failure)
      })
  }
  const save = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    addProviderModel(provider.id, model.trim()).then(onSaved).catch(fail)
  }
  return (
    <Dialog
      open
      onOpenChange={(next) => !next && onClose()}
      title={`Añadir un modelo de ${provider.name}`}
      description="El nombre exacto que da el proveedor en su documentación. Añadirlo no envía nada."
    >
      <form className="grid gap-3" onSubmit={save} autoComplete="off">
        <div className="grid gap-1.5">
          <Label htmlFor="model-name">Modelo</Label>
          <Input
            id="model-name"
            required
            maxLength={200}
            spellCheck={false}
            placeholder={provider.kind === "anthropic" ? "p. ej. claude-sonnet-5-5" : "p. ej. deepseek-v4-flash"}
            value={model}
            onChange={(e) => {
              setModel(e.target.value)
              setCheck(null)
            }}
          />
        </div>
        <CheckResult check={check} />
        <ErrorText>{error}</ErrorText>
        <div className="flex flex-wrap justify-end gap-2">
          <Button type="button" variant="outline" disabled={!model.trim() || check === "running"} onClick={runCheck}>
            {check === "running" ? "Probando…" : "Probar conexión"}
          </Button>
          <Button type="submit" disabled={!model.trim()}>
            Añadir
          </Button>
        </div>
        <Muted>Probar conexión envía una sola frase fija, nada de ningún proyecto, y gasta unos pocos tokens.</Muted>
      </form>
    </Dialog>
  )
}

function CheckResult({ check }: { check: ProviderCheck | "running" | null }) {
  if (check === null || check === "running") return null
  return (
    <div className="grid gap-1">
      <StatusBadge tone={check.status === "ready" ? "verified" : "attention"}>{CHECK_LABELS[check.reason]}</StatusBadge>
      {check.status !== "ready" && (check.detail || check.http_status) && (
        <p className="text-xs break-words text-muted-foreground">
          Respuesta del proveedor{check.http_status ? ` (HTTP ${check.http_status}` : ""}
          {check.error_type ? `, ${check.error_type}` : ""}
          {check.http_status ? ")" : ""}: {check.detail || "sin detalle"}
        </p>
      )}
    </div>
  )
}

function ModelRow({
  provider,
  model,
  onChanged,
  onSessionLost,
}: {
  provider: ModelProvider
  model: { id: string; model: string }
  onChanged: (notice: string | null) => void
  onSessionLost: (error: unknown) => void
}) {
  const [check, setCheck] = useState<ProviderCheck | "running" | null>(null)
  const [confirming, setConfirming] = useState(false)
  const { error, fail } = useErrors(onSessionLost)
  const runCheck = () => {
    setCheck("running")
    checkModelProvider(provider.id, model.model)
      .then(setCheck)
      .catch((failure: unknown) => {
        setCheck(null)
        fail(failure)
      })
  }
  const remove = () =>
    removeProviderModel(provider.id, model.id)
      .then((result) =>
        onChanged(
          result.consents_revoked > 0
            ? `Se quitó ${model.model} y se retiraron ${result.consents_revoked} permisos de proyectos que lo usaban.`
            : null,
        ),
      )
      .catch(fail)
  return (
    <li className="grid gap-1.5 py-2 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <code className="text-sm">{model.model}</code>
        <span className="flex items-center gap-1">
          {confirming ? (
            <>
              <Button size="xs" variant="destructive" onClick={remove}>
                Quitar
              </Button>
              <Button size="xs" variant="ghost" onClick={() => setConfirming(false)}>
                Cancelar
              </Button>
            </>
          ) : (
            <>
              <Button size="xs" variant="ghost" disabled={check === "running"} onClick={runCheck}>
                {check === "running" ? "Probando…" : "Probar"}
              </Button>
              <Button
                size="icon-xs"
                variant="ghost"
                aria-label={`Quitar ${model.model}`}
                title="Quitar"
                onClick={() => setConfirming(true)}
              >
                <Trash2 />
              </Button>
            </>
          )}
        </span>
      </div>
      {confirming && <Muted>Los proyectos que lo usan dejarán de enviarle tareas y volverán a local.</Muted>}
      <CheckResult check={check} />
      <ErrorText>{error}</ErrorText>
    </li>
  )
}

function ProviderCard({
  provider,
  onEdit,
  onAddModel,
  onChanged,
  onSessionLost,
}: {
  provider: ModelProvider
  onEdit: () => void
  onAddModel: () => void
  onChanged: (notice: string | null) => void
  onSessionLost: (error: unknown) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const { error, fail } = useErrors(onSessionLost)
  const remove = () =>
    removeModelProvider(provider.id)
      .then(() => onChanged(null))
      .catch(fail)
  return (
    <li className="grid gap-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <span className="grid min-w-0">
          <span className="font-medium">
            {provider.name} <span className="font-normal text-muted-foreground">· {KIND_LABELS[provider.kind]}</span>
          </span>
          <code className="truncate text-xs text-muted-foreground">
            {provider.base_url}
            {provider.has_key ? ` · clave …${provider.key_hint ?? ""}` : " · sin clave"}
          </code>
        </span>
        <span className="flex items-center gap-1">
          <Button size="sm" variant="ghost" onClick={onEdit}>
            <Pencil data-icon="inline-start" />
            Editar
          </Button>
          <Button
            size="icon-sm"
            variant="ghost"
            aria-label={`Quitar ${provider.name}`}
            title="Quitar"
            onClick={() => setConfirming(true)}
          >
            <Trash2 />
          </Button>
        </span>
      </div>
      {confirming && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-destructive/40 p-2 text-sm">
          <span className="flex-1">
            Se borra su clave del llavero y los proyectos que lo usan vuelven a local.
          </span>
          <Button size="sm" variant="destructive" onClick={remove}>
            Quitar {provider.name}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setConfirming(false)}>
            Cancelar
          </Button>
        </div>
      )}
      <div className="grid gap-2 border-t pt-3">
        {provider.models.length > 0 ? (
          <ul className="grid divide-y">
            {provider.models.map((model) => (
              <ModelRow
                key={model.id}
                provider={provider}
                model={model}
                onChanged={onChanged}
                onSessionLost={onSessionLost}
              />
            ))}
          </ul>
        ) : (
          <Muted>Todavía sin modelos.</Muted>
        )}
        <Button size="sm" variant="outline" className="justify-self-start" onClick={onAddModel}>
          <Plus data-icon="inline-start" />
          Añadir modelo
        </Button>
      </div>
      <ErrorText>{error}</ErrorText>
    </li>
  )
}

/** The workspace's external providers and their models: add, edit, test and remove. */
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
  // "new", a provider being edited, or closed. The key changes the form's state on each open.
  const [editing, setEditing] = useState<ModelProvider | "new" | null>(null)
  const [opened, setOpened] = useState(0)
  const [addingModelTo, setAddingModelTo] = useState<ModelProvider | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const open = (target: ModelProvider | "new") => {
    setNotice(null)
    setOpened((count) => count + 1)
    setEditing(target)
  }
  const changed = (message: string | null) => {
    setNotice(message)
    onChanged()
  }
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <KeyRound className="size-4" />
          Proveedores externos
        </span>
      }
      description="Opcional. Las claves se guardan en el llavero del sistema, nunca en los datos de Scrinium."
      action={
        keyring ? (
          <Button size="sm" variant="outline" onClick={() => open("new")}>
            <Plus data-icon="inline-start" />
            Añadir proveedor
          </Button>
        ) : undefined
      }
    >
      {!keyring && (
        <StatusBadge tone="attention">
          No hay llavero del sistema: sin él no se pueden registrar proveedores externos.
        </StatusBadge>
      )}
      {notice && (
        <p role="status" className="text-sm text-amber-700 dark:text-amber-400">
          {notice}
        </p>
      )}
      {providers.length > 0 ? (
        <ul className="grid gap-3">
          {providers.map((provider) => (
            <ProviderCard
              key={provider.id}
              provider={provider}
              onEdit={() => open(provider)}
              onAddModel={() => setAddingModelTo(provider)}
              onChanged={changed}
              onSessionLost={onSessionLost}
            />
          ))}
        </ul>
      ) : (
        keyring && <Muted>Ninguno todavía. Todo se resuelve con el modelo local.</Muted>
      )}
      <ProviderDialog
        key={opened}
        open={editing !== null}
        provider={editing === "new" ? null : editing}
        onClose={() => setEditing(null)}
        onSaved={(message) => {
          setEditing(null)
          changed(message)
        }}
        onSessionLost={onSessionLost}
      />
      <ModelDialog
        key={addingModelTo?.id ?? "none"}
        provider={addingModelTo}
        onClose={() => setAddingModelTo(null)}
        onSaved={() => {
          setAddingModelTo(null)
          changed(null)
        }}
        onSessionLost={onSessionLost}
      />
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
  const [check, setCheck] = useState<ProviderCheck | "running" | null>(null)
  const { error, setError, fail } = useErrors(onSessionLost)
  const provider = providers.find((candidate) => candidate.id === providerId) ?? null
  const current = `${providerId}\n${model.trim()}`
  const chosen = useDebounced(current, 400)
  // The notice shown is always the one for what is chosen now: a stale one is never shown. The
  // layout does not move while it loads: only the popover's content and the checkbox wait.
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
      .then(setCheck)
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
            list="consent-models"
            value={model}
            onChange={(e) => changeModel(e.target.value)}
          />
          <datalist id="consent-models">
            {provider?.models.map((option) => <option key={option.id} value={option.model} />)}
          </datalist>
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
      {provider && (
        <div className="grid gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-sm">
          <p className="flex items-start gap-1.5">
            <span>
              Lo que se envíe queda en manos de un tercero: los pasajes que necesite cada tarea salen a{" "}
              <strong>{provider.host}</strong>.
            </span>
            <InfoPopover label={`Qué guarda ${provider.name}`} title={`Qué guarda ${provider.name}`}>
              {notice ? (
                <RetentionDetails notice={notice} />
              ) : (
                <p className="text-muted-foreground">
                  {model.trim() ? "Cargando el aviso de este modelo…" : "Escribe el modelo para ver su aviso."}
                </p>
              )}
              <p className="text-xs text-muted-foreground">
                Antes de enviar, Scrinium quita las claves que reconoce y nunca envía las rutas marcadas en Límites. Cada
                envío queda anotado en el registro de abajo.
              </p>
            </InfoPopover>
          </p>
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-primary"
              checked={acknowledged}
              disabled={notice === null}
              onChange={(e) => setAcknowledged(e.target.checked)}
            />
            <span>
              He leído qué guarda {provider.name} (en el ⓘ) y acepto enviarle esas tareas de este proyecto.
            </span>
          </label>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" disabled={!ready || !acknowledged || tasks.length === 0}>
          Permitir
        </Button>
        <Button type="button" variant="outline" disabled={!ready || check === "running"} onClick={runCheck}>
          {check === "running" ? "Probando…" : "Probar conexión"}
        </Button>
      </div>
      <CheckResult check={check} />
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
      setError("El presupuesto debe ser un número entero de tokens, o quedar vacío.")
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
          {ai.cache_hits > 0 && (
            <Muted>
              {tokens(ai.cache_hits)} {ai.cache_hits === 1 ? "respuesta reutilizada" : "respuestas reutilizadas"} sin
              enviar nada: la misma petición ya tenía respuesta.
            </Muted>
          )}
        </div>
      </div>
      <fieldset className="grid gap-1.5">
        <legend className="mb-1.5 text-sm font-medium">Enmascarar antes de enviar</legend>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={maskPersonal}
            onChange={(e) => setMaskPersonal(e.target.checked)}
          />
          Datos personales: correos, teléfonos, direcciones IP y URL con usuario y contraseña
        </label>
        <label className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-primary"
            checked={maskLiterals}
            onChange={(e) => setMaskLiterals(e.target.checked)}
          />
          Textos largos entre comillas (24 caracteres o más)
        </label>
        <Muted>
          Cada valor sale como un marcador (&lt;EMAIL_1&gt;) y vuelve a su sitio en la respuesta. La tabla de
          equivalencias no sale de este equipo ni se guarda. Los nombres de funciones y tablas no se tocan.
        </Muted>
      </fieldset>
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
            <code className="text-xs break-words text-muted-foreground">
              {request.answered_by || request.model} · {request.host} · {tokens(request.prompt_tokens)} tokens enviados
              {request.cached_tokens > 0 ? ` (${tokens(request.cached_tokens)} en caché)` : ""} ·{" "}
              {tokens(request.completion_tokens)} recibidos
              {request.masked > 0 ? ` · ${request.masked} ${request.masked === 1 ? "valor enmascarado" : "valores enmascarados"}` : ""}
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
            <StatusBadge tone="attention">Con IA externa</StatusBadge>
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
      <ProvidersSection providers={providers} keyring={keyring} onChanged={changed} onSessionLost={onSessionLost} />
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
      <ErrorText>{error}</ErrorText>
    </>
  )
}
