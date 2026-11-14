import { StatusBadge } from "@/components/status"
import type { ProviderCheck, RetentionNotice } from "@/lib/api"
import { CHECK_LABELS } from "@/lib/ai"

export function RetentionBadge({ notice }: { notice: RetentionNotice }) {
  if (!notice.verified) return <StatusBadge tone="attention">Sin verificar</StatusBadge>
  return (
    <StatusBadge tone={notice.stale ? "attention" : "verified"}>
      {notice.stale ? "Comprobado hace más de 6 meses" : `Comprobado el ${notice.checked_on}`}
    </StatusBadge>
  )
}

/** What the provider says it keeps, read before consenting (ADR-010), in a popover. */
export function RetentionDetails({ notice }: { notice: RetentionNotice }) {
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

export function CheckResult({ check }: { check: ProviderCheck | "running" | null }) {
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
