import { t } from "@/lib/i18n"
import { ArrowLeft, Camera, GitCompare, Tags } from "lucide-react"
import { type FormEvent, useCallback, useEffect, useState } from "react"
import { Link, useNavigate, useParams } from "react-router"
import { EmptyState, PageHeader, Section } from "@/components/page"
import { StatusBadge } from "@/components/status"
import { type Tone } from "@/lib/status"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/input"
import { Select } from "@/components/ui/select"
import {
  type Release,
  type Source,
  compareReleases,
  discoverReleases,
  listReleases,
  listSources,
  snapshotRelease,
} from "@/lib/api"
import { shortSha } from "@/lib/labels"
import { AnalysisHistory } from "@/screens/AnalysisHistory"
import { useErrors } from "@/screens/hooks"
import { AnalysisDetail, ErrorText, Muted, type ScreenProps } from "@/screens/shared"

const STATUS: Record<Release["status"], [Tone, string]> = {
  discovered: ["no_evidence", t("Sin estado guardado")],
  indexing: ["in_progress", t("Guardando…")],
  ready: ["verified", t("Estado guardado")],
  partial: ["attention", t("Guardado en parte")],
  failed: ["attention", t("Falló")],
}

function Explorer({ project, onSessionLost }: ScreenProps) {
  const navigate = useNavigate()
  const [sources, setSources] = useState<Source[]>([])
  const [releases, setReleases] = useState<Release[] | null>(null)
  const [base, setBase] = useState("")
  const [target, setTarget] = useState("")
  const [notice, setNotice] = useState<string | null>(null)
  const { error, setError, fail } = useErrors(onSessionLost)

  const load = useCallback(() => {
    Promise.all([listSources(project.id), listReleases(project.id)])
      .then(([loadedSources, loadedReleases]) => {
        setSources(loadedSources.filter((source) => source.kind === "git_repository"))
        setReleases(loadedReleases)
      })
      .catch(fail)
  }, [project.id, fail])
  useEffect(load, [load])

  const discover = (source: Source) => {
    setError(null)
    discoverReleases(source.id)
      .then((result) => {
        setNotice(t("{0} versiones nuevas, {1} ya conocidas.", result.created.length, result.existing))
        load()
      })
      .catch(fail)
  }
  const snapshot = (release: Release) => {
    setError(null)
    snapshotRelease(release.id).then(load).catch(fail)
  }
  const compare = (event: FormEvent) => {
    event.preventDefault()
    setError(null)
    compareReleases(project.id, base, target)
      .then((analysis) => navigate(`/versiones/${analysis.id}`))
      .catch(fail)
  }
  const names = new Map((releases ?? []).map((release) => [release.id, release.release_name]))
  const options = (releases ?? []).map((release) => ({ value: release.id, label: release.release_name }))

  return (
    <>
      {(releases?.length ?? 0) >= 2 && (
        <Section
          title={t("Comparar dos versiones")}
          description={t("Qué reglas se añadieron, cambiaron o se retiraron, con su evidencia, y qué código cambió.")}
        >
          <form className="flex flex-wrap items-end gap-3" onSubmit={compare}>
            <div className="grid gap-1.5">
              <Label htmlFor="release-base">{t("Desde")}</Label>
              <Select id="release-base" value={base} options={options} onValueChange={setBase} />
            </div>
            <div className="grid gap-1.5">
              <Label htmlFor="release-target">{t("Hasta")}</Label>
              <Select id="release-target" value={target} options={options} onValueChange={setTarget} />
            </div>
            <Button type="submit" disabled={!base || !target || base === target}>
              <GitCompare data-icon="inline-start" />
              {t("Comparar")}
            </Button>
          </form>
        </Section>
      )}
      <Section
        title={t("Versiones del proyecto")}
        description={t("Salen de los tags de Git. Guardar el estado fija qué reglas se conocían en esa versión; solo se puede en la versión que está sincronizada ahora.")}
        action={
          sources.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {sources.map((source) => (
                <Button key={source.id} size="sm" variant="outline" onClick={() => discover(source)}>
                  <Tags data-icon="inline-start" />
                  {t("Leer tags")}{sources.length > 1 && t(" de {0}", source.display_name)}
                </Button>
              ))}
            </div>
          )
        }
      >
        {notice && <Muted>{notice}</Muted>}
        <ErrorText>{error}</ErrorText>
        {sources.length === 0 && releases !== null ? (
          <EmptyState icon={Tags} title={t("Sin repositorio Git")}>
            {t("Las versiones se leen de los tags de un repositorio. Añade uno en Fuentes.")}
          </EmptyState>
        ) : releases === null ? (
          <Muted>{t("Cargando…")}</Muted>
        ) : releases.length === 0 ? (
          <EmptyState icon={Tags} title={t("Todavía no hay versiones")}>
            {t("Pulsa \"Leer tags\" para descubrirlas.")}
          </EmptyState>
        ) : (
          <ul className="divide-y">
            {releases.map((release) => {
              const [tone, label] = STATUS[release.status]
              return (
                <li key={release.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5">
                  <div className="grid gap-0.5">
                    <span className="flex flex-wrap items-center gap-2 text-sm font-medium">
                      {release.release_name}
                      <StatusBadge tone={tone}>{label}</StatusBadge>
                    </span>
                    <span className="text-xs text-muted-foreground">
                      <code>{shortSha(release.commit_sha)}</code>
                      {release.released_at && ` · ${new Date(release.released_at).toLocaleDateString()}`}
                      {release.previous_release_id &&
                        t(" · después de {0}", names.get(release.previous_release_id) ?? "otra")}
                    </span>
                  </div>
                  <Button size="sm" variant="ghost" onClick={() => snapshot(release)}>
                    <Camera data-icon="inline-start" />
                    {t("Guardar estado")}
                  </Button>
                </li>
              )
            })}
          </ul>
        )}
      </Section>
    </>
  )
}

export function VersionsScreen(props: ScreenProps) {
  const { analysisId } = useParams()
  const [refresh, setRefresh] = useState(0)
  const finished = useCallback(() => setRefresh((value) => value + 1), [])
  return (
    <>
      <PageHeader
        title={t("Versiones")}
        description={t("Explora las versiones del proyecto y compara qué cambió entre dos de ellas.")}
      />
      <div className="grid gap-6 lg:grid-cols-[1fr_16rem]">
        <div className="grid min-w-0 content-start gap-4">
          {analysisId ? (
            <>
              <Link
                to="/versiones"
                className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
              >
                <ArrowLeft className="size-4" />
                {t("Todas las versiones")}
              </Link>
              <Section>
                <AnalysisDetail
                  key={analysisId}
                  analysisId={analysisId}
                  onSessionLost={props.onSessionLost}
                  onFinished={finished}
                />
              </Section>
            </>
          ) : (
            <Explorer {...props} />
          )}
        </div>
        <AnalysisHistory {...props} types={["release_comparison"]} basePath="/versiones" refresh={refresh} />
      </div>
    </>
  )
}
