import { ExternalLink } from "lucide-react"
import { useEffect, useState } from "react"
import { Link } from "react-router"
import { RuleLink } from "@/components/rule-link"
import { StatusBadge } from "@/components/status"
import { Input } from "@/components/ui/input"
import { type TicketInfo, type TicketTriage as Loaded, searchBugTickets, triageTicket } from "@/lib/api"
import { t } from "@/lib/i18n"
import { shortSha } from "@/lib/labels"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Eyebrow, Muted } from "@/screens/shared"

/**
 * Triage from a bug ticket (phase 10.5): its commits → the files they touched → the rules cited
 * there, and resolved bugs with a similar title. The database and local Git only, no AI.
 */
export function TicketTriage({ projectId, onSessionLost }: { projectId: string; onSessionLost: (error: unknown) => void }) {
  const [query, setQuery] = useState("")
  const [bugs, setBugs] = useState<TicketInfo[] | null>(null)
  const [result, setResult] = useState<Loaded | null>(null)
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  useEffect(() => {
    const timer = window.setTimeout(() => {
      searchBugTickets(projectId, query.trim())
        .then(({ tickets }) => setBugs(tickets))
        .catch(fail)
    }, 200)
    return () => window.clearTimeout(timer)
  }, [projectId, query, fail])
  const choose = (ticket: TicketInfo) => {
    setBusy(true)
    setError(null)
    triageTicket(projectId, ticket.id)
      .then(setResult)
      .catch(fail)
      .finally(() => setBusy(false))
  }
  return (
    <div className="grid gap-4">
      <div className="grid gap-2">
        <Input
          aria-label={t("Buscar un ticket de bug")}
          placeholder={t("Clave o título de un bug, como SHOP-230")}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        {bugs?.length === 0 && (
          <Muted>
            {query.trim() ? t("Ningún bug guardado coincide.") : t("No hay bugs guardados.")}{" "}
            <Link to="/fuentes" className="font-medium text-foreground underline-offset-4 hover:underline">
              {t("Conecta Linear en Fuentes")}
            </Link>
          </Muted>
        )}
        {bugs && bugs.length > 0 && (
          <ul className="grid max-h-56 gap-1 overflow-y-auto">
            {bugs.map((ticket) => (
              <li key={ticket.id}>
                <button
                  type="button"
                  disabled={busy}
                  className="flex w-full items-baseline gap-2 rounded-md px-2 py-1 text-left text-sm hover:bg-muted aria-pressed:bg-muted"
                  aria-pressed={result?.ticket.id === ticket.id}
                  onClick={() => choose(ticket)}
                >
                  <code className="text-xs">{ticket.key}</code>
                  <span className="min-w-0 truncate">{ticket.title}</span>
                  {ticket.status && <span className="ml-auto shrink-0 text-xs text-muted-foreground">{ticket.status}</span>}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <ErrorText>{error}</ErrorText>
      {busy && <Muted>{t("Buscando en el historial…")}</Muted>}
      {result && !busy && <TriageResult result={result} />}
    </div>
  )
}

function RuleRow({ rule }: { rule: Loaded["rules"][number] }) {
  return (
    <li className="flex flex-wrap items-baseline gap-x-2">
      <RuleLink id={rule.id} className="font-medium hover:underline">
        {rule.title}
      </RuleLink>
      <span className="font-mono text-xs break-all text-muted-foreground">
        {rule.path}:{rule.start_line}
      </span>
      {rule.commits.length > 0 && <StatusBadge tone="attention">{t("sus líneas cambiaron")}</StatusBadge>}
    </li>
  )
}

function TriageResult({ result }: { result: Loaded }) {
  const { ticket } = result
  const touched = result.rules.filter((rule) => rule.commits.length > 0)
  const nearby = result.rules.filter((rule) => rule.commits.length === 0)
  return (
    <div className="grid gap-4 border-t pt-4">
      <p className="flex flex-wrap items-center gap-2 text-sm">
        <code className="text-xs font-medium">{ticket.key}</code>
        {ticket.url ? (
          <a href={ticket.url} target="_blank" rel="noreferrer noopener" className="inline-flex items-center gap-1 font-medium hover:underline">
            {ticket.title}
            <ExternalLink aria-hidden="true" className="size-3 text-muted-foreground" />
          </a>
        ) : (
          <span className="font-medium">{ticket.title}</span>
        )}
        {ticket.status && <StatusBadge tone={ticket.status_category === "done" ? "verified" : "attention"}>{ticket.status}</StatusBadge>}
      </p>
      <div className="grid gap-2">
        <Eyebrow>{t("Reglas")}</Eyebrow>
        {result.rules.length === 0 && <Muted>{t("Ninguna regla está citada en los archivos de estos commits.")}</Muted>}
        {touched.length > 0 && (
          <ul className="grid gap-1 text-sm">
            {touched.map((rule) => (
              <RuleRow key={rule.id} rule={rule} />
            ))}
          </ul>
        )}
        {touched.length === 0 && nearby.length > 0 && (
          <Muted>{t("Ningún commit cambió las líneas de una regla.")}</Muted>
        )}
        {nearby.length > 0 && (
          <details className="text-sm">
            <summary className="cursor-pointer text-muted-foreground">
              {t("{0} reglas más en archivos que cambiaron", nearby.length)}
            </summary>
            <ul className="mt-2 grid gap-1">
              {nearby.map((rule) => (
                <RuleRow key={rule.id} rule={rule} />
              ))}
            </ul>
          </details>
        )}
      </div>
      <div className="grid gap-2">
        <Eyebrow>{t("Commits")}</Eyebrow>
        {result.commits.length === 0 ? (
          <Muted>{t("Ningún commit nombra este ticket ni está enlazado a él.")}</Muted>
        ) : (
          <ul className="grid gap-1 text-sm">
            {result.commits.map((commit) => (
              <li key={commit.sha} className="grid gap-0.5">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <code className="text-xs">{shortSha(commit.sha)}</code>
                  <span>{commit.subject}</span>
                  <Muted>
                    {commit.author} · {commit.date.slice(0, 10)} · {commit.level === "explicit" ? t("enlazado en Linear") : t("lo nombra")}
                  </Muted>
                </span>
                {commit.files.length > 0 && (
                  <span className="font-mono text-xs break-all text-muted-foreground">{commit.files.join(", ")}</span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
      {result.similar.length > 0 && (
        <div className="grid gap-2">
          <Eyebrow>{t("Bugs resueltos parecidos")}</Eyebrow>
          <ul className="grid gap-1 text-sm">
            {result.similar.map((other) => (
              <li key={other.id} className="flex flex-wrap items-baseline gap-x-2">
                <code className="text-xs">{other.key}</code>
                {other.url ? (
                  <a href={other.url} target="_blank" rel="noreferrer noopener" className="hover:underline">
                    {other.title}
                  </a>
                ) : (
                  <span>{other.title}</span>
                )}
                <Muted>{t("parecido {0} %", Math.round(other.similarity * 100))}</Muted>
              </li>
            ))}
          </ul>
        </div>
      )}
      <Muted>{t("Sale del historial de Git y de los tickets guardados, sin IA: dice dónde mirar, no la causa.")}</Muted>
    </div>
  )
}
