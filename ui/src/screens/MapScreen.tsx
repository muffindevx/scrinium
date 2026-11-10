import { ArrowRight, Map as MapIcon, Workflow, X } from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { cn } from "cn"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { StatusBadge, VerificationStatusBadge } from "@/components/status"
import { type Tone } from "@/lib/status"
import { Button } from "@/components/ui/button"
import {
  type EdgeOrigin,
  type FlowDetail,
  type FlowSummary,
  type GraphNode,
  type KnowledgeGraph,
  type NodeState,
  getFlow,
  getGraph,
  listFlows,
} from "@/lib/api"
import { edgePath, layoutGraph } from "@/lib/graph"
import { EDGE_ORIGIN_LABELS, ENTITY_TYPE_LABELS, NODE_STATE_LABELS } from "@/lib/labels"
import { useErrors } from "@/screens/hooks"
import { RuleEvidence } from "@/screens/RulesScreen"
import { ErrorText, Eyebrow, Muted, type ScreenProps } from "@/screens/shared"

// Stroke by where each edge comes from; colour is not the only cue.
const EDGE_STYLE: Record<EdgeOrigin, { dash?: string; width: number; className: string }> = {
  confirmed: { width: 2.5, className: "stroke-emerald-500" },
  observed: { width: 1.5, className: "stroke-foreground/60" },
  supported: { width: 1.5, className: "stroke-sky-500" },
  inferred: { dash: "5 4", width: 1.25, className: "stroke-muted-foreground" },
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

function GraphView({
  graph,
  selected,
  onSelect,
}: {
  graph: KnowledgeGraph
  selected: string | null
  onSelect: (node: GraphNode) => void
}) {
  const layout = useMemo(() => layoutGraph(graph.nodes), [graph.nodes])
  const nodes = new Map(graph.nodes.map((node) => [node.id, node]))
  return (
    <div className="overflow-auto rounded-lg border bg-muted/30">
      <svg
        role="img"
        aria-label="Mapa de relaciones entre las partes del sistema"
        width={layout.width}
        height={layout.height}
        className="text-xs"
      >
        <defs>
          <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
          </marker>
        </defs>
        {layout.columns.map((column) => (
          <text key={column.entityType} x={column.x} y={18} className="fill-muted-foreground font-medium">
            {ENTITY_TYPE_LABELS[column.entityType] ?? column.entityType}
          </text>
        ))}
        {graph.edges.map((edge) => {
          const path = edgePath(edge, layout)
          if (!path) return null
          const style = EDGE_STYLE[edge.origin]
          return (
            <path
              key={edge.id}
              d={path}
              fill="none"
              strokeWidth={style.width}
              strokeDasharray={style.dash}
              className={style.className}
              markerEnd="url(#arrow)"
            >
              <title>
                {nodes.get(edge.from)?.name} {edge.relation_type} {nodes.get(edge.to)?.name} ·{" "}
                {EDGE_ORIGIN_LABELS[edge.origin]}
              </title>
            </path>
          )
        })}
        {graph.nodes.map((node) => {
          const position = layout.positions.get(node.id)
          if (!position) return null
          const label = node.name.length > 26 ? `${node.name.slice(0, 25)}…` : node.name
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
              <text x={position.x + 10} y={position.y + 17} className="fill-foreground font-medium">
                {label}
              </text>
              <text x={position.x + 10} y={position.y + 31} className="fill-muted-foreground">
                {NODE_STATE_LABELS[node.state]}
              </text>
              <title>{node.name}</title>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {(Object.keys(EDGE_STYLE) as EdgeOrigin[]).map((origin) => (
        <span key={origin} className="flex items-center gap-1.5">
          <svg width="28" height="8" aria-hidden="true">
            <line
              x1="0"
              y1="4"
              x2="28"
              y2="4"
              strokeWidth={EDGE_STYLE[origin].width}
              strokeDasharray={EDGE_STYLE[origin].dash}
              className={EDGE_STYLE[origin].className}
            />
          </svg>
          {EDGE_ORIGIN_LABELS[origin]}
        </span>
      ))}
    </div>
  )
}

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

export function MapScreen(props: ScreenProps) {
  const { project, onSessionLost } = props
  const { entityId } = useParams()
  const navigate = useNavigate()
  const [graph, setGraph] = useState<KnowledgeGraph | null>(null)
  const [flows, setFlows] = useState<FlowSummary[]>([])
  const [node, setNode] = useState<GraphNode | null>(null)
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
        description="Cómo se relacionan las reglas, los procesos y el código, con lo que ya se sabe. Puede estar incompleto: no se inventa nada."
      />
      <ErrorText>{error}</ErrorText>
      <div className={cn("grid gap-4", panelOpen && "xl:grid-cols-[1fr_20rem]")}>
        <div className="grid min-w-0 content-start gap-4">
          <Section>
            {graph === null ? (
              <Muted>Cargando…</Muted>
            ) : graph.nodes.length === 0 ? (
              <EmptyState icon={MapIcon} title="El mapa está vacío">
                Aparece cuando hay reglas y relaciones. Empieza extrayendo reglas del código.
              </EmptyState>
            ) : (
              <>
                <Legend />
                <GraphView graph={graph} selected={node?.id ?? null} onSelect={selectNode} />
                <Muted>
                  {graph.coverage.entities_shown} de {graph.coverage.entities_total} elementos y{" "}
                  {graph.coverage.relationships_shown} de {graph.coverage.relationships_total} relaciones.
                  {graph.coverage.entities_without_relationships > 0 &&
                    ` ${graph.coverage.entities_without_relationships} elementos no tienen relaciones conocidas.`}
                </Muted>
              </>
            )}
          </Section>
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
