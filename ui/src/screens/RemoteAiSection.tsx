import { KeyRound, Pencil, Plus, Trash2 } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useState } from "react"
import { Section } from "@/components/page"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { InfoPopover } from "@/components/ui/info-popover"
import { Input, Label } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { KIND_LABELS, PRESETS, kindFor } from "@/lib/ai"
import {
  type ModelProvider,
  type ProviderCheck,
  type ProviderKind,
  addProviderModel,
  checkModelProvider,
  listModelProviders,
  registerModelProvider,
  removeModelProvider,
  removeProviderModel,
  updateModelProvider,
} from "@/lib/api"
import { CheckResult } from "@/screens/ai-shared"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted } from "@/screens/shared"

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

/** The workspace's external providers and their models, for Ajustes. */
export function ModelCatalog({ onSessionLost }: { onSessionLost: (error: unknown) => void }) {
  const [providers, setProviders] = useState<ModelProvider[]>([])
  const [keyring, setKeyring] = useState(true)
  const { error, fail } = useErrors(onSessionLost)
  const load = useCallback(() => {
    listModelProviders()
      .then((loaded) => {
        setProviders(loaded.providers)
        setKeyring(loaded.keyring)
      })
      .catch(fail)
  }, [fail])
  useEffect(load, [load])
  return (
    <>
      <ProvidersSection providers={providers} keyring={keyring} onChanged={load} onSessionLost={onSessionLost} />
      <ErrorText>{error}</ErrorText>
    </>
  )
}
