import { Boxes, ChevronRight, FileCode2, FileText, Folder, FolderTree, Grid2x2 } from "lucide-react"
import { useEffect, useState } from "react"
import { Link, useSearchParams } from "react-router"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { RuleLink } from "@/components/rule-link"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { type Coverage, type CoverageNode, type DocConflict, getCoverage, getDocConflicts } from "@/lib/api"
import { t } from "@/lib/i18n"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Loading, Muted, type ScreenProps } from "@/screens/shared"

type Counts = Pick<CoverageNode, "decisions" | "verified" | "unverified" | "uncovered">

// The three layers in one bar, like a disk's used and free space.
const LAYERS = [
  { key: "verified", label: t("Con regla verificada"), color: "rgb(16 185 129)" },
  { key: "unverified", label: t("Solo propuestas"), color: "rgb(14 165 233)" },
  { key: "uncovered", label: t("Sin regla"), color: "rgb(245 158 11)" },
] as const

function percent(part: number, whole: number) {
  return whole > 0 ? `${Math.round((part / whole) * 100)} %` : "0 %"
}

/** One stacked bar: verified, unverified and uncovered decisions, in proportion. */
function LayerBar({ counts, size = "sm" }: { counts: Counts; size?: "sm" | "lg" }) {
  const label = t(
    "{0} decisiones: {1} con regla verificada, {2} solo con propuestas, {3} sin regla",
    counts.decisions,
    counts.verified,
    counts.unverified,
    counts.uncovered,
  )
  return (
    <div
      role="img"
      aria-label={label}
      title={label}
      className={`flex w-full overflow-hidden rounded-full bg-muted ${size === "lg" ? "h-3" : "h-2"}`}
    >
      {LAYERS.map(({ key, color }) =>
        counts[key] > 0 ? (
          <span
            key={key}
            // A layer with any decision stays visible, however small its share.
            style={{ width: `${(counts[key] / Math.max(counts.decisions, 1)) * 100}%`, minWidth: "3px", backgroundColor: color }}
          />
        ) : null,
      )}
    </div>
  )
}

function Legend({ counts }: { counts: Counts }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {LAYERS.map(({ key, label, color }) => (
        <li key={key} className="flex items-center gap-1.5">
          <span aria-hidden="true" className="size-2.5 rounded-full" style={{ backgroundColor: color }} />
          {label}
          <span className="font-medium text-foreground tabular-nums">{counts[key]}</span>
          <span className="tabular-nums">({percent(counts[key], counts.decisions)})</span>
        </li>
      ))}
    </ul>
  )
}

const ICONS = { folder: Folder, file: FileCode2, domain: Boxes } as const

/**
 * Coverage (phase 8.1): where the code decides and which decisions have a rule. Each folder,
 * file or domain is one row with one bar, like a disk's usage: verified, proposals only, and
 * decisions with no rule. A click goes down a level; a file lists its decisions with no rule.
 */
export function CoverageScreen({ project, onSessionLost }: ScreenProps) {
  const [params, setParams] = useSearchParams()
  const path = params.get("ruta") ?? ""
  const byDomain = params.get("por") === "dominio"
  const domain = byDomain ? params.get("dominio") : null
  const key = `${byDomain}|${domain ?? ""}|${path}`
  // What was loaded, with the view it is for: a stale level is never shown for a new one.
  const [loaded, setLoaded] = useState<{ key: string; coverage: Coverage } | null>(null)
  const found = loaded?.key === key ? loaded.coverage : null
  const { error, fail, failure, attempt, retry } = useErrors(onSessionLost)
  const [conflicts, setConflicts] = useState<DocConflict[] | null>(null)
  useEffect(() => {
    getDocConflicts(project.id).then(setConflicts).catch(fail)
  }, [project.id, fail])
  useEffect(() => {
    // "·" stands for the root domain (files at the top), which has no name to put in the URL.
    getCoverage(project.id, path, byDomain, domain === "·" ? "" : domain)
      .then((coverage) => setLoaded({ key, coverage }))
      .catch(fail)
  }, [project.id, path, byDomain, domain, key, fail, attempt])

  const update = (changes: Record<string, string | null>) =>
    setParams((current) => {
      const query = new URLSearchParams(current)
      for (const [name, value] of Object.entries(changes)) {
        if (value) query.set(name, value)
        else query.delete(name)
      }
      return query
    })
  const open = (child: CoverageNode) =>
    child.kind === "domain" ? update({ dominio: child.path || "·", ruta: null }) : update({ ruta: child.path })
  const crumbs = path ? path.split("/") : []
  const children = found?.children ?? []
  const biggest = Math.max(1, ...children.map((child) => child.decisions))
  const domainName = (name: string) => (name && name !== "·" ? name : t("(raíz)"))

  return (
    <>
      <PageHeader
        title={t("Cobertura")}
        description={t("Dónde decide el código (condiciones, umbrales, tablas, validaciones) y cuántas de esas decisiones tienen una regla.")}
      />
      <ErrorText>{found !== null && error}</ErrorText>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <nav className="flex min-w-0 flex-wrap items-center gap-1 text-sm" aria-label={t("Ruta")}>
          <button type="button" className="hover:underline" onClick={() => update({ ruta: null, dominio: null })}>
            {project.name}
          </button>
          {domain !== null && (
            <span className="flex items-center gap-1">
              <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
              <button type="button" className="font-medium hover:underline" onClick={() => update({ ruta: null })}>
                {domainName(domain)}
              </button>
            </span>
          )}
          {crumbs.map((crumb, index) => (
            <span key={crumbs.slice(0, index + 1).join("/")} className="flex items-center gap-1">
              <ChevronRight className="size-3.5 text-muted-foreground" aria-hidden="true" />
              <button type="button" className="hover:underline" onClick={() => update({ ruta: crumbs.slice(0, index + 1).join("/") })}>
                {crumb}
              </button>
            </span>
          ))}
        </nav>
        <Tabs
          value={byDomain ? "dominio" : "carpeta"}
          onValueChange={(value) => update({ por: value === "dominio" ? "dominio" : null, dominio: null, ruta: null })}
        >
          <TabsList aria-label={t("Agrupar por")}>
            <TabsTrigger value="carpeta">
              <FolderTree />
              {t("Carpetas")}
            </TabsTrigger>
            <TabsTrigger value="dominio">
              <Boxes />
              {t("Dominios")}
            </TabsTrigger>
          </TabsList>
        </Tabs>
      </div>
      {found === null ? (
        <Loading failure={failure} onRetry={retry} />
      ) : found.total.decisions === 0 ? (
        <EmptyState icon={Grid2x2} title={t("Todavía no hay decisiones")}>
          {t("Añade y sincroniza una fuente con código: Scrinium busca sus decisiones al sincronizar.")}
        </EmptyState>
      ) : (
        <>
          <Section>
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <p className="text-sm">
                <span className="font-heading text-2xl font-medium tabular-nums">{percent(found.total.verified + found.total.unverified, found.total.decisions)}</span>{" "}
                <span className="text-muted-foreground">
                  {t("de {0} decisiones en {1} archivos tienen una regla", found.total.decisions, found.total.files)}
                </span>
              </p>
              {(found.total.bugs ?? 0) > 0 && (
                <Muted>{t("Sus reglas tienen {0} tickets de bug.", found.total.bugs ?? 0)}</Muted>
              )}
            </div>
            <LayerBar counts={found.total} size="lg" />
            <Legend counts={found.total} />
            {found.pending > 0 && <Muted>{t("Faltan {0} archivos por analizar; vuelve en un momento.", found.pending)}</Muted>}
          </Section>
          {byDomain && domain === null && (
            <Muted>
              {children.some((child) => child.declared)
                ? t("Los dominios con nombre vienen de .scrinium/domains.yaml; el resto se proponen por carpeta.")
                : t("Dominios propuestos por carpeta. Para nombrarlos o juntarlos, crea .scrinium/domains.yaml en el repositorio.")}
            </Muted>
          )}
          {(found.domain_errors ?? []).map((message) => (
            <ErrorText key={message}>{t("No se pudo leer el archivo de dominios: {0}", message)}</ErrorText>
          ))}
          {children.length > 0 && (
            <Section className="gap-0 p-0">
              <ul aria-label={t("Carpetas y archivos")} className="divide-y">
                {children.map((child) => {
                  const Icon = ICONS[child.kind]
                  const here = child.kind === "file" && path === child.path
                  return (
                    <li key={child.path}>
                      <button
                        type="button"
                        disabled={here}
                        onClick={() => open(child)}
                        className="grid w-full gap-1.5 px-4 py-3 text-left text-sm hover:bg-muted/50 disabled:cursor-default disabled:hover:bg-transparent sm:grid-cols-[minmax(0,14rem)_1fr_auto] sm:items-center sm:gap-4"
                      >
                        <span className="flex min-w-0 items-center gap-2 font-medium">
                          <Icon className="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                          <span className="truncate">{child.kind === "domain" ? domainName(child.name) : child.name}</span>
                          {child.kind === "domain" && !child.declared && <span className="shrink-0 text-xs font-normal text-muted-foreground">{t("propuesto")}</span>}
                        </span>
                        {/* The bar's length is the share of decisions; its colours, the layers. */}
                        <span className="block min-w-0">
                          <span className="block" style={{ width: `${Math.max((child.decisions / biggest) * 100, 4)}%` }}>
                            <LayerBar counts={child} />
                          </span>
                        </span>
                        <span className="flex gap-3 text-xs text-muted-foreground tabular-nums sm:justify-end">
                          <span>{t("{0} decisiones", child.decisions)}</span>
                          <span className="font-medium text-foreground">{percent(child.verified + child.unverified, child.decisions)}</span>
                          {(child.bugs ?? 0) > 0 && <span>{child.bugs === 1 ? t("1 bug") : t("{0} bugs", child.bugs ?? 0)}</span>}
                        </span>
                      </button>
                    </li>
                  )
                })}
              </ul>
            </Section>
          )}
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
          {conflicts && conflicts.length > 0 && !path && domain === null && <DocConflicts conflicts={conflicts} />}
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

/** Documents against the code (phase 8.2), one card per rule: what the code decides once, then
 * each document that says otherwise. The sentence is one click away; which one is right is a
 * person's call. */
function DocConflicts({ conflicts }: { conflicts: DocConflict[] }) {
  const byRule = new Map<string, DocConflict[]>()
  for (const conflict of conflicts) byRule.set(conflict.rule_id, [...(byRule.get(conflict.rule_id) ?? []), conflict])
  return (
    <Section
      title={t("Documentos frente al código")}
      description={t("Números de los documentos que no coinciden con los que decide el código.")}
    >
      <ul className="grid gap-3">
        {[...byRule.values()].map((group) => {
          const [first] = group
          return (
            <li key={first.rule_id} className="grid gap-2 rounded-lg border p-3">
              <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-1">
                <RuleLink id={first.rule_id} className="min-w-0 font-medium hover:underline">
                  {first.title}
                </RuleLink>
                <span className="flex min-w-0 items-baseline gap-2 text-sm">
                  <span className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground">
                    <FileCode2 className="size-3" aria-hidden="true" />
                    {t("El código decide")}
                  </span>
                  <Value values={first.code} />
                </span>
              </div>
              <ul className="grid gap-1">
                {group.map((conflict) => (
                  <li key={`${conflict.document}${conflict.line}`} className="rounded-md bg-muted/50 px-2 py-1.5 text-sm">
                    <details>
                      <summary className="flex cursor-pointer flex-wrap items-baseline gap-x-2 gap-y-0.5 [&::-webkit-details-marker]:hidden">
                        <FileText className="size-3 shrink-0 self-center text-muted-foreground" aria-hidden="true" />
                        <span className="min-w-0 font-mono text-xs break-all text-muted-foreground">
                          {conflict.document}:{conflict.line}
                        </span>
                        <span className="text-xs text-muted-foreground">{t("dice")}</span>
                        <Value values={conflict.says} />
                      </summary>
                      <p className="mt-1 border-l-2 pl-2 text-muted-foreground">«{conflict.sentence}»</p>
                    </details>
                  </li>
                ))}
              </ul>
            </li>
          )
        })}
      </ul>
    </Section>
  )
}

/** Values as chips, so a long name wraps inside its chip instead of pushing the card wider. */
function Value({ values }: { values: string[] }) {
  return (
    <span className="flex min-w-0 flex-wrap gap-1">
      {values.map((value, index) => (
        <code key={`${value}${index}`} className="rounded bg-background px-1.5 py-0.5 text-xs font-medium break-all">
          {value}
        </code>
      ))}
    </span>
  )
}
