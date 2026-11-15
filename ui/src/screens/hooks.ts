import { useCallback, useEffect, useState } from "react"
import { useLocation } from "react-router"
import { type Analysis, ApiError, getAnalysis, isFinished } from "@/lib/api"
import { errorMessage } from "@/lib/labels"

export const POLL_MS = 1500

/** Shows API errors as text; a lost session is handled by the application. */
export function useErrors(onSessionLost: (error: unknown) => void) {
  const [error, setError] = useState<string | null>(null)
  const fail = useCallback(
    (failure: unknown) => {
      if (failure instanceof ApiError) setError(errorMessage(failure.code))
      else onSessionLost(failure)
    },
    [onSessionLost],
  )
  return { error, setError, fail }
}

/** Polls an analysis until it finishes. */
export function usePolledAnalysis(analysisId: string | null, onSessionLost: (error: unknown) => void) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  useEffect(() => {
    if (!analysisId) return
    let stopped = false
    let timer: number | undefined
    const load = () =>
      getAnalysis(analysisId)
        .then((loaded) => {
          if (stopped) return
          setAnalysis(loaded)
          if (!isFinished(loaded)) timer = window.setTimeout(load, POLL_MS)
        })
        .catch((error: unknown) => {
          if (!stopped && !(error instanceof ApiError)) onSessionLost(error)
        })
    void load()
    return () => {
      stopped = true
      window.clearTimeout(timer)
    }
  }, [analysisId, onSessionLost])
  return analysis
}

/** The value, once it has stopped changing for `ms`. */
export function useDebounced<T>(value: T, ms: number): T {
  const [debounced, setDebounced] = useState(value)
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), ms)
    return () => window.clearTimeout(timer)
  }, [value, ms])
  return debounced
}

const BACK_LABELS: [prefix: string, label: string][] = [
  ["/reglas/", "Regla anterior"],
  ["/reglas", "Reglas"],
  ["/impacto", "Impacto"],
  ["/versiones", "Versiones"],
]

/** Where the open rule came from: a page of this app, or the rule list when unknown. */
export function useBackTarget(): { to: string; label: string } {
  const from = (useLocation().state as { from?: unknown } | null)?.from
  // Only paths inside the app: never another origin ("//host") or a full URL.
  const to = typeof from === "string" && from.startsWith("/") && !from.startsWith("//") ? from : "/reglas"
  if (to === "/") return { to, label: "Inicio" }
  const label = BACK_LABELS.find(([prefix]) => to.startsWith(prefix))?.[1] ?? "Volver"
  return { to, label }
}
