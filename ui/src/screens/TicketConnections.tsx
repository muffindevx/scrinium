import { KeyRound, RefreshCw, Ticket, Trash2 } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useState } from "react"
import { InfoRow, Section } from "@/components/page"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { Input, Label } from "@/components/ui/input"
import {
  type Project,
  type TicketConnection,
  type TrackerTeam,
  connectLinear,
  getTrackerTeams,
  listTicketConnections,
  removeTicketConnection,
  setTrackerScope,
  syncTickets,
} from "@/lib/api"
import { t } from "@/lib/i18n"
import { errorMessage } from "@/lib/labels"
import { relativeTime } from "@/lib/time"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted } from "@/screens/shared"

/**
 * Ticket trackers this project reads (phase 10, ADR-014). Read-only: Scrinium never writes to
 * the tracker. The key goes to the system keyring and never comes back to this screen.
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
  const { error, setError, fail } = useErrors(onSessionLost)
  const load = useCallback(() => {
    listTicketConnections(project.id)
      .then(({ connections: rows }) => setConnections(rows))
      .catch(fail)
  }, [project.id, fail])
  useEffect(load, [load])

  return (
    <Section
      title={
        <span className="flex items-center gap-2">
          <Ticket className="size-4" />
          {t("Tickets")}
        </span>
      }
      description={t("Los tickets explican por qué existe una regla y por qué cambió. Scrinium solo los lee: título, estado, tipo, etiquetas y enlaces, nunca descripciones, comentarios ni personas.")}
      action={
        connections && connections.length === 0 ? (
          <Button size="sm" variant="outline" onClick={() => setAdding(true)}>
            <KeyRound data-icon="inline-start" />
            {t("Conectar Linear")}
          </Button>
        ) : undefined
      }
    >
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
      <ConnectDialog
        open={adding}
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
  const [busy, setBusy] = useState<"sync" | "teams" | "remove" | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [removed, setRemoved] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const run = (kind: "sync" | "teams" | "remove", action: () => Promise<unknown>) => {
    setBusy(kind)
    setError(null)
    action()
      .catch(fail)
      .finally(() => setBusy(null))
  }
  const sync = () => run("sync", () => syncTickets(project.id, connection.id).then(onChanged))
  const teams = () =>
    run("teams", () => getTrackerTeams(project.id, connection.id).then(({ teams: rows }) => onChooseTeams(rows)))
  const remove = () =>
    run("remove", () =>
      removeTicketConnection(project.id, connection.id).then(() => {
        setConfirming(false)
        setRemoved(true)
        onChanged()
      }),
    )
  if (removed) return null
  return (
    <div className="grid gap-3 rounded-lg border p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{t("Linear")}</span>
          {connection.display_name && <span className="text-muted-foreground">· {connection.display_name}</span>}
          <StatusBadge tone={connection.status === "active" ? "verified" : "attention"}>
            {STATUS_LABELS[connection.status]}
          </StatusBadge>
        </span>
        <span className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy !== null} onClick={teams}>
            {busy === "teams" ? t("Cargando…") : t("Equipos")}
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={busy !== null || connection.scope.length === 0}
            onClick={sync}
          >
            <RefreshCw data-icon="inline-start" />
            {busy === "sync" ? t("Sincronizando…") : t("Sincronizar")}
          </Button>
          <Button size="sm" variant="ghost" disabled={busy !== null} onClick={() => setConfirming(true)}>
            <Trash2 data-icon="inline-start" />
            {t("Quitar")}
          </Button>
        </span>
      </div>
      <dl className="divide-y text-sm">
        <InfoRow label={t("Equipos")}>
          {connection.scope.length > 0 ? connection.scope.map((team) => team.key).join(", ") : t("Ninguno todavía")}
        </InfoRow>
        <InfoRow label={t("Tickets guardados")}>{connection.tickets ?? 0}</InfoRow>
        <InfoRow label={t("Última sincronización")}>
          {connection.last_sync_at ? relativeTime(connection.last_sync_at) : t("Nunca")}
        </InfoRow>
        <InfoRow label={t("Clave")}>
          <code>…{connection.key_hint}</code> <Muted>{t("en el llavero del sistema")}</Muted>
        </InfoRow>
      </dl>
      {connection.last_error && <ErrorText>{errorMessage(connectionError(connection.last_error))}</ErrorText>}
      <ErrorText>{error}</ErrorText>
      <Dialog
        open={confirming}
        onOpenChange={(next) => !next && setConfirming(false)}
        title={t("Quitar la conexión con Linear")}
        description={t("Se borran de Scrinium los {0} tickets guardados, sus enlaces con reglas y la clave del llavero.", connection.tickets ?? 0)}
      >
        <p className="text-sm">
          {t("La clave sigue siendo válida en Linear hasta que la revoques en los ajustes de tu cuenta de Linear. Scrinium no puede revocar una clave personal.")}
        </p>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={() => setConfirming(false)}>
            {t("Cancelar")}
          </Button>
          <Button type="button" variant="destructive" disabled={busy === "remove"} onClick={remove}>
            {busy === "remove" ? t("Quitando…") : t("Quitar")}
          </Button>
        </div>
      </Dialog>
    </div>
  )
}

/** The sync stores a stable code; the screen shows the message for the matching API error. */
function connectionError(code: string) {
  if (code === "auth") return "token_rejected"
  if (code === "rate_limited") return "rate_limited"
  if (code === "bad_response") return "tracker_bad_response"
  return "tracker_unavailable"
}

function ConnectDialog({
  open,
  project,
  onClose,
  onConnected,
  onSessionLost,
}: {
  open: boolean
  project: Project
  onClose: () => void
  onConnected: (connection: TicketConnection, teams: TrackerTeam[]) => void
  onSessionLost: (error: unknown) => void
}) {
  const [token, setToken] = useState("")
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const close = () => {
    setToken("")
    setError(null)
    onClose()
  }
  const save = (event: FormEvent) => {
    event.preventDefault()
    setBusy(true)
    setError(null)
    connectLinear(project.id, token.trim())
      .then(({ teams, ...connection }) => onConnected(connection, teams))
      .catch(fail)
      .finally(() => {
        // The key leaves the screen's memory whatever the result.
        setToken("")
        setBusy(false)
      })
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => !next && close()}
      title={t("Conectar Linear")}
      description={t("Scrinium solo lee. Crea una clave personal en los ajustes de tu cuenta de Linear, con acceso de lectura si Linear lo ofrece.")}
    >
      <form className="grid gap-3" onSubmit={save} autoComplete="off">
        <div className="grid gap-1.5">
          <Label htmlFor="linear-key">{t("Clave de API de Linear")}</Label>
          <Input
            id="linear-key"
            required
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            placeholder="lin_api_…"
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
          <Muted>{t("Se prueba con Linear antes de guardarla. Va al llavero del sistema y no vuelve a mostrarse; solo se envía a api.linear.app.")}</Muted>
        </div>
        <ErrorText>{error}</ErrorText>
        <div className="flex justify-end gap-2">
          <Button type="button" variant="ghost" onClick={close}>
            {t("Cancelar")}
          </Button>
          <Button type="submit" disabled={busy || !token.trim()}>
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
      title={t("Qué equipos lee este proyecto")}
      description={t("Solo se leen los tickets de estos equipos. La primera lectura trae todos sus tickets; después, solo los que cambian.")}
    >
      <form className="grid gap-3" onSubmit={save}>
        {teams.length === 0 ? (
          <Muted>{t("La clave no ve ningún equipo.")}</Muted>
        ) : (
          <ul className="grid max-h-72 gap-1 overflow-y-auto">
            {teams.map((team) => (
              <li key={team.id}>
                <label className="flex items-center gap-2 rounded-md px-1 py-1 text-sm hover:bg-muted">
                  <input
                    type="checkbox"
                    className="size-4 accent-primary"
                    checked={chosen.has(team.id)}
                    onChange={() => toggle(team.id)}
                  />
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
