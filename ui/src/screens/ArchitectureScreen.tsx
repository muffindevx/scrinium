import { ChevronRight, FileCode2, Folder, Minus, Network, Plus, SquareFunction } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router"
import { cn } from "cn"
import { CodeExcerpt } from "@/components/code-excerpt"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { type Architecture, type ArchitectureModule, getArchitecture } from "@/lib/api"
import { dependencyPath, layoutArchitecture } from "@/lib/architecture"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Eyebrow, Muted, type ScreenProps } from "@/screens/shared"

const ICONS = { folder: Folder, file: FileCode2, function: SquareFunction }
const VERIFIED = new Set(["human_verified", "source_supported", "test_supported"])
const ZOOMS = [0.5, 0.65, 0.8, 1, 1.25]

/** The path as clickable steps: project / src / game / horde.ts */
function Breadcrumbs({ path, onGo }: { path: string; onGo: (path: string) => void }) {
  const parts = path.replace(/\/$/, "").split("/").filter(Boolean)
  return (
    <nav aria-label="Ruta" className="flex flex-wrap items-center gap-1 text-sm">
      <button type="button" className="font-medium hover:underline" onClick={() => onGo("")}>
        Proyecto
      </button>
      {parts.map((part, index) => {
        const isLast = index === parts.length - 1
        const target = parts.slice(0, index + 1).join("/") + (isLast && !path.endsWith("/") ? "" : "/")
        return (
          <span key={target} className="flex items-center gap-1">
            <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
            {isLast ? (
              <span className="font-mono text-xs">{part}</span>
            ) : (
              <button type="button" className="font-mono text-xs hover:underline" onClick={() => onGo(target)}>
                {part}
              </button>
            )}
          </span>
        )
      })}
    </nav>
  )
}

function Diagram({
  architecture,
  shown,
  selected,
  zoom,
  onSelect,
}: {
  architecture: Architecture
  shown: Set<string>
  selected: string | null
  zoom: number
  onSelect: (module: ArchitectureModule) => void
}) {
  const modules = architecture.modules.filter((module) => shown.has(module.key))
  const edges = architecture.dependencies.filter((edge) => shown.has(edge.from) && shown.has(edge.to))
  const layout = useMemo(
    () =>
      layoutArchitecture(
        modules.map((module) => module.key),
        edges,
      ),
    [modules, edges],
  )
  const heaviest = Math.max(1, ...edges.map((edge) => edge.calls))
  const related = new Set(
    selected ? edges.filter((e) => e.from === selected || e.to === selected).flatMap((e) => [e.from, e.to]) : [],
  )
  const { boxWidth: w, boxHeight: h } = layout
  return (
    <div className="max-h-[70vh] overflow-auto rounded-lg border bg-muted/30">
      <svg
        role="img"
        aria-label="Módulos del código y las llamadas entre ellos"
        width={layout.width * zoom}
        height={layout.height * zoom}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        className="text-xs"
      >
        <defs>
          <marker id="arch-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M 0 0 L 10 5 L 0 10 z" className="fill-muted-foreground" />
          </marker>
        </defs>
        {edges.map((edge) => {
          const from = layout.boxes.get(edge.from)
          const to = layout.boxes.get(edge.to)
          if (!from || !to) return null
          const lit = selected === edge.from || selected === edge.to
          return (
            <path
              key={`${edge.from}->${edge.to}`}
              d={dependencyPath(from, to, w, h)}
              fill="none"
              markerEnd="url(#arch-arrow)"
              strokeWidth={1 + 3 * Math.log2(1 + edge.calls) / Math.log2(1 + heaviest)}
              className={cn(
                "transition-opacity",
                lit ? "stroke-primary opacity-100" : "stroke-muted-foreground",
                selected && !lit ? "opacity-15" : "opacity-60",
              )}
            >
              <title>{`${edge.from} → ${edge.to}: ${edge.calls} llamadas`}</title>
            </path>
          )
        })}
        {modules.map((module) => {
          const box = layout.boxes.get(module.key)
          if (!box) return null
          const dim = selected !== null && selected !== module.key && !related.has(module.key)
          return (
            <g
              key={module.key}
              role="button"
              tabIndex={0}
              aria-label={`${module.name}: ${module.rules_count} reglas, ${module.functions} funciones`}
              aria-pressed={selected === module.key}
              transform={`translate(${box.x} ${box.y})`}
              className={cn("cursor-pointer outline-none transition-opacity", dim && "opacity-35")}
              onClick={() => onSelect(module)}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault()
                  onSelect(module)
                }
              }}
            >
              <rect
                width={w}
                height={h}
                rx={8}
                className={cn(
                  "fill-card stroke-border",
                  selected === module.key && "stroke-primary [stroke-width:2]",
                  module.rules_count > 0 && selected !== module.key && "stroke-foreground/40",
                )}
              />
              <text x={12} y={22} className="fill-foreground text-[13px] font-medium">
                {module.name.length > 24 ? `${module.name.slice(0, 23)}…` : module.name}
              </text>
              <text x={12} y={42} className="fill-muted-foreground">
                {module.rules_count} reglas · {module.kind === "function" ? `${module.calls_in} la llaman` : `${module.functions} funciones`}
              </text>
            </g>
          )
        })}
      </svg>
    </div>
  )
}

function ModulePanel({
  module,
  architecture,
  onOpen,
}: {
  module: ArchitectureModule
  architecture: Architecture
  onOpen: (path: string) => void
}) {
  const Icon = ICONS[module.kind]
  const names = new Map(architecture.modules.map((m) => [m.key, m.name]))
  const calls = architecture.dependencies.filter((edge) => edge.from === module.key)
  const calledBy = architecture.dependencies.filter((edge) => edge.to === module.key)
  return (
    <Section
      className="min-w-0"
      title={
        <span className="flex min-w-0 items-center gap-2">
          <Icon className="size-4 shrink-0" />
          <code className="truncate">{module.name}</code>
        </span>
      }
      description={
        module.kind === "function"
          ? `Líneas ${module.start_line}-${module.end_line} · la llaman ${module.calls_in} veces · hace ${module.calls_out} llamadas`
          : `${module.files} archivos · ${module.functions} funciones · recibe ${module.calls_in} llamadas · hace ${module.calls_out}`
      }
      action={
        module.kind !== "function" ? (
          <Button size="sm" onClick={() => onOpen(module.key)}>
            Abrir
          </Button>
        ) : undefined
      }
    >
      {(calls.length > 0 || calledBy.length > 0) && (
        <div className="grid gap-1 text-sm break-words">
          {calls.length > 0 && (
            <p>
              <Eyebrow>Llama a</Eyebrow>{" "}
              {calls.map((edge) => `${names.get(edge.to) ?? edge.to} (${edge.calls})`).join(", ")}
            </p>
          )}
          {calledBy.length > 0 && (
            <p>
              <Eyebrow>Lo llaman</Eyebrow>{" "}
              {calledBy.map((edge) => `${names.get(edge.from) ?? edge.from} (${edge.calls})`).join(", ")}
            </p>
          )}
        </div>
      )}
      {module.kind === "function" && module.revision_id && module.start_line && module.end_line && (
        <CodeExcerpt
          revisionId={module.revision_id}
          path={architecture.path}
          startLine={0}
          endLine={0}
          window={[module.start_line, module.end_line]}
          className="max-h-80 overflow-y-auto"
        />
      )}
      <div className="grid gap-1.5">
        <Eyebrow>Reglas ({module.rules_count})</Eyebrow>
        <p className="flex flex-wrap gap-3 text-xs text-muted-foreground">
          <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-emerald-500" />verificada</span>
          <span className="flex items-center gap-1"><span className="size-2 rounded-full bg-amber-500" />sin verificar</span>
        </p>
        {module.rules.length === 0 ? (
          <Muted>Ninguna regla vive aquí.</Muted>
        ) : (
          <ul className="grid gap-1 text-sm">
            {module.rules.map((rule) => (
              <li key={rule.entity_id} className="flex items-baseline gap-2">
                <span
                  aria-hidden="true"
                  className={cn(
                    "size-2 shrink-0 translate-y-[-1px] rounded-full",
                    VERIFIED.has(rule.verification_status ?? "") ? "bg-emerald-500" : "bg-amber-500",
                    rule.verification_status === "rejected" && "bg-rose-500",
                  )}
                />
                <Link to={`/reglas/${rule.entity_id}`} className="min-w-0 hover:underline">
                  {rule.name}
                </Link>
                <span className="sr-only">({rule.verification_status === "rejected" ? "falso positivo" : VERIFIED.has(rule.verification_status ?? "") ? "verificada" : "sin verificar"})</span>
              </li>
            ))}
            {module.rules_count > module.rules.length && (
              <li className="text-muted-foreground">y {module.rules_count - module.rules.length} más…</li>
            )}
          </ul>
        )}
      </div>
    </Section>
  )
}

export function ArchitectureScreen({ project, onSessionLost }: ScreenProps) {
  const [params, setParams] = useSearchParams()
  const path = params.get("ruta") ?? ""
  const [architecture, setArchitecture] = useState<Architecture | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [query, setQuery] = useState("")
  const [onlyRules, setOnlyRules] = useState(false)
  const [zoom, setZoom] = useState(3)
  const { error, setError, fail } = useErrors(onSessionLost)
  useEffect(() => {
    const live = { current: true }
    setError(null)
    getArchitecture(project.id, path)
      .then((found) => {
        if (!live.current) return
        setArchitecture(found)
        setSelected(null)
      })
      .catch((failure: unknown) => live.current && fail(failure))
    return () => {
      live.current = false
    }
  }, [project.id, path, fail, setError])

  const go = (next: string) => setParams(next ? { ruta: next } : {})
  const shown = useMemo(() => {
    const term = query.trim().toLowerCase()
    return new Set(
      (architecture?.modules ?? [])
        .filter((module) => (!onlyRules || module.rules_count > 0) && (!term || module.name.toLowerCase().includes(term)))
        .map((module) => module.key),
    )
  }, [architecture, query, onlyRules])
  const current = architecture?.modules.find((module) => module.key === selected) ?? null

  return (
    <>
      <PageHeader
        title="Arquitectura"
        description="El código como módulos y las llamadas entre ellos, de carpeta a archivo, de archivo a función y de función a regla. Sale del análisis del código, sin IA."
      />
      {architecture && <Breadcrumbs path={architecture.path} onGo={go} />}
      <div className="flex flex-wrap items-center gap-3">
        <Input
          aria-label="Filtrar módulos"
          placeholder="Filtrar módulos…"
          className="max-w-56"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={onlyRules} onChange={(event) => setOnlyRules(event.target.checked)} />
          Solo con reglas
        </label>
        <div className="flex items-center gap-1" role="group" aria-label="Zoom">
          <Button size="icon-sm" variant="outline" aria-label="Alejar" disabled={zoom === 0} onClick={() => setZoom(zoom - 1)}>
            <Minus />
          </Button>
          <span className="w-12 text-center text-xs text-muted-foreground tabular-nums">
            {Math.round(ZOOMS[zoom] * 100)} %
          </span>
          <Button
            size="icon-sm"
            variant="outline"
            aria-label="Acercar"
            disabled={zoom === ZOOMS.length - 1}
            onClick={() => setZoom(zoom + 1)}
          >
            <Plus />
          </Button>
        </div>
      </div>
      <ErrorText>{error}</ErrorText>
      {architecture === null ? (
        !error && <Muted>Cargando…</Muted>
      ) : architecture.modules.length === 0 ? (
        <EmptyState icon={Network} title="Todavía no hay código analizado">
          Sincroniza una fuente con código para ver sus módulos.
        </EmptyState>
      ) : (
        <div className="grid min-w-0 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,24rem)]">
          <Diagram
            architecture={architecture}
            shown={shown}
            selected={selected}
            zoom={ZOOMS[zoom]}
            onSelect={(module) => {
              if (module.kind !== "function" && selected === module.key) go(module.key)
              else setSelected(selected === module.key ? null : module.key)
            }}
          />
          {current ? (
            <ModulePanel module={current} architecture={architecture} onOpen={go} />
          ) : (
            <Section title="Cómo leerlo">
              <Muted>
                Cada caja es una carpeta, un archivo o, dentro de un archivo, una función. Las flechas son llamadas
                resueltas: más gruesas, más llamadas. Quien llama queda a la izquierda.
              </Muted>
              <Muted>Pulsa una caja para ver sus reglas; púlsala otra vez, o «Abrir», para entrar.</Muted>
            </Section>
          )}
        </div>
      )}
    </>
  )
}
