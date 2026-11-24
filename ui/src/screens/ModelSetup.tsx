import { t } from "@/lib/i18n"
import { Cloud, Loader2, Server, ShieldOff } from "lucide-react"
import { type FormEvent, useState } from "react"
import { Link } from "react-router"
import { cn } from "cn"
import { Button } from "@/components/ui/button"
import { Input, Label } from "@/components/ui/input"
import { type AiSetup, ApiError, type SetupChoice, checkLocalServer, chooseAiSetup } from "@/lib/api"
import { ErrorText, Muted } from "@/screens/shared"

const ERRORS: Record<string, string> = {
  local_server_not_loopback: t("El servidor tiene que estar en este equipo: http://127.0.0.1:puerto o http://localhost:puerto, sin ruta."),
  invalid_model: t("Escribe el nombre del modelo tal como lo sirve el servidor, sin espacios."),
  isolated_managed: t("Tu organización impuso el modo aislado en este equipo: no se puede cambiar desde aquí."),
}

/**
 * The first-run choice (ADR-013): where the model that writes the rules lives. Nothing is sent
 * until a person chooses, and choosing a provider still sends nothing until a project consents.
 */
export function ModelSetup({
  setup,
  onChosen,
  onSessionLost,
}: {
  setup: AiSetup
  onChosen: (setup: AiSetup) => void
  onSessionLost: (error: unknown) => void
}) {
  const [busy, setBusy] = useState<SetupChoice | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [url, setUrl] = useState(setup.local_server?.url ?? "http://127.0.0.1:11434")
  const [model, setModel] = useState(setup.local_server?.model ?? "")
  const [probe, setProbe] = useState<string | null>(null)

  if (setup.managed) {
    return (
      <Muted>
        {t("Modo aislado impuesto por tu organización (SCRINIUM_PROFILE): no se puede registrar ni usar ningún proveedor externo.")}
      </Muted>
    )
  }

  const fail = (caught: unknown) => {
    if (caught instanceof ApiError) setError(ERRORS[caught.code ?? ""] ?? t("No se pudo guardar ({0}).", caught.code ?? caught.status))
    else onSessionLost(caught)
  }

  const choose = (choice: SetupChoice) => {
    setBusy(choice)
    setError(null)
    chooseAiSetup(choice, { url: url.trim(), model: model.trim() })
      .then(onChosen)
      .catch(fail)
      .finally(() => setBusy(null))
  }

  const test = () => {
    setProbe(null)
    setError(null)
    checkLocalServer(url.trim(), model.trim())
      .then(({ status }) =>
        setProbe(
          status === "ready"
            ? t("Responde y tiene el modelo.")
            : status === "model_missing"
              ? t("Responde, pero no sirve ese modelo.")
              : t("No responde en esa dirección."),
        ),
      )
      .catch(fail)
  }

  const submitLocal = (event: FormEvent) => {
    event.preventDefault()
    choose("local_server")
  }

  return (
    <div className="grid gap-3">
      <div className="grid gap-3 md:grid-cols-3">
        <Option
          icon={Cloud}
          title={t("Tu proveedor")}
          badge={t("Recomendado")}
          current={setup.choice === "provider"}
        >
          <p>
            {t("DeepSeek, GLM, Claude o cualquier API compatible con OpenAI, con tu clave. Escribe las mejores reglas y no necesita GPU.")}
          </p>
          <p>{t("Nada se envía hasta que des permiso en cada proyecto, y ves cada envío.")}</p>
          <Button size="sm" disabled={busy !== null} onClick={() => choose("provider")}>
            {busy === "provider" && <Loader2 className="animate-spin" data-icon="inline-start" />}
            {t("Usar mi proveedor")}
          </Button>
        </Option>
        <Option icon={Server} title={t("Un servidor local")} current={setup.choice === "local_server"}>
          <p>{t("Ollama, vLLM o llama-server en este equipo. El código no sale de aquí.")}</p>
          <form className="grid gap-2" onSubmit={submitLocal}>
            <Label htmlFor="local-url">{t("Dirección")}</Label>
            <Input
              id="local-url"
              required
              maxLength={500}
              className="font-mono"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
            />
            <Label htmlFor="local-model">{t("Modelo")}</Label>
            <Input
              id="local-model"
              required
              maxLength={200}
              placeholder="qwen3:8b"
              className="font-mono"
              value={model}
              onChange={(event) => setModel(event.target.value)}
            />
            <div className="flex flex-wrap gap-1">
              <Button type="button" size="sm" variant="outline" disabled={!url.trim() || !model.trim()} onClick={test}>
                {t("Probar")}
              </Button>
              <Button type="submit" size="sm" variant="outline" disabled={busy !== null || !model.trim()}>
                {busy === "local_server" && <Loader2 className="animate-spin" data-icon="inline-start" />}
                {t("Usar este servidor")}
              </Button>
            </div>
            {probe && <p role="status">{probe}</p>}
          </form>
        </Option>
        <Option icon={ShieldOff} title={t("Modo aislado")} current={setup.choice === "isolated"}>
          <p>
            {t("Nada puede salir del equipo: no se puede registrar ni usar ningún proveedor, y el estado de seguridad lo dice para que se pueda comprobar.")}
          </p>
          <p>{t("Retira los permisos que ya hubiera. Las reglas las escribe el modelo local, si lo instalas.")}</p>
          <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => choose("isolated")}>
            {busy === "isolated" && <Loader2 className="animate-spin" data-icon="inline-start" />}
            {t("Activar el modo aislado")}
          </Button>
        </Option>
      </div>
      <ErrorText>{error}</ErrorText>
      {setup.choice === "provider" && (
        <Muted>
          {t("Registra tu proveedor en")}{" "}
          <Link to="/ajustes/modelos" className="font-medium text-foreground underline-offset-4 hover:underline">
            {t("Ajustes › Modelos de IA")}
          </Link>{" "}
          {t("y da permiso en")}{" "}
          <Link to="/ia" className="font-medium text-foreground underline-offset-4 hover:underline">
            {t("IA del proyecto")}
          </Link>
          {t(". Antes de cada extracción verás el coste estimado.")}
        </Muted>
      )}
    </div>
  )
}

function Option({
  icon: Icon,
  title,
  badge,
  current,
  children,
}: {
  icon: typeof Cloud
  title: string
  badge?: string
  current: boolean
  children: React.ReactNode
}) {
  return (
    <section
      className={cn(
        "grid content-start gap-2 rounded-xl bg-card p-4 text-sm text-muted-foreground shadow-[0_1px_2px_oklch(0_0_0/0.04)] ring-1 ring-border/70",
        current && "ring-primary/40",
      )}
    >
      <h3 className="flex flex-wrap items-center gap-2 font-medium text-foreground">
        <Icon className="size-4" aria-hidden="true" />
        {title}
        {badge && <span className="rounded-full bg-primary/10 px-2 text-xs text-primary">{badge}</span>}
        {current && <span className="text-xs text-primary">{t("· en uso")}</span>}
      </h3>
      {children}
    </section>
  )
}
