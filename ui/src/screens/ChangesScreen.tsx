import { ArrowRight, FileDown, GitCompare, Sparkle } from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import { useSearchParams } from "react-router"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { RuleLink } from "@/components/rule-link"
import { StatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import { type ChangeRefs, type Changes, type ChangeSide, compareChanges, getChangeRefs, releaseNotes } from "@/lib/api"
import { t } from "@/lib/i18n"
import { type Tone } from "@/lib/status"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Muted, type ScreenProps } from "@/screens/shared"

const LEVELS: Record<string, [Tone, string]> = {
  changed: ["attention", t("Cambia la regla")],
  review: ["in_progress", t("Revisar")],
  removed: ["discarded", t("Retirada")],
  moved: ["no_evidence", t("Movida")],
}

const LOCAL = "local"

function where(side: ChangeSide) {
  return `${side.path}:${side.start_line}${side.end_line !== side.start_line ? `-${side.end_line}` : ""}`
}

function values(side: ChangeSide | null) {
  return side && side.values.length > 0 ? side.values.join(", ") : "—"
}

/**
 * Cambios (phase 7.1): two refs of a Git source (branch, tag, commit or the working folder) and
 * what the change does to the rules, with the same engine as `scrinium check`: rules whose values
 * changed, edited, retired or moved, and new decisions nobody has written a rule for.
 */
export function ChangesScreen({ project, onSessionLost }: ScreenProps) {
  const [params, setParams] = useSearchParams()
  const [refs, setRefs] = useState<ChangeRefs | null>(null)
  const [result, setResult] = useState<Changes | null>(null)
  const [busy, setBusy] = useState(false)
  const { error, setError, fail } = useErrors(onSessionLost)
  const sourceId = params.get("fuente") ?? undefined
  const base = params.get("base") ?? ""
  const head = params.get("cabeza") ?? LOCAL

  useEffect(() => {
    getChangeRefs(project.id, sourceId)
      .then((loaded) => {
        setRefs(loaded)
        if (!base && loaded.branches.length > 0) {
          const main = loaded.branches.find((name) => name === "main" || name === "master") ?? loaded.branches[0]
          setParams((current) => {
            const next = new URLSearchParams(current)
            next.set("base", main)
            return next
          }, { replace: true })
        }
      })
      .catch(fail)
  }, [project.id, sourceId, base, setParams, fail])

  const options = useMemo(() => {
    if (!refs) return []
    return [
      ...refs.branches.map((name) => ({ value: name, label: t("Rama {0}", name) })),
      ...refs.tags.map((name) => ({ value: name, label: t("Tag {0}", name) })),
      ...refs.commits.map((commit) => ({ value: commit.sha, label: `${commit.sha.slice(0, 8)} · ${commit.subject}` })),
    ]
  }, [refs])

  const set = (name: string, value: string) =>
    setParams((current) => {
      const next = new URLSearchParams(current)
      next.set(name, value)
      return next
    })

  const run = useCallback(() => {
    if (!base) return
    setBusy(true)
    setError(null)
    compareChanges(project.id, base, head, sourceId)
      .then(setResult)
      .catch(fail)
      .finally(() => setBusy(false))
  }, [project.id, base, head, sourceId, setError, fail])

  const downloadNotes = () => {
    releaseNotes(project.id, base, head, sourceId)
      .then((text) => {
        const link = document.createElement("a")
        link.href = URL.createObjectURL(new Blob([text], { type: "text/markdown" }))
        link.download = `cambios-${base}-${head}.md`.replace(/[^\w.-]+/g, "-")
        link.click()
        URL.revokeObjectURL(link.href)
      })
      .catch(fail)
  }

  return (
    <>
      <PageHeader
        title={t("Cambios")}
        description={t(
          "Qué hace un cambio a las reglas entre dos referencias cualesquiera: una rama, un tag, un commit o tus cambios locales. El mismo motor que scrinium check, sin IA.",
        )}
      />
      <ErrorText>{error}</ErrorText>
      {refs === null ? (
        <Muted>{t("Cargando…")}</Muted>
      ) : (
        <Section title={t("Comparar")}>
          <div className="grid gap-3 md:grid-cols-[1fr_auto_1fr_auto] md:items-end">
            {refs.sources.length > 1 && (
              <div className="grid gap-1 md:col-span-4">
                <Label>{t("Fuente")}</Label>
                <Select
                  value={refs.source_id}
                  options={refs.sources.map((source) => ({ value: source.id, label: source.name }))}
                  onValueChange={(value) => set("fuente", value)}
                />
              </div>
            )}
            <div className="grid min-w-0 gap-1">
              <Label>{t("Desde")}</Label>
              <Select value={base} options={options} onValueChange={(value) => set("base", value)} />
            </div>
            <ArrowRight className="mb-2 size-4 text-muted-foreground max-md:hidden" aria-hidden="true" />
            <div className="grid min-w-0 gap-1">
              <Label>{t("Hasta")}</Label>
              <Select
                value={head}
                options={[{ value: LOCAL, label: t("Tus cambios locales") }, ...options]}
                onValueChange={(value) => set("cabeza", value)}
              />
            </div>
            <Button onClick={run} disabled={busy || !base}>
              <GitCompare data-icon="inline-start" />
              {busy ? t("Comparando…") : t("Comparar")}
            </Button>
          </div>
        </Section>
      )}
      {result && (
        <div>
          <Button size="sm" variant="outline" onClick={() => downloadNotes()}>
            <FileDown data-icon="inline-start" />
            {t("Notas de versión (Markdown)")}
          </Button>
        </div>
      )}
      {result && <ChangesResult result={result} />}
    </>
  )
}

function ChangesResult({ result }: { result: Changes }) {
  const affected = result.rules.filter((change) => change.level !== "moved")
  return (
    <>
      <Muted>
        {t("{0} archivos cambiados · {1} reglas afectadas · {2} s", result.changed_files.length, affected.length, result.seconds)}
      </Muted>
      {result.rules.length === 0 ? (
        <EmptyState icon={GitCompare} title={t("Ninguna regla cambia")}>
          {t("El cambio no toca ninguna decisión con una regla.")}
        </EmptyState>
      ) : (
        <Section title={t("Reglas")}>
          <ul className="grid gap-2">
            {result.rules.map((change) => (
              <li key={`${change.rule_id}${change.before.path}${change.before.start_line}`} className="grid gap-1 rounded-md border p-3 text-sm">
                <div className="flex flex-wrap items-center gap-2">
                  <StatusBadge tone={LEVELS[change.level][0]}>{LEVELS[change.level][1]}</StatusBadge>
                  <RuleLink id={change.rule_id} className="font-medium hover:underline">
                    {change.title}
                  </RuleLink>
                  {change.status === "human_verified" && <Muted>{t("verificada")}</Muted>}
                </div>
                <p className="font-mono text-xs break-all text-muted-foreground">
                  {where(change.before)}
                  {change.after && ` → ${where(change.after)}`}
                </p>
                {change.level === "changed" && (
                  <p className="text-xs">
                    {t("Antes")}: <code>{values(change.before)}</code> · {t("Ahora")}: <code>{values(change.after)}</code>
                  </p>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}
      {result.new_decisions.length > 0 && (
        <Section
          title={t("Decisiones nuevas sin regla")}
          description={t("Condiciones, umbrales o validaciones que aparecen con el cambio y que ninguna regla describe todavía.")}
        >
          <ul className="grid gap-1 text-sm">
            {result.new_decisions.map((decision) => (
              <li key={where(decision)} className="flex min-w-0 flex-wrap items-center gap-2">
                <Sparkle className="size-3.5 text-muted-foreground" aria-hidden="true" />
                <span className="font-mono text-xs break-all">{where(decision)}</span>
                {decision.symbol && <Muted>{decision.symbol}</Muted>}
                {decision.values.length > 0 && <code className="text-xs">{decision.values.join(", ")}</code>}
              </li>
            ))}
          </ul>
        </Section>
      )}
    </>
  )
}
