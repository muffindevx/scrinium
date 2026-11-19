import { Check, ExternalLink, Lightbulb, Link2, MessageSquare, X } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { Link } from "react-router"
import { Section } from "@/components/page"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Confirm } from "@/components/ui/confirm"
import { Input } from "@/components/ui/input"
import {
  type RuleTicket,
  type RuleTickets as Loaded,
  type TicketInfo,
  type TicketLinkLevel,
  getRuleTickets,
  commentOnTicket,
  dismissProposal,
  getCommentDraft,
  linkRuleTicket,
  searchTickets,
  unlinkRuleTicket,
} from "@/lib/api"
import { t } from "@/lib/i18n"
import { shortSha } from "@/lib/labels"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted } from "@/screens/shared"

const LEVEL_LABELS: Record<TicketLinkLevel, string> = {
  confirmed: t("Confirmado"),
  explicit: t("Enlazado en el tracker"),
  mentioned: t("Mencionado en un commit"),
  inferred: t("Propuesto"),
}

const LEVEL_HELP: Record<TicketLinkLevel, string> = {
  confirmed: t("Una persona lo enlazó a esta regla."),
  explicit: t("El tracker adjunta al ticket un commit que cambió las líneas de esta regla."),
  mentioned: t("El mensaje de un commit que cambió las líneas de esta regla nombra este ticket."),
  inferred: t("Parecido por fecha, archivos y texto: sin confirmar."),
}

const CATEGORY_TONES: Record<TicketInfo["status_category"], "verified" | "attention" | "no_evidence"> = {
  "": "no_evidence",
  open: "attention",
  started: "attention",
  done: "verified",
  canceled: "no_evidence",
}

/**
 * Why the rule exists and why it changed (phase 10.4): the tickets reached through the commits
 * behind its lines, and those a person linked. Nothing is guessed. No AI.
 */
export function RuleTickets({
  projectId,
  entityId,
  onSessionLost,
}: {
  projectId: string
  entityId: string
  onSessionLost: (error: unknown) => void
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null)
  const [linking, setLinking] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const load = useCallback(() => {
    getRuleTickets(projectId, entityId).then(setLoaded).catch(fail)
  }, [projectId, entityId, fail])
  useEffect(load, [load])
  const unlink = (ticket: RuleTicket) => {
    setError(null)
    unlinkRuleTicket(projectId, entityId, ticket.id).then(load).catch(fail)
  }
  const confirmProposal = (ticketId: string) => {
    setError(null)
    linkRuleTicket(projectId, entityId, ticketId).then(setLoaded).catch(fail)
  }
  const dismiss = (ticketId: string) => {
    setError(null)
    dismissProposal(projectId, entityId, ticketId).then(setLoaded).catch(fail)
  }
  // 10.7: a comment on the ticket, from a draft the person edits and confirms; never sent alone.
  const comment = async (ticket: RuleTicket) => {
    setError(null)
    try {
      const draft = await getCommentDraft(projectId, ticket.id)
      if (!draft.allowed) {
        await Confirm.call({
          icon: MessageSquare,
          title: t("Comentar no está permitido"),
          description: t("Actívalo en Fuentes › Tickets, en la conexión de este tracker: «Permitir comentar en tickets»."),
          confirmLabel: t("Entendido"),
          cancelLabel: null,
        })
        return
      }
      const answer = await Confirm.call({
        icon: MessageSquare,
        title: t("Comentar en {0}", ticket.key),
        description: t("Se publicará este texto en el ticket, con tu credencial. Revísalo antes."),
        input: { label: t("Comentario"), initial: draft.body, required: true, maxLength: 10_000 },
        confirmLabel: t("Publicar el comentario"),
      })
      if (!answer.confirmed) return
      await commentOnTicket(projectId, ticket.id, answer.value)
      await Confirm.call({
        tone: "success",
        icon: Check,
        title: t("Comentario publicado en {0}", ticket.key),
        confirmLabel: t("Entendido"),
        cancelLabel: null,
      })
    } catch (reason) {
      fail(reason)
    }
  }
  const proposed = loaded?.proposed ?? []
  const empty = loaded !== null && loaded.tickets.length === 0 && loaded.unmatched_keys.length === 0 && proposed.length === 0
  return (
    <Section
      title={t("Por qué")}
      description={t("Los tickets que explican la regla: los que nombran o enlazan los commits que cambiaron sus líneas, y los que enlazó una persona. Sin IA.")}
      action={
        <Button size="sm" variant="outline" onClick={() => setLinking(!linking)}>
          <Link2 data-icon="inline-start" />
          {t("Enlazar un ticket")}
        </Button>
      }
    >
      {linking && (
        <TicketSearch
          projectId={projectId}
          onPick={(ticket) => {
            setError(null)
            linkRuleTicket(projectId, entityId, ticket.id)
              .then((next) => {
                setLoaded(next)
                setLinking(false)
              })
              .catch(fail)
          }}
          onSessionLost={onSessionLost}
        />
      )}
      {empty && !linking && (
        <Muted>
          {t("Ningún commit de estas líneas nombra un ticket.")}{" "}
          <Link to="/fuentes" className="font-medium text-foreground underline-offset-4 hover:underline">
            {t("Conecta Linear o Jira en Fuentes")}
          </Link>{" "}
          {t("para ver títulos y estados, o enlaza uno a mano.")}
        </Muted>
      )}
      {loaded && loaded.tickets.length > 0 && (
        <ul className="grid gap-2">
          {loaded.tickets.map((ticket) => (
            <TicketRow key={ticket.id} ticket={ticket} onUnlink={() => unlink(ticket)} onComment={() => void comment(ticket)} />
          ))}
        </ul>
      )}
      {proposed.length > 0 && (
        <div className="grid gap-2 rounded-lg border border-dashed p-3">
          <p className="flex items-center gap-1.5 text-sm font-medium">
            <Lightbulb className="size-4 text-amber-500" aria-hidden="true" />
            {t("Quizá también la explican")}
          </p>
          <Muted>{t("Comparten palabras con la regla. No son un hecho hasta que los confirmes.")}</Muted>
          <ul className="grid gap-1.5">
            {proposed.map((ticket) => (
              <li key={ticket.id} className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
                <code className="text-xs">{ticket.key}</code>
                <span className="min-w-0 flex-1 truncate">{ticket.title}</span>
                <span className="text-xs text-muted-foreground">{ticket.shared_words.slice(0, 3).join(", ")}</span>
                <Button size="xs" variant="outline" className="text-emerald-700 dark:text-emerald-400" onClick={() => confirmProposal(ticket.id)}>
                  <Check data-icon="inline-start" />
                  {t("Sí, la explica")}
                </Button>
                <Button size="xs" variant="ghost" onClick={() => dismiss(ticket.id)}>
                  <X data-icon="inline-start" />
                  {t("Descartar")}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {loaded && loaded.unmatched_keys.length > 0 && (
        <p className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
          <span>{t("Nombrados en commits, sin ticket conocido:")}</span>
          {loaded.unmatched_keys.map((key) => (
            <code key={key} className="rounded-full border px-2 py-0.5 text-xs">
              {key}
            </code>
          ))}
        </p>
      )}
      <ErrorText>{error}</ErrorText>
    </Section>
  )
}

function TicketRow({ ticket, onUnlink, onComment }: { ticket: RuleTicket; onUnlink: () => void; onComment: () => void }) {
  return (
    <li className="grid gap-1 rounded-lg border p-3 text-sm">
      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <code className="text-xs font-medium">{ticket.key}</code>
        {ticket.url ? (
          <a
            href={ticket.url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-flex items-center gap-1 font-medium hover:underline"
          >
            {ticket.title}
            <ExternalLink aria-hidden="true" className="size-3 text-muted-foreground" />
          </a>
        ) : (
          <span className="font-medium">{ticket.title}</span>
        )}
        {ticket.deleted ? (
          <StatusBadge tone="no_evidence">{t("Borrado en el tracker")}</StatusBadge>
        ) : (
          ticket.status && <StatusBadge tone={CATEGORY_TONES[ticket.status_category]}>{ticket.status}</StatusBadge>
        )}
        {ticket.type === "bug" && <StatusBadge tone="attention">{t("Bug")}</StatusBadge>}
        <span className="ml-auto flex items-center gap-1">
          {!ticket.deleted && (
            <Button size="xs" variant="ghost" onClick={onComment} title={t("Comentar en el ticket las reglas que toca")}>
              <MessageSquare data-icon="inline-start" />
              {t("Comentar")}
            </Button>
          )}
          {ticket.level === "confirmed" && (
            <Button size="xs" variant="ghost" onClick={onUnlink} aria-label={t("Quitar el enlace con {0}", ticket.key)}>
              <X />
            </Button>
          )}
        </span>
      </span>
      <span className="flex flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
        <span title={LEVEL_HELP[ticket.level]}>{LEVEL_LABELS[ticket.level]}</span>
        {ticket.commits.length > 0 && (
          <span className="font-mono">{ticket.commits.map((sha) => shortSha(sha)).join(", ")}</span>
        )}
        {ticket.previous_keys.length > 0 && (
          <span>{t("antes {0}", ticket.previous_keys.join(", "))}</span>
        )}
      </span>
    </li>
  )
}

function TicketSearch({
  projectId,
  onPick,
  onSessionLost,
}: {
  projectId: string
  onPick: (ticket: TicketInfo) => void
  onSessionLost: (error: unknown) => void
}) {
  const [query, setQuery] = useState("")
  const [found, setFound] = useState<TicketInfo[] | null>(null)
  const { error, fail } = useErrors(onSessionLost)
  useEffect(() => {
    const timer = window.setTimeout(() => {
      searchTickets(projectId, query.trim())
        .then(({ tickets }) => setFound(tickets))
        .catch(fail)
    }, 200)
    return () => window.clearTimeout(timer)
  }, [projectId, query, fail])
  return (
    <div className="grid gap-2 rounded-lg border p-3">
      <Input
        autoFocus
        aria-label={t("Buscar un ticket por clave o título")}
        placeholder={t("Clave o título, como SHOP-221")}
        value={query}
        onChange={(event) => setQuery(event.target.value)}
      />
      {found?.length === 0 && (
        <Muted>
          {query.trim() ? t("Ningún ticket guardado coincide.") : t("No hay tickets guardados. Conecta Linear en Fuentes.")}
        </Muted>
      )}
      {found && found.length > 0 && (
        <ul className="grid max-h-60 gap-1 overflow-y-auto">
          {found.map((ticket) => (
            <li key={ticket.id}>
              <button
                type="button"
                className="flex w-full items-baseline gap-2 rounded-md px-2 py-1 text-left text-sm hover:bg-muted"
                onClick={() => onPick(ticket)}
              >
                <code className="text-xs">{ticket.key}</code>
                <span className="min-w-0 truncate">{ticket.title}</span>
                {ticket.deleted && <span className="text-xs text-muted-foreground">{t("borrado")}</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      <ErrorText>{error}</ErrorText>
    </div>
  )
}
