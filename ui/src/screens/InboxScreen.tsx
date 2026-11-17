import { t } from "@/lib/i18n"
import { Check, Inbox as InboxIcon, Merge, PenLine, Undo2, X } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { useNavigate } from "react-router"
import { cn } from "cn"
import { CodeExcerpt } from "@/components/code-excerpt"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { type Inbox, type InboxItem, type VerificationStatus, getInbox, mergeRules, reviewBatch } from "@/lib/api"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Eyebrow, Muted, type ScreenProps } from "@/screens/shared"

const PAGE = 100

const REASON_LABELS: Record<string, string> = {
  rule_changed: t("La regla cambió"),
  code_changed: t("Su código cambió"),
  code_removed: t("Su código ya no está"),
  code_contradicts: t("El código ya no la cumple"),
  possible_duplicate: t("Posible duplicado"),
  contradiction: t("Contradice otra regla"),
  ambiguous: t("Ambigua"),
  source_deleted: t("Se borró su fuente"),
}

/** What was done last, to undo it with `u`. */
type Done = { versionIds: string[]; title: string; previous: VerificationStatus }

/**
 * The review inbox (phase 4.1): one rule at a time, most valuable first, with the code beside
 * it. j/k move, c confirms, r rejects, e opens the rule to rewrite it, u undoes the last
 * decision. Groups of near-identical rules are confirmed, rejected or merged in one action.
 */
export function InboxScreen({ project, onSessionLost }: ScreenProps) {
  const [box, setBox] = useState<Inbox | null>(null)
  const [items, setItems] = useState<InboxItem[]>([])
  const [selected, setSelected] = useState(0)
  const [note, setNote] = useState("")
  const [done, setDone] = useState<Done | null>(null)
  const [reviewed, setReviewed] = useState(0)
  const { error, setError, fail } = useErrors(onSessionLost)
  const navigate = useNavigate()
  const listRef = useRef<HTMLOListElement>(null)

  const load = useCallback(() => {
    getInbox(project.id, 0, PAGE)
      .then((next) => {
        setBox(next)
        setItems(next.items)
        setSelected((current) => Math.min(current, Math.max(next.items.length - 1, 0)))
      })
      .catch(fail)
  }, [project.id, fail])
  useEffect(load, [load])

  const more = () => {
    getInbox(project.id, items.length, PAGE)
      .then((next) => setItems((current) => [...current, ...next.items]))
      .catch(fail)
  }

  const current = items[selected]

  const decide = useCallback(
    (status: VerificationStatus, versionIds: string[], title: string) => {
      const previous = items.find((item) => item.version_id === versionIds[0])?.verification_status ?? "unverified"
      setError(null)
      // Optimistic: out of the list at once; back if the server says no.
      const before = items
      setItems((list) => list.filter((item) => !versionIds.includes(item.version_id)))
      reviewBatch(project.id, versionIds, status, note)
        .then(() => {
          setDone({ versionIds, title, previous })
          setReviewed((count) => count + versionIds.length)
          setNote("")
        })
        .catch((reason: unknown) => {
          setItems(before)
          fail(reason)
        })
    },
    [items, note, project.id, setError, fail],
  )

  const undo = useCallback(() => {
    if (!done) return
    reviewBatch(project.id, done.versionIds, done.previous)
      .then(() => {
        setDone(null)
        setReviewed((count) => Math.max(count - done.versionIds.length, 0))
        load()
      })
      .catch(fail)
  }, [done, project.id, load, fail])

  const merge = (item: InboxItem) => {
    if (!item.group) return
    const others = item.group.members.filter((id) => id !== item.entity_id)
    setError(null)
    Promise.all(others.map((id) => mergeRules(project.id, item.entity_id, id)))
      .then(load)
      .catch(fail)
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      if (event.key === "j") setSelected((index) => Math.min(index + 1, items.length - 1))
      else if (event.key === "k") setSelected((index) => Math.max(index - 1, 0))
      else if (event.key === "c" && current) decide("human_verified", [current.version_id], current.title)
      else if (event.key === "r" && current) decide("rejected", [current.version_id], current.title)
      else if (event.key === "e" && current) navigate(`/reglas/${current.entity_id}`)
      else if (event.key === "u") undo()
      else return
      event.preventDefault()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [items.length, current, decide, undo, navigate])

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${selected}"]`)?.scrollIntoView({ block: "nearest" })
  }, [selected])

  return (
    <>
      <PageHeader
        title={t("Bandeja")}
        description={t("Lo que más vale revisar ahora: reglas sin revisar y reglas cuyo código cambió, por impacto e incertidumbre. j/k para moverte, c confirma, r rechaza, e abre la regla, u deshace.")}
      />
      <ErrorText>{error}</ErrorText>
      {done && (
        <div role="status" className="flex flex-wrap items-center gap-2 text-sm">
          <span>
            {done.versionIds.length > 1 ? t("{0} reglas revisadas", done.versionIds.length) : t("«{0}» revisada", done.title)}.
          </span>
          <Button size="xs" variant="outline" onClick={undo}>
            <Undo2 data-icon="inline-start" />
            {t("Deshacer (u)")}
          </Button>
          {reviewed > 0 && <Muted>{reviewed} {t("en esta sesión.")}</Muted>}
        </div>
      )}
      {box === null ? (
        <Muted>{t("Cargando…")}</Muted>
      ) : items.length === 0 ? (
        <EmptyState icon={InboxIcon} title={t("Nada por revisar")}>
          {t("Todas las reglas están revisadas y ninguna tiene cambios pendientes.")}
        </EmptyState>
      ) : (
        <div className="grid min-w-0 gap-4 *:min-w-0 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
          <ol ref={listRef} className="grid max-h-[70vh] content-start gap-1 overflow-y-auto" aria-label={t("Reglas por revisar")}>
            {items.map((item, index) => (
              <li key={item.version_id} data-index={index}>
                <button
                  type="button"
                  className={cn(
                    "grid w-full gap-0.5 rounded-md border px-3 py-2 text-left text-sm hover:bg-muted/50",
                    index === selected && "border-primary bg-muted/50",
                  )}
                  aria-current={index === selected}
                  onClick={() => setSelected(index)}
                >
                  <span className="font-medium">{item.title}</span>
                  <span className="text-xs text-muted-foreground">
                    {item.review_reason ? REASON_LABELS[item.review_reason] ?? item.review_reason : t("Sin revisar")}
                    {item.group && t(" · grupo de {0}", item.group.size)}
                  </span>
                </button>
              </li>
            ))}
            {items.length < box.total && (
              <li>
                <Button size="sm" variant="ghost" onClick={more}>
                  {t("Ver más (")}{box.total - items.length})
                </Button>
              </li>
            )}
          </ol>
          {current && (
            <Section
              title={current.title}
              description={
                current.review_reason ? REASON_LABELS[current.review_reason] ?? current.review_reason : t("Sin revisar")
              }
            >
              <p className="text-sm">{current.statement}</p>
              {current.now && (
                <div className="grid min-w-0 gap-1">
                  <Eyebrow>{t("Ahora")}</Eyebrow>
                  <CodeExcerpt
                    revisionId={current.now.revision_id}
                    path={current.now.path}
                    startLine={current.now.start_line}
                    endLine={current.now.end_line}
                    className="max-h-80 overflow-y-auto"
                  />
                </div>
              )}
              {current.citation && (
                <div className="grid min-w-0 gap-1">
                  {current.now && <Eyebrow>{t("Lo que se verificó")}</Eyebrow>}
                  <CodeExcerpt
                    revisionId={current.citation.revision_id}
                    path={current.citation.path}
                    startLine={current.citation.start_line}
                    endLine={current.citation.end_line}
                    className="max-h-80 overflow-y-auto"
                  />
                </div>
              )}
              <Input
                aria-label={t("Motivo")}
                maxLength={2000}
                placeholder={t("Motivo (opcional; al rechazar, viaja con la regla al repositorio)")}
                value={note}
                onChange={(event) => setNote(event.target.value)}
              />
              <div className="flex flex-wrap gap-1">
                <Button size="sm" onClick={() => decide("human_verified", [current.version_id], current.title)}>
                  <Check data-icon="inline-start" />
                  {t("Confirmar (c)")}
                </Button>
                <Button
                  size="sm"
                  variant="destructive"
                  onClick={() => decide("rejected", [current.version_id], current.title)}
                >
                  <X data-icon="inline-start" />
                  {t("Rechazar (r)")}
                </Button>
                <Button size="sm" variant="outline" onClick={() => navigate(`/reglas/${current.entity_id}`)}>
                  <PenLine data-icon="inline-start" />
                  {t("Abrir (e)")}
                </Button>
              </div>
              {current.group && <GroupActions item={current} items={items} decide={decide} merge={merge} />}
              <p className="text-xs text-muted-foreground">
                {t("Puntuación")} {current.score}{t(": impacto")} {current.parts.impact} {t("× incertidumbre")} {current.parts.uncertainty} {t("÷ coste")} {current.parts.cost}.
              </p>
            </Section>
          )}
        </div>
      )}
      {box && box.unanchored_total > 0 && (
        <Muted>
          {box.unanchored_total === 1
            ? t("1 propuesta del modelo no entra en la bandeja")
            : t("{0} propuestas del modelo no entran en la bandeja", box.unanchored_total)}
          {t(": su enunciado dice números o nombres que su código no tiene (")}{box.unanchored
            .slice(0, 3)
            .map((rule) => `${rule.title}: ${rule.missing.join(", ")}`)
            .join("; ")}
          {t("). Se pueden ver en Reglas.")}
        </Muted>
      )}
    </>
  )
}

function GroupActions({
  item,
  items,
  decide,
  merge,
}: {
  item: InboxItem
  items: InboxItem[]
  decide: (status: VerificationStatus, versionIds: string[], title: string) => void
  merge: (item: InboxItem) => void
}) {
  const group = item.group
  if (!group) return null
  const pending = items.filter((other) => other.group?.key === group.key).map((other) => other.version_id)
  const label = t("grupo de {0}", group.size)
  return (
    <div className="grid gap-1 border-t pt-3">
      <p className="text-sm">
        {t("Esta regla es casi idéntica a otras")} {group.size - 1}
        {group.confirmed && t("; una ya está confirmada")}.
      </p>
      <div className="flex flex-wrap gap-1">
        {pending.length > 1 && (
          <>
            <Button size="xs" variant="outline" onClick={() => decide("human_verified", pending, label)}>
              {t("Confirmar las")} {pending.length}
            </Button>
            <Button size="xs" variant="outline" onClick={() => decide("rejected", pending, label)}>
              {t("Rechazar las")} {pending.length}
            </Button>
          </>
        )}
        <Button size="xs" variant="outline" onClick={() => merge(item)}>
          <Merge data-icon="inline-start" />
          {t("Fusionar el grupo en esta")}
        </Button>
      </div>
    </div>
  )
}
