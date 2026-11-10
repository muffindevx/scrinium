import { useCallback, useEffect, useState } from "react"
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

