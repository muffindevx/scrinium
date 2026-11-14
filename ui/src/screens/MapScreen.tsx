import { ArrowRight, Map as MapIcon, Network, Workflow, X } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { cn } from "cn"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { StatusBadge, VerificationStatusBadge } from "@/components/status"
import { type Tone } from "@/lib/status"
import { Button } from "@/components/ui/button"
import {
  type FlowDetail,
  type FlowSummary,
  type GraphEdge,
  type GraphNode,
  type KnowledgeGraph,
  type NodeState,
  getFlow,
  getGraph,
  listFlows,
  relateRules,
} from "@/lib/api"
import { connectedGroups, edgePath, layoutLayers } from "@/lib/graph"
import { ENTITY_TYPE_LABELS, NODE_STATE_LABELS } from "@/lib/labels"
import { useErrors } from "@/screens/hooks"
import { RuleEvidence } from "@/screens/RulesScreen"
import { ErrorText, Eyebrow, Muted, type ScreenProps } from "@/screens/shared"

type EdgeKind = "depends" | "same" | "near" | "person"

/** What an edge means for the reader, from its basis: the code relates them, or a person did. */
function edgeKind(edge: GraphEdge): EdgeKind {
  if (edge.basis === null) return edge.relation_type === "depends_on" ? "depends" : "person"
  const kind = edge.basis.split(":", 1)[0]
  if (kind === "calls" || edge.relation_type === "depends_on") return "depends"
  if (kind === "duplicate" || kind === "constraint") return "same"
  return "near"
}

// Stroke by meaning; colour is never the only cue (dash, width and arrow differ too).
const EDGE_STYLE: Record<EdgeKind, { dash?: string; width: number; className: string; arrow: boolean; label: string }> = {
  depends: { width: 1.5, className: "stroke-foreground/60", arrow: true, label: "Su código llama al de la otra" },
  same: { width: 3, className: "stroke-sky-500", arrow: false, label: "La misma decisión en otro sitio" },
  near: { dash: "5 4", width: 1.25, className: "stroke-muted-foreground", arrow: false, label: "Misma función, permiso o tabla" },
  person: { width: 2.5, className: "stroke-emerald-500", arrow: false, label: "Relacionadas por una persona" },
}

function edgeTitle(edge: GraphEdge, from?: string, to?: string) {
  const [kind, detail] = (edge.basis ?? "").split(":", 2)
  if (kind === "calls") return `${from} llama a ${to}${detail === "1" ? "" : ` (a través de ${Number(detail) - 1} función(es))`}`
  if (kind === "duplicate") return `${from} y ${to}: la misma decisión`
  if (kind === "constraint") return `${from} y ${to}: misma restricción ${detail}`
  if (kind === "same_function") return `${from} y ${to}: juntas en ${detail}`
  if (kind === "permission") return `${from} y ${to}: mismo permiso ${detail}`
  if (kind === "same_data") return `${from} y ${to}: misma tabla ${detail}`
  return `${from} → ${to}`
}

/** The relationship between two rules, read from the first one shown to the second. */
function pairReason(edge: GraphEdge, forward: boolean) {
  const [kind, detail] = (edge.basis ?? "").split(":", 2)
  if (kind === "calls") return forward ? "llama al código de" : "la usa"
  if (kind === "duplicate") return "es la misma decisión que"
  if (kind === "constraint") return `misma restricción (${detail}) que`
  if (kind === "same_function") return `junto a ella en ${detail}:`
  if (kind === "permission") return `mismo permiso (${detail}) que`
  if (kind === "same_data") return `misma tabla (${detail}) que`
  if (edge.relation_type === "depends_on") return forward ? "depende de" : "de ella depende"
  return "relacionada con"
}

const NODE_STYLE: Record<NodeState, string> = {
  confirmed: "fill-emerald-500/10 stroke-emerald-500",
  supported: "fill-emerald-500/5 stroke-emerald-500/60",
  inferred: "fill-background stroke-muted-foreground",
  unverified: "fill-amber-500/10 stroke-amber-500",
  conflicted: "fill-orange-500/10 stroke-orange-500",
  rejected: "fill-rose-500/10 stroke-rose-500",
  no_version: "fill-background stroke-border",
}
const NODE_TONE: Record<NodeState, Tone> = {
  confirmed: "verified",
  supported: "verified",
  inferred: "no_evidence",
  unverified: "proposed",
  conflicted: "attention",
  rejected: "discarded",
  no_version: "no_evidence",
}

/** One group of related rules, laid out so that a chain of calls reads from left to right. */
function GroupView({
  nodes,
  edges,
  selected,
  onSelect,
}: {
  nodes: GraphNode[]
  edges: GraphEdge[]
  selected: string | null
  onSelect: (node: GraphNode) => void
}) {
  const layout = useMemo(() => layoutLayers(nodes, edges, { nodeWidth: 184, nodeHeight: 30, gapX: 48, gapY: 10 }), [nodes, edges])
  const names = new Map(nodes.map((node) => [node.id, node.name]))
  return (
    <div className="overflow-auto rounded-lg border bg-muted/30">
      <svg
        role="img"
        aria-label={`Grupo de ${nodes.length} reglas relacionadas`}
        width={layout.width}
        height={layout.height}
        className="text-xs"
      >
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
          </marker>
        </defs>
        {edges.map((edge) => {
          const path = edgePath(edge, layout)
          if (!path) return null
          const style = EDGE_STYLE[edgeKind(edge)]
          return (
            <path
              key={edge.id}
              d={path}
              fill="none"
              strokeWidth={style.width}
              strokeDasharray={style.dash}
              className={style.className}
              markerEnd={style.arrow ? "url(#arrow)" : undefined}
            >
              <title>{edgeTitle(edge, names.get(edge.from), names.get(edge.to))}</title>
            </path>
          )
        })}
        {nodes.map((node) => {
          const position = layout.positions.get(node.id)
          if (!position) return null
          const label = node.name.length > 27 ? `${node.name.slice(0, 26)}…` : node.name
          return (
            <g
              key={node.id}
              role="button"
              tabIndex={0}
              aria-pressed={selected === node.id}
              aria-label={`${node.name}, ${NODE_STATE_LABELS[node.state]}`}
              className="cursor-pointer outline-none [&:focus-visible_rect]:stroke-ring [&:focus-visible_rect]:stroke-[3]"
              onClick={() => onSelect(node)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault()
                  onSelect(node)
                }
              }}
            >
              <rect
                x={position.x}
                y={position.y}
                width={layout.nodeWidth}
                height={layout.nodeHeight}
                rx={8}
                strokeWidth={selected === node.id ? 2.5 : 1.25}
                strokeDasharray={node.state === "inferred" ? "4 3" : undefined}
                className={NODE_STYLE[node.state]}
              />
              <text x={position.x + 10} y={position.y + 19} className="fill-foreground font-medium">
                {label}
              </text>
              <title>
                {node.name} · {node.entity_type === "business_rule"
                  ? NODE_STATE_LABELS[node.state]
                  : (ENTITY_TYPE_LABELS[node.entity_type] ?? node.entity_type)}
              </title>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

/**
 * A group's title: the function, table or permission all its rules share, if they share one;
 * otherwise the most related rule, which is usually what the group is about.
 */
function groupTitle(nodes: GraphNode[], edges: GraphEdge[]) {
  const bases = new Set(edges.map((edge) => edge.basis ?? ""))
  if (bases.size === 1) {
    const [kind, detail] = [...bases][0].split(":", 2)
    if (kind === "same_function") return `En la función ${detail}`
    if (kind === "same_data") return `Sobre la tabla ${detail}`
    if (kind === "permission") return `Con el permiso ${detail}`
  }
  const degree = new Map<string, number>()
  for (const edge of edges) {
    degree.set(edge.from, (degree.get(edge.from) ?? 0) + 1)
    degree.set(edge.to, (degree.get(edge.to) ?? 0) + 1)
  }
  const top = [...nodes].sort((a, b) => (degree.get(b.id) ?? 0) - (degree.get(a.id) ?? 0) || a.name.localeCompare(b.name, "es"))[0]
  return `Alrededor de «${top?.name ?? ""}»`
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      <span className="flex items-center gap-1.5">
        <svg width="20" height="12" aria-hidden="true">
          <rect x="1" y="1" width="18" height="10" rx="3" strokeDasharray="3 2" className="fill-background stroke-muted-foreground" />
        </svg>
        Propuesta por la IA, sin verificar
      </span>
      <span className="flex items-center gap-1.5">
        <svg width="20" height="12" aria-hidden="true">
          <rect x="1" y="1" width="18" height="10" rx="3" className="fill-emerald-500/10 stroke-emerald-500" />
        </svg>
        Verificada
      </span>
      {(Object.keys(EDGE_STYLE) as EdgeKind[]).map((kind) => (
        <span key={kind} className="flex items-center gap-1.5">
          <svg width="28" height="8" aria-hidden="true">
            <line
              x1="0"
              y1="4"
              x2={EDGE_STYLE[kind].arrow ? 24 : 28}
              y2="4"
              strokeWidth={EDGE_STYLE[kind].width}
              strokeDasharray={EDGE_STYLE[kind].dash}
              className={EDGE_STYLE[kind].className}
            />
            {EDGE_STYLE[kind].arrow && <path d="M 23 1 L 28 4 L 23 7 z" className="fill-muted-foreground" />}
          </svg>
          {EDGE_STYLE[kind].label}
        </span>
      ))}
    </div>
  )
}

// Groups this small are many and say little alone: they start folded into one list.
const SMALL_GROUP = 2

function FlowPanel({ project, entityId, onSessionLost }: ScreenProps & { entityId: string }) {
  const [flow, setFlow] = useState<FlowDetail | null>(null)
  const { error, fail } = useErrors(onSessionLost)
  useEffect(() => {
    getFlow(project.id, entityId).then(setFlow).catch(fail)
  }, [project.id, entityId, fail])
  if (error) return <ErrorText>{error}</ErrorText>
  if (!flow) return <Muted>Cargando…</Muted>
  const version = flow.versions[0]
  return (
    <div className="grid gap-3 text-sm">
      <div className="grid gap-1.5">
        <p className="font-medium">{flow.name}</p>
        {version && <VerificationStatusBadge status={version.verification_status} />}
        {flow.lifecycle_status === "proposed" && (
          <Muted>Propuesto: puede tener pasos deducidos sin evidencia.</Muted>
        )}
      </div>
      {version?.description && <p>{version.description}</p>}
      {version && (
        <ol className="grid gap-2">
          {version.steps.map((step, index) => (
            <li key={step.key} className="grid gap-0.5 rounded-lg border p-2">
              <span className="flex items-center gap-2">
                <span className="grid size-5 place-items-center rounded-full bg-muted text-xs">{index + 1}</span>
                <span className="font-medium">{step.label}</span>
              </span>
              {version.transitions
                .filter((transition) => transition.from === step.key)
                .map((transition) => (
                  <span key={`${transition.to}-${transition.transition_type}`} className="pl-7 text-xs text-muted-foreground">
                    → {transition.to}
                    {transition.condition && ` si ${transition.condition}`}
                    {!transition.evidence_id && " · sin evidencia"}
                  </span>
                ))}
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}

function NodePanel({ node, ...props }: ScreenProps & { node: GraphNode }) {
  return (
    <div className="grid gap-3 text-sm">
      <div className="grid gap-1.5">
        <p className="font-medium">{node.name}</p>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge tone={NODE_TONE[node.state]}>{NODE_STATE_LABELS[node.state]}</StatusBadge>
          <span className="text-xs text-muted-foreground">
            {ENTITY_TYPE_LABELS[node.entity_type] ?? node.entity_type}
          </span>
        </div>
      </div>
      {node.entity_type === "business_rule" ? (
        <>
          <Eyebrow>Evidencia</Eyebrow>
          <RuleEvidence {...props} entityId={node.id} />
          <Link to={`/reglas/${node.id}`} className="inline-flex items-center gap-1 font-medium hover:underline">
            Abrir la regla <ArrowRight className="size-3.5" />
          </Link>
        </>
      ) : (
        <Muted>Las citas de este elemento están en las reglas y procesos que lo relacionan.</Muted>
      )}
    </div>
  )
}

const plural = (count: number, one: string, many: string) => `${count} ${count === 1 ? one : many}`

export function MapScreen(props: ScreenProps) {
  const { project, onSessionLost } = props
  const { entityId } = useParams()
  const navigate = useNavigate()
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null)
  const [flows, setFlows] = useState<FlowSummary[]>([])
  const [node, setNode] = useState<GraphNode | null>(null)
  const [relating, setRelating] = useState(false)
  const { error, fail } = useErrors(onSessionLost)
  const load = useCallback(() => {
    Promise.all([getGraph(project.id), listFlows(project.id)])
      .then(([loadedGraph, loadedFlows]) => {
        setGraph(loadedGraph)
        setFlows(loadedFlows)
      })
      .catch(fail)
  }, [project.id, fail])
  useEffect(load, [load])
  const relate = () => {
    setRelating(true)
    relateRules(project.id)
      .then(load)
      .catch(fail)
      .finally(() => setRelating(false))
  }
  const groups = useMemo(() => (graph ? connectedGroups(graph.nodes, graph.edges) : []), [graph])
  const large = groups.filter((group) => group.nodes.length > SMALL_GROUP)
  const small = groups.filter((group) => group.nodes.length <= SMALL_GROUP)

  const selectNode = (selected: GraphNode) => {
    setNode(selected)
    if (entityId) navigate("/mapa")
  }
  const close = () => {
    setNode(null)
    if (entityId) navigate("/mapa")
  }
  const panelOpen = node !== null || Boolean(entityId)
  const panel = useRef<HTMLElement>(null)
  // In narrow windows the panel goes below the map: it is scrolled into view when choosing something.
  useEffect(() => {
    if (node || entityId) panel.current?.scrollIntoView({ behavior: "smooth", block: "nearest" })
  }, [node, entityId])

  return (
    <>
      <PageHeader
        title="Mapa del sistema"
        description="Qué reglas se relacionan entre sí según el código, en grupos: a la izquierda la que llama, a la derecha la llamada. Sale del análisis del código, sin IA."
      />
      <ErrorText>{error}</ErrorText>
      <div className={cn("grid gap-4", panelOpen && "xl:grid-cols-[1fr_20rem]")}>
        <div className="grid min-w-0 content-start gap-4">
          {graph === null ? (
            <Section>
              <Muted>Cargando…</Muted>
            </Section>
          ) : graph.nodes.length === 0 ? (
            <Section>
              <EmptyState
                icon={MapIcon}
                title={graph.coverage.entities_total === 0 ? "El mapa está vacío" : "Todavía no hay relaciones entre reglas"}
                action={
                  graph.coverage.entities_total > 0 && (
                    <Button onClick={relate} disabled={relating}>
                      <Network data-icon="inline-start" />
                      {relating ? "Relacionando…" : "Relacionar las reglas"}
                    </Button>
                  )
                }
              >
                {graph.coverage.entities_total === 0
                  ? "Aparece cuando hay reglas. Empieza extrayendo reglas del código."
                  : "Se calculan desde el código, sin IA, en un momento. Las reglas extraídas antes de esta versión aún no las tienen."}
              </EmptyState>
            </Section>
          ) : (
            <>
              <div className="grid gap-2">
                <Legend />
                <Muted>
                  {plural(groups.length, "grupo", "grupos")} de reglas relacionadas, con{" "}
                  {plural(graph.coverage.entities_shown, "regla", "reglas")}.
                  {graph.coverage.entities_shown < graph.coverage.entities_total - graph.coverage.entities_without_relationships &&
                    " Por tamaño se muestran los grupos mayores."}
                  {graph.coverage.entities_without_relationships > 0 &&
                    ` ${graph.coverage.entities_without_relationships} reglas no se relacionan con ninguna y están solo en Reglas.`}{" "}
                  Pasa el ratón por una línea para ver el motivo.
                </Muted>
              </div>
              {large.map((group) => (
                <Section
                  key={group.nodes[0].id}
                  title={groupTitle(group.nodes, group.edges)}
                  description={plural(group.nodes.length, "regla relacionada", "reglas relacionadas")}
                >
                  <GroupView nodes={group.nodes} edges={group.edges} selected={node?.id ?? null} onSelect={selectNode} />
                </Section>
              ))}
              {small.length > 0 && (
                <Section
                  title="Parejas de reglas"
                  description="Grupos de solo dos reglas: cada línea es una relación."
                >
                  <ul className="grid gap-1 text-sm">
                    {small.map((group) => {
                      const [first, second] = group.nodes
                      const edge = group.edges[0]
                      return (
                        <li key={first.id} className="flex flex-wrap items-baseline gap-x-2">
                          <button type="button" className="font-medium hover:underline" onClick={() => selectNode(first)}>
                            {first.name}
                          </button>
                          <span className="text-xs text-muted-foreground">
                            {edge ? pairReason(edge, edge.from === first.id) : "relacionada con"}
                          </span>
                          {second && (
                            <button type="button" className="font-medium hover:underline" onClick={() => selectNode(second)}>
                              {second.name}
                            </button>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                </Section>
              )}
            </>
          )}
          <Section title="Procesos" description="Pasos y caminos de cada proceso conocido.">
            {flows.length === 0 ? (
              <Muted>Todavía no hay procesos en este proyecto.</Muted>
            ) : (
              <ul className="grid gap-1">
                {flows.map((flow) => (
                  <li key={flow.entity_id}>
                    <Link
                      to={`/mapa/${flow.entity_id}`}
                      onClick={() => setNode(null)}
                      className={cn(
                        "flex items-center justify-between gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-muted",
                        entityId === flow.entity_id && "bg-muted font-medium",
                      )}
                    >
                      <span className="flex items-center gap-2">
                        <Workflow className="size-4 text-muted-foreground" aria-hidden="true" />
                        {flow.name}
                      </span>
                      {flow.latest_version && <VerificationStatusBadge status={flow.latest_version.verification_status} />}
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>
        {panelOpen && (
          <aside
            ref={panel}
            className="grid content-start gap-3 self-start rounded-xl border bg-card p-4 xl:sticky xl:top-0"
          >
            <div className="flex items-center justify-between">
              <Eyebrow>Detalles</Eyebrow>
              <Button size="icon-xs" variant="ghost" aria-label="Cerrar detalles" onClick={close}>
                <X />
              </Button>
            </div>
            {node ? (
              <NodePanel key={node.id} {...props} node={node} />
            ) : entityId ? (
              <FlowPanel key={entityId} {...props} entityId={entityId} />
            ) : null}
          </aside>
        )}
      </div>
    </>
  )
}
