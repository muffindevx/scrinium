import { History } from "lucide-react"
import { useState } from "react"
import { Section } from "@/components/page"
import { Button } from "@/components/ui/button"
import { type RuleHistory as Loaded, getRuleHistory } from "@/lib/api"
import { t } from "@/lib/i18n"
import { relativeTime } from "@/lib/time"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted } from "@/screens/shared"

/**
 * Since when the rule decides with these values (phase 7.2): each period of its decision's
 * values in the Git history, found by bisection over the commits of its file. No AI.
 */
export function RuleHistory({
  projectId,
  entityId,
  onSessionLost,
}: {
  projectId: string
  entityId: string
  onSessionLost: (error: unknown) => void
}) {
  const [history, setHistory] = useState<Loaded | null>(null)
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const load = () => {
    setBusy(true)
    setError(null)
    getRuleHistory(projectId, entityId)
      .then(setHistory)
      .catch(fail)
      .finally(() => setBusy(false))
  }
  return (
    <Section
      title={t("Historia")}
      description={t("Desde cuándo la regla decide con estos valores, según el historial de Git. Sin IA.")}
    >
      {!history && (
        <div>
          <Button size="sm" variant="outline" disabled={busy} onClick={load}>
            <History data-icon="inline-start" />
            {busy ? t("Buscando en el historial…") : t("Ver la historia")}
          </Button>
        </div>
      )}
      <ErrorText>{error}</ErrorText>
      {history?.anchors.map((anchor) => (
        <div key={`${anchor.path}${anchor.symbol}`} className="grid gap-2">
          <p className="font-mono text-xs break-all text-muted-foreground">
            {anchor.path}
            {anchor.symbol && ` · ${anchor.symbol}`}
          </p>
          <ol className="grid gap-1 text-sm">
            {anchor.periods.map((period) => (
              <li key={period.since.sha} className="flex flex-wrap items-baseline gap-x-2">
                <code className="text-xs">{period.values ? period.values.join(", ") || "—" : t("no estaba")}</code>
                <span>
                  {period.until ? t("desde {0} hasta {1}", period.since.date.slice(0, 10), period.until.date.slice(0, 10)) : t("desde {0} ({1})", period.since.date.slice(0, 10), relativeTime(period.since.date))}
                </span>
                <Muted>
                  {period.since.sha.slice(0, 8)} · {period.since.subject} · {period.since.author}
                </Muted>
              </li>
            ))}
          </ol>
          <Muted>
            {t("{0} commits del archivo, {1} leídos, {2} s.", anchor.commits, anchor.parsed, anchor.seconds)}
          </Muted>
        </div>
      ))}
    </Section>
  )
}
