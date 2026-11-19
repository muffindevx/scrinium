import { FileText, KeyRound, MessageSquare, Plus, RefreshCw, Ticket, Trash2 } from "lucide-react"
import { type FormEvent, type ReactNode, useCallback, useEffect, useState } from "react"
import { useSearchParams } from "react-router"
import { InfoRow, Section } from "@/components/page"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Confirm } from "@/components/ui/confirm"
import { Dialog } from "@/components/ui/dialog"
import { Input, Label } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  type ConnectRequest,
  type Project,
  type TicketConnection,
  type TrackerTeam,
  connectTracker,
  getLinearOAuth,
  getTrackerTeams,
  listTicketConnections,
  removeTicketConnection,
  setTrackerOptions,
  setTrackerScope,
  startLinearOAuth,
  syncTickets,
} from "@/lib/api"
import { t } from "@/lib/i18n"
import { errorMessage } from "@/lib/labels"
import { relativeTime } from "@/lib/time"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted } from "@/screens/shared"

const PROVIDERS: Record<TicketConnection["provider"], string> = { linear: "Linear", jira: "Jira" }

// What Linear's consent page sent back (`?linear=…`), in words.
const OAUTH_OUTCOMES: Record<string, { text: string; ok: boolean }> = {
  connected: { text: t("Linear quedó conectado. Elige qué equipos lee este proyecto."), ok: true },
  denied: { text: t("No se dio permiso en Linear; no se conectó nada."), ok: false },
  expired: { text: t("La conexión con Linear caducó o ya se usó. Vuelve a empezar."), ok: false },
  failed: { text: t("Linear no completó la conexión. Prueba otra vez."), ok: false },
}

/**
 * Ticket trackers this project reads (phase 10, ADR-014): Linear and Jira. They are read; text
 * and comments are each a separate choice, off by default. Tokens go to the system keyring and
 * never come back to this screen.
 */
export function TicketConnections({
  project,
  onSessionLost,
}: {
  project: Project
  onSessionLost: (error: unknown) => void
}) {
  const [connections, setConnections] = useState<TicketConnection[] | null>(null)
  const [adding, setAdding] = useState(false)
  // Teams offered right after connecting, before a scope exists.
  const [choosing, setChoosing] = useState<{ connection: TicketConnection; teams: TrackerTeam[] } | null>(null)
  const [params, setParams] = useSearchParams()
  const [outcome] = useState(() => OAUTH_OUTCOMES[params.get("linear") ?? ""] ?? null)
  const { error, setError, fail } = useErrors(onSessionLost)
  const load = useCallback(() => {
    listTicketConnections(project.id)
      .then(({ connections: rows }) => setConnections(rows))
      .catch(fail)
  }, [project.id, fail])
  useEffect(load, [load])
  // The outcome is read once; the URL goes back to plain /fuentes.
  useEffect(() => {
    if (params.has("linear")) setParams({}, { replace: true })
  }, [params, setParams])

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <Ticket className="size-4" />
          {t("Tickets")}
        </span>
      }
      description={t("Los tickets explican por qué existe una regla y por qué cambió. Por defecto Scrinium solo lee título, estado, tipo, etiquetas y enlaces; nunca personas.")}
      action={
        <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
          <Plus data-icon="inline-start" />
          {t("Conectar un tracker")}
        </Button>
      }
    >
      {outcome && (
        <StatusBadge tone={outcome.ok ? "verified" : "attention"}>{outcome.text}</StatusBadge>
      )}
      {connections?.length === 0 && (
        <Muted>{t("Sin conectar. Las claves de ticket que nombran los commits (como SHOP-221) se siguen mostrando como etiquetas.")}</Muted>
      )}
      {connections?.map((connection) => (
        <ConnectionRow
          key={connection.id}
          project={project}
          connection={connection}
          onChanged={load}
          onChooseTeams={(teams) => setChoosing({ connection, teams })}
          onSessionLost={onSessionLost}
        />
      ))}
      <ErrorText>{error}</ErrorText>
      {adding && (
        <ConnectDialog
          project={project}
          onClose={() => setAdding(false)}
          onConnected={(connection, teams) => {
            setAdding(false)
            setError(null)
            load()
            setChoosing({ connection, teams })
          }}
          onSessionLost={onSessionLost}
        />
      )}
      {choosing && (
        <ScopeDialog
          project={project}
          connection={choosing.connection}
          teams={choosing.teams}
          onClose={() => setChoosing(null)}
          onSaved={() => {
            setChoosing(null)
            load()
          }}
          onSessionLost={onSessionLost}
        />
      )}
    </Section>
  )
}

const STATUS_LABELS: Record<TicketConnection["status"], string> = {
  pending: t("Elige los equipos"),
  active: t("Conectado"),
  error: t("Con errores"),
  revoked: t("Revocado"),
  disabled: t("Desactivado"),
}

const AUTH_LABELS: Record<TicketConnection["auth"], string> = {
  api_key: t("Clave personal"),
  oauth: t("OAuth (renovable)"),
  basic: t("Token de API de Jira Cloud"),
  pat: t("Token personal de Jira"),
}

function ConnectionRow({
  project,
  connection,
  onChanged,
  onChooseTeams,
  onSessionLost,
}: {
  project: Project
  connection: TicketConnection
  onChanged: () => void
  onChooseTeams: (teams: TrackerTeam[]) => void
  onSessionLost: (error: unknown) => void
}) {
  const [busy, setBusy] = useState<"sync" | "teams" | "remove" | "options" | null>(null)
  const { error, setError, fail } = useErrors(onSessionLost)
  const provider = PROVIDERS[connection.provider]
  const run = (kind: NonNullable<typeof busy>, action: () => Promise<unknown>) => {
    setBusy(kind)
    setError(null)
    action()
      .catch(fail)
      .finally(() => setBusy(null))
  }
  const sync = () => run("sync", () => syncTickets(project.id, connection.id).then(onChanged))
  const teams = () =>
    run("teams", () => getTrackerTeams(project.id, connection.id).then(({ teams: rows }) => onChooseTeams(rows)))
  const option = (change: { read_text?: boolean; allow_comments?: boolean }) =>
    run("options", () => setTrackerOptions(project.id, connection.id, change).then(onChanged))
  const remove = async () => {
    const oauth = connection.auth === "oauth"
    const answer = await Confirm.call({
      tone: "danger",
      icon: Trash2,
      title: t("¿Quitar la conexión con {0}?", provider),
      description: t("Se borran de Scrinium los {0} tickets guardados, sus enlaces con reglas y la credencial del llavero.", connection.tickets ?? 0),
      content: (
        <p className="text-sm text-muted-foreground">
          {oauth
            ? t("El acceso se revoca también en Linear.")
            : t("La clave sigue siendo válida en {0} hasta que la revoques allí: Scrinium no puede revocar una clave personal.", provider)}
        </p>
      ),
      confirmLabel: t("Quitar"),
    })
    if (answer.confirmed) run("remove", () => removeTicketConnection(project.id, connection.id).then(onChanged))
  }
  return (
    <div className="grid gap-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{provider}</span>
          {connection.display_name && <span className="text-muted-foreground">· {connection.display_name}</span>}
          <StatusBadge tone={connection.status === "active" ? "verified" : "attention"}>
            {STATUS_LABELS[connection.status]}
          </StatusBadge>
        </span>
        <span className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy !== null} onClick={teams}>
            {busy === "teams" ? t("Cargando…") : connection.provider === "jira" ? t("Proyectos") : t("Equipos")}
          </Button>
          <Button size="sm" variant="outline" disabled={busy !== null || connection.scope.length === 0} onClick={sync}>
            <RefreshCw data-icon="inline-start" />
            {busy === "sync" ? t("Sincronizando…") : t("Sincronizar")}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => void remove()}>
            <Trash2 data-icon="inline-start" />
            {t("Quitar")}
          </Button>
        </span>
      </div>
      <dl className="divide-y text-sm">
        <InfoRow label={connection.provider === "jira" ? t("Proyectos") : t("Equipos")}>
          {connection.scope.length > 0 ? connection.scope.map((team) => team.key).join(", ") : t("Ninguno todavía")}
        </InfoRow>
        <InfoRow label={t("Tickets guardados")}>{connection.tickets ?? 0}</InfoRow>
        <InfoRow label={t("Última sincronización")}>
          {connection.last_sync_at ? relativeTime(connection.last_sync_at) : t("Nunca")}
          <Muted>{t("Se comprueba sola cada pocos minutos.")}</Muted>
        </InfoRow>
        <InfoRow label={t("Acceso")}>
          {AUTH_LABELS[connection.auth]}
          {connection.key_hint && <code className="ml-1">…{connection.key_hint}</code>}{" "}
          <Muted>{t("en el llavero del sistema")}</Muted>
        </InfoRow>
      </dl>
      <div className="grid gap-2 rounded-md bg-muted/40 p-3">
        <OptionToggle
          icon={FileText}
          checked={connection.read_text}
          disabled={busy !== null}
          onChange={(value) => option({ read_text: value })}
          title={t("Leer descripciones y comentarios")}
          text={t("Sirve para seguir trazas de error desde un bug. Se guardan solo en este equipo, sin autores, y nunca van a un modelo. Al desactivarlo se borran.")}
        />
        <OptionToggle
          icon={MessageSquare}
          checked={connection.allow_comments}
          disabled={busy !== null}
          onChange={(value) => option({ allow_comments: value })}
          title={t("Permitir comentar en tickets")}
          text={t("Podrás publicar en un ticket las reglas que toca, siempre revisando el texto y confirmando cada comentario. La credencial necesita permiso de escritura.")}
        />
      </div>
      {connection.last_error && <ErrorText>{errorMessage(connectionError(connection.last_error))}</ErrorText>}
      <ErrorText>{error}</ErrorText>
    </div>
  )
}

function OptionToggle({
  icon: Icon,
  checked,
  disabled,
  onChange,
  title,
  text,
}: {
  icon: typeof FileText
  checked: boolean
  disabled: boolean
  onChange: (value: boolean) => void
  title: string
  text: string
}) {
  return (
    <label className="flex cursor-pointer gap-3 text-sm">
      <input
        type="checkbox"
        className="mt-0.5 size-4 shrink-0 accent-primary"
        checked={checked}
        disabled={disabled}
        onChange={(event) => onChange(event.target.checked)}
      />
      <span className="grid gap-0.5">
        <span className="flex items-center gap-1.5 font-medium">
          <Icon className="size-3.5 text-muted-foreground" aria-hidden="true" />
          {title}
        </span>
        <span className="text-xs text-muted-foreground">{text}</span>
      </span>
    </label>
  )
}

/** The sync stores a stable code; the screen shows the message for the matching API error. */
function connectionError(code: string) {
  if (code === "auth") return "token_rejected"
  if (code === "rate_limited") return "rate_limited"
  if (code === "bad_response") return "tracker_bad_response"
  return "tracker_unavailable"
}

function Field({ id, label, children, hint }: { id: string; label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <Muted>{hint}</Muted>}
    </div>
  )
}

function ConnectDialog({
  project,
  onClose,
  onConnected,
  onSessionLost,
}: {
  project: Project
  onClose: () => void
  onConnected: (connection: TicketConnection, teams: TrackerTeam[]) => void
  onSessionLost: (error: unknown) => void
}) {
  const [provider, setProvider] = useState<"linear" | "jira">("linear")
  const [jiraKind, setJiraKind] = useState<"basic" | "pat">("basic")
  const [token, setToken] = useState("")
  const [site, setSite] = useState("")
  const [email, setEmail] = useState("")
  const [busy, setBusy] = useState(false)
  const [oauth, setOauth] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  useEffect(() => {
    getLinearOAuth()
      .then(({ available }) => setOauth(available))
      .catch(() => setOauth(false))
  }, [])
  const viaOAuth = () => {
    setBusy(true)
    setError(null)
    startLinearOAuth(project.id)
      .then(({ authorize_url }) => window.location.assign(authorize_url))
      .catch((reason: unknown) => {
        setBusy(false)
        fail(reason)
      })
  }
  const save = (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    const request: ConnectRequest =
      provider === "linear"
        ? { provider, token: token.trim() }
        : { provider, token: token.trim(), auth: jiraKind, base_url: site.trim(), email: email.trim() }
    connectTracker(project.id, request)
      .then(({ teams, ...connection }) => onConnected(connection, teams))
      .catch(fail)
      .finally(() => {
        // The token leaves the screen's memory whatever the result.
        setToken("")
        setBusy(false)
      })
  }
  const ready = token.trim() && (provider === "linear" || (site.trim() && (jiraKind === "pat" || email.trim())))
  return (
    <Dialog
      open
      onOpenChange={(next) => !next && onClose()}
      title={t("Conectar un tracker")}
      description={t("Scrinium lee los tickets para explicar las reglas. La credencial se prueba antes de guardarse, va al llavero del sistema y solo se envía a ese servidor.")}
    >
      <Tabs value={provider} onValueChange={(value) => setProvider(value as "linear" | "jira")}>
        <TabsList aria-label={t("Tracker")}>
          <TabsTrigger value="linear">Linear</TabsTrigger>
          <TabsTrigger value="jira">Jira</TabsTrigger>
        </TabsList>
      </Tabs>
      {provider === "linear" && oauth && (
        <div className="grid gap-2 rounded-md bg-muted/40 p-3">
          <Button type="button" disabled={busy} onClick={viaOAuth}>
            {t("Conectar con Linear")}
          </Button>
          <Muted>{t("Abre Linear para dar permiso de lectura. Recomendado: el acceso se renueva solo y se revoca al quitarlo.")}</Muted>
        </div>
      )}
      <form className="grid gap-3" onSubmit={save} autoComplete="off">
        {provider === "linear" ? (
          <Field
            id="tracker-token"
            label={oauth ? t("O con una clave personal de Linear") : t("Clave de API de Linear")}
            hint={t("Créala en los ajustes de tu cuenta de Linear, con acceso de lectura si Linear lo ofrece.")}
          >
            <Input id="tracker-token" type="password" autoComplete="new-password" spellCheck={false}
              placeholder="lin_api_…" value={token} onChange={(event) => setToken(event.target.value)} />
          </Field>
        ) : (
          <>
            <Tabs value={jiraKind} onValueChange={(value) => setJiraKind(value as "basic" | "pat")}>
              <TabsList aria-label={t("Tipo de Jira")}>
                <TabsTrigger value="basic">{t("Jira Cloud")}</TabsTrigger>
                <TabsTrigger value="pat">{t("Jira Data Center")}</TabsTrigger>
              </TabsList>
            </Tabs>
            <Field id="tracker-site" label={t("Dirección del sitio")}>
              <Input id="tracker-site" type="url" required
                placeholder={jiraKind === "basic" ? "https://empresa.atlassian.net" : "https://jira.empresa.com"}
                value={site} onChange={(event) => setSite(event.target.value)} />
            </Field>
            {jiraKind === "basic" && (
              <Field id="tracker-email" label={t("E-mail de tu cuenta de Atlassian")}>
                <Input id="tracker-email" type="email" required value={email} onChange={(event) => setEmail(event.target.value)} />
              </Field>
            )}
            <Field
              id="tracker-token"
              label={jiraKind === "basic" ? t("Token de API") : t("Token de acceso personal")}
              hint={jiraKind === "basic"
                ? t("Créalo en id.atlassian.com, en Seguridad › Tokens de API.")
                : t("Créalo en tu perfil de Jira, en Tokens de acceso personal.")}
            >
              <Input id="tracker-token" type="password" autoComplete="new-password" spellCheck={false}
                value={token} onChange={(event) => setToken(event.target.value)} />
            </Field>
          </>
        )}
        <ErrorText>{error}</ErrorText>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("Cancelar")}
          </Button>
          <Button type="submit" disabled={busy || !ready}>
            <KeyRound data-icon="inline-start" />
            {busy ? t("Comprobando…") : t("Conectar")}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function ScopeDialog({
  project,
  connection,
  teams,
  onClose,
  onSaved,
  onSessionLost,
}: {
  project: Project
  connection: TicketConnection
  teams: TrackerTeam[]
  onClose: () => void
  onSaved: () => void
  onSessionLost: (error: unknown) => void
}) {
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(connection.scope.map((team) => team.id)))
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const jira = connection.provider === "jira"
  const leaving = connection.scope.filter((team) => !chosen.has(team.id))
  const toggle = (id: string) => {
    const next = new Set(chosen)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    setChosen(next)
  }
  const save = (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    setTrackerScope(project.id, connection.id, [...chosen])
      .then(onSaved)
      .catch(fail)
      .finally(() => setBusy(false))
  }
  return (
    <Dialog
      open
      onOpenChange={(next) => !next && onClose()}
      title={jira ? t("Qué proyectos de Jira lee este proyecto") : t("Qué equipos lee este proyecto")}
      description={t("Solo se leen estos tickets. La primera lectura los trae todos; después, solo los que cambian.")}
    >
      <form className="grid gap-3" onSubmit={save}>
        {teams.length === 0 ? (
          <Muted>{t("La credencial no ve ninguno.")}</Muted>
        ) : (
          <ul className="grid max-h-72 gap-1 overflow-y-auto">
            {teams.map((team) => (
              <li key={team.id}>
                <label className="flex items-center gap-2 rounded-md px-1 py-1 text-sm hover:bg-muted">
                  <input type="checkbox" className="size-4 accent-primary" checked={chosen.has(team.id)} onChange={() => toggle(team.id)} />
                  <code className="text-xs">{team.key}</code>
                  <span>{team.name}</span>
                </label>
              </li>
            ))}
          </ul>
        )}
        {leaving.length > 0 && (
          <p className="text-sm text-muted-foreground">
            {t("Se borrarán los tickets guardados de: {0}.", leaving.map((team) => team.key).join(", "))}
          </p>
        )}
        <ErrorText>{error}</ErrorText>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={onClose}>
            {t("Cancelar")}
          </Button>
          <Button type="submit" disabled={busy || chosen.size === 0}>
            {busy ? t("Guardando…") : t("Guardar y leer")}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}
