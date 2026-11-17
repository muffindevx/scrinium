import { t } from "@/lib/i18n"
import type { Source, VerificationStatus } from "@/lib/api"

/** The six statuses of the whole UI: the same colour, icon and shape on every screen. */
export type Tone = "verified" | "proposed" | "discarded" | "no_evidence" | "in_progress" | "attention"

export const VERIFIED_STATUSES: VerificationStatus[] = ["human_verified", "source_supported", "test_supported"]

export function sourceTone(source: Source): [Tone, string] {
  const run = source.latest_sync
  if (run?.status === "queued" || run?.status === "running") return ["in_progress", t("Sincronizando…")]
  if (source.status === "error" || run?.status === "failed") return ["attention", t("Error de fuente")]
  if (!source.last_successful_sync_at) return ["no_evidence", t("Sin sincronizar")]
  return ["verified", t("Sincronizado")]
}
