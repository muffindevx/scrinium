import { t } from "@/lib/i18n"
import { Loader2, Lock, ShieldCheck } from "lucide-react"
import { type ReactNode, useCallback, useEffect, useState } from "react"
import { Link } from "react-router"
import { cn } from "cn"
import { Button } from "@/components/ui/button"
import { Confirm } from "@/components/ui/confirm"
import { Input, Label } from "@/components/ui/input"
import {
  type AiSetup,
  ApiError,
  type ModelProvider,
  type SecurityStatus,
  checkLocalServer,
  checkSearchServer,
  chooseAiSetup,
  chooseSearch,
  getIsolationEffects,
  listModelProviders,
  setIsolation,
} from "@/lib/api"
import { ModelCatalog } from "@/screens/RemoteAiSection"
import { ErrorText, Muted } from "@/screens/shared"

const ERRORS: Record<string, string> = {
  local_server_not_loopback: t("El servidor tiene que estar en este equipo: http://127.0.0.1:puerto o http://localhost:puerto, sin ruta."),
  invalid_model: t("Escribe el nombre del modelo tal como lo sirve el servidor, sin espacios."),
  isolated_mode: t("Bloqueado por el modo aislado."),
  isolated_managed: t("Tu organización impuso el modo aislado en este equipo: no se puede cambiar desde aquí."),
  provider_not_found: t("Ese proveedor ya no existe. Elige otro."),
  search_provider_not_acknowledged: t("Marca que entiendes que se envía todo el texto indexado."),
}

const OLLAMA_URL = "https://ollama.com/download"

type Keyring = { keyring: boolean; keyring_usable: boolean; in_docker: boolean }

function Tag({ tone = "plain", children }: { tone?: "plain" | "warning" | "good"; children: ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-full px-2 py-0.5 text-xs",
        tone === "plain" && "bg-muted text-muted-foreground",
        tone === "warning" && "bg-amber-500/15 text-amber-700 dark:text-amber-400",
        tone === "good" && "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
      )}
    >
      {children}
    </span>
  )
}

function Pill({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-full px-2.5 py-0.5 text-xs font-medium",
        ok ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground",
      )}
    >
      {children}
    </span>
  )
}

function Command({ children }: { children: string }) {
  return <pre className="overflow-x-auto rounded-md bg-muted px-3 py-2 font-mono text-xs">{children}</pre>
}

/** One option of a radio list: a title, its consequences as tags, and its form when chosen. */
function Choice({
  name,
  value,
  selected,
  onSelect,
  title,
  tags,
  blocked,
  current,
  children,
}: {
  name: string
  value: string
  selected: boolean
  onSelect: (value: string) => void
  title: string
  tags: ReactNode
  blocked?: string | null
  current?: boolean
  children?: ReactNode
}) {
  return (
    <li className={cn("grid gap-3 rounded-lg border p-3", selected && "border-primary/50 bg-primary/5", blocked && "opacity-60")}>
      <label className="flex cursor-pointer items-start gap-3">
        <input
          type="radio"
          name={name}
          value={value}
          checked={selected}
          disabled={Boolean(blocked)}
          onChange={() => onSelect(value)}
          className="mt-1 accent-primary"
        />
        <span className="grid gap-1.5">
          <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
            {title}
            {current && <span className="text-xs text-primary">{t("· en uso")}</span>}
            {blocked && (
              <span className="flex items-center gap-1 text-xs text-muted-foreground">
                <Lock className="size-3" aria-hidden="true" />
                {blocked}
              </span>
            )}
          </span>
          <span className="flex flex-wrap gap-1.5">{tags}</span>
        </span>
      </label>
      {selected && !blocked && children && <div className="grid gap-3 pl-7 text-sm">{children}</div>}
    </li>
  )
}

/** URL and model of a server on this machine, with a test and the button that uses it. */
function LocalServerForm({
  initialUrl,
  initialModel,
  modelHint,
  check,
  onUse,
  useLabel,
}: {
  initialUrl: string
  initialModel: string
  modelHint: string
  check: (url: string, model: string) => Promise<string>
  onUse: (url: string, model: string) => Promise<void>
  useLabel: string
}) {
  const [url, setUrl] = useState(initialUrl)
  const [model, setModel] = useState(initialModel)
  const [probe, setProbe] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const test = () => {
    setProbe(t("Probando…"))
    check(url.trim(), model.trim())
      .then(setProbe)
      .catch(() => setProbe(t("No se pudo probar.")))
  }
  const use = () => {
    setBusy(true)
    onUse(url.trim(), model.trim()).finally(() => setBusy(false))
  }
  return (
    <div className="grid gap-2">
      <div className="grid gap-2 sm:grid-cols-2">
        <div className="grid gap-1">
          <Label htmlFor={`${useLabel}-url`}>{t("Dirección")}</Label>
          <Input id={`${useLabel}-url`} value={url} onChange={(event) => setUrl(event.target.value)} />
        </div>
        <div className="grid gap-1">
          <Label htmlFor={`${useLabel}-model`}>{t("Modelo")}</Label>
          <Input
            id={`${useLabel}-model`}
            value={model}
            placeholder={modelHint}
            onChange={(event) => setModel(event.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="outline" disabled={!url.trim() || !model.trim()} onClick={test}>
          {t("Probar conexión")}
        </Button>
        <Button size="sm" disabled={busy || !url.trim() || !model.trim()} onClick={use}>
          {busy && <Loader2 className="animate-spin" data-icon="inline-start" />}
          {useLabel}
        </Button>
        {probe && <span className="text-xs text-muted-foreground">{probe}</span>}
      </div>
    </div>
  )
}

function DockerLocalSteps({ show }: { show: boolean }) {
  if (!show) return null
  return (
    <div className="grid gap-1.5 rounded-md border border-dashed p-3">
      <p className="text-xs font-medium">{t("Estás en Docker: el servidor corre fuera del contenedor.")}</p>
      <p className="text-xs text-muted-foreground">{t("En Linux, haz que escuche en la dirección de Docker y arranca Scrinium con su puerto:")}</p>
      <Command>{"OLLAMA_HOST=172.17.0.1:11434 ollama serve\nsh docker/scrinium.sh start --model-port 11434"}</Command>
      <p className="text-xs text-muted-foreground">{t("Aquí la dirección sigue siendo http://127.0.0.1:11434.")}</p>
    </div>
  )
}

/** Why the provider cannot be used yet, and the one step that fixes it. Never a silent button. */
function KeyringNotice({ keyring }: { keyring: Keyring | null }) {
  if (!keyring || keyring.keyring_usable) return null
  if (keyring.in_docker)
    return (
      <div className="grid gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
        <p className="text-xs font-medium">
          {t("El llavero del contenedor no está abierto (o aún no existe): sin él, las claves no se pueden guardar ni usar.")}
        </p>
        <p className="text-xs text-muted-foreground">{t("En la terminal, ábrelo (la primera vez lo crea) y recarga esta página:")}</p>
        <Command>{"sh docker/scrinium.sh unlock"}</Command>
      </div>
    )
  return (
    <div className="grid gap-1.5 rounded-md border border-amber-500/40 bg-amber-500/5 p-3">
      <p className="text-xs font-medium">{t("Este equipo no tiene un llavero del sistema disponible o está cerrado.")}</p>
      <p className="text-xs text-muted-foreground">{t("Scrinium solo guarda claves en el llavero del sistema (Llavero de macOS, Administrador de credenciales de Windows o Secret Service en Linux). Desbloquéalo e inténtalo de nuevo.")}</p>
    </div>
  )
}

function statusText(status: string): string {
  if (status === "ready") return t("Responde y tiene el modelo.")
  if (status === "model_missing") return t("Responde, pero no sirve ese modelo.")
  if (status === "not_embeddings") return t("Responde, pero ese modelo no devuelve vectores: elige uno de embeddings.")
  return t("No responde en esa dirección.")
}

function AnswerModel({
  setup,
  status,
  keyring,
  onChanged,
  fail,
}: {
  setup: AiSetup
  status: SecurityStatus
  keyring: Keyring | null
  onChanged: (setup: AiSetup) => void
  fail: (caught: unknown) => void
}) {
  const [selected, setSelected] = useState<string>(setup.choice ?? "local_server")
  const blocked = setup.isolated ? t("Bloqueado por el modo aislado") : null
  const working = setup.choice === "provider" || (setup.choice !== null && status.providers.llm === "ready")
  const pill =
    setup.choice === null
      ? t("Sin configurar")
      : setup.choice === "provider"
        ? t("Con tu proveedor")
        : status.providers.llm === "ready"
          ? t("Funcionando")
          : t("No responde")
  const choose = (choice: "provider" | "bundled") => chooseAiSetup(choice).then(onChanged).catch(fail)
  return (
    <section className="grid gap-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-heading text-base font-medium">{t("1 · Modelo de respuestas")}</h2>
        <Pill ok={working}>{pill}</Pill>
      </div>
      <ul className="grid gap-2">
        <Choice
          name="answer"
          value="local_server"
          selected={selected === "local_server"}
          current={setup.choice === "local_server"}
          onSelect={setSelected}
          title={t("Un servidor en tu equipo")}
          tags={
            <>
              <Tag tone="good">{t("El código no sale de aquí")}</Tag>
              <Tag>{t("Gratis")}</Tag>
              <Tag tone="warning">{t("Necesita GPU o Mac con memoria")}</Tag>
            </>
          }
        >
          <p className="text-muted-foreground">
            {t("Ollama, llama.cpp o vLLM con un modelo para código. Escribe el nombre tal como lo sirve.")}{" "}
            <a href={OLLAMA_URL} target="_blank" rel="noreferrer" className="font-medium text-foreground underline-offset-4 hover:underline">
              {t("Cómo instalar Ollama")}
            </a>
          </p>
          <DockerLocalSteps show={Boolean(keyring?.in_docker)} />
          <LocalServerForm
            initialUrl={setup.local_server?.url ?? "http://127.0.0.1:11434"}
            initialModel={setup.local_server?.model ?? ""}
            modelHint={t("el nombre que muestra «ollama list»")}
            useLabel={t("Usar este servidor")}
            check={(url, model) => checkLocalServer(url, model).then(({ status: probe }) => statusText(probe))}
            onUse={(url, model) => chooseAiSetup("local_server", { url, model }).then(onChanged).catch(fail)}
          />
        </Choice>
        <Choice
          name="answer"
          value="provider"
          selected={selected === "provider"}
          current={setup.choice === "provider"}
          onSelect={setSelected}
          blocked={blocked}
          title={t("Un proveedor en internet")}
          tags={
            <>
              <Tag tone="warning">{t("El código sale hacia el proveedor")}</Tag>
              <Tag>{t("Pagas por uso, con tu clave")}</Tag>
              <Tag>{t("Sin GPU")}</Tag>
              <Tag>{t("Permiso en cada proyecto")}</Tag>
            </>
          }
        >
          <KeyringNotice keyring={keyring} />
          {keyring?.keyring_usable && (
            <>
              <p className="text-muted-foreground">
                {t("DeepSeek, GLM, Claude o cualquier API compatible con OpenAI. Añádelo aquí con su clave; después cada proyecto da permiso en")}{" "}
                <Link to="/ia" className="font-medium text-foreground underline-offset-4 hover:underline">
                  {t("IA del proyecto")}
                </Link>
                {t(". Nada se envía antes.")}
              </p>
              {setup.choice !== "provider" && (
                <div>
                  <Button size="sm" onClick={() => choose("provider")}>
                    {t("Usar mi proveedor")}
                  </Button>
                </div>
              )}
            </>
          )}
        </Choice>
        <Choice
          name="answer"
          value="bundled"
          selected={selected === "bundled"}
          current={setup.choice === "bundled"}
          onSelect={setSelected}
          title={t("El modelo de prueba incluido")}
          tags={
            <>
              <Tag tone="good">{t("El código no sale de aquí")}</Tag>
              <Tag>{t("2,5 GB")}</Tag>
              <Tag tone="warning">{t("Pequeño: se deja reglas")}</Tag>
            </>
          }
        >
          <p className="text-muted-foreground">{t("Para probar Scrinium, no para un proyecto real. Descárgalo en la terminal:")}</p>
          <Command>{keyring?.in_docker ? "sh docker/scrinium.sh run tools install local" : "scrinium tools install local"}</Command>
          {setup.choice !== "bundled" && (
            <div>
              <Button size="sm" variant="outline" onClick={() => choose("bundled")}>
                {t("Usar el modelo de prueba")}
              </Button>
            </div>
          )}
        </Choice>
      </ul>
    </section>
  )
}

function SearchModel({
  setup,
  status,
  keyring,
  onChanged,
  fail,
}: {
  setup: AiSetup
  status: SecurityStatus
  keyring: Keyring | null
  onChanged: (setup: AiSetup) => void
  fail: (caught: unknown) => void
}) {
  const search = setup.search
  const [selected, setSelected] = useState<string>(search.kind)
  const [providers, setProviders] = useState<ModelProvider[]>([])
  const [providerId, setProviderId] = useState(search.provider_id ?? "")
  const [model, setModel] = useState(search.kind === "provider" ? (search.model ?? "") : "")
  const [accepted, setAccepted] = useState(false)
  useEffect(() => {
    listModelProviders()
      .then((listing) => {
        setProviders(listing.providers)
        setProviderId((current) => current || listing.providers[0]?.id || "")
      })
      .catch(() => undefined)
  }, [])
  const blocked = setup.isolated ? t("Bloqueado por el modo aislado") : null
  const ready = status.providers.embeddings === "ready"
  return (
    <section className="grid gap-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="font-heading text-base font-medium">{t("2 · Modelo de búsqueda")}</h2>
        <Pill ok={ready}>{ready ? t("Funcionando") : t("No responde")}</Pill>
      </div>
      <Muted>{t("Al cambiarlo, Scrinium vuelve a indexar el texto en segundo plano; mientras tanto busca por palabras.")}</Muted>
      <ul className="grid gap-2">
        <Choice
          name="search"
          value="bundled"
          selected={selected === "bundled"}
          current={search.kind === "bundled"}
          onSelect={setSelected}
          title={t("Incluido")}
          tags={
            <>
              <Tag tone="good">{t("Recomendado")}</Tag>
              <Tag tone="good">{t("El código no sale de aquí")}</Tag>
              <Tag>{t("0,6 GB, ya instalado")}</Tag>
            </>
          }
        >
          {search.kind !== "bundled" && (
            <div>
              <Button size="sm" onClick={() => chooseSearch({ kind: "bundled" }).then(onChanged).catch(fail)}>
                {t("Usar el incluido")}
              </Button>
            </div>
          )}
        </Choice>
        <Choice
          name="search"
          value="local_server"
          selected={selected === "local_server"}
          current={search.kind === "local_server"}
          onSelect={setSelected}
          title={t("Un servidor en tu equipo")}
          tags={
            <>
              <Tag tone="good">{t("El código no sale de aquí")}</Tag>
              <Tag>{t("Un modelo de embeddings")}</Tag>
            </>
          }
        >
          <DockerLocalSteps show={Boolean(keyring?.in_docker)} />
          <LocalServerForm
            initialUrl={search.url ?? "http://127.0.0.1:11434"}
            initialModel={search.kind === "local_server" ? (search.model ?? "") : ""}
            modelHint={t("un modelo de embeddings")}
            useLabel={t("Usar para buscar")}
            check={(url, name) => checkSearchServer(url, name).then(({ status: probe }) => statusText(probe))}
            onUse={(url, name) => chooseSearch({ kind: "local_server", url, model: name }).then(onChanged).catch(fail)}
          />
        </Choice>
        <Choice
          name="search"
          value="provider"
          selected={selected === "provider"}
          current={search.kind === "provider"}
          onSelect={setSelected}
          blocked={blocked}
          title={t("Un proveedor en internet")}
          tags={
            <>
              <Tag tone="warning">{t("Envía TODO el texto indexado")}</Tag>
              <Tag>{t("Pagas por uso")}</Tag>
            </>
          }
        >
          <KeyringNotice keyring={keyring} />
          {keyring?.keyring_usable && providers.length === 0 && (
            <Muted>{t("Primero añade un proveedor en el modelo de respuestas, más arriba.")}</Muted>
          )}
          {keyring?.keyring_usable && providers.length > 0 && (
            <div className="grid gap-2">
              <div className="grid gap-2 sm:grid-cols-2">
                <div className="grid gap-1">
                  <Label htmlFor="search-provider">{t("Proveedor")}</Label>
                  <select
                    id="search-provider"
                    className="h-9 rounded-md border bg-background px-2 text-sm"
                    value={providerId}
                    onChange={(event) => setProviderId(event.target.value)}
                  >
                    {providers.map((provider) => (
                      <option key={provider.id} value={provider.id}>
                        {provider.name}
                      </option>
                    ))}
                  </select>
                </div>
                <div className="grid gap-1">
                  <Label htmlFor="search-model">{t("Modelo de embeddings")}</Label>
                  <Input id="search-model" value={model} onChange={(event) => setModel(event.target.value)} />
                </div>
              </div>
              <label className="flex items-start gap-2 text-xs">
                <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} className="mt-0.5" />
                <span>{t("Entiendo que el texto indexado de todos los proyectos (código y documentos) se envía a este proveedor.")}</span>
              </label>
              <div>
                <Button
                  size="sm"
                  disabled={!accepted || !providerId || !model.trim()}
                  onClick={() =>
                    chooseSearch({ kind: "provider", provider_id: providerId, model: model.trim(), sends_all_text: true })
                      .then(onChanged)
                      .catch(fail)
                  }
                >
                  {t("Usar para buscar")}
                </Button>
              </div>
            </div>
          )}
        </Choice>
      </ul>
    </section>
  )
}

function IsolatedMode({
  setup,
  onChanged,
  fail,
}: {
  setup: AiSetup
  onChanged: (setup: AiSetup) => void
  fail: (caught: unknown) => void
}) {
  const turnOn = async () => {
    const effects = await getIsolationEffects()
    const lines = [
      effects.answer_model_lost && t("Tu modelo de respuestas es un proveedor: te quedas sin modelo de respuestas hasta que elijas uno local."),
      effects.search_model_reset && t("La búsqueda vuelve al modelo incluido y se indexa de nuevo."),
      effects.consents > 0 &&
        t("Se retiran {0} permisos de proveedor en: {1}.", effects.consents, effects.projects.join(", ")),
      t("Tus proveedores y sus claves se conservan, pero no se usan."),
    ].filter(Boolean) as string[]
    const { confirmed } = await Confirm.call({
      title: t("¿Activar el modo aislado?"),
      icon: ShieldCheck,
      content: (
        <ul className="grid list-disc gap-1 pl-5 text-sm text-muted-foreground">
          {lines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      ),
      confirmLabel: t("Activar y configurar modelo local"),
    })
    if (!confirmed) return
    const chosen = await setIsolation(true)
    onChanged(chosen)
    document.getElementById("modelo-respuestas")?.scrollIntoView({ behavior: "smooth" })
  }
  return (
    <section className="grid gap-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="flex items-center gap-2 font-heading text-base font-medium">
          <ShieldCheck className="size-4" aria-hidden="true" />
          {t("Modo aislado")}
        </h2>
        <Pill ok={setup.isolated}>{setup.isolated ? t("Activado") : t("Desactivado")}</Pill>
      </div>
      <p className="text-sm">
        {t("Scrinium se niega a registrar o usar cualquier proveedor, para los dos modelos: tu código no sale de este equipo a través de Scrinium. Igual con Docker que instalado. Para código confidencial o empresas que lo exigen.")}
      </p>
      <div className="grid gap-3 text-sm sm:grid-cols-2">
        <div className="grid gap-1">
          <p className="font-medium">{t("Sigue funcionando")}</p>
          <ul className="grid list-disc gap-0.5 pl-5 text-muted-foreground">
            <li>{t("Modelos en tu equipo")}</li>
            <li>{t("Búsqueda")}</li>
            <li>{t("Reglas")}</li>
            <li>{t("Avisos de cambios")}</li>
          </ul>
        </div>
        <div className="grid gap-1">
          <p className="font-medium">{t("Queda bloqueado")}</p>
          <ul className="grid list-disc gap-0.5 pl-5 text-muted-foreground">
            <li>{t("Proveedores, para los dos modelos")}</li>
            <li>{t("Los permisos que dieron los proyectos se retiran")}</li>
          </ul>
        </div>
      </div>
      {setup.managed ? (
        <Muted>{t("Modo aislado impuesto por tu organización (SCRINIUM_PROFILE): no se puede cambiar desde aquí.")}</Muted>
      ) : setup.isolated ? (
        <div>
          <Button size="sm" variant="outline" onClick={() => setIsolation(false).then(onChanged).catch(fail)}>
            {t("Desactivar el modo aislado")}
          </Button>
          <Muted>{t("Los permisos retirados no vuelven solos: cada proyecto los da de nuevo.")}</Muted>
        </div>
      ) : (
        <div>
          <Button size="sm" variant="outline" onClick={() => turnOn().catch(fail)}>
            {t("Activar el modo aislado")}
          </Button>
        </div>
      )}
    </section>
  )
}

/** Settings › AI models: the two models, each with its own choice, and isolated mode. */
export function ModelSettings({
  setup,
  status,
  onChanged,
  onSessionLost,
  engine,
}: {
  setup: AiSetup
  status: SecurityStatus
  onChanged: (setup: AiSetup) => void
  onSessionLost: (error: unknown) => void
  engine: ReactNode
}) {
  const [keyring, setKeyring] = useState<Keyring | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    listModelProviders()
      .then(({ keyring: has, keyring_usable, in_docker }) => setKeyring({ keyring: has, keyring_usable, in_docker }))
      .catch(onSessionLost)
  }, [onSessionLost])
  const fail = useCallback(
    (caught: unknown) => {
      if (caught instanceof ApiError) setError(ERRORS[caught.code ?? ""] ?? t("No se pudo guardar ({0}).", caught.code ?? caught.status))
      else onSessionLost(caught)
    },
    [onSessionLost],
  )
  const changed = (next: AiSetup) => {
    setError(null)
    onChanged(next)
  }
  return (
    <div className="grid gap-4">
      <div className="grid gap-3">
        <h2 className="font-heading text-lg font-medium">{t("Scrinium usa dos modelos con trabajos distintos")}</h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="grid gap-1 rounded-lg border p-3 text-sm">
            <p className="font-medium">{t("Modelo de respuestas")}</p>
            <p className="text-muted-foreground">{t("Escribe las reglas y responde tus preguntas. Sin él no hay reglas: lo pones tú.")}</p>
          </div>
          <div className="grid gap-1 rounded-lg border p-3 text-sm">
            <p className="font-medium">{t("Modelo de búsqueda")}</p>
            <p className="text-muted-foreground">{t("Encuentra los fragmentos de código y documentos relacionados. Ya viene incluido.")}</p>
          </div>
        </div>
      </div>
      <ErrorText>{error}</ErrorText>
      <div id="modelo-respuestas">
        <AnswerModel key={`${setup.choice}-${setup.isolated}`} setup={setup} status={status} keyring={keyring} onChanged={changed} fail={fail} />
      </div>
      {keyring?.keyring_usable && !setup.isolated && <ModelCatalog onSessionLost={onSessionLost} />}
      <SearchModel key={`${setup.search.kind}-${setup.isolated}`} setup={setup} status={status} keyring={keyring} onChanged={changed} fail={fail} />
      <IsolatedMode setup={setup} onChanged={changed} fail={fail} />
      <details className="rounded-xl border bg-card p-4">
        <summary className="cursor-pointer text-sm font-medium">{t("Motor incluido (llama.cpp) y descargas")}</summary>
        <div className="mt-4">{engine}</div>
      </details>
    </div>
  )
}
