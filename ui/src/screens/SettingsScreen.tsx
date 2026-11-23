import { adoptLanguage, language, t } from "@/lib/i18n"
import { Cpu, LogOut, Monitor, Moon, Palette, Search, ShieldCheck, Sun, UserRound } from "lucide-react"
import { type FormEvent, useEffect, useState } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { CodeThemePreview } from "@/components/code-excerpt"
import { InfoRow, PageHeader, Section } from "@/components/page"
import { ModelStatusBadge, StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Input, Label } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  type AiSetup,
  type DataFlow,
  type EngineInfo,
  type LlmCheck,
  type Project,
  type SecurityStatus,
  checkLlm,
  getDataFlow,
  getEngine,
  setLanguage,
  getSearchSettings,
  updateProfile,
  updateSearchSettings,
} from "@/lib/api"
import { CODE_THEMES, type CodeThemeId, setCodeTheme } from "@/lib/highlight"
import { type ThemePreference, setTheme, storedTheme } from "@/lib/theme"
import { useCodeTheme } from "@/lib/use-code-theme"
import { PrivacyCard } from "@/screens/PrivacyCard"
import { setupLabel } from "@/lib/labels"
import { DownloadsSection } from "@/screens/DownloadsSection"
import { ModelSetup } from "@/screens/ModelSetup"
import { ModelCatalog } from "@/screens/RemoteAiSection"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Eyebrow, Muted } from "@/screens/shared"

const GB = 1024 ** 3
const MIN_MEMORY_GB = 8

const ACCELERATOR_LABELS: Record<NonNullable<EngineInfo["engine"]["accelerator"]>, string> = {
  vulkan: t("GPU (Vulkan)"),
  metal: t("GPU (Metal)"),
  cpu: t("Solo CPU"),
}

const CHECK_LABELS: Record<LlmCheck["status"], string> = {
  ok: t("Responde"),
  model_missing: t("Falta el modelo local (scrinium tools install)"),
  unavailable: t("El motor no responde"),
  failed: t("No dio la respuesta esperada"),
}

function gigabytes(bytes: number, decimals = 1) {
  return t("{0} GB", (bytes / GB).toLocaleString("es", { maximumFractionDigits: decimals }))
}

function EngineSection({ status }: { status: SecurityStatus }) {
  const [engine, setEngine] = useState<EngineInfo | null>(null)
  const [check, setCheck] = useState<LlmCheck | "running" | "error" | null>(null)
  useEffect(() => {
    getEngine()
      .then(setEngine)
      .catch(() => setEngine(null))
  }, [])
  const runCheck = () => {
    setCheck("running")
    checkLlm()
      .then(setCheck)
      .catch(() => setCheck("error"))
  }
  const memory = engine?.memory_bytes ?? null
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <Cpu className="size-4" />
          {t("Motor de IA local")}
        </span>
      }
      description={t("Los modelos se ejecutan en este equipo. Para instalar o reparar el motor, usa scrinium tools install y scrinium doctor en la terminal; para moverlo a otro disco, aquí abajo.")}
      action={
        <Button size="sm" variant="outline" disabled={check === "running"} onClick={runCheck}>
          {check === "running" ? t("Probando…") : t("Probar el motor")}
        </Button>
      }
    >
      {check !== null && check !== "running" && (
        <div>
          {check === "error" ? (
            <StatusBadge tone="attention">{t("No se pudo probar")}</StatusBadge>
          ) : check.status === "ok" ? (
            <StatusBadge tone="verified">
              {t("Responde en")} {(check.duration_ms / 1000).toFixed(1)} s
            </StatusBadge>
          ) : (
            <StatusBadge tone="attention">{CHECK_LABELS[check.status]}</StatusBadge>
          )}
        </div>
      )}
      <dl className="divide-y">
        <InfoRow label={t("Respuestas")}>
          <ModelStatusBadge status={status.providers.llm} />
        </InfoRow>
        <InfoRow label={t("Búsqueda por significado")}>
          <ModelStatusBadge status={status.providers.embeddings} />
        </InfoRow>
        {engine && (
          <>
            <InfoRow label={t("Motor")}>
              {engine.provider === "ollama" ? (
                t("Ollama")
              ) : (
                <span className="flex items-center gap-2">
                  {engine.engine.name} {engine.engine.version}
                  {engine.engine.running ? (
                    <StatusBadge tone="verified">{t("En marcha")}</StatusBadge>
                  ) : engine.engine.installed ? (
                    <StatusBadge tone="attention">{t("Detenido")}</StatusBadge>
                  ) : (
                    <StatusBadge tone="attention">{t("Sin instalar")}</StatusBadge>
                  )}
                </span>
              )}
            </InfoRow>
            {engine.provider === "llama" && (
              <InfoRow label={t("Aceleración")}>
                {engine.engine.accelerator ? ACCELERATOR_LABELS[engine.engine.accelerator] : "—"}
              </InfoRow>
            )}
            <InfoRow label={t("Memoria del equipo")}>
              {memory === null ? (
                t("No se pudo saber")
              ) : memory / GB < MIN_MEMORY_GB - 0.5 ? (
                <StatusBadge tone="attention">{gigabytes(memory, 0)}{t(": las respuestas irán lentas")}</StatusBadge>
              ) : (
                gigabytes(memory, 0)
              )}
            </InfoRow>
          </>
        )}
      </dl>
      {engine?.provider === "llama" && (
        <div className="grid gap-2">
          <Eyebrow>{t("Modelos")}</Eyebrow>
          <ul className="grid gap-2">
            {engine.models.map((model) => (
              <li key={model.name} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3 text-sm">
                <span className="grid">
                  <span className="font-medium">{model.role === "chat" ? t("Respuestas") : t("Búsqueda por significado")}</span>
                  <code className="text-xs text-muted-foreground">
                    {model.file} · {gigabytes(model.size_bytes)}
                  </code>
                </span>
                {model.installed ? (
                  <StatusBadge tone="verified">{t("Instalado")}</StatusBadge>
                ) : (
                  <StatusBadge tone="attention">{t("Sin instalar")}</StatusBadge>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {engine?.provider === "llama" && <DownloadsSection initial={engine.downloads} />}
    </Section>
  )
}

const THEMES: { value: ThemePreference; label: string; icon: typeof Sun }[] = [
  { value: "system", label: t("Como el sistema"), icon: Monitor },
  { value: "light", label: t("Claro"), icon: Sun },
  { value: "dark", label: t("Oscuro"), icon: Moon },
]

function AppearanceSection() {
  const [theme, setThemeState] = useState<ThemePreference>(storedTheme)
  const choose = (value: ThemePreference) => {
    setThemeState(value)
    setTheme(value)
  }
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <Palette className="size-4" />
          {t("Apariencia")}
        </span>
      }
    >
      <Tabs value={theme} onValueChange={(value) => choose(value as ThemePreference)}>
        <TabsList aria-label={t("Tema")}>
          {THEMES.map(({ value, label, icon: Icon }) => (
            <TabsTrigger key={value} value={value}>
              <Icon />
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      <CodeThemeChoice />
      <LanguageChoice />
    </Section>
  )
}

/** Spanish or English (phase 5.5): the console and what the models write. Shown in both. */
function LanguageChoice() {
  const [saving, setSaving] = useState(false)
  const choose = (value: string) => {
    if (value !== "es" && value !== "en") return
    setSaving(true)
    setLanguage(value)
      .then(({ language: chosen }) => {
        adoptLanguage(chosen)
        window.location.reload()
      })
      .catch(() => setSaving(false))
  }
  return (
    <div className="grid gap-2">
      <Eyebrow>Idioma · Language</Eyebrow>
      <Tabs value={language()} onValueChange={(value) => choose(String(value))}>
        <TabsList aria-label="Idioma · Language">
          <TabsTrigger value="es" disabled={saving}>
            Español
          </TabsTrigger>
          <TabsTrigger value="en" disabled={saving}>
            English
          </TabsTrigger>
        </TabsList>
      </Tabs>
      <Muted>{t("También el idioma en que el modelo escribe las reglas nuevas.")}</Muted>
    </div>
  )
}

/** The colors of the code in rules and citations: the three most used VS Code themes. */
function CodeThemeChoice() {
  const theme = useCodeTheme()
  const hint = CODE_THEMES.find((option) => option.id === theme)?.hint
  return (
    <div className="grid min-w-0 gap-2">
      <Label>{t("Colores del código")}</Label>
      <Tabs value={theme} onValueChange={(value) => setCodeTheme(value as CodeThemeId)}>
        <TabsList aria-label={t("Colores del código")}>
          {CODE_THEMES.map(({ id, label }) => (
            <TabsTrigger key={id} value={id}>
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      <CodeThemePreview />
    </div>
  )
}

function ProfileSection({
  name,
  onSaved,
  onSessionLost,
}: {
  name: string | null
  onSaved: (name: string) => void
  onSessionLost: (error: unknown) => void
}) {
  const [value, setValue] = useState(name ?? "")
  const { error, setError, fail } = useErrors(onSessionLost)
  const save = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    updateProfile(value.trim())
      .then((profile) => onSaved(profile.display_name ?? ""))
      .catch(fail)
  }
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <UserRound className="size-4" />
          {t("Perfil local")}
        </span>
      }
      description={t("Tu nombre queda registrado al verificar o descartar reglas y hallazgos, y al escribir reglas. No es una cuenta.")}
    >
      <form className="flex flex-wrap items-end gap-2" onSubmit={save}>
        <div className="grid min-w-48 flex-1 gap-1.5">
          <Label htmlFor="profile-name">{t("Nombre")}</Label>
          <Input
            id="profile-name"
            required
            maxLength={100}
            value={value}
            onChange={(event) => setValue(event.target.value)}
          />
        </div>
        <Button type="submit" variant="outline" disabled={!value.trim() || value.trim() === name}>
          {t("Guardar")}
        </Button>
      </form>
      <ErrorText>{error}</ErrorText>
    </Section>
  )
}

function SearchSection({ onSessionLost }: { onSessionLost: (error: unknown) => void }) {
  const [includeTests, setIncludeTests] = useState<boolean | null>(null)
  const { error, setError, fail } = useErrors(onSessionLost)
  useEffect(() => {
    getSearchSettings()
      .then((settings) => setIncludeTests(settings.include_tests))
      .catch(fail)
  }, [fail])
  const toggle = (value: boolean) => {
    setError(null)
    updateSearchSettings(value)
      .then((settings) => setIncludeTests(settings.include_tests))
      .catch(fail)
  }
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <Search className="size-4" />
          {t("Búsqueda")}
        </span>
      }
      description={t("Buscar y preguntar devuelve código, reglas y documentos. Los tests comprueban reglas pero no las explican, así que quedan fuera salvo que los incluyas.")}
    >
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-primary"
          checked={includeTests ?? false}
          disabled={includeTests === null}
          onChange={(event) => toggle(event.target.checked)}
        />
        {t("Incluir los tests en la búsqueda y en las respuestas")}
      </label>
      {includeTests ? (
        <Muted>{t("Sus vectores se calculan en segundo plano; mientras tanto se encuentran por palabras.")}</Muted>
      ) : null}
      <ErrorText>{error}</ErrorText>
    </Section>
  )
}

const DISK_ENCRYPTION: Record<SecurityStatus["storage"]["disk_encryption"], string> = {
  encrypted: t("Activo"),
  not_detected: t("No detectado"),
  unknown: t("No se pudo comprobar"),
}

function listOrNone(items: string[]) {
  return items.length > 0 ? items.join(", ") : t("Ninguno")
}

function PrivacySection({ status, dataFlow }: { status: SecurityStatus; dataFlow: DataFlow }) {
  const { storage } = status
  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <ShieldCheck className="size-4" />
          {t("Privacidad")}
        </span>
      }
      description={t("Qué puede salir de este equipo y cómo se protege lo que se queda.")}
    >
      <dl className="divide-y">
        <InfoRow label={t("Modo de IA")}>
          {dataFlow.ai_mode === "local_only" ? (
            <StatusBadge tone="verified">{t("Solo local")}</StatusBadge>
          ) : (
            <StatusBadge tone="attention">{t("Con IA externa")}</StatusBadge>
          )}
        </InfoRow>
        <InfoRow label={t("Servicios de IA externos")}>{listOrNone(dataFlow.remote_ai_providers)}</InfoRow>
        <InfoRow label={t("Envíos fuera del equipo")}>{listOrNone(dataFlow.outbound_destinations)}</InfoRow>
        <InfoRow label={t("Carpeta de datos")}>
          {storage.data_dir_permissions === "private" ? (
            <StatusBadge tone="verified">{t("Solo tu cuenta")}</StatusBadge>
          ) : (
            <StatusBadge tone="no_evidence">{t("Sin comprobar")}</StatusBadge>
          )}
        </InfoRow>
        <InfoRow label={t("Cifrado de disco")}>
          <StatusBadge tone={storage.disk_encryption === "encrypted" ? "verified" : "attention"}>
            {DISK_ENCRYPTION[storage.disk_encryption]}
          </StatusBadge>
        </InfoRow>
        <InfoRow label={t("Llavero para credenciales")}>
          {storage.secret_store_backend ?? <StatusBadge tone="attention">{t("No disponible")}</StatusBadge>}
        </InfoRow>
      </dl>
      {status.limits.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted-foreground">{t("Lo que esto no garantiza")}</summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-muted-foreground">
            {status.limits.map((limit) => (
              <li key={limit}>{limit}</li>
            ))}
          </ul>
        </details>
      )}
    </Section>
  )
}

function SessionSection({ status, onEndSession }: { status: SecurityStatus; onEndSession: () => void }) {
  return (
      <Section title={t("Sesión")}>
        <dl className="divide-y">
          <InfoRow label={t("Versión")}>{t("Scrinium")} {status.runtime.version}</InfoRow>
          <InfoRow label={t("Dirección")}>
            <code>
              {status.runtime.bind_address}:{status.runtime.port}
            </code>
          </InfoRow>
          <InfoRow label={t("Permisos de administrador")}>{status.runtime.running_as_admin ? t("Sí") : t("No")}</InfoRow>
        </dl>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="outline" onClick={onEndSession}>
            <LogOut data-icon="inline-start" />
            {t("Cerrar sesión")}
          </Button>
          <Muted>{t("Para volver a entrar, reinicia Scrinium con scrinium start.")}</Muted>
        </div>
      </Section>
  )
}

const SETTINGS_TABS = [
  { value: "general", label: t("General"), icon: UserRound },
  { value: "modelos", label: t("Modelos de IA"), icon: Cpu },
  { value: "privacidad", label: t("Privacidad y datos"), icon: ShieldCheck },
] as const

export function SettingsScreen({
  project,
  status,
  dataFlow,
  profileName,
  onProfileSaved,
  onProjectDeleted,
  onWiped,
  onEndSession,
  setup = null,
  onSetupChosen = () => undefined,
  onSessionLost,
}: {
  project: Project | null
  status: SecurityStatus
  dataFlow: DataFlow
  profileName: string | null
  setup?: AiSetup | null
  onSetupChosen?: (setup: AiSetup) => void
  onProfileSaved: (name: string) => void
  onProjectDeleted: (projectId: string) => void
  onWiped: () => void
  onEndSession: () => void
  onSessionLost: (error: unknown) => void
}) {
  // What the privacy summary says follows consents given or revoked on this screen.
  // Consents change on the project's AI page: the privacy summary is read fresh here.
  const [flow, setFlow] = useState(dataFlow)
  useEffect(() => {
    getDataFlow().then(setFlow).catch(onSessionLost)
  }, [onSessionLost])
  const { tab = "general" } = useParams()
  const navigate = useNavigate()
  const current = SETTINGS_TABS.some((option) => option.value === tab) ? tab : "general"
  return (
    <>
      <PageHeader title={t("Ajustes")} />
      <Tabs value={current} onValueChange={(value) => navigate(`/ajustes/${value}`)}>
        <TabsList aria-label={t("Secciones de Ajustes")}>
          {SETTINGS_TABS.map(({ value, label, icon: Icon }) => (
            <TabsTrigger key={value} value={value}>
              <Icon />
              {label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {current === "general" && (
        <>
          <div className="grid gap-6 md:grid-cols-2">
            <ProfileSection name={profileName} onSaved={onProfileSaved} onSessionLost={onSessionLost} />
            <AppearanceSection />
          </div>
          <SearchSection onSessionLost={onSessionLost} />
          <SessionSection status={status} onEndSession={onEndSession} />
        </>
      )}
      {current === "modelos" && (
        <>
          {setup && (
            <Section
              title={t("Qué modelo escribe las reglas")}
              description={t("Ahora: {0}. La búsqueda por significado siempre es local.", setupLabel(setup))}
            >
              <ModelSetup setup={setup} onChosen={onSetupChosen} onSessionLost={onSessionLost} />
            </Section>
          )}
          <EngineSection status={status} />
          <ModelCatalog onSessionLost={onSessionLost} />
          <p className="text-sm text-muted-foreground">
            {t("Qué modelo usa cada tarea de un proyecto se elige en")}{" "}
            <Link to="/ia" className="font-medium text-foreground underline-offset-4 hover:underline">
              {t("IA del proyecto")}
            </Link>
            .
          </p>
        </>
      )}
      {current === "privacidad" && (
        <>
          <PrivacySection status={status} dataFlow={flow} />
          <PrivacyCard
            project={project}
            onProjectDeleted={onProjectDeleted}
            onWiped={onWiped}
            onSessionLost={onSessionLost}
          />
        </>
      )}
    </>
  )
}
