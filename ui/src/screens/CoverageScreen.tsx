import { ChevronRight, FileCode2, Folder, Grid2x2 } from "lucide-react"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router"
import { cn } from "cn"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { type Coverage, type CoverageNode, type DocConflict, getCoverage, getDocConflicts } from "@/lib/api"
import { RuleLink } from "@/components/rule-link"
import { t } from "@/lib/i18n"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted, type ScreenProps } from "@/screens/shared"

type Layer = "verified" | "unverified" | "uncovered"

const LAYERS: { value: Layer; label: string; tone: string }[] = [
  { value: "verified", label: t("Verificadas"), tone: "16 185 129" },
  { value: "unverified", label: t("Sin verificar"), tone: "14 165 233" },
  { value: "uncovered", label: t("Decisiones sin regla"), tone: "245 158 11" },
]

function share(node: CoverageNode, layer: Layer) {
  return node.decisions > 0 ? node[layer] / node.decisions : 0
}

function percent(value: number) {
  return `${Math.round(value * 100)} %`
}

/**
 * Coverage (phase 8.1): where the code decides and which decisions have a rule. Each tile is a
 * folder or file sized by its decisions and coloured by the chosen layer; a click goes down a
 * level, and a file lists the decisions with no rule.
 */
export function CoverageScreen({ project, onSessionLost }: ScreenProps) {
  const [params, setParams] = useSearchParams()
  const path = params.get("ruta") ?? ""
  const layer = (params.get("capa") as Layer | null) ?? "uncovered"
  // What was loaded, with the path it is for: a stale level is never shown for a new one.
  const [loaded, setLoaded] = useState<{ path: string; coverage: Coverage } | null>(null)
  const found = loaded?.path === path ? loaded.coverage : null
  const { error, fail } = useErrors(onSessionLost)
  const [conflicts, setConflicts] = useState<DocConflict[] | null>(null)
  useEffect(() => {
    getDocConflicts(project.id).then(setConflicts).catch(fail)
  }, [project.id, fail])

  useEffect(() => {
    getCoverage(project.id, path)
      .then((coverage) => setLoaded({ path, coverage }))
      .catch(fail)
  }, [project.id, path, fail])

  const go = (next: string) =>
    setParams((current) => {
      const query = new URLSearchParams(current)
      if (next) query.set("ruta", next)
      else query.delete("ruta")
      return query
    })
  const setLayer = (next: string) =>
    setParams((current) => {
      const query = new URLSearchParams(current)
      query.set("capa", next)
      return query
    })
  const tone = LAYERS.find((option) => option.value === layer)?.tone ?? LAYERS[2].tone
  const crumbs = path ? path.split("/") : []
  const children = found?.children ?? []
  const biggest = Math.max(1, ...children.map((child) => child.decisions))

  return (
    <>
      <PageHeader
        title={t("Cobertura")}
        description={t(
          "Dónde decide el código (condiciones, umbrales, tablas, validaciones) y qué decisiones tienen una regla. El tamaño es el número de decisiones; el color, la capa elegida.",
        )}
      />
      <ErrorText>{error}</ErrorText>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav className="flex min-w-0 flex-wrap items-center gap-1 text-sm" aria-label={t("Ruta")}>
          <button type="button" className="hover:underline" onClick={() => go("")}>
            {project.name}
          </button>
          {crumbs.map((crumb, index) => (
            <span key={crumbs.slice(0, index + 1).join("/")} className="flex items-center gap-1">
              <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
              <button type="button" className="hover:underline" onClick={() => go(crumbs.slice(0, index + 1).join("/"))}>
                {crumb}
              </button>
            </span>
          ))}
        </nav>
        <Tabs value={layer} onValueChange={(value) => setLayer(String(value))}>
          <TabsList aria-label={t("Capa")}>
            {LAYERS.map((option) => (
              <TabsTrigger key={option.value} value={option.value}>
                {option.label}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      </div>
      {found === null ? (
        <Muted>{t("Cargando…")}</Muted>
      ) : found.total.decisions === 0 ? (
        <EmptyState icon={Grid2x2} title={t("Todavía no hay decisiones")}>
          {t("Añade y sincroniza una fuente con código: Scrinium busca sus decisiones al sincronizar.")}
        </EmptyState>
      ) : (
        <>
          <Muted>
            {t(
              "{0} decisiones en {1} archivos: {2} con regla verificada, {3} solo con propuestas, {4} sin regla.",
              found.total.decisions,
              found.total.files,
              found.total.verified,
              found.total.unverified,
              found.total.uncovered,
            )}
            {found.pending > 0 && ` ${t("Faltan {0} archivos por analizar; vuelve en un momento.", found.pending)}`}
          </Muted>
          <div className="flex flex-wrap gap-1" role="list" aria-label={t("Carpetas y archivos")}>
            {children.map((child) => {
              const value = share(child, layer)
              return (
                <button
                  key={child.path}
                  type="button"
                  role="listitem"
                  disabled={child.kind === "file" && path === child.path}
                  onClick={() => go(child.path)}
                  title={t("{0}: {1} decisiones, {2} {3}", child.path, child.decisions, percent(value), LAYERS.find((o) => o.value === layer)?.label.toLowerCase() ?? "")}
                  className={cn(
                    "grid min-h-20 min-w-28 content-between gap-1 rounded-md border p-2 text-left text-xs hover:ring-2 hover:ring-ring/40",
                  )}
                  style={{
                    flexGrow: Math.max(child.decisions / biggest, 0.05) * 10,
                    flexBasis: `${Math.max(7, (child.decisions / biggest) * 30)}rem`,
                    backgroundColor: `rgb(${tone} / ${0.08 + value * 0.6})`,
                  }}
                >
                  <span className="flex min-w-0 items-center gap-1 font-medium">
                    {child.kind === "folder" ? (
                      <Folder className="size-3.5 shrink-0" aria-hidden="true" />
                    ) : (
                      <FileCode2 className="size-3.5 shrink-0" aria-hidden="true" />
                    )}
                    <span className="truncate">{child.name}</span>
                  </span>
                  <span className="text-muted-foreground">
                    {child.decisions} · {percent(value)}
                  </span>
                </button>
              )
            })}
          </div>
          {found.uncovered.length > 0 && (
            <Section title={t("Decisiones sin regla en este archivo")}>
              <ul className="grid gap-1 text-sm">
                {found.uncovered.map((decision) => (
                  <li key={decision.start_line} className="font-mono text-xs">
                    {t("líneas {0}-{1}", decision.start_line, decision.end_line)} · {decision.kind}
                  </li>
                ))}
              </ul>
              <Muted>
                <Link to={`/reglas?q=${encodeURIComponent(path)}`} className="hover:underline">
                  {t("Ver las reglas de este archivo")}
                </Link>
              </Muted>
            </Section>
          )}
          {conflicts && conflicts.length > 0 && !path && (
            <Section
              title={t("Documentos frente al código")}
              description={t("Frases de los documentos del proyecto cuyos números no son los que decide el código. Cuál tiene razón lo decide una persona.")}
            >
              <ul className="grid gap-2 text-sm">
                {conflicts.map((conflict) => (
                  <li key={`${conflict.document}${conflict.line}${conflict.rule_id}`} className="grid gap-0.5 rounded-md border p-3">
                    <span className="font-mono text-xs text-muted-foreground">
                      {conflict.document}:{conflict.line}
                    </span>
                    <span>«{conflict.sentence}»</span>
                    <span className="text-xs">
                      {t("El documento dice {0}; la regla", conflict.says.join(", "))}{" "}
                      <RuleLink id={conflict.rule_id} className="font-medium hover:underline">
                        {conflict.title}
                      </RuleLink>{" "}
                      {t("decide con {0} ({1}).", conflict.code.join(", "), conflict.place)}
                    </span>
                  </li>
                ))}
              </ul>
            </Section>
          )}
          <Muted>
            {t(
              "Lo que vale la capa «sin regla», medido: {0} de {1} reglas de referencia caen dentro de una decisión.",
              found.layer_recall.inside_a_decision,
              found.layer_recall.rules,
            )}
          </Muted>
        </>
      )}
    </>
  )
}
