import { t } from "@/lib/i18n"
import { useCallback, useEffect, useState } from "react"
import { useLocation } from "react-router"
import { type Analysis, ApiError, getAnalysis, isFinished } from "@/lib/api"
import { errorMessage } from "@/lib/labels"

export const POLL_MS = 1500

/**
 * Shows API errors as text; a lost session is handled by the application. `failure` keeps the
 * last error whole, so a screen whose first load failed can say why and offer a way out.
 */
export function useErrors(onSessionLost: (error: unknown) => void) {
  const [error, setMessage] = useState<string | null>(null)
  const [failure, setFailure] = useState<ApiError | null>(null)
  const setError = useCallback((message: string | null) => {
    setMessage(message)
    if (message === null) setFailure(null)
  }, [])
  const fail = useCallback(
    (caught: unknown) => {
      if (caught instanceof ApiError) {
        setMessage(errorMessage(caught.code))
        setFailure(caught)
      } else onSessionLost(caught)
    },
    [onSessionLost],
  )
  return { error, setError, fail, failure }
}

/** Polls an analysis until it finishes. */
export function usePolledAnalysis(analysisId: string | null, onSessionLost: (error: unknown) => void) {
  const [analysis, setAnalysis] = useState<Analysis | null>(null)
  useEffect(() => {
    if (!analysisId) return
    let stopped = false
    let timer: number | undefined
    // Server-sent events (phase 9.4): the server says when it changes. If the stream cannot
    // open or breaks, it falls back to asking every POLL_MS.
    let source: EventSource | null = null
    if (typeof EventSource !== "undefined") {
      source = new EventSource(`/api/v1/analyses/${analysisId}/events`)
      source.addEventListener("analysis", (event) => {
        if (!stopped) setAnalysis(JSON.parse((event as MessageEvent<string>).data) as Analysis)
      })
      source.addEventListener("end", () => source?.close())
      source.onerror = () => {
        source?.close()
        source = null
        if (!stopped) void load()
      }
    }
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
    if (!source) void load()
    return () => {
      stopped = true
      source?.close()
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
  ["/reglas/", t("Regla anterior")],
  ["/reglas", t("Reglas")],
  ["/impacto", t("Impacto")],
  ["/versiones", t("Versiones")],
]

/** Where the open rule came from: a page of this app, or the rule list when unknown. */
export function useBackTarget(): { to: string; label: string } {
  const from = (useLocation().state as { from?: unknown } | null)?.from
  // Only paths inside the app: never another origin ("//host") or a full URL.
  const to = typeof from === "string" && from.startsWith("/") && !from.startsWith("//") ? from : "/reglas"
  if (to === "/") return { to, label: t("Inicio") }
  const label = BACK_LABELS.find(([prefix]) => to.startsWith(prefix))?.[1] ?? t("Volver")
  return { to, label }
}
