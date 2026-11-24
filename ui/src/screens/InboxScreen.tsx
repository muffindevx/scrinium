import { t } from "@/lib/i18n"
import {
  ArrowLeft,
  Check,
  ChevronLeft,
  ChevronRight,
  Ban,
  ExternalLink,
  Inbox as InboxIcon,
  Merge,
  PenLine,
  RefreshCw,
  Undo2,
  X,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useNavigate, useParams, useSearchParams } from "react-router"
import { cn } from "cn"
import { CodeExcerpt } from "@/components/code-excerpt"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { ImpactList, RuleDrawer } from "@/components/rule-impact"
import { RuleLink } from "@/components/rule-link"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Confirm } from "@/components/ui/confirm"
import {
  type Inbox,
  type InboxItem,
  type Process,
  type RuleDetail,
  type VerificationStatus,
  getInbox,
  getProcesses,
  getRule,
  mergeRules,
  reviewBatch,
} from "@/lib/api"
import { RuleHistory } from "@/screens/RuleHistory"
import { RuleTickets } from "@/screens/RuleTickets"
import { relationLabel, shownAsImpact } from "@/lib/relations"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Eyebrow, Muted, type ScreenProps } from "@/screens/shared"

// Rows loaded at a time; the tabs' counts come from the server and are always exact.
const PAGE = 500

const REASON_LABELS: Record<string, string> = {
  rule_changed: t("Su código decide ahora con otros valores"),
  code_changed: t("Su código cambió"),
  code_removed: t("Su código ya no está"),
  code_contradicts: t("El código ya no la cumple"),
  possible_duplicate: t("Puede estar repetida"),
  contradiction: t("Contradice otra regla"),
  ambiguous: t("Es ambigua"),
  source_deleted: t("Se borró su fuente"),
}
// Short, for a row: the long label is in the detail.
const REASON_SHORT: Record<string, string> = {
  rule_changed: t("Valores distintos"),
  code_changed: t("Código cambiado"),
  code_removed: t("Código retirado"),
  code_contradicts: t("No se cumple"),
  possible_duplicate: t("Repetida"),
  contradiction: t("Contradicción"),
  ambiguous: t("Ambigua"),
  source_deleted: t("Sin fuente"),
}
// What was true and stopped being so: a person should look first.
const ATTENTION = new Set(["rule_changed", "code_changed", "code_removed", "code_contradicts", "contradiction", "source_deleted"])

type Bucket = "attention" | "high" | "rest"
const BUCKETS: { key: Bucket; label: string; hint: string; dot: string }[] = [
  { key: "attention", label: t("Necesitan atención"), hint: t("Su código cambió o contradice algo"), dot: "bg-red-500" },
  { key: "high", label: t("Prioridad alta"), hint: t("Importancia alta o con bugs"), dot: "bg-amber-500" },
  { key: "rest", label: t("Resto"), hint: t("Propuestas nuevas"), dot: "bg-sky-500" },
]

function bucketOf(item: InboxItem): Bucket {
  if (item.review_reason && ATTENTION.has(item.review_reason)) return "attention"
  if (item.criticality === "high" || item.criticality === "critical" || (item.parts.bugs ?? 0) > 0) return "high"
  return "rest"
}

/** Why this rule is worth a look, in words: its reason, importance, bugs and look-alikes. */
function reasons(item: InboxItem): { text: string; tone: "attention" | "no_evidence" }[] {
  const found: { text: string; tone: "attention" | "no_evidence" }[] = [
    item.review_reason
      ? { text: REASON_LABELS[item.review_reason] ?? item.review_reason, tone: "attention" }
      : { text: t("Propuesta nueva, nadie la ha revisado"), tone: "no_evidence" },
  ]
  if (item.criticality === "high" || item.criticality === "critical") found.push({ text: t("Importancia alta"), tone: "attention" })
  const bugs = item.parts.bugs ?? 0
  if (bugs > 0) found.push({ text: bugs === 1 ? t("1 bug apunta a ella") : t("{0} bugs apuntan a ella", bugs), tone: "attention" })
  if (item.group) found.push({ text: t("Se parece a {0} reglas más", item.group.size - 1), tone: "no_evidence" })
  return found
}

function fileOf(item: InboxItem) {
  const place = item.now ?? item.citation
  return place ? `${place.path.split("/").pop()}:${place.start_line}` : ""
}

/** Rejecting removes a rule for good, so it asks first, and takes an optional reason. */
function askReject(count: number, title: string) {
  return Confirm.call({
    tone: "danger",
    icon: Ban,
    title: count === 1 ? t("¿Rechazar «{0}»?", title) : t("¿Rechazar {0} reglas?", count),
    description: t("Scrinium no volverá a proponerla. Si exportas las reglas, viaja al repositorio como rechazada, con el motivo."),
    confirmLabel: count === 1 ? t("Sí, rechazar") : t("Sí, rechazar las {0}", count),
    input: { label: t("Motivo (opcional)"), placeholder: t("Por qué no es correcta") },
  })
}

function Key({ children }: { children: string }) {
  return (
    <kbd className="ml-1 hidden rounded border border-current/30 px-1 font-mono text-[10px] leading-4 opacity-70 sm:inline">
      {children}
    </kbd>
  )
}

/** What was done last, to undo it with `u`. */
type Done = { versionIds: string[]; title: string; previous: VerificationStatus }

/**
 * Review (phase 4.1), as a mail inbox: the rules to review by priority, a row each, chosen one
 * by one or many at once; opening one shows everything about it. The model proposes rules by
 * reading the code; a person says whether each is true. Keys as in Gmail: j/k move, x selects,
 * Enter opens, c confirms, r rejects, u undoes (or goes back to the list from a rule).
 */
export function InboxScreen({ project, onSessionLost }: ScreenProps) {
  const { entityId } = useParams()
  const navigate = useNavigate()
  const [box, setBox] = useState<Inbox | null>(null)
  const [items, setItems] = useState<InboxItem[]>([])
  // Every row fetched since the last load, decided or not: to keep the tabs' counts exact.
  const [fetched, setFetched] = useState<InboxItem[]>([])
  const [cursor, setCursor] = useState(0)
  const [checked, setChecked] = useState<Set<string>>(new Set())
  const [done, setDone] = useState<Done | null>(null)
  const { error, setError, fail } = useErrors(onSessionLost)

  const load = useCallback(() => {
    getInbox(project.id, 0, PAGE)
      .then((next) => {
        setBox(next)
        setItems(next.items)
        setFetched(next.items)
      })
      .catch(fail)
  }, [project.id, fail])
  useEffect(load, [load])

  const more = () => {
    getInbox(project.id, items.length, PAGE)
      .then((next) => {
        setItems((current) => [...current, ...next.items])
        setFetched((current) => [...current, ...next.items])
      })
      .catch(fail)
  }

  // Priorities as tabs, as Gmail's Primary, Social and Promotions. The tab lives in the URL;
  // an open rule shows the tab it belongs to.
  const [params, setParams] = useSearchParams()
  const count = (list: InboxItem[]) => {
    const found: Record<Bucket, number> = { attention: 0, high: 0, rest: 0 }
    for (const item of list) found[bucketOf(item)] += 1
    return found
  }
  const loadedCounts = useMemo(() => count(items), [items])
  const fetchedCounts = useMemo(() => count(fetched), [fetched])
  // The server counts every rule; what was decided here since is taken off.
  const counts: Record<Bucket, number> = box?.buckets
    ? {
        attention: box.buckets.attention - (fetchedCounts.attention - loadedCounts.attention),
        high: box.buckets.high - (fetchedCounts.high - loadedCounts.high),
        rest: box.buckets.rest - (fetchedCounts.rest - loadedCounts.rest),
      }
    : loadedCounts
  const asked = params.get("pestana") as Bucket | null
  const openItem = entityId ? items.find((item) => item.entity_id === entityId) : undefined
  const tab: Bucket = openItem
    ? bucketOf(openItem)
    : asked && BUCKETS.some((b) => b.key === asked)
      ? asked
      : (BUCKETS.find((b) => counts[b.key] > 0)?.key ?? "attention")
  const query = `?pestana=${tab}`
  const chooseTab = (next: Bucket) => {
    setParams({ pestana: next }, { replace: true })
    setCursor(0)
    setChecked(new Set())
  }
  // The rows of the tab, by score (the server's order).
  const ordered = useMemo(() => items.filter((item) => bucketOf(item) === tab), [items, tab])
  const open = entityId ? ordered.findIndex((item) => item.entity_id === entityId) : -1

  const decide = useCallback(
    (status: VerificationStatus, versionIds: string[], title: string, note = "") => {
      if (versionIds.length === 0) return
      const previous = items.find((item) => item.version_id === versionIds[0])?.verification_status ?? "unverified"
      setError(null)
      // Optimistic: out of the list at once; back if the server says no.
      const before = items
      setItems((list) => list.filter((item) => !versionIds.includes(item.version_id)))
      setChecked((current) => new Set([...current].filter((id) => !versionIds.includes(id))))
      reviewBatch(project.id, versionIds, status, note)
        .then(() => setDone({ versionIds, title, previous }))
        .catch((reason: unknown) => {
          setItems(before)
          fail(reason)
        })
    },
    [items, project.id, setError, fail],
  )

  const undo = useCallback(() => {
    if (!done) return
    reviewBatch(project.id, done.versionIds, done.previous)
      .then(() => {
        setDone(null)
        load()
      })
      .catch(fail)
  }, [done, project.id, load, fail])

  const openAt = useCallback(
    (index: number) => {
      const item = ordered[index]
      if (item) navigate(`/bandeja/${item.entity_id}${query}`)
    },
    [ordered, navigate, query],
  )

  // After deciding an open rule, the next one opens, as in Gmail's "go to the next message".
  const decideOpen = (status: VerificationStatus, item: InboxItem, note: string) => {
    const next = ordered[open + 1] ?? ordered[open - 1]
    const previous = item.verification_status
    setError(null)
    const before = items
    setItems((list) => list.filter((other) => other.version_id !== item.version_id))
    navigate(next ? `/bandeja/${next.entity_id}${query}` : `/bandeja${query}`, { replace: true })
    reviewBatch(project.id, [item.version_id], status, note)
      .then(() => setDone({ versionIds: [item.version_id], title: item.title, previous }))
      .catch((reason: unknown) => {
        setItems(before)
        fail(reason)
      })
  }

  const toggle = (versionId: string) =>
    setChecked((current) => {
      const next = new Set(current)
      if (next.has(versionId)) next.delete(versionId)
      else next.add(versionId)
      return next
    })
  // With rows checked, the decision is for all of them; otherwise for the row at hand.
  // Rejecting asks first: it is the decision that removes a rule for good.
  const decideMany = async (status: VerificationStatus, item?: InboxItem) => {
    const ids = checked.size > 0 ? [...checked] : item ? [item.version_id] : []
    if (ids.length === 0) return
    const title = ids.length === 1 ? (items.find((i) => i.version_id === ids[0])?.title ?? "") : t("{0} reglas", ids.length)
    if (status !== "rejected") return decide(status, ids, title)
    const answer = await askReject(ids.length, title)
    if (answer.confirmed) decide("rejected", ids, title, answer.value)
  }
  const decideOne = async (status: VerificationStatus, item: InboxItem) => {
    if (status !== "rejected") return decideOpen(status, item, "")
    const answer = await askReject(1, item.title)
    if (answer.confirmed) decideOpen("rejected", item, answer.value)
  }

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return
      if (event.metaKey || event.ctrlKey || event.altKey) return
      // A dialog is open: its keys are its own.
      if (document.querySelector('[role="dialog"]')) return
      const here = entityId ? ordered[open] : ordered[cursor]
      // Letters with Shift or Caps Lock count too: the help shows them as C, R, J…
      const key = event.key.length === 1 ? event.key.toLowerCase() : event.key
      if (key === "j" && entityId) openAt(open + 1)
      else if (key === "j") setCursor((i) => Math.min(i + 1, ordered.length - 1))
      else if (key === "k" && entityId) openAt(Math.max(open - 1, 0))
      else if (key === "k") setCursor((i) => Math.max(i - 1, 0))
      else if (key === "x" && !entityId && here) toggle(here.version_id)
      else if ((key === "Enter" || key === "o") && !entityId) openAt(cursor)
      else if ((key === "c" || key === "r") && here) {
        const status = key === "c" ? "human_verified" : "rejected"
        if (entityId) void decideOne(status, here)
        else void decideMany(status, here)
      } else if (key === "u" && entityId) navigate(`/bandeja${query}`)
      else if (key === "u") undo()
      else return
      event.preventDefault()
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  })

  if (entityId && box !== null && open >= 0) {
    return (
      <RuleReview
        key={ordered[open].version_id}
        project={project}
        item={ordered[open]}
        position={open}
        total={ordered.length}
        onBack={() => navigate(`/bandeja${query}`)}
        onMove={openAt}
        hasNext={open < ordered.length - 1}
        onDecide={(status, item) => void decideOne(status, item)}
        onMerge={(item) => {
          const others = item.group?.members.filter((id) => id !== item.entity_id) ?? []
          Promise.all(others.map((id) => mergeRules(project.id, item.entity_id, id)))
            .then(load)
            .catch(fail)
        }}
        onDecideGroup={decide}
        items={items}
        error={error}
        onSessionLost={onSessionLost}
      />
    )
  }

  return (
    <>
      <PageHeader
        title={t("Revisar reglas")}
        description={t("Scrinium propone reglas leyendo el código; tú dices si son ciertas. Lo que confirmas queda como regla verificada, y el check avisa si un cambio la rompe. Lo que rechazas no vuelve.")}
      />
      <ErrorText>{error}</ErrorText>
      {box === null ? (
        <Muted>{t("Cargando…")}</Muted>
      ) : items.length === 0 ? (
        <EmptyState icon={InboxIcon} title={t("Nada por revisar")}>
          {t("Todas las reglas están revisadas y ninguna tiene cambios pendientes.")}
        </EmptyState>
      ) : (
        <Section className="gap-0 overflow-hidden p-0">
          <Tabs tab={tab} counts={counts} onChoose={chooseTab} />
          <Toolbar
            shown={ordered.length}
            checked={checked.size}
            allChecked={checked.size > 0 && checked.size === ordered.length}
            onCheckAll={() => setChecked(checked.size === ordered.length ? new Set() : new Set(ordered.map((i) => i.version_id)))}
            onConfirm={() => void decideMany("human_verified")}
            onReject={() => void decideMany("rejected")}
            onRefresh={load}
            done={done}
            onUndo={undo}
          />
          {ordered.length === 0 ? (
            <p className="px-4 py-10 text-center text-sm text-muted-foreground">{t("Nada en esta pestaña.")}</p>
          ) : (
            <ul>
              {ordered.map((item, index) => (
                <Row
                  key={item.version_id}
                  item={item}
                  dot={BUCKETS.find((b) => b.key === tab)?.dot ?? ""}
                  active={index === cursor}
                  checked={checked.has(item.version_id)}
                  onToggle={() => toggle(item.version_id)}
                  onOpen={() => navigate(`/bandeja/${item.entity_id}${query}`)}
                  onDecide={(status) =>
                    status === "rejected"
                      ? void askReject(1, item.title).then((answer) => {
                          if (answer.confirmed) decide("rejected", [item.version_id], item.title, answer.value)
                        })
                      : decide(status, [item.version_id], item.title)
                  }
                />
              ))}
            </ul>
          )}
          {ordered.length < counts[tab] && items.length < box.total && (
            <div className="border-t px-4 py-2">
              <Button size="sm" variant="ghost" onClick={more}>
                {t("Cargar {0} más", Math.min(PAGE, box.total - items.length))}
              </Button>
            </div>
          )}
        </Section>
      )}
      {box && box.unanchored_total > 0 && (
        <Muted>
          {box.unanchored_total === 1
            ? t("1 propuesta del modelo no entra en la bandeja")
            : t("{0} propuestas del modelo no entran en la bandeja", box.unanchored_total)}
          {t(": su enunciado dice números o nombres que su código no tiene. Se pueden ver en Reglas.")}
        </Muted>
      )}

    </>
  )
}

function Toolbar({
  shown,
  checked,
  allChecked,
  onCheckAll,
  onConfirm,
  onReject,
  onRefresh,
  done,
  onUndo,
}: {
  shown: number
  checked: number
  allChecked: boolean
  onCheckAll: () => void
  onConfirm: () => void
  onReject: () => void
  onRefresh: () => void
  done: Done | null
  onUndo: () => void
}) {
  return (
    <div className="flex min-h-12 flex-wrap items-center gap-2 border-b px-4 py-2 text-sm">
      <input
        type="checkbox"
        className="size-4 accent-primary"
        aria-label={t("Seleccionar todas")}
        checked={allChecked}
        ref={(input) => {
          if (input) input.indeterminate = checked > 0 && !allChecked
        }}
        onChange={onCheckAll}
      />
      {checked > 0 ? (
        <>
          <span className="font-medium">{checked === 1 ? t("1 seleccionada") : t("{0} seleccionadas", checked)}</span>
          <Button size="sm" className="bg-emerald-600 text-white hover:bg-emerald-700" onClick={onConfirm}>
            <Check data-icon="inline-start" />
            {t("Confirmar")}
          </Button>
          <Button size="sm" variant="destructive" onClick={onReject}>
            <X data-icon="inline-start" />
            {t("Rechazar")}
          </Button>
        </>
      ) : (
        <Button size="icon-sm" variant="ghost" aria-label={t("Actualizar")} title={t("Actualizar")} onClick={onRefresh}>
          <RefreshCw />
        </Button>
      )}
      {done && (
        <span role="status" className="flex items-center gap-2 text-xs text-muted-foreground">
          {done.versionIds.length > 1 ? t("{0} reglas revisadas", done.versionIds.length) : t("«{0}» revisada", done.title)}
          <Button size="xs" variant="ghost" onClick={onUndo}>
            <Undo2 data-icon="inline-start" />
            {t("Deshacer")}
          </Button>
        </span>
      )}
      <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground tabular-nums">
        {shown === 1 ? t("1 regla") : t("{0} reglas", shown)}
      </span>
    </div>
  )
}

/** The priorities as tabs, as Gmail's: a coloured mark, a name, how many, and a hint. */
function Tabs({ tab, counts, onChoose }: { tab: Bucket; counts: Record<Bucket, number>; onChoose: (tab: Bucket) => void }) {
  return (
    <div role="tablist" aria-label={t("Prioridad")} className="grid grid-cols-3 border-b">
      {BUCKETS.map((bucket) => {
        const active = bucket.key === tab
        return (
          <button
            key={bucket.key}
            type="button"
            role="tab"
            aria-selected={active}
            onClick={() => onChoose(bucket.key)}
            className={cn(
              "relative grid min-w-0 gap-0.5 px-4 py-3 text-left hover:bg-muted/50",
              active && "after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:bg-primary",
            )}
          >
            <span className={cn("flex min-w-0 items-center gap-2 text-sm", active ? "font-medium text-foreground" : "text-muted-foreground")}>
              <span className={cn("size-2 shrink-0 rounded-full", bucket.dot)} aria-hidden="true" />
              <span className="truncate">{bucket.label}</span>
              {counts[bucket.key] > 0 && (
                <span className={cn("rounded-full px-1.5 text-xs tabular-nums", active ? "bg-primary text-primary-foreground" : "bg-muted")}>
                  {counts[bucket.key]}
                </span>
              )}
            </span>
            <span className="hidden truncate text-xs text-muted-foreground md:block">{bucket.hint}</span>
          </button>
        )
      })}
    </div>
  )
}

/** One rule, one line, like a mail: title, the start of its statement, why, and where. */
function Row({
  item,
  dot,
  active,
  checked,
  onToggle,
  onOpen,
  onDecide,
}: {
  item: InboxItem
  dot: string
  active: boolean
  checked: boolean
  onToggle: () => void
  onOpen: () => void
  onDecide: (status: VerificationStatus) => void
}) {
  const bugs = item.parts.bugs ?? 0
  return (
    <li
      className={cn(
        "group relative flex items-center gap-3 border-b px-4 py-2 text-sm last:border-b-0 hover:bg-muted/50",
        checked && "bg-primary/5",
        active && "before:absolute before:inset-y-0 before:left-0 before:w-0.5 before:bg-primary",
      )}
    >
      <input
        type="checkbox"
        className="size-4 shrink-0 accent-primary"
        aria-label={t("Seleccionar «{0}»", item.title)}
        checked={checked}
        onChange={onToggle}
      />
      <span className={cn("size-2 shrink-0 rounded-full", dot)} aria-hidden="true" />
      <button type="button" onClick={onOpen} className="grid min-w-0 flex-1 gap-0.5 text-left sm:flex sm:items-baseline sm:gap-2">
        <span className="shrink-0 truncate font-medium sm:max-w-[40%]">{item.title}</span>
        <span className="min-w-0 truncate text-muted-foreground">
          <span className="hidden sm:inline">— </span>
          {item.statement}
        </span>
      </button>
      {/* Meta and actions share one slot of fixed width and swap by opacity: nothing moves on
          hover. The actions also show on keyboard focus. */}
      <span className="relative hidden h-8 w-64 shrink-0 md:block">
        <span className="absolute inset-0 flex items-center justify-end gap-2 text-xs text-muted-foreground transition-opacity group-focus-within:opacity-0 group-hover:opacity-0">
          {item.review_reason && <span className="truncate rounded bg-muted px-1.5 py-0.5">{REASON_SHORT[item.review_reason] ?? item.review_reason}</span>}
          {bugs > 0 && <span className="shrink-0 rounded bg-amber-500/15 px-1.5 py-0.5 text-amber-700 dark:text-amber-400">{bugs === 1 ? t("1 bug") : t("{0} bugs", bugs)}</span>}
          {item.group && <span className="shrink-0">{t("×{0}", item.group.size)}</span>}
          <span className="min-w-0 truncate text-right font-mono">{fileOf(item)}</span>
        </span>
        <span className="absolute inset-0 flex items-center justify-end gap-1.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
          <button
            type="button"
            title={t("Es correcta · C")}
            onClick={() => onDecide("human_verified")}
            className="flex h-7 items-center gap-1 rounded-full bg-emerald-500/15 px-2.5 text-xs font-medium text-emerald-700 hover:bg-emerald-500/25 focus-visible:ring-2 focus-visible:ring-emerald-500/50 focus-visible:outline-none dark:text-emerald-400"
          >
            <Check className="size-3.5" aria-hidden="true" />
            {t("Correcta")}
          </button>
          <button
            type="button"
            title={t("No es correcta · R")}
            onClick={() => onDecide("rejected")}
            className="flex h-7 items-center gap-1 rounded-full bg-red-500/15 px-2.5 text-xs font-medium text-red-700 hover:bg-red-500/25 focus-visible:ring-2 focus-visible:ring-red-500/50 focus-visible:outline-none dark:text-red-400"
          >
            <X className="size-3.5" aria-hidden="true" />
            {t("Rechazar")}
          </button>
        </span>
      </span>
    </li>
  )
}

/** One rule opened: what it says, the code it comes from, and everything tied to it. */
function RuleReview({
  project,
  item,
  position,
  total,
  onBack,
  onMove,
  hasNext,
  onDecide,
  onMerge,
  onDecideGroup,
  items,
  error,
  onSessionLost,
}: {
  project: ScreenProps["project"]
  item: InboxItem
  position: number
  total: number
  onBack: () => void
  onMove: (index: number) => void
  hasNext: boolean
  onDecide: (status: VerificationStatus, item: InboxItem) => void
  onMerge: (item: InboxItem) => void
  onDecideGroup: (status: VerificationStatus, versionIds: string[], title: string, note?: string) => void
  items: InboxItem[]
  error: string | null
  onSessionLost: (error: unknown) => void
}) {
  const navigate = useNavigate()
  // A new rule (the component is keyed by it) starts at the top. The page scrolls in <main>.
  useEffect(() => {
    document.querySelector("main")?.scrollTo({ top: 0 })
  }, [])
  return (
    <>
      {/* Pinned while the page scrolls: the decision is always one click away. */}
      <div className="sticky -top-6 z-20 -mx-6 -mt-6 flex flex-wrap items-center gap-1 border-b bg-background/95 px-6 py-2 backdrop-blur max-sm:-mx-4 max-sm:px-4">
        <Button size="sm" variant="ghost" onClick={onBack}>
          <ArrowLeft data-icon="inline-start" />
          {t("Bandeja")}
        </Button>
        <span className="mx-1 h-5 w-px bg-border" aria-hidden="true" />
        <Button
          size="sm"
          className="bg-emerald-600 text-white hover:bg-emerald-700"
          title={t("Es correcta · C")}
          onClick={() => onDecide("human_verified", item)}
        >
          <Check data-icon="inline-start" />
          {t("Es correcta")}
          <Key>C</Key>
        </Button>
        <Button size="sm" variant="destructive" title={t("No es correcta · R")} onClick={() => onDecide("rejected", item)}>
          <X data-icon="inline-start" />
          {t("No es correcta")}
          <Key>R</Key>
        </Button>
        <Button size="sm" variant="ghost" onClick={() => navigate(`/reglas/${item.entity_id}`)}>
          <PenLine data-icon="inline-start" />
          {t("Corregir el texto")}
        </Button>
        <span className="ml-auto flex items-center gap-1 text-xs text-muted-foreground tabular-nums">
          {t("{0} de {1}", position + 1, total)}
          <Button size="icon-sm" variant="ghost" aria-label={t("Anterior")} disabled={position === 0} onClick={() => onMove(position - 1)}>
            <ChevronLeft />
          </Button>
          <Button size="icon-sm" variant="ghost" aria-label={t("Siguiente")} disabled={!hasNext} onClick={() => onMove(position + 1)}>
            <ChevronRight />
          </Button>
        </span>
      </div>
      <ErrorText>{error}</ErrorText>
      <div className="grid min-w-0 gap-4 *:min-w-0 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <Section className="content-start gap-5">
          <div className="flex flex-wrap gap-1.5">
            {reasons(item).map((reason) => (
              <StatusBadge key={reason.text} tone={reason.tone}>
                {reason.text}
              </StatusBadge>
            ))}
          </div>
          <div className="grid gap-1">
            <Eyebrow>{item.title}</Eyebrow>
            <p className="font-heading text-xl leading-snug font-medium text-balance">{item.statement}</p>
          </div>
          <Code item={item} />
          {item.group && <GroupActions item={item} items={items} decide={onDecideGroup} merge={onMerge} />}
        </Section>
        <Related project={project} item={item} onSessionLost={onSessionLost} />
      </div>
    </>
  )
}

/** The code that backs the rule; when it drifted, the verified code and today's. */
function Code({ item }: { item: InboxItem }) {
  if (item.now && item.citation) {
    return (
      <div className="grid min-w-0 gap-3">
        <div className="grid min-w-0 gap-1">
          <Eyebrow>{t("Cuando se verificó")}</Eyebrow>
          <CodeExcerpt revisionId={item.citation.revision_id} path={item.citation.path} startLine={item.citation.start_line} endLine={item.citation.end_line} />
        </div>
        <div className="grid min-w-0 gap-1">
          <Eyebrow>{t("Ahora")}</Eyebrow>
          <CodeExcerpt revisionId={item.now.revision_id} path={item.now.path} startLine={item.now.start_line} endLine={item.now.end_line} />
        </div>
      </div>
    )
  }
  const place = item.now ?? item.citation
  if (!place) return <Muted>{t("Esta regla no cita código.")}</Muted>
  return (
    <div className="grid min-w-0 gap-1">
      <Eyebrow>{t("El código del que sale")}</Eyebrow>
      <CodeExcerpt revisionId={place.revision_id} path={place.path} startLine={place.start_line} endLine={place.end_line} />
    </div>
  )
}

// Processes are the same for every rule of a project: read once per project.
const processCache = new Map<string, Promise<Process[]>>()

/** Everything tied to the rule, as evidence for the decision: tickets, processes, rules that
 * depend on it or relate to it, and its history. */
function Related({
  project,
  item,
  onSessionLost,
}: {
  project: ScreenProps["project"]
  item: InboxItem
  onSessionLost: (error: unknown) => void
}) {
  const [detail, setDetail] = useState<{ id: string; rule: RuleDetail } | null>(null)
  const [processes, setProcesses] = useState<Process[] | null>(null)
  const { fail } = useErrors(onSessionLost)
  useEffect(() => {
    getRule(project.id, item.entity_id)
      .then((rule) => setDetail({ id: item.entity_id, rule }))
      .catch(fail)
  }, [project.id, item.entity_id, fail])
  useEffect(() => {
    if (!processCache.has(project.id)) processCache.set(project.id, getProcesses(project.id))
    processCache.get(project.id)?.then(setProcesses).catch(fail)
  }, [project.id, fail])
  const rule = detail?.id === item.entity_id ? detail.rule : null
  const inProcesses = (processes ?? []).filter((process) =>
    process.steps.some((step) => step.rules.some((r) => r.id === item.entity_id)),
  )
  const [opened, setOpened] = useState<string | null>(null)
  // What "Si cambias esta regla" already says is not repeated here, nor the same rule twice.
  const named = new Set([...(rule?.impact.same_decision ?? []), ...(rule?.impact.dependents ?? [])].map((other) => other.id))
  const related = (rule?.relationships ?? []).filter(
    (relation, index, all) =>
      relation.other.entity_type === "business_rule" &&
      !shownAsImpact(relation) &&
      !named.has(relation.other.id) &&
      all.findIndex((other) => other.other.id === relation.other.id) === index,
  )
  return (
    <div className="grid content-start gap-4">
      <RuleTickets projectId={project.id} entityId={item.entity_id} onSessionLost={onSessionLost} />
      <Section title={t("Dónde se aplica")} description={t("Los procesos que pasan por esta regla.")}>
        {processes === null ? (
          <Muted>{t("Cargando…")}</Muted>
        ) : inProcesses.length === 0 ? (
          <Muted>{t("Ningún proceso detectado pasa por ella.")}</Muted>
        ) : (
          <ul className="grid gap-1 text-sm">
            {inProcesses.map((process) => (
              <li key={`${process.path}:${process.start_line}:${process.name}`} className={cn(process.trigger?.kind === "http" && "font-mono text-xs")}>
                {process.name}
              </li>
            ))}
          </ul>
        )}
      </Section>
      <Section
        title={t("Si cambias esta regla")}
        description={t("Lo que el código dice que depende de ella, sin IA. Pulsa una para verla aquí al lado, sin salir de la bandeja.")}
      >
        {rule === null ? <Muted>{t("Cargando…")}</Muted> : <ImpactList impact={rule.impact} onOpen={setOpened} />}
      </Section>
      {related.length > 0 && (
        <Section title={t("Otras relaciones")} description={t("Lo que el código dice de cómo se relaciona con otras reglas.")}>
          <ul className="grid gap-1 text-sm">
            {related.slice(0, 8).map((relation) => (
              <li key={relation.id} className="flex flex-wrap items-baseline gap-x-2">
                <span className="text-muted-foreground">{relationLabel(relation)}</span>
                <button type="button" className="font-medium hover:underline" onClick={() => setOpened(relation.other.id)}>
                  {relation.other.name}
                </button>
              </li>
            ))}
          </ul>
        </Section>
      )}
      <RuleDrawer projectId={project.id} entityId={opened} onClose={() => setOpened(null)} />
      <RuleHistory projectId={project.id} entityId={item.entity_id} onSessionLost={onSessionLost} />
      <RuleLink id={item.entity_id} className="flex w-fit items-center gap-1 text-sm text-muted-foreground hover:text-foreground">
        <ExternalLink className="size-3.5" aria-hidden="true" />
        {t("Abrir la ficha completa en Reglas")}
      </RuleLink>
    </div>
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
  decide: (status: VerificationStatus, versionIds: string[], title: string, note?: string) => void
  merge: (item: InboxItem) => void
}) {
  const group = item.group
  if (!group) return null
  const pending = items.filter((other) => other.group?.key === group.key).map((other) => other.version_id)
  const label = t("grupo de {0}", group.size)
  return (
    <div className="grid gap-2 rounded-lg bg-muted/40 p-3">
      <p className="text-sm">
        {t("Hay {0} reglas casi iguales a esta", group.size - 1)}
        {group.confirmed && t("; una ya está confirmada")}. {t("Puedes decidir todas a la vez.")}
      </p>
      <div className="flex flex-wrap gap-1">
        {pending.length > 1 && (
          <>
            <Button size="xs" variant="outline" onClick={() => decide("human_verified", pending, label)}>
              {t("Confirmar las")} {pending.length}
            </Button>
            <Button
              size="xs"
              variant="outline"
              onClick={() =>
                void askReject(pending.length, label).then((answer) => {
                  if (answer.confirmed) decide("rejected", pending, label, answer.value)
                })
              }
            >
              {t("Rechazar las")} {pending.length}
            </Button>
          </>
        )}
        <Button size="xs" variant="outline" onClick={() => merge(item)}>
          <Merge data-icon="inline-start" />
          {t("Dejar solo esta (fusionar)")}
        </Button>
      </div>
    </div>
  )
}
