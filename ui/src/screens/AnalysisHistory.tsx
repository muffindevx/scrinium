import { t } from "@/lib/i18n"
import { History, Trash2 } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { NavLink } from "react-router"
import { cn } from "cn"
import { AnalysisStatusBadge } from "@/components/status"
import { Button } from "@/components/ui/button"
import { type Analysis, type AnalysisType, deleteAnalysis, listAnalyses } from "@/lib/api"
import { ANALYSIS_TYPE_LABELS } from "@/lib/labels"
import { relativeTime } from "@/lib/time"
import { useErrors } from "@/screens/hooks"
import { ErrorText, Loading, Muted, type ScreenProps } from "@/screens/shared"

/** History of analyses of some given types; each screen shows only its own. */
export function AnalysisHistory({
  project,
  onSessionLost,
  types,
  basePath,
  refresh,
}: ScreenProps & { types: AnalysisType[]; basePath: string; refresh: number }) {
  const [analyses, setAnalyses] = useState<Analysis[] | null>(null)
  const { error, fail, failure, retry } = useErrors(onSessionLost)
  const key = types.join(",")
  const load = useCallback(() => {
    listAnalyses(project.id)
      .then((all) => setAnalyses(all.filter((analysis) => key.split(",").includes(analysis.analysis_type))))
      .catch(fail)
  }, [project.id, key, fail])
  useEffect(load, [load, refresh])
  const remove = (id: string) => {
    deleteAnalysis(id).then(load).catch(fail)
  }
  return (
    <aside className="grid content-start gap-2">
      <p className="flex items-center gap-1.5 text-sm font-medium">
        <History className="size-4 text-muted-foreground" aria-hidden="true" />
        {t("Historial")}
      </p>
      <ErrorText>{analyses !== null && error}</ErrorText>
      {analyses === null ? (
        <Loading failure={failure} onRetry={() => { retry(); load() }} />
      ) : analyses.length === 0 ? (
        <Muted>{t("Todavía no hay análisis.")}</Muted>
      ) : (
        <ul className="grid gap-0.5">
          {analyses.map((analysis) => (
            <li key={analysis.id} className="group flex items-start gap-1 rounded-lg hover:bg-muted">
              <NavLink
                to={`${basePath}/${analysis.id}`}
                className={({ isActive }) => cn("grid flex-1 gap-1 px-2 py-1.5 text-sm", isActive && "font-medium")}
              >
                <span className="line-clamp-1">{analysis.question || ANALYSIS_TYPE_LABELS[analysis.analysis_type]}</span>
                <span className="flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                  <AnalysisStatusBadge status={analysis.status} />
                  {ANALYSIS_TYPE_LABELS[analysis.analysis_type]} · {relativeTime(analysis.created_at)}
                </span>
              </NavLink>
              <Button
                size="icon-xs"
                variant="ghost"
                className="mt-1.5 mr-1 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
                aria-label={t("Borrar análisis")}
                disabled={analysis.status === "queued" || analysis.status === "running"}
                onClick={() => remove(analysis.id)}
              >
                <Trash2 />
              </Button>
            </li>
          ))}
        </ul>
      )}
    </aside>
  )
}
