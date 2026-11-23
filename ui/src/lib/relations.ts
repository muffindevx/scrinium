import type { ImpactedEntity, Relationship } from "@/lib/api"
import { t } from "@/lib/i18n"

type Citation = NonNullable<ImpactedEntity["citation"]>

/** `path:line`, with only the file name and its folder: enough to tell two rules apart. */
export function where(citation: Citation | null | undefined): string | null {
  if (!citation) return null
  const parts = citation.path.split("/")
  return `${parts.slice(-2).join("/")}:${citation.start_line}`
}

/** A relationship in words, from this rule's side. */
export function relationLabel(relation: Relationship): string {
  const [kind, detail] = (relation.basis ?? "").split(":", 2)
  if (kind === "duplicate") return t("Misma decisión, en otro sitio:")
  if (kind === "same_function") return t("Junto a ella en {0}:", detail)
  if (kind === "constant") return t("Comparte {0} con:", detail)
  if (kind === "constraint") return t("Misma restricción ({0}) en otro sitio:", detail)
  if (kind === "permission") return t("Exige el mismo permiso «{0}» que:", detail)
  if (kind === "same_data") return t("Sobre la misma tabla ({0}):", detail)
  if (kind === "calls") {
    const hops = detail === "1" ? t("directamente") : t("a través de {0} función(es)", Number(detail) - 1)
    return relation.direction === "out" ? t("Su código llama, {0}, al de:", hops) : t("La usa, {0}:", hops)
  }
  if (relation.relation_type === "depends_on") return relation.direction === "out" ? t("Depende de:") : t("Dependen de ella:")
  if (relation.relation_type === "contradicts") return t("Contradice:")
  return t("Relacionada con:")
}

/** Already said by "Si cambias esta regla": shown there, not again among the relationships. */
export function shownAsImpact(relation: Relationship): boolean {
  const kind = (relation.basis ?? "").split(":", 1)[0]
  return (relation.relation_type === "depends_on" && relation.direction === "in") || kind === "duplicate" || kind === "constraint"
}

